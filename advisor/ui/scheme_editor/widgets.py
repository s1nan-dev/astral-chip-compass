"""「编辑筹码方案」窗口里的小部件和「造部件的样子」：折行标签、目标流派一栏、规则提示框、软锁问话、
按字数限长的校验器，以及一组造按钮、警告的小函数和把同一族部件收成一套的 _Kit（PLAIN_KIT、PILL_KIT、GAME_KIT）。

七种排法搭界面时照 _Kit 造部件（造部件和接线在 window.py 的 _make_* 里），摆放见 layouts.py；
界面文字和规则常数见 notes.py。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap, QValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from .. import chrome, theme
from ...data import SCHOOLS
from .notes import (
    CLOSE_BUTTON,
    COPY_BUTTON,
    COPY_DEFAULT_BUTTON,
    DELETE_BUTTON,
    DELETE_NO,
    DELETE_YES,
    HINTS_MAX_LINES,
    LOCK_NO,
    LOCK_QUESTION,
    LOCK_YES,
    NEW_BUTTON,
    SCHOOLS_LABEL,
    TARGET_HINT,
    TARGET_NONE,
)

ICON_NEW, ICON_COPY, ICON_DELETE, ICON_SEARCH = 0xE710, 0xE8C8, 0xE74D, 0xE721  # 按钮、搜索框前面的系统图标

def _plain_label(text: str = "", name: str = "") -> QLabel:
    """显示纯文本的标签（不按富文本渲染）。"""
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    if name:
        label.setObjectName(name)
    return label


def _button(text: str, name: str = "", accent: str = "") -> QPushButton:
    btn = QPushButton(text)
    if name:
        btn.setObjectName(name)
    if accent:
        btn.setProperty("accent", accent)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.TabFocus)
    return btn


def _row(spacing: int = 6) -> tuple[QWidget, QHBoxLayout]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return w, lay


class _HoldWrapped:
    """折行标签的最小高度按现在的宽度折出来的行数算（和 QLabel 一起继承，写在前面）。QLabel 折行时报的最小高度只有一行：
    窗口缩矮时排版会把多出来的几行截掉，连带把同一栏的输入框压扁。游戏内风格的三种排法（side_tabs、card_row、
    card_column）用；还没排过版（没有宽度）时照 QLabel 的算法，宽度变了时请排版重新量。"""

    _held_width = -1

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        if self.wordWrap() and self._held_width > 0 and self.text():
            hint.setHeight(max(hint.height(), self.heightForWidth(self._held_width)))
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = event.size().width()
        if width != self._held_width:
            self._held_width = width
            self.updateGeometry()


class _WrapLabel(_HoldWrapped, QLabel):
    """折行的纯文本标签，最小高度见 _HoldWrapped。"""

    def __init__(self, text: str = "", name: str = ""):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setWordWrap(True)
        if name:
            self.setObjectName(name)


class _WrapWarn(_HoldWrapped, chrome.WarnLabel):
    """警告色的提示（chrome.WarnLabel），最小高度见 _HoldWrapped。"""


def _glyph_icon(code: int, color) -> QIcon | None:
    """系统图标字体里的一个图标做成小图标（搜索框左端的放大镜）；系统里没有图标字体时为 None。"""
    from ..settings import draw_glyph, glyph_font

    if glyph_font(15) is None:
        return None
    pix = QPixmap(18, 18)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    draw_glyph(p, QRectF(0, 0, 18, 18), code, 15, QColor(color))
    p.end()
    return QIcon(pix)


def _utf16_len(text: str) -> int:
    """text 在 Qt 里占几个 UTF-16 码元（表情等基本平面以外的字占两个）：Qt 的光标位置按码元数。"""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def _char_index(text: str, units: int) -> int:
    """Qt 的光标位置（第 units 个 UTF-16 码元之前）是 text 的第几个字之前。"""
    n = 0
    for i, ch in enumerate(text):
        if n >= units:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


class _CharLimit(QValidator):
    """按字数限长（一个表情算一个字）：输入后超过 limit 个字时，去掉刚输入的末尾多出来的几个字，原有的字一个不动
    （同 QLineEdit.setMaxLength：满了时在中间打字、粘贴，多出来的不进去，后面原有的字不会被挤掉）。

    方案数据层按 Python 字数算（一个表情一个字）；QLineEdit.setMaxLength 数的是 UTF-16 码元（一个表情两个），
    两者一起会把名称、作者、说明里的表情多截一半。"""

    def __init__(self, limit: int, parent=None):
        super().__init__(parent)
        self.limit = limit

    def validate(self, text, pos):  # noqa: N802  Qt 的方法名
        excess = len(text) - self.limit
        if excess <= 0:
            return QValidator.Acceptable, text, pos
        at = _char_index(text, pos)  # 光标在刚输入的字后面（打字、粘贴、输入法上屏都是）
        if at >= excess:
            text, at = text[:at - excess] + text[at:], at - excess
        else:  # 光标前面的字不够去（不该出现）：去掉末尾多出来的
            text, at = text[:self.limit], min(at, self.limit)
        return QValidator.Acceptable, text, _utf16_len(text[:at])


# ------------------------------------------------------------ 部件


class TargetSchools(QWidget):
    """目标流派一栏：「目标流派」、推出来的目标流派（流派名的胶囊，没有时「无」）、暗色的「按必选、推荐的筹码自动判断」。
    只显示、不能点（胶囊不是按钮：没有悬停变化，鼠标照常是箭头）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(_plain_label(SCHOOLS_LABEL, "field"))
        self.pills: dict[str, QLabel] = {}
        for school in SCHOOLS:
            pill = _plain_label(school, "schoolPill")
            pill.hide()
            self.pills[school] = pill
            lay.addWidget(pill)
        self.none_label = _plain_label(TARGET_NONE)
        lay.addWidget(self.none_label)
        lay.addSpacing(2)
        self.hint = _plain_label(TARGET_HINT, "dim")
        lay.addWidget(self.hint)
        lay.addStretch(1)
        self._shown: tuple[str, ...] = ()

    def set_schools(self, schools) -> None:
        self._shown = tuple(s for s in SCHOOLS if s in set(schools))
        for school, pill in self.pills.items():
            pill.setVisible(school in self._shown)
        self.none_label.setVisible(not self._shown)

    def schools(self) -> tuple[str, ...]:
        """显示着的目标流派（按 data.SCHOOLS 的顺序）。"""
        return self._shown


