"""筹码列表的数据规格和小工具：块、组、行三份只读数据，去重、排序、悬停说明这些小函数，一行里各部分的位置
（_Metrics），挪动和改之前的样子、拖动中的状态（_Motion、_Snap、_Drag），颜色、圆角和档位按钮、焦点框、小三角
这些几种画法共用的部分，以及动画开关。列表和各项都从这里取，自己不放这些。"""
from __future__ import annotations

import html
import sys
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF

from ...data import SCHOOLS, TIER_LABEL, TIERS
from ..text import tip_paragraphs_html
from ..theme import LIST as L

# ------------------------------------------------------------ 各块共用的常量（列表自己出的提示文字在 view.py）

QUOTED = "「{}」"

TOP, BOTTOM = "top", "bottom"  # 列表的上沿、下沿（提示的位置）

FRAME_MS = 15  # 动画每一帧的间隔
SPI_GETCLIENTAREAANIMATION = 0x1042  # Windows「显示动画」开关的系统参数

# ------------------------------------------------------------ 样子
# 颜色、字号、尺寸都在皮肤里（theme.ListLook），画的时候按当前皮肤取（L.名字）；色号写成 "#rrggbb" 或 (r, g, b, a)，
# 一律经 _color 转换。摆放、拖动、动画的代码不碰颜色。这些名字（FOCUS_RING、ITEM_BASE、LIST_STYLE 等）也能按属性取，
# 见包的 __getattr__。


def _color(c) -> QColor:
    """皮肤里的色号（"#rrggbb" 或 (r, g, b, a)）→ QColor。"""
    if isinstance(c, QColor):
        return QColor(c)
    if isinstance(c, str):
        return QColor(c)
    return QColor(*c)


def _over(base, top) -> QColor:
    """半透明的 top 叠在不透明的 base 上看起来的颜色。"""
    b, t = _color(base), _color(top)
    a = t.alphaF()
    return QColor(round(b.red() + (t.red() - b.red()) * a), round(b.green() + (t.green() - b.green()) * a),
                  round(b.blue() + (t.blue() - b.blue()) * a))


def _item_base() -> QColor:
    """动画、拖动时各项先铺的一层和列表看起来一样的不透明底色（编辑窗口的底色上叠列表的底色）：飞过、滑过、收进去的行
    互相盖住，字不叠在一起。"""
    return _over(L.WINDOW_FILL, L.LIST_FILL)


def _inline_margins() -> tuple[int, int, int, int]:
    """放进列表的控件四周留白（左边和图标对齐）。"""
    return L.ROW_LEFT + L.HANDLE_W + L.HANDLE_GAP, L.INLINE_TOP, L.ROW_RIGHT, L.INLINE_BOTTOM


# ------------------------------------------------------------ 内容


@dataclass(frozen=True)
class RowSpec:
    """一行：chip 筹码名（资料库的键）；tier 亮着的档位按钮（data.TIERS 之一，未标记为 None）；school 名字后面的流派
    （分组里的行为空）；end 行尾文字（一段文字，或几段 (文字, 色号)）和 end_color（一段时的颜色，空为暗色）；end_tip
    行尾的悬停说明；tip 这一行其余地方的悬停说明；name_tip 筹码名、图标的悬停说明（None 为资料库里的效果原文）；
    enabled 四个按钮能不能点；locked 被锁的行：按钮画成不能点的样子，但点了照样发 tier_clicked（由窗口去问）。"""

    chip: str
    tier: str | None = None
    school: str = ""
    end: str | tuple = ""
    end_color: str = ""
    end_tip: str = ""
    tip: str = ""
    name_tip: str | None = None
    enabled: bool = True
    locked: bool = False

    def __post_init__(self):
        if self.tier is not None and self.tier not in TIERS:
            raise ValueError(f"档位应为 {'、'.join(TIERS)} 之一：{self.tier}")
        if not isinstance(self.end, str):
            object.__setattr__(self, "end", tuple((str(text), str(color or "")) for text, color in self.end))

    def end_parts(self) -> tuple[tuple[str, str], ...]:
        """行尾的几段（文字, 色号），色号为空时画成暗色。"""
        if isinstance(self.end, str):
            return ((self.end, self.end_color),) if self.end else ()
        return tuple((text, color) for text, color in self.end if text)


@dataclass(frozen=True)
class GroupSpec:
    """未标记、其他流派里的一组：key 组名（流派名、通用、地图专属；提示里也用它），title 组标题，rows 组里的行，
    open 这个组第一次出现时展开还是收起（之后按 key 记住用户点的收起、展开）。"""

    key: str
    title: str
    rows: tuple = ()
    open: bool = True

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True)
class BlockSpec:
    """一块：key 为档位键（core、first、second、avoid）、BLOCK_UNMARKED 或 BLOCK_OTHER；title 块标题（空字符串时不画
    标题）；rows 直接放在块里的行；groups 块里的组（在 rows 后面）；draggable 块里的行能不能拖着改顺位。"""

    key: str
    title: str
    rows: tuple = ()
    groups: tuple = ()
    draggable: bool = False

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "groups", tuple(self.groups))


