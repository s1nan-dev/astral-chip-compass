"""推荐卡片里自己画的选项块部件：经典主题-黑和反色的圆形筹码图标、描边的筹码名、斜角标，霓虹贴纸和白色海景房的描边位次、
小卡片，碳纤流光的记分板（一行、位次牌、筹码名）。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ... import paths as app_paths
from .. import names, theme
from ..glyphs import draw_glyph, glyph_font
from ..theme import COMMON as C
from ..theme import OVERLAY as O


def _by_scheme(ev) -> bool:
    """这次推荐是按自己的方案计算的（Advice.ranking：卡片上写位次、照方案的先后排）。"""
    return getattr(getattr(ev, "advice", None), "ranking", None) is not None


# ------------------------------------------------------------ 经典主题-黑、反色的推荐（选项块里的几个部件）

REROLL_ICON = 0xE72C  # 刷新的圆钮里画的系统图标（循环箭头）


class _AdviceBox(QWidget):
    """推荐垫的底（OverlayLook.ADVICE_BOX 为 indigo：靛紫渐变铺网点，同游戏「查看筹码」面板，ChromeLook.INDIGO）。"""

    def paintEvent(self, event) -> None:
        from .. import decor

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p = QPainter(self)
        p.drawPixmap(0, 0, decor.cached(self, ("advice", theme.current_key()), self.size(),
                                        lambda q: decor.indigo_box(q, rect, theme.CHROME, 18)))
        p.end()


class _ChipIcon(QWidget):
    """选项块左边游戏里的圆形筹码图标（OverlayLook.OPTION_ICON 为直径）：稀有度底板加筹码图案（widgets.chip_icon，和「已有
    筹码」的图标格子是同一套图片，第一次启动时下载）；图片还没下载好时画稀有度颜色的圆圈和筹码名的首字。首推的图标外面一圈光、
    右上角贴一枚星星（BEST_BADGE 为 star）。刷新是一个圆钮（ChromeLook.REROLL_ICON）里刷新的系统图标。四周留出光圈、星星的地方。"""

    MARGIN = 8

    def __init__(self, chip, reroll: bool, best: bool, size: int):
        super().__init__()
        self._chip, self._reroll, self._best, self._size = chip, reroll, best, size
        self.setFixedSize(size + 2 * self.MARGIN, size + 2 * self.MARGIN)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def _ready(self) -> bool:
        """这个筹码的图片（稀有度底板、图案）已经下载好了。"""
        from ..widgets import RARITY_BASE

        chip = self._chip
        if chip is None or chip.rarity not in RARITY_BASE or not chip.icon:
            return False
        folder = app_paths.download_dir("chips")
        return (folder / RARITY_BASE[chip.rarity]).exists() and (folder / f"UT_Relic_{chip.icon}.png").exists()

    def paintEvent(self, event) -> None:
        from .. import decor
        from ..widgets import W, chip_icon, qcolor

        look = theme.CHROME
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        m, size = self.MARGIN, self._size
        rect = QRectF(m, m, size, size)
        center = rect.center()
        if self._reroll:
            fill, ring = look.REROLL_ICON
            p.setPen(QPen(decor.qc(ring), 3))
            p.setBrush(decor.qc(fill))
            p.drawEllipse(center, size / 2 - 4, size / 2 - 4)
            draw_glyph(p, rect, REROLL_ICON, round(size * 0.4), decor.qc(ring))
            p.end()
            return
        best_star = self._best and O.BEST_BADGE == "star"
        if best_star:
            decor.glow_ring(p, center, size / 2, look.GLOW)
        chip = self._chip
        if self._ready():
            dpr = max(1.0, self.devicePixelRatioF())
            p.drawImage(rect, chip_icon(chip.name, chip.rarity, chip.icon, round(size * dpr)))
        else:  # 图片还没下载好：稀有度颜色的圆圈、筹码名的首字
            ring = C.RARITY_COLOR.get(chip.rarity, W.ICON_FALLBACK_RING) if chip is not None else W.ICON_FALLBACK_RING
            p.setPen(QPen(qcolor(ring), max(2.0, size / 14)))
            p.setBrush(qcolor(W.ICON_FALLBACK_BG))
            p.drawEllipse(center, size / 2 - 2, size / 2 - 2)
            if chip is not None:
                font = QFont(self.font())
                font.setPixelSize(max(6, round(size * 0.42)))
                font.setBold(True)
                p.setFont(font)
                p.setPen(qcolor(W.ICON_FALLBACK_TEXT))
                p.drawText(rect, Qt.AlignCenter, names.chip_name(chip)[:1])
        if best_star:
            decor.star_sticker(p, QPointF(rect.right() - 3, rect.top() + 3), 10, look)
        p.end()


class _GameName(QPushButton):
    """经典主题-黑、反色、霓虹贴纸、白色海景房选项块里的筹码名（OverlayLook.NAME_STYLE）：outline 白字黑描边（标题字体）；
    bar 深色字（标题字体），下面一根稀有度颜色的短条（同游戏结算界面每行下面的条）；capsule 稀有度颜色的胶囊、粗黑描边加
    实心投影、深色字；tinted 稀有度的浅色底（NAME_FILLS）、稀有度颜色的字。clickable：三选一显示期间点了改正这个候选
    筹码（同默认的筹码名按钮，鼠标移上去字变色）；回看上一次推荐时不能点。悬停说明由调用的地方设。"""

    BAR = 5  # 短条的高
    BAR_MIN = 60  # 短条至少这么长

    def __init__(self, text: str, rarity_color: str, point_size: float, clickable: bool = True):
        super().__init__(text)
        self._color, self._pt, self._clickable = rarity_color, point_size, clickable
        self.setFlat(True)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setFocusPolicy(Qt.NoFocus)

    def _font(self):
        if O.NAME_STYLE in ("capsule", "tinted"):  # 胶囊里的名字：首推大一号
            return theme.title_font(16 if self._pt >= C.FONT_XL else 15)
        return theme.title_font(self._pt * 96 / 72 + 1)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font())
        width = fm.horizontalAdvance(self.text()) + 6
        if O.NAME_STYLE == "bar":
            return QSize(math.ceil(max(width, self.BAR_MIN + 2)), math.ceil(fm.height() + self.BAR + 4))
        if O.NAME_STYLE == "capsule":  # 胶囊另留右下的实心投影
            return QSize(math.ceil(width + 20 + 3), math.ceil(fm.height() + 8 + 3))
        if O.NAME_STYLE == "tinted":
            return QSize(math.ceil(width + 18), math.ceil(fm.height() + 8))
        return QSize(math.ceil(width + 4), math.ceil(fm.height() + 2))

    def _rarity(self) -> str:
        """名字的颜色是哪一种稀有度的（按当前皮肤的 RARITY_COLOR 反查）；不是稀有度颜色时为空。"""
        return next((r for r, c in C.RARITY_COLOR.items() if c.lower() == str(self._color).lower()), "")

    def _paint_capsule(self, p: QPainter, hover: bool) -> None:
        """霓虹贴纸（capsule）：稀有度颜色的胶囊、粗黑描边、右下实心投影，深色字；白色海景房（tinted）：稀有度的浅色底、
        稀有度颜色的字，首推的星星用 STAR_FILL。鼠标移上去（能点时）底色亮一点。"""
        from .. import decor

        look = theme.CHROME
        font = self._font()
        text = self.text()
        if O.NAME_STYLE == "capsule":
            body = QRectF(1, 1, self.width() - 5, self.height() - 5)
            fill = decor.qc(self._color)
            decor.sticker(p, decor.capsule(body), fill.lighter(112) if hover else fill, look, 2.2, (3, 3), body)
            decor.outlined_text(p, body, text, font, look.INK)
            return
        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        fill = decor.qc(O.NAME_FILLS.get(self._rarity()) or C.SELECTION_BG)
        p.fillPath(decor.capsule(body), fill.darker(104) if hover else fill)
        star = "★ "
        x = body.left() + 9
        if text.startswith(star):
            width = QFontMetricsF(font).horizontalAdvance(star)
            decor.outlined_text(p, QRectF(x, body.top(), width, body.height()), star.strip(), font, look.STAR_FILL,
                                align=Qt.AlignLeft)
            x += width
            text = text[len(star):]
        decor.outlined_text(p, QRectF(x, body.top(), body.right() - x, body.height()), text, font, self._color,
                            align=Qt.AlignLeft)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        from .. import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = self._font()
        fm = QFontMetricsF(font)
        hover = self._clickable and (self.underMouse() or self.isDown())
        if O.NAME_STYLE in ("capsule", "tinted"):
            self._paint_capsule(p, hover)
        elif O.NAME_STYLE == "bar":
            text_rect = QRectF(1, 0, self.width() - 1, fm.height())
            decor.outlined_text(p, text_rect, self.text(), font, C.MAP_COLOR if hover else C.TEXT_COLOR,
                                align=Qt.AlignLeft)
            bar = QRectF(1, fm.height() + 2, max(fm.horizontalAdvance(self.text()), self.BAR_MIN), self.BAR)
            p.fillPath(decor.rounded(bar, self.BAR / 2), decor.qc(self._color))
        else:
            decor.outlined_text(p, QRectF(self.rect()).adjusted(3, 0, 0, 0), self.text(), font,
                                C.MAP_COLOR if hover else C.TEXT_COLOR, theme.CHROME.TITLE_OUTLINE,
                                max(3.0, font.pixelSize() * 0.24), align=Qt.AlignLeft)
        if self.hasFocus():
            p.setPen(QPen(decor.qc(C.MAP_COLOR), 1.2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.end()


class _BestBadge(QWidget):
    """首推筹码名后面的斜角标（OverlayLook.BEST_BADGE 为 slant，同游戏结算界面的「BEST」）：橙色平行四边形里一颗白星。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(44, 26)

    def paintEvent(self, event) -> None:
        from .. import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.best_badge(p, QRectF(1, 2, 42, 22), theme.CHROME)
        p.end()


