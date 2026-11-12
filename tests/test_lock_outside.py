"""识别线程算推荐时不持着共用锁、识别流程每帧只为读提示进一次锁（WatchHints）、界面线程调用 Session 产生的事件当场交给
悬浮窗（事件只走一条通道），以及同一次三选一里算过的推荐照搬（AdviceMemo）。

- 识别流程经 FrameProcessor 调 Session.on_frame 时，给推荐的那一段放开锁去算（session.compute_outside）。慢的推荐引擎
  （SlowAdvise）让识别线程停在算推荐里，等测试在界面线程（本线程）上做完要做的事再放行：界面线程拿锁不用等；这期间切了方案、
  改正了候选、设了地图、选了角色、撤销了新一局，识别线程算出来的旧结果不装进去，最后显示的是按新状态算的。放行有上限
  （改坏时测试照样结束，不会卡死）。
- 不管在锁里算还是锁外算，事件的种类、先后、序号、内容，识别记录和最后的状态逐项相同；同样的输入照搬算过的推荐，结果也相同。
- 识别函数换成假的（同 tests/test_vision_robust 的 FlowBase：不做文字识别、不截屏）；悬浮窗离屏，推荐日志是假对象，
  方案文件放在测试沙箱里。
"""
from __future__ import annotations

import contextlib
import copy
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog
from advisor.session import AdviceMemo, Event, Session, Settings, WatchHints, _event_record, selection, split_extra
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.state import take_view
from advisor.ui.services import Services
from advisor.vision import pipeline
from advisor.vision.hud import Hud, Pregame, Progress
from tests.support import qtenv, sandbox
from tests.support.fakes import FakeLogbook as _FakeLogbook

APP = qtenv.app()
DB = load_db()
HERO = "芬妮"
OFFER_FENNI = ["医疗箱-完备治疗", "紫色飞星", "大背包"]  # 芬妮按方案「治愈坦克」首推医疗箱-完备治疗，默认推荐不是它
HEAL_BEST = "医疗箱-完备治疗"
OFFER_B = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]
OFFER_C = ["标靶", "标记喷罐", "鹰眼瞄具"]
OFFER_D = ["书签", "标靶", "大背包"]  # 最左格是 0、1 星的固定稀有度（蓝）：0 星时星数拿不准、两种都算
FIRST_ROW, SECOND_ROW = (60, 37, 3), (40, 45, 15)  # 两次接连的三选一：概率行换成另一行
# 识别线程停在算推荐里最多等这么久（界面线程应当马上拿到锁，远用不到）。切方案那一条按它的一半判断界面线程有没有被拖住：
# 那里界面线程自己还要在锁里算两次推荐（平时约 0.3 秒，和十几个测试进程一起跑时到过近 3 秒），留足余量；被拖住时要等满这么久
LIMIT = 10.0
LOW = "第 1 个候选筹码匹配度低"


def frame(names, probabilities=FIRST_ROW, rerolls=2, selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=probabilities,
                           rarity=[], team=False, problems=list(problems), selected=selected)


def kinds(events) -> list[str]:
    return [e.kind for e in events]


def advices(events) -> list:
    """其中的推荐（advice）事件。进入对局后第一次三选一另有「请打开查看筹码面板」、难度的 status，不在这里比。"""
    return [e for e in events if e.kind == "advice"]


def records(events) -> list[dict]:
    return [_event_record(e) for e in events]


class FakeLogbook(_FakeLogbook):
    """推荐日志的假对象（tests/support/fakes），每次推荐另记下首推：offers 里是（编号, 候选, 首推）。"""

    def record_offer(self, state, advice, offer_id, client=None, map_source=None, source_by=None,
                     difficulty_source=None, owned_sources=None, owned_panel=None):
        self.offers.append((offer_id, tuple(e.chip.name for e in advice.evals), advice.best))


def label_changing_map(test: unittest.TestCase) -> tuple[str, int]:
    """找一张地图和进度上限：读到了这个进度上限、地图从待识别改成它时，难度胶囊的写法会变（set_map 往待取队列放一个
    status）。资料里找不到时跳过这条测试。"""
    for name in DB.maps:
        info = DB.map_info.get(name)
        if info is None:
            continue
        for cap in sorted({c for c in info.progress_caps.values() if c}):
            probe = Session(DB, Settings(hero=HERO))
            probe.game.progress_cap = cap
            before = probe.difficulty_info.label
            probe.set_map(name)
            if probe.difficulty_info.label != before and probe.take_events():
                return name, cap
    test.skipTest("资料里没有能让难度胶囊变的地图和进度上限")


class SlowAdvise:
    """换掉记账里调用的推荐引擎（advisor.session.selection.advise）：照常算（真的引擎），在 thread 那个线程（当识别线程）里
    算的时候先停住：entered 置上，等 go 放行（最多 LIMIT 秒）。calls 记每次调用的（线程名, 当时锁是不是被拿着）。"""

    def __init__(self, real, lock):
        self.real, self.lock = real, lock
        self.entered = threading.Event()
        self.go = threading.Event()
        self.thread: threading.Thread | None = None
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, *args, **kwargs):
        me = threading.current_thread()
        self.calls.append((me.name, self.lock.locked()))
        if me is self.thread and not self.go.is_set():
            self.entered.set()
            self.go.wait(LIMIT)
        return self.real(*args, **kwargs)


