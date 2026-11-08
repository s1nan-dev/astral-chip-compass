"""后台识别线程（Watcher）：取帧、发截取状态、暂停、空闲会话的关闭、取帧统计、按热键识别完的通知（capture_done），
识别出错的提示（固定中文、同一句 10 分钟内只发一次，RecogErrorTextTest）。
用假的取帧对象（FakeSource）和假的识别流程（SameScreen），不真的截图、不跑识别；不建悬浮窗。"""
import contextlib
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import Qt

from advisor.data import load_db  # noqa: E402
from advisor.ui.overlay_parts import watcher as watcher_mod
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.overlay_parts.wording import (RECOG_CAPTURE, RECOG_FRAME, RECOG_ITEM, RECOG_ITEMS, RECOG_REPEAT_S,
                                              RECOG_STARTUP, recog_items_text)
from tests.support.overlay_cases import APP, capture_state


class FakeSource:
    """假的取帧对象（ui/screen.py 的 FrameSource 的样子）：grab() 依次取 frames 里的项（是异常时抛出，取完后返回
    一个新的 object()），记下取了几次、重试和关闭；state 是截取状态，可以中途换。不碰系统组件。

    close_if_idle() 依次返回 idle_waits 里的值（还要多久再检查，秒；None 为没有开着的会话），取完后一直返回 None；
    take_period() 返回 period（上次调用以来的取帧统计）并重新计，stats 是累计的统计（和 FrameSource 一样分开）。"""

    def __init__(self, frames=(), state=None, idle_waits=()):
        self.frames = list(frames)
        self.state = state if state is not None else capture_state()
        self.grabs = 0
        self.retries = 0
        self.closed = 0
        self.idle_waits = list(idle_waits)
        self.idle_calls = 0
        self.period: dict = {}
        self.taken = 0
        self.stats = {"grabs": 0, "sessions": 1}

    def grab(self):
        self.grabs += 1
        item = self.frames.pop(0) if self.frames else object()
        if isinstance(item, BaseException):
            raise item
        return item

    def retry(self):
        self.retries += 1

    def close(self):
        self.closed += 1

    def close_if_idle(self):
        self.idle_calls += 1
        return self.idle_waits.pop(0) if self.idle_waits else None

    def take_period(self):
        self.taken += 1
        out, self.period = dict(self.period), {}
        return out

    def snapshot(self):
        s = self.state
        return {"method": s.method, "problem": s.problem, "detail": s.detail, "step": s.step,
                "border_requested": s.border_requested, "build": s.build, "stats": dict(self.stats)}


class WatcherTest(unittest.TestCase):
    """后台线程：按热键要求的那一帧识别完，发 capture_done（画面没变、Session 不发事件时界面靠它收起「正在识别…」）。"""

    def test_capture_done_without_events(self):
        db = load_db()
        session = OverlaySession(db, UiSettings(hero="凛"))

        class SameScreen:
            """仍是同一个筹码三选一界面：没有新事件。"""
            chip_select_visible = True

            def __init__(self, *args):
                pass

            def process(self, img, now, force=False):
                return []

        done, events, status = [], [], []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen):
            # 手动模式：只在 trigger 时取帧
            watcher = Watcher(db, session, threading.Lock(), manual=True, source=FakeSource())
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.events.connect(lambda evs: events.extend(evs))
            watcher.status.connect(status.append)
            watcher.start()
            try:
                watcher.trigger()
                end = time.monotonic() + 3
                while not done and time.monotonic() < end:
                    APP.processEvents()
                    time.sleep(0.02)
            finally:
                watcher.stop()
        self.assertTrue(done)
        self.assertEqual((events, status), ([], []))


