"""性能自检结果差时查原因（advisor/vision/ocr_reason.py）、比只用大核的收益（ocr_check.big_core_gain）、改本进程设置的
那几个函数（sysinfo），以及 OcrCheck 查原因、点「是」/「否」的状态流转：

- diagnose、shown：表里每一行成立和不成立、读不到的项不算、顺序、降频和电源几条的合并、unknown、declined。
- sysinfo 的新函数：大小核分组、只用大核、能用几个核、放开限制、优先级、AVX2 的三种情况。系统调用换成假的，测试里不真的
  改本进程的核限制、优先级。
- Probe：假的处理器时间和 PDH，算出整机 / 本进程 / 游戏各占多少；打不开游戏进程时游戏占用读不到；finish 重复调用；打开一半
  出错、finish 里出错时句柄和查询都关掉。
- big_core_gain：假引擎、假时钟、假 bind：比值对；怎么结束都放开限制；stop、bind 失败返回 None；读得慢时按预算收尾。
- find：什么时候比只用大核的收益、比出错时别的原因照写、什么时候写「可能的原因」；结果一般时（cores_only）只留「排到了
  小核」、不补「可能的原因」，什么时候不比照较差的。
- OcrCheck：结果差时查原因、记录下来；结果一般、有大小核时只比只用大核（在小核上时和较差一样问，不在时照旧自己收起、比值
  照存）；次数跨启动累计；没量出来时换回默认线程数；比的时候设置改了比完放回去；改好以后重新量；点「是」「否」的四种修法和
  低配模式；startup 的 retest。Overlay._game_pids 取各张的游戏进程号。
- UiSettings.big_cores、_clean_ocr_check 的读写和清理。

真实机器上的游戏进程、大小核在这里不假装跑过。
"""
import ctypes
import os
import threading
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest import mock

from advisor import perfstats, sysinfo
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import ocr_check as ui_check
from advisor.ui.overlay_parts.app import UiSettings, _clean_ocr_check
from advisor.ui.overlay_parts.ocr_check import FIX_FAILED, FIXED, LOWEND_ON, OcrNote
from advisor.vision import capture, ocr_check, ocr_reason
from advisor.vision.ocr_reason import Reason
from tests.support import sandbox


# ---------------------------------------------------------------- diagnose、shown


def state(**parts):
    """拼一份 diagnose 用的 state：没给的项是读不到。"""
    base = {"load": {}, "power": {}, "memory": {}, "performance": None, "priority": None,
            "affinity": None, "cpu": {}, "lacks_avx2": None}
    base.update(parts)
    return base


def keys(found):
    return [item.key for item in found]


class DiagnoseTest(unittest.TestCase):
    def test_power_rows(self):
        cases = (
            ("no_ac", state(power={"ac": False}), Reason("no_ac", values={"perf": None})),
            ("saver", state(power={"saver": True}), Reason("saver")),
            ("power_mode", state(power={"mode": "best_efficiency"}), Reason("power_mode")),
            ("power_plan", state(power={"plan": "power_saver"}), Reason("power_plan")),
        )
        for key, given, want in cases:
            with self.subTest(key=key):
                self.assertEqual(ocr_reason.diagnose(given), [want])

    def test_power_read_none_not_counted(self):
        for power in ({"ac": None}, {"saver": None}, {"mode": None}, {"plan": None}, {}):
            with self.subTest(power=power):
                self.assertEqual(ocr_reason.diagnose(state(power=power)), [])

    def test_throttled_alone(self):
        load = {"perf": 50, "limit": 85}
        self.assertEqual(ocr_reason.diagnose(state(load=load)),
                         [Reason("throttled", values={"perf": 50})])

    def test_throttled_needs_both_and_limit(self):
        cases = ({"perf": 50}, {"limit": 50}, {"perf": 50, "limit": 95}, {"perf": 80, "limit": 85})
        for load in cases:
            with self.subTest(load=load):
                self.assertEqual(ocr_reason.diagnose(state(load=load)), [])

    def test_power_hides_throttled(self):
        """被降频、同时有电源那几条时不单列；断电那条带上读到的百分比。"""
        load = {"perf": 40, "limit": 85}
        found = ocr_reason.diagnose(state(load=load, power={"ac": False, "saver": True}))
        self.assertEqual(keys(found), ["no_ac", "saver"])
        self.assertEqual(found[0].values, {"perf": 40})
        found = ocr_reason.diagnose(state(load=load, power={"plan": "power_saver"}))
        self.assertEqual(keys(found), ["power_plan"])

    def test_others_busy_with_game(self):
        load = {"busy": 60, "own": 10, "game": 10}
        self.assertEqual(ocr_reason.diagnose(state(load=load)),
                         [Reason("others_busy", values={"percent": 40, "with_game": False})])
        load = {"busy": 55, "own": 10, "game": 20}
        self.assertEqual(ocr_reason.diagnose(state(load=load)), [])

    def test_others_busy_without_game(self):
        load = {"busy": 70, "own": 5}
        self.assertEqual(ocr_reason.diagnose(state(load=load)),
                         [Reason("others_busy", values={"percent": 65, "with_game": True})])
        load = {"busy": 60, "own": 5}
        self.assertEqual(ocr_reason.diagnose(state(load=load)), [])

    def test_others_busy_needs_readings(self):
        for load in ({}, {"busy": 100}, {"own": 100}, {"busy": 100, "own": None}):
            with self.subTest(load=load):
                self.assertEqual(ocr_reason.diagnose(state(load=load)), [])

    def test_memory(self):
        self.assertEqual(ocr_reason.diagnose(state(memory={"available_gb": 0.5})),
                         [Reason("low_memory", values={"available_gb": 0.5})])
        self.assertEqual(ocr_reason.diagnose(state(memory={"available_gb": 2.0})), [])
        self.assertEqual(ocr_reason.diagnose(state(memory={})), [])

    def test_process_rows(self):
        self.assertEqual(ocr_reason.diagnose(state(performance="eco")), [Reason("eco", "eco")])
        for priority in ("idle", "below_normal"):
            with self.subTest(priority=priority):
                self.assertEqual(ocr_reason.diagnose(state(priority=priority)),
                                 [Reason("low_priority", "priority")])
        self.assertEqual(ocr_reason.diagnose(state(priority="normal")), [])
        self.assertEqual(ocr_reason.diagnose(state(affinity=(4, 8))),
                         [Reason("affinity", "affinity", {"allowed": 4, "total": 8})])
        self.assertEqual(ocr_reason.diagnose(state(affinity=(8, 8))), [])

    def test_small_core(self):
        split = {"big": 8, "small": 16, "ids": [1, 2, 3]}
        self.assertEqual(ocr_reason.diagnose(state(), split=split, gain=1.55),
                         [Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 1.6})])
        self.assertEqual(ocr_reason.diagnose(state(), split=split, gain=1.2), [])
        self.assertEqual(ocr_reason.diagnose(state(), split=None, gain=5.0), [])

    def test_old_cpu_and_few_cores(self):
        self.assertEqual(ocr_reason.diagnose(state(lacks_avx2=True)), [Reason("old_cpu", "lowend")])
        self.assertEqual(ocr_reason.diagnose(state(lacks_avx2=False)), [])
        self.assertEqual(ocr_reason.diagnose(state(cpu={"cores": 2, "logical": 4})),
                         [Reason("few_cores", "lowend", {"cores": 2, "logical": 4})])
        self.assertEqual(ocr_reason.diagnose(state(cpu={"cores": 4, "logical": 8})), [])
        self.assertEqual(ocr_reason.diagnose(state(cpu={"cores": None, "logical": 2})), [])

    def test_unknown(self):
        self.assertEqual(ocr_reason.diagnose(state()), [])
        self.assertEqual(ocr_reason.diagnose(state(), unknown=True), [Reason("unknown", "lowend")])
        self.assertEqual(ocr_reason.diagnose(state(performance="eco"), unknown=True), [Reason("eco", "eco")])

    def test_order(self):
        given = state(load={"busy": 90, "own": 5, "game": 5, "perf": 50, "limit": 85},
                      power={"ac": False, "saver": True}, memory={"available_gb": 0.5},
                      performance="eco", priority="idle", affinity=(2, 8), lacks_avx2=True,
                      cpu={"cores": 2, "logical": 4})
        self.assertEqual(keys(ocr_reason.diagnose(given, split={"big": 2, "small": 4}, gain=2.0)),
                         ["no_ac", "saver", "others_busy", "low_memory", "eco", "low_priority", "affinity",
                          "small_core", "old_cpu", "few_cores"])


