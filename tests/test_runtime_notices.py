"""这次运行里一直留着的三条提示：识别已停止、程序文件夹写不进、「替代高 DPI 缩放行为」被改过。

- 识别线程：开头导入识别库出错、循环里出了没接住的错时线程结束，记进错误记录、发 recog_stopped；没报错就结束的（不是退出时
  叫停的）悬浮窗收到 QThread 的 finished 也算；退出时叫停的、会自动重试的（识别组件启动失败）不算。
- 识别停下以后：状态胶囊、一行显示的摘要、「识别」卡片收起时的摘要以「识别 已停止」开头（不再写「识别 照常进行」「识别中」，
  暂停时也一样）；等待提示不再写「等待筹码三选一界面…」，换成常驻那一句的后半句；按热键不请识别、不显示「正在识别…」，
  停下之前还显示着的随之收起。
- 程序文件夹：启动时试写（paths.unwritable_dirs：还没有的先建好、试写的临时文件删掉；识别图片按实际要写的那一处试）；写不进
  时常驻提示、记错误记录，错误记录也写不进时照样提示；识别图片的文件夹在程序文件夹里、又写不进时不下载识别图片，别的文件夹
  写不进时照常下载；只有个人目录里放识别图片的那一处写不进时不算程序文件夹写不进；发行版的识别图片退回了程序文件夹时不另出
  提示、只记一条错误记录。
- 下载识别图片：写盘出错和网络出错分开（assets.is_write_error）；失败的全是写盘出错时提示换成写不进的那一句（「重试」照旧；
  图片在程序文件夹里时启动时试写得出的那一行不重复，在个人目录里时换成存不进个人目录的那一句），混着网络出错时照旧说检查
  网络；前几张的原因写进错误记录。
- DPI：查到的值不是按每块屏幕的缩放（2）时常驻提示、记错误记录；系统没有查询用的函数、Qt 用的不是 Windows 窗口平台时不查。
- 几条同时出现时的先后；一直留着（不被一次性提示冲掉、没有「关闭本条提示」、边框不变色）；main() 的接线和先后。

离屏，不截屏、不找真实的游戏窗口：识别线程用假的取帧对象和识别流程，下载换成假的（不联网），DPI 的系统调用打桩；试写只写
测试沙箱里的临时文件夹；错误记录写进每个测试自己的临时文件。
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QLabel

from advisor import assets, diagnostics, fileio, paths
from advisor.data import load_db
from advisor.ui import single_instance as si
from advisor.ui import winapi
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import app as app_mod
from advisor.ui.overlay_parts import background as background_mod
from advisor.ui.overlay_parts import notices as notices_mod
from advisor.ui.overlay_parts import status as status_mod
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.overlay_parts.wording import (ASSETS_TIP, ASSETS_TIP_FAILED, CAPSULE_KEYS, DOWNLOAD_FAILED,
                                              DOWNLOAD_UNWRITABLE, DPI_CHANGED, FOLDER_UNWRITABLE, NO_DOWNLOAD_ENV,
                                              NOTICE_AREA, PAUSED_BIT, RECOG_FRAME, RECOG_STARTUP, RECOG_STOPPED,
                                              RECOG_STOPPED_WAITING, RUNNING, RUNNING_BIT, STOPPED, STOPPED_BIT)
from advisor.ui.services import Services
from advisor.ui.text import SUMMARY_RUNNING
from advisor.vision import pipeline
from tests.support import sandbox
from tests.support.errors import capture_errors
from tests.support.overlay_cases import APP, FakeHotkey, FakeWatcher, expire_flash, probe
from tests.support.waits import wait_until
from tests.test_overlay_watcher import FakeSource, SameScreen

STOPPED_TITLE = "后台识别出错，识别已停止（拾星助手仍在运行）"
ENDED_TITLE = "后台识别意外结束，识别已停止（拾星助手仍在运行）"
FOLDER_TITLE = "程序文件夹写不进（拾星助手仍在运行）"
DPI_TITLE = "「替代高 DPI 缩放行为」被改过，识别会认错（拾星助手仍在运行）"


def global_keys(w: Overlay) -> list[str]:
    """标题栏下方全局提示的各行（键），按显示的先后。"""
    box = w.global_notice_box
    return [box.itemAt(i).widget().objectName().removeprefix("notice-") for i in range(box.count())]


def notice_label(w: Overlay, key: str) -> QLabel:
    box = w.global_notice_box
    row = next(box.itemAt(i).widget() for i in range(box.count())
               if box.itemAt(i).widget().objectName() == f"notice-{key}")
    return next(x for x in row.findChildren(QLabel) if x.objectName().startswith("notice_"))


def unwritable_err_file(test: unittest.TestCase) -> Path:
    """写不进的错误记录：放在一个普通文件「下面」（建不了文件夹），log_error 写的时候出错。"""
    blocker = sandbox.temp_dir(test, "errors-blocked-") / "不是文件夹"
    blocker.write_text("x", encoding="utf-8")
    return blocker / "overlay_err.txt"


def err_module():
    """定义 log_error 的模块（它按自己的 ERR_FILE 写，同 tests/support/errors.py）。"""
    import advisor.ui as ui

    return sys.modules[ui.log_error.__module__]


class OverlayCase(unittest.TestCase):
    """建一个离屏悬浮窗（不接后台识别、不建托盘），错误记录写进这个测试自己的临时文件。"""

    def setUp(self):
        self.db = load_db()
        self.errors = capture_errors(self)
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))

    def overlay(self, services: Services | None = None) -> Overlay:
        w = Overlay(self.db, self.session, threading.Lock(), services=services or Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def titles(self) -> list[str]:
        return [title for title, _ in self.errors.entries()]


class WordingTest(unittest.TestCase):
    def test_texts(self):
        """三句和这里写的逐字相同；都放在标题栏下方（全局提示）。"""
        self.assertEqual(RECOG_STOPPED, "识别已停止，请重新打开拾星助手")
        self.assertEqual(FOLDER_UNWRITABLE, "程序文件夹写不进（可能是受保护的文件夹，或被杀毒软件拦了），"
                                            "请把拾星助手整个文件夹挪到别处，例如 D:\\拾星助手")
        self.assertEqual(DPI_CHANGED.format(exe="app\\ACC.exe"),
                         "检测到「替代高 DPI 缩放行为」被改过，识别会认错：请在 app\\ACC.exe 的属性 → 兼容性 → "
                         "更改高 DPI 设置里取消勾选")
        self.assertEqual({NOTICE_AREA[k] for k in ("stopped", "dpi", "folder")}, {"global"})


# ------------------------------------------------------------ 识别线程停下了


class BrokenProcessor:
    """建识别流程时出错（例如识别要用的图片读不出）：没接住的错跑出识别线程的循环。"""

    def __init__(self, *args):
        raise RuntimeError("建识别流程出错（测试）")


class WatcherCrashTest(OverlayCase):
    """识别线程出了没接住的错：线程结束，错误记录写明原文，发 recog_stopped；悬浮窗常驻「识别已停止」，错误记录只记一笔。
    会自动重试的、退出时叫停的不算。悬浮窗照 main() 的接法接上两个信号（recog_stopped 和 QThread 的 finished）。"""

    def watch(self, w: Overlay, watcher: Watcher) -> list:
        """照 main() 接上：返回直接收到 recog_stopped 的记录（在识别线程里收，不等事件循环）。"""
        stopped = []
        watcher.recog_stopped.connect(lambda: stopped.append(threading.current_thread().name),
                                      type=Qt.DirectConnection)
        watcher.recog_stopped.connect(w.on_recog_stopped)
        watcher.finished.connect(w.on_recog_stopped)
        w.watcher = watcher
        return stopped

    def test_import_error_at_start(self):
        """_loop 开头的导入出错（识别库的文件被杀毒软件隔离、解压不完整等）：线程结束，不悄悄退出。"""
        w = self.overlay()
        saved = pipeline.limit_cv_threads
        del pipeline.limit_cv_threads  # from ...vision.pipeline import …, limit_cv_threads 导入出错
        self.addCleanup(setattr, pipeline, "limit_cv_threads", saved)
        source = FakeSource()
        watcher = Watcher(self.db, self.session, w.lock, manual=True, source=source)
        stopped = self.watch(w, watcher)
        watcher.start()
        self.assertTrue(watcher.wait(10000))
        self.assertEqual(len(stopped), 1)
        self.assertTrue(watcher.crashed)
        self.assertFalse(watcher.stopping)
        self.assertTrue(wait_until(lambda: w.notice_model.recog_stopped))
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], [STOPPED_TITLE])  # 悬浮窗那边不另记「意外结束」
        self.assertIn("limit_cv_threads", entries[0][1])  # 原文（调用栈）只进错误记录
        self.assertEqual(source.closed, 0)  # 还没开始用取帧对象就出错了
        self.assertEqual(w.notice_model.texts()["stopped"], "⚠ " + RECOG_STOPPED)

    def test_error_in_loop(self):
        """循环里出了没接住的错（建识别流程出错）：同样停下；取帧对象照常关掉。"""
        w = self.overlay()
        source = FakeSource()
        with mock.patch("advisor.vision.pipeline.FrameProcessor", BrokenProcessor):
            watcher = Watcher(self.db, self.session, w.lock, manual=False, source=source)
            stopped = self.watch(w, watcher)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(len(stopped), 1)
        self.assertEqual(source.closed, 1)
        self.assertTrue(wait_until(lambda: "stopped" in w.notice_model.texts()))
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], [STOPPED_TITLE])
        self.assertIn("建识别流程出错（测试）", entries[0][1])

    def test_ended_without_error(self):
        """没报错就结束了（不是退出时叫停的）：悬浮窗收到 QThread 的 finished 也算，另记一笔「意外结束」。"""
        w = self.overlay()
        with mock.patch.object(Watcher, "_loop", lambda self: None):
            watcher = Watcher(self.db, self.session, w.lock, manual=True, source=FakeSource())
            stopped = self.watch(w, watcher)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(stopped, [])
        self.assertFalse(watcher.crashed)
        self.assertTrue(wait_until(lambda: w.notice_model.recog_stopped))
        self.assertEqual(self.titles(), [ENDED_TITLE])
        self.assertEqual(w.notice_model.texts()["stopped"], "⚠ " + RECOG_STOPPED)

    def test_stopped_on_exit_is_not_shown(self):
        """退出时叫停的（Watcher.stop）：线程照常结束，不提示、不记错误记录；之后再收到 finished 也一样。"""
        w = self.overlay()
        seen = []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen):
            watcher = Watcher(self.db, self.session, w.lock, manual=True, source=FakeSource())
            self.watch(w, watcher)
            watcher.finished.connect(lambda: seen.append(1))
            watcher.start()
            self.assertTrue(wait_until(watcher.isRunning, 5000))
            self.assertTrue(watcher.stop(10000))
        self.assertTrue(watcher.stopping)
        self.assertTrue(wait_until(lambda: seen, 5000))
        APP.sendPostedEvents()
        APP.processEvents()
        w.on_recog_stopped()  # 收到得晚一点也一样
        self.assertFalse(w.notice_model.recog_stopped)
        self.assertNotIn("stopped", w.notice_model.texts())
        self.assertEqual(self.errors.entries(), [])

    def test_startup_retry_is_not_stopped(self):
        """识别组件启动失败（会重试，RECOG_STARTUP）：线程照常跑着，不算停止。窗口截取组件的线程初始化换成假的。"""
        from advisor.ui import screen
        from advisor.vision import window_capture

        w = self.overlay()
        recog = []
        with mock.patch.object(window_capture, "init_thread"), mock.patch.object(window_capture, "uninit_thread"), \
                mock.patch.object(screen, "FrameSource", side_effect=OSError("组件用不了（测试）")), \
                mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen):
            watcher = Watcher(self.db, self.session, w.lock, manual=False)
            self.watch(w, watcher)
            watcher.recog_error.connect(recog.append, type=Qt.DirectConnection)
            watcher.start()
            try:
                self.assertTrue(wait_until(lambda: recog, 10000))
                APP.processEvents()
                self.assertTrue(watcher.isRunning())
                self.assertFalse(w.notice_model.recog_stopped)
            finally:
                self.assertTrue(watcher.stop(10000))
        APP.processEvents()
        self.assertEqual(recog, [RECOG_STARTUP])
        self.assertFalse(watcher.crashed)
        self.assertNotIn("stopped", w.notice_model.texts())

    def test_error_record_unwritable(self):
        """错误记录本身也写不进：照样发 recog_stopped，悬浮窗照样提示。"""
        w = self.overlay()
        source = FakeSource()
        with mock.patch.object(err_module(), "ERR_FILE", unwritable_err_file(self)), \
                mock.patch("advisor.vision.pipeline.FrameProcessor", BrokenProcessor):
            watcher = Watcher(self.db, self.session, w.lock, manual=False, source=source)
            stopped = self.watch(w, watcher)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
            self.assertTrue(wait_until(lambda: w.notice_model.recog_stopped))
        self.assertEqual(len(stopped), 1)

    def test_no_watcher(self):
        """还没接上后台识别（测试、预览）：不管。"""
        w = self.overlay()
        w.on_recog_stopped()
        self.assertFalse(w.notice_model.recog_stopped)


# ------------------------------------------------------------ 识别停下以后：状态胶囊、摘要、热键


class StoppedWatcher(FakeWatcher):
    """出错停下了的识别线程：不是退出时叫停的，错误已经由它记过（Watcher.crashed），悬浮窗不另记一笔。"""

    stopping = False
    crashed = True


class AfterStopTest(OverlayCase):
    def test_capsule_priority(self):
        """识别本身的状态：已停止 ＞ 已暂停 ＞ 暂不可用 ＞ 照常进行。「识别 已停止」橙色，摘要里照写主体、放不下时也不去掉；
        没有悬停说明（常驻的那一句已经说明）；状态胶囊里排在角色胶囊前面。"""
        running = SimpleNamespace(capsule="running", tip="away")
        paused = SimpleNamespace(capsule="paused", tip="minimized")
        for plan, blocked in ((running, False), (paused, False), (running, True), (None, False)):
            with self.subTest(plan=getattr(plan, "capsule", None), blocked=blocked):
                self.assertIs(status_mod.capsule_bit(plan, blocked, stopped=True), STOPPED)
                self.assertEqual(status_mod.activity_tip(plan, blocked, stopped=True), "")
        self.assertIs(status_mod.capsule_bit(running, False), RUNNING)  # 没停下照旧
        self.assertEqual((STOPPED_BIT, STOPPED.tone, STOPPED.key), ("识别 已停止", "warn", "stopped"))
        self.assertEqual(STOPPED.brief_plain(), STOPPED_BIT)
        self.assertIn("stopped", CAPSULE_KEYS)
        self.assertNotIn("stopped", status_mod.SUMMARY_STEPS)

    def stopped_window(self) -> Overlay:
        """直接截取、切出游戏（「识别 照常进行」，摘要开头「识别中」）时识别停下了的悬浮窗。"""
        w = self.overlay()
        w.watcher, w.hotkey_thread = StoppedWatcher(), FakeHotkey()
        w.follower.update_activity(probe("background"))
        self.assertEqual(w.status.shown_bits[0].plain(), RUNNING_BIT)
        self.assertTrue(w.status.summary_plain.startswith(SUMMARY_RUNNING))
        w.on_recog_stopped()
        return w

    def test_status_and_summary(self):
        """停下以后：状态胶囊、一行显示的摘要（和它的悬停说明）、「识别」卡片收起时的摘要都以「识别 已停止」开头，不再写
        「识别 照常进行」「识别中」；游戏窗口最小化（本来写「识别 已暂停」）、游戏切回前台时也一样。"""
        w = self.stopped_window()
        for state in ("background", "minimized", "game"):
            with self.subTest(state=state):
                w.follower.update_activity(probe(minimized=True) if state == "minimized" else probe(state))
                bit = w.status.shown_bits[0]
                self.assertEqual((bit.key, bit.plain(), bit.tone), ("stopped", STOPPED_BIT, "warn"))
                self.assertTrue(w.status.summary_plain.startswith(STOPPED_BIT + " · "), w.status.summary_plain)
                self.assertTrue(w.status.summary_tip_text.startswith(STOPPED_BIT), w.status.summary_tip_text)
                self.assertTrue("".join(t for t, _ in w._recog_summary()).startswith(STOPPED_BIT))
                for old in (RUNNING_BIT, PAUSED_BIT):
                    self.assertNotIn(old, w.status.status_text())
                self.assertNotIn(SUMMARY_RUNNING, w.status.summary_plain)
        self.assertEqual(self.errors.entries(), [])  # 错误已经由识别线程记过，悬浮窗不另记

    def test_waiting_text_after_stop(self):
        """停下以后不再写「等待筹码三选一界面…」（识别不会再恢复，按热键也不再识别）：一行显示的摘要「识别 已停止」后面、
        「推荐」卡片的空闲文字写常驻那一句的后半句「请重新打开拾星助手」；手动模式、本局已结束时也一样。"""
        self.assertEqual(RECOG_STOPPED, f"识别已停止，{RECOG_STOPPED_WAITING}")
        waiting = "等待筹码三选一界面…"
        w = self.overlay()
        self.assertEqual(w._waiting_text(), waiting)
        self.assertEqual(w.message.text, waiting)
        w.watcher = StoppedWatcher()
        w.on_recog_stopped()
        self.assertEqual(w._waiting_text(), RECOG_STOPPED_WAITING)
        self.assertEqual(w.message.text, RECOG_STOPPED_WAITING)  # 显示着的等待提示跟着换，不当成一次性提示留着
        self.assertEqual(w.status.summary_plain, f"{STOPPED_BIT} · {RECOG_STOPPED_WAITING}")
        self.assertNotIn(waiting, w.status.summary_tip_text)
        self.assertEqual(w.idle_label.text(), RECOG_STOPPED_WAITING)
        for manual, over in ((True, False), (False, True), (True, True)):
            with self.subTest(manual=manual, over=over):
                self.assertEqual(status_mod.waiting_text(manual=manual, hotkey="F8", hotkey_ok=True, over=over,
                                                         stopped=True), RECOG_STOPPED_WAITING)

    def test_hotkey_after_stop(self):
        """停下以后按热键：不请识别，不显示「已按下 F8，正在识别…」（等不到识别完的回音，会一直留着），也不另出提示；停下之前
        刚按了热键、还显示着「正在识别…」的随之收起。"""
        w = self.overlay()
        w.watcher = StoppedWatcher()
        requested = []
        w.capture_requested.connect(lambda: requested.append(1))
        w.on_hotkey()
        busy = w.hotkeys.busy
        self.assertTrue(busy.endswith("正在识别…"), busy)
        self.assertEqual((w.message.text, requested), (busy, [1]))
        w.on_recog_stopped()
        self.assertEqual(w.hotkeys.busy, "")
        self.assertNotEqual(w.message.text, busy)
        shown = w.message.text
        w.on_hotkey()
        self.assertEqual((w.hotkeys.busy, w.message.text, requested), ("", shown, [1]))


# ------------------------------------------------------------ 一直留着、先后


class PersistentRowsTest(OverlayCase):
    def test_order_and_level(self):
        """三条都在：排在全局提示的最前面，识别已停止 ＞ DPI 设置被改过 ＞ 程序文件夹写不进，后面才是个人目录存不进、
        图片下载、「免费软件」声明；⚠ 橙色（warn），没有按钮，不是「需要您操作」（边框不变色）。"""
        model = notices_mod.NoticeModel()
        model.recog_stopped, model.folder_unwritable = True, True
        model.dpi_notice = DPI_CHANGED.format(exe="app\\ACC.exe")
        model.storage_reason, model.free_notice = "x", "免费"
        view = SimpleNamespace(game_over=False, unsupported=[], unrecorded=0)
        rows = model.rows(view, live=False, reverted_chip="", capture=None, unlocked=[], schemes="",
                          assets=("downloading", 1, 2, ""), calibrate_games=20, folder="AstralChipCompass")
        self.assertEqual([k for k, *_ in rows], ["stopped", "dpi", "folder", "storage", "assets", "free"])
        for key, text, level, buttons in rows[:3]:
            with self.subTest(key=key):
                self.assertTrue(text.startswith("⚠ "))
                self.assertEqual((level, buttons, model.area(key)), ("warn", [], "global"))
        model.notices = {r[0]: r for r in rows}
        self.assertFalse(model.needs_action())

    def test_stay_for_the_run(self):
        """一直留着：一次性提示来了又收起、切换显示方式、刷新都不收起；没有「关闭本条提示」；边框不变色；一行显示时也在。"""
        w = self.overlay()
        w.notice_model.recog_stopped = True
        w.notice_model.folder_unwritable = True
        w.notice_model.dpi_notice = DPI_CHANGED.format(exe="app\\ACC.exe")
        w.show()
        w.refresh()
        APP.processEvents()
        want = ["stopped", "dpi", "folder"]
        self.assertEqual(global_keys(w), want)
        self.assertFalse(w.expanded)
        self.assertTrue(w.global_notices.isVisibleTo(w.panel))
        self.assertEqual(notice_label(w, "stopped").objectName(), "notice_warn")
        self.assertEqual(notice_label(w, "stopped").text(), "⚠ " + RECOG_STOPPED)
        self.assertEqual([w.notices.buttons(k) for k in want], [[], [], []])
        self.assertFalse(w.needs_attention())
        w.set_flash_status("已截图识别：当前画面不是筹码三选一界面")
        self.assertEqual(global_keys(w), want)
        expire_flash(w)
        w.set_status("「某某」不在筹码表中，请输入完整的筹码名或从列表中选择", "owned")
        w.set_recog_error(RECOG_FRAME)
        expire_flash(w)
        w.set_expanded(True)
        self.assertEqual(global_keys(w), want)
        w.set_expanded(False)
        w.refresh()
        self.assertEqual(global_keys(w), want)
        self.assertEqual(w.notice_model.texts()["folder"], "⚠ " + FOLDER_UNWRITABLE)


# ------------------------------------------------------------ 程序文件夹写不进


class FolderProbeTest(OverlayCase):
    def test_program_dirs(self):
        """试写的几处：临时文件和错误记录、下载的识别图片实际要写的那一处（paths.download_dir：开发版是项目的 assets，发行版
        见 tests/test_storage.py 的 DownloadDirTest）、推荐日志、识别记录、诊断包。"""
        self.assertEqual(paths.program_dirs(), [paths.temp_dir(), paths.download_dir(), paths.logs_dir(),
                                                paths.games_dir(), paths.reports_dir()])
        self.assertEqual(paths.download_dir(), paths.assets_dir())  # 开发版

    def test_probe_writes_and_cleans_up(self):
        """写得进：还没有的文件夹先建好；试写的临时文件删掉，不留东西。"""
        base = sandbox.temp_dir(self, "probe-")
        missing = base / "logs" / "games"
        self.assertEqual(paths.unwritable_dirs([base, missing]), [])
        self.assertTrue(missing.is_dir())
        self.assertEqual([p for p in base.rglob("*") if p.is_file()], [])

    def test_probe_reports_unwritable(self):
        """写不进的报出来：哪一处、出错的类型和错误代码（不含路径）；别的照样试。"""
        base = sandbox.temp_dir(self, "probe-")
        good, bad = base / "assets", base / "reports"
        real = paths.tempfile.mkstemp

        def mkstemp(*args, dir=None, **kwargs):
            if Path(dir) == bad:
                raise PermissionError(13, "拒绝访问", str(Path(dir) / "x.tmp"))
            return real(*args, dir=dir, **kwargs)

        blocker = base / "不是文件夹"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.object(paths.tempfile, "mkstemp", side_effect=mkstemp):
            got = paths.unwritable_dirs([good, bad, blocker / "sub"])
        self.assertEqual([folder for folder, _ in got], [bad, blocker / "sub"])
        self.assertEqual(got[0][1], "PermissionError [Errno 13]")
        self.assertTrue(all(str(base) not in why for _, why in got))
        self.assertEqual([p.name for p in good.iterdir()], [])

    def test_check_folders(self):
        """有写不进的：常驻 FOLDER_UNWRITABLE（全局提示），错误记录写明是哪几处、原因（按用户看到的程序文件夹写相对路径）。"""
        w = self.overlay()
        failed = [(paths.app_dir() / ".tmp", "PermissionError [WinError 5]"),
                  (paths.app_dir() / "logs" / "games", "PermissionError [WinError 5]")]
        with mock.patch.object(paths, "unwritable_dirs", return_value=failed):
            w.notices.check_folders()
        self.assertTrue(w.notice_model.folder_unwritable)
        self.assertEqual(w.notice_model.texts()["folder"], "⚠ " + FOLDER_UNWRITABLE)
        self.assertEqual(global_keys(w), ["folder"])
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], [FOLDER_TITLE])
        self.assertIn(".tmp：PermissionError [WinError 5]", entries[0][1])
        self.assertIn("logs\\games：PermissionError [WinError 5]", entries[0][1])
        self.assertNotIn(str(paths.app_dir()), entries[0][1])  # 不写完整路径

    def test_check_folders_release_names(self):
        """发行版：错误记录里的位置按用户看到的程序文件夹写（app\\.tmp）。只换 paths.shown，不打包。"""
        w = self.overlay()
        failed = [(paths.app_dir() / "reports", "OSError [WinError 225]")]
        with mock.patch.object(paths, "unwritable_dirs", return_value=failed), \
                mock.patch.object(paths, "shown", lambda rel: f"app\\{rel}"):
            w.notices.check_folders()
        self.assertIn("app\\reports：OSError [WinError 225]", self.errors.entries()[0][1])

    def test_all_writable(self):
        w = self.overlay()
        with mock.patch.object(paths, "unwritable_dirs", return_value=[]):
            w.notices.check_folders()
        self.assertFalse(w.notice_model.folder_unwritable)
        self.assertNotIn("folder", w.notice_model.texts())
        self.assertEqual(self.errors.entries(), [])

    def test_error_record_unwritable(self):
        """错误记录本身也写不进（同在程序文件夹里）：就算了，照样提示。"""
        w = self.overlay()
        failed = [(paths.temp_dir(), "PermissionError [WinError 5]")]
        with mock.patch.object(err_module(), "ERR_FILE", unwritable_err_file(self)), \
                mock.patch.object(paths, "unwritable_dirs", return_value=failed):
            w.notices.check_folders()
        self.assertIn("folder", w.notice_model.texts())

    def test_probe_error_not_shown(self):
        """试写本身出了想不到的错：只记错误记录，不提示。"""
        w = self.overlay()
        with mock.patch.object(paths, "unwritable_dirs", side_effect=RuntimeError("试写出错（测试）")):
            w.notices.check_folders()
        self.assertNotIn("folder", w.notice_model.texts())
        self.assertEqual(self.titles(), ["试写程序文件夹出错（拾星助手仍在运行）"])

    def test_download_fallback_only_logged(self):
        """发行版下载的识别图片这次退回了程序文件夹（个人目录用不了）：不另出提示，照常下载、识别；只记一条错误记录，写明原因
        （同诊断包的写法，不含路径）和这次放在哪（按用户看到的程序文件夹写）。只换 paths 的几个函数，不打包。"""
        services = Services(diagnostics=diagnostics)
        for kind, detail, why in ((paths.FALLBACK_NO_LOCALAPPDATA, "", "未设置环境变量 LOCALAPPDATA"),
                                  (paths.FALLBACK_UNWRITABLE, "PermissionError [WinError 5]",
                                   f"无法写入 %LOCALAPPDATA%\\{paths.FOLDER_NAME}（PermissionError [WinError 5]）")):
            with self.subTest(kind=kind):
                self.errors.clear()
                w = self.overlay(services)
                w.refresh()
                before = w.notice_model.texts()
                with mock.patch.object(paths, "unwritable_dirs", return_value=[]), \
                        mock.patch.object(paths, "download_fallback", return_value=paths.Fallback(kind, detail)), \
                        mock.patch.object(paths, "shown", lambda rel: f"app\\{rel}"):
                    w.notices.check_folders()
                w.refresh()
                self.assertEqual(w.notice_model.texts(), before)  # 不另出提示
                self.assertFalse(w.notice_model.folder_unwritable or w.notice_model.assets_unwritable)
                entries = self.errors.entries()
                self.assertEqual([t for t, _ in entries], [notices_mod.DOWNLOAD_FALLBACK_TITLE])
                self.assertEqual(entries[0][1], notices_mod.DOWNLOAD_FALLBACK_DETAIL.format(reason=why, where="app\\assets"))
                self.assertNotIn(str(paths.app_dir()), entries[0][1])
        # 没有退回（开发版、发行版个人目录能用）时什么都不记
        self.errors.clear()
        w = self.overlay(services)
        with mock.patch.object(paths, "unwritable_dirs", return_value=[]):
            w.notices.check_folders()
        self.assertEqual(self.errors.entries(), [])

    def test_personal_download_dir_unwritable(self):
        """只有个人目录里放识别图片的那一处写不进（刚决定位置时试写过，极少见）：不算程序文件夹写不进（不常驻「程序文件夹写不进」，
        照常下载），错误记录换一个标题，位置写成 %LOCALAPPDATA%\\AstralChipCompass\\assets（不写完整路径）。同时有程序文件夹里
        的几处写不进时照旧提示程序文件夹写不进，下载照常（识别图片不在程序文件夹里）。"""
        local = paths.local_dir()
        self.assertIsNotNone(local)  # 测试沙箱里的 LOCALAPPDATA
        personal = local / paths.ASSETS_SUBDIR
        w = self.overlay()
        failed = [(personal, "PermissionError [WinError 5]")]
        with mock.patch.object(paths, "unwritable_dirs", return_value=failed):
            w.notices.check_folders()
        self.assertFalse(w.notice_model.folder_unwritable)
        self.assertFalse(w.notice_model.assets_unwritable)
        self.assertNotIn("folder", w.notice_model.texts())
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], [notices_mod.DOWNLOAD_DIR_TITLE])
        self.assertIn(f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets：PermissionError [WinError 5]", entries[0][1])
        self.assertNotIn(str(local), entries[0][1])
        self.errors.clear()
        other = self.overlay()
        failed = [(paths.logs_dir(), "PermissionError [WinError 5]"), (personal, "PermissionError [WinError 5]")]
        with mock.patch.object(paths, "unwritable_dirs", return_value=failed), \
                mock.patch.object(paths, "download_dir", return_value=personal):
            other.notices.check_folders()
        self.assertTrue(other.notice_model.folder_unwritable)
        self.assertFalse(other.notice_model.assets_unwritable)
        self.assertEqual([t for t, _ in self.errors.entries()], [FOLDER_TITLE])


# ------------------------------------------------------------ 下载识别图片：写盘出错和网络出错


def png(c: str) -> bytes:
    return assets.PNG_MAGIC + c.encode() * 8


class DownloadReasonTest(unittest.TestCase):
    """下载模块：写不进的图片原因以 WRITE_FAILED 开头（is_write_error 认得出），和网络出错分开；写不进的不重试。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "assets-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)

    def items(self, names):
        return [assets.AssetItem("chips", f"{c}.png", c, f"https://img/{c}.png", len(png(c)),
                                 hashlib.sha1(png(c)).hexdigest()) for c in names]

    def test_write_and_network_errors_apart(self):
        fetched = []

        def opener(req, timeout=None):
            name = req.full_url.rsplit("/", 1)[1].removesuffix(".png")
            fetched.append(name)
            if name == "n":
                raise urllib.error.URLError("连不上（测试）")
            return io.BytesIO(png(name))

        def refuse(path, data, **kwargs):
            raise PermissionError(13, "拒绝访问", str(path))

        quiet = {"opener": opener, "sleep": lambda seconds: None, "workers": 1}
        with mock.patch.object(fileio, "write_bytes", side_effect=refuse):
            ok, failed = assets.download(self.items("abn"), **quiet)
        self.assertEqual(ok, [])
        reasons = dict(failed)
        self.assertTrue(assets.is_write_error(reasons["chips/a.png"]), reasons)
        self.assertTrue(assets.is_write_error(reasons["chips/b.png"]))
        self.assertIn("PermissionError", reasons["chips/a.png"])
        self.assertFalse(assets.is_write_error(reasons["chips/n.png"]))
        self.assertEqual(fetched.count("a"), 1)  # 写不进的不再下载第二遍
        self.assertEqual(fetched.count("n"), assets.RETRIES)  # 网络出错照旧重试

    def test_is_write_error(self):
        self.assertTrue(assets.is_write_error(f"{assets.WRITE_FAILED}：PermissionError: 拒绝访问"))
        for reason in ("网络错误：连不上", "HTTP 404", "校验值不符", assets.GONE, assets.CANCELLED, None, 3):
            with self.subTest(reason=reason):
                self.assertFalse(assets.is_write_error(reason))

    def test_failure_reasons(self):
        """悬浮窗这边统一失败的写法：[(文件名, 原因)] 照原样；只给文件名的、下载整个出错时交回的图片清单原因不明；只给张数的
        为空表。"""
        item = self.items("a")[0]
        self.assertEqual(background_mod.failure_reasons([("chips/a.png", "HTTP 404"), "chips/b.png", item]),
                         [("chips/a.png", "HTTP 404"), ("chips/b.png", None), ("chips/a.png", None)])
        self.assertEqual(background_mod.failure_reasons(3), [])


