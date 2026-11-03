"""识别各步耗时和处理器占用的统计。

程序运行中收集：每帧识别花多久（frame）、每次文字识别花多久（ocr）、各项读数各自花多久（read:名字）、算推荐花多久
（advice）、界面线程的定时器晚到了多少（ui_lag）。识别线程每分钟取走一段（take_period），把这一段各步的耗时和处理器
占用写进识别记录（recog_stats，见 overlay_parts/watcher.py 的 Watcher._log_stats），诊断包的 state.json 里也带最近一条。

只加统计、不改识别的行为和读数：记一笔只是取时间、进锁、往列表里追加；出错就丢掉这一笔，绝不往外抛（识别线程、界面
线程都在记）。实测记一笔约 0.15 微秒，不拖慢识别。

处理器占用用 GetProcessTimes、GetThreadTimes、GetSystemTimes（本模块自己的一份 WinDLL 声明，写法照 advisor/sysinfo.py），
不是 Windows、或调用失败时相应的一项不出现。时钟和 Win32 调用是模块级的（_clock、_kernel32），测试可以换掉，
不依赖真实的处理器负载。主线程的句柄用 OpenThread 打开一次留着（界面线程的占用要一直量它）。
界面线程的定时器晚到多少按不含电脑睡眠时间的时钟算（unbiased_clock：QueryUnbiasedInterruptTime）。
"""
from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from ctypes import wintypes

_clock = time.monotonic  # 取当前时刻（秒）；测试换成假时钟

MAX_SAMPLES = 5000  # 每种 kind 这一段最多留这么多个样本（毫秒）；超了以后只累加次数、总和、最大值，不再存样本

# 线程查询权限（OpenThread 用）：只要查时间，取最小的一种
_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
# FILETIME 的一个单位是 100 纳秒：一秒是 1e7 个
_TICKS_PER_SECOND = 10_000_000

_lock = threading.Lock()  # 全部操作都在这一把锁里
_counts: dict[str, "_Kind"] = {}  # 这一段各 kind 的累计
_since: float | None = None  # 这一段从什么时候算起（第一次记一笔时定下）
_cpu_base: dict | None = None  # 上一次取走时的处理器时间（第一次只记基准，cpu 不出现）
_ui_handle = None  # 主线程的句柄，第一次用时打开一次留着
_ui_handle_tried = False

class _Kind:
    """一种 kind 这一段的累计：次数、总和、最大值，以及（不超过上限时）留存的样本。"""

    __slots__ = ("n", "sum_ms", "max_ms", "samples")

    def __init__(self) -> None:
        self.n = 0
        self.sum_ms = 0.0
        self.max_ms = 0.0
        self.samples: list[float] = []


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentThread.argtypes = []
    _kernel32.GetCurrentThread.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                          ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                          ctypes.POINTER(wintypes.FILETIME)]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetThreadTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    _kernel32.GetThreadTimes.restype = wintypes.BOOL
    _kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    _kernel32.GetSystemTimes.restype = wintypes.BOOL
    try:  # Windows 7 起才有；没有时 unbiased_clock 退回 time.monotonic
        _query_unbiased = _kernel32.QueryUnbiasedInterruptTime
        _query_unbiased.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        _query_unbiased.restype = wintypes.BOOL
    except AttributeError:
        _query_unbiased = None
else:  # 不是 Windows：不声明，取处理器占用时返回 None，unbiased_clock 用 time.monotonic
    _kernel32 = None
    _query_unbiased = None


def unbiased_clock() -> float:
    """不含电脑睡眠、休眠时间的时钟（秒），数界面线程的定时器晚到多少（ui_lag）用。Windows 上是
    QueryUnbiasedInterruptTime（100 纳秒为单位，随系统的时钟中断走，刻度 1～15.6 毫秒）：睡眠、休眠期间不走，醒来以后的
    那一次间隔不会算成界面线程卡住，界面线程真卡住多久就是多久。不是 Windows、或取不到时退回 time.monotonic
    （睡眠的时间算在内）。"""
    if _query_unbiased is not None:
        ticks = ctypes.c_ulonglong()
        try:
            if _query_unbiased(ctypes.byref(ticks)):
                return ticks.value / _TICKS_PER_SECOND
        except Exception:  # noqa: BLE001  取不到就退回单调时钟
            pass
    return time.monotonic()


def _ticks(ft) -> int:
    """一个 FILETIME（100 纳秒为单位）转成整数。"""
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _process_ticks() -> int | None:
    """本进程到现在用掉的处理器时间（100 纳秒；内核加用户）。取不到时为 None。"""
    creation, exit_time, kernel, user = (wintypes.FILETIME(), wintypes.FILETIME(),
                                         wintypes.FILETIME(), wintypes.FILETIME())
    if not _kernel32.GetProcessTimes(_kernel32.GetCurrentProcess(), ctypes.byref(creation),
                                     ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return _ticks(kernel) + _ticks(user)


def _thread_ticks(handle) -> int | None:
    """一个线程到现在用掉的处理器时间（100 纳秒）。handle 为空或取不到时为 None。"""
    if not handle:
        return None
    creation, exit_time, kernel, user = (wintypes.FILETIME(), wintypes.FILETIME(),
                                         wintypes.FILETIME(), wintypes.FILETIME())
    if not _kernel32.GetThreadTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                    ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return _ticks(kernel) + _ticks(user)


def _system_ticks() -> tuple[int, int] | None:
    """整机的（空闲时间, 总时间）累计（100 纳秒）；总时间是内核加用户（Windows 上内核时间里含空闲）。取不到时为 None。"""
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    total = _ticks(kernel) + _ticks(user)
    return (_ticks(idle), total) if total else None


def _ui_thread_handle():
    """主线程（界面线程）的句柄，第一次用时打开一次留着（THREAD_QUERY_LIMITED_INFORMATION）。打不开时为 None。"""
    global _ui_handle, _ui_handle_tried
    if _ui_handle is not None or _ui_handle_tried:
        return _ui_handle
    _ui_handle_tried = True
    try:
        tid = threading.main_thread().native_id
        if tid:
            _ui_handle = _kernel32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, tid) or None
    except Exception:  # noqa: BLE001  打不开时这一项不出现，统计照常
        _ui_handle = None
    return _ui_handle


