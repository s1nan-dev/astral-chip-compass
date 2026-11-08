"""截屏、找窗口、热键、窗口层次、按普通优先级打开文件夹共用的 Win32 函数声明。

集中声明 ctypes 的 WinDLL、argtypes、restype、回调类型和常量：同一个系统函数只声明一次；窗口句柄一律按指针传
（wintypes.HWND / c_void_p），64 位下不会被截成 32 位。不导入 Qt，识别模块和窗口模块都能用。

用单独的 WinDLL 实例声明参数类型，不影响其他模块里的 ctypes.windll.user32。用到它的几个模块共用这里的同一份实例：别处不要
再给这些函数另设 argtypes、restype（会连带改掉别的模块的声明），要用新的函数时在这里补上声明。只查询窗口状态、设置本进程
窗口的属性，不向游戏发送任何输入，不激活、不移动任何窗口。

不在这里的：读 MachineGuid、卷序列号（unlock.py）、防多开（ui/single_instance.py）、画缩略图的作业对象
（ui/skin_preview.py）、直接截取窗口用的系统组件（vision/window_capture.py）、取程序路径（paths.py）、结束进程
（ui/overlay_parts/app.py 的 terminate_process）、查本进程的 DPI 感知（ui/winapi.py 的 _dpi_api：这两个函数旧系统上没有，
导入时声明不了）、声明高性能、读回本进程的优先级和读这台电脑的配置（sysinfo.py：处理器、内存、电源、显示器、显卡，
诊断包用）——各自用途独立，自己声明。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int  # 出错时返回 -1，不能按 BOOL 处理

# 64 位系统上是 GetWindowLongPtrW；32 位的 user32 里只有 GetWindowLongW
get_window_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
get_window_long.restype = ctypes.c_ssize_t

SetProcessDPIAware = user32.SetProcessDPIAware

try:
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
except (OSError, AttributeError):  # 没有桌面窗口管理器的系统（实际用不到）：vision/capture.py 改按窗口矩形算
    dwmapi = None  # ui/winapi.py 不判断 None：程序支持的系统都有它

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
# Windows 文件夹（ui/winapi.py 的 open_folder 按完整路径起 explorer）：返回写进去的字数，缓冲区不够时返回要的字数
kernel32.GetSystemWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
kernel32.GetSystemWindowsDirectoryW.restype = wintypes.UINT
# 查别的进程的启动时间（vision/capture.py 比两个游戏窗口谁后启动）：只要这么一点信息，不用申请更大的权限
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME)]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_HWNDNEXT = 2
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT = 0x00000008, 0x00000020
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
# 窗口圆角（Windows 11 起才有；Windows 10 上设置返回错误、照旧直角）：系统默认、不要圆角、圆角
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DEFAULT, DWMWCP_DONOTROUND, DWMWCP_ROUND = 0, 1, 2
HWND_TOP, HWND_TOPMOST, HWND_NOTOPMOST = 0, -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
WDA_NONE = 0  # 截图工具和录屏软件可以截到窗口（系统默认）
# 窗口不出现在任何截图和录屏里。截取屏幕时临时排除悬浮窗遮挡的小块用。Windows 10 2004 及以上支持；之前的系统按
# WDA_MONITOR 处理（设置照常成功，窗口在截图里显示为黑块）
WDA_EXCLUDEFROMCAPTURE = 0x11
