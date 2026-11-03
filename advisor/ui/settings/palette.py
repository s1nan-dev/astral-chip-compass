"""设置窗口里调色盘图标的交互：一颗星从调色盘图标飞进「皮肤」一行（StarFlight）、那一行闪一下金光（RowFlash），
以及图标外面的一层金色外框（PaletteFrame、PaletteTrap）和五角星的画法（star_path）。

颜色取默认皮肤的选中色（theme.DEFAULT.settings.SELECTED_COLOR，金色），不随皮肤变。界面文字（PALETTE_TEXT、
OPAQUE_TITLE）在 dialog.py，从它导入。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .. import theme
from ..text import tip_paragraphs_html
from ..theme import SETTINGS as L
from ..widgets import TextLabel, color
from .dialog import OPAQUE_TITLE, PALETTE_TEXT


# ------------------------------------------------------------ 调色盘图标

STAR_MS = 700  # 一颗星从调色盘图标飞进「皮肤」一行要多久
FLASH_MS = 900  # 「皮肤」一行闪一下金光要多久
STAR_GOLD = theme.DEFAULT.settings.SELECTED_COLOR  # 星和金光的颜色：默认皮肤的选中色（金色），不随皮肤变
PALETTE_PEN = 2.0  # 调色盘图标外面那层金色外框的线宽


def _valid(widget) -> bool:
    """Qt 那边的部件还在（窗口内容重建时旧的部件会被删掉）。"""
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
    except ImportError:
        return True
    return isValid(widget)


def star_path(center: QPointF, radius: float, turn: float = 0.0) -> QPainterPath:
    """五角星（外接圆半径 radius，内凹处 0.45 倍），turn 是转过的角度（度）。"""
    path = QPainterPath()
    for i in range(10):
        r = radius if i % 2 == 0 else radius * 0.45
        angle = math.radians(turn - 90 + 36 * i)
        point = QPointF(center.x() + r * math.cos(angle), center.y() + r * math.sin(angle))
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


class PaletteFrame(QWidget):
    """调色盘图标外面的一层金色外框（鼠标移上去时，PaletteTrap）：盖在图标上、不接鼠标，颜色同选中的模式方块
    （SELECTED_COLOR）。"""

    def __init__(self, icon: QWidget):
        super().__init__(icon)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(icon.rect())
        self.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        half = PALETTE_PEN / 2
        rect = QRectF(self.rect()).adjusted(half, half, -half, -half)
        radius = min(rect.width(), rect.height()) * 0.24
        p.setPen(QPen(color(L.SELECTED_COLOR), PALETTE_PEN))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        p.end()


class PaletteTrap(QObject):
    """解锁「霓虹贴纸」皮肤的机关，装在「悬浮窗背景不透明」那一行左边的调色盘图标（icon）上。mode（unlock.palette_mode）为
    hint 时鼠标移上去图标加一层金色外框（PaletteFrame）、出悬停横幅 PALETTE_TEXT、手形光标；quiet 时这些都没有。点一下这一行
    的标题（title）换成 PALETTE_TEXT，再点一下标题恢复，调用 on_unlock（设置窗口请悬浮窗解锁、飞星）。reset：标题恢复、
    重新数（窗口关了时）。"""

    def __init__(self, icon: QWidget, title: TextLabel, mode: str, on_unlock):
        super().__init__(icon)
        self.icon, self.title, self.mode, self.on_unlock = icon, title, mode, on_unlock
        self.clicks = 0
        self.frame = PaletteFrame(icon)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, False)  # 平时图标不接鼠标（点在字上算点整行）
        icon.setAttribute(Qt.WA_Hover, True)
        if mode == "hint":
            icon.setCursor(Qt.PointingHandCursor)
            icon.setToolTip(tip_paragraphs_html(PALETTE_TEXT))
        icon.installEventFilter(self)

    @property
    def framed(self) -> bool:
        """金色外框显示着（鼠标在图标上、hint 时）。"""
        return not self.frame.isHidden()

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.Enter:
            self.hover(True)
        elif kind == QEvent.Leave:
            self.hover(False)
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            return True  # 不往下传：自己画外框的窗口里按在空白处会拖动窗口
        elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self.icon.rect().contains(event.position().toPoint()):
                self.click()
            return True
        return False

    def hover(self, on: bool) -> None:
        """鼠标移上图标、移开：hint 时出、收金色外框和悬停横幅。"""
        if self.mode != "hint":
            return
        self.frame.setVisible(on)
        if on:
            spot = self.icon.mapToGlobal(QPoint(0, self.icon.height() + 4))
            QToolTip.showText(spot, tip_paragraphs_html(PALETTE_TEXT), self.icon)
        else:
            QToolTip.hideText()

    def click(self) -> None:
        """点了一下：第一下这一行的标题换成 PALETTE_TEXT；第二下标题恢复，解锁（on_unlock）。"""
        self.clicks += 1
        if self.clicks == 1:
            self.title.setText(PALETTE_TEXT)
            return
        self.reset()
        self.on_unlock()

    def reset(self) -> None:
        self.clicks = 0
        if self.title.text() != OPAQUE_TITLE:
            self.title.setText(OPAQUE_TITLE)


class StarFlight(QWidget):
    """一颗金色的星从 start 飞到 end（设置窗口里的坐标），沿一条向上拱的弧线，边飞边转、中途大一些；飞到以后自己删掉、调用
    done。盖在整个窗口上、不接鼠标。"""

    def __init__(self, parent: QWidget, start: QPointF, end: QPointF, done=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.start, self.end, self.done = QPointF(start), QPointF(end), done
        self.t = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(STAR_MS)
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._landed)
        self.show()
        self.raise_()
        self.anim.start()

    def point(self, t: float) -> QPointF:
        """飞到 t（0～1）时星的位置：二次贝塞尔曲线，控制点在两端的右上方。"""
        a, b = self.start, self.end
        lift = max(40.0, abs(b.y() - a.y()) * 0.6 + 30)
        ctrl = QPointF((a.x() + b.x()) / 2 + 60, min(a.y(), b.y()) - lift)
        u = 1 - t
        return QPointF(u * u * a.x() + 2 * u * t * ctrl.x() + t * t * b.x(),
                       u * u * a.y() + 2 * u * t * ctrl.y() + t * t * b.y())

    def _step(self, value) -> None:
        self.t = float(value)
        self.update()

    def _landed(self) -> None:
        self.hide()
        self.deleteLater()
        if self.done is not None:
            self.done()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = self.point(self.t)
        size = 9 + 5 * math.sin(math.pi * self.t)
        glow = QColor(STAR_GOLD)
        glow.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(center, size * 1.6, size * 1.6)
        p.setPen(QPen(QColor("#fff6d8"), 1))
        p.setBrush(QColor(STAR_GOLD))
        p.drawPath(star_path(center, size, self.t * 280))
        p.end()


class RowFlash(QWidget):
    """「皮肤」一行闪一下金光：盖在那一行上（不接鼠标），金色的半透明底和框线淡入再淡出（FLASH_MS），完了自己删掉。"""

    def __init__(self, row: QWidget):
        super().__init__(row)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(row.rect())
        self.level = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(FLASH_MS)
        self.anim.setKeyValueAt(0.0, 0.0)
        self.anim.setKeyValueAt(0.3, 1.0)
        self.anim.setKeyValueAt(1.0, 0.0)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._done)
        self.show()
        self.raise_()
        self.anim.start()

    def _step(self, value) -> None:
        self.level = float(value)
        self.update()

    def _done(self) -> None:
        self.hide()
        self.deleteLater()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fill, edge = QColor(STAR_GOLD), QColor(STAR_GOLD)
        fill.setAlpha(round(80 * self.level))
        edge.setAlpha(round(230 * self.level))
        p.setPen(QPen(edge, 2))
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        p.end()
