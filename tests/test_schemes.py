"""筹码方案的数据结构（四档和顺位、改顺位的操作、适用地图）、列进方案 / 被锁成不选 / 目标流派三样推导、方案文件的存取、
两种分享格式（advisor/schemes.py），以及筹码编号表（data/chip_codes.json 和 tools/check_data.py 的编号检查）。

测试用的方案文件都放在程序目录的 .tmp 下的临时文件夹里，不碰程序目录下真实的 schemes.json。
"""
import copy
import dataclasses
import itertools
import json
import os
import pickle
import random
import shutil
import tempfile
import time
import tomllib
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import paths
from advisor import schemes as S
from advisor.data import SCHOOLS, TIERS, load_db
from tests.support import sandbox
from tools import check_data

NOW = datetime(2026, 10, 5, 12, 0, 0)


def content(s: S.Scheme, text: bool = True) -> tuple:
    """分享格式带得过去的内容（四档连同顺位，Tiers 的相等要求每一档的先后也相同；地图）：短字符串不含说明和作者。"""
    base = (s.hero, s.name, s.tiers, s.maps)
    return base + (s.note, s.author) if text else base


# ------------------------------------------------------------ 第 2 版的固定样本（以后任何版本都要解出同样的内容）

# 编号 1～89 最初对应的筹码（只增不改：以后改名时这个名称记在 old_names 里，编号不变）
FROZEN_NAMES = (
    "拳击手套-初级、拳击手套-中级、拳击手套-高级、摩托头盔-一般、摩托头盔-中级、摩托头盔-高级、夹心饼干-一般、"
    "夹心饼干-可口、夹心饼干-美味、速度轮滑-初级、速度轮滑-中级、速度轮滑-高级、小猪存钱罐、大背包、额外电池、魔法秘典、"
    "智能手表、会员推荐信、优雅之羽、探天卫星、肾上腺素-高效、书签、磨刀石、循环往复、银行卡-余额少、银行卡-余额多、"
    "银行卡-用不完、“8面”骰子、ATM机、手电筒-强光、星币锤、紫色飞星、金色飞星、缓冲盾牌、可口糖果、维生素药丸、友情徽章、"
    "大碗炖肉、医疗箱-紧急治疗、医疗箱-完备治疗、美工刀-初级、美工刀-锋利、标记喷罐、魔法箭袋、忍术飞镖、手持风扇-小、"
    "手持风扇-大、标靶、普通瞄具、鹰眼瞄具、能量回收、电流剑、安全气囊、电击手套、跃迁引擎、电流核心、永动机、电磁炮、大铁锚、"
    "三叉戟、梦想号模型、诅咒之剑、复仇之戟、贯穿之铳、大铜锣、节日红包、佐茶蛋糕、古老法杖、精品剑盾、小蛇玩偶、小鲤鱼玩偶、"
    "惊涛御守、迷幻海鲜汤、交错双鲤、无限之蛇、计时器、重生人偶、糖果会员证、孔雀扇、鸳鸯玉佩、飞羽手环、运动手环、时间怀表、"
    "电击枪、自适应核心、娱乐终端、原初意识、放大镜、汉娜人偶"
).split("、")

# 固定样本的短字符串里带的（编号, 档位）：85 个筹码（写样本时 72～75 是仅有的地图直接发放筹码），从大编号往小写，
# 档位按编号除以 4 的余数，每一档的顺位都不是编号顺序。电击枪（84）、原初意识（87）10-15 起也改成只靠发放，解出来时会被丢掉
ALL_MARKED = tuple((c, TIERS[c % 4]) for t in TIERS for c in range(89, 0, -1)
                   if not 72 <= c <= 75 and TIERS[c % 4] == t)
ALL_CORE = tuple((c, "core") for c in range(89, 0, -1) if not 72 <= c <= 75)  # 85 个全部必选，从大编号往小写

# （短字符串，角色编号，（编号, 档位）按方案的先后）：6 套「复制默认推荐」（顺位按编号）和几套手写的（顺位不按编号）
SHORT_V2 = [
    ("【默认推荐（副本）】CA2-4jfLo1xGLSAozJUV9eq9tN", 121,
     ((14, "core"), (44, "core"), (45, "core"), (43, "first"), (46, "second"), (47, "second"), (25, "avoid"),
      (26, "avoid"), (27, "avoid"))),
    ("【默认推荐（副本）】CA2-Kpuih5EhvrYV", 102, ((27, "core"), (32, "core"), (33, "core"))),
    ("【默认推荐（副本）】CA2-4rFWVsueRZC7GCNjHxeemnYpQQfkawEAip1DJ8vPD2RUoviupPYt", 104,
     ((16, "core"), (20, "core"), (13, "first"), (14, "first"), (15, "first"), (17, "first"), (44, "first"),
      (56, "first"), (61, "first"), (63, "first"), (64, "first"), (68, "first"), (77, "first"), (4, "second"),
      (5, "second"), (6, "second"), (7, "second"), (8, "second"), (9, "second"), (10, "second"), (11, "second"),
      (12, "second"), (18, "second"), (25, "second"), (26, "second"), (27, "second"), (34, "second"), (39, "second"),
      (40, "second"), (45, "second"), (47, "second"), (60, "second"))),
    ("【默认推荐（副本）】CA2-vHV93oUzZJrpTpM5DPckC7zS6mxCvQWaxro", 120,
     ((3, "core"), (20, "core"), (69, "core"), (2, "first"), (15, "first"), (63, "first"), (64, "first"),
      (10, "second"), (11, "second"), (12, "second"), (17, "second"), (34, "second"), (39, "second"), (40, "second"),
      (19, "avoid"), (37, "avoid"), (59, "avoid"))),
    ("【默认推荐（副本）】CA2-4pbrK9kMLehs658mJtf68gYMFAYUZ3iy5wpgx", 113,
     ((11, "first"), (12, "first"), (15, "first"), (34, "first"), (35, "first"), (40, "first"), (49, "first"),
      (50, "first"), (57, "first"), (2, "second"), (3, "second"), (9, "second"), (10, "second"), (31, "second"),
      (37, "second"), (38, "second"), (39, "second"), (51, "second"), (52, "second"), (48, "avoid"))),
    ("【默认推荐（副本）】CA2-CaQNr9RDLDkzrVs8KbidMXpAHjz5WorxTG", 115,
     ((2, "first"), (3, "first"), (11, "first"), (12, "first"), (31, "first"), (34, "first"), (41, "first"),
      (42, "first"), (49, "first"), (50, "first"), (21, "second"), (25, "second"), (26, "second"), (27, "second"),
      (28, "second"), (30, "second"), (39, "second"), (40, "second"), (48, "second"))),
    ("【治愈坦克】CA2-ANkTfrzSwGvz2vHedw1", 102,
     ((40, "core"), (34, "core"), (38, "first"), (35, "first"), (36, "first"), (37, "first"), (39, "first"))),
    ("【标记充能】CA2-4jfM9g2XcNg58TJP8yD7TM", 121,
     ((45, "core"), (44, "core"), (52, "first"), (43, "first"), (47, "second"), (15, "second"), (27, "avoid"),
      (25, "avoid"), (26, "avoid"))),
    ("【空方案】CA2-4VrUjxM", 124, ()),
    ("【不选为主】CA2-PtRA5eu9zrzr", 124, ((89, "second"), (31, "avoid"), (25, "avoid"))),
    ("【全标】CA2-YLMvHhPAXnPbJj9hN15b4wFgx61XbvM9PwQ8DSY6u8Kv8YXQfQQkMnPNMFksTBkYQ8GJMTqzs6Fem5RP46VGsDb", 108,
     ALL_MARKED),
    ("【全部必选】CA2-2vkdf4FfDAg7nzhzoX5Vm23HCNTAfhZpUVxVGBVLL59rn3EYUAWu1iKT3bHcbM9tSbTyx8nYy5MioWiY5KKBNLBZfUgvuwm"
     "Qj2q3Xqb6prmNzMjZFRr", 108, ALL_CORE),
]
SHORT_HEAL = SHORT_V2[6][0]  # 治愈坦克（芬妮）
SHORT_MARK = SHORT_V2[7][0]  # 标记充能（凛）

# 第 3 版的固定样本（带适用地图，以后任何版本都要解出同样的内容）：（短字符串，角色编号，（编号, 档位）按方案的先后，
# 地图编号）。一张地图、几张地图、编号 9；「九号地图」是「逐个」写法，其余是「列表」写法。
SHORT_V3 = [
    ("【一张地图】CA3-y3miY9V4K", 102, ((34, "core"),), (1,)),
    ("【几张地图】CA3-38C8xznxo6ubbhqpqG", 102,
     ((40, "core"), (34, "core"), (12, "core"), (38, "first"), (25, "avoid")), (1, 5)),
    ("【九号地图】CA3-cTFhnioPFSUeArDAV4D1XYv2Dc6i3jyR8L5F3rft3P5bSsE3rZnxkZ9JxiNzGEwcbA5LbDqxT98RXLgUqTo6nKwM"
     "xGArRM41dvnDrGMXtRPx8gGcsP2Td", 102, ALL_CORE, (9,)),
    ("【地图多】CA3-2FmuAvak5G4pijvsq6jXe5bS1", 121,
     ((45, "core"), (44, "core"), (52, "first"), (43, "first"), (47, "second"), (15, "second"), (27, "avoid"),
      (25, "avoid"), (26, "avoid")), (1, 3, 9)),
]

# （可读文字，角色，说明，作者，{档位: 按顺位的编号}）
TEXT_V2 = [
    ("【默认推荐（副本）】筹码方案 v2｜凛｜必选：大背包、魔法箭袋、忍术飞镖｜推荐：标记喷罐｜可选：手持风扇-小、手持风扇-大｜"
     "不选：银行卡-余额少、银行卡-余额多、银行卡-用不完", "凛", "", "",
     {"core": (14, 44, 45), "first": (43,), "second": (46, 47), "avoid": (25, 26, 27)}),
    ("【默认推荐（副本）】筹码方案 v2｜芬妮｜必选：银行卡-用不完、紫色飞星、金色飞星", "芬妮", "", "", {"core": (27, 32, 33)}),
    ("【治愈坦克】筹码方案 v2｜芬妮｜必选：医疗箱-完备治疗、缓冲盾牌｜推荐：大碗炖肉、可口糖果、维生素药丸、友情徽章、"
     "医疗箱-紧急治疗｜说明：以治愈为主，星光只拿银行卡｜作者：某某", "芬妮", "以治愈为主，星光只拿银行卡", "某某",
     {"core": (40, 34), "first": (38, 35, 36, 37, 39)}),
    ("【标记充能】筹码方案 v2｜凛｜必选：忍术飞镖、魔法箭袋｜推荐：电流剑、标记喷罐｜可选：手持风扇-大、额外电池｜"
     "不选：银行卡-用不完、银行卡-余额少、银行卡-余额多", "凛", "", "",
     {"core": (45, 44), "first": (52, 43), "second": (47, 15), "avoid": (27, 25, 26)}),
    ("【不选为主】筹码方案 v2｜照｜可选：汉娜人偶｜不选：星币锤、银行卡-余额少", "照", "", "", {"second": (89,), "avoid": (31, 25)}),
    ("【空方案】筹码方案 v2｜照", "照", "", "", {}),
]
TEXT_HEAL = TEXT_V2[2][0]

# 第 3 版可读文字：（文字，角色，地图（按编号排），{档位: 按顺位的编号}）
TEXT_V3 = [
    ("【地图甲】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图：龙宫游乐园", "芬妮", ("龙宫游乐园",), {"core": (34,)}),
    ("【地图乙】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图：龙宫游乐园、魔法学院", "芬妮",
     ("龙宫游乐园", "魔法学院"), {"core": (34,)}),
    ("【地图丙】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图：龍宮遊樂園，魔法學院", "芬妮",
     ("龙宫游乐园", "魔法学院"), {"core": (34,)}),
    ("【地图丁】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图： 龙宫游乐园 , 魔法学院 ｜地图：异变图书馆", "芬妮",
     ("龙宫游乐园", "魔法学院", "异变图书馆"), {"core": (34,)}),
    ("【地图戊】筹码方案 v3｜芬妮｜地图：魔法学院", "芬妮", ("魔法学院",), {}),
]

# 第 1 版的写法（带目标流派、仅限目标流派，不带顺位）：不再认，导入时报无法导入、不抛异常
OLD_V1 = [
    ("【治愈坦克】CA1-APQ6U9miaVca6zfqWYM", S.PROBLEM_BAD_CODE),
    ("【默认推荐（副本）】CA1-HYEDnwrN6B4K7MaWkeG66M8", S.PROBLEM_BAD_CODE),
    ("【只设流派】CA1-fA7BwDrR", S.PROBLEM_BAD_CODE),
    ("【全标】CA1-2qP8VfQXgNMhHkR43YtGwNKuG6KLfa47yYmbkzE6epoD", S.PROBLEM_BAD_CODE),
    ("【默认推荐（副本）】筹码方案 v1｜凛｜目标流派：治愈、标记｜必选：大背包、魔法箭袋、忍术飞镖｜推荐：标记喷罐｜"
     "可选：手持风扇-小、手持风扇-大｜不选：银行卡-余额少、银行卡-余额多、银行卡-用不完", S.PROBLEM_BAD_TEXT),
    ("【不选为主】筹码方案 v1｜照｜可选：汉娜人偶｜不选：银行卡-余额少、星币锤", S.PROBLEM_BAD_TEXT),
    ("【标记充能】筹码方案 v1｜凛｜目标流派：标记、充能｜必选：魔法箭袋、忍术飞镖｜推荐：标记喷罐、电流剑｜可选：额外电池、"
     "手持风扇-大｜不选：银行卡-余额少、银行卡-余额多、银行卡-用不完｜仅限目标流派", S.PROBLEM_BAD_TEXT),
]


def by_codes(cat, ranked: dict) -> dict[str, str]:
    """{档位: 按顺位的编号} → 筹码名 → 档位（同一档按顺位放进去）。"""
    return {cat.name_of[c]: t for t in TIERS for c in ranked.get(t, ())}


class _TmpDir(unittest.TestCase):
    def tmp(self) -> Path:
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp


