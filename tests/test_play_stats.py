"""「我的统计」的数字（advisor/play_stats.py）：口径照 tools/calibrate.py --all——同一个 offer_id 取最后一条记录、
实际选择也取最后一条、按「查看筹码」面板改正的不算进一致率、补录的照旧算、按筹码方案推荐的另算一行、旧日志的「重选」按
「刷新」算。另核最近 30 天、前 5 个角色、每个角色前 3 个筹码，以及坏记录跳过、不抛异常。

CalibrateAgreementTest 把同一份日志交给 tools/calibrate.py 和 compute，两边的局数、三选一次数、跟首推选的次数要一样：
哪一边的口径改了、另一边没跟上时在这里报出来。

不碰界面：这个模块的测试也不建 QApplication。"""
import json
import re
import unittest
from datetime import datetime
from unittest import mock

from advisor.play_stats import FollowRate, PlayStats, compute

NOW = datetime(2026, 11, 5, 12, 0, 0)


def offer(oid, *, game="g1", hero="梅加斯", best="电流剑", when="2026-11-01T10:00:00", scheme=False):
    record = {"t": "offer", "offer_id": oid, "game": game, "time": when,
              "state": {"hero": hero}, "advice": {"best": best}}
    if scheme:
        record["scheme"] = {"name": "方案"}
    return record


def pick(oid, chosen, via=None):
    record = {"t": "pick", "offer_id": oid, "chosen": chosen}
    if via:
        record["via"] = via
    return record


class FollowRateTest(unittest.TestCase):
    def test_percent(self):
        self.assertIsNone(FollowRate(0, 0).percent)
        self.assertEqual(FollowRate(73, 120).percent, 61)
        self.assertEqual(FollowRate(12, 25).percent, 48)
        self.assertEqual(FollowRate(0, 7).percent, 0)


