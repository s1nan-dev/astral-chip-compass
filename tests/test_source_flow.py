"""三选一的来源：升星还是筹码商店（Session._source_of、Session._settle_sources）。

升星和筹码商店的概率行、最左格都一样，记账按画面上别的迹象分：出现之前不久读到的自己的升星横幅（on_levelup_banner）、
玩家列表上的星数涨了，算升星；出现之前不久看到的筹码商店购买窗（on_shop_window）、出现之前已经是 3 星，算筹码商店；
都没有时拿不准，关掉以后第一次在对局画面上读到星数时按涨没涨补准（source_settled）。怎么定的写在推荐事件上
（Event.source_by）。任务的照旧按概率行判。
写法照 tests/test_session.py：假时钟，喂识别结果，看事件和局面。
"""
import unittest

from advisor.data import load_db
from advisor.session import SOURCE_CUE_S, _event_record
from tests.test_session import OFFER_A, OFFER_B, chips, close, kinds, own, ready, screen

OWN_ROW = 1  # 自己在玩家列表上的那一行（从 0 数）
OWN_RANK = OWN_ROW + 1  # 自己升星时横幅上的名次（从 1 数）
MATE_RANK = 3  # 队友升星时横幅上的名次

ONE = (60, 37, 3)  # 0、1 星时升星和筹码商店的概率行（困难及以上）
TWO = (40, 45, 15)  # 2 星
THREE = (25, 50, 25)  # 3 星
OFFER_TWO = ["大背包", "标靶", "缓冲盾牌"]  # 最左格是紫色：2 星时固定的稀有度
OFFER_THREE = ["星币锤", "维生素药丸", "摩托头盔-中级"]  # 最左格是金色：3 星时固定的稀有度


