"""「刷新」的估值。

默认推荐按分值：刷新后三选一最高分的期望（每个筹码的分值含前瞻，和候选筹码同一个口径）、刷新代价、刷出更好筹码的
概率。刷新次数读到了时，之后每次三选一留 1 次刷新（次数向上取整）、再多留 1 次以防估少（reroll_split，留法见
context.reroll_keep），剩余刷新比这多出来的部分现在用掉不算代价，可以连着刷：有多出来的时按最多连续刷新这么多次、刷到
满意为止递推（reroll_chain），代价为 0；没有多出来的时只算刷新一次、按公式扣代价（reroll_cost）。本次之后预计没有筹码
三选一是留下 0 次的特例。刷新次数没读出来、按默认值补的（可能的最多次数）时不按多出来的算：之后还有三选一就只算刷新
一次、按公式扣代价，之后没有三选一时照样连着刷。地图待识别时每张候选地图分别算后再平均。
自己的方案按档位比（reroll_view：刷新后最好一张的级数分布、刷出排在前面的筹码的概率），档位的级数见 scheme_rank。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..data import DB
from ..pool import expected_best, expected_max_with, prob_above, tie_key
from .context import harmonic, later_picks, pool_and_dists, reroll_keep
from .lookahead import lookahead_on
from .parts import score
from .scheme_rank import TIER_LEVEL, UNMARKED_LEVEL, place_level

if TYPE_CHECKING:
    from .advice import ChipEval
    from .scheme_rank import Place
    from .scorer import Scorer

# 自己的方案里「刷新」和候选比的依据（RerollView.basis）：按档位算平均（候选标了档位或被锁）、按刷出排在它前面的概率（未标记）
BASIS_TIER, BASIS_UNMARKED = "tier", "unmarked"
# RerollView.verdict：预计优于、预计不如、相当（略优 / 略差 / 正好相等）
VERDICT_BETTER, VERDICT_WORSE = "better", "worse"
VERDICT_EVEN_BETTER, VERDICT_EVEN_WORSE, VERDICT_EVEN = "even_better", "even_worse", "even"
# 按档位算平均时，刷新扣除代价后的平均级数和候选的级数相差不到这么多算「相当」（级数之间差 1，代价一般 0.2～0.4 级）
REROLL_LEVEL_EVEN = 0.05
# RerollView.levels 的键：刷新后新的三个候选里最好一张的档位，「avoid」是三张都是不选或被锁
LEVEL_KEYS = {4: "core", 3: "first", 2: "second", 1: "unmarked", 0: "avoid"}


@dataclass
class RerollEval:
    """「刷新」这个选项的估值（reroll）。chain > 1 时 expected 和 p_better 都按最多连续刷新 chain 次计算。"""

    expected: float  # 刷新后三选一最高分的期望；chain > 1 时为最多连续刷新 chain 次、刷到满意为止的期望
    cost: float  # 用掉一次刷新的代价（有多出来的刷新时为 0）
    value: float  # expected - cost
    p_better: float  # 刷新后（chain > 1 时为最多连续刷新 chain 次之内）出现比当前最好选项更好的筹码的概率
    percent: float = 0.0
    # 估值按最多连续刷新几次（reroll_chain）：有多出来的刷新时为多出来的次数，否则为 1
    chain: int = 1
    # 留给之后的三选一的刷新次数和多出来的次数（reroll_split）：之后预计没有三选一时留下 0 次、全部多出来；
    # 没有多出来的时留下的就是剩余刷新次数
    kept: int = 0
    spare: int = 0
    # 之后的三选一按几次留刷新（context.later_picks：预计剩余次数向上取整）；有多出来的刷新时 kept 比它多
    # context.REROLL_MARGIN 次（多留的以防估少）
    need: int = 0
    # 剩余刷新次数是读到的（State.rerolls_known）；为 False 时按默认值补的，不按多出来的算
    known: bool = True


@dataclass
class RerollView:
    """自己的方案里「刷新」那一行要写的比较和概率。

    compare：和哪张候选比（Advice.evals 的下标，即 Advice.top，卡片上排在最前的候选）。basis：BASIS_TIER 按档位算平均
    （那张标了档位或被锁），BASIS_UNMARKED 按刷出排在它前面的概率（那张未标记，含收入降档成未标记的可选）。level：那张
    和刷新比时的级数（TIER_LEVEL，未标记 1，被锁 0；收入降档的按低一档）。expected：刷新后新的三个候选里最好那张的平均
    级数（有多出来的刷新时为最多连续刷新 chain 次的）；cost：刷新代价（级数，有多出来的刷新时为 0）；
    diff = expected − cost − level。verdict：见 VERDICT_*（basis 为 BASIS_UNMARKED 时，p_before 过半、并且按分值算的刷新
    扣掉代价以后也高于那张的总分为 VERDICT_BETTER，否则为 VERDICT_WORSE）。p_up：那张是推荐、可选时刷出更高一档
    （up_tiers 里的档）的概率；p_before：那张未标记时刷出排在它前面的筹码的概率（两者在连续刷新时都按最多 chain 次之内算）。
    levels：只刷新一次时新的三个候选里最好一张的档位比例（LEVEL_KEYS 的键，合计 1）。all_avoid：三张候选（不计被同系列
    上位替代的）都是不选或被锁。first：刷新排第一（首推）。"""

    compare: int
    basis: str
    level: int
    expected: float
    cost: float
    diff: float
    verdict: str
    p_up: float | None
    up_tiers: tuple[str, ...]
    p_before: float | None
    levels: dict[str, float]
    all_avoid: bool
    first: bool
    chain: int = 1


def default_rerolls(db: DB, star: int, owned=()) -> int:
    """一次都没用过时应有的刷新次数：开局的次数加每星的次数，已有循环往复（rules.toml [endless_cycle] chip）时再加
    它给的次数（[reroll] endless_cycle）。筹码名读配置，和给循环往复估值的那一处（parts._build_parts）同一个来源。"""
    r = db.rules["reroll"]
    extra = r["endless_cycle"] if db.rules["endless_cycle"]["chip"] in owned else 0
    return r["initial"] + r["per_star"] * star + extra


# ------------------------------------------------------------ 默认推荐：按分值


def reroll_split(sc: Scorer) -> tuple[int, int]:
    """（留下的刷新次数, 多出来的）：留下的按 context.reroll_keep——刷新次数读到了时为 min(剩余刷新, ⌈之后的三选一次数
    （PicksLeft.total）⌉ + 1)，之后每次三选一留 1 次刷新备用（次数向上取整：剩 0.3 次也留 1 次）、再多留 1 次以防估少；
    多出来的 = 剩余刷新 − 留下的，现在用掉不算代价。之后预计没有三选一时留下 0 次、全部多出来；刷新次数读到了时，之后的
    三选一次数不超过 context.PICKS_EPS（浮点误差）也按没有算（context.picks_needing）。
    刷新次数没读出来（State.rerolls_known 为 False）时不按多出来的算：之后还有三选一时全部留着（只刷一次、按公式算代价），
    之后没有三选一时全部多出来（连着刷），和不分「多出来的」时的算法相同。"""
    rerolls = max(0, sc.st.rerolls)
    kept = reroll_keep(sc, rerolls)
    return kept, rerolls - kept


def reroll_cost(sc: Scorer) -> float:
    """用掉一次刷新的代价 = gain × H(剩余三选一次数 ÷ 剩余刷新次数)。有多出来的刷新（reroll_split）时为 0：用掉的是
    多出来的那一次，留给之后每次三选一的不动。"""
    rc = sc.db.rules["reroll_cost"]
    if sc.st.rerolls <= 0 or sc.picks.total <= 0 or reroll_split(sc)[1] >= 1:
        return 0.0
    return rc["gain"] * harmonic(sc.picks.total / sc.st.rerolls)


def reroll_chain(sc: Scorer) -> int:
    """刷新的估值按最多连续刷新几次算：有多出来的刷新（reroll_split）时为多出来的次数，否则为 1。

    多出来的刷新现在用掉不算代价，可以连着刷到满意为止（最多刷这么多次，留下的照旧留给之后）；
    没有多出来的时照旧只算刷新一次（代价另有 reroll_cost）。之后预计没有三选一时多出来的就是剩余刷新次数。"""
    spare = reroll_split(sc)[1]
    return spare if spare >= 1 else 1


def reroll(sc: Scorer, offer: list[str], best_total: float) -> RerollEval | None:
    """「刷新」的估值：刷新后原来的三个候选不再出现，新的三格按刷新后的分布（slot_dists 的 reroll=True）出，
    每个筹码的分值含前瞻（和候选筹码同一个口径）。地图待识别时，每张候选地图分别计算后再平均。

    - 没有多出来的刷新时只算刷新一次：expected 为刷新后三选一最高分的期望，value = expected − 刷新代价。
    - 有多出来的刷新时（reroll_split），按最多连续刷新 chain 次（多出来的次数，reroll_chain）、刷到满意为止估值
      （最后一次刷新后只能接受刷出的筹码）。最优停止的递推：C_1 = E[本次最高分]，C_k = E[max(本次最高分, C_{k−1})]，
      expected 取 C_chain。每次刷新后的分布视为相同：只排除本次的候选，不排除中途刷出的筹码。刷新代价这时为 0。
      本次之后预计没有筹码三选一时多出来的就是剩余刷新次数。
    - p_better：刷新后出现总分高于 best_total 的筹码的概率；连续刷新时为最多 chain 次之内出现的概率 1 − (1 − p)^chain
      （reroll_p_better；比较对象之后变了时，advise 用它按新的总分重算）。
    """
    if sc.st.rerolls <= 0:
        return None
    chain = reroll_chain(sc)
    dists = reroll_dists(sc, offer)
    if not dists:
        return None
    expected = 0.0
    for left, general, value in dists:
        worth = expected_best(left, general, value)
        for _ in range(chain - 1):
            worth = expected_max_with(left, general, value, worth)
        expected += worth
    expected /= len(dists)
    cost = reroll_cost(sc)
    p_better = reroll_p_better(sc, offer, best_total)
    kept, spare = reroll_split(sc)
    return RerollEval(expected=expected, cost=cost, value=expected - cost, p_better=p_better, chain=chain, kept=kept,
                      spare=spare, need=later_picks(sc), known=bool(getattr(sc.st, "rerolls_known", True)))


def reroll_dists(sc: Scorer, offer) -> list[tuple]:
    """刷新后的三选一在各候选地图上的（最左格的分布, 其余两格的分布, 每个筹码含前瞻的分值）：原来的三个候选不再出现，
    新的三格按刷新后的分布（slot_dists 的 reroll=True）出。筹码池为空的地图不算。按候选缓存（Memo.reroll），
    本模块的几个估值共用。"""
    key = tuple(offer)
    if key not in sc.memo.reroll:
        db, st = sc.db, sc.st
        owned = sc.known_owned
        out = []
        for m in sc.maps:
            pool, left, general = pool_and_dists(
                db, sc.hero, owned, m, st.star, st.source, st.tier,
                probabilities=st.probabilities, left_rarity=st.left_rarity, reroll=True, difficulty=st.difficulty,
                exclude=offer, cache=sc.dist_cache,
            )
            if not pool:
                continue
            value = {
                c.name: score(sc, c.name, owned, st.round, m) + lookahead_on(sc, c.name, owned, m)
                for c in pool
            }
            out.append((left, general, value))
        sc.memo.reroll[key] = out
    return sc.memo.reroll[key]


def reroll_p_better(sc: Scorer, offer, total: float) -> float:
    """刷新后出现总分高于 total 的筹码的概率（RerollEval.p_better）：各候选地图分别算后平均；连续刷新（reroll_chain
    大于 1）时为最多 chain 次之内出现的概率 1 − (1 − p)^chain。分值和 total 按 tie_key 比（pool.prob_above）。"""
    chain = reroll_chain(sc)
    dists = reroll_dists(sc, offer)
    if not dists:
        return 0.0
    p_better = 0.0
    for left, general, value in dists:
        p = prob_above(left, general, value, total)
        p_better += p if chain == 1 else 1 - (1 - p) ** chain
    return min(1.0, max(0.0, p_better / len(dists)))  # 浮点误差可能让它略小于 0


# ------------------------------------------------------------ 自己的方案：刷新按档位比


def _all_prob(left, general, ok) -> float:
    """一次三选一里三格都是 ok(筹码名) 为真的筹码的概率（三格独立，同 pool 的模型；最左格没有特殊分布时三格同分布）。"""
    g = sum(p for n, p in general.items() if ok(n))
    if left is None:
        return g ** 3
    return sum(p for n, p in left.items() if ok(n)) * g * g


def reroll_levels(sc: Scorer, offer) -> list[list[float]]:
    """只刷新一次时，新的三个候选里最好一张的级数（Scorer.level）分布，各候选地图分别算：每张地图一个列表，
    第 L 项是最好一张为 L 级的概率（L = 0～4，合计 1）。"""
    out = []
    for left, general, value in reroll_dists(sc, offer):
        level = {n: sc.level(n) for n in value}
        cdf = [_all_prob(left, general, lambda n, top=top: level.get(n, UNMARKED_LEVEL) <= top)
               for top in range(5)]
        out.append([cdf[0]] + [max(0.0, cdf[k] - cdf[k - 1]) for k in range(1, 5)])
    return out


def chance_before(sc: Scorer, offer, total: float, chain: int) -> float:
    """刷新后出现排在一张未标记候选（总分 total）前面的筹码的概率（RerollView.p_before）：标了必选、推荐、可选的，
    或未标记、总分高于 total 的（按 tie_key 比）。各候选地图分别算后平均；连续刷新时为最多 chain 次之内出现的概率。"""
    dists = reroll_dists(sc, offer)
    if not dists:
        return 0.0
    line = tie_key(total)
    out = 0.0
    for left, general, value in dists:
        def behind(n: str) -> bool:
            lv = sc.level(n)
            return lv <= 0 or lv == UNMARKED_LEVEL and tie_key(value.get(n, 0.0)) <= line
        p = 1 - _all_prob(left, general, behind)
        out += p if chain == 1 else 1 - (1 - p) ** chain
    return min(1.0, max(0.0, out / len(dists)))


def reroll_view(sc: Scorer, offer, cands: list[ChipEval], places: list[Place], compare: int,
                value: float) -> tuple[RerollView, list[bool]]:
    """自己的方案里「刷新」那一行。cands 是卡片上的先后
    （不含被同系列上位替代的），places 是它们和刷新比时用的位次（收入降档的按低一档，见 scheme_rank），compare 是排在
    最前那张在 Advice.evals 里的下标，value 是按分值算的刷新扣掉代价以后的期望（RerollEval.value，按方案的分值）。
    返回（和排在最前那张比的结果, 刷新是否胜过每一张候选）。

    - 按档位算平均（那张标了档位或被锁）：刷新后最好那张的平均级数，减去刷新代价（这个局面里刷新一次平均多拿的级数
      × 调和数（剩余三选一次数 ÷ 剩余刷新次数），和按分值算的刷新代价同一个模型），高于那张的级数才算胜过；
      必选（4 级）不会被胜过（有必选时不首推刷新）；
    - 那张未标记：刷新一次刷出排在它前面的筹码的概率过半，并且 value 也高于那张的总分（都按 tie_key 比）才算胜过：
      刷新次数紧时（代价高），刷出排在它前面的概率过半也不一定值得用掉一次刷新；
    - 有多出来的刷新时（reroll_split：刷新次数读到了时留够之后的三选一、再多留 1 次以外的；没读出来时只有之后没有
      三选一才算多出来）：代价为 0，最多连续刷新 chain 次（多出来的次数）、刷到满意为止（平均级数按最优
      停止递推，概率按 chain 次之内）。地图待识别时递推按每张候选地图分别做、再平均，同默认推荐的 reroll：连续刷新
      刷出来的都是本局那一张地图的筹码池，几张地图的分布混在一起递推，等于假设每刷一次都可能换一张地图；
    - 三张都是不选或被锁、有刷新次数时一律首推刷新（scheme_rank.advise_scheme 里处理，这里给出 all_avoid）。"""
    chain = reroll_chain(sc)
    per_map = reroll_levels(sc, offer)
    n = max(1, len(per_map))
    mix = [sum(m[k] for m in per_map) / n for k in range(5)]
    mean = sum(k * p for k, p in enumerate(mix))
    expected = mean
    if chain > 1 and per_map:
        total = 0.0
        for m in per_map:
            worth = sum(k * p for k, p in enumerate(m))
            for _ in range(chain - 1):
                worth = sum(p * max(k, worth) for k, p in enumerate(m))
            total += worth
        expected = total / n
    cost = 0.0
    if sc.st.rerolls > 0 and sc.picks.total > 0 and reroll_split(sc)[1] < 1:
        gain = sum(p * max(0.0, mean - k) for k, p in enumerate(mix))
        cost = gain * harmonic(sc.picks.total / sc.st.rerolls)
    net = tie_key(expected - cost)
    worth = tie_key(value)

    def beats(e: ChipEval, place: Place) -> bool:
        level = place_level(place)
        if level >= TIER_LEVEL["core"]:
            return False
        if level == UNMARKED_LEVEL:
            return tie_key(chance_before(sc, offer, e.total, chain)) > 0.5 and worth > tie_key(e.total)
        return net > tie_key(level)

    wins = [beats(e, p) for e, p in zip(cands, places)]
    x, place = cands[0], places[0]
    level = place_level(place)
    diff = expected - cost - level
    p_up, up_tiers, p_before = None, (), None
    if level == UNMARKED_LEVEL:
        basis = BASIS_UNMARKED
        p_before = chance_before(sc, offer, x.total, chain)
        # 过半、扣掉代价以后又不如直接选它（刷新次数紧）时也是「预计不如」，卡片上另写原因（ui.text.scheme_reroll_pills）
        verdict = VERDICT_BETTER if tie_key(p_before) > 0.5 and worth > tie_key(x.total) else VERDICT_WORSE
    else:
        basis = BASIS_TIER
        if diff >= REROLL_LEVEL_EVEN:
            verdict = VERDICT_BETTER
        elif diff <= -REROLL_LEVEL_EVEN:
            verdict = VERDICT_WORSE
        elif net > tie_key(level):
            verdict = VERDICT_EVEN_BETTER
        elif net < tie_key(level):
            verdict = VERDICT_EVEN_WORSE
        else:
            verdict = VERDICT_EVEN
        if level in (TIER_LEVEL["first"], TIER_LEVEL["second"]):
            up_tiers = tuple(t for t in ("core", "first") if TIER_LEVEL[t] > level)
            total = 0.0
            for m in per_map:
                p = sum(m[k] for k in range(level + 1, 5))
                total += p if chain == 1 else 1 - (1 - p) ** chain
            p_up = min(1.0, max(0.0, total / n))
    view = RerollView(
        compare=compare, basis=basis, level=level, expected=expected, cost=cost, diff=diff, verdict=verdict, p_up=p_up,
        up_tiers=up_tiers, p_before=p_before, levels={LEVEL_KEYS[k]: mix[k] for k in range(4, -1, -1)},
        all_avoid=all(place_level(p) == 0 for p in places), first=False, chain=chain,
    )
    return view, wins
