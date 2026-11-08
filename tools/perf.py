"""实测识别各阶段的耗时、CPU 占用和进程内存。

默认只读 samples/ 里的截图，不截屏、不碰游戏：
  ① 各阶段单独量：预筛、三选一识别、读轮次、读任务栏、认角色、读星数、选角界面判断、离开对局后的整幅识别、对局外画面的解析
  ② 按 rules.toml [watch] 的节奏，用和悬浮窗相同的 FrameProcessor 实跑几种场景，各跑 --seconds 秒，
     量整个进程的 CPU 时间（含文字识别的多线程）÷ 经过的时间，并数各项识别实际做了几次。
     截图换成「复制一份样本图」，截图本身（mss）的开销不在里面，另外加。
     样本是静止的截图；「每帧都有明显变化」一项把战斗画面每隔一帧左右平移 5%，模拟画面一直在动的最坏情况。
  ③ 三选一出现到给出推荐的延迟：按悬浮窗的节奏先看几秒战斗画面，在随机时刻换成三选一，量到推荐出来用了多久。
  ④ 进程内存：载入文字识别模型、角色照、星数模板前后各记一次；再建一个悬浮窗（offscreen）量 Qt 的部分。
加 --capture 时才对着游戏窗口截图 30 次，量 mss 截图的耗时（只计时，不保存、不显示画面；要先开游戏）。
OpenCV 和悬浮窗的识别线程一样只用 1 个线程（pipeline.limit_cv_threads，全进程的设置），量出来的 CPU 才和实际一致。

用法：.venv\\Scripts\\python tools\\perf.py [--quick] [--seconds 20] [--trials 8] [--unchanged-ms 毫秒] [--capture]
要识别样本：用 samples 里的四张 4K 截图（开头的 SELECT 三选一、HUD 对局画面、TASKS 任务栏开着、BATTLE 战斗画面，按文件名取；
两张对局外画面 OUT_OF_GAME 有就量、没有就跳过）。识别样本不随仓库发布（准备方法见 docs/测试指南.md「准备识别样本」），
缺了先说明缺哪个文件，以退出码 2 结束；换成自己的截图时改这几个常量。
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import os
import random
import statistics
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from advisor.data import load_db  # noqa: E402
from advisor.session import Session, Settings  # noqa: E402
from tools.eval_common import need_samples  # noqa: E402

SAMPLES = ROOT / "samples"
SELECT = SAMPLES / "20261002_013522_chipselect_levelup_star1_badge_on.png"  # 凛，1 星，第 3 轮
HUD = SAMPLES / "20261002_012918_hud_round1.png"  # 凛，对局画面
TASKS = SAMPLES / "20261002_012947_task_panel.png"  # 任务栏开着
BATTLE = SAMPLES / "20261002_021351_battle_scene.png"  # 战斗画面：读不到轮次
# 对局外画面（screens.parse_out_of_game）：结算页只解析文字；组队房间还要在难度名旁边找勾选框，最慢
OUT_OF_GAME = {
    "结算页": SAMPLES / "20261003_132524_result_stats_chips_2560x1369.jpg",
    "组队房间": SAMPLES / "20261003_124442_lobby_room_nightmare_2560x1369.jpg",
}
HERO = "凛"
SHAKE = 0.05  # 「每帧都有明显变化」：每隔一帧把画面左右平移窗口宽度的这个比例
WARMUP = 4.0  # 测延迟时先看几秒战斗画面，再换成三选一
# 场景模拟时统计调用次数的识别函数（advisor.vision.pipeline 里的名字）
COUNTED = {
    "read_chip_select": "三选一",
    "read_hud": "轮次",
    "is_select_screen": "选角",
    "read_hero": "角色",
    "read_own_stars": "星数",
    "read_pregame": "对局外画面",  # 离开对局后的整幅识别，管线一律用它：地图名、对局外画面、畸变组件
    "read_players": "队友",
    "read_chip_panel": "筹码面板",
}


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _counters() -> _MemoryCounters:
    c = _MemoryCounters()
    c.cb = ctypes.sizeof(c)
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c


def memory_mb() -> tuple[float, float]:
    """（工作集, 专用内存）MB。工作集 ≈ 任务管理器「详细信息」里的内存；专用内存含只占地址、没真正用上的部分。"""
    gc.collect()
    c = _counters()
    return c.WorkingSetSize / 2**20, c.PrivateUsage / 2**20


def peak_mb() -> float:
    """到目前为止工作集的峰值（MB）。"""
    return _counters().PeakWorkingSetSize / 2**20


def load(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def measure(fn, runs: int = 5) -> tuple[float, float]:
    """跑 runs 次，返回（墙上时间中位数, CPU 时间中位数），单位毫秒。CPU 时间含所有线程。"""
    walls, cpus = [], []
    for _ in range(runs):
        w0, c0 = time.perf_counter(), time.process_time()
        fn()
        walls.append((time.perf_counter() - w0) * 1000)
        cpus.append((time.process_time() - c0) * 1000)
    return statistics.median(walls), statistics.median(cpus)


def stage_table(db, images) -> None:
    from advisor.vision import pipeline
    from advisor.vision.chip_select import looks_like_overlay_screen, name_strip, read_chip_select, reread_selected
    from advisor.vision.hero import read_hero
    from advisor.vision.hud import is_select_screen, read_hud, read_pregame
    from advisor.vision.pipeline import _thumb
    from advisor.vision.stars import read_own_stars

    from advisor.vision.window_capture import to_bgr

    sel, hud, tasks, battle = images["select"], images["hud"], images["tasks"], images["battle"]
    seen = read_chip_select(sel, db)
    strip = name_strip(sel, seen)
    bgra = np.zeros((sel.shape[0], sel.shape[1], 4), np.uint8)
    rows = [
        # 直接截取读回时的颜色转换（按段单线程转，和悬浮窗相同）；读回的其余几步要显卡，这里量不了
        ("截取后的颜色转换（BGRA→BGR，window_capture.to_bgr）", lambda: to_bgr(bgra)),
        ("预筛：像不像三选一（每帧）", lambda: looks_like_overlay_screen(hud)),
        ("三选一完整识别（新出现、刷新后）", lambda: read_chip_select(sel, db)),
        ("同一个三选一只查绿框（其余各帧）", lambda: reread_selected(sel, seen, strip)),
        ("读轮次（每 3 秒）", lambda: read_hud(hud, db, full=False)),
        ("读轮次 + 任务栏（地图待识别时每 3 秒）", lambda: read_hud(tasks, db, full=True)),
        ("认角色（未识别时每 3 秒，识别后每 20 秒）", lambda: read_hero(hud, db)),
        ("读星数（对局画面每 3 秒，三选一最多 3 帧）", lambda: read_own_stars(hud, HERO)),
        ("选角界面判断（读不到轮次时每 3 秒）", lambda: is_select_screen(battle, db)),
        ("离开对局后的整幅识别（地图名、对局外画面、畸变组件；画面变了才做）", lambda: read_pregame(battle, db, 1280)),
        ("画面缩略图（加载画面检查前）", lambda: _thumb(battle)),
    ]
    fingerprint = getattr(pipeline, "_fingerprint", None)
    if fingerprint is not None:
        rows.append(("画面指纹（过了预筛的帧、读不到轮次时）", lambda: fingerprint(battle, (0.0, 0.0, 1.0, 0.62))))
    panel = getattr(pipeline, "read_chip_panel", None)
    if panel is not None:
        rows.append(("「查看筹码」面板（没有面板时，每 3 秒）", lambda: panel(hud, db, hero_key=HERO)))
    # 对局外画面：解析复用选角界面判断、加载画面找地图名那两次文字识别的结果，这里只量解析本身
    from advisor.vision import io
    from advisor.vision.screens import parse_out_of_game

    for label, path in OUT_OF_GAME.items():
        if path.exists():
            img = load(path)
            lines = io.ocr(img, (0, 0, img.shape[1], img.shape[0]), 1280)
            rows.append((f"对局外画面解析（{label}，不含文字识别）",
                         lambda img=img, lines=lines: parse_out_of_game(img, lines, db)))
    # 推荐计算本身：地图已知时只算一张地图；地图待识别时按候选地图逐张计算再平均
    from advisor.engine import advise
    from advisor.pool import State

    offer = [n for n in (seen.names if seen is not None else ()) if n in db.chips]
    if len(offer) == 3:
        known = State(hero=HERO, star=1, round=4, map=db.active_maps()[0], owned=(), rerolls=2)
        unknown = State(hero=HERO, star=1, round=4, map=None, owned=(), rerolls=2)
        rows.append(("推荐计算（地图已知）", lambda: advise(db, known, offer)))
        rows.append(("推荐计算（地图待识别，按候选地图平均）", lambda: advise(db, unknown, offer)))
    print(f"\n① 各阶段耗时（{sel.shape[1]}x{sel.shape[0]} 样本，各跑 5 次取中位数；CPU 时间含文字识别的多线程）")
    print(f"  {'阶段':<40}{'耗时 ms':>10}{'CPU ms':>10}")
    for name, fn in rows:
        wall, cpu = measure(fn)
        print(f"  {name:<40}{wall:>10.1f}{cpu:>10.1f}")


def new_processor(db, map_known: bool, hero_known: bool = True):
    """和悬浮窗一样的 FrameProcessor：凛，第 1 轮，0 星（读到过），角色已识别。"""
    from advisor.vision.pipeline import FrameProcessor

    settings = Settings(hero=HERO, round=1, star=0, star_read=True)
    if map_known:
        settings.map, settings.map_source, settings.map_confidence = "异变图书馆", "tasks", "high"
    session = Session(db, settings)
    if hero_known:
        session.game.seen_hero = HERO
    return FrameProcessor(db, session, threading.Lock())


def pace(db) -> tuple[float, float]:
    """悬浮窗两帧之间睡多久（秒）：（平时, 三选一期间）。识别流程还有排到下一帧的读数时不睡，见 wait。"""
    watch = db.rules.get("watch", {})
    return watch.get("idle_ms", 500) / 1000, watch.get("active_ms", 300) / 1000


def wait(processor, idle_s: float, active_s: float) -> float:
    """这一帧识别完以后悬浮窗的识别线程等多久（秒），同 Watcher._loop：三选一显示着按 active_ms；还有排到下一帧的读数
    （FrameProcessor.pending：平时一帧只读一项）时不等，紧接着取下一帧；其余按 idle_ms。"""
    if processor.chip_select_visible:
        return active_s
    return 0.0 if getattr(processor, "pending", False) is True else idle_s


class count_calls:
    """场景模拟期间数一数各项识别实际做了几次（包一层 advisor.vision.pipeline 里的函数）。"""

    def __enter__(self) -> dict[str, int]:
        from advisor.vision import pipeline

        self.real = {name: getattr(pipeline, name) for name in COUNTED if callable(getattr(pipeline, name, None))}
        self.counts = dict.fromkeys(COUNTED, 0)

        def wrap(name, fn):
            def call(*args, **kwargs):
                self.counts[name] += 1
                return fn(*args, **kwargs)

            return call

        for name, fn in self.real.items():
            setattr(pipeline, name, wrap(name, fn))
        return self.counts

    def __exit__(self, *exc) -> None:
        from advisor.vision import pipeline

        for name, fn in self.real.items():
            setattr(pipeline, name, fn)


def simulate(db, frames: list[np.ndarray], seconds: float, map_known: bool) -> tuple[float, int, float, dict]:
    """按 [watch] 的节奏实跑 FrameProcessor，返回（CPU 占用 % 单核, 帧数, 平均每帧间隔 秒, 各项识别做了几次）。

    frames 轮流当作每一帧的画面（只有一张就是静止画面）。
    """
    processor = new_processor(db, map_known)
    idle_s, active_s = pace(db)
    n = 0
    with count_calls() as counts:
        w0, c0 = time.perf_counter(), time.process_time()
        while time.perf_counter() - w0 < seconds:
            frame = frames[n % len(frames)].copy()  # 代替截图：至少有一次整幅画面的内存复制
            processor.process(frame, time.monotonic())
            n += 1
            time.sleep(wait(processor, idle_s, active_s))
        wall = time.perf_counter() - w0
        cpu = (time.process_time() - c0) / wall * 100
    return cpu, n, wall / n, dict(counts)


def latency(db, battle: np.ndarray, select: np.ndarray, trials: int) -> list[float]:
    """战斗画面上忽然换成三选一，到给出推荐用了多少秒。每次用新的 Session，切换时刻在一帧的周期里随机取。
    战斗画面上读不到轮次：当作开始前刚在对局画面上读到过轮次（实际对局里战斗之前是对局画面），三选一出现时手上的轮次
    算新，先给推荐、下一帧核对（不等读完左上角，三选一一出来就给推荐）。"""
    idle_s, active_s = pace(db)
    rng = random.Random(1)
    out = []
    for _ in range(trials):
        processor = new_processor(db, map_known=True)
        processor._mark_seen_at = time.monotonic()
        switch = time.perf_counter() + WARMUP + rng.uniform(0, idle_s + 0.5)
        while True:
            img = select if time.perf_counter() >= switch else battle
            events = processor.process(img.copy(), time.monotonic())
            if any(ev.kind == "advice" for ev in events):
                out.append(time.perf_counter() - switch)
                break
            time.sleep(wait(processor, idle_s, active_s))
    return out


def capture_bench(runs: int = 30) -> None:
    from advisor.vision.capture import client_rect, find_window, grab

    try:
        left, top, width, height = client_rect(find_window())
    except RuntimeError as e:
        print(f"\n截图耗时：{e}")
        return
    grab()  # 第一次有初始化开销，不计
    walls, cpus = [], []
    for _ in range(runs):
        w0, c0 = time.perf_counter(), time.process_time()
        grab()  # 只计时，画面立即丢掉
        walls.append((time.perf_counter() - w0) * 1000)
        cpus.append((time.process_time() - c0) * 1000)
    print(f"\n截图（mss，游戏窗口客户区 {width}x{height}，{runs} 次）：耗时中位 {statistics.median(walls):.1f} ms"
          f"（最慢 {max(walls):.1f}），CPU 中位 {statistics.median(cpus):.1f} ms")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只量各阶段耗时，不跑场景模拟")
    ap.add_argument("--seconds", type=float, default=20, help="每个场景模拟多少秒")
    ap.add_argument("--trials", type=int, default=8, help="三选一出现到给出推荐的延迟测几次")
    ap.add_argument("--unchanged-ms", type=int, default=None,
                    help="临时改 [watch] unchanged_max_ms（画面没变时最多隔多久重新识别），用来比较不同取值")
    ap.add_argument("--capture", action="store_true", help="对着游戏窗口截图，量 mss 的耗时（要先开游戏）")
    args = ap.parse_args()
    need_samples(*(p.name for p in (SELECT, HUD, TASKS, BATTLE)))

    from advisor.vision.pipeline import limit_cv_threads

    limit_cv_threads()  # 和悬浮窗的识别线程相同（Watcher._loop 开头）
    print(f"CPU 逻辑核心数：{os.cpu_count()}；OpenCV 线程数：{cv2.getNumThreads()}（和悬浮窗的识别线程相同）")
    mem = [("启动、载入 numpy / OpenCV", memory_mb())]
    db = load_db()
    if args.unchanged_ms is not None:
        db.rules.setdefault("watch", {})["unchanged_max_ms"] = args.unchanged_ms
    mem.append(("载入资料库", memory_mb()))
    images = {"select": load(SELECT), "hud": load(HUD), "tasks": load(TASKS), "battle": load(BATTLE)}
    mem.append(("读入 4 张 4K 样本图（只有测试时才有）", memory_mb()))

    from advisor.vision.chip_select import _engine
    from advisor.vision.hero import read_hero
    from advisor.vision.stars import read_own_stars

    _engine()(images["hud"][:200, :400])
    mem.append(("载入文字识别模型（第一次识别时）", memory_mb()))
    read_hero(images["hud"], db)
    mem.append(("载入角色照模板（第一次认角色时）", memory_mb()))
    read_own_stars(images["hud"], HERO)
    mem.append(("载入星数模板和头像（第一次读星数时）", memory_mb()))

    stage_table(db, images)

    if not args.quick:
        from advisor.vision import pipeline

        watch = db.rules.get("watch", {})
        print(f"\n② 按 [watch] 的节奏实跑（每个场景 {args.seconds:.0f} 秒；不含截图本身的开销）")
        if hasattr(pipeline, "UNCHANGED_MAX_MS"):
            print(f"  画面没变时最多隔 {watch.get('unchanged_max_ms', pipeline.UNCHANGED_MAX_MS)} 毫秒重新识别一次")
        print("  后几列是这段时间里各项识别实际做了几次")
        print(f"  {'场景':<34}{'CPU（单核 %）':>12}{'帧数':>6}{'每帧间隔 s':>12}  "
              + " ".join(f"{label:>4}" for label in COUNTED.values()))
        battle = images["battle"]
        shaken = np.roll(battle, round(SHAKE * battle.shape[1]), axis=1)
        scenes = [
            ("对局画面，角色、地图都已识别", [images["hud"]], True),
            ("对局画面，地图待识别、任务栏没开", [images["hud"]], False),
            ("对局画面，任务栏开着（地图随即识别）", [images["tasks"]], False),
            ("战斗画面，地图已识别", [battle], True),
            ("战斗画面，地图待识别", [battle], False),
            ("战斗画面，每帧都有明显变化（最坏情况）", [battle, shaken], True),
            ("三选一期间", [images["select"]], True),
        ]
        for name, frames, map_known in scenes:
            cpu, n, interval, counts = simulate(db, frames, args.seconds, map_known)
            print(f"  {name:<34}{cpu:>12.1f}{n:>6}{interval:>12.2f}  "
                  + " ".join(f"{counts[k]:>4}" for k in COUNTED))
            mem.append((f"跑完「{name}」", memory_mb()))

        times = latency(db, battle, images["select"], args.trials)
        print(f"\n③ 战斗画面上出现三选一，到给出推荐（{len(times)} 次，切换时刻随机）："
              f"中位 {statistics.median(times):.2f} 秒，最慢 {max(times):.2f} 秒，最快 {min(times):.2f} 秒")
        print("  " + " ".join(f"{t:.2f}" for t in times))

    # 悬浮窗（Qt）本身的内存：offscreen 平台建一个，不显示
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
    from PySide6.QtWidgets import QApplication

    from advisor.ui.overlay import Overlay
    from advisor.ui.overlay_parts.app import OverlaySession, UiSettings

    app = QApplication.instance() or QApplication([])
    window = Overlay(db, OverlaySession(db, UiSettings(hero=HERO)), threading.Lock())
    window.fit_height()
    app.processEvents()
    mem.append(("再建悬浮窗（Qt）", memory_mb()))

    print("\n④ 进程内存（MB，工作集 / 专用；4 张样本图约占 95 MB，实际使用时没有）")
    for name, (ws, private) in mem:
        print(f"  {name:<36}{ws:>8.0f}{private:>8.0f}")
    print(f"  工作集峰值（识别一帧 4K 画面时的临时内存）{peak_mb():>8.0f}")

    if args.capture:
        capture_bench()


if __name__ == "__main__":
    main()
