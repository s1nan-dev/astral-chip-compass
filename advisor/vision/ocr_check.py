"""量这台电脑上文字识别合适的线程数：程序自己画的图上比几种线程数各读几轮，取中位数挑一个。

为什么：线程数合不合适和电脑有关。onnxruntime 的线程不保证落在哪几个核心上，大小核的电脑（例如 8 个大核加 16 个小核）
上 4 个线程可能落在小核上、还要和游戏抢核，一次识别由最慢的那个线程决定，比核心多的台式机慢好几倍。这个数只能在那台
电脑上量，所以程序在每台电脑上自己量一次，记进设置，以后都用它。

只用程序自己画的一张图：不截游戏画面、不看窗口，不联网、不写文件，调用方负责什么时候量、量出来存哪（ui/overlay_parts/
ocr_check.py）、提示栏上写什么和换线程数（chip_select.use_ocr_threads）。量不出来时一律退回默认的 4 个线程
（Result.threads 为 None）。量的时候估还要多久，交给调用方标在提示栏上（run 的 progress）。

量的做法：候选线程数各建一个引擎（自检自己建的，不碰全进程共用的那一个），各读一次热身（第一次读要加载模型，不算数），
然后轮流量 ROUNDS 轮，每轮换一个先后，抵消先建先量的引擎偏快、整体越来越慢这类漂移。读字时拿 chip_select._ENGINE_LOCK，
和真正的识别不重叠（重叠了两边都慢，量出来也不准）；拿到锁才开始计时，读完马上放锁。每读一次歇 REST_S，不连续顶满处理器。

自检结果差时（结果一般时在有大小核的电脑上也是）还要比一下是不是核用错了（big_core_gain）：CANDIDATES 的说明里写的「大小核的
电脑上 4 个线程可能落在小核上」要真比一次才知道——只用大核和不限制交替读几轮，不限制的中位毫秒 ÷ 只用大核的中位毫秒
>= GAIN_MIN（sysinfo.BIG_CORE_GAIN_MIN）才说明改得有用。
"""
from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import sysinfo
from . import chip_select

# 翻倍着试：分得再细费时间，也比不出差别（4 到 8 个线程在实测的电脑上空闲时快约两成半，有负载时约一成半）
CANDIDATES = (2, 4, 8)
ROUNDS = 5  # 每个线程数量几轮（取中位数）
MIN_ROUNDS = 3  # 超过 BUDGET_S 时至少要量了这么多整轮才下结论
BUDGET_S = 45.0  # 量到这么久还没量完就不再开新的一轮：第一次运行多花的时间也要有数
REST_S = 0.2  # 每读一次歇一下，不连续顶满处理器（占着处理器时游戏和界面都会卡）

# 比只用大核的收益（big_core_gain）：轮数、时间预算和换了以后等线程挪过去的时间
GAIN_ROUNDS = 3  # 一轮两种各读一次
GAIN_BUDGET_S = 15.0  # 过了这么久、又已有至少 2 轮就不再开新的一轮
GAIN_SETTLE_S = 0.2  # 换了核的限制以后等线程挪过去再读
CLOSE_ENOUGH = 1.15  # 慢不超过最快这么多就用少的线程：少占核心，给游戏多留
NOISY_RATIO = 2.5  # 同一个线程数最慢和最快差到这么多，算忽快忽慢、量不准

# 估还要多久（提示栏上标的，不影响量的结果）：活按份算。量的时候读一次连同歇一下算一份，建引擎加热身抵 WARMUP_SHARES 份
# （实测 0.6～0.85 秒对 0.43 秒）；还没量到东西时按每份 GUESS_SHARE_S 秒估，之后按实际花的时间估
WARMUP_SHARES = 2
GUESS_SHARE_S = 0.4

# 还没量过的电脑用几个线程：和 chip_select.OCR_THREADS 的默认值一致。读这里不读模块里的当前值（量完以后会变）
DEFAULT_THREADS = 4

# Result.reason：叫停了、量不准、太慢没量完（量出来时为空串），以及调用方另外记的出错
STOPPED = "stopped"
NOISY = "noisy"
SLOW = "slow"

# 合成图的大小：接近一次三选一送去识别的那一块（参考比例、3840 宽的窗口）
SAMPLE_WIDTH = 1568
SAMPLE_HEIGHT = 736


