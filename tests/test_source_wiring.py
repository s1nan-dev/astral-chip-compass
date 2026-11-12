"""三选一来源接进各处的接线：识别流程读升星横幅、购买窗交给 Session；推荐日志记下来源依据和补准的结果；
对局报告带上；悬浮窗把补准的来源交给推荐日志；悬浮窗「来源」那一格和推荐全文按依据写来源。

写法照 tests/test_victory.py 里测 `_check_victory` 的那一组：Session 是真的，画面识别换成假的。
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

import numpy as np

from advisor import game_report, logbook, paths, perfstats
from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.session.selection import SOURCE_BY, SOURCE_LABEL
from advisor.ui import services, text
from advisor.ui.overlay_parts import advice_card, records
from advisor.vision import pipeline, source_cues
from advisor.vision.frame import Frame
from tests.support import sandbox
from tests.support.fakes import FakeLogbook
from tests.test_session import OFFER_A, chips, close, own, ready

BLANK = np.full((1080, 1920, 3), 90, np.uint8)
OFFER_TWO = ["大背包", "标靶", "缓冲盾牌"]  # 2 星时的概率行、最左格是紫色
TWO = (40, 45, 15)
ONE = (60, 37, 3)  # 0、1 星时升星和筹码商店的概率行（困难及以上）


class PipelineTest(unittest.TestCase):
    """识别流程里的 _check_source_cues：glance、read_rank、read_purchase 换成假的，Session 是真的。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.clock = [100.0]
        self.session = Session(self.db, Settings(hero="芬妮", round=3, star=1, star_read=True))
        self.session.game.seen_hero = "芬妮"
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.banner = False
        self.shop = False
        self.rank = 2
        self.purchase = None
        self.rank_calls = 0
        self.purchase_calls = 0

        def fake_rank(img):
            self.rank_calls += 1
            return self.rank

        def fake_purchase(img):
            self.purchase_calls += 1
            return self.purchase

        patches = [
            mock.patch.object(source_cues, "glance",
                              side_effect=lambda img: source_cues.Glance(self.banner, self.shop)),
            mock.patch.object(source_cues, "read_rank", side_effect=fake_rank),
            mock.patch.object(source_cues, "read_purchase", side_effect=fake_purchase),
            mock.patch.object(self.session, "on_levelup_banner",
                              wraps=self.session.on_levelup_banner),
            mock.patch.object(self.session, "on_shop_window", wraps=self.session.on_shop_window),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def check(self, now: float, yielding: bool = False) -> list:
        """处理 now 时刻的一帧（直接调那一处，不跑整帧识别）。"""
        events: list = []
        self.clock[0] = now
        self.proc.pending = False
        self.proc.hints = None
        with mock.patch.object(self.proc, "_yielding", return_value=yielding):
            self.proc._check_source_cues(Frame(BLANK), now, events)
        return events

    def trace(self, kind: str) -> list:
        return [r for r in self.session.recent_events(60) if r["kind"] == kind]

    def test_banner_delivered_every_frame(self):
        """横幅认出名次：认字只做一次，这一段之后的每一帧都再交给 Session（三选一出现时收得下最后一次）。"""
        self.banner, self.rank = True, 2
        self.check(100.0)
        self.check(100.5)
        self.check(101.0)
        self.assertEqual(self.rank_calls, 1)
        self.assertEqual([call.args for call in self.session.on_levelup_banner.call_args_list],
                         [(2,), (2,), (2,)])
        self.assertFalse(self.proc.pending)

    def test_long_shop_segment(self):
        """长的一段购买窗：认字一次，每帧都交；三选一出现时 Session 收得下来源是筹码商店。"""
        self.shop, self.purchase = True, source_cues.Purchase(20)
        for i in range(30):
            self.check(100.0 + i * 0.5)
        self.assertEqual(self.purchase_calls, 1)
        self.assertEqual(self.session.on_shop_window.call_count, 30)
        ev = self.session.on_frame(chips(OFFER_TWO, probabilities=TWO))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual((ev.state.source, ev.source_by), ("shop", "shop_window"))

    def test_shop_price_none_delivered(self):
        """价格读不出（Purchase.price 为 None）也算认出来了，照交。"""
        self.shop, self.purchase = True, source_cues.Purchase(None)
        self.check(100.0)
        self.check(100.5)
        self.assertEqual(self.purchase_calls, 1)
        self.assertEqual(self.session.on_shop_window.call_args.args, (None,))

    def test_not_in_game_or_over(self):
        """没有对局状态、本局已结束时都不看。"""
        self.banner = True
        fresh = Session(self.db, Settings(hero="芬妮"))
        proc = pipeline.FrameProcessor(self.db, fresh)
        proc.hints = None
        events: list = []
        proc._check_source_cues(Frame(BLANK), 100.0, events)
        self.assertEqual(self.rank_calls, 0)

        self.session.game.game_over = True
        self.proc._banner_run = {"since": 1.0, "last": 1.0, "tries": 0, "found": False, "value": None, "noted": False}
        self.check(100.0)
        self.assertEqual(self.rank_calls, 0)
        self.assertIsNone(self.proc._banner_run)

    def test_same_now_looked_once(self):
        """settle 拿同一幅画面接着处理（时刻相同）：只看一次、只交一次。"""
        self.banner, self.rank = True, 2
        self.check(100.0)
        self.check(100.0)
        self.assertEqual((self.rank_calls, self.session.on_levelup_banner.call_count), (1, 1))

    def test_unread_limits_and_restart(self):
        """一段最多认 BANNER_TRIES 次，试满记一条 cue_unread；glance 说不像以后重新算。"""
        self.banner, self.rank = True, None
        for i in range(source_cues.BANNER_TRIES + 3):
            self.check(100.0 + i * 0.5)
        self.assertEqual(self.rank_calls, source_cues.BANNER_TRIES)
        records_ = self.trace("read:cue_unread")
        self.assertEqual([(r["cue"], r["tries"]) for r in records_],
                         [("banner", source_cues.BANNER_TRIES)])
        self.banner = False
        self.check(110.0)
        self.assertIsNone(self.proc._banner_run)
        self.banner, self.rank = True, 3
        self.check(111.0)
        self.assertEqual(self.rank_calls, source_cues.BANNER_TRIES + 1)
        self.assertEqual(self.session.on_levelup_banner.call_args.args, (3,))

    def test_run_s_stops_reading(self):
        """一段超过 RUN_S 不再认字；已经认出来的照交。相邻两帧都不到 SEGMENT_GAP_S，一直算同一段。"""
        with mock.patch.object(source_cues, "BANNER_TRIES", 10):
            self.banner, self.rank = True, None
            for now in (100.0, 102.5, 105.0, 106.5, 108.0):
                self.check(now)
            self.assertEqual(self.rank_calls, 3)
        self.banner = False
        self.check(109.0)  # 这一段结束
        self.banner, self.rank = True, 4
        self.check(200.0)  # 新的一段：认出来了
        self.assertEqual(self.session.on_levelup_banner.call_args.args, (4,))
        for i in range(1, 21):
            self.check(200.0 + i * 0.5)  # 到 210 秒，这一段早已超过 RUN_S：不再认字，但照交
        self.assertEqual(self.rank_calls, 4)
        self.assertEqual(self.session.on_levelup_banner.call_count, 21)

    def test_yielding_skips_reading_but_delivers(self):
        """两开让路时不认字、不算次数；已经认出来的照样交。"""
        self.banner, self.rank = True, None
        for i in range(3):
            self.check(100.0 + i, yielding=True)
        self.assertEqual(self.rank_calls, 0)
        self.check(103.0)
        self.assertEqual(self.rank_calls, 1)
        self.rank = 2
        self.check(103.5)  # 认出来了：交给 Session 一次
        self.check(104.0, yielding=True)  # 让路时已经认出来的照样交
        self.assertEqual(self.session.on_levelup_banner.call_count, 2)

    def test_select_frame_clears_runs(self):
        """三选一的帧把两段状态都清掉：又一段横幅照样认字，交的是新名次。"""
        self.banner, self.rank = True, 2
        self.check(100.0)
        seen = chips(OFFER_TWO, probabilities=TWO)
        self.clock[0] = 101.0
        with mock.patch.object(self.proc, "_read_select", return_value=seen), \
                mock.patch.object(self.proc, "_select_reads"), \
                mock.patch.object(self.proc, "_note_unread"), \
                mock.patch.object(self.proc, "_note_picked"), \
                mock.patch.object(self.proc, "_watch_frame", return_value=((), ())), \
                mock.patch.object(self.proc, "_check_window"), \
                mock.patch.object(self.proc, "_menu_idle", return_value=False), \
                mock.patch.object(self.proc, "_read_panel"), \
                mock.patch.object(self.proc, "_task_panel_opened", return_value=False), \
                mock.patch.object(self.proc, "_idle_reads"), \
                mock.patch.object(self.proc, "_count_quiet"), \
                mock.patch.object(self.proc, "_finish"):
            self.proc.process(BLANK, 101.0)
        self.assertIsNone(self.proc._banner_run)
        self.assertIsNone(self.proc._shop_run)
        self.banner, self.rank = True, 3
        self.check(102.0)
        self.assertEqual(self.rank_calls, 2)
        self.assertEqual(self.session.on_levelup_banner.call_args.args, (3,))

    def test_reads_timed(self):
        """认字记了耗时：perfstats 的 read:levelup、read:purchase。"""
        perfstats.take_period()  # 清掉之前留下的
        self.banner, self.rank = True, 2
        self.shop, self.purchase = True, source_cues.Purchase(10)
        self.check(100.0)
        reads = perfstats.take_period().get("reads", {})
        self.assertEqual((reads["levelup"]["n"], reads["purchase"]["n"]), (1, 1))

    def test_read_error_counts_as_try(self):
        """认字出错（经 _safe，和 process 里一样接住）也算认过一次：一直出错的一段同样最多认 BANNER_TRIES 次、只报这几次错，
        试满记一条 cue_unread；这一段的状态留着，不会每帧都当成刚开始的一段再认。"""
        def broken(img):
            self.rank_calls += 1
            raise RuntimeError("认字出错")

        source_cues.read_rank.side_effect = broken
        self.banner = True
        for i in range(source_cues.BANNER_TRIES + 3):
            now = 100.0 + i * 0.5
            self.clock[0] = now
            self.proc.hints = None
            with mock.patch.object(self.proc, "_yielding", return_value=False):
                self.proc._safe("source", self.proc._check_source_cues, Frame(BLANK), now, [])
        self.assertEqual(self.rank_calls, source_cues.BANNER_TRIES)
        self.assertEqual([name for name, _, _ in self.proc.errors], ["source"] * source_cues.BANNER_TRIES)
        self.assertEqual(self.proc._banner_run["since"], 100.0)
        self.assertEqual([(r["cue"], r["tries"]) for r in self.trace("read:cue_unread")],
                         [("banner", source_cues.BANNER_TRIES)])

    def test_resumed_restarts_segments(self):
        """从暂停恢复：两段画面都从头算。暂停前认出了名次 2，恢复后看到的横幅照样认字，交的是新认出来的 3。"""
        self.banner, self.rank = True, 2
        self.shop, self.purchase = False, None
        self.check(100.0)
        self.proc._shop_run = {"since": 100.0, "last": 100.0, "tries": 0, "found": True, "value": 10, "noted": False}
        self.proc.resumed()
        self.assertEqual((self.proc._banner_run, self.proc._shop_run), (None, None))
        self.rank = 3
        self.check(101.0)
        self.assertEqual(self.rank_calls, 2)
        self.assertEqual(self.session.on_levelup_banner.call_args.args, (3,))

    def test_long_gap_restarts_segment(self):
        """相邻两帧隔了 SEGMENT_GAP_S 以上（手动模式两次按热键之间、电脑卡住）：前后都说像也重新起一段、重新认字。认不出来时
        不交前一次认出的值，免得把另一个横幅、另一个购买窗当成前一个。"""
        gap = source_cues.SEGMENT_GAP_S + 0.5
        self.banner, self.rank = True, 2
        self.shop, self.purchase = True, source_cues.Purchase(20)
        self.check(100.0)
        self.check(100.5)
        self.assertEqual((self.rank_calls, self.purchase_calls), (1, 1))
        self.rank, self.purchase = 3, source_cues.Purchase(30)
        self.check(100.5 + gap)  # 隔久了：重新认，交的是新认出来的
        self.assertEqual((self.rank_calls, self.purchase_calls), (2, 2))
        self.assertEqual([call.args for call in self.session.on_levelup_banner.call_args_list], [(2,), (2,), (3,)])
        self.assertEqual([call.args for call in self.session.on_shop_window.call_args_list], [(20,), (20,), (30,)])
        self.rank, self.purchase = None, None
        self.check(100.5 + 2 * gap)  # 又隔久了、这次认不出来：两样都不再交
        self.assertEqual((self.rank_calls, self.purchase_calls), (3, 3))
        self.assertEqual(self.session.on_levelup_banner.call_count, 3)
        self.assertEqual(self.session.on_shop_window.call_count, 3)


# ------------------------------------------------------------ 推荐日志

def state(**changes) -> dict:
    base = {"hero": "芬妮", "star": 1, "round": 2, "map": "森秋幻境", "owned": ["甲"], "source": "levelup"}
    base.update(changes)
    return base


def advice(**changes) -> dict:
    base = {"evals": [{"chip": {"name": "甲"}}], "best": "甲"}
    base.update(changes)
    return base


def offer(offer_id="o1", when="2026-01-01T10:00:00", game="g1", **changes) -> dict:
    record = {"t": "offer", "time": when, "offer_id": offer_id, "game": game, "state": state(), "advice": advice()}
    record.update(changes)
    return record


def source_record(offer_id="o1", source="shop", by="stars", when="2026-01-01T10:00:10", **changes) -> dict:
    record = {"t": "source", "time": when, "offer_id": offer_id, "source": source, "by": by}
    record.update(changes)
    return record


class LogbookTest(unittest.TestCase):
    def setUp(self):
        self.logs = sandbox.temp_dir(self, "source-wiring-")
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.clock = datetime(2026, 10, 10, 20, 0, 0)
        now = mock.patch.object(logbook, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def records(self) -> list[dict]:
        return list(logbook.iter_records())

    def test_source_by_written_only_when_known(self):
        """offer 记录顶层的 source_by：在白名单里才写；没给、值不认识时不写。"""
        for by in ("banner", "stars", "别的", None):
            logbook.record_offer(state(), advice(), f"o-{by}", source_by=by)
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual([r.get("source_by") for r in offers], ["banner", "stars", None, None])
        self.assertNotIn("source_by", offers[2])
        self.assertNotIn("source_by", offers[3])

    def test_record_source(self):
        """source 记录：写出一条，别的线另带 lane、编号加前缀；不认识的值不写。"""
        logbook.record_source("o-1", "shop", "stars")
        logbook.record_source("o-2", "别的", "stars")
        logbook.record_source("o-3", "shop", "别的")
        line = logbook.lane("taptap")
        line.record_source("o-4", "levelup", "banner")
        sources = [r for r in self.records() if r["t"] == "source"]
        self.assertEqual([(r["offer_id"], r["source"], r["by"]) for r in sources],
                         [("o-1", "shop", "stars"), ("taptap-o-4", "levelup", "banner")])
        self.assertEqual(sources[1]["lane"], "taptap")

    def test_source_does_not_change_resume(self):
        """最后一条是 source 记录时，重启后接哪一局和没有它时一样。"""
        def resume(with_source: bool) -> str | None:
            logbook._reset_for_tests()
            for path in self.logs.glob("*.jsonl"):
                path.unlink()
            logbook.new_game("g1")
            logbook.record_offer(state(), advice(), "o1")
            if with_source:
                logbook.record_source("o1", "shop", "stars")
            logbook._reset_for_tests()
            return logbook.resumable()

        self.assertEqual(resume(False), "g1")
        self.assertEqual(resume(True), "g1")


# ------------------------------------------------------------ 对局报告

class ReportTest(unittest.TestCase):
    def test_source_record_overrides(self):
        """source 记录改掉报告里那次三选一的来源、依据；同一个编号取最后一条。"""
        records = [offer(source_by="guess"), source_record("o1", "shop", "stars")]
        report = game_report.build(records, "g1", install="a" * 32, since=datetime(2025, 1, 1))
        self.assertEqual((report["offers"][0]["state"]["source"], report["offers"][0]["state"]["source_by"]),
                         ("shop", "stars"))
        records = [offer(source_by="guess"), source_record("o1", "shop", "stars"),
                   source_record("o1", "levelup", "banner", when="2026-01-01T10:00:20")]
        report = game_report.build(records, "g1", install="a" * 32, since=datetime(2025, 1, 1))
        self.assertEqual((report["offers"][0]["state"]["source"], report["offers"][0]["state"]["source_by"]),
                         ("levelup", "banner"))

    def test_source_record_needs_both_whitelisted(self):
        """source 记录里两项有一个不认识时不改，照 offer 记录顶层的写。"""
        for source, by in (("别的", "stars"), ("shop", "别的")):
            with self.subTest(source=source, by=by):
                records = [offer(source_by="banner"), source_record("o1", source, by)]
                report = game_report.build(records, "g1", install="a" * 32, since=datetime(2025, 1, 1))
                self.assertEqual((report["offers"][0]["state"]["source"], report["offers"][0]["state"]["source_by"]),
                                 ("levelup", "banner"))

    def test_source_by_without_source_record(self):
        """没有 source 记录时 source_by 照 offer 记录顶层的写；旧记录写 None。"""
        report = game_report.build([offer(source_by="banner")], "g1", install="a" * 32, since=datetime(2025, 1, 1))
        self.assertEqual(report["offers"][0]["state"]["source_by"], "banner")
        report = game_report.build([offer()], "g1", install="a" * 32, since=datetime(2025, 1, 1))
        self.assertEqual(report["offers"][0]["state"]["source_by"], None)
        self.assertIn("source_by", report["offers"][0]["state"])

    def test_whitelists_match_selection(self):
        """推荐日志的两个白名单和 selection 的键一致。"""
        self.assertEqual(logbook.SOURCES, frozenset(SOURCE_LABEL))
        self.assertEqual(logbook.SOURCE_BYS, frozenset(SOURCE_BY))


# ------------------------------------------------------------ 悬浮窗的推荐日志

class Settled:
    """记下补准来源的假推荐日志：只有 record_source，其余照 FakeLogbook。"""

    def __init__(self):
        self.sources: list = []

    def record_source(self, *args):
        self.sources.append(args)


class OfferLogTest(unittest.TestCase):
    def setUp(self):
        self.logbook = FakeLogbook()
        self.services = services.Services(logbook=self.logbook)
        self.log = records.OfferLog(lambda: self.services)
        self.eggs = []

    def ev(self, names, rerolls=3, select_no=None, source_by=None):
        evals = [mock.Mock(chip=mock.Mock(name=n)) for n in names]
        ev = mock.Mock(state=mock.Mock(rerolls=rerolls), advice=mock.Mock(evals=evals))
        ev.select_no = select_no
        ev.source_by = source_by
        return ev

    def test_source_by_passed_to_logbook(self):
        """事件带着 source_by 时记进 offer 记录顶层。"""
        self.logbook.record_offer = mock.Mock()
        self.log.on_advice(self.ev(["A", "B", "C"], source_by="banner"), False, lambda *a: None)
        self.assertEqual(self.logbook.record_offer.call_args.kwargs.get("source_by"), "banner")

    def test_source_settled_all_ids(self):
        """同一次三选一刷新过（两个编号）：补准来源时两个都记。"""
        settled = Settled()
        self.services = services.Services(logbook=settled)
        self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=1), False, lambda *a: None)
        first = self.log.offer_id
        self.log.on_advice(self.ev(["D", "E", "F"], 2, select_no=1), True, lambda *a: None)
        second = self.log.offer_id
        self.assertNotEqual(first, second)
        ev = mock.Mock(select_no=1, source="shop", source_by="stars")
        self.log.on_source_settled(ev)
        self.assertEqual(settled.sources, [(first, "shop", "stars"), (second, "shop", "stars")])

    def test_unknown_select_no_does_nothing(self):
        settled = Settled()
        self.services = services.Services(logbook=settled)
        self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=1), False, lambda *a: None)
        self.log.on_source_settled(mock.Mock(select_no=9, source="shop", source_by="stars"))
        self.assertEqual(settled.sources, [])

    def test_missing_record_source_is_fine(self):
        """假的推荐日志没有 record_source 时不出错（FakeLogbook 就没有）。"""
        self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=1), False, lambda *a: None)
        self.log.on_source_settled(mock.Mock(select_no=1, source="shop", source_by="stars"))

    def test_reopened_select_settles_original_id(self):
        """同一组候选关了又出现（撤销了刚记下的选择）：记账给了新的 select_no，悬浮窗沿用原来的编号；按新的 select_no 补准时
        记在原来那个编号上。"""
        settled = Settled()
        self.services = services.Services(logbook=settled)
        self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=1), False, lambda *a: None)
        first = self.log.offer_id
        self.log.on_picked("A")
        self.log.on_reverted("A", False, None, lambda *a: None)
        self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=2), False, lambda *a: None)
        self.assertEqual(self.log.offer_id, first)
        self.log.on_source_settled(mock.Mock(select_no=2, source="levelup", source_by="stars"))
        self.assertEqual(settled.sources, [(first, "levelup", "stars")])

    def test_keeps_recent_selects_only(self):
        """只留最近 SELECT_OFFERS_KEEP 次三选一的编号：更早那一次的补准不再记，最近那一次的照记。"""
        settled = Settled()
        self.services = services.Services(logbook=settled)
        last = records.SELECT_OFFERS_KEEP + 1
        for no in range(1, last + 1):
            self.log.on_advice(self.ev(["A", "B", "C"], 3, select_no=no), False, lambda *a: None)
        self.assertEqual(sorted(self.log.select_offers), list(range(2, last + 1)))
        self.log.on_source_settled(mock.Mock(select_no=1, source="shop", source_by="stars"))
        self.assertEqual(settled.sources, [])
        self.log.on_source_settled(mock.Mock(select_no=last, source="shop", source_by="stars"))
        self.assertEqual(settled.sources, [(self.log.offer_id, "shop", "stars")])


