"""直接截取的实机测试用的测试窗口进程（tests/live_capture_run.py 在子进程里启动它；测试窗口层次时也可以单独开一个，
当作「别的程序的窗口」）。

Win32 窗口（测试专用的类名和标题，不用游戏的标题），默认用 DirectX 交换链出图（和游戏一样）：完全在屏幕外的窗口
收不到 WM_PAINT、用 GDI 画的内容会被裁掉，DirectX 交换链交出的画面照样交给桌面窗口管理器，直接截取截得到。
另有一个同进程的「盖住的窗口」，用来把测试窗口完全盖住。

窗口始终在所有屏幕的范围以外（启动参数给位置，例如 x = -20000）；不进任务栏（工具窗口），不激活
（WS_EX_NOACTIVATE、SW_SHOWNOACTIVATE、SWP_NOACTIVATE）。工具窗口最小化后系统默认把它放到主屏左下角，
所以建窗口时和每次最小化之前都先把「最小化后的位置」也设到屏幕外（SetWindowPlacement）。

用法：python tests/live_window_host.py 左 上 宽 高 [--frameless] [--topmost] [--transparent] [--plain]
  --frameless 无边框（客户区就是整个窗口）；--topmost 总在最前；--transparent 鼠标可穿透的透明覆盖层样式；
  --plain 不建 DirectX 交换链（只当作盖在上面的窗口用）。
命令从标准输入读，每行一条；每条执行完在标准输出回一行 JSON（窗口句柄、各矩形、是否最小化、前台窗口是不是测试窗口等）：
  info；geom 左 上 宽 高；cover on|off；min；restore；pattern；image 图片路径；freeze；unfreeze；quit。
画面：pattern 是四色块（左上红、右上绿、左下蓝、右下品红）加顶部一条黑白码（编码绘制时刻的毫秒数）；
image 显示一张图片（缩放到客户区大小），没有黑白码。freeze 停止出图（画面静止），unfreeze 恢复。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # 每显示器 DPI 感知 v2，和悬浮窗（Qt）相同

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.vision import window_capture as wc  # noqa: E402  显卡设备和 COM 调用沿用正式模块的写法

kernel32 = ctypes.WinDLL("kernel32")
dwmapi = ctypes.WinDLL("dwmapi")
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


class SWAP_CHAIN_DESC1(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT), ("Format", wintypes.UINT),
                ("Stereo", wintypes.BOOL), ("SampleCount", wintypes.UINT), ("SampleQuality", wintypes.UINT),
                ("BufferUsage", wintypes.UINT), ("BufferCount", wintypes.UINT), ("Scaling", wintypes.UINT),
                ("SwapEffect", wintypes.UINT), ("AlphaMode", wintypes.UINT), ("Flags", wintypes.UINT)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.ValidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

WS_OVERLAPPED, WS_CAPTION, WS_SYSMENU, WS_POPUP = 0x0, 0x00C00000, 0x00080000, 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = (
    0x8, 0x20, 0x80, 0x80000, 0x08000000)
WM_DESTROY, WM_PAINT, WM_ERASEBKGND, WM_GETMINMAXINFO, WM_TIMER, WM_APP = 0x2, 0xF, 0x14, 0x24, 0x113, 0x8000
SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x4, 0x10, 0x40
HWND_TOP = 0
SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 0, 4, 7
GW_HWNDPREV = 3
WPF_SETMINPOSITION = 1
DWMWA_EXTENDED_FRAME_BOUNDS = 9
CELLS, STRIP = 24, 12  # 黑白码 24 格；码条高 12 逻辑像素
QUAD = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 255)]  # 左上红、右上绿、左下蓝、右下品红（RGB）
TIMER_MS = 16
MAX_TRACK = 16384  # 窗口可以比屏幕大（4K 无边框窗口）

IID_IDXGIFactory2 = wc._guid("50C83A1C-E072-4C48-87B0-3630FA36A6D0")
DXGI_DEVICE_GET_ADAPTER = 7  # IDXGIDevice::GetAdapter
DXGI_OBJECT_GET_PARENT = 6  # IDXGIObject::GetParent
FACTORY2_CREATE_SWAP_CHAIN_FOR_HWND = 15
SWAP_PRESENT, SWAP_GET_BUFFER, SWAP_RESIZE_BUFFERS = 8, 9, 13
CONTEXT_UPDATE_SUBRESOURCE = 48
DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL = 3


def now_code() -> int:
    return int(time.perf_counter() * 1000) & 0xFFFFFF


def say(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def client_size(hwnd) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return max(1, rect.right), max(1, rect.bottom)


def rects(hwnd) -> dict:
    window, client, frame = wintypes.RECT(), wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(window))
    user32.GetClientRect(hwnd, ctypes.byref(client))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame), ctypes.sizeof(frame))
    return {"window": [window.left, window.top, window.right, window.bottom],
            "client": [origin.x, origin.y, client.right, client.bottom],
            "frame": [frame.left, frame.top, frame.right, frame.bottom]}


def set_min_position(hwnd, x: int, y: int) -> None:
    """只改最小化后的位置，不改显示状态（隐藏的保持隐藏，显示着的不激活）。"""
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    wp.flags = WPF_SETMINPOSITION
    if not user32.IsWindowVisible(hwnd):
        wp.showCmd = SW_HIDE
    elif user32.IsIconic(hwnd):
        wp.showCmd = SW_SHOWMINNOACTIVE
    else:
        wp.showCmd = SW_SHOWNOACTIVATE
    wp.ptMinPosition = wintypes.POINT(x, y)
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))


class Renderer:
    """D3D11 交换链出图（翻转模型，2 个缓冲）。每 TIMER_MS 毫秒出一帧：画面变了时先把整幅写进两个缓冲，
    之后图案模式只更新顶部的黑白码，图片模式不再写。"""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.device = wc._Device(wc._api())
        adapter, factory, chain = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        wc._check(wc._vcall(self.device.dxgi, DXGI_DEVICE_GET_ADAPTER, wc.HRESULT,
                            (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(adapter))), "GetAdapter")
        try:
            wc._check(wc._vcall(adapter.value, DXGI_OBJECT_GET_PARENT, wc.HRESULT,
                                (ctypes.POINTER(wc.GUID), ctypes.byref(IID_IDXGIFactory2)),
                                (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(factory))), "GetParent")
        finally:
            wc._release(adapter.value)
        self.factory = factory.value
        self.width, self.height = client_size(hwnd)
        desc = SWAP_CHAIN_DESC1(self.width, self.height, wc.FORMAT_BGRA8, False, 1, 0, DXGI_USAGE_RENDER_TARGET_OUTPUT,
                                2, 0, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, 0, 0)
        wc._check(wc._vcall(self.factory, FACTORY2_CREATE_SWAP_CHAIN_FOR_HWND, wc.HRESULT,
                            (ctypes.c_void_p, self.device.dev), (wintypes.HWND, hwnd),
                            (ctypes.POINTER(SWAP_CHAIN_DESC1), ctypes.byref(desc)), (ctypes.c_void_p, None),
                            (ctypes.c_void_p, None), (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(chain))),
                  "CreateSwapChainForHwnd")
        self.chain = chain.value
        self.texture = None
        self.mode = "pattern"
        self.picture: np.ndarray | None = None  # 图片模式的原图（BGRA）
        self.full: np.ndarray | None = None
        self.full_left = 0
        self.frames = 0
        self.code = 0
        self.present_hr = 0
        self._buffer()

    def _buffer(self) -> None:
        texture = ctypes.c_void_p()
        wc._check(wc._vcall(self.chain, SWAP_GET_BUFFER, wc.HRESULT, (wintypes.UINT, 0),
                            (ctypes.POINTER(wc.GUID), ctypes.byref(wc.IID_ID3D11Texture2D)),
                            (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(texture))), "GetBuffer")
        self.texture = texture.value
        self.repaint()

    def repaint(self) -> None:
        """画面变了：重新准备整幅，写进两个缓冲。"""
        if self.mode == "image" and self.picture is not None:
            pic = self.picture
            if pic.shape[1] != self.width or pic.shape[0] != self.height:
                pic = cv2.resize(pic, (self.width, self.height), interpolation=cv2.INTER_AREA)
            self.full = np.ascontiguousarray(pic)
        else:
            img = np.zeros((self.height, self.width, 4), np.uint8)
            hw, hh = self.width // 2, self.height // 2
            for (x0, y0, x1, y1), (r, g, b) in zip(
                    [(0, 0, hw, hh), (hw, 0, self.width, hh), (0, hh, hw, self.height), (hw, hh, self.width, self.height)],
                    QUAD):
                img[y0:y1, x0:x1] = (b, g, r, 255)
            self.full = img
        self.full_left = 2

    def resize_if_needed(self) -> None:
        width, height = client_size(self.hwnd)
        if (width, height) == (self.width, self.height):
            return
        wc._release(self.texture)
        self.texture = None
        wc._check(wc._vcall(self.chain, SWAP_RESIZE_BUFFERS, wc.HRESULT, (wintypes.UINT, 0), (wintypes.UINT, width),
                            (wintypes.UINT, height), (wintypes.UINT, 0), (wintypes.UINT, 0)), "ResizeBuffers")
        self.width, self.height = width, height
        self._buffer()

    def strip(self, code: int) -> np.ndarray:
        height = int(round(STRIP * user32.GetDpiForWindow(self.hwnd) / 96))
        s = np.zeros((height, self.width, 4), np.uint8)
        s[:, :, 3] = 255
        for i in range(CELLS):
            if (code >> (CELLS - 1 - i)) & 1:
                s[:, i * self.width // CELLS:(i + 1) * self.width // CELLS, :3] = 255
        return s

    def upload(self, arr: np.ndarray, box: wc.BOX | None) -> None:
        arr = np.ascontiguousarray(arr)
        wc._vcall(self.device.ctx, CONTEXT_UPDATE_SUBRESOURCE, None, (ctypes.c_void_p, self.texture), (wintypes.UINT, 0),
                  (ctypes.c_void_p, ctypes.addressof(box) if box is not None else None),
                  (ctypes.c_void_p, arr.ctypes.data), (wintypes.UINT, arr.strides[0]), (wintypes.UINT, 0))

    def render(self) -> None:
        if not user32.IsIconic(self.hwnd):  # 最小化时客户区是 0×0：不改缓冲，照常出图（模拟后台继续运行的游戏）
            self.resize_if_needed()
        if self.mode == "pattern":
            code = now_code()
            if self.full_left:
                img = self.full.copy()
                s = self.strip(code)
                img[:s.shape[0]] = s
                self.upload(img, None)
                self.full_left -= 1
            else:
                s = self.strip(code)
                self.upload(s, wc.BOX(0, 0, 0, self.width, s.shape[0], 1))
            self.code = code
        elif self.full_left:
            self.upload(self.full, None)
            self.full_left -= 1
        self.present_hr = wc._vcall(self.chain, SWAP_PRESENT, wc.HRESULT, (wintypes.UINT, 0), (wintypes.UINT, 0))
        self.frames += 1


class State:
    win = cover = None
    renderer: Renderer | None = None
    cmds: list[str] = []
    lock = threading.Lock()
    quitting = False
    frozen = False
    error: str | None = None


S = State()


def info(extra: dict | None = None) -> None:
    fg = int(user32.GetForegroundWindow() or 0)
    d = {"pid": os.getpid(), "hwnd": int(S.win), "cover": int(S.cover), "rects": rects(S.win),
         "iconic": bool(user32.IsIconic(S.win)), "dpr": user32.GetDpiForWindow(S.win) / 96,
         "fg_ours": fg in (int(S.win), int(S.cover)), "exstyle": hex(user32.GetWindowLongPtrW(S.win, -20)),
         "cover_visible": bool(user32.IsWindowVisible(S.cover))}
    r = S.renderer
    if r is not None:
        d.update(frames=r.frames, code=r.code, mode=r.mode, present_hr=r.present_hr)
    if S.error:
        d["render_error"] = S.error
    if d["cover_visible"]:
        d["cover_rects"] = rects(S.cover)
        above, h = False, S.win
        for _ in range(1000):
            h = user32.GetWindow(h, GW_HWNDPREV)
            if not h:
                break
            if int(h) == int(S.cover):
                above = True
                break
        d["cover_above"] = above
    if extra:
        d.update(extra)
    say(d)


def handle(cmd: str) -> None:
    parts = cmd.split(" ", 1)
    op = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if op == "geom":
        x, y, w, h = (int(v) for v in arg.split())
        user32.SetWindowPos(S.win, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    elif op == "cover":
        if arg == "on":
            r = rects(S.win)["window"]
            user32.SetWindowPos(S.cover, HWND_TOP, r[0], r[1], r[2] - r[0], r[3] - r[1], SWP_NOACTIVATE | SWP_SHOWWINDOW)
        else:
            user32.ShowWindow(S.cover, SW_HIDE)
    elif op == "min":
        r = rects(S.win)["window"]
        set_min_position(S.win, r[0], r[1])  # 最小化后的位置也在屏幕外
        user32.ShowWindow(S.win, SW_SHOWMINNOACTIVE)
    elif op == "restore":
        user32.ShowWindow(S.win, SW_SHOWNOACTIVATE)
    elif op in ("pattern", "image") and S.renderer is not None:
        if op == "image":
            data = np.fromfile(arg, dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"cannot read image {arg}")
            S.renderer.picture = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        S.renderer.mode = op
        S.renderer.repaint()
    elif op == "freeze":
        user32.KillTimer(S.win, 1)
        S.frozen = True
    elif op == "unfreeze":
        user32.SetTimer(S.win, 1, TIMER_MS, None)
        S.frozen = False
    elif op == "quit":
        if not S.quitting:
            S.quitting = True
            info({"op": "quit"})
            user32.DestroyWindow(S.cover)
            user32.DestroyWindow(S.win)
        return
    info({"op": cmd})


@WNDPROC
def wndproc(hwnd, msg, wp, lp):
    if msg == WM_GETMINMAXINFO:
        mm = ctypes.cast(lp, ctypes.POINTER(MINMAXINFO)).contents
        mm.ptMaxTrackSize = wintypes.POINT(MAX_TRACK, MAX_TRACK)
        return 0
    if msg == WM_TIMER and S.win is not None and hwnd == S.win:
        if S.renderer is not None:
            try:
                S.renderer.render()
            except Exception as e:  # noqa: BLE001  出图出错：记下来，info 里带上
                S.error = repr(e)
        return 0
    if msg == WM_PAINT:
        user32.ValidateRect(hwnd, None)
        return 0
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_APP:
        with S.lock:
            cmds, S.cmds = S.cmds, []
        for c in cmds:
            try:
                handle(c)
            except Exception as e:  # noqa: BLE001
                say({"op": c, "error": repr(e), "pid": os.getpid(), "hwnd": int(S.win or 0)})
        return 0
    if msg == WM_DESTROY:
        if S.win is not None and hwnd == S.win:
            user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


def reader() -> None:
    for raw in sys.stdin:
        with S.lock:
            S.cmds.append(raw.strip())
        user32.PostMessageW(S.win, WM_APP, 0, 0)
    with S.lock:
        S.cmds.append("quit")
    user32.PostMessageW(S.win, WM_APP, 0, 0)


def main() -> None:
    x, y, w, h = (int(v) for v in sys.argv[1:5])
    flags = set(sys.argv[5:])
    instance = kernel32.GetModuleHandleW(None)
    wc_ = WNDCLASSEXW()
    wc_.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc_.lpfnWndProc = wndproc
    wc_.hInstance = instance
    wc_.lpszClassName = f"ChipAdvisorCaptureTest{os.getpid()}"
    user32.RegisterClassExW(ctypes.byref(wc_))
    style = WS_POPUP if "--frameless" in flags else (WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU)
    ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if "--topmost" in flags:
        ex |= WS_EX_TOPMOST
    if "--transparent" in flags:
        ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    # 建窗口时就给屏幕外的位置，显示前后都不会出现在屏幕上
    S.win = user32.CreateWindowExW(ex, wc_.lpszClassName, "ACC capture test", style, x, y, w, h, None, None,
                                   instance, None)
    S.cover = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, wc_.lpszClassName,
                                     "ACC capture test cover", WS_POPUP, x, y, w, h, None, None, instance, None)
    set_min_position(S.win, x, y)
    set_min_position(S.cover, x, y)
    if "--plain" not in flags:
        S.renderer = Renderer(S.win)
        S.renderer.render()
        S.renderer.render()
    user32.ShowWindow(S.win, SW_SHOWNOACTIVATE)
    user32.SetTimer(S.win, 1, TIMER_MS, None)
    threading.Thread(target=reader, daemon=True).start()
    with S.lock:
        S.cmds.append("info")
    user32.PostMessageW(S.win, WM_APP, 0, 0)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