def candidates(cores: int) -> tuple[int, ...]:
    """这台电脑值得试的线程数：CANDIDATES 里不超过 max(min(DEFAULT_THREADS, cores), cores // 2) 的。

    取一半以上的逻辑核心容易和游戏抢，所以 16 个逻辑核心以上才试 8；核很少时不试超过核心数的。
    例：32→(2, 4, 8)，16→(2, 4, 8)，12→(2, 4)，8→(2, 4)，4→(2, 4)，3→(2)，2→(2)，1→()。"""
    cores = max(0, int(cores))
    limit = max(min(DEFAULT_THREADS, cores), cores // 2)
    return tuple(n for n in CANDIDATES if n <= limit)


def sample_image() -> np.ndarray:
    """量线程数用的合成图（BGR）：深色底、十来行字母和数字。大小和一次三选一送去识别的那一块差不多，十来段文字让认字
    那一步也有活干；耗时看图的大小和文字段数，不看写的是什么字。"""
    img = np.full((SAMPLE_HEIGHT, SAMPLE_WIDTH, 3), 28, dtype=np.uint8)
    img[:120] = (40, 30, 30)
    lines = ["CHIP SELECT 3 OF 12", "ROUND 07 PROGRESS 14", "ALPHA BLADE +25", "BETA SHIELD +40", "GAMMA HEART +15",
             "PROBABILITY 45 40 15", "REROLL 2 LEFT", "COINS 120", "STARS 3", "TASK 2 OF 4", "MAP 05 HARD", "LEVEL UP"]
    for i, text in enumerate(lines):
        x = 60 + (i % 3) * 500
        y = 60 + (i // 3) * 170 + (i % 3) * 18
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (235, 235, 225), 2, cv2.LINE_AA)
    return img


def pick(medians: dict[int, float]) -> int:
    """挑用几个线程：先取最快的，再从少的线程数往多看，第一个不超过「最快 × CLOSE_ENOUGH」的就是结果。

    例：{2: 327, 4: 228, 8: 201}→4；{2: 300, 4: 290, 8: 400}→2；{2: 500, 4: 450, 8: 300}→8。"""
    fastest = min(medians.values())
    allowed = fastest * CLOSE_ENOUGH
    for threads in sorted(medians):
        if medians[threads] <= allowed:
            return threads
    return min(medians, key=medians.get)


@dataclass(frozen=True)
class Result:
    """一次量的结果。

    threads：量出来的线程数；None 是这一次没量出来（没量、量不准、太慢），照默认的线程数用。
    ms：各线程数的中位毫秒（没量完时为空）。
    rounds：量了几整轮。
    reason：没量出来的原因（STOPPED、NOISY、SLOW；量出来时为空串）。"""

    threads: int | None
    ms: dict[int, int] = field(default_factory=dict)
    rounds: int = 0
    reason: str = ""


def first_guess(cores: int) -> float:
    """还没开始量时估要多久（秒）：提示栏上第一次标的数。候选不足两个（不用量）时是 0。"""
    plan = candidates(cores)
    return _total_shares(plan) * GUESS_SHARE_S if len(plan) >= 2 else 0.0


def _total_shares(plan: tuple[int, ...]) -> int:
    """量一次总共几份活（WARMUP_SHARES）。"""
    return len(plan) * (WARMUP_SHARES + ROUNDS)


def _estimate_left(elapsed: float, done: int, total: int, per_round: int) -> float:
    """还要多少秒：按到现在为止每份花的时间估后面的。过了 BUDGET_S 这一轮量完就停，所以最多是预算的余量加一轮。"""
    per_share = elapsed / done
    return min(per_share * (total - done), max(0.0, BUDGET_S - elapsed) + per_share * per_round)


def run(stop=None, *, progress=None, cores=None, build=None, clock=time.perf_counter, sleep=time.sleep) -> Result:
    """量一次（做法见模块说明）。

    stop：返回真时马上停（叫停、悬浮窗暂停了）。
    progress：每做完一份活调用一次，交估的还要多少秒（后台线程里调用，只记数，不碰界面）；不给时不估。
    cores：逻辑核心数，默认 os.cpu_count()。
    build：按线程数建引擎的函数，默认 chip_select.new_engine（测试换成假的）。
    clock、sleep：计时和歇息，测试换成假的。
    不接异常：建引擎、读字出错时往外抛，调用方记错误记录。不管怎么结束都把建的引擎丢掉（finally），量完内存能还回去。"""
    stop = stop or (lambda: False)
    if cores is None:
        cores = os.cpu_count() or 1
    build = build or chip_select.new_engine
    plan = candidates(cores)
    if len(plan) < 2:
        return Result(None, {}, 0, "")

    image = sample_image()
    started = clock()
    engines: dict[int, object] = {}
    samples: dict[int, list[float]] = {n: [] for n in plan}
    rounds_done = 0
    total = _total_shares(plan)
    done = 0

    def report(shares: int) -> None:
        nonlocal done
        done += shares
        if progress is not None:
            progress(_estimate_left(clock() - started, done, total, len(plan)))

    try:
        for threads in plan:
            if stop():
                return Result(None, {}, 0, STOPPED)
            engine = build(threads)
            engines[threads] = engine
            if stop():
                return Result(None, {}, 0, STOPPED)
            with chip_select._ENGINE_LOCK:
                engine(image)  # 热身：第一次读要把模型加载进内存，不算数
            sleep(REST_S)
            report(WARMUP_SHARES)
        for index in range(ROUNDS):
            shift = index % len(plan)
            for threads in plan[shift:] + plan[:shift]:
                if stop():
                    return Result(None, {}, rounds_done, STOPPED)
                with chip_select._ENGINE_LOCK:
                    begin = clock()
                    engines[threads](image)
                    samples[threads].append((clock() - begin) * 1000.0)
                sleep(REST_S)
                report(1)
            rounds_done = index + 1
            if clock() - started > BUDGET_S:
                if rounds_done >= MIN_ROUNDS:
                    break  # 够下结论了，不再开新的一轮
                return Result(None, {}, rounds_done, SLOW)
    finally:
        engines.clear()

    medians = {n: int(round(statistics.median(values))) for n, values in samples.items()}
    for values in samples.values():
        if values and max(values) > min(values) * NOISY_RATIO:
            return Result(None, medians, rounds_done, NOISY)
    return Result(pick(medians), medians, rounds_done, "")


def big_core_gain(stop=None, *, threads=DEFAULT_THREADS, progress=None, bind=None, build=None,
                  clock=time.perf_counter, sleep=time.sleep) -> float | None:
    """只用大核和不限制交替读几轮，返回「不限制的中位毫秒 ÷ 只用大核的中位毫秒」，保留一位小数。

    用来说明线程数是不是落在了小核上：比值不到 GAIN_MIN（sysinfo.BIG_CORE_GAIN_MIN）时改也没有用。bind(on) 是改本进程
    核的限制（默认 sysinfo.use_big_cores），build 是按线程数建引擎（默认 chip_select.new_engine）。每轮换先后（第一轮先
    不限制，第二轮先只用大核……），抵消越跑越慢这类漂移。先建引擎、读一次热身（不算数）；读字时拿 chip_select._ENGINE_LOCK，拿到锁才
    开始计时，读完马上放锁，和 run 一样。stop() 为真、bind 没设上、每种不足 2 次时返回 None。不管怎么结束都放开限制，
    引擎也丢掉。不接异常（调用方接）。progress(left) 交估的还要多少秒（只记数），不给时不估。"""
    stop = stop or (lambda: False)
    bind = bind or sysinfo.use_big_cores
    build = build or chip_select.new_engine
    image = sample_image()
    samples: dict[str, list[float]] = {"plain": [], "big": []}
    engine = None
    try:
        engine = build(threads)
        with chip_select._ENGINE_LOCK:
            engine(image)  # 热身：第一次读要加载模型，不算数
        sleep(REST_S)
        started = clock()
        rounds_done = 0

        def measure(big: bool) -> bool:
            if not bind(big):
                return False
            sleep(GAIN_SETTLE_S)  # 换完等线程挪过去
            with chip_select._ENGINE_LOCK:
                begin = clock()
                engine(image)
                elapsed = (clock() - begin) * 1000.0
            samples["big" if big else "plain"].append(elapsed)
            sleep(REST_S)
            return True

        for index in range(GAIN_ROUNDS):
            if stop():
                return None
            first_big = index % 2 == 1  # 第一轮先不限制，第二轮先只用大核，轮流抵消先后带来的偏差
            for big in ((True, False) if first_big else (False, True)):
                if stop():
                    return None
                if not measure(big):
                    return None
            rounds_done = index + 1
            if progress is not None:
                spent = clock() - started
                average = spent / rounds_done
                progress(max(0.0, (GAIN_ROUNDS - rounds_done) * average))
            if clock() - started > GAIN_BUDGET_S and rounds_done >= 2:
                break
        if len(samples["plain"]) < 2 or len(samples["big"]) < 2:
            return None
        big_ms = statistics.median(samples["big"])
        if big_ms <= 0:
            return None
        return round(statistics.median(samples["plain"]) / big_ms, 1)
    finally:
        bind(False)  # 不管怎么结束都放开限制
        engine = None
