"""「编辑筹码方案」窗口（按功能分成这个包里的几个模块）。

只做再导出：对外的名字（各子模块里定义的和这里从别处取来给大家用的）都能从 advisor.ui.scheme_editor
导入；颜色常量名和样式表（STYLE、DIM_COLOR 等）也能按属性取。

- notes.py：界面文字、规则常数、筹码池提醒 / 默认档位 / 列表内容分块这些纯函数和数据类；
- widgets.py：小部件、造按钮警告的小函数、_Kit；
- layouts.py：七种排法的摆放和 LAYOUTS 表；
- window.py：SchemeEditor 类（状态、信号、读写方案、造部件和接线）。

每种排法里建、摆部件的先后决定 Tab 顺序，改动会影响外观。下面是这个窗口的来龙去脉：

「筹码方案」卡片的「编辑方案」按钮打开，新建、编辑、复制、删除各角色的筹码方案。窗口约 760 × 720，深色、置顶，
不是对话框：打开期间悬浮窗照常识别和推荐。顶部的角色下拉框默认是悬浮窗当前的角色（按六类排，每一类前面一行不能选的
组名），在这里换角色只换编辑的对象，不影响悬浮窗。左边是方案列表（「默认推荐」和该角色的我的方案），悬浮窗对这个角色
正在用的那一套行尾写「使用中」（代替「默认」「我的」；悬浮窗那边换了方案时跟着变）；右边是编辑区和筹码列表。
给悬浮窗当前的角色新建、复制方案（新建空方案、复制、复制为我的方案）时，悬浮窗当场换用新的这套（窗口发 scheme_added，
悬浮窗判断是不是它当前的角色、角色是不是已识别）；给别的角色建的不换。

筹码列表的内容和文字由本模块定（list_layout 分块分组，content 拼成 scheme_list 的 BlockSpec、GroupSpec、
RowSpec），每一行的摆放、画法、拖动和动画由 scheme_list.SchemeList 做；改方案、存盘都在本模块（schemes.Tiers 的
with_tier、moved），改完把新内容整份交给列表：
- 标了档位的筹码按档位归块，从上到下：必选 → 推荐 → 可选 → 不选；块里按顺位（schemes.Tiers），名字后面写流派。
  我的方案里按住行左侧的把手或筹码名在本块里拖动改顺位，点档位按钮改档位（新标的、改了档位的排到那一块最后，
  再点一下同一档取消）。四档都有顺位：同一档的几张在三选一里按顺位排先后。
- 未标记的筹码按流派分组：各流派、通用、地图专属各一组，组标题写本组未标记几个、共几个；每组可收起，地图专属默认收起；
  组里的行不写流派，组上不放整组改档位的按钮。
- 软锁：列进方案的流派（标了可选及以上的，schemes.listed_schools）已有
  一局最多能进的个数（两个）时，其余流派的未标记筹码锁成不选（schemes.locked_schools），排在最后的「其他流派」一块，
  也按流派分组。被锁的行四个按钮看起来不能点（不选亮着）；点必选、推荐、可选时在那一行下面问一次要不要把这个流派
  列进方案（LockQuestion），确认后那一下点的档位生效：这个流派就列进了方案，它其余的筹码回到未标记。锁成的不选不存进
  方案（方案里只记手标的档位），所以哪些流派列进了方案只看标记；一个流派的可选及以上全去掉，它就退出方案，其余列进的
  还有两个时它又被锁（不用问）。被锁流派里手标的不选照旧是手标的不选，排在「不选」一块。
- 列表下方：被锁时常驻一行橙色提示（lock_notice），说明哪些流派按不选计算、怎么列进来；列进方案的流派里有只标了
  可选的，另起一行点名是哪几张可选把它留在方案里、取消以后会怎样（second_only_notes）；其下是灰色的规则提示
  （rule_hints：列进三个以上流派时几个流派按方案的先后竞争、必选较多、同系列低一级标得比高一级高），只提示、不阻止。
- 默认推荐只读：照 schemes.default_marks 的档位和顺位分块显示，不能拖，不列未标记的筹码；没有固定档位的角色写一句
  说明。「复制」「删除」换成「复制为我的方案」：schemes.scheme_from_default 照原样复制，顺位和这里看到的一样；这个角色的
  默认推荐没有固定档位时没有可复制的，不显示这个按钮，只有「新建空方案」（悬停写明新建的不是默认推荐的副本）。
- 「新建空方案」不管选中哪一套都在：建一套空白的我的方案（「方案 1」「方案 2」……），不记「复制自默认推荐」。
- 行尾写这一行在默认推荐里的档位（「默认：可选」，和按钮同一套叫法）或「默认：条件规则」（悬停写规则原文）；标了必选、
  推荐、可选而且有筹码池提醒（pool_notes）时改写提醒，默认档位移到提醒悬停说明的最后一行；被锁的行写「自动不选」。
  未标记的筹码只按角色特性等计算，不用默认推荐的任何部分（自己的方案和默认推荐互斥），悬停照此说明。
- 目标流派由标记推出来（schemes.target_schools：列进方案的流派里有必选、推荐的，可以超过两个），只显示、不能手选，
  随标记即时刷新；它只影响未标记筹码之间的先后。
- 适用地图：一行「适用地图」加一个下拉多选，列出每张能标的地图（schemes.taggable_maps），可以标几张；一张都不标算
  全地图通用，下拉框上写「全地图通用」，标了写着地图名。勾一项、取消一项就存，用 schemes.normalize_maps 整理（按地图
  编号排）；点开以后勾选不收起，点在外面或按 Esc 才收起。默认推荐不显示这一项（它只读，名称、作者、说明也不在）；
  方案文件只读时照其他项一样只能看。标了地图的方案在左边列表里用悬停说明写出地图。
- 搜索（筹码名或效果）和「只看已标记」：只显示符合的行，组里、块里没有符合的行时整组、整块不显示；有筛选时收起的组
  也显示符合的行（收起按钮暂不可用）。

改动即存：档位、顺位、适用地图改了立即存进方案文件；名称、作者、说明停止输入 TEXT_SAVE_MS 毫秒后或离开输入框时存，
同一角色重名时方案存储在名称后加「（2）」。每次改动后发 schemes_changed（方案所属的角色名），悬浮窗据此刷新方案下拉框、
重算正在用的方案。删除在窗口底部确认，不另弹窗口；删的是某个角色正在用的方案时，方案存储把该角色改回默认推荐。
方案文件来自更新版本的拾星助手（store.read_only）时只能查看。筹码列表太长时只有它上下滚动，其余部分不动；窗口放不下
屏幕时按屏幕可用高度缩小，只有筹码列表变短（fit_to_screen）。名称、作者、说明和方案文件里记的筹码名都是用户或文件给的
文字，一律按纯文本显示，不渲染网页标记。

筹码池提醒（pool_notes，只提醒、不阻止）：地图专属的筹码标「仅限地图」，悬停列出能出现的地图；在职业筹码池里、但所在
的职业池都不是这个角色自己的池（初始职业分值高于 STANDARD_WEIGHT 的职业），而且一局里至少见到一次的机会低于
HARD_TO_SEE 的标「较难出现」；不在任何职业池的筹码不标。一局见到的机会按开局前的规划估算（game_chances）：
困难及以上，开局的筹码池，地图未知，按角色的初始职业分值（不计已有筹码）；升星 3 次（1、2、3 星各一次，第一次刷出）、
筹码商店 GAME_SHOPS 次（和升星同样的出法，星数取 1～3 星的平均）、任务 GAME_TASKS 次（任务等级 1～3 平均）、
刷新 GAME_REROLLS 次（刷新后最左格不限职业池，星数取平均）；不算同流派加权和拿走后筹码池变小。"""

