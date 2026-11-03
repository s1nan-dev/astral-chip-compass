"""三选一显示着时另外快速盯绿框：只查哪一张被点选了（出现亮绿框），看到的交给识别流程记账。

玩家从点选筹码（出现绿框、底下的按钮变成「确定」）到点「确定」、界面关掉常常只有 1 秒上下，而识别流程读左上角（核对轮次、
进度条，读不出时隔一会儿再读）、做文字识别的那一帧可能比这还长（较慢的电脑上一帧 1～2 秒）。绿框只在每帧开头查一次的话，
这段时间里一帧都截不到它，界面关掉时就不知道选了哪张。所以识别流程跟着一次三选一的时候，另一个线程每 WATCH_S 秒只查一次
绿框，不等识别流程正在做的读数：

  - 要查的（Target）：识别流程那次完整识别的结果（三张的位置、单位长度）和筹码名那一条的样子（FrameProcessor 的
    _select_cache）。只截盖住筹码名那一条和三栏绿框的那一块（chip_select.select_band），查法和识别流程每帧开头的快速复查
    相同（chip_select.reread_selected）：先确认筹码名那一条没变（画面上还是这三张），再看哪一栏是亮绿框，结果和在整幅画面上
    查的完全相同。
  - 交换（PickWatch）：识别流程放上要查的（publish：换了一组候选、三选一关掉或被隐藏时换成新的一份或 None），盯绿框的线程
    把看到的选中项放回来（end），只留最近一次看到选中的、还没取走的那一次。识别流程在自己的线程里取走（publish、take），
    在同一把锁里、按记账现有的入口交给记账，效果和每帧开头的快速复查交回的读数一样（见 FrameProcessor._watch_frame）：
    盯绿框的线程不碰记账。识别流程每帧开头放上、取走之前，先等盯绿框的线程把正在查的那一次查完（查一次几毫秒，直接截取时
    另要等一帧新画面、最多二十毫秒；最多等 BUSY_WAIT_S）：截到画面早于这一帧的，一定在这一帧的读数之前交给记账，先后不乱。
  - 线程（PickWatcher）：后台识别线程（悬浮窗的 Watcher）开始时建、启动，结束时停下；识别流程重建时换上新的交换（attach）。
    没有要查的（不在三选一、识别暂停、手动模式不跟着三选一）时不截图。取帧对象（只截那一块）在这个线程里建、用、关：
    直接截取的会话只能在建它的线程里用（vision/window_capture.py），所以和后台识别各用各的，互不等待。

看到的时刻按截到那一块画面的时刻记（单调时钟，和识别流程每帧的 now 是同一个钟），识别流程据此把它排在对应那一帧的读数
之前或之后交给记账，先后和画面上的一致。统计（take_period）每分钟随识别统计记一条（recog_stats 的 pick_watch）。
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass

import numpy as np

from . import chip_select

WATCH_S = 0.1  # 多久查一次（约每秒 10 次）
# 识别流程换要查的、取走看到的之前，最多等盯绿框的线程把正在查的那一次查完这么久（平时几毫秒到二十来毫秒，这是卡住时的上限；
# 等不到时那一次看到的不算）
BUSY_WAIT_S = 0.2
IDLE_WAIT_S = 1.0  # 没有要查的时候最多等这么久醒一次（看要不要关掉空闲的截取）
PAUSED_WAIT_S = 0.1  # 识别暂停着时隔这么久再看一次
ERROR_WAIT_S = 2.0  # 查一次出错以后隔这么久再查（一直出错也不会一直占着处理器）
SOURCE_IDLE_S = 30.0  # 这么久没有要查的就关掉截取（直接截取的会话），下一次三选一出现时重建


@dataclass(frozen=True)
class Target:
    """要查的一组候选（识别流程的一次完整识别，见模块说明）。

    serial：第几份（PickWatch 编号，只增不减）；seen：那次完整识别的结果（chip_select.ChipSelect）；strip：筹码名那一条的
    指纹（chip_select.name_strip）；shape：识别流程看的画面（去掉四周黑边以后）的 (高, 宽)；offset：去掉的黑边，即那幅画面的
    左上角在游戏客户区里的位置；client：游戏客户区的 (宽, 高)，截取时客户区大小不是这个就不查（位置都是按它算的）；
    band：要截的那一块 (x0, y0, x1, y1)，识别流程看的画面上的像素（chip_select.select_band）。"""

    serial: int
    seen: object
    strip: np.ndarray
    shape: tuple[int, int]
    offset: tuple[int, int]
    client: tuple[int, int]
    band: tuple[int, int, int, int]

    @property
    def region(self) -> tuple[int, int, int, int]:
        """要截的那一块在游戏客户区里的 (左, 上, 宽, 高)。"""
        x0, y0, x1, y1 = self.band
        return x0 + self.offset[0], y0 + self.offset[1], x1 - x0, y1 - y0

    def check(self, image) -> tuple[bool, int | None]:
        """看截到的那一块（region 那么大）：返回（筹码名那一条对得上，选中的是第几张）。对不上（换了一组、界面关掉、名字被
        挡住）时为 (False, None)；对得上、没有绿框时为 (True, None)。"""
        got = chip_select.reread_selected(image, self.seen, self.strip, origin=self.band[:2], shape=self.shape)
        if got is None:
            return False, None
        return True, got.selected


@dataclass(frozen=True)
class Sighting:
    """盯绿框的线程看到的一次选中：哪一份要查的（target）、第几张（slot，从 0 数）、截到那一块画面的时刻（at，单调时钟）、
    截到的那一块（image，BGR；诊断用）。"""

    target: Target
    slot: int
    at: float
    image: np.ndarray


class PickWatch:
    """识别流程和盯绿框的线程之间的交换（见模块说明）。所有方法都在内部的锁里做，哪个线程调用都可以；识别流程调
    make_target、publish、take，盯绿框的线程调 begin、end、wait。"""

    def __init__(self):
        self._cond = threading.Condition()
        self._target: Target | None = None
        self._serial = 0
        self._sighting: Sighting | None = None  # 最近一次看到选中的、还没取走的
        self._busy = False  # 盯绿框的线程正在查一次（begin 之后、end 之前）
        self.closed = False  # 识别流程不用了（重建、退出）：不再给要查的

    @property
    def target(self) -> Target | None:
        """现在要查的那一份（只读一次引用）。"""
        return self._target

    def make_target(self, seen, strip, shape, offset, client) -> Target | None:
        """按一次完整识别建一份要查的（编号加一）。seen 没有位置信息、筹码名那一条没有指纹（测试里的假读数等，整幅上的快速
        复查也做不成）时为 None。"""
        band = chip_select.select_band(seen, tuple(shape))
        if band is None or strip is None:
            return None
        with self._cond:
            self._serial += 1
            serial = self._serial
        return Target(serial, seen, strip, (int(shape[0]), int(shape[1])), (int(offset[0]), int(offset[1])),
                      (int(client[0]), int(client[1])), band)

    def publish(self, target: Target | None) -> Sighting | None:
        """（识别流程）放上要查的（None 为不查）。先等盯绿框的线程把正在查的那一次查完（最多 BUSY_WAIT_S），再取走还没取走的
        那一次看到的（不论换没换都取），然后换上 target。返回取走的那一次：属于换之前的那一份。关掉以后什么都不放。
        换之前本来就没有要查的（上一次已经放下）时不等：那时正在查的那一次（线程卡住、等满了也没查完的）就算查完，也收不进来
        （end 只收现在要查的那一份），等了也白等；不然线程一直卡着时，不在三选一的每一帧都要白等 BUSY_WAIT_S。"""
        with self._cond:
            deadline = time.monotonic() + BUSY_WAIT_S
            while self._busy and self._target is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            got, self._sighting = self._sighting, None
            target = None if self.closed else target
            if target is not self._target:
                self._target = target
                self._cond.notify_all()
        return got

    def take(self) -> Sighting | None:
        """（识别流程）取走最近一次看到选中的、还没取走的那一次（不等正在查的那一次）。"""
        with self._cond:
            got, self._sighting = self._sighting, None
        return got

    def begin(self) -> Target | None:
        """（盯绿框的线程）开始查一次：返回现在要查的那一份，记下正在查；没有要查的时返回 None（不算在查）。"""
        with self._cond:
            target = self._target
            self._busy = target is not None
            return target

    def end(self, target: Target, slot: int | None, at: float, image) -> bool:
        """（盯绿框的线程）这一次查完（出错时 slot 传 None）。看到了选中（slot 不是 None）、要查的还是这一份时记下（换掉之前
        没取走的），返回 True。不论看到什么都清掉「正在查」，叫醒在等的识别流程。"""
        with self._cond:
            self._busy = False
            kept = slot is not None and target is self._target
            if kept:
                self._sighting = Sighting(target, int(slot), float(at), image)
            self._cond.notify_all()
            return kept

    def wait(self, timeout: float) -> Target | None:
        """（盯绿框的线程）没有要查的时最多等 timeout 秒（有了要查的、关掉、叫醒时提前返回），返回那时要查的。"""
        with self._cond:
            if self._target is None and not self.closed:
                self._cond.wait(timeout)
            return self._target

    def wake(self) -> None:
        """叫醒在 wait 里等的盯绿框的线程（停下、换交换时）。"""
        with self._cond:
            self._cond.notify_all()

    def close(self) -> None:
        """识别流程不用了（重建、退出）：不再查、丢掉没取走的，叫醒在等的线程。"""
        with self._cond:
            self.closed = True
            self._target = None
            self._sighting = None
            self._cond.notify_all()


def _percentile(values: list[float], q: float) -> float | None:
    """最近秩法的分位数（同 perfstats），一位小数；没有样本时为 None。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = min(max(int(np.ceil(q * len(ordered))), 1), len(ordered))
    return round(ordered[rank - 1], 1)


