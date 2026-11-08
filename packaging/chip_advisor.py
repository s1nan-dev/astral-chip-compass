"""发行版的入口：Nuitka 从这里开始编译（见 tools/build_release.py），开发时不用它。
编译出的程序叫 ACC.exe（英文名 Astral Chip Compass 的简写，进程名相同），放在发行版文件夹 AstralChipCompass 的 app 子文件夹
里；发行版根目录的拾星助手.exe（文件名是工具的中文名）是启动程序（packaging/launcher.c），把收到的参数原样转交给它。

  ACC.exe              启动悬浮窗（和开发时的 python -m advisor.ui 相同）
  ACC.exe --selfcheck  不开悬浮窗，检查发行版是否完整（数据、文字识别模型、程序自己画的文字图读不读得出、界面库、界面翻译、
                       窗口截取组件、解锁记录的密钥加载上没有），结果写到程序目录下的 .tmp\\selfcheck.json，退出码 0 表示通过
  ACC.exe --selfcheck --vision-samples <清单.json>
                       自检时另把清单里的样本截图逐张识别一遍（清单里是每张图的路径和这张图读的时候需要的已知条件），
                       读数写到程序目录下的 .tmp\\selfcheck-vision.json。打包试启动用它把发行版和源码的识别读数
                       逐项比（见 advisor/vision/readout.py、tools/build_release.py）；不带这个参数时自检不识别样本
  ACC.exe --skin-previews <JSON>
                       隐藏的参数，只供程序内部使用：正在运行的拾星助手另起子进程画「皮肤」窗口的缩略图时带它
                       （advisor.ui.PREVIEW_ARG，经 run() 分到 preview_child.child_main），画好的图经标准输出交回，
                       不查防多开、不起悬浮窗、不弹窗
  ACC.exe --apply-update <进程号>（或 --apply-update=<进程号>）
                       隐藏的参数，只供软件内更新使用：下好的新版本（发行版文件夹里 update\\new\\app 下的 ACC.exe）带它
                       把旧版本换掉，进程号是要等它退出的旧版本。main() 一开头就接住（两种写法都认，同
                       advisor.update_apply._old_pid），以 advisor.update_apply.apply_main 的返回值退出（做法见那里）：
                       不开崩溃记录、不去 Qt 环境变量、不导入界面库和识别库、不查防多开

导入 advisor 之前先做两件不依赖它的事（只在编出来的发行版里做，从源码运行时都不做；换版本程序两件都不做）：
- 打开崩溃记录（程序目录下的 .tmp\\crash.txt，_open_crash_log）：导入 advisor 时就崩的也记得下。做法同
  advisor/ui/startup.py 的 open_crash_log（这里还不能导入 advisor，另写了一份；位置和做法由 tests/test_startup_guard.py
  核对一致）。画缩略图的子进程不开：它自己把崩溃写到标准错误输出，由主程序记进错误记录。自检走完时（照常结束，或者
  Python 里出了错）清空它（_clear_crash，同 run() 走完时）：里面这时有的只可能是别的库自己接住的异常；自检中途真的崩溃时
  走不到这一步，记录留着。
- 去掉别的软件设的 Qt 环境变量（QT_ENV_DROP）：它们会让发行版加载外来的插件崩掉、找不到平台插件起不来，或者悬浮窗的大小、
  字号、方向变样。去掉了哪些，导入 advisor 以后记进错误记录（只记名字；起悬浮窗时由 run() 查过防多开以后记）。
导入 advisor 出错（程序文件缺失、损坏，或被杀毒软件拦了）：调用栈写进崩溃记录，弹「启动失败」窗口后以 EXIT_REPORTED 退出；
连 ctypes 都用不了（例如 _ctypes.pyd 被杀毒软件删了）时弹不了窗，以 1 退出，交给根目录的启动程序报。
"""
import faulthandler
import os
import sys
import time

