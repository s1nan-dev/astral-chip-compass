"""三选一界面上点了「隐藏」：三选一整个收起、只剩棋盘，正中偏下出现一行「您当前正处于隐藏UI状态」，点「显示」后
三选一照原样回来。认出那行字时当三选一还开着：不记下选中的、不撤销，推荐留着。

A. 认那行字（advisor/vision/chip_select.py 的 hidden_ui_visible，HiddenTextTest、HiddenTextLogicTest）：一局实机录像里的四帧
   缩到 960×540 的样本：隐藏之前、隐藏着两张（一张左下角被轮盘挡了一部分）、点显示以后；上下加黑边成 4:3（三选一界面居中、
   贴着下边各一种）、左右加黑边成 21:9 的窗口；隐藏着的画面过不了三选一的预筛。文字识别换成假的：空画面不调它，读到的字
   读漏一两个、把握低、是别的字时怎么算。样本不随仓库发布，放在 samples/pages/，缺了就跳过。
B. 记账（SelectTracker.hidden、Session.on_select_hidden，HiddenLedgerTest）：录像里那一次（隐藏后点显示：不撤销、不重发推荐，
   确定后记一次）；隐藏着时间到了（那行字没了、三选一没回来：照常按绿框记，打开面板后按面板改正）；那一帧没认出字（照常先记
   后撤，结果仍然对）；隐藏两次；三选一没显示时不起作用；识别记录在开始隐藏时记一条。
C. 识别流程（FrameProcessor，HiddenFlowTest）：只在三选一正要按关掉处理的那一帧看那行字（平时、三选一显示着、消失后的第 1 帧
   都不看），隐藏着时每帧看一次；认出时交给 Session.on_select_hidden；认那行字出错时照常按关掉处理。按热键要求的那一帧也看
   一眼：刚隐藏的第 1 帧照样当三选一还开着；Session 不知道的那一次（手动模式下没按过热键就隐藏了）认出后每帧接着看，
   点显示后照常认出、给推荐，那行字没了就不再看。识别函数换成假的。
D. 悬浮窗的识别线程（Watcher，HiddenFollowTest）：手动模式跟随三选一时，隐藏着也接着看（取帧按平时的间隔），隐藏结束后照常
   再看几帧就停；不是真的 True（测试里的假对象）不算。按热键的那一帧是隐藏着的时候不报「当前画面不是筹码三选一界面」。
"""
from __future__ import annotations

import collections
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np
from PySide6.QtCore import Qt

from advisor.data import load_db
from advisor.session import Session, Settings, split_extra
from advisor.ui.overlay_parts.wording import FOLLOW_FRAMES
from advisor.vision import chip_select, pipeline
from advisor.vision.chip_select import hidden_ui_visible, looks_like_overlay_screen
from tests.support import qtenv, samples
from tests.test_pipeline import _FakeReads, offer
from tests.test_resume import ADVISE, fake_advise, select
from tests.test_watcher_pace import ACTIVE_MS, IDLE_MS, PaceProbe, Source

DB = load_db()
# 录像里隐藏的那一次：绿框在夹心饼干-美味上
OFFER = ["标靶", "可口糖果", "夹心饼干-美味"]
GREEN = 2
# 和三选一有关的事件（其余的状态、提示类事件不看）
SELECT_KINDS = ("advice", "picked", "missed_pick", "idle", "pick_reverted")

# ------------------------------------------------------------------ A. 认那行字

