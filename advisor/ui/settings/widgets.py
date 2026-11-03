"""设置窗口里几套排法共用的行部件：整行可点的箭头（Chevron）、识别模式的方块（ModeTile）、键帽（KeyCap）、状态标签
（StatusChip）、开关（Switch）、整行可点的一行（RowButton）、「皮肤」一行的缩略图和控件（SkinThumb、SkinRow），还有排好的一行
的小容器（RowParts）和按钮小工具（_button）。

颜色、尺寸和样式表都在皮肤里（theme.SettingsLook、theme.settings_style），画控件时按当前皮肤取（L.名字）。界面文字在
dialog.py，从它导入；系统图标（glyphs.py）和底层部件（ui/widgets.py）照旧用。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import decor, skin_preview, theme
from ..glyphs import ICON_CHEVRON, draw_glyph
from ..text import tip_paragraphs_html
from ..theme import SETTINGS as L
from ..widgets import TextLabel, color
from .dialog import CHEVRON_TEXT, SKIN_CHANGE, SKIN_CHANGE_TIP


# ------------------------------------------------------------ 控件
class Chevron(QWidget):
    """整行可点的一行右端的箭头：系统图标，没有图标字体时写 CHEVRON_TEXT。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        rect = QRectF(self.rect())
        if not draw_glyph(p, rect, ICON_CHEVRON, L.CHEVRON_PX, L.DIM_COLOR):
            p.setPen(color(L.DIM_COLOR))
            p.drawText(rect, Qt.AlignCenter, CHEVRON_TEXT)
        p.end()


