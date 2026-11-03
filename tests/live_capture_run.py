"""直接截取的实机测试的执行脚本：在子进程里跑（tests/test_window_capture_live.py 启动它），最后打印一行
「RESULT {json}」，由测试判定是否通过。

用法：python tests/live_capture_run.py [capture] [e2e] [perf]（不给时三项都做）
  capture 截取：不被挡、被同进程的窗口完全盖住、移动、改大小、改回、画面静止、最小化、还原、关闭后重开（新的窗口句柄）；
          另查请求不显示黄框和不截鼠标的读回值、对句柄 0 建截取对象不崩溃；
  e2e     端到端：测试窗口先显示对局画面，再换成 samples 里有标注的筹码三选一截图（3 张），用取帧对象 + 识别流程
          + Session 走完整链路，看候选筹码和标注是否一致、换成三选一后第几次识别给出推荐；
  perf    4K 性能和内存：每秒 5 次取帧 10 秒的耗时和 CPU，建会话前后、连续 300 帧前后、空闲关闭后的内存和句柄数。
          另记分步计时（five_per_second 里的 steps 等几项，见 StepClock）：每一步的墙钟时间和 CPU，CPU 按周期数算。
测试窗口由 live_window_host.py 在另一个进程里显示，全程在所有屏幕的范围以外（x = -20000），不抢焦点；每条命令之后
都核对测试窗口的矩形不和屏幕相交、前台窗口不是测试窗口。不出声、不截屏幕、不碰其他程序的窗口。
结果除了打印「RESULT {json}」一行，也存一份到 .tmp\\live-capture\\result-<时间>.json；跑了 perf 时再打印一张分步表。
"""
from __future__ import annotations

import ctypes
import gc
import json
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # 和悬浮窗一样按物理像素（要先于导入截图模块）

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.ui import screen  # noqa: E402
from advisor.vision import capture, window_capture as wc  # noqa: E402

HOST = Path(__file__).resolve().with_name("live_window_host.py")
SAMPLES = ROOT / "samples"
OFF_X, OFF_Y = -20000, 100
CELLS, STRIP = 24, 12
QUAD_BGR = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 0, 255)]  # 左上红、右上绿、左下蓝、右下品红
TOLERANCE = 24
HUD = "20261002_213905_hud_round7_real.jpg"
SELECTS = ("20261002_212245_chipselect_levelup_star1_real.jpg", "20261002_213757_chipselect_team_task_tier2_real.jpg",
           "20261002_215027_chipselect_levelup_star3_real.jpg")
E2E_SIZE = (3840, 2054)  # 这几张样本是窗口客户区原尺寸
PERF_SIZE = (3840, 2160)

kernel32 = ctypes.WinDLL("kernel32")
psapi = ctypes.WinDLL("psapi")
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetCurrentThread.restype = wintypes.HANDLE
kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryProcessCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]
kernel32.QueryThreadCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def memory() -> dict:
    """本进程的私有内存、工作集（MB）和句柄数。"""
    gc.collect()
    counters = MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(MEMORY_COUNTERS)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    handles = wintypes.DWORD(0)
    kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(handles))
    return {"private_mb": round(counters.PrivateUsage / 2 ** 20, 1), "working_mb": round(counters.WorkingSetSize / 2 ** 20, 1),
            "handles": int(handles.value)}


def virtual_screen() -> tuple[int, int, int, int]:
    left, top = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    return left, top, left + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), top + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)


# 分步计时：mark 的名字 → 和上一个时刻之间那一段记在哪一步下
STEP_NAMES = {
    "begin": "between",  # 上次取帧对象交出画面 → 这次取帧开始：两次取帧之间（主线程在睡，算的是其他线程）
    "start": "find",  # 取帧对象开始取帧 → 会话开始取帧：找游戏窗口、查位置
    "wait": "wait",  # 取出排队的旧帧、等新帧
    "frame": "frame",  # 帧的尺寸和时间戳、取帧的纹理
    "copy": "copy",  # 把客户区那一块拷贝排进显卡
    "map": "map",  # 等显卡做完拷贝、映射
    "convert": "convert",  # BGRA 转 BGR（读映射的内存）
    "unmap": "unmap",  # 解除映射
    "close": "close",  # 释放纹理、关闭帧
    "after": "after",  # 会话交出画面 → 取帧对象交出画面：统计、全黑判断
}


