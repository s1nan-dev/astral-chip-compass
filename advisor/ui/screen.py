"""取帧入口：后台识别用的游戏画面从这里取。

两种截取方式（CaptureState.method）：
  - window 直接截取游戏窗口（advisor/vision/window_capture.py）：Windows 10 版本 2004（版本号 19041）起可用。
    截到的只有游戏窗口自己画的内容，游戏窗口不在前台、被其他窗口遮挡都照样截得到，画面里没有悬浮窗。
    这种方式从不设、从不改任何窗口的截图属性，也不截屏幕（黑屏核对时截游戏画面上的一小块除外）。
  - screen 截取屏幕：截屏幕上游戏窗口客户区那一块，用 grab_frame 截（见下面「贴补」）。
    游戏窗口和拾星助手自己的窗口都不在前台时，游戏画面可能被其他窗口遮挡，这时不截（抛 NoFrame），由悬浮窗暂停识别。

FrameSource（只在后台识别线程里用）按系统版本选方式，能直接截取就直接截取：
  - 会话在第一次取帧时才建，之后一直复用；游戏窗口换了（句柄变了）先关旧的再建新的；连续 IDLE_CLOSE_S 秒没有取帧
    就关掉（后台识别线程长时间等待前调 close_if_idle()），下次取帧时重建（约 0.2 秒）；
  - 出错按 CaptureError.kind 处理：unsupported（系统不支持）、denied（Windows 拒绝）立即改为截取屏幕；
    closed（窗口已关）当作找不到游戏窗口；device_lost（显卡驱动重置）关掉重建，DEVICE_LOST_WINDOW_S 秒内第
    DEVICE_LOST_LIMIT 次才改方式；其他错误（failed）重试一次，仍失败才改方式。出错关掉会话后 BACKOFF_S 秒内不重建，
    这期间抛 NoFrame；建好的会话连续 NO_FRAME_LIMIT_S 秒一帧画面都没有，也按出错处理；
  - 游戏窗口在前台、连续 BLANK_S 秒截到的都几乎全黑时，在游戏客户区里避开本进程窗口截屏幕上一小块核对：
    屏幕上那一块不黑才判为全黑（blank）并改方式；读条时屏幕上也黑，不会误判；
  - 改为截取屏幕后：「系统不支持」这次运行不再试；其他原因在游戏窗口换了（游戏重启）或调用 retry() 后再试一次，
    建会话成功并取到第一帧才改回直接截取。不定时重试；
  - 窗口暂时没有新画面（画面静止时系统不送新帧）时沿用这个会话最近截到的一帧；刚建会话还没有画面时抛 NoFrame。
state（CaptureState）记当前的方式和原因；后台识别线程每次取帧后和上一次比，变了就通知界面。
出错改为截取屏幕时，在错误记录（.tmp\\overlay_err.txt）里记一条（重建会话的出错只在改方式时记）；直接截取用的显卡设备
开不了多线程保护时也记一条（这次运行只记一次，照常截取）。

截取屏幕时的贴补（grab_frame）：悬浮窗平时可以被截图工具截到，也就会出现在截到的这块画面里。截图后：
  1. 照常截游戏窗口的客户区（悬浮窗在图里）；
  2. 找出本进程正在显示、与客户区相交的顶层窗口，没有就直接用这张图（不切换任何窗口的属性）。
     截图前后各查一次、合并两次的结果：两次查询之间被隐藏或移动的窗口，原位置和新位置都算；
  3. 有就让这几个窗口暂时不出现在截图里，只截相交的几小块（每块 10～20 毫秒），改回后把小块贴回原图；
  4. 贴补出错时退回：让这几个窗口暂时不出现在截图里，整幅重截。
悬浮窗被排除的时间只有截小块的那一下，外部截图恰好落在这段时间里的机会很小；录屏则不同，悬浮窗压在游戏画面上时，
每次识别都会让录屏缺一两帧悬浮窗（识别间隔 0.15～1 秒），看起来是周期性闪烁。
Windows 10 2004 之前的系统把「不出现在截图里」按 WDA_MONITOR 处理（窗口在截图里是黑块），贴回的小块也是黑块。
贴补在 AFFINITY_LOCK 里做：同一时刻只有一处在临时改窗口的截图属性（后台识别和导出诊断现截一幅不会互相改回）。
grab_frame 的 allow_capture 参数传 False 时整幅截图、不贴补（悬浮窗一直设成不出现在截图里时用不着贴补；程序里
不这样调用，只有测试用到）；FrameSource 截取屏幕时一律贴补。

三选一显示着时快速盯绿框（advisor/vision/pick_watch.py）另有一个取帧对象 BandSource，只截游戏客户区里筹码那一块，截取方式
跟着 FrameSource：直接截取时在盯绿框的线程里自己建一个会话（和后台识别的会话互不等待、互不抢帧），截取屏幕时截屏幕上的那一块，
同样贴补（grab_block；悬浮窗不在这一块上时不拿 AFFINITY_LOCK，后台识别截整幅时不用等它）。

不导入 Qt：只用 Win32 调用（ui/winapi.py）、截图模块（vision/capture.py）和窗口截取模块（vision/window_capture.py）。
截图模块和窗口截取模块在用到时才导入（截图模块取帧时会设定进程的 DPI 感知方式，不能早于界面库），
并按模块属性调用（capture.grab 等），测试可以打桩。
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from . import winapi

# 截图和切换窗口截图属性互斥：后台线程临时排除悬浮窗期间，别处改窗口的截图属性要等它改回之后再设
AFFINITY_LOCK = threading.Lock()
POLICY_WAIT = 1.0  # 界面线程最多等几秒（截一幅整图约 0.1 秒）；等不到时照常设置，不让界面卡住
_fallback_logged = False

IDLE_CLOSE_S = 30.0  # 连续这么久没有取帧就关掉直接截取的会话（释放约 50 MB；Windows 10 上黄框随之消失）
BACKOFF_S = 2.0  # 出错关掉会话后，这么久之内不重建
DEVICE_LOST_WINDOW_S = 10.0  # 显卡驱动重置：这么久之内
DEVICE_LOST_LIMIT = 3  # 第几次才改为截取屏幕
NO_FRAME_LIMIT_S = 10.0  # 会话开着、窗口没最小化，却这么久一帧画面都没有：按出错处理
WAIT_S = 0.05  # 取帧时最多等多久要一帧新画面
FIRST_WAIT_S = 0.5  # 刚建的会话第一次取帧最多等多久
BLANK_S = 10.0  # 游戏窗口在前台、连续这么久截到的都几乎全黑时核对一次
BLACK_LEVEL = 16  # 几乎全黑：各通道都不超过这个值（整幅按每 8 个像素取一个看）
SCREEN_LEVEL = 24  # 核对时屏幕上那一块的平均亮度超过这个值才算「不黑」
PROBE_SIZE = 64  # 核对时截屏幕上多大一块（边长，物理像素）
PROBE_SPOTS = ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))  # 依次试的位置（客户区的比例）
# 快速盯绿框（BandSource）直接截取时取出排队的旧帧以后最多再等多久要一帧新画面。帧池只有 2 格，排满以后系统不再送帧，排队里
# 最新的那一帧可能是上一次取完时紧接着的那一帧（每 0.1 秒查一次时落后几十毫秒）：等一下新画面，看到的就是这一刻的（60 帧时
# 约 17 毫秒内送来）。识别流程每帧开头要先等盯绿框的线程把正在查的那一次查完（先后不乱，见 vision/pick_watch.py），这一会儿
# 算在里面，所以不等太久：等不到时用排队里最新的那一帧，落后多少按它截到的时刻算（会话的 last_age_s）
BAND_WAIT_S = 0.02
# 快速盯绿框自己的会话出错时记进错误记录的一句（这次运行只记一次，照常识别）
BAND_ERROR = "三选一快速查看选中项时直接截取出错（拾星助手仍在运行，识别照常）"

NO_FRAME = "暂时没有游戏画面"
# 错误记录（.tmp\overlay_err.txt）：直接截取出错、改为截取屏幕时记一条
WINDOW_ERROR = "直接截取游戏窗口出错（步骤：{step}，错误代码 {code}），已改为截取屏幕（拾星助手仍在运行）"
WINDOW_ERROR_NO_CODE = "直接截取游戏窗口出错（步骤：{step}），已改为截取屏幕（拾星助手仍在运行）"
# 直接截取用的显卡设备开不了多线程保护（会话的 notes["multithread"] 不是 True）：照常截取，这次运行只记一条
UNPROTECTED = "直接截取用的显卡设备没能开启多线程保护，照常截取（拾星助手仍在运行）"
UNPROTECTED_DETAIL = "原因：{reason}（取不到多线程保护的接口，或者开启时出错）"
_unprotected_logged = False

METHODS = ("window", "screen")
PROBLEMS = (None, "unsupported", "denied", "failed", "blank")


class NoFrame(RuntimeError):
    """暂时没有画面（刚建会话、窗口大小刚变了、出错后等待重建、截取屏幕时游戏窗口不在前台）。

    后台识别线程不当错误显示，稍后再取。它是 RuntimeError 的子类：要和「找不到游戏窗口」「最小化」分开处理时先接住它。"""


@dataclass(frozen=True)
class CaptureState:
    """识别画面的截取方式。

    method：window 直接截取游戏窗口 / screen 截取屏幕。
    problem：截取屏幕的原因——None（直接截取时）/ unsupported 系统不支持（版本号低于 19041，或系统组件加载不了）/
      denied Windows 拒绝（例如游戏以管理员身份运行而拾星助手不是）/ failed 出错 / blank 直接截取的画面为全黑。
    detail：错误代码，例如 "0x80070005"；没有错误代码时（系统不支持、全黑等）为空。
    border_requested：直接截取时已请求不显示黄框（系统版本号 20348 起，且查得到对应接口）。
    build：Windows 版本号。
    step：出错的那一步（接口名，例如 "CreateForWindow"）；没有时为空。"""

    method: str
    problem: str | None = None
    detail: str = ""
    border_requested: bool = False
    build: int = 0
    step: str = ""


_last_state: CaptureState | None = None


def last_state() -> CaptureState | None:
    """最近一个取帧对象的截取方式（导出诊断时写进 version.txt）；还没建过取帧对象时为 None。哪个线程都可以调用。"""
    return _last_state


def initial_state(support) -> CaptureState:
    """还没取过帧时的截取方式：按系统版本预判（support 是 window_capture.Support）。"""
    if support.window:
        return CaptureState("window", None, "", bool(support.borderless), int(support.build))
    return CaptureState("screen", "unsupported", "", False, int(support.build))


def expected_method(support=None) -> str:
    """界面线程启动时预判截取方式："window" 或 "screen"。support 不给时按本机的系统版本。"""
    if support is None:
        from ..vision.window_capture import system_support

        support = system_support()
    return "window" if support.window else "screen"


# ---------------------------------------------------------------- 取帧对象


class _Tally:
    """一段时间里的取帧统计。"""

    KEYS = ("window", "stale", "screen", "no_frame", "empty", "errors")

    def __init__(self, keep: int | None = None):
        self.counts = dict.fromkeys(self.KEYS, 0)
        self.ms: deque[float] = deque(maxlen=keep)
        self.ages: deque[float] = deque(maxlen=keep)

    def summary(self) -> dict:
        """window 直接截取的新画面、stale 沿用的上一帧、screen 截取屏幕、no_frame 暂时没有画面、empty 直接截取时
        没有新帧的次数、errors 直接截取出错的次数；grabs 交出画面的次数；ms_* 每次取帧的耗时、age_ms_p95 画面落后（毫秒）。"""
        out = dict(self.counts)
        out["grabs"] = out["window"] + out["stale"] + out["screen"]
        out["ms_p50"] = _percentile(self.ms, 50)
        out["ms_p95"] = _percentile(self.ms, 95)
        out["age_ms_p95"] = _percentile(self.ages, 95)
        return out


def _percentile(values, q: float) -> float | None:
    values = list(values)
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values, dtype=float), q)), 2)


class FrameSource:
    """后台识别的取帧对象（见模块说明）。只在后台识别线程里调用 grab()、close_if_idle()、close()；
    retry() 和读 state、stats、snapshot() 哪个线程都可以。

    support：window_capture.Support，不给时按本机的系统版本；backend：直接截取的会话类（window_capture.WindowCapture，
    测试换成假的）；finder：查游戏窗口的函数，返回 capture.GameWindow，找不到时抛 capture.WindowGone（不给时用
    capture.find_game）；screen：截取屏幕的函数（不给时用 grab_frame，贴补悬浮窗遮挡的区域）；clock：秒数时钟。
    grab() 找不到游戏窗口、游戏窗口最小化时抛 capture.WindowGone（后台识别线程只把这一种的两句固定中文原样显示）。"""

    def __init__(self, support=None, backend=None, finder: Callable | None = None, screen: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if support is None or backend is None:
            from ..vision import window_capture

            support = window_capture.system_support() if support is None else support
            backend = window_capture.WindowCapture if backend is None else backend
        self.support = support
        self._backend = backend
        self._finder = finder
        self._screen = screen if screen is not None else grab_frame
        self._clock = clock
        self._session = None  # 直接截取的会话（backend 的实例）
        self._session_hwnd: int | None = None
        self._session_fresh = False  # 刚建的会话还没取到过画面
        self._sessions = 0  # 建过几次会话
        self._last_grab: float | None = None  # 最近一次取帧的时刻（clock）
        self._closed_at: float | None = None  # 出错关掉会话的时刻
        self._fails = 0  # 连续出错（不含显卡驱动重置）的次数
        self._lost: deque[float] = deque()  # 最近几次显卡驱动重置的时刻
        self._black_since: float | None = None  # 游戏在前台、截到的画面开始几乎全黑的时刻
        self._no_frame_since: float | None = None  # 会话开着却一直没有画面，从什么时候开始
        self._retry_hwnd: int | None = None  # 改为截取屏幕时的游戏窗口：换了窗口再试一次直接截取
        self._retry_requested = False
        self._image: np.ndarray | None = None  # 这个会话最近截到的一帧（窗口暂时没有新画面时沿用）
        self._image_key: tuple | None = None  # 它对应的（窗口, 客户区大小）
        self._image_age = 0.0  # 它截到时画面落后多少秒
        self._image_at = 0.0  # 它是什么时候截到的（time.perf_counter）
        self._lock = threading.Lock()  # 统计（界面线程导出诊断时读）
        self._total = _Tally(keep=600)
        self._period = _Tally()
        self.state = initial_state(support)
        self._remember()

    # ------------------------------------------------------------ 对外

    def grab(self) -> np.ndarray:
        """取一帧游戏窗口客户区的 BGR 图。约定同 grab_frame：找不到游戏窗口、窗口最小化时抛 capture.WindowGone；
        暂时没有画面时抛 NoFrame（两种都是 RuntimeError 的子类）。截图模块的其他错误照常抛出。"""
        now = self._clock()
        self._last_grab = now
        start = time.perf_counter()
        try:
            img, source, age = self._grab(now)
        except NoFrame:
            self._note(start, "no_frame")
            raise
        self._note(start, source, age)
        return img

    def retry(self) -> None:
        """游戏窗口换了（游戏重启）：截取屏幕时，下次取帧再试一次直接截取（系统不支持时不试）。"""
        self._retry_requested = True

    def close_if_idle(self, now: float | None = None) -> float | None:
        """连续 IDLE_CLOSE_S 秒没有取帧就关掉会话。返回还要多久再检查（秒）；没有开着的会话时为 None。

        后台识别线程每次要长时间等待（暂停、手动模式平时）之前调用，按返回值设等待的上限。"""
        if self._session is None:
            return None
        now = self._clock() if now is None else now
        idle = now - (self._last_grab if self._last_grab is not None else now)
        if idle >= IDLE_CLOSE_S:
            self._drop_session()
            return None
        return IDLE_CLOSE_S - idle

    def close(self) -> None:
        """关掉直接截取的会话（后台识别线程退出前调用）。之后再取帧会重建。"""
        self._drop_session()

    @property
    def session_open(self) -> bool:
        return self._session is not None

    @property
    def stats(self) -> dict:
        """从建好到现在的取帧统计（_Tally.summary 的各项，另有 sessions：建过几次会话）。"""
        with self._lock:
            out = self._total.summary()
        out["sessions"] = self._sessions
        return out

    def take_period(self) -> dict:
        """上次调用以来的取帧统计（识别记录每 60 秒一条用），并重新开始计。另有 method：当前的截取方式。"""
        with self._lock:
            out = self._period.summary()
            self._period = _Tally()
        out["method"] = self.state.method
        return out

    def snapshot(self) -> dict:
        """诊断包对局状态里的截取字段：方式、原因、错误代码和步骤、系统版本号、是否请求了不显示黄框、取帧统计、
        当前会话的统计和两项请求的结果。哪个线程都可以调用。"""
        state = self.state
        out = {
            "method": state.method, "problem": state.problem, "detail": state.detail, "step": state.step,
            "border_requested": state.border_requested, "build": state.build,
            "support": {"window": bool(self.support.window), "borderless": bool(self.support.borderless)},
            "session_open": self._session is not None, "stats": self.stats,
        }
        session = self._session
        if session is not None:
            try:
                out["session"] = {"stats": dict(getattr(session, "stats", {}) or {}),
                                  "notes": dict(getattr(session, "notes", {}) or {})}
            except Exception:  # noqa: BLE001  会话正好在别的线程里关掉：不带这一项
                pass
        return out

    # ------------------------------------------------------------ 取帧

    def _grab(self, now: float) -> tuple[np.ndarray, str, float | None]:
        try:
            game = self._find()
        except RuntimeError:
            self._drop_session()  # 游戏关了：关掉会话
            raise
        if game.minimized:
            self._no_frame_since = None
            self._black_since = None
            raise _window_gone(minimized=True)  # 会话保留，空闲满 IDLE_CLOSE_S 秒才关
        if self.state.method == "screen":
            if self._can_retry(game):
                got = self._retry(game, now)
                if got is not None:
                    return got, "window", self._image_age
            return self._grab_screen(game), "screen", None
        return self._grab_window(game, now)

    def _find(self):
        if self._finder is not None:
            return self._finder()
        from ..vision import capture

        return capture.find_game()

    def _grab_window(self, game, now: float):
        if self._session is not None and self._session_hwnd != game.hwnd:
            self._drop_session()  # 游戏窗口换了：先关旧的
        if self._session is None:
            if self._closed_at is not None and now - self._closed_at < BACKOFF_S:
                raise NoFrame(NO_FRAME)
            try:
                self._open(game.hwnd)
            except Exception as e:  # noqa: BLE001  任何出错都按直接截取出错处理，不让识别停下
                return self._window_error(e, game, now)
        size = (int(game.client[2]), int(game.client[3]))
        try:
            img = self._session.grab(game.offset, size, FIRST_WAIT_S if self._session_fresh else WAIT_S)
        except Exception as e:  # noqa: BLE001
            return self._window_error(e, game, now)
        key = (game.hwnd, size)
        if img is not None and self._client_changed(game.hwnd, size):
            img = None  # 等新画面的几十毫秒里窗口大小变了：这一帧按旧大小裁，会裁错，丢掉
        if img is None:
            self._count("empty")
            if self._image is None or self._image_key != key:
                return self._no_frame(game, now)
            img, source = self._image, "stale"  # 窗口暂时没有新画面：沿用这个会话最近截到的一帧
            age = self._image_age + (time.perf_counter() - self._image_at)
        else:
            source = "window"
            age = float(getattr(self._session, "last_age_s", None) or 0.0)
            self._image, self._image_key, self._image_age, self._image_at = img, key, age, time.perf_counter()
            self._session_fresh = False
        self._fails = 0
        self._closed_at = None
        self._no_frame_since = None
        self._set_state(CaptureState("window", None, "", bool(getattr(self._session, "border_requested", False)),
                                     int(self.support.build)))
        if self._is_blank(img, game, now):
            self._fallback("blank", None, game)
            return self._grab_screen(game), "screen", None
        return img, source, age

    def _open(self, hwnd: int) -> None:
        self._session = self._backend(hwnd, borderless=bool(self.support.borderless))
        _log_unprotected(self._session)
        self._session_hwnd = hwnd
        self._session_fresh = True
        self._image = self._image_key = None
        self._no_frame_since = None  # 「一直没有画面」和「全黑」都从这个会话重新计
        self._black_since = None
        self._sessions += 1

    def _client_changed(self, hwnd: int, size: tuple[int, int]) -> bool:
        """取到帧后再查一次窗口：还是同一个窗口、客户区大小却变了时返回 True，这一帧丢掉。

        直接截取时，等一帧新画面要几十毫秒；这期间用户把游戏窗口拉大，截到的画面里客户区的位置和大小就变了，
        还按等帧之前量的大小和偏移裁客户区，会裁出「新窗口左上角、旧窗口大小」的一块。只影响这一帧，
        但读数已经错了：丢掉这一帧，下一帧按新的大小重来。
        窗口没了、换了、最小化了，或者这一次查不了时不丢（返回 False）：这一帧照常交出，下一次取帧照旧按找不到窗口、
        最小化处理，和不查这一次时一样。
        """
        try:
            game = self._find()
        except Exception:  # noqa: BLE001  见上：查不了时照常交出这一帧
            return False
        if game.hwnd != hwnd or game.minimized or game.client is None:
            return False
        return (int(game.client[2]), int(game.client[3])) != size

    def _no_frame(self, game, now: float):
        """会话开着却没有画面：先抛 NoFrame；连续 NO_FRAME_LIMIT_S 秒都这样就按出错处理。"""
        if self._no_frame_since is None:
            self._no_frame_since = now
        elif now - self._no_frame_since >= NO_FRAME_LIMIT_S:
            self._no_frame_since = None
            from ..vision.window_capture import CaptureError

            return self._window_error(CaptureError("failed", None, "TryGetNextFrame"), game, now)
        raise NoFrame(NO_FRAME)

    def _window_error(self, error: Exception, game, now: float):
        """直接截取出错：按种类重建、等待或改为截取屏幕（见模块说明）。改方式时返回这一次截取屏幕的结果。"""
        err = _capture_error(error)
        self._count("errors")
        kind = err.kind
        if kind == "closed":
            self._drop_session()
            if not self._still_there(game.hwnd):
                raise _window_gone() from error
            kind = "failed"  # 窗口还在：按出错处理
        if kind in ("unsupported", "denied"):
            self._fallback(kind, err, game)
            return self._grab_screen(game), "screen", None
        self._drop_session(error_at=now)
        if kind == "device_lost":
            self._lost.append(now)
            while self._lost and now - self._lost[0] > DEVICE_LOST_WINDOW_S:
                self._lost.popleft()
            if len(self._lost) >= DEVICE_LOST_LIMIT:
                self._fallback("failed", err, game)
                return self._grab_screen(game), "screen", None
            raise NoFrame(NO_FRAME)
        self._fails += 1
        if self._fails >= 2:
            self._fallback("failed", err, game)
            return self._grab_screen(game), "screen", None
        raise NoFrame(NO_FRAME)

    def _still_there(self, hwnd: int) -> bool:
        try:
            return self._find().hwnd == hwnd
        except RuntimeError:
            return False

    def _fallback(self, problem: str, err, game) -> None:
        """改为截取屏幕，记下原因；出错（failed）时在错误记录里记一条。"""
        self._drop_session()
        self._fails = 0
        self._lost.clear()
        self._black_since = None
        self._no_frame_since = None
        self._retry_hwnd = game.hwnd
        self._retry_requested = False
        self._set_state(CaptureState("screen", problem, err.code if err is not None else "", False,
                                     int(self.support.build), err.step if err is not None else ""))
        if problem == "failed" and err is not None:
            _log_window_error(err)

    def _can_retry(self, game) -> bool:
        if not self.support.window or self.state.problem in (None, "unsupported"):
            return False
        return self._retry_requested or (self._retry_hwnd is not None and game.hwnd != self._retry_hwnd)

    def _retry(self, game, now: float) -> np.ndarray | None:
        """截取屏幕时再试一次直接截取：建会话并取到第一帧才改回直接截取；不成功时照旧截取屏幕，原因不变。"""
        self._retry_requested = False
        self._retry_hwnd = game.hwnd  # 每个游戏窗口只再试一次
        size = (int(game.client[2]), int(game.client[3]))
        try:
            self._open(game.hwnd)
            img = self._session.grab(game.offset, size, FIRST_WAIT_S)
        except Exception:  # noqa: BLE001  再试不成功：不提示，照旧截取屏幕
            self._count("errors")
            self._drop_session()
            return None
        if img is None:
            self._count("empty")
            self._drop_session()
            return None
        self._session_fresh = False
        self._image, self._image_key = img, (game.hwnd, size)
        self._image_age, self._image_at = float(getattr(self._session, "last_age_s", None) or 0.0), time.perf_counter()
        self._fails = 0
        self._closed_at = None
        self._set_state(CaptureState("window", None, "", bool(getattr(self._session, "border_requested", False)),
                                     int(self.support.build)))
        return img

    def _grab_screen(self, game) -> np.ndarray:
        """截取屏幕。游戏窗口和拾星助手自己的窗口都不在前台时不截：游戏画面可能被其他窗口遮挡。"""
        if not (game.foreground or game.own_foreground):
            raise NoFrame(NO_FRAME)
        return self._screen()

    # ------------------------------------------------------------ 全黑核对

    def _is_blank(self, img: np.ndarray, game, now: float) -> bool:
        """游戏窗口在前台、连续 BLANK_S 秒截到的都几乎全黑，而屏幕上同一块不黑：直接截取的画面是全黑。"""
        if not game.foreground or not _near_black(img):
            self._black_since = None
            return False
        if self._black_since is None:
            self._black_since = now
            return False
        if now - self._black_since < BLANK_S:
            return False
        self._black_since = now  # 不论结果，再满 BLANK_S 秒才核对下一次
        return self._screen_differs(img, game)

    def _screen_differs(self, img: np.ndarray, game) -> bool:
        from ..vision import capture

        block = self._probe_block(game)
        if block is None:
            return False
        x, y, w, h = block
        left, top = game.client[0], game.client[1]
        mine = img[y - top:y - top + h, x - left:x - left + w]
        if mine.size == 0 or int(mine.max()) > BLACK_LEVEL:
            return False
        shot = capture.grab_region(x, y, w, h)
        return shot is not None and getattr(shot, "size", 0) > 0 and float(np.mean(shot)) > SCREEN_LEVEL

    def _probe_block(self, game) -> tuple[int, int, int, int] | None:
        """核对用的一小块（屏幕上的物理像素）：在游戏客户区里，不和本进程的窗口相交，屏幕上看到的确实是游戏窗口。"""
        from ..vision import capture

        left, top, width, height = game.client
        size = min(PROBE_SIZE, width, height)
        if size <= 0:
            return None
        own = winapi.own_windows()
        for fx, fy in PROBE_SPOTS:
            x = left + min(max(int(width * fx) - size // 2, 0), width - size)
            y = top + min(max(int(height * fy) - size // 2, 0), height - size)
            block = (x, y, size, size)
            if covered_blocks(own, block):
                continue
            corners = ((x, y), (x + size - 1, y), (x, y + size - 1), (x + size - 1, y + size - 1),
                       (x + size // 2, y + size // 2))
            if capture.visible_at(game.hwnd, corners):
                return block
        return None

    # ------------------------------------------------------------ 内部

    def _drop_session(self, error_at: float | None = None) -> None:
        session, self._session = self._session, None
        self._session_hwnd = None
        self._session_fresh = False
        self._image = self._image_key = None
        if error_at is not None:
            self._closed_at = error_at
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001  关不掉也不影响之后重建
                pass

    def _set_state(self, state: CaptureState) -> None:
        if state != self.state:
            self.state = state
        self._remember()

    def _remember(self) -> None:
        global _last_state
        _last_state = self.state

    def _count(self, key: str) -> None:
        with self._lock:
            self._total.counts[key] += 1
            self._period.counts[key] += 1

    def _note(self, start: float, source: str, age: float | None = None) -> None:
        ms = (time.perf_counter() - start) * 1000
        with self._lock:
            for tally in (self._total, self._period):
                tally.counts[source] += 1
                if source != "no_frame":
                    tally.ms.append(ms)
                if age is not None:
                    tally.ages.append(age * 1000)


class BandSource:
    """快速盯绿框的取帧对象（advisor/vision/pick_watch.py 的 PickWatcher 用）：只截游戏客户区里的一块。只在盯绿框的那个
    线程里建、取帧（grab）、关（close）：直接截取的会话只能在建它的线程里用。

    截取方式跟着后台识别的取帧对象（main：FrameSource；读它的 state.method、support，哪个线程读都可以）：
      window 直接截取：自己建一个会话（backend，和后台识别的会话互不影响），第一次取帧时建，游戏窗口换了先关旧的再建；
        取出排队的旧帧以后最多再等 wait_s 要一帧新画面，只把那一块拷回、转成 BGR；一帧都没有（画面没变、刚建会话）时返回 None。
        出错时关掉会话，BACKOFF_S 秒内不重建、不截，
        这次运行第一次出错时记一条错误记录（log 给了时：log(标题, 调用栈的文字)）；
      screen 截取屏幕：截屏幕上的那一块，贴补拾星助手自己的窗口遮挡的部分（block，默认 grab_block）；游戏窗口和拾星助手自己的
        窗口都不在前台时不截（游戏画面可能被别的窗口盖住）。
    找不到游戏窗口、窗口最小化、客户区大小不是识别流程算位置时的大小、暂时没有画面、main 还没有截取方式（测试里的假取帧对象）
    时都返回 None，不是出错。age：最近截到的那一块画面落后多少秒（直接截取时取会话的 last_age_s，截取屏幕时为 0）。
    finder：查游戏窗口的函数（不给时用 capture.find_game）；clock：秒数时钟。"""

    def __init__(self, main, backend=None, finder: Callable | None = None, block: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic, wait_s: float = BAND_WAIT_S, log: Callable | None = None):
        if backend is None:
            from ..vision import window_capture

            backend = window_capture.WindowCapture
        self.main = main
        self._backend = backend
        self._finder = finder
        self._block = block if block is not None else grab_block
        self._clock = clock
        self.wait_s = wait_s
        self._log = log
        self._session = None
        self._session_hwnd: int | None = None
        self._closed_at: float | None = None  # 出错关掉会话的时刻
        self._logged = False
        self.age = 0.0

    def grab(self, region: tuple[int, int, int, int], client: tuple[int, int]) -> np.ndarray | None:
        """截游戏客户区里 region =（左, 上, 宽, 高）那一块，返回 BGR 图；客户区大小不是 client =（宽, 高）时等情况返回 None
        （见类的说明）。"""
        method = getattr(getattr(self.main, "state", None), "method", None)
        if method not in METHODS:
            return None
        try:
            game = self._find()
        except RuntimeError:  # 找不到游戏窗口（capture.WindowGone 也是 RuntimeError）
            self._drop()
            return None
        if game.minimized or game.client is None:
            return None
        size = (int(game.client[2]), int(game.client[3]))
        if size != (int(client[0]), int(client[1])):
            return None
        if method == "screen":
            self._drop()
            if not (game.foreground or game.own_foreground):
                return None
            x, y, w, h = (int(v) for v in region)
            self.age = 0.0
            return self._block((int(game.client[0]) + x, int(game.client[1]) + y, w, h))
        return self._grab_window(game, region, size)

    def close(self) -> None:
        """关掉直接截取的会话，做完这个线程的窗口截取收尾（线程结束、空闲很久时由盯绿框的线程调用）。之后再取帧会重建。"""
        self._drop()
        try:
            from ..vision import window_capture

            window_capture.uninit_thread()
        except Exception:  # noqa: BLE001  收尾出错不影响之后重建
            pass

    def _find(self):
        if self._finder is not None:
            return self._finder()
        from ..vision import capture

        return capture.find_game()

    def _grab_window(self, game, region, size) -> np.ndarray | None:
        if self._session is not None and self._session_hwnd != game.hwnd:
            self._drop()  # 游戏窗口换了：先关旧的
        if self._session is None:
            if self._closed_at is not None and self._clock() - self._closed_at < BACKOFF_S:
                return None
            try:
                borderless = bool(getattr(getattr(self.main, "support", None), "borderless", False))
                self._session = self._backend(game.hwnd, borderless=borderless)
                self._session_hwnd = game.hwnd
            except Exception:  # noqa: BLE001  建不了：过一会儿再试，不影响后台识别
                self._failed()
                return None
        try:
            img = self._session.grab(game.offset, size, self.wait_s, region=tuple(int(v) for v in region))
        except Exception:  # noqa: BLE001  同上
            self._failed()
            return None
        if img is not None:
            self.age = float(getattr(self._session, "last_age_s", None) or 0.0)
        return img

    def _failed(self) -> None:
        """（在 except 里调用）直接截取出错：关掉会话，BACKOFF_S 秒内不重建；这次运行第一次出错时记一条错误记录。"""
        self._drop()
        self._closed_at = self._clock()
        if self._logged or self._log is None:
            return
        self._logged = True
        try:
            self._log(BAND_ERROR, traceback.format_exc())
        except Exception:  # noqa: BLE001  记不进就算了
            pass

    def _drop(self) -> None:
        session, self._session = self._session, None
        self._session_hwnd = None
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001  关不掉也不影响之后重建
                pass


def _near_black(img: np.ndarray) -> bool:
    """画面几乎全黑（每 8 个像素取一个，各通道都不超过 BLACK_LEVEL）。

    先看每 64 个像素取一个的点：它们都在「每 8 个取一个」里，有一个超过就不是全黑，结果和整套看完全相同。
    平常的游戏画面这一步就判完（4K 约 0.05 毫秒；整套每 8 个取一个约 0.5 毫秒，游戏在前台时每次取帧都要判）。"""
    if img is None or not img.size:
        return False
    if int(img[::64, ::64].max()) > BLACK_LEVEL:
        return False
    return int(img[::8, ::8].max()) <= BLACK_LEVEL


def _capture_error(error: Exception):
    """意外的错误（不是 CaptureError）也按「出错」处理：步骤写错误的类名。"""
    from ..vision.window_capture import CaptureError

    if isinstance(error, CaptureError):
        return error
    wrapped = CaptureError("failed", None, type(error).__name__)
    wrapped.__cause__ = error
    return wrapped


def _log_window_error(err) -> None:
    """直接截取出错、改为截取屏幕：错误记录里记一条（错误代码、出错的步骤和出错的经过）。"""
    code = getattr(err, "code", "")
    title = (WINDOW_ERROR if code else WINDOW_ERROR_NO_CODE).format(step=getattr(err, "step", "") or "?", code=code)
    cause = err.__cause__ if err.__cause__ is not None else err
    _record_error(title, "".join(traceback.format_exception(cause)))


def _record_error(title: str, detail: str) -> None:
    from . import log_error

    log_error(title, detail, append=True)


def _log_unprotected(session) -> None:
    """直接截取用的显卡设备开不了多线程保护（会话的 notes["multithread"] 是原因而不是 True，见 vision/window_capture.py 的
    _Device._protect）：错误记录里记一条（UNPROTECTED），这次运行只记一次（游戏重启、出错重建都会再建会话）；不弹提示，
    照常截取。会话没有这一项（假的会话等）时不记。"""
    global _unprotected_logged
    if _unprotected_logged:
        return
    try:
        reason = (getattr(session, "notes", None) or {}).get("multithread", True)
    except Exception:  # noqa: BLE001  取不到就不记，不影响截取
        return
    if reason is True:
        return
    _unprotected_logged = True
    _record_error(UNPROTECTED, UNPROTECTED_DETAIL.format(reason=reason))


def _window_gone(minimized: bool = False):
    """找不到游戏窗口、游戏窗口最小化时抛的异常（vision/capture.py 的 WindowGone：给玩家看的两句固定中文）。"""
    from ..vision import capture

    return capture.WindowGone(minimized=minimized)


# ---------------------------------------------------------------- 截取屏幕和贴补


@contextmanager
def policy_lock():
    """界面线程设置窗口的截图属性时用：等后台截图告一段落再设。最多等 POLICY_WAIT 秒。"""
    got = AFFINITY_LOCK.acquire(timeout=POLICY_WAIT)
    try:
        yield got
    finally:
        if got:
            AFFINITY_LOCK.release()


def covered_blocks(windows, client) -> list[tuple[int, tuple[int, int, int, int]]]:
    """各窗口和游戏客户区的交集：[(hwnd, (左, 上, 宽, 高))]，物理像素；不相交的窗口不列。

    windows 是 winapi.own_windows() 的结果 [(hwnd, (左, 上, 右, 下))]；client 是客户区 (左, 上, 宽, 高)。
    """
    cl, ct, cw, ch = client
    cr, cb = cl + cw, ct + ch
    blocks = []
    for hwnd, (left, top, right, bottom) in windows:
        x0, y0, x1, y1 = max(left, cl), max(top, ct), min(right, cr), min(bottom, cb)
        if x1 > x0 and y1 > y0:
            blocks.append((hwnd, (x0, y0, x1 - x0, y1 - y0)))
    return blocks


def grab_frame(allow_capture: bool | Callable[[], bool] = True) -> np.ndarray:
    """截取屏幕：截游戏窗口客户区的 BGR 图，图里没有拾星助手自己的窗口。找不到游戏窗口、窗口最小化时抛出 capture.WindowGone。

    默认（截取屏幕时一律这样截）按模块说明贴补悬浮窗遮挡的区域。allow_capture 可以是值，
    也可以是返回该值的函数（拿到 AFFINITY_LOCK 之后才调用）；为 False 时直接整幅截图、不贴补。
    """
    from ..vision import capture

    with AFFINITY_LOCK:
        allow = allow_capture() if callable(allow_capture) else allow_capture
        if not allow:
            return capture.grab()
        before = winapi.own_windows()
        img, rect = capture.grab_client()
        return _patch_own(img, rect, before, capture.grab)


def grab_block(rect: tuple[int, int, int, int]) -> np.ndarray:
    """截取屏幕上的一块（物理像素的 (左, 上, 宽, 高)），返回 BGR 图，图里没有拾星助手自己的窗口：贴补同 grab_frame
    （截图前后各查一次本进程的窗口，压在这一块上的那几个暂时不出现在截图里、重截相交的小块贴回；出错时排除它们后整块重截）。
    快速盯绿框截取屏幕时用（BandSource）。悬浮窗不在这一块上时（三选一期间悬浮窗挪到挡得最少的地方，平常就是这样）不拿
    AFFINITY_LOCK：只截、不改任何窗口的截图属性，后台识别截整幅时不用等它；要贴补时才拿锁，在锁里照 grab_frame 的做法补
    （截图前后查到的窗口都算上）。"""
    from ..vision import capture

    rect = tuple(int(v) for v in rect)
    before = winapi.own_windows()
    img = capture.grab_region(*rect)
    seen = before + [w for w in winapi.own_windows() if w not in before]
    if not covered_blocks(seen, rect):
        return img
    with AFFINITY_LOCK:
        return _patch_own(img, rect, seen, lambda: capture.grab_region(*rect))


def _patch_own(img: np.ndarray, rect, before, regrab: Callable[[], np.ndarray]) -> np.ndarray:
    """（持着 AFFINITY_LOCK 时调用）img 是刚截好的屏幕上 rect =（左, 上, 宽, 高）那一块，before 是截图前查到的本进程窗口：
    再查一次、合并两次的结果（两次之间被隐藏或移动的窗口，原位置和新位置都算），和 rect 相交的那几个窗口暂时不出现在截图里，
    只重截相交的几小块贴回 img。没有相交的窗口时原样返回，不切换任何窗口的属性。贴补出错时排除这几个窗口后整块重截（regrab）。"""
    from ..vision import capture

    windows = before + [w for w in winapi.own_windows() if w not in before]
    blocks = covered_blocks(windows, rect)
    if not blocks:
        return img  # 悬浮窗不在这一块上：不切换，外部截图和录屏不受影响
    hwnds = list(dict.fromkeys(hwnd for hwnd, _ in blocks))
    try:
        with winapi.excluded(hwnds):
            patches = [(block, capture.grab_region(*block)) for _, block in blocks]
        left, top = rect[0], rect[1]
        for (x, y, w, h), patch in patches:
            img[y - top:y - top + h, x - left:x - left + w] = patch
        return img
    except Exception:  # noqa: BLE001  贴补出错：排除这几个窗口后整块重截
        _log_fallback()
        with winapi.excluded(hwnds):
            return regrab()


def _log_fallback() -> None:
    """贴补出错退回整幅重截：只记第一次（每秒截好几次，出错时不刷满错误记录）。"""
    global _fallback_logged
    if _fallback_logged:
        return
    _fallback_logged = True
    from . import log_error

    log_error("截图时贴补悬浮窗遮挡的区域出错，已改为排除悬浮窗后整幅重截（拾星助手仍在运行）",
              traceback.format_exc(), append=True)
