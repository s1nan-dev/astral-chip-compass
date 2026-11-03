"""识别各步耗时和处理器占用的统计（advisor/perfstats.py）：分位数、总和、分组、取走清零、样本上限、
第一次没有处理器占用、Win32 调用失败时不抛、多线程同时记不丢；不含电脑睡眠时间的时钟（unbiased_clock）。

时钟和 Win32 调用都换掉（perfstats._clock、perfstats._kernel32），不依赖真实的处理器负载。"""
import ctypes
import os
import threading
import time
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor import perfstats
from advisor.vision import chip_select


def _write(target, ticks: int) -> None:
    """把一个 ctypes.byref(FILETIME) 指向的时间写成 ticks（100 纳秒）。"""
    obj = target._obj
    obj.dwLowDateTime = ticks & 0xFFFFFFFF
    obj.dwHighDateTime = (ticks >> 32) & 0xFFFFFFFF


class FakeKernel:
    """假的 kernel32：Get*Times 返回预先设定的累计时间（100 纳秒），按 handle 区分主线程和当前线程。"""

    UI_HANDLE = 3
    THREAD_HANDLE = 2

    def __init__(self):
        self.values = {"process": 0, "recog": 0, "ui": 0, "system": (0, 0)}
        self.fail: set[str] = set()

    def __getattr__(self, name):
        if name in ("GetProcessTimes", "GetThreadTimes", "GetSystemTimes"):
            raise AssertionError(name)
        return lambda *a, **k: 0

    def GetCurrentProcess(self):
        return 1

    def GetCurrentThread(self):
        return self.THREAD_HANDLE

    def OpenThread(self, *args):
        return self.UI_HANDLE

    def GetProcessTimes(self, handle, creation, exit_time, kernel, user):
        if "process" in self.fail:
            return 0
        _write(kernel, self.values["process"])
        _write(user, 0)
        return 1

    def GetThreadTimes(self, handle, creation, exit_time, kernel, user):
        key = "ui" if handle == self.UI_HANDLE else "recog"
        if key in self.fail:
            return 0
        _write(kernel, self.values[key])
        _write(user, 0)
        return 1

    def GetSystemTimes(self, idle, kernel, user):
        if "system" in self.fail:
            return 0
        idle_ticks, total = self.values["system"]
        _write(idle, idle_ticks)
        _write(kernel, total)
        _write(user, 0)
        return 1


class PerfStatsTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.kernel = FakeKernel()
        self._saved = (perfstats._clock, perfstats._kernel32, perfstats._counts, perfstats._since,
                       perfstats._cpu_base, perfstats._ui_handle, perfstats._ui_handle_tried, perfstats.MAX_SAMPLES)
        perfstats._clock = lambda: self.now[0]
        perfstats._kernel32 = self.kernel
        perfstats._counts = {}
        perfstats._since = None
        perfstats._cpu_base = None
        perfstats._ui_handle = None
        perfstats._ui_handle_tried = False
        self.addCleanup(self._restore)

    def _restore(self):
        (perfstats._clock, perfstats._kernel32, perfstats._counts, perfstats._since, perfstats._cpu_base,
         perfstats._ui_handle, perfstats._ui_handle_tried, perfstats.MAX_SAMPLES) = self._saved

    def test_quantiles_max_sum(self):
        """分位数（最近秩法）、最大值、总和：p50 取第 ceil(0.5n) 个，p95 取第 ceil(0.95n) 个。"""
        for ms in (10.0, 20.0, 30.0, 40.0):
            perfstats.record("frame", ms)
        got = perfstats.take_period()["frame"]
        self.assertEqual(got["n"], 4)
        self.assertEqual(got["p50"], 20.0)  # ceil(0.5×4)=2 → 第 2 个
        self.assertEqual(got["p95"], 40.0)  # ceil(0.95×4)=4 → 第 4 个
        self.assertEqual(got["max"], 40.0)
        self.assertEqual(got["sum"], 100.0)

    def test_odd_quantile(self):
        for ms in (5.0, 1.0, 3.0):
            perfstats.record("t", ms)
        got = perfstats.take_period()["t"]
        self.assertEqual((got["p50"], got["p95"], got["max"], got["sum"]), (3.0, 5.0, 5.0, 9.0))

    def test_reads_grouped(self):
        """带冒号的 kind 归到 reads 下面（名字取冒号后面的那一截）。"""
        perfstats.record("read:hud", 3.0)
        perfstats.record("read:select", 250.0)
        perfstats.record("read:hud", 5.0)
        got = perfstats.take_period()
        self.assertNotIn("read:hud", got)
        self.assertEqual(got["reads"]["hud"]["n"], 2)
        self.assertEqual(got["reads"]["select"]["sum"], 250.0)

    def test_missing_kinds_absent(self):
        perfstats.record("a", 1.0)
        got = perfstats.take_period()
        self.assertIn("a", got)
        self.assertNotIn("b", got)
        self.assertNotIn("reads", got)

    def test_take_clears(self):
        perfstats.record("frame", 12.0)
        perfstats.take_period()
        got = perfstats.take_period()
        self.assertNotIn("frame", got)
        self.assertEqual([k for k in got if k != "period_s"], [])

    def test_period_seconds(self):
        perfstats.record("frame", 1.0)
        self.now[0] = 160.0
        self.assertEqual(perfstats.take_period()["period_s"], 60.0)
        self.now[0] = 190.0
        self.assertEqual(perfstats.take_period()["period_s"], 30.0)

    def test_sample_cap(self):
        """样本超过上限：只累加次数、总和、最大值，不再存样本（分位数按存下的那几个算）。"""
        perfstats.MAX_SAMPLES = 3
        for ms in (1.0, 2.0, 3.0, 4.0, 5.0):
            perfstats.record("t", ms)
        got = perfstats.take_period()["t"]
        self.assertEqual(got["n"], 5)
        self.assertEqual(got["sum"], 15.0)
        self.assertEqual(got["max"], 5.0)
        self.assertEqual(got["p50"], 2.0)  # 样本只有 [1, 2, 3]
        self.assertEqual(got["p95"], 3.0)

    def test_cpu_after_first_period(self):
        """第一次 take_period 只记基准、没有 cpu；之后按两次快照的差算（一个核的百分比）。"""
        self.kernel.values["system"] = (1_000_000, 2_000_000)  # 基准的整机时间也不能为 0
        first = perfstats.take_period()
        self.assertNotIn("cpu", first)
        # 10 秒里：本进程 2 个核、当前线程半个核、主线程一个核；整机一半时间空闲
        self.kernel.values["process"] = 20 * 10_000_000
        self.kernel.values["recog"] = 5 * 10_000_000
        self.kernel.values["ui"] = 10 * 10_000_000
        self.kernel.values["system"] = (5 * 10_000_000, 10 * 10_000_000)
        self.now[0] += 10.0
        cpu = perfstats.take_period()["cpu"]
        self.assertEqual(cpu, {"process": 200.0, "recog": 50.0, "ui": 100.0, "system": 50.0})

    def test_cpu_missing_on_failure(self):
        """某一项 Win32 调用失败时那一项不出现，其余照常；不是 Windows 时 cpu 整个不出现。"""
        perfstats.take_period()
        self.kernel.fail.add("system")
        self.kernel.values["process"] = 10_000_000
        self.now[0] += 1.0
        cpu = perfstats.take_period()["cpu"]
        self.assertNotIn("system", cpu)
        self.assertEqual(cpu["process"], 100.0)
        perfstats._cpu_base = None
        perfstats._kernel32 = None
        self.now[0] += 1.0
        self.assertNotIn("cpu", perfstats.take_period())

    def test_never_raises(self):
        """取时钟、记一笔、取统计全都出错时不往外抛。"""
        perfstats.record("bad", object())  # 转不成数：丢掉这一笔
        self.now[0] += 1.0
        perfstats.take_period()  # 不抛
        perfstats._counts = {}
        perfstats._since = None
        perfstats._cpu_base = None

        class Boom(FakeKernel):
            def GetProcessTimes(self, *a):
                raise OSError("失败")

        perfstats._kernel32 = Boom()
        perfstats.record("frame", 1.0)
        perfstats.take_period()  # 不抛
        perfstats._clock = mock.Mock(side_effect=RuntimeError("没时钟"))
        self.assertEqual(perfstats.take_period(), {})

    def test_recovers_after_failure(self):
        """记一笔出错只丢那一笔（不留下次数加了、样本没存的半截）；汇总出错只丢那一段，下一段照常，不会一直卡住。"""
        perfstats.record("bad", object())
        perfstats.record("frame", 2.0)
        got = perfstats.take_period()
        self.assertNotIn("bad", got)
        self.assertEqual(got["frame"]["n"], 1)
        perfstats.record("frame", 3.0)
        with mock.patch.object(perfstats, "_nearest_rank", side_effect=RuntimeError("坏了")):
            self.assertEqual(perfstats.take_period(), {})  # 这一段丢掉
        perfstats.record("frame", 4.0)
        self.now[0] += 5.0
        got = perfstats.take_period()
        self.assertEqual((got["frame"]["n"], got["frame"]["max"], got["period_s"]), (1, 4.0, 5.0))

    def test_threads_record_concurrently(self):
        """几个线程同时记：不丢、不抛。"""
        def work():
            for _ in range(500):
                perfstats.record("t", 1.0)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        got = perfstats.take_period()["t"]
        self.assertEqual(got["n"], 2000)
        self.assertEqual(got["sum"], 2000.0)


class FakeEngine:
    """假的文字识别引擎（代替 rapidocr_lite.RapidOCR）：记下每次调用的开关和参数。"""

    def __init__(self):
        self.use_det = self.use_cls = self.use_rec = True
        self.calls: list[dict] = []

    def __call__(self, img, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(txts=("甲", "乙"), scores=(0.9, 0.8), boxes=())

    def update_params(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class TimedEngineTest(unittest.TestCase):
    """文字识别外壳（chip_select._TimedEngine）：每次调用记一笔 ocr；属性读写、update_params 转给里面的
    引擎；识别结果和不套外壳时相同；recognize_only 用完照旧还原开关。"""

    def setUp(self):
        self._saved = (perfstats._clock, perfstats._counts, perfstats._since, perfstats._cpu_base)
        perfstats._counts = {}
        perfstats._since = None
        perfstats._cpu_base = None
        self.addCleanup(self._restore)
        self.inner = FakeEngine()
        self.engine = chip_select._TimedEngine(self.inner)

    def _restore(self):
        perfstats._clock, perfstats._counts, perfstats._since, perfstats._cpu_base = self._saved

    def test_call_records_ocr_and_passes_through(self):
        raw = FakeEngine()
        result = self.engine("图")
        self.assertEqual((result.txts, result.scores), (raw("图").txts, raw("图").scores))
        got = perfstats.take_period()
        self.assertEqual(got["ocr"]["n"], 1)
        self.assertEqual(self.inner.calls, [{}])

    def test_attributes_and_update_params(self):
        self.assertTrue(self.engine.use_det)
        self.engine.use_det = False
        self.assertFalse(self.inner.use_det)
        self.engine.update_params(use_det=True, use_rec=False)
        self.assertEqual((self.inner.use_det, self.inner.use_rec), (True, False))

    def test_recognize_only_restores(self):
        img = np.full((10, 10, 3), 128, np.uint8)
        with mock.patch.object(chip_select, "_engine", return_value=self.engine):
            got = chip_select.recognize_only(img)
        self.assertEqual(got, [("甲", 0.9), ("乙", 0.8)])
        self.assertEqual((self.inner.use_det, self.inner.use_cls, self.inner.use_rec), (True, True, True))
        self.assertEqual(self.inner.calls, [{"use_det": False, "use_cls": False, "use_rec": True}])
        self.assertEqual(perfstats.take_period()["ocr"]["n"], 1)


class UnbiasedClockTest(unittest.TestCase):
    """不含电脑睡眠时间的时钟（perfstats.unbiased_clock，数界面线程的定时器晚到多少用）：Windows 上按
    QueryUnbiasedInterruptTime（100 纳秒为单位）折成秒；没有这个函数、或取不到时退回 time.monotonic。"""

    def setUp(self):
        self.addCleanup(setattr, perfstats, "_query_unbiased", perfstats._query_unbiased)

    def test_ticks_to_seconds(self):
        def fake(target):
            target._obj.value = 123_456_789
            return 1

        perfstats._query_unbiased = fake
        self.assertAlmostEqual(perfstats.unbiased_clock(), 12.3456789, places=9)

    def test_falls_back_to_monotonic(self):
        for fake in (None, lambda target: 0, mock.Mock(side_effect=OSError("取不到"))):
            with self.subTest(fake=fake):
                perfstats._query_unbiased = fake
                before = time.monotonic()
                got = perfstats.unbiased_clock()
                self.assertLessEqual(before, got)
                self.assertLessEqual(got, time.monotonic())

    @unittest.skipUnless(os.name == "nt", "只在 Windows 上有")
    def test_real_clock_runs(self):
        """真的时钟在走，走得和单调时钟一样快（刻度随系统的时钟中断，最粗约 15.6 毫秒）。"""
        self.assertIsNotNone(perfstats._query_unbiased)
        a, m = perfstats.unbiased_clock(), time.monotonic()
        time.sleep(0.1)
        b, n = perfstats.unbiased_clock(), time.monotonic()
        self.assertAlmostEqual(b - a, n - m, delta=0.05)


if __name__ == "__main__":
    unittest.main()
