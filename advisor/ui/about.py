"""「关于」窗口：最上面的「免费软件」声明，以及版本、资料日期、资料来源与授权、许可证、第三方许可证、免责声明、隐私说明
（文字来自 advisor/version.py）。

version.about_sections() 里没有标题的一项（「免费软件」声明，version.FREE_NOTICE）在每种排法里都写在最上面：正文写在
文字框里的排法写成正文的第一段（没有小标题）；accordion 写在顶上的版本卡上面。网址都能点（用系统默认浏览器打开）。
样式表和正文里小标题的颜色在皮肤里（theme.about_style、theme.AboutLook）；换皮肤时（theme.set_skin）当场换（apply_skin）。
怎么排按皮肤取（LAYOUTS）：default 正文和右下角的「关闭」；panel 游戏内风格（左上角「返回」、窗口底色上写标题，深灰面板里
一张浅灰卡片放正文；皮肤自己画外框时没有系统标题栏，见 chrome.py）；plain、card 顶上一行写标题、右上角 ×，正文直接写在
窗口底上（plain）或在一张卡片里（card）；scroll、accordion 同样顶上一行写标题、右上角 ×，正文在一张卡片里的滚动框（scroll，
各节标题垫黑色斜带），或者顶上一张版本卡、其余各节一节一行点了展开收起（accordion）。show_section 打开时停在某一节（设置
窗口「说明」里的按钮用；accordion 展开那一节）。
窗口不超过所在屏幕的可用高度。
"""
from __future__ import annotations

import html

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import chrome, lang, theme
from .text import linkify
from .theme import ABOUT as L
from .window_base import SkinnedWindow

ABOUT_TITLE = "关于拾星助手"
# 读不到版本信息时（版本模块缺了或出错）：界面上不写内部文件名。原文由 services 导入、调用它的时候写进
# 错误记录（Services.load、Services.call）
NO_VERSION = "版本信息不可用：程序文件不完整，请把压缩包整个重新解压。"
HEADING_MARK = "◆"  # 皮肤要求时各节标题前面的小菱形（AboutLook.HEADING_MARK 的每种颜色各写一个）

# 怎么排：排法的名字（皮肤的 theme.Layouts.ABOUT）→ 搭界面的方法。plain：正文直接写在窗口底上（经典主题-黑）；
# card：正文在一张白卡片里（经典主题-反色）；scroll：正文在一张卡片里的滚动框，各节标题垫一条黑色斜带（霓虹贴纸）；
# accordion：顶上一张版本卡，下面每一节一行，点了展开、收起，同一时间只展开一节（白色海景房）；这几种都是顶上一行写标题、
# 右上角 ×（chrome.WindowHead）
LAYOUTS = {"default": "_build", "panel": "_build_panel", "plain": "_build_plain", "card": "_build_card",
           "scroll": "_build_scroll", "accordion": "_build_accordion"}
VERSION_SECTION = "版本"  # 「版本」一节（accordion 排法写在顶上的版本卡里，其余各节一行一行）
HANT_KEEP = ("VERSION_SECTION",)  # 用来和 version.about_sections() 的节名比，必须简体
SCREEN_MARGIN = 40  # 按屏幕可用高度缩小时，给系统标题栏和边框留的高度（自己画外框时不留）


