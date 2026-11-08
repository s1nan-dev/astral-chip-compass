"""悬浮窗。需要 PySide6（装在项目的 .venv 里）。

这个文件不导入 PySide6：缺库时也能把错误写进文件、弹窗告诉用户。错误记录在 advisor/errors.py，log_error、ERR_FILE
照旧能从这里导入。启动时的几项检查（崩溃记录、预加载识别要用的库、悬浮窗出来的信号）在 startup.py。
"""
from __future__ import annotations

import ctypes
import sys

from ..errors import ERR_FILE, EXIT_REPORTED, log_error  # noqa: F401  照旧能从 advisor.ui 导入

MB_ICONERROR = 0x10
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000

# 隐藏的启动参数：在子进程里画皮肤的缩略图（preview_child.child_main）。带它启动的不查防多开、不起悬浮窗、不弹窗
PREVIEW_ARG = "--skin-previews"

# 启动失败的弹窗按原因写的几句。发行版的入口导入不了 advisor 时用前两句，入口里另写了一份（测试核对相等）
BROKEN_FILES = "程序文件不完整或已损坏，请把压缩包整个重新解压。"
LOAD_BLOCKED = ("程序文件加载失败，可能被杀毒软件或 Windows 的「智能应用控制」拦截，也可能解压不完整。"
                "请在杀毒软件中把拾星助手文件夹设为信任、从隔离区恢复被隔离的文件，或把压缩包整个重新解压。")
OLD_CPU = ("这台电脑的处理器太老，拾星助手用不了：需要支持 SSE4.2 的处理器"
           "（英特尔酷睿 i 系列、AMD FX 和锐龙系列都支持）。")
# 认原因用的报错原文（只用来分原因，不显示）：扩展模块或它依赖的 DLL 加载不了。开发版是 Python 自己的写法（「DLL load failed
# while importing X: 系统的说明」），发行版是 Nuitka 的写法（「LoadLibraryExW '路径' failed: 系统的说明」）。
# 发行版在中文、日文等系统上多半连这句也没有：Nuitka 按本机的 ANSI 代码页取系统的说明（路径也是），拼好以后按 UTF-8 转成文字，
# 转不了时抛出的是一个什么字都没有的 ImportError。所以连带的异常里有原文是空的 ImportError 也算（load_failed）
LOAD_FAILED_MARKS = ("DLL load failed", "LoadLibraryExW")
# numpy 导入时发现处理器缺它编译时要求的指令：抛 RuntimeError「NumPy was built with baseline optimizations: (X86_V2) but your
# machine doesn't support: (……).」（numpy 的 npy_cpu_features.c；本项目自编的 numpy 要求 X86_V2，即 SSE4.2 那一代）
OLD_CPU_MARKS = ("baseline optimizations", "your machine doesn't support")


class Exited(SystemExit):
    """悬浮窗启动起来以后的退出（overlay_parts/app.py 的 main() 收尾完抛出，退出码照旧在 code 里）：退出码不是 0 也不是启动失败，
    run() 不弹「启动失败」（非 0 的退出码由 main() 记进错误记录）。"""


def report_startup_error(summary: str, detail: str) -> None:
    """启动失败：追加进错误记录（不清掉之前记下的运行时错误），再弹一个系统消息框（用 ctypes，不依赖 PySide6）。"""
    where = log_error("拾星助手启动失败", detail)
    if sys.stderr:
        print(detail, file=sys.stderr)
    lines = summary.strip().splitlines()
    if len(lines) > 12:
        lines = lines[:12] + [f"……（共 {len(lines)} 行）"]
    ctypes.windll.user32.MessageBoxW(
        None, "\n".join(lines) + "\n\n" + where, "拾星助手启动失败", MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
    )


def _chain(e: BaseException):
    """e 和连带的异常（raise … from、处理时又出的错），每个连同报错原文：numpy 等库把底层的错包一层再抛，原文在里面。"""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        try:
            text = str(e)
        except Exception:  # noqa: BLE001  原文取不出时按空的算
            text = ""
        yield e, text
        e = e.__cause__ or e.__context__


def load_failed(e: BaseException) -> bool:
    """扩展模块或它依赖的 DLL 加载不了：多半是杀毒软件、Windows 的应用控制拦下了，或者解压不完整。报错原文里有
    LOAD_FAILED_MARKS，或者连带的异常里有原文是空的 ImportError（发行版在中文等系统上的样子，见 LOAD_FAILED_MARKS）。"""
    return any(any(mark in text for mark in LOAD_FAILED_MARKS) or (isinstance(error, ImportError) and not text.strip())
               for error, text in _chain(e))