class WatcherPlatformTest(unittest.TestCase):
    """识别线程固定跟一个端（platform）：两个客户端同时识别时，每路的取帧对象只找自己那个端的游戏窗口，不按游戏平台设置找；
    不给时和原来一样。不真截图：建取帧对象的两个类换成假的，看交给它们的 finder。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))

    def watcher(self, platform=None):
        return Watcher(self.db, self.session, threading.Lock(), manual=True, platform=platform)

    def frame_source(self, platform):
        """建这个识别线程的取帧对象（_open_source），返回交给 screen.FrameSource 的参数。"""
        from advisor.ui import screen
        from advisor.vision import window_capture

        made = {}

        class FakeSource:
            def __init__(self, **kwargs):
                made.update(kwargs)

            def close(self):
                pass

        with mock.patch.object(window_capture, "init_thread"), \
                mock.patch.object(window_capture, "uninit_thread"), \
                mock.patch.object(screen, "FrameSource", FakeSource):
            self.watcher(platform)._open_source()
        return made

    def band_source(self, platform):
        """建这个识别线程快速盯绿框的取帧对象（_start_pick_watch 交给盯绿框线程的 make），返回交给 screen.BandSource 的参数。"""
        from advisor.ui import screen

        made = {}

        class FakeBand:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs
                made["band"] = self

            def close(self):
                pass

        watch = self.watcher(platform)
        watch._active = object()
        with mock.patch.object(screen, "BandSource", FakeBand), \
                mock.patch("advisor.vision.pick_watch.PickWatcher") as pick_cls:
            watch._start_pick_watch()
            pick_cls.call_args.args[0]()  # 盯绿框的线程第一次要查时建取帧对象
        return made["band"]

    def test_frame_source_finds_that_platform(self):
        """给了端：取帧对象的 finder 只找这个端（capture.find_game(platform=...)）。"""
        from advisor.vision import capture

        finder = self.frame_source(capture.TAPTAP)["finder"]
        with mock.patch.object(capture, "find_game", return_value="game") as find:
            self.assertEqual(finder(), "game")
        find.assert_called_once_with(platform=capture.TAPTAP)

    def test_frame_source_without_platform(self):
        """不给端（只有一个识别线程）：不传 finder，取帧对象照旧按游戏平台设置找。"""
        self.assertIsNone(self.frame_source(None)["finder"])

    def test_band_source_finds_that_platform(self):
        """快速盯绿框的取帧对象也跟着识别线程的端：finder 只找这个端。"""
        from advisor.vision import capture

        finder = self.band_source(capture.STEAM).kwargs["finder"]
        with mock.patch.object(capture, "find_game", return_value="game") as find:
            self.assertEqual(finder(), "game")
        find.assert_called_once_with(platform=capture.STEAM)

    def test_band_source_without_platform(self):
        self.assertIsNone(self.band_source(None).kwargs["finder"])


def wait_until(check, timeout=3.0):
    """等后台线程做到 check() 为真；处理 Qt 事件，让队列里的信号也能到主线程。"""
    end = time.monotonic() + timeout
    while not check() and time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.02)
    return check()


class WatcherSwitchPlatformTest(unittest.TestCase):
    """运行中换端（set_platform）：识别线程下一圈开头关掉旧的取帧对象（先关会话、再做窗口截取的线程收尾），按新的端重建
    （快速盯绿框的线程也换，新的接到现在的识别流程上）；相同的值不重建；识别流程不重建，只把它的端一起换掉。暂停着时照样换。
    取帧对象、快速盯绿框的线程、识别流程都换成假的，窗口截取的线程初始化和收尾也换成假的，记下先后。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.made, self.calls, self.picks, self.processors = [], [], [], []

    @contextlib.contextmanager
    def fakes(self):
        """换掉建取帧对象、快速盯绿框的线程、识别流程和窗口截取的线程初始化、收尾的几处。"""
        from advisor.ui import screen
        from advisor.vision import window_capture
        from advisor.vision.pick_watch import PickWatch

        made, calls, picks, processors = self.made, self.calls, self.picks, self.processors

        class FakeFrameSource:
            """假的取帧对象：记下交给它的 finder、取了几次；关的时候记一笔（和线程初始化、收尾排在一起看先后）。"""

            def __init__(self, **kwargs):
                self.finder = kwargs.get("finder")
                self.number = len(made)
                self.grabs = 0
                made.append(self)

            def grab(self):
                self.grabs += 1
                return object()

            def close(self):
                calls.append(f"close{self.number}")

            def close_if_idle(self):
                return None

            def take_period(self):
                return {}

        class FakePick:
            """假的快速盯绿框的线程：记下接到了哪些交换、停了几次。"""

            def __init__(self, make, **kwargs):
                self.stopped = 0
                self.attached = []
                picks.append(self)

            def start(self):
                pass

            def stop(self):
                self.stopped += 1
                return True

            def attach(self, exchange):
                self.attached.append(exchange)

            def take_period(self):
                return {}

        class RecordingProcessor:
            """假的识别流程：带一份真的交换（PickWatch）；端由识别线程建好以后设在 platform 上。"""

            chip_select_visible = False

            def __init__(self, *args):
                self.platform = "还没设"
                self.pick_watch = PickWatch()
                processors.append(self)

            def process(self, img, now, force=False):
                return []

        with mock.patch.object(window_capture, "init_thread", side_effect=lambda: calls.append("init")), \
                mock.patch.object(window_capture, "uninit_thread", side_effect=lambda: calls.append("uninit")), \
                mock.patch.object(screen, "FrameSource", FakeFrameSource), \
                mock.patch("advisor.vision.pick_watch.PickWatcher", FakePick), \
                mock.patch("advisor.vision.pipeline.FrameProcessor", RecordingProcessor):
            yield

    def assert_paired(self):
        """每次线程初始化都配一次收尾，收尾之前先关了那一份取帧对象，每一份只关一次（不漏不重）。"""
        calls = self.calls
        self.assertEqual(calls.count("init"), calls.count("uninit"), calls)
        closes = [c for c in calls if c.startswith("close")]
        self.assertEqual(closes, [f"close{n}" for n in range(len(self.made))], calls)
        for i, c in enumerate(calls):
            if c == "uninit":
                self.assertTrue(i > 0 and calls[i - 1].startswith("close"), calls)

    def finds(self, source):
        """这一份取帧对象的 finder 找窗口时交给 capture.find_game 的参数；没有 finder（按游戏平台设置找）时为 None。"""
        from advisor.vision import capture

        if source.finder is None:
            return None
        with mock.patch.object(capture, "find_game", return_value="game") as find:
            self.assertEqual(source.finder(), "game")
        return find.call_args.kwargs

    def test_switch_platform(self):
        from advisor.vision import capture

        made, calls, picks, processors = self.made, self.calls, self.picks, self.processors

        def switched(n):
            """第 n 份取帧对象、第 n 个快速盯绿框的线程都建好，而且后一个已经接到识别流程上（换端那一步做完了）。"""
            return len(made) >= n and len(picks) >= n and bool(picks[n - 1].attached)

        done = []
        with self.fakes():
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, platform=capture.STEAM)
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.start()
            try:
                watcher.trigger()
                self.assertTrue(wait_until(lambda: done))
                self.assertEqual(len(made), 1)
                self.assertEqual(self.finds(made[0]), {"platform": capture.STEAM})
                processor = processors[0]
                self.assertEqual(processor.platform, capture.STEAM)
                self.assertEqual(picks[0].attached, [processor.pick_watch])

                watcher.set_platform(capture.TAPTAP)
                self.assertTrue(wait_until(lambda: switched(2)))
                self.assertEqual(calls, ["init", "close0", "uninit", "init"])  # 先关会话、再收尾，再按新的端建
                self.assertEqual(picks[0].stopped, 1)  # 旧的快速盯绿框的线程也停了
                self.assertEqual(picks[1].attached, [processor.pick_watch])  # 新的接到同一个识别流程上
                self.assertEqual(self.finds(made[1]), {"platform": capture.TAPTAP})
                self.assertEqual(processor.platform, capture.TAPTAP)  # 识别流程跟着换，不重建
                self.assertEqual(processors, [processor])

                watcher.set_platform(capture.TAPTAP)  # 相同的值：不重建
                time.sleep(0.15)
                self.assertEqual((len(made), len(picks)), (2, 2))

                watcher.set_platform(None)  # 换回按游戏平台设置找
                self.assertTrue(wait_until(lambda: switched(3)))
                self.assertEqual(calls[4:], ["close1", "uninit", "init"])
                self.assertIsNone(made[2].finder)
                self.assertIsNone(processor.platform)
                self.assertEqual(picks[2].attached, [processor.pick_watch])
            finally:
                self.assertTrue(watcher.stop())
        self.assertEqual(calls[-2:], ["close2", "uninit"])  # 线程结束时最后一份照样收尾
        self.assertEqual([p.stopped for p in picks], [1, 1, 1])
        self.assert_paired()

    def test_board_handed_to_processor(self):
        """两张悬浮窗共用的牌子（select_board，Lanes 在线程开始前设上）交给建好的识别流程（FrameProcessor.board）；识别流程
        重建、线程结束时，换下来的那一份在牌子上擦掉（不再算显示着三选一）。"""
        notes = []

        class Board:
            def note(self, owner, showing, now):
                notes.append((owner, showing))

        board = Board()
        done = []
        with self.fakes():
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True)
            watcher.select_board = board
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.start()
            try:
                watcher.trigger()
                self.assertTrue(wait_until(lambda: done))
                self.assertIs(self.processors[0].board, board)
                watcher.reload()
                watcher.trigger()
                self.assertTrue(wait_until(lambda: len(done) >= 2))
                self.assertEqual(len(self.processors), 2)
                self.assertIs(self.processors[1].board, board)
                self.assertEqual(notes, [(self.processors[0], False)])
            finally:
                self.assertTrue(watcher.stop())
        self.assertEqual(notes, [(self.processors[0], False), (self.processors[1], False)])

    def test_switch_while_paused(self):
        """暂停着（找不到游戏窗口等）换端：照样换掉取帧对象和快速盯绿框的线程（新建的取帧对象不开会话，恢复以后按新的端
        找），暂停期间不取帧；还没建识别流程时没有要接的。线程结束时收尾照样配对。"""
        from advisor.vision import capture

        with self.fakes():
            watcher = Watcher(self.db, self.session, threading.Lock(), platform=capture.STEAM)
            watcher.set_paused("focus", True, why="nogame")
            watcher.start()
            try:
                self.assertTrue(wait_until(lambda: len(self.picks) >= 1))
                watcher.set_platform(capture.TAPTAP)
                self.assertTrue(wait_until(lambda: len(self.picks) >= 2))
                self.assertEqual(self.calls, ["init", "close0", "uninit", "init"])
                self.assertEqual(self.finds(self.made[1]), {"platform": capture.TAPTAP})
                self.assertEqual([s.grabs for s in self.made], [0, 0])
                self.assertEqual(self.processors, [])
                self.assertEqual([p.attached for p in self.picks], [[], []])
            finally:
                self.assertTrue(watcher.stop())
        self.assert_paired()


