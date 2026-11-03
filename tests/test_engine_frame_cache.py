"""一帧的分布缓存（advise 的可选参数 cache，advisor.engine.context.Cache）。

同一帧的几次推荐共用筹码池和分布，结果必须和各自单算的逐位相同；缓存的键漏掉任何一项输入，就会把别的局面的分布
借过来，这条测试要能查出来。

覆盖同一帧的三次调用（session 里的算法）：星数拿不准时按原星数和高一星各算一次；按方案计算时为了推荐日志里的对照
再按默认推荐算一次。共用缓存的一组、每个各建一个 Cache 的一组、完全不给 cache 的一组，三组逐位相同。

键的完整性：直接调 engine.pool_and_dists，逐项改动键里的输入（角色、已有筹码、地图、星数、来源、tier、概率行、最左格
稀有度、刷新标志、难度、排除的候选）。改动以后必须重新算——拿到的是新对象；若把哪一项从键里去掉，这一项改动就会
命中上一条缓存、返回同一个对象，assertIsNot 立刻失败。

FrameCombosTest：记账一帧里实际会出现的调用组合（方案、方案高一星，再加默认推荐按原星数或高一星；只用默认推荐时
原星数、高一星两次），在几类局面上（地图待识别带或不带候选地图、普通难度、画面读到概率行和最左格稀有度、任务来源、
候选里有未收录的、方案标了循环往复的档位、循环往复是候选）比整个 Advice 的 repr；帧里的调用正着、倒着各算一遍，
缓存按什么先后填进去都不影响结果。FrameOrderTest：test_engine_cache_order 的几种查询顺序各建一个 Scorer、共用一个
Cache，后面的 Scorer 按别的顺序读前面填进去的分布，结果和不给 cache 的逐位相同。
"""
import dataclasses
import json
import unittest

from advisor.data import NORMAL, load_db
from advisor.engine import Cache, Plan, Scorer, advise, pool_and_dists
from advisor.pool import State
from tests.test_engine_cache_order import ORDER_FUNCS, build_probes, run_probes
from tests.test_engine_cache_order import cases as order_cases
from tests.test_engine_sanity import corpus, fingerprint

STRIDE = 5  # corpus 每隔 5 个取一个局面，共 70 个（含地图已知和待识别）
ORDER_STRIDE = 4  # test_engine_cache_order 的局面每隔 4 个取一个


def snap(adv) -> tuple:
    """一次推荐的可比较输出：test_engine_sanity.fingerprint 加上方案模式的先后、位次和附注。

    Ranking 里有字典（各档位比例），过一遍 json（sort_keys 保证键的先后不影响比较；default=repr 兜住不是 JSON 类型的项）。
    """
    rank = None if adv.ranking is None else json.dumps(dataclasses.asdict(adv.ranking), sort_keys=True, default=repr)
    return (fingerprint(adv), tuple(adv.warn_notes), tuple(adv.scheme_schools), adv.default_best, rank)


def cases(db):
    return corpus(db)[::STRIDE]


def plan_for(db, offer) -> Plan | None:
    """从候选里取资料库收录的，按必选、推荐、可选各标一个，做一套筹码方案。"""
    recorded = [n for n in offer if n in db.chips]
    if not recorded:
        return None
    return Plan(name="一帧测试方案", tiers=tuple((n, t) for n, t in zip(recorded, ("core", "first", "second"))))


class FrameCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cases = cases(cls.db)

    def test_shared_cache_matches_single(self):
        """同一帧三次调用：共用缓存、各建一个缓存、不给缓存，三组逐位相同。"""
        for hero, st, offer, rarity in self.cases:
            plan = plan_for(self.db, offer)
            up = dataclasses.replace(st, star=min(3, st.star + 1))
            with self.subTest(hero=hero, offer=offer):
                shared = Cache()
                got = (
                    snap(advise(self.db, st, offer, rarity, scheme=plan, cache=shared)),
                    snap(advise(self.db, up, offer, rarity, scheme=plan, cache=shared)),
                    snap(advise(self.db, st, offer, rarity, cache=shared)),
                )
                single = (
                    snap(advise(self.db, st, offer, rarity, scheme=plan, cache=Cache())),
                    snap(advise(self.db, up, offer, rarity, scheme=plan, cache=Cache())),
                    snap(advise(self.db, st, offer, rarity, cache=Cache())),
                )
                plain = (
                    snap(advise(self.db, st, offer, rarity, scheme=plan)),
                    snap(advise(self.db, up, offer, rarity, scheme=plan)),
                    snap(advise(self.db, st, offer, rarity)),
                )
                self.assertEqual(got, single, "共用一帧缓存和各自单算的结果不同")
                self.assertEqual(got, plain, "共用一帧缓存和不给缓存的结果不同")

    def test_cache_reused_but_frames_independent(self):
        """同一帧里两次相同的调用共用分布（第二次不再算）；两帧之间不共用（各建一个 Cache）。"""
        _hero, st, offer, _rarity = self.cases[0]
        hero = self.db.heroes[st.hero]
        owned = tuple(n for n in st.owned if n in self.db.chips)
        args = dict(hero=hero, owned=owned, map_name=st.map, star=min(3, st.star + 1))
        first = Cache()
        pool_and_dists(self.db, **args, cache=first.dists)
        self.assertTrue(first.dists)
        again = pool_and_dists(self.db, **args, cache=first.dists)
        self.assertIs(again, next(iter(first.dists.values())), "同样的输入没有命中缓存")
        self.assertFalse(Cache().dists, "新建的一帧缓存应当是空的")

    def test_key_complete(self):
        """pool_and_dists 的键逐项核：改动任何一项输入都要重新算，不能拿到上一条缓存。"""
        db = self.db
        other = next(h for h in db.heroes if h != "凛")
        base = dict(hero=db.heroes["凛"], owned=("标靶",), map_name="异变图书馆", star=1, source="levelup",
                    tier=None, probabilities=None, left_rarity=None, reroll=False, difficulty=None, exclude=())
        changes = {
            "角色": dict(hero=db.heroes[other]),
            "已有筹码": dict(owned=("标靶", "书签")),
            "地图": dict(map_name="魔法学院"),
            "星数": dict(star=2),
            "来源": dict(source="task"),
            "概率行": dict(probabilities=(55, 35, 10)),
            "最左格稀有度": dict(left_rarity="紫"),
            "刷新标志": dict(reroll=True),
            "难度": dict(difficulty="normal"),
            "排除的候选": dict(exclude=("标靶",)),
        }
        cache: dict = {}
        first = pool_and_dists(db, **base, cache=cache)
        self.assertTrue(cache)
        self.assertIs(pool_and_dists(db, **base, cache=cache), first, "同样的输入没有命中缓存")
        for label, change in changes.items():
            self.check_key(label, {**base, **change}, first, cache)
        # tier 只在来源是任务时参与稀有度表：两边都按任务来源，只改 tier
        task = {**base, "source": "task", "tier": 1}
        t_cache: dict = {}
        t_first = pool_and_dists(db, **task, cache=t_cache)
        self.check_key("tier", {**task, "tier": 3}, t_first, t_cache)

    def check_key(self, label, args, first, cache):
        """改了一项输入后必须重新算：拿到新对象，且和现算的逐位相同。"""
        with self.subTest(改动=label):
            got = pool_and_dists(self.db, **args, cache=cache)
            self.assertIsNot(got, first, f"键漏了「{label}」：改了这一项还拿到上一条缓存的结果")
            fresh = pool_and_dists(self.db, **args)  # 不给缓存，现算一份
            self.assertEqual(tuple(map(repr, got)), tuple(map(repr, fresh)),
                             f"改动「{label}」之后，缓存里的和现算的不同")


class Counting(dict):
    """数命中次数的分布缓存（换进 Cache.dists）：pool_and_dists 用 get 取，取到已有的一条算一次命中。"""

    hits = 0

    def get(self, key, default=None):
        hit = super().get(key, default)
        if hit is not None:
            self.hits += 1
        return hit


def frame_situations(db) -> list[tuple]:
    """同一帧的组合要覆盖的几类局面：（说明, State, 候选, 稀有度）。corpus 里各取第一个符合的（都有刷新次数，刷新的分布
    按本次候选排除；都不到 3 星，高一星和原星数不同），另加两个手工的。"""
    endless = db.rules["endless_cycle"]["chip"]
    wants = {
        "地图待识别、带几张候选地图": lambda st, offer: st.map is None and len(st.map_candidates) > 1,
        "地图待识别、按全部现役地图": lambda st, offer: st.map is None and not st.map_candidates,
        "普通难度": lambda st, offer: st.difficulty == NORMAL,
        "画面读到概率行和最左格稀有度": lambda st, offer: st.left_rarity is not None,
        "任务来源、给了等级": lambda st, offer: st.source == "task" and st.tier is not None,
        "候选里有资料库未收录的": lambda st, offer: any(n not in db.chips for n in offer),
    }
    every = corpus(db)
    out = []
    for label, ok in wants.items():
        found = next(((st, offer, rarity) for _, st, offer, rarity in every
                      if st.rerolls > 0 and st.star < 3 and ok(st, offer)), None)
        if found is None:
            raise AssertionError(f"corpus 里没有「{label}」的局面：资料或 corpus 改了，换一个条件")
        out.append((label, *found))
    plain = [n for n, c in db.chips.items() if not c.granted and not c.maps and n not in ("标靶", endless)]
    st = State(hero="凛", star=1, round=5, owned=("标靶",), rerolls=2, map_candidates=tuple(db.maps)[:3])
    out.append(("循环往复是候选、地图待识别", st, [endless, plain[0], plain[1]], None))
    st = State(hero="邦妮", star=0, round=3, rerolls=3, difficulty=NORMAL)
    out.append(("普通难度、0 星、地图待识别", st, plain[2:5], None))
    return out