class _Period:
    """一段时间里盯绿框的统计（take_period 取走后重新计）。耗时分两段记：截那一块（grab：直接截取时含等一帧新画面的那一会儿，
    等的时候不占处理器）和查绿框（check：只用处理器）。不按线程的处理器时间记：Windows 按系统时钟的一跳（约 15.6 毫秒）给线程
    记时间，这个线程每次只忙一两毫秒、又总是在一跳刚过时醒来，几乎记不上。"""

    def __init__(self):
        self.checks = 0  # 查了几次（截到了那一块、查了绿框）
        self.selected = 0  # 其中看到选中的次数
        self.mismatch = 0  # 筹码名那一条对不上的次数（换了一组、界面关掉、名字被挡住）
        self.no_frame = 0  # 没截到画面的次数（窗口大小变了、最小化、暂时没有画面等）
        self.errors = 0  # 出错的次数
        self.grab_ms: list[float] = []  # 每次截那一块的耗时，毫秒
        self.check_ms: list[float] = []  # 每次查绿框的耗时（截到了的那几次），毫秒

    def summary(self) -> dict:
        return {
            "checks": self.checks, "selected": self.selected, "mismatch": self.mismatch, "no_frame": self.no_frame,
            "errors": self.errors, "grab_ms_p50": _percentile(self.grab_ms, 0.5),
            "grab_ms_p95": _percentile(self.grab_ms, 0.95), "check_ms_p50": _percentile(self.check_ms, 0.5),
            "check_ms_p95": _percentile(self.check_ms, 0.95),
            "check_ms_sum": round(sum(self.check_ms), 1),
        }


