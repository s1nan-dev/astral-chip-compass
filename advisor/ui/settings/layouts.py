"""设置窗口的排法基类 Layout 和七套皮肤用的各排法，连同只有某一种排法用的小部件（TintIcon、RowArrow、GlyphMark、
ChevronBox、CutRowButton、_note_buttons）。

每个排法 build(items, controls, call) →（一页, {项名: RowParts}），项和控件的行为不变；写了 SettingItem.link 的项，说明
正下方另放一行能点的字（Layout._link，和说明左边对齐）。用哪种排法按当前皮肤
（theme.Layouts.SETTINGS，不认识的按 cards）：
  - cards：默认皮肤的单页分组卡片；
  - side_tabs：左侧页签「识别」「外观」「关于」分页（经典主题-白）；
  - top_tabs、band_tabs：页签在顶上一排、在顶上的黑色通栏里（经典主题-黑、经典主题-反色）；
  - group_cards：单页竖排的分组卡片（霓虹贴纸、白色海景房）；
  - grid：两列方块格子（碳纤流光）。
另一套皮肤另写一种排法、在 LAYOUTS 里登记，项和控件的行为不变。界面文字在 dialog.py，行部件在 widgets.py。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import chrome, decor, lang, theme
from ..glyphs import ICON_CHEVRON, ICON_SETTINGS, draw_glyph, glyph_font
from ..text import tip_paragraphs_html
from ..theme import SETTINGS as L
from ..widgets import GlyphBox, TextLabel, color
from .dialog import (CHEVRON_TEXT, DIAG_CARD, GROUP_DIAG, GROUP_LOOK, GROUP_PRIVACY, GROUP_RECOG, NOTES_CARD,
                     NOTE_SECTIONS, NOTE_TIP, PAGE_SUBTITLE, PAGE_TITLE, TAB_ABOUT, TAB_LOOK, TAB_PRIVACY, TAB_RECOG,
                     UPDATE_CARD, SettingItem)
from .widgets import KeyCap, ModeTile, RelaunchLine, RowButton, RowParts, StatusChip, TextLink


# ------------------------------------------------------------ 怎么排


class Layout:
    """设置窗口各排法的公共部分：这一项的标题、说明、另一段说明三个标签，说明下面能点的字；建一叠页面、建页签、接切换；
    把用不上的控件收进页面藏起来。每个排法另有 build(items, controls, call) →（一页, {项名: RowParts}）。"""

    @staticmethod
    def _labels(item: SettingItem, parent: QWidget | None = None) -> tuple[TextLabel, TextLabel, TextLabel]:
        """这一项的标题、说明、另一段说明三个标签：标题一行；说明自动换行、占满这一栏，另一段说明先收起（有内容时由窗口
        显示）。parent 给了时标题挂到它下面（标题写在卡片、分组标题上时用）；说明和另一段说明由调用的地方按原来的先后挂、
        收（隐藏的占位符也要照原样挂上）。"""
        title = TextLabel(item.title, "rowTitle", wrap=False)
        desc = TextLabel(item.desc, "rowDesc")
        desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # 「界面语言」一项下面那一格放重开那一行（提示和「现在重开」按钮），其余项是暗色说明
        note = RelaunchLine() if item.key == "ui_lang" else TextLabel("", "rowNote")
        note.hide()
        if parent is not None:
            title.setParent(parent)
        return title, desc, note

    @staticmethod
    def _link(item: SettingItem, desc: TextLabel) -> TextLink | None:
        """说明下面那一行能点的字（item.link 有字时才有，例如「隐私与数据」一组开关下面的「我的统计」；字号、粗细同说明
        desc），没有时为 None。各排法把它放在说明的正下方、靠左（和说明左边对齐）；点了做什么由窗口接（RowParts.link）。"""
        return TextLink(item.link, desc) if item.link else None

    @staticmethod
    def _add_desc(texts: QVBoxLayout, desc: TextLabel, link: TextLink | None) -> None:
        """说明放进 texts 这一栏；有能点的字时接在说明正下方、靠左。"""
        texts.addWidget(desc)
        if link is not None:
            texts.addWidget(link, 0, Qt.AlignLeft)

    def _stacked(self, stack: QStackedWidget, add_tab, pages, builder) -> None:
        """建一叠页面、建页签、接切换：pages 是（页签名, 键），builder(页签名, 键) 建出这一页的部件（按原来的先后建）。
        self.tabs 记下各页签；建完由调用的地方选中第一个。"""
        self.tabs = {}
        for name, keys in pages:
            body = builder(name, keys)
            stack.addWidget(body)
            tab = add_tab(name)
            index = stack.count() - 1
            tab.toggled.connect(lambda on, i=index: on and stack.setCurrentIndex(i))
            self.tabs[name] = tab

    @staticmethod
    def _hide_unused(page: QWidget, controls: dict) -> None:
        """这种排法用不上的控件（例如整行「关于」右端的箭头）收进 page 里不显示。"""
        for control in controls.values():
            if control.parent() is None:
                control.setParent(page)
                control.hide()


class CardsLayout(Layout):
    """默认皮肤的排法（Windows 风的单页分组卡片）：顶部图标、大标题和副标题；每组一个分组小标题，下面是圆角卡片，卡片里
    每项一行（左边系统图标、中间标题和说明、右边控件），同一张卡片里的几行之间一条分隔线。识别模式的两块方块在它那一行
    下面，整行可点的「关于」右端是箭头；「启动时自动检查更新」在诊断包和「关于」之间，右边是开关和「检查更新」，检查的结果
    代替说明显示；「隐私与数据」一组只有开关一行，说明下面是能点的「我的统计」。窗口里不显示的项（items 里没有的，例如只有
    一套皮肤时的「皮肤」）连同分隔线不排，一项都没有的卡片、分组不排。"""

    CARDS = (
        (GROUP_RECOG, (("platform", "mode"), ("hotkey", "capture", "lowend", "big_cores"))),
        (GROUP_LOOK, (("opaque", "skin", "ui_lang"),)),
        (GROUP_DIAG, (("export", "update", "about"),)),
        (GROUP_PRIVACY, (("report",),)),
    )

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        page = QFrame()
        page.setObjectName("page")
        box = QVBoxLayout(page)
        box.setContentsMargins(*L.PAGE_MARGINS)
        box.setSpacing(L.PAGE_SPACING)
        box.addLayout(self._header())
        rows: dict[str, RowParts] = {}
        for group, cards in self.CARDS:
            cards = [[k for k in keys if k in items] for keys in cards]
            cards = [keys for keys in cards if keys]
            if not cards:
                continue
            box.addWidget(TextLabel(group, "section", wrap=False))
            for keys in cards:
                card = QFrame()
                card.setObjectName("card")
                lines = QVBoxLayout(card)
                lines.setContentsMargins(0, 0, 0, 0)
                lines.setSpacing(0)
                for i, key in enumerate(keys):
                    if i:
                        divider = QFrame()
                        divider.setObjectName("divider")
                        lines.addWidget(divider)
                    rows[key] = self._row(items[key], controls[key])
                    lines.addWidget(rows[key].row)
                box.addWidget(card)
        box.addStretch(1)
        return page, rows

    @staticmethod
    def _header() -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(12)
        icon = GlyphBox(ICON_SETTINGS, head=True)
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(TextLabel(PAGE_TITLE, "pageTitle", wrap=False))
        titles.addWidget(TextLabel(PAGE_SUBTITLE, "subtitle", wrap=False))
        head.addLayout(titles, 1)
        return head

    @staticmethod
    def _row(item: SettingItem, control: QWidget) -> RowParts:
        row = RowButton(item.title) if item.kind == "link" else QWidget()
        outer = QVBoxLayout(row)
        outer.setContentsMargins(*L.ROW_MARGINS)
        outer.setSpacing(10)
        line = QHBoxLayout()
        line.setSpacing(L.ROW_SPACING)
        icon = GlyphBox(item.icon)
        line.addWidget(icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title, desc, note = CardsLayout._labels(item)
        link = CardsLayout._link(item, desc)
        texts.addWidget(title)
        CardsLayout._add_desc(texts, desc, link)
        texts.addWidget(note)
        line.addLayout(texts, 1)
        if item.kind == "mode":  # 两块方块放在这一行下面，和卡片同宽
            outer.addLayout(line)
            outer.addWidget(control)
        else:
            line.addWidget(control, 0, Qt.AlignTop if item.kind == "status" else Qt.AlignVCenter)
            outer.addLayout(line)
        return RowParts(row, title, desc, note, icon, link)


class SideTabsLayout(Layout):
    """游戏内风格的排法（左侧页签分页）：左上角「返回」（皮肤自己画外框时，Layouts.CHROME），左边白色导航栏里「识别」
    「外观」「关于」三个页签，右边深灰面板里是当前页签的几张浅灰卡片（每项一张，卡片左上角的标签写这一项的标题，卡片里
    左边说明、右边控件；识别模式的两块方块并排放在卡片里）；面板下面写版本。「关于」一页是「诊断」（导出诊断）、「启动时
    自动检查更新」（开关和「检查更新」）和「说明」（NOTE_SECTIONS 四个按钮，点了调用悬浮窗的 show_about(那一节的标题)）；
    「隐私」一页是开关那一张卡片，说明下面是能点的「我的统计」。
    窗口里不显示的项（例如只有一套皮肤时的「皮肤」）不排，一项都没有的页签照旧在（「外观」里还有悬浮窗背景不透明）。"""

    PAGES = ((TAB_RECOG, ("platform", "mode", "hotkey", "capture", "lowend", "big_cores")),
             (TAB_LOOK, ("opaque", "skin", "ui_lang")), (TAB_ABOUT, ("export", "update", "about")),
             (TAB_PRIVACY, ("report",)))
    PANEL_MARGINS = (24, 24, 24, 24)
    CARD_SPACING = 18

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        from ..theme import CHROME as K

        call = call or (lambda *args: None)
        page = chrome.Frame()
        page.setObjectName("sidePage")
        root = QHBoxLayout(page)
        root.setContentsMargins(0, 0, 20, 0)
        root.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 22)
        left.setSpacing(14)
        self.back = None
        if chrome.custom():
            back_row = QHBoxLayout()
            back_row.setContentsMargins(K.BACK_POS[0], K.BACK_POS[1], 0, 0)
            self.back = chrome.BackButton()
            back_row.addWidget(self.back, 0, Qt.AlignLeft)
            left.addLayout(back_row)
        else:
            left.addSpacing(72)
        rail = self.rail = chrome.NavRail()
        left.addWidget(rail, 1)
        root.addLayout(left)
        right = QVBoxLayout()
        right.setContentsMargins(0, 18, 0, 6)
        right.setSpacing(6)
        panel = chrome.Panel()
        stack = self.stack = QStackedWidget(panel)
        stack.setObjectName("sidePages")
        holder = QVBoxLayout(panel)
        holder.setContentsMargins(*self.PANEL_MARGINS)
        holder.addWidget(stack)
        right.addWidget(panel, 1)
        version = QHBoxLayout()
        version.setContentsMargins(40, 0, 0, 0)
        version.addWidget(chrome.VersionTag(), 0, Qt.AlignLeft)
        right.addLayout(version)
        root.addLayout(right, 1)

        rows: dict[str, RowParts] = {}

        def builder(name: str, keys) -> QWidget:
            body = QWidget()
            col = QVBoxLayout(body)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(self.CARD_SPACING)
            for key in keys:
                if key == "about":
                    col.addWidget(self._notes(call))
                    continue
                if key not in items:
                    continue
                card, parts = self._card(items[key], controls[key])
                rows[key] = parts
                col.addWidget(card)
            col.addStretch(1)
            return body

        self._stacked(stack, rail.add_tab, self.PAGES, builder)
        rail.box.addStretch(1)
        rail.tabs()[0].setChecked(True)
        self._hide_unused(page, controls)
        return page, rows

    @staticmethod
    def _card(item: SettingItem, control: QWidget):
        title = DIAG_CARD if item.key == "export" else item.title
        card = chrome.Card(title)
        heading, desc, note = SideTabsLayout._labels(item, card)
        heading.hide()  # 标题写在卡片的标签上（这一个只给按名字找标题的地方用，不显示）
        link = None
        if item.kind == "mode":  # 两块方块并排占满卡片（这一项的说明不显示）
            for label in (desc, note):
                label.setParent(card)
                label.hide()
            card.box.addWidget(control)
        else:
            line = QHBoxLayout()
            line.setSpacing(16)
            texts = QVBoxLayout()
            texts.setSpacing(4)
            link = SideTabsLayout._link(item, desc)
            SideTabsLayout._add_desc(texts, desc, link)
            texts.addWidget(note)
            line.addLayout(texts, 1)
            line.addWidget(control, 0, Qt.AlignVCenter)
            card.box.addLayout(line)
        return card, RowParts(card, heading, desc, note, link=link)

    @staticmethod
    def _notes(call) -> QWidget:
        card = chrome.Card(NOTES_CARD)
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(18)
        for i, section in enumerate(NOTE_SECTIONS):
            label = _section_label(section)
            btn = chrome.PillButton(label, height=56, px=18, seed=10 + i, min_width=230)
            btn.setObjectName("noteButton")
            btn.setToolTip(tip_paragraphs_html(NOTE_TIP.format(section=label)))
            btn.clicked.connect(lambda _=False, s=section: call("show_about", s))
            grid.addWidget(btn, i // 2, i % 2)
        card.box.addLayout(grid)
        return card


def _section_label(section: str) -> str:
    """「说明」卡片的按钮上写的节名：界面是繁体时转成繁体；点了照旧传简体的节名（show_about 按简体节名找那一节）。"""
    return lang.to_hant(section) if lang.is_hant() else section


def _note_buttons(call, kinds, height: int, px: int):
    """「说明」里的四个按钮（chrome.GameButton，种类按 kinds 交替）：两列网格，点了调用悬浮窗的 show_about(那一节的标题)。"""
    grid = QGridLayout()
    grid.setHorizontalSpacing(20)
    grid.setVerticalSpacing(14)
    for i, section in enumerate(NOTE_SECTIONS):
        label = _section_label(section)
        btn = chrome.GameButton(label, kinds[i % len(kinds)], height=height, px=px)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setObjectName("noteButton")
        btn.setToolTip(tip_paragraphs_html(NOTE_TIP.format(section=label)))
        btn.clicked.connect(lambda _=False, s=section: call("show_about", s))
        grid.addWidget(btn, i // 2, i % 2)
    return grid


class TopTabsLayout(Layout):
    """顶上一排页签的排法（经典主题-黑，照游戏「卡牌商店」面板）：窗口顶上一行（chrome.WindowHead：正中的标题页签「设置」，
    右上角 ×），下面左边一排页签「识别」「外观」「关于」、右边暗色的「改动即时生效」；页签下面是当前这一页：每项一行（左边
    系统图标的方块、标题和说明，右边控件），行与行之间一条虚线；识别模式是分组标题（两颗小菱形、横线，右边写这一项的说明）
    下面并排两块方块。「关于」一页是「诊断」「更新」「说明」三个分组（说明里四个按钮打开「关于」窗口对应的一节）和最下面的
    版本。窗口里不显示的项（例如只有一套皮肤时的「皮肤」）不排。"""

    PAGES = SideTabsLayout.PAGES
    MARGINS = (28, 2, 28, 18)

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        from ..widgets import Separator

        call = call or (lambda *args: None)
        page = chrome.Frame()
        page.setObjectName("topTabsPage")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(PAGE_TITLE)
        self.back = head.closer
        root.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(*self.MARGINS)
        body.setSpacing(16)
        line = QHBoxLayout()
        bar = self.bar = chrome.TabBar(spacing=10)
        line.addWidget(bar, 0, Qt.AlignLeft | Qt.AlignVCenter)
        line.addStretch(1)
        line.addWidget(TextLabel(PAGE_SUBTITLE, "subtitle", wrap=False), 0, Qt.AlignVCenter)
        body.addLayout(line)
        stack = self.stack = QStackedWidget()
        stack.setObjectName("sidePages")
        body.addWidget(stack, 1)
        root.addLayout(body, 1)

        rows: dict[str, RowParts] = {}

        def builder(name: str, keys) -> QWidget:
            sheet = QWidget()
            col = QVBoxLayout(sheet)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(12)
            previous = None
            for key in keys:
                if key == "about":
                    col.addSpacing(6)
                    col.addWidget(chrome.SectionTitle(NOTES_CARD))
                    col.addLayout(_note_buttons(call, ("note_a", "note_b", "note_b", "note_a"), 48, 16))
                    continue
                if key not in items:
                    continue
                item = items[key]
                if key == "update":  # 自己一个分组：分组标题代替分隔线
                    col.addWidget(chrome.SectionTitle(UPDATE_CARD))
                elif previous is not None and previous != "mode":
                    col.addWidget(Separator(10))
                row, parts = self._mode(item, controls[key]) if item.kind == "mode" else self._row(item, controls[key])
                if key == "export":
                    col.addWidget(chrome.SectionTitle(DIAG_CARD))
                rows[key] = parts
                col.addWidget(row)
                previous = key
            col.addStretch(1)
            if name == TAB_ABOUT:
                col.addWidget(chrome.VersionTag(px=15), 0, Qt.AlignLeft)
            return sheet

        self._stacked(stack, bar.add_tab, self.PAGES, builder)
        bar.tabs()[0].setChecked(True)
        self._hide_unused(page, controls)
        return page, rows

    @staticmethod
    def _mode(item: SettingItem, control: QWidget):
        """识别模式：分组标题（右边写这一项的说明），下面并排两块方块。"""
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 4)
        col.setSpacing(10)
        col.addWidget(chrome.SectionTitle(item.title, right=item.desc))
        col.addWidget(control)
        title, desc, note = TopTabsLayout._labels(item, box)
        for label in (desc, note):
            label.setParent(box)
            label.hide()
        title.hide()
        return box, RowParts(box, title, desc, note)

    @staticmethod
    def _row(item: SettingItem, control: QWidget):
        """一行：左边系统图标的方块、标题和说明（下面另有能点的字、一段暗色说明时接在说明下面），右边控件。"""
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 2, 0, 2)
        line.setSpacing(14)
        icon = GlyphBox(item.icon)
        line.addWidget(icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        title, desc, note = TopTabsLayout._labels(item)
        link = TopTabsLayout._link(item, desc)
        texts.addWidget(title)
        TopTabsLayout._add_desc(texts, desc, link)
        texts.addWidget(note)
        line.addLayout(texts, 1)
        if item.key == "export":
            control.setObjectName("exportButton")
        line.addWidget(control, 0, Qt.AlignTop if item.kind == "status" else Qt.AlignVCenter)
        return row, RowParts(row, title, desc, note, icon, link)


class BandTabsLayout(Layout):
    """页签在顶上黑色通栏里的排法（经典主题-反色，照游戏结算界面）：通栏左边写标题「设置」，接着几个平行四边形的页签，
    右上角 ×；下面是当前这一页的几张白卡片（卡片左上角压黑色斜角标签，写这一项的标题；识别热键、识别画面两张并排，
    说明在上、控件在下），最下面一条洋红横幅写「改动即时生效」。「关于」一页是「诊断」「启动时自动检查更新」「说明」三张卡片
    和版本。窗口里不显示的项不排。"""

    PAGES = SideTabsLayout.PAGES
    MARGINS = (24, 26, 24, 16)

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        call = call or (lambda *args: None)
        page = chrome.Frame()
        page.setObjectName("bandTabsPage")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(PAGE_TITLE)
        self.back = head.closer
        bar = self.bar = chrome.TabBar(spacing=6)
        head.add(bar)
        root.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(*self.MARGINS)
        body.setSpacing(16)
        stack = self.stack = QStackedWidget()
        stack.setObjectName("sidePages")
        body.addWidget(stack, 1)
        body.addWidget(chrome.Banner(PAGE_SUBTITLE))
        root.addLayout(body, 1)

        rows: dict[str, RowParts] = {}

        def builder(name: str, keys) -> QWidget:
            sheet = QWidget()
            col = QVBoxLayout(sheet)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(22)
            pair = None  # 识别热键、识别画面两张并排
            for key in keys:
                if key == "about":
                    card = chrome.Card(NOTES_CARD, margins=(16, 8, 16, 14))
                    card.box.addLayout(_note_buttons(call, ("note_a", "note_b", "note_b", "note_a"), 48, 16))
                    col.addWidget(card)
                    continue
                if key not in items:
                    continue
                card, parts = self._card(items[key], controls[key])
                rows[key] = parts
                if key in ("hotkey", "capture"):
                    if pair is None:
                        pair = QHBoxLayout()
                        pair.setSpacing(18)
                        col.addLayout(pair)
                    pair.addWidget(card, 1)
                else:
                    col.addWidget(card)
            if name == TAB_ABOUT:
                col.addWidget(chrome.VersionTag(px=15), 0, Qt.AlignLeft)
            col.addStretch(1)
            return sheet

        self._stacked(stack, bar.add_tab, self.PAGES, builder)
        bar.tabs()[0].setChecked(True)
        self._hide_unused(page, controls)
        return page, rows

    @staticmethod
    def _card(item: SettingItem, control: QWidget):
        """一张白卡片：标签写这一项的标题（导出诊断写「诊断」）；识别模式卡片里右上写这一项的说明、下面并排两块方块；
        识别热键、识别画面说明和控件上下排；其余说明在左（下面另有能点的字时接在说明下面）、控件在右。"""
        card = chrome.Card(DIAG_CARD if item.key == "export" else item.title, margins=(16, 12, 16, 14))
        title, desc, note = BandTabsLayout._labels(item, card)
        title.hide()
        link = None
        if item.kind == "mode":
            hint = TextLabel(item.desc, "rowDesc", wrap=False)
            card.box.addWidget(hint, 0, Qt.AlignRight)
            card.box.addWidget(control)
            for label in (desc, note):
                label.setParent(card)
                label.hide()
        elif item.kind == "status":  # 识别画面：上面是截取方式的标签，下面说明
            card.box.addWidget(control, 0, Qt.AlignLeft)
            card.box.addWidget(desc)
            card.box.addWidget(note)
            card.box.addStretch(1)
        elif item.kind == "hotkey":  # 识别热键：上面说明，下面键帽和「更改」
            card.box.addWidget(desc)
            card.box.addWidget(note)
            card.box.addStretch(1)
            card.box.addWidget(control, 0, Qt.AlignLeft)
        else:
            line = QHBoxLayout()
            line.setSpacing(16)
            texts = QVBoxLayout()
            texts.setSpacing(4)
            link = BandTabsLayout._link(item, desc)
            BandTabsLayout._add_desc(texts, desc, link)
            texts.addWidget(note)
            line.addLayout(texts, 1)
            if item.key == "export":
                control.setObjectName("exportButton")
            line.addWidget(control, 0, Qt.AlignVCenter)
            card.box.addLayout(line)
        return card, RowParts(card, title, desc, note, link=link)


class TintIcon(QWidget):
    """霓虹贴纸、白色海景房每一项左边的图标：颜色按项（SettingsLook.ICON_TINTS），形状按 ChromeLook.STYLE——sticker 彩色
    圆标、粗黑描边、右下实心投影；soft 浅色圆角方块里彩色的图标。系统里没有图标字体时不显示（不占地方）。"""

    def __init__(self, item: SettingItem):
        super().__init__()
        self.code, self.key = item.icon, item.key
        extra = 3 if theme.CHROME.STYLE == "sticker" else 0  # 实心投影
        self.setFixedSize(L.ICON_BOX + extra, L.ICON_BOX + extra)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if glyph_font(L.ICON_PX) is None:
            self.hide()

    def paintEvent(self, event) -> None:
        k = theme.CHROME
        fill, glyph = L.ICON_TINTS.get(self.key) or (L.ICON_BG, L.ICON_COLOR)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(1, 1, L.ICON_BOX - 2, L.ICON_BOX - 2)
        if k.STYLE == "sticker":
            decor.sticker(p, decor.capsule(box), fill, k, 2.2, (3, 3), box)
        else:
            p.fillPath(decor.rounded(box, L.ICON_RADIUS), decor.qc(fill))
        draw_glyph(p, box, self.code, L.ICON_PX, decor.qc(glyph))
        p.end()


class RowArrow(QWidget):
    """整行可点的一行（「关于拾星助手」）右端的箭头：霓虹贴纸是描黑边的白色折线，白色海景房是浅灰圆钮里的折线
    （ChromeLook.STYLE、SURFACE）。"""

    SIZE = 26

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        k = theme.CHROME
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        pts = QPolygonF([QPointF(c.x() - 2.5, c.y() - 5.5), QPointF(c.x() + 3, c.y()), QPointF(c.x() - 2.5, c.y() + 5.5)])
        if k.STYLE == "sticker":
            p.setPen(QPen(decor.qc(k.INK), 6.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline(pts)
            p.setPen(QPen(QColor("#ffffff"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(decor.qc(k.SURFACE))
            p.drawEllipse(c, self.SIZE / 2 - 1, self.SIZE / 2 - 1)
            p.setPen(QPen(color(L.DIM_COLOR), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(pts)
        p.end()


class GroupCardsLayout(Layout):
    """霓虹贴纸、白色海景房的排法（单页竖排的分组卡片，分组和每组的项同默认的 cards）：窗口顶上一行写标题「设置」和
    「改动即时生效」、右上角 ×（chrome.WindowHead）；下面每组一个组标题和卡片（chrome.Card，样子按皮肤：霓虹贴纸是压在
    卡片上面的黑色斜带、粗黑描边的贴纸卡片，「识别」一组照默认分成两张；白色海景房每组一张白卡片，组标题是卡片里的小灯点
    加字）。卡片里每项一行（左边彩色的图标 TintIcon、标题和说明、右边控件），行与行之间一条分隔线；识别模式的两块方块在它
    那一行下面；「关于拾星助手」整行可点（RowButton），右端一个箭头。窗口里不显示的项（例如只有一套皮肤时的「皮肤」）不排，
    一项都没有的卡片、分组不排。"""

    GROUP_KEYS = {GROUP_RECOG: "recog", GROUP_LOOK: "look", GROUP_DIAG: "diag", GROUP_PRIVACY: "privacy"}
    MARGINS = (18, 0, 20, 20)

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        from ..widgets import Separator

        page = chrome.Frame()
        page.setObjectName("groupCardsPage")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(PAGE_TITLE)
        head.add(TextLabel(PAGE_SUBTITLE, "pageSubtitle", wrap=False))
        self.back = head.closer
        root.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(*self.MARGINS)
        body.setSpacing(12)
        merge = theme.CHROME.TAG_KIND == "led"  # 组标题写在卡片里面：每组一张卡片
        rows: dict[str, RowParts] = {}
        for group, cards in CardsLayout.CARDS:
            cards = [[k for k in keys if k in items] for keys in cards]
            cards = [keys for keys in cards if keys]
            if not cards:
                continue
            if merge:
                cards = [[k for keys in cards for k in keys]]
            for i, keys in enumerate(cards):
                card = chrome.Card(group if i == 0 else "", key=self.GROUP_KEYS.get(group, ""), margins=(12, 8, 12, 10))
                card.box.setSpacing(0)
                for j, key in enumerate(keys):
                    if j:
                        card.box.addWidget(Separator(12))
                    rows[key] = self._row(items[key], controls[key])
                    card.box.addWidget(rows[key].row)
                body.addWidget(card)
        body.addStretch(1)
        root.addLayout(body, 1)
        self._hide_unused(page, controls)
        return page, rows

    @staticmethod
    def _row(item: SettingItem, control: QWidget) -> RowParts:
        row = RowButton(item.title) if item.kind == "link" else QWidget()
        outer = QVBoxLayout(row)
        outer.setContentsMargins(2, 6, 2, 6)
        outer.setSpacing(10)
        line = QHBoxLayout()
        line.setSpacing(12)
        icon = TintIcon(item)
        line.addWidget(icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title, desc, note = GroupCardsLayout._labels(item)
        link = GroupCardsLayout._link(item, desc)
        texts.addWidget(title)
        GroupCardsLayout._add_desc(texts, desc, link)
        texts.addWidget(note)
        line.addLayout(texts, 1)
        if item.kind == "mode":  # 两块方块放在这一行下面，和卡片同宽
            outer.addLayout(line)
            outer.addWidget(control)
        elif item.kind == "link":
            line.addWidget(RowArrow(), 0, Qt.AlignVCenter)
            outer.addLayout(line)
        else:
            if item.key == "export":
                control.setObjectName("exportButton")
            line.addWidget(control, 0, Qt.AlignTop if item.kind == "status" else Qt.AlignVCenter)
            outer.addLayout(line)
        return RowParts(row, title, desc, note, icon, link)


# 排法：名字 → 排法（有 build(items, controls, call) →（一页, {项名: RowParts}）的类；call(悬浮窗的方法名, 参数…) 供排法里
# 另加的按钮用）。默认皮肤用 cards；游戏内风格用 side_tabs（经典主题-白）、top_tabs（经典主题-黑）、band_tabs（经典主题-反色）；
# 霓虹贴纸、白色海景房用 group_cards；碳纤流光用 grid（GridLayout，下面另一段登记）。以后的皮肤在这里加，再在
# theme.Layouts.SETTINGS 里写它的名字
LAYOUTS = {"cards": CardsLayout, "side_tabs": SideTabsLayout, "top_tabs": TopTabsLayout, "band_tabs": BandTabsLayout,
           "group_cards": GroupCardsLayout}


# ------------------------------------------------------------ 碳纤流光的排法：两列方块格子（grid）


class GlyphMark(QWidget):
    """标题前的一个系统图标（不画底，颜色 tint）。系统里没有图标字体时不显示（不占地方）。"""

    SIZE = 20

    def __init__(self, code: int, tint: str):
        super().__init__()
        self.code, self.tint = code, tint
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if glyph_font(L.ICON_PX) is None:
            self.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        draw_glyph(p, QRectF(self.rect()), self.code, L.ICON_PX, self.tint)
        p.end()


class ChevronBox(QWidget):
    """「关于拾星助手」那一块右下角的箭头钮（碳纤流光）：切角的小方块（ChromeLook 的 CARD_EDGE 细框）里一个向右的箭头
    （ChromeLook.HALO；没有图标字体时写 CHEVRON_TEXT）。只是记号，点的是整块。"""

    SIZE = 30

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        k = theme.CHROME
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = decor.chamfer(box, 6)
        p.fillPath(path, color(L.TILE_BG))
        p.strokePath(path, QPen(decor.qc(k.CARD_EDGE), 1))
        if not draw_glyph(p, box, ICON_CHEVRON, L.CHEVRON_PX, decor.qc(k.HALO or L.SELECTED_COLOR)):
            p.setPen(decor.qc(k.HALO or L.SELECTED_COLOR))
            p.drawText(box, Qt.AlignCenter, CHEVRON_TEXT)
        p.end()


class CutRowButton(QAbstractButton):
    """整块可点的切角面板（碳纤流光的「关于拾星助手」）：同 chrome.CutCard 的样子（标题那一行左边一根 accent 颜色的竖条），
    鼠标移上去框线换成 ChromeLook.HALO。里面的标题、说明、箭头由排法放（box）。"""

    def __init__(self, text: str, accent: str = ""):
        super().__init__()
        self.setText(text)
        self.accent = accent
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 12, 16, 14)
        self.box.setSpacing(8)

    def sizeHint(self) -> QSize:
        return self.layout().totalSizeHint()

    def paintEvent(self, event) -> None:
        k = theme.CHROME
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        decor.cut_card(p, QRectF(self.rect()), k)
        if self.accent:
            p.fillRect(QRectF(0, 13, 3, 20), decor.qc(self.accent))
        if self.underMouse() or self.isDown() or self.hasFocus():
            p.strokePath(decor.chamfer(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), k.CHAMFER),
                         QPen(decor.qc(k.HALO or L.SELECTED_COLOR), 1.2))
        p.end()


class GridLayout(Layout):
    """碳纤流光的排法（两列方块格子，一屏放下全部设置）：窗口顶上一行（四色小方块、斜体粗体的「设置」，后面暗色的「改动即时
    生效」，右上角 ×，chrome.WindowHead）；下面每组一个编号的分组标题（「01 识别 ──」，chrome.IndexHeading），组里每项一块
    切角的面板（chrome.CutCard；标题那一行左边一根这一项颜色的竖条，SettingsLook.ITEM_BARS），游戏平台、识别模式、低配模式、
    皮肤、关于拾星助手、帮助拾星助手改善推荐准确度各占一整行，识别热键和识别画面、悬浮窗背景不透明和界面语言、诊断包和启动时
    自动检查更新两两并排（一屏放下全部设置）。每块：标题行（图标、
    斜体粗体的标题；识别模式、皮肤的说明接在标题后面），下面说明（检查更新的结果代替说明显示；说明下面能点的字，例如「我的
    统计」，接在说明正下方），控件在左下（键帽和「更改」、截取方式、导出诊断、自动检查的开关和「检查更新」）或右边（皮肤的
    「当前皮肤＋更换」一行、开关）；「关于拾星助手」整块可点
    （CutRowButton，右下角一个箭头钮），点了打开关于窗口。按钮、键帽、状态标签画成切角的框（chrome.cut_corners）。窗口里
    不显示的项（只有一套皮肤时的「皮肤」）不排，一项都没有的组不排。"""

    GROUPS = ((GROUP_RECOG, (("platform",), ("mode",), ("hotkey", "capture"), ("lowend",), ("big_cores",))),
              (GROUP_LOOK, (("opaque", "ui_lang"), ("skin",))),
              (GROUP_DIAG, (("export", "update"), ("about",))),
              (GROUP_PRIVACY, (("report",),)))
    MARGINS = (20, 0, 20, 0)
    INLINE = ("mode", "skin")  # 说明接在标题后面的项

    def build(self, items: dict[str, SettingItem], controls: dict[str, QWidget],
              call=None) -> tuple[QFrame, dict[str, RowParts]]:
        page = chrome.Frame()
        page.setObjectName("gridPage")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(PAGE_TITLE)
        head.add(chrome.hint_label(PAGE_SUBTITLE))
        self.back = head.closer
        root.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(*self.MARGINS)
        body.setSpacing(2)
        rows: dict[str, RowParts] = {}
        number = 0
        for group, lines in self.GROUPS:
            lines = [[k for k in keys if k in items] for keys in lines]
            lines = [keys for keys in lines if keys]
            if not lines:
                continue
            number += 1
            body.addWidget(chrome.IndexHeading(f"{number:02d}", group))
            for keys in lines:
                line = QHBoxLayout()
                line.setSpacing(12)
                for key in keys:
                    card, parts = self._card(items[key], controls[key])
                    rows[key] = parts
                    line.addWidget(card, 1)
                body.addLayout(line)
        body.addStretch(1)
        root.addLayout(body, 1)
        self._hide_unused(page, controls)
        self._cut(page, controls)
        return page, rows

    @staticmethod
    def _title_row(title: TextLabel, item: SettingItem, tint: str, inline: str = "") -> QHBoxLayout:
        """标题行：图标（这一项的颜色，GlyphMark：是标题行的第一个部件）、标题，inline 有字时后面接一句暗色说明。"""
        line = QHBoxLayout()
        line.setSpacing(10)
        line.addWidget(GlyphMark(item.icon, tint), 0, Qt.AlignVCenter)
        line.addWidget(title, 0, Qt.AlignVCenter)
        if inline:
            line.addSpacing(4)
            line.addWidget(TextLabel(inline, "rowDesc", wrap=False), 0, Qt.AlignVCenter)
        line.addStretch(1)
        return line

    def _card(self, item: SettingItem, control: QWidget):
        tint = L.ITEM_BARS.get(item.key, L.DIM_COLOR)
        if item.kind == "link":  # 「关于拾星助手」：整块可点，右下角一个箭头钮
            card = CutRowButton(item.title, tint)
            card.box.setContentsMargins(16, 10, 16, 12)
        else:
            card = chrome.CutCard(tint, margins=(16, 10, 16, 12))
        box = card.box
        title, desc, note = self._labels(item)
        if item.key in self.INLINE:
            line = self._title_row(title, item, tint, item.desc)
            desc = line.itemAt(line.count() - 2).widget()  # 接在标题后面的那一句说明
            if item.kind == "skin":  # 「当前皮肤＋更换」在这一行的右边
                line.addWidget(control, 0, Qt.AlignVCenter)
            box.addLayout(line)
            if item.kind == "mode":  # 两块方块并排放在下面
                box.addWidget(control)
                self._tint_tiles(control)
            note.setParent(card)
            return card, RowParts(card, title, desc, note)
        line = self._title_row(title, item, tint)
        icon = line.itemAt(0).widget()  # 标题行最前面的图标（GlyphMark）
        box.addLayout(line)
        link = self._link(item, desc)
        if item.kind in ("switch", "choice"):  # 说明在左、开关（下拉框）在右
            row = QHBoxLayout()
            row.setSpacing(16)
            if link is None:
                row.addWidget(desc, 1)
            else:  # 说明和下面能点的字一起在左边一栏，开关对着这一栏的正中
                texts = QVBoxLayout()
                texts.setSpacing(2)
                self._add_desc(texts, desc, link)
                row.addLayout(texts, 1)
            row.addWidget(control, 0, Qt.AlignVCenter)
            box.addLayout(row)
            box.addWidget(note)
            box.addStretch(1)  # 并排的那一块更高时（界面语言下面多出重开那一行）内容照样靠上
            return card, RowParts(card, title, desc, note, icon, link)
        self._add_desc(box, desc, link)
        box.addWidget(note)
        box.addStretch(1)
        if item.kind == "link":
            box.addWidget(ChevronBox(), 0, Qt.AlignRight)
        else:
            if item.kind == "status":  # 截取方式的标签和「（尚未找到游戏窗口）」靠左
                lay = control.layout()
                for i in range(lay.count()):
                    widget = lay.itemAt(i).widget()
                    if widget is not None:
                        lay.setAlignment(widget, Qt.AlignLeft)
            if item.key == "export":
                control.setObjectName("exportButton")
            box.addWidget(control, 0, Qt.AlignLeft)
        return card, RowParts(card, title, desc, note, icon, link)

    @staticmethod
    def _tint_tiles(control: QWidget) -> None:
        """识别模式的两块方块：选中的那块标题换成选中色（随选中变）。"""
        for tile in control.findChildren(ModeTile):
            def tint(on: bool, label=tile.title) -> None:
                label.setStyleSheet(f"QLabel {{ color: {L.SELECTED_COLOR}; }}" if on else "")
            tile.toggled.connect(tint)
            tint(tile.isChecked())

    @staticmethod
    def _cut(page: QWidget, controls: dict[str, QWidget]) -> None:
        """按钮、键帽、状态标签画成切角的框（chrome.cut_corners）：「导出诊断」是主按钮；键帽的数字用数字字体的窄体。"""
        for button in page.findChildren(QPushButton):
            chrome.cut_corners(button, "primary" if button.objectName() == "exportButton" else "plain")
        for cap in page.findChildren(KeyCap):
            chrome.cut_corners(cap, "keycap")
            font = QFont(cap.font())
            font.setStretch(QFont.Condensed)
            cap.setFont(font)
        for chip in page.findChildren(StatusChip):
            chrome.cut_corners(chip, "chip")


LAYOUTS["grid"] = GridLayout  # 碳纤流光
