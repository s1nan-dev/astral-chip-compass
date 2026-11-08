"""重新打开拾星助手：换了界面语言（lang.py）以后，设置窗口的「现在重开」按这里起一个新的进程、本进程随即退出。

界面语言一个进程只有一种，启动时定好、运行中换不到（界面文字是各模块导入时拷走的常量），所以要重开。新进程带的
WAIT_ARG（--wait-pid 旧进程号）让它在拿防多开的锁以前先等旧进程退出：旧进程还在写设置文件、放锁时，新进程按上一次的
界面语言跑起来就白等了。等不到也照常往下（拿不到锁就和平时第二次双击一样请已在运行的那个显示悬浮窗）。

发行版直接起 app 文件夹里的 ACC.exe（发行版里 sys.executable 不是真的程序文件）；开发版起 sys.executable -m advisor.ui。
工作目录都是程序目录（paths.app_dir）：发行版是 app 文件夹，同根目录的启动程序、换版本时直接起 ACC.exe 的做法（诊断包里
记的工作目录也按它核对）；开发版是项目根目录（-m advisor.ui 从这里找包）。起进程用 update_apply 里的 CreateProcessW 写法
（不经过 shell、不弹黑窗口），这里只是在它上面包一层。
"""
from __future__ import annotations

import sys

from .. import paths, version

WAIT_ARG = "--wait-pid"  # 新进程带的参数，后面接旧进程号
DEFAULT_WAIT_S = 15.0  # 等旧进程退出最多多久（秒）

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

_api = None  # kernel32 里用到的三个函数（第一次用到时声明）；False 表示用不了


def waited_pid(argv) -> int | None:
    """命令行里的旧进程号（WAIT_ARG 后面，或 WAIT_ARG=）；没带、不是正整数时为 None。只认十位以内的 ASCII 数字：「²」这类
    isdigit 认、int 认不了的字，和长得 int 不肯转的一串数字，都按没带（这里在启动的最前面，出错就起不来）。"""
    for i, arg in enumerate(argv):
        if arg == WAIT_ARG:
            value = argv[i + 1] if i + 1 < len(argv) else ""
        elif arg.startswith(WAIT_ARG + "="):
            value = arg[len(WAIT_ARG) + 1:]
        else:
            continue
        digits = value.isascii() and value.isdigit() and len(value) <= 10
        return int(value) if digits and 0 < int(value) <= 0xFFFFFFFF else None
    return None


def command(pid: int) -> tuple[list[str], str]:
    """重开用的命令行和工作目录：发行版直接起 app 文件夹里的 ACC.exe，开发版起当前的解释器运行 advisor.ui；都带上
    WAIT_ARG 和旧进程号。工作目录是程序目录（发行版的 app 文件夹、开发版的项目根目录，见模块说明）。"""
    if paths.is_frozen():
        return [str(paths.app_dir() / f"{version.SHORT_NAME}.exe"), WAIT_ARG, str(pid)], str(paths.app_dir())
    return [sys.executable, "-m", "advisor.ui", WAIT_ARG, str(pid)], str(paths.app_dir())


def start(pid: int) -> bool:
    """起重开用的进程，起来了返回 True；起不来（找不到程序文件、系统不让起等）返回 False。"""
    try:
        argv, cwd = command(pid)
        from .. import update_apply

        return bool(update_apply.start_program(argv, cwd))
    except Exception:  # noqa: BLE001  起不来就照旧运行，由调用的一方决定怎么办
        return False


def _kernel32():
    """kernel32 里 OpenProcess、WaitForSingleObject、CloseHandle 的声明；不是 Windows、声明不了时为 None。"""
    global _api
    if _api is None:
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL
            _api = k32
        except Exception:  # noqa: BLE001  用不了时按进程已退出处理
            _api = False
    return _api or None


def open_process(pid: int) -> int:
    """打开 pid（只要等待的权限）；进程已经不在、打不开时为 0（调用的一方当作已退出）。"""
    api = _kernel32()
    return int(api.OpenProcess(SYNCHRONIZE, False, pid) or 0) if api is not None else 0


def wait_process(handle: int, timeout_ms: int) -> int:
    """等进程退出，最多 timeout_ms 毫秒：退出了为 WAIT_OBJECT_0，到时间还没退出为别的值。"""
    api = _kernel32()
    return int(api.WaitForSingleObject(handle, timeout_ms)) if api is not None else WAIT_OBJECT_0


def close_handle(handle: int) -> None:
    api = _kernel32()
    if api is not None and handle:
        api.CloseHandle(handle)


def wait_for_exit(pid, timeout: float = DEFAULT_WAIT_S) -> bool:
    """等旧进程 pid 退出，最多 timeout 秒：退出了（或者本来就不在、打不开）返回 True，到时间还没退出返回 False。"""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        handle = open_process(pid)
    except Exception:  # noqa: BLE001  打不开当作已退出（启动的最前面调用，不能出错）
        return True
    if not handle:
        return True
    try:
        return wait_process(handle, max(0, int(timeout * 1000))) == WAIT_OBJECT_0
    except Exception:  # noqa: BLE001  等不了就当作已退出，照常启动
        return True
    finally:
        close_handle(handle)
