"""启动时的检查：发行版入口 packaging/chip_advisor.py、advisor/ui/__init__.py 的 run() 和 advisor/ui/startup.py。

  约定      自己弹过「启动失败」窗口以后的退出码是 0xACC3（和根目录的启动程序约好的），入口里另写的那一份和 advisor.errors
            的相等；入口导入 advisor 之前就要用的几样（缩略图子进程的参数、崩溃记录的文件名、弹窗的字）和 advisor 里的相同
  崩溃记录  入口和 startup 算出来的位置和 paths 的一致（开发目录、发行版的 app 文件夹）；上一次的不是空的先另存成
            crash.old.txt，改不了名时接在后面写，文件夹写不进时不开；真的崩溃写得进、下次启动另存；被别的库自己接住的异常
            （假崩溃）也会记下，照常退出时清空；运行中超过上限就停用、错误记录里记一笔（只记一次），没超过不动。崩溃、
            假崩溃、真的停用在子进程里做（faulthandler 是全进程的）
  入口      从源码运行时不碰崩溃记录和环境变量；发行版里先开崩溃记录（缩略图子进程不开）、去掉别的软件设的 Qt 环境变量并记一笔
            （只记名字）；导入 advisor 出错时把调用栈写进崩溃记录、弹「启动失败」窗口（DLL 加载不了时写可能被拦截），以 0xACC3
            退出，连 ctypes 都用不了时以 1 退出；自检走完时清空崩溃记录，自检中途真的崩溃时留着；换版本程序（--apply-update，
            也认 --apply-update=<进程号>，同 update_apply._old_pid）一开头就交给 update_apply.apply_main、以它的返回值退出，
            崩溃记录、环境变量、悬浮窗都不碰
  run()     查过防多开再开崩溃记录、预加载识别要用的库、起悬浮窗，走完清空崩溃记录；弹过启动失败的窗口以后一律以 0xACC3 退出
            （含 SystemExit 带文字、带别的退出码的），悬浮窗起来以后的退出（Exited）照旧
  预加载    按 PRELOAD 的先后导入、出错照常抛出；它导入的覆盖识别线程和文字识别要用的全部第三方库（子进程里核对）
  出错原因  DLL 加载不了（开发版、发行版两种写法，包在别的错里的也算；发行版在中文等系统上没有原文的空 ImportError 也算）、
            处理器太老（numpy 的原文），分别写中文
  信号      run() 一开始取走 ACC_READY_EVENT；悬浮窗显示、开始等「显示」请求以后、事件循环开始之前排好定时器，事件循环
            转起来约五秒以后才触发它（真的建一个命名事件、在离屏的事件循环里核对）；不是经启动程序启动的不建定时器；
            没有这个事件不报，打不开时记一笔

不起悬浮窗、不开窗口、不弹窗：入口和 run() 里的悬浮窗、弹窗都换成假的，子进程只跑几行 Python。
"""
from __future__ import annotations

import builtins
import ctypes
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import advisor.ui as ui
from advisor import errors, paths
from advisor.ui import Exited, startup
from advisor.ui import single_instance as si
from advisor.ui.overlay_parts import app as overlay_app
from tests.support import sandbox
from tests.support.errors import ErrorLog, capture_errors

PROJECT = Path(__file__).resolve().parents[1]
ENTRY_FILE = PROJECT / "packaging" / "chip_advisor.py"
WINDOWS = os.name == "nt"
GENERIC = "拾星助手启动时出错，请重新打开；仍然出错时，请按使用说明「反馈问题」一节反馈。"
DEV_MISSING = "缺少依赖库。请按 requirements.txt 开头的步骤重新安装：\n.venv\\Scripts\\python -m pip install -r requirements.txt"
NUMPY_OLD_CPU = ("NumPy was built with baseline optimizations: \n(X86_V2) but your machine doesn't support:\n"
                 "(SSE42 POPCNT).")
QT_NOTE_TITLE = "启动时去掉了别的软件设的 Qt 环境变量（拾星助手照常启动）"


