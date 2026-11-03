"""「编辑筹码方案」窗口的筹码列表：自己摆放每一行，拖动改顺位，改档位时那一行飞进新的块。

内容由窗口整份交进来（BlockSpec → GroupSpec → RowSpec，都是只读的数据）：标了档位的按档位分块（必选 → 推荐 → 可选 →
不选，块里按顺位），未标记的按流派分组，被锁的流派在最后的「其他流派」一块。列表只负责摆放、拖动和动画，不改方案：
点档位按钮、拖动松手只发信号（tier_clicked、row_moved），窗口改好方案、存好以后再把新内容交进来（set_content）。

- 摆放：每一项（块标题、组标题、行、窗口放进来的问话）都是画布上的一个子控件，位置由本模块算好以后直接摆，不经过 Qt 的
  布局；行高一律相同，拖动时按行高算空位。块、组、行的样子在 specs.py 的「样子」一节和 items.py 各项的 paintEvent
  里（每一行怎么画），拖动和动画只改位置和透明度、不管颜色，换皮肤时只换样子那部分。
- 拖动：按住行左侧的 ⋮⋮、图标或筹码名拖，被拖的那一行跟着鼠标走、略微高亮，
  同一块里相邻的行滑开让出空位；拖出块的边界时停在第一个或最后一个（只在本块里拖）；松手滑进空位，Esc 取消、滑回
  原位；拖到列表上下边缘时自动滚动（本块在那个方向还有没露出来的行时才滚）。
- 改档位：内容换了以后，换了块的行从原处飞到新处，原处下面的行往上补、新处下面的行往下让，落定后闪一下；
  要去的地方不在画面里时画面不滚动，那一行朝那个方向飞到列表上沿或下沿淡出，那一边出一行提示约 2 秒。上锁、解锁
  一次动十几个筹码：整组从「未标记」挪到「其他流派」（或挪回），原处整组收起、新处展开，不逐个飞，同样出一行提示。
- 补回滚动位置：内容变了以后，改之前画面里最上面一项位置没被改动的（同一块同一组里的行、同一个标题）留在画面上的
  原位置，上面的块变长变短时画面不跳；连着标几个筹码时画面停在原处，下一行补到刚点过的位置。
- 动画约 0.25 秒、先快后慢。Windows 设置里关了「显示动画」时直接到位（提示照出，闪一下改成短暂亮一下）；
  Qt 的 offscreen 平台（测试、离屏画图）默认不做动画，测试结果不受时间影响。
- 样子（颜色、字号、尺寸）在皮肤里（theme.ListLook，样式表 theme.list_style），画的时候按当前皮肤取（L.名字）；换皮肤时
  窗口调用 apply_skin，当场按新皮肤重画（正在拖动、正在做动画时照常接着做）。

本包分成几块：specs.py 是数据规格和尺寸、画法共用的小工具，items.py 是画布上的条目（行、标题、问话那一层、提示），
view.py 是列表本体 SchemeList；这里只把对外的名字再导出，零散的说明见各子模块开头。
"""
from __future__ import annotations

from . import specs
from .. import theme
from .items import (  # noqa: F401  对外的名字都从这里取
    HINT_FADE_MS,
    HINT_MS,
    _EdgeHint,
    _HeaderItem,
    _InlineHost,
    _Item,
    _RowItem,
)
from .specs import (  # noqa: F401
    BOTTOM,
    FRAME_MS,
    QUOTED,
    SPI_GETCLIENTAREAANIMATION,
    TOP,
    BlockSpec,
    GroupSpec,
    RowSpec,
    _clean_blocks,
    _color,
    _Drag,
    _ease,
    _focus_ring,
    _inline_margins,
    _item_base,
    _Metrics,
    _Motion,
    _over,
    _quoted,
    _round_rect,
    _school_sorted,
    _Snap,
    _structure,
    _tier_on,
    _tip_html,
    _triangle,
    set_default_animated,
    system_animations,
)
from .view import (  # noqa: F401
    BLOCK_OTHER,
    BLOCK_UNMARKED,
    EDGE_SPEED,
    EDGE_ZONE,
    FLASH_MS,
    FLY_MAX,
    HINT_LOCKED,
    HINT_OTHER,
    HINT_TIER,
    HINT_UNMARKED,
    HINT_UNLOCKED,
    MOVE_MS,
    NO_ANIMATION_PLATFORMS,
    SCROLL_MARGIN,
    SETTLE_MS,
    SHIFT_MS,
    SchemeList,
)


def __getattr__(name: str):
    """外观常量名（scheme_list.FOCUS_RING、HANDLE_W、ITEM_BASE、LIST_STYLE 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "ITEM_BASE":
        return _item_base()
    if name == "LIST_STYLE":
        return theme.list_style()
    if name == "INLINE_MARGINS":
        return _inline_margins()
    if name == "_default_animated":  # 运行时会被改的开关，按 specs 里的那份读
        return specs._default_animated
    return theme.legacy(__name__, name, ("list", "common"))
