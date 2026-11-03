"""「导入/导出筹码方案」窗口：悬浮窗「筹码方案」卡片的「导入/导出」按钮打开。

不分页签，导出在上、导入在下：
- 导出：按角色筛选、勾选我的方案，选择格式（短字符串，或列出全部筹码的可读文字）；预览框里就是要复制的内容（每套
  一行），「复制到剪贴板」把它原样放进剪贴板。「全选」后复制，就是导出当前筛选下的全部方案。默认的角色筛选由 hero
  给出（None 为全部角色）；给了角色时预先勾选该角色正在用的方案。角色下拉框只列有我的方案的角色（筛选中的角色
  没有方案时也列出），下拉列表每一行的画法和悬浮窗相同（widgets.draw_combo_rows）。上次选的格式在本次运行里记住
  （类属性 last_format，各窗口共用；要跨重启记住时由悬浮窗存进设置：format_changed、set_format）。
- 导入：粘贴框里可以一次粘贴多套，前后带别的文字也行；内容变了以后稍等片刻（PARSE_DELAY_MS）自动识别，逐套显示
  结果：能导入的写角色、名称、目标流派（由标记推出来，schemes.target_schools）和各档个数，有问题的写「第 N 套（名称）：」
  和原因（问题的种类由 advisor/schemes.py 的 parse_share 给出，句子在这里拼），不能导入的那一套不导入。
  「导入 N 套为我的方案」只新建、不覆盖已有的方案，同名时名称后加「（2）」；导入后清空粘贴框，在识别结果的位置一套一行写明
  导入了哪几套，并发出 schemes_imported（导入的方案所属的角色名，不重复，按先后）。导入不自动换用：每一行右边一个「使用这套
  方案」按钮，点了发出 use_requested（所属角色, 方案 id），悬浮窗把它设为这个角色用的方案（所属角色正是悬浮窗当前的角色时
  当场换用），再把结果交回来（show_use_result），按钮换成结果那一句。一次导入好几套时每套各自一个按钮，行多了在识别结果区里
  上下滚动，窗口不跟着变高。方案文件只读（来自更新版本的拾星助手）时不能导入，导出照常。

一次最多识别 schemes.PASTE_MAX_SCHEMES 套、前 schemes.PASTE_MAX_CHARS 个字，超出的部分另起一行说明。parse_share 用的
模糊匹配对象是共用的，不能在两个线程里同时调用：识别只在界面线程里做（2 万字的粘贴约 0.1 秒）。

名称、作者、说明和未能识别的筹码名都是别人写的文字，一律按纯文本显示：识别结果是富文本，每一行整体转义后再着色；
方案列表的复选框文字里 & 写成 &&（否则会被当成快捷键的标记吃掉）；预览框和粘贴框是纯文本框。

窗口和悬浮窗、编辑窗口共用同一个 SchemeStore，自己不缓存方案：显示和重新激活时按 store 的最新内容刷新（refresh），
别处改了方案时也可以直接调 refresh。不碰 Session 和悬浮窗：换用方案经 use_requested 交给悬浮窗做。

样式表和识别结果每一行的颜色在皮肤里（theme.share_style、theme.ShareLook）；换皮肤时（theme.set_skin）当场换（apply_skin）。
怎么排按当前皮肤（theme.Layouts.SHARE，见 LAYOUTS）。
"""
from __future__ import annotations

import dataclasses
import html
import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpacerItem, QStackedWidget, QVBoxLayout, QWidget, QWidgetItem,
)

from .. import schemes as S
from ..data import TIERS, heroes_in_group_order
from . import chrome, theme
from .theme import SHARE as L
from .widgets import FlowLayout, draw_combo_rows, fill_hero_combo, free_item
from .window_base import SkinnedWindow

log = logging.getLogger(__name__)

# ------------------------------------------------------------ 界面文字

TITLE = "导入/导出筹码方案"  # 窗口标题，也写在窗口顶部
CLOSE = "关闭"
# 左侧页签的排法（side_tabs）：两个页签；页签和卡片的标签写「导出」「导入」，卡片里的说明去掉 EXPORT_HEAD、IMPORT_HEAD
# 开头的「导出：」「导入：」
TAB_EXPORT = "导出"
TAB_IMPORT = "导入"
# 窗口底部的说明
SHARE_NOTE = "短字符串只是把方案内容编码压缩成一串字符，便于复制，不是保密手段：任何装了拾星助手的人都能导入并看到内容。"

# 导出
EXPORT_HEAD = "导出：勾选方案，选择格式后点「复制到剪贴板」。"
HERO_LABEL = "角色"
ALL_HEROES = "全部角色"
SELECT_ALL = "全选"
FORMAT_LABEL = "格式"
# 两种格式的单选按钮（键同 schemes.FORMAT_SHORT、FORMAT_TEXT），和复制结果那句里的叫法
FORMAT_OPTION = {"short": "短字符串（约 20～70 个字符）", "text": "可读文字（列出全部筹码，较长）"}
FORMAT_NAME = {"short": "短字符串", "text": "可读文字"}
ITEM = "{hero} · {name}"  # 方案列表的一行：角色名 · 方案名
# 还没有一套我的方案、导出区的列表空着时，列表里写下一步怎么做（有了方案就收起）
EXPORT_EMPTY = ("还没有可以导出的方案。导出的是自己的方案：点击悬浮窗「筹码方案」卡片的「编辑方案」按钮，新建或复制一套我的方案"
                "以后，这里就会列出来；导入的方案也会列在这里。")
COPY = "复制到剪贴板"
COPIED = "已复制 {n} 套方案的{format}"
COPY_FAILED = "复制失败，请稍后再试。"  # 剪贴板被别的程序占着、内容没写进去时（读回来核对），写在导出这一块的警告里
# 筹码编号表读不出来（data/chip_codes.json 缺失或损坏）时短字符串生成不了，这几套不列入预览
EXPORT_FAILED = "{n} 套方案无法生成短字符串（筹码编号表读取失败），未列入；请改用可读文字"

# 导入
IMPORT_HEAD = "导入：把方案文字或短字符串粘贴到下面（可以一次粘贴多套，每套一行）"
FOUND = "识别到 {n} 套："
FOUND_NONE = "识别到 0 套"  # 粘贴的内容里没有找到方案时（后面没有逐套的结果，不写冒号）
# 能导入的一套：「✓ 凛 · 标记充能　目标流派 标记、充能；必选 2、推荐 4、不选 3」。没有目标流派、没标筹码的那一项不写，
# 只写标了的档位（个数为 0 的不写）
RESULT_OK = "✓ {hero} · {name}"
RESULT_DETAIL = "　{detail}"
RESULT_SCHOOLS = "目标流派 {schools}"
RESULT_COUNT = "{tier} {n}"
RESULT_SEP = "；"
LIST_SEP = "、"
# 有问题的一套：每个问题一行，前面写第几套和名称
PROBLEM_LINE = "⚠ 第 {index} 套（{name}）：{text}"
# 问题的句子，键是 schemes.Problem.kind（schemes.PROBLEM_*）；schemes.FATAL_PROBLEMS 里的几种让这一套无法导入。
# codes_unavailable：筹码编号表（data/chip_codes.json）读不出来（程序文件不完整时才会出现），短字符串里的筹码认不出
PROBLEM_TEXT = {
    "bad_code": "字符串不完整或被改动（校验不符），无法导入；请粘贴完整的一串",
    "bad_text": "方案文字格式不正确，无法导入；请粘贴完整的一段",
    "newer_version": "这一套来自更新版本的拾星助手，当前版本无法导入",
    "codes_unavailable": "筹码编号表读取失败，无法导入短字符串；请改为粘贴方案文字",
    "unknown_hero_id": "角色编号 {hero_id} 资料库未收录，已跳过",
    "unknown_hero": "角色「{hero}」资料库未收录，已跳过",
    "unknown_codes": "{count} 个筹码不在当前版本的资料库中（可能来自更新版本的拾星助手），已忽略",
    "removed_chips": "已忽略已移除的筹码：{names}",
    "unknown_names": "未能识别的筹码名：{names}（已忽略）",
    "name_truncated": "名称超过 12 个字，已截断",
    "note_truncated": "说明超过 60 个字，已截断",
}
# 一次粘贴超出处理上限时（schemes.PASTE_MAX_CHARS、PASTE_MAX_SCHEMES）
TEXT_CUT = "⚠ 粘贴的内容超过 2 万字，只识别了前 2 万字；其余部分请分次粘贴"
SKIPPED = "⚠ 另有 {n} 套超出一次最多 50 套的限制，未识别；请分次粘贴"
IMPORT = "导入 {n} 套为我的方案"
CLEAR = "清空"
IMPORT_NOTE = "导入后新建为我的方案，不覆盖已有的；同名时名称后加「（2）」"
IMPORTED = "已导入筹码方案「{name}」（{hero}）"
# 导入以后每一套「已导入……」那一行右边的按钮：设为它所属角色使用的方案（导入时不自动换用）。点了以后按钮换成结果：
# 所属角色正是悬浮窗当前的角色时悬浮窗当场换用（USED_NOW）；别的角色只记下，下次玩这个角色时用它（USED_LATER）；
# 这套方案已经在别处删掉了时写 USE_GONE
USE = "使用这套方案"
USED_NOW = "悬浮窗已改用这套方案"
USED_LATER = "已设好，下次玩{hero}时用这套方案"
USE_GONE = "这套方案已被删除"
# 方案文件来自更新版本的拾星助手（只读）时，导入按钮下面的提示（和悬浮窗的方案文件提示同一句）
READ_ONLY = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"