class ComputeTest(unittest.TestCase):
    def test_empty(self):
        for records in ([], None, iter([])):
            stats = compute(records, now=NOW)
            self.assertEqual((stats.games, stats.offers, stats.recent_games, stats.recent_offers), (0, 0, 0, 0))
            self.assertFalse(stats.has_log)
            self.assertEqual((stats.heroes, stats.hero_chips), ([], []))
            self.assertEqual(stats.default_follow.total, 0)

    def test_last_offer_wins(self):
        """同一个三选一更新了几次：按最后一条算，局数、次数都不重复算。"""
        records = [
            offer("o1", game="g1", best="电流剑"),
            offer("o1", game="g1", best="美工刀"),
            pick("o1", "美工刀"),
        ]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.games, stats.offers), (1, 1))
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total), (1, 1))

    def test_last_pick_wins(self):
        records = [offer("o1", best="电流剑"), pick("o1", "电流剑"), pick("o1", "美工刀")]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total), (0, 1))

    def test_panel_correction_not_counted(self):
        """最后一条是按「查看筹码」面板改正的：不算进一致率；之后又有正常的选择时重新算。"""
        fixed = [offer("o1", best="电流剑"), pick("o1", "电流剑"), pick("o1", "美工刀", via="panel")]
        self.assertEqual(compute(fixed, now=NOW).default_follow.total, 0)
        again = fixed + [pick("o1", "美工刀")]
        rate = compute(again, now=NOW).default_follow
        self.assertEqual((rate.agreed, rate.total), (0, 1))

    def test_supplement_counted(self):
        records = [offer("o1", best="电流剑"), pick("o1", "电流剑", via="supplement")]
        rate = compute(records, now=NOW).default_follow
        self.assertEqual((rate.agreed, rate.total), (1, 1))

    def test_no_choice_not_counted(self):
        records = [offer("o1"), pick("o1", None)]
        self.assertEqual(compute(records, now=NOW).default_follow.total, 0)

    def test_reroll_normalized(self):
        """推荐「重选」（旧写法）就是刷新，选「刷新」算跟着首推；刷新这一项不算筹码。"""
        records = [offer("o1", best="重选"), pick("o1", "刷新")]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total), (1, 1))
        self.assertEqual(stats.hero_chips, [("梅加斯", [])])

    def test_scheme_separate(self):
        """按筹码方案推荐的：另算一行，不算进默认推荐的局数、次数和一致率。"""
        records = [
            offer("o1", game="g1", best="电流剑"),
            pick("o1", "电流剑"),
            offer("o2", game="g2", best="美工刀", scheme=True),
            pick("o2", "电流剑"),
        ]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.games, stats.offers), (1, 1))
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total), (1, 1))
        self.assertEqual((stats.scheme_follow.agreed, stats.scheme_follow.total), (0, 1))
        self.assertEqual(stats.heroes, [("梅加斯", 1)])

    def test_recent_30_days(self):
        records = [
            offer("old", game="g1", when="2026-10-06T11:59:59"),
            offer("edge", game="g2", when="2026-10-06T12:00:00"),
            offer("new", game="g3", when="2026-11-05T11:00:00"),
        ]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.games, stats.offers), (3, 3))
        self.assertEqual((stats.recent_games, stats.recent_offers), (2, 2))

    def test_top_heroes_by_games(self):
        """按局数排前 5 个：同一局里的几次三选一只算一局；同数时按名字排，结果固定。"""
        records = []
        for i, hero in enumerate(("甲", "乙", "丙", "丁", "戊", "己")):
            for n in range(i + 1):
                records.append(offer(f"{hero}{n}", game=f"g{hero}-{n}", hero=hero))
        records.append(offer("乙extra", game="g乙-0", hero="乙"))  # 同一局里的另一次三选一，不再加一局
        stats = compute(records, now=NOW)
        self.assertEqual(stats.heroes, [("己", 6), ("戊", 5), ("丁", 4), ("丙", 3), ("乙", 2)])

    def test_top_chips_per_hero(self):
        """每个角色选得最多的 3 个筹码：只数选了筹码的，刷新、没记选择的不算；同数时按名字排。"""
        records = [
            offer("o1", hero="梅加斯", best="电流剑"), pick("o1", "电流剑"),
            offer("o2", hero="梅加斯", best="电流剑"), pick("o2", "电流剑"),
            offer("o3", hero="梅加斯", best="电流剑"), pick("o3", "电流剑"),
            offer("o4", hero="梅加斯", best="美工刀"), pick("o4", "美工刀"),
            offer("o5", hero="梅加斯", best="美工刀"), pick("o5", "美工刀"),
            offer("o6", hero="梅加斯", best="跃迁引擎"), pick("o6", "跃迁引擎"),
            offer("o7", hero="梅加斯", best="月光石"), pick("o7", "月光石"),
            offer("o8", hero="梅加斯", best="刷新"), pick("o8", "刷新"),
            offer("o9", hero="梅加斯", best="电流剑"), pick("o9", None),
        ]
        stats = compute(records, now=NOW)
        self.assertEqual(stats.hero_chips, [("梅加斯", [("电流剑", 3), ("美工刀", 2), ("月光石", 1)])])

    def test_hero_chips_only_top_heroes(self):
        records = []
        for i, hero in enumerate(("甲", "乙", "丙", "丁", "戊", "己")):
            records.append(offer(hero, game=f"g{hero}", hero=hero, best="电流剑"))
            records.append(pick(hero, "电流剑"))
        stats = compute(records, now=NOW)
        self.assertEqual([hero for hero, _ in stats.hero_chips], [hero for hero, _ in stats.heroes])
        self.assertNotIn("甲", [hero for hero, _ in stats.hero_chips])

    def test_bad_records_skipped(self):
        """坏记录（断电截断的半行解析出来不是字典、缺字段、时刻写坏、类型不对）跳过，不抛异常。"""
        records = [
            None, 42, "文字", [], {},
            {"t": "offer"},
            {"t": "offer", "offer_id": ""},
            {"t": "offer", "offer_id": ["列表"]},
            {"t": "offer", "offer_id": True},
            {"t": "pick", "offer_id": {}},
            {"t": "offer", "offer_id": "ok", "time": "不是时刻", "state": "坏", "advice": "坏"},
            {"t": "offer", "offer_id": "ok2", "game": "", "time": None},
            offer("good", best="电流剑"),
            pick("good", "电流剑"),
        ]
        stats = compute(records, now=NOW)
        self.assertEqual((stats.games, stats.offers), (1, 3))  # ok、ok2 缺局号、时刻写坏，但仍是三选一记录
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total), (1, 1))
        self.assertTrue(stats.has_log)

    def test_stats_defaults(self):
        """没有日志时给出空的 PlayStats（界面上按「还没有推荐日志」显示）。"""
        self.assertEqual(PlayStats().has_log, False)
        self.assertEqual(PlayStats().hero_chips, [])