# ------------------------------------------------------------ 接起来走一遍

class WiringTest(unittest.TestCase):
    """真的 Session 发的推荐、补准事件交给悬浮窗的推荐日志（第二张悬浮窗那条线：logbook.lane），推荐日志写进临时文件夹，
    再拼对局报告：编号对得上（推荐事件和补准事件的 select_no、线名前缀），补准的来源改掉报告里那次三选一的来源和依据。"""

    def setUp(self):
        self.logs = sandbox.temp_dir(self, "source-wiring-")
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.session = ready(load_db(), "芬妮")
        self.clock = [100.0]
        self.session._clock = lambda: self.clock[0]
        self.session.note_own(1)

    def test_lane_settled_source_reaches_report(self):
        line = logbook.lane("taptap")
        log = records.OfferLog(lambda: services.Services(logbook=line))
        self.session.on_stars(own(1))
        self.session.on_shop_window(20)
        self.clock[0] += 1.0
        ev = self.session.on_frame(chips(OFFER_A, probabilities=ONE))
        self.assertEqual((ev.kind, ev.state.source, ev.source_by), ("advice", "shop", "shop_window"))
        log.on_advice(ev, False, lambda *a: None)
        close(self.session)
        self.session.on_stars(own(2))  # 星数涨了：补准成升星
        settled = [e for e in self.session.take_events() if e.kind == "source_settled"]
        self.assertEqual([(e.select_no, e.source, e.source_by) for e in settled],
                         [(ev.select_no, "levelup", "stars")])
        log.on_source_settled(settled[0])
        rows = list(logbook.iter_records())
        offers = [r for r in rows if r["t"] == "offer"]
        sources = [r for r in rows if r["t"] == "source"]
        self.assertEqual([(r["lane"], r["offer_id"], r.get("source_by")) for r in offers],
                         [("taptap", f"taptap-{log.offer_id}", "shop_window")])
        self.assertEqual([(r["lane"], r["offer_id"], r["source"], r["by"]) for r in sources],
                         [("taptap", offers[0]["offer_id"], "levelup", "stars")])
        report = game_report.build(rows, offers[0]["game"], install="a" * 32, since=datetime(2025, 1, 1))
        state_ = report["offers"][0]["state"]
        self.assertEqual((state_["source"], state_["source_by"]), ("levelup", "stars"))