def old_cpu(e: BaseException) -> bool:
    """numpy 报这台处理器缺它编译时要求的指令（OLD_CPU_MARKS）。"""
    return any(all(mark in text for mark in OLD_CPU_MARKS) for _, text in _chain(e))


def startup_summary(e: BaseException) -> str:
    """启动失败时弹窗里给用户看的中文：按原因分开写，不含英文原文、类名、路径；报错原文（调用栈）由
    report_startup_error 写进错误记录。"""
    try:
        from .. import data
    except Exception:  # noqa: BLE001  连资料模块也导不进来时按其他原因处理
        data = None
    if data is not None and isinstance(e, data.DataError) and e.file:
        # 资料文件读不出：写出是哪个文件（DataError.file）
        return f"资料文件 {e.file} 已损坏，请把压缩包整个重新解压。"
    if old_cpu(e):
        # 处理器太老：重新解压、重装都没用，写清要求（开发版、发行版一样）
        return OLD_CPU
    if isinstance(e, ImportError):
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            # 发行版（Nuitka 打包）里缺库：DLL 加载不了的多半是被拦下了，写出几种可能和办法；其余是文件夹不完整或损坏
            return LOAD_BLOCKED if load_failed(e) else BROKEN_FILES
        # 开发版（不是发行版）缺依赖库：保留给开发者看的安装步骤，只去掉前面的英文原文
        return ("缺少依赖库。请按 requirements.txt 开头的步骤重新安装：\n"
                ".venv\\Scripts\\python -m pip install -r requirements.txt")
    return "拾星助手启动时出错，请重新打开；仍然出错时，请按使用说明「反馈问题」一节反馈。"


def hand_over_update() -> bool:
    """软件内更新（run() 查过防多开以后、起悬浮窗以前调用）：发行版里，上一次下好、核对过的新版本还没换上时
    （advisor/update_apply.py 的 pending），起换版本程序（launch，把本进程号交给它），起来了返回 True：run() 直接返回，
    退出码 0，双击时的启动程序看到 0 安静退出；换版本程序等本进程退出以后换上新版本、启动它。没有要换的、起不来时返回
    False，照常启动；出错时记进错误记录，照常启动。开发版不查，返回 False。"""
    import os
    import traceback

    from .. import paths

    if not paths.is_frozen():
        return False
    try:
        from .. import update_apply, version

        root = paths.program_folder()
        if update_apply.pending(root, version.VERSION) is None:
            return False
        return bool(update_apply.launch(root, old_pid=os.getpid()))
    except Exception:  # noqa: BLE001  换不了就照常启动现在这一版
        log_error("启动时换上新版本出错，照常启动现在的版本", traceback.format_exc(), append=True)
        return False


def take_hidden_start() -> frozenset:
    """软件内更新下好以后自动重启时悬浮窗收在托盘里的记号（hidden_start.take，run() 查过防多开以后、hand_over_update 以前
    调用）：发行版里读走、删掉，返回这一次要收在托盘里启动的几张；没有、对不上时为空。开发版不读，返回空。出错时记进错误
    记录，返回空（照常显示）。在 hand_over_update 以前读：这次启动转去换上次没换上的版本时，记号已经删了，换好以后照常显示。"""
    import traceback

    from .. import paths

    if not paths.is_frozen():
        return frozenset()
    try:
        from .. import version
        from . import hidden_start

        return hidden_start.take(paths.program_folder(), version.VERSION)
    except Exception:  # noqa: BLE001  读不了就照常显示
        log_error("读「更新后收在托盘里启动」的记号出错，照常显示悬浮窗", traceback.format_exc(), append=True)
        return frozenset()


