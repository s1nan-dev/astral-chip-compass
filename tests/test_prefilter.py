"""三选一预筛（chip_select.looks_like_overlay_screen）的两段。

第一段（dark_band，上半段有一行大半是暗的）的两段式写法：判定和「整幅缩到 96x54」的原写法逐张相同。
原写法整幅缩一次（4K 约 14~18 毫秒）；两段式先隔点取样（约 0.4 毫秒），取样算出的最大暗行占比离阈值 0.6 超过
PRE_SAMPLE_MARGIN 时直接用取样结果，否则按原写法再算一次。NEAR 这张样本在缩到 1920 / 1366 宽时两种写法的判定本来就
不同（取样判成「像」、整幅判成「不像」），正好守住「离阈值近一定要按原写法算」这条。

第二段（title_band_visible，三选一标题带该在的上下两条边往里几乎全黑、往外不是）：
  实机截图：三选一样本（各种比例、全队三选一、图标还没加载的出场动画、选中、刷新按钮不在的）原图和缩小以后都过；
    战斗、玩家详情弹窗、卡牌商店、压暗的对局画面上方也有黑带，过得了第一段，第二段拦下；
  分数最靠近门槛的实机帧（samples/pages，录像和诊断包里的）：金色筹码的光盖住一段标题带的出场、刷新动画，出场头一帧，
    上下有黑边的 16:10 窗口都过；开战转场的黑色爆炸图案拦下；
  合成画面（不要样本）：16:9、16:10、21:9、4:3、竖长窗口里，按界面缩放画在中线以上 400~300 个界面单位的黑带都认得出；
    带子挪开、太薄、满屏全黑、带子外也是黑的都不认；金色筹码的光盖住一段边、带子上面或下面很暗、整个画面抬亮一点时照样认得出。
不做文字识别。
"""
import json
import unittest

import cv2
import numpy as np

from advisor.vision import chip_select, pipeline
from advisor.vision.chip_select import PRE_SAMPLE_MARGIN, SELECT_BAND_EDGES, ui_scale
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
NEAR = "20261003_132306_card_give_al_skill_2560x1369.jpg"  # 1920/1366 宽时两写法判定不同
SELECT = "20261002_013617_chipselect_selected_state.png"
BATTLE = "20261002_012918_hud_round1.png"
PANEL = "20261002_013927_chips_panel_one_chip.png"
KEEP = (NEAR, SELECT, BATTLE, PANEL)
WIDTHS = (None, 1920, 1366)
# 上方也有黑带、过得了第一段的反例：战斗、玩家详情弹窗、卡牌商店、压暗的对局画面
DARK_TOPS = ("20261002_021351_battle_scene.png", "20261002_212105_player_detail_popup_1600x855.jpg",
             "20261002_214207_card_shop_transfer_button_1600x855.jpg",
             "20261003_131150_hud_round9_star2_hp1_dimmed_2560x1369.jpg")
# 录像、诊断包里的几帧（samples/pages）：出场和刷新时金色筹码的光盖住一段标题带、出场头一帧、上下有黑边的 16:10 窗口
TIGHT_SELECTS = ("pages/20261017_012808_f002945_chipselect_enter_glow_3840x2160.png",
                 "pages/20261105_010644_f040312_chipselect_refresh_glow_1920x1080.png",
                 "pages/20261105_010644_f031997_chipselect_enter_first_1920x1080.png",
                 "pages/20261018_103849_chipselect_black_bars_2560x1600.png")
# 开战转场的黑色爆炸图案：过得了第一段、第二段分数最高的反例
BLAST = "pages/20261105_010644_k00788_battle_start_blast_3840x2160.jpg"


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def reference(img: np.ndarray) -> bool:
    """第一段的原写法：把整幅缩到 96x54 再判。"""
    small = cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return bool(((gray[4:27] < 45).mean(axis=1) > 0.6).any())


