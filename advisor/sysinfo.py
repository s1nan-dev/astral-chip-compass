"""本进程和这台电脑的运行状况。

- 向 Windows 声明本进程要高性能（declare_high_performance）：调 SetProcessInformation 的 ProcessPowerThrottling，
  把 ControlMask 设成 PROCESS_POWER_THROTTLING_EXECUTION_SPEED、StateMask 设成 0（HighQoS）。窗口不在焦点上、线程
  优先级被调低时，Windows 会降低进程的服务等级，在大小核处理器（英特尔第 12 代起的酷睿等）上把识别放到能效核、按省电
  频率跑；明确声明以后不再这样。
- 读回本进程的省电设置和优先级类别（performance_state、priority_class）：诊断包和测试用。
- 读这台电脑的配置和运行状况（cpu_info、memory_info、power_info、displays、gpus，合起来是 machine_info）：导出诊断时
  写进诊断包，看诊断包就知道用户是什么环境；machine_lines 把结果写成人看得懂的中文几行。全部不用 WMI、不起子进程、
  不写注册表（WMI 在有的电脑上会卡住不返回，见 advisor/diagnostics.py 的 system_text），正常电脑上几十
  毫秒内返回。

Win32 声明集中在模块开头这一处，每个函数各自接住自己的错误：查不了、设不上都不影响识别和界面。各处只查询，不设系统
状态、不写注册表（读屏幕时只把调用的这个线程临时切到按每块屏幕的缩放，读完切回，见 displays）；不放电脑名、账户名、
序列号这些能认出人的东西。

Win32 函数用本模块自己的一份 WinDLL（kernel32、user32、shcore、powrprof）声明，不影响别处的 ctypes。非 Windows 上
什么都不做，读配置的几个函数返回 None。
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import struct
import traceback
from ctypes import wintypes

from .errors import log_error

# PROCESS_POWER_THROTTLING_STATE 的三个 ULONG（Version、ControlMask、StateMask），共 12 字节；信息类取值 4
# （ProcessPowerThrottling 在 PROCESS_INFORMATION_CLASS 里的第 4 项，微软文档「SetProcessInformation function」
# 的 PROCESS_INFORMATION_CLASS enumeration）。控制位 PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 1。
# ControlMask 带 1 而 StateMask 不带 1 = 明确要高性能（HighQoS）；两个都带 1 = 明确要省电（EcoQoS）。
_POWER_THROTTLING_VERSION = 1  # PROCESS_POWER_THROTTLING_CURRENT_VERSION
POWER_THROTTLING_EXECUTION_SPEED = 0x1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
POWER_THROTTLING = 4  # ProcessPowerThrottling

# 优先级类别（GetPriorityClass 的返回值）写成人看得懂的名字
PRIORITY_NAMES = {
    0x40: "idle",
    0x4000: "below_normal",
    0x20: "normal",
    0x8000: "above_normal",
    0x80: "high",
    0x100: "realtime",
}
# 优先级类别和省电设置写成人看得懂的中文（machine_lines 用）
PRIORITY_LABELS = {"idle": "空闲", "below_normal": "低于正常", "normal": "正常", "above_normal": "高于正常",
                   "high": "高", "realtime": "实时"}
PERFORMANCE_LABELS = {"high": "已声明", "auto": "未声明（由系统决定）", "eco": "节能"}

# 注册表里处理器名称的位置（winreg 只读，不用 WMI）：ProcessorNameString
CPU_NAME_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
CPU_NAME_VALUE = "ProcessorNameString"

# SYSTEM_CPU_SET_INFORMATION（winnt.h）里 Type 的取值：CPU_SET_INFORMATION_TYPE 只有 CpuSetInformation = 0
CPU_SET_INFORMATION = 0
# CpuSet 部分各字段的偏移（字节）：Size 0、Type 4、Id 8（DWORD）、Group 12（WORD）、LogicalProcessorIndex 14、
# CoreIndex 15、LastLevelCacheIndex 16、NumaNodeIndex 17、EfficiencyClass 18（各 1 字节）。按每一项开头的 Size
# 往后跳，Type 不是 CpuSetInformation 的跳过（微软文档「SYSTEM_CPU_SET_INFORMATION structure」）。
# _CPU_SET_FIELDS 从第 8 字节起按上面的顺序读 Id 到 EfficiencyClass（LastLevelCacheIndex、NumaNodeIndex 读了不用）
_CPU_SET_FIELDS = struct.Struct("<IHBBBBB")
_CPU_SET_MIN_BYTES = 8 + _CPU_SET_FIELDS.size  # 认得出这一项、能读到 EfficiencyClass 至少要这么多字节

# SYSTEM_POWER_STATUS（winbase.h）里各字段的取值
_AC_ONLINE, _AC_OFFLINE, _UNKNOWN_BYTE = 1, 0, 255
_BATTERY_FLAG_NO_BATTERY = 128
_BATTERY_SAVER_ON = 1

# 电源模式（Windows 设置里的「电源模式」，系统里叫叠加方案）：PowerGetEffectiveOverlayScheme 拿到当前生效的那个的
# GUID，换成名字。各 GUID 是系统自己的定义（注册表 HKLM\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes 下
# 同名各项的 FriendlyName：Max Performance Overlay、High Performance Overlay、Better Battery-life Overlay）；全 0 是
# 不叠加，即「平衡」。「平衡」以外的电源计划下系统不叠加电源模式（设置里选过别的也一样返回全 0），见 _power_mode_text。
POWER_MODES = {
    "ded574b5-45a0-4f42-8737-46345c09c238": "best_performance",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "better_performance",
    "00000000-0000-0000-0000-000000000000": "balanced",
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "best_efficiency",
}
POWER_MODE_LABELS = {"best_performance": "最佳性能", "better_performance": "更好的性能",
                     "balanced": "平衡", "best_efficiency": "最佳能效"}
# 电源计划（PowerGetActiveScheme 拿到的 GUID）：系统自带的四个换成名字（前三个是 winnt.h 的 GUID_TYPICAL_POWER_SAVINGS、
# GUID_MIN_POWER_SAVINGS、GUID_MAX_POWER_SAVINGS），别的一律写 other。诊断包里不写原始 GUID：自己建的计划的 GUID 是
# 每台电脑随机生成的，写进去就能把同一台电脑的几份诊断包对上。
POWER_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "high_performance",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "power_saver",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "ultimate_performance",
}
POWER_PLAN_OTHER = "other"
POWER_PLAN_LABELS = {"balanced": "平衡", "high_performance": "高性能", "power_saver": "节能",
                     "ultimate_performance": "卓越性能", POWER_PLAN_OTHER: "其他计划"}

# EnumDisplayDevicesW 的 StateFlags 里表示镜像驱动的位（例如远程会话用的伪显卡）：跳过
DISPLAY_DEVICE_MIRRORING = 0x8
# GetMonitorInfoW 的 dwFlags 里表示主屏的位
MONITORINFOF_PRIMARY = 0x1
# GetDpiForMonitor 的 MONITOR_DPI_TYPE：只取 MDT_EFFECTIVE_DPI（当前实际生效的缩放）
MDT_EFFECTIVE_DPI = 0
# 读屏幕时本线程临时切到的 DPI 感知：DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2（按每块屏幕的缩放，伪句柄 -4）；
# GetAwarenessFromDpiAwarenessContext 返回 DPI_AWARENESS_PER_MONITOR_AWARE（2）时本线程是按每块屏幕的缩放
DPI_CONTEXT_PER_MONITOR_V2 = -4
DPI_AWARENESS_PER_MONITOR = 2


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


class MEMORYSTATUSEX(ctypes.Structure):
    """GlobalMemoryStatusEx 写回的内存状态（sysinfoapi.h）：dwLength 要先设成结构大小。"""
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    """GetSystemPowerStatus 写回的电源状态（winbase.h）：四个字节在前，两个 DWORD 在后。"""
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class MONITORINFOEXW(ctypes.Structure):
    """GetMonitorInfoW 写回的显示器信息（winuser.h）：MONITORINFO 后面接设备名。"""
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", ctypes.c_wchar * 32)]


class DISPLAY_DEVICEW(ctypes.Structure):
    """EnumDisplayDevicesW 写回的显卡信息（wingdi.h）：cb 要先设成结构大小，DeviceString 是描述。"""
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", ctypes.c_wchar * 128),
                ("DeviceKey", ctypes.c_wchar * 128)]


class GUID(ctypes.Structure):
    """电源模式和电源计划用的 GUID（Windows 头文件里的 GUID：Data1、Data2、Data3、Data4[8]）。"""
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


MONITORENUMPROC = None  # EnumDisplayMonitors 的回调类型：WINFUNCTYPE 只有 Windows 上的 ctypes 才有，下面再建
_kernel32 = _user32 = _shcore = _powrprof = None  # 测试换成假的时不碰真的系统设置
if os.name == "nt":
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.SetProcessInformation.restype = wintypes.BOOL
    _kernel32.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.GetProcessInformation.restype = wintypes.BOOL
    _kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    _kernel32.GetPriorityClass.restype = wintypes.DWORD
    # 一次拿全各逻辑处理器的效率等级（大小核分组）；先传 NULL/0 拿要的长度
    _kernel32.GetSystemCpuSetInformation.argtypes = [ctypes.c_void_p, wintypes.ULONG,
                                                     ctypes.POINTER(wintypes.ULONG), wintypes.HANDLE, wintypes.ULONG]
    _kernel32.GetSystemCpuSetInformation.restype = wintypes.BOOL
    _kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _kernel32.GetSystemPowerStatus.argtypes = [ctypes.c_void_p]
    _kernel32.GetSystemPowerStatus.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
    _user32.EnumDisplayMonitors.restype = wintypes.BOOL
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW),
                                            wintypes.DWORD]
    _user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    try:  # 读屏幕时本线程临时按每块屏幕的缩放（Windows 10 1607 起）；老系统上没有：displays 照旧读，标明没按实际缩放读
        _user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]  # DPI_AWARENESS_CONTEXT 是句柄一类的值
        _user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        _user32.GetThreadDpiAwarenessContext.argtypes = []
        _user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        _user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    except AttributeError:
        pass

    try:  # 每块屏幕的实际缩放（Windows 8.1 起）；老系统上没有 shcore
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
        _shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                             ctypes.POINTER(wintypes.UINT)]
        _shcore.GetDpiForMonitor.restype = ctypes.c_long
    except OSError:
        _shcore = None

    try:  # 当前生效的电源模式（Windows 10 起）和电源计划；老系统上没有 powrprof 里的这两个函数
        _powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
    except OSError:
        _powrprof = None
    if _powrprof is not None:
        try:
            _powrprof.PowerGetEffectiveOverlayScheme.argtypes = [ctypes.POINTER(GUID)]
            _powrprof.PowerGetEffectiveOverlayScheme.restype = wintypes.DWORD
        except AttributeError:  # 老系统上没有这个函数：power_info 里按 None 处理
            pass
        _powrprof.PowerGetActiveScheme.argtypes = [wintypes.HKEY, ctypes.POINTER(ctypes.POINTER(GUID))]
        _powrprof.PowerGetActiveScheme.restype = wintypes.DWORD

_high_logged = False  # 声明高性能出过错、已经记进错误记录：之后再出错不再记


def declare_high_performance() -> bool:
    """向 Windows 声明本进程要高性能（HighQoS）：SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling,
    {Version 1, ControlMask EXECUTION_SPEED, StateMask 0})。返回成没成。

    不是 Windows 时什么都不做，返回 False。返回 0（设不上）或调用本身出错（例如老系统没有这个信息类）时只记一次错误
    记录，之后再失败不再记，程序照常运行。"""
    global _high_logged
    if _kernel32 is None:
        return False
    state = PROCESS_POWER_THROTTLING_STATE(_POWER_THROTTLING_VERSION, POWER_THROTTLING_EXECUTION_SPEED, 0)
    try:
        ok = bool(_kernel32.SetProcessInformation(_kernel32.GetCurrentProcess(), POWER_THROTTLING,
                                                  ctypes.byref(state), ctypes.sizeof(state)))
        detail = "" if ok else f"SetProcessInformation 没有设上，错误代码 {ctypes.get_last_error()}"
    except Exception:  # noqa: BLE001  声明不上不影响识别和界面
        ok, detail = False, "SetProcessInformation 出错：\n" + traceback.format_exc()
    if not ok and not _high_logged:
        _high_logged = True
        log_error("声明高性能出错（拾星助手仍在运行）", detail, append=True)
    return ok


def performance_state() -> str | None:
    """读回本进程的省电设置（GetProcessInformation 的同一个结构）：ControlMask 带 EXECUTION_SPEED 而 StateMask 不带
    → "high"；两个都带 → "eco"；ControlMask 不带 → "auto"（交给系统决定）；不是 Windows 或读不到 → None。"""
    if _kernel32 is None:
        return None
    # Version 要先填好：GetProcessInformation 按它核对结构，不填（0）时返回 0（错误代码 87）
    state = PROCESS_POWER_THROTTLING_STATE(_POWER_THROTTLING_VERSION, 0, 0)
    try:
        ok = bool(_kernel32.GetProcessInformation(_kernel32.GetCurrentProcess(), POWER_THROTTLING,
                                                  ctypes.byref(state), ctypes.sizeof(state)))
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None
    if not ok:
        return None
    if not state.ControlMask & POWER_THROTTLING_EXECUTION_SPEED:
        return "auto"
    return "eco" if state.StateMask & POWER_THROTTLING_EXECUTION_SPEED else "high"


def priority_class() -> str | None:
    """本进程的优先级类别（GetPriorityClass）：idle、below_normal、normal、above_normal、high、realtime，别的值写成
    0x…，不是 Windows 或读不到 → None。"""
    if _kernel32 is None:
        return None
    try:
        value = int(_kernel32.GetPriorityClass(_kernel32.GetCurrentProcess()))
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None
    if not value:
        return None
    return PRIORITY_NAMES.get(value, hex(value))


# ---------------------------------------------------------------- 电脑配置（诊断包用）


def cpu_info() -> dict | None:
    """这台电脑的处理器：name（注册表里的型号）、logical（逻辑处理器个数）、cores（物理核心个数）、classes（按效率
    等级分组，见 _classes_from）。不是 Windows 或全都读不到 → None。读不到 CPU Set 时 cores 和 classes 为 None，
    logical 退回 os.cpu_count()。"""
    if os.name != "nt":
        return None
    name, sets = _cpu_name(), _cpu_sets()
    if sets:
        logical = len(sets)
        cores = len({(item["group"], item["core"]) for item in sets})
        classes = _classes_from(sets)
    else:
        logical, cores, classes = os.cpu_count(), None, None
    if name is None and not logical:
        return None
    return {"name": name, "logical": logical, "cores": cores, "classes": classes}


def _cpu_name() -> str | None:
    """注册表里处理器的名称（HKLM 下 CPU_NAME_KEY 的 ProcessorNameString），去掉首尾空白；读不到 → None。只读注册表，
    不用 WMI。"""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CPU_NAME_KEY) as key:
            name = str(winreg.QueryValueEx(key, CPU_NAME_VALUE)[0]).strip()
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None
    return name or None


def _cpu_sets() -> list[dict] | None:
    """调 GetSystemCpuSetInformation 拿全各逻辑处理器的 CPU Set：先传 NULL/0 拿要的长度，再分配、再调。读不到 → None。"""
    if _kernel32 is None:
        return None
    try:
        needed = wintypes.ULONG(0)
        _kernel32.GetSystemCpuSetInformation(None, 0, ctypes.byref(needed), None, 0)
        if not needed.value:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        returned = wintypes.ULONG(0)
        if not _kernel32.GetSystemCpuSetInformation(buf, needed.value, ctypes.byref(returned), None, 0):
            return None
        return _parse_cpu_sets(buf.raw[: returned.value])
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None


def _parse_cpu_sets(buf: bytes) -> list[dict]:
    """解析 GetSystemCpuSetInformation 返回的缓冲区：一项项 CpuSet，取 Id、Group、LogicalProcessorIndex、CoreIndex、
    EfficiencyClass。按每一项开头的 Size 往后跳（结构是变长的，以后可能加长），Type 不是 CpuSetInformation 的跳过，
    缓冲区在这里截断时停下，都不抛错。测试直接喂拼好的字节。"""
    out = []
    offset, total = 0, len(buf)
    while offset + 8 <= total:
        size, kind = struct.unpack_from("<II", buf, offset)
        if size < 8 or offset + size > total:  # 认不出这一项、或缓冲区在这里截断：后面的都不完整
            break
        if kind == CPU_SET_INFORMATION and size >= _CPU_SET_MIN_BYTES:
            item = _CPU_SET_FIELDS.unpack_from(buf, offset + 8)
            out.append({"id": item[0], "group": item[1], "logical": item[2], "core": item[3], "efficiency": item[6]})
        offset += size
    return out


def _classes_from(sets: list[dict]) -> list[dict]:
    """按效率等级（EfficiencyClass）分组：每组 cores 是同一等级里不同的 (Group, CoreIndex) 个数（超线程的几个逻辑
    处理器算一个核心），logical 是逻辑处理器个数，processors 是处理器编号写的区间（见 _processors_text）。按等级从高到低
    排，数字大的等级是性能核。"""
    grouped = len({item["group"] for item in sets}) > 1
    groups: dict[int, list[dict]] = {}
    for item in sets:
        groups.setdefault(item["efficiency"], []).append(item)
    return [{"efficiency": efficiency, "cores": len({(item["group"], item["core"]) for item in items}),
             "logical": len(items), "processors": _processors_text(items, grouped)}
            for efficiency, items in sorted(groups.items(), reverse=True)]


def _processors_text(items: list[dict], grouped: bool) -> str:
    """一组逻辑处理器的编号写成区间（_range_text）。编号（LogicalProcessorIndex）是处理器组里的序号：逻辑处理器超过 64 个、
    分成几个处理器组时每组都从 0 数起，混在一起就分不清，这时逐组写「组0：0-63；组1：0-63」；只有一个组时照写「0-15」。"""
    if not grouped:
        return _range_text(sorted(item["logical"] for item in items))
    by_group: dict[int, list[int]] = {}
    for item in items:
        by_group.setdefault(item["group"], []).append(item["logical"])
    return "；".join(f"组{group}：{_range_text(sorted(indices))}" for group, indices in sorted(by_group.items()))


def _range_text(indices: list[int]) -> str:
    """一串处理器编号写成区间：0-15、0-7,16-23（连续的合成一段，单独一个就写它本身）。"""
    if not indices:
        return ""
    parts = []
    start = prev = indices[0]
    for index in indices[1:]:
        if index == prev + 1:
            prev = index
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = index
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def memory_info() -> dict | None:
    """这台电脑的内存（GlobalMemoryStatusEx）：total_gb、available_gb（GB，保留一位小数）。读不到 → None。"""
    if _kernel32 is None:
        return None
    try:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return {"total_gb": round(status.ullTotalPhys / 2 ** 30, 1),
                "available_gb": round(status.ullAvailPhys / 2 ** 30, 1)}
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None


def power_info() -> dict | None:
    """这台电脑的电源状况：ac（插着电源 True / 用电池 False / 不知道 None）、battery_percent（没有电池或读不到时
    None）、saver（节电模式开着）、mode（当前生效的电源模式，见 _power_mode）、plan（当前电源计划，见 _power_plan）。
    不是 Windows → None；在 Windows 上总是这个字典，哪一项读不到那一项为 None。"""
    if _kernel32 is None:
        return None
    info: dict = {"ac": None, "battery_percent": None, "saver": None}
    try:
        status = SYSTEM_POWER_STATUS()
        if _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            info["ac"] = {_AC_ONLINE: True, _AC_OFFLINE: False}.get(status.ACLineStatus)
            has_battery = not status.BatteryFlag & _BATTERY_FLAG_NO_BATTERY and status.BatteryFlag != _UNKNOWN_BYTE
            if has_battery and status.BatteryLifePercent != _UNKNOWN_BYTE:
                info["battery_percent"] = status.BatteryLifePercent
            info["saver"] = status.SystemStatusFlag == _BATTERY_SAVER_ON
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        pass
    info["mode"] = _power_mode()
    info["plan"] = _power_plan()
    return info


def _power_mode() -> str | None:
    """当前生效的电源模式（PowerGetEffectiveOverlayScheme）：换成 best_performance 这类名字，别的照写 GUID 字符串
    （叠加方案的 GUID 是系统或厂商定的，不是每台电脑随机的）；老系统没有这个函数或调用失败 → None。"""
    get_overlay = getattr(_powrprof, "PowerGetEffectiveOverlayScheme", None)
    if get_overlay is None:
        return None
    try:
        guid = GUID()
        if get_overlay(ctypes.byref(guid)) != 0:
            return None
        text = _guid_text(guid)
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None
    return POWER_MODES.get(text, text)


def _power_plan() -> str | None:
    """当前电源计划（PowerGetActiveScheme）：系统自带的四个写 POWER_PLANS 里的名字，别的写 other，不写原始 GUID；拿到的
    指针用 LocalFree 释放。读不到 → None。"""
    get_scheme = getattr(_powrprof, "PowerGetActiveScheme", None)
    if get_scheme is None or _kernel32 is None:
        return None
    try:
        pointer = ctypes.POINTER(GUID)()
        if get_scheme(None, ctypes.byref(pointer)) != 0 or not pointer:
            return None
        try:
            return POWER_PLANS.get(_guid_text(pointer.contents), POWER_PLAN_OTHER)
        finally:
            _kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None


def _guid_text(guid: GUID) -> str:
    """GUID 写成小写的 8-4-4-4-12 字符串。"""
    tail = bytes(guid.Data4).hex()
    return f"{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-{tail[:4]}-{tail[4:]}"


def displays() -> list | None:
    """这台电脑的显示器（EnumDisplayMonitors + GetMonitorInfoW + GetDpiForMonitor）：每块 {width、height、scale、
    primary}，主屏排第一。读不到 → None。

    读的时候本线程临时按每块屏幕的缩放运行（_per_monitor_dpi），读完切回。不是按每块屏幕的缩放运行的线程（「替代高 DPI
    缩放行为」被改过时整个进程都是这样）读到的是系统按缩放换算过的大小，缩放一律是 100%，例如 150% 缩放的 3840×2160
    读成 2560×1440（100%），而诊断包正要在这种情况下记清真实的屏幕。切不成、本线程也不是按每块屏幕的缩放时照旧读：
    scale 为 None（不写一个可能是假的 100%），另带 virtualized: True（宽高可能是换算后的，不一定是物理像素）。"""
    if _user32 is None:
        return None
    found: list[dict] = []
    exact = False  # 这次是不是按每块屏幕的缩放读的（_per_monitor_dpi 产出）

    def collect(hmonitor, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        try:
            if _user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                item = {"width": info.rcMonitor.right - info.rcMonitor.left,
                        "height": info.rcMonitor.bottom - info.rcMonitor.top,
                        "scale": _monitor_scale(hmonitor) if exact else None,
                        "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY)}
                if not exact:
                    item["virtualized"] = True
                found.append(item)
        except Exception:  # noqa: BLE001  这一块读不到就跳过
            pass
        return True

    callback = MONITORENUMPROC(collect)  # 调用期间要留着引用，别被回收
    try:
        with _per_monitor_dpi() as exact:
            _user32.EnumDisplayMonitors(None, None, callback, 0)
    except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
        return None
    if not found:
        return None
    found.sort(key=lambda d: not d["primary"])  # 主屏在前，其余保持枚举顺序
    return found


@contextlib.contextmanager
def _per_monitor_dpi():
    """本线程临时按每块屏幕的缩放运行：SetThreadDpiAwarenessContext 切到 DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2，
    退出时切回原来的（里面出错也切回）。只动调用它的这一个线程，读完就切回，不影响界面和别的线程。

    产出这时读屏幕是不是按实际缩放：查得到本线程的 DPI 感知时看它是不是按每块屏幕的缩放（切成了，或者本来就是）；
    老系统查不了时看切没切成。"""
    switch = getattr(_user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if switch is not None:
        try:
            previous = switch(DPI_CONTEXT_PER_MONITOR_V2)  # 切不成（系统不认这个值）时返回 NULL，本线程不变
        except Exception:  # noqa: BLE001  切不成就照旧读，结果里标明
            previous = None
    try:
        aware = _thread_per_monitor()
        yield previous is not None if aware is None else aware
    finally:
        if previous is not None:
            try:
                switch(previous)
            except Exception:  # noqa: BLE001  切回不成只影响这个线程以后读到的屏幕坐标，不影响导出
                pass


def _thread_per_monitor() -> bool | None:
    """调用它的线程现在是不是按每块屏幕的缩放：GetAwarenessFromDpiAwarenessContext(GetThreadDpiAwarenessContext()) 为
    DPI_AWARENESS_PER_MONITOR。老系统没有这两个函数、查不了 → None。"""
    get_context = getattr(_user32, "GetThreadDpiAwarenessContext", None)
    get_awareness = getattr(_user32, "GetAwarenessFromDpiAwarenessContext", None)
    if get_context is None or get_awareness is None:
        return None
    try:
        return int(get_awareness(get_context())) == DPI_AWARENESS_PER_MONITOR
    except Exception:  # noqa: BLE001  查不了就按切没切成算
        return None


def _monitor_scale(hmonitor) -> int | None:
    """这块屏幕的实际缩放（GetDpiForMonitor 的 MDT_EFFECTIVE_DPI）：DPI ÷ 96 × 100 取整。要在按每块屏幕的缩放的线程上
    调（displays 先切好），不然一律读到 96。老系统上没有 shcore 或读不到 → None（不写一个可能是假的 100%）。"""
    if _shcore is None:
        return None
    try:
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if _shcore.GetDpiForMonitor(hmonitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
            return None
        return round(dpi_x.value / 96 * 100) or None
    except Exception:  # noqa: BLE001  读不到就不写缩放
        return None


def gpus() -> list | None:
    """这台电脑的显卡（EnumDisplayDevicesW 传 NULL 列显示适配器）：每张卡的描述（DeviceString），跳过镜像驱动，
    同名的只留一个，按出现先后。读不到 → None。

    这个函数按显示输出列，只列得出有显示输出的适配器：混合显卡的笔记本上，不接任何显示输出的独显可能不在列表里。远程
    桌面、串流、模拟器装的虚拟显示适配器也会列出来（排查截屏问题用得上，不去掉）。"""
    if _user32 is None:
        return None
    names: list[str] = []
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        try:
            if not _user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
        except Exception:  # noqa: BLE001  读不到就不报，按 None 处理
            break
        index += 1
        if device.StateFlags & DISPLAY_DEVICE_MIRRORING:
            continue
        name = device.DeviceString.strip()
        if name and name not in names:
            names.append(name)
    return names or None


def machine_info() -> dict:
    """这台电脑的配置和本进程的运行状况，合成一个字典：cpu、memory、power、displays、gpus、process；取不到的那一项
    为 None，process 是本进程的 {priority, performance}。不放电脑名、账户名、序列号这些能认出人的东西。"""
    return {
        "cpu": cpu_info(),
        "memory": memory_info(),
        "power": power_info(),
        "displays": displays(),
        "gpus": gpus(),
        "process": {"priority": priority_class(), "performance": performance_state()},
    }


def machine_lines(info: dict) -> list[str]:
    """把 machine_info() 的结果写成给人看的中文几行（version.txt 用）：处理器、内存、电源、运行优先级、显示器、显卡。
    哪一项读不到就写「……：读不到」，不省略这一行；一行里的小项（电源计划、电源模式、高性能声明）读不到也照样写出来。"""
    return [
        _cpu_line(info.get("cpu")),
        _memory_line(info.get("memory")),
        _power_line(info.get("power")),
        _process_line(info.get("process")),
        _displays_line(info.get("displays")),
        _gpus_line(info.get("gpus")),
    ]


def _cpu_line(cpu) -> str:
    """「处理器：型号，N 核 M 线程（大小核分组）」；型号读不到、核心数读得到时型号写「型号读不到」，核心数照写。"""
    if not isinstance(cpu, dict):
        return "处理器：读不到"
    size = _cpu_size_text(cpu.get("cores"), cpu.get("logical"))
    if not cpu.get("name") and not size:
        return "处理器：读不到"
    line = f"处理器：{cpu.get('name') or '型号读不到'}"
    if size:
        line += f"，{size}"
    detail = _classes_text(cpu.get("classes"))
    if detail:
        line += f"（{detail}）"
    return line


def _cpu_size_text(cores, logical) -> str:
    if cores and logical:
        return f"{cores} 核 {logical} 线程"
    if logical:
        return f"{logical} 线程"
    return ""


def _classes_text(classes) -> str:
    """效率等级分组写成一串：两组写「性能核 …，能效核 …」，一组写「只有一种核心」，多于两组逐组写「效率等级 N：…」。"""
    if not classes:
        return ""
    if len(classes) == 1:
        return "只有一种核心"
    if len(classes) == 2:
        high, low = classes
        return f"性能核 {high['cores']} 核 {high['logical']} 线程，能效核 {low['cores']} 核 {low['logical']} 线程"
    return "，".join(f"效率等级 {c['efficiency']}：{c['cores']} 核 {c['logical']} 线程" for c in classes)


def _memory_line(memory) -> str:
    if not isinstance(memory, dict):
        return "内存：读不到"
    return f"内存：共 {memory['total_gb']} GB，可用 {memory['available_gb']} GB"


def _power_line(power) -> str:
    """「电源：插着电源；电源计划：高性能；电源模式：不适用（由电源计划决定）」：电源、电源计划、电源模式三项都写，读不到
    的写「读不到」。"""
    if not isinstance(power, dict):
        return "电源：读不到"
    plan = power.get("plan")
    return (f"电源：{_power_source_text(power)}；电源计划：{POWER_PLAN_LABELS.get(plan, plan) or '读不到'}；"
            f"电源模式：{_power_mode_text(power)}")


def _power_source_text(power) -> str:
    if power.get("ac") is True:
        return "插着电源"
    if power.get("ac") is False:
        notes = []
        if power.get("battery_percent") is not None:
            notes.append(f"电量 {power['battery_percent']}%")
        if power.get("saver"):
            notes.append("节电模式开着")
        return "用电池" + (f"（{'，'.join(notes)}）" if notes else "")
    return "状态未知"


def _power_mode_text(power) -> str:
    """电源模式只在「平衡」电源计划下起作用：计划不是「平衡」、也没有生效的叠加方案（全 0 或读不到）时写「不适用（由
    电源计划决定）」，免得把全 0 当成「平衡」模式；系统报了生效的叠加方案时照写，它就是正在起作用的。别的照对照表写，
    认不出的照写 GUID，读不到写「读不到」。"""
    mode, plan = power.get("mode"), power.get("plan")
    if plan not in (None, "balanced") and mode in (None, "balanced"):
        return "不适用（由电源计划决定）"
    if not mode:
        return "读不到"
    return POWER_MODE_LABELS.get(mode, mode)


def _process_line(process) -> str:
    if not isinstance(process, dict):
        return "运行优先级：读不到"
    priority, performance = process.get("priority"), process.get("performance")
    return (f"运行优先级：{PRIORITY_LABELS.get(priority, priority) or '读不到'}；"
            f"高性能声明：{PERFORMANCE_LABELS.get(performance, performance) or '读不到'}")


def _displays_line(items) -> str:
    if not items:
        return "显示器：读不到"
    return "显示器：" + "；".join(_display_text(item) for item in items)


def _display_text(item) -> str:
    """一块屏幕：「3840×2160（150%，主屏）」；缩放读不到写「缩放读不到」，没能按每块屏幕的缩放读的（virtualized）另写
    一句宽高可能是换算后的。"""
    notes = [f"{item['scale']}%" if item.get("scale") else "缩放读不到"]
    if item.get("virtualized"):
        notes.append("分辨率可能是按缩放换算后的")
    if item.get("primary"):
        notes.append("主屏")
    return f"{item['width']}×{item['height']}（{'，'.join(notes)}）"


def _gpus_line(items) -> str:
    if not items:
        return "显卡：读不到"
    return "显卡：" + "；".join(items)
