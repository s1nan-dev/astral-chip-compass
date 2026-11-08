"""文字识别线程数的自检结果差时，查出是哪一种原因。

什么时候用：自检量出来的线程数读一次画面偏慢（较差、太慢、忽快忽慢）时，调用方先 Probe 记一段读数，再用 diagnose 把查到
的原因一条条列出来。读的几样：这一段整机、本进程、游戏进程各用了多少处理器，处理器性能百分比和限速（PDH 计数器），
以及这一刻的电源状态、内存、本进程的省电设置、优先级、能用哪些核、处理器有没有 AVX2。读不到的项一律按没查到，宁可不说
也不要说错。结果一般时，有大小核的电脑上只比一下只用大核（find 的 cores_only）：读一次不算慢，别的原因不值得提，只看
文字识别是不是被排到了小核。

分界线的理由：别的程序（不含本进程和游戏）占了整机 30% 以上、或者读不到游戏占用时本进程以外占了 60% 以上，说明不是本
进程的活慢；处理器只跑到标称频率的 70% 以下、同时确实有东西在限速（% Performance Limit 低于 90），才算被降频；电源、
优先级、核的限制各按系统给出的那个状态算。这些都是这台电脑上量得到的客观读数，不是猜的。

只读本进程和系统公开的信息，只改本进程自己的设置（优先级、能用哪些核、省电声明）。不往游戏发键盘、鼠标、窗口消息，
不挪游戏窗口，不读写游戏内存，不注入、不挂钩子、不碰游戏的网络。查游戏进程只用 PROCESS_QUERY_LIMITED_INFORMATION 打开，
只读处理器时间。
"""
from __future__ import annotations

import ctypes
import os
import traceback
from ctypes import wintypes
from dataclasses import dataclass, field

from .. import perfstats, sysinfo
from ..errors import log_error

# 别的程序（不含本进程和游戏）占了整机这么多百分比：多半是别的程序在抢处理器，不是本进程慢
OTHERS_BUSY = 30
# 读不到游戏的占用时，本进程以外占了这么多百分比：同上，只是分不清里面有没有游戏
ALL_BUSY = 60
# 处理器性能百分比低于它算被降频（标称频率的 70%）
THROTTLED = 70
# % Performance Limit 低于它才算确实有东西在限速：两个都读到、又都低于分界，才判成降频（跑得低、限速还是 100 时多半只是闲着）
LIMITED = 90
# 可用内存低于这么多 GB
LOW_MEMORY_GB = 1.0
# 逻辑处理器不超过它算核心太少
FEW_LOGICAL = 4
# 只用大核比不限制快这么多倍才算真有用（小核慢一两成不值得少用核）。定义在 sysinfo：设置窗口「只用大核」一项不导入识别模块，
# 也按同一个分界判断（sysinfo.on_small_cores）
GAIN_MIN = sysinfo.BIG_CORE_GAIN_MIN

# 这次的状况可能一会儿就变、或者玩家照着改了，下次启动值得再量的原因：其余的（核太少、老处理器等）不会自己变好
TRANSIENT =("no_ac", "saver", "power_mode", "power_plan", "others_busy", "throttled", "low_memory")

# PDH 计数器的名字（英文名，和系统语言无关）
_PERF = r"\Processor Information(_Total)\% Processor Performance"
_LIMIT = r"\Processor Information(_Total)\% Performance Limit"
_FLAGS = r"\Processor Information(_Total)\Performance Limit Flags"


class _Value(ctypes.Structure):
    """PdhGetFormattedCounterValue 写回的计数器的值（PDH_FMT_COUNTERVALUE）：状态和双精度值。"""
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


@dataclass(frozen=True)
class Reason:
    """查到的一种原因。key 见 diagnose 的表格；fix 是能替玩家做的那件事（空串是只能玩家自己做）；values 是句子里要填的数。"""

    key: str
    fix: str = ""
    values: dict = field(default_factory=dict)

    @property
    def transient(self) -> bool:
        return self.key in TRANSIENT