class Flow(unittest.TestCase):
    """FrameProcessor 接真的 OverlaySession（芬妮、第 6 轮、1 星），识别函数换成假的：三选一读数取 self.seen，轮次取
    self.round，其余读不到；时钟由测试拨（self.clock）。"""

    plan = False  # 建一套「治愈坦克」方案（当前角色还不用它；几次新建的 Session 共用这一份方案存储）

    def setUp(self):
        self.seen, self.round = None, None
        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "reread_selected", return_value=None),
            mock.patch.object(pipeline, "name_strip", return_value=None),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full=False, **kw: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", side_effect=lambda img: Progress()),
            mock.patch.object(pipeline, "is_select_screen", side_effect=lambda img, db, info=None: False),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero=HERO)),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, who, **kw: None),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", side_effect=lambda img, db, **kw: None),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.lock = threading.Lock()
        self.clock = [100.0]
        self.store = None
        if self.plan:
            self.store = SchemeStore(sandbox.temp_dir(self, "lock-outside-") / "schemes.json", catalog=get_catalog(DB))
            self.heal = self.store.add(Scheme(hero=HERO, name="治愈坦克", tiers={HEAL_BEST: "core"}))
        self.fresh_flow()
        self.img = np.full((1080, 1920, 3), 90, np.uint8)
        self.slow = SlowAdvise(selection.advise, self.lock)
        self.got: list | None = None

    def fresh_flow(self) -> None:
        """换一个新的 Session 和识别流程，时钟从头拨。"""
        self.clock[0] = 100.0
        self.session = self.make_session()
        self.proc = pipeline.FrameProcessor(DB, self.session, self.lock)

    def make_session(self) -> OverlaySession:
        session = OverlaySession(DB, UiSettings(hero=HERO))
        session._clock = lambda: self.clock[0]
        session.game.seen_hero = HERO
        s = session.settings
        s.round, s.star, s.star_read = 6, 1, True
        if self.store is not None:
            session.scheme_for = self.store.current
        return session

    def step(self, seen=None, dt=0.15) -> list:
        self.seen = seen
        self.clock[0] += dt
        return self.proc.process(self.img, self.clock[0])

    # ------------------------------------------------------------ 识别线程停在算推荐里

    def start_frame(self, seen) -> threading.Thread:
        """另起一个线程（当识别线程）处理一帧，停在算推荐里。结果放在 self.got。"""
        self.seen = seen
        self.clock[0] += 0.15
        now = self.clock[0]

        def run():
            self.got = self.proc.process(self.img, now)

        t = threading.Thread(target=run, name="识别线程", daemon=True)
        self.slow.thread = t
        self.slow.entered.clear()
        self.slow.go.clear()
        self.addCleanup(self.slow.go.set)
        t.start()
        self.assertTrue(self.slow.entered.wait(10), "识别线程没有走到算推荐")
        return t

    def finish(self, t: threading.Thread) -> list:
        self.slow.go.set()
        t.join(30)
        self.assertFalse(t.is_alive())
        return self.got

    def on_ui(self, fn):
        """在界面线程（本线程）上持锁做 fn，返回（fn 的结果, 等锁的秒数）。识别线程在锁外算时应当马上拿到锁；拿不到（改坏了：
        算推荐时还持着锁）时放行识别线程，测试失败。"""
        t0 = time.perf_counter()
        got = self.lock.acquire(timeout=1.0)
        waited = time.perf_counter() - t0
        if not got:
            self.slow.go.set()
            self.fail(f"界面线程等了 {waited:.2f} 秒还拿不到锁：识别线程算推荐时还持着锁")
        try:
            return fn(), waited
        finally:
            self.lock.release()


def script(test: Flow) -> tuple:
    """一串画面：新的三选一、同一画面、带识别提示的另一组候选（下一帧读全以后重发）、星数变了、界面关掉（漏记、选中）、
    0 星时星数拿不准（两种都算）、紧接着的另一次三选一、本局已结束后出现的三选一（开新的一局）。返回（每一帧的事件、
    识别记录、最后的状态）。"""
    s = test.session
    out = []
    for seen in (frame(OFFER_FENNI), frame(OFFER_FENNI), frame(OFFER_B, problems=[LOW]), frame(OFFER_B)):
        out.append(records(test.step(seen)))
    s.settings.star = 2
    out.append(records(test.step(frame(OFFER_B))))
    out += [records(test.step(None)), records(test.step(None))]
    out += [records(test.step(frame(OFFER_C, selected=1))), records(test.step(None)), records(test.step(None))]
    s.settings.star = 0
    out += [records(test.step(frame(OFFER_D, rerolls=None))), records(test.step(frame(OFFER_D, rerolls=None)))]
    out.append(records(test.step(frame(OFFER_B, probabilities=SECOND_ROW))))
    s.game.game_over = True
    out.append(records(test.step(frame(OFFER_C, probabilities=SECOND_ROW))))
    out += [records(test.step(None)), records(test.step(None))]
    log = [{k: v for k, v in r.items() if k != "time"} for r in s.recent_events(10 ** 6)]
    sel, g = s.select, s.game
    # 快照里没有、推荐要用的几项：刷新次数的推断（每升一星刷新 +1）、去重键、上一次给推荐时的识别提示、推荐用的方案
    inner = (g.per_star_rerolls, g._rerolls_seen, sel._last_key, sel._shown, sel._plan_used, sel._star_check)
    return out, log, s.snapshot(), inner


