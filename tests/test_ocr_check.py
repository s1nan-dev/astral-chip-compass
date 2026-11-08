"""文字识别线程数的自检（advisor/vision/ocr_check.py 和 advisor/ui/overlay_parts/ocr_check.py）：

候选线程数、挑法（含刚好等于 1.15 倍的边界）、量的时候轮换先后、热身不计时、读字时拿着引擎锁、计时不含等锁、
建引擎 / 热身 / 量的各阶段叫停都停得下、时间预算、忽快忽慢、候选不足、出错时把建好的引擎丢掉；
合成图用真引擎读得出 8 段以上（没有模型文件时跳过）；打包自检的「识别合成图」读的是这张图，段数不够时报错；
OcrCheck 什么时候量、量完写什么（有效的记录只换线程数、核心数变了重量、次数用满不再量、各种条件不满足不量、两开时另一张
在对局里也不量、STOPPED 不算次数、量出来换线程数、出错记错误记录、退出时等）；导入悬浮窗不带进识别模块和 OpenCV；
UiSettings.ocr_check 的读写和清理，记录上这台电脑比出来的只用大核的收益（recorded_gain）；提示栏：量的时候估的剩余时间、绿字那一行、量完的结果按读一次的快慢分档、哪些自己收起、
哪些留到玩家关掉、对局进行中先不显示、第二张不显示；结果差时写的原因、建议做法和问法（句子和查原因那边对得上、繁体的叫法），
「是」「否」排在文字下面，点了以后真走悬浮窗的方法。

假的引擎、假的时钟和假的歇息，只有读合成图那一条用真的。提示栏的部分离屏，不显示窗口。
"""
import gc
import json
import math
import os
import re
import subprocess
import sys
import threading
import traceback
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from PySide6.QtCore import QPoint

from advisor import diagnostics, sysinfo
from advisor.data import load_db
from advisor.ui import lang, theme
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import notices as notices_mod
from advisor.ui.overlay_parts import ocr_check as ui_check
from advisor.ui.overlay_parts import wording
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.ocr_check import OcrNote
from advisor.ui.overlay_parts.wording import OCR_CHECK_RETRY, OCR_CHECK_SOON
from advisor.ui.services import Services
from advisor.vision import chip_select, ocr_check, ocr_reason
from advisor.vision.ocr_reason import Reason
from tests.support import sandbox
from tests.support.overlay_cases import APP  # noqa: F401  离屏的 QApplication

HAVE_MODELS = False
try:  # 只看模型文件在不在，不导入原版（同 tests/test_rapidocr_lite.py）
    from rapidocr_lite.config import CLS_MODEL_FILE, DET_MODEL_FILE, REC_MODEL_FILE, installed_models_dir

    _MODELS = installed_models_dir()
    HAVE_MODELS = _MODELS is not None and all(
        (_MODELS / name).is_file() for name in (DET_MODEL_FILE, CLS_MODEL_FILE, REC_MODEL_FILE))
except Exception:  # noqa: BLE001  模型配置文件都没有时当作没有模型
    pass
MODELS_SKIP = "没有文字识别模型文件（assets/ 里没下载下来）"


class FakeClock:
    """假的时钟：每取一次往前走 step 秒；引擎可以 spend 更多（模拟读得慢、等锁）。"""

    def __init__(self, step=0.001):
        self.now = 0.0
        self.step = step
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.now += self.step
        return self.now

    def spend(self, ms):
        self.now += ms / 1000.0


class SlowLock:
    """假的引擎锁：进去时先花 wait_ms 毫秒（模拟等另一路读完），记录拿着锁的有几个。"""

    def __init__(self, clock, wait_ms=0.0):
        self.inner = threading.Lock()
        self.clock = clock
        self.wait_ms = wait_ms
        self.held = 0
        self.most = 0

    def __enter__(self):
        self.inner.acquire()
        if self.wait_ms:
            self.clock.spend(self.wait_ms)
        self.held += 1
        self.most = max(self.most, self.held)
        return self

    def __exit__(self, *exc):
        self.held -= 1
        self.inner.release()
        return False


class FakeEngine:
    """假的文字识别引擎：记下每次读的线程数、拿没拿到锁、读的是不是同一张图，按 ms 花掉一点时间。"""

    def __init__(self, threads, owner, lock):
        self.threads = threads
        self.owner = owner
        self.lock = lock
        self.reads = 0

    def __call__(self, image):
        self.owner.calls.append((self.threads, image))
        busy = self.lock.locked() if hasattr(self.lock, "locked") else self.lock.held > 0
        self.owner.locked.append(busy)
        ms = self.owner.ms(self.threads, self.reads)
        self.reads += 1
        if ms:
            self.owner.clock.spend(ms)


class FakeBuild:
    """假的建引擎：记下按几个线程建的、建出来的引擎；ms(线程数, 第几次读) 是这一次读要花多少毫秒。"""

    def __init__(self, clock, lock=None, ms=0.0):
        self.clock = clock
        self.lock = lock
        self.ms = ms if callable(ms) else (lambda threads, read: ms)
        self.built = []
        self.builds = []
        self.calls = []  # 每次读：(线程数, 图)
        self.locked = []

    def __call__(self, threads):
        self.builds.append(threads)
        engine = FakeEngine(threads, self, self.lock)
        self.built.append(engine)
        return engine

    def order(self, warmups: int) -> list[int]:
        """去掉每个引擎的热身那几次以后，各次读的线程数（照先后）。"""
        counts = {threads: 0 for threads, _ in self.calls}
        out = []
        for threads_used, _ in self.calls:
            if counts[threads_used] < warmups:
                counts[threads_used] += 1
                continue
            out.append(threads_used)
        return out


class CandidatesTest(unittest.TestCase):
    def test_table(self):
        cases = {32: (2, 4, 8), 16: (2, 4, 8), 12: (2, 4), 8: (2, 4), 4: (2, 4), 3: (2,), 2: (2,), 1: ()}
        for cores, want in cases.items():
            with self.subTest(cores=cores):
                self.assertEqual(ocr_check.candidates(cores), want)

    def test_default_threads_is_constant(self):
        """默认线程数写成模块常量，不读 chip_select.OCR_THREADS 的当前值（量完以后会变）。"""
        self.assertEqual(ocr_check.DEFAULT_THREADS, 4)
        with mock.patch.object(chip_select, "OCR_THREADS", 8):
            self.assertEqual(ocr_check.candidates(16), (2, 4, 8))


class PickTest(unittest.TestCase):
    def test_table(self):
        cases = (
            ({2: 327, 4: 228, 8: 201}, 4),
            ({2: 300, 4: 290, 8: 400}, 2),
            ({2: 500, 4: 450, 8: 300}, 8),
        )
        for medians, want in cases:
            with self.subTest(medians=medians):
                self.assertEqual(ocr_check.pick(medians), want)

    def test_boundary(self):
        """刚好等于最快 × 1.15 的算「差不多」，用少的线程；多出一点就不用它。"""
        fastest = 200.0
        exact = fastest * ocr_check.CLOSE_ENOUGH
        self.assertEqual(ocr_check.pick({2: exact, 4: fastest}), 2)
        self.assertEqual(ocr_check.pick({2: exact + 0.01, 4: fastest}), 4)