def diagnose(state: dict, *, split=None, gain=None, unknown=False) -> list[Reason]:
    """按这里的表从上到下的顺序，返回 state 里成立的全部原因（纯函数，不读电脑、不改设置）。

    state 的形状见 read_state：load 是 Probe.finish 的结果，其余是 sysinfo 的读数。读不到的项（None）不算。"""
    load = state.get("load") or {}
    power = state.get("power") or {}
    memory = state.get("memory") or {}
    cpu = state.get("cpu") or {}
    busy, own, game = load.get("busy"), load.get("own"), load.get("game")
    perf, limit = load.get("perf"), load.get("limit")
    throttled = perf is not None and limit is not None and perf < THROTTLED and limit < LIMITED

    found: list[Reason] = []
    power_reasons: list[Reason] = []
    if power.get("ac") is False:
        power_reasons.append(Reason("no_ac", values={"perf": int(perf) if throttled else None}))
    if power.get("saver") is True:
        power_reasons.append(Reason("saver"))
    if power.get("mode") == "best_efficiency":
        power_reasons.append(Reason("power_mode"))
    if power.get("plan") == "power_saver":
        power_reasons.append(Reason("power_plan"))
    found.extend(power_reasons)

    if busy is not None and own is not None:
        if game is not None:
            others = busy - own - game
            if others >= OTHERS_BUSY:
                found.append(Reason("others_busy", values={"percent": int(others), "with_game": False}))
        elif busy - own >= ALL_BUSY:
            found.append(Reason("others_busy", values={"percent": int(busy - own), "with_game": True}))

    # 被降频、同时已经有电源那几条时不单列：多半就是电源设置造成的；其中断电那条带上读到的性能百分比
    if throttled and not power_reasons:
        found.append(Reason("throttled", values={"perf": int(perf)}))

    available = memory.get("available_gb")
    if available is not None and available < LOW_MEMORY_GB:
        found.append(Reason("low_memory", values={"available_gb": available}))

    if state.get("performance") == "eco":
        found.append(Reason("eco", "eco"))
    if state.get("priority") in ("idle", "below_normal"):
        found.append(Reason("low_priority", "priority"))

    affinity = state.get("affinity")
    if affinity is not None and affinity[0] < affinity[1]:
        found.append(Reason("affinity", "affinity", {"allowed": affinity[0], "total": affinity[1]}))

    if split is not None and sysinfo.on_small_cores(gain):
        found.append(Reason("small_core", "big_cores",
                            {"big": split["big"], "small": split["small"], "ratio": round(gain, 1)}))

    if state.get("lacks_avx2") is True:
        found.append(Reason("old_cpu", "lowend"))

    logical, cores = cpu.get("logical"), cpu.get("cores")
    if cores is not None and logical is not None and logical <= FEW_LOGICAL:
        found.append(Reason("few_cores", "lowend", {"cores": cores, "logical": logical}))

    if not found and unknown:
        found.append(Reason("unknown", "lowend"))
    return found


def shown(found, declined=()) -> tuple[Reason, ...]:
    """挑要写出来的（最多两条，能替玩家改的放最后）：先挑一条只能自己做的，再配一条能替玩家改的；玩家说过不要的那些
    能替玩家改的不再问，只剩只能自己做的还照写。纯函数。"""
    offers = [r for r in found if r.fix and r.fix not in declined]
    manual = [r for r in found if not r.fix]
    if manual:
        return (manual[0], offers[0]) if offers else tuple(manual[:2])
    return tuple(offers[:1])


def _ticks(ft) -> int:
    """一个 FILETIME（100 纳秒为单位）转成整数。"""
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