class ModeTile(QAbstractButton):
    """识别模式的一块可选的方块：左上圆点和标题，下面一句说明；选中的金色描边、淡金色底和实心圆点。整块点一下选中。"""

    def __init__(self, title: str, desc: str = ""):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)  # 鼠标移进移出时重画（框线变亮）
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setText(title)
        box = QVBoxLayout(self)
        left, top, right, bottom = L.TILE_MARGINS
        box.setContentsMargins(left + L.RADIO_SIZE + L.RADIO_GAP, top, right, bottom)
        box.setSpacing(4)
        self.title = TextLabel(title, "tileTitle", wrap=False)
        self.desc = TextLabel(desc, "tileDesc")
        box.addWidget(self.title)
        box.addWidget(self.desc)
        box.addStretch(1)  # 两块高度不同时内容都靠上，标题对齐
        if L.TILE_ON_TEXT:  # 选中的方块底色换了（霓虹贴纸的星光黄等）：字跟着换颜色
            self.toggled.connect(self._tint_text)

    def _tint_text(self, on: bool) -> None:
        """选中时标题、说明换成 SettingsLook.TILE_ON_TEXT 的颜色，没选中时照样式表。"""
        for label, ink in zip((self.title, self.desc), L.TILE_ON_TEXT):
            label.setStyleSheet(f"QLabel {{ color: {ink}; }}" if on else "")

    def set_desc(self, text: str) -> None:
        if self.desc.text() != text:
            self.desc.setText(text)

    def sizeHint(self) -> QSize:
        return self.layout().totalSizeHint()

    def _paint_styled(self, p: QPainter, on: bool, hover: bool) -> None:
        """霓虹贴纸、白色海景房的方块（ChromeLook.STYLE）：sticker 选中的整块星光黄、粗黑描边、右下实心投影，圆点黄底黑圈；
        soft 选中的白底、紫粉渐变框线、下面一圈软阴影，圆点是渐变的实心圆加白点。没选中的照 TILE_BG、TILE_BORDER。"""
        from PySide6.QtGui import QPainterPath

        k = theme.CHROME
        sticker = k.STYLE == "sticker"
        rect = QRectF(self.rect()).adjusted(1, 1, -4, -4) if sticker else QRectF(self.rect()).adjusted(1, 1, -1, -4)
        path = decor.rounded(rect, L.TILE_RADIUS)
        if on and sticker:
            decor.sticker(p, path, L.TILE_ON_BG, k, L.TILE_ON_PEN or L.TILE_PEN, (3, 3), rect)
        elif on:
            decor.soft_shadow(p, path, 0, 3, 6, k.SOFT_SHADOW)
            p.fillPath(path, decor.qc(L.TILE_ON_BG))
            p.strokePath(path, QPen(decor.fill_brush(k.ACCENT, rect), L.TILE_ON_PEN or 2))
        else:
            p.fillPath(path, color(L.TILE_BG))
            p.strokePath(path, QPen(color(L.TILE_HOVER_BORDER if hover else L.TILE_BORDER), L.TILE_PEN))
        size = L.RADIO_SIZE
        if size <= 0:
            return
        center = QPointF(L.TILE_MARGINS[0] + size / 2, self.title.geometry().center().y() + 0.5)
        r = size / 2 - 1
        dot = QPainterPath()
        dot.addEllipse(center, r, r)
        if sticker:
            p.fillPath(dot, decor.qc(L.TILE_ON_BG if on else L.TILE_BG))
            p.strokePath(dot, QPen(decor.qc(k.INK), L.RADIO_PEN))
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(decor.qc(k.INK))
                p.drawEllipse(center, L.RADIO_DOT, L.RADIO_DOT)
        elif on:
            p.fillPath(dot, decor.fill_brush(k.ACCENT, dot.boundingRect()))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(center, L.RADIO_DOT, L.RADIO_DOT)
        else:
            p.fillPath(dot, QColor("#ffffff"))
            p.strokePath(dot, QPen(color(L.RADIO_OFF_COLOR), L.RADIO_PEN))

    def _paint_cut(self, p: QPainter, on: bool, hover: bool) -> None:
        """碳纤流光的方块（SettingsLook.TILE_CUT）：切掉左上、右下两个角；选中的框线是选中色、外面一圈光（ChromeLook.HALO）、
        很淡的选中色底，圆点是选中色的圈和实心点；没选中的照 TILE_BG、TILE_BORDER（鼠标移上去 TILE_HOVER_BORDER）。"""
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)  # 四周给光留的地方
        path = decor.chamfer(rect, L.TILE_CUT)
        if on:
            decor.halo(p, path, theme.CHROME.HALO or L.SELECTED_COLOR)
            p.fillPath(path, color(L.TILE_BG))
            p.fillPath(path, color(L.TILE_ON_BG))
            p.strokePath(path, QPen(color(L.SELECTED_COLOR), L.TILE_ON_PEN or L.TILE_PEN))
        else:
            p.fillPath(path, color(L.TILE_BG))
            p.strokePath(path, QPen(color(L.TILE_HOVER_BORDER if hover else L.TILE_BORDER), L.TILE_PEN))
        size = L.RADIO_SIZE
        if size <= 0:
            return
        center = QPointF(L.TILE_MARGINS[0] + size / 2, self.title.geometry().center().y() + 0.5)
        p.setPen(QPen(color(L.SELECTED_COLOR if on else L.RADIO_OFF_COLOR), L.RADIO_PEN))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, size / 2 - 1.5, size / 2 - 1.5)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(color(L.SELECTED_COLOR))
            p.drawEllipse(center, L.RADIO_DOT, L.RADIO_DOT)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        hover = self.underMouse() or self.hasFocus()
        if theme.CHROME.STYLE in ("sticker", "soft"):
            self._paint_styled(p, on, hover)
            p.end()
            return
        if L.TILE_CUT:  # 碳纤流光：切角的方块，选中的外面一圈光
            self._paint_cut(p, on, hover)
            p.end()
            return
        border = L.SELECTED_COLOR if on else (L.TILE_HOVER_BORDER if hover else L.TILE_BORDER)
        pen = L.TILE_ON_PEN if on and L.TILE_ON_PEN else L.TILE_PEN
        p.setPen(QPen(color(border), pen))
        p.setBrush(color(L.TILE_ON_BG if on else L.TILE_BG))
        half = pen / 2 if L.TILE_ON_PEN else 0.5  # 选中时框线加粗的皮肤：框线整个画在方块里面
        badge = bool(L.TILE_BADGE)
        top = 6 if badge else 0  # 右上角的对勾圆标一半露在方块外面：方块往下、往左让出一点
        rect = QRectF(self.rect()).adjusted(half, half + top, -half - top, -half) if badge else \
            QRectF(self.rect()).adjusted(half, half, -half, -half)
        p.drawRoundedRect(rect, L.TILE_RADIUS, L.TILE_RADIUS)
        # 圆点和标题垂直居中对齐
        size = L.RADIO_SIZE
        cy = self.title.geometry().center().y() + 0.5
        cx = L.TILE_MARGINS[0] + size / 2
        if size > 0:
            p.setPen(QPen(color(L.SELECTED_COLOR if on else L.RADIO_OFF_COLOR), L.RADIO_PEN))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), size / 2 - 1.5, size / 2 - 1.5)
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(color(L.SELECTED_COLOR))
                p.drawEllipse(QPointF(cx, cy), L.RADIO_DOT, L.RADIO_DOT)
        if on and badge:  # 选中的方块右上角一个对勾圆标
            fill, ring, tick = L.TILE_BADGE
            c = QPointF(rect.right() - 6, rect.top() + 4)
            p.setPen(QPen(color(ring), 2))
            p.setBrush(color(fill))
            p.drawEllipse(c, 12, 12)
            p.setPen(QPen(color(tick), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(c.x() - 5.5, c.y()), QPointF(c.x() - 1.6, c.y() + 4),
                                      QPointF(c.x() + 5.5, c.y() - 4)]))
        p.end()


