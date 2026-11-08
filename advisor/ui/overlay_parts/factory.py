"""悬浮窗的部件工厂：按钮、标签、行列布局、悬停说明的写法，自动换行的标签，
以及悬浮窗自己画的面板（_Panel）和图标在上、字在下的按钮（IconTextButton）。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..glyphs import ICON_BOOK, draw_glyph, glyph_font, glyph_width
from ..text import keep_words, plain_words, tip_html, tip_paragraphs_html
from ..theme import COMMON as C
from ..theme import OVERLAY as O
from ..widgets import TaggedText, free_item


def _own_style(widget: QWidget, rules: str) -> None:
    """给部件本身设样式（rules 是「color:…;font-size:…」这样的几项），用类选择器（「.QLabel { … }」）只匹配部件本身。

    不带选择器的样式表对部件的悬停说明同样生效：Qt 的悬停说明控件以被悬停的部件为样式来源，部件自己的样式表又优先于
    悬浮窗样式表里的 QToolTip 规则，悬停说明会变成部件的文字颜色、字号和粗细（例如首推推荐度的悬停说明变成大号粗体白字，
    按小字定宽的表格装不下又被折成多行）。"""
    widget.setStyleSheet(f".{widget.metaObject().className()} {{ {rules} }}")


def _set_tip(widget: QWidget, text: str) -> None:
    """设置纯文字的悬停说明：长的照理由标签的做法按 tip_lines 分行（text.tip_paragraphs_html；以换行分段的每段各自分行），
    短的照写。Qt 不给纯文本的悬停说明自动换行，长句会排成很宽的一行。自己拼好的富文本（筹码名的明细等）不经过这里。"""
    widget.setToolTip(tip_paragraphs_html(text))


def _pill_tip(text: str) -> str:
    """推荐卡片里理由标签、位次的悬停说明：一段的照理由标签的做法（text.tip_html），以换行分成几段的（位次后面另起一行的
    「只能再进入一个」、连续刷新时的概率）每段各自分行（text.tip_paragraphs_html）。"""
    return tip_paragraphs_html(text) if "\n" in text else tip_html(text)


def _button(text: str, accent: str = "", color: str | None = None, tip: str = "") -> QPushButton:
    """有底色和框线的按钮（样式见悬浮窗样式表 theme.overlay_style）。accent 选一种强调色，color 只改文字颜色。"""
    btn = QPushButton(text)
    if accent:
        btn.setProperty("accent", accent)
    if color:
        _own_style(btn, f"color:{color}")
    if tip:
        _set_tip(btn, tip)
    btn.setCursor(Qt.PointingHandCursor)
    from .. import chrome

    chrome.cut_corners(btn)  # 皮肤要切角时（碳纤流光）按钮画成切角的框；别的皮肤照样式表画
    return btn


def _set_accent(btn: QPushButton, accent: str) -> None:
    if (btn.property("accent") or "") != accent:
        btn.setProperty("accent", accent)
        btn.style().unpolish(btn)
        btn.style().polish(btn)


def _clear_grid(grid) -> None:
    while grid.count():
        item = grid.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()  # 可能正在处理这个按钮的点击，不能立即删
        free_item(item)  # 取出后调用方负责删，条目在 Qt 那边也一并释放


def _fill_grid(grid: QGridLayout, widgets: list[QWidget], width: int) -> None:
    """把按钮排进网格：一行放得下（不超过 width）就排成一行，否则每行两个。"""
    _clear_grid(grid)
    for w in widgets:
        grid.addWidget(w, 0, grid.count())  # 先放进来，按窗口的样式表量出尺寸
        w.ensurePolished()
    spacing = max(grid.horizontalSpacing(), 0)
    total = sum(w.sizeHint().width() for w in widgets) + spacing * (len(widgets) - 1)
    cols = len(widgets) if total <= width else 2
    for w in widgets:
        grid.removeWidget(w)
    for i, w in enumerate(widgets):
        grid.addWidget(w, i // cols, i % cols)
        w.show()


def _equal_width(buttons: list[QPushButton]) -> None:
    """几个按钮一样宽，并且每个都不少于按它的字体写三个汉字的宽度：一个字的按钮（〔换〕〔不换〕）也好点。按钮文字以外的
    内边距按 sizeHint().width() 减去文字本身的宽度量（各套皮肤的字号、内边距不同，不写死像素）。按钮要先放进悬浮窗，
    按它的样式表量。"""
    widths = []
    for btn in buttons:
        btn.ensurePolished()
        fm = btn.fontMetrics()
        natural = btn.sizeHint().width()
        pad = max(natural - fm.horizontalAdvance(btn.text()), 0)
        widths.append(max(natural, pad + fm.horizontalAdvance("三个字")))
    width = max(widths, default=0)
    for btn in buttons:
        btn.setFixedWidth(width)


def _row_widget(spacing: int = 6) -> tuple[QWidget, QHBoxLayout]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return w, lay


def _grid() -> QGridLayout:
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(6)
    return grid


def _column_widget(spacing: int = 5) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return w, lay


def _clear_layout(layout) -> None:
    """移除布局里的全部部件和空白。部件隐藏后 deleteLater（可能正在处理其中某个按钮的点击，不能立即删）；
    取出的部件、空白条目在 Qt 那边也一并释放（free_item；子布局取出以后归 Python 管，不在这里删）。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
        free_item(item)