class OutsideLockTest(Flow):
    def test_lock_free_while_computing(self):
        """识别线程算推荐时锁是放开的：界面线程马上拿到锁、取得到一份完整的快照；放行后照常给出推荐。
        界面线程在锁里自己重算时照旧持着锁算。"""
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            self.assertFalse(self.lock.locked())
            view, waited = self.on_ui(lambda: take_view(self.session))
            events = self.finish(t)
            with self.lock:
                self.session.settings.round = 7  # 局面变了：界面线程按同一个画面重算（Session.recompute）
                again = self.session.recompute()
        self.assertLess(waited, 0.5)
        self.assertEqual(view.round, 6)
        self.assertEqual(len(advices(events)), 1)
        self.assertEqual(again.kind, "advice")
        self.assertEqual(again.state.round, 7)
        self.assertEqual(set(self.slow.calls), {("识别线程", False), ("MainThread", True)})

    def test_switch_interval_short_while_computing(self):
        """识别线程把一帧交给 Session 的这段时间（算推荐在锁外）换线程的间隔调短：界面线程拿回解释器锁不用等一个计时周期；
        这一帧做完改回原来的。"""
        during = []
        real = self.slow.real

        def probe(*args, **kwargs):
            if threading.current_thread() is self.slow.thread:
                during.append(sys.getswitchinterval())
            return real(*args, **kwargs)

        self.slow.real = probe
        # 从默认的间隔量起：同一个进程里前面的测试万一没把间隔改回来（例如失败的测试留下的识别线程还在 _ui_first 里），
        # 这里也测得出调短了没有
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(0.005)
        before = sys.getswitchinterval()
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            on_ui = sys.getswitchinterval()
            self.finish(t)
        self.assertLessEqual(on_ui, pipeline.UI_FIRST_SWITCH_S)
        self.assertTrue(during)
        self.assertLessEqual(max(during), pipeline.UI_FIRST_SWITCH_S)
        self.assertEqual(sys.getswitchinterval(), before)

    def test_same_result_as_inline(self):
        """同一串画面，算推荐放开锁（识别流程的做法）和不放开（在锁里算）：每一帧的事件（种类、先后、序号、内容）、
        识别记录、最后的状态都相同。"""
        def run(outside: bool):
            self.fresh_flow()
            ctx = contextlib.nullcontext() if outside else mock.patch.object(
                pipeline, "compute_outside", lambda lock: contextlib.nullcontext())
            with ctx:
                return script(self)

        got = run(True)
        self.assertTrue(any(r["kind"] == "advice" for frame_records in got[0] for r in frame_records))
        self.assertEqual(got, run(False))


class SchemeSameResultTest(OutsideLockTest):
    """按方案计算（另算默认推荐的首推）时同样逐项相同。"""

    plan = True

    def setUp(self):
        super().setUp()
        self.store.set_current(HERO, self.heal.id)


