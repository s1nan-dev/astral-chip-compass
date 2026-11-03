"""方案列表画法的焦点框（scheme_cards）：基类画完一套方案的那一行以后调 paint_focus 画焦点框，分组标题不画。

外观基准里编辑窗口的焦点都在角色下拉框上，方案列表的行从来不带焦点，焦点框靠这里守住。每套皮肤用它的编辑窗口排法
所用的那种画法，在离屏的图上把同一行带焦点、不带焦点各画一遍，逐像素比：
- 一套方案的行（默认推荐、我的方案）：带焦点时多出焦点框（和不带焦点时不同），选中、悬停、禁用时都是这样；
- 分组标题（「── 我的方案 ──」）：带不带焦点画得一样（不画焦点框）；
- paint_focus 本身只在带焦点时画：不带焦点时调它，图上什么都不多。
默认排法的普通一行（_SchemeListDelegate）整套交给 Qt 的画法，焦点框也是 Qt 画的，不在这里比。
"""
import unittest

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyleOptionViewItem

from advisor.ui import scheme_cards, theme
from tests.support import qtenv

APP = qtenv.app()

# 编辑窗口各排法的方案列表用哪种画法（scheme_editor/layouts.py 里各排法建方案列表的地方）
DELEGATE_OF = {"side_tabs": "_SchemeTabDelegate", "card_row": "_SchemeCardDelegate",
               "card_column": "_SchemeTileDelegate", "side_column": "_SchemeStickerDelegate",
               "tile_column": "_SchemeWellDelegate", "tab_strip": "_SchemeStripDelegate"}
S = QStyle.StateFlag
STATES = {"普通": S.State_Enabled | S.State_Active, "选中": S.State_Enabled | S.State_Active | S.State_Selected,
          "悬停": S.State_Enabled | S.State_Active | S.State_MouseOver, "禁用": S.State_Active,
          "选中、窗口不在前台": S.State_Enabled | S.State_Selected}
ROWS = (("默认推荐", "", "默认"), ("── 我的方案 ──", None, None), ("治愈坦克", "id-1", "我的"))
HEADER, MINE = 1, 2


class SchemeCardsFocusRenderTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(theme.set_skin, theme.DEFAULT_KEY)

    def skins(self):
        """（皮肤, 它的编辑窗口排法用的画法类名）；默认排法的普通一行不在内。六种画法都要有皮肤用到；皮肤用了这里
        没有的排法时先报出来（新加的排法把它的方案列表画法补进 DELEGATE_OF）。"""
        layouts = {skin.layouts.EDITOR for skin in theme.skins()}
        self.assertEqual(sorted(layouts - set(DELEGATE_OF) - {"default"}), [])
        out = [(skin, DELEGATE_OF[skin.layouts.EDITOR]) for skin in theme.skins() if skin.layouts.EDITOR in DELEGATE_OF]
        self.assertEqual({name for _, name in out}, set(DELEGATE_OF.values()))
        return out

    def view(self) -> QListWidget:
        view = QListWidget()
        self.addCleanup(view.deleteLater)
        view.resize(232, 300)
        for text, scheme_id, tag in ROWS:
            item = QListWidgetItem(text)
            item.setData(scheme_cards._ID_ROLE, scheme_id)
            if tag is not None:
                item.setData(scheme_cards._TAG_ROLE, tag)
            else:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            view.addItem(item)
        return view

    @staticmethod
    def draw(view, delegate, row, state, method: str | None = "paint") -> bytes:
        """在只铺了底色的图上画 row 这一行（method 为 None 时不画，得到同样大小的底）。"""
        index = view.model().index(row, 0)
        opt = QStyleOptionViewItem()
        opt.initFrom(view)
        opt.font = view.font()
        opt.widget = view
        hint = delegate.sizeHint(opt, index)
        opt.rect = QRect(2, 3, hint.width(), hint.height())
        opt.state = state
        img = QImage(opt.rect.right() + 6, opt.rect.bottom() + 6, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(40, 44, 52))
        if method is not None:
            painter = QPainter(img)
            getattr(delegate, method)(painter, opt, index)
            painter.end()
        return bytes(img.constBits())

    def test_rows_draw_focus_headers_do_not(self):
        for skin, name in self.skins():
            theme.set_skin(skin.key)
            view = self.view()
            delegate = getattr(scheme_cards, name)(view)
            for row, (text, _, _) in enumerate(ROWS):
                for label, state in STATES.items():
                    with self.subTest(skin=skin.key, delegate=name, row=text, state=label):
                        focused = self.draw(view, delegate, row, state | S.State_HasFocus)
                        plain = self.draw(view, delegate, row, state)
                        if row == HEADER:
                            self.assertEqual(focused, plain)  # 分组标题不画焦点框
                        else:
                            self.assertNotEqual(focused, plain)  # 一套方案的行带焦点时画焦点框

    def test_used_row_marked(self):
        """悬浮窗正在用的那一套（_USED_ROLE 为真，行尾的字换成「使用中」）：七种画法都画得出来，和没在用的同一行画得不一样
        （游戏内风格的页签平时不写「默认」「我的」，也另压一枚「使用中」）；选中、悬停时也一样。"""
        default = next(s for s in theme.skins() if s.layouts.EDITOR == "default")
        for skin, name in [(default, "_SchemeListDelegate"), *self.skins()]:
            theme.set_skin(skin.key)
            view = self.view()
            delegate = getattr(scheme_cards, name)(view)
            item = view.item(MINE)
            for label, state in STATES.items():
                with self.subTest(skin=skin.key, delegate=name, state=label):
                    item.setData(scheme_cards._TAG_ROLE, "我的")
                    item.setData(scheme_cards._USED_ROLE, False)
                    plain = self.draw(view, delegate, MINE, state)
                    item.setData(scheme_cards._TAG_ROLE, "使用中")
                    item.setData(scheme_cards._USED_ROLE, True)
                    self.assertNotEqual(self.draw(view, delegate, MINE, state), plain)

    def test_paint_focus_only_with_focus(self):
        for skin, name in self.skins():
            theme.set_skin(skin.key)
            view = self.view()
            delegate = getattr(scheme_cards, name)(view)
            for label, state in STATES.items():
                with self.subTest(skin=skin.key, delegate=name, state=label):
                    bare = self.draw(view, delegate, MINE, state, method=None)
                    self.assertEqual(self.draw(view, delegate, MINE, state, method="paint_focus"), bare)
                    self.assertNotEqual(self.draw(view, delegate, MINE, state | S.State_HasFocus, method="paint_focus"),
                                        bare)


if __name__ == "__main__":
    unittest.main()