class ShownTest(unittest.TestCase):
    def test_manual_first_then_offer(self):
        found = [Reason("others_busy", values={"percent": 40}), Reason("throttled"),
                 Reason("priority", "priority")]
        self.assertEqual(ocr_reason.shown(found), (found[0], found[2]))

    def test_manual_only(self):
        found = [Reason("others_busy"), Reason("low_memory")]
        self.assertEqual(ocr_reason.shown(found), (found[0], found[1]))

    def test_offers_only(self):
        found = [Reason("eco", "eco"), Reason("priority", "priority")]
        self.assertEqual(ocr_reason.shown(found), (found[0],))

    def test_declined(self):
        found = [Reason("eco", "eco"), Reason("priority", "priority"), Reason("affinity", "affinity")]
        self.assertEqual(ocr_reason.shown(found, ("eco",)), (found[1],))
        self.assertEqual(ocr_reason.shown(found, ("eco", "priority", "affinity")), ())

    def test_empty(self):
        self.assertEqual(ocr_reason.shown([]), ())


# ---------------------------------------------------------------- sysinfo 的新函数


def cpu_set(logical, core, efficiency, group=0, id_=None, size=32) -> bytes:
    import struct

    if id_ is None:
        id_ = logical
    entry = (struct.pack("<II", size, 0)
             + struct.pack("<IHBBBBB", id_, group, logical, core, 0, 0, efficiency))
    return entry + b"\x00" * (size - 19)


class FakeKernel32:
    def __init__(self, process_mask=0b1111, system_mask=0b1111, feature=(), set_big=1, set_affinity=1,
                 set_priority=1):
        self.process_mask, self.system_mask = process_mask, system_mask
        self.features = set(feature)
        self.set_big, self.set_affinity, self.set_priority = set_big, set_affinity, set_priority
        self.calls = []

    def GetCurrentProcess(self):
        return 1

    def GetProcessAffinityMask(self, handle, process, system):
        process._obj.value = self.process_mask
        system._obj.value = self.system_mask
        return 1

    def SetProcessAffinityMask(self, handle, mask):
        self.calls.append(("SetProcessAffinityMask", mask))
        return self.set_affinity

    def SetProcessDefaultCpuSets(self, handle, ids, count):
        values = None if ids is None else [ids[i] for i in range(count)]
        self.calls.append(("SetProcessDefaultCpuSets", values))
        return self.set_big

    def SetPriorityClass(self, handle, value):
        self.calls.append(("SetPriorityClass", value))
        return self.set_priority

    def IsProcessorFeaturePresent(self, code):
        return 1 if code in self.features else 0