class UiFirstTest(unittest.TestCase):
    """换线程的间隔（pipeline._ui_first）按进出次数计：第一个进去的调短，最后一个出来的才改回原值。几个线程交叠着进出、
    同一个线程嵌套、出错出来都一样。"""

    def setUp(self):
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(0.005)
        self.start = sys.getswitchinterval()  # 读回来的写法（按微秒存，和写进去的小数不一定逐位相同）

    def test_overlapping_threads(self):
        """A 进去、B 进去、A 出来（B 还在里面：照样是调短的）、B 出来（改回原来的 5 毫秒）。"""
        a_in, b_in, a_out = threading.Event(), threading.Event(), threading.Event()
        seen = {}

        def a():
            with pipeline._ui_first():
                a_in.set()
                b_in.wait(5)
            seen["a_out"] = sys.getswitchinterval()
            a_out.set()

        def b():
            a_in.wait(5)
            with pipeline._ui_first():
                b_in.set()
                a_out.wait(5)

        threads = [threading.Thread(target=a), threading.Thread(target=b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertTrue(a_out.is_set())
        self.assertLessEqual(seen["a_out"], pipeline.UI_FIRST_SWITCH_S)
        self.assertEqual(sys.getswitchinterval(), self.start)

    def test_nested_and_error(self):
        with self.assertRaises(ValueError):
            with pipeline._ui_first():
                with pipeline._ui_first():
                    pass
                self.assertLessEqual(sys.getswitchinterval(), pipeline.UI_FIRST_SWITCH_S)
                raise ValueError
        self.assertEqual(sys.getswitchinterval(), self.start)
        sys.setswitchinterval(0.0002)  # 原来就比调短的还短：不动，出来照样是它
        shorter = sys.getswitchinterval()
        with pipeline._ui_first():
            self.assertEqual(sys.getswitchinterval(), shorter)
        self.assertEqual(sys.getswitchinterval(), shorter)

    def test_exact_microseconds(self):
        """原来的间隔读回来原样写回会少 1 微秒的（解释器按微秒存、写的时候截掉小数）：改回以后照样是原来的。"""

        def drifts(us: int) -> bool:
            sys.setswitchinterval(us / 1e6)
            read = sys.getswitchinterval()
            sys.setswitchinterval(read)
            return round(read * 1e6) == us and round(sys.getswitchinterval() * 1e6) != us

        us = next(n for n in range(501, 20000) if drifts(n))
        sys.setswitchinterval(us / 1e6)
        before = sys.getswitchinterval()
        for _ in range(3):
            with pipeline._ui_first():
                pass
        self.assertEqual(round(sys.getswitchinterval() * 1e6), round(before * 1e6))


class InterleaveTest(Flow):
    """识别线程算推荐的期间，界面线程改了会影响推荐的状态：旧的不装进去，最后是按新状态算的。"""

    plan = True
    advice_events = staticmethod(advices)

    def test_scheme_switch_while_computing(self):
        """算的期间切了方案（界面线程当场按新方案重算、给出推荐）：识别线程算出来的旧推荐不装进去；之后同一画面不再发事件。"""
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))

            def switch():
                self.store.set_current(HERO, self.heal.id)
                return self.session.recompute()

            ui_ev, waited = self.on_ui(switch)
            events = self.finish(t)
        self.assertLess(waited, 0.5)
        self.assertEqual(ui_ev.advice.best, HEAL_BEST)
        self.assertEqual(self.advice_events(events), [])
        self.assertEqual(kinds(self.step(frame(OFFER_FENNI))), [])  # 局面没变（已经按新方案给过）：不再发事件

    def test_fix_while_computing(self):
        """算的期间改正了一个候选（界面线程按改正后的名字重算）：旧的不装进去，下一帧也不再按改正前的名字给推荐。"""
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            ui_ev, _ = self.on_ui(lambda: self.session.fix_candidate(2, "书签"))
            events = self.finish(t)
        self.assertEqual([e.chip.name for e in ui_ev.advice.evals], ["医疗箱-完备治疗", "紫色飞星", "书签"])
        self.assertEqual(self.advice_events(events), [])
        self.assertEqual(kinds(self.step(frame(OFFER_FENNI))), [])  # 改正照样套用：局面没变

    def test_map_set_while_computing(self):
        """算的期间手动指定了地图（没有当场重算）：旧的不装进去，下一帧按新地图给出推荐。"""
        target = list(DB.maps)[1]
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            self.on_ui(lambda: self.session.set_map(target))
            events = self.finish(t)
        self.assertEqual(self.advice_events(events), [])
        later = self.advice_events(self.step(frame(OFFER_FENNI)))
        self.assertEqual([e.state.map for e in later], [target])

    def test_hero_set_while_computing(self):
        """三选一刚出现（悬浮窗还没收到推荐，选角色时不重算）时选了别的角色：旧的不装进去，下一帧按新角色给出推荐。"""
        other = next(h for h in DB.heroes if h != HERO)
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            self.on_ui(lambda: self.session.set_hero(other))
            events = self.finish(t)
        self.assertEqual(self.advice_events(events), [])
        later = self.advice_events(self.step(frame(OFFER_FENNI)))
        self.assertEqual([e.state.hero for e in later], [other])

    def test_owned_changed_while_computing(self):
        """算的期间补录了一个筹码：旧的不装进去，下一帧按新的已有筹码给出推荐。"""
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            added, _ = self.on_ui(lambda: self.session.add_chip("书签"))
            events = self.finish(t)
        self.assertTrue(added)
        self.assertEqual(self.advice_events(events), [])
        later = self.advice_events(self.step(frame(OFFER_FENNI)))
        self.assertEqual([e.state.owned for e in later], [("书签",)])

    def test_undo_while_computing(self):
        """算的期间撤销了刚判定的新一局：旧的不装进去，下一帧按撤销后的状态重发。"""
        s = self.session
        s.settings.owned = ["书签"]
        with self.lock:
            ev = s._auto_new_game("star", 0, "星数由 1 星减少为 0 星")
        self.assertEqual(ev.kind, "newgame")
        s.game.seen_hero = HERO
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            ok, _ = self.on_ui(s.undo_new_game)
            events = self.finish(t)
        self.assertTrue(ok)
        self.assertEqual(self.advice_events(events), [])
        later = self.advice_events(self.step(frame(OFFER_FENNI)))
        self.assertEqual([e.state.owned for e in later], [("书签",)])

    def test_other_edits_while_computing(self):
        """算的期间界面上别的会影响推荐的改动（交回自动识别角色、移出筹码、手改已有筹码、补录漏掉的那次三选一，都不当场
        重算）：旧的不装进去，下一帧按新状态给出推荐。"""
        other = next(h for h in DB.heroes if h != HERO)
        cases = {  # 改动：（算之前先做的, 算的期间界面线程做的, 下一帧推荐的局面应当是什么样）
            "release_hero": (lambda s: s.set_hero(other), lambda s: s.release_hero(), lambda st: st.hero == HERO),
            "remove_chip": (lambda s: s.set_owned(["书签"]), lambda s: s.remove_chip("书签"), lambda st: st.owned == ()),
            "set_owned": (lambda s: None, lambda s: s.set_owned(["书签"]), lambda st: st.owned == ("书签",)),
            "resolve_missed": (lambda s: setattr(s.game, "missed_offer", list(OFFER_C)),
                               lambda s: s.resolve_missed(OFFER_C[0]), lambda st: st.owned == (OFFER_C[0],)),
        }
        for name, (prepare, edit, expect) in cases.items():
            with self.subTest(edit=name):
                self.fresh_flow()
                s = self.session
                prepare(s)
                with mock.patch.object(selection, "advise", self.slow):
                    t = self.start_frame(frame(OFFER_FENNI))
                    self.on_ui(lambda: edit(s))
                    events = self.finish(t)
                self.assertEqual(self.advice_events(events), [])
                later = self.advice_events(self.step(frame(OFFER_FENNI)))
                self.assertEqual(len(later), 1)
                self.assertTrue(expect(later[0].state), later[0].state)

    def test_restore_chip_while_computing(self):
        """三选一显示着时「×」移出了一个，识别线程按移出后的已有筹码重算的期间又撤销了移出（Session.restore_chip）：算出来的
        不装进去；放回以后的局面和移出之前一样，下一帧不重复给推荐（界面上还是移出之前那一次的推荐）。"""
        s = self.session
        s.set_owned(["书签", "标靶"])
        first = self.advice_events(self.step(frame(OFFER_FENNI)))  # 三选一显示着
        self.assertEqual([e.state.owned for e in first], [("书签", "标靶")])
        self.assertTrue(s.remove_chip("标靶"))
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))  # 按移出后的已有筹码重算，停在算推荐里
            restored, _ = self.on_ui(s.restore_chip)
            events = self.finish(t)
        self.assertEqual(restored, "标靶")
        self.assertEqual(self.advice_events(events), [])
        self.assertEqual(self.advice_events(self.step(frame(OFFER_FENNI))), [])

    def test_unrelated_ui_keeps_result(self):
        """算的期间界面线程只是刷新、记一行操作、存一下界面设置：不影响推荐，算出来的照常装进去。"""
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))

            def ui():
                take_view(self.session)
                self.session.trace("user", action="measure")
                self.session.settings.collapsed_cards = ["recog"]

            self.on_ui(ui)
            events = self.finish(t)
        self.assertEqual(len(self.advice_events(events)), 1)
        self.assertEqual(kinds(self.step(frame(OFFER_FENNI))), [])


