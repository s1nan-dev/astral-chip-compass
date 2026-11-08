"""同时识别两个客户端以前的性能测试：Steam 版和 TapTap 版的游戏窗口都开着时，两路同时识别一段时间（默认 30 秒；取帧按
三选一显示着时的节奏，比平时的大厅密，见 DualCheck），量三样：三选一出推荐要多久、整台电脑忙不忙、拾星助手自己占多少
处理器，按到了基准值的几成分成「余裕」「正常」「危险」「临界」「过载」五档。设置窗口的「游戏平台」改成「Steam端+TapTap端」
以前先测这一次。

分档的两条基准值：三选一出推荐最慢的一次 SLOW_LINE_S 秒；测的时间里整机处理器占用在 BUSY_LINE 以上的时间占比
BUSY_SHARE_LINE。每样数先算到了基准值的几成，取两样里大的那个（一次都没量到时只看处理器那一样），按 LEVEL_LINES 分档：
七成及以下余裕，超过七成到九成正常，超过九成到十成危险，超过十成到十一成临界，超过十一成过载。玩家关心的是推荐来不来得及、
游戏卡不卡，两条基准值对着这两件事；只看拾星助手自己占多少分不出这两件事，own_share 只写给玩家看。

测的过程（DualCheck）只读画面、不写文件：用的是不存盘的对局状态，不记识别记录。"""
from __future__ import annotations

import os
import statistics
import threading
import time
import traceback
from dataclasses import dataclass

from .. import perfstats
from ..data import DB
from ..engine.scorer import advise
from ..errors import log_error
from ..pool import State
from ..session import Session, Settings
from ..vision import capture, chip_select
from ..vision.pipeline import FrameProcessor, limit_cv_threads
from . import lang
from .screen import FrameSource, NoFrame

SLOW_LINE_S = 2.0  # 三选一出推荐最慢的一次的基准值（平时 0.3～0.7 秒）
BUSY_LINE = 0.9  # 整机处理器占用到这么多算「被压满」（这时游戏容易卡）……
BUSY_SHARE_LINE = 0.5  # ……测的时间里被压满的时间占比的基准值

EASY, NORMAL, DANGER, CRITICAL, OVERLOAD = "easy", "normal", "danger", "critical", "overload"  # 余裕、正常、危险、临界、过载
# 到了基准值的几成算哪一档（从高到低；「超过」是严格大于，正好等于的算低的那一档）。两样数里取大的那个
LEVEL_LINES = ((1.1, OVERLOAD), (1.0, CRITICAL), (0.9, DANGER), (0.7, NORMAL))


def level_of(slowest_s: float | None, busy_share: float) -> str:
    """按两条基准值分档：EASY / NORMAL / DANGER / CRITICAL / OVERLOAD。slowest_s 为 None（一次都没量到）时只看整机忙不忙。"""
    slow = slowest_s or 0.0
    for ratio, level in LEVEL_LINES:
        if slow > SLOW_LINE_S * ratio or busy_share > BUSY_SHARE_LINE * ratio:
            return level
    return EASY


@dataclass(frozen=True)
class CheckResult:
    """一次性能测试的结果。

    slowest_s、median_s：三选一出推荐最慢的一次、中位数（秒；一次都没量到为 None）；busy_share：测的时间里整机处理器占用在
    BUSY_LINE 以上的比例（0～1）；own_share：拾星助手平均占整机处理器的比例（0～1）；seconds：实际测了多久；lowend：测的时候
    按低配模式的节奏识别；stopped：中途停了（取消，或者有一个游戏窗口关了、最小化了），这时 level 不作数，reason 是停下的
    原因（给玩家看的一句固定中文，取消时为空）。"""

    slowest_s: float | None
    median_s: float | None
    busy_share: float
    own_share: float
    seconds: float
    lowend: bool = False
    stopped: bool = False
    reason: str = ""

    @property
    def level(self) -> str:
        """EASY / NORMAL / DANGER / CRITICAL / OVERLOAD（level_of）。"""
        return level_of(self.slowest_s, self.busy_share)


