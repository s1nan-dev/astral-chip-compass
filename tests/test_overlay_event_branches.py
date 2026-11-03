"""Overlay.handle 里几个分支的行为级测试（picked、pick_reverted、map_read、game_over）：只用公开探针（status_text、
summary_text、notice_texts、visible_texts、card_open、needs_attention 等）和推荐日志的假对象记下的调用，断言用户
看得到的文字和推荐日志里记了什么。补 tests/test_overlay_behavior.py 没覆盖到的：

- picked：选择记在给出推荐的那一次三选一上；没给出推荐（候选没认全）就选了时，只写「已选」，不问未选择首推的原因。
- pick_reverted：同一组候选又出现时，推荐日志里撤销刚记下的选择，这一组候选沿用原来的编号；之后选了别的，记在同一个
  编号上，已有筹码里换成新选的。
- map_read：读到地图以后，「打开任务栏」的提示收起，状态里写地图名。
- game_over：推荐日志记一次对局结束（同一局再送到一次也不重复记），带星数和已有筹码；只对进行中的对局有意义的提示
  （星数增加需补录）收起，边框不变色。
- 房间画面结束的局，之后的新一局撤销了：结束时收起的「查看筹码」提示、待补录的提示、补录行（连同「再次出现、已撤销」的
  说法）都回来；撤销说明那次结束是误认的，这一局之后真正结束时推荐日志照常再记一条对局结束（同一局以后一条为准），
  再读到结算画面也不重复记。

事件由真实的 Session 产生、按 FrameProcessor 的顺序交给悬浮窗（BehaviorBase，见 tests/test_overlay_behavior.py）。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from advisor.ui.overlay_parts.wording import DISMISS, FEEDBACK_QUESTION, GAIN_TEXT, MAP_READ, OPEN_TASKS, PICKED_LABEL, PROMPT_PANEL, REVERTED_PROMPT
from tests.test_overlay_behavior import OFFER, RESULT, BehaviorBase
from tests.test_overlay_room_undo import ROOM


class PickedBranchTest(BehaviorBase):
    def test_pick_logged_on_its_offer(self):
        self.enter()
        self.offer()
        offer_id = self.logbook.offers[-1][0]
        self.offer(selected=1)
        self.assertEqual(self.kinds(self.close()), ["picked"])
        self.assertEqual(self.logbook.picks, [(offer_id, OFFER[1])])
        self.w.set_expanded(True)
        self.assertIn(OFFER[1], "\n".join(self.texts("owned")))

    def test_pick_without_advice_asks_nothing(self):
        """候选筹码没认全（没给出推荐）时选了一个：写「已选」，不写「（首推）」也不问原因；推荐日志里没有这一次。"""
        self.enter()
        names = [OFFER[0], None, OFFER[2]]
        seen = dict(names=names, raw=[OFFER[0], "高效", OFFER[2]], rerolls=3, probabilities=None, rarity=[],
                    team=False, problems=["第 2 个候选筹码无法识别（识别文字：「高效」）"])
        self.assertEqual(self.kinds(self.unreadable(SimpleNamespace(**seen, selected=None))), ["unreadable"])
        self.feed(self.session.on_frame, SimpleNamespace(**seen, selected=0))
        self.assertEqual(self.kinds(self.close()), ["picked"])
        self.assertEqual(self.logbook.offers, [])
        self.assertEqual(self.logbook.picks, [])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.status.summary_plain.startswith(f"已记录选择：{OFFER[0]}"), self.w.status.summary_plain)
        self.w.set_expanded(True)
        advice = self.w.advice.texts()
        self.assertIn(f"{PICKED_LABEL}{OFFER[0]}", advice)
        self.assertFalse(any("（首推）" in t or t.startswith(FEEDBACK_QUESTION.split("「")[0]) for t in advice), advice)


class PickRevertedBranchTest(BehaviorBase):
    def test_reverted_then_other_pick_same_offer_id(self):
        self.enter()
        chip = self.pick_first()
        offer_id = self.logbook.offers[-1][0]
        self.assertEqual(self.logbook.picks, [(offer_id, chip)])
        self.assertEqual(self.kinds(self.offer()), ["pick_reverted", "advice"])  # 同一组候选又出现
        self.assertEqual(self.logbook.picks[-1], (offer_id, None))  # 撤销记在原来那一次上
        self.assertEqual(self.logbook.offers[-1][0], offer_id)  # 这一组候选沿用原来的编号
        self.offer(selected=2)
        self.assertEqual(self.kinds(self.close()), ["picked"])
        self.assertEqual(self.logbook.picks[-1], (offer_id, OFFER[2]))
        self.w.set_expanded(True)
        owned = "\n".join(self.texts("owned"))
        self.assertIn(OFFER[2], owned)
        self.assertNotIn(chip, owned)
        self.assertNotIn("reverted", self.w.notice_model.texts())  # 三选一关了：「推荐」卡片里那一行收起


class MapReadBranchTest(BehaviorBase):
    def test_open_tasks_hint_goes_away(self):
        self.hud(1)
        self.hud(1)  # 进入对局，地图待识别：提示打开任务栏
        self.w.set_expanded(True)
        self.assertIn(OPEN_TASKS, self.texts("recog"))
        out = self.hud(None, map="异变图书馆", map_confidence="high", map_candidates=["异变图书馆"],
                       tasks_seen=["击倒魔法茶壶"])
        self.assertIn("map_read", self.kinds(out))
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertNotIn(OPEN_TASKS, "\n".join(self.w.visible_texts()))
                self.assertIn(MAP_READ.format(map="异变图书馆"), self.w.status.status_text())
                self.assertIn("异变图书馆", self.w.status.status_text())


class GameOverBranchTest(BehaviorBase):
    def game_over(self) -> list:
        out = self.feed(self.session.on_out_of_game, RESULT)
        self.assertIn("game_over", self.kinds(out))
        return out

    def test_logged_once_with_star_and_owned(self):
        self.enter(star=2)
        chip = self.pick_best()
        out = self.game_over()
        self.assertEqual(len(self.logbook.game_overs), 1)
        info = self.logbook.game_overs[0]
        self.assertEqual(info["star"], 2)
        self.assertIn(chip, info["owned"])
        again = next(e for e in out if e.kind == "game_over")
        self.w.handle(again)  # 同一局的对局结束又送到一次：不重复记
        self.assertEqual(len(self.logbook.game_overs), 1)

    def test_gain_prompt_put_away(self):
        """星数增加需补录的提示：对局结束后收起（只对进行中的对局有意义），边框不变色。"""
        self.enter(star=1)
        self.stars(2)
        self.stars(2)
        self.stars(2)  # 1 → 2 星，对局画面上又读到两次，仍没有三选一：请用户补录
        self.assertIn("gain", self.w.notice_model.texts())
        self.assertTrue(self.w.needs_attention())
        self.game_over()
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertNotIn("gain", self.w.notice_model.texts())
                self.assertFalse(self.w.needs_attention())
                self.assertTrue(self.w.status.summary_plain.startswith("对局 已结束"), self.w.status.summary_plain)


class UndoAfterRoomEndTest(BehaviorBase):
    GAIN = GAIN_TEXT.format(why="星数由 2 星增加为 3 星，未检测到对应的筹码三选一界面", n="")

    def check_prompts(self, chip: str) -> None:
        """「查看筹码」提示、待补录的提示、补录行（同一组候选再次出现、撤销了 chip 以后没看到选择）都在。"""
        notices = self.w.notice_model.texts()
        self.assertEqual(notices.get("panel"), PROMPT_PANEL)
        self.assertEqual(notices.get("gain"), self.GAIN)
        self.assertEqual(self.w.gain_pending(), 1)
        self.assertEqual(self.w.owned.missed_buttons_text(), [*OFFER, DISMISS])
        self.w.set_expanded(True)
        self.assertIn(REVERTED_PROMPT.format(chip=chip), self.texts("owned"))
        self.w.set_expanded(False)

    def test_prompts_back_and_real_game_over_logged_again(self):
        self.enter(rnd=8, star=2)  # 中途进入对局：请用户打开「查看筹码」面板
        for _ in range(3):
            self.stars(3)  # 2 → 3 星，对局画面上又读到两次，仍没有三选一：待补录 1 个
        chip = self.pick_first()
        self.assertEqual(self.kinds(self.offer()), ["pick_reverted", "advice"])  # 同一组候选又出现：撤销刚记下的
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])  # 这一次没看到选择
        self.check_prompts(chip)
        self.feed(self.session.on_out_of_game, ROOM)
        self.advance(3)
        self.assertIn("game_over", self.kinds(self.feed(self.session.on_out_of_game, ROOM)))  # 房间画面结束本局
        self.assertEqual(len(self.logbook.game_overs), 1)
        misread = self.logbook.game_overs[0]
        for key in ("panel", "gain"):
            self.assertNotIn(key, self.w.notice_model.texts())
        self.assertEqual(self.w.owned.missed_buttons_text(), [])
        # 同一局接着打：隔一会儿连续两次读到第 8 轮，判为新的一局（按对局画面判出的可以撤销；按选角界面判出的不可撤销）
        self.advance(3)
        self.hud(8)
        self.advance(3)
        self.assertIn("newgame", self.kinds(self.hud(8)))
        self.w.notices.buttons("newgame")[0].click()  # 撤销：误认的房间画面，这一局还在打
        self.assertFalse(self.session.game.game_over)
        self.check_prompts(chip)
        self.feed(self.session.on_out_of_game, RESULT)  # 这一局真正结束
        self.assertTrue(self.session.game.game_over)
        # 误认的那条之后再记一条（以后一条为准）：依据、轮次是真正结束时的
        self.assertEqual(len(self.logbook.game_overs), 2)
        real = self.logbook.game_overs[-1]
        self.assertEqual(real["why"], self.session.game_over_info["why"])
        self.assertNotEqual(real["why"], misread["why"])
        self.assertEqual(real["rounds"], RESULT.rounds)
        self.advance(3)
        self.feed(self.session.on_out_of_game, RESULT)  # 结算画面又读到一次：同一局不重复记
        self.assertEqual(len(self.logbook.game_overs), 2)

    def test_next_game_over_still_logged(self):
        """撤销的是对局中途判定的新一局（之前没有结束过）：之后结束时照常记。"""
        self.enter(rnd=8, star=2)
        self.pick_first()
        self.advance(30)
        self.hud(1)
        self.hud(1)  # 第 8 轮 → 第 1 轮：判定为新的一局
        self.w.notices.buttons("newgame")[0].click()
        self.feed(self.session.on_out_of_game, RESULT)
        self.assertEqual(len(self.logbook.game_overs), 1)


if __name__ == "__main__":
    unittest.main()
