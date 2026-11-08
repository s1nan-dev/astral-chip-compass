"""两张悬浮窗的接线（advisor/ui/overlay_parts/lanes.py）：启动时建几张、运行中增减第二张、热键先叫哪张、
截取屏幕时后台那张不识别、设置只有一份、第二张记自己那条推荐日志线（不记解锁记录）、每张记自己那个端的客户端、诊断包
lanes 段、性能测试的暂停和序号。

离屏建悬浮窗（不弹窗），识别线程、热键线程、取帧都换成假的；设置文件写在测试沙箱的临时文件夹里。"""
import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from advisor import diagnostics as diagnostics_mod
from advisor import gamelog, paths
from advisor.data import load_db
from advisor.schemes import Scheme
from advisor.ui import overlay as overlay_mod
from advisor.ui import lang, theme, winapi
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import lanes as lanes_mod
from advisor.ui.overlay_parts.app import BOTH, LANE_KEYS, OverlaySession, STEAM, TAPTAP, UiSettings
from advisor.ui.overlay_parts.background import Background, _in_own_range
from advisor.ui.overlay_parts.follow import WindowFollower
from advisor.ui.overlay_parts.geometry import PLACE_GAP, live_placement, overlay_position
from advisor.ui.overlay_parts.lane_settings import LaneSettings
from advisor.ui.overlay_parts.lanes import Lanes
from advisor.ui.overlay_parts.wording import BLOCKED_HOTKEY
from advisor.ui.services import Services
from advisor.ui.text import tip_paragraphs_html
from advisor.vision import capture
from tests.support import sandbox
from tests.support.errors import capture_errors
from tests.support.fakes import FakeLogbook
from tests.support.overlay_cases import APP, FakeHotkey, FakeWatcher, frame, probe


class FakeScheme(Scheme):
    pass


class FakeStore:
    """只够 _use_scheme、_scheme_added、_use_requested 用：按角色记当前方案，按 id 取方案。"""

    def __init__(self):
        self.items = []
        self.saved = {}

    def add(self, scheme):
        self.items.append(scheme)
        return scheme

    def get(self, scheme_id):
        return next((s for s in self.items if s.id == scheme_id), None)

    def current(self, hero):
        return self.saved.get(hero)

    def set_current(self, hero, scheme_id):
        self.saved[hero] = self.get(scheme_id)

    def schemes(self, hero):
        return [s for s in self.items if s.hero == hero]


class FakeDualCheck:
    """假的性能测试：run 里按 gate 等（不给 gate 时马上返回），记下有没有被取消。"""

    instances = []

    result = SimpleNamespace(slowest_s=0.2, median_s=0.1, busy_share=0.1, own_share=0.05, seconds=30.0,
                             lowend=False, stopped=False, reason="", level="easy")

    def __init__(self, db, *, seconds=30.0, lowend=False, **_):
        self.seconds, self.lowend = seconds, lowend
        self.stop = None
        self.gate = getattr(self, "next_gate", None)
        FakeDualCheck.instances.append(self)

    def missing(self):
        return []

    def run(self, progress=None, stop=None):
        self.stop = stop
        if progress is not None:
            progress(1.0, self.seconds)
        gate = self.gate
        if gate is not None:
            gate.wait(5.0)
        return self.result


def fake_recorder(events, scope, stats=(), stats_scope=None):
    """识别记录器的假对象：flush() 什么都不做，collect() 返回给定的识别记录和导出范围，
    collect_stats() 返回给定的统计记录和说明（不给时为空）。"""
    return SimpleNamespace(flush=lambda: None, collect=lambda: (list(events), dict(scope)),
                           collect_stats=lambda hours=12.0: (
                               list(stats), dict(stats_scope or {"hours": hours, "since": None,
                                                                 "segments": 0, "lines": 0})))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "test-lanes-")
        self.path = self.dir / "state.json"
        self.db = load_db()
        self.lock = threading.Lock()
        self.fake_hotkey = FakeHotkey()
        self.watchers = []
        self.windows = []

    def make(self, platform=None, two=False, title="凛", store=None, services=None, hidden=(), tray=None):
        """hidden、tray：这一次启动时收在托盘里的几张、托盘图标建没建出来（tray 不为 None 时照 main() 调 finish_hidden_start）。"""
        shared = UiSettings(hero=title, platform=platform)
        shared.save(self.path)
        session = OverlaySession(self.db, shared, self.path)
        window = Overlay(self.db, session, self.lock, services=services or Services(), store=store)
        window.native = False
        self.windows.append(window)

        def factory(db, session_, lock, manual=False, platform=None, **_):
            watcher = FakeWatcher(platform=platform)
            self.watchers.append(watcher)
            return watcher

        lanes = Lanes(app=APP, primary=window, primary_session=session, lock=self.lock,
                      db=self.db, services=window.services, store=store, hotkey=self.fake_hotkey,
                      state_file=self.path, record=False, watcher_factory=factory,
                      expected_capture=lambda: "window", probe_game=lambda **kw: None, hidden=hidden)
        lanes.wire_primary()
        if two:
            lanes.add_secondary()
            self.windows.append(lanes.secondary.window)
        if tray is not None:
            lanes.finish_hidden_start(tray)
        lanes.start()
        self.addCleanup(self.cleanup, lanes)
        return lanes

    def cleanup(self, lanes):
        try:
            lanes.shutdown()
        except Exception:  # noqa: BLE001
            pass
        theme.set_skin(theme.DEFAULT_KEY)
        APP.processEvents()
        for window in self.windows:
            try:
                window.hide()
                window.deleteLater()
            except RuntimeError:
                pass
        APP.processEvents()

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class HiddenStartTest(Base):
    """软件内更新自动重启前收在托盘里的：这一次启动时照旧收着（Lanes 的 hidden、finish_hidden_start）。"""

    def test_primary(self):
        """第一张收着启动：接线时不显示，托盘图标建好以后照旧收着；点托盘图标照常显示出来。"""
        lanes = self.make(hidden={"primary"}, tray=True)
        window = lanes.primary.window
        self.assertTrue(window.isHidden())
        window.tray_part.show_from_tray()
        self.assertFalse(window.isHidden())

    def test_no_tray(self):
        """托盘图标建不出来：收着的照常显示（不能既没有悬浮窗又没有托盘图标）。"""
        lanes = self.make(platform=BOTH, two=True, hidden={"primary", "secondary"}, tray=False)
        self.assertFalse(lanes.primary.window.isHidden())
        self.assertFalse(lanes.secondary.window.isHidden())

    def test_each_lane(self):
        """两张各按自己收没收着：只收着第二张时第一张照常显示；从托盘显示时第二张跟着出来。"""
        lanes = self.make(platform=BOTH, two=True, hidden={"secondary"}, tray=True)
        self.assertFalse(lanes.primary.window.isHidden())
        self.assertTrue(lanes.secondary.window.isHidden())
        lanes.primary.window.tray_part.show_more = lanes.show_secondary
        lanes.primary.window.tray_part.show_from_tray()
        self.assertFalse(lanes.secondary.window.isHidden())

    def test_only_at_startup(self):
        """只管启动时这一次：之后才加上的第二张照常显示；没有收着的照常显示。"""
        lanes = self.make(hidden={"secondary"}, tray=True)
        self.assertFalse(lanes.primary.window.isHidden())
        lanes.add_secondary()
        self.windows.append(lanes.secondary.window)
        self.assertFalse(lanes.secondary.window.isHidden())


class StartupTest(Base):
    def test_single_lane(self):
        lanes = self.make()
        self.assertIsNone(lanes.secondary)
        self.assertEqual(len(self.watchers), 1)
        self.assertIsNone(self.watchers[0].platform)  # 不是同时识别：按设置找
        self.assertTrue(self.watchers[0].started)
        self.assertEqual(self.fake_hotkey.starts, 1)

    def test_both_creates_two_with_own_state(self):
        lanes = self.make(platform=BOTH, two=True)
        self.assertIsNotNone(lanes.secondary)
        self.assertEqual(len(self.watchers), 2)
        self.assertEqual(self.watchers[0].platform, STEAM)
        self.assertEqual(self.watchers[1].platform, TAPTAP)
        second = lanes.secondary
        self.assertIsInstance(second.session.settings, LaneSettings)
        self.assertEqual(second.window.platform, TAPTAP)
        self.assertIsNone(second.window.services.logbook)  # 这里传的 Services() 没有推荐日志模块，第二张不记
        self.assertFalse(second.window.records.track)  # 第二张不碰解锁记录
        self.assertTrue(second.window.notice_model.lane_only)
        self.assertIs(second.window.dialogs, lanes.primary.window.dialogs)
        self.assertIs(second.window.updates, lanes.primary.window.updates)
        # 本局状态各一份
        lanes.primary.session.settings.owned.append("书签")
        self.assertEqual(second.session.settings.owned, [])

    def test_ocr_check_sees_every_lane(self):
        """文字识别线程数的自检看第一张的识别线程和前台状态，对局看现在各张的 Session：第二张收掉以后只看第一张。"""
        lanes = self.make(platform=BOTH, two=True)
        window = lanes.primary.window
        check = window.ocr_check
        self.assertIs(check.watcher(), lanes.primary.watcher)
        self.assertEqual(check.activity(), window.follower.activity)
        self.assertEqual(check.sessions(), [lanes.primary.session, lanes.secondary.session])
        lanes.remove_secondary()
        self.assertEqual(check.sessions(), [lanes.primary.session])

    def test_lanes_share_select_board(self):
        """两张的识别线程共用一块「谁的三选一正显示着」的牌子（线程开始前设上，识别流程建好时交给它）；运行中加上的第二张也是
        同一块。"""
        from advisor.vision.pipeline import SelectBoard

        lanes = self.make(platform=BOTH, two=True)
        self.assertIsInstance(lanes.select_board, SelectBoard)
        self.assertEqual([w.select_board for w in self.watchers], [lanes.select_board] * 2)

    def test_runtime_secondary_shares_select_board(self):
        lanes = self.make()
        lanes.set_platform(BOTH)
        self.assertEqual(len(self.watchers), 2)
        self.assertEqual([w.select_board for w in self.watchers], [lanes.select_board] * 2)

    def test_startup_both_primary_follows_steam(self):
        lanes = self.make(platform=BOTH, two=True)
        self.assertEqual(lanes.primary.watcher.platform, STEAM)
        self.assertEqual(lanes.primary.platform, STEAM)
        self.assertEqual(lanes.primary.window._platform, BOTH)

    def test_startup_starts_tracking_once_per_lane(self):
        """启动时就是同时识别：定时查看游戏窗口由 start() 统一开始，第二张不多起一个计时器。"""
        with mock.patch.object(WindowFollower, "start_tracking", autospec=True) as tracking:
            self.make(platform=BOTH, two=True)
        self.assertEqual(tracking.call_count, 2)

    def test_runtime_secondary_starts_tracking_once(self):
        with mock.patch.object(WindowFollower, "start_tracking", autospec=True) as tracking:
            lanes = self.make()
            lanes.set_platform(BOTH)
        self.assertEqual(tracking.call_count, 2)  # 第一张一次、第二张一次


class FakeLaneLogbookLine:
    """假的一条推荐日志线（照 logbook.Lane）：记下收到的调用；故意没有校准计数那几个函数。"""

    def __init__(self, name):
        self.name = name
        self.offers, self.picks, self.feedback = [], [], []
        self.game = None

    def record_offer(self, state, advice, offer_id, client=None):
        self.offers.append((offer_id, tuple(e.chip.name for e in advice.evals)))
        return self.game

    def record_pick(self, offer_id, chosen, via=None):
        self.picks.append((offer_id, chosen))

    def record_feedback(self, offer_id, reason, note):
        self.feedback.append((offer_id, reason, note))

    def record_game_over(self, **info):
        return self.game

    def current_game(self):
        return self.game

    def new_game(self):
        return self.game

    def undo_new_game(self, previous):
        pass

    def resumable(self):
        return self.game

    def records_between(self, start, end=None):
        return []


