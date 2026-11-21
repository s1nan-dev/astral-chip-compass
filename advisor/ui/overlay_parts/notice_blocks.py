"""提示行（标题栏下方的信息栏和各卡片的提示区）的排法：每条提示一个淡底色的小块，块与块之间留间隔。

- 首句加粗当标题（split_head）；其余正文另起一行、字色淡一点，一句一行（sentences）。
- 按钮统一排在块的右下角：一行放得下（NoticeView.notice_width）就排成一行，否则每行两个（factory._fill_grid）；
  EQUAL_WIDTH_NOTICES 里的两个按钮一样宽。
- 要紧的（warn、action 级，⚠ 橙色字）块左边一道橙色竖条，ok 级一道绿色竖条（样式表的 QFrame[noticeBlock="true"]）。
- 网址换成短的文字链接（link_marks）：「说法：网址」的链接文字是说法，单独的网址照写；官网核对页的域名
  （wording.VERIFY_SHOWN）照写域名，让人认得出、也能自己输入。点了照样交给 NoticeView.open_link，鼠标停上去显示地址。
  「免费软件」声明那一条的「关于」照旧是打开「关于」窗口的链接（ABOUT_LINK）。
- 提示的文字一个字不改：只拆段、换行、加粗，网址换成链接。

块的部件名是 notice-<键>（NoticeView.buttons 按它找按钮），标题的部件名是 notice_<级别>（皮肤的样式表按它给颜色）。
"""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolTip, QVBoxLayout

from .. import lang
from ..text import KEEP_JOINER, keep_words, link_style
from .factory import _button, _equal_width, _fill_grid, _grid, _set_tip
from .wording import ABOUT_LINK, FREE_NOTICE_LINK, VERIFY_SHOWN, VERIFY_URL

# 两个按钮一样宽的提示行：按钮只有一个字时（〔换〕〔不换〕〔是〕〔否〕）也要好点，宽度见 factory._equal_width
EQUAL_WIDTH_NOTICES = frozenset({"hant", "ocr_check"})

_OPEN, _CLOSE = "（(「『【", "）)」』】"
HEAD_MAX = 26  # 首句长于这么多字时在第一个（括号外的）逗号、分号、冒号处断开当标题
HEAD_MIN = 6  # 断开处之前至少这么多字（不在「⚠ 有 2」这样的开头断）
TAIL_KEEP = 4  # 每行最后这么多个字连在一起：一句折行时最后一行不只剩一两个字（例如只剩「来。」）
BLOCK_MARGINS = (9, 6, 8, 7)  # 块的内边距（左、上、右、下）；左边另有 3px 竖条
BLOCK_GAP = 7  # 块与块之间的间隔
_URL_LABELED = re.compile(r"([^\s。；，：、（）「」]+)：(https?://[^\s<>「」（）()，。；]+)[ 　]?")
_URL = re.compile(r"(https?://[^\s<>「」（）()，。；]+)[ 　]?")


def _scan(text: str, stops: str, start: int = 0):
    """text 里括号外、第一个 stops 里的字符的位置（从 start 起；括号里的不算）；没有时为 None。"""
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(depth - 1, 0)
        elif depth == 0 and i >= start and ch in stops:
            return i
    return None


def split_head(text: str) -> tuple[str, str]:
    """首句和其余：首句到第一个（括号外的）句号、问号、感叹号为止，或到第一个换行为止（换行不算进两边）；首句长于
    HEAD_MAX 时在第一个括号外的逗号、分号、冒号处断开。head + body（加上去掉的那个换行）就是原文。"""
    line_end = text.find("\n")
    first = text if line_end < 0 else text[:line_end]
    stop = _scan(first, "。！？")
    head = first if stop is None else first[:stop + 1]
    if len(head.lstrip("⚠ ")) > HEAD_MAX:
        comma = _scan(head, "，；：", HEAD_MIN)
        if comma is not None and comma + 1 < len(head):
            head = head[:comma + 1]
    body = text[len(head):]
    if body.startswith("\n"):
        body = body[1:]
    return head, body


def sentences(body: str) -> list[str]:
    """正文按行、再按（括号外的）句号、问号、感叹号拆成一句一行；空行不要。"""
    out = []
    for line in body.split("\n"):
        start = 0
        while True:
            stop = _scan(line[start:], "。！？")
            if stop is None:
                break
            out.append(line[start:start + stop + 1])
            start += stop + 1
        out.append(line[start:])
    return [s for s in out if s.strip()]


def link_marks(text: str, about: bool = False) -> list[tuple[int, int, str, str]]:
    """text 里要换成链接的几处：(起, 止, 链接文字, 地址)，按起点排。「说法：网址」整段换成链接「说法」（后面跟的一个空格
    一并收掉）；单独的网址照写成链接；官网核对页的域名（VERIFY_SHOWN）照写、地址是 VERIFY_URL（两边的空格一并收掉）；
    about 时最后一处「关于」（FREE_NOTICE_LINK）做成打开「关于」窗口的链接。"""
    marks: list[tuple[int, int, str, str]] = []

    def free(at: int) -> bool:
        return not any(a <= at < b for a, b, _, _ in marks)

    for m in _URL_LABELED.finditer(text):
        marks.append((m.start(), m.end(), m.group(1), m.group(2)))
    for m in _URL.finditer(text):
        if free(m.start()):
            marks.append((m.start(), m.end(), m.group(1), m.group(1)))
    for shown in {VERIFY_SHOWN, lang.t(VERIFY_SHOWN)}:
        at = text.find(shown)
        if at >= 0 and free(at):
            a = at - 1 if at > 0 and text[at - 1] == " " else at
            b = at + len(shown)
            b = b + 1 if text[b:b + 1] == " " else b
            marks.append((a, b, shown, VERIFY_URL))
    if about:
        for mark in {FREE_NOTICE_LINK, lang.t(FREE_NOTICE_LINK)}:
            at = text.rfind(mark)
            if at >= 0 and free(at):
                marks.append((at, at + len(mark), mark, ABOUT_LINK))
                break
    marks.sort()
    return marks


