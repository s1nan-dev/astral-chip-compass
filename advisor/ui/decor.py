"""游戏内风格的几种自己画的样子：窗口底色的斜向渐变和细斜线、深灰面板（淡色几何小图案、两条斜带）、浅灰卡片（下沿投影、
底纹）、卡片左上角的标签、页签、「返回」、胶囊按钮、警告的圆标、描边字、星星和网点。

只管画，参数（颜色、尺寸）由调用的地方从皮肤取（theme.ChromeLook）；不含业务逻辑，不建部件。几何小图案、星星的位置按
种子随机，同一个大小、同一个种子每次画出来一样（换皮肤、重画不跳）。数字、斜体标题的字体（index_font、italic_title_font）
按当前皮肤的字体取：chrome、widgets 和各窗口都用，放在这里，widgets 用它们时不必导入 chrome。
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontInfo,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from . import theme


_RGBA = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)")


def qc(c) -> QColor:
    """色号 → QColor，四种写法都认：QColor 本身、"#rrggbb"（其他 QColor 认得的写法也行，例如 "transparent"）、
    样式表里的 "rgba(r,g,b,a)"、(r, g, b) 或 (r, g, b, a) 元组。全项目只此一处解析皮肤颜色，
    widgets.qcolor、widgets.color、settings.color 都是它的别名。"""
    if isinstance(c, QColor):
        return QColor(c)
    if isinstance(c, str):
        m = _RGBA.fullmatch(c.strip())
        return QColor(*(int(v) for v in m.groups())) if m else QColor(c)
    return QColor(*c)


def gradient(x1: float, y1: float, x2: float, y2: float, colors) -> QLinearGradient | QColor:
    """从 (x1, y1) 到 (x2, y2) 的线性渐变，colors 均匀分布；只有一种颜色时就是那种颜色。"""
    colors = list(colors) if isinstance(colors, (tuple, list)) and colors and not isinstance(colors[0], int) else [colors]
    if len(colors) == 1:
        return qc(colors[0])
    g = QLinearGradient(x1, y1, x2, y2)
    for i, c in enumerate(colors):
        g.setColorAt(i / (len(colors) - 1), qc(c))
    return g


def brush(fill) -> QBrush:
    return QBrush(fill) if isinstance(fill, (QLinearGradient, QColor)) else QBrush(qc(fill))


def rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def capsule(rect: QRectF) -> QPainterPath:
    return rounded(rect, rect.height() / 2)


def hard_shadow(p: QPainter, path: QPainterPath, dx: float, dy: float, color) -> None:
    """实心错位投影（游戏界面上卡片、按钮下沿的那一截）。"""
    shadow = QPainterPath(path)
    shadow.translate(dx, dy)
    p.fillPath(shadow, qc(color))


def diag_lines(p: QPainter, rect: QRectF, color, step: float, width: float = 1.0) -> None:
    """45° 的细斜线（左下到右上）。"""
    p.save()
    p.setClipRect(rect, Qt.IntersectClip)
    p.setPen(QPen(qc(color), width))
    h = rect.height()
    x = rect.left() - h
    while x < rect.right() + step:
        p.drawLine(QPointF(x, rect.bottom()), QPointF(x + h, rect.top()))
        x += step
    p.restore()


def doodles(p: QPainter, rect: QRectF, color, density: float, seed: int, size=(5.0, 9.0), width: float = 2.0) -> None:
    """淡色几何小图案（游戏设置界面的底纹）：圆圈、三角、折线、半圆弧。density 为每多少平方像素一个。"""
    n = int(rect.width() * rect.height() / density) if density > 0 else 0
    if n <= 0:
        return
    rng = random.Random(seed)
    p.save()
    p.setPen(QPen(qc(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    for _ in range(n):
        x = rng.uniform(rect.left(), rect.right())
        y = rng.uniform(rect.top(), rect.bottom())
        s = rng.uniform(*size)
        kind = rng.randrange(4)
        if kind == 0:
            p.drawEllipse(QPointF(x, y), s * 0.6, s * 0.6)
        elif kind == 1:
            p.drawPolygon(QPolygonF([QPointF(x, y - s), QPointF(x + s, y + s * 0.8), QPointF(x - s, y + s * 0.8)]))
        elif kind == 2:
            p.drawPolyline(QPolygonF([QPointF(x - s, y), QPointF(x - s / 3, y - s * 0.6), QPointF(x + s / 3, y + s * 0.6),
                                      QPointF(x + s, y)]))
        else:
            p.drawArc(QRectF(x - s, y - s, 2 * s, 2 * s), int(rng.uniform(0, 360)) * 16, 200 * 16)
    p.restore()


def sparkle_path(cx: float, cy: float, r: float, k: float = 0.3) -> QPainterPath:
    """四角星（游戏按钮上铺的那种）。"""
    pts = [(0, -r), (r * k, -r * k), (r, 0), (r * k, r * k), (0, r), (-r * k, r * k), (-r, 0), (-r * k, -r * k)]
    path = QPainterPath()
    path.moveTo(cx + pts[0][0], cy + pts[0][1])
    for a, b in pts[1:]:
        path.lineTo(cx + a, cy + b)
    path.closeSubpath()
    return path


def sparkles(p: QPainter, rect: QRectF, color, count: int, seed: int, radius=(3.0, 7.0)) -> None:
    rng = random.Random(seed)
    for _ in range(max(0, count)):
        p.fillPath(sparkle_path(rng.uniform(rect.left(), rect.right()), rng.uniform(rect.top(), rect.bottom()),
                                rng.uniform(*radius)), qc(color))


def halftone(p: QPainter, rect: QRectF, color, step: float, rmax: float, origin: QPointF, reach: float) -> None:
    """网点：离 origin 越远点越小。步长或范围不是正数时不画（皮肤参数写错时不除零、不死循环）。"""
    if step <= 0 or reach <= 0:
        return
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(qc(color))
    y, row = rect.top(), 0
    while y <= rect.bottom() + step:
        x = rect.left() + (step / 2 if row % 2 else 0)
        while x <= rect.right() + step:
            r = rmax * (1 - math.hypot(x - origin.x(), y - origin.y()) / reach)
            if r > 0.35:
                p.drawEllipse(QPointF(x, y), r, r)
            x += step
        y += step
        row += 1
    p.restore()


def star_path(cx: float, cy: float, r: float, inner: float = 0.48, points: int = 5) -> QPainterPath:
    path = QPainterPath()
    for i in range(points * 2):
        a = math.radians(-90 + i * 180 / points)
        rr = r if i % 2 == 0 else r * inner
        pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


# ------------------------------------------------------------ 字


def index_font(px: float) -> QFont:
    """编号、热键这类数字（碳纤流光：数字字体的窄体）：皮肤的数字字体（没有时为正文字体），像素大小 px。"""
    font = QFont(theme.family("number") or theme.family("body"))
    font.setPixelSize(max(1, round(px)))
    font.setStretch(QFont.Condensed)
    font.setWeight(QFont.Medium)
    font.setItalic(False)  # 明确写成正体：画在斜体字的部件上时（画笔按部件的字体补全没写的属性）不跟着变斜
    return font


def italic_title_font(px: float) -> QFont:
    """斜着写的标题（碳纤流光的窗口标题、分组标题）：标题字体，像素大小 px，斜体（字体里没有斜体时由 Qt 斜着画）。"""
    font = theme.title_font(px)
    font.setItalic(True)
    return font


def px(font: QFont) -> int:
    """字的像素大小（字号按点写的字体也换算成像素）。"""
    return QFontInfo(font).pixelSize()


def text_path(text: str, font: QFont, rect: QRectF, align=Qt.AlignCenter) -> QPainterPath:
    """一行字的轮廓，按 align 放进 rect（左对齐、居中、右对齐；上下居中）。"""
    fm = QFontMetricsF(font)
    width = fm.horizontalAdvance(text)
    if align & Qt.AlignRight:
        x = rect.right() - width
    elif align & Qt.AlignLeft:
        x = rect.left()
    else:
        x = rect.left() + (rect.width() - width) / 2
    y = rect.top() + (rect.height() - (fm.ascent() + fm.descent())) / 2 + fm.ascent()
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    path.addText(QPointF(x, y), font, text)
    return path


def outlined_text(p: QPainter, rect: QRectF, text: str, font: QFont, fill, outline=None, width: float = 0.0,
                  align=Qt.AlignCenter) -> None:
    """一行描边字（游戏里的白字黑边）：先描边再填字。outline 为空时只填字。"""
    if not text:
        return
    path = text_path(text, font, rect, align)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if outline and width > 0:
        p.strokePath(path, QPen(qc(outline), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, brush(fill))
    p.restore()


# ------------------------------------------------------------ 大块的底


def backdrop(p: QPainter, rect: QRectF, look, text_right: float = 0.0, keep_out=()) -> None:
    """窗口底色：左下到右上的斜向渐变，铺细斜线（ChromeLook 的 BG、BG_LINE*）。ChromeLook.FRAME 为 board、stripes、neon、
    seaview 时换成那种底（board_window、stripes_window、neon_window、seaview_window）。text_right：顶上一行标题、说明最右边
    的字的位置，keep_out：顶上一行里的 ×（neon 的网点只铺在字的右边，并让开 ×）。"""
    if look.FRAME == "board":
        board_window(p, rect, look)
        return
    if look.FRAME == "stripes":
        stripes_window(p, rect, look)
        return
    if look.FRAME == "neon":
        neon_window(p, rect, look, text_right, keep_out)
        return
    if look.FRAME == "seaview":
        seaview_window(p, rect, look)
        return
    if look.FRAME == "carbon":  # 碳纤流光：切角的碳黑底、细斜纹、顶边七色灯线
        carbon_window(p, rect, look)
        return
    p.fillRect(rect, brush(gradient(rect.left(), rect.bottom(), rect.right(), rect.top(), look.BG)))
    if look.BG_LINE_STEP > 0:
        diag_lines(p, rect, look.BG_LINE, look.BG_LINE_STEP, look.BG_LINE_WIDTH)


STRIPE_FROM_RIGHT = 150  # 面板上沿：第一条斜带离面板右边
STRIPE_WIDTHS = (50, 28)  # 两条斜带各宽多少（横着量）
STRIPE_SLANT = 0.55  # 往下每走 1 像素，斜带往左挪多少


def panel_stripes(rect: QRectF, look) -> list[tuple[object, QPolygonF]]:
    """面板上两条斜带的（颜色, 四边形），从右上斜到左下。同一高度上斜带的位置只看离面板右边和上沿多远，面板变高、变宽都
    照这个规则（测试也按它找斜带经过哪里）。皮肤没有斜带（PANEL_STRIPES 为空）时为空表。"""
    out = []
    k = rect.height() * STRIPE_SLANT
    x0 = rect.right() - STRIPE_FROM_RIGHT
    for color, w in zip(look.PANEL_STRIPES, STRIPE_WIDTHS):
        out.append((color, QPolygonF([QPointF(x0, rect.top()), QPointF(x0 + w, rect.top()),
                                      QPointF(x0 + w - k, rect.bottom()), QPointF(x0 - k, rect.bottom())])))
        x0 += w
    return out


def panel(p: QPainter, rect: QRectF, look, seed: int = 3, stripes: bool = True, radius: float | None = None,
          fill=None) -> None:
    """深灰面板：圆角底、淡色几何小图案，右上压一黄一粉两条斜带（ChromeLook 的 PANEL_*，panel_stripes）。fill 给了颜色时
    代替底色；fill 为 False 时不画底色，只画图案和斜带（悬浮窗的面板底色在样式表里，带透明度）。斜带压在面板上直接写的字
    下面时，字垫底板（ChromeLook 的 PLATE_*），或者这时不画斜带（stripes 为 False）。"""
    r = look.PANEL_RADIUS if radius is None else radius
    path = rounded(rect, r)
    if fill is not False:
        p.fillPath(path, qc(fill if fill is not None else look.PANEL_FILL))
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    doodles(p, rect, look.PANEL_SHAPE, 2600, seed)
    if stripes:
        p.setPen(Qt.NoPen)
        for color, polygon in panel_stripes(rect, look):
            p.setBrush(qc(color))
            p.drawPolygon(polygon)
    p.restore()


@dataclass(frozen=True)
class CardMetrics:
    """一种卡片的尺寸：chrome.Card 的几何（卡片本身离左边的缩进 body_x、右边和下边留给投影的 right、shadow）；
    widgets.Card 从哪儿开始画：top 为 middle（照区域名标签的中线）、title（从标签上沿往下 frac 倍的标签高）、
    head（在卡片头下面 head_gap 像素）、edge（整张，四周内缩 inset）。"""

    body_x: int = 6
    right: int = 0
    shadow: int = 4
    top: str = "middle"
    frac: float = 0.0
    head_gap: int = 0
    inset: float = 0.0


# 卡片画法的统一签名：(p, rect, look, seed, radius, shadow)。用不到 seed、shadow 的画法照样接收、不使用。
def _card_gray(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    """浅灰卡片：下沿一截实心投影、圆角底、淡色底纹（ChromeLook 的 CARD_*）。rect 不含投影（画在 rect 下面 shadow 像素）。"""
    r = look.CARD_RADIUS if radius is None else radius
    path = rounded(rect, r)
    if shadow:
        hard_shadow(p, path, 0, shadow, look.CARD_EDGE)
    p.fillPath(path, qc(look.CARD_FILL))
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    doodles(p, rect, look.CARD_SHAPE, 1000, seed, size=(5, 8), width=2)
    p.restore()


def _card_indigo(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    indigo_box(p, rect, look, 16 if radius is None else min(radius, 18))


def _card_white(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    white_card(p, rect, look, look.CARD_RADIUS if radius is None else min(radius, look.CARD_RADIUS))


def _card_sticker(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    sticker_card(p, rect, look, look.CARD_RADIUS if radius is None else min(radius, look.CARD_RADIUS))


def _card_well(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    well_card(p, rect, look, look.CARD_RADIUS if radius is None else min(radius, look.CARD_RADIUS))


def _card_cut(p: QPainter, rect: QRectF, look, seed: int, radius, shadow: float) -> None:
    cut_card(p, rect, look)


# 卡片：种类 →（画法, 尺寸）。加一种卡片就是写一个画法、在这里加一行（ChromeLook.CARD_KIND 写它的种类）。
CARD_KINDS: dict[str, tuple] = {
    "gray": (_card_gray, CardMetrics()),
    "indigo": (_card_indigo, CardMetrics()),
    "white": (_card_white, CardMetrics(shadow=6, top="title", frac=0.35)),
    "sticker": (_card_sticker, CardMetrics(body_x=4, right=5, shadow=5, top="head", head_gap=7)),
    "well": (_card_well, CardMetrics(body_x=1, right=1, shadow=1, top="edge", inset=0.5)),
    "cut": (_card_cut, CardMetrics()),
}


def card_metrics(kind: str) -> CardMetrics:
    """这种卡片的尺寸；不认识的按浅灰卡片。"""
    return CARD_KINDS.get(kind, CARD_KINDS["gray"])[1]


def card(p: QPainter, rect: QRectF, look, seed: int = 11, radius: float | None = None, shadow: float = 4.0) -> None:
    """浅灰卡片：下沿一截实心投影、圆角底、淡色底纹（ChromeLook 的 CARD_*）。rect 不含投影（画在 rect 下面 shadow 像素）。
    按 ChromeLook.CARD_KIND 查 CARD_KINDS：indigo 靛紫网点的底、white 白卡片、sticker 贴纸卡片、well 白底细框、cut 切角面板。"""
    CARD_KINDS.get(look.CARD_KIND, CARD_KINDS["gray"])[0](p, rect, look, seed, radius, shadow)


def cached(owner, key: tuple, size, draw) -> QPixmap:
    """按 key 缓存一张画好的图（底纹这类每次都一样的东西）：owner 上只留最近一张。size 为逻辑像素，按 owner 的缩放比画。"""
    dpr = max(1.0, owner.devicePixelRatioF())
    full = (key, round(size.width()), round(size.height()), dpr)
    hit = getattr(owner, "_decor_cache", None)
    if hit is not None and hit[0] == full:
        return hit[1]
    pix = QPixmap(max(1, math.ceil(size.width() * dpr)), max(1, math.ceil(size.height() * dpr)))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    q = QPainter(pix)
    try:
        q.setRenderHint(QPainter.Antialiasing)
        draw(q)
    finally:  # 画的过程中出错也要结束画笔（不然 Qt 会告警）
        q.end()
    owner._decor_cache = (full, pix)
    return pix


# ------------------------------------------------------------ 小部件的样子


def _tag_default(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
                 px: int | None) -> None:
    """橙色圆角标签：上下渐变的圆角块、下沿一截投影，白字黑描边（ChromeLook 的 TAG_*）。rect 含投影的那一截（3 像素）。"""
    body = QRectF(rect.left(), rect.top(), rect.width(), rect.height() - 3)
    path = rounded(body, min(look.TAG_RADIUS, body.height() / 2))
    hard_shadow(p, path, 0, 3, look.TAG_SHADOW)
    p.fillPath(path, brush(gradient(0, body.top(), 0, body.bottom(), look.TAG_FILL)))
    outlined_text(p, body, text, font, look.TAG_TEXT, look.TAG_OUTLINE, max(2.5, QFontInfo(font).pixelSize() * 0.28))


def _tag_band(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
              px: int | None) -> None:
    band_tag(p, rect, text, font, look)


def _tag_led(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
             px: int | None) -> None:
    led_tag(p, rect, text, font, look, key)


def _tag_slant(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
               px: int | None) -> None:
    slant_tag(p, QRectF(rect.left(), rect.top(), rect.width(), rect.height() - 3), text, font,
              look.TAG_FILL[0], look.TAG_TEXT, look.SLANT)


def _tag_diamond(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
                 px: int | None) -> None:
    diamond_title(p, rect, text, font, look)


def _tag_index(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str, number: str,
               px: int | None) -> None:
    """编号标题，不画底（后面的线由用它的部件画，见 index_title、index_rule）。px 给了时（chrome.Tag）编号用数字字体的
    窄体、标题用斜体粗体，都按 px 的大小；没给时（标签以外的地方借这个画法写字）编号和标题都用传进来的 font。"""
    if px is None:
        index_title(p, rect, number, text, font, font, look)
        return
    index_title(p, rect, number, text, index_font(px), italic_title_font(px), look)


def _tag_size_default(font: QFont, text: str, number: str, px: int, look) -> QSize:
    fm = QFontMetricsF(font)
    return QSize(round(fm.horizontalAdvance(text) + 28), font.pixelSize() + 18 + 3)


def _tag_size_band(font: QFont, text: str, number: str, px: int, look) -> QSize:
    fm = QFontMetricsF(font)
    return QSize(round(fm.horizontalAdvance(text) + 50), font.pixelSize() + 15)


def _tag_size_led(font: QFont, text: str, number: str, px: int, look) -> QSize:
    fm = QFontMetricsF(font)
    return QSize(round(fm.horizontalAdvance(text) + 26), font.pixelSize() + 12)


def _tag_size_slant(font: QFont, text: str, number: str, px: int, look) -> QSize:
    fm = QFontMetricsF(font)
    return QSize(round(fm.horizontalAdvance(text) + 30 + look.SLANT), font.pixelSize() + 14 + 3)


def _tag_size_diamond(font: QFont, text: str, number: str, px: int, look) -> QSize:
    fm = QFontMetricsF(font)
    lead = 2 * 5 + 11 * (len(look.DIAMONDS) - 1) + 6 if look.DIAMONDS else 0
    return QSize(round(lead + fm.horizontalAdvance(text) + 8), font.pixelSize() + 12 + 3)


def _tag_size_index(font: QFont, text: str, number: str, px: int, look) -> QSize:
    lead = QFontMetricsF(index_font(px)).horizontalAdvance(number) + 10 if number else 0
    return QSize(round(lead + QFontMetricsF(italic_title_font(px)).horizontalAdvance(text) + 8), font.pixelSize() + 12)


# 标签：种类 →（画法, 尺寸）。画法统一签名 (p, rect, text, font, look, key, number, px)，px 可以是 None（见 tag）；
# 尺寸 (font, text, number, px, look) -> QSize（number、px 只有 index 用）。加一种标签就是写一个画法、在这里加一行
# （ChromeLook.TAG_KIND 写它的种类）。
TAG_KINDS: dict[str, tuple] = {
    "tag": (_tag_default, _tag_size_default),
    "slant": (_tag_slant, _tag_size_slant),
    "band": (_tag_band, _tag_size_band),
    "diamond": (_tag_diamond, _tag_size_diamond),
    "led": (_tag_led, _tag_size_led),
    "index": (_tag_index, _tag_size_index),
}


@dataclass(frozen=True)
class TagPlace:
    """一种标签画在卡片（chrome.Card）上时的摆法：inside 写在卡片里面（不露上沿）、band 整个在卡片上面、slant 往右缩进
    一点、index 是编号标题（卡片另画标题后面的线）。"""

    inside: bool = False
    band: bool = False
    slant: bool = False
    index: bool = False


TAG_PLACE: dict[str, TagPlace] = {
    "tag": TagPlace(),
    "slant": TagPlace(slant=True),
    "band": TagPlace(band=True),
    "diamond": TagPlace(inside=True),
    "led": TagPlace(inside=True),
    "index": TagPlace(inside=True, index=True),
}


def tag_place(kind: str) -> TagPlace:
    """这种标签画在卡片上时的摆法；不认识的按圆角标签。"""
    return TAG_PLACE.get(kind, TAG_PLACE["tag"])


def tag_size(kind: str, font: QFont, text: str, number: str, px: int, look) -> QSize:
    """这种标签的 sizeHint；不认识的按圆角标签。"""
    return TAG_KINDS.get(kind, TAG_KINDS["tag"])[1](font, text, number, px, look)


def tag(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str = "", number: str = "",
        px: int | None = None) -> None:
    """卡片左上角的标签：按 ChromeLook.TAG_KIND 查 TAG_KINDS（band 黑色斜带、led 小灯点加深色字、slant 黑色平行四边形、
    diamond 两颗小菱形加白字黑描边、index 编号标题，其余橙色圆角标签）。key 为组名（led 时决定灯点的颜色）；number、px 只给
    index：px 给了时编号、标题按 px 的大小用编号标题的字体（chrome.Tag），没给时都用 font（widgets.TagLabel 等）。"""
    TAG_KINDS.get(look.TAG_KIND, TAG_KINDS["tag"])[0](p, rect, text, font, look, key, number, px)


def tab(p: QPainter, rect: QRectF, text: str, font: QFont, look, on: bool, hover: bool = False) -> None:
    """左侧页签：左端圆头、右端直角的长块（没选中粉色，选中橙黄色），左半边铺网点，左端一颗白色圆点，白字黑描边。"""
    h = rect.height() - 3
    body = QRectF(rect.left(), rect.top(), rect.width(), h)
    path = rounded(QRectF(body.left() + h / 2, body.top(), body.width() - h / 2, h), 4)
    path = path.united(rounded(QRectF(body.left(), body.top(), h, h), h / 2))
    hard_shadow(p, path, 0, 3, (0, 0, 0, 45))
    fill = qc(look.TAB_ON_FILL if on else look.TAB_FILL)
    if hover and not on:
        fill = fill.lighter(108)
    p.fillPath(path, fill)
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    halftone(p, QRectF(body.left(), body.top(), body.width() * 0.5, h), look.TAB_ON_DOTS if on else look.TAB_DOTS,
             6, 2.6, QPointF(body.left(), body.top() + h / 2), body.width() * 0.5)
    p.restore()
    knob = QPointF(body.left() + h / 2 + 1, body.top() + h / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawEllipse(knob + QPointF(0, 1.5), h * 0.26, h * 0.26)
    p.setBrush(qc(look.TAB_KNOB))
    p.drawEllipse(knob, h * 0.26, h * 0.26)
    left = body.left() + h * 0.95
    outlined_text(p, QRectF(left, body.top(), body.right() - left - 6, h), text, font, look.TAB_TEXT, look.TAB_OUTLINE,
                  max(3.0, px(font) * 0.24))


def back_button(p: QPainter, rect: QRectF, text: str, font: QFont, look, hover: bool = False,
                pressed: bool = False) -> None:
    """左上角「返回」：左边一个箭头的圆角块，白边，蓝色渐变（鼠标移上去青到绿），黑字白描边。rect 含投影（4 像素）。"""
    x, y, w, h = rect.left() + 4, rect.top() + 4, rect.width() - 8, rect.height() - 12
    if pressed:
        y += 2
    body = rounded(QRectF(x + 22, y, w - 22, h), 12)
    arrow = QPainterPath()
    arrow.addPolygon(QPolygonF([QPointF(x, y + h / 2), QPointF(x + 34, y - 2), QPointF(x + 34, y + h + 2)]))
    arrow.closeSubpath()
    path = body.united(arrow)
    if not pressed:
        hard_shadow(p, path, 0, 4, (0, 0, 0, 70))
    p.strokePath(path, QPen(qc(look.BACK_BORDER), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, brush(gradient(x, y, x + w, y + h, look.BACK_HOVER if hover else look.BACK_FILL)))
    outlined_text(p, QRectF(x + 30, y, w - 34, h), text, font, look.BACK_TEXT, look.BACK_OUTLINE,
                  max(3.0, px(font) * 0.26))


def pill_button(p: QPainter, rect: QRectF, look, kind: str = "main", hover: bool = False, pressed: bool = False,
                enabled: bool = True, seed: int = 1, radius: float | None = None) -> QRectF:
    """胶囊按钮的底（字由调用的地方写）：main 粉色铺星星、下沿一截深色做厚度；light 白色；warn 警告色（深色底，左端一个
    「!」圆标由调用的地方画）。按下时整块下沉到厚度那一截。返回字该写在的区域。rect 含厚度（4 像素）。"""
    depth = 4
    top = rect.top() + (depth if pressed else 0)
    body = QRectF(rect.left(), top, rect.width(), rect.height() - depth)
    r = body.height() / 2 if radius is None else min(radius, body.height() / 2)
    path = rounded(body, r)
    if kind == "light":
        fill, edge = qc(look.LIGHT_FILL), qc(look.LIGHT_EDGE)
    elif kind == "warn":
        fill = qc(look.WARN_FILL)
        if hover:
            fill = fill.lighter(160)
        edge = QColor(0, 0, 0, 90)
    else:
        fill = qc(look.BUTTON_DISABLED if not enabled else look.BUTTON_HOVER if hover else look.BUTTON_FILL)
        edge = qc(look.BUTTON_EDGE)
    if not pressed:
        hard_shadow(p, path, 0, depth, edge if enabled else fill.darker(130))
    p.fillPath(path, fill)
    if kind == "main" and enabled and look.BUTTON_SPARKLE:
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        sparkles(p, body, look.BUTTON_SPARKLE, int(body.width() / 7), seed)
        p.restore()
    return body


def badge(p: QPainter, center: QPointF, radius: float, fill, glyph: str, glyph_color, font: QFont) -> None:
    """圆标：实心圆里一个字（警告前面的「!」）。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(qc(fill))
    p.drawEllipse(center, radius, radius)
    p.setFont(font)
    p.setPen(qc(glyph_color))
    p.drawText(QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius), Qt.AlignCenter, glyph)
    p.restore()