class RunTest(unittest.TestCase):
    def setUp(self):
        for name in ("_ENGINE_LOCK", "OCR_THREADS"):
            self.addCleanup(setattr, chip_select, name, getattr(chip_select, name))

    def build(self, cores=16, ms=0.0, stop=None, lock=None, clock=None, sleep=None):
        clock = clock or FakeClock()
        build = FakeBuild(clock, lock=lock or chip_select._ENGINE_LOCK, ms=ms)
        result = ocr_check.run(stop, cores=cores, build=build, clock=clock,
                               sleep=sleep or (lambda seconds: None))
        return result, build, clock

    def test_rotation(self):
        """轮流量：每轮换一个先后（第 1 轮 2,4,8，第 2 轮 4,8,2，第 3 轮 8,2,4……），抵消先建先量的引擎偏快。"""
        result, build, _ = self.build(cores=16)
        self.assertEqual(result.threads, 2)
        self.assertEqual(result.rounds, ocr_check.ROUNDS)
        self.assertEqual(build.order(warmups=1)[:9], [2, 4, 8, 4, 8, 2, 8, 2, 4])

    def test_warmup_not_timed(self):
        """每个引擎建好先读一次热身（不计时），之后才量：时钟只用在量的时候。"""
        result, build, clock = self.build(cores=16)
        reads = ocr_check.ROUNDS * len(ocr_check.candidates(16))
        # 起始那一次 + 每次读的起止 + 每轮量完看一次预算
        self.assertEqual(clock.calls, 1 + 2 * reads + ocr_check.ROUNDS)
        self.assertEqual(result.ms[2], 1)  # 时钟每取一次走 1 毫秒，起止两次之间是 1
        self.assertEqual(len(build.built), 3)

    def test_lock_held_and_wait_not_counted(self):
        """每次读都拿着引擎锁；等锁的时间不算进读数（拿到锁才开始计时）。"""
        clock = FakeClock()
        lock = SlowLock(clock, wait_ms=5000.0)
        chip_select._ENGINE_LOCK = lock
        result, build, _ = self.build(cores=8, clock=clock, lock=lock)
        self.assertGreaterEqual(len(build.locked), 4)
        self.assertTrue(all(build.locked))
        self.assertEqual(lock.most, 1)
        self.assertLess(max(result.ms.values()), 100)  # 每次 5 秒的等锁时间没有算进去

    def test_stop_before_build(self):
        result, build, _ = self.build(cores=16, stop=lambda: True)
        self.assertEqual(result, ocr_check.Result(None, {}, 0, ocr_check.STOPPED))
        self.assertEqual(build.built, [])

    def test_stop_before_warmup(self):
        """建好第一个引擎以后、热身之前叫停：停得下，已建的引擎丢掉。"""
        calls = []

        def stop():
            calls.append(1)
            return len(calls) == 2  # 第一个引擎建好、热身之前

        result, build, _ = self.build(cores=16, stop=stop)
        self.assertEqual(result, ocr_check.Result(None, {}, 0, ocr_check.STOPPED))
        self.assertEqual(len(build.built), 1)
        self.assertEqual(build.built[0].reads, 0)

    def test_stop_between_reads(self):
        """量的中途叫停：返回已经量完的整轮数。"""
        calls = []

        def stop():
            calls.append(1)
            return len(calls) > 7  # 建、热身 6 次检查，第一轮第一次读也放行

        result, _, _ = self.build(cores=16, stop=stop)
        self.assertEqual(result.reason, ocr_check.STOPPED)
        self.assertEqual(result.rounds, 0)
        self.assertEqual(result.ms, {})

    @staticmethod
    def per_read(ms):
        """热身不花时间，量的时候每次花 ms 毫秒（预算按整次量的时间算）。"""
        return lambda threads, read: 0.0 if read == 0 else ms

    def test_budget_slow(self):
        """每读一次 12 秒：两轮就超过预算，不到 3 整轮，算太慢没量完。"""
        result, _, _ = self.build(cores=8, ms=self.per_read(12000.0))
        self.assertEqual(result.reason, ocr_check.SLOW)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.ms, {})

    def test_budget_enough_rounds(self):
        """每读一次 8 秒：第 3 整轮超过预算，已经够了，照样下结论。"""
        result, _, _ = self.build(cores=8, ms=self.per_read(8000.0))
        self.assertEqual(result.rounds, 3)
        self.assertIn(result.threads, (2, 4))
        self.assertEqual(result.reason, "")

    def test_noisy(self):
        """同一个线程数最慢和最快差到 2.5 倍：算忽快忽慢、量不准。"""
        def ms(threads, read):
            return 10.0 if threads == 2 and read in (1, 2) else 10.0 if threads != 2 else 40.0

        result, _, _ = self.build(cores=8, ms=ms)
        self.assertEqual(result.reason, ocr_check.NOISY)
        self.assertIsNone(result.threads)
        self.assertTrue(result.ms)

    def test_candidates_too_few(self):
        """核太少、只有一个候选时什么都不量。"""
        result, build, _ = self.build(cores=2)
        self.assertEqual(result, ocr_check.Result(None, {}, 0, ""))
        self.assertEqual(build.built, [])

    def test_same_image(self):
        """各引擎读的都是同一张合成图（只画一次）。"""
        result, build, _ = self.build(cores=8)
        images = [image for _, image in build.calls]
        self.assertTrue(images)
        for image in images[1:]:
            self.assertIs(image, images[0])
        self.assertEqual(images[0].shape, (ocr_check.SAMPLE_HEIGHT, ocr_check.SAMPLE_WIDTH, 3))

    def test_build_error_drops_engines(self):
        """建引擎出错时往外抛；已经建好的引擎丢掉（没有别处的引用，能被回收）。"""
        engines = []

        class Engine:
            def __init__(self, threads):
                self.threads = threads

            def __call__(self, image):
                return None

        def build(threads):
            if engines:
                raise RuntimeError("建不了")
            engine = Engine(threads)
            engines.append(weakref.ref(engine))
            return engine

        try:
            ocr_check.run(cores=8, build=build, sleep=lambda seconds: None)
        except RuntimeError as exc:
            traceback.clear_frames(exc.__traceback__)
        gc.collect()
        self.assertTrue(engines)
        self.assertIsNone(engines[0]())


class ProgressTest(unittest.TestCase):
    """量的时候估还要多久（run 的 progress、first_guess）。"""

    def test_first_guess(self):
        """还没量到东西时按份估：16 个逻辑核心三个候选，8 个两个候选，候选不足（不用量）时是 0。"""
        per_candidate = (ocr_check.WARMUP_SHARES + ocr_check.ROUNDS) * ocr_check.GUESS_SHARE_S
        self.assertAlmostEqual(ocr_check.first_guess(16), 3 * per_candidate)
        self.assertAlmostEqual(ocr_check.first_guess(8), 2 * per_candidate)
        self.assertEqual(ocr_check.first_guess(2), 0.0)

    def test_estimate(self):
        """按到现在为止每份花的时间估；过了预算这一轮量完就停，最多是预算的余量加一轮。"""
        self.assertAlmostEqual(ocr_check._estimate_left(3.0, 10, 21, 3), 3.3)
        self.assertAlmostEqual(ocr_check._estimate_left(40.0, 10, 21, 3), 5.0 + 4.0 * 3)
        self.assertAlmostEqual(ocr_check._estimate_left(50.0, 10, 21, 3), 5.0 * 3)
        self.assertEqual(ocr_check._estimate_left(3.0, 21, 21, 3), 0.0)

    def run_marks(self, ms, cores=16, stop=None):
        clock = FakeClock(step=0.0)
        build = FakeBuild(clock, lock=chip_select._ENGINE_LOCK, ms=ms)
        marks = []
        result = ocr_check.run(stop, progress=marks.append, cores=cores, build=build, clock=clock,
                               sleep=lambda seconds: clock.spend(seconds * 1000.0))
        return result, marks

    def test_counts_down(self):
        """热身正好抵两份、读一次连同歇一下一份时估得正好：每做完一份交一次，量完时是 0。"""
        share_ms = 300.0

        def ms(threads, read):
            warm = share_ms * ocr_check.WARMUP_SHARES - ocr_check.REST_S * 1000.0
            return warm if read == 0 else share_ms - ocr_check.REST_S * 1000.0

        result, marks = self.run_marks(ms)
        plan = ocr_check.candidates(16)
        total = len(plan) * (ocr_check.WARMUP_SHARES + ocr_check.ROUNDS)
        done = [ocr_check.WARMUP_SHARES * (i + 1) for i in range(len(plan))]
        done += [done[-1] + i + 1 for i in range(ocr_check.ROUNDS * len(plan))]
        self.assertEqual(result.reason, "")
        self.assertEqual(len(marks), len(done))
        for left, shares in zip(marks, done):
            self.assertAlmostEqual(left, (total - shares) * share_ms / 1000.0, places=6)
        self.assertAlmostEqual(marks[-1], 0.0, places=6)

    def test_budget_keeps_estimate_short(self):
        """读得很慢时估的不超过「预算的余量加一轮」：量到预算就收尾，不写一个到不了的数。"""
        reads = {"n": 0}

        def ms(threads, read):
            reads["n"] += 1
            return 0.0 if read == 0 else 12000.0

        result, marks = self.run_marks(ms, cores=8)
        self.assertEqual(result.reason, ocr_check.SLOW)
        self.assertTrue(marks)
        self.assertLess(max(marks), ocr_check.BUDGET_S)

    def test_stopped_reports_nothing_more(self):
        """叫停以后不再交。"""
        calls = []

        def stop():
            calls.append(1)
            return len(calls) > 2  # 第一个候选建好、热身完以后

        result, marks = self.run_marks(0.0, stop=stop)
        self.assertEqual(result.reason, ocr_check.STOPPED)
        self.assertEqual(len(marks), 1)


