"""刷新有多出来的、最后一次三选一时「刷新」的估值（engine.Scorer.reroll、RerollEval.chain、kept、spare、
pool.expected_max_with）。

刷新次数读到了时，剩余刷新比之后的三选一（次数向上取整，每次留 1 次，再多留 1 次以防估少）多出来的部分现在用掉不算代价，
可以连续刷新到满意为止（最后一次刷新后只能接受）。刷新的估值按最优停止递推：C_1 = E[本次最高分]，
C_k = E[max(本次最高分, C_{k−1})]，k 为多出来的次数；本次之后预计没有筹码三选一（PicksLeft.total 为 0）时就是剩余刷新次数。
没有多出来的时照旧只算刷新一次。刷新次数没读出来（按默认值补的）时不按多出来的算。
132328-11（阿尔第 12 轮，剩余刷新 3 次）是那一局最后一次三选一。读到的进度是第 12 格（进度上限 17）：3 星以后还没到进度
上限，之后的三选一不估成 0（rules.toml [picks] late_rate 的下限，约 0.5 次），留 2 次、多出 1 次。同一局面把进度读成
走到头（FINAL，剩余轮数 0）时之后没有三选一：刷新按连续刷新 3 次估值后排第一（已持有友情徽章，刷新出的大碗炖肉另有组合
加分），实际选的缓冲盾牌第二、高于另外两个候选。这里不写死百分比，刷新的估值和测试里独立递推的结果比较。
"""
import dataclasses
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from advisor import cli
from advisor.data import load_db
from advisor.engine import REROLL, Scorer, advise, default_rerolls
from advisor.engine.lookahead import lookahead_on
from advisor.pool import State, Teammate, expected_best, expected_max_with, owned_schools, player_pool, slot_dists

# 10-03 真人对局（阿尔、异变图书馆、三名真人队友；同 tests/test_engine.py AlLiveReviewTest.OFFERS）
AL = dict(hero="阿尔", map="异变图书馆", teammates=(Teammate("帕露南"), Teammate("茉莉"), Teammate("邦妮")))
GOT = ("“8面”骰子", "ATM机", "摩托头盔-高级", "友情徽章", "银行卡-用不完", "星币锤", "银行卡-余额多", "金色飞星")
# 132328-11：最后一次三选一，剩余刷新 3 次，实际选的是缓冲盾牌
LAST = State(**AL, owned=GOT, star=3, round=12, source="levelup", rerolls=3, probabilities=(25, 50, 25),
             left_rarity="金", progress_cap=17, progress=12)
LAST_OFFER = ["汉娜人偶", "大背包", "缓冲盾牌"]
# 同一局面、进度读成走到头（剩余轮数 0）：本次之后预计没有筹码三选一，剩余刷新全部连着用
FINAL = dataclasses.replace(LAST, progress=17)
# 130113-3：之后还有筹码三选一，刷新照旧只算一次
EARLY = State(**AL, owned=GOT[:2], star=1, round=5, source="task", tier=2, rerolls=3, probabilities=(40, 45, 15),
              left_rarity="紫", progress=5)
EARLY_OFFER = ["额外电池", "摩托头盔-高级", "速度轮滑-中级"]


def best_masses(left, general, value) -> list[tuple[float, float]]:
    """一次三选一里三格最高分的分布：[(分值, 概率)]，按分值从低到高，分值相同的筹码合在一起。

    按定义直接求和，不经过 pool._cdf_steps：P(最高分 ≤ v) = P(最左格 ≤ v) × P(其余一格 ≤ v)²，
    没有特殊的最左格时为 P(一格 ≤ v)³。"""
    out, prev = [], 0.0
    for v in sorted({value[n] for n in (*general, *(left or ()))}):
        g = sum(p for n, p in general.items() if value[n] <= v)
        cdf = g**3 if left is None else sum(p for n, p in left.items() if value[n] <= v) * g * g
        out.append((v, cdf - prev))
        prev = cdf
    return out


def chain_value(masses, k: int) -> float:
    """最多连续刷新 k 次、刷到满意为止的期望：C_1 = E[本次最高分]，C_j = E[max(本次最高分, C_{j−1})]。"""
    c = sum(v * p for v, p in masses)
    for _ in range(k - 1):
        c = sum(max(v, c) * p for v, p in masses)
    return c


def reroll_setup(db, st: State, offer, map_name):
    """刷新后那次三选一在地图 map_name 上的分布和分值（同 Scorer.reroll：排除本次候选，分值含前瞻）。刷新后的最左格按
    职业权重出（rules.toml [pool] reroll_leftmost），职业分值按已有筹码累加（class_owned），同 context.pool_and_dists。"""
    scorer = Scorer(db, st, offer=offer)
    owned = scorer.known_owned
    pool = player_pool(db, owned, map_name, exclude=offer)
    left, general = slot_dists(
        db, scorer.hero, pool, owned_schools(db, owned), st.star, st.source, st.tier,
        probabilities=st.probabilities, left_rarity=st.left_rarity, reroll=True, difficulty=st.difficulty,
        class_owned=owned,
    )
    value = {c.name: scorer.score(c.name, owned, st.round, map_name) + lookahead_on(scorer, c.name, owned, map_name)
             for c in pool}
    return left, general, value


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def options(self, adv) -> list[float]:
        return [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])


