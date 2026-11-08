"""打分的各分项：一个筹码在给定的已有筹码、轮次、地图下的分项（Part）和总分（score），不含前瞻。

分项 = 适配分（玩家思路或自动层、组合、队友、难度、收入折算）+ 推荐流派分，另有不加分的职业倾向说明。
游戏内置推荐分不计入总分（official.scale 为 0）时连这几项也不列，公式留着只用来判断游戏的推荐角标。
各分项的先后由 _build_parts 定：玩家思路、循环往复、自动层三者只取其一；带「已持有」条件的规则不再叠加组合；
开套预判、美工刀按还没有治愈来源算的两项排在组合之后；方案模式不加推荐流派分；收入折算要看到前面所有分项；
职业倾向排在最后。
总分和收入折算的 base 都按这个先后求和，先后会影响浮点末位，调整时要连同基准一起核对。
之后选到某筹码的概率（美工刀、治愈来源、配套筹码三个预判）在 chances，这里只把概率折成分项。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..data import LEVEL_NIGHTMARE, NORMAL, TIER_LABEL, TIERS, Chip, Rule
from ..pool import expected_best, expected_max_with, owned_schools, player_pool
from .advice_text import (SCHEME_TIER_WORD, SHORT_LIMIT, chance_text, fill_mates_short, free_short, mates_short,
                          origin_short, origin_text, shown_int)
from .chances import (HEAL_ADVANCE_ORIGIN, HEAL_ADVANCE_REASON, HEAL_ADVANCE_SHORT, HEAL_DISCOUNT_ORIGIN,
                      HEAL_DISCOUNT_REASON, HEAL_DISCOUNT_SHORT, has_heal_source, heal_chance, heal_knife, knife_chance,
                      knife_school_bonus, layer_value, partner_chance)
from .context import (harmonic, hero_classes, keep_count, next_sources, pool_and_dists, reroll_keep,
                      reward_rounds_left, task_tiers)

if TYPE_CHECKING:
    from .scorer import Scorer

# 噩梦及以上难度时 rules.toml [survival] nightmare_chips（摩托头盔-高级）的「难度」分项：整句写机制，不写特性名
NIGHTMARE_REASON = "噩梦及以上难度怪物数值更高，角色被击倒使进度 +1 的代价更大，高级防御力加成对所有角色收益都较高"
NIGHTMARE_SHORT = "难度：噩梦及以上"
# 难度不知道（State.difficulty 不是普通、difficulty_level 为 None：推荐按困难及以上计算，识别卡片不显示难度胶囊）时，
# 生存类筹码「难度：生存类」那一项的整句前面加这一句（只改这条标签的悬停说明，短句不变），
# 后半句的主语由「困难及以上难度」改写成 UNKNOWN_DIFFICULTY_SUBJECT（「困难及以上」不连着出现两次）
UNKNOWN_DIFFICULTY_LEAD = "难度待识别，按困难及以上计算："
UNKNOWN_DIFFICULTY_SUBJECT = "这几档难度"
OFFICIAL_LABELS = ("基础分", "成长分", "角色分", "标签分", "关键词分")
# 收入折算的整句里，已持有 rules.toml [income] exempt_owned 的筹码（金色飞星）时接的一句（悬停里写明原因）
EXEMPT_OWNED_NOTE = "已持有「{chip}」，{why}，折算幅度减半"
# 收入折算的整句里，较晚获得的银行卡（rules.toml [income] late）接的一句：{round} 是从第几轮起。经济型角色（[income]
# economy_trait）不再减半，也不接这一句
LATE_INCOME_NOTE = "第 {round} 轮起获得，星币兑现较晚，收入再减半"
# 难度名是极限时（rules.toml [extreme]）「难度」分项的短句；整句按筹码写在 rules.toml [extreme] reasons
EXTREME_SHORT = "难度：极限"
# 要尽量保住推荐流派（适配表 [[hero]] 的 keep_schools、keep_schools_penalty，rules.toml [synergy] keep_schools）：选择后
# 剩下的流派名额少于还没进入的要保的流派个数时扣分，分项名「推荐流派」。和自己方案里「选择后……只能再进入一个」同一种说法：
# 还剩名额时写「只能再进入」几个（KEEP_SCHOOLS_LEFT_*），名额占满时写推荐流派的筹码本局不再出现（KEEP_SCHOOLS_GONE_*）。
# {enter} 是这个筹码的流派；整句里的 {schools} 是还没进入的要保的流派（每个各加一对「」），短句里的 {names} 用「、」连、
# 不加「」；{n} 是剩下的流派名额（「一」「两」，KEEP_SCHOOLS_COUNT）
KEEP_SCHOOLS_LEFT_REASON = "选择后进入「{enter}」流派，只剩{n}个流派名额：推荐流派{schools}只能再进入{n}个"
KEEP_SCHOOLS_LEFT_SHORT = "选择后{names}只能再进入{n}个"
KEEP_SCHOOLS_GONE_REASON = "选择后进入「{enter}」流派，流派名额占满：推荐流派{schools}的筹码本局不再出现"
KEEP_SCHOOLS_GONE_SHORT = "选择后{names}流派不再出现"
KEEP_SCHOOLS_COUNT = {1: "一", 2: "两", 3: "三"}
# 要尽量保住推荐流派的角色已进入推荐流派以外的流派、要保的流派本局已经进不全时，已进入的那个流派按推荐流派加分
# （_adopted_schools），分项名同样是「推荐流派」、分值 [synergy] school。{school} 是已进入的其他流派；{schools}、{n} 同上：
# 还剩名额时写要保的流派「只能再进入」几个，名额占满时写没进入的要保的流派的筹码本局不再出现
ADOPTED_LEFT_REASON = "已进入「{school}」流派，推荐流派{schools}只能再进入{n}个：「{school}」流派的筹码同样按推荐流派计算"
ADOPTED_GONE_REASON = "已进入「{school}」流派，推荐流派{schools}的筹码本局不再出现：「{school}」流派的筹码同样按推荐流派计算"
ADOPTED_SHORT = "推荐流派：{school}（已进入）"
SAME_MAP = object()  # parts() / score() 的 map_name 默认值：用 State.map
ENDLESS_ORIGIN = "endless_cycle"  # 循环往复那一项的 Part.origin（前瞻按假设的局面重算这一项，见 endless_value）

# 筹码方案（用户在悬浮窗里建的方案，advise 的 scheme；分值在 rules.toml [scheme]）。选「默认推荐」时一项都不用。
# 方案的四档和默认推荐的四档用同一套键（data.TIERS）和叫法（data.TIER_LABEL，advice_text.SCHEME_TIER_WORD）
SCHEME_TIER_KEY = {"core": "must", "first": "recommend", "second": "optional", "avoid": "skip"}  # rules.toml [scheme] 的键
# 方案带来的分项（Part.label，内部的名字；界面上的分项名由 ui.text 换写）：档位、目标流派、锁定保护、被锁的流派
SCHEME_LABEL = "方案"
SCHEME_ORIGIN = "scheme_{tier}"  # 档位那一项的 Part.origin（「scheme_core」是必选：收入折算按必选处理）
SCHEME_CORE_ORIGIN = SCHEME_ORIGIN.format(tier="core")
SCHEME_TIER_ORIGINS = frozenset(SCHEME_ORIGIN.format(tier=t) for t in TIERS)
# 目标流派倾向、锁定保护、被锁成不选的流派（后两项只是内部的分值，整句为空，界面上不显示）
SCHEME_TARGET_ORIGIN, SCHEME_LOCK_ORIGIN, SCHEME_LOCKED_ORIGIN = "scheme_target", "scheme_lock", "scheme_locked"
# 档位那一项的短句和整句（整句只在悬停明细里，推荐卡片上用位次代替档位标签）、目标流派倾向的标签和悬停整句。
# {name} 是方案名，{tier} 是档位（SCHEME_TIER_WORD），{school} 是一个流派名
SCHEME_TIER_SHORT = "筹码方案：{tier}"
SCHEME_TIER_REASON = "筹码方案「{name}」：{tier}"
SCHEME_TARGET_SHORT = "筹码方案：{school}流派"
SCHEME_TARGET_REASON = "「{school}」是筹码方案「{name}」的目标流派"
# 默认推荐（data/synergy.toml 的 [[player]]，分项名「玩家思路」是内部的名字）：四档的标签和悬停整句写法和自己的方案相同，
# 方案名写 DEFAULT_SCHEME（资料里规则的整句以 advice_text.PLAYER_HEAD 开头；必选补分见 advice.lift_core）
DEFAULT_SCHEME = "默认推荐"


@dataclass
class Part:
    """总分里的一项。reason 为空的项不出现在理由里；score 为 0 的项只作说明（例如职业倾向），不写分值。

    label 是分项的名称，可以直接显示（不含特性名）：基础分、成长分、角色分、标签分、关键词分、玩家思路、
    角色专属规则、特性、其他特性、推荐流派、组合、队友、难度、收入折算、循环往复、职业倾向、未收录。
    origin 是内部记录（规则来自哪个特性、哪条组合规则、玩家思路的来源），不显示。
    short 是推荐卡片理由标签上的短句（例如「收入剩余 14 轮」）；
    为空时界面按 reason 的长度决定：不超过 SHORT_LIMIT 个字照写，超过时写分项名。整句 reason 不受影响（悬停、命令行、诊断包照用）。
    悬浮窗上不写分值，分值只出现在命令行和诊断包里。
    net 是净效果的短句（适配表扣分规则的 net，_auto_parts）：同一筹码的一条加分、一条扣分说的是同一个效果时，两项记同一个
    net，推荐卡片上合成一个理由标签（ui.text.reason_pills）；分值、悬停明细、命令行和诊断包里仍是两项。
    """

    label: str
    score: float
    reason: str = ""
    origin: str = ""
    short: str = ""
    net: str = ""

    def text(self) -> str:
        """理由的显示文字：「理由（+40）」；分值为 0 时不写分值。"""
        if abs(self.score) < 0.5:
            return self.reason
        return f"{self.reason}（{self.score:+.0f}）"


# ------------------------------------------------------------ 总分和分项


def score(sc: Scorer, name: str, owned, rnd: int | None, map_name=SAME_MAP) -> float:
    """总分（不含前瞻）：各分项按 _build_parts 的先后相加。按（筹码, 已有筹码的集合, 轮次, 地图）缓存（Memo.score）。"""
    if map_name is SAME_MAP:
        map_name = sc.st.map
    key = (name, frozenset(owned), rnd, map_name)
    if key not in sc.memo.score:
        sc.memo.score[key] = sum(p.score for p in parts(sc, name, owned, rnd, map_name))
    return sc.memo.score[key]


def pool_scores(sc: Scorer, owned, rnd: int | None, map_name, pool) -> dict[str, float]:
    """筹码池里每个筹码的总分表（不含前瞻）：一张地图、一个轮次、一组已有筹码只算一次，调用方按自己的池取用。

    一个筹码的总分只依赖（筹码, 已有筹码, 轮次, 地图），与「这个池子里有哪些筹码」无关，所以同一组已有筹码下更大的
    池子（已有筹码更少、假设拿到的筹码更少时的筹码池）算出来的分值同样可用：前瞻给「拿了某张之后」的筹码池取分值
    就从这一张表里取，不必对池里每个筹码再调一次 score。表按需填（只算这个池子里有的筹码），不预先算整张池子，
    免得把任何假设局面都用不到的筹码也算一遍。键带上地图、轮次和已有筹码，不同的假设局面不会互相借结果。
    """
    key = (map_name, rnd, owned if isinstance(owned, tuple) else tuple(owned))
    table = sc.memo.values.get(key)
    if table is None:
        table = sc.memo.values[key] = {}
    out = {}
    for c in pool:
        name = c.name
        value = table.get(name)
        if value is None:
            value = score(sc, name, owned, rnd, map_name)
            table[name] = value
        out[name] = value
    return out


def parts(sc: Scorer, name: str, owned, rnd: int | None, map_name=SAME_MAP) -> list[Part]:
    """全部分项（按 _build_parts 的先后），取用时复制一份。按（筹码, 已有筹码的集合, 轮次, 地图）缓存（Memo.parts）。"""
    if map_name is SAME_MAP:
        map_name = sc.st.map
    key = (name, frozenset(owned), rnd, map_name)
    if key not in sc.memo.parts:
        # 按集合缓存，计算也只看集合：排成确定的顺序（Context.ordered），不用调用方传进来的顺序
        sc.memo.parts[key] = _build_parts(sc, name, sc.ordered(owned), rnd, map_name)
    return list(sc.memo.parts[key])


def _build_parts(sc: Scorer, name: str, owned, rnd: int | None, map_name) -> list[Part]:
    db, chip = sc.db, sc.db.chips[name]
    owned = tuple(n for n in owned if n in db.chips)
    owned_set = frozenset(owned)
    s = db.rules["synergy"]
    # 内置分不计入总分时（[official] scale 为 0）不列这几项：分值都是 0，玩家会看到不再起作用的理由。
    # 游戏推荐角标另按原值判断（official_raw），和这里无关。
    scaled = db.rules["official"].get("scale", 1.0) != 0
    out = official_parts(sc, name, owned, rnd) if scaled else []
    tendency = _tendency_part(sc, chip, out) if scaled else None
    layer: list[Part] = []

    player, cond = _player_match(sc, name, owned_set, rnd, map_name)
    heal: list[Part] = []  # 美工刀按「还没有治愈来源」算的两项（_heal_parts），排在组合规则之后
    if player:
        layer.append(player)
    elif name == db.rules["endless_cycle"]["chip"]:
        layer.extend(_endless_parts(sc, owned, rnd, map_name))
    else:
        auto, heal = _auto_parts(sc, name, owned_set, rnd, map_name)
        layer.extend(auto)
    if not cond:  # 玩家思路里带「已持有」「有来源」条件的规则已经考虑了组合关系，不再叠加组合规则
        layer.extend(_combo_parts(sc, name, owned_set, map_name))
        school = _school_anticipation(sc, name, owned_set, map_name)
        if school:
            layer.append(school)
    layer.extend(heal)
    layer.extend(teammate_parts(sc, name))
    layer.extend(_survival_parts(sc, name, map_name))
    layer.extend(_extreme_parts(sc, name))
    if sc.plan is not None:
        # 筹码方案：推荐流派（默认推荐的一部分）不加分；没标的筹码另有目标流派倾向、锁定保护、被锁的流派按不选
        layer.extend(_scheme_school_parts(sc, name, owned))
    else:
        if chip.school and chip.school in sc.pref_schools:
            layer.append(Part("推荐流派", s["school"], f"「{chip.school}」是该角色的推荐流派",
                              short=f"推荐流派：{chip.school}"))
        elif chip.school and chip.school in _adopted_schools(sc, owned):
            layer.append(_adopted_part(sc, chip.school, owned))
        # 要保住的流派以外的筹码（只保一部分推荐流派时，没列出的推荐流派也算）：选择后会让要保的流派进不全时扣分
        keep = _keep_schools_part(sc, name, owned)
        if keep is not None:
            layer.append(keep)
    out.extend(layer)
    income = income_part(sc, chip, out, rnd, owned_set)
    if income:
        out.append(income)
    if tendency:
        out.append(tendency)
    return out


def neutral_score(sc: Scorer, rarity: str | None, owned, rnd: int | None) -> float:
    """资料库未收录的筹码的中性分：筹码池里同稀有度筹码（不知道稀有度时为全部筹码）的平均分，不含前瞻。"""
    values = []
    for m in sc.maps:
        pool = player_pool(sc.db, owned, m)
        same = [c for c in pool if not rarity or c.rarity == rarity] or pool
        values.extend(score(sc, c.name, owned, rnd, m) for c in same)
    return sum(values) / len(values) if values else 0.0


# ------------------------------------------------------------ 游戏内置推荐分


def official_parts(sc: Scorer, name: str, owned, rnd: int | None, scale: float | None = None) -> list[Part]:
    """游戏内置推荐分的各项。scale 为 None 时乘 rules.toml 的 official.scale；传 1 得到游戏公式的原值。
    标签分（职业）和关键词分（流派）都按持有 owned 时算：推算「拿了以后」（owned 里有假设拿到的筹码）时，职业也按
    拿了以后的职业分值（_classes_with），和游戏到那时打角标的算法一致。
    同游戏的算法：适配和普通适配两张名单分别算、都在时两项相加；关键词分只算 official.keyword_schools
    里的流派（治愈、星光、标记），充能流派的筹码不管已持有几张充能筹码都没有这一项。"""
    db, chip = sc.db, sc.db.chips[name]
    o = db.rules["official"]
    k = o.get("scale", 1.0) if scale is None else scale
    out = [Part("基础分", chip.base * k)]
    if rnd is not None and chip.growth_round is not None and rnd <= chip.growth_round:
        out.append(Part("成长分", o["growth"] * k, f"当前第 {rnd} 轮，未超过该筹码的成长轮次（第 {chip.growth_round} 轮）",
                        short=f"成长分：至第 {chip.growth_round} 轮"))
    if sc.hero.name in chip.fit:
        out.append(Part("角色分", o["fit"] * k, "游戏内置推荐将其列为该角色的适配筹码", short="角色分：适配"))
    if sc.hero.name in chip.fit_minor:
        out.append(Part("角色分", o["fit_minor"] * k, "游戏内置推荐将其列为该角色的普通适配筹码",
                        short="角色分：普通适配"))
    classes = _classes_with(sc, owned)
    matched = [t for t in chip.tags if t in classes]
    if matched:
        # ui.text.tag_pill 从这句里找职业名，句中必须保留职业名
        out.append(Part("标签分", o["tag"] * k, f"筹码标签「{matched[0]}」与角色当前职业相符"))
    if chip.school in o["keyword_schools"] and chip.school in owned_schools(db, owned):
        out.append(Part("关键词分", o["keyword"] * k, f"已持有「{chip.school}」流派的筹码", short=f"已持有{chip.school}流派"))
    return out


def _classes_with(sc: Scorer, owned) -> frozenset[str]:
    """持有 owned 时角色的职业（hero_classes）：和现在的已有筹码是同一组时就是 sc.classes；推算「拿了以后」时
    （选了美工刀以后获得治愈来源的概率、开套预判里获得配套筹码的概率，都按「已有筹码 + 这一张」给之后的候选打分；
    之后获得美工刀的概率按「已有筹码 + 正在评估的这个治愈来源」给美工刀打分，筹码池不含这一张，见 chances.knife_chance）
    按假设的已有筹码重算。"""
    if frozenset(owned) == frozenset(sc.known_owned):
        return sc.classes
    return hero_classes(sc.db, sc.st.hero, tuple(dict.fromkeys(n for n in owned if n in sc.db.chips)))


def official_raw(sc: Scorer, name: str, owned, rnd: int | None) -> float:
    """游戏「选择筹码推荐」公式的原值（≥ official.recommend 时游戏显示推荐角标）。"""
    return sum(p.score for p in official_parts(sc, name, owned, rnd, scale=1.0))


def _tendency_part(sc: Scorer, chip: Chip, official: list[Part]) -> Part | None:
    """职业分值都没达到门槛时：筹码标签与职业倾向相符，加一条不加分的说明（不少角色开局没有职业达到门槛，靠倾向才写得出职业方面的理由）。"""
    if sc.hero.unrecorded or sc.classes or any(p.label == "标签分" for p in official):
        return None
    names, value = sc.tendency
    match = [t for t in chip.tags if t in names.split("、")]
    if not match:
        return None
    line = sc.db.rules["official"]["class_threshold"]
    return Part(
        "职业倾向", 0.0,
        f"筹码标签「{match[0]}」与角色的职业倾向（{match[0]} {value}）相符；职业分值未达到 {line}，暂不加分",
        short=f"职业倾向：{match[0]}（暂不加分）",
    )


# ------------------------------------------------------------ 推荐流派


def _keep_schools_part(sc: Scorer, name: str, owned: tuple) -> Part | None:
    """默认推荐里要尽量保住推荐流派（角色自动层的 keep_schools，要保的流派见 Context.kept_schools）时，要保的流派以外的
    筹码的扣分（分项名「推荐流派」，分值 Context.keep_penalty：角色的 keep_schools_penalty，没写时 rules.toml [synergy]
    keep_schools）；不扣分时为 None。
    筹码属于还没进入的其他流派，选择后剩下的流派名额（[pool] max_schools）少于还没进入的要保的流派个数时扣分：
    要保的流派本局再也进不全。只保一部分推荐流派时，没列出的推荐流派也算其他流派（它的筹码照旧加推荐流派的分，
    选择后会让要保的流派进不全时另扣这一项）。不属于流派的筹码、属于已进入流派的筹码不扣；流派名额早已占满时这个筹码
    本不该出现（手工输入的状态），也不扣。owned 是计算这一项时的已有筹码（_build_parts 的 owned）。前瞻和刷新的估计给
    之后的候选计分时同样传现在的已有筹码（known_owned，见 lookahead.future_setup、reroll.reroll_dists），不按选择这个
    筹码之后的已有筹码判断这一项。"""
    kept = sc.kept_schools
    if not kept:
        return None
    db = sc.db
    school = db.chips[name].school
    if not school or school in kept:
        return None
    before = owned_schools(db, owned)
    limit = db.rules["pool"]["max_schools"]
    if school in before or len(before) >= limit:
        return None
    missing = [s for s in kept if s not in before]
    left = limit - len(owned_schools(db, (*owned, name)))
    if left >= len(missing):
        return None
    quoted = "".join(f"「{s}」" for s in missing)
    names = "、".join(missing)
    if left > 0:
        n = KEEP_SCHOOLS_COUNT.get(left, f" {left} ")
        reason = KEEP_SCHOOLS_LEFT_REASON.format(enter=school, schools=quoted, n=n)
        short = KEEP_SCHOOLS_LEFT_SHORT.format(names=names, n=n)
    else:
        reason = KEEP_SCHOOLS_GONE_REASON.format(enter=school, schools=quoted)
        short = KEEP_SCHOOLS_GONE_SHORT.format(names=names)
    return Part("推荐流派", sc.keep_penalty, reason, short=short)


def _adopted_schools(sc: Scorer, owned: tuple) -> tuple[str, ...]:
    """要尽量保住推荐流派的角色（keep_schools），已进入推荐流派以外的流派、要保的流派本局已经进不全时，
    按推荐流派加分的已进入流派（默认推荐；其余情况为空）。
    之后的打法是「这个流派 + 还进得了的要保的流派」，再拿这个流派的筹码不会让要保的流派更进不全，和推荐流派一样加分
    （_adopted_part）。还没进入的其他流派照旧按 _keep_schools_part 扣分。流派名额都被其他流派占满、要保的流派一个也
    进不了时，已经没有要保的流派，不加。
    只保一个流派时不会有这一项：它进不全就是流派名额都被别的流派占满、它进不去。这时已进入的其他流派不加分，
    和不保的角色一样；要保的流派还进得了时，已进入的其他流派同样不加，要保的流派照旧加推荐流派的分，
    选择后会让它进不去的筹码扣分。owned 同 _keep_schools_part。"""
    kept = sc.kept_schools
    if sc.plan is not None or not kept:
        return ()
    before = owned_schools(sc.db, owned)
    others = tuple(s for s in before if s not in sc.pref_schools)
    if not others:
        return ()
    missing = [s for s in kept if s not in before]
    left = sc.db.rules["pool"]["max_schools"] - len(before)
    if left >= len(missing):  # 要保的流派还进得全
        return ()
    if left <= 0 and len(missing) == len(kept):  # 要保的流派一个也进不了
        return ()
    return others


def _adopted_part(sc: Scorer, school: str, owned: tuple) -> Part:
    """_adopted_schools 里的流派的筹码：分项名「推荐流派」，分值同推荐流派的加分（[synergy] school）。"""
    db = sc.db
    before = owned_schools(db, owned)
    missing = [s for s in sc.kept_schools if s not in before]
    left = db.rules["pool"]["max_schools"] - len(before)
    quoted = "".join(f"「{s}」" for s in missing)
    if left > 0:
        reason = ADOPTED_LEFT_REASON.format(school=school, schools=quoted, n=KEEP_SCHOOLS_COUNT.get(left, f" {left} "))
    else:
        reason = ADOPTED_GONE_REASON.format(school=school, schools=quoted)
    return Part("推荐流派", db.rules["synergy"]["school"], reason, short=ADOPTED_SHORT.format(school=school))


# ------------------------------------------------------------ 玩家思路和筹码方案


def player_score(sc: Scorer, name: str, owned, rnd: int | None) -> float | None:
    """玩家思路给这个筹码的分（规则或四档）；玩家思路没列入、或角色没有玩家思路时为 None。
    按筹码方案计算时是方案档位那一项的分（没标的为 None）。"""
    part, _ = _player_match(sc, name, frozenset(n for n in owned if n in sc.db.chips), rnd, sc.st.map)
    return part.score if part else None


def _player_match(sc: Scorer, name: str, owned_set: frozenset, rnd: int | None, map_name) -> tuple[Part | None, bool]:
    """返回（玩家思路的分项，是否来自带「已持有」条件的规则）。

    按筹码方案计算时（sc.plan）：方案标了档位的筹码，档位那一项代替玩家思路（同样代替角色特性，见 _build_parts）；
    没标的筹码不用默认推荐的任何部分（四档、带条件的规则都不用，自己的方案和默认推荐互斥：
    作者没标的筹码，默认推荐不能替作者推出去），由 _build_parts 按角色特性算。"""
    if sc.plan is not None:
        tier = sc.marks.get(name)
        return (_scheme_part(sc, tier) if tier is not None else None), False
    return default_player_match(sc, name, owned_set, rnd, map_name)


def _scheme_part(sc: Scorer, tier: str) -> Part:
    """方案的档位那一项：分值取 rules.toml [scheme]，短句「筹码方案：必选」，整句「筹码方案「治愈坦克」：必选」。
    推荐卡片上用位次代替这一项的标签（ui.text 按 SCHEME_TIER_ORIGINS 跳过），整句只在悬停明细里。"""
    word = SCHEME_TIER_WORD[tier]
    return Part(SCHEME_LABEL, sc.db.rules["scheme"][SCHEME_TIER_KEY[tier]],
                SCHEME_TIER_REASON.format(name=sc.plan.name, tier=word), SCHEME_ORIGIN.format(tier=tier),
                SCHEME_TIER_SHORT.format(tier=word))


def _scheme_school_parts(sc: Scorer, name: str, owned: tuple) -> list[Part]:
    """按筹码方案计算时，没标档位的筹码和流派有关的分项（代替推荐流派加分），分值取 rules.toml [scheme]：

    - 属于被锁成不选的流派（方案锁住、按 owned 还没进入）：按不选计分 skip（SCHEME_LOCKED_ORIGIN，内部的分值，
      整句为空）；先后另由 scheme_rank.advise_scheme 排在标了不选的筹码之后。方案外的流派进入以后它的筹码改按未标记算，
      不再有这一项；
    - 属于目标流派：目标流派倾向 target（标签「筹码方案：X流派」）；
    - 属于还没进入的其他流派、选了会占满流派名额（[pool] max_schools）、让某个目标流派本局再也出不来：锁定保护 lock
      （内部的扣分，整句为空；卡片上由「选了之后拿不到……」的提醒说明，见 scheme_rank.loss）。
    标了档位的筹码按档位排先后，这几项都不加。owned 是计算这一项时的已有筹码（_build_parts 的 owned）。"""
    db, plan = sc.db, sc.plan
    cfg = db.rules["scheme"]
    school = db.chips[name].school
    if not school or name in sc.marks:
        return []
    before = owned_schools(db, owned)
    if school in sc.scheme_locked and school not in before:
        return [Part(SCHEME_LABEL, cfg[SCHEME_TIER_KEY["avoid"]], "", SCHEME_LOCKED_ORIGIN)]
    if school in sc.targets:
        return [Part(SCHEME_LABEL, cfg["target"], SCHEME_TARGET_REASON.format(school=school, name=plan.name),
                     SCHEME_TARGET_ORIGIN, SCHEME_TARGET_SHORT.format(school=school))]
    limit = db.rules["pool"]["max_schools"]
    if school in before or len(before) >= limit:
        return []  # 已进入的流派；或流派名额早已占满：这个筹码本不该出现（手工输入的状态），选它也不改变流派
    after = owned_schools(db, (*owned, name))
    if len(after) < limit or all(t in after for t in sc.targets):
        return []
    return [Part(SCHEME_LABEL, cfg["lock"], "", SCHEME_LOCK_ORIGIN)]


def default_player_match(sc: Scorer, name: str, owned_set: frozenset, rnd: int | None,
                         map_name) -> tuple[Part | None, bool]:
    """默认推荐的玩家思路：返回（玩家思路的分项，是否来自带「已持有」（needs_owned）或「有来源」（needs_source）条件的规则）。

    四档的标签和自己的方案同一种写法「筹码方案：推荐」，悬停整句「筹码方案「默认推荐」：推荐」；必选的整句是该角色
    玩家思路的原文（[[player]] 的 note），没有原文时为「筹码方案「默认推荐」：必选」。"""
    if not sc.ps:
        return None, False
    rules = sc.player_rules.get(name, ())
    held = sc.held_sources(owned_set) if rules else frozenset()
    for rule in rules:
        if rule.applies(map_name, owned_set, rnd, sc.st.difficulty, held):
            return (Part("玩家思路", rule.score, rule.reason, rule.source,
                         short=rule.short or free_short(sc.db, rule.reason)),
                    bool(rule.needs_owned or rule.needs_source))
    tier = sc.ps.tiers.get(name)
    if tier:
        source = sc.ps.tier_source.get(name, "")
        word = TIER_LABEL[tier]
        label = SCHEME_TIER_SHORT.format(tier=word)
        sentence = SCHEME_TIER_REASON.format(name=DEFAULT_SCHEME, tier=word)
        reason = (sc.ps.note or sentence) if tier == "core" else sentence
        return Part("玩家思路", sc.db.rules["synergy"][tier], reason, source, short=label), False
    return None, False


# ------------------------------------------------------------ 自动层（角色特性）和组合


def _origin_label(rule: Rule) -> str:
    """自动层分项的名称：角色个别规则为「角色专属规则」，特性规则为「特性」（特性名记在 Part.origin，不显示）。"""
    return "角色专属规则" if rule.origin == "角色" else "特性"


def _auto_parts(sc: Scorer, name: str, owned_set: frozenset, rnd: int | None,
                map_name) -> tuple[list[Part], list[Part]]:
    """自动层的分项：加分的规则取最高的一条，其余按 extra_rule_factor 合成「其他特性」；扣分的规则各列一项。
    返回（自动层的分项，美工刀按「还没有治愈来源」算的分项）：后者由 _build_parts 排在组合规则之后。

    净效果：扣分规则写了 net、其中有最高那条加分规则的来源时，这条扣分说的是那条加分里的同一个效果，
    两项记同一个 Part.net（净效果的短句），推荐卡片上合成一个标签（ui.text.reason_pills）。一条加分规则只和一条扣分合成
    （几条扣分都写了时取靠前的）；加分只在「其他特性」里时不合成（卡片上不会单独出现那条加分的短句）。"""
    s = sc.db.rules["synergy"]
    held = sc.held_sources(owned_set)  # 体系来源（needs_source、without_source 的条件）
    hits = [r for r in sc.auto_rules.get(name, ()) if r.applies(map_name, owned_set, rnd, sc.st.difficulty, held)]
    positive = sorted((r for r in hits if r.score > 0), key=lambda r: -r.score)
    out: list[Part] = []
    top = None
    shares: dict[int, float] = {}  # 每条加分规则实际计入的分（取最高的一条全额，其余按比例分摊「其他特性」）
    if positive:
        top, rest = positive[0], positive[1:]
        out.append(Part(_origin_label(top), top.score, top.reason, top.origin,
                        short=top.short or free_short(sc.db, top.reason, top.origin)))
        shares[id(top)] = top.score
        extra = min(s["extra_rule_factor"] * sum(r.score for r in rest), s["cap"] - top.score)
        if rest and extra > 0:
            names = "；".join(dict.fromkeys(origin_text(sc.db, r) for r in rest))
            reason = f"另符合：{names}"
            points = list(dict.fromkeys(origin_short(sc.db, r) for r in rest))
            short = f"另符合：{points[0]}{'等' if len(points) > 1 else ''}"
            out.append(Part("其他特性", round(extra), reason, "、".join(r.origin for r in rest),
                            short=short if len(short) <= SHORT_LIMIT else free_short(sc.db, reason)))
            total = sum(r.score for r in rest)
            for r in rest:
                shares[id(r)] = round(extra) * r.score / total
    for r in hits:
        if r.score >= 0:
            continue
        part = Part(_origin_label(r), r.score, r.reason, r.origin, short=r.short or free_short(sc.db, r.reason, r.origin))
        net = r.net.get(top.origin) if top is not None else None
        if net and not out[0].net:
            out[0].net = part.net = net
        out.append(part)
    return out, _heal_parts(sc, name, owned_set, map_name, positive, shares)


def _heal_parts(sc: Scorer, name: str, owned_set: frozenset, map_name, positive, shares: dict) -> list[Part]:
    """美工刀还没有治愈来源时的两项：提前加分（_heal_advance）和折算（_heal_discount）。
    条件：[heal_knife] traits 那条特性给这把美工刀实际计入了分，没有治愈来源（chances.has_heal_source），不带 exclude_traits。"""
    cfg = heal_knife(sc)
    if cfg is None or name not in cfg["knives"] or set(cfg.get("exclude_traits", ())) & set(sc.traits):
        return []
    rule = next((r for r in positive if r.origin in cfg["traits"]), None)
    counted = shares.get(id(rule), 0.0) if rule is not None else 0.0
    if counted <= 0 or has_heal_source(sc, owned_set, cfg):
        return []
    parts = (_heal_advance(sc, name, owned_set, map_name, cfg),
             _heal_discount(sc, name, owned_set, map_name, counted))
    return [p for p in parts if p is not None]


def _about_chance(p: float) -> str:
    """概率在整句里的写法：「约 78%」；取整后为 0、100 时用 chance_text（「不到 1%」「超过 99%」）。"""
    shown = shown_int(p)
    return f"约 {shown}%" if 0 < shown < 100 else chance_text(p)


def _heal_advance(sc: Scorer, name: str, owned_set: frozenset, map_name, cfg: dict) -> Part | None:
    """还没有治愈来源时，预支之后再获得治愈来源才有的组合分：之后获得治愈来源的概率 × 以这把美工刀为「已持有」一方的
    组合规则（[heal_knife] combos，适用这个角色的）里最高的组合分 × [school_build] factor。
    和开套预判同一种结构：概率见 chances.heal_chance，系数的依据见 rules.toml。"""
    build = sc.db.rules.get("school_build")
    values = [c.score for c in sc.db.combos
              if c.name in cfg["combos"] and name in c.owned and c.applies_to(sc.hero.name, sc.traits)]
    if not build or not values:
        return None
    p = heal_chance(sc, name, owned_set, map_name)
    score = p * max(values) * build["factor"]
    if score < 0.5:
        return None
    return Part("组合", score, HEAL_ADVANCE_REASON.format(p=_about_chance(p)), HEAL_ADVANCE_ORIGIN,
                HEAL_ADVANCE_SHORT.format(p=chance_text(p)))


def _heal_discount(sc: Scorer, name: str, owned_set: frozenset, map_name, counted: float) -> Part | None:
    """还没有治愈来源时，[heal_knife] traits 那条特性给美工刀的加分按「之后获得治愈来源的概率」折算：
    少算的部分 = 这条规则实际计入的分 ×（1 − 概率），记一项「组合」扣分（这条特性规则本身照常列出）。

    已持有治愈来源、或队友里有带 healer_traits 特性的角色时照常全额（chances.has_heal_source）；带 exclude_traits 特性的角色
    不折算。概率见 chances.heal_chance。
    要尽量保住推荐流派的角色（keep_schools）在默认推荐里给美工刀的推荐流派加分一并折算（美工刀属于要保的流派时，
    chances.knife_school_bonus）。"""
    counted += knife_school_bonus(sc, name)
    p = heal_chance(sc, name, owned_set, map_name)
    loss = counted * (1 - p)
    if loss < 0.5:
        return None
    text = _about_chance(p)
    return Part("组合", -loss, HEAL_DISCOUNT_REASON.format(p=text), HEAL_DISCOUNT_ORIGIN, HEAL_DISCOUNT_SHORT)


def _combo_parts(sc: Scorer, name: str, owned_set: frozenset, map_name=None) -> list[Part]:
    hk = heal_knife(sc)
    out = []
    for combo in sc.db.combos:
        if name not in combo.chips or not combo.applies_to(sc.hero.name, sc.traits):
            continue
        hit = sorted(combo.owned & owned_set)
        scaled = hk is not None and combo.name in hk["combos"]
        if not hit:
            if scaled:
                part = _knife_anticipation(sc, name, combo, owned_set, hk, map_name)
                if part:
                    out.append(part)
            continue
        score = layer_value(name, len(hit), hk, combo.score) if scaled else combo.score
        # 「{owned}」写在书名号里时，每个筹码名各加一对「」
        reason = combo.reason.replace("「{owned}」", "".join(f"「{h}」" for h in hit)).replace("{owned}", "、".join(hit))
        # 理由标签的短句：写触发这条规则的已有筹码（几个时写第一个加「等」），扣分的写「与……冲突」
        who = hit[0] + ("等" if len(hit) > 1 else "")
        out.append(Part("组合", score, reason, combo.name, f"组合：{who}" if score >= 0 else f"与{who}冲突"))
    return out


def _knife_anticipation(sc: Scorer, name: str, combo, owned_set: frozenset, cfg: dict, map_name=None) -> Part | None:
    """未持有美工刀时：之后获得美工刀的概率 × 按治愈层数缩放的组合分（只按一把美工刀计）× anticipation_factor。

    预判分按 anticipation_factor 打折（rules.toml [heal_knife]）：算出的概率仍偏高。
    """
    p = knife_chance(sc, name, owned_set, cfg, map_name)
    score = p * layer_value(name, 1, cfg, combo.score) * cfg.get("anticipation_factor", 1.0)
    if score < 0.5:
        return None
    return Part(
        "组合", score,
        f"预计之后获得美工刀的概率约 {p:.0%}：美工刀在生命值为满时按治愈层数增加攻击力，该筹码提供治愈层数",
        combo.name, f"之后获得美工刀 {p:.0%}",
    )


def _school_anticipation(sc: Scorer, name: str, owned_set: frozenset, map_name=None) -> Part | None:
    """还没有持有 [school_build] schools 里这个筹码所属流派的筹码时：该流派的筹码加
    「之后出现并会选择的配套筹码的概率 × 组合分 × factor」，分项名「组合」。

    配套筹码和组合分取以该筹码为「已持有」一方、
    配的是同一流派筹码的组合规则（有几条时组合分取最高的一条）；概率见 chances.partner_chance。
    """
    db = sc.db
    cfg = db.rules.get("school_build")
    school = db.chips[name].school
    if not cfg or school not in cfg["schools"]:
        return None
    if any(db.chips[o].school == school for o in owned_set if o in db.chips):
        return None
    # 配套筹码只算同一流派、不是自己、也不在已有筹码里的。以该筹码为「已持有」一方、配的是别的流派（例：电流核心与探天卫星），
    # 或者只配到自己的组合规则（例：星光的转账筹码叠加，ATM机两边都有）不算
    combos = [c for c in db.combos if name in c.owned and c.applies_to(sc.hero.name, sc.traits)
              and any(x in db.chips and db.chips[x].school == school and x != name and x not in owned_set
                      for x in c.chips)]
    if not combos:
        return None
    value = max(c.score for c in combos)
    partners = frozenset(x for c in combos for x in c.chips
                         if x in db.chips and db.chips[x].school == school) - owned_set - {name}
    p = partner_chance(sc, name, partners, owned_set, map_name)
    score = p * value * cfg["factor"]
    if score < 0.5:
        return None
    return Part(
        "组合", score,
        f"预计之后获得配套{school}筹码的概率约 {p:.0%}：持有该筹码后，配套筹码的{school}效果可持续触发",
        "school_build", f"之后获得配套筹码 {p:.0%}",
    )


def teammate_parts(sc: Scorer, name: str) -> list[Part]:
    """配合队友的分项（data/synergy.toml 的队友规则），分项名「队友」。"""
    mates = sc.st.teammates
    if not mates:
        return []
    db = sc.db
    out = []
    for rule in db.teammate_rules:
        if name not in rule.chips or not rule.applies_to(sc.hero.name, sc.traits):
            continue
        matched = []
        for t in mates:
            if rule.who == "human" and t.bot or rule.who == "bot" and not t.bot:
                continue
            if rule.heroes and t.hero not in rule.heroes:
                continue
            if rule.traits:
                hs = db.hero_synergy.get(t.hero) if t.hero else None
                if not hs or not (rule.traits & set(hs.traits)):
                    continue
            matched.append(t)
        n = len(matched)
        if n < rule.min_count:
            continue
        score = rule.per * n
        if rule.cap is not None:
            score = max(-rule.cap, min(rule.cap, score))
        # 「{mates}」替换为符合条件的队友角色名，写法同组合规则的「{owned}」
        names = list(dict.fromkeys(t.hero for t in matched if t.hero)) or ["角色待识别"]
        reason = (
            rule.reason.replace("{n}", str(n))
            .replace("「{mates}」", "".join(f"「{h}」" for h in names))
            .replace("{mates}", "、".join(names))
        )
        # 不分真人电脑的规则（who 为 any）匹配到电脑队友时，短句里标出电脑（免得「配合队友：潘大猛」和
        # 「电脑队友不配合站位」同时出现时看不出潘大猛是电脑）；整句和分值不变
        mark = rule.who == "any" and any(t.bot for t in matched)
        short = (fill_mates_short(rule.short, matched, mark) if rule.short
                 else mates_short(rule.who, matched, mark))
        out.append(Part("队友", score, reason, rule.who, short))
    return out


# ------------------------------------------------------------ 难度（rules.toml [survival]、[extreme]）


def survival_values(sc: Scorer) -> dict[str, float]:
    """生存类筹码及其分值：取 [survival] trait 那条特性（「需要生存」）的加分规则，同一筹码取最高的一条。"""
    if sc.memo.survival is None:
        cfg = sc.db.rules.get("survival") or {}
        out: dict[str, float] = {}
        for rule in sc.db.trait_rules.get(cfg.get("trait", ""), ()):
            if rule.score > 0:
                for chip in rule.chips:
                    out[chip] = max(out.get(chip, 0.0), rule.score)
        sc.memo.survival = out
    return sc.memo.survival


def nightmare_plus(sc: Scorer) -> bool:
    """本局确定是噩梦及以上难度：State.difficulty 不是普通，且难度档位为 LEVEL_NIGHTMARE。
    档位为困难、困难及以上（分不出困难还是噩梦）或不知道时为 False，维持困难及以上的算法。"""
    st = sc.st
    return st.difficulty != NORMAL and getattr(st, "difficulty_level", None) == LEVEL_NIGHTMARE


def _survival_parts(sc: Scorer, name: str, map_name=None) -> list[Part]:
    """困难及以上难度（State.difficulty 不是普通）：生存类筹码按 [survival] 的系数加分，分项名「难度」。

    困难及以上加「需要生存」特性分值的一半，普通难度不加，不随轮次变化。已带该特性的角色不再加（自动层已按全额计入）。
    带 fighter_traits 特性的角色（经常挑战怪物），防御力加成筹码（fighter_chips）另加 fighter_factor，合在同一个分项里。
    噩梦及以上（nightmare_plus）时，nightmare_chips（摩托头盔-高级）对所有角色按特性分值 × nightmare_factor
    计，不再另加 fighter_factor；已带该特性的角色另加 nightmare_trait_factor，合计同样是 nightmare_factor 倍。
    摩托头盔-中级、-一般和其他生存类筹码不变。
    难度不知道（State.difficulty_level 为 None，识别卡片不显示难度胶囊）时，整句前面加 UNKNOWN_DIFFICULTY_LEAD，
    分值和短句不变。
    地图 map_name 在 [survival] skip_maps 里时，这张地图列出的筹码不加（水乡古镇的摩托头盔）；地图待识别时照常加。
    """
    cfg = sc.db.rules.get("survival")
    if not cfg or sc.st.difficulty == NORMAL:
        return []
    if map_name and name in (cfg.get("skip_maps") or {}).get(map_name, ()):
        return []
    has_trait = cfg.get("trait") in sc.traits
    if name in cfg.get("nightmare_chips", ()) and nightmare_plus(sc):
        base = survival_values(sc).get(name)
        if not base:
            return []
        score = base * (cfg.get("nightmare_trait_factor", 0.0) if has_trait else cfg.get("nightmare_factor", 0.0))
        if score < 0.5:
            return []
        return [Part("难度", score, NIGHTMARE_REASON, "survival_nightmare", NIGHTMARE_SHORT)]
    if has_trait:
        return []
    base = survival_values(sc).get(name)
    if not base:
        return []
    fighter = name in cfg.get("fighter_chips", ()) and bool(set(cfg.get("fighter_traits", ())) & set(sc.traits))
    score = base * (cfg["factor"] + (cfg.get("fighter_factor", 0.0) if fighter else 0.0))
    if score < 0.5:
        return []
    # 难度不知道（档位为 None）时整句前面写明「难度待识别，按困难及以上计算：」，后半句的主语随之写
    # 「这几档难度」，不让「困难及以上」连着出现两次；短句不变；知道难度时不变
    if getattr(sc.st, "difficulty_level", None) is None:
        lead, subject = UNKNOWN_DIFFICULTY_LEAD, UNKNOWN_DIFFICULTY_SUBJECT
    else:
        lead, subject = "", "困难及以上难度"
    if fighter:
        return [Part("难度", score, f"{lead}{subject}怪物数值更高、进度上限更短；该角色经常挑战怪物，防御力可减少受到的反击伤害",
                     "survival_fighter", "难度：生存类")]
    return [Part("难度", score, f"{lead}{subject}怪物数值更高、进度上限更短，提高生存能力的收益较高", "survival",
                 "难度：生存类")]


def _extreme_parts(sc: Scorer, name: str) -> list[Part]:
    """本局难度是极限（State.difficulty_name 为 [extreme] difficulty：画面上读到极限，或进度条红叉的格数只和极限对得上、
    按进度上限推算成极限）时，[extreme] chips 里的筹码对所有角色加分，分项名「难度」。整句取 [extreme] reasons，短句
    EXTREME_SHORT。普通难度、难度名不是极限或不知道时不加（极限和疯狂进度上限相同的地图只靠推算分不出，不加）。"""
    cfg = sc.db.rules.get("extreme")
    if not cfg or sc.st.difficulty == NORMAL or getattr(sc.st, "difficulty_name", None) != cfg.get("difficulty"):
        return []
    score = (cfg.get("chips") or {}).get(name)
    reason = (cfg.get("reasons") or {}).get(name)
    if not score or not reason:
        return []
    return [Part("难度", float(score), reason, "extreme", EXTREME_SHORT)]


# ------------------------------------------------------------ 收入折算


@dataclass(frozen=True)
class IncomeCut:
    """收入折算（_income）：part 是「收入折算」那一项；cut 是扣掉的比例 = 扣分 ÷ 折算的对象（这个筹码的正分，不含成长分，
    也不含必选的分，同 income_part 的口径），0～1；left_cut 是其中只按剩下几次兑现折算的那一层扣掉的比例（星光、星币按剩余
    轮次或进度折算的系数，不含满 3 星后的 after_max_star、晚拿银行卡的 late_factor；轮次未知时没有这一层，为 0），按方案排
    先后时收入降档按它判断（scheme_rank.demotion）；brief 是剩下多少兑现机会的短说法（「星光只剩 1 次兑现」「收入只剩
    3 轮」，一次都不剩时「星光不再兑现」「收入不再兑现」），收入降档的理由标签用；轮次未知时不降档、用不到，为空。"""

    part: Part
    cut: float
    brief: str
    left_cut: float = 0.0


def income_part(sc: Scorer, chip: Chip, parts: list[Part], rnd: int | None, owned=frozenset()) -> Part | None:
    """收入折算那一项（_income 的 part），没有时为 None。"""
    found = _income(sc, chip, parts, rnd, owned)
    return found.part if found is not None else None


def income_cut(sc: Scorer, chip: Chip, parts: list[Part], rnd: int | None, owned=frozenset()) -> IncomeCut | None:
    """收入折算那一项连同扣掉的比例（IncomeCut），没有时为 None。parts 可以是这个筹码的全部分项（含收入折算那一项本身
    和不加分的职业倾向）：折算的对象只算为正的分项，这两项不进去，结果和打分时相同。scheme_rank 的收入降档按它判断。"""
    return _income(sc, chip, parts, rnd, owned)


def _income(sc: Scorer, chip: Chip, parts: list[Part], rnd: int | None, owned=frozenset()) -> IncomeCut | None:
    """收入类筹码按剩余兑现次数折算（越晚拿到，能兑现的次数越少）。

    - 星光收入：每逢第 3 轮按星光层数发放星币，按「到进度上限前剩余的轮次奖励次数 ÷ 第 1 轮时的次数」折算。
    - 星币收入：按「到进度上限前剩余的轮次 ÷ 第 1 轮时的轮次」折算。
    - 分子（剩下的）按进度条折算后的上限算（sc.cap：读到当前格时为 当前轮次 +（进度上限 − 当前格），进度比轮次走得快时
      剩下的更少）；分母（第 1 轮时的）是整局的参照，固定按进度上限本身算（sc.cap_shown），不跟着当前格变。
      没读到当前格、或当前格等于轮次时两者相同。
    - 自己满 3 星后，非经济型角色的收入再乘 after_max_star（星币不再用于升星）。
    折算的对象是这个筹码的正分（不含成长分，成长分本身已按轮次取消；也不含默认推荐给必选筹码的分：
    必选是对这个筹码的整体判断，不随剩余的兑现次数变）乘以收入占比（rules.toml [income]）。
    带 exempt_traits 特性的角色（星光层数直接换成属性）的星光收入占比再乘 exempt_factor；已持有 exempt_owned 里的筹码
    （金色飞星：星光层数还会增加对首领的伤害）时同样乘一次 exempt_factor，两种都满足时只乘一次。
    [income] late 里的筹码（银行卡）在那一轮及以后获得时，两种折算系数再乘 late_factor（整句接 LATE_INCOME_NOTE）；
    经济型角色（带 economy_trait 特性）不乘这一层，只按剩余兑现次数折算：星币多用于转账给队友，晚到的星币照样
    转得出去，和满 3 星后不再减半同一个理由。
    另记只按剩余兑现次数那一层扣掉的比例（IncomeCut.left_cut，收入降档按它判断）：星光、星币占比 ×（1 − 按剩余轮次或进度
    折算的系数），不乘 late_factor、after_max_star；轮次未知时为 0。
    parts 是前面已经算好的分项（按 _build_parts 的先后），owned 是计算这一项时的已有筹码（_build_parts 的 owned_set）。
    """
    cfg = sc.db.rules["income"]
    star_share = cfg["starlight"].get(chip.name, 0.0)
    coin_share = cfg["coins"].get(chip.name, 0.0)
    if not (star_share or coin_share):
        return None
    by_trait = bool(set(cfg.get("exempt_traits", ())) & set(sc.traits))
    economy = cfg["economy_trait"] in sc.traits
    exempt_owned = cfg.get("exempt_owned", {})
    by_owned = [c for c in exempt_owned if c in owned] if star_share and not by_trait else []
    if by_trait or by_owned:
        star_share *= cfg["exempt_factor"]
    core = sc.db.rules["synergy"]["core"]
    # 自己方案的必选和默认推荐的必选同一口径（SCHEME_CORE_ORIGIN）：也不扣
    base = sum(p.score for p in parts if p.score > 0 and p.label != "成长分"
               and not (p.label == "玩家思路" and p.score >= core) and p.origin != SCHEME_CORE_ORIGIN)
    if base <= 0:
        return None
    cap = sc.cap
    f_star = f_coin = 1.0
    bits = []
    if rnd is not None:
        every = cfg["reward_every"]
        ref = reward_rounds_left(1, sc.cap_shown, every)
        left = reward_rounds_left(rnd, cap, every)
        f_star = left / ref if ref else 0.0
        f_coin = max(0, cap - rnd) / max(1, sc.cap_shown - 1)
    # 只按剩下几次兑现折算的那一层（收入降档按它判断，IncomeCut.left_cut）：下面的 late_factor、after_max_star 不算进去
    left_cut = star_share * (1 - f_star) + coin_share * (1 - f_coin)
    if rnd is not None:
        if star_share:
            bits.append(f"到进度上限（{sc.cap_shown}）前还有 {left} 次轮次奖励")
        if coin_share:
            bits.append(f"到进度上限（{sc.cap_shown}）前还有 {max(0, cap - rnd)} 轮")
        # 较晚获得的银行卡（[income] late）：在按剩余兑现次数的折算之上，收入部分再乘 late_factor；经济型角色不乘
        late = (cfg.get("late") or {}).get(chip.name)
        if late is not None and rnd >= late and not economy:
            f_star *= cfg["late_factor"]
            f_coin *= cfg["late_factor"]
            bits.append(LATE_INCOME_NOTE.format(round=late))
    if by_owned:
        # 「已持有「金色飞星」，星光层数还会增加对首领的伤害，折算幅度减半」（整句写原因；短句不变）
        bits.append(EXEMPT_OWNED_NOTE.format(chip=by_owned[0], why=exempt_owned[by_owned[0]]))
    m = 1.0
    if sc.st.star >= 3 and not economy:
        m = cfg["after_max_star"]
        bits.append("已达到 3 星，星币不再用于升星")
    loss = base * (star_share * (1 - f_star * m) + coin_share * (1 - f_coin * m))
    if loss < 0.5:
        return None
    kind = "星光和星币收入" if star_share and coin_share else "星光收入" if star_share else "星币收入"
    # 轮次未知时只有「满 3 星」一项，不按剩余兑现次数算
    head = f"{kind}按剩余兑现次数折算：" if rnd is not None else f"{kind}折算："
    # 理由标签的短句只写最关键的一个数：剩余轮次（有星币收入时）或剩余的轮次奖励次数；brief 是同一个数的「只剩」说法，
    # 一次都不剩时写「不再兑现」。轮次未知时没有剩余兑现那一层、不会降档，brief 用不到
    if rnd is None:
        short, brief = "星币不再用于升星", ""
    elif coin_share:
        rest = max(0, cap - rnd)
        short, brief = f"收入剩余 {rest} 轮", f"收入只剩 {rest} 轮" if rest else "收入不再兑现"
    else:
        short, brief = f"星光剩余 {left} 次兑现", f"星光只剩 {left} 次兑现" if left else "星光不再兑现"
    return IncomeCut(Part("收入折算", -loss, head + "；".join(bits), "income", short), loss / base, brief, left_cut)


# ------------------------------------------------------------ 循环往复（rules.toml [endless_cycle]）


def endless_wait(db, source: str | None) -> int:
    """循环往复拿到后再过几回合给额外那次三选一：按拿到它的那次三选一的来源取 rules.toml [endless_cycle] delay（升星、
    筹码商店 2 回合，任务 3 回合；来源不在表里、不知道时取最长的）。delay 写成一个数时不分来源。给循环往复估值
    （endless_delay）和 Session 记下额外那次什么时候给共用。"""
    delay = db.rules["endless_cycle"]["delay"]
    if isinstance(delay, dict):
        return int(delay.get(source, max(delay.values())))
    return int(delay)


def endless_delay(sc: Scorer) -> int:
    """这次三选一拿到循环往复的话，再过几回合给额外那次三选一（endless_wait，按这次三选一的来源）。"""
    return endless_wait(sc.db, sc.st.source)


def endless_tiers(sc: Scorer, delay: int) -> list[tuple[int, float]]:
    """额外那次三选一的等级（任务筹码的 1~3 级，对应稀有度概率表的第 1~3 行）：按给的时候预计的星数，
    即当前星数 + [endless_cycle] star_rate × 等待的回合数（不超过 3 星，不到 1 时按 1 级）。
    预计的星数不是整数时按相邻两级插值，返回（等级, 权重）的列表，权重合计为 1（context.task_tiers，已拿循环往复、那一次
    还没给时的 context.endless_sources 同一个算法）。"""
    rate = float(sc.db.rules["endless_cycle"].get("star_rate", 0.0))
    return task_tiers(sc.st.star, rate, delay)


def _endless_chain(sc: Scorer, m, count: int) -> list[tuple[float, list[float]]]:
    """拿了循环往复以后，下一次三选一（地图 m 上）里最多连刷 1～count 次的期望最高分，按来源分开：[(占比, [C(1), …,
    C(count)])]，C 是刷新估值的连刷递推（reroll.reroll）：C(1) = 刷新一次的期望最高分，C(k) = E[max(本次最高分, C(k − 1))]。
    来源是下一次三选一的主体（context.next_sources：0～2 星是下一星的升星，3 星以后是任务和筹码商店，按占比；State.endless_pending
    时 3 星以后是那次额外的三选一，按任务来源，不过那时循环往复已在已有筹码里、不会再是候选，这里碰不到），分布按刷新后的
    （pool_and_dists 的 reroll，最左格按 rules.toml [pool] reroll_leftmost），筹码池是拿了循环往复以后的；各筹码的分值按现在的
    已有筹码在下一次三选一的轮次算，不含前瞻（同前瞻 lookahead.future_setup，和它共用 Memo.values 的分值表）。
    筹码池为空时为空列表。按地图缓存（Memo.endless_chain），count 不够时重算。"""
    found = sc.memo.endless_chain.get(m)
    if found is not None and all(len(c) >= count for _, c in found):
        return found
    db, st = sc.db, sc.st
    after = (*sc.known_owned, db.rules["endless_cycle"]["chip"])
    pool = player_pool(db, after, m)
    out: list[tuple[float, list[float]]] = []
    if pool:
        rnd = None if st.round is None else st.round + db.rules["lookahead"]["round_advance"]
        value = pool_scores(sc, sc.known_owned, rnd, m, pool)
        for share, source, star, tier in next_sources(sc, m):
            _, left, general = pool_and_dists(db, sc.hero, after, m, star, source, tier, reroll=True,
                                              difficulty=st.difficulty, pool=pool, cache=sc.dist_cache)
            chain = [expected_best(left, general, value)]
            while len(chain) < count:
                chain.append(expected_max_with(left, general, value, chain[-1]))
            out.append((share, chain))
    sc.memo.endless_chain[m] = out
    return out


def _endless_chain_gain(sc: Scorer, q: int, map_name) -> float:
    """拿了循环往复以后的第 q 次刷新超出留给之后的三选一的次数（多出来的）时值多少：之后的三选一多连刷一次的期望提升。

    算在下一次三选一上：那时之后的三选一比现在少一次（PicksLeft.total − 1），留下的按同一个留法（context.keep_count）
    少一次，多出来的比现在多一次。第 q 次刷新在那时是多出来的第 n + 1 次（n = q − 那时留下的次数 − 1；现在超出留下的
    那几次，那时至少是多出来的第 2 次，n 不小于 1），值「最多连刷 n + 1 次」比「最多连刷 n 次」多的期望最高分
    C(n + 1) − C(n)（_endless_chain；几种来源按占比加权）。预计之后不到 1 次三选一（PicksLeft.total 小于 1）时，下一次
    三选一只有这么大的机会出现，提升按 min(1, PicksLeft.total) 打折（同前瞻影响的次数不超过剩余三选一次数，
    lookahead.lookahead_times）。地图待识别时按候选地图分别算再平均（map_name 给定时只按这一张）。"""
    rerolls = max(0, sc.st.rerolls) + sc.db.rules["reroll"]["endless_cycle"]
    n = max(1, q - keep_count(rerolls, max(0.0, sc.picks.total - 1.0)) - 1)
    per = []
    for m in ((map_name,) if map_name else sc.maps):
        chains = _endless_chain(sc, m, n + 1)
        if chains:
            per.append(sum(share * (chain[n] - chain[n - 1]) for share, chain in chains))
    if not per:
        return 0.0
    return min(1.0, max(0.0, sc.picks.total)) * (sum(per) / len(per))


def _endless_rerolls(sc: Scorer, map_name) -> float:
    """循环往复给的刷新（[reroll] endless_cycle 次）的价值，未乘 [endless_cycle] weight。

    加上它给的刷新以后按留法分（context.reroll_keep：刷新次数读到了时之后的三选一每次留 1 次、再多留 1 次以防估少）：
    - 拿了以后的第 q 次刷新（q = 现有刷新次数 + j）落在留下的以内：照原来的模型，gain × H(剩余三选一次数 ÷ q)，
      与刷新代价同一个模型（context.harmonic）；
    - 超出留下的（多出来的刷新）：只值之后的三选一多连刷一次的期望提升（_endless_chain_gain；之后不到 1 次三选一时按
      下一次三选一出现的机会打折），并且不超过原来的模型给这一次的 gain × H(剩余三选一次数 ÷ q)：多出来的只往下调。
      连刷的提升按这个局面实际的刷新后分布算，原来的模型是所有角色同一个 gain；刷新收益大的角色（刷新后分布好），连刷
      一次的提升可能比原来的模型还高，封顶以后多出来的每一次都不比原来值钱。
    刷新次数没读出来、按默认值补的（State.rerolls_known 为 False）时不分多出来的，全部照原来的模型。预计之后没有三选一时
    为 0（刷新次数读到了时，剩余三选一次数不超过 context.PICKS_EPS 也算没有）。"""
    db, st = sc.db, sc.st
    total = sc.picks.total
    if total <= 0:
        return 0.0
    gain = db.rules["reroll_cost"]["gain"]
    extra = db.rules["reroll"]["endless_cycle"]
    r = max(0, st.rerolls)
    keep = reroll_keep(sc, r + extra)
    if keep <= 0:
        return 0.0
    # 第 q = r + j 次；都在留下的以内时和原来的模型逐位相同（同样用 sum 按 j 的先后加）
    return sum(gain * harmonic(total / (r + j)) if r + j <= keep
               else min(_endless_chain_gain(sc, r + j, map_name), gain * harmonic(total / (r + j)))
               for j in range(1, extra + 1))


def _endless_parts(sc: Scorer, owned: tuple, rnd: int | None, map_name, gone: tuple = (),
                   zero: str | None = None) -> list[Part]:
    """循环往复按刷新模型估值：

    分值 = weight ×（等待 delay 回合后那次随机三选一的期望最高分 × 时间折算 + 增加的 3 次刷新的价值）。
    - 等待的回合数按这次三选一的来源（endless_delay：升星、筹码商店 2 回合，任务 3 回合）。
    - 额外那次三选一按任务来源（三格全随机）计算，等级按给的时候预计的星数（endless_tiers，不是整数时按相邻两级插值）；
      到那时已到进度上限时记 0。持有的筹码数不设上限，已有筹码再多也照常计这次三选一。
    - 增加的刷新（_endless_rerolls）：加上它给的刷新以后落在留给之后的三选一以内的，第 j 次的价值 = gain × H(剩余三选一次数
      ÷ (现有刷新次数 + j))，与刷新代价同一个模型（context.harmonic）；超出的（多出来的刷新）只值之后的三选一多连刷一次的
      期望提升，不超过前一种给这一次的价值。刷新次数没读出来时全部按前一种。
    gone、zero 只在前瞻假设的局面里给（endless_value）：gone 是假设已经拿到的筹码，额外那次三选一的筹码池和分布按
    「owned + 循环往复 + gone」取（拿走的不会再出现，流派和职业分值跟着变）；zero 在那次三选一里分值记 0。
    那次三选一里各筹码的分值照常按 owned 算。两个都不给时就是这个筹码本身的分项。增加的刷新的价值只由这次的局面定
    （已有筹码、刷新次数），不随 owned、rnd、gone、zero 变，同原来的模型。
    """
    db, st = sc.db, sc.st
    cfg = db.rules["endless_cycle"]
    name = cfg["chip"]
    delay = endless_delay(sc)
    cap = sc.cap
    reached = rnd is not None and rnd + delay >= cap
    pick_value = 0.0
    if not reached:
        maps = (map_name,) if map_name else sc.maps
        star = max(0, min(3, st.star))
        later = None if rnd is None else rnd + delay
        tiers = endless_tiers(sc, delay)
        after = (*owned, name, *gone)
        values = []
        for m in maps:
            pool = player_pool(db, after, m)
            if not pool:
                continue
            value = {c.name: 0.0 if c.name == zero else score(sc, c.name, owned, later, m) for c in pool}
            best = 0.0
            for tier, share in tiers:
                _, left, general = pool_and_dists(
                    db, sc.hero, after, m, star, "task", tier,
                    difficulty=st.difficulty, pool=pool, cache=sc.dist_cache,
                )
                best += share * expected_best(left, general, value)
            values.append(best)
        pick_value = sum(values) / len(values) if values else 0.0
        if rnd is not None:
            pick_value *= max(0, cap - rnd - delay) / max(1, cap - rnd)
    extra = db.rules["reroll"]["endless_cycle"]
    reroll_value = _endless_rerolls(sc, map_name)
    w = cfg["weight"]
    points = w * (pick_value + reroll_value)
    # 整句不写分值（悬浮窗上不显示分数）；刷新和额外三选一两部分只合计在这一项的分值里。
    # 等待的回合数按来源（升星、筹码商店 2 回合，任务 3 回合）
    if reached:
        reason = f"增加 {extra} 次刷新；{delay} 回合后已到进度上限，无法获得额外的筹码三选一"
    else:
        reason = f"增加 {extra} 次刷新；{delay} 回合后获得 1 次筹码三选一"
    # 理由标签的短句（选项名已是循环往复）
    short = f"{extra} 次刷新" if reached else f"{extra} 次刷新和 1 次三选一"
    return [Part("循环往复", points, reason, ENDLESS_ORIGIN, short)]


def endless_value(sc: Scorer, rnd: int | None, map_name, gone: tuple = (), zero: str | None = None) -> float | None:
    """前瞻假设的局面里循环往复的总分（lookahead.future 用；分值按现在的已有筹码，同 lookahead.future_setup）：
    循环往复那一项按这个局面重算（gone 是比现在多拿的筹码，不会在额外那次三选一里出现；zero 在那次里记 0，见
    _endless_parts），其余分项不变，按 _build_parts 的先后相加。循环往复的分项里没有这一项（按筹码方案计算、方案标了
    它的档位，档位那一项代替了它）时为 None，照常用它本身的总分。"""
    name = sc.db.rules["endless_cycle"]["chip"]
    items = parts(sc, name, sc.known_owned, rnd, map_name)
    at = next((i for i, p in enumerate(items) if p.origin == ENDLESS_ORIGIN), None)
    if at is None:
        return None
    items[at] = _endless_parts(sc, sc.ordered(sc.known_owned), rnd, map_name, gone, zero)[0]
    return sum(p.score for p in items)