# ------------------------------------------------------------ 霓虹贴纸、白色海景房的推荐（选项块里的几个部件）


class _OutlineLabel(QLabel):
    """霓虹贴纸选项块第一行的位次、推荐度和「刷新」（OverlayLook.MARK_STYLE 为 outline）：描边的标题字，字、描边的颜色和描边宽
    按 MARK_KINDS 这一种（底不画）。text() 照旧是这段文字（找文字的地方、悬停说明照常）。"""

    def __init__(self, text: str, kind: str, px: float):
        super().__init__(text)
        self._kind, self._px = kind, px
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _font(self):
        return theme.title_font(self._px)

    def _spec(self) -> tuple:
        _, ink, line, width = O.MARK_KINDS.get(self._kind) or ("", O.MARK_COLOR, "", 0)
        return ink, line, float(width) if line else 0.0

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font())
        width = self._spec()[2]
        return QSize(math.ceil(fm.horizontalAdvance(self.text()) + width + 4), math.ceil(fm.height() + width / 2))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        from .. import decor

        ink, line, width = self._spec()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.outlined_text(p, QRectF(self.rect()).adjusted(width / 2 + 1, 0, 0, 0), self.text(), self._font(), ink,
                            line or None, width, align=Qt.AlignLeft)
        p.end()


