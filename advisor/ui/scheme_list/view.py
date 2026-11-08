"""筹码列表本体（SchemeList）：自己摆放每一行，拖动改顺位，改档位时那一行飞进新的块，键盘 Tab 一站一站走。
内容整份交进来，只负责摆放、拖动和动画，不改方案（点档位、拖动松手只发信号）。各项的样子在 items.py，
数据规格和尺寸、画法共用的小工具在 specs.py。"""
from __future__ import annotations

import math
import time
from dataclasses import replace
from functools import partial
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QWidget,
)

from ...data import TIER_LABEL, TIERS
from .. import lang, names, theme
from . import specs
from .items import _EdgeHint, _HeaderItem, _InlineHost, _Item, _RowItem
from .specs import (
    BOTTOM,
    FRAME_MS,
    TOP,
    BlockSpec,
    RowSpec,
    _clean_blocks,
    _Drag,
    _ease,
    _inline_margins,
    _Metrics,
    _Motion,
    _quoted,
    _school_sorted,
    _Snap,
    _structure,
    system_animations,
)
from ..theme import CANVAS_OBJECT, LIST_OBJECT
from ..theme import LIST as L

# ------------------------------------------------------------ 界面文字（列表自己出的提示；块、组、行的文字由窗口交进来）

# 档名是资料库的键（必选、推荐、可选、不选），照旧简体，显示处再转
HANT_KEEP = ("TIER_LABEL",)

HINT_TIER = "已移到{tier}第 {rank} 位"  # 改档位后要去的块不在画面里：tier 为档名（必选、推荐、可选、不选），rank 为顺位
HINT_UNMARKED = "已移到未标记的「{group}」组"  # 取消标记、回到未标记里的组（流派名、通用、地图专属）
HINT_OTHER = "已移到「其他流派」的「{group}」组"  # 取消标记、所属流派被锁：回到「其他流派」里的组
HINT_LOCKED = "{schools}的 {n} 个筹码已移到「其他流派」"  # 上锁：schools 为各流派名逐个加引号连写（「星光」「充能」）
HINT_UNLOCKED = "{schools}的筹码已移回未标记"  # 解锁

# ------------------------------------------------------------ 内容的键

BLOCK_UNMARKED = "unmarked"  # 未标记一块（按流派分组）
BLOCK_OTHER = "other"  # 「其他流派」一块：被锁成不选的流派（按流派分组）

# ------------------------------------------------------------ 动画和拖动

MOVE_MS = 250  # 改档位、收起展开、上锁解锁：约 0.25 秒，先快后慢
SHIFT_MS = 160  # 拖动时相邻的行滑开让位
SETTLE_MS = 160  # 松手（或 Esc）后被拖的行滑进空位
FLASH_MS = 600  # 落定后闪一下（不做动画时改成亮这么久）
FLY_MAX = 8  # 一次要飞的行超过这么多个时不飞、直接到位（一次动十几个的上锁、解锁走整组收起展开）
EDGE_ZONE = 28  # 拖到离列表上下沿这么近（或拖出列表）时自动滚动
EDGE_SPEED = 18  # 自动滚动每一帧最多滚多少像素：越往外越快，拖出边缘两个 EDGE_ZONE 时最快
SCROLL_MARGIN = 4  # scroll_to 时这一行和列表边缘之间留的空
NOTE_MARGIN = 14  # 盖在列表上的说明（set_note）和列表可见区域四边之间留的空
NO_ANIMATION_PLATFORMS = frozenset(("offscreen", "minimal"))  # 这些 Qt 平台上默认不做动画（测试、离屏画图）