class SysInfoTest(unittest.TestCase):
    def use(self, fake):
        patcher = mock.patch.object(sysinfo, "_kernel32", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def sets(self, items):
        patcher = mock.patch.object(sysinfo, "_cpu_sets", return_value=items)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_big_core_split(self):
        self.sets([
            {"id": 1, "group": 0, "logical": 0, "core": 0, "efficiency": 0},
            {"id": 2, "group": 0, "logical": 1, "core": 1, "efficiency": 0},
            {"id": 3, "group": 0, "logical": 2, "core": 0, "efficiency": 1},
        ])
        self.assertEqual(sysinfo.big_core_split(), {"big": 1, "small": 2, "ids": [3]})

    def test_big_core_split_none(self):
        """只有一种效率等级、读到、分成几个处理器组时都不算大小核。"""
        cases = (
            [{"id": i, "group": 0, "logical": i, "core": i, "efficiency": 0} for i in range(4)],
            [{"id": 1, "group": 0, "logical": 0, "core": 0, "efficiency": 0},
             {"id": 2, "group": 1, "logical": 0, "core": 0, "efficiency": 1}],
        )
        for items in cases:
            with self.subTest(items=items):
                self.sets(items)
                self.assertIsNone(sysinfo.big_core_split())
        self.sets(None)
        self.assertIsNone(sysinfo.big_core_split())

    def test_use_big_cores(self):
        fake = self.use(FakeKernel32())
        self.sets([{"id": 7, "group": 0, "logical": 0, "core": 0, "efficiency": 1},
                   {"id": 8, "group": 0, "logical": 1, "core": 1, "efficiency": 0}])
        self.assertTrue(sysinfo.use_big_cores(True))
        self.assertEqual(fake.calls[-1], ("SetProcessDefaultCpuSets", [7]))
        self.assertTrue(sysinfo.use_big_cores(False))
        self.assertEqual(fake.calls[-1], ("SetProcessDefaultCpuSets", None))

    def test_use_big_cores_without_split(self):
        fake = self.use(FakeKernel32())
        self.sets(None)
        self.assertFalse(sysinfo.use_big_cores(True))
        self.assertEqual(fake.calls, [])

    def test_use_big_cores_failure_logged_once(self):
        self.use(FakeKernel32(set_big=0))
        self.sets([{"id": 7, "group": 0, "logical": 0, "core": 0, "efficiency": 1},
                   {"id": 8, "group": 0, "logical": 1, "core": 1, "efficiency": 0}])
        patcher = mock.patch.object(sysinfo, "_big_logged", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        errors = []
        with mock.patch.object(sysinfo, "log_error", side_effect=lambda *a, **k: errors.append(a)):
            self.assertFalse(sysinfo.use_big_cores(True))
            self.assertFalse(sysinfo.use_big_cores(True))
        self.assertEqual(len(errors), 1)

    def test_affinity_counts(self):
        self.use(FakeKernel32(process_mask=0b1011, system_mask=0b11111111))
        self.assertEqual(sysinfo.affinity_counts(), (3, 8))

    def test_affinity_counts_none(self):
        for masks in ((0, 8), (4, 0)):
            with self.subTest(masks=masks):
                self.use(FakeKernel32(process_mask=masks[0], system_mask=masks[1]))
                self.assertIsNone(sysinfo.affinity_counts())
        self.use(None)
        self.assertIsNone(sysinfo.affinity_counts())

    def test_reset_affinity(self):
        fake = self.use(FakeKernel32(process_mask=0b0011, system_mask=0b1111))
        self.assertTrue(sysinfo.reset_affinity())
        self.assertEqual(fake.calls[-1], ("SetProcessAffinityMask", 0b1111))

    def test_reset_affinity_unreadable(self):
        self.use(FakeKernel32(system_mask=0))
        self.assertFalse(sysinfo.reset_affinity())

    def test_reset_priority(self):
        fake = self.use(FakeKernel32())
        self.assertTrue(sysinfo.reset_priority())
        self.assertEqual(fake.calls[-1], ("SetPriorityClass", 0x20))

    def test_lacks_avx2(self):
        self.use(FakeKernel32(feature=(40, 39)))
        self.assertFalse(sysinfo.lacks_avx2())
        self.use(FakeKernel32(feature=(39,)))
        self.assertTrue(sysinfo.lacks_avx2())

    def test_lacks_avx2_old_system(self):
        """两个编号都不认得时按系统版本判：Windows 11 起才算确实没有。"""
        fake = self.use(FakeKernel32())
        for build, want in ((22000, True), (19045, False)):
            with self.subTest(build=build), \
                    mock.patch.object(sysinfo.sys, "getwindowsversion", return_value=SimpleNamespace(build=build)):
                self.assertEqual(sysinfo.lacks_avx2(), want)

    def test_not_windows(self):
        self.use(None)
        self.assertIsNone(sysinfo.affinity_counts())
        self.assertIsNone(sysinfo.big_core_split())
        self.assertFalse(sysinfo.reset_priority())
        self.assertFalse(sysinfo.reset_affinity())
        self.assertFalse(sysinfo.lacks_avx2())


# ---------------------------------------------------------------- Probe


class _Func:
    """假的 Win32 函数：能声明 argtypes / restype，调用时转给真正的处理函数。"""

    def __init__(self, fn):
        self.fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.fn(*args)


class FakePdh:
    def __init__(self, values):
        self.values = values
        self.handles = {}
        self.collects = 0
        self.closed = 0
        self.add_status = 0  # 加计数器时返回的状态（不是 0 是加不上）
        self.collect_status = 0  # 第一次采样返回的状态（不是 0 是采样失败）
        self.PdhOpenQueryW = _Func(self._open)
        self.PdhAddEnglishCounterW = _Func(self._add)
        self.PdhCollectQueryData = _Func(self._collect)
        self.PdhGetFormattedCounterValue = _Func(self._formatted)
        self.PdhCloseQuery = _Func(self._close)

    def _open(self, data, flags, out):
        out._obj.value = 99
        return 0

    def _add(self, query, name, flags, out):
        if self.add_status:
            return self.add_status
        self.handles[id(out._obj)] = name
        out._obj.value = len(self.handles)
        return 0

    def _collect(self, query):
        self.collects += 1
        return self.collect_status if self.collects == 1 else 0

    def _formatted(self, handle, fmt, kind, out):
        name = self.handles.get(id(handle))
        if name not in self.values:
            return 1  # 读不到
        out._obj.CStatus = 0
        out._obj.doubleValue = self.values[name]
        return 0

    def _close(self, query):
        self.closed += 1
        return 0


class FakeProcessKernel:
    def __init__(self, ticks):
        self.ticks = ticks  # pid -> [起点, 终点]
        self.reads = {}
        self.closed = 0
        self.OpenProcess = _Func(self._open)
        self.GetProcessTimes = _Func(self._times)
        self.CloseHandle = _Func(self._close_handle)

    def _open(self, access, inherit, pid):
        return pid if pid in self.ticks else 0

    def _times(self, handle, creation, exited, kernel, user):
        values = self.ticks[handle]
        index = self.reads.get(handle, 0)
        self.reads[handle] = index + 1
        value = values[min(index, len(values) - 1)]
        kernel._obj.dwHighDateTime = value >> 32
        kernel._obj.dwLowDateTime = value & 0xFFFFFFFF
        user._obj.dwHighDateTime = user._obj.dwLowDateTime = 0
        return 1

    def _close_handle(self, handle):
        self.closed += 1
        return 1


class ProbeTest(unittest.TestCase):
    def run_probe(self, start, end, pids=(), kernel_ticks=None, pdh_values=None, add_status=0, collect_status=0):
        patch = mock.patch.object(perfstats, "cpu_ticks", side_effect=[start, end])
        patch.start()
        self.addCleanup(patch.stop)
        kernel = FakeProcessKernel(kernel_ticks or {})
        pdh = FakePdh(pdh_values or {})
        pdh.add_status, pdh.collect_status = add_status, collect_status
        def fake_dll(name, *args, **kwargs):
            return kernel if name == "kernel32" else pdh
        patch = mock.patch.object(ctypes, "WinDLL", side_effect=fake_dll)
        patch.start()
        self.addCleanup(patch.stop)
        probe = ocr_reason.Probe(pids)
        return probe, kernel, pdh

    def test_percentages(self):
        start, end = (100, 100, 1000), (300, 600, 2000)
        values = {ocr_reason._PERF: 45.4, ocr_reason._LIMIT: 100.0, ocr_reason._FLAGS: 0.0}
        probe, kernel, pdh = self.run_probe(start, end, pids=[42], kernel_ticks={42: [100, 300]},
                                            pdh_values=values)
        self.assertEqual(probe.finish(),
                         {"busy": 50, "own": 20, "game": 20, "perf": 45, "limit": 100, "flags": 0})
        self.assertEqual(kernel.closed, 1)
        self.assertEqual(pdh.closed, 1)

    def test_game_unreadable(self):
        """打不开游戏进程、也没给进程号时游戏占用读不到，别的照算。"""
        probe, _, _ = self.run_probe((0, 0, 100), (10, 50, 200), pids=[], pdh_values={})
        self.assertIsNone(probe.finish()["game"])
        probe, _, _ = self.run_probe((0, 0, 100), (10, 50, 200), pids=[7], kernel_ticks={})
        self.assertIsNone(probe.finish()["game"])

    def test_one_of_two_games_unreadable(self):
        """两开时有一个打不开：游戏占用整个算读不到（不然它用的处理器会算到别的程序头上），打开了的那个当场关掉。"""
        for pids, opened in (([42, 7], 1), ([7, 42], 0)):
            with self.subTest(pids=pids):
                probe, kernel, _ = self.run_probe((0, 0, 100), (10, 50, 200), pids=pids, kernel_ticks={42: [100, 300]})
                self.assertEqual(kernel.closed, opened)
                result = probe.finish()
                self.assertIsNone(result["game"])
                self.assertEqual(result["busy"], 50)
                self.assertEqual(kernel.closed, opened)  # finish 不再关一遍

    def test_finish_twice(self):
        probe, kernel, pdh = self.run_probe((0, 0, 100), (10, 50, 200))
        first = probe.finish()
        self.assertIs(probe.finish(), first)
        self.assertEqual(kernel.closed, 0)
        self.assertEqual(pdh.closed, 1)

    def test_no_cpu_ticks(self):
        probe, _, _ = self.run_probe(None, None, pdh_values={})
        self.assertEqual(probe.finish(), {"busy": None, "own": None, "game": None,
                                          "perf": None, "limit": None, "flags": None})

    def test_pdh_half_open_closes_query(self):
        """加不上计数器、第一次采样失败：查询当场关掉、不留着；性能和限速读不到，处理器占用照算。"""
        for kwargs in ({"add_status": 1}, {"collect_status": 1}):
            with self.subTest(kwargs=kwargs):
                probe, _, pdh = self.run_probe((0, 0, 100), (10, 50, 200), **kwargs)
                self.assertEqual(pdh.closed, 1)
                result = probe.finish()
                self.assertEqual(pdh.closed, 1)  # finish 不再关一遍
                self.assertEqual((result["busy"], result["own"], result["perf"], result["limit"]), (50, 10, None, None))

    def test_finish_error_still_closes(self):
        """finish 里算到一半出错：句柄和查询照样关掉，这次的读数都按读不到，不往外抛。"""
        probe, kernel, pdh = self.run_probe((0, 0, 100), RuntimeError("读不了"), pids=[42], kernel_ticks={42: [100]})
        self.assertEqual(probe.finish()["busy"], None)
        self.assertEqual(kernel.closed, 1)
        self.assertEqual(pdh.closed, 1)

    def test_game_unreadable_at_finish(self):
        """起点读得到、终点读不到：游戏占用算读不到（不拿一半的数去减），句柄照样关掉。"""
        probe, kernel, _ = self.run_probe((0, 0, 100), (10, 50, 200), pids=[42], kernel_ticks={42: [100, 300]})
        kernel.GetProcessTimes = _Func(lambda *args: 0)
        self.assertIsNone(probe.finish()["game"])
        self.assertEqual(kernel.closed, 1)

    def test_read_state_readings(self):
        with mock.patch.object(sysinfo, "power_info", return_value={"ac": True, "saver": False,
                                                                   "mode": "balanced", "plan": "balanced"}), \
                mock.patch.object(sysinfo, "memory_info", return_value={"available_gb": 3.5}), \
                mock.patch.object(sysinfo, "performance_state", return_value="high"), \
                mock.patch.object(sysinfo, "priority_class", return_value="normal"), \
                mock.patch.object(sysinfo, "affinity_counts", return_value=(4, 8)), \
                mock.patch.object(sysinfo, "cpu_info", return_value={"cores": 8, "logical": 16}), \
                mock.patch.object(sysinfo, "lacks_avx2", return_value=False):
            given = ocr_reason.read_state({"busy": 10, "own": 5, "game": 3, "perf": 100, "limit": 100,
                                           "flags": 0})
            self.assertEqual(given["affinity"], (4, 8))
            self.assertEqual(given["power"]["mode"], "balanced")
            readings = ocr_reason.readings(given, 1.55)
            self.assertEqual(readings, {"busy": 10, "own": 5, "game": 3, "perf": 100, "limit": 100, "flags": 0,
                                        "ac": True, "saver": False, "mode": "balanced", "plan": "balanced",
                                        "available_gb": 3.5, "performance": "high", "priority": "normal",
                                        "allowed": 4, "total": 8, "gain": 1.6})


# ---------------------------------------------------------------- big_core_gain


class GainClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def spend(self, ms):
        self.now += ms / 1000.0


class GainEnv:
    def __init__(self, clock, plain=30.0, big=10.0, fail=False):
        self.clock, self.plain_ms, self.big_ms = clock, plain, big
        self.fail = fail
        self.bound = False
        self.bind_calls, self.reads = [], []
        self.builds = 0

    def bind(self, on):
        self.bind_calls.append(on)
        self.bound = on
        return True

    def build(self, threads):
        self.builds += 1
        return self.read

    def read(self, image):
        self.reads.append(self.bound)
        if self.fail and len(self.reads) == 3:
            raise RuntimeError("读不了")
        self.clock.spend(self.big_ms if self.bound else self.plain_ms)


class BigCoreGainTest(unittest.TestCase):
    def run_gain(self, env, stop=None):
        return ocr_check.big_core_gain(stop, bind=env.bind, build=env.build, clock=env.clock,
                                       sleep=lambda seconds: None)

    def test_ratio(self):
        clock = GainClock()
        env = GainEnv(clock)
        self.assertEqual(self.run_gain(env), 3.0)
        self.assertEqual(env.builds, 1)
        self.assertEqual(env.bind_calls[-1], False)  # 最后放开了限制

    def test_rotation(self):
        """每轮换先后：第一轮先不限制，第二轮先只用大核，第三轮又先不限制。"""
        clock = GainClock()
        env = GainEnv(clock)
        self.run_gain(env)
        self.assertEqual(env.reads, [False, False, True, True, False, False, True])

    def test_releases_on_error(self):
        clock = GainClock()
        env = GainEnv(clock, fail=True)
        with self.assertRaises(RuntimeError):
            self.run_gain(env)
        self.assertEqual(env.bind_calls[-1], False)

    def test_stop(self):
        clock = GainClock()
        env = GainEnv(clock)
        self.assertIsNone(self.run_gain(env, stop=lambda: True))
        self.assertEqual(env.bind_calls, [False])
        self.assertEqual(env.reads, [False])  # 热身读了一次，还没开始比就叫停了

    def test_bind_fails(self):
        """设不上只用大核、放不开：返回 None，最后照样叫一次放开。"""
        for name, answer in (("都设不上", lambda on: False), ("只用大核设不上", lambda on: not on)):
            with self.subTest(name=name):
                clock = GainClock()
                env = GainEnv(clock)
                calls = []
                env.bind = lambda on, answer=answer: calls.append(on) or answer(on)
                self.assertIsNone(ocr_check.big_core_gain(bind=env.bind, build=env.build, clock=clock,
                                                          sleep=lambda seconds: None))
                self.assertIs(calls[-1], False)

    def test_budget(self):
        """读得慢：过了 GAIN_BUDGET_S、又有两轮了就不再开新的一轮，用这两轮的数；progress 交的剩余时间不是负数。"""
        clock = GainClock()
        env = GainEnv(clock, plain=ocr_check.GAIN_BUDGET_S * 1000.0, big=ocr_check.GAIN_BUDGET_S * 500.0)
        left = []
        self.assertEqual(ocr_check.big_core_gain(bind=env.bind, build=env.build, clock=clock, progress=left.append,
                                                 sleep=lambda seconds: None), 2.0)
        self.assertEqual(env.reads, [False, False, True, True, False])  # 热身一次，两轮各两次
        self.assertEqual(len(left), 2)
        self.assertTrue(all(value >= 0 for value in left))

    def test_not_enough_reads(self):
        """每种不足两次时返回 None（叫停得很早）。"""
        clock = GainClock()
        env = GainEnv(clock)
        calls = []

        def stop():
            calls.append(1)
            return len(calls) > 2

        self.assertIsNone(self.run_gain(env, stop=stop))
        self.assertEqual(env.bind_calls[-1], False)


class FindTest(unittest.TestCase):
    """ocr_reason.find：什么时候比只用大核的收益、比完怎么用；读数换成写好的 state，不读这台电脑。"""

    SPLIT = {"big": 8, "small": 16, "ids": [1, 2]}

    def find(self, kind="poor", state_parts=None, split=SPLIT, gain=2.0, **kwargs):
        given = state(**(state_parts or {}))
        probe = SimpleNamespace(finish=lambda: {})
        effect = {"side_effect": gain} if isinstance(gain, Exception) else {"return_value": gain}
        with mock.patch.object(ocr_reason, "read_state", return_value=given), \
                mock.patch.object(sysinfo, "big_core_split", return_value=split), \
                mock.patch.object(ocr_check, "big_core_gain", **effect) as compare, \
                mock.patch.object(ocr_reason, "log_error") as logged:
            found, readings = ocr_reason.find(probe, kind, threads=8, **kwargs)
        return found, readings, compare, logged

    def test_compares_and_offers(self):
        stop, progress = (lambda: False), (lambda left: None)
        found, readings, compare, _ = self.find(stop=stop, progress=progress)
        compare.assert_called_once_with(stop, threads=8, progress=progress)
        self.assertEqual(found, (Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}),))
        self.assertEqual(readings["gain"], 2.0)

    def test_no_compare(self):
        """没有大小核、忽快忽慢、设置里已经开着、玩家说过不要、本进程被设成省电、被限核时都不比：比出来的不是核的大小、可能只是
        抖动，或者用不着。"""
        cases = (("no_split", {"split": None}), ("noisy", {"kind": "noisy"}), ("setting_on", {"big_cores_on": True}),
                 ("declined", {"declined": ("big_cores",)}), ("eco", {"state_parts": {"performance": "eco"}}),
                 ("limited", {"state_parts": {"affinity": (2, 8)}}))
        for name, kwargs in cases:
            with self.subTest(case=name):
                found, readings, compare, _ = self.find(**kwargs)
                compare.assert_not_called()
                self.assertIsNone(readings["gain"])
                self.assertNotIn("small_core", [reason.key for reason in found])

    def test_compare_error_keeps_other_reasons(self):
        """比的时候出错：记错误记录，别的原因照写。"""
        found, readings, _, logged = self.find(gain=RuntimeError("读不了"), state_parts={"power": {"ac": False}})
        logged.assert_called_once()
        self.assertEqual([reason.key for reason in found], ["no_ac"])
        self.assertIsNone(readings["gain"])

    def test_unknown_only_for_poor_and_slow(self):
        """什么都没查到时，较差、太慢写「可能的原因」，忽快忽慢不写。"""
        for kind, want in (("poor", ["unknown"]), ("slow", ["unknown"]), ("noisy", [])):
            with self.subTest(kind=kind):
                found, _, _, _ = self.find(kind, gain=None)
                self.assertEqual([reason.key for reason in found], want)

    # 查得到好几条原因的读数：没插电、别的程序占得多、本进程优先级被调低
    BUSY = {"power": {"ac": False}, "load": {"busy": 90, "own": 10, "game": 20}, "priority": "below_normal"}

    def test_cores_only_keeps_small_core(self):
        """结果一般（cores_only）：照样比，要写的原因只留「排到了小核」，查到的别的原因不写；读数照旧整份返回。"""
        found, readings, compare, _ = self.find("fair", state_parts=self.BUSY, cores_only=True)
        compare.assert_called_once()
        self.assertEqual(found, (Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}),))
        self.assertEqual((readings["gain"], readings["ac"], readings["busy"], readings["priority"]),
                         (2.0, False, 90, "below_normal"))
        found, _, _, _ = self.find("poor", state_parts=self.BUSY)  # 较差时照旧挑两条写
        self.assertEqual([reason.key for reason in found], ["no_ac", "low_priority"])

    def test_cores_only_nothing_else(self):
        """结果一般、比出来不到分界（本来就在大核上）或者没比出来：什么都不写，也不补「可能的原因」；比值照存。"""
        for gain in (sysinfo.BIG_CORE_GAIN_MIN - 0.1, 1.0, None, RuntimeError("读不了")):
            with self.subTest(gain=gain):
                found, readings, _, _ = self.find("fair", state_parts=self.BUSY, gain=gain, cores_only=True)
                self.assertEqual(found, ())
                self.assertEqual(readings["gain"], round(gain, 1) if isinstance(gain, float) else None)
        found, _, _, _ = self.find("fair", gain=sysinfo.BIG_CORE_GAIN_MIN, cores_only=True)  # 正好到分界也算
        self.assertEqual([reason.key for reason in found], ["small_core"])

    def test_cores_only_same_no_compare(self):
        """结果一般时什么时候不比照较差的：设置里已经开着、玩家说过不要、本进程被设成省电、被限核、没有大小核。"""
        cases = (("no_split", {"split": None}), ("setting_on", {"big_cores_on": True}),
                 ("declined", {"declined": ("big_cores",)}), ("eco", {"state_parts": {"performance": "eco"}}),
                 ("limited", {"state_parts": {"affinity": (2, 8)}}))
        for name, kwargs in cases:
            with self.subTest(case=name):
                found, readings, compare, _ = self.find("fair", cores_only=True, **kwargs)
                compare.assert_not_called()
                self.assertEqual(found, ())
                self.assertIsNone(readings["gain"])


