"""筹码池和三选一的概率模型。

三选一的三格视为相互独立（忽略「三格不重复」带来的微小修正），
这样最高分的期望可以解析算出，同样的输入永远得到同样的结果。
升星和筹码商店的最左格先按职业权重选职业筹码池：推荐引擎按已拿筹码累加职业分值（slot_dists 的 class_owned），
不传 class_owned 时只按角色初始职业分值（编辑窗口的「较难出现」）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import CLASSES, DB, HARD, LEFTMOST_MODES, NORMAL, RARITIES, Chip, Hero

Dist = dict[str, float]  # 筹码名 → 出现在这一格的概率


def tie_key(value: float) -> float:
    """比较总分、百分比时用的键（engine 从这里取，engine.tie_key 就是它）。

    总分由遍历集合（已有筹码、规则里的筹码、筹码池）累加得到，浮点求和的顺序随哈希种子变化，末位会不同。
    舍到 9 位小数后，数学上相等的两个选项就按候选筹码的顺序排，不会在两次启动之间翻转；刷新的概率（prob_above）
    比较筹码池里的分值和比较对象的总分时同样用它。
    """
    return round(value, 9)


@dataclass(frozen=True)
class Teammate:
    """一名队友：角色（识别不出时为 None）、是否电脑。"""

    hero: str | None = None
    bot: bool = False


def _teammate(item) -> Teammate:
    if isinstance(item, Teammate):
        return item
    if isinstance(item, dict):
        return Teammate(hero=item.get("hero"), bot=bool(item.get("bot", False)))
    hero, bot = item
    return Teammate(hero=hero, bot=bool(bot))


@dataclass(frozen=True)
class State:
    """一次三选一时的对局状态。

    列表类的字段（owned、map_candidates、teammates、mutators）可以传 list，构造时统一转成 tuple，
    State 可以比较、也可以当字典的键。
    """

    hero: str
    star: int = 0
    round: int | None = None
    map: str | None = None
    owned: tuple[str, ...] = ()
    source: str = "levelup"  # levelup 升星 / shop 筹码商店 / task 任务
    tier: int | None = None  # 任务筹码的等级 1~3；不填按星数估
    rerolls: int = 0  # 剩余刷新次数（界面上的「刷新 ×N」）
    # 剩余刷新次数是不是读到的（或调用方直接给的）。读不出、按「一次都没用过」补的默认值时为 False（Session 填）：
    # 这时默认值是可能的最多次数，刷新不按「多出来的」算，只算刷新一次、按公式扣代价（engine.context.reroll_keep）。
    # 旧记录里没有这一项时按读到的算
    rerolls_known: bool = True
    # 从画面上直接读到的信息，有的话优先于按星数推算（只用于「这一次」三选一的刷新估计）
    probabilities: tuple[int, int, int] | None = None  # 界面上显示的蓝 / 紫 / 金概率
    left_rarity: str | None = None  # 这次三选一最左格的稀有度
    # 地图待识别时的候选地图：前瞻、刷新期望和剩余三选一次数按这些地图平均。为空时按全部现役地图平均
    map_candidates: tuple[str, ...] = ()
    # 队友（最多 3 名）：Teammate，或 dict(hero=角色名或 None, bot=是否电脑)。为空表示不知道队友，不按队友调分
    teammates: tuple[Teammate, ...] = ()
    mutators: tuple[str, ...] = ()  # 本局的畸变组件：只在推荐区提示，未计入推荐度
    tasks_done: int | None = None  # 本局已完成的筹码任务数；不知道时按已有筹码数推算
    # 本局难度：NORMAL（"normal"）为普通难度，稀有度概率表和进度上限按普通难度取；
    # None 为困难及以上（困难、噩梦、疯狂、极限的筹码概率相同，不区分）或尚未判定
    difficulty: str | None = None
    # 进度条上读到的进度上限（红叉所在的格）；读到时优先于地图资料里按难度查的值
    progress_cap: int | None = None
    # 进度条上当前所在的格（蓝色高亮）。一局在进度到达上限时结束，进度除了每轮 +1 还会因击倒等推进，
    # 读到时剩余的轮数按「进度上限 − 当前格」算，不按「进度上限 − 当前轮次」
    progress: int | None = None
    # 本局难度的档位（data.LEVEL_* 之一）和具体难度名（data.DIFFICULTY_NAMES 之一），None 为不知道。
    # 用于「难度」分项：噩梦及以上的摩托头盔-高级加分（difficulty 不是 NORMAL、档位为 LEVEL_NIGHTMARE 时），
    # 以及难度名是极限时的几个筹码加分（rules.toml [extreme]）；进度条读不出时，进度上限按难度名取那一难度的一列，
    # 难度名是极限时一局的筹码任务数取极限的（data.MapInfo.cap_for、tasks_for）。稀有度概率表只看 difficulty。
    # 旧日志里没有这两项时按 None，维持现状
    difficulty_level: str | None = None
    difficulty_name: str | None = None
    # 已有筹码里不经三选一拿到的个数（飞羽手环定期给、神秘宝藏的宝箱等），不含地图直接发放的（那些由 Chip.granted
    # 排除）：这些没有算作拿过一次三选一，剩余三选一次数不扣。旧记录里没有这一项时按 0
    direct: int = 0
    # 已拿循环往复、它给的额外那次三选一还没给（Session 按拿到它的轮次和来源记账）：剩余三选一次数另加 1 次
    # （engine.context.PicksLeft.endless）。在三选一里认出选了循环往复（含漏记补录时点的，按漏记的那次三选一的轮次和
    # 来源）、当时轮次读得出时才记；面板核对、手动加进来的不知道拿到的轮次，不记。旧记录里没有这一项时按没有
    endless_pending: bool = False
    # 循环往复给的额外那次三选一已经算进「已经有过的三选一」里的次数（0 或 1，Session 记账）：这一次就是认出的额外那次，
    # 或额外那次里选的筹码还在已有筹码里。额外那次不是任务和筹码商店的三选一，不占一局的那份：推算剩余三选一次数时，
    # 「已经有过几次任务和商店三选一」扣掉它（engine.context.Context._picks_left）。和 direct 分开记：那次是三选一里选的。
    # 旧记录里没有这一项时按 0
    endless_given: int = 0
    # 循环往复给的额外那次三选一到期的轮次（拿到它的那次三选一的轮次 + 按来源等的回合数，Session 记账；endless_pending 为
    # 真时才用）。到期不早于进度上限（那一轮打不到）时引擎不多算那 1 次（engine.context.Context._picks_left）；那次三选一的
    # 等级按到期时预计的星数（engine.context.endless_sources）。旧记录、冻结的局面里没有这一项时为 None，照原来算（多算
    # 1 次，等级按当前星数）
    endless_due: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "owned", tuple(self.owned))
        object.__setattr__(self, "map_candidates", tuple(self.map_candidates or ()))
        object.__setattr__(self, "teammates", tuple(_teammate(t) for t in (self.teammates or ())))
        object.__setattr__(self, "mutators", tuple(self.mutators or ()))
        if self.probabilities is not None:
            object.__setattr__(self, "probabilities", tuple(self.probabilities))


def owned_schools(db: DB, owned) -> tuple[str, ...]:
    """已持有筹码占了哪些流派（按拿到的先后）。资料库未收录的筹码不计。

    最多 max_schools 个：游戏里占满后其余流派移出筹码池，不可能再拿到第三个流派。已有筹码记错（识别有误、手动补录错）
    多出来的流派不算，否则推算「拿了之后」时那个流派会回到筹码池，前瞻失真。"""
    seen: list[str] = []
    for name in owned:
        chip = db.chips.get(name)
        school = chip.school if chip else None
        if school and school not in seen:
            seen.append(school)
    return tuple(seen[: db.rules["pool"]["max_schools"]])


def class_weights(db: DB, hero: Hero, owned=None) -> dict[str, float]:
    """升星和筹码商店最左格选职业筹码池的权重（slot_dists 用）。

    owned 为 None 时就是角色的初始职业分值（hero.weights），编辑窗口的「较难出现」（开局前的规划）用这个。
    给了已拿的筹码（可以为空）时按职业分值累加：初始职业分值 + 这些筹码的职业分值（rules.toml [pool]
    accumulate_class_points 为 false 时不累加，和不给时相同）。资料库未收录的筹码不计。"""
    weights = {cls: float(v) for cls, v in hero.weights.items()}
    if owned is None or not db.rules["pool"].get("accumulate_class_points", True):
        return weights
    for name in dict.fromkeys(owned):
        chip = db.chips.get(name)
        if chip is None:
            continue
        for cls, pts in chip.class_points.items():
            weights[cls] = weights.get(cls, 0.0) + pts
    return weights


def player_pool(db: DB, owned, map_name: str | None, exclude=()) -> list[Chip]:
    """这名玩家当前还能随机到的筹码。"""
    owned_set = set(owned)
    skip = set(exclude)
    schools = owned_schools(db, owned)
    locked = len(schools) >= db.rules["pool"]["max_schools"]
    pool = []
    for chip in db.chips.values():
        if chip.granted or chip.name in owned_set or chip.name in skip:
            continue
        if chip.maps and map_name not in chip.maps:
            continue
        if locked and chip.school and chip.school not in schools:
            continue
        pool.append(chip)
    return pool


def rarity_rows(db: DB, difficulty: str | None = None) -> list:
    """稀有度概率表（行号 = 星数 0~3，每行是蓝 / 紫 / 金的百分比）：普通难度用 [rarity] normal，
    困难及以上和尚未判定时用 [rarity] rows。"""
    rarity = db.rules["rarity"]
    if difficulty == NORMAL:
        return rarity.get("normal") or rarity["rows"]
    return rarity["rows"]


def difficulty_of(db: DB, probabilities) -> str | None:
    """三选一界面上的概率行是哪张表里的一行：普通难度的表为 NORMAL，困难及以上的表为 HARD，都不是时为 None。

    两张表没有相同的行（见 rules.toml [rarity]），一次三选一就能分辨。
    """
    if not probabilities:
        return None
    row = tuple(probabilities)
    rarity = db.rules["rarity"]
    for difficulty, key in ((NORMAL, "normal"), (HARD, "rows")):
        if any(tuple(r) == row for r in rarity.get(key, ())):
            return difficulty
    return None


# 最左格的出法：rules.toml [pool] 的 leftmost 管第一次刷出，reroll_leftmost 管刷新以后
REROLL_LEFT_RARITY = "rarity"  # 固定稀有度，从该稀有度的全部筹码（玩家筹码池）里按普通权重抽，不限职业筹码池
REROLL_LEFT_CLASS_POOL = "class_pool"  # 固定稀有度，按职业权重从职业筹码池（池表）出
# 固定稀有度，按职业权重选职业筹码池，但职业筹码池按筹码的职业标签建（tags 里属于职业的那几个就是它所在的
# 职业池），不按池表：带职业标签却不在池表里的筹码（磨刀石、电磁炮等）也可能出现
REROLL_LEFT_CLASS_TAG = "class_tag"
REROLL_LEFT_RANDOM = "random"  # 与其余两格一样随机，不固定稀有度
# 全部取值（载入 rules.toml 时也按它核对，所以放在 data 那一层）：就是上面这四个
REROLL_LEFT_MODES = LEFTMOST_MODES


def class_tags(chip: Chip) -> tuple[str, ...]:
    """筹码的职业标签：tags 里属于职业（data.CLASSES）的那几个，按标签的先后。最左格按 REROLL_LEFT_CLASS_TAG 出时，
    它们就是这个筹码所在的职业筹码池。"""
    return tuple(t for t in dict.fromkeys(chip.tags) if t in CLASSES)


def leftmost_mode(db: DB, reroll: bool = False) -> str:
    """最左格的出法：reroll=False（第一次刷出）读 rules.toml [pool] leftmost，reroll=True（刷新以后）读 reroll_leftmost。

    没有 leftmost 时按旧开关 leftmost_class_pool（true = 职业池，false = 随机），两个都没写时是 class_pool；
    刷新以后同理按旧开关 reroll_leftmost_class_pool。现在采用 REROLL_LEFT_CLASS_TAG：稀有度按星数固定，按职业权重选
    职业筹码池，池按筹码的职业标签建。依据见 rules.toml [pool] 的注释。
    """
    cfg = db.rules["pool"]
    if reroll:
        key, legacy = "reroll_leftmost", cfg.get("reroll_leftmost_class_pool", True)
    else:
        key, legacy = "leftmost", cfg.get("leftmost_class_pool", True)
    mode = cfg.get(key)
    if mode is None:
        return REROLL_LEFT_CLASS_POOL if legacy else REROLL_LEFT_RANDOM
    if mode not in REROLL_LEFT_MODES:
        raise ValueError(f"rules.toml [pool] {key} 应为 {'、'.join(REROLL_LEFT_MODES)} 之一（现为「{mode}」）")
    return mode


def reroll_leftmost_mode(db: DB) -> str:
    """刷新后最左格的出法（leftmost_mode(db, True)）。"""
    return leftmost_mode(db, True)


def rarity_row(db: DB, star: int, source: str, tier: int | None, reroll: bool = False,
               difficulty: str | None = None):
    """返回（蓝紫金的概率，最左格固定稀有度或 None）。reroll=True 表示刷新后刷出的三选一。
    difficulty 为 NORMAL 时用普通难度的概率表，否则用困难及以上的。"""
    star = max(0, min(3, star))
    rows = rarity_rows(db, difficulty)
    if source == "task":
        row = rows[max(1, min(3, tier if tier else star))]
        fixed = None
    else:
        row = rows[star]
        fixed = db.rules["rarity"]["fixed"][star]
        # 刷新后的最左格和第一次刷出时分开设：刷新后按 reroll_leftmost，第一次刷出按 leftmost
        if leftmost_mode(db, reroll) == REROLL_LEFT_RANDOM:
            fixed = None
    total = sum(row)
    return [p / total for p in row], fixed


def slot_dists(
    db: DB,
    hero: Hero,
    pool: list[Chip],
    schools: tuple[str, ...],
    star: int,
    source: str = "levelup",
    tier: int | None = None,
    probabilities: tuple[int, int, int] | None = None,
    left_rarity: str | None = None,
    reroll: bool = False,
    difficulty: str | None = None,
    class_owned=None,
) -> tuple[Dist | None, Dist]:
    """返回（最左格的分布，其余两格的分布）。任务筹码没有特殊的最左格，返回 None。

    probabilities / left_rarity 是从画面上读到的概率和最左格稀有度，给了就用它们。
    reroll=True 时按「刷新后刷出的三选一」算最左格（rules.toml [pool] reroll_leftmost），False 时按第一次刷出
    （[pool] leftmost）：现在都采用 class_tag，固定稀有度、按职业权重选职业筹码池，但池按筹码的职业标签建（class_tags），
    带职业标签却不在池表里的磨刀石等也可能出现；另有 rarity（不按职业权重，从该稀有度的全部筹码里按普通权重抽）、
    class_pool（按池表）、random。
    刷新的估值、自己的方案里「刷出排在前面的概率」、循环往复多出来的刷新、编辑方案的「较难出现」都经这里取刷新后的分布。
    difficulty 为 NORMAL 时按普通难度的概率表取稀有度概率（画面上读到的概率仍然优先）。
    class_owned：已拿的筹码。给了时最左格选职业筹码池的权重按职业分值累加（class_weights：初始职业分值 + 这些筹码的
    职业分值）；不给（None）时只按角色初始职业分值（编辑窗口的「较难出现」用不给的版本）。
    """
    probs, fixed = rarity_row(db, star, source, tier, reroll, difficulty)
    if probabilities and sum(probabilities) > 0:
        probs = [p / sum(probabilities) for p in probabilities]
    if left_rarity and fixed is not None:
        fixed = left_rarity
    boost = db.rules["pool"]["keyword_boost"]
    # 每个筹码的权重、按稀有度分好的桶只算一遍，不必每个稀有度各扫一遍整个池、最左格再按职业各扫一遍
    w = {c.name: (boost if c.school in schools else 1.0) for c in pool}
    by_rarity: dict[str, list[Chip]] = {}
    for c in pool:
        by_rarity.setdefault(c.rarity, []).append(c)

    general: Dist = {}
    for rarity, p in zip(RARITIES, probs):
        members = by_rarity.get(rarity, ())
        total = sum(w[c.name] for c in members)
        for c in members:
            general[c.name] = p * w[c.name] / total
    mass = sum(general.values())
    if mass:
        general = {n: p / mass for n, p in general.items()}

    if fixed is None:
        return None, general

    left: Dist = {}
    fixed_members = by_rarity.get(fixed, ())
    mode = leftmost_mode(db, reroll)
    if mode == REROLL_LEFT_RARITY:
        # 稀有度仍固定，但不按职业权重、不限职业筹码池（同流派加权照常）
        for c in fixed_members:
            left[c.name] = w[c.name]
    else:
        # 按职业权重选职业筹码池，池里按同流派加权抽。class_pool 的池按池表（Chip.pools）；
        # class_tag 的池按筹码的职业标签（class_tags）
        weights = class_weights(db, hero, class_owned)
        by_class: dict[str, list[Chip]] = {}
        for c in fixed_members:
            for cls in (class_tags(c) if mode == REROLL_LEFT_CLASS_TAG else dict.fromkeys(c.pools)):
                by_class.setdefault(cls, []).append(c)
        for cls in CLASSES:
            members = by_class.get(cls, ())
            total = sum(w[c.name] for c in members)
            for c in members:
                left[c.name] = left.get(c.name, 0.0) + weights.get(cls, 0.0) * w[c.name] / total
    mass = sum(left.values())
    if not mass:
        return None, general
    return {n: p / mass for n, p in left.items()}, general


def appear_prob(left: Dist | None, general: Dist, name: str) -> float:
    """某个筹码出现在一次三选一里的概率。"""
    p = general.get(name, 0.0)
    if left is None:
        return 1 - (1 - p) ** 3
    return 1 - (1 - left.get(name, 0.0)) * (1 - p) ** 2


def appear_prob_mix(dists, name: str) -> float:
    """来源不定的一次三选一里某个筹码出现的概率：dists 是（占比, 最左格的分布, 其余两格的分布）的序列，占比合计为 1
    （例如 3 星以后的任务和筹码商店）。各来源的出现率按占比加权；只有一种来源时就是 appear_prob。"""
    if len(dists) == 1:
        _, left, general = dists[0]
        return appear_prob(left, general, name)
    return sum(share * appear_prob(left, general, name) for share, left, general in dists)


def appear_any(left: Dist | None, general: Dist, names) -> float:
    """一次三选一里 names 中至少出现一个的概率。和 appear_prob 同一套出法，三格各自独立地出：最左格出到其中一个的概率 L 是
    这几个在最左格分布里的概率之和，其余两格每格出到其中一个的概率 G 是在其余两格分布里的概率之和（L、G 都夹在 0～1），
    一个都不出现的概率是 (1 − L)(1 − G)²；最左格没有固定稀有度（left 为 None）时三格都按其余两格的分布，为 (1 − G)³。
    不在分布里的（不在筹码池里）按 0 算。只有一个筹码时和 appear_prob 逐位相同；几个时不小于其中最大的一个，不大于各自
    出现率之和（也不超过 1）。names 要遍历两遍（其余两格、最左格），传进来的生成器先转成元组。"""
    names = tuple(names)
    g = min(1.0, max(0.0, sum(general.get(n, 0.0) for n in names)))
    if left is None:
        return 1 - (1 - g) ** 3
    lv = min(1.0, max(0.0, sum(left.get(n, 0.0) for n in names)))
    return 1 - (1 - lv) * (1 - g) ** 2


def appear_any_mix(dists, names) -> float:
    """来源不定的一次三选一里 names 中至少出现一个的概率（dists 同 appear_prob_mix）：一次三选一的三格出自同一种来源，
    各来源按占比加权；只有一种来源时就是 appear_any。只有一个筹码时和 appear_prob_mix 逐位相同。几种来源各算一遍 names，
    传进来的生成器先转成元组。"""
    names = tuple(names)
    if len(dists) == 1:
        _, left, general = dists[0]
        return appear_any(left, general, names)
    return sum(share * appear_any(left, general, names) for share, left, general in dists)


def _cdf_steps(left: Dist | None, general: Dist, value: dict[str, float]):
    """按分值从低到高，产出（分值，三格最高分 ≤ 该分值的概率）。"""
    names = sorted(set(general) | set(left or ()), key=lambda n: (value[n], n))
    f_left = f_gen = 0.0
    for n in names:
        f_gen += general.get(n, 0.0)
        if left is None:
            yield value[n], f_gen**3
        else:
            f_left += left.get(n, 0.0)
            yield value[n], f_left * f_gen * f_gen


def expected_best(left: Dist | None, general: Dist, value: dict[str, float]) -> float:
    """一次三选一里最高分的期望。"""
    total = prev = 0.0
    for v, g in _cdf_steps(left, general, value):
        total += v * (g - prev)
        prev = g
    return total


def expected_best_mix(dists, value: dict[str, float]) -> float:
    """来源不定的一次三选一里最高分的期望（dists 同 appear_prob_mix）。一次三选一的三格出自同一种来源，所以按占比加权
    各来源的期望，不把几种来源的分布先平均再算（那样等于三格各自换来源）。只有一种来源时就是 expected_best。"""
    if len(dists) == 1:
        _, left, general = dists[0]
        return expected_best(left, general, value)
    return sum(share * expected_best(left, general, value) for share, left, general in dists)


def expected_max_with(left: Dist | None, general: Dist, value: dict[str, float], floor: float) -> float:
    """一次三选一里 max(三格最高分, floor) 的期望。floor 为 -inf 时等于 expected_best。

    连续刷新的递推（engine.Scorer.reroll）用它：floor 是「不要这次刷出的筹码、继续刷新」的价值，
    这次刷出的最高分不如它时继续刷新。"""
    total = prev = 0.0
    for v, g in _cdf_steps(left, general, value):
        total += max(v, floor) * (g - prev)
        prev = g
    return total


def prob_above(left: Dist | None, general: Dist, value: dict[str, float], threshold: float) -> float:
    """一次三选一里至少出现一个分值高于 threshold 的筹码的概率。

    分值和 threshold 都按 tie_key 比：筹码池里有一张和比较对象分值相等的筹码时（例如两张筹码的分项完全相同），
    浮点求和的末位误差随哈希种子变化，直接比会时而把它算作「高于」、时而不算，概率在两次启动之间不同
    （随机局面里有在 49% 和 54% 之间变的）。相等的不算高于。"""
    line = tie_key(threshold)
    below = 0.0
    for v, g in _cdf_steps(left, general, value):
        if tie_key(v) > line:
            break
        below = g
    return 1 - below