def load_entry():
    """按文件载入发行版的入口（从源码运行：FROZEN 为假）。"""
    spec = importlib.util.spec_from_file_location("chip_advisor_entry", ENTRY_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def qt_env(**values):
    """环境变量里 QT_ENV_DROP 那几个先都去掉（跑测试时设了 QT_QPA_PLATFORM），再设上 values；离开时整个换回。"""
    entry = load_entry()
    with mock.patch.dict(os.environ):
        for name in entry.QT_ENV_DROP:
            os.environ.pop(name, None)
        os.environ.update(values)
        yield


def blocked_import(failures: dict):
    """导入 failures 里的模块（绝对导入）时抛给定的异常，别的照常导入：模拟程序文件缺失、被拦，不动 sys.modules。"""
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in failures:
            raise failures[name]
        return real(name, globals, locals, fromlist, level)

    return mock.patch.object(builtins, "__import__", fake)


def chained(outer: BaseException, inner: BaseException) -> BaseException:
    """outer 由 inner 引起（raise outer from inner）。"""
    try:
        try:
            raise inner
        except BaseException as e:
            raise outer from e
    except BaseException as e:  # noqa: BLE001
        return e


class ContractTest(unittest.TestCase):
    def test_exit_reported(self):
        """弹过窗以后的退出码：0xACC3（和根目录的启动程序约好的）；入口导入不了 advisor 时用的那一份和 advisor.errors 的相等，
        advisor.ui 上转引的是同一个。"""
        entry = load_entry()
        self.assertEqual(errors.EXIT_REPORTED, 0xACC3)
        self.assertEqual(entry.EXIT_REPORTED, errors.EXIT_REPORTED)
        self.assertIs(ui.EXIT_REPORTED, errors.EXIT_REPORTED)

    def test_entry_copies_match(self):
        """入口在导入 advisor 之前就要用、所以另写了一份的几样，和 advisor 里的相同。"""
        from advisor import update_apply

        entry = load_entry()
        self.assertEqual(entry.PREVIEW_ARG, ui.PREVIEW_ARG)
        self.assertEqual(entry.APPLY_ARG, update_apply.APPLY_ARG)
        self.assertEqual((entry.CRASH_NAME, entry.CRASH_OLD_NAME), (startup.CRASH_NAME, startup.CRASH_OLD_NAME))
        self.assertEqual((entry.CRASH_NAME, entry.CRASH_OLD_NAME), ("crash.txt", "crash.old.txt"))
        self.assertEqual(entry.BROKEN_FILES, ui.BROKEN_FILES)
        self.assertEqual(entry.LOAD_BLOCKED, ui.LOAD_BLOCKED)
        self.assertEqual(entry.LOAD_FAILED_MARKS, ui.LOAD_FAILED_MARKS)
        self.assertEqual(entry.ERROR_TITLE, "拾星助手启动失败")
        self.assertEqual(entry.MB_FLAGS, ui.MB_ICONERROR | ui.MB_SETFOREGROUND | ui.MB_TOPMOST)
        # 弹窗的标题和 report_startup_error 用的相同
        with mock.patch.object(ui, "ctypes") as fake, mock.patch("sys.stderr", new_callable=io.StringIO):
            capture_errors(self)
            ui.report_startup_error(ui.BROKEN_FILES, "调用栈")
        self.assertEqual(fake.windll.user32.MessageBoxW.call_args.args[2], entry.ERROR_TITLE)

    def test_load_failed_same_in_entry(self):
        """认「DLL 加载不了」的两份写法（入口的 _load_failed、advisor.ui.load_failed）对同一批报错结论相同。"""
        entry = load_entry()
        cases = (
            ImportError("LoadLibraryExW 'x.pyd' failed: 拒绝访问。"), ImportError("DLL load failed while importing x"),
            ImportError(), ImportError("  "), ImportError("No module named 'x'"), RuntimeError(), OSError("DLL load failed"),
            chained(ImportError("cannot import name 'x'"), ImportError()), chained(RuntimeError("外层"), ImportError("内层")),
        )
        for error in cases:
            with self.subTest(error=repr(error)):
                self.assertEqual(entry._load_failed(error), ui.load_failed(error))

    def test_ready_env_name(self):
        """「悬浮窗出来了」的事件名放在这个环境变量里（和根目录的启动程序约好的）。"""
        self.assertEqual(startup.READY_ENV, "ACC_READY_EVENT")

    def test_same_as_launcher_source(self):
        """两边各写一份的约定（C 和 Python 共用不了常量），按启动程序的源码直接比：launcher.c 的 EXIT_REPORTED 和
        advisor.errors、打包脚本的 LAUNCHER_REPORTED 相等，READY_VAR 就是 startup.READY_ENV。只改了一边（连带改了那一边测试里
        写死的数）时，这里报出来。"""
        import re

        from tools import build_release

        source = (PROJECT / "packaging" / "launcher.c").read_text(encoding="utf-8")

        def define(name: str) -> str:
            found = re.search(rf"^#define {name}\s+(\S+)", source, re.M)
            self.assertIsNotNone(found, f"launcher.c 里没有 #define {name}")
            return found.group(1)

        self.assertEqual(int(define("EXIT_REPORTED"), 16), errors.EXIT_REPORTED)
        self.assertEqual(build_release.LAUNCHER_REPORTED, errors.EXIT_REPORTED)
        self.assertEqual(define("READY_VAR"), f'L"{startup.READY_ENV}"')


class CrashPathTest(unittest.TestCase):
    def test_dev_layout(self):
        """开发目录：入口自己算的、startup 的崩溃记录都在 paths.temp_dir() 下（和错误记录同一个文件夹）。只算路径，不写文件。"""
        entry = load_entry()
        with sandbox.real_paths():
            want = paths.temp_dir() / "crash.txt"
            self.assertEqual(startup.crash_path(), want)
            self.assertEqual(Path(entry._app_dir()) / ".tmp" / entry.CRASH_NAME, want)
            self.assertEqual(Path(errors.ERR_FILE).name, "overlay_err.txt")
        self.assertEqual(startup.crash_path(), paths.temp_dir() / startup.CRASH_NAME)  # 测试里在沙箱

    def test_release_layout(self):
        """发行版：Nuitka 按程序所在的文件夹给各模块定 __file__（入口在 app 文件夹下，advisor/paths.py 在它下面一层）。
        用 paths.py 的源码按发行版的 __file__ 再算一遍程序目录，和入口在发行版里（FROZEN）自己算的相同。"""
        app = sandbox.temp_dir(self) / "AstralChipCompass" / "app"
        (app / "advisor").mkdir(parents=True)
        spec = importlib.util.spec_from_file_location("advisor._paths_release_probe", PROJECT / "advisor" / "paths.py")
        probe = importlib.util.module_from_spec(spec)
        probe.__file__ = str(app / "advisor" / "paths.py")
        sys.modules[spec.name] = probe  # dataclass 要按模块名找到它；算完就拿掉
        try:
            spec.loader.exec_module(probe)
        finally:
            sys.modules.pop(spec.name, None)
        entry = load_entry()
        with mock.patch.object(entry, "FROZEN", True), mock.patch.object(entry, "__file__", str(app / "chip_advisor.py")):
            got = Path(entry._app_dir())
        self.assertEqual(probe.app_dir(), app.resolve())
        self.assertEqual(got, probe.app_dir())
        self.assertEqual(got / ".tmp" / entry.CRASH_NAME, probe.temp_dir() / startup.CRASH_NAME)


class CrashLogOpenTest(unittest.TestCase):
    """打开崩溃记录的做法（入口和 startup 各一份，一样核对）。faulthandler 换成假的：不改测试进程自己的崩溃输出。"""

    def setUp(self):
        self.folder = sandbox.temp_dir(self)
        self.crash = self.folder / "crash.txt"
        self.old = self.folder / "crash.old.txt"
        self.entry = load_entry()

    def tearDown(self):
        for file in (getattr(self, "_opened", None), startup._crash_file):
            if file is not None:
                file.close()
        startup._crash_file = None

    def open_with(self, kind: str, folder: Path):
        """按 kind（entry / startup）打开 folder 里的崩溃记录，faulthandler 当作没开着。返回（结果, 假的 enable）。"""
        module = self.entry if kind == "entry" else startup
        with mock.patch.object(module.faulthandler, "is_enabled", return_value=False), \
                mock.patch.object(module.faulthandler, "enable") as enable:
            if kind == "entry":
                result = self.entry._open_crash_log(str(folder))
                self._opened = result
            else:
                result = startup.open_crash_log(folder)
        return result, enable

    def test_previous_crash_moved_aside(self):
        """上一次的记录不是空的：改名成 crash.old.txt（原来的 old 被换掉，只留一份），新开一个空的接着写；空的不另存。"""
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind):
                self.tearDown()
                self.old.write_text("更早的一次", encoding="utf-8")
                self.crash.write_text("Windows fatal exception: access violation\n", encoding="utf-8")
                result, enable = self.open_with(kind, self.folder)
                self.assertIsNotNone(result)
                self.assertEqual(self.old.read_text(encoding="utf-8"), "Windows fatal exception: access violation\n")
                self.assertEqual(self.crash.read_bytes(), b"")
                enable.assert_called_once()
                self.assertTrue(enable.call_args.kwargs["all_threads"])
                self.assertEqual(Path(enable.call_args.kwargs["file"].name), self.crash)
                self.tearDown()
                result, _ = self.open_with(kind, self.folder)  # 这次是空的：不另存，old 还是上一次崩溃的
                self.assertIsNotNone(result)
                self.assertEqual(self.old.read_text(encoding="utf-8"), "Windows fatal exception: access violation\n")

    def test_previous_crash_scrubbed(self):
        """另存的 crash.old.txt 里的用户目录、账户名换成占位（faulthandler 把非 ASCII 字符写成 \\uXXXX，这种写法也换），
        换行照原样：开发版在 open_crash_log 里换；发行版由入口改名（入口还不能导入 advisor，只改名），交给
        adopt_crash_log 时换。"""
        env = {"USERNAME": "张三", "USERPROFILE": "C:\\Users\\张三", "APPDATA": "C:\\Users\\张三\\AppData\\Roaming",
               "LOCALAPPDATA": "C:\\Users\\张三\\AppData\\Local"}
        raw = ('Fatal Python error: Segmentation fault\n\n'
               '  File "C:\\Users\\\\u5f20\\u4e09\\Desktop\\app\\x.py", line 2 in main\n').encode("utf-8")
        want = ('Fatal Python error: Segmentation fault\n\n'
                '  File "%USERPROFILE%\\Desktop\\app\\x.py", line 2 in main\n').encode("utf-8")
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind), mock.patch.dict(os.environ, env):
                self.tearDown()
                self.crash.write_bytes(raw)
                result, _ = self.open_with(kind, self.folder)
                self.assertIsNotNone(result)
                if kind == "entry":
                    self.assertEqual(self.old.read_bytes(), raw)
                    startup.adopt_crash_log(result)
                self.assertEqual(self.old.read_bytes(), want)
                self.assertEqual(self.crash.read_bytes(), b"")

    def test_locked_record_appended(self):
        """另一个拾星助手正开着它（改不了名）：不另存、不清掉，接在后面写。"""
        self.crash.write_text("别的进程记下的\n", encoding="utf-8")
        with open(self.crash, "a", encoding="utf-8"):  # 像另一个进程那样开着（不许改名）
            for kind in ("entry", "startup"):
                with self.subTest(kind=kind):
                    self.tearDown()
                    result, enable = self.open_with(kind, self.folder)
                    self.assertIsNotNone(result)
                    enable.assert_called_once()
                    self.assertFalse(self.old.exists())
                    self.assertEqual(self.crash.read_text(encoding="utf-8"), "别的进程记下的\n")

    def test_unwritable_folder_quiet(self):
        """文件夹写不进（这里是同名的文件挡着）：不打开、不报错、不开 faulthandler。"""
        blocker = self.folder / "挡着的文件"
        blocker.write_text("", encoding="utf-8")
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind):
                result, enable = self.open_with(kind, blocker / ".tmp")
                self.assertIsNone(result)
                enable.assert_not_called()

    def test_already_enabled_untouched(self):
        """faulthandler 已经开着（发行版的入口开过、开发时 -X faulthandler、测试）：startup 不再打开，不建文件。"""
        with mock.patch.object(startup.faulthandler, "is_enabled", return_value=True), \
                mock.patch.object(startup.faulthandler, "enable") as enable:
            self.assertIsNone(startup.open_crash_log(self.folder))
        enable.assert_not_called()
        self.assertFalse(self.crash.exists())
        self.assertIsNone(startup._crash_file)
        startup.clear_crash_log()  # 本进程没开着崩溃记录：不动文件
        self.assertFalse(self.crash.exists())


