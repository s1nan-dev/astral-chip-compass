"""收到系统托盘时的小提示框（TrayToast）：每次运行第一次点悬浮窗的 × 时，贴着托盘图标弹出「已收到系统托盘……」那一句。

不用 Windows 的系统通知（QSystemTrayIcon.showMessage）：Windows 10、11 弹系统通知时会放提示音。这里自己画一个小框，也不用
别的会放提示音的东西（QMessageBox 的图标类型、MessageBeep），不出声。
- 位置（toast_position）：托盘图标的位置（QSystemTrayIcon.geometry）拿得到时贴着它，放在它上方，任务栏在屏幕上沿、左右两边
  时挪进那块屏幕的可用区域里；拿不到（图标收在「隐藏的图标」里、系统不给位置）时放在主屏幕可用区域的右下角。
- 约 TOAST_MS 毫秒后自己消失，点一下也消失；不抢焦点（显示时不激活、不接键盘焦点）、不进任务栏、总在最前。
- 颜色照当前皮肤悬浮窗的悬停说明（theme.OVERLAY 的 TIP_BG、TIP_TEXT、TIP_BORDER），每次弹出时现取，换了皮肤下次照新的。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from .theme import COMMON as C
from .theme import OVERLAY as O

TOAST_MS = 4000  # 显示多久（毫秒）
TOAST_WIDTH = 300  # 提示框的宽（逻辑像素）：字多时折行，高度跟着字
TOAST_GAP = 8  # 和托盘图标、屏幕可用区域边缘之间留的空
TOAST_RADIUS = 6


def toast_position(size: QSize, icon: QRect | None, screens, primary: QRect) -> QPoint:
    """提示框（大小 size）左上角放在哪。icon 是托盘图标在屏幕上的位置（拿不到时为 None 或空的 QRect）；screens 是各块屏幕的
    [(整块屏幕, 可用区域)]（可用区域不含任务栏）；primary 是主屏幕的可用区域。

    图标在哪块屏幕上（按图标的中心），就放在那块屏幕的可用区域里：横向对着图标的中心，纵向在图标上方，再挪进可用区域
    （任务栏在屏幕下沿时就是任务栏上方、贴着图标；在上沿、左右两边时挪到任务栏旁边）。图标的位置拿不到、不在任何一块屏幕上
    时放在主屏幕可用区域的右下角。"""
    w, h = size.width(), size.height()
    area = None
    if icon is not None and icon.isValid() and not icon.isEmpty():
        center = icon.center()
        area = next((avail for full, avail in screens if full.contains(center)), None)
    if area is None:
        return QPoint(primary.right() + 1 - w - TOAST_GAP, primary.bottom() + 1 - h - TOAST_GAP)
    x = icon.center().x() - w // 2
    y = icon.top() - h - TOAST_GAP
    x = max(area.left() + TOAST_GAP, min(x, area.right() + 1 - w - TOAST_GAP))
    y = max(area.top() + TOAST_GAP, min(y, area.bottom() + 1 - h - TOAST_GAP))
    return QPoint(x, y)


class TrayToast(QWidget):
    """贴着托盘图标的小提示框：标题（粗体）和一两行字，TOAST_MS 后自己藏起来，点一下也藏起来。没有父窗口（悬浮窗收到托盘里
    藏着时它照样显示），关掉悬浮窗不删它，下次照样用。"""

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # 显示时不激活：不抢游戏的键盘焦点
        self.setAttribute(Qt.WA_TranslucentBackground)  # 圆角外面透明
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(TOAST_WIDTH)
        self.frame = QFrame(self)
        self.frame.setObjectName("trayToast")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        box = QVBoxLayout(self.frame)
        box.setContentsMargins(12, 9, 12, 10)
        box.setSpacing(3)
        self.title = QLabel()
        self.text = QLabel()
        self.text.setWordWrap(True)
        for label in (self.title, self.text):
            label.setTextFormat(Qt.PlainText)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)  # 点在字上也算点了提示框
            box.addWidget(label)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def apply_look(self) -> None:
        """照当前皮肤悬浮窗悬停说明的颜色：底色、字、框线。"""
        self.frame.setStyleSheet(
            f"QFrame#trayToast {{ background-color: {O.TIP_BG}; border: 1px solid {O.TIP_BORDER}; "
            f"border-radius: {TOAST_RADIUS}px; }}"
            f"QLabel {{ color: {O.TIP_TEXT}; background: transparent; border: none; font-size: {C.FONT_M}pt; }}")
        bold = self.title.font()
        bold.setBold(True)
        self.title.setFont(bold)

    def popup(self, title: str, text: str, icon: QRect | None = None, ms: int = TOAST_MS) -> None:
        """弹出：标题 title（空时不写标题这一行）、正文 text，贴着托盘图标的位置 icon（拿不到时为 None），ms 毫秒后藏起来。"""
        self.apply_look()
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.text.setText(text)
        self.adjustSize()
        screens = [(s.geometry(), s.availableGeometry()) for s in QGuiApplication.screens()]
        primary = QGuiApplication.primaryScreen()
        area = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 720)
        self.move(toast_position(self.size(), icon, screens, area))
        self.show()
        self.raise_()
        self.timer.start(ms)

    def mousePressEvent(self, event) -> None:  # noqa: N802  Qt 的事件名
        """点一下就藏起来（不等计时）。"""
        self.timer.stop()
        self.hide()
        event.accept()
