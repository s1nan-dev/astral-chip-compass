"""按自己的方案排先后：卡片上按档位排，必选 > 推荐 > 可选 > 未标记 > 不选 > 被锁的其他流派；每张候选的位次（Place）、
档位的级数、「选了之后拿不到……」的提醒（Loss）、推荐区的附注（SchemeNote），以及把这些和「刷新」那一行
（reroll.reroll_view）组织成 Advice.ranking 的 advise_scheme。

按档位排的三处例外（advise_scheme 的先后）：
- 收入降档：标了推荐、可选的收入类筹码，只按剩下几次兑现算、收入扣掉一半以上（rules.toml [scheme] income_demote；满 3 星
  后的减半不算进去）时按低一档排，排在那一档已有的候选后面（Demotion）；
- 同一档的推荐、可选先照顺位，后一张的档位前瞻（lookahead.tier_lookahead）比前一张高出不少于 [scheme] same_tier_near、
  并且卡片上写的数（选了以后，下一次三选一里至少出现一张方案里的必选、推荐的机会，lookahead.tier_ahead）不比前一张低时
  两张对调（Move）；两样方向相反时照顺位；必选照顺位；
- 选了会让方案里更要紧的必选、推荐本局拿不到（reach：先比还能拿到的必选个数、再比推荐个数）、另选别的能保住时，
  排到保得住的候选后面（Loss.lost、Loss.behind）。不选、被锁的照旧在最后。

分值照 Scorer 算（档位代替玩家思路和角色特性，没标的不用默认推荐，见 parts），这里只定先后和说明。同系列、队友刚需、
推荐度和附注的做法和默认推荐相同（advice），只是不越过档位。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import TYPE_CHECKING

from ..data import DB, RARITIES, SCHOOLS, TIERS
from ..pool import owned_schools, tie_key
from ..schemes import school_of
from .advice import (NEED_LEAD, NEED_ORIGIN, NEED_PLACE, NEED_SECOND, Advice, ChipEval, common_notes, lift_need,
                     mark_series, mate_needs, need_pick, softmax)
from .advice_text import (PLACE_LOCKED, PLACE_MARKED, PLACE_STRAYED, PLACE_UNMARKED, REROLL, REROLL_SLOT,
                          SCHEME_TIER_WORD)
from .parts import income_cut

if TYPE_CHECKING:
    from .lookahead import TierAhead
    from .reroll import RerollEval, RerollView
    from .scorer import Scorer

# 自己的方案里（_series_near_scheme，两张都未标记）卡片上不写推荐度，后接这一句
SERIES_NEAR_TIP_SCHEME = "两张按角色特性计算的结果接近，稀有度较高的「{x}」排在前面。"
# 推荐区的方案附注（SchemeNote）：方案外的流派已进入（走偏）；
# 流派名额占满、方案里的必选和推荐本局不再出现（{chips} 按档位写「必选「A」「B」、推荐「C」」，
# 超过 SCHEME_LOST_MAX 个时只写前几个、后接 SCHEME_LOST_MORE）。{schools}、{entered} 每个流派各加一对「」
SCHEME_STRAYED_NOTE = "已进入方案外的流派{schools}"
SCHEME_LOST_NOTE = "已进入{entered}流派，筹码方案中的{chips}本局不再出现"
SCHEME_LOST_MAX = 4
SCHEME_LOST_MORE = "等 {n} 个"
# 「选了之后拿不到……」的种类（Loss.kind）：马上拿不到、只能再进入一个（做成标签）、只能再进入一个（只写在悬停里）
LOSS_NOW, LOSS_ONE_LEFT, LOSS_TIP = "now", "one_left", "tip"
# 推荐区附注的种类（SchemeNote.kind）
NOTE_STRAYED, NOTE_LOST = "strayed", "lost"
# 档位的级数（自己的方案里刷新按它算平均，和排在最前那张比）：必选 4、推荐 3、可选 2、未标记 1，
# 不选和被锁的 0（三张都是不选时最好的一张也是 0）
TIER_LEVEL = {"core": 4, "first": 3, "second": 2, "avoid": 0}
UNMARKED_LEVEL = 1
# 卡片上的先后：必选 → 推荐 → 可选 → 未标记（含走偏的、资料库未收录的）→ 不选 → 被锁的其他流派
_PLACE_CLASS = {"core": 0, "first": 1, "second": 2, PLACE_UNMARKED: 3, PLACE_STRAYED: 3, "avoid": 4, PLACE_LOCKED: 5}
_UNMARKED_CLASS = 3
_LAST_CLASSES = (_PLACE_CLASS["avoid"], _PLACE_CLASS[PLACE_LOCKED])  # 不选、被锁：不参加「拿不到的往后」
# 按档位前瞻排先后的两档（必选照顺位）
SAME_TIER_TIERS = ("first", "second")
# 收入降档：推荐当可选、可选当未标记（None）
DEMOTE_TO = {"first": "second", "second": None}
# 同一档按档位前瞻排先后时，一张候选为什么排在这里（Move.kind）：
# MOVE_AHEAD 两样都比同一档顺位更前的几张好（档位前瞻高出不少于 same_tier_near，卡片上写的至少出现一张的机会不比它们低），
# 排到了它们前面；MOVE_BEHIND 同一档顺位更后的几张两样都更好，排到了它前面；同一档里照顺位排在最前的那张（只有一张时不写）：
# MOVE_LEAD 两样也都比下一张好，MOVE_NEAR 和下一张的档位前瞻相差不到线（差不多，照顺位），MOVE_MIXED 档位前瞻相差不少于线、
# 卡片上的那个数却方向相反（看作差不多，照顺位），MOVE_LAST 预计之后没有三选一（档位前瞻都是 0，照顺位）
MOVE_AHEAD, MOVE_BEHIND, MOVE_LEAD, MOVE_NEAR, MOVE_LAST = "ahead", "behind", "lead", "near", "last"
MOVE_MIXED = "mixed"
# 选了以后方案里还能拿到的必选、推荐比现在少（Loss.lost）：少了必选、只少了推荐
LOST_CORE, LOST_FIRST = "core", "first"
_LOST_GROUP = {"": 0, LOST_FIRST: 1, LOST_CORE: 2}  # 拿不到的往后：没有拿不到的 → 只少了推荐 → 少了必选
# 卡片上第几位的写法（队友刚需那一句，NEED_PLACE）：三张候选加「刷新」最多 4 行
_PLACE_NUMBERS = {1: "一", 2: "二", 3: "三", 4: "四"}


@dataclass(frozen=True)
class Place:
    """自己的方案里一张候选筹码的位次（advice_text.place_text 的写法）。

    kind：PLACE_MARKED 标了档位；PLACE_UNMARKED 未标记（含资料库未收录的）；PLACE_STRAYED 方案外的流派已进入以后，
    这个流派里没标的筹码改按未标记算；PLACE_LOCKED 被锁成不选的流派里没标的筹码（排在标了不选的筹码之后）。
    tier、rank：kind 为 PLACE_MARKED 时的档位和在这一档里的顺位（从 1 起，和编辑窗口相同）；school：kind 为
    PLACE_STRAYED、PLACE_LOCKED 时这张筹码的流派。"""

    kind: str
    tier: str | None = None
    rank: int | None = None
    school: str | None = None


@dataclass(frozen=True)
class Loss:
    """自己的方案里选了这张候选之后拿不到的必选、推荐。

    kind：LOSS_NOW 选了以后流派名额占满，方案里的必选、推荐马上拿不到；LOSS_ONE_LEFT 选了以后只剩一个流派名额，
    几个列进方案的流派只能再进一个（候选是方案外流派的筹码，做成标签）；LOSS_TIP 同上，候选属于列进方案的流派（不论标没标
    档位：这时方案一定列了三个以上流派、还没进流派），不算拿不到时只写在位次的悬停里。
    warn：这一项算不算拿不到（lost 不为空），True 橙色（LOSS_TIP 也做成标签）、False 灰色（避免不了：另选别的也保不住更
    要紧的必选、推荐）。
    enter：选择后已进入的流派（原来已进入的在前，按进入的先后）。gone：LOSS_NOW 时为本局不再出现、其中有方案的必选和推荐的
    流派；另两种为只能再进入一个的几个列进方案的流派（按 data.SCHOOLS 的顺序）。chips：拿不到（或其中一部分会拿不到）的
    必选、推荐，（筹码名, 档位）按方案的先后，已持有的不算。by_school：按流派分开的必选、推荐筹码名，只列有这些筹码的流派。
    lost：选了以后方案里还能拿到的必选、推荐比现在少（reach，按这张候选的档位看，见 lost_kind）：LOST_CORE 少了必选，
    LOST_FIRST 只少了推荐，空为不算拿不到。behind：排在它前面、能保住的几张候选（拿不到的情况比它好的，Advice.evals
    的下标，按卡片上的先后）；不选、被锁的不挪，为空。keep_last：算拿不到、排在它前面的又都保不住（behind 为空）时，能保住的
    不选、被锁的几张（它们照旧排在最后，所以这张仍排在前面；按卡片上的先后）。alone：算拿不到、这次的候选选哪张都会少拿
    （behind、keep_last 都为空；不选、被锁的候选自己为 False）。final：本次之后预计没有筹码三选一（PicksLeft.total
    为 0）：之后本来就拿不到别的筹码，这一项不算拿不到、不影响先后。"""

    kind: str
    warn: bool
    enter: tuple[str, ...]
    gone: tuple[str, ...]
    chips: tuple[tuple[str, str], ...]
    by_school: tuple[tuple[str, tuple[str, ...]], ...] = ()
    lost: str = ""
    behind: tuple[int, ...] = ()
    final: bool = False
    keep_last: tuple[int, ...] = ()
    alone: bool = False


@dataclass(frozen=True)
class Move:
    """同一档（推荐、可选）按档位前瞻排先后时，一张候选为什么排在这里：kind 见 MOVE_*；others 是比较的对象
    （Advice.evals 的下标）：MOVE_AHEAD 为它排到前面去的几张（按顺位），MOVE_BEHIND 为排到它前面的几张（按卡片上的先后），
    MOVE_LEAD、MOVE_NEAR、MOVE_MIXED、MOVE_LAST 为同一档里紧跟在它后面的那一张。"""

    kind: str
    others: tuple[int, ...]


@dataclass(frozen=True)
class Demotion:
    """收入降档：标了推荐、可选的收入类筹码，只按剩下几次兑现折算扣掉的比例 cut（parts.IncomeCut.left_cut，不含满 3 星、
    晚拿银行卡的减半）不小于 [scheme] income_demote，按低一档排。
    tier：当作哪一档（推荐降成可选为 "second"；可选降成未标记为 None）。brief：剩下多少兑现机会（「星光只剩 1 次兑现」，
    parts.IncomeCut）；reason：收入折算那一项的整句。位次照写原来的档。"""

    tier: str | None
    cut: float
    brief: str
    reason: str


@dataclass(frozen=True)
class SchemeNote:
    """自己的方案在推荐区的附注：kind 为 NOTE_STRAYED（已进入方案外的流派）或 NOTE_LOST（流派名额占满，方案里的必选、推荐
    本局不再出现）；text 是附注全文（不带 ⚠）；warn 为 True 时橙色（同时在 Advice.warn_notes 里），False 时灰色。"""

    kind: str
    text: str
    warn: bool


@dataclass
class Ranking:
    """按自己的方案计算时推荐卡片上要显示的东西（Advice.ranking；默认推荐时为 None）。

    order：各选项从上到下的先后，Advice.evals 的下标，「刷新」为 REROLL_SLOT；被同系列上位替代的排在最后。
    places、losses：与 Advice.evals 同序的位次和「选了之后拿不到……」（没有时为 None）。reroll：有「刷新」时那一行的比较和概率。
    notes：推荐区的方案附注（也在 Advice.notes 的最前面）。listed、locked、strayed、targets、entered：列进方案的流派、
    仍被锁成不选的流派（方案锁住、还没进入的）、已进入的方案外的流派、目标流派、已进入的流派（按进入的先后）。
    aheads、moves、demotions：与 Advice.evals 同序的档位前瞻（标了档位的候选，lookahead.TierAhead；其余为 None）、
    同一档按档位前瞻排先后的说明（Move，没有时为空）、收入降档（Demotion，没有时为 None）。"""

    order: list[int]
    places: list[Place]
    losses: list[Loss | None]
    reroll: RerollView | None = None
    notes: list[SchemeNote] = field(default_factory=list)
    listed: tuple[str, ...] = ()
    locked: tuple[str, ...] = ()
    strayed: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    entered: tuple[str, ...] = ()
    aheads: list[TierAhead | None] = field(default_factory=list)
    moves: list[tuple[Move, ...]] = field(default_factory=list)
    demotions: list[Demotion | None] = field(default_factory=list)


# ------------------------------------------------------------ 位次和级数


def place(sc: Scorer, e: ChipEval) -> Place:
    """按自己的方案计算时这张候选的位次（Place）。默认推荐时不用。"""
    name = e.chip.name
    if e.unrecorded or name not in sc.db.chips:
        return Place(PLACE_UNMARKED)
    tier = sc.marks.get(name)
    if tier is not None:
        return Place(PLACE_MARKED, tier, sc.ranks[name])
    school = school_of(sc.db, name)
    if school in sc.scheme_locked:
        return Place(PLACE_STRAYED if school in sc.entered else PLACE_LOCKED, school=school)
    return Place(PLACE_UNMARKED)


def level(sc: Scorer, name: str) -> int:
    """按自己的方案时筹码的级数（TIER_LEVEL）：标了档位的按档位，被锁成不选的流派里没标的 0（已进入的方案外流派
    不算被锁），其余未标记的 1。刷新后的档位比例、刷新和候选比都按它。"""
    tier = sc.marks.get(name)
    if tier is not None:
        return TIER_LEVEL[tier]
    school = school_of(sc.db, name)
    if school in sc.scheme_locked and school not in sc.entered:
        return 0
    return UNMARKED_LEVEL


def place_class(place: Place) -> int:
    """位次在卡片上的大类（_PLACE_CLASS）：同一类里标了档位的按顺位、其余按总分排。"""
    return _PLACE_CLASS[place.tier if place.kind == PLACE_MARKED else place.kind]


def place_level(place: Place) -> int:
    """位次的级数（TIER_LEVEL）：标了档位的按档位，被锁的 0，其余 1。"""
    if place.kind == PLACE_MARKED:
        return TIER_LEVEL[place.tier]
    return 0 if place.kind == PLACE_LOCKED else UNMARKED_LEVEL


# ------------------------------------------------------------ 拿不到的提醒和附注


def reach(sc: Scorer, owned) -> tuple[int, int]:
    """持有这些筹码时，方案里还能拿到的（必选个数, 推荐个数）：已持有的算拿到；没持有、属于某个流派的，要在之后最好的
    流派搭配里才算。最好的搭配是已进入的流派加上用剩下的名额（rules.toml [pool] max_schools）能进的流派，在所有可能里
    取（必选, 推荐）按字典序最大的一种：先保必选，再保推荐。不属于流派的不受选哪张影响（比较时两边一样），不算。"""
    db = sc.db
    owned = tuple(n for n in owned if n in db.chips)
    held = set(owned)
    entered = owned_schools(db, owned)
    got = [0, 0]
    rest: dict[str, list[int]] = {}
    for chip, tier in sc.plan.tiers:
        if tier not in ("core", "first"):
            continue
        k = 0 if tier == "core" else 1
        school = school_of(db, chip)
        if chip in held or school in entered:
            got[k] += 1
        elif school is not None:
            rest.setdefault(school, [0, 0])[k] += 1
    room = max(0, db.rules["pool"]["max_schools"] - len(entered))
    schools = [s for s in SCHOOLS if s in rest]
    best = (0, 0)
    for picked in combinations(schools, min(room, len(schools))):  # 名额用满不会更差：每个流派都只加不减
        best = max(best, (sum(rest[s][0] for s in picked), sum(rest[s][1] for s in picked)))
    return got[0] + best[0], got[1] + best[1]


def lost_kind(sc: Scorer, e: ChipEval, place: Place, now: tuple[int, int] | None = None) -> str:
    """选了这张候选以后，方案里还能拿到的必选、推荐（reach）比现在少的是哪一档：按这张候选自己的档位看——必选、推荐
    只看必选个数（为了保住必选放弃另一个流派的推荐不算），可选、未标记、不选、被锁的先比必选、再比推荐（字典序）。
    LOST_CORE 少了必选，LOST_FIRST 只少了推荐，空为不算拿不到（含「避免不了」的：最好的搭配本来就拿不到它们）。
    本次之后预计没有筹码三选一（PicksLeft.total 为 0）时一律为空：之后本来就拿不到别的筹码。
    now：现在的 reach（几张候选一起算时传进来，不重复算）。"""
    if e.unrecorded or e.chip.name not in sc.db.chips or sc.picks.total <= 0:
        return ""
    now = reach(sc, sc.known_owned) if now is None else now
    after = reach(sc, (*sc.known_owned, e.chip.name))
    if after[0] < now[0]:
        return LOST_CORE
    marked = place.kind == PLACE_MARKED and place.tier in ("core", "first")
    if not marked and after < now:
        return LOST_FIRST
    return ""


def loss(sc: Scorer, e: ChipEval, place: Place, now: tuple[int, int] | None = None) -> Loss | None:
    """自己的方案里选了这张候选之后拿不到的必选、推荐（Loss）。只看方案里没持有的必选、推荐；通用、地图专属筹码不属于流派，
    选了不改变流派，也不会因此拿不到。

    - 选了以后流派名额占满（LOSS_NOW）：其余流派里的必选、推荐本局不再出现；
    - 一个流派都还没进入、选了以后只剩一个名额，剩下的列进方案的流派里有两个以上各有必选、推荐（只能再进一个，注定
      拿不全）：候选是方案外流派的筹码时做成标签（LOSS_ONE_LEFT）；候选属于列进方案的流派时（这时方案一定列了三个以上
      流派）为 LOSS_TIP，不算拿不到时只写在位次的悬停里（开局时几乎每张候选都会进一个列进方案的流派，做成标签会成为噪音）。
    颜色按算不算拿不到（lost_kind）：算的橙色（这时 advise_scheme 把它排到保得住的候选后面），不算的灰色（避免不了；
    本次之后预计没有三选一时也是灰色，Loss.final）。now 同 lost_kind。"""
    db = sc.db
    if e.unrecorded:
        return None
    school = school_of(db, e.chip.name)
    limit = db.rules["pool"]["max_schools"]
    entered = sc.entered
    if school is None or school in entered or len(entered) >= limit:
        return None
    after = (*entered, school)
    owned = set(sc.known_owned)
    wanted = [(c, t) for c, t in sc.plan.tiers if t in ("core", "first") and c not in owned
              and school_of(db, c) is not None]

    def by_school(chips, schools):
        return tuple((s, tuple(c for c, _ in chips if school_of(db, c) == s)) for s in schools)

    if len(after) >= limit:
        chips = tuple((c, t) for c, t in wanted if school_of(db, c) not in after)
        if not chips:
            return None
        gone = tuple(s for s in SCHOOLS if any(school_of(db, c) == s for c, _ in chips))
        kind, groups = LOSS_NOW, by_school(chips, gone)
    else:
        gone = tuple(s for s in SCHOOLS if s in sc.listed and s not in after)
        with_wanted = tuple(s for s in gone if any(school_of(db, c) == s for c, _ in wanted))
        if len(with_wanted) <= limit - len(after):
            return None
        chips = tuple((c, t) for c, t in wanted if school_of(db, c) in gone)
        kind, groups = (LOSS_ONE_LEFT if school not in sc.listed else LOSS_TIP), by_school(chips, with_wanted)
    lost = lost_kind(sc, e, place, now)
    return Loss(kind, bool(lost), after, gone, chips, groups, lost, final=sc.picks.total <= 0)


def _quoted(names) -> str:
    """每个名字各加一对「」：「治愈」「标记」。"""
    return "".join(f"「{n}」" for n in names)


def lost_text(lost) -> str:
    """附注里拿不到的必选、推荐：「必选「A」「B」、推荐「C」」（按方案的先后）；超过 SCHEME_LOST_MAX 个时只写前几个，
    后接「等 N 个」（N 是全部的个数）。"""
    shown = lost[:SCHEME_LOST_MAX]
    groups = []
    for tier in TIERS:
        names = [c for c, t in shown if t == tier]
        if names:
            groups.append(SCHEME_TIER_WORD[tier] + _quoted(names))
    text = "、".join(groups)
    if len(lost) > SCHEME_LOST_MAX:
        text += SCHEME_LOST_MORE.format(n=len(lost))
    return text


def _scheme_notes(db: DB, scorer: Scorer) -> list[SchemeNote]:
    """自己的方案在推荐区的附注（排在附注最前面）：

    - 方案锁住的流派已经进入（已有筹码里有它的，走偏）：「已进入方案外的流派「X」」，橙色；识别认错、在「已有筹码」里改掉
      以后自然消失；
    - 流派名额占满、方案里有没持有的必选和推荐属于其余流派（中途换方案、走偏、列了三个以上流派等）：「已进入「A」「B」流派，
      筹码方案中的必选「X」、推荐「Y」本局不再出现」。两个已进入的流派都列在方案里时灰色，进了方案外的流派时橙色。"""
    plan = scorer.plan
    entered = scorer.entered
    out = []
    strayed = tuple(s for s in entered if s in scorer.scheme_locked)
    if strayed:
        out.append(SchemeNote(NOTE_STRAYED, SCHEME_STRAYED_NOTE.format(schools=_quoted(strayed)), True))
    if len(entered) >= db.rules["pool"]["max_schools"]:
        owned = set(scorer.known_owned)
        lost = [(c, t) for c, t in plan.tiers if t in ("core", "first") and c not in owned
                and school_of(db, c) is not None and school_of(db, c) not in entered]
        if lost:
            warn = any(s not in scorer.listed for s in entered)
            out.append(SchemeNote(NOTE_LOST, SCHEME_LOST_NOTE.format(entered=_quoted(entered), chips=lost_text(lost)), warn))
    return out


# ------------------------------------------------------------ 先后


def _need_second_scheme(db: DB, scorer: Scorer, cands: list[ChipEval], unmarked) -> ChipEval | None:
    """自己的方案里的队友刚需：只在按总分排先后的未标记筹码之间调（cands 是卡片上的先后），不越过档位。
    排前两位的和被提的都是未标记的（unmarked(e) 为真）时才提，提法和默认推荐相同（advice 的 _need_second：总分提到第一、
    第二之间），在未标记里排第二；不和「刷新」比（刷新排在哪里另按方案的规矩定）。之后拿不到的往后、插进来的「刷新」
    还可能挪动它，卡片上最后排第几由 _need_place 写进理由。返回被提的筹码。"""
    needs = mate_needs(db, scorer.st)
    if not needs or len(cands) < 3 or not (unmarked(cands[0]) and unmarked(cands[1])):
        return None
    first, second = cands[0].total, cands[1].total
    if first - second < 0.02:
        return None
    picked = need_pick(db, scorer, needs, [e for e in cands[2:] if unmarked(e)])
    if picked is None:
        return None
    e, givers = picked
    lift_need(e, givers, second + min(NEED_LEAD, (first - second) / 2) - e.total)
    return e


def _need_place(e: ChipEval, n: int) -> None:
    """队友刚需提的那张（_need_second_scheme）：提的时候排到未标记里的第二位，之后「拿不到的往后」、插进来的「刷新」还可能
    挪动它。最后不在卡片上第二位（n 是卡片上第几行，含「刷新」那一行）时，「队友」那一项整句里的「排在第二位，第一位仍按
    自身收益」改写成实际的位次（NEED_PLACE），和卡片上看到的一致。"""
    if n == 2:
        return
    place = NEED_PLACE.format(n=_PLACE_NUMBERS.get(n, str(n)))
    e.parts = [replace(p, reason=p.reason.replace(NEED_SECOND, place)) if p.origin == NEED_ORIGIN else p
               for p in e.parts]


def _series_near_scheme(db: DB, cands: list[ChipEval], unmarked, lifted: ChipEval | None) -> list[ChipEval]:
    """自己的方案里同系列两张都是未标记、在卡片上相邻、总分相差不到 rules.toml [advice] series_near 时，稀有度高的一张
    排在前面（同 advice.series_near，只在按总分排先后的未标记筹码之间调；被队友刚需提到第二位的那张不调）。返回调好的先后。
    卡片上不写推荐度，排到前面那张的对比行悬停后接 SERIES_NEAR_TIP_SCHEME（不是默认推荐的 SERIES_NEAR_TIP）。"""
    gap = db.rules["advice"].get("series_near", 0)
    out = list(cands)
    if gap <= 0:
        return out
    rank = {r: i for i, r in enumerate(RARITIES)}
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        pair = next((p for p in db.series if p.low == a.chip.name and p.high == b.chip.name), None)
        if (pair is not None and unmarked(a) and unmarked(b) and lifted is not a and lifted is not b
                and rank.get(b.chip.rarity, -1) > rank.get(a.chip.rarity, -1) and a.total - b.total < gap):
            out[i], out[i + 1] = b, a
            near = SERIES_NEAR_TIP_SCHEME.format(x=b.chip.name)
            b.versus = [(o, g, t + near if o == a.chip.name else t) for o, g, t in b.versus]
            i += 2
            continue
        i += 1
    return out


def _scheme_percents(db: DB, evals: list[ChipEval], reroll: RerollEval | None, order: list[int],
                     places: list[Place]) -> None:
    """自己的方案里的推荐度（只记在推荐日志里；卡片上写位次，命令行和诊断包写位次和总分），和卡片上的先后一致。
    places 是排先后用的位次（收入降档的按低一档，effective）：

    - 排第一的是必选：它 100%，其余 0%（几张必选时只有顺位最前的一张，都写 100% 就看不出该拿哪张）；
    - 候选都是不选（含被锁的）：有刷新次数时刷新 100%、候选 0%；没有时都是 0%（推荐度合计不为 100% 的唯一情形）；
    - 其余：按总分（刷新按扣除代价后的期望）算出推荐度，从高到低依次分给卡片上不是不选的选项；不选、被锁的 0%。"""
    for e in evals:
        e.percent = 0.0
    if reroll is not None:
        reroll.percent = 0.0
    options = [k for k in order if k == REROLL_SLOT or not evals[k].superseded_by]
    levels = {k: place_level(places[k]) for k in options if k != REROLL_SLOT}
    if not options:
        return
    first = options[0]
    if first != REROLL_SLOT and levels[first] == TIER_LEVEL["core"]:
        evals[first].percent = 1.0
        return
    if all(v == 0 for v in levels.values()):
        if reroll is not None:
            reroll.percent = 1.0
        return
    eligible = [k for k in options if k == REROLL_SLOT or levels[k] > 0]
    values = [reroll.value if k == REROLL_SLOT else evals[k].total for k in eligible]
    shares = sorted(softmax(values, db.rules["softmax"]["temperature"]), reverse=True)
    for k, p in zip(eligible, shares):
        if k == REROLL_SLOT:
            reroll.percent = p
        else:
            evals[k].percent = p


def demotion(db: DB, scorer: Scorer, e: ChipEval, place: Place) -> Demotion | None:
    """收入降档：标了推荐、可选的候选，只按剩下几次兑现折算的那一层扣掉的比例（parts.income_cut 的 left_cut：星光、星币按
    剩余轮次或进度折算，扣分 ÷ 筹码的正分，不含成长分，和折算同一个基数）不小于 rules.toml [scheme] income_demote 时按低一档
    排（推荐当可选、可选当未标记）。满 3 星后非经济型角色收入再减半、晚拿的银行卡再减半，这两层照旧算进总分，但不算进这个
    比例：满 3 星不等于收入快用不上（第 7 轮刚满 3 星时还有好几次兑现）。轮次未知时没有剩余兑现那一层，不降档。
    必选不扣收入折算，也不降档；不选不降。比较按 tie_key。"""
    if e.unrecorded or place.kind != PLACE_MARKED or place.tier not in DEMOTE_TO:
        return None
    found = income_cut(scorer, e.chip, e.parts, scorer.st.round, frozenset(scorer.known_owned))
    if found is None or tie_key(found.left_cut) < tie_key(db.rules["scheme"]["income_demote"]):
        return None
    return Demotion(DEMOTE_TO[place.tier], found.left_cut, found.brief, found.part.reason)


def effective(place: Place, demoted: Demotion | None) -> Place:
    """排先后、和「刷新」比时用的位次：收入降档的按低一档（推荐当可选、可选当未标记），其余照原来的位次。
    卡片上照写原来的位次（Ranking.places）。"""
    if demoted is None:
        return place
    if demoted.tier is None:
        return Place(PLACE_UNMARKED)
    return Place(PLACE_MARKED, demoted.tier, place.rank)


def _beats(a: float, b: float, near: float) -> bool:
    """档位前瞻 b 比 a 高出不少于 near（相差正好等于线也算），并且确实更高（near 写成 0 或负数时不会两张来回对调）。"""
    diff = tie_key(b - a)
    return diff > 0 and diff >= tie_key(near)


def shown_rate(x: float) -> int:
    """卡片上写的百分数（「12%」）里的那个整数：和卡片同一个取整（f"{x:.0%}"，lookahead.tier_ahead、ui.text 的写法）。"""
    return int(f"{x:.0%}"[:-1])


def rate_key(ahead: TierAhead | None) -> tuple[int, int | None] | None:
    """卡片上档位前瞻那一条写的数，按卡片的取整：（选了以后，下一次三选一里至少出现一张必选、推荐（方案里没有这样的必选、
    推荐时为可选）的机会（TierAhead.after）, 另写的可选那一个「选了以后」的数（TierAhead.second，没有时为 None））。
    卡片上不写这个数（TierAhead.words 为空：没有可说的、之后没有三选一等）时为 None。同一次三选一的几张按同一组筹码说
    （「选之前」的数相同），比「选了以后」的就够了。"""
    if ahead is None or not ahead.words:
        return None
    return shown_rate(ahead.after), (shown_rate(ahead.second[1]) if ahead.second is not None else None)


def _rate_ok(a: tuple[int, int | None] | None, b: tuple[int, int | None] | None) -> bool:
    """b 卡片上的数（rate_key）不比 a 低：先比必选、推荐那一个；取整后相同、两张都另写了可选的时再比可选（这时卡片的
    悬停另接一句可选的，ui.text._move_rates）。任一张不写这个数时不比（卡片上没有会对不上的数字）。"""
    if a is None or b is None:
        return True
    if b[0] != a[0]:
        return b[0] > a[0]
    return a[1] is None or b[1] is None or b[1] >= a[1]


def _passes(value, rate, a: ChipEval, b: ChipEval, near: float) -> bool:
    """同一档里顺位靠后的 b 越过顺位、排到 a 前面：两样都更好——档位前瞻 value 高出不少于 near（_beats），并且卡片上写的
    至少出现一张的机会 rate 不比 a 低（_rate_ok，按卡片的取整比，卡片上的数字和先后一定对得上）。两样方向相反时照顺位。"""
    return _beats(value(a), value(b), near) and _rate_ok(rate(a), rate(b))


def _same_tier(cands: list[ChipEval], native, value, rate, near: float) -> list[ChipEval]:
    """同一档（推荐、可选）的几张按档位前瞻排先后：先照顺位（cands 已按顺位排好），相邻两张里后一张两样都更好（_passes：
    档位前瞻比前一张高出不少于 near，卡片上的数不比前一张低）时两张对调，重复到没有可调的（冒泡；一次三选一最多三张）。
    相差不到 near、或两样方向相反时照顺位。只比相邻的两张，不隔着中间一张比：例如顺位 1、2、3 的档位前瞻是 0、2.5、5
    （near 为 3）时，2 越不过 1、3 也越不过 2，照顺位，哪怕 3 比 1 高出 5。只在原本就是这一档的候选之间调（native(e, 档位)），降档进来的排在那一档最后、
    不参加。value(e) 是档位前瞻，rate(e) 是卡片上写的至少出现一张的机会（rate_key）。对调过的两张，后来不会再换回去（换回去
    要原来那张的档位前瞻反过来高出 near），所以排到前面的每一张都直接比过、两样都更好。"""
    out = list(cands)
    for tier in SAME_TIER_TIERS:
        at = [k for k, e in enumerate(out) if native(e, tier)]
        group = [out[k] for k in at]
        changed = len(group) > 1
        while changed:
            changed = False
            for k in range(len(group) - 1):
                if _passes(value, rate, group[k], group[k + 1], near):
                    group[k], group[k + 1] = group[k + 1], group[k]
                    changed = True
        for k, e in zip(at, group):
            out[k] = e
    return out


def _moves(cands: list[ChipEval], native, slot, places, value, rate, lost, near: float, later: bool,
           count: int) -> list[tuple[Move, ...]]:
    """同一档（推荐、可选）按档位前瞻排先后的说明（Move），按卡片上最后的先后（cands）看：只比同一档、拿不到的情况相同
    （lost(e) 相同）的几张，它们之间的先后才是档位前瞻定的。排到顺位更前那几张前面的写 MOVE_AHEAD，被排到后面的写
    MOVE_BEHIND；照顺位排在最前的那张和紧跟的一张比：两样都更好（_passes：档位前瞻高出不少于 near，卡片上的数 rate 不比它
    低）为 MOVE_LEAD；档位前瞻有一张高出不少于线、卡片上的数却是另一张高（两样方向相反，照顺位）为 MOVE_MIXED；其余（相差
    不到线）MOVE_NEAR；预计之后没有三选一（later 为假，档位前瞻都是 0）时为 MOVE_LAST。同一档只有一张时不写。count 是候选的
    个数（Advice.evals）。"""
    out: list[tuple[Move, ...]] = [() for _ in range(count)]

    def rank(e: ChipEval) -> int:
        return places[slot[id(e)]].rank

    for tier in SAME_TIER_TIERS:
        members = [e for e in cands if native(e, tier)]
        for group in dict.fromkeys(lost(e) for e in members):
            sub = [e for e in members if lost(e) == group]
            if len(sub) < 2:
                continue
            for k, e in enumerate(sub):
                passed = sorted((x for x in sub[k + 1:] if rank(x) < rank(e)), key=rank)
                passers = [x for x in sub[:k] if rank(x) > rank(e)]
                moves = []
                if passed:
                    moves.append(Move(MOVE_AHEAD, tuple(slot[id(x)] for x in passed)))
                if passers:
                    moves.append(Move(MOVE_BEHIND, tuple(slot[id(x)] for x in passers)))
                if k == 0 and not passed:
                    nxt = sub[1]
                    if not later:
                        kind = MOVE_LAST
                    elif _passes(value, rate, nxt, e, near):
                        kind = MOVE_LEAD
                    elif (_beats(value(e), value(nxt), near) and not _rate_ok(rate(e), rate(nxt))
                          or _beats(value(nxt), value(e), near) and not _rate_ok(rate(nxt), rate(e))):
                        # 档位前瞻有一张高出不少于线，卡片上的数却是另一张高：两样方向相反，照顺位
                        kind = MOVE_MIXED
                    else:
                        kind = MOVE_NEAR
                    moves.append(Move(kind, (slot[id(nxt)],)))
                out[slot[id(e)]] = tuple(moves)
    return out


def advise_scheme(db: DB, scorer: Scorer, offer: list[str], evals: list[ChipEval], unrecorded: list[int]) -> Advice:
    """按自己的方案给出推荐（advise 的 scheme 不为 None）。分值照 Scorer（档位代替玩家思路和角色特性，没标的不用默认推荐），
    先后按档位，依次：

    1. 位次（Scorer.place）；同系列按档位：低一级的档位高于高一级（含高一级没标）时不算上位替代，两张照档位比
       （advice.mark_series 的 keep）；
    2. 收入降档（demotion，只按剩下几次兑现算）定下每张的有效档（effective）；
    3. 按有效档排：必选 > 推荐 > 可选 > 未标记 > 不选 > 被锁的其他流派。必选照顺位；推荐、可选先照顺位，再按档位前瞻
       调（_same_tier：档位前瞻和卡片上写的至少出现一张的机会两样都更好才越过顺位），降档进来的排在那一档最后（按原来的顺位）；
       未标记之间按总分，队友刚需和同系列总分接近只在未标记之间调；不选按顺位、被锁的按总分；
    4. 拿不到的往后（Scorer.loss 的 lost）：不选、被锁以外的候选稳定分组，没有拿不到的在前、保持原来的先后，然后是只少了
       推荐的，最后是少了必选的；不选、被锁的照旧在最后。拿不到的记下排在它前面、能保住的是哪几张（Loss.behind），
       前面没有能保住的时记下排在它后面、能保住的不选、被锁的（Loss.keep_last）；
    5. 「刷新」和排在最前的候选比（Scorer.reroll_view，收入降档的按低一档比）：插在它胜过的第一张候选前面，三张都是不选
       时排第一；队友刚需提的那张最后不在卡片上第二位时，理由里写实际的位次（_need_place）；
    6. 推荐度和先后一致（_scheme_percents，只给命令行、诊断包排查用）；同一档的说明（_moves）；附注（_scheme_notes）。"""
    st = scorer.st
    places = [scorer.place(e) for e in evals]
    place_of = {e.chip.name: places[i] for i, e in enumerate(evals) if not e.unrecorded}

    def keep(low: str, high: str) -> bool:
        # 档位相同或高一级的更高时照旧算上位替代；低一级的高时两张照档位比
        return place_class(place_of[high]) <= place_class(place_of[low])

    active = mark_series(db, evals, scorer.starlight_use(), keep=keep)
    slot = {id(e): i for i, e in enumerate(evals)}
    demotions = [None if e.superseded_by else demotion(db, scorer, e, places[i]) for i, e in enumerate(evals)]
    eff = [effective(p, d) for p, d in zip(places, demotions)]

    def unmarked(e: ChipEval) -> bool:
        i = slot[id(e)]
        return demotions[i] is None and place_class(places[i]) == _UNMARKED_CLASS

    def native(e: ChipEval, tier: str) -> bool:
        i = slot[id(e)]
        return demotions[i] is None and places[i].kind == PLACE_MARKED and places[i].tier == tier

    def key(e: ChipEval):
        i = slot[id(e)]
        if demotions[i] is not None:  # 降档的排在那一档已有的候选后面，按原来的顺位
            return place_class(eff[i]), 1, places[i].rank, i
        place = places[i]
        within = place.rank if place.kind == PLACE_MARKED else -tie_key(e.total)
        return place_class(place), 0, within, i

    cands = sorted(active, key=key)
    lifted = _need_second_scheme(db, scorer, cands, unmarked)
    if lifted is not None:
        cands = sorted(active, key=key)
    cands = _series_near_scheme(db, cands, unmarked, lifted)
    owned = scorer.known_owned
    aheads = [None if e.superseded_by or e.unrecorded or places[i].kind != PLACE_MARKED
              else scorer.tier_ahead(e.chip.name, owned) for i, e in enumerate(evals)]

    def value(e: ChipEval) -> float:
        return aheads[slot[id(e)]].value

    def rate(e: ChipEval):
        return rate_key(aheads[slot[id(e)]])

    near = db.rules["scheme"]["same_tier_near"]
    cands = _same_tier(cands, native, value, rate, near)
    # 拿不到的往后（不选、被锁的不动，本来就在最后）
    now = reach(scorer, owned)
    losses = [None if e.superseded_by else scorer.loss(e, places[i], now) for i, e in enumerate(evals)]

    def lost(e: ChipEval) -> str:
        found = losses[slot[id(e)]]
        return found.lost if found is not None else ""

    head = [e for e in cands if place_class(eff[slot[id(e)]]) not in _LAST_CLASSES]
    tail = [e for e in cands if place_class(eff[slot[id(e)]]) in _LAST_CLASSES]
    cands = sorted(head, key=lambda e: _LOST_GROUP[lost(e)]) + tail
    for k, e in enumerate(cands[:len(head)]):
        if lost(e):  # 排在它前面、能保住的（拿不到的情况更好的）几张；没有时记下能保住的不选、被锁的（它们照旧排在最后）
            group = _LOST_GROUP[lost(e)]
            behind = tuple(slot[id(x)] for x in cands[:k] if _LOST_GROUP[lost(x)] < group)
            last = () if behind else tuple(slot[id(x)] for x in tail if _LOST_GROUP[lost(x)] < group)
            losses[slot[id(e)]] = replace(losses[slot[id(e)]], behind=behind, keep_last=last,
                                          alone=not (behind or last))
    moves = _moves(cands, native, slot, places, value, rate, lost, near, scorer.lookahead_times() > 0, len(evals))
    top_eval = cands[0]
    top = slot[id(top_eval)]
    reroll = scorer.reroll(offer, top_eval.total)
    view = None
    order = [slot[id(e)] for e in cands]
    if reroll is not None:
        view, beats = scorer.reroll_view(offer, cands, [eff[slot[id(e)]] for e in cands], top, reroll.value)
        at = 0 if view.all_avoid else next((k for k, b in enumerate(beats) if b), len(cands))
        order.insert(at, REROLL_SLOT)
        view.first = at == 0
    if lifted is not None:
        _need_place(lifted, order.index(slot[id(lifted)]) + 1)
    order += [i for i, e in enumerate(evals) if e.superseded_by]
    best = REROLL if order[0] == REROLL_SLOT else top_eval.chip.name
    _scheme_percents(db, evals, reroll, order, eff)
    snotes = _scheme_notes(db, scorer)
    ranking = Ranking(
        order=order, places=places, losses=losses, reroll=view, notes=snotes, listed=scorer.listed,
        locked=tuple(s for s in scorer.scheme_locked if s not in scorer.entered),
        strayed=tuple(s for s in scorer.entered if s in scorer.scheme_locked), targets=scorer.targets,
        entered=scorer.entered, aheads=aheads, moves=moves, demotions=demotions,
    )
    return Advice(
        state=st, evals=evals, reroll=reroll, best=best,
        notes=[n.text for n in snotes] + common_notes(db, scorer, offer, unrecorded), picks=scorer.picks,
        maps=tuple(m for m in scorer.maps if m), tendency=scorer.tendency, unrecorded=unrecorded, top=top,
        scheme=scorer.plan, scheme_schools=scorer.targets, warn_notes=[n.text for n in snotes if n.warn],
        ranking=ranking,
    )
