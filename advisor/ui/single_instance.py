"""同一个 Windows 登录会话里只运行一个拾星助手（防多开；已在运行的那个没有响应时怎么办见下）。

同时开着两个时，每个都只在启动时读一次设置和方案文件，之后每次改动都把自己手上的整份写回去，一边的改动会被另一边下一次
保存覆盖；热键只有先登记的那个能用，识别也做两遍。悬浮窗的 × 只是收到系统托盘，用户再双击启动时最容易开出第二个。

开发版（python -m advisor.ui）和发行版（ACC.exe）都从 advisor.ui.run() 启动，在导入悬浮窗（PySide6）之前调用 claim()：
判断出已有一个在运行时退得快，不弹窗、不出托盘图标、不建识别记录、不写设置和方案，也不写错误记录（已在运行的那个没有
响应时除外，见下）。

- 锁：Local\\ 下的命名互斥体（Local\\ 是每个登录会话一份的命名空间）。开发版和发行版用同一组名字（NAMES），互相拦。
  拿到锁的进程把句柄留到进程结束，由系统关掉：正常退出、程序崩溃、被任务管理器结束都一样，不会留下残留的锁。只看互斥体
  在不在，不用它的「拥有」。不用设置目录里的锁文件：开发版和发行版的设置目录不同，拦不住；进程被强行结束时还会留下锁文件。
- 「显示」请求：Local\\ 下两个自动复位的命名事件「显示」「已显示」，拿到锁的进程马上建好。悬浮窗建好后 listen() 起一个
  后台线程等「显示」，等到了调用回调（悬浮窗经 Qt 信号交给界面线程，和点托盘图标一样显示出来）；界面线程显示完后调用
  acknowledge() 触发「已显示」。「显示」在悬浮窗建好之前被触发也没关系：自动复位的事件一直保持触发状态，等待线程开始等
  的时候马上处理。
- 记着已在运行的是谁：拿到锁的进程接着把自己的进程号、创建时间和拿到锁的时刻写进 Local\\ 下的一块命名共享内存
  （Names.owner，格式 OWNER_RECORD），句柄同样留到进程结束、由系统关掉：进程不论怎么结束，这块共享内存都随之消失，不留
  残留。再次启动的进程靠它查出已在运行的是谁、开了多久（_find_owner）。创建时间防进程号被别的进程重用：只认进程号和创建
  时间都对得上的那个。开了多久按拿到锁的时刻算：结束了没有响应的那个以后接手的进程，弹窗等用户点的那段时间不算（它拿到锁
  以后才开始加载悬浮窗）。标记不对（格式不同的旧记录）时按查不到处理。
- 再次启动的进程拿不到锁时（start）：清掉「已显示」上没人等到的旧回应，允许别的进程把窗口放到前台（用户刚启动的进程才有
  这个权限，交给已在运行的那个用），触发「显示」，再等「已显示」，等到了就退出。已在运行的那个刚启动、悬浮窗还没建好，
  或者正在退出时等不到回应：每隔 POLL_S 秒再试一次拿锁，锁在这期间释放了就照常启动，合计 WAIT_S 秒还没有就按下一条处理，
  新启动的不会一直挂着。
- 等满 WAIT_S 秒既没回应、锁也没释放（_unanswered）：先卡住的那个（还没显示出悬浮窗就卡住、界面卡死、退出时
  收尾卡住）以后每次双击都没反应，要能不开任务管理器就从这里出来，又不能误伤正常的那个。
  - 查得到它、它拿到锁还不满 STUCK_S 秒：刚开、还在加载，不算卡住，照旧安静退出（它加载完会处理还挂着的「显示」请求）。
  - 查得到、拿到锁满了 STUCK_S 秒：弹窗问用户（stuck_prompt；PySide6 只在这条慢路上导入，这时建的 QApplication 留给悬浮窗
    接着用）。点「取消」：退出。点「结束它并重新打开」：先再查一次它是不是回应了（问的这段时间里回应了，或者再请它显示
    一次、RECHECK_S 秒内回应了），回应了就不结束、直接退出；没回应就结束它（_end：打开进程以后先核对创建时间，再用同一个
    句柄 TerminateProcess，只结束核对过的那个），等它真的结束、锁释放后拿下锁，照常启动，拿不到就照旧退出；结束不了（权限
    不够）时弹说明窗。结束它会丢掉它还没存的改动：设置、方案都是先写临时文件再替换的，最多丢最近一次改动。
  - 它以管理员身份运行（锁、共享内存或进程打不开）、查不到（更早带防多开的版本没有这块共享内存；共享内存打不开；进程对不
    上）：弹只有「确定」的说明窗，请用户在任务管理器中结束它，不替用户结束。
  - 弹窗（问的窗、说明窗）显示期间每隔 DIALOG_POLL_S 秒试一次拿锁（_Released）：它自己退出了（正常退出时收尾超过 WAIT_S
    秒、缓过来以后退出、用户照说明窗在任务管理器里结束了它），拿到锁就关掉弹窗、照常启动，不用用户再双击一次。
  结束了它（请系统结束以后没等到它退出时另记一笔）、结束不了、处理时出错，都记进错误记录。

不用 QLocalServer、QLocalSocket：发行版不带 PySide6.QtNetwork（tools/build_release.py 的 NOFOLLOW），用了会在发行版里
起不来，开发版里又看不出来。
非 Windows、ctypes 用不了、建互斥体出错（「已存在」「拒绝访问」以外的错误）时不拦，照常启动。以管理员身份运行的那个建的
互斥体、事件和共享内存，普通权限的进程都打不开（拒绝访问）：按已有一个在运行处理，请不动它显示；也查不到它是谁、开了
多久，所以等满 WAIT_S 秒后一律按上面一条弹「以管理员身份运行」的说明窗，它刚启动、还在加载时也一样。0.12.0 及以前的
版本没有这项检查，管不到。
"""
from __future__ import annotations

