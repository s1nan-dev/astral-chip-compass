"""悬浮窗用到的几个 Win32 调用：找游戏窗口、看谁在前台、悬浮窗有没有被别的窗口盖住、窗口层次（置顶、退到某个窗口
后面、放到最上层）、截取屏幕时临时让窗口不出现在截图里；自己画外框的窗口（chrome.py）取窗口矩形、设系统圆角；
查本进程是不是按每块屏幕的缩放运行（dpi_awareness）；用户点「打开文件夹」时在资源管理器里打开（open_folder）。

只查询窗口状态和设置自己窗口的属性，不向游戏发送任何输入，不激活、不移动任何窗口，不改游戏和别的程序的窗口。
查别的窗口只看位置、大小和样式，不读标题、不截画面（类名只写进本机的识别记录；游戏窗口记的标题就是找它时对上的那一个，
不另读，也只写进本机的识别记录）。
用单独的 WinDLL 实例声明参数类型，不影响其他模块里的 ctypes.windll.user32。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import traceback
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, replace

from ..errors import log_error
# 声明在 advisor/win32.py（截屏、找窗口、热键、窗口层次共用），这里按同样的名字取用；DWMWCP_* 等常量也从这里再导出。
from ..win32 import (DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DEFAULT,
                     DWMWCP_DONOTROUND, DWMWCP_ROUND, GA_ROOT, GW_HWNDNEXT, GWL_EXSTYLE, HWND_NOTOPMOST, HWND_TOP,
                     HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, WDA_EXCLUDEFROMCAPTURE, WDA_NONE,
                     WNDENUMPROC, WS_EX_TOPMOST, WS_EX_TRANSPARENT)
from ..win32 import dwmapi as _dwmapi
from ..win32 import get_window_long as _get_window_long
from ..win32 import kernel32 as _kernel32
from ..win32 import user32 as _user32

# 游戏窗口的标题：Steam 版的两个（国服、国际服）在前、TapTap 版在后，与 advisor/vision/capture.py 的 WINDOW_TITLES 相同
# （测试核对）。这里照旧单写一份、不导入 capture：界面模块对识别包都是用到时才导入，导入界面时不连带加载识别库
GAME_TITLES = ("AstralParty_CN", "AstralParty_INT", "吉星派对")
COVER_MIN = 8  # 别的窗口和悬浮窗相交至少这么多像素（宽、高都要够）才算盖住
MAX_WINDOWS = 4096  # 按层次往下数顶层窗口时最多数这么多个（数的过程中层次在变时不至于一直数下去）


@dataclass(frozen=True)
class GameProbe:
    """一次查询的结果。rect 是游戏客户区在屏幕上的（左, 上, 宽, 高），物理像素；找不到或最小化时为 None。

    hwnd 是游戏窗口的句柄（找不到时为 None）；covered：别的程序的窗口盖住了悬浮窗（只在查询时给了悬浮窗、游戏和
    拾星助手自己的窗口都不在前台时才查）；cover_below：悬浮窗退后时插在哪个窗口下面（盖住它的窗口里最下面的那个）；
    cover_class、game_class：盖住悬浮窗的窗口、游戏窗口的类名；game_title：这一次找到游戏窗口时对上的标题（capture 的
    这一份找法交出来的，不读模块里上一次找到的那个，两路识别各拿各的；不另读窗口的标题；三个版本的类名一样，只有标题
    分得出是哪个客户端）。这三项只写进本机的识别记录。notice：找不到游戏窗口时给玩家看的那一句（capture.missing_notice：
    设了游戏平台、开着的是另一个端时点明是设置的原因，指定了端时写明没找到哪个端，其余是 NOT_FOUND）；找到时为空。"""

    found: bool
    rect: tuple[int, int, int, int] | None
    minimized: bool
    game_foreground: bool  # 游戏窗口在前台
    own_foreground: bool  # 拾星助手自己的窗口（悬浮窗、「关于」窗口、下拉列表等）在前台
    hwnd: int | None = None
    covered: bool = False
    cover_below: int | None = None
    cover_class: str = ""
    game_class: str = ""
    game_title: str = ""
    notice: str = ""


@dataclass(frozen=True)
class TopWindow:
    """游戏窗口上面的一个顶层窗口。rect 是系统合成的可见边框（左, 上, 右, 下），物理像素（普通窗口四周约 7 像素
    看不见的拖拽边不算在内）。"""

    hwnd: int
    rect: tuple[int, int, int, int]
    own: bool = False  # 拾星助手自己的窗口
    visible: bool = True
    minimized: bool = False
    cloaked: bool = False  # 被系统隐藏（在别的虚拟桌面、收起的开始菜单等）
    transparent: bool = False  # 鼠标可以穿透（显卡、语音软件的全屏覆盖层多是这种）
    topmost: bool = False


def _client_rect(hwnd) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return origin.x, origin.y, width, height


def _pid(hwnd) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def frame_rect(hwnd) -> tuple[int, int, int, int] | None:
    """窗口的可见边框（左, 上, 右, 下），物理像素：优先取系统合成的边框，取不到时用窗口矩形。"""
    rect = wintypes.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0 and not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """窗口矩形（左, 上, 右, 下），物理像素，和 WM_NCHITTEST 给的鼠标位置同一种坐标；取不到时为 None。"""
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def set_corner_preference(hwnd: int, preference: int) -> bool:
    """设窗口的圆角（DWMWCP_ROUND 圆角、DWMWCP_DEFAULT 系统默认）。只改本进程自己的窗口；系统不支持（Windows 10）、
    句柄无效时返回 False。"""
    value = ctypes.c_int(preference)
    hr = _dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


def _cloaked(hwnd) -> bool:
    value = wintypes.DWORD(0)
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def window_class(hwnd) -> str:
    """窗口的类名（识别记录用）；取不到时为空。"""
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value[:n] if n > 0 else ""


def windows_above(game: int) -> list[TopWindow]:
    """按层次从最上面往下数顶层窗口，数到游戏窗口为止：在游戏窗口上面的那些（从上往下）。没数到游戏窗口时为空表。"""
    pid = os.getpid()
    out: list[TopWindow] = []
    hwnd = _user32.GetTopWindow(None)
    for _ in range(MAX_WINDOWS):
        if not hwnd:
            return []
        if int(hwnd) == int(game):
            return out
        visible = bool(_user32.IsWindowVisible(hwnd))
        rect = frame_rect(hwnd) if visible else None
        if rect is not None:
            ex = _get_window_long(hwnd, GWL_EXSTYLE)
            out.append(TopWindow(
                int(hwnd), rect, own=_pid(hwnd) == pid, visible=visible, minimized=bool(_user32.IsIconic(hwnd)),
                cloaked=_cloaked(hwnd), transparent=bool(ex & WS_EX_TRANSPARENT), topmost=bool(ex & WS_EX_TOPMOST),
            ))
        hwnd = _user32.GetWindow(hwnd, GW_HWNDNEXT)
    return []


def covering(windows, overlay: tuple[int, int, int, int], min_side: int = COVER_MIN) -> list[TopWindow]:
    """windows（游戏窗口上面的顶层窗口，从上往下）里盖住悬浮窗 overlay（左, 上, 右, 下）的：跳过本进程的、隐藏的、
    最小化的、被系统隐藏的、鼠标可以穿透的；按可见边框算，相交的宽、高都至少 min_side 像素才算。保持从上往下的顺序，
    最后一个就是盖住悬浮窗的窗口里最下面的那个（退后时插在它下面）。不碰 Win32，可以单独测试。"""
    left, top, right, bottom = overlay
    out = []
    for w in windows:
        if w.own or not w.visible or w.minimized or w.cloaked or w.transparent:
            continue
        x0, y0 = max(w.rect[0], left), max(w.rect[1], top)
        x1, y1 = min(w.rect[2], right), min(w.rect[3], bottom)
        if x1 - x0 >= min_side and y1 - y0 >= min_side:
            out.append(w)
    return out


def find_game_hwnd(title=None, platform=None):
    """按游戏平台设置找游戏窗口的句柄（找法见 advisor/vision/capture.py 的 _find）；都找不到时为 None。

    给了 title 时照先后找那几个标题。platform 给了 STEAM、TAPTAP 时只找那个端的窗口，不看游戏平台设置（两路识别各找各的
    端）；不给时按设置。用 capture 的那一份找法：截图、前台判断、悬浮窗查看游戏窗口认的一定是同一个窗口。"""
    from ..vision import capture

    return capture._find(title, platform) or None


def probe_game(title=None, cover: int | None = None, platform=None, ignore=None) -> GameProbe:
    """游戏窗口在哪、是不是最小化、前台是游戏还是拾星助手自己。游戏窗口照 find_game_hwnd 找（默认按游戏平台设置；
    platform 给了 STEAM、TAPTAP 时只找那个端）。

    cover 是悬浮窗的句柄：给了、游戏窗口没有最小化、游戏和拾星助手自己的窗口都不在前台时，再查悬浮窗有没有被游戏窗口
    上面的别的窗口盖住（直接截取游戏窗口时用；截取屏幕时不给）。ignore 是同时识别时另一张悬浮窗跟的游戏窗口的句柄：
    它不算盖住这张悬浮窗的窗口，玩家正看着那个游戏，两张悬浮窗叠在一起也不该让这张退到它后面。game_title 是这一次
    找到它时对上的标题，notice 按这一次找的端说。"""
    from ..vision import capture

    located = capture._locate_for(title, platform)
    game, game_title = located if located is not None else (0, "")
    fg = _user32.GetForegroundWindow()
    own = bool(fg) and _pid(fg) == os.getpid()
    if not game:
        return GameProbe(False, None, False, False, own, notice=capture.missing_notice(platform))
    minimized = bool(_user32.IsIconic(game))
    rect = None if minimized else _client_rect(game)
    root = _user32.GetAncestor(fg, GA_ROOT) if fg else None
    game_fg = bool(fg) and (fg == game or root == game)
    probe = GameProbe(True, rect, minimized, game_fg and not minimized, own, hwnd=int(game),
                      game_class=window_class(game), game_title=game_title)
    if cover and not minimized and not probe.game_foreground and not own:
        overlay = frame_rect(cover)
        found = covering(windows_above(int(game)), overlay) if overlay is not None else []
        if ignore is not None:
            found = [w for w in found if w.hwnd != int(ignore)]
        if found:
            lowest = found[-1]
            probe = replace(probe, covered=True, cover_below=lowest.hwnd, cover_class=window_class(lowest.hwnd))
    return probe


def foreground() -> tuple[int, bool]:
    """前台窗口的句柄（没有时为 0，例如前台正在交给另一个程序）和它是不是拾星助手自己的窗口。"""
    fg = _user32.GetForegroundWindow()
    return (int(fg), _pid(fg) == os.getpid()) if fg else (0, False)


def topmost(hwnd: int) -> bool:
    """窗口现在是不是置顶的（WS_EX_TOPMOST）。"""
    return bool(_get_window_long(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def below(hwnd: int, wanted) -> list[int]:
    """按层次从 hwnd 往下数顶层窗口，wanted（句柄）里数到的那些，从上往下；在 hwnd 上面的不算（数到底也没有的不在里面）。"""
    wanted = {int(h) for h in wanted}
    found: list[int] = []
    cur = _user32.GetWindow(hwnd, GW_HWNDNEXT)
    for _ in range(MAX_WINDOWS):
        if not cur or len(found) == len(wanted):
            break
        if int(cur) in wanted:
            found.append(int(cur))
        cur = _user32.GetWindow(cur, GW_HWNDNEXT)
    return found


def set_topmost(hwnd: int, on: bool) -> bool:
    """置顶或取消置顶，不移动、不改大小、不激活。取消置顶的窗口放到普通窗口的最上面（本来就不置顶的不动）。"""
    return bool(_user32.SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    ))


def put_below(hwnd: int, after: int) -> bool:
    """把窗口放在 after 的正下方（after 不是置顶窗口时一并取消置顶），不移动、不改大小、不激活。"""
    return bool(_user32.SetWindowPos(hwnd, after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


# 桌面的窗口类名（Progman 是桌面的窗口，WorkerW 是它后面铺壁纸的那一层）
DESKTOP_CLASSES = ("Progman", "WorkerW")


def step_behind_foreground(hwnd: int) -> bool:
    """把窗口放到前台窗口的正下方（不移动、不改大小、不激活）。前台没有窗口、是拾星助手自己、是置顶窗口（放在它下面又会变成
    置顶）、是桌面（放在它下面就看不见了）时不动，返回 False。"""
    fg, own = foreground()
    if not fg or own or topmost(fg) or window_class(fg) in DESKTOP_CLASSES:
        return False
    return put_below(hwnd, fg)


def bring_to_front(hwnd: int) -> bool:
    """置顶并排到置顶窗口的最前面（盖过设成「总在最前」的播放器等），不移动、不改大小、不激活。"""
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    ok = bool(_user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags))
    return bool(_user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, flags)) and ok


def set_capture_allowed(hwnd: int, allowed: bool) -> bool:
    """allowed 为 True 时截图工具可以截到窗口（WDA_NONE），False 时窗口不出现在截图里（WDA_EXCLUDEFROMCAPTURE）。

    只能设置本进程的窗口；设置失败（例如句柄无效）时返回 False。Windows 10 2004 之前的系统不支持
    WDA_EXCLUDEFROMCAPTURE，按 WDA_MONITOR 处理：设置照常成功，窗口在截图里显示为黑块。

    截取屏幕时由后台识别线程（和导出诊断现截一幅的后台线程）调用（screen.grab_frame 经 excluded），改的是界面线程的
    窗口，不会和界面线程互相等：
    - 这个设置由系统存在内核里窗口的数据上（微软文档：stores the display affinity setting in kernel mode on the hWnd），
      只要求窗口属于本进程，不给窗口所属的线程发消息、不等它处理消息。实测（Windows 11）：窗口所属的
      线程卡住、不处理消息时，别的线程设置和查询都在 2 毫秒内返回，窗口过程一条消息都没收到（隐藏的窗口和显示着的
      分层窗口都一样）；
    - ctypes 调用时放开 GIL，界面线程照常跑；
    - 后台识别截图时只拿着 screen.AFFINITY_LOCK（不拿 Session 的锁），界面线程不会一直等这把锁（screen.policy_lock
      最多等 POLICY_WAIT 秒）：就算设置要等界面线程，也凑不成一直互相等。
    """
    return bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_NONE if allowed else WDA_EXCLUDEFROMCAPTURE))


def exclude_from_capture(hwnd: int) -> bool:
    """让窗口不出现在截图里（Windows 10 2004 及以上；之前的系统上显示为黑块）。"""
    return set_capture_allowed(hwnd, False)


def capture_affinity(hwnd: int) -> int | None:
    """窗口当前的截图属性（WDA_NONE、WDA_EXCLUDEFROMCAPTURE 等）；查询失败时为 None。"""
    value = wintypes.DWORD(0)
    if not _user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
        return None
    return int(value.value)


def own_windows() -> list[tuple[int, tuple[int, int, int, int]]]:
    """本进程正在显示的顶层窗口（悬浮窗、悬停说明、下拉列表、菜单、「关于」「设置」窗口等）：
    [(hwnd, (左, 上, 右, 下))]，物理像素。"""
    pid = os.getpid()
    found: list[tuple[int, tuple[int, int, int, int]]] = []

    def visit(hwnd, _param):
        if hwnd and _user32.IsWindowVisible(hwnd) and _pid(hwnd) == pid:
            rect = wintypes.RECT()
            if _user32.GetWindowRect(hwnd, ctypes.byref(rect)) and rect.right > rect.left and rect.bottom > rect.top:
                found.append((int(hwnd), (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    callback = WNDENUMPROC(visit)  # 调用期间保持引用
    _user32.EnumWindows(callback, 0)
    return found


@contextmanager
def excluded(hwnds):
    """在 with 块里让这些窗口暂时不出现在截图里，结束时（包括出错时）改回（截取屏幕时贴补悬浮窗遮挡的区域用）。

    只改当前是 WDA_NONE 的窗口；本来就不出现在截图里的、查询或设置失败的跳过。
    Windows 10 2004 之前的系统上设置照常成功，但窗口在截图里是黑块而不是它下面的画面。产出实际改过的窗口。
    """
    changed = []
    try:
        for hwnd in hwnds:
            if capture_affinity(hwnd) == WDA_NONE and set_capture_allowed(hwnd, False):
                changed.append(hwnd)
        yield list(changed)
    finally:
        for hwnd in changed:
            set_capture_allowed(hwnd, True)


# ------------------------------------------------------------ 本进程的 DPI 感知
# Qt 的 Windows 窗口平台启动时把进程设成按每块屏幕的缩放（per-monitor）。「替代高 DPI 缩放行为」被改过（ACC.exe 属性里的
# 兼容性设置等）时系统不让它设，进程按系统缩放或不感知 DPI 运行：系统报给本进程的游戏客户区大小被缩小了，直接截取的画面和
# 窗口边框却是物理像素，裁出的游戏画面位置不对，识别认错又不报错。悬浮窗启动后查一次（notices.NoticeView.check_dpi）。

DPI_PER_MONITOR = 2  # GetAwarenessFromDpiAwarenessContext 的返回值：按每块屏幕的缩放（DPI_AWARENESS_PER_MONITOR_AWARE）
# 查到的值的叫法（写进错误记录）
DPI_NAMES = {-1: "无效", 0: "不感知 DPI", 1: "按系统缩放", 2: "按每块屏幕的缩放"}


def _dpi_api():
    """查 DPI 感知用的两个 user32 函数（GetThreadDpiAwarenessContext、GetAwarenessFromDpiAwarenessContext）；Windows 10 1607
    之前没有它们，返回 None。用自己的一份 WinDLL 声明：这两个函数可能没有，不能像 advisor/win32.py 的公共声明那样导入时就
    声明好；自己的一份也不影响别处。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_context = user32.GetThreadDpiAwarenessContext
        get_awareness = user32.GetAwarenessFromDpiAwarenessContext
    except (OSError, AttributeError):  # AttributeError：系统的 user32 里没有这个函数
        return None
    get_context.argtypes = []
    get_context.restype = ctypes.c_void_p  # DPI_AWARENESS_CONTEXT 是句柄一类的值，按指针传
    get_awareness.argtypes = [ctypes.c_void_p]
    get_awareness.restype = ctypes.c_int
    return get_context, get_awareness


