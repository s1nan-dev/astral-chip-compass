"""悬浮窗退出流程（advisor/ui/overlay_parts/app.py 的 main()）。

退出时除了已有的「显示、监听、exec、停监听、停热键」的先后（tests/test_single_instance.py），还要把这几样停干净：
识别线程（watcher.stop()）、图片下载（stop_download()）、识别记录（session.recorder.close()）。识别线程、下载由悬浮窗的
shutdown() 停（各等一个上限），识别记录由 main() 关。

几条测试（shutdown() 本身的测试在 tests/test_overlay_shutdown.py）：
- 进程内：main() 用到的界面、识别、热键、等待线程全换成假的，看 shutdown() 和关识别记录有没有调用；shutdown() 说识别线程
  没停下时，关好识别记录以后直接结束进程（terminate_process，退出码照旧；不用 os._exit）；事件循环带着非 0 的退出码结束、
  收尾的某一步出错时，记进错误记录、照常退出，run() 不按启动失败弹窗。
- 子进程：离屏真的建出悬浮窗，把截屏、找窗口、改窗口层次、全局热键、托盘、下载、试写程序文件夹、设置和识别记录的文件
  都换成假的或指到临时文件夹，让它显示后马上退出：退出码是 0、没有没接住的错误、错误记录里什么都没写，三样照样调用了。
- 子进程：识别线程卡在一次很慢的调用里（不看停止请求）：等满上限以后照常结束（退出码 0；让 Qt 直接终止进程是 0xC0000409，用 os._exit
  结束偶尔是 0xC0000005），错误记录里记了一笔，识别记录照样关了。
- 子进程：terminate_process 的退出码照传，不跑 atexit 这类收尾。

打桩打在 main()、build() 实际所在的模块上：它们以后搬到别的模块时照样打得中；打不中（名字不在了）时 mock.patch.object
直接报错，不会真的启动识别、注册热键。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor.ui import single_instance as si
from advisor.ui.overlay_parts import app as overlay_app
from tests.support import sandbox
from tests.support.errors import ErrorLog, capture_errors

ROOT = Path(__file__).resolve().parents[1]
# 三样收尾，子进程里每调用一样记一行（调用的当时就写进文件：收尾以后可能直接结束进程，等不到最后再报）
SHUTDOWN = {"watcher.stop", "stop_download", "recorder.close"}

# 子进程里跑的脚本。参数：项目根目录、临时文件夹（设置文件、错误记录、调用记录都写在这里），可选再跟「stuck 秒数」：识别线程
# 用真的 Watcher，但它卡在一次很慢的调用里、不看停止请求，退出时等它的上限缩短到这么多秒（STUCK_WAIT_S）。
STUCK_WAIT_S = 0.5
CHILD = r"""
import os, sys, time
from pathlib import Path
from unittest import mock

root, work = sys.argv[1], Path(sys.argv[2])
stuck = sys.argv[3:4] == ["stuck"]
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
sys.path.insert(0, root)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from advisor import paths as app_paths
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui.overlay_parts import wording
from advisor.ui import overlay
from advisor.ui import single_instance as si
from advisor.ui import winapi
from advisor.ui.overlay_parts.follow import Tray, WindowFollower
from advisor.ui.overlay_parts.background import Background


def note(name):
    with open(work / "calls.txt", "a", encoding="utf-8") as f:
        f.write(name + "\n")


main_mod = sys.modules[overlay_app.main.__module__]
build_mod = sys.modules[main_mod.build.__module__]
err_mod = sys.modules[overlay.log_error.__module__]
shutdown_mod = sys.modules[overlay.Overlay.shutdown.__module__]

watcher = mock.MagicMock()  # 假的识别线程：按已经停下回答
watcher.stop.side_effect = lambda *a, **k: note("watcher.stop") or True
watcher.isRunning.return_value = False
watcher.wait.return_value = True
real_watcher_stop = main_mod.Watcher.stop