class StepClock:
    """分步计时：每一步的墙钟时间、整个进程所有线程的 CPU（QueryProcessCycleTime）和本线程的 CPU
    （QueryThreadCycleTime）。周期数按开头量出的计数频率换成毫秒。

    time.process_time() 按时钟中断采样：一次只有一两毫秒、分在很多线程上的工作，会被少算或多算（多线程做颜色转换时，
    同样每秒 5 次量 10 秒，process_time 几次在 1.9%～21% 之间跳，按周期数一直约 11%；只用一个线程时两者相近），
    所以这里另按周期数算。mark(名字) 记下一个时刻，和上一个时刻之间的一段记在 STEP_NAMES[名字] 下；只在 active 时记。"""

    def __init__(self):
        self.active = False
        self.last: tuple[float, int, int] | None = None
        self.rows: dict[str, dict[str, list[float]]] = {}
        self.hz = self._calibrate()

    @staticmethod
    def cycles() -> tuple[int, int]:
        """（整个进程的周期数, 本线程的周期数）。"""
        proc, thread = ctypes.c_uint64(0), ctypes.c_uint64(0)
        kernel32.QueryProcessCycleTime(kernel32.GetCurrentProcess(), ctypes.byref(proc))
        kernel32.QueryThreadCycleTime(kernel32.GetCurrentThread(), ctypes.byref(thread))
        return proc.value, thread.value

    def _calibrate(self) -> float:
        """周期数每秒多少：本线程空转 50 毫秒量三次，取最大的（没被打断的那次）。"""
        rates = []
        for _ in range(3):
            t0, c0 = time.perf_counter(), self.cycles()[1]
            while time.perf_counter() - t0 < 0.05:
                pass
            rates.append((self.cycles()[1] - c0) / (time.perf_counter() - t0))
        return max(rates)

    def now(self) -> tuple[float, int, int]:
        proc, thread = self.cycles()
        return time.perf_counter(), proc, thread

    def ms(self, cycles: int) -> float:
        return 1000.0 * cycles / self.hz

    def start(self) -> None:
        self.rows, self.last, self.active = {}, None, True

    def mark(self, name: str) -> None:
        if not self.active:
            return
        now = self.now()
        if self.last is not None:
            row = self.rows.setdefault(STEP_NAMES.get(name, name), {"wall": [], "cpu": [], "main": []})
            row["wall"].append((now[0] - self.last[0]) * 1000)
            row["cpu"].append(self.ms(now[1] - self.last[1]))
            row["main"].append(self.ms(now[2] - self.last[2]))
        self.last = now

    def summary(self, wall_s: float) -> dict:
        """每一步：n 次数；wall_ms_p50 / p95 墙钟；cpu_ms_mean / p95 整个进程的 CPU；main_ms_mean 其中取帧线程的；
        share_percent 这一步合计的 CPU 占单核的百分比（各步相加约等于 cpu_percent_cycles）。"""
        out = {}
        for name in [n for n in STEP_NAMES.values() if n in self.rows]:
            row = self.rows[name]
            wall, cpu, main = (np.asarray(row[k], dtype=float) for k in ("wall", "cpu", "main"))
            out[name] = {
                "n": int(cpu.size),
                "wall_ms_p50": round(float(np.percentile(wall, 50)), 3),
                "wall_ms_p95": round(float(np.percentile(wall, 95)), 3),
                "cpu_ms_mean": round(float(cpu.mean()), 3),
                "cpu_ms_p95": round(float(np.percentile(cpu, 95)), 3),
                "main_ms_mean": round(float(main.mean()), 3),
                "share_percent": round(100 * float(cpu.sum()) / 1000 / wall_s, 2),
            }
        return out