# ---------------------------------------------------------------- OcrCheck 的状态流转


class FakeSession:
    def __init__(self, record=None):
        self.settings = SimpleNamespace(ocr_check=dict(record or {}), big_cores=False)
        self.has_game_state = False
        self.game = SimpleNamespace(game_over=False)
        self.game_quiet = False
        self.saves = 0
        self.logged = []

    def save(self):
        self.saves += 1

    def log_state(self, kind, **data):
        self.logged.append((kind, data))


class FakeReasons:
    def __init__(self, begin_result=None, find_result=((), {}), find_error=None, begin_error=None):
        self.begin_result = begin_result if begin_result is not None else SimpleNamespace(finish=lambda: {})
        self.find_result, self.find_error, self.begin_error = find_result, find_error, begin_error
        self.pids, self.finds = [], []

    def begin(self, pids):
        self.pids.append(tuple(pids))
        if self.begin_error is not None:
            raise self.begin_error
        return self.begin_result

    def find(self, probe, kind, **kwargs):
        self.finds.append((kind, kwargs))
        if self.find_error is not None:
            raise self.find_error
        return self.find_result


class Env:
    def __init__(self, record=None, reasons=None, activity="game"):
        self.session = FakeSession(record)
        self.lock = threading.Lock()
        self.watcher = SimpleNamespace(paused=False, lowend=False)
        self.activity = activity
        self.later_calls = []
        self.sessions = [self.session]
        self.shown = []
        self.reasons = reasons or FakeReasons()
        self.set_lowend_calls, self.set_big_cores_calls = [], []
        self.check = ui_check.OcrCheck(
            session=self.session, lock=self.lock, run_background=lambda fn: fn(),
            later=lambda ms, fn: self.later_calls.append((ms, fn)),
            watcher=lambda: self.watcher, activity=lambda: self.activity, sessions=lambda: self.sessions,
            refresh=lambda: self.shown.append(self.check.notice()), clock=lambda: 100.0,
            game_pids=lambda: [11, 22],
            set_lowend=lambda on: self.set_lowend_calls.append(on),
            set_big_cores=lambda on: self.set_big_cores_calls.append(on) or True,
            big_cores_on=lambda: False, reasons=self.reasons)

    def run(self, result):
        """开始一次并等后台量完（run_background 先记下来，再当场调用）。"""
        pending = []
        self.check.run_background = pending.append
        with mock.patch.object(ocr_check, "run", return_value=result):
            self.check._begin()
            pending[0]()

    def accepted(self, offer):
        note = OcrNote("poor", threads=4, ms=ui_check.POOR_MS + 100,
                       reasons=(Reason(offer_key(offer), offer),))
        self.check._outcome = note
        self.check.accept()


