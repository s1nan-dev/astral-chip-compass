"""「编辑筹码方案」窗口的左边（或上边）一栏：方案列表在七套皮肤下的七种画法。

七种排法各一种：默认的一行（_SchemeListDelegate）、游戏内风格的页签（_SchemeTabDelegate，side_tabs）、横排的卡牌
（_SchemeCardDelegate，card_row）、竖排的白卡（_SchemeTileDelegate，card_column）、霓虹贴纸（_SchemeStickerDelegate，
side_column）、白色海景房的白卡（_SchemeWellDelegate，tile_column）、一排页签（_SchemeStripDelegate，tab_strip）。
画法类的公共部分在 _SchemeRowDelegate：分组标题（_ID_ROLE 为 None）和一套方案的分别（sizeHint 和 paint 都按它分）、
画之前统一保存画笔状态并开抗锯齿、画完恢复；一套方案画完以后接着调 paint_focus 画焦点框（分组标题不调，默认不画）；
一套方案行尾的「默认」「我的」小胶囊由 _scheme_tag 画。子类写 paint_header（分组标题）、paint_row（一套方案）和
paint_focus（自己的焦点框，有的才写）。

悬浮窗对这个角色正在用的那一套（_USED_ROLE 为真）行尾写「使用中」，代替「默认」「我的」（文字同样放在 _TAG_ROLE 里），
换成这套皮肤表示「正常、已生效」的颜色（EditorLook.SAVED_COLOR，和「已保存」同色；画成小胶囊的几种画法用
ChromeLook.SCHEME_CARD 的 tag_used、tag_used_text，白色海景房另有 tag_used_fill）。游戏内风格的页签（side_tabs）平时
不写「默认」「我的」，只在正在用的那一页右上角压一枚写「使用中」的小胶囊。

列表里每一行的内容（名称、行尾的「默认」「我的」「使用中」、哪一行是分组标题、哪一套悬浮窗正在用）由 scheme_editor 放进
QListWidgetItem 的 Qt.DisplayRole、_ID_ROLE、_TAG_ROLE、_USED_ROLE 四个角色；颜色按当前皮肤取（theme.EDITOR、
ChromeLook.SCHEME_CARD）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme
from .theme import COMMON as C
from .theme import EDITOR as L

_ID_ROLE = Qt.UserRole  # 方案列表每一行的方案 id（默认推荐为空字符串，分组标题为 None）
_TAG_ROLE = Qt.UserRole + 1  # 方案列表每一行行尾的「默认」「我的」（悬浮窗正在用的那一套写「使用中」）
_USED_ROLE = Qt.UserRole + 2  # 悬浮窗对这个角色正在用的那一套为 True（行尾的字换成表示已生效的颜色）


def _used_ink(look) -> str:
    """「使用中」小胶囊的底色（ChromeLook.SCHEME_CARD 的 tag_used；没写时用「已保存」的颜色 EditorLook.SAVED_COLOR）。"""
    return look.get("tag_used") or L.SAVED_COLOR


def _scheme_tag(painter: QPainter, rect: QRectF, tag: str, mine: bool, font: QFont, border=None,
                used: bool = False) -> None:
    """方案卡片上的「默认」「我的」小胶囊（ChromeLook.SCHEME_CARD 的 tag_*）；悬浮窗正在用的那一套（used）写「使用中」，
    底色 tag_used、字 tag_used_text（没写时同 tag_text）。"""
    from . import decor
    from .theme import CHROME as K

    look = K.SCHEME_CARD
    path = decor.capsule(rect)
    fill = _used_ink(look) if used else (look["tag_mine"] if mine else look["tag_default"])
    painter.fillPath(path, decor.qc(fill))
    if border is not None:
        painter.strokePath(path, QPen(decor.qc(border), 1.6))
    ink = (look.get("tag_used_text") or look.get("tag_text") or L.TEXT_COLOR) if used else look["tag_text"]
    decor.outlined_text(painter, rect, tag, font, ink)


class _SchemeRowDelegate(QStyledItemDelegate):
    """方案列表各画法的公共部分：分组标题和一套方案的分别（按 _ID_ROLE 是不是 None）、画之前统一保存画笔状态并开抗锯齿、
    画完恢复；子类写 paint_header（分组标题）、paint_row（一套方案）和 paint_focus（一套方案那一行的焦点框）。分组标题不画
    焦点框；画法没有焦点框的不重写 paint_focus（默认不画）。HEIGHT/ROW_H 是行高，HEADER_H 是分组标题的高；行高和宽度另有
    算法的子类自己写 sizeHint。"""

    HEADER_H = 0
    ROW_H = 0

    @staticmethod
    def is_header(index) -> bool:
        """这一项是不是分组标题（「── 我的方案 ──」之类）。"""
        return index.data(_ID_ROLE) is None

    @staticmethod
    def is_mine(index) -> bool:
        """这一行是不是「我的方案」：默认推荐那一行的方案 id 为空字符串，我的方案都有 id。"""
        return bool(index.data(_ID_ROLE))

    @staticmethod
    def is_used(index) -> bool:
        """这一行是不是悬浮窗对这个角色正在用的那一套（行尾写「使用中」）。"""
        return bool(index.data(_USED_ROLE))

    def sizeHint(self, option, index) -> QSize:  # noqa: N802  Qt 的方法名
        return QSize(option.rect.width(), self.HEADER_H if self.is_header(index) else self.ROW_H)

    def paint(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if self.is_header(index):
            self.paint_header(painter, option, index)
        else:
            self.paint_row(painter, option, index)
            self.paint_focus(painter, option, index)
        painter.restore()

    def paint_header(self, painter, option, index) -> None:
        raise NotImplementedError

    def paint_row(self, painter, option, index) -> None:
        raise NotImplementedError

    def paint_focus(self, painter, option, index) -> None:
        """键盘选中的一套方案那一行的焦点框；只在一套方案的行上画（分组标题不画）。默认不画，有焦点框的画法重写。"""


class _SchemeListDelegate(_SchemeRowDelegate):
    """方案列表的一行（默认排法）：名称在左（放不下时省略），「默认」「我的」暗色写在右边（悬浮窗正在用的那一套写「使用中」，
    「已保存」的颜色）。名称按纯文本画。"""

    PAD, GAP = 6, 10

    def sizeHint(self, option, index) -> QSize:  # noqa: N802  Qt 的方法名
        # 照 QStyledItemDelegate 的算法（行高由字体和样式定），不走基类的固定行高
        return QStyledItemDelegate.sizeHint(self, option, index)

    def paint(self, painter, option, index) -> None:  # noqa: N802  Qt 的方法名
        tag = index.data(_TAG_ROLE)
        if not tag:
            QStyledItemDelegate.paint(self, painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        fm = QFontMetrics(opt.font)
        rect = opt.rect.adjusted(self.PAD + 6, 0, -(self.PAD + 6), 0)
        tag_w = fm.horizontalAdvance(tag)
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(QColor(L.LIST_NAME_COLOR))
        name_rect = rect.adjusted(0, 0, -(tag_w + self.GAP), 0)
        painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         fm.elidedText(text, Qt.ElideRight, max(name_rect.width(), 0)))
        painter.setPen(QColor(L.SAVED_COLOR if self.is_used(index) else L.LIST_TAG_COLOR))
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, tag)
        painter.restore()


class _SchemeTabDelegate(_SchemeRowDelegate):
    """游戏内风格（side_tabs）的方案列表：每一行画成左侧导航栏的页签（选中的变橙黄色、往右突出），分组标题「── 我的方案 ──」
    写成居中的暗色小字，字后面垫一块导航栏的底色（导航栏的竖条从字后面过去，不压在字下面）。名称放不下时末尾省略；名称是
    用户写的文字，按纯文本画（标题字体里没有的字退回正文字体）。页签上不写「默认」「我的」；悬浮窗正在用的那一页右上角压一枚
    写「使用中」的小胶囊（高 USED_H，在页签上沿和名称之间，不挡名称）。"""

    HEADER_H = 34
    HEADER_PAD = (6, 3)  # 分组标题的底色比字宽出、高出多少（左右、上下）
    USED_H, USED_PX = 15, 10  # 「使用中」小胶囊的高、字号（标题字体）

    def sizeHint(self, option, index) -> QSize:  # noqa: N802  Qt 的方法名
        from .theme import CHROME as K

        height = self.HEADER_H if self.is_header(index) else K.TAB_HEIGHT + 3
        return QSize(option.rect.width(), height)

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        font = QFont(option.font)
        font.setPointSizeF(C.FONT_S)
        font.setBold(True)
        painter.setFont(font)
        pad_x, pad_y = self.HEADER_PAD
        back = painter.boundingRect(rect, Qt.AlignCenter, text).adjusted(-pad_x, -pad_y, pad_x, pad_y)
        painter.fillRect(back, decor.qc(K.RAIL_FILL))
        painter.setPen(QColor(L.LIST_HEADER_COLOR))
        painter.drawText(rect, Qt.AlignCenter, text)

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        x = rect.left() + (K.TAB_SHIFT if on else 14)
        width = min(K.TAB_SIZE[1] if on else K.TAB_SIZE[0], rect.right() - x)
        font = theme.title_font(18)
        room = width - K.TAB_HEIGHT * 0.95 - 10
        shown = QFontMetricsF(font).elidedText(text, Qt.ElideRight, max(room, 10))
        decor.tab(painter, QRectF(x, rect.top(), width, rect.height()), shown, font, K, on, hover and not on)
        if self.is_used(index):
            small = theme.title_font(self.USED_PX)
            tag = str(index.data(_TAG_ROLE) or "")
            w = QFontMetricsF(small).horizontalAdvance(tag) + 12
            _scheme_tag(painter, QRectF(x + width - w - 8, rect.top() + 1, w, self.USED_H), tag, True, small, used=True)

    def paint_focus(self, painter, option, index) -> None:
        from .theme import CHROME as K

        if not option.state & QStyle.State_HasFocus:
            return
        rect = QRectF(option.rect)
        on = bool(option.state & QStyle.State_Selected)
        x = rect.left() + (K.TAB_SHIFT if on else 14)
        width = min(K.TAB_SIZE[1] if on else K.TAB_SIZE[0], rect.right() - x)
        painter.setPen(QPen(QColor(K.TAB_OUTLINE), 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(x, rect.top(), width, rect.height()).adjusted(1, 1, -1, -4), 10, 10)


class _SchemeCardDelegate(_SchemeRowDelegate):
    """方案列表横排成一行卡牌（card_row，经典主题-黑，同游戏「卡牌商店」里的卡牌）：深色卡底，上面一条名牌写方案名（选中的
    换成粉色、白字黑描边，整张卡牌黄框、往上抬一点），下面一枚小胶囊写「默认」「我的」；分组标题「── 我的方案 ──」写成暗色
    小字。颜色见 ChromeLook.SCHEME_CARD。名称放不下时末尾省略，按纯文本画（标题字体里没有的字退回正文字体）。"""

    CARD = (128, 70)
    HEIGHT = 86

    def sizeHint(self, option, index) -> QSize:  # noqa: N802  Qt 的方法名
        if self.is_header(index):
            font = QFont(option.font)
            font.setPointSizeF(C.FONT_S)
            font.setBold(True)
            return QSize(QFontMetrics(font).horizontalAdvance(str(index.data(Qt.DisplayRole) or "")) + 18, self.HEIGHT)
        return QSize(self.CARD[0] + 6, self.HEIGHT)

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        font = QFont(option.font)
        font.setPointSizeF(C.FONT_S)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(decor.qc(look["header"]))
        painter.drawText(rect.adjusted(0, 8, 0, 0), Qt.AlignCenter, text)

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        w, h = self.CARD
        card = QRectF(rect.left() + 3, rect.top() + (4 if on else 10), w, h)
        path = decor.rounded(card, 10)
        decor.hard_shadow(painter, path, 0, 4, (0, 0, 0, 200))
        painter.fillPath(path, decor.qc(look["body"]))
        edge = look["on_edge"] if on else look["edge"]
        if edge:
            painter.strokePath(path, QPen(decor.qc(edge), 3.5 if on else (2.0 if hover else 1.4)))
        strip = QRectF(card.left() + 8, card.top() + 8, w - 16, 30)
        painter.fillPath(decor.rounded(strip, 7), decor.qc(look["name_on"] if on else look["name"]))
        font = theme.title_font(14)
        shown = QFontMetricsF(font).elidedText(text, Qt.ElideRight, strip.width() - 10)
        decor.outlined_text(painter, strip, shown, font, look["name_on_text"] if on else look["name_text"],
                            K.TITLE_OUTLINE if on else None, 3.5 if on else 0.0)
        tag = str(index.data(_TAG_ROLE) or "")
        if tag:
            small = theme.title_font(11)
            tw = QFontMetricsF(small).horizontalAdvance(tag) + 18
            _scheme_tag(painter, QRectF(card.center().x() - tw / 2, card.top() + 45, tw, 18), tag, self.is_mine(index), small,
                        used=self.is_used(index))

    def paint_focus(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        if not option.state & QStyle.State_HasFocus:
            return
        rect = QRectF(option.rect)
        w, h = self.CARD
        card = QRectF(rect.left() + 3, rect.top() + (4 if option.state & QStyle.State_Selected else 10), w, h)
        painter.setPen(QPen(decor.qc(K.SCHEME_CARD["on_edge"]), 1.5, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(card.adjusted(-3, -3, 3, 3), 12, 12)


class _SchemeTileDelegate(_SchemeRowDelegate):
    """方案列表竖排成一张张白卡片（card_column，经典主题-反色）：方案名写在左边，「默认」「我的」小胶囊在右边；选中的黑色粗框、
    左边一根亮绿竖条；分组标题「── 我的方案 ──」白字黑描边、居中。颜色见 ChromeLook.SCHEME_CARD。名称放不下时末尾省略，
    按纯文本画。"""

    HEIGHT = 60
    HEADER_H = 34
    ROW_H = HEIGHT

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        font = theme.title_font(13)
        decor.outlined_text(painter, rect, text, font, look["header"], look["header_outline"] or None, 3.0)

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        card = QRectF(rect.left() + 1, rect.top() + 2, rect.width() - 4, rect.height() - 10)
        path = decor.rounded(card, 12)
        decor.hard_shadow(painter, path, 0, 4, K.CARD_SHADOW)
        painter.fillPath(path, decor.qc(look["body"]))
        if on and look["accent"]:
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(QRectF(card.left(), card.top(), 10, card.height()), decor.qc(look["accent"]))
            painter.restore()
        if on or hover:
            painter.strokePath(path, QPen(decor.qc(look["on_edge"]), 3 if on else 1.4))
        font = theme.title_font(14)
        small = theme.title_font(11)
        tag = str(index.data(_TAG_ROLE) or "")
        tw = QFontMetricsF(small).horizontalAdvance(tag) + 22 if tag else 0
        name = QRectF(card.left() + 20, card.top(), card.width() - 34 - tw, card.height())
        shown = QFontMetricsF(font).elidedText(text, Qt.ElideRight, max(name.width(), 10))
        decor.outlined_text(painter, name, shown, font, look["name_on_text"] if on else look["name_text"],
                            align=Qt.AlignLeft)
        if tag:
            _scheme_tag(painter, QRectF(card.right() - 12 - tw, card.center().y() - 11, tw, 22), tag, self.is_mine(index),
                        small, border=look["on_edge"], used=self.is_used(index))

    def paint_focus(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        if not option.state & QStyle.State_HasFocus:
            return
        rect = QRectF(option.rect)
        card = QRectF(rect.left() + 1, rect.top() + 2, rect.width() - 4, rect.height() - 10)
        painter.setPen(QPen(decor.qc(K.SCHEME_CARD["on_edge"]), 1.5, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(card.adjusted(4, 4, -4, -4), 9, 9)


class _SchemeStickerDelegate(_SchemeRowDelegate):
    """霓虹贴纸（side_column）的方案列表（放在一张贴纸卡片里）：每一行左边名称、右边「默认」「我的」；选中的一行是整块星光黄、
    粗黑描边、右下实心投影，字换成深色；鼠标移上去的一行铺一层淡色。分组标题「── 我的方案 ──」写成居中的暗色小字。颜色见
    ChromeLook.SCHEME_CARD（on 选中的底、on_text 选中的字、text 名称、tag / tag_on 行尾、header 分组标题）。名称放不下时末尾
    省略，按纯文本画。"""

    ROW_H = 38
    HEADER_H = 28

    def paint(self, painter, option, index) -> None:
        from .theme import CHROME as K

        if K.STYLE != "sticker":  # 刚换了皮肤、窗口还没按新皮肤重建的那一下：照普通的一行画
            QStyledItemDelegate.paint(self, painter, option, index)
            return
        super().paint(painter, option, index)

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        small = QFont(option.font)
        small.setPointSizeF(C.FONT_S)
        painter.setFont(small)
        painter.setPen(decor.qc(look.get("header") or L.LIST_HEADER_COLOR))
        painter.drawText(rect, Qt.AlignCenter, text)

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        small = QFont(option.font)
        small.setPointSizeF(C.FONT_S)
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        box = rect.adjusted(6, 3, -9, -6)
        path = decor.rounded(box, 10)
        if on:
            decor.sticker(painter, path, look.get("on") or K.TAG_NOTCH or K.SURFACE, K, 2.0, (3, 3), box)
        elif hover:
            painter.fillPath(path, QColor(255, 255, 255, 18))
        bold = QFont(option.font)
        bold.setBold(True)
        tag = str(index.data(_TAG_ROLE) or "")
        tag_w = QFontMetricsF(small).horizontalAdvance(tag) if tag else 0
        name = box.adjusted(12, 0, -(tag_w + 22), 0)
        painter.setFont(bold)
        ink = (look.get("on_text") or K.ACCENT_TEXT) if on else (look.get("text") or L.LIST_NAME_COLOR)
        painter.setPen(decor.qc(ink))
        painter.drawText(name, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetricsF(bold).elidedText(text, Qt.ElideRight, max(name.width(), 10)))
        if tag:
            painter.setFont(small)
            if self.is_used(index):  # 「使用中」：tag_used（选中的那一行是星光黄底，用 tag_used_on）
                ink = (look.get("tag_used_on") or look.get("tag_on") or K.ACCENT_TEXT) if on else _used_ink(look)
            else:
                ink = (look.get("tag_on") or K.ACCENT_TEXT) if on else (look.get("tag") or L.LIST_TAG_COLOR)
            painter.setPen(decor.qc(ink))
            painter.drawText(box.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, tag)

    def paint_focus(self, painter, option, index) -> None:
        from . import decor

        if not option.state & QStyle.State_HasFocus:
            return
        box = QRectF(option.rect).adjusted(6, 3, -9, -6)
        painter.setPen(QPen(decor.qc(L.ACCENT_FILL), 1.4, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 11, 11)


class _SchemeWellDelegate(_SchemeRowDelegate):
    """白色海景房（tile_column）的方案列表：一张张白色圆角卡片，前面一颗小灯点、名称，右边「默认」「我的」小胶囊；选中的卡片
    紫粉渐变框线、下面一圈软阴影，灯点和小胶囊换成强调色。分组标题「── 我的方案 ──」写成居中的暗色小字。颜色见
    ChromeLook.SCHEME_CARD（body 卡底、on 选中的卡底、edge 框线、text 名称、dot / dot_on 灯点、tag / tag_fill、tag_on /
    tag_on_fill 小胶囊、header 分组标题）。名称放不下时末尾省略，按纯文本画。"""

    ROW_H = 48
    HEADER_H = 28

    def paint(self, painter, option, index) -> None:
        from .theme import CHROME as K

        if K.STYLE != "soft":  # 刚换了皮肤、窗口还没按新皮肤重建的那一下：照普通的一行画
            QStyledItemDelegate.paint(self, painter, option, index)
            return
        super().paint(painter, option, index)

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        small = QFont(option.font)
        small.setPointSizeF(C.FONT_S)
        painter.setFont(small)
        painter.setPen(decor.qc(look.get("header") or L.LIST_HEADER_COLOR))
        painter.drawText(rect, Qt.AlignCenter, text)

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        look = K.SCHEME_CARD
        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        small = QFont(option.font)
        small.setPointSizeF(C.FONT_S)
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        card = rect.adjusted(2, 2, -3, -7)
        path = decor.rounded(card, 12)
        if on:
            decor.soft_shadow(painter, path, 0, 3, 5, K.SOFT_SHADOW)
            painter.fillPath(path, decor.qc(look.get("on") or "#ffffff"))
            painter.strokePath(path, QPen(decor.fill_brush(K.ACCENT or K.CARD_EDGE, card), 1.8))
        else:
            painter.fillPath(path, decor.qc(look.get("body") or "#ffffff"))
            painter.strokePath(path, QPen(decor.qc(K.TAG_TEXT if hover else (look.get("edge") or K.CARD_EDGE)),
                                          1.2 if hover else 1))
        accent = look.get("dot_on") or L.ACCENT_LINE
        painter.setPen(Qt.NoPen)
        painter.setBrush(decor.qc(accent if on else (look.get("dot") or L.LIST_TAG_COLOR)))
        painter.drawEllipse(QPointF(card.left() + 16, card.center().y()), 4, 4)
        tag = str(index.data(_TAG_ROLE) or "")
        tag_w = QFontMetricsF(small).horizontalAdvance(tag) + 14 if tag else 0
        bold = QFont(option.font)
        bold.setBold(True)
        name = QRectF(card.left() + 28, card.top(), card.width() - 28 - tag_w - 16, card.height())
        painter.setFont(bold)
        painter.setPen(decor.qc(look.get("text") or L.LIST_NAME_COLOR))
        painter.drawText(name, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetricsF(bold).elidedText(text, Qt.ElideRight, max(name.width(), 10)))
        if tag:
            pill = QRectF(card.right() - 10 - tag_w, card.center().y() - 9, tag_w, 18)
            if self.is_used(index):  # 「使用中」：选中与否都是 tag_used_fill 的底、tag_used 的字
                fill, ink = look.get("tag_used_fill"), _used_ink(look)
            else:
                fill = look.get("tag_on_fill") if on else look.get("tag_fill")
                ink = (look.get("tag_on") or accent) if on else (look.get("tag") or L.LIST_TAG_COLOR)
            painter.fillPath(decor.capsule(pill), decor.qc(fill or K.SURFACE))
            painter.setFont(small)
            painter.setPen(decor.qc(ink))
            painter.drawText(pill, Qt.AlignCenter, tag)

    def paint_focus(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        if not option.state & QStyle.State_HasFocus:
            return
        card = QRectF(option.rect).adjusted(2, 2, -3, -7)
        accent = K.SCHEME_CARD.get("dot_on") or L.ACCENT_LINE
        painter.setPen(QPen(decor.qc(accent), 1.4, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(card.adjusted(3, 3, -3, -3), 10, 10)


class _SchemeStripDelegate(_SchemeRowDelegate):
    """碳纤流光（tab_strip）的方案列表：横排成一排页签。每一页写方案名（粗体），后面暗色小字「默认」「我的」；选中的那一页
    铺一层很淡的亮色（ChromeLook.HALO）、名称换成亮色，底下一条亮色线；鼠标移上去的一页铺一层更淡的亮色。分组标题
    「── 我的方案 ──」写成前面一条竖线的暗色小字（不写两头的横线）。名称放不下时末尾省略，按纯文本画。"""

    HEIGHT = 40
    PAD = 14  # 页签左右留白
    GAP = 6  # 名称和「默认」「我的」之间
    NAME_MAX = 160  # 名称最多占这么宽（再长的末尾省略）

    @staticmethod
    def _fonts(option) -> tuple[QFont, QFont]:
        bold = QFont(option.font)
        bold.setBold(True)
        small = QFont(option.font)
        small.setPointSizeF(C.FONT_S)
        return bold, small

    def sizeHint(self, option, index) -> QSize:  # noqa: N802  Qt 的方法名
        bold, small = self._fonts(option)
        text = str(index.data(Qt.DisplayRole) or "")
        if self.is_header(index):
            return QSize(round(QFontMetricsF(small).horizontalAdvance(text.strip("─ ")) + 26), self.HEIGHT)
        tag = str(index.data(_TAG_ROLE) or "")
        name_w = min(QFontMetricsF(bold).horizontalAdvance(text), self.NAME_MAX)
        tag_w = QFontMetricsF(small).horizontalAdvance(tag) + self.GAP if tag else 0
        return QSize(round(2 * self.PAD + name_w + tag_w + 2), self.HEIGHT)

    def paint_header(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        bold, small = self._fonts(option)
        painter.setPen(QPen(decor.qc(K.CARD_EDGE), 1))
        x = rect.left() + 4.5
        painter.drawLine(QPointF(x, rect.top() + 12), QPointF(x, rect.bottom() - 12))
        painter.setFont(small)
        painter.setPen(QColor(L.LIST_HEADER_COLOR))
        painter.drawText(rect.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text.strip("─ "))

    def paint_row(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        rect = QRectF(option.rect)
        text = str(index.data(Qt.DisplayRole) or "")
        bold, small = self._fonts(option)
        accent = decor.qc(K.HALO or L.ACCENT_COLOR)
        on = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        tab = rect.adjusted(1, 2, -1, -2)
        if on or hover:
            fill = QColor(accent)
            fill.setAlpha(26 if on else 12)
            painter.fillRect(tab, fill)
        if on:
            painter.fillRect(QRectF(tab.left(), tab.bottom() - 2.5, tab.width(), 2.5), accent)
        tag = str(index.data(_TAG_ROLE) or "")
        tag_w = QFontMetricsF(small).horizontalAdvance(tag) if tag else 0.0
        room = tab.width() - 2 * self.PAD - (tag_w + self.GAP if tag else 0)
        shown = QFontMetricsF(bold).elidedText(text, Qt.ElideRight, max(room, 10))
        name_w = QFontMetricsF(bold).horizontalAdvance(shown)
        x = tab.left() + self.PAD
        painter.setFont(bold)
        painter.setPen(accent if on else QColor(L.LIST_NAME_COLOR))
        painter.drawText(QRectF(x, tab.top(), name_w + 2, tab.height()), Qt.AlignVCenter | Qt.AlignLeft, shown)
        if tag:
            painter.setFont(small)
            painter.setPen(QColor(L.SAVED_COLOR if self.is_used(index) else L.LIST_TAG_COLOR))
            painter.drawText(QRectF(x + name_w + self.GAP, tab.top(), tag_w + 2, tab.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, tag)

    def paint_focus(self, painter, option, index) -> None:
        from . import decor
        from .theme import CHROME as K

        if not option.state & QStyle.State_HasFocus:
            return
        tab = QRectF(option.rect).adjusted(1, 2, -1, -2)
        accent = decor.qc(K.HALO or L.ACCENT_COLOR)
        painter.setPen(QPen(accent, 1.2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(tab.adjusted(2, 2, -2, -5))