def intersects(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


class Watch:
    """每条命令之后核对：测试窗口（和显示着的盖住的窗口）不和任何屏幕相交；前台窗口不是测试窗口。"""

    def __init__(self):
        self.screen = virtual_screen()
        self.on_screen: list[dict] = []
        self.foreground: list[str] = []
        self.checks = 0

    def check(self, info: dict, where: str) -> None:
        self.checks += 1
        keys = ["rects"] + (["cover_rects"] if info.get("cover_visible") else [])
        for key in keys:
            rect = info.get(key, {}).get("window")
            if rect and intersects(rect, self.screen):
                self.on_screen.append({"where": where, "window": key, "rect": rect})
        if info.get("fg_ours"):
            self.foreground.append(where)


class Host:
    """测试窗口进程（live_window_host.py）。"""

    def __init__(self, watch: Watch, x: int, y: int, w: int, h: int, *flags: str):
        self.watch = watch
        self.p = subprocess.Popen([sys.executable, str(HOST), str(x), str(y), str(w), str(h), *flags],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.info = self._read("start")
        self.hwnd = self.info["hwnd"]

    def _read(self, where: str) -> dict:
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError(f"测试窗口进程退出了（{where}）")
            line = line.strip()
            if line.startswith("{"):
                info = json.loads(line)
                if "error" in info:
                    raise RuntimeError(f"测试窗口进程执行「{info.get('op')}」出错：{info['error']}")
                if "rects" in info:
                    self.watch.check(info, where)
                    return info

    def cmd(self, text: str) -> dict:
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()
        self.info = self._read(text)
        return self.info

    def quit(self) -> None:
        if self.p.poll() is not None:
            return
        try:
            self.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def verify(img: np.ndarray | None, info: dict) -> dict:
    """四色块逐像素比对（容差 TOLERANCE）：截到的应是测试窗口的客户区，大小一致、四块颜色都对。"""
    cw, ch = info["rects"]["client"][2], info["rects"]["client"][3]
    if img is None:
        return {"ok": False, "reason": "no image", "client": [cw, ch]}
    res = {"size": [int(img.shape[1]), int(img.shape[0])], "client": [cw, ch]}
    if (img.shape[1], img.shape[0]) != (cw, ch):
        res["ok"] = False
        return res
    top = int(round(STRIP * info["dpr"])) + 2  # 跳过顶部的黑白码
    hw, hh = cw // 2, ch // 2
    regions = [(0, top, hw, hh), (hw, top, cw, hh), (0, hh, hw, ch), (hw, hh, cw, ch)]
    quads = []
    for (x0, y0, x1, y1), bgr in zip(regions, QUAD_BGR):
        reg = img[y0 + 3:y1 - 3, x0 + 3:x1 - 3].astype(int)
        quads.append(round(float((np.abs(reg - np.array(bgr)).max(axis=2) <= TOLERANCE).mean()), 4))
    res["quads"] = quads
    res["ok"] = min(quads) >= 0.99
    return res


def grab_retry(src: screen.FrameSource, tries: int = 40) -> tuple[np.ndarray, int]:
    """取一帧；暂时没有画面时稍等再取。返回（画面, 遇到几次「暂时没有画面」）。"""
    missed = 0
    for _ in range(tries):
        try:
            return src.grab(), missed
        except screen.NoFrame:
            missed += 1
            time.sleep(0.05)
    raise RuntimeError(f"连续 {tries} 次没有画面")


def window_finder(target: dict):
    return lambda: capture.window_info(target["hwnd"])


def no_screen(*_args, **_kw):
    """实机测试不截屏幕：取帧对象万一改为截取屏幕，或要核对全黑时，这里直接报错（这一步算失败）。"""
    raise RuntimeError("实机测试不截屏幕")


def frame_source(target: dict, **kw) -> screen.FrameSource:
    return screen.FrameSource(finder=window_finder(target), screen=no_screen, **kw)


# ---------------------------------------------------------------- 截取


def run_capture(watch: Watch) -> dict:
    out: dict = {"steps": []}
    host = Host(watch, OFF_X, OFF_Y, 320, 200)
    target = {"hwnd": host.hwnd}
    src = frame_source(target)
    out["support"] = [src.support.window, src.support.borderless, src.support.build]

    def step(name: str, cmd: str | None = None, settle: float = 0.3) -> dict:
        if cmd:
            host.cmd(cmd)
        time.sleep(settle)
        info = host.cmd("info")
        img, missed = grab_retry(src, tries=10)
        rec = {"step": name, "cmd": cmd, "rects": info["rects"], "cover_above": info.get("cover_above"),
               "no_frames": missed, "sessions": src.stats["sessions"], "verify": verify(img, info),
               "method": src.state.method}
        out["steps"].append(rec)
        return rec

    try:
        step("normal", settle=0.5)
        out["border_requested"] = src.state.border_requested
        session = src._session
        out["notes"] = dict(getattr(session, "notes", {}) or {})
        step("covered", "cover on")
        host.cmd("cover off")
        step("moved", f"geom {OFF_X + 2000} {OFF_Y + 500} 320 200")
        step("resized", f"geom {OFF_X + 2000} {OFF_Y + 500} 300 180")
        step("resized_back", f"geom {OFF_X} {OFF_Y} 320 200")
        # 画面静止：系统不再送新帧，沿用最近一帧
        host.cmd("freeze")
        time.sleep(0.3)
        info = host.cmd("info")
        first, _ = grab_retry(src)
        stale_before = src.stats["stale"]
        second, _ = grab_retry(src)
        out["static"] = {"stale_added": src.stats["stale"] - stale_before, "same": bool(np.array_equal(first, second)),
                         "verify": verify(second, info)}
        host.cmd("unfreeze")
        # 最小化：取帧对象报「已最小化」，会话直接取帧没有画面；最小化后的位置也在屏幕外
        game = capture.window_info(target["hwnd"])
        offset, size = game.offset, (game.client[2], game.client[3])
        info = host.cmd("min")
        time.sleep(0.5)
        info = host.cmd("info")
        rec = {"step": "minimized", "iconic": info["iconic"], "rects": info["rects"]}
        try:
            src.grab()
            rec["frame_source"] = "image"
        except screen.NoFrame:
            rec["frame_source"] = "NoFrame"
        except RuntimeError as e:
            rec["frame_source"] = "RuntimeError"
            rec["message"] = str(e)
        session = src._session
        rec["session_open"] = session is not None
        rec["backend_none"] = [session.grab(offset, size, 0.3) is None for _ in range(2)] if session is not None else []
        out["minimized"] = rec
        sessions = src.stats["sessions"]
        restored = step("restored", "restore", settle=0.4)
        out["restored_rebuilt"] = restored["sessions"] != sessions
        # 关闭后重开：报找不到窗口；重开（新句柄）后重建会话
        host.quit()
        time.sleep(0.3)
        rec = {"step": "closed"}
        try:
            src.grab()
            rec["result"] = "image"
        except screen.NoFrame:
            rec["result"] = "NoFrame"
        except RuntimeError as e:
            rec["result"] = "RuntimeError"
            rec["message"] = str(e)
        rec["session_open"] = src.session_open
        out["closed"] = rec
        host = Host(watch, OFF_X, OFF_Y, 320, 200)
        out["reopened_new_handle"] = host.hwnd != target["hwnd"]
        target["hwnd"] = host.hwnd
        sessions = src.stats["sessions"]
        reopened = step("reopened", settle=0.5)
        out["reopened_rebuilt"] = reopened["sessions"] == sessions + 1
        out["stats"] = src.stats
        out["state"] = [src.state.method, src.state.problem, src.state.detail, src.state.step]
    finally:
        src.close()
        host.quit()
    # 对句柄 0 建截取对象：得到 CaptureError，不崩溃
    try:
        wc.WindowCapture(0, borderless=False).close()
        out["hwnd0"] = "no error"
    except wc.CaptureError as e:
        out["hwnd0"] = [e.kind, e.code, e.step]
    return out


# ---------------------------------------------------------------- 端到端


def run_e2e(watch: Watch) -> dict:
    from unittest import mock

    from advisor.data import load_db
    from advisor.session import Session, Settings
    from advisor.vision import pipeline

    db = load_db()
    rules = db.rules.get("watch", {})
    idle_s, active_s = rules.get("idle_ms", 500) / 1000, rules.get("active_ms", 300) / 1000
    labels = {s["file"]: s for s in json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))["samples"]}
    out: dict = {"idle_s": idle_s, "active_s": active_s, "samples": []}
    host = Host(watch, OFF_X, OFF_Y, *E2E_SIZE, "--frameless")
    target = {"hwnd": host.hwnd}
    src = frame_source(target)
    try:
        with mock.patch.object(capture, "exclusive_fullscreen", return_value=False):
            for name in SELECTS:
                label = labels[name]
                expected = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
                session = Session(db, Settings(hero=label["hero"]))  # 不传路径：不写设置文件
                session.game.seen_hero = label["hero"]
                proc = pipeline.FrameProcessor(db, session)
                host.cmd(f"image {SAMPLES / HUD}")
                time.sleep(0.3)
                for _ in range(2):  # 对局画面上照常识别两轮（和后台识别线程一样：还有排着的读数时紧接着取下一帧，读完等 idle）
                    while True:
                        img, _ = grab_retry(src)
                        proc.process(img, time.monotonic())
                        if not proc.pending:
                            break
                    time.sleep(idle_s)
                switched = time.perf_counter()
                host.cmd(f"image {SAMPLES / name}")
                rec: dict = {"file": name, "offer": label["offer"], "cycles": None}
                for cycle in range(1, 5):
                    img, _ = grab_retry(src)
                    events = proc.process(img, time.monotonic())
                    advice = [e for e in events if e.kind == "advice"]
                    if advice:
                        diff = np.abs(img.astype(np.int16) - expected.astype(np.int16)).max(axis=2) \
                            if img.shape == expected.shape else None
                        rec.update(cycles=cycle, seconds=round(time.perf_counter() - switched, 2),
                                   names=[e.chip.name for e in advice[0].advice.evals],
                                   same_size=img.shape == expected.shape,
                                   pixels_match=None if diff is None else round(float((diff <= TOLERANCE).mean()), 4),
                                   max_diff=None if diff is None else int(diff.max()))
                        break
                    time.sleep(active_s if proc.chip_select_visible else (0 if proc.pending else idle_s))
                out["samples"].append(rec)
        out["state"] = [src.state.method, src.state.problem]
    finally:
        src.close()
        host.quit()
    return out