class _Chips:
    """按流派分好的能标档位的筹码（按编号排），三样推导和极端组合的测试用。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = S.get_catalog(cls.db)
        markable = [cls.db.chips[n] for n in cls.cat.sort_chips(cls.cat.markable)]
        cls.school = {s: [c.name for c in markable if c.school == s] for s in SCHOOLS}
        cls.general = [c.name for c in markable if not c.school and not c.maps]
        cls.map_only = [c.name for c in markable if c.maps]
        cls.star, cls.heal, cls.mark, cls.charge = (cls.school[s] for s in SCHOOLS)

    def schools(self, marks) -> tuple:
        """三样推导：（列进方案的流派，被锁成不选的流派，目标流派）。"""
        return S.listed_schools(self.db, marks), S.locked_schools(self.db, marks), S.target_schools(self.db, marks)


# ------------------------------------------------------------ 编号表


class CodeTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.codes = S.load_codes()
        cls.cat = S.get_catalog(cls.db, cls.codes)

    def test_every_chip_has_a_code(self):
        self.assertEqual(sorted(set(self.db.chips) - set(self.cat.code_of)), [])
        codes = [e.code for e in self.codes.entries]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(1 <= c <= S.CODE_MAX for c in codes))
        live = [e.name for e in self.codes.entries if not e.removed]
        self.assertEqual(len(live), len(set(live)))
        self.assertEqual(self.cat.gone, {})
        # 能标档位的筹码：92 张现役筹码里，地图直接发放的 6 张不能标（龙宫游乐园 4 张，电击枪、原初意识 10-15 起
        # 也改成只靠畸变发放），88 → 86
        self.assertEqual(len(self.cat.markable), 86)

    def test_codes_never_change(self):
        """编号只增不改：1～89 永远对应最初的那个筹码（改名后最初的名称在 old_names 里）。"""
        self.assertEqual(len(FROZEN_NAMES), 89)
        by_code = {e.code: e for e in self.codes.entries}
        for code, name in enumerate(FROZEN_NAMES, 1):
            with self.subTest(code=code):
                self.assertIn(name, (by_code[code].name, *by_code[code].old_names))

    def test_chip_codes_cover_all_chips(self):
        """筹码编号表覆盖每一张现役筹码、编号不重复、不超上限（tools/check_data.py 的 codes_problems）：改编号表后
        跑 check_data 会报出来。编号只由 check_data 的 --add-codes 追加。"""
        chips = json.loads((paths.data_dir() / "chips.json").read_text(encoding="utf-8"))["chips"]
        table = json.loads((paths.data_dir() / "chip_codes.json").read_text(encoding="utf-8"))
        self.assertEqual(check_data.codes_problems(chips, table), [])
        self.assertEqual(check_data.CODE_MAX, S.CODE_MAX)  # 编号上限和短字符串格式共用一处

    def test_read_codes_rejects_bad_tables(self):
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        bad = [{"chips": [{"code": 1, "name": "A"}, {"code": 1, "name": "B"}]}, {"chips": [{"code": 0, "name": "A"}]},
               {"chips": "x"}, [], {"chips": [{"code": 1, "name": "A", "old_names": "B"}]}]
        for i, raw in enumerate(bad):
            path = tmp / f"{i}.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                S.read_codes(path)
        with self.assertLogs("advisor.schemes", level="WARNING"):
            self.assertEqual(S.load_codes(str(tmp / "missing.json")).entries, ())


class MapCodeTest(unittest.TestCase):
    """地图编号（data/maps.json 的 code）：分享方案的短字符串和方案文件靠它代表方案标的地图。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_codes_never_change(self):
        # 编号只增不改：改了的话，以前分享出去的方案会标到别的地图上。运动场没有编号
        self.assertEqual(S.map_codes(self.db), {
            "龙宫游乐园": 1, "星趴·梦想号": 2, "园林中庭": 3, "幽魂暗巷": 4, "魔法学院": 5,
            "御魂庆典": 6, "水乡古镇": 7, "森秋幻境": 8, "异变图书馆": 9})

    def test_taggable_and_normalize(self):
        # 能标的地图按编号排。标上的地图去重、去掉不认识的和没有编号的，按编号排好：同样几张地图存出来总是同一个写法
        self.assertEqual(S.taggable_maps(self.db), tuple(S.map_codes(self.db)))
        self.assertNotIn("海选赛运动场", S.taggable_maps(self.db))
        self.assertEqual(S.normalize_maps(self.db, ["魔法学院", "龙宫游乐园", "龙宫游乐园", "海选赛运动场", "不存在", None]),
                         ("龙宫游乐园", "魔法学院"))
        self.assertEqual(S.normalize_maps(self.db, []), ())

    def test_ended_map_keeps_code(self):
        # 结束了的地图留着编号（标过它的方案、旧的分享码照样认得出），只是不再列进能标的地图
        info = dataclasses.replace(self.db.map_info["魔法学院"], ended=True)
        db = dataclasses.replace(self.db, map_info={**self.db.map_info, "魔法学院": info})
        self.assertEqual(S.map_codes(db)["魔法学院"], 5)
        self.assertNotIn("魔法学院", S.taggable_maps(db))
        self.assertEqual(S.normalize_maps(db, ["魔法学院"]), ("魔法学院",))

    def test_duplicate_code_one_map(self):
        # 资料里两张地图写了同一个编号（tools/check_data.py 会报）：一个编号仍然只对应一张地图，解码不会有两种结果
        info = dataclasses.replace(self.db.map_info["园林中庭"], code=5)
        db = dataclasses.replace(self.db, map_info={**self.db.map_info, "园林中庭": info})
        codes = S.map_codes(db)
        self.assertEqual(list(codes.values()).count(5), 1)
        self.assertEqual(len(codes), 8)


# ------------------------------------------------------------ 四档和顺位