class Probe:
    """一次自检期间的读数：起点记下本进程、整机和游戏进程的处理器时间，开一个 PDH 查询；finish 时记终点、算出这一段
    各用的百分比和处理器性能、限速。游戏进程有一个打不开，游戏占用就算读不到（不然它用的处理器会算到别的程序头上）。全部
    接住异常，读不到的一项为 None。只读，不改任何设置。"""

    def __init__(self, pids=()):
        self._start = perfstats.cpu_ticks()
        self._k32 = None
        self._processes: list[tuple] = []  # （进程句柄, 起点处理器时间）
        self._pdh = None
        self._query = None
        self._counters: dict = {}
        self._result: dict | None = None
        try:
            self._open_processes(pids)
        except Exception:  # noqa: BLE001  打不开游戏进程只影响这一项，照常记别的
            self._processes = []
        try:
            self._open_pdh()
        except Exception:  # noqa: BLE001  PDH 打不开时性能、限速为 None
            self._close_pdh()

    def _open_processes(self, pids) -> None:
        """按进程号打开游戏进程（只要 PROCESS_QUERY_LIMITED_INFORMATION）、记下现在用掉的处理器时间；有一个打不开、读不到时
        一个都不留（游戏占用算读不到）。"""
        if os.name != "nt":
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # 自己的一份：声明参数类型不影响别处
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                        ctypes.POINTER(wintypes.FILETIME)]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._k32 = k32
        for pid in pids or ():
            handle = base = None
            try:
                handle = k32.OpenProcess(0x1000, False, int(pid))
                base = self._process_ticks(handle) if handle else None
            except Exception:  # noqa: BLE001  出错和打不开一样
                pass
            if base is None:
                if handle:
                    self._processes.append((handle, 0))  # 和前面打开的一起关
                self._close_processes()
                return
            self._processes.append((handle, base))

    def _process_ticks(self, handle) -> int | None:
        if not handle:
            return None
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self._k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                         ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return _ticks(kernel) + _ticks(user)

    def _open_pdh(self) -> None:
        """开 PDH 查询、加三个计数器、记一次起点（累计型计数器要两次才读得出这一段的平均）。打不开时什么都不留。"""
        if os.name != "nt":
            return
        pdh = ctypes.WinDLL("pdh")  # 到这里才加载：用不到自检的电脑上不加载这个库
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhOpenQueryW.restype = wintypes.LONG
        pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t,
                                              ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhCollectQueryData.restype = wintypes.LONG
        pdh.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                    ctypes.POINTER(_Value)]
        pdh.PdhGetFormattedCounterValue.restype = wintypes.LONG
        pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        pdh.PdhCloseQuery.restype = wintypes.LONG
        query = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0 or not query:
            return
        counters = {}
        try:
            for name in (_PERF, _LIMIT, _FLAGS):
                handle = ctypes.c_void_p()
                if pdh.PdhAddEnglishCounterW(query, name, 0, ctypes.byref(handle)) == 0:
                    counters[name] = handle
            if not counters or pdh.PdhCollectQueryData(query) != 0:
                pdh.PdhCloseQuery(query)
                return
        except Exception:  # noqa: BLE001  加计数器失败时关掉查询，性能、限速按读不到
            pdh.PdhCloseQuery(query)
            raise
        self._pdh, self._query, self._counters = pdh, query, counters

    def finish(self) -> dict:
        """记终点、算出这一段的读数：busy、own、game 是占整机的百分比，perf、limit、flags 是 PDH 读到的数；都是四舍五入
        的整数，读不到的为 None。关掉游戏进程句柄和 PDH 查询；重复调用返回同一个结果。"""
        if self._result is not None:
            return self._result
        result = {"busy": None, "own": None, "game": None, "perf": None, "limit": None, "flags": None}
        try:
            now = perfstats.cpu_ticks()
            total = None
            if self._start is not None and now is not None:
                own_ticks = now[0] - self._start[0]
                busy_ticks = now[1] - self._start[1]
                total = now[2] - self._start[2]
                if total > 0:
                    result["busy"] = round(100.0 * busy_ticks / total)
                    result["own"] = round(100.0 * own_ticks / total)
            if total is not None and total > 0 and self._processes:
                game_ticks = 0
                readable = True
                for handle, base in self._processes:
                    current = self._process_ticks(handle)
                    if current is None:
                        readable = False
                        break
                    game_ticks += current - base
                if readable:
                    result["game"] = round(100.0 * game_ticks / total)
            if self._pdh is not None and self._pdh.PdhCollectQueryData(self._query) == 0:
                for name, key in ((_PERF, "perf"), (_LIMIT, "limit"), (_FLAGS, "flags")):
                    handle = self._counters.get(name)
                    if handle is None:
                        continue
                    value = _Value()
                    if self._pdh.PdhGetFormattedCounterValue(handle, 0x200, None, ctypes.byref(value)) == 0:
                        result[key] = round(value.doubleValue)
        except Exception:  # noqa: BLE001  算不出来的一项按 None，不往外抛
            pass
        finally:
            self._close()
            self._result = result
        return result

    def _close(self) -> None:
        """关掉游戏进程句柄和 PDH 查询（重复调用不出错）。"""
        try:
            if self._pdh is not None and self._query:
                self._pdh.PdhCloseQuery(self._query)
        except Exception:  # noqa: BLE001  关不上不影响结果
            pass
        self._pdh = self._query = None
        self._counters = {}
        self._close_processes()

    def _close_processes(self) -> None:
        for handle, _base in self._processes:
            try:
                if handle:
                    self._k32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass
        self._processes = []

    def _close_pdh(self) -> None:
        try:
            if self._pdh is not None and self._query:
                self._pdh.PdhCloseQuery(self._query)
        except Exception:  # noqa: BLE001
            pass
        self._pdh = self._query = None
        self._counters = {}


