"""悬浮窗的几种部件：自动换行的流式布局、胶囊标签、带流派标签的效果说明、筹码图标格子、地图下拉胶囊、滚轮不切换选项的
下拉框、下拉列表每一行的画法和不能选的分组标题（角色下拉框按六类分组）、卡片和卡片的折叠按钮、分隔线。

只管外观和尺寸，不含业务逻辑；显示什么文字、用什么颜色由 overlay.py 和 text.py 决定。
Qt 的富文本不支持圆角，所以胶囊标签自己画：文字用 QTextLayout 排版（中文按字换行），放不下时在标签内换行。
"""
from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStylePainter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from shiboken6 import delete, getCppPointer, isValid

from ..data import heroes_in_group_order
from ..paths import download_dir  # 筹码图标是下载的识别图片（发行版在个人目录里）；每次用到时才取文件夹
from . import decor, glyphs, theme
from .text import keep_words, plain_words
from .theme import COMMON as C
from .theme import SETTINGS as L
from .theme import WIDGETS as W

# 颜色、尺寸都在皮肤里（theme.WidgetLook：胶囊的色调 TONES、内边距、下拉列表每一行 COMBO_*、折叠按钮、卡片的边距等），
# 画的时候按当前皮肤取（W.名字）。TONES、HOVER_FILL、COMBO_HOT_FILL 等名字也能按本模块的属性取，见 __getattr__。


# 皮肤颜色的解析只有一处：decor.qc（认 "#rrggbb"、"rgba(r,g,b,a)"、元组、QColor）。这里再导出成 qcolor 给调用处和测试。
qcolor = decor.qc
# 同一个解析，各窗口按 color 这个名字取（settings.color 也再导出它）。
color = decor.qc


# ------------------------------------------------------------ 纯文字标签、系统图标方块
# 放在这里而不放在 settings：底层的部件不反过来依赖设置窗口。settings 再导出这两个名字（见那里）。


class TextLabel(QLabel):
    """纯文字标签：自动换行时名称、术语和常用双字词不从中间拆开（text.keep_words），text() 返回原文；不接鼠标（点在字上
    等于点在它所在的方块、整行上）。"""

    def __init__(self, text: str = "", name: str = "", wrap: bool = True):
        super().__init__()
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if name:
            self.setObjectName(name)
        self.setWordWrap(wrap)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802  Qt 的方法名
        super().setText(keep_words(text) if self.wordWrap() else text)

    def text(self) -> str:
        return plain_words(super().text())


class GlyphBox(QWidget):
    """一个系统图标：圆角方块里居中画图标。head 为假时是每行左边的图标（皮肤的 ICON_BOX、ICON_PX、ICON_RADIUS，图标
    ICON_COLOR、方块 ICON_BG），为真时是窗口顶部大标题旁的那个（HEAD_ICON_*，金色方块里画底色的图标）。系统里没有图标字体时
    不显示（不占地方）。"""

    def __init__(self, code: int, head: bool = False):
        super().__init__()
        self.code, self.head = code, head
        box = L.HEAD_ICON_BOX if head else L.ICON_BOX
        self.setFixedSize(box, box)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if glyphs.glyph_font(self.px) is None:
            self.hide()  # 只在没有图标字体时隐藏（还没放进窗口的部件不能 show，否则会单独弹出来）

    @property
    def px(self) -> int:
        return L.HEAD_ICON_PX if self.head else L.ICON_PX

    def paintEvent(self, event) -> None:
        if self.head:
            fill, bg, radius = L.WINDOW_BG, L.SELECTED_COLOR, L.HEAD_ICON_RADIUS
        else:
            fill, bg, radius = L.ICON_COLOR, L.ICON_BG, L.ICON_RADIUS
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if bg:
            p.setPen(Qt.NoPen)
            p.setBrush(color(bg))
            p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        glyphs.draw_glyph(p, QRectF(self.rect()), self.code, self.px, fill)
        p.end()


def free_item(item) -> None:
    """把从布局里取出的条目（QLayoutItem）在 Qt 那边也删掉：QLayout.takeAt 只是把条目交给调用方，不删就一直留在 C++
    那边（FlowLayout 反复重搭推荐区，条目只涨不降）。部件的条目只删条目、不删部件；空白（addSpacing、addStretch）的
    条目同样删掉。取出的子布局（item.layout()）不在这里删：取出以后归 Python 管，没人再拿着时自动释放，别处还拿着它时
    删了反而出错。"""
    if item is None or not isValid(item):
        return
    if item.widget() is not None or item.spacerItem() is not None:
        delete(item)


def _release_items(items) -> None:
    """把布局里还没取走的条目逐个释放（布局析构时用；不能是绑在布局上的方法，那时布局已不完整）。"""
    while items:
        free_item(items.pop())


def __getattr__(name: str):
    """按外观常量名（TONES、HOVER_FILL、COMBO_HOT_FILL 等）取当前皮肤的值，给测试和预览工具用。TONES、HOVER_* 给的是
    QColor。"""
    if name == "TONES":
        return {k: _tone(k) for k in W.TONES}
    if name in ("HOVER_FILL", "HOVER_BORDER"):
        return qcolor(getattr(W, name))
    return theme.legacy(__name__, name, ("widgets", "common"))


def _tone(tone: str):
    """胶囊的色调 →（框线, 底色或 None, 默认文字颜色），都是 QColor。不认识的按 ""。"""
    border, fill, text = W.TONES.get(tone, W.TONES[""])
    return qcolor(border), qcolor(fill) if fill is not None else None, qcolor(text)


def _dark_fill(tone: str) -> bool:
    """这个色调的胶囊是不是实心的深色底（里面写的颜色要换成深色底上用的，theme.ink）。"""
    fill = _tone(tone)[1]
    return fill is not None and fill.alpha() > 200 and fill.lightnessF() < 0.5


def _segments(segments) -> list[tuple[str, str | None]]:
    """[(文字, 颜色或 None)]；也接受单独的字符串。空文字去掉。"""
    out = []
    for seg in segments or ():
        if isinstance(seg, str):
            text, color = seg, None
        else:
            text, color = seg[0], (seg[1] if len(seg) > 1 else None)
        if text:
            out.append((str(text), color))
    return out


def _formats(segments, default: QColor, dark: bool = False, table=None) -> list:
    """各段文字的颜色（段里没写颜色的用 default）。dark 为真时是写在深色胶囊里：段里写的颜色换成深色底上用的（theme.ink，
    默认皮肤照原样）。table 给了时（皮肤的 WidgetLook.TONE_INK：亮色、浅色底的胶囊）段里写的颜色按这张表换，不再按 dark。"""
    ranges, pos = [], 0
    for text, color in segments:
        fr = QTextLayout.FormatRange()
        fr.start, fr.length = pos, len(text)
        fmt = QTextCharFormat()
        if table is not None and color:
            fmt.setForeground(QColor(table.get(color, color)))
        else:
            fmt.setForeground(QColor(theme.ink(color) if dark else color) if color else default)
        fr.format = fmt
        ranges.append(fr)
        pos += len(text)
    return ranges


def _tone_ink(tone: str):
    """这个色调的胶囊里各段颜色的换色表（WidgetLook.TONE_INK）；没写时为 None。"""
    return W.TONE_INK.get(tone)


def _badge(tone: str):
    """这个色调的胶囊前面的小圆标（圆里的字, 圆的颜色, 字的颜色）；没有时为 None（默认皮肤都没有）。"""
    return W.TONE_BADGE.get(tone)