class _FlowTargets(TargetSchools):
    """目标流派一栏（经典主题-白、经典主题-反色：和「说明」在同一行）：目标流派多于 INLINE_MAX 个时，「按必选、推荐的筹码
    自动判断」从这一行挪到下面一行（below，由排法放在这一行下面），这一行不把窗口撑宽。"""

    INLINE_MAX = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.below = _plain_label(TARGET_HINT, "dim")
        self.below.hide()

    def set_schools(self, schools) -> None:
        many = len([s for s in SCHOOLS if s in set(schools)]) > self.INLINE_MAX
        if many:  # 先把说明挪到下面一行再显示胶囊：中间这一下也不把窗口撑宽
            self.hint.hide()
            self.below.show()
        super().set_schools(schools)
        if not many:
            self.below.hide()
            self.hint.show()


class HintBox(QScrollArea):
    """列表下面的灰色规则提示（label，纯文本，一句一行，放不下时折行）。高度随内容，最多 HINTS_MAX_LINES 行高，
    再多的在框里上下滚动：同系列的提醒多时不把筹码列表挤没。没有提示时整个不显示。
    plate 为 True 时（游戏内风格：提示直接写在有斜带、几何小图案的面板上）提示垫一块底板（chrome.plate_sheet），
    字离底板边缘 ChromeLook.PLATE_PADDING；默认的排法直接画在窗口的底色上。"""

    def __init__(self, parent=None, plate: bool = False):
        super().__init__(parent)
        self._plate = plate
        self.setObjectName("hintBox")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.label = _plain_label("", "dim")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWidget(self.label)
        self.label.setAutoFillBackground(False)  # setWidget 会打开自动填底色：提示直接画在窗口的底色上
        self.viewport().setAutoFillBackground(False)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.apply_plate()

    def apply_plate(self) -> None:
        """底板按当前皮肤（ChromeLook 的 PLATE_*）：底色、圆角写进自己的样式表，字离底板边缘的距离是视口的边距。不垫底板时
        什么都不做。"""
        if not self._plate:
            return
        self.setStyleSheet(chrome.plate_sheet("QScrollArea#hintBox"))
        self.setViewportMargins(*self._padding())
        self.updateGeometry()

    def _padding(self) -> tuple[int, int, int, int]:
        return tuple(theme.CHROME.PLATE_PADDING) if self._plate else (0, 0, 0, 0)

    def set_lines(self, lines) -> None:
        self.label.setText("\n".join(lines))
        self.setVisible(bool(lines))
        self.updateGeometry()

    def _cap(self) -> int:
        _, top, _, bottom = self._padding()
        return QFontMetrics(self.label.font()).lineSpacing() * HINTS_MAX_LINES + 2 + top + bottom

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        left, top, right, bottom = self._padding()
        return min(self.label.heightForWidth(max(width - left - right, 1)) + top + bottom, self._cap())

    def sizeHint(self) -> QSize:
        width = self.width() if self.width() > 0 else self.label.sizeHint().width()
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.sizeHint().height())


