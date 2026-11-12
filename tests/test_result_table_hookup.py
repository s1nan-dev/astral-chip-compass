"""结算数据表的接线：记账什么时候等表、认出哪一栏是自己、记下的内容（不含名字）；推荐日志的 table 记录；
识别流程什么时候看、什么时候读；悬浮窗记进推荐日志并重拼报告。读表一律用假的函数和假的 TableReading。"""
from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor import logbook, paths
from advisor.data import load_db
from advisor.session import OUT_CONFIRM_GAP_S, TABLE_WAIT_S, Session, Settings
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import reporting
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services
from advisor.vision import pipeline
from advisor.vision.frame import Frame
from tests.support import qtenv, sandbox

APP = qtenv.app()


def column(seat, name=None, bot=None, muted=None, stats=None, best=(), coins_all=0, coins_transferred=0,
           chips=None, chips_unknown=None, badges=None):
    stats = {"kills": 0, "downs": 0, "damage_dealt": 0, "damage_taken": 0, "heal": 0, **(stats or {})}
    return SimpleNamespace(seat=seat, name=name, bot=bot, muted=muted, stats=stats, best=frozenset(best),
                           coins_all=coins_all, coins_transferred=coins_transferred, chips=chips,
                           chips_unknown=chips_unknown, badges=badges)


def reading(columns, consistent=True, map="异变图书馆", difficulty="噩梦", rounds=10):
    return SimpleNamespace(columns=tuple(columns), map=map, difficulty=difficulty, rounds=rounds,
                           consistent=consistent)


def screen(kind):
    return SimpleNamespace(kind=kind, difficulty=None, map=None, rounds=None)


