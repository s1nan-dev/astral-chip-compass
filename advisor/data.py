"""加载 data/ 下的数据，并提供名称的模糊匹配。

资料库对不上时降级运行，不拒绝启动：适配表里引用了不存在的筹码或角色、缺少某个角色的自动层等情况
都记进 DB.warnings（同时写日志），推荐照常给出；缺自动层的角色只用通用规则。
写坏的一项同样只记警告、跳过这一项：适配表某一段写成了非数组、某条规则缺 chips 或 score、字段类型不对，
chips.json、heroes.json 里某个筹码或角色缺字段，maps.json、mutators.json、meta.json 整份读不出或某一项写得不对。
rules.toml 里引用的筹码名、特性名、组合规则名、地图名，推荐要用到的键和取值范围（除零的几项）载入时核对，
对不上记警告（_check_rules；[sources] 的筹码名、角色名和适配表规则引用的来源名另由 _load_sources、load_db 核对）。
资料里出现名单（CLASSES、SCHOOLS、RARITIES）以外的职业、流派、稀有度时记警告。
DB.problems 只放严重错误：目前只有 synergy.toml 格式错误、整份读不出来这一种（调用方据此提示「适配表存在错误」）。
chips.json、heroes.json、rules.toml 是推荐离不开的三份，整份读不出来（文件不在、不是 UTF-8、格式错误、最外层的结构不对）
时抛 DataError，消息写明文件名和出错的位置。
series.toml（同系列筹码）读不出来或某一对写得不对时只记警告，忽略读不出的部分，推荐照常给出（不写对比行、不标上位替代）。
"""
from __future__ import annotations

import difflib
import json
import logging
import math
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import clock
from .paths import data_dir

log = logging.getLogger(__name__)

CLASSES = ("攻击", "卡牌", "辅助", "生存")
SCHOOLS = ("星光", "治愈", "标记", "充能")
RARITIES = ("蓝", "紫", "金")
# 稀有度的颜色称呼：同系列的对比行「比金色多 4 层星光」、悬浮窗上的「被金色上位替代」（data/series.toml）
RARITY_WORD = {"蓝": "蓝色", "紫": "紫色", "金": "金色"}
TIERS = ("core", "first", "second", "avoid")
# 四档在界面上的叫法，和筹码方案的档位同一套（schemes.TIER_LABEL）；内部的档位键 TIERS 不变
TIER_LABEL = {"core": "必选", "first": "推荐", "second": "可选", "avoid": "不选"}
# 玩家思路的来源：按攻略视频和实际打法整理的思路（两者冲突时以视频为准），记作「攻略」。适配表里没写 source 时也按「攻略」处理
SOURCE_GUIDE = "攻略"
SOURCES = (SOURCE_GUIDE,)
GENERAL_TRAIT = "通用"
# 筹码标签里的「通用」：不是职业，游戏内置推荐的标签分不给它；同系列的严格上位替代比标签时不计它（strict_problems）
GENERAL_TAG = "通用"
UNRECORDED = "资料库未收录"
# 合作挑战的难度，也是地图资料库第二节「进度上限」一列的排列顺序（极限只在个别地图限时开放，不单列，另记在 MapInfo 的
# extreme_cap、extreme_tasks）。
# 困难、噩梦、疯狂、极限的筹码概率相同，稀有度概率表只区分「普通」和「困难及以上」（NORMAL / HARD，按三选一的概率行
# 判定）；具体难度另记在档位（LEVEL_*）和难度名里，用于「难度」分项（噩梦及以上的摩托头盔-高级加分、难度名是极限时的
# 几个筹码加分），以及知道具体难度时进度上限取那一难度的一列（MapInfo.cap_for）
DIFFICULTIES = ("普通", "困难", "噩梦", "疯狂")
NORMAL = "normal"  # 本局难度为普通（State.difficulty、Session 记下的本局难度）
HARD = "hard"  # 本局难度为困难及以上（只在 Session 里记；State.difficulty 里困难及以上和尚未判定都记为 None）
EXTREME = "极限"  # 极限难度的难度名（只有几张地图有，进度上限、任务数见 MapInfo 的 extreme_cap、extreme_tasks）
# 游戏「选择难度」界面上的全部难度名（画面上读到的难度、State.difficulty_name 的取值）。
# DIFFICULTIES 只列每张地图都有进度上限资料的四个难度，不含极限
DIFFICULTY_NAMES = (*DIFFICULTIES, EXTREME)
_NIGHTMARE_NAMES = DIFFICULTY_NAMES[2:]  # 噩梦、疯狂、极限
MAP_CODE_MAX = 63  # 地图编号最大是几（MapInfo.code）：分享方案的短字符串里一个地图编号占 6 位
# 本局难度的档位（State.difficulty_level）：合并画面读数、进度上限推算和概率行的结果，None 为不知道。
# 只有 LEVEL_NIGHTMARE 改变推荐（rules.toml [survival] nightmare_*）；其余档位和 None 一样维持困难及以上的计算
LEVEL_NORMAL = NORMAL  # 普通
LEVEL_HARD = "hard_only"  # 确定是困难
LEVEL_NIGHTMARE = "nightmare_plus"  # 噩梦、疯狂、极限之一（可能分不出是哪一个）
LEVEL_HARD_PLUS = "hard_plus"  # 困难及以上，分不出是困难还是噩梦及以上
LEVEL_LABEL = {LEVEL_NORMAL: "普通", LEVEL_HARD: "困难", LEVEL_NIGHTMARE: "噩梦及以上", LEVEL_HARD_PLUS: "困难及以上"}
# 规则（rule）的 difficulty 条件：适配表里写的难度名 → State.difficulty 的取值。只在本局难度识别为这个难度时生效。
# State.difficulty 只分得出普通（困难及以上和尚未判定都是 None），所以只能写「普通」
RULE_DIFFICULTY = {"普通": NORMAL}

# 名称匹配（资料库还没收录的新筹码不硬配成同系列的旧筹码）：相似度低于 MATCH_MIN 判为未收录；
# 在 MATCH_MIN ~ MATCH_SURE 之间时，给了稀有度（图标边框颜色）且与资料库不符也判为未收录
MATCH_MIN = 0.5
MATCH_SURE = 0.8
# 相似度与最高值相差不超过 MATCH_BAND 的筹码都算候选，再按稀有度和同系列挑（同系列筹码的相似度常常并列或只差一点：
# 「拳击手套」对电击手套 0.75、对拳击手套-中级 0.727）
MATCH_BAND = 0.05


@dataclass(frozen=True, eq=False)
class Chip:
    name: str
    en: str
    rarity: str
    icon: str
    group: str
    school: str | None
    maps: tuple[str, ...]
    granted: bool
    effect: str
    base: int
    growth_round: int | None
    tags: tuple[str, ...]
    pools: tuple[str, ...]
    class_points: dict[str, int]
    fit: frozenset[str]
    fit_minor: frozenset[str]
    # 游戏繁体中文界面里的名字（chips.json 的 hant，默认没有）：认筹码名字的地方（resolve_chip、match_chip）一并认，
    # 程序内部、方案和分享码仍用简体名做键
    hant: str = ""
    # 旧名、俗称（chips.json 的 aliases，默认没有）：认筹码名字的地方（resolve_chip、match_chip）一并当成本筹码的名字，
    # 分享码和方案文件里的旧名另有 data/chip_codes.json 的 old_names 认（改了名的筹码编号不变）
    aliases: tuple[str, ...] = ()
    unrecorded: bool = False  # 资料库未收录的筹码（识别到的名称不在筹码表中），只在推荐里临时构造


def unrecorded_chip(name: str, rarity: str | None = None) -> Chip:
    """资料库未收录的筹码：没有效果、标签和分值，推荐里按中性分参与比较。rarity 不知道时为空字符串。"""
    return Chip(
        name=name, en="", rarity=rarity or "", icon="", group=UNRECORDED, school=None, maps=(),
        granted=False, effect=UNRECORDED, base=0, growth_round=None, tags=(), pools=(),
        class_points={}, fit=frozenset(), fit_minor=frozenset(), unrecorded=True,
    )


@dataclass(frozen=True, eq=False)
class Hero:
    id: int
    name: str
    en: str
    full_name: str
    aliases: tuple[str, ...]
    group: str
    atk: int
    dfn: int
    hp: int
    potential: bool
    weights: dict[str, int]
    title: str = ""  # 游戏选角界面上显示的叫法：称号，或联动角色的简称
    # 游戏繁体中文界面里的名字、称号、全名（heroes.json 的 hant、hant_title、hant_full_name）：认角色时一并认，
    # 程序内部、方案和分享码仍用简体名做键
    hant: str = ""
    hant_title: str = ""
    hant_full_name: str = ""
    unrecorded: bool = False

    @property
    def label(self) -> str:
        """给人看的名字：称号 + 角色名，例如「新人调查员 · 凛」「汉娜 · 远野汉娜」。"""
        if self.title and self.title != self.name:
            return f"{self.title} · {self.name}"
        if self.full_name != self.name:
            return f"{self.name} · {self.full_name}"
        return self.name


def unrecorded_hero(name: str) -> Hero:
    """资料库未收录的角色：四个职业权重相同（最左格的职业筹码池平均分配），不属于任何职业。"""
    return Hero(
        id=0, name=name, en="", full_name=name, aliases=(name,), group=UNRECORDED, atk=0, dfn=0, hp=0,
        potential=False, weights={c: 15 for c in CLASSES}, title="", unrecorded=True,
    )


# 角色的分组（Hero.group、heroes.json 的 group）：和机制文档 mechanics/characters.md 第三章的六类一致，固定按这个顺序排。
# 悬浮窗、编辑方案、导入/导出三处的角色下拉框和审查清单的分节都按它（heroes_in_group_order），同一类里保持资料库里的先后。
# 不参与推荐打分。
HERO_GROUPS = ("物理主C", "法术C", "辅C", "钱辅", "奶辅", "功能辅")


def heroes_in_group_order(heroes: Iterable[Hero]) -> list[Hero]:
    """按 HERO_GROUPS 的顺序排角色，同一类里保持传入的先后；不在六类里的组排在最后，按组首次出现的先后。"""
    heroes = list(heroes)
    order = {g: i for i, g in enumerate(HERO_GROUPS)}
    for group in (h.group for h in heroes):
        order.setdefault(group, len(order))
    return sorted(heroes, key=lambda h: order[h.group])


@dataclass(frozen=True, eq=False)
class Rule:
    chips: frozenset[str]
    score: float
    reason: str
    origin: str  # 特性名、「角色」或「玩家思路」
    maps: frozenset[str] = frozenset()
    needs_owned: frozenset[str] = frozenset()
    max_round: int | None = None
    source: str = ""  # 玩家思路的规则：来源（攻略）
    short: str = ""  # 理由标签上的短句（推荐卡片）；空时按 engine.advice_text.free_short 从 reason 取
    # 净效果（只用于扣分的自动层规则）：这条扣分说的是同一筹码某条加分理由里的同一个效果（例如「辅助定位」的星币锤 -20
    # 说的就是「经济」+40 里「攻击时花费星币增加攻击力」那一半）。键是那条加分规则的来源（特性名，角色专属规则写「角色」），
    # 值是两条合成一条后理由标签上的短句。那条加分规则是该筹码自动层分值最高的一条（单独成一项）时，推荐卡片上两条合成
    # 一个标签（engine.parts._auto_parts 给两项记同一个 Part.net，ui.text.reason_pills 合并）
    net: dict = field(default_factory=dict)
    # 不对这些角色生效（只用于特性规则：带这条特性、却不适用这条规则的角色，载入时就不收进该角色的自动层）
    exclude: frozenset[str] = frozenset()
    # 只在本局难度为这个难度时生效（State.difficulty 的取值，适配表里写难度名，见 RULE_DIFFICULTY）；None 为不限难度。
    # 写了 RULE_DIFFICULTY 以外的难度名时保留原文，和哪个 State.difficulty 都对不上，这条规则不生效（载入时记警告）
    difficulty: str | None = None
    # 体系来源（rules.toml [sources] 的名字，见 Source）：needs_source 有这种来源时才生效，without_source 没有时才生效。
    # needs_owned 只看已有筹码，表达不了「自己或队友的角色本身就提供」这一类来源（例如角色的技能本身就施加标记），所以另设
    # 这两个条件。两个条件成对写在同一筹码的两条规则上时，任何局面恰好命中一条（有来源时一个分值、没有时另一个）
    needs_source: str | None = None
    without_source: str | None = None

    def applies(self, map_name: str | None, owned: frozenset[str], rnd: int | None,
                difficulty: str | None = None, sources: frozenset[str] = frozenset()) -> bool:
        """difficulty 是本局难度（State.difficulty）；sources 是已有来源的体系名（engine.context.Context.held_sources），
        不给时按一种来源都没有算。"""
        if self.maps and map_name not in self.maps:
            return False
        if self.needs_owned and not (self.needs_owned & owned):
            return False
        if self.max_round is not None and rnd is not None and rnd > self.max_round:
            return False
        if self.difficulty is not None and difficulty != self.difficulty:
            return False
        if self.needs_source is not None and self.needs_source not in sources:
            return False
        if self.without_source is not None and self.without_source in sources:
            return False
        return True