def offer_key(fix):
    return "eco" if fix == "eco" else "priority" if fix == "priority" else \
        "affinity" if fix == "affinity" else "small_core" if fix == "big_cores" else "old_cpu"


class OcrCheckReasonTest(unittest.TestCase):
    def setUp(self):
        self.errors = []
        patcher = mock.patch.object(ui_check, "log_error",
                                    side_effect=lambda *a, **k: self.errors.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_poor_reason_recorded(self):
        reason = Reason("throttled", values={"perf": 50})
        env = Env()
        env.reasons.find_result = ((reason,), {"perf": 50})
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertEqual(env.check.notice().reasons, (reason,))
        self.assertEqual(env.reasons.pids, [(11, 22)])
        record = env.session.settings.ocr_check
        self.assertEqual(record["reasons"], ["throttled"])
        self.assertEqual(record["readings"], {"perf": 50})
        self.assertEqual(env.reasons.finds[0][0], "poor")

    def test_poor_transient_retest(self):
        """结果差、原因是会变的：记下次数和 retest，下次启动再量；次数用满时只记次数、不再 retest，提示也不写「下次再试」。"""
        reason = Reason("others_busy", values={"percent": 40})
        env = Env()
        env.reasons.find_result = ((reason,), {})
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        record = env.session.settings.ocr_check
        self.assertEqual(record["tries"], 1)
        self.assertTrue(record["retest"])
        self.assertTrue(env.check.notice().retry)

        cores = os.cpu_count() or 1
        env = Env({"cores": cores, "tries": ui_check.MAX_TRIES - 1, "retest": True})
        env.reasons.find_result = ((reason,), {})
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        record = env.session.settings.ocr_check
        self.assertEqual(record["tries"], ui_check.MAX_TRIES)
        self.assertNotIn("retest", record)
        self.assertFalse(env.check.notice().retry)

    def test_tries_add_up_with_failed_runs(self):
        """较差（原因是一时的）和太慢、量不准交替出现：次数跨启动接着算，一共量满 MAX_TRIES 次就不再量，不会没完没了。"""
        poor = ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, "")
        slow = ocr_check.Result(None, {}, 2, ocr_check.SLOW)
        noisy = ocr_check.Result(None, {2: 400}, 5, ocr_check.NOISY)
        reason = Reason("no_ac", values={"perf": None})
        for name, order in (("较差先", (poor, slow)), ("太慢先", (slow, poor)), ("量不准", (noisy, poor))):
            with self.subTest(order=name), mock.patch.object(os, "cpu_count", return_value=16), \
                    mock.patch.object(ocr_check.chip_select, "use_ocr_threads"):
                record, runs, last = {}, 0, None
                for result in order * ui_check.MAX_TRIES:
                    env = Env(record)
                    env.reasons.find_result = ((reason,), {})
                    env.check.startup()
                    if not env.later_calls:  # 这次启动不用再量了
                        break
                    env.run(result)
                    runs += 1
                    last = env.check.notice()
                    record = env.session.settings.ocr_check
                self.assertEqual(runs, ui_check.MAX_TRIES)
                self.assertFalse(last.retry)  # 最后一次不再说「下次启动时再测一次」

    def test_failed_run_back_to_default_threads(self):
        """再量的时候手上是上一次量出来的线程数：这次没量出来时换回默认的，和提示上写的、下次启动时用的一样；中途停下的不换。"""
        retest = {"cores": 16, "threads": 8, "tries": 1, "retest": True}
        with mock.patch.object(os, "cpu_count", return_value=16):
            env = Env(retest)
            with mock.patch.object(ocr_check.chip_select, "use_ocr_threads") as use:
                env.check.startup()
                use.assert_called_once_with(8)
                env.run(ocr_check.Result(None, {}, 2, ocr_check.SLOW))
                use.assert_called_with(ocr_check.DEFAULT_THREADS)
            self.assertEqual(env.check.notice().threads, ocr_check.DEFAULT_THREADS)
            self.assertNotIn("threads", env.session.settings.ocr_check)

            env = Env(retest)
            with mock.patch.object(ocr_check.chip_select, "use_ocr_threads") as use:
                env.run(ocr_check.Result(None, {}, 2, ocr_check.STOPPED))
                use.assert_not_called()
            self.assertEqual(env.session.settings.ocr_check, retest)

    def test_setting_turned_on_while_comparing(self):
        """比只用大核的收益时玩家在设置里打开了这一项（比的时候、比完都会放开限制）：比完按现在的设置放回去；一直没开的不动。"""
        for states, want in (((False, True), [mock.call(True)]), ((False, False), [])):
            with self.subTest(states=states):
                env = Env()
                answers = iter(states)
                env.check.big_cores_on = lambda answers=answers: next(answers)
                with mock.patch.object(sysinfo, "use_big_cores") as use:
                    env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
                self.assertIs(env.reasons.finds[0][1]["big_cores_on"], False)
                self.assertEqual(use.call_args_list, want)

    def test_poor_permanent_reason_no_retest(self):
        """原因不会自己变好（优先级、核太少等）时不记 retest。"""
        env = Env()
        env.reasons.find_result = ((Reason("low_priority", "priority"),), {})
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertNotIn("tries", env.session.settings.ocr_check)
        self.assertNotIn("retest", env.session.settings.ocr_check)
        self.assertFalse(env.check.notice().retry)

    def test_good_and_fair_do_not_query(self):
        """良好不查；一般在没有大小核的电脑上也不查（测试默认按没有大小核算）。"""
        for ms, big in ((100, False), (100, True), (ui_check.FAIR_MS + 1, False), (ui_check.POOR_MS, False)):
            with self.subTest(ms=ms, big=big), mock.patch.object(sysinfo, "has_big_cores", return_value=big):
                env = Env()
                env.run(ocr_check.Result(4, {4: ms}, 5, ""))
                self.assertEqual(env.reasons.finds, [])
                self.assertNotIn("reasons", env.session.settings.ocr_check)
                self.assertNotIn("readings", env.session.settings.ocr_check)

    def test_fair_with_big_cores_compares(self):
        """一般、有大小核：也查，只比只用大核（cores_only），比的时候照旧报剩余时间；较差、太慢照旧查全（不带 cores_only）。"""
        cases = (("fair", ocr_check.Result(4, {4: ui_check.FAIR_MS + 1}, 5, ""), True),
                 ("poor", ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""), False),
                 ("slow", ocr_check.Result(None, {}, 2, ocr_check.SLOW), False))
        for kind, result, only in cases:
            with self.subTest(kind=kind), mock.patch.object(sysinfo, "has_big_cores", return_value=True):
                env = Env()
                env.run(result)
                self.assertEqual(len(env.reasons.finds), 1)
                found_kind, kwargs = env.reasons.finds[0]
                self.assertEqual((found_kind, kwargs["cores_only"]), (kind, only))
                self.assertEqual(kwargs["progress"], env.check._on_progress)
                self.assertEqual(kwargs["threads"], result.threads or ocr_check.DEFAULT_THREADS)

    def test_fair_small_core_asks(self):
        """一般、比出来排在小核上：和较差一样写进记录（原因、读数），提示带原因、问是否，不自己收起；不记 retest、次数。"""
        reason = Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0})
        with mock.patch.object(sysinfo, "has_big_cores", return_value=True):
            env = Env()
            env.reasons.find_result = ((reason,), {"gain": 2.0})
            env.run(ocr_check.Result(4, {4: ui_check.FAIR_MS + 1}, 5, ""))
        note = env.check.notice()
        self.assertEqual((note.kind, note.reasons, note.offer, note.retry), ("fair", (reason,), "big_cores", False))
        record = env.session.settings.ocr_check
        self.assertEqual((record["reasons"], record["readings"]), (["small_core"], {"gain": 2.0}))
        self.assertNotIn("retest", record)
        self.assertNotIn("tries", record)
        env.check._tick()
        self.assertNotIn(env.check.dismiss, [fn for _, fn in env.later_calls])

    def test_fair_on_big_cores_closes(self):
        """一般、比出来本来就在大核上：提示照旧只写「一般」那句、过一会儿自己收起；比值照存进记录（设置窗口按它不显示「只用
        大核」一项）。"""
        with mock.patch.object(sysinfo, "has_big_cores", return_value=True):
            env = Env()
            env.reasons.find_result = ((), {"gain": 1.1})
            env.run(ocr_check.Result(4, {4: ui_check.FAIR_MS + 1}, 5, ""))
        note = env.check.notice()
        self.assertEqual((note.kind, note.reasons), ("fair", ()))
        record = env.session.settings.ocr_check
        self.assertEqual(record["readings"], {"gain": 1.1})
        self.assertNotIn("reasons", record)
        self.assertEqual(ui_check.recorded_gain(record), 1.1)
        env.check._tick()
        self.assertIn((ui_check.RESULT_MS, env.check.dismiss), env.later_calls)

    def test_gain_kept_when_not_compared(self):
        """再量时没比只用大核（结果良好不查；查了但没比：开关开着、比到一半停下等）：记录里留着这台电脑上一次比出来的倍数、
        标上 gain_kept，设置窗口照它写（点了「是」以后又关掉，照样提醒在小核上）；这次的其余读数照存；识别记录照记这次的读数。
        这次比了就换成这次的；换了电脑（核心数不同）的旧倍数不留。"""
        cores = os.cpu_count() or 1
        before = {"cores": cores, "threads": 4, "ms": {"4": 400}, "rounds": 5, "readings": {"gain": 2.0, "busy": 30}}
        good = ocr_check.Result(4, {4: 100}, 5, "")
        fair = ocr_check.Result(4, {4: ui_check.FAIR_MS + 1}, 5, "")
        cases = (("良好不查", good, False, ((), {}), {"gain": 2.0, "gain_kept": True}),
                 ("查了没比", fair, True, ((), {"gain": None, "busy": 20}), {"gain": 2.0, "busy": 20, "gain_kept": True}))
        for name, result, big, found, want in cases:
            with self.subTest(name), mock.patch.object(sysinfo, "has_big_cores", return_value=big):
                env = Env(before)
                env.reasons.find_result = found
                env.run(result)
                record = env.session.settings.ocr_check
                self.assertEqual(record["readings"], want)
                self.assertEqual(ui_check.recorded_gain(record), 2.0)
                logged = [data for kind, data in env.session.logged if kind == "ocr_check"][-1]
                self.assertIsNone(logged["readings"].get("gain"))
                self.assertNotIn("gain_kept", logged["readings"])

        with mock.patch.object(sysinfo, "has_big_cores", return_value=True):
            env = Env(before)
            env.reasons.find_result = ((), {"gain": 1.1})
            env.run(fair)
        self.assertEqual(env.session.settings.ocr_check["readings"], {"gain": 1.1})

        env = Env({**before, "cores": cores + 2})
        env.run(good)
        self.assertNotIn("readings", env.session.settings.ocr_check)
        self.assertIsNone(ui_check.recorded_gain(env.session.settings.ocr_check))

    def test_noisy_with_reason_stays(self):
        env = Env()
        env.reasons.find_result = ((Reason("others_busy", values={"percent": 40}),), {})
        env.run(ocr_check.Result(None, {2: 400}, 5, ocr_check.NOISY))
        env.check._tick()
        self.assertNotIn(env.check.dismiss, [fn for _, fn in env.later_calls])
        self.assertEqual(env.check.notice().kind, "noisy")

    def test_noisy_without_reason_closes(self):
        env = Env()
        env.run(ocr_check.Result(None, {2: 400}, 5, ocr_check.NOISY))
        env.check._tick()
        self.assertIn(env.check.dismiss, [fn for _, fn in env.later_calls])

    def test_find_error_keeps_result(self):
        env = Env(reasons=FakeReasons(find_error=RuntimeError("查不了")))
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertEqual(env.check.notice().kind, "poor")
        self.assertEqual(env.session.settings.ocr_check["threads"], 4)
        self.assertTrue(self.errors)

    def test_probe_error_keeps_result(self):
        env = Env(reasons=FakeReasons(begin_error=RuntimeError("记不了")))
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertEqual(env.check.notice().kind, "poor")
        self.assertEqual(env.reasons.finds, [])

    def test_reasons_module_error_keeps_result(self):
        """查原因的模块导入不了：结果照常，提示不会一直停在「正在量」。"""
        env = Env()
        env.check._reasons = mock.Mock(side_effect=ImportError("没有"))
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertFalse(env.check._running)
        self.assertEqual(env.check.notice().kind, "poor")

    def test_probe_always_finished(self):
        """不管这一次怎么结束（良好、中途停下、量的时候出错、结果差、查原因出错），读数都收尾：句柄和 PDH 查询不留着。"""
        poor = ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, "")
        cases = (("good", ocr_check.Result(4, {4: 100}, 5, ""), None),
                 ("stopped", ocr_check.Result(None, {}, 0, ocr_check.STOPPED), None),
                 ("poor", poor, None), ("find_error", poor, RuntimeError("查不了")))
        for name, result, error in cases:
            with self.subTest(case=name):
                finished = []
                probe = SimpleNamespace(finish=lambda finished=finished: finished.append(1) or {})
                env = Env(reasons=FakeReasons(begin_result=probe, find_error=error))
                env.run(result)
                self.assertEqual(len(finished), 1)
        finished = []
        probe = SimpleNamespace(finish=lambda: finished.append(1) or {})
        env = Env(reasons=FakeReasons(begin_result=probe))
        pending = []
        env.check.run_background = pending.append
        with mock.patch.object(ocr_check, "run", side_effect=RuntimeError("炸了")):
            env.check._begin()
            pending[0]()
        self.assertEqual(len(finished), 1)
        self.assertEqual(env.check.notice().kind, "error")


class AcceptTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ui_check, "log_error")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_big_cores(self):
        env = Env()
        env.accepted("big_cores")
        self.assertEqual(env.set_big_cores_calls, [True])
        self.assertEqual(env.check.notice(), OcrNote(FIXED))
        self.assertFalse(env.check._finished)
        self.assertEqual(env.check._runs, 0)
        self.assertIn((ui_check.RETEST_DELAY_MS, env.check._poll), env.later_calls)

    def test_eco(self):
        env = Env()
        with mock.patch.object(sysinfo, "declare_high_performance"), \
                mock.patch.object(sysinfo, "priority_class", return_value="normal"), \
                mock.patch.object(sysinfo, "performance_state", return_value="high"):
            env.accepted("eco")
        self.assertEqual(env.check.notice(), OcrNote(FIXED))

    def test_priority(self):
        env = Env()
        with mock.patch.object(sysinfo, "reset_priority", return_value=True) as reset, \
                mock.patch.object(sysinfo, "priority_class", return_value="normal"):
            env.accepted("priority")
        reset.assert_called_once_with()
        self.assertEqual(env.check.notice(), OcrNote(FIXED))

    def test_affinity(self):
        env = Env()
        with mock.patch.object(sysinfo, "reset_affinity", return_value=True) as reset, \
                mock.patch.object(sysinfo, "affinity_counts", return_value=(8, 8)):
            env.accepted("affinity")
        reset.assert_called_once_with()
        self.assertEqual(env.check.notice(), OcrNote(FIXED))

    def test_big_cores_failure(self):
        env = Env()
        env.check.set_big_cores = lambda on: False
        env.accepted("big_cores")
        self.assertEqual(env.check.notice(), OcrNote(FIX_FAILED))
        self.assertIn((ui_check.RESULT_MS, env.check.dismiss), env.later_calls)

    def test_eco_failure(self):
        env = Env()
        with mock.patch.object(sysinfo, "declare_high_performance"), \
                mock.patch.object(sysinfo, "priority_class", return_value="normal"), \
                mock.patch.object(sysinfo, "performance_state", return_value="eco"):
            env.accepted("eco")
        self.assertEqual(env.check.notice(), OcrNote(FIX_FAILED))
        self.assertIn((ui_check.RESULT_MS, env.check.dismiss), env.later_calls)

    def test_priority_failure(self):
        env = Env()
        with mock.patch.object(sysinfo, "reset_priority", return_value=True), \
                mock.patch.object(sysinfo, "priority_class", return_value="below_normal"):
            env.accepted("priority")
        self.assertEqual(env.check.notice(), OcrNote(FIX_FAILED))

    def test_affinity_failure(self):
        env = Env()
        with mock.patch.object(sysinfo, "reset_affinity", return_value=True), \
                mock.patch.object(sysinfo, "affinity_counts", return_value=(2, 8)):
            env.accepted("affinity")
        self.assertEqual(env.check.notice(), OcrNote(FIX_FAILED))

    def test_lowend(self):
        env = Env()
        env.check._outcome = OcrNote("poor", threads=4, reasons=(Reason("old_cpu", "lowend"),))
        env.check.accept()
        self.assertEqual(env.set_lowend_calls, [True])
        self.assertEqual(env.check.notice(), OcrNote(LOWEND_ON))
        self.assertIn((ui_check.RESULT_MS, env.check.dismiss), env.later_calls)

    def test_decline_then_find_gets_it(self):
        env = Env({"cores": os.cpu_count() or 1})
        env.check._outcome = OcrNote("poor", threads=4, reasons=(Reason("low_priority", "priority"),))
        env.check.decline()
        self.assertEqual(env.session.settings.ocr_check["declined"], ["priority"])
        self.assertEqual(env.session.logged[-1][0], "ocr_check_decline")
        self.assertIsNone(env.check.notice())
        env.reasons.find_result = ((), {})
        env.run(ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""))
        self.assertEqual(env.reasons.finds[0][1]["declined"], ("priority",))

    def test_declined_not_kept_for_other_cores(self):
        env = Env({"cores": (os.cpu_count() or 1) + 1, "declined": ["priority"]})
        self.assertEqual(env.check._declined(), ())

    def test_startup_retest(self):
        cores = os.cpu_count() or 1
        threads = ocr_check.candidates(cores)[0]
        env = Env({"cores": cores, "threads": threads, "tries": 1, "retest": True})
        with mock.patch.object(ocr_check.chip_select, "use_ocr_threads"):
            env.check.startup()
        self.assertEqual(env.later_calls, [(ui_check.FIRST_DELAY_MS, env.check._poll)])
        self.assertFalse(env.check._finished)

    def test_startup_retest_exhausted(self):
        cores = os.cpu_count() or 1
        threads = ocr_check.candidates(cores)[0]
        env = Env({"cores": cores, "threads": threads, "tries": ui_check.MAX_TRIES, "retest": True})
        with mock.patch.object(ocr_check.chip_select, "use_ocr_threads"):
            env.check.startup()
        self.assertEqual(env.later_calls, [])
        self.assertTrue(env.check._finished)