class SampleImageTest(unittest.TestCase):
    def test_shape(self):
        image = ocr_check.sample_image()
        self.assertEqual(image.shape, (736, 1568, 3))
        self.assertEqual(image.dtype, np.uint8)
        self.assertGreater(int(image.max()), int(image.min()))  # 有字

    @unittest.skipUnless(HAVE_MODELS, MODELS_SKIP)
    def test_real_engine_reads_it(self):
        """真引擎读这张图，读到 8 段以上。"""
        image = ocr_check.sample_image()
        engine = chip_select.new_engine(2)
        result = engine(image)
        texts = [str(text) for text in (getattr(result, "txts", None) or ()) if str(text).strip()]
        self.assertGreaterEqual(len(texts), 8, texts)


class RenderCheckTest(unittest.TestCase):
    """打包自检的「识别合成图」（diagnostics.ocr_render_check）：读的是线程数自检用的那张图，读到的段数不够时报错。"""

    def read(self, texts):
        images = []

        def engine(image):
            images.append(image)
            return SimpleNamespace(txts=texts)

        with mock.patch.object(chip_select, "_engine", return_value=engine):
            return diagnostics.ocr_render_check(), images

    def test_enough(self):
        texts = tuple(f"line {i}" for i in range(diagnostics.MIN_OCR_LINES))
        result, images = self.read(texts)
        self.assertEqual(result, {"lines": diagnostics.MIN_OCR_LINES})
        self.assertEqual(images[0].shape, (ocr_check.SAMPLE_HEIGHT, ocr_check.SAMPLE_WIDTH, 3))

    def test_too_few(self):
        for texts in (None, (), tuple("abc")):
            with self.subTest(texts=texts), self.assertRaises(RuntimeError):
                self.read(texts)


class FakeSession:
    def __init__(self, record=None):
        self.settings = SimpleNamespace(ocr_check=dict(record or {}))
        self.has_game_state = False
        self.game = SimpleNamespace(game_over=False)
        self.game_quiet = False
        self.saves = 0
        self.logged = []

    def save(self):
        self.saves += 1

    def log_state(self, kind, **data):
        self.logged.append((kind, data))


class FakeWatcher:
    def __init__(self):
        self.paused = False
        self.lowend = False


class FakeReasons:
    """假的查原因模块：begin 只记下进程号，find 返回 env.reason_result（默认什么都没查到）。"""

    def __init__(self, env):
        self.env = env

    def begin(self, pids):
        self.env.pids.append(tuple(pids))
        return SimpleNamespace(finish=lambda: {})

    def find(self, probe, kind, **kwargs):
        self.env.finds.append((kind, kwargs))
        return self.env.reason_result


class Check:
    """一套 OcrCheck 和它的假环境：later 只记下来（由测试手动触发），run_background 当场调用；shown 是每次刷新时提示栏上写的
    （OcrCheck.notice），now 是算剩余时间用的假时钟。查原因换成假的：不读这台电脑、不真的去比大小核。"""

    def __init__(self, record=None, activity="game"):
        self.session = FakeSession(record)
        self.lock = threading.Lock()
        self.watcher = FakeWatcher()
        self.activity = activity
        self.later_calls = []
        self.sessions = [self.session]
        self.shown = []
        self.now = 100.0
        self.pids = []
        self.finds = []
        self.reason_result = ((), {})
        self.check = ui_check.OcrCheck(
            session=self.session, lock=self.lock, run_background=lambda fn: fn(),
            later=lambda ms, fn: self.later_calls.append((ms, fn)),
            watcher=lambda: self.watcher, activity=lambda: self.activity, sessions=lambda: self.sessions,
            refresh=lambda: self.shown.append(self.check.notice()), clock=lambda: self.now,
            reasons=FakeReasons(self))

    def poll(self):
        """触发记下的最后一次定时器（之后又排的留在 later_calls 里）。"""
        ms, fn = self.later_calls.pop()
        fn()

    def fire(self, fn):
        """触发记下的定时器里要做 fn 的那一个（按要做的事找：POLL_MS 和 RESULT_MS 一样长）。"""
        index = next(i for i, (_, planned) in enumerate(self.later_calls) if planned == fn)
        self.later_calls.pop(index)[1]()

    def timers(self):
        """记下的定时器要做的事。"""
        return [fn for _, fn in self.later_calls]

    def record(self):
        return dict(self.session.settings.ocr_check)


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.used = []
        patcher = mock.patch.object(chip_select, "use_ocr_threads", side_effect=self.used.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cores = os.cpu_count() or 1

    def test_valid_record(self):
        """记录在这台电脑上还有效：只换线程数、不再排定时。"""
        env = Check({"cores": self.cores, "threads": 2, "ms": {"2": 300}, "rounds": 5})
        env.check.startup()
        self.assertEqual(self.used, [2])
        self.assertEqual(env.later_calls, [])
        self.assertTrue(env.check._finished)

    def test_core_changed(self):
        """核心数变了的旧记录当没有：重新排一次，次数从 0 算。"""
        env = Check({"cores": self.cores + 1, "threads": 2, "tries": 3})
        env.check.startup()
        self.assertEqual(self.used, [])
        self.assertEqual([ms for ms, _ in env.later_calls], [ui_check.FIRST_DELAY_MS])

    def test_threads_not_candidate(self):
        """记录里的线程数不在候选里：当没有。"""
        env = Check({"cores": self.cores, "threads": 3})
        env.check.startup()
        self.assertEqual(self.used, [])
        self.assertTrue(env.later_calls)

    def test_tries_exhausted(self):
        """这台电脑已经量满 MAX_TRIES 次：不再量，照默认。"""
        env = Check({"cores": self.cores, "tries": ui_check.MAX_TRIES})
        env.check.startup()
        self.assertEqual(env.later_calls, [])
        self.assertTrue(env.check._finished)

    def test_too_few_cores(self):
        env = Check()
        with mock.patch.object(os, "cpu_count", return_value=1):
            env.check.startup()
        self.assertEqual(env.later_calls, [])
        self.assertTrue(env.check._finished)


class ReadyTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ocr_check, "run", return_value=ocr_check.Result(4, {4: 100}, 5, ""))
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(chip_select, "use_ocr_threads")
        self.use = patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_ready(self):
        """watcher 没了、暂停了、低配模式开着、游戏不在前台、在对局里：都不开始。"""
        cases = ("no_watcher", "paused", "lowend", "background", "in_game")
        for case in cases:
            with self.subTest(case=case):
                env = Check()
                if case == "no_watcher":
                    env.check.watcher = lambda: None
                elif case == "paused":
                    env.watcher.paused = True
                elif case == "lowend":
                    env.watcher.lowend = True
                elif case == "background":
                    env.activity = "background"
                else:
                    env.session.has_game_state = True
                env.check.startup()
                env.poll()
                self.run.assert_not_called()

    def test_other_lane_in_game(self):
        """两开时另一张在对局里：不开始，正在量的也停下（文字识别引擎全程序只有一个，那一张的识别也要排队）。"""
        env = Check()
        other = FakeSession()
        env.sessions = [env.session, other]
        other.has_game_state = True
        env.check.startup()
        env.poll()
        self.run.assert_not_called()
        self.assertTrue(env.check._should_stop())
        other.game.game_over = True
        self.assertFalse(env.check._should_stop())
        env.poll()
        self.run.assert_called_once()

    def test_in_game_over_can_run(self):
        """对局已经结束（结算页）不算在对局里：可以量。"""
        env = Check()
        env.session.has_game_state = True
        env.session.game.game_over = True
        env.check.startup()
        env.poll()
        self.run.assert_called_once()

    def test_quiet_game_can_run(self):
        """中途退出、认不出主菜单（连着一阵子看不到对局里的读数，Session.game_quiet），这一局的状态还留着：不算在对局里，
        可以量；又读到对局里的东西时算，量到一半的也停下。"""
        env = Check()
        env.session.has_game_state = True
        env.check.startup()
        env.poll()
        self.run.assert_not_called()
        self.assertTrue(env.check._should_stop())
        env.session.game_quiet = True
        self.assertFalse(env.check._should_stop())
        env.poll()
        self.run.assert_called_once()

    def test_not_twice_while_running(self):
        """正在量的时候不重复开始。"""
        env = Check()
        env.check._running = True
        env.check.startup()
        env.poll()
        self.run.assert_not_called()


class ApplyTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(chip_select, "use_ocr_threads")
        self.use = patcher.start()
        self.addCleanup(patcher.stop)
        self.errors = []
        patcher = mock.patch.object(ui_check, "log_error", side_effect=lambda *a, **k: self.errors.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_measured(self):
        """量出来：先换线程数，再写设置（cores、threads、ms、rounds）、存盘、记一条识别记录；做完了不再看。"""
        env = Check()
        result = ocr_check.Result(4, {2: 300, 4: 250}, 5, "")
        with mock.patch.object(ocr_check, "run", return_value=result):
            env.check.startup()
            env.poll()
        self.use.assert_called_once_with(4)
        self.assertEqual(env.record(), {"cores": os.cpu_count() or 1, "threads": 4,
                                        "ms": {"2": 300, "4": 250}, "rounds": 5})
        self.assertEqual(env.session.saves, 1)
        self.assertEqual(env.session.logged[0][0], "ocr_check")
        self.assertEqual(env.session.logged[0][1]["threads"], 4)
        self.assertTrue(env.check._finished)
        self.assertEqual(env.later_calls, [(ui_check.RESULT_MS, env.check.dismiss)])  # 只剩提示栏上结果那一条到点收起，不再看

    def test_stopped_not_counted(self):
        """STOPPED：不算次数、不写设置；满 MAX_RUNS 次才不再看。"""
        env = Check({"cores": os.cpu_count() or 1, "tries": 1, "why": "noisy"})
        before = env.record()
        with mock.patch.object(ocr_check, "run",
                               return_value=ocr_check.Result(None, {}, 2, ocr_check.STOPPED)):
            env.check.startup()
            for _ in range(ui_check.MAX_RUNS):
                env.poll()
        self.assertEqual(env.record(), before)
        self.assertEqual(env.session.saves, 0)
        self.assertEqual(env.check._runs, ui_check.MAX_RUNS)
        self.assertTrue(env.check._finished)

    def test_apply_error_still_stops(self):
        """量完写设置时出错（这一次没做完）：这次运行开始满 MAX_RUNS 次也不再看，不会一直隔一阵量一次。"""
        env = Check()

        def background(fn):
            with self.assertRaises(RuntimeError):
                fn()

        env.check.run_background = background
        result = ocr_check.Result(4, {2: 300, 4: 250}, 5, "")
        with mock.patch.object(ocr_check, "run", return_value=result) as run, \
                mock.patch.object(env.session, "save", side_effect=RuntimeError("写不了")):
            env.check.startup()
            for _ in range(ui_check.MAX_RUNS + 1):
                env.poll()
        self.assertEqual(run.call_count, ui_check.MAX_RUNS)
        self.assertTrue(env.check._finished)
        self.assertEqual(env.later_calls, [])

    def test_failures_count(self):
        """NOISY、SLOW、出错：tries 加一写进设置（连着急存盘），这次运行做完了。"""
        for reason in (ocr_check.NOISY, ocr_check.SLOW):
            with self.subTest(reason=reason):
                env = Check({"cores": os.cpu_count() or 1, "tries": 1, "why": "slow", "ms": {"2": 1}})
                result = ocr_check.Result(None, {2: 400}, 3, reason)
                with mock.patch.object(ocr_check, "run", return_value=result):
                    env.check.startup()
                    env.poll()
                self.assertEqual(env.record()["tries"], 2)
                self.assertEqual(env.record()["why"], reason)
                self.assertEqual(env.record()["ms"], {"2": 400})
                self.assertNotIn("threads", env.record())
                self.assertTrue(env.check._finished)

    def test_error(self):
        """量的时候出错：记错误记录，算一次没量出来，原因写 error。"""
        env = Check()
        with mock.patch.object(ocr_check, "run", side_effect=RuntimeError("炸了")):
            env.check.startup()
            env.poll()
        self.assertEqual(len(self.errors), 1)
        self.assertIn("拾星助手仍在运行", self.errors[0][0])
        self.assertEqual(env.record()["tries"], 1)
        self.assertEqual(env.record()["why"], "error")
        self.assertTrue(env.check._finished)
        self.assertFalse(env.check._running)

    def test_core_change_resets_tries(self):
        """核心数变了：tries 从 0 算。"""
        env = Check({"cores": 1, "tries": 2, "why": "slow"})
        result = ocr_check.Result(None, {}, 3, ocr_check.SLOW)
        with mock.patch.object(ocr_check, "run", return_value=result):
            env.check.startup()
            env.poll()
        self.assertEqual(env.record()["tries"], 1)
        self.assertEqual(env.record()["cores"], os.cpu_count() or 1)

    def test_no_later_from_background(self):
        """后台那次量完不再从后台线程排定时（later 只在界面线程的 _poll 里用）。"""
        env = Check()
        before = len(env.later_calls)
        with mock.patch.object(ocr_check, "run", return_value=ocr_check.Result(2, {2: 1}, 5, "")):
            env.check._work()
        self.assertEqual(len(env.later_calls), before)


class NoticeTest(unittest.TestCase):
    """提示栏上写什么（OcrCheck.notice）、什么时候刷新、什么时候收起。"""

    def setUp(self):
        patcher = mock.patch.object(chip_select, "use_ocr_threads")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ui_check, "log_error")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(os, "cpu_count", return_value=16)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def run_result(result):
        return mock.patch.object(ocr_check, "run", return_value=result)

    def begun(self, env=None):
        """开始量、后台还没开始跑：返回环境和后台要跑的函数。"""
        env = env or Check()
        pending = []
        env.check.run_background = pending.append
        env.check.startup()
        env.poll()
        return env, pending[0]

    def test_running(self):
        """一开始就写上：剩余时间先按估的，定时器每秒刷新一次，也接着看要不要再量。"""
        env, _ = self.begun()
        planned = [(ui_check.TICK_MS, env.check._tick), (ui_check.POLL_MS, env.check._poll)]
        self.assertEqual(env.shown, [OcrNote("running", left=math.ceil(ocr_check.first_guess(16)))])
        self.assertCountEqual(env.later_calls, planned)
        env.fire(env.check._tick)
        self.assertEqual(len(env.shown), 2)
        self.assertCountEqual(env.later_calls, planned)

    def test_seconds_left(self):
        """按后台最近一次估的减去过的时间；估的多一点时不往上跳（JITTER_S），多很多才改；到点了是 0。"""
        env, _ = self.begun()
        check = env.check
        check._on_progress(8.2)
        self.assertEqual(check.notice().left, 9)
        env.now += 3.0
        self.assertEqual(check.notice().left, 6)
        check._on_progress(6.5)  # 多估了一点：照旧显示 6
        self.assertEqual(check.notice().left, 6)
        check._on_progress(6.0 + ui_check.JITTER_S)
        self.assertEqual(check.notice().left, 6)
        check._on_progress(6.0 + ui_check.JITTER_S + 1.0)  # 多出 JITTER_S 以上：改
        self.assertEqual(check.notice().left, 6 + ui_check.JITTER_S + 1)
        env.now += 1000.0
        self.assertEqual(check.notice().left, 0)

    def test_progress_from_background(self):
        """量的时候后台线程调用 progress：只记数，不刷新、不排定时器；交给 run 的就是它。"""
        env, work = self.begun()
        before = (len(env.shown), len(env.later_calls))
        env.check._on_progress(3.0)
        self.assertEqual((len(env.shown), len(env.later_calls)), before)
        with self.run_result(ocr_check.Result(4, {4: 100}, 5, "")) as run:
            work()
        self.assertEqual(run.call_args.kwargs["progress"], env.check._on_progress)
        self.assertEqual((len(env.shown), len(env.later_calls)), before)

    def test_result_then_closes(self):
        """量完下一次定时器换成结果，不再每秒刷新，过 RESULT_MS 自己收起。"""
        env, work = self.begun()
        with self.run_result(ocr_check.Result(4, {2: 300, 4: 250}, 5, "")):
            work()
        env.fire(env.check._tick)
        self.assertEqual(env.shown[-1], OcrNote("good", threads=4, ms=250))
        self.assertNotIn(env.check._tick, env.timers())
        self.assertIn((ui_check.RESULT_MS, env.check.dismiss), env.later_calls)
        env.fire(env.check.dismiss)
        self.assertIsNone(env.shown[-1])
        self.assertIsNone(env.check.notice())

    def test_late_finish_shows_next_tick(self):
        """刷新到一半才量完：这一次照旧排下一次定时器，下一次换成结果，不会停在「正在量」。"""
        env, work = self.begun()
        finish = self.run_result(ocr_check.Result(4, {4: 100}, 5, ""))

        def refresh():
            with finish:
                work()
            env.shown.append("刷新")

        env.check.refresh = refresh
        env.fire(env.check._tick)
        self.assertIn(env.check._tick, env.timers())
        env.check.refresh = lambda: env.shown.append(env.check.notice())
        env.fire(env.check._tick)
        self.assertEqual(env.shown[-1], OcrNote("good", threads=4, ms=100))

    def test_speed_kinds(self):
        """按读一次的中位毫秒分三档，边上的值归快的那一档。"""
        cases = ((100, "good"), (ui_check.FAIR_MS, "good"), (ui_check.FAIR_MS + 1, "fair"),
                 (ui_check.POOR_MS, "fair"), (ui_check.POOR_MS + 1, "poor"))
        for ms, kind in cases:
            with self.subTest(ms=ms):
                env = Check()
                with self.run_result(ocr_check.Result(2, {2: ms, 4: ms + 500}, 5, "")):
                    env.check.startup()
                    env.poll()
                self.assertEqual(env.check.notice(), OcrNote(kind, threads=2, ms=ms))

    def test_poor_stays_until_closed(self):
        """性能差的不自己收起，留到玩家关掉。"""
        cases = (ocr_check.Result(4, {4: ui_check.POOR_MS + 100}, 5, ""), ocr_check.Result(None, {}, 2, ocr_check.SLOW))
        for result in cases:
            with self.subTest(result=result):
                env = Check()
                with self.run_result(result):
                    env.check.startup()
                    env.poll()
                self.assertNotIn(env.check.dismiss, env.timers())
                self.assertIn(env.check.notice().kind, ui_check.KEEP_KINDS)
                count = len(env.shown)
                env.check.dismiss()
                self.assertIsNone(env.check.notice())
                self.assertEqual(len(env.shown), count + 1)
                env.check.dismiss()  # 已经关了：不再刷新
                self.assertEqual(len(env.shown), count + 1)

    def test_not_measured(self):
        """太慢、量不准、出错：照用默认线程数，次数没用满时下次启动再试；量不准、出错的自己收起。"""
        cases = ((ocr_check.Result(None, {}, 2, ocr_check.SLOW), "slow", False),
                 (ocr_check.Result(None, {2: 400}, 5, ocr_check.NOISY), "noisy", True),
                 (RuntimeError("炸了"), "error", True))
        for outcome, kind, closes in cases:
            for tries, retry in ((0, True), (ui_check.MAX_TRIES - 2, True), (ui_check.MAX_TRIES - 1, False)):
                with self.subTest(kind=kind, tries=tries):
                    env = Check({"cores": 16, "tries": tries, "why": "noisy"})
                    effect = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                    with mock.patch.object(ocr_check, "run", **effect):
                        env.check.startup()
                        env.poll()
                    self.assertEqual(env.check.notice(),
                                     OcrNote(kind, threads=ocr_check.DEFAULT_THREADS, retry=retry))
                    self.assertEqual(env.check.dismiss in env.timers(), closes)

    def test_stopped_removes_row(self):
        """中途停下（进了对局等）：提示去掉，不报结果，过一阵再看、再量时重新写上。"""
        env, work = self.begun()
        with self.run_result(ocr_check.Result(None, {}, 0, ocr_check.STOPPED)):
            work()
        env.fire(env.check._tick)
        self.assertIsNone(env.shown[-1])
        self.assertEqual(env.later_calls, [(ui_check.POLL_MS, env.check._poll)])
        pending = []
        env.check.run_background = pending.append
        env.fire(env.check._poll)
        self.assertEqual(env.check.notice().kind, "running")
        self.assertEqual(env.check.notice().left, math.ceil(ocr_check.first_guess(16)))
        self.assertIn(env.check._tick, env.timers())

    def test_restart_keeps_one_timer(self):
        """上一次刚停下、定时器还没走到又开始一次：只留一个每秒刷新的定时器。"""
        env, work = self.begun()
        with self.run_result(ocr_check.Result(None, {}, 0, ocr_check.STOPPED)):
            work()
        pending = []
        env.check.run_background = pending.append
        env.fire(env.check._poll)
        self.assertEqual(env.timers().count(env.check._tick), 1)

    def test_shutdown_stops_refreshing(self):
        """退出时叫停以后，定时器到点不再刷新、不再排。"""
        env, _ = self.begun()
        env.check._done.set()
        env.check.shutdown(0.0)
        count = len(env.shown)
        env.fire(env.check._tick)
        self.assertEqual(len(env.shown), count)
        self.assertNotIn(env.check._tick, env.timers())

    def test_valid_record_says_nothing(self):
        """有效记录直接用，不量，提示栏上什么都不写。"""
        env = Check({"cores": 16, "threads": 4, "ms": {"4": 100}, "rounds": 5})
        env.check.startup()
        self.assertIsNone(env.check.notice())
        self.assertEqual(env.shown, [])