# ------------------------------------------------------------ 经典主题-黑（卡牌商店）、经典主题-反色（结算看板）


def slant(rect: QRectF, k: float) -> QPainterPath:
    """平行四边形：上边比下边往右错开 k（游戏结算界面的标签、按钮、页签、警告条）。"""
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(rect.left() + k, rect.top()), QPointF(rect.right(), rect.top()),
                               QPointF(rect.right() - k, rect.bottom()), QPointF(rect.left(), rect.bottom())]))
    path.closeSubpath()
    return path


def checker(p: QPainter, path: QPainterPath, base, square, step: float) -> None:
    """棋盘格：path 里铺 base，再隔一格铺一块 square（游戏「卡牌商店」面板的深色棋盘格）。步长不是正数时只铺底色。"""
    p.fillPath(path, qc(base))
    if step <= 0:
        return
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    p.setPen(Qt.NoPen)
    p.setBrush(qc(square))
    r = path.boundingRect()
    y, row = r.top(), 0
    while y < r.bottom():
        x = r.left() + (step if row % 2 else 0)
        while x < r.right():
            p.drawRect(QRectF(x, y, step, step))
            x += 2 * step
        y += step
        row += 1
    p.restore()


def ridge(p: QPainter, rect: QRectF, look) -> None:
    """一排竖纹（游戏面板顶边那一排）：深色底上隔 7 像素一条 3 像素宽的浅色竖条（ChromeLook.RIDGE）。"""
    dark, light = look.RIDGE
    p.fillRect(rect, qc(dark))
    p.save()
    p.setClipRect(rect, Qt.IntersectClip)
    p.setPen(Qt.NoPen)
    p.setBrush(qc(light))
    x = rect.left()
    while x < rect.right():
        p.drawRect(QRectF(x, rect.top() + 3, 3, max(0.0, rect.height() - 6)))
        x += 7
    p.restore()