class EventOrderTest(Flow):
    def test_ui_takes_only_its_own(self):
        """识别线程这一帧在算推荐之前放进队列的事件（同一组候选又出现时的撤销）留在队列里：界面线程这时调用 Session 以后只取
        它自己这次放进来的（选地图让难度胶囊变了的 status）；识别线程照原来的先后交出它的那些，不重复、不丢（地图变了，
        它算的推荐不要）。"""
        name, cap = label_changing_map(self)
        s = self.session
        s.game.panel_needed = False  # 已有筹码已知：进入对局时不请用户打开面板
        s.game.progress_cap = cap
        self.step(frame(OFFER_FENNI, selected=0))
        closed = self.step(None) + self.step(None)
        self.assertEqual(kinds(closed), ["picked"])
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))  # 同一组候选又出现：先放进队列一个撤销，再去算推荐

            def ui():
                mark = s.events_mark()
                s.set_map(name)
                return s.take_events(after=mark)

            taken, _ = self.on_ui(ui)
            events = self.finish(t)
        self.assertEqual(kinds(taken), ["status"])
        self.assertEqual(kinds(events), ["pick_reverted"])
        self.assertLess(events[0].seq, taken[0].seq)
        later = self.step(frame(OFFER_FENNI))
        self.assertEqual([e.state.map for e in advices(later)], [name])

    def test_queue_order_kept_without_ui(self):
        """没有界面线程插进来时，这一帧在算推荐之前放进队列的事件和推荐照原来的先后交出（撤销在推荐前面）。"""
        self.session.game.panel_needed = False
        self.step(frame(OFFER_FENNI, selected=0))
        self.step(None)
        self.step(None)
        events = self.step(frame(OFFER_FENNI))
        self.assertEqual(kinds(events), ["pick_reverted", "advice"])
        self.assertLess(events[0].seq, events[1].seq)

    def test_split_extra(self):
        """取走的提示类事件：撤销、上一次的结算排在主要事件前面，其余在后面，各自保持先后。"""
        extra = [Event("status", seq=1), Event("picked", seq=2, chip="书签"), Event("map_read", seq=3),
                 Event("pick_reverted", seq=4, chip="标靶"), Event("missed_pick", seq=5)]
        before, after = split_extra(extra)
        self.assertEqual([e.seq for e in before], [2, 4, 5])
        self.assertEqual([e.seq for e in after], [1, 3])


