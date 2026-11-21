"""推荐日志分线：一张悬浮窗一条线，各记各的「当前这一局」，互不拆局、互不串号。

默认的那条线（模块级函数，第一张悬浮窗用）写出的记录和没有分线时逐字段相同；别的线（lane(name)，同时识别两个客户端时的
第二张悬浮窗用）写出的每条记录另带 "lane"，三选一编号带「线名-」前缀，选择、反馈照样和它的三选一对得上（校准工具按
offer_id 配对，不用改）。各条线各自推断新的一局、各自在重启后接上自己没打完的那一局；局号全模块不重复；读整个日志的
函数不分线。

推荐日志写进测试沙箱里的临时文件夹，时钟用假的，随机造的日志用固定种子。
"""
from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timedelta
from unittest import mock

from advisor import game_report, logbook, paths
from advisor.diagnostics import _jsonable
from advisor.pool import State
from advisor.ui.services import Services
from tests.support import sandbox
from tools import calibrate


class LaneTestBase(unittest.TestCase):
    def setUp(self):
        self.logs = sandbox.temp_dir(self, "logbook-lanes-")
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.clock = datetime(2026, 10, 10, 20, 0, 0)
        now = mock.patch.object(logbook, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def later(self, seconds: float = 1) -> None:
        self.clock += timedelta(seconds=seconds)

    def records(self) -> list[dict]:
        return list(logbook.iter_records())

    def offer(self, lane, hero: str, rnd: int, owned, offer_id: str, advice=None) -> str:
        self.later()
        return lane.record_offer(State(hero=hero, round=rnd, owned=tuple(owned)),
                                 advice or {"best": owned[-1]}, offer_id)


class DefaultLaneTest(LaneTestBase):
    """模块级函数写出的记录和没有分线时一样：不带 "lane"、编号原样、字段顺序不变。"""

    def test_client_written_only_when_given(self):
        """offer 记录里的 client：给了（cn、int、taptap）才写；没给、给的不认识时不写（旧记录、别处调用照旧）。"""
        game = logbook.new_game()
        state = State(hero="芬妮", round=3, owned=("大背包",))
        self.later()
        self.assertEqual(logbook.record_offer(state, {"best": "大背包"}, "o-1", client="taptap"), game)
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-2", client="别的")
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-3")
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual([r.get("client") for r in offers], ["taptap", None, None])
        self.assertNotIn("client", offers[1])
        self.assertNotIn("client", offers[2])

    def test_map_source_written_only_when_known(self):
        """offer 记录顶层的 map_source：在白名单里才写；没给、值不认识时不写。"""
        state = State(hero="芬妮", round=3, owned=("大背包",))
        for source in ("tasks", "manual", "别的", None):
            self.later()
            logbook.record_offer(state, {"best": "大背包"}, f"o-{source}", map_source=source)
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual([r.get("map_source") for r in offers], ["tasks", "manual", None, None])
        self.assertNotIn("map_source", offers[2])
        self.assertNotIn("map_source", offers[3])

    def test_difficulty_source_written_only_when_known(self):
        """offer 记录顶层的 difficulty_source：在 DIFFICULTY_SOURCES 里才写；没给、值不认识时不写。"""
        state = State(hero="芬妮", round=3, owned=("大背包",))
        for source in ("rows", "cap", "别的", None):
            self.later()
            logbook.record_offer(state, {"best": "大背包"}, f"o-{source}", difficulty_source=source)
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual([r.get("difficulty_source") for r in offers], ["rows", "cap", None, None])
        self.assertNotIn("difficulty_source", offers[2])

    def test_owned_lists_written_only_when_aligned(self):
        """offer 记录顶层 owned_sources、owned_panel：和 state.owned 一样长、每项合格才写；长度不对、值不认识时不写。"""
        state = State(hero="芬妮", round=3, owned=("大背包", "书签"))
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-1",
                             owned_sources=["pick", None], owned_panel=[True, False])
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-2",
                             owned_sources=["pick"], owned_panel=[True, False])  # 来路长度不对：只写面板记号
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-3",
                             owned_sources=["pick", "别的"], owned_panel=[True, "是"])  # 值不对：那一样不写
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual((offers[0]["owned_sources"], offers[0]["owned_panel"]), (["pick", None], [True, False]))
        self.assertNotIn("owned_sources", offers[1])
        self.assertIn("owned_panel", offers[1])  # 两样各看各的：面板记号合格就写
        self.assertNotIn("owned_sources", offers[2])
        self.assertNotIn("owned_panel", offers[2])

    def test_teammate_guesses_written_only_when_aligned(self):
        """offer 记录顶层 teammate_guesses：和 state.teammates 一样长、至少有一项没认出的队友才写；guess 不是文字时两个数
        也写 null，数不合格的写 null，不是对象的项写 null。没给、长度不对、全是认出了的队友时不写。"""
        state = State(hero="芬妮", round=3, teammates=({"hero": "米米", "bot": True}, {"hero": None, "bot": False}))
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-1",
                             teammate_guesses=[None, {"guess": "恋", "score": 0.834, "margin": 0.021}])
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-2",
                             teammate_guesses=["别的", {"guess": "", "score": 0.5, "margin": float("nan")}])
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-3", teammate_guesses=[None])  # 长度不对
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-4", teammate_guesses=[None, None])  # 没有没认出的
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-5")
        self.later()
        logbook.record_offer(state, {"best": "大背包"}, "o-6",
                             teammate_guesses=[None, {"guess": "恋", "score": True, "margin": "0.1"}])
        offers = [r for r in self.records() if r["t"] == "offer"]
        self.assertEqual(offers[0]["teammate_guesses"], [None, {"guess": "恋", "score": 0.834, "margin": 0.021}])
        self.assertEqual(offers[1]["teammate_guesses"], [None, {"guess": None, "score": None, "margin": None}])
        for record in offers[2:5]:
            self.assertNotIn("teammate_guesses", record)
        self.assertEqual(offers[5]["teammate_guesses"], [None, {"guess": "恋", "score": None, "margin": None}])

    def test_unadvised_cleaned(self):
        """unadvised 记录写之前清理：reasons 只留那三种、不重复；round、star 不是非负整数写 null；advised 不是布尔值写
        False；source 不在 SOURCES 里写 null；pick 不是文字写 null；一项原因都不剩时不记。"""
        game = logbook.new_game()
        self.later()
        logbook.record_unadvised(0, reasons=["别的"])  # 一项原因都不剩（值都不认得）：不记
        self.assertEqual([r for r in self.records() if r["t"] == "unadvised"], [])
        self.later()
        logbook.record_unadvised(3, round=5, reasons=["name", "name", "别的", "unknown", "hero"],
                                 advised="是", star=-1, source="别的", pick=7)
        self.later()
        logbook.record_unadvised(4, round=-2, reasons=["hero"], advised=True, star=True, source="task", pick="书签")
        records = [r for r in self.records() if r["t"] == "unadvised"]
        self.assertEqual(len(records), 2)
        first, second = records
        self.assertEqual((first["game"], first["select_no"], first["round"]), (game, 3, 5))
        self.assertEqual(first["reasons"], ["name", "unknown", "hero"])
        self.assertFalse(first["advised"])
        self.assertIsNone(first["star"])
        self.assertIsNone(first["source"])
        self.assertIsNone(first["pick"])
        self.assertEqual((second["round"], second["star"], second["source"], second["advised"], second["pick"]),
                         (None, None, "task", True, "书签"))
        self.assertEqual(list(first)[:4], ["t", "time", "game", "select_no"])

    def test_unadvised_on_second_lane_has_lane(self):
        """第二张悬浮窗那条线写出的 unadvised 记录另带 "lane"（它没有 offer_id 可加前缀）：定局号以前挂着、后补写的
        那条也一样。"""
        line = logbook.lane("taptap")
        self.later()
        line.record_unadvised(1, reasons=["hero"])  # 这次运行还没定局号：先挂着
        game = line.new_game()
        self.later()
        line.record_unadvised(2, reasons=["hero"])
        records = [r for r in self.records() if r["t"] == "unadvised"]
        self.assertEqual([(r["lane"], r["game"], r["select_no"]) for r in records],
                         [("taptap", None, 1), ("taptap", game, 2)])

    def test_whitelists_match_labels(self):
        """推荐日志的白名单和记账里的叫法一致（改了一边忘了另一边时在这里看出来）。"""
        from advisor.session import CHIP_SOURCE_LABEL, DIFFICULTY_SOURCE_LABEL
        self.assertEqual(logbook.DIFFICULTY_SOURCES, frozenset(DIFFICULTY_SOURCE_LABEL))
        self.assertEqual(logbook.OWNED_SOURCES, frozenset(CHIP_SOURCE_LABEL))

    def test_lane_client(self):
        line = logbook.lane("taptap")
        self.later()
        line.record_offer(State(hero="芬妮", round=1, owned=()), {"best": "大背包"}, "o-1", client="int")
        record = [r for r in self.records() if r["t"] == "offer"][-1]
        self.assertEqual((record["lane"], record["client"]), ("taptap", "int"))

    def test_default_records_unchanged(self):
        game = logbook.new_game()
        state = State(hero="芬妮", round=3, owned=("大背包",))
        advice = {"best": "大背包"}
        self.later()
        self.assertEqual(logbook.record_offer(state, advice, "o-1"), game)
        logbook.record_pick("o-1", "大背包")
        logbook.record_feedback("o-1", "地图因素", " 地图太窄 ")
        self.later()
        logbook.record_game_over(why="检测到结算画面", t="别的", time="别的", game="别的", lane="taptap")

        records = self.records()
        self.assertEqual([r["t"] for r in records], ["newgame", "offer", "pick", "feedback", "gameover"])
        for record in records:
            self.assertNotIn("lane", record)

        newgame, offer, pick, feedback, gameover = records
        self.assertEqual(list(newgame), ["t", "time", "game"])
        self.assertEqual(list(offer), ["t", "time", "offer_id", "game", "v", "data_date", "state", "advice"])
        self.assertEqual(offer["offer_id"], "o-1")
        self.assertEqual(offer["game"], game)
        self.assertEqual(offer["state"], _jsonable(state))
        self.assertEqual(offer["advice"], advice)
        self.assertEqual(list(pick), ["t", "time", "offer_id", "chosen"])
        self.assertEqual(pick["offer_id"], "o-1")
        self.assertEqual(list(feedback), ["t", "time", "offer_id", "reason", "note"])
        self.assertEqual(feedback["note"], "地图太窄")
        self.assertEqual(list(gameover), ["t", "time", "game", "why"])
        self.assertEqual(gameover["game"], game)
        self.assertEqual(gameover["why"], "检测到结算画面")