class SameScreen:
    """假的识别流程：仍是同一个筹码三选一界面，没有新事件。"""

    chip_select_visible = True

    def __init__(self, *args):
        pass

    def process(self, img, now, force=False):
        return []


def wait_for(items, n=1, timeout=3.0):
    """等后台线程的信号排队到主线程：处理事件，直到 items 里有 n 项。"""
    end = time.monotonic() + timeout
    while len(items) < n and time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.02)
    return items


class WatcherCaptureTest(unittest.TestCase):
    """后台线程用取帧对象（ui/screen.py 的 FrameSource）取帧：截取状态在线程开始时和每次变了时发 capture_changed；
    暂时没有画面（screen.NoFrame）不当出错；取帧出错时不退出；线程结束时关掉取帧对象。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))

    def run_watcher(self, source, until, extra=None):
        """跑一会儿后台线程（手动模式：只在按热键时取帧）：按一次热键，等 until() 为真。返回 (发出的截取状态, 状态行,
        capture_done, 后台线程)。"""
        states, status, done = [], [], []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen):
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, source=source)
            watcher.capture_changed.connect(states.append)
            watcher.status.connect(status.append)
            watcher.capture_done.connect(lambda: done.append(1))
            if extra is not None:
                extra(watcher)
            watcher.start()
            try:
                watcher.trigger()
                wait_for(done)
                end = time.monotonic() + 3
                while not until() and time.monotonic() < end:
                    APP.processEvents()
                    time.sleep(0.02)
            finally:
                watcher.stop()
            APP.processEvents()
        return states, status, done, watcher

    def test_state_emitted_and_source_closed(self):
        """线程开始时发一次截取状态；取帧后状态变了（改为截取屏幕）再发一次；线程结束时关掉取帧对象（先关会话）。"""
        source = FakeSource()
        later = capture_state("screen", "denied", "0x80070005")

        def grab():
            source.state = later  # 这一帧取的时候判定为「拒绝」，改为截取屏幕
            return object()

        source.grab = grab
        states, status, done, watcher = self.run_watcher(source, lambda: True)
        self.assertEqual([s.method for s in states], ["window", "screen"])
        self.assertIs(watcher.capture_state, later)
        self.assertEqual(source.closed, 1)
        self.assertEqual(status, [])

    def test_no_frame_is_not_an_error(self):
        """暂时没有画面（刚建会话，screen.NoFrame，是 RuntimeError 的一种）：不写「识别出错」也不写找不到游戏窗口，按热键的
        那一帧稍后再取，取到后照常识别。"""
        from advisor.ui import screen

        class NoFrame(RuntimeError):
            pass

        no_frame = getattr(screen, "NoFrame", None) or NoFrame
        source = FakeSource([no_frame("暂时没有画面"), no_frame("暂时没有画面")])
        with mock.patch.object(screen, "NoFrame", no_frame, create=True):
            states, status, done, _ = self.run_watcher(source, lambda: source.grabs >= 3)
        self.assertEqual(status, [])
        self.assertGreaterEqual(source.grabs, 3)
        self.assertTrue(done)

    def test_capture_error_keeps_running(self):
        """取帧时系统调用出错（不是找不到游戏窗口）：记进错误记录，界面显示固定的中文（不含报错原文），后台线程不退出，
        下次照常取帧。"""
        done, recog, logged = [], [], []
        source = FakeSource([OSError("截图失败")])
        with mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen), \
                mock.patch("advisor.ui.overlay_parts.watcher.log_error", side_effect=lambda *a, **k: logged.append(a[0])):
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, source=source)
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.recog_error.connect(recog.append)
            watcher.start()
            try:
                watcher.trigger()
                wait_for(recog)
                self.assertTrue(watcher.isRunning())
                watcher.trigger()
                wait_for(done, 2)
            finally:
                watcher.stop()
        self.assertEqual(recog, [RECOG_CAPTURE])
        self.assertEqual(logged, ["截图出错（拾星助手仍在运行）"])
        # 出错的那一帧也告诉界面按热键的这一帧完了（提示去重以后不发时，「正在识别…」靠它收起），第二次照常
        self.assertEqual(done, [1, 1])
        self.assertGreaterEqual(source.grabs, 2)

    def watch_once(self, source) -> tuple[list, list, list]:
        """按一次热键、等到界面收到一句提示（status 或识别出错的提示）就停：返回（status, 识别出错的提示, 错误记录里记下的
        （标题, 详细信息））。"""
        status, recog, logged = [], [], []
        with mock.patch("advisor.vision.pipeline.FrameProcessor", SameScreen), \
                mock.patch("advisor.ui.overlay_parts.watcher.log_error", side_effect=lambda *a, **k: logged.append(a)):
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, source=source)
            watcher.status.connect(status.append)
            watcher.recog_error.connect(recog.append)
            watcher.start()
            try:
                watcher.trigger()
                end = time.monotonic() + 3
                while not (status or recog) and time.monotonic() < end:
                    APP.processEvents()
                    time.sleep(0.02)
            finally:
                watcher.stop()
            APP.processEvents()
        return status, recog, logged

    def test_window_gone_shown_as_is(self):
        """找不到游戏窗口、窗口最小化（capture.WindowGone）：截取那边约定的几句固定中文原样显示，不算出错（不记错误记录，
        不发识别出错的提示）。"""
        from advisor.vision import capture

        for minimized, text in ((False, capture.NOT_FOUND), (True, capture.MINIMIZED)):
            with self.subTest(minimized=minimized):
                status, recog, logged = self.watch_once(FakeSource([capture.WindowGone(minimized=minimized)]))
                self.assertEqual((status, recog, logged), ([text], [], []))

    def test_other_runtime_error_is_capture_error(self):
        """别的 RuntimeError（不是截取那边约定的两种，例如库自己抛的）：不原样显示，按截取出错提示固定的中文；原文只进错误
        记录。"""
        raw = "Internal C++ object (QWidget) already deleted."
        status, recog, logged = self.watch_once(FakeSource([RuntimeError(raw)]))
        self.assertEqual((status, recog), ([], [RECOG_CAPTURE]))
        self.assertEqual([title for title, *_ in logged], ["截图出错（拾星助手仍在运行）"])
        self.assertIn(raw, logged[0][1])

    def test_retry_requested_before_next_grab(self):
        """游戏窗口换了（悬浮窗看到新的窗口句柄）：下次取帧前请取帧对象再试一次直接截取。"""
        source = FakeSource()
        self.run_watcher(source, lambda: source.retries >= 1, extra=lambda w: w.request_retry())
        self.assertEqual(source.retries, 1)

    # 取帧对象 take_period() 的样子（ui/screen.py 的 _Tally.summary 各项，另有 method）
    PERIOD = {"window": 11, "stale": 1, "screen": 0, "no_frame": 0, "empty": 1, "errors": 0, "grabs": 12,
              "ms_p50": 4.1, "ms_p95": 9.8, "age_ms_p95": 30.0, "method": "window"}

    def test_grab_stats_logged(self):
        """满 STATS_S 秒时把这段时间的取帧统计（取帧对象的 take_period：上次调用以来的，取完重新计；不是累计的 stats）
        写进识别记录（grab_stats），并留给诊断包。"""
        source = FakeSource()
        source.period = dict(self.PERIOD)
        source.stats = {"grabs": 500, "sessions": 2}  # 累计的统计，不写进这一条
        records = []
        self.session.log_state = lambda kind, **data: records.append((kind, data))
        with mock.patch.object(Watcher, "STATS_S", 0.0):
            _, _, _, watcher = self.run_watcher(source, lambda: bool(records))
        self.assertGreaterEqual(source.taken, 1)
        self.assertEqual(records[0], ("grab_stats", self.PERIOD))
        self.assertEqual(watcher.capture_stats, self.PERIOD)
        self.assertEqual([k for k, _ in records].count("grab_stats"), 1)  # 之后几段没有新的统计（空的）不写

    def test_grab_stats_waits_for_period(self):
        """不满 STATS_S 秒（60 秒）时不取统计、不写记录。"""
        source = FakeSource()
        source.period = dict(self.PERIOD)
        records = []
        self.session.log_state = lambda kind, **data: records.append((kind, data))
        self.run_watcher(source, lambda: source.grabs >= 1)
        self.assertGreaterEqual(source.grabs, 1)
        self.assertEqual(source.taken, 0)
        self.assertNotIn("grab_stats", [k for k, _ in records])

    def test_recog_stats_logged(self):
        """每分钟紧挨着 grab_stats 记一条 recog_stats：这一次的识别耗时统计（含每帧识别的 frame），
        加上优先级、高性能声明、低配模式和文字识别线程数；watcher.recog_stats 存着最近一条。"""
        source = FakeSource()
        source.period = dict(self.PERIOD)
        records = []
        self.session.log_state = lambda kind, **data: records.append((kind, data))
        with mock.patch.object(Watcher, "STATS_S", 0.0):
            _, _, _, watcher = self.run_watcher(
                source, lambda: any(k == "recog_stats" and "frame" in d for k, d in records))
        kinds = [k for k, _ in records]
        self.assertIn("grab_stats", kinds)
        self.assertIn("recog_stats", kinds)
        self.assertLess(kinds.index("grab_stats"), kinds.index("recog_stats"))  # 紧挨着后面
        data = next(d for k, d in records if k == "recog_stats" and "frame" in d)
        self.assertGreaterEqual(data["frame"]["n"], 1)
        for key in ("priority", "performance", "lowend", "ocr_threads"):
            self.assertIn(key, data)
        self.assertIn(watcher.recog_stats, [d for k, d in records if k == "recog_stats"])

    def test_recog_stats_when_grab_stats_fails(self):
        """取帧统计出错（take_period 抛异常）时，grab_stats 不记，recog_stats 照样记。"""
        source = FakeSource()
        source.take_period = mock.Mock(side_effect=OSError("统计坏了"))
        records = []
        self.session.log_state = lambda kind, **data: records.append((kind, data))
        with mock.patch("advisor.ui.overlay_parts.watcher.log_error"), \
                mock.patch.object(Watcher, "STATS_S", 0.0):
            self.run_watcher(source, lambda: any(k == "recog_stats" for k, _ in records))
        kinds = [k for k, _ in records]
        self.assertNotIn("grab_stats", kinds)
        self.assertIn("recog_stats", kinds)

    def test_recog_stats_when_extras_fail(self):
        """读优先级等那几项出错：只记错误记录，recog_stats 照样记（少了那几项），不往外抛、不影响识别。"""
        from advisor import sysinfo

        records, logged = [], []
        self.session.log_state = lambda kind, **data: records.append((kind, data))
        watcher = Watcher(self.db, self.session, threading.Lock(), manual=True)
        with mock.patch.object(sysinfo, "priority_class", side_effect=OSError("读不到")), \
                mock.patch.object(watcher_mod, "log_error", side_effect=lambda *a, **k: logged.append(a[0])):
            watcher._log_recog_stats()
        self.assertEqual([k for k, _ in records], ["recog_stats"])
        self.assertIn("period_s", records[0][1])
        self.assertNotIn("priority", records[0][1])
        self.assertEqual(watcher.recog_stats, records[0][1])
        self.assertEqual(logged, ["读取优先级等运行状况出错（拾星助手仍在运行）"])

    def test_recog_stats_after_pause(self):
        """识别从暂停恢复：暂停那一段攒下的识别统计（这里是暂停期间界面线程记的一笔 ui_lag）取走丢掉、不记；这一轮从恢复时
        重新算起：恢复后的第一帧不马上记（离线程开始已过 STATS_S 也不记），满 STATS_S 秒的那一帧才记，这一条从恢复时算起。"""
        from advisor import perfstats

        class NotSelect(SameScreen):
            chip_select_visible = False  # 不是三选一：手动模式按一次热键只取一帧

        source = FakeSource()
        records, done = [], []
        self.session.log_state = lambda kind, **data: records.append((kind, data))

        def stats():
            return [d for k, d in records if k == "recog_stats"]

        with mock.patch("advisor.vision.pipeline.FrameProcessor", NotSelect), \
                mock.patch.object(Watcher, "STATS_S", 0.5):
            watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, source=source)
            watcher.capture_done.connect(lambda: done.append(1))
            watcher.set_paused("focus", True, why="minimized")
            watcher.start()
            try:
                end = time.monotonic() + 3
                while not source.idle_calls and time.monotonic() < end:
                    time.sleep(0.02)  # 等线程进了暂停的那一段（暂停时等待之前先调 close_if_idle）
                perfstats.record("ui_lag", 999.0)  # 暂停期间界面线程记的
                time.sleep(0.8)  # 暂停得比 STATS_S 久
                watcher.set_paused("focus", False)
                watcher.trigger()
                wait_for(done)
                self.assertEqual(stats(), [])  # 恢复后的第一帧：这一轮刚从恢复时算起
                time.sleep(0.6)
                watcher.trigger()
                wait_for(done, 2)
                end = time.monotonic() + 3
                while not stats() and time.monotonic() < end:
                    APP.processEvents()
                    time.sleep(0.02)
            finally:
                watcher.stop()
        self.assertEqual(len(stats()), 1)
        first = stats()[0]
        self.assertNotIn("ui_lag", first)  # 暂停那一段丢掉了
        self.assertEqual(first["frame"]["n"], 1)  # 恢复后的第一帧
        self.assertLess(first["period_s"], 1.3)  # 从恢复时算起（不丢的话含暂停的 0.8 秒，约 1.4 秒以上）

    def test_idle_wait_limit(self):
        """长时间等待（暂停、手动模式平时）之前先请取帧对象关掉空闲满 30 秒的会话（close_if_idle），按它给的秒数设等待
        上限（不到 50 毫秒按 50 毫秒）；没有开着的会话（None）、还没有取帧对象时才一直等。取帧对象出错时记进错误记录，
        照常一直等。"""
        watcher = Watcher(self.db, self.session, threading.Lock(), manual=True)
        self.assertIsNone(watcher._idle_wait())  # 还没有取帧对象
        source = FakeSource(idle_waits=[12.5, 0.0, None])
        watcher._active = source
        self.assertEqual(watcher._idle_wait(), 12500.0)
        self.assertEqual(watcher._idle_wait(), 50.0)
        self.assertIsNone(watcher._idle_wait())
        self.assertEqual(source.idle_calls, 3)
        logged = []
        source.close_if_idle = mock.Mock(side_effect=OSError("关不掉"))
        with mock.patch("advisor.ui.overlay_parts.watcher.log_error", side_effect=lambda *a, **k: logged.append(a[0])):
            self.assertIsNone(watcher._idle_wait())
        self.assertEqual(logged, ["关闭空闲的截取会话出错（拾星助手仍在运行）"])

    def test_idle_session_closed_while_waiting(self):
        """手动模式平时、暂停时不取帧：后台线程等待前调 close_if_idle，会话开着时按它给的上限醒来再查一次（空闲满 30 秒
        时取帧对象关掉会话；Windows 10 上黄框随之消失），返回 None（没有开着的会话）之后一直等、不再查。全程不取帧。"""
        for paused in (False, True):
            with self.subTest(paused=paused):
                source = FakeSource(idle_waits=[0.05, 0.05])
                watcher = Watcher(self.db, self.session, threading.Lock(), manual=True, source=source)
                if paused:
                    watcher.set_paused("focus", True, why="minimized")
                watcher.start()
                try:
                    end = time.monotonic() + 3
                    while source.idle_calls < 3 and time.monotonic() < end:
                        time.sleep(0.02)
                    time.sleep(0.3)  # 返回 None 之后一直等：不再查
                    calls = source.idle_calls
                finally:
                    watcher.stop()
                self.assertEqual(calls, 3)
                self.assertEqual(source.grabs, 0)
                self.assertEqual(source.closed, 1)  # 线程结束时关掉取帧对象


class WatcherPauseTest(unittest.TestCase):
    def test_paused_does_not_grab(self):
        db = load_db()
        session = OverlaySession(db, UiSettings(hero="凛"))
        source = FakeSource()
        done = []
        watcher = Watcher(db, session, threading.Lock(), manual=True, source=source)
        watcher.capture_done.connect(lambda: done.append(1))
        watcher.set_paused("focus", True, why="minimized")
        watcher.start()
        try:
            watcher.trigger()
            end = time.monotonic() + 2
            while not done and time.monotonic() < end:
                APP.processEvents()
                time.sleep(0.02)
        finally:
            watcher.stop()
        self.assertTrue(done)  # 按热键时也收起「正在识别…」
        self.assertEqual(source.grabs, 0)
        self.assertEqual(source.closed, 1)  # 线程结束时关掉取帧对象
        self.assertTrue(watcher.paused)


class RecogErrorTextTest(unittest.TestCase):
    """识别出错的提示：界面上只发固定的中文（不带报错原文），报错原文只进错误记录。每一项各自去重：这次运行
    只提示一次，距离这一项上一次出错（不是上一次显示）满 RECOG_REPEAT_S（10 分钟）以后又出错时再提示；同一帧几项一起出错时
    合成一句（具体项用「、」连起来；没有界面叫法的几项合成笼统那一句，有到期的具体项时只发具体项那一句）。时钟换成假的
    （watcher.clock），不起线程：直接走出错时的那几步。截取出错见 WatcherCaptureTest，整帧和子识别出错在识别线程里的样子见
    tests/test_watcher_pace.py。"""

    def setUp(self):
        db = load_db()
        self.now = [0.0]
        patcher = mock.patch.object(watcher_mod, "clock", lambda: self.now[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.watcher = Watcher(db, OverlaySession(db, UiSettings(hero="凛")), threading.Lock(), manual=True)
        self.sent: list[str] = []
        self.watcher.recog_error.connect(self.sent.append, type=Qt.DirectConnection)

    def error_at(self, seconds: float, text: str = RECOG_FRAME) -> None:
        """截取出错、整帧识别出错、识别组件启动失败（一种错一句）。"""
        self.now[0] = seconds
        self.watcher._emit_recog(text)

    def frame_at(self, seconds: float, *names: str) -> None:
        """一帧里这几项子识别出错（FrameProcessor.errors 里的名字，照先后）。"""
        self.now[0] = seconds
        self.watcher._emit_recog_items(list(names))

    def test_texts(self):
        """四种写法逐字核对；同一帧几项用「、」连成一句；「哪一项」的叫法（loading 等内部步骤不在里面，合成笼统那一句）。"""
        tail = "多次出现请点击「设置」窗口中的「检查更新」按钮，更新到最新版拾星助手。"
        self.assertEqual(RECOG_ITEM, "{item}这次没读出来，已自动重试；" + tail)
        self.assertEqual(RECOG_FRAME, "识别出错，已自动重试；" + tail)
        self.assertEqual(RECOG_CAPTURE, "截取游戏画面出错，已自动重试；" + tail)
        self.assertEqual(RECOG_STARTUP, "识别组件启动失败，正在重试；" + tail)
        self.assertEqual(recog_items_text(["星数"]), "星数这次没读出来，已自动重试；" + tail)
        self.assertEqual(recog_items_text(["进度条", "星数"]), "进度条、星数这次没读出来，已自动重试；" + tail)
        self.assertEqual(RECOG_ITEMS, {"hud": "轮次", "progress": "进度条", "hero": "角色", "stars": "星数",
                                       "teammates": "队友", "panel": "「查看筹码」面板", "task_panel": "任务栏",
                                       "select": "候选筹码"})

    def test_repeat_counts_from_last_error(self):
        self.error_at(0)  # 第一次：发
        self.error_at(300)  # 10 分钟里又出：不发
        self.error_at(650)  # 距第一次发已过 10 分钟，距上一次出错（300 秒时）还不到：不发
        self.error_at(650, RECOG_CAPTURE)  # 另一句：各算各的
        self.error_at(1251)  # 距上一次出错（650 秒时）满 10 分钟：再发一次
        self.error_at(1300)
        self.assertEqual(self.sent, [RECOG_FRAME, RECOG_CAPTURE, RECOG_FRAME])
        self.assertEqual(RECOG_REPEAT_S, 600)

    def test_items_in_one_frame(self):
        """同一帧两项一起出错：合成一句，照先后用「、」连起来（同一项在一帧里出两次只写一次）。"""
        self.frame_at(0, "progress", "stars", "progress")
        self.assertEqual(self.sent, [recog_items_text(["进度条", "星数"])])

    def test_items_due_separately(self):
        """每一项各算各的：一项到期、一项不到期时只写到期的那一项；都不到期就不发。"""
        self.frame_at(0, "stars")
        self.frame_at(100, "stars", "progress")  # 星数 10 分钟里出过：只写进度条
        self.frame_at(300, "progress", "stars")  # 都不到期：不发
        self.frame_at(950, "stars", "progress")  # 都距上一次出错（300 秒时）满 10 分钟：两项一起
        self.assertEqual(self.sent, [recog_items_text(["星数"]), recog_items_text(["进度条"]),
                                     recog_items_text(["星数", "进度条"])])

    def test_generic_item_only(self):
        """只有没有界面叫法的几项出错：合成一个笼统项，发笼统那一句；它和整帧识别出错算同一项，同样按 10 分钟算。"""
        self.frame_at(0, "window", "loading")  # loading（对局外整幅画面的文字识别）也没有界面叫法
        self.frame_at(10, "trim")  # 笼统项 10 分钟里出过：不发
        self.error_at(20)  # 整帧识别出错和笼统项同一项：不发
        self.frame_at(30, "stars", "window")  # 星数到期、笼统项不到期：只写星数
        self.frame_at(640, "window")  # 笼统项距上一次出错（30 秒时）满 10 分钟：再发笼统那一句
        self.assertEqual(self.sent, [RECOG_FRAME, recog_items_text(["星数"]), RECOG_FRAME])

    def test_generic_waits_for_specific(self):
        """具体项和笼统项同一帧都到期：只发具体项那一句，笼统项这一回不算显示过；下一帧只有笼统项到期，再发笼统那一句。"""
        self.frame_at(0, "stars", "window")
        self.frame_at(2, "stars", "window")  # 星数不到期；笼统项从来没显示过
        self.frame_at(4, "stars", "window")  # 都不到期
        self.assertEqual(self.sent, [recog_items_text(["星数"]), RECOG_FRAME])

    def test_source_init_error(self):
        """建取帧对象出错（识别组件启动失败）：错误记录照记，提示固定的中文一句、不带报错原文；返回 None，稍后再试。
        窗口截取组件的线程初始化换成假的，不碰系统组件。"""
        from advisor.ui import screen
        from advisor.vision import window_capture

        logged = []
        raw = "[WinError -2147221008] CoInitialize has not been called"
        with mock.patch.object(window_capture, "init_thread"), mock.patch.object(window_capture, "uninit_thread"), \
                mock.patch.object(screen, "FrameSource", side_effect=OSError(raw)), \
                mock.patch.object(watcher_mod, "log_error", side_effect=lambda *a, **k: logged.append(a)):
            source, _ = self.watcher._open_source()
        self.assertIsNone(source)
        self.assertEqual(self.sent, [RECOG_STARTUP])
        self.assertEqual([entry[0] for entry in logged], ["取帧对象初始化出错（拾星助手仍在运行）"])
        self.assertIn(raw, logged[0][1])  # 原文（调用栈）只进错误记录


if __name__ == "__main__":
    unittest.main()