def _paint_badge(p: QPainter, tone: str, font: QFont, height: float) -> None:
    """在胶囊左端画小圆标（警告前面的「!」）；皮肤写了 triangle 时画三角形的警告标。"""
    spec = _badge(tone)
    if spec is None:
        return
    glyph, fill, color = spec[:3]
    r = _badge_radius(font)
    if len(spec) > 3 and spec[3] == "triangle":
        from . import decor

        bold = QFont(font)
        bold.setBold(True)
        bold.setPointSizeF(max(6.0, font.pointSizeF() * 0.8))
        decor.warn_triangle(p, QPointF(W.PILL_PAD_X + 1 + r + 2, height / 2), 2 * r + 2, fill, color, bold)
        return
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(qcolor(fill))
    center = QPointF(W.PILL_PAD_X + 1 + r, height / 2)
    p.drawEllipse(center, r, r)
    bold = QFont(font)
    bold.setBold(True)
    bold.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
    p.setFont(bold)
    p.setPen(qcolor(color))
    p.drawText(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), Qt.AlignCenter, glyph)
    p.restore()


def _badge_radius(font: QFont) -> float:
    return max(5.0, QFontMetricsF(font).height() * 0.36)


def _badge_room(tone: str, font: QFont) -> int:
    """小圆标占的宽（含它和字之间的空隙）；没有圆标时为 0。三角形的标宽一点。"""
    spec = _badge(tone)
    if spec is None:
        return 0
    return math.ceil(2 * _badge_radius(font) + (9 if len(spec) > 3 and spec[3] == "triangle" else 4))


def _slant_room(tone: str) -> int:
    """平行四边形的胶囊（WidgetLook.TONE_SHAPE 为 slant）两头斜出去的那一截另留的宽；圆角的为 0。"""
    return 10 if W.TONE_SHAPE.get(tone) == "slant" else 0


def shape_text(text: str, formats, font: QFont, first_width: float, width: float, first_x: float = 0.0,
               pitch: float | None = None) -> tuple[QTextLayout, int, float]:
    """排版一段文字：第一行宽 first_width、从 first_x 起，其余行宽 width、从 0 起。
    pitch 为行距（None 时按字体的行高）。返回 (排好的 QTextLayout, 行数, 最后一行底部的 y)。"""
    layout = QTextLayout(text, font)
    layout.setFormats(formats)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    y, n, bottom = 0.0, 0, 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1.0, first_width if n == 0 else width))
        line.setPosition(QPointF(first_x if n == 0 else 0.0, y))
        bottom = y + line.height()
        y += pitch if pitch else line.height()
        n += 1
    layout.endLayout()
    return layout, n, bottom


def natural_width(text: str, formats, font: QFont) -> float:
    """一行排下时文字的宽度。"""
    layout, _, _ = shape_text(text, formats, font, 1e6, 1e6)
    return layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0.0


# ------------------------------------------------------------ 流式布局