FROZEN = "__compiled__" in globals()  # Nuitka 编出来的发行版
VISION_SAMPLES_ARG = "--vision-samples"  # 带样本清单自检（--selfcheck）时用；值就是清单文件
PREVIEW_ARG = "--skin-previews"  # 画皮肤缩略图的子进程（同 advisor.ui.PREVIEW_ARG）
APPLY_ARG = "--apply-update"  # 换版本程序（同 advisor.update_apply.APPLY_ARG）
# 自己弹过「启动失败」窗口以后的退出码（同 advisor.errors.EXIT_REPORTED）：根目录的启动程序看到它不再另弹窗
EXIT_REPORTED = 0xACC3
CRASH_NAME = "crash.txt"  # 崩溃记录（同 advisor.ui.startup.CRASH_NAME）
CRASH_OLD_NAME = "crash.old.txt"  # 上一次崩溃的记录（同 advisor.ui.startup.CRASH_OLD_NAME）
# 别的软件（或给别的 Qt 程序调界面）设的 Qt 环境变量，发行版启动时去掉。打包脚本的试启动按这里的源码读出同一份名单
# （tools/build_release.py 的 entry_qt_env_drop，不导入本文件），在试启动的环境里也去掉，所以要一直写成字面的元组：
QT_ENV_DROP = (
    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",  # 到别处加载插件：别的版本的 Qt 插件会让程序崩掉
    "QT_QPA_PLATFORM",  # 换平台插件：发行版只带了 Windows 的那一个，换成别的起不来
    "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_ENABLE_HIGHDPI_SCALING", "QT_SCALE_FACTOR_ROUNDING_POLICY",
    "QT_USE_PHYSICAL_DPI", "QT_DPI_ADJUSTMENT_POLICY", "QT_FONT_DPI",  # 缩放和字号：悬浮窗变大变小、字号不对
    "QT_STYLE_OVERRIDE",  # 换界面风格：下拉框、滚动条变样
    "QT_LAYOUT_DIRECTION",  # 整个界面左右翻过来
    "QT_WIDGETS_RHI", "QT_WIDGETS_RHI_BACKEND",  # 换掉窗口的绘制方式（透明的悬浮窗可能画不出来）
    "QT_FATAL_WARNINGS", "QT_FATAL_CRITICALS",  # Qt 一报警告就结束程序
    "QT_WIN_DEBUG_CONSOLE",  # 另开一个控制台窗口
)
# 导入 advisor 出错时弹窗的两句（同 advisor.ui 的 BROKEN_FILES、LOAD_BLOCKED），认原因用的报错原文同 advisor.ui.LOAD_FAILED_MARKS
BROKEN_FILES = "程序文件不完整或已损坏，请把压缩包整个重新解压。"
LOAD_BLOCKED = ("程序文件加载失败，可能被杀毒软件或 Windows 的「智能应用控制」拦截，也可能解压不完整。"
                "请在杀毒软件中把拾星助手文件夹设为信任、从隔离区恢复被隔离的文件，或把压缩包整个重新解压。")
LOAD_FAILED_MARKS = ("DLL load failed", "LoadLibraryExW")
ERROR_TITLE = "拾星助手启动失败"  # 同 advisor.ui.report_startup_error 的弹窗
MB_FLAGS = 0x10 | 0x10000 | 0x40000  # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST（同上）

_crash = None  # 崩溃记录（整个进程期间开着；faulthandler 也拿着它）


def _option(argv: list[str], name: str) -> str | None:
    """取 --名字 <值>（或 --名字=<值>）里的值；没带时为 None。"""
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


def _applying(argv: list[str]) -> bool:
    """带了换版本程序的参数：APPLY_ARG 本身（进程号在下一个参数）或 APPLY_ARG=<进程号>，认法同
    advisor.update_apply._old_pid（进程号对不对由 apply_main 核对，不对时它什么都不改就退出）。"""
    return any(arg == APPLY_ARG or arg.startswith(APPLY_ARG + "=") for arg in argv)


def _app_dir() -> str:
    """程序目录（同 advisor.paths.app_dir）：发行版里 Nuitka 按程序所在的文件夹给各模块定 __file__，入口就在程序目录（app
    文件夹）下，advisor 包在它下面一层；从源码运行时入口在项目根目录的 packaging 文件夹里。"""
    here = os.path.dirname(os.path.realpath(__file__))
    return here if FROZEN else os.path.dirname(here)