class GamePidsTest(unittest.TestCase):
    """Overlay._game_pids：各张悬浮窗跟着的游戏窗口换成进程号；窗口没了（进程号 0）、还没找到窗口的不要。"""

    @staticmethod
    def pid_of(hwnd):
        return {5: 50, 6: 60}.get(hwnd, 0)

    def test_one_window(self):
        window = SimpleNamespace(lanes=None, follower=SimpleNamespace(game_hwnd=5))
        with mock.patch.object(capture, "window_pid", side_effect=self.pid_of):
            self.assertEqual(Overlay._game_pids(window), [50])

    def test_lanes(self):
        def lane(hwnd):
            return SimpleNamespace(window=SimpleNamespace(follower=SimpleNamespace(game_hwnd=hwnd)))

        lanes = SimpleNamespace(all=lambda: [lane(5), lane(6)])
        window = SimpleNamespace(lanes=lanes, follower=None)
        with mock.patch.object(capture, "window_pid", side_effect=self.pid_of):
            self.assertEqual(Overlay._game_pids(window), [50, 60])
            lanes.all = lambda: [lane(5), lane(7), lane(None)]  # 7：窗口没了；None：还没找到
            self.assertEqual(Overlay._game_pids(window), [50])

    def test_window_pid_without_window(self):
        self.assertEqual(capture.window_pid(0), 0)