class FakeLaneLogbook:
    """假的推荐日志模块：lane(name) 返回按名字缓存的假线；模块级的调用记下第一张的。"""

    def __init__(self):
        self._lanes = {}
        self.offers, self.picks, self.feedback = [], [], []

    def lane(self, name):
        line = self._lanes.get(name)
        if line is None:
            line = self._lanes[name] = FakeLaneLogbookLine(name)
        return line

    def record_offer(self, state, advice, offer_id, client=None):
        self.offers.append((offer_id, tuple(e.chip.name for e in advice.evals)))

    def record_pick(self, offer_id, chosen, via=None):
        self.picks.append((offer_id, chosen))

    def record_feedback(self, offer_id, reason, note):
        self.feedback.append((offer_id, reason, note))


class LaneLogbookTest(Base):
    """第二张悬浮窗接 TapTap 那条推荐日志线：不接第一张的模块本身，取不到线时不记也不问。"""

    def test_second_uses_taptap_line(self):
        logbook = FakeLaneLogbook()
        diagnostics, assets, version = object(), object(), object()
        lanes = self.make(two=True, services=Services(logbook=logbook, diagnostics=diagnostics,
                                                      assets=assets, version=version))
        line = logbook.lane(TAPTAP)
        self.assertIs(lanes.secondary.window.services.logbook, line)
        self.assertIs(lanes.primary.window.services.logbook, logbook)
        for name, shared in (("diagnostics", diagnostics), ("assets", assets), ("version", version)):
            self.assertIs(getattr(lanes.secondary.window.services, name), shared)
        # 第二张拿不到校准计数：「可以校准」的提示只在第一张上出
        self.assertIsNone(lanes.secondary.window.services.call(
            "logbook", "game_count_since_calibration", default=None))

    def test_no_lane_method_gives_none(self):
        lanes = self.make(two=True, services=Services(logbook=SimpleNamespace()))
        self.assertIsNone(lanes.secondary.window.services.logbook)

    def test_lane_error_logged_and_none(self):
        errors = capture_errors(self)

        class BadLogbook:
            def lane(self, name):
                raise RuntimeError("取不到")

        lanes = self.make(two=True, services=Services(logbook=BadLogbook()))
        self.assertIsNone(lanes.secondary.window.services.logbook)
        titles = [title for title, _ in errors.entries()]
        self.assertTrue(any("推荐日志取不到" in title for title in titles), titles)
        errors.clear()

    def test_second_offer_and_feedback_go_to_line(self):
        logbook = FakeLaneLogbook()
        lanes = self.make(two=True, services=Services(logbook=logbook))
        second = lanes.secondary.window
        session = lanes.secondary.session
        session.game.seen_hero = "凛"
        offer = ["标靶", "大背包", "缓冲盾牌"]
        second.handle(session.on_frame(frame(offer)))
        line = logbook.lane(TAPTAP)
        self.assertEqual(len(line.offers), 1)
        self.assertEqual(logbook.offers, [])  # 第一张的模块没收到
        best = second.advice.last_ev.advice.best
        other = next(n for n in offer if n != best)
        session.on_frame(frame(offer, selected=offer.index(other)))
        session.on_frame(None)
        second.handle(session.on_frame(None))
        self.assertEqual(line.picks[-1][1], other)
        self.assertEqual(second.advice.question_state(), "ask")
        second.set_expanded(True)
        second.reason_buttons[0].click()
        self.assertEqual(len(line.feedback), 1)
        self.assertEqual(logbook.feedback, [])

    def test_dismiss_per_window(self):
        """两张都在问未选择首推的原因：第二张点「×」只收起第二张的这一问，第一张照旧问、「×」照旧在；第一张点原因记在
        第一张的模块上，第二张那条线上没有反馈。"""
        logbook = FakeLaneLogbook()
        lanes = self.make(two=True, services=Services(logbook=logbook))
        offer = ["标靶", "大背包", "缓冲盾牌"]
        for part in (lanes.primary, lanes.secondary):
            window, session = part.window, part.session
            session.game.seen_hero = "凛"
            window.handle(session.on_frame(frame(offer)))
            other = next(n for n in offer if n != window.advice.last_ev.advice.best)
            session.on_frame(frame(offer, selected=offer.index(other)))
            session.on_frame(None)
            window.handle(session.on_frame(None))
            window.set_expanded(True)
            self.assertEqual(window.advice.question_state(), "ask")
        first, second = lanes.primary.window, lanes.secondary.window
        second.feedback_dismiss.click()
        self.assertIsNone(second.advice.question_state())
        self.assertTrue(second.question.isHidden())
        self.assertEqual(first.advice.question_state(), "ask")
        self.assertFalse(first.question.isHidden())
        self.assertFalse(first.feedback_dismiss.isHidden())
        first.reason_buttons[-1].click()
        self.assertEqual([reason for _, reason, _ in logbook.feedback], ["想试别的玩法"])
        self.assertEqual(logbook.lane(TAPTAP).feedback, [])


class ClientTest(Base):
    """推荐日志 offer 里记的 client（对局报告用）：每张按自己跟的端取上一次找到的窗口标题。两开时两路识别先后覆盖「上一次
    找到的」，第一张取 Steam 那一路的、第二张取 TapTap 那一路的，谁最后找过窗口都不串；只开一个客户端时用上一次找到的。"""

    def test_each_lane_own_client(self):
        lanes = self.make(platform=BOTH, two=True)
        titles = {STEAM: "AstralParty_INT", TAPTAP: "吉星派对"}
        for last in ((TAPTAP, "吉星派对"), (STEAM, "AstralParty_INT")):
            with self.subTest(last=last), mock.patch.object(capture, "_found_titles", titles), \
                    mock.patch.object(capture, "_found", last):
                self.assertEqual(lanes.primary.window.offers._client_kwargs(), {"client": "int"})
                self.assertEqual(lanes.secondary.window.offers._client_kwargs(), {"client": "taptap"})

    def test_single_client_uses_last_found(self):
        lanes = self.make()
        with mock.patch.object(capture, "_found_titles", {STEAM: "AstralParty_CN"}), \
                mock.patch.object(capture, "_found", (TAPTAP, "吉星派对")):
            self.assertEqual(lanes.primary.window.offers._client_kwargs(), {"client": "taptap"})
        with mock.patch.object(capture, "_found_titles", {}), mock.patch.object(capture, "_found", None):
            self.assertEqual(lanes.primary.window.offers._client_kwargs(), {})


class SwitchTest(Base):
    def test_add_and_remove_secondary(self):
        lanes = self.make()
        primary = lanes.primary.window
        lanes.set_platform(BOTH)
        self.assertIs(lanes.primary.window, primary)  # 第一张不重建
        self.assertIsNotNone(lanes.secondary)
        self.assertEqual(self.watchers[0].platform, STEAM)
        second = lanes.secondary
        lanes.set_platform(None)
        self.assertIsNone(lanes.secondary)
        self.assertTrue(second.watcher.stopped)
        self.assertEqual(self.watchers[0].platform, None)
        self.assertIs(lanes.primary.window, primary)

    def test_remove_keeps_lane_settings(self):
        lanes = self.make(platform=BOTH, two=True)
        lanes.secondary.session.set_hero("芬妮")
        hero = lanes.secondary.session.settings.hero
        lanes.remove_secondary()
        self.assertEqual(self.saved()["lanes"][TAPTAP]["hero"], hero)