class FlowLayout(QLayout):
    """自动换行的流式布局（按 Qt 官方示例 Flow Layout 改写）。

    一行放不下就换到下一行；比整行还宽的部件（长理由的胶囊标签）占满一行，高度按 heightForWidth 算（标签内换行）。
    同一行里高矮不同的部件上下居中。隐藏的部件不占位置。
    """

    def __init__(self, parent=None, hspacing: int = 4, vspacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._hs, self._vs = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)
        # 布局自己被删掉时（父部件销毁、整块重建推荐区）把还没取走的条目在 Qt 那边也释放掉：Qt 的 QLayout 析构只清它自己
        # 记的那份，这里自己存的一份不释放就一直留着。信号接在模块级函数上（接在 self 的方法上时 self 已经不完整）
        self.destroyed.connect(lambda *_, items=self._items: _release_items(items))

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        """理想大小：全部排成一行。"""
        visible = [i for i in self._items if not i.isEmpty()]
        m = self.contentsMargins()
        w = sum(i.sizeHint().width() for i in visible) + self._hs * max(len(visible) - 1, 0)
        h = max((i.sizeHint().height() for i in visible), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        avail = max(area.width(), 1)
        lines: list[list[tuple]] = [[]]
        x = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            w = min(hint.width(), avail)
            if w < hint.width() and item.hasHeightForWidth():
                h = item.heightForWidth(w)
            else:
                h = hint.height()
            if lines[-1] and x + w > avail:
                lines.append([])
                x = 0
            lines[-1].append((item, w, h))
            x += w + self._hs
        y = area.y()
        for line in lines:
            if not line:
                continue
            height = max(h for _, _, h in line)
            if apply:
                lx = area.x()
                for item, w, h in line:
                    item.setGeometry(QRect(QPoint(lx, y + (height - h) // 2), QSize(w, h)))
                    lx += w + self._hs
            y += height + self._vs
        used = y - self._vs - area.y() if any(lines) else 0
        return max(used, 0) + m.top() + m.bottom()

    def clear(self) -> None:
        """移除并删除全部部件（可能正在处理其中某个按钮的点击，所以部件用 deleteLater），条目在 Qt 那边也一并释放。"""
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            free_item(item)
        self.invalidate()

    def widgets(self) -> list[QWidget]:
        return [i.widget() for i in self._items if i.widget() is not None]


# ------------------------------------------------------------ 胶囊标签


class Pill(QWidget):
    """胶囊标签：1 像素框线、全圆角，文字可以分段着色（例如主体暗色、数值亮色）。

    比可用宽度长时在标签内换行，标签变成圆角矩形，不截断。不可点击（可点击的地图胶囊见 MapPill）。
    内边距、圆角、色调的颜色按当前皮肤（theme.WidgetLook 的 PILL_*、TONES）。
    """

    def __init__(self, segments=(), tone: str = "", parent=None, *, point_size: float | None = None):
        super().__init__(parent)
        if point_size:
            font = QFont(self.font())
            font.setPointSizeF(point_size)
            self.setFont(font)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._segments: list = []
        self._tone = ""
        self._cache: dict = {}
        self.set_segments(segments, tone)

    def set_segments(self, segments, tone: str = "") -> None:
        segments = _segments(segments)
        if (segments, tone) == (self._segments, self._tone):
            return
        self._segments = segments
        self._tone = tone
        self._cache = {}
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return "".join(t for t, _ in self._segments)

    @property
    def tone(self) -> str:
        return self._tone

    def set_point_size(self, point_size: float | None) -> None:
        """改字号（换皮肤时：字体、字号按新皮肤）：先回到继承来的字体，再改字号；point_size 为空时就用继承来的字体。"""
        self.setFont(QFont())
        if point_size:
            font = QFont(self.font())
            font.setPointSizeF(point_size)
            self.setFont(font)
        self._cache = {}
        self.updateGeometry()
        self.update()

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._cache = {}
            self.updateGeometry()
        super().changeEvent(event)

    def _fmt(self):
        table = _tone_ink(self._tone)
        if table is not None:  # 皮肤给这个色调写了换色表（亮色、浅色底的胶囊）
            return _formats(self._segments, _tone(self._tone)[2], table=table)
        return _formats(self._segments, _tone(self._tone)[2], dark=_dark_fill(self._tone))

    def _natural(self) -> float:
        if "natural" not in self._cache:
            self._cache["natural"] = natural_width(self.text(), self._fmt(), self.font())
        return self._cache["natural"]

    def _line_height(self) -> int:
        return math.ceil(QFontMetricsF(self.font()).height())

    def _frame(self) -> tuple[int, int]:
        """左右、上下另加的宽和高：内边距、1 像素框线，有小圆标时左边再让出圆标的位置（平行四边形的胶囊两头另留一截）。"""
        return (2 * (W.PILL_PAD_X + 1) + _badge_room(self._tone, self.font()) + _slant_room(self._tone),
                2 * (W.PILL_PAD_Y + 1))

    def _shape(self, text_width: int):
        key = ("shape", text_width)
        if key not in self._cache:
            self._cache[key] = shape_text(self.text(), self._fmt(), self.font(), text_width, text_width)
        return self._cache[key]

    def sizeHint(self) -> QSize:
        fx, fy = self._frame()
        return QSize(math.ceil(self._natural()) + fx, self._line_height() + fy)

    def minimumSizeHint(self) -> QSize:
        fx, fy = self._frame()
        return QSize(min(self.sizeHint().width(), fx + 2 * self._line_height()), self._line_height() + fy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        fx, fy = self._frame()
        text_width = width - fx
        if text_width >= math.ceil(self._natural()):
            return self._line_height() + fy
        _, _, bottom = self._shape(max(text_width, 1))
        return math.ceil(bottom) + fy

    def paintEvent(self, event) -> None:
        border, fill, _ = _tone(self._tone)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = W.TONE_PEN.get(self._tone, 1)
        rect = QRectF(self.rect()).adjusted(pen / 2, pen / 2, -pen / 2, -pen / 2)
        slant = _slant_room(self._tone)
        p.setPen(QPen(border, pen))
        p.setBrush(fill if fill is not None else Qt.NoBrush)
        if slant:  # 平行四边形（经典主题-反色的警告）
            from . import decor

            p.drawPath(decor.slant(rect, min(slant, rect.height() * 0.45)))
        elif W.TONE_SHAPE.get(self._tone) == "cut":  # 切掉左上、右下两个角（碳纤流光）
            from . import decor

            p.drawPath(decor.chamfer(rect, min(6.0, rect.height() * 0.3)))
        else:
            radius = min(W.PILL_RADIUS, rect.height() / 2)
            p.drawRoundedRect(rect, radius, radius)
        fx, fy = self._frame()
        text_width = self.width() - fx
        if text_width >= math.ceil(self._natural()):
            layout, _, bottom = self._shape(math.ceil(self._natural()) + 1)
        else:
            layout, _, bottom = self._shape(max(text_width, 1))
        y = (self.height() - bottom) / 2
        room = _badge_room(self._tone, self.font())
        if room:
            _paint_badge(p, self._tone, self.font(), self.height())
        layout.draw(p, QPointF(W.PILL_PAD_X + 1 + room + slant / 2, y))
        p.end()


class TaggedText(QWidget):
    """筹码小卡片的第二行：开头一个胶囊标签（流派），后面紧跟效果描述；描述不另起一行，长时自然换行，不截断。

    point_size：标签和描述的字号（不写时沿用窗口字体）。标签和描述的间距、标签的内边距按当前皮肤（theme.WidgetLook 的 TAG_*）。
    """

    def __init__(self, tag: str, tag_tone: str, text: str, color: str, parent=None, *,
                 point_size: float | None = None):
        super().__init__(parent)
        if point_size:
            font = QFont(self.font())
            font.setPointSizeF(point_size)
            self.setFont(font)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._tag, self._tag_tone, self._text, self._color = tag, tag_tone, text, color
        self._cache: dict = {}

    def text(self) -> str:
        return f"{self._tag} {self._text}".strip()

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._cache = {}
            self.updateGeometry()
        super().changeEvent(event)

    def _line_height(self) -> int:
        return math.ceil(QFontMetricsF(self.font()).height())

    def _tag_size(self) -> tuple[int, int]:
        if "tag" not in self._cache:
            fmt = _formats([(self._tag, None)], _tone(self._tag_tone)[2])
            w = math.ceil(natural_width(self._tag, fmt, self.font())) + 2 * (W.TAG_PAD_X + 1)
            self._cache["tag"] = (w, self._line_height() + 2 * (W.TAG_PAD_Y + 1))
        return self._cache["tag"]

    def _pitch(self) -> int:
        """行距：第二行要从标签下面开始，各行用同一个行距。"""
        _, tag_h = self._tag_size()
        line = self._line_height()
        y0 = (tag_h - line) / 2
        return math.ceil(max(line + 2, tag_h - y0 + 1))

    def _shape(self, width: int):
        key = ("shape", width)
        if key not in self._cache:
            tag_w, _ = self._tag_size()
            first_x = tag_w + W.TAG_GAP
            fmt = _formats([(self._text, self._color)], QColor(self._color))
            self._cache[key] = shape_text(self._text, fmt, self.font(), width - first_x, width, first_x, self._pitch())
        return self._cache[key]

    def sizeHint(self) -> QSize:
        tag_w, tag_h = self._tag_size()
        fmt = _formats([(self._text, self._color)], QColor(self._color))
        text_w = math.ceil(natural_width(self._text, fmt, self.font())) if self._text else 0
        return QSize(tag_w + (W.TAG_GAP + text_w if text_w else 0), tag_h)

    def minimumSizeHint(self) -> QSize:
        tag_w, tag_h = self._tag_size()
        return QSize(tag_w, tag_h)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        _, tag_h = self._tag_size()
        if not self._text:
            return tag_h
        _, _, bottom = self._shape(max(width, self._tag_size()[0] + W.TAG_GAP + 8))
        y0 = (tag_h - self._line_height()) / 2
        return max(tag_h, math.ceil(y0 + bottom))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tag_w, tag_h = self._tag_size()
        border, fill, color = _tone(self._tag_tone)
        rect = QRectF(0.5, 0.5, tag_w - 1, tag_h - 1)
        p.setPen(QPen(border, 1))
        p.setBrush(fill if fill is not None else Qt.NoBrush)
        radius = min(W.PILL_RADIUS, rect.height() / 2)
        p.drawRoundedRect(rect, radius, radius)
        fmt = _formats([(self._tag, None)], color)
        layout, _, bottom = shape_text(self._tag, fmt, self.font(), 1e6, 1e6)
        layout.draw(p, QPointF(W.TAG_PAD_X + 1, (tag_h - bottom) / 2))
        if self._text:
            text_layout, _, _ = self._shape(max(self.width(), tag_w + W.TAG_GAP + 8))
            text_layout.draw(p, QPointF(0, (tag_h - self._line_height()) / 2))
        p.end()


# ------------------------------------------------------------ 筹码图标


RARITY_BASE = {"蓝": "UT_RelicBase_Blue.png", "紫": "UT_RelicBase_Purple.png", "金": "UT_RelicBase_Orange.png"}
# 底板中间是黑色；游戏「查看筹码」面板里图案下面是稀有度颜色的底色，这里照样补上（颜色量自 wiki 底板的色环，略暗）。
# 这是照游戏画的筹码图标本身，不随皮肤变
RARITY_FILL = {"蓝": QColor(4, 150, 186), "紫": QColor(138, 4, 186), "金": QColor(186, 122, 4)}
ICON_SCALE = 248 / 300  # 图案（248 像素见方）相对底板（300 像素见方）的大小，和面板识别用的比例一致
_icon_cache: dict = {}  # (筹码, 尺寸[, 缺图时用的颜色]) →（图, 画的时候还没有的图片文件）；两张图都画上时不带颜色（各套皮肤同一张图）


def chip_icon(name: str, rarity: str, icon: str, size: int) -> QImage:
    """筹码图标（size 为物理像素）：wiki 的稀有度底板 + 筹码图案，和「查看筹码」面板识别用的是同一套图片。
    没有图片文件时画稀有度颜色的圆圈加筹码名首字（圆圈、底色、首字的颜色按当前皮肤，theme.WidgetLook 的 ICON_FALLBACK_*）。
    结果按 (筹码, 尺寸) 缓存；有一张图没画上的（缺了图片文件：第一次启动、图片还没下载完时；或者文件读不出来、稀有度
    不认识、没有图案编号），画法用了当前皮肤的颜色，这时另按加上这几个颜色的键缓存。再取时先看缺的那几个文件在不在：
    后来下载好了就重画（第一次启动下载完以后，图标重画时就换成真的，不用重启）；图片都在的不再查文件。两张图都画上时
    各套皮肤是同一张图，不按颜色各存一份。"""
    ring = C.RARITY_COLOR.get(rarity, W.ICON_FALLBACK_RING)
    key = (name, rarity, icon, size)
    fallback_key = (*key, ring, W.ICON_FALLBACK_BG, W.ICON_FALLBACK_TEXT)
    for k in (key, fallback_key):
        cached = _icon_cache.get(k)
        if cached is not None:
            img, missing = cached
            if not missing or not any(path.exists() for path in missing):
                return img
    folder = download_dir("chips")
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    color = qcolor(ring)
    fill = RARITY_FILL.get(rarity, QColor(70, 76, 96))
    missing = []  # 画的时候还没有的图片文件（文件在、读不出来的不算：不反复读坏文件）
    base_file = folder / RARITY_BASE[rarity] if rarity in RARITY_BASE else None
    if base_file is not None and not base_file.exists():
        missing.append(base_file)
    base = QImage(str(base_file)) if base_file is not None and base_file not in missing else QImage()
    full = QRectF(0, 0, size, size)
    if not base.isNull():
        p.drawImage(full, base)
        r = size * 0.345  # 底板中间黑色部分的半径（量自 wiki 底板：色环从 0.34 开始）
    else:
        p.setPen(QPen(color, max(1.0, size / 14)))
        p.setBrush(qcolor(W.ICON_FALLBACK_BG))
        p.drawEllipse(full.adjusted(1, 1, -1, -1))
        r = size * 0.40
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawEllipse(QPointF(size / 2, size / 2), r, r)
    pattern_file = folder / f"UT_Relic_{icon}.png" if icon else None
    if pattern_file is not None and not pattern_file.exists():
        missing.append(pattern_file)
    pattern = QImage(str(pattern_file)) if pattern_file is not None and pattern_file not in missing else QImage()
    if not pattern.isNull():
        inner = size * ICON_SCALE
        p.drawImage(QRectF((size - inner) / 2, (size - inner) / 2, inner, inner), pattern)
    else:
        font = QFont()
        font.setPixelSize(max(6, round(size * 0.42)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(qcolor(W.ICON_FALLBACK_TEXT))
        p.drawText(full, Qt.AlignCenter, name[:1])
    p.end()
    # 底板或图案有一张没画上，就画了缺图时的圆圈或首字（用了当前皮肤的颜色）：按带颜色的键存，换皮肤时不拿错
    used_colors = base.isNull() or pattern.isNull()
    _icon_cache[fallback_key if used_colors else key] = (img, tuple(missing))
    return img


class ChipGrid(QWidget):
    """「查看筹码」面板的 3-4-3 图标格子（每页 10 格）：已有筹码按获得顺序填入，空格子画成暗色圆圈；悬停显示筹码名和效果。
    超过 10 个时照面板分页：格子下面一行左右箭头和页码「1/2」，点亮着的箭头翻页；第一页的左箭头、最后一页的右箭头画暗色、
    点了没反应。全部筹码列在旁边的筹码小卡片里，翻到哪一页只看得到那一页的格子。

    格子的大小和间距按面板的比例（格子外径 164、横向间距 213、行距 184，单位见 vision/chip_panel.py）。不超过 10 个时
    不画这一行，卡片不因为分页变大。
    """

    ROWS = (3, 4, 3)
    PAGE_SIZE = sum(ROWS)
    PREV_TIP = "上一页"
    NEXT_TIP = "下一页"

    def __init__(self, diameter: int = 26, parent=None):
        super().__init__(parent)
        self._chips: list = []  # [(名称, 稀有度, 图标 ID, 悬停说明)]：全部已有筹码，不只当前页
        self._page = 1  # 第几页，从 1 数
        self._hover = 0  # 鼠标在哪个箭头上：-1 左、1 右、0 不在
        self.set_diameter(diameter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_diameter(self, diameter: int) -> None:
        """格子的直径（逻辑像素）；间距按面板的比例跟着变。"""
        self._d = diameter
        self._px = diameter * 213 / 164
        self._py = diameter * 184 / 164
        self.updateGeometry()
        self.update()

    def set_chips(self, chips: list[tuple[str, str, str, str]]) -> None:
        """重设全部已有筹码。当前页还在就留在当前页；筹码少了、这一页没了就退到最后一页；不超过 10 个回第 1 页。
        跨过 10 个（多出或去掉翻页那一行）时重算高度。"""
        was_paged = self.page_count() > 1
        self._chips = list(chips)
        self._page = min(self._page, self.page_count())
        if len(self._chips) <= self.PAGE_SIZE:
            self._page = 1
        if was_paged != (self.page_count() > 1):
            self.updateGeometry()
        self.update()

    def names(self) -> list[str]:
        """当前这一页画着的筹码名。"""
        return [c[0] for c in self._page_chips()]

    def all_names(self) -> list[str]:
        """全部已有筹码名（不只是当前页）。"""
        return [c[0] for c in self._chips]

    def page(self) -> int:
        """当前是第几页（从 1 数）。"""
        return self._page

    def page_count(self) -> int:
        """一共几页；没有筹码时也算 1 页。"""
        return max(1, math.ceil(len(self._chips) / self.PAGE_SIZE))

    def set_page(self, page: int) -> None:
        """翻到第 page 页（超出范围时取最近的一页）。"""
        page = max(1, min(int(page), self.page_count()))
        if page != self._page:
            self._page = page
            self.update()

    def _page_chips(self) -> list:
        start = (self._page - 1) * self.PAGE_SIZE
        return self._chips[start:start + self.PAGE_SIZE]

    def centers(self) -> list[QPointF]:
        out = []
        r = self._d / 2
        for row, n in enumerate(self.ROWS):
            offset = 0.0 if n == 4 else self._px / 2
            for k in range(n):
                out.append(QPointF(r + offset + k * self._px, r + row * self._py))
        return out

    def _content_height(self) -> float:
        return self._d + 2 * self._py

    def _pager_height(self) -> float:
        return max(20.0, self._d * 0.8)

    def sizeHint(self) -> QSize:
        height = self._content_height()
        if self.page_count() > 1:
            height += self._pager_height()
        return QSize(math.ceil(self._d + 3 * self._px), math.ceil(height))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def slot_at(self, pos) -> int | None:
        """光标在格子的第几格（0～9，不含页的偏移）；不在格子上为 None。"""
        r = self._d / 2
        for i, c in enumerate(self.centers()):
            if (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= r * r:
                return i
        return None

    def _pager_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(9, round(self._d * 0.42)))
        font.setBold(True)
        return font

    def _page_text(self) -> str:
        return f"{self._page}/{self.page_count()}"

    def _pager_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        """页码那一行的（左箭头、页码、右箭头）区域；不分页时都是空矩形。"""
        if self.page_count() <= 1:
            return QRectF(), QRectF(), QRectF()
        fm = QFontMetricsF(self._pager_font())
        width = self.width() or math.ceil(self._d + 3 * self._px)
        top = self._content_height()
        height = self._pager_height()
        cy = top + height / 2
        box = max(18.0, self._d * 0.72)
        gap = max(8.0, self._d * 0.3)
        mid = width / 2
        text_w = fm.horizontalAdvance(self._page_text())
        left_x = mid - text_w / 2 - gap - box
        right_x = mid + text_w / 2 + gap
        return (
            QRectF(left_x, cy - box / 2, box, box),
            QRectF(mid - text_w / 2, cy - fm.height() / 2, text_w, fm.height()),
            QRectF(right_x, cy - box / 2, box, box),
        )

    def _arrow_at(self, pos) -> int:
        """光标在哪个箭头上：-1 左、1 右、0 不在。"""
        if self.page_count() <= 1:
            return 0
        point = QPointF(pos)
        left, _, right = self._pager_rects()
        if left.contains(point):
            return -1
        if right.contains(point):
            return 1
        return 0

    def _can_turn(self, step: int) -> bool:
        return 1 <= self._page + step <= self.page_count()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        dpr = self.devicePixelRatioF()
        size = max(8, round(self._d * dpr))
        r = self._d / 2
        chips = self._page_chips()
        for i, c in enumerate(self.centers()):
            rect = QRectF(c.x() - r, c.y() - r, self._d, self._d)
            if i < len(chips):
                name, rarity, icon, _ = chips[i]
                p.drawImage(rect, chip_icon(name, rarity, icon, size))
            else:
                p.setPen(QPen(qcolor(W.GRID_EMPTY_BORDER), 1))
                p.setBrush(qcolor(W.GRID_EMPTY_FILL))
                p.drawEllipse(rect.adjusted(1, 1, -1, -1))
        self._paint_pager(p)
        p.end()

    def _paint_pager(self, p: QPainter) -> None:
        if self.page_count() <= 1:
            return
        left, middle, right = self._pager_rects()
        p.setFont(self._pager_font())
        p.setPen(qcolor(C.DIM_COLOR))
        p.drawText(middle, Qt.AlignCenter, self._page_text())
        aw = max(4.5, self._d * 0.2)
        ah = max(8.0, self._d * 0.4)
        for rect, step in ((left, -1), (right, 1)):
            enabled = self._can_turn(step)
            if enabled and self._hover == step:
                color = W.HOVER_BORDER
            else:
                color = C.TEXT_COLOR if enabled else C.DIM_COLOR
            cx, cy = rect.center().x(), rect.center().y()
            if step < 0:
                points = [QPointF(cx + aw / 2, cy - ah / 2), QPointF(cx + aw / 2, cy + ah / 2),
                          QPointF(cx - aw / 2, cy)]
            else:
                points = [QPointF(cx - aw / 2, cy - ah / 2), QPointF(cx - aw / 2, cy + ah / 2),
                          QPointF(cx + aw / 2, cy)]
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(color))
            p.drawPolygon(QPolygonF(points))

    def event(self, event) -> bool:
        if event.type() == QEvent.ToolTip:
            arrow = self._arrow_at(event.pos())
            if arrow and self._can_turn(arrow):
                QToolTip.showText(event.globalPos(), self.PREV_TIP if arrow < 0 else self.NEXT_TIP, self)
                return True
            if arrow:  # 暗着的箭头点了没反应，不写「上一页」「下一页」
                QToolTip.hideText()
                return True
            slot = self.slot_at(event.pos())
            chips = self._page_chips()
            if slot is not None and slot < len(chips) and chips[slot][3]:
                QToolTip.showText(event.globalPos(), chips[slot][3], self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            arrow = self._arrow_at(event.position())
            if arrow:
                if self._can_turn(arrow):
                    self.set_page(self._page + arrow)
                    if not self._can_turn(arrow):  # 翻到头了：鼠标没动、还停在这个箭头上，它已经暗了
                        self.unsetCursor()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        hover = self._arrow_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()
        if hover and self._can_turn(hover):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover:
            self._hover = 0
            self.update()
        self.unsetCursor()
        super().leaveEvent(event)


# ------------------------------------------------------------ 地图下拉胶囊


class MapPill(QComboBox):
    """地图下拉框，画成胶囊：可点击，所以有底色和框线，右侧有 ▾。

    胶囊上显示的文字由调用方指定（地图名、「地图 待识别」等，主体暗色、数值按含义着色）；
    点开后的下拉列表照常列出「地图：自动识别」和各张地图。滚轮不切换地图（免得滚动时误改）。
    内边距、框线和底色按当前皮肤（theme.WidgetLook 的 MAP_*、HOVER_*、TONES 的 button 和 warn）。
    """

    ARROW = "▾"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list = []
        self._tone = ""
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_display(self, segments, tone: str = "") -> None:
        segments = _segments(segments)
        if (segments, tone) != (self._segments, self._tone):
            self._segments, self._tone = segments, tone
            self.updateGeometry()
            self.update()

    def display_text(self) -> str:
        return "".join(t for t, _ in self._segments)

    def _text_width(self) -> float:
        fmt = _formats(self._segments, self._text_color())
        return natural_width(self.display_text(), fmt, self.font())

    def _text_color(self) -> QColor:
        if self._tone == "warn":
            return _tone("warn")[2]
        return QColor(theme.ink(C.TEXT_COLOR) if _dark_fill("button") else C.TEXT_COLOR)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        w = math.ceil(self._text_width() + fm.horizontalAdvance(" " + self.ARROW)) + 2 * (W.MAP_PAD_X + 1)
        return QSize(w, math.ceil(fm.height()) + 2 * (W.PILL_PAD_Y + 1))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(W.PILL_RADIUS, rect.height() / 2)
        hover = self.underMouse() or self.view().isVisible()
        button_border, button_fill, _ = _tone("button")
        border = qcolor(W.MAP_WARN_BORDER) if self._tone == "warn" else (
            qcolor(W.HOVER_BORDER) if hover else button_border)
        p.setPen(QPen(border, 1))
        p.setBrush(qcolor(W.HOVER_FILL) if hover else button_fill)
        if W.TONE_SHAPE.get("button") == "cut":  # 切掉左上、右下两个角（碳纤流光）
            from . import decor

            p.drawPath(decor.chamfer(rect, min(6.0, rect.height() * 0.3)))
        else:
            p.drawRoundedRect(rect, radius, radius)
        segments = self._segments + [(" " + self.ARROW, W.MAP_ARROW_COLOR)]
        text = "".join(t for t, _ in segments)
        fmt = _formats(segments, self._text_color(), dark=_dark_fill("button"))
        layout, _, bottom = shape_text(text, fmt, self.font(), 1e6, 1e6)
        layout.draw(p, QPointF(W.MAP_PAD_X + 1, (self.height() - bottom) / 2))
        p.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def showPopup(self) -> None:
        fm = self.view().fontMetrics()
        widest = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0)
        self.view().setMinimumWidth(widest + 36)
        super().showPopup()

    def hidePopup(self) -> None:
        super().hidePopup()
        self.update()


class WheelLockedCombo(QComboBox):
    """普通样式的下拉框（「筹码方案」卡片的方案下拉框），只是滚轮不切换选项：鼠标停在上面滚动时不会误换方案
    （换方案会立即重算推荐）。点开下拉列表选择、键盘选择照常。外观和其他下拉框完全相同（样式见悬浮窗样式表 theme.overlay_style）。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


# ------------------------------------------------------------ 下拉列表的每一行

# 下拉列表每一行的画法。Windows 11 自己画的鼠标指示是比底色只暗 1 级的
# 圆角框，在深色的悬浮窗上看不出来，所以自己画：鼠标所在的那一行（键盘移到的那一行同样）亮灰圆角框、字变白；下拉框当前的
# 值那一行左边一根圆头短条，颜色跟 Windows 的强调色（应用程序调色板的 Accent，换了主题色跟着变）；两者可以在同一行。
# 不能选的行（分组标题）不高亮、不画短条，字写成粗体、颜色是平时的字色调淡（COMBO_HEADER_ALPHA）。框本身和字不另加颜色。
# 颜色和尺寸在皮肤里（theme.WidgetLook 的 COMBO_*）
COMBO_HEADER_ALPHA = 150  # 分组标题的字：平时的字色的不透明度（0～255）


class ComboRowDelegate(QStyledItemDelegate):
    """下拉列表每一行的画法（见上面的说明）。行高、宽度照原来的条目代理（base；没有时照 QStyledItemDelegate），只在放不下
    文字时加宽。current 返回当前的值在第几行（下拉框的 currentIndex）；为 None 时不画短条（例如输入筹码名时的补全列表）。
    悬停说明（各项的 ToolTipRole）照 QStyledItemDelegate 显示。"""

    def __init__(self, parent, base=None, current: Callable[[], int] | None = None):
        super().__init__(parent)
        self._base = base  # 原来的条目代理：留着算行高（行高照现在的）
        self._base_ptr = getCppPointer(base)[0] if base is not None else None
        self._current = current

    def _base_delegate(self):
        """原来的条目代理。它是 Qt 自己建的（挂在下拉列表下面），PySide 偶尔会连同下拉列表的 Python 包装一起作废（底层的 Qt
        对象还在，例如换皮肤时重套样式表之前有一批延迟删除）；这时按原来的地址在下拉框、下拉列表下面重新找一次。找不到时
        为 None（行高按 QStyledItemDelegate）。"""
        base = self._base
        if base is None or isValid(base):
            return base
        self._base = None
        owner = self.parent()
        roots = [owner]
        view = getattr(owner, "view", None)
        if callable(view):
            roots.append(view())
        for root in roots:
            for found in root.findChildren(QAbstractItemDelegate):
                if getCppPointer(found)[0] == self._base_ptr:
                    self._base = found
                    return found
        return None

    def sizeHint(self, option, index) -> QSize:
        base = self._base_delegate()
        size = base.sizeHint(option, index) if base is not None else super().sizeHint(option, index)
        text = str(index.data(Qt.DisplayRole) or "")
        need = QFontMetrics(option.font).horizontalAdvance(text) + sum(W.COMBO_TEXT_PAD)
        return QSize(max(size.width(), need), size.height())

    @staticmethod
    def accent() -> QColor:
        """短条的颜色：皮肤指定了颜色（COMBO_BAR_COLOR）时用它，否则用 Windows 的强调色（应用程序调色板的 Accent）。"""
        if W.COMBO_BAR_COLOR:
            return qcolor(W.COMBO_BAR_COLOR)
        return QGuiApplication.palette().color(QPalette.ColorRole.Accent)

    @staticmethod
    def is_header(index) -> bool:
        """这一行是不是不能选的分组标题（add_combo_header：不可用、不能选）。"""
        flags = index.flags()
        return not flags & Qt.ItemIsEnabled and not flags & Qt.ItemIsSelectable

    def paint(self, painter, option, index) -> None:
        enabled = bool(option.state & QStyle.State_Enabled) and bool(index.flags() & Qt.ItemIsEnabled)
        hot = enabled and bool(option.state & (QStyle.State_Selected | QStyle.State_MouseOver))
        current = enabled and self._current is not None and index.row() == self._current()
        header = self.is_header(index)
        dx, dy = W.COMBO_HOT_INSET
        box = QRectF(option.rect).adjusted(dx, dy, -dx, -dy)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if hot:
            painter.setBrush(qcolor(W.COMBO_HOT_FILL))
            painter.drawRoundedRect(box, W.COMBO_HOT_RADIUS, W.COMBO_HOT_RADIUS)
        if current:
            bar_w = W.COMBO_BAR_WIDTH
            length = round(box.height() * W.COMBO_BAR_LENGTH)
            bar = QRectF(box.left() + W.COMBO_BAR_X, box.top() + (box.height() - length) / 2, bar_w, length)
            painter.setBrush(self.accent())
            painter.drawRoundedRect(bar, bar_w / 2, bar_w / 2)
        font = option.font
        if hot:
            color = qcolor(W.COMBO_HOT_TEXT)
        elif header:  # 分组标题：粗体，平时的字色调淡（COMBO_HEADER_ALPHA），各套皮肤上都看得出是标题、和下面的行分得开
            color = QColor(option.palette.color(QPalette.Active, QPalette.Text))
            color.setAlpha(COMBO_HEADER_ALPHA)
            font = QFont(font)
            font.setBold(True)
        elif not enabled:
            color = option.palette.color(QPalette.Disabled, QPalette.Text)
        else:
            color = option.palette.color(QPalette.Active, QPalette.Text)
        left, right = W.COMBO_TEXT_PAD
        area = QRect(option.rect).adjusted(left, 0, -right, 0)
        painter.setFont(font)
        painter.setPen(color)
        text = QFontMetrics(font).elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideRight, area.width())
        painter.drawText(area, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()


def draw_combo_rows(combo: QComboBox) -> ComboRowDelegate:
    """下拉框的下拉列表改用 ComboRowDelegate 画每一行（原来的条目代理留着算行高）；可编辑的下拉框（输入筹码名搜索的）
    另把补全列表也换上（补全列表没有「当前的值」，不画短条）。返回下拉列表的条目代理。皮肤写了 COMBO_ARROW 时下拉框右端的
    箭头自己画（ComboArrow）。"""
    delegate = ComboRowDelegate(combo, combo.itemDelegate(), combo.currentIndex)
    combo.setItemDelegate(delegate)
    completer = combo.completer()
    if completer is not None:
        popup = completer.popup()
        popup.setItemDelegate(ComboRowDelegate(popup, popup.itemDelegate()))
    if not isinstance(combo, MapPill):
        combo.installEventFilter(_combo_arrow())
    return delegate


# ------------------------------------------------------------ 下拉列表里不能选的分组标题
# 悬浮窗方案下拉框的「── 我的方案 ──」、三处角色下拉框（悬浮窗、编辑方案、导入/导出）每一类前面的「── 物理主C ──」：
# 没有数据（itemData 为 None），不可用、不能选。Qt 的下拉框按键盘上下、滚轮、按首字母跳都跳过不可用的项，下拉列表里点它也
# 选不上；按数据找项（findData）、按下标取数据（itemData）时它不会被当成哪个角色或方案。下拉列表里照 ComboRowDelegate 画：
# 不高亮、不画短条，字写成粗体、平时的字色调淡（COMBO_HEADER_ALPHA），看得出是标题。

HERO_GROUP_HEADER = "── {group} ──"  # 角色下拉框里一类的分组标题（组名是 data.HERO_GROUPS 的六类）


def add_combo_header(combo: QComboBox, text: str) -> int:
    """在下拉框末尾加一行不能选的分组标题（text 照原样写，例如「── 我的方案 ──」），返回它的下标。"""
    combo.addItem(text)
    index = combo.count() - 1
    item = combo.model().item(index)
    item.setEnabled(False)
    item.setSelectable(False)
    return index


def fill_hero_combo(combo: QComboBox, heroes, text: Callable | None = None) -> None:
    """角色下拉框：按 data.heroes_in_group_order 的六类排（物理主C、法术C、辅C、钱辅、奶辅、功能辅，同一类里保持传入的
    先后），每一类前面一行不能选的分组标题（HERO_GROUP_HEADER，add_combo_header）；各项的数据是角色名，文字是 text(角色)
    （不给时为称号 · 角色名，Hero.label）。接在下拉框已有的项后面（「全部角色」这类项先加好）。"""
    label = text or (lambda hero: hero.label)
    group = None
    for hero in heroes_in_group_order(heroes):
        if hero.group != group:
            group = hero.group
            add_combo_header(combo, HERO_GROUP_HEADER.format(group=group))
        combo.addItem(label(hero), hero.name)


def first_data(combo: QComboBox):
    """下拉框里第一项有数据的项的数据（跳过分组标题）；没有时为 None。"""
    return next((combo.itemData(i) for i in range(combo.count()) if combo.itemData(i)), None)


class ComboArrow(QObject):
    """下拉框右端的实心小三角（皮肤的 COMBO_ARROW 写了颜色时）：下拉框照常画完以后在右端补画。样式表画不出三角形（用框线
    拼的会画成方块），系统的箭头在深色的胶囊上又看不清。皮肤没写时什么都不做（下拉框照自己的画法）。"""

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Paint or not W.COMBO_ARROW or not isinstance(obj, QComboBox):
            return False
        QComboBox.paintEvent(obj, event)
        p = QPainter(obj)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(W.COMBO_ARROW))
        cx, cy = obj.width() - 15.0, obj.height() / 2 + 0.5
        p.drawPolygon(QPolygonF([QPointF(cx - 5, cy - 3), QPointF(cx + 5, cy - 3), QPointF(cx, cy + 4)]))
        p.end()
        return True


_COMBO_ARROW: ComboArrow | None = None


def _combo_arrow() -> ComboArrow:
    global _COMBO_ARROW
    if _COMBO_ARROW is None:
        _COMBO_ARROW = ComboArrow()
    return _COMBO_ARROW


# ------------------------------------------------------------ 卡片、分隔线


class FoldButton(QPushButton):
    """卡片头的折叠按钮：小号方形按钮，展开中显示 ▾，已收起显示 ▸（按钮文字就是这两个字符）。

    底色和框线照样式表画（悬浮窗样式表的 #cardToggle：和其他按钮同一套样式，尺寸更小、颜色更淡），
    箭头自己画成实心三角形：字体里的 ▾ ▸ 只有四五个像素宽，在小按钮里看不清。边长（比文字按钮矮一些）、三角形的大小和颜色
    按当前皮肤（theme.WidgetLook 的 FOLD_*）。
    管整个悬浮窗的「全部折叠 / 全部展开」在标题栏，是文字按钮。
    """

    OPEN, CLOSED = "▾", "▸"

    def __init__(self, parent=None):
        super().__init__(self.OPEN, parent)
        self.setObjectName("cardToggle")
        self.apply_skin()
        self.setCursor(Qt.PointingHandCursor)

    def apply_skin(self) -> None:
        """按当前皮肤的边长定大小（换皮肤时卡片调用）。"""
        self.setFixedSize(W.FOLD_SIZE, W.FOLD_SIZE)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(W.FOLD_SIZE, W.FOLD_SIZE)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_open(self, on: bool) -> None:
        self.setText(self.OPEN if on else self.CLOSED)

    def paintEvent(self, event) -> None:
        p = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""  # 底色、框线和悬停效果照样式表画，箭头在下面画
        p.drawControl(QStyle.CE_PushButton, option)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        # 平时暗色，悬停或按下时变亮（和样式表里 #cardToggle 的 color 一致）
        arrow = qcolor(W.FOLD_ARROW_HOVER if self.underMouse() or self.isDown() else W.FOLD_ARROW_COLOR)
        p.setBrush(arrow)
        w, h = W.FOLD_ARROW
        cx, cy = self.width() / 2, self.height() / 2
        if W.FOLD_CHEVRON:  # 皮肤要折线箭头（∨ ›）
            p.setPen(QPen(arrow, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            if self.text() == self.CLOSED:
                pts = [QPointF(cx - h / 2 + 0.5, cy - w / 2), QPointF(cx + h / 2 + 0.5, cy), QPointF(cx - h / 2 + 0.5, cy + w / 2)]
            else:
                pts = [QPointF(cx - w / 2, cy - h / 2 + 0.5), QPointF(cx, cy + h / 2 + 0.5), QPointF(cx + w / 2, cy - h / 2 + 0.5)]
            p.drawPolyline(QPolygonF(pts))
            p.end()
            return
        # 三角形朝箭头方向挪半个像素：看起来居中，100% 缩放时边缘也正好落在像素上
        if self.text() == self.CLOSED:
            cx += 0.5
            points = [QPointF(cx - h / 2, cy - w / 2), QPointF(cx + h / 2, cy), QPointF(cx - h / 2, cy + w / 2)]
        else:
            cy += 0.5
            points = [QPointF(cx - w / 2, cy - h / 2), QPointF(cx + w / 2, cy - h / 2), QPointF(cx, cy + h / 2)]
        p.drawPolygon(QPolygonF(points))
        p.end()


class TagLabel(QLabel):
    """文字标签，皮肤可以给它画成别的样子（WidgetLook.LABEL_DECOR 按用途 role 取画法：tag 卡片左上角的标签、tab 页签，
    颜色见 ChromeLook）。皮肤没写这个用途时就是普通的 QLabel（样子照样式表）。大小照样式表（字号、内边距），字自己画在
    标签正中（白字黑描边）。"""

    def __init__(self, text: str = "", role: str = "", parent=None):
        super().__init__(text, parent)
        self.role = role
        self.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗

    def paintEvent(self, event) -> None:
        kind = W.LABEL_DECOR.get(self.role)
        if not kind:
            super().paintEvent(event)
            return
        from . import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        look = theme.CHROME
        if kind == "tab":
            decor.tab(p, rect, self.text(), self.font(), look, on=False)
        elif kind == "title_tab":  # 压在上沿正中的粉色标题页签（经典主题-黑）
            decor.title_tab(p, rect, self.text(), self.font(), look)
        elif kind == "plain":  # 白字黑描边，不画底（经典主题-反色：写在黑色通栏上）
            decor.outlined_text(p, rect, self.text(), self.font(), look.TITLE_TEXT, look.TITLE_OUTLINE,
                                max(3.0, decor.px(self.font()) * 0.26), align=Qt.AlignLeft)
        elif kind == "logo":  # 前面一个渐变小方块、深色字（白色海景房的悬浮窗标题；左边的内边距留给小方块）
            decor.logo_title(p, rect, self.text(), self.font(), look)
        elif kind in ("index", "rgb"):  # 碳纤流光：左边的内边距里写编号（number 属性）或画四色小方块，字斜着写
            self._paint_carbon(p, kind, look)
        else:  # 标签的形状按 ChromeLook.TAG_KIND；白色海景房的小灯点按所在卡片的名字（Card.key）取颜色
            decor.tag(p, rect, self.text(), self.font(), look, getattr(self.parentWidget(), "key", "") or "")
        p.end()

    def _paint_carbon(self, p: QPainter, kind: str, look) -> None:
        """碳纤流光的标题（LABEL_DECOR 为 index、rgb）：样式表的左内边距里，index 写编号（number 属性，ChromeLook.INDEX_INK，
        数字字体的窄体），rgb 画四色小方块（ChromeLook.RGB）；内容区里写斜体的字（颜色照样式表）。"""
        from . import decor
        from .decor import index_font

        inner = QRectF(self.contentsRect())
        lead = QRectF(0, inner.top(), max(0.0, inner.left() - 4), inner.height())
        font = QFont(self.font())
        font.setItalic(True)
        size = decor.px(font)
        if kind == "rgb":
            side = min(lead.width(), size + 2.0)
            decor.rgb_logo(p, QRectF(lead.left() + 2, lead.center().y() - side / 2, side, side), look.RGB)
        else:
            number = str(self.property("number") or "")
            if number:
                p.setFont(index_font(size))
                p.setPen(decor.qc(look.INDEX_INK or C.TEXT_COLOR))
                p.drawText(lead, Qt.AlignVCenter | Qt.AlignLeft, number)
        decor.flat_text(p, inner, self.text(), font, self.palette().color(self.foregroundRole()))


class Card(QFrame):
    """一张卡片：卡片头一行（区域名、折叠时的摘要、额外部件、折叠按钮 ▾ / ▸）和主体。

    底色比面板浅一级、圆角 8（样式见悬浮窗样式表的 #card）；内边距、各行之间的间距按当前皮肤（theme.WidgetLook 的 CARD_*）。
    折叠后只剩卡片头，摘要显示在区域名后面。皮肤的 CARD_DECOR 为 chrome 时卡片照游戏内风格自己画（浅灰底、下沿投影、底纹），
    从区域名标签的中线往下画，标签和折叠按钮一半露在卡片上沿外面。
    """

    toggle_clicked = Signal()

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("card")
        box = QVBoxLayout(self)
        head = self.head = QHBoxLayout()
        self.title = TagLabel(title, "card")
        self.title.setObjectName("cardTitle")
        self.summary = QLabel("")
        self.summary.setObjectName("cardSummary")
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setTextInteractionFlags(Qt.NoTextInteraction)  # 富文本默认接走鼠标；关掉文字交互，按在文字上也能拖动悬浮窗
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.toggle = FoldButton()
        self.toggle.clicked.connect(self.toggle_clicked)
        head.addWidget(self.title)
        head.addWidget(self.summary, 1)
        head.addWidget(self.toggle)
        box.addLayout(head)
        self.body = QWidget()
        self.body_box = QVBoxLayout(self.body)
        self.body_box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.body)
        self._open = True
        self.extras: list[QWidget] = []  # 卡片头里另加的部件（「已有筹码」的个数「6 个」和「＋ 补录筹码」）
        self.apply_skin()
        self.set_open(True)

    def apply_skin(self) -> None:
        """按当前皮肤设内边距和间距、折叠按钮的大小（建卡片时和换皮肤时调用）。"""
        self.layout().setContentsMargins(*W.CARD_MARGINS)
        self.layout().setSpacing(W.CARD_HEAD_GAP if W.CARD_HEAD_GAP >= 0 else W.CARD_SPACING)
        self.head.setSpacing(W.CARD_HEAD_SPACING)
        self.body_box.setSpacing(W.CARD_SPACING)
        self.toggle.apply_skin()
        self.update()

    def paintEvent(self, event) -> None:
        if W.CARD_DECOR == "index":  # 不画卡片：区域名后面一小截亮色、一条细线（碳纤流光；只画在字和按钮之间的空白里）
            span = self._rule_span()
            if span is not None:
                from . import decor

                p = QPainter(self)
                decor.index_rule(p, span[0], span[1], self.title.geometry().center().y() + 0.5, theme.CHROME)
                p.end()
            return
        if W.CARD_DECOR == "section":  # 不画卡片：区域名后面一条横线（只画在字和按钮之间的空白里）
            span = self._rule_span()
            if span is not None:
                from . import decor

                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(decor.qc(theme.CHROME.RULE), 1.4))
                y = self.title.geometry().center().y() + 0.5
                p.drawLine(QPointF(span[0], y), QPointF(span[1], y))
                p.end()
            return
        if W.CARD_DECOR != "chrome":
            super().paintEvent(event)
            return
        from . import decor

        look = theme.CHROME
        metrics = decor.card_metrics(look.CARD_KIND)
        if metrics.top == "edge":  # 白卡片整张画（卡片头的小灯点、区域名写在卡片里面）
            top, left = 0, 0
            body = QRectF(metrics.inset, metrics.inset, self.width() - 2 * metrics.inset,
                          self.height() - 2 * metrics.inset)
        elif metrics.top == "head":  # 贴纸卡片在卡片头（黑色斜带）下面，右下留出投影；收起时只剩卡片头，不画卡片
            if not self.body.isVisibleTo(self):
                return
            top = self.head.geometry().bottom() + metrics.head_gap
            left = metrics.body_x
            body = QRectF(left, top, self.width() - left - metrics.right, max(0, self.height() - top - metrics.shadow))
        else:
            top = self.title.geometry().center().y()
            if metrics.top == "title":  # 白卡片从区域名标签的上半截往下画：卡片头里的「0 个」、按钮都落在卡片上
                top = self.title.geometry().top() + round(self.title.height() * metrics.frac)
            left = max(0, self.layout().contentsMargins().left() - 8)
            body = QRectF(left, top, self.width() - left - 2, max(0, self.height() - top - 4))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, decor.cached(self, ("card", theme.current_key(), self.key, top, left), self.size(),
                                        lambda q: decor.card(q, body, look, seed=len(self.key) * 7 + 11, radius=22)))
        p.end()

    def _rule_span(self) -> tuple[float, float] | None:
        """区域名后面那条横线的左右两头（CARD_DECOR 为 section 时）：从区域名、紧跟它的部件和收起时的摘要的右边起，到右边
        第一个显示着的部件（「＋ 补录筹码」、折叠按钮）前面为止，两头各留 10 像素；放不下 24 像素时为 None。"""
        head = self.head
        idx = head.indexOf(self.summary)
        left, right = 0.0, float(self.width())
        for i in range(head.count()):
            widget = head.itemAt(i).widget()
            if widget is None or widget is self.summary or not widget.isVisibleTo(self):
                continue
            g = widget.geometry()
            if i < idx:
                left = max(left, float(g.right()))
            else:
                right = min(right, float(g.left()))
        if self.summary.isVisibleTo(self) and self.summary.text():
            left = max(left, self.summary.x() + min(self.summary.sizeHint().width(), self.summary.width()))
        left, right = left + 10, right - 10
        return (left, right) if right - left >= 24 else None

    def add_title_widget(self, widget: QWidget) -> None:
        """紧跟在区域名后面（例如「已有筹码」后面的「6 个」）。"""
        self.head.insertWidget(1, widget)
        self.extras.append(widget)

    def add_header_widget(self, widget: QWidget) -> None:
        """放在卡片头折叠按钮的左边。"""
        self.head.insertWidget(self.head.count() - 1, widget)
        self.extras.append(widget)

    def add_title_note(self, widget: QWidget) -> None:
        """紧跟在区域名（和 add_title_widget 加的部件）后面的一段说明（「推荐」卡片头写这次推荐按哪个角色、哪套方案算的那段
        暗色小字）：只在卡片展开时显示，收起时由管它的部件藏起来（这时卡片头写摘要），所以不算进 extras（收起时摘要能用的
        宽度不用让出它的地方）。"""
        self.head.insertWidget(self.head.indexOf(self.summary), widget)

    @property
    def is_open(self) -> bool:
        return self._open

    def set_open(self, on: bool, summary_html: str = "") -> None:
        """展开或收起：折叠按钮的箭头（▾ 展开中 / ▸ 已收起）和悬停说明随之切换，悬停说明写明作用和哪张卡片。"""
        self._open = on
        self.body.setVisible(on)
        self.summary.setText("" if on else summary_html)
        self.toggle.set_open(on)
        name = self.title.text()
        self.toggle.setToolTip(
            f"收起「{name}」卡片（只显示卡片头和摘要）" if on else f"展开「{name}」卡片"
        )


class Separator(QWidget):
    """选项块之间的分隔：上下留出间距，中间一条很淡的横线。"""

    def __init__(self, height: int = 14, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        pen = QPen(qcolor(W.SEPARATOR_COLOR), 1)
        if W.SEPARATOR_DASH:
            pen.setDashPattern(list(W.SEPARATOR_DASH))
            pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        y = self.height() // 2
        p.drawLine(0, y, self.width(), y)
        p.end()


def scaled_pixmap(image: QImage, dpr: float) -> QPixmap:
    pix = QPixmap.fromImage(image)
    pix.setDevicePixelRatio(dpr)
    return pix