class _ShortHints(HintBox):
    """游戏内风格三种排法（side_tabs、card_row、card_column）的规则提示（垫底板）：平时同 HintBox，随内容、最多
    HINTS_MAX_LINES 行高；窗口缩矮时最矮缩到 SHORT_LINES 行高，其余在框里滚动，窗口放得进矮屏幕。排版时不按「高度随宽度」
    排（那样不能缩），首选高度按现在的宽度量，宽度变了时请排版重新量。"""

    SHORT_LINES = 2

    def __init__(self):
        super().__init__(plate=True)
        self.setSizePolicy(QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum))
        self._held_width = -1

    def hasHeightForWidth(self) -> bool:
        return False

    def minimumSizeHint(self) -> QSize:
        _, top, _, bottom = self._padding()
        low = QFontMetrics(self.label.font()).lineSpacing() * self.SHORT_LINES + 2 + top + bottom
        return QSize(0, min(self.sizeHint().height(), low))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != self._held_width:
            self._held_width = event.size().width()
            self.updateGeometry()


class LockQuestion(QFrame):
    """点被锁流派的档位按钮时问一次要不要把这个流派列进方案：问句（左，放不下时折行）和「列进方案」「取消」（右）。
    窗口用 SchemeList.set_inline 把它放进列表、紧挨在那一行下面（占满一行宽，高度按这个宽度下问句折几行定，跟着那一行
    滚动），不另弹窗口。answered 发出回答（True 为列进方案）；问的是哪一行、哪一档记在 chip、tier。"""

    answered = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("lockQuestion")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.chip = ""
        self.tier = ""
        self.school = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)
        self.text = _plain_label("", "question")
        self.text.setWordWrap(True)
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self.text, 1)
        self.yes = _button(LOCK_YES, "small", accent="primary")
        self.yes.clicked.connect(lambda: self.answered.emit(True))
        self.no = _button(LOCK_NO, "small")
        self.no.clicked.connect(lambda: self.answered.emit(False))
        for btn in (self.yes, self.no):
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        self.hide()

    def ask(self, chip: str, tier: str, school: str, limit: int) -> None:
        """问 chip（属于被锁的流派 school）点的 tier 这一档要不要生效。"""
        self.chip, self.tier, self.school = chip, tier, school
        self.text.setText(LOCK_QUESTION.format(limit=limit, school=school))
        self.updateGeometry()

    def clear(self) -> None:
        self.chip = self.tier = self.school = ""


class _LinkedBox(QWidget):
    """编辑区（我的方案，mine_box）：显示、隐藏时连带 partners 一起。霓虹贴纸（side_column）把目标流派放在右边一栏的最上面，
    它和左边侧栏里的名称、作者、说明同属编辑区：选中默认推荐时一起藏起来。"""

    def __init__(self, partners=()):
        super().__init__()
        self.partners = list(partners)

    def setVisible(self, visible: bool) -> None:  # noqa: N802  Qt 的方法名
        super().setVisible(visible)
        for widget in self.partners:
            widget.setVisible(visible)


# ------------------------------------------------------------ 搭界面用的部件样子


def _plain_dim(text: str = "", name: str = "") -> QLabel:
    return _plain_label(text, name)


def _wrap_dim(text: str = "", name: str = "") -> QLabel:
    return _WrapLabel(text, name)


def _plain_warn(text: str = "", name: str = "") -> QWidget:
    return _plain_label(text, name)


def _wrap_warn(text: str = "", name: str = "") -> QWidget:
    return _WrapWarn(text, name=name)


def _plain_confirm() -> QWidget:
    return _plain_label("", "warn")


def _pill_confirm() -> QWidget:
    return chrome.WarnLabel(name="warn", badge=False)


def _game_confirm() -> QWidget:
    return _WrapWarn(name="warn", badge=False)