@dataclass(frozen=True, eq=False)
class Source:
    """一种体系的来源（rules.toml [sources] 的一项，例如「充能」）：已持有 chips 里任意一个筹码，或自己的角色、
    任一队友（真人、电脑都算）是 heroes 里的角色时，算有这种来源。适配表规则的 needs_source、without_source 写它的名字
    （Rule.applies），推荐时按 engine.context.Context.held_sources 判断。"""

    name: str
    chips: frozenset[str] = frozenset()
    heroes: frozenset[str] = frozenset()


@dataclass(frozen=True, eq=False)
class HeroSynergy:
    traits: tuple[str, ...]
    schools: tuple[str, ...]
    why: str
    rules: tuple[Rule, ...]  # 角色个别规则 + 所有特性的规则
    # 要尽量保住的推荐流派（适配表 [[hero]] 的 keep_schools）：True 为推荐流派都要保住，流派名的元组为只保这几个
    # （都在 schools 里），False 为不保。选择后会让要保的流派本局再也进不全的筹码扣分（engine.parts._keep_schools_part），
    # 没列出的推荐流派也按其他流派算；要保的流派本局已经进不全时，已进入的其他流派按推荐流派加分
    # （engine.parts._adopted_schools）。只用于默认推荐
    keep_schools: bool | tuple[str, ...] = False
    # 这个角色的扣分（适配表 [[hero]] 的 keep_schools_penalty，负数）；None 时用 rules.toml [synergy] keep_schools
    keep_schools_penalty: float | None = None
    # 这几张筹码不加「推荐流派」这一笔分（适配表 [[hero]] 的 school_skip）：推荐流派里有几张这名角色用不上的筹码时，
    # 用它代替一条条写扣分的规则。筹码照旧算这个流派的筹码（要保的流派扣分、开套预判照旧），只是不拿推荐流派的分。
    # 只用于默认推荐；推荐流派来自玩家思路时不起作用（engine.parts._build_parts）
    school_skip: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class PlayerSynergy:
    note: str
    schools: tuple[str, ...]
    tiers: dict[str, str]  # 筹码名 → core / first / second / avoid
    rules: tuple[Rule, ...]
    sources: tuple[str, ...] = (SOURCE_GUIDE,)  # 这个角色的玩家思路来自哪里
    tier_source: dict[str, str] = field(default_factory=dict)  # 筹码名 → 档位取自哪个来源


@dataclass(frozen=True, eq=False)
class Combo:
    """组合规则：已持有 owned 中任意一个筹码时，候选筹码在 chips 中的加减 score 分。"""

    name: str
    owned: frozenset[str]
    chips: frozenset[str]
    score: float
    reason: str  # 理由；「{owned}」替换为已持有的、触发这条规则的筹码名
    heroes: frozenset[str] = frozenset()  # 只对这些角色生效（空 = 不限）
    traits: frozenset[str] = frozenset()  # 只对带这些特性之一的角色生效（空 = 不限）
    exclude: frozenset[str] = frozenset()  # 不对这些角色生效
    exclude_traits: frozenset[str] = frozenset()  # 不对带这些特性之一的角色生效

    def applies_to(self, hero: str, traits) -> bool:
        if hero in self.exclude:
            return False
        if self.heroes and hero not in self.heroes:
            return False
        if self.traits and not (self.traits & set(traits)):
            return False
        if self.exclude_traits & set(traits):
            return False
        return True


@dataclass(frozen=True, eq=False)
class TeammateRule:
    """队友调分：按符合条件的队友人数，对依赖队友的筹码加减分。没有队友信息时不调分。"""

    chips: frozenset[str]
    who: str  # human 真人队友 / bot 电脑队友 / any 全部队友
    per: float  # 每个符合条件的队友加减的分
    reason: str  # 「{n}」替换为符合条件的队友人数
    traits: frozenset[str] = frozenset()  # 只计角色带这些特性之一的队友（空 = 不限）
    heroes: frozenset[str] = frozenset()  # 只计这些角色的队友（空 = 不限）
    min_count: int = 1  # 符合条件的队友至少几个才生效
    cap: float | None = None  # 调整量的绝对值上限
    self_heroes: frozenset[str] = frozenset()  # 只在自己是这些角色时生效（空 = 不限）
    self_traits: frozenset[str] = frozenset()  # 只在自己的角色带这些特性之一时生效（空 = 不限）
    short: str = ""  # 理由标签上的短句；可写 {mates}、{n}，由 engine.advice_text.fill_mates_short 替换；空时写队友的角色名

    def applies_to(self, hero: str, traits) -> bool:
        """自己的角色是否满足 self_heroes / self_traits。"""
        if self.self_heroes and hero not in self.self_heroes:
            return False
        if self.self_traits and not (self.self_traits & set(traits)):
            return False
        return True


@dataclass(frozen=True, eq=False)
class SeriesPair:
    """同系列的两张筹码（data/series.toml 的一个 [[pair]]）：同一次三选一里同时出现时写对比行，严格上位替代时低一级不参与推荐度。

    gain 只写多出的要点（例如「3 层星光」），不带「比X多」：界面按对方稀有度的颜色词拼成「比X多……」，命令行和诊断包同理。
    """

    low: str  # 低一级（稀有度较低）
    high: str  # 高一级
    # 严格上位替代：high 包含 low 的全部效果，在任何局面都不比 low 差；资料里查得出的几条另由 strict_problems 检查
    strict: bool
    low_gain: str = ""  # low 比 high 多的要点；strict 时为空
    high_gain: str = ""  # high 比 low 多的要点；为空时 high 下面不写对比行
    tip: str = ""  # 悬停说明：两张的效果对照（完整的句子）；可写 {starlight_use}，见 StarlightUse
    # 算或不算严格上位替代的理由（「算：……」「不算：……」），只给维护资料的人看，界面上不显示。
    # 载入时不要求写；检查数据时（tools/check_data.py 的 series_problems）每一对都要写
    reason: str = ""


# 同系列悬停说明（SeriesPair.tip）里的占位符：按当前角色和已有筹码写星光层数的其他用处（engine.Scorer.starlight_use）
STARLIGHT_USE = "{starlight_use}"


@dataclass(frozen=True, eq=False)
class StarlightUse:
    """星光层数的一种其他用处（data/series.toml 的 [[starlight_use]]）：同系列悬停说明的 {starlight_use} 按它写。

    heroes 非空时只对这些角色，owned 非空时只在已持有其中任意一个时；两者都写时都要满足。"""

    text: str
    heroes: frozenset[str] = frozenset()
    owned: frozenset[str] = frozenset()


@dataclass(frozen=True, eq=False)
class NeedGive:
    """队友刚需（[[need]] 的 give 里的一组）：哪些筹码能提供这项需要，以及自己要带的特性（空 = 不限）。"""

    chips: frozenset[str]
    self_traits: frozenset[str] = frozenset()


@dataclass(frozen=True, eq=False)
class TeammateNeed:
    """队友刚需（data/synergy.toml 的 [[need]]）：这名队友是真人时，自己能提供的筹码排到第二位（engine.advice._need_second）。"""

    hero: str
    what: str  # 刚需的是什么（标记、星光）
    why: str  # 为什么是刚需（写机制）
    gives: tuple[NeedGive, ...]

    def can_give(self, chip: str, traits) -> bool:
        """自己（带 traits 这些特性）拿这个筹码能否提供这项需要。"""
        return any(chip in g.chips and (not g.self_traits or g.self_traits & set(traits)) for g in self.gives)


@dataclass(frozen=True, eq=False)
class MapInfo:
    name: str
    # 疯狂难度的进度上限：只知道是困难及以上、不知道具体难度时用它（cap_for）
    progress_cap: int | None
    # 一局可完成的筹码任务数（有路线分支时取各路线的平均），按普通到疯狂里最高的难度取：普通到疯狂的任务数相同，
    # 不知道具体难度时也按它（极限多出的任务另记在 extreme_tasks，只在知道是极限时用）
    chip_tasks: float
    limited: bool = False  # 限时地图
    ended: bool = False  # 资料里写明已结束
    until: str | None = None  # 限时地图的结束日期（YYYY-MM-DD）
    # 各难度的进度上限：难度名（DIFFICULTIES）→ 上限，资料里没有数据的为 None
    progress_caps: dict = field(default_factory=dict)
    # 极限难度（只有几张地图有）的进度上限和筹码任务数（maps.json 的 extreme）；没有极限难度、或资料里没写时为 None
    extreme_cap: int | None = None
    extreme_tasks: float | None = None
    # 各难度一局的筹码任务等级（maps.json 的 task_levels）：难度名（DIFFICULTY_NAMES）→ 各路线的等级列表；一条路线时
    # 直接写一个等级列表。路线不知道时按各条线平均（MapInfo.task_level_shares）；没写的难度按困难那一列
    task_levels: dict = field(default_factory=dict)
    # 游戏繁体中文界面里的地图名（maps.json 的 hant）：认地图时一并认
    hant: str = ""
    # 游戏任务栏里的繁体任务文字（maps.json 的 hant_tasks，原样，不归一化）。资料里和 tasks 一一对应，拼不出来写 null 的
    # 那几条这里已去掉（有去掉的时候就不再按位置和简体任务对应）；载入时按位置配好的对见 DB.map_task_keys
    hant_tasks: tuple[str, ...] = ()
    # 地图编号（maps.json 的 code，1～MAP_CODE_MAX）：筹码方案能标的地图才有，分享方案的短字符串、方案文件用它代表地图。
    # 和筹码编号一样只增不改：改名时编号不变，结束的地图留着编号、不再给别的地图用。没有编号的地图（已经结束的运动场）
    # 不能标在方案上；资料里没写时为 None（旧的资料也照常读）
    code: int | None = None

    def cap_for(self, difficulty: str | None = None, name: str | None = None) -> int | None:
        """推荐用的进度上限。知道具体难度（name：困难、噩梦、疯狂、极限等 DIFFICULTY_NAMES 里的难度名）时取那一难度的
        一列（资料里没有那一列时照下面）；否则普通难度取普通一列（资料里没有时退回 progress_cap），困难及以上和不知道的
        取 progress_cap（疯狂一列）。"""
        if name == EXTREME and self.extreme_cap:
            return self.extreme_cap
        if name in DIFFICULTIES and self.progress_caps.get(name):
            return self.progress_caps[name]
        if difficulty == NORMAL:
            return self.progress_caps.get(DIFFICULTIES[0]) or self.progress_cap
        return self.progress_cap

    def tasks_for(self, name: str | None = None) -> float:
        """推荐用的一局筹码任务数：知道是极限难度（name 为极限）、资料里写了极限的任务数时取它，其余一律取 chip_tasks
        （普通到疯狂的任务数相同；不知道具体难度时不按极限算，以免极限多出的任务让别的难度的估计跟着变）。"""
        if name == EXTREME and self.extreme_tasks is not None:
            return self.extreme_tasks
        return self.chip_tasks

    def task_level_shares(self, name: str | None = None) -> tuple[tuple[int, float], ...] | None:
        """之后任务三选一的等级比例：这个难度的任务等级（task_levels）里 1～3 级各占多少，各条路线平均（路线不知道
        时按各条线平均，和 chip_tasks 的取法相同）。name 是难度名，只知道档位时由调用方换成那一档的一列（噩梦及以上按
        噩梦，engine.context.task_level_column）。难度完全不知道（None）、或这个难度没写时取困难那一列：各难度等级相同的
        地图不受影响；现在的资料里水乡古镇、魔法学院的噩梦及以上和困难那一列不同，难度完全不知道时这两张图按困难估、会
        偏一些。
        整张地图没写等级时为 None，调用方照旧按当前星数估。

        等级写在任务上、与星数无关（mechanics/chips.md），所以一局里先做哪几个任务不影响比例。返回（等级, 占比）的
        元组，按等级从小到大，占比合计为 1。"""
        routes = self.task_levels.get(name) if name is not None else None
        if not routes:
            routes = self.task_levels.get(DIFFICULTIES[1])
        if not routes:
            return None
        if all(_is_int(v) for v in routes):  # 一条路线：直接写的等级列表
            routes = [routes]
        shares: dict[int, float] = {}
        for route in routes:
            for level in route:
                shares[level] = shares.get(level, 0.0) + 1 / len(route) / len(routes)
        return tuple(sorted(shares.items()))

    def difficulties_at(self, cap: int) -> tuple[str, ...] | None:
        """进度上限为 cap 的难度（DIFFICULTIES 里的，按其顺序；没有一个是 cap 时为空元组）。

        四个难度的进度上限有缺的（资料不全）时为 None：缺的那一列也可能是 cap，推算不出。
        极限（只有几张地图有，extreme_cap）：四个难度都不是 cap、只有极限是时为（极限,），例如御魂庆典 17 格。和四个难度里
        的某一个相同时（星趴·梦想号 18、水乡古镇 16 都和疯狂相同）分不出来，照旧只列那几个难度、不列极限：推算成疯狂，
        进度上限一样，推荐上的差别是极限多出的筹码任务（MapInfo.tasks_for）和 rules.toml [extreme] 几个筹码的加分，
        要画面上读到「极限」才算。
        """
        caps = [self.progress_caps.get(d) for d in DIFFICULTIES]
        if any(c is None for c in caps):
            return None
        found = tuple(d for d, c in zip(DIFFICULTIES, caps) if c == cap)
        if not found and self.extreme_cap and self.extreme_cap == cap:
            return (EXTREME,)
        return found

    def active(self, today: date | None = None) -> bool:
        """是否为现役地图。已结束的限时地图只作后备候选。"""
        if self.ended:
            return False
        if self.limited and self.until:
            return (today or clock.today()).isoformat() <= self.until
        return True


