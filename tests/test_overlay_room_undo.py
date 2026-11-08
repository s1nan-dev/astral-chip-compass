"""悬浮窗的行为级测试：房间画面结束的局，之后按对局画面判出的新一局能撤销。

搭法和 tests/test_overlay_behavior.py 相同（真实的 OverlaySession 产生事件，按识别流程的顺序交给悬浮窗，只用公开探针断言）。
对局状态还在时，组队房间要隔一会儿连续读到两次才结束本局；之后按轮次开始的新一局和对局中途判出的新一局一样带「撤销」，
按「需要您操作」显示，撤销后恢复上一局的已有筹码，「对局 已结束」也一起撤回。从选角界面开始的不可撤销（选角界面只在开局时
出现，确实开了新的一局）：和结算画面结束的局之后一样只作一次性的说明（test_overlay_behavior 的
test_newgame_after_result_is_info_only）。
"""
import unittest
from types import SimpleNamespace

from advisor.ui.overlay_parts.wording import (GAME_OVER_WAITING, NEWGAME_MIDGAME, NEWGAME_OWNED, NEWGAME_TEXT,
                                              PROMPT_PANEL)
from tests.test_overlay_behavior import RESULT, WAITING, BehaviorBase

ROOM = SimpleNamespace(kind="team_room", difficulty="噩梦", map=None, rounds=None)
MENU = SimpleNamespace(kind="main_menu", difficulty=None, map=None, rounds=None)


class NewGameAfterRoomTest(BehaviorBase):
    def end_by_room(self) -> str:
        """第 8 轮、2 星，记下一个筹码；组队房间隔约 3 秒读到两次，本局结束。返回记下的筹码。"""
        self.enter(rnd=8, star=2)
        chip = self.pick_first()
        self.assertNotIn("game_over", self.kinds(self.feed(self.session.on_out_of_game, ROOM)))
        self.assertNotIn("对局 已结束", self.w.status.status_text())  # 只读到一次：还在这一局
        self.advance(3)
        self.assertIn("game_over", self.kinds(self.feed(self.session.on_out_of_game, ROOM)))
        self.assertIn("对局 已结束", self.w.status.status_text())
        return chip

    def undo(self, chip: str) -> None:
        self.w.notices.buttons("newgame")[0].click()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.assertFalse(self.session.game.game_over)
        self.assertNotIn("对局 已结束", self.w.status.status_text())  # 「已结束」一起撤回
        self.w.set_expanded(True)
        self.assertIn(chip, self.texts("owned"))  # 恢复上一局的已有筹码

    def test_select_after_room_not_undoable(self):
        """选角界面开的新一局不可撤销：没有「撤销」，只作一次性的说明（同结算画面结束的局之后）。"""
        self.end_by_room()
        self.assertEqual(self.kinds(self.feed(self.session.on_select_screen)), ["newgame"])
        self.assertFalse(self.session.can_undo_new_game)
        expected = NEWGAME_TEXT.format(why="上一局已结束，检测到选角界面")
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertNotIn("newgame", self.w.notice_model.texts())  # 不是带「撤销」的提示行
                self.assertEqual(self.w.notices.buttons("newgame"), [])
                self.assertIn(expected, self.w.status.status_text())
                self.assertFalse(self.w.needs_attention())  # 不可撤销：没有可做的操作
        self.w.set_expanded(True)
        self.assertIn(expected, self.texts("recog"))

    def test_round_after_room_undoable(self):
        """误认出的房间结束了本局，同一局接着打：按轮次开的新一局按「需要您操作」显示，撤销后接着记这一局。"""
        chip = self.end_by_room()
        self.advance(3)
        out = self.hud(8)
        self.advance(3)
        out += self.hud(8)
        self.assertIn("newgame", self.kinds(out))
        midgame = NEWGAME_MIDGAME.format(why="上一局已结束，检测到第 8 轮的对局画面")
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.needs_attention())
                texts = self.w.notice_model.texts()
                self.assertEqual(texts.get("newgame"), midgame)
                self.assertEqual(texts.get("newgame_owned"), NEWGAME_OWNED)
                self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        self.undo(chip)
        self.assertIn("第 8 轮", self.w.status.status_text())


class RejoinAfterMenuTest(BehaviorBase):
    """主菜单结束的局（中途退出、断线），之后又连回了同一局（连续两次读到不比离开时小的轮次）：不开新的一局，悬浮窗回到结束
    之前的样子，口径同撤销房间等画面结束以后开的新一局：「对局 已结束」和等下一局开始的那句收起，已有筹码照旧，「查看筹码」
    面板的提示按结束前放回；不另加提示。推荐日志不换局，这一局真正结束时再记一条结束（以后一条为准）。"""

    def test_rejoin_restores_view(self):
        self.enter(rnd=6, star=2)
        chip = self.pick_first()
        self.assertEqual(self.w.notice_model.texts().get("panel"), PROMPT_PANEL)  # 进对局时提示过，还没读到面板
        self.feed(self.session.on_out_of_game, MENU)
        self.advance(3)
        self.assertIn("game_over", self.kinds(self.feed(self.session.on_out_of_game, MENU)))
        self.assertIn(GAME_OVER_WAITING, self.w.status.status_text())
        self.assertEqual([g["why"] for g in self.logbook.game_overs], ["检测到主菜单"])
        self.advance(16)
        out = self.hud(7)
        self.advance(3)
        out += self.hud(7)
        self.assertNotIn("newgame", self.kinds(out))
        for mode in self.modes():
            with self.subTest(mode=mode):
                status = self.w.status.status_text()
                self.assertNotIn("对局 已结束", status)
                self.assertNotIn(GAME_OVER_WAITING, status)
                self.assertIn(WAITING, status)
                self.assertIn("第 7 轮", status)
                texts = self.w.notice_model.texts()
                self.assertNotIn("newgame", texts)  # 不另加提示
                self.assertEqual(texts.get("panel"), PROMPT_PANEL)
        self.w.set_expanded(True)
        self.assertIn(chip, self.texts("owned"))
        self.assertEqual(self.logbook.games_log, [])  # 推荐日志没有换局
        self.advance(3)
        self.feed(self.session.on_out_of_game, RESULT)  # 这一局真正打完了
        self.assertEqual([g["why"] for g in self.logbook.game_overs],
                         ["检测到主菜单", "检测到结算画面（异变图书馆、噩梦、10 轮）"])


if __name__ == "__main__":
    unittest.main()