import ctypes
import struct
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from ..paths import FOLDER_NAME

WAIT_S = 2.0  # 再次启动的进程最多等多久：等已在运行的那个回应、等锁释放，合计
POLL_S = 0.1  # 等的时候每隔多久再试一次拿锁
STOP_S = 2.0  # 停下等待线程时最多等多久
STUCK_S = 15.0  # 已在运行的那个拿到锁满这么多秒还不回应，才算卡住、问用户；刚开、还在加载的不算
RECHECK_S = 1.0  # 点了「结束它并重新打开」以后，再请它显示一次、最多等它回应这么多秒（回应了就不结束）
END_WAIT_S = 5.0  # 结束卡住的那个以后，最多等它真的结束这么多秒
TAKE_S = 2.0  # 它结束以后，最多等锁释放这么多秒
ENDED_CODE = 1  # 结束卡住的那个时给它的退出码
DIALOG_POLL_S = 0.2  # 弹窗显示期间每隔多久试一次拿锁：它自己退出了就关掉弹窗、照常启动

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PAGE_READWRITE = 0x04
FILE_MAP_WRITE = 0x0002
FILE_MAP_READ = 0x0004
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
ASFW_ANY = 0xFFFFFFFF  # AllowSetForegroundWindow 的 (DWORD)-1：允许任何进程

# 共享内存里记的：标记（认得出是这个格式）、进程号、进程的创建时间（GetProcessTimes）、拿到锁的时刻
# （GetSystemTimeAsFileTime）。两个时间都是 FILETIME：1601 年起的 100 纳秒数，UTC。改了格式就换标记：读到别的标记（例如
# 只记进程号和创建时间的 ACC1）按查不到处理
OWNER_RECORD = struct.Struct("<4sIQQ")
OWNER_MAGIC = b"ACC2"
FILETIME_PER_S = 10_000_000

# 说明窗（只有「确定」）的几种，文字在 stuck_prompt
UNKNOWN = "unknown"  # 查不到已在运行的是谁
ELEVATED = "elevated"  # 它以管理员身份运行等，打不开
CANNOT_END = "cannot_end"  # 点了「结束它并重新打开」，结束不了

# _end 的结果：结束了；已请系统结束它、END_WAIT_S 秒内没等到它退出；它已经不在了（自己退出了，或者进程号已被别的进程
# 重用），没有结束谁；打不开、结束不了
ENDED, NOT_EXITED, GONE, DENIED = "ended", "not_exited", "gone", "denied"


@dataclass(frozen=True)
class Names:
    """一组内核对象的名字：互斥体（锁）、「显示」事件、「已显示」事件，和记着拿锁的是谁的共享内存（owner）。
    这几种对象共用一个命名空间，名字各不相同。owner 为空时不记、不查（只给前三个名字时）。"""

    mutex: str
    show: str
    shown: str
    owner: str = ""

    @classmethod
    def under(cls, base: str) -> Names:
        return cls(f"{base}.Overlay", f"{base}.Show", f"{base}.Shown", f"{base}.Owner")