BEFORE = "pages/20261017_012808_f000434_chipselect_before_hide_960x540.png"  # 三选一开着（另一组候选）
HIDDEN = "pages/20261017_012808_f000445_chipselect_hidden_960x540.png"  # 隐藏着
WHEEL = "pages/20261017_012808_f000500_chipselect_hidden_wheel_960x540.png"  # 隐藏着，左下角被轮盘挡了一部分
SHOWN = "pages/20261017_012808_f000508_chipselect_shown_again_960x540.png"  # 点了显示，三选一回来了
SHOWING = {BEFORE: False, HIDDEN: True, WHEEL: True, SHOWN: False}  # 画面上有没有那行字
HAVE_FRAMES = samples.samples_ok(*SHOWING)


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def windows(img: np.ndarray):
    """同一幅 16:9 的画面放进几种比例的窗口：比 16:9 窄时游戏界面按宽度缩放、上下空出来（三选一界面居中，或贴着下边），
    比 16:9 宽时左右空出来。"""
    def pad(top: int, bottom: int, side: int) -> np.ndarray:
        return cv2.copyMakeBorder(img, top, bottom, side, side, cv2.BORDER_CONSTANT, value=(0, 0, 0))

    h, w = img.shape[:2]
    extra = round(w * 3 / 4) - h  # 补成 4:3 要多出来的高度
    yield "16:9", img
    yield "4:3 居中", pad(extra // 2, extra - extra // 2, 0)
    yield "4:3 贴着下边", pad(extra, 0, 0)
    yield "21:9", pad(0, 0, round((h * 21 / 9 - w) / 2))


@unittest.skipUnless(HAVE_FRAMES, samples.samples_reason(*SHOWING))
class HiddenTextTest(unittest.TestCase):
    """A：实机录像帧上认那行字（真的文字识别）。"""

    def test_frames(self):
        """隐藏着的两张认得出（轮盘挡的是左下角，那行字完整）；隐藏之前、点显示以后的三选一画面上没有。几种窗口比例都一样。"""
        for name, want in SHOWING.items():
            for label, img in windows(load(name)):
                with self.subTest(name=name, window=label):
                    self.assertIs(hidden_ui_visible(img), want)

    def test_hidden_screen_is_not_select(self):
        """隐藏着的画面上没有三选一（过不了预筛），识别流程照常当成看不到三选一，才轮到看那行字；点显示以后又是三选一。"""
        for name, showing in SHOWING.items():
            with self.subTest(name=name):
                self.assertIs(looks_like_overlay_screen(load(name)), not showing)


class HiddenTextLogicTest(unittest.TestCase):
    """A：认那行字的几条规矩（文字识别换成假的）。"""

    def strip_frame(self) -> np.ndarray:
        """960×540 的画面，那行字的位置上画一块白色（当成有字），其余是暗色。"""
        img = np.full((540, 960, 3), 40, np.uint8)
        cv2.rectangle(img, (400, 370), (560, 386), (255, 255, 255), -1)
        return img

    def check(self, img, result) -> tuple[bool, int]:
        with mock.patch.object(chip_select, "recognize_only", return_value=result) as rec:
            return hidden_ui_visible(img), rec.call_count

    def test_blank_frame_skips_ocr(self):
        """那一条里白色的字不够多（空画面、棋盘）：不调文字识别；画面太小也不看。"""
        self.assertEqual(self.check(np.full((540, 960, 3), 40, np.uint8), [("您当前正处于隐藏UI状态", 0.99)]),
                         (False, 0))
        self.assertEqual(self.check(np.zeros((10, 10, 3), np.uint8), []), (False, 0))

    def test_text_rules(self):
        """读到的字：完整的、读漏一两个字的算；把握低、是别的字（三选一界面上那一条的字）的不算。"""
        img = self.strip_frame()
        cases = [
            ([("您当前正处于隐藏UI状态", 0.99)], True),
            ([("当前正处于隐藏U状态", 0.9)], True),
            ([("您当前正处于隐 藏UI状态", 0.95)], True),  # 中间读出空格
            ([("您当前正处于隐藏UI状态", chip_select.HIDDEN_REC_MIN - 0.01)], False),
            ([("却时间-1。", 0.97)], False),
            ([], False),
        ]
        for result, want in cases:
            with self.subTest(result=result):
                got, calls = self.check(img, result)
                self.assertIs(got, want)
                self.assertGreaterEqual(calls, 1)


# ------------------------------------------------------------------ B. 记账


class PatchedAdvise(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch(ADVISE, fake_advise)
        patcher.start()
        self.addCleanup(patcher.stop)


def panel(owned):
    return SimpleNamespace(is_panel=True, self_tab=True, owned=list(owned), unknown_slots=0, confidence=0.95)


class HiddenLedgerTest(PatchedAdvise):
    """B：三选一被隐藏时的记账。每一帧照识别流程的顺序喂：看得到三选一时 on_frame(读数)；看不到时，正要按关掉处理的那一帧
    认出了那行字就 on_select_hidden，否则 on_frame(None)。"""

    def setUp(self):
        super().setUp()
        self.s = Session(DB, Settings(hero="凛"))
        self.s.skip_panel()
        self.s.on_hero(SimpleNamespace(hero="凛"))  # 角色认出了：三选一出来就给推荐
        self.s.take_events()
        self.events: list = []

    def feed(self, seen=None, hidden: bool = False):
        """一帧：seen 是三选一读数（None 为看不到）；hidden 是这一帧认出了那行字（只在正要按关掉处理时才会认）。"""
        s = self.s
        if hidden:
            self.assertIsNone(seen)
            self.assertTrue(s.select.closing)  # 识别流程只在这样的帧上看那行字
            ev = s.on_select_hidden()
            self.assertIsNone(ev)  # 不发事件
        else:
            ev = s.on_frame(seen)
        before, after = split_extra(s.take_events())
        self.events += before + ([ev] if ev is not None else []) + after

    def kinds(self) -> list:
        return [e.kind for e in self.events if e.kind in SELECT_KINDS]

    def hidden_records(self) -> list:
        return [r for r in self.s.recent_events(600) if r["kind"] == "read:select_hidden"]

    def test_hidden_then_shown(self):
        """录像里的那一次：绿框在夹心饼干-美味上时点了隐藏。消失后第 1 帧照常算看不到；之后几帧认出那行字：不记下、不撤销。
        点显示以后同一组候选回来：接着这一次，不撤销、不重发推荐；之后确定，只记一次。识别记录在开始隐藏时记一条。"""
        self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        for _ in range(6):
            self.feed(hidden=True)
        self.assertEqual(self.s.settings.owned, [])
        self.assertTrue(self.s.select.closing)  # 隐藏期间一直是「正要按关掉处理」
        self.feed(select(OFFER, selected=GREEN))
        self.assertFalse(self.s.select.closing)
        self.feed(None)
        self.feed(None)
        self.assertEqual(self.kinds(), ["advice", "picked"])
        self.assertEqual(self.s.settings.owned, [OFFER[GREEN]])
        records = self.hidden_records()
        self.assertEqual([r["offer"] for r in records], [OFFER])

    def test_shown_with_other_choice(self):
        """隐藏着的时候看过棋盘，点显示以后改选了最左边的：绿框照常认，确定后记下改选的那个。"""
        self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        self.feed(hidden=True)
        self.feed(select(OFFER, selected=0))
        self.feed(None)
        self.feed(None)
        self.assertEqual(self.kinds(), ["advice", "picked"])
        self.assertEqual(self.s.settings.owned, [OFFER[0]])

    def test_hidden_then_timeout(self):
        """隐藏着的时候思考时间到了，游戏替玩家选了：那行字没了、三选一也没回来，照常按关掉处理，记下最后看到的绿框（和没隐藏
        时时间到了一样）。游戏实际拿的是最左边那个时，之后打开面板按面板改正。"""
        self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        for _ in range(3):
            self.feed(hidden=True)
        self.feed(None)
        self.assertEqual(self.kinds(), ["advice", "picked"])
        self.assertEqual(self.s.settings.owned, [OFFER[GREEN]])
        ev = self.s.on_panel(panel([OFFER[0]]))
        self.assertEqual(ev.fixes, [(OFFER[GREEN], OFFER[0])])
        self.assertEqual(self.s.settings.owned, [OFFER[0]])

    def test_text_not_recognized(self):
        """那一帧没认出那行字（被挡住、认错）：照常按关掉处理，先记下绿框；三选一回来时同一组候选又出现，撤销刚才那条、重新
        给推荐，确定后再记一次：结果仍然对。"""
        self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        self.feed(None)
        self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        self.feed(None)
        self.assertEqual(self.kinds(), ["advice", "picked", "pick_reverted", "advice", "picked"])
        self.assertEqual(self.s.settings.owned, [OFFER[GREEN]])
        self.assertEqual(self.hidden_records(), [])

    def test_hidden_twice(self):
        """隐藏、显示、再隐藏、再显示：每次都接着这一次，确定后只记一次；识别记录每开始隐藏一次记一条。"""
        self.feed(select(OFFER, selected=GREEN))
        for _ in range(2):
            self.feed(None)
            self.feed(hidden=True)
            self.feed(hidden=True)
            self.feed(select(OFFER, selected=GREEN))
        self.feed(None)
        self.feed(None)
        self.assertEqual(self.kinds(), ["advice", "picked"])
        self.assertEqual(len(self.hidden_records()), 2)

    def test_not_showing_no_effect(self):
        """三选一没显示时（已经关掉了）认出那行字不起作用：不记识别记录，状态不变。"""
        s = self.s
        self.assertFalse(s.select.closing)
        self.assertIsNone(s.on_select_hidden())
        self.assertFalse(s.select.closing)
        self.assertEqual(self.hidden_records(), [])
        self.feed(select(OFFER, selected=GREEN))
        self.assertFalse(s.select.closing)  # 显示着、这一帧看得到：不是正要按关掉处理


# ------------------------------------------------------------------ C. 识别流程


class HiddenFlowTest(_FakeReads):
    """C：FrameProcessor 什么时候看那行字、认出以后怎么交给 Session（识别函数换成假的，见 tests/test_pipeline.py 的
    _FakeReads；看那行字记成 hidden）。"""

    def setUp(self):
        super().setUp()
        advise = mock.patch(ADVISE, fake_advise)
        advise.start()
        self.addCleanup(advise.stop)
        self.hidden = False  # 画面上有没有那行字
        self.hidden_error: Exception | None = None

        def fake_hidden(img):
            self.log.append("hidden")
            if self.hidden_error is not None:
                raise self.hidden_error
            return self.hidden

        patcher = mock.patch.object(pipeline, "hidden_ui_visible", side_effect=fake_hidden)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session.skip_panel()
        self.session.game.seen_hero = "凛"
        self.settle(0.0)
        self.settle(3.0)  # 对局画面：第 5 轮，进度条第 9 格
        self.green = offer(OFFER)
        self.green.selected = GREEN

    @staticmethod
    def kinds(events) -> list:
        return [e.kind for e in events if e.kind in SELECT_KINDS]

    def open(self, t: float = 4.0) -> None:
        self.seen = self.green
        events, log = self.frame(t)
        self.assertEqual(self.kinds(events).count("advice"), 1)
        self.assertNotIn("hidden", log)  # 显示着不看

    def test_checked_only_when_closing(self):
        """平时（对局画面）不看；三选一显示着不看；消失后第 1 帧不看（照常算看不到一帧），第 2 帧（正要按关掉处理）看一次，
        没有那行字就照常关掉、记下绿框；关掉以后不再看。"""
        for t in (3.3, 3.6):
            self.assertNotIn("hidden", self.frame(t)[1])
        self.open()
        self.seen = None
        events, log = self.frame(4.3)
        self.assertNotIn("hidden", log)
        self.assertEqual(self.kinds(events), [])
        events, log = self.frame(4.6)
        self.assertEqual(log.count("hidden"), 1)
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])
        self.assertFalse(self.proc.select_hidden)
        for t in (4.9, 5.2, 8.0):
            self.assertNotIn("hidden", self.frame(t)[1])

    def test_hidden_keeps_select_open(self):
        """认出那行字：交给 Session 当三选一还开着，隐藏期间每帧看一次（约 10 毫秒），不记下、不发事件；识别流程标出这一帧是隐藏
        着（悬浮窗手动模式据此接着看）。点显示、同一组候选回来：不撤销、不重发推荐；确定后只记一次。"""
        self.open()
        self.seen = None
        self.hidden = True
        self.frame(4.3)
        later = []
        for i in range(10):
            events, log = self.frame(4.6 + 0.5 * i)
            later += events
            self.assertEqual(log.count("hidden"), 1)
            self.assertTrue(self.proc.select_hidden)
            self.assertFalse(self.proc.chip_select_visible)
        self.assertFalse({"picked", "missed_pick", "idle", "pick_reverted"} & set(self.kinds(later)))
        self.assertEqual(self.session.settings.owned, [])
        self.hidden, self.seen = False, self.green  # 点了显示
        events, log = self.frame(10.0)
        self.assertNotIn("hidden", log)
        self.assertFalse(self.proc.select_hidden)
        self.assertEqual([k for k in self.kinds(events) if k in ("advice", "pick_reverted")], [])
        self.seen = None
        events = self.frame(10.3)[0] + self.frame(10.6)[0]
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])
        self.assertEqual(self.session.settings.owned, [OFFER[GREEN]])

    def test_text_gone_records_green(self):
        """隐藏着时间到了、游戏替玩家选了：那行字没了、三选一没回来，下一帧照常按关掉处理，记下绿框。"""
        self.open()
        self.seen = None
        self.hidden = True
        for t in (4.3, 4.6, 5.1):
            self.assertEqual(self.kinds(self.frame(t)[0]), [])
        self.hidden = False
        events, log = self.frame(5.6)
        self.assertEqual(log.count("hidden"), 1)
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])

    def test_text_not_recognized(self):
        """那一帧没认出那行字：照常先记后撤，三选一回来时撤销刚才那条、重新给推荐，确定后再记一次。"""
        self.open()
        self.seen = None
        self.frame(4.3)
        self.assertIn("picked", self.kinds(self.frame(4.6)[0]))
        self.seen = self.green
        events = self.frame(5.0)[0]
        self.assertEqual([k for k in self.kinds(events) if k in ("advice", "pick_reverted")], ["pick_reverted", "advice"])
        self.seen = None
        events = self.frame(5.3)[0] + self.frame(5.6)[0]
        self.assertIn("picked", self.kinds(events))
        self.assertEqual(self.session.settings.owned, [OFFER[GREEN]])

    def test_check_error_falls_back(self):
        """看那行字出错：这一项作废、照样报出错误，这一帧照常按关掉处理（同没认出字），不让三选一一直挂着。"""
        self.open()
        self.seen = None
        self.frame(4.3)
        self.hidden_error = RuntimeError("认那行字出错")
        events, log = self.frame(4.6)
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])
        self.assertEqual([name for name, _, _ in self.proc.errors], ["select_hidden"])
        self.assertFalse(self.proc.select_hidden)

    def test_hotkey_right_after_hiding(self):
        """按热键要求的那一帧正好是三选一刚被隐藏的第 1 帧（还不是正要按关掉处理）：照样看一眼，认出了就当三选一还开着；
        之后每帧照样看，不记下、不发事件。点显示、同一组候选回来：接着这一次（不撤销、不重发推荐）；确定后只记一次。"""
        self.open()
        self.seen = None
        self.hidden = True
        events, log = self.frame(4.3, force=True)
        self.assertEqual(log.count("hidden"), 1)
        self.assertTrue(self.proc.select_hidden)
        self.assertEqual(self.kinds(events), [])
        for t in (4.6, 5.1, 5.6):
            events, log = self.frame(t)
            self.assertEqual(log.count("hidden"), 1)
            self.assertTrue(self.proc.select_hidden)
            self.assertEqual(self.kinds(events), [])
        self.hidden, self.seen = False, self.green  # 点了显示
        events = self.frame(6.0)[0]
        self.assertEqual([k for k in self.kinds(events) if k in ("advice", "pick_reverted")], [])
        self.seen = None
        events = self.frame(6.3)[0] + self.frame(6.6)[0]
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])
        self.assertEqual(self.session.settings.owned, [OFFER[GREEN]])

    def test_hotkey_finds_hidden_select_not_seen_before(self):
        """手动模式下三选一出来以后没按过热键就被隐藏了：Session 不知道这一次三选一（不是正要按关掉处理）。按热键要求的那一帧
        也看一眼那行字，认出了就标出 select_hidden（悬浮窗据此跟随），之后每帧照样看一次；Session 那边不起作用、不发事件。
        点显示、三选一回来：照常认出、给推荐，不再单独看那行字；确定后照常记下。"""
        self.proc.set_manual(True)
        self.seen = None
        self.hidden = True
        events, log = self.frame(4.0, force=True)
        self.assertEqual(log.count("hidden"), 1)
        self.assertTrue(self.proc.select_hidden)
        self.assertFalse(self.session.select.closing)
        self.assertEqual(self.kinds(events), [])
        for t in (4.5, 5.0):
            events, log = self.frame(t)
            self.assertEqual(log.count("hidden"), 1)
            self.assertTrue(self.proc.select_hidden)
            self.assertEqual(self.kinds(events), [])
        self.assertEqual([r for r in self.session.recent_events(600) if r["kind"] == "read:select_hidden"], [])
        self.hidden, self.seen = False, self.green  # 点了显示
        events, log = self.frame(5.5)
        self.assertNotIn("hidden", log)
        self.assertFalse(self.proc.select_hidden)
        self.assertEqual(self.kinds(events).count("advice"), 1)
        self.seen = None
        events = self.frame(6.0)[0] + self.frame(6.5)[0]
        self.assertEqual([e.chip for e in events if e.kind == "picked"], [OFFER[GREEN]])
        self.assertEqual(self.session.settings.owned, [OFFER[GREEN]])

    def test_hotkey_hidden_text_gone(self):
        """按热键认出了 Session 不知道的那一次被隐藏着，之后那行字没了、三选一也没回来（时间到了，游戏替玩家选了）：那一帧看
        一眼就不再看，什么都不记（这一次的候选没见过）。按热键的那一帧没有那行字：看一眼，不标，之后不再看。"""
        self.seen = None
        self.hidden = True
        self.frame(4.0, force=True)
        self.assertTrue(self.proc.select_hidden)
        self.hidden = False
        events, log = self.frame(4.5)
        self.assertEqual(log.count("hidden"), 1)
        self.assertFalse(self.proc.select_hidden)
        for t in (5.0, 5.5):
            self.assertNotIn("hidden", self.frame(t)[1])
        events, log = self.frame(6.0, force=True)
        self.assertEqual(log.count("hidden"), 1)
        self.assertFalse(self.proc.select_hidden)
        self.assertNotIn("hidden", self.frame(6.5)[1])
        self.assertEqual(self.session.settings.owned, [])

    def test_hotkey_check_error_not_repeated(self):
        """按热键的那一帧看那行字出错：这一项报错、不标；之后的帧不再接着看（不会每帧都出同一个错）。"""
        self.seen = None
        self.hidden = True
        self.hidden_error = RuntimeError("认那行字出错")
        self.frame(4.0, force=True)
        self.assertEqual([name for name, _, _ in self.proc.errors], ["select_hidden"])
        self.assertFalse(self.proc.select_hidden)
        self.assertNotIn("hidden", self.frame(4.5)[1])
        # 认出过、之后看那行字出错：同样不再接着看
        self.hidden_error = None
        self.frame(5.0, force=True)
        self.assertTrue(self.proc.select_hidden)
        self.hidden_error = RuntimeError("认那行字出错")
        self.frame(5.5)
        self.assertEqual([name for name, _, _ in self.proc.errors], ["select_hidden"])
        self.assertNotIn("hidden", self.frame(6.0)[1])