def strided_share(img: np.ndarray) -> float:
    h, w = img.shape[:2]
    step = max(1, min(w // 96, h // 54) // 4)
    small = cv2.resize(img[::step, ::step], (96, 54), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float((gray[4:27] < 45).mean(axis=1).max())


def shrink(img: np.ndarray, width: int):
    if width is None or img.shape[1] <= width:
        return img
    return cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)


@unittest.skipUnless(samples.samples_ok(*KEEP), samples.samples_reason(*KEEP))
class PrefilterTest(unittest.TestCase):
    def test_matches_full_resize(self):
        """每张样本 × 几种宽度：第一段的两段式判定和原写法逐张相同。"""
        for name in KEEP:
            img = load(name)
            for width in WIDTHS:
                im, _ = pipeline.trim_borders(shrink(img, width))
                self.assertEqual(chip_select.dark_band(im), reference(im), f"{name} @{width}")

    def test_near_threshold_recomputed(self):
        """NEAR 在 1920 宽时取样占比落在阈值附近：两段式必须按原写法再算一次，判定和原写法一样（都是「不像」）。"""
        im, _ = pipeline.trim_borders(shrink(load(NEAR), 1920))
        share = strided_share(im)
        self.assertLessEqual(abs(share - 0.6), PRE_SAMPLE_MARGIN)  # 确实落在余量里，这条才是有效样本
        self.assertFalse(reference(im))                            # 原写法判「不像」
        self.assertFalse(chip_select.dark_band(im))               # 两段式也判「不像」
        self.assertFalse(chip_select.looks_like_overlay_screen(im))

    def test_select_screen_like(self):
        """三选一样本判成「像」：两段都过。"""
        for name in (SELECT,):
            im, _ = pipeline.trim_borders(load(name))
            self.assertTrue(chip_select.dark_band(im))
            self.assertTrue(chip_select.title_band_visible(im))
            self.assertTrue(chip_select.looks_like_overlay_screen(im))

    def test_matches_full_resize_on_plain_shapes(self):
        """合成画面（全黑、全白、噪声、下方黑带）：第一段的两段式和原写法相同；整个预筛不比第一段宽。"""
        rng = np.random.default_rng(2026)
        shapes = [np.zeros((900, 1600, 3), np.uint8), np.full((900, 1600, 3), 255, np.uint8),
                  rng.integers(0, 256, (900, 1600, 3), dtype=np.uint8)]
        band = np.full((900, 1600, 3), 255, np.uint8)
        band[700:850] = 0  # 黑带在画面下方，不在 gray[4:27] 那一段里
        shapes.append(band)
        for i, im in enumerate(shapes):
            self.assertEqual(chip_select.dark_band(im), reference(im), f"shape {i}")
            if not reference(im):
                self.assertFalse(chip_select.looks_like_overlay_screen(im), f"shape {i}")


def _labels() -> list[dict]:
    return json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))["samples"]


@unittest.skipUnless(samples.samples_ok("labels.json", *DARK_TOPS), samples.samples_reason("labels.json", *DARK_TOPS))
class TitleBandSampleTest(unittest.TestCase):
    """第二段在实机截图上：三选一一张不漏，上方有黑带的反例拦下。"""

    def test_every_select_sample_passes(self):
        """labels.json 里的三选一样本（3840x2054、2000x1087、21:9、4:3、预览图；全队、出场动画、选中、刷新按钮不在的）：
        原图、缩到 1920 宽、缩到一半都过（缩一半不低于最小窗口：16:9 宽 1000、更宽的窗口高 563）。"""
        checked = 0
        for item in _labels():
            if not item["scene"].startswith("chip_select") or not (SAMPLES / item["file"]).exists():
                continue
            img = load(item["file"])
            h, w = img.shape[:2]
            half = max(0.5, pipeline.MIN_UI_SCALE / ui_scale(w, h))
            variants = {"原图": img, "1920 宽": shrink(img, 1920),
                        "缩小": cv2.resize(img, (round(w * half), round(h * half)), interpolation=cv2.INTER_AREA)}
            for variant, im in variants.items():
                im, _ = pipeline.trim_borders(im)
                with self.subTest(file=item["file"], variant=variant):
                    self.assertTrue(chip_select.looks_like_overlay_screen(im), "预筛把三选一画面漏掉了")
            checked += 1
        self.assertGreater(checked, 0)

    def test_dark_tops_blocked(self):
        """战斗、玩家详情弹窗、卡牌商店、压暗的对局画面：过得了第一段，第二段拦下（原图和 1280 宽）。"""
        for name in DARK_TOPS:
            for width in (None, 1280):
                im, _ = pipeline.trim_borders(shrink(load(name), width))
                with self.subTest(file=name, width=width):
                    self.assertTrue(chip_select.dark_band(im))
                    self.assertFalse(chip_select.title_band_visible(im))
                    self.assertFalse(chip_select.looks_like_overlay_screen(im))


