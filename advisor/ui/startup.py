"""启动时的几项检查：崩溃记录、显示悬浮窗之前把识别要用的库加载一遍、悬浮窗出来以后通知双击时的启动程序。

run()（advisor/ui/__init__.py）在导入界面库之前就用到这里，所以模块本身不导入 PySide6（只有悬浮窗起来以后才调用的
signal_ready_later、watch_crash_log 在函数里导入）。发行版的入口（packaging/chip_advisor.py）要在导入 advisor 之前就打开崩溃
记录、导入出错时自己弹窗，那几样在入口里另写了一份（文件名、退出码、弹窗的字和这里的相同，tests/test_startup_guard.py 核对）。

崩溃记录（程序目录 .tmp\\crash.txt，和错误记录 overlay_err.txt 在同一个文件夹）：
- 用 faulthandler 写：进程在本机代码里崩溃（访问违例、处理器不认识的指令等）时，把各线程停在哪一行写进去；文件在整个进程期间
  开着。发行版由入口最先打开，再交给这里（adopt_crash_log）；开发版由 run() 在查过防多开以后打开（open_crash_log），再次启动、
  只请已在运行的那个显示悬浮窗的进程不碰它。faulthandler 已经开着时（发行版的入口开过，开发时用 -X faulthandler 打到控制台，
  测试）不再改它。
- 上一次的 crash.txt 不是空的（上一次崩溃了）：先改名成 crash.old.txt（只留一份），用户重新打开一次不会把记录冲掉；改不了名
  （例如另一个拾星助手正开着它）时接在后面写，不清掉。文件夹写不进时不打开，也不报错。
- 另存的 crash.old.txt 里的用户目录、账户名换成占位（scrub_old_crash_log，同错误记录）：使用说明请用户把它发给作者。
  崩溃那一刻由 faulthandler 原样写，那时换不了，所以另存以后、下次启动时换；发行版由入口改名，交过来时（adopt_crash_log）换。
- Windows 上 faulthandler 用的是向量化异常处理：进程里别的库自己接住了的异常（例如显卡驱动里试探内存时的访问违例）也会照样
  记成「Windows fatal exception」，其实不是崩溃。所以 run() 走完时（照常退出、启动失败弹过窗以后退出）清空它
  （clear_crash_log）：这时 Python 还在跑，里面有的都不是崩溃；真的崩溃时走不到这一步，记录留着，下次启动另存成 crash.old.txt。
- 这类异常有的会反复出现（显卡驱动、剪贴板这类 COM 调用），每出一次就把各线程的调用栈写一遍，文件一直长、每次也要花时间。
  所以运行中界面线程隔一会儿看一眼它的大小（watch_crash_log，只取大小），超过 CRASH_LIMIT 就停用这次运行的崩溃记录
  （check_crash_log：文件留着，照常退出时照旧清空），在错误记录里记一笔记录里第一条异常是哪一行。
"""
from __future__ import annotations

import faulthandler
import importlib
import os
import re
import traceback
from pathlib import Path

from .. import paths
from ..errors import log_error

CRASH_NAME = "crash.txt"
CRASH_OLD_NAME = "crash.old.txt"  # 上一次崩溃的记录（只留一份）
# 崩溃记录在一次运行里最多记这么大（check_crash_log）：一次真的崩溃只写几 KB 到几十 KB，到了这么大还在长的，多半是别的库
# 反复自己接住的异常（例如剪贴板这类 COM 调用的「Windows fatal exception: code 0x8001010d」）
CRASH_LIMIT = 512 * 1024
CRASH_CHECK_MS = 10_000  # 运行中隔多久看一眼崩溃记录的大小（毫秒，watch_crash_log）
# 崩溃记录超过 CRASH_LIMIT、这次运行不再往里记时，错误记录里这一条的标题（详细信息写大小和记录里第一条异常那一行）
CRASH_FULL_TITLE = "崩溃记录太大，这次运行不再往里记（拾星助手仍在运行）"
# 崩溃记录里一条异常的开头那一行（faulthandler 的写法：本机代码里的异常、Python 自己的致命错误）
EXCEPTION_LINE = re.compile(r"^(?:Windows fatal exception|Fatal Python error)\b.*$", re.M)