class HintsTest(Flow):
    """WatchHints：每一项和分别进锁读到的一样；识别流程在每个读它的地方用的都和那时现读的相同，一帧最多为它进一次锁。"""

    # WatchHints 的每一项 → 分别进锁读时的写法
    DIRECT = {
        "game_over": lambda s, seen: s.game.game_over,
        "hero_pending": lambda s, seen: s.hero_source == "pending",
        "stars_hero": lambda s, seen: s.stars_hero,
        "seen_hero": lambda s, seen: s.game.seen_hero,
        "has_game_state": lambda s, seen: s.has_game_state,
        "wants_map": lambda s, seen: s.wants_map,
        "wants_loading": lambda s, seen: s.wants_loading,
        "wants_teammates": lambda s, seen: s.wants_teammates,
        "round_jump_pending": lambda s, seen: s.round_jump_pending,
        "round_drop_pending": lambda s, seen: s.round_drop_pending,
        "progress_pending": lambda s, seen: s.progress_pending,
        "panel_needed": lambda s, seen: s.game.panel_needed,
        "own_row": lambda s, seen: getattr(s.game, "own_row", None),
        "player_name": lambda s, seen: getattr(s, "player_name", None),
        "template_wanted": lambda s, seen: s.game.template_wanted,
        "games": lambda s, seen: s.games,
        "holding": lambda s, seen: s.holding,
        "another_select": lambda s, seen: s.starts_another_select(seen) if seen is not None else False,
        "round_known": lambda s, seen: s.settings.round is not None,
        # 手上的轮次（轮次被挡住时只认一小块读到的数和它差得多就不直接交给记账，见 tests/test_pipeline.py 的 OccludedIdleTest）
        "round_now": lambda s, seen: s.settings.round,
        # 三选一正要按关掉处理（这一帧看不看「您当前正处于隐藏UI状态」，见 tests/test_select_hidden.py）
        "select_closing": lambda s, seen: s.select.closing,
        # 三选一刚出现、还没给过推荐（读得不完整时先重读三张卡、读左上角排在后面，见 tests/test_pipeline.py 的
        # FirstRereadTest）；最近一次交来的三选一新读的、有筹码没读出来（识别流程留下这一帧，导出诊断时另存）
        "select_fresh": lambda s, seen: s.select.fresh,
        "select_unread": lambda s, seen: s.select.unread,
        # 这次三选一显示着、给过推荐（完整识别读到的名字突然变了时判断是不是被临时盖住，见 tests/test_pick_watch.py 的
        # CoveredTest）；最近一次交来的读数记下了绿框选中的另一张（识别流程留下记下它用的那一帧）
        "select_told": lambda s, seen: s.select.told,
        "select_noted": lambda s, seen: s.select.noted,
        # 已经当作不在对局里了（连着 quiet_ms 看不到对局读数，见 tests/test_pipeline.py 的 OutOfGameFlowTest）
        "game_quiet": lambda s, seen: s.game_quiet is True,
        # 在等结算数据表（每帧先看一眼像不像，见 tests/test_result_table_hookup.py 的 PipelineTableTest）
        "wants_table": lambda s, seen: s.wants_table,
    }

    def assert_matches(self, session, seen=None):
        hints = WatchHints.of(session, seen)
        self.assertEqual(set(hints.__dataclass_fields__), set(self.DIRECT))
        for name, read in self.DIRECT.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(hints, name), read(session, seen))

    def test_fields_match_direct_reads(self):
        self.assert_matches(Session(DB, Settings(hero="凛")))
        s = self.session
        self.assert_matches(s)
        s.note_own(2, "玩家")
        s.game.template_wanted = HERO
        s.game._progress_pending = 7
        s.on_teammates([{"row": 0, "hero": None, "bot": None}])
        self.assert_matches(s)
        self.step(frame(OFFER_FENNI))
        self.assert_matches(s, frame(OFFER_B, probabilities=SECOND_ROW))  # 紧接着的另一次三选一
        self.assert_matches(s, frame(OFFER_B, rerolls=1))  # 同一行、次数少了：刷新
        self.assert_matches(s, frame(OFFER_B))  # 同一行、三张全换、次数没少：另一次
        self.step(frame([None, "紫色飞星", "大背包"], rerolls=1))  # 刚点了刷新、还没读全：等着
        self.assertTrue(s.holding)
        self.assert_matches(s)
        self.step(None)  # 三选一看不到了一帧：再看不到就按关掉处理
        self.assertTrue(s.select.closing)
        self.assert_matches(s)
        s.game.game_over = True
        s.game.seen_hero = None
        self.assert_matches(s, frame(OFFER_C))

    def test_first_unread_matches(self):
        """三选一刚出现、前两张没读出来（等着，还没给过推荐）：select_fresh、select_unread 都为真，和现读的一样；读全、给了
        推荐以后都为假。"""
        s = self.session
        self.step(frame([None, None, "大背包"]))
        self.assertEqual((s.select.fresh, s.select.unread, s.holding), (True, True, True))
        self.assert_matches(s)
        self.step(frame(OFFER_FENNI), dt=0.6)
        self.assertEqual((s.select.fresh, s.select.unread), (False, False))
        self.assert_matches(s)

    def test_hints_fresh_at_every_read(self):
        """跑一串画面（同 test_same_result_as_inline 的，再加几帧对局画面）：识别流程每次读提示时用的都和当时在锁里现读的
        一样；每一帧专为读提示进锁最多一次。"""
        checks, fetches = [], []
        real_hint = pipeline.FrameProcessor._hint
        real_process = pipeline.FrameProcessor.process

        def checked(proc):
            fetching = proc.hints is None
            hints = real_hint(proc)
            fetches[-1] += fetching
            with proc.lock:
                checks.append(hints == WatchHints.of(proc.session, proc._seen))
            return hints

        def process(proc, img, now, force=False):
            fetches.append(0)
            return real_process(proc, img, now, force)

        with mock.patch.object(pipeline.FrameProcessor, "_hint", checked), \
                mock.patch.object(pipeline.FrameProcessor, "process", process):
            script(self)
            self.session.settings.map = None
            self.round = 6
            for _ in range(4):
                self.step(None, dt=3.0)  # 对局画面：读轮次、认角色、读星数和队友、找模板、读进度条、看面板、找加载画面
        self.assertGreater(len(checks), 20)
        self.assertTrue(all(checks), checks)
        self.assertLessEqual(max(fetches), 1)