def stuck_recognition(self):  # 卡住的识别线程：一次很慢的调用（例如第一次加载文字识别模型），中途不看停止请求
    time.sleep(60)


def watcher_stop(self, *args, **kwargs):
    note("watcher.stop")
    return real_watcher_stop(self, *args, **kwargs)


gamelog = mock.MagicMock()
gamelog.GameRecorder.return_value.open.return_value.close.side_effect = lambda *a, **k: note("recorder.close")
real_stop_download = Background.stop_download


def stop_download(self):
    note("stop_download")
    real_stop_download(self)


os.environ[wording.NO_DOWNLOAD_ENV] = "1"  # 不下载识别用的图片，也不画皮肤预览图
patches = [
    mock.patch.object(build_mod, "STATE_FILE", work / "state.json"),              # 设置文件放在临时文件夹
    mock.patch.object(build_mod, "gamelog", gamelog),                             # 识别记录不写 logs\games
    mock.patch.object(build_mod, "open_scheme_store", return_value=None),         # 不读写筹码方案文件
    mock.patch.object(main_mod, "Hotkey", return_value=mock.MagicMock()),         # 不注册全局热键
    mock.patch.object(main_mod, "expected_capture", return_value="window"),
    mock.patch.object(main_mod.Services, "load", return_value=main_mod.Services()),  # 不接推荐日志等：不读写 logs
    mock.patch.object(si, "listen", return_value=mock.MagicMock()),               # 不占防多开的名字
    mock.patch.object(winapi, "probe_game", return_value=None),                   # 不找真实的游戏窗口
    # 离屏窗口的句柄是假的：真去改窗口层次，会改到别的程序的窗口上
    mock.patch.object(winapi, "set_topmost", return_value=True),
    mock.patch.object(winapi, "put_below", return_value=True),
    mock.patch.object(winapi, "bring_to_front", return_value=True),
    mock.patch.object(winapi, "foreground", return_value=(0, False)),
    mock.patch.object(Tray, "setup_tray", return_value=False),                    # 不建托盘图标
    mock.patch.object(WindowFollower, "start_tracking"),                          # 不起定时查游戏窗口
    mock.patch.object(Background, "stop_download", stop_download),
    mock.patch.object(err_mod, "ERR_FILE", work / "overlay_err.txt"),             # 错误记录写进临时文件夹
    mock.patch.object(app_paths, "unwritable_dirs", return_value=[]),             # 不试写真实的程序文件夹（logs、reports 等）
]
if stuck:  # 真的识别线程（不截屏：取帧之前就卡住），退出时只等 STUCK_WAIT_S 秒
    patches += [
        mock.patch.object(main_mod.Watcher, "_loop", stuck_recognition),
        mock.patch.object(main_mod.Watcher, "stop", watcher_stop),
        mock.patch.object(shutdown_mod, "SHUTDOWN_WATCHER_S", float(sys.argv[4])),
    ]
else:
    patches.append(mock.patch.object(main_mod, "Watcher", return_value=watcher))  # 不启动识别线程、不截屏
for p in patches:
    p.start()

app = QApplication(["acc-exit-test"])
# 事件循环一转起来就退出（和托盘菜单的「退出」同一个 quit）；在 exec 之前被别处转了一下事件时 quit 不起作用，所以反复发
quit_timer = QTimer()
quit_timer.timeout.connect(app.quit)
quit_timer.start(0)
overlay_app.main()
"""


# 子进程里跑的脚本：调用 terminate_process（参数：项目根目录、标记文件、退出码）。照常退出时 atexit 会写标记文件，
# terminate_process 返回了也会写
TERMINATE_CHILD = r"""
import atexit, sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
marker = Path(sys.argv[2])
from advisor.ui import overlay
from advisor.ui.overlay_parts import app as overlay_app