class KeyCap(QLabel):
    """键帽样子的当前热键（样式见设置窗口样式表 theme.settings_style 的 #keycap）。tone：""、"capture"（等待按键，金色）、
    "warn"（不可用，橙色框线）。"""

    def __init__(self):
        super().__init__("")
        self.setObjectName("keycap")
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setProperty("tone", "")

    def set_key(self, text: str, tone: str = "") -> None:
        self.setText("" if text is None else str(text))  # 不是文字的（坏的设置值）照样显示出来，不报错
        if (self.property("tone") or "") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)

    @property
    def tone(self) -> str:
        return self.property("tone") or ""


class StatusChip(QLabel):
    """只读的状态标签：圆角胶囊，左边一个圆点，颜色随 tone（"ok" 绿色、"warn" 橙色，样式见设置窗口样式表的 #chip；圆点的
    大小和位置见皮肤的 CHIP_DOT、CHIP_DOT_X）。"""

    def __init__(self):
        super().__init__("")
        self.setObjectName("chip")
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setProperty("tone", "ok")

    def set_status(self, text: str, tone: str = "ok") -> None:
        self.setText(text)
        if (self.property("tone") or "") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)
        if L.CHIP_BADGE_ROOM:  # 皮肤在警告的状态前面画圆标：字往右让出圆标的地方
            self.setIndent(L.CHIP_BADGE_ROOM if tone == "warn" else -1)

    @property
    def tone(self) -> str:
        return self.property("tone") or ""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        tint = (L.CHIP_WARN if self.tone == "warn" else L.CHIP_OK)[0]
        if L.CHIP_BADGE and self.tone == "warn":  # 皮肤在需要留意的状态前面画圆标（例如「!」）
            decor.badge(p, QPointF(L.CHIP_DOT_X, self.height() / 2), 9, color(tint), L.CHIP_BADGE,
                        color(L.CHIP_WARN[1]), theme.title_font(12))
        elif L.CHIP_DOT > 0:
            p.setBrush(color(tint))
            p.drawEllipse(QPointF(L.CHIP_DOT_X, self.height() / 2), L.CHIP_DOT, L.CHIP_DOT)
        p.end()


