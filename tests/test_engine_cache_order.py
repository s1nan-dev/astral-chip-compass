"""打分的缓存（Scorer.memo，advisor.engine.context.Memo）按不同顺序填写时结果必须逐位相同。

同一个局面用一个新建的 Scorer 按固定顺序算一遍作为基准；每种打乱的顺序（候选倒序、先查刷新再查候选、从中间转着查、
重复查询同一候选）各用一个新建的 Scorer 从头查起，结果必须和基准逐位相同（缓存按什么顺序填进去都不影响结果）；
基准那个 Scorer 算完以后，在它上面再按每种顺序查一遍，结果同样不变（读缓存和现算一致）。
按地图分开算的调用（score、future、前瞻、三个概率）对每张候选地图各查一次，和默认地图、按候选地图平均的那一次
混在一起打乱：缓存键漏了地图时，先查到哪一个就会串成哪一个。基准只在同一进程里比，不受哈希种子影响。
这条测试只能查出「换个顺序结果就变」的错；每种顺序都错得一样的（例如某个缓存一直取不到、每次都现算出同一个错值）
要靠 test_engine 等按数值核对的测试和基准比对。

覆盖 Memo 的十三项缓存：score、parts、setup、values、future、endless、endless_chain、knife、heal、partner、reroll、
lookahead_keys、survival（另有只算一次的 picks），按筹码方案计算时另有档位前瞻的 tier_future、tier_values（SCHEME_CACHES）。
按名字检查它们确实被写到过，缓存改了组织方式时 CACHES 跟着改。endless_chain（循环往复给的刷新里多出来的那几次按下一次三选一
的连刷递推估值）在之后的三选一的筹码池里有循环往复、刷新次数加上它给的以后有多出来的局面里写到。
"""
import unittest

from advisor.data import load_db
from advisor.engine import Plan, Scorer
from advisor.engine.chances import heal_chance, heal_knife, knife_chance, partner_chance
from advisor.engine.lookahead import future_setup, lookahead_on, tier_future
from advisor.engine.parts import survival_values
from advisor.engine.reroll import reroll_dists
from tests.test_engine_sanity import corpus

# 每项缓存（Scorer.memo 上的名字）：这批局面算过一遍之后应当非空（覆盖检查用）
CACHES = ("score", "parts", "setup", "values", "future", "endless", "endless_chain", "knife", "heal", "partner", "reroll",
          "lookahead_keys", "survival")
# 只在按筹码方案计算时用的缓存（档位前瞻）
SCHEME_CACHES = ("tier_future", "tier_values")
# 三个概率的缓存：至少有一个局面里同一个 Scorer 缓存了不同的值。全是 0（之后没有三选一、筹码都够不上推荐线）时，
# 键漏了地图或筹码也串不出差别，检查等于空跑
CHANCE_CACHES = ("knife", "heal", "partner")
# 先查刷新一侧：刷新、前瞻、概率等先用已有筹码和全部候选地图算，之后才逐个候选查分项和总分
REFRESH_PREFIXES = ("reroll", "future", "setup", "picks", "neutral", "survival", "knife", "heal", "partner")
STRIDE = 6  # corpus 每隔 6 个取一个局面，共 59 个
# 另外固定加进来的 corpus 序号：多张候选地图、美工刀（和配套筹码）的概率按地图分开算各不相同（隔 6 个取的局面里
# 没有美工刀的这种）。资料或 corpus 改了以后不再是这样时，test_order_independent 的概率缓存检查会报出来，换几个
EXTRA = (88, 124, 243)


def _best(scorer, st, offer) -> float:
    """三个候选里最高的总分（reroll 和 reroll_p_better 的比较对象）。"""
    owned = scorer.known_owned
    values = [scorer.score(n, owned, st.round) for n in offer if n in scorer.db.chips]
    return max(values) if values else 0.0