class RowTest(unittest.TestCase):
    """性能自检那一行的文字、级别和按钮（notices.ocr_check_row），排在哪里。"""

    def test_running(self):
        key, text, level, buttons = notices_mod.ocr_check_row(OcrNote("running", left=8))
        self.assertEqual(key, "ocr_check")
        self.assertEqual(text, "正在进行性能自检，可正常进行游戏。剩余约 8 秒")
        self.assertEqual((level, buttons), ("ok", []))
        for left in (1, 0):
            self.assertIn(OCR_CHECK_SOON, notices_mod.ocr_check_row(OcrNote("running", left=left))[1])

    def test_results(self):
        """没查到原因时：结论和读数，不写原因，只有「关闭本条提示」。"""
        cases = (
            (OcrNote("good", threads=4, ms=222), "ok", "性能良好", "读一次约 0.2 秒"),
            (OcrNote("fair", threads=2, ms=620), "info", "性能一般", "读一次约 0.6 秒"),
            (OcrNote("poor", threads=8, ms=1240), "warn", "性能较差", "读一次约 1.2 秒"),
            (OcrNote("slow", threads=4), "warn", "性能很差", "默认的 4 个线程"),
            (OcrNote("noisy", threads=4), "info", "没测出来", "默认的 4 个线程"),
            (OcrNote("error", threads=4), "info", "没能测完", "默认的 4 个线程"),
        )
        for note, level, conclusion, detail in cases:
            with self.subTest(kind=note.kind):
                _, text, got, buttons = notices_mod.ocr_check_row(note)
                self.assertEqual(got, level)
                self.assertEqual(text.startswith("⚠ 自检结果："), level == "warn")
                self.assertIn("自检结果：", text)
                self.assertIn(conclusion, text)
                self.assertIn(detail, text)
                self.assertNotIn("原因", text)
                self.assertNotIn("建议做法", text)
                self.assertEqual([b[2] for b in buttons], ["dismiss_ocr_check"])

    def test_threads_written(self):
        """用了几个线程照实写。"""
        self.assertIn("文字识别用 8 个线程", notices_mod.ocr_check_row(OcrNote("good", threads=8, ms=165))[1])

    def test_seconds_floor(self):
        """读得再快也写 0.1 秒，不写 0.0 秒。"""
        self.assertIn("约 0.1 秒", notices_mod.ocr_check_row(OcrNote("good", threads=4, ms=40))[1])

    def test_retry_sentence(self):
        """下次启动还会再试时才写那一句。"""
        for kind in ("slow", "noisy", "error"):
            with self.subTest(kind=kind):
                self.assertIn(OCR_CHECK_RETRY, notices_mod.ocr_check_row(OcrNote(kind, threads=4, retry=True))[1])
                self.assertNotIn(OCR_CHECK_RETRY, notices_mod.ocr_check_row(OcrNote(kind, threads=4))[1])

    def rows(self, model, note):
        view = SimpleNamespace(game_over=False, unsupported=[], unrecorded=0, map_switched=False)
        return model.rows(view, live=False, reverted_chip="", capture=None, unlocked=[], schemes="",
                          assets=("downloading", 1, 2, ""), calibrate_games=20, folder="AstralChipCompass",
                          ocr_note=note)

    def test_position(self):
        """标题栏下方：排在图片下载后面、「免费软件」声明前面；不让边框变色。"""
        model = notices_mod.NoticeModel()
        model.free_notice = "免费"
        rows = self.rows(model, OcrNote("running", left=8))
        self.assertEqual([k for k, *_ in rows], ["assets", "ocr_check", "free"])
        self.assertEqual(model.area("ocr_check"), "global")
        model.notices = {r[0]: r for r in rows}
        self.assertFalse(model.needs_action())

    def test_no_note_no_row(self):
        self.assertEqual([k for k, *_ in self.rows(notices_mod.NoticeModel(), None)], ["assets"])

    def test_second_lane_hides_it(self):
        """第二张只显示本局的提示和「识别已停止」。"""
        rows = self.rows(notices_mod.NoticeModel(lane_only=True), OcrNote("running", left=8))
        self.assertEqual(rows, [])


