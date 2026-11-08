"""「编辑筹码方案」窗口本身（SchemeEditor）：状态、信号、读写方案、换皮肤，以及一组造部件和接线的方法（_make_*，
七种排法共用一份）。界面文字和规则常数见 notes.py，小部件见 widgets.py，七种排法的摆放见 layouts.py。

窗口约 760 × 720，深色、置顶，不是对话框：打开期间悬浮窗照常识别和推荐；左边是方案列表（「默认推荐」和该角色的
我的方案），右边是编辑区和筹码列表。改动即存，发 schemes_changed（方案所属的角色名）。窗口和各部件、软锁、列表内容
的来龙去脉见本包 __init__.py 开头。"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidgetItem,
    QSizePolicy,
    QWidget,
)

from .. import chrome, lang, names, theme
from ..scheme_list import BlockSpec, SchemeList
from ...schemes import (
    AUTHOR_MAX,
    LISTED_TIERS,
    NAME_MAX,
    NOTE_MAX,
    Scheme,
    SchemesReadOnly,
    Tiers,
    default_marks,
    default_schools,
    get_catalog,
    locked_schools,
    normalize_maps,
    scheme_from_default,
    school_of,
    taggable_maps,
    target_schools,
)
from ..text import tip_paragraphs_html
from ..theme import COMMON as C
from ..theme import EDITOR as L
from ..widgets import draw_combo_rows, fill_hero_combo, first_data
from ..window_base import SkinnedWindow
from .layouts import (
    LAYOUTS,
    build,
    build_card_column,
    build_card_row,
    build_side_column,
    build_tab_strip,
    build_tabs,
    build_tile_column,
)
from .notes import (
    AUTHOR_PLACEHOLDER,
    DEFAULT_NO_SCHOOLS,
    DEFAULT_READ_ONLY,
    DEFAULT_EMPTY_GUIDE,
    DEFAULT_READ_ONLY_EMPTY,
    DEFAULT_ROW,
    DEFAULT_SCHOOLS,
    DEFAULT_TAG,
    DELETE_CONFIRM,
    EDITOR_TITLE,
    LIST_MAPS,
    LIST_NOTE,
    LIST_SEP,
    MINE_HEADER,
    MINE_TAG,
    MIN_LIST_HEIGHT,
    NAME_HINT,
    NEW_EMPTY_TIP,
    ONLY_MARKED,
    PoolNote,
    SAVE_FAILED,
    SAVED,
    SEARCH_PLACEHOLDER,
    STALE_NOTE,
    STALE_REMOVE,
    TEXT_SAVE_MS,
    TITLE_ALLOWANCE,
    USED_TAG,
    content,
    copy_name,
    copied_note,
    default_ends,
    lock_notice,
    max_schools,
    new_name,
    pool_notes,
    rule_hints,
    target_note,
)
from ..scheme_cards import _ID_ROLE, _TAG_ROLE, _USED_ROLE
from .widgets import (
    ICON_SEARCH,
    LockQuestion,
    MapField,
    _CharLimit,
    _Kit,
    _button,
    _glyph_icon,
    _plain_label,
    _row,
)

# ------------------------------------------------------------ 窗口


class SchemeEditor(SkinnedWindow, chrome.EdgeResize, chrome.HideNotice, QWidget):
    """「编辑筹码方案」窗口。db 为资料库，store 为和悬浮窗共用的方案存储（schemes.SchemeStore），hero 为悬浮窗当前的角色
    （None 或资料库未收录时取角色下拉框的第一个）。改动即存，改完发 schemes_changed（方案所属的角色名）；新建、复制了一套
    （新建空方案、复制、复制为我的方案）时接着另发 scheme_added（角色名, 新方案的 id）：悬浮窗据此判断要不要当场换用它。
    左边列表里，悬浮窗对这个角色正在用的那一套（SchemeStore.current；没有时是「默认推荐」那一行）行尾写「使用中」；悬浮窗那边
    换了方案时调 show_used 重标。
    能改大小：用系统标题栏时拖系统的边框，自己画外框时拖窗口边缘（chrome.EdgeResize）；最小为内容需要的大小。"""

    schemes_changed = Signal(str)
    scheme_added = Signal(str, str)
    LAYOUT_FIELD = "EDITOR"
    LAYOUTS = LAYOUTS  # 排法名 → 建法（模块级的表：tab_strip、test-same 这类后加进来的也认）

    def __init__(self, db, store, hero: str | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.store = store
        catalog = getattr(store, "catalog", None)
        self.catalog = catalog if catalog is not None and catalog.db is db else get_catalog(db)
        self.hero = ""
        self.scheme: Scheme | None = None  # 选中的我的方案；选中默认推荐时为 None
        self._default_marks = Tiers()  # 默认推荐的四档和顺位（schemes.default_marks）
        self._ends: dict[str, tuple[str, str]] = {}
        self._notes: dict[str, PoolNote] = {}
        self._dirty_fields: set[QLineEdit] = set()  # 名称、作者、说明里改过、还没存进方案的输入框
        self._loading = False
        self._placed = False
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(TEXT_SAVE_MS)
        self._text_timer.timeout.connect(self._commit_text)
        self.setObjectName("schemeEditor")
        self.setWindowTitle(EDITOR_TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setAttribute(Qt.WA_StyledBackground, True)
        font = QFont(self.font())
        font.setPointSizeF(C.FONT_M)
        self.setFont(font)
        self.setStyleSheet(theme.editor_style())
        self.back = None  # 自己画外框时左上角的「返回」（side_tabs 排法里建）
        self.search_icon = None  # 搜索框左端的放大镜图标（按皮肤取色；换皮肤时重设）
        self.build_layout()
        chrome.frameless(self, self.back is not None, self.FLAGS, resizable=True)
        self.resize(L.WINDOW_W, L.WINDOW_H)
        self.set_hero(hero)
        self.subscribe_skin()

    FLAGS = Qt.Window  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：窗口的字号和样式表按新皮肤重设；左边方案列表的分组标题换色（选中的照旧），规则提示的
        底板、游戏内风格的警告和导航栏上说明的底色按新皮肤重设，筹码列表按新皮肤重画（尺寸变了时重排，见
        SchemeList.apply_skin），每一行的行尾颜色按新皮肤重写（SchemeList.restyle）。名称、作者、说明还没存的照旧在输入框里
        （不重读方案）。窗口里已有的部件按新的字体重设（_follow_font）。排法变了的窗口要重建（悬浮窗做：layout_key 和
        layout_name() 不同时关掉重开）。"""
        font = QFont(QApplication.font(self))  # 同建窗口时：程序的字体，字号按皮肤
        font.setPointSizeF(C.FONT_M)
        if font != self.font():  # 字体没变时不重设（重设会让筹码列表重排，打断正在拖的那一行）
            self.setFont(font)
        self.setStyleSheet(theme.editor_style())
        self._follow_font()
        self._recolor_list_header()
        self.hints_box.apply_plate()
        if self.layout_key != "default":  # 自己画的底板、警告按新皮肤的颜色重设
            if self.layout_key == "side_tabs":
                chrome.rail_backed(self.hero_hint)
            for label in self.findChildren(chrome.WarnLabel):
                label.apply_skin()
        self._update_search_icon()  # 搜索框左端的放大镜图标按新皮肤取色（排法不变时窗口不重建）
        self.chip_list.apply_skin()
        self.chip_list.restyle(content(self))

    def _follow_font(self) -> None:
        """窗口里已有的部件改用窗口现在的字体（换皮肤时，重新套样式表以后调用）。新建窗口时部件从窗口继承字体（正文字号
        C.FONT_M）；窗口套了样式表以后，Qt 不再把窗口后来设的字体传给已有的部件，重新套样式表时还会按程序的默认字体
        重新补全它们的字体——换了正文字号或程序字号的皮肤里就和新开的窗口不一样，所以逐个明确设成窗口的字体（不论现在
        是否相同）。样式表里另写了字号的（暗色说明、小按钮等）照样式表。筹码列表（连同放在它里面的部件）和软锁的问话不设：
        新开的窗口里它们也不跟窗口的字体（列表的字号由皮肤直接给，ListLook.FONT_ROW 等；问话放在列表里），设了列表还会
        重排、打断正在拖的那一行。"""
        font = self.font()
        skip = (self.chip_list, self.question)
        for widget in self.findChildren(QWidget):
            if not any(widget is s or s.isAncestorOf(widget) for s in skip):
                widget.setFont(font)

    @staticmethod
    def _limit_field(edit: QLineEdit, limit: int) -> None:
        """输入框按字数限长（一个表情算一个字，同方案数据层的算法）。"""
        edit.setValidator(_CharLimit(limit, edit))

    def _make_search_icon(self) -> QIcon | None:
        """搜索框左端的放大镜图标，按当前的皮肤取色（换皮肤时也要换色）。"""
        from ..theme import CHROME as K

        if self.layout_key == "side_tabs":
            color = K.INPUT_TEXT
        else:
            color = K.SEARCH_GLYPH or (K.INPUT_TEXT if K.HEAD == "band" else K.STAR_FILL)
        return _glyph_icon(ICON_SEARCH, color)

    def _set_search_icon(self) -> None:
        icon = self._make_search_icon()
        self.search_icon = self.search_edit.addAction(icon, QLineEdit.LeadingPosition) if icon is not None else None

    def _update_search_icon(self) -> None:
        icon = self._make_search_icon()
        if self.search_icon is not None and icon is not None:
            self.search_icon.setIcon(icon)

    # ---------------------------------------------------------------- 造部件和接线
    # 七种排法共用这一组：照排法的 _Kit 造部件、接信号；摆放在 layouts.py 的各个 build_*。属性名不变（别的地方按名字找部件）。

    def _make_hero_box(self) -> QComboBox:
        """角色下拉框（按六类排、每一类前面一行不能选的组名，下拉列表每一行和悬浮窗同一种画法），选中时换编辑的角色。"""
        box = QComboBox()
        box.setCursor(Qt.PointingHandCursor)
        fill_hero_combo(box, self.db.heroes.values())
        draw_combo_rows(box)
        box.activated.connect(self._hero_chosen)
        return box

    def _make_fields(self, kit: _Kit) -> None:
        """名称、作者、说明三个输入框（统一按字数限长），和名称一行的说明、复制来源、目标流派的说明、适用地图；三个框一次接好
        输入信号，适用地图的下拉多选接好勾选信号。"""
        self.name_edit = QLineEdit()
        if kit.fields_named:
            self.name_edit.setObjectName("nameEdit")
        self._limit_field(self.name_edit, NAME_MAX)
        self.author_edit = QLineEdit()
        self._limit_field(self.author_edit, AUTHOR_MAX)
        self.author_edit.setPlaceholderText(AUTHOR_PLACEHOLDER)
        self.note_edit = QLineEdit()
        self._limit_field(self.note_edit, NOTE_MAX)
        self.name_hint = _plain_label(NAME_HINT, "dim")
        self.copied_label = kit.dim("", "dim")
        self.copied_label.setWordWrap(True)
        self.target_note = kit.dim("", "dim")
        self.target_note.setWordWrap(True)
        self.maps_box = MapField(taggable_maps(self.db), display=lambda name: names.map_in(self.db, name))
        self.maps_box.toggled.connect(self._map_toggled)
        for edit in (self.name_edit, self.author_edit, self.note_edit):
            edit.textEdited.connect(self._text_edited)
            edit.editingFinished.connect(self._text_finished)

    def _make_actions(self, kit: _Kit) -> None:
        """新建、复制、复制为我的方案、删除四个按钮，一次接好信号。"""
        self.new_btn, self.copy_btn, self.copy_default_btn, self.delete_btn = kit.buttons()
        self.new_btn.clicked.connect(self._new)
        self.copy_btn.clicked.connect(self._copy)
        self.copy_default_btn.clicked.connect(self._copy_default)
        self.delete_btn.clicked.connect(self._ask_delete)
        for btn in (self.new_btn, self.copy_btn, self.copy_default_btn, self.delete_btn):
            btn.setFocusPolicy(Qt.TabFocus)

    def _make_confirm(self, kit: _Kit) -> None:
        """删除的确认：说明和「删除」「取消」两个按钮，一次接好信号。"""
        self.confirm_label = kit.confirm_label()
        self.confirm_yes, self.confirm_no = kit.confirm_buttons()
        self.confirm_yes.clicked.connect(self._confirm_delete)
        self.confirm_no.clicked.connect(self._cancel_delete)

    def _make_search(self, kit: _Kit) -> QWidget:
        """搜索行：搜索框（游戏内风格的排法左端有放大镜）和「只看已标记」。"""
        row, lay = _row(kit.search_gap)
        self.search_row = row
        self.search_edit = QLineEdit()
        if kit.search_named:
            self.search_edit.setObjectName("search")
        self.search_edit.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search_edit.setClearButtonEnabled(True)
        if kit.search_named:
            self._set_search_icon()
        self.search_edit.textChanged.connect(self._filter_changed)
        lay.addWidget(self.search_edit, 1)
        self.only_marked = QCheckBox(ONLY_MARKED)
        if kit.search_named:
            self.only_marked.setObjectName("onlyMarked")
        self.only_marked.setCursor(Qt.PointingHandCursor)
        self.only_marked.toggled.connect(self._filter_changed)
        lay.addWidget(self.only_marked)
        return row

    def _make_stale(self, kit: _Kit) -> QWidget:
        """对不上的筹码一行：说明和「移除」。"""
        row, lay = _row(8)
        self.stale_row = row
        self.stale_label = kit.warn("", "warn")
        self.stale_label.setWordWrap(True)
        self.stale_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.stale_label, 1)
        self.stale_remove = _button(STALE_REMOVE, "small")
        self.stale_remove.clicked.connect(self._remove_stale)
        lay.addWidget(self.stale_remove)
        return row

    def _make_list_area(self, kit: _Kit) -> None:
        """列表上方的说明、筹码列表（只有它上下滚动）、软锁的问话（LockQuestion，反复用同一个）、软锁提示和规则提示。"""
        self.list_note = kit.dim(LIST_NOTE, "dim")
        self.list_note.setWordWrap(True)
        self.chip_list = SchemeList(self.db)
        self.chip_list.setMinimumHeight(MIN_LIST_HEIGHT)
        self.chip_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.chip_list.tier_clicked.connect(self._tier_clicked)
        self.chip_list.row_moved.connect(self._row_moved)
        # 选中默认推荐、又没有固定档位时筹码列表空着：在空框里写下一步怎么做（_show_selected 定显示与否）
        self.empty_guide = kit.dim(DEFAULT_EMPTY_GUIDE, "dim")
        self.empty_guide.setWordWrap(True)
        self.empty_guide.hide()
        self.chip_list.set_note(self.empty_guide)
        self.question = LockQuestion(self)
        self.question.answered.connect(self._lock_answered)
        self.lock_label = kit.warn("", "warn")
        self.lock_label.setWordWrap(True)
        self.hints_box = kit.hints()
        self.hints_label = self.hints_box.label

    def _make_saved_and_close(self, bottom: QWidget, lay: QHBoxLayout, kit: _Kit) -> None:
        """最下面一行右边：「已保存」（按排法决定出现、消失时是否占住位置）和关闭按钮。自己画外框的排法（×/「返回」就是
        关闭窗口）不另放「关闭」，close_btn 就是它。"""
        self.saved_label = _plain_label(SAVED, "saved")
        if kit.retain_saved:  # 「已保存」出现、消失时这一行不变高矮，上面的列表不跳
            keep = self.saved_label.sizePolicy()
            keep.setRetainSizeWhenHidden(True)
            self.saved_label.setSizePolicy(keep)
        self.saved_label.hide()
        lay.addWidget(self.saved_label)
        if self.back is not None:  # 自己画外框：不另放「关闭」，「已保存」写在原来按钮的位置（最右边）
            self.close_btn = self.back
            bottom.setMinimumHeight(self.confirm_yes.minimumHeight())  # 删除的确认出现时这一行也不变高
        else:
            self.close_btn = kit.close_button()
            self.close_btn.setFocusPolicy(Qt.TabFocus)
            self.close_btn.clicked.connect(self.close)
            lay.addWidget(self.close_btn)
    # ---------------------------------------------------------------- 对外

    def set_hero(self, hero: str | None) -> None:
        """换成编辑 hero 的方案（悬浮窗打开窗口时用当前角色调用）。资料库没有这个角色时不换（还没有角色时取第一个）。
        选中该角色正在用的方案，没有时选中默认推荐。"""
        if hero not in self.db.heroes:
            if self.hero:
                return
            hero = first_data(self.hero_box)  # 第一个角色（跳过分组标题）
            if hero is None:
                return
        if hero == self.hero:
            return
        self._flush()
        self.hero = hero
        index = self.hero_box.findData(hero)
        if index >= 0 and index != self.hero_box.currentIndex():
            self.hero_box.setCurrentIndex(index)
        self._default_marks = default_marks(self.db, hero)
        self._ends = default_ends(self.db, hero)
        self._notes = pool_notes(self.db, hero)
        current = self.store.current(hero)
        self._reload_list(current.id if current is not None else "")

    def refresh(self) -> None:
        """按方案存储的现状重新显示（窗口重新打开、别的窗口导入了方案时）：选中的方案还在就仍选中它，否则选中默认推荐。"""
        self._flush()
        self._reload_list(self.scheme.id if self.scheme is not None else "")

    def selected_id(self) -> str:
        """选中的方案 id；选中默认推荐时为空字符串。"""
        return self.scheme.id if self.scheme is not None else ""

    def select(self, scheme_id: str) -> None:
        """选中一套方案（空字符串为默认推荐）。"""
        for i in range(self.scheme_list.count()):
            item = self.scheme_list.item(i)
            if item.data(_ID_ROLE) == scheme_id:
                self.scheme_list.setCurrentItem(item)
                return

    def state(self) -> dict:
        """交出的状态：编辑的角色、选中的方案（默认推荐为空字符串）、搜索文字、只看已标记、筹码列表各组的收起展开和滚动
        位置，加上窗口的位置。换皮肤重开窗口时靠它保住编辑对象和输入。名称、作者、说明没存的输入不在这里：关窗口时照常
        存进方案文件，重开的窗口从方案文件读出来。"""
        s = super().state()
        s.update(hero=self.hero, scheme=self.selected_id(), search=self.search_edit.text(),
                 only_marked=self.only_marked.isChecked(), folds=self.chip_list.folds(),
                 scroll=self.chip_list.verticalScrollBar().value())
        return s

    def restore(self, state) -> None:
        """接回 state() 交出的状态：换回角色和方案（选中的是默认推荐时也停在默认推荐）、恢复搜索和勾选、各组的收起展开、
        回到原来的滚动位置，窗口位置按屏幕挪回来。方案在换皮肤期间被删掉时停在新开窗口时选的那一套
        （该角色正在用的，没有时默认推荐），不报错。接回时不改方案、不发 schemes_changed。"""
        if not state:
            return
        self._placed = True  # 显示时不再居中放置（showEvent 走「关了再打开」那一支，按屏幕把窗口挪回来）
        super().restore(state)
        hero = state.get("hero")
        if hero in self.db.heroes:
            self.set_hero(hero)
            if "scheme" in state:
                self.select(state["scheme"])  # 找不到（删掉了）时不动
        self.search_edit.setText(state.get("search", ""))
        self.only_marked.setChecked(bool(state.get("only_marked")))
        # 收起展开放在选回方案、接回搜索和勾选以后（前面几步都会重设列表内容），滚动位置之前（按收起以后的排版算）
        self.chip_list.set_folds(state.get("folds"))
        # 滚动位置等列表排好版以后再放：刚显示时筹码列表的高度还没算出来，这时设会被夹回 0
        value = int(state.get("scroll", 0))
        QTimer.singleShot(0, self, lambda v=value: self.chip_list.verticalScrollBar().setValue(v))

    def keep_on_screen(self) -> None:
        """接回窗口位置时用：大小照用户上次调的，放不下时缩小，并挪进可用区域（同关了再打开时的 _keep_on_screen）。"""
        self._keep_on_screen()

    def fit_to_screen(self, available: QRect | None = None, center: bool = False) -> None:
        """按屏幕可用区域定窗口大小：宽 WINDOW_W、高 WINDOW_H，放不下时缩小到可用区域（含标题栏），筹码列表随之变短，
        其余部分不变；再把窗口挪进可用区域（center 为 True 时放在正中）。available 不给时取悬浮窗或本窗口所在屏幕的可用区域。"""
        area = available if available is not None else self._available_area()
        if area is None or area.isEmpty():
            return
        frame = self.frameGeometry().height() - self.geometry().height() if self.isVisible() else 0
        if frame <= 0:
            frame = TITLE_ALLOWANCE if available is None and self.back is None else 0
        minimum = self.minimumSizeHint()
        width = max(minimum.width(), min(L.WINDOW_W, area.width()))
        height = max(minimum.height(), min(L.WINDOW_H, area.height() - frame))
        self.resize(width, height)
        x, y = self.x(), self.y()
        if center:
            x = area.x() + (area.width() - width) // 2
            y = area.y() + max(0, (area.height() - height - frame) // 2)
        x = max(area.left(), min(x, area.left() + area.width() - width))
        y = max(area.top(), min(y, area.top() + area.height() - height - frame))
        self.move(x, y)

    # ---------------------------------------------------------------- 方案列表

    def _reload_list(self, select_id: str) -> None:
        """重建左边的方案列表（默认推荐、我的方案），选中 select_id（不存在时选中默认推荐）；悬浮窗对这个角色正在用的那一套
        标「使用中」（_mark_used）。"""
        self._loading = True
        try:
            self.scheme_list.clear()
            item = QListWidgetItem(DEFAULT_ROW)
            item.setData(_ID_ROLE, "")
            item.setData(_TAG_ROLE, DEFAULT_TAG)
            self.scheme_list.addItem(item)
            target = item
            mine = self.store.schemes(self.hero)
            if mine:
                header = QListWidgetItem(MINE_HEADER)
                header.setFlags(Qt.NoItemFlags)
                header.setData(_ID_ROLE, None)
                header.setForeground(QColor(L.LIST_HEADER_COLOR))
                self.scheme_list.addItem(header)
            for scheme in mine:
                # 列表上的方案名显示时按界面语言转；编辑框里照旧是原文（存的就是它）
                item = QListWidgetItem(lang.t(scheme.name))
                item.setData(_ID_ROLE, scheme.id)
                item.setData(_TAG_ROLE, MINE_TAG)
                item.setToolTip(self._maps_tip(scheme))
                self.scheme_list.addItem(item)
                if scheme.id == select_id:
                    target = item
            self._mark_used()
            self.scheme_list.setCurrentItem(target)
        finally:
            self._loading = False
        self._show_selected()

    def _maps_tip(self, scheme: Scheme) -> str:
        """方案列表里这一行的悬停说明：标了地图的写「适用地图：……」，通用的不写（空字符串）。"""
        return LIST_MAPS.format(maps=LIST_SEP.join(names.map_in(self.db, m) for m in scheme.maps)) \
            if scheme.maps else ""

    def used_id(self) -> str:
        """悬浮窗对正在编辑的角色正在用的方案 id（SchemeStore.current）；用默认推荐时为空字符串。"""
        current = self.store.current(self.hero) if self.hero else None
        return current.id if current is not None else ""

    def _mark_used(self) -> bool:
        """左边列表里悬浮窗正在用的那一套（used_id）行尾写「使用中」（USED_TAG 代替「默认」「我的」，_USED_ROLE 为真时
        按已生效的颜色画，见 scheme_cards），其余的照旧写「默认」「我的」。分组标题不动。返回有没有改动。"""
        used = self.used_id()
        changed = False
        for i in range(self.scheme_list.count()):
            item = self.scheme_list.item(i)
            scheme_id = item.data(_ID_ROLE)
            if scheme_id is None:
                continue
            on = scheme_id == used
            tag = USED_TAG if on else (MINE_TAG if scheme_id else DEFAULT_TAG)
            if bool(item.data(_USED_ROLE)) != on or item.data(_TAG_ROLE) != tag:
                item.setData(_USED_ROLE, on)
                item.setData(_TAG_ROLE, tag)
                changed = True
        return changed

    def show_used(self) -> None:
        """悬浮窗换了方案（方案下拉框、新建复制以后、导入后「使用这套方案」）：按方案存储的现状重标「使用中」，不重建列表
        （选中的方案、没存的输入、滚动位置都不动）。行宽跟着字变的画法（一排页签）重新排一次。"""
        if self._mark_used():
            self.scheme_list.doItemsLayout()
            self.scheme_list.viewport().update()

    def _recolor_list_header(self) -> None:
        """方案列表里的分组标题按当前皮肤的颜色重设（换皮肤时；不重排列表，选中的方案和没存的输入都不动）。"""
        for i in range(self.scheme_list.count()):
            item = self.scheme_list.item(i)
            if item.data(_ID_ROLE) is None:
                item.setForeground(QColor(L.LIST_HEADER_COLOR))
        self.scheme_list.viewport().update()

    def _list_changed(self, current, previous) -> None:
        if self._loading:
            return
        if current is None or current.data(_ID_ROLE) is None:
            return
        self._flush()
        self._show_selected()

    def _list_item(self, scheme_id: str) -> QListWidgetItem | None:
        for i in range(self.scheme_list.count()):
            item = self.scheme_list.item(i)
            if item.data(_ID_ROLE) == scheme_id:
                return item
        return None

    # ---------------------------------------------------------------- 右边的显示

    def _show_selected(self) -> None:
        """按列表里选中的一行显示右边：默认推荐（只读）或我的方案（编辑区）。"""
        item = self.scheme_list.currentItem()
        scheme_id = item.data(_ID_ROLE) if item is not None else ""
        self.scheme = self.store.get(scheme_id) if scheme_id else None
        if self.scheme is not None and self.scheme.hero != self.hero:
            self.scheme = None
        self._cancel_question()
        self._dirty_fields.clear()
        self._text_timer.stop()
        self.confirm_row.hide()
        self.action_row.show()
        # 换回一行的「已保存」再藏起来：藏起来时照样占着位置，写不进时那句折成两行的高度不留到换了方案以后
        self._set_saved(SAVED, warn=False)
        self.saved_label.hide()
        read_only = bool(self.store.read_only)
        self.read_only_label.setVisible(read_only)
        mine = self.scheme is not None
        self.mine_box.setVisible(mine)
        self.default_box.setVisible(not mine)
        self.search_row.setVisible(mine)
        self.list_note.setVisible(mine)
        self.copy_btn.setVisible(mine)
        self.delete_btn.setVisible(mine)
        self.copy_default_btn.setVisible(not mine and bool(self._default_marks))
        self.new_btn.setToolTip("")  # 选中默认推荐、又没有固定档位时另写（_show_copy_default）
        for btn in (self.new_btn, self.copy_btn, self.delete_btn, self.copy_default_btn):
            btn.setEnabled(not read_only)
        if mine:
            self._show_fields()
        else:
            schools = default_schools(self.db, self.hero)
            self.default_schools.setText(
                DEFAULT_SCHOOLS.format(schools=LIST_SEP.join(lang.t(s) for s in schools))
                if schools else DEFAULT_NO_SCHOOLS)
            self.no_tiers_label.setVisible(not self._default_marks)
            self._show_copy_default()
        # 默认推荐没有固定档位时筹码列表一行都没有：空框里写下一步怎么做（新建空方案、标档位、悬浮窗直接改用）
        self.empty_guide.setVisible(not mine and not self._default_marks)
        self._show_list()
        self._apply_filter()
        self._show_stale()
        self._show_hints()
        self.chip_list.scroll_to_top()

    def _show_fields(self) -> None:
        """把选中方案的名称、作者、说明、适用地图、复制来源和目标流派写进编辑区（不算用户的改动）。"""
        s = self.scheme
        read_only = bool(self.store.read_only)
        for edit, text in ((self.name_edit, s.name), (self.author_edit, s.author), (self.note_edit, s.note)):
            edit.setText(text)
            edit.setReadOnly(read_only)
            edit.setCursorPosition(0)
        self.maps_box.set_maps(s.maps, keep_extra=False)  # 上一套补的结束地图不带到这一套
        self.maps_box.set_enabled(not read_only)
        note = copied_note(self.db, s)
        self.copied_label.setText(note)
        self.copied_label.setVisible(bool(note))
        self._show_targets()

    def _show_targets(self) -> None:
        """目标流派一栏和它下面的说明（按当前标记推出来）。"""
        tiers = self.scheme.tiers if self.scheme is not None else Tiers()
        self.targets.set_schools(target_schools(self.db, tiers))
        note = target_note(self.db, tiers)
        self.target_note.setText(note)
        self.target_note.setVisible(bool(note))

    def _tiers(self) -> Tiers:
        """列表里显示的档位和顺位：我的方案的，或默认推荐的。"""
        return self.scheme.tiers if self.scheme is not None else self._default_marks

    def _show_stale(self) -> None:
        stale = self.scheme.stale if self.scheme is not None else ()
        self.stale_row.setVisible(bool(stale))
        if stale:
            shown = LIST_SEP.join(dict.fromkeys(names.chip_in(self.db, x.name) for x in stale))
            self.stale_label.setText(STALE_NOTE.format(n=len(stale), names=shown))
            self.stale_remove.setEnabled(not self.store.read_only)

    def _show_hints(self) -> None:
        """列表下面的橙色软锁提示和灰色规则提示（只对我的方案）。"""
        mine = self.scheme is not None
        notice = lock_notice(self.db, self.catalog, self.scheme.tiers) if mine else ""
        self.lock_label.setText(notice)
        self.lock_label.setVisible(bool(notice))
        self.hints_box.set_lines(rule_hints(self.db, self.scheme) if mine else [])

    def _filter_changed(self, *_) -> None:
        """改了搜索文字或「只看已标记」：重新筛选，列表回到最上面。"""
        self._cancel_question()
        self._apply_filter()
        self.chip_list.scroll_to_top()

    def _matched(self) -> set[str] | None:
        """按搜索（筹码名或效果，不分大小写）和「只看已标记」符合的筹码；没有筛选时为 None。只对我的方案。"""
        if self.scheme is None:
            return None
        query = self.search_edit.text().strip().casefold()
        only = self.only_marked.isChecked()
        if not query and not only:
            return None
        tiers = self.scheme.tiers
        out = set()
        for name in self.catalog.markable:
            if only and name not in tiers:
                continue
            chip = self.db.chips[name]
            if query and query not in name.casefold() and query not in chip.hant.casefold() \
                    and query not in chip.effect.casefold():
                continue
            out.add(name)
        return out

    def _apply_filter(self) -> None:
        self.chip_list.set_filter(self._matched())

    # ---------------------------------------------------------------- 修改（改动即存）

    def _commit(self, **changes) -> bool:
        """把改动存进方案存储（名称、作者、说明有没存的输入时一起存），返回内容是否变了；变了时发 schemes_changed。
        档位、顺位变了时列表按新内容重排（改了档位的行飞进新的块）。"""
        base = self.scheme
        if base is None or self.store.read_only:
            return False
        if self._dirty_fields:
            for key, edit in self._text_edits():
                if edit in self._dirty_fields:  # 没改过的框不回写：按显示的字数存不下时也不会把内容截短
                    changes.setdefault(key, edit.text())
            self._dirty_fields.clear()
            self._text_timer.stop()
        try:
            wanted = replace(base, **changes)
        except ValueError:
            return False
        if wanted == base:
            self.maps_box.set_maps(base.maps)  # 勾了、取消了一张地图，整理后回到原样：下拉框跟着恢复
            self._show_saved()
            return False
        try:
            stored = self.store.update(wanted)
        except SchemesReadOnly:
            self._show_selected()
            return False
        except ValueError:  # 这套方案已不在方案存储里（别的窗口删了）
            self.refresh()
            return False
        self.scheme = stored
        changed = stored != base
        if stored.name != base.name:
            item = self._list_item(stored.id)
            if item is not None:
                item.setText(lang.t(stored.name))
            if not self.name_edit.hasFocus():
                self.name_edit.setText(stored.name)
        if stored.tiers != base.tiers:
            self._show_list(animate=True)
            self._apply_filter()
            self._show_targets()
        if stored.maps != base.maps:
            self.maps_box.set_maps(stored.maps)
            item = self._list_item(stored.id)
            if item is not None:
                item.setToolTip(self._maps_tip(stored))
        if stored.stale != base.stale:
            self._show_stale()
        self._show_hints()
        self._show_saved()
        if changed:
            self.schemes_changed.emit(stored.hero)
        return changed

    def _show_saved(self) -> None:
        """改动存好后右下角写「已保存」；现在的内容不在方案文件里时（写不进文件，见 SchemeStore.saved）在同一个位置用警告色
        写写不进的说明（SAVE_FAILED）；方案文件只读时不写（窗口里另有只读的说明）。"""
        if self.store.read_only:
            self.saved_label.hide()
            return
        if self.store.saved:
            self._set_saved(SAVED, warn=False)
        else:
            self._set_saved(SAVE_FAILED, warn=True)
        self.saved_label.show()

    def _set_saved(self, text: str, *, warn: bool) -> None:
        """右下角那行提示改成 text：warn 时用警告色（样式表 #warn）并允许折行，普通时用显示成功的那一种（#saved）。"""
        self.saved_label.setText(text)
        self.saved_label.setWordWrap(warn)
        name = "warn" if warn else "saved"
        if self.saved_label.objectName() != name:
            self.saved_label.setObjectName(name)
            self.saved_label.style().unpolish(self.saved_label)
            self.saved_label.style().polish(self.saved_label)
        self.saved_label.updateGeometry()

    def _text_edits(self) -> tuple[tuple[str, QLineEdit], ...]:
        """三个文字框和它们在方案里的字段名。"""
        return (("name", self.name_edit), ("author", self.author_edit), ("note", self.note_edit))

    def _flush(self) -> None:
        """名称、作者、说明还有没存的输入时立即存（换方案、换角色、关窗口前）。"""
        if self._dirty_fields:
            self._commit()

    def _text_edited(self, *_) -> None:
        if self.scheme is None or self.store.read_only:
            return
        edit = self.sender()  # 哪个框改过就只记哪个：存的时候只回写改过的框，别的框照旧
        if isinstance(edit, QLineEdit):
            self._dirty_fields.add(edit)
        self.saved_label.hide()
        self._text_timer.start()

    def _commit_text(self) -> None:
        if self._dirty_fields:
            self._commit()

    def _text_finished(self) -> None:
        """离开输入框或按回车：存，再把输入框换成存进去的写法（重名时加了「（2）」、为空时「未命名方案」等）。"""
        self._commit_text()
        if self.scheme is not None:
            for key, edit in self._text_edits():
                text = getattr(self.scheme, key)
                if edit.text() != text:
                    edit.setText(text)

    def _map_toggled(self, name: str, on: bool) -> None:
        """在适用地图的下拉框里勾了、取消了一张地图：标上或去掉这张地图（用 normalize_maps 整理成 Scheme.maps 的样子），
        改动即存。"""
        if self.scheme is None or self.store.read_only:
            return
        wanted = set(self.scheme.maps)
        if on:
            wanted.add(name)
        else:
            wanted.discard(name)
        self._commit(maps=normalize_maps(self.db, wanted))

    def _tier_clicked(self, name: str, tier: str) -> None:
        """点档位按钮：标成这一档（新标的、改了档位的排到那一块最后），已经是这一档时取消。被锁流派的筹码点必选、推荐、
        可选时先在那一行下面问要不要把这个流派列进方案（_lock_answered 再改）；被锁的行点不选不变（本来就按不选计算）。
        列表自己不改按钮，没改成时不用重画。"""
        self._cancel_question()
        if self.scheme is None or self.store.read_only:
            return
        tiers = self.scheme.tiers
        current = tiers.get(name)
        if current == tier:
            wanted = tiers.with_tier(name, None)
        else:
            school = school_of(self.db, name)
            if school is not None and school in locked_schools(self.db, tiers):
                if tier in LISTED_TIERS:
                    self._ask_lock(name, tier, school)
                    return
                if current is None:
                    return
            wanted = tiers.with_tier(name, tier)
        self._commit(tiers=wanted)

    def _ask_lock(self, name: str, tier: str, school: str) -> None:
        """在那一行下面问要不要把被锁的流派列进方案（那一行不在列表里时不问）。"""
        if self.chip_list.row_spec(name) is None:
            return
        self.question.ask(name, tier, school, max_schools(self.db))
        self.chip_list.set_inline(name, self.question)
        QTimer.singleShot(0, self, self._reveal_question)  # 带接收对象：窗口关掉后不再回调

    def _reveal_question(self) -> None:
        """问话放进列表、排好位置以后滚到它整个露出来（那一行在列表下沿时问话会在画面外）。"""
        if self.question.chip and self.question.isVisible():
            self.chip_list.ensureWidgetVisible(self.question, 0, 4)

    def _cancel_question(self) -> None:
        """收起软锁的问话（不改）：换方案、换角色、筛选、关窗口、改了别的档位、按 Esc 时。"""
        if not self.question.chip:
            return
        self.question.clear()
        self.chip_list.set_inline(None, None)
        self.question.setParent(self)  # 列表拿掉以后父控件为空：放回窗口里藏着，免得成了单独的窗口
        self.question.hide()

    def _lock_answered(self, yes: bool) -> None:
        """「列进方案」：那一下点的档位生效，这个流派随之列进方案，它其余的筹码回到未标记；「取消」：不变。"""
        name, tier = self.question.chip, self.question.tier
        self._cancel_question()
        if not yes or not name or self.scheme is None or self.store.read_only:
            return
        self._commit(tiers=self.scheme.tiers.with_tier(name, tier))

    def _row_moved(self, name: str, tier: str, rank: int) -> None:
        """拖动改顺位：name 拖到 tier 这一块的第 rank 位（只在本块里拖；rank 按整块的先后算）。列表已经把这一行摆在
        松手的位置，存不进去时照原样的内容重画，这一行滑回去。"""
        self._cancel_question()
        if self.scheme is None or self.store.read_only or self.scheme.tiers.get(name) != tier:
            self._show_list(animate=True)
            return
        if not self._commit(tiers=self.scheme.tiers.moved(name, tier, rank)):
            self._show_list(animate=True)

    def _remove_stale(self) -> None:
        """「移除」：去掉资料更新后对不上的筹码。"""
        self._commit(stale=())

    def _add(self, scheme: Scheme) -> None:
        """新建一套我的方案（新建空方案、复制、复制为我的方案），选中它，光标放进名称框。发 schemes_changed，再发 scheme_added
        （悬浮窗当前的角色就是它所属的角色时，悬浮窗当场换用它，列表里的「使用中」跟着标到它上面）。"""
        self._flush()
        try:
            added = self.store.add(scheme)
        except SchemesReadOnly:
            self._show_selected()
            return
        except ValueError:
            return
        self.schemes_changed.emit(added.hero)
        self._reload_list(added.id)
        self._show_saved()
        self.scheme_added.emit(added.hero, added.id)
        self.name_edit.setFocus(Qt.OtherFocusReason)
        self.name_edit.selectAll()

    def _new(self) -> None:
        self._add(Scheme(hero=self.hero, name=new_name(s.name for s in self.store.schemes(self.hero))))

    def _copy(self) -> None:
        if self.scheme is None:
            return
        self._flush()
        self._add(replace(self.scheme, id="", name=copy_name(self.scheme.name)))

    def _show_copy_default(self) -> None:
        """选中默认推荐时：这个角色的默认推荐有固定档位时另有「复制为我的方案」（照原样复制，_show_selected 定显示不显示）；
        没有时没有可复制的，只有「新建空方案」，它的悬停写明新建的不是默认推荐的副本。只读说明跟着换。"""
        empty = not self._default_marks
        self.new_btn.setToolTip(tip_paragraphs_html(NEW_EMPTY_TIP) if empty else "")
        self.default_read_only.setText(DEFAULT_READ_ONLY_EMPTY if empty else DEFAULT_READ_ONLY)

    def _copy_default(self) -> None:
        """「复制为我的方案」照原样复制默认推荐。这个角色的默认推荐没有固定档位时按钮不显示，万一点到也什么都不做（新建空白
        方案用「新建空方案」）。"""
        if not self._default_marks:
            return
        self._add(scheme_from_default(self.db, self.hero))

    def _ask_delete(self) -> None:
        """「删除」：在窗口底部确认（不另弹窗口）。"""
        if self.scheme is None or self.store.read_only:
            return
        self._flush()
        self._cancel_question()
        self.confirm_label.setText(DELETE_CONFIRM.format(name=lang.t(self.scheme.name)))
        self.action_row.hide()
        self.confirm_row.show()

    def _cancel_delete(self) -> None:
        self.confirm_row.hide()
        self.action_row.show()

    def _confirm_delete(self) -> None:
        scheme = self.scheme
        self._cancel_delete()
        if scheme is None or self.store.read_only:
            return
        ids = [self.scheme_list.item(i).data(_ID_ROLE) for i in range(self.scheme_list.count())]
        mine = [i for i in ids if i]
        index = mine.index(scheme.id) if scheme.id in mine else -1
        try:
            self.store.delete(scheme.id)
        except SchemesReadOnly:
            self._show_selected()
            return
        except ValueError:
            self.refresh()
            return
        self.scheme = None
        rest = [i for i in mine if i != scheme.id]
        # 选中列表里它上面的一套；它是第一套时选中下面的，没有了时选中默认推荐
        after = rest[index - 1] if index > 0 else (rest[0] if rest else "")
        self.schemes_changed.emit(scheme.hero)
        self._reload_list(after)
        self._show_saved()

    def _hero_chosen(self, index: int) -> None:
        self.set_hero(self.hero_box.itemData(index))

    # ---------------------------------------------------------------- 筹码列表（scheme_list.SchemeList）

    def _show_list(self, animate: bool = False) -> None:
        """把列表的内容整份交给 SchemeList。animate：用户改了档位、顺位、列进方案以后为 True（改了档位的行飞进新的块，
        上锁、解锁时整组挪动）；换方案、换角色、外部刷新时为 False（直接换）。"""
        self.chip_list.set_content(content(self), animate=animate)

    def _content(self) -> list[BlockSpec]:
        """列表的内容（分块、分组、行尾和悬停见 notes.content）：窗口上同名转发一份，测试按它核对。"""
        return content(self)

    # ---------------------------------------------------------------- 窗口

    def _available_area(self) -> QRect | None:
        parent = self.parentWidget()
        screen = parent.window().screen() if parent is not None else None
        screen = screen or self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _keep_on_screen(self) -> None:
        """重新打开时：大小照用户上次调的，只在放不下可用区域时缩小，并挪进可用区域。"""
        area = self._available_area()
        if area is None or area.isEmpty():
            return
        frame = self.frameGeometry().height() - self.geometry().height()
        frame = frame if frame > 0 else (TITLE_ALLOWANCE if self.back is None else 0)
        height = max(self.minimumSizeHint().height(), min(self.height(), area.height() - frame))
        width = max(self.minimumSizeHint().width(), min(self.width(), area.width()))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        x = max(area.left(), min(self.x(), area.left() + area.width() - width))
        y = max(area.top(), min(self.y(), area.top() + area.height() - height - frame))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():  # 从最小化还原等系统引起的显示：照旧
            return
        if not self._placed:
            self._placed = True
            self.fit_to_screen(center=True)
        else:  # 关了再打开：按方案存储的现状重新显示，窗口挪进屏幕
            self.refresh()
            self._keep_on_screen()

    def keyPressEvent(self, event) -> None:
        """Esc：软锁的问话开着时等于「取消」；没有问话时按基类的规矩（自己画外框时关闭窗口）。"""
        if event.key() == Qt.Key_Escape and self.question.chip:
            self._cancel_question()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._flush()
        self._cancel_delete()
        self._cancel_question()
        super().closeEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(L.WINDOW_W, L.WINDOW_H)

    # 七种排法的摆放写在 layouts.py（函数拿窗口做第一个参数）；这里按 LAYOUTS 里的方法名绑成方法。
    # build_layout() 用 getattr(self, LAYOUTS[排法名])() 调用，调用时第一个参数就是窗口，每种排法用自己那套部件样式。
    _build = build
    _build_tabs = build_tabs
    _build_card_row = build_card_row
    _build_card_column = build_card_column
    _build_side_column = build_side_column
    _build_tile_column = build_tile_column
    _build_tab_strip = build_tab_strip
