"""悬浮窗按事件序号（Event.seq）只显示最新的推荐。

识别线程在锁里调用 Session 算好事件，放锁以后才发信号，排着队等界面线程处理；这中间用户换了方案、改正了候选或选了角色，
界面线程当场重算、显示新的推荐，排着队的旧事件随后才送到。旧事件比新推荐早产生（序号小）：送到时只记下，不显示、
不记推荐日志（同一编号以后一条为准，记进去会把新的盖掉）。之后识别线程新算的照常显示；没有序号的事件（Session 以外
建的）不比先后，照常处理。

识别线程的事件用 hold 先取出来（照 FrameProcessor._call 的顺序），等界面上的操作做完再交给悬浮窗，相当于排着队的信号。
事件全部由真实的 Session（OverlaySession）产生；推荐日志用假对象，方案文件放在测试沙箱的临时文件夹里。只用公开探针。
"""
from __future__ import annotations

import dataclasses
import threading
import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QPushButton

from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog
from advisor.session import Event, split_extra
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import DISMISS, HERO_PAUSED, SCHEME_SWITCHED
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services
from tests.support import qtenv, sandbox
from tests.test_overlay_behavior import HERO, OFFER, OFFER2, RESULT, BehaviorBase, FakeLogbook

APP = qtenv.app()

OFFER_FENNI = ["医疗箱-完备治疗", "紫色飞星", "大背包"]  # 芬妮按方案「治愈坦克」首推医疗箱-完备治疗，默认推荐不是它
HEAL_BEST = "医疗箱-完备治疗"
# 两个任务接连完成：概率行换成另一行的另一次三选一（两次之间没有棋盘画面）
FIRST_ROW, SECOND_ROW = (60, 37, 3), (40, 45, 15)
OFFER_B = ["书签", "标靶", "大背包"]
OFFER_C = ["标记喷罐", "额外电池", "探天卫星"]


def frame(names, probabilities=None, rerolls=3, selected=None):
    return SimpleNamespace(names=list(names), raw=[n or "？" for n in names], rerolls=rerolls,
                           probabilities=probabilities, rarity=[], team=False, problems=[], selected=selected)


def own(n: int):
    return SimpleNamespace(stars=n, confidence=0.9)


class GameLogbook(FakeLogbook):
    """推荐日志的假对象，另记每条推荐记在哪一局（局号）。"""

    def __init__(self):
        super().__init__()
        self.offer_games: list[str] = []

    def record_offer(self, state, advice, offer_id, client=None):
        super().record_offer(state, advice, offer_id)
        self.offer_games.append(self.game)


class LateBase(BehaviorBase):
    """离屏悬浮窗，接上一个沙箱里的方案文件（芬妮有一套「治愈坦克」）。喂事件的辅助方法同 BehaviorBase。"""

    def setUp(self):
        self.db = load_db()
        self.logbook = GameLogbook()
        self.session = OverlaySession(self.db, UiSettings(hero=HERO))
        self.store = SchemeStore(sandbox.temp_dir(self, "late-events-") / "schemes.json", catalog=get_catalog(self.db))
        self.store.add(Scheme(hero=HERO, name="治愈坦克", tiers={HEAL_BEST: "core"}))
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook),
                         store=self.store)
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def hold(self, fn, *args) -> list:
        """识别线程在锁里调用 Session、算好了还没送到界面线程的事件：照 FrameProcessor._call 的顺序取出来、记进最近
        事件，先不交给悬浮窗。"""
        with self.w.lock:
            ev = fn(*args)
            extra = self.session.take_events()
            before, after = split_extra(extra)
            out = [*before, *([ev] if ev is not None else []), *after]
            if out:
                self.session.note(out)
        return out

    def deliver(self, events) -> None:
        """排着队的事件送到界面线程。"""
        for ev in events:
            self.w.handle(ev)

    def choose_scheme(self, name: str) -> None:
        """用户在方案下拉框里点选一项。"""
        index = next(i for i, (text, _, _) in enumerate(self.w.scheme.items()) if text == name)
        self.w.scheme_box.setCurrentIndex(index)
        self.w.scheme_box.activated.emit(index)

    def choose_hero(self, hero: str) -> None:
        """用户在角色下拉框里点选一项。"""
        index = self.w.hero_box.findData(hero)
        self.w.hero_box.setCurrentIndex(index)
        self.w.hero_box.activated.emit(index)

    def bests(self) -> list[str]:
        """推荐日志里每条推荐的首推，按记下的先后。"""
        return [best for _, _, best in self.logbook.offers]