class DownloadNoticeTest(OverlayCase):
    """悬浮窗上：失败的全是写盘出错时下载那一行换成写不进的那一句（「重试」照旧）；混着几张网络出错时按个别图片下不了写
    （其余识别照常、之后自动再试，见 tests/test_download_fail.py），失败的很多时照旧说检查网络；前几张的原因写进错误
    记录。下载换成假的：按 self.results 依次交回 [(文件名, 原因)]；分「个别」「大部分」用下载模块自己的 isolated、deferred。"""

    WRITE = f"{assets.WRITE_FAILED}：PermissionError: [WinError 5] 拒绝访问。"
    NET = "网络错误：连不上"

    def setUp(self):
        super().setUp()
        self.results: list = []
        self.calls = 0

        def download(items, progress, stop, stats=None):
            self.calls += 1
            return 0, self.results.pop(0)

        self.services = Services(assets=SimpleNamespace(
            missing=lambda: ["chips/a.png", "chips/b.png", "chips/c.png"], retry_items=lambda: ["chips/a.png"],
            download=download, is_write_error=assets.is_write_error, isolated=assets.isolated,
            deferred=assets.deferred))
        patcher = mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""})  # 外面设了「试启动不下载」时也照常下载
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_write_errors(self):
        w = self.overlay(self.services)
        self.results = [[("chips/a.png", self.WRITE), ("chips/b.png", self.WRITE), ("chips/c.png", self.WRITE)]]
        self.assertTrue(w.check_assets())
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + FOLDER_UNWRITABLE)
        self.assertEqual([b.text() for b in w.notices.buttons("assets")], ["重试"])
        self.assertNotIn("检查网络", w.notice_model.texts()["assets"])
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], ["识别所需的图片有 3 张下载失败（拾星助手仍在运行）"])
        self.assertIn("写盘出错 3 张，网络等其他原因 0 张", entries[0][1])
        self.assertIn(f"chips/b.png：{self.WRITE}", entries[0][1])

    def test_mixed_errors(self):
        """写盘出错混着几张网络出错：不是全是写盘出错，不换成写不进的那一句；网络出错的只有几张，按个别图片下不了写（带
        「关闭本条提示」；写不进的下次启动时就重下，不都是明天再试，写「之后会自动再试」）。失败超过 FAILED_MANY 张时（按总
        张数，写不进的也算进去）照旧说检查网络、只有「重试」。"""
        w = self.overlay(self.services)
        self.results = [[("chips/a.png", self.WRITE), ("chips/b.png", self.NET), ("chips/c.png", self.WRITE)],
                        [("chips/a.png", self.WRITE)] + [(f"heroes/{i}.png", self.NET)
                                                         for i in range(assets.FAILED_MANY + 1)]]
        w.check_assets()
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + notices_mod.few_failed_text(3, later=True))
        self.assertIn("之后会自动再试", w.notice_model.texts()["assets"])
        self.assertEqual([b.text() for b in w.notices.buttons("assets")], ["重试", "关闭本条提示"])
        detail = self.errors.entries()[0][1]
        self.assertIn("写盘出错 2 张，网络等其他原因 1 张", detail)
        self.assertIn(f"chips/b.png：{self.NET}", detail)
        w.notices.buttons("assets")[0].click()
        n = assets.FAILED_MANY + 2
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + DOWNLOAD_FAILED.format(n=n))
        self.assertEqual([b.text() for b in w.notices.buttons("assets")], ["重试"])

    def test_only_first_reasons_logged(self):
        w = self.overlay(self.services)
        self.results = [[(f"chips/{i}.png", self.NET) for i in range(8)]]
        w.check_assets()
        detail = self.errors.entries()[0][1]
        self.assertIn("chips/4.png", detail)
        self.assertNotIn("chips/5.png", detail)
        self.assertIn("（另有 3 张没有列出）", detail)

    def test_unknown_reasons_not_write(self):
        """原因不明的（只给了文件名、下载模块没有 is_write_error、isolated）不算写盘出错：照旧说检查网络。"""
        w = self.overlay(self.services)
        self.results = [["chips/a.png", "chips/b.png"]]
        w.check_assets()
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + DOWNLOAD_FAILED.format(n=2))
        self.assertIn("chips/a.png：原因不明", self.errors.entries()[0][1])
        del self.services.assets.is_write_error, self.services.assets.isolated
        self.results = [[("chips/a.png", self.WRITE)]]
        w.notices.buttons("assets")[0].click()
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + DOWNLOAD_FAILED.format(n=1))

    def test_with_folder_probe_no_duplicate(self):
        """启动时试写也写不进、下载又全是写盘出错：只留下载那一行（带「重试」），不重复；点「重试」以后下载中、下载换成
        网络出错时，启动时那一行回来。"""
        w = self.overlay(self.services)
        w.notice_model.folder_unwritable = True
        self.results = [[("chips/a.png", self.WRITE)] * 3, [("chips/a.png", self.NET)]]
        w.check_assets()
        self.assertEqual([k for k in global_keys(w) if k in ("folder", "assets")], ["assets"])
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + FOLDER_UNWRITABLE)
        w.notices.buttons("assets")[0].click()
        self.assertEqual(self.calls, 2)
        self.assertEqual([k for k in global_keys(w) if k in ("folder", "assets")], ["folder", "assets"])
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + notices_mod.few_failed_text(1))

    def test_error_record_unwritable(self):
        w = self.overlay(self.services)
        self.results = [[("chips/a.png", self.WRITE)]]
        with mock.patch.object(err_module(), "ERR_FILE", unwritable_err_file(self)):
            w.check_assets()
        self.assertEqual(w.notice_model.texts()["assets"], "⚠ " + FOLDER_UNWRITABLE)

    def test_assets_folder_unwritable_skips_download(self):
        """启动时试写，识别图片的文件夹（assets）写不进：不下载（下了也存不进），下载那一行不出现，只留常驻的「写不进」，识别
        也不因下载暂停；只是别的文件夹（logs 等）写不进时照常下载。"""
        w = self.overlay(self.services)
        with mock.patch.object(paths, "unwritable_dirs",
                               return_value=[(paths.download_dir(), "PermissionError [WinError 5]")]):
            w.notices.check_folders()
        self.assertTrue(w.notice_model.assets_unwritable)
        self.assertFalse(w.check_assets())
        self.assertEqual(self.calls, 0)
        self.assertFalse(w.background.blocked)
        texts = w.notice_model.texts()
        self.assertNotIn("assets", texts)
        self.assertEqual(texts["folder"], "⚠ " + FOLDER_UNWRITABLE)
        other = self.overlay(self.services)
        with mock.patch.object(paths, "unwritable_dirs",
                               return_value=[(paths.logs_dir(), "PermissionError [WinError 5]")]):
            other.notices.check_folders()
        self.assertFalse(other.notice_model.assets_unwritable)
        self.results = [[("chips/a.png", self.NET)]]
        self.assertTrue(other.check_assets())
        self.assertEqual(self.calls, 1)

    def test_write_errors_in_personal_dir(self):
        """识别图片在个人目录里（发行版平时）、下载失败的全是写盘出错：下载那一行换成存不进个人目录的那一句（不叫人挪程序文件夹、
        不说检查网络，「重试」照旧）；启动时程序文件夹也写不进时，那一行照旧显示（说的是别的文件夹）。悬停说明的开头写图片保存在
        个人目录里。图片在个人目录里换成 paths.personal_shown 交回它的写法来模拟，不打包。"""
        shown = f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets"
        w = self.overlay(self.services)
        w.notice_model.folder_unwritable = True
        self.results = [[("chips/a.png", self.WRITE)] * 3]
        with mock.patch.object(paths, "personal_shown", return_value=shown):
            w.check_assets()
            texts = w.notice_model.texts()
            tips = w.notice_model.tips(logs="", assets_folder=w.notices.images_shown(), capture=None,
                                       assets=w.background.assets_state, assets_personal=True)
        text = DOWNLOAD_UNWRITABLE.format(n=3, folder=paths.FOLDER_NAME)
        self.assertEqual(texts["assets"], "⚠ " + text)
        self.assertEqual(texts["folder"], "⚠ " + FOLDER_UNWRITABLE)
        self.assertEqual([k for k in global_keys(w) if k in ("folder", "assets")], ["folder", "assets"])
        self.assertEqual([b.text() for b in w.notices.buttons("assets")], ["重试"])
        self.assertNotIn("检查网络", text)
        self.assertNotIn("挪到别处", text)
        self.assertIn(f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}", text)
        self.assertEqual(tips["assets"], ASSETS_TIP.format(folder=shown) + ASSETS_TIP_FAILED)
        # 图片在程序文件夹里时（开发版、退回时）照旧：只留下载那一行，是程序文件夹写不进的那一句
        other = self.overlay(self.services)
        other.notice_model.folder_unwritable = True
        self.results = [[("chips/a.png", self.WRITE)] * 3]
        other.check_assets()
        self.assertEqual([k for k in global_keys(other) if k in ("folder", "assets")], ["assets"])
        self.assertEqual(other.notice_model.texts()["assets"], "⚠ " + FOLDER_UNWRITABLE)


