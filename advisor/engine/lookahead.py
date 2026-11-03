"""前瞻：拿一个筹码对之后三选一的影响（筹码池和出现概率的变化），以及前瞻的说明和理由标签的短句。

前瞻 = weight × 影响的选择次数里各次三选一最高分期望的变化（拿 / 不拿）之和，各筹码的分值一律按现在的已有筹码算
（future_setup），地图待识别时按候选地图平均。刷新的估值（reroll）给刷出的筹码打分时也加上这里的前瞻（lookahead_on）。
影响的那几次按次数分开算（context.split_picks，之后选到某筹码的三个概率 chances.take_chance 同一个口径）：剩下的升星一定
会来，先算满，按下一星的升星（最左格出职业筹码池）；已拿循环往复、它给的额外那次还没给时，那一次也一定会来，接着算，
按任务来源（三格全随机，context.endless_sources）；其余是任务和筹码商店的三选一，按两者在一局里的占比（rules.toml
[picks]：本地图的筹码任务数 × task_rate 对 shop）混合，稀有度按当前星数：任务三格全随机、等级取当前星数（至少 1 级），
筹码商店照升星的规则（最左格出职业筹码池）。所以 0～1 星全按升星，2 星影响 1.5 次时 1 次按升星、半次按混合（循环往复那
一次还没给时按它），3 星以后全按混合（任务按 3 级、筹码商店按 3 星；循环往复那一次还没给时先按它）。一次三选一的三格
出自同一种来源，期望最高分和各筹码的出现率都按占比加权（pool.expected_best_mix、appear_prob_mix）。说明里的「下次
出现率」按下一次三选一的主体写（context.next_sources：0～2 星是升星，3 星以后是循环往复那一次或任务和筹码商店）。
口径：前瞻只衡量筹码池和出现概率的变化，这一张自己拿走以后不会再出现不算损失，它自己的分值不进前瞻。循环往复的分值里
含它额外给的那次三选一，这一张也可能在那次出现，那一层同样按这个口径算（future）。
这个口径有一处不管：治愈来源的分值里有美工刀预判（parts 经 chances.knife_chance；[heal_knife] 只管带物理输出特性的
角色），「之后选到美工刀的概率」按现在的已有筹码算，正在评估的那把美工刀自己也在里面，所以这类角色评估美工刀时，它自己
的总分会经治愈来源的预判带进前瞻（前瞻值最多差约 0.2 分，推荐度通常差在一个百分点以内）。把它排除要让预判和各缓存都
多带一个参数，推荐计算慢一成到两成半，极接近的局面里还会改首推，所以保持现在的算法。
前瞻的说明和短句判断前瞻正负时经 Scorer.lookahead 取值（测试会在 Scorer 上把它换成固定值）。

档位前瞻（tier_lookahead，只在按自己的方案计算时用）：影响的次数、拿 / 不拿两边、候选地图平均、筹码池和分布都和前瞻
同一套，只把之后三选一里每个筹码值多少换成方案的档位（tier_values：必选、推荐、可选、不选取 rules.toml [scheme] 的
must、recommend、optional、skip，没标的取 unmarked_lookahead，被锁成不选的流派里没标的按 skip），衡量「选了这张以后，
方案里标了档位的筹码之后出现的机会怎么变」。scheme_rank 按它给同一档（推荐、可选）的几张排先后。卡片上标了档位的候选
前瞻那一条写选了以后，下一次三选一里至少出现一张方案里的必选、推荐的机会怎么变（tier_ahead）。缓存和前瞻分开
（Memo.tier_future）。
循环往复在档位前瞻里按自己的档位给分（没标的按 unmarked_lookahead），不做 future 里按假设局面重算那一层：档位分不随
已有筹码变，拿和不拿两边它的分值相同，正在评估的候选自己的价值不会经它带进来；它额外给的那次三选一不算进档位前瞻
（和前瞻同一口径：只衡量筹码池和出现率的变化）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..data import CLASSES, SCHOOLS
from ..pool import appear_any_mix, appear_prob_mix, expected_best_mix, owned_schools
from .advice_text import SCHEME_TIER_WORD, SHORT_LIMIT
from .context import TOP_STAR, endless_sources, mixed_sources, next_sources, pool_and_dists, split_picks
from .parts import SAME_MAP, SCHEME_TIER_KEY, endless_value, pool_scores

if TYPE_CHECKING:
    from .scorer import Scorer

# 前瞻：职业分值累加（rules.toml [pool] accumulate_class_points）。{points} 写「辅助职业分值 +50」（几个职业用「、」连），
# {classes} 写职业名（「辅助」「攻击、生存」）；短句前面另加「前瞻：」，短句是整句的一部分
CLASS_SHIFT_REASON = "选择后{points}，之后升星和筹码商店的三选一更常出现{classes}职业的筹码"
CLASS_SHIFT_SHORT = "更常出现{classes}职业的筹码"
# 3 星以后不再升星，职业分值只影响筹码商店的最左格（任务三格全随机）。短句放不下时（两个职业）照上面的短句写
CLASS_SHIFT_SHOP_REASON = "选择后{points}，之后筹码商店的三选一更常出现{classes}职业的筹码"
CLASS_SHIFT_SHOP_SHORT = "筹码商店更常出现{classes}职业的筹码"
# 档位前瞻的说明（按自己的方案计算时，标了档位的候选前瞻那一条）：{words} 说的是哪几档（「必选、推荐」；方案里没有还没
# 持有的必选、推荐时是「可选」），{before}、{after} 是下一次三选一里这几档还没持有、不在本次候选里的筹码至少出现一张的
# 机会（不拿 / 拿了以后，按下一次三选一的主体，同前瞻的说明；_avg_any）。一次三选一只能选一张，下一次能不能见到至少一张
# 想要的最要紧，见到两张和见到一张差别不大，所以不把各张的出现率相加（几张相加会超过 100%，方案里标得多时也会虚高）。
# 整句后接变化最大的一两张各自的出现率（TIER_AHEAD_ITEM，用「、」连，外加括号；各张取整后都没变时不接括号）；说的只有
# 一张、它的出现率取整后变了时括号里只写它的名字（TIER_AHEAD_ONE）
TIER_AHEAD_REASON = "选择后，筹码方案里{words}的筹码在下一次三选一里至少出现一张的机会 {before} → {after}"
TIER_AHEAD_ITEM = "{chip} {before} → {after}"
# 说的那几档只有一张时：至少出现一张就是它自己的出现率，括号里只写名字，不把整句的数再写一遍
TIER_AHEAD_ONE = "（{chip}）"
TIER_AHEAD_SHORT = "前瞻：{words} {before}→{after}"
TIER_AHEAD_MAX = 2  # 整句里最多举出几张
# 档位前瞻说的是哪几档：先看必选、推荐（还有没持有的就说它们）；都没有时说可选
TIER_AHEAD_GROUPS = (("core", "first"), ("second",))


@dataclass(frozen=True)
class TierAhead:
    """一张标了档位的候选的档位前瞻（tier_lookahead）和它的说明（tier_ahead）。

    value：档位前瞻的分值（scheme_rank 按它给同一档排先后，命令行和诊断包写出来，卡片上不写）。words、before、after、
    items：说的是哪几档、下一次三选一里这几档还没持有、不在本次候选里的筹码至少出现一张的机会（不拿 / 拿了以后，不超过
    100%）、变化最大的一两张各自的出现率（筹码名, 不拿, 拿了以后）；没有可说的（方案里没有这样的必选、推荐、可选，或预计
    之后没有三选一）时 words 为空。reason：整句（悬停、命令行、诊断包；括号里写 items，items 为空时不写括号；说的只有一张时
    括号里只写它的名字，不把同一个数再写一遍）；short：理由标签上的短句，before、after 取整后相同时为空（卡片上不做这个
    标签）。second：words 说的是必选、推荐时，同样算法的可选至少出现一张的机会（不拿, 拿了以后），方案里没有这样的可选时
    为 None；同一档几张的必选、推荐那个数差不多、档位前瞻的差别在可选上时，排先后的说明里补写它。"""

    value: float
    words: str = ""
    before: float = 0.0
    after: float = 0.0
    items: tuple[tuple[str, float, float], ...] = ()
    reason: str = ""
    short: str = ""
    second: tuple[float, float] | None = None


def _next_round(sc: Scorer) -> int | None:
    """下一次三选一时的轮次：当前轮次 + [lookahead] round_advance；轮次未知时为 None。"""
    st = sc.st
    return None if st.round is None else st.round + sc.db.rules["lookahead"]["round_advance"]


def _source_key(sc: Scorer, mixed: bool, endless: bool = False):
    """缓存键里的来源（context.Memo）：False 是下一次三选一的主体（next_sources），True 是任务和筹码商店的混合
    （mixed_sources），"endless" 是循环往复给的额外那次（endless_sources）。来源相同时共用一份：3 星以后主体是循环往复那一次
    （还没给时）或混合，和对应的那一类记成 False。0～2 星被剩余轮次封顶、之后只剩循环往复那一次时（context.levelups_ahead
    为 0）主体也是它，这时不合并，两份算出来相同。"""
    if not (mixed or endless):
        return False
    if sc.st.star >= TOP_STAR and bool(endless) == bool(sc.picks.endless):
        return False
    return "endless" if endless else True


def _sources(sc: Scorer, map_name, key):
    """缓存键里的来源（_source_key）对应的来源列表。"""
    if key == "endless":
        return endless_sources(sc)
    return mixed_sources(sc, map_name) if key else next_sources(sc, map_name)


def future_setup(sc: Scorer, owned: tuple, map_name, mixed: bool = False, endless: bool = False):
    """持有这些筹码时，下一次三选一的筹码池、各来源的分布和每个筹码的分值（在地图 map_name 上）：
    （筹码池, ((占比, 最左格的分布, 其余两格的分布), …), {筹码名: 分值}），来源按 next_sources（mixed 时按 mixed_sources，
    升星和循环往复那一次以外的那几次；endless 时按 endless_sources，循环往复给的额外那次）的先后；最左格没有固定稀有度时
    （任务）最左格的分布为 None。

    几种来源的筹码池相同（只由已有筹码和地图定），只算一次、分值也只取一次；分布按来源各算一份。
    缓存的键是已有筹码、地图和来源（_source_key）：来源和占比由星数、地图和循环往复那一次给了没有定，一个上下文里都不变
    （context.Memo）。
    分值一律按「现在已有的筹码」来算，不随假设拿到的筹码变化：
    前瞻只衡量筹码池和出现概率的变化，同流派之间的配合留到真正拿到时再计分。
    循环往复的分值在这里是它本身的总分，按假设的局面调整在 future 里做。
    """
    kind = _source_key(sc, mixed, endless)
    key = (frozenset(owned), map_name, kind)
    if key not in sc.memo.setup:
        owned = sc.ordered(owned)  # 按集合缓存，计算也只看集合（同 parts）
        db, st = sc.db, sc.st
        rnd = _next_round(sc)
        pool, dists = None, []
        for share, source, star, tier in _sources(sc, map_name, kind):
            pool, left, general = pool_and_dists(db, sc.hero, owned, map_name, star, source, tier,
                                                 difficulty=st.difficulty, pool=pool, cache=sc.dist_cache)
            dists.append((share, left, general))
        value = pool_scores(sc, sc.known_owned, rnd, map_name, pool)
        sc.memo.setup[key] = (pool, tuple(dists), value)
    return sc.memo.setup[key]


def future(sc: Scorer, owned: tuple, zero: str | None = None, map_name=SAME_MAP, mixed: bool = False,
           endless: bool = False) -> float:
    """持有这些筹码时，下一次三选一里最高分的期望（在地图 map_name 上；默认为第一张候选地图，地图已知时就是 State.map）。
    来源同 future_setup：mixed、endless 都为 False 时是下一次三选一的主体（0～2 星按下一星的升星，3 星以后按循环往复那一次
    或任务和筹码商店），mixed 为 True 时是升星和循环往复那一次以外的那几次（任务和筹码商店），endless 为 True 时是循环往复
    给的额外那次（任务来源）；有两种来源时按占比加权两种来源的期望，分值的调整几种来源共用。

    zero：这个筹码仍留在筹码池里、照常占着出现的份额，但分值记为 0。
    循环往复在筹码池里时，它的分值里额外那次三选一按同一个假设的局面算（parts.endless_value）：owned 里比现在多拿的
    筹码不会在那次出现（筹码池和分布按拿了以后的），zero 在那次同样记 0。这样拿和不拿两边，这一张自己的分值都不经
    循环往复带进前瞻；局面和现在相同（没有多拿的、也没有 zero）时循环往复照常是它本身的总分。
    """
    if map_name is SAME_MAP:
        map_name = sc.maps[0]
    kind = _source_key(sc, mixed, endless)
    key = (frozenset(owned), zero, map_name, kind)
    if key not in sc.memo.future:
        _, dists, value = _setup_of(sc, owned, map_name, kind)
        if zero in value:
            value = {**value, zero: 0.0}
        endless = sc.db.rules["endless_cycle"]["chip"]
        gone = tuple(n for n in sc.ordered(owned) if n not in sc.known_owned)
        if endless in value and endless != zero and (gone or zero is not None):
            adjusted = _endless_adjusted(sc, owned, zero, map_name, gone)
            if adjusted is not None:
                value = {**value, endless: adjusted}
        sc.memo.future[key] = expected_best_mix(dists, value)
    return sc.memo.future[key]


def _setup_of(sc: Scorer, owned: tuple, map_name, kind):
    """按缓存键里的来源（_source_key 的结果）取 future_setup：False 是主体，True 是混合，"endless" 是循环往复那一次。"""
    if kind == "endless":
        return future_setup(sc, owned, map_name, endless=True)
    return future_setup(sc, owned, map_name, kind)


def _endless_adjusted(sc: Scorer, owned: tuple, zero: str | None, map_name, gone: tuple) -> float | None:
    """future 里循环往复按假设的局面重算的总分（parts.endless_value），按（已有筹码, 记 0 的筹码, 地图）缓存：只由这几样定，
    下一次三选一的主体、mixed 和 endless 的那一份共用一份，不重复算（context.Memo 的 endless）。"""
    key = (frozenset(owned), zero, map_name)
    if key not in sc.memo.endless:
        sc.memo.endless[key] = endless_value(sc, _next_round(sc), map_name, gone, zero)
    return sc.memo.endless[key]


def lookahead_times(sc: Scorer) -> float:
    """前瞻影响的选择次数：按星数取（[lookahead] picks），不超过预计剩余的筹码三选一次数（PicksLeft.total：预计之后
    没有三选一时为 0，和「之后无需保留刷新」一致）。持有的筹码数不设上限，不按空着的格数封顶。
    为 0 时前瞻记 0、不写前瞻的说明。"""
    la = sc.db.rules["lookahead"]
    picks = sc.picks
    return max(0.0, min(la["picks"][max(0, min(3, sc.st.star))], picks.total))


def _split_on(sc: Scorer, times: float, change) -> float:
    """前瞻、档位前瞻按次数分开加起来：weight ×（各类的次数 × 那一类的变化）之和（context.split_picks：升星、循环往复那
    一次、混合，按这个先后）。change(mixed, endless) 是一类三选一里拿 / 不拿两边的差（future 或 tier_future 的参数）。
    只有一类时就是 weight × 次数 × 那一类的变化，那一类一定是下一次三选一的主体（context.next_sources）：0～2 星的升星
    （被剩余轮次封顶、之后只剩循环往复那一次时是它），3 星以后循环往复那一次（还没给时）或混合。"""
    la = sc.db.rules["lookahead"]
    ups, endless, rest = split_picks(sc, times)
    groups = [(n, mixed, flag) for n, mixed, flag in ((ups, False, False), (endless, False, True), (rest, True, False))
              if n > 0]
    if len(groups) <= 1:
        return la["weight"] * times * change(False, False)
    total = 0.0
    for n, mixed, flag in groups:
        total += n * change(mixed, flag)
    return la["weight"] * total


def lookahead_on(sc: Scorer, name: str, owned: tuple, map_name) -> float:
    """前瞻 = weight × 影响的选择次数（lookahead_times）里各次三选一最高分期望的变化之和。刷新期望里的前瞻也走这里。

    影响的次数按次数分开算（context.split_picks）：剩下的升星一定会来，先算满，min(剩余升星次数, 次数) 次按下一星的升星；
    已拿循环往复、额外那次还没给时接着算那一次，按任务来源（future 的 endless）；其余按任务和筹码商店混合（future 的
    mixed）。0～1 星剩下的升星不少于影响的次数，全按升星；3 星以后全按混合（循环往复那一次还没给时先按它）；2 星影响 1.5 次
    时 1 次按升星（升到 3 星那次）、多出的半次按循环往复那一次（还没给时）或混合。只有一类时就是 weight × 次数 × 那一类的
    变化（_split_on）。"""
    times = lookahead_times(sc)
    if times <= 0:
        return 0.0

    def change(mixed: bool, endless: bool) -> float:
        return (future(sc, (*owned, name), map_name=map_name, mixed=mixed, endless=endless)
                - future(sc, owned, zero=name, map_name=map_name, mixed=mixed, endless=endless))

    return _split_on(sc, times, change)


def lookahead(sc: Scorer, name: str, owned: tuple) -> float:
    """拿这个筹码对之后三选一的影响：筹码池和出现概率的变化带来的部分（地图待识别时按候选地图平均）。

    - 「拿」：这个筹码移出筹码池（每个筹码一局只能拿一次）；带来新流派时还有同流派加权、两个流派后的锁定。
    - 「不拿」：它仍在筹码池里占着份额，但它自己的分值记为 0。

    这样「拿走后它不会再出现」不算损失（它已经在手上了），而它腾出的份额让给同池其他筹码的效果照算：
    例如手持风扇-大和魔法箭袋同在卡牌职业筹码池的紫色一栏，拿走风扇-大，箭袋在最左格的份额就变大。
    循环往复额外给的那次三选一里同样：拿的一边那次不会出现它，不拿的一边它在那次也记 0（future）。
    预计之后没有筹码三选一（PicksLeft.total 为 0）时记 0（lookahead_times）。
    已占满流派名额时，第三个流派的筹码本不该出现（识别或记账有误、手工输入的状态），拿了也不会改变筹码池，记 0
    （_locked_out：不按它的职业分值推算之后的最左格）。
    """
    owned = tuple(n for n in owned if n in sc.db.chips)
    if (name in owned or name not in sc.db.chips or lookahead_times(sc) <= 0
            or _locked_out(sc, name, owned)):
        return 0.0
    return sum(lookahead_on(sc, name, owned, m) for m in sc.maps) / len(sc.maps)


# ------------------------------------------------------------ 档位前瞻（只在按自己的方案计算时用）


def tier_values(sc: Scorer) -> dict[str, float]:
    """档位前瞻里之后三选一的每个筹码值多少（按方案的档位，不看分值）：标了档位的取 rules.toml [scheme] 对应的
    must、recommend、optional、skip；没标的取 unmarked_lookahead，属于被锁成不选、还没进入的流派的按 skip（同 Scorer.level
    的级数 0）。和分值一样按现在的已有筹码定，不随假设拿到的筹码变化（同前瞻的口径）：候选自己属于被锁成不选的流派、
    拿了以后会进那个流派时，「拿了」一边里那个流派没标的筹码仍按 skip 算（拿了以后它们其实不再被锁）。标了档位、又属于
    被锁的流派的候选只有标了不选的（排在最后，不参加同一档排先后），所以这只影响它们卡片上档位前瞻那一条的数。
    只算一次（Memo.tier_values）。"""
    if sc.memo.tier_values is None:
        cfg = sc.db.rules["scheme"]
        out: dict[str, float] = {}
        for name in sc.db.chips:
            tier = sc.marks.get(name)
            if tier is not None:
                out[name] = float(cfg[SCHEME_TIER_KEY[tier]])
            elif sc.level(name) <= 0:
                out[name] = float(cfg[SCHEME_TIER_KEY["avoid"]])
            else:
                out[name] = float(cfg["unmarked_lookahead"])
        sc.memo.tier_values = out
    return sc.memo.tier_values


def tier_future(sc: Scorer, owned: tuple, zero: str | None = None, map_name=SAME_MAP, mixed: bool = False,
                endless: bool = False) -> float:
    """持有这些筹码时，下一次三选一里按档位给分（tier_values）的最高分的期望。筹码池、来源和分布同 future（future_setup），
    zero 同样仍留在筹码池里、分值记 0。循环往复按自己的档位给分，不按假设的局面调整（见模块说明）。
    缓存在 Memo.tier_future（键同 future，和 future 分开放）。"""
    if map_name is SAME_MAP:
        map_name = sc.maps[0]
    kind = _source_key(sc, mixed, endless)
    key = (frozenset(owned), zero, map_name, kind)
    if key not in sc.memo.tier_future:
        pool, dists, _ = _setup_of(sc, owned, map_name, kind)
        values = tier_values(sc)
        value = {c.name: 0.0 if c.name == zero else values[c.name] for c in pool}
        sc.memo.tier_future[key] = expected_best_mix(dists, value)
    return sc.memo.tier_future[key]


def _tier_on(sc: Scorer, name: str, owned: tuple, map_name) -> float:
    """一张候选地图上的档位前瞻：和 lookahead_on 同一个算法（影响的次数、按次数分开算，_split_on），只把 future 换成
    tier_future。"""
    times = lookahead_times(sc)
    if times <= 0:
        return 0.0

    def change(mixed: bool, endless: bool) -> float:
        return (tier_future(sc, (*owned, name), map_name=map_name, mixed=mixed, endless=endless)
                - tier_future(sc, owned, zero=name, map_name=map_name, mixed=mixed, endless=endless))

    return _split_on(sc, times, change)


def tier_lookahead(sc: Scorer, name: str, owned: tuple) -> float:
    """档位前瞻：选了这张以后，方案里标了档位的筹码之后出现的机会怎么变（同 lookahead 的「拿 / 不拿」两边、影响的次数、
    候选地图平均，之后的筹码按 tier_values 给分）。不算「没选的那张以后还会再出现」（同前瞻的口径）。
    默认推荐、资料库没有的筹码、已持有的、预计之后没有三选一、已占满流派名额时第三个流派的筹码记 0（同 lookahead）。"""
    owned = tuple(n for n in owned if n in sc.db.chips)
    if (sc.plan is None or name in owned or name not in sc.db.chips or lookahead_times(sc) <= 0
            or _locked_out(sc, name, owned)):
        return 0.0
    return sum(_tier_on(sc, name, owned, m) for m in sc.maps) / len(sc.maps)


def tier_ahead(sc: Scorer, name: str, owned: tuple) -> TierAhead:
    """档位前瞻和它的说明（TierAhead）：下一次三选一里至少出现一张方案里还没持有的必选、推荐（没有时为可选）的机会，选了
    以后怎么变（_avg_any），举出变化最大的一两张各自的出现率（_avg_probs）。本次的候选（这张自己和另外两张）不算：同一次
    三选一的几张都按同一组筹码说，「现在」的机会相同，选了各张以后的机会可以直接比（scheme_rank 给同一档排先后时比的就是
    它）。按下一次三选一的主体，地图待识别时按候选地图平均（同前瞻的说明）；不在筹码池里的按 0 算。档位前瞻记 0 的情形
    （tier_lookahead）不写说明。"""
    db = sc.db
    value = sc.tier_lookahead(name, owned)
    owned = tuple(n for n in owned if n in db.chips)
    if (sc.plan is None or name in owned or name not in db.chips or lookahead_times(sc) <= 0
            or _locked_out(sc, name, owned)):
        return TierAhead(value)
    skip = {*owned, *sc.offer, name}
    rest = [(c, t) for c, t in sc.plan.tiers if c not in skip]
    chips, words, group = [], "", ()
    for group in TIER_AHEAD_GROUPS:
        chips = [c for c, t in rest if t in group]
        if chips:
            words = "、".join(SCHEME_TIER_WORD[t] for t in group if any(tt == t for _, tt in rest))
            break
    if not chips:
        return TierAhead(value)
    taken = (*owned, name)
    before, after = _avg_any(sc, owned, chips), _avg_any(sc, taken, chips)
    lower = [c for c, t in rest if t == "second"] if group != ("second",) else []
    second = (_avg_any(sc, owned, lower), _avg_any(sc, taken, lower)) if lower else None
    p0, _ = _avg_probs(sc, owned)  # 变化最大的一两张：各自的出现率
    p1, _ = _avg_probs(sc, taken)
    moved = [(abs(p1.get(c, 0.0) - p0.get(c, 0.0)), i, c) for i, c in enumerate(chips)
             if f"{p0.get(c, 0.0):.0%}" != f"{p1.get(c, 0.0):.0%}"]
    moved.sort(key=lambda r: (-r[0], r[1]))  # 变化大的在前，一样大的按方案的先后
    items = tuple((c, p0.get(c, 0.0), p1.get(c, 0.0)) for _, _, c in moved[:TIER_AHEAD_MAX])
    reason = TIER_AHEAD_REASON.format(words=words, before=f"{before:.0%}", after=f"{after:.0%}")
    if items and len(chips) == 1:
        # 只说一张时「至少出现一张」就是它自己的出现率，括号里只写名字，不把同一个数再写一遍；取整后没变（items 为空）时
        # 和几张时一样不写括号
        reason += TIER_AHEAD_ONE.format(chip=chips[0])
    elif items:
        reason += "（" + "、".join(TIER_AHEAD_ITEM.format(chip=c, before=f"{q0:.0%}", after=f"{q1:.0%}")
                                  for c, q0, q1 in items) + "）"
    short = ""
    if f"{before:.0%}" != f"{after:.0%}":
        short = TIER_AHEAD_SHORT.format(words=words, before=f"{before:.0%}", after=f"{after:.0%}")
        if len(short) > SHORT_LIMIT:  # 「必选、推荐 100%→100%」也放得下；万一放不下只写「前瞻」
            short = "前瞻"
    return TierAhead(value, words, before, after, items, reason, short, second)


def _class_shift(sc: Scorer, name: str) -> list[tuple[str, float]]:
    """选择这个筹码后职业分值增加的职业和分值（按 CLASSES 的顺序）：之后升星和筹码商店（3 星以后只有筹码商店）的最左格
    更常出现这些职业池的筹码（职业分值累加，rules.toml [pool] accumulate_class_points；关掉时为空）。3 星以后之后的任务和
    筹码商店里只有任务（[picks] shop 为 0）时，最左格不再出自职业筹码池，也为空（循环往复给的额外那次也是三格全随机，
    不看它：之后照样有筹码商店）。之后的三选一只剩循环往复给的那一次时（PicksLeft.total 不超过 endless：进度条走到头、
    那一次还没给，0～2 星时封顶多加的那 1 次也留给它，context.levelups_ahead）同样为空：那一次是任务来源，不看职业，
    之后也不再有升星和筹码商店。"""
    chip = sc.db.chips.get(name)
    if chip is None or not sc.db.rules["pool"].get("accumulate_class_points", True):
        return []
    picks = sc.picks
    if picks.endless and picks.total <= picks.endless:
        return []
    if sc.st.star >= TOP_STAR and all(source == "task" for m in sc.maps for _, source, _, _ in mixed_sources(sc, m)):
        return []
    return [(cls, chip.class_points[cls]) for cls in CLASSES if chip.class_points.get(cls, 0) > 0]


def _class_shift_short(sc: Scorer, classes: str) -> str:
    """职业分值累加那一句的短句（不含「前瞻：」）：3 星以后写筹码商店（CLASS_SHIFT_SHOP_SHORT），两个职业时放不下
    （「前瞻：」连短句超过 SHORT_LIMIT 个字），和还能升星时一样写 CLASS_SHIFT_SHORT（悬停整句里写明是筹码商店）。"""
    if sc.st.star >= TOP_STAR:
        text = CLASS_SHIFT_SHOP_SHORT.format(classes=classes)
        if len("前瞻：" + text) <= SHORT_LIMIT:
            return text
    return CLASS_SHIFT_SHORT.format(classes=classes)


def _locked_out(sc: Scorer, name: str, owned: tuple) -> bool:
    """已有筹码占满了流派名额（[pool] max_schools），这个筹码属于其余的流派：游戏里它不会出现在三选一里。"""
    chip = sc.db.chips.get(name)
    if chip is None or not chip.school:
        return False
    before = owned_schools(sc.db, owned)
    return chip.school not in before and len(before) >= sc.db.rules["pool"]["max_schools"]


def _avg_probs(sc: Scorer, owned: tuple):
    """各候选地图上：每个筹码在下一次三选一的平均出现率（不在某张地图的筹码池里时按 0 算；3 星以后按任务和筹码商店的
    占比加权，同 future），以及它在所在地图上的平均分值。"""
    probs: dict[str, float] = {}
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    n = len(sc.maps)
    for m in sc.maps:
        pool, dists, value = future_setup(sc, owned, m)
        for c in pool:
            probs[c.name] = probs.get(c.name, 0.0) + appear_prob_mix(dists, c.name) / n
            totals[c.name] = totals.get(c.name, 0.0) + value[c.name]
            counts[c.name] = counts.get(c.name, 0) + 1
    return probs, {c: totals[c] / counts[c] for c in totals}


def _avg_any(sc: Scorer, owned: tuple, chips) -> float:
    """下一次三选一里 chips 中至少出现一张的机会（档位前瞻卡片上的数，tier_ahead）：各候选地图上按 pool.appear_any_mix
    算（和逐张的出现率同一套出法：最左格和其余两格各自出到这几张的概率，三格各自独立；3 星以后按任务和筹码商店的占比加权，
    同 _avg_probs），地图待识别时按候选地图平均；不在某张地图筹码池里的按 0 算。只有一张时和 _avg_probs 里那一张的出现率
    逐位相同（每张地图加的次序也一样：不在那张地图筹码池里时加的是 0）。"""
    n = len(sc.maps)
    total = 0.0
    for m in sc.maps:
        _, dists, _ = future_setup(sc, owned, m)
        total += appear_any_mix(dists, chips) / n
    return total


def lookahead_reason(sc: Scorer, name: str, owned: tuple) -> str:
    """前瞻的说明（整句）。前瞻记 0 的情形（lookahead_times 为 0：预计之后没有筹码三选一，持有的筹码数不设上限，不按
    空着的格数算；已占满流派名额时第三个流派的筹码，_locked_out）不写。记下的要点（Memo.lookahead_keys）给 lookahead_short 用。"""
    db = sc.db
    owned = tuple(n for n in owned if n in db.chips)
    if name in owned or name not in db.chips or lookahead_times(sc) <= 0 or _locked_out(sc, name, owned):
        return ""
    chip = db.chips[name]
    before = owned_schools(db, owned)
    after = owned_schools(db, (*owned, name))
    strong = db.rules["lookahead"]["high_value"]
    bits: list[str] = []
    keys: dict = {}  # 理由标签短句（lookahead_short）用的要点，和 bits 同时记下

    # 和 lookahead() 一样：「不拿」的一边，这个筹码仍在筹码池里占着份额
    p0, value0 = _avg_probs(sc, owned)
    p1, value1 = _avg_probs(sc, (*owned, name))
    new_school = bool(chip.school) and chip.school not in before

    max_schools = db.rules["pool"]["max_schools"]
    if new_school and len(before) >= max_schools:
        pass  # 已占满流派时这个筹码本不该出现（手工输入的状态），不写流派的说明
    elif new_school:
        if len(after) >= max_schools:
            locked = [s for s in SCHOOLS if s not in after]
            # 按筹码方案计算时，锁掉的流派里有方案的必选、推荐另由「选了之后拿不到……」的提醒写（scheme_rank.loss），
            # 这句不再另接方案的说法
            bits.append("选择后锁定" + "".join(f"「{s}」" for s in after)
                        + "流派，" + "".join(f"「{s}」" for s in locked) + "流派的筹码本局不再出现")
            keys["locked"] = (after, locked)
            lost = sorted(
                (c for c in value0 if db.chips[c].school in locked and value0[c] >= strong),
                key=lambda c: -value0[c],
            )
            if lost:
                # 高分筹码：不计前瞻、按当前已有筹码算的分值 ≥ high_value。
                # 本次的其他候选也在其中时单独写「放弃本次的X，之后也不再出现」（它就摆在卡片上，
                # 写「本局不再出现」读起来像说错了），其余的照旧
                here = [c for c in lost if c in sc.offer]
                away = [c for c in lost if c not in sc.offer]
                if here:
                    bits.append("放弃本次的" + "、".join(here) + "，之后也不再出现")
                if away:
                    bits.append("本局不再出现的高适配度筹码：" + "、".join(away[:2]))
                keys["lost"] = lost
                keys["lost_here"] = here
        elif sc.lookahead(name, owned) < 0:
            bits.append(f"进入「{chip.school}」流派后同流派筹码出现率提高，其他高适配度筹码出现率下降")
            keys["diluted"] = None
            # 同流派加权会挤占别的筹码：举出被挤掉最多的那个高分筹码
            diluted = []
            for c, q0 in p0.items():
                if db.chips[c].school == chip.school or value0[c] < strong or c == name:
                    continue
                q1 = p1.get(c, 0.0)
                if f"{q0:.0%}" != f"{q1:.0%}":
                    diluted.append((value0[c] * (q0 - q1), c, q0, q1))
            for _, cname, q0, q1 in sorted(diluted, reverse=True)[:1]:
                bits.append(f"{cname}下次出现率 {q0:.0%} → {q1:.0%}")
                keys["diluted"] = (cname, q0, q1)
        else:
            bits.append(f"进入「{chip.school}」流派，之后同流派筹码出现率提高")
            keys["school"] = chip.school

    # 职业分值累加（rules.toml [pool] accumulate_class_points）：选择后之后升星和筹码商店的最左格更常出现这些职业池的筹码；
    # 3 星以后不再升星，只写筹码商店
    shift = _class_shift(sc, name)
    if shift:
        points = "、".join(f"{cls}职业分值 +{v:g}" for cls, v in shift)
        classes = "、".join(cls for cls, _ in shift)
        template = CLASS_SHIFT_SHOP_REASON if sc.st.star >= TOP_STAR else CLASS_SHIFT_REASON
        bits.append(template.format(points=points, classes=classes))
        keys["classes"] = classes
        if "diluted" not in keys and sc.lookahead(name, owned) < 0:
            # 前瞻为负、又不是进入新流派造成的：举出出现率下降最多的那个高分筹码。锁掉的流派里的（keys 的 lost）不举：
            # 前面已经写了本局不再出现，再写「→ 0%」是同一件事说两遍
            lost = keys.get("lost", ())
            drops = []
            for c, q0 in p0.items():
                if value0[c] < strong or c == name or c in lost:
                    continue
                q1 = p1.get(c, 0.0)
                if q1 < q0 and f"{q0:.0%}" != f"{q1:.0%}":
                    drops.append((value0[c] * (q0 - q1), c, q0, q1))
            for _, cname, q0, q1 in sorted(drops, reverse=True)[:1]:
                bits.append(f"{cname}下次出现率 {q0:.0%} → {q1:.0%}")
                keys["drop"] = (cname, q0, q1)

    # 出现率上升的高分筹码：同流派加权，以及这个筹码腾出的份额（同稀有度、同一职业筹码池）。按「分值 × 上升幅度」取前两个
    gains = []
    for c, q1 in p1.items():
        if value1[c] < strong:
            continue
        q0 = p0.get(c, 0.0)
        # 取整后要不同（同出现率下降那一种）：不写「4% → 4%」
        if q1 > q0 + 0.005 and f"{q0:.0%}" != f"{q1:.0%}":
            gains.append((value1[c] * (q1 - q0), c, q0, q1))
    if not new_school:
        bits.append("选择后该筹码移出筹码池，同稀有度筹码出现率提高")
        keys["pool"] = True
    for _, cname, q0, q1 in sorted(gains, reverse=True)[:2]:
        bits.append(f"{cname}下次出现率 {q0:.0%} → {q1:.0%}")
        keys.setdefault("gain", (cname, q0, q1))
    sc.memo.lookahead_keys[(name, owned)] = keys
    return "；".join(bits)


def lookahead_short(sc: Scorer, name: str, owned: tuple) -> str:
    """前瞻理由标签上的短句（不含分值）：从 lookahead_reason 的几句里取一个要点。

    前瞻为正时依次取：出现率上升最多的高分筹码、锁定的两个流派、进入的流派、之后更常出现的职业（职业分值累加）、
    同稀有度筹码出现率提高；
    为负时依次取：本局不再出现的高分筹码、出现率下降的高分筹码、其他高分筹码出现率下降、不再出现的流派、
    之后更常出现的职业。
    本局不再出现的高分筹码里分值最高的是本次的另一个候选时写「放弃本次的X」（不写「X不再出现」），
    不是本次候选的照旧写「X等不再出现」（「等」只算不是本次候选的）。整个短句不超过 SHORT_LIMIT 个字。
    """
    owned = tuple(n for n in owned if n in sc.db.chips)
    if (name, owned) not in sc.memo.lookahead_keys:
        lookahead_reason(sc, name, owned)
    keys = sc.memo.lookahead_keys.get((name, owned))
    if not keys:
        return ""

    def rate(item) -> str:
        cname, q0, q1 = item
        return f"{cname} {q0:.0%}→{q1:.0%}"

    lost, locked = keys.get("lost"), keys.get("locked")
    here = keys.get("lost_here") or ()
    lost_text = ""
    if lost and lost[0] in here:
        lost_text = f"放弃本次的{lost[0]}"
    elif lost:
        away = [c for c in lost if c not in here]
        lost_text = f"{away[0]}{'等' if len(away) > 1 else ''}不再出现"
    if len("前瞻：" + lost_text) > SHORT_LIMIT:
        lost_text = ""  # 筹码名太长放不下（现有筹码名最长 8 个字，放得下）：改取下一个要点
    classes = keys.get("classes") and _class_shift_short(sc, keys["classes"])
    if sc.lookahead(name, owned) < 0:
        options = [
            lost_text,
            keys.get("diluted") and rate(keys["diluted"]),
            keys.get("drop") and rate(keys["drop"]),
            "diluted" in keys and "其他高适配度筹码出现率下降",
            locked and "、".join(locked[1]) + "流派不再出现",
            classes,
            keys.get("gain") and rate(keys["gain"]),
        ]
    else:
        options = [
            keys.get("gain") and rate(keys["gain"]),
            locked and "锁定" + "、".join(locked[0]) + "流派",
            keys.get("school") and f"进入{keys['school']}流派",
            classes,
            keys.get("pool") and "同稀有度筹码出现率提高",
            lost_text,
        ]
    text = next((t for t in options if t), "")
    return f"前瞻：{text}" if text else ""