atexit.register(lambda: marker.write_text("atexit", encoding="utf-8"))
sys.stdout.write("before\n")
sys.stdout.flush()
overlay_app.terminate_process(int(sys.argv[3]))
marker.write_text("returned", encoding="utf-8")
"""


class Ended(BaseException):
    """测试里代替 terminate_process：记下退出码，让 main() 停在这里（真的结束进程会结束跑测试的进程）。"""


class MainShutdownTest(unittest.TestCase):
    """main() 退出时调用悬浮窗的 shutdown()（停识别线程、下载等），再关识别记录。界面、识别、热键、等待线程都换成假的。"""

    def run_main(self, code=0, stopped=True, listener=None):
        """跑一遍 main()（事件循环马上带着 code 结束，shutdown() 返回 stopped）。返回（main 抛出的异常, 先后记录, 窗口）。"""
        from advisor.ui import overlay

        main_mod = sys.modules[overlay_app.main.__module__]
        order = []
        app = mock.Mock()
        app.exec.side_effect = lambda: order.append("exec") or code
        window = mock.MagicMock()
        window.shutdown.side_effect = lambda: order.append("shutdown") or stopped
        recorder = mock.Mock()
        recorder.close.side_effect = lambda: order.append("recorder.close")
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=recorder), save=lambda force=False: None)

        def terminate(n):
            order.append(f"terminate({n})")
            raise Ended(n)

        with mock.patch.object(main_mod, "build", return_value=(app, window, session, threading.Lock())), \
                mock.patch.object(main_mod, "Watcher", return_value=mock.MagicMock()), \
                mock.patch.object(main_mod, "Hotkey", return_value=mock.MagicMock()), \
                mock.patch.object(main_mod, "expected_capture", return_value="window"), \
                mock.patch.object(main_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", return_value=listener or mock.MagicMock()), \
                mock.patch.object(main_mod, "declare_high_performance"), \
                mock.patch.object(main_mod, "terminate_process", side_effect=terminate), \
                mock.patch.object(os, "_exit", side_effect=AssertionError("不该用 os._exit 结束进程")), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            try:
                overlay_app.main()
            except BaseException as e:  # noqa: BLE001  SystemExit / Ended
                return e, order, window
        self.fail("main() 没有结束")

    def test_main_calls_shutdown_and_closes_recorder(self):
        errors = capture_errors(self)
        raised, order, window = self.run_main()
        self.assertIsInstance(raised, SystemExit)
        self.assertEqual(raised.code, 0)
        self.assertEqual(order, ["exec", "shutdown", "recorder.close"])  # 不直接结束进程
        self.assertEqual(errors.entries(), [])

    def test_stuck_watcher_ends_process_after_recorder_closed(self):
        """shutdown() 说识别线程没停下：关好识别记录以后直接结束进程（退出码 0），不让 Qt 析构还在跑的线程。"""
        capture_errors(self)
        raised, order, _ = self.run_main(stopped=False)
        self.assertIsInstance(raised, Ended)
        self.assertEqual(order, ["exec", "shutdown", "recorder.close", "terminate(0)"])

    def test_nonzero_code_is_not_startup_failure(self):
        """事件循环带着非 0 的退出码结束：记一笔，退出码照旧；经 run() 启动时不按启动失败弹窗。"""
        import advisor.ui as ui

        errors = capture_errors(self)
        raised, _, _ = self.run_main(code=3)
        self.assertIsInstance(raised, ui.Exited)
        self.assertEqual(raised.code, 3)
        self.assertEqual([t for t, _ in errors.entries()], ["拾星助手退出，退出码 3"])
        with mock.patch.object(si, "claim", return_value=True), \
                mock.patch("advisor.ui.overlay_parts.app.main", side_effect=raised), \
                mock.patch.object(ui, "report_startup_error", side_effect=AssertionError("不该按启动失败弹窗")):
            with self.assertRaises(SystemExit) as again:
                ui.run()
        self.assertEqual(again.exception.code, 3)

    def test_startup_exit_code_still_reported(self):
        """对照：启动时就退出（build() 读到坏的适配表等，普通的 SystemExit）照旧按启动失败弹窗。"""
        import advisor.ui as ui

        capture_errors(self)
        with mock.patch.object(si, "claim", return_value=True), \
                mock.patch("advisor.ui.overlay_parts.app.main", side_effect=SystemExit("适配表存在错误")), \
                mock.patch.object(ui, "report_startup_error") as report:
            with self.assertRaises(SystemExit):
                ui.run()
        report.assert_called_once()

    def test_exit_step_error_logged(self):
        """收尾的某一步出错（停等待线程出错）：记进错误记录，后面几步照做，照常退出。"""
        errors = capture_errors(self)
        listener = mock.Mock()
        listener.stop.side_effect = OSError("关不掉")
        raised, order, _ = self.run_main(listener=listener)
        self.assertIsInstance(raised, SystemExit)
        self.assertEqual(raised.code, 0)
        self.assertEqual(order, ["exec", "shutdown", "recorder.close"])
        entries = errors.entries()
        self.assertEqual([t for t, _ in entries], ["退出时收尾出错（拾星助手照常退出）"])
        self.assertIn("关不掉", entries[0][1])


class ExitSubprocessTest(unittest.TestCase):
    """在子进程里真的走一遍 main()：显示后马上退出，退出码是 0，没出错，三样收尾都做了。"""

    def run_child(self, *extra) -> tuple[subprocess.CompletedProcess, Path]:
        work = sandbox.temp_dir(self, "test-exitflow-")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-c", CHILD, str(ROOT), str(work), *extra], cwd=str(ROOT), env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return proc, work

    def test_main_exits_cleanly_in_subprocess(self):
        proc, work = self.run_child()
        detail = f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        self.assertEqual(proc.returncode, 0, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)  # 界面回调、其他线程里没接住的错误
        err = work / "overlay_err.txt"
        self.assertFalse(err.exists(), err.read_text(encoding="utf-8", errors="replace") if err.exists() else "")
        calls = work / "calls.txt"
        done = set(calls.read_text(encoding="utf-8").split()) if calls.exists() else set()
        self.assertLessEqual(SHUTDOWN, done)

    def test_stuck_watcher_exits_normally(self):
        """识别线程卡在一次很慢的调用里：等满上限后照常结束（不让 Qt 等 3 秒后直接终止进程，那样退出码是 0xC0000409；用 os._exit
        结束时约四次里有一次 0xC0000005），错误记录里写明没停下、当时停在哪里，识别记录照样关了。"""
        proc, work = self.run_child("stuck", str(STUCK_WAIT_S))
        detail = f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        self.assertEqual(proc.returncode, 0, detail)
        self.assertNotIn("Traceback", proc.stderr, detail)
        log = ErrorLog(work / "overlay_err.txt")
        entries = log.entries()
        self.assertEqual([t for t, _ in entries], [f"退出时后台识别没有在 {STUCK_WAIT_S:g} 秒内停下，已直接结束拾星助手"],
                         log.text())
        self.assertIn("stuck_recognition", entries[0][1])  # 当时停在哪里
        calls = work / "calls.txt"
        done = set(calls.read_text(encoding="utf-8").split()) if calls.exists() else set()
        self.assertLessEqual(SHUTDOWN, done)

    def test_terminate_process_passes_code(self):
        """terminate_process：退出码照传；不跑 atexit 这类收尾，调用之后的代码也不再执行；之前写出去的输出照样在。"""
        marker = sandbox.temp_dir(self, "test-terminate-") / "marker.txt"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-B", "-c", TERMINATE_CHILD, str(ROOT), str(marker), "3"], cwd=str(ROOT),
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 3, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
        self.assertEqual(proc.stdout, "before\n")
        self.assertFalse(marker.exists(), marker.read_text(encoding="utf-8") if marker.exists() else "")


if __name__ == "__main__":
    unittest.main()
