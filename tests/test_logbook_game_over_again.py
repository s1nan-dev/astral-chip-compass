"""推荐日志里同一局有两条对局结束：房间等画面结束的局，之后开的新一局被撤销（那次结束是误认的），这一局真正结束时悬浮窗
再记一条（同一局以后一条为准，推荐日志的格式不变）。读推荐日志的几处都要按后一条算、不把一局算成两局：

- 重启时接局号（logbook.resumable、接上日志里的最后一局）：真正结束之前接回这一局，记了真正的结束以后不再接；
- 局数（logbook.stats、game_count_since_calibration，校准提示按它）：按三选一记录的局号数，对局结束不计入，两条也只是
  一局；
- 诊断包取的推荐日志（logbook.records_between）：照原样两条都在，后一条是真正结束的。

照悬浮窗调用推荐日志的先后：误认结束时 record_game_over，判定新一局时 new_game，撤销时 undo_new_game（接回原来的局号），
真正结束时再 record_game_over。推荐日志写进沙箱里的临时文件夹，时钟用假的。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from advisor import logbook, paths
from advisor.pool import State
from tests.support import sandbox


class GameOverAgainTest(unittest.TestCase):
    def setUp(self):
        self.logs = sandbox.temp_dir(self, "logbook-")
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)
        self.clock = datetime(2026, 10, 10, 20, 0, 0)
        now = mock.patch.object(logbook, "_now", side_effect=lambda: self.clock)
        now.start()
        self.addCleanup(now.stop)

    def later(self, minutes: float = 1) -> None:
        self.clock += timedelta(minutes=minutes)

    def offer(self, rnd: int, offer_id: str) -> str:
        self.later()
        return logbook.record_offer(State(hero="芬妮", round=rnd, owned=("大背包",)), {"best": "大背包"}, offer_id)

    def game_overs(self, game: str) -> list[dict]:
        return [r for r in logbook.iter_records() if r.get("t") == "gameover" and r.get("game") == game]

    def play_undone_end(self) -> str:
        """一局打到第 8 轮，误认房间画面结束、开了新一局又撤销，接着打到第 9 轮。返回这一局的局号。"""
        game = logbook.new_game()
        self.offer(8, "o1")
        self.later()
        self.assertEqual(logbook.record_game_over(why="检测到组队房间", rounds=8), game)  # 误认的结束
        self.later()
        self.assertNotEqual(logbook.new_game(), game)  # 判定新的一局
        self.later()
        logbook.undo_new_game(game)  # 撤销：接回原来的局号
        self.assertEqual(self.offer(9, "o2"), game)
        return game

    def test_resume_follows_later_record(self):
        game = self.play_undone_end()
        logbook._reset_for_tests()  # 真正结束之前重启：接回这一局
        self.assertEqual(logbook.resumable(), game)
        self.assertEqual(self.offer(9, "o3"), game)
        self.later()
        self.assertEqual(logbook.record_game_over(why="检测到结算画面", rounds=10), game)  # 真正结束
        logbook._reset_for_tests()  # 之后重启：最后一条是真正的结束，不再接这一局
        self.assertIsNone(logbook.resumable())
        self.assertNotEqual(self.offer(1, "o4"), game)

    def test_counts_one_game(self):
        game = self.play_undone_end()
        self.later()
        logbook.record_game_over(why="检测到结算画面", rounds=10)
        overs = self.game_overs(game)
        self.assertEqual([r["rounds"] for r in overs], [8, 10])  # 两条都在，后一条是真正结束的
        self.assertEqual(logbook.stats()["games"], 1)
        self.assertEqual(logbook.game_count_since_calibration(), 1)
        got = logbook.records_between(datetime(2026, 10, 10, 20, 0, 0))
        self.assertEqual([r.get("rounds") for r in got if r["t"] == "gameover"], [8, 10])


if __name__ == "__main__":
    unittest.main()