class SourceBase(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.clock = [100.0]
        self.s._clock = lambda: self.clock[0]
        self.s.note_own(OWN_ROW)

    def later(self, seconds: float):
        self.clock[0] += seconds

    def stars(self, n: int, times: int = 1):
        """对局画面上读到 n 星（times 次）。"""
        for _ in range(times):
            self.s.on_stars(own(n))

    def advice(self, names, probabilities=ONE, **kw):
        ev = self.s.on_frame(chips(names, probabilities=probabilities, **kw))
        self.assertEqual(ev.kind, "advice")
        return ev

    def settled(self) -> list:
        """取走待取的事件，留下补准来源的（第几次三选一, 来源, 依据）。"""
        return [(e.select_no, e.source, e.source_by) for e in kinds(self.s, "source_settled")]


class SourceDecideTest(SourceBase):
    """第一次给推荐时按出现之前收下的迹象定：横幅 → 星数 → 购买窗 → 已经 3 星 → 拿不准。"""

    def test_own_banner_means_levelup(self):
        """自己那一行被标题带挡住、记下的还是 1 星：横幅名次对得上自己那一行，算升星。"""
        self.stars(1)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("levelup", "banner", 2))

    def test_teammate_banner_is_guess(self):
        self.stars(1)
        self.s.on_levelup_banner(MATE_RANK)
        self.later(1.0)
        ev = self.advice(OFFER_A)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "guess"))

    def test_banner_counts_once_own_row_is_known(self):
        """还不知道自己在第几行时横幅不算；同一次三选一里认出了自己那一行，按横幅重定、重发推荐（局面没变也发）。"""
        self.s.game.own_row = None
        self.stars(1)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        first = self.advice(OFFER_A)
        self.assertEqual(first.source_by, "guess")
        self.s.note_own(OWN_ROW)
        again = self.s.on_frame(chips(OFFER_A, probabilities=ONE))
        self.assertEqual((again.kind, again.source_by, again.state), ("advice", "banner", first.state))
        self.assertIsNone(self.s.on_frame(chips(OFFER_A, probabilities=ONE)))  # 之后照常不重复发

    def test_shop_window_means_shop(self):
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(1.0)
        ev = self.advice(OFFER_A)
        self.assertEqual((ev.state.source, ev.source_by), ("shop", "shop_window"))

    def test_banner_wins_over_shop_window(self):
        """两样都有（看了购买窗、点了离开，随后自己升星）：按升星。"""
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(0.5)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "banner"))

    def test_star_gain_wins_over_shop_window(self):
        self.stars(1)
        self.s.on_shop_window(20)
        self.stars(2)
        self.later(1.0)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "stars"))

    def test_stale_banner_is_not_counted(self):
        self.stars(1)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(SOURCE_CUE_S + 0.5)
        self.assertEqual(self.advice(OFFER_TWO, TWO).source_by, "guess")

    def test_stale_shop_window_is_not_counted(self):
        """早些时候看过购买窗（点了离开）：过了 SOURCE_CUE_S 才出的三选一不算筹码商店。"""
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(SOURCE_CUE_S + 0.5)
        ev = self.advice(OFFER_A)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "guess"))

    def test_cue_counts_for_the_next_select_only(self):
        """一个迹象只算给它之后的第一次三选一：关掉以后紧接着的下一次，还在时限以内也不算。"""
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(1.0)
        self.assertEqual(self.advice(OFFER_A).source_by, "shop_window")
        close(self.s)
        self.later(1.0)
        self.assertEqual(self.advice(OFFER_B).source_by, "guess")

    def test_cue_before_a_task_select_is_void(self):
        """横幅之后先来了一次全队的任务三选一：横幅算给了那一次，关掉以后的下一次不算。"""
        self.stars(1)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        task = self.advice(OFFER_A, team=True)
        self.assertEqual((task.state.source, task.source_by), ("task", "rows"))
        close(self.s)
        self.later(1.0)
        self.assertEqual(self.advice(OFFER_TWO, TWO).source_by, "guess")

    def test_star_gain_before_select(self):
        """新的星先在玩家列表上亮，三选一随后出现。"""
        self.stars(1)
        self.stars(2)
        self.later(1.0)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("levelup", "stars", 2))

    def test_star_gain_after_task_select(self):
        """任务的三选一关掉以后、下一次出现之前星数涨了 1（中间没读到过星数）：任务不升星，涨的这一颗算这一次的；
        关掉以后不再补。"""
        self.stars(1)
        task = self.advice(OFFER_A, TWO)  # 1 星、概率行是 2 星那一行、最左格蓝色：任务 2 级
        self.assertEqual(task.source_by, "rows")
        close(self.s)
        self.stars(2)
        self.later(1.0)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "stars"))
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [])

    def test_star_gain_after_task_with_unseen_select_before(self):
        """拿不准的一次关掉以后没读到过星数，接着一次任务，之后星数涨了 1：涨的这一颗也可能是更早那一次的，之后这一次
        不按它定（拿不准）；涨的那一下照常拿去补准更早那一次。"""
        self.stars(1)
        first = self.advice(OFFER_A)
        close(self.s)
        self.assertEqual(self.advice(OFFER_B, TWO).source_by, "rows")  # 最左格蓝色：任务 2 级
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [(first.select_no, "levelup", "stars")])
        self.later(1.0)
        self.assertEqual(self.advice(OFFER_TWO, TWO).source_by, "guess")

    def test_star_up_read_on_first_frame(self):
        """三选一出现的那一帧在三选一画面上读到多 1 星：改之前的星数是出现之前的。"""
        self.stars(1)
        self.s.on_stars(own(2), in_select=True)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("levelup", "stars", 2))
        self.assertEqual(self.s.game._select_rec["before"], 1)

    def test_star_up_read_later_re_advises(self):
        """出现时读不到星数（拿不准），后来在三选一画面上读到多 1 星：按星数重定，局面没变也重发推荐。"""
        self.stars(1)
        first = self.advice(OFFER_TWO, TWO)
        self.assertEqual(first.source_by, "guess")
        self.s.on_stars(own(2), in_select=True)
        again = self.s.on_frame(chips(OFFER_TWO, probabilities=TWO))
        self.assertEqual((again.kind, again.source_by, again.state.source), ("advice", "stars", "levelup"))
        self.assertEqual(again.state, first.state)
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [])  # 星数定的不补

    def test_star_up_in_select_not_counted_after_unseen_select(self):
        """上一次三选一关掉以后没读到过星数：这一次三选一画面上多的 1 星也可能是上一次升的，不认，照旧拿不准。"""
        self.stars(1)
        self.advice(OFFER_A)
        close(self.s)
        self.advice(OFFER_TWO, TWO)
        self.s.on_stars(own(2), in_select=True)
        ev = self.s.on_frame(chips(OFFER_TWO, probabilities=TWO))
        self.assertIsNone(ev)  # 依据没变，不重发
        self.assertEqual(self.s.select._source_by, "guess")

    def test_three_stars_means_shop(self):
        self.stars(3)  # 这一局第一次读到就是 3 星
        ev = self.advice(OFFER_THREE, THREE)
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("shop", "stars", 3))

    def test_select_that_reached_three_stars_is_levelup(self):
        self.stars(2)
        self.stars(3)
        ev = self.advice(OFFER_THREE, THREE)
        self.assertEqual((ev.state.source, ev.source_by), ("levelup", "stars"))

    def test_zero_stars_banner_counts_the_new_star(self):
        """0 星时自己的横幅之后出的：就是升到 1 星的那一次，按 1 星算（刷新次数看不出来也一样），记下的星数不改。"""
        self.stars(0)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        ev = self.advice(OFFER_A, rerolls=2)  # 次数和 0 星时一样：只看次数会按 0 星算
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("levelup", "banner", 1))
        self.assertEqual((self.s.settings.star, self.s.settings.star_read), (0, True))

    def test_zero_stars_shop_window_stays_zero(self):
        """0 星时购买窗之后出的：筹码商店不升星，按 0 星算（刷新次数多 1 也一样）。"""
        self.stars(0)
        self.s.on_shop_window(15)
        self.later(1.0)
        ev = self.advice(OFFER_A, rerolls=3)  # 只看次数会按升到 1 星算
        self.assertEqual((ev.state.source, ev.source_by, ev.state.star), ("shop", "shop_window", 0))

    def test_task_select_unchanged(self):
        """概率行认出是任务的照旧（依据 rows），横幅不改它；这一局非任务三选一的次数不变，关掉以后也不补。"""
        self.stars(1)
        self.s.on_levelup_banner(OWN_RANK)
        self.later(1.0)
        opens = self.s.game._game_opens
        ev = self.advice(OFFER_A, TWO)  # 1 星、概率行是 2 星那一行、最左格蓝色：任务 2 级
        self.assertEqual((ev.state.source, ev.source_by, ev.state.tier), ("task", "rows", 2))
        self.assertEqual(self.s.game._game_opens, opens)
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [])

    def test_reopened_select_keeps_its_record(self):
        """同一组候选关了又出现：仍按出现之前收下的购买窗定，补准时用又出现时的编号。"""
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(1.0)
        first = self.advice(OFFER_A)
        close(self.s)  # 没选就关了
        self.later(1.0)
        again = self.advice(OFFER_A)
        self.assertEqual((again.select_no, again.source_by), (first.select_no + 1, "shop_window"))
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [(again.select_no, "levelup", "stars")])

    def test_cues_ignored_when_unreasonable_or_game_over(self):
        for rank in (0, 9, True, None, "2"):
            self.s.on_levelup_banner(rank)
        self.assertIsNone(self.s.game._banner_cue)
        self.s.on_shop_window(20)
        self.later(0.5)
        self.s.on_shop_window(None)  # 这一帧价格读不出：沿用刚才读到的
        self.assertEqual(self.s.game._shop_cue, (100.5, 20))
        self.s.on_out_of_game(screen("result"))
        self.assertTrue(self.s.game.game_over)
        self.s.game._banner_cue = self.s.game._shop_cue = None
        self.s.on_levelup_banner(OWN_RANK)
        self.s.on_shop_window(20)
        self.assertEqual((self.s.game._banner_cue, self.s.game._shop_cue), (None, None))

    def test_event_record_has_source_by(self):
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(1.0)
        ev = self.advice(OFFER_A)
        self.assertEqual(_event_record(ev)["source_by"], "shop_window")
        close(self.s)
        self.stars(2)
        [settled] = kinds(self.s, "source_settled")
        record = _event_record(settled)
        self.assertEqual({k: record.get(k) for k in ("kind", "select_no", "source", "source_by")},
                         {"kind": "source_settled", "select_no": ev.select_no, "source": "levelup", "source_by": "stars"})