class CrashLimitTest(unittest.TestCase):
    """崩溃记录在运行中的上限（check_crash_log、watch_crash_log）。faulthandler.disable 换成假的：不停用测试进程自己的
    崩溃输出（真的停用在子进程里核对，见 CrashLogProcessTest）。"""

    # 剪贴板这类 COM 调用里别的库自己接住的异常，faulthandler 记下的样子
    RECORD = ("Windows fatal exception: code 0x8001010d\n\nThread 0x00002a3c (most recent call first):\n"
              '  File "D:\\AstralChipCompass\\app\\advisor\\ui\\scheme_share.py", line 1 in copy\n\n')

    def setUp(self):
        self.path = sandbox.temp_dir(self) / "crash.txt"
        self.log = capture_errors(self)
        self.addCleanup(self.reset)

    def reset(self):
        timer = startup._crash_timer
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if startup._crash_file is not None:
            startup._crash_file.close()
        startup._crash_file, startup._crash_full, startup._crash_timer = None, False, None

    def open_log(self, text: str) -> None:
        """崩溃记录里先有 text，像本进程开着它那样交给 startup。"""
        self.path.write_text(text, encoding="utf-8")
        startup._crash_file = open(self.path, "a", encoding="utf-8")

    @staticmethod
    def run_loop(app, seconds: float, until=lambda: False) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not until():
            app.processEvents()
            time.sleep(0.01)

    def test_limits(self):
        self.assertEqual((startup.CRASH_LIMIT, startup.CRASH_CHECK_MS), (512 * 1024, 10_000))

    def test_under_limit_untouched(self):
        """没超过上限：不停用、不记，文件不动。"""
        self.open_log(self.RECORD)
        with mock.patch.object(startup.faulthandler, "disable") as disable:
            self.assertFalse(startup.check_crash_log(limit=1024))
        disable.assert_not_called()
        self.assertFalse(startup._crash_full)
        self.assertEqual(self.log.entries(), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.RECORD)

    def test_over_limit_disabled_logged_once(self):
        """超过上限：停用这次运行的崩溃记录，错误记录里记一笔大小和第一条异常那一行，只记一次；文件留着，照常退出时照旧清空。"""
        text = self.RECORD * 40
        self.open_log(text)
        size = self.path.stat().st_size
        self.assertGreater(size, 1024)
        with mock.patch.object(startup.faulthandler, "disable") as disable:
            self.assertTrue(startup.check_crash_log(limit=1024))
            self.assertTrue(startup.check_crash_log(limit=1024))  # 再看一次：不再停用、不再记
        disable.assert_called_once_with()
        (title, detail), = self.log.entries()
        self.assertEqual(title, startup.CRASH_FULL_TITLE)
        self.assertIn(f"已有 {size // 1024} KB，超过上限 1 KB", detail)
        self.assertIn("第一条：Windows fatal exception: code 0x8001010d", detail)
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)  # 文件留着
        startup.clear_crash_log()
        self.assertEqual(self.path.read_bytes(), b"")

    def test_first_exception_line(self):
        """第一条异常那一行：前面有空行、别的字时也找得到；没有异常的开头行时取第一行不空的；读不出时为空。"""
        cases = (("\n\nFatal Python error: Aborted\n\nCurrent thread 0x1 (most recent call first):\n",
                  "Fatal Python error: Aborted"),
                 ("别的字\nWindows fatal exception: access violation\n", "Windows fatal exception: access violation"),
                 ("\n  一行说明  \n第二行\n", "一行说明"), ("", ""))
        for text, want in cases:
            with self.subTest(text=text[:20]):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(startup._first_exception_line(self.path), want)
        self.assertEqual(startup._first_exception_line(self.path.parent / "没有这个文件.txt"), "")

    def test_no_crash_log(self):
        """本进程没开着崩溃记录（开发时 -X faulthandler、测试）：不看、不停用、不记，也不建定时器。"""
        with mock.patch.object(startup.faulthandler, "disable") as disable:
            self.assertTrue(startup.check_crash_log(limit=0))
            self.assertIsNone(startup.watch_crash_log(interval_ms=10, limit=0))
        disable.assert_not_called()
        self.assertIsNone(startup._crash_timer)
        self.assertEqual(self.log.entries(), [])

    def test_watch_stops_when_full(self):
        """界面线程定时看（离屏的事件循环）：超过上限的那一次停用、记一笔，定时器停下；只建一个定时器。"""
        from tests.support import qtenv

        app = qtenv.app()
        self.open_log(self.RECORD * 40)
        with mock.patch.object(startup.faulthandler, "disable") as disable:
            timer = startup.watch_crash_log(interval_ms=20, limit=1024)
            self.assertIsNotNone(timer)
            self.assertTrue(timer.isActive())
            self.assertIsNone(startup.watch_crash_log(interval_ms=20, limit=1024))  # 已经在看
            self.run_loop(app, 5, until=lambda: startup._crash_full)
            self.run_loop(app, 0.2)
        disable.assert_called_once_with()
        self.assertFalse(timer.isActive())
        self.assertEqual([title for title, _ in self.log.entries()], [startup.CRASH_FULL_TITLE])

    def test_watch_under_limit_keeps_going(self):
        """没超过上限：定时器照常转，不停用、不记。"""
        from tests.support import qtenv

        app = qtenv.app()
        self.open_log(self.RECORD)
        with mock.patch.object(startup.faulthandler, "disable") as disable:
            timer = startup.watch_crash_log(interval_ms=20, limit=1024)
            self.run_loop(app, 0.3)
        disable.assert_not_called()
        self.assertTrue(timer.isActive())
        self.assertEqual(self.log.entries(), [])


# 子进程里跑的脚本。参数：项目根目录、哪一份（entry / startup）、崩溃记录所在的文件夹、做什么：
#   none        开了就退出；crash  开了以后崩溃（faulthandler._sigsegv，不弹系统的错误窗口）；
#   fake        开了以后让系统函数自己接住一次访问违例（IsBadReadPtr 试读一个读不了的地址）再照常退出；
#   fake-clear  同 fake，退出前照 run() 走完时的做法清空（入口那一份先交给 startup，同发行版）；
#   full        反复假崩溃到崩溃记录超过 2 KB，照运行中的定时检查看一次（check_crash_log），再假崩溃一次，最后一行打印
#               各时刻的大小。子进程没有测试沙箱，错误记录改写到同一个文件夹
CHILD = r"""
import ctypes, faulthandler, importlib.util, json, sys
root, kind, folder, action = sys.argv[1:5]
sys.path.insert(0, root)
if kind == "entry":
    spec = importlib.util.spec_from_file_location("chip_advisor_entry", root + r"\packaging\chip_advisor.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    file = entry._open_crash_log(folder)
    from advisor.ui import startup
    startup.adopt_crash_log(file)
    opened = file is not None
else:
    from advisor.ui import startup
    opened = startup.open_crash_log(folder) is not None
print(json.dumps({"opened": opened, "enabled": faulthandler.is_enabled()}), flush=True)
if action == "crash":
    faulthandler._sigsegv()
elif action in ("fake", "fake-clear"):
    ctypes.windll.kernel32.IsBadReadPtr(ctypes.c_void_p(0x1000), 1)
    print("still running", flush=True)
    if action == "fake-clear":
        startup.clear_crash_log()
elif action == "full":
    from pathlib import Path
    import advisor.errors
    advisor.errors.ERR_FILE = Path(folder) / "overlay_err.txt"
    path = Path(folder) / "crash.txt"
    for _ in range(500):
        if path.stat().st_size > 2048:
            break
        ctypes.windll.kernel32.IsBadReadPtr(ctypes.c_void_p(0x1000), 1)
    full = path.stat().st_size
    stop = startup.check_crash_log(2048)
    ctypes.windll.kernel32.IsBadReadPtr(ctypes.c_void_p(0x1000), 1)
    print(json.dumps({"full": full, "stop": stop, "after": path.stat().st_size, "enabled": faulthandler.is_enabled()}),
          flush=True)
"""

