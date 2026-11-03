"""自己画外框的窗口（皮肤的 Layouts.CHROME 不是 system 时：设置、编辑方案、导入/导出、关于窗口）和左侧页签排法用的部件。
样子都按当前皮肤的 ChromeLook 画（decor.py），默认皮肤不用这里的东西（用系统标题栏和各窗口默认的排法）。

- frameless(window, on, base_flags, resizable)：去掉或恢复系统标题栏。没有系统标题栏时：
  - 窗口里没有被按钮、输入框接走的地方（底色、导航栏和面板的空白、卡片的空白和说明文字）按住拖动就是移动窗口：
    交给系统移动（QWindow.startSystemMove：跨屏幕、贴边、换缩放比都照系统的做法），系统不支持时自己跟着鼠标挪（Frame）；
  - resizable 的窗口（编辑方案、导入/导出，和默认皮肤一样；设置、关于固定大小）：窗口边缘 RESIZE_BORDER 宽的一圈按住
    拖动改大小，四条边、四个角（离角 RESIZE_CORNER 以内）都行，光标跟着变成对应的箭头。做法是窗口类混入 EdgeResize：
    系统问鼠标在窗口的哪一部分（WM_NCHITTEST）时，边缘那一圈答「左边」「右上角」这类（edge_at），改大小、换光标都由
    系统做；其余地方照旧交给程序，所以边缘以外的空白照旧是按住拖动移动窗口。最小不小于窗口内容需要的大小（同默认皮肤）；
  - Windows 11 上用系统的圆角（round_corners，每次显示时设）；Windows 10、设置失败时照旧直角，不报错；
  - Esc 关闭窗口（esc_closes）；
  - 窗口照旧在任务栏里有按钮（不用 Tool 类型；带系统菜单和最小化，点任务栏按钮照常最小化、还原）。
- HideNotice：混入设置、关于、编辑方案、导入/导出、皮肤窗口（不论用不用系统标题栏），窗口要藏起来之前先告诉悬浮窗
  （before_hide），悬浮窗赶在 Windows 交出前台之前垫好窗口层次。
- 一个窗口只留一个关闭按钮：自己画外框时左上角「返回」就是关闭窗口，排法里不再另放「关闭」按钮（窗口的 close_btn 就是
  「返回」）。以后的皮肤照这条做（外框是右上角 × 的也一样）。
- 装饰不压在字下面：字写在卡片、胶囊、警告这类有底色的东西上；直接写在面板上的说明垫底板（ChromeLook 的 PLATE_*：
  plate_sheet；警告色的说明用 WarnLabel），写在导航栏上的垫一块导航栏底色（rail_backed）。
- Frame：窗口内容的底，画窗口底色（斜向渐变、细斜线）。
- BackButton：左上角「返回」，点了关闭窗口（悬停说明 BACK_TIP）。
- NavRail、NavTab：左侧白色导航栏和页签（选中的变橙黄色、往右突出）；NavRail.add_tab 建页签，切页由调用的地方接 toggled。
- Panel：右边的深灰面板。Card：浅灰卡片，左上角可以压一个标签（Tag）。
- PillButton：胶囊按钮（main 粉色铺星星；light 白色）。WarnLabel：警告（深色底、黄字，前面一个「!」圆标；badge 为 False 时
  不画圆标，当说明的底板用）。
- VersionTag：版本标签（「版本：」和版本号）。
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import decor, theme, winapi
from .decor import index_font, italic_title_font  # 数字字体、斜体标题字体：定义在 decor，这里再导出
from .theme import CHROME as K

log = logging.getLogger(__name__)

# ------------------------------------------------------------ 界面文字

BACK_TEXT = "返回"
BACK_TIP = "关闭窗口（也可以按 Esc）"
VERSION_LABEL = "版本："


def custom() -> bool:
    """当前皮肤是不是自己画外框（Layouts.CHROME 不是 system）。"""
    return theme.current().layouts.CHROME != "system"


def closer() -> QAbstractButton | None:
    """当前皮肤自己画外框时窗口唯一的关闭按钮：back 为左上角「返回」（BackButton），close 为右上角 ×（CloseButton）；用系统
    标题栏时为 None（关闭用系统标题栏的 ×）。排法把它放在对应的位置，窗口的 back、close_btn 都指向它。"""
    kind = theme.current().layouts.CHROME
    if kind == "close":
        return CloseButton()
    if kind == "back":
        return BackButton()
    return None


# ------------------------------------------------------------ 外框


def frameless(window: QWidget, on: bool, base_flags, resizable: bool = False) -> None:
    """按 on 去掉（自己画外框）或恢复（base_flags，窗口原来的标志）系统标题栏。窗口已经显示着时改完重新显示（改标志会把
    窗口藏起来）。不用 Tool 类型：自己画外框的窗口照旧在任务栏里有按钮。resizable：自己画外框时能不能拖边缘改大小（窗口类
    要混入 EdgeResize）。外框换过一次以后，窗口每次显示时按有没有系统标题栏设圆角（_CornerHook）。"""
    window._chrome_resizable = bool(on and resizable)
    if bool(window.windowFlags() & Qt.FramelessWindowHint) == on:
        return  # 已经是这样（默认皮肤一直用系统标题栏：什么都不改）
    if on:
        flags = (Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint
                 | (base_flags & Qt.WindowStaysOnTopHint))
    else:
        flags = base_flags
    visible = window.isVisible()
    window.setWindowFlags(flags)
    if getattr(window, "_corner_hook", None) is None:
        window._corner_hook = _CornerHook(window)
        window.installEventFilter(window._corner_hook)
    if visible:
        window.show()


# ------------------------------------------------------------ 拖边缘改大小

RESIZE_BORDER = 6  # 窗口边缘这么宽（逻辑像素）的一圈按住拖动改大小；往里的空白照旧是按住拖动移动窗口
RESIZE_CORNER = 16  # 角：离窗口的角这么近（沿边量，逻辑像素）时同时改两边
WM_NCHITTEST = 0x0084
# WM_NCHITTEST 的回答：鼠标在窗口的哪条边、哪个角上（系统按它改大小、换光标）
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = range(10, 18)


def edge_at(x: int, y: int, width: int, height: int, border: int, corner: int) -> int:
    """窗口里的一点 (x, y)（以窗口左上角为原点；窗口宽 width、高 height，都是同一种像素）落在哪条边、哪个角上：HTLEFT
    这类编码；离边缘超过 border、在窗口外面时为 0。角：贴着一条边（border 以内）、离另一条边不到 corner。"""
    if border <= 0 or not (0 <= x < width and 0 <= y < height):
        return 0
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if not (left or right or top or bottom):
        return 0
    near_left, near_right = x < corner, x >= width - corner
    near_top, near_bottom = y < corner, y >= height - corner
    if (top and near_left) or (left and near_top):
        return HTTOPLEFT
    if (top and near_right) or (right and near_top):
        return HTTOPRIGHT
    if (bottom and near_left) or (left and near_bottom):
        return HTBOTTOMLEFT
    if (bottom and near_right) or (right and near_bottom):
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    return HTTOP if top else HTBOTTOM


def can_resize(window: QWidget) -> bool:
    """窗口现在能不能拖边缘改大小：自己画外框、窗口类允许（frameless 的 resizable），而且不是最小化、最大化、全屏。"""
    return (bool(getattr(window, "_chrome_resizable", False))
            and not (window.isMinimized() or window.isMaximized() or window.isFullScreen()))


def resize_hit(window: QWidget, message) -> int:
    """窗口收到的系统消息（message 是 MSG 的地址）是问鼠标在哪（WM_NCHITTEST）、鼠标又在可以改大小的边缘上时，回答哪条边、
    哪个角（HT* 编码）；别的消息、别的地方为 0（照旧交给程序）。按物理像素算：鼠标位置和窗口矩形都是系统给的物理像素，
    边缘的宽按窗口所在屏幕的缩放比换算。"""
    if not getattr(window, "_chrome_resizable", False):
        return 0
    try:
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_NCHITTEST or not can_resize(window):
            return 0
        rect = winapi.window_rect(msg.hWnd)
        if rect is None:
            return 0
        lp = int(msg.lParam)
        x = ctypes.c_short(lp & 0xFFFF).value - rect[0]
        y = ctypes.c_short((lp >> 16) & 0xFFFF).value - rect[1]
        scale = max(1.0, window.devicePixelRatioF())
        return edge_at(x, y, rect[2] - rect[0], rect[3] - rect[1], round(RESIZE_BORDER * scale),
                       round(RESIZE_CORNER * scale))
    except (OSError, ValueError, TypeError, OverflowError, RuntimeError) as e:  # 读不了消息时照旧交给程序（不改大小）
        log.debug("改大小的命中测试失败：%s", e)
        return 0


class EdgeResize:
    """混入（写在 QWidget 前面）：自己画外框、可以改大小的窗口（frameless 的 resizable）拖边缘改大小（resize_hit）。
    用系统标题栏时、不在边缘上时照旧交给程序，和没有混入一样。"""

    def nativeEvent(self, event_type, message):  # noqa: N802  Qt 的虚函数
        code = resize_hit(self, message)
        return (True, code) if code else (False, 0)


# ------------------------------------------------------------ 藏起来之前


class HideNotice:
    """混入（写在 QWidget、QDialog 前面）：窗口要藏起来之前（点 ×、按 Esc、程序里关掉，换外框时 Qt 也先藏再显示）调用
    before_hide(窗口)，这时窗口还显示着、还在前台。悬浮窗打开设置、关于、编辑方案、导入/导出、皮肤窗口时设上
    （follow.WindowStack.show_window），赶在 Windows 把前台交给别的程序之前垫好窗口层次。Qt 里各种藏窗口的路子最后都经过
    setVisible(False)，所以放在这里；没设时和没有混入一样。"""

    before_hide = None

    def setVisible(self, visible: bool) -> None:  # noqa: N802  Qt 的虚函数
        hook = self.before_hide
        try:
            if hook is not None and not visible and self.isVisible():
                hook(self)
        finally:  # 钩子出错也照样藏起来
            super().setVisible(visible)


# ------------------------------------------------------------ 圆角（Windows 11）


def _native_windows() -> bool:
    """程序跑在 Windows 的窗口系统上（不是测试用的离屏平台）。"""
    return QGuiApplication.platformName() == "windows"


def round_corners(window: QWidget, on: bool = True) -> bool:
    """on 为 True 时窗口用系统的圆角（Windows 11 起有；DWMWA_WINDOW_CORNER_PREFERENCE 设为 ROUND），False 时改回系统默认。
    系统不支持（Windows 10）、设置失败、不在 Windows 的窗口系统上时什么都不改、不报错，返回 False。"""
    if not _native_windows():
        return False
    try:
        preference = winapi.DWMWCP_ROUND if on else winapi.DWMWCP_DEFAULT
        return bool(winapi.set_corner_preference(int(window.winId()), preference))
    except (OSError, AttributeError, ValueError, TypeError, RuntimeError) as e:
        log.debug("DwmSetWindowAttribute 失败：%s", e)
        return False


class _CornerHook(QObject):
    """装在换过外框的窗口上（frameless）：窗口每次显示时，自己画外框就用系统的圆角，用系统标题栏就改回系统默认。系统窗口
    重建以后（换外框、关了再开）设过的会丢，所以每次显示都设。"""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802  Qt 的虚函数
        if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
            round_corners(watched, bool(watched.windowFlags() & Qt.FramelessWindowHint))
        return False


def esc_closes(window: QWidget, event) -> bool:
    """自己画外框的窗口按 Esc 关闭（keyPressEvent 里调用；关了返回 True）。用系统标题栏时不管。"""
    if event.key() == Qt.Key_Escape and custom():
        window.close()
        event.accept()
        return True
    return False


class Frame(QFrame):
    """窗口内容的底：画窗口底色（斜向渐变、细斜线）。按住没有被子部件接走的地方拖动，移动整个窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chromeFrame")
        self._drag_from: QPoint | None = None

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        right, keep = self._head_text() if K.FRAME == "neon" else (0.0, ())
        p.drawPixmap(0, 0, decor.cached(self, ("frame", theme.current_key(), right, keep), r.size(),
                                        lambda q: decor.backdrop(q, r, K, right, [QRectF(*b) for b in keep])))
        p.end()

    def _head_text(self) -> tuple[float, tuple]:
        """顶上一行（WindowHead）里标题、说明最右边的字的位置和 × 占的地方（本部件的坐标）：霓虹贴纸的网点只铺在字的右边，
        并让开 ×，不压在字下面。"""
        head = self.findChild(WindowHead)
        if head is None:
            return 0.0, ()
        right = float(head.mapTo(self, QPoint(round(head.text_right()), 0)).x())
        keep = ()
        if head.closer is not None:
            g = head.closer.geometry()
            corner = head.mapTo(self, g.topLeft())
            keep = ((corner.x(), corner.y(), g.width(), g.height()),)
        return right, keep

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.window().windowHandle()
        if handle is not None and handle.startSystemMove():
            event.accept()
            return
        self._drag_from = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)