# 双击发行版根目录的启动程序时，它建一个命名事件、把名字放在这个环境变量里，最多等十几秒：悬浮窗出来、事件循环转了
# READY_DELAY_MS 以后触发它（signal_ready_later），启动程序随即退出；这期间程序崩了、以别的退出码退出，由启动程序弹窗说明。
# 开发版、直接双击 ACC.exe 时没有
READY_ENV = "ACC_READY_EVENT"
EVENT_MODIFY_STATE = 0x0002  # OpenEventW 要的权限：触发事件
ERROR_FILE_NOT_FOUND = 2  # 事件已经没了：启动程序等满了、不再等
# 事件循环转起来以后过多久才通知启动程序（毫秒，signal_ready_later）：悬浮窗刚显示时第一次绘制还没做、识别刚开始，紧接着
# 崩溃的话启动程序已经退出、接不住，用户只看到窗口一闪就没了。多等这一会儿，这段时间里崩溃了由启动程序按退出码弹窗说明；
# 等约五秒，识别线程开头几秒（第一次截取画面、第一次文字识别）出的事也接得住，启动程序只是在后台多留几秒。
# 启动程序最多等 15 秒，够用（打包脚本试启动的时限 LAUNCHER_EXIT_S 要比它加上悬浮窗出来的时间长）
READY_DELAY_MS = 5000

# 识别要用的库：advisor/vision 一导入就要的，和后台识别线程第一次做文字识别时才导入的那几个（overlay_parts/watcher.py 的
# _loop、vision/chip_select.py 的 _engine）。numpy 放最前：处理器缺 numpy 编译时要求的指令时在这里就报出来
# （startup_summary 按「处理器太老」写）
PRELOAD = (
    "numpy",
    "cv2",  # OpenCV
    "mss",  # 截取屏幕
    "onnxruntime",  # 文字识别的推理库
    "rapidocr_lite.main",  # 精简文字识别库（连带 pyclipper）
    "rapidocr_lite.inference_engine.onnxruntime",  # 它接推理库的那一层
)

# 发行版的入口去掉了别的软件设的 Qt 环境变量时，错误记录里这一条的标题（详细信息是去掉了的那几个名字，不记值）
DROPPED_ENV_TITLE = "启动时去掉了别的软件设的 Qt 环境变量（拾星助手照常启动）"

_crash_file = None  # 本进程开着的崩溃记录（开发版由 open_crash_log 打开，发行版由入口打开后交过来）
_crash_full = False  # 崩溃记录已经太大、这次运行停用了（check_crash_log）
_crash_timer = None  # 运行中看崩溃记录大小的定时器（watch_crash_log）
_ready_name: str | None = None  # READY_ENV 里的事件名（take_ready_event 取走）
_dropped_env: tuple[str, ...] = ()  # 发行版的入口去掉了的 Qt 环境变量的名字，还没记进错误记录的


def crash_path() -> Path:
    """崩溃记录的位置：程序目录 .tmp 下的 crash.txt（同 paths.temp_dir()，和错误记录在同一个文件夹）。"""
    return paths.temp_dir() / CRASH_NAME


