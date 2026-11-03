"""Scorer：一次推荐的上下文（Context）加上对外的方法；advise：给出一次三选一的推荐。

Scorer 的方法都很薄，计算在本包各模块的函数里（parts 分项和总分、lookahead 前瞻、reroll 刷新、advice 推荐的组织、
scheme_rank 按方案排先后），它们接收这个对象、共用它的状态和缓存（Context.memo）。只继承上下文，不用多重继承拼类。
模块之间往上调用（chances 给之后的候选打分）、以及前瞻的说明判断前瞻正负、队友刚需判断流派时，都经这里的方法调用：
测试和工具会在 Scorer 上替换 lookahead、reroll、locks_useless_school，换了以后这几处跟着用替换的。
"""
from __future__ import annotations

from ..data import DB, UNRECORDED, unrecorded_chip
from ..pool import State
from . import advice, lookahead, parts, reroll, scheme_rank
from .advice import Advice, ChipEval, advise_default
from .context import Context
from .lookahead import TierAhead
from .parts import OFFICIAL_LABELS, SAME_MAP, Part
from .reroll import RerollEval, RerollView
from .scheme_rank import Loss, Place, advise_scheme

# advise 收到空的候选时抛出的 ValueError 的说明（调用方都传三个候选，空的说明识别或调用有误：明确拒绝，不让它在
# 取最高分、取排在最前的候选时以「max() arg is an empty sequence」这类说不清原因的错误退出）
EMPTY_OFFER = "候选筹码为空，无法给出推荐"


class Scorer(Context):
    """一次三选一里所有打分共用的上下文（Context：局面、方案、候选和各项缓存）和对外的方法。

    一个 Scorer 只服务一次推荐，换了局面、方案或候选要新建（缓存的前提，见 context.Memo）。"""

    # ------------------------------------------------------------ 分项和总分（parts）

    def score(self, name: str, owned, rnd: int | None, map_name=SAME_MAP) -> float:
        """总分，不含前瞻（parts.score）。map_name 默认用 State.map。"""
        return parts.score(self, name, owned, rnd, map_name)

    def parts(self, name: str, owned, rnd: int | None, map_name=SAME_MAP) -> list[Part]:
        """全部分项（parts.parts）。map_name 默认用 State.map。"""
        return parts.parts(self, name, owned, rnd, map_name)

    def official_parts(self, name: str, owned, rnd: int | None, scale: float | None = None) -> list[Part]:
        """游戏内置推荐分的各项（parts.official_parts）。"""
        return parts.official_parts(self, name, owned, rnd, scale)

    def official_raw(self, name: str, owned, rnd: int | None) -> float:
        """游戏「选择筹码推荐」公式的原值（parts.official_raw）。"""
        return parts.official_raw(self, name, owned, rnd)

    def player_score(self, name: str, owned, rnd: int | None) -> float | None:
        """玩家思路（按方案计算时是方案档位）给这个筹码的分，没列入时为 None（parts.player_score）。"""
        return parts.player_score(self, name, owned, rnd)

    def neutral_score(self, rarity: str | None, owned, rnd: int | None) -> float:
        """资料库未收录的筹码的中性分（parts.neutral_score）。"""
        return parts.neutral_score(self, rarity, owned, rnd)

    def endless_delay(self) -> int:
        """循环往复拿到后再过几回合给额外那次三选一（parts.endless_delay）。"""
        return parts.endless_delay(self)

    def endless_tiers(self, delay: int) -> list[tuple[int, float]]:
        """额外那次三选一的等级和权重（parts.endless_tiers）。"""
        return parts.endless_tiers(self, delay)

    # ------------------------------------------------------------ 前瞻（lookahead）

    def future(self, owned: tuple, zero: str | None = None, map_name=SAME_MAP) -> float:
        """持有这些筹码时下一次三选一里最高分的期望（lookahead.future）。"""
        return lookahead.future(self, owned, zero, map_name)

    def lookahead_times(self) -> float:
        """前瞻影响的选择次数（lookahead.lookahead_times）。"""
        return lookahead.lookahead_times(self)

    def lookahead(self, name: str, owned: tuple) -> float:
        """拿这个筹码对之后三选一的影响（lookahead.lookahead）。"""
        return lookahead.lookahead(self, name, owned)

    def lookahead_reason(self, name: str, owned: tuple) -> str:
        """前瞻的说明（lookahead.lookahead_reason）。"""
        return lookahead.lookahead_reason(self, name, owned)

    def lookahead_short(self, name: str, owned: tuple) -> str:
        """前瞻理由标签上的短句（lookahead.lookahead_short）。"""
        return lookahead.lookahead_short(self, name, owned)

    def tier_lookahead(self, name: str, owned: tuple) -> float:
        """档位前瞻：选了这张以后，方案里标了档位的筹码之后出现的机会怎么变（lookahead.tier_lookahead）。"""
        return lookahead.tier_lookahead(self, name, owned)

    def tier_ahead(self, name: str, owned: tuple) -> TierAhead:
        """档位前瞻和它的说明（lookahead.tier_ahead）。"""
        return lookahead.tier_ahead(self, name, owned)

    # ------------------------------------------------------------ 刷新（reroll）

    def reroll(self, offer: list[str], best_total: float) -> RerollEval | None:
        """「刷新」的估值（reroll.reroll）；没有刷新次数时为 None。"""
        return reroll.reroll(self, offer, best_total)

    def reroll_cost(self) -> float:
        """用掉一次刷新的代价（reroll.reroll_cost）。"""
        return reroll.reroll_cost(self)

    def reroll_chain(self) -> int:
        """刷新的估值按最多连续刷新几次算（reroll.reroll_chain）。"""
        return reroll.reroll_chain(self)

    def reroll_p_better(self, offer, total: float) -> float:
        """刷新后出现总分高于 total 的筹码的概率（reroll.reroll_p_better）。"""
        return reroll.reroll_p_better(self, offer, total)

    def reroll_levels(self, offer) -> list[list[float]]:
        """只刷新一次时新的三个候选里最好一张的级数分布，各候选地图分别算（reroll.reroll_levels）。"""
        return reroll.reroll_levels(self, offer)

    def reroll_view(self, offer, cands: list[ChipEval], places: list[Place], compare: int,
                    value: float) -> tuple[RerollView, list[bool]]:
        """自己的方案里「刷新」那一行（reroll.reroll_view）。"""
        return reroll.reroll_view(self, offer, cands, places, compare, value)

    def reroll_split(self) -> tuple[int, int]:
        """（留下的刷新次数, 多出来的）（reroll.reroll_split）。"""
        return reroll.reroll_split(self)

    # ------------------------------------------------------------ 推荐的组织（advice）和按方案排先后（scheme_rank）

    def starlight_use(self) -> str:
        """同系列悬停说明里 {starlight_use} 的写法（advice.starlight_use）。"""
        return advice.starlight_use(self)

    def locks_useless_school(self, name: str) -> bool:
        """拿这个筹码会把自己锁进对自己没用的流派（advice.locks_useless_school，队友刚需排第二的前提）。"""
        return advice.locks_useless_school(self, name)

    def place(self, e: ChipEval) -> Place:
        """按自己的方案计算时这张候选的位次（scheme_rank.place）。"""
        return scheme_rank.place(self, e)

    def level(self, name: str) -> int:
        """按自己的方案时筹码的级数（scheme_rank.level）。"""
        return scheme_rank.level(self, name)

    def loss(self, e: ChipEval, place: Place, now: tuple[int, int] | None = None) -> Loss | None:
        """自己的方案里选了这张候选之后拿不到的必选、推荐（scheme_rank.loss）。"""
        return scheme_rank.loss(self, e, place, now)