class _EntryCard(QWidget):
    """白色海景房推荐里的一条（OverlayLook.ENTRY_BARS 有写时）：一张小白卡片（细框；首推的下面一圈淡紫的软阴影），左边一根
    这一种的颜色的竖条（ENTRY_BARS：首推是紫粉渐变）。box 是卡片内容的竖排布局。"""

    SHADOW = 4  # 下沿给首推的软阴影留的高

    def __init__(self, kind: str, best: bool):
        super().__init__()
        self._kind, self._best = kind, best
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 9, 10, 9 + self.SHADOW)
        self.box.setSpacing(4)

    def paintEvent(self, event) -> None:
        from .. import decor

        look = theme.CHROME
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1 - self.SHADOW)
        bar = O.ENTRY_BARS.get(self._kind) or O.ENTRY_BARS.get("unmarked") or look.CARD_EDGE

        def draw(q: QPainter) -> None:
            path = decor.rounded(rect, 12)
            if self._best:
                decor.soft_shadow(q, path, 0, 3, 5, look.SOFT_SHADOW)
            q.fillPath(path, decor.qc("#ffffff"))
            q.save()
            q.setClipPath(path)
            q.fillRect(QRectF(rect.left(), rect.top(), 5, rect.height()),
                       decor.fill_brush(bar, QRectF(rect.left(), rect.top(), 5, rect.height()), vertical=True))
            q.restore()
            q.strokePath(path, QPen(decor.qc(look.CARD_EDGE), 1))

        p = QPainter(self)
        p.drawPixmap(0, 0, decor.cached(self, ("entry", theme.current_key(), self._kind, self._best), self.size(), draw))
        p.end()


# ------------------------------------------------------------ 碳纤流光的推荐（记分板：OverlayLook.SCOREBOARD）


