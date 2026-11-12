"""一局的胜负（通关还是失败）：结算页之前的通关画面「VICTORY」（Session.on_victory）、打输时棋盘上的「GAME OVER」横幅
（Session.on_game_over_banner）和结束以后奖励画面上「星币」那一格的数（advisor/session/offgame.py 的 outcome、reward_read），
记进对局结束的内容（game_over_info 的 outcome、how）。

  判定表   看到通关画面算通关、依据写通关画面；只看到打输的横幅算失败、依据写横幅（星币是 0、10、读不到都一样）；两种画面
           都看到算矛盾；没看到这两种时按星币数：≥ 25 通关、正好 10 失败，依据写奖励画面，别的数（和电脑打的局给的 0、
           读漏一位的 +3）不知道；两边说的不一样时两项都不知道；什么都没有不算失败
  时效     通关画面、横幅都要在结束前 60 秒之内读到；没有对局状态、本局已结束以后读到的不记
  定了     结束以后读到奖励画面（result_settled）：第一次读到时胜负没变也发 status（悬浮窗据此拼报告），变了时改掉、发 status
  其余     星币数取读到过的最大的；奖励画面结束本局时当场用上它的星币数；撤销了房间等画面结束以后开的新一局时，胜负的依据
           一起作废；新的一局从头算
悬浮窗上不显示胜负（只进推荐日志和对局报告），这里只核对记账，不碰界面。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from advisor.data import load_db
from advisor.session import GameState, Session, Settings, offgame

RESULT = SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=12)
ENDED_KEYS = {"why", "map", "difficulty", "level", "rounds", "cap", "difficulty_source", "cap_rejected",
              "outcome", "how", "map_source", "map_in_game"}


def screen(kind, coins=None):
    """对局外画面的识别结果（advisor.vision.screens.OutOfGame 的样子）。"""
    return SimpleNamespace(kind=kind, difficulty=None, map=None, rounds=None, coins=coins)


def reward(coins=None):
    return screen("reward", coins)


class DecisionTableTest(unittest.TestCase):
    """offgame.outcome 的判定表：直接摆这一局的几项依据（结束在第 1000 秒）。"""

    @staticmethod
    def game(victory=None, banner=None, coins=None, ended=1000.0):
        g = GameState()
        g._victory_at, g._game_over_at, g._reward_coins, g._ended_at = victory, banner, coins, ended
        return g

    def test_table(self):
        win_v, win_r, loss_r, loss_go, unknown = (("win", "victory"), ("win", "reward"), ("loss", "reward"),
                                                  ("loss", "game_over"), (None, None))
        cases = [
            ("通关画面", 998.0, None, None, win_v),
            ("通关画面、星币多", 998.0, None, 30, win_v),
            ("通关画面、星币拿不准", 998.0, None, 20, win_v),
            ("通关画面、星币少：矛盾", 998.0, None, 10, unknown),
            ("通关画面正好在结束前 60 秒", 940.0, None, None, win_v),
            ("通关画面在结束前 60 秒以上：不算", 939.0, None, None, unknown),
            ("通关画面太早、星币多", 900.0, None, 45, win_r),
            ("通关画面、星币 0（和电脑打的局）", 998.0, None, 0, win_v),
            ("横幅", None, 998.0, None, loss_go),
            ("横幅、星币 0（和电脑打的局）", None, 998.0, 0, loss_go),
            ("横幅、星币正好 10", None, 998.0, 10, loss_go),
            ("横幅、星币拿不准（20）也算失败", None, 998.0, 20, loss_go),
            ("横幅、星币多：矛盾", None, 998.0, 30, unknown),
            ("横幅、星币 25：矛盾", None, 998.0, 25, unknown),
            ("横幅正好在结束前 60 秒", None, 940.0, None, loss_go),
            ("横幅在结束前 60 秒以上：不算", None, 939.0, None, unknown),
            ("通关画面和横幅都看到：矛盾", 998.0, 998.0, None, unknown),
            ("通关画面和横幅都太早", 900.0, 900.0, 45, win_r),
            ("星币 25", None, None, 25, win_r),
            ("星币 30", None, None, 30, win_r),
            ("星币 45", None, None, 45, win_r),
            ("星币正好 10", None, None, 10, loss_r),
            ("星币 0：和电脑打的局输赢都是 0", None, None, 0, unknown),
            ("星币 3：+30 读漏一位", None, None, 3, unknown),
            ("星币 5：+35 读漏一位", None, None, 5, unknown),
            ("星币 1：+10 读漏一位", None, None, 1, unknown),
            ("星币 15", None, None, 15, unknown),
            ("星币 24", None, None, 24, unknown),
            ("什么都没有：不算失败", None, None, None, unknown),
        ]
        for name, victory, banner, coins, want in cases:
            with self.subTest(name):
                self.assertEqual(offgame.outcome(self.game(victory, banner, coins)), want)
        self.assertEqual(offgame.outcome(self.game(998.0, ended=None)), unknown)  # 还没结束
        self.assertEqual(offgame.outcome(self.game(banner=998.0, ended=None)), unknown)  # 还没结束

    def test_thresholds(self):
        self.assertEqual((offgame.REWARD_WIN_COINS, offgame.REWARD_LOSS_COINS), (25, 10))
        self.assertEqual(offgame.VICTORY_BEFORE_END_S, 60.0)
        self.assertEqual((offgame.BY_VICTORY, offgame.BY_GAME_OVER, offgame.BY_REWARD),
                         ("victory", "game_over", "reward"))


class OutcomeFlowTest(unittest.TestCase):
    """真实的 Session：打到第 12 轮，读到（或没读到）通关画面，结算画面结束本局，再读到奖励画面。"""

    def setUp(self):
        self.db = load_db()
        self.t = 1000.0

    def tick(self, seconds=3.0):
        self.t += seconds

    def playing(self) -> Session:
        s = Session(self.db, Settings(hero="阿尔"))
        s.game.seen_hero = "阿尔"
        s._clock = lambda: self.t
        for _ in range(2):
            self.tick()
            s.on_hud(SimpleNamespace(round=12, map="异变图书馆"))
            s.on_stars(SimpleNamespace(stars=3, confidence=0.9))
            s.on_progress(12, 17)
        s.take_events()
        return s

    @staticmethod
    def kinds(s) -> list[str]:
        return [e.kind for e in s.take_events()]

    @staticmethod
    def result(s) -> tuple:
        info = s.game_over_info
        return info["outcome"], info["how"]

    def end(self, s, victory: bool, banner: bool = False) -> None:
        """（读到通关画面、打输的横幅、）隔 2 秒读到结算画面：本局结束。"""
        if victory:
            self.tick()
            s.on_victory()
            self.assertEqual(self.kinds(s), [])  # 不发事件：悬浮窗上不显示
        if banner:
            self.tick()
            s.on_game_over_banner()
            self.assertEqual(self.kinds(s), [])
        self.tick(2)
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.kinds(s)

    def test_victory_then_matching_reward(self):
        """通关画面 → 结算画面：结束时就记通关、依据通关画面，还没定；奖励画面星币也多：胜负不变，第一次读到时发一个
        status，算定了；之后再读到不再发。"""
        s = self.playing()
        self.end(s, victory=True)
        self.assertEqual(set(s.game_over_info), ENDED_KEYS)
        self.assertEqual(self.result(s), ("win", "victory"))
        self.assertFalse(s.result_settled)
        self.tick(20)
        self.assertIsNone(s.on_out_of_game(reward(30)))
        self.assertEqual(self.kinds(s), ["status"])
        self.assertTrue(s.result_settled)
        self.assertEqual(self.result(s), ("win", "victory"))
        self.tick(1)
        s.on_out_of_game(reward(30))
        self.assertEqual(self.kinds(s), [])
        traced = [r["kind"] for r in s.recent_events(120)]
        self.assertIn("read:victory", traced)
        self.assertNotIn("read:game_result", traced)  # 胜负没变

    def test_banner_then_result(self):
        """打输的横幅 → 结算画面：结束时就记失败、依据横幅，还没定；奖励画面星币是 0（和电脑打的局）也照样是失败，第一次
        读到时发一个 status，算定了。"""
        s = self.playing()
        self.end(s, victory=False, banner=True)
        self.assertEqual(self.result(s), ("loss", "game_over"))
        self.assertFalse(s.result_settled)
        self.tick(20)
        self.assertIsNone(s.on_out_of_game(reward(0)))
        self.assertEqual(self.kinds(s), ["status"])
        self.assertTrue(s.result_settled)
        self.assertEqual(self.result(s), ("loss", "game_over"))
        traced = [r["kind"] for r in s.recent_events(120)]
        self.assertIn("read:game_over_banner", traced)
        self.assertNotIn("read:game_result", traced)

    def test_banner_contradicted_by_reward(self):
        """横幅、奖励画面星币又像是通关（≥ 25）：两边矛盾，两项都不知道，发 status、记一条识别读数。"""
        s = self.playing()
        self.end(s, victory=False, banner=True)
        self.tick(20)
        s.on_out_of_game(reward(30))
        self.assertEqual(self.kinds(s), ["status"])
        self.assertEqual(self.result(s), (None, None))
        self.assertTrue(s.result_settled)
        record = next(r for r in s.recent_events(120) if r["kind"] == "read:game_result")
        self.assertEqual((record["outcome"], record["how"], record["coins"]), (None, None, 30))

    def test_victory_and_banner_conflict(self):
        """通关画面和横幅都看到了（认错了一次）：矛盾，两项都不知道。"""
        s = self.playing()
        self.end(s, victory=True, banner=True)
        self.assertEqual(self.result(s), (None, None))

    def test_banner_too_early(self):
        """对局中途认错的一次横幅（结束前一分钟以上）不算这一局的。"""
        s = self.playing()
        s.on_game_over_banner()
        self.tick(61)
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.assertEqual(self.result(s), (None, None))

    def test_banner_needs_game_in_progress(self):
        """没有对局状态时、本局已结束以后读到横幅都不记。"""
        s = Session(self.db, Settings(hero="阿尔"))
        s._clock = lambda: self.t
        s.on_game_over_banner()
        self.assertIsNone(s.game._game_over_at)
        s = self.playing()
        self.end(s, victory=False)
        self.tick(1)
        s.on_game_over_banner()
        self.assertIsNone(s.game._game_over_at)
        self.assertNotIn("read:game_over_banner", [r["kind"] for r in s.recent_events(120)])

    def test_victory_contradicted(self):
        """通关画面、奖励画面星币又少：两边矛盾，两项都不知道，发 status、记一条识别读数。"""
        s = self.playing()
        self.end(s, victory=True)
        self.tick(20)
        s.on_out_of_game(reward(10))
        self.assertEqual(self.kinds(s), ["status"])
        self.assertEqual(self.result(s), (None, None))
        self.assertTrue(s.result_settled)
        record = next(r for r in s.recent_events(120) if r["kind"] == "read:game_result")
        self.assertEqual((record["outcome"], record["how"], record["coins"]), (None, None, 10))

    def test_reward_only(self):
        """没看到通关画面：结束时两项都不知道；奖励画面按星币数定（≥ 25 通关、正好 10 失败），0（和电脑打的局）、读漏一位的
        +3、别的数、读不出的照旧不知道，也算定了。"""
        for coins, want in ((30, ("win", "reward")), (25, ("win", "reward")), (10, ("loss", "reward")),
                            (0, (None, None)), (3, (None, None)), (20, (None, None)), (None, (None, None))):
            with self.subTest(coins=coins):
                s = self.playing()
                self.end(s, victory=False)
                self.assertEqual(self.result(s), (None, None))
                self.tick(20)
                s.on_out_of_game(reward(coins))
                self.assertEqual(self.kinds(s), ["status"])
                self.assertEqual(self.result(s), want)
                self.assertTrue(s.result_settled)

    def test_reward_keeps_largest(self):
        """奖励画面滑进时只读到半截（+30 读成 +3）：先不知道（不当成失败），读到 30 改成通关，之后读到小的（10）不再改回去。"""
        s = self.playing()
        self.end(s, victory=False)
        self.tick(20)
        s.on_out_of_game(reward(3))
        self.assertEqual(self.result(s), (None, None))
        self.assertEqual(self.kinds(s), ["status"])  # 第一次读到奖励画面：胜负没变也发
        self.tick(1)
        s.on_out_of_game(reward(30))
        self.assertEqual(self.kinds(s), ["status"])
        self.assertEqual(self.result(s), ("win", "reward"))
        self.tick(1)
        s.on_out_of_game(reward(10))
        self.assertEqual(self.kinds(s), [])
        self.assertEqual(self.result(s), ("win", "reward"))

    def test_loss_needs_whole_number(self):
        """失败的 +10 读漏一位成了 +1：先不知道，读全了才算失败。"""
        s = self.playing()
        self.end(s, victory=False)
        self.tick(20)
        s.on_out_of_game(reward(1))
        self.assertEqual(self.result(s), (None, None))
        self.tick(1)
        s.on_out_of_game(reward(10))
        self.assertEqual(self.result(s), ("loss", "reward"))

    def test_odd_coins_ignored(self):
        """星币数不是非负整数（布尔、负数、文字）的不算，奖励画面照样算读到过。"""
        for coins in (True, -5, "30", 2.5):
            with self.subTest(coins=coins):
                s = self.playing()
                self.end(s, victory=False)
                self.tick(20)
                s.on_out_of_game(reward(coins))
                self.assertEqual(self.result(s), (None, None))
                self.assertTrue(s.result_settled)
                self.assertIsNone(s.game._reward_coins)

    def test_victory_too_early(self):
        """对局中途认错的一次（结束前一分钟以上）不算这一局的。"""
        s = self.playing()
        s.on_victory()
        self.tick(61)
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.assertEqual(self.result(s), (None, None))

    def test_victory_needs_game_in_progress(self):
        """没有对局状态时、本局已结束以后读到通关画面都不记。"""
        s = Session(self.db, Settings(hero="阿尔"))
        s._clock = lambda: self.t
        s.on_victory()
        self.assertIsNone(s.game._victory_at)
        s = self.playing()
        self.end(s, victory=False)
        self.tick(1)
        s.on_victory()
        self.assertIsNone(s.game._victory_at)
        self.assertNotIn("read:victory", [r["kind"] for r in s.recent_events(120)])

    def test_not_settled_before_reward(self):
        """对局里、刚结束都不算定了；奖励画面要在结束以后读到（对局里只读到一次的不算）。"""
        s = self.playing()
        self.assertFalse(s.result_settled)
        s.on_out_of_game(reward(30))  # 对局状态还在：只读到一次，还不算结束
        self.assertFalse(s.game.game_over)
        self.assertFalse(s.result_settled)
        self.assertFalse(s.game._reward_seen)

    def test_reward_ends_game(self):
        """结算画面没认出、奖励画面隔一会儿读到两次结束本局：结束时当场用上第二次的星币数，算定了。"""
        s = self.playing()
        self.tick()
        self.assertIsNone(s.on_out_of_game(reward(30)))
        self.tick()
        ev = s.on_out_of_game(reward(30))
        self.assertEqual(ev.kind, "game_over")
        self.assertEqual(self.result(s), ("win", "reward"))
        self.assertTrue(s.result_settled)
        self.assertIn("status", self.kinds(s))

    def test_undo_drops_reward(self):
        """房间画面结束本局（误认）、又读到了奖励画面，之后开的新一局撤销了：那次结束连同胜负的依据一起撤回，这一局
        真正结束时从头算。"""
        s = self.playing()
        room = screen("team_room")
        self.tick()
        s.on_out_of_game(room)
        self.tick()
        self.assertEqual(s.on_out_of_game(room).kind, "game_over")
        self.tick()
        s.on_out_of_game(reward(30))
        self.assertTrue(s.result_settled)
        self.assertEqual(self.result(s), ("win", "reward"))
        for _ in range(2):  # 同一局接着打：隔一会儿连续两次读到第 12 轮，判为新的一局（可以撤销）
            self.tick()
            s.on_hud(SimpleNamespace(round=12, map="异变图书馆"))
        self.assertTrue(s.can_undo_new_game)
        self.assertTrue(s.undo_new_game())
        self.assertFalse(s.game.game_over)
        self.assertFalse(s.result_settled)
        self.assertEqual((s.game._ended_at, s.game._reward_coins), (None, None))
        self.tick()
        s.on_out_of_game(RESULT)
        self.assertEqual(self.result(s), (None, None))
        self.assertFalse(s.result_settled)

    def test_undo_drops_banner(self):
        """房间画面结束本局（误认）之前认错过一次横幅、通关画面：撤销以后这一局接着打，一分钟以内真的结束时也不算它们。"""
        for read in ("on_game_over_banner", "on_victory"):
            with self.subTest(read):
                s = self.playing()
                getattr(s, read)()
                room = screen("team_room")
                self.tick()
                s.on_out_of_game(room)
                self.tick()
                self.assertEqual(s.on_out_of_game(room).kind, "game_over")
                self.assertIsNotNone(self.result(s)[0])
                for _ in range(2):
                    self.tick()
                    s.on_hud(SimpleNamespace(round=12, map="异变图书馆"))
                self.assertTrue(s.undo_new_game())
                self.assertEqual((s.game._victory_at, s.game._game_over_at), (None, None))
                self.tick()
                s.on_out_of_game(RESULT)
                self.assertEqual(self.result(s), (None, None))

    def test_next_game_starts_clean(self):
        """结算画面之后的选角界面开了新的一局：胜负的依据都是新的。"""
        s = self.playing()
        self.end(s, victory=True, banner=True)
        self.tick(20)
        s.on_out_of_game(reward(30))
        self.tick()
        self.assertEqual(s.on_select_screen().kind, "newgame")
        g = s.game
        self.assertEqual((g._victory_at, g._game_over_at, g._ended_at, g._reward_seen, g._reward_coins),
                         (None, None, None, False, None))
        self.assertFalse(s.result_settled)
        self.assertIsNone(s.game_over_info)


if __name__ == "__main__":
    unittest.main()