def begin(pids=()) -> Probe:
    """开始一次自检的读数（pids 是各张悬浮窗对应的游戏进程号）。"""
    return Probe(pids)


def read_state(load: dict) -> dict:
    """读这一刻的其余状况，和 load（Probe.finish 的结果）合成 state（形状见 diagnose）。"""
    return {
        "load": load,
        "power": sysinfo.power_info(),
        "memory": sysinfo.memory_info(),
        "performance": sysinfo.performance_state(),
        "priority": sysinfo.priority_class(),
        "affinity": sysinfo.affinity_counts(),
        "cpu": sysinfo.cpu_info(),
        "lacks_avx2": sysinfo.lacks_avx2(),
    }


def readings(state, gain) -> dict:
    """给设置文件存的一层字典（值只有 int、float、str、bool、None）：别人发诊断包时靠它看当时的读数，所以分界线没触发
    的也照存。"""
    load = state.get("load") or {}
    power = state.get("power") or {}
    memory = state.get("memory") or {}
    affinity = state.get("affinity")
    return {
        "busy": load.get("busy"),
        "own": load.get("own"),
        "game": load.get("game"),
        "perf": load.get("perf"),
        "limit": load.get("limit"),
        "flags": load.get("flags"),
        "ac": power.get("ac"),
        "saver": power.get("saver"),
        "mode": power.get("mode"),
        "plan": power.get("plan"),
        "available_gb": memory.get("available_gb"),
        "performance": state.get("performance"),
        "priority": state.get("priority"),
        "allowed": affinity[0] if affinity is not None else None,
        "total": affinity[1] if affinity is not None else None,
        "gain": round(gain, 1) if gain is not None else None,
    }


def find(probe, kind, *, threads, stop=None, progress=None, declined=(), big_cores_on=False,
         cores_only=False) -> tuple:
    """收尾这次读数、查原因：返回（要写出来的原因, 给设置文件存的读数）。

    本进程被设成省电、被限核时比出来的不是核的大小（这时不比，先问它们）；忽快忽慢时比出来的可能只是抖动（下次启动还会再
    量）；设置里已经开着只用大核、玩家说过不要时用不着：这些都不比。比不出来不影响别的原因。

    cores_only 为真（结果一般时）只看核的大小：要写出来的原因只留「排到了小核」（small_core）这一条，查到的别的原因不写，
    一条都没查到时也不补「可能的原因」；什么时候比照上面，读数照旧整份返回（比出来的比值在 gain 里）。"""
    state = read_state(probe.finish())
    split = sysinfo.big_core_split()
    gain = None
    affinity = state.get("affinity")
    limited = affinity is not None and affinity[0] < affinity[1]
    if split and kind != "noisy" and not big_cores_on and "big_cores" not in declined \
            and state.get("performance") != "eco" and not limited:
        try:
            from . import ocr_check

            gain = ocr_check.big_core_gain(stop, threads=threads, progress=progress)
        except Exception:  # noqa: BLE001  比不出来不影响别的原因
            log_error("比较只用大核的收益时出错（拾星助手仍在运行）", traceback.format_exc(), append=True)
    found = diagnose(state, split=split, gain=gain, unknown=kind in ("poor", "slow") and not cores_only)
    if cores_only:
        found = [reason for reason in found if reason.key == "small_core"]
    return shown(found, declined), readings(state, gain)