# ------------------------------------------------------------ 尺寸和样式

# 打开时的大小（SIZE，逻辑像素；可以拖大，屏幕放不下时按可用高度缩小）和按屏幕可用高度缩小时给标题栏和边框留的高度
# （SCREEN_MARGIN）在皮肤里（theme.ShareLook），这两个名字也能按属性取（__getattr__）
PARSE_DELAY_MS = 100  # 粘贴框的内容变了以后，停这么久没有再变才识别（连续输入时不每个字都识别一次）
LIST_MIN_H = 56  # 方案列表至少约两行高
RESULTS_MIN_H = 44
# 预览框、粘贴框的（首选高度, 最矮）：平时约三行，窗口放不下时约两行；多出来的高度给方案列表和识别结果区
PREVIEW_H = (64, 44)
PASTE_H = (64, 48)
GAME_LIST_MIN_H = 84  # 经典主题-黑、反色的排法：方案列表至少两行高（每行是带底色的一块，比默认的高）
GAME_PREVIEW_H = (46, 38)  # 经典主题-反色（上下两张卡片）和霓虹贴纸、白色海景房（RGB_KIT）的预览框：约一行半
# 怎么排：排法的名字 → 搭界面的方法名（theme.Layouts.SHARE，不认识的按 default）。default：导出在上、导入在下；
# side_tabs：游戏内风格，左侧「导出」「导入」两个页签；side_by_side：导出、导入两块左右并排（经典主题-黑）；stacked：
# 导出、导入两张卡片上下排，最下面一条横幅写短字符串的说明（经典主题-反色）；banded：导出、导入两块上下排，每块上面一条
# 黑色斜带写「导出」「导入」和说明（霓虹贴纸）；capsules：上下两张白卡片，卡片里左上角一枚渐变胶囊写「导出」「导入」，
# 勾选方案是一排可点的胶囊、格式是分段按钮（白色海景房）
LAYOUTS = {"default": "_build", "side_tabs": "_build_tabs", "side_by_side": "_build_columns", "stacked": "_build_stacked",
           "banded": "_build_banded", "capsules": "_build_capsules"}
# split：导出、导入两块左右并排，中间一条竖线，每块上面是编号的分组标题（碳纤流光）
LAYOUTS["split"] = "_build_split"
ICON_COPY = 0xE8C8  # 「复制到剪贴板」前面的系统图标（复制）
ICON_ADD = 0xE710  # 「导入 N 套为我的方案」前面的系统图标（加号）

# 识别结果每一行的颜色、样式表在皮肤里（theme.ShareLook 的 TONE_COLOR，theme.share_style）。这两个名字（TONE_COLOR、STYLE）
# 也能按属性取，见 __getattr__


def __getattr__(name: str):
    """颜色常量名和样式表（scheme_share.TONE_COLOR、STYLE 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.share_style()
    return theme.legacy(__name__, name, ("share", "common"))


def button_text(text: str) -> str:
    """按钮、复选框上的文字原样显示：& 写成 &&（单个 & 会被当成快捷键的标记，不显示出来）。"""
    return text.replace("&", "&&")


def result_html(lines: list[tuple[str, str]]) -> str:
    """识别结果 [(色调, 文字)] → 富文本：每一行整体转义（文字里的网页标记原样显示），保留空格，按色调着色（当前皮肤）；
    皮肤给这种色调写了色块（ShareLook.TONE_BOX）时整行写在色块里。"""
    colors = L.TONE_COLOR
    out = []
    for tone, text in lines:
        box = L.TONE_BOX.get(tone)
        if box:
            fill, ink = box
            out.append(f'<table cellspacing="0" cellpadding="5" width="100%" style="background-color:{fill}; '
                       f'margin-bottom:4px"><tr><td style="color:{ink}; white-space:pre-wrap; font-weight:bold">'
                       f'{html.escape(text)}</td></tr></table>')
        else:
            out.append(f'<div style="color:{colors.get(tone, L.TEXT_COLOR)}; white-space:pre-wrap">'
                       f'{html.escape(text)}</div>')
    return "".join(out)


def problem_text(problem: S.Problem) -> str:
    """一个问题的句子（不含「第 N 套（名称）：」）。不认识的种类为空文字。"""
    template = PROBLEM_TEXT.get(problem.kind)
    if template is None:
        return ""
    return template.format(count=problem.count, names=LIST_SEP.join(problem.names), hero_id=problem.hero_id,
                           hero=problem.hero)


def ok_line(scheme: S.Scheme, db) -> str:
    """能导入的一套：「✓ 角色 · 名称　目标流派 X、Y；必选 2、推荐 4」。目标流派由标记推出来（列进方案的流派里有必选、
    推荐的，schemes.target_schools；db 是资料库），分享格式里不带。"""
    parts = []
    schools = S.target_schools(db, scheme.tiers)
    if schools:
        parts.append(RESULT_SCHOOLS.format(schools=LIST_SEP.join(schools)))
    counts = scheme.counts()
    marks = LIST_SEP.join(RESULT_COUNT.format(tier=S.TIER_LABEL[t], n=counts[t]) for t in TIERS if counts[t])
    if marks:
        parts.append(marks)
    line = RESULT_OK.format(hero=scheme.hero, name=scheme.name)
    if parts:
        line += RESULT_DETAIL.format(detail=RESULT_SEP.join(parts))
    return line


def result_lines(result: S.ParseResult, db) -> list[tuple[str, str]]:
    """识别结果的各行 [(色调, 文字)]：「识别到 N 套：」，逐套写能导入的（✓）和每个问题（⚠），最后写超出上限的部分。
    db 是资料库（推导目标流派用）。"""
    n = len(result.items)
    lines = [("dim", FOUND.format(n=n) if n else FOUND_NONE)]
    for item in result.items:
        if item.scheme is not None:
            lines.append(("ok", ok_line(item.scheme, db)))
        for p in item.problems:
            text = problem_text(p)
            if text:
                lines.append(("warn", PROBLEM_LINE.format(index=item.index, name=item.name, text=text)))
    if result.text_cut:
        lines.append(("warn", TEXT_CUT))
    if result.skipped:
        lines.append(("warn", SKIPPED.format(n=result.skipped)))
    return lines


@dataclass
class _Imported:
    """导入以后的一套（识别结果区里一行）：方案 id、所属角色、名称，点了「使用这套方案」以后的结果（空为还没点；now 悬浮窗
    已改用、later 已设为那个角色用的方案、gone 这套方案已不在）。"""

    scheme_id: str
    hero: str
    name: str
    result: str = ""

    def line(self) -> str:
        """那一行左边的字：「已导入筹码方案「X」（芬妮）」。"""
        return IMPORTED.format(name=self.name, hero=self.hero)

    def outcome(self) -> tuple[str, str]:
        """点过「使用这套方案」以后按钮换成的那一句和它的样式名（样式表的 useNote 绿色小字、useNoteWarn 橙色小字）；还没点时
        为空。"""
        if self.result == "now":
            return USED_NOW, "useNote"
        if self.result == "later":
            return USED_LATER.format(hero=self.hero), "useNote"
        if self.result == "gone":
            return USE_GONE, "useNoteWarn"
        return "", ""


def _label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _button(text: str, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if primary:
        btn.setProperty("accent", "primary")
    return btn


def _box() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("box")
    box = QVBoxLayout(frame)
    box.setContentsMargins(10, 8, 10, 10)
    box.setSpacing(6)
    return frame, box


class _TextBox(QPlainTextEdit):
    """纯文本框，首选高度和最矮高度按 heights =（首选, 最矮）定（QPlainTextEdit 默认的首选高度约 190，太高）。"""

    def __init__(self, heights: tuple[int, int], read_only: bool = False):
        super().__init__()
        self._heights = heights
        self.setReadOnly(read_only)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._heights[0])

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._heights[1])


class _FitArea(QScrollArea):
    """游戏内风格排法的识别结果区：宽度变了（窗口改大小、外面的滚动区出现或去掉滚动条）时调用 on_width，按新的宽度重新
    定它的高（SchemeShareWindow._fit_results）：不然多出来的几行只能在区里滚动着看。prefer_content 为 True 时首选高度就是
    定好的最高（内容的高度）：两张卡片放在外面的滚动区里按首选高度排时（stacked），结果区整个排开，不缩成最矮的一行。"""

    prefer_content = False

    def __init__(self, on_width):
        super().__init__()
        self._on_width = on_width
        self._width = -1

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.prefer_content and self.maximumHeight() < 10000:
            hint.setHeight(max(self.minimumHeight(), self.maximumHeight()))
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != self._width:
            self._width = event.size().width()
            self._on_width()


class _FlowList(FlowLayout):
    """勾选方案排成一排可点的胶囊（capsules 排法），放不下时换行。和竖排的方案列表同样用 insertWidget 插在最后一项（一个
    不显示、不占地方的空白）前面，_fill_list 不用分排法。"""

    def __init__(self, parent=None):
        super().__init__(parent, 8, 8)
        self.addItem(QSpacerItem(0, 0))

    def insertWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802  同 QBoxLayout 的方法名
        self.addChildWidget(widget)
        self._items.insert(max(0, index), QWidgetItem(widget))
        self.invalidate()


class _PillTag(QWidget):
    """卡片左上角写「导出」「导入」的渐变小胶囊（capsules 排法；颜色是几种渐变色，字白色、标题字体）。"""

    PX = 13

    def __init__(self, text: str, colors, parent=None):
        super().__init__(parent)
        self._text, self._colors = text, colors
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.sizeHint())

    def text(self) -> str:
        return self._text

    def set_colors(self, colors) -> None:
        """换皮肤（排法不变、窗口没重建）时按新皮肤的渐变色重画。"""
        self._colors = colors
        self.update()

    def sizeHint(self) -> QSize:
        from PySide6.QtGui import QFontMetricsF

        return QSize(round(QFontMetricsF(theme.title_font(self.PX)).horizontalAdvance(self._text) + 26), 24)

    def paintEvent(self, event) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        from . import decor

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillPath(decor.capsule(rect), decor.fill_brush(self._colors, rect))
        decor.outlined_text(p, rect, self._text, theme.title_font(self.PX), "#ffffff")
        p.end()


# ------------------------------------------------------------ 搭界面用的部件样子


def _frame_box() -> QFrame:
    """默认排法的卡片（QFrame#box，和 _box 同一种）。造的卡片都有 box 属性（卡片里的竖排布局），和 chrome.Card 一样。"""
    frame, box = _box()
    frame.box = box
    return frame


