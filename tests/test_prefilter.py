"""三选一预筛（chip_select.looks_like_overlay_screen）的两段式写法：判定和「整幅缩到 96x54」的原写法逐张相同。

原写法整幅缩一次（4K 约 14~18 毫秒）；两段式先隔点取样（约 0.4 毫秒），取样算出的最大暗行占比离阈值 0.6 超过
PRE_SAMPLE_MARGIN 时直接用取样结果，否则按原写法再算一次。NEAR 这张样本在缩到 1920 / 1366 宽时两种写法的判定本来就
不同（取样判成「像」、整幅判成「不像」），正好守住「离阈值近一定要按原写法算」这条。
用实机截图，不做文字识别。
"""
import unittest
from pathlib import Path

import cv2
import numpy as np

from advisor.vision import chip_select, pipeline
from advisor.vision.chip_select import PRE_SAMPLE_MARGIN
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
NEAR = "20261003_132306_card_give_al_skill_2560x1369.jpg"  # 1920/1366 宽时两写法判定不同
SELECT = "20261002_013617_chipselect_selected_state.png"
BATTLE = "20261002_012918_hud_round1.png"
PANEL = "20261002_013927_chips_panel_one_chip.png"
KEEP = (NEAR, SELECT, BATTLE, PANEL)
WIDTHS = (None, 1920, 1366)


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def reference(img: np.ndarray) -> bool:
    """原写法：把整幅缩到 96x54 再判。"""
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
        """每张样本 × 几种宽度：两段式的判定和原写法逐张相同。"""
        for name in KEEP:
            img = load(name)
            for width in WIDTHS:
                im, _ = pipeline.trim_borders(shrink(img, width))
                self.assertEqual(chip_select.looks_like_overlay_screen(im), reference(im), f"{name} @{width}")

    def test_near_threshold_recomputed(self):
        """NEAR 在 1920 宽时取样占比落在阈值附近：两段式必须按原写法再算一次，判定和原写法一样（都是「不像」）。"""
        im, _ = pipeline.trim_borders(shrink(load(NEAR), 1920))
        share = strided_share(im)
        self.assertLessEqual(abs(share - 0.6), PRE_SAMPLE_MARGIN)  # 确实落在余量里，这条才是有效样本
        self.assertFalse(reference(im))                            # 原写法判「不像」
        self.assertFalse(chip_select.looks_like_overlay_screen(im))  # 两段式也判「不像」

    def test_select_screen_like(self):
        """三选一样本判成「像」。"""
        for name in (SELECT,):
            im, _ = pipeline.trim_borders(load(name))
            self.assertTrue(chip_select.looks_like_overlay_screen(im))

    def test_matches_full_resize_on_plain_shapes(self):
        """合成画面（全黑、全白、噪声、下方黑带）：两段式和原写法相同。"""
        rng = np.random.default_rng(2026)
        shapes = [np.zeros((900, 1600, 3), np.uint8), np.full((900, 1600, 3), 255, np.uint8),
                  rng.integers(0, 256, (900, 1600, 3), dtype=np.uint8)]
        band = np.full((900, 1600, 3), 255, np.uint8)
        band[700:850] = 0  # 黑带在画面下方，不在 gray[4:27] 那一段里
        shapes.append(band)
        for i, im in enumerate(shapes):
            self.assertEqual(chip_select.looks_like_overlay_screen(im), reference(im), f"shape {i}")


if __name__ == "__main__":
    unittest.main()