# ------------------------------------------------------------ 「返回」


class BackButton(QAbstractButton):
    """左上角「返回」：点了关闭所在的窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText(BACK_TEXT)
        self.setToolTip(BACK_TIP)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # 关窗口用 Esc 也行；不抢第一个焦点（抢到时一直是悬停的样子）
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(*K.BACK_SIZE)
        self.clicked.connect(lambda: self.window().close())

    def sizeHint(self) -> QSize:
        return QSize(*K.BACK_SIZE)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse()
        decor.back_button(p, QRectF(self.rect()), self.text(), theme.title_font(K.BACK_PX), K, hover, self.isDown())
        p.end()


# ------------------------------------------------------------ 左侧导航栏和页签


class NavTab(QAbstractButton):
    """左侧的一个页签（可选中；同一个导航栏里只能选中一个）。没选中时靠左，选中时往右突出 TAB_SHIFT。"""

    def __init__(self, text: str, parent=None, px: int | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self._px = px
        self.setFixedHeight(K.TAB_HEIGHT + 3)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self) -> QSize:
        return QSize(K.TAB_SIZE[1] + K.TAB_SHIFT, K.TAB_HEIGHT + 3)

    def paintEvent(self, event) -> None:
        on = self.isChecked()
        width = K.TAB_SIZE[1] if on else K.TAB_SIZE[0]
        x = K.TAB_SHIFT if on else 14
        width = min(width, self.width() - x)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(x, 0, width, self.height())
        font = theme.title_font(self._px or K.TAB_PX)
        decor.tab(p, rect, self._elided(font, width), font, K, on, self.underMouse())
        if self.hasFocus():  # 键盘焦点：页签外面一圈虚线
            pen = QPen(decor.qc(K.TAB_OUTLINE), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -4), 10, 10)
        p.end()

    def _elided(self, font, width: float) -> str:
        """放不下时末尾省略（用户自己起的方案名可能很长）。"""
        fm = QFontMetricsF(font)
        room = width - K.TAB_HEIGHT * 0.95 - 10
        return fm.elidedText(self.text(), Qt.ElideRight, max(room, 10))


class NavRail(QWidget):
    """左侧白色导航栏：圆角白条（左边贴窗口边）、点缀的星星、一条竖着的渐变条；里面从上往下放页签和别的部件
    （box 是它的竖排布局）。宽度是白条的宽加上选中的页签往右突出的那一截。"""

    def __init__(self, parent=None, seed: int = 7):
        super().__init__(parent)
        self._seed = seed
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 30, 0, 22)
        self.box.setSpacing(K.TAB_GAP)
        self.setFixedWidth(K.RAIL_WIDTH + 26)

    def add_tab(self, text: str, px: int | None = None) -> NavTab:
        tab = NavTab(text, px=px)
        self.group.addButton(tab)
        self.box.addWidget(tab)
        return tab

    def tabs(self) -> list[NavTab]:
        return [b for b in self.group.buttons() if isinstance(b, NavTab)]

    def paintEvent(self, event) -> None:
        w, h = K.RAIL_WIDTH, self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        def draw(q: QPainter) -> None:
            rail = decor.rounded(QRectF(-K.RAIL_RADIUS, 0, w + K.RAIL_RADIUS, h), K.RAIL_RADIUS)
            q.fillPath(rail, decor.qc(K.RAIL_FILL))
            q.save()
            q.setClipPath(rail)
            decor.sparkles(q, QRectF(0, 8, w, h - 30), K.RAIL_SPARKLE, 26, self._seed, radius=(5, 11))
            x0, x1 = K.RAIL_STRIPE_X
            q.fillRect(QRectF(x0, 0, x1 - x0, h), decor.brush(decor.gradient(0, 0, 0, h, K.RAIL_STRIPE)))
            q.restore()

        p.drawPixmap(0, 0, decor.cached(self, ("rail", theme.current_key(), self._seed), QSize(w, h), draw))
        p.end()


# ------------------------------------------------------------ 面板、卡片、标签


class Panel(QFrame):
    """右边的深灰面板：几何小图案、右上压一黄一粉两条斜带。"""

    def __init__(self, parent=None, seed: int = 3, stripes: bool = True):
        super().__init__(parent)
        self._seed, self._stripes = seed, stripes
        self.setObjectName("chromePanel")

    def paintEvent(self, event) -> None:
        r = QRectF(self.rect())
        p = QPainter(self)
        p.drawPixmap(0, 0, decor.cached(self, ("panel", theme.current_key(), self._seed, self._stripes), r.size(),
                                        lambda q: decor.panel(q, r, K, self._seed, self._stripes)))
        p.end()


class Tag(QWidget):
    """卡片左上角的标签（白字黑描边的橙色圆角块；按 ChromeLook.TAG_KIND 也可以是黑色平行四边形、两颗小菱形加白字、黑色斜带、
    小灯点加深色字）。key 为组名（TAG_KIND 为 led 时决定灯点的颜色）。"""

    def __init__(self, text: str, parent=None, px: int | None = None, key: str = ""):
        super().__init__(parent)
        self._text = text
        self._px = px
        self.key = key
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802  和 QLabel 同名
        self._text = text
        self.resize(self.sizeHint())
        self.update()

    def font_(self):
        return theme.title_font(self._px or K.TAG_PX)

    def sizeHint(self) -> QSize:
        px = self._px or K.TAG_PX
        return decor.tag_size(K.TAG_KIND, self.font_(), self._text, self.number, px, K)

    number = ""  # TAG_KIND 为 index 时标题前的编号（「01」），set_number 设

    def set_number(self, number: str) -> None:
        """标题前的编号（TAG_KIND 为 index 时写出来）。"""
        self.number = number
        self.resize(self.sizeHint())
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.tag(p, QRectF(self.rect()), self._text, self.font_(), K, self.key, self.number, self._px or K.TAG_PX)
        p.end()


class Card(QFrame):
    """浅灰卡片：下沿一截投影、淡色底纹；title 不为空时左上角压一个标签（一半露在卡片上沿外面）。box 是卡片内容的竖排布局，
    margins 为内容离卡片边缘（不含标签、投影）的距离。"""

    TAG_X = 0  # 标签离卡片左边
    BODY_X = 6  # 卡片本身比标签往右缩进一点（标签露出卡片左边）
    BAND_GAP = 6  # 黑色斜带（TAG_KIND 为 band）和卡片之间

    def __init__(self, title: str = "", parent=None, seed: int = 11, margins=(28, 18, 22, 18), radius: int | None = None,
                 key: str = ""):
        super().__init__(parent)
        self.setObjectName("chromeCard")
        self._seed = seed
        self._radius = radius
        self.key = key
        self.tag = Tag(title, self, key=key) if title else None
        left, top, right, bottom = margins
        # 卡片本身离左边、右边留给投影、下边留给投影：按 CARD_KIND 查 decor.CARD_KINDS 的尺寸（霓虹贴纸的贴纸卡片右下有
        # 实心投影；白色海景房的白卡片没有投影）
        metrics = decor.card_metrics(K.CARD_KIND)
        self._body_x, self._right, self._shadow = metrics.body_x, metrics.right, metrics.shadow
        # 标签的放法：两颗小菱形的分组标题、小灯点的组标题、编号标题写在卡片里面（经典主题-黑、白色海景房、碳纤流光）；
        # 黑色斜角标签往右缩进一点、一半压在上沿（经典主题-反色）；黑色斜带整个在卡片上面（霓虹贴纸）；其余照默认的
        # （一半露在卡片上沿外面、露出卡片左边）
        place = decor.tag_place(K.TAG_KIND)
        self._inside = self.tag is not None and place.inside
        band = self.tag is not None and place.band
        self._tag_pos = (self._body_x + left, top) if self._inside else (
            (self._body_x + 12, 0) if place.slant else (self.TAG_X, 0))
        if self._inside or self.tag is None:
            self._lift = 0
        elif band:
            self._lift = self.tag.sizeHint().height() + self.BAND_GAP
        else:
            self._lift = (self.tag.sizeHint().height() - 3) // 2
        self.box = QVBoxLayout(self)
        if self._inside:
            self.box.setContentsMargins(self._body_x + left, top + self.tag.sizeHint().height() + 6, right + self._right,
                                        bottom + self._shadow)
        else:
            self.box.setContentsMargins(self._body_x + left, self._lift + top + (8 if self.tag and not band else 0),
                                        right + self._right, bottom + self._shadow)
        self.box.setSpacing(10)

    def body_rect(self) -> QRectF:
        return QRectF(self._body_x, self._lift, self.width() - self._body_x - self._right,
                      self.height() - self._lift - self._shadow)

    def resizeEvent(self, event) -> None:
        if self.tag is not None:
            self.tag.move(*self._tag_pos)
            self.tag.raise_()
        super().resizeEvent(event)

    plain = False  # TAG_KIND 为 index（碳纤流光）时：True 为不画卡片的底和框，只画标题后面的线（导入/导出并排的两块）

    def set_number(self, number: str) -> None:
        """标题前的编号（TAG_KIND 为 index 时写出来，例如「01」）。"""
        if self.tag is not None:
            self.tag.set_number(number)
            self.update()

    def paintEvent(self, event) -> None:
        body = self.body_rect()
        p = QPainter(self)
        if decor.tag_place(K.TAG_KIND).index:  # 碳纤流光：切角的卡片（plain 时不画），编号标题后面一小截亮色、一条细线到右边
            rule = None
            if self.tag is not None:
                g = self.tag.geometry()
                rule = (float(g.right() + 8), float(self.width() - self.box.contentsMargins().right()),
                        g.center().y() + 0.5)

            def draw_index(q: QPainter) -> None:
                if not self.plain:
                    decor.card(q, body, K, self._seed, self._radius)
                if rule is not None:
                    decor.index_rule(q, *rule, K)

            key = ("card", theme.current_key(), self._seed, self._radius, self.plain, rule)
            p.drawPixmap(0, 0, decor.cached(self, key, QSize(self.width(), self.height()), draw_index))
            p.end()
            return

        def draw(q: QPainter) -> None:
            decor.card(q, body, K, self._seed, self._radius)

        p.drawPixmap(0, 0, decor.cached(self, ("card", theme.current_key(), self._seed, self._radius),
                                        QSize(self.width(), self.height()), draw))
        p.end()


# ------------------------------------------------------------ 按钮、警告、版本


class PillButton(QPushButton):
    """胶囊按钮：kind 为 main（粉色、铺星星、下沿一截深色做厚度、白字）或 light（白色、深色字）。高度 height（含厚度）。"""

    def __init__(self, text: str, kind: str = "main", height: int = 40, px: int = 15, parent=None, seed: int = 1,
                 min_width: int = 0, radius: float | None = None):
        super().__init__(text, parent)
        self.kind, self._px, self._seed, self._min_w, self._radius = kind, px, seed, min_width, radius
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def font_(self):
        return theme.title_font(self._px)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font_())
        badge = 22 if self.kind == "warn" else 0
        return QSize(max(self._min_w, round(fm.horizontalAdvance(self.text()) + 40 + badge)), self.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = decor.pill_button(p, QRectF(self.rect()), K, self.kind, self.underMouse(), self.isDown(),
                                 self.isEnabled(), self._seed, self._radius)
        color = {"light": K.LIGHT_TEXT, "warn": K.WARN_TEXT}.get(self.kind, K.BUTTON_TEXT)
        text_rect = body
        if self.kind == "warn":  # 警告色的按钮：左端一个「!」圆标
            r = min(9.0, body.height() / 2 - 4)
            decor.badge(p, QPointF(body.left() + 6 + r, body.center().y()), r, K.WARN_BADGE, WarnLabel.BADGE,
                        K.WARN_BADGE_TEXT, theme.title_font(12))
            text_rect = body.adjusted(2 * r + 4, 0, 0, 0)
        decor.outlined_text(p, text_rect, self.text().replace("&&", "&"), self.font_(), color)
        if self.hasFocus():  # 键盘焦点：按钮里面一圈虚线
            p.setPen(QPen(decor.qc(color), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            r = body.adjusted(4, 4, -4, -4)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()


class WarnLabel(QLabel):
    """警告：深色圆角底、黄字（自动换行），左边一个「!」圆标。颜色按皮肤的 ChromeLook WARN_*。badge 为 False 时不画圆标
    （旁边已经有带圆标的按钮时，例如删除的确认），只当警告色的底板。"""

    BADGE = "!"

    def __init__(self, text: str = "", parent=None, name: str = "", badge: bool = True):
        super().__init__(text, parent)
        self._badge = badge
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setWordWrap(True)
        if name:
            self.setObjectName(name)
        self.apply_skin()

    def apply_skin(self) -> None:
        left = 38 if self._badge else 12
        if K.WARN_SHAPE == "cut":  # 碳纤流光：切角的细框自己画（paintEvent），样式表只管字和内边距
            self.setStyleSheet(f"QLabel {{ background: transparent; color: {decor.qc(K.WARN_TEXT).name()}; "
                               f"padding: 7px 12px 7px {left}px; }}")
            return
        if K.WARN_SHAPE == "slant":  # 黑色斜条自己画（paintEvent），样式表只管字和内边距
            self.setStyleSheet(f"QLabel {{ background: transparent; color: {decor.qc(K.WARN_TEXT).name()}; "
                               f"padding: 8px {12 + K.SLANT}px 8px {left + 6}px; font-weight: bold; }}")
            return
        border = f"border: {K.WARN_PEN}px solid {decor.qc(K.WARN_BORDER).name()}; " if K.WARN_BORDER else ""
        self.setStyleSheet(f"QLabel {{ background: {decor.qc(K.WARN_FILL).name()}; color: {decor.qc(K.WARN_TEXT).name()}; "
                           f"{border}border-radius: 14px; padding: 7px 12px 7px {left}px; font-weight: bold; }}")

    def paintEvent(self, event) -> None:
        slanted = K.WARN_SHAPE == "slant"
        if K.WARN_SHAPE == "cut":  # 字下面先画切角的细框、很淡的底（碳纤流光）
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            decor.cut_warn(p, QRectF(self.rect()), K)
            p.end()
        if slanted:  # 字下面先铺黑色斜条（平行四边形，右下错开一截投影）
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            body = QRectF(self.rect()).adjusted(0, 0, -3, -3)
            path = decor.slant(body, min(12.0, body.height() * 0.3))
            decor.hard_shadow(p, path, 3, 3, (0, 0, 0, 90))
            p.fillPath(path, decor.qc(K.WARN_FILL))
            p.end()
        super().paintEvent(event)
        if not self._badge:
            return
        p = QPainter(self)
        font = theme.title_font(13)
        cy = min(18.0, self.height() / 2)
        if K.WARN_BADGE_SHAPE == "triangle":
            decor.warn_triangle(p, QPointF(26 if slanted else 20, cy), 20, K.WARN_BADGE, K.WARN_BADGE_TEXT,
                                theme.title_font(12))
        else:
            decor.badge(p, QPointF(20, cy), 10, K.WARN_BADGE, self.BADGE, K.WARN_BADGE_TEXT, font)
        p.end()


def plate_sheet(selector: str) -> str:
    """直接写在面板上的说明垫的底板（ChromeLook 的 PLATE_FILL、PLATE_RADIUS）：给 selector 那个部件的样式表。字离底板边缘
    多远（PLATE_PADDING）由部件自己留（例如滚动区的视口边距）。"""
    return (f"{selector} {{ background: {decor.qc(K.PLATE_FILL).name()}; border: none; "
            f"border-radius: {K.PLATE_RADIUS}px; }}")


def rail_backed(label: QLabel) -> None:
    """写在左侧导航栏上的说明：垫一块导航栏的底色，导航栏的竖条、星星从字后面过去（字压在竖条上看不清）。"""
    label.setStyleSheet(f"QLabel {{ background: {decor.qc(K.RAIL_FILL).name()}; }}")


class TitleText(QWidget):
    """窗口顶上写在底色上的大标题（白字黑描边，标题字体）。colors 为（字, 描边），不给时用 ChromeLook 的 TAG_TEXT、TAG_OUTLINE。"""

    def __init__(self, text: str, px: int = 22, parent=None, colors: tuple | None = None):
        super().__init__(parent)
        self._text, self._px, self._colors = text, px, colors
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(px + 18)

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        return QSize(round(QFontMetricsF(theme.title_font(self._px)).horizontalAdvance(self._text) + 16), self._px + 18)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fill, outline = self._colors or (K.TAG_TEXT, K.TAG_OUTLINE)
        decor.outlined_text(p, QRectF(self.rect()).adjusted(6, 0, 0, 0), self._text, theme.title_font(self._px),
                            fill, outline, 6, align=Qt.AlignLeft)
        p.end()


class VersionTag(QWidget):
    """版本标签：白色的「版本：」接粉色的版本号（例如「1.0.0」）。"""

    def __init__(self, parent=None, px: int = 17):
        super().__init__(parent)
        from ..version import VERSION

        self._value = VERSION
        self._px = px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return VERSION_LABEL + self._value

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(theme.title_font(self._px))
        if K.VERSION_KIND == "pill":
            return QSize(round(fm.horizontalAdvance(self.text()) + 36), 36)
        return QSize(round(fm.horizontalAdvance(VERSION_LABEL) + 24 + fm.horizontalAdvance(self._value) + 40 - 10), 38)

    def paintEvent(self, event) -> None:
        font = theme.title_font(self._px)
        fm = QFontMetricsF(font)
        left_w = fm.horizontalAdvance(VERSION_LABEL) + 24
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if K.VERSION_KIND == "pill":  # 一整个胶囊：「版本：1.0.0」
            body = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = decor.capsule(body)
            p.fillPath(path, decor.qc(K.VERSION_FILL[0]))
            if K.VERSION_BORDER is not None:
                p.strokePath(path, QPen(decor.qc(K.VERSION_BORDER), 2 if isinstance(K.VERSION_BORDER, str) else 1.2))
            decor.outlined_text(p, body, self.text(), font, K.VERSION_TEXT[0])
            p.end()
            return
        p.fillPath(decor.rounded(QRectF(0, 0, self.width(), 38), 14), decor.qc(K.VERSION_FILL[0]))
        value = QRectF(left_w - 10, 0, self.width() - left_w + 10, 38)
        p.fillPath(decor.rounded(value, 12), decor.qc(K.VERSION_FILL[1]))
        decor.outlined_text(p, QRectF(12, 0, left_w, 38), VERSION_LABEL, font, K.VERSION_TEXT[0], align=Qt.AlignLeft)
        decor.outlined_text(p, value, self._value, font, K.VERSION_TEXT[1])
        p.end()


# ------------------------------------------------------------ 右上角 × 的外框（Layouts.CHROME 为 close）和它的部件


class CloseButton(QAbstractButton):
    """右上角的 ×：Layouts.CHROME 为 close 时窗口唯一的关闭按钮，点了关闭所在的窗口（悬停说明同「返回」）。样子按皮肤
    （ChromeLook 的 CLOSE_*，decor.close_mark）。不抢焦点（键盘用 Esc 关）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip(BACK_TIP)
        self.setAccessibleName(BACK_TIP)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(K.CLOSE_SIZE, K.CLOSE_SIZE)
        self.clicked.connect(lambda: self.window().close())

    def sizeHint(self) -> QSize:
        return QSize(K.CLOSE_SIZE, K.CLOSE_SIZE)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.close_mark(p, QRectF(self.rect()), K, self.underMouse(), self.isDown())
        p.end()