@dataclass(frozen=True, eq=False)
class Mutator:
    name: str
    en: str
    effect: str
    scope: str  # 通用、星趴·梦想号、运动场系列等
    hint: str = ""  # 对选择筹码的影响（仅提示，未计入推荐度）
    hant: str = ""  # 游戏繁体中文界面里的组件名（mutators.json 的 hant）


@dataclass(eq=False)
class DB:
    chips: dict[str, Chip]
    heroes: dict[str, Hero]
    removed: tuple[str, ...]
    rules: dict
    traits: dict[str, str]  # 特性名 → 书面化的特性说明（推荐理由引用说明，不引用特性名）
    hero_synergy: dict[str, HeroSynergy]
    player_synergy: dict[str, PlayerSynergy]
    maps: tuple[str, ...]
    map_tasks: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 地图 → 归一化后的简体任务文字（每条任务一个）
    # 地图 → 每条任务在任务栏上的几种写法（归一化后）：和 map_tasks 里这张地图的任务一一对应、先后相同，每一组第一个是
    # 简体的写法（就是 map_tasks 里的那一个），后面接游戏繁体中文界面上的写法（maps.json 的 hant_tasks，按列表里的位置
    # 和 tasks 配对）。繁体写法归一化后和简体相同、写了 null、或资料里没有时，这一组只有简体的一种。任务栏读到的一行
    # 对上某条任务的任一种写法，这条任务算对上一次（hud._guess_map）：对上几条、读到这张地图几成任务都按任务算，
    # 不按写法算，繁体任务栏和简体任务栏读全时得分相同。没有这一项的地图按只有简体的写法算
    map_task_keys: dict[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # 严重错误：推荐无法进行
    warnings: list[str] = field(default_factory=list)  # 资料对不上，已降级处理
    trait_rules: dict[str, tuple[Rule, ...]] = field(default_factory=dict)
    # 特性名 → 理由标签「另符合：……」里用的短说明（[[trait]] 的 short；没写时用 traits 里的说明）
    trait_shorts: dict[str, str] = field(default_factory=dict)
    combos: tuple[Combo, ...] = ()
    teammate_rules: tuple[TeammateRule, ...] = ()
    map_info: dict[str, MapInfo] = field(default_factory=dict)
    mutators: dict[str, Mutator] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)  # data/meta.json：资料同步日期等
    series: tuple[SeriesPair, ...] = ()  # data/series.toml：同系列筹码的上位替代和对比要点（文件可以没有）
    needs: dict[str, TeammateNeed] = field(default_factory=dict)  # 队友刚需：角色名 → 刚需（synergy.toml 的 [[need]]）
    # data/series.toml 的 [[starlight_use]]：同系列悬停说明里「星光层数另有用处」按当前角色和已有筹码写的内容
    starlight_uses: tuple[StarlightUse, ...] = ()
    # rules.toml [sources]：体系名 → 来源（Source；适配表规则的 needs_source、without_source 引用这里的名字）
    sources: dict[str, Source] = field(default_factory=dict)

    # ------------------------------------------------------------ 名称匹配

    def resolve_chip(self, text: str) -> Chip:
        return self.chips[_resolve(text, {_norm(n): c.name for c in self.chips.values()
                                          for n in (c.name, c.hant, *c.aliases) if n}, "筹码")]

    def resolve_hero(self, text: str) -> Hero:
        index = {_norm(a): h.name for h in self.heroes.values()
                 for a in (h.name, *h.aliases, h.hant, h.hant_title, h.hant_full_name) if a}
        return self.heroes[_resolve(text, index, "角色")]

    def resolve_map(self, text: str) -> str:
        def names(m: str) -> tuple[str, ...]:
            info = self.map_info.get(m)
            return (m, info.hant) if info and info.hant else (m,)

        key = _norm(text)
        for m in self.maps:
            if key and any(key in _norm(n) or _norm(n) in key for n in names(m)):
                return m
        return _resolve(text, {_norm(n): m for m in self.maps for n in names(m)}, "地图")

    def match_chip(self, text: str, rarity: str | None = None, *, closest: bool = False) -> tuple[str | None, float, bool]:
        """把识别到的文字配成筹码名。返回（筹码名或 None，相似度，是否判为资料库未收录）。

        候选：相似度与最高值相差不超过 MATCH_BAND 的筹码，以及名称以这段文字开头的同系列筹码（「美工刀」→ 美工刀-初级、
        美工刀-锋利）。候选里先挑稀有度与图标边框颜色相同的，再挑同系列的，最后比相似度。
        - 最高相似度 < 0.5：判为未收录。
        - 给了稀有度（图标边框颜色）、候选里没有一个稀有度相同、且挑出的筹码相似度 < 0.8：判为未收录。
        - 其余按挑出的筹码计算（≥ 0.8 时稀有度不一致由识别模块另行提示）。
        - 没有文字：返回（None, 0.0, False），属于「未识别到文字」，不是未收录。
        已移除的筹码也参与匹配（稀有度视为未知），配上时返回它的名字（不在 chips 里），由调用方决定怎么处理。

        closest=True 时走识别逐段挑名字那一套（chip_select._match_chip）：只取相似度最高、且不低于 MATCH_MIN 的
        那一个，不做同系列和稀有度的取舍；此时第三项一律为 False。识别流程按这个口径逐段挑，其余调用方用默认口径。
        """
        key = _norm(text)
        if not key:
            return None, 0.0, False
        index = {_norm(n): n for n in self.chips}
        index.update({_norm(a): c.name for c in self.chips.values() for a in (c.hant, *c.aliases) if a})
        index.update({_norm(n): n for n in self.removed if _norm(n) not in index})
        if key in index:
            return index[key], 1.0, False
        if closest:
            close = difflib.get_close_matches(key, list(index), n=1, cutoff=MATCH_MIN)
            if not close:
                return None, 0.0, False
            return index[close[0]], difflib.SequenceMatcher(None, key, close[0]).ratio(), False
        ratio = {k: difflib.SequenceMatcher(None, key, k).ratio() for k in index}
        top = max(ratio.values(), default=0.0)
        if top < MATCH_MIN:
            return None, top, True
        cands = [k for k, s in ratio.items() if s >= top - MATCH_BAND or (k.startswith(key) and s >= MATCH_MIN)]

        def fits(k: str) -> int:
            """2 = 稀有度相同，1 = 不知道（没给颜色或已移除的筹码），0 = 不同。"""
            chip = self.chips.get(index[k])
            if not rarity or chip is None:
                return 1
            return 2 if chip.rarity == rarity else 0

        order = list(index)  # 同分时按筹码表的顺序
        best = max(cands, key=lambda k: (fits(k), k.startswith(key), ratio[k], -order.index(k)))
        score = ratio[best]
        if fits(best) == 0 and score < MATCH_SURE:
            return None, score, True
        return index[best], score, False

    # ------------------------------------------------------------ 地图

    def active_maps(self, today: date | None = None) -> tuple[str, ...]:
        """现役地图（限时地图到期或写明已结束的除外），按 maps 的顺序。"""
        if not self.map_info:
            return self.maps
        return tuple(m for m in self.maps if m not in self.map_info or self.map_info[m].active(today))

    def general_rules(self) -> tuple[Rule, ...]:
        """通用规则：对所有角色都成立。缺自动层的角色只用这些。"""
        return self.trait_rules.get(GENERAL_TRAIT, ())


# ------------------------------------------------------------ 本局难度


def level_of_name(name: str | None) -> str | None:
    """难度名（DIFFICULTY_NAMES 之一）所属的档位：普通、困难各一档，噩梦、疯狂、极限为噩梦及以上。不是难度名时为 None。"""
    if name == DIFFICULTY_NAMES[0]:
        return LEVEL_NORMAL
    if name == DIFFICULTY_NAMES[1]:
        return LEVEL_HARD
    if name in _NIGHTMARE_NAMES:
        return LEVEL_NIGHTMARE
    return None


def classify_difficulties(names) -> tuple[str | None, str | None]:
    """把候选难度名归到一个档位，返回（档位，难度名）。只有一个候选时带它的难度名，否则难度名为 None。

    - 只有普通：普通；只有困难：困难；只含噩梦、疯狂、极限：噩梦及以上；
    - 困难和噩梦及以上都有（没有普通）：困难及以上；
    - 普通和其他难度都有、没有候选、含不是难度名的项：（None, None）。
    """
    names = set(names)
    levels = {level_of_name(n) for n in names}
    if not names or None in levels:
        return None, None
    single = next(iter(names)) if len(names) == 1 else None
    if levels == {LEVEL_NORMAL}:
        return LEVEL_NORMAL, single
    if LEVEL_NORMAL in levels:
        return None, None
    if len(levels) == 1:
        return levels.pop(), single
    return LEVEL_HARD_PLUS, None


def _and_join(names) -> str:
    """「困难与噩梦」「普通、困难与噩梦」。"""
    names = list(names)
    if len(names) < 2:
        return "".join(names)
    return f"{'、'.join(names[:-1])}与{names[-1]}"


def infer_difficulty(db: DB, map_name: str | None, cap: int | None, hard: bool = False
                     ) -> tuple[str | None, str | None, str, tuple[str, ...]]:
    """按地图和进度条上读到的进度上限（红叉所在的格）推算本局难度。

    返回（档位 LEVEL_* 或 None，难度名或 None，说明，候选难度名）。hard 为 True 表示三选一的概率行已判定为困难及以上，
    候选里去掉普通。说明不带句末标点，供悬停说明使用：推算出档位时写依据，例如「依据地图「异变图书馆」的进度上限推算：
    进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格」；推算不出或只能推到困难及以上时写原因。

    推算不出（档位为 None）的情形：地图待识别；地图列在 rules.toml [difficulty] cap_untrusted 里（进度上限资料与实测不符，
    推算结果等同于读不到）；地图没有资料，或四个难度的进度上限有缺；尚未读到进度上限；资料里没有这个进度上限；
    普通和其他难度的进度上限相同。四个难度都对不上、只有极限的进度上限对得上时推算成极限（MapInfo.difficulties_at）。
    """
    if not map_name:
        return None, None, "地图待识别", ()
    untrusted = (db.rules.get("difficulty") or {}).get("cap_untrusted", ())
    if map_name in untrusted:
        return None, None, f"地图「{map_name}」的进度上限资料与实测不符", ()
    info = db.map_info.get(map_name)
    if info is None:
        return None, None, f"地图「{map_name}」没有进度上限资料", ()
    missing = [d for d in DIFFICULTIES if info.progress_caps.get(d) is None]
    if missing:
        return None, None, f"地图「{map_name}」缺少{'、'.join(missing)}的进度上限资料", ()
    if not cap:
        return None, None, "尚未读到进度上限", ()
    found = info.difficulties_at(cap) or ()
    cands = tuple(d for d in found if not (hard and d == DIFFICULTIES[0]))
    if not cands:
        scope = "困难及以上各难度" if hard else "各难度"
        return None, None, f"进度条红叉在第 {cap} 格，与地图「{map_name}」{scope}的进度上限资料均不符", ()
    level, name = classify_difficulties(cands)
    if level is None or level == LEVEL_HARD_PLUS:
        return level, None, f"地图「{map_name}」{_and_join(cands)}的进度上限相同", cands
    note = (f"依据地图「{map_name}」的进度上限推算：进度条红叉在第 {cap} 格，"
            f"{'、'.join(cands)}难度{'均' if len(cands) > 1 else ''}为 {cap} 格")
    if len(cands) < len(found):
        note += "；筹码概率为困难及以上，不是普通难度"
    return level, name, note, cands


# 全角、长、短连接号和连字符：认名字和任务文字时都当成「-」（游戏里写「—」的，读成哪一种都认得出）
_DASHES = "－—–‐"


def _dash(text: str) -> str:
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return text


def _norm(text: str) -> str:
    text = re.sub(r"[\s“”\"'‘’「」【】·]", "", text.strip().lower())
    return _dash(text).replace("微章", "徽章")


