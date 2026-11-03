"""卡片、标签的画法登记表（advisor/ui/decor.py 的 CARD_KINDS、TAG_KINDS、TAG_PLACE）：ChromeLook 里写了表里没有的种类时按
浅灰卡片、橙色圆角标签画；编号标题（TAG_KIND 为 index）的标签不给 px 时编号和标题都用传进来的字体（标签部件
widgets.TagLabel 借这个画法写字），给了 px 时用编号标题的字体（chrome.Tag）。

离屏 Qt，不弹窗口。
"""
import dataclasses
import unittest

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter

from advisor.ui import decor, theme
from tests.support import qtenv

APP = qtenv.app()
TEXT = "识别模式"


def draw(paint, size=(240, 60)) -> QImage:
    """在透明底的图上画一次（paint(p)），返回画好的图。"""
    img = QImage(*size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    try:
        paint(p)
    finally:
        p.end()
    return img


class UnknownKindTest(unittest.TestCase):
    """表里没有的种类：卡片按浅灰卡片（gray），标签按橙色圆角标签（tag），尺寸、摆法也一样。"""

    def test_card(self):
        look = theme.CLASSIC_WHITE.chrome
        self.assertEqual(look.CARD_KIND, "gray")
        odd = dataclasses.replace(look, CARD_KIND="no-such-card")
        self.assertEqual(decor.card_metrics(odd.CARD_KIND), decor.card_metrics("gray"))
        rect = QRectF(8, 8, 200, 40)
        with theme.drawing(theme.CLASSIC_WHITE_KEY):
            for radius, shadow in ((None, 4.0), (10, 0)):
                with self.subTest(radius=radius, shadow=shadow):
                    self.assertEqual(draw(lambda p: decor.card(p, rect, odd, 11, radius, shadow)),
                                     draw(lambda p: decor.card(p, rect, look, 11, radius, shadow)))

    def test_tag(self):
        look = theme.CLASSIC_WHITE.chrome
        self.assertEqual(look.TAG_KIND, "tag")
        odd = dataclasses.replace(look, TAG_KIND="no-such-tag")
        self.assertEqual(decor.tag_place(odd.TAG_KIND), decor.tag_place("tag"))
        rect = QRectF(4, 4, 200, 40)
        with theme.drawing(theme.CLASSIC_WHITE_KEY):
            font = theme.title_font(look.TAG_PX)
            self.assertEqual(decor.tag_size(odd.TAG_KIND, font, TEXT, "", look.TAG_PX, odd),
                             decor.tag_size("tag", font, TEXT, "", look.TAG_PX, look))
            self.assertEqual(draw(lambda p: decor.tag(p, rect, TEXT, font, odd)),
                             draw(lambda p: decor.tag(p, rect, TEXT, font, look)))


class IndexTagFontTest(unittest.TestCase):
    """编号标题（碳纤流光）的标签：不给 px 时编号和标题都用传进来的字体，给了 px 时编号用数字字体的窄体、标题用斜体粗体。"""

    def setUp(self):
        self.look = theme.CARBON_FLOW.chrome
        self.assertEqual(self.look.TAG_KIND, "index")
        self.rect = QRectF(4, 4, 230, 44)

    def test_without_px_uses_given_font(self):
        look, rect = self.look, self.rect
        with theme.drawing(theme.CARBON_FLOW_KEY):
            font = theme.title_font(20)  # 和编号标题的字体（斜体粗体、TAG_PX）不同
            got = draw(lambda p: decor.tag(p, rect, TEXT, font, look))
            want = draw(lambda p: decor.index_title(p, rect, "", TEXT, font, font, look))
            titled = draw(lambda p: decor.index_title(p, rect, "", TEXT, decor.index_font(look.TAG_PX),
                                                      decor.italic_title_font(look.TAG_PX), look))
        self.assertEqual(got, want)
        self.assertNotEqual(got, titled)  # 两种字体画出来确实不同：上面的相等不是碰巧

    def test_with_px_uses_index_fonts(self):
        look, rect = self.look, self.rect
        with theme.drawing(theme.CARBON_FLOW_KEY):
            font = theme.title_font(20)
            got = draw(lambda p: decor.tag(p, rect, TEXT, font, look, "", "01", 15))
            want = draw(lambda p: decor.index_title(p, rect, "01", TEXT, decor.index_font(15),
                                                    decor.italic_title_font(15), look))
            size = decor.tag_size("index", font, TEXT, "01", 15, look)
            lead = QFontMetricsF(decor.index_font(15)).horizontalAdvance("01") + 10
            width = round(lead + QFontMetricsF(decor.italic_title_font(15)).horizontalAdvance(TEXT) + 8)
        self.assertEqual(got, want)
        self.assertEqual((size.width(), size.height()), (width, font.pixelSize() + 12))


if __name__ == "__main__":
    unittest.main()