def _tail_glued(text: str) -> str:
    """keep_words 以后再把最后 TAIL_KEEP 个字连起来（中间插 KEEP_JOINER）：折行时最后一行至少这么多字。"""
    kept = keep_words(text)
    plain = [i for i, ch in enumerate(kept) if ch != KEEP_JOINER]
    if len(plain) <= TAIL_KEEP:
        return kept
    cut = plain[-TAIL_KEEP]
    head, tail = kept[:cut], kept[cut:].replace(KEEP_JOINER, "")
    return head + KEEP_JOINER.join(tail)


def rich_line(text: str, about: bool = False) -> str:
    """一句纯文字 → 富文本：link_marks 那几处换成带底色的链接（链接文字整个不拆到两行），其余转义；名称、常用词不从中间
    拆开（keep_words），最后几个字连在一起（_tail_glued）。链接前面可以断行（零宽空格），所以链接前的字不会被链接拖着
    孤零零留在上一行之外。"""
    style = link_style()
    out, pos = [], 0
    marks = link_marks(text, about)
    for a, b, label, href in marks:
        out.append(html.escape(keep_words(text[pos:a])))
        glued = KEEP_JOINER.join(label)
        out.append(f'​<a href="{html.escape(href)}" style="{style}">&nbsp;{html.escape(glued)}&nbsp;</a>')
        pos = b
    rest = text[pos:]
    out.append(html.escape(_tail_glued(rest) if len(rest) > TAIL_KEEP or not marks else keep_words(rest)))
    return "".join(out)


def head_html(head: str, about: bool = False) -> str:
    return f"<b>{rich_line(head, about)}</b>"


def body_html(body: str, about: bool = False) -> str:
    return "<br>".join(rich_line(s, about) for s in sentences(body))


def _label(rich: str, name: str, view, links: bool) -> QLabel:
    """富文本标签：有链接时链接由 view.open_link 接（不交给 Qt 自己打开），鼠标停在链接上显示地址；没有链接时不接鼠标
    （按在文字上也能拖动悬浮窗）。"""
    label = QLabel()
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    label.setText(rich)
    label.setOpenExternalLinks(False)
    if links:
        label.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        label.linkActivated.connect(view.open_link)
        label.linkHovered.connect(lambda href: QToolTip.showText(QCursor.pos(), href) if href.startswith("http")
                                  else None)
    else:
        label.setTextInteractionFlags(Qt.NoTextInteraction)
    return label


def build_block(view, name: str, text: str, level: str, btns, tip: str):
    """一条提示的小块：(块, 按钮的网格或 None, 按钮)。text 已转成界面语言；tip 是悬停说明（空串为没有）。"""
    about = name == "free"
    head, body = split_head(text)
    frame = QFrame()
    frame.setObjectName(f"notice-{name}")
    frame.setProperty("noticeBlock", True)
    frame.setProperty("level", level)
    frame.setProperty("noticeText", text)  # 原文（测试、预览核对用）
    frame.setAttribute(Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*BLOCK_MARGINS)
    lay.setSpacing(3)
    title = _label(head_html(head, about), f"notice_{level}", view, bool(link_marks(head, about)))
    lay.addWidget(title)
    labels = [title]
    if body.strip():
        body_label = _label(body_html(body, about), "noticeBody", view, bool(link_marks(body, about)))
        lay.addWidget(body_label)
        labels.append(body_label)
    if tip:
        for label in labels:
            _set_tip(label, tip)
    buttons = []
    for btn_text, accent, action, btn_tip in btns:
        btn = _button(lang.t(btn_text), accent=accent, tip=lang.t(btn_tip or btn_text))
        btn.clicked.connect(lambda _=False, cb=view.action(action): cb())
        buttons.append(btn)
    grid = None
    if buttons:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addStretch(1)
        grid = _grid()
        row.addLayout(grid)
        lay.addLayout(row)
    return frame, grid, buttons


def render(view, rows, tips: dict, shown_tip) -> None:
    """把提示行排进各块（view 是 NoticeView：boxes、model、action、open_link、notice_width）。rows 的文字已转成界面语言；
    tips 为各行的悬停说明，shown_tip(说明) 转成界面语言（路径原样）。按钮放进悬浮窗以后再量（按悬浮窗的样式表）。"""
    width = view.notice_width() if view.notice_width is not None else 0
    inner = max(width - BLOCK_MARGINS[0] - BLOCK_MARGINS[2] - 3, 0)
    for name, text, level, btns in rows:
        tip = shown_tip(tips[name]) if tips.get(name) else ""
        frame, grid, buttons = build_block(view, name, text, level, btns, tip)
        view.boxes[view.model.area(name)].addWidget(frame)
        if name in EQUAL_WIDTH_NOTICES and len(buttons) > 1:
            _equal_width(buttons)
        if grid is not None:
            _fill_grid(grid, buttons, inner)
        frame.show()
