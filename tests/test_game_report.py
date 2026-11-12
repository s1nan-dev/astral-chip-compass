"""对局报告（advisor/game_report.py）：从推荐日志拼报告、存进 logs\\upload、启动时补拼、发送和清理。

拼出来的报告都拿报告格式的检查函数（report_format.clean_report）过一遍：收数据的服务一项不合格就整份拒收，
拼法改坏了要在这里看出来。检查函数和收数据服务（intake.py）在收数据服务那一头：环境变量 ACC_REPORT_CHECK 指向放
它们的文件夹时才核格式、跑端到端，没设时跳过这几步，其余照跑。发送用假的联网会话，本机起收数据服务的端到端核对在文件最后。

时间都用固定的「现在」，推荐日志写在测试沙箱的临时文件夹里。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from advisor import data, errors, game_report, logbook, paths, schemes, update

# 收数据服务那一头放 report_format.py、intake.py 的文件夹（环境变量 ACC_REPORT_CHECK）；没设时核格式、端到端跳过
_REF = os.environ.get("ACC_REPORT_CHECK", "").strip()
REF = Path(_REF) if _REF else None
GAME = "g20260101-100000"
INSTALL = "a" * 32
SECRET = "d" * 32
NOW = datetime(2026, 1, 2, 12, 0, 0)
SINCE = datetime(2025, 1, 1)


def format_module():
    """报告格式那一头的模块；没设 ACC_REPORT_CHECK、或那里没有 report_format.py 时跳过。"""
    if REF is None or not (REF / "report_format.py").is_file():
        raise unittest.SkipTest("没有报告格式的检查函数（环境变量 ACC_REPORT_CHECK）")
    if str(REF) not in sys.path:
        sys.path.insert(0, str(REF))
    import report_format

    return report_format


def format_checker():
    """报告格式的检查函数；没设 ACC_REPORT_CHECK、或那里没有 report_format.py 时跳过。"""
    return format_module().clean_report


def chip(name: str, rarity: str = "蓝", school: str = "星光") -> dict:
    return {"name": name, "rarity": rarity, "school": school, "effect": "给人看的效果原文", "icon": "1"}


def state(**changes) -> dict:
    base = {
        "hero": "芬妮", "star": 1, "round": 2, "map": "森秋幻境", "owned": ["甲"],
        "source": "levelup", "tier": None, "rerolls": 2, "rerolls_known": True,
        "probabilities": [60, 30, 10], "left_rarity": "蓝", "map_candidates": ["森秋幻境"],
        "teammates": [{"hero": "米米", "bot": True}, {"hero": "帕露南", "bot": True}],
        "mutators": [], "tasks_done": None, "difficulty": "normal", "difficulty_level": "normal",
        "difficulty_name": "普通", "progress": 1, "progress_cap": None, "direct": 0,
        "endless_pending": False, "endless_given": 0, "endless_due": None,
    }
    base.update(changes)
    return base


def advice(**changes) -> dict:
    base = {
        "state": state(),
        "evals": [
            {"chip": chip("甲"), "parts": [{"label": "基础分", "score": 5.0, "origin": "",
                                            "reason": "给人看的整句", "short": "短句", "net": ""}],
             "official": 30.0, "official_raw": 60.0, "immediate": 30.0, "lookahead": -2.0,
             "total": 28.0, "percent": 0.13, "superseded_by": None, "lookahead_reason": "整句"},
            {"chip": chip("乙", "紫", "治愈"), "total": 20.0},
        ],
        "reroll": {"expected": 74.2, "cost": 37.5, "value": 36.7, "p_better": 0.67, "percent": 0.18,
                   "chain": 1, "kept": 2, "spare": 0, "need": 8, "known": True},
        "best": "甲", "notes": ["给人看的一句"], "top": 1,
        "picks": {"total": 8.0, "levelups": 3, "extras": 5.0, "cap": 18, "rounds_left": 17,
                  "capped": False, "endless": 0},
        "maps": ["森秋幻境"], "tendency": ["攻击", 100], "unrecorded": [],
        "ranking": None, "state_again": "不带",
    }
    base.update(changes)
    return base


def offer(offer_id="o1", when="2026-01-01T10:00:00", game=GAME, **changes) -> dict:
    record = {"t": "offer", "time": when, "offer_id": offer_id, "game": game, "v": "1.2.3",
              "data_date": "2026-01-01", "state": state(), "advice": advice()}
    record.update(changes)
    return record


def pick(offer_id="o1", chosen="甲", when="2026-01-01T10:00:05", **changes) -> dict:
    record = {"t": "pick", "time": when, "offer_id": offer_id, "chosen": chosen}
    record.update(changes)
    return record


def feedback(offer_id="o1", reason="地图因素", when="2026-01-01T10:00:06", **changes) -> dict:
    record = {"t": "feedback", "time": when, "offer_id": offer_id, "reason": reason, "note": "补充说明"}
    record.update(changes)
    return record


def gameover(when="2026-01-01T11:00:00", game=GAME, **changes) -> dict:
    record = {"t": "gameover", "time": when, "game": game, "why": "结算画面", "map": "森秋幻境",
              "difficulty": "普通", "level": "normal", "rounds": 7, "cap": None, "star": 2,
              "progress": 7, "owned": ["甲", "乙"], "lane": None}
    record.update(changes)
    return record


def table_player(seat=1, **changes) -> dict:
    record = {"seat": seat, "self": False, "bot": True, "hero": "米米",
              "kills": 3, "downs": 1, "damage_dealt": 1000, "damage_taken": 500, "heal": 0,
              "best": ["damage_dealt"], "coins_all": 120, "coins_transferred": 30,
              "chips": ["甲", "乙"], "chips_unknown": 0,
              "badges": [{"kind": "大荒星陨", "value": 1000}]}
    record.update(changes)
    return record


def table(when="2026-01-01T11:00:10", game=GAME, **changes) -> dict:
    record = {"t": "table", "time": when, "game": game, "map": "森秋幻境", "difficulty": "普通",
              "rounds": 13, "players": [table_player(1), table_player(2)]}
    record.update(changes)
    return record


def unadvised(when="2026-01-01T10:30:00", game=GAME, **changes) -> dict:
    record = {"t": "unadvised", "time": when, "game": game, "select_no": 3, "round": 4,
              "reasons": ["name"], "advised": False, "star": 2, "source": None, "pick": None}
    record.update(changes)
    return record


class Case(unittest.TestCase):
    def setUp(self):
        base = paths.temp_dir() / "tests-game-report"
        base.mkdir(parents=True, exist_ok=True)
        self._dir = tempfile.TemporaryDirectory(dir=base)
        self.logs = Path(self._dir.name)
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        game_report._reset_for_tests()
        self.addCleanup(game_report._reset_for_tests)

    def clean(self, report):
        """拿报告格式的检查函数过一遍；没有检查函数时只跳过这一步，后面的断言照跑。"""
        try:
            clean = format_checker()
        except unittest.SkipTest:
            return report
        clean(report)
        return report

    def explain(self, report):
        """table 在收数据的服务那一头不会被存成 null；没有检查函数时只跳过这一步。"""
        try:
            module = format_module()
        except unittest.SkipTest:
            return
        self.assertIsNone(module.explain_table(report))

    def explain_unadvised(self, report):
        """unadvised 在收数据的服务那一头不会被存成 null；没有检查函数时只跳过这一步。"""
        try:
            module = format_module()
        except unittest.SkipTest:
            return
        self.assertIsNone(module.explain_unadvised(report))

    def write(self, records):
        path = self.logs / "2026-01.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def assemble(self, records, *, enabled=True, now=NOW, since=SINCE, secret=SECRET, install=INSTALL):
        with mock.patch.object(logbook, "records_between", return_value=records):
            return game_report.assemble(GAME, install=install, secret=secret, since=since,
                                        enabled=(enabled if callable(enabled) else lambda: enabled), now=now)

    def reports(self):
        if not game_report.folder().is_dir():
            return []
        return sorted(path for path in game_report.folder().glob("*.json")
                      if game_report._REPORT_NAME.fullmatch(path.name))


class BuildTest(Case):
    def test_map_source_in_state(self):
        """每次三选一的 state 里总有 map_source：从 offer 记录顶层取（不是 state 里的），白名单里的照写，旧记录、
        值不对的写 null。"""
        for record, want in ((offer(map_source="tasks"), "tasks"), (offer(), None), (offer(map_source="别的"), None),
                             (offer(state=state(map_source="result")), None)):
            with self.subTest(record=record, want=want):
                report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
                self.assertEqual(report["offers"][0]["state"]["map_source"], want)

    def test_end_map_source(self):
        """结束记录：map_source 只收 END_MAP_SOURCES 里的值，别的写 null；map_in_game 照文字写。旧记录没有这两项
        时也不写。"""
        records = [offer(), gameover(map_source="result", map_in_game="园林中庭")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual((report["end"]["map_source"], report["end"]["map_in_game"]), ("result", "园林中庭"))
        records = [offer(), gameover(map_source="别的", map_in_game="园林中庭")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertIsNone(report["end"]["map_source"])
        self.assertEqual(report["end"]["map_in_game"], "园林中庭")
        report = self.clean(game_report.build([offer(), gameover()], GAME, install=INSTALL, since=SINCE))
        self.assertNotIn("map_source", report["end"])

    def test_difficulty_source_in_state(self):
        """每次三选一的 state 里总有 difficulty_source：从 offer 记录顶层取，白名单里的照写，旧记录、值不对的写 null。"""
        for record, want in ((offer(difficulty_source="cap"), "cap"), (offer(), None),
                             (offer(difficulty_source="别的"), None),
                             (offer(state=state(difficulty_source="rows")), None)):
            with self.subTest(record=record, want=want):
                report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
                self.assertEqual(report["offers"][0]["state"]["difficulty_source"], want)

    def test_owned_lists_in_state(self):
        """每次三选一的 state 里 owned_sources、owned_panel 和清理过的 owned 一一对应：旧记录没有时都是 null；
        长度对不上时两样都 null；不是文字的筹码项和数组按同一批下标对齐。"""
        record = offer(state=state(owned=["甲", "乙"]), owned_sources=["pick", "manual"],
                       owned_panel=[True, False])
        report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
        st = report["offers"][0]["state"]
        self.assertEqual((st["owned"], st["owned_sources"], st["owned_panel"]),
                         (["甲", "乙"], ["pick", "manual"], [True, False]))
        for record in (offer(),  # 旧记录没有
                       offer(state=state(owned=["甲", "乙"]), owned_sources=["pick"],
                             owned_panel=[True, False])):  # 长度对不上
            with self.subTest(record=record):
                report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
                st = report["offers"][0]["state"]
                self.assertIsNone(st["owned_sources"])
                self.assertIsNone(st["owned_panel"])
        # 来路不是白名单里的值：那一项写 null
        record = offer(state=state(owned=["甲", "乙"]), owned_sources=["pick", "别的"],
                       owned_panel=[True, False])
        report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["state"]["owned_sources"], ["pick", None])
        # owned 里有不是文字的项：_text_list 丢掉它，两样按同一批下标取
        record = offer(state=state(owned=["甲", 5, "乙"]), owned_sources=["pick", "given", "added"],
                       owned_panel=[True, True, False])
        report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
        st = report["offers"][0]["state"]
        self.assertEqual((st["owned"], st["owned_sources"], st["owned_panel"]),
                         (["甲", "乙"], ["pick", "added"], [True, False]))
        # 超过上限：owned 截到 200 项，两样也截到同样的项
        names = [f"筹{i}" for i in range(game_report.MAX_ARRAY + 5)]
        record = offer(state=state(owned=names), owned_sources=["pick"] * len(names),
                       owned_panel=[True] * len(names))
        report = self.clean(game_report.build([record], GAME, install=INSTALL, since=SINCE))
        st = report["offers"][0]["state"]
        self.assertEqual((len(st["owned"]), len(st["owned_sources"]), len(st["owned_panel"])),
                         (game_report.MAX_ARRAY, game_report.MAX_ARRAY, game_report.MAX_ARRAY))
        self.assertEqual(set(st["owned_sources"]), {"pick"})

    def test_end_new_fields(self):
        """结束记录新加的四项：难度依据只收 DIFFICULTY_SOURCES 里的值；两样数组和 end.owned 对齐；cap_rejected
        只收 0～10 亿的整数。旧记录没有时写 null，每一项都在。"""
        records = [offer(state=state(owned=["甲", "乙"])),
                   gameover(owned=["甲", "乙"], owned_sources=["pick", "panel"], owned_panel=[True, False],
                            difficulty_source="result", cap_rejected=2)]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        end = report["end"]
        self.assertEqual((end["owned_sources"], end["owned_panel"], end["difficulty_source"], end["cap_rejected"]),
                         (["pick", "panel"], [True, False], "result", 2))
        report = self.clean(game_report.build([offer(), gameover()], GAME, install=INSTALL, since=SINCE))
        end = report["end"]
        self.assertEqual((end["owned_sources"], end["owned_panel"], end["difficulty_source"], end["cap_rejected"]),
                         (None, None, None, None))
        for bad in ("-1", -1, True, 1_000_000_001, 2.0):
            records = [offer(), gameover(difficulty_source="别的", cap_rejected=bad)]
            report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
            self.assertEqual((report["end"]["difficulty_source"], report["end"]["cap_rejected"]), (None, None))

    def test_map_sources_match_labels(self):
        """报告的 MAP_SOURCES 和 session.core 的 MAP_SOURCE_LABEL 的键一致，结束的另加 result。"""
        from advisor.session.core import MAP_SOURCE_LABEL
        self.assertEqual(game_report.MAP_SOURCES, frozenset(MAP_SOURCE_LABEL))
        self.assertEqual(game_report.END_MAP_SOURCES, game_report.MAP_SOURCES | {"result"})

    def test_updates_use_last_record(self):
        """同一次三选一有几条时取最后一条：局面和推荐以后一条为准，选择、反馈也各取最后一条。"""
        records = [
            offer("o1", advice=advice(best="甲")),
            offer("o1", when="2026-01-01T10:00:02", advice=advice(best="乙")),
            pick("o1", "甲"), pick("o1", "乙"),
            feedback("o1", "照顾队友"), feedback("o1", "推荐有误"),
        ]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["advice"]["best"], "乙")
        self.assertEqual(report["offers"][0]["pick"], "乙")
        self.assertEqual(report["offers"][0]["feedback"], "推荐有误")

    def test_pick_reroll_and_via(self):
        """旧日志里的「重选」写成「刷新」；via 只认 panel、supplement，正常选的为 null。"""
        records = [
            offer("o1", pick="重选", via="panel"),
            offer("o2", when="2026-01-01T10:01:00", pick="甲", via="supplement"),
            offer("o3", when="2026-01-01T10:02:00", pick="甲"),
            pick("o1", "重选", via="panel"), pick("o2", "甲", via="supplement"), pick("o3", "甲"),
        ]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual([item["pick"] for item in report["offers"]], ["刷新", "甲", "甲"])
        self.assertEqual([item["via"] for item in report["offers"]], ["panel", "supplement", None])

    def test_feedback_reason_only(self):
        """反馈只带四个原因之一，补充说明（note）不带；原因不在里面时为 null。"""
        records = [offer("o1"), feedback("o1", "地图因素"), offer("o2", when="2026-01-01T10:01:00"),
                   feedback("o2", "别的说法")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual([item["feedback"] for item in report["offers"]], ["地图因素", None])
        self.assertNotIn("补充说明", json.dumps(report, ensure_ascii=False))

    def test_feedback_try_other_school(self):
        """点了「想试别的玩法」的那一次，报告里的 feedback 就是这四个字。"""
        report = self.clean(game_report.build([offer("o1"), feedback("o1", "想试别的玩法")], GAME,
                                              install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["feedback"], "想试别的玩法")

    def test_evals_only_name_rarity_school(self):
        """evals 的 chip 只写筹码名，rarity、school 另从日志里的筹码资料取；其余整份资料不带。"""
        report = self.clean(game_report.build([offer()], GAME, install=INSTALL, since=SINCE))
        first = report["offers"][0]["advice"]["evals"][0]
        self.assertEqual((first["chip"], first["rarity"], first["school"]), ("甲", "蓝", "星光"))
        self.assertNotIn("effect", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("给人看的效果原文", json.dumps(report, ensure_ascii=False))

    def test_whitelist_drops_extra_fields(self):
        """白名单外的字段不带：给人看的整句（notes、理由、提示）、推荐里重复的那份局面都不带。"""
        report = self.clean(game_report.build([offer()], GAME, install=INSTALL, since=SINCE))
        text = json.dumps(report, ensure_ascii=False)
        for word in ("给人看的一句", "给人看的整句", "短句", "lookahead_reason", "state_again", "notes"):
            self.assertNotIn(word, text)

    def test_tendency_and_unrecorded(self):
        """职业倾向照抄 [职业, 分值]（分值不能丢）；unrecorded 是整数下标组成的数组。"""
        records = [offer(advice=advice(tendency=["卡牌、辅助", 60], unrecorded=[1, 0]))]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["advice"]["tendency"], ["卡牌、辅助", 60])
        self.assertEqual(report["offers"][0]["advice"]["unrecorded"], [1, 0])
        records = [offer(advice=advice(unrecorded=[0, True, "1", 2]))]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["advice"]["unrecorded"], [0, 2])

    def test_scheme_share_code(self):
        """按方案推荐时把日志里记的那份方案写回分享码，只取 ACC<版本>-<编码> 这一段。"""
        db = data.load_db()
        hero = next(iter(db.heroes))
        first, second = list(db.chips)[:2]
        catalog = schemes.get_catalog()
        expected = schemes.to_short(schemes.Scheme(hero=hero, tiers={first: "core", second: "first"}), catalog)
        expected = expected[expected.index("ACC"):]
        raw = {"id": "x", "name": "方案名", "hero": hero,
               "tiers": {first: "必选", second: "推荐"}, "schools": ["攻击"]}
        records = [offer(advice=advice(best=first), scheme=raw, default_best=second)]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        item = report["offers"][0]
        self.assertEqual(item["scheme"], expected)
        self.assertEqual(item["default_best"], second)
        self.assertNotIn("方案名", json.dumps(report, ensure_ascii=False))

    def test_scheme_from_plan_record(self):
        """日志里记的是 Plan.record 的那份（档位按方案的先后，同一档的先后就是顺位）：写回的分享码和拿原来那套方案直接写
        的一样，同一档里几个筹码的先后也对得上。"""
        from advisor.engine.context import plan_of

        db = data.load_db()
        catalog = schemes.get_catalog()
        hero = next(iter(db.heroes))
        chips = [name for name in db.chips if name in catalog.code_of][:5]
        original = schemes.Scheme(hero=hero, tiers={chips[0]: "first", chips[1]: "core", chips[2]: "first",
                                                    chips[3]: "core", chips[4]: "avoid"})
        expected = schemes.to_short(original, catalog)
        expected = expected[expected.index("ACC"):]
        raw = plan_of(db, original).record(["攻击"])
        report = self.clean(game_report.build([offer(scheme=raw, default_best=chips[0])], GAME,
                                              install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["scheme"], expected)
        swapped = dict(raw, tiers=dict(reversed(list(raw["tiers"].items()))))  # 同一档里的先后反过来，写出来就不一样
        report = game_report.build([offer(scheme=swapped)], GAME, install=INSTALL, since=SINCE)
        self.assertNotEqual(report["offers"][0]["scheme"], expected)

    def test_scheme_code_has_no_maps(self):
        """分享码一律写第 2 版、不带地图：导出改写第 3 版、日志里的方案标了地图时也一样（作业站按它数局数）。"""
        db = data.load_db()
        catalog = schemes.get_catalog()
        hero = next(iter(db.heroes))
        first, second = [name for name in db.chips if name in catalog.code_of][:2]
        place = next(iter(catalog.map_code_of))
        plain = schemes.to_short(schemes.Scheme(hero=hero, tiers={first: "core", second: "first"}), catalog,
                                 maps=False)
        raw = {"hero": hero, "tiers": {first: "必选", second: "推荐"}, "maps": [place]}
        with mock.patch.object(schemes, "WRITE_MAPS", True):
            report = self.clean(game_report.build([offer(scheme=raw)], GAME, install=INSTALL, since=SINCE))
        code = report["offers"][0]["scheme"]
        self.assertTrue(code.startswith("ACC2-"), code)
        self.assertEqual(code, plain[plain.index("ACC"):])

    def test_scheme_unwritable_is_null(self):
        """筹码没有编号等写不出分享码时，这一条 scheme 写 null，整份报告照样拼得出来。"""
        raw = {"hero": "芬妮", "tiers": {"不存在的筹码": "必选"}}
        report = self.clean(game_report.build([offer(scheme=raw)], GAME, install=INSTALL, since=SINCE))
        self.assertIsNone(report["offers"][0]["scheme"])

    def test_default_recommendation_scheme_null(self):
        report = self.clean(game_report.build([offer()], GAME, install=INSTALL, since=SINCE))
        self.assertIsNone(report["offers"][0]["scheme"])
        self.assertIsNone(report["offers"][0]["default_best"])

    def test_client_last_non_empty(self):
        """client 取这一局 offer 记录里最后一个非空的；旧记录没有这一项时为 null。"""
        records = [offer("o1"), offer("o2", when="2026-01-01T10:01:00", client="cn"),
                   offer("o3", when="2026-01-01T10:02:00", client="taptap")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["client"], "taptap")
        report = self.clean(game_report.build([offer()], GAME, install=INSTALL, since=SINCE))
        self.assertIsNone(report["client"])

    def test_started_is_utc_minutes(self):
        """started 是这一局第一条三选一的时刻，换成 UTC、只到分钟；之后更新同一次三选一时的时刻不算。"""
        records = [offer("o1", when="2026-01-01T10:00:00"),
                   offer("o1", when="2026-01-01T10:00:03", advice=advice(best="乙"))]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        local = datetime.fromisoformat("2026-01-01T10:00:00")
        from datetime import timezone

        self.assertEqual(report["started"], local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))

    def test_end_last_game_over(self):
        """end 取同一局最后一条 gameover；那一条没记胜负（旧日志、胜负还没定）时 result 的两项是 null；没有结束时
        end、result 都是 null。"""
        records = [offer(), gameover(why="误认"), gameover(when="2026-01-01T11:30:00", why="结算画面")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["end"]["why"], "结算画面")
        self.assertEqual(report["result"], {"outcome": None, "how": None})
        self.assertNotIn("outcome", report["end"])
        report = self.clean(game_report.build([offer()], GAME, install=INSTALL, since=SINCE))
        self.assertIsNone(report["end"])
        self.assertIsNone(report["result"])

    def test_result_from_later_record(self):
        """胜负在结束以后才定的：后记的那条 gameover（别的项照抄）带着胜负，result 取它，和 end 出自同一条；
        后一条又没记胜负时以后一条为准。三种认法都照写。"""
        first = gameover(why="结算画面")
        for outcome, how in (("win", "victory"), ("loss", "reward"), ("win", "reward"), ("loss", "game_over")):
            with self.subTest(outcome=outcome, how=how):
                later = gameover(when="2026-01-01T11:00:20", outcome=outcome, how=how)
                report = self.clean(game_report.build([offer(), first, later], GAME, install=INSTALL, since=SINCE))
                self.assertEqual(report["result"], {"outcome": outcome, "how": how})
                self.assertEqual(report["end"]["why"], "结算画面")
                self.assertNotIn("how", report["end"])
        decided = gameover(outcome="win", how="victory")
        undecided = gameover(when="2026-01-01T11:00:20")
        report = self.clean(game_report.build([offer(), decided, undecided], GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["result"], {"outcome": None, "how": None})
        # 两项都定不了时照写 null（结束画面上认出了 VICTORY、奖励页的星币又像是失败）
        report = self.clean(game_report.build([offer(), gameover(outcome=None, how=None)], GAME, install=INSTALL,
                                              since=SINCE))
        self.assertEqual(report["result"], {"outcome": None, "how": None})

    def test_result_rejects_unknown_values(self):
        """result 的两项只认 win/loss、victory/reward/game_over，大小写一字不差：别的值（大小写不对、数字、布尔、
        数组、对象、空文字）那一项写 null，另一项照写。"""
        odd = ("Win", "WIN", " win", "draw", "", 1, 0, True, False, 1.5, ["win"], {"outcome": "win"})
        for value in odd:
            with self.subTest(outcome=value):
                report = self.clean(game_report.build([offer(), gameover(outcome=value, how="reward")], GAME,
                                                      install=INSTALL, since=SINCE))
                self.assertEqual(report["result"], {"outcome": None, "how": "reward"})
        for value in ("Victory", "VICTORY", "screen", "", 2, True, ["victory"], {"how": "victory"}):
            with self.subTest(how=value):
                report = self.clean(game_report.build([offer(), gameover(outcome="loss", how=value)], GAME,
                                                      install=INSTALL, since=SINCE))
                self.assertEqual(report["result"], {"outcome": "loss", "how": None})

    def test_end_follows_last_offer(self):
        """end 只取紧跟在这份报告最后一次三选一后面（不早于它、不超过 GAME_GAP）的最后一条 gameover：
        隔得太久、在三选一之前（之后这一局接着打了）、时刻读不出的都不算；窗口里有两条时取后一条。"""
        start = datetime(2026, 1, 1, 10, 0, 0)
        first = offer("o1", when=start.isoformat())

        def report(records):
            return self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))

        for label, gap, expected in (("18 小时以后", timedelta(hours=18), False),
                                     ("30 分钟以后", timedelta(minutes=30), True),
                                     ("正好 GAME_GAP", logbook.GAME_GAP, True)):
            with self.subTest(label):
                end = gameover(when=(start + gap).isoformat(), why=label)
                built = report([first, end])
                self.assertEqual(built["end"] is not None, expected)
                if expected:
                    self.assertEqual(built["end"]["why"], label)

        later = offer("o2", when=(start + timedelta(hours=1)).isoformat())
        false_end = gameover(when=(start + timedelta(minutes=30)).isoformat(), why="误认")
        self.assertIsNone(report([first, false_end, later])["end"])

        two = [first, gameover(when=(start + timedelta(minutes=30)).isoformat(), why="误认"),
               gameover(when=(start + timedelta(hours=1)).isoformat(), why="结算画面")]
        self.assertEqual(report(two)["end"]["why"], "结算画面")

        self.assertIsNone(report([first, gameover(when="坏")])["end"])

    def test_offer_belongs_to_its_last_game(self):
        """同一次三选一分在两局：只算在最后一条 offer 的局。前一局没有别的三选一时没有报告、_games 也不列它；
        前一局另有一次自己的三选一时只带那一次；since 以前的前一条不算，后一条落在 since 以后时只算在后一局。"""
        first = "g20260101-100000"
        second = "g20260101-100500"
        records = [offer("o1", when="2026-01-01T10:00:00", game=first, state=state(hero="甲")),
                   offer("o1", when="2026-01-01T10:00:05", game=second, state=state(hero="乙"))]
        self.assertIsNone(game_report.build(records, first, install=INSTALL, since=SINCE))
        report = self.clean(game_report.build(records, second, install=INSTALL, since=SINCE))
        self.assertEqual(len(report["offers"]), 1)
        self.assertEqual(report["offers"][0]["state"]["hero"], "乙")
        self.assertEqual(set(game_report._games(records, SINCE)), {second})

        own = offer("a1", when="2026-01-01T10:01:00", game=first, state=state(hero="芬妮"))
        report = self.clean(game_report.build(records + [own], first, install=INSTALL, since=SINCE))
        self.assertEqual(len(report["offers"]), 1)
        self.assertEqual(report["offers"][0]["state"]["hero"], "芬妮")

        cut = [offer("o1", when="2025-12-31T23:00:00", game=first, state=state(hero="甲")),
               offer("o1", when="2026-01-01T00:10:00", game=second, state=state(hero="乙"))]
        self.assertIsNone(game_report.build(cut, first, install=INSTALL, since=datetime(2026, 1, 1)))
        report = self.clean(game_report.build(cut, second, install=INSTALL, since=datetime(2026, 1, 1)))
        self.assertEqual(report["offers"][0]["state"]["hero"], "乙")

    def test_end_needs_readable_offer_time(self):
        """报告带上的三选一时刻都读不出时没有可比的范围：end 为 None（没有 since 时这些三选一照样带上）。"""
        report = game_report.build([offer("o1", when="坏")], GAME, install=INSTALL, since=None)
        self.assertEqual(len(report["offers"]), 1)
        self.assertIsNone(report["end"])

    def test_end_far_future_time(self):
        """三选一时刻读到 9999 年时 build 不出错：比间隔用两个时刻相减，不是加上 GAME_GAP。"""
        records = [offer("o1", when="9999-12-31T23:59:00"),
                   gameover(when="9999-12-31T23:59:30")]
        report = game_report.build(records, GAME, install=INSTALL, since=None)
        self.assertIsNotNone(report["end"])

    def test_since_cutoff(self):
        """since（打开开关的时刻）以前的三选一不带：整局都在以前时没有报告。"""
        records = [offer("o1", when="2025-12-31T10:00:00"), offer("o2", when="2026-01-01T10:00:00")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL,
                                              since=datetime(2026, 1, 1)))
        self.assertEqual(len(report["offers"]), 1)
        self.assertIsNone(game_report.build(records, GAME, install=INSTALL, since=datetime(2026, 2, 1)))

    def test_empty_advice_and_state(self):
        """advice 不是对象、或者 candidates 一条都认不出时，不要这一条；一条都不剩时为 None。"""
        records = [offer("o1", advice="坏"), offer("o2", when="2026-01-01T10:01:00")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(len(report["offers"]), 1)
        self.assertIsNone(game_report.build([offer(advice="坏")], GAME, install=INSTALL, since=SINCE))

    def test_other_game_offers_not_taken(self):
        records = [offer("o1"), offer("o2", game="别的局")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertEqual(len(report["offers"]), 1)

    def test_truncation_and_nan(self):
        """照格式的上限截断，NaN、Infinity 写 null，布尔值放在要数的地方也写 null；截断了记一行错误记录。"""
        records = [offer(advice=advice(
            evals=[{"chip": chip("筹码%d" % i), "parts": [{"label": "x", "score": i} for i in range(50)]}
                   for i in range(8)],
            reroll={"expected": float("nan"), "cost": float("inf"), "value": True},
            probabilities=[60, 30, 10],
            top=True,
        ))]
        records[0]["state"]["probabilities"] = [60, float("nan"), 10]
        records[0]["state"]["teammates"] = [{"hero": "a", "bot": True}] * 6
        records[0]["state"]["owned"] = ["甲"] * 250
        records[0]["advice"]["unrecorded"] = [1, 2]
        with mock.patch.object(errors, "log_error") as logged:
            report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        item = report["offers"][0]
        self.assertEqual(len(item["advice"]["evals"]), 6)
        self.assertEqual(len(item["advice"]["evals"][0]["parts"]), 40)
        self.assertEqual(len(item["state"]["teammates"]), 4)
        self.assertEqual(len(item["state"]["owned"]), 200)
        self.assertIsNone(item["advice"]["reroll"]["expected"])
        self.assertIsNone(item["advice"]["reroll"]["cost"])
        self.assertIsNone(item["advice"]["top"])
        self.assertIsNone(item["state"]["probabilities"][1])
        self.assertTrue(logged.called)

    def test_number_only_fields(self):
        """reroll、picks 的各项只收数、布尔值或 null，top 只收整数（格式这样定）：日志里写成文字、小数的写 null，整份照样
        过格式检查，不会因为一项被拒收、删掉。"""
        records = [offer(advice=advice(reroll={"expected": 74.2, "chain": "两次", "known": True},
                                       picks={"total": "8", "capped": False, "cap": [18]}, top=2.0))]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        item = report["offers"][0]["advice"]
        self.assertEqual(item["reroll"], {"expected": 74.2, "chain": None, "known": True})
        self.assertEqual(item["picks"], {"total": None, "capped": False, "cap": None})
        self.assertIsNone(item["top"])
        report = self.clean(game_report.build([offer(advice=advice(top=3))], GAME, install=INSTALL, since=SINCE))
        self.assertEqual(report["offers"][0]["advice"]["top"], 3)

    def test_long_text_cut(self):
        report = self.clean(game_report.build([offer(advice=advice(evals=[{"chip": chip("长" * 300)}]))],
                                              GAME, install=INSTALL, since=SINCE))
        self.assertEqual(len(report["offers"][0]["advice"]["evals"][0]["chip"]), 200)

    def test_report_id_kept(self):
        report = game_report.build([offer()], GAME, install=INSTALL, since=SINCE, report_id="b" * 32)
        self.assertEqual(report["report"], "b" * 32)
        self.clean(report)

    def test_app_fields(self):
        """version、data_date 取这一局最后一条 offer；认不出的版本号写成当前版本，日期不对时为 null。"""
        records = [offer("o1", v="1.0.0", data_date="2026-01-01"),
                   offer("o2", when="2026-01-01T10:01:00", v="坏的", data_date="2026-13-40")]
        report = self.clean(game_report.build(records, GAME, install=INSTALL, since=SINCE))
        self.assertTrue(report["app"]["version"].count(".") == 2)
        self.assertIsNone(report["app"]["data_date"])


class TableTest(Case):
    """结束时的数据表（第 3 版）：取这一局最后一条 table 记录，照格式清理。"""

    def built(self, records):
        return game_report.build(records, GAME, install=INSTALL, since=SINCE)

    def test_without_table_record(self):
        """没有 table 记录时 schema 是 3、table 是 null，整份过格式检查。"""
        report = self.clean(self.built([offer()]))
        self.assertEqual(report["schema"], 3)
        self.assertIsNone(report["table"])
        self.explain(report)

    def test_table_fields(self):
        """正常的表：地图、难度、轮数和每栏的各项照写；整份过格式检查，table 不会被服务存成 null。"""
        report = self.clean(self.built([offer(), table()]))
        item = report["table"]
        self.assertEqual((item["map"], item["difficulty"], item["rounds"]), ("森秋幻境", "普通", 13))
        self.assertEqual(len(item["players"]), 2)
        first = item["players"][0]
        self.assertEqual(first, {
            "seat": 1, "self": False, "bot": True, "hero": "米米",
            "kills": 3, "downs": 1, "damage_dealt": 1000, "damage_taken": 500, "heal": 0,
            "best": ["damage_dealt"], "coins_all": 120, "coins_transferred": 30,
            "chips": ["甲", "乙"], "chips_unknown": 0,
            "badges": [{"kind": "大荒星陨", "value": 1000}],
        })
        self.explain(report)

    def test_last_record_of_this_game_only(self):
        """同一局几条 table 取最后一条（按记录先后，不按结束时刻）；别的局的 table 不带进来。"""
        records = [offer(), table(when="2026-01-01T11:00:20", map="后记的"),
                   table(when="2026-01-01T11:00:10", map="真正最后一条"),
                   table(game="别的局", map="别的局的地图")]
        report = self.clean(self.built(records))
        self.assertEqual(report["table"]["map"], "真正最后一条")
        self.explain(report)

    def test_numbers_cleaned(self):
        """整数只收 0～1e9：布尔值、3.0、负数、超过上限、NaN 都写 null；两端照收。"""
        player = table_player(1, kills=True, downs=3.0, damage_dealt=-1,
                              damage_taken=1_000_000_001, heal=float("nan"),
                              coins_all=0, coins_transferred=1_000_000_000,
                              chips_unknown="0")
        report = self.clean(self.built([offer(), table(rounds=-1, players=[player])]))
        item = report["table"]
        self.assertIsNone(item["rounds"])
        cleaned = item["players"][0]
        self.assertIsNone(cleaned["kills"])
        self.assertIsNone(cleaned["downs"])
        self.assertIsNone(cleaned["damage_dealt"])
        self.assertIsNone(cleaned["damage_taken"])
        self.assertIsNone(cleaned["heal"])
        self.assertIsNone(cleaned["chips_unknown"])
        self.assertEqual((cleaned["coins_all"], cleaned["coins_transferred"]), (0, 1_000_000_000))
        self.explain(report)

    def test_best_cleaned(self):
        """best 只留五项数字里的，去重、保持原来的先后；不认得的丢掉，不是数组时写 null。"""
        player = table_player(1, best=["kills", "不认得", "kills", {"a": 1}, "damage_dealt", "heal"])
        report = self.clean(self.built([offer(), table(players=[player])]))
        self.assertEqual(report["table"]["players"][0]["best"], ["kills", "damage_dealt", "heal"])
        self.explain(report)
        player = table_player(1, best="damage_dealt")
        report = self.clean(self.built([offer(), table(players=[player])]))
        self.assertIsNone(report["table"]["players"][0]["best"])
        self.explain(report)

    def test_badges_cleaned(self):
        """称号只收那 14 个：认不出的写 null，不是对象的项丢掉；不是数组时写 null。"""
        player = table_player(1, badges=[
            {"kind": "死神", "value": 3.0}, {"kind": "不认得的称号", "value": 5},
            {"kind": "蜗牛", "value": -1}, "不是对象", {"kind": "囤积者"},
        ])
        report = self.clean(self.built([offer(), table(players=[player])]))
        self.assertEqual(report["table"]["players"][0]["badges"], [
            {"kind": "死神", "value": None}, {"kind": None, "value": 5},
            {"kind": "蜗牛", "value": None}, {"kind": "囤积者", "value": None},
        ])
        self.explain(report)
        player = table_player(1, badges=None)
        report = self.clean(self.built([offer(), table(players=[player])]))
        self.assertIsNone(report["table"]["players"][0]["badges"])
        self.explain(report)

    def test_chips_and_badges_cut(self):
        """筹码多于 40 截到 40、只留文字项；称号多于 8 截到 8；截断了记一行错误记录。"""
        player = table_player(1, chips=["筹码%d" % i for i in range(45)] + [None, {"a": 1}],
                              badges=[{"kind": "死神", "value": i} for i in range(10)])
        with mock.patch.object(errors, "log_error") as logged:
            report = self.clean(self.built([offer(), table(players=[player])]))
        cleaned = report["table"]["players"][0]
        self.assertEqual(len(cleaned["chips"]), 40)
        self.assertEqual(cleaned["chips"][0], "筹码0")
        self.assertEqual(len(cleaned["badges"]), 8)
        self.assertTrue(logged.called)
        self.explain(report)
        player = table_player(1, chips=["甲", None, {"a": 1}, "乙"])
        report = self.clean(self.built([offer(), table(players=[player])]))
        self.assertEqual(report["table"]["players"][0]["chips"], ["甲", "乙"])
        self.explain(report)

    def test_bad_structure_drops_whole_table(self):
        """players 不是数组、是空数组、多于 4 项、有非对象、座位重复或超出 1～4：整个 table 为 null。"""
        cases = {
            "不是数组": "坏的",
            "空数组": [],
            "多于 4 项": [table_player(i) for i in range(1, 6)],
            "有非对象": [table_player(1), "不是对象"],
            "座位重复": [table_player(1), table_player(1)],
            "座位 0": [table_player(0)],
            "座位 5": [table_player(5)],
        }
        for label, players in cases.items():
            with self.subTest(label):
                report = self.clean(self.built([offer(), table(players=players)]))
                self.assertIsNone(report["table"])
                self.explain(report)

    def test_two_self_all_null(self):
        """两项以上 self 为 true 时，所有玩家的 self 都写 null（服务也是这么存的）。"""
        players = [table_player(1, self=True), table_player(2, self=True), table_player(3, self=False)]
        report = self.clean(self.built([offer(), table(players=players)]))
        self.assertEqual([player["self"] for player in report["table"]["players"]], [None, None, None])
        self.explain(report)

    def test_no_names_or_extra_keys(self):
        """记录里多写的 name、level、game、time、lane 都不带；报告整份转成文字也查不到。"""
        player = table_player(1, name="玩家假名甲乙丙", level=999)
        record = table(players=[player], lane="假线名", extra="多的键")
        text = json.dumps(self.clean(self.built([offer(), record])), ensure_ascii=False)
        for word in ("玩家假名甲乙丙", '"level"', "假线名", "多的键", GAME, record["time"]):
            self.assertNotIn(word, text)

    def test_constants_match_result_table(self):
        """五项数字、14 个称号和 vision/result_table.py 的 STATS、BADGES 一样。"""
        from advisor.vision import result_table
        self.assertEqual(game_report.TABLE_STATS, result_table.STATS)
        self.assertEqual(game_report.TABLE_BADGES, result_table.BADGES)


class UnadvisedTest(Case):
    """认出了三选一、没给推荐的那几次（第 3 版外层的 unadvised）：按局号和 since 挑，最多 50 项，清理成六个键，
    不合格的一项丢掉，一项都没有时写空数组。"""

    def built(self, records, **kw):
        return game_report.build(records, GAME, install=INSTALL, since=kw.pop("since", SINCE), **kw)

    def test_without_records_is_empty_list(self):
        """一次都没有时写 []（不是 null）：整份过格式检查，unadvised 不会被服务存成 null。"""
        report = self.clean(self.built([offer()]))
        self.assertEqual(report["unadvised"], [])
        self.explain_unadvised(report)

    def test_fields_and_order(self):
        """按日志里的先后，只带六个键（select_no 不发）；值照清理：reasons 只留三种、不重复，round/star 非负整数，
        advised 布尔值，source 和 pick 文字或 null。"""
        records = [offer(),
                   unadvised(select_no=9, round=6, reasons=["unknown", "unknown", "别的", "name"],
                             advised=True, star=3, source="shop", pick="书签", extra="多的键"),
                   unadvised(when="2026-01-01T10:40:00", select_no=10, round=True, reasons=["hero"],
                             advised="是", star=-1, source="别的", pick=7)]
        report = self.clean(self.built(records))
        first, second = report["unadvised"]
        self.assertEqual(first, {"round": 6, "reasons": ["unknown", "name"], "advised": True, "star": 3,
                                 "source": "shop", "pick": "书签"})
        self.assertEqual(second, {"round": None, "reasons": ["hero"], "advised": False, "star": None,
                                  "source": None, "pick": None})
        self.explain_unadvised(report)

    def test_bad_item_dropped(self):
        """原因一项都不剩的项丢掉，整块照旧是数组、其余项还在，不会变成 null。"""
        records = [offer(), unadvised(reasons=[]), unadvised(reasons=["别的"]),
                   unadvised(when="2026-01-01T10:40:00", reasons=["name"])]
        report = self.clean(self.built(records))
        self.assertEqual([item["reasons"] for item in report["unadvised"]], [["name"]])
        self.explain_unadvised(report)

    def test_other_games_and_before_since_left_out(self):
        """别的局的、since 以前的（时刻读不出的也算）不带进来。"""
        since = datetime(2026, 1, 1, 10, 20)
        records = [offer(when="2026-01-01T10:30:00"), unadvised(when="2026-01-01T10:10:00"), unadvised(when="读不出"),
                   unadvised(game="别的局"), unadvised(when="2026-01-01T10:31:00")]
        report = self.clean(self.built(records, since=since))
        self.assertEqual(len(report["unadvised"]), 1)
        self.explain_unadvised(report)

    def test_max_fifty(self):
        """多于 50 项时截到 50，并记一行错误记录。"""
        records = [offer()] + [unadvised(when=f"2026-01-01T10:{i // 60:02d}:{i % 60:02d}", round=i)
                               for i in range(55)]
        with mock.patch.object(errors, "log_error") as logged:
            report = self.clean(self.built(records))
        self.assertEqual(len(report["unadvised"]), 50)
        self.assertEqual((report["unadvised"][0]["round"], report["unadvised"][-1]["round"]), (0, 49))
        self.assertTrue(logged.called)
        self.explain_unadvised(report)


class StoreTest(Case):
    def test_compact_and_atomic(self):
        path = self.assemble([offer(), gameover()])
        self.assertIsNotNone(path)
        data_bytes = path.read_bytes()
        self.assertIn(b'{"schema":3,"report":', data_bytes)
        self.assertNotIn(b'", "', data_bytes)
        self.assertTrue(data_bytes.startswith(b"{"))
        self.assertNotIn(b"\xef\xbb\xbf", data_bytes)
        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        self.assertEqual(path.stem, json.loads(data_bytes.decode("utf-8"))["report"])
        self.assertTrue((game_report.folder() / "index.json").is_file())

    def test_same_game_overwrites_same_file(self):
        """同一局拼过两次（先误认结束、后来真正结束）沿用原来的编号，覆盖同一份，以后一次为准。"""
        first = self.assemble([offer(), gameover(why="误认")])
        second = self.assemble([offer(), gameover(why="结算画面")])
        self.assertEqual(first, second)
        self.assertEqual(len(self.reports()), 1)
        index = json.loads((game_report.folder() / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["games"][GAME]["report"], first.stem)
        self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["end"]["why"], "结算画面")

    def test_report_id_from_secret_and_game(self):
        """报告编号是随机值、换行、局号拼起来的 MD5（收数据的服务那一头从推荐日志拼报告时同一种算法）；本机编号照旧写进
        报告，但不参与算编号，换了它编号不变；index.json 丢了重拼，还是同一个编号、同一份文件。"""
        expected = hashlib.md5(f"{SECRET}\n{GAME}".encode("utf-8")).hexdigest()
        first = self.assemble([offer(), gameover(why="误认")])
        self.assertEqual(first.stem, expected)
        text = first.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text)["report"], expected)
        self.assertEqual(json.loads(text)["install"], INSTALL)  # 报告里照旧写本机编号
        self.assertNotIn(SECRET, text)  # 随机值不写进报告
        self.assertEqual(self.assemble([offer()], install="b" * 32), first)  # 换本机编号，编号不变
        (game_report.folder() / "index.json").unlink()
        game_report._reset_for_tests()  # 内存里的索引也忘掉：同启动时读不到 index.json
        second = self.assemble([offer(), gameover(why="结算画面")])
        self.assertEqual(second, first)
        self.assertEqual(len(self.reports()), 1)
        self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["end"]["why"], "结算画面")

    def test_two_lanes_two_reports(self):
        """同时识别两个客户端：两张悬浮窗各自的一局（局号不同，第二张的记录带 lane、三选一编号带「线名-」前缀）各拼一份，
        编号不同、互不覆盖，各自只带自己那一局的三选一和选择。"""
        other = "g20260101-100500"
        records = [
            offer("o1"), pick("o1", "甲"),
            offer("taptap-1", when="2026-01-01T10:00:30", game=other, lane="taptap", client="taptap",
                  advice=advice(best="乙")),
            pick("taptap-1", "乙", lane="taptap"),
            gameover(), gameover(when="2026-01-01T11:05:00", game=other, lane="taptap", why="结算画面"),
        ]
        with mock.patch.object(logbook, "records_between", return_value=records):
            first = game_report.assemble(GAME, install=INSTALL, secret=SECRET, since=SINCE,
                                         enabled=lambda: True, now=NOW)
            second = game_report.assemble(other, install=INSTALL, secret=SECRET, since=SINCE,
                                          enabled=lambda: True, now=NOW)
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(self.reports()), sorted([first, second]))
        one, two = (self.clean(json.loads(p.read_text(encoding="utf-8"))) for p in (first, second))
        self.assertEqual([item["pick"] for item in one["offers"]], ["甲"])
        self.assertEqual([item["pick"] for item in two["offers"]], ["乙"])
        self.assertEqual((one["client"], two["client"]), (None, "taptap"))
        self.assertEqual(two["report"], game_report.report_id_for(SECRET, other))

    def test_disabled_does_not_write(self):
        """关着开关时不写；关掉开关那一刻正在后台拼的，写文件之前再问一次也不留文件。"""
        self.assertIsNone(self.assemble([offer()], enabled=False))
        self.assertEqual(self.reports(), [])

    def test_pending_count(self):
        self.assertEqual(game_report.pending_count(), 0)
        self.assemble([offer()])
        self.assertTrue(os.path.isfile(game_report.folder() / "index.json"))
        self.assertEqual(game_report.pending_count(), 1)


class PruneClearTest(Case):
    def make(self, count, mtime):
        made = []
        for i in range(count):
            path = game_report.folder() / f"{i:032x}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (mtime, mtime))
            made.append(path)
        return made

    def test_prune_old_and_excess(self):
        old = NOW.timestamp() - 31 * 86400
        fresh = NOW.timestamp() - 86400
        stale = self.make(1, old)
        self.make(55, fresh)
        game_report.prune(NOW)
        remaining = self.reports()
        self.assertEqual(len(remaining), game_report.KEEP)
        self.assertFalse(stale[0].exists())

    def test_clear_removes_reports_and_index(self):
        self.assemble([offer()])
        self.assertTrue(self.reports())
        game_report.clear()
        self.assertEqual(self.reports(), [])
        self.assertFalse((game_report.folder() / "index.json").exists())
        self.assertEqual(game_report.pending_count(), 0)

    def test_keeps_files_that_are_not_reports(self):
        folder = game_report.folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "notes.txt").write_text("不是报告", encoding="utf-8")
        (folder / "index.json").write_text("{}", encoding="utf-8")
        self.assertEqual(game_report.pending_count(), 0)
        game_report.prune(NOW)
        self.assertTrue((folder / "notes.txt").exists())


class BackfillTest(Case):
    def backfill(self, records, *, now=NOW, since=SINCE, enabled=True, secret=SECRET):
        with mock.patch.object(logbook, "records_between", return_value=records) as between:
            written = game_report.backfill(install=INSTALL, secret=secret, since=since,
                                           enabled=(enabled if callable(enabled) else lambda: enabled), now=now)
        return written, between

    def test_written_only_for_finished_games(self):
        """离现在不到 GAME_GAP 的不拼（可能还会接着打）；拼过的不拼；since 以前的不拼；有没有 gameover 都拼。"""
        records = [
            offer("o1", when=(NOW - timedelta(hours=3)).isoformat()),
            gameover(when=(NOW - timedelta(hours=2, minutes=30)).isoformat()),
            offer("o2", when=(NOW - timedelta(hours=1)).isoformat(), game="g-running"),
            offer("o3", when=(NOW - timedelta(hours=3)).isoformat(), game="g-old"),
            offer("o4", when=(NOW - timedelta(days=40)).isoformat(), game="g-ancient"),
        ]
        written, _between = self.backfill(records, since=NOW - timedelta(days=10))
        self.assertEqual(len(written), 2)
        contents = [json.loads(path.read_text(encoding="utf-8")) for path in self.reports()]
        ends = sorted((item["end"] is not None) for item in contents)
        self.assertEqual(ends, [False, True])

    def test_recent_game_over_waits(self):
        """记了对局结束的局也要等 GAME_GAP：误认的结束以后这一局还会接着打（重启后也会接上），这时拼了、发了，真正结束时
        重拼的那份会被收数据的服务当作重复的丢掉。隔够了再拼，end 照样带上。"""
        records = [offer(when=(NOW - timedelta(hours=1)).isoformat()),
                   gameover(when=(NOW - timedelta(minutes=30)).isoformat())]
        written, _ = self.backfill(records)
        self.assertEqual((written, self.reports()), ([], []))
        written, _ = self.backfill(records, now=NOW + timedelta(hours=2))
        self.assertEqual(len(written), 1)
        self.assertIsNotNone(json.loads(written[0].read_text(encoding="utf-8"))["end"])

    def test_far_later_end_not_taken(self):
        """隔了很久才认到的结束（离这一局最后一次三选一超过 GAME_GAP）不带进 end：那是程序没认出三选一的另一局；
        补拼出来的这一局 index.json 里 ended 为 false。"""
        records = [offer("o1", when=(NOW - timedelta(hours=21)).isoformat()),
                   gameover(when=(NOW - timedelta(hours=3)).isoformat(), why="另一局")]
        written, _ = self.backfill(records)
        self.assertEqual(len(written), 1)
        report = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertIsNone(report["end"])
        index = json.loads((game_report.folder() / "index.json").read_text(encoding="utf-8"))
        self.assertFalse(index["games"][GAME]["ended"])

    def test_offer_moved_to_its_last_game(self):
        """同一次三选一的前一条在 A、后一条在 B：A 没有别的三选一，不补拼；B 的报告带上后一条。"""
        records = [offer("o1", when=(NOW - timedelta(hours=4)).isoformat(), game="g-moved"),
                   offer("o1", when=(NOW - timedelta(hours=3)).isoformat(), state=state(hero="乙"))]
        written, _ = self.backfill(records)
        self.assertEqual(len(written), 1)
        report = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertEqual(report["offers"][0]["state"]["hero"], "乙")
        index = json.loads((game_report.folder() / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(set(index["games"]), {GAME})

    def test_going_on_after_false_end(self):
        """误认的结束以后撤销了新一局、接着打（之后又有三选一）：按最后一条记录算，不到 GAME_GAP 不拼这半局。"""
        records = [
            offer("o1", when=(NOW - timedelta(hours=3)).isoformat()),
            gameover(when=(NOW - timedelta(hours=2, minutes=50)).isoformat(), why="误认"),
            {"t": "newgame", "time": (NOW - timedelta(hours=2, minutes=49)).isoformat(), "game": GAME, "undo": True},
            offer("o2", when=(NOW - timedelta(minutes=20)).isoformat()),
        ]
        written, _ = self.backfill(records)
        self.assertEqual((written, self.reports()), ([], []))

    def test_already_in_index_skipped(self):
        first, _ = self.backfill([offer(when=(NOW - timedelta(hours=3)).isoformat())])
        self.assertEqual(len(first), 1)
        second, _ = self.backfill([offer(when=(NOW - timedelta(hours=3)).isoformat())])
        self.assertEqual(second, [])

    def test_lost_index_same_id(self):
        """补拼的编号同样由随机值和局号算出来：index.json 丢了、再补拼一遍，还是同一个编号、同一份文件。"""
        records = [offer(when=(NOW - timedelta(hours=3)).isoformat())]
        first, _ = self.backfill(records)
        self.assertEqual([p.stem for p in first], [game_report.report_id_for(SECRET, GAME)])
        (game_report.folder() / "index.json").unlink()
        game_report._reset_for_tests()
        again, _ = self.backfill(records)
        self.assertEqual(again, first)
        self.assertEqual(len(self.reports()), 1)

    def test_since_cutoff(self):
        records = [offer(when=(NOW - timedelta(hours=3)).isoformat())]
        written, _ = self.backfill(records, since=NOW - timedelta(hours=2))
        self.assertEqual(written, [])

    def test_disabled(self):
        written, _ = self.backfill([offer(when=(NOW - timedelta(hours=3)).isoformat())], enabled=False)
        self.assertEqual((written, self.reports()), ([], []))

    def test_watermark_limits_next_read(self):
        """补拼看到哪儿记进索引；下次从「界线 − GAME_GAP」和 since、30 天前里最晚的那个起读。"""
        records = [offer(when=(NOW - timedelta(hours=3)).isoformat())]
        _written, between = self.backfill(records)
        self.assertEqual(between.call_args[0][0], NOW - timedelta(days=game_report.KEEP_DAYS))
        _written, between = self.backfill(records, now=NOW + timedelta(hours=1), since=NOW - timedelta(days=10))
        self.assertEqual(between.call_args[0][0], NOW - timedelta(hours=4))
        index = json.loads((game_report.folder() / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["seen"], (NOW + timedelta(hours=1) - logbook.GAME_GAP).isoformat(timespec="seconds"))


class TargetTest(Case):
    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {game_report.URL_ENV: "http://127.0.0.1:8777/v1/report"}):
            self.assertEqual(game_report.target(False), "http://127.0.0.1:8777/v1/report")
            self.assertEqual(game_report.target(True), "http://127.0.0.1:8777/v1/report")

    def test_development_only_stores(self):
        with mock.patch.dict(os.environ, {game_report.URL_ENV: ""}):
            self.assertIsNone(game_report.target(False))
            with mock.patch.object(update, "USE_MIRROR", True):
                self.assertIsNone(game_report.target(False))
                self.assertEqual(game_report.target(True), game_report.URL)

    def test_fork_only_stores(self):
        with mock.patch.dict(os.environ, {game_report.URL_ENV: ""}), \
                mock.patch.object(update, "USE_MIRROR", False):
            self.assertIsNone(game_report.target(True))


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.closed = False

    def post(self, url, data, headers, **kwargs):
        self.calls.append((url, data, headers, kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self):
        self.closed = True


class SendTest(Case):
    def make_reports(self, count):
        for i in range(count):
            path = game_report.folder() / f"{i:032x}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"n": i}), encoding="utf-8")
            os.utime(path, (100 + i, 100 + i))

    def send(self, results, *, enabled=True):
        game_report._send_error = False  # 同一次运行只记一行：每个用例、每个子用例各算一次运行
        session = FakeSession(results)
        with mock.patch.object(errors, "log_error") as logged:
            out = game_report.send_pending("http://127.0.0.1:1/v1/report", enabled=(
                enabled if callable(enabled) else lambda: enabled), session_factory=lambda: session)
        return out, session, logged

    def test_accepted_deleted(self):
        self.make_reports(1)
        out, session, logged = self.send([(200, b'{"ok":true}')])
        self.assertEqual((out["sent"], out["deleted"], out["left"]), (1, 1, 0))
        self.assertEqual(len(session.calls), 1)
        url, data, headers, kwargs = session.calls[0]
        self.assertEqual(json.loads(gzip.decompress(data)), {"n": 0})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(headers["User-Agent"], update.USER_AGENT)
        self.assertEqual(kwargs["connect_timeout"], game_report.CONNECT_TIMEOUT)
        self.assertEqual(kwargs["timeout"], game_report.TIMEOUT)
        self.assertFalse(logged.called)
        self.assertEqual(self.reports(), [])

    def test_rejected_deleted_and_logged(self):
        for status in (400, 413, 415):
            with self.subTest(status=status):
                self.make_reports(1)
                out, _session, logged = self.send([(status, b'{"ok":false,"error":"bad_field:offers[0].state.star"}')])
                self.assertEqual((out["deleted"], out["left"]), (1, 0))
                self.assertEqual(logged.call_count, 1)
                detail = logged.call_args[0][1]
                self.assertIn(str(status), detail)
                if status == 400:
                    self.assertIn("bad_field:offers[0].state.star", detail)
                self.assertNotIn('"ok"', detail)

    def test_kept_and_stopped(self):
        for result in [(429, b""), (503, b""), (411, b""), urllib.error.URLError("连不上")]:
            with self.subTest(result=result):
                self.make_reports(2)  # 两份：一份没发出去就不再往下发
                out, session, logged = self.send([result])
                sent = 0 if isinstance(result, BaseException) else 1
                self.assertEqual((out["sent"], out["deleted"], out["left"]), (sent, 0, 2))
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(logged.call_count, 1)
                for path in self.reports():
                    path.unlink()

    def test_error_logged_once(self):
        self.make_reports(2)
        _out, _session, logged = self.send([urllib.error.URLError("连不上")])
        self.assertEqual(logged.call_count, 1)

    def test_send_max(self):
        self.make_reports(25)
        out, session, _logged = self.send([(200, b"{}")] * 30)
        self.assertEqual((out["sent"], out["left"]), (game_report.SEND_MAX, 25 - game_report.SEND_MAX))
        self.assertEqual(len(session.calls), game_report.SEND_MAX)

    def test_turned_off_stops(self):
        self.make_reports(3)
        calls = [True, True, False]
        out, session, _logged = self.send([(200, b"{}"), (200, b"{}")], enabled=lambda: calls.pop(0))
        self.assertEqual((out["sent"], out["deleted"], out["left"]), (2, 2, 1))


class InstallTest(unittest.TestCase):
    def test_random_hex(self):
        first, second = game_report.new_install(), game_report.new_install()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)

    def test_secret_random_hex(self):
        first, second = game_report.new_secret(), game_report.new_secret()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)

    def test_report_id_for(self):
        """报告编号：32 位小写十六进制；同一随机值、同一局总是同一个，换了随机值或局号就不同。"""
        value = game_report.report_id_for(SECRET, GAME)
        self.assertRegex(value, r"^[0-9a-f]{32}$")
        self.assertEqual(value, game_report.report_id_for(SECRET, GAME))
        self.assertEqual(value, hashlib.md5(f"{SECRET}\n{GAME}".encode("utf-8")).hexdigest())
        self.assertNotEqual(value, game_report.report_id_for("e" * 32, GAME))
        self.assertNotEqual(value, game_report.report_id_for(SECRET, "g20260101-100001"))


class IntakeTest(Case):
    """端到端：本机起作业站的收数据服务，拿 send_pending 发一份，核对服务收下了、本机的那份删掉了。"""

    def round_trip(self, records, report_id):
        """发一份报告给本机的收数据服务，返回服务存下来的那份。"""
        if REF is None or not (REF / "intake.py").is_file():
            raise unittest.SkipTest("没有收数据服务（环境变量 ACC_REPORT_CHECK）")
        import threading

        sys.path.insert(0, str(REF))
        import intake

        data_dir = Path(self._dir.name) / "intake"
        data_dir.mkdir(parents=True, exist_ok=True)
        config = intake.Config(port=0, min_free_mb=0, min_free_inodes=0)
        service = intake.Service(config, data_dir, datetime.now().astimezone().tzinfo)
        service.store.prepare()
        server = intake.make_server(config, service)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            report = game_report.build(records, GAME, install=INSTALL, since=SINCE, report_id=report_id)
            path = game_report.report_path(report["report"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/report"
            out = game_report.send_pending(url, enabled=lambda: True)
            self.assertEqual((out["sent"], out["deleted"], out["left"]), (1, 1, 0))
            self.assertFalse(path.exists())
            stored = list(data_dir.glob("players/raw/*/*.json.gz"))
            self.assertEqual(len(stored), 1)
            return json.loads(gzip.decompress(stored[0].read_bytes()).decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()

    def test_round_trip(self):
        stored = self.round_trip([offer(), gameover()], "c" * 32)
        self.assertEqual(stored["schema"], 3)
        self.assertIsNone(stored["table"])

    def test_round_trip_with_table(self):
        """带数据表的报告也照收：服务存下以后 table 还在，没被存成 null。"""
        stored = self.round_trip([offer(), gameover(), table()], "d" * 32)
        self.assertEqual(stored["table"]["players"][0]["seat"], 1)
        self.assertEqual(stored["table"]["map"], "森秋幻境")


if __name__ == "__main__":
    unittest.main()