class TiersTest(unittest.TestCase):
    """Tiers：每个标了档位的筹码存档位和它在这一档里的顺位；改档位、改顺位的操作都返回新的 Tiers。"""

    def test_ranks_follow_given_order(self):
        """同一档按传入的先后定顺位，从 1 起、连续；迭代按方案的先后（先按档位，同一档按顺位）；未标记没有顺位。"""
        t = S.Tiers({"甲": "first", "乙": "core", "丙": "first", "丁": "avoid", "戊": "second"})
        self.assertEqual(list(t), ["乙", "甲", "丙", "戊", "丁"])
        self.assertEqual(list(t.items()), [("乙", "core"), ("甲", "first"), ("丙", "first"), ("戊", "second"),
                                           ("丁", "avoid")])
        self.assertEqual([t.order(x) for x in TIERS], [("乙",), ("甲", "丙"), ("戊",), ("丁",)])
        self.assertEqual([t.rank(x) for x in ("乙", "甲", "丙", "戊", "丁", "没标的")], [1, 1, 2, 1, 1, None])
        self.assertEqual(t.place("丙"), S.Mark("first", 2))
        self.assertEqual((t.place("丙").tier, t.place("丙").rank), ("first", 2))
        self.assertIsNone(t.place("没标的"))
        self.assertEqual((t["甲"], t.get("没标的"), "丁" in t, "没标的" in t, len(t)), ("first", None, True, False, 5))
        # （筹码名, 档位）序列也行；同一个筹码出现几次时和 dict 相同：位置按第一次，档位按最后一次
        self.assertEqual(S.Tiers([("甲", "first"), ("丙", "first")]).order("first"), ("甲", "丙"))
        twice = S.Tiers([("甲", "first"), ("乙", "first"), ("甲", "core"), ("丙", "first")])
        self.assertEqual([twice.order(x) for x in TIERS], [("甲",), ("乙", "丙"), (), ()])
        self.assertEqual(len(S.Tiers()), 0)
        self.assertEqual(S.Tiers(None), {})
        self.assertEqual(S.Tiers(t), t)  # 从 Tiers 再建一遍不变
        for bad in ({"甲": "必选"}, {"甲": None}, [("甲", 1)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                S.Tiers(bad)
        with self.assertRaises(ValueError):
            t.order("必选")

    def test_equality_counts_ranks(self):
        """相等要求每一档的筹码和先后都相同；不同档之间的先后不算。可哈希，能放进集合、能 pickle。"""
        a = S.Tiers({"甲": "core", "乙": "core", "丙": "first"})
        b = S.Tiers({"乙": "core", "甲": "core", "丙": "first"})
        self.assertNotEqual(a, b)  # 只是必选的顺位对调
        self.assertEqual(dict(a), dict(b))  # 只比档位时用 dict
        self.assertEqual(a, {"甲": "core", "乙": "core", "丙": "first"})
        self.assertEqual(a, {"丙": "first", "甲": "core", "乙": "core"})  # 不同档之间的先后不算
        self.assertEqual({"甲": "core", "乙": "core", "丙": "first"}, a)  # 普通 dict 放在前面也一样
        self.assertNotEqual(a, {"乙": "core", "甲": "core", "丙": "first"})
        for other in ({"甲": "core", "乙": "core"}, {"甲": "core", "乙": "core", "丙": "second"},
                      {"甲": "core", "乙": "core", "丙": "x"}, [("甲", "core"), ("乙", "core"), ("丙", "first")]):
            with self.subTest(other=other):
                self.assertNotEqual(a, other)
        self.assertEqual(hash(a), hash(S.Tiers(dict(a))))
        self.assertEqual(len({a, b, S.Tiers({"甲": "core", "乙": "core", "丙": "first"})}), 2)
        self.assertEqual(pickle.loads(pickle.dumps(b)), b)
        self.assertEqual(copy.deepcopy(b), b)
        self.assertEqual(repr(b), "Tiers({'乙': 'core', '甲': 'core', '丙': 'first'})")

    def test_with_tier(self):
        """标档位：新标的、改了档位的排到那一档最后；已经是这一档时顺位不动；取消标记后面的依次前移。"""
        t = S.Tiers([("甲", "core"), ("乙", "core"), ("丙", "first"), ("丁", "first")])
        self.assertEqual(t.with_tier("戊", "core").order("core"), ("甲", "乙", "戊"))  # 新标的排到最后
        moved = t.with_tier("甲", "first")  # 改档位：排到新的一档最后，原来那一档后面的前移
        self.assertEqual((moved.order("core"), moved.order("first")), (("乙",), ("丙", "丁", "甲")))
        self.assertEqual((moved.rank("乙"), moved.rank("甲")), (1, 3))
        self.assertIs(t.with_tier("乙", "core"), t)  # 已经是这一档：不变
        self.assertEqual(t.with_tier("乙", "core"), t)
        gone = t.with_tier("丙", None)  # 取消标记
        self.assertEqual((gone.order("first"), gone.rank("丁"), gone.rank("丙")), (("丁",), 1, None))
        self.assertIs(t.with_tier("没标的", None), t)
        # 改回原来的档位：排到最后（不回到原来的顺位）
        back = moved.with_tier("甲", "core")
        self.assertEqual(back.order("core"), ("乙", "甲"))
        self.assertNotEqual(back, t)
        self.assertEqual(t.order("core"), ("甲", "乙"))  # 原来的不变
        with self.assertRaises(ValueError):
            t.with_tier("甲", "必选")

    def test_moved(self):
        """移到某档某位：本档里上下挪、跨档放到指定位置、没标过的筹码直接放进去；位置超出范围时放到最前或最后。"""
        t = S.Tiers([("A", "first"), ("B", "first"), ("C", "first"), ("D", "first"), ("X", "core")])
        self.assertEqual(t.moved("A", "first", 3).order("first"), ("B", "C", "A", "D"))
        self.assertEqual(t.moved("D", "first", 1).order("first"), ("D", "A", "B", "C"))
        self.assertEqual(t.moved("B", "first", 2), t)  # 原位
        self.assertEqual(t.moved("A", "first", 99).order("first"), ("B", "C", "D", "A"))
        self.assertEqual(t.moved("D", "first", 0).order("first"), ("D", "A", "B", "C"))
        self.assertEqual(t.moved("D", "first", -5).order("first"), ("D", "A", "B", "C"))
        across = t.moved("C", "core", 1)  # 跨档放到指定位置
        self.assertEqual((across.order("core"), across.order("first")), (("C", "X"), ("A", "B", "D")))
        new = t.moved("新", "first", 2)  # 没标过的
        self.assertEqual((new.order("first"), new.place("新")), (("A", "新", "B", "C", "D"), S.Mark("first", 2)))
        self.assertEqual(t.moved("X", "avoid", 1).order("avoid"), ("X",))
        with self.assertRaises(ValueError):
            t.moved("A", None, 1)

    def test_shifted(self):
        """上移下移：只在本档里挪，到头为止；未标记的不变。"""
        t = S.Tiers([("A", "core"), ("B", "core"), ("C", "core"), ("Z", "first")])
        self.assertEqual(t.shifted("B", -1).order("core"), ("B", "A", "C"))
        self.assertEqual(t.shifted("B", 1).order("core"), ("A", "C", "B"))
        self.assertEqual(t.shifted("A", -1), t)
        self.assertEqual(t.shifted("C", 1), t)
        self.assertEqual(t.shifted("A", 2).order("core"), ("B", "C", "A"))
        self.assertEqual(t.shifted("A", 10).order("core"), ("B", "C", "A"))
        self.assertEqual(t.shifted("Z", -1), t)  # 一档只有一个
        self.assertIs(t.shifted("没标的", 1), t)

    def test_scheme_keeps_ranks(self):
        """方案里的 tiers 带顺位：只改顺位也算方案变了；marked 按顺位；传普通 dict 时同一档按 dict 里的先后。"""
        s = S.Scheme(hero="芬妮", name="甲", tiers={"缓冲盾牌": "core", "医疗箱-完备治疗": "core", "大碗炖肉": "first"})
        self.assertIsInstance(s.tiers, S.Tiers)
        self.assertEqual(s.marked("core"), ["缓冲盾牌", "医疗箱-完备治疗"])
        swapped = replace(s, tiers=s.tiers.moved("医疗箱-完备治疗", "core", 1))
        self.assertNotEqual(swapped, s)
        self.assertNotEqual(hash(swapped), hash(s))
        self.assertEqual(swapped.marked("core"), ["医疗箱-完备治疗", "缓冲盾牌"])
        self.assertEqual(swapped.counts(), s.counts())
        self.assertEqual(replace(swapped, tiers=swapped.tiers.shifted("医疗箱-完备治疗", 1)), s)


# ------------------------------------------------------------ 数据结构


class SchemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = S.get_catalog(cls.db)

    def test_cleaning_and_validation(self):
        s = S.Scheme(hero="芬妮", name=" 【精华】治愈" + chr(7) + "\N{ZERO WIDTH SPACE}坦克\n第二行超长超长 ",
                     note="第一行\r\n第二行\t" + "说" * 70, author="作者名字很长很长很长很长很长",
                     tiers={"缓冲盾牌": "core"})
        self.assertEqual(s.name, "〔精华〕治愈坦克 第二行")
        self.assertEqual(len(s.name), S.NAME_MAX)
        self.assertEqual(len(s.note), S.NOTE_MAX)
        self.assertTrue(s.note.startswith("第一行 第二行 说"))
        self.assertEqual(len(s.author), S.AUTHOR_MAX)
        self.assertEqual(S.Scheme(hero="芬妮", name=" \n ").name, S.UNNAMED)
        # 标的地图存成元组：方案不可变，传进来的列表在外面改了，存着的方案不跟着变
        maps = ["魔法学院"]
        s = S.Scheme(hero="芬妮", maps=maps)
        maps.append("园林中庭")
        self.assertEqual((s.maps, S.Scheme(hero="芬妮").maps), (("魔法学院",), ()))
        self.assertEqual(S.clean_text("<b>粗体</b>&amp;", 20), ("<b>粗体</b>&amp;", False))  # 网页标记原样保留，显示时由界面转义
        # 档位写错、适用地图传成一个字符串（会拆成一个个字）或编号：都报 ValueError，replace 时也一样
        bads = ({"tiers": {"缓冲盾牌": "必选"}}, {"tiers": {"缓冲盾牌": None}}, {"maps": "魔法学院"}, {"maps": (5,)},
                {"maps": ("魔法学院", None)})
        for bad in bads:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                S.Scheme(hero="芬妮", **bad)
            with self.subTest(bad=bad, via="replace"), self.assertRaises(ValueError):
                replace(s, **bad)

    def test_equality_and_hash(self):
        a = S.Scheme(hero="芬妮", name="甲", tiers={"缓冲盾牌": "core", "大碗炖肉": "first"})
        b = S.Scheme(hero="芬妮", name="甲", tiers={"大碗炖肉": "first", "缓冲盾牌": "core"})
        self.assertEqual(a, b)  # 不同档之间的先后不算
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(a.tiers, {"缓冲盾牌": "core", "大碗炖肉": "first"})
        self.assertNotEqual(a, replace(a, tiers={"缓冲盾牌": "first", "大碗炖肉": "first"}))
        self.assertEqual(len({a, b, replace(a, name="乙")}), 2)
        self.assertEqual(a.counts(), {"core": 1, "first": 1, "second": 0, "avoid": 0})
        self.assertEqual(a.marked("first"), ["大碗炖肉"])
        with self.assertRaises(Exception):
            a.name = "改"  # 不可修改

    def test_no_hand_picked_schools(self):
        """Scheme 没有手选的目标流派、仅限目标流派这两项（构造时传入报 TypeError），目标流派由标记推出来
        （target_schools）；也没有手选目标流派的上限 SCHOOLS_MAX。"""
        for old in ({"schools": ("星光",)}, {"only_schools": True}):
            with self.subTest(old=old), self.assertRaises(TypeError):
                S.Scheme(hero="芬妮", **old)
        s = S.Scheme(hero="芬妮", name="甲", tiers={"缓冲盾牌": "core", "电流剑": "first", "标靶": "second"})
        self.assertEqual({f.name for f in dataclasses.fields(S.Scheme)},
                         {"hero", "name", "tiers", "author", "note", "id", "stale", "copied_from_default", "maps"})
        self.assertFalse(hasattr(s, "schools"))
        self.assertFalse(hasattr(s, "only_schools"))
        self.assertEqual(S.target_schools(self.db, s), ("治愈", "充能"))
        self.assertFalse(hasattr(S, "SCHOOLS_MAX"))

    def test_scheme_from_default(self):
        """复制默认推荐：玩家思路里不带条件的四档照原样换成方案档位，带条件规则的筹码不标；同一档按编号（默认推荐列表的
        固定顺序）定顺位；攻略列作备选的流派都算列进方案，不砍。适配表现在没有角色写玩家思路层，复制出来的方案是空的；
        机制用测试用的默认推荐档位测（tests/support/player_layer.toml）。"""
        synergy = tomllib.loads((paths.data_dir() / "synergy.toml").read_text(encoding="utf-8"))
        self.assertEqual([p["hero"] for p in synergy.get("player", [])], [])
        for hero in ("凛", "芬妮", "糖糖"):
            with self.subTest(hero=hero):
                self.assertEqual(S.scheme_from_default(self.db, hero).tiers, {})
                self.assertEqual(S.default_schools(self.db, hero), self.db.hero_synergy[hero].schools)
        # 资料库（换上测试用的档位）里有玩家思路的角色，正好是那份档位里写到的角色（同一角色写了几段时合并成一个）
        from tests.support.player_layer import PLAYER_FILE, with_player_layer
        db = with_player_layer(self.db)
        listed = {p["hero"] for p in tomllib.loads(PLAYER_FILE.read_text(encoding="utf-8"))["player"]}
        self.assertTrue(listed)
        self.assertEqual(set(db.player_synergy), listed)
        more_than_two = []
        for hero, ps in db.player_synergy.items():
            with self.subTest(hero=hero):
                s = S.scheme_from_default(db, hero, today=NOW.date())
                conditional = {c for r in ps.rules for c in r.chips}
                expected = {c: t for c, t in ps.tiers.items() if c not in conditional}
                self.assertEqual(dict(s.tiers), expected)
                self.assertEqual((s.name, s.copied_from_default, s.id), (S.DEFAULT_COPY_NAME, "2026-10-05", ""))
                self.assertEqual(s.tiers, S.default_marks(db, hero))
                for tier in TIERS:  # 顺位：同一档按编号
                    codes = [self.cat.code_of[c] for c in s.tiers.order(tier)]
                    self.assertEqual(codes, sorted(codes))
                # 列进方案的流派连理由一起查：每个列进的流派都有标了可选及以上的筹码，没列进的一个都没有
                reasons = {sc: sorted(c for c, t in expected.items() if t != "avoid" and db.chips[c].school == sc)
                           for sc in SCHOOLS}
                self.assertEqual(S.listed_schools(db, s), tuple(sc for sc in SCHOOLS if reasons[sc]))
                targets = {db.chips[c].school for c, t in expected.items() if t in ("core", "first")} - {None}
                self.assertEqual(set(S.target_schools(db, s)), targets)
                if len(S.listed_schools(db, s)) > 2:
                    more_than_two.append(hero)
        # 备选流派照留：6 个角色的副本列进了三个以上流派（攻略本来就这么写）
        self.assertGreaterEqual(len(more_than_two), 5)
        rin = S.scheme_from_default(db, "凛")
        self.assertEqual(rin.tiers["大背包"], "core")
        self.assertEqual(rin.marked("core"), ["大背包", "魔法箭袋", "忍术飞镖"])
        self.assertEqual(rin.marked("avoid"), ["银行卡-余额少", "银行卡-余额多", "银行卡-用不完"])
        self.assertEqual(S.scheme_from_default(db, "芬妮").tiers,
                         {"银行卡-用不完": "core", "紫色飞星": "core", "金色飞星": "core"})
        kaede = S.scheme_from_default(db, "姬梦枫")  # 四个流派都列进了：没有被锁的
        self.assertEqual((S.listed_schools(db, kaede), S.locked_schools(db, kaede)), (SCHOOLS, ()))
        sykes = S.scheme_from_default(db, "赛克斯")  # 列进三个：锁住剩下的充能
        self.assertEqual((S.listed_schools(db, sykes), S.locked_schools(db, sykes)),
                         (("星光", "治愈", "标记"), ("充能",)))
        self.assertEqual(S.scheme_from_default(db, "糖糖").tiers, {})  # 没有玩家思路的角色
        self.assertEqual(S.default_marks(db, "糖糖"), {})
        self.assertEqual(S.default_schools(db, "芬妮"), db.player_synergy["芬妮"].schools)

    def test_unique_name(self):
        self.assertEqual(S.unique_name("治愈坦克", ["治愈坦克"]), "治愈坦克（2）")
        self.assertEqual(S.unique_name("治愈坦克", ["治愈坦克", "治愈坦克（2）"]), "治愈坦克（3）")
        self.assertEqual(S.unique_name("治愈坦克", ["别的"]), "治愈坦克")
        long = "一二三四五六七八九十一二"
        self.assertEqual(S.unique_name(long, [long]), "一二三四五六七八九（2）")


# ------------------------------------------------------------ 列进方案的流派、被锁成不选的流派、目标流派


class SchoolsTest(_Chips, unittest.TestCase):
    def test_school_of(self):
        """流派看资料库里筹码的 school：通用筹码、地图专属筹码、资料库里没有的筹码不属于任何流派。"""
        self.assertEqual(len(self.general) + len(self.map_only) + sum(len(v) for v in self.school.values()),
                         len(self.cat.markable))
        self.assertTrue(all(len(v) >= 8 for v in self.school.values()))
        for s in SCHOOLS:
            self.assertTrue(all(S.school_of(self.db, c) == s for c in self.school[s]))
        for c in (*self.general, *self.map_only, "不存在的筹码", None, 5):
            self.assertIsNone(S.school_of(self.db, c), c)

    def test_list_one_to_four_schools_and_leave(self):
        """列一、二、三、四个流派，再一个个退出：两个以上时其余流派被锁；退出后锁跟着变；标了又撤掉回到原样。"""
        star, heal, mark, charge = self.star, self.heal, self.mark, self.charge
        t = S.Tiers()
        self.assertEqual(self.schools(t), ((), (), ()))
        steps = [
            # （改动，列进，锁住，目标）
            ((heal[0], "first"), ("治愈",), (), ("治愈",)),
            ((mark[0], "second"), ("治愈", "标记"), ("星光", "充能"), ("治愈",)),  # 第二个流派：其余锁住
            ((star[0], "avoid"), ("治愈", "标记"), ("星光", "充能"), ("治愈",)),  # 被锁流派里手标不选：照旧锁着
            ((charge[0], "second"), ("治愈", "标记", "充能"), ("星光",), ("治愈",)),  # 确认列进第三个
            ((star[1], "core"), SCHOOLS, (), ("星光", "治愈")),  # 四个都列进：没有锁
            ((star[1], None), ("治愈", "标记", "充能"), ("星光",), ("治愈",)),  # 星光只剩不选：退出、又被锁
            ((charge[0], None), ("治愈", "标记"), ("星光", "充能"), ("治愈",)),
            ((mark[0], None), ("治愈",), (), ("治愈",)),  # 只剩一个：锁解除
            ((heal[0], None), (), (), ()),
        ]
        for (chip, tier), listed, locked, targets in steps:
            t = t.with_tier(chip, tier)
            with self.subTest(chip=chip, tier=tier):
                self.assertEqual(self.schools(t), (listed, locked, targets))
        self.assertEqual(t, {star[0]: "avoid"})  # 手标的不选一直留着；锁成的不选不存
        self.assertEqual(self.schools(t.with_tier(star[0], None)), ((), (), ()))

    def test_targets_and_lock_can_differ(self):
        """目标流派看必选、推荐，上锁看可选及以上：推荐都在星光、治愈只标了可选时，目标只有星光，治愈不会被锁。"""
        t = {self.star[0]: "first", self.heal[0]: "second", self.heal[1]: "second"}
        self.assertEqual(self.schools(t), (("星光", "治愈"), ("标记", "充能"), ("星光",)))
        # 只有可选：列进但不是目标
        self.assertEqual(self.schools({self.mark[0]: "second"}), (("标记",), (), ()))

    def test_extremes(self):
        """极端组合：全部必选、全部不选、全标、只标通用或地图专属、同一流派里四档都有、方案外流派只有不选标记。"""
        markable = self.cat.sort_chips(self.cat.markable)
        h = self.heal
        cases = {
            "全部必选": ({c: "core" for c in markable}, (SCHOOLS, (), SCHOOLS)),
            "全部不选": ({c: "avoid" for c in markable}, ((), (), ())),
            "全标": ({c: TIERS[i % 4] for i, c in enumerate(markable)}, (SCHOOLS, (), SCHOOLS)),
            "只标通用": ({c: "core" for c in self.general}, ((), (), ())),
            "只标地图专属": ({c: "first" for c in self.map_only}, ((), (), ())),
            "通用和地图专属": ({self.general[0]: "core", self.map_only[0]: "second", self.general[1]: "avoid"}, ((), (), ())),
            "同一流派四档都有": ({h[0]: "core", h[1]: "first", h[2]: "second", h[3]: "avoid"}, (("治愈",), (), ("治愈",))),
            "方案外流派只有不选": ({h[0]: "first", self.mark[0]: "second", self.star[0]: "avoid", self.star[1]: "avoid",
                             self.charge[0]: "avoid"}, (("治愈", "标记"), ("星光", "充能"), ("治愈",))),
            "一个流派加不选": ({h[0]: "core", self.star[0]: "avoid", self.mark[0]: "avoid"}, (("治愈",), (), ("治愈",))),
            "只有可选": ({c: "second" for c in (*self.star[:2], *self.charge[:2])}, (("星光", "充能"), ("治愈", "标记"), ())),
            "空": ({}, ((), (), ())),
        }
        for label, (marks, expected) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(self.schools(marks), expected)

    def test_marks_argument_kinds(self):
        """marks 可以是 Scheme、Tiers、映射、（筹码名, 档位）序列，或带 tiers 属性的对象：结果相同。"""
        pairs = [(self.heal[0], "core"), (self.mark[0], "second"), (self.star[0], "avoid"), ("不存在的筹码", "first")]
        expected = (("治愈", "标记"), ("星光", "充能"), ("治愈",))
        for marks in (S.Scheme(hero="芬妮", tiers=pairs), S.Tiers(pairs), dict(pairs), pairs, tuple(sorted(pairs)),
                      SimpleNamespace(tiers=tuple(pairs)), SimpleNamespace(tiers=dict(pairs))):
            with self.subTest(marks=type(marks).__name__):
                self.assertEqual(self.schools(marks), expected)
        self.assertEqual(self.schools(None), ((), (), ()))

    def test_lock_threshold_from_rules(self):
        """被锁的门槛是一局最多能进的流派数（rules.toml [pool] max_schools）。"""
        self.assertEqual(self.db.rules["pool"]["max_schools"], 2)
        three = replace(self.db, rules={**self.db.rules, "pool": {**self.db.rules["pool"], "max_schools": 3}})
        two = {self.heal[0]: "first", self.mark[0]: "second"}
        self.assertEqual(S.locked_schools(three, two), ())
        self.assertEqual(S.locked_schools(three, {**two, self.charge[0]: "second"}), ("星光",))


# ------------------------------------------------------------ 短字符串的编码


class CodecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = S.get_catalog()

    @staticmethod
    def code_of(*fields, version: int = S.SHARE_VERSION) -> str:
        """按位拼出编码部分（校验照规矩算），用来造不按规矩写的内容。version 为分享格式的版本（校验里带上它）。"""
        bits = S._Bits()
        for value, width in fields:
            bits.put(value, width)
        raw = bits.to_bytes()
        return S._b58encode(raw + S._check(raw, version))

    def test_fixed_samples_decode(self):
        """第 2 版的固定样本：以后任何版本都解出同样的内容（含顺位），再编码也得到同样的字符串。"""
        for text, hero_id, marks in SHORT_V2:
            with self.subTest(text=text):
                code = text.split("CA2-", 1)[1]
                payload = S.decode_payload(code)
                self.assertEqual(payload, S.ShortPayload(hero_id, marks))
                self.assertEqual(S.encode_payload(payload), code)
                item = S.parse_share(text, self.cat).items[0]
                self.assertEqual(item.problems, ())
                # 解出来只留能标档位的筹码：电击枪（84）、原初意识（87）10-15 起改为只靠畸变发放后不能再标，
                # 这两套固定样本的短字符串里仍带着它们（样本是那以前生成的），parse_share 会把这两张丢掉
                self.assertEqual([(self.cat.code_of[n], t) for n, t in item.scheme.tiers.items()],
                                 [m for m in marks if self.cat.name_of.get(m[0]) in self.cat.markable])

    def test_fixed_v3_samples_decode(self):
        """第 3 版的固定样本（带地图）：解出同样的内容，再编码也得到同样的字符串；解析出的方案带着地图名。样本是「CA3-」开头
        （分享方案的网站早先给出的就是这种），换成导出时写的「ACC3-」开头解出来一样。"""
        for text, hero_id, marks, maps in SHORT_V3:
            with self.subTest(text=text[:12]):
                code = text.split("CA3-", 1)[1]
                payload = S.decode_payload(code, S.SHARE_VERSION_MAPS)
                self.assertEqual(payload, S.ShortPayload(hero_id, marks, maps))
                self.assertEqual(S.encode_payload(payload), code)
                item = S.parse_share(text, self.cat).items[0]
                self.assertEqual(item.problems, ())
                self.assertEqual([self.cat.map_code_of[n] for n in item.scheme.maps], list(maps))
                self.assertEqual([(self.cat.code_of[n], t) for n, t in item.scheme.tiers.items()],
                                 [m for m in marks if self.cat.name_of.get(m[0]) in self.cat.markable])
                acc = S.parse_share(text.replace("CA3-", "ACC3-"), self.cat).items[0]
                self.assertEqual((acc.scheme, acc.problems), (item.scheme, ()))

    def test_short_prefixes(self):
        """短字符串的开头：导出时第 2、3 版都写「ACC」（ACC2-、ACC3-）；
        导入时 ACC、CA 两种开头哪个版本都认，不分大小写、全角半角，中间可以有空格；开头不算进校验。别的字母开头不认。"""
        s = S.Scheme(hero="芬妮", name="甲", tiers=[("缓冲盾牌", "core")], maps=("魔法学院",))
        short = S.to_short(s, self.cat)
        self.assertTrue(short.startswith("【甲】ACC3-"), short)
        plain = S.to_short(S.Scheme(hero="芬妮", name="乙", tiers=[("缓冲盾牌", "core")]), self.cat)
        self.assertTrue(plain.startswith("【乙】ACC2-"), plain)
        v3, v2 = short.split("ACC3-", 1)[1], plain.split("ACC2-", 1)[1]
        for head in ("ACC3-", "acc3-", "Acc 3 -", "ＡＣＣ３－", "CA3-", "ca3－"):
            with self.subTest(head=head):
                item = S.parse_share("【甲】" + head + v3, self.cat).items[0]
                self.assertEqual(item.problems, ())
                self.assertEqual((item.scheme.hero, item.scheme.maps), ("芬妮", ("魔法学院",)))
        for head in ("ACC2-", "ａｃｃ２-", "CA2-"):
            with self.subTest(head=head):
                item = S.parse_share("【乙】" + head + v2, self.cat).items[0]
                self.assertEqual((item.problems, item.scheme.maps), ((), ()))
        # 比现在新的版本，两种开头都报来自更新版本
        for head in ("ACC4-", "CA4-"):
            with self.subTest(head=head):
                item = S.parse_share("【新】" + head + v3, self.cat).items[0]
                self.assertEqual([p.kind for p in item.problems], [S.PROBLEM_NEWER])
        # 别的字母开头不是分享码
        for head in ("AC3-", "CC3-", "AAC3-"):
            with self.subTest(head=head):
                self.assertEqual(S.parse_share("【甲】" + head + v3, self.cat).items, ())

    def test_v3_rejections(self):
        """第 3 版：一张地图都没标、最大编号那一位是 0（包括写了最大编号、每一位却都是 0）、补齐的位不是 0 都报编码不对，
        粘贴时这一套报编码不对、不抛出别的异常；第 2 版的编码改成 CA3-（反过来也一样）校验不符。"""
        good = self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (1, 6), (1, 1), version=S.SHARE_VERSION_MAPS)
        self.assertEqual(S.decode_payload(good, S.SHARE_VERSION_MAPS), S.ShortPayload(102, ((34, "core"),), (1,)))
        bad = [
            self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (0, 6), version=S.SHARE_VERSION_MAPS),  # 没标地图
            self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (3, 6), (1, 1), (0, 1), (0, 1),
                         version=S.SHARE_VERSION_MAPS),  # 最大编号那一位是 0
            self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (3, 6), (0, 1), (0, 1), (0, 1),
                         version=S.SHARE_VERSION_MAPS),  # 写了最大编号，每一位却都是 0
            self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (1, 6), (0, 1),
                         version=S.SHARE_VERSION_MAPS),  # 同上，最大编号为 1
            self.code_of((102, 10), (0, 4), (1, 7), (34, 8), (0, 2), (1, 6), (1, 1), (1, 1),
                         version=S.SHARE_VERSION_MAPS),  # 补齐的位不是 0
        ]
        for code in bad:
            with self.subTest(code=code), self.assertRaises(S.ShareError) as e:
                S.decode_payload(code, S.SHARE_VERSION_MAPS)
            self.assertEqual(e.exception.kind, S.PROBLEM_BAD_CODE)
            item = S.parse_share("【坏】CA3-" + code, self.cat).items[0]
            self.assertEqual((item.scheme, [p.kind for p in item.problems]), (None, [S.PROBLEM_BAD_CODE]))
        v2 = S.encode_payload(S.ShortPayload(102, ((34, "core"),)))
        v3 = S.encode_payload(S.ShortPayload(102, ((34, "core"),), (1,)))
        for text in ("【改版本】CA3-" + v2, "【改版本】CA2-" + v3):
            item = S.parse_share(text, self.cat).items[0]
            self.assertIsNone(item.scheme)
            self.assertEqual([p.kind for p in item.problems], [S.PROBLEM_BAD_CODE])

    def test_v3_unknown_map_codes_ignored(self):
        """编码里地图编号本版本资料库没有（可能来自更新版本）：忽略这一张，记一条问题，方案照常导入。"""
        code = S.encode_payload(S.ShortPayload(102, ((34, "core"),), (1, 40)))
        item = S.parse_share("【未知地图】CA3-" + code, self.cat).items[0]
        self.assertEqual(item.scheme.maps, ("龙宫游乐园",))
        self.assertEqual([p.kind for p in item.problems], [S.PROBLEM_UNKNOWN_MAP_CODES])
        self.assertEqual(item.problems[0].count, 1)

    def test_payload_order_is_rank(self):
        """marks 的先后就是顺位：只按档位排（稳定排序），同一档保持传入的先后；两种写法都带顺位。"""
        mixed = ((40, "first"), (34, "core"), (38, "first"), (12, "core"), (25, "avoid"), (3, "first"))
        canon = ((34, "core"), (12, "core"), (40, "first"), (38, "first"), (3, "first"), (25, "avoid"))
        self.assertEqual(S.decode_payload(S.encode_payload(S.ShortPayload(102, mixed))), S.ShortPayload(102, canon))
        for codes in itertools.permutations((5, 9, 13, 60)):  # 同一档四个筹码的 24 种先后
            for tier in ("core", "avoid"):
                marks = tuple((c, tier) for c in codes)
                self.assertEqual(S.decode_payload(S.encode_payload(S.ShortPayload(102, marks))).marks, marks)
        # 逐个写法（标得多时更短）也带顺位：全标、全部必选两个固定样本就是逐个写法
        for text, _, marks in SHORT_V2[10:]:
            self.assertEqual(S._Reader(S._b58decode(text.split("CA2-", 1)[1])).take(14) & 0b1111, S._FLAG_FULL)

    def test_small_codes_exhaustive(self):
        """编号 1～6 里任取 3 个，每个四档之一，所有先后：编码再解码都还原（按档位排好的先后）。"""
        n = 0
        for codes in itertools.combinations(range(1, 7), 3):
            for tiers in itertools.product(TIERS, repeat=3):
                for order in itertools.permutations(range(3)):
                    marks = tuple((codes[i], tiers[i]) for i in order)
                    canon = tuple(sorted(marks, key=lambda m: TIERS.index(m[1])))
                    self.assertEqual(S.decode_payload(S.encode_payload(S.ShortPayload(102, marks))),
                                     S.ShortPayload(102, canon))
                    n += 1
        self.assertEqual(n, 20 * 64 * 6)

    def test_reserved_flags_mean_newer_version(self):
        code = self.code_of((102, 10), (0b0010, 4), (0, 7))
        with self.assertRaises(S.ShareError) as e:
            S.decode_payload(code)
        self.assertEqual(e.exception.kind, S.PROBLEM_NEWER)
        result = S.parse_share("【新】CA2-" + code, self.cat)
        self.assertEqual([p.kind for p in result.items[0].problems], [S.PROBLEM_NEWER])

    def test_non_canonical_payloads_rejected(self):
        """校验对得上、但不是按规矩写出的内容也不认：列表写法档位没按先后、编号重复、编号 0、多出来的位、补齐的位不是 0、
        角色编号 0；逐个写法最大编号那一位没标、排列序号超出范围。"""
        bad = [
            self.code_of((102, 10), (0, 4), (2, 7), (40, 8), (1, 2), (34, 8), (0, 2)),  # 推荐写在必选前面
            self.code_of((102, 10), (0, 4), (2, 7), (34, 8), (0, 2), (34, 8), (1, 2)),  # 同一个编号两次
            self.code_of((102, 10), (0, 4), (1, 7), (0, 8), (0, 2)),  # 编号 0
            self.code_of((102, 10), (0, 4), (0, 7), (1, 7)),  # 多出来一个字节
            self.code_of((102, 10), (0, 4), (0, 7), (1, 3)),  # 补齐的位不是 0
            self.code_of((0, 10), (0, 4), (0, 7)),  # 角色编号 0
            self.code_of((102, 10), (1, 4), (3, 8), (5, 7)),  # 逐个写法：最大编号那一位没标
            self.code_of((102, 10), (1, 4), (3, 8), (124, 7), (7, 3)),  # 逐个写法：三个不选的排列只有 6 种，序号 7 超出
            self.code_of((102, 10), (1, 4), (0, 8)),  # 逐个写法：最大编号 0
        ]
        for code in bad:
            with self.subTest(code=code), self.assertRaises(S.ShareError) as e:
                S.decode_payload(code)
            self.assertEqual(e.exception.kind, S.PROBLEM_BAD_CODE)
        # 对照：同样的写法，按规矩写时能解出来
        self.assertEqual(S.decode_payload(self.code_of((102, 10), (1, 4), (3, 8), (124, 7), (5, 3))).marks,
                         ((3, "avoid"), (2, "avoid"), (1, "avoid")))

    def test_checksum_covers_version(self):
        """校验连同版本号一起算：同样的内容按第 1 版的校验写出来（或者把别的版本的编码改写成「CA2-」）报校验不符。"""
        bits = S._Bits()
        for value, width in ((102, 10), (0, 4), (1, 7), (34, 8), (0, 2)):
            bits.put(value, width)
        raw = bits.to_bytes()
        good = S._b58encode(raw + S._check(raw))
        self.assertEqual(S.decode_payload(good), S.ShortPayload(102, ((34, "core"),)))
        other = S._b58encode(raw + S._crc16(bytes((1,)) + raw).to_bytes(2, "big"))
        plain = S._b58encode(raw + S._crc16(raw).to_bytes(2, "big"))
        for code in (other, plain, "APQ6U9miaVca6zfqWYM"):  # 最后一个是第 1 版的「治愈坦克」
            with self.subTest(code=code), self.assertRaises(S.ShareError) as e:
                S.decode_payload(code)
            self.assertEqual(e.exception.kind, S.PROBLEM_BAD_CODE)

    def test_every_corruption_rejected(self):
        """短字符串的编码部分每个位置换一个字符、删一个字符、加一个字符：全部报校验不符、不导入、不崩溃。"""
        for text in (SHORT_V2[0][0], SHORT_HEAL, SHORT_V2[10][0], SHORT_V3[0][0], SHORT_V3[1][0]):
            marker = "CA3-" if "CA3-" in text else "CA2-"
            head, code = text.split(marker, 1)
            head += marker
            variants = []
            for i in range(len(code)):
                variants += [head + code[:i] + c + code[i + 1:] for c in S.B58 if c != code[i]]
                variants.append(head + code[:i] + code[i + 1:])
            for i in range(len(code) + 1):
                variants += [head + code[:i] + c + code[i:] for c in S.B58]
            accepted = []
            for v in variants:
                result = S.parse_share(v, self.cat)
                self.assertEqual(len(result.items), 1)
                if result.schemes:
                    accepted.append(v)
                else:
                    self.assertEqual([p.kind for p in result.items[0].problems], [S.PROBLEM_BAD_CODE])
            with self.subTest(text=text):
                self.assertEqual(accepted, [])

    def test_every_prefix_rejected(self):
        """短字符串的每个前缀：都不导入、不崩溃；带上了「CA2-」「CA3-」的报校验不符。"""
        for text in (SHORT_V2[3][0], SHORT_V2[10][0], SHORT_V3[0][0], SHORT_V3[1][0]):
            marker = (text.index("CA3-") if "CA3-" in text else text.index("CA2-")) + 4
            for n in range(len(text)):
                with self.subTest(text=text, n=n):
                    result = S.parse_share(text[:n], self.cat)
                    self.assertEqual(result.schemes, [])
                    if n >= marker:
                        self.assertEqual([p.kind for p in result.items[0].problems], [S.PROBLEM_BAD_CODE])
                    else:
                        self.assertEqual(result.items, ())

    def test_encode_rejects_out_of_range(self):
        for payload in (S.ShortPayload(0), S.ShortPayload(1024), S.ShortPayload(102, ((0, "core"),)),
                        S.ShortPayload(102, ((256, "core"),)), S.ShortPayload(102, ((5, "x"),)),
                        S.ShortPayload(102, ((5, "core"), (5, "first"))), S.ShortPayload(102, ((True, "core"),))):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                S.encode_payload(payload)