class SourceSettleTest(SourceBase):
    """拿不准的、按购买窗定的，关掉以后第一次在对局画面上读到星数时按涨没涨补准。"""

    def test_guess_then_star_up_is_levelup(self):
        self.stars(1)
        ev = self.advice(OFFER_TWO, TWO)
        self.assertEqual(ev.source_by, "guess")
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [(ev.select_no, "levelup", "stars")])

    def test_guess_then_same_star_is_shop(self):
        self.stars(1)
        ev = self.advice(OFFER_A)
        close(self.s)
        self.stars(1)
        self.assertEqual(self.settled(), [(ev.select_no, "shop", "stars")])
        self.stars(2)
        self.assertEqual(self.settled(), [])  # 补过的不再补

    def test_shop_window_then_star_up_is_levelup(self):
        self.stars(1)
        self.s.on_shop_window(20)
        self.later(1.0)
        ev = self.advice(OFFER_A)
        self.assertEqual(ev.source_by, "shop_window")
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [(ev.select_no, "levelup", "stars")])

    def test_row_one_star_up_without_star_up_not_shop(self):
        """概率行比记下的星数正好高一星（最左格对得上，按刚升星算），关掉以后星数却没涨：筹码商店的概率行是当时星数的那一行，
        这几次不补成筹码商店，留着拿不准（按购买窗定的也一样）；同一段里概率行对得上当时星数的照常补成筹码商店。星数涨了的
        照常补成升星（test_guess_then_star_up_is_levelup）。"""
        self.stars(1)
        a = self.advice(OFFER_TWO, TWO)
        self.assertEqual((a.state.star, a.source_by), (2, "guess"))
        close(self.s)
        self.s.on_shop_window(20)
        self.later(1.0)
        b = self.advice(["额外电池", "标靶", "缓冲盾牌"], TWO)  # 另一组候选（同一组又出现算同一次），最左格也是紫色
        self.assertEqual((b.state.star, b.state.source, b.source_by), (2, "shop", "shop_window"))
        close(self.s)
        c = self.advice(OFFER_A)
        self.assertEqual((c.state.star, c.source_by), (1, "guess"))
        close(self.s)
        self.stars(1)
        self.assertEqual(self.settled(), [(c.select_no, "shop", "stars")])
        traced = [r for r in self.s.recent_events(60) if r["kind"] == "read:source_settle"]
        self.assertEqual((traced[-1]["source"], traced[-1]["row_up"]), ("shop", [a.select_no, b.select_no]))
        self.stars(1)
        self.assertEqual(self.settled(), [])  # 这一段对过账了：没补的不再补

    def test_read_before_close_waits(self):
        """三选一刚看不到、还没按关掉处理时读到的星数先不用，关掉以后的下一次再补。"""
        self.stars(1)
        ev = self.advice(OFFER_A)
        self.s.on_frame(None)
        self.stars(1)
        self.assertEqual(self.settled(), [])
        self.s.on_frame(None)  # 关掉
        self.stars(1)
        self.assertEqual(self.settled(), [(ev.select_no, "shop", "stars")])

    def test_two_selects_mismatch_not_settled(self):
        """两次三选一之间没读到星数，之后只涨了 1 星：分不出是哪一次，都不补；这一段清掉，之后也不补。"""
        self.stars(1)
        self.advice(OFFER_A)
        close(self.s)
        self.advice(OFFER_B)
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [])
        self.stars(2)
        self.assertEqual(self.settled(), [])

    def test_two_selects_both_levelup(self):
        self.stars(1)
        a = self.advice(OFFER_A)
        close(self.s)
        b = self.advice(OFFER_B)
        close(self.s)
        self.stars(3, times=2)  # 跳两颗：连续读到两次才采用
        self.assertEqual(self.settled(), [(a.select_no, "levelup", "stars"), (b.select_no, "levelup", "stars")])

    def test_two_selects_both_shop(self):
        self.stars(1)
        a = self.advice(OFFER_A)
        close(self.s)
        b = self.advice(OFFER_B)
        close(self.s)
        self.stars(1)
        self.assertEqual(self.settled(), [(a.select_no, "shop", "stars"), (b.select_no, "shop", "stars")])

    def test_star_up_right_after_own_banner_waits_for_its_select(self):
        """拿不准的一次关掉以后自己升星：横幅之后星数先涨、升星的三选一随后才出。涨的这 1 星是之后那一次的，先不对账；
        那一次关掉以后对账：横幅定的升星扣掉，之前那一次没涨，是筹码商店。"""
        self.stars(1)
        a = self.advice(OFFER_A)
        close(self.s)
        self.s.on_levelup_banner(OWN_RANK)
        self.stars(2)
        self.assertEqual(self.settled(), [])
        self.later(1.0)
        b = self.advice(OFFER_TWO, TWO)
        self.assertEqual(b.source_by, "banner")
        close(self.s)
        self.stars(2)
        self.assertEqual(self.settled(), [(a.select_no, "shop", "stars")])

    def test_banner_without_its_select_not_settled(self):
        """自己的横幅以后过了 SOURCE_CUE_S 那次三选一还没出（没认出来）：涨的星数对不上之前那一次，不补。"""
        self.stars(1)
        self.advice(OFFER_A)
        close(self.s)
        self.s.on_levelup_banner(OWN_RANK)
        self.stars(2)
        self.later(SOURCE_CUE_S + 1.0)
        self.stars(2)
        self.assertEqual(self.settled(), [])

    def test_game_over_and_new_game_drop_unsettled(self):
        """本局结束以后读到的星数不补；开了新的一局，上一局没补上的不再补。"""
        self.stars(1)
        self.advice(OFFER_A)
        close(self.s)
        self.s.on_out_of_game(screen("result"))
        self.assertTrue(self.s.game.game_over)
        self.stars(2)
        self.assertEqual(self.settled(), [])
        self.s.new_game()
        self.stars(0)
        self.stars(1)
        self.assertEqual(self.settled(), [])


if __name__ == "__main__":
    unittest.main()
