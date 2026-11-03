"""程序图标：金色筹码标志——深色圆盘、金色圆环、正中一颗金色四角星，96 像素以上沿外圈写 ASTRAL CHIP COMPASS。

和使用说明网页页头是同一个图形。exe 图标（tools/build_release.py 按 ICON_SIZES 分别画出 packaging/app.ico）、悬浮窗和
设置、关于、编辑方案、导入/导出各窗口的标题栏、系统托盘都由 render_icon 画出，各处一致。托盘和标题栏的尺寸（16～32 像素）
不写外圈的字（小了只剩一圈杂点），圆环和星星加粗放大，16 像素也认得出；任务栏、桌面的尺寸（33～95 像素）也不写外圈的字，
圆环和星星取中间的一档；96 像素以上照设计稿画，外圈的字拉开排满一整圈。

外圈的字在打 exe 图标（离屏平台、没有字体文件夹）、跑测试（离屏平台、字体文件夹指向系统字体）和程序运行（Windows 平台）
三种环境里要画出同一个样子，字体由 _outer_font 自己保证：先 Bahnschrift，不在字体库里就现装
%WINDIR%\\Fonts\\bahnschrift.ttf；装不上退回 Segoe UI Semibold（seguisb.ttf，同样现装）；都没有时用 Qt 的默认字体，
不报错。每个进程只找一次。
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen
from PySide6.QtGui import QPixmap, QTransform

DISC = QColor("#1d1f2b")  # 圆盘（整块底色）
RING = QColor("#f2b232")  # 圆环
STAR = QColor("#ffd36e")  # 正中那颗四角星
LETTERS = QColor("#a9aec0")  # 外圈的字
CANVAS = 74  # 设计稿边长；原点在画布中心，y 轴向下
DISC_RADIUS = 36  # 圆盘半径，画布上四边各留 1 个单位
LETTERS_MIN = 96  # 到这个尺寸才写外圈的字：更小的尺寸上字只剩一圈杂点
LETTERS_TEXT = "ASTRAL CHIP COMPASS · ASTRAL CHIP COMPASS ·"
LETTERS_SIZE = 5.6  # 字号，设计稿单位
LETTERS_RADIUS = 29.5  # 字所在的基线圆半径
LETTERS_LENGTH = 182.0  # 整圈字拉开到的总长（只加大字距，不拉宽字形）
LETTERS_PX = 100  # 量字宽用的像素大小（设计稿单位 = 量出的宽度 × LETTERS_SIZE / LETTERS_PX）
FONT_FILES = {"Bahnschrift": "bahnschrift.ttf", "Segoe UI": "seguisb.ttf"}  # 字体库里没有时现装的字体文件（Segoe UI 装的是 Semibold 那一款，字重 600 取的就是它）
BOLD_PX, BOLD_BOX, BOLD_GLYPH = 32, 48, "A"  # 核对字重：参考字形画成这么大的图，数有颜色的像素
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)  # exe 图标里的各个尺寸（Windows 按显示缩放挑选）


def _params(size: int) -> tuple[float, float, float]:
    """（圆环中线半径、圆环线宽、星星放大倍数），单位是设计稿的单位。
    托盘、标题栏（16～32 像素）上环和星星细得看不清，加粗放大；任务栏、桌面（33～95 像素）取中间的一档。"""
    if size <= 32:
        return 23, 9, 1.3
    if size < LETTERS_MIN:
        return 24, 7, 1.1
    return 24, 6, 1.0


def _star_path() -> QPainterPath:
    """四角星：四段对称的三次曲线，四个尖在正上、正右、正下、正左。"""
    path = QPainterPath()
    path.moveTo(0, -12)
    path.cubicTo(1.2, -4.2, 4.2, -1.2, 12, 0)
    path.cubicTo(4.2, 1.2, 1.2, 4.2, 0, 12)
    path.cubicTo(-1.2, 4.2, -4.2, 1.2, -12, 0)
    path.cubicTo(-4.2, -1.2, -1.2, -4.2, 0, -12)
    path.closeSubpath()
    return path


STAR_PATH = _star_path()

_font: QFont | None = None
_letters: QPainterPath | None = None


def _loaded(family: str) -> bool:
    """family 在字体库里能不能用；没有就现装它对应的字体文件（每个进程每种字体只装一次）。"""
    if QFontDatabase.hasFamily(family):
        return True
    font_file = FONT_FILES.get(family)
    if font_file is None:
        return False
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    QFontDatabase.addApplicationFont(str(fonts / font_file))
    return QFontDatabase.hasFamily(family)


def _ink(font: QFont) -> int:
    """把参考字形画进一张小图、数有颜色的像素：笔画变粗时这个数变大。"""
    small = QFont(font)
    small.setPixelSize(BOLD_PX)
    img = QImage(BOLD_BOX, BOLD_BOX, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(small)
    p.setPen(Qt.black)
    p.drawText(img.rect(), Qt.AlignCenter, BOLD_GLYPH)
    p.end()
    return sum(img.pixelColor(x, y).alpha() > 127 for y in range(BOLD_BOX) for x in range(BOLD_BOX))


def _outer_font() -> QFont:
    """外圈字用的字体（每个进程只找一次）：先 Bahnschrift，再退回 Segoe UI Semibold，都没有时用 Qt 的默认字体。"""
    global _font
    if _font is None:
        family = next((name for name in FONT_FILES if _loaded(name)), None)
        font = QFont(family) if family else QFont()
        font.setPixelSize(LETTERS_PX)
        font.setWeight(QFont.Weight.DemiBold)
        thin = QFont(font)
        thin.setWeight(QFont.Weight.Normal)
        if _ink(font) <= _ink(thin) and hasattr(font, "setVariableAxis"):
            # Bahnschrift 是可变字体，字重由 wght 轴决定：光设 QFont 的字重没画出更粗的笔画时改用这个轴
            font.setVariableAxis(QFont.Tag("wght"), QFont.Weight.DemiBold)
        _font = font
    return _font


def _letters_path() -> QPainterPath:
    """外圈字整条的路径（设计稿单位，和尺寸无关，每个进程只算一次）。

    字排在半径 LETTERS_RADIUS 的圆上：起点在圆的最左点，顺时针走（先往上过顶部，再从右边下来，最后沿底部回到左边），
    所以顶上的字正着读、底下的字倒过来，每个字的上方朝外。整条拉到 LETTERS_LENGTH 长，多出来的距离平均加在每个字后面，
    只加大字距、不拉宽字形。
    """
    global _letters
    if _letters is None:
        font = _outer_font()
        metrics = QFontMetricsF(font)
        k = LETTERS_SIZE / LETTERS_PX
        widths = [metrics.horizontalAdvance(ch) * k for ch in LETTERS_TEXT]
        extra = (LETTERS_LENGTH - sum(widths)) / len(LETTERS_TEXT)
        path = QPainterPath()
        s = 0.0
        for ch, width in zip(LETTERS_TEXT, widths):
            angle = math.pi + (s + width / 2) / LETTERS_RADIUS
            glyph = QPainterPath()
            glyph.addText(0, 0, font, ch)
            place = (QTransform()
                     .translate(LETTERS_RADIUS * math.cos(angle), LETTERS_RADIUS * math.sin(angle))
                     .rotate(math.degrees(angle) + 90)
                     .scale(k, k)
                     .translate(-width / k / 2, 0))
            path.addPath(place.map(glyph))
            s += width + extra
        _letters = path
    return _letters


def render_icon(size: int) -> QImage:
    """画一张 size × size 的图标（透明底）。size 不是正数时按 1 画（画空图没有意义）。"""
    size = max(1, round(size))
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    ring_r, ring_w, star = _params(size)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(size / 2, size / 2)
    p.scale(size / CANVAS, size / CANVAS)
    p.setPen(Qt.NoPen)
    p.setBrush(DISC)
    p.drawEllipse(QPointF(0, 0), DISC_RADIUS, DISC_RADIUS)
    if size >= LETTERS_MIN:
        p.setBrush(LETTERS)
        p.drawPath(_letters_path())
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(RING, ring_w))
    p.drawEllipse(QPointF(0, 0), ring_r, ring_r)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(STAR)
    p.scale(star, star)
    p.drawPath(STAR_PATH)
    p.restore()
    p.end()
    return img


def app_icon() -> QIcon:
    """悬浮窗的窗口和托盘图标：和 exe 图标同一套图，按尺寸分别画。"""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon
