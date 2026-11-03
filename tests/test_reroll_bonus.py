"""从画面上的刷新次数推断「每升一星刷新 +1」（合作挑战等级 Lv6 及以上）。只影响读不出刷新次数时的默认值。"""
import unittest

from advisor.data import load_db
from tests.test_session import close, frame, ready

ONE_STAR = (60, 37, 3)
TWO_STAR = (40, 45, 15)
BLUE_FIRST = ["标靶", "大背包", "缓冲盾牌"]  # 1 星升星的三选一：最左格是蓝色
PURPLE_FIRST = ["大背包", "标靶", "缓冲盾牌"]  # 2 星升星的三选一：最左格是紫色


class RerollBonusTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, hero="凛")
        self.s.settings.star, self.s.settings.star_read = 1, True

    def level_up(self, before: int, after: int):
        """1 星时的三选一最后读到 before 次刷新；升到 2 星后第一次三选一读到 after 次。"""
        s = self.s
        s.on_frame(frame(BLUE_FIRST, rerolls=before, probabilities=ONE_STAR))
        close(s)
        s.settings.star = 2  # 玩家列表上读到 2 星
        return s.on_frame(frame(PURPLE_FIRST, rerolls=after, probabilities=TWO_STAR))

    def test_one_more_after_level_up_means_bonus(self):
        ev = self.level_up(1, 2)
        self.assertEqual(ev.state.source, "levelup")
        self.assertIs(self.s.game.per_star_rerolls, True)

    def test_same_count_means_no_bonus(self):
        self.level_up(1, 1)
        self.assertIs(self.s.game.per_star_rerolls, False)

    def test_other_differences_decide_nothing(self):
        """差值不是 0 或 1（例如中间拿了循环往复 +3）：不下结论。"""
        self.level_up(1, 4)
        self.assertIsNone(self.s.game.per_star_rerolls)

    def test_unreadable_count_uses_inferred_default(self):
        """读不出刷新次数时的默认值：有加成按 2 + 星数，没有加成只算开局的 2 次。局面记下这是按默认值补的
        （State.rerolls_known 为 False），引擎不按多出来的刷新算；读到了的为 True。"""
        for after, expected in ((2, 4), (1, 2)):
            with self.subTest(after=after):
                self.setUp()
                read = self.level_up(1, after)
                self.assertTrue(read.state.rerolls_known)
                ev = self.s.on_frame(frame(["大背包", "缓冲盾牌", "标靶"], rerolls=None, probabilities=TWO_STAR))
                self.assertEqual(ev.state.rerolls, expected)
                self.assertFalse(ev.state.rerolls_known)
                self.assertFalse(ev.advice.reroll.known)
                self.assertEqual(ev.advice.reroll.spare, 0)  # 之后还有三选一：不按多出来的算，只刷一次、扣代价
                self.assertGreater(ev.advice.reroll.cost, 0)

    def test_reset_by_new_game(self):
        self.level_up(1, 1)
        self.s.new_game()
        self.assertIsNone(self.s.game.per_star_rerolls)


if __name__ == "__main__":
    unittest.main()