# ------------------------------------------------------------ DPI


class DpiApiTest(unittest.TestCase):
    def test_missing_functions(self):
        """Windows 10 1607 之前没有这两个函数：不查（None）。"""
        with mock.patch.object(winapi.ctypes, "WinDLL", return_value=SimpleNamespace()):
            self.assertIsNone(winapi._dpi_api())
        with mock.patch.object(winapi, "_dpi_api", return_value=None):
            self.assertIsNone(winapi.dpi_awareness())

    def test_value(self):
        calls = []
        api = (lambda: calls.append("context") or 0x1234, lambda ctx: calls.append(ctx) or 1)
        with mock.patch.object(winapi, "_dpi_api", return_value=api):
            self.assertEqual(winapi.dpi_awareness(), 1)
        self.assertEqual(calls, ["context", 0x1234])

    def test_real_call(self):
        """这台电脑上真的查一次（只读本进程的设置）：系统有这两个函数时返回 -1～2 之一。"""
        if winapi._dpi_api() is None:
            self.skipTest("这个系统没有 GetThreadDpiAwarenessContext")
        self.assertIn(winapi.dpi_awareness(), (-1, 0, 1, 2))


class DpiCheckTest(OverlayCase):
    WINDOWS = SimpleNamespace(platformName=lambda: "windows")

    def check(self, value, platform=WINDOWS):
        w = self.overlay()
        with mock.patch.object(notices_mod, "QGuiApplication", platform), \
                mock.patch.object(winapi, "dpi_awareness", return_value=value) as query, \
                mock.patch.object(notices_mod, "dpi_program", return_value="app\\ACC.exe"):
            w.notices.check_dpi()
        return w, query

    def test_not_per_monitor(self):
        """按系统缩放（1）、不感知 DPI（0）：常驻提示，错误记录写上查到的值。"""
        for value, name in ((1, "按系统缩放"), (0, "不感知 DPI")):
            with self.subTest(value=value):
                self.errors.clear()
                w, _ = self.check(value)
                text = DPI_CHANGED.format(exe="app\\ACC.exe")
                self.assertEqual(w.notice_model.texts()["dpi"], "⚠ " + text)
                self.assertEqual(global_keys(w), ["dpi"])
                entries = self.errors.entries()
                self.assertEqual([t for t, _ in entries], [DPI_TITLE])
                self.assertIn(f"{value}（{name}）", entries[0][1])

    def test_per_monitor_or_unknown(self):
        """按每块屏幕的缩放（2）、系统没有查询用的函数（None）：不提示。"""
        for value in (2, None):
            with self.subTest(value=value):
                w, _ = self.check(value)
                self.assertEqual(w.notice_model.dpi_notice, "")
                self.assertNotIn("dpi", w.notice_model.texts())
        self.assertEqual(self.errors.entries(), [])

    def test_offscreen_not_checked(self):
        """Qt 用的不是 Windows 窗口平台（离屏）：不查。"""
        w, query = self.check(1, platform=SimpleNamespace(platformName=lambda: "offscreen"))
        query.assert_not_called()
        self.assertNotIn("dpi", w.notice_model.texts())

    def test_query_error(self):
        w = self.overlay()
        with mock.patch.object(notices_mod, "QGuiApplication", self.WINDOWS), \
                mock.patch.object(winapi, "dpi_awareness", side_effect=OSError("查不了（测试）")):
            w.notices.check_dpi()
        self.assertNotIn("dpi", w.notice_model.texts())
        self.assertEqual(self.titles(), ["查询本进程的 DPI 感知出错（拾星助手仍在运行）"])

    def test_program_text(self):
        """那一句里的程序文件：发行版 app\\ACC.exe；开发版写正在运行的 Python，在程序文件夹里时写相对路径。"""
        with mock.patch.object(paths, "is_frozen", return_value=True), \
                mock.patch.object(paths, "executable", return_value="D:\\拾星助手\\app\\ACC.exe"), \
                mock.patch.object(paths, "shown", lambda rel: f"app\\{rel}"):
            self.assertEqual(notices_mod.dpi_program(), "app\\ACC.exe")
        inside = paths.program_folder() / ".venv" / "Scripts" / "pythonw.exe"
        with mock.patch.object(paths, "executable", return_value=str(inside)):
            self.assertEqual(notices_mod.dpi_program(), ".venv\\Scripts\\pythonw.exe")
        with mock.patch.object(paths, "executable", return_value="Z:\\别处\\python\\pythonw.exe"):
            self.assertEqual(notices_mod.dpi_program(), "Z:\\别处\\python\\pythonw.exe")


