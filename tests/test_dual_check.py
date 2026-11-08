"""同时识别两个客户端的性能测试（advisor/ui/dual_check.py）：五档的每条边界、missing()、正常测完的秒数、
三选一延迟的样本、整机占用和本进程占用的算法、取消、窗口中途关了、出例外时按没测完返回、低配按倍数放慢并复原文字识别的
线程数、对局状态不存盘、识别流程停在主菜单也不空转（FrameProcessor.menu_idle 关掉）。

时钟、等的函数、找窗口、取帧、识别流程、读字和算推荐都换成假的：时间由假睡推着走，不真的截图、不真的识别，整个文件
几秒内跑完。"""
import os
import threading
import unittest
from unittest import mock

import numpy as np

from advisor.ui import dual_check
from advisor.vision import capture


class FakeClock:
    """不会自己走的时钟：只在假睡里往前推，模拟一段几十秒的测试也只要几毫秒。"""

    def __init__(self, start: float = 100.0):
        self.now = start
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds


class FakeDB:
    """资料库只放构造时用到的几项：节奏、角色、候选筹码、现役地图。"""

    rules = {"watch": {"idle_ms": 400, "active_ms": 20, "lowend_factor": 2}}
    heroes = {"凛": object(), "阿": object()}
    chips = {"甲": object(), "乙": object(), "丙": object(), "丁": object()}

    def active_maps(self):
        return ("测试地图",)


def game_window(minimized: bool = False):
    return capture.GameWindow(1, minimized, None if minimized else (0, 0, 100, 100), (0, 0), True, False)


class FakeSource:
    """假的取帧对象：交回一张很小的图，记下关没关。"""

    instances: list["FakeSource"] = []

    def __init__(self, finder=None):
        self.finder = finder
        self.grabs = 0
        self.closed = False
        FakeSource.instances.append(self)

    def grab(self):
        self.grabs += 1
        return np.zeros((8, 8, 3), np.uint8)

    def close(self):
        self.closed = True


class GoneSource(FakeSource):
    """取第三帧时游戏窗口关了（capture.WindowGone）。"""

    def grab(self):
        self.grabs += 1
        if self.grabs >= 3:
            raise capture.WindowGone()
        return np.zeros((8, 8, 3), np.uint8)


class FakeProcessor:
    """假的识别流程：记下跑了多少帧、低配的倍数。"""

    instances: list["FakeProcessor"] = []

    def __init__(self, db, session, lock):
        self.frames = 0
        self.pace = None
        FakeProcessor.instances.append(self)

    def set_pace(self, factor):
        self.pace = factor

    def process(self, image, now):
        self.frames += 1
        return []


class BadProcessor(FakeProcessor):
    """识别一帧就出错。"""

    def process(self, image, now):
        raise RuntimeError("识别坏了")


class FakeSession:
    """假的记账：只记下存不存盘。"""

    paths: list = []

    def __init__(self, db, settings, path=None):
        FakeSession.paths.append(path)


class FakeSampler:
    """假的处理器时间：第一次只记基准，之后每次调用整机总时间 +1 秒、忙的时间按给定比例、本进程按给定倍数各核 +1 秒。
    每次交回来的值都记下来，测试按首尾算本进程的平均占用。"""

    def __init__(self, cores: int, busy_share: float = 0.95, own: float = 0.5):
        self.cores = cores
        self.busy_share = busy_share
        self.own = own
        self.values: list[tuple[int, int, int]] = []
        self.process = 0
        self.busy = 0
        self.total = 0

    def __call__(self):
        if self.values:
            self.total += 10_000_000
            self.busy += int(10_000_000 * self.busy_share)
            self.process += int(10_000_000 * self.cores * self.own)
        sample = (self.process, self.busy, self.total)
        self.values.append(sample)
        return sample


class DualCheckTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sleeps: list[float] = []
        self.on_sleep = None
        self.lowend_calls: list[bool] = []
        self.ocr_calls: list = []
        FakeSource.instances = []
        FakeProcessor.instances = []
        FakeSession.paths = []
        self._patch(dual_check, "sleep", self._sleep)
        self._patch(dual_check, "FrameSource", FakeSource)
        self._patch(dual_check, "FrameProcessor", FakeProcessor)
        self._patch(dual_check, "Session", FakeSession)
        self._patch(dual_check, "limit_cv_threads", lambda: None)
        self._patch(dual_check, "advise", lambda db, state, offer: None)
        self._patch(dual_check.chip_select, "_ocr_lines", self._ocr_lines)
        self._patch(dual_check.chip_select, "use_lowend_threads", self.lowend_calls.append)

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sleep(self, seconds):
        # 记下这次要等多久（低配时看倍数），时钟只往前推一小步：推得太多，工作线程会在计时线程没跑到之前把整段假时间走完
        self.sleeps.append(seconds)
        self.clock.advance(0.01)
        _real_sleep(0.001)  # 交一下 GIL，让两路的工作线程有机会跑
        if self.on_sleep is not None:
            self.on_sleep()

    def _ocr_lines(self, image):
        """读字固定花 0.25 秒（推假时钟，不真的识别）。"""
        self.ocr_calls.append(image)
        self.clock.advance(0.25)

    def check(self, **kwargs):
        kwargs.setdefault("finder", lambda platform: game_window())
        kwargs.setdefault("clock", self.clock)
        return dual_check.DualCheck(FakeDB(), **kwargs)


_real_sleep = dual_check.sleep


class LevelTest(unittest.TestCase):
    """五档的每条边界：两样数各试正好在线上、刚过线；一次都没量到时只看整机忙不忙。"""

    def level(self, slow, busy=0.0):
        return dual_check.level_of(slow, busy)

    def test_easy(self):
        self.assertEqual(self.level(None, 0.0), dual_check.EASY)
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 0.7), dual_check.EASY)  # 正好七成还是余裕
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 0.7), dual_check.EASY)

    def test_normal_by_latency(self):
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 0.7 + 0.01), dual_check.NORMAL)  # 刚过七成
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 0.9), dual_check.NORMAL)  # 正好九成

    def test_danger_by_latency(self):
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 0.9 + 0.01), dual_check.DANGER)
        self.assertEqual(self.level(dual_check.SLOW_LINE_S), dual_check.DANGER)  # 正好到基准值不算超过

    def test_critical_by_latency(self):
        self.assertEqual(self.level(dual_check.SLOW_LINE_S + 0.01), dual_check.CRITICAL)
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 1.1), dual_check.CRITICAL)  # 超过一成还是临界

    def test_overload_by_latency(self):
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 1.1 + 0.01), dual_check.OVERLOAD)

    def test_normal_by_busy(self):
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 0.7 + 0.001), dual_check.NORMAL)
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 0.9), dual_check.NORMAL)

    def test_danger_by_busy(self):
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 0.9 + 0.001), dual_check.DANGER)
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE), dual_check.DANGER)

    def test_critical_by_busy(self):
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE + 0.001), dual_check.CRITICAL)
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 1.1), dual_check.CRITICAL)

    def test_overload_by_busy(self):
        self.assertEqual(self.level(None, dual_check.BUSY_SHARE_LINE * 1.1 + 0.001), dual_check.OVERLOAD)

    def test_takes_larger(self):
        """两样数里取大的那个：一样余裕、另一样到了哪一档就算哪一档。"""
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 0.7, dual_check.BUSY_SHARE_LINE * 1.2), dual_check.OVERLOAD)
        self.assertEqual(self.level(dual_check.SLOW_LINE_S * 1.05, 0.0), dual_check.CRITICAL)

    def test_result_level_uses_slowest_and_busy(self):
        result = dual_check.CheckResult(2.5, 0.2, 0.1, 0.05, 30.0)
        self.assertEqual(result.level, dual_check.OVERLOAD)


class MissingTest(unittest.TestCase):
    def missing(self, finder):
        return dual_check.DualCheck(FakeDB(), finder=finder, clock=lambda: 0.0).missing()

    def test_both_open(self):
        self.assertEqual(self.missing(lambda platform: game_window()), [])

    def test_one_missing(self):
        def finder(platform):
            if platform == capture.TAPTAP:
                raise capture.WindowGone()
            return game_window()

        self.assertEqual(self.missing(finder), [capture.TAPTAP])

    def test_both_missing(self):
        def finder(platform):
            raise capture.WindowGone()

        self.assertEqual(self.missing(finder), [capture.STEAM, capture.TAPTAP])

    def test_minimized(self):
        def finder(platform):
            return game_window(minimized=platform == capture.TAPTAP)

        self.assertEqual(self.missing(finder), [capture.TAPTAP])