LATENCY_EVERY_S = 4.0  # 每路隔多久量一个三选一的延迟样本（两路错开时刻；低配时乘 lowend_factor）
STEP_S = 0.1  # 测的过程中多久看一次时钟（进度、取消、取处理器时间都按它排队）
CPU_EVERY_S = 1.0  # 每秒取一次整机和本进程的处理器时间
TICKS_PER_SECOND = 10_000_000  # 处理器时间的一个单位是 100 纳秒
GONE_REASON = "「{name}」的游戏窗口关了或最小化了，测试停下了。"  # {name} 取 capture.PLATFORM_NAMES
ERROR_REASON = "性能测试出错，没有测完。"
ERROR_TITLE = "同时识别两个客户端时性能测试出错（拾星助手仍在运行）"
HANT_KEEP = ("ERROR_TITLE",)  # 只写进错误记录，不转
# 等的函数（测试换成假的，不用真的等）。放在模块级，和 perfstats 的时钟一样，测试可以换
sleep = time.sleep


class DualCheck:
    """一次同时识别两个客户端的性能测试（模块说明）。在后台线程里调用 run()：进度用 progress 回调，取消用 stop；
    开始以前用 missing() 查两个游戏窗口开没开。只读画面、不写文件：对局状态不存盘，不记识别记录、不存截图。

    取帧的节奏（_pace）有意比平时的大厅重：每取到一帧都按 [watch] active_ms 等（悬浮窗的识别线程只在三选一显示着时才按它，
    大厅里按 idle_ms，见 Watcher._loop），低配时 active_ms 也乘 lowend_factor（识别线程低配时只放慢 idle_ms）。测的时候两个
    游戏都停在大厅，大厅的帧比对局里的轻（读的东西少）：取帧比大厅里密，量出来的负担宁可偏重，不让玩家进了对局才发现
    吃不消。同样的道理，识别流程停在主菜单时也照常认字（FrameProcessor.menu_idle 关掉）：悬浮窗的识别线程停在主菜单、
    底栏和开始按钮那一块没变时不做文字识别（主菜单空转），照它量的话几乎只剩取帧。

    三选一的延迟量的是「读字 + 算推荐」：两个画面都停在大厅，read_chip_select 在预筛那一步就返回、不读字，所以直接调用它
    里面读字的那一步（chip_select._ocr_lines），再用资料库里现成的状态和三个候选筹码算一次推荐（advisor.engine.advise）。
    每路每隔 LATENCY_EVERY_S 秒量一个样本，两路错开时刻；低配时整个间隔乘 lowend_factor。和真实三选一相比少了按标题找
    位置、判断选中项等几步（大厅画面没有这些），读字本身的时间是一样的；两路同时读字时排队等文字识别引擎的时间也算在里面。

    低配时文字识别按低配模式的线程数测（chip_select.use_lowend_threads，在两路各自的线程里换）：这个开关全进程只有一份，
    测之前记下原来的样子，测完换回去（悬浮窗的识别线程每帧和它比，测的途中它换过也照它自己的低配模式再换回来）。"""

    def __init__(self, db: DB, *, seconds: float = 30.0, lowend: bool = False,
                 platforms=(capture.STEAM, capture.TAPTAP), finder=None, sampler=None,
                 clock=time.monotonic):
        """db：资料库；seconds：测多少秒；lowend：按低配模式的节奏测；platforms：测哪几个端（默认两个都测）；
        finder(platform)：返回 capture.GameWindow，找不到时抛 capture.WindowGone（默认 capture.find_game）；
        sampler()：返回（本进程, 整机忙, 整机总）的处理器时间累计，取不到时为 None（默认 perfstats.cpu_ticks）；
        clock：秒数时钟（测试用）。"""
        self.db = db
        self.seconds = max(0.0, float(seconds))
        self.lowend = bool(lowend)
        self.platforms = tuple(platforms)
        self.finder = finder if finder is not None else (lambda platform: capture.find_game(platform=platform))
        self.sampler = sampler if sampler is not None else perfstats.cpu_ticks
        self.clock = clock
        watch = db.rules.get("watch", {})
        # 低配模式：节奏和各项识别按这个倍数放慢（同 Watcher 读 [watch] lowend_factor）
        self._factor = max(1.0, float(watch.get("lowend_factor", 2)))
        self._state, self._offer = self._advice_inputs(db)

    @staticmethod
    def _advice_inputs(db: DB):
        """算推荐用的现成状态和三个候选筹码（照 tools/perf.py 的做法）：角色取资料库里的「凛」（没有就取第一个），
        地图取第一张现役地图，候选取筹码表的前三个。"""
        hero = "凛" if "凛" in db.heroes else next(iter(db.heroes), "")
        maps = db.active_maps()
        state = State(hero=hero, star=1, round=4, map=maps[0] if maps else None, owned=(), rerolls=2)
        return state, list(db.chips)[:3]

    def missing(self) -> list[str]:
        """没开或最小化了的端，按 platforms 的顺序（capture.STEAM、capture.TAPTAP 这两个值）。设置窗口开始测以前调用，
        提示玩家把两个都打开。"""
        out = []
        for platform in self.platforms:
            try:
                window = self.finder(platform)
            except RuntimeError:
                out.append(platform)
                continue
            if window.minimized or window.client is None:
                out.append(platform)
        return out

    def run(self, progress=None, stop=None) -> CheckResult:
        """一直跑到测完才返回（在后台线程里调用）。progress(已测秒数, 总秒数) 每秒回调一次，在测试线程里调用；
        stop 是 threading.Event，设了就尽快停下，返回 stopped=True、reason 为空。有一个游戏窗口找不到或最小化了时
        停下，reason 是「「TapTap端」的游戏窗口关了或最小化了，测试停下了。」这样的一句；出了例外记进错误记录，返回
        stopped=True 和「性能测试出错，没有测完。」，不往外抛。两路线程都结束以后才返回。"""
        if stop is None:
            stop = threading.Event()
        started = None
        try:
            started = self.clock()
            return self._run(started, progress, stop)
        except Exception:  # noqa: BLE001  出错时记进错误记录，按没测完返回，不往外抛
            log_error(ERROR_TITLE, traceback.format_exc(), append=True)
            elapsed = 0.0
            if started is not None:
                try:
                    elapsed = min(self.seconds, max(0.0, self.clock() - started))
                except Exception:  # noqa: BLE001  时钟也坏了：按 0 秒返回
                    elapsed = 0.0
            return CheckResult(None, None, 0.0, 0.0, elapsed, self.lowend, True, ERROR_REASON)

    def _run(self, started: float, progress, stop) -> CheckResult:
        try:
            limit_cv_threads()  # 和悬浮窗的识别线程相同（全进程的设置）
        except Exception:  # noqa: BLE001  省 CPU 的设置，出错照常测
            pass
        shared = _Shared()
        # 低配时两路要把文字识别换成低配模式的线程数：这个开关全进程只有一份，先记下原来的样子，测完换回去（不一律换成关：
        # 低配模式本来就开着时，悬浮窗的识别线程已经按它换好了）
        threads_before = chip_select.lowend_threads() if self.lowend else None
        cpu = _Cpu(self._sample())
        # 内部叫停两路用：一路出错、窗口关了，或者计时这一边要收尾时设它。不动调用方给的 stop，
        # 测完以后那个事件仍然只有调用方设过才为真
        abort = threading.Event()
        count = max(1, len(self.platforms))
        threads = []
        for index, platform in enumerate(self.platforms):
            offset = LATENCY_EVERY_S * index / count  # 两路错开时刻，读字不在同一刻
            thread = threading.Thread(target=self._worker, args=(platform, started, offset, stop, abort, shared),
                                      name=f"dual-check-{platform}", daemon=True)
            threads.append(thread)
        deadline = started + self.seconds
        last_sample = started
        try:
            for thread in threads:
                thread.start()
            if progress is not None:
                self._report(progress, 0.0)
            while not stop.is_set() and not abort.is_set():
                now = self.clock()
                while now - last_sample >= CPU_EVERY_S:
                    last_sample += CPU_EVERY_S
                    cpu.add(self._sample())
                    if progress is not None:
                        self._report(progress, min(last_sample - started, self.seconds))
                if now >= deadline:
                    break
                sleep(min(STEP_S, max(0.0, deadline - now)))
        finally:
            abort.set()
            for thread in threads:
                thread.join()
            if threads_before is not None:
                try:
                    chip_select.use_lowend_threads(threads_before)  # 测完换回测之前的样子
                except Exception:  # noqa: BLE001  换不回来也不影响返回
                    pass
        cpu.finish(self._sample())  # 让本进程的处理器时间算到最后
        elapsed = min(self.seconds, max(0.0, self.clock() - started))
        busy_share, own_share = cpu.result(elapsed)
        samples = shared.samples
        slowest = max(samples) if samples else None
        median = statistics.median(samples) if samples else None
        if shared.error:
            log_error(ERROR_TITLE, shared.detail, append=True)
        stopped = shared.error or bool(shared.reason) or (stop.is_set() and elapsed < self.seconds)
        return CheckResult(slowest, median, busy_share, own_share, elapsed, self.lowend, stopped,
                           ERROR_REASON if shared.error else shared.reason)

    def _worker(self, platform: str, started: float, offset: float, stop, abort, shared: "_Shared") -> None:
        """一路的识别：按 _pace 的节奏取帧、process（比平时的大厅密，见类的说明），隔一会儿量一次三选一的延迟。"""
        source = None
        try:
            if self.lowend:
                # 文字识别少用几个线程：和识别线程一样，在用识别引擎的这个线程里、第一次识别之前换
                chip_select.use_lowend_threads(True)
            source = FrameSource(finder=lambda platform=platform: self.finder(platform))
            session = Session(self.db, Settings(), path=None)  # 不存盘
            processor = FrameProcessor(self.db, session, threading.Lock())
            processor.menu_idle = False  # 停在主菜单也照常认字，不空转（见类的说明）
            if self.lowend:
                processor.set_pace(self._factor)
            deadline = started + self.seconds
            every = LATENCY_EVERY_S * (self._factor if self.lowend else 1.0)
            next_latency = started + offset
            while not stop.is_set() and not abort.is_set() and self.clock() < deadline:
                try:
                    image = source.grab()
                except NoFrame:
                    sleep(self._pace(False))
                    continue
                except capture.WindowGone:
                    shared.stop(capture.PLATFORM_NAMES.get(platform, platform))
                    abort.set()
                    return
                processor.process(image, self.clock())
                now = self.clock()
                if now >= next_latency:
                    shared.add(self._latency(image))
                    next_latency = max(next_latency + every, now)
                sleep(self._pace(True))
        except Exception:  # noqa: BLE001  一路出错整次测试作废，但不能往外抛、不能卡住
            shared.fail(traceback.format_exc())
            abort.set()
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:  # noqa: BLE001  关不掉不影响返回
                    pass
            try:
                # 直接截取的会话在这个线程里建的时候初始化过 Windows 运行时（window_capture.init_thread）：先关会话、再收尾，
                # 和识别线程一样配对（没初始化过时什么都不做）
                from ..vision import window_capture

                window_capture.uninit_thread()
            except Exception:  # noqa: BLE001  收尾出错不影响返回
                pass

    def _pace(self, visible: bool) -> float:
        """这一帧以后等多久（秒）：取到了画面按 [watch] active_ms，暂时没有画面（刚建会话等）按 idle_ms；低配时两样都乘
        lowend_factor。比悬浮窗的识别线程在大厅里的节奏重（它不在三选一时按 idle_ms、低配时 active_ms 不放慢），是有意的：
        见类的说明。"""
        watch = self.db.rules.get("watch", {})
        ms = watch.get("active_ms", 300) if visible else watch.get("idle_ms", 500)
        return max(0.0, float(ms) / 1000.0 * (self._factor if self.lowend else 1.0))

    def _latency(self, image) -> float:
        """照三选一的读法量一次：读字，再算一次推荐，返回用了多少秒。"""
        begin = self.clock()
        chip_select._ocr_lines(image)  # 三选一最费时的一步；大厅画面在 read_chip_select 的预筛就返回，读不到这里
        advise(self.db, self._state, self._offer)
        return max(0.0, self.clock() - begin)

    def _sample(self):
        try:
            return self.sampler()
        except Exception:  # noqa: BLE001  取不到处理器时间不影响测试
            return None

    def _report(self, progress, done: float) -> None:
        try:
            progress(done, self.seconds)
        except Exception:  # noqa: BLE001  界面回调出错不影响测试
            pass