# 发行版自检的子进程（照发行版走：入口先开崩溃记录）。参数：项目根目录、文件夹（崩溃记录在它的 .tmp 下）、做什么：
#   ok     自检里有库自己接住了一次异常（记进崩溃记录），自检照常走完；
#   crash  同样一次以后，自检中途真的崩溃（读空指针，不弹系统的错误窗口）。
# 自检换成假的；子进程没有测试沙箱，错误记录改写到同一个文件夹。第一行打印自检里那一次记下以后崩溃记录的大小
SELFCHECK_CHILD = r"""
import ctypes, faulthandler, importlib.util, json, sys, types
from pathlib import Path
root, folder, action = sys.argv[1:4]
sys.path.insert(0, root)
import advisor.errors
advisor.errors.ERR_FILE = Path(folder) / "overlay_err.txt"
spec = importlib.util.spec_from_file_location("chip_advisor_entry", root + r"\packaging\chip_advisor.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.FROZEN = True
entry._app_dir = lambda: folder
crash = Path(folder) / ".tmp" / "crash.txt"


def self_check_main():
    ctypes.windll.kernel32.IsBadReadPtr(ctypes.c_void_p(0x1000), 1)
    print(json.dumps({"during": crash.stat().st_size}), flush=True)
    if action == "crash":
        faulthandler._read_null()
    return 0


fake = types.ModuleType("advisor.diagnostics")
fake.self_check_main = self_check_main
sys.modules["advisor.diagnostics"] = fake
sys.argv = ["ACC.exe", "--selfcheck"]
entry.main()
"""


@unittest.skipUnless(WINDOWS, "只在 Windows 上有")
class CrashLogProcessTest(unittest.TestCase):
    """真的在子进程里崩溃、假崩溃一次（入口和 startup 两份都核对）。"""

    def run_child(self, kind: str, folder: Path, action: str) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, "-B", "-c", CHILD, str(PROJECT), kind, str(folder), action],
                              cwd=str(PROJECT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120)
        first = (proc.stdout.splitlines() or ["{}"])[0]
        self.assertEqual(json.loads(first), {"opened": True, "enabled": True}, proc.stdout + proc.stderr)
        return proc

    def test_crash_recorded_and_kept(self):
        """崩溃时调用栈写进 crash.txt（进程以非 0 结束）；下次启动另存成 crash.old.txt，crash.txt 换成空的。"""
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind):
                folder = sandbox.temp_dir(self)
                crashed = self.run_child(kind, folder, "crash")
                self.assertNotEqual(crashed.returncode, 0)
                record = (folder / "crash.txt").read_text(encoding="utf-8", errors="replace")
                self.assertRegex(record, "Fatal Python error|Windows fatal exception")
                self.assertIn("<string>", record)  # 停在哪一行（子进程的脚本）
                again = self.run_child(kind, folder, "none")
                self.assertEqual(again.returncode, 0, again.stderr)
                # 另存时用户目录换成占位（子进程和这里是同一份环境变量）
                self.assertEqual((folder / "crash.old.txt").read_text(encoding="utf-8", errors="replace"),
                                 paths.scrub_paths(record))
                self.assertEqual((folder / "crash.txt").read_bytes(), b"")

    def test_handled_exception_cleared_on_normal_exit(self):
        """假崩溃：别的代码自己接住的访问违例照样记成「Windows fatal exception」（进程照常运行、退出码 0）；照常退出时清空，
        下次启动不当成崩溃另存。"""
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind):
                folder = sandbox.temp_dir(self)
                fake = self.run_child(kind, folder, "fake")
                self.assertEqual(fake.returncode, 0, fake.stderr)
                self.assertIn("still running", fake.stdout)
                self.assertIn("Windows fatal exception: access violation",
                              (folder / "crash.txt").read_text(encoding="utf-8", errors="replace"))
                folder = sandbox.temp_dir(self)
                cleared = self.run_child(kind, folder, "fake-clear")
                self.assertEqual(cleared.returncode, 0, cleared.stderr)
                self.assertEqual((folder / "crash.txt").read_bytes(), b"")
                self.run_child(kind, folder, "none")
                self.assertFalse((folder / "crash.old.txt").exists())

    def test_full_record_disabled(self):
        """崩溃记录超过上限：真的停用这次运行的崩溃记录（之后再有别的库自己接住的异常也不往里记），文件留着，错误记录里
        记一笔第一条异常那一行。"""
        for kind in ("entry", "startup"):
            with self.subTest(kind=kind):
                folder = sandbox.temp_dir(self)
                proc = self.run_child(kind, folder, "full")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                got = json.loads(proc.stdout.strip().splitlines()[-1])
                self.assertGreater(got["full"], 2048)
                self.assertEqual((got["stop"], got["enabled"]), (True, False))
                self.assertEqual(got["after"], got["full"])  # 停用以后不再往里记
                self.assertEqual((folder / "crash.txt").stat().st_size, got["full"])  # 文件留着
                (title, detail), = ErrorLog(folder / "overlay_err.txt").entries()
                self.assertEqual(title, startup.CRASH_FULL_TITLE)
                self.assertIn("第一条：Windows fatal exception: access violation", detail)

    def test_selfcheck_clears_unless_crashed(self):
        """发行版的自检照常走完：清空崩溃记录（自检里别的库自己接住的异常不留到下次启动当成崩溃）；自检中途真的崩溃：
        记录留着（自检里那一次和真的崩溃都在）。"""
        env = {k: v for k, v in os.environ.items() if k not in load_entry().QT_ENV_DROP}  # 入口不用去掉、记录什么
        for action in ("ok", "crash"):
            with self.subTest(action=action):
                folder = sandbox.temp_dir(self)
                proc = subprocess.run([sys.executable, "-B", "-c", SELFCHECK_CHILD, str(PROJECT), str(folder), action],
                                      cwd=str(PROJECT), env=env, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=120)
                lines = proc.stdout.strip().splitlines()
                self.assertTrue(lines, proc.stderr[-2000:])
                self.assertGreater(json.loads(lines[0])["during"], 0)  # 自检里那一次记下了
                record = (folder / ".tmp" / "crash.txt").read_text(encoding="utf-8", errors="replace")
                if action == "ok":
                    self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
                    self.assertEqual(record, "")
                else:
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(record.count("Windows fatal exception: access violation"), 2, record[:2000])
                    self.assertIn("<string>", record)  # 停在哪一行（子进程的脚本）
                self.assertFalse((folder / "overlay_err.txt").exists())