def _clean_blocks(blocks: Iterable[BlockSpec]) -> tuple[BlockSpec, ...]:
    """去掉重复的块（同一个 key 只留第一个）和重复的筹码（同一个筹码只留第一次出现的那一行）。"""
    out, keys, chips = [], set(), set()

    def keep(rows):
        kept = []
        for r in rows:
            if r.chip not in chips:
                chips.add(r.chip)
                kept.append(r)
        return tuple(kept)

    for b in blocks:
        if b.key in keys:
            continue
        keys.add(b.key)
        groups, group_keys = [], set()
        rows = keep(b.rows)
        for g in b.groups:
            if g.key in group_keys:
                continue
            group_keys.add(g.key)
            groups.append(replace(g, rows=keep(g.rows)))
        out.append(replace(b, rows=rows, groups=tuple(groups)))
    return tuple(out)


def _structure(blocks: tuple[BlockSpec, ...]) -> tuple:
    """内容的骨架：块、组、行的先后和标题、能不能拖（不含每一行的档位、行尾等）。"""
    return tuple((b.key, b.title, b.draggable, tuple(r.chip for r in b.rows),
                  tuple((g.key, g.title, tuple(r.chip for r in g.rows)) for g in b.groups)) for b in blocks)


def _quoted(names: Iterable[str]) -> str:
    return "".join(QUOTED.format(n) for n in names)


def _school_sorted(names: Iterable[str]) -> list[str]:
    """组名按流派的固定顺序排（data.SCHOOLS），不是流派的排在后面、照原来的先后。"""
    names = list(dict.fromkeys(names))
    return sorted(names, key=lambda n: (SCHOOLS.index(n) if n in SCHOOLS else len(SCHOOLS), names.index(n)))


def _tip_html(text: str) -> str:
    """悬停说明：长句按 text.tip_lines 分行（tip_paragraphs_html，已转义）；每段都放得进一行时也转义成富文本，
    文字里像网页标记的内容原样显示（Qt 会把像网页标记的纯文本当富文本渲染）。"""
    if not text:
        return ""
    out = tip_paragraphs_html(text)
    if out != text:
        return out
    return "<span>" + "<br>".join(html.escape(p) for p in text.split("\n")) + "</span>"


def _ease(p: float) -> float:
    """先快后慢（三次缓出）。"""
    return 1.0 - (1.0 - p) ** 3


# ------------------------------------------------------------ 动画开关

_default_animated: bool | None = None


def set_default_animated(on: bool | None) -> None:
    """所有列表的默认动画开关：True、False，或 None（跟 Windows「显示动画」走；offscreen 平台上不做动画）。
    单个列表用 SchemeList.set_animated 另设。"""
    global _default_animated
    _default_animated = on