def open_crash_log(folder: Path | str | None = None) -> Path | None:
    """打开崩溃记录（folder 默认 paths.temp_dir()），交给 faulthandler（各线程都写），返回它的位置。faulthandler 已经开着时
    什么都不做；打不开、文件夹写不进时也不报错。两种情形都返回 None。上一次的记录不是空的先另存，见模块说明。"""
    global _crash_file
    if faulthandler.is_enabled():
        return None
    folder = Path(folder) if folder is not None else paths.temp_dir()
    path = folder / CRASH_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size:
                os.replace(path, folder / CRASH_OLD_NAME)
        except OSError:
            pass  # 还没有记录；或者改不了名（另一个拾星助手正开着它）：接在后面写
        scrub_old_crash_log(folder)
        file = open(path, "a", encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        faulthandler.enable(file=file, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        file.close()
        return None
    _crash_file = file
    return path


def adopt_crash_log(file) -> None:
    """发行版的入口在导入 advisor 之前打开的崩溃记录（没打开时为 None）：交给这里，run() 走完时由 clear_crash_log 清空。"""
    global _crash_file
    if file is not None:
        _crash_file = file
        name = getattr(file, "name", None)
        scrub_old_crash_log(Path(name).parent if isinstance(name, str) else None)


def scrub_old_crash_log(folder: Path | str | None = None) -> None:
    """另存的上一次崩溃记录（folder 里的 crash.old.txt，folder 默认 paths.temp_dir()）里的用户目录、账户名换成占位
    （paths.scrub_paths；faulthandler 把非 ASCII 字符写成 \\uXXXX，这种写法也换）。换过的再换一次不变；没有、读不了、
    写不进时不管。"""
    old = (Path(folder) if folder is not None else paths.temp_dir()) / CRASH_OLD_NAME
    try:
        text = old.read_text(encoding="utf-8", errors="replace", newline="")  # 换行照原样（faulthandler 写的是 \n）
        clean = paths.scrub_paths(text)
        if clean != text:
            old.write_text(clean, encoding="utf-8", newline="")
    except OSError:
        pass


def clear_crash_log() -> None:
    """run() 走完时调用（照常退出，或启动失败弹过窗以后退出）：本进程开着的崩溃记录里这时有的，只可能是别的库自己接住了的
    异常（见模块说明），清空，免得下次启动当成崩溃另存（太大、这次运行停用了的也一样清空）。本进程没开着崩溃记录时
    （faulthandler 本来就开着）不动文件。"""
    file = _crash_file
    if file is None:
        return
    try:
        file.flush()
        file.truncate(0)
    except (OSError, ValueError):
        pass  # 清不掉只是下次启动多另存一份


def check_crash_log(limit: int = CRASH_LIMIT) -> bool:
    """看一眼本进程开着的崩溃记录有多大（只取大小，不读内容）。超过 limit 时停用这次运行的崩溃记录（faulthandler.disable()：
    不再往里记；文件留着，照常退出时照旧清空），在错误记录里记一笔大小和记录里第一条异常那一行（只在这时读文件开头），只记
    一次。返回以后还用不用接着看：停用了、本进程没开着崩溃记录、取不了大小时为 True。"""
    global _crash_full
    file = _crash_file
    if file is None or _crash_full:
        return True
    try:
        size = os.fstat(file.fileno()).st_size
    except (OSError, ValueError):  # 文件已经关了
        return True
    if size <= limit:
        return False
    _crash_full = True
    faulthandler.disable()
    first = _first_exception_line(file.name) or "（读不出）"
    log_error(CRASH_FULL_TITLE,
              f"崩溃记录 {CRASH_NAME} 已有 {size // 1024} KB，超过上限 {limit // 1024} KB：多半是别的库自己接住了的异常"
              f"（不是崩溃）反复出现，每出一次就记一次。这次运行不再往里记（文件留着，照常退出时清空）。\n第一条：{first}",
              append=True)
    return True


def _first_exception_line(path, head: int = 4096) -> str:
    """崩溃记录开头 head 字节里第一条异常那一行（EXCEPTION_LINE；没有时取第一行不空的），最多 200 个字；读不出时为空。"""
    try:
        with open(path, "rb") as f:
            text = f.read(head).decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return ""
    found = EXCEPTION_LINE.search(text)
    line = found.group(0) if found else next((s for s in text.splitlines() if s.strip()), "")
    return line.strip()[:200]


def watch_crash_log(interval_ms: int = CRASH_CHECK_MS, limit: int = CRASH_LIMIT):
    """悬浮窗起来以后、事件循环开始之前调用（overlay_parts/app.py 的 main()）：本进程开着崩溃记录时，在界面线程里每隔
    interval_ms 看一眼它的大小（check_crash_log，超过 limit 就停用、不再看），返回定时器。本进程没开着崩溃记录（开发时用
    -X faulthandler、测试）、已经停用或已经在看时什么都不做，返回 None。界面库在函数里导入（见模块说明）。"""
    global _crash_timer
    if _crash_file is None or _crash_full or _crash_timer is not None:
        return None
    from PySide6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication.instance()
    if app is None:
        return None
    timer = QTimer(app)  # 跟着程序对象：退出时一起销毁

    def tick() -> None:
        if check_crash_log(limit):
            timer.stop()

    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    timer.start()
    _crash_timer = timer
    return timer


def remember_dropped_env(names) -> None:
    """发行版的入口在导入 PySide6 之前去掉了的 Qt 环境变量（名字）：先记在这里，由 log_dropped_env 写进错误记录。"""
    global _dropped_env
    _dropped_env = tuple(names)


def log_dropped_env() -> None:
    """把 remember_dropped_env 记下的名字写进错误记录（只记名字，不记值），只写一次。run() 查过防多开以后调用：再次启动、
    只请已在运行的那个显示悬浮窗的进程不写；自检由入口直接调用。"""
    global _dropped_env
    names, _dropped_env = _dropped_env, ()
    if names:
        log_error(DROPPED_ENV_TITLE, "、".join(names), append=True)


def take_ready_event() -> None:
    """run() 一开始调用：从环境变量里取走 READY_ENV，记在这里，等悬浮窗出来以后再触发（signal_ready_later）。取走以后，之后起的
    子进程（画皮肤缩略图等）继承不到它。"""
    global _ready_name
    _ready_name = os.environ.pop(READY_ENV, "").strip() or None


def signal_ready_later(delay_ms: int = READY_DELAY_MS) -> None:
    """悬浮窗显示以后、事件循环开始之前调用（overlay_parts/app.py 的 main()）：排一个只走一次的定时器，事件循环转起来约
    delay_ms 毫秒以后再 signal_ready（为什么要等见 READY_DELAY_MS）。这之前悬浮窗照常退出了的不通知：启动程序看到退出码 0
    不弹窗。没有事件名（不是经启动程序启动的：开发版、直接双击 ACC.exe）时什么都不做，不建定时器。
    界面库只在这里用到、在函数里导入：调用它时悬浮窗早已建好，界面库已经加载。"""
    if _ready_name is None:
        return
    from PySide6.QtCore import QTimer

    QTimer.singleShot(delay_ms, signal_ready)


def signal_ready() -> None:
    """悬浮窗显示出来、事件循环转了一会儿以后调用（signal_ready_later 排的定时器）：打开 take_ready_event 取走的命名事件、
    触发它、关掉句柄，双击时的启动程序等到它就退出。只做一次；没有这个事件名时什么都不做。事件已经没了（启动程序等满了、
    不再等）时不报；别的原因出错只记进错误记录，悬浮窗照常运行。"""
    global _ready_name
    name, _ready_name = _ready_name, None
    if not name or os.name != "nt":
        return
    title = "通知启动程序「悬浮窗已显示」时出错（拾星助手仍在运行）"
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # 自己的一份：声明参数类型不影响别处
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if not handle:
            error = ctypes.get_last_error()
            if error != ERROR_FILE_NOT_FOUND:
                log_error(title, f"打开事件失败（OpenEventW），错误代码 {error}", append=True)
            return
        try:
            if not kernel32.SetEvent(handle):
                log_error(title, f"触发事件失败（SetEvent），错误代码 {ctypes.get_last_error()}", append=True)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001  通知不到只是启动程序多等一会儿，不影响悬浮窗
        log_error(title, traceback.format_exc(), append=True)


def preload() -> None:
    """显示悬浮窗之前把 PRELOAD 导入一遍（run() 调用）。出错照常抛出，由 run() 按启动失败报（startup_summary 分原因写），
    不开悬浮窗：不然要等后台识别线程导入时才出错，线程退出，悬浮窗一直显示「等待…」。这些本来也要导入（识别线程一开始、
    第一次文字识别时），提前到这里只让悬浮窗晚出来一点（开发环境实测多约 0.16 秒：numpy、OpenCV 本来就在显示之前导入，
    多出来的主要是推理库）。"""
    for name in PRELOAD:
        importlib.import_module(name)