class TableCase(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.clock = [1000.0]
        self.session = Session(self.db, Settings(hero="凛"), clock=lambda: self.clock[0])
        self.session.game.seen_hero = "凛"
        self.session.settings.round = 3
        self.session.settings.owned = ["书签"]

    def end_by_result(self):
        self.session.report_enabled = True
        ev = self.session.on_out_of_game(screen("result"))
        self.assertEqual(ev.kind, "game_over")
        self.assertTrue(self.session.wants_table)

    def columns(self):
        return [
            column(1, name="甲", muted=True, stats={"kills": 3, "damage_dealt": 900}, best=["damage_dealt"],
                   coins_all=12, chips=("书签",), chips_unknown=0,
                   badges=(("大荒星陨", 3),)),
            column(2, name="乙", muted=False, bot=False, stats={"kills": 1}),
        ]


class WaitingTest(TableCase):
    def test_off_switch_no_wait(self):
        self.session.report_enabled = False
        self.session.on_out_of_game(screen("result"))
        self.assertFalse(self.session.wants_table)

    def test_result_starts_wait(self):
        self.end_by_result()

    def test_other_screen_does_not_start_wait(self):
        self.session.report_enabled = True
        self.session.on_out_of_game(screen("team_room"))
        self.clock[0] += 1.5
        self.session.on_out_of_game(screen("team_room"))
        self.assertTrue(self.session.game.game_over)
        self.assertFalse(self.session.wants_table)

    def test_reward_and_menu_stop_wait(self):
        for kind in ("reward", "main_menu", "match_found"):
            with self.subTest(kind=kind):
                self.setUp()
                self.end_by_result()
                self.session.on_out_of_game(screen(kind))
                self.assertFalse(self.session.wants_table)

    def test_new_game_stops_wait(self):
        self.end_by_result()
        self.session.new_game()
        self.assertFalse(self.session.wants_table)

    def test_select_screen_stops_wait(self):
        self.end_by_result()
        self.session.on_select_screen()
        self.assertFalse(self.session.wants_table)

    def test_expires_after_table_wait(self):
        self.end_by_result()
        self.clock[0] += TABLE_WAIT_S
        self.assertFalse(self.session.wants_table)
        self.assertIsNone(self.session.on_result_table(reading(self.columns())))


class EndedByScreenThenResultTest(TableCase):
    """对局里读不到轮次的画面被连着两次认成房间、先把这一局结束了，之后才出来真的结算画面：也要等表。"""

    def end_by_room_twice(self):
        self.session.report_enabled = True
        self.assertIsNone(self.session.on_out_of_game(screen("team_room")))
        self.clock[0] += OUT_CONFIRM_GAP_S + 0.5
        ev = self.session.on_out_of_game(screen("team_room"))
        self.assertEqual(ev.kind, "game_over")
        self.assertTrue(self.session.game._ended_by_screen)
        self.assertFalse(self.session.wants_table)

    def test_late_result_starts_wait(self):
        self.end_by_room_twice()
        self.assertIsNone(self.session.on_out_of_game(screen("result")))
        self.assertTrue(self.session.wants_table)
        ev = self.session.on_result_table(reading(self.columns()))
        self.assertEqual(ev.kind, "table")
        self.assertFalse(self.session.wants_table)

    def test_late_result_does_not_restart_timer(self):
        self.end_by_room_twice()
        self.session.on_out_of_game(screen("result"))
        self.clock[0] += TABLE_WAIT_S - 1
        self.session.on_out_of_game(screen("result"))  # 停在结算画面上：这一支只走一次
        self.assertTrue(self.session.wants_table)
        self.clock[0] += 2
        self.assertFalse(self.session.wants_table)

    def test_off_switch_no_wait_after_room_end(self):
        self.end_by_room_twice()
        self.session.report_enabled = False
        self.session.on_out_of_game(screen("result"))
        self.assertFalse(self.session.wants_table)


class AcceptTest(TableCase):
    def sparse(self, known=1, offset=0, consistent=False):
        """只读到 known 个数的一份表（一栏）：五项数字、两个星币数里其余的是 None。offset 让数不一样。"""
        keys = ("kills", "downs", "damage_dealt", "damage_taken", "heal")
        stats = {k: (i + 1 + offset if i < known else None) for i, k in enumerate(keys)}
        return reading([column(1, stats=stats,
                               coins_all=1 + offset if known > 5 else None,
                               coins_transferred=2 + offset if known > 6 else None)],
                       consistent=consistent)

    def test_consistent_immediately(self):
        self.end_by_result()
        ev = self.session.on_result_table(reading(self.columns()))
        self.assertEqual(ev.kind, "table")
        self.assertFalse(self.session.wants_table)
        self.assertIsNone(self.session.on_result_table(reading(self.columns())))  # 认定以后不再记

    def test_inconsistent_needs_identical_twice(self):
        self.end_by_result()
        first = reading(self.columns(), consistent=False)
        self.assertIsNone(self.session.on_result_table(first))
        self.assertIsNotNone(self.session.on_result_table(reading(self.columns(), consistent=False)))
        self.assertTrue(self.session.wants_table)  # 记下了，但接着等更全的一次
        # 两次不一样：换成新的那次再等，再读到一样才记
        self.setUp()
        self.end_by_result()
        a = reading(self.columns(), consistent=False)
        b = reading([column(1, name="甲", muted=True, stats={"kills": 9}), column(2, name="乙", muted=False)],
                    consistent=False)
        self.assertIsNone(self.session.on_result_table(a))
        self.assertIsNone(self.session.on_result_table(b))
        self.assertIsNotNone(self.session.on_result_table(b))

    def test_fade_in_records_partial_then_complete(self):
        """淡入时读到不完整的一份：先记一条，但接着等；之后读到 consistent 为真的再记一条、结束等表。"""
        self.end_by_result()
        fade = self.sparse(1)
        self.assertIsNone(self.session.on_result_table(fade))
        ev = self.session.on_result_table(fade)
        self.assertEqual(ev.kind, "table")
        self.assertTrue(self.session.wants_table)
        ev = self.session.on_result_table(reading(self.columns()))
        self.assertEqual(ev.kind, "table")
        self.assertFalse(self.session.wants_table)
        self.assertIsNone(self.session.on_result_table(reading(self.columns())))

    def test_more_numbers_record_again(self):
        """记过不完整的一份以后：两次读到一样、数更多才再记，接着等。"""
        self.end_by_result()
        small = self.sparse(1)
        self.assertIsNone(self.session.on_result_table(small))
        self.assertIsNotNone(self.session.on_result_table(small))
        more = self.sparse(3)
        self.assertIsNone(self.session.on_result_table(more))
        self.assertIsNotNone(self.session.on_result_table(more))
        self.assertTrue(self.session.wants_table)

    def test_settled_without_more_numbers_stops_waiting(self):
        """记过不完整的一份以后，又两次读到一样、数一样多的一份（数不同也算）：不记，结束等表；之后读到更全的、
        consistent 为真的也不再记。"""
        self.end_by_result()
        small = self.sparse(3)
        self.assertIsNone(self.session.on_result_table(small))
        self.assertIsNotNone(self.session.on_result_table(small))
        same = self.sparse(3, offset=10)
        self.assertIsNone(self.session.on_result_table(same))
        self.assertIsNone(self.session.on_result_table(same))
        self.assertFalse(self.session.wants_table)
        for later in (self.sparse(5), reading(self.columns())):
            self.assertIsNone(self.session.on_result_table(later))
            self.assertIsNone(self.session.on_result_table(later))

    def test_fewer_numbers_stop_waiting(self):
        """记过不完整的一份以后，又两次读到一样、数更少的一份：不记，结束等表。"""
        self.end_by_result()
        more = self.sparse(3)
        self.assertIsNone(self.session.on_result_table(more))
        self.assertIsNotNone(self.session.on_result_table(more))
        fewer = self.sparse(2)
        self.assertIsNone(self.session.on_result_table(fewer))
        self.assertTrue(self.session.wants_table)  # 只读到一次，还没读稳
        self.assertIsNone(self.session.on_result_table(fewer))
        self.assertFalse(self.session.wants_table)

    def test_consistent_record_stops_recording(self):
        """consistent 为真记下以后，再读到什么都不记。"""
        self.end_by_result()
        self.assertIsNotNone(self.session.on_result_table(reading(self.columns())))
        self.assertIsNone(self.session.on_result_table(reading(self.columns(), consistent=False)))
        self.assertIsNone(self.session.on_result_table(self.sparse(1)))

    def test_empty_readings_do_not_count(self):
        """五项数字一个都没读出来的（过渡帧等）不算读到：两次都是空的也不认定，之后读到的真表照样记。"""
        self.end_by_result()
        empty = [column(1, stats={k: None for k in ("kills", "downs", "damage_dealt", "damage_taken", "heal")},
                        coins_all=None, coins_transferred=None),
                 column(2, stats={k: None for k in ("kills", "downs", "damage_dealt", "damage_taken", "heal")},
                        coins_all=None, coins_transferred=None)]
        self.assertIsNone(self.session.on_result_table(reading(empty, consistent=False)))
        self.assertIsNone(self.session.on_result_table(reading(empty, consistent=False)))
        self.assertTrue(self.session.wants_table)
        ev = self.session.on_result_table(reading(self.columns()))
        self.assertEqual(ev.kind, "table")

    def test_record_has_no_name_or_level(self):
        self.end_by_result()
        secret = "秘密名字"
        cols = [column(1, name=secret, muted=True, stats={"kills": 2}, coins_all=7, coins_transferred=1,
                       chips=("书签",), chips_unknown=0, badges=(("死神", None),)),
                column(2, name="乙", muted=False, bot=True)]
        ev = self.session.on_result_table(reading(cols))
        text = json.dumps(ev.record, ensure_ascii=False)
        self.assertNotIn(secret, text)
        self.assertNotIn("Lv", text)
        self.assertNotIn("name", text)
        self.assertEqual((ev.record["map"], ev.record["rounds"]), ("异变图书馆", 10))


class SelfTest(TableCase):
    def seat_self(self, cols, player_name=None, own_row=None):
        self.setUp()  # 每次从干净的一局开始（同一局只认定一次表）
        s = self.session
        s.player_name, s.game.own_row = player_name, own_row
        self.end_by_result()
        ev = s.on_result_table(reading(cols))
        return [p["self"] for p in ev.record["players"]]

    def test_by_name_with_whitespace_and_width(self):
        cols = [column(1, name="甲", muted=True), column(2, name="Ｂ 乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="B乙"), [False, True])

    def test_by_seat(self):
        cols = [column(1, muted=True), column(2, muted=True)]
        self.assertEqual(self.seat_self(cols, own_row=1), [False, True])

    def test_conflicting_clues_all_null(self):
        cols = [column(1, name="甲", muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="甲", own_row=1), [None, None])

    def test_muted_false_excluded(self):
        cols = [column(1, name="甲", muted=False), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="甲"), [None, None])
        self.assertEqual(self.seat_self(cols, player_name="乙"), [False, True])

    def test_hero_and_bot_by_seat(self):
        s = self.session
        s.game.own_row = 0
        s.game.teammates = [{"row": 1, "hero": "芬妮", "bot": False}, {"row": 2, "hero": None, "bot": True},
                            {"row": 3, "hero": None, "bot": False}]
        # 第 1、2 行读名字判过是不是电脑；第 3 行没判过（teammates 里照样写着 False），表上也读不出：写 null
        s.game._mate_bots = {1: False, 2: True}
        cols = [column(1, name="甲", muted=True, bot=None), column(2, name="乙", muted=True, bot=None),
                column(3, name="丙", muted=True, bot=None), column(4, muted=True, bot=None)]
        self.end_by_result()
        ev = s.on_result_table(reading(cols))
        got = [(p["hero"], p["bot"]) for p in ev.record["players"]]
        self.assertEqual(got, [("凛", None), ("芬妮", False), (None, True), (None, None)])

    def test_name_compared_by_stem(self):
        """player_name 是玩家列表上名字的主干（stars.name_key：去掉标点、字母转小写、去掉首尾数字）：表上的名字照同样取主干
        才对得上。"""
        from advisor.vision.stars import name_key

        cols = [column(1, name="Ｍｉｋｕ_39", muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name=name_key("Miku_39")), [True, False])
        cols = [column(1, name="甲", muted=True), column(2, name="Kai·Ren2", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name=name_key("kai ren")), [False, True])

    def test_table_bot_wins_over_player_list(self):
        s = self.session
        s.game.teammates = [{"row": 1, "hero": "芬妮", "bot": False}]
        cols = [column(1, name="甲", muted=True), column(2, name="【Bot1】", muted=True, bot=True)]
        self.end_by_result()
        ev = s.on_result_table(reading(cols))
        self.assertEqual([p["bot"] for p in ev.record["players"]], [None, True])

    def test_name_tolerates_one_wrong_char(self):
        """表上读到的名字错一个字（相似度仍不低于 0.8）也算自己的名字，不再落到座位那一条。"""
        cols = [column(1, name="冒险者甲丙", muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="冒险者甲乙"), [True, False])

    def test_seat_column_with_other_name_is_null(self):
        """名字一栏都没对上，座位那一栏读出来的名字又明确不是自己：座位多半认错了，不认。"""
        cols = [column(1, name="甲", muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="丙", own_row=1), [None, None])

    def test_named_column_is_bot(self):
        cols = [column(1, name="甲", muted=True, bot=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="甲"), [None, None])

    def test_seat_column_without_name_is_trusted(self):
        cols = [column(1, muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, player_name="丙", own_row=0), [True, False])

    def test_seat_trusted_without_own_name(self):
        cols = [column(1, name="甲", muted=True), column(2, name="乙", muted=True)]
        self.assertEqual(self.seat_self(cols, own_row=0), [True, False])


class FakeRecordingSession:
    """Reporting 用的 Session：设置对象、开关和存了几次。"""

    def __init__(self, settings=None):
        self.settings = settings or UiSettings()
        self.report_enabled = False
        self.saved = 0

    def save(self, **_kwargs):
        self.saved += 1


class ReportingTableTest(unittest.TestCase):
    def setUp(self):
        self.session = FakeRecordingSession()
        self.report = reporting.Reporting(
            session=self.session, lock=threading.Lock(), run_background=lambda fn: fn(),
            later=lambda ms, fn: None, delay_ms=0, frozen=lambda: False)
        self.report.enabled = lambda: True
        self.report.assemble = mock.MagicMock()
        self.report.ensure()
        self.assertTrue(self.session.report_enabled)

    def test_table_read_reassembles_finished_game(self):
        self.report.table_read("g1")
        self.report.assemble.assert_called_once_with("g1")

    def test_table_read_skips_waiting_game(self):
        self.report._waiting.add("g1")
        self.report.table_read("g1")
        self.report.assemble.assert_not_called()

    def test_set_enabled_updates_session(self):
        self.report.set_enabled(False)
        self.assertFalse(self.session.report_enabled)


class LogbookTableTest(unittest.TestCase):
    def setUp(self):
        self.logs = sandbox.temp_dir(self, "logbook-table-")
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.clock = datetime(2026, 10, 10, 20, 0, 0)
        now = mock.patch.object(logbook, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def records(self):
        return list(logbook.iter_records())

    def payload(self):
        return {"map": "异变图书馆", "difficulty": "噩梦", "rounds": 10,
                "players": [{"seat": 1, "self": True, "kills": 3, "best": ["damage_dealt"]}]}

    def test_record_fields(self):
        game = logbook.new_game()
        self.clock = self.clock.replace(minute=1)
        self.assertEqual(logbook.record_table(**self.payload()), game)
        rec = self.records()[-1]
        self.assertEqual((rec["t"], rec["game"], rec["rounds"]), ("table", game, 10))
        self.assertEqual(rec["players"][0]["kills"], 3)
        self.assertNotIn("lane", rec)

    def test_lane_field(self):
        line = logbook.lane("taptap")
        line.new_game()
        line.record_table(**self.payload())
        rec = self.records()[-1]
        self.assertEqual((rec["t"], rec["lane"]), ("table", "taptap"))

    def test_does_not_affect_resume(self):
        game = logbook.new_game()
        logbook.record_table(**self.payload())
        self.assertNotIn("table", logbook._LAST_KINDS)
        logbook._reset_for_tests()
        self.assertEqual(logbook.resumable(), game)


class PipelineTableTest(TableCase):
    def setUp(self):
        super().setUp()
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.proc.hints = SimpleNamespace(wants_table=False)
        self.frame = Frame(np.zeros((120, 200, 3), np.uint8), (0, 0))

    def test_not_waiting_calls_nothing(self):
        with mock.patch.object(pipeline.result_table, "looks_like_table") as looks, \
                mock.patch.object(pipeline.result_table, "read_table") as read:
            self.proc._read_table(self.frame, 0.0, [])
        looks.assert_not_called()
        read.assert_not_called()

    def test_waiting_looks_then_reads_only_if_like(self):
        self.proc.hints = SimpleNamespace(wants_table=True)
        with mock.patch.object(pipeline.result_table, "looks_like_table", return_value=False) as looks, \
                mock.patch.object(pipeline.result_table, "read_table") as read:
            self.proc._read_table(self.frame, 0.0, [])
        looks.assert_called_once()
        read.assert_not_called()

    def test_reading_goes_to_session(self):
        self.end_by_result()
        self.proc.hints = SimpleNamespace(wants_table=True)
        got = reading(self.columns())
        events = []
        with mock.patch.object(pipeline.result_table, "looks_like_table", return_value=True), \
                mock.patch.object(pipeline.result_table, "read_table", return_value=got):
            self.proc._read_table(self.frame, 0.0, events)
        self.assertEqual([e.kind for e in events], ["table"])
        self.assertEqual(events[0].record["rounds"], 10)
        traced = [r for r in self.session.recent_events() if r.get("kind") == "read:table"]
        self.assertTrue(traced)
        self.assertNotIn("甲", json.dumps(traced, ensure_ascii=False))

    def test_read_error_reported_once_per_wait(self):
        """读表出错：这一帧照常往下走，同一段等表里只报第一次（不每帧刷错误记录）；下一局等表时再出错照样报。"""
        self.proc.hints = SimpleNamespace(wants_table=True, games=3)
        with mock.patch.object(pipeline.result_table, "looks_like_table", return_value=True), \
                mock.patch.object(pipeline.result_table, "read_table", side_effect=ValueError("坏了")):
            for i in range(3):
                self.proc.errors = []
                self.proc._safe("table", self.proc._read_table, self.frame, 0.0, [])
                self.assertEqual(len(self.proc.errors), 1 if i == 0 else 0)
            self.proc.hints = SimpleNamespace(wants_table=True, games=4)
            self.proc.errors = []
            self.proc._safe("table", self.proc._read_table, self.frame, 0.0, [])
            self.assertEqual([name for name, _, _ in self.proc.errors], ["table"])


class Recorder:
    def __init__(self):
        self.games = []

    def table_read(self, game):
        self.games.append(game)


class OverlayTableTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.logbook = tar = SimpleNamespace()
        tar.tables = []
        tar.game = "g1"
        tar.current_game = lambda: tar.game
        tar.record_table = lambda **record: tar.tables.append(record)
        self.session = OverlaySession(self.db, UiSettings(hero="凛"), clock=lambda: 1000.0)
        self.session.game.seen_hero = "凛"
        self.session.settings.round = 3
        self.session.report_enabled = True
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=tar))
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)
        self.recorder = Recorder()
        self.w.reporting = self.recorder

    def test_table_logged_and_report_rebuilt(self):
        self.session.on_out_of_game(screen("result"))
        ev = self.session.on_result_table(reading([column(1, name="甲", muted=True)]))
        self.w.handle(ev)
        self.assertEqual(len(self.logbook.tables), 1)
        self.assertEqual(self.logbook.tables[0]["rounds"], 10)
        self.assertEqual(self.recorder.games, ["g1"])

    def test_second_window_forwards_to_primary(self):
        primary = SimpleNamespace(reporting=Recorder())
        self.w.primary = primary
        try:
            self.w.report_table("g5")
        finally:
            self.w.primary = None  # 收尾刷新读的是第一张的部件，别留一个假的
        self.assertEqual(primary.reporting.games, ["g5"])


if __name__ == "__main__":
    unittest.main()