def _head_label(text: str) -> QWidget:
    return _label(text, "groupHead", wrap=True)


def _dim_label(text: str) -> QWidget:
    return _label(text, "dim")


def _field_label(text: str) -> QWidget:
    return _label(text, "fieldTitle")


def _plain_warn(text: str = "") -> QWidget:
    return _label(text, "warnText", wrap=True)


def _skin_warn(text: str = "") -> QWidget:
    return chrome.WarnLabel(text, name="warnText")


@dataclass(frozen=True)
class _Kit:
    """导入/导出窗口一种排法搭界面时用的部件样子（同一族的排法共用一套）。default 用 PLAIN，side_tabs 用 PILL，
    经典主题-黑 / 反色 / 碳纤流光（side_by_side、stacked、split）用 GAME，霓虹贴纸 / 白色海景房（banded、capsules）用 RGB。
    建界面只经 _make_export / _make_import 照这个造部件和接线，摆放留在各个 _build_* 里。"""

    export_card: Callable[[], QWidget]
    import_card: Callable[[], QWidget]
    format_card: Callable[[], QWidget] | None  # 「格式」单独一张卡片的排法（side_tabs）
    box_spacing: int | None  # 卡片里内容的间距（None 照卡片自己的）
    custom_head: bool  # 分组说明另做（霓虹贴纸的斜带、白色海景房的胶囊），不在这里写一行小字
    strip_head: bool  # 分组说明去掉「导出：」「导入：」开头
    head: Callable[[str], QWidget]
    field: Callable[[str], QWidget]  # 小标题（角色、格式）
    dim: Callable[[str], QWidget]
    warn: Callable[[str], QWidget]
    copy_button: Callable[[], QWidget]
    import_button: Callable[[], QWidget]
    clear_button: Callable[[], QWidget]
    use_button: Callable[[], QWidget]  # 导入以后每一套那一行右边的「使用这套方案」（比导入、清空矮一号）
    hero_spacing: int
    hero_gap: int
    copy_spacing: int
    buttons_spacing: int
    format_own_card: bool  # 格式、预览、复制单独放在 format_card 里
    format_indent: bool  # 格式的单选按钮缩进成一个子布局，标题单独放（经典主题-黑等）
    format_segments: bool  # 格式的标题和单选按钮放在一行里（霓虹贴纸 / 白色海景房）
    format_line_spacing: int
    format_spacing: int
    select_name: str
    paste_name: str
    preview_name: str
    list_min_h: int
    list_margins: tuple
    list_spacing: int
    preview_h: tuple
    game_preview: bool  # 预览框按排法定高矮：左右并排用高的（PREVIEW_H），上下排用矮的（GAME_PREVIEW_H，让出地方给方案列表）
    results_stretch: int
    trailing_stretch: bool  # 导入卡片最下面留出空白（按钮紧跟识别结果）


PLAIN_KIT = _Kit(export_card=_frame_box, import_card=_frame_box, format_card=None, box_spacing=None,
                 custom_head=False, strip_head=False,
                 head=_head_label, field=_dim_label, dim=_dim_label, warn=_plain_warn,
                 copy_button=lambda: _button(COPY, primary=True),
                 import_button=lambda: _button(IMPORT.format(n=0), primary=True),
                 clear_button=lambda: _button(CLEAR),
                 use_button=lambda: _button(USE),
                 hero_spacing=6, hero_gap=6, copy_spacing=8, buttons_spacing=8,
                 format_own_card=False, format_indent=False, format_segments=False, format_line_spacing=0, format_spacing=10,
                 select_name="", paste_name="", preview_name="",
                 list_min_h=LIST_MIN_H, list_margins=(8, 6, 8, 6), list_spacing=4,
                 preview_h=PREVIEW_H, game_preview=False, results_stretch=1, trailing_stretch=False)
PILL_KIT = _Kit(export_card=lambda: chrome.Card(TAB_EXPORT, seed=51),
                import_card=lambda: chrome.Card(TAB_IMPORT, seed=53),
                format_card=lambda: chrome.Card(FORMAT_LABEL, seed=52), box_spacing=None,
                custom_head=False, strip_head=True,
                head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
                copy_button=lambda: chrome.PillButton(COPY, height=46, px=16, seed=6),
                import_button=lambda: chrome.PillButton(IMPORT.format(n=0), height=44, px=15, seed=7),
                clear_button=lambda: chrome.PillButton(CLEAR, kind="light", height=44, px=15),
                use_button=lambda: chrome.PillButton(USE, kind="light", height=34, px=13),
                hero_spacing=10, hero_gap=14, copy_spacing=14, buttons_spacing=12,
                format_own_card=True, format_indent=False, format_segments=False, format_line_spacing=0, format_spacing=18,
                select_name="selectAll", paste_name="paste", preview_name="preview",
                list_min_h=LIST_MIN_H, list_margins=(0, 2, 6, 2), list_spacing=6,
                preview_h=PREVIEW_H, game_preview=False, results_stretch=100, trailing_stretch=True)
GAME_KIT = _Kit(export_card=lambda: chrome.Card(TAB_EXPORT, seed=51, margins=(16, 12, 16, 14)),
                import_card=lambda: chrome.Card(TAB_IMPORT, seed=53, margins=(16, 12, 16, 14)),
                format_card=None, box_spacing=10, custom_head=False, strip_head=True,
                head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
                copy_button=lambda: chrome.GameButton(COPY, "action", height=42, px=15, icon=ICON_COPY),
                import_button=lambda: chrome.GameButton(IMPORT.format(n=0), "primary", height=42, px=15, icon=ICON_ADD),
                clear_button=lambda: chrome.GameButton(CLEAR, "plain", height=42, px=15),
                use_button=lambda: chrome.GameButton(USE, "secondary", height=32, px=13),
                hero_spacing=10, hero_gap=14, copy_spacing=14, buttons_spacing=12,
                format_own_card=False, format_indent=True, format_segments=False, format_line_spacing=14, format_spacing=18,
                select_name="selectAll", paste_name="paste", preview_name="preview",
                list_min_h=GAME_LIST_MIN_H, list_margins=(0, 2, 6, 2), list_spacing=6,
                preview_h=PREVIEW_H, game_preview=True, results_stretch=100, trailing_stretch=True)
RGB_KIT = _Kit(export_card=lambda: chrome.Card(seed=51, margins=(14, 12, 14, 12)),
               import_card=lambda: chrome.Card(seed=53, margins=(14, 12, 14, 12)),
               format_card=None, box_spacing=10, custom_head=True, strip_head=True,
               head=_head_label, field=_field_label, dim=_dim_label, warn=_skin_warn,
               copy_button=lambda: chrome.GameButton(COPY, "primary", height=32, px=13, icon=ICON_COPY),
               import_button=lambda: chrome.GameButton(IMPORT.format(n=0), "primary", height=32, px=13, icon=ICON_ADD),
               clear_button=lambda: chrome.GameButton(CLEAR, "plain", height=32, px=13),
               use_button=lambda: chrome.GameButton(USE, "secondary", height=28, px=12),
               hero_spacing=10, hero_gap=12, copy_spacing=12, buttons_spacing=10,
               format_own_card=False, format_indent=False, format_segments=True, format_line_spacing=12, format_spacing=18,
               select_name="selectAll", paste_name="paste", preview_name="preview",
               list_min_h=LIST_MIN_H, list_margins=(10, 6, 10, 6), list_spacing=4,
               preview_h=GAME_PREVIEW_H, game_preview=False, results_stretch=100, trailing_stretch=True)