# ---------------------------------------------------------------- 性能和内存


def run_perf(watch: Watch) -> dict:
    out: dict = {"size": list(PERF_SIZE)}
    host = Host(watch, OFF_X, OFF_Y, *PERF_SIZE, "--frameless")
    target = {"hwnd": host.hwnd}
    shift = [0.0]
    steps = StepClock()

    def timed_session(hwnd: int, *, borderless: bool) -> wc.WindowCapture:
        """正式的会话类，只多传分步计时（只在每秒 5 次那 10 秒里记）。"""
        return wc.WindowCapture(hwnd, borderless=borderless, steps=steps.mark)

    src = frame_source(target, backend=timed_session, clock=lambda: time.monotonic() + shift[0])
    try:
        time.sleep(1.0)
        out["before"] = memory()
        img, _ = grab_retry(src)  # 建会话
        for _ in range(20):
            img, _ = grab_retry(src)
            time.sleep(0.2)
        out["client"] = [int(img.shape[1]), int(img.shape[0])]
        del img
        out["after_open"] = memory()
        # 每秒 5 次、10 秒
        times: list[float] = []
        missed = 0
        session0 = src._session
        skipped0 = (getattr(session0, "stats", None) or {}).get("skipped", 0) if session0 is not None else 0
        steps.start()
        cyc0 = steps.now()
        cpu0, wall0 = time.process_time(), time.perf_counter()
        next_at = wall0
        while time.perf_counter() - wall0 < 10:
            start = time.perf_counter()
            steps.mark("begin")
            try:
                img = src.grab()
                times.append((time.perf_counter() - start) * 1000)
            except screen.NoFrame:
                missed += 1
            steps.mark("after")
            next_at += 0.2
            time.sleep(max(0.0, next_at - time.perf_counter()))
        wall = time.perf_counter() - wall0
        cpu_time = time.process_time() - cpu0
        cyc1 = steps.now()
        steps.active = False
        session = src._session
        skipped = None  # 中途重建过会话时不算
        if session is not None and session is session0:
            skipped = (getattr(session, "stats", None) or {}).get("skipped", 0) - skipped0
        out["five_per_second"] = {
            "grabs": len(times), "no_frames": missed, "cpu_percent": round(100 * cpu_time / wall, 2),
            "ms_p50": round(float(np.percentile(times, 50)), 2), "ms_p95": round(float(np.percentile(times, 95)), 2),
            "ms_max": round(float(max(times)), 2),
            # 按周期数算的 CPU（整个进程，CPU 的通过标准用它）；以下其余只是输出项：其中取帧线程的、每一步的耗时、
            # 每次取帧没用上就关掉的旧帧数、怎么等显卡做完拷贝（fence 为用信号等）
            "cpu_percent_cycles": round(100 * steps.ms(cyc1[1] - cyc0[1]) / 1000 / wall, 2),
            "main_thread_percent_cycles": round(100 * steps.ms(cyc1[2] - cyc0[2]) / 1000 / wall, 2),
            "steps": steps.summary(wall),
            "skipped_per_grab": round(skipped / max(1, len(times)), 2) if skipped is not None else None,
            "copy_wait": (getattr(session, "notes", None) or {}).get("copy_wait") if session is not None else None,
        }
        img = None
        # 连续 300 帧：句柄数和内存不涨
        for i in range(300):
            img, _ = grab_retry(src)
            if i == 49:
                img = None
                out["after_50"] = memory()
        img = None
        out["after_300"] = memory()
        # 空闲 30 秒关闭（把取帧对象的时钟往后拨）
        out["before_idle_close"] = memory()
        shift[0] += screen.IDLE_CLOSE_S + 1
        out["idle_closed"] = src.close_if_idle() is None and not src.session_open
        time.sleep(0.5)
        out["after_idle_close"] = memory()
        out["stats"] = src.stats
    finally:
        src.close()
        host.quit()
    return out