class HotkeyTest(Base):
    def test_front_lane_first_then_other(self):
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "background"
        second.window.follower.activity = "game"
        lanes.on_hotkey()
        self.assertEqual(second.watcher.requests, 1)
        self.assertEqual(primary.watcher.requests, 0)
        self.assertTrue(second.window.hotkeys.busy)
        second.watcher.capture_done.emit()
        self.assertEqual(primary.watcher.requests, 1)
        self.assertFalse(primary.window.hotkeys.busy)

    def test_screen_behind_lane_not_recognized(self):
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="screen")
        lanes.on_hotkey()
        self.assertEqual(second.watcher.requests, 0)
        self.assertEqual(second.window.message.text, lanes_mod.SCREEN_BEHIND)
        self.assertEqual(primary.watcher.requests, 1)

    def test_direct_capture_background_lane_runs(self):
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="window")
        lanes.on_hotkey()
        self.assertEqual(second.watcher.requests, 0)
        primary.watcher.capture_done.emit()
        self.assertEqual(second.watcher.requests, 1)

    def test_chosen_did_not_start_triggers_other(self):
        """先叫的那张没开始识别（busy 为空）：不等 capture_done，另一张直接叫。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "background"
        second.window.follower.activity = "background"
        lanes.on_hotkey()
        self.assertFalse(primary.window.hotkeys.busy)
        self.assertEqual(second.watcher.requests, 1)

    def test_wait_timeout_still_triggers_other(self):
        """等 capture_done 的保险：先叫的那张一直不回声（识别线程中途停了等），到点照样叫另一张。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="window")
        with mock.patch.object(lanes_mod, "PENDING_WAIT_S", 0.02):
            lanes.on_hotkey()
            self.assertEqual(second.watcher.requests, 0)
            deadline = time.monotonic() + 2.0
            while not second.watcher.requests and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.01)
        self.assertEqual(second.watcher.requests, 1)

    def test_timeout_callback_runs_in_ui_thread(self):
        """5 秒保险的到点回调在界面线程里（Qt 的计时器挂在先叫的那张悬浮窗上）。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="window")
        seen = []
        original = lanes._fire_pending

        def fire():
            seen.append(threading.get_ident())
            original()

        lanes._fire_pending = fire
        with mock.patch.object(lanes_mod, "PENDING_WAIT_S", 0.02):
            lanes.on_hotkey()
            deadline = time.monotonic() + 2.0
            while not seen and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.01)
        self.assertEqual(seen, [threading.get_ident()])

    def test_stale_timeout_from_earlier_press_ignored(self):
        """按了两次：第一次识别完、叫过另一张以后又按一次，第一次的保险到点时不再叫另一张（序号已经换了），等第二次识别完。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="window")
        with mock.patch.object(lanes_mod, "PENDING_WAIT_S", 0.02):
            lanes.on_hotkey()
        primary.watcher.capture_done.emit()
        self.assertEqual(second.watcher.requests, 1)
        with mock.patch.object(lanes_mod, "PENDING_WAIT_S", 30.0):
            lanes.on_hotkey()  # 第一张还在识别这一次
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        self.assertEqual(second.watcher.requests, 1)
        primary.watcher.capture_done.emit()
        self.assertEqual(second.watcher.requests, 2)

    def test_capturing_on_primary_skips_other(self):
        """第一张正等用户按新热键时按下了热键：游戏在前台的是第二张，只叫它，它识别完也不叫第一张（看的是第一张的状态）。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.follower.activity = "background"
        second.window.follower.activity = "game"
        primary.window.hotkeys.capturing = True
        lanes.on_hotkey()
        self.assertEqual(second.watcher.requests, 1)
        second.watcher.capture_done.emit()
        self.assertEqual(primary.watcher.requests, 0)

    def test_gate_registers_when_any_lane_wants(self):
        lanes = self.make(two=True)
        first = lanes.primary.window.hotkey_thread
        second = lanes.secondary.window.hotkey_thread
        self.assertIsInstance(first, lanes_mod.LaneHotkey)
        first.set_enabled(True)
        self.assertEqual(self.fake_hotkey.calls[-1], True)
        second.set_enabled(False)
        self.assertEqual(self.fake_hotkey.calls[-1], True)
        first.set_enabled(False)
        self.assertEqual(self.fake_hotkey.calls[-1], False)

    def test_switch_keeps_registered_when_primary_wants(self):
        """第一张游戏在前台、热键已登记：加上第二张以后（它第一次报不登记）热键仍登记；收回一张时按第一张的计划。"""
        lanes = self.make()
        lanes.primary.window.follower.activity = "game"  # 第一张的游戏在前台（刷新时会按它重算计划）
        lanes.primary.window.follower.update_plan()
        lanes.set_platform(BOTH)
        self.assertEqual(self.fake_hotkey.calls[-1], True)
        lanes.set_platform(None)
        self.assertEqual(self.fake_hotkey.calls[-1], True)


class TrayTest(Base):
    def test_second_close_hides_only_itself(self):
        lanes = self.make(two=True)
        second, primary = lanes.secondary, lanes.primary
        second.window.show()
        primary.window.show()
        second.window.tray_part.close_clicked()
        self.assertTrue(second.window.isHidden())
        self.assertFalse(primary.window.isHidden())

    def test_tray_show_shows_second(self):
        lanes = self.make(two=True)
        with mock.patch.object(lanes.secondary.window.tray_part, "show_from_tray") as show:
            lanes.primary.window.tray_part.show_more = lanes.show_secondary
            lanes.primary.window.tray_part.show_more()
        show.assert_called_once_with()

    def test_second_close_tip(self):
        lanes = self.make(two=True)
        from advisor.ui.overlay_parts.follow import LANE_CLOSE_TIP
        self.assertEqual(lanes.secondary.window.close_btn.toolTip(), tip_paragraphs_html(LANE_CLOSE_TIP))

    def test_second_close_tip_wraps_long_text(self):
        """第二张 × 的悬停说明照别的悬停说明分行，不排成很宽的一行：换成一句会分行的长句核对。"""
        from advisor.ui.overlay_parts.follow import LANE_CLOSE_TIP
        long_tip = LANE_CLOSE_TIP * 2
        self.assertNotEqual(tip_paragraphs_html(long_tip), long_tip)  # 这么长会分行
        with mock.patch.object(overlay_mod, "LANE_CLOSE_TIP", long_tip):
            lanes = self.make(two=True)
        self.assertEqual(lanes.secondary.window.close_btn.toolTip(), tip_paragraphs_html(long_tip))

    def test_second_buttons_open_primary_windows(self):
        """第二张的「设置」「导入/导出」「编辑方案」接到第一张那一份窗口（第二张自己那份还是空的）。"""
        lanes = self.make(two=True)
        second, primary = lanes.secondary, lanes.primary
        own = second.window._own_dialogs
        self.assertIsNot(own, primary.window.dialogs)
        with mock.patch.object(primary.window.dialogs, "show_settings") as settings, \
                mock.patch.object(primary.window.dialogs, "open_share") as share, \
                mock.patch.object(primary.window.dialogs, "open_editor") as editor:
            second.window.settings_btn.click()
            second.window.share_btn.click()
            second.window.edit_btn.click()
        settings.assert_called_once_with()
        share.assert_called_once_with()
        editor.assert_called_once_with(hero=second.session.settings.hero)
        self.assertIsNone(own.settings)
        self.assertIsNone(own.share)
        self.assertIsNone(own.editor)

    def test_second_edit_button_uses_second_hero(self):
        """第二张点「编辑方案」：建在第一张那份 Dialogs 上，角色是第二张当前的角色。"""
        store = FakeStore()
        lanes = self.make(two=True, store=store)
        second = lanes.secondary
        second.session.set_hero("芬妮")
        editor_class = mock.Mock()
        primary_dialogs = lanes.primary.window.dialogs
        primary_dialogs.editor_class = editor_class
        primary_dialogs.editor = None
        second.window.edit_btn.click()
        self.assertEqual(editor_class.call_args.args, (self.db, store, "芬妮"))


class SettingsTest(Base):
    def test_second_forwards_shared_settings(self):
        lanes = self.make(platform=BOTH, two=True)
        second, primary = lanes.secondary, lanes.primary
        second.window.set_manual(True)
        second.window.set_lowend(True)
        second.window.set_opaque(True)
        self.assertTrue(primary.window.manual)
        self.assertEqual(second.window._lowend, True)
        self.assertTrue(self.saved()["manual"])
        self.assertTrue(self.saved()["lowend"])
        self.assertTrue(self.saved()["opaque"])
        second.window.set_platform(TAPTAP)
        self.assertEqual(self.saved()["platform"], TAPTAP)
        self.assertEqual(self.saved()["manual"], True)  # 转错了会悄悄丢掉

    def test_second_forwards_ui_lang_and_restart(self):
        """第二张选界面语言、点「现在重开」：都转给第一张。界面语言存进共用设置（第二张存盘只写自己的四项，自己存会悄悄
        丢掉），第二张的设置跟着抄过去；重开只起一次新进程，由第一张退出。"""
        lanes = self.make(platform=BOTH, two=True)
        second, primary = lanes.secondary, lanes.primary
        second.window.set_ui_lang(lang.HANT)
        self.assertEqual(self.saved()["ui_lang"], lang.HANT)
        self.assertEqual(primary.session.settings.ui_lang, lang.HANT)
        self.assertEqual(second.session.settings.ui_lang, lang.HANT)
        with mock.patch.object(overlay_mod.relaunch, "start", return_value=True) as start, \
                mock.patch.object(primary.window, "_quit_app") as quit_primary, \
                mock.patch.object(second.window, "_quit_app") as quit_second:
            second.window.restart_app()
        start.assert_called_once()
        quit_primary.assert_called_once_with()
        quit_second.assert_not_called()

    def test_second_does_not_write_platform_seen_or_skin(self):
        lanes = self.make(platform=BOTH, two=True)
        second, primary = lanes.secondary, lanes.primary
        seen = self.saved().get("platform_seen")
        with mock.patch("advisor.vision.capture.found_platform", return_value=TAPTAP):
            second.window._notice_platform_seen()
        self.assertEqual(self.saved().get("platform_seen"), seen)
        keys = [s.key for s in theme.skins()]
        other = next((k for k in keys if k != theme.current_key()), None)
        if other is not None:
            second.window.set_skin(other)
            self.assertEqual(self.saved()["skin"], other)
            self.assertEqual(primary.session.settings.skin, other)

    def test_sync_applies_to_second(self):
        lanes = self.make(two=True)
        lanes.primary.window.set_lowend(True)
        self.assertTrue(lanes.secondary.window._lowend)
        self.assertTrue(lanes.secondary.watcher.lowend)
        lanes.primary.window.set_manual(True)
        self.assertTrue(lanes.secondary.window._manual)
        self.assertTrue(lanes.secondary.watcher.manual)

    def test_manual_switch_rewrites_second_waiting_text(self):
        lanes = self.make(two=True)
        second = lanes.secondary.window
        second.message.say(second._waiting_text(), "advice")  # 第二张正显示着等待提示
        lanes.primary.window.set_manual(True)
        self.assertEqual(second.message.text, second._waiting_text())
        self.assertIn("F8", second.message.text)  # 手动模式的等待提示写明按哪个键

    def test_hotkey_state_follows_primary(self):
        lanes = self.make(two=True)
        second = lanes.secondary.window.hotkeys
        lanes.secondary.window.message.say(lanes.secondary.window._waiting_text(), "advice")
        lanes.primary.window.set_manual(True)
        lanes.primary.window.on_hotkey_changed("Ctrl+F9")
        self.assertEqual((second.hotkey, second.hotkey_ok, second.hotkey_error), ("Ctrl+F9", True, ""))
        self.assertIn("Ctrl+F9", lanes.secondary.window.message.text)  # 第二张手动模式的等待提示
        lanes.primary.window.on_hotkey_failed("Ctrl+F9 被其他程序占用")
        self.assertEqual(second.hotkey_ok, False)
        self.assertEqual(second.hotkey_error, "Ctrl+F9 被其他程序占用")
        lanes.primary.window.on_hotkey_restored("Ctrl+F9")
        self.assertEqual((second.hotkey_ok, second.hotkey_error), (True, ""))

    def test_hotkey_state_copied_when_added(self):
        """运行中才加上的第二张：建好时照第一张当时的热键、能不能用、不能用的原因抄一遍（这里热键已经登记不上）。"""
        lanes = self.make()
        lanes.primary.window.on_hotkey_failed("F8 被其他程序占用")
        lanes.set_platform(BOTH)
        first, second = lanes.primary.window.hotkeys, lanes.secondary.window.hotkeys
        self.assertEqual((second.hotkey, second.hotkey_ok, second.hotkey_error),
                         (first.hotkey, False, "F8 被其他程序占用"))

    def test_second_shows_blocked_like_primary(self):
        """第一张下载识别图片、挡着识别时：第二张的状态胶囊也是「识别 暂不可用」；第二张的游戏在前台时按热键，第二张上
        说明下载期间不识别，两张都不识别。"""
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.background._set_blocked(True)
        second.window.refresh()
        self.assertIn("blocked", [bit.key for bit in second.window.status.shown_bits])
        primary.window.follower.activity = "background"
        second.window.follower.activity = "game"
        lanes.on_hotkey()
        self.assertEqual(second.window.message.text, BLOCKED_HOTKEY)
        self.assertEqual((primary.watcher.requests, second.watcher.requests), (0, 0))

    def test_download_blocks_and_reloads_both(self):
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.background._set_blocked(True)
        self.assertTrue(primary.watcher.paused.get("assets"))
        self.assertTrue(second.watcher.paused.get("assets"))
        self.assertTrue(second.window._assets_blocked())  # 第二张看第一张的
        primary.window.background.download_finished([], [])
        self.assertFalse(primary.watcher.paused.get("assets"))
        self.assertFalse(second.watcher.paused.get("assets"))
        self.assertGreaterEqual(primary.watcher.reloaded, 1)
        self.assertGreaterEqual(second.watcher.reloaded, 1)

    def test_download_blocked_hotkey_does_not_call_other(self):
        lanes = self.make(two=True)
        primary, second = lanes.primary, lanes.secondary
        primary.window.background._set_blocked(True)
        primary.window.follower.activity = "game"
        second.window.follower.activity = "background"
        second.window._capture = SimpleNamespace(method="window")
        lanes.on_hotkey()
        self.assertEqual(second.watcher.requests, 0)

    def test_secondary_added_while_blocked_starts_paused(self):
        lanes = self.make()
        lanes.primary.window.background._set_blocked(True)
        lanes.set_platform(BOTH)
        self.assertTrue(lanes.secondary.watcher.paused.get("assets"))


class SaveTest(Base):
    def test_save_secondary_gives_up_when_lock_held(self):
        lanes = self.make(two=True)
        second = lanes.secondary
        self.lock.acquire()
        self.addCleanup(self.lock.release)
        errors = capture_errors(self)
        with mock.patch.object(lanes_mod, "EXIT_SAVE_WAIT_S", 0.02), \
                mock.patch.object(second.session, "save") as save:
            started = time.monotonic()
            lanes.save_secondary()
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        save.assert_not_called()
        self.assertEqual([title for title, _ in errors.entries()], ["退出时没有再存一次设置"])

    def test_remove_secondary_save_is_bounded(self):
        lanes = self.make(two=True)
        second = lanes.secondary
        self.lock.acquire()
        self.addCleanup(self.lock.release)
        errors = capture_errors(self)
        with mock.patch.object(lanes_mod, "EXIT_SAVE_WAIT_S", 0.02), \
                mock.patch.object(second.session, "save") as save:
            started = time.monotonic()
            lanes.remove_secondary()
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        save.assert_not_called()
        self.assertEqual([title for title, _ in errors.entries()], ["换回一张悬浮窗时没有存上第二张的设置"])


class SchemesTest(Base):
    def _store(self):
        store = FakeStore()
        hero = "凛"
        first = store.add(FakeScheme(hero=hero, name="方案一", id="s1"))
        other = store.add(FakeScheme(hero=hero, name="方案二", id="s2"))
        return store, first, other

    @staticmethod
    def _recompute(flash="", change=None, quiet=""):
        """照真的 _recompute 先做改动（记下新方案），不算推荐。"""
        if change is not None:
            change()
        return False

    def test_scheme_change_recomputes_second(self):
        store, first, _ = self._store()
        lanes = self.make(two=True, store=store)
        with mock.patch.object(lanes.primary.window, "_recompute", side_effect=self._recompute) as primary, \
                mock.patch.object(lanes.secondary.window, "_recompute", side_effect=self._recompute) as second:
            lanes.primary.window._use_scheme("凛", first.id, announce=True, via="menu")
        primary.assert_called_once()
        second.assert_called_once()
        self.assertIs(store.current("凛"), first)

    def test_second_scheme_change_updates_primary(self):
        store, _, second = self._store()
        lanes = self.make(two=True, store=store)
        with mock.patch.object(lanes.primary.window, "_recompute", side_effect=self._recompute) as primary:
            lanes.secondary.window._use_scheme("凛", second.id, announce=True, via="menu")
        primary.assert_called_once()
        self.assertIs(store.current("凛"), second)

    def test_other_hero_not_recomputed(self):
        store, first, _ = self._store()
        lanes = self.make(two=True, store=store)
        lanes.secondary.session.set_hero("芬妮")
        with mock.patch.object(lanes.primary.window, "_recompute", side_effect=self._recompute), \
                mock.patch.object(lanes.secondary.window, "_recompute") as second:
            lanes.primary.window._use_scheme("凛", first.id, announce=True, via="menu")
        second.assert_not_called()

    def test_scheme_added_for_second_hero_used_there(self):
        store = FakeStore()
        scheme = store.add(FakeScheme(hero="芬妮", name="芬妮的方案", id="s2"))
        lanes = self.make(two=True, store=store)
        lanes.secondary.session.set_hero("芬妮")
        with mock.patch.object(lanes.primary.window, "_recompute") as primary, \
                mock.patch.object(lanes.secondary.window, "_recompute",
                                  side_effect=self._recompute) as second:
            lanes.primary.window._scheme_added("芬妮", scheme.id)
        second.assert_called_once()
        primary.assert_not_called()
        self.assertIs(store.current("芬妮"), scheme)

    def test_import_use_for_second_hero_used_there(self):
        store = FakeStore()
        scheme = store.add(FakeScheme(hero="芬妮", name="芬妮的方案", id="s2"))
        lanes = self.make(two=True, store=store)
        lanes.secondary.session.set_hero("芬妮")
        with mock.patch.object(lanes.primary.window, "_recompute") as primary, \
                mock.patch.object(lanes.secondary.window, "_recompute",
                                  side_effect=self._recompute) as second, \
                mock.patch.object(lanes.primary.window.dialogs, "show_use_result") as result:
            lanes.primary.window._use_requested("芬妮", scheme.id)
        second.assert_called_once()
        primary.assert_not_called()
        result.assert_called_once_with(scheme.id, "now")
        self.assertIs(store.current("芬妮"), scheme)


class HubTest(Base):
    def test_second_hub_uses_its_own_hero(self):
        lanes = self.make(two=True)
        lanes.secondary.session.set_hero("芬妮")
        lanes.secondary.window.open_url = mock.Mock(return_value=True)
        with mock.patch.object(overlay_mod.hub, "hero_plans_url", return_value="u") as plans:
            lanes.secondary.window.open_hub("plans")
        self.assertEqual(plans.call_args.args[1], "芬妮")


class DiagnosticTest(Base):
    def exporting(self):
        """两张悬浮窗、诊断模块换成假的（不写盘）：返回 (lanes, 每次 export 收到的参数)。第一张的 run_background 换成直接调用，
        这样走的就是真的 Background.export_diagnostics。"""
        calls = []

        def export(frame_bgr, snapshot, events, advice_text_, extra=None, lanes=None, **shot):
            calls.append({"frame": frame_bgr, "snapshot": snapshot, "events": events, "extra": extra,
                          "lanes": lanes, "shot": shot})
            return str(self.dir / "诊断包")

        lanes = self.make(two=True, services=Services(diagnostics=SimpleNamespace(export=export)))
        lanes.primary.window.run_background = lambda fn: fn()
        return lanes, calls

    def test_export_includes_lanes(self):
        lanes = self.make(two=True)
        lanes.secondary.watcher.last_frame = "第二张画面"
        with mock.patch.object(lanes.primary.window.background, "export_diagnostics") as export:
            lanes.primary.window.export_diagnostics()
        extra = export.call_args.kwargs["extra"]
        self.assertEqual(set(extra["lanes"]), {TAPTAP})
        self.assertIn("diagnostic_state", extra["lanes"][TAPTAP])
        self.assertIn("session", extra["lanes"][TAPTAP])
        sources = export.call_args.kwargs["lanes"]
        self.assertEqual(set(sources), {TAPTAP})
        self.assertEqual(sources[TAPTAP]["frame"], "第二张画面")
        self.assertTrue(callable(sources[TAPTAP]["read"]))
        self.assertTrue(callable(sources[TAPTAP]["stats"]))

    def test_single_lane_has_no_lanes_key(self):
        lanes = self.make()
        with mock.patch.object(lanes.primary.window.background, "export_diagnostics") as export:
            lanes.primary.window.export_diagnostics()
        self.assertIsNone(export.call_args.kwargs["extra"])
        self.assertNotIn("lanes", export.call_args.kwargs)

    def test_second_export_forwards_to_primary(self):
        lanes = self.make(two=True)
        with mock.patch.object(lanes.primary.window, "export_diagnostics") as export:
            lanes.secondary.window.export_diagnostics()
        export.assert_called_once_with()

    def test_export_writes_second_lane_frame_and_events(self):
        """两张都在时导出：另一张最近的画面和本局、上一局的识别记录交给诊断包的 lanes，导出范围写进
        lanes.taptap.export_scope；第一张自己的画面、识别记录、导出范围照旧。"""
        lanes, calls = self.exporting()
        primary_events = [{"time": "2026-10-03T12:41:05.120", "kind": "read:hud", "round": 1}]
        primary_scope = {"segments": [{"file": "20261003-124000.jsonl"}], "since": "2026-10-03T12:40:00.000"}
        lane_events = [{"time": "2026-10-03T13:00:07.000", "kind": "read:hud", "round": 2}]
        lane_scope = {"segments": [{"file": "20261003-130000.jsonl"}], "since": "2026-10-03T13:00:07.000"}
        lanes.primary.watcher.last_frame = "第一张画面"
        lanes.secondary.watcher.last_frame = "第二张画面"
        lanes.primary.session.eventlog.recorder = fake_recorder(primary_events, primary_scope)
        lanes.secondary.session.eventlog.recorder = fake_recorder(lane_events, lane_scope)

        lanes.primary.window.export_diagnostics()

        self.assertEqual(len(calls), 1)
        got = calls[0]
        self.assertEqual(got["frame"], "第一张画面")
        self.assertEqual(got["events"], primary_events)
        self.assertEqual(got["snapshot"]["export_scope"], primary_scope)
        self.assertEqual(got["lanes"], {TAPTAP: {"frame": "第二张画面", "events": lane_events, "stats": []}})
        entry = got["snapshot"]["lanes"][TAPTAP]
        self.assertIn("diagnostic_state", entry)
        self.assertIn("session", entry)
        self.assertEqual(entry["export_scope"], lane_scope)
        self.assertEqual(entry["stats_scope"], {"hours": 12.0, "since": None, "segments": 0, "lines": 0})

    def test_export_from_second_same_as_primary(self):
        """从第二张点「导出诊断」：转给第一张，结果和从第一张点一样（含第二张的画面和识别记录）。"""
        lanes, calls = self.exporting()
        lane_events = [{"time": "2026-10-03T13:00:07.000", "kind": "read:hud", "round": 2}]
        lane_scope = {"segments": [], "since": "2026-10-03T13:00:07.000"}
        lanes.secondary.watcher.last_frame = "第二张画面"
        lanes.secondary.session.eventlog.recorder = fake_recorder(lane_events, lane_scope)

        lanes.secondary.window.export_diagnostics()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["lanes"], {TAPTAP: {"frame": "第二张画面", "events": lane_events, "stats": []}})
        self.assertEqual(calls[0]["snapshot"]["lanes"][TAPTAP]["export_scope"], lane_scope)
        self.assertEqual(calls[0]["snapshot"]["lanes"][TAPTAP]["stats_scope"],
                         {"hours": 12.0, "since": None, "segments": 0, "lines": 0})

    def exporting_with_logbook(self):
        """两张悬浮窗，推荐日志和诊断模块换成假的（不写盘）：返回 (lanes, 每次 export 收到的参数, 假推荐日志)。"""
        calls, logbook = [], FakeLogbook()

        def export(frame_bgr, snapshot, events, advice_text_, extra=None, lanes=None, **shot):
            calls.append({"extra": extra, "snapshot": snapshot})
            return str(self.dir / "诊断包")

        lanes = self.make(two=True, services=Services(logbook=logbook,
                                                      diagnostics=SimpleNamespace(export=export)))
        lanes.primary.window.run_background = lambda fn: fn()
        return lanes, calls, logbook

    @staticmethod
    def set_scopes(lanes, primary_since, lane_since):
        lanes.primary.session.eventlog.recorder = fake_recorder([], {"segments": [], "since": primary_since})
        lanes.secondary.session.eventlog.recorder = fake_recorder([], {"segments": [], "since": lane_since})

    def test_logbook_scope_uses_earlier_lane(self):
        """另一张的本局、上一局比第一张开始得早：推荐日志从另一张的 since 取起。"""
        lanes, calls, logbook = self.exporting_with_logbook()
        self.set_scopes(lanes, "2026-10-03T13:00:00.000", "2026-10-03T12:00:00.000")
        lanes.primary.window.export_diagnostics()
        self.assertEqual(logbook.between[-1], "2026-10-03T12:00:00.000")
        self.assertEqual(len(calls), 1)

    def test_logbook_scope_uses_primary_when_earlier(self):
        """第一张开始得更早：照旧用第一张的 since。"""
        lanes, _, logbook = self.exporting_with_logbook()
        self.set_scopes(lanes, "2026-10-03T12:00:00.000", "2026-10-03T13:00:00.000")
        lanes.primary.window.export_diagnostics()
        self.assertEqual(logbook.between[-1], "2026-10-03T12:00:00.000")

    def test_logbook_scope_ignores_missing_or_bad_lane_since(self):
        """另一张的 since 是 None、读不了时不算，用第一张的。"""
        for lane_since in (None, "不是时刻"):
            with self.subTest(lane_since=lane_since):
                lanes, _, logbook = self.exporting_with_logbook()
                self.set_scopes(lanes, "2026-10-03T13:00:00.000", lane_since)
                lanes.primary.window.export_diagnostics()
                self.assertEqual(logbook.between[-1], "2026-10-03T13:00:00.000")

    def test_logbook_scope_single_lane_unchanged(self):
        """只有一张时结果和原来一样：用第一张的 since。"""
        logbook = FakeLogbook()
        lanes = self.make(services=Services(logbook=logbook,
                                            diagnostics=SimpleNamespace(export=lambda *a, **k: None)))
        lanes.primary.window.run_background = lambda fn: fn()
        lanes.primary.session.eventlog.recorder = fake_recorder([], {"segments": [],
                                                                     "since": "2026-10-03T13:00:00.000"})
        lanes.primary.window.export_diagnostics()
        self.assertEqual(logbook.between, ["2026-10-03T13:00:00.000"])

    def test_logbook_scope_single_lane_unreadable_since_unchanged(self):
        """只有一张、since 读不了：照旧原样交给 records_between（由它按读不了处理），和只按第一张取时一样。"""
        logbook = FakeLogbook()
        lanes = self.make(services=Services(logbook=logbook,
                                            diagnostics=SimpleNamespace(export=lambda *a, **k: None)))
        lanes.primary.window.run_background = lambda fn: fn()
        lanes.primary.session.eventlog.recorder = fake_recorder([], {"segments": [], "since": "不是时刻"})
        lanes.primary.window.export_diagnostics()
        self.assertEqual(logbook.between, ["不是时刻"])

    def test_logbook_scope_out_of_range_lane_since_still_exports(self):
        """另一张的 since 换不成本地时间（换出来超出年份范围）：不算它，诊断包照常导出，推荐日志从第一张的 since 取起。"""
        lanes, calls, logbook = self.exporting_with_logbook()
        self.set_scopes(lanes, "2026-10-03T13:00:00.000", "0001-01-01T00:00:00+05:00")
        lanes.primary.window.export_diagnostics()
        self.assertEqual(len(calls), 1)
        self.assertEqual(logbook.between[-1], "2026-10-03T13:00:00.000")
        self.assertIn(diagnostics_mod.LOGBOOK, calls[0]["extra"])

    def test_logbook_scope_compares_by_time_not_text(self):
        """按时刻比、不按字面比：两个带时区的时刻，按字面 05 点那个在前，按时刻 13 点（+09:00）那个早一小时；带时区和
        不带时区的混在一起也能比（都换成本地时间，前一天零点 UTC 那个一定更早）。"""
        def earliest(primary, lane):
            return Background._earliest_scope({"since": primary}, {TAPTAP: {"export_scope": {"since": lane}}})

        plus9, utc = "2026-10-03T13:00:00+09:00", "2026-10-03T05:00:00+00:00"
        self.assertEqual(earliest(plus9, utc), {"since": plus9})
        self.assertEqual(earliest(utc, plus9), {"since": plus9})
        naive, aware = "2026-10-03T12:00:00.000", "2026-10-02T00:00:00+00:00"
        self.assertEqual(earliest(naive, aware), {"since": aware})
        self.assertEqual(earliest(aware, naive), {"since": aware})

    def test_logbook_each_lane_keeps_its_own_range(self):
        """两张的推荐日志在同一份日志里：按两张里最早的那一刻取出来以后，每条只留它那一张自己的导出范围以内的。另一张
        开始得早时，这段时间里第一张更早的记录不带进来；和起点同一秒的不漏（起点按秒向下取整，推荐日志的时刻只到秒）。"""
        lanes, calls, logbook = self.exporting_with_logbook()
        logbook.records = [
            {"t": "offer", "time": "2026-10-03T11:00:00", "offer_id": "a"},  # 两张的范围都没到
            {"t": "offer", "lane": TAPTAP, "time": "2026-10-03T12:10:00", "offer_id": "taptap-1"},
            {"t": "offer", "time": "2026-10-03T12:30:00", "offer_id": "b"},  # 第一张的，早于第一张的起点
            {"t": "offer", "time": "2026-10-03T13:00:00", "offer_id": "c"},  # 和第一张的起点同一秒
            {"t": "offer", "lane": TAPTAP, "time": "2026-10-03T13:20:00", "offer_id": "taptap-2"},
        ]
        self.set_scopes(lanes, "2026-10-03T13:00:00.500", "2026-10-03T12:00:00.000")
        lanes.primary.window.export_diagnostics()
        self.assertEqual(logbook.between[-1], "2026-10-03T12:00:00.000")
        got = calls[0]["extra"][diagnostics_mod.LOGBOOK]
        self.assertEqual([r["offer_id"] for r in got], ["taptap-1", "c", "taptap-2"])

    def test_lane_starts_and_own_range(self):
        """_lane_starts：只有一张（没有 lanes）时为空表、不筛；起点换成本地时间、按秒向下取整，读不了的那条线不放进来。
        _in_own_range：没有 "lane" 的按第一张的起点；起点不知道的线、时刻读不了、不是字典、"lane" 不是文字的都留着。"""
        from datetime import datetime

        self.assertEqual(Background._lane_starts({"since": "2026-10-03T13:00:00.000"}, None), {})
        self.assertEqual(Background._lane_starts({"since": "2026-10-03T13:00:00.000"}, {}), {})
        starts = Background._lane_starts({"since": "2026-10-03T13:00:00.700"},
                                         {TAPTAP: {"export_scope": {"since": "不是时刻"}}})
        self.assertEqual(starts, {None: datetime(2026, 10, 3, 13, 0, 0)})
        starts = {None: datetime(2026, 10, 3, 13, 0, 0), TAPTAP: datetime(2026, 10, 3, 12, 0, 0)}
        self.assertFalse(_in_own_range({"time": "2026-10-03T12:59:59"}, starts))
        self.assertTrue(_in_own_range({"time": "2026-10-03T13:00:00"}, starts))
        self.assertTrue(_in_own_range({"lane": TAPTAP, "time": "2026-10-03T12:00:00"}, starts))
        self.assertFalse(_in_own_range({"lane": TAPTAP, "time": "2026-10-03T11:59:59"}, starts))
        for record in ({"lane": "别的线", "time": "2026-10-03T01:00:00"}, {"time": "读不了"}, {},
                       "不是字典", {"lane": ["x"], "time": "2026-10-03T01:00:00"}):
            with self.subTest(record=record):
                self.assertTrue(_in_own_range(record, starts))

    def test_export_second_lane_without_frame(self):
        """第二张还没有画面：frame 为 None（诊断包里不写这张画面），识别记录照带，照常导出。"""
        lanes, calls = self.exporting()
        lanes.secondary.session.eventlog.recorder = fake_recorder([{"kind": "x"}], {"segments": [], "since": None})
        lanes.primary.window.export_diagnostics()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["lanes"][TAPTAP]["frame"])
        self.assertEqual(calls[0]["lanes"][TAPTAP]["events"], [{"kind": "x"}])
        self.assertEqual(calls[0]["lanes"][TAPTAP]["stats"], [])

    def test_diagnostic_sources_error_still_exports(self):
        """取第二张的画面和识别记录时出错：记一条错误记录，诊断包照常导出、不带 lanes；第二张的状态照旧在 extra 里。"""
        log = capture_errors(self)
        lanes, calls = self.exporting()
        with mock.patch.object(lanes, "diagnostic_sources", side_effect=RuntimeError("取不到")):
            lanes.primary.window.export_diagnostics()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["lanes"])
        self.assertIn(TAPTAP, calls[0]["snapshot"]["lanes"])
        titles = [title for title, _ in log.entries()]
        self.assertTrue(any("第二张" in title and "画面和识别记录" in title for title in titles), titles)
        log.clear()

    def test_single_lane_export_called_as_before(self):
        """只有一张时调用方式和原来一样：diagnostics.export 收到的参数里没有 lanes（假函数不认这个参数）。"""
        calls = []

        def export(frame_bgr, snapshot, events, advice_text_, extra=None):
            calls.append((frame_bgr, snapshot, events, advice_text_, extra))
            return str(self.dir / "诊断包")

        lanes = self.make(services=Services(diagnostics=SimpleNamespace(export=export)))
        lanes.primary.window.run_background = lambda fn: fn()
        lanes.primary.window.export_diagnostics()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][4], {"stats.jsonl": []})

    def test_export_really_writes_taptap_folder(self):
        """用真的诊断模块写盘：两张都在时诊断包里有 taptap/frame.png、taptap/events.jsonl，
        state.json 的 lanes.taptap.export_scope 是第二张的导出范围。"""
        reports = self.dir / "reports"
        reports.mkdir()
        settings = self.dir / "diag-settings"
        settings.mkdir()
        lanes = self.make(two=True, services=Services(diagnostics=diagnostics_mod))
        lanes.primary.window.run_background = lambda fn: fn()
        image = np.zeros((24, 32, 3), np.uint8)
        image[4:8, 5:9] = (10, 200, 30)
        lanes.secondary.watcher.last_frame = image
        lane_events = [{"time": "2026-10-03T14:00:05.000", "kind": "read:hud", "round": 2}]
        lane_scope = {"segments": [{"file": "20261003-140000.jsonl"}], "since": "2026-10-03T14:00:05.000"}
        lanes.secondary.session.eventlog.recorder = fake_recorder(lane_events, lane_scope)
        with mock.patch.object(paths, "reports_dir", return_value=reports), \
                mock.patch.object(paths, "state_path", return_value=settings / "state.json"), \
                mock.patch.object(paths, "schemes_path", return_value=settings / "schemes.json"):
            lanes.primary.window.export_diagnostics()

        out = next(path for path in reports.iterdir() if path.is_dir())
        self.assertTrue((out / TAPTAP / diagnostics_mod.FRAME).is_file())
        self.assertTrue((out / TAPTAP / diagnostics_mod.EVENTS).is_file())
        self.assertTrue((out / TAPTAP / diagnostics_mod.STATS).is_file())
        state = json.loads((out / diagnostics_mod.STATE).read_text(encoding="utf-8"))
        entry = state["lanes"][TAPTAP]
        self.assertEqual(entry["export_scope"], lane_scope)
        self.assertEqual(entry["stats_scope"], {"hours": 12.0, "since": None, "segments": 0, "lines": 0})
        self.assertIn("diagnostic_state", entry)
        self.assertIn("session", entry)
        self.assertNotIn("frame_error", entry)
        lines = (out / TAPTAP / diagnostics_mod.EVENTS).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0]), lane_events[0])


class ShutdownTest(Base):
    def test_shutdown_stops_both_and_saves(self):
        lanes = self.make(platform=BOTH, two=True)
        second = lanes.secondary
        with mock.patch.object(lanes.primary.window, "shutdown", return_value=True), \
                mock.patch.object(second.window, "shutdown", return_value=True):
            self.assertTrue(lanes.shutdown())
        self.assertTrue(second.watcher.stopped)
        lanes.save_secondary()
        self.assertIn(TAPTAP, self.saved()["lanes"])

    def test_watchers_wait_one_deadline_together(self):
        """两张都停不下来：总共只等一个期限（不是一张等完再等另一张），两张各记一条错误记录。
        一张等完再等另一张要等两个期限（1 秒）；上限留出两张悬浮窗自己收尾的时间，机器忙时也不误报。"""
        lanes = self.make(platform=BOTH, two=True)
        for lane in lanes.all():
            lane.watcher.stop_result = False
            lane.watcher.stop_seconds = 1.0
        errors = capture_errors(self)
        with mock.patch("advisor.ui.overlay.SHUTDOWN_WATCHER_S", 0.5):
            started = time.monotonic()
            stopped = lanes.shutdown()
            elapsed = time.monotonic() - started
        self.assertFalse(stopped)
        self.assertLess(elapsed, 0.85)
        self.assertGreaterEqual(elapsed, 0.45)
        titles = [title for title, _ in errors.entries()]
        self.assertEqual(titles.count("退出时后台识别没有在 0.5 秒内停下，已直接结束拾星助手"), 2)

    def test_second_hidden_before_primary_waits(self):
        """第二张先收：第一张的收尾要等下载、更新停下，这段时间里第二张已经藏起来了。"""
        lanes = self.make(platform=BOTH, two=True)
        order = []
        primary, second = lanes.primary.window, lanes.secondary.window
        with mock.patch.object(primary, "shutdown", side_effect=lambda **kw: order.append("primary") or True), \
                mock.patch.object(second, "shutdown", side_effect=lambda **kw: order.append("second") or True):
            lanes.shutdown()
        self.assertEqual(order, ["second", "primary"])

    def test_ocr_check_stopped_before_watchers(self):
        """退出时先叫停文字识别线程数的自检（限时等它），再停识别线程；限时内没停下算没停下，照样往下收尾。"""
        lanes = self.make(platform=BOTH, two=True)
        check = lanes.primary.window.ocr_check
        for stopped in (True, False):
            order = []
            with self.subTest(stopped=stopped), \
                    mock.patch.object(check, "shutdown", side_effect=lambda timeout: order.append(("check", timeout)) or stopped), \
                    mock.patch.object(lanes.primary.watcher, "request_stop", side_effect=lambda: order.append("first")), \
                    mock.patch.object(lanes.secondary.watcher, "request_stop", side_effect=lambda: order.append("second")):
                self.assertEqual(lanes.shutdown(), stopped)
                # 之后各张自己的收尾还会再发一次停止请求（window.shutdown），这里只看最前面的先后
                self.assertEqual(order[:3], [("check", 2.0), "first", "second"])


class DualTestTest(Base):
    def setUp(self):
        super().setUp()
        FakeDualCheck.instances = []
        FakeDualCheck.next_gate = None
        self.patcher = mock.patch("advisor.ui.dual_check.DualCheck", FakeDualCheck)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(setattr, FakeDualCheck, "next_gate", None)

    def test_pause_resume_and_done(self):
        lanes = self.make(two=True)
        done = []
        primary, second = lanes.primary, lanes.secondary
        primary.window.run_background = lambda fn: fn()
        primary.window.start_dual_test(False, lambda d, t: None, done.append)
        self.assertTrue(primary.watcher.paused.get("dual"))
        self.assertTrue(second.watcher.paused.get("dual"))
        primary.window.wait_dual(2.0)
        APP.processEvents()
        self.assertEqual(len(done), 1)
        self.assertFalse(primary.watcher.paused.get("dual"))
        self.assertFalse(second.watcher.paused.get("dual"))

    def test_cancel_restores_immediately(self):
        lanes = self.make(two=True)
        primary = lanes.primary
        gate = threading.Event()
        FakeDualCheck.next_gate = gate
        self.addCleanup(setattr, FakeDualCheck, "next_gate", None)
        primary.window.start_dual_test(False, lambda d, t: None, lambda r: None)
        self.assertTrue(primary.watcher.paused.get("dual"))
        primary.window.cancel_dual_test()
        self.assertFalse(primary.watcher.paused.get("dual"))
        gate.set()
        primary.window.wait_dual(2.0)
        APP.processEvents()

    def test_stale_done_after_retest_keeps_new_pause(self):
        lanes = self.make(two=True)
        primary = lanes.primary
        first_gate, second_gate = threading.Event(), threading.Event()
        FakeDualCheck.next_gate = first_gate
        primary.window.start_dual_test(False, lambda d, t: None, lambda r: None)
        primary.window.cancel_dual_test()
        FakeDualCheck.next_gate = second_gate
        primary.window.start_dual_test(False, lambda d, t: None, lambda r: None)
        self.assertTrue(primary.watcher.paused.get("dual"))
        first_gate.set()  # 旧的那次结束：只清理，不解开新一次的暂停
        time.sleep(0.05)
        APP.processEvents()
        self.assertTrue(primary.watcher.paused.get("dual"))
        second_gate.set()
        primary.window.wait_dual(2.0)
        APP.processEvents()
        self.assertFalse(primary.watcher.paused.get("dual"))


class GamelogFolderTest(unittest.TestCase):
    def test_subfolder_created_and_not_pruned(self):
        folder = sandbox.temp_dir(self, "test-lanes-logs-")
        main = gamelog.GameRecorder(folder, keep=1).open("startup")
        try:
            sub = folder / TAPTAP
            second = gamelog.GameRecorder(sub).open("startup")
            second.close()
            self.assertTrue(sub.is_dir())
            self.assertTrue(any(sub.iterdir()))
            # 第一份删旧段时不碰子文件夹
            for i in range(3):
                main.new_segment("size")
            self.assertTrue(sub.is_dir())
        finally:
            main.close()


class RecordFolderTest(Base):
    def test_unwritable_taptap_folder_still_adds_second(self):
        """识别记录的 taptap 子文件夹建不出来（那里是个同名文件）：照样加上第二张，写盘错误由记录器自己记。"""
        (self.dir / TAPTAP).write_text("", encoding="utf-8")
        reported = []
        lanes = self.make()
        lanes.record = True
        lanes.recorder_factory = lambda folder: gamelog.GameRecorder(
            folder, on_error=lambda title, detail: reported.append(title))
        with mock.patch.object(lanes_mod, "games_dir", return_value=self.dir):
            lane = lanes.add_secondary()
        self.assertIsNotNone(lane)
        self.windows.append(lane.window)
        self.assertIsNotNone(lane.recorder)
        self.assertEqual(lane.recorder.folder, self.dir / TAPTAP)
        self.assertTrue(reported)  # 段头写不进：记录器记了一次写盘错误


class LaneTagTest(Base):
    """标题栏最左边的端标签：同时识别时第一张写 Steam、第二张写 TapTap；只有一张时藏着；增减第二张时跟着出现、去掉。"""

    def test_single_lane_has_no_tag(self):
        lanes = self.make()
        tag = lanes.primary.window.lane_tag
        self.assertTrue(tag.isHidden())
        self.assertEqual(tag.text(), "")
        self.assertEqual(tag.toolTip(), "")

    def test_two_lanes_tag_each_end(self):
        lanes = self.make(platform=BOTH, two=True)
        primary, second = lanes.primary.window, lanes.secondary.window
        self.assertFalse(primary.lane_tag.isHidden())
        self.assertEqual(primary.lane_tag.text(), "Steam")
        self.assertIn(capture.PLATFORM_NAMES[STEAM], primary.lane_tag.toolTip())
        self.assertFalse(second.lane_tag.isHidden())
        self.assertEqual(second.lane_tag.text(), "TapTap")
        self.assertIn(capture.PLATFORM_NAMES[TAPTAP], second.lane_tag.toolTip())
        self.assertIn(lanes_mod.LANE_TAG_TIP.format(name=capture.PLATFORM_NAMES[STEAM]), primary.lane_tag.toolTip())

    def test_switch_platform_adds_and_removes_tag(self):
        lanes = self.make()
        window = lanes.primary.window
        plain = window._summary_width()
        self.assertTrue(window.lane_tag.isHidden())
        lanes.set_platform(BOTH)
        self.assertFalse(window.lane_tag.isHidden())
        self.assertEqual(window.lane_tag.text(), "Steam")
        self.assertLess(window._summary_width(), plain)
        lanes.set_platform(None)
        self.assertTrue(window.lane_tag.isHidden())
        self.assertEqual(window._summary_width(), plain)
        lanes.set_platform(BOTH)
        self.assertFalse(window.lane_tag.isHidden())


class TwoLaneLayerTest(Base):
    """同时识别时两张的窗口层次：没找到游戏窗口的那张看另一张（window_plan 的 companion），另一张的游戏在前台时也
    置顶；另一张的游戏切到后台、最小化时退回不置顶，并放到前台窗口的正下方；关了、从托盘显示回来、只有一张时不动。
    前台窗口、置顶与否、类名都用假的，记下每一次改层次的调用（带窗口句柄）。"""

    DESKTOP = 0x5000

    def setUp(self):
        super().setUp()
        self.calls: list[tuple] = []
        self.fg = (0, False)  # 前台窗口（句柄, 是不是拾星助手自己的）
        self.top: set[int] = set()  # 现在置顶的窗口
        self.classes: dict[int, str] = {}
        self.zorder: list[int] = []  # 顶层窗口的先后，从上到下（Win32 那边按它回答 below）

        def front(h):
            h = int(h)
            if h in self.zorder:
                self.zorder.remove(h)
            self.zorder.insert(0, h)

        def set_topmost(h, on):
            self.calls.append(("top", h, on))
            if on:
                self.top.add(h)
                front(h)
            else:
                self.top.discard(h)
                keep = [x for x in self.zorder if x in self.top]
                if h in self.zorder:
                    self.zorder.remove(h)
                self.zorder.insert(max((self.zorder.index(x) for x in keep), default=-1) + 1, h)
            return True

        def put_below(h, after):
            self.calls.append(("below", h, after))
            self.top.discard(h)
            h, after = int(h), int(after)
            if h in self.zorder:
                self.zorder.remove(h)
            if after in self.zorder:
                self.zorder.insert(self.zorder.index(after) + 1, h)
            else:
                self.zorder.append(h)
            return True

        def bring_to_front(h):
            self.calls.append(("front", h))
            self.top.add(h)
            front(h)
            return True

        def below(hwnd, wanted):
            wanted = {int(h) for h in wanted}
            hwnd = int(hwnd)
            start = self.zorder.index(hwnd) + 1 if hwnd in self.zorder else 0
            return [h for h in self.zorder[start:] if h in wanted]

        patches = [
            mock.patch("advisor.ui.winapi.set_topmost", side_effect=set_topmost),
            mock.patch("advisor.ui.winapi.put_below", side_effect=put_below),
            mock.patch("advisor.ui.winapi.bring_to_front", side_effect=bring_to_front),
            mock.patch("advisor.ui.winapi.below", side_effect=below),
            mock.patch("advisor.ui.winapi.foreground", side_effect=lambda: self.fg),
            mock.patch("advisor.ui.winapi.topmost", side_effect=lambda h: h in self.top),
            mock.patch("advisor.ui.winapi.window_class", side_effect=lambda h: self.classes.get(h, "")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def make_two(self):
        lanes = self.make(two=True)
        self.calls.clear()
        # 假的窗口先后：第二张先排在前面，restack 要按游戏在前台的那张排回去
        self.zorder[:] = [self.hwnd(lanes.secondary), self.hwnd(lanes.primary)]
        return lanes

    @staticmethod
    def set_probe(lane, **kw):
        kw.setdefault("hwnd", 0x1001 if lane.primary else 0x1002)
        lane.window.probe = lambda: probe(**kw)

    @staticmethod
    def poll(lane):
        lane.window.follower.poll()

    @staticmethod
    def hwnd(lane) -> int:
        return int(lane.window.winId())

    def below_calls(self) -> list:
        return [call for call in self.calls if call[0] == "below"]

    def test_missing_lane_tops_when_other_game_front(self):
        """第一张的游戏在前台：第二张（找不到游戏窗口）下一次查看就置顶，识别仍暂停、热键没因它登记。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, found=False)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "top")
        self.assertIn(("top", self.hwnd(second), True), self.calls)
        self.assertTrue(second.watcher.paused.get("focus"))
        self.assertEqual(second.watcher.why, "nogame")
        self.assertFalse(second.window.follower.plan.hotkey)
        self.assertFalse(lanes._hotkey_wants[id(second)])
        self.assertIs(primary.window._companion_activity(), "nogame")
        self.assertIs(second.window._companion_activity(), "game")

    def test_missing_primary_tops_when_second_game_front(self):
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(second, fg="game")
        self.set_probe(primary, found=False)
        self.poll(second)
        self.calls.clear()
        self.poll(primary)
        self.assertEqual(primary.window.follower.plan.layer, "top")
        self.assertIn(("top", self.hwnd(primary), True), self.calls)
        self.assertTrue(primary.watcher.paused.get("focus"))
        self.assertEqual(primary.watcher.why, "nogame")
        self.assertFalse(lanes._hotkey_wants[id(primary)])

    def top_second_then_other(self, **probe_kw):
        """把第二张弄成置顶（第一张的游戏在前台），再把第一张改成 probe_kw 的状态，返回（第二张、它退回时的调用）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, found=False)
        self.poll(primary)
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "top")
        self.fg = (self.DESKTOP, False)  # 前台是别的程序（普通窗口）
        self.classes[self.DESKTOP] = "Notepad"
        self.set_probe(primary, **probe_kw)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "none")
        return second, self.calls

    def test_other_background_steps_behind(self):
        """第一张的游戏切到后台：第二张退回不置顶，并放到前台窗口的正下方。"""
        second, calls = self.top_second_then_other(fg="other")
        self.assertIn(("top", self.hwnd(second), False), calls)
        self.assertIn(("below", self.hwnd(second), self.DESKTOP), calls)

    def test_other_minimized_steps_behind(self):
        """第一张的游戏最小化：同切到后台，也放到前台窗口下面。"""
        second, calls = self.top_second_then_other(minimized=True)
        self.assertIn(("top", self.hwnd(second), False), calls)
        self.assertIn(("below", self.hwnd(second), self.DESKTOP), calls)

    def test_other_closed_no_step_behind(self):
        """第一张的游戏也关了：两张都没有游戏，第二张不置顶，但不放到前台窗口下面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, found=False)
        self.poll(primary)
        self.poll(second)
        self.fg = (self.DESKTOP, False)
        self.classes[self.DESKTOP] = "Notepad"
        self.set_probe(primary, found=False)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "none")
        self.assertEqual(self.below_calls(), [])

    def test_tray_show_does_not_step_behind(self):
        """从托盘「显示」回来（reset_layer，从 front 算起）：另一张的游戏刚切到后台，也不放到前台窗口下面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, found=False)
        self.poll(primary)
        self.poll(second)
        self.fg = (self.DESKTOP, False)
        self.classes[self.DESKTOP] = "Notepad"
        self.set_probe(primary, fg="other")
        self.poll(primary)
        self.calls.clear()
        second.window.follower.reset_layer()
        self.assertEqual(second.window.follower.plan.layer, "none")
        self.assertEqual(self.below_calls(), [])

    def test_own_game_minimized_no_step_behind(self):
        """只有找不到游戏窗口的那张才放到前台窗口下面：这张自己的游戏最小化（从置顶退回不置顶）时，另一张的游戏在后台
        也不放，和只有一张时一样。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="other")
        self.set_probe(second, fg="game")
        self.poll(primary)
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "top")
        self.fg = (self.DESKTOP, False)
        self.classes[self.DESKTOP] = "Notepad"
        self.set_probe(second, minimized=True)
        self.calls.clear()
        self.poll(second)
        self.assertEqual(primary.window.follower.activity, "background")
        self.assertEqual(second.window.follower.activity, "minimized")
        self.assertEqual(second.window.follower.plan.layer, "none")
        self.assertIn(("top", self.hwnd(second), False), self.calls)
        self.assertEqual(self.below_calls(), [])

    def test_single_lane_never_tops_without_game(self):
        """只有一张（只选一个端）：找不到游戏窗口永远不置顶，也不调 step_behind_foreground。"""
        lanes = self.make()
        window = lanes.primary.window
        self.set_probe(lanes.primary, found=False)
        self.calls.clear()
        self.poll(lanes.primary)
        self.assertEqual(window.follower.plan.layer, "none")
        self.assertIsNone(window._companion_activity())
        self.assertEqual(self.below_calls(), [])

    def test_background_lane_stays_top_when_other_game_front(self):
        """同时在玩另一个游戏：这一张的游戏在后台、被另一个游戏的窗口盖住，也照样置顶、不退到后面（直接截取时照常识别）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "top")
        self.assertIn(("top", self.hwnd(second), True), self.calls)
        self.assertEqual(self.below_calls(), [])
        self.assertFalse(second.watcher.paused.get("focus"))

    def test_background_lane_stays_top_when_second_game_front(self):
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(second, fg="game")
        self.set_probe(primary, fg="other", covered=True)
        self.poll(second)
        self.calls.clear()
        self.poll(primary)
        self.poll(primary)
        self.assertEqual(primary.window.follower.plan.layer, "top")
        self.assertIn(("top", self.hwnd(primary), True), self.calls)
        self.assertEqual(self.below_calls(), [])

    def test_background_lane_steps_behind_when_other_not_front(self):
        """另一张的游戏也在后台（前台是别的程序）：照原来的规矩，连续被盖住两次就退到盖住它的窗口后面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="other")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.poll(second)
        self.assertEqual(second.window.follower.plan.layer, "below")
        self.assertIn(("below", self.hwnd(second), 0x2002), self.calls)

    def test_screen_lane_paused_still_tops(self):
        """截取屏幕方式：这一张的游戏在后台时识别照旧暂停，但也置顶（同找不到游戏窗口那张）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        second.window.set_expected_capture("screen")
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.calls.clear()
        self.poll(second)
        self.poll(second)
        self.assertTrue(second.window.follower.plan.paused)
        self.assertEqual(second.watcher.why, "screen-background")
        self.assertEqual(second.window.follower.plan.layer, "top")
        self.assertIn(("top", self.hwnd(second), True), self.calls)
        self.assertEqual(self.below_calls(), [])

    def test_probe_passes_other_game_as_ignore(self):
        """查「被盖住」时把另一张跟的游戏窗口交给 probe_game 的 ignore；另一张找不到游戏窗口、没有另一张时为 None。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other")
        self.poll(primary)
        self.poll(second)
        calls = []
        lanes.probe_game = lambda **kw: calls.append(kw) or None
        lanes._probe(second)()
        self.assertEqual(calls[-1]["ignore"], 0x1001)
        self.set_probe(primary, found=False)
        self.poll(primary)
        calls.clear()
        lanes._probe(second)()
        self.assertIsNone(calls[-1]["ignore"])
        single = self.make()
        single.probe_game = lambda **kw: calls.append(kw) or None
        calls.clear()
        single._probe(single.primary)()
        self.assertIsNone(calls[-1]["ignore"])

    def test_restack_puts_front_game_lane_on_top(self):
        """两张都置顶、第二张排在上面时，把游戏在前台的第一张排到上面，并记一条 layer_order。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.poll(second)
        records = []
        primary.session.log_state = lambda kind, **data: records.append((kind, data))
        winapi.bring_to_front(self.hwnd(second))  # 第二张排在前面
        self.calls.clear()
        lanes.restack()
        self.assertEqual(self.calls, [("front", self.hwnd(primary))])
        self.assertEqual(records, [("layer_order", {"top": "steam"})])

    def test_restack_noop_cases(self):
        """另一张已经排在下面、不是都置顶、两张都不是游戏在前台、只有一张、native 为假时都不动。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.poll(second)
        winapi.bring_to_front(self.hwnd(primary))
        self.calls.clear()
        lanes.restack()
        self.assertEqual(self.calls, [])
        for layer in ("none", "below"):
            with self.subTest(layer=layer):
                second.window.follower.layer = (layer, None)
                self.calls.clear()
                lanes.restack()
                self.assertEqual(self.calls, [])
        second.window.follower.layer = ("top", None)
        self.set_probe(primary, fg="other")
        self.poll(primary)
        self.calls.clear()
        lanes.restack()
        self.assertEqual(self.calls, [])  # 两张都不是游戏在前台
        primary.window.native = False
        self.set_probe(primary, fg="game")
        self.poll(primary)
        self.calls.clear()
        lanes.restack()
        self.assertEqual(self.calls, [])
        single = self.make()
        self.calls.clear()
        single.restack()
        self.assertEqual(self.calls, [])

    def test_restack_after_other_live_brings_front_game_back(self):
        """第二张出三选一跳到最前面以后，第一张（游戏在前台）又被排回上面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.poll(second)
        second.window.live_state.on = True
        self.calls.clear()
        self.poll(second)
        self.assertEqual(second.window.follower.layer[0], "front")
        self.assertIn(("front", self.hwnd(second)), self.calls)
        self.assertEqual(self.calls[-1], ("front", self.hwnd(primary)))

    def test_restack_after_tray_show(self):
        """从托盘「显示」两张都放到最前面以后，游戏在前台的那张在上面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.poll(second)
        primary.window.tray_part.show_more = lanes.show_secondary
        self.calls.clear()
        primary.window.tray_part.show_from_tray()
        self.assertEqual(self.calls[-1], ("front", self.hwnd(primary)))

    def test_restack_after_switching_game(self):
        """切到另一个游戏（第一张从 game 变 background、第二张变 game）以后，第二张排在上面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other", covered=True)
        self.poll(primary)
        self.poll(second)
        self.set_probe(primary, fg="other")
        self.set_probe(second, fg="game")
        self.calls.clear()
        self.poll(primary)
        self.poll(second)
        self.assertEqual(self.calls[-1], ("front", self.hwnd(second)))

    def open_window(self, lanes) -> int:
        """开着一个独立窗口（设置、编辑方案这类，两张共用第一张的那一份），放在最上面；返回它的句柄。"""
        dialog = QWidget()
        dialog.show()
        self.windows.append(dialog)
        stack = lanes.primary.window.windows
        stack.opened.append(dialog)
        stack.mark_layer(dialog, "top")
        hwnd = int(dialog.winId())
        self.zorder.insert(0, hwnd)
        self.top.add(hwnd)
        return hwnd

    def test_restack_keeps_open_windows_above(self):
        """设置窗口开着时点回游戏：游戏在前台的那张排到另一张上面以后，设置窗口照旧压在悬浮窗上面（同只有一张时）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        for lane in (primary, second):
            self.set_probe(lane, fg="own")
            self.poll(lane)
        self.zorder[:] = [self.hwnd(second), self.hwnd(primary)]
        dialog = self.open_window(lanes)
        self.set_probe(primary, fg="game")
        self.calls.clear()
        self.poll(primary)
        self.assertIn(("front", self.hwnd(primary)), self.calls)
        self.assertEqual(self.calls[-1], ("front", dialog))
        self.assertEqual(self.zorder[:3], [dialog, self.hwnd(primary), self.hwnd(second)])

    def test_restack_other_live_leaves_windows(self):
        """另一张在三选一（front，本来就盖过独立窗口）时排先后不把独立窗口放回上面。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other")
        self.poll(primary)
        self.poll(second)
        dialog = self.open_window(lanes)
        second.window.live_state.on = True
        self.calls.clear()
        self.poll(second)
        self.assertEqual(second.window.follower.layer[0], "front")
        self.assertEqual(self.calls[-1], ("front", self.hwnd(primary)))
        self.assertNotIn(("front", dialog), self.calls)
        self.assertEqual(self.zorder[:2], [self.hwnd(primary), self.hwnd(second)])

    def test_tray_show_puts_open_windows_above_both(self):
        """玩着 TapTap 那边时从托盘「显示」：两张都放到最前面以后，开着的独立窗口照旧压在两张悬浮窗上面（第二张放到最前面
        以后也把共用的那一份放回它上面，同只有一张时）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="other")
        self.set_probe(second, fg="game")
        self.poll(primary)
        self.poll(second)
        self.assertEqual((primary.window.follower.layer[0], second.window.follower.layer[0]), ("top", "top"))
        dialog = self.open_window(lanes)
        primary.window.tray_part.show_more = lanes.show_secondary
        self.calls.clear()
        primary.window.tray_part.show_from_tray()
        self.assertIn(("front", self.hwnd(second)), self.calls)
        self.assertEqual(self.calls[-1], ("front", dialog))
        self.assertEqual(self.zorder[:3], [dialog, self.hwnd(second), self.hwnd(primary)])

    def test_second_relayer_uses_shared_stack(self):
        """第二张的托盘和层次部件接的是共用的那一份独立窗口：开着的窗口、记下的层次都在那一份里；第二张调 relayer 照第一张的
        层次设（第二张自己的层次不一样也不跟着它）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.set_probe(primary, fg="game")
        self.set_probe(second, fg="other")
        self.poll(primary)
        self.poll(second)
        hwnd = self.open_window(lanes)
        dialog = self.windows[-1]
        stack = primary.window.windows
        self.assertIs(second.window.windows, stack)
        self.assertEqual(second.window.tray_part.open_windows(), [dialog])
        second.window.tray_part.mark_layer(dialog, "none")  # 记着的和第一张现在的层次（top）对不上
        self.assertEqual(stack.layers.get(dialog), "none")
        second.window.follower.layer = ("none", None)
        self.calls.clear()
        second.window.follower.relayer()
        self.assertEqual(self.calls, [("top", hwnd, True)])
        self.assertEqual(stack.layers.get(dialog), "top")

    def test_second_keeps_primary_settle_round(self):
        """关窗口时临时置顶的那一轮只归第一张：第二张的层次变了不打断它（打断了，临时置顶的就不再退回）。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        stack = primary.window.windows
        state = {"window": None, "closing": 0, "ticks": 0, "first": 0, "seen": 0}
        stack.settling = state
        second.window.follower.cancel_settle()
        self.assertIs(stack.settling, state)
        primary.window.follower.cancel_settle()
        self.assertIsNone(stack.settling)

    def test_removed_second_has_no_companion(self):
        """收掉的第二张（藏着、等着删）不再把第一张当成另一张，第一张也不再有另一张。"""
        lanes = self.make_two()
        primary, second = lanes.primary, lanes.secondary
        self.assertIs(second.window._companion(), primary.window)
        lanes.remove_secondary()
        self.assertIsNone(second.window._companion())
        self.assertIsNone(second.window._companion_activity())
        self.assertIsNone(primary.window._companion())

    def test_step_behind_foreground_branches(self):
        """放在前台窗口下面：前台是普通窗口时放；是拾星助手自己、置顶窗口、桌面、没有前台窗口时不动。"""
        hwnd, fg = 0x111, 0x222
        self.classes[fg] = "Notepad"
        self.fg = (fg, False)
        self.assertTrue(winapi.step_behind_foreground(hwnd))
        self.assertIn(("below", hwnd, fg), self.calls)
        self.assertEqual(winapi.DESKTOP_CLASSES, ("Progman", "WorkerW"))
        for own, top, cls, front in ((True, False, "Notepad", fg), (False, True, "Notepad", fg),
                                     (False, False, "Progman", fg), (False, False, "WorkerW", fg),
                                     (False, False, "Notepad", 0)):
            with self.subTest(own=own, top=top, cls=cls, front=front):
                self.calls.clear()
                self.fg = (front, own)
                if top:
                    self.top.add(fg)
                else:
                    self.top.discard(fg)
                self.classes[fg] = cls
                self.assertFalse(winapi.step_behind_foreground(hwnd))
                self.assertEqual(self.calls, [])


