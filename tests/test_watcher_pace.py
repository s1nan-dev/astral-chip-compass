"""后台识别线程（overlay_parts/watcher.py 的 Watcher）一帧识别完以后隔多久再取帧：

- 这一帧有几项子识别出错（FrameProcessor.errors，其余读数照常交出）：筹码三选一显示着时照常按 active_ms 再取（隔得久了
  容易漏看「选中」那一下）；没显示时隔 2 秒。两种情况都照旧把错记进错误记录，提示哪一项没读出来（固定中文）。
- 没出错时照旧：显示着按 active_ms，没显示按 idle_ms。
- errors 只认真的列表、元组：测试里的 MagicMock 这类不算有错（不然每帧都多等 2 秒）。
- 识别流程还有排到下一帧的读数（FrameProcessor.pending，平时一帧只做一项读数）：不等，紧接着取下一帧；手动模式也接着取；
  识别流程知道手动模式开没开、刚从暂停恢复（PendingReadsTest）。
- 认三选一那一步出错（FrameProcessor.select_failed）：不知道三选一显示没显示（chip_select_visible 还是上一帧的），照旧隔
  2 秒；按热键要求的那一帧不报「当前画面不是筹码三选一界面」，只报候选筹码没读出来；手动模式不因为这一帧接着跟随三选一。
  标记只认真的 True。识别流程置上、清掉这个标记见 SelectFailedFlagTest。
- 识别流程留下的三选一读不全的那一帧（FrameProcessor.unread_shot）每帧换到识别线程身上，识别流程那边没有时照旧留着
  （UnreadShotTest）；记下选中项用的那一帧、不采用的那一帧、对局中途像一局打完的那一帧（pick_shot、covered_shot、
  finish_shot）同样。
- 快速盯绿框的线程（vision.pick_watch.PickWatcher）跟着识别线程：开始时建、启动，识别流程建好以后接到它的交换上，识别线程
  结束时停下；运行中换端时换一个新的、接到同一个识别流程上；暂停、手动模式不跟着三选一时告诉识别流程不再盯；每分钟的识别
  统计带上它的统计（PickWatchWiringTest）。

识别线程真的起一个，取帧对象、识别流程都是假的（不截屏）；要等的时候记下等多久，不真的等，记够了就停下线程。
"""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PySide6.QtCore import Qt

from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.ui import screen
from advisor.ui.overlay_parts import watcher as watcher_mod
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.vision.pick_watch import PickWatch, PickWatcher
from advisor.ui.overlay_parts.wording import RECOG_FRAME, RECOG_ITEM, recog_items_text
from tests.support import qtenv
from tests.support.errors import capture_errors
from tests.test_vision_robust import OFFER, FlowBase

DB = load_db()
ACTIVE_MS = DB.rules["watch"]["active_ms"]
IDLE_MS = DB.rules["watch"]["idle_ms"]
# 子识别错误（FrameProcessor.errors 的一项）：（这一项的名字、异常、调用栈的文字）。界面只按名字组固定中文
ERROR = ("stars", RuntimeError("读星币数出错"), "Traceback（假的调用栈）\nRuntimeError: 读星币数出错")
SELECT_ERROR = ("select", RuntimeError("认三选一出错"), "Traceback（假的调用栈）\nRuntimeError: 认三选一出错")


class Source:
    """假的取帧对象：每次都取到一幅画面，不截屏。"""

    state = None

    def grab(self):
        return np.zeros((8, 8, 3), np.uint8)

    def close(self):
        pass