def system_animations() -> bool:
    """Windows 设置里的「显示动画」（动画效果）开着没有。不是 Windows、读不到时按开着算。"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        value = wintypes.BOOL(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
    except (AttributeError, OSError, ValueError):
        return True
    return bool(value.value) if ok else True


# ------------------------------------------------------------ 每一项（尺寸和画法共用的小工具）


class _Metrics:
    """字体和一行里各部分的位置（按列表的字体算；流派一栏随出现过的最宽的流派文字变宽，不变窄）。"""

    def __init__(self, base: QFont, names: Iterable[str], school_w: int = 0):
        self.font_row = QFont(base)
        self.font_row.setPointSizeF(L.FONT_ROW)
        self.font_small = QFont(base)
        self.font_small.setPointSizeF(L.FONT_SMALL)
        self.font_title = QFont(self.font_row)
        self.font_title.setBold(True)
        self.fm_row = QFontMetrics(self.font_row)
        self.fm_small = QFontMetrics(self.font_small)
        self.fm_title = QFontMetrics(self.font_title)
        self.button_w = max(self.fm_small.horizontalAdvance(TIER_LABEL[t]) for t in TIERS) + 2 * L.BUTTON_PAD_X + 2
        self.button_h = self.fm_small.height() + 2 * L.BUTTON_PAD_Y + 2
        self.row_h = max(L.ICON, self.button_h) + 2 * L.ROW_PAD_Y
        self.name_w = max((self.fm_row.horizontalAdvance(n) for n in names), default=60) + L.NAME_EXTRA
        self.school_floor = self.fm_small.horizontalAdvance("　" * L.SCHOOL_CHARS)
        self.school_w = max(self.school_floor, school_w)
        x = L.ROW_LEFT
        self.handle_x = x
        x += L.HANDLE_W + L.HANDLE_GAP
        self.icon_x = x
        x += L.ICON + L.ICON_GAP
        self.name_x = x
        x += self.name_w + L.NAME_GAP
        self.school_x = x
        x += self.school_w + L.SCHOOL_GAP
        self.button_x = tuple(x + i * (self.button_w + L.BUTTON_GAP) for i in range(len(TIERS)))
        self.end_x = self.button_x[-1] + self.button_w + L.END_GAP
        self.title_h = self.fm_title.height()
        self.block_h = self.title_h + L.BLOCK_PAD_TOP + L.BLOCK_PAD_BOTTOM
        self.group_h = max(L.FOLD_SIZE, self.title_h) + L.GROUP_PAD_TOP + L.GROUP_PAD_BOTTOM

    def handle_rect(self, h: int) -> QRectF:
        return QRectF(self.handle_x, 3, L.HANDLE_W, max(0, h - 6))

    def icon_rect(self, h: int) -> QRectF:
        return QRectF(self.icon_x, (h - L.ICON) / 2, L.ICON, L.ICON)

    def name_rect(self, h: int) -> QRectF:
        return QRectF(self.name_x, 0, self.name_w, h)

    def grab_rect(self, h: int) -> QRectF:
        """按住能拖的地方：把手、图标、筹码名。"""
        return QRectF(self.handle_x, 0, self.name_x + self.name_w - self.handle_x, h)

    def school_rect(self, h: int) -> QRectF:
        return QRectF(self.school_x, 0, self.school_w, h)

    def button_rect(self, i: int, h: int) -> QRectF:
        return QRectF(self.button_x[i], (h - self.button_h) / 2, self.button_w, self.button_h)

    def end_rect(self, w: int, h: int) -> QRectF:
        return QRectF(self.end_x, 0, max(0, w - L.ROW_RIGHT - self.end_x), h)


def _round_rect(p: QPainter, rect: QRectF, fill, border, radius: float) -> None:
    """圆角矩形：fill 底色、border 1 像素框线（None 为不画）。"""
    p.setBrush(_color(fill) if fill is not None else Qt.NoBrush)
    p.setPen(QPen(_color(border), 1) if border is not None else Qt.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def _tier_on(tier: str, faded: bool = False) -> tuple:
    """皮肤里按档位写的亮着的按钮（ListLook.TIER_ON）：（字, 底, 框）。faded 为真（不能点的行）时底和框淡一些。"""
    text, fill, border = L.TIER_ON[tier]
    if not faded:
        return text, fill, border
    fill, border = _color(fill), _color(border)
    fill.setAlpha(fill.alpha() * 140 // 255)
    border.setAlpha(border.alpha() * 140 // 255)
    return text, fill, border


def _focus_ring(p: QPainter, rect: QRectF, radius: float, bounds: QRectF) -> None:
    """键盘焦点框：套在 rect（按钮）外面一圈，FOCUS_W 粗、外沿离按钮 FOCUS_PAD；超出 bounds（这一项自己）的部分收进来。"""
    half = L.FOCUS_W / 2
    grow = L.FOCUS_PAD - half
    ring = rect.adjusted(-grow, -grow, grow, grow).intersected(bounds.adjusted(half, half, -half, -half))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_color(L.FOCUS_RING), L.FOCUS_W))
    p.drawRoundedRect(ring, radius + grow, radius + grow)


def _triangle(p: QPainter, cx: float, cy: float, size: tuple[int, int], direction: str, color) -> None:
    """实心小三角：direction 为 down（▾）、right（▸）、up。"""
    w, h = size
    p.setPen(Qt.NoPen)
    p.setBrush(_color(color))
    if direction == "right":
        pts = [QPointF(cx - h / 2, cy - w / 2), QPointF(cx + h / 2, cy), QPointF(cx - h / 2, cy + w / 2)]
    elif direction == "up":
        pts = [QPointF(cx - w / 2, cy + h / 2), QPointF(cx + w / 2, cy + h / 2), QPointF(cx, cy - h / 2)]
    else:
        pts = [QPointF(cx - w / 2, cy - h / 2), QPointF(cx + w / 2, cy - h / 2), QPointF(cx, cy + h / 2)]
    p.drawPolygon(QPolygonF(pts))


@dataclass
class _Motion:
    """一项的一段动画：位置 y0 → y1（画布坐标）、透明度 op0 → op1，从 t0 起 ms 毫秒，先快后慢；then 在到终点时调用。
    special 为 True 的（飞到边缘淡出、淡入淡出、展开）在下一次改动开始时直接到终点，只有普通的挪位置会接着改终点。"""

    y0: float
    y1: float
    op0: float
    op1: float
    t0: float
    ms: float
    then: Callable[[], None] | None = None
    special: bool = False


@dataclass
class _Snap:
    """改之前的样子：显示中的各项排好的位置、画面上的位置、先后、属于哪一块哪一组、影子的做法、滚动位置。"""

    placed: dict
    order: list
    vy: dict
    where: dict
    recipes: dict
    headers: dict
    scroll: int
    vh: int
    stops: list  # 能用 Tab 走到的地方（先后），键盘焦点所在的那一行挪走时按它找补位的


@dataclass
class _Drag:
    """拖动中：row 被拖的行（items.py 的 _RowItem），members 本块显示中的行（拖之前的先后），start 原来的位置，
    index 现在空出的位置，top 第一行的位置，pitch 行高，grab 按下处离行顶的距离，pos 鼠标最后的全局位置，
    motion 做不做动画。"""

    row: _RowItem
    block: str
    members: list
    start: int
    index: int
    top: float
    pitch: int
    grab: float
    pos: QPointF
    motion: bool
    head: float = 0.0  # 块标题的位置（自动往上滚到它露出来为止）
