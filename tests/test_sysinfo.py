"""向 Windows 声明本进程要高性能、读回本进程的运行状况（advisor/sysinfo.py）。

- declare_high_performance：调 SetProcessInformation 的 ProcessPowerThrottling，传进去的结构是 Version 1、
  ControlMask 1（PROCESS_POWER_THROTTLING_EXECUTION_SPEED）、StateMask 0，信息类 4、大小 12 字节；返回 0（设不上）、
  调用本身出错（老系统没有这个信息类）时返回 False，只记一次错误记录，不抛出；不是 Windows 时什么都不做。
- performance_state：ControlMask 带 1 而 StateMask 不带 → high，两个都带 → eco，ControlMask 不带 → auto，读不到 → None。
- priority_class：几个优先级类别换成名字，别的值写成 0x…，读不到 → None。
- 这台电脑的配置（machine_info 一组）：CPU Set 缓冲区的解析（按 Size 跳、跳过别的 Type、截断、分处理器组）、写成中文
  几行（machine_lines）；电源计划只写名字（系统自带的四个，别的写 other），不写原始 GUID，计划不是「平衡」时电源模式
  写「不适用」；displays 读屏幕时本线程临时切到按每块屏幕的缩放、读完切回，切不成时标明没按实际缩放读；不是 Windows
  时模块照样导入得了。

系统调用换成假的（模块级的 _kernel32、_user32、_shcore、_powrprof），只在 Windows 上真调一次（只影响跑测试的这个进程）。
错误记录写进测试自己的临时文件，不写真的错误记录。
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import struct
import sys
import time
import unittest
import uuid
from ctypes import wintypes
from unittest import mock

from advisor import sysinfo
from tests.support.errors import capture_errors


def cpu_set(logical, core, efficiency, group=0, id_=None, kind=0, size=32) -> bytes:
    """拼一项 SYSTEM_CPU_SET_INFORMATION 的字节：Size、Type、CpuSet 的 Id/Group/LogicalProcessorIndex/CoreIndex/
    EfficiencyClass，其余补零到 size。"""
    if id_ is None:
        id_ = logical
    entry = struct.pack("<II", size, kind) + struct.pack("<IHBBBBB", id_, group, logical, core, 0, 0, efficiency)
    return entry + b"\x00" * (size - 19)


class FakeKernel32:
    """假的 kernel32：记下调用了哪个函数、传了什么，返回预先给的结果；error 给了时调用都抛出它。

    state 是 GetProcessInformation 写回的结构（ControlMask, StateMask）；priority 是 GetPriorityClass 的返回值。"""

    PROCESS = 0x7F01  # 假的伪句柄

    def __init__(self, set_result=1, get_result=1, priority=0x20, error: Exception | None = None,
                 state: tuple[int, int] = (1, 0)):
        self.calls: list = []
        self.set_result, self.get_result, self.priority = set_result, get_result, priority
        self.error, self.state = error, state
        self.passed = None  # SetProcessInformation 传进去的结构
        self.get_versions: list = []  # GetProcessInformation 收到的 Version（要先填好）

    def GetCurrentProcess(self):
        return self.PROCESS

    def SetProcessInformation(self, handle, info_class, pointer, size):
        self.calls.append(("SetProcessInformation", handle, info_class, size))
        if self.error is not None:
            raise self.error
        self.passed = pointer._obj
        return self.set_result

    def GetProcessInformation(self, handle, info_class, pointer, size):
        self.calls.append(("GetProcessInformation", handle, info_class, size))
        if self.error is not None:
            raise self.error
        self.get_versions.append(pointer._obj.Version)
        pointer._obj.ControlMask, pointer._obj.StateMask = self.state
        return self.get_result

    def GetPriorityClass(self, handle):
        if self.error is not None:
            raise self.error
        return self.priority


class SysInfoCase(unittest.TestCase):
    def use(self, fake: FakeKernel32) -> FakeKernel32:
        patcher = mock.patch.object(sysinfo, "_kernel32", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class DeclareTest(SysInfoCase):
    def setUp(self):
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), []))
        patcher = mock.patch.object(sysinfo, "_high_logged", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sets_high(self):
        """传进去的是 Version 1、ControlMask 1、StateMask 0，信息类 4，大小 12；成了返回 True，不记错误记录。"""
        fake = self.use(FakeKernel32())
        self.assertIs(sysinfo.declare_high_performance(), True)
        self.assertEqual(fake.calls, [("SetProcessInformation", fake.PROCESS, 4, 12)])
        self.assertEqual((fake.passed.Version, fake.passed.ControlMask, fake.passed.StateMask), (1, 1, 0))
        self.assertEqual(sysinfo.POWER_THROTTLING, 4)
        self.assertEqual(sysinfo.POWER_THROTTLING_EXECUTION_SPEED, 1)
        self.assertEqual(ctypes.sizeof(sysinfo.PROCESS_POWER_THROTTLING_STATE), 12)
        self.assertEqual(self.errors.entries(), [])

    def test_zero_logged_once(self):
        """返回 0（设不上）：返回 False，只记一次错误记录（写明哪个调用、错误代码），之后再设不上也不再记，不抛出。"""
        self.use(FakeKernel32(set_result=0))
        with mock.patch.object(sysinfo.ctypes, "get_last_error", return_value=5):
            self.assertIs(sysinfo.declare_high_performance(), False)
            self.assertIs(sysinfo.declare_high_performance(), False)
        entries = self.errors.entries()
        self.assertEqual(len(entries), 1, entries)
        self.assertIn("SetProcessInformation", entries[0][1])
        self.assertIn("错误代码 5", entries[0][1])
        self.errors.clear()

    def test_exception_logged_once(self):
        """调用本身出错（老系统没有这个信息类）：同样返回 False、只记一次，不抛出。"""
        self.use(FakeKernel32(error=OSError("没有这个信息类")))
        self.assertIs(sysinfo.declare_high_performance(), False)
        self.assertIs(sysinfo.declare_high_performance(), False)
        entries = self.errors.entries()
        self.assertEqual(len(entries), 1, entries)
        self.assertIn("没有这个信息类", entries[0][1])
        self.errors.clear()

    def test_not_windows(self):
        """不是 Windows（没有 kernel32）：什么都不做，返回 False；读回的两项都是 None。"""
        with mock.patch.object(sysinfo, "_kernel32", None):
            self.assertIs(sysinfo.declare_high_performance(), False)
            self.assertIsNone(sysinfo.performance_state())
            self.assertIsNone(sysinfo.priority_class())
        self.assertEqual(self.errors.entries(), [])


class PerformanceStateTest(SysInfoCase):
    def test_states(self):
        """ControlMask 带 1 而 StateMask 不带 → high；两个都带 → eco；ControlMask 不带 → auto（两个都读回的是信息类的 4）。"""
        fake = self.use(FakeKernel32())
        for control, state, want in ((1, 0, "high"), (1, 1, "eco"), (0, 0, "auto"), (0, 1, "auto")):
            with self.subTest(control=control, state=state):
                fake.state = (control, state)
                self.assertEqual(sysinfo.performance_state(), want)
        self.assertEqual([c[2] for c in fake.calls], [4] * 4)  # 读的是同一个信息类
        self.assertEqual(fake.get_versions, [1] * 4)  # GetProcessInformation 前把 Version 填成 1（不填返回 0）

    def test_unreadable(self):
        """读不到（返回 0、调用出错）：None。"""
        self.use(FakeKernel32(get_result=0))
        self.assertIsNone(sysinfo.performance_state())
        self.use(FakeKernel32(error=OSError("没有这个函数")))
        self.assertIsNone(sysinfo.performance_state())


class PriorityClassTest(SysInfoCase):
    def test_names(self):
        """六个优先级类别换成名字，别的值写成 0x…。"""
        fake = self.use(FakeKernel32())
        cases = ((0x40, "idle"), (0x4000, "below_normal"), (0x20, "normal"), (0x8000, "above_normal"),
                 (0x80, "high"), (0x100, "realtime"), (0x200, "0x200"))
        for value, want in cases:
            with self.subTest(value=hex(value)):
                fake.priority = value
                self.assertEqual(sysinfo.priority_class(), want)

    def test_unreadable(self):
        """读到 0（失败）、调用出错：None。"""
        self.use(FakeKernel32(priority=0))
        self.assertIsNone(sysinfo.priority_class())
        self.use(FakeKernel32(error=OSError("没有这个函数")))
        self.assertIsNone(sysinfo.priority_class())


class ParseCpuSetsTest(unittest.TestCase):
    """解析 GetSystemCpuSetInformation 的缓冲区（_parse_cpu_sets、_classes_from、_range_text）：喂拼好的字节。"""

    def test_two_efficiency_classes(self):
        """两种效率等级：8 个性能核每核 2 线程（效率等级 1，逻辑处理器 16-31）+ 16 个能效核每核 1 线程
        （效率等级 0，逻辑处理器 0-15）：分成两组、等级高的在前，每组的核心数、逻辑处理器数、处理器编号区间对。"""
        buf = b"".join(cpu_set(i, core=i, efficiency=0) for i in range(16))
        for i in range(8):
            buf += cpu_set(16 + 2 * i, core=i, efficiency=1) + cpu_set(17 + 2 * i, core=i, efficiency=1)
        sets = sysinfo._parse_cpu_sets(buf)
        self.assertEqual(len(sets), 32)
        classes = sysinfo._classes_from(sets)
        self.assertEqual([c["efficiency"] for c in classes], [1, 0])
        self.assertEqual((classes[0]["cores"], classes[0]["logical"], classes[0]["processors"]), (8, 16, "16-31"))
        self.assertEqual((classes[1]["cores"], classes[1]["logical"], classes[1]["processors"]), (16, 16, "0-15"))

    def test_single_class_and_ranges(self):
        """只有一种效率等级：一组；处理器编号的区间写法（0-15、0-2,7-9，空表为空串）。"""
        buf = b"".join(cpu_set(i, core=i, efficiency=0) for i in range(16))
        classes = sysinfo._classes_from(sysinfo._parse_cpu_sets(buf))
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0]["processors"], "0-15")
        self.assertEqual(sysinfo._range_text([0, 1, 2, 7, 8, 9]), "0-2,7-9")
        self.assertEqual(sysinfo._range_text([3]), "3")
        self.assertEqual(sysinfo._range_text([]), "")

    def test_size_bigger_than_32_skipped_correctly(self):
        """Size 比 32 大的项按它的 Size 往后跳：后面的一项照样读到。"""
        buf = (cpu_set(0, core=0, efficiency=0) + cpu_set(1, core=1, efficiency=0, size=40)
               + cpu_set(2, core=2, efficiency=0))
        self.assertEqual([s["logical"] for s in sysinfo._parse_cpu_sets(buf)], [0, 1, 2])

    def test_other_type_skipped(self):
        """Type 不是 CpuSetInformation（0）的项跳过，按它的 Size 往后跳。"""
        buf = cpu_set(0, core=0, efficiency=0, kind=1) + cpu_set(1, core=1, efficiency=0)
        self.assertEqual([s["logical"] for s in sysinfo._parse_cpu_sets(buf)], [1])

    def test_truncated_buffer(self):
        """截断的缓冲区（最后一项不完整）不抛错，返回已经读到的；空缓冲区返回空表。"""
        buf = cpu_set(0, core=0, efficiency=0) + cpu_set(1, core=1, efficiency=0)[:10]
        self.assertEqual([s["logical"] for s in sysinfo._parse_cpu_sets(buf)], [0])
        self.assertEqual(sysinfo._parse_cpu_sets(b""), [])

    def test_processor_groups(self):
        """逻辑处理器分成两个处理器组（超过 64 个时）：编号是组里的序号、每组都从 0 数起，区间逐组写「组0：…；组1：…」，
        不混成一串；核心数按 (Group, CoreIndex) 算，两组同号的核心不算成一个。"""
        buf = b"".join(cpu_set(i, core=i - i % 2, efficiency=0, group=g) for g in (0, 1) for i in range(64))
        classes = sysinfo._classes_from(sysinfo._parse_cpu_sets(buf))
        self.assertEqual(classes, [{"efficiency": 0, "cores": 64, "logical": 128, "processors": "组0：0-63；组1：0-63"}])


class MachineLinesTest(unittest.TestCase):
    """把 machine_info() 的结果写成中文几行（machine_lines）：大小核、只有一种核心、多于两组、用电池加节电、读不到。"""

    def test_big_little(self):
        """大小核：两组写「性能核 …，能效核 …」；内存、电源（插着电源、平衡计划下最佳性能）、运行优先级、显示器、显卡照写。"""
        info = {
            "cpu": {"name": "Intel(R) Core(TM) i5-13600K", "cores": 14, "logical": 20,
                    "classes": [{"efficiency": 1, "cores": 6, "logical": 12, "processors": "8-19"},
                                {"efficiency": 0, "cores": 8, "logical": 8, "processors": "0-7"}]},
            "memory": {"total_gb": 31.7, "available_gb": 12.3},
            "power": {"ac": True, "battery_percent": None, "saver": False, "mode": "best_performance",
                      "plan": "balanced"},
            "displays": [{"width": 2560, "height": 1440, "scale": 125, "primary": True},
                         {"width": 1920, "height": 1080, "scale": 100, "primary": False}],
            "gpus": ["NVIDIA GeForce RTX 3060", "Intel(R) UHD Graphics 770"],
            "process": {"priority": "normal", "performance": "high"},
        }
        lines = sysinfo.machine_lines(info)
        for line in ("处理器：Intel(R) Core(TM) i5-13600K，14 核 20 线程（性能核 6 核 12 线程，能效核 8 核 8 线程）",
                     "内存：共 31.7 GB，可用 12.3 GB", "电源：插着电源；电源计划：平衡；电源模式：最佳性能",
                     "运行优先级：正常；高性能声明：已声明", "显示器：2560×1440（125%，主屏）；1920×1080（100%）",
                     "显卡：NVIDIA GeForce RTX 3060；Intel(R) UHD Graphics 770"):
            with self.subTest(line=line):
                self.assertIn(line, lines)

    def test_single_class_battery_and_missing(self):
        """只有一种核心写「只有一种核心」；用电池加节电；优先级「低于正常」、省电写「节能」；读不到的各项写「……：读不到」，
        电源计划读不到也写出来。"""
        info = {"cpu": {"name": "AMD Ryzen 5 5600 6-Core Processor", "cores": 6, "logical": 12,
                        "classes": [{"efficiency": 0, "cores": 6, "logical": 12, "processors": "0-11"}]},
                "memory": None, "displays": None, "gpus": None,
                "power": {"ac": False, "battery_percent": 54, "saver": True, "mode": "balanced"},
                "process": {"priority": "below_normal", "performance": "eco"}}
        lines = sysinfo.machine_lines(info)
        for line in ("处理器：AMD Ryzen 5 5600 6-Core Processor，6 核 12 线程（只有一种核心）",
                     "电源：用电池（电量 54%，节电模式开着）；电源计划：读不到；电源模式：平衡",
                     "运行优先级：低于正常；高性能声明：节能", "内存：读不到", "显示器：读不到", "显卡：读不到"):
            with self.subTest(line=line):
                self.assertIn(line, lines)

    def test_many_classes_and_unknown_mode(self):
        """多于两组逐组写「效率等级 N：…」；认不出的电源模式照写 GUID；认不出的优先级照写 0x…；空表也算读不到。"""
        info = {"cpu": {"name": "X", "cores": 3, "logical": 3,
                        "classes": [{"efficiency": 2, "cores": 1, "logical": 1, "processors": "0"},
                                    {"efficiency": 1, "cores": 1, "logical": 1, "processors": "1"},
                                    {"efficiency": 0, "cores": 1, "logical": 1, "processors": "2"}]},
                "memory": {"total_gb": 1.0, "available_gb": 0.5},
                "power": {"ac": None, "battery_percent": None, "saver": False, "mode": "some-guid"},
                "displays": [], "gpus": [], "process": {"priority": "0x200", "performance": "auto"}}
        lines = sysinfo.machine_lines(info)
        for line in ("处理器：X，3 核 3 线程（效率等级 2：1 核 1 线程，效率等级 1：1 核 1 线程，效率等级 0：1 核 1 线程）",
                     "电源：状态未知；电源计划：读不到；电源模式：some-guid",
                     "运行优先级：0x200；高性能声明：未声明（由系统决定）", "显示器：读不到", "显卡：读不到"):
            with self.subTest(line=line):
                self.assertIn(line, lines)

    def test_power_plans(self):
        """电源计划：系统自带的四个写「平衡」「高性能」「节能」「卓越性能」，别的写「其他计划」；计划不是「平衡」、叠加方案
        又是全 0（系统在这些计划下不叠加电源模式）时，电源模式写「不适用（由电源计划决定）」，不写成「平衡」；计划是
        「平衡」时照对照表写。"""
        cases = (("balanced", "平衡", "平衡"), ("high_performance", "高性能", "不适用（由电源计划决定）"),
                 ("power_saver", "节能", "不适用（由电源计划决定）"),
                 ("ultimate_performance", "卓越性能", "不适用（由电源计划决定）"),
                 ("other", "其他计划", "不适用（由电源计划决定）"))
        for plan, plan_text, mode_text in cases:
            with self.subTest(plan=plan):
                lines = sysinfo.machine_lines({"power": {"ac": True, "mode": "balanced", "plan": plan}})
                self.assertIn(f"电源：插着电源；电源计划：{plan_text}；电源模式：{mode_text}", lines)

    def test_plan_mode_edge_cases(self):
        """计划不是「平衡」而系统报了生效的叠加方案：照写那个电源模式（它就是正在起作用的）；计划不是「平衡」、电源模式读
        不到：「不适用」；计划是「平衡」、电源模式读不到：「读不到」。"""
        cases = (({"plan": "other", "mode": "best_performance"}, "电源计划：其他计划；电源模式：最佳性能"),
                 ({"plan": "high_performance", "mode": None}, "电源计划：高性能；电源模式：不适用（由电源计划决定）"),
                 ({"plan": "balanced", "mode": None}, "电源计划：平衡；电源模式：读不到"))
        for power, text in cases:
            with self.subTest(**power):
                self.assertIn(f"电源：插着电源；{text}", sysinfo.machine_lines({"power": dict(power, ac=True)}))

    def test_sub_items_unreadable(self):
        """一行里的小项读不到也写出来：型号读不到、核心数读得到时写「型号读不到」，核心数照写；高性能声明读不到写
        「读不到」。"""
        lines = sysinfo.machine_lines({"cpu": {"name": None, "cores": 8, "logical": 16,
                                               "classes": [{"efficiency": 0, "cores": 8, "logical": 16,
                                                            "processors": "0-15"}]},
                                       "process": {"priority": "normal", "performance": None}})
        self.assertIn("处理器：型号读不到，8 核 16 线程（只有一种核心）", lines)
        self.assertIn("运行优先级：正常；高性能声明：读不到", lines)
        self.assertIn("处理器：读不到", sysinfo.machine_lines({"cpu": {"name": None, "cores": None, "logical": None}}))

    def test_all_missing(self):
        """什么都没读到：六行都在，各写「读不到」，不省略任何一行。"""
        self.assertEqual(sysinfo.machine_lines({}),
                         ["处理器：读不到", "内存：读不到", "电源：读不到", "运行优先级：读不到",
                          "显示器：读不到", "显卡：读不到"])

    def test_display_not_per_monitor(self):
        """没能按每块屏幕的缩放读的屏幕（virtualized）：写「缩放读不到」，另写宽高可能是换算后的；只是缩放读不到的只写
        「缩放读不到」。"""
        lines = sysinfo.machine_lines({"displays": [
            {"width": 2560, "height": 1440, "scale": None, "primary": True, "virtualized": True},
            {"width": 1920, "height": 1080, "scale": None, "primary": False}]})
        self.assertIn("显示器：2560×1440（缩放读不到，分辨率可能是按缩放换算后的，主屏）；1920×1080（缩放读不到）", lines)


def guid(text: str) -> sysinfo.GUID:
    """GUID 字符串拼成 sysinfo.GUID 结构（同 Windows 头文件：Data1、Data2、Data3、Data4[8]）。"""
    value = uuid.UUID(text)
    return sysinfo.GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))


class FakePower:
    """假的 kernel32（GetSystemPowerStatus、LocalFree）兼 powrprof（PowerGetEffectiveOverlayScheme、PowerGetActiveScheme）。

    plan、overlay 是两个函数交回的 GUID 字符串；plan_result 不是 0 时 PowerGetActiveScheme 算失败（返回这个错误代码）；
    overlay 为 None 时没有 PowerGetEffectiveOverlayScheme（老系统）。freed 是 LocalFree 释放了几次。"""

    ZERO = "00000000-0000-0000-0000-000000000000"

    def __init__(self, plan: str, overlay: str | None = ZERO, plan_result: int = 0):
        self.plan, self.overlay, self.plan_result = plan, overlay, plan_result
        self.freed = 0
        self.held = None  # PowerGetActiveScheme 交出去的 GUID（系统里由它分配、调用方用 LocalFree 释放）
        if overlay is None:
            self.PowerGetEffectiveOverlayScheme = None

    def GetSystemPowerStatus(self, pointer):
        pointer._obj.ACLineStatus, pointer._obj.BatteryFlag = 1, 128  # 插着电源、没有电池
        return 1

    def PowerGetEffectiveOverlayScheme(self, pointer):
        source = guid(self.overlay)
        ctypes.memmove(ctypes.addressof(pointer._obj), ctypes.addressof(source), ctypes.sizeof(source))
        return 0

    def PowerGetActiveScheme(self, root, pointer):
        if self.plan_result:
            return self.plan_result
        self.held = guid(self.plan)
        pointer._obj.contents = self.held
        return 0

    def LocalFree(self, handle):
        self.freed += 1


class PowerInfoTest(unittest.TestCase):
    """power_info：电源计划只写名字（系统自带的四个，别的写 other），不写原始 GUID，拿到的指针照样释放；电源模式按对照表
    换成名字，老系统没有那个函数时为 None。系统调用换成假的（FakePower）。"""

    PLANS = (("381b4222-f694-41f0-9685-ff5bb260df2e", "balanced"),
             ("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "high_performance"),
             ("a1841308-3541-4fab-bc81-f71556f20b4a", "power_saver"),
             ("e9a42b02-d5df-448d-aa00-03f14749eb61", "ultimate_performance"),
             ("5fb4e2a1-0d34-4c6b-9b0e-7e1f3c2a9d48", "other"))  # 最后一个像自己建的计划：GUID 是随机生成的

    def use(self, fake: FakePower) -> FakePower:
        for name in ("_kernel32", "_powrprof"):
            patcher = mock.patch.object(sysinfo, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def test_plans(self):
        """四个自带计划写各自的名字，别的写 other；结果里没有原始 GUID；PowerGetActiveScheme 交出的指针 LocalFree 了一次。"""
        for text, want in self.PLANS:
            with self.subTest(plan=want):
                fake = self.use(FakePower(text))
                info = sysinfo.power_info()
                self.assertEqual(info["plan"], want)
                self.assertNotIn(text, repr(info))
                self.assertEqual(fake.freed, 1)
                self.assertEqual((info["ac"], info["battery_percent"]), (True, None))

    def test_plan_unreadable(self):
        """PowerGetActiveScheme 失败（返回错误代码）：plan 为 None，没拿到指针也就不释放。"""
        fake = self.use(FakePower(self.PLANS[0][0], plan_result=2))
        self.assertIsNone(sysinfo.power_info()["plan"])
        self.assertEqual(fake.freed, 0)

    def test_modes(self):
        """叠加方案换成名字：最佳性能、更好的性能、平衡（全 0）、最佳能效，认不出的照写 GUID；老系统没有
        PowerGetEffectiveOverlayScheme 时为 None。"""
        unknown = "0f0e0d0c-0b0a-0908-0706-050403020100"
        cases = (("ded574b5-45a0-4f42-8737-46345c09c238", "best_performance"),
                 ("3af9b8d9-7c97-431d-ad78-34a8bfea439f", "better_performance"), (FakePower.ZERO, "balanced"),
                 ("961cc777-2547-4f9d-8174-7d86181b8a7a", "best_efficiency"), (unknown, unknown), (None, None))
        for overlay, want in cases:
            with self.subTest(overlay=overlay):
                self.use(FakePower(self.PLANS[0][0], overlay=overlay))
                self.assertEqual(sysinfo.power_info()["mode"], want)


class FakeScreens:
    """假的 user32（读屏幕的几个函数）兼 shcore（GetDpiForMonitor）：monitors 是 [(句柄, 宽, 高, 主屏, DPI)]，宽高是物理
    像素。本线程不是按每块屏幕的缩放时，像系统那样把宽高按 96 ÷ DPI 换算、DPI 一律报 96。

    switch：SetThreadDpiAwarenessContext 切得成（"ok"）、返回 NULL（"fail"），或者没有这个函数（None）；awareness：本线程
    原来的 DPI 感知（0 不感知、1 按系统缩放、2 按每块屏幕的缩放）；query=False 时没有查 DPI 感知的两个函数；error：
    EnumDisplayMonitors 抛出它。switches 记下每次切换传进去的值，enum_awareness 是读屏幕时本线程的 DPI 感知。"""

    ORIGINAL = 0x6010  # 本线程原来的 DPI 感知（系统给的句柄一类的值）
    PER_MONITOR = 0x22  # 切到按每块屏幕的缩放以后本线程的值

    def __init__(self, monitors, switch="ok", awareness=0, query=True, error: Exception | None = None):
        self.monitors = {m[0]: m for m in monitors}
        self.order = [m[0] for m in monitors]
        self.switch, self.awareness, self.error = switch, awareness, error
        self.context = self.ORIGINAL
        self.switches: list = []
        self.enum_awareness = None
        self.dpi_calls = 0
        if switch is None:
            self.SetThreadDpiAwarenessContext = None
        if not query:
            self.GetThreadDpiAwarenessContext = self.GetAwarenessFromDpiAwarenessContext = None

    def SetThreadDpiAwarenessContext(self, value):
        self.switches.append(value)
        if self.switch == "fail":
            return None
        old = self.context
        self.context = self.PER_MONITOR if value == sysinfo.DPI_CONTEXT_PER_MONITOR_V2 else value
        return old

    def GetThreadDpiAwarenessContext(self):
        return self.context

    def GetAwarenessFromDpiAwarenessContext(self, value):
        return 2 if value == self.PER_MONITOR else self.awareness

    def per_monitor(self) -> bool:
        return self.context == self.PER_MONITOR or self.awareness == 2

    def EnumDisplayMonitors(self, hdc, clip, callback, data):
        self.enum_awareness = 2 if self.per_monitor() else self.awareness
        if self.error is not None:
            raise self.error
        for handle in self.order:
            callback(handle, None, None, 0)
        return 1

    def GetMonitorInfoW(self, handle, pointer):
        _, width, height, primary, dpi = self.monitors[handle]
        if not self.per_monitor():
            width, height = round(width * 96 / dpi), round(height * 96 / dpi)
        info = pointer._obj
        info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom = 0, 0, width, height
        info.dwFlags = sysinfo.MONITORINFOF_PRIMARY if primary else 0
        return 1

    def GetDpiForMonitor(self, handle, kind, dpi_x, dpi_y):
        self.dpi_calls += 1
        dpi_x._obj.value = dpi_y._obj.value = self.monitors[handle][4] if self.per_monitor() else 96
        return 0


class DisplaysTest(unittest.TestCase):
    """displays：读屏幕时本线程临时切到按每块屏幕的缩放、读完切回（出错也切回）；切不成、本线程也不是按每块屏幕的缩放时
    照旧读，不写缩放，标明 virtualized。系统调用换成假的（FakeScreens）。"""

    MONITORS = [(1, 1920, 1080, False, 96), (2, 3840, 2160, True, 144)]
    EXACT = [{"width": 3840, "height": 2160, "scale": 150, "primary": True},
             {"width": 1920, "height": 1080, "scale": 100, "primary": False}]

    def use(self, fake: FakeScreens) -> FakeScreens:
        for name in ("_user32", "_shcore"):
            patcher = mock.patch.object(sysinfo, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def test_switches_and_restores(self):
        """本线程原来不感知 DPI（「替代高 DPI 缩放行为」被改过时就是这样）：先切到按每块屏幕的缩放再读，读到物理像素和
        实际缩放（150% 的 3840×2160 不再读成 2560×1440、100%），读完切回原来的值；主屏排第一。"""
        fake = self.use(FakeScreens(self.MONITORS))
        self.assertEqual(sysinfo.displays(), self.EXACT)
        self.assertEqual(fake.switches, [sysinfo.DPI_CONTEXT_PER_MONITOR_V2, FakeScreens.ORIGINAL])
        self.assertEqual(fake.enum_awareness, 2)
        self.assertEqual(fake.context, FakeScreens.ORIGINAL)

    def test_restores_on_error(self):
        """读屏幕时出错：返回 None，照样切回原来的值。"""
        fake = self.use(FakeScreens(self.MONITORS, error=OSError("读不了（测试）")))
        self.assertIsNone(sysinfo.displays())
        self.assertEqual(fake.switches, [sysinfo.DPI_CONTEXT_PER_MONITOR_V2, FakeScreens.ORIGINAL])
        self.assertEqual(fake.context, FakeScreens.ORIGINAL)

    def test_cannot_switch(self):
        """切不成（返回 NULL）、老系统没有切换的函数（连查 DPI 感知的两个函数也没有）：照旧读，宽高是换算后的值，不写缩放
        （不问 GetDpiForMonitor），每块都标 virtualized；没切成时不调切回。"""
        virtualized = [{"width": 2560, "height": 1440, "scale": None, "primary": True, "virtualized": True},
                       {"width": 1920, "height": 1080, "scale": None, "primary": False, "virtualized": True}]
        for kwargs, switches in (({"switch": "fail"}, [sysinfo.DPI_CONTEXT_PER_MONITOR_V2]),
                                 ({"switch": None}, []), ({"switch": None, "query": False}, [])):
            with self.subTest(**kwargs):
                fake = self.use(FakeScreens(self.MONITORS, **kwargs))
                self.assertEqual(sysinfo.displays(), virtualized)
                self.assertEqual(fake.switches, switches)
                self.assertEqual(fake.dpi_calls, 0)

    def test_already_per_monitor(self):
        """切不成，但本线程本来就是按每块屏幕的缩放（悬浮窗进程平时就是这样）：读到的就是物理像素和实际缩放，不标
        virtualized。"""
        self.use(FakeScreens(self.MONITORS, switch="fail", awareness=2))
        self.assertEqual(sysinfo.displays(), self.EXACT)

    def test_no_shcore(self):
        """切成了，但读不到实际缩放（老系统没有 shcore）：宽高照写，缩放为 None（不写成 100%），不标 virtualized。"""
        self.use(FakeScreens(self.MONITORS))
        with mock.patch.object(sysinfo, "_shcore", None):
            self.assertEqual(sysinfo.displays(), [dict(item, scale=None) for item in self.EXACT])


@unittest.skipUnless(sys.platform == "win32", "只在 Windows 上真调一次")
class RealCallTest(unittest.TestCase):
    def test_real_declare(self):
        """这台电脑上真的声明一次（只影响跑测试的这个进程）：设上了，读回来是 high；优先级是登记的几个名字之一。"""
        self.assertIs(sysinfo.declare_high_performance(), True)
        self.assertEqual(sysinfo.performance_state(), "high")
        self.assertIn(sysinfo.priority_class(), sysinfo.PRIORITY_NAMES.values())


@unittest.skipUnless(sys.platform == "win32", "只在 Windows 上真调一次")
class RealMachineInfoTest(unittest.TestCase):
    def test_types_and_fast(self):
        """这台电脑上真调一次 machine_info：各项类型对（cpu 的 logical 是正整数、memory 的 total_gb 大于 0 等），
        一次调用在 1 秒内返回。"""
        start = time.perf_counter()
        info = sysinfo.machine_info()
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertIsInstance(info["cpu"], dict)
        self.assertIsInstance(info["cpu"]["logical"], int)
        self.assertGreater(info["cpu"]["logical"], 0)
        if info["cpu"]["cores"] is not None:
            self.assertGreaterEqual(info["cpu"]["cores"], 1)
        self.assertIsInstance(info["memory"], dict)
        self.assertGreater(info["memory"]["total_gb"], 0)
        self.assertGreaterEqual(info["memory"]["available_gb"], 0)
        self.assertIsInstance(info["power"], dict)
        self.assertIn(info["power"]["ac"], (True, False, None))
        self.assertIn(info["power"]["plan"], (*sysinfo.POWER_PLANS.values(), sysinfo.POWER_PLAN_OTHER, None))
        self.assertIsInstance(info["process"], dict)
        self.assertIn(info["process"]["priority"], sysinfo.PRIORITY_NAMES.values())
        self.assertEqual(len(sysinfo.machine_lines(info)), 6)


class DEVMODEW(ctypes.Structure):
    """EnumDisplaySettingsW 写回的显示模式（wingdi.h 的 DEVMODEW，共 220 字节）：只用到 dmSize 和宽高，别的字段按头文件
    里的位置补齐。"""
    _fields_ = [("_head", ctypes.c_byte * 68), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("_middle", ctypes.c_byte * 100), ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("_tail", ctypes.c_byte * 40)]


@unittest.skipUnless(sys.platform == "win32", "只在 Windows 上真调一次")
class RealDisplaysTest(unittest.TestCase):
    ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
    UNAWARE = -1  # DPI_AWARENESS_CONTEXT_UNAWARE

    def test_physical_pixels_when_thread_unaware(self):
        """本线程先切到不感知 DPI（和「替代高 DPI 缩放行为」被改过的进程一样），再真调 displays：每块屏幕的宽高是物理像素
        （和 EnumDisplaySettingsW 当前显示模式的宽高相同，它不随调用线程的 DPI 感知变），缩放和「物理宽度 ÷ 不感知 DPI 时
        系统报的宽度」相同（取整，差 1 以内），不标 virtualized；读完本线程照旧不感知 DPI。"""
        self.assertEqual(ctypes.sizeof(DEVMODEW), 220)
        user32 = ctypes.WinDLL("user32", use_last_error=True)  # 测试自己的一份声明，不碰 sysinfo 的
        try:
            switch = user32.SetThreadDpiAwarenessContext
            get_context = user32.GetThreadDpiAwarenessContext
            get_awareness = user32.GetAwarenessFromDpiAwarenessContext
        except AttributeError:
            self.skipTest("这个系统没有 SetThreadDpiAwarenessContext")
        switch.argtypes, switch.restype = [ctypes.c_void_p], ctypes.c_void_p
        get_context.argtypes, get_context.restype = [], ctypes.c_void_p
        get_awareness.argtypes, get_awareness.restype = [ctypes.c_void_p], ctypes.c_int
        user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, sysinfo.MONITORENUMPROC, wintypes.LPARAM]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(sysinfo.MONITORINFOEXW)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
        user32.EnumDisplaySettingsW.restype = wintypes.BOOL
        seen = []  # 不感知 DPI 时系统报的 (设备名, 宽, 主屏)

        def collect(hmonitor, _hdc, _rect, _lparam):
            info = sysinfo.MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                seen.append((info.szDevice, info.rcMonitor.right - info.rcMonitor.left,
                             bool(info.dwFlags & sysinfo.MONITORINFOF_PRIMARY)))
            return True

        previous = switch(self.UNAWARE)
        self.assertIsNotNone(previous)
        try:
            self.assertEqual(get_awareness(get_context()), 0)
            callback = sysinfo.MONITORENUMPROC(collect)
            user32.EnumDisplayMonitors(None, None, callback, 0)
            got = sysinfo.displays()
            self.assertEqual(get_awareness(get_context()), 0)  # displays 读完切回了原来的（不感知 DPI）
        finally:
            switch(previous)
        expected = []
        for device, logical_width, primary in seen:
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(mode)
            self.assertTrue(user32.EnumDisplaySettingsW(device, self.ENUM_CURRENT_SETTINGS, ctypes.byref(mode)), device)
            expected.append((mode.dmPelsWidth, mode.dmPelsHeight, primary, mode.dmPelsWidth / logical_width * 100))
        expected.sort(key=lambda row: not row[2])  # 同 displays：主屏在前，其余照枚举的先后
        self.assertTrue(got)
        self.assertEqual([(d["width"], d["height"], d["primary"]) for d in got], [row[:3] for row in expected])
        for item, row in zip(got, expected):
            with self.subTest(item=item):
                self.assertAlmostEqual(item["scale"], row[3], delta=1)
                self.assertNotIn("virtualized", item)


class NotWindowsImportTest(unittest.TestCase):
    def test_import_and_calls(self):
        """不是 Windows（os.name 不是 nt，ctypes 里没有 WinDLL、WINFUNCTYPE）：模块照样导入得了（另载一份，不动已经导入
        的那份），声明高性能返回 False，读配置的几项都是 None，machine_lines 照样写六行「读不到」。"""
        spec = importlib.util.spec_from_file_location("advisor._sysinfo_not_windows", sysinfo.__file__)
        module = importlib.util.module_from_spec(spec)
        saved = {name: getattr(ctypes, name) for name in ("WinDLL", "WINFUNCTYPE") if hasattr(ctypes, name)}
        try:
            for name in saved:
                delattr(ctypes, name)
            with mock.patch.object(os, "name", "posix"):
                spec.loader.exec_module(module)
                self.assertIsNone(module.MONITORENUMPROC)
                self.assertIs(module.declare_high_performance(), False)
                info = module.machine_info()
        finally:
            for name, value in saved.items():
                setattr(ctypes, name, value)
        self.assertEqual(info, {"cpu": None, "memory": None, "power": None, "displays": None, "gpus": None,
                                "process": {"priority": None, "performance": None}})
        self.assertEqual(module.machine_lines(info), ["处理器：读不到", "内存：读不到", "电源：读不到",
                                                      "运行优先级：读不到；高性能声明：读不到", "显示器：读不到",
                                                      "显卡：读不到"])


if __name__ == "__main__":
    unittest.main()