class PaceProbe(Watcher):
    """要等的时候记下等多久（None：一直等到被叫醒），不真的等；记够 waits 次就请线程停下。"""

    def __init__(self, *args, waits: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.sleeps: list = []
        self.waits = waits

    def _sleep(self, ms):
        self.sleeps.append(ms)
        if len(self.sleeps) >= self.waits:
            self.requestInterruption()


def processor(visible: bool, errors, select_failed=False):
    """假的识别流程：这一帧三选一显示着没有（visible），接住的子识别错误是 errors，认三选一出错没有（select_failed）。"""

    class Fake:
        chip_select_visible = visible

        def __init__(self, *args):
            self.errors = []
            self.select_failed = False

        def process(self, img, now, force=False):
            self.errors, self.select_failed = errors, select_failed
            return []

    return Fake


class PaceTest(unittest.TestCase):
    def setUp(self):
        qtenv.app()
        self.errors = capture_errors(self)

    def run_watcher(self, fake, manual: bool = False, hotkey: bool = False, waits: int = 1) -> SimpleNamespace:
        """跑识别线程，记够 waits 次等待就停：返回 sleeps（每次要等多少毫秒）、status（发出的提示）、done（按热键要求的
        那一帧识别完了几次）。hotkey：先按一次热键（要求识别一次）。"""
        out = SimpleNamespace(status=[], done=[])
        with mock.patch("advisor.vision.pipeline.FrameProcessor", fake):
            watcher = PaceProbe(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=manual, source=Source(),
                                waits=waits)
            watcher.status.connect(out.status.append, type=Qt.DirectConnection)  # 在识别线程里当场记下
            watcher.recog_error.connect(out.status.append, type=Qt.DirectConnection)  # 识别出错另走一条信号
            watcher.capture_done.connect(lambda: out.done.append(1), type=Qt.DirectConnection)
            if hotkey:
                watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        out.sleeps = watcher.sleeps
        return out

    def first_wait(self, visible: bool, errors) -> tuple[float, list]:
        """跑识别线程的第一帧：返回（随后要等多少毫秒, 发出的提示）。"""
        out = self.run_watcher(processor(visible, errors))
        self.assertEqual(len(out.sleeps), 1)
        return out.sleeps[0], out.status

    def test_error_while_select_visible_keeps_active_pace(self):
        wait, status = self.first_wait(True, [ERROR])
        self.assertEqual(wait, ACTIVE_MS)
        self.assertEqual(status, [RECOG_ITEM.format(item="星数")])  # 固定中文，不含报错原文
        self.assertEqual(self.errors.entries(), [("识别出错（拾星助手仍在运行）", ERROR[2])])  # 错误记录照旧

    def test_error_while_hidden_waits_two_seconds(self):
        wait, status = self.first_wait(False, [ERROR])
        self.assertEqual(wait, 2000)
        self.assertEqual(status, [RECOG_ITEM.format(item="星数")])
        self.assertEqual(len(self.errors.entries()), 1)

    def test_frame_error_fixed_text(self):
        """整帧识别出错（process 抛出，例如发行版缺了程序文件）：提示固定的中文一句，不带报错原文；原文（调用栈）照旧进
        错误记录；隔 2 秒再取。"""
        raw = "Module 'numpy.ma' was actively excluded from Nuitka compilation"

        class Broken:
            chip_select_visible = False

            def __init__(self, *args):
                self.errors, self.select_failed = [], False

            def process(self, img, now, force=False):
                raise ImportError(raw)

        out = self.run_watcher(Broken)
        self.assertEqual(out.status, [RECOG_FRAME])
        self.assertEqual(out.sleeps, [2000])
        self.assertEqual(out.done, [])  # 不是按热键要求的那一帧
        (title, detail), = self.errors.entries()
        self.assertEqual(title, "识别出错（拾星助手仍在运行）")
        self.assertIn(raw, detail)
        # 按热键要求的这一帧整帧出错：照样告诉界面这一帧完了（同一句提示去重不发时，「正在识别…」靠它收起）
        self.errors.clear()
        hotkey = self.run_watcher(Broken, manual=True, hotkey=True)
        self.assertEqual((hotkey.status, hotkey.done), ([RECOG_FRAME], [1]))
        self.errors.clear()

    def test_same_error_logged_each_time_shown_once(self):
        """同一个错一帧接一帧地出：错误记录每次都记（log_error 照调），提示只发一次（10 分钟里不再发，按时间的核对见
        tests/test_overlay_watcher.py 的 RecogErrorTextTest）。"""
        logged = []
        with mock.patch.object(watcher_mod, "log_error", side_effect=lambda *a, **k: logged.append(a[0])):
            out = self.run_watcher(processor(False, [ERROR]), waits=3)
        self.assertEqual(out.status, [RECOG_ITEM.format(item="星数")])
        self.assertEqual(logged, ["识别出错（拾星助手仍在运行）"] * 3)
        self.assertEqual(out.sleeps, [2000] * 3)

    def test_items_in_one_frame_one_sentence(self):
        """一帧里两项一起出错：每个错照旧记进错误记录，提示合成一句（每一项怎么去重见 tests/test_overlay_watcher.py 的
        RecogErrorTextTest）。"""
        hero = ("hero", RuntimeError("认角色出错"), "Traceback（假的调用栈）\nRuntimeError: 认角色出错")
        out = self.run_watcher(processor(False, [ERROR, hero]))
        self.assertEqual(out.status, [recog_items_text(["星数", "角色"])])
        self.assertEqual(len(self.errors.entries()), 2)

    def test_no_error_unchanged(self):
        self.assertEqual(self.first_wait(True, [])[0], ACTIVE_MS)
        self.assertEqual(self.first_wait(False, ())[0], IDLE_MS)
        self.assertEqual(self.errors.entries(), [])

    def test_mock_errors_not_counted(self):
        """errors 不是列表、元组（测试里的 MagicMock 等）：不算有错，照常按 idle_ms，不记错误、不提示。"""
        wait, status = self.first_wait(False, mock.MagicMock())
        self.assertEqual(wait, IDLE_MS)
        self.assertEqual(status, [])
        self.assertEqual(self.errors.entries(), [])

    def test_select_error_waits_two_seconds(self):
        """认三选一出错（上一帧还显示着三选一，chip_select_visible 是上一帧的）：不知道显示没显示，照旧隔 2 秒；错误记录
        照旧，提示「候选筹码这次没读出来……」。"""
        out = self.run_watcher(processor(True, [SELECT_ERROR], select_failed=True))
        self.assertEqual(out.sleeps, [2000])
        self.assertEqual(out.status, [recog_items_text(["候选筹码"])])
        self.assertEqual(len(self.errors.entries()), 1)

    def test_select_flag_only_true_counts(self):
        """标记不是真的 True（测试里的 MagicMock 等）：不算认三选一出错，显示着照常按 active_ms。"""
        out = self.run_watcher(processor(True, [], select_failed=mock.MagicMock()))
        self.assertEqual(out.sleeps, [ACTIVE_MS])

    def test_select_error_on_hotkey_frame(self):
        """按热键要求的那一帧认三选一出错：不报「当前画面不是筹码三选一界面」（不知道是不是），只报候选筹码没读出来；照常
        告诉界面这一帧识别完了（收起「正在识别…」）。"""
        out = self.run_watcher(processor(False, [SELECT_ERROR], select_failed=True), manual=True, hotkey=True)
        self.assertEqual(out.status, [recog_items_text(["候选筹码"])])
        self.assertEqual(out.done, [1])
        self.assertEqual(out.sleeps, [2000])

    def test_select_error_does_not_keep_manual_follow(self):
        """手动模式按了热键、这一帧认三选一出错（上一帧显示着三选一）：不因为这一帧接着跟随三选一，回到平时不截图（一直等
        到下一次按热键）。"""
        out = self.run_watcher(processor(True, [SELECT_ERROR], select_failed=True), manual=True, hotkey=True, waits=2)
        self.assertEqual(out.sleeps, [2000, None])


class Hooked(PaceProbe):
    """要等的时候先调 on_sleep(识别线程, 第几次)，再照 PaceProbe 记下、够数就停。"""

    def __init__(self, *args, on_sleep=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_sleep = on_sleep

    def _sleep(self, ms):
        if self.on_sleep is not None:
            self.on_sleep(self, len(self.sleeps) + 1)
        super()._sleep(ms)


def pending_processor(log: list, pending_frames: int, errors=()):
    """假的识别流程（没显示三选一）：前 pending_frames 帧做完以后还有排到下一帧的读数（FrameProcessor.pending）；
    记下每一帧、手动模式开关（set_manual）、刚从暂停恢复（resumed）。"""

    class Fake:
        chip_select_visible = False

        def __init__(self, *args):
            self.errors, self.select_failed, self.pending, self.frames = [], False, False, 0

        def set_manual(self, on):
            log.append(("manual", on))

        def resumed(self):
            log.append("resumed")

        def process(self, img, now, force=False):
            self.frames += 1
            log.append(("process", force))
            self.pending = self.frames <= pending_frames
            self.errors = list(errors)
            return []

    return Fake


class PendingReadsTest(unittest.TestCase):
    """识别流程一帧只做一项读数、还有排到下一帧的（pending）：识别线程不等 idle_ms，紧接着取下一帧；手动模式
    也接着取，做完为止；出错时照旧隔 2 秒。识别线程在两帧之间告诉识别流程手动模式开没开（set_manual，变了才告诉）、刚从
    暂停恢复（resumed，恢复后的第一帧之前）。"""

    def setUp(self):
        qtenv.app()
        self.errors = capture_errors(self)
        self.log: list = []

    def run_watcher(self, fake, manual=False, hotkey=False, waits=1, on_sleep=None) -> Hooked:
        with mock.patch("advisor.vision.pipeline.FrameProcessor", fake):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=manual, source=Source(),
                             waits=waits, on_sleep=on_sleep)
            if hotkey:
                watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        return watcher

    def frames(self) -> list:
        return [e for e in self.log if isinstance(e, tuple) and e[0] == "process"]

    def test_pending_no_wait(self):
        watcher = self.run_watcher(pending_processor(self.log, 2), waits=3)
        self.assertEqual(watcher.sleeps, [0, 0, IDLE_MS])
        self.assertEqual(len(self.frames()), 3)

    def test_error_still_waits(self):
        """这一帧有一项出错：照旧隔 2 秒（错误照样记、照样提示），不因为还有排着的读数就不等。"""
        watcher = self.run_watcher(pending_processor(self.log, 2, errors=[ERROR]))
        self.assertEqual(watcher.sleeps, [2000])
        self.assertEqual(len(self.errors.entries()), 1)

    def test_manual_reads_until_done(self):
        """手动模式按了热键：这一帧以后还有排着的读数时接着取帧（不是按热键要求的帧），做完才回到平时不截图。"""
        watcher = self.run_watcher(pending_processor(self.log, 2), manual=True, hotkey=True, waits=4)
        self.assertEqual(watcher.sleeps, [0, 0, IDLE_MS, None])
        self.assertEqual(self.frames(), [("process", True), ("process", False), ("process", False)])

    def test_tells_manual_and_resume(self):
        """开始时告诉识别流程手动模式没开；第 1 帧以后切到手动模式（按热键要求一帧），下一帧之前告诉它；第 2 帧以后暂停、
        再恢复（按热键要求一帧），恢复后的第一帧之前告诉它刚恢复。"""

        def on_sleep(watcher, n):
            if n == 1:
                watcher.set_manual(True)
                watcher.trigger()
            elif n == 2:
                watcher.set_paused("focus", True, why="minimized")
            elif n == 3:
                watcher.set_paused("focus", False)
                watcher.trigger()

        self.run_watcher(pending_processor(self.log, 0), waits=4, on_sleep=on_sleep)
        self.assertEqual(self.log, [("manual", False), ("process", False), ("manual", True), ("process", True),
                                    "resumed", ("process", True)])


class SelectFailedFlagTest(FlowBase):
    """FrameProcessor.select_failed：认三选一出错的那一帧置上，下一帧开头清掉；chip_select_visible 的意思不变（出错的那一帧
    保持上一帧的值，见 tests/test_vision_robust.py 的 test_select_error_does_not_close_offer）。识别函数换成假的（FlowBase）。"""

    def test_flag_follows_select_error(self):
        self.seen = SimpleNamespace(names=list(OFFER), raw=list(OFFER), rerolls=2, probabilities=(60, 37, 3),
                                    selected=None, problems=[])
        self.step(0.0)
        self.assertFalse(self.proc.select_failed)
        self.broken["select"] = [RuntimeError("认三选一出错")]
        self.step(0.3)
        self.assertIs(self.proc.select_failed, True)
        self.assertTrue(self.proc.chip_select_visible)  # 保持上一帧的值
        self.step(0.6)  # 这一帧认三选一没出错
        self.assertIs(self.proc.select_failed, False)
        self.assertTrue(self.proc.chip_select_visible)


class UnreadShotTest(unittest.TestCase):
    """识别流程留下的三选一读不全的那一帧（FrameProcessor.unread_shot）：识别线程每帧识别完照它换到自己身上
    （Watcher.unread_shot，导出诊断时经悬浮窗的 unread_source 取）；识别流程那边没有（刚重建、还没有）时照旧留着上一张；
    不是元组的（测试里的假对象这类）不算。"""

    def setUp(self):
        qtenv.app()
        self.errors = capture_errors(self)

    def test_kept_until_next_shot(self):
        first, second = ("时刻一", "画面一"), ("时刻二", "画面二")
        shots = iter([first, None, mock.MagicMock(), second])

        class Fake:
            chip_select_visible = True

            def __init__(self, *args):
                self.errors, self.select_failed, self.unread_shot = [], False, None

            def process(self, img, now, force=False):
                self.unread_shot = next(shots)
                return []

        seen = []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", Fake):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(), waits=4,
                             on_sleep=lambda w, n: seen.append(w.unread_shot))
            self.assertIsNone(watcher.unread_shot)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(seen, [first, first, first, second])

    def test_pick_and_covered_shots(self):
        """记下选中项用的那一帧、不采用的那一帧、对局中途像一局打完的那一帧（FrameProcessor.pick_shot、covered_shot、
        finish_shot）同样每帧换过来，没有的时候照旧留着。"""
        picks = iter([("时刻一", "整幅", {"slot": 1}), None, ("时刻二", "那一块", {"slot": 0, "watch": True})])
        covers = iter([None, ("时刻三", "盖住的", {"slots": [2]}), mock.MagicMock()])
        finishes = iter([("时刻四", "对局中途", {"look": "coral"}), None, mock.MagicMock()])

        class Fake:
            chip_select_visible = True

            def __init__(self, *args):
                self.errors, self.select_failed = [], False
                self.pick_shot = self.covered_shot = self.finish_shot = None

            def process(self, img, now, force=False):
                self.pick_shot, self.covered_shot, self.finish_shot = next(picks), next(covers), next(finishes)
                return []

        seen = []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", Fake):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(), waits=3,
                             on_sleep=lambda w, n: seen.append((w.pick_shot, w.covered_shot, w.finish_shot)))
            self.assertEqual((watcher.pick_shot, watcher.covered_shot, watcher.finish_shot), (None, None, None))
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        first, cover = ("时刻一", "整幅", {"slot": 1}), ("时刻三", "盖住的", {"slots": [2]})
        finish = ("时刻四", "对局中途", {"look": "coral"})
        self.assertEqual(seen, [(first, None, finish), (first, cover, finish),
                                (("时刻二", "那一块", {"slot": 0, "watch": True}), cover, finish)])