class SchemeList(QScrollArea):
    """筹码列表（只有它上下滚动）。db 为资料库（取筹码的图标、稀有度、效果原文）。内容、方法、信号见模块说明和
    各方法的说明；改方案、存盘都由窗口做。"""

    tier_clicked = Signal(str, str)  # 筹码名, 档位：点了档位按钮（enabled 的行，locked 的行也发）
    row_moved = Signal(str, str, int)  # 筹码名, 块（档位键）, 顺位（从 1 起）：拖动松手，位置变了
    group_toggled = Signal(str, bool)  # 组名, 是否展开：用户点了组的收起按钮

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._blocks: tuple[BlockSpec, ...] = ()
        self._rows: dict[str, _RowItem] = {}
        self._headers: dict[tuple, _HeaderItem] = {}
        self._where: dict[str, tuple[str, str | None]] = {}  # 筹码名 → （块, 组）
        self._open: dict[str, bool] = {}  # 组名 → 展开着没有
        self._filter: frozenset[str] | None = None
        self._inline: tuple[str, _InlineHost] | None = None
        self._placed: list[tuple[_Item, float, int]] = []  # 显示中的各项（先后、排好的位置、高度）
        self._total = 0  # 排好以后的总高度
        self._school_w = 0
        self._metrics: _Metrics | None = None
        self._laid: tuple | None = None  # 现在的位置是按哪一组尺寸排的（_geometry，_compute 时记下；换皮肤时和新的比）
        self._animated: bool | None = None
        self._clock: Callable[[], float] = time.monotonic
        self._moving: set[_Item] = set()
        self._flashing: set[_Item] = set()
        self._lit: set[_Item] = set()  # 不做动画时亮着的（闪一下的代替）
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._lit_timer = QTimer(self)
        self._lit_timer.setSingleShot(True)
        self._lit_timer.timeout.connect(self._unlight)
        self._drag: _Drag | None = None
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(FRAME_MS)
        self._edge_timer.timeout.connect(self._edge_scroll)
        # 键盘：_cursor 是键盘焦点所在的地方（见 _stops）；_ring 为 True 时画焦点框（用键盘走过来的才画，鼠标点的不画）；
        # 放进列表的控件里能用 Tab 走到的子控件由列表管先后（_inline_focus，放着时设成不接 Tab，原来的设置记在 _inline_policies）
        self._cursor: tuple | None = None
        self._ring = False
        self._inline_focus: list[QWidget] = []
        self._inline_policies: dict = {}
        self._inline_return: tuple | None = None  # 焦点在放进来的控件里时，拿掉以后回到哪个按钮
        self.setObjectName(LIST_OBJECT)
        self.setStyleSheet(theme.list_style())
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas = QWidget()
        self.canvas.setObjectName(CANVAS_OBJECT)
        self.setWidget(self.canvas)
        self.canvas.setAutoFillBackground(False)  # setWidget 会打开自动填底色；列表的底色由外框画
        self._hints = {side: _EdgeHint(self, side) for side in (TOP, BOTTOM)}
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self._note: QWidget | None = None  # 盖在可见区域里的一块说明（set_note）

    # ---------------------------------------------------------------- 对外

    def set_note(self, note: QWidget | None) -> None:
        """盖在列表可见区域里的一块说明（编辑窗口选中默认推荐、又没有固定档位、列表一行都没有时写下一步怎么做，由编辑窗口
        给，显示与否也由它定；None 为去掉）：铺满可见区域的宽、从上沿起，四周留 NOTE_MARGIN，随列表的大小变；不接鼠标，
        滚轮、点击照常交给列表。"""
        old, self._note = self._note, note
        if old is not None and old is not note:
            old.hide()
        if note is not None:
            note.setParent(self.viewport())
            note.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._place_note()

    def _place_note(self) -> None:
        """说明（set_note）按可见区域现在的大小摆：宽铺满（扣掉两边的 NOTE_MARGIN），高按折行以后的字，不超过可见区域。"""
        note = self._note
        if note is None:
            return
        area = self.viewport().rect().adjusted(NOTE_MARGIN, NOTE_MARGIN, -NOTE_MARGIN, -NOTE_MARGIN)
        width = max(area.width(), 0)
        height = note.heightForWidth(width) if note.hasHeightForWidth() else note.sizeHint().height()
        note.setGeometry(area.left(), area.top(), width, max(0, min(height, area.height())))
        note.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802  Qt 的事件名
        super().resizeEvent(event)
        self._place_note()

    def apply_skin(self) -> None:
        """换皮肤（编辑窗口调用）：重新套样式表，各项按新皮肤重画，行里的图标按新皮肤重做（缺图时画的圆圈用皮肤的颜色）。
        字号、尺寸变了时按新的尺寸重新排（正在拖的那一行先放回原位，正在做的动画直接到位）；只换颜色时不动位置，
        正在拖、正在做的动画照常接着做。比的是排现在这些位置时用的尺寸（_laid）：这里取到的留白等已经是新皮肤的，
        不能拿来当「换之前」。"""
        before = self._laid
        self.setStyleSheet(theme.list_style())
        self._metrics = None
        for item in self._rows.values():
            item.refresh_icon()
        if self._inline is not None:
            self._inline[1].layout().setContentsMargins(*_inline_margins())
        if before is None or self._geometry() != before:
            self._end_drag(cancel=True)
            self._relayout()
            if self._inline is not None:  # 放进来的问话照旧整个露出来（同 set_inline）
                chip, host = self._inline
                row = self._rows.get(chip)
                if row is not None and self._is_placed(row) and self._is_placed(host):
                    self._reveal(row.ty, host.ty + host.th)
        for item in [*self._rows.values(), *self._headers.values()]:
            item.update()
        for hint in self._hints.values():
            if hint.active:
                hint.reposition()
                hint.update()
        self._place_note()  # 字号可能变了：说明按新的高度摆

    def refresh_icons(self) -> None:
        """识别用的图片下载好了（悬浮窗调用）：各行的筹码图标重做、重画，缺图时画的圆圈换成真的图标。不动位置。"""
        for item in self._rows.values():
            item.refresh_icon()
            item.update()

    def restyle(self, blocks: Iterable[BlockSpec]) -> None:
        """换皮肤时（编辑窗口调用）：内容只是按新皮肤换了颜色（行尾的颜色等），块、组、行的先后和标题都没变——只换每一行的
        内容、重画，不重新排，不打断正在拖的那一行和正在做的动画。先后变了时照 set_content 直接换（不做动画）。"""
        blocks = _clean_blocks(blocks)
        if _structure(blocks) != _structure(self._blocks):
            self.set_content(blocks, animate=False)
            return
        for block in blocks:
            for spec in block.rows:
                self._rows[spec.chip].set_spec(spec, block.key, None, block.draggable)
            for group in block.groups:
                for spec in group.rows:
                    self._rows[spec.chip].set_spec(spec, block.key, group.key, False)
        self._blocks = blocks

    def _geometry(self) -> tuple:
        """决定各项位置和大小的量（按当前皮肤的字号、尺寸算）：排的时候记下（_laid），换皮肤时比较，变了才重新排。"""
        m = self._m()
        return (m.row_h, m.button_w, m.button_h, m.name_w, m.school_w, m.title_h, m.block_h, m.group_h, m.button_x,
                m.end_x, m.icon_x, m.name_x, m.school_x, _inline_margins(), L.CANVAS_PAD_X, L.CANVAS_PAD_TOP,
                L.CANVAS_PAD_BOTTOM, L.SEP_SPACE)

    def set_content(self, blocks: Iterable[BlockSpec], animate: bool = False) -> None:
        """换内容。animate 为 True（用户改了档位、拖动、列进方案、上锁解锁以后）：换了块的行飞过去、落定闪一下，
        整组换块的收起展开，要去的地方不在画面里时出提示；为 False（换方案、换角色、外部刷新）：直接换。
        两种都补回滚动位置（画面里没被改动的那几行不跳）。"""
        blocks = _clean_blocks(blocks)
        self._end_drag(cancel=True)
        snap = self._snapshot()
        self._adopt(blocks)
        self._compute()
        self._transition(snap, animate=bool(animate), effects=bool(animate))

    def set_filter(self, names: Iterable[str] | None) -> None:
        """只显示 names 里的筹码（None 为不筛选）。没有符合的行的组、块整个不显示；筛选时收起的组也展开显示符合的行。"""
        new = None if names is None else frozenset(names)
        if new == self._filter:
            return
        self._end_drag(cancel=True)
        snap = self._snapshot()
        self._filter = new
        self._compute()
        self._transition(snap, animate=False, effects=False)

    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)

    def scroll_to(self, chip: str) -> None:
        """滚到让这一行整行露出来；已经在画面里时不动。"""
        item = self._rows.get(chip)
        if item is not None and self._is_placed(item):
            self._reveal(item.ty, item.ty + item.th)

    def _reveal(self, top: float, bottom: float) -> None:
        """滚到让画布上 top 到 bottom 这一段露出来（放不下时先保证上沿露出来）；已经在画面里时不动。"""
        bar = self.verticalScrollBar()
        s = value = bar.value()
        vh = self.viewport().height()
        if bottom > s + vh:
            s = int(math.ceil(bottom - vh)) + SCROLL_MARGIN
        if top < s:
            s = max(0, int(top) - SCROLL_MARGIN)
        if s != value:
            bar.setValue(s)

    def flash(self, chip: str) -> None:
        """这一行闪一下。"""
        item = self._rows.get(chip)
        if item is not None and self._is_placed(item):
            self._flash(item)

    def row_rect(self, chip: str, to: QWidget | None = None) -> QRect | None:
        """这一行（动画结束后的位置）在 to（默认列表自己）坐标里的位置；没显示时为 None。"""
        item = self._rows.get(chip)
        if item is None or not self._is_placed(item):
            return None
        rect = QRect(self._item_x(), round(item.ty), self._item_w(), item.th)
        target = to if to is not None else self
        top_left = target.mapFromGlobal(self.canvas.mapToGlobal(rect.topLeft()))
        return QRect(top_left, rect.size())

    def set_inline(self, chip: str | None, widget: QWidget | None) -> None:
        """把 widget 放进列表、紧挨在 chip 那一行下面（下面的行让开）；chip 或 widget 为 None 时拿掉原来放的。
        拿掉时把控件藏起来、父控件设回 None，由窗口留着或删掉。同一时间只放一个。"""
        old = self._inline
        if old is not None and widget is old[1].widget and chip == old[0]:
            return
        # 用键盘点了这一行的按钮才放进来的：放好以后键盘焦点跟进去（问话的第一个按钮）
        follow = (chip is not None and widget is not None and self.hasFocus() and self._ring
                  and self._cursor is not None and self._cursor[0] == "button" and self._cursor[1] == chip)
        self._end_drag(cancel=True)
        snap = self._snapshot()
        if old is not None:
            if self._focus_in_inline():  # 焦点在要拿掉的控件里：先回到列表（回到点开它的那个按钮），再藏控件
                self._cursor = self._inline_return or self._cursor
                self._ring = True
                self.setFocus(Qt.OtherFocusReason)
            self._release_inline_focus()
            self._inline = None
            host = old[1]
            self._forget(host)
            host.release()
            host.hide()
            host.deleteLater()
        if chip is not None and widget is not None:
            self._inline = (chip, _InlineHost(self, widget))
            self._adopt_inline_focus(widget)
            # 焦点在问话里时拿掉问话，回到点开它的那个按钮（键盘、鼠标点的都记在 _cursor 上），不是这一行时回到第一个按钮
            on_row = self._cursor is not None and self._cursor[0] == "button" and self._cursor[1] == chip
            self._inline_return = self._cursor if on_row else ("button", chip, 0)
        self._compute()
        row = self._rows.get(chip) if chip is not None else None
        self._transition(snap, animate=True, effects=False, prefer=row)
        if self._inline is not None and row is not None and self._is_placed(self._inline[1]):
            host = self._inline[1]
            self._reveal(row.ty, host.ty + host.th)  # 问话整个露出来（放不下时先露出那一行）
            if follow:
                self._go(("inline",), True)

    def set_group_open(self, key: str, open_: bool) -> None:
        """收起、展开组名为 key 的组（不发 group_toggled）。"""
        if self._open.get(key) == bool(open_):
            return
        self._end_drag(cancel=True)
        snap = self._snapshot()
        self._open[key] = bool(open_)
        self._compute()
        self._transition(snap, animate=False, effects=False, prefer=self._group_header(key))

    def group_open(self, key: str) -> bool:
        """组名为 key 的组展开着没有（还没出现过的组为 True）。"""
        return self._open.get(key, True)

    def folds(self) -> dict[str, bool]:
        """各组展开着没有（组名 → 展开着没有，只含出现过、设过的组）：编辑窗口换皮肤重开时交出，新的列表用 set_folds
        接回。"""
        return dict(self._open)

    def set_folds(self, folds: dict[str, bool] | None) -> None:
        """接回 folds() 交出的收起展开状态：直接到位，不发 group_toggled。还没出现的组照样记下，出现时按记下的收起展开
        （同 set_group_open）。"""
        new = {str(key): bool(open_) for key, open_ in (folds or {}).items()}
        if all(self._open.get(key) == open_ for key, open_ in new.items()):
            return
        self._end_drag(cancel=True)
        snap = self._snapshot()
        self._open.update(new)
        self._compute()
        self._transition(snap, animate=False, effects=False)

    def set_animated(self, on: bool | None) -> None:
        """这个列表做不做动画：True、False，或 None（跟模块默认，见 set_default_animated）。"""
        self._animated = on
        if on is False:
            self.finish_animations()

    def finish_animations(self) -> None:
        """正在做的动画立即到终点。"""
        self._finish_motions()
        for item in list(self._flashing):
            item.set_flash(0.0)
        self._flashing.clear()
        self._unlight()
        self._timer.stop()
        self._fit_canvas(self._total)

    # ---------------------------------------------------------------- 测试和窗口用的查询

    def content(self) -> tuple[BlockSpec, ...]:
        """现在显示的内容（去掉重复以后）。"""
        return self._blocks

    def rows_in(self, block: str, group: str | None = None, visible_only: bool = True) -> list[str]:
        """这一块（或块里这一组）的筹码名，按画面上的先后；visible_only 为 False 时连筛掉、收起的一起（按内容的先后）。"""
        if visible_only:
            return [it.chip for it, _, _ in self._placed
                    if isinstance(it, _RowItem) and it.block == block and it.group == group]
        for b in self._blocks:
            if b.key != block:
                continue
            if group is None:
                return [r.chip for r in b.rows]
            for g in b.groups:
                if g.key == group:
                    return [r.chip for r in g.rows]
        return []

    def row_spec(self, chip: str) -> RowSpec | None:
        item = self._rows.get(chip)
        return item.spec if item is not None else None

    def row_widget(self, chip: str) -> QWidget | None:
        return self._rows.get(chip)

    def click_tier(self, chip: str, tier: str) -> None:
        """和用鼠标点那个按钮一样：enabled 的行（被锁的行也是）发 tier_clicked。"""
        item = self._rows.get(chip)
        if item is not None and tier in TIERS and item._clickable():
            self.tier_clicked.emit(chip, tier)

    def tier_button_rect(self, chip: str, tier: str) -> QRect | None:
        """那个按钮在列表坐标里的位置（这一行没显示时为 None）。"""
        item = self._rows.get(chip)
        if item is None or tier not in TIERS or not self._is_placed(item):
            return None
        rect = self._m().button_rect(TIERS.index(tier), item.height()).toAlignedRect()
        return QRect(item.mapTo(self, rect.topLeft()), rect.size())

    def hint_lines(self, side: str) -> list[str]:
        """上沿（TOP）或下沿（BOTTOM）正在显示的提示。"""
        hint = self._hints.get(side)
        return list(hint.lines) if hint is not None and hint.active else []

    # ---------------------------------------------------------------- 动画开关

    def _animations_on(self) -> bool:
        if self._animated is not None:
            return self._animated
        if specs._default_animated is not None:
            return specs._default_animated
        if QGuiApplication.platformName() in NO_ANIMATION_PLATFORMS:
            return False
        return system_animations()

    # ---------------------------------------------------------------- 尺寸

    def _m(self) -> _Metrics:
        if self._metrics is None:
            # 筹码名一栏按行首实际画的名字量（繁体界面是游戏的繁体名，见 items.py 的 shown_name）
            shown = [names.chip_name(chip) for chip in self.db.chips.values()]
            self._metrics = _Metrics(self.font(), shown, self._school_w)
        return self._metrics

    def _item_x(self) -> int:
        return L.CANVAS_PAD_X

    def _item_w(self) -> int:
        return max(0, self.canvas.width() - 2 * L.CANVAS_PAD_X)

    def _fit_canvas(self, height: float) -> None:
        vp = self.viewport()
        h = max(0, int(math.ceil(height)))
        if (self.canvas.width(), self.canvas.height()) != (vp.width(), h):
            self.canvas.resize(vp.width(), h)
        self.verticalScrollBar().setRange(0, max(0, h - vp.height()))

    def _set_scroll(self, value: int) -> None:
        self.verticalScrollBar().setValue(int(value))

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Resize:  # 放到事件循环里做：这时滚动条可能正在出现或消失
            QTimer.singleShot(0, self, self._viewport_resized)
        return super().viewportEvent(event)

    def _viewport_resized(self) -> None:
        """列表变宽变窄（或滚动条出现、消失）：各项跟着变宽，放进来的控件高度变了时重排；提示挪回边缘。"""
        vp = self.viewport()
        if self.canvas.width() != vp.width():
            self.canvas.resize(vp.width(), self.canvas.height())
            for item, _, _ in self._placed:
                item.apply(item.vy, item.opacity)
            for item in list(self._moving):
                if item.ghost:
                    item.apply(item.vy, item.opacity)
        self._inline_changed()
        self.verticalScrollBar().setRange(0, max(0, self.canvas.height() - vp.height()))
        for hint in self._hints.values():
            if hint.active:
                hint.reposition()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.FontChange and getattr(self, "_hints", None) is not None:
            self._end_drag(cancel=True)  # 要按新的字号重新排：正在拖的那一行先放回原位
            self._metrics = None
            self._relayout()
            for item in [*self._rows.values(), *self._headers.values()]:
                item.update()

    def _relayout(self) -> None:
        """按现在的内容重新排（不做动画）。"""
        snap = self._snapshot()
        self._compute()
        self._transition(snap, animate=False, effects=False)

    def _inline_changed(self) -> None:
        """放进来的控件内容变了：高度变了才重排（重排本身不会再引起重排）。"""
        if self._inline is None or self._drag is not None:
            return
        host = self._inline[1]
        if self._is_placed(host) and host.measure(self._item_w()) != host.th:
            self._relayout()

    # ---------------------------------------------------------------- 内容 → 各项

    def _adopt(self, blocks: tuple[BlockSpec, ...]) -> None:
        """换成新内容：同一个筹码沿用原来的行，同一个块、组沿用原来的标题，新出现的新建，不再出现的删掉。"""
        where: dict[str, tuple[str, str | None]] = {}
        headers_seen: set[tuple] = set()
        widest = 0
        m = self._m()
        for block in blocks:
            key = ("block", block.key)
            header = self._headers.get(key) or self._new_header(key)
            header.set_title(block.title)
            headers_seen.add(key)
            for spec in block.rows:
                self._take_row(spec, block.key, None, block.draggable)
                where[spec.chip] = (block.key, None)
                widest = max(widest, m.fm_small.horizontalAdvance(lang.t(spec.school)))  # 按显示的流派名量（items.py）
            for group in block.groups:
                key = ("group", block.key, group.key)
                header = self._headers.get(key) or self._new_header(key)
                header.set_title(group.title)
                headers_seen.add(key)
                self._open.setdefault(group.key, bool(group.open))
                for spec in group.rows:
                    self._take_row(spec, block.key, group.key, False)
                    where[spec.chip] = (block.key, group.key)
                    widest = max(widest, m.fm_small.horizontalAdvance(lang.t(spec.school)))
        for chip in [c for c in self._rows if c not in where]:
            self._drop(self._rows.pop(chip))
        for key in [k for k in self._headers if k not in headers_seen]:
            self._drop(self._headers.pop(key))
        self._blocks = blocks
        self._where = where
        if widest > m.school_w:  # 流派一栏只变宽、不变窄，免得换内容时按钮左右挪
            self._school_w = widest
            self._metrics = None
            for item in self._rows.values():
                item.update()

    def _new_header(self, key: tuple) -> _HeaderItem:
        kind, block, *rest = key
        header = _HeaderItem(self, kind, block, rest[0] if rest else None)
        self._headers[key] = header
        return header

    def _take_row(self, spec: RowSpec, block: str, group: str | None, draggable: bool) -> None:
        item = self._rows.get(spec.chip)
        if item is None:
            item = self._rows[spec.chip] = _RowItem(self, spec.chip)
        item.set_spec(spec, block, group, draggable)

    def _stop(self, item: _Item) -> None:
        """停下这一项正在做的挪动（不到终点）。"""
        self._supersede(item)
        item.motion = None
        self._moving.discard(item)

    def _supersede(self, item: _Item) -> None:
        """这一项正在做的挪动要被换掉或停下：是拖动松手后滑进空位的那一段（_drag_end 的 settled）时先收尾，收起拖动时的
        底色和框。松手时窗口马上存好方案、交来新内容（set_content），新的挪动会换掉这一段；不收尾的话框一直留在行上。
        其余动画的收尾（落定闪一下、删掉影子等）照旧不跑。"""
        m = item.motion
        if m is not None and getattr(m.then, "ends_drag", False):
            m.then()

    def _forget(self, item: _Item) -> None:
        """这一项要藏起来或删掉：动画和闪一下都停下。"""
        self._stop(item)
        self._flashing.discard(item)
        self._lit.discard(item)
        item.flash = 0.0

    def _drop(self, item: _Item) -> None:
        self._forget(item)
        item.hide()
        item.deleteLater()

    def _group_header(self, key: str) -> _HeaderItem | None:
        for (kind, *_rest), header in self._headers.items():
            if kind == "group" and header.group == key:
                return header
        return None

    def _block_spec(self, key: str) -> BlockSpec | None:
        return next((b for b in self._blocks if b.key == key), None)

    # ---------------------------------------------------------------- 排位置

    def _compute(self) -> None:
        """按内容、组的收起展开、筛选和放进来的控件排好每一项：位置记在各项的 ty、th，显示的项按先后记在 _placed。"""
        m = self._m()
        filt = self._filter
        inline_chip, host = self._inline if self._inline is not None else (None, None)
        width = self._item_w()
        placed: list[tuple[_Item, float, int]] = []
        y = float(L.CANVAS_PAD_TOP)

        def put(item: _Item, h: int) -> None:
            nonlocal y
            item.ty, item.th = y, h
            placed.append((item, y, h))
            y += h

        def put_row(spec: RowSpec) -> None:
            put(self._rows[spec.chip], m.row_h)
            if spec.chip == inline_chip:
                put(host, host.measure(width))

        first = True
        for block in self._blocks:
            direct = [r for r in block.rows if filt is None or r.chip in filt]
            groups = []
            for g in block.groups:
                members = [r for r in g.rows if filt is None or r.chip in filt]
                if filt is not None and not members:
                    continue
                open_ = filt is not None or self._open.get(g.key, g.open)
                groups.append((g, members if open_ else [], open_))
            if filt is not None and not direct and not groups:
                continue
            header = self._headers[("block", block.key)]
            header.set_state(first=first)
            first = False
            h = header.measure()
            if h > 0:
                put(header, h)
            for r in direct:
                put_row(r)
            for g, members, open_ in groups:
                gh = self._headers[("group", block.key, g.key)]
                gh.set_state(open_=open_, fold_enabled=filt is None)
                put(gh, gh.measure())
                for r in members:
                    put_row(r)
        self._placed = placed
        self._total = int(math.ceil(y + L.CANVAS_PAD_BOTTOM)) if placed else 0
        self._laid = self._geometry()

    def _is_placed(self, item: _Item) -> bool:
        return any(it is item for it, _, _ in self._placed)

    def _snapshot(self) -> _Snap:
        """改之前的样子（先把飞到边缘、淡入淡出这类动画做完，普通的挪位置照留）。"""
        self._settle_special()
        order = [item for item, _, _ in self._placed]
        return _Snap(
            placed={item: (y, h) for item, y, h in self._placed},
            order=order,
            vy={item: item.vy for item in order},
            where=dict(self._where),
            recipes={item: item.recipe() for item in order},
            headers=dict(self._headers),
            scroll=self.verticalScrollBar().value(),
            vh=self.viewport().height(),
            stops=self._stops(),
        )

    def _moved_groups(self, before: dict) -> dict[str, tuple[str, str]]:
        """整组换了块的组（上锁、解锁）：组名 → （原来的块, 现在的块）。原来那一块里没有了这个组、现在那一块里原来
        没有这个组的才算。"""
        old = {(b, g) for b, g in before.values() if g is not None}
        new = {(b, g) for b, g in self._where.values() if g is not None}
        out = {}
        for b0, g in old:
            if (b0, g) in new:
                continue
            targets = [b1 for b1, g1 in new if g1 == g and b1 != b0 and (b1, g) not in old]
            if len(targets) == 1:
                out[g] = (b0, targets[0])
        return out

    def _anchor(self, snap: _Snap, new: dict, moving: set, prefer: _Item | None) -> _Item | None:
        """补回滚动位置时留在原位的一项：改之前画面里最上面一项位置没被改动的（prefer 在画面里时用它）。"""
        s, vh = snap.scroll, snap.vh

        def visible(item):
            y, h = snap.placed[item]
            return y + h > s and y < s + vh

        def stable(item):
            return item in new and item in snap.placed and item not in moving

        if prefer is not None and stable(prefer) and visible(prefer):
            return prefer
        for item in snap.order:
            if stable(item) and visible(item):
                return item
        return None

    def _transition(self, snap: _Snap, animate: bool, effects: bool, prefer: _Item | None = None) -> None:
        """按改之前的样子（snap）和现在排好的位置挪各项：先补回滚动位置；animate 为 True 且开着动画时换了块的行飞、
        整组换块和收起展开的组收起展开、其余的滑到新位置，否则直接到位；effects 为 True（用户的改动）时出提示、
        落定闪一下。"""
        new = {item: (y, h) for item, y, h in self._placed}
        vh = max(0, self.viewport().height())
        s = snap.scroll
        moved_groups = self._moved_groups(snap.where)
        flyers: list[_RowItem] = []
        regrouped: set[_RowItem] = set()
        for item in new:
            if isinstance(item, _RowItem) and item in snap.placed:
                before, after = snap.where.get(item.chip), self._where.get(item.chip)
                if before != after:
                    if before[1] is not None and before[1] == after[1] \
                            and moved_groups.get(before[1]) == (before[0], after[0]):
                        regrouped.add(item)
                    else:
                        flyers.append(item)
        # 换了地方、新地方在收起的组里（看不见）的行 → 那个组的组标题：飞进组标题里淡出
        tucked: dict[_RowItem, _HeaderItem] = {}
        for item in snap.order:
            if isinstance(item, _RowItem) and item not in new and item.chip in self._where:
                before, after = snap.where.get(item.chip), self._where[item.chip]
                whole = before[1] is not None and moved_groups.get(before[1]) == (before[0], after[0])
                header = self._headers.get(("group", *after)) if after[1] is not None else None
                if before != after and not whole and header is not None and header in new:
                    tucked[item] = header
        for item in (*flyers, *regrouped, *tucked):
            item.reset_pointer()
        many = len(flyers) + len(tucked) > FLY_MAX
        anchor = self._anchor(snap, new, set(flyers) | regrouped, prefer)
        total = self._total
        s2 = s if anchor is None else s + round(new[anchor][0] - snap.placed[anchor][0])
        s2 = max(0, min(s2, max(0, total - vh)))
        lines: dict[str, list[str]] = {TOP: [], BOTTOM: []}
        landing: set[_RowItem] = set()
        if effects:
            if not many:
                for item in flyers:
                    y, h = new[item]
                    side = self._side(y, h, s2, vh)
                    if side is None:
                        landing.add(item)
                    else:
                        text = self._moved_text(item)
                        if text:
                            lines[side].append(text)
                for item, header in tucked.items():  # 收起的组的组标题不在画面里时也出提示
                    side = self._side(*new[header], s2, vh)
                    text = self._moved_text(item)
                    if side is not None and text:
                        lines[side].append(text)
            for text, dest in self._group_texts(moved_groups):
                lines[self._side_of_block(dest, s2, vh)].append(text)
        if animate and not many and self._animations_on():
            self._animate(snap, new, flyers, regrouped, s2, vh, landing, tucked)
        else:
            self._finish_motions()
            self._fit_canvas(total)
            self._set_scroll(s2)
            for item, (y, h) in new.items():
                item.apply(y, 1.0)
            self._hide_unplaced(new)
            for item in landing:
                self._flash(item)
        self._restack(flyers)
        if effects:  # 提示只说最近这一下改动：没有新提示的那一边，上一次的提示收起
            for side in (TOP, BOTTOM):
                if lines[side]:
                    self._hints[side].show_lines(lines[side])
                elif self._hints[side].active:
                    self._hints[side].close_hint()
        self._repair_cursor(snap)

    def _animate(self, snap: _Snap, new: dict, flyers: list, regrouped: set, s2: int, vh: int, landing: set,
                 tucked: dict | None = None) -> None:
        tucked = tucked or {}
        d = s2 - snap.scroll
        plans = []
        extent = float(self._total)
        flyer_set = set(flyers)

        def on_screen(y, h):
            return y + h > s2 and y < s2 + vh

        for item, (y, h) in new.items():
            if item in flyer_set:
                y0 = snap.vy[item] + d
                side = self._side(y, h, s2, vh)
                if not on_screen(y0, h):  # 原来就不在画面里（不是刚点的那一行）：不横穿画面，原地淡入或直接到位
                    if side is not None:
                        self._stop(item)
                        item.apply(y, 1.0)
                        continue
                    plans.append((item, y, y, 0.0, 1.0, partial(self._land, item, item in landing), True))
                elif side == TOP:
                    plans.append((item, y0, s2 - h, 1.0, 0.0, partial(self._land, item, False), True))
                elif side == BOTTOM:
                    plans.append((item, y0, s2 + vh, 1.0, 0.0, partial(self._land, item, False), True))
                else:
                    plans.append((item, y0, y, 1.0, 1.0, partial(self._land, item, item in landing), True))
            elif item in snap.placed and item not in regrouped:
                y0 = snap.vy[item] + d
                if abs(y0 - y) < 0.5 or not (on_screen(y0, h) or on_screen(y, h)):
                    self._stop(item)
                    item.apply(y, 1.0)
                    continue
                plans.append((item, y0, y, 1.0, 1.0, None, False))
            else:  # 新显示的：组里的行从组标题处展开，其余原地淡入
                source = self._unfold_source(item, new)
                y0 = y if source is None else source
                if not on_screen(y, h):
                    self._stop(item)
                    item.apply(y, 1.0)
                    continue
                plans.append((item, y0, y, 0.0, 1.0, None, True))
            extent = max(extent, plans[-1][1] + h)
        ghosts = []
        for item in snap.order:
            if item in new and item not in regrouped:
                continue
            y, h = snap.placed[item]
            if y + h <= snap.scroll or y >= snap.scroll + snap.vh:
                continue
            make = snap.recipes.get(item)
            if make is None:
                continue
            y0 = snap.vy[item] + d
            if item in tucked:  # 飞进收起的组的组标题（标题不在画面里时飞到那一边的边缘）
                target = min(max(new[tucked[item]][0], s2 - h), s2 + vh)
                ghosts.append((make, y0, target, h, 2))
            else:
                ghosts.append((make, y0, self._fold_target(item, snap, new, d, y0), h, int(isinstance(item, _RowItem))))
            extent = max(extent, y0 + h)
        self._fit_canvas(extent)
        self._set_scroll(s2)
        self._hide_unplaced(new)
        # 标题的影子先放，收起的行的影子压在下面；飞进收起的组的那一行在最上面
        for make, y0, y1, h, kind in sorted(ghosts, key=lambda g: g[4]):
            ghost = make()
            ghost.th = h
            if kind == 2:
                ghost.raise_()
            else:
                ghost.lower()
            self._start(ghost, y0, y1, 1.0, 0.0, MOVE_MS, partial(self._drop, ghost), True)
        for item, y0, y1, op0, op1, then, special in plans:
            self._start(item, y0, y1, op0, op1, MOVE_MS, then, special)

    def _land(self, item: _RowItem, flash: bool) -> None:
        """飞的行到了：摆到排好的位置（飞到边缘淡出的也回到排好的位置），flash 为 True 时闪一下。"""
        item.apply(item.ty, 1.0)
        if flash:
            self._flash(item)

    def _unfold_source(self, item: _Item, new: dict) -> float | None:
        """新显示的行从哪里展开：所在组的组标题显示着时是组标题的位置，否则 None（原地淡入）。"""
        if isinstance(item, _RowItem) and item.group is not None:
            header = self._headers.get(("group", item.block, item.group))
            if header is not None and header in new:
                return new[header][0]
        return None

    def _fold_target(self, item: _Item, snap: _Snap, new: dict, d: int, y0: float) -> float:
        """不再显示的一项的影子往哪里收：组里的行收进原来的组标题（组标题还在时收到它的新位置），其余原地淡出。"""
        if isinstance(item, _RowItem):
            where = snap.where.get(item.chip)
            if where is not None and where[1] is not None:
                header = snap.headers.get(("group", where[0], where[1]))
                if header is not None and header in new:
                    return new[header][0]
                if header is not None and header in snap.placed:
                    return snap.placed[header][0] + d
        return y0

    def _hide_unplaced(self, new: dict) -> None:
        items = [*self._rows.values(), *self._headers.values()]
        if self._inline is not None:
            items.append(self._inline[1])
        for item in items:
            if item not in new and item.isVisibleTo(self.canvas):
                self._forget(item)
                item.hide()

    def _restack(self, flyers: Iterable[_Item] = ()) -> None:
        """标题压在行上面（展开的行从标题下面出来），飞的行、被拖的行在最上面。"""
        for header in self._headers.values():
            if header.isVisibleTo(self.canvas):
                header.raise_()
        for item in flyers:
            item.raise_()
        if self._drag is not None:
            self._drag.row.raise_()

    @staticmethod
    def _side(y: float, h: float, s: float, vh: float) -> str | None:
        """排好的位置在画面上方（TOP）、下方（BOTTOM），还是在画面里（None）。露出来不到半行的按不在画面里算。"""
        if vh <= 0:
            return None
        shown = min(y + h, s + vh) - max(y, s)
        if shown >= h / 2:
            return None
        return TOP if y + h / 2 < s + vh / 2 else BOTTOM

    def _side_of_block(self, key: str, s: float, vh: float) -> str:
        """一块在画面上方时为 TOP，其余为 BOTTOM（提示放在那一边）。"""
        for item, y, h in self._placed:
            if getattr(item, "block", None) == key:
                return TOP if self._side(y, h, s, vh) == TOP else BOTTOM
        return BOTTOM

    def _moved_text(self, item: _RowItem) -> str:
        block, group = self._where.get(item.chip, ("", None))
        if block in TIER_LABEL and group is None:
            spec = self._block_spec(block)
            chips = [r.chip for r in spec.rows] if spec is not None else []
            if item.chip in chips:
                return HINT_TIER.format(tier=lang.t(TIER_LABEL[block]), rank=chips.index(item.chip) + 1)
        if group is not None and block == BLOCK_UNMARKED:
            return HINT_UNMARKED.format(group=lang.t(group))
        if group is not None and block == BLOCK_OTHER:
            return HINT_OTHER.format(group=lang.t(group))
        return ""

    def _group_texts(self, moved: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
        """上锁、解锁的提示：（文字, 要去的那一块）。"""
        out = []
        locked = _school_sorted(g for g, way in moved.items() if way == (BLOCK_UNMARKED, BLOCK_OTHER))
        unlocked = _school_sorted(g for g, way in moved.items() if way == (BLOCK_OTHER, BLOCK_UNMARKED))
        if locked:
            other = self._block_spec(BLOCK_OTHER)
            n = sum(len(g.rows) for g in (other.groups if other is not None else ()) if g.key in locked)
            out.append((HINT_LOCKED.format(schools=_quoted(locked), n=n), BLOCK_OTHER))
        if unlocked:
            out.append((HINT_UNLOCKED.format(schools=_quoted(unlocked)), BLOCK_UNMARKED))
        return out

    # ---------------------------------------------------------------- 动画

    def _start(self, item: _Item, y0: float, y1: float, op0: float, op1: float, ms: float,
               then: Callable[[], None] | None = None, special: bool = False) -> None:
        self._supersede(item)
        item.motion = _Motion(y0, y1, op0, op1, self._clock(), ms, then, special)
        item.apply(y0, op0)
        self._moving.add(item)
        if not self._timer.isActive():
            self._timer.start()

    def _move(self, item: _Item, y: float, ms: float, motion: bool) -> None:
        """普通的挪位置（拖动时让位、松手滑进空位）。"""
        if motion and abs(item.vy - y) >= 0.5:
            self._start(item, item.vy, y, item.opacity, 1.0, ms)
        else:
            self._stop(item)
            item.apply(y, 1.0)

    def _tick(self) -> None:
        now = self._clock()
        was_moving = bool(self._moving)
        for item in list(self._moving):
            m = item.motion
            if m is None:
                self._moving.discard(item)
                continue
            p = 1.0 if m.ms <= 0 else min(1.0, max(0.0, (now - m.t0) * 1000.0 / m.ms))
            e = _ease(p)
            item.apply(m.y0 + (m.y1 - m.y0) * e, m.op0 + (m.op1 - m.op0) * e)
            if p >= 1.0:
                item.motion = None
                self._moving.discard(item)
                if m.then is not None:
                    m.then()
        for item in list(self._flashing):
            p = (now - item.flash_t0) * 1000.0 / FLASH_MS
            if p >= 1.0:
                item.set_flash(0.0)
                self._flashing.discard(item)
            else:
                item.set_flash(math.sin(math.pi * p))
        if was_moving and not self._moving:
            self._motion_over()
        if not self._moving and not self._flashing:
            self._timer.stop()

    def _in_motion(self) -> bool:
        """有在做的挪动、或者正在拖：各项先铺不透明的底色（paint_base）。"""
        return bool(self._moving) or self._drag is not None

    def _motion_over(self) -> None:
        """挪动都停了（也没在拖）：画布缩回排好的高度，各项重画一遍（去掉动的时候铺的底色）。"""
        if self._in_motion():
            return
        self._fit_canvas(self._total)
        for item, _, _ in self._placed:
            item.update()

    def _finish(self, item: _Item) -> None:
        m = item.motion
        if m is None:
            return
        item.motion = None
        self._moving.discard(item)
        item.apply(m.y1, m.op1)
        if m.then is not None:
            m.then()

    def _finish_motions(self) -> None:
        moved = bool(self._moving)
        while self._moving:
            for item in list(self._moving):
                self._finish(item)
        if moved:
            self._motion_over()

    def _settle_special(self) -> None:
        """飞到边缘淡出、淡入淡出、展开这类动画直接到终点（影子删掉）；普通的挪位置照旧做完。"""
        for item in list(self._moving):
            if item.motion is not None and item.motion.special:
                self._finish(item)

    def _flash(self, item: _Item) -> None:
        if self._animations_on():
            item.flash_t0 = self._clock()
            item.set_flash(0.0)
            self._flashing.add(item)
            if not self._timer.isActive():
                self._timer.start()
        else:
            item.set_flash(1.0)
            self._lit.add(item)
            self._lit_timer.start(FLASH_MS)

    def _unlight(self) -> None:
        for item in list(self._lit):
            item.set_flash(0.0)
        self._lit.clear()
        self._lit_timer.stop()

    # ---------------------------------------------------------------- 点按钮、收起展开

    def _row_clicked(self, item: _RowItem, tier: str) -> None:
        if item._clickable():
            self.tier_clicked.emit(item.chip, tier)

    def _toggle_group(self, header: _HeaderItem) -> None:
        if self._filter is not None or header.group is None:
            return
        self._end_drag(cancel=True)
        snap = self._snapshot()
        key = header.group
        self._open[key] = not self._open.get(key, True)
        self._compute()
        self._transition(snap, animate=True, effects=False, prefer=header)
        self.group_toggled.emit(key, self._open[key])

    # ---------------------------------------------------------------- 键盘（Tab、Shift+Tab、空格、回车）
    # 整张列表在窗口的 Tab 先后里占一个位置；进来以后按画面上的先后一站一站走（_stops），走到头再出去。
    # 每一站：组标题的收起按钮、能点的行的四个档位按钮、放进列表的控件（里面能用 Tab 走到的子控件）。
    # 空格、回车等于用鼠标点那个按钮（tier_clicked、收起展开）。焦点所在的那一行挪走了（改了档位），焦点留在原处，
    # 落到补上来的那一行的同一个按钮（连着用键盘标几个筹码时一路往下走）。

    def _stops(self) -> list[tuple]:
        """能用 Tab 走到的地方，按画面上的先后：("fold", 组名)、("button", 筹码名, 第几个按钮)、("inline",)。
        不能点的行（enabled 为 False）、筛选时的收起按钮不算。"""
        out: list[tuple] = []
        for item, _, _ in self._placed:
            if isinstance(item, _RowItem):
                if item._clickable():
                    out.extend(("button", item.chip, i) for i in range(len(TIERS)))
            elif isinstance(item, _HeaderItem):
                if item.kind == "group" and item.fold_enabled:
                    out.append(("fold", item.group))
            elif isinstance(item, _InlineHost) and self._inline_targets():
                out.append(("inline",))
        return out

    def _stop_item(self, stop: tuple | None) -> _Item | None:
        if not stop:
            return None
        if stop[0] == "button":
            return self._rows.get(stop[1])
        if stop[0] == "fold":
            return self._group_header(stop[1])
        return self._inline[1] if self._inline is not None else None

    def _ring_on(self, stop: tuple) -> bool:
        """这一站要不要画键盘焦点框：列表有焦点、是用键盘走过来的、焦点正在这一站。"""
        return self._ring and self._cursor == stop and self.hasFocus()

    def _set_cursor(self, stop: tuple | None, ring: bool | None = None, reveal: bool = False) -> None:
        before = self._stop_item(self._cursor)
        self._cursor = stop
        if ring is not None:
            self._ring = ring
        item = self._stop_item(stop)
        for it in (before, item):
            if it is not None:
                it.update()
        if reveal and item is not None and self._is_placed(item):
            self._reveal(item.ty, item.ty + item.th)

    def _mouse_cursor(self, stop: tuple) -> None:
        """用鼠标点了按钮：键盘焦点挪到这里（不画框，之后按 Tab 从这里接着走）。"""
        if stop in self._stops():
            self._set_cursor(stop, ring=False)

    def _go(self, stop: tuple, forward: bool) -> None:
        """键盘焦点走到 stop（画框，滚到它露出来）；放进列表的控件就把焦点交给它里面的第一个（往回走时最后一个）子控件。"""
        if stop == ("inline",):
            targets = self._inline_targets()
            if targets:
                self._set_cursor(stop, ring=True, reveal=True)
                (targets[0] if forward else targets[-1]).setFocus(
                    Qt.TabFocusReason if forward else Qt.BacktabFocusReason)
            return
        self._set_cursor(stop, ring=True, reveal=True)

    def _activate(self, stop: tuple) -> None:
        """空格、回车：等于用鼠标点这一站。"""
        if stop[0] == "button":
            row = self._rows.get(stop[1])
            if row is not None and self._is_placed(row):
                self._row_clicked(row, TIERS[stop[2]])
        elif stop[0] == "fold":
            header = self._group_header(stop[1])
            if header is not None and header.fold_enabled and self._is_placed(header):
                self._toggle_group(header)

    def focus_stop(self) -> tuple | None:
        """键盘焦点现在在哪一站（测试用）：列表或放进来的控件有焦点、而且焦点框显示着时为 _stops 里的一项，否则 None。"""
        if self._focus_in_inline():
            return ("inline",)
        return self._cursor if self.hasFocus() and self._ring else None

    def focusNextPrevChild(self, next_: bool) -> bool:
        if not self.hasFocus():
            return super().focusNextPrevChild(next_)
        stops = self._stops()
        if self._cursor in stops:
            j = stops.index(self._cursor) + (1 if next_ else -1)
        else:
            j = 0 if next_ else len(stops) - 1
        if 0 <= j < len(stops):
            self._go(stops[j], next_)
            return True
        return self._leave(next_)

    def _leave(self, forward: bool) -> bool:
        """走到头：出列表，交给窗口里的下一个（上一个）。不用 QScrollArea 自己的那一份：它走完会滚到列表里最后得到过
        焦点的子控件（例如放进来的问话），出列表时画面会跳。"""
        return QWidget.focusNextPrevChild(self, forward)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        reason = event.reason()
        if reason in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            forward = reason == Qt.TabFocusReason
            stops = self._stops()
            if not stops:  # 没有能走到的（例如只读的默认推荐）：不停，直接交给下一个
                self._leave(forward)
                return
            self._go(stops[0] if forward else stops[-1], forward)
        elif reason == Qt.MouseFocusReason:
            self._set_cursor(self._cursor, ring=False)
        else:
            self._set_cursor(self._cursor)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._set_cursor(self._cursor)  # 重画：框去掉

    def keyPressEvent(self, event) -> None:
        keys = (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)
        mods = event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        if event.key() in keys and not mods and self._cursor in self._stops() and self._cursor != ("inline",):
            event.accept()
            if event.isAutoRepeat():
                return
            if not self._ring:  # 鼠标点过以后第一次按：先把焦点框显示出来，不点
                self._set_cursor(self._cursor, ring=True, reveal=True)
            else:
                self._activate(self._cursor)
            return
        super().keyPressEvent(event)

    def _stop_moved(self, stop: tuple, snap: _Snap) -> bool:
        return stop[0] == "button" and snap.where.get(stop[1]) != self._where.get(stop[1])

    def _repair_cursor(self, snap: _Snap) -> None:
        """内容、收起展开、筛选变了以后：键盘焦点所在的那一站还在、没挪走就不动；挪走了（改了档位、藏起来）就留在原处，
        换成改之前排在它后面、现在还在原处的那一行的同一个按钮；后面没有同一列的按钮时取后面最近的一站，再没有时往前找。"""
        cur = self._cursor
        if cur is None or cur == ("inline",):
            return
        stops = self._stops()
        if cur in stops and not self._stop_moved(cur, snap):
            return
        now = set(stops)

        def stays(s):
            return s in now and not self._stop_moved(s, snap)

        def same_column(s):
            return cur[0] == "button" and s[0] == "button" and s[2] == cur[2]

        choice = None
        if cur in snap.stops:
            i = snap.stops.index(cur)
            ahead, behind = snap.stops[i + 1:], snap.stops[:i][::-1]
            for seq in ([s for s in ahead if same_column(s)], ahead, [s for s in behind if same_column(s)], behind):
                choice = next((s for s in seq if stays(s)), None)
                if choice is not None:
                    break
        if choice is None and stops:
            choice = stops[0]
        self._set_cursor(choice, reveal=self.hasFocus() and self._ring)

    # ---------------------------------------------------------------- 键盘：放进列表的控件

    def _adopt_inline_focus(self, widget: QWidget) -> None:
        """放进来的控件里能用 Tab 走到的子控件：记下先后和原来的焦点方式，放着期间设成不接 Tab（免得窗口按自己的
        先后走到它们），由列表接管：Tab、Shift+Tab 在它们之间走、走出去回到列表，回车等于点。"""
        found, w = [], widget
        for _ in range(5000):
            if (w is widget or widget.isAncestorOf(w)) and w.focusPolicy() & Qt.TabFocus:
                found.append(w)
            w = w.nextInFocusChain()
            if w is widget:
                break
        self._inline_focus = found
        self._inline_policies = {w: w.focusPolicy() for w in found}
        for w in found:
            w.setFocusPolicy(Qt.NoFocus)
            w.installEventFilter(self)

    def _release_inline_focus(self) -> None:
        for w, policy in self._inline_policies.items():
            try:
                w.setFocusPolicy(policy)
                w.removeEventFilter(self)
            except RuntimeError:  # 控件已经被窗口删掉了
                pass
        self._inline_focus = []
        self._inline_policies = {}

    def _inline_targets(self) -> list[QWidget]:
        return [w for w in self._inline_focus if w.isVisibleTo(self) and w.isEnabled()]

    def _focus_in_inline(self) -> bool:
        if self._inline is None:
            return False
        focused = QApplication.focusWidget()
        widget = self._inline[1].widget
        return focused is not None and (focused is widget or widget.isAncestorOf(focused))

    def _inline_tab(self, obj: QWidget, forward: bool) -> None:
        """焦点在放进来的控件里按 Tab、Shift+Tab：在它的子控件之间走；走出去回到列表的前一站、后一站（列表走到头时出列表）。"""
        targets = self._inline_targets()
        j = (targets.index(obj) + (1 if forward else -1)) if obj in targets else len(targets)
        if 0 <= j < len(targets):
            targets[j].setFocus(Qt.TabFocusReason if forward else Qt.BacktabFocusReason)
            return
        stops = self._stops()
        k = stops.index(("inline",)) + (1 if forward else -1) if ("inline",) in stops else -1
        self._cursor = self._inline_return
        self._ring = True
        self.setFocus(Qt.OtherFocusReason)
        if 0 <= k < len(stops):
            self._go(stops[k], forward)
        else:
            self._leave(forward)

    # ---------------------------------------------------------------- 拖动

    def _can_drag(self, item: _RowItem) -> bool:
        if item.ghost or not item.draggable or item.group is not None:
            return False
        if not item.spec.enabled or item.spec.locked:
            return False
        if self._inline is not None:
            host_row = self._rows.get(self._inline[0])
            if host_row is not None and host_row.block == item.block:
                return False
        return self._is_placed(item)

    def _drag_begin(self, row: _RowItem, press: QPointF, now: QPointF) -> None:
        if self._drag is not None or not self._can_drag(row):
            return
        self._settle_special()
        members = [it for it, _, _ in self._placed
                   if isinstance(it, _RowItem) and it.block == row.block and it.group is None]
        pitch = self._m().row_h
        top = members[0].ty
        if any(abs(it.ty - (top + k * pitch)) > 0.5 for k, it in enumerate(members)):
            return
        start = members.index(row)
        grab = self.canvas.mapFromGlobal(QPointF(press)).y() - row.ty
        header = self._headers.get(("block", row.block))
        head = header.ty if header is not None and self._is_placed(header) else top
        self._drag = _Drag(row, row.block, members, start, start, top, pitch, grab, QPointF(now),
                           self._animations_on(), head)
        self._stop(row)
        row.dragging = True
        row.raise_()
        row.update()
        row.setCursor(Qt.ClosedHandCursor)
        QToolTip.hideText()
        QApplication.instance().installEventFilter(self)
        self._edge_timer.start()
        self._drag_move(now)

    def _drag_move(self, global_pos: QPointF) -> None:
        d = self._drag
        if d is None:
            return
        d.pos = QPointF(global_pos)
        y = self.canvas.mapFromGlobal(d.pos).y() - d.grab
        last = len(d.members) - 1
        y = min(max(y, d.top), d.top + last * d.pitch)
        d.row.apply(y, 1.0)
        index = min(max(int(math.floor((y - d.top) / d.pitch + 0.5)), 0), last)
        if index != d.index:
            d.index = index
            self._drag_shift(d, SHIFT_MS)

    def _drag_shift(self, d: _Drag, ms: float) -> None:
        """本块里其余的行按空位排：空位在 d.index。"""
        others = iter(r for r in d.members if r is not d.row)
        for k in range(len(d.members)):
            if k == d.index:
                continue
            item = next(others)
            item.ty = d.top + k * d.pitch
            if item.motion is None or abs(item.motion.y1 - item.ty) >= 0.5:
                self._move(item, item.ty, ms, d.motion)

    def _drag_end(self, cancel: bool = False) -> None:
        """松手（cancel 为 True：Esc、内容换了）：被拖的行滑进空位（取消时滑回原位）；位置变了时发 row_moved。"""
        d = self._drag
        if d is None:
            return
        self._drag = None
        QApplication.instance().removeEventFilter(self)
        self._edge_timer.stop()
        d.row.unsetCursor()
        if cancel:
            d.index = d.start
        self._drag_shift(d, SETTLE_MS)
        target = d.top + d.index * d.pitch
        d.row.ty = target

        def settled():
            d.row.dragging = False
            d.row.update()

        settled.ends_drag = True  # 滑的这一段被别的挪动换掉时也要跑（_supersede）
        if d.motion and abs(d.row.vy - target) >= 0.5:
            self._start(d.row, d.row.vy, target, 1.0, 1.0, SETTLE_MS, settled)
        else:
            d.row.apply(target, 1.0)
            settled()
        self._motion_over()  # 不做动画时这就停了（做动画时等滑完）
        if cancel or d.index == d.start:
            return
        rank = self._full_rank(d)
        spec = self._block_spec(d.block)
        if spec is None:
            return
        chips = [r.chip for r in spec.rows]
        if d.row.chip not in chips or chips.index(d.row.chip) + 1 == rank:  # 不在这一块里（内容刚换过）：不动
            return
        self._reorder(d.block, d.row.chip, rank)
        self.row_moved.emit(d.row.chip, d.block, rank)

    def _full_rank(self, d: _Drag) -> int:
        """松手的位置换成整块（含筛掉的行）里的顺位：放在空位后面那一行的前面；空位在最后时放在最后一行的后面。"""
        spec = self._block_spec(d.block)
        chip = d.row.chip
        full = [r.chip for r in spec.rows] if spec is not None else []
        rest = [c for c in full if c != chip]
        shown = [r.chip for r in d.members if r is not d.row]
        if not shown or chip not in full:
            return full.index(chip) + 1 if chip in full else 1
        if d.index < len(shown):
            return rest.index(shown[d.index]) + 1
        return rest.index(shown[-1]) + 2

    def _reorder(self, block: str, chip: str, rank: int) -> None:
        """先按松手的位置改好显示的内容（窗口存好以后会交来同样的内容）。"""
        blocks = list(self._blocks)
        for i, b in enumerate(blocks):
            if b.key == block:
                row = next((r for r in b.rows if r.chip == chip), None)
                if row is None:  # 这一行已不在这一块里（正常操作走不到）：不动
                    return
                rows = [r for r in b.rows if r.chip != chip]
                rows.insert(rank - 1, row)
                blocks[i] = replace(b, rows=tuple(rows))
        self._blocks = tuple(blocks)
        self._compute()
        motion = self._animations_on()
        for item, y, _ in self._placed:  # 排好的位置和拖动时摆的一致；不一致的（不应出现）挪过去
            if item.motion is None and abs(item.vy - y) >= 0.5:
                item.apply(y, 1.0)
            elif item.motion is not None and abs(item.motion.y1 - y) >= 0.5:
                self._move(item, y, SETTLE_MS, motion)

    def _edge_scroll(self) -> None:
        """拖到列表上下边缘时自动滚动（本块在那个方向还有没露出来的行时才滚；往上滚到块标题露出来为止）。"""
        d = self._drag
        if d is None:
            self._edge_timer.stop()
            return
        vp = self.viewport()
        y = vp.mapFromGlobal(d.pos).y()
        vh = vp.height()
        bar = self.verticalScrollBar()
        s = bar.value()
        step = 0
        if y < EDGE_ZONE and d.head < s:
            step = -min(self._edge_step(EDGE_ZONE - y), s - math.floor(d.head))
        elif y > vh - EDGE_ZONE and d.top + len(d.members) * d.pitch > s + vh:
            step = min(self._edge_step(y - (vh - EDGE_ZONE)), math.ceil(d.top + len(d.members) * d.pitch - s - vh))
        if step:
            bar.setValue(s + step)

    @staticmethod
    def _edge_step(depth: float) -> int:
        return max(1, round(EDGE_SPEED * min(1.0, depth / (2 * EDGE_ZONE))))

    def _scrolled(self, value: int) -> None:
        if self._drag is not None:
            self._drag_move(self._drag.pos)

    def eventFilter(self, obj, event) -> bool:
        if self._drag is not None and event.type() in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            self._end_drag(cancel=True)  # 程序失去前台（Alt+Tab、系统弹窗）：松手事件可能收不到，取消拖动
            return False
        if self._drag is not None and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            # 拖动中又按下左键：上一次的松手事件没送到。取消拖动，这一下不交给鼠标下面的部件（被拖的行滑回原位以后，
            # 鼠标下面可能已经是别的行，点下去会改错筹码的档位）
            self._end_drag(cancel=True)
            return True
        if self._drag is not None and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self._end_drag(cancel=True)
            return True
        if event.type() == QEvent.KeyPress and obj in self._inline_policies:  # 放进来的控件里的按键（见 _adopt_inline_focus）
            key, mods = event.key(), event.modifiers()
            if not mods & (Qt.ControlModifier | Qt.AltModifier):
                if key == Qt.Key_Backtab or (key == Qt.Key_Tab and mods & Qt.ShiftModifier):
                    self._inline_tab(obj, False)
                    return True
                if key == Qt.Key_Tab:
                    self._inline_tab(obj, True)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and isinstance(obj, QAbstractButton):
                    if not event.isAutoRepeat():
                        obj.click()
                    return True
        return super().eventFilter(obj, event)

    def _end_drag(self, cancel: bool) -> None:
        if self._drag is not None:
            self._drag_end(cancel=cancel)

    def hideEvent(self, event) -> None:
        self._end_drag(cancel=True)
        super().hideEvent(event)


lang.localize(globals())
