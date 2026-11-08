"""对局里按地图自动换筹码方案：记账这一层（Session.scheme_choice、scheme_plan、stop_map_scheme、take_map_switch）和悬浮窗
这一层（认出、改判地图时换用并出一次性提示，三选一开着时按换成的方案重算，「筹码方案」卡片里「本局按地图换用了……」那一行
和「换回原来的」，方案下拉框的「适用地图」悬停说明，编辑窗口新建、导入后使用，本局结束）。

保存的方案（方案存储里这个角色上次用的那套，SchemeStore.current）和这一局实际用的方案分开：按地图换只改后者，不存盘；
手动换方案改的是前者。方案文件放在测试沙箱的临时文件夹里；悬浮窗用 Qt 的 offscreen 平台，不会弹出窗口。"""
import threading
import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog, taggable_maps
from advisor.session import SchemeChoice, Session, Settings
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.state import take_view
from advisor.ui.overlay_parts.wording import (MAP_AUTO, MAP_SCHEME_REVERT, MAP_SCHEME_REVERTED, MAP_SCHEME_REVERT_TIP,
                                              MAP_SCHEME_SWITCHED, MAP_SCHEME_TEXT, SCHEME_SET, SCHEME_SWITCHED)
from advisor.ui.services import Services
from tests.support.fakes import FakeLogbook
from tests.support.overlay_cases import (OFFER_FENNI, expire_flash, frame, plain_html, shown, temp_dir, tip_text)

HERO = "芬妮"
LONG = "龙宫游乐园"
MAGIC = "魔法学院"
GARDEN = "园林中庭"  # 下面谁都没标它
FIELD = "海选赛运动场"  # 没有编号的地图：不能标在方案上
RESULT = SimpleNamespace(kind="result", difficulty=None, map=None, rounds=None)  # 结算画面：读到一次就结束本局
SNAKE_OFFER = ["小蛇玩偶", "医疗箱-完备治疗", "大背包"]  # 小蛇玩偶是龙宫游乐园的专属筹码


class Schemes:
    """几套方案放进测试沙箱里的方案文件：芬妮的 U（通用，没标地图，三选一首推大背包）、L1 和 L2（都标了龙宫游乐园，L1
    在前；L1 首推医疗箱-完备治疗）、M1（标了魔法学院，首推紫色飞星）、X（通用），凛的 R（标了龙宫游乐园）。芬妮保存的是 U。"""

    def __init__(self, test: unittest.TestCase, db):
        self.store = SchemeStore(temp_dir(test) / "schemes.json", catalog=get_catalog(db))
        add = self.store.add
        self.u = add(Scheme(hero=HERO, name="U", tiers={"大背包": "core"}))
        self.l1 = add(Scheme(hero=HERO, name="L1", maps=(LONG,), tiers={"医疗箱-完备治疗": "core"}))
        self.l2 = add(Scheme(hero=HERO, name="L2", maps=(LONG,), tiers={"紫色飞星": "core"}))
        self.m1 = add(Scheme(hero=HERO, name="M1", maps=(MAGIC,), tiers={"紫色飞星": "core"}))
        self.x = add(Scheme(hero=HERO, name="X", tiers={"紫色飞星": "core"}))
        self.r = add(Scheme(hero="凛", name="R", maps=(LONG,)))
        self.store.set_current(HERO, self.u.id)


