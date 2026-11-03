"""「查看筹码」面板翻页：一页 10 格，筹码多于一页时按拿到的先后分页，中间一行两端的箭头亮着（橙色）表示
那一边还有一页。

A. 认第几页（advisor/vision/chip_panel.py 的 page、arrows，ArrowFramesTest）：一局实机录像里的三帧缩到 1920×1080 的样本：
   第一页（右箭头亮，10 个筹码）、第二页（左箭头亮，只有刚点过的电磁炮，鼠标指针盖着图案）、翻页动画（不算面板）；原样和
   压暗到 0.35（三选一开着时）各读一遍；在第一页上把箭头改画成别的样子：只有一页、中间页、最后一页、被挡住看不清。
   样本不随仓库发布，放在 samples/pages/，缺了就跳过。只有一页的旧样本见 tests/test_chip_panel.py。
B. 每一页对应已有筹码的哪一段（advisor/session/panel.py 的 page_span，PageSpanTest）：最后一页是记着的最后一页或再后一页，
   不往更前挑；中间页按内容滑动着比，对得上一半以上才算，对得一样好的几组隔得太远时不改。
C. 按页对位记账（Session.on_panel，PagedLedgerTest）：已有筹码经三选一一个个记下（候选是这个筹码加两个陪衬），面板读数是
   造的（带 page）。11、15、20、23 个筹码；只看第一页、只看第二页、两页都看；第二页只有 1 个；第一页、第二页各有一格认错或
   认不出；漏记的、补录得晚的；中间页对不上、箭头看不清时不改；没有页码的读数照只有一页算；记着的不少于一页、或者这一局
   认出过多于一页的面板时，读成只有一页要连着两帧才改（亮着的箭头被浅色的东西盖住时会读成不亮）。
D. 离开后回来判新的一局只看第一页（ResumePagesTest）：先看到第二页、中间页时不判，翻回第一页再判（翻页动画那几帧、只看到过
   后面的页就关了面板时核对留着；看过第一页、下不了结论的，翻页动画那几帧也不打断，面板看不到 RESUME_TURN_S 秒以上才算关了）；
   第一页上被挤进来的原谅几个；新的一局照样判得出（只有一页的连着两帧再判）；一帧箭头被盖住不判；按第一页判出新一局、撤销
   以后都记着认出过多于一页的面板。
E. 识别流程读面板的节奏（PanelGraceTest）：翻页动画那一帧不算面板，翻过去的那一页马上读到，不等 hud_ms。

推荐引擎换成假的（只测记账），整个文件几秒内跑完。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.paths import assets_dir
from advisor.session import PAGE_SLOTS, PANEL_MIN_CONFIDENCE, Session, Settings
from advisor.session import panel as panel_mod
from advisor.vision import anchors, chip_panel, pipeline, templates
from advisor.vision.chip_panel import read_chip_panel
from tests.support import samples
from tests.test_pipeline import _FakeReads
from tests.test_resume import ADVISE, fake_advise, select

DB = load_db()
HERO = "凛"

# ------------------------------------------------------------------ 识别样本（samples/pages/）

PAGE1 = "pages/20261017_012808_f002618_chips_panel_page1_of2_1920x1080.png"  # 第一页：右箭头亮
PAGE2 = "pages/20261017_012808_f002621_chips_panel_page2_of2_1920x1080.png"  # 第二页：左箭头亮，只有电磁炮
TURNING = "pages/20261017_012808_f002624_chips_panel_turning_1920x1080.png"  # 翻页动画：格子错位
PAGE1_OWNED = ["夹心饼干-美味", "电击手套", "额外电池", "拳击手套-高级", "鹰眼瞄具", "速度轮滑-高级", "电流剑", "摩托头盔-高级",
               "永动机", "电流核心"]
SELF_ROW = 3  # 这局自己是第 4 个页签
HAVE_PAGES = samples.samples_ok(PAGE1, PAGE2, TURNING)
HAVE_ICONS = (assets_dir("chips") / "UT_Relic_50081.png").exists()
LIT_HSV, DIM_HSV = (18, 210, 253), (0, 0, 213)  # 亮的箭头（橙色）、不亮的（灰白色），量自录像帧


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def dim(img: np.ndarray, k: float) -> np.ndarray:
    """整幅画面的亮度乘 k（三选一界面打开时游戏把画面压暗）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hsv[..., 2] = (hsv[..., 2].astype(np.float32) * k).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def bgr(hsv) -> tuple[int, int, int]:
    return tuple(int(v) for v in cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0])


def arrow_box(reading, side: int) -> tuple[int, int, int, int]:
    """读数里第 side 个箭头（0 左、1 右）的取色框（x0, y0, x1, y1，原图像素）：格子中心的重心对应 anchors.SLOTS 的重心。"""
    cx = float(np.mean([s.center[0] for s in reading.slots]))
    cy = float(np.mean([s.center[1] for s in reading.slots]))
    mx, my = np.array(anchors.SLOTS).mean(axis=0)
    (ax, ay), (hw, hh), a = anchors.ARROWS[side], anchors.ARROW_HALF, reading.unit
    x, y = cx + (ax - mx) * a, cy + (ay - my) * a
    return round(x - hw * a), round(y - hh * a), round(x + hw * a), round(y + hh * a)


def repaint(img: np.ndarray, reading, side: int, to: str) -> np.ndarray:
    """把第 side 个箭头改画成 to：lit（灰白的像素换成橙色）、dim（橙色的换成灰白）、covered（整块涂成深灰，像被挡住）。"""
    out = img.copy()
    x0, y0, x1, y1 = arrow_box(reading, side)
    box = out[y0:y1, x0:x1]
    if to == "covered":
        box[:] = 30
        return out
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    orange = (h >= 8) & (h <= 28) & (s >= 140) & (v >= 170)
    grey = (s <= 50) & (v >= 160)
    if to == "lit":
        box[grey] = bgr(LIT_HSV)
    else:
        box[orange] = bgr(DIM_HSV)
    return out