# ------------------------------------------------------------ 分享往返


def random_scheme(rng: random.Random, db, cat, n_marks: int | None = None) -> S.Scheme:
    """随机方案（顺位也随机）：名称、说明、作者里混入网页标记、全角半角分隔符、控制字符、【】等。"""
    pool = ("治愈坦克星光标记充能速攻流派方案测试abcXYZ0129 <>&\"'/｜|：:、，,【】〔〕[]()（）·-—_~!?！？"
            + chr(7) + chr(9) + "\n\N{ZERO WIDTH SPACE}\N{IDEOGRAPHIC SPACE}")
    heroes = [h for h in db.heroes.values() if h.id]

    def text(limit: int) -> str:
        return "".join(rng.choice(pool) for _ in range(rng.randint(0, limit)))

    while True:
        marks = sorted(cat.markable)
        k = rng.randint(0, len(marks)) if n_marks is None else n_marks
        s = S.Scheme(
            hero=rng.choice(heroes).name, name=text(14), author=text(14), note=text(64),
            tiers=[(c, rng.choice(TIERS)) for c in rng.sample(marks, k)],
        )
        # 说明、作者里写出「｜作者：」「｜说明：」或另一套的开头时，可读文字分不清各段（见 parse_share），不在往返的范围里
        if not any(p.search(x) for x in (s.note, s.author) for p in (S._AUTHOR_FIELD, S._NOTE_FIELD, S._START)):
            return s