def advise(db: DB, st: State, offer: list[str], rarity=None, scheme=None, cache=None) -> Advice:
    """给出一次三选一的推荐。

    offer：三个候选筹码的标准名；不在筹码表里的名称按「资料库未收录」处理（中性分，不算前瞻）。
    rarity：可选，三个候选筹码识别到的稀有度（图标边框颜色），只用于给未收录的筹码定中性分。
    同系列的高一级严格上位替代某个候选时（data/series.toml），该候选的 superseded_by 为高一级的筹码名、推荐度为 0，
    不会是首推；Advice.top 是其余候选里总分最高的一个（含队友刚需的提升，「刷新」和它比）。
    不算上位替代的同系列两张总分相差不到 rules.toml [advice] series_near 时，稀有度高的一张排在前面（advice.series_near：
    两张的推荐度对调，分值不变），这时 Advice.top 和首推是排到前面的那张。
    scheme：按这套筹码方案计算（Plan，或方案存储里的一套方案 advisor.schemes.Scheme，见 plan_of）；为 None 时是默认推荐，
    不走按方案计算的分支。Advice.scheme 记下用的方案（Plan），卡片上的先后、位次等在 Advice.ranking
    （scheme_rank.advise_scheme）。
    cache：这一帧的分布缓存（Cache，可选）。一帧最多算三次推荐（星数拿不准时按高一星再算一次；按方案计算时为了推荐日志
    里的对照再按默认推荐算一次），把这三次用的同一个 cache 传进来，共用筹码池和分布（context.pool_and_dists）；不给时
    每次现算。cache 只活这一帧，由调用方创建、调用方丢弃，不跨线程共用（见 context.Cache）。
    offer 为空时抛 ValueError（EMPTY_OFFER）：没有候选就没有首推，也没有「刷新」要比的对象。
    """
    if not offer:
        raise ValueError(EMPTY_OFFER)
    scorer = Scorer(db, st, offer=offer, scheme=scheme, cache=cache)
    owned = scorer.known_owned
    rarity = list(rarity or ())
    unrecorded: list[int] = []

    evals = []
    for i, name in enumerate(offer):
        if name not in db.chips:
            r = rarity[i] if i < len(rarity) else None
            neutral = scorer.neutral_score(r, owned, st.round)
            what = f"{r}色筹码" if r else "筹码池里各筹码"
            part = Part("未收录", neutral, f"{UNRECORDED}：按{what}的平均水平计算", "unrecorded", "未收录：按平均水平")
            evals.append(ChipEval(
                chip=unrecorded_chip(name, r), parts=[part], official=0.0, immediate=neutral,
                lookahead=0.0, lookahead_reason="", total=neutral,
            ))
            unrecorded.append(i)
            continue
        # 候选本身按本局的地图算分（State.map；地图待识别时为 None，带地图条件的规则和 [survival] skip_maps 都不按哪一张
        # 地图生效），前瞻和刷新则按每张候选地图分别算分再平均。两者口径不同是有意的：候选的分项要在卡片和悬停
        # 明细上逐条写出理由，按地图平均会得到「几分之几生效」的分项，写不成一句理由，也不该把可能不是本局的地图上才有的
        # 加减分算进眼前这张筹码；之后的三选一本来就从各张地图自己的筹码池里出，按地图分别算才和筹码池对得上
        chip_parts = scorer.parts(name, owned, st.round)
        immediate = sum(p.score for p in chip_parts)
        la = scorer.lookahead(name, owned)
        evals.append(
            ChipEval(
                chip=db.chips[name],
                parts=chip_parts,
                official=sum(p.score for p in chip_parts if p.label in OFFICIAL_LABELS),
                immediate=immediate,
                lookahead=la,
                lookahead_reason=scorer.lookahead_reason(name, owned),
                total=immediate + la,
                official_raw=scorer.official_raw(name, owned, st.round),
                lookahead_short=scorer.lookahead_short(name, owned),
            )
        )
    if scorer.plan is not None:
        return advise_scheme(db, scorer, offer, evals, unrecorded)
    return advise_default(db, scorer, offer, evals, unrecorded)
