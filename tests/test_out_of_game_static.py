"""离开对局后的整幅识别（FrameProcessor._check_loading）和「连续两次才结束本局」配合：

  创建房间界面、自建房间、「匹配成功」画面、奖励画面（和 16:9 以外窗口里的组队房间）只有整幅识别认得出。本局还没结束时
  认出这几种画面，画面停着不动也按 loading_ms / mutators_ms 的间隔再认一次，Session 第二次读到就结束本局；两次之间隔的
  时间落在 OUT_CONFIRM_GAP_S 和 OUT_CONFIRM_S 之间。本局结束以后、结算画面、或认不出对局外画面时，静止画面照旧只认一次。

识别函数全部换成假的，只看 FrameProcessor 什么时候做整幅识别、读到的交给了谁。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor import session as session_mod
from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.vision import pipeline
from advisor.vision.hud import Hud, Pregame, Progress
from advisor.vision.screens import CREATE_ROOM, CUSTOM_ROOM, MATCH_FOUND, RESULT, REWARD, TEAM_ROOM, OutOfGame

DB = load_db()
MAP = "星趴·梦想号"


class StaticOutOfGameTest(unittest.TestCase):
    def setUp(self):
        self.round = None
        self.stars = None  # 自己的星数（只在对局画面上读得到）
        self.full = None  # 整幅识别读到的对局外画面（OutOfGame），None 为认不出
        self.pregame_at: list[float] = []

        def pregame(img, db, width=1280, catalog=None):
            self.pregame_at.append(self.clock[0])
            return Pregame(screen=self.full)

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full, **kw: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, who, **kw: self.stars),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "read_pregame", side_effect=pregame),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.still = np.full((1080, 1920, 3), 200, np.uint8)  # 停着不动的对局外画面

    def fresh(self, game_map=None):
        """新的 Session（game_map：本局地图已知）和 FrameProcessor，在对局里读到几次轮次；返回最后一次读到轮次的时刻。"""
        self.pregame_at.clear()
        self.session = Session(DB, Settings(hero="凛", map=game_map))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.round, self.stars = 12, SimpleNamespace(stars=2, confidence=0.9)
        for n, t in enumerate((0.0, 4.0, 8.0)):
            self.step(t, np.full((1080, 1920, 3), 40 + 20 * n, np.uint8))
        self.round, self.stars = None, None
        return 8.0

    def step(self, t: float, img=None) -> list:
        """t 秒处的一帧，到期的读数都做完（settle：悬浮窗一帧只读一项，整幅识别排在读轮次那一轮后面，紧接着几帧做完）。"""
        self.clock[0] = t
        return self.proc.settle(self.still if img is None else img, t)

    def watch_still(self, until: float) -> float | None:
        """从 9 秒起每秒交一帧静止画面，到 until 为止。返回结束本局的时刻（没结束为 None）。"""
        ended = None
        t = 9.0
        while t <= until:
            events = self.step(t)
            if ended is None and any(e.kind == "game_over" for e in events):
                ended = t
            t += 1.0
        return ended

    def test_still_screen_ends_game(self):
        """只有整幅识别认得出的画面停着不动：第一次认出以后隔 loading_ms（地图未知）或 mutators_ms（地图已知）再认一次，
        第二次读到就结束本局，前后不超过 OUT_CONFIRM_S；结束以后同样的画面不再重复整幅识别。"""
        for game_map, interval in ((None, self.proc_interval("loading_ms")), (MAP, self.proc_interval("mutators_ms"))):
            for kind in (REWARD, CREATE_ROOM, CUSTOM_ROOM, MATCH_FOUND, TEAM_ROOM):
                with self.subTest(kind=kind, game_map=game_map):
                    self.full = OutOfGame(kind)
                    last_round = self.fresh(game_map)
                    ended = self.watch_still(100.0)
                    first, second = self.pregame_at[:2]
                    self.assertGreaterEqual(first, last_round + self.proc.after_game_s)
                    self.assertEqual(second - first, interval)
                    self.assertTrue(session_mod.OUT_CONFIRM_GAP_S <= second - first <= session_mod.OUT_CONFIRM_S)
                    self.assertEqual(ended, second)
                    self.assertTrue(self.session.game.game_over)
                    self.assertEqual(self.pregame_at, [first, second])  # 结束以后静止画面不再认

    def test_result_screen_read_once(self):
        """结算画面读到一次就结束本局：静止画面照旧只认一次。"""
        self.full = OutOfGame(RESULT, "困难", MAP, 12)
        self.fresh()
        ended = self.watch_still(100.0)
        self.assertIsNotNone(ended)
        self.assertEqual(self.pregame_at, [ended])

    def test_unrecognized_still_screen_read_once(self):
        """认不出对局外画面的静止画面（战斗、加载画面等）：照旧只认一次，不因为这一项多做整幅识别。"""
        self.full = None
        self.fresh()
        self.assertIsNone(self.watch_still(100.0))
        self.assertEqual(len(self.pregame_at), 1)
        self.assertFalse(self.session.game.game_over)

    def test_no_game_state_read_once(self):
        """没有对局状态时（启动时已在房间里等）认出房间：不用确认，静止画面照旧只认一次。"""
        self.full = OutOfGame(CREATE_ROOM, "困难", MAP)
        self.session = Session(DB, Settings(hero="凛"))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.assertFalse(self.session.has_game_state)
        for t in range(0, 60):
            self.step(float(t))
        self.assertEqual(len(self.pregame_at), 1)

    def proc_interval(self, key: str) -> float:
        """[map_detect] 里的间隔（秒）。"""
        return DB.rules.get("map_detect", {}).get(key, {"loading_ms": 2000, "mutators_ms": 6000}[key]) / 1000


if __name__ == "__main__":
    unittest.main()