class EntryTest(unittest.TestCase):
    """发行版的入口（main）。悬浮窗（advisor.ui.run）、自检、弹窗都换成假的。"""

    def setUp(self):
        self.entry = load_entry()
        self.log = capture_errors(self)

    def test_source_run_touches_nothing(self):
        """从源码运行（不是编出来的发行版）：不开崩溃记录、不去环境变量，照常交给 run()。"""
        entry = self.entry
        self.assertFalse(entry.FROZEN)
        with qt_env(QT_SCALE_FACTOR="2"), mock.patch.object(sys, "argv", ["ACC.exe"]), \
                mock.patch.object(entry, "_open_crash_log", side_effect=AssertionError("不该开崩溃记录")), \
                mock.patch.object(entry, "_drop_qt_env", side_effect=AssertionError("不该去环境变量")), \
                mock.patch("advisor.ui.run") as run:
            entry.main()
            self.assertEqual(os.environ["QT_SCALE_FACTOR"], "2")
        run.assert_called_once_with()
        self.assertEqual(self.log.entries(), [])

    def test_frozen_overlay(self):
        """发行版：先开崩溃记录、交给 startup，去掉 Qt 环境变量（别的不动），去掉了哪些（只有名字，按 QT_ENV_DROP 的先后）交给
        startup 由 run() 查过防多开以后记，再起悬浮窗。"""
        entry = self.entry
        crash = object()
        with qt_env(QT_SCALE_FACTOR="1.5", QT_PLUGIN_PATH=r"C:\OtherQtApp\plugins", QT_FATAL_WARNINGS="1",
                    QT_QPA_FONTDIR="留着"), \
                mock.patch.object(entry, "FROZEN", True), mock.patch.object(sys, "argv", ["ACC.exe"]), \
                mock.patch.object(entry, "_open_crash_log", return_value=crash) as opened, \
                mock.patch.object(startup, "adopt_crash_log") as adopt, \
                mock.patch.object(startup, "remember_dropped_env") as remember, mock.patch("advisor.ui.run") as run:
            entry.main()
            for name in ("QT_SCALE_FACTOR", "QT_PLUGIN_PATH", "QT_FATAL_WARNINGS"):
                self.assertNotIn(name, os.environ)
            self.assertEqual(os.environ["QT_QPA_FONTDIR"], "留着")
        opened.assert_called_once_with()
        adopt.assert_called_once_with(crash)
        remember.assert_called_once_with(["QT_PLUGIN_PATH", "QT_SCALE_FACTOR", "QT_FATAL_WARNINGS"])
        run.assert_called_once_with()
        self.assertEqual(self.log.entries(), [])  # 还没记：等 run() 查过防多开

    def test_frozen_preview_child(self):
        """发行版画缩略图的子进程：不开崩溃记录（它自己写到标准错误输出）、不写错误记录，环境变量照样去掉。"""
        entry = self.entry
        with qt_env(QT_SCALE_FACTOR="2"), mock.patch.object(entry, "FROZEN", True), \
                mock.patch.object(sys, "argv", ["ACC.exe", entry.PREVIEW_ARG, '{"keys": []}']), \
                mock.patch.object(entry, "_open_crash_log", side_effect=AssertionError("子进程不开崩溃记录")), \
                mock.patch.object(startup, "remember_dropped_env", side_effect=AssertionError("子进程不写错误记录")), \
                mock.patch.object(startup, "adopt_crash_log") as adopt, mock.patch("advisor.ui.run") as run:
            entry.main()
            self.assertNotIn("QT_SCALE_FACTOR", os.environ)
        adopt.assert_called_once_with(None)
        run.assert_called_once_with()
        self.assertEqual(self.log.entries(), [])

    def test_frozen_selfcheck(self):
        """发行版自检：同样开崩溃记录、去掉环境变量并马上记一笔（只记名字），退出码照自检的（不起悬浮窗）。"""
        self.assertEqual(startup.DROPPED_ENV_TITLE, QT_NOTE_TITLE)
        entry = self.entry
        with qt_env(QT_FONT_DPI="144"), mock.patch.object(entry, "FROZEN", True), \
                mock.patch.object(sys, "argv", ["ACC.exe", "--selfcheck"]), \
                mock.patch.object(entry, "_open_crash_log", return_value=None) as opened, \
                mock.patch("advisor.diagnostics.self_check_main", return_value=1) as check, \
                mock.patch("advisor.ui.run", side_effect=AssertionError("自检不该启动悬浮窗")):
            with self.assertRaises(SystemExit) as raised:
                entry.main()
        self.assertEqual(raised.exception.code, 1)  # 自检的退出码不变
        opened.assert_called_once_with()
        check.assert_called_once_with()
        self.assertEqual(self.log.entries(), [(QT_NOTE_TITLE, "QT_FONT_DPI")])

    def test_frozen_selfcheck_clears_crash_log(self):
        """发行版自检走完（通过、不通过、自检里 Python 出了错）：清空崩溃记录，同 run() 走完时；退出码照自检的。
        自检中途真的崩溃时留着，在子进程里核对（CrashLogProcessTest）。"""
        entry = self.entry
        path = sandbox.temp_dir(self) / "crash.txt"
        for outcome in (0, 1, RuntimeError("自检里出错")):
            with self.subTest(outcome=repr(outcome)):
                path.write_text("Windows fatal exception: code 0x8001010d\n", encoding="utf-8")
                file = open(path, "a", encoding="utf-8")
                self.addCleanup(file.close)
                failed = isinstance(outcome, Exception)
                check = mock.Mock(side_effect=outcome) if failed else mock.Mock(return_value=outcome)
                with qt_env(), mock.patch.object(entry, "FROZEN", True), mock.patch.object(entry, "_crash", None), \
                        mock.patch.object(sys, "argv", ["ACC.exe", "--selfcheck"]), \
                        mock.patch.object(entry, "_open_crash_log", return_value=file), \
                        mock.patch("advisor.diagnostics.self_check_main", check), \
                        mock.patch("advisor.ui.run", side_effect=AssertionError("自检不该启动悬浮窗")):
                    with self.assertRaises(RuntimeError if failed else SystemExit) as raised:
                        entry.main()
                if not failed:
                    self.assertEqual(raised.exception.code, outcome)
                self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(self.log.entries(), [])

    def test_apply_update(self):
        """换版本程序（--apply-update）：发行版、从源码运行都一样，一开头就交给 update_apply.apply_main（参数原样），以它的
        返回值退出；不开崩溃记录、不去环境变量、不起悬浮窗、不写错误记录。"""
        entry = self.entry
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                with qt_env(QT_SCALE_FACTOR="2"), mock.patch.object(entry, "FROZEN", frozen), \
                        mock.patch.object(sys, "argv", ["ACC.exe", entry.APPLY_ARG, "1234"]), \
                        mock.patch.object(entry, "_open_crash_log", side_effect=AssertionError("不该开崩溃记录")), \
                        mock.patch.object(entry, "_drop_qt_env", side_effect=AssertionError("不该去环境变量")), \
                        mock.patch.object(startup, "adopt_crash_log", side_effect=AssertionError("不该交崩溃记录")), \
                        mock.patch("advisor.ui.run", side_effect=AssertionError("不该起悬浮窗")), \
                        mock.patch("advisor.update_apply.apply_main", return_value=0xACD1) as apply:
                    with self.assertRaises(SystemExit) as raised:
                        entry.main()
                    self.assertEqual(os.environ["QT_SCALE_FACTOR"], "2")
                self.assertEqual(raised.exception.code, 0xACD1)
                apply.assert_called_once_with([entry.APPLY_ARG, "1234"])
        self.assertEqual(self.log.entries(), [])

    def test_apply_update_equals_form(self):
        """换版本程序的参数也认「--apply-update=<进程号>」：同样一开头就交给 apply_main（参数原样）、以它的返回值退出，别的都不碰。
        认法和 update_apply._old_pid 一样：它认得出进程号的写法入口都交给它（进程号不对的也交给它，由它核对、不改就退出）；
        名字只是开头相同的参数不算，照常起悬浮窗。"""
        from advisor import update_apply

        entry = self.entry
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                with qt_env(QT_SCALE_FACTOR="2"), mock.patch.object(entry, "FROZEN", frozen), \
                        mock.patch.object(sys, "argv", ["ACC.exe", f"{entry.APPLY_ARG}=1234"]), \
                        mock.patch.object(entry, "_open_crash_log", side_effect=AssertionError("不该开崩溃记录")), \
                        mock.patch.object(entry, "_drop_qt_env", side_effect=AssertionError("不该去环境变量")), \
                        mock.patch.object(startup, "adopt_crash_log", side_effect=AssertionError("不该交崩溃记录")), \
                        mock.patch("advisor.ui.run", side_effect=AssertionError("不该起悬浮窗")), \
                        mock.patch("advisor.update_apply.apply_main", return_value=0) as apply:
                    with self.assertRaises(SystemExit) as raised:
                        entry.main()
                    self.assertEqual(os.environ["QT_SCALE_FACTOR"], "2")
                self.assertEqual(raised.exception.code, 0)
                apply.assert_called_once_with([f"{entry.APPLY_ARG}=1234"])
        arg = entry.APPLY_ARG
        for argv in ([arg, "7"], [f"{arg}=12"], ["x", arg, "7"], ["x", f"{arg}=7", "y"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(update_apply._old_pid(argv))
                self.assertTrue(entry._applying(argv))
        for argv in ([arg], [f"{arg}="], [f"{arg}=x"], [arg, "0"]):  # 进程号不对：交给 apply_main，由它拒绝
            with self.subTest(argv=argv):
                self.assertIsNone(update_apply._old_pid(argv))
                self.assertTrue(entry._applying(argv))
        for argv in ([], ["--apply-updates", "7"], [f"{arg}s=7"], [f"{arg}-now=7"], ["-apply-update", "7"],
                     [arg.upper(), "7"], ["--selfcheck"]):
            with self.subTest(argv=argv):
                self.assertIsNone(update_apply._old_pid(argv))
                self.assertFalse(entry._applying(argv))
        with qt_env(), mock.patch.object(entry, "FROZEN", False), \
                mock.patch.object(sys, "argv", ["ACC.exe", f"{arg}s=7"]), \
                mock.patch("advisor.update_apply.apply_main", side_effect=AssertionError("不该交给换版本程序")), \
                mock.patch("advisor.ui.run") as run:
            entry.main()
        run.assert_called_once_with()
        self.assertEqual(self.log.entries(), [])

    def test_drop_qt_env(self):
        """只去掉设了的那几个（含设成空的），返回名字；QT_ENV_DROP 以外的不动。"""
        entry = self.entry
        with qt_env(QT_STYLE_OVERRIDE="fusion", QT_QPA_PLATFORM="offscreen", QT_SCREEN_SCALE_FACTORS="",
                    QT_QPA_FONTDIR="x", QT_LOGGING_RULES="*=false"):
            self.assertEqual(entry._drop_qt_env(), ["QT_QPA_PLATFORM", "QT_SCREEN_SCALE_FACTORS", "QT_STYLE_OVERRIDE"])
            self.assertEqual(entry._drop_qt_env(), [])
            self.assertEqual((os.environ["QT_QPA_FONTDIR"], os.environ["QT_LOGGING_RULES"]), ("x", "*=false"))

    def test_qt_env_names_are_qt6(self):
        """要去掉的每个名字都是这一版 Qt（界面库、窗口部件库和 Windows 平台插件）确实会读的环境变量：名字写错时这里报出来。"""
        import re

        import PySide6

        base = Path(PySide6.__file__).parent
        blob = b"".join((base / rel).read_bytes() for rel in
                        ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "plugins/platforms/qwindows.dll"))
        for name in self.entry.QT_ENV_DROP:
            with self.subTest(name=name):
                # 整个名字（前后不再接大写字母、数字、下划线），单字节或双字节的写法都算
                narrow = rb"(?<![A-Z0-9_])" + re.escape(name.encode("ascii")) + rb"(?![A-Z0-9_])"
                wide = re.escape(name.encode("utf-16-le")) + rb"(?!(?:[A-Z0-9_]\x00))"
                self.assertTrue(re.search(narrow, blob) or re.search(wide, blob))

    def run_failed_import(self, failures: dict, argv=("ACC.exe",), crash=True):
        """导入 failures 里的模块出错时跑一遍 main()。返回（退出码, 弹窗的 (正文, 标题) 或 None, 崩溃记录的内容, 文件）。"""
        entry = self.entry
        file = None
        if crash:
            path = sandbox.temp_dir(self) / "crash.txt"
            file = open(path, "a", encoding="utf-8")
            self.addCleanup(file.close)
        with mock.patch.object(entry, "_crash", file), mock.patch.object(sys, "argv", list(argv)), \
                mock.patch("sys.stderr", new_callable=io.StringIO), \
                mock.patch.object(ctypes, "windll") as windll, blocked_import(failures):
            with self.assertRaises(SystemExit) as raised:
                entry.main()
        box = windll.user32.MessageBoxW
        shown = tuple(box.call_args.args[1:3]) if box.called else None
        text = Path(file.name).read_text(encoding="utf-8") if file is not None else ""
        return raised.exception.code, shown, text, file

    def test_import_failure_dialog(self):
        """导入 advisor 出错：调用栈写进崩溃记录，弹「启动失败」窗口（程序文件不完整或已损坏），以 EXIT_REPORTED 退出。"""
        error = ImportError("Module 'advisor.cli' was actively excluded from Nuitka compilation")
        code, shown, text, file = self.run_failed_import({"advisor.ui": error})
        self.assertEqual(code, self.entry.EXIT_REPORTED)
        self.assertEqual(shown, (f"{ui.BROKEN_FILES}\n\n详细信息已写入：\n{file.name}", "拾星助手启动失败"))
        self.assertIn("拾星助手启动失败：导入程序模块出错", text)
        self.assertIn("ImportError: Module 'advisor.cli' was actively excluded", text)

    def test_import_failure_load_blocked(self):
        """DLL 加载不了（发行版里 Nuitka 的写法、Python 的写法，包在别的错里的也算；Nuitka 在中文等系统上拼不出原文时的空
        ImportError 也算）：写可能被杀毒软件拦截。"""
        cases = (
            ImportError("LoadLibraryExW 'D:\\拾星助手\\app\\unicodedata.pyd' failed: 找不到指定的模块。"),
            ImportError("DLL load failed while importing _ctypes: 应用程序控制策略已阻止此文件。"),
            chained(ImportError("cannot import name 'x'"), ImportError("LoadLibraryExW 'x.pyd' failed: 拒绝访问。")),
            ImportError(),
            chained(ImportError("cannot import name 'x'"), ImportError()),
        )
        for error in cases:
            with self.subTest(error=str(error)):
                code, shown, _, _ = self.run_failed_import({"advisor.ui": error})
                self.assertEqual(code, self.entry.EXIT_REPORTED)
                self.assertTrue(shown[0].startswith(ui.LOAD_BLOCKED + "\n\n"))

    def test_selfcheck_import_failure(self):
        """自检导入出错也一样弹窗、以 EXIT_REPORTED 退出。"""
        code, shown, text, _ = self.run_failed_import({"advisor.diagnostics": ImportError("坏了")},
                                                       argv=("ACC.exe", "--selfcheck"))
        self.assertEqual(code, self.entry.EXIT_REPORTED)
        self.assertTrue(shown[0].startswith(ui.BROKEN_FILES))
        self.assertIn("ImportError: 坏了", text)

    def test_no_ctypes_exits_1(self):
        """连 ctypes 都用不了（_ctypes.pyd 被删）：弹不了窗，以 1 退出，交给启动程序报；调用栈照样写进崩溃记录。"""
        failures = {"advisor.ui": ImportError("LoadLibraryExW '_ctypes.pyd' failed"),
                    "ctypes": ImportError("LoadLibraryExW '_ctypes.pyd' failed")}
        code, shown, text, _ = self.run_failed_import(failures)
        self.assertEqual(code, 1)
        self.assertIsNone(shown)
        self.assertIn("LoadLibraryExW '_ctypes.pyd' failed", text)

    def test_no_crash_file(self):
        """崩溃记录没开成：弹窗末尾只写中文一句（同错误记录写不进时），照样以 EXIT_REPORTED 退出。"""
        code, shown, _, _ = self.run_failed_import({"advisor.ui": ImportError("坏了")}, crash=False)
        self.assertEqual(code, self.entry.EXIT_REPORTED)
        self.assertEqual(shown[0], f"{ui.BROKEN_FILES}\n\n（错误信息也未能写入错误记录）")


class SummaryTest(unittest.TestCase):
    """启动失败的弹窗按原因写中文（startup_summary）：DLL 加载不了、处理器太老这两种原因，以及它们包在别的错里时。"""

    def summary(self, error: BaseException, frozen: bool) -> str:
        with mock.patch.object(ui.sys, "frozen", frozen, create=True):
            return ui.startup_summary(error)

    def test_load_blocked(self):
        numpy_wrapped = ImportError("\n\nIMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!\n…\n"
                                    "Original error was: DLL load failed while importing _multiarray_umath: 找不到指定的模块。\n")
        # 发行版在中文、日文等系统上：Nuitka 把系统的说明按 ANSI 代码页拼进原文，按 UTF-8 转不了，抛出的 ImportError 没有原文；
        # numpy 照样包一层（「Original error was: 」后面是空的）
        numpy_wrapped_empty = chained(ImportError("\n\nIMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!\n"
                                                  "…\nOriginal error was: \n"), ImportError())
        cases = (
            ImportError("DLL load failed while importing cv2: 应用程序控制策略已阻止此文件。"),
            ImportError("LoadLibraryExW 'D:\\x\\app\\cv2\\cv2.pyd' failed: 动态链接库(DLL)初始化例程失败。"),
            numpy_wrapped,
            chained(ImportError("numpy._core.multiarray failed to import"), ImportError("DLL load failed while importing x")),
            ImportError(),
            numpy_wrapped_empty,
        )
        for error in cases:
            with self.subTest(error=str(error)[:40]):
                self.assertEqual(self.summary(error, True), ui.LOAD_BLOCKED)
                self.assertEqual(self.summary(error, False), DEV_MISSING)  # 开发版照旧写重装的步骤

    def test_old_cpu(self):
        """numpy 报处理器缺它要求的指令：发行版、开发版都写「处理器太老」和要求，包在 ImportError 里的也认得出。"""
        for error in (RuntimeError(NUMPY_OLD_CPU), chained(ImportError("cv2 导入失败"), RuntimeError(NUMPY_OLD_CPU))):
            for frozen in (True, False):
                with self.subTest(error=type(error).__name__, frozen=frozen):
                    self.assertEqual(self.summary(error, frozen), ui.OLD_CPU)
        self.assertIn("SSE4.2", ui.OLD_CPU)

    def test_others_unchanged(self):
        self.assertEqual(self.summary(ImportError("No module named 'cv2'"), True), ui.BROKEN_FILES)
        self.assertEqual(self.summary(ImportError("cannot import name 'x' from 'y'"), True), ui.BROKEN_FILES)
        self.assertEqual(self.summary(RuntimeError("baseline"), True), GENERIC)  # 只有一半字样的不算处理器太老
        self.assertEqual(self.summary(OSError("DLL load failed"), True), GENERIC)  # 不是导入出的错
        self.assertEqual(self.summary(RuntimeError(), True), GENERIC)  # 原文是空的只对 ImportError 算加载失败
        for text in (ui.LOAD_BLOCKED, ui.OLD_CPU):  # 只有中文，没有报错原文、类名
            self.assertNotIn("Error", text)
            self.assertNotIn("DLL load", text)


class RunTest(unittest.TestCase):
    """run()：预加载的位置、弹过窗以后的退出码、走完清空崩溃记录。防多开、悬浮窗、预加载、弹窗都换成假的。"""

    def setUp(self):
        self.main_mod = sys.modules[overlay_app.main.__module__]
        self.calls: list[str] = []
        self.reports: list[tuple[str, str]] = []

    def step(self, name, error=None, result=None):
        def run(*args, **kwargs):
            self.calls.append(name)
            if error is not None:
                raise error
            return result

        return run

    def run_ui(self, main_error=None, preload_error=None, claim=True, frozen=False):
        """跑一遍 run()，返回退出码（照常返回时为 "returned"）。"""
        with mock.patch.object(si, "claim", side_effect=self.step("claim", result=claim)), \
                mock.patch.object(self.main_mod, "main", side_effect=self.step("main", main_error)), \
                mock.patch.object(startup, "preload", side_effect=self.step("preload", preload_error)), \
                mock.patch.object(startup, "open_crash_log", side_effect=self.step("open_crash_log")), \
                mock.patch.object(startup, "clear_crash_log", side_effect=self.step("clear_crash_log")), \
                mock.patch.object(ui, "report_startup_error", side_effect=lambda s, d: self.reports.append((s, d))), \
                mock.patch.object(ui.sys, "frozen", frozen, create=True), mock.patch.object(sys, "argv", ["ACC.exe"]):
            try:
                ui.run()
            except SystemExit as e:
                return e.code
        return "returned"

    def test_normal_order(self):
        """查过防多开才开崩溃记录（再次启动、只请已在运行的那个显示的进程不碰它），显示悬浮窗之前预加载，走完清空。"""
        self.assertEqual(self.run_ui(), "returned")
        self.assertEqual(self.calls, ["claim", "open_crash_log", "preload", "main", "clear_crash_log"])
        self.assertEqual(self.reports, [])

    def test_already_running(self):
        self.assertEqual(self.run_ui(claim=False), "returned")
        self.assertEqual(self.calls, ["claim"])

    def test_dropped_env_logged_after_claim(self):
        """发行版的入口去掉了的 Qt 环境变量：再次启动（已有一个在运行）的进程不写错误记录；查过防多开以后记一笔，只记一次。"""
        log = capture_errors(self)
        self.addCleanup(setattr, startup, "_dropped_env", ())
        startup.remember_dropped_env(["QT_SCALE_FACTOR", "QT_FONT_DPI"])
        self.assertEqual(self.run_ui(claim=False), "returned")
        self.assertEqual(log.entries(), [])
        self.assertEqual(self.run_ui(), "returned")
        self.assertEqual(log.entries(), [(QT_NOTE_TITLE, "QT_SCALE_FACTOR、QT_FONT_DPI")])
        self.run_ui()
        self.assertEqual(len(log.entries()), 1)

    def test_preload_failure_reported(self):
        """识别要用的库加载出错：按原因弹窗（报错原文只进详细信息），不起悬浮窗，以 EXIT_REPORTED 退出。"""
        cases = (
            (ImportError("DLL load failed while importing cv2: 应用程序控制策略已阻止此文件。"), True, ui.LOAD_BLOCKED),
            (ImportError("No module named 'onnxruntime'"), True, ui.BROKEN_FILES),
            (ImportError("No module named 'onnxruntime'"), False, DEV_MISSING),
            (RuntimeError(NUMPY_OLD_CPU), True, ui.OLD_CPU),
            (OSError("别的原因"), True, GENERIC),
        )
        for error, frozen, want in cases:
            with self.subTest(error=str(error)[:30], frozen=frozen):
                self.calls.clear()
                self.reports.clear()
                self.assertEqual(self.run_ui(preload_error=error, frozen=frozen), errors.EXIT_REPORTED)
                self.assertEqual(self.calls, ["claim", "open_crash_log", "preload", "clear_crash_log"])
                (summary, detail), = self.reports
                self.assertEqual(summary, want)
                self.assertIn(str(error).splitlines()[0], detail)  # 原文在调用栈里（进错误记录）

    def test_startup_exits_reported(self):
        """启动时就退出（SystemExit 带文字、带非 0 的退出码）、启动出错：弹过窗以后一律以 EXIT_REPORTED 退出。"""
        text = "资料文件 synergy.toml 已损坏，请把压缩包整个重新解压。"
        cases = ((SystemExit(text), text), (SystemExit(7), "程序异常退出（退出码 7）"), (RuntimeError("出错"), GENERIC))
        for error, want in cases:
            with self.subTest(error=repr(error)):
                self.calls.clear()
                self.reports.clear()
                self.assertEqual(self.run_ui(main_error=error), errors.EXIT_REPORTED)
                self.assertEqual([s for s, _ in self.reports], [want])
                self.assertEqual(self.calls[-1], "clear_crash_log")

    def test_normal_exits_unchanged(self):
        """悬浮窗起来以后的退出（Exited）照旧带原来的退出码、不弹窗；退出码 0 / None 的 SystemExit 也不弹窗。都清空崩溃记录。"""
        for error, want in ((Exited(3), 3), (Exited(0), 0), (SystemExit(0), 0), (SystemExit(None), None)):
            with self.subTest(error=repr(error)):
                self.calls.clear()
                self.assertEqual(self.run_ui(main_error=error), want)
                self.assertEqual(self.reports, [])
                self.assertEqual(self.calls[-1], "clear_crash_log")

    def test_real_dialog_text(self):
        """经真的 report_startup_error（只把弹窗换成假的）：正文是中文一句，空一行接错误记录的位置；原文只在错误记录里。"""
        log = capture_errors(self)
        error = ImportError("DLL load failed while importing cv2: 应用程序控制策略已阻止此文件。")
        with mock.patch.object(si, "claim", return_value=True), mock.patch.object(self.main_mod, "main"), \
                mock.patch.object(startup, "preload", side_effect=error), \
                mock.patch.object(ui, "ctypes") as fake, mock.patch("sys.stderr", new_callable=io.StringIO), \
                mock.patch.object(ui.sys, "frozen", True, create=True), mock.patch.object(sys, "argv", ["ACC.exe"]):
            with self.assertRaises(SystemExit) as raised:
                ui.run()
        self.assertEqual(raised.exception.code, errors.EXIT_REPORTED)
        self.assertEqual(fake.windll.user32.MessageBoxW.call_args.args[1],
                         f"{ui.LOAD_BLOCKED}\n\n详细信息已写入：\n{log.path}")
        (title, detail), = log.entries()
        self.assertEqual(title, "拾星助手启动失败")
        self.assertIn("DLL load failed while importing cv2", detail)

    def test_ready_event_taken_first(self):
        """run() 一开始就把 ACC_READY_EVENT 从环境变量里取走（画缩略图的子进程也是），之后起的子进程继承不到。"""
        self.addCleanup(setattr, startup, "_ready_name", None)
        with mock.patch.dict(os.environ, {startup.READY_ENV: "Local\\测试事件"}):
            self.run_ui()
            self.assertNotIn(startup.READY_ENV, os.environ)
        self.assertEqual(startup._ready_name, "Local\\测试事件")
        startup._ready_name = None
        with mock.patch.dict(os.environ, {startup.READY_ENV: "Local\\测试事件"}), \
                mock.patch.object(sys, "argv", ["ACC.exe", ui.PREVIEW_ARG, "{}"]), \
                mock.patch("advisor.ui.preview_child.child_main", return_value=0):
            with self.assertRaises(SystemExit):
                ui.run()
            self.assertNotIn(startup.READY_ENV, os.environ)


class PreloadTest(unittest.TestCase):
    def test_order_and_failure(self):
        """按 PRELOAD 的先后导入（numpy 在最前），出错照常抛出、后面的不再导入。"""
        self.assertEqual(startup.PRELOAD[0], "numpy")
        seen = []
        with mock.patch.object(startup.importlib, "import_module", side_effect=seen.append):
            startup.preload()
        self.assertEqual(seen, list(startup.PRELOAD))
        seen.clear()

        def fail_at_onnxruntime(name):
            seen.append(name)
            if name == "onnxruntime":
                raise ImportError("DLL load failed while importing onnxruntime_pybind11_state")

        with mock.patch.object(startup.importlib, "import_module", side_effect=fail_at_onnxruntime):
            with self.assertRaises(ImportError):
                startup.preload()
        self.assertEqual(seen, list(startup.PRELOAD[:startup.PRELOAD.index("onnxruntime") + 1]))

    def test_covers_recognition_imports(self):
        """新进程里先预加载，再导入后台识别线程要用的识别模块、建一次文字识别引擎：之后不再导入任何新的第三方库
        （识别要用的都在预加载里加载过了）；预加载里有 numpy、OpenCV、截屏、推理库和 pyclipper。"""
        child = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from advisor.ui import startup
skip = set(sys.stdlib_module_names) | {"advisor", "rapidocr_lite", "PySide6", "shiboken6", "__main__"}
def third(names):
    return sorted({n.split(".")[0] for n in names} - skip)
before = set(sys.modules)
startup.preload()
preloaded = set(sys.modules)
import advisor.vision, advisor.vision.capture, advisor.vision.pipeline, advisor.vision.window_capture
from advisor.vision import chip_select
chip_select._engine()
print(json.dumps({"preloaded": third(preloaded - before), "after": third(set(sys.modules) - preloaded)}))
"""
        proc = subprocess.run([sys.executable, "-B", "-c", child, str(PROJECT)], cwd=str(PROJECT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(got["after"], [])
        self.assertLessEqual({"numpy", "cv2", "mss", "onnxruntime", "pyclipper"}, set(got["preloaded"]))


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.ResetEvent.argtypes = [wintypes.HANDLE]
    k.ResetEvent.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102


@unittest.skipUnless(WINDOWS, "命名事件只在 Windows 上有")
class ReadySignalTest(unittest.TestCase):
    """悬浮窗出来的信号：真的建一个命名事件（测试专用的名字），看 signal_ready 有没有触发它。"""

    def setUp(self):
        self.k32 = _kernel32()
        self.log = capture_errors(self)
        self.name = f"Local\\ChipAdvisorTest-Ready-{uuid.uuid4().hex}"
        self.addCleanup(setattr, startup, "_ready_name", None)

    def take(self, name: str) -> None:
        with mock.patch.dict(os.environ, {startup.READY_ENV: name}):
            startup.take_ready_event()
            self.assertNotIn(startup.READY_ENV, os.environ)

    def test_signal_sets_event_once(self):
        event = self.k32.CreateEventW(None, True, False, self.name)  # 手动复位，开始时没触发
        self.assertTrue(event)
        self.addCleanup(self.k32.CloseHandle, event)
        self.take(self.name)
        self.assertEqual(self.k32.WaitForSingleObject(event, 0), WAIT_TIMEOUT)
        startup.signal_ready()
        self.assertEqual(self.k32.WaitForSingleObject(event, 0), WAIT_OBJECT_0)
        self.k32.ResetEvent(event)
        startup.signal_ready()  # 只做一次
        self.assertEqual(self.k32.WaitForSingleObject(event, 0), WAIT_TIMEOUT)
        self.assertEqual(self.log.entries(), [])

    def test_no_event_quiet(self):
        """没有这个变量（开发版、直接双击 ACC.exe），或者事件已经没了（启动程序等满了）：什么都不做，不记错误。"""
        startup.take_ready_event()
        startup.signal_ready()
        self.take(self.name)  # 没人建这个事件
        startup.signal_ready()
        self.assertEqual(self.log.entries(), [])

    def test_open_error_logged(self):
        """打不开的别的原因（这里同名的是别种对象）：记进错误记录，不抛出。"""
        mutex = self.k32.CreateMutexW(None, False, self.name)
        self.assertTrue(mutex)
        self.addCleanup(self.k32.CloseHandle, mutex)
        self.take(self.name)
        startup.signal_ready()
        (title, detail), = self.log.entries()
        self.assertEqual(title, "通知启动程序「悬浮窗已显示」时出错（拾星助手仍在运行）")
        self.assertIn("错误代码", detail)

    def wait_set(self, app, event, seconds: float) -> float | None:
        """转着事件循环等 event 触发，最多 seconds 秒：返回等了多久，没等到为 None。"""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            app.processEvents()
            if self.k32.WaitForSingleObject(event, 0) == WAIT_OBJECT_0:
                return time.monotonic() - start
            time.sleep(0.01)
        return None

    def test_signal_later_waits_for_event_loop(self):
        """signal_ready_later：当时不触发，事件循环转起来、过了给定的时间才触发，只触发一次（离屏的事件循环）。"""
        from tests.support import qtenv

        app = qtenv.app()
        self.assertEqual(startup.READY_DELAY_MS, 5000)
        event = self.k32.CreateEventW(None, True, False, self.name)
        self.assertTrue(event)
        self.addCleanup(self.k32.CloseHandle, event)
        self.take(self.name)
        startup.signal_ready_later(300)
        self.assertEqual(self.k32.WaitForSingleObject(event, 0), WAIT_TIMEOUT)  # 排好了，还没触发
        self.assertIsNone(self.wait_set(app, event, 0.1))  # 事件循环转了，时间还没到
        waited = self.wait_set(app, event, 10)
        self.assertIsNotNone(waited)
        self.k32.ResetEvent(event)
        self.assertIsNone(self.wait_set(app, event, 0.5))  # 只触发一次
        self.assertIsNone(startup._ready_name)
        self.assertEqual(self.log.entries(), [])

    def test_signal_later_without_event_no_timer(self):
        """不是经启动程序启动的（没有事件名）：不建定时器，事件循环转了也什么都不做。"""
        from tests.support import qtenv

        app = qtenv.app()
        startup._ready_name = None
        with mock.patch.object(startup, "signal_ready") as signal:
            startup.signal_ready_later(1)
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
        signal.assert_not_called()

    def test_main_signals_after_show(self):
        """悬浮窗 main()：显示、开始等「显示」请求以后，事件循环开始之前排好「过一会儿再通知」（触发在事件循环里，见上）和
        崩溃记录大小的定时检查（CrashLimitTest）。界面、识别、热键都换成假的。"""
        order = []
        app = mock.Mock()
        app.exec.side_effect = lambda: order.append("exec") or 0
        window = mock.MagicMock()
        window.show.side_effect = lambda: order.append("show")
        listener = mock.Mock()

        def listen(callback, on_start=None):
            order.append("listen")
            return listener

        main_mod = sys.modules[overlay_app.main.__module__]
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=None), save=lambda force=False: None)
        with mock.patch.object(main_mod, "build", return_value=(app, window, session, threading.Lock())), \
                mock.patch.object(main_mod, "Watcher"), mock.patch.object(main_mod, "Hotkey"), \
                mock.patch.object(main_mod, "expected_capture", return_value="window"), \
                mock.patch.object(main_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", side_effect=listen), \
                mock.patch.object(main_mod, "declare_high_performance"), \
                mock.patch.object(startup, "signal_ready_later", side_effect=lambda: order.append("schedule")), \
                mock.patch.object(startup, "signal_ready", side_effect=AssertionError("事件循环开始之前不该直接触发")), \
                mock.patch.object(startup, "watch_crash_log", side_effect=lambda: order.append("watch")), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit) as raised:
                overlay_app.main()
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(order, ["show", "listen", "schedule", "watch", "exec"])


if __name__ == "__main__":
    unittest.main()
