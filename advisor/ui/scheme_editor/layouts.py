"""「编辑筹码方案」窗口七种排法的摆放：每种排法一个函数，第一个参数是编辑窗口，有的另带这一栏用的部件套件 _Kit；
输入框、按钮这些部件一律经窗口的 _make_* 照套件造、接好信号；这里只建排法自己的骨架（布局、卡片、外框、面板、
页签条和游戏内风格的方案列表），把部件放进去。

排法名 → 建法 的 LAYOUTS 表也在本模块（值是 window.SchemeEditor 上的方法名，window.py 把这里的函数绑成同名方法），
最后一个排法（tab_strip）在模块末尾登记。每种排法里建、摆部件的先后决定 Tab 顺序，改动会影响外观。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..widgets import FlowLayout
from .. import chrome
from .notes import (
    AUTHOR_LABEL,
    DEFAULT_READ_ONLY,
    DEFAULT_ROW,
    EDITOR_TITLE,
    HERO_HINT,
    HERO_LABEL,
    LIST_WIDTH,
    NAME_LABEL,
    NO_PLAYER_TIERS,
    NOTE_LABEL,
    READ_ONLY_NOTICE,
)
from ..scheme_cards import (
    _SchemeCardDelegate,
    _SchemeListDelegate,
    _SchemeStripDelegate,
    _SchemeStickerDelegate,
    _SchemeTabDelegate,
    _SchemeTileDelegate,
    _SchemeWellDelegate,
)
from .widgets import (
    GAME_KIT,
    PILL_KIT,
    PLAIN_KIT,
    TargetSchools,
    _FlowTargets,
    _Kit,
    _LinkedBox,
    _plain_label,
    _row,
    _WrapLabel,
    _WrapWarn,
)

# 怎么排：排法的名字 → 搭界面的方法名（theme.Layouts.EDITOR，不认识的按 default）。default：左边方案列表，右边编辑区和
# 筹码列表；side_tabs：游戏内风格，左边白色导航栏里角色和方案页签（方案列表画成页签），右边深灰面板里放编辑区和筹码列表；
# card_row：方案像卡牌横排一行，下面左边一栏表单、右边筹码列表（经典主题-黑）；card_column：左边一列方案卡，右边一张大卡片
# 放表单和筹码列表（经典主题-反色）；side_column：左边一条侧栏放角色、方案列表、新建复制删除、名称作者说明，右边整列放目标
# 流派、搜索和筹码列表（霓虹贴纸）；tile_column：左边一张张方案卡、下面新建复制删除，右边表单和筹码列表（白色海景房）
LAYOUTS = {"default": "_build", "side_tabs": "_build_tabs", "card_row": "_build_card_row",
           "card_column": "_build_card_column", "side_column": "_build_side_column",
           "tile_column": "_build_tile_column"}


# ---------------------------------------------------------------- 搭界面

def build(ed, kit=PLAIN_KIT) -> None:
    box = QVBoxLayout(ed)
    box.setContentsMargins(14, 12, 14, 12)
    box.setSpacing(8)

    hero_row, lay = _row(8)
    lay.addWidget(_plain_label(HERO_LABEL, "field"))
    ed.hero_box = ed._make_hero_box()
    lay.addWidget(ed.hero_box)
    ed.hero_hint = _plain_label(HERO_HINT, "dim")
    lay.addWidget(ed.hero_hint)
    lay.addStretch(1)
    box.addWidget(hero_row)

    ed.read_only_label = _plain_label(READ_ONLY_NOTICE, "warn")
    ed.read_only_label.setWordWrap(True)
    box.addWidget(ed.read_only_label)

    middle = QHBoxLayout()
    middle.setSpacing(12)
    ed.scheme_list = QListWidget()
    ed.scheme_list.setObjectName("schemeList")
    ed.scheme_list.setFixedWidth(LIST_WIDTH)
    ed.scheme_list.setItemDelegate(_SchemeListDelegate(ed.scheme_list))
    ed.scheme_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ed.scheme_list.currentItemChanged.connect(ed._list_changed)
    middle.addWidget(ed.scheme_list)
    right = QVBoxLayout()
    right.setSpacing(6)
    build_mine(ed, kit, right)
    build_default(ed, right)
    build_chip_list(ed, kit, right)
    middle.addLayout(right, 1)
    box.addLayout(middle, 1)

    bottom, lay = _row(8)
    ed._make_actions(kit)
    ed.action_row, actions = _row(8)
    for w in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn):
        actions.addWidget(w)
    ed.action_row.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    ed._make_confirm(kit)
    ed.confirm_row, confirm = _row(8)
    for w in (ed.confirm_label, ed.confirm_yes, ed.confirm_no):
        confirm.addWidget(w)
    ed.confirm_row.hide()
    lay.addWidget(ed.action_row)
    lay.addWidget(ed.confirm_row)
    lay.addStretch(1)
    ed._make_saved_and_close(bottom, lay, kit)
    box.addWidget(bottom)


def build_mine(ed, kit, right: QVBoxLayout) -> None:
    """我的方案的编辑区：名称、作者、目标流派（只显示）和它的说明、说明；下面是搜索行和对不上的筹码。"""
    ed.mine_box = QWidget()
    col = QVBoxLayout(ed.mine_box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)
    ed._make_fields(kit)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    ed.name_edit.setFixedWidth(220)
    lay.addWidget(ed.name_edit)
    lay.addWidget(ed.name_hint)
    lay.addStretch(1)
    col.addWidget(row)
    col.addWidget(ed.copied_label)
    row, lay = _row(8)
    lay.addWidget(_plain_label(AUTHOR_LABEL, "field"))
    ed.author_edit.setFixedWidth(260)
    lay.addWidget(ed.author_edit)
    lay.addStretch(1)
    col.addWidget(row)
    ed.targets = TargetSchools()
    col.addWidget(ed.targets)
    col.addWidget(ed.target_note)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NOTE_LABEL, "field"))
    lay.addWidget(ed.note_edit, 1)
    col.addWidget(row)
    right.addWidget(ed.mine_box)

    right.addWidget(ed._make_search(kit))
    right.addWidget(ed._make_stale(kit))


def build_default(ed, right: QVBoxLayout, moved: bool = False) -> None:
    """选中默认推荐时代替编辑区：名称「默认推荐」、只读的说明、推荐流派，没有固定档位时的一句说明。moved：窄栏里
    「默认推荐，不能修改……」一行放不下时，挪到名称下面一行、自动换行（card_row、side_column）。"""
    ed.default_box = QWidget()
    col = QVBoxLayout(ed.default_box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(6)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    lay.addWidget(_plain_label(DEFAULT_ROW, "defaultName"))
    ed.default_read_only = _plain_label(DEFAULT_READ_ONLY, "dim")
    if not moved:
        lay.addWidget(ed.default_read_only)
    lay.addStretch(1)
    col.addWidget(row)
    ed.default_schools = _plain_label("", "")
    col.addWidget(ed.default_schools)
    ed.no_tiers_label = _plain_label(NO_PLAYER_TIERS, "dim")
    ed.no_tiers_label.setWordWrap(True)
    col.addWidget(ed.no_tiers_label)
    if moved:  # 折行的说明插在名称下面（栏里放不下一行）：插进去的排在子部件最后，同先建好再挪位置
        ed.default_read_only.setWordWrap(True)
        col.insertWidget(1, ed.default_read_only)
    right.addWidget(ed.default_box)


def build_chip_list(ed, kit, right: QVBoxLayout) -> None:
    """筹码列表上方的说明、筹码列表（只有它上下滚动）、列表下面的橙色软锁提示和灰色规则提示（HintBox）。软锁的问话
    （LockQuestion）问的时候由列表放在那一行下面，平时不在列表里（这里留着同一个，反复用）。"""
    ed._make_list_area(kit)
    right.addWidget(ed.list_note)
    right.addWidget(ed.chip_list, 1)
    right.addWidget(ed.lock_label)
    right.addWidget(ed.hints_box)


def build_tabs(ed, kit=PILL_KIT) -> None:
    """游戏内风格的排法（side_tabs）：左上角「返回」（皮肤自己画外框时），左边白色导航栏里从上往下是角色下拉框和它的
    说明、方案页签（方案列表画成页签，_SchemeTabDelegate）、新建 / 复制 / 删除；右边深灰面板里是编辑区卡片（名称、作者、
    说明、目标流派，标题写成橙色小标签）、搜索行、筹码列表卡片（列表上方的说明在卡片里）、软锁提示（黑底黄字、「!」
    圆标）和规则提示，最下面一行左边是删除的确认、右边「已保存」。部件的名字和默认的排法相同（别的方法按这些名字找部件）。
    一个窗口只留一个关闭按钮：自己画外框时「返回」就是关闭（close_btn 就是它），不另放「关闭」；用系统标题栏时右下角照旧
    有「关闭」。面板上直接写的说明（规则提示、对不上的筹码、删除的确认）都垫底板，导航栏上的说明垫导航栏的底色：斜带、
    几何小图案、竖条不压在字下面。"""
    from ..theme import CHROME as K

    outer = QVBoxLayout(ed)
    outer.setContentsMargins(0, 0, 0, 0)
    frame = ed.frame = chrome.Frame()
    outer.addWidget(frame)
    root = QHBoxLayout(frame)
    root.setContentsMargins(0, 0, 18, 18)
    root.setSpacing(0)

    # ---- 左：「返回」、导航栏
    left = QVBoxLayout()
    left.setContentsMargins(0, 0, 0, 4)
    left.setSpacing(14)
    if chrome.custom():
        back_row = QHBoxLayout()
        back_row.setContentsMargins(K.BACK_POS[0], K.BACK_POS[1], 0, 0)
        ed.back = chrome.BackButton()
        back_row.addWidget(ed.back, 0, Qt.AlignLeft)
        left.addLayout(back_row)
    else:
        left.addSpacing(72)
    rail = ed.rail = chrome.NavRail()
    rail.box.setContentsMargins(0, 14, 0, 18)
    rail.box.setSpacing(8)
    pad = QVBoxLayout()
    pad.setContentsMargins(14, 0, 30, 0)
    pad.setSpacing(6)
    pad.addWidget(_plain_label(HERO_LABEL, "railTitle"))
    ed.hero_box = ed._make_hero_box()
    pad.addWidget(ed.hero_box)
    ed.hero_hint = _plain_label(HERO_HINT, "dim")
    ed.hero_hint.setWordWrap(True)
    chrome.rail_backed(ed.hero_hint)
    pad.addWidget(ed.hero_hint)
    rail.box.addLayout(pad)
    ed.scheme_list = QListWidget()
    ed.scheme_list.setObjectName("schemeTabs")
    ed.scheme_list.setItemDelegate(_SchemeTabDelegate(ed.scheme_list))
    ed.scheme_list.setSpacing(6)
    ed.scheme_list.setFrameShape(QFrame.NoFrame)
    ed.scheme_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ed.scheme_list.setMouseTracking(True)
    ed.scheme_list.viewport().setAutoFillBackground(False)
    ed.scheme_list.currentItemChanged.connect(ed._list_changed)
    rail.box.addWidget(ed.scheme_list, 1)
    ed._make_actions(kit)
    ed.action_row = QWidget()
    actions = QGridLayout(ed.action_row)
    actions.setContentsMargins(14, 0, 30, 0)
    actions.setHorizontalSpacing(8)
    actions.setVerticalSpacing(10)
    # 一个按钮一行：栏窄，「新建空方案」和「复制」并排放不下（会被压窄）；藏起来的那几个不占行
    for row, btn in enumerate((ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn)):
        actions.addWidget(btn, row, 0, Qt.AlignLeft)
    rail.box.addWidget(ed.action_row)
    left.addWidget(rail, 1)
    root.addLayout(left)

    # ---- 右：深灰面板
    panel = chrome.Panel(seed=8)
    right = QVBoxLayout(panel)
    right.setContentsMargins(22, 20, 22, 16)
    right.setSpacing(12)
    ed.read_only_label = _WrapWarn(READ_ONLY_NOTICE, name="warn")
    right.addWidget(ed.read_only_label)
    form = chrome.Card(seed=41, margins=(14, 12, 16, 12), radius=22)
    form.box.setSpacing(8)
    # 折行的说明最小高度按折出来的行数算、目标流派多时说明挪到下一行（同经典主题-反色）：矮屏幕上不压扁输入框，
    # 目标流派三个、四个时窗口不变宽
    build_mine_tabs(ed, kit, form.box, schools=_FlowTargets)
    build_default(ed, form.box)
    right.addWidget(form)

    right.addWidget(ed._make_search(kit))

    right.addWidget(ed._make_stale(kit))

    ed._make_list_area(kit)
    card = chrome.Card(seed=42, margins=(12, 10, 10, 10), radius=22)
    card.box.setSpacing(6)
    card.box.addWidget(ed.list_note)
    card.box.addWidget(ed.chip_list, 1)
    right.addWidget(card, 1)
    right.addWidget(ed.lock_label)
    right.addWidget(ed.hints_box)

    bottom, lay = _row(10)
    ed.confirm_row, confirm = _row(8)
    # 删除的确认：黑底黄字的底板（旁边的「删除」按钮已经有「!」圆标，这里不再画），一行写完
    ed._make_confirm(kit)
    ed.confirm_label.setWordWrap(False)
    for w in (ed.confirm_label, ed.confirm_yes, ed.confirm_no):
        confirm.addWidget(w)
    ed.confirm_row.hide()
    lay.addWidget(ed.confirm_row)
    lay.addStretch(1)
    ed._make_saved_and_close(bottom, lay, kit)
    right.addWidget(bottom)
    column = QVBoxLayout()
    column.setContentsMargins(0, 18, 0, 0)  # 面板从顶上 18 开始，「返回」在它左上方
    column.addWidget(panel, 1)
    root.addLayout(column, 1)


def build_mine_tabs(ed, kit: _Kit, col: QVBoxLayout, schools=None) -> None:
    """游戏内风格的编辑区（我的方案）：两行，第一行名称、名称的说明、作者，第二行说明、目标流派；下面复制来源和目标
    流派的说明。每一项的标题（样式表里 #field 写成橙色小标签）在它的输入框左边。schools 给了时目标流派一栏用它建
    （例如 _FlowTargets，它的 below 放在第二行下面），不给时为 TargetSchools。"""
    ed.mine_box = QWidget()
    box = QVBoxLayout(ed.mine_box)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(8)
    ed._make_fields(kit)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    ed.name_edit.setFixedWidth(150)
    lay.addWidget(ed.name_edit)
    lay.addWidget(ed.name_hint)
    lay.addSpacing(6)
    lay.addWidget(_plain_label(AUTHOR_LABEL, "field"))
    lay.addWidget(ed.author_edit, 1)
    box.addWidget(row)
    box.addWidget(ed.copied_label)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NOTE_LABEL, "field"))
    ed.note_edit.setMinimumWidth(110)  # 目标流派多时让出地方（窗口不跟着变宽）
    lay.addWidget(ed.note_edit, 1)
    lay.addSpacing(6)
    ed.targets = (schools or TargetSchools)()
    lay.addWidget(ed.targets)
    box.addWidget(row)
    below = getattr(ed.targets, "below", None)  # 目标流派多时挪到下面一行的说明（_FlowTargets）
    if below is not None:
        box.addWidget(below)
    box.addWidget(ed.target_note)
    col.addWidget(ed.mine_box)

# ---- 经典主题-黑（card_row）、经典主题-反色（card_column）

def game_frame(ed, *head_widgets) -> QVBoxLayout:
    """两种排法共用：窗口底（chrome.Frame）、顶上一行（标题，右上角 ×；head_widgets 另放在标题右边，chrome.WindowHead）。
    返回放内容的竖排布局。× 就是关闭窗口（close_btn）。"""
    outer = QVBoxLayout(ed)
    outer.setContentsMargins(0, 0, 0, 0)
    frame = ed.frame = chrome.Frame()
    outer.addWidget(frame)
    root = QVBoxLayout(frame)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    head = chrome.WindowHead(EDITOR_TITLE)
    for widget in head_widgets:
        head.add(widget)
    ed.back = head.closer
    root.addWidget(head)
    body = QVBoxLayout()
    root.addLayout(body, 1)
    return body


def game_list(ed, delegate, horizontal: bool) -> QListWidget:
    """画成卡片的方案列表（delegate：_SchemeCardDelegate 横排一行、_SchemeTileDelegate 竖排）。"""
    from PySide6.QtWidgets import QListView

    lst = QListWidget()
    lst.setObjectName("schemeCards")
    lst.setItemDelegate(delegate(lst))
    lst.setFrameShape(QFrame.NoFrame)
    lst.setMouseTracking(True)
    lst.viewport().setAutoFillBackground(False)
    if horizontal:
        lst.setFlow(QListView.LeftToRight)
        lst.setWrapping(False)
        lst.setFixedHeight(_SchemeCardDelegate.HEIGHT + 14)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    else:
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    lst.currentItemChanged.connect(ed._list_changed)
    return lst


def game_list_side(ed, kit, col: QVBoxLayout, confirm_box: QVBoxLayout | None = None) -> None:
    """两种排法共用的右边一栏：只读提示、搜索行、对不上的筹码、列表上方的说明、筹码列表（只有它上下滚动）、软锁提示、
    规则提示（垫底板），最下面一行左边删除的确认、右边「已保存」（一个窗口只留一个关闭按钮：× 就是关闭；用系统标题栏的
    皮肤另放「关闭」）。confirm_box 给了时删除的确认放在那里（说明折行，下面「删除」「取消」），不放在最下面一行。
    部件的名字和默认的排法相同。折行的说明、提示的最小高度按折出来的行数算（_HoldWrapped）：窗口缩矮时只有筹码列表
    变短，不截字。"""
    ed.read_only_label = _WrapWarn(READ_ONLY_NOTICE, name="warn")
    col.addWidget(ed.read_only_label)
    col.addWidget(ed._make_search(kit))
    col.addWidget(ed._make_stale(kit))
    ed._make_list_area(kit)
    col.addWidget(ed.list_note)
    col.addWidget(ed.chip_list, 1)
    col.addWidget(ed.lock_label)
    col.addWidget(ed.hints_box)
    bottom, lay = _row(10)
    ed._make_confirm(kit)  # 旁边的「删除」按钮已经有警告标
    if confirm_box is None:  # 最下面一行左边：说明一行写完，后面「删除」「取消」
        ed.confirm_row, confirm = _row(8)
        ed.confirm_label.setWordWrap(False)
        for w in (ed.confirm_label, ed.confirm_yes, ed.confirm_no):
            confirm.addWidget(w)
        lay.addWidget(ed.confirm_row)
    else:  # 放在 confirm_box 里：说明折行，下面「删除」「取消」
        ed.confirm_row = QWidget()
        stack = QVBoxLayout(ed.confirm_row)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(8)
        stack.addWidget(ed.confirm_label)
        buttons, line = _row(8)
        line.addWidget(ed.confirm_yes)
        line.addWidget(ed.confirm_no)
        line.addStretch(1)
        stack.addWidget(buttons)
        confirm_box.addWidget(ed.confirm_row)
    ed.confirm_row.hide()
    lay.addStretch(1)
    ed._make_saved_and_close(bottom, lay, kit)
    col.addWidget(bottom)


def build_card_row(ed, kit=GAME_KIT) -> None:
    """经典主题-黑的排法（照游戏「卡牌商店」）：顶上正中的标题页签、右上角 ×；下面一行「角色」下拉框和它的说明；再下面
    方案像卡牌横排一行（_SchemeCardDelegate），右边新建、复制、删除；最下面左边一栏靛紫网点的表单（名称、作者、说明、
    目标流派，选中默认推荐时换成默认推荐的说明；窗口矮时在栏里上下滚动），右边是搜索行、筹码列表、软锁提示和规则提示。"""
    body = game_frame(ed)
    body.setContentsMargins(26, 8, 22, 18)
    body.setSpacing(12)
    row, lay = _row(12)
    lay.addWidget(_plain_label(HERO_LABEL, "railTitle"))
    ed.hero_box = ed._make_hero_box()
    ed.hero_box.setMinimumWidth(190)
    lay.addWidget(ed.hero_box)
    ed.hero_hint = _plain_label(HERO_HINT, "dim")
    lay.addWidget(ed.hero_hint, 1)
    body.addWidget(row)
    row, lay = _row(10)
    ed.scheme_list = game_list(ed, _SchemeCardDelegate, horizontal=True)
    lay.addWidget(ed.scheme_list, 1)
    ed._make_actions(kit)
    ed.action_row, actions = _row(10)
    for btn in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn):
        actions.addWidget(btn)
    lay.addWidget(ed.action_row, 0, Qt.AlignVCenter)
    body.addWidget(row)
    middle = QHBoxLayout()
    middle.setSpacing(16)
    form = chrome.Card(seed=41, margins=(14, 14, 16, 14))
    form.setFixedWidth(272)
    # 表单放在透明的滚动区里：窗口缩矮时这一栏上下滚动，不截掉文字、不压扁输入框
    scroll = QScrollArea()
    scroll.setObjectName("formScroll")
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFocusPolicy(Qt.NoFocus)
    fields = QWidget()
    fields.setObjectName("formBody")
    column = QVBoxLayout(fields)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    build_mine_column(ed, kit, column)
    build_default(ed, column, moved=True)
    column.addStretch(1)
    scroll.setWidget(fields)
    form.box.addWidget(scroll)
    middle.addWidget(form)
    right = QVBoxLayout()
    right.setSpacing(8)
    game_list_side(ed, kit, right)
    middle.addLayout(right, 1)
    body.addLayout(middle, 1)


def build_mine_column(ed, kit, col: QVBoxLayout) -> None:
    """竖排的编辑区（card_row 左边一栏）：名称（右边写「最多 12 个字」）、作者、说明各占两行（标题在上、输入框在下），
    下面复制来源、目标流派（标题单独一行，流派名的胶囊在下面一行，「按必选、推荐的筹码自动判断」再下一行）和它的说明。"""
    ed.mine_box = QWidget()
    box = QVBoxLayout(ed.mine_box)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(6)
    ed._make_fields(kit)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    lay.addStretch(1)
    lay.addWidget(ed.name_hint)
    box.addWidget(row)
    box.addWidget(ed.name_edit)
    box.addWidget(ed.copied_label)
    box.addSpacing(4)
    box.addWidget(_plain_label(AUTHOR_LABEL, "field"))
    box.addWidget(ed.author_edit)
    box.addSpacing(4)
    box.addWidget(_plain_label(NOTE_LABEL, "field"))
    box.addWidget(ed.note_edit)
    box.addSpacing(6)
    ed.targets = TargetSchools()
    lay = ed.targets.layout()
    heading = lay.itemAt(0).widget()  # 「目标流派」单独一行放在上面，提示放到下面一行（这一栏窄）
    lay.removeWidget(heading)
    lay.removeWidget(ed.targets.hint)
    box.addWidget(heading)
    box.addWidget(ed.targets)
    ed.targets.hint.setWordWrap(True)
    box.addWidget(ed.targets.hint)
    box.addWidget(ed.target_note)
    col.addWidget(ed.mine_box)


def build_card_column(ed, kit=GAME_KIT) -> None:
    """经典主题-反色的排法（照游戏结算界面）：顶上黑色通栏写标题、「角色」下拉框，右上角 ×；左边一列白色方案卡
    （_SchemeTileDelegate，上面一张小白卡写角色下拉框的说明，下面新建、复制、删除），右边一张大白卡片放编辑区（名称、
    作者一行，说明、目标流派一行）、搜索行、筹码列表、软锁提示和规则提示。"""
    title = _plain_label(HERO_LABEL, "railTitle")
    ed.hero_box = ed._make_hero_box()
    ed.hero_box.setObjectName("bandCombo")  # 写在黑色通栏上的下拉框（样式表另给一种底色）
    ed.hero_box.setMinimumWidth(196)
    body = game_frame(ed, title, ed.hero_box)
    body.setContentsMargins(20, 20, 20, 20)
    columns = QHBoxLayout()
    columns.setSpacing(18)
    left_widget = QWidget()
    left_widget.setFixedWidth(204)
    left = QVBoxLayout(left_widget)
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(12)
    hint_card = chrome.Card(seed=43, margins=(10, 8, 10, 8))
    ed.hero_hint = _WrapLabel(HERO_HINT, "heroHint")
    hint_card.box.addWidget(ed.hero_hint)
    left.addWidget(hint_card)
    ed.scheme_list = game_list(ed, _SchemeTileDelegate, horizontal=False)
    left.addWidget(ed.scheme_list, 1)
    ed._make_actions(kit)
    ed.action_row = QWidget()
    actions = QGridLayout(ed.action_row)
    actions.setContentsMargins(0, 0, 0, 0)
    actions.setHorizontalSpacing(8)
    actions.setVerticalSpacing(10)
    # 一个按钮一行：这一栏只有 204 宽，「新建空方案」和「复制」并排放不下（会被压窄）；藏起来的那几个不占行
    for row, btn in enumerate((ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn)):
        actions.addWidget(btn, row, 0, Qt.AlignLeft)
    left.addWidget(ed.action_row)
    columns.addWidget(left_widget)
    card = chrome.Card(seed=44, margins=(14, 14, 14, 12))
    card.box.setSpacing(10)
    build_mine_tabs(ed, kit, card.box, schools=_FlowTargets)
    build_default(ed, card.box)
    # 删除的确认放在左边一栏（「删除」按钮所在的地方）：右边的卡片窄，一行写不下，放在下面会把窗口撑宽
    game_list_side(ed, kit, card.box, confirm_box=left)
    columns.addWidget(card, 1)
    body.addLayout(columns, 1)

# ---- 霓虹贴纸（side_column）、白色海景房（tile_column）

def side_hero(ed, col: QVBoxLayout) -> None:
    """两种排法共用：左边一栏最上面的「角色」、角色下拉框和它的说明（折行，最小高度按折出来的行数算）。"""
    col.addWidget(_plain_label(HERO_LABEL, "railTitle"))
    ed.hero_box = ed._make_hero_box()
    col.addWidget(ed.hero_box)
    ed.hero_hint = _WrapLabel(HERO_HINT, "dim")
    col.addWidget(ed.hero_hint)


def side_actions(ed, kit) -> QWidget:
    """两种排法共用：新建空方案、复制、复制为我的方案、删除排成一行（选中默认推荐时只有新建空方案，默认推荐有固定档位时
    另有复制为我的方案）；一栏放不下时自动换到下一行（FlowLayout），不把按钮挤出栏外。"""
    ed._make_actions(kit)
    ed.action_row = QWidget()
    actions = FlowLayout(ed.action_row, 6, 8)
    for btn in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn):
        actions.addWidget(btn)
    return ed.action_row


def build_side_column(ed, kit=GAME_KIT) -> None:
    """霓虹贴纸的排法：顶上一行白字黑描边的标题、右上角 ×；左边一条侧栏，从上往下是「角色」下拉框和说明、方案列表（贴纸
    卡片里，选中的一行整块星光黄，_SchemeStickerDelegate）、新建 / 复制 / 删除、名称作者说明的表单卡片（选中默认推荐时换成
    默认推荐的说明）；右边一整列是目标流派和它的说明、搜索行、筹码列表（贴纸卡片里）、软锁提示和规则提示，最下面一行左边
    删除的确认、右边「已保存」。目标流派属于编辑区（选中默认推荐时和左边的表单一起藏起来，_LinkedBox）。"""
    body = game_frame(ed)
    body.setContentsMargins(20, 0, 22, 18)
    columns = QHBoxLayout()
    columns.setSpacing(18)
    side = QWidget()
    side.setFixedWidth(232)
    left = QVBoxLayout(side)
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(8)
    side_hero(ed, left)
    list_card = chrome.Card(seed=45, margins=(2, 6, 2, 6))
    ed.scheme_list = game_list(ed, _SchemeStickerDelegate, horizontal=False)
    # 矮屏幕上侧栏放不下时方案列表最矮缩到一行（在列表里滚动），下面的表单不被压扁
    ed.scheme_list.setMinimumHeight(_SchemeStickerDelegate.ROW_H + _SchemeStickerDelegate.HEADER_H)
    list_card.box.addWidget(ed.scheme_list)
    left.addWidget(list_card, 1)
    left.addWidget(side_actions(ed, kit))
    # 删除的确认放在侧栏「删除」按钮那里（说明折行，下面「删除」「取消」）：右边一栏一行写不下，放在下面会把窗口撑宽
    confirm_holder = QVBoxLayout()
    confirm_holder.setContentsMargins(0, 0, 0, 0)
    left.addLayout(confirm_holder)
    ed._make_fields(kit)
    targets_box = QWidget()
    targets = QVBoxLayout(targets_box)
    targets.setContentsMargins(0, 0, 0, 0)
    targets.setSpacing(4)
    ed.targets = TargetSchools()
    targets.addWidget(ed.targets)
    targets.addWidget(ed.target_note)
    ed.mine_box = _LinkedBox([targets_box])
    box = QVBoxLayout(ed.mine_box)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(4)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    lay.addStretch(1)
    lay.addWidget(ed.name_hint)
    box.addWidget(row)
    box.addWidget(ed.name_edit)
    box.addWidget(ed.copied_label)
    box.addSpacing(4)
    box.addWidget(_plain_label(AUTHOR_LABEL, "field"))
    box.addWidget(ed.author_edit)
    box.addSpacing(4)
    box.addWidget(_plain_label(NOTE_LABEL, "field"))
    box.addWidget(ed.note_edit)
    form = chrome.Card(seed=41, margins=(12, 10, 12, 12))
    form.box.setSpacing(6)
    form.box.addWidget(ed.mine_box)
    build_default(ed, form.box, moved=True)
    ed.default_schools.setWordWrap(True)
    ed.no_tiers_label.setWordWrap(True)
    left.addWidget(form)
    columns.addWidget(side)
    # 右边一栏放在一个部件里（布局装在部件上，放进去的部件马上有主：下面挪动筹码列表时别的部件不会被回收）
    column = QWidget()
    right = QVBoxLayout(column)
    right.setContentsMargins(0, 0, 0, 0)
    right.setSpacing(8)
    right.addWidget(targets_box)
    game_list_side(ed, kit, right, confirm_box=confirm_holder)
    # 筹码列表放进一张贴纸卡片里（只有它上下滚动）
    index = right.indexOf(ed.chip_list)
    right.removeWidget(ed.chip_list)
    list_frame = chrome.Card(seed=42, margins=(4, 6, 2, 6))
    list_frame.box.addWidget(ed.chip_list)
    right.insertWidget(index, list_frame, 1)
    columns.addWidget(column, 1)
    body.addLayout(columns, 1)


def build_tile_column(ed, kit=GAME_KIT) -> None:
    """白色海景房的排法：顶上一行标题（前面一个渐变小方块）、右上角 ×；左边一栏从上往下是「角色」下拉框和说明、一张张
    白色方案卡（选中的紫粉渐变框线，_SchemeWellDelegate）、最下面新建 / 复制 / 删除；右边是编辑区（名称、名称的说明、
    作者一行，目标流派和它的说明，说明一行）、搜索行、筹码列表（白卡片的底，档位是滑块式的分段按钮）、软锁提示和规则提示，
    最下面一行左边删除的确认、右边「已保存」。"""
    body = game_frame(ed)
    body.setContentsMargins(18, 0, 20, 16)
    columns = QHBoxLayout()
    columns.setSpacing(16)
    side = QWidget()
    side.setFixedWidth(206)
    left = QVBoxLayout(side)
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(8)
    side_hero(ed, left)
    ed.scheme_list = game_list(ed, _SchemeWellDelegate, horizontal=False)
    ed.scheme_list.setMinimumHeight(_SchemeWellDelegate.ROW_H * 2 + _SchemeWellDelegate.HEADER_H)
    left.addWidget(ed.scheme_list, 1)
    left.addWidget(side_actions(ed, kit))
    columns.addWidget(side)
    right = QVBoxLayout()
    right.setSpacing(8)
    ed.mine_box = QWidget()
    box = QVBoxLayout(ed.mine_box)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(6)
    ed._make_fields(kit)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NAME_LABEL, "field"))
    ed.name_edit.setFixedWidth(170)
    lay.addWidget(ed.name_edit)
    lay.addWidget(ed.name_hint)
    lay.addSpacing(8)
    lay.addWidget(_plain_label(AUTHOR_LABEL, "field"))
    ed.author_edit.setMinimumWidth(110)
    lay.addWidget(ed.author_edit, 1)
    box.addWidget(row)
    box.addWidget(ed.copied_label)
    ed.targets = TargetSchools()
    box.addWidget(ed.targets)
    box.addWidget(ed.target_note)
    row, lay = _row(8)
    lay.addWidget(_plain_label(NOTE_LABEL, "field"))
    lay.addWidget(ed.note_edit, 1)
    box.addWidget(row)
    right.addWidget(ed.mine_box)
    build_default(ed, right)
    # 删除的确认放在左边一栏最下面（「删除」按钮那里，说明折行）：右边一行写不下，放在下面会把窗口撑宽
    game_list_side(ed, kit, right, confirm_box=left)
    columns.addLayout(right, 1)
    body.addLayout(columns, 1)

# ---- 碳纤流光（tab_strip）

def build_tab_strip(ed, kit=GAME_KIT) -> None:
    """碳纤流光的排法：顶上一行（四色小方块、斜体粗体的标题，右上角 ×）；下面一行「角色」下拉框和它的说明；再下面方案
    列表画成一排页签（_SchemeStripDelegate：选中的那一页底下一条亮色线），右边新建、复制、删除；编辑区是两列的表单（名称、
    作者一列，目标流派、说明一列），下面复制来源和目标流派的说明（选中默认推荐时换成默认推荐的说明）；再下面搜索行、筹码
    列表（切角的面板里，只有它上下滚动）、软锁提示和规则提示，最下面一行左边删除的确认、右边「已保存」。按钮、搜索框、
    目标流派的胶囊画成切角的框（chrome.cut_corners）。部件的名字和默认的排法相同。"""
    body = game_frame(ed)
    body.setContentsMargins(22, 0, 22, 16)
    body.setSpacing(8)
    row, lay = _row(12)
    lay.addWidget(_plain_label(HERO_LABEL, "field"))
    ed.hero_box = ed._make_hero_box()
    ed.hero_box.setMinimumWidth(200)
    lay.addWidget(ed.hero_box)
    ed.hero_hint = _plain_label(HERO_HINT, "dim")
    lay.addWidget(ed.hero_hint, 1)
    body.addWidget(row)
    row, lay = _row(10)
    ed.scheme_list = game_list(ed, _SchemeStripDelegate, horizontal=True)
    ed.scheme_list.setFixedHeight(_SchemeStripDelegate.HEIGHT + 8)
    lay.addWidget(ed.scheme_list, 1)
    ed._make_actions(kit)
    ed.new_btn.kind = "secondary"  # 新建空方案和复制同一种样子：页签旁边这一排按钮不抢眼
    ed.action_row, actions = _row(6)
    for btn in (ed.new_btn, ed.copy_btn, ed.copy_default_btn, ed.delete_btn):
        actions.addWidget(btn)
    lay.addWidget(ed.action_row, 0, Qt.AlignVCenter)
    body.addWidget(row)
    ed.mine_box = QWidget()
    box = QVBoxLayout(ed.mine_box)
    box.setContentsMargins(0, 2, 0, 0)
    box.setSpacing(6)
    ed._make_fields(kit)
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(8)
    grid.addWidget(_plain_label(NAME_LABEL, "field"), 0, 0)
    line, lay = _row(10)
    lay.addWidget(ed.name_edit, 1)
    lay.addWidget(ed.name_hint)
    grid.addWidget(line, 0, 1)
    ed.targets = _FlowTargets()  # 目标流派多时说明挪到表单下面一行（below），这一列不把窗口撑宽
    heading = ed.targets.layout().itemAt(0).widget()  # 「目标流派」放在左边那一格，和「说明」对齐
    ed.targets.layout().removeWidget(heading)
    grid.addWidget(heading, 0, 2)
    grid.addWidget(ed.targets, 0, 3)
    grid.addWidget(_plain_label(AUTHOR_LABEL, "field"), 1, 0)
    grid.addWidget(ed.author_edit, 1, 1)
    grid.addWidget(_plain_label(NOTE_LABEL, "field"), 1, 2)
    grid.addWidget(ed.note_edit, 1, 3)
    grid.setColumnStretch(1, 1)
    grid.setColumnStretch(3, 1)
    box.addLayout(grid)
    box.addWidget(ed.targets.below)
    box.addWidget(ed.copied_label)
    box.addWidget(ed.target_note)
    body.addWidget(ed.mine_box)
    build_default(ed, body)
    game_list_side(ed, kit, body)
    # 筹码列表放进一张切角的面板里（只有它上下滚动）
    index = body.indexOf(ed.chip_list)
    body.removeWidget(ed.chip_list)
    frame = chrome.CutCard(margins=(4, 6, 4, 6))
    frame.box.addWidget(ed.chip_list)
    body.insertWidget(index, frame, 1)
    chrome.cut_corners(ed.search_edit, "field")
    for pill in ed.targets.pills.values():
        chrome.cut_corners(pill, "pill")
    for button in ed.findChildren(QPushButton):  # 「列进方案」「取消」「移除」：样式表画的按钮
        if not isinstance(button, chrome.GameButton):
            chrome.cut_corners(button)


LAYOUTS["tab_strip"] = "_build_tab_strip"  # 方案列表是顶上一排页签、表单排成两列（碳纤流光）