def board_window(p: QPainter, rect: QRectF, look) -> None:
    """经典主题-黑的窗口底（游戏「卡牌商店」面板）：整块外框色，里面从 INNER_TOP 往下一圈棋盘格（四周留 6 像素外框），棋盘格
    顶边一排竖纹（RIDGE_HEIGHT 高），一圈很淡的白线。上面留出的那一截给压在上沿正中的标题页签（chrome.WindowHead）。"""
    p.fillRect(rect, qc(look.FRAME_FILL))
    inner_rect = QRectF(rect.left() + 6, rect.top() + look.INNER_TOP, rect.width() - 12,
                        max(0.0, rect.height() - look.INNER_TOP - 6))
    inner = rounded(inner_rect, 15)
    checker(p, inner, look.CHECK_FILL[0], look.CHECK_FILL[1], look.CHECK_STEP)
    p.save()
    p.setClipPath(inner, Qt.IntersectClip)
    ridge(p, QRectF(inner_rect.left(), inner_rect.top(), inner_rect.width(), look.RIDGE_HEIGHT), look)
    p.restore()
    p.strokePath(inner, QPen(QColor(255, 255, 255, 40), 1.4))


def board_panel(p: QPainter, rect: QRectF, look, top: float = 0.0, ridge_at: str = "top", alpha: float = 1.0,
                border=None, dashed: bool = False) -> None:
    """经典主题-黑的悬浮窗面板：圆角外框（FRAME_FILL）里一圈棋盘格，竖纹在顶边（ridge_at 为 top，全部展开时：标题页签、
    按钮压在上面）或左边一窄条（left，一行显示时：摘要写在面板上，字不压在竖纹上）。top：外框从这么高开始画（上面留给
    压在上沿正中的标题页签）。alpha：整块的不透明度（悬浮窗按不透明画、传 1，透明度在整个窗口上做）。border：另描一圈框（需要操作时的警告色，2 像素；
    dashed 为暂停时的虚线）。"""
    body = QRectF(rect.left(), rect.top() + top, rect.width(), max(0.0, rect.height() - top))
    outer = rounded(body, look.FRAME_RADIUS)
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.fillPath(outer, qc(look.FRAME_FILL))
    inner_rect = body.adjusted(6, 6, -6, -6)
    inner = rounded(inner_rect, max(4, look.FRAME_RADIUS - 5))
    step = look.CHECK_STEP if ridge_at == "top" else max(10, look.CHECK_STEP * 2 // 3)
    checker(p, inner, look.CHECK_FILL[0], look.CHECK_FILL[1], step)
    p.save()
    p.setClipPath(inner, Qt.IntersectClip)
    if ridge_at == "top":
        ridge(p, QRectF(inner_rect.left(), inner_rect.top(), inner_rect.width(), look.RIDGE_HEIGHT - 2), look)
    elif ridge_at == "left":
        ridge(p, QRectF(inner_rect.left(), inner_rect.top(), 10, inner_rect.height()), look)
    p.restore()
    p.strokePath(inner, QPen(QColor(255, 255, 255, 40), 1.4))
    p.restore()
    if border is not None:
        pen = QPen(qc(border), 2, Qt.DashLine if dashed else Qt.SolidLine)
        p.strokePath(rounded(body.adjusted(1, 1, -1, -1), look.FRAME_RADIUS - 1), pen)


def wide_stripes(p: QPainter, rect: QRectF, look, alpha: float = 1.0) -> None:
    """经典主题-反色的底（游戏结算界面）：蓝紫粉斜向渐变（BG），叠一浅一深交替的宽斜条（STRIPE_FILL、STRIPE_STEP）。
    步长不是正数时只铺底色（不死循环）。"""
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.fillRect(rect, brush(gradient(rect.left(), rect.bottom(), rect.right(), rect.top(), look.BG)))
    if look.STRIPE_STEP <= 0:
        p.restore()
        return
    p.setClipRect(rect, Qt.IntersectClip)
    p.setPen(Qt.NoPen)
    light, dark = look.STRIPE_FILL
    step, h = float(look.STRIPE_STEP), rect.height()
    x, i = rect.left() - h, 0
    while x < rect.right() + h:
        p.setBrush(qc(light if i % 2 else dark))
        p.drawPolygon(QPolygonF([QPointF(x, rect.bottom()), QPointF(x + step * 0.5, rect.bottom()),
                                 QPointF(x + step * 0.5 + h, rect.top()), QPointF(x + h, rect.top())]))
        x += step
        i += 1
    p.restore()


def band(p: QPainter, rect: QRectF, look) -> None:
    """顶上的黑色通栏（游戏三选一界面「请选择一枚战斗筹码」那一条），下沿一条很淡的细线（BAND_FILL、BAND_LINE）。"""
    p.fillRect(rect, qc(look.BAND_FILL))
    p.fillRect(QRectF(rect.left(), rect.bottom() - 1.5, rect.width(), 1.0), qc(look.BAND_LINE))


def stripes_window(p: QPainter, rect: QRectF, look) -> None:
    """经典主题-反色的窗口底：宽斜条的底，顶上 HEAD_HEIGHT 高的黑色通栏（标题、页签、× 写在通栏里）。"""
    wide_stripes(p, rect, look)
    band(p, QRectF(rect.left(), rect.top(), rect.width(), look.HEAD_HEIGHT), look)


def stripes_panel(p: QPainter, rect: QRectF, look, band_h: float, alpha: float = 1.0, border=None,
                  dashed: bool = False) -> None:
    """经典主题-反色的悬浮窗面板：圆角的宽斜条底，顶上 band_h 高的黑色通栏（标题栏一行写在通栏里）；border 见 board_panel。"""
    path = rounded(rect, look.FRAME_RADIUS)
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    wide_stripes(p, rect, look, alpha)
    p.setOpacity(p.opacity() * alpha)
    band(p, QRectF(rect.left(), rect.top(), rect.width(), band_h), look)
    p.restore()
    p.strokePath(path, QPen(QColor(0, 0, 0), 2.5))
    if border is not None:
        pen = QPen(qc(border), 2, Qt.DashLine if dashed else Qt.SolidLine)
        p.strokePath(rounded(rect.adjusted(1.5, 1.5, -1.5, -1.5), look.FRAME_RADIUS - 1), pen)


def title_tab(p: QPainter, rect: QRectF, text: str, font: QFont, look) -> None:
    """压在上沿正中的标题页签（游戏「卡牌商店」的粉色页签）：粉色圆角块、白边、两角铺网点，白字黑描边，下沿一截投影
    （TITLE_*）。rect 含白边（四周 3 像素）和投影（下沿 4 像素）。"""
    body = QRectF(rect.left() + 3, rect.top() + 3, rect.width() - 6, rect.height() - 3 - 4)
    path = rounded(body, 12)
    hard_shadow(p, path, 0, 4, (0, 0, 0, 120))
    p.strokePath(path, QPen(qc(look.TITLE_BORDER), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, qc(look.TITLE_FILL))
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    reach = body.width() * 0.45
    halftone(p, body, look.TITLE_DOTS, 6, 2.4, QPointF(body.left(), body.bottom()), reach)
    halftone(p, body, look.TITLE_DOTS, 6, 2.4, QPointF(body.right(), body.top()), reach)
    p.restore()
    outlined_text(p, body, text, font, look.TITLE_TEXT, look.TITLE_OUTLINE, max(3.0, px(font) * 0.26))


def diamonds(p: QPainter, x: float, cy: float, colors, r: float = 5.0, gap: float = 11.0) -> float:
    """几颗小菱形（游戏里的菱形指示，粉、黄）：从 x 起往右排，上下以 cy 为中心。返回占的宽。"""
    if not colors:
        return 0.0
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    for i, c in enumerate(colors):
        cx = x + r + i * gap
        p.setBrush(qc(c))
        p.drawPolygon(QPolygonF([QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r), QPointF(cx - r, cy)]))
    p.restore()
    return 2 * r + gap * (len(colors) - 1)


def diamond_title(p: QPainter, rect: QRectF, text: str, font: QFont, look) -> float:
    """分组标题：左边两颗小菱形（DIAMONDS），后面白字黑描边（TAG_TEXT、TAG_OUTLINE），不画底。返回字的右边缘。"""
    cy = rect.top() + rect.height() / 2
    used = diamonds(p, rect.left() + 1, cy, look.DIAMONDS) + 6 if look.DIAMONDS else 0.0
    area = QRectF(rect.left() + used, rect.top(), max(0.0, rect.width() - used), rect.height())
    outlined_text(p, area, text, font, look.TAG_TEXT, look.TAG_OUTLINE, max(2.5, px(font) * 0.24), align=Qt.AlignLeft)
    return area.left() + QFontMetricsF(font).horizontalAdvance(text)


def slant_tag(p: QPainter, rect: QRectF, text: str, font: QFont, fill, ink, k: float = 9.0, marker=None,
              align=Qt.AlignCenter) -> None:
    """黑色平行四边形的小标签（卡片左上角、块标题）：fill 底、ink 字；marker 给了颜色时字前面先画一小块这种颜色的斜块
    （块标题的档位颜色）。"""
    p.fillPath(slant(rect, k), qc(fill))
    left = rect.left() + k * 0.5 + 4
    if marker is not None:
        mark = QRectF(rect.left() + k * 0.9, rect.top() + rect.height() * 0.22, 9, rect.height() * 0.56)
        p.fillPath(slant(mark, 4), qc(marker))
        left = mark.right() + 6
    area = QRectF(left, rect.top(), max(0.0, rect.right() - k * 0.5 - 4 - left), rect.height())
    outlined_text(p, area, text, font, ink, align=align if marker is None else Qt.AlignLeft)


def ribbon(p: QPainter, rect: QRectF, fill, border=None, notch: float = 10.0) -> None:
    """彩色缎带（块标题，经典主题-黑）：长方块，右端剪出一个三角缺口；border 给了颜色时描一圈细框。"""
    path = QPainterPath()
    path.addPolygon(QPolygonF([rect.topLeft(), QPointF(rect.right(), rect.top()),
                               QPointF(rect.right() - notch, rect.center().y()), QPointF(rect.right(), rect.bottom()),
                               rect.bottomLeft()]))
    path.closeSubpath()
    p.fillPath(path, qc(fill))
    if border is not None:
        p.strokePath(path, QPen(qc(border), 1.6, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))


def warn_triangle(p: QPainter, center: QPointF, size: float, fill, glyph_color, font: QFont) -> None:
    """三角形的警告标：fill 颜色的三角里一个「!」（经典主题-反色）。size 为三角的高。"""
    cx, cy = center.x(), center.y()
    w = size * 1.15
    tri = QPolygonF([QPointF(cx, cy - size * 0.55), QPointF(cx + w / 2, cy + size * 0.45),
                     QPointF(cx - w / 2, cy + size * 0.45)])
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(qc(fill), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(qc(fill))
    p.drawPolygon(tri)
    p.setFont(font)
    p.setPen(qc(glyph_color))
    p.drawText(QRectF(cx - w / 2, cy - size * 0.2, w, size * 0.62), Qt.AlignCenter, "!")
    p.restore()


def close_mark(p: QPainter, rect: QRectF, look, hover: bool = False, pressed: bool = False) -> None:
    """右上角的 ×（CLOSE_*）：CLOSE_FILL 有颜色时画成圆角方块里的 ×（经典主题-黑），空时只画 ×；CLOSE_OUTLINE 有颜色时
    × 描一圈边（经典主题-反色：黑色通栏上的白 ×）。鼠标移上去 × 换成 CLOSE_GLYPH_HOVER。"""
    r = QRectF(rect)
    if pressed:
        r.translate(0, 1)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if look.CLOSE_FILL:
        inner = r.adjusted(1, 1, -1, -1)
        box = capsule(inner) if look.CLOSE_ROUND else rounded(inner, 8)  # CLOSE_ROUND：圆（白色海景房）
        if look.CHAMFER and not look.CLOSE_ROUND:  # 碳纤流光：切角的方块
            box = chamfer(inner, min(look.CHAMFER * 0.6, inner.height() * 0.3))
        p.fillPath(box, qc(look.CLOSE_HOVER if hover and look.CLOSE_HOVER else look.CLOSE_FILL))
        p.strokePath(box, QPen(qc(look.CLOSE_BORDER), 1.4))
    c = r.center()
    s = r.width() * (0.17 if look.CLOSE_ROUND else 0.22 if look.CLOSE_FILL else 0.26)
    lines = [(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s)),
             (QPointF(c.x() + s, c.y() - s), QPointF(c.x() - s, c.y() + s))]
    if look.CLOSE_OUTLINE:
        p.setPen(QPen(qc(look.CLOSE_OUTLINE), 7.5, Qt.SolidLine, Qt.RoundCap))
        for a, b in lines:
            p.drawLine(a, b)
    width = look.CLOSE_PEN or (3.8 if look.CLOSE_OUTLINE else 3.0)
    p.setPen(QPen(qc(look.CLOSE_GLYPH_HOVER if hover else look.CLOSE_GLYPH), width, Qt.SolidLine, Qt.RoundCap))
    for a, b in lines:
        p.drawLine(a, b)
    p.restore()


def indigo_box(p: QPainter, rect: QRectF, look, radius: float = 18.0) -> None:
    """靛紫渐变铺网点的底（游戏「查看筹码」面板，ChromeLook.INDIGO）：经典主题-黑的推荐区、编辑方案的表单、导入/导出的两块。
    网点只铺在左上、右下两角，往里越来越小，很淡。"""
    c1, c2, dots = look.INDIGO
    path = rounded(rect, radius)
    p.fillPath(path, brush(gradient(rect.left(), rect.top(), rect.right(), rect.bottom(), (c1, c2))))
    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    reach = max(rect.width(), rect.height())
    halftone(p, rect, dots, 9, 3.2, rect.topLeft(), reach * 0.7)
    halftone(p, rect, dots, 9, 3.2, rect.bottomRight(), reach * 0.5)
    p.restore()
    p.strokePath(path, QPen(QColor(255, 255, 255, 35), 1.2))


def white_card(p: QPainter, rect: QRectF, look, radius: float = 16.0) -> None:
    """白色圆角卡片，下面一截软投影（经典主题-反色，CARD_FILL、CARD_SHADOW）。rect 不含投影（画在下面 5 像素）。"""
    path = rounded(rect, radius)
    hard_shadow(p, path, 0, 5, look.CARD_SHADOW)
    p.fillPath(path, qc(look.CARD_FILL))


def banner(p: QPainter, rect: QRectF, look) -> QRectF:
    """底部的洋红横幅（游戏结算界面写地图名的那一条）：洋红渐变的平行四边形、黑框（BANNER_*）。返回字该写在的区域。"""
    k = 14.0
    path = slant(rect, k)
    p.fillPath(path, brush(gradient(rect.left(), rect.top(), rect.right(), rect.top(), look.BANNER_FILL)))
    p.strokePath(path, QPen(qc(look.BANNER_BORDER), 2, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
    return rect.adjusted(k + 10, 0, -(k + 10), 0)


def star_sticker(p: QPainter, center: QPointF, r: float, look) -> None:
    """首推图标右上角贴的一枚星星：白色圆底上一颗黄星、黑边（STAR_FILL、STAR_OUTLINE）。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(center, r, r)
    star = star_path(center.x(), center.y() + 0.5, r * 0.78, 0.48)
    p.strokePath(star, QPen(qc(look.STAR_OUTLINE), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(star, qc(look.STAR_FILL))
    p.restore()


def glow_ring(p: QPainter, center: QPointF, radius: float, color, width: float = 3.0) -> None:
    """首推图标外面一圈光（GLOW）：几圈由淡到浓往里收的描边，最里面一圈实线。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    base = qc(color)
    for grow, alpha in ((7.0, 35), (5.0, 60), (3.0, 100)):
        c = QColor(base)
        c.setAlpha(alpha)
        p.setPen(QPen(c, 3.0))
        p.drawEllipse(center, radius + grow, radius + grow)
    p.setPen(QPen(base, width))
    p.drawEllipse(center, radius + 1.5, radius + 1.5)
    p.restore()


def best_badge(p: QPainter, rect: QRectF, look) -> None:
    """首推的斜角标（游戏结算界面的「BEST」，经典主题-反色）：橙色平行四边形、黑框，里面一颗白星。"""
    path = slant(rect, 7)
    p.fillPath(path, qc(look.BEST_FILL))
    p.strokePath(path, QPen(QColor(0, 0, 0), 1.6, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
    star = star_path(rect.center().x(), rect.center().y() + 0.5, rect.height() * 0.36, 0.46)
    p.fillPath(star, qc(look.STAR_FILL))
    p.strokePath(star, QPen(qc(look.STAR_OUTLINE), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))


def button_style(look, kind: str) -> tuple:
    """按钮种类的颜色（ChromeLook.BUTTON_KINDS）：（底, 投影, 框线, 框线宽, 字, 字的描边, 左边画不画警告标）；没写的种类照 main
    或第一种。"""
    kinds = look.BUTTON_KINDS
    style = kinds.get(kind) or kinds.get("primary") or next(iter(kinds.values()), None)
    if style is None:
        return (look.BUTTON_FILL, look.BUTTON_EDGE, "", 0, look.BUTTON_TEXT, "", False)
    return tuple(style) + (False,) * (7 - len(style))


def game_button(p: QPainter, rect: QRectF, look, kind: str, hover: bool = False, pressed: bool = False,
                enabled: bool = True) -> QRectF:
    """经典主题-黑、反色的按钮的底（字由调用的地方写）：ChromeLook.BUTTON_SHAPE 为 rounded（圆角块、下沿一截投影）或 slant
    （平行四边形、右下错开一截投影）。按下时整块下沉到投影那一截；不能点时颜色变淡。返回字该写在的区域。rect 含投影（4）。
    BUTTON_SHAPE 为 pill 时见 pill_button_body。"""
    if look.BUTTON_SHAPE == "pill":
        return pill_button_body(p, rect, look, kind, hover, pressed, enabled)
    if look.BUTTON_SHAPE == "cut":  # 碳纤流光：切角的按钮，主按钮外面一圈光
        return cut_button(p, rect, look, kind, hover, pressed, enabled)
    fill, shadow, border, border_w, _, _, _ = button_style(look, kind)
    depth = 4.0
    slanted = look.BUTTON_SHAPE == "slant"
    dx = 3.0 if slanted else 0.0
    top = rect.top() + (depth if pressed else 0)
    body = QRectF(rect.left(), top, rect.width() - dx, rect.height() - depth)
    path = slant(body, min(9.0, body.height() * 0.24)) if slanted else rounded(body, min(12.0, body.height() * 0.32))
    color = qc(fill)
    if not enabled:
        color.setAlpha(110)
    elif hover:
        color = color.lighter(110)
    if not pressed and shadow:
        hard_shadow(p, path, dx, depth, shadow if enabled else (0, 0, 0, 60))
    p.fillPath(path, color)
    if border and border_w:
        p.strokePath(path, QPen(qc(border), border_w, Qt.SolidLine, Qt.RoundCap, Qt.MiterJoin))
    return body


def pill_button_body(p: QPainter, rect: QRectF, look, kind: str, hover: bool = False, pressed: bool = False,
                     enabled: bool = True) -> QRectF:
    """胶囊按钮的底（BUTTON_SHAPE 为 pill：霓虹贴纸粗黑描边、右下一截实心投影，白色海景房白底细框或紫粉渐变）：颜色按
    BUTTON_KINDS（底可以是几种颜色的渐变；投影色为空时不画投影），投影错开 BUTTON_SHADOW。按下时整块挪到投影的位置；不能点
    时颜色变淡。返回字该写在的区域。rect 含投影。"""
    fill, shadow, border, border_w, _, _, _ = button_style(look, kind)
    dx, dy = look.BUTTON_SHADOW if shadow else (0, 0)
    shift = (dx, dy) if pressed else (0, 0)
    body = QRectF(rect.left() + shift[0], rect.top() + shift[1], rect.width() - dx, rect.height() - dy)
    path = capsule(body)
    if not pressed and shadow and (dx or dy):
        hard_shadow(p, path, dx, dy, shadow if enabled else (0, 0, 0, 60))
    colors = colors_of(fill)
    if not enabled or hover:
        tinted = []
        for c in colors:
            q = qc(c)
            if not enabled:
                q.setAlpha(110)
            else:
                q = q.lighter(108) if q.lightnessF() > 0.5 else q.lighter(125)
            tinted.append((q.red(), q.green(), q.blue(), q.alpha()))
        colors = tinted
    p.fillPath(path, fill_brush(tuple(colors) if len(colors) > 1 else colors[0], body))
    if border and border_w:
        p.strokePath(path, QPen(qc(border), border_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    return body


def game_tab(p: QPainter, rect: QRectF, text: str, font: QFont, look, on: bool, hover: bool = False) -> None:
    """页签（ChromeLook.TAB_*）：pill 圆角块，选中的白边、往上高出一截、下沿一截投影，没选中的深色细框；slant 平行四边形，
    选中的亮色黑框。rect 是整个页签占的地方（选中时用满，没选中时 pill 往下缩 6 像素）。"""
    fill, ink, border, outline = look.TABS_ON if on else look.TABS_OFF
    if look.TAB_KIND == "slant":
        body = rect.adjusted(0, 2, 0, -2)
        path = slant(body, 12)
        color = qc(fill)
        if hover and not on:
            color = color.lighter(140)
        p.fillPath(path, color)
        if border:
            p.strokePath(path, QPen(qc(border), 2, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
        area = body.adjusted(6, 0, -6, 0)
    else:
        body = rect.adjusted(3, 3 if on else 9, -3, -4)
        path = rounded(body, 12)
        if on:
            if look.TABS_SHADOW:
                hard_shadow(p, path, 0, 4, look.TABS_SHADOW)
            if border:
                p.strokePath(path, QPen(qc(border), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        color = qc(fill)
        if hover and not on:
            color = color.lighter(140)
        p.fillPath(path, color)
        if border and not on:
            p.strokePath(path, QPen(qc(border), 1.4))
        area = body
    outlined_text(p, area, text, font, ink, outline or None, max(3.0, px(font) * 0.22) if outline else 0.0)


# ------------------------------------------------------------ 碳纤流光（碳黑底、切角细线框、七色灯带、发光）


def chamfer(rect: QRectF, cut: float) -> QPainterPath:
    """切角的方块：切掉左上、右下两个角，切口 cut（不超过宽、高的一半）。cut 为 0 时就是直角方块。"""
    c = max(0.0, min(cut, rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(rect.left() + c, rect.top()), QPointF(rect.right(), rect.top()),
                               QPointF(rect.right(), rect.bottom() - c), QPointF(rect.right() - c, rect.bottom()),
                               QPointF(rect.left(), rect.bottom()), QPointF(rect.left(), rect.top() + c)]))
    path.closeSubpath()
    return path


def halo(p: QPainter, path: QPainterPath, color, strength: float = 1.0) -> None:
    """外面一圈光（选中的方块、主按钮）：几圈由淡到浓往里收的描边。画在底的下面，底随后盖住描边在里面的那一半。"""
    if not color:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    for width, alpha in ((9.0, 20), (6.0, 38), (3.0, 72)):
        c = qc(color)
        c.setAlpha(max(0, min(255, round(alpha * strength))))
        p.setPen(QPen(c, width, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
        p.drawPath(path)
    p.restore()


def rgb_brush(rect: QRectF, colors, vertical: bool = False) -> QBrush:
    """灯带的渐变（几种颜色均匀分布）：从左到右，vertical 时从上到下。"""
    if vertical:
        return brush(gradient(rect.left(), rect.top(), rect.left(), rect.bottom(), colors))
    return brush(gradient(rect.left(), rect.top(), rect.right(), rect.top(), colors))


def carbon_fill(p: QPainter, path: QPainterPath, rect: QRectF, look, alpha: float = 1.0) -> None:
    """碳黑底（FRAME_FILL）铺细斜纹（BG_LINE、BG_LINE_STEP、BG_LINE_WIDTH），只画在 path 里；alpha 为整块的不透明度。"""
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.fillPath(path, qc(look.FRAME_FILL))
    if look.BG_LINE_STEP > 0:
        p.setClipPath(path, Qt.IntersectClip)
        diag_lines(p, rect, look.BG_LINE, look.BG_LINE_STEP, look.BG_LINE_WIDTH)
    p.restore()


def corner_mark(p: QPainter, rect: QRectF, look) -> None:
    """右下角的记号：底边一小截亮色（HALO），切掉的角里三道短斜线（CARD_EDGE）。rect 是切角方块本身。"""
    if not look.HALO:
        return
    cut = max(4.0, float(look.CHAMFER))
    p.save()
    p.fillRect(QRectF(rect.right() - cut - 28, rect.bottom() - 2.5, 18, 2.5), qc(look.HALO))
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(qc(look.CARD_EDGE), 1.2))
    for k in (0.35, 0.6, 0.85):  # 平行于切口的三道
        d = cut * k
        p.drawLine(QPointF(rect.right() - d + 2, rect.bottom() + 1), QPointF(rect.right() + 1, rect.bottom() - d + 2))
    p.restore()


def carbon_window(p: QPainter, rect: QRectF, look) -> None:
    """碳纤流光的窗口底：切掉的两个角是黑色，里面切角的碳黑底铺细斜纹（carbon_fill）、细线外框（CARD_EDGE），顶边一条七色
    灯线（RGB），右下角的记号（corner_mark）。"""
    p.fillRect(rect, QColor(0, 0, 0))
    body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    cut = look.CHAMFER + 6
    path = chamfer(body, cut)
    carbon_fill(p, path, body, look)
    p.strokePath(path, QPen(qc(look.CARD_EDGE), 1.2))
    if look.RGB:
        line = QRectF(body.left() + cut + 18, body.top() + 1.5, max(0.0, body.width() - cut - 40), 3)
        p.fillRect(line, rgb_brush(line, look.RGB))
    corner_mark(p, body, look)


def carbon_panel(p: QPainter, rect: QRectF, look, alpha: float = 1.0, border=None, dashed: bool = False) -> None:
    """碳纤流光的悬浮窗面板：切角的碳黑底铺细斜纹（alpha 为不透明度）、细线外框，左边一条竖着的七色灯带（RGB），右下角的
    记号。border：需要操作时另描一圈警告色的框；dashed：识别暂停时外框画成虚线。"""
    body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    cut = float(look.CHAMFER)
    path = chamfer(body, cut)
    carbon_fill(p, path, body, look, alpha)
    p.strokePath(path, QPen(qc(look.CARD_EDGE), 1.2, Qt.DashLine if dashed and border is None else Qt.SolidLine))
    if look.RGB:
        strip = QRectF(body.left() + 3, body.top() + cut + 4, 3, max(0.0, body.height() - 2 * cut - 8))
        p.fillRect(strip, rgb_brush(strip, look.RGB, vertical=True))
    corner_mark(p, body, look)
    if border is not None:
        p.strokePath(chamfer(body.adjusted(1.5, 1.5, -1.5, -1.5), max(0.0, cut - 1)),
                     QPen(qc(border), 2, Qt.DashLine if dashed else Qt.SolidLine))


def cut_card(p: QPainter, rect: QRectF, look, cut: float | None = None) -> None:
    """切角的面板（CARD_KIND 为 cut）：CARD_FILL 的底、CARD_EDGE 的细框。"""
    path = chamfer(rect.adjusted(0.5, 0.5, -0.5, -0.5), look.CHAMFER if cut is None else cut)
    p.fillPath(path, qc(look.CARD_FILL))
    p.strokePath(path, QPen(qc(look.CARD_EDGE), 1.0))


def rgb_logo(p: QPainter, rect: QRectF, colors) -> None:
    """四色小方块（标题前）：2×2 四块小圆角方块，取灯带的第 1、4、5、7 种颜色（左上、右上、左下、右下）。"""
    if not colors:
        return
    s = min(rect.width(), rect.height())
    gap = max(1.5, s * 0.14)
    cell = (s - gap) / 2
    x0, y0 = rect.left() + (rect.width() - s) / 2, rect.top() + (rect.height() - s) / 2
    picks = [colors[min(i, len(colors) - 1)] for i in (0, 3, 4, 6)]
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for i, color in enumerate(picks):
        cx, cy = x0 + (i % 2) * (cell + gap), y0 + (i // 2) * (cell + gap)
        p.fillPath(rounded(QRectF(cx, cy, cell, cell), cell * 0.22), qc(color))
    p.restore()


def index_title(p: QPainter, rect: QRectF, number: str, text: str, number_font: QFont, font: QFont, look) -> float:
    """分组标题（碳纤流光）：编号（INDEX_INK，number_font：数字字体的窄体）、标题（TAG_TEXT，font：斜体粗体）。返回字的
    右边缘。number 为空时只写标题。"""
    x = rect.left()
    if number:
        fm = QFontMetricsF(number_font)
        width = fm.horizontalAdvance(number)
        p.save()
        p.setFont(number_font)
        p.setPen(qc(look.INDEX_INK or look.TAG_TEXT))
        p.drawText(QRectF(x, rect.top(), width + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, number)
        p.restore()
        x += width + 10
    flat_text(p, QRectF(x, rect.top(), max(0.0, rect.right() - x), rect.height()), text, font, look.TAG_TEXT)
    return x + QFontMetricsF(font).horizontalAdvance(text)


def flat_text(p: QPainter, rect: QRectF, text: str, font: QFont, color, align=Qt.AlignLeft) -> None:
    """一行不描边的字，上下居中（drawText 画：斜体的字体里没有斜体字形时，由字体引擎斜着画）。color 可以是 QColor。"""
    if not text:
        return
    p.save()
    p.setFont(font)
    p.setPen(color if isinstance(color, QColor) else qc(color))
    p.drawText(rect, align | Qt.AlignVCenter, text)
    p.restore()


def index_rule(p: QPainter, left: float, right: float, y: float, look) -> None:
    """分组标题后面的线：一小截亮色（HALO），接一条细线（RULE）到 right。放不下 24 像素时不画。"""
    if right - left < 24:
        return
    accent = min(18.0, (right - left) / 3)
    p.save()
    p.fillRect(QRectF(left, y - 1, accent, 2), qc(look.HALO or look.RULE))
    p.fillRect(QRectF(left + accent, y - 0.5, right - left - accent, 1), qc(look.RULE))
    p.restore()


def cut_button(p: QPainter, rect: QRectF, look, kind: str, hover: bool = False, pressed: bool = False,
               enabled: bool = True) -> QRectF:
    """切角的按钮的底（BUTTON_SHAPE 为 cut；字由调用的地方写）：颜色按 BUTTON_KINDS（底、光、框线、框线宽），光的颜色写了时
    外面一圈光（主按钮）。鼠标移上去底亮一点、没有光的框线换成 HALO；按下时底暗一点；不能点时整块变淡。rect 四周留 3 像素
    给光。返回字该写在的区域。"""
    fill, glow, border, border_w, _, _, _ = button_style(look, kind)
    body = rect.adjusted(3, 3, -3, -3)
    path = chamfer(body, min(look.CHAMFER * 0.7, body.height() * 0.3))
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if not enabled:
        p.setOpacity(p.opacity() * 0.45)
    if glow and enabled:
        halo(p, path, glow, 0.6 if pressed else 1.0)
    color = qc(fill)
    if pressed:
        color = color.darker(125)
    elif hover and enabled:
        color = color.lighter(135) if color.lightnessF() < 0.5 else color.lighter(110)
    p.fillPath(path, color)
    if border and border_w:
        line = look.HALO if hover and enabled and look.HALO and not glow else border
        p.strokePath(path, QPen(qc(line), border_w, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
    p.restore()
    return body


def cut_warn(p: QPainter, rect: QRectF, look) -> None:
    """切角的警告框（WARN_SHAPE 为 cut）：很淡的底（WARN_FILL）、细框（WARN_BORDER，没写时同字的颜色）。"""
    path = chamfer(rect.adjusted(0.5, 0.5, -0.5, -0.5), min(look.CHAMFER * 0.8, rect.height() * 0.3))
    p.fillPath(path, qc(look.WARN_FILL))
    p.strokePath(path, QPen(qc(look.WARN_BORDER or look.WARN_TEXT), 1.2))


def cut_plate(p: QPainter, rect: QRectF, fill, line, width: float, glow=None) -> QPainterPath:
    """切角的小牌子（悬浮窗推荐区的位次牌、「使用中」等）：glow 写了颜色时外面一圈光。返回牌子的形状。"""
    path = chamfer(rect, min(7.0, rect.height() * 0.3))
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if glow:
        halo(p, path, glow, 0.8)
    if fill:
        p.fillPath(path, qc(fill))
    if line and width:
        p.strokePath(path, QPen(qc(line), width, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
    p.restore()
    return path


# ------------------------------------------------------------ 霓虹贴纸（贴纸）、白色海景房（浅色）


def colors_of(value) -> list:
    """皮肤里写的颜色：一种（"#rrggbb"、(r, g, b, a)）或几种（渐变）→ 颜色的列表。"""
    if isinstance(value, (tuple, list)) and value and not isinstance(value[0], int):
        return list(value)
    return [value]


def fill_brush(fill, rect: QRectF, vertical: bool = False) -> QBrush:
    """一种颜色的底，或几种颜色从左到右（vertical 时从上到下）渐变的底。"""
    if vertical:
        return brush(gradient(rect.left(), rect.top(), rect.left(), rect.bottom(), fill))
    return brush(gradient(rect.left(), rect.top(), rect.right(), rect.top(), fill))


def soft_shadow(p: QPainter, path: QPainterPath, dx: float, dy: float, radius: float, color) -> None:
    """软阴影：把 path 填成 color，模糊 radius 像素，错开 (dx, dy) 画在下面（白色海景房的卡片、选中的方块）。"""
    from PySide6.QtGui import QImage

    if radius <= 0.5:
        hard_shadow(p, path, dx, dy, color)
        return
    scale = max(1.0, p.device().devicePixelRatioF()) if p.device() is not None else 1.0
    area = path.boundingRect().adjusted(-2.2 * radius, -2.2 * radius, 2.2 * radius, 2.2 * radius)
    w, h = max(1, math.ceil(area.width() * scale)), max(1, math.ceil(area.height() * scale))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing)
    q.scale(scale, scale)
    q.translate(-area.left(), -area.top())
    q.fillPath(path, qc(color))
    q.end()
    k = max(1.0, radius * scale / 1.6)
    for _ in range(2):  # 缩小再放大两遍：粗略的模糊，够画阴影
        small = img.scaled(max(1, round(w / k)), max(1, round(h / k)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img.setDevicePixelRatio(scale)
    p.drawImage(QPointF(area.left() + dx, area.top() + dy), img)


def sticker(p: QPainter, path: QPainterPath, fill, look, pen: float = 2.2, shadow: tuple = (4, 4),
            rect: QRectF | None = None) -> None:
    """贴纸（霓虹贴纸）：右下一截实心投影、底（一种颜色或渐变）、粗黑描边，颜色 ChromeLook.INK。"""
    rect = rect or path.boundingRect()
    if shadow and (shadow[0] or shadow[1]):
        hard_shadow(p, path, shadow[0], shadow[1], look.INK)
    p.fillPath(path, fill_brush(fill, rect))
    if pen:
        p.strokePath(path, QPen(qc(look.INK), pen, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))


def halftone_corners(p: QPainter, rect: QRectF, look, head: float, text_right: float, keep_out=(),
                     strip: float | None = None) -> None:
    """霓虹贴纸的淡紫网点（ChromeLook.HALFTONE），不压在字下面：右上角的只铺在顶上 head 高的一行里、text_right（标题、说明
    最右边的字）往右，并让开 keep_out 里的几块（×、按钮）；左下角的只铺在左边、下边贴边的一圈（宽 strip，没给时按 HALFTONE
    的最后一项），内容都在这一圈以里。"""
    if not look.HALFTONE:
        return
    color, step, rmax, reach_tr, reach_bl, edge_strip = look.HALFTONE
    strip = edge_strip if strip is None else strip
    p.save()
    top = QPainterPath()
    left = max(rect.left(), text_right + 16)
    if head > 0 and left < rect.right():
        top.addRect(QRectF(left, rect.top(), rect.right() - left, head))
        for box in keep_out:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(box).adjusted(-4, -4, 4, 4), 6, 6)
            top = top.subtracted(hole)
        p.setClipPath(top, Qt.IntersectClip)
        halftone(p, rect, color, step, rmax, rect.topRight(), reach_tr)
    p.restore()
    p.save()
    edge = QPainterPath()
    edge.addRect(QRectF(rect.left(), rect.top() + head, strip, max(0.0, rect.height() - head)))
    edge.addRect(QRectF(rect.left(), rect.bottom() - strip, rect.width(), strip))
    p.setClipPath(edge, Qt.IntersectClip)
    halftone(p, rect, color, step, rmax, rect.bottomLeft(), reach_bl)
    p.restore()


def edge_line(p: QPainter, path: QPainterPath, rect: QRectF, look, dashed: bool = False) -> None:
    """窗口一圈的框线（ChromeLook.EDGE：一种颜色或从左上到右下渐变的几种颜色）。dashed：识别暂停时的虚线。"""
    if not look.EDGE:
        return
    colors, width = look.EDGE
    pen = QPen(brush(gradient(rect.left(), rect.top(), rect.right(), rect.bottom(), colors)), width,
               Qt.DashLine if dashed else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.strokePath(path, pen)


def neon_window(p: QPainter, rect: QRectF, look, text_right: float = 0.0, keep_out=()) -> None:
    """霓虹贴纸的窗口底：夜紫上下渐变（BG），网点（halftone_corners：顶上一行 HEAD_HEIGHT 高），一圈墨黑框线（EDGE）。"""
    p.fillRect(rect, fill_brush(look.BG, rect, vertical=True))
    halftone_corners(p, rect, look, look.HEAD_HEIGHT, text_right, keep_out)
    width = look.EDGE[1] if look.EDGE else 0
    edge_line(p, rounded(rect.adjusted(width / 2, width / 2, -width / 2, -width / 2), 2), rect, look)


def glows(p: QPainter, path: QPainterPath, rect: QRectF, look, alpha: float = 1.0) -> None:
    """白色海景房角落透出的光晕（ChromeLook.GLOWS：圆心、半径按 rect 的宽高算），只画在 path 里。"""
    from PySide6.QtGui import QRadialGradient

    p.save()
    p.setClipPath(path, Qt.IntersectClip)
    for fx, fy, fr, color, opacity in look.GLOWS:
        center = QPointF(rect.left() + rect.width() * fx, rect.top() + rect.height() * fy)
        g = QRadialGradient(center, max(1.0, rect.width() * fr))
        c0, c1 = qc(color), qc(color)
        c0.setAlphaF(opacity * alpha)
        c1.setAlphaF(0.0)
        g.setColorAt(0, c0)
        g.setColorAt(1, c1)
        p.fillRect(rect, QBrush(g))
    p.restore()


def seaview_window(p: QPainter, rect: QRectF, look) -> None:
    """白色海景房的窗口底：浅灰白（BG），角落透出光晕（GLOWS），一圈彩色渐变灯边（EDGE）。"""
    p.fillRect(rect, fill_brush(look.BG, rect))
    path = QPainterPath()
    path.addRect(rect)
    glows(p, path, rect, look)
    width = look.EDGE[1] if look.EDGE else 0
    edge_line(p, rounded(rect.adjusted(width / 2, width / 2, -width / 2, -width / 2), 2), rect, look)


def neon_panel(p: QPainter, rect: QRectF, look, line: bool, alpha: float = 1.0, border=None, dashed: bool = False,
               head: float = 0.0, text_right: float = 0.0, keep_out=()) -> None:
    """霓虹贴纸的悬浮窗面板：圆角的夜紫渐变、右下一截实心投影、墨黑粗框；全部展开时铺网点（顶上 head 高的一行里
    text_right 往右、让开 keep_out 里的 ×、按钮，左边下边贴边的一圈；一行显示时不铺）。border：需要操作时的警告色框（dashed
    为暂停时的虚线）。"""
    body = rect.adjusted(1.5, 1.5, -6.5, -6.5)
    path = rounded(body, 14 if line else look.FRAME_RADIUS)
    hard_shadow(p, path, 5, 5, look.INK)
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.fillPath(path, fill_brush(look.BG, body, vertical=True))
    p.restore()
    if not line:  # 面板的内边距比窗口的窄：左边、下边的网点只铺贴边的 8 像素，不碰到底栏的按钮
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        halftone_corners(p, body, look, head, text_right, keep_out, strip=8)
        p.restore()
    width = look.EDGE[1] if look.EDGE else 2.6
    p.strokePath(path, QPen(qc(look.INK), width, Qt.DashLine if dashed and border is None else Qt.SolidLine))
    if border is not None:
        p.strokePath(rounded(body.adjusted(2.5, 2.5, -2.5, -2.5), max(4.0, (14 if line else look.FRAME_RADIUS) - 2)),
                     QPen(qc(border), 2, Qt.DashLine if dashed else Qt.SolidLine))


def seaview_panel(p: QPainter, rect: QRectF, look, line: bool, alpha: float = 1.0, border=None,
                  dashed: bool = False) -> None:
    """白色海景房的悬浮窗面板：浅灰白、角落透出光晕、一圈彩色渐变灯边；一行显示时两头是半圆（胶囊）。border：
    需要操作时的警告色框；dashed：暂停时灯边画成虚线。"""
    body = rect.adjusted(2, 2, -2, -2)
    radius = body.height() / 2 if line else look.FRAME_RADIUS
    path = rounded(body, radius)
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.fillPath(path, fill_brush(look.BG, body))
    p.restore()
    glows(p, path, body, look, alpha)
    edge_line(p, path, body, look, dashed=dashed and border is None)
    if border is not None:
        p.strokePath(rounded(body.adjusted(2.5, 2.5, -2.5, -2.5), max(4.0, radius - 2.5)),
                     QPen(qc(border), 2, Qt.DashLine if dashed else Qt.SolidLine))


def sticker_card(p: QPainter, rect: QRectF, look, radius: float = 14.0) -> None:
    """霓虹贴纸的卡片：深紫底（CARD_FILL）、粗黑描边、右下一截实心投影，上沿里面一条很淡的高光。rect 不含投影（画在右下
    4 像素）。"""
    path = rounded(rect, radius)
    sticker(p, path, look.CARD_FILL, look, 2.2, (4, 4), rect)
    p.setPen(QPen(QColor(255, 255, 255, 22), 1))
    p.drawLine(QPointF(rect.left() + radius, rect.top() + 2.5), QPointF(rect.right() - radius, rect.top() + 2.5))


def well_card(p: QPainter, rect: QRectF, look, radius: float = 14.0) -> None:
    """白色海景房的卡片：白底（CARD_FILL，略透明）、一圈浅灰细框（CARD_EDGE）。"""
    path = rounded(rect, radius)
    p.fillPath(path, qc(look.CARD_FILL))
    p.strokePath(path, QPen(qc(look.CARD_EDGE), 1))


def band_tag(p: QPainter, rect: QRectF, text: str, font: QFont, look) -> None:
    """霓虹贴纸的分组标题：黑色斜带（平行四边形，TAG_FILL），左头一小截黄（TAG_NOTCH），白字（TAG_TEXT）写在小截后面。"""
    k = look.SLANT
    p.fillPath(slant(rect, k), qc(look.TAG_FILL[0]))
    if look.TAG_NOTCH:
        p.fillPath(slant(QRectF(rect.left() + k + 4, rect.top() + 6, 8, max(4.0, rect.height() - 12)), 5),
                   qc(look.TAG_NOTCH))
    area = QRectF(rect.left() + 26, rect.top(), max(0.0, rect.width() - 26), rect.height())
    outlined_text(p, area, text, font, look.TAG_TEXT, look.TAG_OUTLINE or None, 2.5 if look.TAG_OUTLINE else 0.0,
                  align=Qt.AlignLeft)


def led_dot(p: QPainter, center: QPointF, color, r: float = 4.0) -> None:
    """一颗小灯点（白色海景房各组标题前）：实心圆，外面一圈很淡的光。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    for grow, alpha in ((4.0, 28), (2.4, 55), (1.2, 90)):
        c = qc(color)
        c.setAlpha(alpha)
        p.setBrush(c)
        p.drawEllipse(center, r + grow, r + grow)
    p.setBrush(qc(color))
    p.drawEllipse(center, r, r)
    p.restore()


def led_tag(p: QPainter, rect: QRectF, text: str, font: QFont, look, key: str = "") -> None:
    """白色海景房的组标题：前面一颗小灯点（颜色按组，ChromeLook.LED_COLORS；没写的组用强调色），后面深色字（TAG_TEXT）。"""
    color = look.LED_COLORS.get(key) or (colors_of(look.ACCENT)[0] if look.ACCENT else look.TAG_TEXT)
    led_dot(p, QPointF(rect.left() + 6, rect.center().y()), color)
    outlined_text(p, QRectF(rect.left() + 18, rect.top(), max(0.0, rect.width() - 18), rect.height()), text, font,
                  look.TAG_TEXT, align=Qt.AlignLeft)


def logo(p: QPainter, rect: QRectF, look) -> None:
    """白色海景房标题前的小方块：几种灯带颜色的渐变（ChromeLook.LOGO），中间一块白色。"""
    s = min(rect.width(), rect.height())
    box = QRectF(rect.left(), rect.top() + (rect.height() - s) / 2, s, s)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.fillPath(rounded(box, s * 0.32), brush(gradient(box.left(), box.top(), box.right(), box.bottom(), look.LOGO)))
    inset = s * 0.27
    p.fillPath(rounded(box.adjusted(inset, inset, -inset, -inset), s * 0.14), QColor(255, 255, 255))
    p.restore()


def logo_title(p: QPainter, rect: QRectF, text: str, font: QFont, look, size: float = 22.0) -> None:
    """标题前一个小方块（logo）、后面深色字（TITLE_TEXT，不描边）：白色海景房的窗口标题、悬浮窗标题。"""
    logo(p, QRectF(rect.left() + 2, rect.center().y() - size / 2, size, size), look)
    outlined_text(p, QRectF(rect.left() + size + 10, rect.top(), max(0.0, rect.width() - size - 10), rect.height()),
                  text, font, look.TITLE_TEXT, align=Qt.AlignLeft)