class SchemeSwitchTest(LateBase):
    def test_queued_advice_does_not_cover_switched(self):
        """三选一期间识别线程按默认推荐算好一条（读到了升星），还没送到时用户换了方案：界面上按新方案重算的推荐留着，
        晚到的旧推荐不显示、不记推荐日志；之后同一画面 Session 不再发事件，悬浮窗也一直是新方案的推荐。"""
        self.enter(star=1)
        self.feed(self.session.on_frame, frame(OFFER_FENNI))
        default_best = self.best()
        self.assertNotEqual(default_best, HEAL_BEST)
        held = self.hold(self.session.on_stars, own(2), True) + self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.assertEqual(self.kinds(held), ["status", "advice"])
        self.assertNotEqual(held[-1].advice.best, HEAL_BEST)  # 晚到的是按默认推荐算的
        self.choose_scheme("治愈坦克")
        self.deliver(held)
        for mode in self.modes():
            with self.subTest(mode=mode):
                advice = self.w.advice.texts()
                self.assertIn(f"★ {HEAL_BEST}", advice)
                self.assertNotIn(f"★ {default_best}", advice)
                self.assertIn(SCHEME_SWITCHED.format(name="治愈坦克"), self.w.status.status_text())
        self.assertEqual(self.bests(), [default_best, HEAL_BEST])  # 晚到的没记
        self.assertEqual(len({offer_id for offer_id, _, _ in self.logbook.offers}), 1)  # 同一次三选一
        self.assertEqual(self.feed(self.session.on_frame, frame(OFFER_FENNI)), [])  # 局面没变：不再发事件
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())

    def test_first_advice_still_queued(self):
        """三选一刚出现，识别线程算好的第一条推荐还没送到时用户换了方案：显示按新方案算的，排着的那条不显示、不记。"""
        self.enter()
        held = self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.assertEqual(self.kinds(held), ["advice"])
        self.assertNotEqual(held[0].advice.best, HEAL_BEST)
        self.choose_scheme("治愈坦克")
        self.deliver(held)
        self.assertTrue(self.w.live_state.on)
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        self.assertEqual(self.bests(), [HEAL_BEST])

    def test_interleaved_batches(self):
        """交错到达：换方案前识别线程算好的一批（旧）和换方案后算好的一批（新）先后送到。旧的那批里的推荐不显示、不记，
        新的那批照常显示、照常记；提示类事件（status）两批都照常处理。"""
        self.enter(star=1)
        self.feed(self.session.on_frame, frame(OFFER_FENNI))
        default_best = self.best()
        old = self.hold(self.session.on_stars, own(2), True) + self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.choose_scheme("治愈坦克")
        new = self.hold(self.session.on_stars, own(3), True) + self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.assertEqual(self.kinds(old), ["status", "advice"])
        self.assertEqual(self.kinds(new), ["status", "advice"])
        self.assertLess(old[-1].seq, new[-1].seq)
        self.deliver(old + new)
        self.assertEqual(self.bests(), [default_best, HEAL_BEST, HEAL_BEST])  # 起初、换方案时、新的那批各一条
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        self.assertIn("3 星", self.w.status.status_text())

    def test_event_without_seq_always_handled(self):
        """没有序号（0）的事件不比先后：已经处理过序号更大的推荐以后照样显示。"""
        self.enter()
        held = self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.choose_scheme("治愈坦克")
        self.deliver(held)
        self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        plain = dataclasses.replace(held[0], seq=0)
        self.w.handle(plain)
        self.assertIn(f"★ {plain.advice.best}", self.w.advice.texts())
        self.w.handle(Event("idle"))
        self.assertFalse(self.w.live_state.on)


