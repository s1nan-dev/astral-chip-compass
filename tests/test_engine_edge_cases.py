"""推荐引擎的几处边界：候选为空时明确拒绝；同系列三张连成链时推荐度不轮换；
数已有筹码时同一个筹码只数一次。三处都不改正常局面的推荐（基准比对逐位相同），注释写明不这样处理时会怎样。"""
import unittest
from dataclasses import replace

from advisor.data import SeriesPair, load_db
from advisor.engine import EMPTY_OFFER, ChipEval, Plan, Scorer, advise
from advisor.engine.advice import series_near
from advisor.pool import State


class EmptyOfferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_rejected_with_reason(self):
        # 不明确拒绝的话：默认推荐抛 ValueError「max() arg is an empty sequence」，按方案计算抛 IndexError，看不出原因
        st = State(hero="凛", star=1, round=4, map="魔法学院", rerolls=3)
        plan = Plan(name="测试", hero="凛", tiers=(("标靶", "first"),))
        for scheme in (None, plan):
            with self.subTest(scheme=scheme is not None):
                with self.assertRaises(ValueError) as caught:
                    advise(self.db, st, [], scheme=scheme)
                self.assertEqual(str(caught.exception), EMPTY_OFFER)
        # 两个候选、重复的候选照常出结果
        self.assertEqual(len(advise(self.db, st, ["标靶", "书签"]).evals), 2)
        self.assertEqual(len(advise(self.db, st, ["标靶", "标靶", "书签"]).evals), 3)


class SeriesChainTest(unittest.TestCase):
    """同系列 A < B < C（稀有度由低到高）三张都不算上位替代、按卡片顺序 A、B、C 相邻且总分都接近时，只对调 A、B 这一对：
    两对都对调的话（B、A 一次，C、B 又一次），推荐度会轮换成 C 40%、A 33%、B 27%；只对调一对是 B 40%、A 33%、C 27%，和按方案
    计算时（scheme_rank._series_near_scheme）一样，对调过的一对里排在后面的那张不再和下一张比。现在的资料里不算上位替代的几对没有连成链，
    用临时的同系列表构造。"""

    def test_chain_swaps_once(self):
        db = load_db()
        a, b, c = "夹心饼干-一般", "夹心饼干-可口", "夹心饼干-美味"
        fake = replace(db, series=(SeriesPair(a, b, False, "x", "y"), SeriesPair(b, c, False, "x", "y"),
                                   SeriesPair(a, c, False, "x", "y")))

        def ev(name, total, percent):
            return ChipEval(chip=db.chips[name], parts=[], official=0.0, immediate=total, lookahead=0.0,
                            lookahead_reason="", total=total, percent=percent)

        evals = [ev(a, 60.0, 0.40), ev(b, 59.0, 0.33), ev(c, 58.0, 0.27)]
        swaps = series_near(fake, evals, evals, None, None)
        self.assertEqual([(high.chip.name, low.chip.name) for high, low in swaps], [(b, a)])
        for high, low in swaps:  # 和 advise 里的对调相同
            high.percent, low.percent = low.percent, high.percent
        self.assertEqual([(e.chip.name, e.percent) for e in sorted(evals, key=lambda e: -e.percent)],
                         [(b, 0.40), (a, 0.33), (c, 0.27)])


class PicksLeftDedupTest(unittest.TestCase):
    def test_duplicate_owned_counted_once(self):
        # 已有筹码里同一个记了两次（识别或补录有误）：数已有筹码时只数一个，数成两个会把预计剩余的三选一少算一次
        # （数空格时同样去重）
        db = load_db()
        once = Scorer(db, State(hero="凛", star=1, owned=("书签", "标靶"), rerolls=3)).picks
        twice = Scorer(db, State(hero="凛", star=1, owned=("书签", "书签", "标靶"), rerolls=3)).picks
        self.assertEqual(twice, once)
        self.assertGreater(once.extras, 0)


if __name__ == "__main__":
    unittest.main()