def __getattr__(name: str):
    """样式表名（about.STYLE）：当前皮肤的样式表，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.about_style()
    return theme.legacy(__name__, name, ("about", "common"))


def about_html(sections: list[tuple[str, str]]) -> str:
    if not sections:
        return f"<p>{html.escape(NO_VERSION)}</p>"
    font = theme.font_css("title")
    parts = []
    for title, text in sections:
        if title:
            name = html.escape(lang.t(title))
            if L.HEADING_BG:  # 皮肤把小标题写成标签的样子（底色只铺在字后面）
                name = f'<span style="background-color:{L.HEADING_BG}">&nbsp;{name}&nbsp;</span>'
            if L.HEADING_MARK:  # 皮肤在小标题前面加几颗小菱形
                marks = "".join(f'<span style="color:{c}">{HEADING_MARK}</span>' for c in L.HEADING_MARK)
                name = f"{marks}&nbsp;{name}"
            parts.append(f'<h3 style="color:{L.HEADING_COLOR};margin:{L.HEADING_MARGIN}{font}">{name}</h3>')
        parts.append(f'<p style="margin:{L.PARAGRAPH_MARGIN}">{linkify(lang.t(text))}</p>')
    return "".join(parts)


class _BandBrowser(QTextBrowser):
    """霓虹贴纸「关于」的正文（scroll 排法）：各节标题下面垫一条黑色斜带（左头一小截黄，同各窗口的分组标题，decor.band_tag 的
    样子；颜色按 ChromeLook 的 TAG_FILL、TAG_NOTCH），标题字写在斜带上（标题的左边距留出那一小截）。斜带先画、字后画，
    跟着正文一起滚动。"""

    def paintEvent(self, event) -> None:
        from . import decor, theme

        k = theme.CHROME
        doc = self.document()
        dy = -self.verticalScrollBar().value()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and block.layout() is not None and block.layout().lineCount():
                layout = block.layout()
                line = layout.lineAt(0)
                pos = layout.position()
                left = pos.x() + line.x()
                rect = QRectF(left - 26, pos.y() + line.y() + dy - 4, 26 + line.naturalTextWidth() + 24,
                              line.height() + 8)
                if rect.bottom() >= 0 and rect.top() <= self.viewport().height():
                    p.fillPath(decor.slant(rect, k.SLANT), decor.qc(k.TAG_FILL[0]))
                    if k.TAG_NOTCH:
                        notch = QRectF(rect.left() + k.SLANT + 4, rect.top() + 6, 8, max(4.0, rect.height() - 12))
                        p.fillPath(decor.slant(notch, 5), decor.qc(k.TAG_NOTCH))
            block = block.next()
        p.end()
        super().paintEvent(event)


class _VersionCard(QFrame):
    """白色海景房「关于」顶上的版本卡（accordion 排法）：淡紫粉的渐变底（ChromeLook.ACCENT 调淡）、细框、下面一圈软阴影，
    左边一个大号的渐变小方块（decor.logo），右边版本、资料同步日期、运行环境三行（发行版有打包信息时运行环境上面多一行
    打包时间）。"""

    LOGO = 40
    SHADOW = 5

    def __init__(self, lines: list[str]):
        super().__init__()
        from . import theme

        c = theme.COMMON
        self.lines = list(lines)
        box = QHBoxLayout(self)
        box.setContentsMargins(16 + self.LOGO + 14, 12, 16, 12 + self.SHADOW)
        col = QVBoxLayout()
        col.setSpacing(1)
        for i, line in enumerate(self.lines):
            label = QLabel(lang.t(line))
            label.setTextFormat(Qt.PlainText)
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            label.setWordWrap(i > 0)
            if i == 0:
                label.setFont(theme.title_font(16))
                label.setStyleSheet(f"QLabel {{ color: {c.TEXT_COLOR}; }}")
            else:
                label.setStyleSheet(f"QLabel {{ color: {c.SOFT_COLOR if i == 1 else c.DIM_COLOR}; font-size: {c.FONT_S}pt; }}")
            col.addWidget(label)
        box.addLayout(col, 1)

    def paintEvent(self, event) -> None:
        from . import decor, theme

        k = theme.CHROME
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1 - self.SHADOW)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = decor.rounded(rect, 16)
        decor.soft_shadow(p, path, 0, 3, 6, k.SOFT_SHADOW)
        p.fillPath(path, decor.qc("#ffffff"))
        tint = []
        for value in decor.colors_of(k.ACCENT) if k.ACCENT else [k.CARD_EDGE]:
            q = decor.qc(value)
            tint.append((q.red(), q.green(), q.blue(), 26))
        p.fillPath(path, decor.fill_brush(tuple(tint) if len(tint) > 1 else tint[0], rect))
        p.strokePath(path, QPen(decor.qc(k.CARD_EDGE), 1))
        decor.logo(p, QRectF(rect.left() + 16, rect.center().y() - self.LOGO / 2, self.LOGO, self.LOGO), k)
        p.end()


class _FoldHead(QAbstractButton):
    """「关于」一节的一行（accordion 排法）：前面一颗小灯点、标题，右边浅灰圆钮里的折线箭头（展开时朝上）。点了展开、收起。"""

    HEIGHT = 38

    def __init__(self, title: str, color):
        super().__init__()
        self.setText(title)
        self._color = color
        self.open = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        from . import theme

        return QSize(round(QFontMetricsF(theme.title_font(14)).horizontalAdvance(self.text()) + 80), self.HEIGHT)

    def paintEvent(self, event) -> None:
        from . import decor, theme

        k, c = theme.CHROME, theme.COMMON
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        decor.led_dot(p, QPointF(8, h / 2), self._color)
        hover = self.underMouse() or self.hasFocus()
        decor.outlined_text(p, QRectF(22, 0, self.width() - 60, h), self.text(), theme.title_font(14),
                            c.MAP_COLOR if hover else c.TEXT_COLOR, align=Qt.AlignLeft)
        center = QPointF(self.width() - 14, h / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(decor.qc(k.SURFACE))
        p.drawEllipse(center, 11, 11)
        p.setPen(QPen(decor.qc(c.SOFT_COLOR), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        s = 3.8
        if self.open:
            pts = [QPointF(center.x() - s, center.y() + s / 2), QPointF(center.x(), center.y() - s / 2),
                   QPointF(center.x() + s, center.y() + s / 2)]
        else:
            pts = [QPointF(center.x() - s, center.y() - s / 2), QPointF(center.x(), center.y() + s / 2),
                   QPointF(center.x() + s, center.y() - s / 2)]
        p.drawPolyline(pts)
        if self.hasFocus():
            p.setPen(QPen(decor.qc(c.MAP_COLOR), 1.2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 4, -1, -4), 10, 10)
        p.end()


class _Fold(QFrame):
    """「关于」的一节（accordion 排法）：一行标题（_FoldHead），展开时下面写这一节的正文（链接能点）。收起时是一条白色的胶囊、
    浅灰细框；展开时白底、紫粉渐变的框线、下面一圈软阴影。clicked(本节) 由窗口接：同一时间只展开一节。"""

    SHADOW = 4

    def __init__(self, title: str, text: str, color, on_click):
        super().__init__()
        from . import theme

        self.title, self.plain = title, text
        self.head = _FoldHead(lang.t(title), color)
        self.head.clicked.connect(lambda: on_click(self))
        c = theme.COMMON
        self.body = QLabel(f'<div style="color:{c.SOFT_COLOR}; line-height:140%">{linkify(lang.t(text))}</div>')
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setOpenExternalLinks(True)  # 点链接用系统默认浏览器打开（用户自己点才打开）
        self.body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.body.setContentsMargins(8, 0, 6, 10)
        self.body.hide()
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 0, 10, self.SHADOW)
        box.setSpacing(0)
        box.addWidget(self.head)
        box.addWidget(self.body)

    @property
    def is_open(self) -> bool:
        return self.body.isVisibleTo(self)

    def set_open(self, on: bool) -> None:
        self.body.setVisible(on)
        self.head.open = on
        self.head.update()
        self.update()

    def paintEvent(self, event) -> None:
        from . import decor, theme

        k = theme.CHROME
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1 - self.SHADOW)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.head.open:
            path = decor.rounded(rect, 14)
            decor.soft_shadow(p, path, 0, 3, 6, k.SOFT_SHADOW)
            p.fillPath(path, decor.qc("#ffffff"))
            p.strokePath(path, QPen(decor.fill_brush(k.ACCENT or k.CARD_EDGE, rect), 1.6))
        else:
            path = decor.rounded(rect, min(18.0, rect.height() / 2))
            p.fillPath(path, decor.qc(k.CARD_FILL))
            p.strokePath(path, QPen(decor.qc(k.CARD_EDGE), 1))
        p.end()


class AboutDialog(SkinnedWindow, chrome.HideNotice, QDialog):
    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）
    LAYOUT_FIELD = "ABOUT"
    LAYOUTS = LAYOUTS  # 排法名 → 建法（模块级的表：test-same 这类后加进来的也认）

    def __init__(self, sections: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(ABOUT_TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setStyleSheet(theme.about_style())
        self.resize(*L.SIZE)
        self._sections = list(sections)
        self._section: str | None = None
        self._lead_texts: list[str] = []  # accordion 排法写在版本卡上面的、没有标题的几项（「免费软件」声明）
        self.back = None
        self.build_layout()
        chrome.frameless(self, self.back is not None, self.FLAGS)
        self.subscribe_skin()

    def _build(self) -> None:
        """搭界面（默认的排法）：正文（browser，可滚动，链接可点）和右下角的「关闭」按钮。"""
        box = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)  # 点链接用系统默认浏览器打开（用户自己点才打开）
        self.browser.setHtml(about_html(self._sections))
        box.addWidget(self.browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.close)
        row.addWidget(close)
        box.addLayout(row)

    def _build_panel(self) -> None:
        """游戏内风格的排法：窗口底色上左上角「返回」（皮肤自己画外框时）和标题，下面深灰面板里一张浅灰卡片放正文
        （browser，可滚动，链接可点）。没有「关闭」按钮：「返回」、Esc 关闭。"""
        from . import chrome

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        box = QVBoxLayout(frame)
        box.setContentsMargins(18, 8, 18, 18)
        box.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(12)
        if chrome.custom():
            self.back = chrome.BackButton()
            head.addWidget(self.back)
        head.addWidget(chrome.TitleText(ABOUT_TITLE), 0, Qt.AlignVCenter)
        head.addStretch(1)
        box.addLayout(head)
        panel = chrome.Panel(seed=9)
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(18, 22, 18, 22)
        card = chrome.Card(seed=40, margins=(14, 12, 14, 12))
        self.browser = QTextBrowser()
        self.browser.setObjectName("aboutText")
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(about_html(self._sections))
        card.box.addWidget(self.browser)
        inner.addWidget(card)
        box.addWidget(panel, 1)

    def _head(self):
        """自己画外框、右上角 × 的排法（plain、card）共用：窗口底（chrome.Frame）和顶上一行（标题、×；chrome.WindowHead），
        返回（窗口底, 放正文的竖排布局）。没有「关闭」「返回」按钮：右上角 ×、Esc 关闭（用系统标题栏的皮肤另放「关闭」）。"""
        from . import chrome

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        box = QVBoxLayout(frame)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        head = chrome.WindowHead(ABOUT_TITLE)
        self.back = head.closer
        box.addWidget(head)
        body = QVBoxLayout()
        box.addLayout(body, 1)
        self.browser = QTextBrowser()
        self.browser.setObjectName("aboutText")
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(about_html(self._sections))
        if self.back is None:
            row = QHBoxLayout()
            row.addStretch(1)
            close = QPushButton("关闭")
            close.setCursor(Qt.PointingHandCursor)
            close.clicked.connect(self.close)
            row.addWidget(close)
            box.addLayout(row)
        return frame, body

    def _build_plain(self) -> None:
        """经典主题-黑的排法：棋盘格的窗口底上直接写正文（各节标题前面两颗小菱形），顶上正中的标题页签、右上角 ×。"""
        _, body = self._head()
        body.setContentsMargins(30, 14, 22, 22)
        body.addWidget(self.browser)

    def _build_card(self) -> None:
        """经典主题-反色的排法：顶上黑色通栏写标题、右上角 ×，下面一张白卡片放正文（各节标题写在黑底的标签里）。"""
        from . import chrome

        _, body = self._head()
        body.setContentsMargins(22, 24, 22, 20)
        card = chrome.Card(seed=40, margins=(16, 14, 16, 12))
        card.box.addWidget(self.browser)
        body.addWidget(card)

    def _build_scroll(self) -> None:
        """霓虹贴纸的排法：顶上一行写标题、右上角 ×；下面一张贴纸卡片里是正文的滚动框，各节标题垫黑色斜带（_BandBrowser）。"""
        from . import chrome

        _, body = self._head()
        body.setContentsMargins(20, 4, 22, 22)
        card = chrome.Card(seed=40, margins=(14, 12, 8, 12))
        browser = _BandBrowser()
        browser.setObjectName("aboutText")
        browser.setOpenExternalLinks(True)
        browser.setHtml(about_html(self._sections))
        self.browser = browser
        card.box.addWidget(browser)
        body.addWidget(card, 1)

    def _build_accordion(self) -> None:
        """白色海景房的排法：顶上一行写标题、右上角 ×；最上面是没有标题的几项（「免费软件」声明，链接可点）；下面一张版本卡
        （「版本」一节的版本、资料同步日期、发行版的打包时间、运行环境，_VersionCard），这一节最后一行的说明写在卡片下面；再下面
        其余各节一节一行（_Fold），点一行展开、收起，同一时间只展开一节（打开时展开第一节；跳到某一节时展开那一节，
        show_section）。各节放在滚动区里，屏幕矮时在窗口里滚动。"""
        from . import theme as th

        _, body = self._head()
        body.setContentsMargins(18, 2, 18, 18)
        body.setSpacing(8)
        c, k = th.COMMON, th.CHROME
        self.folds: dict[str, _Fold] = {}
        self._version_lines: list[str] = []
        sections = list(self._sections)
        self._lead_texts = [text for title, text in sections if not title]
        for text in self._lead_texts:
            lead = QLabel(f'<div style="color:{c.TEXT_COLOR}; line-height:140%">{linkify(lang.t(text))}</div>')
            lead.setObjectName("aboutLead")
            lead.setTextFormat(Qt.RichText)
            lead.setWordWrap(True)
            lead.setOpenExternalLinks(True)  # 点链接用系统默认浏览器打开（用户自己点才打开）
            lead.setTextInteractionFlags(Qt.TextBrowserInteraction)
            lead.setContentsMargins(4, 0, 4, 0)
            body.addWidget(lead)
        version = next((text for title, text in sections if title == VERSION_SECTION), None)
        if version is not None:
            lines = [line for line in version.split("\n") if line.strip()]
            self._version_lines = lines
            # 卡里放最后一行以外的几行（版本、资料同步日期、发行版的打包时间、运行环境），最后一行（非官方工具的说明）写在卡片
            # 下面；这一节只有三行以内时都放进卡里
            card = len(lines) - 1 if len(lines) > 3 else len(lines)
            self.version_card = _VersionCard(lines[:card])
            body.addWidget(self.version_card)
            for line in lines[card:]:
                note = QLabel(lang.t(line))
                note.setTextFormat(Qt.PlainText)
                note.setTextInteractionFlags(Qt.NoTextInteraction)
                note.setWordWrap(True)
                note.setStyleSheet(f"QLabel {{ color: {c.SOFT_COLOR}; font-size: {c.FONT_S}pt; padding: 0px 4px; }}")
                body.addWidget(note)
        elif not sections:
            missing = QLabel(NO_VERSION)
            missing.setTextInteractionFlags(Qt.NoTextInteraction)
            missing.setWordWrap(True)
            body.addWidget(missing)
        scroll = self._fold_scroll = QScrollArea()
        scroll.setObjectName("aboutFolds")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea#aboutFolds, QScrollArea#aboutFolds > QWidget#qt_scrollarea_viewport "
                             "{ background: transparent; border: none; }")
        inner = QWidget()
        inner.setAutoFillBackground(False)
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(4)
        leds = list(k.LED_COLORS.values()) or [c.MAP_COLOR]
        for i, (title, text) in enumerate(s for s in sections if s[0] and s[0] != VERSION_SECTION):
            fold = _Fold(title, text, leds[i % len(leds)], self._fold_clicked)
            self.folds[title] = fold
            column.addWidget(fold)
        column.addStretch(1)
        scroll.setWidget(inner)
        body.addWidget(scroll, 1)
        if self.folds:
            self.open_fold(next(iter(self.folds)))

    def open_fold(self, title: str | None) -> None:
        """accordion 排法：展开标题为 title 的那一节，其余收起（title 为 None 时全部收起）。不是 accordion 排法时
        （没有各节）什么都不做。"""
        for name, fold in getattr(self, "folds", {}).items():
            fold.set_open(name == title)

    def opened(self) -> str | None:
        """accordion 排法：展开着的那一节的标题；都收着、或不是 accordion 排法时为 None。"""
        return next((name for name, fold in getattr(self, "folds", {}).items() if fold.is_open), None)

    def _fold_clicked(self, fold) -> None:
        """点一节的标题：收着的展开（别的收起），展开着的收起。"""
        self.open_fold(None if fold.is_open else fold.title)

    def _build_toc(self) -> None:
        """碳纤流光的排法：顶上一行写标题、右上角 ×；下面左边一列目录（各节的编号和标题，_TocItem），一条竖线，右边正文
        （_IndexBrowser：各节标题左边写编号、后面一条线）。点目录跳到那一节（show_section）；正文滚动时目录跟着标出最上面的
        那一节。"""
        from . import decor

        k = theme.CHROME
        _, body = self._head()
        body.setContentsMargins(24, 6, 26, 24)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        toc = QWidget()
        toc.setObjectName("aboutToc")
        toc.setFixedWidth(TOC_WIDTH)
        column = QVBoxLayout(toc)
        column.setContentsMargins(0, 2, 0, 0)
        column.setSpacing(4)
        self.toc: dict[str, _TocItem] = {}
        for i, (title, _text) in enumerate(s for s in self._sections if s[0]):
            item = _TocItem(f"{i + 1:02d}", title)
            item.clicked.connect(lambda _=False, t=title: self._toc_clicked(t))
            column.addWidget(item)
            self.toc[title] = item
        column.addStretch(1)
        columns.addWidget(toc)
        rule = QFrame()
        rule.setFixedWidth(1)
        rule.setStyleSheet(f"QFrame {{ background: {decor.qc(k.CARD_EDGE).name()}; border: none; }}")
        columns.addWidget(rule)
        self.browser = _IndexBrowser()
        self.browser.setObjectName("aboutText")
        self.browser.setOpenExternalLinks(True)  # 点链接用系统默认浏览器打开（用户自己点才打开）
        self.browser.setHtml(toc_html(self._sections))
        self.browser.verticalScrollBar().valueChanged.connect(self._sync_toc)
        # 正文排好版（显示出来、改了宽度）以后各节的位置才对：那时再标一次
        self.browser.document().documentLayout().documentSizeChanged.connect(self._sync_toc)
        columns.addWidget(self.browser, 1)
        body.addLayout(columns, 1)
        self._sync_toc()

    def _toc_clicked(self, title: str) -> None:
        """点目录的一项：正文跳到那一节，目录标出这一项。"""
        self.show_section(title)
        self._mark_toc(title)

    def _mark_toc(self, title: str | None) -> None:
        for name, item in getattr(self, "toc", {}).items():
            item.set_current(name == title or lang.t(name) == title)

    def _sync_toc(self, *_) -> None:
        """正文滚动了：目录标出最上面露出来的那一节（滚到底时为最后一节）。"""
        if self.layout_key != "toc":
            return
        bar = self.browser.verticalScrollBar()
        doc = self.browser.document()
        current = None
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().headingLevel():
                if doc.documentLayout().blockBoundingRect(block).top() <= bar.value() + 8 or current is None:
                    current = block.text().strip()
            block = block.next()
        if bar.maximum() > 0 and bar.value() >= bar.maximum() and self._section in getattr(self, "toc", {}):
            current = self._section  # 最后几节滚不到最上面：跳过去的那一节照旧标着
        self._mark_toc(current)

    def apply_skin(self, skin=None) -> None:
        """换皮肤：重新套样式表、按新皮肤的颜色重排正文（滚动位置照旧）。排法变了的窗口要重建（悬浮窗做：layout_key 和
        layout_name() 不同时关掉重开）。accordion 排法的各节自己画（颜色按皮肤取），重画即可。"""
        self.setStyleSheet(theme.about_style())
        if self.layout_key == "accordion":
            self.update()
            return
        bar = self.browser.verticalScrollBar()
        at = bar.value()
        self.browser.setHtml(toc_html(self._sections) if self.layout_key == "toc" else about_html(self._sections))
        bar.setValue(at)

    def state(self) -> dict:
        """交出的状态：所在章节、正文滚动位置、accordion 展开的那一节，加上窗口的位置。"""
        s = super().state()
        s["section"] = self._section
        browser = getattr(self, "browser", None)
        s["scroll"] = browser.verticalScrollBar().value() if browser is not None else 0
        if self.layout_key == "accordion":
            s["fold"] = self.opened()
        return s

    def restore(self, state) -> None:
        """接回 state() 交出的状态：章节、滚动位置、展开的那一节，窗口位置按屏幕挪回来。"""
        if not state:
            return
        super().restore(state)
        if state.get("section"):
            self.show_section(state["section"])
        browser = getattr(self, "browser", None)
        if browser is not None and state.get("scroll"):
            browser.verticalScrollBar().setValue(state["scroll"])
        if self.layout_key == "accordion" and state.get("fold"):
            self.open_fold(state["fold"])

    def text(self) -> str:
        if self.layout_key == "accordion":
            parts = [*self._lead_texts, *self._version_lines]
            for title, fold in self.folds.items():
                parts += [title, fold.plain]
            return "\n".join(parts)
        return self.browser.toPlainText()

    def show_section(self, title: str | None) -> bool:
        """正文滚到标题为 title 的那一节（这一节的标题在最上面）。没有这一节时不动，返回 False。窗口还没排好（没显示）时
        记下来，显示以后再滚。accordion 排法：展开那一节（别的收起），滚到它露出来；「版本」一节在顶上的版本卡里。"""
        self._section = title
        if not title:
            return False
        if self.layout_key == "accordion":
            return self._show_fold(title)
        doc = self.browser.document()
        block = doc.begin()
        while block.isValid():
            # 标题前面皮肤加的小菱形（HEADING_MARK）不算
            if block.text().strip().lstrip(HEADING_MARK).strip().replace(" ", "") == lang.t(title):
                break
            block = block.next()
        else:
            return False
        if not self.isVisible():
            return True
        top = doc.documentLayout().blockBoundingRect(block).top()
        self.browser.verticalScrollBar().setValue(int(top))
        return True

    def _show_fold(self, title: str) -> bool:
        if title == VERSION_SECTION and self._version_lines:
            self._fold_scroll.verticalScrollBar().setValue(0)
            return True
        fold = self.folds.get(title)
        if fold is None:
            return False
        self.open_fold(title)
        if self.isVisible():
            QTimer.singleShot(0, self, lambda f=fold: self._fold_scroll.verticalScrollBar().setValue(f.y()))
        return True

    def _available(self) -> QRect | None:
        return self.available_area()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        area = self._available()
        if area is not None:
            margin = 0 if self.back is not None else SCREEN_MARGIN
            limit = max(self.minimumSizeHint().height(), area.height() - margin)
            if self.height() > limit:
                self.resize(self.width(), limit)
        if self._section:
            QTimer.singleShot(0, self, lambda: self.show_section(self._section))


# ------------------------------------------------------------ 碳纤流光的排法：左边目录、右边正文（toc）

TOC_WIDTH = 168  # 左边目录一列的宽
LAYOUTS["toc"] = "_build_toc"


def toc_html(sections: list[tuple[str, str]]) -> str:
    """toc 排法的正文：各节标题斜着写（标题字体），左边留出写编号的地方（AboutLook.HEADING_MARGIN 的左边距；编号和标题后面
    的线由 _IndexBrowser 画，不写进正文）；正文同 about_html。"""
    if not sections:
        return f"<p>{html.escape(NO_VERSION)}</p>"
    font = theme.font_css("title")
    parts = []
    for title, text in sections:
        if title:
            parts.append(f'<h3 style="color:{L.HEADING_COLOR};margin:{L.HEADING_MARGIN};font-style:italic{font}">'
                         f"{html.escape(lang.t(title))}</h3>")
        parts.append(f'<p style="margin:{L.PARAGRAPH_MARGIN}">{linkify(lang.t(text))}</p>')
    return "".join(parts)


class _IndexBrowser(QTextBrowser):
    """toc 排法的正文：各节标题左边留出的地方写编号（ChromeLook.INDEX_INK，数字字体的窄体），标题后面一小截亮色、一条细线
    到右边（decor.index_rule），跟着正文一起滚动。编号不写进正文（找某一节、取文字时照旧只是标题）。"""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        from . import chrome, decor

        k = theme.CHROME
        doc = self.document()
        dy = -self.verticalScrollBar().value()
        width = self.viewport().width()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        number = 0
        block = doc.begin()
        while block.isValid():
            layout = block.layout()
            if block.blockFormat().headingLevel() and layout is not None and layout.lineCount():
                number += 1
                line = layout.lineAt(0)
                pos = layout.position()
                top = pos.y() + line.y() + dy
                if top + line.height() >= 0 and top <= self.viewport().height():
                    left = pos.x() + line.x()
                    lead = QRectF(left - block.blockFormat().leftMargin(), top, block.blockFormat().leftMargin(),
                                  line.height())
                    p.setFont(chrome.index_font(line.height() * 0.62))
                    p.setPen(decor.qc(k.INDEX_INK or theme.COMMON.TEXT_COLOR))
                    p.drawText(lead, Qt.AlignVCenter | Qt.AlignLeft, f"{number:02d}")
                    decor.index_rule(p, left + line.naturalTextWidth() + 12, width - 6, top + line.height() / 2, k)
            block = block.next()
        p.end()


class _TocItem(QAbstractButton):
    """toc 排法左边目录的一项：编号（数字字体的窄体）和这一节的标题。标着的一项（set_current）左边一根亮色竖条
    （ChromeLook.HALO）、往右渐淡的底，编号亮色、标题正文色；其余编号、标题暗色，鼠标移上去标题变亮。点了跳到这一节。"""

    HEIGHT = 34

    def __init__(self, number: str, title: str):
        super().__init__()
        self.number = number
        self.setText(lang.t(title))  # 显示的标题照界面语言；找那一节、标出当前项按 AboutDialog.toc 的键（简体节名）
        self.current = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_current(self, on: bool) -> None:
        if self.current != on:
            self.current = on
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(TOC_WIDTH, self.HEIGHT)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QFont, QLinearGradient

        from . import chrome, decor

        k, c = theme.CHROME, theme.COMMON
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        halo = decor.qc(k.HALO or c.MAP_COLOR)
        if self.current:
            fade = QLinearGradient(rect.left(), 0, rect.right(), 0)
            start, end = QColor(halo), QColor(halo)
            start.setAlpha(46)
            end.setAlpha(0)
            fade.setColorAt(0, start)
            fade.setColorAt(1, end)
            p.fillRect(rect, fade)
            p.fillRect(QRectF(rect.left(), rect.top() + 4, 3, rect.height() - 8), halo)
        hover = self.underMouse() or self.hasFocus()
        p.setFont(chrome.index_font(15))
        p.setPen(halo if self.current else decor.qc(c.CAPTION_COLOR))
        p.drawText(QRectF(rect.left() + 14, rect.top(), 26, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, self.number)
        font = QFont(self.font())
        font.setPointSizeF(c.FONT_M)
        p.setFont(font)
        p.setPen(decor.qc(c.TEXT_COLOR if self.current or hover else c.DIM_COLOR))
        text_rect = QRectF(rect.left() + 46, rect.top(), rect.width() - 50, rect.height())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(font).elidedText(self.text(), Qt.ElideRight, text_rect.width()))
        p.end()


lang.localize(globals())