# ------------------------------------------------------------ pool.expected_max_with


class ExpectedMaxWithTest(Base):
    def test_example(self):
        # 三格都是 a、b 各一半：最高分为 a（0 分）的概率 1/8，记 floor 50；为 b（100 分）的概率 7/8
        general, value = {"a": 0.5, "b": 0.5}, {"a": 0.0, "b": 100.0}
        self.assertAlmostEqual(expected_max_with(None, general, value, 50), 93.75)
        self.assertAlmostEqual(expected_max_with({"a": 1.0}, general, value, 50), 50 * 0.25 + 100 * 0.75)

    def test_floor_minus_inf_is_expected_best(self):
        general = {"a": 0.2, "b": 0.3, "c": 0.5}
        value = {"a": 10.0, "b": 40.0, "c": 25.0}
        for left in (None, {"a": 0.5, "b": 0.5}, {"c": 1.0}):
            with self.subTest(left=left):
                self.assertAlmostEqual(expected_max_with(left, general, value, -math.inf),
                                       expected_best(left, general, value))

    def test_floor_bounds(self):
        # floor 不低于所有分值时就是 floor；低于所有分值时等于 expected_best
        general = {"a": 0.2, "b": 0.3, "c": 0.5}
        value = {"a": 10.0, "b": 40.0, "c": 25.0}
        self.assertAlmostEqual(expected_max_with(None, general, value, 40.0), 40.0)
        self.assertAlmostEqual(expected_max_with(None, general, value, 99.0), 99.0)
        self.assertAlmostEqual(expected_max_with(None, general, value, 9.0), expected_best(None, general, value))
        self.assertGreater(expected_max_with(None, general, value, 20.0), expected_best(None, general, value))

    def test_matches_direct_sum(self):
        # 用 132328-11 刷新后的真实分布：和按定义直接求和的结果一致（含分值相同的筹码）
        left, general, value = reroll_setup(self.db, LAST, LAST_OFFER, LAST.map)
        masses = best_masses(left, general, value)
        self.assertAlmostEqual(sum(p for _, p in masses), 1.0, places=9)
        self.assertAlmostEqual(expected_best(left, general, value), chain_value(masses, 1), places=9)
        for floor in (-50.0, 0.0, 40.0, 70.0, 90.0, 500.0):
            with self.subTest(floor=floor):
                direct = sum(max(v, floor) * p for v, p in masses)
                self.assertAlmostEqual(expected_max_with(left, general, value, floor), direct, places=9)


# ------------------------------------------------------------ 最后一次三选一：132328-11


class LastPickTest(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adv = advise(cls.db, FINAL, LAST_OFFER)
        cls.setup = reroll_setup(cls.db, FINAL, LAST_OFFER, FINAL.map)

    def test_chain_follows_rerolls(self):
        adv = self.adv
        self.assertEqual(adv.picks.total, 0)
        self.assertEqual(adv.reroll.chain, 3)
        self.assertEqual(adv.reroll.cost, 0)
        self.assertEqual(adv.reroll.value, adv.reroll.expected)

    def test_real_reading_not_final(self):
        # 实际读到的进度第 12 格（进度上限 17，C = 16）：3 星以后、还没到进度上限，之后的三选一至少按 late_rate ×（H + 0.5）
        # 算，H = (max(0.5 × 16, 12) + 16) ÷ 2 − 12 = 2，约 0.5 次（任务和商店的份额已经用完）。按 1 次留、再多留 1 次：
        # 留 2 次、多出 1 次，只刷一次、代价 0
        adv = advise(self.db, LAST, LAST_OFFER)
        late = self.db.rules["picks"]["late_rate"]
        self.assertAlmostEqual(adv.picks.total, late * (2 + 0.5))
        self.assertEqual((adv.reroll.kept, adv.reroll.spare, adv.reroll.chain, adv.reroll.cost), (2, 1, 1, 0.0))

    def test_reroll_first_ahead_of_actual_pick(self):
        # 刷新按连续刷新估值后排第一；实际选的缓冲盾牌第二，高于另外两个候选
        adv = self.adv
        ev = {e.chip.name: e for e in adv.evals}
        self.assertEqual(adv.best, REROLL)
        self.assertGreater(adv.reroll.value, ev["缓冲盾牌"].total)
        self.assertGreater(ev["缓冲盾牌"].total, max(ev["汉娜人偶"].total, ev["大背包"].total))
        self.assertGreater(adv.reroll.percent, ev["缓冲盾牌"].percent)
        self.assertGreater(ev["缓冲盾牌"].percent, max(ev["汉娜人偶"].percent, ev["大背包"].percent))
        self.assertAlmostEqual(sum(self.options(adv)), 1.0, places=9)
        # 已持有友情徽章时，刷新出的大碗炖肉另有组合「友情徽章与大碗炖肉」加分：去掉这条时刷新的估值降低，
        # 仍高于实际选的缓冲盾牌（阿尔的探天卫星、金色飞星等另有角色专属加分，刷新出的金色筹码本身就值得等）
        db = dataclasses.replace(self.db, combos=tuple(c for c in self.db.combos if c.name != "友情徽章与大碗炖肉"))
        without = advise(db, FINAL, LAST_OFFER)
        self.assertEqual(without.best, REROLL)
        self.assertLess(without.reroll.value, adv.reroll.value)
        self.assertGreater(without.reroll.value, ev["缓冲盾牌"].total)

    def test_value_matches_recursion(self):
        # 刷新的估值 = 测试里独立递推的 C_3，并且高于同一局面只剩 1 次刷新时的估值（C_1）
        masses = best_masses(*self.setup)
        self.assertAlmostEqual(self.adv.reroll.expected, chain_value(masses, 3), places=9)
        single = advise(self.db, dataclasses.replace(FINAL, rerolls=1), LAST_OFFER).reroll
        self.assertEqual(single.chain, 1)
        self.assertAlmostEqual(single.expected, chain_value(masses, 1), places=9)
        self.assertGreater(self.adv.reroll.value, single.value)

    def test_single_reroll_unchanged(self):
        # 只剩 1 次刷新时：刷新后三选一最高分的期望（expected_best），出现更好筹码的概率只算一次
        left, general, value = self.setup
        single = advise(self.db, dataclasses.replace(FINAL, rerolls=1), LAST_OFFER)
        best = max(e.total for e in single.evals)
        self.assertEqual(single.reroll.expected, expected_best(left, general, value))
        self.assertAlmostEqual(single.reroll.p_better, sum(p for v, p in best_masses(left, general, value) if v > best),
                               places=9)

    def test_monotonic_in_rerolls(self):
        # 剩余刷新 1~5 次：估值和出现更好筹码的概率都不减；估值不超过筹码池里的最高分值；
        # 概率为「最多 k 次之内出现」：1 − (1 − p₁)^k
        _, _, value = self.setup
        top = max(value.values())
        last = last_p = -math.inf
        p1 = None
        for k in range(1, 6):
            r = advise(self.db, dataclasses.replace(FINAL, rerolls=k), LAST_OFFER).reroll
            with self.subTest(rerolls=k):
                self.assertEqual(r.chain, k)
                self.assertGreaterEqual(r.value, last - 1e-9)
                self.assertGreaterEqual(r.p_better, last_p - 1e-12)
                self.assertLessEqual(r.value, top + 1e-9)
                p1 = r.p_better if p1 is None else p1
                self.assertAlmostEqual(r.p_better, 1 - (1 - p1) ** k, places=9)
            last, last_p = r.value, r.p_better

    def test_candidates_unchanged(self):
        # 只改「刷新」的估值：三个候选的分值和前瞻与剩余刷新次数无关（之后没有三选一时循环往复给的刷新也不值分，
        # 候选和刷新后筹码池里循环往复的分值不随剩余刷新次数变）
        single = advise(self.db, dataclasses.replace(FINAL, rerolls=1), LAST_OFFER)
        for a, b in zip(self.adv.evals, single.evals):
            with self.subTest(chip=a.chip.name):
                self.assertAlmostEqual(a.total, b.total, places=9)
                self.assertAlmostEqual(a.lookahead, b.lookahead, places=9)


# ------------------------------------------------------------ 其他局面


class OtherCasesTest(Base):
    def test_more_picks_keep_single_reroll(self):
        # 130113-3：之后还有筹码三选一，照旧只算刷新一次，value = expected − 刷新代价
        adv = advise(self.db, EARLY, EARLY_OFFER)
        r = adv.reroll
        self.assertGreater(adv.picks.total, 0)
        self.assertEqual(r.chain, 1)
        self.assertGreater(r.cost, 0)
        self.assertAlmostEqual(r.value, r.expected - r.cost)
        left, general, value = reroll_setup(self.db, EARLY, EARLY_OFFER, EARLY.map)
        self.assertEqual(r.expected, expected_best(left, general, value))

    def test_no_rerolls(self):
        adv = advise(self.db, dataclasses.replace(LAST, rerolls=0), LAST_OFFER)
        self.assertIsNone(adv.reroll)
        self.assertNotEqual(adv.best, REROLL)

    def test_unknown_map_averages_each_map(self):
        # 地图待识别时，每张候选地图分别递推，再取平均。第 16 轮：三张地图的进度上限各不相同时取最小的 16，到进度上限了，
        # 之后没有三选一
        st = State(hero="凛", star=3, round=16, map_candidates=("异变图书馆", "水乡古镇", "御魂庆典"), rerolls=4,
                   owned=("标记喷罐", "大背包", "标靶"))
        offer = ["额外电池", "夹心饼干-一般", "书签"]
        adv = advise(self.db, st, offer)
        self.assertEqual(adv.picks.total, 0)
        self.assertGreater(len(adv.maps), 1)
        self.assertEqual(adv.reroll.chain, 4)
        each = [chain_value(best_masses(*reroll_setup(self.db, st, offer, m)), 4) for m in adv.maps]
        self.assertAlmostEqual(adv.reroll.expected, sum(each) / len(each), places=9)


# ------------------------------------------------------------ 多出来的刷新（还有之后的三选一时）


class SpareRerollTest(Base):
    """剩余刷新次数读到了时，比之后的三选一（次数向上取整，每次留 1 次）再多留 1 次以外多出来的：多出来的按最多连着刷
    这么多次估值、代价为 0；没有多出来的照旧刷新一次、按公式扣代价。之后预计没有三选一是留下 0 次的特例（结果和原来一样，
    见 LastPickTest）。刷新次数没读出来（State.rerolls_known 为 False，按默认值补的）时不按多出来的算（UnreadRerollsTest）。"""

    RIN = dict(hero="凛", star=3, map="魔法学院", owned=("标靶", "标记喷罐", "大背包", "缓冲盾牌"), rerolls=4)
    RIN_OFFER = ["摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"]

    def test_split(self):
        # 留下的 = min(剩余刷新, ⌈之后的三选一次数⌉ + 1)（之后没有三选一时 0），多出来的 = 剩余刷新 − 留下的。
        # 魔法学院进度上限 16：第 12、13 轮按第 13 轮收窄到 1/3、0 次以后，3 星的下限是 0.2 ×（H + 0.5）= 0.4、0.3 次；
        # 第 16 轮到了进度上限，之后没有三选一
        for rnd, rerolls, picks, kept in ((11, 4, 0.67, 2), (12, 4, 0.4, 2), (13, 4, 0.3, 2), (16, 4, 0.0, 0),
                                          (12, 1, 0.4, 1), (12, 2, 0.4, 2), (12, 0, 0.4, 0)):
            sc = Scorer(self.db, State(**{**self.RIN, "round": rnd, "rerolls": rerolls}))
            with self.subTest(round=rnd, rerolls=rerolls):
                self.assertAlmostEqual(sc.picks.total, picks, places=2)
                self.assertEqual(sc.reroll_split(), (kept, rerolls - kept))
        # 之后的三选一正好是整数次（浮点末位多出一点也一样）：按这么多次留、再多留 1 次，不因末位多留
        with mock.patch.object(Scorer, "picks", SimpleNamespace(total=2.0 + 1e-12)):
            self.assertEqual(Scorer(self.db, State(hero="凛", rerolls=4)).reroll_split(), (3, 1))
        with mock.patch.object(Scorer, "picks", SimpleNamespace(total=2.000001)):
            self.assertEqual(Scorer(self.db, State(hero="凛", rerolls=4)).reroll_split(), (4, 0))
        # 之后的三选一不超过 1e-9 次：按没有三选一算，全部多出来
        with mock.patch.object(Scorer, "picks", SimpleNamespace(total=1e-10)):
            self.assertEqual(Scorer(self.db, State(hero="凛", rerolls=3)).reroll_split(), (0, 3))

    def test_rin_example(self):
        # 凛 3 星、读到 4 次刷新：第 11～13 轮（之后约 0.67、0.4、0.3 次三选一）留 2 次（1 次 + 多留 1 次）、多出 2 次连着刷，
        # 第 16 轮（到了进度上限，之后没有）4 次都能用
        out = {}
        for rnd in (11, 12, 13, 16):
            adv = advise(self.db, State(**{**self.RIN, "round": rnd}), self.RIN_OFFER)
            r = adv.reroll
            out[rnd] = r
            with self.subTest(round=rnd):
                self.assertEqual((r.chain, r.kept, r.spare, r.need, r.cost),
                                 (2, 2, 2, 1, 0.0) if rnd < 16 else (4, 0, 4, 0, 0.0))
                self.assertTrue(r.known)
                self.assertEqual(r.value, r.expected)
                self.assertEqual(adv.best, REROLL)
        # 估值就是只排除本次候选的分布上按最多连着刷 2 次递推的期望；第 16 轮连着刷 4 次更高
        st = State(**{**self.RIN, "round": 12})
        masses = best_masses(*reroll_setup(self.db, st, self.RIN_OFFER, st.map))
        self.assertAlmostEqual(out[12].expected, chain_value(masses, 2), places=9)
        self.assertGreater(out[16].value, out[12].value)
        # 比没读出刷新次数时（只刷一次、扣代价）高
        unread = advise(self.db, State(**{**self.RIN, "round": 12, "rerolls_known": False}), self.RIN_OFFER).reroll
        self.assertGreater(out[12].value, unread.value)

    def test_no_spare_unchanged(self):
        # 没有多出来的（剩余刷新不多于之后的三选一再加 1 次）：照旧刷新一次、代价 = gain × H(之后的三选一 ÷ 剩余刷新)
        adv = advise(self.db, dataclasses.replace(EARLY, rerolls=2), EARLY_OFFER)
        r = adv.reroll
        self.assertEqual((r.chain, r.spare), (1, 0))
        gain = self.db.rules["reroll_cost"]["gain"]
        from advisor.engine.context import harmonic
        self.assertAlmostEqual(r.cost, gain * harmonic(adv.picks.total / 2))
        # 留下的正好等于剩余次数（剩 2 次、之后不到 1 次三选一：1 次 + 多留 1 次）：没有多出来的，照旧扣代价
        st = State(**{**self.RIN, "round": 12, "rerolls": 2})
        r = advise(self.db, st, self.RIN_OFFER).reroll
        self.assertEqual((r.kept, r.spare, r.chain), (2, 0, 1))
        self.assertGreater(r.cost, 0)
        self.assertAlmostEqual(r.cost, gain * harmonic(advise(self.db, st, self.RIN_OFFER).picks.total / 2))
        # 剩 3 次：多出 1 次，只刷一次、代价 0
        r = advise(self.db, State(**{**self.RIN, "round": 12, "rerolls": 3}), self.RIN_OFFER).reroll
        self.assertEqual((r.kept, r.spare, r.chain, r.cost), (2, 1, 1, 0.0))

    def test_scheme_view(self):
        # 自己的方案：刷新那一行的级数递推和代价同样按多出来的刷新（剩 5 次、留 2 次：代价为 0，最多连着刷 3 次）
        from advisor.engine import Plan
        # 已进标记、治愈：这两个流派里标几张推荐，刷新后才有机会刷出更高一档（连着刷的平均级数才会更高）
        plan = Plan(name="测试方案", hero="凛", tiers=(("速度轮滑-中级", "second"), ("鹰眼瞄具", "first"),
                                                       ("普通瞄具", "first"), ("医疗箱-完备治疗", "first"),
                                                       ("大碗炖肉", "first")))
        adv = advise(self.db, State(**{**self.RIN, "round": 12, "rerolls": 5}), self.RIN_OFFER, scheme=plan)
        v = adv.ranking.reroll
        self.assertEqual((v.chain, v.cost), (3, 0.0))
        once = advise(self.db, State(**{**self.RIN, "round": 12, "rerolls": 1}), self.RIN_OFFER, scheme=plan)
        once = once.ranking.reroll
        self.assertEqual(once.chain, 1)
        self.assertGreater(v.expected, once.expected)
        # 没读出刷新次数时同默认推荐：之后还有三选一就只刷一次、扣代价
        unread = advise(self.db, State(**{**self.RIN, "round": 12, "rerolls": 5, "rerolls_known": False}),
                        self.RIN_OFFER, scheme=plan).ranking.reroll
        self.assertEqual(unread.chain, 1)
        self.assertGreater(unread.cost, 0)


class EndlessCycleRerollsTest(Base):
    """循环往复给的 3 次刷新（parts._endless_rerolls）：加上以后按同一个留法分，落在留下的以内的照原来的模型
    gain × H(剩余三选一次数 ÷ (现有刷新次数 + j))；超出的（多出来的刷新）只值多连刷一次的提升，算在下一次三选一上
    （那时之后的三选一少一次、留下的少一次）：多出来的第 n + 1 次值 C(n + 1) − C(n)，之后不到 1 次三选一时按下一次三选一
    出现的机会（剩余三选一次数）打折；每一次再以原来的模型给这一次的值封顶，只往下调。刷新次数没读出来时全部照原来的模型。"""

    def old_one(self, sc, q: int) -> float:
        """原来的模型给第 q 次刷新的值：gain × H(剩余三选一次数 ÷ q)（多出来的那几次以它封顶）。"""
        from advisor.engine.context import harmonic
        return self.db.rules["reroll_cost"]["gain"] * harmonic(sc.picks.total / q)

    AL = dict(hero="阿尔", map="魔法学院", owned=("缓冲盾牌",))

    def old_model(self, sc) -> float:
        from advisor.engine.context import harmonic
        gain, extra = self.db.rules["reroll_cost"]["gain"], self.db.rules["reroll"]["endless_cycle"]
        total, r = sc.picks.total, sc.st.rerolls
        return sum(gain * harmonic(total / (r + j)) for j in range(1, extra + 1)) if total > 0 else 0.0

    def test_unread_and_within_kept_unchanged(self):
        from advisor.engine import parts
        from advisor.engine.context import reroll_keep
        # 刷新次数没读出来：全部照原来的模型
        sc = Scorer(self.db, State(**self.AL, star=2, round=7, rerolls=4, rerolls_known=False))
        self.assertEqual(parts._endless_rerolls(sc, sc.st.map), self.old_model(sc))
        # 读到了、加上以后都在留下的以内（1 星第 4 轮、剩 0 次：之后约 5 次三选一，留 6 次，3 次都留着）：同原来的模型
        sc = Scorer(self.db, State(**self.AL, star=1, round=4, rerolls=0))
        self.assertEqual(reroll_keep(sc, 3), 3)
        self.assertEqual(parts._endless_rerolls(sc, sc.st.map), self.old_model(sc))

    def test_spare_values_chain_gain(self):
        # 2 星第 7 轮、剩 4 次（之后约 3 次三选一：按 3 次留、再多留 1 次，留 4 次）：加上以后 7 次，第 5～7 次多出来。
        # 下一次三选一时之后约 2 次、留 3 次，它们是多出来的第 2～4 次：值 C(2) − C(1)、C(3) − C(2)、C(4) − C(3)，
        # C 按下一次三选一（升到 3 星的那次）刷新后的分布、拿了循环往复以后的筹码池、各筹码不含前瞻的分值递推
        from advisor.engine import parts
        from advisor.engine.context import next_sources, reroll_keep
        st = State(**self.AL, star=2, round=7, rerolls=4)
        sc = Scorer(self.db, st)
        self.assertAlmostEqual(sc.picks.total, 3.0)
        self.assertEqual(reroll_keep(sc, 7), 4)
        m = st.map
        after = (*sc.known_owned, "循环往复")
        pool = player_pool(self.db, after, m)
        rnd = st.round + self.db.rules["lookahead"]["round_advance"]
        value = {c.name: sc.score(c.name, sc.known_owned, rnd, m) for c in pool}
        (share, source, star, tier), = next_sources(sc, m)
        left, general = slot_dists(self.db, sc.hero, pool, owned_schools(self.db, after), star, source, tier,
                                   reroll=True, difficulty=st.difficulty, class_owned=after)
        masses = best_masses(left, general, value)
        # 加上的 3 次都多出来：第 q = 4 + n 次（n = 1、2、3）值 min(C(n + 1) − C(n), 原来的模型给第 q 次的值)
        steps = [chain_value(masses, n + 1) - chain_value(masses, n) for n in (1, 2, 3)]
        spare = sum(min(step, self.old_one(sc, 4 + n)) for n, step in zip((1, 2, 3), steps))
        self.assertAlmostEqual(parts._endless_rerolls(sc, m), spare, places=9)
        # 这个局面里有一次连刷的提升比原来的模型给那一次的值高：封顶以后按原来的模型，合计比不封顶时低
        self.assertTrue(any(step > self.old_one(sc, 4 + n) for n, step in zip((1, 2, 3), steps)))
        self.assertLess(spare, sum(steps))
        # 多出来的只值多连刷一次的提升：比原来的模型低；循环往复的分值跟着低
        self.assertLess(parts._endless_rerolls(sc, m), self.old_model(sc))
        unread = Scorer(self.db, dataclasses.replace(st, rerolls_known=False))
        self.assertLess(sc.score("循环往复", sc.known_owned, st.round), unread.score("循环往复", sc.known_owned, st.round))

    def test_part_kept_part_spare(self):
        # 3 星第 10 轮、剩 1 次（之后约 1 次三选一：留 2 次）：加上的第 1 次在留下的以内照原来的模型，后 2 次多出来
        # （各自不超过原来的模型给那一次的值）
        from advisor.engine import parts
        from advisor.engine.context import reroll_keep
        st = State(**self.AL, star=3, round=10, rerolls=1)
        sc = Scorer(self.db, st)
        self.assertEqual(reroll_keep(sc, 4), 2)
        first = self.old_one(sc, 2)
        rest = sum(min(parts._endless_chain_gain(sc, q, st.map), self.old_one(sc, q)) for q in (3, 4))
        self.assertAlmostEqual(parts._endless_rerolls(sc, st.map), first + rest, places=9)
        self.assertGreater(rest, 0)
        self.assertLessEqual(rest, self.old_one(sc, 3) + self.old_one(sc, 4))

    def test_under_one_later_pick(self):
        # 3 星第 12 轮、剩 2 次（之后约 0.4 次三选一：按 1 次留、再多留 1 次，留 2 次）：加上的 3 次都多出来。下一次三选一
        # 时之后没有三选一、一次都不留，它们是多出来的第 3～5 次，值 C(3) − C(2)、C(4) − C(3)、C(5) − C(4)；下一次三选一只有
        # 约 0.4 的机会出现，提升按它打折（同前瞻影响的次数不超过剩余三选一次数）；每一次再以原来的模型封顶
        from advisor.engine import parts
        from advisor.engine.context import next_sources, reroll_keep
        st = State(**self.AL, star=3, round=12, rerolls=2)
        sc = Scorer(self.db, st)
        total = sc.picks.total
        self.assertTrue(0 < total < 1)
        self.assertEqual(reroll_keep(sc, 5), 2)
        m = st.map
        after = (*sc.known_owned, "循环往复")
        pool = player_pool(self.db, after, m)
        rnd = st.round + self.db.rules["lookahead"]["round_advance"]
        value = {c.name: sc.score(c.name, sc.known_owned, rnd, m) for c in pool}
        steps = [0.0, 0.0, 0.0]  # 第 3、4、5 次（n = 2、3、4）的提升，几种来源按占比加权
        for share, source, star, tier in next_sources(sc, m):  # 3 星以后是任务和筹码商店，按占比
            left, general = slot_dists(self.db, sc.hero, pool, owned_schools(self.db, after), star, source, tier,
                                       reroll=True, difficulty=st.difficulty, class_owned=after)
            masses = best_masses(left, general, value)
            for i, n in enumerate((2, 3, 4)):
                steps[i] += share * (chain_value(masses, n + 1) - chain_value(masses, n))
        self.assertGreater(sum(steps), 0)
        want = sum(min(total * step, self.old_one(sc, q)) for q, step in zip((3, 4, 5), steps))
        self.assertAlmostEqual(parts._endless_rerolls(sc, m), want, places=9)

    def test_capped_at_old_model(self):
        # 多出来的每一次不超过原来的模型给那一次的值，只往下调。凛 3 星第 9 轮（魔法学院，已有标靶、标记喷罐、大背包、
        # 缓冲盾牌）读到 2 次刷新、之后约 1.33 次三选一（留 3 次）：加上的第 1 次在留下的以内，第 2、3 次多出来；凛刷新后的
        # 分布好，多连刷一次的提升比原来的模型给这两次的值都高，封顶以后三次合计就是原来的模型
        from advisor.engine import parts
        st = State(hero="凛", star=3, round=9, map="魔法学院", owned=("标靶", "标记喷罐", "大背包", "缓冲盾牌"), rerolls=2)
        sc = Scorer(self.db, st)
        self.assertAlmostEqual(sc.picks.total, 4 / 3)
        for q in (4, 5):
            with self.subTest(q=q):
                self.assertGreater(parts._endless_chain_gain(sc, q, st.map), self.old_one(sc, q))
        self.assertAlmostEqual(parts._endless_rerolls(sc, st.map), self.old_model(sc), places=9)
        # 本来就比原来的模型低的照旧：阿尔 3 星第 12 轮剩 2 次（之后约 0.4 次三选一），多出来的 3 次都不封顶
        st = State(**self.AL, star=3, round=12, rerolls=2)
        sc = Scorer(self.db, st)
        spare = [parts._endless_chain_gain(sc, q, st.map) for q in (3, 4, 5)]
        self.assertTrue(all(v < self.old_one(sc, q) for v, q in zip(spare, (3, 4, 5))))
        self.assertAlmostEqual(parts._endless_rerolls(sc, st.map), sum(spare), places=9)

    def test_no_later_picks(self):
        # 读到了刷新次数、之后预计没有三选一：增加的刷新不值分（同原来的模型）
        from advisor.engine import parts
        sc = Scorer(self.db, State(**self.AL, star=3, round=16, rerolls=2))
        self.assertEqual(sc.picks.total, 0)
        self.assertEqual(parts._endless_rerolls(sc, sc.st.map), 0.0)


class UnreadRerollsTest(Base):
    """剩余刷新次数没读出来、按默认值补的（State.rerolls_known 为 False）：默认值是可能的最多次数，不按多出来的刷新算。
    之后还有三选一时只刷一次、代价按公式（gain × H(之后的三选一 ÷ 剩余刷新)）；之后没有三选一时照样连着刷。"""

    RIN = SpareRerollTest.RIN
    RIN_OFFER = SpareRerollTest.RIN_OFFER

    def test_split_and_cost(self):
        from advisor.engine.context import harmonic
        gain = self.db.rules["reroll_cost"]["gain"]
        for rnd in (11, 12):
            st = State(**{**self.RIN, "round": rnd, "rerolls_known": False})
            adv = advise(self.db, st, self.RIN_OFFER)
            r = adv.reroll
            with self.subTest(round=rnd):
                self.assertEqual((r.kept, r.spare, r.chain, r.known), (4, 0, 1, False))
                self.assertAlmostEqual(r.cost, gain * harmonic(adv.picks.total / 4))
                self.assertAlmostEqual(r.value, r.expected - r.cost)
        # 之后没有三选一（第 16 轮，到了进度上限）：和读到了一样，4 次都能连着用
        unread = advise(self.db, State(**{**self.RIN, "round": 16, "rerolls_known": False}), self.RIN_OFFER).reroll
        read = advise(self.db, State(**{**self.RIN, "round": 16}), self.RIN_OFFER).reroll
        self.assertEqual((unread.kept, unread.spare, unread.chain, unread.cost), (0, 4, 4, 0.0))
        self.assertEqual(unread.value, read.value)
        # 之后的三选一不超过 1e-9 次（读到了时按没有算）：没读出来时照不分多出来的算法，之后还有（大于 0）就只刷一次
        with mock.patch.object(Scorer, "picks", SimpleNamespace(total=1e-10)):
            sc = Scorer(self.db, State(hero="凛", rerolls=3, rerolls_known=False))
            self.assertEqual(sc.reroll_split(), (3, 0))
            self.assertEqual(sc.reroll_chain(), 1)

    def test_default_is_read(self):
        # 不写这一项（测试、推荐基准、命令行直接给了刷新次数）时按读到了算
        self.assertTrue(State(hero="凛").rerolls_known)
        self.assertTrue(advise(self.db, State(**{**self.RIN, "round": 12}), self.RIN_OFFER).reroll.known)


# ------------------------------------------------------------ 命令行


class CliTest(Base):
    def test_render_chain(self):
        text = cli.render(self.db, advise(self.db, FINAL, LAST_OFFER))
        line = next(s for s in text.splitlines() if f"  {REROLL}  " in s)
        self.assertIn("刷新后最高总分约", line)
        self.assertIn(cli.CHAIN_NOTE.format(n=3), line)
        self.assertEqual(cli.CHAIN_NOTE.format(n=3), "（按最多连续刷新 3 次估计）")
        self.assertIn("扣除刷新代价 0 后为", line)
        self.assertIn("· 最多连续刷新 3 次时，出现总分高于当前候选筹码最高总分的筹码的概率：", text)
        data = cli.to_json(advise(self.db, FINAL, LAST_OFFER))
        self.assertEqual(data["best"], REROLL)
        self.assertEqual(data["reroll"]["chain"], 3)

    def test_render_single(self):
        # 之后还有三选一时照旧：不写连续刷新
        text = cli.render(self.db, advise(self.db, EARLY, EARLY_OFFER))
        self.assertNotIn("连续刷新", text)
        self.assertIn("· 刷新后出现总分高于当前候选筹码最高总分的筹码的概率：", text)
        self.assertEqual(cli.to_json(advise(self.db, EARLY, EARLY_OFFER))["reroll"]["chain"], 1)

    def run_cli(self, *extra) -> tuple[str, dict]:
        base = ["--hero", "凛", "--star", "3", "--round", "11", "--map", "魔法学院",
                "--owned", "标靶", "标记喷罐", "大背包", "缓冲盾牌", "--offer", "摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"]
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*base, *extra])
        text = out.getvalue()
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*base, *extra, "--json"])
        return text, json.loads(out.getvalue())["reroll"]

    def test_spare_and_unread(self):
        from advisor.engine import SPARE_NOTE, UNREAD_NOTE
        # 直接给了刷新次数（--rerolls 4，之后不到 1 次三选一）：按 1 次留、再多留 1 次，多出来的 2 次连着刷
        text, data = self.run_cli("--rerolls", "4")
        self.assertEqual((data["kept"], data["spare"], data["need"], data["known"], data["chain"]), (2, 2, 1, True, 2))
        self.assertIn(SPARE_NOTE.format(need=1, margin="，另多留 1 次以防估少", kept=2, spare=2), text)
        self.assertIn("（之后的三选一按 1 次留刷新，另多留 1 次以防估少，共留 2 次；多出来的 2 次不计代价）", text)
        self.assertIn("｜刷新 ×4\n", text)
        self.assertNotIn(cli.UNREAD_REROLLS, text)
        # 给了已用的次数（--rerolls-used 1）：剩余按规则推算（5 − 1 = 4），同样算知道
        _, data = self.run_cli("--rerolls-used", "1")
        self.assertEqual((data["kept"], data["spare"], data["known"]), (2, 2, True))
        # 都不给：按一次都没用过推算（3 星 5 次），同悬浮窗读不出刷新次数时——不按多出来的算，只刷一次、扣代价
        text, data = self.run_cli()
        self.assertEqual((data["kept"], data["spare"], data["known"], data["chain"]), (5, 0, False, 1))
        self.assertGreater(data["cost"], 0)
        self.assertIn(f"刷新 ×5{cli.UNREAD_REROLLS}", text)
        self.assertIn(UNREAD_NOTE, text)

    def test_main_late_game(self):
        # 命令行：3 星、第 19 轮（御魂庆典，困难及以上按疯狂一列的进度上限 19：到了进度上限，之后没有三选一），刷新次数按
        # 规则推算：没读出来时之后没有三选一也照样连着刷
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--hero", "凛", "--star", "3", "--round", "19", "--map", "御魂庆典",
                      "--offer", "大背包", "额外电池", "标靶"])
        n = default_rerolls(self.db, 3)
        self.assertGreater(n, 1)
        self.assertIn(f"刷新 ×{n}", out.getvalue())
        self.assertIn(cli.CHAIN_NOTE.format(n=n), out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--hero", "凛", "--star", "3", "--round", "19", "--map", "御魂庆典",
                      "--offer", "大背包", "额外电池", "标靶", "--json"])
        self.assertEqual(json.loads(out.getvalue())["reroll"]["chain"], n)
        # 第 15 轮：3 星以后、还没到进度上限，之后的三选一不估成 0（下限），没读出刷新次数时只刷一次、扣代价
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--hero", "凛", "--star", "3", "--round", "15", "--map", "御魂庆典",
                      "--offer", "大背包", "额外电池", "标靶", "--json"])
        data = json.loads(out.getvalue())
        self.assertGreater(data["picks_left"], 0)
        self.assertEqual((data["reroll"]["chain"], data["reroll"]["known"]), (1, False))


if __name__ == "__main__":
    unittest.main()
