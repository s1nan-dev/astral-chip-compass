"""进度领先时攒钱筹码的收入折算：剩下的兑现次数按进度条算，比较的基准
（第 1 轮时的次数、轮次）固定按整局的进度上限算。

分母要是也按进度条折算后的上限算：当前格比轮次快几格，「第 1 轮时」的参照就跟着变小，折算系数偏大、扣得偏少。
芬妮在魔法学院（进度上限 16）1 星时：
- 银行卡-余额多，第 6 轮、当前格第 9 格：−80.4（分母也按进度条算时是 −67；不领先时 −53.6）；
- 小猪存钱罐，第 6 轮、第 9 格：−24（分母也按进度条算时是 −18.75）；第 9 轮、第 14 格：−39（同上是 −36）。
"""
import unittest

from advisor.data import load_db
from advisor.engine import Scorer, advise
from advisor.pool import State


def income(db, chip: str, rnd: int, progress: int | None) -> float:
    st = State(hero="芬妮", map="魔法学院", star=1, round=rnd, progress=progress)
    sc = Scorer(db, st)
    return sum(p.score for p in sc.parts(chip, sc.known_owned, st.round) if p.label == "收入折算")


class IncomeProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_leading_progress_deducts_more(self):
        cases = (("银行卡-余额多", 6, 9, -80.4), ("小猪存钱罐", 6, 9, -24.0), ("小猪存钱罐", 9, 14, -39.0))
        for chip, rnd, progress, want in cases:
            with self.subTest(chip=chip, round=rnd, progress=progress):
                lead = income(self.db, chip, rnd, progress)
                self.assertAlmostEqual(lead, want, places=9)
                # 当前格领先时扣得不少于不领先时（剩下的兑现次数更少，比较的基准相同）
                self.assertLessEqual(lead, income(self.db, chip, rnd, rnd))

    def test_not_leading_unchanged(self):
        # 没读到当前格、当前格等于轮次：分子分母都按进度上限算
        for chip, want in (("银行卡-余额多", -53.6), ("小猪存钱罐", -15.0)):
            with self.subTest(chip=chip):
                self.assertAlmostEqual(income(self.db, chip, 6, None), want, places=9)
                self.assertEqual(income(self.db, chip, 6, 6), income(self.db, chip, 6, None))

    def test_reason_counts_by_progress(self):
        # 整句里剩下的次数也按进度条算（第 9 格：只剩 2 次轮次奖励），写的进度上限是读到的那个数
        st = State(hero="芬妮", map="魔法学院", star=1, round=6, progress=9)
        sc = Scorer(self.db, st)
        part = next(p for p in sc.parts("银行卡-余额多", sc.known_owned, 6) if p.label == "收入折算")
        self.assertEqual((part.reason, part.short), ("星光收入按剩余兑现次数折算：到进度上限（16）前还有 2 次轮次奖励",
                                                     "星光剩余 2 次兑现"))

    def test_advice_follows(self):
        # 同一组候选：当前格领先 3 格时小猪存钱罐扣得更多，首推仍是它。
        # 前瞻随筹码池和打分规则变（这里只核它很小），
        # 只核前瞻以外的部分：基础分 10、标签分 20、特性 15、收入折算 −24。
        # 剩 2 次刷新：剩 3 次时刷新的代价低，刷新扣掉代价后和小猪存钱罐只差不到 1 分，首推看的就不是收入折算了
        st = State(hero="芬妮", star=1, round=6, progress=9, map="魔法学院", rerolls=2)
        adv = advise(self.db, st, ["小猪存钱罐", "标靶", "书签"])
        pig = adv.evals[0]
        self.assertEqual(adv.best, "小猪存钱罐")
        self.assertAlmostEqual(pig.total - pig.lookahead, 21.0, places=6)
        self.assertLess(abs(pig.lookahead), 0.5)


if __name__ == "__main__":
    unittest.main()
