"""文字识别线程数的性能自检在悬浮窗这一边（OcrCheck）：什么时候量、量完存哪、用不用量出来的线程数、结果差时查什么原因、
提示栏上写什么。

量本身在 advisor/vision/ocr_check.py：用程序自己画的一张图比几种线程数，不截游戏画面、不联网、不写文件。这里只在
不打扰玩家的时候叫它量：启动 30 秒以后才开始看，游戏窗口在前台、不在对局里、识别没暂停、低配模式关着时才量；量的时候
和真正的识别共用同一把引擎锁，不会两边一起慢。量不出来（忽快忽慢、太慢、出错）当作这一次没量出来，照默认线程数
（chip_select.OCR_THREADS 的 4 个）；最多试 MAX_TRIES 次（累计，跨启动），这次运行最多开始 MAX_RUNS 次，进进出出对局时
不会一直试。

结果差（较差、太慢、忽快忽慢）时另查原因（advisor/vision/ocr_reason.py）：查之前先记一段处理器、电源、内存的读数，查
出来的原因最多写两条，能替玩家改的放最后。结果一般时，有大小核的电脑上只比一下只用大核（ocr_reason.find 的
cores_only）：比出来文字识别被排到了小核时和较差一样写出来、问要不要只用大核，别的原因不写；没排到小核时照旧只写「一般」。
比出来的比值不管到没到都存进设置（readings 的 gain），设置窗口「只用大核」一项的说明、出不出现按它（recorded_gain）；
再量时没比的（开关开着、结果良好、比到一半停下等）留着这台电脑上一次比出来的（gain_kept）。
玩家点了「是」就改（改本进程自己的设置：省电声明、优先级、能用哪些核、只用大核、低配模式），改好了过一会儿再量一次；
点了「否」记进设置，这台电脑上不再问同一件事。读不到的一律当没查到。

提示栏（标题栏下方，notices.ocr_check_row 排成提示行）：量的时候一条绿字，写明可以照常玩、还要多久，每 TICK_MS 刷新一次；
量完换成结果，按读一次的快慢分良好、一般、较差（FAIR_MS、POOR_MS），较差和太慢量不完的另写查到的原因，一般的查到排在
小核上时也写。较差、太慢的留到玩家关掉（KEEP_KINDS，对局进行中先不显示），其余的 RESULT_MS 后自己收起；等玩家回答的、
等着重新量的不自己消失。中途停下（进了对局等）只去掉提示，之后再量。

结果写进设置（UiSettings.ocr_check），诊断包带着设置文件，别人发诊断包就看得到。量出来以后下一次识别按新的线程数
重建引擎（chip_select.use_ocr_threads）；低配模式开着时照旧用低配模式的线程数。
"""
from __future__ import annotations

import math
import os
import threading
import time
import traceback
from dataclasses import dataclass

from .. import log_error

FIRST_DELAY_MS = 30000  # 启动 30 秒以后才开始看：不和启动、第一次识别、约 15 秒时的检查更新抢时间
POLL_MS = 30000  # 没量成时多久再看一次
MAX_TRIES = 3  # 累计（跨启动）最多试几次：出错、忽快忽慢、太慢才算一次
MAX_RUNS = 3  # 这次运行最多开始几次（中途停下的也算）：进进出出对局时不一直试
RETEST_DELAY_MS = 1500  # 改好以后过一会儿再量：让玩家看到「改好了」

ERROR = "error"  # 量的时候出错（Result.reason 以外，记在设置的 why 里）

