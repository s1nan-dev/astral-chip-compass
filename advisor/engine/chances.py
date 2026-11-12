"""之后选到某筹码的概率：美工刀、治愈来源、配套筹码三个预判共用 take_chance（按地图取之后几次三选一的分布、几次里一个
都不出现的概率、按候选地图平均），各自只定「已有筹码按哪一组算」和「会选哪几个」：

- 之后获得美工刀（knife_chance）：筹码池不含正在评估的这个治愈来源，美工刀的分数按「已有筹码 + 这个治愈来源」算（那时
  没有治愈来源的两项：提前加分和折算）；
- 选了美工刀以后获得治愈来源（heal_chance）、选了以后获得配套筹码（partner_chance）：按「已有筹码 + 这一张」算，
  配套筹码按筹码名排好先后。
之后的几次三选一和前瞻一样按次数分开算（context.split_picks）：剩下的升星一定会来，按下一星的升星；已拿循环往复、额外
那次还没给时那一次按任务来源；其余是任务和筹码商店，按两者在一局里的占比混合。
三个预判的口径不同是有意的：美工刀预判的系数（rules.toml [heal_knife] anticipation_factor）是按它现在的口径定的。
另有 [heal_knife] 的几项设置（heal_knife、layer_value、has_heal_source、knife_school_bonus）。
把概率折成分项（治愈来源的提前加分和折算、美工刀预判、开套预判）在 parts；这里给之后的候选打分时经 Scorer.score、
Scorer.parts，不直接导入 parts（parts 要用这里的概率），也不导入 lookahead（它导入 parts）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..pool import appear_prob
from .context import endless_sources, levelup_sources, mixed_sources, pool_and_dists, split_picks

if TYPE_CHECKING:
    from .scorer import Scorer

# 美工刀的治愈来源折算（rules.toml [heal_knife] ③）：还没有治愈来源时，按之后获得治愈来源的概率折算美工刀的攻击力加成，
# 少算的部分记一项「组合」扣分（parts._heal_discount）。{p} 是概率的写法（「约 35%」「不到 1%」「超过 99%」）
HEAL_DISCOUNT_ORIGIN = "heal_discount"
HEAL_DISCOUNT_REASON = "尚未持有治愈来源，之后获得治愈来源的概率{p}：美工刀需生命值为满才生效，收益按该概率折算"
HEAL_DISCOUNT_SHORT = "尚无治愈来源，难以保持满血"
# 美工刀的治愈来源提前加分（rules.toml [heal_knife] ④）：还没有治愈来源时，预支之后配上治愈来源才有的组合分，记一项「组合」
# 加分（parts._heal_advance）。{p} 同上
HEAL_ADVANCE_ORIGIN = "heal_advance"
HEAL_ADVANCE_REASON = "预计之后获得治愈来源的概率{p}：美工刀在生命值为满时按治愈层数增加攻击力，治愈来源提供治愈层数"
HEAL_ADVANCE_SHORT = "之后获得治愈来源 {p}"


# ------------------------------------------------------------ 美工刀与治愈来源（rules.toml [heal_knife]）


def heal_knife(sc: Scorer) -> dict | None:
    """[heal_knife] 的设置；角色不带其中 traits 的任何一个特性时为 None（组合规则按原分值计）。

    只对物理输出角色（美工刀加的是攻击力）：未持有美工刀时按「之后获得美工刀的概率 × 组合分 × anticipation_factor」
    预先加分（算出的概率仍偏高，预判分打折，依据见 rules.toml），已持有时组合分按治愈层数缩放。
    """
    cfg = sc.db.rules.get("heal_knife")
    if not cfg or not set(cfg.get("traits", ())) & set(sc.traits):
        return None
    return cfg


def layer_value(name: str, knives: int, cfg: dict, default: float) -> float:
    """治愈来源配美工刀的组合分 = 每层分值 × 层数表（[heal_knife] layers：稳定治愈层数另加 1 层）里这个来源的层数
    × 持有的美工刀数（两把美工刀时层数计两次）。层数表里没有的筹码按组合规则的原分值。"""
    layers = cfg["layers"].get(name)
    if layers is None:
        return default
    return cfg["per_layer"] * layers * max(1, knives)


def has_heal_source(sc: Scorer, owned_set: frozenset, cfg: dict) -> bool:
    """已持有治愈来源（[heal_knife] layers 里的筹码），或队友里有带 healer_traits 特性的角色（视同已有治愈来源）。"""
    if owned_set & set(cfg["layers"]):
        return True
    healers = set(cfg.get("healer_traits", ()))
    for t in sc.st.teammates:
        hs = sc.db.hero_synergy.get(t.hero) if t.hero else None
        if hs and healers & set(hs.traits):
            return True
    return False


def knife_school_bonus(sc: Scorer, name: str) -> float:
    """美工刀的治愈来源折算（parts._heal_discount）要一并折算的推荐流派加分：要尽量保住推荐流派的角色（keep_schools，
    保全部或其中几个都算），默认推荐里美工刀属于要保的流派时为推荐流派的加分（[synergy] school），其余为 0。
    这类角色把治愈流派列为要保的流派，靠的是美工刀配治愈来源：还没有治愈来源时，美工刀的攻击力加成难以生效，
    进入治愈流派这一点同样要等治愈来源才有用，美工刀要先有治愈来源才推荐。只保一部分推荐流派、没列出治愈时，
    治愈的筹码照常加推荐流派的分（和不保的角色一样），这一项不折算。
    角色把美工刀列进 school_skip 时它没有推荐流派的加分（parts._build_parts），也就没有这一笔可折算，为 0。"""
    if sc.plan is not None or not sc.kept_schools or name in sc.school_skip:
        return 0.0
    school = sc.db.chips[name].school
    return float(sc.db.rules["synergy"]["school"]) if school and school in sc.kept_schools else 0.0


# ------------------------------------------------------------ 之后选到某筹码的概率


def take_chance(sc: Scorer, owned: tuple, take: list, maps) -> float:
    """之后的三选一里出现、并且会选 take 中至少一个筹码的概率（美工刀、治愈来源、配套筹码三个预判共用）。

    剩余 n 次三选一（picks.total）按次数分开算，和前瞻同一个口径（context.split_picks）：剩下的升星一定会来，L 次
    （min(剩余升星次数, n)）按下一星的升星（第一次刷出的规则，context.levelup_sources）；已拿循环往复、额外那次还没给时，
    接着 X 次（min(1, n − L)）按任务来源（三格全随机，context.endless_sources）；其余 E = n − L − X 次是任务和筹码商店，
    按两者在一局里的占比混合，任务的稀有度按地图资料里任务自带的等级、筹码商店按当前星数（context.mixed_sources）。3 星以后
    L = 0。
    一次三选一里 take 一个都不出现的概率：升星为 Π(1 − k 的出现率)；任务和筹码商店的一次三选一三格出自同一种来源，按来源
    分别算、再按占比加权：Σ 占比 × Π(1 − 这种来源下 k 的出现率)，不把两种来源的分布先平均再算（那样等于三格各自换来源）。
    n 次都不出现的概率是 miss_升星^L × miss_循环往复^X × miss_混合^E，至少一次为 1 减去它；地图待识别时按候选地图分别算出
    再平均，筹码池为空的地图不算（各来源的筹码池相同，只由已有筹码和地图定）。
    owned 是排好先后的已有筹码，take 是会选的筹码（只含总分达到推荐线的）：两者怎么取由调用方各按自己的口径定
    （美工刀不含正在评估的这一张、按集合排序，治愈来源和配套筹码按「已有筹码 + 这一张」）。take 为空、或之后没有
    三选一时为 0。"""
    n = sc.picks.total
    if not (take and n > 0):
        return 0.0
    db, st = sc.db, sc.st
    ups, endless, rest = split_picks(sc, n)
    per = []
    for m in maps:
        groups = []  # （次数, 这一类的来源）
        if ups > 0:
            groups.append((ups, levelup_sources(sc)))
        if endless > 0:
            groups.append((endless, endless_sources(sc)))
        if rest > 0:
            groups.append((rest, mixed_sources(sc, m)))
        pool, miss = None, 1.0  # miss：这几次三选一里一个都不出现的概率
        for count, sources in groups:
            one = 0.0  # 这一类的一次三选一里一个都不出现的概率（按来源的占比加权）
            for share, source, star, tier in sources:
                pool, left, general = pool_and_dists(db, sc.hero, owned, m, star, source, tier,
                                                     difficulty=st.difficulty, pool=pool, cache=sc.dist_cache)
                if not pool:
                    break
                each = 1.0
                for k in take:
                    each *= 1 - appear_prob(left, general, k)
                one += share * each
            if not pool:
                break
            miss *= one ** count
        if not pool:
            continue
        per.append(1 - miss)
    return sum(per) / len(per) if per else 0.0


def knife_chance(sc: Scorer, name: str, owned_set: frozenset, cfg: dict, map_name=None) -> float:
    """选择治愈来源 name 之后，之后的三选一里出现、并且会选美工刀（cfg["knives"]）的概率，算法见 take_chance（剩下的升星
    按下一星的升星，其余按任务和筹码商店混合）。

    只算总分达到推荐线（[advice] recommend_total）的美工刀。预判的是选择这个治愈来源之后的情形，所以美工刀的分数按
    「已有筹码 + name」算：有这个治愈来源配美工刀的组合分，也不再有治愈来源的那两项（parts._heal_advance、parts._heal_discount）。
    筹码池仍按现在的已有筹码 owned_set 算，不含 name（另两个预判连筹码池也按「已有筹码 + 这一张」）：
    [heal_knife] anticipation_factor 是按这个口径定的，统一口径要连同它重新定。
    map_name 给定时只按这张地图算（刷新期望、前瞻逐张地图计算时）；地图待识别时按候选地图分别算出概率再平均，
    和「按候选地图平均计算」的其余部分同一口径。
    """
    maps = (map_name,) if map_name else tuple(sc.maps)
    key = (name, owned_set, maps)
    if key in sc.memo.knife:
        return sc.memo.knife[key]
    db, st = sc.db, sc.st
    owned = sc.ordered(owned_set)  # 不按集合的遍历顺序（随哈希种子变化）
    line = db.rules["advice"]["recommend_total"]
    knives = [k for k in cfg["knives"] if k in db.chips and k not in owned_set]
    take = [k for k in knives if sc.score(k, (*owned, name), st.round) >= line]
    p = take_chance(sc, owned, take, maps)
    sc.memo.knife[key] = p
    return p


def heal_chance(sc: Scorer, name: str, owned_set: frozenset, map_name=None) -> float:
    """选择美工刀 name 之后，剩余三选一里出现、并且会选治愈来源（[heal_knife] layers）中至少一个的概率。

    算法见 take_chance（剩下的升星按下一星的升星，其余按任务和筹码商店混合），只算总分达到推荐线的治愈来源；折算不乘系数
    （parts._heal_discount），提前加分乘 [school_build] factor（parts._heal_advance），
    两者都不乘 [heal_knife] anticipation_factor。
    筹码池、流派、职业分值和治愈来源的分数都按「已有筹码 + 这把美工刀」计算：美工刀属于治愈流派，选择后同流派加权；
    治愈来源的分数含「已持有美工刀」的组合分（不会再回头算美工刀预判，两者不互相依赖）。"""
    maps = (map_name,) if map_name else tuple(sc.maps)
    key = (name, owned_set, maps)
    if key in sc.memo.heal:
        return sc.memo.heal[key]
    db, st = sc.db, sc.st
    cfg = db.rules["heal_knife"]
    owned = sc.ordered(owned_set) + (name,)  # 不按集合的遍历顺序（随哈希种子变化）
    line = db.rules["advice"]["recommend_total"]
    take = [h for h in cfg["layers"] if h in db.chips and h not in owned and sc.score(h, owned, st.round) >= line]
    p = take_chance(sc, owned, take, maps)
    sc.memo.heal[key] = p
    return p


def partner_chance(sc: Scorer, name: str, partners: frozenset, owned_set: frozenset, map_name=None) -> float:
    """选择 name 之后，剩余三选一里出现、并且会选 partners 中至少一个的概率，算法见 take_chance（剩下的升星按下一星的
    升星，其余按任务和筹码商店混合）。

    只算总分达到推荐线的配套筹码，按筹码名排好先后（不按集合的遍历顺序）。筹码池、流派和配套筹码的分数都按
    「已有筹码 + name」计算（选择后同流派加权）。
    """
    maps = (map_name,) if map_name else tuple(sc.maps)
    key = (name, partners, owned_set, maps)
    if key in sc.memo.partner:
        return sc.memo.partner[key]
    db, st = sc.db, sc.st
    owned = sc.ordered(owned_set) + (name,)  # 不按集合的遍历顺序（随哈希种子变化）
    line = db.rules["advice"]["recommend_total"]
    take = [k for k in sorted(partners) if k in db.chips and sc.score(k, owned, st.round) >= line]
    p = take_chance(sc, owned, take, maps)
    sc.memo.partner[key] = p
    return p