# 正式的名字，只在 claim()（run() 调用）和 running()（打包脚本调用）里用。测试一律另起带随机后缀的名字：用户可能正开着
# 拾星助手，用了正式的名字，测试会把用户的悬浮窗弹到前面，或者被用户的那个拦住，甚至问要不要结束用户的那个。
# 名字和用法（两个事件都自动复位，「显示」由新启动的那个触发、「已显示」由已在运行的那个触发）定了以后不能再改：
# 改了以后新旧版本互相拦不住、请不动对方显示（同 paths.FOLDER_NAME）。owner 的格式同样不能再改；
# 更早带防多开的版本没有这块共享内存，再次启动的进程按「查不到」处理
NAMES = Names.under(f"Local\\{FOLDER_NAME}")


class _Win32:
    """用到的几个 kernel32、user32 函数。用单独的 WinDLL 实例声明参数类型（句柄按指针传），不影响别处的 ctypes.windll；
    use_last_error：调用前把错误代码清零，调用后马上取 ctypes.get_last_error()。"""

    def __init__(self) -> None:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        handle = wintypes.HANDLE
        filetime = ctypes.POINTER(wintypes.FILETIME)
        for fn, args, result in (
            (kernel32.CreateMutexW, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], handle),
            (kernel32.OpenMutexW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], handle),
            (kernel32.CreateEventW, [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], handle),
            (kernel32.OpenEventW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], handle),
            (kernel32.SetEvent, [handle], wintypes.BOOL),
            (kernel32.ResetEvent, [handle], wintypes.BOOL),
            (kernel32.WaitForSingleObject, [handle, wintypes.DWORD], wintypes.DWORD),
            (kernel32.WaitForMultipleObjects, [wintypes.DWORD, ctypes.POINTER(handle), wintypes.BOOL, wintypes.DWORD],
             wintypes.DWORD),
            (kernel32.CloseHandle, [handle], wintypes.BOOL),
            (kernel32.CreateFileMappingW, [handle, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.LPCWSTR], handle),
            (kernel32.OpenFileMappingW, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], handle),
            (kernel32.MapViewOfFile, [handle, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t],
             ctypes.c_void_p),
            (kernel32.UnmapViewOfFile, [ctypes.c_void_p], wintypes.BOOL),
            (kernel32.GetCurrentProcess, [], handle),
            (kernel32.GetCurrentProcessId, [], wintypes.DWORD),
            (kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], handle),
            (kernel32.GetProcessTimes, [handle, filetime, filetime, filetime, filetime], wintypes.BOOL),
            (kernel32.TerminateProcess, [handle, wintypes.UINT], wintypes.BOOL),
            (kernel32.GetSystemTimeAsFileTime, [filetime], None),
            (user32.AllowSetForegroundWindow, [wintypes.DWORD], wintypes.BOOL),
        ):
            fn.argtypes, fn.restype = args, result
        self._kernel32, self._user32, self._handle = kernel32, user32, handle
        self._filetime = wintypes.FILETIME
        self._no_file = handle(-1).value  # INVALID_HANDLE_VALUE：共享内存不对应文件（放在系统的分页文件里）

    def create_mutex(self, name: str) -> tuple[int, int]:
        """建互斥体（已有时打开已有的那个）：（句柄, 错误代码）。句柄为 0 时没建成；错误代码为 ERROR_ALREADY_EXISTS 时
        是已有的那个。"""
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, name)
        return int(handle or 0), ctypes.get_last_error()

    def open_mutex(self, name: str) -> tuple[int, int]:
        """只打开已有的互斥体（只要等待的权限，不建）：（句柄, 错误代码），没有或打不开时句柄为 0。"""
        ctypes.set_last_error(0)
        handle = self._kernel32.OpenMutexW(SYNCHRONIZE, False, name)
        return int(handle or 0), ctypes.get_last_error()

    def create_event(self, name: str | None = None, manual: bool = False) -> int:
        """建事件（已有时打开已有的那个），不触发；没建成时为 0。name 为 None 时不起名（只在本进程里用）。"""
        return int(self._kernel32.CreateEventW(None, manual, False, name) or 0)

    def open_event(self, name: str) -> int:
        """打开已有的事件（等待、触发、清除的权限）；没有或打不开时为 0。"""
        return int(self._kernel32.OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, False, name) or 0)

    def set_event(self, handle: int) -> bool:
        return bool(self._kernel32.SetEvent(handle))

    def reset_event(self, handle: int) -> bool:
        return bool(self._kernel32.ResetEvent(handle))

    def wait(self, handle: int, ms: int) -> int:
        return int(self._kernel32.WaitForSingleObject(handle, ms))

    def wait_any(self, handles: tuple[int, ...], ms: int) -> int:
        """等其中任意一个；同时触发时返回排在前面的那个（WAIT_OBJECT_0 + 下标）。"""
        array = (self._handle * len(handles))(*handles)
        return int(self._kernel32.WaitForMultipleObjects(len(handles), array, False, ms))

    def close(self, handle: int) -> None:
        if handle:
            self._kernel32.CloseHandle(handle)

    def allow_foreground(self) -> None:
        self._user32.AllowSetForegroundWindow(ASFW_ANY)

    def create_mapping(self, name: str, size: int) -> tuple[int, int]:
        """建一块 size 字节的命名共享内存（已有时打开已有的那块）：（句柄, 错误代码），没建成时句柄为 0。"""
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateFileMappingW(self._no_file, None, PAGE_READWRITE, 0, size, name)
        return int(handle or 0), ctypes.get_last_error()

    def open_mapping(self, name: str) -> tuple[int, int]:
        """只读打开已有的命名共享内存（不建）：（句柄, 错误代码），没有或打不开时句柄为 0。"""
        ctypes.set_last_error(0)
        handle = self._kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
        return int(handle or 0), ctypes.get_last_error()

    def write_mapping(self, handle: int, data: bytes) -> bool:
        """把 data 写到共享内存的开头；写成了返回 True。"""
        view = self._kernel32.MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, len(data))
        if not view:
            return False
        try:
            ctypes.memmove(view, data, len(data))
        finally:
            self._kernel32.UnmapViewOfFile(view)
        return True

    def read_mapping(self, handle: int, size: int) -> bytes:
        """读共享内存开头的 size 个字节；读不了时为空。"""
        view = self._kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, size)
        if not view:
            return b""
        try:
            return ctypes.string_at(view, size)
        finally:
            self._kernel32.UnmapViewOfFile(view)

    def current_process(self) -> tuple[int, int]:
        """本进程的（进程号, 创建时间）；取不到创建时间时为 0。"""
        return int(self._kernel32.GetCurrentProcessId()), self.process_created(self._kernel32.GetCurrentProcess())

    def open_process(self, pid: int, terminate: bool = False) -> tuple[int, int]:
        """打开进程 pid（查创建时间的权限；terminate 时再加结束它、等它结束的权限）：（句柄, 错误代码），打不开时句柄
        为 0。没有这个进程时错误代码是 ERROR_INVALID_PARAMETER，权限不够时是 ERROR_ACCESS_DENIED。"""
        access = PROCESS_QUERY_LIMITED_INFORMATION | (PROCESS_TERMINATE | SYNCHRONIZE if terminate else 0)
        ctypes.set_last_error(0)
        handle = self._kernel32.OpenProcess(access, False, pid)
        return int(handle or 0), ctypes.get_last_error()

    def process_created(self, handle: int) -> int:
        """进程的创建时间（FILETIME 的整数值）；取不到时为 0。"""
        times = [self._filetime() for _ in range(4)]  # 创建、退出、内核态、用户态
        if not self._kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return 0
        return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime

    def terminate(self, handle: int, code: int) -> bool:
        """结束进程（handle 要有结束它的权限）；结束不了时返回 False。"""
        return bool(self._kernel32.TerminateProcess(handle, code))

    def now(self) -> int:
        """现在的时间（FILETIME 的整数值，和进程的创建时间同一种计法）。"""
        now = self._filetime()
        self._kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
        return now.dwHighDateTime << 32 | now.dwLowDateTime