class TwoLanePlacementTest(Base):
    """同时识别时两张的摆放：第二张没拖动过时让开第一张（不在它上面，三选一时也避开），拖动过以后放在拖到的地方；
    两个游戏窗口分开摆时各在各的上面，不受影响；只有一张时和改之前一样。屏幕用假的，两个游戏窗口一样大。"""

    SCREEN = QRect(0, 0, 1600, 1000)
    GAME = (100, 100, 1200, 800)

    def make_placed(self, primary_game=None, second_game=None):
        lanes = self.make(two=True)
        self.primary, self.second = lanes.primary, lanes.secondary
        for lane in (self.primary, self.second):
            lane.window._screens = lambda: [self.SCREEN]
        self.set_game(self.primary, primary_game or self.GAME, 0x1001)
        self.set_game(self.second, second_game or self.GAME, 0x1002)
        for lane in (self.primary, self.second):
            lane.window.follower.poll()
            lane.window.follower.place()
        return lanes

    @staticmethod
    def set_game(lane, rect, hwnd):
        lane.window.probe = lambda: probe(rect=rect, hwnd=hwnd)

    def test_second_starts_beside_primary(self):
        """两个游戏窗口在同一处：第二张没拖动过时建出来就在第一张旁边，和第一张不相交。"""
        self.make_placed()
        p, s = self.primary.window, self.second.window
        self.assertFalse(s.follower.rel_custom)
        self.assertFalse(s.frameGeometry().intersects(p.frameGeometry()))
        self.assertLess(s.frameGeometry().right(), p.frameGeometry().left())
        expected = overlay_position(s.follower.game_rect, (s.width(), s.height()), s.follower.rel,
                                    s.follower.default_rel, [self.SCREEN])
        self.assertNotEqual(s.pos(), expected)  # 默认位置正好压在第一张上，挪到了旁边

    def test_second_moves_when_primary_dragged_onto_it(self):
        self.make_placed()
        p, s = self.primary.window, self.second.window
        p.move(s.pos())
        p.follower.remember_position()
        before = s.pos()
        s.follower.poll()
        self.assertNotEqual(s.pos(), before)
        self.assertFalse(s.frameGeometry().intersects(p.frameGeometry()))

    def test_second_dragged_stays_overlapped(self):
        """第二张拖动过：拖到哪就在哪，和第一张叠着也不动。"""
        self.make_placed()
        p, s = self.primary.window, self.second.window
        s.move(p.pos())
        s.follower.remember_position()
        before = s.pos()
        s.follower.poll()
        self.assertLessEqual((s.pos() - before).manhattanLength(), 2)
        self.assertTrue(s.frameGeometry().intersects(p.frameGeometry()))

    def test_separate_games_keep_own_positions(self):
        """两个游戏窗口分开摆：两张各在自己的游戏窗口上，第二张和只有一张时算出来的一样。"""
        self.make_placed(primary_game=(50, 50, 700, 500), second_game=(850, 50, 700, 500))
        p, s = self.primary.window, self.second.window
        expected = overlay_position(s.follower.game_rect, (s.width(), s.height()), s.follower.rel,
                                    s.follower.default_rel, [self.SCREEN])
        self.assertEqual(s.pos(), expected)
        self.assertFalse(s.frameGeometry().intersects(p.frameGeometry()))

    def test_live_second_avoids_primary(self):
        """三选一时第二张自动让开的位置不和第一张相交。"""
        self.make_placed()
        p, s = self.primary.window, self.second.window
        p.live_state.on = True
        s.live_state.on = True
        p.follower.reposition()
        s.follower.reposition()
        self.assertFalse(s.frameGeometry().intersects(p.frameGeometry()))

    def test_live_first_avoids_dragged_second(self):
        """第二张拖动过（位置固定）时，第一张三选一自动让开也避开它。"""
        self.make_placed()
        p, s = self.primary.window, self.second.window
        s.move(p.pos())
        s.follower.remember_position()
        p.live_state.on = True
        s.live_state.on = True
        s.follower.reposition()
        p.follower.reposition()
        self.assertFalse(p.frameGeometry().intersects(s.frameGeometry()))

    def test_live_first_ignores_unddragged_second(self):
        """第二张没拖动过时第一张算位置不看它（第二张会自己让开，免得两张来回躲）。"""
        self.make_placed()
        p = self.primary.window
        p.live_state.on = True
        expected = live_placement(p.follower.game_rect, (p.width(), p.height()), p.follower.rel, [self.SCREEN])
        self.assertEqual(p.follower.target_position(), expected)

    def test_no_game_still_apart(self):
        """两个游戏都找不到：第二张也不和第一张叠在一起。"""
        lanes = self.make(two=True)
        p, s = lanes.primary.window, lanes.secondary.window
        for window in (p, s):
            window._screens = lambda: [self.SCREEN]
            window.probe = lambda: probe(found=False)
        p.follower.place()
        s.follower.place()
        self.assertFalse(s.frameGeometry().intersects(p.frameGeometry()))

    def test_single_lane_unchanged(self):
        """只有一张时位置和改之前一样。"""
        lanes = self.make()
        w = lanes.primary.window
        w._screens = lambda: [self.SCREEN]
        w.probe = lambda: probe(rect=self.GAME, hwnd=0x1001)
        w.follower.place()
        expected = overlay_position(w.follower.game_rect, (w.width(), w.height()), w.follower.rel,
                                    w.follower.default_rel, [self.SCREEN])
        self.assertEqual(w.pos(), expected)