class PickWatchWiringTest(unittest.TestCase):
    """快速盯绿框的线程跟着识别线程（Watcher._start_pick_watch、_attach_pick、_watch_idle、_stop_pick）。识别流程换成假的，
    带一份真的交换（PickWatch）；取帧对象是假的（不截屏）。"""

    def setUp(self):
        qtenv.app()
        self.errors = capture_errors(self)
        self.made: list = []

    def fake(self, visible: bool = False):
        made = self.made

        class Fake:
            chip_select_visible = visible

            def __init__(self, *args):
                self.errors, self.select_failed = [], False
                self.pick_watch = PickWatch()
                self.idles = 0
                made.append(self)

            def watch_idle(self):
                self.idles += 1

            def process(self, img, now, force=False):
                return []

        return Fake

    @staticmethod
    def band():
        return SimpleNamespace(grab=lambda region, client: None, close=lambda: None, age=0.0)

    def test_started_attached_stopped(self):
        """开始时建、启动盯绿框的线程，识别流程建好以后接到它的交换上；识别线程结束时它也停下。"""
        picks, seen = [], []

        def on_sleep(w, n):
            picks.append(w._pick)
            seen.append((w._pick._exchange is self.made[-1].pick_watch, w._pick._thread.is_alive()))

        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake()):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(),
                             band_source=self.band, waits=2, on_sleep=on_sleep)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(seen, [(True, True), (True, True)])
        self.assertIs(picks[0], picks[1])
        self.assertIsNone(watcher._pick)
        self.assertFalse(picks[0]._thread.is_alive())

    def test_rebuilt_processor_reattached(self):
        """识别流程重建（识别图片下载完、重新加载）：接到新的交换上，原来的那一份关掉。"""
        seen = []

        def on_sleep(w, n):
            seen.append(w._pick._exchange)
            if n == 1:
                w.reload()

        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake()), \
                mock.patch("advisor.vision.reset_caches"):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(),
                             band_source=self.band, waits=2, on_sleep=on_sleep)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(len(self.made), 2)
        self.assertEqual(seen, [self.made[0].pick_watch, self.made[1].pick_watch])
        self.assertTrue(self.made[0].pick_watch.closed)

    def test_switch_platform_reattached(self):
        """运行中换端（set_platform）：旧的盯绿框线程停下，新建的接到现在的识别流程的交换上；识别流程不重建，交换照旧开着。"""
        from advisor.vision import capture

        seen = []

        def on_sleep(w, n):
            seen.append((w._pick, w._pick._exchange, w._pick._thread.is_alive()))
            if n == 1:
                w.set_platform(capture.TAPTAP)

        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake()):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(),
                             band_source=self.band, waits=2, on_sleep=on_sleep)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        (first, first_exchange, _), (second, second_exchange, alive) = seen
        self.assertEqual(len(self.made), 1)
        self.assertIsNot(second, first)
        self.assertIs(first_exchange, self.made[0].pick_watch)
        self.assertIs(second_exchange, self.made[0].pick_watch)
        self.assertTrue(alive)
        self.assertFalse(first._thread.is_alive())
        self.assertFalse(self.made[0].pick_watch.closed)
        self.assertEqual(self.made[0].platform, capture.TAPTAP)

    def test_no_pick_with_fake_source(self):
        """测试传入了假的取帧对象、没给 band_source：不建盯绿框的线程，照常识别。"""
        watcher = Watcher(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source())
        self.assertIsNone(watcher._start_pick_watch())

    def test_default_band_source(self):
        """平时（没传取帧对象）：盯绿框的取帧对象是 screen.BandSource，截取方式跟着这个线程的取帧对象。"""
        watcher = Watcher(DB, Session(DB, Settings(hero="凛")), threading.Lock())
        watcher._active = SimpleNamespace(state=None)
        pick = watcher._start_pick_watch()
        try:
            self.assertIsInstance(pick, PickWatcher)
            source = pick._make_source()
            self.assertIsInstance(source, screen.BandSource)
            self.assertIs(source.main, watcher._active)
            self.assertIsNone(source.grab((0, 0, 10, 10), (100, 100)))  # 还没有截取方式：不截
        finally:
            self.assertTrue(pick.stop(5))

    def test_band_source_waits_for_main_source(self):
        """这个线程的取帧对象还没建成（换端时重建没成）：盯绿框的取帧对象先不建（返回 None，盯绿框的线程下次要查时再来取），
        建成以后照常建、跟着它。"""
        watcher = Watcher(DB, Session(DB, Settings(hero="凛")), threading.Lock())
        pick = watcher._start_pick_watch()
        try:
            self.assertIsNone(pick._make_source())
            watcher._active = SimpleNamespace(state=None)
            source = pick._make_source()
            self.assertIsInstance(source, screen.BandSource)
            self.assertIs(source.main, watcher._active)
        finally:
            self.assertTrue(pick.stop(5))

    def test_idle_on_pause_and_manual(self):
        """识别暂停时、手动模式平时（等热键，不跟着三选一）告诉识别流程不再盯（watch_idle）。"""
        def on_sleep(w, n):
            if n == 1:
                w.set_paused("focus", True, why="minimized")

        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake()):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), source=Source(),
                             band_source=self.band, waits=2, on_sleep=on_sleep)
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(self.made[0].idles, 1)
        self.made.clear()
        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake()):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=True, source=Source(),
                             band_source=self.band, waits=2)
            watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(self.made[0].idles, 1)

    def test_following_select_keeps_watching(self):
        """手动模式跟着三选一（显示着）：不告诉识别流程停下，照样盯。"""
        with mock.patch("advisor.vision.pipeline.FrameProcessor", self.fake(visible=True)):
            watcher = Hooked(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=True, source=Source(),
                             band_source=self.band, waits=3)
            watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        self.assertEqual(self.made[0].idles, 0)
        self.assertEqual(watcher.sleeps, [ACTIVE_MS] * 3)

    def test_recog_stats_has_pick_watch(self):
        """每分钟的识别统计带上盯绿框这一段的统计（pick_watch）；取不到时只记错误记录，其余照记。"""
        records, logged = [], []
        session = Session(DB, Settings(hero="凛"))
        session.log_state = lambda kind, **data: records.append((kind, data))
        watcher = Watcher(DB, session, threading.Lock(), manual=True)
        watcher._pick = SimpleNamespace(take_period=lambda: {"checks": 3, "selected": 1})
        watcher._log_recog_stats()
        self.assertEqual(records[-1][1]["pick_watch"], {"checks": 3, "selected": 1})
        watcher._pick = SimpleNamespace(take_period=mock.Mock(side_effect=RuntimeError("坏了")))
        with mock.patch.object(watcher_mod, "log_error", side_effect=lambda *a, **k: logged.append(a[0])):
            watcher._log_recog_stats()
        self.assertNotIn("pick_watch", records[-1][1])
        self.assertIn("period_s", records[-1][1])
        self.assertEqual(logged, ["读取快速查看选中项的统计出错（拾星助手仍在运行）"])
        watcher._pick = None


if __name__ == "__main__":
    unittest.main()