class PickWatcher:
    """盯绿框的线程（见模块说明）。

    make_source：建取帧对象的函数，在这个线程里第一次要查时调用（建不了时返回 None，过一会儿再建）。取帧对象只在这个线程里用：
      grab(region, client) 截游戏客户区里 region =（左, 上, 宽, 高）那一块，返回 BGR 图；客户区大小不是 client =（宽, 高）、
      窗口最小化、暂时没有画面时返回 None。age 是最近截到的那一块落后了多少秒，close() 关掉；有 close_if_idle 时没事做的时候
      调它。
    paused：识别暂停着（游戏窗口最小化等）时为真的函数：暂停期间不截图。
    clock：单调时钟（和识别流程每帧的 now 同一个钟；测试换成假的）。
    on_error：查一次出错时调用 on_error(标题, 调用栈的文字)（记进错误记录），这次运行只调一次；出错不影响识别流程。"""

    def __init__(self, make_source, paused=None, clock=time.monotonic, interval: float = WATCH_S, on_error=None):
        self._make_source = make_source
        self._paused = paused if paused is not None else (lambda: False)
        self._clock = clock
        self.interval = interval
        self._on_error = on_error
        self._exchange: PickWatch | None = None
        self._source = None
        self._source_used = 0.0  # 最近一次用取帧对象的时刻（clock）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._retry_at: float | None = None  # 出错以后到这个时刻再查（clock）
        self._error_logged = False
        self._stats_lock = threading.Lock()
        self._period = _Period()

    # ------------------------------------------------------------ 对外

    def attach(self, exchange: PickWatch | None) -> None:
        """换上（新建的识别流程的）交换。原来的那一份关掉：不再查它，在等它的线程醒来换到新的。哪个线程调用都可以。"""
        old, self._exchange = self._exchange, exchange
        if old is not None and old is not exchange:
            old.close()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pick-watch", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """请线程停下，最多等 timeout 秒，返回停下了没有（没启动过也算）。线程收尾时关掉取帧对象。"""
        self._stop.set()
        exchange = self._exchange
        if exchange is not None:
            exchange.wake()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def take_period(self) -> dict:
        """上次取走以来的统计（_Period.summary）并重新计。识别线程每分钟随识别统计取一次。"""
        with self._stats_lock:
            period, self._period = self._period, _Period()
        return period.summary()

    def tick(self) -> bool:
        """在当前线程里查一次：截那一块、查绿框，看到的交给交换。返回查没查（没有要查的、暂停着、没截到画面、出错时为 False）。
        线程的循环每 interval 秒调一次；测试直接调它。"""
        exchange = self._exchange
        if exchange is None or self._paused():
            return False
        target = exchange.begin()
        if target is None:
            return False
        slot, at, image, outcome = None, 0.0, None, "no_frame"
        began = time.perf_counter()
        grabbed = None  # 截完那一块的时刻（perf_counter）
        try:
            source = self._ensure_source()
            image = None if source is None else source.grab(target.region, target.client)
            grabbed = time.perf_counter()
            if image is not None:
                at = self._clock() - max(0.0, float(getattr(source, "age", 0.0) or 0.0))
                self._source_used = self._clock()
                matched, slot = target.check(image)
                outcome = "selected" if slot is not None else ("checked" if matched else "mismatch")
        except Exception:  # noqa: BLE001  盯绿框只是补充：出错不能影响识别流程，这一次不算
            slot, outcome = None, "error"
            self._failed(traceback.format_exc())
        finally:
            exchange.end(target, slot, at, image)
            done = time.perf_counter()
            grab_ms = ((grabbed if grabbed is not None else done) - began) * 1000.0
            check_ms = (done - grabbed) * 1000.0 if grabbed is not None and image is not None else None
            self._count(outcome, grab_ms, check_ms)
        return outcome in ("selected", "checked", "mismatch")

    # ------------------------------------------------------------ 线程

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                exchange = self._exchange
                if exchange is None:
                    self._idle(IDLE_WAIT_S)
                    continue
                if self._paused():
                    self._close_if_idle()
                    self._stop.wait(PAUSED_WAIT_S)
                    continue
                target = exchange.wait(IDLE_WAIT_S)
                if self._stop.is_set():
                    break
                if target is None or exchange is not self._exchange:
                    self._close_if_idle()
                    continue
                now = self._clock()
                if self._retry_at is not None and now < self._retry_at:
                    self._stop.wait(min(self._retry_at - now, IDLE_WAIT_S))
                    continue
                self.tick()
                left = self.interval - (self._clock() - now)
                if left > 0:
                    self._stop.wait(left)
        finally:
            self._close_source()

    def _idle(self, seconds: float) -> None:
        self._close_if_idle()
        self._stop.wait(seconds)

    def _ensure_source(self):
        if self._source is None:
            self._source = self._make_source()
            self._source_used = self._clock()
        return self._source

    def _close_if_idle(self) -> None:
        """没有要查的满 SOURCE_IDLE_S 秒：关掉取帧对象（直接截取的会话占着显卡内存；Windows 10 上黄框随之消失）。"""
        source = self._source
        if source is None:
            return
        if self._clock() - self._source_used >= SOURCE_IDLE_S:
            self._close_source()

    def _close_source(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            try:
                source.close()
            except Exception:  # noqa: BLE001  关不掉也不影响之后重建
                pass

    def _failed(self, detail: str) -> None:
        """查一次出错：关掉取帧对象（下一次重建），隔 ERROR_WAIT_S 再查；错误记录这次运行只记一次。"""
        self._close_source()
        self._retry_at = self._clock() + ERROR_WAIT_S
        if self._error_logged or self._on_error is None:
            return
        self._error_logged = True
        try:
            self._on_error("三选一快速查看选中项时出错（拾星助手仍在运行，识别照常）", detail)
        except Exception:  # noqa: BLE001  记不进就算了
            pass

    def _count(self, outcome: str, grab_ms: float, check_ms: float | None) -> None:
        with self._stats_lock:
            p = self._period
            if outcome == "error":
                p.errors += 1
            elif outcome == "no_frame":
                p.no_frame += 1
            else:
                p.checks += 1
                if outcome == "selected":
                    p.selected += 1
                elif outcome == "mismatch":
                    p.mismatch += 1
            p.grab_ms.append(grab_ms)
            if check_ms is not None:
                p.check_ms.append(check_ms)
            for samples in (p.grab_ms, p.check_ms):
                if len(samples) > 5000:  # 一分钟最多约 600 次；万一没人取走，只留最近的
                    del samples[:1000]
