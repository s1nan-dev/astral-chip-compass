"""「编辑筹码方案」窗口里的小部件和「造部件的样子」：折行标签、目标流派一栏、规则提示框、软锁问话、
按字数限长的校验器，以及一组造按钮、警告的小函数和把同一族部件收成一套的 _Kit（PLAIN_KIT、PILL_KIT、GAME_KIT）。

七种排法搭界面时照 _Kit 造部件（造部件和接线在 window.py 的 _make_* 里），摆放见 layouts.py；
界面文字和规则常数见 notes.py。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap, QValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .. import chrome, lang, theme
from ...data import SCHOOLS
from ..widgets import ComboRowDelegate, draw_combo_arrow
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
    LIST_SEP,
    MAPS_ALL,
    MAPS_ALL_SHORT,
    MAPS_COUNT,
    MAPS_HINT,
    MAPS_LABEL,
    MAPS_TIP_CHECKED,
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
            pill = _plain_label(lang.t(school), "schoolPill")
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


class MapCombo(QComboBox):
    """适用地图的下拉多选：下拉框上写现在标了哪几张（一张都没标写「全地图通用」；标了几张、放不下时改写张数，再放不下
    才在末尾省略），点开列出每张地图，
    每一行前面一个勾选标记，勾一项、取消一项都发出 toggled（地图名, 是否标上）。

    点开以后勾选不收起下拉列表，点在外面或按 Esc 才收起（Qt 默认点一项就收起，这里不放行那一次）。滚轮不改勾选：滚动
    编辑区时不会把地图勾掉（同 WheelLockedCombo）。下拉列表的每一行照悬浮窗的下拉框画（ComboRowDelegate），勾选标记占
    左边的短条那里；皮肤写了 COMBO_ARROW 时右端的三角和别的下拉框一样自己画。

    set_maps 按方案里的地图勾选；方案里有一张已经结束、不再列出来的地图时，照样补一项，免得改别的地图时把这一张悄悄
    去掉。补的那一项只属于显示着的那一套：换显示别的方案时（set_maps 的 keep_extra 为假）拿掉，结束的地图不会被标到
    别的方案上。"""

    toggled = Signal(str, bool)

    def __init__(self, names, hint: str = MAPS_HINT, parent=None, display=None):
        super().__init__(parent)
        self._hint = hint
        # 显示名对照（繁体界面用繁体地图名）；itemData 一律是简体地图名，勾选、存盘按它
        self._display = display or str
        self._keep = False  # 这一下点（勾选、取消）不收起下拉列表
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.setView(QListView())  # 自己建视图，才挡得住勾选时收起；行宽也另行放宽
        self.view().setMouseTracking(True)
        # 装在 Qt 的下拉列表自己的事件过滤之后，先于它收到：按键、松开鼠标在它收起下拉列表之前处理
        self.view().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self.view().pressed.connect(self._row_pressed)
        self.setItemDelegate(ComboRowDelegate(self, self.itemDelegate(), None, self._row_checked))
        draw_combo_arrow(self)
        for name in names:
            self._add(str(name))
        self._listed = tuple(self.names())  # 一直列着的（能标的地图）；其余是 set_maps 补的
        self.setCurrentIndex(-1)
        self._refresh()

    def _add(self, name: str) -> int:
        self.addItem(self._display(name), name)
        return self.count() - 1

    def _index_of(self, name: str) -> int:
        for row in range(self.count()):
            if self.itemData(row) == name:
                return row
        return -1

    def names(self) -> tuple[str, ...]:
        """现在列着的地图，按下拉列表的先后。"""
        return tuple(self.itemData(row) for row in range(self.count()))

    def _row_checked(self, row: int) -> bool:
        item = self.model().item(row)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def set_checked(self, name: str, on: bool) -> None:
        """勾上或取消一项，不发信号（set_maps 用）。"""
        row = self._index_of(name)
        if row >= 0:
            self.model().item(row).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _toggle_row(self, row: int) -> None:
        name = self.itemData(row)
        if not name or not self.isEnabled():
            return
        self.set_checked(str(name), not self._row_checked(row))
        self._refresh()
        self.view().viewport().update()
        self.toggled.emit(str(name), self._row_checked(row))

    def _row_pressed(self, index) -> None:
        """按下鼠标就勾选（或取消）这一行。Qt 的下拉列表在松开这一下时收起（当作选中了一项）：挡住那一次（_keep），
        松开处理完以后清掉（eventFilter），下一次点在外面、按 Esc 照常收起。"""
        if not index.isValid():
            return
        self._keep = True
        self._toggle_row(index.row())

    def _clear_keep(self) -> None:
        self._keep = False

    def set_maps(self, names, keep_extra: bool = True) -> None:
        """按方案里标的地图勾选（勾选时不发信号）；不在列表里的（已经结束的地图）补一项。keep_extra 为假时（换显示
        别的方案），先拿掉以前补的、这一套没标的项；为真时（同一套方案改了地图）留着，刚去掉的还能再点回来。"""
        names = tuple(names)
        if not keep_extra:
            for name in [n for n in self.names() if n not in self._listed and n not in names]:
                row = self._index_of(name)
                if row >= 0:
                    self.removeItem(row)
        for name in names:
            if self._index_of(str(name)) < 0:
                self._add(str(name))
        for name in self.names():
            self.set_checked(name, name in names)
        self._refresh()

    def maps(self) -> tuple[str, ...]:
        """现在勾着的地图，按下拉列表的先后。"""
        return tuple(name for name in self.names() if self._row_checked(self._index_of(name)))

    def toggle(self, name: str) -> None:
        """按用户点这一项处理：改勾选、发 toggled。"""
        row = self._index_of(name)
        if row >= 0:
            self._toggle_row(row)

    def display_text(self) -> str:
        """下拉框上写的字的全文（放不下时画出来的是 _display_text：改写张数或末尾省略）。"""
        return self._maps_text()

    def set_enabled(self, on: bool) -> None:
        self.setEnabled(on)

    def _maps_text(self) -> str:
        maps = [self._display(name) for name in self.maps()]
        return LIST_SEP.join(maps) if maps else MAPS_ALL

    def _display_text(self, width: int) -> str:
        """下拉框上画的字：放得下写全；标了几张、放不下时写张数（MAPS_COUNT），张数也放不下才在末尾省略。只标了一张时
        照旧省略地图名（前几个字比「1 张」更看得出是哪一张）。一张都没标、「全地图通用」放不下时写「通用」（MAPS_ALL_SHORT），
        不省略成「全…」。"""
        metrics, width = QFontMetrics(self.font()), max(width, 1)
        text, maps = self._maps_text(), self.maps()
        if len(maps) > 1 and metrics.horizontalAdvance(text) > width:
            text = MAPS_COUNT.format(n=len(maps))
        elif not maps and metrics.horizontalAdvance(text) > width:
            text = MAPS_ALL_SHORT
        return metrics.elidedText(text, Qt.ElideRight, width)

    def _refresh(self) -> None:
        """下拉框上的字、悬停说明（写明全标了哪几张，省略了也看得全）按现在的勾选重写。"""
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self) -> str:
        maps = [self._display(name) for name in self.maps()]
        return self._hint + ("\n" + MAPS_TIP_CHECKED.format(maps=LIST_SEP.join(maps)) if maps else "")

    def showPopup(self) -> None:
        if self.currentIndex() < 0 and self.count():
            self.setCurrentIndex(0)
        widest = max((self.view().fontMetrics().horizontalAdvance(self.itemText(row))
                      for row in range(self.count())), default=0)
        self.view().setMinimumWidth(widest + 48)  # 勾选标记、两边的留白
        super().showPopup()

    def hidePopup(self) -> None:
        if self._keep:  # 刚勾了一行：下拉列表留着，可以接着勾
            self._keep = False
            return
        super().hidePopup()

    def eventFilter(self, obj, event) -> bool:
        view = self.view()
        kind = event.type()
        if obj is view.viewport() and kind == QEvent.MouseButtonRelease:
            # 按在一行上勾了（_row_pressed）：Qt 处理这一下松开时要收起下拉列表，被 hidePopup 挡住；松开处理完以后清掉
            # （按下以后拖到别处才松开、Qt 没去收起的也清掉）
            QTimer.singleShot(0, self, self._clear_keep)
            return False
        if obj is view and kind == QEvent.ShortcutOverride and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # 回车在 Qt 的下拉列表里是「选中这一项并收起」（在这一步做）：这里当普通按键，交给下面勾选
            event.accept()
            return True
        if obj is view and kind == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._toggle_row(max(view.currentIndex().row(), 0))
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def keyPressEvent(self, event) -> None:
        """空格、回车点开；下拉列表开着时，空格、回车勾选当前一行（不收起），上下键照常移动。"""
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if not self.view().isVisible():
                self.showPopup()
            else:
                self._toggle_row(max(self.view().currentIndex().row(), 0))
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        edit = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
        option.currentText = self._display_text(edit.width() - 2)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(200, hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(96, hint.height())


class MapField(QWidget):
    """适用地图一栏：「适用地图」和右边一个下拉多选（MapCombo），只占一行。"""

    toggled = Signal(str, bool)

    def __init__(self, names, parent=None, display=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(_plain_label(MAPS_LABEL, "field"))
        self.combo = MapCombo(names, parent=self, display=display)
        self.combo.toggled.connect(self.toggled.emit)
        lay.addWidget(self.combo, 1)

    def set_maps(self, names, keep_extra: bool = True) -> None:
        """见 MapCombo.set_maps。"""
        self.combo.set_maps(names, keep_extra)

    def maps(self) -> tuple[str, ...]:
        """现在勾着的地图，按下拉列表的先后。"""
        return self.combo.maps()

    def set_enabled(self, on: bool) -> None:
        self.combo.setEnabled(on)


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
    适用地图也在这个栏里，它们和左边侧栏里的名称、作者、说明同属编辑区：选中默认推荐时一起藏起来。"""

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
