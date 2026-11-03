"""系统图标（Windows 自带的 Segoe Fluent Icons，Windows 10 上是 Segoe MDL2 Assets，编号相同）的画法：找字体、画一个图标、
把图标做成按钮前面的小图标。

设置窗口各行的图标、皮肤窗口的说明图标、悬浮窗「筹码方案」卡片的按钮都用这些画法。放这里是为了让底层的部件（chrome）借用
图标画法时不必反过来导入设置窗口；settings/ 也再导出这些名字，别处和测试从 settings 取也一样。测试要替换（patch）
时换这里的名字（例如 glyphs.icon_family）：settings 里的只是指向同一个函数的另一个名字，换掉它，这里的画法照样用这里的。

只依赖 Qt 和底层的装饰模块（色号解析 decor.qc）：不导入设置、悬浮窗这些上层窗口。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

from .decor import qc as color

ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")  # 按顺序找第一个系统里有的
ICON_SETTINGS = 0xE713  # 齿轮：窗口顶部大标题旁
ICON_MODE = 0xE890  # 眼睛：识别模式
ICON_HOTKEY = 0xE765  # 键盘：识别热键
ICON_CAPTURE = 0xE7F4  # 显示器：识别画面
ICON_LOWEND = 0xE8BE  # 叶子：低配模式（Windows 任务管理器的「效率模式」也用叶子表示少占处理器）
ICON_SKIN = 0xE771  # 画笔：皮肤
ICON_LOOK = 0xE790  # 调色板：悬浮窗背景
ICON_DIAG = 0xE9D9  # 诊断：诊断包
ICON_UPDATE = 0xE895  # 两个转圈的箭头（Windows 设置里「Windows 更新」也用它）：启动时自动检查更新
ICON_ABOUT = 0xE946  # 信息：关于拾星助手
ICON_CHEVRON = 0xE76C  # 向右的箭头：整行可点的一行右端

_icon_family: str | None = None
_icon_checked = False


def icon_family() -> str | None:
    """系统里的图标字体（ICON_FAMILIES 里第一个有的）；都没有时为 None。第一次用到时查一次（要先有 QApplication）。"""
    global _icon_family, _icon_checked
    if not _icon_checked:
        families = set(QFontDatabase.families())
        _icon_family = next((f for f in ICON_FAMILIES if f in families), None)
        _icon_checked = True
    return _icon_family


def glyph_font(px: int) -> QFont | None:
    """画系统图标用的字体（像素大小 px）；系统里没有图标字体时为 None（调用的地方不画图标、只写字）。不用别的字体顶替：
    图标的编号在别的字体里没有对应的字。"""
    family = icon_family()
    if family is None:
        return None
    font = QFont(family)
    font.setPixelSize(px)
    font.setStyleStrategy(QFont.NoFontMerging)
    return font


def draw_glyph(painter: QPainter, rect: QRectF, code: int, px: int, fill: str | QColor) -> bool:
    """在 rect 里居中画系统图标 code（像素大小 px，颜色 fill）。没有图标字体时不画，返回 False。"""
    font = glyph_font(px)
    if font is None:
        return False
    painter.save()
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(fill if isinstance(fill, QColor) else color(fill))
    painter.drawText(rect, Qt.AlignCenter, chr(code))
    painter.restore()
    return True


GLYPH_RATIOS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)  # 做按钮图标时按这几种缩放比各画一张


def glyph_icon(code: int, fill: str | QColor, px: int) -> QIcon | None:
    """系统图标 code 做成按钮前面的小图标（px 逻辑像素见方，颜色 fill）：按几种缩放比各画一张（GLYPH_RATIOS），高分屏上
    按实际的像素取，不发虚。系统里没有图标字体时为 None（按钮只写字）。"""
    if glyph_font(px) is None:
        return None
    icon = QIcon()
    for ratio in GLYPH_RATIOS:
        size = round(px * ratio)
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        draw_glyph(p, QRectF(0, 0, size, size), code, size, fill)
        p.end()
        icon.addPixmap(pix)
    return icon