def build_probes(scorer, st, offer, rarity) -> list:
    """对局面上所有会读写缓存的调用，返回（键, 取值函数）的列表。键以调用名开头（供 REFRESH_PREFIXES 分类）。

    每个取值都 repr 出来：浮点用 repr 往返精确保存，逐位比较。同一局面键唯一（按候选序号）。
    """
    db = scorer.db
    owned = scorer.known_owned
    recorded = [n for n in offer if n in db.chips]
    probes: list = []
    for i, name in enumerate(recorded):
        probes.append((f"parts:{i}", lambda n=name: repr(scorer.parts(n, owned, st.round))))
        probes.append((f"score:{i}", lambda n=name: repr(scorer.score(n, owned, st.round))))
        probes.append((f"lookahead:{i}", lambda n=name: repr(scorer.lookahead(n, owned))))
        probes.append((f"lookahead_reason:{i}", lambda n=name: repr(scorer.lookahead_reason(n, owned))))
        probes.append((f"lookahead_short:{i}", lambda n=name: repr(scorer.lookahead_short(n, owned))))
        probes.append((f"official_raw:{i}", lambda n=name: repr(scorer.official_raw(n, owned, st.round))))
        probes.append((f"future:{i}", lambda n=name: repr(scorer.future((*owned, n)))))
        probes.append((f"future_zero:{i}", lambda n=name: repr(scorer.future((*owned, n), zero=n))))
        # 按地图分开查（推荐里前瞻、刷新都按候选地图的先后算，总是先碰到第一张；分开查才会先碰到别的地图）
        for m in scorer.maps:
            probes.append((f"future_on:{i}:{m!r}", lambda n=name, m=m: repr(scorer.future((*owned, n), map_name=m))))
            probes.append((f"lookahead_on:{i}:{m!r}", lambda n=name, m=m: repr(lookahead_on(scorer, n, owned, m))))
        if scorer.plan is not None:
            # 档位前瞻（Memo.tier_future、tier_values）：键和前瞻的 future 一样、值不一样，混在一起查才看得出串没串
            probes.append((f"tier_lookahead:{i}", lambda n=name: repr(scorer.tier_lookahead(n, owned))))
            probes.append((f"tier_ahead:{i}", lambda n=name: repr(scorer.tier_ahead(n, owned))))
            for m in scorer.maps:
                probes.append((f"tier_future_on:{i}:{m!r}",
                               lambda n=name, m=m: repr(tier_future(scorer, (*owned, n), map_name=m))))
                probes.append((f"tier_future_zero:{i}:{m!r}",
                               lambda n=name, m=m: repr(tier_future(scorer, owned, zero=n, map_name=m))))
    probes.append(("neutral", lambda: repr(scorer.neutral_score(rarity[0] if rarity else None, owned, st.round))))
    probes.append(("survival", lambda: repr(survival_values(scorer))))
    probes.append(("picks", lambda: repr(scorer.picks)))
    for m in scorer.maps:
        probes.append((f"setup:{m!r}", lambda m=m: repr(future_setup(scorer, owned, m))))
    probes.append(("reroll", lambda: repr(scorer.reroll(offer, _best(scorer, st, offer)))))
    probes.append(("reroll_p_better", lambda: repr(scorer.reroll_p_better(offer, _best(scorer, st, offer)))))
    probes.append(("reroll_levels", lambda: repr(scorer.reroll_levels(offer))))
    probes.append(("reroll_dists", lambda: repr(reroll_dists(scorer, offer))))
    # 美工刀、治愈来源、配套筹码的概率（各自的缓存 memo.knife、memo.heal、memo.partner）：按候选地图平均的一次
    # （地图参数为 None），以及每张候选地图各一次（分项里的预判、刷新和前瞻按地图算时就这样调用）。
    # 治愈来源的概率在推荐里按各把美工刀分别算，这里也按第一个候选和各把美工刀分别查；配套筹码取开套流派
    # （[charge_build] school）里除已有和自己以外的筹码，和推荐里的算法一样有机会达到推荐线（概率不为 0）
    cfg = heal_knife(scorer)
    owned_set = frozenset(owned)
    knives = [k for k in cfg["knives"] if k in db.chips and k not in owned_set] if cfg is not None else []
    heal_names = list(dict.fromkeys((*recorded[:1], *knives)))
    school = (db.rules.get("charge_build") or {}).get("school")
    partners = frozenset(n for n, c in db.chips.items() if school and c.school == school) - owned_set - set(recorded[:1])
    partners = partners or frozenset(recorded[1:2]) or frozenset(recorded[:1])
    for m in dict.fromkeys((None, *scorer.maps)):  # 没有候选地图时 maps 是 (None,)，不重复
        tag = "" if m is None else f":{m!r}"
        if cfg is not None:
            probes.append((f"knife_chance{tag}", lambda m=m: repr(knife_chance(scorer, owned_set, cfg, m))))
        for j, name in enumerate(heal_names):
            probes.append((f"heal_chance:{j}{tag}", lambda n=name, m=m: repr(heal_chance(scorer, n, owned_set, m))))
        if recorded:
            probes.append((f"partner_chance{tag}", lambda m=m: repr(
                partner_chance(scorer, recorded[0], partners, owned_set, m))))
    # 各候选在每张候选地图上的总分（memo.score、memo.parts 的键含地图；地图待识别时 score:i 按 None 算）
    for m in scorer.maps:
        for i, name in enumerate(recorded):
            probes.append((f"score_on:{i}:{m!r}", lambda n=name, m=m: repr(scorer.score(n, owned, st.round, m))))
    return probes


def run_probes(probes, keys) -> dict:
    """按 keys 的顺序调用各取值函数，返回 {键: 结果}。"""
    by_key = dict(probes)
    out = {}
    for key in keys:
        out[key] = by_key[key]()
    return out