class SessionMapSchemeTest(unittest.TestCase):
    """记账这一层：这一局实际用哪套每次现算（scheme_choice），推荐按它算（scheme_plan）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.k = Schemes(self, self.db)
        self.store = self.k.store
        self.s = self.session()

    def session(self, hero_seen: bool = True) -> Session:
        s = Session(self.db, Settings(hero=HERO))
        if hero_seen:
            s.game.seen_hero = HERO
        s.scheme_for, s.schemes_of = self.store.current, self.store.schemes
        return s

    def test_generic_saved_switches_to_first_tagged(self):
        """保存的是通用方案、这个角色有两套标了这张地图：认出地图以后这一局用方案下拉框里靠前的那一套（L1）；保存的方案不变
        （不存盘）。别的角色标了这张地图的方案不算进来，那个角色保存的方案也不动；换成那个角色时按它自己的方案算。"""
        s, k = self.s, self.k
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        self.assertEqual(s.scheme_plan().name, "U")
        s.on_loading(LONG)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.l1, LONG))
        self.assertEqual(s.scheme_plan().name, "L1")
        self.assertEqual(self.store.current(HERO), k.u)
        self.assertIsNone(self.store.current("凛"))
        s.set_hero("凛")
        self.assertEqual(s.scheme_choice(), SchemeChoice(None, k.r, LONG))
        s.set_hero(HERO)
        self.assertEqual(s.scheme_choice().used, k.l1)

    def test_saved_tagged_kept(self):
        """保存的方案就标了这张地图：照用它，不换（排在它前面、也标了这张地图的 L1 不换过去），也没有要告诉的。"""
        self.store.set_current(HERO, self.k.l2.id)
        self.s.on_loading(LONG)
        self.assertEqual(self.s.scheme_choice(), SchemeChoice(self.k.l2, self.k.l2, None))
        self.assertIsNone(self.s.take_map_switch())

    def test_saved_tagged_other_map(self):
        """保存的方案只标了别的地图（魔法学院）：到了龙宫游乐园换成标了它的 L1；改判到谁都没标的园林中庭用保存的；
        到了魔法学院也是保存的（它本来就标了）。"""
        s, k = self.s, self.k
        self.store.set_current(HERO, k.m1.id)
        s.on_loading(LONG)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.m1, k.l1, LONG))
        s.set_map(GARDEN)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.m1, k.m1, None))
        s.set_map(MAGIC)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.m1, k.m1, None))

    def test_stop_map_scheme(self):
        """这一局不再按地图换（stop_map_scheme）：之后地图怎么改判都用保存的方案；局面版本加一（在锁外算的推荐作废）。
        新的一局清掉；撤销新的一局时连同告诉过的那一次跟着那一局回来（不重复告诉）。还没有这一局的对局状态时（刚启动、
        没进对局）不打，之后开始的一局照常换。"""
        s, k = self.s, self.k
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))
        edits = s._edits
        self.assertTrue(s.stop_map_scheme())
        self.assertEqual(s._edits, edits + 1)
        self.assertTrue(s.game.map_scheme_off)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        for name in (MAGIC, None, LONG):
            s.set_map(name)
            self.assertEqual(s.scheme_choice().used, k.u)
        self.assertIsNone(s.take_map_switch())
        self.assertIsNotNone(s.on_select_screen())  # 选角界面：新的一局（可以撤销）
        self.assertFalse(s.game.map_scheme_off)
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))
        self.assertTrue(s.undo_new_game())
        self.assertTrue(s.game.map_scheme_off)
        self.assertEqual(s.scheme_choice().used, k.u)

        fresh = self.session(hero_seen=False)
        self.assertFalse(fresh.has_game_state)
        self.assertFalse(fresh.stop_map_scheme())
        self.assertFalse(fresh.game.map_scheme_off)
        fresh.on_loading(LONG)
        self.assertEqual(fresh.scheme_choice().used, k.l1)

    def test_undo_keeps_told(self):
        """撤销新的一局：那一局换着的方案告诉过了，换回来以后不再告诉。"""
        s, k = self.s, self.k
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch().used, k.l1)
        self.assertIsNotNone(s.on_select_screen())
        self.assertIsNone(s.take_map_switch())  # 新的一局地图还不知道
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.scheme_choice().used, k.l1)
        self.assertIsNone(s.take_map_switch())

    def test_game_over_and_new_game(self):
        """本局结束：回到保存的方案（也忘掉告诉过的那一次）；新的一局认出地图以后照常再换，再告诉一次。"""
        s, k = self.s, self.k
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))
        self.assertIsNone(s.take_map_switch())
        self.assertEqual(s.on_out_of_game(RESULT).kind, "game_over")
        self.assertTrue(s.game.game_over)
        self.assertEqual(s.settings.map, LONG)  # 地图留到新的一局开始，方案照样回到保存的
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        self.assertIsNone(s.take_map_switch())
        s.new_game()
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))

    def test_map_rejudged(self):
        """地图改判：龙宫游乐园（L1）→ 魔法学院（M1）→ 园林中庭（谁都没标：保存的）→ 交回待识别（保存的）。每换上一套告诉
        一次（take_map_switch），回到保存的不告诉，之后再换过去照样再告诉。"""
        s, k = self.s, self.k
        s.on_loading(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))
        s.set_map(MAGIC)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.m1, MAGIC))
        self.assertIsNone(s.take_map_switch())
        s.set_map(GARDEN)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        self.assertIsNone(s.take_map_switch())
        s.set_map(None)
        self.assertEqual(s.scheme_choice(), SchemeChoice(k.u, k.u, None))
        s.set_map(LONG)
        self.assertEqual(s.take_map_switch(), SchemeChoice(k.u, k.l1, LONG))

    def test_default_saved_switches(self):
        """保存的是默认推荐：同样换成标了这张地图的方案；这一局不再按地图换以后回到默认推荐。"""
        s, k = self.s, self.k
        self.store.set_current(HERO, None)
        self.assertEqual(s.scheme_choice(), SchemeChoice(None, None, None))
        self.assertIsNone(s.scheme_plan())
        s.on_loading(LONG)
        self.assertEqual(s.scheme_choice(), SchemeChoice(None, k.l1, LONG))
        self.assertEqual(s.scheme_plan().name, "L1")
        self.assertTrue(s.stop_map_scheme())
        self.assertIsNone(s.scheme_plan())

    def test_advice_follows_used_scheme(self):
        """推荐按这一局实际用的方案算：三选一开着时认出、改判地图，同一个画面立即重算（recompute）或下一帧就按换成的方案算；
        地图是从三选一里的地图专属筹码认出来的，那一帧就按换成的方案算。"""
        s = self.s
        ev = s.on_frame(frame(OFFER_FENNI))
        self.assertEqual((ev.advice.scheme.name, ev.advice.best), ("U", "大背包"))
        s.on_loading(LONG)
        ev = s.on_frame(frame(OFFER_FENNI))
        self.assertEqual((ev.kind, ev.advice.scheme.name, ev.advice.best), ("advice", "L1", "医疗箱-完备治疗"))
        s.set_map(MAGIC)
        ev = s.recompute()
        self.assertEqual((ev.advice.scheme.name, ev.advice.best), ("M1", "紫色飞星"))
        self.assertIsNone(s.recompute())  # 方案没再变：不重复给推荐

        chips = self.session()
        ev = chips.on_frame(frame(SNAKE_OFFER))
        self.assertEqual(chips.settings.map, LONG)
        self.assertEqual((ev.kind, ev.advice.scheme.name), ("advice", "L1"))
        self.assertIsNone(chips.recompute())

    def test_untaggable_map(self):
        """不能标在方案上的地图（没有编号的运动场）：不换，方案里写着它也不换。"""
        self.assertNotIn(FIELD, taggable_maps(self.db))
        self.store.add(Scheme(hero=HERO, name="运动场", maps=(FIELD,)))
        s = self.s
        s.on_loading(FIELD)
        self.assertEqual(s.settings.map, FIELD)
        self.assertEqual(s.scheme_choice(), SchemeChoice(self.k.u, self.k.u, None))
        self.assertIsNone(s.take_map_switch())

    def test_without_store(self):
        """没有接上方案存储：默认推荐，不换；只接了 scheme_for、没接 schemes_of：用保存的，不换；资料库未收录的角色：默认推荐。"""
        s = Session(self.db, Settings(hero=HERO))
        s.on_loading(LONG)
        self.assertEqual(s.scheme_choice(), SchemeChoice())
        self.assertIsNone(s.take_map_switch())
        s.scheme_for = self.store.current
        self.assertEqual(s.scheme_choice(), SchemeChoice(self.k.u, self.k.u, None))
        self.assertEqual(s.scheme_choice("某位新角色"), SchemeChoice())


class OverlayMapSchemeTest(unittest.TestCase):
    """悬浮窗这一层：刷新开头跟上这一局实际用的方案（重算、一次性提示、识别记录），「换回原来的」（revert_map_scheme），
    方案下拉框、编辑窗口、导入后使用都让这一局不再按地图换，本局结束时回到保存的方案。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.k = Schemes(self, self.db)
        self.store = self.k.store
        self.session = OverlaySession(self.db, UiSettings(hero=HERO))
        self.session.game.seen_hero = HERO
        self.logbook = FakeLogbook()
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook),
                         store=self.store)
        self.w.run_background = lambda fn: fn()
        self.addCleanup(self.w.hide)

    def recognize(self, name: str) -> None:
        """识别线程在加载画面上认出了地图：持锁交给记账，事件交给悬浮窗（相当于排队的信号）。"""
        with self.w.lock:
            ev = self.session.on_loading(name)
        self.w.handle(ev)

    def choose_scheme(self, name: str) -> None:
        """模拟用户在方案下拉框里点选一项。"""
        w = self.w
        index = next(i for i, (text, _, _) in enumerate(w.scheme.items()) if text == name)
        w.scheme_box.setCurrentIndex(index)
        w.scheme_box.activated.emit(index)

    def records(self, **match) -> list[dict]:
        """识别记录里的用户操作 scheme（换方案），可按字段筛。"""
        return [e for e in self.session.recent_events(600) if e.get("kind") == "read:user"
                and e.get("action") == "scheme" and all(e.get(k) == v for k, v in match.items())]

    def count_set_current(self) -> list:
        """记下之后每次改保存的方案（SchemeStore.set_current）。"""
        calls, real = [], self.store.set_current

        def counting(hero, scheme_id):
            calls.append((hero, scheme_id))
            real(hero, scheme_id)

        self.store.set_current = counting
        return calls

    def advice(self) -> tuple:
        adv = self.w.advice.last_ev.advice
        return adv.scheme.name if adv.scheme is not None else None, adv.best

    def test_auto_switch(self):
        """三选一开着时认出地图：按换成的方案立即重算（推荐日志记在同一次三选一上），出一次性提示 MAP_SCHEME_SWITCHED，方案
        下拉框和卡片头的摘要都是换成的那套，保存的方案不变；识别记录记一条 scheme（via 为 map，带地图名）。只提示一次，之后
        刷新不再提示。快照里有「本局按地图换用了……」那一行要的几项，诊断包里有实际用的、保存的、按哪张地图换的。"""
        w, k = self.w, self.k
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        offer_id = w.offers.offer_id
        self.assertEqual(self.advice(), ("U", "大背包"))
        self.recognize(LONG)
        self.assertEqual(self.advice(), ("L1", "医疗箱-完备治疗"))
        self.assertEqual([o[0] for o in self.logbook.offers], [offer_id, offer_id])
        message = MAP_SCHEME_SWITCHED.format(name="L1")
        self.assertEqual(message, "这张地图有专用方案：已换用「L1」")
        self.assertEqual(w.message.text, message)
        self.assertEqual(w.scheme_box.currentText(), "L1")
        self.assertEqual(w.scheme.current_scheme(), k.l1)
        self.assertEqual(w.scheme.summary()[0][0], w.scheme.summary(HERO, take_view(self.session))[0][0])
        self.assertIn("L1", w.scheme.summary()[0][0])
        self.assertEqual(self.store.current(HERO), k.u)
        view = take_view(self.session)
        self.assertEqual((view.map_switched, view.scheme_name, view.saved_scheme_name), (True, "L1", "U"))
        told = self.records(via="map")
        self.assertEqual([(r["hero"], r["scheme"], r["name"], r["map"]) for r in told], [(HERO, k.l1.id, "L1", LONG)])
        expire_flash(w)
        w.refresh()
        self.assertNotEqual(w.message.text, message)
        self.assertEqual(len(self.records(via="map")), 1)
        self.assertEqual(len(self.logbook.offers), 2)  # 方案没再变：刷新时不重复给推荐
        state = w.background.snapshot()["overlay"]["schemes"]
        self.assertEqual((state["current"], state["saved"], state["map"], state["map_off"]),
                         ({"id": k.l1.id, "name": "L1"}, {"id": k.u.id, "name": "U"}, LONG, False))

    def test_switch_in_same_frame_as_chips(self):
        """地图是从三选一里的地图专属筹码认出来的：那一帧就按换成的方案算（不另算一次），同样出一次提示。"""
        w = self.w
        with w.lock:
            ev = self.session.on_frame(frame(SNAKE_OFFER))
        w.handle(ev)
        self.assertEqual(self.session.settings.map, LONG)
        self.assertEqual(self.advice()[0], "L1")
        self.assertEqual(len(self.logbook.offers), 1)
        self.assertEqual(w.message.text, MAP_SCHEME_SWITCHED.format(name="L1"))
        self.assertEqual(w.scheme_box.currentText(), "L1")

    def test_revert(self):
        """「换回原来的」（revert_map_scheme）：换回保存的那套、按它立即重算，提示 MAP_SCHEME_REVERTED，不改方案文件；识别记录
        记一条 scheme（via 为 map_revert，带地图名）。之后改判地图也不再换；已经换回了再调用什么都不做。"""
        w, k = self.w, self.k
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.recognize(LONG)
        calls = self.count_set_current()
        self.assertTrue(w.revert_map_scheme())
        self.assertEqual(self.advice(), ("U", "大背包"))
        message = MAP_SCHEME_REVERTED.format(name="U")
        self.assertEqual(message, "已换回「U」，这一局不再按地图自动换方案")
        self.assertEqual(w.message.text, message)
        self.assertEqual(w.scheme_box.currentText(), "U")
        self.assertFalse(take_view(self.session).map_switched)
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.current(HERO), k.u)
        reverted = self.records(via="map_revert")
        self.assertEqual([(r["scheme"], r["name"], r["map"]) for r in reverted], [(k.u.id, "U", LONG)])
        self.assertFalse(w.revert_map_scheme())
        self.assertEqual(len(self.records(via="map_revert")), 1)
        w._map_changed(MAGIC)
        self.assertEqual((w.scheme_box.currentText(), self.advice()[0]), ("U", "U"))
        self.assertEqual(len(self.records(via="map")), 1)

    def test_revert_without_offer(self):
        """没有三选一时「换回原来的」：没有可重算的推荐，照样出提示，方案下拉框回到保存的那套。"""
        w = self.w
        self.recognize(LONG)
        self.assertTrue(w.revert_map_scheme())
        self.assertEqual(w.message.text, MAP_SCHEME_REVERTED.format(name="U"))
        self.assertEqual(w.scheme_box.currentText(), "U")
        self.assertTrue(self.session.game.map_scheme_off)

    def test_dropdown_saved_and_same(self):
        """按地图换着时在方案下拉框里：选的就是这一局正在用的那套，什么都不做（不改方案文件、不记识别记录、照样按地图换）；
        选回保存的那套，等于点了「换回原来的」（不调 set_current）。"""
        w, k = self.w, self.k
        self.recognize(LONG)
        calls = self.count_set_current()
        before = len(self.records())
        self.choose_scheme("L1")
        self.assertEqual((calls, len(self.records()), self.session.game.map_scheme_off), ([], before, False))
        self.assertEqual(w.scheme_box.currentText(), "L1")
        self.choose_scheme("U")
        self.assertEqual(calls, [])
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertEqual(w.message.text, MAP_SCHEME_REVERTED.format(name="U"))
        self.assertEqual(self.records()[-1]["via"], "map_revert")
        self.assertEqual((w.scheme_box.currentText(), self.store.current(HERO)), ("U", k.u))

    def test_dropdown_other_scheme(self):
        """按地图换着时在方案下拉框里选了另一套：记下这个角色用它（set_current），这一局不再按地图换，按它立即重算并提示
        SCHEME_SWITCHED；之后改判地图也不再换。"""
        w, k = self.w, self.k
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.recognize(LONG)
        calls = self.count_set_current()
        self.choose_scheme("X")
        self.assertEqual(calls, [(HERO, k.x.id)])
        self.assertEqual(self.store.current(HERO), k.x)
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertEqual(self.advice(), ("X", "紫色飞星"))
        self.assertEqual(w.message.text, SCHEME_SWITCHED.format(name="X"))
        self.assertFalse(take_view(self.session).map_switched)
        self.assertNotIn("via", self.records()[-1])
        w._map_changed(MAGIC)
        w._map_changed(LONG)
        self.assertEqual((w.scheme_box.currentText(), self.advice()[0]), ("X", "X"))
        self.assertEqual(len(self.records(via="map")), 1)

    def test_editor_and_import_stop_switch(self):
        """编辑窗口新建、复制了一套（scheme_added）、导入以后点了「使用这套方案」（use_requested）：记下这个角色用它，这一局
        不再按地图换；导入后使用的那套正好就是按地图换上的那套时也一样（照样记下、不再按地图换）。"""
        w, k = self.w, self.k
        self.recognize(LONG)
        w._use_requested(HERO, k.l1.id)
        self.assertEqual(self.store.current(HERO), k.l1)
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertFalse(take_view(self.session).map_switched)
        self.assertEqual(w.scheme_box.currentText(), "L1")
        self.assertEqual(self.records()[-1]["via"], "import")
        self.assertEqual(w.message.text, SCHEME_SET.format(name="L1"))

        with w.lock:
            self.session.new_game()
            self.store.set_current(HERO, k.u.id)
        w.refresh()
        self.assertFalse(self.session.game.map_scheme_off)
        self.recognize(LONG)
        self.assertEqual(w.scheme_box.currentText(), "L1")
        new = self.store.add(Scheme(hero=HERO, name="新的"))
        w._scheme_added(HERO, new.id)
        self.assertEqual(self.store.current(HERO), new)
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertEqual(w.scheme_box.currentText(), "新的")
        self.assertEqual(self.records()[-1]["via"], "editor")

    def test_game_over_returns_to_saved(self):
        """本局结束：方案下拉框回到保存的那套，不出提示、不记识别记录，「换回原来的」也没有可换回的了；新的一局认出地图以后
        照常再换、再提示。"""
        w, k = self.w, self.k
        self.recognize(LONG)
        self.assertEqual(w.scheme_box.currentText(), "L1")
        expire_flash(w)
        before = len(self.records())
        with w.lock:
            ev = self.session.on_out_of_game(RESULT)
        w.handle(ev)
        self.assertEqual(w.scheme_box.currentText(), "U")
        self.assertEqual(w.scheme.current_scheme(), k.u)
        self.assertFalse(take_view(self.session).map_switched)
        self.assertNotIn("专用方案", w.message.text)
        self.assertNotIn("已换回", w.message.text)
        self.assertEqual(len(self.records()), before)
        self.assertFalse(w.revert_map_scheme())
        with w.lock:
            self.session.new_game()
        w.refresh()
        self.recognize(LONG)
        self.assertEqual(w.scheme_box.currentText(), "L1")
        self.assertEqual(w.message.text, MAP_SCHEME_SWITCHED.format(name="L1"))
        self.assertEqual(len(self.records(via="map")), 2)

    def test_map_dropdown_back_to_auto(self):
        """地图交回自动识别（地图未知）：回到保存的方案，不出提示；三选一开着时按它立即重算。"""
        w = self.w
        w.handle(self.session.on_frame(frame(OFFER_FENNI)))
        self.recognize(LONG)
        self.assertEqual(self.advice()[0], "L1")
        expire_flash(w)
        w._map_changed(MAP_AUTO)
        self.assertIsNone(self.session.settings.map)
        self.assertEqual(w.scheme_box.currentText(), "U")
        self.assertEqual(self.advice(), ("U", "大背包"))
        self.assertNotIn("专用方案", w.message.text)


