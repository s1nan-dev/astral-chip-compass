"""程序图标（advisor/ui/icon.py、packaging/app.ico）。

图案：深色圆盘、金色圆环、正中一颗金色四角星，96 像素以上沿外圈写 ASTRAL CHIP COMPASS，
更小的尺寸不写外圈的字。用 Qt 的 offscreen 平台，不会弹出窗口。
"""
import math
import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage  # noqa: E402

from advisor.ui.icon import CANVAS, DISC, ICON_SIZES, LETTERS, LETTERS_MIN, RING, STAR, _params, app_icon  # noqa: E402
from advisor.ui.icon import render_icon  # noqa: E402
from tests.support import qtenv  # noqa: E402

APP = qtenv.app()
ICO = Path(__file__).resolve().parents[1] / "packaging" / "app.ico"
BAND = 31  # 外圈字那一圈离圆心多远（设计稿单位；字的基线在 29.5）
BRIGHT = 100  # 「比圆盘亮」的口径：三个通道的和比圆盘多这么多


def letters_like(c: QColor) -> bool:
    """这一点是外圈的字：比圆盘亮得多，而且是灰蓝色。圆环的金色红远大于蓝，掺进深色也还是这样，
    所以小尺寸上圆环挨着那一圈的边、抗锯齿出来的过渡色不会被当成字。"""
    return sum(c.getRgb()[:3]) > sum(DISC.getRgb()[:3]) + BRIGHT and c.blue() > c.red() + 10


def pixel(img: QImage, x: float, y: float) -> QColor:
    return img.pixelColor(min(img.width() - 1, int(x)), min(img.height() - 1, int(y)))


def at(size: int, x: float, y: float) -> tuple[float, float]:
    """把设计稿单位的一点 (x, y)（原点在画布中心）换算成 size 像素图里的坐标。"""
    k = size / CANVAS
    return size / 2 + x * k, size / 2 + y * k


def close(a: QColor, b: QColor, tol: int = 40) -> bool:
    return abs(a.red() - b.red()) <= tol and abs(a.green() - b.green()) <= tol and abs(a.blue() - b.blue()) <= tol


def band(size: int) -> list[QColor]:
    """外圈字那一圈（离圆心 BAND 个设计稿单位）上沿整圈取的样。"""
    img = render_icon(size)
    return [pixel(img, *at(size, BAND * math.cos(step * math.pi / 360), BAND * math.sin(step * math.pi / 360)))
            for step in range(720)]


class RenderTest(unittest.TestCase):
    def test_shape_and_colors(self):
        for size in ICON_SIZES:
            with self.subTest(size=size):
                img = render_icon(size)
                self.assertEqual((img.width(), img.height()), (size, size))
                # 四角透明（圆盘没有占满画布）
                for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
                    self.assertEqual(img.pixelColor(x, y).alpha(), 0)
                # 正中是星星
                self.assertTrue(close(pixel(img, *at(size, 0, 0)), STAR))
                # 圆环中线上（上、左、右三处）是圆环的颜色
                ring_r = _params(size)[0]
                for dx, dy in ((0, -ring_r), (-ring_r, 0), (ring_r, 0)):
                    self.assertTrue(close(pixel(img, *at(size, dx, dy)), RING), f"圆环中线的 ({dx}, {dy})")
                # 45° 方向上、星星和圆环之间是圆盘的颜色
                d = 12 / math.sqrt(2)
                self.assertTrue(close(pixel(img, *at(size, d, d)), DISC), "45° 上不是圆盘的颜色")

    def test_letters_only_from_96(self):
        # 外圈字那一圈：96 像素以上沿整圈都有字（有画满、颜色和外圈的字一致的像素），更小的尺寸一个字都没有
        for size in (16, 24, 32, 64, LETTERS_MIN, 128, 256):
            with self.subTest(size=size):
                colors = band(size)
                lit = [c for c in colors if c.alpha() and letters_like(c)]
                if size >= LETTERS_MIN:
                    self.assertGreaterEqual(len(lit), 720 // 8, f"{size} 像素的外圈没有字")
                    self.assertTrue(any(close(c, LETTERS) for c in colors), f"{size} 像素外圈的字颜色不对")
                else:
                    self.assertEqual(lit, [], f"{size} 像素的外圈不该有字")

    def test_app_icon_has_every_size(self):
        icon = app_icon()
        self.assertEqual(sorted(s.width() for s in icon.availableSizes()), sorted(ICON_SIZES))

    def test_packaged_ico_matches_drawing(self):
        # packaging/app.ico 只在没有时由 render_icon 画出来（打包不每次重画）：仓库里这份要和现在的画法一致，
        # 画法改了以后删掉它再运行 ensure_icon()（或重新打包）
        from PIL import Image

        with Image.open(ICO) as ico:
            self.assertEqual(sorted(ico.info["sizes"]), sorted((s, s) for s in ICON_SIZES))
            for size in (16, 32, 256):
                with self.subTest(size=size):
                    frame = ico.ico.getimage((size, size)).convert("RGBA")
                    img = render_icon(size).convertToFormat(QImage.Format_RGBA8888)
                    worst = 0
                    for y in range(0, size, max(1, size // 32)):
                        for x in range(0, size, max(1, size // 32)):
                            c = img.pixelColor(x, y)
                            worst = max(worst, *(abs(a - b) for a, b in zip(frame.getpixel((x, y)), c.getRgb())))
                    self.assertLessEqual(worst, 2, "packaging/app.ico 和 advisor/ui/icon.py 的画法不一致：删掉 "
                                                   "packaging/app.ico 再运行 ensure_icon()（或重新打包）")


if __name__ == "__main__":
    unittest.main()