class Switch(QAbstractButton):
    """开关：关时灰色框线、圆钮在左；开时金色底、深色圆钮在右。点一下切换（clicked 带切换后的状态）。"""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)  # 鼠标移进移出时重画（框线变亮）
        self.setFixedSize(*L.SWITCH_SIZE)

    def sizeHint(self) -> QSize:
        return QSize(*L.SWITCH_SIZE)

    def _paint_styled(self, p: QPainter) -> None:
        """霓虹贴纸、白色海景房的开关（ChromeLook.STYLE）：sticker 胶囊粗黑描边、右下实心投影，开时星光黄、关时深紫；soft
        开时紫粉渐变、关时浅灰，白色圆钮下面一圈软阴影。"""
        k = theme.CHROME
        w, h = L.SWITCH_SIZE
        on = self.isChecked()
        hover = self.underMouse() or self.hasFocus()
        if k.STYLE == "sticker":
            track = QRectF(1, 1, w - 5, h - 5)
            fill = (L.SWITCH_ON_FILL or L.SELECTED_COLOR) if on else k.SURFACE
            decor.sticker(p, decor.capsule(track), fill, k, L.SWITCH_PEN, (3, 3), track)
            if hover and not on:
                p.strokePath(decor.capsule(track.adjusted(2, 2, -2, -2)), QPen(color(L.SWITCH_HOVER_BORDER), 1.2))
        else:
            track = QRectF(0.5, 0.5, w - 1, h - 1)
            path = decor.capsule(track)
            p.fillPath(path, decor.fill_brush(k.ACCENT, track) if on else color(L.SWITCH_OFF_BORDER))
            if hover and not on:
                p.strokePath(path, QPen(color(L.SWITCH_HOVER_BORDER), 1.2))
        r = track.height() / 2 - L.SWITCH_KNOB_INSET + 1
        cx = track.right() - track.height() / 2 if on else track.left() + track.height() / 2
        knob = decor.capsule(QRectF(cx - r, track.center().y() - r, 2 * r, 2 * r))
        if k.STYLE == "soft":
            decor.soft_shadow(p, knob, 0, 1, 2, (0, 0, 0, 60))
        p.fillPath(knob, color(L.SWITCH_ON_KNOB if on else L.SWITCH_OFF_KNOB))

    def _paint_cut(self, p: QPainter) -> None:
        """碳纤流光的开关（SettingsLook.TILE_CUT）：切角的轨道，开时选中色（SWITCH_ON_FILL）、外面一圈光，关时细框（鼠标移上去
        SWITCH_HOVER_BORDER）；圆钮画成切角的小方块（开时在右、关时在左）。"""
        w, h = L.SWITCH_SIZE
        on = self.isChecked()
        hover = self.underMouse() or self.hasFocus()
        track = QRectF(3, 3, w - 6, h - 6)
        cut = min(L.TILE_CUT * 0.6, track.height() * 0.35)
        path = decor.chamfer(track, cut)
        if on:
            decor.halo(p, path, theme.CHROME.HALO, 0.6)
            p.fillPath(path, color(L.SWITCH_ON_FILL or L.SELECTED_COLOR))
        else:
            p.fillPath(path, color(L.TILE_BG))
            p.strokePath(path, QPen(color(L.SWITCH_HOVER_BORDER if hover else L.SWITCH_OFF_BORDER), L.SWITCH_PEN))
        side = track.height() - 2 * L.SWITCH_KNOB_INSET
        x = track.right() - L.SWITCH_KNOB_INSET - side if on else track.left() + L.SWITCH_KNOB_INSET
        knob = decor.chamfer(QRectF(x, track.top() + L.SWITCH_KNOB_INSET, side, side), side * 0.3)
        p.fillPath(knob, color(L.SWITCH_ON_KNOB if on else L.SWITCH_OFF_KNOB))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if theme.CHROME.STYLE in ("sticker", "soft"):
            self._paint_styled(p)
            p.end()
            return
        if L.TILE_CUT:  # 碳纤流光：切角的轨道和方钮
            self._paint_cut(p)
            p.end()
            return
        w, h = L.SWITCH_SIZE
        track = QRectF(1, 1, w - 2, h - 2)
        radius = track.height() / 2
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(color(L.SWITCH_ON_FILL or L.SELECTED_COLOR))
        else:
            hover = self.underMouse() or self.hasFocus()
            p.setPen(QPen(color(L.SWITCH_HOVER_BORDER if hover else L.SWITCH_OFF_BORDER), L.SWITCH_PEN))
            p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(track, radius, radius)
        p.setPen(Qt.NoPen)
        p.setBrush(color(L.SWITCH_ON_KNOB if self.isChecked() else L.SWITCH_OFF_KNOB))
        knob = h / 2 - L.SWITCH_KNOB_INSET
        cx = w - 1 - radius if self.isChecked() else 1 + radius
        p.drawEllipse(QPointF(cx, h / 2), knob, knob)
        p.end()


