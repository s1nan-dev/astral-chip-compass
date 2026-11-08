"""「拾星助手皮肤」窗口（设置窗口「外观」组「皮肤」一行的「更换」打开）：登记的各套皮肤按 theme.skins() 的先后排成格子（每行
COLUMNS 格，七套是 4＋3 两行：第一行默认和 RGB 风的三套，第二行经典主题三套），每格是这套皮肤的悬浮窗缩略图和名称，点一下
当场换成这套（悬浮窗的 set_skin：存进设置，下次启动沿用）；正在用的那套标「使用中」。格子空出的一格写一句缩略图的说明。

- 缩略图是预览图（skin_preview：程序自己离屏画的各套皮肤的悬浮窗，存在 assets\\skins）；还没画好时先画这套的几块主色，
  画好时（PreviewStore.drawn）当场换上。
- 皮肤解锁（advisor/unlock.py）：还没解锁的那几套照样占一格，格子里是一张灰色剪影（paint_silhouette：通用的悬浮窗轮廓，
  几套都一样，看不出各自的样子），中间写 LOCKED_TEXT，名称写 LOCKED_NAME；点了没反应。剪影下面、窗口顶上都不写提示。
  解锁了一套时窗口当场重建（refresh_unlock）。开发版一律解锁。
- 窗口自己跟着当前皮肤换样子：配色、字体、外框都按皮肤取，不为哪一套单写。用系统标题栏的皮肤（Layouts.CHROME 为 system，
  默认皮肤）照设置窗口的样子（样式表 theme.settings_style，大标题旁的图标、卡片、模式方块的颜色，SettingsLook），关闭用系统
  标题栏的 ×，不另放「关闭」按钮；自己画外框的皮肤照游戏内风格（chrome.py：窗口底色、深灰面板、卡片，「使用中」是卡片
  左上角的标签）：外框为 back 时左上角「返回」，为 close 时顶上一行照这套皮肤的样子（标题页签或通栏、右上角 ×，
  chrome.WindowHead），别的外框右上角 ×（一个窗口只留一个关闭按钮）。小控件有自己画风的皮肤（ChromeLook.STYLE：霓虹贴纸、
  白色海景房）说明跟在顶上一行的标题后面，格子直接放在窗口底上、照这套的卡片画，「使用中」是名称后面的一枚胶囊。
- 换皮肤时窗口当场按新皮肤重建内容（apply_skin），窗口的左上角不动；大小随皮肤变，变大以后超出屏幕的往回挪。
- 窗口不超过所在屏幕的可用高度：放不下时格子在窗口里上下滚动，标题照旧在上面。
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import unlock
from . import chrome, decor, lang, skin_preview, theme
from .glyphs import ICON_ABOUT, ICON_SKIN, draw_glyph
from .widgets import GlyphBox, TextLabel, color
from .theme import COMMON as C
from .theme import SETTINGS as L
from .window_base import SkinnedWindow

# ------------------------------------------------------------ 界面文字

WINDOW_TITLE = "拾星助手皮肤"  # 窗口标题（系统标题栏、任务栏）
PAGE_TITLE = "皮肤"  # 窗口里的大标题
HINT = "点击缩略图即可更换：所有窗口立即换成所选的样子，下次启动沿用"
NOTE = "缩略图是各套皮肤的悬浮窗"  # 格子空出的一格
CURRENT = "使用中"  # 正在用的那一套的标记
# 还没解锁的格子（灰色剪影中间写 LOCKED_TEXT，名称写 LOCKED_NAME）
LOCKED_TEXT = "未解锁"
LOCKED_NAME = "？？？"

# ------------------------------------------------------------ 尺寸（逻辑像素）

COLUMNS = 4  # 每行几格（七套是 4＋3 两行）；格子少时也按 4 列的宽度排，窗口宽度不随皮肤的套数变
TILE_W = 176  # 一格的宽
PAD = 8  # 缩略图离格子边缘
THUMB_H = 190  # 缩略图的高（宽 = TILE_W - 2 × PAD）
CAPTION_H = 38  # 缩略图下面写名称的一行
GAP = 12  # 格子之间
CHROME_GAP = 16  # 游戏内风格的格子之间（卡片带投影、标签）
SHADOW = 4  # 游戏内风格的卡片下沿的投影（decor.card）
FOCUS_PEN = 1.5  # 键盘焦点：格子里面一圈虚线
INFO_PX = 20  # 说明那一格的「i」
GLOW = 4  # 正在用的那一格外面一圈光（ChromeLook.CURRENT_MARK 为 chip）：格子四周留出的地方
STYLED_ROOM = 10  # 霓虹贴纸、白色海景房的格子：卡片外给投影（贴纸右下的实心投影、浅色卡片的软阴影）留的高度
# 没解锁的格子里的灰色剪影（paint_silhouette）：底（从上到下）、悬浮窗的轮廓、轮廓里的几块、字底下的一块和字。不随皮肤
SILHOUETTE_BG = ("#5b5e66", "#45474e")
SILHOUETTE_PANEL = "#73767f"
SILHOUETTE_BLOCK = "#868a93"
SILHOUETTE_PLATE = (40, 42, 48, 210)
SILHOUETTE_TEXT = "#eef0f4"


def paint_silhouette(p: QPainter, rect: QRectF, radius: float = 6.0) -> None:
    """还没解锁的格子里的缩略图：灰色的底上一个通用的悬浮窗轮廓（一条标题栏、三块卡片、右上一排圆点，几套都一样，看不出
    各自的样子），中间一块深灰的底上写 LOCKED_TEXT。圆角 radius。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip, Qt.IntersectClip)
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0, QColor(SILHOUETTE_BG[0]))
    g.setColorAt(1, QColor(SILHOUETTE_BG[1]))
    p.fillRect(rect, g)
    pad = max(2.0, round(rect.width() * 0.05))
    inner = rect.adjusted(pad, pad, -pad, 0)
    r = max(3.0, inner.width() * 0.06)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(SILHOUETTE_PANEL))
    p.drawRoundedRect(inner.adjusted(0, 0, 0, inner.height()), r, r)  # 往下伸出去：悬浮窗只看上面一截
    m = inner.width() * 0.07
    x, w, y = inner.left() + m, inner.width() - 2 * m, inner.top() + m
    bar = max(4.0, inner.height() * 0.06)
    p.setBrush(QColor(SILHOUETTE_BLOCK))
    p.drawRoundedRect(QRectF(x, y, w * 0.45, bar), bar / 2, bar / 2)
    dot = max(2.5, inner.width() * 0.035)
    for i in range(3):
        p.drawEllipse(QPointF(inner.right() - m - dot - i * 2.6 * dot, y + bar / 2), dot, dot)
    y += bar + m
    for i in range(3):
        h = inner.height() * (0.16 if i == 0 else 0.2)
        p.drawRoundedRect(QRectF(x, y, w, h), r * 0.7, r * 0.7)
        y += h + m * 0.8
    font = QFont(p.font())
    font.setPointSizeF(C.FONT_M)
    font.setBold(True)
    fm = QFontMetricsF(font)
    plate = QRectF(0, 0, fm.horizontalAdvance(LOCKED_TEXT) + 24, fm.height() + 12)
    plate.moveCenter(rect.center())
    p.setBrush(QColor(*SILHOUETTE_PLATE))
    p.drawRoundedRect(plate, plate.height() / 2, plate.height() / 2)
    p.setFont(font)
    p.setPen(QColor(SILHOUETTE_TEXT))
    p.drawText(plate, Qt.AlignCenter, LOCKED_TEXT)
    p.restore()


