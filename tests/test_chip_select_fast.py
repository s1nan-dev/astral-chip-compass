"""三选一的快读法（advisor/vision/chip_select.py 的 select_layout、read_chip_select_fast）：完整读一次记下标题、三个筹码名、
概率行、刷新按钮的位置，之后只把这几小块裁出来直接认字（不找字），认出的字交给和完整读法同一套后处理；有一处对不上就交回
原因，由调用方退回完整读法。

A. 规矩（FastReadTest、FastNameTest、LayoutTest）：在合成的画面上，完整读法读到的字、快读法各块认出的字和圆环颜色都换成
   假的（不建识别引擎）。认出的字和完整读法一样时结果逐项一样；画面大小变了、标题没认出或简繁不同、概率之和不是 100、名字
   对不上、圆环颜色看不出、刷新按钮在却没认出「刷新」、认出「刷新」却读不出次数，各交回对应的原因；名字两头多出来的字、
   认成「一」的「-」怎么修（修完要只对得上一个筹码、整段最像的也是它、圆环颜色和资料相符）；完整读得不完整、三个名字不在
   停稳时的那一行时不记位置，比 16:9 窄的窗口里第一栏不裁进左边的玩家列表。
B. 真样本（FastSampleTest）：samples 里几张三选一截图（四种窗口大小，有没有刷新按钮、全队三选一、名字两头连着别的字的），
   真的文字识别：同一张上快读和完整读逐项相同；同一窗口大小下拿另一张记的位置读也相同；画面大小不同、不是三选一的画面、
   位置整体偏了几十像素时退回，或读出和完整读法相同的结果，不会读出别的。样本不随仓库发布，缺了就跳过。
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass, replace
from unittest import mock

import cv2
import numpy as np

from advisor.data import MATCH_SURE, load_db
from advisor.vision import chip_select
from advisor.vision.anchors import (
    PLAYER_LIST_RIGHT, REROLL_BELOW_TITLE_U, REROLL_COUNT_DY_U, REROLL_FROM_RIGHT, TEAM_BANNER_ABOVE_U, TITLE,
    TITLE_HANT, TITLE_WIDTH_U,
)
from advisor.vision.chip_select import (
    FAST_BUTTON, FAST_COUNT, FAST_HANT, FAST_NAME, FAST_NO_TITLE, FAST_PROB, FAST_RING, FAST_SIZE, FastSpot, Line,
    read_chip_select, read_chip_select_fast, select_layout, ui_scale,
)
from tests.support import samples

DB = load_db()
SPELLINGS = chip_select._exact_spellings(DB)
# 和完整读法逐项比的几项
FIELDS = ("names", "raw", "name_scores", "rarity", "rerolls", "probabilities", "selected", "problems", "team", "hant",
          "partial", "unrecorded")
KINDS = ["title", "name0", "name1", "name2", "prob", "button", "count", "banner"]

# ------------------------------------------------------------------ A. 规矩（合成画面）

NAMES = ["会员推荐信", "手电筒-强光", "优雅之羽"]
PROB = "筹码获取概率：40%/45%/15%"
BANNER = "玩家甲正在选择强化筹码…"


@dataclass(frozen=True)
class Geo:
    """合成画面的排版：窗口大小、长度单位 U（像素）、标题中心。"""

    w: int = 1920
    h: int = 1080
    unit: float = 300.0
    tx: float = 960.0
    ty: float = 200.0

    @property
    def button(self) -> tuple[float, float]:
        return self.w - REROLL_FROM_RIGHT * ui_scale(self.w, self.h), self.ty + REROLL_BELOW_TITLE_U * self.unit


def full_lines(geo: Geo = Geo(), names=NAMES, *, title=TITLE, prob=PROB, button="刷新", count="×3", banner=None,
               row: float = 0.24, drop: float = 0.0) -> list[Line]:
    """完整读法读到的各段字（假的）：标题、三个名字（在标题以下 row U，中间一个再往下 drop U）、概率行（左边缘在标题中心左边
    1.83 U），刷新按钮和次数、全队三选一那一行（给了才有）。"""
    u = geo.unit
    lines = [Line(title, geo.tx, geo.ty, TITLE_WIDTH_U * u, 0.12 * u)]
    for i, name in enumerate(names):
        lines.append(Line(name, geo.tx + (i - 1) * u, geo.ty + (row + (drop if i == 1 else 0.0)) * u,
                          0.08 * u * len(name), 0.1 * u))
    if prob is not None:
        lines.append(Line(prob, geo.tx - 1.33 * u, geo.ty + 0.044 * u, 1.0 * u, 0.08 * u))
    if button is not None:
        bx, by = geo.button
        lines.append(Line(button, bx, by, 0.19 * u, 0.1 * u))
        if count is not None:
            lines.append(Line(count, bx, by + REROLL_COUNT_DY_U * u, 0.13 * u, 0.09 * u))
    if banner is not None:
        lines.append(Line(banner, geo.tx, geo.ty - TEAM_BANNER_ABOVE_U * u, 0.8 * u, 0.077 * u))
    return lines


class FastCase(unittest.TestCase):
    """合成画面（暗色底）：圆环颜色按第几栏给（self.colors，默认是资料里的稀有度），刷新次数放大重读换成假的（默认读不出）。"""

    geo = Geo()

    def setUp(self):
        self.colors = [DB.chips[n].rarity for n in NAMES]
        self.img = np.full((self.geo.h, self.geo.w, 3), 30, np.uint8)
        self.recognized = 0

        def ring(img, cx, cy, radius):
            return self.colors[round((cx - self.geo.tx) / self.geo.unit) + 1]

        for name, fake in (("_ring_rarity", ring), ("_reread_count", lambda img, button, unit: None)):
            patcher = mock.patch.object(chip_select, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def full(self, lines: list[Line]):
        """完整读法（读到的字是 lines）和照它记下的位置。"""
        info: dict = {}
        with mock.patch.object(chip_select, "_ocr_lines", return_value=lines):
            seen = read_chip_select(self.img, DB, info=info)
        return seen, select_layout(self.img.shape, seen, info["lines"])

    def texts(self, **changes) -> dict:
        """快读法各块认出的字：默认和 full_lines() 读到的一样（没有全队那一行）；changes 里给 (字, 把握) 可以改把握。"""
        out = {"title": TITLE, "name0": NAMES[0], "name1": NAMES[1], "name2": NAMES[2], "prob": PROB, "button": "刷新",
               "count": "×3"}
        out.update(changes)
        return out

    def fast(self, layout, texts: dict, img: np.ndarray | None = None):
        """快读法：texts 是各块认出的字（没给的当没认出字）。"""
        def reads(crops):
            self.recognized += 1
            self.assertEqual(len(crops), len(layout.spots))
            got = [texts.get(s.kind, "") for s in layout.spots]
            return [g if isinstance(g, tuple) else (g, 0.99) for g in got]

        with mock.patch.object(chip_select, "_recognize_crops", side_effect=reads):
            return read_chip_select_fast(self.img if img is None else img, DB, layout)

    def assertSameRead(self, fast, full):
        for key in FIELDS:
            self.assertEqual(getattr(fast, key), getattr(full, key), key)


class FastReadTest(FastCase):
    """A：快读法什么时候读成、什么时候退回。"""

    def test_same_text_same_result(self):
        """认出的字和完整读法读到的一样：结果逐项一样（名字、原文、匹配度、圆环颜色、次数、概率、选中、提示、全队、简繁）。
        各块按标题、三个名字、概率行、刷新按钮、次数、全队那一行的顺序，一次交给认字。"""
        seen, layout = self.full(full_lines())
        self.assertTrue(seen.ok)
        self.assertEqual([s.kind for s in layout.spots], KINDS)
        self.assertEqual((layout.size, layout.hant), ((1920, 1080), False))
        fast, why = self.fast(layout, self.texts())
        self.assertEqual(why, "")
        self.assertSameRead(fast, seen)
        self.assertEqual((fast.rerolls, fast.probabilities, fast.team), (3, (40, 45, 15), False))
        self.assertEqual(self.recognized, 1)

    def test_team_banner_read_each_time(self):
        """全队三选一那一行每次都读：认出时 team 和完整读法一样为真；队友都选完、那一行没了，快读法也跟着为假。"""
        seen, layout = self.full(full_lines(banner=BANNER))
        self.assertTrue(seen.team)
        fast, why = self.fast(layout, self.texts(banner=BANNER))
        self.assertEqual(why, "")
        self.assertSameRead(fast, seen)
        fast, why = self.fast(layout, self.texts())
        self.assertEqual((why, fast.team), ("", False))

    def test_size_changed(self):
        """画面大小和记下位置时不同：不认字，直接退回。"""
        _, layout = self.full(full_lines())
        self.assertEqual(self.fast(layout, self.texts(), img=np.zeros((1080, 1919, 3), np.uint8)), (None, FAST_SIZE))
        self.assertEqual(self.recognized, 0)

    def test_title(self):
        """标题：没认出、把握低（当没认出）、少了几个字的退回；九个字读漏一个照用；繁体界面的标题对简体记的位置退回。"""
        _, layout = self.full(full_lines())
        cases = [("", FAST_NO_TITLE), ((TITLE, chip_select.FAST_TEXT_SCORE - 0.01), FAST_NO_TITLE),
                 ("请选择一枚", FAST_NO_TITLE), ("请选择一枚战斗筹", ""), (TITLE_HANT, FAST_HANT)]
        for title, want in cases:
            with self.subTest(title=title):
                self.assertEqual(self.fast(layout, self.texts(title=title))[1], want)

    def test_hant_layout(self):
        """繁体中文界面上记的位置：繁体的标题照读，结果的 hant 为真；简体的标题退回。"""
        seen, layout = self.full(full_lines(title=TITLE_HANT))
        self.assertTrue(layout.hant)
        fast, why = self.fast(layout, self.texts(title=TITLE_HANT))
        self.assertEqual((why, fast.hant), ("", True))
        self.assertSameRead(fast, seen)
        self.assertEqual(self.fast(layout, self.texts())[1], FAST_HANT)

    def test_probabilities(self):
        """概率行：没读到、只读到两项、三项之和不是 100（淡入时把「/」读成数字）的退回；只读到三项数字照用。"""
        _, layout = self.full(full_lines())
        cases = [("", FAST_PROB), ("筹码获取概率：40%/45%", FAST_PROB), ("筹码获取概率：40%/45%/25%", FAST_PROB),
                 ("筹码获取概率：60%/37%13%", FAST_PROB), ("40%/45%/15%", "")]
        for prob, want in cases:
            with self.subTest(prob=prob):
                self.assertEqual(self.fast(layout, self.texts(prob=prob))[1], want)

    def test_names(self):
        """名字：没认出、把握低、只读到两个筹码的共同部分、别的字的退回；两头多出来的字去掉以后照用，原文是去掉以后的。"""
        seen, layout = self.full(full_lines())
        cases = [("", FAST_NAME), ((NAMES[0], chip_select.FAST_TEXT_SCORE - 0.01), FAST_NAME), ("银行卡-余额", FAST_NAME),
                 ("BATTLE TOKEN", FAST_NAME), ("会员推", FAST_NAME), ("人" + NAMES[0], ""), (NAMES[0] + "12", "")]
        for name, want in cases:
            with self.subTest(name=name):
                fast, why = self.fast(layout, self.texts(name0=name))
                self.assertEqual(why, want)
                if not why:
                    self.assertSameRead(fast, seen)

    def test_trimmed_name_checked_by_ring(self):
        """名字去掉两头的字才对上时，这一栏的圆环颜色要和资料里这个筹码的稀有度相同，不同就退回；原样就对上的名字照完整读法
        处理（颜色不同时结果里有一条稀有度的提示，和完整读法一样）。"""
        _, layout = self.full(full_lines())
        other = next(r for r in ("蓝", "紫", "金") if r != self.colors[0])
        self.colors[0] = other
        self.assertEqual(self.fast(layout, self.texts(name0="人" + NAMES[0])), (None, FAST_NAME))
        fast, why = self.fast(layout, self.texts())
        self.assertEqual(why, "")
        with mock.patch.object(chip_select, "_ocr_lines", return_value=full_lines()):
            full = read_chip_select(self.img, DB)
        self.assertSameRead(fast, full)
        self.assertTrue(any("稀有度" in p for p in fast.problems))

    def test_ring_unreadable(self):
        """三个圆环的颜色有一个看不出（位置偏了、卡片还在动）：退回。"""
        _, layout = self.full(full_lines())
        self.colors[1] = None
        self.assertEqual(self.fast(layout, self.texts()), (None, FAST_RING))

    def test_reroll_button(self):
        """刷新按钮：认出「刷新」和次数照读；那一块没认出「刷新」、也不是按钮的黄色时当没有按钮（次数为 0，那一块和次数那一块
        认出什么都不管）；有一大片按钮的黄色却没认出「刷新」时退回，不能当成 0 次；认出「刷新」、次数那一块和放大重读都读不出
        时退回，放大重读读出来了照用。"""
        _, layout = self.full(full_lines())
        for button, count, rerolls in (("刷新", "×3", 3), ("刷新×2", "", 2), ("", "×3", 0), ("V", "×3", 0)):
            with self.subTest(button=button, count=count):
                fast, why = self.fast(layout, self.texts(button=button, count=count))
                self.assertEqual((why, fast.rerolls), ("", rerolls))
        self.assertEqual(self.fast(layout, self.texts(count=""))[1], FAST_COUNT)
        with mock.patch.object(chip_select, "_reread_count", return_value=2):
            fast, why = self.fast(layout, self.texts(count=""))
        self.assertEqual((why, fast.rerolls), ("", 2))
        bx, by = self.geo.button
        u = self.geo.unit
        corner0, corner1 = (round(bx - 0.1 * u), round(by - 0.05 * u)), (round(bx + 0.1 * u), round(by + 0.05 * u))
        cv2.rectangle(self.img, corner0, corner1, (0, 220, 255), -1)  # 按钮的亮黄色底
        for button in ("", "V"):
            with self.subTest(yellow=True, button=button):
                self.assertEqual(self.fast(layout, self.texts(button=button, count=""))[1], FAST_BUTTON)
        fast, why = self.fast(layout, self.texts())
        self.assertEqual((why, fast.rerolls), ("", 3))

    def test_layout_without_button(self):
        """记位置的那一次没有刷新按钮（次数用完）：照样记下按钮那两块（照窗口右边推算），之后照常读。"""
        seen, layout = self.full(full_lines(button=None))
        self.assertEqual(seen.rerolls, 0)
        self.assertEqual([s.kind for s in layout.spots], KINDS)
        fast, why = self.fast(layout, self.texts(button="", count=""))
        self.assertEqual(why, "")
        self.assertSameRead(fast, seen)


class FastNameTest(unittest.TestCase):
    """A：名字那一块认出的字怎么对资料里的写法（_fast_name）。例子是 samples 里的三选一截图实际认成的。"""

    def check(self, text: str, spellings: dict | None = None):
        return chip_select._fast_name(DB, text, SPELLINGS if spellings is None else spellings)

    def test_exact(self):
        """原样就是资料里的写法（归一化以后：去掉引号、间隔号、空白）：照原样交，不算改过。"""
        for text, name in (("会员推荐信", "会员推荐信"), ("“8面”骰子", "“8面”骰子"), ("8面骰子", "“8面”骰子"),
                           ("书签·", "书签"), ("夹心饼干-一般", "夹心饼干-一般")):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), (text, name, False))

    def test_trim(self):
        """两头多出来的字：不是汉字的去掉几个都行，汉字最多去掉一个。"""
        cases = {"人会员推荐信": "会员推荐信", "用电击手套": "电击手套", "海智能手表": "智能手表", "小猪存钱罐121": "小猪存钱罐",
                 "，书签31": "书签", "拳击手套-高级了": "拳击手套-高级", "探天卫星C": "探天卫星", "魔法秘典3": "魔法秘典",
                 "手电筒-强光师1": "手电筒-强光", "优雅之羽。": "优雅之羽", "V魔法秘典M": "魔法秘典",
                 "人星币锤1/224": "星币锤"}
        for text, name in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.check(text), (name, name, True))

    def test_dash(self):
        """「-」认成了「一」：逐处换回去试（「一般」里真的「一」不动），换完还可以再去掉两头的字。"""
        cases = {"银行卡一余额多": "银行卡-余额多", "美工刀一初级": "美工刀-初级", "摩托头盔一一般": "摩托头盔-一般",
                 "手持风扇一小": "手持风扇-小", "美工刀一初级1": "美工刀-初级"}
        for text, name in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.check(text), (name, name, True))

    def test_not_fixed(self):
        """修不成的退回：两头各有汉字、合计两个以上（「海盗智能手表」）；中间读错、漏了「-」；只读到共同部分；没有字。"""
        for text in ("海盗智能手表", "手电筒强光师1", "夹心饼干-—般", "银行卡-余额", "飞星", ""):
            with self.subTest(text=text):
                self.assertIsNone(self.check(text))

    def test_ambiguous_or_closer_to_another(self):
        """两头去掉以后一样长的有两个不同的筹码：不认。去掉两头以后是（假设的）筹码名「紧急治疗」，整段字「箱紧急治疗」按完整
        读法的挑法最像的却是医疗箱-紧急治疗：不认。"""
        self.assertIsNone(self.check("甲乙丙", {"甲乙": "甲乙", "乙丙": "乙丙"}))
        self.assertIsNone(self.check("箱紧急治疗", {**SPELLINGS, "紧急治疗": "紧急治疗"}))

    def test_spellings_same_as_db(self):
        """快读法认的写法和 DB.match_chip 认成相似度 1.0 的写法是同一套：每种写法都配成同一个筹码，每个筹码都在里面。"""
        for key, name in SPELLINGS.items():
            with self.subTest(key=key):
                self.assertEqual(DB.match_chip(key, closest=True)[:2], (name, 1.0))
        self.assertEqual(set(DB.chips) - set(SPELLINGS.values()), set())

    def test_no_name_inside_another(self):
        """资料里没有一个筹码名的写法是另一个的一部分（FAST_TRIM_HAN 的说明靠这一点：去掉两头的字不会把完整的名字认成更短的
        另一个）。以后加的筹码打破这一点时，回头看去掉两头的规矩还够不够稳妥。"""
        inside = [(a, b) for a in SPELLINGS for b in SPELLINGS if a != b and a in b]
        self.assertEqual(inside, [])


class LayoutTest(FastCase):
    """A：什么时候记位置、各块裁在哪里。"""

    def test_boxes(self):
        """标题、三个名字、概率行照读到的位置，刷新按钮和次数照窗口右边、全队那一行照标题推算；各块都在画面里。"""
        _, layout = self.full(full_lines())
        u = self.geo.unit
        spots = {s.kind: s for s in layout.spots}
        for i in range(3):
            cx, cy = self.geo.tx + (i - 1) * u, self.geo.ty + 0.24 * u
            for got, want in zip(spots[f"name{i}"].box, (cx - 0.38 * u, cy - 0.055 * u, cx + 0.38 * u, cy + 0.055 * u)):
                self.assertAlmostEqual(got, want, delta=1)
        bx, by = self.geo.button
        for kind, want in (("button", (bx, by)), ("count", (bx, by + REROLL_COUNT_DY_U * u)),
                           ("banner", (self.geo.tx, self.geo.ty - TEAM_BANNER_ABOVE_U * u))):
            for got, w in zip(spots[kind].at[:2], want):
                self.assertAlmostEqual(got, w, places=6)
        self.assertLessEqual(spots["prob"].box[2], spots["title"].box[0])
        for s in layout.spots:
            x0, y0, x1, y1 = s.box
            self.assertTrue(0 <= x0 < x1 <= self.geo.w and 0 <= y0 < y1 <= self.geo.h, s)

    def test_not_recorded(self):
        """这几种不记位置：完整读法没认成三选一、名字匹配度低、圆环颜色有看不出的、概率行没读到、三个名字不在一条线上、名字
        那一行不在停稳时的位置（卡片还在滑进来，图标没出来的那一张在标题以下 0.19 U）。"""
        self.assertIsNone(select_layout(self.img.shape, None, full_lines()))
        seen, _ = self.full(full_lines())
        self.assertIsNone(select_layout(self.img.shape, seen, []))
        low = ["会员推", NAMES[1], NAMES[2]]
        seen, layout = self.full(full_lines(names=low))
        self.assertLess(min(seen.name_scores), MATCH_SURE)
        self.assertIsNone(layout)
        for lines in (full_lines(prob=None), full_lines(row=0.19), full_lines(drop=0.03)):
            with self.subTest(lines=[ln.text for ln in lines]):
                self.assertIsNone(self.full(lines)[1])
        self.colors[2] = None
        seen, layout = self.full(full_lines())
        self.assertIsNotNone(seen)
        self.assertIsNone(layout)


class NarrowLayoutTest(FastCase):
    """A：比 16:9 窄的窗口（4:3，界面按宽度缩放）里第一栏的名字紧挨着左边的玩家列表：名字那一块只裁到列表右边为止。"""

    geo = Geo(w=1440, h=1080, unit=355.0, tx=720.0, ty=300.0)

    def test_first_name_clipped(self):
        _, layout = self.full(full_lines(self.geo))
        spots = {s.kind: s for s in layout.spots}
        self.assertEqual(spots["name0"].box[0], round(PLAYER_LIST_RIGHT * ui_scale(1440, 1080)))
        self.assertEqual(spots["name1"].box[0], round(self.geo.tx - 0.38 * self.geo.unit))
        fast, why = self.fast(layout, self.texts())
        self.assertEqual(why, "")


# ------------------------------------------------------------------ B. 真样本

SAME = (
    "20261002_020027_chipselect_levelup_star1_hanna.png",  # 3840x2054；第一栏连着左上角的角标，认成「人会员推荐信」
    "20261002_030000_chipselect_al_garden_badge.png",  # 2000x1087
    "20261002_150912_chipselect_selected_shop_star3_2560x1057.jpg",  # 21:9；没有刷新按钮（那一块认出一个「V」）；选中第二个
    "20261002_192915_chipselect_star2_normal_2397x1798.jpg",  # 比 16:9 窄
    "20261002_213803_chipselect_team_task_tier2_after_reroll1_real.jpg",  # 全队三选一；认成「银行卡-用不完1」「美工刀一初级」
)
# 同一窗口大小下拿 A 记的位置读 B：刷新以后、下一次三选一（B 没有刷新按钮、选中了一个）
CROSS = (
    ("20261002_144845_chipselect_levelup_star1_2560x1057.jpg",
     "20261002_150218_chipselect_selected_after_reroll_no_reroll_button_2560x1057.jpg"),
    ("20261002_213051_chipselect_team_task_tier1_real.jpg", "20261002_214927_chipselect_team_task_tier3_real.jpg"),
)
# 和 SAME 最后一张同样大小、不是三选一的画面：对局画面、筹码面板（面板上也有筹码名）
OTHER = ("20261002_213811_hud_round7_own_turn_real.jpg", "20261003_131252_chips_panel_six_chips_real.jpg")
# 位置偏了也不能读出别的：整体挪这么多像素（4K 画面上的相邻两个筹码相距约 1000 像素）
SHIFTS = ((60, 0), (-60, 0), (0, 50), (0, -50), (30, 0), (0, 25))
ALL = tuple(dict.fromkeys(SAME + tuple(f for pair in CROSS for f in pair) + OTHER))


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def shifted(layout, dx: int, dy: int):
    spots = tuple(FastSpot(s.kind, (s.box[0] + dx, s.box[1] + dy, s.box[2] + dx, s.box[3] + dy),
                           (s.at[0] + dx, s.at[1] + dy, s.at[2], s.at[3])) for s in layout.spots)
    return replace(layout, spots=spots)


@unittest.skipUnless(samples.samples_ok(*ALL), samples.samples_reason(*ALL))
class FastSampleTest(unittest.TestCase):
    """B：真的文字识别。每张三选一先完整读一次、记位置（setUpClass 里做一次）。"""

    @classmethod
    def setUpClass(cls):
        cls.images = {name: load(name) for name in ALL}
        cls.full, cls.layouts = {}, {}
        for name in dict.fromkeys(SAME + tuple(f for pair in CROSS for f in pair)):
            info: dict = {}
            cls.full[name] = read_chip_select(cls.images[name], DB, info=info)
            cls.layouts[name] = select_layout(cls.images[name].shape, cls.full[name], info["lines"])

    def assertSameRead(self, fast, full, fields=FIELDS):
        for key in fields:
            self.assertEqual(getattr(fast, key), getattr(full, key), key)

    def test_same_image(self):
        """同一张上：记得下位置，快读读成，和完整读法逐项相同（包括去掉两头的字、换回「-」以后的名字和原文）。"""
        for name in SAME:
            with self.subTest(name=name):
                layout = self.layouts[name]
                self.assertIsNotNone(layout)
                fast, why = read_chip_select_fast(self.images[name], DB, layout)
                self.assertEqual(why, "")
                self.assertSameRead(fast, self.full[name])

    def test_other_shot_same_size(self):
        """同一窗口大小下拿另一张记的位置读：和这一张的完整读法逐项相同（有没有刷新按钮、选中的都跟着这一张）。"""
        for a, b in CROSS:
            with self.subTest(a=a, b=b):
                fast, why = read_chip_select_fast(self.images[b], DB, self.layouts[a])
                self.assertEqual(why, "")
                self.assertSameRead(fast, self.full[b])

    def test_other_size(self):
        """画面大小不同：退回。"""
        self.assertEqual(read_chip_select_fast(self.images[SAME[2]], DB, self.layouts[SAME[0]]), (None, FAST_SIZE))

    def test_not_select(self):
        """不是三选一的画面：退回（标题那一块认不出标题）。"""
        layout = self.layouts[SAME[-1]]
        for name in OTHER:
            with self.subTest(name=name):
                self.assertEqual(self.images[name].shape, self.images[SAME[-1]].shape)
                self.assertEqual(read_chip_select_fast(self.images[name], DB, layout), (None, FAST_NO_TITLE))

    def test_shifted_layout(self):
        """位置整体偏了：要么退回，要么读出和完整读法相同的名字、颜色、次数、概率、选中、全队。"""
        keys = ("names", "rarity", "rerolls", "probabilities", "selected", "team")
        for name in (SAME[0], SAME[2]):
            for dx, dy in SHIFTS:
                with self.subTest(name=name, dx=dx, dy=dy):
                    fast, why = read_chip_select_fast(self.images[name], DB, shifted(self.layouts[name], dx, dy))
                    if fast is None:
                        self.assertTrue(why)
                    else:
                        self.assertSameRead(fast, self.full[name], keys)


if __name__ == "__main__":
    unittest.main()