def frame_plans(db, offer) -> list[Plan]:
    """每类局面用的方案：候选各标一档（plan_for，候选全是未收录的时没有），和标了循环往复档位的一套（循环往复那一项由档位
    代替，前瞻里不再按假设的局面重算它，和默认推荐走的路不同）。"""
    endless = db.rules["endless_cycle"]["chip"]
    other = [n for n in offer if n in db.chips and n != endless][:1]
    marked = Plan(name="一帧测试方案（循环往复）", tiers=((endless, "first"), *((n, "core") for n in other)))
    return [p for p in (plan_for(db, offer), marked) if p is not None]


class FrameCombosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.situations = frame_situations(cls.db)

    def test_frame_combos(self):
        """一帧里的几种调用组合共用一个 Cache，正着、倒着各算一遍，每次的 Advice 都和不给 cache 单算的逐位相同。"""
        db = self.db
        hits = 0
        for label, st, offer, rarity in self.situations:
            up = dataclasses.replace(st, star=min(3, st.star + 1))
            plans = frame_plans(db, offer)
            # （星数, 方案）→ 不给 cache 单算的 repr（下面每种组合都会用到）
            alone = {(s.star, p): repr(advise(db, s, offer, rarity, scheme=p))
                     for s in (st, up) for p in (None, *plans)}
            # 记账一帧的组合：只用默认推荐时原星数、高一星；按方案时方案的原星数、高一星，再按显示的那个局面
            # （首推相同时原星数，不同时高一星）算一次默认推荐
            combos = [[(st, None), (up, None)]]
            for plan in plans:
                combos += [[(st, plan), (up, plan), (st, None)], [(st, plan), (up, plan), (up, None)]]
            for calls in combos:
                for order in (calls, calls[::-1]):
                    shown = [(s.star, p.name if p else "默认推荐") for s, p in order]
                    with self.subTest(局面=label, 调用=shown):
                        frame = Cache()
                        frame.dists = Counting()
                        for state, plan in order:
                            got = repr(advise(db, state, offer, rarity, scheme=plan, cache=frame))
                            self.assertEqual(got, alone[(state.star, plan)], "共用一帧缓存和不给 cache 单算的结果不同")
                        hits += frame.dists.hits
        # 同一帧后面的推荐确实用上了前面算好的分布（免得 cache 没传到 pool_and_dists，这条测试悄悄变成空跑）
        self.assertGreater(hits, 0, "共用的缓存一次都没有命中")


class FrameOrderTest(unittest.TestCase):
    def test_orders_share_frame(self):
        """test_engine_cache_order 的每种查询顺序各建一个 Scorer、共用一个 Cache：和不给 cache 的结果逐位相同。"""
        db = load_db()
        checked = 0
        for hero, st, offer, rarity in order_cases(db)[::ORDER_STRIDE]:
            with self.subTest(hero=hero, offer=offer):
                plain = Scorer(db, st, offer=offer)
                probes = build_probes(plain, st, offer, rarity)
                reference = run_probes(probes, [key for key, _ in probes])
                frame = Cache()
                for label, order in ORDER_FUNCS.items():
                    scorer = Scorer(db, st, offer=offer, cache=frame)
                    probes = build_probes(scorer, st, offer, rarity)
                    values = run_probes(probes, order([key for key, _ in probes]))
                    bad = sorted(k for k in reference.keys() | values.keys() if reference.get(k) != values.get(k))
                    self.assertEqual(bad, [], f"共用一帧缓存、按 {label} 顺序查，结果不同")
                self.assertTrue(frame.dists, "这个局面一次分布都没算（前瞻、刷新、概率都没用到筹码池）")
            checked += 1
        self.assertGreaterEqual(checked, 10)


if __name__ == "__main__":
    unittest.main()