def _load() -> _Win32 | None:
    """Win32 调用；非 Windows（ctypes 没有 WinDLL）或 ctypes 用不了时为 None：不拦，照常启动。"""
    try:
        return _Win32()
    except Exception:  # noqa: BLE001  查不了时不拦
        return None


_api = _load()


def _report(title: str, detail: str) -> None:
    """记进错误记录：已在运行的那个的等待线程里出的错（同悬浮窗其他运行时的错误）；再次启动的进程结束了没有响应的那个、
    结束不了、处理时出错。"""
    from . import log_error

    log_error(title, detail, append=True)


class Listener:
    """等「显示」请求的后台线程（守护线程，建好就开始等）。同时等一个只在本进程里用的「停止」事件：stop() 立即停下，
    等的时候不轮询、不占 CPU。api 为 None 或没有「显示」事件时（不拦、没拿到锁）不起线程。
    on_start：线程开始时、开始等之前在这个线程里调用一次；出错只记进错误记录，照样等。"""

    def __init__(self, api: _Win32 | None, show: int, on_show: Callable[[], None],
                 on_start: Callable[[], object] | None = None):
        self._api, self._show, self._on_show = api, show, on_show
        self._on_start = on_start
        self._stop = api.create_event(manual=True) if api is not None and show else 0
        self._thread: threading.Thread | None = None
        if self._stop:
            self._thread = threading.Thread(target=self._run, name="relaunch-listener", daemon=True)
            self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        if self._on_start is not None:
            try:
                self._on_start()
            except Exception:  # noqa: BLE001  回调出错不影响等待
                _report("等待再次启动的显示请求的线程开始时出错（拾星助手仍在运行）", traceback.format_exc())
        # 「停止」排在前面：两个同时触发时先停；等待出错（WAIT_FAILED）时同样结束，不空转
        while self._api.wait_any((self._stop, self._show), INFINITE) == WAIT_OBJECT_0 + 1:
            try:
                self._on_show()
            except Exception:  # noqa: BLE001  回调出错不停止等待
                _report("处理再次启动时的显示请求出错（拾星助手仍在运行）", traceback.format_exc())

    def stop(self, timeout: float = STOP_S) -> bool:
        """停下等待线程，最多等 timeout 秒；停下了（或本来就没起线程）返回 True。"""
        thread = self._thread
        if thread is None:
            return True
        self._api.set_event(self._stop)
        thread.join(timeout)
        if thread.is_alive():
            return False
        self._api.close(self._stop)
        self._stop, self._thread = 0, None
        return True