class RunTest(DualCheckTest):
    def test_finishes_with_seconds_samples_and_cpu(self):
        cores = os.cpu_count() or 1
        sampler = FakeSampler(cores)
        check = self.check(seconds=2.0, platforms=(capture.STEAM,), sampler=sampler)
        progress = []
        result = check.run(progress=lambda done, total: progress.append((done, total)))

        self.assertFalse(result.stopped)
        self.assertEqual(result.reason, "")
        self.assertAlmostEqual(result.seconds, 2.0)
        # 每秒取的整机时间都在 BUSY_LINE 以上：被压满的比例按整秒数算
        self.assertAlmostEqual(result.busy_share, 1.0)
        # 本进程的平均占用 = 首尾两次处理器时间之差 ÷（秒数 × 核数）
        expected = ((sampler.values[-1][0] - sampler.values[0][0])
                    / (result.seconds * cores * 10_000_000))
        self.assertAlmostEqual(result.own_share, min(1.0, expected), places=6)
        self.assertGreaterEqual(len(sampler.values), 3)  # 基准、至少一次、结束
        self.assertEqual(result.slowest_s, result.median_s)
        self.assertEqual(len(self.ocr_calls), 1)  # 默认 4 秒一个样本，测 2 秒只有开场那一个
        done = [value for value, _ in progress]
        self.assertEqual(progress[0], (0.0, 2.0))
        self.assertEqual(done, sorted(done))
        self.assertEqual(done[-1], 2.0)
        self.assertTrue(all(total == 2.0 for _, total in progress))
        self.assertTrue(FakeSource.instances[-1].closed)
        self.assertTrue(all(path is None for path in FakeSession.paths))  # 对局状态不存盘
        # 停在主菜单也照常认字，不空转：量的是不空转时的负担
        self.assertEqual([p.menu_idle for p in FakeProcessor.instances], [False])

    def test_busy_line_is_strict(self):
        """整机占用正好等于 BUSY_LINE 不算被压满；多一点才算。"""
        cores = os.cpu_count() or 1
        result = self.check(seconds=2.0, platforms=(capture.STEAM,),
                            sampler=FakeSampler(cores, busy_share=0.90)).run()
        self.assertEqual(result.busy_share, 0.0)
        self.clock = FakeClock()
        result = self.check(seconds=2.0, platforms=(capture.STEAM,),
                            sampler=FakeSampler(cores, busy_share=0.91)).run()
        self.assertGreater(result.busy_share, 0.0)

    def test_stop_midway(self):
        stop = threading.Event()
        calls = [0]

        def hook():
            calls[0] += 1
            if calls[0] >= 4:
                stop.set()

        self.on_sleep = hook
        check = self.check(seconds=30.0)
        result = check.run(stop=stop)
        self.assertTrue(result.stopped)
        self.assertEqual(result.reason, "")
        self.assertLess(result.seconds, 30.0)
        self.assertTrue(FakeSource.instances[-1].closed)

    def test_window_closed_midway(self):
        self._patch(dual_check, "FrameSource", GoneSource)
        check = self.check(seconds=30.0, platforms=(capture.STEAM,))
        result = check.run()
        self.assertTrue(result.stopped)
        self.assertEqual(result.reason, "「Steam端」的游戏窗口关了或最小化了，测试停下了。")
        self.assertTrue(FakeSource.instances[-1].closed)

    def test_exception_is_reported_not_raised(self):
        self._patch(dual_check, "FrameProcessor", BadProcessor)
        logged = []
        self._patch(dual_check, "log_error", lambda title, detail, append=True: logged.append(title))
        check = self.check(seconds=30.0)
        result = check.run()
        self.assertTrue(result.stopped)
        self.assertEqual(result.reason, dual_check.ERROR_REASON)
        self.assertEqual(logged, [dual_check.ERROR_TITLE])

    def test_two_platforms_staggered(self):
        """两路各量各的，样本总数比一路多，两个取帧对象都关了。"""
        self._patch(dual_check, "LATENCY_EVERY_S", 0.5)
        check = self.check(seconds=2.0)
        result = check.run()
        self.assertFalse(result.stopped)
        self.assertGreaterEqual(len(self.ocr_calls), 2)
        self.assertGreaterEqual(len(FakeSource.instances), 2)
        self.assertTrue(all(source.closed for source in FakeSource.instances))

    def test_capture_thread_cleanup(self):
        """每一路在自己的线程里关掉取帧对象以后做直接截取的线程收尾（window_capture.uninit_thread，和识别线程一样配对）；
        窗口中途关了也做。"""
        from advisor.vision import window_capture

        class ThreadSource(FakeSource):
            """记下是在哪个线程里建的。"""

            def __init__(self, finder=None):
                super().__init__(finder)
                self.thread = threading.current_thread().name

        def uninit():
            name = threading.current_thread().name
            mine = [source for source in FakeSource.instances if getattr(source, "thread", None) == name]
            seen.append((name, bool(mine) and all(source.closed for source in mine)))

        seen = []
        self._patch(window_capture, "uninit_thread", uninit)
        self._patch(dual_check, "FrameSource", ThreadSource)
        self.assertFalse(self.check(seconds=2.0).run().stopped)
        self.assertEqual(sorted(name for name, _ in seen), ["dual-check-steam", "dual-check-taptap"])
        self.assertTrue(all(closed for _, closed in seen))  # 每个线程的取帧对象先关、再收尾
        seen.clear()
        self._patch(dual_check, "FrameSource", GoneSource)
        self.assertTrue(self.check(seconds=30.0, platforms=(capture.STEAM,)).run().stopped)
        self.assertEqual([name for name, _ in seen], ["dual-check-steam"])