class TitleTab(QWidget):
    """压在窗口上沿正中的标题页签（ChromeLook.HEAD 为 tab：粉色圆角块、白边、网点，白字黑描边，decor.title_tab）。不接鼠标：
    按住它照样拖动窗口。高度为 HEAD_HEIGHT（含白边和投影），宽度随字。"""

    def __init__(self, text: str, px: int | None = None, parent=None):
        super().__init__(parent)
        self._text, self._px = text, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def font_(self):
        return theme.title_font(self._px or K.TITLE_PX)

    def sizeHint(self) -> QSize:
        return QSize(round(QFontMetricsF(self.font_()).horizontalAdvance(self._text) + 62), K.HEAD_HEIGHT - 1)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.title_tab(p, QRectF(self.rect()), self._text, self.font_(), K)
        p.end()


class LogoTitle(QWidget):
    """标题前一个小方块（ChromeLook.LOGO 的渐变，中间一块白色）、后面深色的标题字（TITLE_TEXT，不描边；白色海景房，
    decor.logo_title）。不接鼠标：按住它照样拖动窗口。"""

    LOGO = 22

    def __init__(self, text: str, px: int | None = None, parent=None):
        super().__init__(parent)
        self._text, self._px = text, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def font_(self):
        return theme.title_font(self._px or K.TITLE_PX)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font_())
        return QSize(round(self.LOGO + 10 + fm.horizontalAdvance(self._text) + 6), max(self.LOGO, round(fm.height())) + 8)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.logo_title(p, QRectF(self.rect()), self._text, self.font_(), K, self.LOGO)
        p.end()


