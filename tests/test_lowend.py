"""低配模式和启动时声明高性能：

- 节奏：低配模式开着时 [watch] 的 idle_ms、hud_ms、coins_ms、hero_ms、unchanged_max_ms，三选一上补认、再读左上角之前
  等多久（select_reread_ms、hud_retry_ms），轮次被挡住时多久看一眼、多久完整读一次（occluded_recheck_ms、occluded_full_ms），
  连标记都没看见时多久完整读一次（blank_full_ms），任务栏没开时多久连带读一次整块（map_block_ms），不在对局里时多久看一眼
  选角界面（select_ms），对局中刚看见过轮次标记多久
  以内先不判选角界面（pick_grace_ms）乘 lowend_factor；
  active_ms（三选一显示着时）、出错后等的 2 秒、没画面时重取的间隔不变。FrameProcessor.set_pace 按构造时读到的原值乘、
  不累乘，已经记下的时刻和沿用的结论不动；识别线程（Watcher）在两帧之间换，开关变了才换，下载完图片重建识别流程时照开关
  重新套上。
- 文字识别：低配模式开着时按 chip_select.LOWEND_OCR_THREADS 个线程（use_lowend_threads），识别线程在两帧之间换：缓存的
  引擎线程数不同时丢掉、下一次识别重建，正在识别的那一帧照旧；关掉时回到 OCR_THREADS 个；核心少、算下来一样时不重建；
  重建识别流程时不跟着换；开关没开过时不碰（测试和基准工具改的 OCR_THREADS 照旧有效）；同一进程里别处换过全进程的那一份
  开关时，下一帧照识别线程自己的低配模式换回来。
- 设置：UiSettings.lowend（默认关；旧文件没有这一项、读到不是 true / false 的值都按关）；Overlay.set_lowend 存盘、记一条
  用户操作、经 lowend_changed 告诉识别线程；main() 在识别线程开始以前按设置套上，并接好信号；诊断包的 overlay 段写着
  开没开。
- 打开文件夹：winapi.open_folder 按普通优先级另起 explorer（起进程的调用换成假的，不真的起）；提示里的「打开文件夹」经
  open_folder。
- 运行状况：main() 在 build() 之前向 Windows 声明要高性能（declare_high_performance，换成假的、不真的
  改）；界面线程不提高优先级，识别线程、热键线程都照普通优先级起（QThread 默认跟着起它的线程）；等「显示」请求的线程不
  带 on_start；build() 不声明，工具和测试照旧。声明成没成都不抛出。

识别线程真的起一个（取帧对象、识别流程是假的，不截屏）；要等的时候记下等多久，不真的等。文字识别引擎换成假的类（不建
真的引擎）。用 Qt 的 offscreen 平台，不开窗口；设置文件写在测试沙箱的临时文件夹里。
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PySide6.QtCore import QThread

from advisor.data import load_db
from advisor.session import GAME_FIELDS, Session, Settings
from advisor.ui import screen, winapi
from advisor.ui import single_instance as si
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import app as app_mod
from advisor.ui.overlay_parts import watcher as watcher_mod
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.services import Services
from advisor.vision import chip_select, pipeline
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tests.support.widgets import dispose
from tests.test_vision_robust import OFFER, FlowBase

APP = qtenv.app()
DB = load_db()
WATCH = DB.rules["watch"]
IDLE_MS, ACTIVE_MS, FACTOR = WATCH["idle_ms"], WATCH["active_ms"], WATCH["lowend_factor"]
# 子识别错误（FrameProcessor.errors 的一项）：（这一项的名字、异常、调用栈的文字）
ERROR = ("stars", RuntimeError("读星币数出错"), "Traceback（假的调用栈）\nRuntimeError: 读星币数出错")


class RulesTest(unittest.TestCase):
    def test_factor_written_out(self):
        """rules.toml 写明的倍数和代码里的默认值一致；按实测平时的间隔翻倍、相关场景的 CPU 约减半，定为 2。"""
        self.assertEqual(FACTOR, watcher_mod.LOWEND_FACTOR)
        self.assertEqual(FACTOR, 2)


# ---------------------------------------------------------------- 识别流程的节奏（FrameProcessor.set_pace）


class PaceTest(FlowBase):
    """FrameProcessor.set_pace。识别函数换成假的（FlowBase）。"""

    def paces(self) -> tuple:
        p = self.proc
        return (p.hud_s, p.hero_s, p.coins_s, p._not_select.max_s, p._not_pick.max_s, p.reread_s, p.retry_s,
                p.occluded_s, p.occluded_full_s, p.blank_full_s, p.block_s, p.select_s, p.pick_grace_s)

    def test_from_original_not_cumulative(self):
        """按构造时读到的原值乘，再套一次也不累乘；改回 1 倍时逐位是原值。对局中刚看见过轮次标记多久以内先不判选角界面
        （pick_grace_ms）也乘：读轮次的间隔跟着乘，一段读不到轮次的时间里先不判的次数和原来一样。"""
        base = self.paces()
        unchanged = WATCH["unchanged_max_ms"] / 1000
        self.assertEqual(base, (WATCH["hud_ms"] / 1000, WATCH["hero_ms"] / 1000, WATCH["coins_ms"] / 1000,
                                unchanged, unchanged, WATCH["select_reread_ms"] / 1000, WATCH["hud_retry_ms"] / 1000,
                                WATCH["occluded_recheck_ms"] / 1000, WATCH["occluded_full_ms"] / 1000,
                                WATCH["blank_full_ms"] / 1000, WATCH["map_block_ms"] / 1000,
                                WATCH["select_ms"] / 1000, WATCH["pick_grace_ms"] / 1000))
        doubled = tuple(v * FACTOR for v in base)
        self.proc.set_pace(FACTOR)
        self.assertEqual(self.paces(), doubled)
        self.proc.set_pace(FACTOR)
        self.assertEqual(self.paces(), doubled)
        self.proc.set_pace(1)
        self.assertEqual([repr(v) for v in self.paces()], [repr(v) for v in base])

    def state(self) -> dict:
        """识别流程身上的全部状态（两个「画面没变就不再认」的对象展开一层）。"""
        out = {}
        for key, value in vars(self.proc).items():
            if isinstance(value, pipeline._Unchanged):
                out.update({f"{key}.{k}": v for k, v in vars(value).items()})
            else:
                out[key] = value
        return out

    def test_recorded_state_kept(self):
        """只换这几个间隔：读过的时刻、这一局读了几次队友、三选一的沿用结果、「画面没变就不再认」的结论都是原来那个对象，
        切换一次开关不会多读一轮。"""
        self.round = 4
        self.step(0.0)
        self.seen = SimpleNamespace(names=list(OFFER), raw=list(OFFER), rerolls=2, probabilities=(60, 37, 3),
                                    selected=None, problems=[])
        self.step(1.0)
        self.assertIsNotNone(self.proc._select_cache)
        before = self.state()
        self.proc.set_pace(FACTOR)
        after = self.state()
        self.assertEqual(before.keys(), after.keys())
        changed = {k for k in before if before[k] is not after[k]}
        self.assertEqual(changed, {"hud_s", "hero_s", "coins_s", "_not_select.max_s", "_not_pick.max_s", "progress_s",
                                   "reread_s", "retry_s", "occluded_s", "occluded_full_s", "blank_full_s", "block_s",
                                   "select_s", "pick_grace_s"})
        self.assertEqual((self.proc._last_hud, self.proc._last_hero), (0.0, 0.0))

    def test_hud_cadence(self):
        """对局画面上读轮次：改成 2 倍以后隔 2 倍的 hud_ms；改回来以后从上次读的时刻算，过了 hud_ms 就读。"""
        hud = WATCH["hud_ms"] / 1000
        self.round = 4
        self.step(0.0)
        self.assertEqual(self.calls["hud"], 1)
        self.proc.set_pace(FACTOR)
        self.step(hud)
        self.assertEqual(self.calls["hud"], 1)
        self.step(hud * FACTOR)
        self.assertEqual(self.calls["hud"], 2)
        self.proc.set_pace(1)
        self.step(hud * FACTOR + hud)
        self.assertEqual(self.calls["hud"], 3)


# ---------------------------------------------------------------- 识别线程（Watcher）


class Source:
    """假的取帧对象：每次都取到一幅画面，不截屏。no_frame 时一直报「暂时没有画面」。"""

    state = None

    def __init__(self, no_frame: bool = False):
        self.no_frame = no_frame

    def grab(self):
        if self.no_frame:
            raise screen.NoFrame("暂时没有画面")
        return np.zeros((8, 8, 3), np.uint8)

    def close(self):
        pass


class Probe(Watcher):
    """要等的时候记下等多久，不真的等；记够 waits 次就请线程停下。on_sleep(识别线程, 第几次)：每次等之前调用。"""

    def __init__(self, *args, waits: int = 1, on_sleep=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sleeps: list = []
        self.waits, self.on_sleep = waits, on_sleep

    def _sleep(self, ms):
        self.sleeps.append(ms)
        if self.on_sleep is not None:
            self.on_sleep(self, len(self.sleeps))
        if len(self.sleeps) >= self.waits:
            self.requestInterruption()


def fake_processor(log: list, visible: bool = False, errors=(), pace: bool = True, during=None):
    """假的识别流程：建、改节奏、每一帧都记进 log。during(第几帧)：识别这一帧的中途调用（测试在一帧中途改开关）。
    pace 为假时没有 set_pace（旧的识别流程、测试里别的假对象）。"""

    class Fake:
        chip_select_visible = visible

        def __init__(self, *args):
            self.errors, self.select_failed = [], False
            log.append("new")

        def process(self, img, now, force=False):
            frames = sum(1 for e in log if e == "process") + 1
            if during is not None:
                during(frames)
            log.append("process")
            self.errors = list(errors)
            return []

    if pace:
        Fake.set_pace = lambda self, factor: log.append(("pace", factor))
    return Fake


class WatcherCase(unittest.TestCase):
    def setUp(self):
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), []))
        self.log: list = []
        # 文字识别的低配开关是全进程的一份（chip_select.lowend_threads），识别线程每帧和它比：每个用例从关开始，测完换回
        self.addCleanup(setattr, chip_select, "_lowend", chip_select._lowend)
        chip_select._lowend = False

    def _fake_threads(self, on) -> None:
        """假的 chip_select.use_lowend_threads：记进 log（("threads", 开没开)），只改全进程的那一份开关（识别线程每帧和它比），
        不动模块里缓存的引擎。"""
        self.log.append(("threads", on))
        chip_select._lowend = bool(on)

    def run_watcher(self, fake, lowend=None, waits=1, on_sleep=None, source=None, hotkey=False,
                    holder: dict | None = None, db=DB, real_threads=False) -> Probe:
        """跑识别线程，等够 waits 次就停。lowend 不是 None 时线程开始以前套上；holder 里放上识别线程（假的识别流程要用）。
        文字识别的线程数（chip_select.use_lowend_threads）平时换成假的（_fake_threads），不动模块里缓存的引擎；
        real_threads 时用真的（OcrThreadsTest 另外换掉建引擎）。"""
        threads = contextlib.nullcontext() if real_threads else mock.patch.object(
            chip_select, "use_lowend_threads", side_effect=self._fake_threads)
        with mock.patch("advisor.vision.pipeline.FrameProcessor", fake), threads, \
                mock.patch("advisor.vision.reset_caches"):  # 重建识别流程时不动真的图片缓存
            watcher = Probe(db, Session(db, Settings(hero="凛")), threading.Lock(), source=source or Source(),
                            waits=waits, on_sleep=on_sleep)
            if holder is not None:
                holder["w"] = watcher
            if lowend is not None:
                watcher.set_lowend(lowend)
            if hotkey:
                watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        return watcher


class WatcherPaceTest(WatcherCase):
    """识别线程的节奏、文字识别的线程数和换的时机。"""

    def test_off_untouched(self):
        """开关没开过：照 [watch] 原来的节奏，不改识别流程的节奏，也不碰文字识别的线程数。"""
        watcher = self.run_watcher(fake_processor(self.log), waits=2)
        self.assertEqual(watcher.sleeps, [IDLE_MS, IDLE_MS])
        self.assertEqual(self.log, ["new", "process", "process"])

    def test_on_before_start(self):
        """线程开始以前套上：第一帧之前就换好节奏和线程数，平时的间隔乘倍数。"""
        watcher = self.run_watcher(fake_processor(self.log), lowend=True)
        self.assertEqual(watcher.sleeps, [IDLE_MS * FACTOR])
        self.assertEqual(self.log, ["new", ("pace", FACTOR), ("threads", True), "process"])

    def test_factor_from_rules(self):
        """倍数按 [watch] lowend_factor 取（这里改成 3），不是写死的默认值。"""
        db = dataclasses.replace(DB, rules={**DB.rules, "watch": {**WATCH, "lowend_factor": 3}})
        watcher = self.run_watcher(fake_processor(self.log), lowend=True, db=db)
        self.assertEqual(watcher.sleeps, [IDLE_MS * 3])
        self.assertEqual(self.log, ["new", ("pace", 3), ("threads", True), "process"])

    def test_active_and_error_waits_unchanged(self):
        """三选一显示着时照常按 active_ms（看晚了会漏记选了哪个）；出错以后照旧隔 2 秒。"""
        self.assertEqual(self.run_watcher(fake_processor(self.log, visible=True), lowend=True).sleeps, [ACTIVE_MS])
        failed = self.run_watcher(fake_processor(self.log, errors=[ERROR]), lowend=True)
        self.assertEqual(failed.sleeps, [2000])
        self.errors.clear()  # 这一帧的错照常记进了错误记录

    def test_no_frame_retries_unchanged(self):
        """按热键要求的那一帧暂时没有画面：照旧隔 NO_FRAME_WAIT_MS 重取，重取完了按低配模式的平时间隔再取。"""
        watcher = self.run_watcher(fake_processor(self.log), lowend=True, waits=Watcher.NO_FRAME_RETRIES + 1,
                                   source=Source(no_frame=True), hotkey=True)
        self.assertEqual(watcher.sleeps, [Watcher.NO_FRAME_WAIT_MS] * Watcher.NO_FRAME_RETRIES + [IDLE_MS * FACTOR])

    def test_switched_between_frames(self):
        """一帧识别到一半时（别的线程）打开、再过一帧中途关掉：这一帧照旧，下一帧开始前才换，开关变一次只换一回。"""
        holder: dict = {}

        def during(frame):
            if frame == 1:
                other = threading.Thread(target=holder["w"].set_lowend, args=(True,))
                other.start()
                other.join()
            elif frame == 3:
                holder["w"].set_lowend(False)

        watcher = self.run_watcher(fake_processor(self.log, during=during), waits=4, holder=holder)
        self.assertEqual(self.log, ["new", "process", ("pace", FACTOR), ("threads", True), "process", "process",
                                    ("pace", 1), ("threads", False), "process"])
        self.assertEqual(watcher.sleeps, [IDLE_MS, IDLE_MS * FACTOR, IDLE_MS * FACTOR, IDLE_MS])

    def test_reload_reapplies(self):
        """下载完图片后重建识别流程：新建的照开关套上低配模式的节奏；文字识别的线程数是全进程的，不跟着再换一次。"""
        watcher = self.run_watcher(fake_processor(self.log), lowend=True, waits=2,
                                   on_sleep=lambda probe, n: probe.reload() if n == 1 else None)
        self.assertEqual(self.log, ["new", ("pace", FACTOR), ("threads", True), "process", "new", ("pace", FACTOR),
                                    "process"])
        self.assertEqual(watcher.sleeps, [IDLE_MS * FACTOR] * 2)

    def test_reload_and_switch_off_together(self):
        """关掉低配模式和重建识别流程赶在同一帧之前：新建的识别流程本来就是原来的节奏，文字识别的线程数照样换回来。"""

        def on_sleep(probe, n):
            if n == 1:
                probe.set_lowend(False)
                probe.reload()

        watcher = self.run_watcher(fake_processor(self.log), lowend=True, waits=2, on_sleep=on_sleep)
        self.assertEqual(self.log, ["new", ("pace", FACTOR), ("threads", True), "process", "new", ("threads", False),
                                    "process"])
        self.assertEqual(watcher.sleeps, [IDLE_MS * FACTOR, IDLE_MS])

    def test_threads_reapplied_after_changed_elsewhere(self):
        """同一进程里别处把文字识别的低配开关换掉了（性能测试测完换回测之前的样子）：识别线程下一帧照自己的低配模式再换回来，
        识别流程的节奏不重套；之后两边一样，不再调用。"""

        def during(frame):
            if frame == 1:
                chip_select._lowend = False  # 第 1 帧中途别处换回了关

        watcher = self.run_watcher(fake_processor(self.log, during=during), lowend=True, waits=3)
        self.assertEqual(self.log, ["new", ("pace", FACTOR), ("threads", True), "process", ("threads", True), "process",
                                    "process"])
        self.assertTrue(chip_select._lowend)
        self.assertEqual(watcher.sleeps, [IDLE_MS * FACTOR] * 3)

    def test_threads_switched_off_after_changed_elsewhere(self):
        """低配模式关着、别处把开关换成了开（没换回来）：识别线程下一帧换回关。"""

        def during(frame):
            if frame == 1:
                chip_select._lowend = True

        self.run_watcher(fake_processor(self.log, during=during), waits=2)
        self.assertEqual(self.log, ["new", "process", ("threads", False), "process"])
        self.assertFalse(chip_select._lowend)

    def test_processor_without_set_pace(self):
        """识别流程没有 set_pace（测试里别的假对象）：不出错，平时的间隔照样乘倍数。"""
        watcher = self.run_watcher(fake_processor(self.log, pace=False), lowend=True)
        self.assertEqual(watcher.sleeps, [IDLE_MS * FACTOR])
        self.assertEqual(self.log, ["new", ("threads", True), "process"])

    def test_set_lowend_any_thread(self):
        """哪个线程调用都可以（线程开始以前也行）：设在锁里，叫醒识别线程；读回来是 True / False。"""
        watcher = Watcher(DB, Session(DB, Settings()), threading.Lock())
        self.assertIs(watcher.lowend, False)
        other = threading.Thread(target=watcher.set_lowend, args=(1,))
        other.start()
        other.join()
        self.assertIs(watcher.lowend, True)
        self.assertTrue(watcher._wake.is_set())
        watcher.set_lowend(0)
        self.assertIs(watcher.lowend, False)


class FakeEngine:
    """假的文字识别引擎（代替 rapidocr_lite.RapidOCR）：记下按几个线程建的。"""

    built: list = []

    def __init__(self, params=None):
        self.threads = params["EngineConfig.onnxruntime.intra_op_num_threads"]
        FakeEngine.built.append(self)

    def __call__(self, img, **kwargs):
        return SimpleNamespace(boxes=None, txts=None, scores=None)


class OcrCase(WatcherCase):
    """建引擎换成 FakeEngine；模块里缓存的引擎、它的线程数、低配模式的开关和 OCR_THREADS 测试完换回原来的。核心数固定成
    16（cores 可改）。"""

    cores = 16

    def setUp(self):
        super().setUp()
        for name in ("_ocr", "_ocr_threads", "_lowend", "OCR_THREADS"):
            self.addCleanup(setattr, chip_select, name, getattr(chip_select, name))
        chip_select._ocr, chip_select._ocr_threads, chip_select._lowend = None, None, False
        chip_select.OCR_THREADS = 4
        FakeEngine.built = []
        for patcher in (mock.patch("rapidocr_lite.RapidOCR", FakeEngine),
                        mock.patch.object(chip_select.paths, "ocr_models_dir", return_value=Path("models")),
                        mock.patch.object(chip_select.os, "cpu_count", side_effect=lambda: self.cores)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def built(self) -> list[int]:
        return [e.threads for e in FakeEngine.built]


class OcrThreadsFunctionTest(OcrCase):
    """chip_select.ocr_threads / use_lowend_threads 本身。"""

    def test_threads(self):
        self.assertEqual(chip_select.LOWEND_OCR_THREADS, 2)
        self.assertEqual(chip_select.ocr_threads(), 4)
        chip_select.use_lowend_threads(True)  # 还没建引擎：只记下开关
        self.assertEqual((chip_select.ocr_threads(), chip_select._ocr), (2, None))
        self.assertEqual(chip_select._engine().threads, 2)
        chip_select.use_lowend_threads(False)
        self.assertIsNone(chip_select._ocr)  # 线程数不同：丢掉，下一次按 4 个重建
        self.assertEqual(chip_select._engine().threads, 4)
        self.assertEqual(self.built(), [2, 4])

    def test_use_ocr_threads(self):
        """量出来的线程数（use_ocr_threads）：换成它，缓存的引擎线程数不同才丢掉、下一次识别重建；低配模式开着时
        照旧用 2 个，量出来的结果等关掉才生效。"""
        self.assertEqual(chip_select._engine().threads, 4)
        chip_select.use_ocr_threads(8)
        self.assertEqual((chip_select.OCR_THREADS, chip_select.ocr_threads()), (8, 8))
        self.assertIsNone(chip_select._ocr)  # 4 和 8 不同：丢掉
        self.assertEqual(chip_select._engine().threads, 8)
        engine = chip_select._engine()
        chip_select.use_ocr_threads(8)  # 和现在相同：不重建
        self.assertIs(chip_select._engine(), engine)
        chip_select.use_lowend_threads(True)  # 低配模式开着：按 2 个建
        self.assertEqual(chip_select._engine().threads, 2)
        chip_select.use_ocr_threads(4)  # 低配开着时换成 4：ocr_threads 仍是 2，缓存的引擎不动
        self.assertEqual((chip_select.OCR_THREADS, chip_select.ocr_threads()), (4, 2))
        self.assertEqual(chip_select._engine().threads, 2)
        chip_select.use_lowend_threads(False)  # 关掉低配模式：按量出来的 4 个重建
        self.assertIsNone(chip_select._ocr)
        self.assertEqual(chip_select._engine().threads, 4)
        self.assertEqual(self.built(), [4, 8, 2, 4])

    def test_same_count_kept(self):
        """开关没变、或核心少算下来一样：缓存的引擎不动。"""
        engine = chip_select._engine()
        chip_select.use_lowend_threads(False)
        self.assertIs(chip_select._engine(), engine)
        self.cores = 2  # 只有 2 个核心：平时也只用 2 个线程
        chip_select._ocr = None
        engine = chip_select._engine()
        chip_select.use_lowend_threads(True)
        self.assertIs(chip_select._engine(), engine)
        self.assertEqual(self.built(), [4, 2])


class OcrThreadsTest(OcrCase):
    """识别线程在两帧之间换文字识别的线程数：低配模式开着时 LOWEND_OCR_THREADS 个，关掉时回到 OCR_THREADS 个；缓存的引擎
    线程数不同时丢掉、下一次识别重建，正在识别的那一帧照旧用原来的引擎；核心少、算下来一样时不重建；开关没开过时不碰。"""

    def run_frames(self, frames: int, toggles: dict, lowend=None) -> list[tuple]:
        """跑 frames 帧：每一帧用两次引擎（识别的开头和末尾），toggles[第几帧] 是这一帧中途（两次之间）设的开关。
        返回每一帧用的两个引擎。"""
        holder, used = {}, []

        class Fake:
            chip_select_visible = False

            def __init__(self, *args):
                self.errors, self.select_failed = [], False

            def set_pace(self, factor):
                pass

            def process(self, img, now, force=False):
                first = chip_select._engine()
                if len(used) + 1 in toggles:
                    holder["w"].set_lowend(toggles[len(used) + 1])
                used.append((first, chip_select._engine()))
                return []

        self.run_watcher(Fake, lowend=lowend, waits=frames, holder=holder, real_threads=True)
        self.assertEqual(len(used), frames)
        for first, last in used:
            self.assertIs(first, last)  # 正在识别的那一帧不受影响
        return [first for first, _ in used]

    def test_switched_between_frames(self):
        """第 1 帧中途打开、第 3 帧中途关掉：第 2 帧起按 2 个线程，第 4 帧起回到 4 个；中间不变的那一帧沿用同一个引擎。"""
        a, b, c, d = self.run_frames(4, {1: True, 3: False})
        self.assertIs(b, c)
        self.assertEqual(len({id(a), id(b), id(d)}), 3)
        self.assertEqual([e.threads for e in (a, b, d)], [4, 2, 4])
        self.assertEqual(self.built(), [4, 2, 4])

    def test_on_before_start(self):
        self.assertEqual([e.threads for e in self.run_frames(2, {}, lowend=True)], [2, 2])
        self.assertEqual(self.built(), [2])

    def test_never_switched(self):
        """开关没开过：不调 use_lowend_threads，测试和基准工具改的 OCR_THREADS（这里是 1）照旧有效。"""
        chip_select.OCR_THREADS = 1
        with mock.patch.object(chip_select, "use_lowend_threads", wraps=chip_select.use_lowend_threads) as use:
            engines = self.run_frames(3, {})
        use.assert_not_called()
        self.assertEqual(len({id(e) for e in engines}), 1)
        self.assertEqual(self.built(), [1])

    def test_few_cores_not_rebuilt(self):
        """只有 2 个核心：平时也只用 2 个线程，打开低配模式算下来一样，不重建引擎。"""
        self.cores = 2
        engines = self.run_frames(3, {1: True})
        self.assertEqual(len({id(e) for e in engines}), 1)
        self.assertEqual(self.built(), [2])


# ---------------------------------------------------------------- 设置和悬浮窗


class SettingsTest(unittest.TestCase):
    """UiSettings.lowend：默认关；旧文件没有这一项按关，存的时候补上；读到不是 true / false 的值只这一项按关。"""

    def setUp(self):
        self.path = sandbox.temp_dir(self, "test-lowend-") / "state.json"

    def test_default_and_old_file(self):
        self.assertIs(UiSettings().lowend, False)
        self.assertNotIn("lowend", GAME_FIELDS)  # 跨对局、跨启动的设置
        self.path.write_text(json.dumps({"hero": "芬妮", "opaque": True}), encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            got = UiSettings.load(self.path)
        self.assertIs(got.lowend, False)
        self.assertEqual(got.save(self.path), self.path)
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["lowend"], False)

    def test_bad_values(self):
        self.assertIs(UiSettings(lowend="yes").lowend, False)
        self.assertIs(UiSettings(lowend=1).lowend, False)
        for raw in (1, 0, "true", None, [True], {"x": 1}):
            with self.subTest(raw=raw):
                self.path.write_text(json.dumps({"hero": "邦妮", "lowend": raw}), encoding="utf-8")
                with self.assertLogs("advisor.prefs", level="WARNING"):
                    got = UiSettings.load(self.path)
                self.assertEqual((got.lowend, got.hero), (False, "邦妮"))

    def test_saved_and_read_back(self):
        self.assertEqual(UiSettings(hero="芬妮", lowend=True).save(self.path), self.path)
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["lowend"], True)
        self.assertIs(UiSettings.load(self.path).lowend, True)


class OverlayTest(unittest.TestCase):
    """Overlay.set_lowend：改设置、存盘、记一条用户操作（read:user，action 为 lowend），经 lowend_changed 告诉识别线程；
    和当前相同时什么都不做；重启后照旧。"""

    def setUp(self):
        self.path = sandbox.temp_dir(self, "test-lowend-ov-") / "state.json"
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), []))

    def make(self, settings: UiSettings | None = None) -> Overlay:
        session = OverlaySession(DB, settings or UiSettings(hero="凛"), self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(DB, session, threading.Lock(), services=Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(dispose, w)
        return w

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["lowend"]

    def test_toggle(self):
        w = self.make()
        self.assertIs(w.lowend, False)
        got: list = []
        w.lowend_changed.connect(got.append)
        with mock.patch.object(w.session, "save", wraps=w.session.save) as save:
            w.set_lowend(True)
            self.assertEqual((w.lowend, w.session.settings.lowend, self.saved(), got), (True, True, True, [True]))
            w.set_lowend(True)  # 和当前相同
            self.assertEqual((got, save.call_count), ([True], 1))
            w.set_lowend(False)
            self.assertEqual((w.lowend, self.saved(), got, save.call_count), (False, False, [True, False], 2))
        users = [e for e in w.session.recent_events(600) if e["kind"] == "read:user"]
        self.assertEqual([(e["action"], e["on"]) for e in users], [("lowend", True), ("lowend", False)])
        w.set_lowend(True)
        self.assertIs(self.make(UiSettings.load(self.path)).lowend, True)  # 重启后照旧

    def test_tells_watcher(self):
        """照 main() 的接法接上识别线程（不启动）：切换以后识别线程马上知道。"""
        w = self.make()
        watcher = Watcher(DB, w.session, w.lock)
        w.lowend_changed.connect(watcher.set_lowend)
        w.set_lowend(True)
        self.assertIs(watcher.lowend, True)
        w.set_lowend(False)
        self.assertIs(watcher.lowend, False)

    def test_diagnostics_state(self):
        """诊断包的对局状态（state.json 的 overlay 段）写着低配模式开没开。"""
        w = self.make()
        self.assertIs(w.background.snapshot()["overlay"]["lowend"], False)
        w.set_lowend(True)
        self.assertIs(w.background.snapshot()["overlay"]["lowend"], True)

    def test_open_export_folder(self):
        """提示里的「打开文件夹」经 winapi.open_folder（资源管理器按普通优先级起），不直接 os.startfile。"""
        w = self.make()
        folder = sandbox.temp_dir(self, "test-lowend-export-")
        w.notice_model.export_path = str(folder)
        with mock.patch.object(winapi, "open_folder") as opened, \
                mock.patch.object(os, "startfile", side_effect=AssertionError("不该直接 os.startfile"), create=True):
            w.notices.open_export()
        opened.assert_called_once_with(str(folder))


class MainTest(unittest.TestCase):
    """main() 的接线和运行状况：界面、识别、热键、等待线程、声明高性能都换成假的（不改测试进程的运行状况）。"""

    def run_main(self, lowend: bool) -> SimpleNamespace:
        """跑一遍 main()（事件循环马上结束）：lowend 是设置里的低配模式；向 Windows 声明要高性能（declare_high_performance）
        换成假的。"""
        order: list = []
        app = mock.Mock()
        app.exec.side_effect = lambda: order.append("exec") or 0
        window = mock.MagicMock()
        window.lowend = lowend
        watcher = mock.MagicMock()
        watcher.stop.return_value = True
        watcher.set_lowend.side_effect = lambda on: order.append(("watcher.set_lowend", on))
        watcher.start.side_effect = lambda *args: order.append(("watcher.start", *args))
        hotkey = mock.MagicMock()
        hotkey.start.side_effect = lambda *args: order.append(("hotkey.start", *args))
        listen = mock.Mock(side_effect=lambda *args, **kwargs: order.append("listen") or mock.MagicMock())
        high = mock.Mock(side_effect=lambda: order.append("declare_high_performance"))

        def build(**kwargs):
            order.append("build")
            return (app, window,
                    SimpleNamespace(eventlog=SimpleNamespace(recorder=None), save=lambda force=False: None,
                                    settings=SimpleNamespace(big_cores=False, lowend=lowend)),
                    threading.Lock())

        with mock.patch.object(app_mod, "build", side_effect=build), \
                mock.patch.object(app_mod, "Watcher", return_value=watcher), \
                mock.patch.object(app_mod, "Hotkey", return_value=hotkey), \
                mock.patch.object(app_mod, "QTimer"), \
                mock.patch.object(app_mod, "expected_capture", return_value="window"), \
                mock.patch.object(app_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", listen), \
                mock.patch.object(app_mod, "declare_high_performance", high), \
                mock.patch.object(app_mod, "terminate_process", side_effect=AssertionError("进程内不许直接结束进程")), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit) as raised:
                app_mod.main()
        self.assertEqual(raised.exception.code, 0)
        return SimpleNamespace(order=order, window=window, watcher=watcher, listen=listen, high=high)

    def test_wiring_and_high_performance(self):
        """先声明高性能再建界面；识别线程、热键线程都照普通优先级起（不跟着界面线程），等待线程不带 on_start。"""
        got = self.run_main(lowend=True)
        order = got.order
        self.assertEqual(order[:2], ["declare_high_performance", "build"])
        got.high.assert_called_once_with()
        start = ("watcher.start", QThread.Priority.NormalPriority)
        self.assertIn(start, order)
        self.assertLess(order.index(("watcher.set_lowend", True)), order.index(start))
        self.assertIn(("hotkey.start", QThread.Priority.NormalPriority), order)
        got.window.lowend_changed.connect.assert_called_once_with(got.watcher.set_lowend)
        got.listen.assert_called_once_with(got.window.relaunch_requested.emit)

    def test_off_setting(self):
        self.assertIn(("watcher.set_lowend", False), self.run_main(lowend=False).order)

    def test_build_does_not_declare(self):
        """build() 给工具和测试用，不向 Windows 声明高性能、不动运行状况。"""
        folder = sandbox.temp_dir(self, "test-lowend-build-")
        boom = mock.Mock(side_effect=AssertionError("build() 不该声明高性能"))
        with mock.patch.object(app_mod, "declare_high_performance", boom), \
                mock.patch.object(app_mod, "STATE_FILE", folder / "state.json"), \
                mock.patch.object(app_mod, "open_scheme_store", return_value=None):
            _, window, _, _ = app_mod.build(DB)
        self.addCleanup(dispose, window)
        boom.assert_not_called()


# ---------------------------------------------------------------- 按普通优先级打开文件夹


class OpenExternalTest(unittest.TestCase):
    """winapi.open_folder：按普通优先级另起 explorer（系统文件夹里的完整路径），不开控制台窗口，资源管理器随它按普通优先级起；
    只认已有的文件夹；起不了时退回 os.startfile，只记一次错误记录。起进程的调用都换成假的，不真的起任何程序。"""

    def setUp(self):
        self.errors = capture_errors(self)
        self.patch(winapi, "_open_logged", False)
        self.popen = self.patch(winapi.subprocess, "Popen")
        self.startfile = self.patch(winapi.os, "startfile", side_effect=AssertionError("不该退回 os.startfile"),
                                    create=True)
        self.explorer = Path(os.environ.get("SystemRoot") or os.environ["windir"]) / "explorer.exe"

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def same_file(self, a, b):
        self.assertEqual(os.path.normcase(str(a)), os.path.normcase(str(b)))

    def check_flags(self, call):
        flags = call.kwargs["creationflags"]
        self.assertEqual(flags & 0x20, 0x20)  # NORMAL_PRIORITY_CLASS
        self.assertEqual(flags & 0x08000000, 0x08000000)  # CREATE_NO_WINDOW
        self.assertEqual(flags & 0x4000, 0)  # 不是 BELOW_NORMAL_PRIORITY_CLASS
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(call.kwargs[stream], subprocess.DEVNULL)

    def test_folder(self):
        """路径加引号整个交给 explorer（空格、逗号、中文都在一个参数里）。"""
        folder = sandbox.temp_dir(self, "test-lowend-open-") / "诊断包 1,2"
        folder.mkdir()
        self.assertIs(winapi.open_folder(folder), True)
        self.popen.assert_called_once()
        call = self.popen.call_args
        self.same_file(call.kwargs["executable"], self.explorer)
        self.assertEqual(call.args[0], f'"{call.kwargs["executable"]}" "{folder}"')
        self.check_flags(call)

    def test_folder_rejected(self):
        folder = sandbox.temp_dir(self, "test-lowend-open-")
        (folder / "a.txt").write_text("x", encoding="utf-8")
        for path in (folder / "没有这个文件夹", folder / "a.txt"):
            with self.subTest(path=path):
                self.assertIs(winapi.open_folder(path), False)
        self.popen.assert_not_called()

    def test_fallback_logged_once(self):
        """起不了另一个程序：退回 os.startfile（照样打开），错误记录只记一次。"""
        self.popen.side_effect = OSError("起不了")
        self.startfile.side_effect = None
        folder = sandbox.temp_dir(self, "test-lowend-open-")
        self.assertIs(winapi.open_folder(folder), True)
        self.assertEqual([c.args for c in self.startfile.call_args_list], [(str(folder),)])
        entries = self.errors.entries()
        self.assertEqual(len(entries), 1, entries)
        self.assertIn("起不了", entries[0][1])


@unittest.skipIf(si._api is None, "Win32 调用用不了")
class ListenerStartTest(unittest.TestCase):
    """等「显示」请求的线程：on_start 在它自己的线程里、开始等之前调用一次；出错只记进错误记录，照样等。只用不起名的事件，
    不碰防多开的名字。"""

    def setUp(self):
        self.api = si._api
        self.show = self.api.create_event()
        self.addCleanup(self.api.close, self.show)
        self.errors = capture_errors(self)

    def test_on_start_in_listener_thread(self):
        started, shown = [], threading.Event()
        listener = si.Instance(self.api, 0, self.show, 0).listen(
            shown.set, on_start=lambda: started.append((threading.current_thread(), shown.is_set())))
        self.addCleanup(listener.stop)
        self.api.set_event(self.show)
        self.assertTrue(shown.wait(5))
        self.assertEqual(len(started), 1)
        thread, already = started[0]
        self.assertEqual((thread.name, already), ("relaunch-listener", False))
        self.assertEqual(self.errors.entries(), [])

    def test_on_start_error_keeps_listening(self):
        shown = threading.Event()
        listener = si.Listener(self.api, self.show, shown.set, on_start=mock.Mock(side_effect=OSError("设不了")))
        self.addCleanup(listener.stop)
        self.api.set_event(self.show)
        self.assertTrue(shown.wait(5))
        entries = self.errors.entries()
        self.assertEqual(len(entries), 1, entries)
        self.assertIn("设不了", entries[0][1])

    def test_module_listen_passes_on_start(self):
        held, callback, start = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(si, "_held", held):
            si.listen(callback, on_start=start)
        held.listen.assert_called_once_with(callback, start)
        with mock.patch.object(si, "_held", None):
            self.assertFalse(si.listen(callback, on_start=start).running)  # 没拿到锁：不起线程
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