@unittest.skipUnless(HAVE_PAGES and HAVE_ICONS, samples.samples_reason(PAGE1, PAGE2, TURNING) if not HAVE_PAGES
                     else "没有下载筹码图标（tools/fetch_chip_icons.py 下载）")
class ArrowFramesTest(unittest.TestCase):
    """A：实机录像帧上认第几页。"""

    def setUp(self):
        templates.assets._last_fit = None
        templates.assets._slot_cache.clear()

    def read(self, img):
        templates.assets._last_fit = None
        return read_chip_panel(img, DB, self_row=SELF_ROW)

    def test_first_page(self):
        """第一页：只有右边的箭头亮；10 格都是最早拿的 10 个，认对了，把握照常。"""
        got = self.read(load(PAGE1))
        self.assertTrue(got.is_panel and got.self_tab, got.reason)
        self.assertEqual((got.page, got.arrows), ("first", ("dim", "lit")))
        self.assertEqual((got.owned, got.unknown_slots, got.page_full), (PAGE1_OWNED, 0, True))
        self.assertGreaterEqual(got.confidence, 0.9)

    def test_last_page_single_chip(self):
        """第二页（最后一页）：只有左边的箭头亮，只有第 11 个电磁炮。其余 9 格是空格子，不拉低把握；这一帧把握低，是因为
        电磁炮那一格刚被点过（外圈黄绿色）、鼠标指针正盖着图案，图案相关度只比下限高一点：这一帧不用，指针挪开后照常。"""
        got = self.read(load(PAGE2))
        self.assertTrue(got.is_panel and got.self_tab, got.reason)
        self.assertEqual((got.page, got.arrows), ("last", ("lit", "dim")))
        self.assertEqual((got.owned, got.unknown_slots, got.selected_slot), (["电磁炮"], 0, 0))
        self.assertEqual([s.state for s in got.slots[1:]], ["empty"] * 9)
        self.assertLess(got.confidence, PANEL_MIN_CONFIDENCE)
        chip = got.slots[0]
        self.assertTrue(chip.selected)
        self.assertEqual(got.confidence,
                         round(min(1.0, (chip.score - chip_panel.ICON_MIN) / 0.2, chip.margin / 0.3), 3))

    def test_dimmed(self):
        """三选一开着时画面被压暗（亮度 x0.35）：箭头的亮度下限跟着中间一行格子的亮度系数降，认出的页不变。"""
        for name, page in ((PAGE1, "first"), (PAGE2, "last")):
            with self.subTest(name=name):
                got = self.read(dim(load(name), 0.35))
                self.assertTrue(got.is_panel, got.reason)
                self.assertEqual(got.page, page)

    def test_turning_is_not_panel(self):
        """翻页动画那几帧格子错位、外圈对不上：不算面板（记账不改）。"""
        got = self.read(load(TURNING))
        self.assertFalse(got.is_panel)

    def test_arrow_combinations(self):
        """第一页上把箭头改画：右边也不亮是只有一页；左边也亮是中间页；左亮右不亮是最后一页；左边被挡住看不清时 page 为空
        （记账不按它改），另一边照样认。"""
        base = load(PAGE1)
        first = self.read(base)
        cases = {
            "only": repaint(base, first, 1, "dim"),
            "middle": repaint(base, first, 0, "lit"),
            "last": repaint(repaint(base, first, 0, "lit"), first, 1, "dim"),
            "": repaint(base, first, 0, "covered"),
        }
        want_arrows = {"only": ("dim", "dim"), "middle": ("lit", "lit"), "last": ("lit", "dim"), "": ("", "lit")}
        for page, img in cases.items():
            with self.subTest(page=page):
                got = self.read(img)
                self.assertTrue(got.is_panel, got.reason)
                self.assertEqual((got.page, got.arrows), (page, want_arrows[page]))
                self.assertEqual(got.owned, PAGE1_OWNED)


# ------------------------------------------------------------------ 记账：已有筹码、面板读数

# 普通筹码（不是地图专属、也不是地图发放的，免得改了地图）：前 23 个是一局里实际拿到的（按先后），接着 3 个只用来当
# 认错成的筹码（不出现在任何一组候选里），其余当三选一的陪衬
PLAIN = [c.name for c in DB.chips.values() if not c.maps and not c.granted and c.name != "循环往复"]
TRUTH, MISREAD, FILLER = PLAIN[:23], PLAIN[23:26], PLAIN[26:]


def panel(owned, page, unknown=0, confidence=0.95, **extra):
    """一页「查看筹码」面板的读数（advisor.vision.chip_panel.PanelReading 的样子）：page 是第几页（None 时不带这个属性）。"""
    reading = SimpleNamespace(is_panel=True, self_tab=True, self_index=None, selected_tab=None, owned=list(owned),
                              unknown_slots=unknown, confidence=confidence, page_full=len(owned) + unknown >= PAGE_SLOTS,
                              problems=[], **extra)
    if page is not None:
        reading.page = page
    return reading


def page_kind(k: int, pages: int) -> str:
    if pages == 1:
        return "only"
    return "first" if k == 0 else "last" if k == pages - 1 else "middle"


