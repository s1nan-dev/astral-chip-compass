"""直接截取的实机测试（截取、端到端、4K 性能合成一次跑完）：设了环境变量 CHIP_ADVISOR_LIVE_CAPTURE=1 才跑，默认跳过。

在子进程里执行 tests/live_capture_run.py（它再开测试窗口进程 tests/live_window_host.py），子进程崩溃或超时算失败。
测试窗口全程在所有屏幕的范围以外（x = -20000），不进任务栏、不抢焦点、不出声，屏幕上不会出现任何窗口；
不截屏幕，不碰其他程序的窗口。用到「请求不显示黄框」时 Windows 会在系统的隐私记录里记一条 python.exe 的使用记录
（系统自己记的，和截图隐私设置里「最近使用」的列表是同一处）。约 1.5～3 分钟，其中 4K 性能约 1 分钟。

怎么跑（在项目根目录，PowerShell；只跑这一个文件）：
  $env:CHIP_ADVISOR_LIVE_CAPTURE = "1"
  .venv\\Scripts\\python -m unittest tests.test_window_capture_live -v
test_screen_capture.py 里截取屏幕的实机测试会在屏幕上显示小窗，它只认 CHIP_ADVISOR_LIVE_CAPTURE=screen，不会跟着跑。

通过标准：
  截取：每一步四色块吻合 ≥ 99%（容差 24）：不被挡、被同进程的窗口完全盖住、移动、改大小、改回、画面静止、还原、重开；
        改大小那一步最多丢 1 帧；最小化时取帧对象报「已最小化」、会话直接取帧没有新画面，还原后不重建会话；
        关闭后报找不到窗口、会话关掉，重开（新句柄）后重建会话；Windows 11 上已请求不显示黄框且读回为「不需要」、
        已请求不截鼠标；对句柄 0 建截取对象得到 CaptureError；
  全程：测试窗口的矩形从不和屏幕相交，前台窗口从来不是测试窗口；
  端到端：3 张三选一截图的候选筹码和标注一致，换成三选一后 2 次识别以内给出推荐，截到的画面和原图逐像素一致（≥ 99%）；
  性能（4K）：每秒 5 次取帧时 95% 的取帧 ≤ 15 毫秒、CPU ≤ 单核 6%（按 CPU 周期数算，即 cpu_percent_cycles）；
        建会话和取帧共增加内存 ≤ 120 MB，空闲关闭后释放 ≥ 40 MB；
        连续 300 帧句柄数变化 ≤ 5、前 50 帧之后私有内存增长 ≤ 10 MB。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().with_name("live_capture_run.py")
LIVE_ENV = "CHIP_ADVISOR_LIVE_CAPTURE"
TIMEOUT_S = 900


@unittest.skipUnless(sys.platform == "win32" and os.environ.get(LIVE_ENV) == "1",
                     f"实机测试（测试窗口都在屏幕范围以外）：设环境变量 {LIVE_ENV}=1 时运行")
class LiveWindowCaptureTest(unittest.TestCase):
    result: dict = {}
    proc: subprocess.CompletedProcess | None = None

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cls.proc = subprocess.run([sys.executable, str(RUNNER), "capture", "e2e", "perf"], cwd=str(ROOT), env=env,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        line = next((s for s in cls.proc.stdout.splitlines() if s.startswith("RESULT ")), None)
        cls.result = json.loads(line[len("RESULT "):]) if line else {}

    def part(self, name: str) -> dict:
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-3000:])
        self.assertIn(name, self.result, self.proc.stderr[-3000:])
        self.assertNotIn("error", self.result[name], self.result[name].get("error"))
        return self.result[name]["run"]

    def test_runner_finished(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-3000:])
        self.assertTrue(self.result, "子进程没有给出结果")

    def test_never_on_screen_or_foreground(self):
        self.test_runner_finished()
        self.assertGreater(self.result["checks"], 20)
        self.assertEqual(self.result["on_screen"], [])
        self.assertEqual(self.result["foreground"], [])

    def test_capture_steps(self):
        out = self.part("capture")
        window, borderless, build = out["support"]
        self.assertTrue(window, f"本机（版本号 {build}）不支持直接截取")
        for step in out["steps"]:
            with self.subTest(step=step["step"]):
                self.assertTrue(step["verify"]["ok"], step["verify"])
                self.assertEqual(step["method"], "window")
                self.assertLessEqual(step["no_frames"], 1)  # 改大小那一步最多丢 1 帧
        covered = next(s for s in out["steps"] if s["step"] == "covered")
        self.assertTrue(covered["cover_above"])  # 确实被盖住了
        self.assertTrue(out["static"]["same"])
        self.assertTrue(out["static"]["verify"]["ok"])
        minimized = out["minimized"]
        self.assertTrue(minimized["iconic"])
        self.assertEqual(minimized["frame_source"], "RuntimeError")
        self.assertIn("最小化", minimized["message"])
        self.assertTrue(minimized["session_open"])
        self.assertTrue(minimized["backend_none"][-1])  # 最小化时没有新画面
        self.assertFalse(out["restored_rebuilt"])
        self.assertEqual((out["closed"]["result"], out["closed"]["session_open"]), ("RuntimeError", False))
        self.assertIn("未找到游戏窗口", out["closed"]["message"])
        self.assertTrue(out["reopened_new_handle"])
        self.assertTrue(out["reopened_rebuilt"])
        self.assertEqual(out["notes"].get("cursor"), "off")
        if borderless:
            self.assertTrue(out["border_requested"])
            self.assertIs(out["notes"].get("border_required"), False)
        self.assertEqual(out["hwnd0"][2], "CreateForWindow")
        self.assertEqual(out["state"][0], "window")

    def test_end_to_end(self):
        out = self.part("e2e")
        self.assertEqual(len(out["samples"]), 3)
        for rec in out["samples"]:
            with self.subTest(file=rec["file"]):
                self.assertIsNotNone(rec["cycles"], "没有给出推荐")
                self.assertLessEqual(rec["cycles"], 2)
                self.assertEqual(rec["names"], rec["offer"])
                self.assertTrue(rec["same_size"])
                self.assertGreaterEqual(rec["pixels_match"], 0.99)
        self.assertEqual(out["state"][0], "window")

    def test_performance_4k(self):
        out = self.part("perf")
        self.assertEqual(out["client"], out["size"])
        five = out["five_per_second"]
        self.assertGreaterEqual(five["grabs"], 45)
        self.assertLessEqual(five["ms_p95"], 15)
        # CPU 按周期数算（QueryProcessCycleTime）：time.process_time() 按时钟中断采样，多线程的短活会被少算或多算
        cycles = five.get("cpu_percent_cycles")
        cpu_msg = f"cpu_percent_cycles={cycles}，cpu_percent（process_time）={five.get('cpu_percent')}"
        self.assertIsNotNone(cycles, cpu_msg)
        self.assertLessEqual(cycles, 6, cpu_msg)
        self.assertLessEqual(out["after_open"]["private_mb"] - out["before"]["private_mb"], 120)
        self.assertTrue(out["idle_closed"])
        self.assertGreaterEqual(out["before_idle_close"]["private_mb"] - out["after_idle_close"]["private_mb"], 40)
        self.assertLessEqual(abs(out["after_300"]["handles"] - out["after_50"]["handles"]), 5)
        self.assertLessEqual(out["after_300"]["private_mb"] - out["after_50"]["private_mb"], 10)


if __name__ == "__main__":
    unittest.main()