@unittest.skipUnless(samples.samples_ok(*TIGHT_SELECTS, BLAST), samples.samples_reason(*TIGHT_SELECTS, BLAST))
class TitleBandTightTest(unittest.TestCase):
    """第二段在分数最靠近门槛的实机帧上：动画里的三选一照样认得出，开战转场拦下。"""

    def test_tight_selects_pass(self):
        for name in TIGHT_SELECTS:
            for width in (None, 1280):
                im, _ = pipeline.trim_borders(shrink(load(name), width))
                with self.subTest(file=name, width=width):
                    self.assertTrue(chip_select.looks_like_overlay_screen(im))

    def test_blast_blocked(self):
        for width in (None, 1280):
            im, _ = pipeline.trim_borders(shrink(load(BLAST), width))
            with self.subTest(width=width):
                self.assertTrue(chip_select.dark_band(im))
                self.assertFalse(chip_select.title_band_visible(im))


def _scene(w: int, h: int, seed: int = 7, level: tuple[int, int] = (50, 140)) -> np.ndarray:
    """压暗的棋盘：带颜色的色块（不是纯黑），大小约 40 个界面单位。"""
    rng = np.random.default_rng(seed)
    cell = max(4, round(40 * ui_scale(w, h)))
    blocks = rng.integers(level[0], level[1], (h // cell + 1, w // cell + 1, 3), dtype=np.uint8)
    return np.ascontiguousarray(cv2.resize(blocks, None, fx=cell, fy=cell, interpolation=cv2.INTER_NEAREST)[:h, :w])


def _draw_band(img: np.ndarray, top_ui: float = SELECT_BAND_EDGES[0], bottom_ui: float = SELECT_BAND_EDGES[1],
               text: bool = True) -> np.ndarray:
    """照三选一标题带画一条：离中线 top_ui~bottom_ui 个界面单位、横贯全宽的纯黑带，中间几行有白色的「字」。"""
    h, w = img.shape[:2]
    unit = ui_scale(w, h)
    y0, y1 = round(h / 2 + top_ui * unit), round(h / 2 + bottom_ui * unit)
    img[y0:y1] = 0
    if text:
        ty0, ty1 = round(h / 2 + (top_ui + 35) * unit), round(h / 2 + (bottom_ui - 35) * unit)
        for k in range(12):  # 标题：中间一串字块
            x0 = round(w / 2 + (-240 + 40 * k) * unit)
            img[ty0:ty1, x0:x0 + max(1, round(28 * unit))] = 255
        img[ty0:ty1, round(w * 0.05):round(w * 0.2)] = 230  # 左边的概率行
    return img


class TitleBandSynthTest(unittest.TestCase):
    """第二段在合成画面上（不要样本）。"""

    SIZES = {"16:9": (1920, 1080), "16:10": (2560, 1600), "21:9": (2560, 1080), "4:3": (1440, 1080),
             "竖长": (1080, 1920), "4K": (3840, 2160)}

    def test_band_found_in_every_window(self):
        for name, (w, h) in self.SIZES.items():
            with self.subTest(window=name):
                self.assertTrue(chip_select.title_band_visible(_draw_band(_scene(w, h))))

    def test_small_shift_still_found(self):
        """整条带子上下错开 20 个界面单位（截图裁得不齐、手机实拍）照样认得出。"""
        for d in (-20, 20):
            img = _draw_band(_scene(1920, 1080), SELECT_BAND_EDGES[0] + d, SELECT_BAND_EDGES[1] + d)
            self.assertTrue(chip_select.title_band_visible(img), d)

    def test_wrong_place_or_height(self):
        """带子挪开 80 个界面单位、只有约 24 个界面单位高（战斗画面顶上那种黑条），都不认。"""
        top, bottom = SELECT_BAND_EDGES
        cases = {"往上挪": (top - 80, bottom - 80), "往下挪": (top + 80, bottom + 80), "太薄": (top, top + 24)}
        for name, (a, b) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(chip_select.title_band_visible(_draw_band(_scene(1920, 1080), a, b, text=False)))

    def test_dark_above(self):
        """带子上面那一段也很暗（七成是纯黑）、只有下边清楚：带子外两段取平均，照样认得出（宁可多认几帧，不漏三选一）。"""
        img = _scene(1920, 1080)
        unit = ui_scale(1920, 1080)
        above = img[round(540 + (SELECT_BAND_EDGES[0] - 40) * unit):round(540 + SELECT_BAND_EDGES[0] * unit)]
        rng = np.random.default_rng(5)
        above[rng.random(above.shape[:2]) < 0.7] = 0
        self.assertTrue(chip_select.title_band_visible(_draw_band(img)))

    def test_all_dark(self):
        """满屏全黑（转场）、带子外面也是黑的：没有上下两条边，不认。"""
        self.assertFalse(chip_select.title_band_visible(np.zeros((1080, 1920, 3), np.uint8)))
        img = _scene(1920, 1080)
        unit = ui_scale(1920, 1080)
        img[round(540 - 470 * unit):round(540 - 230 * unit)] = 0  # 黑的一大片把带子和上下都盖住
        self.assertFalse(chip_select.title_band_visible(img))

    def test_partly_covered(self):
        """出场动画里金色筹码的光盖住三成多的带子（贴边那几行也盖住）：照样认得出。"""
        img = _draw_band(_scene(1920, 1080))
        unit = ui_scale(1920, 1080)
        y0, y1 = round(540 + (SELECT_BAND_EDGES[0] - 10) * unit), round(540 + (SELECT_BAND_EDGES[1] + 10) * unit)
        for x0, x1 in ((0.10, 0.27), (0.62, 0.80)):
            img[y0:y1, round(1920 * x0):round(1920 * x1)] = (40, 200, 250)
        self.assertTrue(chip_select.title_band_visible(img))

    def test_very_dark_map_below(self):
        """带子下面是很暗的地图（压暗以后三成多是纯黑的）：照样认得出。"""
        img = _scene(1920, 1080)
        unit = ui_scale(1920, 1080)
        below = img[round(540 + SELECT_BAND_EDGES[1] * unit):]
        rng = np.random.default_rng(11)
        below[rng.random(below.shape[:2]) < 0.35] = 0
        self.assertTrue(chip_select.title_band_visible(_draw_band(img)))

    def test_black_lifted(self):
        """滤镜、色彩设置把整个画面抬亮一点，标题带黑得不那么纯：抬亮 SELECT_BAND_BLACK 以内照样认得出；再亮就不算纯黑了
        （第一段还过，按热键的那一帧还认得出）。"""
        img = _draw_band(_scene(1920, 1080))
        for lift, want in ((chip_select.SELECT_BAND_BLACK, True), (chip_select.SELECT_BAND_BLACK + 5, False)):
            lifted = cv2.add(img, np.full(img.shape, lift, np.uint8))
            with self.subTest(lift=lift):
                self.assertTrue(chip_select.dark_band(lifted))
                self.assertEqual(chip_select.title_band_visible(lifted), want)

    def test_tiny_or_empty(self):
        self.assertFalse(chip_select.title_band_visible(np.zeros((8, 8, 3), np.uint8)))
        self.assertFalse(chip_select.looks_like_overlay_screen(np.zeros((8, 8, 3), np.uint8)))


if __name__ == "__main__":
    unittest.main()
