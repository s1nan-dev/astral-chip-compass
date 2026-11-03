"""截取游戏窗口的客户区（截取屏幕的方式），以及直接截取时要用到的游戏窗口位置。只读画面，不向游戏发送任何输入。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np

from ..win32 import DWMWA_EXTENDED_FRAME_BOUNDS, GA_ROOT, SetProcessDPIAware
from ..win32 import dwmapi as _dwm
from ..win32 import user32 as _win

# 游戏窗口的标题（游戏的产品名）：国服、国际服。两边是 Steam 上同一个游戏、启动时选服务器，拾星助手用到的画面两边一样，
# 只是窗口标题不同。照先后找，找到哪个用哪个：同一个游戏、一次选一个服务器，两个窗口同时开着的极少见，真遇上了照第一个
# 找到的（国服）认，不另做判断。advisor/ui/winapi.py、tools/build_release.py 的 GAME_TITLES 和这里相同（测试核对）
WINDOW_TITLES = ("AstralParty_CN", "AstralParty_INT")
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # SHQueryUserNotificationState：有程序在独占全屏模式下运行（Direct3D）
# 找不到游戏窗口时给玩家看的一句（同悬浮窗的 wording.NO_GAME，测试核对）。不写窗口标题：两个标题都写上，悬浮窗一行显示的
# 摘要放不下，句末的「请先启动游戏」会被省略掉；按哪两个标题找写在使用说明的常见问题里
NOT_FOUND = "未找到游戏窗口，请先启动游戏。"
MINIMIZED = "游戏窗口已最小化，无法截图。请还原游戏窗口。"


def _find(title=WINDOW_TITLES) -> int:
    """照先后按标题找游戏窗口（title 可以是一个标题，也可以是几个），返回第一个找到的句柄；都找不到时为 0。"""
    for name in (title,) if isinstance(title, str) else title:
        hwnd = _win.FindWindowW(None, name)
        if hwnd:
            return int(hwnd)
    return 0


class WindowGone(RuntimeError):
    """找不到游戏窗口、游戏窗口最小化：截取不到画面，不是出错。notice 是给玩家看的固定中文，只从 NOT_FOUND、MINIMIZED
    这两句里按情形取，不接别的文字：悬浮窗的后台识别线程只把这一种原样显示，别的 RuntimeError 一律按截取出错提示、原文
    只进错误记录。是 RuntimeError 的一种，只按 RuntimeError 接的地方也接得住。"""

    def __init__(self, minimized: bool = False):
        self.notice = MINIMIZED if minimized else NOT_FOUND
        super().__init__(self.notice)


_DPI_SET = False


def _set_dpi_awareness() -> None:
    """把进程的 DPI 感知方式设成「系统感知」（全进程的设置，只需一次）。

    截屏和窗口位置都按物理像素算，要在取窗口位置、截屏之前设好。做成显式函数、由用到坐标的入口调用，
    不在导入时就调用（那是全进程的副作用）：界面库自己会设 DPI 感知，等它设完再设，避免互相覆盖。"""
    global _DPI_SET
    if _DPI_SET:
        return
    try:
        SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    _DPI_SET = True


# 窗口句柄按指针传的声明在 advisor/win32.py（截屏、找窗口、热键、窗口层次共用）：_win 是 user32，_dwm 是 dwmapi
# （这台电脑没有桌面窗口管理器时为 None）。


def _notification_state() -> int | None:
    """SHQueryUserNotificationState 的结果（QUNS_*）；查询失败时为 None。"""
    state = ctypes.c_int(0)
    try:
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except (AttributeError, OSError):
        return None
    return int(state.value) if hr == 0 else None


def game_in_front(title=WINDOW_TITLES) -> bool:
    """前台窗口属于游戏进程（游戏窗口本身，或游戏进程的其他窗口）。游戏窗口照 _find 找（国服、国际服都认，和识别截的
    是同一个窗口）；找不到游戏窗口时为 False。"""
    game = _find(title)
    fg = _win.GetForegroundWindow()
    if not game or not fg:
        return False
    pid = _pid(game)
    return pid != 0 and _pid(int(fg)) == pid


def exclusive_fullscreen() -> bool:
    """游戏处于独占全屏模式（Direct3D）：悬浮窗显示不到它上面，截图也可能失败。只查询系统状态，不碰游戏。

    系统报的是前台的程序（独占全屏的程序只在前台时保持独占），所以只在前台窗口属于游戏时才算：直接截取游戏窗口时
    切出游戏也照常识别，这时报出来的是别的程序（例如另一块屏幕上独占全屏的播放器、另一个游戏），不是游戏。"""
    if _notification_state() != QUNS_RUNNING_D3D_FULL_SCREEN:
        return False
    return game_in_front()


def find_window() -> int:
    """游戏窗口的句柄（国服、国际服都认，见 WINDOW_TITLES）。找不到时抛出 WindowGone。"""
    _set_dpi_awareness()
    hwnd = _find()
    if not hwnd:
        raise WindowGone()
    return hwnd


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """返回客户区在屏幕上的 (left, top, width, height)。"""
    _set_dpi_awareness()
    rect = wintypes.RECT()
    _win.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    _win.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def grab_region(left: int, top: int, width: int, height: int) -> np.ndarray:
    """截屏幕上的一块（物理像素，多显示器时可以是负坐标），返回 BGR 图。

    BGRA 转 BGR 和直接截取用同一个 window_capture.to_bgr：和取前三个通道逐像素相同，只用当前线程分段转
    （4K 约 3 毫秒 CPU；OpenCV 整幅多线程转墙钟约 1.3 毫秒，但合计 17～20 毫秒 CPU）。"""
    from .window_capture import to_bgr

    _set_dpi_awareness()
    with mss.MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return to_bgr(np.asarray(shot))


def grab_client() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """返回游戏窗口客户区的 BGR 图和客户区在屏幕上的 (left, top, width, height)。找不到窗口、窗口最小化时抛出 WindowGone。"""
    rect = client_rect(find_window())
    left, top, width, height = rect
    if width <= 0 or height <= 0:
        raise WindowGone(minimized=True)
    return grab_region(left, top, width, height), rect


def grab() -> np.ndarray:
    """返回游戏窗口客户区的 BGR 图。找不到窗口、窗口最小化时抛出 WindowGone。"""
    return grab_client()[0]


# ---------------------------------------------------------------- 直接截取时用的窗口位置


@dataclass(frozen=True)
class GameWindow:
    """一次查询的游戏窗口。坐标都是屏幕上的物理像素。

    client：客户区 (左, 上, 宽, 高)，最小化时为 None；offset：客户区左上角相对窗口可见边框左上角的偏移
    （直接截取截到的是可见边框以内的整个窗口，按它裁出客户区；带标题栏约 (2, 45)，无边框全屏为 (0, 0)）；
    foreground：游戏窗口在前台；own_foreground：拾星助手自己的窗口在前台。"""

    hwnd: int
    minimized: bool
    client: tuple[int, int, int, int] | None
    offset: tuple[int, int]
    foreground: bool
    own_foreground: bool


def is_window(hwnd: int) -> bool:
    """句柄对应的窗口还在。"""
    return bool(hwnd) and bool(_win.IsWindow(hwnd))


def frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口实际可见的边框 (左, 上, 右, 下)：DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)，取不到时用窗口矩形。"""
    _set_dpi_awareness()
    rect = wintypes.RECT()
    if _dwm is not None and _dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                                        ctypes.sizeof(rect)) == 0:
        return rect.left, rect.top, rect.right, rect.bottom
    if _win.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def _client(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not _win.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not _win.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return origin.x, origin.y, width, height


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _win.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_info(hwnd: int) -> GameWindow:
    """查一个窗口的位置和前台状态（GameWindow）。窗口已经不在时抛出 WindowGone（同找不到游戏窗口）。"""
    _set_dpi_awareness()
    if not is_window(hwnd):
        raise WindowGone()
    minimized = bool(_win.IsIconic(hwnd))
    client = None if minimized else _client(hwnd)
    offset = (0, 0)
    if client is None:
        minimized = True  # 客户区为空（最小化或正在隐藏）：同最小化，截不到画面
    else:
        bounds = frame_bounds(hwnd)
        if bounds is not None:
            offset = (max(0, client[0] - bounds[0]), max(0, client[1] - bounds[1]))
    fg = _win.GetForegroundWindow()
    root = _win.GetAncestor(fg, GA_ROOT) if fg else None
    foreground = bool(fg) and not minimized and (int(fg) == int(hwnd) or (root is not None and int(root) == int(hwnd)))
    own = bool(fg) and _pid(fg) == os.getpid()
    return GameWindow(int(hwnd), minimized, client, offset, foreground, own)


def find_game(title=WINDOW_TITLES) -> GameWindow:
    """找游戏窗口（title 照先后找，见 _find）并查它的位置和前台状态。找不到时抛出 WindowGone（「未找到游戏窗口……」）。"""
    _set_dpi_awareness()
    hwnd = _find(title)
    if not hwnd:
        raise WindowGone()
    return window_info(hwnd)


def visible_at(hwnd: int, points) -> bool:
    """屏幕上这几个点（物理像素）看到的都是这个窗口（或它的子窗口），没有被别的窗口盖住。"""
    _set_dpi_awareness()
    for x, y in points:
        at = _win.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not at:
            return False
        root = _win.GetAncestor(at, GA_ROOT)
        if int(at) != int(hwnd) and (not root or int(root) != int(hwnd)):
            return False
    return True