# ---------------------------------------------------------------- 设置


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = sandbox.temp_dir(self, "test-ocr-reason-") / "state.json"

    def test_big_cores(self):
        self.assertFalse(UiSettings().big_cores)
        self.assertTrue(UiSettings(big_cores=True).big_cores)
        self.assertFalse(UiSettings(big_cores="yes").big_cores)
        settings = UiSettings(hero="凛", big_cores=True)
        settings.save(self.path)
        self.assertTrue(UiSettings.load(self.path).big_cores)

    def test_clean_new_keys(self):
        good = {"cores": 16, "threads": 4, "retest": True, "reasons": ["throttled", "eco"],
                "declined": ["eco", "big_cores"], "readings": {"busy": 20, "ac": False, "mode": None, "x": "y"}}
        self.assertEqual(_clean_ocr_check(good), good)

    def test_record_survives_file(self):
        """一条结果差的记录存盘读回来还是原样：下次启动要靠 retest、tries 再量，靠 declined 不再问。"""
        record = {"cores": 16, "threads": 4, "ms": {"4": 900}, "rounds": 5, "tries": 1, "retest": True,
                  "reasons": ["no_ac"], "declined": ["big_cores"],
                  "readings": {"busy": 20, "ac": False, "mode": None, "available_gb": 1.5}}
        UiSettings(hero="凛", ocr_check=record).save(self.path)
        self.assertEqual(UiSettings.load(self.path).ocr_check, record)

    def test_clean_bad_new_keys(self):
        bad = {"retest": "yes", "reasons": "throttled", "declined": ["nope", "eco", 3],
               "readings": {"busy": 20, "x": [], 2: 1}}
        self.assertEqual(_clean_ocr_check(bad), {"declined": ["eco"], "readings": {"busy": 20}})
        self.assertEqual(_clean_ocr_check({"readings": {"x": []}}), {})


if __name__ == "__main__":
    unittest.main()