class MemoTest(Flow):
    """同一次三选一里同样的输入照搬算过的推荐：调推荐引擎的次数少了，结果和重算的相同。"""

    plan = True

    def setUp(self):
        super().setUp()
        self.count = [0]
        real = selection.advise

        def counting(*args, **kwargs):
            self.count[0] += 1
            return real(*args, **kwargs)

        p = mock.patch.object(selection, "advise", counting)
        p.start()
        self.addCleanup(p.stop)

    def calls(self, fn) -> int:
        before = self.count[0]
        fn()
        return self.count[0] - before

    def test_reemit_reuses_advice(self):
        """同一组候选读全以后去掉过时的提示重发：不再调推荐引擎，内容和上一次的相同（只是识别提示少了）。"""
        first = advices(self.step(frame(OFFER_FENNI, problems=[LOW])))
        self.assertEqual(len(first), 1)
        again = []
        self.assertEqual(self.calls(lambda: again.extend(self.step(frame(OFFER_FENNI)))), 0)
        self.assertEqual(kinds(again), ["advice"])
        a, b = records(first)[0], records(again)[0]
        self.assertEqual(a.pop("problems"), [LOW])
        self.assertNotIn("problems", b)
        a.pop("seq"), b.pop("seq")
        self.assertEqual(a, b)

    def test_default_best_reused_on_switch(self):
        """按默认推荐给过推荐以后换成方案：方案那一份照算，默认推荐的首推照搬（少算一次）；换回默认推荐、再换成方案也一样。"""
        self.assertEqual(self.calls(lambda: self.step(frame(OFFER_FENNI))), 1)
        default_best = self.session.select._memo.last[1][1].best

        def switch(scheme_id):
            with self.lock:
                self.store.set_current(HERO, scheme_id)
                return self.session.recompute()

        got = []
        self.assertEqual(self.calls(lambda: got.append(switch(self.heal.id))), 1)
        self.assertEqual((got[0].advice.best, got[0].advice.default_best), (HEAL_BEST, default_best))
        self.assertEqual(self.calls(lambda: got.append(switch(None))), 1)
        self.assertEqual(got[1].advice.best, default_best)
        self.assertEqual(self.calls(lambda: got.append(switch(self.heal.id))), 1)
        self.assertEqual(got[2].advice.default_best, default_best)

    def test_same_as_without_memo(self):
        """照搬算过的和每次都重算的，结果逐项相同（照搬的少调了推荐引擎）。"""
        with_memo = script(self)
        used = self.count[0]
        self.fresh_flow()
        self.count[0] = 0
        with mock.patch.object(AdviceMemo, "remember", lambda memo, *args: None):
            without = script(self)
        self.assertLess(used, self.count[0])
        self.assertEqual(with_memo, without)

    def test_advice_timed(self):
        """真算推荐的那一次记一笔 advice（耗时统计）；照搬算过的（AdviceMemo 命中）不记。"""
        from advisor import perfstats

        perfstats.take_period()  # 这一段清零
        self.step(frame(OFFER_FENNI, problems=[LOW]))
        first = perfstats.take_period()
        self.assertEqual(first["advice"]["n"], 1)
        again = self.step(frame(OFFER_FENNI))
        self.assertEqual(kinds(again), ["advice"])  # 同一组候选读全后照搬重发
        self.assertNotIn("advice", perfstats.take_period())


class MemoScopeTest(Flow):
    def test_memo_scope(self):
        """界面关掉时清空；深拷贝出来的 Session 是空的（重算一遍结果相同）。"""
        self.step(frame(OFFER_FENNI))
        self.assertIsNotNone(self.session.select._memo.last)
        clone = copy.deepcopy(self.session)
        self.assertIsNone(clone.select._memo.last)
        self.step(None)
        self.step(None)
        self.assertIsNone(self.session.select._memo.last)
        self.assertEqual(self.session.select._memo.defaults, {})