def run() -> None:
    """python -m advisor.ui 的入口（发行版 ACC.exe 同样调用它）：启动悬浮窗，失败时写文件并弹窗，而不是悄无声息地退出。

    一开始先从环境变量里取走「悬浮窗出来了」的事件名（startup.take_ready_event），之后起的子进程继承不到。

    带 PREVIEW_ARG 启动的是正在运行的拾星助手为画皮肤缩略图另起的子进程：在查防多开之前分出去，画完就退出，出错也只写在
    错误输出里（由主程序记进错误记录），不弹窗。它不读设置文件，界面语言照主进程放进环境变量的语言定（lang.init_from_env）。

    带 --wait-pid 旧进程号启动的是设置窗口的「现在重开」起的新进程（advisor/ui/relaunch.py）：在查防多开之前先等旧进程
    退出，最多 DEFAULT_WAIT_S 秒；等不到也照常往下，拿不到防多开的锁就和平时第二次双击一样。

    再查是否已有一个拾星助手在运行（single_instance.claim，开发版和发行版算同一个）：有就请它显示悬浮窗，本进程直接返回
    （退出码 0），不导入界面库，不弹窗、不写任何文件。查的时候出错不拦，照常启动。

    发行版里，上一次下好、核对过的新版本还没换上时，起换版本程序、本进程直接返回（hand_over_update）；起不来照常启动。在这之前
    读走软件内更新自动重启时留的「收在托盘里启动」的记号（take_hidden_start），交给悬浮窗（main 的 start_hidden）。

    换上新版本这一步做完、导入界面模块以前定这个进程的界面语言（lang.init）：设置文件里选过的，没选过时按 Windows 的显示
    语言。界面文字多是各模块导入时就拷走的常量，所以要在导入悬浮窗以前定。不能放到查防多开以前：找设置文件的位置时可能
    把退回用的设置文件写回个人目录，马上要退出的第二个实例不能碰它（lang.py 的模块说明）。定的时候出错（设置文件坏得一读就
    报错，例如嵌套太深的 JSON）不拦：按 Windows 的显示语言照常启动，坏文件由之后读设置的地方另存、拿备份恢复。

    之后打开崩溃记录（开发版；发行版的入口已经打开了），把发行版的入口去掉了的 Qt 环境变量记进错误记录，显示悬浮窗之前
    把识别要用的库加载一遍（startup.preload），再起悬浮窗。
    启动失败时按原因弹窗（report_startup_error），弹过窗就以 EXIT_REPORTED 退出：发行版根目录的启动程序看到它不再另弹窗。
    悬浮窗起来以后的退出（Exited）照旧带原来的退出码，不弹窗。走完时清空崩溃记录里不是崩溃的记录（startup.clear_crash_log）。"""
    import traceback

    from . import startup

    startup.take_ready_event()
    if PREVIEW_ARG in sys.argv[1:]:
        from . import lang

        lang.init_from_env()  # 子进程不读设置文件：照主进程放进环境变量的语言（lang.child_env）
        from .preview_child import child_main

        sys.exit(child_main())
    from . import relaunch

    waited = relaunch.waited_pid(sys.argv[1:])
    if waited is not None:
        # 「现在重开」起的新进程：先等旧进程退出（它还在写设置、放防多开的锁），等不到照常往下
        relaunch.wait_for_exit(waited)
    try:
        from . import single_instance

        if not single_instance.claim():
            return
    except Exception:  # noqa: BLE001  查不了时不拦
        log_error("检查是否已有拾星助手在运行时出错，照常启动", traceback.format_exc(), append=True)
    start_hidden = take_hidden_start()
    if hand_over_update():
        return
    from . import lang

    try:
        lang.init()  # 导入界面模块以前定好界面语言（见上面的说明）
    except Exception:  # noqa: BLE001  定不了时按 Windows 的显示语言照常启动（见上面的说明）
        lang.CURRENT = lang.system_language()
        log_error("读设置文件里的界面语言时出错，按 Windows 的显示语言启动", traceback.format_exc(), append=True)
    startup.open_crash_log()
    startup.log_dropped_env()
    try:
        from .overlay_parts.app import main

        startup.preload()
        if start_hidden:
            main(start_hidden=start_hidden)  # 自动重启前收在托盘里的几张，这一次照旧收着启动
        else:
            main()
    except SystemExit as e:
        if isinstance(e, Exited) or e.code in (None, 0):
            raise
        summary = e.code if isinstance(e.code, str) else f"程序异常退出（退出码 {e.code}）"
        report_startup_error(summary, summary)
        sys.exit(EXIT_REPORTED)
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001  任何错误都要让用户看到
        report_startup_error(startup_summary(e), traceback.format_exc())
        sys.exit(EXIT_REPORTED)
    finally:
        startup.clear_crash_log()