class InterleavedLanesTest(LaneTestBase):
    """两张悬浮窗交替记三选一：各是一个局号，日志里不多出 newgame，局数算两局。"""

    def test_interleaved_offers_do_not_split_games(self):
        taptap = logbook.lane("taptap")
        primary_ids, taptap_ids = set(), set()
        primary_owned, taptap_owned = [], []
        for rnd in (3, 4, 5):
            primary_owned.append(f"芬妮筹码{rnd}")
            primary_ids.add(self.offer(logbook, "芬妮", rnd, primary_owned, f"p{rnd}"))
            taptap_owned.append(f"凛筹码{rnd}")
            taptap_ids.add(self.offer(taptap, "凛", rnd - 1, taptap_owned, f"t{rnd}"))

        self.assertEqual(len(primary_ids), 1)
        self.assertEqual(len(taptap_ids), 1)
        self.assertNotEqual(primary_ids, taptap_ids)
        records = self.records()
        self.assertEqual([r["t"] for r in records], ["offer"] * 6)  # 没有多出来的 newgame
        self.assertEqual(logbook.game_count_since_calibration(), 2)
        self.assertEqual(logbook.stats()["games"], 2)

    def test_scan_log_keeps_lanes_apart(self):
        """默认的 _scan_log 看不见别的线；_scan_log("taptap") 只看得见它自己的。末尾一大段全是别的线的记录时也往前找。"""
        game = logbook.new_game()
        self.offer(logbook, "芬妮", 3, ["大背包"], "p1")
        primary_time = self.clock.isoformat()
        taptap = logbook.lane("taptap")
        owned = []
        for i in range(30):
            owned.append(f"凛筹码{i}")
            self.offer(taptap, "凛", 2 + i, owned, f"t{i}")

        last, latest = logbook._scan_log()
        self.assertEqual(last["offer_id"], "p1")
        self.assertEqual(last["game"], game)
        self.assertEqual(latest, primary_time)
        taptap_last, taptap_latest = logbook._scan_log("taptap")
        self.assertEqual(taptap_last["offer_id"], "taptap-t29")
        self.assertNotEqual(taptap_latest, primary_time)

        with mock.patch.object(logbook, "_TAIL_CHUNK", 50):
            self.assertEqual(logbook._scan_log(), (last, latest))
            self.assertEqual(logbook._scan_log("taptap"), (taptap_last, taptap_latest))