TICK_MS = 1000  # 量的时候多久刷新一次提示栏（剩余时间跟着变）
RESULT_MS = 30000  # 结果那一条多久自己收起（KEEP_KINDS 的不收，留到玩家关掉）
JITTER_S = 3  # 估的剩余时间比显示的多出这么多秒才往上改：估的数来回抖时，显示的只往下走
# 读一次合成图的中位毫秒：不超过 FAIR_MS 算良好，超过 POOR_MS 算较差，中间算一般。真实的三选一比合成图慢一倍左右，一次三选一
# 要读不止一次：超过 POOR_MS 时读一次真实画面要 1.5 秒以上，推荐明显晚
FAIR_MS = 350
POOR_MS = 800
KEEP_KINDS = ("poor", "slow")  # 性能差的结果留到玩家关掉

# OcrNote.kind 里另外三种：改好了等着重新量、打开了低配模式、没改成
FIXED = "fixed"
LOWEND_ON = "lowend_on"
FIX_FAILED = "fix_failed"

# 能记进「这台电脑上不再问」的修法（ocr_reason.Reason.fix）
DECLINABLE = ("eco", "priority", "affinity", "big_cores", "lowend")


def recorded_gain(record, cores: int | None = None) -> float | None:
    """设置里的自检记录（UiSettings.ocr_check）上这台电脑比出来的只用大核的收益：readings 里的 gain（不限制的中位毫秒 ÷ 只用
    大核的中位毫秒；最近一次没比时是留下来的上一次的，readings 的 gain_kept 为真）。记录的核心数和这台电脑的逻辑核心数（cores，
    默认 os.cpu_count()）相同、比过时才有，其余（没有记录、换了电脑、没比过、值不是数）为 None。设置窗口「只用大核」一项的说明
    和出不出现按它（sysinfo.on_small_cores）。纯函数。"""
    if cores is None:
        cores = os.cpu_count() or 1
    if not isinstance(record, dict) or record.get("cores") != cores:
        return None
    readings = record.get("readings")
    gain = readings.get("gain") if isinstance(readings, dict) else None
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain):
        return None
    return float(gain)


@dataclass(frozen=True)
class OcrNote:
    """提示栏上性能自检那一条（OcrCheck.notice 给，notices.ocr_check_row 排成提示行）。kind：running 正在量（left：还要多少秒，
    0 是快好了）；good、fair、poor 量出来了，按读一次的快慢分（threads：用几个线程，ms：读一次的中位毫秒）；slow 太慢量不完、
    noisy 忽快忽慢量不准、error 出错（threads：照用的默认线程数，retry：下次启动还会再试）；fixed 改好了等着重新量、
    lowend_on 打开了低配模式、fix_failed 没改成（这三个不带别的数）。reasons 是查到的原因（ocr_reason.Reason，最多两条，能替
    玩家改的在最后）；offer 是最后一条能替玩家改的修法，空串是没有要问的。"""

    kind: str
    left: int = 0
    threads: int = 0
    ms: int = 0
    retry: bool = False
    reasons: tuple = ()

    @property
    def offer(self) -> str:
        return self.reasons[-1].fix if self.reasons else ""