# ------------------------------------------------------------ main() 的接线


class MainWiringTest(unittest.TestCase):
    """main()：悬浮窗显示以后接上识别停下的两个信号；试写程序文件夹、查 DPI 在显示以后、下载识别图片之前。界面、识别、
    热键、等待线程都换成假的（同 tests/test_lowend.py 的 MainTest）。"""

    def test_wiring_and_order(self):
        order: list = []
        app = mock.Mock()
        app.exec.return_value = 0
        window = mock.MagicMock()
        for name in ("show", "check_assets"):
            getattr(window, name).side_effect = lambda n=name: order.append(n)
        for name in ("check_free_notice", "check_folders", "check_dpi", "check_calibration"):
            getattr(window.notices, name).side_effect = lambda n=name: order.append(n)
        watcher = mock.MagicMock()
        watcher.recog_stopped.connect.side_effect = lambda slot: order.append("connect recog_stopped")
        watcher.finished.connect.side_effect = lambda slot: order.append("connect finished")
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=None), save=lambda force=False: None)
        with mock.patch.object(app_mod, "build", return_value=(app, window, session, threading.Lock())), \
                mock.patch.object(app_mod, "Watcher", return_value=watcher), \
                mock.patch.object(app_mod, "Hotkey", return_value=mock.MagicMock()), \
                mock.patch.object(app_mod, "expected_capture", return_value="window"), \
                mock.patch.object(app_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", return_value=mock.MagicMock()), \
                mock.patch.object(app_mod, "declare_high_performance"), \
                mock.patch.object(app_mod, "terminate_process", side_effect=AssertionError("进程内不许直接结束进程")), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit) as raised:
                app_mod.main()
        self.assertEqual(raised.exception.code, 0)
        watcher.recog_stopped.connect.assert_called_once_with(window.on_recog_stopped)
        watcher.finished.connect.assert_called_once_with(window.on_recog_stopped)
        self.assertEqual(order, ["show", "connect recog_stopped", "connect finished", "check_free_notice",
                                 "check_folders", "check_dpi", "check_assets", "check_calibration"])
        start = [c for c in watcher.method_calls if c[0] == "start"]
        self.assertEqual(start, [mock.call.start(QThread.Priority.NormalPriority)])


if __name__ == "__main__":
    unittest.main()