class RoundTripTest(_Chips, unittest.TestCase):
    def assert_round_trip(self, s: S.Scheme, maps=None):
        text, short = S.to_text(s, maps=maps), S.to_short(s, self.cat, maps=maps)
        for out, full in ((text, True), (short, False)):
            result = S.parse_share(out, self.cat)
            self.assertEqual(len(result.items), 1, out)
            item = result.items[0]
            self.assertEqual(item.problems, (), out)
            self.assertEqual(content(item.scheme, full), content(s, full), out)
            self.assertEqual(item.name, s.name)
        marker = "ACC3-" if "ACC3-" in short else "ACC2-"
        code = short.split(marker, 1)[1]
        self.assertLessEqual(len(code), 117, short)  # 能标的筹码全部必选时最长（顺位的排列最多；带地图的另算）
        return text, short

    def test_copies_of_default(self):
        """测试用的默认推荐档位里每个有玩家思路的角色一套「复制默认推荐」：两种格式导出再导入内容（含顺位）完全相同；
        短字符串 26～51 个字符（标得最多的姬梦枫 20 个筹码，51 个字符）。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        lengths = []
        for hero in db.player_synergy:
            with self.subTest(hero=hero):
                _, short = self.assert_round_trip(S.scheme_from_default(db, hero))
                lengths.append(len(short))
        self.assertLessEqual(max(lengths), 70)
        self.assertGreaterEqual(min(lengths), 20)

    def test_random_200(self):
        """随机 200 套（含 0 个和全部标上，顺位随机）：两种格式往返内容完全相同。"""
        rng = random.Random(20261006)
        samples = [random_scheme(rng, self.db, self.cat, n) for n in (0, len(self.cat.markable), len(self.cat.markable))]
        samples += [random_scheme(rng, self.db, self.cat) for _ in range(197)]
        self.assertEqual(len(samples), 200)
        for i, s in enumerate(samples):
            with self.subTest(i=i):
                self.assert_round_trip(s)

    def test_delimiters_in_note_and_author(self):
        """说明、作者里写出【】｜（分段标记或另一套的开头）：导出换成近似字、导入换回来，内容完整还原。"""
        cases = [
            S.Scheme(hero="芬妮", name="甲", note="【分享】筹码方案 v2 之类", author="乙"),
            S.Scheme(hero="芬妮", name="甲的", note="前面｜作者：后面", author=""),
            S.Scheme(hero="芬妮", name="甲的（2）", note="说明正文", author="某｜说明：某某"),
            S.Scheme(hero="芬妮", name="甲", note="方括号【】和竖线｜都写进去", author="作【者】｜"),
        ]
        for s in cases:
            with self.subTest(note=s.note, author=s.author):
                text = S.to_text(s)
                self.assertNotIn("【", text[text.index("】") + 1:], text)  # 名称之外没有【（都换成近似字）
                item = S.parse_share(text, self.cat).items[0]
                self.assertEqual(item.problems, ())
                self.assertEqual((item.scheme.note, item.scheme.author), (s.note, s.author))
                self.assertEqual(item.scheme.tiers, s.tiers)

    def test_extremes(self):
        """极端组合两种格式往返（含顺位）：全部必选（从大编号往小排）、全部不选、全标、只标通用或地图专属、同一流派四档都有、
        方案外流派只有不选、空方案。全部必选最长（编码 117 个字符），全标 90 个。"""
        markable = self.cat.sort_chips(self.cat.markable)
        h = self.heal
        cases = {
            "全部必选": [(c, "core") for c in reversed(markable)],
            "全部不选": [(c, "avoid") for c in markable],
            "全标": [(c, TIERS[self.cat.code_of[c] % 4]) for c in reversed(markable)],  # 同固定样本 ALL_MARKED
            "只标通用": [(c, "first") for c in self.general],
            "只标地图专属": [(c, "second") for c in reversed(self.map_only)],
            "同一流派四档都有": [(h[3], "avoid"), (h[2], "second"), (h[1], "first"), (h[0], "core"), (h[4], "core")],
            "方案外流派只有不选": [(h[0], "first"), (self.mark[0], "second"), (self.star[2], "avoid"), (self.star[0], "avoid")],
            "空": [],
        }
        lengths = {}
        for label, marks in cases.items():
            with self.subTest(label=label):
                s = S.Scheme(hero="米米", name=label, tiers=marks)
                _, short = self.assert_round_trip(s)
                lengths[label] = len(short.split("ACC2-", 1)[1])
        self.assertEqual((lengths["全部必选"], lengths["全标"]), (117, 90))

    def test_maps_round_trip(self):
        """打开写的开关时，带地图的方案两种格式往返内容完全相同（含地图）；没标地图的照写第 2 版。"""
        for maps in ((), ("龙宫游乐园",), ("龙宫游乐园", "魔法学院"), ("异变图书馆",)):
            with self.subTest(maps=maps):
                s = S.Scheme(hero="芬妮", name="甲", tiers=[("缓冲盾牌", "core")], maps=maps)
                text, short = self.assert_round_trip(s, maps=True)
                self.assertEqual("v3" in text, bool(maps))
                self.assertEqual("ACC3-" in short, bool(maps))
                self.assertEqual("ACC2-" in short, not maps)

    def test_write_maps_switch(self):
        """WRITE_MAPS 开着：标了地图的方案导出写第 3 版，没标的照写第 2 版。参数关掉时（maps=False，或开关关着）一律写
        第 2 版、不带地图。"""
        s = S.Scheme(hero="芬妮", name="甲", tiers=[("缓冲盾牌", "core")], maps=("魔法学院",))
        self.assertTrue(S.WRITE_MAPS)
        for kw in ({}, {"maps": True}):
            self.assertIn("【甲】ACC3-", S.to_short(s, self.cat, **kw))
            self.assertEqual(S.to_text(S.Scheme(hero="芬妮", name="甲", maps=("魔法学院",)), **kw),
                             "【甲】筹码方案 v3｜芬妮｜地图：魔法学院")
        with mock.patch.object(S, "WRITE_MAPS", False):
            plain = ((S.to_text(s), S.to_short(s, self.cat)),)
        for text, short in (*plain, (S.to_text(s, maps=False), S.to_short(s, self.cat, maps=False))):
            self.assertIn("筹码方案 v2", text)
            self.assertNotIn("地图", text)
            self.assertIn("ACC2-", short)
        empty = S.Scheme(hero="芬妮", name="空")
        for kw in ({}, {"maps": True}):
            self.assertIn("筹码方案 v2", S.to_text(empty, **kw))
            self.assertIn("ACC2-", S.to_short(empty, self.cat, **kw))
        # 没有编号的地图（已经结束的运动场）不能写进方案
        with self.assertRaises(ValueError):
            S.to_short(S.Scheme(hero="芬妮", name="甲", maps=("海选赛运动场",)), self.cat, maps=True)

    def test_text_format(self):
        """可读文字的写法：每一档按顺位列出，没有的项不写；不写目标流派、仅限目标流派；短字符串只有数字和字母、不含说明和作者。"""
        s = S.Scheme(hero="芬妮", name="治愈坦克", note="说明文字", author="某某",
                     tiers=[("医疗箱-完备治疗", "core"), ("缓冲盾牌", "core"), ("额外电池", "second"), ("大碗炖肉", "first"),
                            ("星币锤", "avoid")])
        self.assertEqual(S.to_text(s),
                         "【治愈坦克】筹码方案 v2｜芬妮｜必选：医疗箱-完备治疗、缓冲盾牌｜推荐：大碗炖肉｜可选：额外电池｜"
                         "不选：星币锤｜说明：说明文字｜作者：某某")
        self.assertEqual(S.to_text(S.Scheme(hero="芬妮", name="空")), "【空】筹码方案 v2｜芬妮")
        # 固定字样里的版本号和解析时认的版本一致
        self.assertIn(f"v{S.SHARE_VERSION}", S.TEXT_HEAD)
        self.assertIn(f"ACC{S.SHARE_VERSION}-", S.SHORT_TEXT)
        self.assertEqual(S.SHARE_VERSION, 2)
        short = S.to_short(s, self.cat)
        self.assertRegex(short, r"^【治愈坦克】ACC2-[1-9A-HJ-NP-Za-km-z]+$")  # 不含说明和作者，只有数字和字母
        with self.assertRaises(ValueError):
            S.to_short(S.Scheme(hero="不存在的角色"), self.cat)
        with self.assertRaises(ValueError):
            S.to_short(S.Scheme(hero="芬妮", tiers={"不存在的筹码": "core"}), self.cat)


# ------------------------------------------------------------ 宽容解析和安全


class ParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cat = S.get_catalog(cls.db)

    def kinds(self, item) -> list[str]:
        return [p.kind for p in item.problems]

    def test_fixed_text_samples(self):
        """第 2 版可读文字的固定样本：以后任何版本都解出同样的内容（按编号比，改名不影响；每一档的先后就是顺位）。"""
        for text, hero, note, author, ranked in TEXT_V2:
            with self.subTest(text=text[:20]):
                item = S.parse_share(text, self.cat).items[0]
                self.assertEqual(item.problems, ())
                s = item.scheme
                self.assertEqual((s.hero, s.note, s.author), (hero, note, author))
                got = {t: tuple(self.cat.code_of[n] for n in s.tiers.order(t)) for t in TIERS}
                self.assertEqual({t: v for t, v in got.items() if v}, ranked)
                self.assertEqual(s.tiers, by_codes(self.cat, ranked))
                self.assertEqual(S.to_text(s), text)  # 再生成也是同样的文字

    def test_fixed_v3_text_samples(self):
        """第 3 版可读文字：认「地图」一段（简体、繁体、几种分隔符、写了两段都认，地图按编号排）；没写「地图」算通用。
        按简体名、常规写法写出的样本，再生成也是同样的文字。"""
        for text, hero, maps, ranked in TEXT_V3:
            with self.subTest(text=text[:16]):
                item = S.parse_share(text, self.cat).items[0]
                self.assertEqual(item.problems, ())
                s = item.scheme
                self.assertEqual((s.hero, s.maps), (hero, maps))
                self.assertEqual(s.tiers, by_codes(self.cat, ranked))
        for i in (0, 1, 4):
            text = TEXT_V3[i][0]
            item = S.parse_share(text, self.cat).items[0]
            self.assertEqual(S.to_text(item.scheme, maps=True), text)

    def test_text_v3_unknown_maps(self):
        """只认有编号的地图、名称要精确相同：「图书馆」这样的半截不算（不按包含匹配认成异变图书馆）；运动场的名字、
        写错的名字都忽略、记一条问题，方案照常导入。"""
        text = ("【地图错】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图：海选赛运动场、不存在的地图、图书馆")
        item = S.parse_share(text, self.cat).items[0]
        self.assertEqual(item.scheme.maps, ())
        self.assertEqual([p.kind for p in item.problems], [S.PROBLEM_UNKNOWN_MAP_NAMES])
        self.assertEqual(item.problems[0].names, ("海选赛运动场", "不存在的地图", "图书馆"))

    def test_text_v3_names_cap(self):
        """一套里最多认 200 个名称，地图名和筹码名合起来算：正好 200 个照常导入（同一张地图写了几遍只标一次），多一个按
        格式不正确；全是认不出的地图名时也一样，问题里不会列出成百上千个名字。"""
        head = "【多图】筹码方案 v3｜芬妮｜必选：缓冲盾牌｜地图："
        fits = S.parse_share(head + "、".join(["龙宫游乐园"] * 199), self.cat).items[0]
        self.assertEqual((fits.scheme.maps, self.kinds(fits)), (("龙宫游乐园",), []))
        over = S.parse_share(head + "、".join(["龙宫游乐园"] * 200), self.cat).items[0]
        self.assertEqual((over.scheme, self.kinds(over)), (None, [S.PROBLEM_BAD_TEXT]))
        unknown = "【乱图】筹码方案 v3｜芬妮｜地图：" + "、".join(f"地图{i}" for i in range(201))
        self.assertEqual(self.kinds(S.parse_share(unknown, self.cat).items[0]), [S.PROBLEM_BAD_TEXT])

    def test_text_and_short_agree(self):
        """同一套方案的两种固定样本内容相同。"""
        pairs = ((TEXT_V2[0][0], SHORT_V2[0][0]), (TEXT_V2[1][0], SHORT_V2[1][0]), (TEXT_HEAL, SHORT_HEAL),
                 (TEXT_V2[3][0], SHORT_MARK), (TEXT_V2[4][0], SHORT_V2[9][0]), (TEXT_V2[5][0], SHORT_V2[8][0]))
        for text, short in pairs:
            with self.subTest(text=text[:20]):
                a, b = S.parse_share(text + "\n" + short, self.cat).schemes
                self.assertEqual(content(a, False), content(b, False))

    def test_found_in_surrounding_text(self):
        """前后带别的文字、换行、[]、多余空格、一次多套（同一行也行），都找出来；有问题的那套单独报，其余照常。"""
        broken = SHORT_HEAL[:-1] + ("2" if SHORT_HEAL[-1] != "2" else "3")
        paste = ("大家好，分享两套：\r\n" + TEXT_HEAL + "\n\n  " + SHORT_HEAL.replace("【", "[").replace("】", "]")
                 + "  欢迎交流！" + broken.replace("治愈坦克", "坏的") + " 后面还有【转载】别的文字\n"
                 + "【 标记 充能 】  筹码方案  V2 ｜ 凛 ｜ 必选 : 忍术飞镖 , 魔法箭袋 ｜ 推荐 ： 电流剑 \n")
        result = S.parse_share(paste, self.cat)
        self.assertEqual([i.name for i in result.items], ["治愈坦克", "治愈坦克", "坏的", "标记 充能"])
        self.assertEqual([i.index for i in result.items], [1, 2, 3, 4])
        self.assertEqual([i.kind for i in result.items], [S.FORMAT_TEXT, S.FORMAT_SHORT, S.FORMAT_SHORT, S.FORMAT_TEXT])
        self.assertEqual(self.kinds(result.items[2]), [S.PROBLEM_BAD_CODE])
        self.assertEqual(len(result.schemes), 3)
        a, b, c = result.schemes
        self.assertEqual(content(a, False), content(b, False))  # 同一套的两种写法
        self.assertEqual((a.note, a.author), ("以治愈为主，星光只拿银行卡", "某某"))
        self.assertEqual((c.hero, c.tiers), ("凛", {"忍术飞镖": "core", "魔法箭袋": "core", "电流剑": "first"}))
        self.assertEqual(c.tiers.order("core"), ("忍术飞镖", "魔法箭袋"))
        start, end = result.items[1].span
        self.assertEqual(paste[start:end], SHORT_HEAL.replace("【", "[").replace("】", "]"))

    def test_hand_edited_text(self):
        """手改的可读文字：全角半角混用、筹码名少一个字、改名前的旧名、「称号 · 角色名」；认不出的单独列出，其余照常；
        顺位按写出的先后，认不出的不占顺位；同一档写了两段时接着排；同一个筹码写两次按先写的。"""
        text = ("【手改】筹码方案 ｖ２|古怪神探 · 芬妮|必选:医疗箱－完备治,缓冲盾|推荐：大碗炖、手电筒-一般、"
                "8面骰子、美工刀、完全不存在的筹码|不选:银行卡-用不完|推荐:可口糖果、缓冲盾牌|说明:  手改的  ")
        item = S.parse_share(text, self.cat).items[0]
        s = item.scheme
        self.assertEqual((s.hero, s.note), ("芬妮", "手改的"))
        self.assertEqual(s.tiers, {"医疗箱-完备治疗": "core", "缓冲盾牌": "core", "大碗炖肉": "first",
                                   "“8面”骰子": "first", "可口糖果": "first", "银行卡-用不完": "avoid"})
        self.assertEqual(s.tiers.place("“8面”骰子"), S.Mark("first", 2))
        self.assertEqual(self.kinds(item), [S.PROBLEM_REMOVED, S.PROBLEM_UNKNOWN_NAMES])
        self.assertEqual(item.problems[0].names, ("手电筒-一般",))
        self.assertEqual(item.problems[1].names, ("美工刀", "完全不存在的筹码"))  # 美工刀两张都像，不猜

    def test_bad_text_and_heroes(self):
        cases = [
            ("【缺角色】筹码方案 v2｜必选：缓冲盾牌", S.PROBLEM_BAD_TEXT),
            ("【什么都没有】筹码方案 v2", S.PROBLEM_BAD_TEXT),
            ("【不认识的段】筹码方案 v2｜芬妮｜地图：幽魂暗巷", S.PROBLEM_BAD_TEXT),
            ("【目标流派】筹码方案 v2｜芬妮｜目标流派：治愈｜必选：缓冲盾牌", S.PROBLEM_BAD_TEXT),  # 第 1 版才有的段
            ("【仅限】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜仅限目标流派", S.PROBLEM_BAD_TEXT),
            ("【没有冒号】筹码方案 v2｜芬妮｜必选缓冲盾牌", S.PROBLEM_BAD_TEXT),
            ("【新版本】筹码方案 v4｜芬妮｜必选：缓冲盾牌", S.PROBLEM_NEWER),
            ("【旧版本】筹码方案 v1｜芬妮｜必选：缓冲盾牌", S.PROBLEM_BAD_TEXT),
            ("【零版本】筹码方案 v0｜芬妮｜必选：缓冲盾牌", S.PROBLEM_BAD_TEXT),
            ("【新角色】筹码方案 v2｜不存在的角色｜必选：缓冲盾牌", S.PROBLEM_HERO_NAME),
            ("【新版本】CA4-" + SHORT_HEAL.split("CA2-")[1], S.PROBLEM_NEWER),
            ("【旧版本】CA1-" + SHORT_HEAL.split("CA2-")[1], S.PROBLEM_BAD_CODE),
            ("【空】CA2-", S.PROBLEM_BAD_CODE),
            ("【有零】CA2-ANkTfrzSwGvz2vHedw0", S.PROBLEM_BAD_CODE),
        ]
        for text, kind in cases:
            with self.subTest(text=text):
                item = S.parse_share(text, self.cat).items[0]
                self.assertIsNone(item.scheme)
                self.assertEqual(self.kinds(item), [kind])
        item = S.parse_share("【新角色】筹码方案 v2｜不存在的角色｜必选：缓冲盾牌", self.cat).items[0]
        self.assertEqual(item.problems[0].hero, "不存在的角色")
        # 角色编号资料库未收录（编号 999，其余都对）
        code = S.encode_payload(S.ShortPayload(999, ((34, "core"),)))
        item = S.parse_share("【未收录】CA2-" + code, self.cat).items[0]
        self.assertEqual((self.kinds(item), item.problems[0].hero_id), ([S.PROBLEM_HERO_ID], 999))

    def test_old_version_strings(self):
        """第 1 版的短字符串、可读文字（第 1 版没有发布过，不做兼容）：每套都报无法导入、不抛异常，不影响同一次
        粘贴里的第 2 版。"""
        for text, kind in OLD_V1:
            with self.subTest(text=text[:20]):
                item = S.parse_share(text, self.cat).items[0]
                self.assertIsNone(item.scheme)
                self.assertEqual(self.kinds(item), [kind])
                self.assertTrue(item.problems[0].fatal)
        mixed = "\n".join([OLD_V1[0][0], SHORT_HEAL, OLD_V1[4][0], TEXT_HEAL])
        result = S.parse_share(mixed, self.cat)
        self.assertEqual([i.scheme is not None for i in result.items], [False, True, False, True])

    def test_garbage_never_crashes(self):
        """乱填的文字：随机字符拼的编码、随机的段和名称、各种分隔符和括号，都不抛异常；认出来的方案档位都认识。"""
        rng = random.Random(1006)
        b58 = S.B58 + "0OIl-_ "
        noise = "【】[]［］｜|：:、，,筹码方案vV12３CA-ca必选推荐可选不选说明作者目标流派仅限芬妮凛缓冲盾牌治愈\n\t "
        samples = []
        for _ in range(400):
            samples.append("【乱】CA2-" + "".join(rng.choice(b58) for _ in range(rng.randint(0, 60))))
            samples.append("【乱】筹码方案 v2｜" + "".join(rng.choice(noise) for _ in range(rng.randint(0, 80))))
            samples.append("".join(rng.choice(noise + b58) for _ in range(rng.randint(0, 120))))
        for text in samples:
            result = S.parse_share(text, self.cat)
            for item in result.items:
                if item.scheme is not None:
                    self.assertTrue(all(t in TIERS for t in item.scheme.tiers.values()), text)
                    self.assertFalse(any(p.fatal for p in item.problems), text)

    def test_names_notes_and_markup(self):
        """名称、说明超长截断并注明；控制字符去掉；网页标记原样保留（显示时由界面转义）。"""
        name = "<b>粗</b>" + chr(7) + "超长的名称再加几个字"
        text = "【" + name + "】筹码方案 v2｜芬妮｜说明：" + "长" * 61 + "｜作者：<i>某</i>&lt;"
        item = S.parse_share(text, self.cat).items[0]
        self.assertEqual(item.name, "<b>粗</b>超长的名")
        self.assertEqual(item.scheme.name, item.name)
        self.assertEqual(self.kinds(item), [S.PROBLEM_NAME_CUT, S.PROBLEM_NOTE_CUT])
        self.assertEqual((len(item.scheme.note), item.scheme.author), (S.NOTE_MAX, "<i>某</i>&lt;"))
        item = S.parse_share("【" + name + "】" + SHORT_HEAL.split("】", 1)[1], self.cat).items[0]
        self.assertEqual((item.name, self.kinds(item)), ("<b>粗</b>超长的名", [S.PROBLEM_NAME_CUT]))
        empty = S.parse_share("【】" + SHORT_HEAL.split("】", 1)[1], self.cat).items[0]
        self.assertEqual(empty.scheme.name, S.UNNAMED)

    def test_text_prefixes_and_deletions_do_not_crash(self):
        """可读文字的每个前缀、每处删掉一个字：都不崩溃；带上了开头的都找得到这一套（能认的照常认，认不出的写明）。"""
        text = TEXT_HEAL
        head = text.index("v2") + 2
        variants = [text[:n] for n in range(len(text))] + [text[:i] + text[i + 1:] for i in range(len(text))]
        for v in variants:
            with self.subTest(v=v):
                result = S.parse_share(v, self.cat)
                self.assertLessEqual(len(result.items), 1)
                if v.startswith(text[:head]):
                    self.assertEqual(len(result.items), 1)

    def test_note_and_author_may_contain_separators(self):
        s = S.Scheme(hero="芬妮", name="a｜b：c", note="治愈｜星光：都要、都要", author="某｜某",
                     tiers={"缓冲盾牌": "core"})
        item = S.parse_share(S.to_text(s), self.cat).items[0]
        self.assertEqual(content(item.scheme), content(s))

    def test_large_pastes(self):
        """2 万字以上、50 套以上的粘贴：0.5 秒内给出结果，超出的部分记在 text_cut、skipped。"""
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)  # 测试用的默认推荐档位：复制出来的方案有内容
        rows = []
        for hero in list(db.player_synergy) * 5:
            s = S.scheme_from_default(db, hero)
            rows += [S.to_text(s), S.to_short(s, self.cat)]
        many = "\n".join(rows)
        result = S.parse_share(many, self.cat)
        total = len(db.player_synergy) * 5 * 2  # 每个有玩家思路的角色 5 份，每份可读文字、短字符串各一套
        self.assertGreater(total, S.PASTE_MAX_SCHEMES)
        self.assertEqual((len(result.items), result.skipped, result.text_cut), (50, total - 50, False))
        self.assertTrue(all(i.scheme is not None for i in result.items))
        big = many + "\n" + "噪声" * 10000
        result = S.parse_share(big, self.cat)
        self.assertTrue(result.text_cut)
        rng = random.Random(7)
        pool = "".join(sorted({ch for n in self.db.chips for ch in n}))
        garbage = "\n".join(
            f"【乱{i}】筹码方案 v2｜芬妮｜推荐：" + "、".join("".join(rng.choice(pool) for _ in range(rng.randint(2, 5)))
                                                       for _ in range(150))
            for i in range(80))
        for label, text in (("正常", many), ("超长", big), ("乱码", garbage)):
            best = min(self._timed(text) for _ in range(3))  # 取三次里最快的
            with self.subTest(label=label):
                self.assertLess(best, 0.5)
        result = S.parse_share(garbage, self.cat)
        self.assertTrue(result.text_cut)
        self.assertTrue(all(self.kinds(i) == [S.PROBLEM_UNKNOWN_NAMES] or i.scheme is not None for i in result.items))
        self.assertTrue(any(self.kinds(i) == [S.PROBLEM_UNKNOWN_NAMES] for i in result.items))  # 乱码确实走到了模糊匹配
        too_many = "【多】筹码方案 v2｜芬妮｜推荐：" + "、".join(["缓冲盾牌"] * 201)
        self.assertEqual(self.kinds(S.parse_share(too_many, self.cat).items[0]), [S.PROBLEM_BAD_TEXT])

    def _timed(self, text: str) -> float:
        """解析一次用的 CPU 时间（这个线程的）：解析是单线程的纯计算，CPU 时间就是它本身的开销；不用墙上时间，免得并行跑
        测试、优先级调低时被别的进程拖慢而误报（取三次里最快的也挡不住）。"""
        cat = S.Catalog(self.db, self.cat.codes)  # 新的 Catalog：模糊匹配没有缓存
        t0 = time.thread_time()
        S.parse_share(text, cat)
        return time.thread_time() - t0

    def test_not_text(self):
        self.assertEqual(S.parse_share(None, self.cat).items, ())
        self.assertEqual(S.parse_share("", self.cat).items, ())
        self.assertEqual(S.parse_share("【求助】大家看看这套怎么样", self.cat).items, ())


# ------------------------------------------------------------ 资料更新


class DataUpdateTest(_TmpDir):
    """模拟资料更新：加 3 个新筹码、改 1 个名（额外电池 → 备用电池，编号 15 不变）、移除 1 个（书签，编号 22）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.old = S.get_catalog(cls.db)
        chips = {n: c for n, c in cls.db.chips.items() if n != "书签"}
        chips = {("备用电池" if n == "额外电池" else n): (replace(c, name="备用电池") if n == "额外电池" else c)
                 for n, c in chips.items()}
        base = cls.db.chips["拳击手套-初级"]
        for i in (1, 2, 3):
            chips[f"新筹码{i}"] = replace(base, name=f"新筹码{i}", en=f"New Chip {i}")
        new_db = replace(cls.db, chips=chips, removed=(*cls.db.removed, "书签"))
        entries = []
        for e in cls.old.codes.entries:
            if e.code == 15:
                e = replace(e, name="备用电池", old_names=("额外电池",))
            elif e.code == 22:
                e = replace(e, removed=True)
            entries.append(e)
        top = max(e.code for e in cls.old.codes.entries)
        entries += [S.CodeEntry(top + i, f"新筹码{i}", f"New Chip {i}") for i in (1, 2, 3)]
        cls.new = S.Catalog(new_db, S.ChipCodes(tuple(entries)))

    def test_strings_after_update(self):
        """更新前导出的字符串：改名的按新名字认，已移除的单独报、不占顺位（后面的依次前移）。"""
        old = S.Scheme(hero="小町", name="旧", tiers=[("书签", "first"), ("额外电池", "first"), ("缓冲盾牌", "core"),
                                                       ("大背包", "first")])
        for text in (S.to_short(old, self.old), S.to_text(old)):
            with self.subTest(text=text):
                item = S.parse_share(text, self.new).items[0]
                self.assertEqual(item.scheme.tiers, {"缓冲盾牌": "core", "备用电池": "first", "大背包": "first"})
                self.assertEqual(item.scheme.tiers.rank("备用电池"), 1)
                self.assertEqual([(p.kind, p.names) for p in item.problems], [(S.PROBLEM_REMOVED, ("书签",))])

    def test_strings_from_newer_version(self):
        """更新版本资料里的新筹码：短字符串报「N 个筹码不在当前版本的资料库中」，可读文字报未能识别的筹码名，其余照常导入。"""
        newer = S.Scheme(hero="小町", name="新", tiers={"新筹码1": "core", "新筹码3": "first", "缓冲盾牌": "core"})
        item = S.parse_share(S.to_short(newer, self.new), self.old).items[0]
        self.assertEqual([(p.kind, p.count) for p in item.problems], [(S.PROBLEM_UNKNOWN_CODES, 2)])
        self.assertEqual(item.scheme.tiers, {"缓冲盾牌": "core"})
        item = S.parse_share(S.to_text(newer), self.old).items[0]
        self.assertEqual([(p.kind, p.names) for p in item.problems], [(S.PROBLEM_UNKNOWN_NAMES, ("新筹码1", "新筹码3"))])

    def test_code_table_unreadable(self):
        """筹码编号表读不出来（程序文件不完整，load_codes 返回空表）：标了档位的短字符串不导入、写明原因（不当成「来自更新
        版本」，也不导入成一套空方案）；什么都没标的短字符串、可读文字照常导入。"""
        empty = S.Catalog(self.db, S.ChipCodes())
        marked = S.Scheme(hero="小町", name="有档位", tiers={"缓冲盾牌": "core"})
        bare = S.Scheme(hero="小町", name="空方案")
        text = "\n".join([S.to_short(marked, self.old), S.to_short(bare, self.old), S.to_text(marked)])
        result = S.parse_share(text, empty)
        self.assertEqual([[p.kind for p in item.problems] for item in result.items], [[S.PROBLEM_NO_CODES], [], []])
        self.assertIsNone(result.items[0].scheme)
        self.assertTrue(S.Problem(S.PROBLEM_NO_CODES).fatal)
        self.assertEqual([(s.name, dict(s.tiers)) for s in result.schemes],
                         [("空方案", {}), ("有档位", {"缓冲盾牌": "core"})])

    def test_file_after_update(self):
        """旧版本存的方案文件：编号对上的按新名字读，已移除的留在文件里、不参与计算、不占顺位；没改过的方案原样写回。"""
        path = self.tmp() / "schemes.json"
        store = S.SchemeStore(path, self.old, now=lambda: NOW)
        a = store.add(S.Scheme(hero="小町", name="甲", tiers=[("书签", "first"), ("额外电池", "first"),
                                                             ("大背包", "second")]))
        b = store.add(S.Scheme(hero="小町", name="乙", tiers={"缓冲盾牌": "core"}))
        before = json.loads(path.read_text(encoding="utf-8"))
        store = S.SchemeStore(path, self.new, now=lambda: NOW)
        self.assertIsNone(store.notice)
        got = store.get(a.id)
        self.assertEqual(got.tiers, {"备用电池": "first", "大背包": "second"})
        self.assertEqual(got.tiers.rank("备用电池"), 1)
        self.assertEqual(got.stale, (S.StaleChip("书签", "first", 22, True),))
        store.update(replace(store.get(b.id), note="改一下乙"))
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(after["schemes"][0], before["schemes"][0])  # 甲没改过：原样
        store.update(replace(got, note="改一下甲"))
        chips = json.loads(path.read_text(encoding="utf-8"))["schemes"][0]["chips"]
        self.assertEqual(chips, [{"code": 15, "name": "备用电池", "tier": "first"},
                                 {"code": 14, "name": "大背包", "tier": "second"},
                                 {"code": 22, "name": "书签", "tier": "first"}])  # 对不上的写在最后
        store.update(replace(store.get(a.id), stale=()))  # 编辑窗口的「移除」
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schemes"][0]["chips"],
                         [{"code": 15, "name": "备用电池", "tier": "first"}, {"code": 14, "name": "大背包", "tier": "second"}])