def _plain_buttons() -> tuple[QPushButton, ...]:
    return (_button(NEW_BUTTON), _button(COPY_BUTTON), _button(COPY_DEFAULT_BUTTON, accent="primary"),
            _button(DELETE_BUTTON, accent="warn"))


def _pill_buttons() -> tuple[QWidget, ...]:
    return (chrome.PillButton(NEW_BUTTON, height=40, px=14, seed=2, min_width=82),
            chrome.PillButton(COPY_BUTTON, height=40, px=14, seed=3, min_width=82),
            chrome.PillButton(COPY_DEFAULT_BUTTON, height=40, px=14, seed=4),
            chrome.PillButton(DELETE_BUTTON, kind="warn", height=36, px=13))


def _game_action_buttons() -> tuple[QWidget, ...]:
    return (chrome.GameButton(NEW_BUTTON, "primary", height=40, px=14, icon=ICON_NEW),
            chrome.GameButton(COPY_BUTTON, "secondary", height=40, px=14, icon=ICON_COPY),
            chrome.GameButton(COPY_DEFAULT_BUTTON, "secondary", height=40, px=14, icon=ICON_COPY),
            chrome.GameButton(DELETE_BUTTON, "danger", height=38, px=13, icon=ICON_DELETE))


def _plain_confirm_buttons() -> tuple[QPushButton, QPushButton]:
    return _button(DELETE_YES, accent="warn"), _button(DELETE_NO)


def _pill_confirm_buttons() -> tuple[QWidget, QWidget]:
    return (chrome.PillButton(DELETE_YES, kind="warn", height=36, px=13),
            chrome.PillButton(DELETE_NO, kind="light", height=36, px=13))


def _game_confirm_buttons() -> tuple[QWidget, QWidget]:
    return (chrome.GameButton(DELETE_YES, "danger", height=36, px=13),
            chrome.GameButton(DELETE_NO, "plain", height=36, px=13))


@dataclass(frozen=True)
class _Kit:
    """一种排法搭界面时用的部件样子（同一族的排法共用一套）。default 用 PLAIN，side_tabs 用 PILL，其余五种用 GAME。
    建界面只经 _make_* 照这个造部件和接线，摆放在 layouts.py 的各个 build_*；这样七种排法不用各抄一遍同一批部件。"""

    dim: Callable[..., QLabel]  # 暗色说明：游戏风格的排法折行（_wrap_dim），默认排法是普通标签
    warn: Callable[..., QWidget]  # 警告：游戏风格的排法是黑底黄字、垫底板（_wrap_warn），默认排法是普通标签
    confirm_label: Callable[[], QWidget]  # 删除的确认那一行说明
    hints: Callable[[], QWidget]  # 列表下面的规则提示框
    buttons: Callable[[], tuple[QWidget, ...]]  # 新建、复制、复制为我的方案、删除
    confirm_buttons: Callable[[], tuple[QWidget, QWidget]]  # 「删除」「取消」
    search_gap: int  # 搜索行的间距
    search_named: bool  # 搜索框、「只看已标记」带对象名，搜索框左端有放大镜
    fields_named: bool  # 名称输入框带对象名 nameEdit
    retain_saved: bool  # 「已保存」出现、消失时这一行不变高矮（上面的列表不跳）
    close_button: Callable[[], QWidget]  # 右下角的「关闭」（自己画外框时不用：×/「返回」就是关闭）


PLAIN_KIT = _Kit(dim=_plain_dim, warn=_plain_warn, confirm_label=_plain_confirm, hints=HintBox,
                 buttons=_plain_buttons, confirm_buttons=_plain_confirm_buttons, search_gap=8,
                 search_named=False, fields_named=False, retain_saved=False,
                 close_button=lambda: _button(CLOSE_BUTTON))
PILL_KIT = _Kit(dim=_wrap_dim, warn=_wrap_warn, confirm_label=_pill_confirm, hints=_ShortHints,
                buttons=_pill_buttons, confirm_buttons=_pill_confirm_buttons, search_gap=12,
                search_named=True, fields_named=True, retain_saved=True,
                close_button=lambda: chrome.PillButton(CLOSE_BUTTON, height=40, px=14, seed=12))
GAME_KIT = _Kit(dim=_wrap_dim, warn=_wrap_warn, confirm_label=_game_confirm, hints=_ShortHints,
                buttons=_game_action_buttons, confirm_buttons=_game_confirm_buttons, search_gap=12,
                search_named=True, fields_named=True, retain_saved=True,
                close_button=lambda: chrome.GameButton(CLOSE_BUTTON, "secondary", height=40, px=14))
