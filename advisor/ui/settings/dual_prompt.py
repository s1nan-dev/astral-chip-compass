"""设置窗口里选了「Steam端+TapTap端」以后弹的性能测试对话框：先警告，再对着开着的两个游戏实测 30 秒，按结果决定开不开启。

流程（按钮见下面各常量）：
- 警告：说明同时识别两个客户端对性能有较大影响，按钮「开始性能测试」「取消」。
- 缺游戏窗口（悬浮窗的 dual_missing 返回缺的端）：写明要先打开两个游戏（缺一个时点名是哪个端），按钮照旧。
- 测试中：显示还剩几秒（整数，向上取整）和进度条，只有「取消」（关掉窗口、按 Esc 也是取消）。
- 结果：写明哪一档、这一档的意思、量到的三个数。余裕、正常时直接开启，按钮「好」；危险、临界、过载时按钮「打开低配模式
  再测」「无视风险，继续开启」「取消」，危险默认按「无视风险，继续开启」，临界、过载默认按「打开低配模式再测」
  （已经开着低配模式时第一个按钮换成「再测一次」）。只有档名那一段按档着色（SETTINGS.LEVEL_COLOR），说明和数字不变色。
- 测试中途停了（CheckResult.stopped，例如游戏窗口关了）：写出停下的原因，按钮「开始性能测试」「取消」，可以重测。

低配模式：为了重测而打开的低配模式，取消时关回去；原来就开着的不动。测试通过、点了「无视风险，继续开启」以后不关。

和悬浮窗的接口（设置窗口经 _call 按名字调用；这几个方法以后由悬浮窗实现）：
- dual_missing() -> list[str]：缺哪几个端的游戏窗口，端名是 capture.STEAM、capture.TAPTAP；都开着时返回空列表。
- start_dual_test(lowend: bool, progress, done) -> None：按 lowend 的节奏在后台开始测试；progress(已测秒数, 总秒数)
  和 done(CheckResult) 在界面线程上回调。
- cancel_dual_test() -> None：停下正在进行的测试（已经停下时什么都不做）。
- set_platform(value)：改成同时识别时传 capture.BOTH。
- set_lowend(on)：打开、关回低配模式，和设置窗口里低配模式的开关一样。

对话框跟着皮肤：默认皮肤用系统标题栏，其余皮肤和设置窗口一样自己画外框（chrome.Frame、WindowHead），只有一个关闭按钮。
没有用 QMessageBox 的信息、警告、错误图标：那几种图标类型在 Windows 上会放系统提示音。

不导入 advisor/ui/dual_check.py（测试的过程在悬浮窗那边跑，这里只拿结果）：它连带加载识别库（截图、识别流程），设置窗口
一导入就要加载；五档的取值另写一份（测试核对），端名在用到时才从 capture 取。
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QProgressBar, QVBoxLayout, QWidget

from .. import chrome, lang, theme
from ..text import rich
from ..theme import SETTINGS as L
from ..widgets import TextLabel
from ..window_base import SkinnedWindow
from .widgets import _button

if TYPE_CHECKING:
    from ..dual_check import CheckResult

# 五档的取值：同 advisor/ui/dual_check.py 的 EASY、NORMAL、DANGER、CRITICAL、OVERLOAD（测试核对；不导入它的原因见模块说明）
EASY, NORMAL, DANGER, CRITICAL, OVERLOAD = "easy", "normal", "danger", "critical", "overload"
# 各档出哪些按钮：余裕、正常直接开启；危险默认「无视风险，继续开启」；临界、过载默认「打开低配模式再测」。
# 以后要改哪一档的按钮，改这几个集合
DIRECT_OPEN = frozenset({EASY, NORMAL})
IGNORE_DEFAULT = frozenset({DANGER})
RETEST_DEFAULT = frozenset({CRITICAL, OVERLOAD})

# ------------------------------------------------------------ 界面文字

PROMPT_TITLE = "同时识别两个客户端"  # 窗口标题；自己画外框时顶上一行也写这句
WARNING = "警告，选择此项将会同时识别两个客户端的画面，对性能有较大影响。"
START = "开始性能测试"
CANCEL = "取消"
OK = "好"
IGNORE = "无视风险，继续开启"
LOWEND_RETEST = "打开低配模式再测"
RETEST = "再测一次"
# 缺游戏窗口：两个都缺时 {first}、{second} 按 capture.PLATFORM_NAMES 的先后填两个端名；缺一个时 {name} 填缺的那个
MISSING_ALL = "性能测试要两个客户端的游戏都开着（停在大厅就行，不要最小化）：请先把「{first}」和「{second}」都打开。"
MISSING_ONE = "性能测试要两个客户端的游戏都开着（停在大厅就行，不要最小化）：「{name}」的游戏窗口没找到，请先把它打开。"
TESTING = "性能测试中：正在统计性能…"
REMAIN = "性能测试中：还剩 {seconds} 秒"  # 整数秒，向上取整（remain_seconds）
RESULT_TITLE = "性能测试结果：{level}"
STOPPED_TITLE = "性能测试已停止"
STOPPED_FALLBACK = "性能测试已停止。"
LEVEL_NAME = {EASY: "余裕", NORMAL: "正常", DANGER: "危险", CRITICAL: "临界", OVERLOAD: "过载"}
LEVEL_MEANING = {
    EASY: "余裕：识别迅速，游戏不受影响。",
    NORMAL: "正常：识别偶尔稍慢，游戏基本不受影响。",
    DANGER: "危险：识别缓慢，游戏偶尔可能卡一下。",
    CRITICAL: "临界：识别反应迟钝，游戏容易卡。",
    OVERLOAD: "过载：识别延迟很高，游戏经常卡。",
}
SLOWEST = "三选一出推荐：最慢 {seconds:.1f} 秒"
# 一次都没量到（测得太短、读字出错）。测试不等三选一画面出现，对着大厅的画面量读字和算推荐（见 DualCheck），
# 所以不写「没有出现三选一」
NO_SLOWEST = "三选一出推荐：没有量到"
BUSY = "整机处理器被压满的时间：{share:.0%}"
OWN = "拾星助手占整机处理器：{share:.0%}"
APPLIED = "已开启「{name}」。"
# 默认按钮（每一步推荐点的那个）的样子。自己画外框的皮肤照设置窗口的主按钮（「导出诊断包」）配色：各皮肤的设置样式表按
# 这个对象名给它上色（霓虹贴纸的黄底、碳纤流光切角里的深色字等），没另配颜色的皮肤和别的按钮一样。默认皮肤的按钮都是深色底，
# 默认按钮另描一圈金边、写金字（DEFAULT_BUTTON_SHEET，只给默认皮肤）。不一律按选中色写字：有的皮肤按钮的底色就是选中色
PRIMARY_NAME = "exportButton"
DEFAULT_BUTTON_SHEET = 'QPushButton[default="true"] {{ border: 1px solid {color}; color: {color}; font-weight: bold; }}\n'


def missing_text(missing) -> str:
    """缺游戏窗口时的那一句：missing 是悬浮窗 dual_missing 给的端（capture.STEAM、capture.TAPTAP）。正好缺一个时点名是哪个端，
    其余（两个都缺、给了认不出的端）都按两个都缺说。端名取 capture.PLATFORM_NAMES（不另写一份）。"""
    from ...vision import capture

    names = capture.PLATFORM_NAMES
    gone = [key for key in names if key in set(missing or ())]
    if len(gone) == 1:
        return MISSING_ONE.format(name=lang.t(names[gone[0]]))
    return MISSING_ALL.format(first=lang.t(names[capture.STEAM]), second=lang.t(names[capture.TAPTAP]))


def remain_seconds(done: float, total: float) -> int:
    """还剩几秒（整数，向上取整，不小于 0）。先按毫秒取整再向上取整：已测的秒数是两个时刻相减得来的，带着浮点误差
    （例如 11.999999999），直接向上取整会多出一秒。"""
    return max(0, math.ceil(round(max(float(total) - float(done), 0.0), 3)))


def result_texts(result: CheckResult) -> tuple[str, str, str]:
    """一次性能测试结果的三段文字：（标题，这一档的意思，量到的三个数）。秒保留一位小数，百分比取整；一次都没量到
    三选一延迟时写明，不写 0 秒。"""
    level = result.level
    title = RESULT_TITLE.format(level=LEVEL_NAME[level])
    meaning = LEVEL_MEANING[level]
    slowest = NO_SLOWEST if result.slowest_s is None else SLOWEST.format(seconds=result.slowest_s)
    detail = "\n".join((slowest, BUSY.format(share=result.busy_share), OWN.format(share=result.own_share)))
    return title, meaning, detail


def level_color(level: str) -> str:
    """这一档档名的颜色（按当前皮肤取 SETTINGS.LEVEL_COLOR）；没配时用正文颜色。"""
    return theme.SETTINGS.LEVEL_COLOR.get(level, theme.SETTINGS.TEXT_COLOR)


class _TitleLabel(TextLabel):
    """对话框顶上那一行：结果那一步只给档名的一段上色（SETTINGS.LEVEL_COLOR），其余几步是纯文字。
    text() 仍是原文，测试和截图按原文核对。"""

    def __init__(self):
        super().__init__("", "rowTitle", wrap=False)
        self._plain = ""

    def setText(self, text: str) -> None:  # noqa: N802  Qt 的方法名
        self._plain = text
        self.setTextFormat(Qt.PlainText)
        super().setText(text)

    def set_level(self, prefix: str, level: str, color: str) -> None:
        """写上「{prefix}{档名}」，只给档名着色。"""
        self._plain = prefix + level
        self.setTextFormat(Qt.RichText)
        QLabel.setText(self, rich([(prefix, None), (level, color)]))

    def text(self) -> str:
        return self._plain

    def html(self) -> str:
        """富文本显示时的原文（不是富文本时为空），测试用。"""
        return QLabel.text(self) if self.textFormat() == Qt.RichText else ""


class DualPromptDialog(SkinnedWindow, chrome.HideNotice, QDialog):
    """「Steam端+TapTap端」的性能测试对话框（流程见模块说明）。host 是悬浮窗，lowend 是当前的低配模式开关。测试的过程
    由悬浮窗在后台跑，回调在界面线程上调用 _progress、_done。"""

    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 和设置窗口一样不总在最前
    # 最窄的宽度（逻辑像素）。按钮一行排不下时按排得下所需的宽度加宽（fit_size）
    MIN_WIDTH = 360
    HEADER_GAP = 8  # 左上角「返回」和正中的标题页签之间至少留这么宽

    def __init__(self, host, parent=None, lowend: bool = False):
        super().__init__(parent)
        self._host = host
        self._lowend = bool(lowend)  # 现在的低配模式开关
        self._lowend_set = False  # 这一次为了重测打开的低配模式（取消时要关回去）
        self._testing = False
        self._applied = False  # 已经开启同时识别（set_platform 调用过）
        self._phase = "warning"
        self._missing: list[str] = []  # 上一次 dual_missing 给的缺的端（缺游戏窗口那一步按它写）
        self._result: CheckResult | None = None
        self._elapsed = 0.0
        self._total: float | None = None
        self._page: QWidget | None = None
        self.buttons: dict[str, object] = {}
        self.default_button = None
        self.setWindowTitle(PROMPT_TITLE)
        self.setWindowFlags(self.FLAGS)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._page = self._build_page()
        self._outer.addWidget(self._page)
        self._render()
        self.subscribe_skin()

    # ---- 搭窗口（换皮肤时重建）

    @property
    def applied(self) -> bool:
        """测试通过、已经开启同时识别（设置了 game platform）时为真；取消时为假。"""
        return self._applied

    def _build_page(self) -> QWidget:
        """按当前皮肤搭这一页：内容放在卡片里（同设置窗口的行）；自己画外框的皮肤用 chrome.Frame、chrome.Card 和顶上
        一行，默认皮肤用系统标题栏和设置样式表里的卡片。默认按钮的样子见 PRIMARY_NAME。"""
        sheet = theme.settings_style()
        if not chrome.custom():
            sheet += DEFAULT_BUTTON_SHEET.format(color=L.SELECTED_COLOR)
        self.setStyleSheet(sheet)
        if chrome.custom():
            page = chrome.Frame()
            root = QVBoxLayout(page)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            head = chrome.WindowHead(PROMPT_TITLE)
            self.close_btn = head.closer
            root.addWidget(head)
            body = QVBoxLayout()
            body.setContentsMargins(12, 12, 12, 12)
            root.addLayout(body, 1)
            card = chrome.Card("", margins=(14, 12, 14, 12))
            card.box.setSpacing(8)
            body.addWidget(card, 1)
            column = card.box
        else:
            page = QWidget()
            page.setObjectName("dualPrompt")
            self.close_btn = None
            outer = QVBoxLayout(page)
            outer.setContentsMargins(12, 12, 12, 12)
            card = QFrame()
            card.setObjectName("card")
            column = QVBoxLayout(card)
            column.setContentsMargins(14, 12, 14, 12)
            column.setSpacing(8)
            outer.addWidget(card, 1)
        self.title_label = _TitleLabel()
        self.message = TextLabel("", "rowDesc")
        self.detail = TextLabel("", "rowDesc")
        self.note = TextLabel("", "rowDesc")
        self.progress_label = TextLabel("", "rowDesc", wrap=False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        # 进度条和设置窗口的开关同一套颜色：走过的那段是开关开着时轨道的颜色，没走到的照开关关着时只描一圈框。不用键帽的
        # 底色当轨道：有的皮肤键帽是白底、选中色也是白色，走过的那段就看不出来了
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: transparent; border: 1px solid {L.SWITCH_OFF_BORDER}; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background: {L.SWITCH_ON_FILL or L.SELECTED_COLOR}; border-radius: 4px; }}")
        self.button_row = QWidget()
        line = QHBoxLayout(self.button_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addStretch(1)
        column.addWidget(self.title_label)
        column.addWidget(self.message)
        column.addWidget(self.progress_label)
        column.addWidget(self.progress)
        column.addWidget(self.detail)
        column.addWidget(self.note)
        column.addStretch(1)
        column.addWidget(self.button_row)
        chrome.frameless(self, chrome.custom(), self.FLAGS)
        return page

    def apply_skin(self, skin=None) -> None:
        """换皮肤：按新皮肤重建内容，正在显示的那一步（警告、测试中、结果）照旧。"""
        if self._page is None:
            return
        old = self._page
        self._outer.removeWidget(old)
        old.hide()
        old.deleteLater()
        self._page = self._build_page()
        self._outer.addWidget(self._page)
        if self.isVisible():
            # 先让新的一页排好版再量宽度：还没排过版的部件一律是 640 宽，拿它和按钮行比较就量不出该加多宽
            self._page.show()
            self.show()
        self._render()

    # ---- 显示哪一步

    def _render(self) -> None:
        """按现在的阶段填字和按钮。"""
        self._clear_buttons()
        self.default_button = None
        self.title_label.hide()
        self.message.show()
        self.progress_label.hide()
        self.progress.hide()
        self.detail.hide()
        self.note.hide()
        phase = self._phase
        if phase == "warning":
            self.message.setText(WARNING)
            self._add_button(START, self._start, default=True)
            self._add_button(CANCEL, self.reject)
        elif phase == "missing":
            self.message.setText(missing_text(self._missing))
            self._add_button(START, self._start, default=True)
            self._add_button(CANCEL, self.reject)
        elif phase == "testing":
            self.message.hide()
            self.progress_label.setText(TESTING if self._total is None else self._remain())
            self.progress_label.show()
            self.progress.show()
            self._add_button(CANCEL, self._cancel_test)
        elif phase == "stopped":
            self.title_label.setText(STOPPED_TITLE)
            self.title_label.show()
            reason = self._result.reason if self._result is not None else ""
            self.message.setText(reason or STOPPED_FALLBACK)
            self._add_button(START, self._start, default=True)
            self._add_button(CANCEL, self.reject)
        elif phase == "result" and self._result is not None:
            _, meaning, numbers = result_texts(self._result)
            level = self._result.level
            self.title_label.set_level(RESULT_TITLE.format(level=""), LEVEL_NAME[level], level_color(level))
            self.title_label.show()
            self.message.setText(meaning)
            self.detail.setText(numbers)
            self.detail.show()
            if level in DIRECT_OPEN:
                self.note.setText(APPLIED.format(name=both_name()))
                self.note.show()
                self._add_button(OK, self.accept, default=True)
            else:
                self._add_button(LOWEND_RETEST if not self._lowend else RETEST, self._retest,
                                 default=level in RETEST_DEFAULT)
                self._add_button(IGNORE, self._ignore, default=level in IGNORE_DEFAULT)
                self._add_button(CANCEL, self.reject)
        self.fit_size()

    def _remain(self) -> str:
        return REMAIN.format(seconds=remain_seconds(self._elapsed, self._total or 0.0))

    def _clear_buttons(self) -> None:
        layout = self.button_row.layout()
        for i in reversed(range(layout.count())):
            widget = layout.itemAt(i).widget()
            if widget is not None:
                layout.takeAt(i)
                widget.hide()
                widget.deleteLater()
        self.buttons = {}

    def _add_button(self, text: str, slot, default: bool = False):
        button = _button(text)
        button.clicked.connect(slot)
        if default:
            button.setDefault(True)
            button.setProperty("default", True)
            button.setObjectName(PRIMARY_NAME)
            self.default_button = button
        if theme.CHROME.CHAMFER:
            chrome.cut_corners(button, "primary" if default else "plain")
        self.button_row.layout().addWidget(button)
        # 对话框已经显示着时，往布局里加的新控件 Qt 先当作隐藏的，排到下一轮事件循环才显示。这一步马上要量高度定死窗口，
        # 量的时候没把按钮这一行算进去，等按钮显示出来就只能把这一列一起往下压（字只露出上半截）。先显示出来再量。
        button.show()
        self.buttons[text] = button
        return button

    def fit_size(self) -> None:
        """宽度不小于这一步的最窄宽度（MIN_WIDTH，有的皮肤另要够外框的装饰，见 _min_width）：按钮一行排得下时就是最窄
        宽度，排不下时只加宽到刚好排得下（按钮不缩、不换行）。每一步、换皮肤后都重算。高度按这一步的内容量（文字换行后
        变高，按钮多了也放得下）。"""
        self.ensurePolished()
        base = self._min_width()
        self.setFixedWidth(base)
        width = base
        self._settle_layout()
        # 按钮行的宽度是窗口宽度减去各层边距；按钮一行所需宽度多于现在分到的宽度时，把差的这一截加到窗口宽度上。加宽后
        # 边距不变，差多少就补多少；再量一次兜住取整的误差
        for _ in range(2):
            gap = self.button_row.sizeHint().width() - self.button_row.width()
            if gap <= 0:
                break
            width += gap
            self.setFixedWidth(width)
            self._settle_layout()
        layout = self._page.layout()
        layout.activate()
        # 换行的标签自己估的 sizeHint() 按它当时的宽度算行数，比这个固定宽度下实际多，取它的高度会在按钮上面多留一块
        # 空白。按这个宽度下实际要的高度算；布局给不出（-1，或者不是正数）时才退回 sizeHint
        need = layout.totalHeightForWidth(width) if hasattr(layout, "totalHeightForWidth") else -1
        self.setFixedHeight(need if need > 0 else self._page.sizeHint().height())

    def _min_width(self) -> int:
        """宽度的下限：MIN_WIDTH。标题页签压在正中、左上角又是「返回」的皮肤（经典主题-白）还要够这两样并排、不互相
        压住：页签居中，左右各留出「返回」所占的位置和一点空白。"""
        head = self._page.findChild(chrome.WindowHead)
        if head is None or not isinstance(head.closer, chrome.BackButton) or not isinstance(head.title, chrome.TitleTab):
            return self.MIN_WIDTH
        reserve = chrome.K.BACK_POS[0] + chrome.K.BACK_SIZE[0] + self.HEADER_GAP
        return max(self.MIN_WIDTH, 2 * reserve + head.title.sizeHint().width())

    def _settle_layout(self) -> None:
        """把宽度改完以后的排版落定：重算每个看得见的部件的尺寸，让按钮行拿到新的宽度。"""
        self._outer.activate()
        page = self._page
        for widget in page.findChildren(QWidget):
            if widget.isVisibleTo(page):
                widget.updateGeometry()
        for nested in page.findChildren(QLayout):
            nested.invalidate()
        layout = page.layout()
        layout.activate()
        self.button_row.layout().activate()

    # ---- 按钮和悬浮窗的回调

    def _start(self) -> None:
        """「开始性能测试」：先问缺不缺游戏窗口，都开着就开始测。"""
        missing = self._call("dual_missing")
        if missing:
            self._missing = list(missing)
            self._phase = "missing"
            self._render()
            return
        if not callable(getattr(self._host, "start_dual_test", None)):
            return
        self._run_test()

    def _run_test(self) -> None:
        self._testing = True
        self._phase = "testing"
        self._elapsed = 0.0
        self._total = None
        self.progress.setValue(0)
        self._render()
        self._call("start_dual_test", self._lowend, self._progress, self._done)

    def _retest(self) -> None:
        """「打开低配模式再测」「再测一次」：没开低配模式时先打开（记下是这一次打开的，取消时关回去），按低配的节奏再测。"""
        if not self._lowend:
            self._lowend = True
            self._lowend_set = True
            self._call("set_lowend", True)
        self._run_test()

    def _ignore(self) -> None:
        """「无视风险，继续开启」：开启同时识别，关掉对话框。"""
        self._apply()
        self.accept()

    def _apply(self) -> None:
        if not self._applied:
            from ...vision import capture

            self._applied = True
            self._call("set_platform", capture.BOTH)

    def _progress(self, done: float, total: float) -> None:
        """测试进行中（界面线程回调）：还剩几秒、进度条。已经不在测试中（点了取消）时不再更新。"""
        if not self._testing:
            return
        self._elapsed, self._total = float(done), float(total)
        self.progress_label.setText(self._remain())
        if total > 0:
            self.progress.setValue(round(max(0.0, min(1.0, done / total)) * 1000))

    def _done(self, result: CheckResult) -> None:
        """测试结束（界面线程回调）：停下了写原因，否则按结果分档；余裕、正常时直接开启。"""
        if not self._testing:
            return
        self._testing = False
        self._result = result
        if result.stopped:
            self._phase = "stopped"
        else:
            self._phase = "result"
            if result.level in DIRECT_OPEN:
                self._apply()
        self._render()

    def _cancel_test(self) -> None:
        """测试中点的「取消」：先叫停后台的测试，再按取消关掉对话框。"""
        self._testing = False
        self._call("cancel_dual_test")
        self.reject()

    # ---- 关窗口

    def reject(self) -> None:
        """取消（「取消」、Esc、标题栏的 ×）：测试正在跑的先停；这一次为了重测打开的低配模式关回去；关掉对话框。
        已经开启（点过「无视风险，继续开启」）时只关窗口，不关低配模式。"""
        if self._testing:
            self._testing = False
            self._call("cancel_dual_test")
        if not self._applied and self._lowend_set and self._lowend:
            self._call("set_lowend", False)
            self._lowend = False
            self._lowend_set = False
        super().reject()

    def _call(self, name: str, *args):
        fn = getattr(self._host, name, None)
        return fn(*args) if callable(fn) else None


def both_name() -> str:
    """同时识别在界面上用的名字（取 capture.BOTH_NAME，不另写一份）。"""
    from ...vision import capture

    return lang.t(capture.BOTH_NAME)


lang.localize(globals())