# ------------------------------------------------------------------ D. 悬浮窗的识别线程


def scripted(frames):
    """假的识别流程：第 i 帧的（三选一显示着没有, 这一帧是不是隐藏着）照 frames 走（走完一直用最后一项），frames 里的
    隐藏着可以是任何对象（只认真的 True）。processed 记一共识别了几帧。"""
    processed = collections.Counter()

    class Fake:
        chip_select_visible = False

        def __init__(self, *args):
            self.errors, self.select_failed, self.select_hidden = [], False, False

        def process(self, img, now, force=False):
            visible, hidden = frames[min(processed["n"], len(frames) - 1)]
            processed["n"] += 1
            self.chip_select_visible, self.select_hidden = visible, hidden
            return []

    return Fake, processed


class HiddenFollowTest(unittest.TestCase):
    """D：手动模式（只在按热键时识别）按热键认出三选一以后，默认跟随到界面关掉；隐藏着时也接着跟随。"""

    def setUp(self):
        qtenv.app()

    def run_watcher(self, frames, waits: int):
        fake, processed = scripted(frames)
        with mock.patch("advisor.vision.pipeline.FrameProcessor", fake):
            watcher = PaceProbe(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=True, source=Source(),
                                waits=waits)
            watcher.trigger()
            watcher.start()
            self.assertTrue(watcher.wait(10000))
        return watcher.sleeps, processed["n"]

    def test_follow_while_hidden(self):
        """按热键：三选一显示着（按三选一期间的节奏），之后隐藏了 8 帧（比跟随的帧数多）：一直接着看，取帧按平时的间隔；
        隐藏结束、三选一也没回来，照常再看 FOLLOW_FRAMES 帧就停，等下一次按热键。"""
        hidden = 8
        frames = [(True, False)] + [(False, True)] * hidden + [(False, False)]
        sleeps, processed = self.run_watcher(frames, waits=1 + hidden + FOLLOW_FRAMES + 1)
        self.assertEqual(sleeps, [ACTIVE_MS] + [IDLE_MS] * (hidden + FOLLOW_FRAMES) + [None])
        self.assertEqual(processed, 1 + hidden + FOLLOW_FRAMES)

    def test_only_true_counts(self):
        """隐藏着的标记不是真的 True（测试里的 MagicMock 等）：不算，照常只再看 FOLLOW_FRAMES 帧。"""
        frames = [(True, False)] + [(False, mock.MagicMock())] * 8
        sleeps, processed = self.run_watcher(frames, waits=FOLLOW_FRAMES + 3)
        self.assertEqual(sleeps, [ACTIVE_MS] + [IDLE_MS] * FOLLOW_FRAMES + [None, None])
        self.assertEqual(processed, 1 + FOLLOW_FRAMES)

    def test_hotkey_while_hidden(self):
        """按热键要求的那一帧三选一被隐藏着（正跟着看的那一次被隐藏，或没按过热键就隐藏了的那一次，识别流程都标出
        select_hidden）：不报「当前画面不是筹码三选一界面」（推荐照旧留着），照常告诉界面这一帧识别完了；接着看（按平时的
        间隔），点显示、三选一回来时按三选一期间的节奏。按热键的那一帧不是隐藏着的照旧报一句。"""
        for frames, flash, sleeps in (
                ([(False, True)] * 3 + [(True, False)], [], [IDLE_MS] * 3 + [ACTIVE_MS]),
                ([(False, False)], ["已截图识别：当前画面不是筹码三选一界面"], [IDLE_MS, None]),
                ([(False, mock.MagicMock())], ["已截图识别：当前画面不是筹码三选一界面"], [IDLE_MS, None])):
            with self.subTest(frames=frames[0]):
                fake, _ = scripted(frames)
                status, done = [], []
                with mock.patch("advisor.vision.pipeline.FrameProcessor", fake):
                    watcher = PaceProbe(DB, Session(DB, Settings(hero="凛")), threading.Lock(), manual=True,
                                        source=Source(), waits=len(sleeps))
                    watcher.flash_status.connect(status.append, type=Qt.DirectConnection)
                    watcher.capture_done.connect(lambda: done.append(1), type=Qt.DirectConnection)
                    watcher.trigger()
                    watcher.start()
                    self.assertTrue(watcher.wait(10000))
                self.assertEqual((status, done, watcher.sleeps), (flash, [1], sleeps))


if __name__ == "__main__":
    unittest.main()