class ReasonTextTest(unittest.TestCase):
    """结果差时（一般、查到排在小核上时也是）写的原因、建议做法、问法（wording.ocr_reason_text），带不带「是」「否」。"""

    MANUAL = {
        "no_ac": Reason("no_ac", values={"perf": None}),
        "saver": Reason("saver"),
        "power_mode": Reason("power_mode"),
        "power_plan": Reason("power_plan"),
        "others_busy": Reason("others_busy", values={"percent": 70, "with_game": False}),
        "throttled": Reason("throttled", values={"perf": 40}),
        "low_memory": Reason("low_memory", values={"available_gb": 0.6}),
    }
    OFFERS = {
        "small_core": Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}),
        "eco": Reason("eco", "eco"),
        "low_priority": Reason("low_priority", "priority"),
        "affinity": Reason("affinity", "affinity", {"allowed": 2, "total": 16}),
        "old_cpu": Reason("old_cpu", "lowend"),
        "few_cores": Reason("few_cores", "lowend", {"cores": 2, "logical": 4}),
    }

    def test_tables_cover_every_reason(self):
        """几组句子合起来正好是查原因那边会给的全部原因（TRANSIENT 的都是只能玩家自己做的）；每种修法都有「是」的悬停说明，
        也都能记进「不再问」。"""
        written = set(wording.OCR_REASON_MANUAL) | set(wording.OCR_REASON_FIX) | set(wording.OCR_REASON_LOW_SPEC)
        self.assertEqual(written, set(self.MANUAL) | set(self.OFFERS))
        self.assertEqual(set(wording.OCR_REASON_MANUAL), set(self.MANUAL))
        self.assertLessEqual(set(ocr_reason.TRANSIENT), set(self.MANUAL))
        fixes = {reason.fix for reason in self.OFFERS.values()}
        self.assertEqual(fixes, set(wording.OCR_CHECK_YES_TIP))
        self.assertEqual(fixes, set(ui_check.DECLINABLE))

    def test_diagnose_gives_only_written_reasons(self):
        """查原因那边把能给的原因都给一遍，每一条都写得出句子（键、数都对得上）。"""
        state = {"power": {"ac": False, "saver": True, "mode": "best_efficiency", "plan": "power_saver"},
                 "load": {"busy": 90, "own": 10, "game": 10, "perf": 40, "limit": 50},
                 "memory": {"available_gb": 0.6}, "performance": "eco", "priority": "idle", "affinity": (2, 16),
                 "lacks_avx2": True, "cpu": {"logical": 4, "cores": 2}}
        found = ocr_reason.diagnose(state, split={"big": 8, "small": 16, "ids": []}, gain=2.0)
        found += ocr_reason.diagnose({"load": {"busy": 80, "own": 10, "game": None, "perf": 40, "limit": 50}})
        found += ocr_reason.diagnose({}, unknown=True)
        self.assertEqual({r.key for r in found}, set(self.MANUAL) | set(self.OFFERS) | {"unknown"})
        for reason in found:
            with self.subTest(reason=reason.key):
                text = wording.ocr_reason_text((reason,))
                self.assertNotIn("{", text)
                self.assertTrue(text.startswith("\n"))

    def test_manual(self):
        """只能玩家自己做的：原因、建议做法；还要再测时接「下次启动时再测一次」。数都填上，不带问法。"""
        expected = {"others_busy": "约 70%", "throttled": "约 40%", "low_memory": "约 0.6 GB"}
        for key, reason in self.MANUAL.items():
            with self.subTest(key=key):
                text = wording.ocr_reason_text((reason,), retry=True)
                self.assertRegex(text, r"^\n原因：[^\n]+。\n建议做法：[^\n]+。下次启动时再测一次。$")
                self.assertNotIn("{", text)
                self.assertIn(expected.get(key, ""), text)
                self.assertFalse(wording.ocr_reason_text((reason,)).endswith(OCR_CHECK_RETRY))

    def test_offer(self):
        """能替玩家改的：写到问法为止，不提下次再测（等玩家答）；能改的问「是否允许？」，低配模式问「是否打开？」。"""
        expected = {"small_core": ("8 个大核、16 个小核", "快约 2 倍"), "affinity": ("只能用 2 个核心", "一共 16 个"),
                    "few_cores": ("2 核 4 线程",)}
        for key, reason in self.OFFERS.items():
            with self.subTest(key=key):
                text = wording.ocr_reason_text((reason,), retry=True)
                ask = wording.OCR_LOW_SPEC_ASK if reason.fix == "lowend" else wording.OCR_REASON_ASK
                self.assertRegex(text, r"^\n原因：[^\n]+。\n建议做法：[^\n]+。\n" + re.escape(ask) + "$")
                self.assertNotIn(OCR_CHECK_RETRY, text)
                self.assertNotIn("{", text)
                for part in expected.get(key, ()):
                    self.assertIn(part, text)

    def test_two(self):
        """两条：只能自己做的在前，能改的在后；原因连成一句，建议做法写成「也可以」，问法写明问的是哪一件。"""
        text = wording.ocr_reason_text((self.MANUAL["others_busy"], self.OFFERS["small_core"]))
        self.assertEqual(text, "\n原因：自检时别的程序占了约 70% 的处理器；另外文字识别被排到了小核，只用大核试过，快约 2 倍。"
                               "\n建议做法：等下载、杀毒扫描、录屏这类程序忙完，或者先关掉。"
                               "也可以让拾星助手只用大核，游戏帧数可能略受影响，可以在设置里改回来。"
                               "\n是否允许只用大核？")
        text = wording.ocr_reason_text((self.MANUAL["low_memory"], self.OFFERS["few_cores"]))
        self.assertIn("也可以打开「低配模式」", text)
        self.assertTrue(text.endswith("是否打开「低配模式」？"))
        for fix in self.OFFERS.values():
            with self.subTest(fix=fix.key):
                self.assertNotIn("{", wording.ocr_reason_text((self.MANUAL["saver"], fix)))

    def test_two_manual(self):
        """两条都只能自己做：建议做法一句接一句；第二条以 Windows 开头时和「另外」隔一个空格。"""
        text = wording.ocr_reason_text((self.MANUAL["no_ac"], self.MANUAL["saver"]), retry=True)
        self.assertIn("；另外 Windows 开着节电模式", text)
        self.assertTrue(text.endswith("插上电源。在系统设置的「电源和电池」里关掉节电模式。下次启动时再测一次。"))
        text = wording.ocr_reason_text((self.MANUAL["others_busy"], self.MANUAL["low_memory"]))
        self.assertIn("；另外内存快用完了", text)

    def test_variants(self):
        """没插电量到了限速写读到的数；分不开游戏的占用时写「游戏和别的程序一共」。"""
        self.assertIn("只跑到标称频率的约 45%", wording.ocr_reason_text((Reason("no_ac", values={"perf": 45}),)))
        self.assertIn("Windows 可能在限制", wording.ocr_reason_text((self.MANUAL["no_ac"],)))
        reason = Reason("others_busy", values={"percent": 70, "with_game": True})
        self.assertIn("游戏和别的程序一共占了约 70%", wording.ocr_reason_text((reason,)))

    def test_unknown(self):
        """都查过没问题：写「可能的原因」，问要不要开低配模式；能开所以带「是」「否」。"""
        reason = Reason("unknown", "lowend")
        text = wording.ocr_reason_text((reason,))
        self.assertTrue(text.startswith("\n可能的原因："))
        self.assertTrue(text.endswith(wording.OCR_LOW_SPEC_ASK))
        note = OcrNote("slow", threads=4, retry=True, reasons=(reason,))
        self.assertEqual([b[2] for b in notices_mod.ocr_check_row(note)[3]], ["ocr_check_accept", "ocr_check_decline"])

    def test_no_reason(self):
        self.assertEqual(wording.ocr_reason_text(()), "")
        self.assertEqual(wording.ocr_reason_text((), retry=True), OCR_CHECK_RETRY)

    def test_gain(self):
        """只用大核快多少：不到 2 倍写百分比（整十），到了写倍数。"""
        for ratio, want in ((1.3, "30%"), (1.5, "50%"), (1.9, "90%"), (2.0, "2 倍"), (2.5, "2.5 倍"), (3.0, "3 倍")):
            with self.subTest(ratio=ratio):
                self.assertEqual(wording.ocr_gain_text(ratio), want)

    def test_buttons(self):
        """能替玩家改的带「是」「否」（「是」突出，悬停说明按修法写；「否」说不再问）；只能自己做的、没原因的只有关闭。"""
        for reason in self.OFFERS.values():
            with self.subTest(key=reason.key):
                note = OcrNote("poor", threads=4, ms=1240, reasons=(reason,))
                self.assertEqual(note.offer, reason.fix)
                buttons = notices_mod.ocr_check_row(note)[3]
                self.assertEqual(buttons, [("是", "primary", "ocr_check_accept", wording.OCR_CHECK_YES_TIP[reason.fix]),
                                           ("否", "", "ocr_check_decline", wording.OCR_CHECK_NO_TIP)])
        for reason in self.MANUAL.values():
            with self.subTest(key=reason.key):
                note = OcrNote("poor", threads=4, ms=1240, retry=True, reasons=(reason,))
                self.assertEqual(note.offer, "")
                self.assertEqual([b[2] for b in notices_mod.ocr_check_row(note)[3]], ["dismiss_ocr_check"])
        two = OcrNote("poor", threads=4, ms=1240, reasons=(self.MANUAL["saver"], self.OFFERS["eco"]))
        self.assertEqual(two.offer, "eco")

    def test_fair_small_core(self):
        """一般、查到排在小核上：结论照「一般」写（说明色，不带⚠），后面接原因、建议做法和问法，带「是」「否」。"""
        reason = Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0})
        key, text, level, buttons = notices_mod.ocr_check_row(OcrNote("fair", threads=8, ms=400, reasons=(reason,)))
        self.assertEqual((key, level), ("ocr_check", "info"))
        self.assertTrue(text.startswith("自检结果："))
        self.assertIn("性能一般", text)
        self.assertIn("读一次约 0.4 秒", text)
        self.assertTrue(text.endswith(wording.ocr_reason_text((reason,))))
        self.assertIn("小核", text)
        self.assertEqual([b[2] for b in buttons], ["ocr_check_accept", "ocr_check_decline"])

    def test_after_answering(self):
        """点了「是」以后：改好了、打开了低配模式、没改成各一句，说明色，带关闭。"""
        for kind, text in wording.OCR_CHECK_AFTER.items():
            with self.subTest(kind=kind):
                key, got, level, buttons = notices_mod.ocr_check_row(OcrNote(kind))
                self.assertEqual((key, got, level), ("ocr_check", text, "info"))
                self.assertEqual([b[2] for b in buttons], ["dismiss_ocr_check"])

    def test_noisy(self):
        """没测出来：查到原因时结论只写时快时慢（原因另写），没查到时才写「可能是电脑正忙」。"""
        found = notices_mod.ocr_check_row(OcrNote("noisy", threads=4, retry=True, reasons=(self.MANUAL["others_busy"],)))[1]
        self.assertIn("读一次时快时慢", found)
        self.assertNotIn("可能是电脑正忙", found)
        self.assertIn("可能是电脑正忙", notices_mod.ocr_check_row(OcrNote("noisy", threads=4))[1])

    def test_hant(self):
        """繁体界面：Windows 上看到的字样和电脑的叫法照繁体的说法，句子里不剩这几个简体的写法。"""
        texts = {key: wording.ocr_reason_text((reason,)) for key, reason in {**self.MANUAL, **self.OFFERS}.items()}
        wants = {"no_ac": "筆電沒插電", "saver": "省電模式", "power_mode": "最佳電源效率", "power_plan": "控制台",
                 "throttled": "筆電插上電源"}
        for key, want in wants.items():
            with self.subTest(key=key):
                self.assertIn(want, lang.to_hant(texts[key]))
        self.assertIn("電源與電池", lang.to_hant(texts["saver"]))
        self.assertIn("「省電」", lang.to_hant(texts["power_plan"]))