ORDER_FUNCS = {
    "normal": lambda keys: list(keys),
    "reversed": lambda keys: list(reversed(keys)),
    # 先查刷新一侧（刷新、前瞻、概率），再按倒序查各候选
    "refresh_first": lambda keys: [k for k in keys if k.startswith(REFRESH_PREFIXES)]
    + [k for k in reversed(keys) if not k.startswith(REFRESH_PREFIXES)],
    # 从三分之一处转着来，最后的候选排到最前
    "rotate": lambda keys: keys[len(keys) // 3:] + keys[:len(keys) // 3],
    # 每个调用重复两次（重复查询同一候选）
    "doubled": lambda keys: list(keys) + list(keys),
}


def cases(db):
    """固定种子的一批局面（test_engine_sanity 的 corpus）：地图已知和待识别（含多张候选地图）、有无队友各有覆盖。"""
    every = corpus(db)
    return every[::STRIDE] + [every[i] for i in EXTRA if i % STRIDE]


def scheme_for(db, offer) -> Plan | None:
    """从候选里取资料库收录的，按必选、推荐、可选各标一个，做一套筹码方案。"""
    recorded = [n for n in offer if n in db.chips]
    if not recorded:
        return None
    tiers = tuple((n, t) for n, t in zip(recorded, ("core", "first", "second")))
    return Plan(name="顺序测试方案", tiers=tiers)


class CacheOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cases = cases(cls.db)

    def test_cases_cover_dimensions(self):
        # 抽到的局面确实覆盖了地图（已知 / 待识别）和队友（有 / 无），免得改了种子或生成方法后悄悄变成一种；
        # 多张候选地图、并且角色带美工刀设置的局面要有几个（概率按地图分开算和平均算的键才会碰到一起）
        states = [st for _, st, _, _ in self.cases]
        self.assertEqual({st.map is None for st in states}, {True, False})
        self.assertEqual({bool(st.teammates) for st in states}, {True, False})
        self.assertGreaterEqual(len(self.cases), 50)
        scorers = [Scorer(self.db, st, offer=offer) for _, st, offer, _ in self.cases]
        both = [sc for sc in scorers if len(sc.maps) > 1 and heal_knife(sc) is not None]
        self.assertGreaterEqual(len(both), 3)

    def test_order_independent(self):
        touched: set = set()
        varied: set = set()
        for hero, st, offer, rarity in self.cases:
            with self.subTest(hero=hero, offer=offer):
                written, differ = self.check(st, offer, rarity)
                touched |= written
                varied |= differ
        # 十三个缓存都确实被这批局面写到过，三个概率的缓存里有过不同的值（免得某个缓存的检查悄悄变成空跑）
        self.assertEqual(set(CACHES) - touched, set(), "这几个缓存一个局面都没写到")
        self.assertEqual(set(CHANCE_CACHES) - varied, set(),
                         "这几个概率缓存在每个局面里都只存了同一个值：换几个 EXTRA（多张候选地图、角色带美工刀设置、之后还有三选一）")

    def test_order_independent_with_scheme(self):
        # 按筹码方案计算时同样顺序无关（方案分支用 parts._scheme_school_parts 等，填的还是这十三项缓存，另有档位前瞻的
        # tier_future、tier_values 两项）
        checked = 0
        touched: set = set()
        for hero, st, offer, rarity in self.cases[::8]:
            scheme = scheme_for(self.db, offer)
            if scheme is None:
                continue
            with self.subTest(hero=hero, offer=offer, scheme=True):
                written, _ = self.check(st, offer, rarity, scheme=scheme)
                touched |= written
            checked += 1
        self.assertGreaterEqual(checked, 5)
        self.assertEqual(set(SCHEME_CACHES) - touched, set(), "档位前瞻的缓存一个局面都没写到")

    def check(self, st, offer, rarity, scheme=None) -> tuple[set, set]:
        """按每种顺序各建一个 Scorer 查一遍、和第一种（normal）逐位比；再在第一种的那个 Scorer 上按每种顺序重查。
        返回（写到过的缓存名, 缓存了不同的值的概率缓存名）。"""
        db = self.db
        reference = first = None
        touched, varied = set(), set()
        for label, order in ORDER_FUNCS.items():
            scorer = Scorer(db, st, offer=offer, scheme=scheme)
            probes = build_probes(scorer, st, offer, rarity)
            values = run_probes(probes, order([key for key, _ in probes]))
            touched |= {c for c in (*CACHES, *SCHEME_CACHES) if getattr(scorer.memo, c)}
            varied |= {c for c in CHANCE_CACHES if len(set(getattr(scorer.memo, c).values())) > 1}
            if reference is None:
                reference, first = values, probes
            else:
                self.assertEqual(self.diff(reference, values), [], f"新建的 Scorer 按 {label} 顺序查，结果不同")
        # 同一个 Scorer（已按 normal 顺序算过一遍）上按每种顺序再查：读缓存和现算一致
        keys = [key for key, _ in first]
        for label, order in ORDER_FUNCS.items():
            again = run_probes(first, order(keys))
            self.assertEqual(self.diff(reference, again), [], f"同一个 Scorer 按 {label} 顺序重查，结果不同")
        return touched, varied

    @staticmethod
    def diff(reference: dict, values: dict) -> list:
        """结果不同的键（键的集合本身不同时也算）。"""
        return sorted(k for k in reference.keys() | values.keys() if reference.get(k) != values.get(k))


if __name__ == "__main__":
    unittest.main()
