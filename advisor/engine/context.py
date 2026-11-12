"""一次推荐的上下文（Context）：这次三选一的局面、筹码方案和候选，以及由它们定下来的已有筹码、地图、进度上限、
剩余三选一次数和各项计算的缓存（Memo）。Scorer（scorer.py）是它的子类，另加对外的方法；本包各模块里的计算函数
都接收这个对象。

缓存的前提：一个上下文只服务一次推荐。State、方案、候选在建的时候定下来、之后不变，所以各项缓存的结果只由键决定，
先算哪个、按什么顺序填进来都不影响结果（tests/test_engine_cache_order 按几种顺序核对）。换了局面、方案或候选要新建
一个，不能接着用旧的。

另有引擎用的筹码方案（Plan、plan_of）、角色的职业（hero_classes、hero_tendency）、剩余次数的两个换算（harmonic、
reward_rounds_left）、刷新留给之后的三选一几次（later_picks、reroll_keep：刷新的估值 reroll 和循环往复的估值 parts
共用，parts 不能导入 reroll），以及之后的三选一出自哪种来源（levelup_sources、endless_sources、mixed_sources、
next_sources）和按次数怎么分（split_picks）：前瞻（lookahead）和之后选到某筹码的三个概率（chances）共用它们。chances
不能导入 lookahead（lookahead 导入 parts，parts 又要用 chances 的概率），所以它们放在这里。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from ..data import CLASSES, DB, DIFFICULTIES, LEVEL_NIGHTMARE, NORMAL, TIERS, Chip, Hero, Rule, unrecorded_hero
from ..pool import Dist, State, owned_schools, player_pool, slot_dists
from ..schemes import listed_schools, locked_schools, target_schools
from .advice_text import SCHEME_TIER_WORD


@dataclass(frozen=True)
class Plan:
    """引擎用的筹码方案：只含参与打分的内容（不含说明、作者），可以比较、当字典的键（Session 判断「是不是同一个三选一」）。

    tiers 是（筹码名, 档位 core / first / second / avoid）按方案的先后排的元组：先按档位（必选 → 推荐 → 可选 → 不选），
    同一档按顺位（构造时按档位重排，同一档里保持传入的先后；同一个筹码出现几次时只留第一次）。由 plan_of 从方案存储的
    一套方案整理出来，只含资料库里有的筹码。名称参与比较（理由的整句里写方案名，改名后要重写），顺位也参与（改了顺位要重算）。
    列进方案的流派、被锁的流派、目标流派都由档位推出来（advisor.schemes 的 listed_schools 等），不另存。
    """

    name: str
    hero: str = ""
    tiers: tuple[tuple[str, str], ...] = ()
    ident: str = ""  # 方案存储里这套方案的编号（推荐日志、识别记录用）

    def __post_init__(self):
        seen: set[str] = set()
        pairs = []
        for chip, tier in self.tiers:
            if chip in seen or tier not in TIERS:
                continue
            seen.add(chip)
            pairs.append((chip, tier))
        pairs.sort(key=lambda p: TIERS.index(p[1]))  # 稳定排序：同一档里保持传入的先后
        object.__setattr__(self, "tiers", tuple(pairs))

    def marks(self) -> dict[str, str]:
        """筹码名 → 档位（按方案的先后）。"""
        return dict(self.tiers)

    def ranks(self) -> dict[str, int]:
        """筹码名 → 在这一档里的顺位（从 1 起）。"""
        out: dict[str, int] = {}
        count: dict[str, int] = {}
        for chip, tier in self.tiers:
            count[tier] = count.get(tier, 0) + 1
            out[chip] = count[tier]
        return out

    def record(self, schools=None) -> dict:
        """写进推荐日志、识别记录的内容：档位写成方案的叫法（必选、推荐……），按方案的先后（同一档的先后就是顺位）。
        schools 是计算时的目标流派（Advice.scheme_schools）。"""
        out = {"id": self.ident, "name": self.name, "hero": self.hero,
               "tiers": {chip: SCHEME_TIER_WORD[t] for chip, t in self.tiers}}
        if schools is not None:
            out["schools"] = list(schools)
        return out


def plan_of(db: DB, scheme) -> Plan | None:
    """方案存储里的一套方案（advisor.schemes.Scheme：hero、name、tiers、id；tiers 按方案的先后迭代）→ 引擎用的 Plan。

    只留资料库里有的筹码（资料更新后对不上的不参与计算）；档位不认识的不计。None（默认推荐）原样返回 None，
    已经是 Plan 的原样返回。"""
    if scheme is None or isinstance(scheme, Plan):
        return scheme
    marks = scheme.tiers
    items = marks.items() if hasattr(marks, "items") else marks
    tiers = tuple((chip, tier) for chip, tier in items if chip in db.chips and tier in SCHEME_TIER_WORD)
    return Plan(name=str(scheme.name), hero=str(getattr(scheme, "hero", "") or ""), tiers=tiers,
                ident=str(getattr(scheme, "id", "") or ""))


@dataclass
class PicksLeft:
    """本次之后预计还有几次筹码三选一（刷新的代价、前瞻影响的次数等都按它算）。

    total = levelups + extras + endless，轮次已知时再用「进度上限 − 当前轮次」封顶（有 endless 时封顶跟着加上它）。
    持有的筹码数不设上限（一局能拿超过 10 个，「查看筹码」面板一页 10 格，多了翻页），不按已有筹码的个数封顶。
    """

    total: float
    levelups: int  # 剩余升星次数（到 3 星）
    extras: float  # 预计还有的任务和筹码商店三选一（3 星以后、轮次已知时不低于 Context._late_floor）
    # 进度上限：读到进度条上的红叉时用它；否则知道具体难度时取那一难度的一列，普通难度取普通一列，困难及以上、不知道
    # 具体难度时取疯狂一列（data.MapInfo.cap_for）；地图待识别时取候选地图的常见值
    cap: int | None
    rounds_left: int | None  # 进度上限 − 当前轮次；轮次待识别时为 None
    capped: bool = False  # 是否被剩余轮次封顶
    # 已拿循环往复、它给的额外那次三选一还没给（State.endless_pending）时为 1：这一次一定会来，另外加上，不占任务和筹码
    # 商店的份额；没有、或到期那一轮打不到（State.endless_due 不早于进度上限，Context._endless_too_late）时为 0
    endless: int = 0


def _class_values(db: DB, hero_name: str, owned) -> dict[str, int]:
    hero = db.heroes.get(hero_name) or unrecorded_hero(hero_name)
    values = dict(hero.weights)
    for name in owned:
        chip = db.chips.get(name)
        if chip is None:
            continue
        for cls, pts in chip.class_points.items():
            values[cls] = values.get(cls, 0) + pts
    return values


def hero_classes(db: DB, hero_name: str, owned) -> frozenset[str]:
    """角色当前的职业：初始职业分值加上已有筹码的职业分值，达到职业门槛（rules.toml 的 class_threshold）的都算。

    可以同时属于几个职业，也可以一个都不属于（例如凛的卡牌 80，没有筹码时不属于任何职业）。
    """
    line = db.rules["official"]["class_threshold"]
    return frozenset(c for c, v in _class_values(db, hero_name, owned).items() if v >= line)


def hero_tendency(db: DB, hero_name: str, owned=()) -> tuple[str, int]:
    """职业倾向：职业分值最高的职业及其分值，例如（「攻击」，80）。并列时用「、」连接，例如（「卡牌、辅助」，60）。

    状态行在「职业：无」时写「职业：无（倾向：攻击 80）」；分值达到职业门槛时它就是当前职业之一。
    """
    values = _class_values(db, hero_name, owned)
    top = max(values.values())
    return "、".join(c for c in CLASSES if values.get(c) == top), top


def harmonic(x: float) -> float:
    """调和数 H(x) = 1 + 1/2 + … + 1/x，非整数时取连续延拓（x ≥ 0）。刷新代价、循环往复增加的刷新、自己的方案里
    按档位算的刷新代价都按它把剩余次数换成价值。

    m 个平均为 1 的指数分布随机数，最大值的期望就是 H(m)。
    先用 H(x) = H(x + 1) − 1/(x + 1) 把 x 推到 10 以上，再用渐近展开。H(0) = 0（展开式在这里有约 1e-6 的误差）。
    """
    if x <= 0:
        return 0.0
    shift = 0.0
    while x < 10:
        x += 1
        shift += 1 / x
    return math.log(x) + 0.5772156649015329 + 1 / (2 * x) - 1 / (12 * x * x) - shift


# 读到了刷新次数时，之后的三选一每次留 1 次刷新（次数向上取整）以外再多留几次：「之后还有几次三选一」是估计值，
# 对着真实对局常估少一次左右（一局的三选一往往比 rules.toml [picks] 按的多），多留 1 次以防估少（reroll_keep）
REROLL_MARGIN = 1
# 留刷新时「之后的三选一次数」按多少以内算作浮点误差（picks_needing）：total 不超过它时按之后没有三选一，2.000000001 也
# 不多留一次。PicksLeft.total 由整数次数和 rules.toml 里一位小数的参数加减乘除得到，不会正好落在 (0, PICKS_EPS] 里。
# 真落进去时：刷新次数读到了的按之后没有三选一留（留 0 次，剩余的刷新全部多出来、代价 0），只看 total > 0 的地方
# （例如 parts._endless_rerolls 开头、reroll.reroll_cost 开头）虽然仍当作还有三选一，按留法算下来同样不扣代价，两边结果
# 相同；刷新次数没读出来的不按多出来的算（keep_count 只看 total > 0），照旧全部留着、按公式扣代价，代价只有
# gain × H(total ÷ 剩余刷新次数) 那么一点
PICKS_EPS = 1e-9


def picks_needing(total: float) -> int:
    """预计还有 total 次筹码三选一时，按几次留刷新：向上取整，剩 0.3 次也按 1 次；浮点末位的误差不多算一次（total 不超过
    PICKS_EPS 时为 0，即预计之后没有三选一）。"""
    return max(0, math.ceil(total - PICKS_EPS))


def keep_count(rerolls: int, total: float, known: bool = True) -> int:
    """手上有 rerolls 次刷新、预计之后还有 total 次三选一时，留给之后的三选一的次数（其余是多出来的，现在用掉不算代价、
    可以连着用）。

    - 刷新次数读到了（known）：留 min(rerolls, picks_needing(total) + REROLL_MARGIN) 次，即每次三选一留 1 次、再多留 1 次
      以防估少；预计之后没有三选一时留 0 次，全部多出来。
    - 刷新次数没读出来、按默认值补的（可能的最多次数）：不按多出来的算。预计之后还有三选一（total 大于 0）时全部留着
      （只算刷新一次、按公式扣代价），之后没有三选一时留 0 次（全部连着用）。"""
    rerolls = max(0, rerolls)
    if not known:
        return rerolls if total > 0 else 0
    need = picks_needing(total)
    return min(rerolls, need + REROLL_MARGIN) if need > 0 else 0


def later_picks(ctx: Context) -> int:
    """这次三选一之后的筹码三选一按几次留刷新（picks_needing(PicksLeft.total)）。"""
    return picks_needing(ctx.picks.total)


def reroll_keep(ctx: Context, rerolls: int) -> int:
    """这次三选一时手上有 rerolls 次刷新，留给之后的三选一的次数（keep_count，按 PicksLeft.total 和
    State.rerolls_known）。reroll.reroll_split（这一次三选一的刷新）和 parts 里循环往复给的刷新（加上它给的次数以后）
    都按它分。"""
    return keep_count(rerolls, ctx.picks.total, bool(getattr(ctx.st, "rerolls_known", True)))


def reward_rounds_left(rnd: int, cap: int, every: int = 3) -> int:
    """当前轮次之后、进度上限之前还有几次轮次奖励（每逢第 3 轮开始时发放）。

    进度每轮 +1，进度到达上限的那一轮开始时即失败，所以只算 rnd < k < cap 的 k。
    """
    return sum(1 for k in range(rnd + 1, cap) if k % every == 0)


def _index(rules) -> dict[str, list[Rule]]:
    out: dict[str, list[Rule]] = {}
    for rule in rules:
        for chip in rule.chips:
            out.setdefault(chip, []).append(rule)
    return out


class Memo:
    """一次推荐里各项计算的缓存（Context.memo）：按名字分开的几个字典，和两个只算一次的值。

    键的约定：已有筹码一律按集合（frozenset）进键，计算时按 Context.ordered 排成确定的先后；轮次、地图可以是 None
    （不知道）；「地图组」是参与平均的几张地图（只按一张地图算时就是这一张）。
    - score、parts：(筹码名, frozenset(已有筹码), 轮次, 地图) → 总分、分项（parts 取用时复制一份）；
    - knife：(治愈来源, frozenset(已有筹码), 地图组) → 选这个治愈来源之后获得美工刀的概率；
    - setup：(frozenset(已有筹码), 地图, 来源) → 下一次三选一的（筹码池, 各来源的（占比, 最左格的分布, 其余两格的分布）,
      各筹码的分值）。来源为 False 时是下一次三选一的主体（next_sources：0～2 星是下一星的升星，3 星以后是循环往复给的
      额外那次（还没给时）或任务和筹码商店），为 True 时是升星和循环往复那一次以外的那几次（mixed_sources，任务和筹码
      商店），为 "endless" 时是循环往复给的额外那次（endless_sources）；和主体相同的一类键里记 False（lookahead._source_key）。
      来源和占比由星数、地图和循环往复那一次给了没有定，一个上下文里都不变，键里不另带（knife、heal、partner 三个概率同样）；
    - values：(地图, 轮次, 已有筹码) → 那个筹码池里每个筹码的总分表。一个筹码的总分只依赖（筹码, 已有筹码, 轮次, 地图），
      与「这个池子里有哪些筹码」无关，所以同一组已有筹码下更大的池子（例如现在已有筹码的池子）算出来的分值同样可用；
      前瞻每张地图、每个轮次只建一次分值表，之后各种「拿了某张之后」的筹码池直接从表里取（lookahead.future_setup）；
    - future：(frozenset(已有筹码), 分值记为 0 的筹码, 地图, 来源) → 下一次三选一最高分的期望（循环往复的分值按这个假设的
      局面调整，由键里的已有筹码和记 0 的筹码定，见 lookahead.future；来源同 setup）；
    - tier_future：键同 future → 按方案的档位给分时下一次三选一最高分的期望（lookahead.tier_future，档位前瞻用）。和 future
      分开放：两者键相同、分值不同，放在一个字典里会互相借结果；
    - tier_values：筹码名 → 档位前瞻里之后三选一的筹码值多少（lookahead.tier_values），只算一次，还没算时为 None；
    - endless：(frozenset(已有筹码), 分值记为 0 的筹码, 地图) → future 里按这个假设的局面重算的循环往复的总分
      （parts.endless_value，没有那一项时为 None），几种来源的期望共用；
    - endless_chain：地图 → 拿了循环往复以后，下一次三选一里最多连刷 1、2、… 次的期望最高分（各来源的（占比, 列表)），
      循环往复给的刷新里多出来的那几次按它估值（parts._endless_chain_gain）。只由这次的局面定（已有筹码、刷新次数、星数、
      轮次），和分项的已有筹码、轮次参数无关；
    - knife：(frozenset(已有筹码), 地图组) → 之后获得美工刀的概率；
    - heal：(美工刀, frozenset(已有筹码), 地图组) → 选这把美工刀之后获得治愈来源的概率；
    - partner：(筹码名, frozenset(配套筹码), frozenset(已有筹码), 地图组) → 选这个筹码之后获得配套筹码的概率；
    - reroll：tuple(候选) → 刷新后各候选地图上的（最左格的分布, 其余两格的分布, 各筹码含前瞻的分值）；
    - lookahead_keys：(筹码名, tuple(已有筹码)) → 前瞻说明里记下的要点（lookahead_reason 写、lookahead_short 读）；
    - survival：生存类筹码 → 分值；picks：剩余三选一次数（PicksLeft）。这两个只算一次，还没算时为 None。
    """

    __slots__ = ("score", "parts", "setup", "values", "future", "tier_future", "tier_values", "endless", "endless_chain",
                 "knife", "heal", "partner", "reroll", "lookahead_keys", "survival", "picks")

    def __init__(self):
        self.score: dict = {}
        self.parts: dict = {}
        self.setup: dict = {}
        self.values: dict = {}
        self.future: dict = {}
        self.tier_future: dict = {}
        self.tier_values: dict[str, float] | None = None
        self.endless: dict = {}
        self.endless_chain: dict = {}
        self.knife: dict = {}
        self.heal: dict = {}
        self.partner: dict = {}
        self.reroll: dict = {}
        self.lookahead_keys: dict = {}
        self.survival: dict[str, float] | None = None
        self.picks: PicksLeft | None = None


class Cache:
    """一帧里几次推荐共用的筹码池和分布（advise 的可选参数 cache）：由调用方创建、调用方丢弃，不能跨帧留着。

    只共用「筹码池 + 最左格分布 + 其余两格分布」这一层：分值不共用（星数不同时一部分筹码的分值不同）。分布只依赖
    pool_and_dists 键里的那些输入，所以键里全带上就不会把别的局面的分布借过来。一帧最多几次推荐（星数拿不准时按高一星
    再算一次；按方案计算时为了推荐日志里的对照再按默认推荐算一次），算完就丢，不随一局、一整天累积。
    键里不含资料库：共用一个 Cache 的几次推荐要用同一份资料库。不跨线程共用：在调用推荐的那个线程里、算这一帧之前新建。
    """

    __slots__ = ("dists",)

    def __init__(self):
        self.dists: dict = {}


TOP_STAR = 3  # 最高星数：到了以后不再升星，之后的三选一是任务和筹码商店（mixed_sources）

# 之后的三选一按次数分开算（前瞻 lookahead.lookahead_on、之后选到某筹码的三个概率 chances.take_chance 同一个口径）：
# 剩下的升星（PicksLeft.levelups，到 3 星）一定会来，先算满，按下一星的升星（levelup_sources）；已拿循环往复、它给的
# 额外那次三选一还没给时（PicksLeft.endless），那一次也一定会来，接着算，按任务来源（endless_sources）；其余的是任务和
# 筹码商店，按两者在一局里的占比混合（mixed_sources）。split_picks 把之后的几次三选一分成这三类。
# 来源写成（占比, 来源, 星数, 任务等级）的元组，一类的占比合计为 1。


def levelup_sources(ctx: Context) -> tuple[tuple[float, str, int, int | None], ...]:
    """剩下的升星：只有一种，下一星的升星（第一次刷出的规则，最左格是固定稀有度、出自职业筹码池）。只在还能升星
    （0～2 星）时用；剩下的几次升星都按下一星算。"""
    return ((1.0, "levelup", min(TOP_STAR, ctx.st.star + 1), None),)


def chip_tasks_on(ctx: Context, map_name) -> float:
    """地图 map_name 上一局的筹码任务数（剩余三选一次数和任务、筹码商店的占比共用）：按 data.MapInfo.tasks_for，知道是
    极限难度（State.difficulty_name）时取极限那一列，其余取普通到疯狂的那一列；地图不知道（None）、没有地图资料时按
    rules.toml [picks] default_tasks。"""
    info = ctx.db.map_info.get(map_name)
    if info is None:
        return float(ctx.db.rules["picks"]["default_tasks"])
    return info.tasks_for(getattr(ctx.st, "difficulty_name", None))


def task_level_column(st) -> str | None:
    """取任务等级（MapInfo.task_level_shares）时用哪一个难度那一列：知道难度名（State.difficulty_name）时就是它；没有
    难度名、只知道档位时按档位取：普通按普通那一列，噩梦及以上（分不出是噩梦、疯狂还是极限，例如进度上限噩梦和疯狂
    相同、画面上又没读到难度名）按噩梦那一列（资料里噩梦、疯狂两列相同；不按极限，同 chip_tasks_on 不知道具体难度时
    不按极限算）；都不知道时为 None，按困难那一列。"""
    name = getattr(st, "difficulty_name", None)
    if name is not None:
        return name
    if getattr(st, "difficulty", None) == NORMAL:
        return DIFFICULTIES[0]
    if getattr(st, "difficulty_level", None) == LEVEL_NIGHTMARE:
        return DIFFICULTIES[2]
    return None


def mixed_sources(ctx: Context, map_name) -> tuple[tuple[float, str, int, int | None], ...]:
    """升星以外的三选一（在地图 map_name 上）：任务和筹码商店，占比按 rules.toml [picks] 一局的任务和商店三选一（本地图
    的筹码任务数 chip_tasks_on × task_rate 对 shop；地图待识别时每张候选地图按各自的任务数，没有地图资料时按 default_tasks），
    和剩余三选一次数的推算同一个口径（Context._picks_left）。

    稀有度：任务三格全随机，等级按地图资料里任务自带的等级（MapInfo.task_level_shares：这个难度一局的等级比例，路线
    不知道时按各条线平均；取哪一列难度见 task_level_column），按比例拆成几项，和当前星数无关；地图不知道、这张图没写
    等级时照旧按当前星数（至少 1 级，3 星时 3 级）。筹码商店照升星的规则，稀有度按当前星数那一行、最左格是这一行的固定稀有度、出自职业筹码池。
    占比为 0 的来源不列；两者都为 0 时之后没有这类三选一（剩余三选一次数里只有升星，3 星以后为 0，也不加 3 星的下限，
    前瞻和三个概率都记 0），这里按筹码商店给一种，不会用到。"""
    star = max(0, min(TOP_STAR, ctx.st.star))
    cfg = ctx.db.rules["picks"]
    task = chip_tasks_on(ctx, map_name) * cfg["task_rate"]
    shop = cfg["shop"]
    if task + shop <= 0:
        return ((1.0, "shop", star, None),)
    out = []
    if task > 0:
        share = task / (task + shop)
        info = ctx.db.map_info.get(map_name)
        levels = info.task_level_shares(task_level_column(ctx.st)) if info else None
        if levels:
            out.extend((share * p, "task", star, level) for level, p in levels)
        else:
            out.append((share, "task", star, max(1, star)))
    if shop > 0:
        out.append((shop / (task + shop), "shop", star, None))
    return tuple(out)


def task_tiers(star: int, rate: float, wait: float) -> list[tuple[int, float]]:
    """过 wait 回合以后给的一次任务来源三选一的等级（任务筹码的 1~3 级，对应稀有度概率表的第 1~3 行）：按那时预计的星数，
    即当前星数 star + rate × wait（rate 是 rules.toml [endless_cycle] star_rate；不超过 3 星，不到 1 时按 1 级）。预计的
    星数不是整数时按相邻两级插值，返回（等级, 权重）的列表，权重合计为 1。wait 为 0 时就是当前星数那一级（至少 1 级）。
    循环往复给的额外那次：给它估值（parts.endless_tiers）和已拿到、那一次还没给时（endless_sources）共用。"""
    expect = min(3.0, max(1.0, max(0, min(TOP_STAR, star)) + rate * wait))
    low = int(math.floor(expect + 1e-9))
    frac = expect - low
    if frac < 1e-9 or low >= 3:
        return [(min(3, low), 1.0)]
    return [(low, 1.0 - frac), (low + 1, frac)]


def endless_sources(ctx: Context) -> tuple[tuple[float, str, int, int | None], ...]:
    """循环往复给的额外那次三选一（PicksLeft.endless）：任务来源（三格全随机），等级按到期那一轮预计的星数（task_tiers：
    当前星数 + [endless_cycle] star_rate × 离到期还有几轮，不是整数时按相邻两级插值、占比就是插值的权重），和给循环往复
    估值时额外那次三选一同一个算法（parts._endless_parts）。到期轮次（State.endless_due）或当前轮次不知道（旧记录、冻结的
    局面、命令行没给）、已经到期时按当前星数（至少 1 级）。3 星以后都是 3 级。"""
    st = ctx.st
    star = max(0, min(TOP_STAR, st.star))
    due = getattr(st, "endless_due", None)
    if due is None or st.round is None or due <= st.round:
        return ((1.0, "task", star, max(1, star)),)
    rate = float(ctx.db.rules["endless_cycle"].get("star_rate", 0.0))
    return tuple((share, "task", star, tier) for tier, share in task_tiers(star, rate, due - st.round))


def levelups_ahead(ctx: Context) -> int | float:
    """之后的三选一里最多几次按升星算：剩余升星次数（PicksLeft.levelups）。被剩余轮次封顶、又有循环往复那 1 次时
    （PicksLeft.capped、endless），封顶多加的 1 次是留给循环往复那一次的，升星最多只占「进度上限 − 当前轮次」那几次
    （PicksLeft.total − endless）：例如 1 星、进度条走到头（剩余 0 轮）、那一次还没给时，之后的 1 次是它，不是升星。
    没有被封顶，或剩余的轮数不少于剩余升星次数时就是剩余升星次数（原样的整数）。"""
    p = ctx.picks
    if p.capped and p.endless:
        return min(p.levelups, p.total - p.endless)
    return p.levelups


def next_sources(ctx: Context, map_name) -> tuple[tuple[float, str, int, int | None], ...]:
    """下一次三选一的主体（在地图 map_name 上），按 split_picks 的先后：还能升星（0～2 星）时是下一星的升星（剩下的升星
    一定会来，levelup_sources）；3 星以后不再升星，循环往复给的额外那次还没给时是那一次（endless_sources），否则是任务和
    筹码商店（mixed_sources）。0～2 星被剩余轮次封顶、之后只剩循环往复那一次时（levelups_ahead 为 0）也是那一次。
    前瞻的说明（「下次出现率」）和档位前瞻卡片上的数（下一次三选一里至少出现一张方案里的必选、推荐的机会）按它写。"""
    if ctx.st.star < TOP_STAR and levelups_ahead(ctx) > 0:
        return levelup_sources(ctx)
    return endless_sources(ctx) if ctx.picks.endless else mixed_sources(ctx, map_name)


def split_picks(ctx: Context, count: float) -> tuple[float, float, float]:
    """之后的 count 次三选一里（预计剩余的次数，或前瞻影响的次数），按升星、循环往复给的那一次、任务和筹码商店混合各算
    几次：剩下的升星一定会来，先算满，min(levelups_ahead, count) 次按升星；已拿循环往复、额外那次还没给时
    （PicksLeft.endless）接着算那一次，min(1, 剩下的) 次按任务来源（endless_sources）；其余按混合。3 星以后没有升星。
    剩余次数被剩余轮次封顶时，封顶多加的那 1 次留给循环往复那一次（levelups_ahead），升星只占剩余的轮数；没有那一次时
    先算升星。返回（升星, 循环往复那一次, 混合）。"""
    ups = min(levelups_ahead(ctx), count)
    endless = min(ctx.picks.endless, count - ups)
    return ups, endless, count - ups - endless


def pool_and_dists(
    db: DB,
    hero: Hero,
    owned,
    map_name: str | None,
    star: int,
    source: str = "levelup",
    tier: int | None = None,
    probabilities: tuple[int, int, int] | None = None,
    left_rarity: str | None = None,
    reroll: bool = False,
    difficulty: str | None = None,
    exclude=(),
    pool: list[Chip] | None = None,
    cache: dict | None = None,
) -> tuple[list[Chip], Dist | None, Dist]:
    """一次筹码池和它的分布（最左格, 其余两格）：pool.player_pool + pool.slot_dists。

    pool 给了就不再算筹码池：它必须就是 player_pool(db, owned, map_name, exclude=exclude) 算出来的那个池子（调用方已经
    算过），给了 cache 时它连同分布按键存进去、供同一帧别的调用取用。cache 是「一帧的分布缓存」（advise 的可选参数，Cache.dists）：
    给了时按下面的输入取用、算好再存进去，同一帧的几次推荐（星数拿不准时按高一星再算一次、方案推荐和默认推荐各算一次）
    共用这一层；不给时每次现算。键含它们依赖的全部输入——角色名、已有筹码、地图、星数、来源、tier、概率行、
    最左格稀有度、刷新标志、难度、排除的候选，缺一个就会把别的局面的分布借过来。放在缓存里的清单只读，调用方不要改。
    """
    if cache is None:
        if pool is None:
            pool = player_pool(db, owned, map_name, exclude=exclude)
        left, general = slot_dists(db, hero, pool, owned_schools(db, owned), star, source, tier,
                                   probabilities=probabilities, left_rarity=left_rarity, reroll=reroll,
                                   difficulty=difficulty, class_owned=owned)
        return pool, left, general
    key = (hero.name, tuple(owned), map_name, star, source, tier, probabilities, left_rarity, bool(reroll),
           difficulty, tuple(exclude))
    hit = cache.get(key)
    if hit is None:
        if pool is None:
            pool = player_pool(db, owned, map_name, exclude=exclude)
        left, general = slot_dists(db, hero, pool, owned_schools(db, owned), star, source, tier,
                                   probabilities=probabilities, left_rarity=left_rarity, reroll=reroll,
                                   difficulty=difficulty, class_owned=owned)
        hit = (pool, left, general)
        cache[key] = hit
    return hit


class Context:
    """一次三选一里所有打分共用的上下文（见模块说明）。"""

    def __init__(self, db: DB, st: State, use_player: bool = True, offer=(), scheme=None, cache=None):
        """use_player=False 时忽略玩家思路层，只用自动层（tools/audit_synergy.py 核对自动层时用）。
        offer 用来缩小候选地图（地图专属筹码），前瞻的说明也用它区分本局不再出现的高适配度筹码是不是本次的候选
        （lookahead_reason）。
        scheme：按这套筹码方案计算（Plan，或方案存储里的一套方案，经 plan_of 整理）；为 None 时是默认推荐，
        不走按方案计算的分支。规则见 parts._player_match、parts._scheme_school_parts 和 scheme_rank.advise_scheme。
        cache：这一帧的分布缓存（Cache，可选）。给了时同一帧的几次推荐共用筹码池和分布；不给时每次现算。"""
        self.db = db
        self.st = st
        self.offer = tuple(offer)  # 本次三选一的候选筹码（不知道时为空）
        self.dist_cache = cache.dists if cache is not None else None  # 一帧共用的分布缓存（传给 pool_and_dists）
        self.plan = scheme = plan_of(db, scheme)
        self.marks: dict[str, str] = scheme.marks() if scheme is not None else {}  # 方案标了档位的筹码 → 档名
        self.ranks: dict[str, int] = scheme.ranks() if scheme is not None else {}  # 方案标了档位的筹码 → 顺位
        # 列进方案的流派、方案锁住的流派（不管进没进）、目标流派：都由档位推出来（advisor.schemes）；默认推荐时为空
        self.listed = listed_schools(db, self.marks) if scheme is not None else ()
        self.scheme_locked = locked_schools(db, self.marks) if scheme is not None else ()
        self.targets = target_schools(db, self.marks) if scheme is not None else ()
        self.hero = db.heroes.get(st.hero) or unrecorded_hero(st.hero)
        self.known_owned = tuple(n for n in st.owned if n in db.chips)
        self.entered = owned_schools(db, self.known_owned)  # 已有筹码占了哪些流派（按拿到的先后）
        self._owned_rank: dict[str, int] = {}  # 已有筹码 → 第几个获得的（ordered 用）
        for i, n in enumerate(st.owned):
            self._owned_rank.setdefault(n, i)
        self.classes = hero_classes(db, st.hero, self.known_owned)
        self.tendency = hero_tendency(db, st.hero, self.known_owned)
        self.hs = db.hero_synergy.get(st.hero)
        self.traits = self.hs.traits if self.hs else ()
        self.ps = db.player_synergy.get(st.hero) if use_player else None
        if self.ps and self.ps.schools:
            self.pref_schools = self.ps.schools
            # 玩家思路点名要的流派不受 school_skip 影响，名单只在推荐流派来自角色自己的 schools 时起作用
            self.school_skip: frozenset[str] = frozenset()
        else:
            self.pref_schools = self.hs.schools if self.hs else ()
            self.school_skip = frozenset(self.hs.school_skip) if self.hs else frozenset()
        # 默认推荐里要尽量保住的推荐流派（HeroSynergy.keep_schools：True 为推荐流派都保，元组为只保其中这几个），按推荐流派的
        # 先后；不保时为空。扣分取角色自己写的 keep_schools_penalty，没写时取 rules.toml [synergy] keep_schools
        keep = self.hs.keep_schools if self.hs else False
        if keep is True:
            self.kept_schools = self.pref_schools
        else:
            self.kept_schools = tuple(s for s in self.pref_schools if keep and s in keep)
        penalty = self.hs.keep_schools_penalty if self.hs else None
        self.keep_penalty = db.rules["synergy"].get("keep_schools") if penalty is None else penalty
        self.auto_rules = _index(self.hs.rules if self.hs else db.general_rules())  # 自动层：筹码名 → 规则
        self.player_rules = _index(self.ps.rules if self.ps else ())  # 玩家思路（默认推荐的规则）：筹码名 → 规则
        # 靠角色提供的体系来源（rules.toml [sources] 的 heroes）：自己或任一队友是其中的角色时，这种来源本局一直都有，
        # 和已有筹码无关，建的时候算一次（held_sources）。队友真人、电脑都算，角色待识别的队友不算
        present = {st.hero, *(t.hero for t in st.teammates if t.hero)}
        self.innate_sources = frozenset(name for name, s in db.sources.items() if s.heroes & present)
        self.maps = self._pool_maps(offer)
        # cap_shown 是进度上限本身（显示用）；cap 折算成「轮次」：读到当前格时为 当前轮次 +（进度上限 − 当前格），
        # 这样「cap − 当前轮次」就是剩余的轮数（进度比轮次走得快时，剩下的轮数更少）
        self.cap_shown = self._progress_cap()
        self.cap = self._round_cap(self.cap_shown)
        self.memo = Memo()

    def ordered(self, owned) -> tuple[str, ...]:
        """一组已有筹码排成确定的顺序（只留资料库收录的，去掉重复）：State.owned 里的按获得的先后，其余的（推算
        「拿了之后」时假设拿到的）排在后面、按筹码名。

        流派按已有筹码的先后最多取前两个（pool.owned_schools）。分值、筹码池和下一次三选一的分布都按
        frozenset(owned) 缓存，算的时候却会用到传进来的顺序：调用方有的从集合转回元组，顺序随 Python 的哈希种子变化。
        已有筹码记成 3 个以上流派时（识别有误、手动补录错），取哪两个流派就跟着变，同一个局面每次启动的分值不同。
        统一排成这个顺序后结果只由集合本身决定；流派不超过两个时（游戏里只会这样）先后不影响结果。"""
        rank, last = self._owned_rank, len(self._owned_rank)
        names = dict.fromkeys(n for n in owned if n in self.db.chips)
        return tuple(sorted(names, key=lambda n: (rank.get(n, last), "" if n in rank else n)))

    def held_sources(self, owned_set: frozenset) -> frozenset[str]:
        """已有来源的体系（rules.toml [sources] 的名字）：适配表规则的 needs_source、without_source 按它判断（Rule.applies）。
        靠角色提供的（innate_sources）一直都有，其余看已有筹码里有没有那一项 chips 里的筹码。owned_set 是计算分项时的
        已有筹码：推算「拿了以后」时含假设拿到的，正在评估的候选自己不在里面（候选自己带来的效果不算它自己的来源）。"""
        held = [name for name, s in self.db.sources.items() if name not in self.innate_sources and s.chips & owned_set]
        return self.innate_sources.union(held) if held else self.innate_sources

    # ------------------------------------------------------------ 地图

    def _pool_maps(self, offer) -> tuple[str | None, ...]:
        """计算筹码池时用的地图。地图已知时只有这一张；待识别时为候选地图，并按地图专属筹码缩小范围。

        候选里没有包含某个专属筹码的地图时（例如运动场系列等已结束的限时地图），改从全部地图（含已结束、
        已过期的后备地图）里取包含它的地图。
        """
        db, st = self.db, self.st
        if st.map:
            return (st.map,)
        cands = [m for m in (st.map_candidates or db.active_maps()) if m in db.maps]
        fixed = False  # 已经按某个专属筹码缩小过：之后对不上的筹码（识别或记账有误）不再改换候选
        for name in (*offer, *st.owned):
            chip = db.chips.get(name)
            if chip and chip.maps:
                narrowed = [m for m in cands if m in chip.maps]
                if not narrowed and not fixed:
                    narrowed = [m for m in db.maps if m in chip.maps]
                if narrowed:
                    cands, fixed = narrowed, True
        return tuple(cands) or (None,)

    def _progress_cap(self) -> int:
        """进度上限：对局画面的进度条上读到了红叉所在的格（State.progress_cap）就用它；否则按地图资料（MapInfo.cap_for）：
        画面上读到了具体难度（State.difficulty_name：困难、噩梦、疯狂、极限）时取那一难度的一列，普通难度取普通一列，
        困难及以上、不知道具体难度时取疯狂一列。地图待识别时取候选地图里最常见的值，并列时取较小的。"""
        seen = getattr(self.st, "progress_cap", None)
        if seen:
            return int(seen)
        info = self.db.map_info
        name = getattr(self.st, "difficulty_name", None)
        caps = [c for c in (info[m].cap_for(self.st.difficulty, name) for m in self.maps if m in info) if c]
        if not caps:
            return int(self.db.rules["picks"]["default_cap"])
        count = Counter(caps)
        return min(count, key=lambda c: (-count[c], c))

    def _round_cap(self, cap: int) -> int:
        """进度上限折算成轮次：轮次和当前格都读到时为 当前轮次 +（进度上限 − 当前格），否则就是进度上限。"""
        st = self.st
        progress = getattr(st, "progress", None)
        if progress and st.round is not None:
            return st.round + max(0, cap - int(progress))
        return cap

    # ------------------------------------------------------------ 剩余三选一次数

    @property
    def picks(self) -> PicksLeft:
        if self.memo.picks is None:
            self.memo.picks = self._picks_left()
        return self.memo.picks

    def _picks_left(self) -> PicksLeft:
        """本次之后预计还有几次筹码三选一。

        剩余升星次数（到 3 星）+ 预计还有的任务和筹码商店三选一 + 循环往复还没给的那 1 次，轮次已知时用「进度上限 −
        当前轮次」封顶（循环往复那 1 次不受这一层封顶：封顶也加 1）。
        - 一局的任务和商店三选一 = 本地图的筹码任务数（chip_tasks_on：知道是极限时按极限一列）× task_rate + shop
          （候选地图取平均）。
        - 已经有过几次：已有筹码数（不含地图直接发放的）减去已升星的次数和不经三选一拿到的个数（State.direct），
          不小于 0；State.tasks_done 已知时取两者较大的。同一个筹码记了两次（识别或补录有误）时只数一次。循环往复给的
          额外那次三选一不是任务和筹码商店的三选一、不占一局的那份：这一次就是它、或它选到的筹码在已有筹码里时
          （State.endless_given）扣掉。
        - 轮次已知时，任务和商店三选一还按「到 end_round 前剩余的轮次比例」收窄（玩家经验：约第 12–14 轮结束）。
        - 3 星以后、轮次已知时，任务和商店三选一不低于 _late_floor：一局没有固定的结束轮次，3 星以后还会去筹码商店买，
          按第 13 轮收窄到 0 以后仍可能还有，进度条走到头之前不估成 0。一局的任务和商店三选一为 0（rules.toml 的
          task_rate、shop 都写 0）时没有这类三选一，不加下限。
        - 已拿循环往复、它给的额外那次三选一还没给（State.endless_pending，Session 记账）时另加 1 次（PicksLeft.endless）；
          到期那一轮打不到（_endless_too_late）时不加。
        - 持有的筹码数不设上限，不按空着的格数封顶（见 PicksLeft）。
        """
        db, st = self.db, self.st
        cfg = db.rules["picks"]
        star = max(0, min(TOP_STAR, st.star))
        levelups = TOP_STAR - star
        owned = [n for n in dict.fromkeys(st.owned) if not (n in db.chips and db.chips[n].granted)]
        levelups_done = star - 1 if st.source == "levelup" and star > 0 else star
        direct = max(0, min(len(owned), int(st.direct or 0)))
        before = max(0, len(owned) - levelups_done - direct)
        if st.tasks_done is not None:
            before = max(before, st.tasks_done)
        if st.source != "levelup":
            before += 1  # 这一次本身就是任务或筹码商店的三选一
        # 循环往复给的额外那次（这一次就是它，或它选到的筹码在已有筹码里）上面按任务或筹码商店的一次数进去了，扣掉
        given = max(0, int(getattr(st, "endless_given", 0) or 0))
        if given:
            before = max(0, before - given)
        info = db.map_info
        tasks = [chip_tasks_on(self, m) for m in self.maps if m in info]
        per_game = (sum(tasks) / len(tasks) if tasks else cfg["default_tasks"]) * cfg["task_rate"] + cfg["shop"]
        extras = max(0.0, per_game - before)
        endless = 1 if getattr(st, "endless_pending", False) and not self._endless_too_late() else 0
        rounds_left = None
        capped = False
        if st.round is not None:
            end = cfg["end_round"]
            frac = min(1.0, max(0.0, (end - st.round) / (end - 1)))
            extras = min(extras, per_game * frac)
            if star >= TOP_STAR and per_game > 0:
                extras = max(extras, self._late_floor())
            rounds_left = max(0, self.cap - st.round)
        total = levelups + extras + endless
        if rounds_left is not None and total > rounds_left + endless:
            total, capped = float(rounds_left + endless), True
        return PicksLeft(total=total, levelups=levelups, extras=extras, cap=self.cap_shown, rounds_left=rounds_left,
                         capped=capped, endless=endless)

    def _endless_too_late(self) -> bool:
        """循环往复给的额外那次三选一到期的轮次（State.endless_due）不早于进度上限（Context.cap，按轮次算）：到那一轮时
        一局已经结束，这一次不会来，_picks_left 不多算它。和给循环往复估值时同一个口径（parts._endless_parts：拿到以后过
        delay 回合已到进度上限时，额外那次记 0）。到期轮次不知道（旧记录、冻结的局面、命令行没给），或进度上限只是估计的
        （进度条上没读到、地图又待识别或没有资料）时为 False，照原来多算 1 次。"""
        due = getattr(self.st, "endless_due", None)
        if due is None:
            return False
        if not getattr(self.st, "progress_cap", None):
            info = self.db.map_info.get(self.st.map) if self.st.map else None
            if info is None or not info.cap_for(self.st.difficulty, getattr(self.st, "difficulty_name", None)):
                return False
        return due >= self.cap

    def _late_floor(self) -> float:
        """3 星以后（轮次已知时）任务和筹码商店三选一的下限：rules.toml [picks] late_rate ×（H + 0.5），H 是「存活条件下
        预计还剩几轮」（_late_rounds）。到进度上限前最后能打的一轮也还有 late_rate × 0.5，进度条走到头（剩余轮数为 0）
        以后由「进度上限 − 当前轮次」封顶到 0。"""
        cfg = self.db.rules["picks"]
        return float(cfg["late_rate"]) * (self._late_rounds() + 0.5)

    def _late_rounds(self) -> float:
        """存活条件下预计还剩几轮（H，轮次已知时用）：一局没有固定的结束轮次（打倒首领就赢，进度条走到红叉才输），
        结束的轮次假定在「半个进度上限」到 C 之间均匀，已经打到本轮的只看本轮以后那一段，取平均再减去本轮。
        C 是进度上限前最后能打的一轮（Context.cap − 1：读到当前格时 cap 是本轮 +（进度上限 − 当前格），否则就是进度
        上限）；C 不大于本轮时为 0。「半个进度上限」是 rules.toml [picks] late_from ×（进度上限 − 1），按进度上限本身
        （cap_shown）算，不跟着当前格变。"""
        rnd = self.st.round
        last = self.cap - 1
        if rnd is None or last <= rnd:
            return 0.0
        start = min(max(float(self.db.rules["picks"]["late_from"]) * (self.cap_shown - 1), rnd), last)
        return (start + last) / 2 - rnd
