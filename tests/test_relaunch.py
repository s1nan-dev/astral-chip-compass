"""重新打开拾星助手（advisor/ui/relaunch.py）和启动流程里和它、界面语言有关的部分：

- 重开用的命令行和工作目录：发行版起 app\\ACC.exe，开发版起当前的解释器运行 advisor.ui，都带 --wait-pid 旧进程号；
- --wait-pid 的认法（等于号写法也认），等旧进程退出：已经不在、打不开当作已退出，到时间还没退出为 False（等的是假的系统
  调用，不真的起程序、不真的结束进程）；
- 起新进程交给 update_apply.start_program（换成假的，不真的起）；
- run()：--wait-pid 在查防多开以前等；lang.init 在查过防多开、换上新版本以后、导入悬浮窗以前调，第二个实例不调；画缩略图
  的子进程调 lang.init_from_env；画缩略图的子进程环境变量里有界面语言；
- 发行版入口不把 --wait-pid 当成别的参数拒掉、吃掉。

不弹窗、不起悬浮窗：run() 里的都换成假的。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import advisor.ui as ui
from advisor import paths
from advisor.ui import lang, relaunch
from advisor.ui import single_instance as si
from advisor.ui import startup
from advisor.ui.overlay_parts import app as overlay_app

PROJECT = Path(__file__).resolve().parents[1]
ENTRY_FILE = PROJECT / "packaging" / "chip_advisor.py"


def load_entry():
    """按文件载入发行版的入口（从源码运行：FROZEN 为假）。"""
    spec = importlib.util.spec_from_file_location("chip_advisor_entry", ENTRY_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WaitedPidTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(relaunch.waited_pid([relaunch.WAIT_ARG, "1234"]), 1234)
        self.assertEqual(relaunch.waited_pid([f"{relaunch.WAIT_ARG}=77"]), 77)
        self.assertEqual(relaunch.waited_pid(["x", relaunch.WAIT_ARG, "9", "y"]), 9)
        self.assertEqual(relaunch.waited_pid([relaunch.WAIT_ARG, "4294967295"]), 0xFFFFFFFF)
        for argv in ([], [relaunch.WAIT_ARG], [f"{relaunch.WAIT_ARG}="], [relaunch.WAIT_ARG, "0"],
                     [relaunch.WAIT_ARG, "x"], [f"{relaunch.WAIT_ARG}=-1"], ["--wait-pid-now", "5"],
                     [relaunch.WAIT_ARG, "4294967296"], [relaunch.WAIT_ARG, " 5"], [relaunch.WAIT_ARG, "+5"]):
            with self.subTest(argv=argv):
                self.assertIsNone(relaunch.waited_pid(argv))

    def test_odd_digits_do_not_raise(self):
        """isdigit 认、int 认不了（上标数字）或认成别的数（别的文字的数字）的字，长得 int 不肯转的一串数字：都按没带，
        不抛出（启动的最前面调用，抛出就起不来）。"""
        for value in ("²", "1²", "١٢٣", "５", "9" * 5000):
            with self.subTest(value=value[:12]):
                self.assertIsNone(relaunch.waited_pid([relaunch.WAIT_ARG, value]))
                self.assertIsNone(relaunch.waited_pid([f"{relaunch.WAIT_ARG}={value}"]))


class CommandTest(unittest.TestCase):
    def test_release(self):
        """发行版：直接起 app 文件夹里的 ACC.exe，工作目录是 app 文件夹（同根目录的启动程序、换版本时直接起的做法），
        不是根目录。"""
        app = Path(r"D:\拾星助手\app")
        root = Path(r"D:\拾星助手")
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "app_dir", return_value=app), \
                mock.patch.object(paths, "program_folder", return_value=root):
            argv, cwd = relaunch.command(4321)
        self.assertEqual(argv, [str(app / "ACC.exe"), relaunch.WAIT_ARG, "4321"])
        self.assertEqual(cwd, str(app))

    def test_development(self):
        """开发版：当前的解释器 -m advisor.ui，工作目录是项目根目录（-m 从这里找包）。"""
        root = Path(r"D:\项目")
        with mock.patch.object(paths, "is_frozen", return_value=False), \
                mock.patch.object(paths, "app_dir", return_value=root), \
                mock.patch.object(sys, "executable", r"C:\Python\python.exe"):
            argv, cwd = relaunch.command(99)
        self.assertEqual(argv, [r"C:\Python\python.exe", "-m", "advisor.ui", relaunch.WAIT_ARG, "99"])
        self.assertEqual(cwd, str(root))


class StartTest(unittest.TestCase):
    def test_hands_command_to_update_apply(self):
        with mock.patch.object(relaunch, "command", return_value=(["x.exe", relaunch.WAIT_ARG, "7"], "cwd")), \
                mock.patch("advisor.update_apply.start_program", return_value=True) as start:
            self.assertTrue(relaunch.start(7))
        start.assert_called_once_with(["x.exe", relaunch.WAIT_ARG, "7"], "cwd")

    def test_failure_is_false(self):
        with mock.patch.object(relaunch, "command", return_value=(["x.exe"], "cwd")), \
                mock.patch("advisor.update_apply.start_program", return_value=False):
            self.assertFalse(relaunch.start(7))
        with mock.patch.object(relaunch, "command", side_effect=OSError("起不来")):
            self.assertFalse(relaunch.start(7))


class StartProgramTest(unittest.TestCase):
    """update_apply.start_program：照换版本起程序的写法（_start，换成假的，不真的起），环境变量去掉给换版本程序的那几个，
    不等它、关掉句柄；起不来返回 False。"""

    def test_start_and_close(self):
        from advisor import update_apply as ua

        extra = {ua.READY_ENV: "x", ua.STARTED_ENV: "y", ua.COMPAT_ENV: "z", "ACC_RELAUNCH_TEST_KEEP": "1"}
        with mock.patch.dict("os.environ", extra), \
                mock.patch.object(ua, "_start", return_value=ua._Child(55, 1234)) as start, \
                mock.patch.object(ua, "_close") as close:
            self.assertTrue(ua.start_program(["a.exe", relaunch.WAIT_ARG, "9"], r"D:\拾星助手\app"))
        (command, cwd, env), kwargs = start.call_args
        self.assertEqual((command, cwd, kwargs), (["a.exe", relaunch.WAIT_ARG, "9"], Path(r"D:\拾星助手\app"),
                                                  {"quiet": False}))
        self.assertEqual(env.get("ACC_RELAUNCH_TEST_KEEP"), "1")
        self.assertFalse({ua.READY_ENV, ua.STARTED_ENV, ua.COMPAT_ENV} & {k.upper() for k in env})
        close.assert_called_once_with(55)

    def test_cannot_start(self):
        from advisor import update_apply as ua

        with mock.patch.object(ua, "_start", side_effect=OSError(2, "找不到文件")), \
                mock.patch.object(ua, "_close") as close:
            self.assertFalse(ua.start_program(["a.exe"], "cwd"))
        close.assert_not_called()


class WaitTest(unittest.TestCase):
    """等旧进程退出：等的是假的系统调用。"""

    def setUp(self):
        self.closed = []
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(relaunch, "close_handle", side_effect=self.closed.append).start()

    def test_not_running_or_cannot_open(self):
        """进程已经不在、打不开（open_process 给 0）：当作已退出，不去等；不是正整数的进程号也一样。"""
        with mock.patch.object(relaunch, "open_process", return_value=0) as opened, \
                mock.patch.object(relaunch, "wait_process") as waited:
            self.assertTrue(relaunch.wait_for_exit(1234))
        opened.assert_called_once_with(1234)
        waited.assert_not_called()
        for pid in (0, -1, "1234", None, True):
            with self.subTest(pid=pid):
                with mock.patch.object(relaunch, "open_process") as opened2:
                    self.assertTrue(relaunch.wait_for_exit(pid))
                opened2.assert_not_called()
        # 打开时出了意外（声明的参数类型转不了等）：同样当作已退出，不抛出（启动的最前面调用）
        with mock.patch.object(relaunch, "open_process", side_effect=OverflowError("太大")), \
                mock.patch.object(relaunch, "wait_process") as waited:
            self.assertTrue(relaunch.wait_for_exit(1234))
        waited.assert_not_called()
        self.assertEqual(self.closed, [])

    def test_exit_and_timeout(self):
        with mock.patch.object(relaunch, "open_process", return_value=11), \
                mock.patch.object(relaunch, "wait_process", return_value=relaunch.WAIT_OBJECT_0) as waited:
            self.assertTrue(relaunch.wait_for_exit(1234, timeout=2.5))
        waited.assert_called_once_with(11, 2500)
        self.assertEqual(self.closed, [11])
        with mock.patch.object(relaunch, "open_process", return_value=12), \
                mock.patch.object(relaunch, "wait_process", return_value=0x102) as waited:
            self.assertFalse(relaunch.wait_for_exit(1234, timeout=0))
        waited.assert_called_once_with(12, 0)
        self.assertEqual(self.closed, [11, 12])


class StartupTest(unittest.TestCase):
    """run() 里和 --wait-pid、界面语言有关的先后。悬浮窗、预加载、弹窗都换成假的。"""

    def setUp(self):
        self.main_mod = sys.modules[overlay_app.main.__module__]

    def run_ui(self, argv, claim=True, hand_over=False, init=None):
        order = []

        def step(name, result=None):
            def run(*args, **kwargs):
                order.append(name)
                return result

            return run

        with mock.patch.object(si, "claim", side_effect=step("claim", claim)), \
                mock.patch.object(ui, "hand_over_update", side_effect=step("hand_over", hand_over)), \
                mock.patch.object(lang, "init", side_effect=init or step("lang", lang.HANS)), \
                mock.patch.object(lang, "init_from_env", side_effect=step("lang_env", lang.HANS)), \
                mock.patch.object(relaunch, "wait_for_exit", side_effect=lambda pid: order.append(("wait", pid))), \
                mock.patch.object(startup, "open_crash_log", side_effect=step("crash")), \
                mock.patch.object(startup, "preload", side_effect=step("preload")), \
                mock.patch.object(self.main_mod, "main", side_effect=step("main")), \
                mock.patch("advisor.ui.preview_child.child_main", side_effect=step("child", 0)), \
                mock.patch.object(sys, "argv", argv):
            try:
                ui.run()
            except SystemExit as e:
                return order, e.code
        return order, "returned"

    def test_wait_then_claim_then_lang(self):
        """--wait-pid 在查防多开以前等；lang.init 在查过防多开、换新版本以后、起悬浮窗以前调。"""
        order, code = self.run_ui(["ACC.exe", relaunch.WAIT_ARG, "1234"])
        self.assertEqual(code, "returned")
        self.assertEqual(order, [("wait", 1234), "claim", "hand_over", "lang", "crash", "preload", "main"])

    def test_second_instance_does_not_init_language(self):
        """第二个实例（拿不到防多开的锁）：等完旧进程就直接返回，不读设置、不调 lang.init。"""
        order, code = self.run_ui(["ACC.exe", relaunch.WAIT_ARG, "1234"], claim=False)
        self.assertEqual(code, "returned")
        self.assertEqual(order, [("wait", 1234), "claim"])

    def test_hand_over_skips_language(self):
        """换上新版本、本进程直接返回时（hand_over_update 为真）不调 lang.init。"""
        order, code = self.run_ui(["ACC.exe"], hand_over=True)
        self.assertEqual(code, "returned")
        self.assertEqual(order, ["claim", "hand_over"])

    def test_language_error_does_not_stop_startup(self):
        """定界面语言时出错（设置文件坏得一读就报错，例如嵌套太深的 JSON：读设置的地方把它当读坏、拿备份恢复，lang 那里
        却会抛出）：按 Windows 的显示语言照常启动，记进错误记录。"""
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        lang.CURRENT = lang.HANS
        with mock.patch.object(lang, "system_language", return_value=lang.HANT), \
                mock.patch.object(ui, "log_error") as logged:
            order, code = self.run_ui(["ACC.exe"], init=RecursionError("太深"))
        self.assertEqual(code, "returned")
        self.assertEqual(order, ["claim", "hand_over", "crash", "preload", "main"])
        self.assertEqual(lang.CURRENT, lang.HANT)
        logged.assert_called_once()
        self.assertIn("RecursionError", logged.call_args.args[1])

    def test_preview_child_uses_env_language(self):
        """画缩略图的子进程：照环境变量定语言（init_from_env），在 child_main 以前，不调主进程的 lang.init。"""
        order, code = self.run_ui(["ACC.exe", ui.PREVIEW_ARG, "{}"])
        self.assertEqual(code, 0)
        self.assertEqual(order, ["lang_env", "child"])


class ChildEnvTest(unittest.TestCase):
    def test_spawn_env_has_language(self):
        """起画缩略图的子进程时，环境变量里有这个进程的界面语言（不改本进程的环境变量）。"""
        from advisor.ui import skin_preview

        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        lang.CURRENT = lang.HANT
        captured = {}

        class Proc:
            pid = 1

        def fake_popen(command, **kwargs):
            captured.update(kwargs)
            return Proc()

        with mock.patch.object(skin_preview, "_new_job", return_value=None), \
                mock.patch.object(skin_preview.subprocess, "Popen", side_effect=fake_popen):
            proc, job = skin_preview._start_child(["default"])
        self.assertEqual(captured["env"][lang.ENV], lang.HANT)
        self.assertIsNone(job)


class EntryTest(unittest.TestCase):
    def test_wait_arg_passes_through(self):
        """发行版的入口不把 --wait-pid 当成换版本、自检、缩略图子进程的参数，也不吃掉它：照常交给 run()。"""
        entry = load_entry()
        argv = ["ACC.exe", relaunch.WAIT_ARG, "1234"]
        self.assertFalse(entry._applying(argv))
        with mock.patch.object(sys, "argv", list(argv)), mock.patch("advisor.ui.run") as run:
            entry.main()
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