class CalibrateAgreementTest(unittest.TestCase):
    """同一份日志交给 tools/calibrate.py（--all 的汇总）和 compute：局数、三选一次数、跟首推选的次数一样。日志里有同一个
    三选一更新了几次、面板改正、补录、旧的「重选」、没记选择、跟首推不一样、按方案推荐这几种。日志写在测试沙箱的临时
    文件夹里，经 logbook.iter_records 读，和窗口读的是同一条路。"""

    RECORDS = [
        offer("o1", game="g1", best="电流剑"), offer("o1", game="g1", best="美工刀"), pick("o1", "美工刀"),
        offer("o2", game="g1", best="电流剑"), pick("o2", "电流剑"), pick("o2", "美工刀", via="panel"),
        offer("o3", game="g2", best="电流剑"), pick("o3", "电流剑", via="supplement"),
        offer("o4", game="g2", best="重选"), pick("o4", "刷新"),
        offer("o5", game="g3", best="跃迁引擎"), pick("o5", None),
        offer("o6", game="g3", best="美工刀"), pick("o6", "电流剑"),
        offer("o7", game="g4", best="美工刀", scheme=True), pick("o7", "美工刀"),
    ]

    def test_same_numbers_as_calibrate(self):
        from pathlib import Path

        from advisor import logbook, paths
        from tests.support import sandbox
        from tools import calibrate

        folder = sandbox.temp_dir(self, "stats-")
        lines = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in self.RECORDS)
        (Path(folder) / "2026-11.jsonl").write_text(lines, encoding="utf-8")
        with mock.patch.object(paths, "logs_dir", return_value=Path(folder)):
            text = calibrate.report(*calibrate.collect(since_calibration=False))
            stats = compute(logbook.iter_records(), now=NOW)
        counts = re.search(r"局数 (\d+)；筹码三选一 (\d+) 次", text)
        rate = re.search(r"推荐与实际选择一致：(\d+)/(\d+)", text)
        self.assertIsNotNone(counts, text)
        self.assertIsNotNone(rate, text)
        self.assertEqual((stats.games, stats.offers), (int(counts.group(1)), int(counts.group(2))))
        self.assertEqual((stats.default_follow.agreed, stats.default_follow.total),
                         (int(rate.group(1)), int(rate.group(2))))
        self.assertIn("另有 1 次三选一按筹码方案推荐", text)
        # 手算：默认推荐 6 次、3 局；面板改正的 o2、没记选择的 o5 不算，o1、o3、o4 跟着首推，o6 没有
        self.assertEqual((stats.games, stats.offers, stats.default_follow.agreed, stats.default_follow.total),
                         (3, 6, 3, 4))
        self.assertEqual((stats.scheme_follow.agreed, stats.scheme_follow.total), (1, 1))


if __name__ == "__main__":
    unittest.main()