class Instance:
    """拿到的锁、两个事件和记着本进程的共享内存，句柄留到进程结束（由系统关掉）。查不了、不拦时是空的（held 为 False）：
    listen() 不起线程，acknowledge() 什么都不做。"""

    def __init__(self, api: _Win32 | None = None, mutex: int = 0, show_event: int = 0, shown_event: int = 0,
                 owner_map: int = 0):
        self._api = api
        self.mutex, self.show_event, self.shown_event = mutex, show_event, shown_event
        self.owner_map = owner_map

    @property
    def held(self) -> bool:
        """拿着锁（不是查不了、不拦时的空 Instance）。"""
        return self._api is not None and bool(self.mutex)

    def listen(self, on_show: Callable[[], None], on_start: Callable[[], object] | None = None) -> Listener:
        """起等待线程：收到「显示」请求时在那个线程里调用 on_show（不能直接碰界面）；on_start 见 Listener。"""
        return Listener(self._api if self.show_event else None, self.show_event, on_show, on_start)

    def acknowledge(self) -> None:
        """显示完了：触发「已显示」，等着的那个新启动的进程随即退出。"""
        if self._api is not None and self.shown_event:
            self._api.set_event(self.shown_event)

    def close(self) -> None:
        """释放锁、事件和共享内存。正式运行时不调用（留到进程结束，由系统关掉）；测试里放锁用。"""
        if self._api is not None:
            for handle in (self.owner_map, self.show_event, self.shown_event, self.mutex):
                self._api.close(handle)
        self.mutex = self.show_event = self.shown_event = self.owner_map = 0


def acquire(names: Names) -> Instance | None:
    """拿锁（建互斥体）。拿到了：建好「已显示」「显示」两个事件，把本进程记进共享内存（names.owner），返回 Instance；
    已有一个在运行（互斥体已存在，或打不开）时返回 None；查不了（非 Windows、ctypes 用不了、其他错误）时返回空的 Instance
    （不拦）。"""
    api = _api
    if api is None:
        return Instance()
    mutex, error = api.create_mutex(names.mutex)
    if not mutex:
        # 以管理员身份运行的那个建的互斥体，普通权限的进程可能打不开（拒绝访问）：也是已有一个在运行
        return None if error == ERROR_ACCESS_DENIED else Instance()
    if error == ERROR_ALREADY_EXISTS:
        api.close(mutex)  # 马上关掉：本进程开着句柄时，已在运行的那个退出后互斥体也不会消失，等不到锁释放
        return None
    # 先建「已显示」再建「显示」：再次启动的进程打得开「显示」时，「已显示」一定已经在了
    shown_event = api.create_event(names.shown)
    show_event = api.create_event(names.show)
    for handle in (shown_event, show_event):
        if handle:
            # 别的进程还开着旧事件时它不会随上一个运行者消失，上面残留的触发状态不算数（本进程启动时悬浮窗本来就显示）
            api.reset_event(handle)
    owner_map = _record_owner(api, names.owner) if names.owner else 0
    return Instance(api, mutex, show_event, shown_event, owner_map)