class Ledger:
    """一局：truth 是实际拿到的筹码（面板上的先后），按先后经三选一一个个记下（候选是这个筹码加两个陪衬，绿框在它上面）。

    skip：没记下的（离开期间游戏代选、地图发放的……）；wrong：{第几个: 记下的}，候选是（实际拿的, 陪衬, 记下的），
    思考时间用完、游戏拿了最左边的，绿框在记下的那个上面；added：之后才补录的（接在末尾，来源是补录）。"""

    def __init__(self, n: int, skip=(), wrong=None, added=(), truth=None):
        self.s = Session(DB, Settings(hero=HERO))
        self.clock = 1000.0
        self.s._clock = lambda: self.clock
        self.truth = list(truth if truth is not None else TRUTH[:n])
        self.events: list = []
        wrong = dict(wrong or {})
        for i, chip in enumerate(self.truth):
            if i in skip or i in added:
                continue
            if i in wrong:
                self.pick((chip, FILLER[i % len(FILLER)], wrong[i]), 2)
            else:
                self.pick((chip, FILLER[(2 * i) % len(FILLER)], FILLER[(2 * i + 1) % len(FILLER)]), 0)
        for i in added:
            self.s.add_chip(self.truth[i])
        self.s.skip_panel()
        self.drain()

    def drain(self) -> list:
        got = self.s.take_events()
        self.events += got
        return got

    def pick(self, offer, green: int) -> None:
        s = self.s
        self.clock += 20
        s.on_frame(select(list(offer), selected=green))
        s.on_frame(None)
        s.on_frame(None)
        self.drain()

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.truth) // PAGE_SLOTS))

    def page(self, k: int) -> list:
        return self.truth[k * PAGE_SLOTS : (k + 1) * PAGE_SLOTS]

    def show(self, k: int, owned=None, **kw):
        """打开面板看第 k 页（0 起，默认读对了）。返回主要事件。"""
        self.clock += 1
        ev = self.s.on_panel(panel(self.page(k) if owned is None else owned, kw.pop("page", page_kind(k, self.pages)),
                                   **kw))
        if ev is not None:
            self.events.append(ev)
        self.drain()
        return ev

    def close(self) -> None:
        self.s.on_panel(None)
        self.drain()

    @property
    def owned(self) -> list:
        return list(self.s.settings.owned)

    def kinds(self, kind: str) -> list:
        return [e for e in self.events if e.kind == kind]


