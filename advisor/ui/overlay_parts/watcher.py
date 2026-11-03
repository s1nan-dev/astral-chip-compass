"""后台识别线程（Watcher）：取帧 → 识别 → 交给 Session 记录。

只读游戏画面，不向游戏发送任何输入。取帧、识别的节奏、暂停和恢复、按热键要求识别一次都在这里；界面上的显示在悬浮窗
（advisor/ui/overlay.py）。overlay.py 另外再导出一份（Watcher）。
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from ... import perfstats
from ...session import WatchHints
from .. import log_error
from .state import CLOSED, EV_MISSED_PICK, EV_NEWGAME, EV_PICKED
from .wording import (FOLLOW_FRAMES, RECOG_CAPTURE, RECOG_FRAME, RECOG_ITEMS, RECOG_REPEAT_S, RECOG_STARTUP,
                      recog_items_text)

LOWEND_FACTOR = 2  # [watch] lowend_factor 没写时的默认值（低配模式按它拉长节奏，见 Watcher.set_lowend）
clock = time.monotonic  # 数「识别出错的每一项多久没再出」用（测试换成假时钟）


class Watcher(QThread):
    """后台线程：取帧 → 识别 → 交给 Session 记录。

    取帧用取帧对象（ui/screen.py 的 FrameSource）：能直接截取游戏窗口时直接截取（游戏窗口不在前台、被别的窗口盖住也照样
    截得到，画面里没有悬浮窗），用不了时改为截取屏幕（悬浮窗压在游戏画面上时贴补遮挡的小块）。取帧对象在这个线程里建、用、
    关（窗口截取的会话只能在一个线程里用）；截取状态（screen.CaptureState：方式、原因、错误代码）变了时发 capture_changed。
    暂时没有画面（刚建会话等，screen.NoFrame）时不当出错，按平时的节奏再取。
    自动模式按 [watch] 的节奏持续识别；手动模式平时不取帧，只在 trigger()（热键）时取。
    手动模式下按热键识别到三选一后，默认持续识别至界面关闭（[watch] manual_follow），这样才能记录选择了哪个。三选一被玩家
    隐藏着时按热键也一样（认出「您当前正处于隐藏UI状态」，见 FrameProcessor._select_hidden）：接着看，点「显示」后照常认。
    识别流程一帧做完以后还有排到下一帧的读数（FrameProcessor.pending：不在三选一时一帧只做一项读数）时，不等
    idle_ms，紧接着取下一帧接着做（手动模式也接着取，做完为止）；识别流程知道现在是不是手动模式（set_manual）、刚从暂停恢复
    （resumed）：这两种情况下三选一出现时先读完轮次、进度条再给推荐。
    暂停（set_paused）时不取帧：游戏窗口最小化、找不到游戏窗口、截取屏幕时游戏和悬浮窗都不在前台、识别所需的图片还在下载。
    暂停、手动模式平时等待之前先请取帧对象关掉空闲满 30 秒的会话（close_if_idle），按它给的时间设等待上限。
    每 STATS_S 秒把这段时间的取帧统计（FrameSource.take_period：次数、耗时、画面落后、空帧次数）写进识别记录（grab_stats），
    紧接着记一条识别各步耗时和处理器占用（recog_stats，advisor/perfstats.py）；从暂停恢复时，暂停那一段的识别统计丢掉不记，
    这一轮从恢复时重新算起（_drop_paused_period）。
    低配模式（set_lowend）开着时平时的间隔和识别流程里各项的节奏乘 [watch] lowend_factor，文字识别少用几个
    线程（chip_select.LOWEND_OCR_THREADS），看到的东西不变。
    三选一显示着时另有一个线程快速盯绿框（vision/pick_watch.py 的 PickWatcher，取帧对象 screen.BandSource 只截筹码那一带、
    在那个线程里建）：这个线程开始时建、启动，结束时停下；识别流程重建时接到新的识别流程上。识别暂停、手动模式不跟着三选一
    （平时等热键）时告诉识别流程不再盯（FrameProcessor.watch_idle）。它的统计每分钟随 recog_stats 记（pick_watch）。低配模式
    不改它的节奏：只在三选一显示着时查，每次只截一小块、几毫秒。
    线程里出了没接住的错（开头导入识别库、建识别流程出错等）时线程就此结束，不再自动重试：记进错误记录，发 recog_stopped，
    界面上常驻「识别已停止」（见 run）。会自动重试的（识别组件启动失败、截取出错、识别出错）照旧只提示一下。
    """

    # 一帧识别出来的全部事件（照先后）一次交出：界面在一个合并范围里逐个处理，处理完只刷新一次（Overlay.handle_events）。
    # 一帧里没有事件时不发：没人看的帧不惊动界面线程
    events = Signal(object)
    status = Signal(str)
    # 一次性的操作结果提示（按热键识别时画面不是筹码三选一）：界面收到后按 FLASH_MS 自动收起
    # （Overlay.set_flash_status）。和 status 分开：status 发的是截取那边约定的两句固定中文，由截取状态管、留到窗口还原
    flash_status = Signal(str)
    # 识别出错的提示：固定中文，不含报错原文；界面收到后按 RECOG_ERROR_MS 自动收起。和 status 分开，
    # 不靠开头的字符串认（见 Overlay.set_recog_error）
    recog_error = Signal(str)
    # 识别线程出了没接住的错、就此停下：错误已记进错误记录；界面收到后常驻「识别已停止」
    # （Overlay.on_recog_stopped）。会自动重试的几种出错不发这个
    recog_stopped = Signal()
    capture_done = Signal()  # 按热键要求的那一帧识别完了（事件和提示都已先发出）
    capture_changed = Signal(object)  # 截取状态变了（screen.CaptureState），线程开始时也发一次
    STATS_S = 60.0
    NO_FRAME_RETRIES = 3  # 按热键要求的那一帧暂时没有画面（刚建会话）时，隔 NO_FRAME_WAIT_MS 再取，最多再取几次
    NO_FRAME_WAIT_MS = 100

    def __init__(self, db: DB, session: Session, lock: threading.Lock, manual: bool = False, source=None,
                 band_source=None):
        super().__init__()
        self.db, self.session, self.lock = db, session, lock
        self.manual = manual
        # 取帧对象：有 grab()、state、close()、retry() 的对象（ui/screen.py 的 FrameSource）。None 时在线程开始时建一个，
        # 并做窗口截取的线程初始化（测试传入假的取帧对象，不碰系统组件）
        self.source = source
        # 快速盯绿框的取帧对象怎么建（在盯绿框的线程里调用，见 _start_pick_watch）：None 时按 source 建 screen.BandSource；
        # 测试传入了假的 source、没给这一项时没有快速盯绿框
        self.band_source = band_source
        self._pick = None  # 快速盯绿框的线程（vision.pick_watch.PickWatcher），这个线程开始时建、结束时停下
        # 最近一次记下选中项用的那一帧、被临时盖住而没有采用的那一帧（识别流程留的，FrameProcessor.pick_shot、covered_shot；
        # 导出诊断时另存）：同 unread_shot，每帧识别完照识别流程的换，识别流程重建以后照旧留着
        self.pick_shot = None
        self.covered_shot = None
        self._wake = threading.Event()
        # 两个请求（按了热键要求识别一次；游戏窗口换了、游戏重启时请取帧对象再试一次直接截取）：别的线程设上、这个线程取走，
        # 设和取都在 _plock 里做，取走的同时别的线程设上的不会被清掉
        self._requested = False
        self._retry = False
        self._lowend = False  # 低配模式开着（set_lowend 在 _plock 里设，这个线程每一帧开始前在 _plock 里读）
        self._paused: set[str] = set()
        self._paused_at: float | None = None  # 这一次暂停从什么时候开始（time.monotonic）
        # 识别记录用：这一次暂停期间出现过的原因，各原因的细节（focus：minimized 游戏窗口最小化 / nogame 找不到游戏窗口 /
        # screen-background 截取屏幕时游戏和悬浮窗都不在前台）
        self._pause_seen: set[str] = set()
        self._pause_why: dict[str, str] = {}
        self._plock = threading.Lock()
        self._reload = False
        self.last_frame = None  # 最近一次取到的画面（导出诊断用）
        # 最近一次三选一有筹码没读出来时的（时刻, 画面）（识别流程留的那一张，FrameProcessor.unread_shot；导出诊断时另存）。
        # 每帧识别完照识别流程的换（整个元组一次换掉，别的线程读到的时刻和画面是同一次的）；识别流程重建（识别图片下载完、
        # 重新加载）以后照旧留着，直到新的一张来
        self.unread_shot = None
        self.capture_state = None  # 最近一次发出的截取状态
        self.capture_stats: dict | None = None  # 最近一次的取帧统计（诊断包用）
        self.recog_stats: dict | None = None  # 最近一次的识别耗时和处理器占用统计（诊断包用，见 _log_stats）
        self._active = None  # 线程里正在用的取帧对象
        self._finish = lambda: None  # 线程结束时的收尾
        self._ident: int | None = None  # 线程开始后的线程号（退出时等不到它停下，查它停在哪里用）
        # 识别出错的提示去重（见 _due）：每一项上一次出错的时刻（每次出错都更新，不管显示没显示），以及显示过的
        # 几项。具体的一项按界面上的叫法记（RECOG_ITEMS 的值）；截取出错、识别组件启动失败按各自那一句记；整帧识别出错和
        # 没有界面叫法的几项（笼统项）按整帧出错那一句（RECOG_FRAME）记，算同一项
        self._recog_last: dict[str, float] = {}
        self._recog_shown: set[str] = set()
        # 线程结束得是不是意外（界面按这两项判断，见 Overlay.on_recog_stopped）：stopping 为退出时叫停过
        # （stop），这样结束的不算意外；crashed 为线程里出了没接住的错、已经记进错误记录（界面不再另记一笔）
        self.stopping = False
        self.crashed = False

    def request_retry(self) -> None:
        """游戏窗口换了（游戏重启）：下次取帧时请取帧对象再试一次直接截取。哪个线程调用都可以。"""
        with self._plock:
            self._retry = True
        self._wake.set()

    def trigger(self) -> None:
        """立即截图识别一次。哪个线程调用都可以。"""
        with self._plock:
            self._requested = True
        self._wake.set()

    def _take_request(self) -> bool:
        """取走热键要求的识别（返回有没有）。"""
        with self._plock:
            requested, self._requested = self._requested, False
        return requested

    def _take_retry(self) -> bool:
        """取走重试直接截取的请求（返回有没有）。"""
        with self._plock:
            retry, self._retry = self._retry, False
        return retry

    def _due(self, keys) -> list[str]:
        """这几项里这一次该显示的（照先后）：从来没显示过，或者距离这一项上一次出错已满 RECOG_REPEAT_S（同一项这次
        运行只提示一次，10 分钟里没再出、又出错时再提示）。这几项上一次出错的时刻随即都改成现在，显示不显示都改。
        出错记录由各处照旧每次都记，这里只管发不发到界面。在识别线程里调用（每次出错都在这条线程上）。"""
        now = clock()
        due = [k for k in keys
               if k not in self._recog_shown or now - self._recog_last.get(k, now) >= RECOG_REPEAT_S]
        for k in keys:
            self._recog_last[k] = now
        return due

    def _emit_recog(self, text: str) -> None:
        """截取出错、整帧识别出错、识别组件启动失败：一种错一句固定中文（不含报错原文），按这一句去重（_due）。"""
        if self._due([text]):
            self._recog_shown.add(text)
            self.recog_error.emit(text)

    def _emit_recog_items(self, names) -> None:
        """一帧里几项子识别出错（names：FrameProcessor.errors 里各项的名字，照先后）：有界面叫法的（RECOG_ITEMS）各算一项，
        没有的合成一个笼统项（整帧识别出错那一句 RECOG_FRAME，和整帧识别出错算同一项）。各项各自去重（_due）；这一帧有到期的
        具体项时把它们用「、」连成一句发（recog_items_text），只有笼统项到期时才发笼统那一句，都不到期就不发。只发了具体项
        那一句时，同一帧到期的笼统项这一回不算显示过（以前没显示过的，下一帧照样算没显示过）。"""
        keys = list(dict.fromkeys(RECOG_ITEMS.get(name, RECOG_FRAME) for name in names))
        due = self._due(keys)
        items = [k for k in due if k != RECOG_FRAME]
        if items:
            self._recog_shown.update(items)
            self.recog_error.emit(recog_items_text(items))
        elif due:
            self._recog_shown.add(RECOG_FRAME)
            self.recog_error.emit(RECOG_FRAME)

    def set_manual(self, on: bool) -> None:
        self.manual = on
        self._wake.set()

    def set_lowend(self, on: bool) -> None:
        """低配模式开或关：平时多久看一次（idle_ms）和识别流程里各项多久做一次（FrameProcessor.set_pace）
        乘 [watch] lowend_factor，文字识别按 chip_select.LOWEND_OCR_THREADS 个线程（chip_select.use_lowend_threads）；
        三选一显示着时的间隔（active_ms）、出错后等的 2 秒、按热键那一帧没画面时重取的间隔不变。
        哪个线程调用都可以，线程开始以前调也行（main() 启动时按设置套上）。这个线程在下一帧开始前换，正在识别的那一帧照旧。"""
        with self._plock:
            self._lowend = bool(on)
        self._wake.set()

    @property
    def lowend(self) -> bool:
        with self._plock:
            return self._lowend

    def set_paused(self, reason: str, on: bool, why: str = "") -> None:
        """按原因暂停或恢复（几个原因都解除才恢复）。哪个线程调用都可以。

        reason：focus（游戏窗口最小化、找不到游戏窗口，或截取屏幕时游戏和悬浮窗都不在前台）、assets（正在下载识别所需的
        图片）；why 是细节（focus 时为 minimized / nogame / screen-background，悬浮窗按 window_plan 传）。
        恢复时把暂停了多久告诉 Session（on_resume）：离开很久后，期间可能开了新的一局，要请用户核对已有筹码。
        识别记录（Session.log_state）：开始暂停、或暂停期间原因和细节变了时记一条 pause（reasons、why）；恢复时记一条
        resume（seconds：暂停了多久；reasons：期间出现过的原因）。"""
        resumed = None
        record = None
        with self._plock:
            was = bool(self._paused)
            before = (frozenset(self._paused), dict(self._pause_why))
            if on:
                self._paused.add(reason)
                if why:
                    self._pause_why[reason] = why
            else:
                self._paused.discard(reason)
                self._pause_why.pop(reason, None)
            if self._paused and not was:
                self._paused_at = time.monotonic()
                self._pause_seen = set(self._paused)
            elif self._paused:
                self._pause_seen |= self._paused
            elif was and self._paused_at is not None:
                resumed, self._paused_at = time.monotonic() - self._paused_at, None
                record = ("resume", {"seconds": round(resumed, 1), "reasons": sorted(self._pause_seen)})
            if self._paused and (frozenset(self._paused), self._pause_why) != before:
                record = ("pause", {"reasons": sorted(self._paused), "why": dict(self._pause_why)})
        if record is not None:
            with self.lock:
                self.session.log_state(record[0], **record[1])
        if resumed is not None:
            with self.lock:
                self.session.on_resume(resumed)
        self._wake.set()

    @property
    def paused(self) -> bool:
        with self._plock:
            return bool(self._paused)

    def reload(self) -> None:
        """识别所需的图片下载完后，重新建识别流程，让新图片生效。"""
        self._reload = True
        self._wake.set()

    def stop(self, timeout_ms: int = 3000) -> bool:
        """请线程停下，最多等 timeout_ms 毫秒，返回停下了没有（没启动过也算停下了）。正卡在一次很慢的取帧或识别里时
        等不到（_loop 只在取帧前后看要不要停），怎么收场由调用的地方定（Overlay.shutdown）。
        先记下叫停过（stopping）：线程结束以后 Qt 的停止请求就查不到了，界面靠它分清是退出时叫停的还是意外结束的。"""
        self.stopping = True
        self.requestInterruption()
        self._wake.set()
        return bool(self.wait(timeout_ms))

    def current_stack(self) -> str:
        """线程现在停在哪里（调用栈的文字，退出时等不到它停下时写进错误记录）；没在跑、取不到时为空。"""
        frame = sys._current_frames().get(self._ident) if self._ident is not None else None
        return "".join(traceback.format_stack(frame)) if frame is not None else ""

    def _sleep(self, ms: float | None) -> None:
        """等 ms 毫秒（None：一直等）。按热键、切换模式、暂停或恢复、退出时提前醒来。"""
        self._wake.wait(None if ms is None else ms / 1000)
        self._wake.clear()

    def _note_state(self, source) -> None:
        """取帧对象的截取状态和上次发出的不同时发 capture_changed。"""
        state = getattr(source, "state", None)
        if state is not None and state != self.capture_state:
            self.capture_state = state
            self.capture_changed.emit(state)

    def _log_stats(self, source, since: float) -> float:
        """距上次满 STATS_S 秒时把这段时间的取帧统计（FrameSource.take_period：次数、耗时、画面落后、空帧次数，
        取完重新计）写进识别记录（grab_stats），紧接着记一条识别耗时和处理器占用统计（recog_stats，见 _log_recog_stats）。
        取帧统计取不到、或出错时 recog_stats 照样记。返回这一轮从什么时候算起。"""
        now = time.monotonic()
        if now - since < self.STATS_S:
            return since
        take = getattr(source, "take_period", None)
        try:
            stats = take() if callable(take) else None
        except Exception:  # noqa: BLE001  统计出错不影响识别
            log_error("读取取帧统计出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            stats = None
        if isinstance(stats, dict) and stats:
            self.capture_stats = dict(stats)
            with self.lock:
                self.session.log_state("grab_stats", **self.capture_stats)
        self._log_recog_stats()
        return now

    def _log_recog_stats(self) -> None:
        """记一条识别耗时和处理器占用统计（recog_stats）：这一段各步的耗时和处理器占用（
        perfstats.take_period），加上优先级、高性能声明、低配模式和文字识别线程数，有快速盯绿框的线程时另加它这一段的统计
        （pick_watch：查了几次、看到选中几次、截那一块和查绿框各要多久，见 vision.pick_watch.PickWatcher.take_period）。
        交给 Session 记进识别记录，同时存一份在 self.recog_stats（诊断包用）。取统计、读那几项或记录出错时只记错误记录，
        不影响识别。"""
        try:
            data = dict(perfstats.take_period())
        except Exception:  # noqa: BLE001  统计取不到也照样记其余几项
            log_error("读取识别统计出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            data = {}
        pick = self._pick
        if pick is not None:
            try:
                data["pick_watch"] = pick.take_period()
            except Exception:  # noqa: BLE001  这一项取不到也照样记其余几项
                log_error("读取快速查看选中项的统计出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        try:
            from ... import sysinfo
            from ...vision.chip_select import ocr_threads

            data["priority"] = sysinfo.priority_class()
            data["performance"] = sysinfo.performance_state()
            data["lowend"] = self.lowend
            data["ocr_threads"] = ocr_threads()
        except Exception:  # noqa: BLE001  这几项读不到也照样记统计
            log_error("读取优先级等运行状况出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
        self.recog_stats = data
        try:
            with self.lock:
                self.session.log_state("recog_stats", **data)
        except Exception:  # noqa: BLE001
            log_error("记录识别统计出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    @staticmethod
    def _drop_paused_period() -> float:
        """识别刚从暂停恢复：暂停那一段攒下的识别统计（界面线程的 ui_lag 等，暂停前还没记的那一点也在内）
        取走丢掉，不记进识别记录，不然恢复后的第一条要把整段暂停算进去（处理器占用摊薄、ui_lag 堆积）。返回这一轮从什么时候
        算起（恢复的这一刻）：下一条 recog_stats 从恢复时算起，和 grab_stats 一起满 STATS_S 秒再记。在识别线程里调用：
        perfstats.take_period 量的是调用它的线程，处理器占用的基准要取识别线程的。"""
        perfstats.take_period()
        return time.monotonic()

    def source_snapshot(self) -> dict | None:
        """取帧对象的截取字段（FrameSource.snapshot，诊断包的对局状态用；哪个线程都可以调用）；还没有取帧对象时为 None。"""
        fn = getattr(self._active, "snapshot", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001
            log_error("读取截取方式出错", traceback.format_exc(), append=True)
            return None

    def _idle_wait(self) -> float | None:
        """要长时间等待（暂停、手动模式平时）之前：取帧对象连续 30 秒没取帧就关掉会话（释放显卡资源，Windows 10 上
        黄框随之消失）。返回最多等多少毫秒再查一次；没有开着的会话时为 None（一直等）。"""
        fn = getattr(self._active, "close_if_idle", None)
        if fn is None:
            return None
        try:
            wait = fn()
        except Exception:  # noqa: BLE001
            log_error("关闭空闲的截取会话出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            return None
        return None if wait is None else max(float(wait), 0.05) * 1000

    def _open_source(self):
        """取帧对象和用完时的收尾：传入的（测试）直接用；否则做窗口截取的线程初始化，建 screen.FrameSource。
        建不了时记进错误记录，返回 (None, 收尾)，稍后再试。"""
        from .. import screen

        if self.source is not None:
            source = self.source
            return source, lambda: self._close_source(source)
        wc = None
        try:
            from ...vision import window_capture as wc

            wc.init_thread()  # 窗口截取的系统组件按线程初始化（多线程模式）
        except Exception:  # noqa: BLE001  组件用不了：取帧对象自己会改为截取屏幕
            log_error("窗口截取组件的线程初始化出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            wc = None
        try:
            source = screen.FrameSource()
        except Exception:  # noqa: BLE001
            log_error("取帧对象初始化出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            self._emit_recog(RECOG_STARTUP)
            if wc is not None:
                self._uninit(wc)
            return None, lambda: None

        def finish():
            self._close_source(source)  # 先关会话再做线程收尾
            if wc is not None:
                self._uninit(wc)

        return source, finish

    @staticmethod
    def _close_source(source) -> None:
        try:
            source.close()
        except Exception:  # noqa: BLE001
            log_error("关闭取帧对象出错", traceback.format_exc(), append=True)

    @staticmethod
    def _uninit(wc) -> None:
        try:
            wc.uninit_thread()
        except Exception:  # noqa: BLE001
            log_error("窗口截取组件的线程收尾出错", traceback.format_exc(), append=True)

    def run(self) -> None:
        """线程的入口：整个包在 try 里，含 _loop 开头导入识别库的那几行。出了没接住的错时线程就此结束
        （_crashed：记进错误记录、发 recog_stopped），不再悄悄退出；快速盯绿框的线程停下、关会话、线程收尾照常做。"""
        self._ident = threading.get_ident()
        self._active = None  # 正在用的取帧对象
        self._finish = lambda: None  # 线程结束时的收尾（关会话、线程收尾）
        try:
            self._loop()
        except BaseException:  # noqa: BLE001  一律接住：交给 Qt 只会打到没人看的错误输出，界面一直以为还在识别
            self._crashed(traceback.format_exc())
        finally:
            self._stop_pick()
            self._finish()
            self._ident = None

    def _start_pick_watch(self):
        """建、启动快速盯绿框的线程（vision.pick_watch.PickWatcher）。取帧对象在那个线程里第一次要查时建：band_source 给了时
        用它，否则 screen.BandSource（截取方式跟着这个线程的取帧对象）；测试传入了假的取帧对象（source）、没给 band_source 时
        不建。建不了时只记错误记录，照常识别（没有快速盯绿框）。返回建好的线程或 None。"""
        make = self.band_source
        if make is None:
            if self.source is not None:
                return None
            from .. import screen

            def make():
                return screen.BandSource(self._active, log=lambda title, detail: log_error(title, detail, append=True))
        try:
            from ...vision.pick_watch import PickWatcher

            pick = PickWatcher(make, paused=lambda: self.paused,
                               on_error=lambda title, detail: log_error(title, detail, append=True))
            pick.start()
        except Exception:  # noqa: BLE001  快速盯绿框只是补充：起不来照常识别
            log_error("快速查看选中项的线程启动出错（拾星助手仍在运行，识别照常）", traceback.format_exc(), append=True)
            return None
        return pick

    def _stop_pick(self) -> None:
        """停下快速盯绿框的线程（这个线程结束时）。等不到它停下、出错时只记错误记录。"""
        pick, self._pick = self._pick, None
        if pick is None:
            return
        try:
            if not pick.stop():
                log_error("快速查看选中项的线程没有及时停下", "", append=True)
        except Exception:  # noqa: BLE001
            log_error("停下快速查看选中项的线程出错", traceback.format_exc(), append=True)

    def _attach_pick(self, processor) -> None:
        """识别流程（新）建好以后，把快速盯绿框的线程接到它的交换（FrameProcessor.pick_watch）上；原来的那一份关掉。
        测试里的假识别流程没有交换：接 None（不盯）。"""
        if self._pick is None:
            return
        from ...vision.pick_watch import PickWatch

        exchange = getattr(processor, "pick_watch", None)
        self._pick.attach(exchange if isinstance(exchange, PickWatch) else None)

    @staticmethod
    def _watch_idle(processor) -> None:
        """要长时间不取帧之前（暂停、手动模式不跟着三选一）：告诉识别流程不再盯绿框（FrameProcessor.watch_idle）。没有这个
        方法的（测试里的假对象）不告诉。"""
        idle = getattr(processor, "watch_idle", None)
        if callable(idle):
            try:
                idle()
            except Exception:  # noqa: BLE001  只是让盯绿框的线程歇着：出错不影响识别
                log_error("让快速查看选中项停下时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)

    def _crashed(self, detail: str) -> None:
        """识别线程出了没接住的错（run）：记进错误记录（错误记录本身也写不进时就算了），发 recog_stopped。退出时叫停以后
        才出的错照样记；界面那边按正在退出处理，不显示（Overlay.on_recog_stopped 看 stopping）。"""
        self.crashed = True
        try:
            log_error("后台识别出错，识别已停止（拾星助手仍在运行）", detail, append=True)
        except Exception:  # noqa: BLE001  记不进就算了，照样告诉界面
            pass
        try:
            self.recog_stopped.emit()
        except RuntimeError:  # 退出时 Qt 那边的对象已经删掉：界面也不在了，不用再告诉
            pass

    def _ensure_source(self):
        """正在用的取帧对象；还没有时建一个（线程开始时，或上次没建成），建好后发一次截取状态。"""
        if self._active is None:
            source, finish = self._open_source()
            if source is not None:
                self._active, self._finish = source, finish
                self._note_state(source)
        return self._active

    def _loop(self) -> None:
        from ...vision.capture import WindowGone
        from ...vision.chip_select import use_lowend_threads
        from ...vision.pipeline import FrameProcessor, limit_cv_threads
        from .. import screen

        limit_cv_threads()  # OpenCV 不按全部核心分线程（全进程的设置，见 pipeline.CV_THREADS）

        watch = self.db.rules.get("watch", {})
        idle_ms = watch.get("idle_ms", 500)
        active_ms = watch.get("active_ms", 300)
        factor = watch.get("lowend_factor", LOWEND_FACTOR)  # 低配模式：平时的间隔和各项识别的节奏乘它
        follow = watch.get("manual_follow", True)
        no_frame = getattr(screen, "NoFrame", None) or ()  # 暂时没有画面（不当出错）
        processor = None
        paced = False  # 识别流程现在是不是按低配模式的节奏（新建的按 [watch] 的原值）
        # 文字识别现在是不是按低配模式的线程数（chip_select.use_lowend_threads）：引擎是全进程共用的，不跟着识别流程重建，
        # 和 paced 分开记
        threads_lowend = False
        manual_told = None  # 告诉过识别流程的手动模式开关（set_manual；None 为还没告诉过，识别流程刚建）
        resumed = False  # 刚从暂停恢复：下一帧之前告诉识别流程（resumed）
        # 识别流程上一帧还有排到下一帧的读数（FrameProcessor.pending）：不等 idle_ms，紧接着取下一帧接着做
        pending = False
        following = 0  # 手动模式：还要继续看几帧
        retries = 0  # 按热键要求的那一帧暂时没有画面时已经重取了几次
        stats_at = time.monotonic()
        was_paused = False  # 上一轮是暂停着的：恢复时丢掉暂停那一段的识别统计（_drop_paused_period）
        self._ensure_source()  # 线程一开始就建好取帧对象，界面马上知道实际的截取方式
        self._pick = self._start_pick_watch()  # 快速盯绿框的线程（三选一显示着时才查）

        while not self.isInterruptionRequested():
            requested = self._take_request()
            if self.paused:
                was_paused = True
                following = retries = 0
                if requested:
                    self.capture_done.emit()
                self._watch_idle(processor)
                self._sleep(self._idle_wait())
                continue
            if was_paused:
                was_paused = False
                resumed = True
                stats_at = self._drop_paused_period()
            if self.manual and not requested and not following and not pending:
                self._watch_idle(processor)  # 手动模式不跟着三选一的时候不盯
                self._sleep(self._idle_wait())
                continue
            source = self._ensure_source()
            if source is None:
                if requested:
                    self.capture_done.emit()
                self._sleep(2000)
                continue
            if processor is None or self._reload:
                if self._reload:
                    # 识别所需的图片刚下载完：各识别模块缓存的图片清单作废，重新读取
                    from ...vision import reset_caches

                    reset_caches()
                self._reload = False
                processor = FrameProcessor(self.db, self.session, self.lock)
                self._attach_pick(processor)
                paced = False
                manual_told = None
                pending = False
            manual = bool(self.manual)
            if manual != manual_told:
                # 手动模式开关变了（或识别流程刚建）：两帧之间告诉识别流程。没有 set_manual 的（测试里的假对象）不告诉
                set_manual = getattr(processor, "set_manual", None)
                if callable(set_manual):
                    set_manual(manual)
                manual_told = manual
            if resumed:
                resumed = False
                mark = getattr(processor, "resumed", None)
                if callable(mark):
                    mark()
            lowend = self.lowend
            if lowend != paced:
                # 开关变了（或识别流程刚重建）：在两帧之间换节奏，正在识别的那一帧不受影响。改的是识别流程身上的几个间隔，
                # 它记着的时刻和沿用的结论不动（不重建它，不然切一次开关就多读一轮）。没有 set_pace 的（测试里的假对象）不改
                set_pace = getattr(processor, "set_pace", None)
                if callable(set_pace):
                    set_pace(factor if lowend else 1)
                paced = lowend
            if lowend != threads_lowend:
                # 文字识别的线程数也在这里、两帧之间换：引擎的线程数不同时丢掉，下一次识别按新的线程数重建
                use_lowend_threads(lowend)
                threads_lowend = lowend
            idle = idle_ms * factor if paced else idle_ms
            if self._take_retry():
                try:
                    source.retry()
                except Exception:  # noqa: BLE001
                    log_error("请取帧对象重试直接截取时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            try:
                img = source.grab()
            except no_frame:
                # 暂时没有画面（刚建会话、画面还没送来）：不当出错。按热键要求的那一帧稍后再取几次
                self._note_state(source)
                if requested and retries < self.NO_FRAME_RETRIES:
                    retries += 1
                    with self._plock:
                        self._requested = True  # 放回去：等一会儿再取（不叫醒，照样等 NO_FRAME_WAIT_MS）
                    self._sleep(self.NO_FRAME_WAIT_MS)
                    continue
                retries = 0
                if requested:
                    self.capture_done.emit()
                self._sleep(active_ms if following else idle)
                continue
            except WindowGone as e:
                # 找不到游戏窗口、窗口最小化：截取那边约定的两句固定中文（capture.WindowGone.notice），原样显示。别的
                # RuntimeError 不算这一种，走下面截取出错的分支（原文只进错误记录）
                self._note_state(source)
                self.status.emit(e.notice)
                following = retries = 0
                self._sleep(2000)
                continue
            except Exception:  # 取帧或系统调用出错（WindowGone 以外的 RuntimeError 也在内）：记下来，稍后重试，不让后台线程退出
                self._note_state(source)
                log_error("截图出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                self._emit_recog(RECOG_CAPTURE)
                if requested:
                    # 同一句提示去重以后可能不发（见 _emit_recog）：照样告诉界面按热键的这一帧完了，「正在识别…」才收得起来
                    self.capture_done.emit()
                following = retries = 0
                self._sleep(2000)
                continue
            retries = 0
            self._note_state(source)
            stats_at = self._log_stats(source, stats_at)
            self.last_frame = img
            if self.isInterruptionRequested():
                break  # 取帧的时候要退出了：这一帧不再识别（一次识别要零点几秒，慢电脑上更久），早点停下
            frame_at = time.monotonic()
            try:
                # 按热键要求的这一帧：不沿用「画面没变、上次认定不是三选一」的结论，一律重新识别
                events = processor.process(img, frame_at, force=requested)
            except Exception:  # 识别出错不应该让悬浮窗退出
                perfstats.record("frame", (time.monotonic() - frame_at) * 1000.0)  # 出错的那一帧也记，耗时统计不漏掉出错的帧
                log_error("识别出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
                self._emit_recog(RECOG_FRAME)
                if requested:
                    self.capture_done.emit()  # 同上：提示可能去重不发，「正在识别…」照样收起
                pending = False
                self._sleep(2000)
                continue
            perfstats.record("frame", (time.monotonic() - frame_at) * 1000.0)
            # 只认真的 True：测试里的 MagicMock 这类不算，不然一直不等
            pending = getattr(processor, "pending", False) is True
            for name in ("unread_shot", "pick_shot", "covered_shot"):
                shot = getattr(processor, name, None)
                if isinstance(shot, tuple):  # 只认元组：识别流程刚重建、还没有时为 None，测试里的假对象也不算
                    setattr(self, name, shot)
            if events:
                self.events.emit(events)
            # 这一帧认三选一出错（FrameProcessor.select_failed）：不知道是不是三选一（chip_select_visible 还是上一帧的）。下面
            # 接着跟随、定取帧的节奏都按没显示算；按热键要求的这一帧也不报「不是三选一」，只报「识别出错」。只认真的 True
            unknown = getattr(processor, "select_failed", False) is True
            visible = processor.chip_select_visible and not unknown
            kinds = {ev.kind for ev in events}
            # 三选一被玩家隐藏着（FrameProcessor.select_hidden，画面上是「您当前正处于隐藏UI状态」）：Session 当它还开着，
            # 手动模式跟随三选一时也接着看，等它回来或关掉再记下选择（取帧按平时的间隔）。按热键要求的这一帧是隐藏着的时候
            # 也不报「不是三选一」：推荐照旧留着；Session 还不知道这一次的（没按过热键就隐藏了），点「显示」后跟随着认出。
            # 只认真的 True
            hidden = getattr(processor, "select_hidden", False) is True
            if (requested and not visible and not unknown and not hidden
                    and not kinds & {EV_PICKED, EV_MISSED_PICK, EV_NEWGAME}):
                self.flash_status.emit("已截图识别：当前画面不是筹码三选一界面")
            if requested:
                # 仍是同一个三选一界面时 Session 不发事件，界面靠这个信号收起「正在识别…」
                self.capture_done.emit()
            if not (self.manual and follow) or kinds & CLOSED:
                following = 0
            elif visible or hidden:
                following = FOLLOW_FRAMES
            elif following:
                following -= 1
            hints = getattr(processor, "hints", None)
            if isinstance(hints, WatchHints):
                # 识别流程这一帧最后一次在锁里调完 Session 时顺手取的（FrameProcessor._renew）：不为它再进一次锁
                holding = hints.holding
            else:
                with self.lock:
                    holding = bool(self.session.holding)
            if visible and holding:
                # 正在等候选筹码读全（三选一刚出现时有筹码没读出来、刷新动画的中间帧等，SelectTracker.hold_frame，最多约 1 秒）：
                # 手动模式不跟随三选一时也接着截图，不然刚出现时的推荐、「正在刷新」后的新推荐都要等下一次按热键才出来
                following = max(following, 1)
            # 这一帧有几项子识别出错（FrameProcessor 各自接住，其余读数上面已照常交出）：每个错照整帧出错时一样记进错误记录，
            # 提示合成一句（_emit_recog_items）。三选一没显示（或不知道显示没显示）时隔 2 秒再取下一帧；显示着时照常按
            # active_ms 取，隔得久了容易漏看「选中」那一下（同一个错错误记录只整条记一次，取得勤也不刷屏）。errors 只认列表、
            # 元组，别的（测试里的假对象等）不算有错
            failed = getattr(processor, "errors", None)
            failed = list(failed) if isinstance(failed, (list, tuple)) else []
            for _, _, detail in failed:
                log_error("识别出错（拾星助手仍在运行）", detail, append=True)
            if failed:
                self._emit_recog_items([name for name, _, _ in failed])
            # 没显示三选一、还有排到下一帧的读数（pending）时不等：一帧只做一项读数，做完一项马上取下一帧，先看是不是三选一
            # 再接着读
            self._sleep(active_ms if visible else (2000 if failed else (0 if pending else idle)))