class OverlayRowTest(unittest.TestCase):
    """接到悬浮窗上：标题栏下方那一行、绿字、「关闭本条提示」、对局进行中先不显示结果。离屏，不接后台识别。"""

    def setUp(self):
        self.window = Overlay(load_db(), OverlaySession(load_db(), UiSettings(hero="凛")), threading.Lock(),
                              services=Services())
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def label(self):
        box = self.window.global_notice_box
        rows = [box.itemAt(i).widget() for i in range(box.count())]
        row = next(r for r in rows if r.objectName() == "notice-ocr_check")
        return next(x for x in row.findChildren(type(self.window.summary)) if x.objectName().startswith("notice_"))

    def test_running_is_green(self):
        """量的时候：绿字（notice_ok），没有按钮，一行显示时也看得到，鼠标停上去有说明。"""
        w = self.window
        w.ocr_check.notice = lambda: OcrNote("running", left=8)
        w.show()
        w.refresh()
        self.assertEqual(w.notice_model.texts()["ocr_check"], "正在进行性能自检，可正常进行游戏。剩余约 8 秒")
        self.assertEqual(self.label().objectName(), "notice_ok")
        self.assertTrue(self.label().toolTip())
        self.assertEqual(w.notices.buttons("ocr_check"), [])
        self.assertFalse(w.expanded)
        self.assertTrue(w.global_notices.isVisibleTo(w.panel))
        self.assertFalse(w.needs_attention())

    def test_close_button(self):
        """结果那一条的「关闭本条提示」：收起，不再显示。"""
        w = self.window
        w.ocr_check._outcome = OcrNote("poor", threads=4, ms=1240)
        w.refresh()
        self.assertEqual(self.label().objectName(), "notice_warn")
        buttons = w.notices.buttons("ocr_check")
        self.assertEqual(len(buttons), 1)
        buttons[0].click()
        self.assertNotIn("ocr_check", w.notice_model.texts())
        self.assertIsNone(w.ocr_check.notice())

    def asking(self, reason):
        """结果那一条问玩家是否允许：排到悬浮窗上，返回两个按钮。显示出来，量得到位置。"""
        w = self.window
        w.ocr_check._outcome = OcrNote("poor", threads=4, ms=1240, reasons=(reason,))
        w.show()
        w.refresh()
        return w.notices.buttons("ocr_check")

    def test_yes_no_under_text(self):
        """「是」「否」排在文字下面，一样宽，「是」在前。"""
        yes, no = self.asking(Reason("eco", "eco"))
        self.assertEqual((yes.text(), no.text()), ("是", "否"))
        self.assertEqual(yes.width(), no.width())
        label = self.label()
        self.assertGreaterEqual(yes.mapTo(self.window, QPoint(0, 0)).y(),
                                label.mapTo(self.window, QPoint(0, label.height())).y())
        self.assertEqual(yes.mapTo(self.window, QPoint(0, 0)).y(), no.mapTo(self.window, QPoint(0, 0)).y())
        self.assertLess(yes.mapTo(self.window, QPoint(0, 0)).x(), no.mapTo(self.window, QPoint(0, 0)).x())

    def test_only_close_stays_beside_text(self):
        """只有「关闭本条提示」时照旧在文字右边。"""
        w = self.window
        w.ocr_check._outcome = OcrNote("poor", threads=4, ms=1240, retry=True, reasons=(Reason("saver"),))
        w.show()
        w.refresh()
        button = w.notices.buttons("ocr_check")[0]
        label = self.label()
        self.assertGreaterEqual(button.mapTo(w, QPoint(0, 0)).x(), label.mapTo(w, QPoint(label.width(), 0)).x())

    def test_yes_big_cores_saves_setting(self):
        """点「是」（只用大核）：真走 Overlay.set_big_cores，设上、存进设置，提示换成「已改好」，过一会儿再量。"""
        w = self.window
        calls = []
        w.ocr_check.later = lambda ms, fn: calls.append((ms, fn))
        yes, _ = self.asking(Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}))
        with mock.patch.object(sysinfo, "use_big_cores", return_value=True) as use, mock.patch.object(w.session, "save") as save:
            yes.click()
        use.assert_called_once_with(True)
        save.assert_called_once_with()
        self.assertIs(w.session.settings.big_cores, True)
        self.assertEqual(w.ocr_check.notice(), OcrNote(ui_check.FIXED))
        self.assertEqual(w.notice_model.texts()["ocr_check"], "已改好，重新自检。")
        self.assertIn((ui_check.RETEST_DELAY_MS, w.ocr_check._poll), calls)

    def test_yes_big_cores_cannot_set(self):
        """点「是」（只用大核）设不上：不存开着，提示换成「没改成」，过一会儿自己收起。"""
        w = self.window
        calls = []
        w.ocr_check.later = lambda ms, fn: calls.append((ms, fn))
        yes, _ = self.asking(Reason("small_core", "big_cores", {"big": 8, "small": 16, "ratio": 2.0}))
        with mock.patch.object(sysinfo, "use_big_cores", return_value=False), mock.patch.object(w.session, "save"):
            yes.click()
        self.assertIs(w.session.settings.big_cores, False)
        self.assertEqual(w.ocr_check.notice(), OcrNote(ui_check.FIX_FAILED))
        self.assertIn((ui_check.RESULT_MS, w.ocr_check.dismiss), calls)

    def test_yes_lowend(self):
        """点「是」（低配模式）：打开低配模式（和设置窗口的开关同一个方法），提示写可以在设置里关掉。"""
        w = self.window
        w.ocr_check.later = lambda ms, fn: None
        yes, _ = self.asking(Reason("old_cpu", "lowend"))
        with mock.patch.object(w.session, "save"):
            yes.click()
        self.assertTrue(w.lowend)
        self.assertEqual(w.notice_model.texts()["ocr_check"], "已打开「低配模式」，可以在设置里关掉。")

    def test_no_remembers(self):
        """点「否」：记进设置（这台电脑上不再问同一件事），提示收起。"""
        w = self.window
        _, no = self.asking(Reason("low_priority", "priority"))
        with mock.patch.object(w.session, "save") as save:
            no.click()
        save.assert_called_once_with()
        self.assertEqual(w.session.settings.ocr_check["declined"], ["priority"])
        self.assertIsNone(w.ocr_check.notice())
        self.assertNotIn("ocr_check", w.notice_model.texts())

    def test_hidden_while_playing(self):
        """对局进行中：结果先不显示（还留着，这一局结束再显示）；手动模式量的时候照旧标剩余时间。"""
        w = self.window
        w.notices.playing = lambda view: True
        w.ocr_check._outcome = OcrNote("poor", threads=4, ms=1240)
        w.refresh()
        self.assertNotIn("ocr_check", w.notice_model.texts())
        w.notices.playing = lambda view: False
        w.refresh()
        self.assertIn("ocr_check", w.notice_model.texts())
        w.notices.playing = lambda view: True
        w.ocr_check.notice = lambda: OcrNote("running", left=8)
        w.refresh()
        self.assertIn("ocr_check", w.notice_model.texts())

    def test_real_flow_on_overlay(self):
        """走一遍真的 OcrCheck：开始时写上剩余时间，量完换成结果。"""
        w = self.window
        w.run_background = lambda fn: fn()
        w.ocr_check.watcher = FakeWatcher
        w.ocr_check.activity = lambda: "game"
        calls = []
        w.ocr_check.later = lambda ms, fn: calls.append((ms, fn))
        result = ocr_check.Result(4, {2: 300, 4: 250}, 5, "")
        with mock.patch.object(ocr_check, "run", return_value=result), \
                mock.patch.object(chip_select, "use_ocr_threads"), \
                mock.patch.object(w.session, "save"):
            w.ocr_check.startup()
            calls.pop()[1]()
        self.assertEqual(w.notice_model.texts()["ocr_check"],
                         "自检结果：性能良好。\n文字识别用 4 个线程，读一次约 0.2 秒。")

    def test_every_skin_writes_green(self):
        """七套皮肤的样式表都有这一条，用这套皮肤自己的绿色；各套另加的规则没有盖掉它。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                sheet = theme.overlay_style(skin)
                self.assertIn(f"#notice_ok {{ color: {skin.common.OK_COLOR}; }}", sheet)
                self.assertEqual(sheet.count("notice_ok"), 1)


class ImportTest(unittest.TestCase):
    def test_overlay_import_does_not_load_opencv(self):
        """导入悬浮窗不带进识别模块和 OpenCV：皮肤预览的子进程也导入悬浮窗，用不到它们。另起一个 Python 进程，导入以后看有哪些模块。"""
        code = ("import json, sys; import advisor.ui.overlay; "
                "print(json.dumps([m for m in ('advisor.vision.ocr_check', 'advisor.vision.chip_select', 'cv2') "
                "if m in sys.modules]))")
        proc = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1]), [])


class ShutdownTest(unittest.TestCase):
    def test_not_running(self):
        env = Check()
        self.assertTrue(env.check.shutdown(0.0))

    def test_waits_then_times_out(self):
        """正在量时等：放下 done 之前等不到，最多等给的时间。"""
        env = Check()
        env.check._done.clear()
        timer = threading.Timer(0.2, env.check._done.set)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertFalse(env.check.shutdown(0.05))
        self.assertTrue(env.check.shutdown(1.0))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = sandbox.temp_dir(self, "test-ocr-check-") / "state.json"

    def test_default_and_old_file(self):
        self.assertEqual(UiSettings().ocr_check, {})
        self.assertEqual(UiSettings.load(self.path).ocr_check, {})

    def test_good_kept(self):
        record = {"cores": 16, "threads": 4, "ms": {"2": 327, "4": 228}, "rounds": 5}
        settings = UiSettings(hero="凛", ocr_check=record)
        self.assertEqual(settings.ocr_check, record)
        settings.save(self.path)
        self.assertEqual(UiSettings.load(self.path).ocr_check, record)

    def test_bad_dropped(self):
        cases = (
            (None, {}), ("x", {}), ([1], {}),
            ({"unknown": 1}, {}),
            ({"cores": "16", "threads": True, "rounds": 2.5, "why": 3}, {}),
            ({"cores": 16, "tries": 1, "why": "noisy", "ms": {"a": 1, 2: 3, "4": "x", "5": 50}},
             {"cores": 16, "tries": 1, "why": "noisy", "ms": {"5": 50}}),
        )
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.assertEqual(UiSettings(ocr_check=raw).ocr_check, want)


class RecordedGainTest(unittest.TestCase):
    """设置里的自检记录上这台电脑比出来的只用大核的收益（recorded_gain，设置窗口「只用大核」一项按它）。"""

    def test_this_machine(self):
        """记录的核心数和这台电脑的相同、readings 里有比值时取它（整数也算）；cores 不给时按 os.cpu_count()。"""
        self.assertEqual(ui_check.recorded_gain({"cores": 16, "readings": {"gain": 2.0}}, 16), 2.0)
        self.assertEqual(ui_check.recorded_gain({"cores": 16, "readings": {"gain": 1}}, 16), 1.0)
        with mock.patch.object(os, "cpu_count", return_value=24):
            self.assertEqual(ui_check.recorded_gain({"cores": 24, "threads": 8, "readings": {"gain": 1.1}}), 1.1)

    def test_none(self):
        """没有记录、换了电脑（核心数不同）、没比过（没有 readings、gain 是空值）、值不是有限的数：None。"""
        cases = (None, {}, "x", {"cores": 8, "readings": {"gain": 2.0}}, {"readings": {"gain": 2.0}},
                 {"cores": 16}, {"cores": 16, "readings": {}}, {"cores": 16, "readings": {"gain": None}},
                 {"cores": 16, "readings": {"gain": "2.0"}}, {"cores": 16, "readings": {"gain": True}},
                 {"cores": 16, "readings": {"gain": math.nan}}, {"cores": 16, "readings": "x"})
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(ui_check.recorded_gain(record, 16))

    def test_saved_record(self):
        """存进设置文件、读回来以后照样取得到（readings 的比值经得起清理）。"""
        path = sandbox.temp_dir(self, "test-ocr-gain-") / "state.json"
        cores = os.cpu_count() or 1
        UiSettings(hero="凛", ocr_check={"cores": cores, "threads": 4, "ms": {"4": 400}, "rounds": 5,
                                         "readings": {"gain": 1.6, "busy": 40}}).save(path)
        self.assertEqual(ui_check.recorded_gain(UiSettings.load(path).ocr_check), 1.6)


if __name__ == "__main__":
    unittest.main()