class PatchedAdvise(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch(ADVISE, fake_advise)
        patcher.start()
        self.addCleanup(patcher.stop)


class PageSpanTest(unittest.TestCase):
    """B：面板这一页对应已有筹码里的哪一段（只做计算）。"""

    def span(self, page, old, seen, sources=None, offers=None):
        return panel_mod.page_span(page, old, seen, sources or {n: "pick" for n in old}, offers or {})

    def test_only_and_first(self):
        old = TRUTH[:15]
        self.assertEqual(self.span("only", old, old[:3]), (0, 15))
        self.assertEqual(self.span("first", old, old[:10]), (0, 10))
        self.assertEqual(self.span("first", old[:6], old[:6]), (0, 6))  # 记着的不到一页（之后拿的还没记上）

    def test_last_page(self):
        """最后一页从记着的最后一页或再后一页起：对得上的多的那个；一样多（都对不上）时挑靠后的，挑错了少去掉几个。"""
        old = TRUTH[:20]
        self.assertEqual(self.span("last", TRUTH[:11], TRUTH[10:11]), (10, 11))
        self.assertEqual(self.span("last", old, TRUTH[10:20]), (10, 20))
        self.assertEqual(self.span("last", old, MISREAD[:1]), (20, 20))  # 漏记了：这一页上的接在末尾，什么都不去掉
        self.assertEqual(self.span("last", TRUTH[:10], TRUTH[10:11]), (10, 10))
        self.assertEqual(self.span("last", TRUTH[:21], TRUTH[20:21]), (20, 21))
        self.assertEqual(self.span("last", TRUTH[:23], TRUTH[20:23]), (20, 23))

    def test_last_page_not_further_back(self):
        """漏记了第一页上的一个、后面的都错开一位（记着 29 个）：最后一页上的第 21~30 个记在第 20~29 位。从第 11 位起的那一段
        对得上的还多一个，但不往更前挑（那一段比一页还长，第二页上的 9 个不在最后一页上，会被当成记错去掉），挑从第 21 位起的。"""
        truth = (TRUTH + FILLER)[:30]
        old = truth[:4] + truth[5:]
        self.assertEqual(self.span("last", old, truth[20:30]), (20, 29))

    def test_last_page_mostly_unmatched(self):
        """记着的最后一页那一段对得上的（加上认不出的格数）不到一半时，最后一页是再后面一页：这一页上的接在末尾，什么都不去掉
        （挑记着的最后一页的话，那一段里这一页上没有的都要去掉）。前面的页上漏记了一个，记着的第 11~20 个其实是第 12~21 个，
        最后一页上只有第 21 个（就在那一段末尾，对得上 1 个）；第 21 个没记上，它正好是那一段里某次三选一的候选（能解释一条
        记错，也算对得上 1 个）。认不出的格子算进对得上的：5 个里认出 2 个、认不出 1 格时照样挑记着的最后一页。"""
        truth = TRUTH[:21]
        self.assertEqual(self.span("last", truth[:4] + truth[5:], truth[20:]), (20, 20))
        old = truth[:20]
        offers = {old[12]: (old[12], truth[20], FILLER[0])}
        self.assertEqual(self.span("last", old, truth[20:], offers=offers), (20, 20))
        old, sources = TRUTH[:15], {n: "pick" for n in TRUTH[:15]}
        self.assertEqual(panel_mod.page_span("last", old, TRUTH[10:12], sources, {}), (15, 15))
        self.assertEqual(panel_mod.page_span("last", old, TRUTH[10:12], sources, {}, unknown=1), (10, 15))
        self.assertEqual(panel_mod.page_span("last", old, [], sources, {}, unknown=5), (15, 15))  # 一个都没认出

    def test_middle_page(self):
        """中间页：一组 10 个滑动着比；对得一样多时挑正好从页头开始的；之前漏记了两个、这一组错开两位时照样对上。"""
        old = TRUTH[:23]
        self.assertEqual(self.span("middle", old, TRUTH[10:20]), (10, 20))
        self.assertEqual(self.span("middle", old, TRUTH[12:20]), (10, 20))  # 两格认不出：几组一样好，从页头开始的
        shifted = TRUTH[:3] + TRUTH[5:23]  # 第一页上漏记了两个
        self.assertEqual(self.span("middle", shifted, TRUTH[10:20]), (8, 18))

    def test_middle_page_no_match(self):
        """中间页对不上就不改（None）：面板上认出的不到一半在记着的某一组里；对得一样好的几组隔得超过一页（分不出是哪一页）；
        一个都没认出。"""
        old = TRUTH[:23]
        self.assertIsNone(self.span("middle", old, MISREAD + TRUTH[11:12]))  # 4 个里只对上 1 个
        self.assertEqual(self.span("middle", old, MISREAD[:1] + TRUTH[11:12]), (10, 20))  # 正好一半
        self.assertIsNone(self.span("middle", old, [TRUTH[1], TRUTH[22]]))
        self.assertIsNone(self.span("middle", old, []))
        self.assertIsNone(self.span("", old, TRUTH[:10]))


class PagedLedgerTest(PatchedAdvise):
    """C：按页对位记账。"""

    def test_consistent_ledger_unchanged(self):
        """记得都对：11、15、20、23 个筹码，每一页看一遍（先后各种），已有筹码一直不变，看到的都算面板确认过的（翻到另一页时
        已有筹码没变也一样）。每翻到一页发一次读数，同一页开着不再发。"""
        for n in (11, 15, 20, 23):
            pages = -(-n // PAGE_SLOTS)
            for order in (list(range(pages)), list(reversed(range(pages)))):
                with self.subTest(n=n, order=order):
                    t = Ledger(n)
                    self.assertEqual(t.owned, t.truth)  # 前提：三选一记下的就是拿到的先后
                    for k in order:
                        t.show(k)
                        self.assertEqual(t.owned, t.truth)
                        self.assertTrue(all(t.s.game.chip_sources[c] == "panel" for c in t.page(k)))
                        self.assertIsNone(t.show(k))  # 同一页、同样的读数
                    self.assertEqual(set(t.s.game.chip_sources.values()), {"panel"})
                    self.assertEqual(len(t.kinds("panel_read")), pages)
                    self.assertEqual(t.s.game.unrecorded, 0)

    def test_first_page_only(self):
        """只看第一页：只核对前 10 个（第 4 个记错了，按面板改正），第 11 个以后的照记着的不动（第 13 个也记错了，没看到）。"""
        t = Ledger(15, wrong={3: MISREAD[0], 12: MISREAD[1]})
        ev = t.show(0)
        self.assertEqual(ev.fixes, [(MISREAD[0], t.truth[3])])
        self.assertEqual(t.owned, t.truth[:12] + [MISREAD[1]] + t.truth[13:])

    def test_second_page_only(self):
        """只看第二页：只核对第 11 个以后的（第 13 个记错了，按面板改正），前 10 个照记着的不动。"""
        t = Ledger(15, wrong={3: MISREAD[0], 12: MISREAD[1]})
        ev = t.show(1)
        self.assertEqual(ev.fixes, [(MISREAD[1], t.truth[12])])
        self.assertEqual(t.owned, t.truth[:3] + [MISREAD[0]] + t.truth[4:])
        offer = t.s.game._pick_offers[t.truth[12]]
        self.assertEqual(offer[0], t.truth[12])  # 候选组跟着改到面板上那个名下

    def test_both_pages(self):
        """两页都看：第一页上漏记的第 5 个补进它的位置（第 11 个记着的挤到了第一页末尾，按面板挪回第二页），第二页上记错的
        第 13 个按面板改正，最后和实际一样。"""
        t = Ledger(15, skip=(4,), wrong={12: MISREAD[1]})
        t.s.game.unrecorded = 1  # 星数多了、没看到对应的三选一：待补录 1 个
        t.show(0)
        self.assertEqual(t.owned, t.truth[:12] + [MISREAD[1]] + t.truth[13:])
        self.assertEqual(t.s.game.unrecorded, 0)  # 这一页上多出来的那个抵掉待补录
        t.show(1)
        self.assertEqual(t.owned, t.truth)

    def test_second_page_single_chip(self):
        """第二页只有 1 个（第 11 个）：记对了不变；没记上时补在末尾；记错了按面板改正。把握照读数（空格子不拉低）。"""
        t = Ledger(11)
        self.assertEqual(t.show(1).kind, "panel_read")
        self.assertEqual(t.owned, t.truth)
        t = Ledger(11, skip=(10,))
        t.show(1)
        self.assertEqual(t.owned, t.truth)
        t = Ledger(11, wrong={10: MISREAD[0]})
        self.assertEqual(t.show(1).fixes, [(MISREAD[0], t.truth[10])])
        self.assertEqual(t.owned, t.truth)

    def test_low_confidence_page_ignored(self):
        """把握不够的读数不用（鼠标指针盖着刚点过的那一格等），和只有一页时一样。"""
        t = Ledger(11, wrong={10: MISREAD[0]})
        self.assertIsNone(t.show(1, confidence=0.12))
        self.assertEqual(t.owned, t.truth[:10] + [MISREAD[0]])

    def test_first_page_misread(self):
        """第一页把第 4 个认成了没拿过的筹码：第 4 个不去掉（可能在没看到的下一页），挪到这一页之后；认错的那个记进来。
        再看第二页（读对了）：第 4 个不在最后一页上，去掉，和只有一页时一样至多差这 1 个。之后第一页读对了，认错的那个挪到
        这一页之后，再看第二页时去掉：两页都读对一遍就和实际一样。"""
        t = Ledger(15)
        seen = t.page(0)
        seen[3] = MISREAD[0]
        t.show(0, owned=seen)
        self.assertEqual(t.owned, seen + [t.truth[3]] + t.truth[10:])
        t.show(1)
        self.assertEqual(t.owned, seen + t.truth[10:])
        t.show(0)
        t.show(1)
        self.assertEqual(t.owned, t.truth)

    def test_first_page_unknown_slot(self):
        """第一页有一格认不出（藏着第 4 个）：记着的第 4 个留在原位，不改。"""
        t = Ledger(15)
        seen = t.page(0)
        del seen[3]
        t.show(0, owned=seen, unknown=1)
        self.assertEqual(t.owned, t.truth)

    def test_second_page_misread_and_unknown(self):
        """第二页把第 13 个认成了别的：最后一页上没有它（三选一当场记下的，位置可靠），按面板换成认错的那个，和只有一页时
        一样；那一格认不出时留着。前 10 个都不动。"""
        t = Ledger(15)
        seen = t.page(1)
        seen[2] = MISREAD[0]
        t.show(1, owned=seen)
        self.assertEqual(t.owned, t.truth[:12] + [MISREAD[0]] + t.truth[13:])
        t = Ledger(15)
        seen = t.page(1)
        del seen[2]
        t.show(1, owned=seen, unknown=1)
        self.assertEqual(t.owned, t.truth)

    def test_late_added_kept_on_last_page(self):
        """第 5 个是之后才补录的（接在末尾）：第二页上没有它，但它的位置不可靠（可能在前面的页上），不去掉，挪到这一页之前；
        再看第一页就回到第 5 位。"""
        t = Ledger(15, added=(4,))
        self.assertEqual(t.owned[-1], t.truth[4])  # 前提：补录的接在末尾
        t.show(1)
        self.assertEqual(sorted(t.owned), sorted(t.truth))
        self.assertEqual(t.owned[-5:], t.truth[10:])
        t.show(0)
        self.assertEqual(t.owned, t.truth)

    def test_last_page_after_missed_chip(self):
        """一共 21 个、记着 20 个，先看到最后一页（第三页，只有第 21 个）：第一页上漏记了第 5 个（记着的第 11~20 个其实是第
        12~21 个）；或者第 21 个没记上，而第 13 个那次三选一的候选里有它、或那次最左边那格没读出名字（第 21 个能「解释」那一条）。
        记着的第 11~20 个都还在（不能当成最后一页上没有就去掉：中间页对不上时补不回来），第 21 个在末尾；之后看前两页就和实际
        一样。"""
        truth = TRUTH[:21]
        cases = {"第一页漏记": Ledger(21, skip=(4,))}
        offers = {"候选里有它": (truth[12], truth[20], FILLER[0]), "最左边没读出": (None, truth[12], FILLER[0])}
        for name, offer in offers.items():
            t = Ledger(0, truth=[])
            t.truth = truth
            for i, chip in enumerate(truth[:20]):
                if i == 12:
                    t.pick(offer, offer.index(chip))
                else:
                    t.pick((chip, FILLER[(2 * i) % len(FILLER)], FILLER[(2 * i + 1) % len(FILLER)]), 0)
            self.assertEqual(t.s.game._pick_offers[truth[12]], offer)  # 前提
            cases[name] = t
        for name, t in cases.items():
            with self.subTest(name):
                before = t.owned
                self.assertEqual(len(before), 20)
                self.assertEqual(t.show(2).kind, "panel_read")
                self.assertEqual([n for n in before if n not in t.owned], [])
                self.assertEqual(t.owned[-1], truth[20])
                t.show(0)
                t.show(1)
                self.assertEqual(t.owned, t.truth)

    def test_middle_page(self):
        """23 个筹码（三页）：中间页上记错的第 16 个按面板改正；中间页上的筹码记着的哪一组都对不上时不改、不发读数。"""
        t = Ledger(23, wrong={15: MISREAD[0]})
        self.assertEqual(t.show(1).fixes, [(MISREAD[0], t.truth[15])])
        self.assertEqual(t.owned, t.truth)
        t = Ledger(23)
        self.assertIsNone(t.show(1, owned=MISREAD))
        self.assertEqual(t.owned, t.truth)
        self.assertEqual(t.kinds("panel_read"), [])

    def test_unclear_page_unchanged(self):
        """箭头看不清（page 为空串）、页码不认识：不知道对应哪一段，不改、不发读数；面板算打开过（不再请用户打开）。"""
        for page in ("", None, "第二页"):
            with self.subTest(page=page):
                t = Ledger(15, wrong={12: MISREAD[1]})
                reading = panel(t.page(1), "last")
                reading.page = page
                self.assertIsNone(t.s.on_panel(reading))
                self.assertEqual(t.owned, t.truth[:12] + [MISREAD[1]] + t.truth[13:])
                self.assertTrue(t.s.game._panel_prompted)

    def test_single_only_frame_ignored(self):
        """记着的不少于一页时，只有一帧读成只有一页（亮着的箭头被浅色的东西盖住，读成不亮）：和看不清一样，不改、不发读数。第一页、
        最后一页上都一样；两帧之间隔着别的页、面板关了一下、一帧把握不够、队友的页签、看不清的，都不算连着两帧。"""
        breaks = {
            "别的页": lambda t, k: t.show(k),
            "面板关了": lambda t, k: t.close(),
            "把握不够": lambda t, k: t.show(k, page="only", confidence=0.1),
            "队友的页签": lambda t, k: t.s.on_panel(SimpleNamespace(
                is_panel=True, self_tab=False, self_index=3, selected_tab=0, owned=[], unknown_slots=0, confidence=0.9)),
            "看不清": lambda t, k: t.show(k, page=""),
        }
        for k in (0, 1):
            for name, between in breaks.items():
                with self.subTest(page=k, between=name):
                    t = Ledger(15)
                    t.show(k)  # 箭头没盖住时先读到这一页
                    reads = len(t.kinds("panel_read"))
                    self.assertIsNone(t.show(k, page="only"))
                    self.assertEqual(t.owned, t.truth)
                    between(t, k)
                    self.assertIsNone(t.show(k, page="only"))
                    self.assertEqual(t.owned, t.truth)
                    self.assertEqual(len(t.kinds("panel_read")), reads)
                    self.assertEqual(t.s.game.unrecorded, 0)

    def test_two_only_frames_applied(self):
        """连着两帧都读成只有一页（真的只有一页，例如记着的是上一局的）：第二帧照只有一页改，面板上的就是全部，之后每帧照常（同样
        的读数不再发）。记着的正好满一页（10 个）时也要两帧；不到一页、这一局又没认出过多于一页的面板时一帧就改。"""
        nxt = FILLER[:5]
        for n in (15, PAGE_SLOTS):
            with self.subTest(n=n):
                t = Ledger(n)
                self.assertIsNone(t.show(0, owned=nxt, page="only"))
                self.assertEqual(t.owned, t.truth)
                self.assertEqual(t.show(0, owned=nxt, page="only").kind, "panel_read")
                self.assertEqual(t.owned, nxt)
                self.assertIsNone(t.show(0, owned=nxt, page="only"))
        for n in (8, PAGE_SLOTS - 1):
            with self.subTest(n=n):
                t = Ledger(n)
                self.assertEqual(t.show(0, owned=nxt, page="only").kind, "panel_read")
                self.assertEqual(t.owned, nxt)

    def test_only_after_paged_needs_two_frames(self):
        """这一局认出过多于一页的面板以后（同一局里筹码只增不减，面板不会再变回只有一页），记着的不到一页时读成只有一页也要连着
        两帧：一共 12 个、前 4 个没记上（记着 8 个），先看第二页（最后一页）；之后一帧左箭头被盖住、第二页读成只有一页，不改（按
        只有一页改会把记着的第 5~10 个去掉）；箭头露出来，照第一页、第二页核对就和实际一样。新的一局清掉这个标记。"""
        t = Ledger(12, skip=(0, 1, 2, 3))
        self.assertEqual(len(t.owned), 8)  # 前提：记着的不到一页
        self.assertFalse(t.s.game._paged)
        t.show(1)
        self.assertTrue(t.s.game._paged)
        before = t.owned
        self.assertIsNone(t.show(1, page="only"))
        self.assertEqual(t.owned, before)
        t.show(0)
        t.show(1)
        self.assertEqual(t.owned, t.truth)
        t.s.new_game()
        self.assertFalse(t.s.game._paged)
        t = Ledger(12, skip=(0, 1, 2, 3))
        self.assertEqual(t.show(1, page="only").kind, "panel_read")  # 没认出过多于一页的面板：一帧就改
        self.assertEqual(t.owned, t.page(1))

    def test_reading_without_page_is_only(self):
        """没有页码的读数（旧的读数、造的读数）照只有一页算：面板上的就是全部。记着的多于一页时和读成只有一页一样，要连着
        两帧才改；记着的不超过一页时一帧就改。"""
        t = Ledger(12)
        self.assertIsNone(t.show(0, page=None, owned=t.truth[:10]))
        self.assertEqual(t.owned, t.truth)
        t.show(0, page=None, owned=t.truth[:10])
        self.assertEqual(t.owned, t.truth[:10])
        t = Ledger(8)
        t.show(0, page=None, owned=t.truth[:6])
        self.assertEqual(t.owned, t.truth[:6])


class ResumePagesTest(PatchedAdvise):
    """D：离开很久后回来，判是不是新的一局只看第一页（最早拿的 10 个）。"""

    def away(self, t: Ledger, seconds: float = 300) -> None:
        t.clock += seconds
        t.s.on_resume(seconds)
        self.assertTrue(t.s.game._resume_check)

    def test_second_page_first_same_game(self):
        """同一局：离开期间游戏代选了一个（第 16 个）。回来先看到第二页：不判，按页核对补上代选的；翻回第一页：对得上，
        核对结束，没有判新的一局。"""
        t = Ledger(16, skip=(15,))
        self.away(t)
        ev = t.show(1)
        self.assertEqual(ev.kind, "panel_read")
        self.assertTrue(t.s.game._resume_check)  # 还没下结论
        self.assertEqual(t.owned, t.truth)
        t.show(0)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])
        self.assertEqual(t.owned, t.truth)

    def test_second_page_first_new_game(self):
        """下一局开始后才回来（下一局也有 13 个）：先看到下一局的第二页不判；翻回第一页，记着的前 10 个一个都不在，判出新的
        一局，已有筹码换成这一页的 10 个；再翻到第二页补上后 3 个。"""
        nxt = (FILLER + MISREAD)[:13]
        t = Ledger(15)
        self.away(t)
        t.show(1, owned=nxt[10:])
        self.assertEqual(t.kinds("newgame"), [])
        ev = t.show(0, owned=nxt[:10])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertEqual(t.owned, nxt[:10])
        self.assertTrue(t.s.game._paged)  # 按第一页判出的新一局也认出过多于一页的面板
        t.show(1, owned=nxt[10:])
        self.assertEqual(t.owned, nxt)
        self.assertEqual(len(t.kinds("newgame")), 1)

    def test_paged_flag_after_undo(self):
        """记着 8 个，离开后回来看到的第一页（多于一页的面板）对不上，判出新的一局：新的一局记着认出过多于一页的面板；点「撤销」
        恢复的这一局也记着（撤销说明还是同一局，它的筹码多于一页），之后一帧读成只有一页不改。"""
        t = Ledger(8)
        self.away(t)
        nxt = (FILLER + MISREAD)[:13]
        ev = t.show(0, owned=nxt[:10], page="first")
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertTrue(t.s.game._paged)
        self.assertTrue(t.s.undo_new_game())
        t.drain()
        self.assertEqual(t.owned, t.truth)
        self.assertTrue(t.s.game._paged)
        self.assertIsNone(t.show(0, owned=nxt[:10], page="only"))
        self.assertEqual(t.owned, t.truth)

    def test_turning_frames_keep_check(self):
        """先看到第二页，翻回第一页的途中有翻页动画那几帧（不算面板）：核对留着，看到第一页时照常判。只看到过第二页就关了
        面板也留着，下次看到第一页再判（同一局里第一页上的筹码不会变）；看过第一页、没下结论就关了的，到此为止。"""
        t = Ledger(15)
        self.away(t)
        t.show(1)
        t.s.on_panel(SimpleNamespace(is_panel=False, self_tab=False, owned=[], unknown_slots=0, confidence=0.0))
        self.assertTrue(t.s.game._resume_check)
        t.show(0)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])
        nxt = (FILLER + MISREAD)[:13]
        t = Ledger(15)
        self.away(t)
        t.show(1, owned=nxt[10:])
        t.close()
        self.assertTrue(t.s.game._resume_check)
        ev = t.show(0, owned=nxt[:10])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        t = Ledger(15)
        self.away(t)
        t.show(0, owned=t.page(0)[:5], unknown=5)  # 认不出的格子多：下不了结论
        self.assertTrue(t.s.game._resume_check)
        t.close()
        self.assertTrue(t.s.game._resume_check)  # 刚看不到：可能是翻页动画那几帧
        t.clock += panel_mod.RESUME_TURN_S
        t.close()
        self.assertFalse(t.s.game._resume_check)  # 看不到这么久：面板关了，到此为止
        t.show(0)
        self.assertEqual(t.kinds("newgame"), [])
        # 关了这么久、中间没有别的帧（两帧隔得久）就又打开：同样算关过，到此为止，这一帧只按面板写入
        t = Ledger(15)
        self.away(t)
        t.show(0, owned=t.page(0)[:5], unknown=5)
        t.close()
        t.clock += panel_mod.RESUME_TURN_S
        t.show(0, owned=(FILLER + MISREAD)[:10])
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])

    def test_turning_after_first_page_keeps_check(self):
        """看过第一页、认不出的格子多下不了结论，接着翻到第二页再翻回来（中间夹着翻页动画那几帧，不算面板）：核对留着，
        看清第一页时照常判。下一局开始后才回来（下一局也有 13 个）：判出新的一局；同一局：对得上，核对结束。翻页动画那一帧
        要是当成面板关了、核对到此为止，下一局就判不出来了。"""
        nxt = (FILLER + MISREAD)[:13]
        turning = SimpleNamespace(is_panel=False, self_tab=False, owned=[], unknown_slots=0, confidence=0.0)
        for flip in (False, True):
            with self.subTest(flip=flip):
                t = Ledger(15)
                self.away(t)
                t.show(0, owned=nxt[:1], unknown=9)  # 只认出 1 格：少了的可能都在认不出的格子里
                self.assertEqual(t.kinds("newgame"), [])
                self.assertTrue(t.s.game._resume_check)
                t.s.on_panel(turning)
                if flip:
                    t.show(1, owned=nxt[10:])  # 第二页：不拿它判
                    t.s.on_panel(turning)
                self.assertTrue(t.s.game._resume_check)
                ev = t.show(0, owned=nxt[:10])
                self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
                self.assertEqual(t.owned, nxt[:10])
                t.show(1, owned=nxt[10:])
                self.assertEqual(t.owned, nxt)
        t = Ledger(15)
        self.away(t)
        t.show(0, owned=t.page(0)[:1], unknown=9)
        t.s.on_panel(turning)
        t.show(1)
        t.s.on_panel(turning)
        t.show(0)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])
        self.assertEqual(t.owned, t.truth)

    def test_middle_page_first(self):
        """三页时先看到中间页：不判；翻回第一页再判（同一局）。"""
        t = Ledger(23)
        self.away(t)
        t.show(1)
        self.assertTrue(t.s.game._resume_check)
        t.show(0)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])

    def test_first_page_forgives_pushed_chips(self):
        """第 4、6 个是之后才补录的（接在末尾）：记着的前 10 个里挤进了第 11、12 个（它们其实在第二页）。第一页上没有这两个，
        但它们排在这一段末尾、个数不超过第一页上有而这一段里没有的，原谅：不判新的一局，按第一页排好。"""
        t = Ledger(13, added=(3, 5))
        self.away(t)
        t.show(0)
        self.assertEqual(t.kinds("newgame"), [])
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.owned, t.truth)
        self.assertEqual(panel_mod.resume_missing("first", t.truth[:3] + t.truth[4:5] + t.truth[6:] + [t.truth[3], t.truth[5]],
                                                  t.truth[:10], {}, {}),
                         ([t.truth[10], t.truth[11]], 2))

    def test_forgiveness_limits(self):
        """被挤进来的最多原谅 RESUME_SHIFT_MAX 个；第一页上不在记着的前 10 个里的多于这么多个时一个都不原谅（不是漏记了几个，
        更像换了一局）。"""
        old = TRUTH[:15]
        sources = {n: "pick" for n in old}
        late = [old[i] for i in (2, 4, 6)]  # 第 3、5、7 个之后才补录（接在末尾）：前 10 个里挤进了第 11~13 个
        shifted = [n for n in old if n not in late] + late
        self.assertEqual(panel_mod.resume_missing("first", shifted, old[:10], sources, {}),
                         (old[10:13], panel_mod.RESUME_SHIFT_MAX))
        seen = TRUTH[:5] + MISREAD + FILLER[:2]  # 前 5 个对得上，后 5 格是别的
        self.assertEqual(panel_mod.resume_missing("first", old, seen, sources, {}), (TRUTH[5:10], 0))

    def test_small_ledger_new_game(self):
        """记着的不到一页（3 个、4 个），下一局已经多于一页：第一页上一个都对不上，或只有开头两个正好一样（同一个角色常拿的）时，
        照只有一页时的规矩全算少了，判出新的一局，不当成被挤到了下一页。"""
        for n, shared in ((3, 0), (4, 2)):
            with self.subTest(n=n, shared=shared):
                t = Ledger(n)
                self.away(t)
                ev = t.show(0, owned=t.truth[:shared] + FILLER[: PAGE_SLOTS - shared], page="first")
                self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))

    def test_misjudged_new_game_undo(self):
        """对局中途才启动、第一页没读过：记着的只有第 13、14 个。离开后回来看第一页，记着的都不在上面、第一页上的又都不在
        记着的前 10 个里（多于 RESUME_SHIFT_MAX 个，一个都不原谅），判成新的一局。点「撤销」恢复原来的记录、核对结束；面板还
        开着，下一帧照常按第一页核对，再看第二页就和实际一样，不再判新的一局。不撤销时看完两页，已有筹码也和实际一样。"""
        for undo in (True, False):
            with self.subTest(undo=undo):
                t = Ledger(14, skip=tuple(range(12)))
                self.assertEqual(t.owned, t.truth[12:])
                self.away(t)
                ev = t.show(0)
                self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
                self.assertEqual(t.owned, t.truth[:10])
                if undo:
                    self.assertTrue(t.s.undo_new_game())
                    t.drain()
                    self.assertEqual(t.owned, t.truth[12:])
                    self.assertFalse(t.s.game._resume_check)
                    t.show(0)
                    self.assertEqual(t.owned, t.truth[:10] + t.truth[12:])
                t.show(1)
                self.assertEqual(t.owned, t.truth)
                self.assertEqual(len(t.kinds("newgame")), 1)

    def test_first_page_new_game(self):
        """下一局的第一页和记着的前 10 个大多对不上：判出新的一局。"""
        t = Ledger(15)
        self.away(t)
        nxt = TRUTH[:2] + MISREAD + FILLER[:5]
        ev = t.show(0, owned=nxt)
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))

    def test_only_page_after_many(self):
        """记着 12 个（两页），回来时面板只有一页、5 个：同一局里筹码只增不减，判出新的一局。记着的多于一页，读成只有一页要连着
        两帧：第一帧不判、核对留着（也没记下核对的起点），第二帧判出。"""
        t = Ledger(12)
        self.away(t)
        self.assertIsNone(t.show(0, owned=FILLER[:5], page="only"))
        self.assertTrue(t.s.game._resume_check)
        self.assertIsNone(t.s.game._resume_base)
        ev = t.show(0, owned=FILLER[:5], page="only")
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))

    def test_covered_arrow_during_check(self):
        """离开后回来看第一页，有一帧右箭头被盖住、读成只有一页（记着 15 个，按只有一页比会少 5 个、判成新的一局）：不判，核对
        留着；下一帧箭头露出来，照第一页核对，是同一局。"""
        t = Ledger(15)
        self.away(t)
        self.assertIsNone(t.show(0, page="only"))
        self.assertTrue(t.s.game._resume_check)
        t.show(0)
        self.assertFalse(t.s.game._resume_check)
        self.assertEqual(t.kinds("newgame"), [])
        self.assertEqual(t.owned, t.truth)