class TwoLaneResetTest(Base):
    """两张悬浮窗时「重置悬浮窗」：两张一起展开、显示，第一张放到主显示器正中、第二张紧挨在它右边；两张都当作
    用户拖到了那里，各记一条 reset。屏幕用假的，够放两张。"""

    SCREEN = QRect(0, 0, 1600, 1200)

    def setUp(self):
        super().setUp()
        # 接线时两张的 native 都设成真：从托盘显示、排先后会调 Win32 改窗口层次。离屏窗口的句柄是假的，这几个调用换成
        # 假的，不去碰别的真窗口
        for name, value in (("bring_to_front", True), ("set_topmost", True), ("put_below", True), ("below", []),
                            ("foreground", (0, False)), ("topmost", False), ("window_class", "")):
            patcher = mock.patch(f"advisor.ui.winapi.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_two(self):
        lanes = self.make(two=True)
        self.primary, self.second = lanes.primary.window, lanes.secondary.window
        for window in (self.primary, self.second):
            window._screens = lambda: [self.SCREEN]
        self.primary._primary_area = lambda: self.SCREEN
        return lanes

    def prepare(self, lanes):
        """两张都收在托盘里、一行显示，各自收起「已有筹码」这张卡片。"""
        for window in (self.primary, self.second):
            window.set_expanded(False)
            window.cards["owned"].toggle.click()
            window.hide()
        APP.processEvents()

    def reset(self, window=None):
        (window or self.primary).reset_overlay()
        APP.processEvents()
        APP.processEvents()

    def test_both_expand_show_and_clear_collapsed(self):
        lanes = self.make_two()
        self.prepare(lanes)
        self.reset()
        for window in (self.primary, self.second):
            self.assertFalse(window.isHidden())
            self.assertTrue(window.expanded)
            self.assertTrue(window.card_open("owned"))
            self.assertEqual(window._collapsed, set())
            self.assertEqual([v for v in window._dismissed.values() if v], [])
        saved = self.saved()
        self.assertEqual(saved["collapsed_cards"], [])
        self.assertEqual(saved["lanes"][TAPTAP]["collapsed_cards"], [])

    def test_placed_centered_and_beside(self):
        lanes = self.make_two()
        self.prepare(lanes)
        self.reset()
        first = self.primary.frameGeometry()
        second = self.second.frameGeometry()
        self.assertLessEqual(abs(first.center().x() - self.SCREEN.center().x()), 1, first)
        self.assertLessEqual(abs(first.center().y() - self.SCREEN.center().y()), 1, first)
        self.assertEqual(second.x(), first.x() + first.width() + PLACE_GAP)
        self.assertEqual(second.y(), first.y())

    def test_both_remembered_as_dragged(self):
        lanes = self.make_two()
        for lane in (lanes.primary, lanes.secondary):
            lane.window.follower.update_game(
                probe(rect=(0, 0, 1400, 900), hwnd=0x1001 if lane.primary else 0x1002))
        self.prepare(lanes)
        self.reset()
        self.assertTrue(self.primary.follower.rel_custom)
        self.assertTrue(self.second.follower.rel_custom)
        saved = self.saved()
        self.assertEqual(saved["lanes"][TAPTAP]["window_rel"], list(self.second.follower.rel))
        # 两次存盘都留在文件里：第一张的写在顶层，第二张的写在 lanes.taptap，后存的第二张没有盖掉第一张的
        self.assertEqual(saved["window_rel"], list(self.primary.follower.rel))
        self.assertEqual(saved["window_pos"], [self.primary.pos().x(), self.primary.pos().y()])
        self.assertEqual(saved["lanes"][TAPTAP]["window_pos"], [self.second.pos().x(), self.second.pos().y()])

    def test_reset_from_second_forwards_to_primary(self):
        lanes = self.make_two()
        self.prepare(lanes)
        self.reset(window=self.second)
        first = self.primary.frameGeometry()
        second = self.second.frameGeometry()
        self.assertFalse(self.primary.isHidden())
        self.assertFalse(self.second.isHidden())
        self.assertLessEqual(abs(first.center().x() - self.SCREEN.center().x()), 1, first)
        self.assertEqual(second.x(), first.x() + first.width() + PLACE_GAP)

    def test_reset_recorded_for_both(self):
        lanes = self.make_two()
        self.prepare(lanes)
        self.reset()
        for lane in (lanes.primary, lanes.secondary):
            records = [e for e in lane.session.recent_events(600)
                       if e.get("kind") == "read:user" and e.get("action") == "reset"]
            self.assertEqual(len(records), 1, lane.label())
            self.assertTrue(records[0]["hidden"])

    def test_second_hidden_is_state_before_click(self):
        """和启动时一样接上托盘的 show_more（第一张从托盘显示出来时把第二张也显示出来）：第二张记的 hidden 仍是点之前
        收在托盘里。先做第一张的话，它的 show_from_tray 已经把第二张显示出来，第二张就记成没收着。"""
        lanes = self.make_two()
        self.primary.tray_part.show_more = lanes.show_secondary
        self.prepare(lanes)
        self.reset()
        records = [e for e in lanes.secondary.session.recent_events(600)
                   if e.get("kind") == "read:user" and e.get("action") == "reset"]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["hidden"])
        self.assertFalse(self.second.isHidden())

    def test_partner_removed_before_centering(self):
        """点了以后、居中之前第二张被收掉（改回只识别一个客户端）：第一张照常居中，不去摆已经收掉的第二张。"""
        lanes = self.make_two()
        self.prepare(lanes)
        self.primary.reset_overlay()
        lanes.remove_secondary()
        with mock.patch.object(overlay_mod, "pair_position") as pair:
            APP.processEvents()
            APP.processEvents()
        pair.assert_not_called()
        first = self.primary.frameGeometry()
        self.assertLessEqual(abs(first.center().x() - self.SCREEN.center().x()), 1, first)
        self.assertLessEqual(abs(first.center().y() - self.SCREEN.center().y()), 1, first)

    def test_restack_called(self):
        lanes = self.make_two()
        self.prepare(lanes)
        with mock.patch.object(lanes, "restack") as restack:
            self.reset()
        self.assertTrue(restack.called)

    def test_second_partner_returns_other_window(self):
        lanes = self.make_two()
        self.assertIs(self.primary._reset_partner(), self.second)
        self.assertIsNone(self.second._reset_partner())


if __name__ == "__main__":
    unittest.main()
