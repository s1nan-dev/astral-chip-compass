"""截取游戏窗口的客户区（截取屏幕的方式）、按游戏平台找游戏窗口（可以指定只找哪个端），以及直接截取时要用到的游戏窗口
位置。只读画面，不向游戏发送任何输入。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np

from ..win32 import DWMWA_EXTENDED_FRAME_BOUNDS, GA_ROOT, PROCESS_QUERY_LIMITED_INFORMATION, SetProcessDPIAware
from ..win32 import dwmapi as _dwm
from ..win32 import kernel32 as _k32
from ..win32 import user32 as _win

# 游戏窗口的类名：三个版本（Steam 版的国服、国际服，TapTap 版）的游戏引擎文件逐字节相同，主窗口都是 UnityWndClass。标题本身
# 不够用：「吉星派对」这四个字太普通，在资源管理器里打开一个叫「吉星派对」的文件夹，窗口标题也是它（类名 CabinetWClass），只比
# 标题会认错，所以标题和类名都要对上
GAME_CLASS = "UnityWndClass"
# 游戏的两个渠道：Steam 版是 Steam 上同一个游戏、启动时选服务器，两个窗口标题不同（国服、国际服）；TapTap 版是国服的另一个
# 渠道，画面、资源都和国服相同，窗口标题是游戏的产品名。同一个版本只能开一份（引擎自己拦），Steam 版和 TapTap 版可以同时开，
# 所以最多同时两个游戏窗口
STEAM = "steam"
TAPTAP = "taptap"
# 游戏平台的第三种设置：两个客户端同时识别（Steam 版、TapTap 版各跑一路识别、各一张悬浮窗）。找窗口时和不指定端一样，
# 哪个都认、都开着时跟后启动的那个（_chosen）；两路识别各自直接指定端，不读这个设置
BOTH = "both"
PLATFORM_TITLES = {STEAM: ("AstralParty_CN", "AstralParty_INT"), TAPTAP: ("吉星派对",)}
# 窗口标题 → 对局报告里的客户端（国服、国际服、TapTap）。国服、国际服都是 Steam 版，标题不同
CLIENT_TITLES = {"AstralParty_CN": "cn", "AstralParty_INT": "int", "吉星派对": "taptap"}
# 给玩家看的平台名：设置窗口显示「Steam端（自动识别）」，找不到窗口的提示里也用它
PLATFORM_NAMES = {STEAM: "Steam端", TAPTAP: "TapTap端"}
# 同时识别时悬浮窗标题栏最左边小标签上的字（不带「端」）：两张悬浮窗的标题一样，靠这个标签分得出跟的是哪个端
PLATFORM_TAGS = {STEAM: "Steam", TAPTAP: "TapTap"}
# 第三种平台设置给玩家看的名字（设置窗口的下拉框用）。不放进 PLATFORM_NAMES：那个表只放一个端的名字，
# 「{name}（自动识别）」和只找一个端时的提示按它取
BOTH_NAME = "Steam端+TapTap端"
# 所有游戏窗口的标题，Steam 的两个在前：自动识别时照这个先后找，同时找到两个时再按游戏进程的启动时间取晚的那个（_find）。
# advisor/ui/winapi.py、tools/build_release.py 的 GAME_TITLES 和这里相同（测试核对）
WINDOW_TITLES = PLATFORM_TITLES[STEAM] + PLATFORM_TITLES[TAPTAP]
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # SHQueryUserNotificationState：有程序在独占全屏模式下运行（Direct3D）
# 找不到游戏窗口时给玩家看的一句（同悬浮窗的 wording.NO_GAME，测试核对）。不写窗口标题：三个标题都写上，悬浮窗一行显示的
# 摘要放不下，句末的「请先启动游戏」会被省略掉；按哪些标题找写在使用说明的常见问题里
NOT_FOUND = "未找到游戏窗口，请先启动游戏。"
# 设了游戏平台、那个平台的窗口没开、开着的是另一个平台的窗口时的一句：点明是设置的原因，不让人以为游戏没开。
# {name} 填 PLATFORM_NAMES 里的名字。和 NOT_FOUND 差不多长，避免悬浮窗一行显示时句末被省略
NOT_FOUND_PLATFORM = "游戏平台选的是「{name}」，没找到它的窗口。"
# 指定了端（两路识别各找各的端）、那个端的窗口没开时的一句：{name} 填 PLATFORM_NAMES 里的名字。两路识别同时开着时，另一个
# 端开着也是这一句：写明没找到哪个端的窗口，玩家才知道该开哪一个。和 NOT_FOUND 差不多长，避免悬浮窗一行显示时句末被省略
NOT_FOUND_ONE = "未找到「{name}」的游戏窗口。"
MINIMIZED = "游戏窗口已最小化，无法截图。请还原游戏窗口。"

# 游戏平台：界面线程设、识别线程读，只是一个赋值，不加锁；None 为自动识别。用到它的函数开头只读一次（_chosen、find_window、
# find_game）：找的途中界面线程改了设置，这一次照旧按开头读到的找，找不到时的提示也按它说。两路识别同时跑时各自直接指定端
# （参数 platform），不读这里
_platform: str | None = None
# 上一次找到的游戏窗口：（哪个端, 对上的标题），found_platform、found_title 用。找不到窗口时沿用上一次的。两项放在一起换，
# 别的线程读到的总是同一次找到的
_found: tuple[str | None, str] | None = None
# 按端各记一份上一次找到的标题（端 → 标题）：两开（Steam 版、TapTap 版各一路识别）时两路会先后覆盖 _found，一路读到的
# 可能是另一路的标题；每张悬浮窗按自己跟的端从这里取，认得出这一局是哪个客户端
_found_titles: dict[str, str] = {}
# （窗口句柄, 进程号）到进程启动时间（_started 的缓存），只留最近的几项
_STARTED: dict[tuple[int, int], int] = {}
_STARTED_KEEP = 8


def set_platform(platform) -> None:
    """设置游戏平台：STEAM、TAPTAP、BOTH（两个客户端同时识别），None 为自动识别。别的值按自动识别，不抛错（设置文件被
    改坏时照常能用）。"""
    global _platform
    _platform = platform if isinstance(platform, str) and platform in (STEAM, TAPTAP, BOTH) else None


def platform() -> str | None:
    """现在设的游戏平台（STEAM、TAPTAP、BOTH；None 为自动识别）。"""
    return _platform


def found_platform() -> str | None:
    """上一次找到的游戏窗口是哪个端（STEAM、TAPTAP）；还没找到过为 None。之后找不到窗口、改了设置也照旧是上一次的结果。"""
    found = _found
    return found[0] if found is not None else None


def found_title(platform=None) -> str:
    """上一次找到的游戏窗口是按哪个标题找到的（WINDOW_TITLES 里的一个；识别记录靠它分出是哪个客户端）；还没找到过为空。
    就是找窗口时对上的那个，不另读窗口的标题。沿用的规矩同 found_platform。

    platform 给了 STEAM、TAPTAP 时取那个端上一次找到的标题：两开时两路识别会先后覆盖「上一次找到的」，按端各记一份，
    这一张悬浮窗才取得到自己跟的那个端的。"""
    if platform in PLATFORM_TITLES:
        return _found_titles.get(platform, "")
    found = _found
    return found[1] if found is not None else ""


def client_of(title) -> str | None:
    """窗口标题 → 对局报告里的客户端（cn、int、taptap）；不是游戏窗口的标题时为 None。"""
    return CLIENT_TITLES.get(title) if isinstance(title, str) else None


def _platform_of(title: str) -> str | None:
    """这个窗口标题属于哪个端；不是游戏窗口的标题时为 None。"""
    for name, titles in PLATFORM_TITLES.items():
        if title in titles:
            return name
    return None


def _started(hwnd: int) -> int:
    """窗口所属进程的启动时间：FILETIME 的 64 位值，越大越晚；取不到（进程已经退出、权限不够）时为 0。

    只在自动识别同时找到两个平台的窗口、要比先后时才查。按（窗口句柄, 进程号）缓存：同一个进程的启动时间不会变，而找
    窗口每秒要调用好多次。只按进程号缓存不行：游戏关了再开，新进程可能正好拿到旧进程的进程号，拿到的就是旧进程的时间，
    后启动的反被当成先启动的；关了再开的游戏窗口是新建的，句柄和原来的不同，带上句柄就不会拿错。取不到的不缓存，下次再查。"""
    pid = _pid(hwnd)
    if not pid:
        return 0
    key = (int(hwnd), pid)
    known = _STARTED.get(key)
    if known is not None:
        return known
    value = _process_started(pid)
    if value:
        if len(_STARTED) >= _STARTED_KEEP:
            _STARTED.clear()  # 关掉的游戏留下的旧项不再用到：满了就清空重来
        _STARTED[key] = value
    return value


def _process_started(pid: int) -> int:
    """进程的启动时间（GetProcessTimes 的创建时间，FILETIME 的 64 位值）；取不到时为 0。只申请查询少量信息的权限。"""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return 0
    finally:
        _k32.CloseHandle(handle)
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _one(platform) -> str | None:
    """参数明确指定了一个端时返回它（STEAM、TAPTAP）；没指定（None、BOTH、别的值）时为 None。"""
    return platform if isinstance(platform, str) and platform in PLATFORM_TITLES else None


def _chosen(platform) -> str | None:
    """这一次找窗口按哪个端找：参数给了 STEAM、TAPTAP 时只找这个端（不看模块里的设置）；给了 BOTH 时和自动识别一样；
    没给（None）时照旧用模块里的设置。别的值按没给处理。"""
    if platform == BOTH:
        return BOTH
    return _one(platform) or _platform


def _find(title=None, platform=None) -> int:
    """找游戏窗口的句柄；都找不到时为 0。

    title 给了一个或几个标题时照先后找第一个（带标题查找的调用这样用）。不给时按游戏平台找：platform 给了 STEAM、TAPTAP
    时只找那个端（两路识别各找各的端，不看模块里的设置）；其余按模块里的设置，设了一个端时只找那个端，BOTH、None 都是
    自动识别：所有标题都找，两个平台各找到一个时跟最后启动的那个（取不到启动时间的排在后面，都取不到时照标题先后）。标题
    和类名都对上才算：同名文件夹的资源管理器窗口不算。找到后记下是哪个端、对上的是哪个标题（found_platform、found_title）。"""
    return _find_as(title, _chosen(platform))


def _locate(title, chosen: str | None) -> tuple[int, str] | None:
    """找游戏窗口：返回这一次找到的（句柄, 对上的标题）；找不到时为 None。

    chosen 是这一次用的平台设置（STEAM、TAPTAP 只找那个端；BOTH、None 为自动识别），调用的地方读一次传进来。同一句柄对上
    几个标题时只算一次；都找到时取最后启动的那个。找到后照旧更新 found_platform、found_title，但两路识别同时跑时各自用
    返回的这一对，不读记下的那个（另一路会覆盖）。"""
    global _found
    if title is None:
        names = PLATFORM_TITLES.get(chosen, WINDOW_TITLES)
        first = chosen in PLATFORM_TITLES
    else:
        names = (title,) if isinstance(title, str) else tuple(title)
        first = True
    found: list[tuple[int, str]] = []
    seen: set[int] = set()
    for name in names:
        hwnd = _win.FindWindowW(GAME_CLASS, name)
        if hwnd:
            # 同一个窗口对上了几个标题（系统里改了标题、假的 FindWindowW 对每个标题都给同一个句柄）时只算一次：
            # 不然会被当成同时开着几个窗口，还要拿同一个进程去比启动时间
            if int(hwnd) in seen:
                continue
            seen.add(int(hwnd))
            found.append((int(hwnd), name))
            if first:
                break
    if not found:
        return None
    located = found[0] if len(found) == 1 else max(found, key=lambda item: _started(item[0]))
    _found = (_platform_of(located[1]), located[1])
    if _found[0] is not None:
        _found_titles[_found[0]] = located[1]
    return located


def _locate_for(title=None, platform=None) -> tuple[int, str] | None:
    """按平台找游戏窗口（platform 同 _find）：返回这一次找到的（句柄, 对上的标题），找不到时为 None。"""
    return _locate(title, _chosen(platform))


def _find_as(title, chosen: str | None) -> int:
    """同 _find，平台设置用 chosen（调用的地方读一次传进来）。"""
    located = _locate(title, chosen)
    return located[0] if located is not None else 0


def _other_open(chosen: str) -> bool:
    """设的端（chosen）以外的游戏窗口开着没有。"""
    return any(_win.FindWindowW(GAME_CLASS, title)
               for name, titles in PLATFORM_TITLES.items() if name != chosen for title in titles)


def _platform_gone(chosen: str | None) -> bool:
    """找不到游戏窗口的原因在游戏平台设置上：设了一个端（chosen），它的窗口没开，开着的是另一个端。"""
    return chosen in PLATFORM_TITLES and _other_open(chosen)


def _gone(chosen: str | None, one: str | None = None) -> "WindowGone":
    """按游戏平台找不到游戏窗口时的异常。one 是这一次明确指定的端（chosen 是找的时候用的设置）：给了就是
    NOT_FOUND_ONE；不给时照旧：设了一个端、开着的是另一个端时点明是设置的原因。"""
    if one is not None:
        return WindowGone(one=one)
    return WindowGone(platform=chosen if _platform_gone(chosen) else None)


def missing_notice(platform=None) -> str:
    """现在找不到游戏窗口时给玩家看的那一句（winapi.probe_game 的 notice）。不给 platform 时用眼下设的游戏平台：设了一个
    端、开着的是另一个端时点明是设置的原因，其余是 NOT_FOUND。给了 STEAM、TAPTAP 时（两路识别各找各的端）是 NOT_FOUND_ONE，
    填上那个端的名字：另一路可能正认着另一个端，写明没找到哪个端的窗口，玩家才知道该开哪一个。"""
    one = _one(platform)
    if one is not None:
        return NOT_FOUND_ONE.format(name=PLATFORM_NAMES[one])
    chosen = _platform
    if _platform_gone(chosen):
        return NOT_FOUND_PLATFORM.format(name=PLATFORM_NAMES[chosen])
    return NOT_FOUND


class WindowGone(RuntimeError):
    """找不到游戏窗口、游戏窗口最小化：截取不到画面，不是出错。notice 是给玩家看的固定中文：最小化时是 MINIMIZED；给了
    one（明确指定了找哪个端，两路识别各找各的端）时是 NOT_FOUND_ONE，填上那个端的名字；给了 platform（设的那个端：设了游戏
    平台、那个端的窗口没开、开着的是另一个端）时是 NOT_FOUND_PLATFORM，填上那个端的名字；其余是 NOT_FOUND。两项都给了时按
    one。不接别的文字：悬浮窗的后台识别线程只把这一种原样显示，别的 RuntimeError 一律按截取出错提示、原文只进错误记录。
    是 RuntimeError 的一种，只按 RuntimeError 接的地方也接得住。"""

    def __init__(self, minimized: bool = False, platform: str | None = None, one: str | None = None):
        if minimized:
            self.notice = MINIMIZED
        elif isinstance(one, str) and one in PLATFORM_NAMES:
            self.notice = NOT_FOUND_ONE.format(name=PLATFORM_NAMES[one])
        elif isinstance(platform, str) and platform in PLATFORM_NAMES:
            self.notice = NOT_FOUND_PLATFORM.format(name=PLATFORM_NAMES[platform])
        else:
            self.notice = NOT_FOUND
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


def game_in_front(title=None, platform=None) -> bool:
    """前台窗口属于游戏进程（游戏窗口本身，或游戏进程的其他窗口）。游戏窗口照 _find 找（默认按游戏平台设置，给了
    platform 时只找那个端，和识别截的是同一个窗口）；找不到游戏窗口时为 False。"""
    game = _find(title, platform)
    fg = _win.GetForegroundWindow()
    if not game or not fg:
        return False
    pid = _pid(game)
    return pid != 0 and _pid(int(fg)) == pid


def exclusive_fullscreen(platform=None) -> bool:
    """游戏处于独占全屏模式（Direct3D）：悬浮窗显示不到它上面，截图也可能失败。只查询系统状态，不碰游戏。

    系统报的是前台的程序（独占全屏的程序只在前台时保持独占），所以只在前台窗口属于游戏时才算：直接截取游戏窗口时
    切出游戏也照常识别，这时报出来的是别的程序（例如另一块屏幕上独占全屏的播放器、另一个游戏），不是游戏。platform 同
    game_in_front。"""
    if _notification_state() != QUNS_RUNNING_D3D_FULL_SCREEN:
        return False
    return game_in_front(platform=platform)


def find_window(platform=None) -> int:
    """游戏窗口的句柄（按游戏平台设置找，见 _find）。platform 给了 STEAM、TAPTAP 时只找那个端，不看设置。找不到时抛出
    WindowGone（指定了端时是 NOT_FOUND_ONE）。"""
    _set_dpi_awareness()
    one = _one(platform)
    chosen = _chosen(platform)
    hwnd = _find_as(None, chosen)
    if not hwnd:
        raise _gone(chosen, one)
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


def window_pid(hwnd: int) -> int:
    """窗口所属进程的进程号；句柄为 0、窗口不在或读不到时为 0。只读窗口归属，不碰进程本身。"""
    return _pid(int(hwnd)) if hwnd else 0


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


def find_game(title=None, platform=None) -> GameWindow:
    """找游戏窗口（默认按游戏平台设置，给了 title 时照先后找那几个标题，见 _find；platform 给了 STEAM、TAPTAP 时只找那个
    端，不看设置）并查它的位置和前台状态。找不到时抛出 WindowGone（「未找到游戏窗口……」；按平台找且开着的是另一个端时
    点明是设置的原因；指定了端时是 NOT_FOUND_ONE）。"""
    _set_dpi_awareness()
    one = _one(platform)
    chosen = _chosen(platform)
    hwnd = _find_as(title, chosen)
    if not hwnd:
        raise WindowGone() if title is not None else _gone(chosen, one)
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