def dpi_awareness() -> int | None:
    """调用它的线程（在界面线程上调用）的 DPI 感知：GetAwarenessFromDpiAwarenessContext(GetThreadDpiAwarenessContext())，
    按每块屏幕的缩放时为 DPI_PER_MONITOR（2）。系统没有这两个函数时为 None（不查）。"""
    api = _dpi_api()
    if api is None:
        return None
    get_context, get_awareness = api
    return int(get_awareness(get_context()))


# ------------------------------------------------------------ 按普通优先级打开文件夹
# 打开资源管理器时另起一个写明普通优先级的程序（explorer），不跟着本进程的优先级走；只交出程序自己拼出来的、已有的文件夹
# （open_folder）或文件（reveal_file：打开所在的文件夹并选中它）。

_NORMAL_FLAGS = getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
_open_logged = False  # 按普通优先级打开出过错、已经记进错误记录：之后再出错不再记


def open_folder(path) -> bool:
    """在资源管理器里打开已有的文件夹（导出诊断后提示里的「打开文件夹」），资源管理器按普通优先级起。路径加引号整个交给
    explorer（路径里有空格、逗号也认得对）。不是已有的文件夹时什么都不打开。返回交出去了没有。"""
    folder = os.path.abspath(os.fspath(path))
    if not os.path.isdir(folder) or '"' in folder:
        return False

    def command():
        explorer = _system_path(_kernel32.GetSystemWindowsDirectoryW, "explorer.exe")
        target = folder if folder.endswith("\\") else f'"{folder}"'  # 盘的根（D:\）没有空格、逗号，不加引号
        return f'"{explorer}" {target}', explorer

    return _start_normal(command, folder)


