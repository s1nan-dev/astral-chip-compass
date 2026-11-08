"""「编辑筹码方案」窗口里不依赖界面的部分：界面文字的写法、规则常数，以及按资料库算出的筹码池提醒、
默认档位、列表内容分块、行尾和提示这些纯函数和数据类。另有 content(ed)：按编辑窗口现在的状态（角色、方案、筛选）
拼出筹码列表的内容，读窗口的状态，但不建部件。

窗口、排法、部件分别见 window.py、layouts.py、widgets.py；这几个子模块和外面的模块都从这里取界面文字和规则常数，
改一句话、改一个规则数值都在这里改。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

from ...data import CLASSES, SCHOOLS, TIERS
from ...data import TIER_LABEL as PLAYER_TIER_LABEL
from ...pool import appear_prob, player_pool, slot_dists
from .. import lang, names
from ..scheme_list import BLOCK_OTHER, BLOCK_UNMARKED, BlockSpec, GroupSpec, RowSpec
from ...schemes import (
    LISTED_TIERS,
    NAME_MAX,
    Scheme,
    TIER_LABEL,
    Tiers,
    listed_schools,
    locked_schools,
    school_of,
    target_schools,
)
from ..theme import EDITOR as L

# 拿来和筹码资料比对、当分组和档位的键：必须照资料库的简体写
HANT_KEEP = ("CLASSES", "SCHOOLS", "TIER_LABEL", "PLAYER_TIER_LABEL")

# ------------------------------------------------------------ 界面文字

EDITOR_TITLE = "编辑筹码方案"
HERO_LABEL = "角色"
HERO_HINT = "默认是悬浮窗当前的角色；换成其他角色编辑不影响悬浮窗"
READ_ONLY_NOTICE = "筹码方案文件来自更新版本的拾星助手，当前只能查看，不能修改"
# 左边的方案列表：「默认推荐」和我的方案，行尾暗色写「默认」「我的」；悬浮窗对这个角色正在用的那一套（SchemeStore.current，
# 没有时是「默认推荐」那一行）行尾改写 USED_TAG（「已保存」的颜色，见 scheme_cards），悬浮窗那边换了方案时跟着变
DEFAULT_ROW = "默认推荐"
DEFAULT_TAG = "默认"
MINE_HEADER = "── 我的方案 ──"
MINE_TAG = "我的"
USED_TAG = "使用中"
NEW_BUTTON = "新建空方案"  # 新建一套空白的我的方案（一个筹码都没标），选中哪一套时都在
COPY_BUTTON = "复制"
DELETE_BUTTON = "删除"
# 选中默认推荐、而这个角色的默认推荐有固定档位时代替「复制」「删除」；没有固定档位（schemes.default_marks 为空）时没有
# 可复制的，不显示，只留「新建空方案」，它的悬停写明新建的不是默认推荐的副本（NEW_EMPTY_TIP）
COPY_DEFAULT_BUTTON = "复制为我的方案"
NEW_EMPTY_TIP = ("新建一套空白的我的方案，一个筹码都没标，不是默认推荐的副本：该角色的默认推荐按角色特性计算，"
                 "没有固定的档位可以复制。")
SAVED = "已保存"
SAVE_FAILED = "未能保存到方案文件，本次改动只在关闭程序前有效。"  # 现在的内容不在方案文件里时，在「已保存」的位置用警告色写这一句
CLOSE_BUTTON = "关闭"
# 选中默认推荐时（只读）；没有固定档位时写 DEFAULT_READ_ONLY_EMPTY（只有「新建空方案」）
DEFAULT_READ_ONLY = "默认推荐，不能修改；可复制为我的方案后修改"
DEFAULT_READ_ONLY_EMPTY = "默认推荐，不能修改；可新建空方案自己标档位"
DEFAULT_SCHOOLS = "推荐流派：{schools}"
DEFAULT_NO_SCHOOLS = "推荐流派：无"
NO_PLAYER_TIERS = "该角色的默认推荐按角色特性计算，没有固定的必选、推荐筹码。"
# 选中默认推荐、又没有固定档位时筹码列表一行都没有：空框里写下一步怎么做（SchemeList.set_note 盖在列表上）
DEFAULT_EMPTY_GUIDE = ("想按自己的思路推荐时，点击「新建空方案」按钮新建一套我的方案，在这里给筹码标上必选、推荐、可选、不选，"
                       "没标的照旧按角色特性计算。给悬浮窗当前的角色新建的方案，悬浮窗直接改用。")
# 编辑区
NAME_LABEL = "名称"
NAME_HINT = "最多 12 个字"
NEW_NAME = "方案 {n}"  # 新建的方案：「方案 1」「方案 2」……，取该角色还没用过的最小编号
COPY_NAME = "{name}（副本）"  # 复制我的方案；名称加上「（副本）」超过 12 个字时截短原名称
# 复制默认推荐得来的方案：名称下面的说明。该角色的默认推荐有带条件的规则时用 COPIED_NOTE_RULES（条件规则不随副本复制）
COPIED_NOTE = "复制自默认推荐，{date}；以后默认推荐更新时副本不跟着变"
COPIED_NOTE_RULES = "复制自默认推荐，{date}；带条件的规则不随副本复制，相关筹码按角色特性计算；以后默认推荐更新时副本不跟着变"
AUTHOR_LABEL = "作者"
AUTHOR_PLACEHOLDER = "选填，导出为可读文字时附在末尾"
# 目标流派一栏：只显示推出来的结果（流派名的胶囊，没有时「无」），后接暗色 TARGET_HINT；下面一行暗色 TARGET_NOTE
SCHOOLS_LABEL = "目标流派"
TARGET_NONE = "无"
TARGET_HINT = "按必选、推荐的筹码自动判断"
TARGET_COUNT = "{school}：必选 {core}、推荐 {first}"  # TARGET_NOTE 的前半句：每个目标流派一段，段间 TARGET_COUNT_SEP
TARGET_COUNT_SEP = "；"
TARGET_NOTE = "{counts}。未标记的筹码中，目标流派的优先推荐，会让目标流派本局不再出现的排在后面。"
NOTE_LABEL = "说明"
# 适用地图：一套方案可以标几张地图（多选），一张都不标算全地图通用。对局里认出地图以后，悬浮窗自动换到这个角色标了这张图的
# 方案。左边方案列表里标了地图的那一行在悬停说明里写出地图，通用的不写。
MAPS_LABEL = "适用地图"
MAPS_ALL = "全地图通用"  # 下拉框上一张都没标时写这一句
# 窄的排法（游戏内风格，下拉框和说明并排）里「全地图通用」放不下时写这两个字，不省略成「全…」
MAPS_ALL_SHORT = "通用"
# 标了几张、地图名连起来在下拉框上放不下时改写张数：截成「龙…」看不出标了什么（全标了哪几张写在悬停说明里）。
# 左边紧挨着「适用地图」，只写张数；窄的排法里下拉框只有四五个字宽，写成「2 张地图」也放不下
MAPS_COUNT = "{n} 张"
MAPS_HINT = "不选＝全地图通用；对局中认出标了的地图时，悬浮窗自动换用这套方案"  # 下拉框的悬停说明
MAPS_TIP_CHECKED = "已标地图：{maps}"  # 悬停说明的第二行：全标了哪几张（下拉框上省略了也看得全）
LIST_MAPS = "适用地图：{maps}"
# 筹码列表
SEARCH_PLACEHOLDER = "搜索筹码名称或效果"
ONLY_MARKED = "只看已标记"
LIST_NOTE = "标了档位的筹码按档位排在上方，按住行左侧的 ⋮⋮ 或筹码名拖动，可在同一块里调整顺位；未标记的筹码按流派分组。"
BLOCK_TITLE = "{tier} · {n} 个（拖动调整顺位）"  # 四块的标题（tier 为 TIER_LABEL 的叫法）
BLOCK_TITLE_FIXED = "{tier} · {n} 个"  # 不能拖时（默认推荐、只读的方案文件）
UNMARKED_TITLE = "未标记 · {n} 个（按流派分组）"  # 未标记那一部分：n 为各组未标记的合计（不含被锁的）
GROUP_TITLE = "{group} · 未标记 {n} 个（共 {total} 个）"  # 未标记的一组：total 为本组全部筹码（含已标记的）
GROUP_GENERAL = "通用"  # 不属于任何流派、也不是地图专属的筹码（组名，也是标了档位的行名字后面写的流派）
GROUP_MAP = "地图专属"
OTHER_TITLE = "其他流派 · {n} 个（未列进方案，按不选计算）"  # 被锁成不选的筹码那一块
OTHER_GROUP_TITLE = "{school} · {n} 个"  # 「其他流派」里的一组
LOCKED_END = "自动不选"  # 「其他流派」里每行的行尾
LOCKED_TIP = "「{school}」未列进方案。点击档位按钮可将该流派列进方案。"  # 被锁的行的悬停
DEFAULT_TIER = "默认：{tier}"  # 行尾：默认推荐里的档位，tier 为 data.TIER_LABEL 的叫法（必选、推荐、可选、不选）
DEFAULT_RULE = "默认：条件规则"  # 行尾：默认推荐的规则里出现的筹码，悬停写规则原文
UNMARKED_TIP = "未标记：按角色特性计算"  # 我的方案里未标记的行的悬停
STALE_NOTE = "{n} 个筹码已不在资料库中：{names}"
STALE_REMOVE = "移除"
# 筹码池提醒（行尾橙色；只对标了必选、推荐、可选的行）
POOL_MAP = "仅限地图"
POOL_RARE = "较难出现"
POOL_MAP_TIP = "「{chip}」只出现在地图{maps}的筹码三选一中，其他地图不会出现。"
POOL_RARE_TIP = "「{chip}」只在{classes}职业筹码池中，{hero}的{classes}职业分值是标准值 {weight}，较难出现：一局里见到它的机会约 {percent}%。"
# 筹码在两个（或更多）这样的职业池里时；count 为 COUNT_WORD 里的写法
POOL_RARE_TIP_MULTI = (
    "「{chip}」只在{classes}职业筹码池中，{hero}这{count}项职业分值都是标准值 {weight}，较难出现：一局里见到它的机会约 {percent}%。"
)
COUNT_WORD = {2: "两", 3: "三", 4: "四"}
QUOTED = "「{}」"  # 地图名、职业名、流派名逐个加引号连写：「攻击」「生存」
LIST_SEP = "、"
# 软锁：点被锁流派的档位按钮时在那一行旁边问（LockQuestion）；列表下方常驻的橙色提示（lock_notice）
LOCK_QUESTION = (
    "一局最多进入 {limit} 个流派。将「{school}」也列进方案后，列进方案的几个流派按筹码在方案中的先后竞争："
    "本局先获得哪两个流派的筹码，就进入哪两个流派。"
)
LOCK_YES = "列进方案"
LOCK_NO = "取消"
LOCK_NOTICE = (
    "一局最多进入 {limit} 个流派：方案已列进{listed}，{locked}的 {n} 个筹码按不选计算。"
    "点击这些筹码的档位按钮可将该流派也列进方案。"
)
# 列进方案的流派里只标了可选、没有必选和推荐的：LOCK_NOTICE 下面另起一行，每个这样的流派一句，点名是哪几张可选把它留在
# 方案里（可选超过 LOCK_SECOND_SHOWN 张时写 LOCK_SECOND_MORE），再说取消以后会怎样：列进方案的流派正好是一局能进的个数时
# 写 LOCK_SECOND_UNLOCK（locked 同 LOCK_NOTICE），更多时写 LOCK_SECOND_LEAVE。these 为 LOCK_THESE：「这张」「这两张」「这 5 张」
LOCK_SECOND_ONLY = "「{school}」只标了可选：{chips}。"
LOCK_SECOND_MORE = "{chips}等 {n} 张"
LOCK_SECOND_UNLOCK = "取消{these}的可选，{locked}就不再按不选计算。"
LOCK_SECOND_UNLOCK_ANY = "取消其中一个流派的可选，{locked}就不再按不选计算。"  # 列进的两个流派都只标了可选时合成一句
LOCK_SECOND_LEAVE = "取消{these}的可选，「{school}」就退出方案。"
LOCK_THESE = "这{count}张"  # count：一张时为空，两到四张为 COUNT_WORD 里的写法，再多写「 N 」
# 列表下面的规则提示（灰色；只提示、不阻止）
HINT_SCHOOLS = "方案列进了{schools}{n} 个流派：一局最多进入 {limit} 个，按筹码在方案中的先后竞争。"
HINT_CORE_MANY = "必选筹码较多：同时出现时只能选一个，推荐顺位靠前的"
HINT_SERIES = "「{low}」的档位高于同系列高一级的「{high}」：两张同时出现时按档位比较，不算上位替代。"
HINT_SERIES_UNMARKED = "「{low}」已标记，同系列高一级的「{high}」未标记：两张同时出现时按档位比较，不算上位替代。"
# 删除在窗口底部确认
DELETE_CONFIRM = "删除筹码方案「{name}」？删除后无法恢复。"
DELETE_YES = "删除"
DELETE_NO = "取消"
# ------------------------------------------------------------ 规则和尺寸

HARD_TO_SEE = 0.38  # 「较难出现」：一局里至少见到一次的机会低于这个值（还要不在角色自己的职业池里）
STANDARD_WEIGHT = 15  # 职业分值的标准值：初始职业分值高于它的职业是这个角色自己的职业筹码池
GAME_STARS = (1, 2, 3)  # 一局里升星各一次的星数；任务等级同样取 1～3
GAME_SHOPS = 1.0  # 一局里筹码商店的次数
GAME_TASKS = 3.5  # 一局里完成筹码任务的次数
GAME_REROLLS = 5  # 一局里刷新的次数（开局 2 次 + 每升一星 1 次）
NOTE_TIERS = ("core", "first", "second")  # 有筹码池提醒的档位（不选和未标记的不提醒）
CORE_MANY = 3  # 必选超过这么多个时提示「必选筹码较多」
LOCK_SECOND_SHOWN = 3  # 橙色提示点名只标了可选的流派时最多列几张，再多的写「等 N 张」
# 档位的先后（同系列的提醒用）：必选 > 推荐 > 可选 > 未标记（None）> 不选，和三选一里自己方案的排法相同
TIER_STEP = {"core": 4, "first": 3, "second": 2, None: 1, "avoid": 0}
TEXT_SAVE_MS = 500  # 名称、作者、说明停止输入多久后存
# 窗口打开时的大小（WINDOW_W、WINDOW_H）在皮肤里（theme.EditorLook），这两个名字也能按属性取（包的 __getattr__）
TITLE_ALLOWANCE = 32  # 量不到窗口标题栏的高度时（还没显示）按这么高留出（自己画外框、没有系统标题栏时不留）
LIST_WIDTH = 200  # 左边方案列表的宽度
MIN_LIST_HEIGHT = 56  # 窗口缩到最小时筹码列表至少留这么高（约两行；矮屏幕上放不下时由列表让出地方，折行的提示不截字）
# 列表下面的灰色规则提示最多占几行高（同系列的提醒可能有十几句），再多的在提示框里上下滚动，免得把筹码列表挤没
HINTS_MAX_LINES = 4

NOTE_MAP, NOTE_RARE = "map", "rare"  # PoolNote.kind
# 颜色和样式表在皮肤里（theme.EditorLook、theme.editor_style；筹码列表的见 theme.ListLook），画的时候按当前皮肤取。
# 这些名字（ACCENT_COLOR、BUBBLE_FILL、STYLE、DIM_COLOR 等）也能按属性取，见包的 __getattr__（__init__.py）

# ------------------------------------------------------------ 不依赖界面的部分（筹码池提醒、默认档位、列表内容、提示）


@dataclass(frozen=True)
class PoolNote:
    """一个筹码的筹码池提醒：kind 为 NOTE_MAP（仅限地图）或 NOTE_RARE（较难出现），label 为行尾的四个字，tip 为悬停说明，
    chance 为一局里至少见到一次的机会（只有较难出现的有）。"""

    kind: str
    label: str
    tip: str
    chance: float | None = None


def game_chances(db, hero) -> dict[str, float]:
    """开局前的规划下，一局里至少见到某个筹码一次的机会：筹码名 → 概率。hero 是资料库的角色（data.Hero）。

    只用 pool.py 现有的 player_pool、slot_dists、appear_prob，按角色的初始职业分值算、不传已有筹码：困难及以上，
    开局的筹码池，地图未知（地图专属的筹码不在里面）。升星 1、2、3 星各一次（第一次刷出，最左格按职业分值从职业筹码池抽），
    筹码商店 GAME_SHOPS 次（同升星，星数取平均），任务 GAME_TASKS 次（任务等级 1～3 平均，没有按职业抽的最左格），
    刷新 GAME_REROLLS 次（刷新后的最左格按 rules.toml [pool] reroll_leftmost，和推荐同一个 slot_dists：现在是按职业分值抽、
    职业池按筹码的职业标签建）。一局见到的机会 = 1 − 各次都没出现的概率之积。"""
    pool = player_pool(db, (), None)
    levelup = [slot_dists(db, hero, pool, (), star) for star in GAME_STARS]
    reroll = [slot_dists(db, hero, pool, (), star, reroll=True) for star in GAME_STARS]
    task = [slot_dists(db, hero, pool, (), tier, source="task", tier=tier) for tier in GAME_STARS]
    out = {}
    for chip in pool:
        p_up = [appear_prob(left, general, chip.name) for left, general in levelup]
        p_reroll = [appear_prob(left, general, chip.name) for left, general in reroll]
        p_task = [appear_prob(left, general, chip.name) for left, general in task]
        miss = math.prod(1 - p for p in p_up)
        miss *= (1 - mean(p_up)) ** GAME_SHOPS
        miss *= (1 - mean(p_task)) ** GAME_TASKS
        miss *= (1 - mean(p_reroll)) ** GAME_REROLLS
        out[chip.name] = 1 - miss
    return out


def own_classes(hero) -> frozenset[str]:
    """角色自己的职业筹码池：初始职业分值高于标准值 STANDARD_WEIGHT 的职业。"""
    return frozenset(cls for cls, weight in hero.weights.items() if weight > STANDARD_WEIGHT)


def percent_text(p: float) -> int:
    """「约 N%」里的 N：百分数四舍五入取整。"""
    return math.floor(p * 100 + 0.5)


def _quoted(names) -> str:
    return "".join(QUOTED.format(lang.t(n)) for n in names)


def pool_notes(db, hero_name: str | None) -> dict[str, PoolNote]:
    """编辑窗口的筹码池提醒：筹码名 → PoolNote（只列有提醒的筹码；显示时只用于标了必选、推荐、可选的行）。

    - 地图专属的筹码（不论角色）：「仅限地图」，悬停逐个列出能出现的地图；
    - 在职业筹码池里、所在的职业池都不是这个角色自己的池（own_classes），而且一局里至少见到一次的机会（game_chances）
      低于 HARD_TO_SEE：「较难出现」，悬停写职业池、职业分值和取整后的机会；
    - 不在任何职业池的筹码、地图直接发放的筹码不标。角色资料库未收录时只有地图专属的提醒。"""
    out: dict[str, PoolNote] = {}
    for name, chip in db.chips.items():
        if chip.maps and not chip.granted:
            out[name] = PoolNote(NOTE_MAP, POOL_MAP, POOL_MAP_TIP.format(
                chip=names.chip_name(chip), maps=_quoted([names.map_in(db, m) for m in chip.maps])))
    hero = db.heroes.get(hero_name) if hero_name else None
    if hero is None:
        return out
    own = own_classes(hero)
    for name, chance in game_chances(db, hero).items():
        chip = db.chips[name]
        if name in out or not chip.pools or own & set(chip.pools) or chance >= HARD_TO_SEE:
            continue
        classes = [cls for cls in CLASSES if cls in chip.pools]
        if not classes:  # 职业池里的名字都不在 CLASSES 里：不提醒（资料库不该这样，防越界）
            continue
        fields = dict(chip=names.chip_name(chip), classes=_quoted(classes), hero=names.hero_name(hero),
                      weight=hero.weights.get(classes[0], STANDARD_WEIGHT), percent=percent_text(chance))
        if len(classes) == 1:
            tip = POOL_RARE_TIP.format(**fields)
        else:
            tip = POOL_RARE_TIP_MULTI.format(count=COUNT_WORD.get(len(classes), str(len(classes))), **fields)
        out[name] = PoolNote(NOTE_RARE, POOL_RARE, tip, chance)
    return out


def default_ends(db, hero_name: str | None) -> dict[str, tuple[str, str]]:
    """行尾的默认档位：筹码名 → （行尾文字, 悬停说明）。按该角色的默认推荐（资料库的玩家思路）：不带条件的四档写
    「默认：可选」等（没有悬停说明）；默认推荐的规则里出现的筹码写「默认：条件规则」，悬停写规则原文。没有默认推荐的
    固定档位和规则时为空。只列能标档位的筹码（地图直接发放的不列）。"""
    ps = db.player_synergy.get(hero_name) if hero_name else None
    if ps is None:
        return {}
    reasons: dict[str, list[str]] = {}
    for rule in ps.rules:
        for chip in rule.chips:
            reasons.setdefault(chip, []).append(rule.reason)
    out: dict[str, tuple[str, str]] = {}
    for chip, tier in ps.tiers.items():
        if chip in db.chips and not db.chips[chip].granted and chip not in reasons and tier in PLAYER_TIER_LABEL:
            out[chip] = (DEFAULT_TIER.format(tier=lang.t(PLAYER_TIER_LABEL[tier])), "")
    for chip, texts in reasons.items():
        if chip in db.chips and not db.chips[chip].granted:
            out[chip] = (DEFAULT_RULE, "\n".join(dict.fromkeys(texts)))
    return out


def max_schools(db) -> int:
    """一局最多能进入几个流派（rules.toml [pool] max_schools）。"""
    return db.rules["pool"]["max_schools"]


def chip_group(chip) -> str:
    """筹码在未标记那一部分的分组：地图专属、流派名或「通用」。标了档位的行名字后面也照这个写。"""
    if chip.maps:
        return GROUP_MAP
    return chip.school if chip.school in SCHOOLS else GROUP_GENERAL


def group_order() -> tuple[str, ...]:
    return (*SCHOOLS, GROUP_GENERAL, GROUP_MAP)


@dataclass(frozen=True)
class ListLayout:
    """筹码列表各部分放哪些筹码。

    - blocks：（档位, 这一档的筹码按顺位），四档按必选 → 推荐 → 可选 → 不选都在，空的也在。照方案里的顺位原样列出
      （方案文件只存能标档位的筹码），列表上第几行就是第几位，拖动松手的位置直接用作顺位；
    - unmarked：（组, 本组未标记的能标档位的筹码按编号），各流派、通用、地图专属按 group_order，不含被锁的流派，空组也在；
    - other：（被锁的流派, 它的未标记筹码按编号），按 data.SCHOOLS，空组也在；没有被锁的流派时为空；
    - totals：（组, 本组全部能标档位的筹码个数，含已标记的），未标记组标题里的「共 N 个」。
    默认推荐（mine 为 False）只有 blocks。"""

    blocks: tuple[tuple[str, tuple[str, ...]], ...]
    unmarked: tuple[tuple[str, tuple[str, ...]], ...] = ()
    other: tuple[tuple[str, tuple[str, ...]], ...] = ()
    totals: tuple[tuple[str, int], ...] = ()

    def names(self) -> list[str]:
        """全部行的筹码名，按列表从上到下的先后。"""
        return [n for part in (self.blocks, self.unmarked, self.other) for _, names in part for n in names]


def list_layout(db, catalog, tiers: Tiers, mine: bool = True) -> ListLayout:
    """按档位和软锁把能标档位的筹码分到各块、各组（ListLayout）。tiers 是我的方案的标记，或默认推荐的
    schemes.default_marks；mine 为 False（默认推荐）时只分四块。被锁的筹码 = 被锁流派里自己没有标记的筹码；
    被锁流派里手标的不选照旧在「不选」一块。"""
    blocks = tuple((t, tiers.order(t)) for t in TIERS)
    if not mine:
        return ListLayout(blocks)
    markable = catalog.sort_chips(catalog.markable)
    locked = set(locked_schools(db, tiers))
    groups: dict[str, list[str]] = {g: [] for g in group_order()}
    other: dict[str, list[str]] = {s: [] for s in SCHOOLS if s in locked}
    totals: dict[str, int] = dict.fromkeys(group_order(), 0)
    for name in markable:
        group = chip_group(db.chips[name])
        totals[group] += 1
        if name in tiers:
            continue
        (other if group in locked else groups)[group].append(name)
    return ListLayout(
        blocks=blocks,
        unmarked=tuple((g, tuple(groups[g])) for g in group_order() if g not in locked),
        other=tuple((s, tuple(names)) for s, names in other.items()),
        totals=tuple(totals.items()),
    )


def block_title(tier: str, n: int, draggable: bool) -> str:
    """四块的标题：「必选 · 6 个（拖动调整顺位）」；不能拖时（默认推荐、只读）「必选 · 3 个」。"""
    return (BLOCK_TITLE if draggable else BLOCK_TITLE_FIXED).format(tier=lang.t(TIER_LABEL[tier]), n=n)


def row_end(name: str, tier: str | None, *, mine: bool, locked_school: str | None,
            ends: dict[str, tuple[str, str]], notes: dict[str, PoolNote]) -> tuple[str, str, str, str]:
    """一行的（行尾文字, 行尾颜色, 行尾悬停, 整行悬停），都是纯文本。

    - 默认推荐里的档位写「默认：可选」，规则里出现的写「默认：条件规则」（悬停写规则原文）；
    - 标了必选、推荐、可选而且有筹码池提醒时改写提醒（橙色），默认档位移到提醒悬停的最后一行；
    - 被锁的行（locked_school 为它的流派）写「自动不选」，默认档位和规则原文挪进行尾的悬停，整行悬停说明怎么列进方案；
    - 我的方案里未标记的行，整行悬停写「未标记：按角色特性计算」。"""
    text, tip = ends.get(name, ("", ""))
    if locked_school is not None:
        end_tip = "\n".join(x for x in (text, tip) if x)
        return LOCKED_END, L.END_COLOR, end_tip, LOCKED_TIP.format(school=lang.t(locked_school))
    color = L.END_COLOR
    note = notes.get(name) if tier in NOTE_TIERS else None
    if note is not None:
        tip = note.tip + ("\n" + text if text else "")
        text, color = note.label, L.END_WARN_COLOR
    return text, color, tip, UNMARKED_TIP if mine and tier is None else ""


def lock_notice(db, catalog, tiers) -> str:
    """列表下方常驻的橙色提示：有被锁的流派、而且其中还有被锁成不选的筹码时写明哪些流派、几个筹码按不选计算，
    以及怎么把流派列进方案；没有时为空（锁解除时消失）。个数只算被锁的筹码（被锁流派里手标了不选的不算），
    和「其他流派」一块的个数相同。列进方案的流派里有只标了可选的，下面另起一行点名（second_only_notes）：
    标了可选也算列进方案，只剩一两张可选时看不出是哪几张把这个流派留在了方案里。"""
    locked = locked_schools(db, tiers)
    if not locked:
        return ""
    counts = dict.fromkeys(locked, 0)
    for name in catalog.markable:
        school = school_of(db, name)
        if school in counts and name not in tiers:
            counts[school] += 1
    n = sum(counts.values())
    if not n:
        return ""
    listed = listed_schools(db, tiers)
    shown = _quoted(s for s in locked if counts[s])
    notice = LOCK_NOTICE.format(limit=max_schools(db), listed=_quoted(listed), locked=shown, n=n)
    extra = second_only_notes(db, tiers, listed, shown)
    return notice + "\n" + extra if extra else notice


def second_only_notes(db, tiers, listed, locked: str) -> str:
    """lock_notice 的第二行：列进方案的流派（listed）里只标了可选、没有必选和推荐的，按 data.SCHOOLS 的顺序每个一句，
    列出它标了可选的筹码（按可选一块的顺位，超过 LOCK_SECOND_SHOWN 张时只列前几张），再说取消以后会怎样：列进方案的
    流派正好是一局能进的个数时，取消哪一个流派的可选都会解锁 locked（第一行里按不选计算的流派），两个都只标了可选时合成
    一句 LOCK_SECOND_UNLOCK_ANY；更多时这个流派退出方案，其余的锁照旧。没有这样的流派时为空。"""
    targets = set(target_schools(db, tiers))
    seconds = tiers.order("second")
    unlock = len(listed) == max_schools(db)
    heads, tails = [], []
    for school in listed:
        chips_in = [name for name in seconds if school_of(db, name) == school]
        if school in targets or not chips_in:
            continue
        k = len(chips_in)
        chips = LIST_SEP.join(names.chip_in(db, n) for n in chips_in[:LOCK_SECOND_SHOWN])
        if k > LOCK_SECOND_SHOWN:
            chips = LOCK_SECOND_MORE.format(chips=chips, n=k)
        these = LOCK_THESE.format(count="" if k == 1 else COUNT_WORD.get(k, f" {k} "))
        heads.append(LOCK_SECOND_ONLY.format(school=lang.t(school), chips=chips))
        # locked 是 lock_notice 用 _quoted 拼好的（已按界面语言转过），这里和下面合成的一句都原样用
        tails.append(LOCK_SECOND_UNLOCK.format(these=these, locked=locked) if unlock
                     else LOCK_SECOND_LEAVE.format(these=these, school=lang.t(school)))
    if unlock and len(heads) > 1:
        return "".join(heads) + LOCK_SECOND_UNLOCK_ANY.format(locked=locked)
    return "".join(h + t for h, t in zip(heads, tails))


def series_hints(db, tiers) -> list[str]:
    """同系列低一级标得比高一级高（含高一级没标）时的提醒：两张同时出现时按档位比较，不算上位替代。
    只看算严格上位替代的同系列对（series.toml 的 strict）；先后按 TIER_STEP，低一级要标了必选、
    推荐、可选才算（低一级是不选或未标记时照旧替代）。按同系列表的顺序，每一对一句。"""
    out = []
    for pair in db.series:
        if not pair.strict:
            continue
        low, high = tiers.get(pair.low), tiers.get(pair.high)
        if low not in LISTED_TIERS or TIER_STEP[low] <= TIER_STEP[high]:
            continue
        out.append((HINT_SERIES if high is not None else HINT_SERIES_UNMARKED).format(
            low=names.chip_in(db, pair.low), high=names.chip_in(db, pair.high)))
    return out


def rule_hints(db, scheme: Scheme) -> list[str]:
    """列表下面的灰色规则提示（只提示、不阻止），按当前标记：

    - 列进方案的流派超过一局能进入的个数时：几个流派按筹码在方案中的先后竞争；
    - 必选超过 CORE_MANY 个；
    - 同系列低一级标得比高一级高（series_hints）。"""
    tiers = scheme.tiers
    hints: list[str] = []
    listed = listed_schools(db, tiers)
    limit = max_schools(db)
    if len(listed) > limit:
        hints.append(HINT_SCHOOLS.format(schools=_quoted(listed), n=len(listed), limit=limit))
    if len(tiers.order("core")) > CORE_MANY:
        hints.append(HINT_CORE_MANY)
    hints.extend(series_hints(db, tiers))
    return hints


def target_note(db, tiers) -> str:
    """目标流派一栏下面的说明：每个目标流派标了几个必选、几个推荐，再说明目标流派管什么；没有目标流派时为空。"""
    targets = target_schools(db, tiers)
    if not targets:
        return ""
    parts = []
    for school in targets:
        core = sum(1 for n in tiers.order("core") if school_of(db, n) == school)
        first = sum(1 for n in tiers.order("first") if school_of(db, n) == school)
        parts.append(TARGET_COUNT.format(school=lang.t(school), core=core, first=first))
    return TARGET_NOTE.format(counts=TARGET_COUNT_SEP.join(parts))


def copied_note(db, scheme: Scheme) -> str:
    """复制默认推荐得来的方案名称下面的说明；该角色的默认推荐有带条件的规则时另说明规则不随副本复制。不是复制来的为空。"""
    if not scheme.copied_from_default:
        return ""
    ps = db.player_synergy.get(scheme.hero)
    text = COPIED_NOTE_RULES if ps is not None and ps.rules else COPIED_NOTE
    return text.format(date=scheme.copied_from_default)


def copy_name(name: str) -> str:
    """复制我的方案时的名称「X（副本）」：加上「（副本）」超过 NAME_MAX 个字时截短 X。"""
    room = NAME_MAX - len(COPY_NAME.format(name=""))
    return COPY_NAME.format(name=name[:max(room, 0)].rstrip())


def new_name(taken) -> str:
    """新建方案的名称：「方案 1」「方案 2」……里还没被占用的编号最小的一个。"""
    taken = set(taken)
    n = 1
    while NEW_NAME.format(n=n) in taken:
        n += 1
    return NEW_NAME.format(n=n)


def content(ed) -> list[BlockSpec]:
    """列表的内容：四块（没有筹码的档位不显示）、未标记的各组、「其他流派」的各组（没有行的组、块不显示），
    每一行的档位、流派、行尾和悬停（list_layout、row_end）。"""
    mine = ed.scheme is not None
    writable = mine and not ed.store.read_only
    tiers = ed._tiers()
    layout = list_layout(ed.db, ed.catalog, tiers, mine)

    def row(name: str, tier: str | None, school: str = "", locked_school: str | None = None) -> RowSpec:
        end, color, end_tip, tip = row_end(name, tier, mine=mine, locked_school=locked_school,
                                           ends=ed._ends, notes=ed._notes)
        chip = ed.db.chips.get(name)
        name_tip = None
        if chip is not None and chip.maps:
            name_tip = chip.effect + "\n" + POOL_MAP_TIP.format(
                chip=names.chip_name(chip), maps=_quoted([names.map_in(ed.db, m) for m in chip.maps]))
        return RowSpec(chip=name, tier="avoid" if locked_school is not None else tier, school=school, end=end,
                       end_color=color, end_tip=end_tip, tip=tip, name_tip=name_tip, enabled=writable,
                       locked=locked_school is not None)

    def school_text(name: str) -> str:
        chip = ed.db.chips.get(name)
        return chip_group(chip) if chip is not None else ""

    blocks = [BlockSpec(key=tier, title=block_title(tier, len(names), writable),
                        rows=tuple(row(n, tier, school_text(n)) for n in names), draggable=writable)
              for tier, names in layout.blocks if names]
    totals = dict(layout.totals)
    groups = tuple(GroupSpec(key=g, title=GROUP_TITLE.format(group=lang.t(g), n=len(names), total=totals[g]),
                             rows=tuple(row(n, None) for n in names), open=g != GROUP_MAP)
                   for g, names in layout.unmarked if names)
    if groups:
        n = sum(len(g.rows) for g in groups)
        blocks.append(BlockSpec(key=BLOCK_UNMARKED, title=UNMARKED_TITLE.format(n=n), groups=groups))
    groups = tuple(GroupSpec(key=s, title=OTHER_GROUP_TITLE.format(school=lang.t(s), n=len(names)),
                             rows=tuple(row(n, None, locked_school=s) for n in names))
                   for s, names in layout.other if names)
    if groups:
        n = sum(len(g.rows) for g in groups)
        blocks.append(BlockSpec(key=BLOCK_OTHER, title=OTHER_TITLE.format(n=n), groups=groups))
    return blocks


lang.localize(globals())
