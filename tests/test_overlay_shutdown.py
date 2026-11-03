"""悬浮窗退出时的收尾（Overlay.shutdown）和后台识别线程（Watcher）的停止、请求。

- shutdown()：藏起悬浮窗，停定时器，关掉开着的独立窗口，停下载并等它一个上限，停识别线程并等一个上限；识别线程等满了
  还没停下时返回 False，错误记录里写明它当时停在哪里；下载没停下只记一笔，照样往下停识别线程；关窗口出错只记一笔，
  后面几步照做。
- Watcher.stop(timeout)：返回停下了没有；卡在一次取帧里时等不到（返回 False，current_stack 看得到停在哪里），取帧回来
  发现要退出了就不再识别这一帧。
- 热键要求的识别：取走请求的那一刻别的线程又按了一次热键，这一次不会被随后的清零冲掉。

离屏，不截屏、不找真实的游戏窗口；下载、识别流程、取帧对象都是假的；错误记录写进每个测试自己的临时文件。
"""
from __future__ import annotations

import os
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from advisor.data import load_db
from advisor.ui import overlay as ov
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import NO_DOWNLOAD_ENV, POLL_MS
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.services import Services
from tests.support import qtenv
from tests.support.errors import capture_errors
from tests.support.waits import wait_until

APP = qtenv.app()


class FakeWatcher:
    """悬浮窗用到的识别线程的几样：暂停、重新加载、停（按 stops 回答停下了没有）、当时停在哪里。"""

    def __init__(self, stops=True, stack=""):
        self.stops, self.stack = stops, stack
        self.stop_calls: list = []
        self.paused: dict = {}

    def set_paused(self, reason, on, why=""):
        self.paused[reason] = on

    def reload(self):
        pass

    def stop(self, timeout_ms=3000):
        self.stop_calls.append(timeout_ms)
        return self.stops

    def current_stack(self):
        return self.stack


class FakeAssets:
    """识别所需图片的下载：一直「下载」到被要求停下（obey 时看 stop()）或 release 设上。"""

    def __init__(self, obey=True):
        self.obey = obey
        self.release = threading.Event()
        self.started = threading.Event()
        self.stopped_by_flag = False

    def missing(self):
        return ["chips/星币锤.png"]

    def download(self, items, progress, stop, stats=None):
        self.started.set()
        while not self.release.wait(0.01):
            if self.obey and stop():
                self.stopped_by_flag = True
                break
        return 0, list(items)


class BrokenWindow(QWidget):
    """关闭时自己的收尾出错的独立窗口。"""

    def closeEvent(self, event):  # noqa: N802  Qt 的虚函数
        raise RuntimeError("关窗口时的收尾出错")


def poll_timers(w: Overlay) -> list:
    """悬浮窗上正在跑的「定时查看游戏窗口」的计时器（start_tracking 建的）。"""
    return [t for t in w.findChildren(QTimer) if t.isActive() and t.interval() == POLL_MS]


class ShutdownTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.errors = capture_errors(self)
        # 等下载停下的上限放宽：下载看到停止标志就返回，机器忙时线程晚一点轮到也不算没停下（只看停没停，不看快慢）
        patcher = mock.patch.object(ov, "SHUTDOWN_DOWNLOAD_S", 30.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def overlay(self, watcher=None, assets=None) -> Overlay:
        w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock(),
                    services=Services(assets=assets))
        w.watcher = watcher
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def start_download(self, w: Overlay, assets: FakeAssets) -> None:
        """开始下载（后台线程），测试结束时放走还在「下载」的线程，等悬浮窗收到「下载完了」。"""
        with mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""}):
            self.assertTrue(w.check_assets())
        self.assertTrue(assets.started.wait(10))

        def finish():
            assets.release.set()
            self.assertTrue(wait_until(lambda: not w.background._downloading))

        self.addCleanup(finish)

    def test_stops_everything(self):
        """藏起悬浮窗、停定时器、关掉开着的设置窗口、停下载（等它停下）、停识别线程（等 SHUTDOWN_WATCHER_S 秒）。"""
        watcher, assets = FakeWatcher(), FakeAssets()
        w = self.overlay(watcher, assets)
        w.show()
        w.follower.start_tracking()
        self.assertEqual(len(poll_timers(w)), 1)
        w.dialogs.show_settings()
        dialog = w.dialogs.settings
        self.assertTrue(dialog.isVisible())
        self.start_download(w, assets)
        self.assertTrue(w.shutdown())
        self.assertTrue(w.isHidden())
        self.assertEqual(poll_timers(w), [])
        self.assertFalse(dialog.isVisible())
        self.assertTrue(assets.stopped_by_flag)  # 下载看到了停止标志；shutdown 等它停下才往下走
        self.assertEqual(watcher.stop_calls, [round(ov.SHUTDOWN_WATCHER_S * 1000)])
        self.assertEqual(self.errors.entries(), [])

    def test_no_watcher_no_download(self):
        """测试、预览里的悬浮窗：没有识别线程、没下载过，照样能收尾。"""
        w = self.overlay()
        self.assertTrue(w.shutdown())
        self.assertEqual(self.errors.entries(), [])

    def test_stuck_watcher_reported(self):
        watcher = FakeWatcher(stops=False, stack='  File "pipeline.py", line 1, in process\n')
        w = self.overlay(watcher)
        self.assertFalse(w.shutdown())
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries],
                         [f"退出时后台识别没有在 {ov.SHUTDOWN_WATCHER_S:g} 秒内停下，已直接结束拾星助手"])
        self.assertIn('File "pipeline.py", line 1, in process', entries[0][1])

    def test_window_close_error_does_not_skip_rest(self):
        """关一个独立窗口时它自己的收尾出错（Qt 把 closeEvent 里的错传回 close()）：记一笔，后面打开的窗口照样关，下载、
        识别线程照样叫停。"""
        watcher, assets = FakeWatcher(), FakeAssets()
        w = self.overlay(watcher, assets)
        broken = BrokenWindow()
        self.addCleanup(broken.deleteLater)
        w.windows.show_window(broken)
        w.dialogs.show_settings()
        dialog = w.dialogs.settings
        self.start_download(w, assets)
        self.assertTrue(w.shutdown())
        self.assertFalse(dialog.isVisible())
        self.assertTrue(assets.stopped_by_flag)
        self.assertEqual(watcher.stop_calls, [round(ov.SHUTDOWN_WATCHER_S * 1000)])
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], ["退出时收尾出错（拾星助手照常退出）"])
        self.assertIn("关窗口时的收尾出错", entries[0][1])

    def test_download_not_stopping_logged(self):
        """下载不理停止标志（下载线程怎么停得快归下载模块）：等满上限记一笔，照样往下停识别线程。"""
        watcher, assets = FakeWatcher(), FakeAssets(obey=False)
        w = self.overlay(watcher, assets)
        self.start_download(w, assets)
        with mock.patch.object(ov, "SHUTDOWN_DOWNLOAD_S", 0.05):
            self.assertTrue(w.shutdown())
        self.assertEqual([t for t, _ in self.errors.entries()], ["退出时识别所需图片的下载没有在 0.05 秒内停下"])
        self.assertEqual(len(watcher.stop_calls), 1)


class BlockingSource:
    """假的取帧对象：grab() 一直等到 release 设上（模拟一次很慢的取帧），进去时设上 entered。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = 0

    def grab(self):
        self.entered.set()
        self.release.wait(30)
        return object()

    def close(self):
        self.closed += 1


class WatcherStopTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))

    def test_never_started_counts_as_stopped(self):
        self.assertTrue(Watcher(self.db, self.session, threading.Lock()).stop(10))

    def test_stuck_grab_then_skips_recognition(self):
        """卡在取帧里：stop 等不到（返回 False），看得到停在 grab 里；取帧回来发现要退出了，这一帧不再识别，线程停下。"""
        source = BlockingSource()
        processor = mock.MagicMock()
        with mock.patch("advisor.vision.pipeline.FrameProcessor", return_value=processor):
            watcher = Watcher(self.db, self.session, threading.Lock(), source=source)
            watcher.start()
            try:
                self.assertTrue(source.entered.wait(10))
                self.assertFalse(watcher.stop(50))
                self.assertIn("in grab", watcher.current_stack())
            finally:
                source.release.set()
                stopped = watcher.stop(10000)
        self.assertTrue(stopped)
        processor.process.assert_not_called()
        self.assertEqual(source.closed, 1)  # 线程结束时照常关掉取帧对象
        self.assertEqual(watcher.current_stack(), "")  # 停下以后没有可看的


class RacyWatcher(Watcher):
    """取走热键请求（读 _requested）的那一刻，别的线程按了热键（trigger）：看这次请求会不会被随后的清零冲掉。"""

    arm = False  # 下一次读 _requested 时让别的线程按热键
    race: threading.Thread | None = None
    flag = False

    @property
    def _requested(self):
        value = self.flag
        if self.arm:
            self.arm = False
            self.race = threading.Thread(target=self.trigger)
            self.race.start()
            self.race.join(0.3)  # 别的线程这时设上请求（取请求时持锁：它要等这边取完才设得上）
        return value

    @_requested.setter
    def _requested(self, value):
        self.flag = value


class HotkeyRequestTest(unittest.TestCase):
    def test_trigger_while_taking_not_lost(self):
        db = load_db()
        watcher = RacyWatcher(db, OverlaySession(db, UiSettings(hero="凛")), threading.Lock())
        watcher.arm = True
        self.assertFalse(watcher._take_request())  # 取的那一刻还没有请求
        watcher.race.join(10)
        self.assertTrue(watcher.flag)  # 取的同时按下的那一次留着：下一圈照样识别
        self.assertTrue(watcher._take_request())
        self.assertFalse(watcher._take_request())


if __name__ == "__main__":
    unittest.main()