def _open_crash_log(folder: str | None = None):
    """打开崩溃记录（folder 默认程序目录下的 .tmp），交给 faulthandler（各线程都写），返回打开的文件；打不开、文件夹写不进时
    为 None，不报错。上一次的记录不是空的（上一次崩溃了）先改名成 CRASH_OLD_NAME（只留一份）；改不了名（另一个拾星助手正开着
    它）时接在后面写，不清掉。"""
    folder = folder or os.path.join(_app_dir(), ".tmp")
    path = os.path.join(folder, CRASH_NAME)
    try:
        os.makedirs(folder, exist_ok=True)
        try:
            if os.path.getsize(path):
                os.replace(path, os.path.join(folder, CRASH_OLD_NAME))
        except OSError:
            pass  # 还没有记录；或者改不了名：接在后面写
        file = open(path, "a", encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        faulthandler.enable(file=file, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        file.close()
        return None
    return file


def _clear_crash() -> None:
    """自检走完时清空崩溃记录（同 advisor.ui.startup.clear_crash_log）：这时还在跑，里面有的只可能是别的库自己接住的异常。
    没开着时不动。"""
    if _crash is None:
        return
    try:
        _crash.flush()
        _crash.truncate(0)
    except (OSError, ValueError):
        pass  # 清不掉只是下次启动多另存一份


def _drop_qt_env() -> list[str]:
    """去掉 QT_ENV_DROP 里设了的环境变量（在导入 PySide6 之前），返回去掉了哪几个（只有名字）。"""
    return [name for name in QT_ENV_DROP if os.environ.pop(name, None) is not None]


def _note_qt_env(removed: list[str]) -> None:
    """自检：去掉了 Qt 环境变量时马上记进错误记录（只记名字，不记值；写法在 advisor.ui.startup）。记不下也照常自检。"""
    if not removed:
        return
    try:
        from advisor.ui import startup

        startup.remember_dropped_env(removed)
        startup.log_dropped_env()
    except Exception:  # noqa: BLE001
        pass


def _load_failed(error: BaseException) -> bool:
    """扩展模块或它依赖的 DLL 加载不了（同 advisor.ui.load_failed）：报错原文（连带的异常也看）里有 LOAD_FAILED_MARKS，
    或者连带的异常里有原文是空的 ImportError（Nuitka 在中文等系统上拼不出那句原文时就是这样，见 advisor.ui 的说明）。"""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        try:
            text = str(error)
        except Exception:  # noqa: BLE001
            text = ""
        if any(mark in text for mark in LOAD_FAILED_MARKS) or (isinstance(error, ImportError) and not text.strip()):
            return True
        error = error.__cause__ or error.__context__
    return False


def _write_crash(detail: str) -> str:
    """把导入出错的调用栈写进崩溃记录，返回弹窗末尾那句（同错误记录的写法：写在哪里，写不进时只写中文一句）。"""
    if _crash is not None:
        try:
            _crash.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {ERROR_TITLE}：导入程序模块出错\n{detail.rstrip()}\n\n")
            _crash.flush()
            return f"详细信息已写入：\n{_crash.name}"
        except (OSError, ValueError):
            pass
    if sys.stderr:
        print(detail, file=sys.stderr)
    return "（错误信息也未能写入错误记录）"


def _import_failed(error: BaseException) -> None:
    """导入 advisor 出错：调用栈写进崩溃记录，弹「启动失败」窗口，以 EXIT_REPORTED 退出；连 ctypes 都用不了时以 1 退出。"""
    try:
        import traceback

        detail = "".join(traceback.format_exception(error))
    except Exception:  # noqa: BLE001  连调用栈都整理不了时只记类名和原文
        detail = f"{type(error).__name__}: {error}"
    where = _write_crash(detail)
    text = LOAD_BLOCKED if _load_failed(error) else BROKEN_FILES
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, f"{text}\n\n{where}", ERROR_TITLE, MB_FLAGS)
    except Exception:  # noqa: BLE001  弹不了窗：交给根目录的启动程序按退出码报
        sys.exit(1)
    sys.exit(EXIT_REPORTED)


def main() -> None:
    global _crash
    argv = sys.argv[1:]
    if _applying(argv):
        # 换版本程序：在开崩溃记录、去 Qt 环境变量之前接住。它的程序目录是 update\new\app，崩溃记录、错误记录写在这里会
        # 跟着复制成新的 app、挡住用户数据搬过去，所以都不写，每一步记在 update\apply.log。导入出错时不弹窗（它在后台运行，
        # 旧版本还开着），以 1 退出，起它的旧版本记成这次没换上
        from advisor.update_apply import apply_main
        sys.exit(apply_main(argv))
    removed: list[str] = []
    if FROZEN:
        if PREVIEW_ARG not in argv:
            _crash = _open_crash_log()
        removed = _drop_qt_env()
    if "--selfcheck" in argv:
        try:
            from advisor.diagnostics import self_check_main
        except Exception as e:  # noqa: BLE001  程序文件缺失、损坏，或被拦了
            _import_failed(e)
        _note_qt_env(removed)
        manifest = _option(argv, VISION_SAMPLES_ARG)
        try:
            # 不带样本清单时不传参数（只做完整性检查）
            code = self_check_main() if manifest is None else self_check_main(manifest)
        finally:
            _clear_crash()  # 自检走完（真的崩溃时走不到这里，记录留着）
        sys.exit(code)
    try:
        from advisor.ui import run, startup
    except Exception as e:  # noqa: BLE001  同上
        _import_failed(e)
    startup.adopt_crash_log(_crash)  # 照常退出时由 run() 清空里面不是崩溃的记录
    if PREVIEW_ARG not in argv:
        # 去掉了哪些由 run() 查过防多开以后记进错误记录（再次启动、只请已在运行的那个显示悬浮窗的进程不写）；
        # 画缩略图的子进程不写任何文件
        startup.remember_dropped_env(removed)
    run()


if __name__ == "__main__":
    main()