class FixAndHeroTest(LateBase):
    def test_queued_advice_does_not_undo_fix(self):
        """改正候选时识别线程正好算好一条（改正前的名字）：改正后重算的推荐留着，「手动指定」的那一格不被换回去。"""
        self.enter(star=1)
        self.feed(self.session.on_frame, frame(OFFER_FENNI))
        self.w.advice.open_fix(2)  # 想把「大背包」改成「书签」
        self.w.fix_box.setEditText("书签")
        held = self.hold(self.session.on_stars, own(2), True) + self.hold(self.session.on_frame, frame(OFFER_FENNI))
        self.assertEqual(self.kinds(held), ["status", "advice"])
        self.w.fix_box.lineEdit().returnPressed.emit()
        self.deliver(held)
        body = "｜".join(self.w.visible_texts(self.w.body))
        self.assertIn("（手动指定）", body)
        self.assertIn("书签", body)
        self.assertEqual(set(self.logbook.offers[-1][1]), {"医疗箱-完备治疗", "紫色飞星", "书签"})

    def test_queued_hero_needed_after_choosing_hero(self):
        """角色待识别、推荐暂停时刷新了候选：识别线程算好的「请选择角色」还没送到，用户就选了角色、按新的候选给出推荐；
        晚到的「请选择角色」不显示，推荐不再暂停。"""
        self.hud(4)
        self.hud(4)
        self.stars(1)  # 还没认出角色
        self.assertEqual(self.kinds(self.offer()), ["hero_needed"])
        # 刷新：候选换了一组、刷新次数少 1（三张全换而次数没少的是紧接着出现的另一次三选一）
        held = self.hold(self.session.on_frame, frame(OFFER2, rerolls=2))
        self.assertEqual(self.kinds(held), ["hero_needed"])
        self.choose_hero(HERO)
        self.deliver(held)
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertNotIn("推荐已暂停", self.w.status.summary_plain)
                advice = self.w.advice.texts()
                self.assertNotIn(HERO_PAUSED, advice)
                self.assertTrue(any(t.startswith("★ ") for t in advice), advice)
        self.assertEqual(set(self.logbook.offers[-1][1]), set(OFFER2))


class LateCloseTest(LateBase):
    def test_late_newgame_keeps_newer_advice(self):
        """上一局已结束，下一局的第一次三选一：识别线程先判定新的一局、再算好推荐，都还没送到时用户换了方案。按新方案
        算的推荐先显示，随后送到的 newgame 照常开新的一局，推荐照新方案的重画（记进新的一局），排着的旧推荐不显示；
        三选一显示着却空着的情况不出现。"""
        self.enter()
        self.feed(self.session.on_out_of_game, RESULT)
        held = (self.hold(self.session.begin_offer) + self.hold(self.session.on_hero, SimpleNamespace(hero=HERO))
                + self.hold(self.session.on_frame, frame(OFFER_FENNI)))
        queued = next(e for e in held if e.kind == "advice")
        self.assertEqual(held[0].kind, "newgame")
        self.assertNotEqual(queued.advice.best, HEAL_BEST)
        self.choose_scheme("治愈坦克")
        self.deliver(held)
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.live_state.on)
                self.assertTrue(self.w.card_open("advice"))
                self.assertIn(f"★ {HEAL_BEST}", self.w.advice.texts())
        self.assertEqual(self.logbook.games_log[-1][0], "new")
        self.assertEqual((self.bests()[-1], self.logbook.offer_games[-1]), (HEAL_BEST, self.logbook.game))  # 新的一局
        self.assertNotIn(queued.advice.best, self.bests())

    def test_late_missed_pick_not_logged_on_old_offer(self):
        """两个任务接连完成（两次三选一之间没有棋盘画面）、前一次没看到选择：识别线程算好「漏记」和下一次的推荐，还没送到
        时用户换了方案。晚到的「漏记」照常出补录行，补录记进已有筹码；推荐日志里那一次的编号已经给了后来的推荐，
        补录的不记进推荐日志，更不会记到更早那次漏记的编号上。"""
        self.enter()
        self.feed(self.session.on_frame, frame(OFFER, FIRST_ROW))
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])
        self.click_missed(OFFER[0])  # 更早的一次漏记：补录了，推荐日志记在那一次的编号上
        before = list(self.logbook.picks)
        self.assertEqual(before[-1][1], OFFER[0])
        self.feed(self.session.on_frame, frame(OFFER_B, FIRST_ROW))
        held = self.hold(self.session.on_frame, frame(OFFER_C, SECOND_ROW))
        self.assertEqual(self.kinds(held), ["missed_pick", "advice"])
        self.choose_scheme("治愈坦克")
        self.deliver(held)
        self.assertTrue(self.w.live_state.on)
        self.assertEqual(self.w.owned.missed_buttons_text(), [*OFFER_B, DISMISS])
        self.click_missed(OFFER_B[0])
        self.assertIn(OFFER_B[0], self.session.settings.owned)
        self.assertEqual(self.logbook.picks, before)

    def click_missed(self, name: str) -> None:
        """点补录行里的筹码按钮。"""
        button = next(b for b in self.w.missed.findChildren(QPushButton) if b.text() == name and not b.isHidden())
        button.click()


if __name__ == "__main__":
    unittest.main()