def task_key(text: str) -> str:
    """把任务文字归一化：去掉括号说明、数量和量词，几种连接号当成同一个（同 _norm），便于和游戏里任务栏的文字比对。

    「击倒 3 只魔法茶壶」和游戏里的「击倒魔法茶壶」都变成「击倒魔法茶壶」；「击倒人工生命体—天崩」和读成
    「击倒人工生命体-天崩」的一样。
    """
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\d+\s*[只个艘次张条层名位颗隻個張條層顆]?", "", text)
    return re.sub(r"[\s\\/:：，,。.*×x]", "", _dash(text))


def _resolve(text: str, index: dict[str, str], kind: str) -> str:
    key = _norm(text)
    if key in index:
        return index[key]
    close = difflib.get_close_matches(key, list(index), n=3, cutoff=0.6)
    if close:
        return index[close[0]]
    raise KeyError(f"未找到{kind}「{text}」")


def _rule(raw: dict, origin: str, source: str = "") -> Rule:
    net = raw.get("net", {})
    difficulty = raw.get("difficulty")
    return Rule(
        chips=frozenset(raw["chips"]),
        score=float(raw["score"]),
        reason=raw.get("reason", ""),
        origin=origin,
        maps=frozenset(raw.get("maps", ())),
        needs_owned=frozenset(raw.get("needs_owned", ())),
        max_round=raw.get("max_round"),
        source=source,
        short=raw.get("short", ""),
        net=dict(net) if isinstance(net, dict) else {},
        exclude=frozenset(raw.get("exclude", ())),
        difficulty=None if difficulty is None else RULE_DIFFICULTY.get(difficulty, str(difficulty)),
        needs_source=raw.get("needs_source"),
        without_source=raw.get("without_source"),
    )


def _check_difficulty(raw: dict, where: str, warn) -> None:
    """规则的 difficulty 只能写 RULE_DIFFICULTY 里的难度名；写错时记警告（这条规则照常载入，但不会生效，见 Rule.difficulty）。"""
    difficulty = raw.get("difficulty")
    if difficulty is not None and difficulty not in RULE_DIFFICULTY:
        warn(f"{where}：difficulty 只能写「{'」「'.join(RULE_DIFFICULTY)}」（现为「{difficulty}」），这条规则不会生效")


def _check_net(rule: Rule, raw: dict, where: str, trait_names, warn) -> Rule:
    """净效果（Rule.net）写得不对时记警告并去掉：只能写在扣分规则上，键是特性名或「角色」，值是非空的文字。"""
    net = raw.get("net")
    if net is None:
        return rule
    bad = (not isinstance(net, dict) or rule.score >= 0
           or any(not isinstance(v, str) or not v for v in net.values())
           or any(k != "角色" and k not in trait_names for k in net))
    if bad:
        warn(f"{where}：net 只能写在扣分规则上，键为特性名或「角色」、值为短句，已忽略这一项")
        return replace(rule, net={})
    return rule


class DataError(ValueError):
    """推荐离不开的资料（chips.json、heroes.json、rules.toml）整份读不出来：消息写明文件名和出错的位置（第几行第几列，
    或最外层的结构不对）。随程序发布的资料有测试把关，只在手改或文件损坏时出现；消息只进错误记录，启动失败的弹窗按 file
    写中文一句（不给玩家看报错原文），不从消息里解析文件名。某一个筹码、角色写得不对时不抛，记警告、跳过那一项。"""

    def __init__(self, message: str, file: str = "") -> None:
        super().__init__(message)
        self.file = file  # 出问题的资料文件名（弹窗里写给用户看），没有时为空


def _is_number(value) -> bool:
    """数（整数或小数；true、false 不算）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value) -> bool:
    return isinstance(value, str)


def _is_name(value) -> bool:
    return isinstance(value, str) and bool(value)


def _is_names(value) -> bool:
    """名称的列表（每一项都是非空的文字）。误写成一个字符串时不算：按字拆开会变成一串不存在的名字。"""
    return isinstance(value, list) and all(_is_name(v) for v in value)


def _is_texts(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _is_levels(value) -> bool:
    """任务等级的列表：1～3 级的整数（对应 data/rules.toml [rarity] 的第 1～3 行）。"""
    return isinstance(value, list) and all(_is_int(v) and 1 <= v <= 3 for v in value)


def _is_level_routes(value) -> bool:
    """maps.json 的任务等级：一条路线写成等级列表，几条路线写成列表的列表；每条路线都要有等级。没写这个难度时
    整个不写，空表不算（空路线算不出比例）。"""
    if not isinstance(value, list) or not value:
        return False
    if all(_is_int(v) for v in value):
        return _is_levels(value)
    return all(_is_levels(v) and v for v in value)


def _is_points(value) -> bool:
    """名称 → 数 的表（职业分值、角色权重）。"""
    return isinstance(value, dict) and all(_is_name(k) and _is_number(v) for k, v in value.items())


# chips.json 每个筹码、heroes.json 每个角色必须有的字段和取值的类型：缺字段或类型不对的那一项记警告、跳过（_field_problem）。
# 游戏繁体中文界面里的名字（hant 等）不在这里：可以不写，见 _optional_text
_CHIP_FIELDS = {
    "name": _is_name, "en": _is_text, "rarity": _is_text, "icon": _is_text, "group": _is_text,
    "school": lambda v: v is None or isinstance(v, str), "maps": _is_names, "granted": lambda v: isinstance(v, bool),
    "effect": _is_text, "base": _is_number, "growth_round": lambda v: v is None or _is_int(v), "tags": _is_names,
    "pools": _is_names, "class_points": _is_points, "fit": _is_names, "fit_minor": _is_names,
}
_HERO_FIELDS = {
    "id": _is_int, "name": _is_name, "en": _is_text, "full_name": _is_text, "aliases": _is_names, "group": _is_text,
    "atk": _is_number, "def": _is_number, "hp": _is_number, "potential": lambda v: isinstance(v, bool),
    "weights": _is_points,
}


def _optional_text(raw: dict, key: str, where: str, warn, ignored: str = "已忽略") -> str:
    """可以不写的一项文字（筹码、角色、地图、畸变组件在游戏繁体中文界面里的名字）。没写（或写 null）时为空串，不记警告：
    缺了只是认不出繁体的写法，由 tools/check_data.py 报给维护资料的人；写了却不是文字时记警告、按没写处理，这一个筹码
    （角色、地图、组件）照常载入。"""
    value = raw.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    warn(f"{where}：{key} 的类型不对，{ignored}")
    return ""


def _field_problem(raw, fields: dict) -> str | None:
    """一个筹码或角色写得不对的地方（fields 见 _CHIP_FIELDS）：不是表、缺字段或类型不对；都没问题时为 None。"""
    if not isinstance(raw, dict):
        return "格式不对"
    for key, ok in fields.items():
        if key not in raw:
            return f"缺少 {key}"
        if not ok(raw[key]):
            return f"{key} 的类型不对"
    return None


def _read_text(path: Path) -> str:
    """推荐离不开的资料文件的全文；读不出来时抛 DataError。只写文件名，不写完整路径（路径里可能有 Windows 账户名）。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise DataError(f"资料文件 {path.name} 不存在", file=path.name) from e
    except UnicodeDecodeError as e:
        raise DataError(f"资料文件 {path.name} 不是 UTF-8 编码（第 {e.start + 1} 个字节）", file=path.name) from e
    except OSError as e:
        raise DataError(f"资料文件 {path.name} 读不出来（{type(e).__name__}）", file=path.name) from e


def _required_json(path: Path, kind: type):
    """chips.json、heroes.json 的内容：格式错误时抛 DataError，写明第几行第几列；最外层不是 kind（表或列表）时同样抛。"""
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as e:
        raise DataError(f"资料文件 {path.name} 格式错误（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}", file=path.name) from e
    if not isinstance(value, kind):
        raise DataError(f"资料文件 {path.name} 的最外层应为{'表' if kind is dict else '列表'}", file=path.name)
    return value


def _read_json(path: Path, default, warn):
    """可以没有的资料文件（maps.json、mutators.json、meta.json）：没有时为 default。读不出来（不是 UTF-8、格式错误）、
    或最外层和 default 不是同一种结构时记警告，当作没有这个文件。"""
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        warn(f"{path.name} 格式错误（第 {e.lineno} 行第 {e.colno} 列），已忽略")
        return default
    except (OSError, UnicodeDecodeError) as e:
        warn(f"{path.name} 读不出来（{type(e).__name__}），已忽略")
        return default
    if not isinstance(value, type(default)):
        warn(f"{path.name} 的最外层应为{'表' if isinstance(default, dict) else '列表'}，已忽略")
        return default
    return value


def _section(syn: dict, key: str, warn) -> list:
    """适配表里写成 [[key]] 段落的一段（各段的条目）：误写成 [key]（一个表）或别的值时记警告，整段忽略。"""
    raw = syn.get(key, [])
    if isinstance(raw, list):
        return raw
    warn(f"适配表：{key} 应写成 [[{key}]] 段落，已忽略")
    return []


def _rule_problem(raw) -> str | None:
    """一条规则（[[trait]]、[[hero]]、[[player]] 下的 [[…rule]]）写得不对的地方；没问题时为 None。
    difficulty、net 写得不对时这条照常载入，另由 _check_difficulty、_check_net 记警告。"""
    if not isinstance(raw, dict):
        return "格式不对"
    if not _is_names(raw.get("chips")) or not raw["chips"]:
        return "chips 应为筹码名的列表"
    if not _is_number(raw.get("score")):
        return "score 应为数"
    for key in ("maps", "needs_owned", "exclude"):
        if key in raw and not _is_names(raw[key]):
            return f"{key} 应为名称的列表"
    for key in ("reason", "short"):
        if key in raw and not isinstance(raw[key], str):
            return f"{key} 应为文字"
    if raw.get("max_round") is not None and not _is_int(raw["max_round"]):
        return "max_round 应为整数"
    for key in ("needs_source", "without_source"):
        if key in raw and not _is_name(raw[key]):
            return f"{key} 应为来源名（rules.toml [sources] 里的名字）"
    if "needs_source" in raw and raw.get("needs_source") == raw.get("without_source"):
        return "needs_source 和 without_source 写了同一个来源，这条规则永远不会生效"
    return None


def _valid_rules(raw, where: str, warn) -> list:
    """一段下面的规则（rule）里写得对的几条（_rule_problem）：写错的一条记警告、跳过；rule 不是数组时整段忽略。"""
    if not isinstance(raw, list):
        warn(f"{where}：rule 应写成规则的数组，已忽略")
        return []
    out = []
    for i, r in enumerate(raw, 1):
        problem = _rule_problem(r)
        if problem:
            warn(f"{where}的第 {i} 条规则：{problem}，已忽略")
            continue
        out.append(r)
    return out


_SERIES_TEXTS = ("low_gain", "high_gain", "tip", "reason")  # series.toml 每一对里的文字字段（载入时都可以不写）


def strict_problems(low: Chip, high: Chip) -> list[str]:
    """两张同系列筹码在资料上能不能算严格上位替代：返回不满足的几条（写进警告用），都满足时为空列表。

    严格上位替代的意思是 high 在任何局面都不比 low 差，所以 low 不参与推荐度的比较。high 的效果是否包含 low 的
    全部效果，资料里查不出来，由 series.toml 的 strict 写明；这里查资料里查得出的三条：
    - 标签不计「通用」（GENERAL_TAG）：low 的职业标签 high 都要有。「通用」不是职业，游戏内置推荐的标签分不给它，
      high 只是多出职业标签时不会更差（例如手持风扇-小「通用」、-大「卡牌」）；
    - 流派相同：流派决定之后同流派筹码的出现率和流派相关的规则；
    - high 没有 low 没有的职业分值：职业分值会让之后最左格偏向那个职业的筹码池，多出来的职业未必是角色要的
      （例如夹心饼干-美味多出攻击）。职业相同、只是分值不同的照算（紫色飞星辅助 50、金色飞星辅助 30）。
    """
    out: list[str] = []
    tags = [t for t in low.tags if t != GENERAL_TAG and t not in high.tags]
    if tags:
        out.append(f"低一级的标签「{'」「'.join(tags)}」高一级没有")
    if low.school != high.school:
        out.append(f"流派不同（{low.school or '无流派'} / {high.school or '无流派'}）")
    points = [c for c, v in high.class_points.items() if v and not low.class_points.get(c)]
    if points:
        out.append(f"高一级多出职业分值「{'」「'.join(points)}」")
    return out


