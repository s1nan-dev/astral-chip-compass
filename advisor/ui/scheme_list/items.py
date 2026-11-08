"""筹码列表里画在画布上的条目：一行（拖动把手、图标、筹码名、流派、四个档位按钮、行尾）、块标题、组标题、
放进列表的控件外面那一层、上沿和下沿的一行提示。各项的位置由列表算好（ty、th、vy），自己只按位置摆放、
自己画；鼠标（悬停、点档位、按把手拖、收起展开）、悬停说明和拖动时的样子都在这里。改方案、存盘由窗口做。"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QApplication, QToolTip, QVBoxLayout, QWidget

from ...data import TIER_LABEL, TIERS
from .. import lang, names
from ..widgets import chip_icon, scaled_pixmap
from .specs import (
    FRAME_MS,
    TOP,
    RowSpec,
    _color,
    _focus_ring,
    _inline_margins,
    _item_base,
    _Metrics,
    _Motion,
    _round_rect,
    _tier_on,
    _tip_html,
    _triangle,
)
from ..theme import COMMON as C
from ..theme import LIST as L

# ------------------------------------------------------------ 提示显示多久

HINT_MS = 2000  # 列表上沿、下沿的提示显示多久
HINT_FADE_MS = 300  # 提示消失前淡出（不做动画时直接消失）


class _Item(QWidget):
    """画布上自己摆放的一项。ty、th 是排好的位置和高度（动画的终点），vy 是画面上的位置（动画中间值），opacity 是透明度；
    样子由子类画。ghost 为 True 的是动画用的影子（原处收起、淡出的那一份），不接鼠标，动画完就删掉。"""

    def __init__(self, owner: SchemeList, ghost: bool = False):
        super().__init__(owner.canvas)
        self.owner = owner
        self.ghost = ghost
        self.ty = 0.0
        self.th = 0
        self.vy = 0.0
        self.opacity = 1.0
        self.motion: _Motion | None = None
        self.flash = 0.0
        self.flash_t0 = 0.0
        self.setFocusPolicy(Qt.NoFocus)
        if ghost:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def apply(self, y: float, opacity: float = 1.0) -> None:
        """摆到 y（画布坐标），透明度 opacity，并显示出来。"""
        self.vy = float(y)
        if opacity != self.opacity:
            self.opacity = opacity
            self.update()
        geo = QRect(self.owner._item_x(), round(self.vy), self.owner._item_w(), self.th)
        if geo != self.geometry():
            self.setGeometry(geo)
        if not self.isVisibleTo(self.owner.canvas):
            self.show()

    def set_flash(self, level: float) -> None:
        if level != self.flash:
            self.flash = level
            self.update()

    def paint_base(self, p: QPainter) -> None:
        """列表在动（动画、拖动）时、影子：先铺不透明的底色，盖住下面经过的行。"""
        if self.ghost or self.owner._in_motion():
            p.fillRect(self.rect(), _item_base())

    def recipe(self) -> Callable[[], _Item] | None:
        """做一个和现在一样的影子（原处收起、淡出时用）；不需要影子的返回 None。"""
        return None


class _RowItem(_Item):
    """一行：拖动把手（可拖的块里才有）、图标、筹码名（稀有度颜色）、流派、四个档位按钮、行尾。"""

    PART_HANDLE, PART_NAME, PART_END, PART_ROW = "handle", "name", "end", "row"

    def __init__(self, owner: SchemeList, chip: str, ghost: bool = False):
        super().__init__(owner, ghost)
        self.chip = chip
        self.data = owner.db.chips.get(chip)
        self.shown_name = names.chip_in(owner.db, chip)  # 画在行首的名字（繁体界面为繁体名，chip 仍作程序用的键）
        self.spec = RowSpec(chip)
        self.block = ""
        self.group: str | None = None
        self.draggable = False
        self.dragging = False
        self.hover: object = None  # 鼠标所在的部分：PART_* 或 ("button", 序号)
        self.pressed: int | None = None  # 按下的档位按钮序号
        self.press_at: QPointF | None = None  # 按下把手、图标或筹码名时的全局位置（还没开始拖）
        self.refresh_icon()
        if not ghost:
            self.setMouseTracking(True)

    def refresh_icon(self) -> None:
        """按当前皮肤的图标大小（和缺图时的画法）做这一行的筹码图标。"""
        dpr = max(1.0, self.devicePixelRatioF())
        rarity = self.data.rarity if self.data is not None else ""
        icon = self.data.icon if self.data is not None else ""
        self.pixmap = scaled_pixmap(chip_icon(self.chip, rarity, icon, round(L.ICON * dpr)), dpr)

    def set_spec(self, spec: RowSpec, block: str, group: str | None, draggable: bool) -> None:
        if (spec, block, group, draggable) == (self.spec, self.block, self.group, self.draggable):
            return
        self.spec, self.block, self.group, self.draggable = spec, block, group, draggable
        self.update()

    def reset_pointer(self) -> None:
        """这一行挪走了：鼠标所在、按下的状态清掉（鼠标再移动时重新算）。"""
        self.hover = None
        self.pressed = None
        self.press_at = None
        self.unsetCursor()
        self.update()

    def recipe(self):
        spec, block, group, draggable = self.spec, self.block, self.group, self.draggable
        owner, chip = self.owner, self.chip

        def make():
            ghost = _RowItem(owner, chip, ghost=True)
            ghost.set_spec(spec, block, group, draggable)
            return ghost

        return make

    # ---------------------------------------------------------------- 画

    def _clickable(self) -> bool:
        """点按钮会不会发 tier_clicked：enabled 的行（被锁的行画成不能点的样子，照样发，由窗口去问）。"""
        return not self.ghost and self.spec.enabled

    def _button_look(self, i: int) -> str:
        tier = TIERS[i]
        live = self.spec.enabled and not self.spec.locked  # 影子照原来的样子画（只是不接鼠标）
        if self.spec.tier == tier:
            look = "avoid_on" if tier == "avoid" else "on"
            return look if live else look + "_off"
        if not live:
            return "off"
        if self.hover == ("button", i):
            return "pressed" if self.pressed == i else "hover"
        return "normal"

    def paintEvent(self, event) -> None:
        m = self.owner._m()
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        self.paint_base(p)
        full = QRectF(0.5, 0.5, w - 1, h - 1)
        if L.ROW_FILL is not None:  # 皮肤给每一行先铺一条圆角底（行和行之间留一点空）
            _round_rect(p, QRectF(0, 1.5, w, h - 3), L.ROW_FILL, None, L.ROW_FILL_RADIUS)
        if self.dragging:
            _round_rect(p, full, L.DRAG_FILL, L.DRAG_BORDER, L.ROW_RADIUS)
        if self.flash > 0:
            fill = _color(L.FLASH_FILL)
            fill.setAlphaF(fill.alphaF() * min(1.0, self.flash))
            _round_rect(p, full, fill, None, L.ROW_RADIUS)
        if self.draggable:
            lit = self.dragging or self.hover in (self.PART_HANDLE, self.PART_NAME)
            rect = m.handle_rect(h)
            _round_rect(p, rect, L.HANDLE_FILL_HOVER if lit else L.HANDLE_FILL, None, L.HANDLE_RADIUS)
            p.setPen(Qt.NoPen)
            p.setBrush(_color(L.HANDLE_DOTS_HOVER if lit else L.HANDLE_DOTS))
            cx, cy = rect.center().x(), rect.center().y()
            for dx in (-2.0, 2.0):
                for dy in (-4.0, 0.0, 4.0):
                    p.drawEllipse(QPointF(cx + dx, cy + dy), L.HANDLE_DOT, L.HANDLE_DOT)
        p.drawPixmap(m.icon_rect(h).topLeft(), self.pixmap)
        rarity = self.data.rarity if self.data is not None else ""
        p.setFont(m.font_row)
        p.setPen(_color(C.RARITY_COLOR.get(rarity, L.NAME_COLOR)))
        name = m.name_rect(h)
        p.drawText(name, Qt.AlignVCenter | Qt.AlignLeft,
                   m.fm_row.elidedText(self.shown_name, Qt.ElideRight, int(name.width())))
        p.setFont(m.font_small)
        if self.spec.school:
            p.setPen(_color(L.SCHOOL_COLOR))
            school = m.school_rect(h)
            shown = m.fm_small.elidedText(lang.t(self.spec.school), Qt.ElideRight,
                                          int(school.width()) - (10 if L.SCHOOL_FILL else 0))
            if L.SCHOOL_FILL is not None:  # 皮肤把流派写在小胶囊里（白色海景房）
                pill = QRectF(school.left(), school.center().y() - 9, m.fm_small.horizontalAdvance(shown) + 12, 18)
                _round_rect(p, pill, L.SCHOOL_FILL, None, 9)
                p.setPen(_color(L.SCHOOL_COLOR))
                p.drawText(pill, Qt.AlignCenter, shown)
            else:
                p.drawText(school, Qt.AlignVCenter | Qt.AlignLeft, shown)
        if L.TIER_SEGMENTED:  # 皮肤把四个按钮连成一整条分段胶囊
            self._paint_segments(p, m, h)
        else:
            for i, tier in enumerate(TIERS):
                look = self._button_look(i)
                text, fill, border = L.BUTTON_LOOK[look]
                if self.spec.tier == tier and tier in L.TIER_ON:  # 皮肤让亮着的按钮按档位各用各的颜色
                    text, fill, border = _tier_on(tier, faded=look.endswith("_off"))
                rect = m.button_rect(i, h).adjusted(0.5, 0.5, -0.5, -0.5)
                _round_rect(p, rect, fill, border, L.BUTTON_RADIUS)
                p.setPen(_color(text))
                p.drawText(rect, Qt.AlignCenter, lang.t(TIER_LABEL[tier]))
                if not self.ghost and self.owner._ring_on(("button", self.chip, i)):
                    _focus_ring(p, m.button_rect(i, h), L.BUTTON_RADIUS, QRectF(self.rect()))
        end = m.end_rect(w, h)
        x, right = end.x(), end.right()
        for text, color in self.spec.end_parts():
            room = int(right - x)
            if room <= 0:
                break
            shown = m.fm_small.elidedText(text, Qt.ElideRight, room)
            p.setPen(_color(color or L.END_COLOR))
            p.drawText(QRectF(x, 0, room, h), Qt.AlignVCenter | Qt.AlignLeft, shown)
            x += m.fm_small.horizontalAdvance(shown) + L.END_PART_GAP
        p.end()

    def _paint_segments(self, p: QPainter, m: _Metrics, h: int) -> None:
        """四个档位按钮连成一整条分段胶囊（ListLook.TIER_SEGMENTED）：外面一圈框线（SEGMENT_BORDER），段与段之间一条竖线；
        每一段的底色、字照 BUTTON_LOOK / TIER_ON（同分开画的按钮），焦点框套在那一段外面。按钮的位置、点哪里算哪一档不变。"""
        from PySide6.QtGui import QPainterPath

        first, last = m.button_rect(0, h), m.button_rect(len(TIERS) - 1, h)
        outer = QRectF(first.left(), first.top(), last.right() - first.left(), first.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(L.BUTTON_RADIUS, outer.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(outer, radius, radius)
        if L.SEGMENT_STYLE == "slider":
            self._paint_slider(p, m, h, outer, path)
            return
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.NoPen)
        for i, tier in enumerate(TIERS):
            look = self._button_look(i)
            text, fill, _ = L.BUTTON_LOOK[look]
            if self.spec.tier == tier and tier in L.TIER_ON:
                text, fill, _ = _tier_on(tier, faded=look.endswith("_off"))
            seg = m.button_rect(i, h)
            p.fillRect(seg, _color(fill))
            p.setPen(_color(text))
            p.drawText(seg, Qt.AlignCenter, lang.t(TIER_LABEL[tier]))
            p.setPen(Qt.NoPen)
        p.restore()
        border = _color(L.SEGMENT_BORDER if L.SEGMENT_BORDER is not None else L.BUTTON_LOOK["normal"][2])
        p.setPen(QPen(border, 1.2))
        for i in range(1, len(TIERS)):
            x = m.button_rect(i, h).left()
            p.drawLine(QPointF(x, outer.top()), QPointF(x, outer.bottom()))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(border, 1.6))
        p.drawPath(path)
        for i in range(len(TIERS)):
            if not self.ghost and self.owner._ring_on(("button", self.chip, i)):
                _focus_ring(p, m.button_rect(i, h), 4, QRectF(self.rect()))

    def _paint_slider(self, p: QPainter, m: _Metrics, h: int, outer: QRectF, path) -> None:
        """分段胶囊画成滑块的样子（ListLook.SEGMENT_STYLE 为 slider，白色海景房）：一整条浅灰底（BUTTON_LOOK 平时那一种的底），
        不画框线和竖线；亮着的那一段是缩进一圈、这一档颜色的胶囊（TIER_ON），其余几段只写字（鼠标移上去、按下的那一段照
        BUTTON_LOOK 换字的颜色、铺一层淡色）。"""
        p.fillPath(path, _color(L.BUTTON_LOOK["normal"][1]))
        for i, tier in enumerate(TIERS):
            look = self._button_look(i)
            text, fill, _ = L.BUTTON_LOOK[look]
            seg = m.button_rect(i, h)
            if self.spec.tier == tier and tier in L.TIER_ON:
                text, fill, _ = _tier_on(tier, faded=look.endswith("_off"))
                knob = seg.adjusted(2, 2, -2, -2)
                _round_rect(p, knob, fill, None, knob.height() / 2)
            elif look in ("hover", "pressed"):
                knob = seg.adjusted(2, 2, -2, -2)
                _round_rect(p, knob, fill, None, knob.height() / 2)
            p.setPen(_color(text))
            p.drawText(seg, Qt.AlignCenter, lang.t(TIER_LABEL[tier]))
        for i in range(len(TIERS)):
            if not self.ghost and self.owner._ring_on(("button", self.chip, i)):
                _focus_ring(p, m.button_rect(i, h), m.button_rect(i, h).height() / 2, QRectF(self.rect()))

    # ---------------------------------------------------------------- 鼠标

    def _part_at(self, pos: QPointF):
        m = self.owner._m()
        h = self.height()
        for i in range(len(TIERS)):
            if m.button_rect(i, h).contains(pos):
                return ("button", i)
        if self.draggable and m.handle_rect(h).adjusted(-2, -3, 2, 3).contains(pos):
            return self.PART_HANDLE
        if m.grab_rect(h).contains(pos) and pos.x() >= m.icon_x:
            return self.PART_NAME
        if m.end_rect(self.width(), h).contains(pos):
            return self.PART_END
        return self.PART_ROW

    def _cursor_for(self, part) -> Qt.CursorShape | None:
        if isinstance(part, tuple):
            return Qt.PointingHandCursor if self._clickable() else None
        if part in (self.PART_HANDLE, self.PART_NAME) and self.owner._can_drag(self):
            return Qt.OpenHandCursor
        return None

    def _set_hover(self, part) -> None:
        if part == self.hover:
            return
        self.hover = part
        shape = self._cursor_for(part)
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(shape)
        self.update()

    def mousePressEvent(self, event) -> None:
        if self.ghost or event.button() != Qt.LeftButton:
            event.ignore()
            return
        part = self._part_at(event.position())
        self._set_hover(part)
        if isinstance(part, tuple):
            if self._clickable():
                self.pressed = part[1]
                self.update()
                self.owner._mouse_cursor(("button", self.chip, part[1]))  # 之后按 Tab 从这个按钮接着走
            event.accept()
            return
        if part in (self.PART_HANDLE, self.PART_NAME) and self.owner._can_drag(self):
            self.press_at = QPointF(event.globalPosition())
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        drag = self.owner._drag
        if drag is not None and drag.row is self:
            if not event.buttons() & Qt.LeftButton:  # 松手事件没送到（切走、系统弹窗抢了捕获）：取消拖动
                self.owner._end_drag(cancel=True)
                return
            self.owner._drag_move(event.globalPosition())
            return
        if self.press_at is not None and event.buttons() & Qt.LeftButton:
            moved = (QPointF(event.globalPosition()) - self.press_at).manhattanLength()
            if moved >= QApplication.startDragDistance():
                start, self.press_at = self.press_at, None
                self.owner._drag_begin(self, start, event.globalPosition())
            return
        self._set_hover(self._part_at(event.position()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        drag = self.owner._drag
        if drag is not None and drag.row is self:
            self.owner._drag_end()
            return
        self.press_at = None
        if self.pressed is not None:
            i, self.pressed = self.pressed, None
            self.update()
            if self._part_at(event.position()) == ("button", i):
                self.owner._row_clicked(self, TIERS[i])

    def leaveEvent(self, event) -> None:
        if self.owner._drag is None or self.owner._drag.row is not self:
            self._set_hover(None)
        super().leaveEvent(event)

    def tip_at(self, pos: QPointF) -> tuple[str, QRect]:
        """鼠标在 pos 时的悬停说明和它管的那一块（移出这一块就收起，换成那里的说明）：筹码名和图标写效果原文
        （或 name_tip），行尾写 end_tip，其余地方写 tip。"""
        m = self.owner._m()
        w, h = self.width(), self.height()
        part = self._part_at(pos)
        spec = self.spec
        if part in (self.PART_HANDLE, self.PART_NAME):
            text = spec.name_tip if spec.name_tip is not None else getattr(self.data, "effect", "")
            rect = m.grab_rect(h)
        elif part == self.PART_END and spec.end_tip:
            text, rect = spec.end_tip, m.end_rect(w, h)
        else:
            right = m.end_x if spec.end_tip else w
            text, rect = spec.tip, QRectF(m.school_x, 0, max(0, right - m.school_x), h)
        rect = rect.toAlignedRect()
        return text, (rect if rect.contains(pos.toPoint()) else self.rect())

    def event(self, event) -> bool:
        if event.type() == QEvent.ToolTip:
            text, rect = self.tip_at(QPointF(event.pos()))
            if text and self.owner._drag is None:
                QToolTip.showText(event.globalPos(), _tip_html(lang.t(text)), self, rect)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)


class _HeaderItem(_Item):
    """块标题（kind 为 "block"：不是第一块时上方画一条分隔线）或组标题（kind 为 "group"：右边一个收起按钮）。"""

    def __init__(self, owner: SchemeList, kind: str, block: str, group: str | None = None, ghost: bool = False):
        super().__init__(owner, ghost)
        self.kind, self.block, self.group = kind, block, group
        self.title = ""
        self.first = True
        self.open = True
        self.fold_enabled = True
        self.hover_fold = False
        self.pressed = False
        if not ghost:
            self.setMouseTracking(True)

    def set_title(self, title: str) -> None:
        if title != self.title:
            self.title = title
            self.update()

    def set_state(self, first: bool | None = None, open_: bool | None = None, fold_enabled: bool | None = None):
        before = (self.first, self.open, self.fold_enabled)
        if first is not None:
            self.first = first
        if open_ is not None:
            self.open = open_
        if fold_enabled is not None:
            self.fold_enabled = fold_enabled
        if (self.first, self.open, self.fold_enabled) != before:
            self.update()

    def measure(self) -> int:
        m = self.owner._m()
        if self.kind == "block":
            return (0 if self.first else L.SEP_SPACE) + (m.block_h if self.title else 0)
        return m.group_h

    def recipe(self):
        owner, kind, block, group = self.owner, self.kind, self.block, self.group
        title, first, open_, fold_enabled = self.title, self.first, self.open, self.fold_enabled

        def make():
            ghost = _HeaderItem(owner, kind, block, group, ghost=True)
            ghost.title, ghost.first, ghost.open, ghost.fold_enabled = title, first, open_, fold_enabled
            return ghost

        return make

    def fold_rect(self) -> QRectF:
        m = self.owner._m()
        inner = max(L.FOLD_SIZE, m.title_h)
        return QRectF(self.width() - L.ROW_RIGHT - L.FOLD_SIZE, L.GROUP_PAD_TOP + (inner - L.FOLD_SIZE) / 2,
                      L.FOLD_SIZE, L.FOLD_SIZE)

    def paintEvent(self, event) -> None:
        m = self.owner._m()
        w = self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        self.paint_base(p)
        p.setFont(m.font_title)
        if self.kind == "block":
            top = 0
            if not self.first:
                p.setPen(QPen(_color(L.RULE_COLOR), 1))
                y = L.SEP_SPACE / 2 + 0.5
                p.drawLine(QPointF(2, y), QPointF(w - 2, y))
                top = L.SEP_SPACE
            if self.title:
                rect = QRectF(L.BLOCK_INDENT, top + L.BLOCK_PAD_TOP, max(0, w - L.BLOCK_INDENT - L.ROW_RIGHT), m.title_h)
                shown = m.fm_title.elidedText(self.title, Qt.ElideRight, int(rect.width()) - 20)
                tag = L.BLOCK_TAGS.get(self.block)
                if tag and L.BLOCK_TAG_SHAPE:  # 皮肤把块标题写在缎带、黑色斜角标签里
                    self._paint_block_tag(p, m, rect, shown, tag)
                elif tag:  # 皮肤把块标题写在这一档颜色的圆角标签里
                    box = QRectF(rect.left(), rect.top() - 2, m.fm_title.horizontalAdvance(shown) + 20,
                                 rect.height() + 4)
                    _round_rect(p, box, tag, None, min(9.0, box.height() / 2))
                    p.setPen(_color(L.BLOCK_TAG_TEXT or L.TITLE_COLOR))
                    p.drawText(box, Qt.AlignCenter, shown)
                else:
                    p.setPen(_color(L.TITLE_COLOR))
                    p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                               m.fm_title.elidedText(self.title, Qt.ElideRight, int(rect.width())))
        else:
            fold = self.fold_rect()
            inner = max(L.FOLD_SIZE, m.title_h)
            rect = QRectF(L.GROUP_INDENT, L.GROUP_PAD_TOP, max(0, fold.x() - 6 - L.GROUP_INDENT), inner)
            p.setPen(_color(L.TITLE_COLOR))
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                       m.fm_title.elidedText(self.title, Qt.ElideRight, int(rect.width())))
            look = "off" if not self.fold_enabled else "hover" if (self.hover_fold or self.pressed) else "normal"
            arrow, fill, border = L.FOLD_LOOK[look]
            _round_rect(p, fold.adjusted(0.5, 0.5, -0.5, -0.5), fill, border, L.FOLD_RADIUS)
            if not self.ghost and self.owner._ring_on(("fold", self.group)):
                _focus_ring(p, fold, L.FOLD_RADIUS, QRectF(self.rect()))
            c = fold.center()
            if self.open:
                _triangle(p, c.x(), c.y() + 0.5, L.FOLD_ARROW, "down", arrow)
            else:
                _triangle(p, c.x() + 0.5, c.y(), L.FOLD_ARROW, "right", arrow)
        p.end()

    def _paint_block_tag(self, p: QPainter, m: _Metrics, rect: QRectF, shown: str, color) -> None:
        """块标题写在缎带（ribbon：这一档颜色、右端一个缺口、细框）或黑色斜角标签（slant：前面一小块这一档的颜色）里
        （ListLook.BLOCK_TAG_SHAPE）。字的颜色按档位（BLOCK_TAG_INK，没写的照 BLOCK_TAG_TEXT）。"""
        from .. import decor

        ink = L.BLOCK_TAG_INK.get(self.block) or L.BLOCK_TAG_TEXT or L.TITLE_COLOR
        width = m.fm_title.horizontalAdvance(shown)
        if L.BLOCK_TAG_SHAPE == "dot":  # 前面一颗这一档颜色的圆点，后面照常写字（白色海景房）
            p.setPen(Qt.NoPen)
            p.setBrush(_color(color))
            p.drawEllipse(QPointF(rect.left() + 5, rect.center().y()), 5, 5)
            p.setPen(_color(ink))
            p.drawText(QRectF(rect.left() + 16, rect.top(), rect.width() - 16, rect.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, shown)
            return
        if L.BLOCK_TAG_SHAPE == "bar":  # 前面一根这一档颜色的竖条，后面写字，字后面一条细线（BLOCK_TAG_BORDER）到右边（碳纤流光）
            p.fillRect(QRectF(rect.left(), rect.top() + 2, 3, rect.height() - 4), _color(color))
            p.setPen(_color(ink))
            p.drawText(QRectF(rect.left() + 12, rect.top(), rect.width() - 12, rect.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, shown)
            start = rect.left() + 12 + width + 14
            if L.BLOCK_TAG_BORDER is not None and rect.right() - start > 24:
                y = rect.center().y() + 0.5
                p.setPen(QPen(_color(L.BLOCK_TAG_BORDER), 1))
                p.drawLine(QPointF(start, y), QPointF(rect.right(), y))
            return
        if L.BLOCK_TAG_SHAPE == "ribbon":
            box = QRectF(rect.left(), rect.top() - 3, width + 34, rect.height() + 6)
            decor.ribbon(p, box, _color(color), _color(L.BLOCK_TAG_BORDER) if L.BLOCK_TAG_BORDER is not None else None)
            p.setPen(_color(ink))
            p.drawText(box.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, shown)
            return
        box = QRectF(rect.left(), rect.top() - 3, width + 44, rect.height() + 6)
        fill = L.BLOCK_TAG_FILL if L.BLOCK_TAG_FILL is not None else L.TITLE_COLOR
        p.fillPath(decor.slant(box, 8), _color(fill))
        mark = QRectF(box.left() + 9, box.top() + box.height() * 0.24, 9, box.height() * 0.52)
        p.fillPath(decor.slant(mark, 4), _color(color))
        p.setPen(_color(ink))
        p.drawText(QRectF(mark.right() + 6, box.top(), box.width() - 30, box.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   shown)

    def _on_fold(self, pos: QPointF) -> bool:
        return self.kind == "group" and self.fold_rect().contains(pos)

    def _set_hover(self, on: bool) -> None:
        if on != self.hover_fold:
            self.hover_fold = on
            if on and self.fold_enabled:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()

    def mousePressEvent(self, event) -> None:
        if self.ghost or event.button() != Qt.LeftButton or not self._on_fold(event.position()) \
                or not self.fold_enabled:
            event.ignore()
            return
        self.pressed = True
        self.update()
        self.owner._mouse_cursor(("fold", self.group))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        self._set_hover(self._on_fold(event.position()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self.pressed:
            return
        self.pressed = False
        self.update()
        if self._on_fold(event.position()) and self.fold_enabled:
            self.owner._toggle_group(self)

    def leaveEvent(self, event) -> None:
        self._set_hover(False)
        super().leaveEvent(event)


class _InlineHost(_Item):
    """窗口放进列表的控件（例如软锁的问话）外面那一层：占满一行宽，紧挨在某一行下面。"""

    def __init__(self, owner: SchemeList, widget: QWidget):
        super().__init__(owner)
        self.widget = widget
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*_inline_margins())
        lay.setSpacing(0)
        lay.addWidget(widget)
        widget.show()

    def measure(self, width: int) -> int:
        if self.hasHeightForWidth():
            return max(1, self.heightForWidth(width))
        return max(1, self.sizeHint().height())

    def release(self) -> None:
        """把窗口的控件交还：藏起来、父控件设回 None。"""
        self.layout().removeWidget(self.widget)
        self.widget.hide()
        self.widget.setParent(None)

    def event(self, event) -> bool:
        if event.type() == QEvent.LayoutRequest:
            QTimer.singleShot(0, self.owner, self.owner._inline_changed)
        return super().event(event)


class _EdgeHint(QWidget):
    """列表上沿或下沿的一行提示：深色圆角底、亮色字，左边一个指向那一边的小三角；不接鼠标（点得到下面的行），
    HINT_MS 毫秒后消失（做动画时先淡出）。"""

    def __init__(self, owner: SchemeList, side: str):
        super().__init__(owner.viewport())
        self.owner = owner
        self.side = side
        self.lines: list[str] = []
        self.active = False
        self.opacity = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self._fade = QTimer(self)
        self._fade.setInterval(FRAME_MS)
        self._fade.timeout.connect(self._fade_step)
        self._fade_t0 = 0.0
        self.hide()

    def show_lines(self, lines: list[str]) -> None:
        lines = list(dict.fromkeys(lines))
        self.lines = lines[-L.HINT_LINES:] if L.HINT_LINES > 0 else []  # 上限为 0 时一行都不显示（[-0:] 会取到全部）
        if not self.lines:  # 没有要显示的：整个提示框都不显示（不画空的底和小三角）
            self.close_hint()
            return
        self.active = True
        self.opacity = 1.0
        self._fade.stop()
        self.reposition()
        self.show()
        self.raise_()
        self.update()
        self.timer.start(HINT_MS)

    def expire(self) -> None:
        if self.owner._animations_on():
            self._fade_t0 = self.owner._clock()
            self._fade.start()
        else:
            self.close_hint()

    def _fade_step(self) -> None:
        p = (self.owner._clock() - self._fade_t0) * 1000.0 / HINT_FADE_MS
        if p >= 1.0:
            self.close_hint()
        else:
            self.opacity = 1.0 - p
            self.update()

    def close_hint(self) -> None:
        self.timer.stop()
        self._fade.stop()
        self.active = False
        self.lines = []
        self.hide()

    def reposition(self) -> None:
        if not self.lines:
            return
        vp = self.owner.viewport()
        m = self.owner._m()
        text_w = max(m.fm_row.horizontalAdvance(line) for line in self.lines)
        w = min(max(0, vp.width() - 2 * L.HINT_MARGIN),
                2 * L.HINT_PAD_X + L.HINT_ARROW[0] + L.HINT_ARROW_GAP + text_w + 2)
        h = 2 * L.HINT_PAD_Y + len(self.lines) * m.fm_row.height() + 2
        x = (vp.width() - w) // 2
        y = L.HINT_MARGIN if self.side == TOP else vp.height() - h - L.HINT_MARGIN
        self.setGeometry(x, max(0, y), w, h)

    def paintEvent(self, event) -> None:
        m = self.owner._m()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        _round_rect(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), L.HINT_FILL, L.HINT_BORDER, L.HINT_RADIUS)
        line_h = m.fm_row.height()
        top = L.HINT_PAD_Y + 1
        ax = L.HINT_PAD_X + L.HINT_ARROW[0] / 2
        _triangle(p, ax, top + line_h / 2, L.HINT_ARROW, "up" if self.side == TOP else "down", L.HINT_ARROW_COLOR)
        x = L.HINT_PAD_X + L.HINT_ARROW[0] + L.HINT_ARROW_GAP
        room = max(0, self.width() - x - L.HINT_PAD_X)
        p.setFont(m.font_row)
        p.setPen(_color(L.HINT_TEXT))
        for i, line in enumerate(self.lines):
            p.drawText(QRectF(x, top + i * line_h, room, line_h), Qt.AlignVCenter | Qt.AlignLeft,
                       m.fm_row.elidedText(line, Qt.ElideRight, room))
        p.end()
