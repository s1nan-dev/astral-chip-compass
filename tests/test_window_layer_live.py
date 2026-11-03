"""悬浮窗窗口层次的实机测试：设了环境变量 CHIP_ADVISOR_LIVE_CAPTURE=1 才跑（和直接截取的实机测试共用这个开关），默认跳过。

在子进程里执行 tests/live_layer_run.py：真的悬浮窗（Qt 的 windows 平台，显示时不激活、不透明度为 0）和几个测试窗口
（tests/live_window_host.py 在另外的进程里显示，当作游戏窗口和别的程序的窗口）都放在所有屏幕的范围以外（x 约 -19000），
不进任务栏、不抢焦点、不出声，不截屏幕；悬浮窗照正式的做法判断前台状态和盖住的情形、调整自己的窗口层次，每一步之后
按系统里的窗口层次核对。子进程崩溃、超时、没有给出结果都算失败。约 15～40 秒（主要是开 5 个测试窗口进程）。

怎么跑（在项目根目录，PowerShell；和直接截取的实机测试一条命令跑完）：
  $env:CHIP_ADVISOR_LIVE_CAPTURE = "1"
  .venv\\Scripts\\python.exe -m unittest tests.test_window_capture_live tests.test_window_layer_live -v
子进程的 QT_QPA_PLATFORM 由本测试设为 windows（真窗口；外面设的 offscreen 不影响它）。

通过标准：
  只相交 7 像素不算盖住；相交 8 像素第一次不退，连续第二次退到盖住它的窗口后面，层次为「盖住的窗口 → 悬浮窗 → 游戏」、
  悬浮窗不再置顶；三选一出现时回到最上层（置顶并排在最前），关闭后再退回；不再盖住时马上恢复置顶；
  鼠标可穿透的透明覆盖层、隐藏的窗口、最小化的窗口不算盖住；
  「总在最前」的窗口盖住时排在它正下方、仍置顶，三选一时盖过它，关闭后排回它正下方，它关掉后照常置顶；
  游戏窗口最小化时暂停识别、不再置顶，还原后恢复；改为截取屏幕时切出就暂停、不再置顶，恢复直接截取后照常置顶；
  全程：悬浮窗和测试窗口的矩形从不和屏幕相交，前台窗口从来不是本测试的窗口。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().with_name("live_layer_run.py")
LIVE_ENV = "CHIP_ADVISOR_LIVE_CAPTURE"
TIMEOUT_S = 300


@unittest.skipUnless(sys.platform == "win32" and os.environ.get(LIVE_ENV) == "1",
                     f"实机测试（悬浮窗和测试窗口都在屏幕范围以外）：设环境变量 {LIVE_ENV}=1 时运行")
class LiveWindowLayerTest(unittest.TestCase):
    result: dict = {}
    proc: subprocess.CompletedProcess | None = None

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="windows")
        cls.proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(ROOT), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        line = next((s for s in cls.proc.stdout.splitlines() if s.startswith("RESULT ")), None)
        cls.result = json.loads(line[len("RESULT "):]) if line else {}
        cls.steps = {s["step"]: s for s in cls.result.get("steps", [])}

    def step(self, name: str) -> dict:
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-3000:])
        self.assertNotIn("error", self.result, self.result.get("error"))
        self.assertIn(name, self.steps, self.proc.stderr[-3000:])
        return self.steps[name]

    def above(self, step: dict, upper: str, lower: str) -> None:
        """upper 在 lower 上面（层次里更靠前）。"""
        order = step["order"]
        self.assertLess(order.index(upper), order.index(lower), (step["step"], order))

    def test_runner_finished(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-3000:])
        self.assertTrue(self.result, "子进程没有给出结果")
        self.assertNotIn("error", self.result, self.result.get("error"))
        self.assertEqual(self.result.get("platform"), "windows")

    def test_never_on_screen_or_foreground(self):
        self.test_runner_finished()
        self.assertGreater(self.result["checks"], 20)
        self.assertEqual(self.result["on_screen"], [])
        self.assertEqual(self.result["foreground"], [])

    def test_start_topmost(self):
        s = self.step("start")
        self.assertEqual((s["state"], s["layer"], s["covered"], s["paused"]), ("background", "top", False, False))
        self.assertEqual(s["capsule"], "running")
        self.assertTrue(s["topmost"])
        self.above(s, "overlay", "game")

    def test_cover_threshold(self):
        """相交 7 像素不算；8 像素第一次不退，第二次退到它后面：「B → 悬浮窗 → 游戏」，悬浮窗不再置顶。"""
        s = self.step("cover_7px")
        self.assertEqual(s["overlap"][0], 7)
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))
        s = self.step("cover_8px_once")
        self.assertEqual(s["overlap"][0], 8)
        self.assertGreaterEqual(s["overlap"][1], 8)
        self.assertEqual((s["covered"], s["cover_below"], s["layer"], s["topmost"]), (True, "B", "top", True))
        for name in ("cover_8px_twice", "cover_8px_again"):
            with self.subTest(step=name):
                s = self.step(name)
                self.assertEqual((s["layer"], s["state"], s["prev"], s["topmost"]), ("below", "covered", "B", False))
                self.assertEqual(s["capsule"], "running")
                self.above(s, "B", "overlay")
                self.above(s, "overlay", "game")

    def test_front_during_chip_select(self):
        s = self.step("live_front")
        self.assertTrue(s["live"])
        self.assertEqual((s["layer"], s["topmost"]), ("front", True))
        self.above(s, "overlay", "B")
        s = self.step("live_closed")
        self.assertFalse(s["live"])
        self.assertEqual((s["layer"], s["prev"], s["topmost"]), ("below", "B", False))
        self.above(s, "overlay", "game")

    def test_uncovered_restores_topmost(self):
        s = self.step("uncovered")
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))
        self.above(s, "overlay", "B")

    def test_skips_transparent_hidden_minimized(self):
        s = self.step("transparent")
        self.assertTrue(s["transparent"])
        self.assertGreaterEqual(min(s["overlap"]), 8)
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))
        s = self.step("hidden_visible")  # 先确认它显示着时确实算盖住
        self.assertEqual((s["covered"], s["layer"], s["prev"]), (True, "below", "H"))
        s = self.step("hidden_and_minimized")
        self.assertTrue(s["minimized"])
        self.assertFalse(s["hidden_visible"])
        self.assertGreaterEqual(min(s["hidden_overlap"]), 8)
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))
        s = self.step("hidden_gone")
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))

    def test_topmost_cover(self):
        """「总在最前」的窗口 C 盖住：排在它正下方、仍置顶；三选一时盖过它；关闭后排回它正下方；C 关掉后照常置顶。"""
        s = self.step("topmost_shown")
        self.assertTrue(s["c_topmost"])
        self.above(s, "C", "overlay")
        s = self.step("topmost_covered")
        self.assertEqual((s["covered"], s["cover_below"], s["layer"], s["prev"], s["topmost"]),
                         (True, "C", "below", "C", True))
        self.above(s, "overlay", "game")
        s = self.step("topmost_live")
        self.assertEqual((s["layer"], s["topmost"]), ("front", True))
        self.above(s, "overlay", "C")
        s = self.step("topmost_closed")
        self.assertEqual((s["layer"], s["prev"], s["topmost"]), ("below", "C", True))
        s = self.step("topmost_gone")
        self.assertEqual((s["covered"], s["layer"], s["topmost"]), (False, "top", True))

    def test_minimized_game(self):
        s = self.step("game_minimized")
        self.assertEqual((s["state"], s["layer"], s["topmost"], s["paused"], s["capsule"]),
                         ("minimized", "none", False, True, "paused"))
        s = self.step("game_restored")
        self.assertEqual((s["state"], s["layer"], s["topmost"], s["paused"]), ("background", "top", True, False))

    def test_screen_capture_like_0_12_0(self):
        s = self.step("screen_capture")
        self.assertEqual((s["layer"], s["topmost"], s["paused"], s["capsule"]), ("none", False, True, "paused"))
        s = self.step("window_again")
        self.assertEqual((s["layer"], s["topmost"], s["paused"], s["capsule"]), ("top", True, False, "running"))


if __name__ == "__main__":
    unittest.main()