class LaneScanEquivalenceTest(unittest.TestCase):
    """_scan_log(lane) 和整份按顺序读（iter_records）再按这条线挑出来逐位相同：几条线的记录随机交错、分在几个月份文件里
    （这条线的记录可能全在更早的文件里，新的文件末尾全是别的线的）、有的记录没有 time 键；块大小用默认、7、64 三种。"""

    MONTHS = ("2026-08", "2026-09", "2026-10", "2026-11")
    LANES = (None, "taptap", "steam")

    @staticmethod
    def last_two(records) -> tuple:
        """整份按顺序读的口径：最后一条三选一、新一局或对局结束的记录，和最后一条带 time 的记录的 time。"""
        last = latest = None
        for record in records:
            latest = record.get("time", latest)
            if record.get("t") in ("offer", "newgame", "gameover"):
                last = record
        return last, latest

    @staticmethod
    def in_lane(record: dict, lane: str | None) -> bool:
        return "lane" not in record if lane is None else record.get("lane") == lane

    def make_folder(self, folder, rng: random.Random) -> None:
        folder.mkdir()
        for month in rng.sample(self.MONTHS, rng.randint(1, 3)):
            rows = []
            for _ in range(rng.randint(0, 30)):
                record = {"t": rng.choice(["offer", "pick", "feedback", "newgame", "gameover", "calibrated"])}
                if rng.random() < 0.85:
                    record["time"] = f"{month}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:00:00"
                lane = rng.choice(self.LANES + ("taptap",))
                if lane is not None:
                    record["lane"] = lane
                record["game"] = f"g{rng.randint(0, 4)}"
                rows.append(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\r\n")
            (folder / f"{month}.jsonl").write_bytes(b"".join(rows))

    def test_random_mixed_lanes_match_whole_read(self):
        base = sandbox.temp_dir(self, "logbook-lanes-scan-")
        rng = random.Random(97)
        filtered = 0  # 按线挑和不按线挑结果不同的次数：这批数据确实考到了按线筛
        for case in range(60):
            folder = base / f"c{case:02d}"
            self.make_folder(folder, rng)
            now = datetime(2026, rng.randint(9, 12), rng.randint(1, 28), 12, 0, 0)
            with mock.patch.object(paths, "logs_dir", return_value=folder), \
                    mock.patch.object(logbook, "_now", side_effect=lambda when=now: when):
                records = list(logbook.iter_records(since=now - timedelta(days=31)))
                everything = self.last_two(records)
                for lane in self.LANES:
                    want = self.last_two(r for r in records if self.in_lane(r, lane))
                    filtered += want != everything
                    for chunk in (None, 7, 64):
                        with self.subTest(case=case, lane=lane, chunk=chunk):
                            if chunk is None:
                                self.assertEqual(logbook._scan_log(lane), want)
                            else:
                                with mock.patch.object(logbook, "_TAIL_CHUNK", chunk):
                                    self.assertEqual(logbook._scan_log(lane), want)
        self.assertGreater(filtered, 60)


class LaneRecordTest(LaneTestBase):
    """别的线的记录：每条带 lane，编号带前缀，选择和反馈和它的三选一对得上。"""

    def test_lane_fields_and_prefix(self):
        taptap = logbook.lane("taptap")
        self.offer(logbook, "芬妮", 3, ["大背包"], "p1")
        self.offer(taptap, "凛", 2, ["短刀"], "t1")
        self.offer(taptap, "凛", 3, ["短刀", "护符"], "t2")
        logbook.record_pick("p1", "大背包")
        taptap.record_pick("t1", "短刀")
        logbook.record_feedback("p1", "照顾队友", "给队友")
        taptap.record_feedback("t2", "地图因素", "地图窄")
        taptap.record_game_over(why="检测到结算画面", lane="steam", game="别的")

        lane_records = [r for r in self.records() if r.get("lane") == "taptap"]
        self.assertEqual(len(lane_records), 5)
        for record in lane_records:
            self.assertEqual(list(record)[:3], ["t", "time", "lane"])
        self.assertEqual([r["offer_id"] for r in lane_records if r["t"] == "offer"], ["taptap-t1", "taptap-t2"])
        self.assertEqual([r["offer_id"] for r in lane_records if r["t"] == "pick"], ["taptap-t1"])
        self.assertEqual([r["offer_id"] for r in lane_records if r["t"] == "feedback"], ["taptap-t2"])
        gameover = [r for r in lane_records if r["t"] == "gameover"][0]
        self.assertEqual(gameover["lane"], "taptap")  # 调用方给的 lane、game 不采用
        self.assertEqual(gameover["game"], taptap.game)

    def test_calibrate_pairs_each_lane(self):
        """两张悬浮窗各自从 1 起编号，两条线给的是同一串编号：校准工具照样各配各的，三选一次数、选择数也不少算。"""
        taptap = logbook.lane("taptap")
        self.offer(logbook, "芬妮", 3, ["大背包"], "1")
        self.offer(taptap, "凛", 2, ["短刀"], "1")
        self.offer(logbook, "芬妮", 4, ["大背包", "护符"], "2")
        self.offer(taptap, "凛", 3, ["短刀", "护符"], "2")
        logbook.record_pick("1", "大背包")
        taptap.record_pick("1", "短刀")
        logbook.record_feedback("2", "地图因素", "窄")
        taptap.record_feedback("2", "照顾队友", "队友要")

        offers, picks, feedback, fixed, schemed = calibrate.collect()
        self.assertEqual(set(offers), {"1", "2", "taptap-1", "taptap-2"})
        self.assertEqual(picks, {"1": "大背包", "taptap-1": "短刀"})
        self.assertEqual({f["offer_id"]: f["reason"] for f in feedback}, {"2": "地图因素", "taptap-2": "照顾队友"})
        self.assertEqual(fixed, set())
        self.assertEqual(schemed, set())
        self.assertEqual(offers["taptap-1"]["lane"], "taptap")
        self.assertEqual((offers["1"]["state"]["hero"], offers["taptap-1"]["state"]["hero"]), ("芬妮", "凛"))
        self.assertEqual(offers["1"]["game"], logbook._PRIMARY.game)
        self.assertEqual(offers["taptap-1"]["game"], taptap.game)
        stats = logbook.stats()
        self.assertEqual((stats["games"], stats["offers"], stats["picks"], stats["feedback"]), (2, 4, 2, 2))


class LaneIndependenceTest(LaneTestBase):
    """一条线开新局、撤销、记对局结束，另一条的「当前这一局」不变。"""

    def test_operations_do_not_leak_between_lanes(self):
        taptap = logbook.lane("taptap")
        first = logbook.new_game()
        taptap_game = taptap.new_game()
        self.assertNotEqual(first, taptap_game)

        second = logbook.new_game()
        self.assertNotEqual(second, first)
        self.assertEqual(taptap.current_game(), taptap_game)
        logbook.undo_new_game(first)
        self.assertEqual(logbook.current_game(), first)
        self.assertEqual(taptap.current_game(), taptap_game)
        logbook.record_game_over(why="检测到结算画面")
        self.assertEqual(taptap.current_game(), taptap_game)
        taptap.new_game()
        self.assertEqual(logbook.current_game(), first)  # 另一条线开新局也不影响这条线


class RestartTest(LaneTestBase):
    """模拟重启（只清内存里的状态、日志留着）：两条线各接各的，restart 的时刻按各自最后一条记录算。"""

    def play_both(self) -> tuple[str, str]:
        primary_game = logbook.new_game()
        self.offer(logbook, "芬妮", 3, ["大背包"], "p3")
        taptap = logbook.lane("taptap")
        taptap_game = taptap.new_game()
        self.offer(taptap, "凛", 2, ["短刀"], "t2")
        self.later()
        taptap.record_pick("t2", "短刀")
        self.later()
        logbook.record_pick("p3", "大背包")
        return primary_game, taptap_game

    def test_each_lane_resumes_its_own_game(self):
        primary_game, taptap_game = self.play_both()
        self.later()
        logbook._reset_for_tests()  # 重启：日志文件留着

        self.assertEqual(logbook.resumable(), primary_game)
        taptap = logbook.lane("taptap")
        self.assertEqual(taptap.resumable(), taptap_game)
        self.assertEqual(self.offer(logbook, "芬妮", 4, ["大背包", "护符"], "p4"), primary_game)
        self.assertEqual(self.offer(taptap, "凛", 3, ["短刀", "护符"], "t3"), taptap_game)

    def test_restart_time_is_per_lane(self):
        primary_game, taptap_game = self.play_both()
        primary_last = self.clock.isoformat()  # 最后一条是默认线的选择
        self.later()
        taptap = logbook.lane("taptap")
        taptap.record_feedback("t2", "地图因素", "地图窄")  # taptap 最后一条更晚
        taptap_last = self.clock.isoformat()

        logbook._reset_for_tests()
        logbook._PRIMARY._resume_from_log()
        self.assertEqual(logbook._PRIMARY.restart, datetime.fromisoformat(primary_last))
        taptap = logbook.lane("taptap")
        taptap._resume_from_log()
        self.assertEqual(taptap.restart, datetime.fromisoformat(taptap_last))
        self.assertEqual(logbook._PRIMARY.game, primary_game)
        self.assertEqual(taptap.game, taptap_game)

    def test_gameover_ends_only_its_lane(self):
        primary_game, taptap_game = self.play_both()
        logbook.record_game_over(why="检测到结算画面")
        logbook._reset_for_tests()

        self.assertIsNone(logbook.resumable())  # 默认线最后一条是对局结束：不接
        taptap = logbook.lane("taptap")
        self.assertEqual(taptap.resumable(), taptap_game)  # 另一条线照样接
        new_game = self.offer(logbook, "芬妮", 1, ["新筹码"], "p5")
        self.assertNotEqual(new_game, primary_game)
        self.assertEqual(self.offer(taptap, "凛", 4, ["短刀", "护符", "腰带"], "t4"), taptap_game)

    def test_new_game_resumes_only_within_gap(self):
        """最后一条是新的一局时也看 GAME_GAP：隔了 3 小时不接（下一次三选一开新的局号），1 小时照旧接上；
        撤销新一局写的那条隔了 3 小时同样不接。第二条线只看自己的记录。"""
        taptap = logbook.lane("taptap")
        first = taptap.new_game()
        self.later(3 * 3600)
        logbook._reset_for_tests()
        taptap = logbook.lane("taptap")
        self.assertIsNone(taptap.resumable())
        second = self.offer(taptap, "凛", 1, ["短刀"], "t1")
        self.assertNotEqual(second, first)

        third = taptap.new_game()
        self.later(3600)
        logbook._reset_for_tests()
        taptap = logbook.lane("taptap")
        self.assertEqual(taptap.resumable(), third)
        self.assertEqual(self.offer(taptap, "凛", 2, ["短刀", "护符"], "t2"), third)

        taptap.undo_new_game(third)
        self.later(3 * 3600)
        logbook._reset_for_tests()
        taptap = logbook.lane("taptap")
        self.assertIsNone(taptap.resumable())
        fourth = self.offer(taptap, "凛", 3, ["短刀", "护符", "腰带"], "t3")
        self.assertNotEqual(fourth, third)


class HeldUnadvisedTest(LaneTestBase):
    """这次运行还没定局号时记的 unadvised 先挂着：日志里接得上的那一局可能是上次运行没打完的另一局，等头一次三选一判过
    是不是新的一局再写；之前先来了新一局、对局结束、数据表的，game 写 null。"""

    def unadvised(self) -> list[dict]:
        return [r for r in self.records() if r["t"] == "unadvised"]

    def test_first_offer_decides_game(self):
        """上次运行打到第 8 轮没结束，重启半小时后先来了一次没给推荐的三选一（角色还没认出来），接着头一次三选一换了
        角色、轮次变小：判出是新的一局，挂着的那项写进新的这一局（时刻照记的时候的，排在那次三选一前面），只进新这一局的
        报告、不进上次那一局的。定了局号以后的照常直接写。"""
        old = self.offer(logbook, "芬妮", 8, ["大背包"], "p8")
        logbook._reset_for_tests()  # 重启：日志文件留着
        self.later(30 * 60)
        held_at = self.clock.isoformat(timespec="seconds")
        logbook.record_unadvised(1, round=2, reasons=["hero"])
        self.assertEqual(self.unadvised(), [])  # 还没定局号：先不写
        self.later(30)
        new = logbook.record_offer(State(hero="凛", round=2, owned=()), {"best": "护符"}, "p2")
        self.assertNotEqual(new, old)
        records = self.records()
        self.assertEqual([r["t"] for r in records][-2:], ["unadvised", "offer"])
        self.assertEqual((records[-2]["game"], records[-2]["time"], records[-2]["select_no"], records[-2]["round"]),
                         (new, held_at, 1, 2))
        self.assertEqual(len(game_report.build(records, new, install="0" * 32)["unadvised"]), 1)
        self.assertEqual(game_report.build(records, old, install="0" * 32)["unadvised"], [])
        self.later()
        logbook.record_unadvised(2, reasons=["name"])
        self.assertEqual([(r["game"], r["select_no"]) for r in self.unadvised()], [(new, 1), (new, 2)])

    def test_game_unknown_when_other_records_come_first(self):
        """定局号以前先来了新的一局或数据表：挂着的不知道属于哪一局，game 写 null（时刻照记的时候的），写在那一条前面。
        新的一局以后的照常写进这一局；数据表以后还是没有局号，接着挂着。"""
        logbook.record_unadvised(9, round=7, reasons=["name"])
        logbook.record_game_over(why="上次的结束")  # 日志里最后一条是对局结束：之后的都不接它
        for name, step in (("new_game", logbook.new_game), ("record_table", lambda: logbook.record_table(map="m"))):
            with self.subTest(name):
                logbook._reset_for_tests()
                self.later(60)
                held_at = self.clock.isoformat(timespec="seconds")
                logbook.record_unadvised(1, round=2, reasons=["name"])
                self.later()
                game = step()
                records = self.records()
                self.assertEqual(records[-2]["t"], "unadvised")
                self.assertEqual((records[-2]["game"], records[-2]["time"]), (None, held_at))
                self.later()
                logbook.record_unadvised(2, reasons=["hero"])
                if game is None:
                    self.assertEqual(self.records()[-1]["t"], "table")  # 还没有局号：挂着
                    self.assertEqual(len(logbook._PRIMARY.held), 1)
                else:
                    self.assertEqual(self.records()[-1]["game"], game)
                logbook.new_game()  # 挂着的写成 null，不留给下一个子测试
                logbook.record_game_over(why="收尾")  # 下一个子测试重启后不接这一局

    def test_game_over_without_log_opens_game(self):
        """中途打开、日志里没有接得上的局，整局一次推荐都没给就结束了：对局结束另开一个局号（按挂着的头一条的时刻），
        挂着的照原来的时刻写进这一局、排在结束前面，对局结束也记在这一局；拼得出一份 offers 为空的报告。"""
        self.later(60)
        held_at = self.clock.isoformat(timespec="seconds")
        logbook.record_unadvised(1, round=2, reasons=["hero"], hero_guess="米米", hero_score=0.7)
        self.later(600)
        logbook.record_unadvised(2, round=5, reasons=["hero"])
        self.later(600)
        game = logbook.record_game_over(why="检测到结算画面")
        self.assertIsNotNone(game)
        self.assertEqual(logbook.current_game(), game)
        records = self.records()
        self.assertEqual([(r["t"], r["game"]) for r in records],
                         [("unadvised", game), ("unadvised", game), ("gameover", game)])
        self.assertEqual(records[0]["time"], held_at)
        self.assertEqual((records[0]["hero_guess"], records[0]["hero_score"]), ("米米", 0.7))
        report = game_report.build(records, game, install="0" * 32)
        self.assertEqual((report["offers"], len(report["unadvised"])), ([], 2))
        self.assertIsNotNone(report["end"])
        # 下次启动：最后一条是对局结束，不接这一局
        logbook._reset_for_tests()
        self.later(60)
        self.assertIsNone(logbook.resumable())

    def test_game_over_resumes_unfinished_game(self):
        """上次运行打到第 3 轮没结束，重启后整局一次推荐都没给（第 5 轮起认不到角色）就结束了：轮次没变小、没隔太久，
        算接上的那一局，挂着的和对局结束都写进它，这一局只有一份报告（带着上次的三选一和这次的 unadvised）。"""
        old = self.offer(logbook, "芬妮", 3, ["大背包"], "p3")
        logbook._reset_for_tests()
        self.later(5 * 60)
        logbook.record_unadvised(1, round=5, reasons=["hero"])
        self.later(20 * 60)
        self.assertEqual(logbook.record_game_over(why="检测到结算画面"), old)
        records = self.records()
        self.assertEqual({r["game"] for r in records}, {old})
        report = game_report.build(records, old, install="0" * 32)
        self.assertEqual((len(report["offers"]), len(report["unadvised"])), (1, 1))
        self.assertIsNotNone(report["end"])
        self.assertEqual(list(game_report._games(records, None)), [old])

    def test_game_over_after_unfinished_game_is_new(self):
        """上次运行打到第 8 轮没结束（之后关了程序、那一局再没接着打），重启后的一局一次推荐都没给就结束了：挂着的头一条
        轮次比上次的小、或者离上次最后一次三选一超过 GAME_GAP 时，是新的一局，另开局号；上次那一局不多出这次的
        unadvised、也不拿这次的结束。"""
        for name, gap, rnd in (("轮次变小", 30 * 60, 2), ("隔得太久", 3 * 3600, 9)):
            with self.subTest(name):
                old = self.offer(logbook, "芬妮", 8, ["大背包"], f"p8-{name}")
                logbook._reset_for_tests()
                self.later(gap)
                logbook.record_unadvised(1, round=rnd, reasons=["hero"])
                self.later(600)
                new = logbook.record_game_over(why="检测到结算画面")
                self.assertNotEqual(new, old)
                records = self.records()
                self.assertEqual([(r["t"], r["game"]) for r in records[-2:]], [("unadvised", new), ("gameover", new)])
                old_report = game_report.build(records, old, install="0" * 32)
                self.assertEqual(old_report["unadvised"], [])
                self.assertIsNone(old_report["end"])
                self.assertEqual(game_report.build(records, new, install="0" * 32)["offers"], [])
                self.later(3 * 3600)
                logbook._reset_for_tests()

    def test_next_game_after_opened_game_not_merged(self):
        """对局结束时按挂着的另开了局号以后，下一局没判出新的一局（没有 new_game）：下一局的三选一轮次变小，照样判成新的
        一局，不并进刚结束的那一局；轮次没变小（那次结束是误认的，这一局接着打）时算同一局，只有一份报告。"""
        logbook.record_unadvised(1, round=6, reasons=["hero"])
        self.later(600)
        logbook.record_unadvised(2, round=12, reasons=["hero"])
        self.later(600)
        first = logbook.record_game_over(why="检测到结算画面")
        self.later(300)
        logbook.record_unadvised(1, round=1, reasons=["hero"])  # 下一局开头：先挂着，等判过再写
        self.assertEqual(self.records()[-1]["t"], "gameover")
        self.later(60)
        second = logbook.record_offer(State(hero="芬妮", round=2, owned=()), {"best": "大背包"}, "n-1")
        self.assertNotEqual(second, first)
        records = self.records()
        self.assertEqual([(r["t"], r["game"]) for r in records[-2:]], [("unadvised", second), ("offer", second)])
        self.assertEqual(len(game_report.build(records, first, install="0" * 32)["unadvised"]), 2)
        # 误认的结束：轮次接着往上走，算同一局
        logbook._reset_for_tests()
        self.later(5 * 3600)
        logbook.record_unadvised(1, round=4, reasons=["hero"])
        self.later(600)
        game = logbook.record_game_over(why="检测到房间画面")
        self.later(60)
        logbook.record_unadvised(2, round=6, reasons=["hero"])
        self.later(60)
        self.assertEqual(logbook.record_offer(State(hero="芬妮", round=7, owned=()), {"best": "大背包"}, "n-2"), game)
        self.later(600)
        self.assertEqual(logbook.record_game_over(why="检测到结算画面"), game)
        report = game_report.build(self.records(), game, install="0" * 32)
        self.assertEqual((len(report["offers"]), len(report["unadvised"])), (1, 2))

    def test_two_games_without_advice_not_merged(self):
        """连着两局都一次推荐都没给（自己的角色一直认不出），中间没判出新的一局：第二局的挂着的轮次变小，另开局号，两局各一份
        报告、各带自己的 unadvised；结束以后在大厅偶然认成的、之后开了新的一局的，不进任何一局。"""
        logbook.record_unadvised(1, round=6, reasons=["hero"])
        self.later(600)
        first = logbook.record_game_over(why="检测到结算画面")
        self.later(300)
        logbook.record_unadvised(2, round=3, reasons=["hero"])
        self.later(600)
        second = logbook.record_game_over(why="检测到结算画面")
        self.assertNotEqual(second, first)
        records = self.records()
        self.assertEqual([[u["round"] for u in game_report.build(records, g, install="0" * 32)["unadvised"]]
                          for g in (first, second)], [[6], [3]])
        self.later(60)
        logbook.record_unadvised(3, reasons=["name"])  # 大厅里偶然认成的
        logbook.new_game()
        stray = [r for r in self.records() if r["t"] == "unadvised"][-1]
        self.assertIsNone(stray["game"])

    def test_later_offer_same_game_one_report(self):
        """开局以后头几次认不到角色（没给推荐），过了几轮认到了、给了推荐：前面的 unadvised 和后来的三选一在同一局，
        结束时只有一份报告、两样都带；中途打开（还没定局号）时同样并进头一次三选一判出的那一局。"""
        for name, opening in (("开局时定了局号", logbook.new_game), ("中途打开", lambda: None)):
            with self.subTest(name):
                logbook._reset_for_tests()
                self.later(3 * 3600)
                opening()
                self.later()
                logbook.record_unadvised(1, round=1, reasons=["hero"])
                self.later(60)
                logbook.record_unadvised(2, round=2, reasons=["hero"])
                self.later(60)
                game = logbook.record_offer(State(hero="芬妮", round=3, owned=()), {"best": "大背包"}, f"q-{name}")
                self.later(600)
                self.assertEqual(logbook.record_game_over(why="检测到结算画面"), game)
                records = [r for r in self.records() if r.get("game") == game]
                self.assertEqual([r["t"] for r in records if r["t"] != "newgame"],
                                 ["unadvised", "unadvised", "offer", "gameover"])
                report = game_report.build(self.records(), game, install="0" * 32)
                self.assertEqual((len(report["offers"]), len(report["unadvised"])), (1, 2))
                self.assertEqual([g for g in game_report._games(self.records(), None) if g == game], [game])

    def test_strays_after_end_no_new_report(self):
        """一局结束以后（还没开下一局）在大厅等画面上偶然认成三选一：记在原来那一局，但不进那一局的报告（时刻在结束以后），
        也不另造一局；没有结束信号就什么都不发。"""
        game = logbook.new_game()
        self.later()
        logbook.record_unadvised(1, round=3, reasons=["name"])
        self.later(600)
        logbook.record_game_over(why="检测到结算画面")
        self.later(60)
        logbook.record_unadvised(2, round=None, reasons=["name"])
        records = self.records()
        self.assertEqual(game_report.build(records, game, install="0" * 32)["unadvised"][0]["round"], 3)
        self.assertEqual(len(game_report.build(records, game, install="0" * 32)["unadvised"]), 1)
        # 刚打开程序、日志里最后一条是对局结束：大厅里偶然认成的先挂着，开了新的一局时写成 null，不进任何一局的报告
        logbook._reset_for_tests()
        self.later(60)
        logbook.record_unadvised(3, reasons=["hero"])
        logbook.new_game()
        stray = [r for r in self.records() if r["t"] == "unadvised"][-1]
        self.assertIsNone(stray["game"])

    def test_client_and_version_written(self):
        """unadvised 记录另带版本、数据日期，给了认得的客户端才写 client。"""
        logbook.new_game()
        self.later()
        logbook.record_unadvised(1, reasons=["name"], client="int")
        self.later()
        logbook.record_unadvised(2, reasons=["name"], client="别的")
        first, second = self.unadvised()
        self.assertEqual((first["client"], first["v"]), ("int", logbook.VERSION))
        self.assertIn("data_date", first)
        self.assertNotIn("client", second)

    def test_hero_guess_only_with_hero_reason(self):
        """hero_guess、hero_score 只在原因里有 hero 时写；hero_guess 不是文字时两项都是 null，hero_score 不是有限的数写 null。"""
        logbook.new_game()
        for n, (reasons, guess, score) in enumerate(((["hero"], "米米", 0.71), (["name"], "米米", 0.71),
                                                     (["hero"], 5, 0.71), (["hero"], "恋", float("inf")))):
            self.later()
            logbook.record_unadvised(n, reasons=reasons, hero_guess=guess, hero_score=score)
        self.assertEqual([(r["hero_guess"], r["hero_score"]) for r in self.unadvised()],
                         [("米米", 0.71), (None, None), (None, None), ("恋", None)])

    def test_held_at_most_max(self):
        """挂着的最多 MAX_HELD_UNADVISED 条，多的丢掉（留先来的）。"""
        for n in range(logbook.MAX_HELD_UNADVISED + 5):
            self.later()
            logbook.record_unadvised(n, reasons=["hero"])
        self.offer(logbook, "芬妮", 1, ["大背包"], "p1")
        self.assertEqual([r["select_no"] for r in self.unadvised()], list(range(logbook.MAX_HELD_UNADVISED)))


class LaneApiTest(LaneTestBase):
    """线的取用和对象上的东西。"""

    def test_same_name_same_object_and_bad_names(self):
        self.assertIs(logbook.lane("taptap"), logbook.lane("taptap"))
        self.assertIsNot(logbook.lane("taptap"), logbook.lane("steam"))
        for bad in ("", None):
            with self.assertRaises(ValueError):
                logbook.lane(bad)

    def test_lane_has_what_the_second_overlay_uses(self):
        taptap = logbook.lane("taptap")
        self.assertEqual(taptap.PICK_REROLL, logbook.PICK_REROLL)
        self.assertEqual(taptap.FEEDBACK_REASONS, logbook.FEEDBACK_REASONS)
        for name in ("game_count_since_calibration", "calibration_due", "stats", "mark_calibrated", "iter_records"):
            self.assertFalse(hasattr(taptap, name), name)
        for name in ("new_game", "undo_new_game", "current_game", "resumable", "record_offer", "record_pick",
                     "record_feedback", "record_game_over", "records_between"):
            self.assertTrue(callable(getattr(taptap, name, None)), name)

    def test_records_between_reads_both_lanes(self):
        """线上的 records_between 和模块级的取法相同：按时刻取，两条线的记录都在里面（第二张悬浮窗存诊断包用）。"""
        self.offer(logbook, "芬妮", 3, ["大背包"], "1")
        start = self.clock + timedelta(seconds=1)
        self.offer(logbook, "芬妮", 4, ["大背包", "星币锤"], "2")
        taptap = logbook.lane("taptap")
        self.offer(taptap, "凛", 2, ["短刀"], "1")
        got = taptap.records_between(start)
        self.assertEqual(got, logbook.records_between(start))
        self.assertEqual([(r.get("lane"), r["offer_id"]) for r in got], [(None, "2"), ("taptap", "taptap-1")])

    def test_same_second_games_get_different_ids(self):
        first = logbook.new_game()
        second = logbook.lane("taptap").new_game()
        self.assertNotEqual(first, second)
        self.assertEqual([r["game"] for r in self.records() if r["t"] == "newgame"], [first, second])

    def test_services_can_use_a_lane(self):
        taptap = logbook.lane("taptap")
        services = Services(logbook=taptap)
        self.assertTrue(services.has("logbook", "record_offer"))
        self.assertTrue(services.has("logbook", "record_feedback"))
        self.assertTrue(services.has("logbook", "records_between"))
        self.assertIsNone(services.call("logbook", "game_count_since_calibration", default=None))
        game = services.call("logbook", "record_offer", State(hero="凛", round=2, owned=("短刀",)),
                             {"best": "短刀"}, "s1")
        self.assertEqual(game, taptap.current_game())
        offer = [r for r in self.records() if r["t"] == "offer"][-1]
        self.assertEqual(offer["lane"], "taptap")
        self.assertEqual(offer["offer_id"], "taptap-s1")
        self.assertEqual(offer["game"], game)


if __name__ == "__main__":
    unittest.main()