def main(argv: list[str]) -> int:
    wanted = [a for a in argv if a in ("capture", "e2e", "perf")] or ["capture", "e2e", "perf"]
    capture.grab_region = no_screen  # 不截屏幕（截取屏幕和全黑核对都经过它）
    capture.grab = no_screen
    watch = Watch()
    result: dict = {"build": wc.windows_build(), "virtual_screen": list(watch.screen)}
    wc.init_thread()
    try:
        for name in wanted:
            started = time.perf_counter()
            try:
                result[name] = {"run": {"capture": run_capture, "e2e": run_e2e, "perf": run_perf}[name](watch)}
            except Exception:  # noqa: BLE001  一项出错不影响其余几项，错误写进结果
                result[name] = {"error": traceback.format_exc()}
            result[name]["seconds"] = round(time.perf_counter() - started, 1)
    finally:
        wc.uninit_thread()
    result["on_screen"] = watch.on_screen
    result["foreground"] = watch.foreground
    result["checks"] = watch.checks
    print("RESULT " + json.dumps(result, ensure_ascii=False, default=str), flush=True)
    save_result(result)
    print_steps(result)
    return 0


def save_result(result: dict) -> None:
    """结果另存一份到 .tmp\\live-capture\\result-<时间>.json（存不了不影响测试）。"""
    try:
        folder = ROOT / ".tmp" / "live-capture"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("result-%Y%m%d-%H%M%S.json")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"SAVED {path}", flush=True)
    except OSError as e:
        print(f"SAVE FAILED {e}", flush=True)


def print_steps(result: dict) -> None:
    """跑了 perf 时打印分步表：每一步的墙钟（p50 / p95）、CPU（整个进程平均 / 其中取帧线程平均，毫秒）和占单核的百分比。"""
    five = ((result.get("perf") or {}).get("run") or {}).get("five_per_second")
    if not five:
        return
    print(f"PERF grabs={five['grabs']} ms_p95={five['ms_p95']} cpu_percent={five['cpu_percent']} "
          f"cpu_percent_cycles={five.get('cpu_percent_cycles')} main_thread_percent_cycles="
          f"{five.get('main_thread_percent_cycles')} skipped_per_grab={five.get('skipped_per_grab')} "
          f"copy_wait={five.get('copy_wait')}", flush=True)
    print(f"{'step':8} {'n':>4} {'wall p50':>9} {'wall p95':>9} {'cpu mean':>9} {'main mean':>10} {'share%':>7}")
    for name, row in (five.get("steps") or {}).items():
        print(f"{name:8} {row['n']:>4} {row['wall_ms_p50']:>9.3f} {row['wall_ms_p95']:>9.3f} {row['cpu_ms_mean']:>9.3f} "
              f"{row['main_ms_mean']:>10.3f} {row['share_percent']:>7.2f}", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
