"""一次推荐的组织：同系列（上位替代、对比行、总分接近时稀有度高的排前）、必选补分、队友刚需排第二、推荐度和附注，
以及默认推荐的先后（advise_default）。按筹码方案计算时的先后在 scheme_rank（同系列、队友刚需、推荐度、附注用这里的）。

这一步只调整这一次三选一的先后、推荐度和说明，各候选的分值、前瞻和刷新的期望照常由 Scorer 计算。比较总分、推荐度时
一律按 tie_key（同分时按候选的先后、刷新在后），先后和平局规则改了会改变推荐。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..data import DB, NORMAL, RARITIES, STARLIGHT_USE, UNRECORDED, Chip
from ..pool import State, owned_schools, tie_key
from .advice_text import REROLL
from .parts import Part

if TYPE_CHECKING:
    from .context import PicksLeft, Plan
    from .reroll import RerollEval
    from .scheme_rank import Ranking
    from .scorer import Scorer

# 普通难度时推荐区的附注。困难及以上不加
NORMAL_NOTE = "筹码概率按普通难度计算"
# 首推是「刷新」、三个候选筹码的总分又都低于推荐线时推荐区的附注（只在首推是刷新时显示，正好解释为什么
# 推荐刷新；首推是筹码时不加，免得和推荐度很高的筹码读起来矛盾）
LOW_NOTE = "三个候选筹码的适配度均偏低"
# 同系列悬停说明里的 {starlight_use}（data/series.toml，starlight_use）：当前角色和已有筹码有星光层数的其他用处时
# 写出用处，没有时不举例
STARLIGHT_USES, STARLIGHT_NO_USE = "星光层数另有用处（{uses}）", "星光层数另有用处时"
# 同系列两张总分接近、稀有度高的一张排到前面时（series_near，rules.toml [advice] series_near），排到前面那张的对比行
# 「比X多……」的悬停说明后接这一句。{x} 是排到前面的筹码名
SERIES_NEAR_TIP = "两张的推荐度接近，稀有度较高的「{x}」排在前面。"
# 对调后两张的推荐度完全相同、低一级的候选顺序在前时，高一级多记这么多（只决定先后，显示的整数百分数不受影响）
_SERIES_TIE_NUDGE = 1e-8
# 默认推荐的必选补分后接的一句（lift_core）：必选的整句是攻略原文，补分后接 CORE_LEAD
CORE_LEAD = "，优先于本次未列入筹码方案的筹码"
# 理由的整句以这个开头时是助手写的句子（默认推荐的规则、档位），不是攻略原文：补分时直接接 CORE_LEAD
_GENERATED_HEAD = "筹码方案"
NEED_LEAD = 1.0  # 队友刚需：被提的筹码排在原第二名之上多少分（不超过第一、二名差距的一半）
NEED_ORIGIN = "need"  # 队友刚需那一项的 Part.origin（界面按它把这一项排在理由标签的第一个）
# 队友刚需那一项整句的结尾（lift_need）：提到第二位。按自己的方案计算时，提过以后还有「拿不到的往后」和插进来的「刷新」，
# 最后不在卡片上第二位时，scheme_rank 把这一句换成 NEED_PLACE（{n} 是卡片上实际排在第几位，含「刷新」那一行）
NEED_SECOND = "排在第二位，第一位仍按自身收益"
NEED_PLACE = "排在第{n}位"


@dataclass
class ChipEval:
    chip: Chip
    parts: list[Part]  # 全部分项（悬停明细用），总分 = 各分项之和 + 前瞻分
    official: float  # 游戏内置推荐分（已乘 official.scale，计入总分的部分）
    immediate: float  # 不含前瞻的分值
    lookahead: float
    lookahead_reason: str
    total: float
    percent: float = 0.0
    official_raw: float = 0.0  # 游戏内置推荐分的原值（游戏「选择筹码推荐」的公式，≥50 有推荐角标）
    lookahead_short: str = ""  # 前瞻理由标签上的短句，不含分值（Scorer.lookahead_short）
    # 本次三选一里严格上位替代它的同系列筹码名（data/series.toml，几张时取稀有度最高的）；被替代的不参与比较，percent 为 0
    superseded_by: str | None = None
    superseded_tip: str = ""  # 被替代时这两张的效果对照（series.toml 那一对的 tip），悬停说明用
    # 同系列的对比行：（对方筹码名, 比对方多的要点, 悬停说明）。要点不带「比X多」，由界面按对方稀有度的颜色词拼写
    versus: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def unrecorded(self) -> bool:
        return self.chip.unrecorded

    def reasons(self, limit: int = 3) -> list[str]:
        items = sorted((p for p in self.parts if p.reason), key=lambda p: -abs(p.score))
        out = [p.text() for p in items[:limit]]
        if self.lookahead_reason and abs(self.lookahead) >= 3:
            out.append(f"{self.lookahead_reason}（前瞻 {self.lookahead:+.0f}）")
        return out


@dataclass
class Advice:
    state: State
    evals: list[ChipEval]  # 与传入的候选同序
    reroll: RerollEval | None
    best: str  # 筹码名，或 REROLL（「刷新」）
    notes: list[str] = field(default_factory=list)
    picks: PicksLeft | None = None  # 剩余三选一次数的推算（刷新代价、循环往复、收入折算、前瞻影响的次数都用它）
    maps: tuple[str, ...] = ()  # 计算前瞻和刷新期望时用的地图（地图待识别时为候选地图）
    tendency: tuple[str, int] | None = None  # 职业倾向（hero_tendency 的结果）
    unrecorded: list[int] = field(default_factory=list)  # 资料库未收录的候选筹码（从 0 数）
    # 未被同系列上位替代的候选里总分最高的一个（evals 的下标，平局时取靠前的）：「刷新」拿来比较的就是它。
    # 总分含队友刚需的提升：首推是刷新、刚需把某张提到原来最高的候选之上时就是被提的那张（卡片上排在最前的筹码），
    # RerollEval.p_better 也按它的总分算。同系列两张总分接近、稀有度高的一张排到前面时（series_near）
    # 是排到前面的那张，它的总分可能略低于另一张。
    # 界面写「预计优于X」时用它，不要自己取总分最高的（被替代的低一级、接近时排到后面的低一级总分可能更高）。
    # 按自己的方案计算时是卡片上排在最前的候选（Ranking.order 里第一张筹码），不一定总分最高
    top: int | None = None
    # 筹码方案（advise 的 scheme）：按方案计算时是那套方案（Plan），默认推荐时为 None；scheme_schools 是方案的目标流派
    # （由档位推出来，见 schemes.target_schools；默认推荐时为空）
    scheme: Plan | None = None
    scheme_schools: tuple[str, ...] = ()
    # 按方案计算时，同一个局面按默认推荐计算的首推（Session 另算后填上，推荐日志记它，校准用的对照）；默认推荐时为 None
    default_best: str | None = None
    # notes 里要以橙色显示的几条（自己的方案的附注里 warn 的几条，SchemeNote）；默认推荐时为空
    warn_notes: list[str] = field(default_factory=list)
    # 按自己的方案计算时推荐卡片上要显示的东西（位次、先后、刷新那一行、拿不到的提醒、附注）；默认推荐时为 None
    ranking: Ranking | None = None


def starlight_use(sc: Scorer) -> str:
    """同系列悬停说明里 {starlight_use} 的写法（data/series.toml）：按当前角色和已有筹码，取满足条件的
    [[starlight_use]] 各条，写「星光层数另有用处（阿尔每 6 层星光攻击力、防御力各 +1）」；一条都不满足时不举例，
    写「星光层数另有用处时」（例子按当前角色写，没有相关的就不举例）。"""
    owned = set(sc.known_owned)
    uses = [u.text for u in sc.db.starlight_uses
            if (not u.heroes or sc.st.hero in u.heroes) and (not u.owned or u.owned & owned)]
    return STARLIGHT_USES.format(uses="；".join(uses)) if uses else STARLIGHT_NO_USE


def locks_useless_school(sc: Scorer, name: str) -> bool:
    """拿这个筹码会把自己锁进对自己没用的流派（队友刚需排第二的前提，need_pick）：筹码属于还没进入、也不是「有用的」
    流派，拿了以后流派名额（[pool] max_schools）占满，有用的流派里有一个本局再也进不去。有用的流派：默认推荐是推荐流派；
    按筹码方案计算时是列进方案的流派（推荐流派是默认推荐的一部分，不看）。两种计算同一个口径。
    拿了以后还有空位、有用的流派都已进入、或一个有用的流派都没有（没有推荐流派的角色、方案一个流派都没列）时不算：
    为配合队友提到第二位不耽误自己要的流派。"""
    chip = sc.db.chips.get(name)
    if chip is None or not chip.school:
        return False
    if chip.school in owned_schools(sc.db, sc.known_owned):
        return False
    useful = sc.listed if sc.plan is not None else sc.pref_schools
    if chip.school in useful:
        return False
    after = owned_schools(sc.db, (*sc.known_owned, name))
    if len(after) < sc.db.rules["pool"]["max_schools"]:
        return False
    return any(s not in after for s in useful)


# ------------------------------------------------------------ 同系列（data/series.toml）


def mark_series(db: DB, evals: list[ChipEval], starlight_use: str | None = None, keep=None) -> list[ChipEval]:
    """同系列筹码（data/series.toml）同时出现时：标出被严格上位替代的候选、写对比行，返回未被替代的候选（保持原顺序）。

    ① 候选的高一级也在这次三选一里、并且是严格上位替代时，superseded_by 记高一级的筹码名（几张时取稀有度最高的），
       superseded_tip 记这一对的悬停说明；
    ② 对比行只在未被替代的同系列候选之间写，各自写比对方多的要点（要点为空的一方不写）；
    ③ 未被替代的一方和其他候选之间没有对比行时，写它比被它替代的那张（几张时取稀有度最高的）多什么。
    被替代的候选不写对比行；资料库未收录的候选不参加。只改这次三选一的显示和比较，不改分值。
    悬停说明里的 {starlight_use}（data.STARLIGHT_USE）换成 starlight_use（Scorer.starlight_use，按当前角色和已有筹码写；
    不传时为不举例的 STARLIGHT_NO_USE）。
    keep(低一级, 高一级)：严格上位替代的一对这一次算不算替代（自己的方案按档位定，见 scheme_rank.advise_scheme）；不传时都算。
    """
    starlight_use = STARLIGHT_NO_USE if starlight_use is None else starlight_use
    present = {e.chip.name for e in evals if not e.unrecorded}
    pairs = [p for p in db.series if p.low in present and p.high in present]
    if not pairs:
        return list(evals)
    rank = {r: i for i, r in enumerate(RARITIES)}

    def rarity_rank(name: str) -> int:
        return rank.get(db.chips[name].rarity, -1)

    def tip(p) -> str:
        return p.tip.replace(STARLIGHT_USE, starlight_use)

    for e in evals:
        if e.unrecorded:
            continue
        highs = [p for p in pairs if p.strict and p.low == e.chip.name and (keep is None or keep(p.low, p.high))]
        if highs:
            by = max(highs, key=lambda p: rarity_rank(p.high))
            e.superseded_by, e.superseded_tip = by.high, tip(by)
    active = [e for e in evals if not e.superseded_by]
    by_name: dict[str, list[ChipEval]] = {}
    for e in active:
        if not e.unrecorded:
            by_name.setdefault(e.chip.name, []).append(e)
    for p in pairs:
        if p.low not in by_name or p.high not in by_name:
            continue
        for e in by_name[p.low]:
            if p.low_gain:
                e.versus.append((p.high, p.low_gain, tip(p)))
        for e in by_name[p.high]:
            if p.high_gain:
                e.versus.append((p.low, p.high_gain, tip(p)))
    for name, group in by_name.items():
        lows = list(dict.fromkeys(e.chip.name for e in evals if e.superseded_by == name))
        if not lows:
            continue
        low = max(lows, key=rarity_rank)
        pair = next(p for p in pairs if p.low == low and p.high == name)
        for e in group:
            if not e.versus and pair.high_gain:
                e.versus.append((low, pair.high_gain, tip(pair)))
    return active


def series_near(db: DB, evals: list[ChipEval], active: list[ChipEval], reroll: RerollEval | None,
                lifted: ChipEval | None) -> list[tuple[ChipEval, ChipEval]]:
    """同系列两张一起出现、总分相差不到 rules.toml [advice] series_near 时，稀有度高的一张排在前面：
    返回要对调先后的（高一级, 低一级）。只改先后、不改分值：advise 把两张的推荐度对调，其余选项的推荐度和先后不变，
    首推和「刷新」比较的对象跟着排在前面的那张。

    按推荐卡片的顺序（总分从高到低，平局时按候选顺序、刷新在后）看，低一级排在高一级紧前面、两张总分相差不到 series_near
    （含相等）时才对调：
    - 只看都参与推荐度比较的一对（data/series.toml 里不算上位替代的几对；严格上位替代的低一级本来就排在最后）；
    - 两张中间夹着别的选项（含刷新）时不对调：对调会连带改变两张和中间那个选项的先后，和刷新的结论对不上；
    - 其中一张是队友刚需提到第二位的筹码（_need_second）时不对调：那一张写明了「排在第二位」；
    - 对调过的一对里排在后面的那张不再和下一张比（同 scheme_rank 的 _series_near_scheme）：同系列三张连成链、两对都接近
      时只对调前一对。不然中间那张会先后和两边各对调一次，三张的推荐度成了轮换（低、中、高三级原来依次排列，调完变成
      高、低、中），不是只把相邻的一对调过来。"""
    gap = db.rules["advice"].get("series_near", 0)
    if gap <= 0:
        return []
    names = {e.chip.name for e in active if not e.unrecorded}
    pairs = [p for p in db.series if p.low in names and p.high in names]
    if not pairs:
        return []
    rows = [(e.total, i, e) for i, e in enumerate(evals) if any(e is a for a in active)]
    if reroll is not None:
        rows.append((reroll.value, len(evals), reroll))
    rows.sort(key=lambda r: (-tie_key(r[0]), r[1]))  # 推荐卡片的顺序（ui.text.ordered_options）
    rank = {r: i for i, r in enumerate(RARITIES)}
    out = []
    k = 0
    while k < len(rows) - 1:
        (v1, _, first), (v2, _, second) = rows[k], rows[k + 1]
        k += 1
        if first is reroll or second is reroll or first is lifted or second is lifted:
            continue
        pair = next((p for p in pairs if p.low == first.chip.name and p.high == second.chip.name), None)
        if pair is None or rank.get(second.chip.rarity, -1) <= rank.get(first.chip.rarity, -1):
            continue
        if v1 - v2 < gap:
            out.append((second, first))
            k += 1  # 对调过的这一对里排在后面的那张不再和下一张比
    return out


# ------------------------------------------------------------ 必选补分


def _core_first(db: DB, scorer: Scorer, evals: list[ChipEval], owned: tuple, rnd: int | None) -> None:
    """玩家思路列为必选的筹码，总分至少比同一次三选一里玩家没列入的筹码高 core_lead 分，不够就补上。

    只在这次三选一里补，筹码本身的分不变，所以前瞻和刷新的估计不受影响。
    有两个必选时一起补同样的分数，保持它们原来的先后。分值 ≥ 必选档的玩家思路规则也算必选。
    只用于默认推荐：自己的方案里没标的筹码不用默认推荐的任何部分，标了的严格按档位和顺位排（scheme_rank.advise_scheme），
    都不补分。
    """
    syn = db.rules["synergy"]
    player = {
        e.chip.name: None if e.unrecorded else scorer.player_score(e.chip.name, owned, rnd) for e in evals
    }
    core = [e for e in evals if player[e.chip.name] is not None and player[e.chip.name] >= syn["core"]]
    unlisted = [e for e in evals if player[e.chip.name] is None]
    if core and unlisted:
        lift = max(e.total for e in unlisted) + syn["core_lead"] - min(e.total for e in core)
        if lift > 0:
            lift_core(core, lift, CORE_LEAD)


def lift_core(core: list[ChipEval], lift: float, suffix: str) -> None:
    """给必选筹码补分：并进「玩家思路」那一项，理由里只占一行。

    只和同一次三选一里玩家没列入的筹码比，不和「刷新」比（必选在场时也可能首推刷新）。
    整句是助手写的句子时（以 _GENERATED_HEAD「筹码方案」开头：默认推荐的规则「筹码方案：……」、没有原文的档位
    「筹码方案「默认推荐」：必选」）在后面接 suffix（「，优先于……」）；必选档的整句是攻略原文（parts.default_player_match），
    另起一句接在后面（「……。优先于……」；原文本身以句号结尾时不再多加一个）。
    """
    for e in core:
        parts = []
        for p in e.parts:
            if p.label == "玩家思路":
                reason = (f"{p.reason}{suffix}" if p.reason.startswith(_GENERATED_HEAD)
                          else f"{p.reason.rstrip('。')}。{suffix.lstrip('，')}")
                p = Part(p.label, p.score + lift, reason, p.origin, p.short)
            parts.append(p)
        e.parts = parts
        e.immediate += lift
        e.total += lift


# ------------------------------------------------------------ 队友刚需排第二（data/synergy.toml 的 [[need]]）


def mate_needs(db: DB, st: State) -> list:
    """真人队友的刚需（data/synergy.toml 的 [[need]]），同一项只记一次。"""
    needs = []
    for t in st.teammates:
        need = db.needs.get(t.hero) if t.hero and not t.bot else None
        if need is not None and need not in needs:
            needs.append(need)
    return needs


def need_pick(db: DB, scorer: Scorer, needs: list, rest) -> tuple[ChipEval, list] | None:
    """rest（排第三名及以后的候选）里能提到第二位的一张和能提供的刚需：本身的分（总分减去为正的「队友」分项）达到推荐线
    （[advice] recommend_total），不会把自己锁进对自己没用的流派（Scorer.locks_useless_school：拿了以后流派名额占满、
    有用的流派再也进不去）；几张都满足时取本身的分最高的（平局时取靠前的）。没有时为 None。"""
    line = db.rules["advice"]["recommend_total"]
    cands = []
    for e in rest:
        if e is None or e.unrecorded:
            continue
        givers = [n for n in needs if n.can_give(e.chip.name, scorer.traits)]
        if not givers:
            continue
        own = e.total - sum(p.score for p in e.parts if p.label == "队友" and p.score > 0)
        if own < line or scorer.locks_useless_school(e.chip.name):
            continue
        cands.append((own, e, givers))
    if not cands:
        return None
    _, e, givers = max(cands, key=lambda c: tie_key(c[0]))
    return e, givers


def lift_need(e: ChipEval, givers: list, lift: float) -> None:
    """把队友刚需的提升量补进「队友」分项（NEED_ORIGIN）。"""
    names = [g.hero for g in givers]
    whats = "、".join(dict.fromkeys(g.what for g in givers))
    whys = "；".join(g.why for g in givers)
    # 整句直接从队友写起，短句写「配合队友：邦妮刚需标记」（和其他队友理由同一写法）
    reason = "队友" + "".join(f"「{h}」" for h in names) + f"刚需{whats}（{whys}）；该筹码可提供，" + NEED_SECOND
    short = f"配合队友：{'、'.join(names)}刚需{whats}"
    e.parts = [*e.parts, Part("队友", lift, reason, NEED_ORIGIN, short)]
    e.immediate += lift
    e.total += lift


def _need_second(db: DB, scorer: Scorer, active: list[ChipEval], reroll: RerollEval | None) -> ChipEval | None:
    """队友刚需排第二（data/synergy.toml 的 [[need]]）。

    真人队友在刚需名单里时，自己能提供其刚需、排第三名及以后的筹码，满足以下条件时提到第二位（第一位不动）：
    本身的分（总分减去为正的「队友」分项）达到推荐线（[advice] recommend_total）；不会把自己锁进对自己没用的流派
    （Scorer.locks_useless_school：拿了以后流派名额占满、推荐流派再也进不去）。几张都满足时提本身的分最高的一张。
    「刷新」也参加排名；被同系列上位替代的不参加。
    提升量补在「队友」分项里，只补到原第二名之上 NEED_LEAD 分（不超过第一、二名差距的一半）。返回被提的筹码。
    第一名是「刷新」时，原第二名就是总分最高的候选，被提的那张总分会高于它，advise 让「刷新」改和被提的那张比。
    """
    needs = mate_needs(db, scorer.st)
    if not needs:
        return None
    options = [(e.total, e) for e in active] + ([(reroll.value, None)] if reroll else [])
    if len(options) < 3:
        return None
    options.sort(key=lambda o: -tie_key(o[0]))  # 稳定排序：平局时候选在前、刷新在后
    first, second = options[0][0], options[1][0]
    if first - second < 0.02:
        return None  # 第一、二名几乎相同，排不进两者之间
    picked = need_pick(db, scorer, needs, [e for _, e in options[2:]])
    if picked is None:
        return None
    e, givers = picked
    lift_need(e, givers, second + min(NEED_LEAD, (first - second) / 2) - e.total)
    return e


# ------------------------------------------------------------ 推荐度和附注


def softmax(values: list[float], temperature: float) -> list[float]:
    """推荐度：各选项的总分（刷新按扣除代价后的期望）按 rules.toml [softmax] temperature 换算，合计 1。"""
    top = max(values)
    weights = [math.exp((v - top) / temperature) for v in values]
    total = sum(weights)
    return [w / total for w in weights]


def map_note(db: DB, scorer: Scorer) -> str | None:
    """地图待识别时推荐区的说明：这时按候选地图平均计算，写明出来。"""
    if scorer.st.map:
        return None
    note = "地图：待识别，按候选地图平均计算"
    if getattr(scorer.st, "progress_cap", None):
        note += f"（进度上限 {scorer.cap_shown}，读自进度条）"
    elif scorer.st.round is not None:
        note += f"（进度上限取常见值 {scorer.cap_shown}）"
    return note


def mutator_notes(db: DB, st: State) -> list[str]:
    """畸变组件对选择筹码的影响：只提示，未计入推荐度。"""
    out = []
    for name in st.mutators:
        m = db.mutators.get(name)
        if m and m.hint:
            out.append(f"畸变组件「{name}」：{m.hint.rstrip('。；; ')}（仅提示，未计入推荐度）")
    return out


def common_notes(db: DB, scorer: Scorer, offer, unrecorded) -> list[str]:
    """默认推荐和自己的方案共用的推荐附注：角色或候选筹码资料库未收录、地图待识别、普通难度、畸变组件。"""
    st = scorer.st
    notes = []
    if scorer.hero.unrecorded:
        notes.append(f"角色「{st.hero}」{UNRECORDED}：只按游戏内置推荐和通用规则计算")
    elif scorer.hs is None:
        notes.append(f"适配表缺少角色「{st.hero}」：只按游戏内置推荐和通用规则计算")
    for i in unrecorded:
        notes.append(f"第 {i + 1} 个候选筹码「{offer[i]}」{UNRECORDED}：按平均水平计算，不计前瞻")
    m = map_note(db, scorer)
    if m:
        notes.append(m)
    if st.difficulty == NORMAL:
        notes.append(NORMAL_NOTE)
    notes.extend(mutator_notes(db, st))
    return notes


# ------------------------------------------------------------ 默认推荐的先后


def advise_default(db: DB, scorer: Scorer, offer: list[str], evals: list[ChipEval], unrecorded: list[int]) -> Advice:
    """默认推荐（advise 的 scheme 为 None）：同系列、必选补分、首推、刷新、队友刚需、推荐度、同系列接近时的先后和附注。
    evals 是各候选的估值（和 offer 同序，advise 算好），unrecorded 是资料库未收录的候选的下标。"""
    st = scorer.st
    owned = scorer.known_owned
    # 被同系列严格上位替代的候选不参与比较：必选补分、首推、刷新比较的对象、推荐度换算和「适配度均偏低」的附注
    # 都只看其余的候选，被替代的推荐度为 0。各候选的分值、前瞻和刷新的期望不受影响
    active = mark_series(db, evals, scorer.starlight_use())
    _core_first(db, scorer, active, owned, st.round)

    best_eval = max(active, key=lambda e: tie_key(e.total))  # 平局时取靠前的候选
    top = next(i for i, e in enumerate(evals) if e is best_eval)
    reroll = scorer.reroll(offer, best_eval.total)
    # 队友刚需排第二：只改第二位，首推不变（_need_second）
    lifted = _need_second(db, scorer, active, reroll)
    if reroll and lifted is not None and tie_key(lifted.total) > tie_key(best_eval.total):
        # 首推是刷新时，刚需把这张提到原来总分最高的候选之上（首推是筹码时提不到它之上）：刷新改和它比
        # （卡片上排在最前的那张筹码，比较时计入刚需的提升）。结论按它的总分，刷出更好筹码的概率按它的总分重算；
        # Advice.top 跟着改，悬浮窗、诊断包、命令行和推荐日志都按它
        top = next(i for i, e in enumerate(evals) if e is lifted)
        reroll.p_better = scorer.reroll_p_better(offer, lifted.total)
    values = [e.total for e in active] + ([reroll.value] if reroll else [])
    percents = softmax(values, db.rules["softmax"]["temperature"])
    for e in evals:
        e.percent = 0.0
    for e, p in zip(active, percents):
        e.percent = p
    if reroll:
        reroll.percent = percents[-1]

    # 同系列两张总分接近时稀有度高的一张排在前面（series_near）：只对调两张的推荐度，分值不变；
    # 两张在推荐卡片上相邻，其余选项的推荐度和先后不变。排在最前的筹码换成了高一级时，首推和「刷新」比较的对象跟着它
    # （结论和概率都和卡片上排在最前的那张筹码比）
    for high, low in series_near(db, evals, active, reroll, lifted):
        high.percent, low.percent = low.percent, high.percent
        slot = {id(e): i for i, e in enumerate(evals)}
        if tie_key(high.percent) <= tie_key(low.percent) and slot[id(low)] < slot[id(high)]:
            nudge = min(_SERIES_TIE_NUDGE, low.percent / 2)  # 推荐度相同时界面按候选顺序排：让高一级排在前面
            high.percent += nudge
            low.percent -= nudge
        near = SERIES_NEAR_TIP.format(x=high.chip.name)
        high.versus = [(o, g, t + near if o == low.chip.name else t) for o, g, t in high.versus]
        if low is best_eval:
            best_eval = high
            if top == slot[id(low)]:
                top = slot[id(high)]
                if reroll:
                    reroll.p_better = scorer.reroll_p_better(offer, high.total)

    # 首推按刚需提升前的比（提升量不超过第一、二名差距的一半，提升后刷新照样排在被提的那张之前，首推不变）。
    # 同系列对调后 best_eval 是排到前面的高一级：两张中间没有刷新（series_near），和刷新比的结论与按低一级比相同
    best = REROLL if reroll and tie_key(reroll.value) > tie_key(best_eval.total) else best_eval.chip.name
    notes = []
    line = db.rules["advice"]["recommend_total"]
    if best == REROLL and all(e.total < line for e in active):
        # 首推是刷新、总分又都低于推荐线（[advice] recommend_total）时才加；悬浮窗上不写分值
        notes.append(LOW_NOTE)
    notes.extend(common_notes(db, scorer, offer, unrecorded))
    return Advice(
        state=st, evals=evals, reroll=reroll, best=best, notes=notes, picks=scorer.picks,
        maps=tuple(m for m in scorer.maps if m), tendency=scorer.tendency, unrecorded=unrecorded, top=top,
    )