# ------------------------------------------------------------ 悬浮窗上的写法

class LabelTest(unittest.TestCase):
    """悬浮窗「来源」那一格（AdviceCard.bits）、推荐全文（text.advice_text）里的来源：画面上的迹象定下了才分开写「升星」
    「筹码商店」，拿不准的照旧写「升星或筹码商店」。"""

    def test_source_text(self):
        cases = [
            (("levelup", "banner"), "升星"), (("levelup", "stars"), "升星"), (("shop", "stars"), "筹码商店"),
            (("shop", "shop_window"), "筹码商店"), (("levelup", "guess"), "升星或筹码商店"),
            (("shop", "guess"), "升星或筹码商店"), (("levelup", None), "升星或筹码商店"),
            (("levelup", ["banner"]), "升星或筹码商店"), (("other", "banner"), "升星或筹码商店"),
            (("task", "rows"), "任务"), (("task", "rows", 2), "任务（2 级）"), (("task", None, None), "任务"),
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(text.source_text(*args), want)
        # 分开写的几种都是 Session 会写的依据；拿不准的、认出任务的不在里面
        self.assertLessEqual(text.SOURCE_SURE, set(SOURCE_BY))
        self.assertTrue(text.SOURCE_SURE.isdisjoint({"guess", "rows"}))

    def advice(self, shop_window: bool):
        """真的 Session 发的一次推荐：1 星、星数没涨；shop_window 时出现之前不久看到了购买窗。"""
        db = load_db()
        session = ready(db, "芬妮")
        clock = [100.0]
        session._clock = lambda: clock[0]
        session.note_own(1)
        session.on_stars(own(1))
        if shop_window:
            session.on_shop_window(20)
        clock[0] += 1.0
        ev = session.on_frame(chips(OFFER_A, probabilities=ONE))
        self.assertEqual(ev.kind, "advice")
        card = advice_card.AdviceCard.__new__(advice_card.AdviceCard)  # bits 只用到改正过的几格和 Session 的设置
        card.session, card.fixed = session, set
        return db, ev, card

    def test_card_and_full_text(self):
        """购买窗定下的：「来源」那一格写「筹码商店」，推荐全文另写依据；拿不准的照旧写「升星或筹码商店」（推荐照旧按升星算）。"""
        db, ev, card = self.advice(shop_window=True)
        self.assertEqual((ev.state.source, ev.source_by), ("shop", "shop_window"))
        self.assertEqual(card.bits(ev.state, ev.advice, ev.source_by)["source"].value, "筹码商店")
        self.assertEqual(card.bits(ev.state, ev.advice)["source"].value, "升星或筹码商店")  # 不知道依据：照旧
        self.assertIn("｜来源 筹码商店｜来源依据 购买窗｜", text.advice_text(db, ev))

        db, ev, card = self.advice(shop_window=False)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "guess"))
        self.assertEqual(card.bits(ev.state, ev.advice, ev.source_by)["source"].value, "升星或筹码商店")
        self.assertIn("｜来源 升星或筹码商店｜来源依据 拿不准｜", text.advice_text(db, ev))


if __name__ == "__main__":
    unittest.main()