# ------------------------------------------------------------ 方案文件


class StoreTest(_TmpDir):
    @classmethod
    def setUpClass(cls):
        cls.cat = S.get_catalog()

    def setUp(self):
        self.dir = self.tmp()
        self.path = self.dir / "schemes.json"

    def open(self) -> S.SchemeStore:
        return S.SchemeStore(self.path, self.cat, now=lambda: NOW)

    def sample(self, hero="芬妮", name="治愈坦克", **kw) -> S.Scheme:
        return S.Scheme(hero=hero, name=name, tiers={"缓冲盾牌": "core", "大碗炖肉": "first"}, **kw)

    def files(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def test_default_location(self):
        with sandbox.real_paths():  # 开发版的真实位置（只取位置，不写文件）
            self.assertEqual(paths.schemes_path(), paths.app_dir() / "schemes.json")
        self.assertEqual(S.backup_path(self.path).name, "schemes.json.bak")

    def test_missing_file(self):
        """没有文件：照常开始，全部默认推荐，不写文件，直到第一次保存（没有任何内容时不用建文件，也算写成）。"""
        store = self.open()
        self.assertEqual((store.schemes(), store.notice, store.read_only), ([], None, False))
        self.assertIsNone(store.current("芬妮"))
        self.assertTrue(store.save())
        self.assertTrue(store.saved)
        self.assertEqual(self.files(), [])

    def test_add_update_delete_and_last_used(self):
        store = self.open()
        a = store.add(self.sample())
        self.assertTrue(a.id)
        self.assertEqual(self.files(), ["schemes.json"])  # 第一次保存：没有原文件，不留备份
        b = store.add(self.sample())
        self.assertEqual(b.name, "治愈坦克（2）")
        c = store.add(self.sample(hero="凛"))
        self.assertEqual(c.name, "治愈坦克")  # 不同角色可以同名
        self.assertEqual(self.files(), ["schemes.json", "schemes.json.bak"])
        self.assertEqual([s.id for s in store.schemes("芬妮")], [a.id, b.id])
        self.assertEqual(len(store.schemes()), 3)
        store.set_current("芬妮", b.id)
        store.set_current("凛", c.id)
        with self.assertRaises(ValueError):
            store.set_current("芬妮", c.id)  # 别的角色的方案
        renamed = store.update(replace(b, name="治愈坦克", note="改名"))
        self.assertEqual(renamed.name, "治愈坦克（2）")  # 和 a 同名：仍加「（2）」
        with self.assertRaises(ValueError):
            store.update(replace(b, hero="凛"))
        reopened = self.open()
        self.assertEqual(reopened.current("芬妮"), renamed)
        self.assertEqual(reopened.current("凛"), c)
        self.assertEqual(reopened.schemes(), store.schemes())
        reopened.delete(b.id)  # 删的是芬妮在用的：芬妮改回默认推荐
        self.assertIsNone(reopened.current("芬妮"))
        self.assertEqual(reopened.current("凛"), c)
        reopened.set_current("凛", None)
        self.assertIsNone(self.open().current("凛"))
        with self.assertRaises(ValueError):
            reopened.add(S.Scheme(hero="不存在的角色"))

    def test_file_format(self):
        """格式版本 2；每个筹码同时记编号和名称，按方案的先后写（先按档位，同一档按顺位）；地图写成空列表；
        没有目标流派、仅限目标流派；文件是带缩进的 JSON。"""
        store = self.open()
        a = store.add(replace(S.Scheme(hero="芬妮", name="治愈坦克", note="说明", author="某某",
                                       tiers=[("大碗炖肉", "first"), ("医疗箱-完备治疗", "core"), ("可口糖果", "first"),
                                              ("缓冲盾牌", "core")]),
                              copied_from_default="2026-10-05"))
        store.set_current("芬妮", a.id)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {
            "version": 2,
            "schemes": [{"id": a.id, "hero_id": 102, "hero": "芬妮", "name": "治愈坦克", "author": "某某", "note": "说明",
                         "chips": [{"code": 40, "name": "医疗箱-完备治疗", "tier": "core"},
                                   {"code": 34, "name": "缓冲盾牌", "tier": "core"},
                                   {"code": 38, "name": "大碗炖肉", "tier": "first"},
                                   {"code": 35, "name": "可口糖果", "tier": "first"}],
                         "maps": [], "copied_from_default": "2026-10-05"}],
            "last_used": {"102": a.id},
        })
        self.assertEqual(S.FILE_VERSION, 2)
        self.assertIn("\n ", self.path.read_text(encoding="utf-8"))

    def test_ranks_saved_and_changed(self):
        """顺位存进文件、读回来不变；只改顺位、改档位、取消标记都算改动，改动即存。"""
        store = self.open()
        s = store.add(S.Scheme(hero="芬妮", name="顺位", tiers=[("医疗箱-完备治疗", "core"), ("缓冲盾牌", "core"),
                                                               ("大碗炖肉", "first"), ("可口糖果", "first")]))
        self.assertEqual(self.open().get(s.id).tiers, s.tiers)
        swapped = store.update(replace(s, tiers=s.tiers.moved("缓冲盾牌", "core", 1)))
        self.assertNotEqual(swapped, s)
        self.assertEqual(self.open().get(s.id).tiers.order("core"), ("缓冲盾牌", "医疗箱-完备治疗"))
        relabeled = store.update(replace(swapped, tiers=swapped.tiers.with_tier("缓冲盾牌", "first")))
        self.assertEqual(self.open().get(s.id).tiers.order("first"), ("大碗炖肉", "可口糖果", "缓冲盾牌"))
        dropped = store.update(replace(relabeled, tiers=relabeled.tiers.with_tier("大碗炖肉", None)))
        again = self.open().get(s.id)
        self.assertEqual(again, dropped)
        self.assertEqual([again.tiers.place(c) for c in ("医疗箱-完备治疗", "可口糖果", "缓冲盾牌", "大碗炖肉")],
                         [S.Mark("core", 1), S.Mark("first", 1), S.Mark("first", 2), None])
        # 没变的不写
        stat = self.path.stat()
        time.sleep(0.02)
        store.update(again)
        self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)

    def test_rows_order_defines_ranks(self):
        """文件里筹码行的先后定顺位（同一档里按先后，不同档可以穿插）；对不上的筹码不占顺位；没改过原样写回，改过以后按
        方案的先后重写、对不上的写在最后。"""
        rows = [{"code": 38, "name": "大碗炖肉", "tier": "first"},
                {"code": 40, "name": "医疗箱-完备治疗", "tier": "core"},
                {"code": 201, "name": "以后的筹码", "tier": "first"},
                {"code": 35, "name": "可口糖果", "tier": "first"},
                {"code": 34, "name": "缓冲盾牌", "tier": "core"}]
        raw = {"version": 2, "last_used": {}, "schemes": [
            {"id": "x", "hero_id": 102, "hero": "芬妮", "name": "甲", "chips": rows},
            {"id": "y", "hero_id": 102, "hero": "芬妮", "name": "乙"}]}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        store = self.open()
        x = store.get("x")
        self.assertEqual([x.tiers.order(t) for t in ("core", "first")],
                         [("医疗箱-完备治疗", "缓冲盾牌"), ("大碗炖肉", "可口糖果")])
        self.assertEqual(x.stale, (S.StaleChip("以后的筹码", "first", 201, False),))
        store.update(replace(store.get("y"), note="改乙"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schemes"][0]["chips"], rows)  # 甲原样
        store.update(replace(x, note="改甲"))
        self.assertEqual([r["name"] for r in json.loads(self.path.read_text(encoding="utf-8"))["schemes"][0]["chips"]],
                         ["医疗箱-完备治疗", "缓冲盾牌", "大碗炖肉", "可口糖果", "以后的筹码"])
        self.assertEqual(self.open().get("x").tiers, x.tiers)

    def test_unchanged_content_not_written(self):
        """内容没变不写（文件不动）；文件里本来就是这些内容，save 照样算写成。"""
        store = self.open()
        a = store.add(self.sample())
        stat = self.path.stat()
        time.sleep(0.02)
        self.assertTrue(store.save())
        store.update(a)
        store.update(replace(a, note=a.note))  # 内容没变
        store.set_current("芬妮", None)
        self.assertTrue(self.open().save())
        self.assertEqual(self.path.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.files(), ["schemes.json"])

    def test_backup_is_previous_version(self):
        store = self.open()
        a = store.add(self.sample())
        first = self.path.read_bytes()
        store.update(replace(a, note="第二版"))
        self.assertEqual(S.backup_path(self.path).read_bytes(), first)
        second = self.path.read_bytes()
        store.update(replace(store.get(a.id), note="第三版"))
        self.assertEqual(S.backup_path(self.path).read_bytes(), second)  # 只留一份：上一个版本

    def test_broken_files(self):
        """空文件、不是 JSON、字段类型不对、旧格式：原文件另存为 .broken-时间，没有备份时从空的开始，不覆盖原文件。"""
        good = {"version": 2, "schemes": [], "last_used": {}}
        cases = {"空文件": b"", "不是 JSON": b"{not json", "顶层不是对象": b"[]", "没有版本号": b'{"schemes": []}',
                 "版本号不是整数": json.dumps({**good, "version": "2"}).encode(),
                 "旧格式": json.dumps({**good, "version": 1}).encode(),
                 "schemes 不是列表": json.dumps({**good, "schemes": "x"}).encode(),
                 "方案缺 id": json.dumps({**good, "schemes": [{"hero_id": 102, "hero": "芬妮", "name": "a"}]}).encode(),
                 "档位不认识": json.dumps({**good, "schemes": [{"id": "x", "hero_id": 102, "hero": "芬妮", "name": "a",
                                                          "chips": [{"code": 34, "name": "缓冲盾牌", "tier": "必选"}]}]}).encode(),
                 "id 重复": json.dumps({**good, "schemes": [{"id": "x", "hero_id": 102, "hero": "芬妮", "name": "a"}] * 2}).encode(),
                 "last_used 不对": json.dumps({**good, "last_used": [1]}).encode(),
                 "不是 UTF-8": "{\"version\": 2}".encode("utf-16")}
        for label, data in cases.items():
            with self.subTest(label=label):
                for p in self.dir.iterdir():
                    p.unlink()
                self.path.write_bytes(data)
                with self.assertLogs("advisor.schemes", level="WARNING"):
                    store = self.open()
                self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_BROKEN, "schemes.json",
                                                            broken="schemes.json.broken-20261005-120000"))
                self.assertEqual(store.schemes(), [])
                self.assertEqual(self.files(), ["schemes.json.broken-20261005-120000"])
                self.assertEqual((self.dir / "schemes.json.broken-20261005-120000").read_bytes(), data)
                store.add(self.sample())
                self.assertEqual(self.files(), ["schemes.json", "schemes.json.broken-20261005-120000"])

    def test_old_format_file_set_aside(self):
        """旧格式（版本 1：带手选的目标流派、仅限目标流派，不带顺位）的方案文件和备份：不迁移，原文件和备份都改名留一份，
        当作没有方案（提示和读坏时相同）；之后照常新建，存成新格式，留下的那两份不动。"""
        old = {"version": 1, "last_used": {"102": "a1"}, "schemes": [
            {"id": "a1", "hero_id": 102, "hero": "芬妮", "name": "治愈坦克", "author": "", "note": "",
             "schools": ["治愈"], "only_schools": True, "chips": [{"code": 34, "name": "缓冲盾牌", "tier": "core"}],
             "copied_from_default": ""}]}
        data = json.dumps(old, ensure_ascii=False, indent=1).encode("utf-8")
        self.path.write_bytes(data)
        S.backup_path(self.path).write_bytes(data)
        with self.assertLogs("advisor.schemes", level="WARNING") as logs:
            store = self.open()
        self.assertTrue(any("旧格式" in line for line in logs.output))
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_BROKEN, "schemes.json",
                                                    broken="schemes.json.broken-20261005-120000"))
        self.assertEqual((store.schemes(), store.current("芬妮"), store.read_only), ([], None, False))
        kept = ["schemes.json.bak.broken-20261005-120000", "schemes.json.broken-20261005-120000"]
        self.assertEqual(self.files(), kept)
        self.assertTrue(all((self.dir / name).read_bytes() == data for name in kept))
        a = store.add(self.sample())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertNotIn("schools", saved["schemes"][0])
        self.assertEqual(self.files(), ["schemes.json", *kept])
        again = self.open()
        self.assertIsNone(again.notice)
        self.assertEqual(again.schemes(), [a])
        self.assertTrue(all((self.dir / name).read_bytes() == data for name in kept))

    def test_old_format_file_with_new_backup(self):
        """方案文件是旧格式、备份是新格式：原文件另存，改读备份并用它恢复方案文件。"""
        store = self.open()
        a = store.add(self.sample())
        new = self.path.read_bytes()
        S.backup_path(self.path).write_bytes(new)
        self.path.write_text(json.dumps({"version": 1, "schemes": [], "last_used": {}}), encoding="utf-8")
        with self.assertLogs("advisor.schemes", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak",
                                                    "schemes.json.broken-20261005-120000"))
        self.assertEqual(store.schemes(), [a])
        self.assertEqual(self.path.read_bytes(), new)

    def test_broken_file_with_backup(self):
        """读坏了、有能读的备份：改读备份并马上恢复方案文件，坏文件另存；下次启动直接读恢复后的文件。"""
        store = self.open()
        a = store.add(self.sample())
        store.set_current("芬妮", a.id)
        store.update(replace(a, note="新的"))
        bak = S.backup_path(self.path).read_bytes()
        self.path.write_bytes(b'{"version": 2, "schemes": [')  # 写到一半
        with self.assertLogs("advisor.schemes", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak",
                                                    "schemes.json.broken-20261005-120000"))
        self.assertEqual(store.get(a.id).note, "")  # 备份是上一个版本
        self.assertEqual(store.current("芬妮").id, a.id)
        self.assertEqual(self.path.read_bytes(), bak)
        self.assertEqual((self.dir / "schemes.json.broken-20261005-120000").read_bytes(), b'{"version": 2, "schemes": [')
        again = self.open()
        self.assertIsNone(again.notice)
        self.assertEqual(again.schemes(), store.schemes())
        # 同一秒里又坏一次：另存的名字不重
        self.path.write_bytes(b"x")
        with self.assertLogs("advisor.schemes", level="WARNING"):
            self.assertEqual(self.open().notice.broken, "schemes.json.broken-20261005-120000-2")

    def test_broken_file_with_newer_backup(self):
        """读坏了、备份来自更新版本：改读备份并且只读（不当坏文件另存），用备份逐字节恢复方案文件；下次启动按更新版本的
        文件只读。"""
        raw = {"version": 3, "schemes": [{"id": "n1", "hero_id": 102, "hero": "芬妮", "name": "新版本的", "new_field": 1,
                                          "chips": [{"code": 34, "name": "缓冲盾牌", "tier": "core"}]}],
               "last_used": {"102": "n1"}}
        bak = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(b'{"version": 2, "schemes": [')
        S.backup_path(self.path).write_bytes(bak)
        with self.assertLogs("advisor.schemes", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak",
                                                    "schemes.json.broken-20261005-120000"))
        self.assertTrue(store.read_only)
        self.assertEqual(store.current("芬妮").name, "新版本的")
        with self.assertRaises(S.SchemesReadOnly):
            store.add(self.sample())
        self.assertFalse(store.save())
        self.assertEqual((self.path.read_bytes(), S.backup_path(self.path).read_bytes()), (bak, bak))
        self.assertEqual(self.files(), ["schemes.json", "schemes.json.bak", "schemes.json.broken-20261005-120000"])
        again = self.open()
        self.assertEqual((again.notice.kind, again.read_only), (S.NOTICE_NEWER, True))

    def test_lone_surrogate_kept(self):
        """文件里原样保留的内容有单独的代理项（\\udXXX 转义）：保存不抛出，写回同一个转义，读回来内容不变。"""
        self.path.write_text('{"version": 2, "schemes": [], "last_used": {}, "x": "a\\ud800b"}', encoding="utf-8")
        store = self.open()
        store.add(self.sample())
        self.assertIsNone(store.save_error)
        again = self.open()
        self.assertIsNone(again.notice)
        self.assertEqual(len(again.schemes()), 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["x"], "a\ud800b")

    def test_broken_file_and_backup(self):
        self.path.write_bytes(b"x")
        S.backup_path(self.path).write_bytes(b"y")
        with self.assertLogs("advisor.schemes", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice.kind, S.NOTICE_BROKEN)
        self.assertEqual(self.files(), ["schemes.json.bak.broken-20261005-120000", "schemes.json.broken-20261005-120000"])
        store.add(self.sample())
        store.add(self.sample(hero="凛"))
        self.assertEqual((self.dir / "schemes.json.bak.broken-20261005-120000").read_bytes(), b"y")

    def test_interrupted_write(self):
        """写到一半中断（写临时文件时、替换前）：原文件和备份都完好，临时文件不留下；内存里的改动照旧有效，saved 为假；
        恢复以后照常保存。写盘的每一步出错的保证见 tests/test_fileio.py，这里核对方案存储接得对。"""
        from tests.test_fileio import Disk  # 换掉写盘用到的底层操作，在指定的那一步出错

        store = self.open()
        a = store.add(self.sample())
        store.update(replace(a, note="二"))
        main, bak = self.path.read_bytes(), S.backup_path(self.path).read_bytes()
        with Disk() as disk, self.assertLogs("advisor.schemes", level="WARNING"):
            disk.fail("write", 1)  # 写临时文件写到一半：磁盘满了
            store.update(replace(a, note="三"))
        self.assertIsInstance(store.save_error, OSError)
        self.assertFalse(store.saved)
        self.assertEqual((self.path.read_bytes(), S.backup_path(self.path).read_bytes()), (main, bak))
        self.assertEqual(store.get(a.id).note, "三")  # 内存里的改动照旧有效
        with Disk() as disk, self.assertLogs("advisor.schemes", level="WARNING"):
            disk.fail("replace", 1)  # 换上方案文件那一步出错
            store.update(replace(a, note="四"))
        self.assertEqual((self.path.read_bytes(), S.backup_path(self.path).read_bytes()), (main, bak))
        self.assertEqual(self.files(), ["schemes.json", "schemes.json.bak"])
        self.assertEqual(self.open().get(a.id).note, "二")
        self.assertTrue(store.save())  # 恢复以后照常保存
        self.assertIsNone(store.save_error)
        self.assertTrue(store.saved)
        self.assertEqual(self.open().get(a.id).note, "四")
        self.assertEqual(S.backup_path(self.path).read_bytes(), main)

    def test_newer_version_is_read_only(self):
        """来自更新版本的文件：能读的照常列出，只读，任何操作后文件逐字节不变、不建备份。"""
        raw = {"version": 3, "schemes": [
            {"id": "n1", "hero_id": 102, "hero": "芬妮", "name": "新版本的", "schools": ["治愈"],
             "chips": [{"code": 34, "name": "缓冲盾牌", "tier": "core"}], "new_field": {"x": 1}},
            {"id": "n2", "hero_id": 102, "hero": "芬妮", "name": 5}],  # 看不懂的一套跳过
            "last_used": {"102": "n1"}, "profiles": [1, 2]}
        data = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(data)
        store = self.open()
        self.assertTrue(store.read_only)
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_NEWER, "schemes.json"))
        self.assertEqual([s.name for s in store.schemes("芬妮")], ["新版本的"])
        self.assertEqual(store.current("芬妮").id, "n1")
        for action in (lambda: store.add(self.sample()), lambda: store.update(store.get("n1")),
                       lambda: store.delete("n1"), lambda: store.import_schemes([self.sample()])):
            with self.assertRaises(S.SchemesReadOnly):
                action()
        store.set_current("芬妮", None)  # 只在本次运行里生效
        self.assertIsNone(store.current("芬妮"))
        self.assertFalse(store.save())
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(self.files(), ["schemes.json"])
        self.assertEqual(self.open().current("芬妮").id, "n1")

    def test_unknown_content_preserved(self):
        """不认识的字段原样保留：顶层的、每套里的（包括本版本不再用的 schools）；角色不在资料库的方案原样留着、不列出来；
        没改过的方案原样写回。"""
        raw = {"version": 2, "future": {"a": [1, 2]}, "last_used": {"999": "ghost", "102": "f1"}, "schemes": [
            {"id": "f1", "hero_id": 102, "hero": "芬妮", "name": "甲", "color": "red", "schools": ["治愈", "新流派"],
             "chips": [{"code": 34, "name": "缓冲盾牌", "tier": "core", "pinned": True}]},
            {"id": "ghost", "hero_id": 999, "hero": "以后的角色", "name": "乙", "chips": [{"code": 120, "name": "X", "tier": "core"}]},
            {"id": "f2", "hero_id": 102, "hero": "芬妮", "name": "一二三四五六七八九十一二三四" + chr(7)},
        ]}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        store = self.open()
        self.assertIsNone(store.notice)
        self.assertEqual([s.id for s in store.schemes()], ["f1", "f2"])
        self.assertEqual(store.get("f1").tiers, {"缓冲盾牌": "core"})
        self.assertEqual(store.get("f2").name, "一二三四五六七八九十一二")
        self.assertEqual(store.current("芬妮").id, "f1")
        store.update(replace(store.get("f2"), note="改了乙以外的"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["future"], {"a": [1, 2]})
        self.assertEqual(data["last_used"], {"999": "ghost", "102": "f1"})
        self.assertEqual(data["schemes"][0], raw["schemes"][0])
        self.assertEqual(data["schemes"][1], raw["schemes"][1])
        store.update(replace(store.get("f1"), note="改甲"))
        f1 = json.loads(self.path.read_text(encoding="utf-8"))["schemes"][0]
        self.assertEqual((f1["color"], f1["schools"], f1["note"]), ("red", ["治愈", "新流派"], "改甲"))
        self.assertEqual(f1["chips"], [{"code": 34, "name": "缓冲盾牌", "tier": "core", "pinned": True}])  # 筹码行里的也留着
        store.delete("f1")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["last_used"], {"999": "ghost"})

    def test_unknown_chip_fields_follow_the_chip(self):
        """改过的那套：筹码行里不认识的字段跟着那个筹码留下（改了档位也留着），取消标记的筹码连同它的行一起去掉；
        对不上的筹码（留在文件里、不参与计算）的行也原样留着。"""
        raw = {"version": 2, "last_used": {}, "schemes": [{"id": "f1", "hero_id": 102, "hero": "芬妮", "name": "甲", "chips": [
            {"code": 34, "name": "缓冲盾牌", "tier": "core", "pinned": True},
            {"code": 38, "name": "大碗炖肉", "tier": "first", "weight": 2},
            {"code": 15, "name": "额外电池", "tier": "second", "seen": [1, 2]},
            {"code": 201, "name": "以后的筹码", "tier": "avoid", "weight": 3},
        ]}]}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        store = self.open()
        s = store.get("f1")
        store.update(replace(s, tiers=s.tiers.with_tier("大碗炖肉", "core").with_tier("额外电池", None)))
        rows = json.loads(self.path.read_text(encoding="utf-8"))["schemes"][0]["chips"]
        self.assertEqual(rows, [{"code": 34, "name": "缓冲盾牌", "tier": "core", "pinned": True},
                                {"code": 38, "name": "大碗炖肉", "tier": "core", "weight": 2},
                                {"code": 201, "name": "以后的筹码", "tier": "avoid", "weight": 3}])
        self.assertEqual(self.open().get("f1"), store.get("f1"))

    def test_chip_lookup_in_file(self):
        """读文件时先认编号、再认名称：编号对上名称变了按编号；编号不认识按名称；都对不上的留着、不参与计算。"""
        raw = {"version": 2, "last_used": {}, "schemes": [{"id": "x", "hero_id": 0, "hero": "芬妮", "name": "甲", "chips": [
            {"code": 15, "name": "旧名字", "tier": "first"},
            {"code": 200, "name": "缓冲盾牌", "tier": "core"},
            {"code": None, "name": "大碗炖肉", "tier": "second"},
            {"code": 201, "name": "以后的筹码", "tier": "avoid"},
            {"name": "手电筒-一般", "tier": "first"},
            {"code": 72, "name": "惊涛御守", "tier": "first"},  # 地图直接发放的：不能标，不参与计算
        ]}]}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        s = self.open().get("x")
        self.assertEqual(s.hero, "芬妮")  # 角色编号不认识时按名称
        self.assertEqual(s.tiers, {"额外电池": "first", "缓冲盾牌": "core", "大碗炖肉": "second"})
        self.assertEqual(s.stale, (S.StaleChip("以后的筹码", "avoid", 201, False), S.StaleChip("手电筒-一般", "first", None, True)))

    def test_onedrive_conflict_copies_untouched(self):
        store = self.open()
        store.add(self.sample())
        copies = {"schemes-DESKTOP-ABC.json": b'{"version": 2, "schemes": "other"}', "schemes (1).json": b"x"}
        for name, data in copies.items():
            (self.dir / name).write_bytes(data)
        store = self.open()
        self.assertIsNone(store.notice)
        store.add(self.sample(hero="凛"))
        for name, data in copies.items():
            self.assertEqual((self.dir / name).read_bytes(), data)
        self.assertEqual(len(store.schemes()), 2)

    def test_import(self):
        store = self.open()
        store.add(self.sample(name="治愈坦克"))
        result = S.parse_share(SHORT_HEAL + "\n" + SHORT_HEAL + "\n" + SHORT_V2[0][0], self.cat)
        added = store.import_schemes(result.schemes)
        self.assertEqual([s.name for s in added], ["治愈坦克（2）", "治愈坦克（3）", "默认推荐（副本）"])
        self.assertEqual(len({s.id for s in store.schemes()}), 4)
        self.assertTrue(all(s.id and not s.copied_from_default for s in added))
        self.assertEqual(self.open().get(added[0].id).tiers.order("core"), ("医疗箱-完备治疗", "缓冲盾牌"))  # 顺位跟着存
        with self.assertRaises(ValueError):  # 有一套的角色不在资料库：一套都不导入
            store.import_schemes([self.sample(name="新的"), S.Scheme(hero="不存在的角色")])
        self.assertEqual(len(store.schemes()), 4)
        self.assertEqual(len(self.open().schemes()), 4)

    def test_extremes_saved_and_read_back(self):
        """极端组合存进文件再读回来不变：全部必选、全部不选、全标、只标通用和地图专属、空方案。"""
        cat = self.cat
        markable = cat.sort_chips(cat.markable)
        db = cat.db
        general = [c for c in markable if not db.chips[c].school]
        cases = [[(c, "core") for c in reversed(markable)], [(c, "avoid") for c in markable],
                 [(c, TIERS[i % 4]) for i, c in enumerate(reversed(markable))], [(c, "first") for c in general], []]
        store = self.open()
        added = [store.add(S.Scheme(hero="米米", name=f"极端{i}", tiers=marks)) for i, marks in enumerate(cases)]
        again = self.open()
        for s in added:
            with self.subTest(name=s.name):
                self.assertEqual(again.get(s.id), s)

    def test_save_errors_do_not_raise(self):
        store = self.open()
        with mock.patch.object(S, "_atomic_write", side_effect=PermissionError("拒绝访问")), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            a = store.add(self.sample())
            self.assertFalse(store.save())
        self.assertIsInstance(store.save_error, PermissionError)
        self.assertFalse(store.saved)
        self.assertEqual(store.schemes(), [a])
        self.assertTrue(store.save())
        self.assertIsNone(store.save_error)
        self.assertTrue(store.saved)

    def test_save_falls_back_when_unwritable(self):
        """写不进去时改存到 fallback（发行版里是程序目录的 schemes.fallback.json，见 tests/test_storage.py）：之后都存在
        那里，同样留上一版作备份；原来的文件不动。另给了位置的存储不改 paths 的全局状态；没给 fallback 时照旧只记 save_error。"""
        self.assertIsNone(self.open().fallback)
        fallback = self.dir / "app" / "schemes.fallback.json"
        store = S.SchemeStore(self.path, self.cat, now=lambda: NOW, fallback=fallback)
        store.add(self.sample())
        real = S._atomic_write

        def main_fails(path, data, old):
            if path == self.path:
                raise PermissionError(13, "拒绝访问")
            real(path, data, old)

        with mock.patch.object(paths, "note_fallback") as noted, mock.patch.object(S, "_atomic_write", main_fails), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            b = store.add(self.sample(name="第二套"))
            store.update(replace(b, note="说明"))
        noted.assert_not_called()
        self.assertEqual((store.path, store.fallback, store.save_error), (fallback, None, None))
        moved = S.SchemeStore(fallback, self.cat)
        self.assertEqual([(s.name, s.note) for s in moved.schemes()], [("治愈坦克", ""), ("第二套", "说明")])
        self.assertTrue(S.backup_path(fallback).is_file())
        self.assertEqual([s.name for s in self.open().schemes()], ["治愈坦克"])

    def test_unreadable_file_never_overwritten(self):
        """方案文件在、读不了（被占用、没有权限）：这次运行不写方案文件（blocked），save 返回 False、saved 为假，不提示。"""
        store = self.open()
        store.add(self.sample())
        data = self.path.read_bytes()
        with mock.patch.object(S.fileio, "read_with_backup", side_effect=PermissionError("被占用")), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            locked = self.open()
        self.assertEqual(locked.schemes(), [])
        self.assertEqual((locked.blocked, locked.notice), (True, None))
        locked.add(self.sample(hero="凛"))
        self.assertFalse(locked.save())
        self.assertFalse(locked.saved)
        self.assertEqual(self.path.read_bytes(), data)

    def test_retry_when_briefly_locked(self):
        calls = []
        real = os.replace

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError("被占用")
            real(src, dst)

        store = self.open()
        with mock.patch.object(S.os, "replace", flaky):
            store.add(self.sample())
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.open().schemes()), 1)

    def test_delete_while_current_is_read(self):
        """SchemeStore 不加锁：识别线程只读 current()，界面线程改方案。删除时换一张新列表、不在原列表上删：模拟识别线程
        查到一半时界面线程删掉了排在前面的另一套，查的那一遍照旧找到正在用的方案（在原列表上删会跳过它，这一帧就按
        默认推荐算了）。"""
        store = self.open()
        first = store.add(self.sample(name="甲"))
        used = store.add(self.sample(name="乙"))
        store.set_current("芬妮", used.id)
        entry = store._entries[0]

        class Reading:
            """列表第一行：识别线程读到它时，界面线程正好删掉了它。"""
            raw, parsed, fired = entry.raw, entry.parsed, False

            @property
            def scheme(self_):
                if not Reading.fired:
                    Reading.fired = True
                    store.delete(first.id)
                return entry.scheme

        store._entries[0] = Reading()
        self.assertEqual(store.current("芬妮"), used)
        self.assertTrue(Reading.fired)
        self.assertEqual([s.name for s in store.schemes()], ["乙"])
        self.assertEqual([s.name for s in self.open().schemes()], ["乙"])
        self.assertEqual(self.open().current("芬妮"), used)


if __name__ == "__main__":
    unittest.main()