def _snapshot() -> dict | None:
    """这一刻各处的处理器时间。不是 Windows 时为 None；某一项取不到那一项是 None。"""
    if _kernel32 is None:
        return None
    try:
        return {
            "process": _process_ticks(),
            "recog": _thread_ticks(_kernel32.GetCurrentThread()),
            "ui": _thread_ticks(_ui_thread_handle()),
            "system": _system_ticks(),
        }
    except Exception:  # noqa: BLE001  取不到就不出现，绝不往外抛
        return None


def _percent(base: dict, now: dict, elapsed: float) -> dict:
    """两次快照之间各处的处理器占用（百分比，一位小数）：本进程和两个线程折成「一个核的百分比」（可以超过 100），
    整机忙的百分比是 0～100。这一段的时长（秒）算不出来时为空。"""
    if elapsed <= 0:
        return {}
    out: dict = {}
    span = elapsed * _TICKS_PER_SECOND
    for key in ("process", "recog", "ui"):
        before, after = base.get(key), now.get(key)
        if before is not None and after is not None and after >= before:
            out[key] = round(100.0 * (after - before) / span, 1)
    before, after = base.get("system"), now.get("system")
    if before is not None and after is not None:
        total = after[1] - before[1]
        idle = after[0] - before[0]
        if total > 0:
            out["system"] = round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)
    return out


def _nearest_rank(ordered: list[float], q: float) -> float:
    """最近秩法：样本从小到大排好，取第 ceil(q × 样本数) 个（第 1 个是最小的）；q 为 0.5、样本数是偶数时取中间两个里
    较小的那一个。"""
    m = len(ordered)
    rank = math.ceil(q * m)
    return ordered[min(max(rank, 1), m) - 1]


def record(kind: str, ms: float) -> None:
    """记一笔耗时（毫秒）。识别线程和界面线程都会调用；任何情况下不往外抛异常（出错就丢掉这一笔）。
    先转成数再动累计：转不成时这一笔整个不记，不留下次数加了、样本没存的半截（那样这一段汇总时会出错）。"""
    global _since
    try:
        value = float(ms)
        with _lock:
            if _since is None:
                _since = _clock()
            item = _counts.get(kind)
            if item is None:
                item = _counts[kind] = _Kind()
            item.n += 1
            item.sum_ms += value
            if value > item.max_ms:
                item.max_ms = value
            if len(item.samples) < MAX_SAMPLES:
                item.samples.append(value)
    except Exception:  # noqa: BLE001  统计本身不能影响识别
        pass


def take_period() -> dict:
    """取走这一段的汇总并清零，返回这一段各步的耗时和处理器占用。

    返回：period_s（这一段多长，秒，一位小数）；每种 kind 一项 {"n", "p50", "p95", "max", "sum"}（毫秒，一位小数；
    分位数按存下的样本用最近秩法算）；kind 带冒号的（read:xxx）归到 reads 下面（{名字: 那一项}）；cpu（这一段的处理器
    占用：process 本进程、recog 调用本函数的那个线程、ui 主线程，都折成「一个核的百分比」，system 整机忙的百分比）。
    第一次调用只记处理器时间的基准，cpu 不出现；不是 Windows、或调用失败时 cpu 不出现或缺那一项。没有样本的 kind 不出现。
    取走后这一段清零，下一次从零开始累加。汇总出错时返回空字典、只丢这一段：照样清零、换基准（取不到时下一次只记基准），
    不会一直卡在同一笔坏数据上。
    """
    global _since, _cpu_base
    with _lock:
        now = snapshot = None
        try:
            now = _clock()
            elapsed = 0.0 if _since is None else max(0.0, now - _since)
            out: dict = {"period_s": round(elapsed, 1)}
            reads: dict = {}
            for kind in sorted(_counts):
                item = _counts[kind]
                ordered = sorted(item.samples)
                entry = {
                    "n": item.n,
                    "p50": round(_nearest_rank(ordered, 0.50), 1),
                    "p95": round(_nearest_rank(ordered, 0.95), 1),
                    "max": round(item.max_ms, 1),
                    "sum": round(item.sum_ms, 1),
                }
                if ":" in kind:
                    reads[kind.split(":", 1)[1]] = entry
                else:
                    out[kind] = entry
            if reads:
                out["reads"] = reads
            snapshot = _snapshot()
            if _cpu_base is not None and snapshot is not None:
                cpu = _percent(_cpu_base, snapshot, elapsed)
                if cpu:
                    out["cpu"] = cpu
            return out
        except Exception:  # noqa: BLE001  取统计出错时丢掉这一段，绝不往外抛
            return {}
        finally:
            _cpu_base = snapshot
            _counts.clear()
            _since = now