class WindowHead(QWidget):
    """自己画外框、右上角 × 的窗口（Layouts.CHROME 为 close）顶上一行，HEAD_HEIGHT 高，按 ChromeLook.HEAD 摆标题：
    - tab：标题页签压在上沿正中（TitleTab），× 在右边、和顶边那一排竖纹同高（经典主题-黑；竖纹由窗口底 Frame 画）；
    - band：标题写在黑色通栏的左边（白字黑描边），中间可以另放部件（add：页签、下拉框、说明），× 在右边（经典主题-反色；通栏由
      Frame 画。霓虹贴纸的窗口底不画通栏，标题同样白字黑描边写在左边）；
    - logo：标题前一个渐变小方块、深色字不描边（LogoTitle），后面可以另放部件，× 在右边（白色海景房）；
    - rgb：四色小方块、斜体粗体的标题在左（RgbTitle），后面可以另放说明，× 在右边的切角方块里（碳纤流光）。
    closer 是 ×（窗口唯一的关闭按钮；用系统标题栏的皮肤为 None）。这一行没有被部件接走的地方按住拖动移动窗口（交给 Frame）。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(K.HEAD_HEIGHT)
        self.kind = K.HEAD
        self.closer = closer()
        self.box = QHBoxLayout(self)
        self.box.setSpacing(14)
        reserve = K.CLOSE_SIZE + 26 if self.closer is not None else 16
        if self.kind == "band":
            self.title = TitleText(title, K.TITLE_PX, colors=(K.TITLE_TEXT, K.TITLE_OUTLINE))
            self.box.setContentsMargins(18, 0, reserve, 0)
            self.box.addWidget(self.title, 0, Qt.AlignVCenter)
        elif self.kind == "logo":
            self.title = LogoTitle(title)
            self.box.setContentsMargins(20, 0, reserve, 0)
            self.box.setSpacing(10)
            self.box.addWidget(self.title, 0, Qt.AlignVCenter)
        elif self.kind == "rgb":  # 碳纤流光：四色小方块、斜体粗体的标题在左，后面可以另放说明
            self.title = RgbTitle(title)
            self.box.setContentsMargins(20, 4, reserve, 0)
            self.box.setSpacing(16)
            self.box.addWidget(self.title, 0, Qt.AlignVCenter)
        else:
            self.title = TitleTab(title, parent=self)
            self.box.setContentsMargins(reserve, 0, reserve, 0)
        self.box.addStretch(1)
        if self.closer is not None:
            self.closer.setParent(self)

    def add(self, widget: QWidget, stretch: int = 0, align=Qt.AlignVCenter) -> QWidget:
        """在标题右边（band、logo）、× 左边另放一个部件（排在已放的后面）。"""
        self.box.insertWidget(self.box.count() - 1, widget, stretch, align)
        return widget

    def text_right(self) -> float:
        """这一行里标题和另放的部件最右边的位置（本部件的坐标，× 不算）。"""
        right = 0.0
        for i in range(self.box.count()):
            widget = self.box.itemAt(i).widget()
            if widget is not None and widget.isVisibleTo(self):
                right = max(right, float(widget.geometry().right() + 1))
        if self.kind not in ("band", "logo", "rgb"):
            right = max(right, float(self.title.geometry().right() + 1))
        return right

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if self.kind not in ("band", "logo", "rgb"):
            self.title.move((w - self.title.width()) // 2, 1)
        if self.closer is not None:
            size = self.closer.height()
            if isinstance(self.closer, BackButton):
                self.closer.move(K.BACK_POS[0], max(0, (h - size) // 2))
            else:
                y = (h - size) // 2 if self.kind in ("band", "logo", "rgb") else K.INNER_TOP + (K.RIDGE_HEIGHT - size) // 2
                self.closer.move(w - 14 - size, y)
            self.closer.raise_()


class GameButton(QPushButton):
    """经典主题-黑、反色的按钮（decor.game_button）：kind 为种类（ChromeLook.BUTTON_KINDS 的键：primary 新建、导入一类；
    secondary 复制、更改；action 复制到剪贴板、导出诊断；plain 清空；danger 删除；note_a、note_b 交替的说明按钮；dark），
    形状按 BUTTON_SHAPE（圆角块或平行四边形）。height 含投影；icon 为系统图标编号，画在字前面（没有图标字体时不画）。
    danger 的皮肤要求时左边一个警告标（三角或圆标，同 WarnLabel）。"""

    def __init__(self, text: str, kind: str = "primary", height: int = 40, px: int = 15, icon: int | None = None,
                 min_width: int = 0, parent=None):
        super().__init__(text, parent)
        self.kind, self._px, self._icon, self._min_w = kind, px, icon, min_width
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def font_(self):
        return theme.title_font(self._px)

    def _icon_w(self) -> float:
        if self._icon is None:
            return 0.0
        from .glyphs import glyph_font

        return self._px + 8.0 if glyph_font(self._px) is not None else 0.0

    def _badge_w(self) -> float:
        return 24.0 if decor.button_style(K, self.kind)[6] else 0.0

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font_())
        extra = (3 + 2 * K.SLANT) if K.BUTTON_SHAPE == "slant" else (K.BUTTON_SHADOW[0] if K.BUTTON_SHAPE == "pill" else 0)
        w = fm.horizontalAdvance(self.text().replace("&&", "&")) + 34 + self._icon_w() + self._badge_w() + extra
        return QSize(max(self._min_w, round(w)), self.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = decor.game_button(p, QRectF(self.rect()), K, self.kind, self.underMouse(), self.isDown(),
                                 self.isEnabled())
        _, _, _, _, ink, outline, badge = decor.button_style(K, self.kind)
        text = self.text().replace("&&", "&")
        font = self.font_()
        fm = QFontMetricsF(font)
        icon_w, badge_w = self._icon_w(), self._badge_w()
        content = icon_w + badge_w + fm.horizontalAdvance(text)
        x = body.left() + (body.width() - content) / 2
        if badge:
            cy = body.center().y()
            if K.WARN_BADGE_SHAPE == "triangle":
                decor.warn_triangle(p, QPointF(x + 9, cy), 17, K.WARN_BADGE, K.WARN_BADGE_TEXT, theme.title_font(11))
            else:
                decor.badge(p, QPointF(x + 9, cy), 9, K.WARN_BADGE, WarnLabel.BADGE, K.WARN_BADGE_TEXT,
                            theme.title_font(12))
            x += badge_w
        if icon_w:
            from .glyphs import draw_glyph

            draw_glyph(p, QRectF(x, body.top(), self._px + 2, body.height()), self._icon, self._px, decor.qc(ink))
            x += icon_w
        decor.outlined_text(p, QRectF(x, body.top(), body.right() - x, body.height()), text, font, ink,
                            outline or None, max(2.5, self._px * 0.22) if outline else 0.0, align=Qt.AlignLeft)
        if self.hasFocus():  # 键盘焦点：按钮里面一圈虚线
            p.setPen(QPen(decor.qc(ink), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body.adjusted(5, 4, -5, -4), 6, 6)
        p.end()


class GameTab(QAbstractButton):
    """顶上一排的页签（decor.game_tab，ChromeLook 的 TAB_*）：可选中，同一排里只能选一个（TabBar）。"""

    def __init__(self, text: str, px: int = 16, parent=None):
        super().__init__(parent)
        self.setText(text)
        self._px = px
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(self.sizeHint())
        self.toggled.connect(lambda _: self.update())

    def font_(self):
        return theme.title_font(self._px)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font_())
        if K.TAB_KIND == "slant":
            return QSize(round(fm.horizontalAdvance(self.text()) + 54), 44)
        return QSize(round(fm.horizontalAdvance(self.text()) + 54), 48)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        decor.game_tab(p, rect, self.text(), self.font_(), K, self.isChecked(), self.underMouse())
        if self.hasFocus():
            p.setPen(QPen(decor.qc(K.TABS_ON[0]), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)
        p.end()


class TabBar(QWidget):
    """一排页签（GameTab），同一时间只选中一个；add_tab 建页签，切页由调用的地方接页签的 toggled。"""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(spacing)

    def add_tab(self, text: str, px: int = 16) -> GameTab:
        tab = GameTab(text, px)
        self.group.addButton(tab)
        self.box.addWidget(tab, 0, Qt.AlignVCenter)
        return tab

    def tabs(self) -> list[GameTab]:
        return [b for b in self.group.buttons() if isinstance(b, GameTab)]


class SectionTitle(QWidget):
    """分组标题（经典主题-黑）：两颗小菱形、白字黑描边的标题（decor.diamond_title），后面一条横线（ChromeLook.RULE）；
    right 给了文字时横线停在它前面，右边写这段暗色说明。横线只画在字和字之间的空白里，不压在字下面。"""

    def __init__(self, text: str, right: str = "", px: int = 15, parent=None):
        super().__init__(parent)
        self._text, self._right, self._px = text, right, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(px + 14)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def text(self) -> str:
        return self._text

    def _right_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(theme.COMMON.FONT_S)
        return font

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(theme.title_font(self._px))
        right = QFontMetricsF(self._right_font()).horizontalAdvance(self._right) + 16 if self._right else 0
        return QSize(round(30 + fm.horizontalAdvance(self._text) + 40 + right), self.height())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        end = decor.diamond_title(p, rect, self._text, theme.title_font(self._px), K)
        stop = rect.right() - 2
        if self._right:
            font = self._right_font()
            width = QFontMetricsF(font).horizontalAdvance(self._right)
            p.setFont(font)
            p.setPen(decor.qc(theme.COMMON.SOFT_COLOR))
            p.drawText(QRectF(rect.right() - width - 2, rect.top(), width + 2, rect.height()),
                       Qt.AlignVCenter | Qt.AlignRight, self._right)
            stop = rect.right() - width - 16
        if stop > end + 24:
            y = rect.center().y() + 0.5
            p.setPen(QPen(decor.qc(K.RULE), 1.4))
            p.drawLine(QPointF(end + 12, y), QPointF(stop, y))
        p.end()


class Banner(QWidget):
    """底部的洋红横幅（经典主题-反色，decor.banner）：写一段说明，放不下时折行（白字描边）。高度随字数。"""

    PAD_Y = 8

    def __init__(self, text: str, px: int = 13, parent=None):
        super().__init__(parent)
        self._text, self._px = text, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def text(self) -> str:
        return self._text

    def font_(self):
        return theme.title_font(self._px)

    def _lines(self, width: float) -> list[str]:
        """按 width 折行后的各行（中文按字换行）。"""
        from PySide6.QtGui import QTextLayout, QTextOption

        layout = QTextLayout(self._text, self.font_())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        lines = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(10.0, width))
            lines.append(self._text[line.textStart():line.textStart() + line.textLength()])
        layout.endLayout()
        return lines or [""]

    def _text_width(self, width: float) -> float:
        return width - 2 * (14 + 10) - 2

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        line_h = QFontMetricsF(self.font_()).height() + 2
        return max(42, round(len(self._lines(self._text_width(width))) * line_h + 2 * self.PAD_Y + 2))

    def sizeHint(self) -> QSize:
        width = self.width() if self.width() > 50 else 600
        return QSize(width, self.heightForWidth(width))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        area = decor.banner(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), K)
        font = self.font_()
        line_h = QFontMetricsF(font).height() + 2
        lines = self._lines(area.width())
        y = area.top() + (area.height() - len(lines) * line_h) / 2
        for line in lines:
            decor.outlined_text(p, QRectF(area.left(), y, area.width(), line_h), line.strip(), font, K.BANNER_TEXT,
                                K.BANNER_OUTLINE, 2.4, align=Qt.AlignLeft)
            y += line_h
        p.end()


# ------------------------------------------------------------ 碳纤流光的几个部件（ChromeLook 的 CHAMFER、RGB、HALO、INDEX_INK）
# index_font（数字字体的窄体）、italic_title_font（斜体标题字体）定义在 decor.py，这里再导出（见文件开头）。


class RgbTitle(QWidget):
    """窗口顶上的标题（HEAD 为 rgb）：四色小方块（ChromeLook.RGB，decor.rgb_logo），后面斜体粗体的标题字（TITLE_TEXT）。
    不接鼠标：按住它照样拖动窗口。"""

    LOGO = 18

    def __init__(self, text: str, px: int | None = None, parent=None):
        super().__init__(parent)
        self._text, self._px = text, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def font_(self) -> QFont:
        return italic_title_font(self._px or K.TITLE_PX)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font_())
        return QSize(round(self.LOGO + 12 + fm.horizontalAdvance(self._text) + 10), max(self.LOGO, round(fm.height())) + 8)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = float(self.height())
        decor.rgb_logo(p, QRectF(0, (h - self.LOGO) / 2, self.LOGO, self.LOGO), K.RGB)
        decor.flat_text(p, QRectF(self.LOGO + 12, 0, self.width() - self.LOGO - 12, h), self._text, self.font_(),
                        K.TITLE_TEXT)
        p.end()


class IndexHeading(QWidget):
    """分组标题（碳纤流光）：编号（INDEX_INK，数字字体的窄体）、斜体粗体的标题（TAG_TEXT），后面一小截亮色（HALO）、一条
    细线（RULE）一直到右边（decor.index_title、index_rule）。线只画在字后面的空白里。不接鼠标。"""

    def __init__(self, number: str, text: str, px: int = 15, parent=None):
        super().__init__(parent)
        self._number, self._text, self._px = number, text, px
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(px + 14)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def text(self) -> str:
        return self._text

    def number(self) -> str:
        return self._number

    def text_right(self) -> float:
        """编号和标题的右边缘（线从这里往右）。"""
        lead = QFontMetricsF(index_font(self._px)).horizontalAdvance(self._number) + 10 if self._number else 0.0
        return lead + QFontMetricsF(italic_title_font(self._px)).horizontalAdvance(self._text)

    def sizeHint(self) -> QSize:
        return QSize(round(self.text_right() + 60), self.height())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        end = decor.index_title(p, rect, self._number, self._text, index_font(self._px), italic_title_font(self._px), K)
        decor.index_rule(p, end + 12, rect.right() - 1, rect.center().y() + 0.5, K)
        p.end()


class CutCard(QFrame):
    """切角的面板（碳纤流光，decor.cut_card：CARD_FILL 的底、CARD_EDGE 的细框）。box 是内容的竖排布局，margins 为内容离面板
    边缘。accent 写了颜色时，标题那一行左边贴着面板左边一根这种颜色的竖条（bar 为竖条的上沿和高）。"""

    def __init__(self, accent: str = "", margins=(16, 12, 16, 14), bar=(13, 20), parent=None):
        super().__init__(parent)
        self.setObjectName("cutCard")
        self.accent, self._bar = accent, tuple(bar)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(*margins)
        self.box.setSpacing(8)

    def paintEvent(self, event) -> None:
        rect = QRectF(self.rect())
        p = QPainter(self)

        def draw(q: QPainter) -> None:
            decor.cut_card(q, rect, K)
            if self.accent:
                top, height = self._bar
                q.fillRect(QRectF(0, top, 3, height), decor.qc(self.accent))

        p.drawPixmap(0, 0, decor.cached(self, ("cutCard", theme.current_key(), self.accent, self._bar), self.size(), draw))
        p.end()


def _cut_kind(widget: QWidget) -> str:
    """CutBox 按哪一种画（ChromeLook.BUTTON_KINDS 的键）：部件的 cut 属性；键帽、状态标签再按 tone 属性换（键帽：warn 不可用、
    capture 等待按键；状态标签：ok、warn）；按钮没写 cut 时，按下去不弹起的按钮按下时为 primary，其余按 accent 属性
    （primary、map、capture 为 primary，warn 为 danger，没有为 plain）。"""
    kind = widget.property("cut") or ""
    tone = widget.property("tone") or ""
    if kind == "keycap":
        return {"warn": "danger", "capture": "primary"}.get(tone, "keycap")
    if kind == "chip":
        return "chip_warn" if tone == "warn" else "chip_ok"
    if kind:
        return kind
    if isinstance(widget, QAbstractButton) and widget.isCheckable() and widget.isChecked():
        return "primary"
    accent = widget.property("accent") or ""
    return {"primary": "primary", "map": "primary", "capture": "primary", "warn": "danger"}.get(accent, "plain")


class CutBox(QObject):
    """切角的框（碳纤流光）：给样式表画的按钮、标签（键帽、状态标签）先画一个切角的底（decor.cut_button，颜色按种类，
    _cut_kind），再让部件自己画字（这一套的样式表把这些部件的底和框设成透明）。只在皮肤要切角时（ChromeLook.CHAMFER）画；
    别的皮肤照部件自己的画法，这里什么都不做。"""

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Paint or not K.CHAMFER or not isinstance(obj, QWidget):
            return False
        button = isinstance(obj, QAbstractButton)
        enabled = obj.isEnabled()
        p = QPainter(obj)
        p.setRenderHint(QPainter.Antialiasing)
        decor.cut_button(p, QRectF(obj.rect()), K, _cut_kind(obj), enabled and obj.underMouse(),
                         button and obj.isDown(), enabled)
        p.end()
        type(obj).paintEvent(obj, event)
        return True


_CUT_BOX: CutBox | None = None


def cut_corners(widget: QWidget, kind: str = "") -> QWidget:
    """给样式表画的部件装上切角的框（CutBox；只在皮肤要切角时画，别的皮肤不变）。kind 为种类（不写时按钮按 accent 属性）；
    样式表可以按 cutbox 属性（"true"）把这些部件的底和框设成透明。返回 widget。"""
    global _CUT_BOX
    if _CUT_BOX is None:
        _CUT_BOX = CutBox()
    widget.setProperty("cutbox", "true")
    if kind:
        widget.setProperty("cut", kind)
    widget.setAttribute(Qt.WA_Hover)
    widget.installEventFilter(_CUT_BOX)
    return widget


def hint_label(text: str, color: str | None = None, pt: float | None = None) -> QLabel:
    """写在窗口顶上一行、标题后面的一句暗色说明（碳纤流光：「改动即时生效」「点击缩略图即可更换……」）：纯文本，颜色、字号
    只作用于它自己（不写时为 Common.DIM_COLOR、FONT_S）。"""
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    label.setObjectName("headHint")
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    common = theme.COMMON
    label.setStyleSheet(f".QLabel {{ color: {color or common.DIM_COLOR}; font-size: {pt or common.FONT_S}pt; "
                        f"background: transparent; }}")
    return label