class OcrCheck:
    """文字识别线程数的自检（模块说明）。session、lock 是第一张的设置；run_background 起后台线程（测试里换成直接调用）；
    later(毫秒, 函数) 是「过一会儿再做」（界面线程）；watcher 返回第一张的 Watcher 或 None；activity 返回前台状态
    （follow.WindowFollower.activity）；sessions 返回现在各张的 Session（两开时两个，共用 lock）：有一张在对局里就不量；
    refresh 刷新悬浮窗（提示栏现取 notice）；clock 是算剩余时间用的时钟（测试换成假的）。

    game_pids 返回现在各张悬浮窗对应的游戏进程号（界面线程调用）；set_lowend、set_big_cores 点「是」时用（界面线程调用，
    set_big_cores 返回改没改成）；big_cores_on 返回设置里「只用大核」开着没有；reasons 是查原因的模块（测试换成假的，默认
    用的时候才导入 ocr_reason）。"""

    def __init__(self, *, session, lock, run_background, later, watcher, activity, sessions, refresh,
                 clock=time.monotonic, game_pids=None, set_lowend=None, set_big_cores=None,
                 big_cores_on=None, reasons=None):
        self.session, self.lock = session, lock
        self.run_background, self.later = run_background, later
        self.watcher = watcher
        self.activity = activity
        self.sessions = sessions
        self.refresh = refresh
        self.clock = clock
        self.game_pids = game_pids
        self.set_lowend = set_lowend
        self.set_big_cores = set_big_cores
        self.big_cores_on = big_cores_on
        self._reasons_source = reasons
        self._started = False  # 这次运行已经排过（或已经不用量了）
        self._finished = False  # 这次运行不用再看（量完了、量满了次数、不用量）
        self._running = False  # 后台正在量
        self._runs = 0  # 这次运行已经开始了几次
        self._pids: list = []  # 这一次量时各张悬浮窗对应的游戏进程号（开始量时取下）
        self._stop = threading.Event()  # 退出时叫停
        self._done = threading.Event()  # 后台那一次量完了（没在量时是设着的）
        self._done.set()
        self._left = (0.0, 0.0)  # 最近一次估的剩余秒数和估的时刻：后台写、界面线程读，整个元组一次换掉
        self._shown_left: int | None = None  # 提示栏上显示着的秒数（界面线程）
        self._outcome: OcrNote | None = None  # 量完要告诉玩家的（后台写一次；收起以后为 None）
        self._ticking = False  # 刷新提示栏的定时器在走（界面线程）

    # ------------------------------------------------------------ 启动

    def startup(self) -> None:
        """启动时（main() 对第一张调一次）：记录在这台电脑上还有效就照用、不再量；候选不足两个、或者这台电脑已经量满
        MAX_TRIES 次就不再量；其余过 FIRST_DELAY_MS 开始看。核心数变了的旧记录当没有，次数从 0 算。上次量出来很差、记着要
        再测（retest）又没用满次数时，照用记着的线程数，但仍算没做完、照常再量一次。
        识别模块在这里才导入，不在模块开头：皮肤预览的子进程也导入悬浮窗，用不到 OpenCV。这时识别要用的库已经加载过了
        （startup.preload），不会让界面明显变慢。"""
        if self._started:
            return
        self._started = True
        from ...vision import chip_select, ocr_check

        cores = os.cpu_count() or 1
        with self.lock:
            record = dict(self.session.settings.ocr_check or {})
        threads = record.get("threads")
        if record.get("cores") == cores and isinstance(threads, int) and threads in ocr_check.candidates(cores):
            chip_select.use_ocr_threads(threads)
            if record.get("retest") and int(record.get("tries") or 0) < MAX_TRIES:
                self.later(FIRST_DELAY_MS, self._poll)
            else:
                self._finished = True
            return
        if len(ocr_check.candidates(cores)) < 2:
            self._finished = True
            return
        if record.get("cores") == cores and int(record.get("tries") or 0) >= MAX_TRIES:
            self._finished = True
            return
        self.later(FIRST_DELAY_MS, self._poll)

    # ------------------------------------------------------------ 定时看、条件

    def _poll(self) -> None:
        """界面线程的定时器：停了或不用再看就返回；没在量、条件都满足时开始一次，这次运行开始满 MAX_RUNS 次就不再看；
        还没做完就过 POLL_MS 接着看。不从后台线程调用（later 用 QTimer，只能在界面线程）。"""
        if self._finished or self._stop.is_set():
            return
        if not self._running:
            if self._runs >= MAX_RUNS:
                self._finished = True
                return
            if self._ready():
                self._begin()
        if not self._finished:
            self.later(POLL_MS, self._poll)

    def _ready(self) -> bool:
        """现在能不能量：有识别线程、没暂停（游戏窗口在、没最小化、没在下载那一大批挡着识别的图片、没在跑性能测试；只补少量
        图片、被拒绝过以后慢速下载时识别照常，也可能量）、低配模式关着、游戏窗口在前台、哪一张都不在对局里。手动模式下读不到
        对局，照样可以量。"""
        watcher = self.watcher()
        if watcher is None or watcher.paused or watcher.lowend:
            return False
        if self.activity() != "game":
            return False
        return not self._in_game()

    def _in_game(self) -> bool:
        """有一张在对局里：文字识别引擎全程序只有一个，另一张的识别也要和量的时候排队。连着一阵子看不到对局里的读数的
        （Session.game_quiet：中途退出、回到了大厅等，这一局的状态还留着）不算；又读到了就算，量到一半照样停下。"""
        with self.lock:
            return any(session.has_game_state and not session.game.game_over and not session.game_quiet
                       for session in self.sessions())

    def _should_stop(self) -> bool:
        """后台线程里给 ocr_check.run 的 stop：叫停了、识别线程没了、暂停了、低配模式开了、进了对局，有一条就停。
        不看 activity（Qt 部件的状态只在界面线程看）。"""
        if self._stop.is_set():
            return True
        watcher = self.watcher()
        if watcher is None or watcher.paused or watcher.lowend:
            return True
        return self._in_game()

    # ------------------------------------------------------------ 量

    def _begin(self) -> None:
        """界面线程：开始一次，提示栏马上写上（剩余时间先按估的，量到东西以后按实际的）。先取下各张悬浮窗对应的游戏
        进程号（查原因时只读它们的处理器时间）。"""
        from ...vision import ocr_check

        try:
            self._pids = list(self.game_pids()) if self.game_pids is not None else []
        except Exception:  # noqa: BLE001  取不到进程号时按空，查原因时游戏占用算读不到
            self._pids = []
        self._running = True
        self._runs += 1
        self._done.clear()
        self._on_progress(ocr_check.first_guess(os.cpu_count() or 1))
        self._shown_left = None
        self.run_background(self._work)
        if not self._ticking:
            self._ticking = True
            self._tick()

    def _reasons(self):
        """查原因的模块：测试传了假的就用假的，否则用的时候才导入（同 startup 里导入识别模块的理由）。"""
        if self._reasons_source is not None:
            return self._reasons_source
        from ...vision import ocr_reason

        return ocr_reason

    def _declined(self) -> tuple:
        """这台电脑上玩家说过不要再问的修法（记录的核心数和现在不同时按没有）。"""
        cores = os.cpu_count() or 1
        with self.lock:
            record = dict(self.session.settings.ocr_check or {})
        if record.get("cores") != cores:
            return ()
        value = record.get("declined")
        return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()

    def _big_cores_set(self) -> bool:
        """设置里「只用大核」现在开着没有（回调本身负责持锁；取不到按没开）。"""
        if self.big_cores_on is None:
            return False
        try:
            return bool(self.big_cores_on())
        except Exception:  # noqa: BLE001  读不到按没开
            return False

    @staticmethod
    def _has_big_cores() -> bool:
        """这台电脑有没有大小核（sysinfo.has_big_cores；读不到按没有）：结果一般时只在有大小核的电脑上比只用大核。"""
        from ... import sysinfo

        try:
            return bool(sysinfo.has_big_cores())
        except Exception:  # noqa: BLE001  读不到按没有，不比
            return False

    def _work(self) -> None:
        """后台线程：量一次；结果差时再查原因（读一段处理器、电源、内存，必要时比一下只用大核的收益），结果一般、这台电脑
        有大小核时只比只用大核（cores_only，只看是不是排到了小核），把结果写进设置、换线程数、记识别记录。比的时候提示栏
        照旧写剩余时间（progress）。良好、出错、中途停下不查。量的时候出错只记错误记录（算一次没量出来）；查原因出错不影响
        结果。"""
        from ...vision import ocr_check

        reasons = probe = None
        try:
            reasons = self._reasons()
            probe = reasons.begin(self._pids)
        except Exception:  # noqa: BLE001  记不了读数时只影响查原因
            probe = None
        try:
            result = ocr_check.run(self._should_stop, progress=self._on_progress)
        except Exception:  # noqa: BLE001  量的时候出错不影响识别，照默认线程数
            log_error("量文字识别的线程数时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            result = ocr_check.Result(None, {}, 0, ERROR)
        shown: tuple = ()
        readings: dict = {}
        kind = self._kind_of(result)
        try:
            cores_only = kind == "fair" and probe is not None and self._has_big_cores()
            if probe is not None and (kind in ("poor", "slow", "noisy") or cores_only) and not self._stop.is_set():
                big_on = self._big_cores_set()
                shown, readings = reasons.find(
                    probe, kind, threads=result.threads or ocr_check.DEFAULT_THREADS, stop=self._should_stop,
                    progress=self._on_progress, declined=self._declined(), big_cores_on=big_on,
                    cores_only=cores_only)
                # 比的时候动过核的限制：比完按现在的设置放回去（比的时候玩家可能在设置里改过）
                if self._big_cores_set():
                    from ... import sysinfo

                    sysinfo.use_big_cores(True)
        except Exception:  # noqa: BLE001  查原因出错不影响结果
            log_error("查性能自检的原因时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
            shown, readings = (), {}
        finally:
            if probe is not None:
                try:
                    probe.finish()
                except Exception:  # noqa: BLE001  收尾出错不影响结果
                    pass
        try:
            self._apply(result, shown, readings)
        finally:
            self._running = False
            self._done.set()

    def _on_progress(self, left: float) -> None:
        """记下还要多少秒（量的时候后台线程调用，只换一个元组，不碰界面）。"""
        self._left = (float(left), self.clock())

    def _apply(self, result, shown: tuple = (), readings: dict | None = None) -> None:
        readings = readings or {}
        from ...vision import chip_select, ocr_check

        cores = os.cpu_count() or 1
        tries = 0
        if result.threads is not None:
            # 先换线程数（不持 lock：它要等 _ENGINE_LOCK，不嵌套拿两把锁），再把这一项写进设置
            chip_select.use_ocr_threads(result.threads)
        elif result.reason != ocr_check.STOPPED:
            # 这次没量出来：换回默认线程数，和提示上写的、下次启动时用的一样（再量的时候手上可能是上一次量出来的数）
            chip_select.use_ocr_threads(ocr_check.DEFAULT_THREADS)
        kind = self._kind_of(result)
        retest = False
        with self.lock:
            settings = self.session.settings
            record = dict(settings.ocr_check or {})
            same = record.get("cores") == cores
            declined = record.get("declined") if same else None
            declined = [item for item in declined if isinstance(item, str)] if isinstance(declined, list) else []
            data = None
            if result.threads is not None:
                data = {"cores": cores, "threads": int(result.threads),
                        "ms": {str(n): int(ms) for n, ms in result.ms.items()}, "rounds": int(result.rounds)}
            elif result.reason != ocr_check.STOPPED:
                tries = (int(record.get("tries") or 0) if same else 0) + 1
                data = {"cores": cores, "tries": tries, "why": result.reason,
                        "ms": {str(n): int(ms) for n, ms in result.ms.items()}}
            transient_poor = (result.threads is not None and kind == "poor"
                              and any(reason.transient for reason in shown))
            if transient_poor:
                # 结果差、原因是会变的：记着下次启动再量一次，次数接着上一次算，没量出来的那几次也算在内（较差和太慢交替
                # 出现时才不会没完没了）
                previous = int(record.get("tries") or 0) if same else 0
                tries = previous + 1
                retest = tries < MAX_TRIES
            if data is not None:
                if declined:
                    data["declined"] = declined
                if shown:
                    data["reasons"] = [reason.key for reason in shown]
                if readings:
                    data["readings"] = readings
                kept = recorded_gain(record, cores)
                if kept is not None and recorded_gain(data, cores) is None:
                    # 这次没比只用大核（开关开着、结果良好、比到一半停下等）：留着这台电脑上一次比出来的倍数，设置窗口「只用大核」
                    # 一项照它写（点了「是」以后又关掉，照样提醒在小核上）。gain_kept 标明是上一次比的、不是这次读的；识别记录里
                    # 照记这次的读数
                    data["readings"] = {**data.get("readings", {}), "gain": kept, "gain_kept": True}
                if transient_poor:
                    data["tries"] = tries
                    if retest:
                        data["retest"] = True
                settings.ocr_check = data
                self.session.save()
            self.session.log_state("ocr_check", threads=result.threads,
                                   ms={str(n): int(ms) for n, ms in result.ms.items()},
                                   rounds=result.rounds, why=result.reason,
                                   reasons=[reason.key for reason in shown] if shown else [],
                                   readings=readings)
        self._outcome = self._note_of(result, tries, shown, retest=retest)
        if result.threads is not None or result.reason != ocr_check.STOPPED:
            self._finished = True  # 量出来了，或者这一次没量成（下次启动再试）
        elif self._runs >= MAX_RUNS:
            self._finished = True

    @classmethod
    def _kind_of(cls, result) -> str | None:
        """这一次是良好、一般、较差、太慢、量不准、出错，还是中途停下（None）。"""
        from ...vision import ocr_check

        if result.threads is not None:
            ms = int(result.ms.get(result.threads, 0))
            return "poor" if ms > POOR_MS else "fair" if ms > FAIR_MS else "good"
        if result.reason in (ocr_check.SLOW, ocr_check.NOISY, ERROR):
            return result.reason
        return None

    @classmethod
    def _note_of(cls, result, tries: int, shown: tuple = (), *, retest: bool = False) -> OcrNote | None:
        """这一次要告诉玩家的：量出来了按读一次的快慢分三档；太慢、量不准、出错写照用默认线程数，tries 没用满时下次启动再试；
        中途停下的不告诉（之后再量）。查到的原因照带上。"""
        from ...vision import ocr_check

        if result.threads is not None:
            ms = int(result.ms.get(result.threads, 0))
            kind = "poor" if ms > POOR_MS else "fair" if ms > FAIR_MS else "good"
            return OcrNote(kind, threads=int(result.threads), ms=ms, retry=retest, reasons=tuple(shown))
        if result.reason in (ocr_check.SLOW, ocr_check.NOISY, ERROR):
            return OcrNote(result.reason, threads=ocr_check.DEFAULT_THREADS, retry=tries < MAX_TRIES,
                           reasons=tuple(shown))
        return None

    # ------------------------------------------------------------ 提示栏

    def notice(self) -> OcrNote | None:
        """提示栏这一轮写什么（界面线程，NoticeView.update 每轮现取）：正在量、量完的结果，或者没有。"""
        if self._running:
            return OcrNote("running", left=self._seconds_left())
        return self._outcome

    def _seconds_left(self) -> int:
        """还要多少秒：后台最近一次估的减去那以后过的时间，到点了是 0。估的数来回抖时显示的不来回跳（JITTER_S）。"""
        left, at = self._left
        now = max(0, math.ceil(left - (self.clock() - at)))
        if self._shown_left is None or now < self._shown_left or now > self._shown_left + JITTER_S:
            self._shown_left = now
        return self._shown_left

    def _tick(self) -> None:
        """界面线程的定时器：量的时候每 TICK_MS 刷新一次提示栏；量完（或中途停下）最后刷新一次，换成结果或者去掉，
        结果不留到玩家关掉的过 RESULT_MS 自己收起。等玩家回答的（查到原因）、等着重新量的（改好了）不自己消失。"""
        if self._stop.is_set():
            self._ticking = False
            return
        running = self._running  # 先取：刷新到一半才量完的，下一次再换成结果
        self.refresh()
        if running:
            self.later(TICK_MS, self._tick)
            return
        self._ticking = False
        note = self._outcome
        if note is not None and note.kind not in KEEP_KINDS and not note.reasons and note.kind != FIXED:
            self.later(RESULT_MS, self.dismiss)

    def dismiss(self) -> None:
        """收起结果那一条：玩家点了「关闭本条提示」，或者到了 RESULT_MS。"""
        if self._outcome is not None:
            self._outcome = None
            self.refresh()

    # ------------------------------------------------------------ 玩家点了「是」/「否」

    def accept(self) -> None:
        """界面线程（点「是」）：能替玩家改的就改，改好了过 RETEST_DELAY_MS 再量一次。没在量、没有要问的时什么都不做。"""
        note = self._outcome
        if self._running or note is None or not note.offer:
            return
        offer = note.offer
        if offer == "lowend":
            if self.set_lowend is not None:
                try:
                    self.set_lowend(True)
                except Exception:  # noqa: BLE001  打不开低配模式时照常提示
                    pass
            self._outcome = OcrNote(LOWEND_ON)
            self.later(RESULT_MS, self.dismiss)
            self.refresh()
            return
        ok = self._fix(offer)
        with self.lock:
            self.session.log_state("ocr_check_fix", fix=offer, ok=ok)
        if not ok:
            self._outcome = OcrNote(FIX_FAILED)
            self.later(RESULT_MS, self.dismiss)
            self.refresh()
            return
        self._outcome = OcrNote(FIXED)
        self._finished = False
        self._runs = 0
        self.refresh()
        self.later(RETEST_DELAY_MS, self._poll)

    def _fix(self, offer: str) -> bool:
        """替玩家改这一项（只改本进程自己的设置），返回改没改成。全部接住异常，出错算没改成。改好以后设置里的自检记录
        不动，再量一次时覆盖。"""
        from ... import sysinfo

        try:
            if offer == "big_cores":
                return bool(self.set_big_cores(True)) if self.set_big_cores is not None else False
            if offer == "eco":
                sysinfo.declare_high_performance()
                if sysinfo.priority_class() in ("idle", "below_normal"):
                    sysinfo.reset_priority()  # 任务管理器的效率模式两样一起改
                return sysinfo.performance_state() != "eco"
            if offer == "priority":
                sysinfo.reset_priority()
                return sysinfo.priority_class() == "normal"
            if offer == "affinity":
                sysinfo.reset_affinity()
                counts = sysinfo.affinity_counts()
                return bool(counts is not None and counts[0] == counts[1])
        except Exception:  # noqa: BLE001  改不上算没改成
            return False
        return False

    def decline(self) -> None:
        """界面线程（点「否」）：把这一项记进这台电脑的设置，不再问同一件事，然后收起提示。"""
        note = self._outcome
        if self._running or note is None or not note.offer:
            return
        offer = note.offer
        if offer not in DECLINABLE:
            self.dismiss()
            return
        cores = os.cpu_count() or 1
        with self.lock:
            settings = self.session.settings
            record = dict(settings.ocr_check or {})
            same = record.get("cores") == cores
            declined = record.get("declined") if same else None
            declined = [item for item in declined if isinstance(item, str)] if isinstance(declined, list) else []
            if offer not in declined:
                declined.append(offer)
            record["declined"] = declined
            settings.ocr_check = record
            self.session.save()
            self.session.log_state("ocr_check_decline", fix=offer)
        self.dismiss()

    # ------------------------------------------------------------ 退出

    def shutdown(self, timeout: float) -> bool:
        """退出时叫停（main() 经 Lanes.shutdown 调用）：设停止标记；正在量时等它最多 timeout 秒，返回停下了没有；
        没在量返回 True。"""
        self._stop.set()
        return bool(self._done.wait(max(0.0, float(timeout))))