def _rgba(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


class WrapLabel(QLabel):
    """自动换行的纯文字标签（卡片里的提示、附注）：Qt 换行时名称、术语和常用双字词不从中间拆开（text.keep_words 在词内插入
    不显示的 WORD JOINER，例如不会拆成「打 / 开任务栏」「不 / 计前瞻」；数字旁的空格换成不换行空格）。text() 返回原文，
    不含插入、替换的字符。"""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802  Qt 的方法名
        super().setText(keep_words(text))

    def text(self) -> str:
        return plain_words(super().text())


class WrapTaggedText(TaggedText):
    """筹码小卡片的第二行（widgets.TaggedText：流派标签后面紧跟效果描述）：效果描述换行时名称、术语和常用双字词不从中间
    拆开，和 WrapLabel 同一套（text.keep_words；例如不会拆成「所有角色的轮次星 / 币奖励 +3」）。插入的字符不显示、
    不占宽度，小卡片的宽度和高度只随换行位置变化。text() 返回原文，不含插入的字符。"""

    def __init__(self, tag: str, tag_tone: str, text: str, color: str, *, point_size: float | None = None):
        super().__init__(tag, tag_tone, keep_words(text), color, point_size=point_size)

    def text(self) -> str:
        return plain_words(super().text())


def _text_rules(size: float | None = None, color: str | None = None, bold: bool = False, number: bool = False) -> str:
    """纯文字标签自己的样式（_own_style 的写法）：字号、颜色没写时按当前皮肤的正文（C.FONT_M、C.TEXT_COLOR）；number 为真时
    用皮肤的数字字体（推荐度、位次；皮肤没写数字字体时不加）。"""
    size = C.FONT_M if size is None else size
    color = C.TEXT_COLOR if color is None else color
    box = ""
    if O.WARN_BOX and color == C.WARN_COLOR:  # 皮肤把警告色的提示写在另一种底色上：字换成那种底上用的颜色
        color, box = O.WARN_BOX_TEXT or theme.ink(color), ";" + O.WARN_BOX
    rules = f"color:{color};font-size:{size}pt;font-weight:{700 if bold else 400}" + box
    return rules + theme.font_css("number") if number else rules


def _capsule(label: QLabel, rules: str, background: str, padding: str) -> None:
    """文字写在 background 底色的胶囊里（皮肤的 MARK_BG、REROLL_BG）：rules 是 _text_rules 给的字号、颜色。圆角取量出来的
    高度的一半（略小一点：样式表的圆角比高度的一半大时整个不画圆角）。"""
    box = f"{rules};background:{background};padding:{padding or '1px 10px'}"
    _own_style(label, box)
    label.ensurePolished()
    _own_style(label, f"{box};border-radius:{max(3, label.sizeHint().height() // 2 - 1)}px")


def _text_label(text: str, size: float | None = None, color: str | None = None, bold: bool = False,
                wrap: bool = False, number: bool = False) -> QLabel:
    """纯文字的标签：字号只用 C.FONT_* 的 4 档（不写时为正文 C.FONT_M、颜色 C.TEXT_COLOR，按当前皮肤）。颜色、字号和粗细
    只作用于标签本身，不带到它的悬停说明上（_own_style）。自动换行的（wrap）用 WrapLabel：词不从中间拆开。number 见
    _text_rules。"""
    label = WrapLabel(text) if wrap else QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)  # 不接鼠标按下和拖动，按在文字上也能拖动悬浮窗
    _own_style(label, _text_rules(size, color, bold, number))
    label.setWordWrap(wrap)
    return label