class _ScoreRow(QWidget):
    """记分板的一行（碳纤流光）：首推那一行铺一层很淡的底色（OverlayLook.SCORE_BEST_FILL），底下一条七色灯线（ChromeLook.RGB）；
    其余各行不画底。"""

    LINE = 2.0  # 灯线的粗细

    def __init__(self, best: bool):
        super().__init__()
        self._best = best

    def paintEvent(self, event) -> None:
        if not self._best:
            return
        from .. import decor

        look = theme.CHROME
        rect = QRectF(self.rect())
        p = QPainter(self)
        if O.SCORE_BEST_FILL:
            p.fillRect(rect.adjusted(0, 0, 0, -self.LINE), decor.qc(O.SCORE_BEST_FILL))
        if look.RGB:
            line = QRectF(rect.left(), rect.bottom() - self.LINE + 1, rect.width(), self.LINE)
            p.fillRect(line, decor.rgb_brush(line, look.RGB))
        p.end()


class _ScorePlate(QLabel):
    """记分板一行左边的位次牌（碳纤流光）：切角的小牌子（decor.cut_plate），颜色按种类（OverlayLook.MARK_KINDS：底, 字, 框线,
    框线宽），首推外面一圈光（ChromeLook.HALO）；「刷新」前面画刷新的系统图标（没有图标字体时只写字），推荐度用数字字体的窄体。
    text() 照旧是这段文字（找文字的地方、悬停说明照常）。宽 SCORE_PLATE_W。"""

    HEIGHT = 30
    PAD = 4  # 四周给光留的地方

    def __init__(self, text: str, kind: str, reroll: bool = False, number: bool = False):
        super().__init__(text)
        self._kind, self._reroll, self._number = kind, reroll, number
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
        self.setFixedSize(O.SCORE_PLATE_W, self.HEIGHT)

    def kind(self) -> str:
        return self._kind

    def _font(self) -> QFont:
        if self._number:
            from ..chrome import index_font

            return index_font(17)
        font = QFont(self.font())
        font.setPointSizeF(C.FONT_M)
        font.setBold(True)
        return font

    def paintEvent(self, event) -> None:
        from .. import decor

        fill, ink, line, width = O.MARK_KINDS.get(self._kind) or (O.MARK_BG or C.POPUP_BG, O.MARK_COLOR, "", 0)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        decor.cut_plate(p, body, fill, line, width, theme.CHROME.HALO if self._kind == "best" else None)
        font = self._font()
        text_w = QFontMetricsF(font).horizontalAdvance(self.text())
        icon = 13 if self._reroll and glyph_font(13) is not None else 0
        x = body.center().x() - (text_w + (icon + 4 if icon else 0)) / 2
        if icon:
            draw_glyph(p, QRectF(x, body.top(), icon, body.height()), REROLL_ICON, icon, decor.qc(ink))
            x += icon + 4
        decor.flat_text(p, QRectF(x, body.top(), body.right() - x, body.height()), self.text(), font, ink)
        p.end()


class _ScoreName(_GameName):
    """记分板一行右边的筹码名（碳纤流光）：斜体粗体（标题字体），稀有度颜色；首推前面一颗星（ChromeLook.STAR_FILL）。三选一
    显示期间点了改正、回看上一次推荐时不能点，同 _GameName。text() 是筹码名（不含星）。"""

    def __init__(self, text: str, rarity_color: str, point_size: float, best: bool, clickable: bool = True):
        super().__init__(text, rarity_color, point_size, clickable)
        self._best = best

    def _font(self):
        font = theme.title_font(self._pt * 96 / 72)
        font.setItalic(True)
        return font

    def _star(self) -> float:
        return QFontMetricsF(self._font()).ascent() * 0.95 if self._best else 0.0

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font())
        star = self._star()
        return QSize(math.ceil(fm.horizontalAdvance(self.text()) + (star + 6 if star else 0) + 8), math.ceil(fm.height() + 2))

    def paintEvent(self, event) -> None:
        from .. import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self._clickable and (self.underMouse() or self.isDown())
        x, star = 1.0, self._star()
        if star:
            cy = self.height() / 2
            p.fillPath(decor.star_path(x + star / 2, cy + 0.5, star / 2, 0.48), decor.qc(theme.CHROME.STAR_FILL))
            x += star + 6
        decor.flat_text(p, QRectF(x, 0, self.width() - x, self.height()), self.text(), self._font(),
                        C.MAP_COLOR if hover else self._color)
        if self.hasFocus():
            p.setPen(QPen(decor.qc(C.MAP_COLOR), 1.2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()