def _custom() -> bool:
    """当前皮肤自己画外框（同 chrome.custom）。"""
    return chrome.custom()


def _styled() -> bool:
    """当前皮肤的小控件有自己的画风（ChromeLook.STYLE：霓虹贴纸 sticker、白色海景房 soft）：格子、空出那一格、说明照它画，
    格子直接放在窗口底上（不另垫深灰面板）。"""
    return _custom() and theme.CHROME.STYLE in ("sticker", "soft")


def _tag_size(font: QFont, text: str) -> tuple[float, float]:
    """卡片左上角标签（decor.tag，同 chrome.Tag）的宽和高（含下沿 3 像素的投影）。"""
    return QFontMetricsF(font).horizontalAdvance(text) + 28, font.pixelSize() + 18 + 3


class SkinTile(QAbstractButton):
    """一格：缩略图（这套皮肤的悬浮窗，skin_preview.paint_thumb；预览图还没画好时是几块主色），下面一行是圆点和名称；正在用的
    那套标「使用中」。点一下换成这套。样子按建它时的皮肤（换皮肤时窗口整个重建）：
    - 用系统标题栏的皮肤：照设置窗口的卡片（CARD_BG、CARD_BORDER）和模式方块（圆点；选中的金色框线、淡金色底、实心圆点），
      名称右边写「使用中」（选中色）；
    - 自己画外框的皮肤：游戏内风格的浅灰卡片（ChromeLook 的 CARD_*，decor.card），名称用标题字体，右下一个白底圆点；使用中
      卡片外一圈标签的颜色（TAG_FILL 的最后一种），左上角压「使用中」标签（decor.tag），圆点里画实心的选中色；
    - 霓虹贴纸、白色海景房（ChromeLook.STYLE 为 sticker、soft）：照这套的卡片画（_paint_styled），圆点在名称左边，「使用中」
      是名称后面的胶囊；
    - 碳纤流光（ChromeLook.CURRENT_MARK 为 chip）：切角的面板（_paint_chip），圆点在名称左边，正在用的那一格外面一圈光，
      「使用中」是名称一行最右边的切角小牌子。"""

    def __init__(self, skin: theme.Skin, parent=None):
        super().__init__(parent)
        self.key = skin.key
        # 还没解锁：灰色剪影、名称写 LOCKED_NAME，点了没反应（窗口不接它的 clicked），鼠标移上去不变亮
        self.locked = not unlock.skin_open(skin.key)
        label = LOCKED_NAME if self.locked else lang.t(skin.label)
        self.setText(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.ArrowCursor if self.locked else Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus if self.locked else Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)  # 鼠标移进移出时重画（框线变亮）
        self._chrome = _custom()
        self._style = theme.CHROME.STYLE if _styled() else ""  # 霓虹贴纸 sticker、白色海景房 soft 的画法（_paint_styled）
        self._colors = skin_preview.main_colors(skin)
        self._image = None
        self._current = False
        self._key_focus = False  # 用 Tab 键走到的：画焦点框（打开窗口时自动落在第一格的焦点不画）
        self.setFixedSize(self.sizeHint())

    def focusInEvent(self, event) -> None:  # noqa: N802  Qt 的虚函数
        self._key_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._key_focus = False
        super().focusOutEvent(event)

    # ---- 状态

    def set_image(self, image) -> None:
        """换上预览图（None：还没有，画几块主色）。"""
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    @property
    def has_preview(self) -> bool:
        return self._image is not None

    def set_current(self, on: bool) -> None:
        self._current = bool(on)
        self.update()

    @property
    def is_current(self) -> bool:
        return self._current

    def colors(self) -> list:
        """还没有预览图时画的几块主色（skin_preview.main_colors）。"""
        return list(self._colors)

    # ---- 尺寸

    def _tag_room(self) -> float:
        """游戏内风格：卡片上沿往下挪的高度（「使用中」标签一半露在卡片外面）。"""
        if self._chrome and theme.CHROME.CURRENT_MARK == "chip":  # 「使用中」在名称一行最右边：上沿只留出外面一圈光的地方
            return float(GLOW)
        return (_tag_size(theme.title_font(theme.CHROME.TAG_PX), CURRENT)[1] - 3) / 2 if self._chrome else 0.0

    def sizeHint(self) -> QSize:
        if self._style:
            extra = STYLED_ROOM
        else:
            extra = round(self._tag_room()) + SHADOW if self._chrome else 0
        return QSize(TILE_W, PAD + THUMB_H + CAPTION_H + extra)

    def body_rect(self) -> QRectF:
        """卡片本身（不含上面给标签留的地方和下沿的投影）。"""
        if self._style == "sticker":  # 右下留出实心投影
            return QRectF(1, 1, self.width() - 6, self.height() - 6)
        if self._style:  # 四周、下面留出软阴影
            return QRectF(4, 1, self.width() - 8, self.height() - 10)
        if self._chrome and theme.CHROME.CURRENT_MARK == "chip":  # 四周留出外面一圈光的地方（下沿同投影那一截）
            return QRectF(GLOW, GLOW, self.width() - 2 * GLOW, self.height() - GLOW - SHADOW)
        if self._chrome:
            top = self._tag_room()
            return QRectF(1, top, self.width() - 2, self.height() - top - SHADOW - 1)
        return QRectF(self.rect()).adjusted(1, 1, -1, -1)

    def thumb_rect(self) -> QRectF:
        body = self.body_rect()
        return QRectF(body.left() + PAD - 1, body.top() + PAD - 1, body.width() - 2 * (PAD - 1), THUMB_H)

    # ---- 画

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._chrome:
            self._paint_chrome(p)
        else:
            self._paint_plain(p)
        p.end()

    @staticmethod
    def _radio(p: QPainter, center: QPointF, ring, fill, dot, on: bool) -> None:
        """圆点（同设置窗口的模式方块：RADIO_SIZE、RADIO_PEN、RADIO_DOT）：平时 ring 色的圈（fill 不为空时圈里铺它），选中时
        圈和中间的实心点都是 dot 色。"""
        size = L.RADIO_SIZE
        p.setPen(QPen(color(dot if on else ring), L.RADIO_PEN))
        p.setBrush(decor.qc(fill) if fill else Qt.NoBrush)
        p.drawEllipse(center, size / 2 - 1.5, size / 2 - 1.5)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(color(dot))
            p.drawEllipse(center, L.RADIO_DOT, L.RADIO_DOT)

    def _focused(self) -> bool:
        """用键盘走到这一格（画焦点框、框线变亮）。"""
        return self._key_focus and self.hasFocus()

    def _hovered(self) -> bool:
        """鼠标在这一格上、或用键盘走到这一格（框线变亮）；没解锁的格子不变亮（点了没反应）。"""
        return not self.locked and (self.underMouse() or self._focused())

    def _thumb(self, p: QPainter, rect: QRectF, radius: float) -> None:
        """缩略图：预览图（还没画好时几块主色，skin_preview.paint_thumb）；没解锁的是灰色剪影（paint_silhouette）。"""
        if self.locked:
            paint_silhouette(p, rect, radius)
        else:
            skin_preview.paint_thumb(p, rect, self._image, self._colors, radius=radius)

    def _paint_plain(self, p: QPainter) -> None:
        body = self.body_rect()
        on, hover = self._current, self._hovered()
        radius = L.CARD_RADIUS
        p.setPen(Qt.NoPen)
        p.setBrush(color(L.CARD_BG))
        p.drawRoundedRect(body, radius, radius)
        if on:
            p.setBrush(color(L.TILE_ON_BG))
            p.drawRoundedRect(body, radius, radius)
        border = L.SELECTED_COLOR if on else (L.TILE_HOVER_BORDER if hover else L.CARD_BORDER)
        pen_w = 2.0 if on else 1.0
        p.setPen(QPen(color(border), pen_w))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body.adjusted(pen_w / 2, pen_w / 2, -pen_w / 2, -pen_w / 2), radius, radius)
        self._thumb(p, self.thumb_rect(), max(2.0, radius - 2))
        line = QRectF(body.left() + PAD, body.top() + PAD + THUMB_H, body.width() - 2 * PAD, CAPTION_H - 2)
        if not self.locked:
            self._radio(p, QPointF(line.left() + L.RADIO_SIZE / 2, line.center().y()), L.RADIO_OFF_COLOR, "",
                        L.SELECTED_COLOR, on)
        left, right = line.left() + L.RADIO_SIZE + L.RADIO_GAP, line.right()
        if on:
            mark = QFont(self.font())
            mark.setPointSizeF(C.FONT_M)
            p.setFont(mark)
            p.setPen(color(L.SELECTED_COLOR))
            mark_w = QFontMetricsF(mark).horizontalAdvance(CURRENT)
            p.drawText(QRectF(right - mark_w, line.top(), mark_w, line.height()), Qt.AlignVCenter | Qt.AlignRight,
                       CURRENT)
            right -= mark_w + 8
        name = QFont(self.font())
        name.setPointSizeF(C.FONT_M)
        name.setBold(True)
        p.setFont(name)
        p.setPen(color(L.TEXT_COLOR))
        rect = QRectF(left, line.top(), max(right - left, 1), line.height())
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(name).elidedText(self.text(), Qt.ElideRight, rect.width()))
        if self._focused():
            self._focus_ring(p, body, radius, color(L.TEXT_COLOR))

    def _paint_styled(self, p: QPainter) -> None:
        """霓虹贴纸、白色海景房（ChromeLook.STYLE）的格子，卡片直接放在窗口底上：
        - sticker：粗黑描边、右下实心投影的深色贴纸卡片（CARD_FILL），正在用的那一格整块星光黄（ACCENT，同设置里选中的模式
          方块），名称换成深色（ACCENT_TEXT），后面一枚墨黑胶囊「使用中」（字是星光黄）；圆点平时白圈，选中时黑圈黑点；
        - soft：白卡片、浅灰框线，正在用的那一格紫粉渐变框线、下面一圈软阴影，名称换成强调色，后面一枚渐变胶囊「使用中」
          （白字）；圆点平时浅灰圈，选中时渐变的实心圆加白点。"""
        k = theme.CHROME
        body = self.body_rect()
        sticker = self._style == "sticker"
        accent = decor.colors_of(k.ACCENT) if k.ACCENT else [L.SELECTED_COLOR]
        on, hover = self._current, self._hovered()
        radius = 14.0 if sticker else 16.0
        path = decor.rounded(body, radius)
        if sticker:
            decor.sticker(p, path, accent[0] if on else k.CARD_FILL, k, 2.2, (4, 4), body)
            if hover and not on:
                p.strokePath(decor.rounded(body.adjusted(3, 3, -3, -3), radius - 3),
                             QPen(color(L.TILE_HOVER_BORDER), 1.6))
        elif on:
            decor.soft_shadow(p, path, 0, 3, 4, k.SOFT_SHADOW)
            p.fillPath(path, decor.qc("#ffffff"))
            p.strokePath(decor.rounded(body.adjusted(1, 1, -1, -1), radius - 1),
                         QPen(decor.fill_brush(k.ACCENT, body), 2))
        else:
            p.fillPath(path, decor.qc(k.CARD_FILL))
            edge = color(L.TILE_HOVER_BORDER) if hover else decor.qc(k.CARD_EDGE)
            p.strokePath(path, QPen(edge, 1.4 if hover else 1))
        thumb = self.thumb_rect()
        self._thumb(p, thumb, 10 if sticker else 11)
        if sticker:
            p.setPen(QPen(decor.qc(k.INK), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(thumb, 10, 10)
        line = QRectF(body.left() + PAD, thumb.bottom(), body.width() - 2 * PAD, body.bottom() - thumb.bottom())
        center = QPointF(line.left() + 9, line.center().y())
        dot = decor.capsule(QRectF(center.x() - 8, center.y() - 8, 16, 16))
        if self.locked:
            pass  # 没解锁的不画圆点
        elif sticker:
            if on:
                p.fillPath(dot, decor.qc(accent[0]))
            p.strokePath(dot, QPen(decor.qc(k.INK if on else C.TEXT_COLOR), 2))
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(decor.qc(k.INK))
                p.drawEllipse(center, 3.4, 3.4)
        elif on:
            p.fillPath(dot, decor.fill_brush(k.ACCENT, dot.boundingRect()))
            p.setPen(Qt.NoPen)
            p.setBrush(decor.qc("#ffffff"))
            p.drawEllipse(center, 3, 3)
        else:
            p.fillPath(dot, decor.qc("#ffffff"))
            p.strokePath(dot, QPen(color(L.RADIO_OFF_COLOR), 1.4))
        right = line.right()
        if on:
            small = theme.title_font(12)
            tag_w = QFontMetricsF(small).horizontalAdvance(CURRENT) + 16
            pill = QRectF(right - tag_w, line.center().y() - 11, tag_w, 22)
            if sticker:
                p.fillPath(decor.capsule(pill), decor.qc(k.INK))
                decor.outlined_text(p, pill, CURRENT, small, accent[0])
            else:
                p.fillPath(decor.capsule(pill), decor.fill_brush(k.ACCENT, pill))
                decor.outlined_text(p, pill, CURRENT, small, k.ACCENT_TEXT)
            right -= tag_w + 6
        ink = (k.ACCENT_TEXT if sticker else accent[0]) if on else C.TEXT_COLOR
        name = QRectF(line.left() + 24, line.top(), max(right - line.left() - 24, 1), line.height())
        font = theme.title_font(14)
        for px in (13, 12):  # 名称连着「使用中」放不下时字号减一点，再放不下才省略
            if QFontMetricsF(font).horizontalAdvance(self.text()) <= name.width():
                break
            font = theme.title_font(px)
        decor.outlined_text(p, name, QFontMetricsF(font).elidedText(self.text(), Qt.ElideRight, name.width()), font,
                            ink, align=Qt.AlignLeft)
        if self._focused():
            self._focus_ring(p, body.adjusted(4, 4, -4, -4), radius - 3, decor.qc(k.INK if sticker else accent[0]))

    def _paint_chip(self, p: QPainter) -> None:
        """ChromeLook.CURRENT_MARK 为 chip（碳纤流光）的格子：切角的面板（decor.card），左下圆点、斜体的名称；正在用的那一格
        外面一圈光（HALO）、框线和名称换成 HALO 的颜色，名称一行最右边一枚切角的「使用中」（HALO 色的细框和字，很淡的底）；
        鼠标移上去框线变亮。"""
        k = theme.CHROME
        body = self.body_rect()
        on, hover = self._current, self._hovered()
        halo = decor.qc(k.HALO or L.SELECTED_COLOR)
        path = decor.chamfer(body.adjusted(0.5, 0.5, -0.5, -0.5), k.CHAMFER)
        if on:
            decor.halo(p, path, halo)
        decor.card(p, body, k)
        if on or hover:
            p.strokePath(path, QPen(halo if on else color(L.TILE_HOVER_BORDER), 1.6 if on else 1.2))
        self._thumb(p, self.thumb_rect(), 2)
        line = QRectF(body.left() + PAD, body.top() + PAD + THUMB_H, body.width() - 2 * PAD, CAPTION_H - 2)
        if not self.locked:
            self._radio(p, QPointF(line.left() + L.RADIO_SIZE / 2, line.center().y()), L.RADIO_OFF_COLOR, "",
                        L.SELECTED_COLOR, on)
        left, right = line.left() + L.RADIO_SIZE + L.RADIO_GAP, line.right()
        if on:
            small = QFont(self.font())
            small.setPointSizeF(C.FONT_S)
            small.setBold(True)
            tag_w = QFontMetricsF(small).horizontalAdvance(CURRENT) + 16
            chip = QRectF(right - tag_w, line.center().y() - 11, tag_w, 22)
            faint = decor.qc(halo)
            faint.setAlpha(30)
            decor.cut_plate(p, chip, faint, halo, 1.2)
            decor.flat_text(p, chip, CURRENT, small, halo, Qt.AlignHCenter)
            right -= tag_w + 6
        font = theme.title_font(14)
        font.setItalic(True)
        name = QRectF(left, line.top(), max(right - left, 1), line.height())
        decor.flat_text(p, name, QFontMetricsF(font).elidedText(self.text(), Qt.ElideRight, name.width() - 4), font,
                        halo if on else decor.qc(C.TEXT_COLOR))
        if self._focused():
            p.setPen(QPen(decor.qc(C.TEXT_COLOR), FOCUS_PEN, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(decor.chamfer(body.adjusted(5, 5, -5, -5), max(0.0, k.CHAMFER - 4)))

    def _paint_chrome(self, p: QPainter) -> None:
        if self._style:
            self._paint_styled(p)
            return
        if theme.CHROME.CURRENT_MARK == "chip":
            self._paint_chip(p)
            return
        k = theme.CHROME
        body = self.body_rect()
        radius = min(float(k.CARD_RADIUS), 18.0)
        on, hover = self._current, self._hovered()
        decor.card(p, body, k, seed=11 + len(self.key) * 5, radius=radius, shadow=SHADOW)
        self._thumb(p, self.thumb_rect(), max(4.0, radius - 6))
        line = QRectF(body.left() + PAD + 4, body.top() + PAD + THUMB_H, body.width() - 2 * PAD - 8, CAPTION_H - 2)
        if not self.locked:
            self._radio(p, QPointF(line.right() - L.RADIO_SIZE / 2, line.center().y()), L.RADIO_OFF_COLOR,
                        k.LIGHT_FILL, L.SELECTED_COLOR, on)
        font = theme.title_font(15)
        p.setFont(font)
        p.setPen(decor.qc(C.TEXT_COLOR))
        rect = QRectF(line.left(), line.top(), max(line.width() - L.RADIO_SIZE - 6, 1), line.height())
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(font).elidedText(self.text(), Qt.ElideRight, rect.width()))
        if on or hover:
            pen = QPen(decor.qc(decor.colors_of(k.TAG_FILL)[-1]) if on else color(L.TILE_HOVER_BORDER),
                       4.0 if on else 2.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            inset = pen.widthF() / 2
            p.drawRoundedRect(body.adjusted(inset, inset, -inset, -inset), radius, radius)
        if on:
            font = theme.title_font(k.TAG_PX)
            tag_w, tag_h = _tag_size(font, CURRENT)
            decor.tag(p, QRectF(0, 0, tag_w, tag_h), CURRENT, font, k)
        if self._focused():
            self._focus_ring(p, body.adjusted(4, 4, -4, -4), radius - 3, decor.qc(C.TEXT_COLOR))

    @staticmethod
    def _focus_ring(p: QPainter, rect: QRectF, radius: float, ink) -> None:
        p.setPen(QPen(ink, FOCUS_PEN, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        r = max(radius - 3, 1.0)
        p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), r, r)


class NoteCell(QWidget):
    """格子里空出的一格：一个「i」和一句说明（NOTE），居中。用系统标题栏的皮肤是暗色的系统图标「信息」和暗色的字；自己画
    外框的皮肤写在深灰面板上，垫一块底板（ChromeLook 的 PLATE_*：面板上的斜带、小图案不压字），「i」是标签颜色的圆标；
    霓虹贴纸、白色海景房直接写在窗口底上（底上的网点、光晕不铺到格子这一块），「i」是信息色的圆标（贴纸是实心圆加黑圈，
    浅色是淡底细圈），字是 SOFT_COLOR。"""

    def __init__(self, size: QSize, parent=None):
        super().__init__(parent)
        self._chrome = _custom()
        self._style = theme.CHROME.STYLE if _styled() else ""
        self.setFixedSize(size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def text(self) -> str:
        return NOTE

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(C.FONT_S)
        font.setBold(self._chrome and not self._style)
        return font

    def _paint_info(self, p: QPainter, rect: QRectF) -> None:
        """霓虹贴纸、白色海景房的「i」：信息色（白色海景房取识别那一组的灯点颜色，霓虹贴纸取链接色）的圆标。"""
        k = theme.CHROME
        info = (k.LED_COLORS or {}).get("recog") or C.LINK_COLOR
        circle = decor.capsule(rect)
        if self._style == "sticker":
            p.fillPath(circle, decor.qc(info))
            p.strokePath(circle, QPen(decor.qc(k.INK), 2))
            glyph = k.INK
        else:
            tint = decor.qc(info)
            tint.setAlpha(36)
            p.fillPath(circle, tint)
            p.strokePath(circle, QPen(decor.qc(info), 1.4))
            glyph = info
        decor.outlined_text(p, rect, "i", theme.title_font(14), glyph)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = self._font()
        fm = QFontMetricsF(font)
        text_w, text_h = fm.horizontalAdvance(NOTE), fm.height()
        icon = INFO_PX + 4
        block_h = icon + 8 + text_h
        cx, top = self.width() / 2, (self.height() - block_h) / 2
        if self._style:
            self._paint_info(p, QRectF(cx - icon / 2, top, icon, icon))
            ink = decor.qc(C.SOFT_COLOR)
        elif self._chrome and theme.CHROME.CURRENT_MARK == "chip":  # 碳纤流光：直接写在窗口底上，「信息」图标是 HALO 的颜色
            if not draw_glyph(p, QRectF(cx - icon / 2, top, icon, icon), ICON_ABOUT, INFO_PX,
                              theme.CHROME.HALO or L.DIM_COLOR):
                top -= (icon + 8) / 2  # 没有图标字体时只写字
            ink = color(L.DIM_COLOR)
        elif self._chrome:
            k = theme.CHROME
            pad_l, pad_t, pad_r, pad_b = k.PLATE_PADDING
            plate = QRectF(cx - text_w / 2 - pad_l, top - pad_t, text_w + pad_l + pad_r, block_h + pad_t + pad_b)
            p.setPen(Qt.NoPen)
            p.setBrush(decor.qc(k.PLATE_FILL))
            p.drawRoundedRect(plate, k.PLATE_RADIUS, k.PLATE_RADIUS)
            decor.badge(p, QPointF(cx, top + icon / 2), icon / 2, decor.colors_of(k.TAG_FILL)[-1], "i", k.TAG_TEXT,
                        theme.title_font(14))
            ink = decor.qc(theme.ink(C.SOFT_COLOR))
        else:
            if not draw_glyph(p, QRectF(cx - icon / 2, top, icon, icon), ICON_ABOUT, INFO_PX, L.DIM_COLOR):
                top -= (icon + 8) / 2  # 没有图标字体时只写字
            ink = color(L.DIM_COLOR)
        p.setFont(font)
        p.setPen(ink)
        p.drawText(QRectF(0, top + icon + 8, self.width(), text_h), Qt.AlignHCenter | Qt.AlignVCenter, NOTE)
        p.end()


class HintText(QWidget):
    """自己画外框的皮肤：大标题右边的一句说明，写在窗口底色上（白字黑描边，同大标题的颜色 TAG_TEXT、TAG_OUTLINE；正文字体
    加粗）。"""

    PX = 13

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def _font(self) -> QFont:
        font = QFont(theme.family("body"))
        font.setPixelSize(self.PX)
        font.setBold(True)
        return font

    def sizeHint(self) -> QSize:
        return QSize(round(QFontMetricsF(self._font()).horizontalAdvance(self._text) + 12), self.PX + 16)

    def paintEvent(self, event) -> None:
        k = theme.CHROME
        p = QPainter(self)
        decor.outlined_text(p, QRectF(self.rect()).adjusted(4, 0, 0, 0), self._text, self._font(), k.TAG_TEXT,
                            k.TAG_OUTLINE, 3.0, align=Qt.AlignLeft)
        p.end()


# ------------------------------------------------------------ 窗口


class SkinWindow(SkinnedWindow, chrome.HideNotice, QDialog):
    """「拾星助手皮肤」窗口。previews 为预览图（skin_preview.PreviewStore，None 时格子里一律画主色）；host 为悬浮窗（点格子时
    调用它的 set_skin(皮肤名)：换皮肤、存进设置），None 时只显示、点了不做事。换皮肤时（theme.set_skin）当场按新皮肤重建
    内容（apply_skin），左上角不动。"""

    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）
    LAYOUTS = {"plain": "_build_plain", "custom": "_build_custom"}  # 两种样子：用系统标题栏 / 自己画外框（_build 里按皮肤选）

    def __init__(self, previews=None, host=None, parent=None):
        super().__init__(parent)
        self._previews = previews
        self._host = host
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(self.FLAGS)
        self.tiles: dict[str, SkinTile] = {}
        self.note: NoteCell | None = None
        self.title: QWidget | None = None  # 大标题、说明
        self.hint: QWidget | None = None
        self.back = None  # 左上角「返回」（外框为 back 的皮肤）
        self.close_btn = None  # 窗口里的关闭按钮（「返回」或右上角 ×）；用系统标题栏时为 None（关闭用系统的 ×）
        self.scroll: QScrollArea | None = None
        self.content: QWidget | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._build()
        if previews is not None:
            previews.drawn.connect(self._preview_drawn)
        self.subscribe_skin()

    def layout_name(self) -> str:
        """现在用的样子：自己画外框的皮肤按 custom，用系统标题栏的按 plain（不按 theme.Layouts 分，只有这两种）。"""
        return "custom" if _custom() else "plain"

    # ---- 搭界面

    def _build(self) -> None:
        """按当前皮肤搭窗口的内容：样式表（同设置窗口，theme.settings_style）、标题和格子；自己画外框的皮肤去掉系统标题栏
        （chrome.frameless），用系统标题栏的皮肤换回来。再量窗口的大小（fit_size）。"""
        self.setStyleSheet(theme.settings_style())
        self.back = self.close_btn = None
        custom = _custom()
        self.layout_key = self.layout_name()
        self.content = self._build_custom() if custom else self._build_plain()
        self.layout().addWidget(self.content)
        self._mark_current()
        self._load_images()
        chrome.frameless(self, custom, self.FLAGS)
        self.fit_size()

    def _build_plain(self) -> QWidget:
        """用系统标题栏的皮肤（默认皮肤）：照设置窗口顶部的样子写大标题和说明（左边选中色方块里的「画笔」图标），下面是格子。"""
        page = QFrame()
        page.setObjectName("page")
        box = QVBoxLayout(page)
        box.setContentsMargins(*L.PAGE_MARGINS)
        box.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(GlyphBox(ICON_SKIN, head=True))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = TextLabel(PAGE_TITLE, "pageTitle", wrap=False)
        self.hint = TextLabel(HINT, "subtitle", wrap=False)
        titles.addWidget(self.title)
        titles.addWidget(self.hint)
        head.addLayout(titles, 1)
        box.addLayout(head)
        self.scroll = self._grid(GAP)
        box.addWidget(self.scroll, 0, Qt.AlignLeft | Qt.AlignTop)
        box.addStretch(1)
        return page

    def _build_custom(self) -> QWidget:
        """自己画外框的皮肤：窗口底色上写大标题和说明，下面深灰面板里是格子。外框为 back 时左上角「返回」，标题和说明跟在
        右边；外框为 close（右上角 ×）时顶上一行照这套皮肤的样子（标题页签或通栏、×，chrome.WindowHead），说明写在下面
        （霓虹贴纸、白色海景房、碳纤流光的说明跟在标题后面，格子不垫面板）；别的外框标题在左、× 在右上角
        （chrome.CloseButton）。"""
        frame = chrome.Frame()
        kind = theme.current().layouts.CHROME
        styled = _styled() or theme.CHROME.CURRENT_MARK == "chip"  # 碳纤流光（「使用中」写在名称一行）同样这么排
        if styled and kind == "close":
            # 霓虹贴纸、白色海景房、碳纤流光：说明跟在顶上一行的标题后面（同设置窗口），格子直接放在窗口底上（不另垫深灰面板）
            root = QVBoxLayout(frame)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            head = chrome.WindowHead(PAGE_TITLE)
            self.title, self.close_btn = head.title, head.closer
            self.hint = head.add(TextLabel(HINT, "pageSubtitle", wrap=False))
            root.addWidget(head)
            box = QVBoxLayout()
            box.setContentsMargins(22, 2, 22, 22)
            self.scroll = self._grid(CHROME_GAP)
            self.scroll.setStyleSheet("QScrollArea, QWidget#qt_scrollarea_viewport "
                                      "{ background: transparent; border: none; }")
            box.addWidget(self.scroll, 0, Qt.AlignLeft | Qt.AlignTop)
            box.addStretch(1)
            root.addLayout(box, 1)
            return frame
        self.hint = HintText(HINT)
        if kind == "close":
            root = QVBoxLayout(frame)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            head = chrome.WindowHead(PAGE_TITLE)
            self.title, self.close_btn = head.title, head.closer
            root.addWidget(head)
            box = QVBoxLayout()
            box.setContentsMargins(18, 6, 18, 18)
            box.setSpacing(8)
            box.addWidget(self.hint, 0, Qt.AlignLeft)
            root.addLayout(box, 1)
        else:
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 8, 18, 18)
            box.setSpacing(8)
            head = QHBoxLayout()
            head.setSpacing(12)
            if kind == "back":
                self.back = self.close_btn = chrome.BackButton()
                head.addWidget(self.back)
            else:
                head.addSpacing(6)
            self.title = chrome.TitleText(PAGE_TITLE)
            head.addWidget(self.title, 0, Qt.AlignVCenter)
            head.addWidget(self.hint, 0, Qt.AlignVCenter)
            head.addStretch(1)
            if self.back is None:
                self.close_btn = chrome.CloseButton()
                head.addWidget(self.close_btn, 0, Qt.AlignTop)
            box.addLayout(head)
        panel = chrome.Panel(seed=17)
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(22, 20, 22, 22)
        self.scroll = self._grid(CHROME_GAP)
        self.scroll.setStyleSheet("QScrollArea, QWidget#qt_scrollarea_viewport { background: transparent; border: none; }")
        inner.addWidget(self.scroll, 0, Qt.AlignLeft | Qt.AlignTop)
        inner.addStretch(1)
        box.addWidget(panel, 1)
        return frame

    def _grid(self, gap: int) -> QScrollArea:
        """格子：登记的各套皮肤按 theme.skins() 的先后每行 COLUMNS 格，最后一格之后写说明（NoteCell）。放在滚动区里
        （fit_size 决定要不要滚动）。"""
        area = QScrollArea()
        area.setObjectName("skinScroll")
        area.setFrameShape(QFrame.NoFrame)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("skinGrid")
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(gap)
        grid.setVerticalSpacing(gap)
        self.tiles = {}
        skins = theme.skins()
        tile = None
        for i, skin in enumerate(skins):
            tile = SkinTile(skin)
            if not tile.locked:  # 还没解锁的格子点了没反应
                tile.clicked.connect(lambda _=False, key=skin.key: self._choose(key))
            grid.addWidget(tile, i // COLUMNS, i % COLUMNS)
            self.tiles[skin.key] = tile
        self.note = NoteCell(tile.size() if tile is not None else QSize(TILE_W, PAD + THUMB_H + CAPTION_H))
        grid.addWidget(self.note, len(skins) // COLUMNS, len(skins) % COLUMNS)
        for column in range(COLUMNS):
            grid.setColumnMinimumWidth(column, TILE_W)
        area.setWidget(body)
        body.setAutoFillBackground(False)  # 滚动区会让放进去的部件铺底色：不铺，透出滚动区（或面板）的底色
        return area

    # ---- 显示的内容

    def _mark_current(self) -> None:
        current = theme.current_key()
        for key, tile in self.tiles.items():
            tile.set_current(key == current)

    def _load_images(self) -> None:
        for key, tile in self.tiles.items():
            usable = self._previews is not None and not tile.locked
            tile.set_image(self._previews.image(key) if usable else None)

    def _preview_drawn(self, key: str) -> None:
        """一张预览图画好了：换上（窗口关着时也换，下次打开就是新的）。还没解锁的格子不换（一直是剪影）。"""
        tile = self.tiles.get(key)
        if tile is not None and self._previews is not None and not tile.locked:
            tile.set_image(self._previews.image(key))

    def refresh_unlock(self) -> None:
        """皮肤解锁了一套（悬浮窗调用）：格子按新的解锁情况重建（同换皮肤，左上角不动）。"""
        if any(tile.locked == unlock.skin_open(key) for key, tile in self.tiles.items()):
            self.apply_skin()

    def current_key(self) -> str | None:
        """标着「使用中」的那一格的皮肤名。"""
        return next((key for key, tile in self.tiles.items() if tile.is_current), None)

    # ---- 操作

    def _choose(self, key: str) -> None:
        """点了一格：请悬浮窗换成这套（存进设置）。和当前相同时悬浮窗什么都不做。"""
        fn = getattr(self._host, "set_skin", None)
        if callable(fn):
            fn(key)

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：按新皮肤重建内容（格子、标记、外框），窗口的左上角不动；新的样子更大、会超出屏幕
        时挪回屏幕里（_place）。旧的内容在事件循环里删掉：换皮肤正是旧内容里的格子发起的。"""
        visible = self.isVisible()
        corner = self.frameGeometry().topLeft()
        old = self.content
        if old is not None:
            self.layout().removeWidget(old)
            old.hide()
            old.deleteLater()
        self._build()
        if visible:
            if not self.isVisible():
                self.show()
            self._place(corner)
            # 系统换外框以后边框的宽度有时过一会儿才更新（离屏平台），回到事件循环再对一次
            QTimer.singleShot(0, self, lambda c=corner: self._place(c, settle=True))

    def _place(self, corner: QPoint, settle: bool = False) -> None:
        """把窗口外框的左上角放到 corner。换外框以后部件记的边框宽度还是旧的（move 会差一个边框），所以按现在的外框算出
        内容区的位置再放：自己画外框没有边框，用系统标题栏时用系统给的边框宽度。新皮肤的窗口更大、放在 corner 会超出所在
        屏幕的可用区域时往回挪（_inside）。settle：已经在该在的地方时不动。"""
        if not self.isVisible():
            return
        handle = self.windowHandle()
        framed = not (self.windowFlags() & Qt.FramelessWindowHint)
        margins = handle.frameMargins() if framed and handle is not None else QMargins()
        corner = self._inside(corner, margins)
        if settle and self.frameGeometry().topLeft() == corner:
            return
        self.setGeometry(corner.x() + margins.left(), corner.y() + margins.top(), self.width(), self.height())

    def _area(self, corner: QPoint):
        """corner 所在屏幕的可用区域（不含任务栏）；找不到屏幕时为 None。"""
        screen = QGuiApplication.screenAt(corner) or self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _inside(self, corner: QPoint, margins: QMargins) -> QPoint:
        """外框的左上角尽量留在 corner；窗口（连外框）放在那里超出屏幕可用区域的右边、下边时往左、往上挪回来，放不下时
        左上角对齐可用区域。"""
        area = self._area(corner)
        if area is None or area.isEmpty():
            return corner
        width = self.width() + margins.left() + margins.right()
        height = self.height() + margins.top() + margins.bottom()
        x = max(area.left(), min(corner.x(), area.left() + area.width() - width))
        y = max(area.top(), min(corner.y(), area.top() + area.height() - height))
        return QPoint(x, y)

    def keep_on_screen(self) -> None:
        """接回窗口位置时用：按现在的窗口（连外框）挪进可用区域（同换皮肤时的 _place）。"""
        if self.isVisible():
            self._place(self.frameGeometry().topLeft())

    # ---- 尺寸

    def max_height(self) -> int:
        """窗口最高多高：所在屏幕（还没显示时是主屏）的可用高度；用系统标题栏时再减去设置窗口留给标题栏的高度
        （SettingsLook.SCREEN_MARGIN）。取不到屏幕时为 0（不限）。"""
        return self.screen_height_limit(0 if _custom() else L.SCREEN_MARGIN)

    def fit_size(self) -> None:
        """窗口的大小按内容定：格子全部放得下时不滚动；超过 max_height 时窗口高度按它，格子在窗口里上下滚动（滚动条另占
        一点宽度，最右边一列不被遮住）。"""
        self.ensurePolished()
        area = self.scroll
        grid = area.widget().layout()
        grid.invalidate()
        natural = grid.totalSizeHint()
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setFixedSize(natural)
        outer = self.layout()
        for nested in self.content.findChildren(QLayout):
            nested.invalidate()
        outer.invalidate()
        need = outer.totalSizeHint()
        limit = self.max_height()
        if 0 < limit < need.height():
            area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            area.setFixedSize(natural.width() + L.SCROLL_WIDTH + 6, max(natural.height() - (need.height() - limit), 80))
            for nested in self.content.findChildren(QLayout):
                nested.invalidate()
            outer.invalidate()
            need = QSize(outer.totalSizeHint().width(), limit)
        self.setFixedSize(need)

    def scrolls(self) -> bool:
        """格子放不下、在窗口里滚动。"""
        return self.scroll is not None and self.scroll.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn

    def showEvent(self, event) -> None:
        # 显示时窗口已放到所在的屏幕上，按那块屏幕的可用高度再量一次
        self.fit_size()
        super().showEvent(event)


lang.localize(globals())