from __future__ import annotations

from .. import theme
from ...schemes import AUTHOR_MAX
from .layouts import LAYOUTS
from .notes import (
    AUTHOR_LABEL,
    AUTHOR_PLACEHOLDER,
    BLOCK_TITLE,
    BLOCK_TITLE_FIXED,
    CLASSES,
    CLOSE_BUTTON,
    CORE_MANY,
    COPIED_NOTE,
    COPIED_NOTE_RULES,
    COPY_BUTTON,
    COPY_DEFAULT_BUTTON,
    COPY_NAME,
    COUNT_WORD,
    DEFAULT_EMPTY_GUIDE,
    DEFAULT_NO_SCHOOLS,
    DEFAULT_READ_ONLY,
    DEFAULT_READ_ONLY_EMPTY,
    DEFAULT_ROW,
    DEFAULT_RULE,
    DEFAULT_SCHOOLS,
    DEFAULT_TAG,
    DEFAULT_TIER,
    DELETE_BUTTON,
    DELETE_CONFIRM,
    DELETE_NO,
    DELETE_YES,
    EDITOR_TITLE,
    GAME_REROLLS,
    GAME_SHOPS,
    GAME_STARS,
    GAME_TASKS,
    GROUP_GENERAL,
    GROUP_MAP,
    GROUP_TITLE,
    HARD_TO_SEE,
    HERO_HINT,
    HERO_LABEL,
    HINT_CORE_MANY,
    HINT_SCHOOLS,
    HINT_SERIES,
    HINT_SERIES_UNMARKED,
    HINTS_MAX_LINES,
    LIST_NOTE,
    LIST_SEP,
    LIST_WIDTH,
    LOCK_NO,
    LOCK_NOTICE,
    LOCK_QUESTION,
    LOCK_SECOND_LEAVE,
    LOCK_SECOND_MORE,
    LOCK_SECOND_ONLY,
    LOCK_SECOND_SHOWN,
    LOCK_SECOND_UNLOCK,
    LOCK_SECOND_UNLOCK_ANY,
    LOCK_THESE,
    LOCK_YES,
    LOCKED_END,
    LOCKED_TIP,
    LIST_MAPS,
    MAPS_ALL,
    MAPS_ALL_SHORT,
    MAPS_COUNT,
    MAPS_HINT,
    MAPS_LABEL,
    MAPS_TIP_CHECKED,
    MINE_HEADER,
    MINE_TAG,
    MIN_LIST_HEIGHT,
    NAME_HINT,
    NAME_LABEL,
    NEW_BUTTON,
    NEW_EMPTY_TIP,
    NEW_NAME,
    NO_PLAYER_TIERS,
    NOTE_LABEL,
    NOTE_MAP,
    NOTE_RARE,
    NOTE_TIERS,
    ONLY_MARKED,
    OTHER_GROUP_TITLE,
    OTHER_TITLE,
    POOL_MAP,
    POOL_MAP_TIP,
    POOL_RARE,
    POOL_RARE_TIP,
    POOL_RARE_TIP_MULTI,
    QUOTED,
    READ_ONLY_NOTICE,
    SAVE_FAILED,
    SAVED,
    SCHOOLS_LABEL,
    SEARCH_PLACEHOLDER,
    STANDARD_WEIGHT,
    STALE_NOTE,
    STALE_REMOVE,
    TARGET_COUNT,
    TARGET_COUNT_SEP,
    TARGET_HINT,
    TARGET_NONE,
    TARGET_NOTE,
    TEXT_SAVE_MS,
    TIER_STEP,
    TITLE_ALLOWANCE,
    UNMARKED_TITLE,
    UNMARKED_TIP,
    USED_TAG,
    ListLayout,
    PoolNote,
    _quoted,
    block_title,
    chip_group,
    copied_note,
    copy_name,
    default_ends,
    game_chances,
    group_order,
    list_layout,
    lock_notice,
    max_schools,
    new_name,
    own_classes,
    percent_text,
    pool_notes,
    row_end,
    rule_hints,
    second_only_notes,
    series_hints,
    target_note,
)
from ..scheme_cards import (
    _ID_ROLE,
    _SchemeCardDelegate,
    _SchemeListDelegate,
    _SchemeStripDelegate,
    _SchemeStickerDelegate,
    _SchemeTabDelegate,
    _SchemeTileDelegate,
    _SchemeWellDelegate,
    _TAG_ROLE,
    _USED_ROLE,
)
from .widgets import (
    GAME_KIT,
    ICON_COPY,
    ICON_DELETE,
    ICON_NEW,
    ICON_SEARCH,
    PILL_KIT,
    PLAIN_KIT,
    HintBox,
    LockQuestion,
    MapField,
    TargetSchools,
    _button,
    _CharLimit,
    _char_index,
    _FlowTargets,
    _glyph_icon,
    _HoldWrapped,
    _Kit,
    _LinkedBox,
    _plain_label,
    _row,
    _ShortHints,
    _utf16_len,
    _WrapLabel,
    _WrapWarn,
)
from .window import SchemeEditor


def __getattr__(name: str):
    """颜色常量名和样式表（scheme_editor.STYLE、ACCENT_COLOR 等）：当前皮肤的值，给测试和预览工具用。"""
    if name == "STYLE":
        return theme.editor_style()
    return theme.legacy(__name__, name, ("editor", "list", "common"))