class SchemeShareWindow(SkinnedWindow, chrome.EdgeResize, chrome.HideNotice, QWidget):
    """「导入/导出筹码方案」窗口。db 为资料库（角色的排列顺序），store 为悬浮窗共用的 SchemeStore，hero 为默认的
    角色筛选（None 为全部角色）。能改大小：用系统标题栏时拖系统的边框，自己画外框时拖窗口边缘（chrome.EdgeResize）；
    最小为内容需要的大小。"""

    schemes_imported = Signal(list)  # 导入了方案：这些方案所属的角色名（不重复，按先后）
    format_changed = Signal(str)  # 选了另一种格式（"short" / "text"）
    use_requested = Signal(str, str)  # 导入以后点了一套的「使用这套方案」：（所属角色, 方案 id）；结果经 show_use_result 交回
    FLAGS = Qt.Dialog | Qt.WindowCloseButtonHint  # 用系统标题栏时的窗口标志；不总在最前，窗口层次跟着悬浮窗（follow.WindowStack.layer_windows）
    LAYOUT_FIELD = "SHARE"
    LAYOUTS = LAYOUTS  # 排法名 → 建法（模块级的表：split、test-same 这类后加进来的也认）

    last_format = S.FORMAT_SHORT  # 上次选的格式：本次运行里各窗口共用，新开的窗口照它

    def __init__(self, db, store, hero: str | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.store = store
        self.catalog = store.catalog
        self._filter: str | None = None  # 角色筛选（None 为全部角色）
        self._checked: set[str] = set()  # 勾选的方案 id（换筛选时记住，导出的只是当前列出来的）
        self._visible: list[S.Scheme] = []
        self._boxes: dict[str, QCheckBox] = {}
        self._list_key: tuple | None = None
        self._hero_items: list[str | None] = []
        self._export_text = ""
        self._export_count = 0
        self._export_failed = 0  # 生成不了短字符串的套数（EXPORT_FAILED）
        self._copy_failed = False  # 上一次复制没写进剪贴板（COPY_FAILED；预览的内容变了以后收起，同「已复制」）
        self._result = S.ParseResult()
        self._done: list[_Imported] = []  # 刚导入的几套（「已导入筹码方案……」和「使用这套方案」），粘贴新内容后不再显示
        self._done_widgets: list[tuple[QPushButton, QLabel]] = []  # 那几行右边的按钮和换成的结果（和 _done 一一对应）
        self._use_button: Callable[[], QWidget] = lambda: _button(USE)  # 照排法造「使用这套方案」（_make_import 换掉）
        self.result_lines: list[tuple[str, str]] = []  # 识别结果区现在显示的各行（色调, 文字）
        self.setObjectName("shareWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle(TITLE)
        self.setWindowFlags(self.FLAGS)
        self.setStyleSheet(theme.share_style())
        self._parse_timer = QTimer(self)
        self._parse_timer.setSingleShot(True)
        self._parse_timer.setInterval(PARSE_DELAY_MS)
        self._parse_timer.timeout.connect(self.recognize)
        self.back = None  # 自己画外框时左上角的「返回」（side_tabs 排法里建）
        self.build_layout()
        chrome.frameless(self, self.back is not None, self.FLAGS, resizable=True)
        self.resize(*L.SIZE)
        self.set_format(type(self).last_format)
        self.set_hero(hero)
        self._render_results()
        self.subscribe_skin()

    # ---------------------------------------------------------------- 布局

    def apply_skin(self, skin=None) -> None:
        """换皮肤（theme.set_skin 通知）：重新套样式表，识别结果按新皮肤的颜色重写，几处建窗口时取的皮肤颜色重设。
        排法变了的窗口要重建（悬浮窗做：layout_key 和 layout_name() 不同时关掉重开）。"""
        self.setStyleSheet(theme.share_style())
        self._apply_skin_colors()
        self._render_results()

    def _pill_colors(self, word: str):
        """「导出」「导入」小胶囊的渐变色（白色海景房），按当前皮肤取。"""
        from .theme import CHROME as K

        if word == TAB_IMPORT:
            return (K.LED_COLORS.get("import") or "#22c3ee", K.LED_COLORS.get("diag") or "#3b82f6")
        return K.ACCENT

    @staticmethod
    def _style_split_line(frame: QFrame) -> None:
        """split 排法里分隔线的颜色，按当前皮肤取（排法不变、窗口没重建时 apply_skin 里重设）。"""
        from .theme import CHROME as K

        frame.setStyleSheet(f"QFrame#{frame.objectName()} {{ background: {chrome.decor.qc(K.CARD_EDGE).name()}; "
                            "border: none; }")

    def _apply_skin_colors(self) -> None:
        """把几处建窗口时取的皮肤颜色按新皮肤重设：胶囊的渐变色、split 排法的两条分隔线（排法不变时窗口不重建）。"""
        for tag in self.findChildren(_PillTag):
            tag.set_colors(self._pill_colors(tag.text()))
        for frame in (getattr(self, "_split_rule", None), getattr(self, "_split_line", None)):
            if frame is not None:
                self._style_split_line(frame)

    # ---------------------------------------------------------------- 造部件和接线
    # 各排法共用：照排法的 _Kit 造部件、接信号，摆放留在各个 _build_* 里。属性名不变（别的地方按名字找部件）。

    def _make_hero_row(self, box: QVBoxLayout, kit: _Kit) -> None:
        """角色筛选、「全选」那一行。"""
        row = QHBoxLayout()
        row.setSpacing(kit.hero_spacing)
        row.addWidget(kit.field(HERO_LABEL))
        self.hero_box = QComboBox()
        self.hero_box.setCursor(Qt.PointingHandCursor)
        self.hero_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        draw_combo_rows(self.hero_box)  # 下拉列表每一行和悬浮窗同一种画法
        self.hero_box.currentIndexChanged.connect(self._hero_picked)
        row.addWidget(self.hero_box)
        row.addSpacing(kit.hero_gap)
        self.select_all = QCheckBox(SELECT_ALL)
        if kit.select_name:
            self.select_all.setObjectName(kit.select_name)
        self.select_all.setCursor(Qt.PointingHandCursor)
        self.select_all.clicked.connect(self._select_all_clicked)
        row.addWidget(self.select_all)
        row.addStretch(1)
        box.addLayout(row)

    def _make_scheme_list(self, box: QVBoxLayout, kit: _Kit) -> None:
        """勾选方案的列表（竖向滚动的框；白色海景房的胶囊在 _make_scheme_flow 里另做）。"""
        self.list_area = QScrollArea()
        self.list_area.setObjectName("schemeList")
        self.list_area.setWidgetResizable(True)
        self.list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_area.setMinimumHeight(kit.list_min_h)
        body = QWidget()
        body.setObjectName("schemeListBody")
        self.list_box = QVBoxLayout(body)
        self.list_box.setContentsMargins(*kit.list_margins)
        self.list_box.setSpacing(kit.list_spacing)
        self.export_empty = _label(EXPORT_EMPTY, "caption", wrap=True)  # 还没有方案时写在空框里（_fill_list）
        self.export_empty.hide()
        self.list_box.addWidget(self.export_empty)
        self.list_box.addStretch(1)
        self.list_area.setWidget(body)
        box.addWidget(self.list_area, 1)

    def _make_scheme_flow(self, box: QVBoxLayout) -> None:
        """勾选方案排成一排可点的胶囊（白色海景房），放不下时换行、随内容变高。"""
        body = QWidget()
        body.setObjectName("schemeListBody")
        self.list_area = body
        self.list_box = _FlowList(body)
        self.list_box.setContentsMargins(0, 2, 0, 2)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        body.setSizePolicy(policy)
        box.addWidget(body)
        # 还没有方案时写下一步怎么做：胶囊排成一排、不是竖着的框，写在胶囊的位置下面（_fill_list）
        self.export_empty = _label(EXPORT_EMPTY, "caption", wrap=True)
        self.export_empty.hide()
        box.addWidget(self.export_empty)

    def _make_paste(self, name: str) -> None:
        """粘贴框（按 Tab 换焦点，不往框里插制表符）。"""
        self.paste = _TextBox(PASTE_H)
        self.paste.setTabChangesFocus(True)
        if name:
            self.paste.setObjectName(name)
        self.paste.textChanged.connect(self._paste_changed)

    def _make_results(self, fit: bool, prefer: bool) -> None:
        """识别结果区：fit 时按内容定高（游戏内风格的排法），prefer 时按首选高度排开。识别结果写在一个富文本标签里；导入以后
        换成一套一行（done_box：「已导入……」和右边的「使用这套方案」，_fill_done 建），行多了在区里上下滚动。"""
        self.results_area = _FitArea(self._fit_results) if fit else QScrollArea()
        if prefer:
            self.results_area.prefer_content = True
        self.results_area.setObjectName("results")
        self.results_area.setWidgetResizable(True)
        self.results_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_area.setMinimumHeight(RESULTS_MIN_H)
        body = QWidget()
        body.setObjectName("resultsBody")
        results_box = QVBoxLayout(body)
        results_box.setContentsMargins(0, 0, 0, 0)
        self.results = QLabel("")
        self.results.setTextFormat(Qt.RichText)  # 内容都经过 result_html 转义
        self.results.setTextInteractionFlags(Qt.NoTextInteraction)  # 富文本默认接走鼠标移动；关掉文字交互，自己画外框时按在字上照样能拖动窗口
        self.results.setWordWrap(True)
        self.results.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        results_box.addWidget(self.results)
        self.done_box = QWidget()
        self.done_box.setObjectName("doneBox")
        self.done_rows = QVBoxLayout(self.done_box)
        self.done_rows.setContentsMargins(0, 0, 4, 0)
        self.done_rows.setSpacing(6)
        self.done_box.hide()
        results_box.addWidget(self.done_box)
        results_box.addStretch(1)
        self.results_area.setWidget(body)

    def _make_format(self, box: QVBoxLayout, kit: _Kit, vertical: bool, capsules: bool) -> None:
        """格式的两个单选按钮（白色海景房画成一段一段的分段按钮）。"""
        self.format_group = QButtonGroup(self)
        self.format_buttons: dict[str, QRadioButton] = {}

        def add_radio(layout, key: str) -> None:
            radio = QRadioButton(FORMAT_OPTION[key])
            radio.setCursor(Qt.PointingHandCursor)
            if capsules:
                radio.setObjectName("segment")
            radio.toggled.connect(lambda on, f=key: on and self._format_picked(f))
            self.format_group.addButton(radio)
            self.format_buttons[key] = radio
            layout.addWidget(radio)

        if kit.format_segments:  # 标题和单选按钮放在一行（霓虹贴纸、白色海景房）
            line = QHBoxLayout()
            line.setSpacing(kit.format_line_spacing)
            line.addWidget(kit.field(FORMAT_LABEL), 0, Qt.AlignVCenter)
            formats = QHBoxLayout()
            if capsules:  # 分段按钮：浅灰底的一条，选中的那一段是白色的滑块
                segments = QWidget()
                segments.setObjectName("segments")
                formats.setSpacing(0)
                formats.setContentsMargins(3, 3, 3, 3)
                segments.setLayout(formats)
                for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                    add_radio(formats, key)
                line.addWidget(segments, 0, Qt.AlignVCenter)
                line.addStretch(1)
            else:
                formats.setSpacing(kit.format_spacing)
                for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                    add_radio(formats, key)
                formats.addStretch(1)
                line.addLayout(formats, 1)
            box.addLayout(line)
            return
        if kit.format_indent:  # 标题单独放，两颗单选按钮缩进成一个子布局（经典主题-黑等）
            line = QHBoxLayout()
            line.setSpacing(kit.format_line_spacing)
            line.addWidget(kit.field(FORMAT_LABEL), 0, Qt.AlignTop)
            formats = QVBoxLayout() if vertical else QHBoxLayout()
            formats.setSpacing(6 if vertical else kit.format_spacing)
            for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
                add_radio(formats, key)
            if not vertical:
                formats.addStretch(1)
            line.addLayout(formats, 1)
            box.addLayout(line)
            return
        row = QHBoxLayout()
        row.setSpacing(kit.format_spacing)
        if not kit.format_own_card:  # 格式卡片自己写了「格式」，不再写一遍
            row.addWidget(kit.dim(FORMAT_LABEL))
        for key in (S.FORMAT_SHORT, S.FORMAT_TEXT):
            add_radio(row, key)
        row.addStretch(1)
        box.addLayout(row)

    def _make_export(self, kit: _Kit, vertical_formats: bool = False, inner_head: QWidget | None = None,
                     capsules: bool = False) -> tuple[QWidget, QWidget | None]:
        """导出这一块：分组说明、角色筛选和「全选」、勾选方案的列表、格式、预览、复制。（导出卡片, 格式卡片或 None）。
        inner_head 给了时放在卡片最上面（白色海景房的胶囊）；capsules：勾选方案是一排胶囊、格式是分段按钮。"""
        export = self.export_box = kit.export_card()
        if kit.box_spacing is not None:
            export.box.setSpacing(kit.box_spacing)
        if not kit.custom_head:
            text = EXPORT_HEAD.removeprefix(TAB_EXPORT + "：") if kit.strip_head else EXPORT_HEAD
            export.box.addWidget(kit.head(text))
        if inner_head is not None:
            export.box.addWidget(inner_head)
        self._make_hero_row(export.box, kit)
        if capsules:
            self._make_scheme_flow(export.box)
        else:
            self._make_scheme_list(export.box, kit)
        card = kit.format_card() if kit.format_card is not None else None
        tail = card.box if card is not None else export.box
        self._make_format(tail, kit, vertical_formats, capsules)
        self.preview = _TextBox(PREVIEW_H if vertical_formats else GAME_PREVIEW_H, read_only=True) if kit.game_preview \
            else _TextBox(kit.preview_h, read_only=True)
        if kit.preview_name:
            self.preview.setObjectName(kit.preview_name)
        tail.addWidget(self.preview)
        self.export_warn = kit.warn("")
        self.export_warn.hide()
        tail.addWidget(self.export_warn)
        row = QHBoxLayout()
        row.setSpacing(kit.copy_spacing)
        self.copy_btn = kit.copy_button()
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        row.addWidget(self.copy_btn)
        self.copied = _label("", "okText")
        self.copied.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.copied, 1)
        tail.addLayout(row)
        return export, card

    def _make_import(self, kit: _Kit, fit: bool = False, inner_head: QWidget | None = None,
                     prefer: bool = False) -> QWidget:
        """导入这一块：分组说明、粘贴框、识别结果（游戏内风格的排法按内容定高）、导入和清空、只读提示、说明。"""
        imp = self.import_box = kit.import_card()
        self._use_button = kit.use_button  # 导入以后每一套那一行的「使用这套方案」照这一种排法造（_fill_done）
        if kit.box_spacing is not None:
            imp.box.setSpacing(kit.box_spacing)
        if not kit.custom_head:
            text = IMPORT_HEAD.removeprefix(TAB_IMPORT + "：") if kit.strip_head else IMPORT_HEAD
            imp.box.addWidget(kit.head(text))
        if inner_head is not None:
            imp.box.addWidget(inner_head)
        self._make_paste(kit.paste_name)
        imp.box.addWidget(self.paste)
        self._make_results(fit, prefer)
        imp.box.addWidget(self.results_area, kit.results_stretch)
        row = QHBoxLayout()
        row.setSpacing(kit.buttons_spacing)
        self.import_btn = kit.import_button()
        self.import_btn.clicked.connect(self.import_schemes)
        row.addWidget(self.import_btn)
        self.clear_btn = kit.clear_button()
        self.clear_btn.clicked.connect(self.clear_paste)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        imp.box.addLayout(row)
        self.read_only_hint = kit.warn(READ_ONLY)
        self.read_only_hint.hide()
        imp.box.addWidget(self.read_only_hint)
        imp.box.addWidget(_label(IMPORT_NOTE, "caption", wrap=True))
        if kit.trailing_stretch:  # 识别结果、按钮紧跟在粘贴框下面，多出来的空白在卡片最下面
            imp.box.addStretch(1)
        return imp

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)
        title = _label(TITLE, "section")
        outer.addWidget(title)

        # ---- 导出
        export, _ = self._make_export(PLAIN_KIT)
        outer.addWidget(export, 1)

        # ---- 导入
        imp = self._make_import(PLAIN_KIT)
        outer.addWidget(imp, 1)

        outer.addWidget(_label(SHARE_NOTE, "caption", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = _button(CLOSE)
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.close_btn)
        outer.addLayout(row)

    def _build_tabs(self) -> None:
        """游戏内风格的排法：左上角「返回」（皮肤自己画外框时）、左侧白色导航栏里「导出」「导入」两个页签，右边深灰面板里
        当前页签的卡片（导出：勾选方案、格式和预览、复制；导入：粘贴框、识别结果、导入），面板下面写短字符串的说明。
        部件的名字和默认的排法相同（别的方法按这些名字找部件）；一个窗口只留一个关闭按钮：自己画外框时没有「关闭」按钮，
        close_btn 就是「返回」。"""
        from .theme import CHROME as K

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        root = QHBoxLayout(frame)
        root.setContentsMargins(0, 0, 20, 0)
        root.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 22)
        left.setSpacing(14)
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
        right.setContentsMargins(0, 18, 0, 8)
        right.setSpacing(6)
        panel = chrome.Panel(seed=30)
        holder = QVBoxLayout(panel)
        holder.setContentsMargins(24, 24, 24, 24)
        stack = self.pages = QStackedWidget()
        holder.addWidget(stack)
        right.addWidget(panel, 1)
        note = _label(SHARE_NOTE, "shareNote", wrap=True)
        note.setContentsMargins(24, 0, 12, 0)
        right.addWidget(note)
        root.addLayout(right, 1)

        # ---- 导出：一张卡片放角色、全选、方案列表；一张「格式」卡片放格式、预览、复制
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(18)
        export, fmt = self._make_export(PILL_KIT)
        col.addWidget(export, 1)
        col.addWidget(fmt)
        stack.addWidget(page)

        # ---- 导入：一张卡片放粘贴框、识别结果、导入和清空
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        imp = self._make_import(PILL_KIT)
        col.addWidget(imp, 1)
        stack.addWidget(page)

        for index, name in enumerate((TAB_EXPORT, TAB_IMPORT)):
            tab = rail.add_tab(name)
            tab.toggled.connect(lambda on, i=index: on and stack.setCurrentIndex(i))
        rail.box.addStretch(1)
        rail.tabs()[0].setChecked(True)
        self.tabs = {tab.text(): tab for tab in rail.tabs()}
        if self.back is not None:
            self.close_btn = self.back
        else:  # 用系统标题栏时右下角照旧有「关闭」
            self.close_btn = chrome.PillButton(CLOSE)
            self.close_btn.clicked.connect(self.close)
            right.addWidget(self.close_btn, 0, Qt.AlignRight)

    def _game_parts(self, vertical_formats: bool) -> tuple[QWidget, QWidget]:
        """经典主题-黑、反色、碳纤流光三种排法共用：导出、导入两张卡片（chrome.Card：卡片的样子、标签按皮肤）。导出：角色、
        全选、方案列表、格式和预览、复制；导入：粘贴框、识别结果、导入和清空、只读提示和说明。部件的名字和默认的排法相同。
        vertical_formats：两种格式的单选按钮上下排（左右并排的窗口每块较窄）。"""
        export, _ = self._make_export(GAME_KIT, vertical_formats=vertical_formats)
        imp = self._make_import(GAME_KIT, fit=True)
        return export, imp

    def _game_frame(self) -> tuple[QVBoxLayout, QVBoxLayout]:
        """经典主题-黑、反色两种排法共用：窗口底（chrome.Frame）、顶上一行（标题、右上角 ×，chrome.WindowHead）。返回
        （整个窗口的竖排布局, 放内容的竖排布局）。× 就是关闭窗口（close_btn）；用系统标题栏的皮肤另放「关闭」。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = self.frame = chrome.Frame()
        outer.addWidget(frame)
        root = QVBoxLayout(frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = chrome.WindowHead(TITLE)
        self.back = head.closer
        root.addWidget(head)
        body = QVBoxLayout()
        root.addLayout(body, 1)
        return root, body

    def _closer_or_button(self, row: QHBoxLayout) -> None:
        """一个窗口只留一个关闭按钮：自己画外框时 close_btn 就是右上角的 ×；用系统标题栏时 row 的右端另放「关闭」。"""
        if self.back is not None:
            self.close_btn = self.back
        else:
            self.close_btn = chrome.GameButton(CLOSE, "secondary", height=40, px=15)
            self.close_btn.clicked.connect(self.close)
            row.addWidget(self.close_btn, 0, Qt.AlignRight | Qt.AlignVCenter)

    def _build_columns(self) -> None:
        """经典主题-黑的排法（窗口横着，约 920×600）：顶上正中的标题页签、右上角 ×；导出、导入两块（靛紫网点的底，标题前
        两颗小菱形）左右并排，最下面一行写短字符串的说明。"""
        _, body = self._game_frame()
        body.setContentsMargins(26, 14, 26, 18)
        body.setSpacing(14)
        export, imp = self._game_parts(vertical_formats=True)
        columns = QHBoxLayout()
        columns.setSpacing(20)
        columns.addWidget(export, 1)
        columns.addWidget(imp, 1)
        body.addLayout(columns, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        note = _label(SHARE_NOTE, "shareNote", wrap=True)
        row.addWidget(note, 1)
        self._closer_or_button(row)
        body.addLayout(row)

    def _build_stacked(self) -> None:
        """经典主题-反色的排法（窗口竖着，约 620×804）：顶上黑色通栏写标题、右上角 ×；导出、导入两张白卡片上下排（卡片
        左上角压黑色斜角标签），最下面一条洋红横幅写短字符串的说明。"""
        _, body = self._game_frame()
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(14)
        export, imp = self._game_parts(vertical_formats=False)
        self.results_area.prefer_content = True  # 卡片按首选高度排：识别结果整个排开
        # 两张卡片上下排比较高：放在一个透明的滚动区里，屏幕放不下时窗口照样能缩到可用高度，卡片在窗口里上下滚动
        scroll = QScrollArea()
        scroll.setObjectName("cardsScroll")
        scroll.setFocusPolicy(Qt.NoFocus)  # 透明的滚动区不进 Tab 顺序（焦点停在它上面看不出来）
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards = QWidget()
        cards.setObjectName("cardsBody")
        column = QVBoxLayout(cards)
        column.setContentsMargins(0, 10, 0, 0)
        column.setSpacing(22)
        column.addWidget(export, 1)
        column.addWidget(imp, 1)
        scroll.setWidget(cards)
        # 只上下滚动：窗口最窄为卡片内容需要的宽（再加滚动条），不把卡片挤坏
        scroll.setMinimumWidth(cards.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)
        body.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(chrome.Banner(SHARE_NOTE), 1)
        self._closer_or_button(row)
        body.addLayout(row)

    # ---- 霓虹贴纸（banded）、白色海景房（capsules）

    def _rgb_head(self, word: str, head: str, colors) -> QWidget:
        """「导出」「导入」那一行：霓虹贴纸是一条黑色斜带（chrome.Tag，ChromeLook.TAG_KIND 为 band），白色海景房是一枚渐变小胶囊
        （_PillTag，colors 为渐变色）；后面接说明（去掉开头的「导出：」「导入：」）。"""
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        tag = _PillTag(word, colors) if colors else chrome.Tag(word)
        tag.setFixedSize(tag.sizeHint())
        line.addWidget(tag, 0, Qt.AlignVCenter)
        line.addWidget(_label(head.removeprefix(word + "："), "groupHead", wrap=True), 1, Qt.AlignVCenter)
        return row

    def _rgb_parts(self, capsules: bool) -> tuple[QWidget, QWidget, QWidget, QWidget]:
        """霓虹贴纸、白色海景房两种排法共用：（导出那一行, 导出卡片, 导入那一行, 导入卡片）。部件的名字和默认的排法相同。
        capsules：勾选方案排成一排可点的胶囊（_FlowList），格式是分段按钮（单选按钮画成一段一段，样式表的 #segment）；否则
        方案列表竖排在一个框里，格式是两个单选按钮。"""
        k = theme.CHROME
        export_colors = k.ACCENT if capsules else None
        import_colors = ((k.LED_COLORS.get("import") or "#22c3ee", k.LED_COLORS.get("diag") or "#3b82f6")
                         if capsules else None)
        export_head = self._rgb_head(TAB_EXPORT, EXPORT_HEAD, export_colors)
        import_head = self._rgb_head(TAB_IMPORT, IMPORT_HEAD, import_colors)
        export, _ = self._make_export(RGB_KIT, inner_head=export_head if capsules else None, capsules=capsules)
        imp = self._make_import(RGB_KIT, fit=True, inner_head=import_head if capsules else None, prefer=True)
        return export_head, export, import_head, imp

    def _rgb_frame(self, capsules: bool) -> None:
        """霓虹贴纸、白色海景房两种排法：窗口底和顶上一行（标题、右上角 ×，_game_frame）；下面导出、导入两块上下排，放在透明的
        滚动区里（屏幕放不下时窗口照样能缩到可用高度，卡片在窗口里上下滚动）；最下面一行写短字符串的说明。capsules：「导出」
        「导入」的小胶囊写在卡片里面（白色海景房）；否则黑色斜带写在卡片上面（霓虹贴纸）。"""
        _, body = self._game_frame()
        body.setContentsMargins(20, 2, 20, 18)
        body.setSpacing(10)
        export_head, export, import_head, imp = self._rgb_parts(capsules)
        scroll = QScrollArea()
        scroll.setObjectName("cardsScroll")
        scroll.setFocusPolicy(Qt.NoFocus)  # 透明的滚动区不进 Tab 顺序（焦点停在它上面看不出来）
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards = QWidget()
        cards.setObjectName("cardsBody")
        column = QVBoxLayout(cards)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8 if not capsules else 12)
        for head, card in ((export_head, export), (import_head, imp)):
            if not capsules:
                column.addWidget(head)
            column.addWidget(card, 1)
            if not capsules and card is export:
                column.addSpacing(6)
        scroll.setWidget(cards)
        scroll.setMinimumWidth(cards.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)
        body.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_label(SHARE_NOTE, "shareNote", wrap=True), 1)
        self._closer_or_button(row)
        body.addLayout(row)

    def _build_banded(self) -> None:
        """霓虹贴纸的排法（窗口竖着，约 600×672）：顶上一行白字黑描边的标题、右上角 ×；导出、导入两块上下排，每块上面一条黑色
        斜带写「导出」「导入」、后面接说明，下面是粗黑描边的贴纸卡片；最下面一行写短字符串的说明。"""
        self._rgb_frame(capsules=False)

    def _build_capsules(self) -> None:
        """白色海景房的排法（窗口竖着，约 600×672）：顶上一行标题、右上角 ×；导出、导入两张白卡片上下排，卡片左上角一枚渐变
        小胶囊写「导出」「导入」、后面接说明；勾选方案是一排可点的胶囊，格式是分段按钮；最下面一行写短字符串的说明。"""
        self._rgb_frame(capsules=True)

    # ---- 碳纤流光（split）

    def _build_split(self) -> None:
        """碳纤流光的排法（窗口横着，约 900×540）：顶上一行（四色小方块、斜体粗体的标题，右上角 ×）；导出、导入两块左右并排，
        中间一条竖线，每块上面是编号的分组标题（「01 导出 ──」「02 导入 ──」，不画卡片的底和框）；最下面一行写短字符串的
        说明。两块的内容同经典主题-黑（_game_parts：两种格式上下排）。按钮是切角的，主按钮外面一圈光。"""
        _, body = self._game_frame()
        body.setContentsMargins(22, 2, 22, 16)
        body.setSpacing(12)
        export, imp = self._game_parts(vertical_formats=True)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        for number, card in (("01", export), ("02", imp)):
            card.plain = True
            card.set_number(number)
        columns.addWidget(export, 1)
        rule = self._split_rule = QFrame()
        rule.setObjectName("splitRule")
        rule.setFixedWidth(1)
        self._style_split_line(rule)
        columns.addWidget(rule)
        columns.addWidget(imp, 1)
        body.addLayout(columns, 1)
        line = self._split_line = QFrame()
        line.setObjectName("splitLine")
        line.setFixedHeight(1)
        self._style_split_line(line)
        body.addWidget(line)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_label(SHARE_NOTE, "shareNote", wrap=True), 1)
        self._closer_or_button(row)
        body.addLayout(row)
        # 方案列表、预览框、粘贴框画成切角的框（样式表里它们的底和框透明，四周留出画框线的地方）
        chrome.cut_corners(self.list_area, "well")
        for box in (self.preview, self.paste):
            chrome.cut_corners(box, "field")

    # ---------------------------------------------------------------- 导出

    def set_hero(self, hero: str | None) -> None:
        """角色筛选改为 hero（None、资料库里没有的角色为全部角色），并勾选该角色正在用的方案（有的话）。"""
        hero = hero if isinstance(hero, str) and hero in self.db.heroes else None
        self._filter = hero
        if hero is not None:
            current = self.store.current(hero)
            if current is not None:
                self._checked.add(current.id)
        self.refresh()

    def hero_filter(self) -> str | None:
        """现在的角色筛选（None 为全部角色）。"""
        return self._filter

    def refresh(self) -> None:
        """按方案文件的最新内容刷新：角色下拉框、方案列表（保留勾选，删掉的方案不再勾选）、预览、导入按钮和只读提示；
        刚导入的几行里在别处删掉了的那一套写「这套方案已被删除」（只改那一行的字，不重建：可能正在点里面的按钮）。"""
        schemes = self.store.schemes()
        self._checked &= {s.id for s in schemes}
        self._fill_heroes(schemes)
        self._fill_list(schemes)
        self.read_only_hint.setVisible(bool(self.store.read_only))
        self._update_import_button()
        self._update_export()
        self._show_done()

    def _hero_rank(self) -> dict[str, int]:
        """角色的排列顺序：和悬浮窗的角色下拉框相同（按六类分组，组内按资料库的顺序）。"""
        return {h.name: i for i, h in enumerate(heroes_in_group_order(self.db.heroes.values()))}

    def _fill_heroes(self, schemes: list[S.Scheme]) -> None:
        """角色下拉框：「全部角色」，再列有我的方案的角色（筛选中的角色没有方案时也列出），按六类排，每一类前面一行不能选的
        组名（widgets.fill_hero_combo，和悬浮窗、编辑窗口的角色下拉框同一种）；选中项按筛选的角色找（findData）。"""
        names = {s.hero for s in schemes}
        if self._filter is not None:
            names.add(self._filter)
        heroes = [h for h in self.db.heroes.values() if h.name in names]  # 资料库的先后（同一类里照它排）
        items: list[str | None] = [None, *(h.name for h in heroes_in_group_order(heroes))]
        self.hero_box.blockSignals(True)
        if items != self._hero_items:
            self.hero_box.clear()
            self.hero_box.addItem(ALL_HEROES, None)
            fill_hero_combo(self.hero_box, heroes, text=lambda h: h.name)
            self._hero_items = items
        self.hero_box.setCurrentIndex(0 if self._filter is None else max(self.hero_box.findData(self._filter), 0))
        self.hero_box.blockSignals(False)

    def _fill_list(self, schemes: list[S.Scheme]) -> None:
        """方案列表：当前筛选下的我的方案，按角色排（同一角色里按方案文件的先后），每套一个复选框。一套我的方案都没有时
        列表里写下一步怎么做（EXPORT_EMPTY）。"""
        if self.export_empty.isHidden() == (not schemes):
            self.export_empty.setVisible(not schemes)
        rank = self._hero_rank()
        visible = [s for s in schemes if self._filter is None or s.hero == self._filter]
        visible.sort(key=lambda s: rank.get(s.hero, len(rank)))
        self._visible = visible
        key = tuple((s.id, s.hero, s.name) for s in visible)
        if key != self._list_key:
            for box in self._boxes.values():
                self.list_box.removeWidget(box)
                box.hide()
                box.deleteLater()
            self._boxes = {}
            for s in visible:
                box = QCheckBox(button_text(ITEM.format(hero=s.hero, name=s.name)))
                box.setCursor(Qt.PointingHandCursor)
                box.toggled.connect(lambda on, sid=s.id: self._item_toggled(sid, on))
                self.list_box.insertWidget(self.list_box.count() - 1, box)
                self._boxes[s.id] = box
            self._list_key = key
            self._order_tabs()
        for sid, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(sid in self._checked)
            box.blockSignals(False)

    def _order_tabs(self) -> None:
        """方案勾选框每次刷新都会重建，默认的次序把它们排到了最后（落在「关闭」之后）。这里排到「全选」后面、
        列表区、格式之前，和画面上的先后一致。"""
        prev = self.select_all
        for box in self.list_boxes():
            self.setTabOrder(prev, box)
            prev = box
        self.setTabOrder(prev, self.list_area)
        short = self.format_buttons.get(S.FORMAT_SHORT)
        if short is not None:
            self.setTabOrder(self.list_area, short)

    def _hero_picked(self, index: int) -> None:
        item = self.hero_box.model().item(index) if index >= 0 else None
        if item is not None and not item.isEnabled():  # 分组标题选不上；万一选到了，按原来的筛选复原
            self._fill_heroes(self.store.schemes())
            return
        self._filter = self.hero_box.itemData(index)
        self._fill_list(self.store.schemes())
        self._update_export()

    def _item_toggled(self, scheme_id: str, on: bool) -> None:
        if on:
            self._checked.add(scheme_id)
        else:
            self._checked.discard(scheme_id)
        self._update_export()

    def _select_all_clicked(self, on: bool) -> None:
        """「全选」：勾选当前列出的全部方案；已经全勾上时再点则全部取消。"""
        ids = {s.id for s in self._visible}
        if on:
            self._checked |= ids
        else:
            self._checked -= ids
        for sid, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(sid in self._checked)
            box.blockSignals(False)
        self._update_export()

    def list_boxes(self) -> list[QCheckBox]:
        """方案列表现在的各行（复选框），按列表的顺序。"""
        return [self._boxes[s.id] for s in self._visible]

    def selected_schemes(self) -> list[S.Scheme]:
        """要导出的方案：当前列出来、勾选了的，按列表的顺序。"""
        return [s for s in self._visible if s.id in self._checked]

    def format(self) -> str:
        """现在选的格式：schemes.FORMAT_SHORT 或 FORMAT_TEXT。"""
        return next((f for f, radio in self.format_buttons.items() if radio.isChecked()), S.FORMAT_SHORT)

    def set_format(self, fmt: str) -> None:
        """选格式（不认识的按短字符串）。"""
        fmt = fmt if fmt in self.format_buttons else S.FORMAT_SHORT
        self.format_buttons[fmt].setChecked(True)
        self._update_export()

    def _format_picked(self, fmt: str) -> None:
        if type(self).last_format != fmt:
            type(self).last_format = fmt
        self._update_export()
        self.format_changed.emit(fmt)

    def export_text(self) -> str:
        """预览框里的内容，也就是「复制到剪贴板」放进剪贴板的内容：选中的方案每套一行。"""
        return self._export_text

    def _update_export(self) -> None:
        fmt = self.format()
        lines: list[str] = []
        failed = 0
        for s in self.selected_schemes():
            if fmt == S.FORMAT_TEXT:
                lines.append(S.to_text(s))
                continue
            try:
                lines.append(S.to_short(s, self.catalog))
            except ValueError as e:  # 筹码编号表读不出来、或缺这个筹码的编号
                failed += 1
                log.warning("方案「%s」（%s）无法生成短字符串：%s", s.name, s.hero, e)
        text = "\n".join(lines)
        if text != self._export_text:
            self._export_text = text
            self.preview.setPlainText(text)
            self.copied.clear()
            self._copy_failed = False
        self._export_count = len(lines)
        self.copy_btn.setEnabled(bool(lines))
        self._export_failed = failed
        self._show_export_warn()
        ids = {s.id for s in self._visible}
        self.select_all.setEnabled(bool(ids))
        self.select_all.setChecked(bool(ids) and ids <= self._checked)

    def _show_export_warn(self) -> None:
        """导出这一块的警告（export_warn，和窗口里其他警告同一个样子）：生成不了短字符串的几套、复制失败，各一行；
        都没有时藏起来。"""
        lines = [EXPORT_FAILED.format(n=self._export_failed)] if self._export_failed else []
        if self._copy_failed:
            lines.append(COPY_FAILED)
        self.export_warn.setText("\n".join(lines))
        self.export_warn.setVisible(bool(lines))

    def copy_to_clipboard(self) -> str:
        """把预览的内容放进剪贴板，写明复制了几套、什么格式。没有选中方案时什么都不做。返回复制的内容。

        写进以后读回来核对：剪贴板被别的程序占着、没写进去时不写「已复制」，在导出这一块的警告里写复制失败，返回
        空字符串。"""
        text = self._export_text
        if not text:
            return ""
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        self._copy_failed = clipboard.text().replace("\r\n", "\n") != text.replace("\r\n", "\n")
        self._show_export_warn()
        if self._copy_failed:
            self.copied.clear()
            return ""
        self.copied.setText(COPIED.format(n=self._export_count, format=FORMAT_NAME[self.format()]))
        return text

    # ---------------------------------------------------------------- 导入

    def _paste_changed(self) -> None:
        self.clear_btn.setEnabled(bool(self.paste.toPlainText() or self._done))
        self._parse_timer.start()

    def recognize(self) -> S.ParseResult:
        """马上识别粘贴框里的内容并显示结果（平时由粘贴框的内容变化触发，稍等 PARSE_DELAY_MS 后调用）。"""
        self._parse_timer.stop()
        text = self.paste.toPlainText()
        if text.strip():
            self._done = []
            self._result = S.parse_share(text, self.catalog)
        else:
            self._result = S.ParseResult()
        self._render_results()
        return self._result

    def _render_results(self) -> None:
        """识别结果区：粘贴框里有内容时写识别结果（一个富文本标签）；空着、刚导入过时一套一行写导入了哪几套，每行右边
        「使用这套方案」（done_box）。"""
        done = not self.paste.toPlainText().strip()
        if done:
            lines = [("ok", item.line()) for item in self._done]
        else:
            lines = result_lines(self._result, self.db)
        self.result_lines = lines
        self.results.setText("" if done else result_html(lines))
        self.results.setVisible(not done)
        self._fill_done(self._done if done else [])
        self.clear_btn.setEnabled(bool(self.paste.toPlainText() or self._done))
        self._update_import_button()
        self._fit_results()

    def _fill_done(self, items: list[_Imported]) -> None:
        """导入以后的那几行（done_box）：左边「已导入筹码方案「X」（芬妮）」（同识别结果「能导入的」那一种颜色，皮肤写了色块时
        也写在色块里），右边「使用这套方案」；点过的那一行按钮换成结果（_show_done）。items 为空时收起。每次按 _done 重建
        （导入、粘贴新内容、清空、换皮肤接回时）；只是结果变了时不重建（_show_done），免得正在点的按钮被换掉。"""
        while self.done_rows.count():
            item = self.done_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()  # 可能正在处理其中某个按钮的点击，不能立即删
            free_item(item)
        self._done_widgets = []
        for record in items:
            row = QWidget()
            row.setObjectName("doneRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            text = QLabel(result_html([("ok", record.line())]))
            text.setTextFormat(Qt.RichText)  # 内容经过 result_html 转义：方案名里的网页标记原样显示
            text.setTextInteractionFlags(Qt.NoTextInteraction)
            text.setWordWrap(True)
            text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            line.addWidget(text, 1)
            button = self._use_button()
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, sid=record.scheme_id: self._use_clicked(sid))
            line.addWidget(button, 0, Qt.AlignVCenter)
            note = _label("", "useNote")  # 一行写完，左边的字放不下时由它折行
            line.addWidget(note, 0, Qt.AlignVCenter)
            self.done_rows.addWidget(row)
            self._done_widgets.append((button, note))
        self.done_box.setVisible(bool(items))
        self._show_done()

    def _show_done(self) -> None:
        """导入以后那几行右边：还没点的是「使用这套方案」（这套方案已经在别处删掉了时写 USE_GONE），点过的换成结果那一句。"""
        for record, (button, note) in zip(self._done, self._done_widgets):
            if not record.result and self.store.get(record.scheme_id) is None:
                record.result = "gone"
            text, name = record.outcome()
            button.setVisible(not text)
            note.setText(text)
            if text and note.objectName() != name:
                note.setObjectName(name)
                note.style().unpolish(note)
                note.style().polish(note)
            note.setVisible(bool(text))

    def _use_clicked(self, scheme_id: str) -> None:
        """「使用这套方案」：发 use_requested（所属角色, 方案 id），由悬浮窗设为这个角色用的方案，结果经 show_use_result 回来。"""
        record = next((r for r in self._done if r.scheme_id == scheme_id), None)
        if record is not None:
            self.use_requested.emit(record.hero, record.scheme_id)

    def show_use_result(self, scheme_id: str, result: str) -> None:
        """悬浮窗交回「使用这套方案」的结果（now 悬浮窗已改用、later 已设为那个角色用的方案、gone 这套方案已不在）：那一行
        的按钮换成结果那一句。不在刚导入的几行里时不管。"""
        for record in self._done:
            if record.scheme_id == scheme_id:
                record.result = result
        self._show_done()
        self._fit_results()

    def imported_rows(self) -> list[tuple[str, str]]:
        """导入以后那几行显示的内容（测试用）：（左边的字, 右边的结果；还没点、按钮还在时为空字符串）。"""
        return [(record.line(), record.outcome()[0]) for record in self._done]

    def _fit_results(self) -> None:
        """游戏内风格的几种排法：识别结果区按内容的高度（不超过卡片里剩下的地方，再多在区里滚动），下面的按钮紧跟着它，
        多出来的空白留在卡片最下面。默认的排法照旧由识别结果区占满剩下的地方。"""
        if self.layout_key == "default":
            return
        width = self.results_area.viewport().width()
        if width < 50:
            width = max(self.import_box.width() - 60, 300)
        lines = 0 if self.results.isHidden() else self.results.heightForWidth(width)
        if not self.done_box.isHidden():
            box = self.done_rows
            lines += box.totalHeightForWidth(width) if box.hasHeightForWidth() else self.done_box.sizeHint().height()
        self.results_area.setMaximumHeight(max(RESULTS_MIN_H, lines + 6))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_results()

    def _update_import_button(self) -> None:
        n = len(self._result.schemes)
        self.import_btn.setText(IMPORT.format(n=n))
        self.import_btn.setEnabled(n > 0 and not self.store.read_only)

    def import_schemes(self) -> list[S.Scheme]:
        """把识别出的、能导入的方案都新建为我的方案（不覆盖已有的，同名时名称后加「（2）」）。导入后清空粘贴框，
        一套一行写明导入了哪几套（每行右边「使用这套方案」；导入不自动换用），刷新方案列表，发出 schemes_imported。
        方案文件只读、没有能导入的方案时什么都不做。返回新建的方案。"""
        if self._parse_timer.isActive():
            self.recognize()
        schemes = self._result.schemes
        if not schemes or self.store.read_only:
            return []
        try:
            added = self.store.import_schemes(schemes)
        except (S.SchemesReadOnly, ValueError) as e:
            log.warning("导入筹码方案失败：%s", e)
            self.refresh()
            return []
        self._done = [_Imported(s.id, s.hero, s.name) for s in added]
        self.paste.blockSignals(True)
        self.paste.clear()
        self.paste.blockSignals(False)
        self._result = S.ParseResult()
        self._render_results()
        self.refresh()
        self.schemes_imported.emit(list(dict.fromkeys(s.hero for s in added)))
        return added

    def clear_paste(self) -> None:
        """「清空」：清空粘贴框和识别结果（含刚导入的那几行）。"""
        self._done = []
        self.paste.blockSignals(True)
        self.paste.clear()
        self.paste.blockSignals(False)
        self.recognize()

    # ---------------------------------------------------------------- 窗口

    def _available_height(self) -> int | None:
        area = self.available_area()
        return area.height() if area is not None else None

    def fit_screen(self) -> None:
        """屏幕放不下时按可用高度缩小（方案列表和识别结果区随之变短，其余部分不被裁掉）。"""
        avail = self._available_height()
        if avail is None:
            return
        limit = max(self.minimumSizeHint().height(), avail - L.SCREEN_MARGIN)
        if self.height() > limit:
            self.resize(self.width(), limit)

    def showEvent(self, event) -> None:
        self.refresh()
        self.fit_screen()
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        # 重新激活时（例如在编辑窗口改过方案后切回来）按方案文件的最新内容刷新
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh()
        super().changeEvent(event)

    def state(self) -> dict:
        """交出的状态：粘贴框内容、勾选的方案、角色筛选、格式、刚导入的几行、当前页签，加上窗口的位置。
        换皮肤重开窗口时靠它保住输入和勾选。"""
        s = super().state()
        s.update(paste=self.paste.toPlainText(), checked=sorted(self._checked), filter=self._filter, format=self.format(),
                 done=[dataclasses.asdict(item) for item in self._done],
                 tab=next((name for name, tab in getattr(self, "tabs", {}).items() if tab.isChecked()), None))
        return s

    def restore(self, state) -> None:
        """接回 state() 交出的状态：换回角色筛选和勾选、粘贴框内容、格式、刚导入的几行、页签，窗口位置按屏幕挪回来。
        原来的排法没有页签时，粘贴框有内容或刚导入过就停在「导入」页。勾选的方案在换皮肤期间被删掉时
        不再勾选（refresh 里按现有方案取交集），不报错。"""
        if not state:
            return
        super().restore(state)
        self._filter = state.get("filter")
        self._checked = set(state.get("checked") or ())
        self.refresh()
        fmt = state.get("format")
        if fmt and self.format() != fmt:
            self.set_format(fmt)
        self.paste.blockSignals(True)
        self.paste.setPlainText(state.get("paste") or "")
        self.paste.blockSignals(False)
        self._done = [_Imported(**item) for item in state.get("done") or () if isinstance(item, dict)]
        self.recognize()
        tabs = getattr(self, "tabs", {})
        tab = state.get("tab")
        if tab is None and (self.paste.toPlainText().strip() or self._done):
            # 从没有页签的排法换过来：粘贴框里有内容或刚导入过，说明正在导入，停在「导入」页（不然开在第一页「导出」）
            tab = TAB_IMPORT
        if tab in tabs:
            tabs[tab].setChecked(True)

    def keep_on_screen(self) -> None:
        """接回窗口位置时用：先按可用高度缩小，再挪进可用区域。"""
        self.fit_screen()
        super().keep_on_screen()