def _record_owner(api: _Win32, name: str) -> int:
    """把本进程的进程号、创建时间和拿到锁的时刻（现在）写进共享内存 name，返回它的句柄（留到进程结束）。已有
    这块共享内存（上一个运行者的，别的进程正开着它）时照样写上本进程。写不成时为 0：照常拿锁，只是再次启动的进程查不到是谁，
    退回说明窗。"""
    handle = 0
    try:
        handle, _ = api.create_mapping(name, OWNER_RECORD.size)
        if handle:
            pid, created = api.current_process()
            acquired = api.now()
            if pid and created and acquired and api.write_mapping(
                    handle, OWNER_RECORD.pack(OWNER_MAGIC, pid, created, acquired)):
                return handle
    except Exception:  # noqa: BLE001  记不上不影响拿锁
        pass
    api.close(handle)
    return 0


def _ask(api: _Win32, names: Names) -> bool:
    """请已在运行的那个显示悬浮窗：清掉「已显示」上没人等到的旧回应，允许别的进程把窗口放到前台，触发「显示」。
    两个事件有一个打不开（它刚拿到锁、还没建好事件，正在退出，或打不开）时返回 False，下一轮再试。"""
    shown_event = api.open_event(names.shown)
    if not shown_event:
        return False
    try:
        show_event = api.open_event(names.show)
        if not show_event:
            return False
        try:
            api.reset_event(shown_event)
            api.allow_foreground()
            return api.set_event(show_event)
        finally:
            api.close(show_event)
    finally:
        api.close(shown_event)


def _answered(api: _Win32, names: Names, timeout: float) -> bool:
    """最多等 timeout 秒「已显示」。打不开（已在运行的那个正在退出）时照样等满 timeout 再返回，不空转。"""
    shown_event = api.open_event(names.shown)
    if not shown_event:
        time.sleep(timeout)
        return False
    try:
        return api.wait(shown_event, max(int(timeout * 1000), 1)) == WAIT_OBJECT_0
    finally:
        api.close(shown_event)


def start(names: Names, wait: float = WAIT_S) -> Instance | None:
    """拿锁，拿不到时请已在运行的那个显示悬浮窗。

    返回 Instance：本进程照常启动（拿到了锁；或者查不了、不拦；或者已在运行的那个没有响应，按用户的选择结束了它、拿到了
    锁，或者弹窗显示期间它自己退出了、拿到了锁）。返回 None：本进程直接退出，已在运行的那个回应了，或者合计 wait 秒内既没
    回应、锁也没释放（之后见 _unanswered）。
    等的过程中每隔 POLL_S 秒再试一次拿锁，锁释放了就拿下它，照常启动。"""
    instance = acquire(names)
    if instance is not None:
        return instance
    api = _api
    deadline = time.monotonic() + wait
    asked = False
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        step = min(POLL_S, left)
        if not asked:
            asked = _ask(api, names)
        if asked:
            if _answered(api, names, step):
                return None
        else:
            time.sleep(step)
        instance = acquire(names)
        if instance is not None:
            return instance
    try:
        return _unanswered(api, names)
    except Exception:  # noqa: BLE001  弹窗、查进程出错时照旧退出：已经有一个在运行，不能再开一个
        _report("处理没有响应的拾星助手时出错（新启动的照旧退出）", traceback.format_exc())
        return None


@dataclass(frozen=True)
class Owner:
    """拿着锁、已在运行的那个（_find_owner 查出来的）。pid、created（进程号、创建时间）从共享内存读出，和那个进程核对过；
    age 是它拿到锁以后已经运行了多少秒（开了多久）。查不到（没有这块共享内存、读不出、格式不对、进程对不上）时 pid 为 0、
    age 为 None。denied：锁、共享内存或进程打不开（拒绝访问，多半是它以管理员身份运行），这时 age 可能也是 None。"""

    pid: int = 0
    created: int = 0
    age: float | None = None
    denied: bool = False


def _find_owner(api: _Win32, names: Names) -> Owner:
    """查已在运行的是谁、开了多久：读共享内存里记的进程号、创建时间和拿到锁的时刻，再打开这个进程核对创建时间（进程号可能
    已被别的进程重用）；开了多久按拿到锁的时刻算。标记不对（格式不同的旧记录）、记的是本进程自己时（同一个进程里拿过锁，
    只在测试里会有）当作查不到，不会问要不要结束自己。"""
    mutex, error = api.open_mutex(names.mutex)
    api.close(mutex)
    denied = error == ERROR_ACCESS_DENIED  # 以管理员身份运行的那个建的锁，普通权限的进程打不开
    if not names.owner:
        return Owner(denied=denied)
    mapping, error = api.open_mapping(names.owner)
    if not mapping:
        return Owner(denied=denied or error == ERROR_ACCESS_DENIED)
    try:
        data = api.read_mapping(mapping, OWNER_RECORD.size)
    finally:
        api.close(mapping)
    if len(data) != OWNER_RECORD.size:
        return Owner(denied=denied)
    magic, pid, created, acquired = OWNER_RECORD.unpack(data)
    if magic != OWNER_MAGIC or not pid or not created or not acquired or pid == api.current_process()[0]:
        return Owner(denied=denied)
    process, error = api.open_process(pid)
    if not process:
        return Owner(denied=denied or error == ERROR_ACCESS_DENIED)
    try:
        actual = api.process_created(process)
    finally:
        api.close(process)
    if actual != created:
        return Owner(denied=denied)  # 记的那个已经退出，进程号被别的进程重用了
    return Owner(pid, created, max(0.0, (api.now() - acquired) / FILETIME_PER_S), denied)