class RowButton(QAbstractButton):
    """整行可点的一行（「关于拾星助手」）：里面的图标、文字、箭头由排法放；鼠标移上去、按下时整行变亮。"""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)  # 鼠标移进移出时重画（整行变亮）
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def sizeHint(self) -> QSize:
        return self.layout().totalSizeHint() if self.layout() is not None else super().sizeHint()

    def paintEvent(self, event) -> None:
        if not (self.underMouse() or self.isDown() or self.hasFocus()):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(color(L.ROW_PRESSED_BG if self.isDown() else L.ROW_HOVER_BG))
        inset = L.ROW_INSET
        p.drawRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), L.CARD_RADIUS - 2, L.CARD_RADIUS - 2)
        p.end()


class SkinThumb(QWidget):
    """「皮肤」一行右边的小缩略图：当前这套皮肤的悬浮窗上面一截（预览图，skin_preview；还没画好时画这套的几块主色），
    外面一圈方块的框线（TILE_BORDER）。只显示，不接鼠标。previews 为预览图（skin_preview.PreviewStore），None 时画主色；
    预览图后来才画好时由设置窗口换上（set_image）。"""

    SIZE = (44, 28)
    RADIUS = 4

    def __init__(self, previews=None):
        super().__init__()
        self.key = theme.current_key()
        self._colors = skin_preview.main_colors(theme.current())
        self._image = previews.image(self.key) if previews is not None else None
        self.setFixedSize(*self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    @property
    def has_preview(self) -> bool:
        return self._image is not None

    def set_image(self, image) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        skin_preview.paint_thumb(p, rect, self._image, self._colors, radius=self.RADIUS, fade=False)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color(L.TILE_BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), self.RADIUS, self.RADIUS)
        p.end()


class SkinRow(QWidget):
    """「皮肤」一行右边的控件（做法同「识别热键」一行的键帽和「更改」）：当前这套的小缩略图和名称（只显示，不能点），
    「更换」按钮（悬停说明 SKIN_CHANGE_TIP；点了打开「皮肤」窗口，由设置窗口接到悬浮窗的 show_skins）。"""

    def __init__(self, previews=None):
        super().__init__()
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.thumb = SkinThumb(previews)
        self.name = TextLabel(theme.current().label, "skinName", wrap=False)
        self.button = _button(SKIN_CHANGE, SKIN_CHANGE_TIP)
        line.addWidget(self.thumb, 0, Qt.AlignVCenter)
        line.addWidget(self.name, 0, Qt.AlignVCenter)
        line.addSpacing(4)
        line.addWidget(self.button, 0, Qt.AlignVCenter)


def _button(text: str, tip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoDefault(False)  # 回车不触发窗口里的按钮（等待按键时回车也可能是要设的热键）
    if tip:
        button.setToolTip(tip_paragraphs_html(tip))
    return button


# ------------------------------------------------------------ 排好的一行

@dataclass
class RowParts:
    """排好的一行：整行（「关于」那一行是 RowButton）、标题、说明，和说明下面另一段暗色说明（各排法都留着这一格，平时隐藏；
    现在只有「启动时自动检查更新」一行用它写检查的结果，有字时代替说明显示）；icon 是这一行左边的系统
    图标（画行图标的排法才有，皮肤解锁的调色盘机关用，见 PaletteTrap）。"""

    row: QWidget
    title: TextLabel
    desc: TextLabel
    note: TextLabel
    icon: QWidget | None = None