def _rich_label(html_text: str = "", wrap: bool = True) -> QLabel:
    label = QLabel(html_text)
    label.setTextFormat(Qt.RichText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)  # 富文本标签默认接走鼠标；关掉文字交互，按在文字上也能拖动悬浮窗
    label.setWordWrap(wrap)
    return label


def _alive(widget) -> bool:
    """部件建过、底层的 Qt 对象还在（窗口关闭时如果被删掉了，例如设了关闭即删除，就要重新建）。"""
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
    except ImportError:  # 不该出现：PySide6 自带 shiboken6
        return True
    return isValid(widget)


def _plain(text: str) -> str:
    """标签上的富文本 → 纯文本（测试和诊断包用）。"""
    if "<" not in text:
        return text
    doc = QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText()


def _message_label() -> QLabel:
    """卡片里（或标题栏下方）显示一次性提示的一行（自动换行，词不从中间拆开：WrapLabel）。纯文本：提示里可能有方案名这类
    用户写的文字，带网页标记时原样显示，不渲染。"""
    label = WrapLabel("")
    label.setTextFormat(Qt.PlainText)
    label.setObjectName("msg")
    label.hide()
    return label


class _Panel(QFrame):
    """悬浮窗的面板（#panel，底色、框线照样式表）。皮肤的 PANEL_DECOR 为 chrome 时在底色上另画游戏内风格的几何小图案和
    两条斜带（ChromeLook，decor.panel），画在卡片下面。斜带不压在字下面：一行显示时（标题栏里的一行摘要直接写在面板上，
    摘要长时伸到斜带那里）不画斜带，卡片多、面板变高时也不画；全部展开时面板上直接写的只有卡片和按钮，画斜带。
    PANEL_DECOR 为 board、stripes 时整块面板自己画（样式表不画底和框，_paint_game）。

    画的时候要看标题栏那一行的几个控件显示着没有、占在哪里：搭界面时把它们交进来（head：title、summary、toggle_btn、
    close_btn、quick_add → 控件），面板不顺着父子关系到悬浮窗上去找。还没交进来时按都没有算。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.head: dict = {}

    def _stripes(self) -> bool:
        summary = self.head.get("summary")
        return self.height() > 120 and not (summary is not None and summary.isVisibleTo(self))

    def _paint_game(self) -> None:
        """经典主题-黑（board）、反色（stripes）的面板：
        - board：圆角外框里一圈棋盘格；全部展开时外框从标题页签的上半截往下画（页签压在上沿正中），竖纹在顶边、标题页签和
          按钮压在上面；一行显示时竖纹在左边一窄条（摘要直接写在面板上，字不压在竖纹上）；
        - stripes：宽斜条的底，标题栏那一行垫一条黑色通栏（标题、摘要、按钮都写在通栏里）。
        按不透明画：平时透出下方的游戏画面靠整个窗口的透明度（皮肤的 WINDOW_ALPHA，Overlay.see_through），「悬浮窗背景
        不透明」时窗口不透明。识别暂停时虚线框（窗口更透明）；需要用户操作时另描一圈警告色的框。"""
        from .. import decor

        title = self.head.get("title")
        full = title is not None and title.isVisibleTo(self)
        paused, attention = bool(self.property("paused")), bool(self.property("attention"))
        alpha = 1.0
        border = O.PANEL_ATTENTION_BORDER if attention else ((255, 255, 255, 150) if paused else None)
        rect = QRectF(self.rect())
        look = theme.CHROME
        if O.PANEL_DECOR == "board":
            top = float(title.geometry().top() + 18) if full else 0.0
            key = ("board", theme.current_key(), full, top, alpha, attention, paused)
            draw = lambda q: decor.board_panel(q, rect, look, top, "top" if full else "left", alpha, border,  # noqa: E731
                                               paused and not attention)
        else:
            band = self._band_height()
            key = ("stripes", theme.current_key(), band, alpha, attention, paused)
            draw = lambda q: decor.stripes_panel(q, rect, look, band, alpha, border, paused and not attention)  # noqa: E731
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, decor.cached(self, key, self.size(), draw))
        p.end()

    def _band_height(self) -> float:
        """黑色通栏的高：标题栏那一行（标题、摘要、按钮里最低的那个）的下沿往下 8 像素。"""
        bottom = 0
        for name in ("title", "summary", "toggle_btn", "close_btn", "quick_add"):
            widget = self.head.get(name)
            if widget is not None and widget.isVisibleTo(self):
                bottom = max(bottom, widget.geometry().bottom())
        return float(bottom + 9) if bottom else 46.0

    def _paint_rgb(self) -> None:
        """霓虹贴纸（neon）、白色海景房（seaview）的面板，整块自己画（样式表不画底和框）：
        - neon：圆角的夜紫渐变、墨黑粗框、右下一截实心投影；全部展开时铺淡紫网点：右上角的只在标题栏那一行、标题右边（让开
          按钮和 ×），左下角的只在左边、下边贴边的一圈，网点不压在字下面；一行显示时不铺；
        - seaview：浅灰白底、角落透出光晕、一圈彩色渐变灯边；只有一行时两头是半圆。
        按不透明画：平时透出下方的游戏画面靠整个窗口的透明度（皮肤的 WINDOW_ALPHA，Overlay.see_through），「悬浮窗背景
        不透明」时窗口不透明。识别暂停时框线画成虚线（窗口更透明）；需要用户操作时另描一圈警告色的框。"""
        from .. import decor

        title = self.head.get("title")
        full = title is not None and title.isVisibleTo(self)
        paused, attention = bool(self.property("paused")), bool(self.property("attention"))
        alpha = 1.0
        border = O.PANEL_ATTENTION_BORDER if attention else None
        rect = QRectF(self.rect())
        look = theme.CHROME
        if O.PANEL_DECOR == "neon":
            head, right, keep = self._head_box() if full else (0.0, 0.0, ())
            key = ("neon", theme.current_key(), full, alpha, attention, paused, head, right, keep)
            draw = lambda q: decor.neon_panel(q, rect, look, not full, alpha, border, paused, head, right,  # noqa: E731
                                              [QRectF(*b) for b in keep])
        else:
            line = not full and self.height() < 70
            key = ("seaview", theme.current_key(), line, alpha, attention, paused)
            draw = lambda q: decor.seaview_panel(q, rect, look, line, alpha, border, paused)  # noqa: E731
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, decor.cached(self, key, self.size(), draw))
        p.end()

    def _head_box(self) -> tuple[float, float, tuple]:
        """全部展开时标题栏那一行：（这一行的下沿, 标题的右边, 右边几个按钮各占的地方）——霓虹贴纸的网点只铺在这一行里、标题
        右边，并让开按钮。"""
        bottom, right, keep = 0, 0, []
        for name in ("title", "toggle_btn", "close_btn", "quick_add"):
            widget = self.head.get(name)
            if widget is not None and widget.isVisibleTo(self):
                g = widget.geometry()
                bottom = max(bottom, g.bottom() + 1)
                if name == "title":
                    right = g.right() + 1
                else:
                    keep.append((g.x(), g.y(), g.width(), g.height()))
        return float(bottom + 6), float(right), tuple(keep)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if O.PANEL_DECOR in ("board", "stripes"):
            self._paint_game()
            return
        if O.PANEL_DECOR in ("neon", "seaview"):
            self._paint_rgb()
            return
        if O.PANEL_DECOR == "carbon":
            self._paint_carbon()
            return
        if O.PANEL_DECOR != "chrome":
            return
        from .. import decor

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        stripes = self._stripes()
        if stripes != getattr(self, "_drawn_stripes", stripes) and event.rect() != self.rect():
            self.update()  # 画不画斜带变了、这一次只重画了一部分：整块再画一遍，免得半截斜带留在面板上
        self._drawn_stripes = stripes
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, decor.cached(self, ("panel", theme.current_key(), self.property("paused"), stripes),
                                        self.size(),
                                        lambda q: decor.panel(q, rect, theme.CHROME, seed=5, stripes=stripes,
                                                              fill=False, radius=O.PANEL_RADIUS)))
        p.end()

    def _paint_carbon(self) -> None:
        """碳纤流光的面板（PANEL_DECOR 为 carbon），整块自己画（样式表不画底和框，decor.carbon_panel）：切角的碳黑底铺细斜纹、
        细线外框，左边一条竖着的七色灯带，右下角的记号。按不透明画：平时透出下方的游戏画面靠整个窗口的透明度（皮肤的
        WINDOW_ALPHA，Overlay.see_through），「悬浮窗背景不透明」时窗口不透明。识别暂停时外框画成虚线（窗口更透明）；需要用户
        操作时另描一圈警告色的框。一行显示和全部展开画法相同（灯带在左边，摘要的字不压在灯带上）。"""
        from .. import decor

        paused, attention = bool(self.property("paused")), bool(self.property("attention"))
        alpha = 1.0
        border = O.PANEL_ATTENTION_BORDER if attention else None
        rect = QRectF(self.rect())
        key = ("carbon", theme.current_key(), alpha, attention, paused)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, decor.cached(self, key, self.size(),
                                        lambda q: decor.carbon_panel(q, rect, theme.CHROME, alpha, border, paused)))
        p.end()


class IconTextButton(QPushButton):
    """图标在上、字在下的按钮（「筹码方案」卡片的「编辑方案」「导入/导出」「作业站」，跨两行）。底色、框线、悬停和按下的效果照
    样式表画（悬浮窗样式表的 QPushButton 和 #iconText），图标（系统图标字体，settings.draw_glyph）和字自己画：按钮文字可以
    分几行写（text() 里的换行），每行水平居中，图标加几行字整块上下居中；系统里没有图标字体时系统图标只写字，自己画的
    书（ICON_BOOK）照样画。按钮文字就是 text()。并排的几个按钮可以一样宽（same_width：都取其中最宽的一个）。
    图标编号见 SCHEME_EDIT_ICON、SCHEME_SHARE_ICON、SCHEME_HUB_ICON，大小、颜色、内边距见皮肤的 SCHEME_TOOL_*。"""

    def __init__(self, code: int | str, text: str, tip: str = ""):
        super().__init__(text)
        self.code = code
        self.peers: tuple[IconTextButton, ...] = ()  # same_width 的一组（含自己）；为空时宽度只看自己
        self.setObjectName("iconText")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)  # 撑满两行
        if tip:
            _set_tip(self, tip)
        from .. import chrome

        chrome.cut_corners(self)  # 皮肤要切角时（碳纤流光）底画成切角的框；别的皮肤照样式表画

    def lines(self) -> list[str]:
        """按钮上的字按换行分开的几行。"""
        return self.text().split("\n")

    def _icon_height(self) -> int:
        """图标占的高度（含图标和字之间的空隙）；没有图标字体时为 0（自己画的书不看图标字体，照样留出这一行）。"""
        return (O.SCHEME_TOOL_ICON_PX + O.SCHEME_TOOL_GAP
                if self.code == ICON_BOOK or glyph_font(O.SCHEME_TOOL_ICON_PX) is not None else 0)

    @staticmethod
    def same_width(*buttons: IconTextButton) -> None:
        """这几个按钮一样宽：每个都取其中最宽的一个的宽度（natural_width）。换皮肤、字号变了时一起重算。"""
        for button in buttons:
            button.peers = buttons
            button.updateGeometry()

    def natural_width(self) -> int:
        """只按自己的字和图标算的宽度：最宽的一行字或图标，加左右内边距和框线。"""
        self.ensurePolished()  # 字号在样式表里
        pad_x = O.SCHEME_TOOL_PADDING[0]
        icon_w = glyph_width(self.code, O.SCHEME_TOOL_ICON_PX)
        return max(max(self.fontMetrics().horizontalAdvance(line) for line in self.lines()), icon_w) + 2 * pad_x + 2

    def sizeHint(self) -> QSize:
        self.ensurePolished()  # 字号在样式表里
        pad_y = O.SCHEME_TOOL_PADDING[1]
        width = max((b.natural_width() for b in self.peers if _alive(b)), default=self.natural_width())
        return QSize(width, self._icon_height() + self.fontMetrics().lineSpacing() * len(self.lines()) + 2 * pad_y + 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):  # 自己的宽度变了：同一组的按钮也跟着重算
            for peer in self.peers:
                if peer is not self and _alive(peer):
                    peer.updateGeometry()

    def paintEvent(self, event) -> None:
        p = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""  # 底色、框线和悬停效果照样式表画，图标和字在下面画
        option.icon = QIcon()
        p.drawControl(QStyle.CE_PushButton, option)
        fill = QColor(O.SCHEME_TOOL_COLOR if self.isEnabled() else O.SCHEME_TOOL_DISABLED)
        fm = self.fontMetrics()
        icon_h = self._icon_height()
        line_h = fm.lineSpacing()
        top = (self.height() - icon_h - line_h * len(self.lines())) / 2
        if icon_h:
            draw_glyph(p, QRectF(0, top, self.width(), O.SCHEME_TOOL_ICON_PX), self.code, O.SCHEME_TOOL_ICON_PX, fill)
        p.setFont(self.font())
        p.setPen(fill)
        for i, line in enumerate(self.lines()):
            p.drawText(QRectF(0, top + icon_h + i * line_h, self.width(), line_h),
                       Qt.AlignHCenter | Qt.AlignVCenter, line)
        p.end()
