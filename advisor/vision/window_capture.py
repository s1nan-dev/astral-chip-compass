"""直接截取游戏窗口：用 Windows 的窗口截取接口（Windows.Graphics.Capture）按窗口句柄截窗口自己画的内容。

截到的只有这个窗口自己的画面：窗口不在前台、被其他窗口遮挡都照样截得到，画面里没有盖在上面的窗口
（悬浮窗本来就不在里面，不用临时排除，也就不会让录屏闪烁）。窗口最小化时不来新帧。

能不能用只看 Windows 版本号（system_support），不读注册表、不看隐私设置：
  - 版本号 19041（Windows 10 版本 2004）起能按窗口截取，并能请求不截鼠标指针；
  - 版本号 20348 起能请求不显示黄色边框（是否显示由 Windows 决定）；更早的系统上一直显示，不发这个请求。

只用 ctypes 调系统组件（combase、d3d11），不加第三方库；不导入 Qt，不写界面句子。出错时抛 CaptureError，
只交出种类、错误代码和出错的那一步，提示的句子由界面模块拼。

做法：
  1. 建 D3D11 设备，打开它的多线程保护（帧池是自由线程的，系统会在它自己的线程里用这个设备），转成 Windows 运行时的
     IDirect3DDevice；
  2. 由窗口句柄建截取对象（GraphicsCaptureItem），建「自由线程」帧池（2 个缓冲）和截取会话；请求不截鼠标，
     能请求不显示黄框时请求，然后开始截取；
  3. 取帧：先取出排队的旧帧，再每 2 毫秒问一次新帧，最多等 wait_s 秒；帧的内容尺寸和帧池不同时（窗口大小、
     所在屏幕、缩放变了）按新尺寸重建帧池、丢弃这一帧；只把客户区那一块复制到 CPU 可读的纹理读回，直接转成 BGR。
COM 对象按虚表下标调用：每个接口先 QueryInterface，查不到就当不支持，不靠版本号猜接口布局；引用计数自己管，
每个拿到的指针用完都释放。

省 CPU 的两处（合成的 4K 数据上量，每秒 5 次取帧时合计约省单核 9%）：
  - 等显卡做完拷贝：拷贝排进显卡后让显卡做完时发信号（ID3D11Fence），用事件等，再映射。直接映射时有的显卡驱动
    在另一个线程里空转等待（4K 每帧约 1.3 毫秒 CPU）；用不了信号时照旧由映射自己等。
  - 颜色转换只用当前线程，按行分段调 cv2.cvtColor（to_bgr）：OpenCV 默认按 CPU 核数多线程转，4K 墙钟约 1.3 毫秒，
    但所有线程合计约 17～20 毫秒 CPU；分段单线程墙钟和 CPU 都约 3 毫秒。不改 OpenCV 的全局线程设置。

线程：会话在哪个线程建，就只在那个线程里取帧和关闭（D3D11 的即时上下文不能多线程同时用）。线程开头调
init_thread()（Windows 运行时的多线程模式），结束时先 close() 再 uninit_thread()。三选一显示着时快速盯绿框
（advisor/vision/pick_watch.py）在它自己的线程里另建一个会话，只拷回筹码那一块（grab 的 region）：同一个窗口可以同时有
几个会话，各自收到每一帧，和后台识别的会话互不等待、互不抢帧。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

WINDOW_BUILD = 19041  # Windows 10 版本 2004：按窗口截取，并能请求不截鼠标指针
BORDERLESS_BUILD = 20348  # 能请求不显示黄框（IGraphicsCaptureSession3）
BUFFERS = 2  # 帧池的缓冲数
POLL_S = 0.002  # 等新帧时每隔多久问一次
SAMPLES = 600  # 统计耗时、画面落后时保留最近多少次
STRIP_PIXELS = 1 << 16  # 颜色转换每段最多这么多像素：OpenCV 对一次不到这么多像素的转换不分线程（见 to_bgr）
FENCE_WAIT_MS = 500  # 等显卡做完拷贝最多等多久（毫秒）；超时后这个设备改由映射自己等
# 分步计时（WindowCapture 的 steps）每取一帧依次报的步骤名：开始取帧、等到帧、取到帧的纹理、拷贝排进显卡、
# 等拷贝做完并映射、颜色转换、解除映射、关闭帧。没有画面时只报 start、wait；这一帧丢弃时报 start、wait、close。
STEPS = ("start", "wait", "frame", "copy", "map", "convert", "unmap", "close")

# 错误代码（HRESULT，按无符号整数写）
E_ACCESSDENIED = 0x80070005
E_INVALIDARG = 0x80070057
E_NOINTERFACE = 0x80004002
REGDB_E_CLASSNOTREG = 0x80040154
RPC_E_CHANGED_MODE = 0x80010106
DXGI_ERROR_DEVICE_REMOVED = 0x887A0005
DXGI_ERROR_DEVICE_RESET = 0x887A0007

# 错误种类：unsupported 系统不支持（类未注册、不支持接口、缺系统函数）/ denied Windows 拒绝 /
# closed 窗口已关（对已关闭的句柄建截取对象）/ device_lost 显卡驱动重置（设备已移除或已重置）/ failed 其他错误
KINDS = {
    E_ACCESSDENIED: "denied",
    REGDB_E_CLASSNOTREG: "unsupported",
    E_NOINTERFACE: "unsupported",
    E_INVALIDARG: "closed",
    DXGI_ERROR_DEVICE_REMOVED: "device_lost",
    DXGI_ERROR_DEVICE_RESET: "device_lost",
}

ITEM_CLASS = "Windows.Graphics.Capture.GraphicsCaptureItem"
POOL_CLASS = "Windows.Graphics.Capture.Direct3D11CaptureFramePool"
FORMAT_BGRA8 = 87  # DirectXPixelFormat.B8G8R8A8UIntNormalized，和 DXGI_FORMAT_B8G8R8A8_UNORM 同值

# 虚表下标。IUnknown：0 QueryInterface、1 AddRef、2 Release；Windows 运行时接口另有 3～5（IInspectable）
INTEROP_CREATE_FOR_WINDOW = 3  # IGraphicsCaptureItemInterop::CreateForWindow
ITEM_GET_SIZE = 7  # IGraphicsCaptureItem::get_Size
STATICS2_CREATE_FREE_THREADED = 6  # IDirect3D11CaptureFramePoolStatics2::CreateFreeThreaded
POOL_RECREATE, POOL_TRY_GET_NEXT_FRAME, POOL_CREATE_CAPTURE_SESSION = 6, 7, 10  # IDirect3D11CaptureFramePool
SESSION_START_CAPTURE = 6  # IGraphicsCaptureSession::StartCapture
SESSION2_PUT_CURSOR = 7  # IGraphicsCaptureSession2::put_IsCursorCaptureEnabled
SESSION3_GET_BORDER, SESSION3_PUT_BORDER = 6, 7  # IGraphicsCaptureSession3::get/put_IsBorderRequired
CLOSABLE_CLOSE = 6  # IClosable::Close
FRAME_GET_SURFACE, FRAME_GET_TIME, FRAME_GET_CONTENT_SIZE = 6, 7, 8  # IDirect3D11CaptureFrame
ACCESS_GET_INTERFACE = 3  # IDirect3DDxgiInterfaceAccess::GetInterface
DEVICE_CREATE_TEXTURE2D = 5  # ID3D11Device::CreateTexture2D
DEVICE5_CREATE_FENCE = 68  # ID3D11Device5::CreateFence
CONTEXT_MAP, CONTEXT_UNMAP, CONTEXT_COPY_SUBRESOURCE_REGION = 14, 15, 46  # ID3D11DeviceContext
CONTEXT_FLUSH = 111  # ID3D11DeviceContext::Flush
CONTEXT4_SIGNAL = 147  # ID3D11DeviceContext4::Signal
FENCE_SET_EVENT_ON_COMPLETION = 9  # ID3D11Fence::SetEventOnCompletion
TEXTURE_GET_DESC = 10  # ID3D11Texture2D::GetDesc
# ID3D11Multithread（ID3D10Multithread 的布局相同）：3 Enter、4 Leave、5 SetMultithreadProtected、6 GetMultithreadProtected
MULTITHREAD_SET_PROTECTED, MULTITHREAD_GET_PROTECTED = 5, 6

D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_SDK_VERSION = 7
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1
RO_INIT_MULTITHREADED = 1
WAIT_OBJECT_0 = 0

HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    u = uuid.UUID(text)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    g.Data4[:] = list(u.bytes[8:])
    return g


IID_IGraphicsCaptureItemInterop = _guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356")
IID_IGraphicsCaptureItem = _guid("79C3F95B-31F7-4EC2-A464-632EF5D30760")
IID_IDirect3D11CaptureFramePoolStatics2 = _guid("589B103F-6BBC-5DF5-A991-02E28B3B66D5")
IID_IGraphicsCaptureSession2 = _guid("2C39AE40-7D2E-5044-804E-8B6799D4CF9E")
IID_IGraphicsCaptureSession3 = _guid("F2CDD966-22AE-5EA1-9596-3A289344C3BE")
IID_IDirect3DDxgiInterfaceAccess = _guid("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1")
IID_IDirect3DDevice = _guid("A37624AB-8D5F-4650-9D3E-9EAE3D9BC670")
IID_IClosable = _guid("30D5A829-7FA4-4026-83BB-D75BAE4EA99E")
IID_IDXGIDevice = _guid("54EC77FA-1377-44E6-8C32-88FD5F44C84C")
IID_ID3D11Texture2D = _guid("6F15AAF2-D208-4E89-9AB4-489535D34F9C")
IID_ID3D11Device5 = _guid("8FFDE202-A0E7-45DF-9E01-E837801B5EA0")
IID_ID3D11DeviceContext4 = _guid("917600DA-F58C-4C33-98D8-3E15B390FA24")
IID_ID3D11Fence = _guid("AFFDE9D1-1DF7-4BB7-8A34-0F46251DAB80")
# ID3D11Multithread 和 ID3D10Multithread 的接口编号相同（Windows SDK 的 d3d11_4.h、d3d10.h），只是向谁要不同：前者向即时上下文
# 要，后者是向设备要的老办法（见 _Device._protect）
IID_ID3D11Multithread = _guid("9B7E4E00-342C-4106-A19F-4F2704F689F0")


class SizeInt32(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_int32), ("Height", ctypes.c_int32)]


class TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT), ("MipLevels", wintypes.UINT),
                ("ArraySize", wintypes.UINT), ("Format", wintypes.UINT), ("SampleCount", wintypes.UINT),
                ("SampleQuality", wintypes.UINT), ("Usage", wintypes.UINT), ("BindFlags", wintypes.UINT),
                ("CPUAccessFlags", wintypes.UINT), ("MiscFlags", wintypes.UINT)]


class MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", wintypes.UINT), ("DepthPitch", wintypes.UINT)]


class BOX(ctypes.Structure):
    _fields_ = [("left", wintypes.UINT), ("top", wintypes.UINT), ("front", wintypes.UINT),
                ("right", wintypes.UINT), ("bottom", wintypes.UINT), ("back", wintypes.UINT)]


class OSVERSIONINFOW(ctypes.Structure):
    _fields_ = [("dwOSVersionInfoSize", wintypes.DWORD), ("dwMajorVersion", wintypes.DWORD),
                ("dwMinorVersion", wintypes.DWORD), ("dwBuildNumber", wintypes.DWORD),
                ("dwPlatformId", wintypes.DWORD), ("szCSDVersion", wintypes.WCHAR * 128)]


# ---------------------------------------------------------------- 错误


def classify(hresult: int | None) -> str:
    """错误代码 → 错误种类（见 KINDS）；没有错误代码或不认识的一律算 failed。"""
    if hresult is None:
        return "failed"
    return KINDS.get(hresult & 0xFFFFFFFF, "failed")


def hex_code(hresult: int | None) -> str:
    """错误代码的写法，例如 "0x80070005"；没有错误代码时为空。"""
    return "" if hresult is None else f"0x{hresult & 0xFFFFFFFF:08X}"


class CaptureError(OSError):
    """直接截取出错。kind：unsupported / denied / closed / device_lost / failed（见 KINDS）；
    hresult：错误代码（无符号整数），不是系统调用出的错时为 None；step：出错的那一步（接口名，例如 "CreateForWindow"）。"""

    def __init__(self, kind: str, hresult: int | None = None, step: str = ""):
        self.kind = kind
        self.hresult = None if hresult is None else hresult & 0xFFFFFFFF
        self.step = step
        code = hex_code(self.hresult)
        super().__init__(f"{step or 'WindowCapture'}: {kind}" + (f" ({code})" if code else ""))

    @property
    def code(self) -> str:
        """错误代码的写法，例如 "0x80070005"；没有时为空。"""
        return hex_code(self.hresult)


def _check(hr: int, step: str) -> None:
    if hr < 0:
        code = hr & 0xFFFFFFFF
        raise CaptureError(classify(code), code, step)


def _reason(error: BaseException) -> str:
    """出错的简短写法（诊断用）：CaptureError 写错误代码（没有时写种类），其他错误写类名。"""
    if isinstance(error, CaptureError):
        return error.code or error.kind
    return type(error).__name__


# ---------------------------------------------------------------- 系统版本


@dataclass(frozen=True)
class Support:
    """这台电脑的系统能做什么（只看版本号）。window：能直接截取游戏窗口；borderless：能请求不显示黄框；build：版本号。"""

    window: bool
    borderless: bool
    build: int


def windows_build() -> int:
    """Windows 的版本号（例如 Windows 11 25H2 为 26200）。用 RtlGetVersion 取真实版本（不受程序兼容性清单影响）。"""
    try:
        info = OSVERSIONINFOW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.RtlGetVersion.argtypes = [ctypes.POINTER(OSVERSIONINFOW)]
        ntdll.RtlGetVersion.restype = ctypes.c_long
        if ntdll.RtlGetVersion(ctypes.byref(info)) == 0 and info.dwBuildNumber:
            return int(info.dwBuildNumber)
    except (OSError, AttributeError):
        pass
    try:
        return int(sys.getwindowsversion().build)
    except AttributeError:  # 不是 Windows
        return 0


def system_support(build: int | None = None) -> Support:
    """按版本号判断（build 不给时取本机的）：19041 起能直接截取，20348 起能请求不显示黄框。"""
    build = windows_build() if build is None else int(build)
    return Support(window=build >= WINDOW_BUILD, borderless=build >= BORDERLESS_BUILD, build=build)


# ---------------------------------------------------------------- 系统函数和 COM 调用


class _Api:
    """用到的系统函数。第一次用时加载；缺 DLL 或函数时按「不支持」处理。"""

    def __init__(self):
        combase = ctypes.WinDLL("combase")
        d3d11 = ctypes.WinDLL("d3d11")
        kernel32 = ctypes.WinDLL("kernel32")
        self.RoInitialize = combase.RoInitialize
        self.RoInitialize.argtypes = [ctypes.c_int]
        self.RoInitialize.restype = HRESULT
        self.RoUninitialize = combase.RoUninitialize
        self.RoUninitialize.argtypes = []
        self.RoUninitialize.restype = None
        self.WindowsCreateString = combase.WindowsCreateString
        self.WindowsCreateString.argtypes = [wintypes.LPCWSTR, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        self.WindowsCreateString.restype = HRESULT
        self.WindowsDeleteString = combase.WindowsDeleteString
        self.WindowsDeleteString.argtypes = [ctypes.c_void_p]
        self.WindowsDeleteString.restype = HRESULT
        self.RoGetActivationFactory = combase.RoGetActivationFactory
        self.RoGetActivationFactory.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.RoGetActivationFactory.restype = HRESULT
        self.D3D11CreateDevice = d3d11.D3D11CreateDevice
        self.D3D11CreateDevice.argtypes = [
            ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
            wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT), ctypes.POINTER(ctypes.c_void_p),
        ]
        self.D3D11CreateDevice.restype = HRESULT
        self.CreateDirect3D11DeviceFromDXGIDevice = d3d11.CreateDirect3D11DeviceFromDXGIDevice
        self.CreateDirect3D11DeviceFromDXGIDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.CreateDirect3D11DeviceFromDXGIDevice.restype = HRESULT
        self.QueryPerformanceCounter = kernel32.QueryPerformanceCounter
        self.QueryPerformanceCounter.argtypes = [ctypes.POINTER(ctypes.c_int64)]
        self.QueryPerformanceCounter.restype = wintypes.BOOL
        self.CreateEventW = kernel32.CreateEventW
        self.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.CreateEventW.restype = wintypes.HANDLE
        self.CloseHandle = kernel32.CloseHandle
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL
        self.WaitForSingleObject = kernel32.WaitForSingleObject
        self.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.WaitForSingleObject.restype = wintypes.DWORD
        freq = ctypes.c_int64(0)
        kernel32.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(ctypes.c_int64)]
        kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
        self.qpc_frequency = freq.value or 1

    def qpc_seconds(self) -> float:
        """系统的高精度计时（秒）；帧的系统时间戳（SystemRelativeTime）用的是同一个时钟。"""
        now = ctypes.c_int64(0)
        self.QueryPerformanceCounter(ctypes.byref(now))
        return now.value / self.qpc_frequency


_API: _Api | None = None
_API_ERROR: CaptureError | None = None
_API_LOCK = threading.Lock()


def _api() -> _Api:
    global _API, _API_ERROR
    with _API_LOCK:
        if _API is None and _API_ERROR is None:
            try:
                _API = _Api()
            except (OSError, AttributeError):  # 缺 DLL 或函数（系统太旧）
                _API_ERROR = CaptureError("unsupported", None, "LoadLibrary")
        if _API is None:
            raise CaptureError(_API_ERROR.kind, _API_ERROR.hresult, _API_ERROR.step)
        return _API


_PROTOTYPES: dict = {}


def _vcall(ptr: int | None, index: int, restype, *args):
    """调 COM 对象虚表的第 index 个方法。args：[(ctypes 类型, 值), …]。空指针不调用（直接按出错处理，不让程序崩溃）。"""
    if not ptr:
        raise CaptureError("failed", None, "NullPointer")
    key = (restype, *(t for t, _ in args))
    proto = _PROTOTYPES.get(key)
    if proto is None:
        proto = _PROTOTYPES[key] = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *key[1:])
    vtable = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return proto(vtable[index])(ctypes.c_void_p(ptr), *(v for _, v in args))


def _query(ptr: int, iid: GUID, step: str = "QueryInterface") -> int:
    """QueryInterface：拿到接口指针（用完要 _release）；查不到时抛 CaptureError（unsupported）。"""
    out = ctypes.c_void_p()
    _check(_vcall(ptr, 0, HRESULT, (ctypes.POINTER(GUID), ctypes.byref(iid)),
                  (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out))), step)
    if not out.value:
        raise CaptureError("unsupported", E_NOINTERFACE, step)
    return out.value


def _release(ptr: int | None) -> None:
    if ptr:
        _vcall(ptr, 2, ctypes.c_ulong)


def _close_object(ptr: int | None) -> None:
    """Windows 运行时对象先 Close（IClosable）再释放；Close 出错时照样释放。"""
    if not ptr:
        return
    try:
        closable = _query(ptr, IID_IClosable)
    except CaptureError:
        closable = None
    if closable:
        try:
            _vcall(closable, CLOSABLE_CLOSE, HRESULT)
        finally:
            _release(closable)
    _release(ptr)


def _factory(api: _Api, name: str, iid: GUID) -> int:
    """取系统类的工厂（RoGetActivationFactory），返回 iid 指定的接口（用完要 _release）。"""
    text = ctypes.c_void_p()
    _check(api.WindowsCreateString(name, len(name), ctypes.byref(text)), "WindowsCreateString")
    try:
        out = ctypes.c_void_p()
        _check(api.RoGetActivationFactory(text, ctypes.byref(iid), ctypes.byref(out)), "RoGetActivationFactory")
        if not out.value:
            raise CaptureError("unsupported", REGDB_E_CLASSNOTREG, "RoGetActivationFactory")
        return out.value
    finally:
        api.WindowsDeleteString(text)


# ---------------------------------------------------------------- 线程初始化


_THREAD = threading.local()


def init_thread() -> None:
    """当前线程初始化 Windows 运行时（多线程模式）。同一线程重复调用只做一次；加载不了系统组件时什么都不做
    （之后建会话时会抛 CaptureError）。线程原来是单线程模式时照样能用，不改它。"""
    if getattr(_THREAD, "done", False):
        return
    _THREAD.done = True
    _THREAD.owned = False
    try:
        api = _api()
    except CaptureError:
        return
    hr = api.RoInitialize(RO_INIT_MULTITHREADED)
    _THREAD.owned = hr >= 0  # S_OK、S_FALSE 都要配一次 RoUninitialize；RPC_E_CHANGED_MODE 不用


def uninit_thread() -> None:
    """线程结束前调用（先关掉这个线程里的会话）：和 init_thread() 配对。"""
    if getattr(_THREAD, "owned", False):
        try:
            _api().RoUninitialize()
        except CaptureError:
            pass
    _THREAD.done = False
    _THREAD.owned = False


# ---------------------------------------------------------------- 图像


def client_box(content: tuple[int, int], offset: tuple[int, int], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """客户区在截到的画面里的位置 (左, 上, 宽, 高)。

    content：截到的画面的宽高（窗口可见边框的大小）；offset：客户区左上角相对可见边框左上角的偏移
    （带标题栏的窗口约 (2, 45)，无边框全屏为 (0, 0)）；size：客户区的宽高。都按物理像素。
    画面里放不下整个客户区（画面尺寸和窗口当前的大小不一致，例如窗口刚改了大小）时为 None：这一帧丢弃。
    """
    width, height = content
    left, top = max(0, int(offset[0])), max(0, int(offset[1]))
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0 or left + w > width or top + h > height:
        return None
    return left, top, w, h


def region_box(client: tuple[int, int, int, int], region: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """客户区里的一块在截到的画面里的位置 (左, 上, 宽, 高)。client 是 client_box 的结果；region 是这一块在客户区里的
    (左, 上, 宽, 高)。这一块不全在客户区里（或是空的）时为 None：这一帧丢弃。"""
    left, top, width, height = client
    x, y, w, h = (int(v) for v in region)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return left + x, top + y, w, h


def strip_rows(width: int) -> int:
    """颜色转换每段几行：一段不超过 STRIP_PIXELS 个像素（至少 1 行）。"""
    return max(1, STRIP_PIXELS // max(1, int(width)))


def to_bgr(bgra: np.ndarray) -> np.ndarray:
    """BGRA 转 BGR：和取前三个通道逐像素相同，结果是新的连续数组。可以直接传映射内存上按行距取的视图。

    只用当前线程：按行分段调 cv2.cvtColor，每段不超过 STRIP_PIXELS 个像素，OpenCV 对这么小的一次转换不分线程，
    逐段写进先分配好的结果里。4K 墙钟和 CPU 都约 3 毫秒；整幅一次转换时 OpenCV 按 CPU 核数多线程转，墙钟约 1.3 毫秒，
    所有线程合计的 CPU 却有 17～20 毫秒（各线程大半时间在等内存和互相等待）。numpy 切片复制约 23 毫秒。"""
    import cv2

    height, width = bgra.shape[:2]
    out = np.empty((height, width, 3), np.uint8)
    rows = strip_rows(width)
    for top in range(0, height, rows):
        part, target = bgra[top:top + rows], out[top:top + rows]
        try:
            got = cv2.cvtColor(part, cv2.COLOR_BGRA2BGR, dst=target)
        except cv2.error:  # OpenCV 不认这种内存排列：先复制成连续的再转
            got = cv2.cvtColor(np.ascontiguousarray(part), cv2.COLOR_BGRA2BGR, dst=target)
        if got is not target:  # 没有写进给它的那一段（另给了新数组）：复制过去
            target[...] = got
    return out


def _percentile(values, q: float) -> float | None:
    values = list(values)
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values, dtype=float), q)), 2)


# ---------------------------------------------------------------- D3D11 设备


class _Device:
    """D3D11 设备、即时上下文、读回用的纹理（按大小缓存），以及等拷贝做完用的信号（ID3D11Fence）和事件。

    copy_wait：怎么等显卡做完拷贝——"fence"：拷贝后让显卡做完时发信号，用事件等（不占 CPU）；用不了时是原因
    （错误代码或种类，等太久为 "timeout"），这时由 Map 自己等。notes 给了时也写进 notes["copy_wait"]（诊断用）。
    multithread：设备的多线程保护开上了为 True；开不了时是原因（错误代码或种类，见 _protect）。notes 给了时也写进
    notes["multithread"]：开不了时由界面模块记进错误记录（advisor/ui/screen.py 的 _log_unprotected）。"""

    def __init__(self, api: _Api, notes: dict | None = None):
        self.api = api
        self.notes = notes
        self.dev = self.ctx = self.dxgi = None
        self.fence = self.ctx4 = self.event = None
        self.fence_value = 0
        self.copy_wait = "map"
        self.multithread: bool | str = False
        self.staging: tuple[int, int, int, int] | None = None  # (指针, 宽, 高, 格式)
        dev, ctx, level = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.UINT(0)
        hr = api.D3D11CreateDevice(None, D3D_DRIVER_TYPE_HARDWARE, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT, None, 0,
                                   D3D11_SDK_VERSION, ctypes.byref(dev), ctypes.byref(level), ctypes.byref(ctx))
        self.dev, self.ctx, self.level = dev.value, ctx.value, int(level.value)
        try:
            _check(hr, "D3D11CreateDevice")
            if not self.dev or not self.ctx:
                raise CaptureError("failed", None, "D3D11CreateDevice")
            self.dxgi = _query(self.dev, IID_IDXGIDevice)
            self._protect()
            self._open_fence()
        except BaseException:
            self.close()
            raise

    def _protect(self) -> None:
        """打开设备的多线程保护（SetMultithreadProtected）：帧池是自由线程的，系统会在它自己的线程里用这个设备，打开以后
        D3D 给即时上下文的调用加锁，换哪种显卡驱动都不会有两个线程同时用它；代价只是每次调用多一次加锁。先向即时上下文要
        ID3D11Multithread，要不到再向设备要（ID3D10Multithread 的老办法，接口编号相同）。都要不到或调用出错时照常往下走
        （不算出错、不影响截取）。结果记在 multithread（notes 给了时也写进 notes["multithread"]）。"""
        result: bool | str = "failed"
        for owner in (self.ctx, self.dev):
            try:
                multithread = _query(owner, IID_ID3D11Multithread, "Multithread")
            except Exception as e:  # noqa: BLE001  要不到就向下一个要
                result = _reason(e)
                continue
            try:
                # 返回的是原来开没开（BOOL），不是出错代码
                _vcall(multithread, MULTITHREAD_SET_PROTECTED, wintypes.BOOL, (wintypes.BOOL, True))
            except Exception as e:  # noqa: BLE001  调用出错：照常往下走
                result = _reason(e)
                continue
            finally:
                _release(multithread)
            result = True
            break
        self.multithread = result
        if self.notes is not None:
            self.notes["multithread"] = result

    def _set_copy_wait(self, value: str) -> None:
        self.copy_wait = value
        if self.notes is not None:
            self.notes["copy_wait"] = value

    def _open_fence(self) -> None:
        """建等拷贝用的信号和事件（ID3D11Device5::CreateFence，Windows 10 版本 1703 起）。查不到接口或建不了时
        记下原因，照旧由 Map 等，不算出错。"""
        dev5 = None
        try:
            dev5 = _query(self.dev, IID_ID3D11Device5)
            fence = ctypes.c_void_p()
            _check(_vcall(dev5, DEVICE5_CREATE_FENCE, HRESULT, (ctypes.c_uint64, 0), (wintypes.UINT, 0),
                          (ctypes.POINTER(GUID), ctypes.byref(IID_ID3D11Fence)),
                          (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(fence))), "CreateFence")
            self.fence = fence.value
            if not self.fence:
                raise CaptureError("failed", None, "CreateFence")
            self.ctx4 = _query(self.ctx, IID_ID3D11DeviceContext4)
            self.event = self.api.CreateEventW(None, False, False, None)  # 自动复位：每次等到就复位
            if not self.event:
                raise CaptureError("failed", None, "CreateEvent")
            self._set_copy_wait("fence")
        except Exception as e:  # noqa: BLE001  只是省 CPU 的办法：用不了就由 Map 等，不算出错
            self._drop_fence(_reason(e))
        finally:
            _release(dev5)

    def _free_fence(self) -> None:
        fence, ctx4, event = self.fence, self.ctx4, self.event
        self.fence = self.ctx4 = self.event = None
        _release(fence)
        _release(ctx4)
        if event:
            self.api.CloseHandle(event)

    def _drop_fence(self, reason: str) -> None:
        """不再用信号等拷贝（改由 Map 等）：释放信号、上下文接口，关掉事件，记下原因。"""
        self._free_fence()
        self._set_copy_wait(reason)

    def _wait_copy(self) -> None:
        """拷贝排进显卡之后：让显卡做完时发信号、提交，用事件等到做完（等的时候不占 CPU），之后 Map 不用再等。
        出错或等了 FENCE_WAIT_MS 还没做完时，这个设备不再用这个办法，交给 Map 等（设备出错时由 Map 报出来）。"""
        if self.fence is None:
            return
        self.fence_value += 1
        try:
            _check(_vcall(self.ctx4, CONTEXT4_SIGNAL, HRESULT, (ctypes.c_void_p, self.fence),
                          (ctypes.c_uint64, self.fence_value)), "Signal")
            _vcall(self.ctx, CONTEXT_FLUSH, None)
            _check(_vcall(self.fence, FENCE_SET_EVENT_ON_COMPLETION, HRESULT, (ctypes.c_uint64, self.fence_value),
                          (wintypes.HANDLE, self.event)), "SetEventOnCompletion")
        except Exception as e:  # noqa: BLE001  交给 Map 等
            self._drop_fence(_reason(e))
            return
        if self.api.WaitForSingleObject(self.event, FENCE_WAIT_MS) != WAIT_OBJECT_0:
            self._drop_fence("timeout")

    def _staging(self, width: int, height: int, fmt: int) -> int:
        if self.staging is not None and self.staging[1:] == (width, height, fmt):
            return self.staging[0]
        if self.staging is not None:
            _release(self.staging[0])
            self.staging = None
        desc = TEXTURE2D_DESC(width, height, 1, 1, fmt, 1, 0, D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ, 0)
        out = ctypes.c_void_p()
        _check(_vcall(self.dev, DEVICE_CREATE_TEXTURE2D, HRESULT, (ctypes.POINTER(TEXTURE2D_DESC), ctypes.byref(desc)),
                      (ctypes.c_void_p, None), (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out))), "CreateTexture2D")
        if not out.value:
            raise CaptureError("failed", None, "CreateTexture2D")
        self.staging = (out.value, width, height, fmt)
        return out.value

    def read_bgr(self, texture: int, box: tuple[int, int, int, int], step=None) -> np.ndarray:
        """把纹理里的一块 box=(左, 上, 宽, 高) 读回成 (高, 宽, 3) 的 BGR 数组。step 给了时在拷贝、映射、转换、
        解除映射之后各调用一次 step(步骤名)（分步计时，见 STEPS）。"""
        left, top, width, height = box
        desc = TEXTURE2D_DESC()
        _vcall(texture, TEXTURE_GET_DESC, None, (ctypes.POINTER(TEXTURE2D_DESC), ctypes.byref(desc)))
        if desc.Format != FORMAT_BGRA8:
            raise CaptureError("failed", None, "TextureFormat")
        staging = self._staging(width, height, desc.Format)
        region = BOX(left, top, 0, left + width, top + height, 1)
        _vcall(self.ctx, CONTEXT_COPY_SUBRESOURCE_REGION, None, (ctypes.c_void_p, staging), (wintypes.UINT, 0),
               (wintypes.UINT, 0), (wintypes.UINT, 0), (wintypes.UINT, 0), (ctypes.c_void_p, texture), (wintypes.UINT, 0),
               (ctypes.POINTER(BOX), ctypes.byref(region)))
        if step is not None:
            step("copy")
        self._wait_copy()
        mapped = MAPPED_SUBRESOURCE()
        _check(_vcall(self.ctx, CONTEXT_MAP, HRESULT, (ctypes.c_void_p, staging), (wintypes.UINT, 0),
                      (wintypes.UINT, D3D11_MAP_READ), (wintypes.UINT, 0),
                      (ctypes.POINTER(MAPPED_SUBRESOURCE), ctypes.byref(mapped))), "Map")
        if step is not None:
            step("map")
        try:
            if not mapped.pData:
                raise CaptureError("failed", None, "Map")
            pitch = int(mapped.RowPitch)
            buffer = (ctypes.c_ubyte * (pitch * (height - 1) + width * 4)).from_address(mapped.pData)
            view = np.ndarray((height, width, 4), np.uint8, buffer=buffer, strides=(pitch, 4, 1))
            img = to_bgr(view)  # 转换时直接读映射的内存，结果是新的数组，Unmap 之后照样能用
            if step is not None:
                step("convert")
        finally:
            _vcall(self.ctx, CONTEXT_UNMAP, None, (ctypes.c_void_p, staging), (wintypes.UINT, 0))
        if step is not None:
            step("unmap")
        return img

    def close(self) -> None:
        if self.staging is not None:
            _release(self.staging[0])
            self.staging = None
        self._free_fence()
        for name in ("dxgi", "ctx", "dev"):
            ptr = getattr(self, name, None)
            setattr(self, name, None)
            _release(ptr)


# ---------------------------------------------------------------- 截取会话


class WindowCapture:
    """按窗口句柄直接截取一个窗口。建好就开始截取；用 grab() 按需取一帧，不用时 close()。

    失败时抛 CaptureError。一律请求不截鼠标指针；borderless 为 True 时（系统版本号 20348 起）再查询
    IGraphicsCaptureSession3，查到才请求不显示黄框，border_requested 记下请求是否发出并被接受。
    notes 记录这两项请求的结果和读回值，以及怎么等显卡做完拷贝（copy_wait，见 _Device；诊断用）。
    steps：分步计时（实机测试用），不为 None 时每取一帧在各步结束时调用 steps(步骤名)，步骤名见 STEPS。
    """

    def __init__(self, hwnd: int, *, borderless: bool, steps=None):
        self.hwnd = int(hwnd or 0)
        self.border_requested = False
        self.notes: dict = {}
        self.steps = steps
        self.device: _Device | None = None
        self.rt_device = self.item = self.pool = self.session = None
        self.size = (0, 0)  # 帧池的尺寸（截取对象的大小，物理像素）
        self.last_age_s: float | None = None  # 最近一帧画面落后多少秒
        self.setup_s = 0.0
        self._lock = threading.Lock()
        self._ms: deque[float] = deque(maxlen=SAMPLES)
        self._ages: deque[float] = deque(maxlen=SAMPLES)
        self._counts = {"frames": 0, "empty": 0, "dropped": 0, "recreated": 0, "skipped": 0}
        start = time.perf_counter()
        self._api = _api()
        init_thread()
        try:
            self._open(borderless)
        except BaseException:
            self.close()
            raise
        self.setup_s = round(time.perf_counter() - start, 4)

    def _open(self, borderless: bool) -> None:
        api = self._api
        self.device = _Device(api, self.notes)
        inspectable = ctypes.c_void_p()
        _check(api.CreateDirect3D11DeviceFromDXGIDevice(self.device.dxgi, ctypes.byref(inspectable)),
               "CreateDirect3D11DeviceFromDXGIDevice")
        try:
            self.rt_device = _query(inspectable.value, IID_IDirect3DDevice)
        finally:
            _release(inspectable.value)
        interop = _factory(api, ITEM_CLASS, IID_IGraphicsCaptureItemInterop)
        try:
            item = ctypes.c_void_p()
            _check(_vcall(interop, INTEROP_CREATE_FOR_WINDOW, HRESULT, (wintypes.HWND, self.hwnd),
                          (ctypes.POINTER(GUID), ctypes.byref(IID_IGraphicsCaptureItem)),
                          (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(item))), "CreateForWindow")
            self.item = item.value
        finally:
            _release(interop)
        if not self.item:
            raise CaptureError("failed", None, "CreateForWindow")
        size = SizeInt32()
        _check(_vcall(self.item, ITEM_GET_SIZE, HRESULT, (ctypes.POINTER(SizeInt32), ctypes.byref(size))), "Size")
        self.size = (int(size.Width), int(size.Height))
        statics = _factory(api, POOL_CLASS, IID_IDirect3D11CaptureFramePoolStatics2)
        try:
            pool = ctypes.c_void_p()
            _check(_vcall(statics, STATICS2_CREATE_FREE_THREADED, HRESULT, (ctypes.c_void_p, self.rt_device),
                          (ctypes.c_int32, FORMAT_BGRA8), (ctypes.c_int32, BUFFERS), (SizeInt32, size),
                          (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(pool))), "CreateFreeThreaded")
            self.pool = pool.value
        finally:
            _release(statics)
        if not self.pool:
            raise CaptureError("failed", None, "CreateFreeThreaded")
        session = ctypes.c_void_p()
        _check(_vcall(self.pool, POOL_CREATE_CAPTURE_SESSION, HRESULT, (ctypes.c_void_p, self.item),
                      (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(session))), "CreateCaptureSession")
        self.session = session.value
        if not self.session:
            raise CaptureError("failed", None, "CreateCaptureSession")
        self._cursor_off()
        if borderless:
            self._request_borderless()
        _check(_vcall(self.session, SESSION_START_CAPTURE, HRESULT), "StartCapture")

    def _cursor_off(self) -> None:
        """请求不截鼠标指针（Windows 10 版本 2004 起）。查不到接口或设置失败时只记下，照常截取。"""
        try:
            session2 = _query(self.session, IID_IGraphicsCaptureSession2)
        except CaptureError as e:
            self.notes["cursor"] = e.code or e.kind
            return
        try:
            _check(_vcall(session2, SESSION2_PUT_CURSOR, HRESULT, (ctypes.c_bool, False)), "IsCursorCaptureEnabled")
            self.notes["cursor"] = "off"
        except CaptureError as e:
            self.notes["cursor"] = e.code or e.kind
        finally:
            _release(session2)

    def _request_borderless(self) -> None:
        """请求不显示黄框：查不到 IGraphicsCaptureSession3（Windows 10）就不设；设置失败只记下，照常截取。"""
        try:
            session3 = _query(self.session, IID_IGraphicsCaptureSession3)
        except CaptureError as e:
            self.notes["border"] = e.code or e.kind
            return
        try:
            _check(_vcall(session3, SESSION3_PUT_BORDER, HRESULT, (ctypes.c_bool, False)), "IsBorderRequired")
            self.border_requested = True
            self.notes["border"] = "requested"
            value = ctypes.c_bool(True)
            if _vcall(session3, SESSION3_GET_BORDER, HRESULT, (ctypes.POINTER(ctypes.c_bool), ctypes.byref(value))) >= 0:
                self.notes["border_required"] = bool(value.value)  # 读回值：Windows 是否仍要显示黄框
        except CaptureError as e:
            self.notes["border"] = e.code or e.kind
        finally:
            _release(session3)

    # ------------------------------------------------------------ 取帧

    def _next(self) -> int | None:
        out = ctypes.c_void_p()
        _check(_vcall(self.pool, POOL_TRY_GET_NEXT_FRAME, HRESULT, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out))),
               "TryGetNextFrame")
        return out.value or None

    def _skip(self, frame: int | None) -> None:
        """关掉一帧没用上的旧帧（有更新的一帧了），记一次 skipped。"""
        if frame:
            _close_object(frame)
            self._count("skipped")

    def _drain(self, frame: int | None) -> int | None:
        """把排队的帧都取出来，只留最新的一帧（其余关闭、释放）。出错时也不漏放。"""
        while True:
            try:
                newer = self._next()
            except BaseException:
                _close_object(frame)
                raise
            if newer is None:
                return frame
            self._skip(frame)
            frame = newer

    def _latest(self, wait_s: float) -> int | None:
        """先取出排队的旧帧，再最多等 wait_s 秒要一帧新的（每 POLL_S 问一次）；等不到时用排队里最新的那一帧。

        帧池排满后系统不再送帧，排队里的帧可能是很久以前的；取空以后系统会送来最新的画面。"""
        frame = self._drain(None)
        deadline = time.perf_counter() + max(0.0, wait_s)
        while time.perf_counter() < deadline:
            try:
                newer = self._next()
            except BaseException:
                _close_object(frame)
                raise
            if newer is not None:
                self._skip(frame)
                return self._drain(newer)
            time.sleep(POLL_S)
        return frame

    def _step(self, name: str) -> None:
        if self.steps is not None:
            self.steps(name)

    def grab(self, offset: tuple[int, int], size: tuple[int, int], wait_s: float = 0.05,
             region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
        """取一帧，返回客户区的 BGR 图 (高, 宽, 3)。

        offset：客户区左上角相对窗口可见边框左上角的偏移；size：客户区的宽高（物理像素，见 client_box）。
        region：只要客户区里的一块 (左, 上, 宽, 高) 时给（三选一时快速盯绿框只截筹码那一带，见 advisor/vision/pick_watch.py）：
        只把这一块拷回、转成 BGR，返回 (高, 宽, 3) 的这一块；这一块不全在客户区里时这一帧丢弃（返回 None）。
        暂时没有画面（刚开始截取、窗口最小化、窗口大小刚变了这一帧）时返回 None；出错抛 CaptureError。"""
        if not self.pool:
            raise CaptureError("failed", None, "Closed")
        start = time.perf_counter()
        self._step("start")
        frame = self._latest(wait_s)
        self._step("wait")
        if frame is None:
            self._count("empty")
            return None
        try:
            img = self._read(frame, offset, size) if region is None else self._read(frame, offset, size, region)
        finally:
            _close_object(frame)
        self._step("close")
        if img is None:
            self._count("empty")
            return None
        with self._lock:
            self._counts["frames"] += 1
            self._ms.append((time.perf_counter() - start) * 1000)
            if self.last_age_s is not None:
                self._ages.append(self.last_age_s * 1000)
        return img

    def _read(self, frame: int, offset: tuple[int, int], size: tuple[int, int],
              region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
        content = SizeInt32()
        _check(_vcall(frame, FRAME_GET_CONTENT_SIZE, HRESULT, (ctypes.POINTER(SizeInt32), ctypes.byref(content))),
               "ContentSize")
        stamp = ctypes.c_int64(0)
        _check(_vcall(frame, FRAME_GET_TIME, HRESULT, (ctypes.POINTER(ctypes.c_int64), ctypes.byref(stamp))),
               "SystemRelativeTime")
        self.last_age_s = max(0.0, self._api.qpc_seconds() - stamp.value / 1e7)
        width, height = int(content.Width), int(content.Height)
        if (width, height) != self.size:
            # 窗口大小、所在屏幕或缩放变了：按新尺寸重建帧池，这一帧丢弃
            _check(_vcall(self.pool, POOL_RECREATE, HRESULT, (ctypes.c_void_p, self.rt_device),
                          (ctypes.c_int32, FORMAT_BGRA8), (ctypes.c_int32, BUFFERS), (SizeInt32, content)), "Recreate")
            self.size = (width, height)
            self._count("recreated")
            self._count("dropped")
            return None
        box = client_box((width, height), offset, size)
        if box is not None and region is not None:
            box = region_box(box, region)
        if box is None:  # 画面里放不下客户区（或要的那一块）：这一帧和窗口当前的大小对不上
            self._count("dropped")
            return None
        surface = ctypes.c_void_p()
        _check(_vcall(frame, FRAME_GET_SURFACE, HRESULT, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(surface))),
               "Surface")
        try:
            if not surface.value:
                raise CaptureError("failed", None, "Surface")
            access = _query(surface.value, IID_IDirect3DDxgiInterfaceAccess)
        finally:
            _release(surface.value)
        texture = ctypes.c_void_p()
        try:
            _check(_vcall(access, ACCESS_GET_INTERFACE, HRESULT, (ctypes.POINTER(GUID), ctypes.byref(IID_ID3D11Texture2D)),
                          (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(texture))), "GetInterface")
        finally:
            _release(access)
        try:
            if not texture.value:
                raise CaptureError("failed", None, "GetInterface")
            self._step("frame")
            return self.device.read_bgr(texture.value, box, self.steps)
        finally:
            _release(texture.value)

    def _count(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    @property
    def stats(self) -> dict:
        """取帧统计：frames 取到的帧数、empty 没有画面的次数（含丢弃的）、dropped 丢弃的帧数、recreated 重建帧池的次数、
        skipped 取出后有更新的一帧、没用上就关掉的旧帧数、ms_p50 / ms_p95 每次取帧的耗时（毫秒）、age_ms_p95 画面落后
        （毫秒）、setup_s 建会话用了多久。"""
        with self._lock:
            out = dict(self._counts)
            ms, ages = list(self._ms), list(self._ages)
        out.update(ms_p50=_percentile(ms, 50), ms_p95=_percentile(ms, 95), age_ms_p95=_percentile(ages, 95),
                   setup_s=self.setup_s)
        return out

    def close(self) -> None:
        """停止截取、释放全部对象。可以重复调用。"""
        session, pool, item, rt_device, device = self.session, self.pool, self.item, self.rt_device, self.device
        self.session = self.pool = self.item = self.rt_device = self.device = None
        for ptr in (session, pool):
            try:
                _close_object(ptr)
            except CaptureError:
                pass
        for ptr in (item, rt_device):
            try:
                _release(ptr)
            except CaptureError:
                pass
        if device is not None:
            device.close()


# ---------------------------------------------------------------- 自检


def component_check(timeout: float = 15.0) -> dict:
    """发行版自检「窗口截取组件」：版本号、取得两个系统类的工厂、建显卡设备并转成 Windows 运行时的设备。
    结果里另有 copy_wait（等显卡做完拷贝的办法，见 _Device；用不了信号不算不通过）和 multithread（设备的多线程保护开上
    没有，开不了时是原因；同样不算不通过）。

    不建截取会话，所以不出黄框、不在系统里留使用记录。在单独的线程里做（不改调用线程的初始化方式）。
    不通过时抛 CaptureError（系统版本不够时 kind 为 unsupported）。"""
    support = system_support()
    result: dict = {"build": support.build, "window": support.window, "borderless": support.borderless}
    if not support.window:
        raise CaptureError("unsupported", None, f"WindowsBuild{support.build}")
    box: dict = {}

    def work() -> None:
        init_thread()
        try:
            api = _api()
            device = _Device(api)
            try:
                inspectable = ctypes.c_void_p()
                _check(api.CreateDirect3D11DeviceFromDXGIDevice(device.dxgi, ctypes.byref(inspectable)),
                       "CreateDirect3D11DeviceFromDXGIDevice")
                try:
                    _release(_query(inspectable.value, IID_IDirect3DDevice))
                finally:
                    _release(inspectable.value)
                _release(_factory(api, ITEM_CLASS, IID_IGraphicsCaptureItemInterop))
                _release(_factory(api, POOL_CLASS, IID_IDirect3D11CaptureFramePoolStatics2))
                box["feature_level"] = hex(device.level)
                box["copy_wait"] = device.copy_wait
                box["multithread"] = device.multithread
            finally:
                device.close()
        except BaseException as e:  # noqa: BLE001  交给调用的线程抛出
            box["error"] = e
        finally:
            uninit_thread()

    thread = threading.Thread(target=work, name="window-capture-check", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise CaptureError("failed", None, "Timeout")
    if "error" in box:
        raise box["error"]
    result["feature_level"] = box.get("feature_level")
    result["copy_wait"] = box.get("copy_wait")
    result["multithread"] = box.get("multithread")
    return result
