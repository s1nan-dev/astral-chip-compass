"""本进程的处理器占用：运行中每分钟记一条，同时存进程序目录 logs 下的 usage.jsonl，导出诊断时现量各线程的占用和调用栈。

为什么要单独记：原来的占用统计（advisor/perfstats.py）只在取到游戏画面、识别线程跑起来以后才记，玩家没开游戏、手动模式
空闲、识别暂停时一条都没有。这时界面线程仍然在跑定时器、跟随窗口、刷新卡片，诊断包里却查不到这段时间的占用。

每分钟一条另存在程序目录的 logs\\usage.jsonl（FILE_NAME）：启动时由 attach 读回最近 12 小时内记下的（KEEP * TICK_S），导出
诊断时照旧带上最近 12 小时。这样重启、崩溃以后再重启，重启以前的记录也还在。不写进识别记录（advisor/gamelog.py、
Session.log_state）：识别记录有总量上限、按局切段，对局报告也读它，每分钟多一条会挤掉对局内容；导出诊断时另写成诊断包里
的 usage.jsonl（见 advisor/diagnostics.py、advisor/ui/overlay_parts/background.py，和程序目录里的这个文件是两个东西）。
只留最近 12 小时：启动时丢掉更早的，超过 KEEP 条时丢掉最旧的；文件超过 2 * KEEP 行时整份重写成最近 KEEP 条（一直追加会
越来越长）。每分钟一条攒 FLUSH_RECORDS 条写一次，崩溃时最多丢这么多分钟；程序退出时 flush 把还没写的写完。没有 attach 时
（测试、工具）只放在内存里，不写任何文件。每条另记下当时的电源状况（"power"，见 advisor/sysinfo.py 的 power_info）：插不插
电源、电池电量、节电模式、电源模式和电源计划；取不到时不加这一项。

导出诊断时现量（probe）：枚举本进程的线程，量这一小段时间里每个线程占了多少处理器；忙的线程再带上它当时在跑哪几行代码
（只看文件名和函数名，不读局部变量）。看的就是拾星助手自己：不开别的进程、不碰游戏窗口和内存，也不暂停任何线程。

明确不记的：进程里加载的第三方模块的文件名、线程的起始地址和所属模块、别的进程的任何信息、电脑名；写进诊断包的文字一律
经 paths.scrub_paths 去掉账户名。

不导入 Qt，也不导入 advisor.ui 下的任何东西：界面线程的定时器只调用 tick()，导出时在后台线程调用 probe()。导入
advisor.sysinfo 只为读本机的电源状况，它也不导入界面库。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from ctypes import wintypes
from pathlib import Path

from . import fileio, perfstats, sysinfo

# 每分钟记一条：和识别记录的统计同一节奏，也够看出空闲时的占用
TICK_S = 60
# 留最近这么多条：12 小时，够覆盖一次长时间挂机（内存里和文件里都按它）
KEEP = 720
# 程序目录 logs 下存这些记录的文件名。诊断包里另有一个同名的 usage.jsonl（advisor/diagnostics.py 的 USAGE），那个是导出时
# 从内存取出来的一次性副本，和这个文件不是一回事
FILE_NAME = "usage.jsonl"
# 攒这么多条写一次文件：程序目录常在 OneDrive 里，每分钟写一次就每分钟同步一次；攒 5 条写一次，崩溃时最多丢 5 分钟
FLUSH_RECORDS = 5
# 导出诊断时现量的时长（秒）：调用的线程会睡着等，放在后台线程里做；太短量不准，太长玩家要多等
PROBE_S = 3.0
# 现量期间看几遍调用栈：每 PROBE_S / STACK_SAMPLES 看一遍，偶尔跑到一次的代码也能撞上
STACK_SAMPLES = 5
# 一个核的百分比到这么多才算忙，才带调用栈：库自己的线程（推理、图形）占得少时没有参考价值，还白占地方
BUSY_CORE = 10.0
# 最多列几个线程、每个线程的调用栈最多写几层、最多记几种不同的调用栈：只留最占处理器的，诊断包不至于太大
MAX_THREADS = 12
MAX_FRAMES = 12
MAX_STACKS = 3

# 线程查询权限（OpenThread 用）：只要查时间
_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
# CreateToolhelp32Snapshot 的 TH32CS_SNAPTHREAD：列出进程的线程
_TH32CS_SNAPTHREAD = 0x00000004
# FILETIME 的一个单位是 100 纳秒
_TICKS_PER_SECOND = 10_000_000

_lock = threading.Lock()
_records: deque = deque(maxlen=KEEP)  # 每分钟一条，最旧的先丢
_base: dict | None = None  # 上一段的起点：{"at": 时钟, "snapshot": 各处的处理器时间}
_power = sysinfo.power_info  # 取电源状况的函数（测试里换掉）：读不到、出错时不出这一项
_path: Path | None = None  # attach 给的文件；没 attach 时为 None，只放内存、不写任何文件
_pending: list[dict] = []  # 已经记下、还没写进文件的记录
_file_lines = 0  # 文件里现在有几行（attach 留下的加上之后追加的）
_write_failed = False  # 写盘出过错：本次运行不再写盘，内存里照常记
# 写盘时按「写盘出错」处理的：写不进（OSError）、写不成文字（ValueError：编码出错、循环引用）、读回的记录嵌套太深
# （RecursionError，json.dumps 报）。attach 在启动时重写文件，这几种都不能往外抛
_WRITE_ERRORS = (OSError, ValueError, RecursionError)


class THREADENTRY32(ctypes.Structure):
    """Thread32First/Thread32Next 写回的线程信息（tlhelp32.h）。"""

    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.GetThreadTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    _kernel32.GetThreadTimes.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:  # 不是 Windows：下面取不到线程，probe 返回 None
    _kernel32 = None


def tick(state: dict | None = None) -> None:
    """界面线程的定时器调用：这一段够长时记一条处理器占用。

    第一次调用只记基准，不出记录。之后每次出一条 {"at": 本地时刻, "s": 这一段几秒, "process": 本进程, "ui": 界面线程,
    "system": 整机, "power": 电源状况, **state}；口径和 recog_stats 的 cpu 相同（process、ui 折成一个核的百分比，可以超过
    100；system 是整机忙的百分比）。这一段的时长按不含电脑睡眠时间的时钟算，睡一小时不会把占用摊薄。不到 1 秒的一段不出
    记录（定时器抖动），基准也不换，下一段接着从原来的起点算。取电源状况出错、读不到时只是不带 power，这一条照记。attach
    过时也攒着写进文件（攒满 FLUSH_RECORDS 条写一次）。任何异常都接住、不往外抛：它是定时器的回调。
    """
    global _base
    try:
        now = perfstats.unbiased_clock()
        snapshot = perfstats.usage_snapshot()
        with _lock:
            base = _base
            if base is None:
                _base = {"at": now, "snapshot": snapshot}
                return
            elapsed = now - base["at"]
            if elapsed < 1.0:
                return
            _base = {"at": now, "snapshot": snapshot}
        if base["snapshot"] is None or snapshot is None:
            return
        percent = perfstats.usage_percent(base["snapshot"], snapshot, elapsed)
        record = {"at": datetime.now().isoformat(timespec="seconds"), "s": round(elapsed, 1), **percent}
        if isinstance(state, dict):
            record.update(state)
        power = _read_power()
        if power is not None:
            record["power"] = power
        with _lock:
            _records.append(record)
            if _path is None or _write_failed:
                return
            _pending.append(record)
            if len(_pending) < FLUSH_RECORDS:
                return
            pending, path = list(_pending), _path
            _pending.clear()
        _append(path, pending)
    except Exception:  # noqa: BLE001  定时器的回调不能把异常抛出去
        pass


def _read_power() -> dict | None:
    """取这一刻的电源状况；读不到或出错时为 None（这一条记录只是不带 power）。"""
    try:
        return _power()
    except Exception:  # noqa: BLE001  取不到不影响记占用
        return None


def records() -> list[dict]:
    """现在留在内存里的记录（最旧的在前），拷一份交出去，调用方改了也不影响这里。"""
    with _lock:
        return [dict(record) for record in _records]


def attach(path: Path | str) -> None:
    """程序启动时调用一次：把以后记的每分钟一条也存进 path，并读回那里已有的记录。

    读一行一条的 JSON，只留 at 在现在往前 KEEP * TICK_S 秒（12 小时）以内的，按先后放进 _records（最多 KEEP 条，旧的先
    丢）；读不了的行（坏 JSON、嵌套太深、没有 at、格式不对）跳过。有要丢的行时把文件重写成只剩留下的这些（fileio 的整份
    写入：先写临时文件再改名）。文件不存在时不新建，等第一条记录再建。读不了整个文件时（被占用、没有权限、读的时候出别的
    错）按里面什么都没有处理，不报错，文件不动。启动时调用：任何情况下都不往外抛，不能让程序起不来。
    """
    global _path, _pending, _file_lines, _write_failed
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        kept, lines = _parse(text)
    except FileNotFoundError:
        text, kept, lines = None, [], 0
    except Exception:  # noqa: BLE001  读不了：按没有能用的记录处理，之后照常追加
        text, kept, lines = "", [], 0
    dropped = text is not None and lines != len(kept)
    with _lock:
        _path = path
        _pending = []
        _write_failed = False
        _file_lines = len(kept) if dropped else lines
        _records.clear()
        _records.extend(kept[-KEEP:])
    if dropped:
        # 写盘在锁外做；写不成时按写盘出错处理（本次运行不再写，内存里照常记）
        try:
            _rewrite(path, kept)
        except Exception as e:  # noqa: BLE001  _WRITE_ERRORS 以外的也接住：启动时不能抛
            _fail("改写", e)


def flush() -> None:
    """把攒着还没写的记录立刻写进文件（程序退出时调用）。没 attach、写盘出过错时什么都不做。"""
    with _lock:
        if _path is None or _write_failed or not _pending:
            return
        pending, path = list(_pending), _path
        _pending.clear()
    _append(path, pending)


def _parse(text: str | None) -> tuple[list[dict], int]:
    """读回文件里的记录：返回（现在还在 12 小时以内的记录，文件里的行数）。坏行和过期的行跳过，不算进前者。"""
    if not text:
        return [], 0
    cutoff = datetime.now() - timedelta(seconds=KEEP * TICK_S)
    kept, lines = [], 0
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue  # 末尾的换行、空行不算一行
        lines += 1
        try:
            record = json.loads(line)
            at = datetime.fromisoformat(record["at"])
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):  # 嵌套太深的 JSON 报 RecursionError
            continue
        try:
            if at < cutoff:
                continue
        except TypeError:  # at 带了时区：和本地时刻比不了，当成读不了
            continue
        if isinstance(record, dict):
            kept.append(record)
    return kept, lines


def _append(path: Path, records: list[dict]) -> None:
    """把 records 追加进文件；之后文件太长时整份重写成最近 KEEP 条。写盘出错时记一次错误记录、本次运行不再写。"""
    global _file_lines
    try:
        fileio.append_lines(path, [json.dumps(record, ensure_ascii=False, default=str) for record in records],
                            newline="\n")
    except _WRITE_ERRORS as e:
        _fail("写入", e)
        return
    with _lock:
        if path != _path:
            return
        _file_lines += len(records)
        too_long = _file_lines > 2 * KEEP
        kept = list(_records)
    if too_long:
        _rewrite(path, kept)


def _rewrite(path: Path, records: list[dict]) -> None:
    """把文件整份重写成 records（fileio 的整份写入）。写盘出错时记一次错误记录、本次运行不再写。"""
    global _file_lines
    try:
        fileio.write_text(path, "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n"
                                        for record in records), newline="\n")
    except _WRITE_ERRORS as e:
        _fail("改写", e)
        return
    with _lock:
        if path == _path:
            _file_lines = len(records)


def _fail(action: str, error: BaseException) -> None:
    """写盘出错：记一次错误记录，本次运行不再写盘（内存里照常记录，导出诊断照常）。"""
    global _write_failed
    with _lock:
        if _write_failed:
            return
        _write_failed = True
        path = _path
    try:
        from .errors import log_error

        log_error(f"处理器占用记录{action}失败，本次运行不再写盘（内存中照常记录，导出诊断不受影响）",
                  f"{path}\n{type(error).__name__}: {error}", append=True)
    except Exception:  # noqa: BLE001  记错误本身出错时不影响别的
        pass


def reset() -> None:
    """清空记录、基准和存盘的状态（测试用）：回到没 attach 的样子。"""
    global _base, _path, _pending, _file_lines, _write_failed
    with _lock:
        _records.clear()
        _base = None
        _path = None
        _pending = []
        _file_lines = 0
        _write_failed = False


def probe(seconds: float | None = None, *, sleep=time.sleep) -> dict | None:
    """现量这一小段时间里本进程各线程的处理器占用，返回结果；不是 Windows 时为 None。

    seconds 不给时调用时才读 PROBE_S。枚举本进程的线程（CreateToolhelp32Snapshot 的 TH32CS_SNAPTHREAD，只留本进程号），
    记下起点各线程的时间、perfstats.cpu_ticks() 和时钟；睡几觉，每觉以后看一遍 sys._current_frames()；再记终点。不暂停
    任何线程，不读局部变量；调用栈只留文件名、行号和函数名，用完不留帧对象的引用。

    结果里："seconds" 实际秒数（一位小数）；"process" 本进程（一个核的百分比）、"system" 整机（0～100）；"thread_count"
    起点时的线程总数；"stack_threads" 取到过调用栈的线程数（不管忙不忙）；"threads" 按 core 从大到小（最多 MAX_THREADS 个，
    core 为 0 的不列），每项 {"tid", "core", "name"（只在 threading.enumerate() 里有时才写）, "stacks"}。stacks 只在 core
    达到 BUSY_CORE 且取到过调用栈时有：按次数从多到少，最多 MAX_STACKS 条，每条 {"n": 次数, "frames": [...]}，最里面的
    调用在前。起点和终点之间退出的线程不列，新建的不算。sys._current_frames() 出错时占用照常算，另多一项 "stacks_error"。
    """
    if _kernel32 is None:
        return None
    try:
        span = float(PROBE_S if seconds is None else seconds)
        if span < 0:
            span = 0.0
        return _probe(span, sleep)
    except Exception:  # noqa: BLE001  导出诊断时量不了也不能让导出失败
        return None


def _probe(span: float, sleep) -> dict:
    start_times = _thread_times()
    base = _cpu_base()
    start = perfstats.unbiased_clock()
    own = threading.get_ident()
    stacks: dict[int, dict[tuple, int]] = {}
    stacks_error = None
    interval = span / STACK_SAMPLES
    for _ in range(STACK_SAMPLES):
        sleep(interval)
        try:
            frames = sys._current_frames()
        except Exception as e:  # noqa: BLE001  拿不到调用栈时占用照常算
            stacks_error = f"{type(e).__name__}: {e}"
            continue
        try:
            _collect_stacks(frames, stacks, own)
        finally:
            frames = None
    end = perfstats.unbiased_clock()
    end_times = _thread_times()
    now = _cpu_base()
    elapsed = max(end - start, 0.0)
    percent = perfstats.usage_percent(base, now, elapsed) if base is not None and now is not None else {}
    names = _thread_names()
    span_ticks = elapsed * _TICKS_PER_SECOND
    threads = []
    for tid, before in start_times.items():
        after = end_times.get(tid)
        if after is None or after < before:
            continue  # 这一段里退出了：不列
        core = round(100.0 * (after - before) / span_ticks, 1) if span_ticks > 0 else 0.0
        if core <= 0:
            continue
        item = {"tid": tid, "core": core}
        if tid in names:
            item["name"] = names[tid]
        if core >= BUSY_CORE and stacks.get(tid):
            item["stacks"] = _stack_list(stacks[tid])
        threads.append(item)
    threads.sort(key=lambda item: item["core"], reverse=True)
    result = {
        "seconds": round(elapsed, 1),
        "process": percent.get("process"),
        "system": percent.get("system"),
        "thread_count": len(start_times),
        "stack_threads": len(stacks),
        "threads": threads[:MAX_THREADS],
    }
    if stacks_error is not None:
        result["stacks_error"] = stacks_error
    return result


def _cpu_base() -> dict | None:
    """这一刻的处理器时间，写法同 perfstats.usage_snapshot 但没有 ui：probe 只算本进程和整机。"""
    ticks = perfstats.cpu_ticks()
    if ticks is None:
        return None
    process, busy, total = ticks
    return {"process": process, "system": (total - busy, total)}


def _thread_names() -> dict[int, str]:
    """threading.enumerate() 里各线程的名字：{线程号: 名字}。取不到时为空表（结果里就不写 name）。"""
    try:
        return {t.ident: t.name for t in threading.enumerate() if t.ident is not None}
    except Exception:  # noqa: BLE001  名字只是参考，取不到不影响占用
        return {}


def _thread_ids() -> list[int] | None:
    """本进程的线程号（CreateToolhelp32Snapshot 的 TH32CS_SNAPTHREAD，只用本进程号的那几项）；取不到时为 None。"""
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        pid = os.getpid()
        found = []
        more = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                found.append(entry.th32ThreadID)
            more = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        return found
    finally:
        _kernel32.CloseHandle(snapshot)


def _thread_times() -> dict[int, int]:
    """这一刻本进程各线程的处理器时间（100 纳秒，内核加用户）：{线程号: 时间}。打不开的线程不列。"""
    out: dict[int, int] = {}
    tids = _thread_ids()
    if tids is None:
        return out
    for tid in tids:
        handle = _kernel32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, tid)
        if not handle:
            continue
        try:
            creation, exit_time, kernel, user = (wintypes.FILETIME(), wintypes.FILETIME(),
                                                 wintypes.FILETIME(), wintypes.FILETIME())
            if _kernel32.GetThreadTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                        ctypes.byref(kernel), ctypes.byref(user)):
                out[tid] = _ticks(kernel) + _ticks(user)
        finally:
            _kernel32.CloseHandle(handle)
    return out


def _ticks(value) -> int:
    """一个 FILETIME（100 纳秒为单位）转成整数。"""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _collect_stacks(frames: dict, stacks: dict[int, dict[tuple, int]], own: int) -> None:
    """看一遍各线程的调用栈：跳过调用 probe 的这个线程，别的线程按「调用栈」的次数累计（只要文字，不留下帧对象）。"""
    for tid, frame in frames.items():
        if tid == own:
            continue
        try:
            text = _frames_of(frame)
        except Exception:  # noqa: BLE001  某一帧读不了不影响别的线程
            continue
        if not text:
            continue
        key = tuple(text)
        per_thread = stacks.setdefault(tid, {})
        per_thread[key] = per_thread.get(key, 0) + 1
        frame = None


def _frames_of(frame) -> list[str]:
    """沿 f_back 往外走，最里面的调用在前，最多 MAX_FRAMES 层。只取文件名、行号和函数名：不读源文件（发行版里没有），
    也不读局部变量。"""
    out = []
    f = frame
    try:
        while f is not None and len(out) < MAX_FRAMES:
            code = f.f_code
            out.append(f"{os.path.basename(code.co_filename)}:{f.f_lineno} {code.co_name}")
            f = f.f_back
    finally:
        f = None
    return out


def _stack_list(counts: dict[tuple, int]) -> list[dict]:
    """一个线程的几种调用栈：按次数从多到少，最多 MAX_STACKS 条。"""
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{"n": n, "frames": list(frames)} for frames, n in ordered[:MAX_STACKS]]