def reveal_file(path) -> bool:
    """在资源管理器里打开已有文件所在的文件夹并选中它（导出诊断后提示里的「打开文件夹」：选中诊断包的压缩包），资源管理器按
    普通优先级起。参数照 explorer 的写法「/select,"完整路径"」（路径加引号，有空格、逗号也认得对）。不是已有的文件时什么都
    不打开。起不了 explorer 时同 open_folder 退回系统的打开方式，打开它所在的文件夹（选不中它）。返回交出去了没有。"""
    target = os.path.abspath(os.fspath(path))
    if not os.path.isfile(target) or '"' in target:
        return False

    def command():
        explorer = _system_path(_kernel32.GetSystemWindowsDirectoryW, "explorer.exe")
        return f'"{explorer}" /select,"{target}"', explorer

    return _start_normal(command, os.path.dirname(target))


def _system_path(get_dir, name: str) -> str:
    """系统文件夹里的程序的完整路径（不按搜索顺序找，程序文件夹、当前文件夹里同名的程序不会被起来）。"""
    buf = ctypes.create_unicode_buffer(260)
    n = get_dir(buf, len(buf))
    if not 0 < n < len(buf):
        raise OSError(f"取不到系统文件夹（{getattr(get_dir, '__name__', get_dir)} 返回 {n}）")
    return os.path.join(buf.value, name)


def _start_normal(command, target: str) -> bool:
    """按普通优先级起 command() 给的（参数, 程序的完整路径或 None），不等它，不开控制台窗口。起不了时第一次记进错误记录，
    退回系统的打开方式（os.startfile：这时起来的程序随系统，总比打不开好）。返回交出去了没有。"""
    global _open_logged
    try:
        args, executable = command()
        subprocess.Popen(args, executable=executable, creationflags=_NORMAL_FLAGS, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        detail = traceback.format_exc()
    if not _open_logged:
        _open_logged = True
        log_error("按普通优先级打开文件夹出错，改用系统的打开方式（拾星助手仍在运行）", detail, append=True)
    try:
        os.startfile(target)  # noqa: S606  用户点了按钮才打开
        return True
    except OSError:
        return False