class _Released:
    """弹窗（问的窗、说明窗）显示期间，由弹窗的定时器每隔 DIALOG_POLL_S 秒调用一次：试拿锁，拿到了（已在运行的那个自己
    退出了）返回 True，弹窗随即关掉，本进程照常启动。拿到的锁在 instance。只认真拿到了锁的：查不了、不拦时的空 Instance
    不算（那时它可能还在运行，不能另开一个）。拿锁出错时记一笔，之后不再试，弹窗照常等用户点。"""

    def __init__(self, names: Names):
        self._names = names
        self._broken = False
        self.instance: Instance | None = None

    def __call__(self) -> bool:
        if self.instance is None and not self._broken:
            try:
                instance = acquire(self._names)
            except Exception:  # noqa: BLE001  试不了只是不能自动关掉弹窗
                self._broken = True
                _report("弹窗显示期间试拿锁时出错（不再试，弹窗照常等用户点）", traceback.format_exc())
            else:
                if instance is not None and instance.held:
                    self.instance = instance
        return self.instance is not None


def _unanswered(api: _Win32, names: Names) -> Instance | None:
    """等满了还没回应、锁也没释放：刚开、还在加载的照旧安静退出；拿到锁满 STUCK_S 秒的问用户要不要结束它并
    重新打开；打不开、查不到的弹说明窗。弹窗显示期间它自己退出了，就关掉弹窗、拿着锁照常启动（_Released）。返回同 start()。"""
    instance = acquire(names)  # 刚好在最后一刻释放了
    if instance is not None:
        return instance
    owner = _find_owner(api, names)
    if owner.age is not None and owner.age < STUCK_S:
        return None  # 刚开、还在加载：它加载完会处理还挂着的「显示」请求
    released = _Released(names)
    if owner.denied or owner.age is None:
        _tell(ELEVATED if owner.denied else UNKNOWN, released)
        return released.instance
    end = _ask_end(owner.age, released)
    if released.instance is not None:
        return released.instance  # 问的时候它自己退出了：已经拿到锁，不用结束谁
    if not end:
        return None
    return _end_and_take(api, names, owner, released)


def _end_and_take(api: _Win32, names: Names, owner: Owner, released: _Released) -> Instance | None:
    """用户点了「结束它并重新打开」：它其实回应了就不结束、退出；它自己退出了就拿下锁；否则结束它（只结束核对过的那个），
    等它真的结束、锁释放后拿下锁照常启动，拿不到就退出。结束不了时弹说明窗（显示期间它退出了同样关掉、照常启动）。"""
    if _recheck(api, names):
        return None  # 回应了：悬浮窗已经显示出来
    instance = acquire(names)
    if instance is not None:
        return instance
    result = _end(api, owner)
    if result == DENIED:
        _report("没能结束没有响应的拾星助手", f"进程号 {owner.pid}：打不开或结束不了（可能以管理员身份运行）")
        _tell(CANNOT_END, released)
        return released.instance
    if result == ENDED:
        _report("结束了没有响应的拾星助手（用户选了「结束它并重新打开」）",
                f"进程号 {owner.pid}，已运行约 {owner.age:.0f} 秒，{WAIT_S:g} 秒内没有回应再次启动的显示请求")
    elif result == NOT_EXITED:
        _report("已请系统结束没有响应的拾星助手，但它没有及时退出（用户选了「结束它并重新打开」）",
                f"进程号 {owner.pid}，已运行约 {owner.age:.0f} 秒；请系统结束它以后等了 {END_WAIT_S:g} 秒还没有退出，"
                f"再等 {TAKE_S:g} 秒锁释放，等不到时新启动的照旧退出")
    return _take(names, TAKE_S)