class OverlayTest(Flow):
    """接上离屏悬浮窗：识别线程算推荐的期间界面上的刷新、切方案不用等；最后显示的是按新状态算的。识别线程交出的事件在它
    处理完这一帧以后才交给悬浮窗（相当于排着队的信号）。"""

    plan = True

    def setUp(self):
        super().setUp()
        self.logbook = FakeLogbook()
        self.w = Overlay(DB, self.session, self.lock, services=Services(logbook=self.logbook), store=self.store)
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def deliver(self, events) -> None:
        for ev in events:
            self.w.handle(ev)

    def choose_scheme(self, name: str) -> float:
        """在方案下拉框里点选一项，返回用了多久（秒）。"""
        index = next(i for i, (text, _, _) in enumerate(self.w.scheme.items()) if text == name)
        t0 = time.perf_counter()
        self.w.scheme_box.setCurrentIndex(index)
        self.w.scheme_box.activated.emit(index)
        return time.perf_counter() - t0

    def test_refresh_and_switch_while_computing(self):
        """三选一显示着、局面变了（星数）识别线程正在重算：刷新马上返回；这时切方案，当场按新方案给出推荐；识别线程那一份
        不装进去，悬浮窗上和推荐日志里都只有按新方案的。"""
        self.deliver(self.step(frame(OFFER_FENNI)))
        default_best = self.logbook.offers[-1][2]
        self.assertNotEqual(default_best, HEAL_BEST)
        self.session.settings.star = 2
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            t0 = time.perf_counter()
            self.w.refresh()
            refresh_s = time.perf_counter() - t0
            switch_s = self.choose_scheme("治愈坦克")
            events = self.finish(t)
        self.deliver(events)
        self.assertLess(refresh_s, 1.0)
        self.assertLess(switch_s, LIMIT / 2)
        self.assertEqual([e for e in events if e.kind == "advice"], [])
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        self.assertEqual([best for _, _, best in self.logbook.offers], [default_best, HEAL_BEST])
        self.deliver(self.step(frame(OFFER_FENNI)))  # 下一帧：局面没变，不再发事件
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        self.assertEqual(len(self.logbook.offers), 2)

    def test_same_row_back_to_back_logged_apart(self):
        """两次三选一紧接着出现、概率行一样、三张全换、刷新次数没少：推荐日志记成两次，编号不同，第一次绿框
        选中的记在第一次的编号上；之后这一次里真刷新（次数少了 1）照旧把这一组记为「刷新」、换新编号。"""
        self.deliver(self.step(frame(OFFER_FENNI, selected=0)))
        self.deliver(self.step(frame(OFFER_B)))
        self.deliver(self.step(frame(OFFER_C, rerolls=1)))
        ids = [offer_id for offer_id, _, _ in self.logbook.offers]
        self.assertEqual([names for _, names, _ in self.logbook.offers],
                         [tuple(OFFER_FENNI), tuple(OFFER_B), tuple(OFFER_C)])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(self.logbook.picks, [(ids[0], OFFER_FENNI[0]), (ids[1], FakeLogbook.PICK_REROLL)])
        self.assertEqual(self.session.settings.owned, [OFFER_FENNI[0]])

    def test_fix_while_computing(self):
        """识别线程正在算的时候改正了一个候选：显示改正后的推荐，「手动指定」的那一格不被换回去。"""
        self.deliver(self.step(frame(OFFER_FENNI)))
        self.session.settings.star = 2
        with mock.patch.object(selection, "advise", self.slow):
            t = self.start_frame(frame(OFFER_FENNI))
            self.w.advice.open_fix(2)
            self.w.fix_box.setEditText("书签")
            self.w.fix_box.lineEdit().returnPressed.emit()
            events = self.finish(t)
        self.deliver(events)
        body = "｜".join(self.w.visible_texts(self.w.body))
        self.assertIn("（手动指定）", body)
        self.assertIn("书签", body)
        self.assertEqual(set(self.logbook.offers[-1][1]), {"医疗箱-完备治疗", "紫色飞星", "书签"})


class UiEventsTest(unittest.TestCase):
    """界面线程调用 Session 放进待取队列的事件，调完当场交给悬浮窗（照识别流程的先后），记进最近事件；识别线程下一次调用
    不会再取到（不重复、不丢）。"""

    def setUp(self):
        self.logbook = FakeLogbook()
        self.session = OverlaySession(DB, UiSettings(hero=HERO))
        self.session._clock = lambda: 100.0
        self.w = Overlay(DB, self.session, threading.Lock(), services=Services(logbook=self.logbook))
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)
        self.handled: list = []
        real = self.w.handle

        def handle(ev):
            self.handled.append(ev)
            real(ev)

        self.w.handle = handle

    def test_map_status_delivered_now(self):
        """选地图（set_map）让难度胶囊变了、放进队列的 status：当场交给悬浮窗、记进最近事件，队列里不留；之前留在队列里的
        （不是这次调用放进来的）照旧留着。"""
        name, cap = label_changing_map(self)
        self.session.game.progress_cap = cap
        earlier = Event("panel_prompt")
        self.session.eventlog.push(earlier)  # 识别流程这一帧还没取走的（例如它正在锁外算推荐）
        self.w._choose_map(name)
        self.assertEqual(kinds(self.handled), ["status"])
        self.assertEqual(self.session.take_events(), [earlier])
        noted = [r for r in self.session.recent_events(10 ** 6) if r.get("kind") == "status"]
        self.assertEqual([r.get("seq") for r in noted], [self.handled[0].seq])

    def test_hero_choice_after_game_over(self):
        """三选一显示着、推荐暂停（角色待识别），本局随后结束；这时选了角色：按同一画面开新的一局，新一局和这一次三选一的事件
        当场按先后交给悬浮窗（新一局在前），不留到识别线程下一次来取（手动模式下要等下一次按热键）。"""
        s, w = self.session, self.w

        def call(fn, *args) -> list:
            ev = fn(*args)
            before, after = split_extra(s.take_events())
            out = [*before, *([ev] if ev is not None else []), *after]
            for e in out:
                w.handle(e)
            return out

        call(s.on_hud, SimpleNamespace(round=4, map=None))
        call(s.on_hud, SimpleNamespace(round=4, map=None))
        self.assertIn("hero_needed", kinds(call(s.on_frame, frame(OFFER_FENNI))))
        call(s.on_out_of_game, SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=10))
        self.assertTrue(s.game.game_over)
        self.handled.clear()
        index = w.hero_box.findData(HERO)
        w.hero_box.setCurrentIndex(index)
        w.hero_box.activated.emit(index)
        self.assertEqual(kinds(self.handled)[0], "newgame")
        self.assertGreater(len(self.handled), 1)
        self.assertEqual([e.seq for e in self.handled], sorted(e.seq for e in self.handled))
        self.assertEqual(s.take_events(), [])


if __name__ == "__main__":
    unittest.main()