class LowendTest(DualCheckTest):
    def test_pace_threads_and_restore(self):
        check = self.check(seconds=2.0, lowend=True, platforms=(capture.STEAM,))
        result = check.run()
        self.assertTrue(result.lowend)
        self.assertFalse(result.stopped)
        # active_ms 20 毫秒乘 lowend_factor 2：工作线程按 40 毫秒等，平时那一档 20 毫秒不出现
        self.assertIn(0.04, self.sleeps)
        self.assertNotIn(0.02, self.sleeps)
        self.assertEqual(FakeProcessor.instances[-1].pace, 2)
        self.assertEqual(self.lowend_calls, [True, False])  # 用低配线程数，测完换回测之前的样子（关）

    def test_threads_restored_to_before(self):
        """测之前文字识别已经按低配模式的线程数（低配模式本来就开着、识别线程换好了）：测完换回的是开，不一律换成关。"""
        self.addCleanup(setattr, dual_check.chip_select, "_lowend", dual_check.chip_select._lowend)
        dual_check.chip_select._lowend = True
        result = self.check(seconds=2.0, lowend=True, platforms=(capture.STEAM,)).run()
        self.assertFalse(result.stopped)
        self.assertEqual(self.lowend_calls, [True, True])

    def test_normal_pace_leaves_threads_alone(self):
        """不是低配的那一次：不碰文字识别的线程数（开关没开过时不调用，测试和基准工具改的线程数照旧有效）。"""
        result = self.check(seconds=2.0, platforms=(capture.STEAM,)).run()
        self.assertFalse(result.stopped)
        self.assertEqual(self.lowend_calls, [])

    def test_samples_are_spaced_out(self):
        """低配时量三选一延迟的间隔也乘倍数：同样时长里样本更少。"""
        self._patch(dual_check, "LATENCY_EVERY_S", 0.5)
        normal = self.check(seconds=2.0, platforms=(capture.STEAM,)).run()
        normal_count = len(self.ocr_calls)
        self.ocr_calls.clear()
        lowend = self.check(seconds=2.0, lowend=True, platforms=(capture.STEAM,)).run()
        self.assertFalse(normal.stopped)
        self.assertFalse(lowend.stopped)
        self.assertGreater(normal_count, len(self.ocr_calls))


class LatencyTest(unittest.TestCase):
    def test_measures_read_and_advice(self):
        """量的是从开始读字到算完推荐：读字 0.25 秒、推荐 0.05 秒，正好 0.3 秒。"""
        clock = FakeClock()
        with mock.patch.object(dual_check.chip_select, "_ocr_lines", lambda image: clock.advance(0.25)), \
                mock.patch.object(dual_check, "advise", lambda db, state, offer: clock.advance(0.05)):
            check = dual_check.DualCheck(FakeDB(), clock=clock)
            self.assertAlmostEqual(check._latency(None), 0.30)


if __name__ == "__main__":
    unittest.main()