class _Shared:
    """两路共用的结果：三选一延迟的样本、停下的原因或出错。线程之间加锁。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.samples: list[float] = []
        self.reason = ""
        self.error = False
        self.detail = ""

    def add(self, seconds: float) -> None:
        with self._lock:
            self.samples.append(seconds)

    def stop(self, name: str) -> None:
        with self._lock:
            if not self.reason:
                self.reason = lang.t(GONE_REASON.format(name=lang.t(name)))

    def fail(self, detail: str) -> None:
        with self._lock:
            self.error = True
            if not self.detail:
                self.detail = detail


class _Cpu:
    """整机和本进程的处理器占用：每秒取一次，整机占用在 BUSY_LINE 以上的整秒数记下来；本进程的处理器时间按开始、结束
    两次累计值算平均占整机多少。取不到的样本跳过。"""

    def __init__(self, baseline) -> None:
        self.first = self.prev = _ticks_of(baseline)
        self.busy_seconds = 0

    def add(self, sample) -> None:
        now = _ticks_of(sample)
        if now is None:
            return
        prev = self.prev
        total = 0 if prev is None else now[2] - prev[2]
        busy = 0 if prev is None else now[1] - prev[1]
        if total > 0 and busy > 0 and busy / total > BUSY_LINE:
            self.busy_seconds += 1
        self.prev = now
        if self.first is None:
            self.first = now

    def finish(self, sample) -> None:
        """结束前再取一次，只更新本进程的处理器时间（最后不到一秒的那一段不算整机占用）。"""
        now = _ticks_of(sample)
        if now is not None:
            self.prev = now

    def result(self, elapsed: float) -> tuple[float, float]:
        """（整机占用在 BUSY_LINE 以上的比例, 本进程平均占整机的比例）。时长算不出来时都为 0。"""
        if elapsed <= 0:
            return 0.0, 0.0
        busy_share = min(1.0, self.busy_seconds / elapsed)
        own_share = 0.0
        if self.first is not None and self.prev is not None and self.prev[0] > self.first[0]:
            own_share = ((self.prev[0] - self.first[0]) / (elapsed * (os.cpu_count() or 1) * TICKS_PER_SECOND))
        return busy_share, min(1.0, own_share)


def _ticks_of(sample) -> tuple[int, int, int] | None:
    """sampler 交回来的一项转成三个整数；不是三个数时为 None。"""
    if sample is None:
        return None
    try:
        process, busy, total = sample
        return int(process), int(busy), int(total)
    except (TypeError, ValueError):
        return None


lang.localize(globals())