def _load_needs(raw: list, chips: dict[str, Chip], heroes: dict[str, Hero], trait_rules: dict, warn) -> dict[str, TeammateNeed]:
    """读 synergy.toml 的 [[need]]（队友刚需）。写得不对的记警告后忽略：缺字段、角色不存在、
    筹码或特性不存在（这一组忽略）、一组都没有、同一角色写了两次。"""
    out: dict[str, TeammateNeed] = {}
    for i, n in enumerate(raw if isinstance(raw, list) else [], 1):
        where = f"队友刚需第 {i} 条"
        if not isinstance(n, dict) or not all(isinstance(n.get(k), str) and n.get(k) for k in ("hero", "what", "why")):
            warn(f"{where}：hero、what、why 应为文字，已忽略")
            continue
        hero = n["hero"]
        where = f"{where}（{hero}）"
        if hero not in heroes:
            warn(f"{where}：角色不存在，已忽略")
            continue
        if hero in out:
            warn(f"{where}：同一角色写了两次，已忽略后一条")
            continue
        gives = []
        for g in n.get("give", []) if isinstance(n.get("give"), list) else []:
            if not isinstance(g, dict) or not _is_names(g.get("chips")) or not g["chips"]:
                warn(f"{where}：give 的每一组应写 chips，这一组已忽略")
                continue
            if not _is_names(g.get("self_traits", [])):
                warn(f"{where}：self_traits 应为特性名的列表，这一组已忽略")
                continue
            bad = [c for c in g["chips"] if c not in chips]
            bad_traits = [t for t in g.get("self_traits", ()) if t not in trait_rules]
            if bad or bad_traits:
                warn(f"{where}：筹码「{'」「'.join(bad)}」或特性「{'」「'.join(bad_traits)}」不存在，这一组已忽略")
                continue
            gives.append(NeedGive(frozenset(g["chips"]), frozenset(g.get("self_traits", ()))))
        if not gives:
            warn(f"{where}：没有能提供的筹码（give），已忽略")
            continue
        out[hero] = TeammateNeed(hero, n["what"], n["why"], tuple(gives))
    return out


def _load_series(path: Path, chips: dict[str, Chip], warn,
                 heroes=None) -> tuple[tuple[SeriesPair, ...], tuple[StarlightUse, ...]]:
    """读 data/series.toml 的 [[pair]] 和 [[starlight_use]]，返回（同系列的对, 星光层数的其他用处）。文件可以没有。

    写得不对的一对记警告后忽略，不当严重错误：格式错误（整份忽略）、缺 low / high / strict 或类型不对、筹码不存在、
    同一张筹码、稀有度不是由低到高（蓝、紫、金）、写了严格上位替代但资料上不满足（strict_problems：标签不计「通用」、
    流派、职业分值）、严格上位替代却写了 low_gain、两段要点都空、要点以「比」开头（「比X多」由界面拼）、同一对写了两次；
    tip 里写了 {starlight_use} 以外的占位符时记警告（照常读入，占位符原样留着）。reason 可以不写（生成资料时才要求）。
    [[starlight_use]] 缺 text、角色或筹码不存在的一条记警告后忽略。
    """
    if not path.exists():
        return (), ()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        warn(f"同系列筹码表（series.toml）格式错误，已忽略：{e}")
        return (), ()
    except (OSError, UnicodeDecodeError) as e:
        warn(f"同系列筹码表（series.toml）读不出来（{type(e).__name__}），已忽略")
        return (), ()
    uses = _load_starlight_uses(raw.get("starlight_use", []), chips, heroes, warn)
    pairs = raw.get("pair", [])
    if not isinstance(pairs, list):
        warn("同系列筹码表（series.toml）：pair 应写成 [[pair]] 段落，已忽略")
        return (), uses
    order = {r: i for i, r in enumerate(RARITIES)}
    out: list[SeriesPair] = []
    seen: set[frozenset] = set()
    for i, p in enumerate(pairs, 1):
        where = f"同系列筹码表第 {i} 对"
        if not isinstance(p, dict):
            warn(f"{where}：格式不对，已忽略")
            continue
        low, high, strict = p.get("low"), p.get("high"), p.get("strict")
        texts = {k: p.get(k, "") for k in _SERIES_TEXTS}
        if not isinstance(low, str) or not isinstance(high, str) or not isinstance(strict, bool):
            warn(f"{where}：low、high 应为筹码名，strict 应为 true 或 false，已忽略")
            continue
        if not all(isinstance(v, str) for v in texts.values()):
            warn(f"{where}（{low} / {high}）：{'、'.join(_SERIES_TEXTS)} 应为文字，已忽略")
            continue
        where = f"{where}（{low} / {high}）"
        extra = sorted(set(p) - {"low", "high", "strict", *_SERIES_TEXTS})
        if extra:
            warn(f"{where}：不认识的字段 {'、'.join(extra)}，这些字段已忽略")
        missing = [n for n in (low, high) if n not in chips]
        if missing:
            warn(f"{where}：筹码「{'」「'.join(missing)}」不存在，已忽略")
            continue
        a, b = chips[low], chips[high]
        if low == high:
            warn(f"{where}：两张是同一个筹码，已忽略")
            continue
        if order.get(a.rarity, -1) >= order.get(b.rarity, -1):
            warn(f"{where}：稀有度应由低到高（{a.rarity} / {b.rarity}），已忽略")
            continue
        problems = strict_problems(a, b) if strict else []
        if problems:
            warn(f"{where}：不能算严格上位替代（{'；'.join(problems)}），已忽略")
            continue
        if strict and texts["low_gain"]:
            warn(f"{where}：严格上位替代的低一级没有多出的效果，不应写 low_gain，已忽略")
            continue
        if not (texts["low_gain"] or texts["high_gain"]):
            warn(f"{where}：low_gain、high_gain 都是空的，已忽略")
            continue
        if any(texts[k].startswith("比") for k in ("low_gain", "high_gain")):
            warn(f"{where}：要点只写多出的部分，不以「比」开头（「比X多」由界面拼写），已忽略")
            continue
        key = frozenset((low, high))
        if key in seen:
            warn(f"{where}：重复的一对，已忽略")
            continue
        odd = sorted(set(re.findall(r"\{[^}]*\}", texts["tip"])) - {STARLIGHT_USE})
        if odd:
            warn(f"{where}：tip 里只能写 {STARLIGHT_USE}，「{'」「'.join(odd)}」不会被替换")
        seen.add(key)
        out.append(SeriesPair(low=low, high=high, strict=strict, **texts))
    # 严格上位替代要写全：A < B、B < C 都是时，A < C 也要有（三张同时出现时，A 记为被 C 替代，悬停说明取 A / C 这一对）
    strict_pairs = [(p.low, p.high) for p in out if p.strict]
    for a, b in strict_pairs:
        for b2, c in strict_pairs:
            if b2 == b and (a, c) not in strict_pairs:
                if frozenset((a, c)) in seen:  # 写了这一对，却写成不算：互相矛盾
                    warn(f"同系列筹码表：{a} / {b}、{b} / {c} 都是严格上位替代，{a} / {c} 也应写成严格上位替代")
                else:
                    warn(f"同系列筹码表：{a} / {b}、{b} / {c} 都是严格上位替代，缺少 {a} / {c} 这一对")
    return tuple(out), uses


def _load_starlight_uses(raw, chips: dict[str, Chip], heroes, warn) -> tuple[StarlightUse, ...]:
    """读 series.toml 的 [[starlight_use]]（星光层数的其他用处）。写得不对的一条记警告后忽略；误写成单个
    [starlight_use] 表等不是 [[starlight_use]] 段落时整段忽略并记警告（和 [[pair]] 同样处理）。"""
    out: list[StarlightUse] = []
    if not isinstance(raw, list):
        warn("同系列筹码表（series.toml）：starlight_use 应写成 [[starlight_use]] 段落，已忽略")
        return ()
    for i, u in enumerate(raw, 1):
        where = f"同系列筹码表的星光用处第 {i} 条"
        if not isinstance(u, dict) or not isinstance(u.get("text"), str) or not u["text"]:
            warn(f"{where}：text 应为文字，已忽略")
            continue
        names = {k: u.get(k, []) for k in ("heroes", "owned")}
        if not all(isinstance(v, list) and all(isinstance(n, str) for n in v) for v in names.values()):
            warn(f"{where}：heroes、owned 应为名称列表，已忽略")
            continue
        missing = [n for n in names["owned"] if n not in chips]
        missing += [n for n in names["heroes"] if heroes is not None and n not in heroes]
        if missing:
            warn(f"{where}：「{'」「'.join(missing)}」不存在，已忽略")
            continue
        out.append(StarlightUse(u["text"], frozenset(names["heroes"]), frozenset(names["owned"])))
    return tuple(out)


def _names_or_empty(raw: dict, key: str, where: str, warn) -> list:
    """raw[key] 是名称的列表时原样返回（没写时为空列表）；误写成字符串等别的值时记警告，按没写处理。"""
    value = raw.get(key, [])
    if _is_names(value):
        return value
    warn(f"{where}：{key} 应为名称的列表，已忽略")
    return []


def _keep_schools(raw: dict, name: str, schools: tuple[str, ...], warn) -> tuple[bool | tuple[str, ...], float | None]:
    """[[hero]] 的 keep_schools、keep_schools_penalty 整理成 HeroSynergy 的两项，返回（要保住的推荐流派, 扣分）。
    schools 是这个角色写对了的推荐流派。

    keep_schools 写 true（推荐流派都要保住）、false 或不写（不保），或者流派名的列表（只保列出的几个，重复的只算一次）。
    写成别的值时记警告、按 false 处理；列表里不在 schools 里的流派记警告、去掉，一个都不剩时按 false 处理。
    keep_schools_penalty 是这个角色的扣分，要写有限的负数，不写时按所有角色共用的扣分 rules.toml [synergy] keep_schools 计算
    （返回 None）；写成别的值（含 nan、-inf）时记警告、同样按共用的扣分计算。没有要保住的流派时写了也不起作用，记警告。
    警告里不写 rules.toml：检查脚本（tools/check_data.py）按警告里有没有资料文件名补「synergy.toml：」，写了就认成
    rules.toml 的问题。"""
    keep = raw.get("keep_schools", False)
    if isinstance(keep, list) and all(_is_name(s) for s in keep):
        listed = list(dict.fromkeys(keep))
        for s in listed:
            if s not in schools:
                warn(f"角色 {name}：keep_schools 里的流派「{s}」不在 schools（推荐流派）里，已忽略")
        keep = tuple(s for s in listed if s in schools) or False
    elif not isinstance(keep, bool):
        warn(f"角色 {name}：keep_schools 应为 true、false 或流派名的列表，已按 false 处理")
        keep = False
    penalty = raw.get("keep_schools_penalty")
    if penalty is None:
        return keep, None
    if not (_is_number(penalty) and penalty < 0):  # nan 不小于 0，也在这里
        warn(f"角色 {name}：keep_schools_penalty 应为负数，已按所有角色共用的扣分计算")
        return keep, None
    if not math.isfinite(penalty):  # -inf 会让总分、推荐度算不出来
        warn(f"角色 {name}：keep_schools_penalty 应为有限的负数（不能写 -inf），已按所有角色共用的扣分计算")
        return keep, None
    if not keep:
        warn(f"角色 {name}：写了 keep_schools_penalty，没有要保住的推荐流派（keep_schools），这一项不起作用")
    return keep, float(penalty)


def _school_skip(raw: dict, name: str, schools: tuple[str, ...], chips: dict[str, Chip],
                 warn) -> tuple[str, ...]:
    """[[hero]] 的 school_skip（这几张筹码不加「推荐流派」这一笔分）整理成 HeroSynergy.school_skip。
    schools 是这个角色写对了的推荐流派。

    没写时为空。要写成筹码名的列表；写成别的值时记警告、按没写处理。名单里不是筹码名的、不属于任何流派的、
    流派不在 schools 里的记警告、去掉（写了也不起作用，多半是写错了）；重复的只算一次。
    """
    value = raw.get("school_skip")
    if value is None:
        return ()
    if not _is_names(value):
        warn(f"角色 {name}：school_skip 应为筹码名的列表，已忽略")
        return ()
    out: list[str] = []
    for chip_name in dict.fromkeys(value):
        chip = chips.get(chip_name)
        if chip is None:
            warn(f"角色 {name}：school_skip 里的筹码「{chip_name}」不存在，已忽略")
            continue
        if not chip.school:
            warn(f"角色 {name}：school_skip 里的筹码「{chip_name}」不属于任何流派，已忽略")
            continue
        if chip.school not in schools:
            warn(f"角色 {name}：school_skip 里的筹码「{chip_name}」的流派「{chip.school}」不在 schools"
                 f"（推荐流派）里，已忽略")
            continue
        out.append(chip_name)
    return tuple(out)


def _player_entry(p: dict, name: str, warn) -> dict:
    """[[player]] 的一段整理成 _merge_players 直接能用的样子（不改原表）：四档里不是筹码名列表的一档（误写成一个字符串时
    按字拆开会变成一串不存在的筹码）记一条警告、整档忽略；写错的规则跳过（_valid_rules）；推荐流派不是名称的列表时忽略，
    名单外的流派记警告后去掉（同 [[hero]] 的 schools）；note 不是文字时忽略。"""
    entry = dict(p)
    for tier in TIERS:
        if tier in entry and not _is_names(entry[tier]):
            warn(f"玩家思路 {name}「{TIER_LABEL[tier]}」档：应写成筹码名的列表，已忽略这一档")
            del entry[tier]
    entry["rule"] = _valid_rules(p.get("rule", []), f"玩家思路 {name}", warn)
    if "schools" in entry:
        schools = _names_or_empty(entry, "schools", f"玩家思路 {name}", warn)
        for s in schools:
            if s not in SCHOOLS:
                warn(f"玩家思路 {name}：流派「{s}」不存在")
        entry["schools"] = [s for s in schools if s in SCHOOLS]
    if "note" in entry and not isinstance(entry["note"], str):
        warn(f"玩家思路 {name}：note 应为文字，已忽略")
        del entry["note"]
    return entry