class PanelGraceTest(_FakeReads):
    """E：识别流程（FrameProcessor）读面板的节奏：面板开着时每帧都读；翻页动画那一帧不算面板，刚看不到的 PANEL_GRACE_S 秒
    以内照样每帧读，翻过去的那一页马上读到（不等 hud_ms）；面板关了，过了这段时间才回到按 hud_ms 读。识别函数换成假的
    （见 tests/test_pipeline.py 的 _FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.reading = None
        self.panel_reads = 0

        def fake_panel(img, db, **kw):
            self.panel_reads += 1
            return self.reading

        patcher = mock.patch.object(pipeline, "read_chip_panel", side_effect=fake_panel)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session.set_owned(TRUTH[:15])
        self.session.take_events()

    def reads_at(self, t: float, reading) -> int:
        """t 秒处的一帧，画面上是 reading：这一帧读了几次面板。"""
        self.reading = reading
        before = self.panel_reads
        self.frame(t)
        return self.panel_reads - before

    def test_turned_page_read_at_once(self):
        turning = SimpleNamespace(is_panel=False, self_tab=False, owned=[], unknown_slots=0, confidence=0.0)
        self.assertEqual(self.reads_at(10.0, panel(TRUTH[:10], "first")), 1)
        self.assertEqual(self.reads_at(10.5, panel(TRUTH[:10], "first")), 1)
        self.assertEqual(self.reads_at(11.0, turning), 1)
        self.assertEqual(self.reads_at(11.5, panel(TRUTH[10:15], "last")), 1)  # 翻过去的第二页：不等 hud_ms
        self.assertEqual({self.session.game.chip_sources[c] for c in TRUTH[10:15]}, {"panel"})
        self.assertGreater(pipeline.PANEL_GRACE_S, 1.5)
        for t in (12.0, 12.5, 13.0):  # 面板关了：最后看到面板以后的 PANEL_GRACE_S 秒以内照样每帧读
            self.assertEqual(self.reads_at(t, None), 1)
        self.assertEqual(self.reads_at(13.6, None), 0)  # 过了这段时间：离上次读不到 hud_ms，不读
        self.assertEqual(self.reads_at(16.1, None), 1)  # 照平时按 hud_ms


if __name__ == "__main__":
    unittest.main()