def _recheck(api: _Win32, names: Names) -> bool:
    """结束之前再查一次它是不是回应了：问的这段时间里回应了（「已显示」触发了），或者再请它显示一次、RECHECK_S 秒内回应
    了。再请一次是因为它的回应只有一份：同时有别的再次启动的进程在等时，可能被那一个先取走。"""
    if _answered(api, names, 0):
        return True
    return _ask(api, names) and _answered(api, names, RECHECK_S)


def _end(api: _Win32, owner: Owner) -> str:
    """结束 owner，按实际结果返回 ENDED、NOT_EXITED、GONE、DENIED。只结束进程号和创建时间都对得上的那个：打开进程以后先
    核对创建时间，再用同一个句柄结束（句柄指着的进程不会换），打开之前它已经退出、进程号被别的进程重用时核对不上，不结束。
    请系统结束它以后最多等 END_WAIT_S 秒：等到它退出了才算 ENDED，没等到是 NOT_EXITED。结束不了时看一眼它是不是刚好自己
    退出了（已经退出的进程不让再结束）：是就是 GONE（没有结束谁），不是才是 DENIED。"""
    process, error = api.open_process(owner.pid, terminate=True)
    if not process:
        return DENIED if error == ERROR_ACCESS_DENIED else GONE
    try:
        if api.process_created(process) != owner.created:
            return GONE
        if not api.terminate(process, ENDED_CODE):
            return GONE if api.wait(process, 0) == WAIT_OBJECT_0 else DENIED
        return ENDED if api.wait(process, int(END_WAIT_S * 1000)) == WAIT_OBJECT_0 else NOT_EXITED
    finally:
        api.close(process)


def _take(names: Names, seconds: float) -> Instance | None:
    """最多等 seconds 秒，锁释放了就拿下它（每隔 POLL_S 秒试一次）；等不到时为 None。"""
    deadline = time.monotonic() + seconds
    while True:
        instance = acquire(names)
        if instance is not None or time.monotonic() >= deadline:
            return instance
        time.sleep(POLL_S)


def _ask_end(age: float, released: Callable[[], bool] | None = None) -> bool:
    """问用户要不要结束没有响应的那个（age：它拿到锁以后已经运行了多少秒），选了「结束它并重新打开」时为 True。released：
    弹窗显示期间每隔 DIALOG_POLL_S 秒调用一次，返回 True（它自己退出了、已经拿到锁）时弹窗自己关掉，这里为 False。弹窗用
    PySide6，只在这条慢路上导入；测试里换成假的。"""
    from .stuck_prompt import ask_end

    return ask_end(age, released)


def _tell(kind: str, released: Callable[[], bool] | None = None) -> None:
    """弹只有「确定」的说明窗（kind：UNKNOWN、ELEVATED、CANNOT_END）；released 同 _ask_end。测试里换成假的。"""
    from .stuck_prompt import tell

    tell(kind, released)


_held: Instance | None = None  # claim() 拿到的（查不了、不拦时是空的 Instance），留到进程结束


def claim() -> bool:
    """run() 在导入悬浮窗之前调用一次（正式的名字 NAMES）：本进程可以照常启动时返回 True（拿到了锁，或者查不了、不拦，
    或者结束了没有响应的那个、拿到了锁，或者弹窗显示期间它自己退出了、拿到了锁），已有一个拾星助手在运行时返回 False（已请
    它显示悬浮窗；或者它没回应、锁也没释放，用户没让结束它），调用方直接退出。"""
    global _held
    if _held is None:
        _held = start(NAMES)
    return _held is not None


def listen(on_show: Callable[[], None], on_start: Callable[[], object] | None = None) -> Listener:
    """悬浮窗建好后调用：claim() 拿到锁时起等待线程，收到「显示」请求时在那个线程里调用 on_show（on_start 见 Listener）；
    否则返回不起线程的 Listener。退出前调用它的 stop()。"""
    held = _held
    return held.listen(on_show, on_start) if held is not None else Listener(None, 0, on_show, on_start)


def acknowledge() -> None:
    """（界面线程）显示完悬浮窗以后调用：告诉新启动的那个已经显示，它随即退出。claim() 没拿到锁时什么都不做。"""
    held = _held
    if held is not None:
        held.acknowledge()


def running(name: str | None = None) -> bool:
    """有没有拾星助手（开发版，或带这项检查的发行版）正在运行：只打开互斥体看在不在，不建、不拿、不碰那个进程。
    name 默认正式的名字。打不开（以管理员身份运行的那个）也算在运行；查不了时为 False。打包脚本试启动之前用。"""
    api = _api
    if api is None:
        return False
    handle, error = api.open_mutex(name or NAMES.mutex)
    if handle:
        api.close(handle)
        return True
    return error == ERROR_ACCESS_DENIED