def _merge_players(name: str, entries: list[dict], warn) -> PlayerSynergy:
    """同一角色的几段玩家思路按先后合并：同一筹码的档位以后写的一段为准；规则按先后排（写在前面的先匹配）；
    原文（note）依次用「；」连接；推荐流派取第一段写了的。

    来源只有「攻略」一种：没写 source 时按「攻略」处理，写了别的值时记警告，同样按「攻略」处理。"""
    tiers: dict[str, str] = {}
    tier_source: dict[str, str] = {}
    rules: list[Rule] = []
    notes: list[str] = []
    schools: tuple[str, ...] = ()
    for p in entries:
        source = p.get("source", SOURCE_GUIDE)
        if source not in SOURCES:
            warn(f"玩家思路 {name}：来源「{source}」无效（应为「{SOURCE_GUIDE}」），按「{SOURCE_GUIDE}」处理")
            source = SOURCE_GUIDE
        own: dict[str, str] = {}
        for tier in TIERS:
            for chip in p.get(tier, []):
                if chip in own:
                    warn(f"玩家思路 {name}：「{chip}」同时列在「{TIER_LABEL[own[chip]]}」和「{TIER_LABEL[tier]}」两档")
                own[chip] = tier
        rules.extend(_rule(r, "玩家思路", source) for r in p.get("rule", []))
        for chip, tier in own.items():
            tiers[chip] = tier
            tier_source[chip] = source
        if p.get("note"):
            notes.append(p["note"])
        if not schools and p.get("schools"):
            schools = tuple(p["schools"])
    return PlayerSynergy(
        note="；".join(notes),
        schools=schools,
        tiers=tiers,
        rules=tuple(rules),
        sources=SOURCES,
        tier_source=tier_source,
    )


def _load_chips(raw: dict, warn) -> dict[str, Chip]:
    """chips.json 的筹码：缺字段、类型不对的一个记警告、跳过（_CHIP_FIELDS）；稀有度、流派、职业筹码池、职业分值里有名单
    （RARITIES、SCHOOLS、CLASSES）以外的值时记警告、照常载入（游戏加了新的稀有度、流派、职业，这里的名单要一起补上，
    否则按名单分桶、选池的地方会悄悄把它漏掉）。"""
    chips: dict[str, Chip] = {}
    for i, c in enumerate(raw["chips"], 1):
        problem = _field_problem(c, _CHIP_FIELDS)
        if problem:
            label = f"（「{c['name']}」）" if isinstance(c, dict) and _is_name(c.get("name")) else ""
            warn(f"chips.json 第 {i} 个筹码{label}：{problem}，已忽略")
            continue
        aliases = c.get("aliases", [])
        if not _is_names(aliases):
            warn(f"chips.json 筹码「{c['name']}」：aliases 应为名称的列表，已忽略")
            aliases = []
        chips[c["name"]] = Chip(
            name=c["name"],
            en=c["en"],
            rarity=c["rarity"],
            icon=c["icon"],
            group=c["group"],
            school=c["school"],
            maps=tuple(c["maps"]),
            granted=c["granted"],
            effect=c["effect"],
            base=c["base"],
            growth_round=c["growth_round"],
            tags=tuple(c["tags"]),
            pools=tuple(c["pools"]),
            class_points=c["class_points"],
            fit=frozenset(c["fit"]),
            fit_minor=frozenset(c["fit_minor"]),
            hant=_optional_text(c, "hant", f"chips.json 筹码「{c['name']}」", warn),
            aliases=tuple(aliases),
        )
    for c in chips.values():
        odd = [f"稀有度「{c.rarity}」"] if c.rarity not in RARITIES else []
        odd += [f"流派「{c.school}」"] if c.school and c.school not in SCHOOLS else []
        odd += [f"职业筹码池「{p}」" for p in c.pools if p not in CLASSES]
        odd += [f"职业分值「{k}」" for k in c.class_points if k not in CLASSES]
        if odd:
            warn(f"chips.json 筹码「{c.name}」：{'、'.join(odd)}不在名单里")
    return chips


def _load_heroes(raw: list, warn) -> dict[str, Hero]:
    """heroes.json 的角色：缺字段、类型不对的一个记警告、跳过（_HERO_FIELDS）；初始职业分值里有名单（CLASSES）以外的职业时
    记警告、照常载入。"""
    heroes: dict[str, Hero] = {}
    for i, h in enumerate(raw, 1):
        problem = _field_problem(h, _HERO_FIELDS) or (None if isinstance(h.get("title", ""), str) else "title 的类型不对")
        if problem:
            label = f"（「{h['name']}」）" if isinstance(h, dict) and _is_name(h.get("name")) else ""
            warn(f"heroes.json 第 {i} 个角色{label}：{problem}，已忽略")
            continue
        where = f"heroes.json 角色「{h['name']}」"
        heroes[h["name"]] = Hero(
            id=h["id"],
            name=h["name"],
            en=h["en"],
            full_name=h["full_name"],
            aliases=tuple(h["aliases"]),
            group=h["group"],
            atk=h["atk"],
            dfn=h["def"],
            hp=h["hp"],
            potential=h["potential"],
            weights=h["weights"],
            title=h.get("title", ""),
            hant=_optional_text(h, "hant", where, warn),
            hant_title=_optional_text(h, "hant_title", where, warn),
            hant_full_name=_optional_text(h, "hant_full_name", where, warn),
        )
    for h in heroes.values():
        odd = [k for k in h.weights if k not in CLASSES]
        if odd:
            warn(f"heroes.json 角色「{h.name}」：职业分值「{'」「'.join(odd)}」不在名单里")
    return heroes


def _load_maps(raw: dict, warn) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[tuple[str, ...], ...]],
                                          dict[str, MapInfo]]:
    """maps.json：返回（地图 → 归一化后的简体任务文字, 地图 → 每条任务的几种写法, 地图 → MapInfo），前两项见
    DB.map_tasks、DB.map_task_keys。某张地图不是表时记警告、跳过；tasks 不是文字的列表、hant_tasks 不是文字（或 null）的
    列表、hant 和进度上限等某一项的类型不对时记警告，那一项按没有资料处理（没有任务、进度上限不知道、筹码任务数 0）。
    hant、hant_tasks 可以不写（不记警告）。

    任务文字只放简体的写法，和资料里只有简体任务时一模一样。繁体任务按列表里的位置和简体任务配对（在去掉 null 之前配，
    第 i 条繁体就是第 i 条简体的繁体写法），归进那条任务的写法里，任务栏读到的繁体文字也认得出这张地图；写 null 的、
    比简体任务多出来的几条不参加比对（条数对不上由 tools/check_data.py 报）。几条简体任务归一化后相同时（只差数量）
    算一条，它们的繁体写法并进这一条。"""
    map_tasks: dict[str, tuple[str, ...]] = {}
    map_task_keys: dict[str, tuple[tuple[str, ...], ...]] = {}
    map_info: dict[str, MapInfo] = {}
    # 某一项的检查和类型不对时用的值。extreme（极限难度的进度上限和筹码任务数，只有几张地图有）：两项都可以不写，
    # 写了的要是正整数、不小于 0 的数
    fields = {
        "code": (lambda v: v is None or _is_int(v) and 1 <= v <= MAP_CODE_MAX, None),
        "progress_cap": (lambda v: v is None or _is_int(v), None),
        "chip_tasks": (_is_number, 0.0),
        "until": (lambda v: v is None or isinstance(v, str), None),
        "progress_caps": (lambda v: v is None or isinstance(v, dict) and all(x is None or _is_int(x) for x in v.values()),
                          None),
        "extreme": (lambda v: v is None or isinstance(v, dict) and set(v) <= {"progress_cap", "chip_tasks"}
                    and (v.get("progress_cap") is None or _is_int(v["progress_cap"]) and v["progress_cap"] > 0)
                    and (v.get("chip_tasks") is None
                         or _is_number(v["chip_tasks"]) and math.isfinite(v["chip_tasks"]) and v["chip_tasks"] >= 0),
                    None),
        "task_levels": (lambda v: v is None or isinstance(v, dict)
                        and all(d in DIFFICULTY_NAMES and _is_level_routes(x) for d, x in v.items()), None),
    }
    for name, info in raw.items():
        where = f"maps.json 地图「{name}」"
        if not isinstance(info, dict):
            warn(f"{where}：格式不对，已忽略")
            continue
        tasks = info.get("tasks")
        tasks_ok = _is_texts(tasks)
        if not tasks_ok:
            warn(f"{where}：tasks 应为任务文字的列表，按没有任务处理")
            tasks = []
        hant = _optional_text(info, "hant", where, warn, "按没有资料处理")
        hant_tasks = info.get("hant_tasks")
        if hant_tasks is None:  # 没写：只认简体的任务文字，不记警告（由 tools/check_data.py 报）
            hant_tasks = []
        elif not isinstance(hant_tasks, list) or not all(v is None or isinstance(v, str) for v in hant_tasks):
            warn(f"{where}：hant_tasks 应为任务文字的列表（拼不出来的写 null），按没有资料处理")
            hant_tasks = []
        elif not tasks_ok:
            # 简体任务写坏了，繁体任务也不知道怎么对应：一起按没有任务处理
            hant_tasks = []
        # 每条简体任务（归一化后）→ 它的几种写法，简体的在前；繁体按位置配对，null 占着位置、不算一种写法
        spellings: dict[str, list[str]] = {}
        for i, task in enumerate(tasks):
            key = task_key(task)
            if not key:
                continue
            group = spellings.setdefault(key, [key])
            hant_key = task_key(hant_tasks[i]) if i < len(hant_tasks) and hant_tasks[i] is not None else ""
            if hant_key and hant_key not in group:
                group.append(hant_key)
        hant_tasks = [t for t in hant_tasks if t is not None]  # 拼不出来写 null 的那几条占位，配完对就去掉
        values = {}
        for key, (ok, default) in fields.items():
            values[key] = info.get(key, default)
            if not ok(values[key]):
                warn(f"{where}：{key} 的类型不对，按没有资料处理")
                values[key] = default
        map_tasks[name] = tuple(dict.fromkeys(k for k in (task_key(t) for t in tasks) if k))
        map_task_keys[name] = tuple(tuple(spellings[k]) for k in map_tasks[name])
        extreme = values["extreme"] or {}
        map_info[name] = MapInfo(
            name=name,
            progress_cap=values["progress_cap"],
            chip_tasks=float(values["chip_tasks"]),
            limited=bool(info.get("limited", False)),
            ended=bool(info.get("ended", False)),
            until=values["until"],
            progress_caps=dict(values["progress_caps"] or {}),
            extreme_cap=extreme.get("progress_cap"),
            extreme_tasks=None if extreme.get("chip_tasks") is None else float(extreme["chip_tasks"]),
            task_levels=dict(values["task_levels"] or {}),
            hant=hant,
            hant_tasks=tuple(hant_tasks),
            code=values["code"],
        )
    return map_tasks, map_task_keys, map_info


