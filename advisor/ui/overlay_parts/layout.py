"""搭界面和按显示方式摆放卡片：悬浮窗的排版。

搭界面（build）按区域分成几个函数：标题栏、全局提示、「识别」「筹码方案」「推荐」「已有筹码」四张卡片和底栏。控件工厂、
自绘部件从 factory、widgets 等模块取；建界面时按皮肤设的几处登记在悬浮窗上（Overlay._skinned）。切换显示方式
（apply_mode）按一张表 CARD_WHEN_COLLAPSED 决定各卡片在「一行显示」时出现与否，再按当前快照摆放、算摘要。

做成函数、不做成部件：排版是窗口外壳的事。建出来的控件挂在悬浮窗上（外壳和测试按名字取），管它的部件建的时候另收一份
（Overlay.__init__ 在搭完界面以后建这几个部件）；接到这几个部件方法上的信号等部件建好再接（connect_parts）。
Overlay.refresh 直接调用这里，悬浮窗上不留转发。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..text import tip_html
from ..theme import COMMON as C
from ..theme import OVERLAY as O
from ..widgets import (Card, ChipGrid, FlowLayout, MapPill, Pill, TagLabel, WheelLockedCombo, draw_combo_rows,
                       fill_hero_combo)
from .factory import (
    IconTextButton,
    WrapLabel,
    _button,
    _column_widget,
    _grid,
    _message_label,
    _own_style,
    _Panel,
    _rich_label,
    _row_widget,
    _set_accent,
    _set_tip,
    _text_label,
    _text_rules,
)
from .state import EV_REFRESHING
from .wording import *  # noqa: F401,F403


def build(ov) -> None:
    """搭界面（默认的排法）：标题栏、全局提示、四张卡片（识别、筹码方案、推荐、已有筹码）、底栏。卡片左右边缘和标题栏、
    底栏对齐。面板的内边距、各部分之间的间距按皮肤（O.PANEL_MARGINS 等）。"""
    panel = ov.panel = _Panel(ov)
    panel.setObjectName("panel")
    outer = QVBoxLayout(ov)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(panel)
    box = QVBoxLayout(panel)
    ov._skinned(lambda b=box: b.setContentsMargins(*O.PANEL_MARGINS))
    ov._skinned(lambda b=box: b.setSpacing(O.PANEL_SPACING))  # 卡片之间

    build_head(ov, box)
    build_global(ov, box)
    build_recog(ov, box)
    build_scheme(ov, box)
    build_advice(ov, box)
    build_owned(ov, box)
    ov.cards = {"recog": ov.recog_card, "scheme": ov.scheme_card, "advice": ov.advice_card, "owned": ov.owned_card}
    from .. import chrome

    for i, card in enumerate(ov.cards.values(), 1):  # 区域名前的编号（皮肤画成「01 识别」时用，碳纤流光）
        card.title.setProperty("number", f"{i:02d}")
        chrome.cut_corners(card.toggle)  # 折叠按钮：皮肤要切角时画成切角的框；别的皮肤照样式表画
    ov._notice_boxes = {
        "global": ov.global_notice_box, "recog": ov.recog_notice_box, "recog_top": ov.recog_top_box,
        "advice": ov.advice_notice_box, "owned": ov.owned_notice_box,
    }

    build_toolbar(ov, box)


def build_head(ov, box) -> None:
    """标题栏：一行时是「摘要 + 全部展开 + ×」，全部展开时是「标题 + 全部折叠 + ×」（识别模式和热键在设置窗口里）。
    管整个悬浮窗的按钮都在这里；「全部展开」「全部折叠」是同一个按钮，随显示方式换文字。"""
    head = ov.head = QHBoxLayout()
    ov._skinned(lambda h=head: h.setSpacing(O.HEAD_SPACING))
    title = ov.title = TagLabel(TITLE_DEV if ov.dev_label else TITLE, "title")  # 皮肤可以画成页签的样子
    title.setObjectName("title")
    ov.summary = QLabel("")
    ov.summary.setObjectName("summary")
    ov.summary.setTextFormat(Qt.RichText)
    ov.summary.setTextInteractionFlags(Qt.NoTextInteraction)  # 富文本默认接走鼠标；关掉文字交互，按在文字上也能拖动悬浮窗
    ov.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    ov.head_spacer = QWidget()
    ov.head_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    ov.quick_add = _button("补录筹码", accent="primary")
    ov.quick_add.setObjectName("quickAdd")
    ov.quick_add.clicked.connect(ov.open_add)
    ov.toggle_btn = _button(EXPAND_ALL)
    ov.toggle_btn.setObjectName("toggleAll")  # 皮肤的样式表可以单独给它样子
    ov.toggle_btn.clicked.connect(ov.toggle_expanded)
    close = ov.close_btn = _button("×", tip="退出拾星助手")
    close.setObjectName("closeButton")  # 皮肤的样式表可以单独给它样子
    # 只有一个字符，左右内边距比其他按钮小（只作用于按钮本身，悬停说明的内边距照样式表里的 QToolTip）
    ov._skinned(lambda w=close: _own_style(w, f"padding: {O.CLOSE_PADDING}"))
    # 标题在标题栏正中的皮肤（OverlayLook.TITLE_CENTER）：标题左边先放一块和右边按钮一样宽的空，再放一块能伸缩的空
    # （和右边的 head_spacer 平分多出来的宽），标题就落在正中。平时都藏着（不占地方）
    ov.head_lead = QWidget()
    ov.head_lead.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    ov.head_lead_stretch = QWidget()
    ov.head_lead_stretch.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    for w in (ov.head_lead, ov.head_lead_stretch):
        w.hide()
    for w in (ov.head_lead, ov.head_lead_stretch, title, ov.summary, ov.head_spacer, ov.quick_add,
              ov.toggle_btn, close):
        head.addWidget(w, 1 if w is ov.summary else 0)
    # 面板画底纹时要看标题栏这一行的几个控件（_Panel.head）
    ov.panel.head = {"title": title, "summary": ov.summary, "toggle_btn": ov.toggle_btn, "close_btn": close,
                     "quick_add": ov.quick_add}
    ov._skinned(ov._align_head)
    box.addLayout(head)


def build_global(ov, box) -> None:
    """全局提示（不是卡片，有内容时才出现）：图片下载、校准、诊断包位置，以及热键、模式等操作的结果。"""
    ov.global_area, global_box = _column_widget(5)
    ov.global_area.setObjectName("globalArea")  # 皮肤的样式表可以给这一块底色（GLOBAL_MARGINS 为内边距）
    ov._skinned(lambda b=global_box: b.setContentsMargins(*O.GLOBAL_MARGINS))
    ov.global_notices, ov.global_notice_box = _column_widget(5)
    ov.global_msg = _message_label()
    global_box.addWidget(ov.global_notices)
    global_box.addWidget(ov.global_msg)
    box.addWidget(ov.global_area)


def build_recog(ov, box) -> None:
    """「识别」卡片：状态胶囊（角色胶囊在里面，地图胶囊可点击）、地图选择行、新一局（撤销）、不支持的情况。"""
    recog = ov.recog_card = Card("recog", "识别")
    recog.toggle_clicked.connect(lambda: ov._toggle_card("recog"))
    # 对局中途判定的新一局（可撤销）和截取方式的提示排在卡片最上面
    ov.recog_top, ov.recog_top_box = _column_widget(5)
    recog.body_box.addWidget(ov.recog_top)
    # 状态胶囊：自动换行。第一项是识别本身的状态（有时才有），接着是不可点击的角色胶囊（已识别 / 手动指定 / 待识别），
    # 手动指定时紧跟「恢复自动识别」按钮，然后是轮次、星数等（_show_status 排）；角色下拉框在下面的「筹码方案」卡片里。
    # 地图胶囊是地图下拉框（点开列出「地图：自动识别」和各张地图）
    ov.status_box = QWidget()
    ov.status_flow = FlowLayout(ov.status_box, 4, 4)
    ov.hero_state = Pill()
    ov.hero_state.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # 一行写完
    ov.hero_restore = _button("恢复自动识别")
    ov.hero_restore.clicked.connect(ov._hero_restore_clicked)
    ov.hero_restore.hide()
    ov.map_box = MapPill()
    ov.map_box.addItem(MAP_AUTO)
    ov.map_box.addItems(ov.db.maps)
    draw_combo_rows(ov.map_box)  # 下拉列表每一行自己画（鼠标所在的行亮灰圆角框，当前的值左边一根短条）
    ov.map_box.currentTextChanged.connect(ov._map_changed)
    for widget in (ov.hero_state, ov.hero_restore, ov.map_box):
        ov.status_flow.addWidget(widget)
    recog.body_box.addWidget(ov.status_box)
    # 地图选择行：地图待识别时提示打开任务栏；几张地图的匹配度都低时请用户选，候选地图按钮放在提示的右侧
    ov.map_row, map_line = _row_widget(8)
    ov.map_prompt = WrapLabel("")  # 自动换行时不拆开「打开」「任务栏」等词
    ov.map_buttons = _grid()
    ov.map_dismiss = _button(
        DISMISS, tip="不打开任务栏，关闭本条提示（这一局不再提示；也可点击上方的地图下拉框手动指定地图）")
    ov.map_dismiss.hide()
    map_line.addWidget(ov.map_prompt, 1)
    map_line.addLayout(ov.map_buttons)
    map_line.addWidget(ov.map_dismiss, 0, Qt.AlignVCenter)
    recog.body_box.addWidget(ov.map_row)
    ov.recog_notices, ov.recog_notice_box = _column_widget(5)
    recog.body_box.addWidget(ov.recog_notices)
    ov.recog_msg = _message_label()
    recog.body_box.addWidget(ov.recog_msg)
    box.addWidget(recog)


def build_scheme(ov, box) -> None:
    """「筹码方案」卡片：角色下拉框（「称号 · 角色名」，按六类排，每一类前面一行不能选的组名「── 物理主C ──」）在上、
    方案下拉框在下（等宽、左对齐，都是默认样式、不加颜色），右边并排「编辑方案」「导入/导出」两个按钮（图标在上、字在下），
    都跨两行：高度等于两个下拉框加中间的间距。"""
    scheme = ov.scheme_card = Card("scheme", SCHEME_CARD)
    scheme.toggle_clicked.connect(lambda: ov._toggle_card("scheme"))
    ov.hero_box = QComboBox()
    fill_hero_combo(ov.hero_box, ov.db.heroes.values())
    _set_tip(ov.hero_box, HERO_BOX_TIP)
    ov.hero_box.setCursor(Qt.PointingHandCursor)
    # activated：用户在下拉框里点选时发出，选的和当前显示的相同也发（角色待识别时，下拉框显示的往往就是本局的角色）
    ov.hero_box.activated.connect(ov._hero_changed)
    ov.scheme_box = WheelLockedCombo()  # 滚轮不切换方案（换方案会立即重算推荐）
    _set_tip(ov.scheme_box, SCHEME_BOX_TIP)
    ov.scheme_box.setCursor(Qt.PointingHandCursor)
    ov.scheme_box.activated.connect(ov._scheme_chosen)
    for combo in (ov.hero_box, ov.scheme_box):
        draw_combo_rows(combo)
    ov.edit_btn = IconTextButton(SCHEME_EDIT_ICON, SCHEME_EDIT, SCHEME_EDIT_TIP)
    ov.edit_btn.setProperty("tool", "edit")  # 皮肤的样式表可以给两个按钮不同的底色（[tool="share"]）
    ov.edit_btn.clicked.connect(ov.dialogs.open_editor)
    ov.share_btn = IconTextButton(SCHEME_SHARE_ICON, SHARE_BTN, SHARE_TIP)
    ov.share_btn.setProperty("tool", "share")
    ov.share_btn.clicked.connect(ov.dialogs.open_share)
    ov.scheme_body = QWidget()
    grid = QGridLayout(ov.scheme_body)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(6)
    grid.addWidget(ov.hero_box, 0, 0)
    grid.addWidget(ov.scheme_box, 1, 0)
    grid.addWidget(ov.edit_btn, 0, 1, 2, 1)
    grid.addWidget(ov.share_btn, 0, 2, 2, 1)
    grid.setColumnStretch(0, 1)
    scheme.body_box.addWidget(ov.scheme_body)
    ov.scheme_msg = _message_label()  # 切换方案后的一次性提示
    scheme.body_box.addWidget(ov.scheme_msg)
    box.addWidget(scheme)


def build_advice(ov, box) -> None:
    """「推荐」卡片：三选一的推荐（选项块）和附注、改正栏；关闭后的「已选」和「未选择首推的原因」、上一次推荐。"""
    advice = ov.advice_card = Card("advice", "推荐")
    advice.toggle_clicked.connect(lambda: ov._toggle_card("advice"))
    # 「正在刷新」：刚点了刷新、新的候选筹码还没读全时紧跟在区域名后面（REFRESHING）
    ov.refresh_pill = Pill(REFRESHING.segments(), REFRESHING.tone, point_size=C.FONT_S)
    ov.refresh_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    ov.refresh_pill.setToolTip(tip_html(REFRESHING_TIP))
    ov.refresh_pill.hide()
    advice.add_title_widget(ov.refresh_pill)
    # 区域名（和「正在刷新」）后面一段暗色小字：这次推荐按哪个角色、哪套方案算（「芬妮 · 银行卡流」，AdviceCard.show_basis
    # 写字、定显示与否；只在卡片展开、有推荐时显示，放不下时截短，不把卡片头撑成两行）
    ov.advice_basis = _text_label("")
    ov.advice_basis.setObjectName("adviceBasis")
    # 皮肤要求时写在胶囊里（OverlayLook.BASIS_BOX、BASIS_TEXT：卡片头一半压在卡片上沿外面的几套）；和区域名之间多空一点，
    # 区域名也是暗色小字的皮肤里不连成一句
    ov._skinned(lambda w=ov.advice_basis: _own_style(
        w, _text_rules(C.FONT_S, O.BASIS_TEXT or C.DIM_COLOR) + ";margin-left:6px"
        + (f";{O.BASIS_BOX}" if O.BASIS_BOX else "")))
    ov.advice_basis.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    ov.advice_basis.hide()
    advice.add_title_note(ov.advice_basis)
    ov.advice_notices, ov.advice_notice_box = _column_widget(5)
    advice.body_box.addWidget(ov.advice_notices)
    ov.body, ov.body_box = _column_widget(0)  # 三选一显示期间的内容，每次事件重建
    _set_tip(ov.body, LIVE_TIP)
    advice.body_box.addWidget(ov.body)
    ov.picked_label = _rich_label("")
    advice.body_box.addWidget(ov.picked_label)
    ov.question, question_box = _column_widget(4)
    ov.question_label = _rich_label("")
    reasons, reasons_line = _row_widget(6)
    ov.reason_buttons: list[QPushButton] = []
    for text in FEEDBACK_REASONS:
        btn = _button(text, tip=FEEDBACK_TIP.format(reason=text))
        btn.clicked.connect(lambda _=False, r=text: ov.advice.submit_feedback(r))
        ov.reason_buttons.append(btn)
        reasons_line.addWidget(btn)
    reasons_line.addStretch(1)
    question_box.addWidget(ov.question_label)
    question_box.addWidget(reasons)
    advice.body_box.addWidget(ov.question)
    ov.stale, ov.stale_box = _column_widget(0)  # 回看上一次推荐（全部展开时）
    advice.body_box.addWidget(ov.stale)
    ov.idle_label = _text_label("", C.FONT_M, C.SOFT_COLOR, wrap=True)
    ov._skin_hooks.append(lambda w=ov.idle_label: _own_style(w, _text_rules(C.FONT_M, C.SOFT_COLOR)))
    advice.body_box.addWidget(ov.idle_label)
    # 改正栏：点筹码名按钮后出现
    ov.fix_row, fix = _row_widget()
    ov.fix_label = QLabel("")
    ov.fix_label.setTextInteractionFlags(Qt.NoTextInteraction)
    ov._skinned(lambda w=ov.fix_label: _own_style(w, f"color:{C.DIM_COLOR}"))  # 固定文字暗色
    ov.fix_box = ov._chip_combo()
    fix_ok = ov.fix_ok = _button("确定")
    fix_cancel = ov.fix_cancel = _button("取消")
    fix.addWidget(ov.fix_label)
    fix.addWidget(ov.fix_box, 1)
    fix.addWidget(fix_ok)
    fix.addWidget(fix_cancel)
    ov.fix_row.hide()
    advice.body_box.addWidget(ov.fix_row)
    ov.advice_msg = _message_label()
    advice.body_box.addWidget(ov.advice_msg)
    box.addWidget(advice)


def build_owned(ov, box) -> None:
    """「已有筹码」卡片：卡片头「已有筹码 6 个」「＋ 补录筹码」；补录栏、补录行、提示、地图发放的筹码；
    主体左边是 3-4-3 图标格子，右边是筹码小卡片（随筹码数往下延伸，超出屏幕时在卡片内滚动）。"""
    owned = ov.owned_card = Card("owned", "已有筹码")
    owned.toggle_clicked.connect(lambda: ov._toggle_card("owned"))
    ov.owned_count = _rich_label("", wrap=False)
    ov.owned_count.setObjectName("ownedCount")  # 皮肤的样式表可以把它写在深色胶囊里
    owned.add_title_widget(ov.owned_count)
    ov.add_btn = _button("＋ 补录筹码", tip=ADD_TIP)
    ov.add_btn.setObjectName("addOwned")  # 皮肤的样式表可以单独给它样子
    ov.add_btn.clicked.connect(ov.open_add)
    owned.add_header_widget(ov.add_btn)
    # 补录栏：点「＋ 补录筹码」按钮后出现，可输入筹码名搜索
    ov.add_row, add = _row_widget()
    ov.add_label = QLabel("补录筹码")
    ov.add_label.setTextInteractionFlags(Qt.NoTextInteraction)
    ov._skinned(lambda w=ov.add_label: _own_style(w, f"color:{C.DIM_COLOR}"))
    ov.add_box = ov._chip_combo()
    ov.add_box.lineEdit().returnPressed.connect(ov._add_confirm)
    add_ok = _button("确定")
    add_ok.clicked.connect(ov._add_confirm)
    add_cancel = _button("取消")
    add_cancel.clicked.connect(ov.close_add)
    add.addWidget(ov.add_label)
    add.addWidget(ov.add_box, 1)
    add.addWidget(add_ok)
    add.addWidget(add_cancel)
    ov.add_row.hide()
    owned.body_box.addWidget(ov.add_row)
    # 补录行（Session.missed_offer）：补录或点「忽略」之前一直显示
    ov.missed = QWidget()
    missed_box = QVBoxLayout(ov.missed)
    missed_box.setContentsMargins(0, 0, 0, 0)
    missed_box.setSpacing(5)
    ov.missed_label = WrapLabel(MISSED_PROMPT)
    ov.missed_label.setObjectName("ask")
    ov.missed_buttons = _grid()
    missed_box.addWidget(ov.missed_label)
    missed_box.addLayout(ov.missed_buttons)
    ov.missed.hide()
    owned.body_box.addWidget(ov.missed)
    ov.owned_notices, ov.owned_notice_box = _column_widget(5)
    owned.body_box.addWidget(ov.owned_notices)
    # 地图发放的筹码（龙宫游乐园）：不经筹码三选一，点按钮补录
    ov.granted_row, granted_line = _row_widget(8)
    ov.granted_label = QLabel("")
    ov.granted_label.setTextInteractionFlags(Qt.NoTextInteraction)
    ov._skinned(lambda w=ov.granted_label: _own_style(w, f"color:{C.DIM_COLOR}"))
    ov.granted_label.setWordWrap(True)
    ov.granted_buttons = _grid()
    granted_line.addWidget(ov.granted_label, 1)
    granted_line.addLayout(ov.granted_buttons)
    ov.granted_row.hide()
    owned.body_box.addWidget(ov.granted_row)
    ov.owned_msg = _message_label()
    owned.body_box.addWidget(ov.owned_msg)
    ov.owned_empty = _text_label(OWNED_EMPTY, C.FONT_M, C.DIM_COLOR)
    ov._skin_hooks.append(lambda w=ov.owned_empty: _own_style(w, _text_rules(C.FONT_M, C.DIM_COLOR)))
    owned.body_box.addWidget(ov.owned_empty)
    ov.owned_scroll = QScrollArea()
    ov.owned_scroll.setObjectName("ownedScroll")
    ov.owned_scroll.setFrameShape(QFrame.NoFrame)
    ov.owned_scroll.setWidgetResizable(True)
    ov.owned_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ov.owned_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ov.owned_body = QWidget()
    ov.owned_body.setObjectName("ownedBody")
    split = QHBoxLayout(ov.owned_body)
    split.setContentsMargins(0, 0, 0, 0)
    split.setSpacing(8)
    ov.chip_grid = ChipGrid(O.GRID_DIAMETER)
    ov._skin_hooks.append(lambda g=ov.chip_grid: g.set_diameter(O.GRID_DIAMETER))
    split.addWidget(ov.chip_grid, 0, Qt.AlignTop)
    ov.mini_list, ov.mini_box = _column_widget(3)  # 小卡片之间 3（收紧：筹码多时窗口不至于太长）
    split.addWidget(ov.mini_list, 1, Qt.AlignTop)  # 筹码少时小卡片不被拉高到图标格子的高度
    ov.owned_scroll.setWidget(ov.owned_body)
    owned.body_box.addWidget(ov.owned_scroll)
    box.addWidget(owned)


def connect_parts(ov) -> None:
    """接到部件方法上的几个信号（这几个部件要用搭好的控件，在搭完界面以后才建）：标题栏的 ×，改正栏的回车、「确定」「取消」，
    地图选择行的「关闭本条提示」。接的都是部件的方法本身（「取消」照旧收到 clicked 的 checked，见 AdviceCard.fix_close）。"""
    ov.close_btn.clicked.connect(ov.tray_part.close_clicked)
    ov.map_dismiss.clicked.connect(ov.scheme.dismiss_map_hint)
    ov.fix_box.lineEdit().returnPressed.connect(ov.advice.fix_confirm)
    ov.fix_ok.clicked.connect(ov.advice.fix_confirm)
    ov.fix_cancel.clicked.connect(ov.advice.fix_close)


def build_toolbar(ov, box) -> None:
    """底栏（全部展开时）：只有「设置」，靠左（识别模式、热键、导出诊断、关于都在设置窗口里）。
    收回一行的按钮在标题栏（「全部折叠」）。"""
    ov.toolbar, tools = _row_widget()
    ov.settings_btn = _button("设置", tip=SETTINGS_TIP)
    ov.settings_btn.setObjectName("settingsButton")  # 皮肤的样式表可以单独给它样子
    ov.settings_btn.clicked.connect(ov.dialogs.show_settings)
    ov._skinned(ov._settings_glyph)  # 皮肤要时字前面一个齿轮图标
    tools.addWidget(ov.settings_btn)
    tools.addStretch(1)
    box.addWidget(ov.toolbar)


# 一行显示（四张卡片都收起）时，各卡片出现与否：卡片 key → 判断（ov, view, keys, prompt）。
# 全部展开（full）时四张卡片都显示，不看这张表。键的顺序就是界面上卡片的先后（同 CARD_KEYS）。
#   recog、owned：有当前要显示的提示行（keys）时出现；
#   scheme：三选一出现但角色待识别、推荐暂停时整张出现，供选择角色；
#   advice：三选一显示中，或要问「未选择首推的原因」（只问 QUESTION_LINE_MS 那么久，含刚记录时的一下；prompt），或 keys
#   不空。keys 比「只看提示行」多三种键（live:、question:、fix），一行显示时它们只在前两项成立时才出现，所以结果和只看
#   提示行一样。
CARD_WHEN_COLLAPSED = {
    "recog": lambda ov, view, keys, prompt: bool(keys),
    "scheme": lambda ov, view, keys, prompt: ov.live_state.hero_needed,
    "advice": lambda ov, view, keys, prompt: ov.live_state.on or prompt or bool(keys),
    "owned": lambda ov, view, keys, prompt: bool(keys),
}


def apply_mode(ov, view) -> None:
    """按「一行 / 全部展开」和各卡片的折叠状态决定各部分显示与否。view 是这一轮刷新的快照。

    一行显示时，卡片只在有提示时出现（三选一的推荐、请用户选地图或补录等），而且只显示那几行提示；三选一出现但角色待识别
    （推荐暂停）时「筹码方案」卡片整张出现，供选择角色。全部展开时四张卡片都显示。卡片收起着、又出现了新的提示时
    自动展开（用户收起时已在显示的提示除外）。各卡片「一行显示时出现与否」的规则在 CARD_WHEN_COLLAPSED。"""
    full = ov._expanded
    waiting = ov._waiting_text(over=view.game_over)
    notices = ov.notice_model
    for w in (ov.title, ov.head_spacer):
        w.setVisible(full)
    ov.summary.setVisible(not full)
    ov._center_title(full)
    # 标题栏 × 左边的同一个按钮：全部展开时「全部折叠」，一行显示时「全部展开」
    ov.toggle_btn.setText(COLLAPSE_ALL if full else EXPAND_ALL)
    if full:
        _set_tip(ov.toggle_btn, COLLAPSE_ALL_TIP)
    else:
        stale = ov.advice.last_ev is not None and not ov.live_state.on
        _set_tip(ov.toggle_btn, EXPAND_ALL_TIP + (EXPAND_ALL_STALE if stale else ""))
    source = ov.owned.extra_chip_source(view)
    ov.quick_add.setVisible(not full and source is not None)
    _set_accent(ov.add_btn, "primary" if source else "")
    if source:
        tip = EXTRA_CHIP_TIP.format(source=source)
        _set_tip(ov.quick_add, tip)
        _set_tip(ov.add_btn, tip)
    else:
        _set_tip(ov.add_btn, ADD_TIP)

    # ---- 全局提示
    message = ov._area_message("global", waiting) if full else ""
    ov._set_message(ov.global_msg, message)
    shown = ov.global_notice_box.count() > 0
    ov.global_notices.setVisible(shown)
    ov.global_area.setVisible(shown or bool(message))

    # 「推荐」卡片要用的状态先算出来（表里 advice 一行用 prompt）：要问/刚记录「未选择首推的原因」时，一行显示也出现。一行
    # 显示时要问的那一块只挂 QUESTION_LINE_MS（AdviceCard.ask_until），到点不点就收起（这一次不记原因）；全部展开时照旧挂到
    # 下一次三选一（收起以后再全部展开也照旧显示）
    live = ov.live_state.on
    question = ov.advice.question_state()
    flash = question == "done" and time.monotonic() < ov.advice.feedback_until
    asking = question == "ask" and time.monotonic() < ov.advice.ask_until
    prompt = not live and (asking or flash)

    # ---- 识别
    keys = set()
    top = notices.keys("recog_top")
    keys |= top
    ov.recog_top.setVisible(bool(top))
    ov.status_box.setVisible(full)
    # 地图选择行：请用户选地图时一直显示；只是提示打开任务栏时，全部展开要进入对局以后，一行显示另外要三选一没开着
    ov.map_row.setVisible(ov.scheme.map_on and (ov.scheme.map_entered if full else ov.scheme.map_prompt))
    if ov.scheme.map_on and ov.scheme.map_prompt:
        keys.add(f"map:{ov.scheme.map_key}")
    notes = notices.keys("recog")
    keys |= notes
    ov.recog_notices.setVisible(bool(notes))
    ov._set_message(ov.recog_msg, ov._area_message("recog", waiting) if full else "")
    ov._place_card(ov.recog_card, full or CARD_WHEN_COLLAPSED["recog"](ov, view, keys, prompt), keys,
                   ov._recog_summary())

    # ---- 筹码方案：一行显示时平时不出现；三选一出现但角色待识别（推荐暂停）时出现，供选择角色（收起着也自动展开）
    keys = {"hero"} if ov.live_state.hero_needed else set()
    ov._set_message(ov.scheme_msg, ov._area_message("scheme", waiting) if full else "")
    ov._place_card(ov.scheme_card, full or CARD_WHEN_COLLAPSED["scheme"](ov, view, keys, prompt), keys,
                   ov.scheme.summary(view.hero))

    # ---- 推荐
    keys = set()
    ov.body.setVisible(live)
    ov.refresh_pill.setVisible(live and ov.live_state.kind == EV_REFRESHING)
    if live:
        keys.add(f"live:{ov.live_state.seq}")
    elif question == "ask" and (full or asking):
        keys.add(f"question:{ov.advice.pick['offer']}")
    ov.picked_label.setVisible(not live and ov.advice.pick is not None and (full or prompt))
    ov.question.setVisible(not live and question is not None and (full or prompt))
    for btn in ov.reason_buttons:
        btn.setVisible(question == "ask")
    stale = not live and full and ov.advice.last_ev is not None
    if stale:
        ov.advice.show_stale(view)
    ov.stale.setVisible(stale)
    ov.idle_label.setText(waiting)
    ov.idle_label.setVisible(not live and full and ov.advice.last_ev is None and ov.advice.pick is None)
    if not live and (not ov.fix_row.isHidden() or ov.advice.fix_slot is not None):
        ov.advice.fix_close(fit=False)
    if not ov.fix_row.isHidden():
        keys.add("fix")
    notes = notices.keys("advice")
    keys |= notes
    ov.advice_notices.setVisible(bool(notes))
    ov._set_message(ov.advice_msg, ov._area_message("advice", waiting) if full else "")
    ov._place_card(ov.advice_card, full or CARD_WHEN_COLLAPSED["advice"](ov, view, keys, prompt), keys,
                   ov.advice.summary(waiting))
    # 卡片头那段「芬妮 · 银行卡流」：卡片展开着才写（摆好卡片、知道展开与否以后再定）
    ov.advice.show_basis(view, stale)

    # ---- 已有筹码
    keys = set()
    ov.add_row.setVisible(ov._add_open)
    if ov._add_open:
        keys.add(f"add:{ov._add_seq}")
    ov.missed.setVisible(ov.owned.missed_on)
    if ov.owned.missed_on:
        keys.add("missed:" + "、".join(str(n) for n in ov.owned.missed_offer))
    ov.granted_row.setVisible(ov.scheme.granted_on)
    if ov.scheme.granted_on:
        keys.add("granted:" + "、".join(ov.scheme.granted_shown[1]))
    notes = notices.keys("owned")
    keys |= notes
    ov.owned_notices.setVisible(bool(notes))
    ov._set_message(ov.owned_msg, ov._area_message("owned", waiting) if full else "")
    has_chips = bool(ov.owned.owned_shown)
    unknown = notices.panel_prompt or notices.panel_tab is not None
    ov.owned_empty.setText(OWNED_UNKNOWN if unknown else OWNED_EMPTY)
    ov.owned_empty.setVisible(full and not has_chips)
    ov.owned_scroll.setVisible(full and has_chips)
    ov._place_card(ov.owned_card, full or CARD_WHEN_COLLAPSED["owned"](ov, view, keys, prompt), keys, [])

    ov.toolbar.setVisible(full)