class SchemeMapNoticeTest(unittest.TestCase):
    """「筹码方案」卡片里「本局按地图换用了……」那一行（带「换回原来的」）和方案下拉框的「适用地图」悬停说明。照「已有筹码」
    卡片里「移出」那一行带「撤销」的做法：一行显示、全部展开、卡片收起时都看得见这一行。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.k = Schemes(self, self.db)
        self.store = self.k.store
        self.session = OverlaySession(self.db, UiSettings(hero=HERO))
        self.session.game.seen_hero = HERO
        self.w = self.make()

    def make(self, store=None) -> Overlay:
        w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()),
                    store=store or self.store)
        w.run_background = lambda fn: fn()
        self.addCleanup(w.hide)
        return w

    def recognize(self, name: str) -> None:
        """识别线程在加载画面上认出了地图：持锁交给记账，事件交给悬浮窗。"""
        with self.w.lock:
            ev = self.session.on_loading(name)
        self.w.handle(ev)

    def row(self):
        """这一轮刷新的提示行里「本局按地图换用了……」那一行（键 map_switch），没有时为 None。"""
        return self.w.notice_model.notices.get("map_switch")

    def test_row_and_button(self):
        """按地图换着：卡片里有那一行（文字、级别、按钮、悬停说明），排在「筹码方案」卡片里。"""
        w = self.w
        self.assertIsNone(self.row())
        self.recognize(LONG)
        row = self.row()
        self.assertEqual((row[0], row[1], row[2]), ("map_switch", MAP_SCHEME_TEXT.format(name="L1"), "info"))
        self.assertEqual(row[1], "本局按地图换用了「L1」")
        self.assertEqual(w.notice_model.area("map_switch"), "scheme")
        btns = w.notices.buttons("map_switch")
        self.assertEqual([b.text() for b in btns], [MAP_SCHEME_REVERT])
        self.assertEqual(btns[0].text(), "换回原来的")
        self.assertEqual(tip_text(btns[0].toolTip()), MAP_SCHEME_REVERT_TIP.format(name="U"))
        self.assertEqual(tip_text(btns[0].toolTip()), "换回「U」，这一局不再按地图自动换")
        self.assertIn("本局按地图换用了「L1」", shown(w, w.scheme_notices))
        self.assertIn("本局按地图换用了「L1」", shown(w, w.scheme_card))
        self.assertIn("换回原来的", shown(w, w.scheme_card))

    def test_saved_default_tip(self):
        """保存的是默认推荐：悬停说明写默认推荐的叫法（text.SCHEME_DEFAULT）。"""
        w = self.w
        self.store.set_current(HERO, None)
        w.refresh()
        self.recognize(LONG)
        self.assertEqual(self.row()[1], "本局按地图换用了「L1」")
        self.assertEqual(tip_text(w.notices.buttons("map_switch")[0].toolTip()),
                         "换回「默认推荐」，这一局不再按地图自动换")

    def test_revert_click(self):
        """点「换回原来的」：调用了换回（提示、重算、打标记都在那里），这一行收起，这一局不再按地图换。"""
        w = self.w
        self.recognize(LONG)
        w.notices.buttons("map_switch")[0].click()
        self.assertIsNone(self.row())
        self.assertNotIn("map_switch", w.notice_model.notices)
        self.assertEqual(w.message.text, MAP_SCHEME_REVERTED.format(name="U"))
        self.assertTrue(self.session.game.map_scheme_off)
        self.assertEqual(w.scheme_box.currentText(), "U")
        self.assertFalse(take_view(self.session).map_switched)

    def test_hides_after_game_and_new_game(self):
        """对局结束、新的一局：这一行收起；新的一局认出地图以后照常再出现。"""
        w = self.w
        self.recognize(LONG)
        with w.lock:
            ev = self.session.on_out_of_game(RESULT)
        w.handle(ev)
        self.assertIsNone(self.row())
        with w.lock:
            self.session.new_game()
        w.refresh()
        self.assertIsNone(self.row())
        self.recognize(LONG)
        self.assertEqual(self.row()[1], MAP_SCHEME_TEXT.format(name="L1"))

    def test_special_characters_plain(self):
        """方案名里有尖括号、& 时按纯文本显示：不当作网页标记，标签上的文字就是方案名本身。"""
        store = SchemeStore(temp_dir(self) / "special.json", catalog=get_catalog(self.db))
        store.add(Scheme(hero=HERO, name="A<B&C>", maps=(LONG,)))
        w = self.w = self.make(store)
        self.recognize(LONG)
        text = "本局按地图换用了「A<B&C>」"
        self.assertEqual(self.row()[1], text)
        labels = [lb.text() for lb in w.scheme_notices.findChildren(QLabel) if lb.text()]
        self.assertIn(text, labels)

    def test_markup_name_plain(self):
        """方案名像网页标记（<b>粗</b>、&lt;）时也照原样显示：那一行的标签按纯文本画，不画成粗体，&lt; 也不画成 <。"""
        name = "<b>粗</b>&lt;"  # 正好 NAME_MAX 个字，不截短
        store = SchemeStore(temp_dir(self) / "markup.json", catalog=get_catalog(self.db))
        store.add(Scheme(hero=HERO, name=name, maps=(LONG,)))
        w = self.w = self.make(store)
        self.recognize(LONG)
        text = MAP_SCHEME_TEXT.format(name=name)
        self.assertEqual(self.row()[1], text)
        labels = [lb for lb in w.scheme_notices.findChildren(QLabel) if lb.text() == text]
        self.assertEqual([lb.textFormat() for lb in labels], [Qt.PlainText])

    def test_dropdown_applicable_maps(self):
        """方案下拉框：标了适用地图的那几项悬停说明最后一段写「适用地图：……」（按 Scheme.maps 的先后、用「、」隔开），
        通用方案不写；行上的文字照旧只有方案名。"""
        w = self.w
        self.store.add(Scheme(hero=HERO, name="两张图", maps=(LONG, MAGIC)))
        w.refresh()
        tips = {text: (data, plain_html(tip_text(w.scheme_box.itemData(i, Qt.ToolTipRole))))
                for i, (text, data, _) in enumerate(w.scheme.items()) if data is not None}
        self.assertTrue(tips["L1"][1].endswith("适用地图：龙宫游乐园"), tips["L1"][1])
        self.assertTrue(tips["M1"][1].endswith("适用地图：魔法学院"))
        self.assertTrue(tips["两张图"][1].endswith("适用地图：龙宫游乐园、魔法学院"))
        for generic in ("默认推荐", "U", "X"):
            self.assertNotIn("适用地图", tips[generic][1])
        self.assertEqual([text for text, _, _ in w.scheme.items() if text in ("L1", "M1", "两张图")],
                         ["L1", "M1", "两张图"])

    def test_one_line_and_collapsed(self):
        """一行显示、全部展开、卡片收起时照「移出」那一行：一行显示时「筹码方案」卡片随这一行出现；全部展开时卡片收起着，
        又换了一套（这一行的文字变了）时自动展开。"""
        w = self.w
        self.assertTrue(w.scheme_card.isHidden())
        self.recognize(LONG)
        self.assertFalse(w.scheme_card.isHidden())
        w.set_expanded(True)
        w.scheme_card.toggle.click()  # 用户收起
        self.assertFalse(w.card_open("scheme"))
        self.assertEqual(plain_html(w.scheme_card.summary.text()), "芬妮 · L1")
        w.set_expanded(False)
        self.assertFalse(w.scheme_card.isHidden())
        w.set_expanded(True)
        w._map_changed(MAGIC)  # 地图改判、又换了一套：这一行变了，卡片自动展开
        self.assertTrue(w.card_open("scheme"))
        self.assertIn("本局按地图换用了「M1」", shown(w, w.scheme_notices))


if __name__ == "__main__":
    unittest.main()