def _load_sources(raw, chips: dict[str, Chip], heroes: dict[str, Hero], warn) -> dict[str, Source]:
    """rules.toml [sources]（体系来源，Source）：每一项是一张表，chips 写筹码名的列表、heroes 写角色名的列表。
    写得不对时记警告：整节或某一项不是表时忽略它；chips、heroes 不是名称的列表时按没写处理；写了这两个以外的键时忽略那个键；
    筹码、角色不存在时照常载入（对不上的名字永远不算来源）；两样都空着的一项照常载入（这种来源一直没有），同样记警告。
    没有这一节时为空：带 needs_source 的规则都不生效，带 without_source 的都生效（引用不到的名字另有警告，见 load_db）。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        warn("rules.toml：[sources] 应为一节（表），已忽略")
        return {}
    out: dict[str, Source] = {}
    for name, item in raw.items():
        where = f"rules.toml [sources] {name}"
        if not isinstance(item, dict):
            warn(f"{where}：应写成一张表（chips、heroes），已忽略")
            continue
        odd = [k for k in item if k not in ("chips", "heroes")]
        if odd:
            warn(f"{where}：只能写 chips、heroes，「{'」「'.join(odd)}」已忽略")
        found = {}
        for key, what, known in (("chips", "筹码", chips), ("heroes", "角色", heroes)):
            names = item.get(key, [])
            if not _is_names(names):
                warn(f"{where} {key}：应为{what}名的列表，按没写处理")
                names = []
            bad = [n for n in names if n not in known]
            if bad:
                warn(f"{where} {key}：{what}「{'」「'.join(bad)}」不存在")
            found[key] = frozenset(names)
        if not found["chips"] and not found["heroes"]:
            warn(f"{where}：chips、heroes 都没写，按一直没有这种来源计算")
        out[name] = Source(name=name, chips=found["chips"], heroes=found["heroes"])
    return out


@lru_cache(maxsize=None)
def load_db(data_path: str | None = None) -> DB:
    """读 data 文件夹（不给时为 paths.data_dir()）里的资料。怎么降级、什么时候抛 DataError 见模块说明。"""
    base = Path(data_path) if data_path else data_dir()
    warnings: list[str] = []

    def warn(text: str) -> None:
        warnings.append(text)
        log.warning(text)

    chips_raw = _required_json(base / "chips.json", dict)
    if not isinstance(chips_raw.get("chips"), list):
        raise DataError("资料文件 chips.json 里没有筹码的列表（chips）", file="chips.json")
    heroes_raw = _required_json(base / "heroes.json", list)
    try:
        rules = tomllib.loads(_read_text(base / "rules.toml"))
    except tomllib.TOMLDecodeError as e:
        raise DataError(f"资料文件 rules.toml 格式错误：{e}", file="rules.toml") from e
    problems: list[str] = []
    syn_path = base / "synergy.toml"
    syn: dict = {}
    if syn_path.exists():
        try:
            syn = tomllib.loads(syn_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            # 格式错误时整份适配表都读不出来：作为严重错误报告（悬浮窗启动时显示），不悄悄忽略
            problems.append(f"格式错误，无法读取：{e}")
        except (OSError, UnicodeDecodeError) as e:
            problems.append(f"无法读取（{type(e).__name__}）")

    chips = _load_chips(chips_raw, warn)
    heroes = _load_heroes(heroes_raw, warn)
    removed = chips_raw.get("removed", [])
    if not _is_names(removed):
        warn("chips.json：removed 应为筹码名的列表，已忽略")
        removed = []

    def check_chips(names, where: str) -> None:
        for n in names:
            if n not in chips:
                warn(f"{where}：筹码「{n}」不存在")

    sources = _load_sources(rules.get("sources"), chips, heroes, warn)

    def check_source(raw: dict, where: str) -> None:
        """规则的 needs_source、without_source 写的名字要在 rules.toml [sources] 里（是不是文字已由 _rule_problem 核对）。"""
        for key, effect in (("needs_source", "这条规则不会生效"), ("without_source", "按一直没有这种来源计算")):
            name = raw.get(key)
            if name is not None and name not in sources:
                warn(f"{where}：{key} 写的来源「{name}」在 rules.toml [sources] 里没有，{effect}")

    traits: dict[str, str] = {}
    trait_shorts: dict[str, str] = {}
    trait_rules: dict[str, tuple[Rule, ...]] = {}
    trait_entries = []
    for i, t in enumerate(_section(syn, "trait", warn), 1):
        if not isinstance(t, dict) or not _is_name(t.get("name")):
            warn(f"适配表的特性第 {i} 条：name 应为文字，已忽略")
            continue
        trait_entries.append(t)
    trait_names = {t["name"] for t in trait_entries}  # 净效果（net）的键可以是写在后面的特性
    for t in trait_entries:
        name = t["name"]
        desc, short = t.get("desc", ""), t.get("short", "")
        if not isinstance(desc, str) or not isinstance(short, str):
            warn(f"特性 {name}：desc、short 应为文字，类型不对的一项已忽略")
            desc, short = (desc if isinstance(desc, str) else ""), (short if isinstance(short, str) else "")
        traits[name] = desc
        if short:
            trait_shorts[name] = short
        raw_rules = _valid_rules(t.get("rule", []), f"特性 {name}", warn)
        trait_rules[name] = tuple(
            _check_net(_rule(r, name), r, f"特性 {name} 的规则（{'、'.join(r['chips'])}）", trait_names, warn)
            for r in raw_rules
        )
        for r in raw_rules:
            _check_difficulty(r, f"特性 {name} 的规则（{'、'.join(r['chips'])}）", warn)
            check_source(r, f"特性 {name} 的规则（{'、'.join(r['chips'])}）")
        for r in trait_rules[name]:
            check_chips(r.chips | r.needs_owned, f"特性 {name}")
            for h in sorted(r.exclude - set(heroes)):
                warn(f"特性 {name} 的规则（{'、'.join(sorted(r.chips))}）：exclude 里的角色「{h}」不存在")
    if GENERAL_TRAIT not in trait_rules:
        warn(f"适配表缺少「{GENERAL_TRAIT}」特性")

    hero_synergy: dict[str, HeroSynergy] = {}
    for i, h in enumerate(_section(syn, "hero", warn), 1):
        if not isinstance(h, dict) or not _is_name(h.get("name")):
            warn(f"角色自动层第 {i} 条：name 应为文字，已忽略")
            continue
        name = h["name"]
        if name not in heroes:
            warn(f"角色自动层：角色「{name}」不存在")
            continue
        raw_rules = _valid_rules(h.get("rule", []), f"角色 {name}", warn)
        own = [_check_net(_rule(r, "角色"), r, f"角色 {name} 的规则（{'、'.join(r['chips'])}）", trait_names, warn)
               for r in raw_rules]
        for r in raw_rules:
            _check_difficulty(r, f"角色 {name} 的规则（{'、'.join(r['chips'])}）", warn)
            check_source(r, f"角色 {name} 的规则（{'、'.join(r['chips'])}）")
        for r in own:
            check_chips(r.chips | r.needs_owned, f"角色 {name}")
        hero_traits = _names_or_empty(h, "traits", f"角色 {name}", warn)
        schools = _names_or_empty(h, "schools", f"角色 {name}", warn)
        collected = list(own)
        for t in [GENERAL_TRAIT, *hero_traits]:
            if t not in trait_rules:
                if t != GENERAL_TRAIT:
                    warn(f"角色 {name}：特性「{t}」不存在")
                continue
            collected.extend(r for r in trait_rules[t] if name not in r.exclude)
        for s in schools:
            if s not in SCHOOLS:
                warn(f"角色 {name}：流派「{s}」不存在")
        known_schools = tuple(s for s in schools if s in SCHOOLS)
        keep, penalty = _keep_schools(h, name, known_schools, warn)
        skip = _school_skip(h, name, known_schools, chips, warn)
        why = h.get("why", "")
        if not isinstance(why, str):
            warn(f"角色 {name}：why 应为文字，已忽略")
            why = ""
        hero_synergy[name] = HeroSynergy(
            traits=tuple(t for t in hero_traits if t in trait_rules),
            schools=tuple(s for s in schools if s in SCHOOLS),
            why=why,
            rules=tuple(collected),
            keep_schools=keep,
            keep_schools_penalty=penalty,
            school_skip=skip,
        )
    for name in heroes:
        if name not in hero_synergy:
            warn(f"角色自动层：缺少角色「{name}」，该角色只按通用规则计算")
        elif not hero_synergy[name].traits:
            warn(f"角色 {name}：未指定任何特性")

    grouped: dict[str, list[dict]] = {}
    for i, p in enumerate(_section(syn, "player", warn), 1):
        if not isinstance(p, dict) or not _is_name(p.get("hero")):
            warn(f"玩家思路层第 {i} 条：hero 应为文字，已忽略")
            continue
        name = p["hero"]
        if name not in heroes:
            warn(f"玩家思路层：角色「{name}」不存在")
            continue
        entry = _player_entry(p, name, warn)
        for tier in TIERS:
            check_chips(entry.get(tier, []), f"玩家思路 {name}「{TIER_LABEL[tier]}」档")
        for r in entry["rule"]:
            check_chips(set(r["chips"]) | set(r.get("needs_owned", ())), f"玩家思路 {name} 的规则")
            _check_difficulty(r, f"玩家思路 {name} 的规则（{'、'.join(r['chips'])}）", warn)
            check_source(r, f"玩家思路 {name} 的规则（{'、'.join(r['chips'])}）")
        grouped.setdefault(name, []).append(entry)
    player_synergy = {name: _merge_players(name, entries, warn) for name, entries in grouped.items()}

    combos: list[Combo] = []
    for i, c in enumerate(_section(syn, "combo", warn), 1):
        if not isinstance(c, dict):
            warn(f"组合规则第 {i} 条：格式不对，已忽略")
            continue
        where = f"组合规则「{c.get('name', '')}」"
        missing = next((k for k in ("owned", "chips", "score") if k not in c), None)
        if missing:
            warn(f"{where}：缺少字段 {missing}，已忽略")
            continue
        bad = [k for k in ("owned", "chips", "heroes", "traits", "exclude", "exclude_traits")
               if k in c and not _is_names(c[k])]
        bad += [k for k in ("name", "reason") if k in c and not isinstance(c[k], str)]
        bad += [] if _is_number(c["score"]) else ["score"]
        if bad:
            warn(f"{where}：{'、'.join(bad)} 的类型不对，已忽略")
            continue
        combo = Combo(
            name=c.get("name", ""),
            owned=frozenset(c["owned"]),
            chips=frozenset(c["chips"]),
            score=float(c["score"]),
            reason=c.get("reason", ""),
            heroes=frozenset(c.get("heroes", ())),
            traits=frozenset(c.get("traits", ())),
            exclude=frozenset(c.get("exclude", ())),
            exclude_traits=frozenset(c.get("exclude_traits", ())),
        )
        check_chips(combo.owned | combo.chips, where)
        for h in combo.heroes | combo.exclude:
            if h not in heroes:
                warn(f"{where}：角色「{h}」不存在")
        for t in combo.traits | combo.exclude_traits:
            if t not in trait_rules:
                warn(f"{where}：特性「{t}」不存在")
        combos.append(combo)

    teammate_rules: list[TeammateRule] = []
    for i, t in enumerate(_section(syn, "teammates", warn), 1):
        where = f"队友调分第 {i} 条"
        if not isinstance(t, dict):
            warn(f"{where}：格式不对，已忽略")
            continue
        missing = next((k for k in ("chips", "per") if k not in t), None)
        if missing:
            warn(f"{where}：缺少字段 {missing}，已忽略")
            continue
        bad = [k for k in ("chips", "traits", "heroes", "self_heroes", "self_traits") if k in t and not _is_names(t[k])]
        bad += [k for k in ("who", "reason", "short") if k in t and not isinstance(t[k], str)]
        bad += [k for k in ("per", "max") if k in t and not _is_number(t[k])]
        bad += ["min_count"] if "min_count" in t and not _is_int(t["min_count"]) else []
        if bad:
            warn(f"{where}：{'、'.join(bad)} 的类型不对，已忽略")
            continue
        rule = TeammateRule(
            chips=frozenset(t["chips"]),
            who=t.get("who", "any"),
            per=float(t["per"]),
            reason=t.get("reason", ""),
            traits=frozenset(t.get("traits", ())),
            heroes=frozenset(t.get("heroes", ())),
            min_count=int(t.get("min_count", 1)),
            cap=float(t["max"]) if "max" in t else None,
            self_heroes=frozenset(t.get("self_heroes", ())),
            self_traits=frozenset(t.get("self_traits", ())),
            short=t.get("short", ""),
        )
        if rule.who not in ("human", "bot", "any"):
            warn(f"{where}：who 应为 human、bot 或 any（现为「{rule.who}」），已忽略")
            continue
        check_chips(rule.chips, where)
        for h in rule.heroes | rule.self_heroes:
            if h not in heroes:
                warn(f"{where}：角色「{h}」不存在")
        for tr in rule.traits | rule.self_traits:
            if tr not in trait_rules:
                warn(f"{where}：特性「{tr}」不存在")
        odd = set(re.findall(r"\{[^}]*\}", rule.short)) - {"{mates}", "{n}"}
        if odd:
            warn(f"{where}：short 里只能写 {{mates}}、{{n}}，「{'」「'.join(sorted(odd))}」不会被替换")
        teammate_rules.append(rule)

    needs = _load_needs(_section(syn, "need", warn), chips, heroes, trait_rules, warn)

    map_tasks, map_task_keys, map_info = _load_maps(_read_json(base / "maps.json", {}, warn), warn)
    # 地图清单取两处的并集：专属筹码出现过的地图（按筹码表的顺序），再接 maps.json 里另有的地图
    # （没有专属筹码的新地图也参加「按候选地图平均」，命令行也认得出）
    maps = tuple(dict.fromkeys([*(m for c in chips.values() for m in c.maps), *map_info]))
    mutators: dict[str, Mutator] = {}
    for name, m in _read_json(base / "mutators.json", {}, warn).items():
        if not isinstance(m, dict) or not all(isinstance(m.get(k, ""), str) for k in ("en", "effect", "scope", "hint")):
            warn(f"mutators.json 组件「{name}」：格式不对，已忽略")
            continue
        mutators[name] = Mutator(name=name, en=m.get("en", ""), effect=m.get("effect", ""), scope=m.get("scope", ""),
                                 hint=m.get("hint", ""),
                                 hant=_optional_text(m, "hant", f"mutators.json 组件「{name}」", warn))
    series, starlight_uses = _load_series(base / "series.toml", chips, warn, heroes)
    _check_rules(rules, {"chips": set(chips), "maps": set(maps),
                         # 适配表读不出来时没有特性和组合规则，不核对这两种（不然每一个都报不存在）
                         "traits": None if problems else set(traits), "combos": None if problems else {c.name for c in combos}},
                 warn)
    return DB(
        map_tasks=map_tasks,
        map_task_keys=map_task_keys,
        chips=chips,
        heroes=heroes,
        removed=tuple(removed),
        rules=rules,
        traits=traits,
        hero_synergy=hero_synergy,
        player_synergy=player_synergy,
        maps=maps,
        problems=problems,
        warnings=warnings,
        trait_rules=trait_rules,
        trait_shorts=trait_shorts,
        combos=tuple(combos),
        teammate_rules=tuple(teammate_rules),
        map_info=map_info,
        mutators=mutators,
        meta=_read_json(base / "meta.json", {}, warn),
        series=series,
        needs=needs,
        starlight_uses=starlight_uses,
        sources=sources,
    )


# rules.toml [pool] leftmost、reroll_leftmost（最左格的出法）的取值；各自怎么出见 advisor/pool.py 的 REROLL_LEFT_*。
# 放在这里，载入时核对取值不用导入推荐那一层（advisor/pool.py 从这里取）
LEFTMOST_MODES = ("rarity", "class_pool", "class_tag", "random")

# rules.toml 里推荐要用到的项（_check_rules 载入时核对）：节 → {键: 取值的种类，见 _rule_value_problem}。
# 缺了这几项，推荐算到那里才出错；载入时先记一条警告，写明缺哪一项
_RULES_REQUIRED = {
    "rarity": {"rows": "rows", "fixed": "rarities"},
    "reroll": {"initial": "count", "per_star": "count", "endless_cycle": "count"},
    "pool": {"max_schools": "posint", "keyword_boost": "pos"},
    "official": {"growth": "num", "fit": "num", "fit_minor": "num", "tag": "num", "keyword": "num", "class_threshold": "num",
                 "keyword_schools": "schools"},
    "synergy": {"core": "num", "first": "num", "second": "num", "avoid": "num", "core_lead": "num", "school": "num",
                "keep_schools": "num", "extra_rule_factor": "num", "cap": "num"},
    "scheme": {"must": "num", "recommend": "num", "optional": "num", "skip": "num", "target": "num", "lock": "num",
               "unmarked_lookahead": "num", "same_tier_near": "num", "income_demote": "num"},
    "lookahead": {"weight": "num", "tier_weight": "num", "picks": "star_numbers", "round_advance": "count",
                  "high_value": "num"},
    "reroll_cost": {"gain": "num"},
    "picks": {"end_round": "above_one", "task_rate": "num", "shop": "num", "default_tasks": "num", "default_cap": "posint",
              "late_rate": "num", "late_from": "num"},
    "endless_cycle": {"chip": "chip", "delay": "delay", "weight": "num"},
    "income": {"reward_every": "posint", "after_max_star": "num", "economy_trait": "trait", "exempt_factor": "num",
               "starlight": "chip_numbers", "coins": "chip_numbers"},
    "softmax": {"temperature": "pos"},
    "advice": {"recommend_total": "num"},
}
# 写了才核对的项（不写时推荐按默认值算，或这一项不起作用）
_RULES_OPTIONAL = {
    "rarity": {"normal": "rows"},
    "pool": {"accumulate_class_points": "bool", "leftmost_class_pool": "bool", "leftmost": "leftmost_mode",
             "reroll_leftmost": "leftmost_mode"},
    "official": {"scale": "num"},
    "endless_cycle": {"star_rate": "num"},
    "income": {"exempt_traits": "traits", "exempt_owned": "chip_texts", "late": "chip_rounds", "late_factor": "num"},
    "advice": {"series_near": "num"},
    "difficulty": {"cap_untrusted": "maps"},
    "map_hints": {"markers": "map_texts"},
}
# 整节可以不写的几节（不写时那一项加分、折算不算）：（写了这一节就要有的键, 写了才核对的键）
_RULES_SECTIONS = {
    "heal_knife": ({"traits": "traits", "knives": "chips", "combos": "combos", "per_layer": "num", "layers": "chip_numbers"},
                   {"anticipation_factor": "num", "healer_traits": "traits", "exclude_traits": "traits"}),
    "survival": ({"trait": "trait", "factor": "num"},
                 {"fighter_traits": "traits", "fighter_chips": "chips", "fighter_factor": "num", "nightmare_chips": "chips",
                  "nightmare_factor": "num", "nightmare_trait_factor": "num", "skip_maps": "map_chips"}),
    "extreme": ({"difficulty": "difficulty", "chips": "chip_numbers", "reasons": "chip_texts"}, {}),
    "school_build": ({"schools": "schools", "factor": "num"}, {}),
}
_DELAY_SOURCES = ("levelup", "shop", "task")  # [endless_cycle] delay 按来源写时的键（State.source 的取值）
# 取值是数（或数的列表、表）的几种：TOML 认得 inf、nan，写进去总分、推荐度会算不出来，同角色的 keep_schools_penalty 一样当成写错
_NUMBER_KINDS = ("num", "pos", "above_one", "rows", "star_numbers", "chip_numbers")


def _non_finite(value) -> bool:
    """value 里有没有 inf、nan：单个数、列表（rows 是列表的列表）、表的值都看。"""
    if _is_number(value):
        return not math.isfinite(value)
    if isinstance(value, list):
        return any(_non_finite(v) for v in value)
    if isinstance(value, dict):
        return any(_non_finite(v) for v in value.values())
    return False


def _rule_value_problem(kind: str, value, known: dict) -> str | None:
    """rules.toml 一项的取值（kind 见 _RULES_REQUIRED）不对时返回说明（「应为大于 0 的数」「筹码「X」不存在」），对时为 None。
    known：资料里有的名字（chips、maps、traits、combos；为 None 的一种不核对）。"""
    if kind in _NUMBER_KINDS and _non_finite(value):
        return "应为有限的数（不能写 inf、nan）"

    def missing(names, what: str, key: str) -> str | None:
        pool = known.get(key)
        bad = [n for n in names if pool is not None and n not in pool]
        return f"{what}「{'」「'.join(bad)}」不存在" if bad else None

    def table(check) -> bool:
        return isinstance(value, dict) and all(_is_name(k) and check(v) for k, v in value.items())

    if kind == "num":
        return None if _is_number(value) else "应为数"
    if kind == "pos":  # 当除数或权重用：为 0 时除零
        return None if _is_number(value) and value > 0 else "应为大于 0 的数"
    if kind == "above_one":  # [picks] end_round：按「(end_round − 当前轮次) ÷ (end_round − 1)」收窄
        return None if _is_number(value) and value > 1 else "应为大于 1 的数"
    if kind == "count":
        return None if _is_int(value) and value >= 0 else "应为不小于 0 的整数"
    if kind == "posint":
        return None if _is_int(value) and value > 0 else "应为正整数"
    if kind == "bool":
        return None if isinstance(value, bool) else "应为 true 或 false"
    if kind == "leftmost_mode":  # 最左格的出法（LEFTMOST_MODES）
        return None if value in LEFTMOST_MODES else f"应为 {'、'.join(LEFTMOST_MODES)} 之一"
    if kind == "rows":  # 行号 = 星数 0～3，每行蓝 / 紫 / 金；一行合计为 0 时按比例换算会除零
        ok = (isinstance(value, list) and len(value) == 4
              and all(isinstance(r, list) and len(r) == 3 and all(_is_number(p) and p >= 0 for p in r) and sum(r) > 0
                      for r in value))
        return None if ok else "应为 4 行、每行蓝 / 紫 / 金 3 个不小于 0 的数，每行合计大于 0"
    if kind == "rarities":
        if not (isinstance(value, list) and len(value) == 4 and all(isinstance(r, str) for r in value)):
            return "应为 4 个稀有度（行号 = 星数 0～3）"
        bad = [r for r in value if r not in RARITIES]
        return f"稀有度「{'」「'.join(bad)}」不存在" if bad else None
    if kind == "star_numbers":
        ok = isinstance(value, list) and len(value) == 4 and all(_is_number(v) and v >= 0 for v in value)
        return None if ok else "应为 4 个不小于 0 的数（行号 = 星数 0～3）"
    if kind == "delay":
        if _is_int(value) and value >= 0:
            return None
        ok = (isinstance(value, dict) and value and all(k in _DELAY_SOURCES for k in value)
              and all(_is_int(v) and v >= 0 for v in value.values()))
        return None if ok else f"应为不小于 0 的整数，或按来源（{'、'.join(_DELAY_SOURCES)}）分别写"
    if kind == "chip":
        return missing([value], "筹码", "chips") if _is_name(value) else "应为筹码名"
    if kind == "chips":
        return missing(value, "筹码", "chips") if _is_names(value) else "应为筹码名的列表"
    if kind in ("chip_numbers", "chip_texts", "chip_rounds"):
        check = {"chip_numbers": _is_number, "chip_texts": _is_name,
                 "chip_rounds": lambda v: _is_int(v) and v > 0}[kind]
        if not table(check):
            return {"chip_numbers": "应为筹码名 → 数的表", "chip_texts": "应为筹码名 → 文字的表",
                    "chip_rounds": "应为筹码名 → 轮次（正整数）的表"}[kind]
        return missing(value, "筹码", "chips")
    if kind == "trait":
        return missing([value], "特性", "traits") if _is_name(value) else "应为特性名"
    if kind == "traits":
        return missing(value, "特性", "traits") if _is_names(value) else "应为特性名的列表"
    if kind == "combos":
        return missing(value, "组合规则", "combos") if _is_names(value) else "应为组合规则名的列表"
    if kind == "schools":
        if not _is_names(value):
            return f"应为流派名（{'、'.join(SCHOOLS)}）的列表"
        bad = [v for v in value if v not in SCHOOLS]  # 点名写错的那几个（同 missing 和 [[hero]] 的 schools）
        return f"流派「{'」「'.join(bad)}」不存在" if bad else None
    if kind == "difficulty":
        return None if value in DIFFICULTY_NAMES else f"应为难度名（{'、'.join(DIFFICULTY_NAMES)}）"
    if kind == "maps":
        return missing(value, "地图", "maps") if _is_names(value) else "应为地图名的列表"
    if kind in ("map_texts", "map_chips"):
        if not table(_is_names):
            return "应为地图名 → 名称列表的表"
        bad = missing(value, "地图", "maps")
        if kind == "map_chips":
            bad = bad or missing([c for chips in value.values() for c in chips], "筹码", "chips")
        return bad
    raise ValueError(f"不认识的取值种类 {kind}")


def _check_rules(rules: dict, known: dict, warn) -> None:
    """rules.toml 载入时核对，对不上只记警告、读法不变：推荐要用到的键都在，取值的类型和范围对（当除数的几项要大于 0），
    引用的筹码名、特性名、组合规则名、地图名、流派名、难度名在资料里都有。游戏改名而这里漏改时对应的加分会悄悄不起作用，
    缺键、除零要到算推荐时才出错：载入时就记下来。known 见 _rule_value_problem。"""
    def check(section: str, items: dict, required: bool) -> None:
        table = rules.get(section)
        for key, kind in items.items():
            if key not in table:
                if required:
                    warn(f"rules.toml [{section}]：缺少 {key}")
                continue
            problem = _rule_value_problem(kind, table[key], known)
            if problem:
                warn(f"rules.toml [{section}] {key}：{problem}")

    sections = {name: (need, _RULES_OPTIONAL.get(name, {})) for name, need in _RULES_REQUIRED.items()}
    # 只有可选项的几节（[difficulty]、[map_hints]）和整节可以不写的几节：写了才核对
    for name, items in _RULES_OPTIONAL.items():
        if name not in sections and name in rules:
            sections[name] = ({}, items)
    for name, items in _RULES_SECTIONS.items():
        if name in rules:
            sections[name] = items
    for name, (need, optional) in sections.items():
        if name not in rules:
            warn(f"rules.toml：缺少 [{name}] 一节")
            continue
        if not isinstance(rules[name], dict):
            warn(f"rules.toml：[{name}] 应为一节（表），已跳过核对")
            continue
        check(name, need, True)
        check(name, optional, False)
    income = rules.get("income")
    if isinstance(income, dict) and income.get("late") and "late_factor" not in income:
        warn("rules.toml [income]：写了 late，缺少 late_factor")
    extreme = rules.get("extreme")
    if isinstance(extreme, dict) and isinstance(extreme.get("chips"), dict) and isinstance(extreme.get("reasons"), dict):
        lack = [c for c in extreme["chips"] if c not in extreme["reasons"]]
        if lack:
            warn(f"rules.toml [extreme] reasons：缺少「{'」「'.join(lack)}」的整句（没有整句时这个筹码不加分）")
