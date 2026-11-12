"""合并刷新的守卫：一次操作里中间的几次刷新合成一次，做完以后的显示和不合并（逐个刷新）时一样。

计数：数 overlay_parts.layout.apply_mode——每次真正刷新都会调它一次（refresh 里「显示方式」那一块）；一次操作里刷新被
合并成一次时它只调一次。计数只用这个公开函数，不碰悬浮窗的私有名字。

「和不合并时一样」：一帧的事件（handle_events）和一次交出的几个事件（EventRouter.deliver）能逐事件做一遍对照，
两边的显示逐项相同；其余合并的入口（改正候选、切换方案重算、newgame 带 restore）比较的是「做完以后再刷新一次，
显示不变」（合并只少掉了中间没人看的几次重画，最后一次刷新看到的局面和逐个做时相同），并对内容作断言。
一帧几个事件的几种组合（FrameCombosTest）两套场景喂同一串识别读数，一行显示、全部展开各比一遍，连整窗截图一起比
（比之前先把排着的事件处理完）。窗口没显示时（HiddenWindowTest）、收起的卡片记着收起时已在显示的提示时
（CollapsedCardTest）中间的刷新照做；合并范围里出错时往外抛的仍是原来那个错（BatchErrorTest）。各测试的窗口都在
离屏平台上显示着（不出真窗口）。

全程用 Qt 的 offscreen 平台，不弹窗口、不截屏、不写用户的文件（方案文件放测试沙箱的临时文件夹）。
"""
import sys
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

from PySide6.QtGui import QImage

from advisor.data import load_db
from advisor.schemes import Scheme, SchemeStore, get_catalog
from advisor.session import Event, split_extra
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import layout
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.wording import SCHEME_SWITCHED
from advisor.ui.services import Services
from tests.support import qtenv
from tests.support.overlay_cases import temp_dir
from tests.support.widgets import dispose
from tests.test_overlay_behavior import HERO, OFFER, OFFER2, RESULT, BehaviorBase, FakeLogbook

APP = qtenv.app()

MAP = "异变图书馆"
ROWS = ((60, 37, 3), (40, 45, 15))  # 概率表里的两行：同一个三选一界面上概率行换了，是紧接着出现的另一次三选一


@contextmanager
def refreshes():
    """数一次操作里真正刷新了几次：每次真刷新都会调一次 layout.apply_mode。"""
    with mock.patch.object(layout, "apply_mode", wraps=layout.apply_mode) as spy:
        yield spy


def snapshot(window) -> dict:
    """悬浮窗上能看到的、和刷新有关的东西：各块的文字、可见与否、高度、摘要和悬停说明。"""
    return {
        "texts": list(window.visible_texts()),
        "summary": window.status.summary_plain,
        "tip": window.status.summary_tip_text,
        "status": window.status.status_text(),
        "height": window.height(),
        "cards": {key: (window.cards[key].isHidden(), window.card_open(key)) for key in window.cards},
        "message": (window.message.text, window.message.area),
    }


class BatchBase(BehaviorBase):
    """再加：造第二套一样的场景、按帧取出事件、把探针临时指向另一套场景。窗口在离屏平台上显示着（不出真窗口）：窗口没显示
    时不合并（见 Overlay.refresh）。"""

    def setUp(self):
        super().setUp()
        self.w.show()

    def second(self):
        """再建一套一样的场景（同样的初始状态），用来逐步做一遍、和合并版对照。"""
        db = load_db()
        session = OverlaySession(db, UiSettings(hero=HERO))
        logbook = FakeLogbook()
        w = Overlay(db, session, threading.Lock(), services=Services(logbook=logbook))
        w.run_background = lambda fn: fn()
        w.show()
        self.addCleanup(w.hide)
        return w, session, logbook

    @contextmanager
    def using(self, w, session, logbook):
        """临时把探针指向另一套场景：BehaviorBase 的 feed / enter / offer 等跟着走。"""
        old = (self.w, self.session, self.logbook)
        self.w, self.session, self.logbook = w, session, logbook
        try:
            yield
        finally:
            self.w, self.session, self.logbook = old

    def offer_seen(self, selected=None):
        """BehaviorBase.offer 用的那份识别结果（不交给悬浮窗）。"""
        return SimpleNamespace(names=list(OFFER), raw=list(OFFER), rerolls=3, probabilities=None, rarity=[],
                               team=False, problems=[], selected=selected)

    def events_of(self, fn, *args) -> list:
        """调用 Session，按 FrameProcessor 的顺序取出这一帧的事件（不交给悬浮窗、不刷新）。"""
        ev = fn(*args)
        extra = self.session.take_events()
        before, after = split_extra(extra)
        out = [*before, *([ev] if ev is not None else []), *after]
        if out:
            self.session.note(out)
        return out


class FrameBatchTest(BatchBase):
    """一帧的整批事件（Overlay.handle_events）：处理完只刷新一次，显示和逐个处理一样。"""

    def scene(self):
        """把两边都跑到「记下选择后关闭、同一组候选再次出现」前一帧。"""
        self.enter()
        self.pick_first()  # 记下选择（picked）并关闭

    def test_frame_events_refresh_once(self):
        # 同一组候选再次出现：这一帧是 pick_reverted + advice
        self.scene()
        second_w, second_s, second_lb = self.second()
        with self.using(second_w, second_s, second_lb):
            self.scene()

        with refreshes() as spy:
            out = self.events_of(self.session.on_frame, self.offer_seen())
            self.assertEqual(self.kinds(out), ["pick_reverted", "advice"])
            self.w.handle_events(out)
        self.assertEqual(spy.call_count, 1, "一帧整批处理只该真正刷新一次")
        merged = snapshot(self.w)

        # 逐步版：同样的一帧，每个事件各交一次（每交一次刷新一次），最后的显示应当相同
        with self.using(second_w, second_s, second_lb):
            out2 = self.events_of(self.session.on_frame, self.offer_seen())
            self.assertEqual(self.kinds(out2), ["pick_reverted", "advice"])
            for ev in out2:
                self.w.handle(ev)
        self.assertEqual(snapshot(second_w), merged)

    def test_middle_error_does_not_block_the_rest(self):
        """一帧里中间一个事件处理出错：记进错误记录（sys.excepthook，和 Qt 槽里没接住时同一处），后面的照常处理、
        最后照常刷新一次。"""
        self.enter(rnd=9, star=2)
        with refreshes() as spy, mock.patch.object(sys, "excepthook") as hook:
            self.w.handle_events([Event("status"), Event("advice"), Event("map_read", map=MAP)])
        self.assertEqual(spy.call_count, 1)
        hook.assert_called_once()  # 出错的这一个交给错误记录
        self.assertIn(MAP, self.w.message.text)  # 出错后面的那个事件照常处理了


class DeliverBatchTest(BatchBase):
    """EventRouter.deliver 一次交出好几个事件：交出完只刷新一次，显示和逐个处理一样。"""

    def scene(self):
        self.enter()
        self.pick_first()

    def test_deliver_refresh_once_and_matches_one_by_one(self):
        self.scene()
        second_w, second_s, second_lb = self.second()
        with self.using(second_w, second_s, second_lb):
            self.scene()

        with refreshes() as spy:
            out = self.events_of(self.session.on_frame, self.offer_seen())
            self.assertEqual(self.kinds(out), ["pick_reverted", "advice"])
            self.assertTrue(self.w.router.deliver(None, out))  # 一次把这一帧的事件全交出去
        self.assertEqual(spy.call_count, 1, "一次交出多个事件只该真正刷新一次")
        merged = snapshot(self.w)

        with self.using(second_w, second_s, second_lb):
            out2 = self.events_of(self.session.on_frame, self.offer_seen())
            for ev in out2:
                self.w.handle(ev)
        self.assertEqual(snapshot(second_w), merged)

    def test_recorded_refresh_still_done_after_an_error(self):
        """合并范围里出了异常：已经记下的刷新照样做（finally），异常照旧往外抛。"""
        with refreshes() as spy:
            with self.assertRaises(AttributeError):
                self.w.router.deliver(Event("status"), [Event("advice")])  # 后一个缺字段，处理时出错
        self.assertEqual(spy.call_count, 1, "出错时记下的刷新也要做掉")


class NewGameRestoreBatchTest(BatchBase):
    """newgame 晚到、另有要照着重画的那次推荐：两次刷新合并成一次。"""

    def test_restore_refreshes_once(self):
        self.enter(rnd=9, star=2)
        self.offer()
        self.assertTrue(self.w.live_state.on)
        shown = self.w.advice.shown_ev
        late = Event("newgame", undo=False, why="检测到选角界面")
        late.seq = shown.seq - 1  # 比已经显示的那次推荐早产生：晚到的 newgame，要照它重画
        with refreshes() as spy:
            self.w.handle_events([late])
        self.assertEqual(spy.call_count, 1, "重画那一次要合并进同一次刷新")
        self.assertTrue(self.w.card_open("advice"), "重画后仍然显示着那次推荐")
        for name in OFFER:
            self.assertTrue(any(name in text for text in self.w.advice.texts()))

    def test_router_handle_merges_restore(self):
        """直接调 EventRouter.handle（不带外层 handle_events）：newgame 晚到、另有要照着重画的那次推荐时，handle 自己也把
        两次刷新合并成一次。"""
        self.enter(rnd=9, star=2)
        self.offer()
        shown = self.w.advice.shown_ev
        late = Event("newgame", undo=False, why="检测到选角界面")
        late.seq = shown.seq - 1
        with refreshes() as spy:
            self.w.router.handle(late)
        self.assertEqual(spy.call_count, 1, "EventRouter.handle 自己也要把重画那次刷新合并进同一次")
        self.assertTrue(self.w.card_open("advice"), "重画后仍然显示着那次推荐")


class FixConfirmBatchTest(BatchBase):
    """改正候选（fix_confirm）：交出事件、出提示各自会刷新，合成改正完一次。"""

    def test_fix_confirm_refreshes_once(self):
        self.enter(rnd=9, star=2)
        seen = SimpleNamespace(names=[OFFER[0], None, None], raw=[OFFER[0], "某甲", "某乙"], rerolls=3,
                               probabilities=None, rarity=[], team=False, problems=[],
                               selected=None)
        self.assertEqual(self.kinds(self.unreadable(seen)), ["unreadable"])
        self.w.advice.open_fix(1)
        self.w.fix_box.setEditText(OFFER[1])
        with refreshes() as spy:
            self.w.advice.fix_confirm()
        self.assertEqual(spy.call_count, 1, "改正完只该真正刷新一次")
        self.assertIn(f"已手动指定为「{OFFER[1]}」", self.w.message.text)
        settled = snapshot(self.w)
        self.w.refresh()  # 合并范围已经出来，这次是真刷新：显示不该再变
        self.assertEqual(snapshot(self.w), settled)


class RecomputeBatchTest(BatchBase):
    """切换方案、按当前画面重算出推荐（_recompute）：交出推荐和出提示合成一次刷新。"""

    def setUp(self):
        super().setUp()
        self.store = SchemeStore(temp_dir(self) / "schemes.json", catalog=get_catalog(self.db))
        self.store.add(Scheme(hero=HERO, name="治愈坦克", tiers={OFFER[0]: "core"}))
        w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook),
                    store=self.store)
        w.run_background = lambda fn: fn()
        w.show()
        self.addCleanup(w.hide)
        self.w = w

    def test_scheme_switch_refreshes_once(self):
        self.enter(rnd=9, star=2)
        self.offer()
        index = next(i for i, (text, *_) in enumerate(self.w.scheme.items()) if text == "治愈坦克")
        self.w.scheme_box.setCurrentIndex(index)
        with refreshes() as spy:
            self.w.scheme_box.activated.emit(index)  # 等于用户在方案下拉框里选了一项
        self.assertEqual(spy.call_count, 1, "切换方案重算只该真正刷新一次")
        self.assertIn(SCHEME_SWITCHED.format(name="治愈坦克"), self.w.message.text)
        settled = snapshot(self.w)
        self.w.refresh()
        self.assertEqual(snapshot(self.w), settled)


class MapChangedBatchTest(BatchBase):
    """地图下拉框里选一项（_map_changed）：选地图时难度胶囊跟着变、Session 放进 status，交出它和这里的刷新合并成一次。"""

    def test_map_changed_refreshes_once(self):
        self.enter(rnd=9, star=2)
        # 进度上限是异变图书馆的红叉（17 格）：选出这张地图后难度胶囊变成「噩梦及以上」，Session 会把这次变化放进 status
        self.session.game.progress_cap = 17
        name = MAP
        with refreshes() as spy:
            self.w.map_box.setCurrentText(name)
        self.assertEqual(spy.call_count, 1, "选地图（难度胶囊跟着变）只该真正刷新一次")
        self.assertEqual(self.w.map_box.currentText(), name)
        self.assertIn("难度 噩梦及以上", self.w.status.status_text())


class HiddenWindowTest(BatchBase):
    """窗口没显示（收在托盘里）时不合并，每次都真刷新，和不合并时一样：没显示的窗口里刚建的部件一次排版量不准高度，靠后面
    几次刷新才落定，合并成一次的话从托盘显示出来时高度不对。"""

    def test_hidden_window_refreshes_every_time(self):
        self.enter(rnd=9, star=2)
        frame = [Event("status"), Event("map_read", map=MAP)]
        with refreshes() as spy:
            self.w.handle_events(frame)
        self.assertEqual(spy.call_count, 1, "显示着时整批只刷新一次")
        self.w.hide()
        with refreshes() as spy:
            self.w.handle_events(frame)
        self.assertEqual(spy.call_count, 2, "没显示时每个事件照旧各刷新一次")
        self.w.show()
        with refreshes() as spy:
            self.w.handle_events(frame)
        self.assertEqual(spy.call_count, 1, "显示出来以后照常合并")


class BatchErrorTest(BatchBase):
    """合并范围里出了错，出范围的那次刷新自己也出错：往外抛的仍是范围里原来那个错，刷新的错交给错误记录。"""

    def test_refresh_error_does_not_hide_the_original(self):
        with mock.patch.object(self.w.records, "count_refresh", side_effect=RuntimeError("刷新出错")), \
                mock.patch.object(sys, "excepthook") as hook:
            with self.assertRaises(AttributeError):
                self.w.router.deliver(Event("status"), [Event("advice")])  # 后一个缺字段，处理时出错
        hook.assert_called_once()
        self.assertIs(hook.call_args.args[0], RuntimeError)

    def test_refresh_error_without_other_error_still_raises(self):
        """范围里没出错时，刷新自己的错照旧往外抛（和不合并时刷新出错一样）。"""
        with mock.patch.object(self.w.records, "count_refresh", side_effect=RuntimeError("刷新出错")), \
                mock.patch.object(sys, "excepthook") as hook:
            with self.assertRaises(RuntimeError):
                self.w.router.deliver(Event("status"), [Event("status")])
        hook.assert_not_called()


def frame_seen(names, selected=None, row=0, rerolls=3):
    """一帧三选一的识别结果（FrameProcessor 交给 Session.on_frame 的）；row 是概率行（ROWS 的第几行）。"""
    names = list(names)
    return SimpleNamespace(names=names, raw=[n or "？" for n in names], rerolls=rerolls, probabilities=ROWS[row],
                           rarity=[], team=False, problems=[], selected=selected)


def hud(rnd):
    return SimpleNamespace(round=rnd, map=None)


def stars(n):
    return SimpleNamespace(stars=n, confidence=0.9)


class Twin:
    """两套同样的场景（各自的 Session、悬浮窗和推荐日志假对象，同一个假时钟）喂同一串识别读数：merged 把一帧的事件整批
    处理（handle_events），single 一个事件交一次、各刷新一次（一个事件一个信号时的走法）。窗口在离屏平台上显示着，比较以前
    先把排着的事件处理完（排版落定，和实际运行时画出来之前一样）。"""

    def __init__(self, test, expanded=False):
        self.clock = [1000.0]
        self.sides = []
        for _ in range(2):
            db = load_db()
            session = OverlaySession(db, UiSettings(hero=HERO))
            session._clock = lambda: self.clock[0]
            w = Overlay(db, session, threading.Lock(), services=Services(logbook=FakeLogbook()))
            w.run_background = lambda fn: fn()
            w.set_expanded(expanded)
            w.show()  # 离屏平台：不出真窗口
            test.addCleanup(dispose, w)
            self.sides.append((w, session))
        self.merged_refreshes = 0

    def windows(self):
        return [w for w, _ in self.sides]

    def frame(self, *calls) -> list:
        """一帧：照 FrameProcessor 的先后调 Session（("status",) 是查窗口大小时补的 status 事件），主要事件和待取的事件按
        FrameProcessor._call 排好，两边各自处理；返回事件种类。merged_refreshes 记下整批那边真正刷新了几次。"""
        kinds = []
        for i, (w, session) in enumerate(self.sides):
            events = []
            for method, *args in calls:
                if method == "status":
                    events.append(Event("status"))
                    continue
                ev = getattr(session, method)(*args)
                extra = session.take_events()
                before, after = split_extra(extra)
                events += [*before, *([ev] if ev is not None else []), *after]
            if events:
                session.note(events)
            kinds.append([e.kind for e in events])
            if i == 0:
                with refreshes() as spy:
                    w.handle_events(events)
                self.merged_refreshes = spy.call_count
            else:
                for ev in events:
                    w.handle(ev)
        assert kinds[0] == kinds[1], kinds
        return kinds[0]

    def enter(self, star=2) -> None:
        """进入对局：两次读到轮次，认出角色，两次读到星数（各一帧）。"""
        self.frame(("on_hud", hud(4)))
        self.frame(("on_hud", hud(4)))
        self.frame(("on_hero", SimpleNamespace(hero=HERO)))
        self.frame(("on_stars", stars(star)))
        self.frame(("on_stars", stars(star)))

    def looks(self) -> list[dict]:
        """两边的样子：各块的文字、开合、高度、摘要、提示和整窗截图。先处理排着的事件（排版请求、延迟删除），不跑计时器：
        一次性提示过一会儿自动收起的计时器两边差几毫秒，跑了可能一边收起、一边没收。"""
        APP.sendPostedEvents()
        return [dict(snapshot(w), pixels=w.grab().toImage().convertToFormat(QImage.Format_ARGB32))
                for w in self.windows()]


class FrameCombosTest(unittest.TestCase):
    """一帧里几个事件的几种组合（状态 + 推荐、上一次的结算 + 新的推荐、同一组又出现、新的一局 + 三选一、结束 + 状态、
    漏记 + 读到面板），一行显示和全部展开各做一遍：整批做完和逐个做完，各块的文字、开合、高度和整窗截图都相同，整批只
    真正刷新一次。"""

    def check(self, setup, last: tuple, kinds: list) -> None:
        for expanded in (False, True):
            with self.subTest(全部展开=expanded):
                twin = Twin(self, expanded)
                twin.enter()
                setup(twin)
                self.assertEqual(twin.frame(*last), kinds)
                self.assertEqual(twin.merged_refreshes, 1, "一帧整批处理只该真正刷新一次")
                merged, single = twin.looks()
                self.assertEqual(merged, single)

    @staticmethod
    def picked(twin) -> None:
        """三选一界面里选中第 1 个，之后两帧看不到界面（记下选择、关闭）。"""
        twin.frame(("on_frame", frame_seen(OFFER)))
        twin.frame(("on_frame", frame_seen(OFFER, selected=0)))
        twin.frame(("on_frame", None))
        twin.frame(("on_frame", None))

    def test_status_and_advice(self):
        self.check(lambda t: None, (("status",), ("on_frame", frame_seen(OFFER))), ["status", "advice", "status"])

    def test_pick_settled_by_next_offer(self):
        def setup(t):
            t.frame(("on_frame", frame_seen(OFFER)))
            t.frame(("on_frame", frame_seen(OFFER, selected=0)))

        self.check(setup, (("on_frame", frame_seen(OFFER2, row=1)),), ["picked", "advice"])

    def test_missed_pick_settled_by_next_offer(self):
        self.check(lambda t: t.frame(("on_frame", frame_seen(OFFER))), (("on_frame", frame_seen(OFFER2, row=1)),),
                   ["missed_pick", "advice"])

    def test_same_offer_again(self):
        self.check(self.picked, (("on_frame", frame_seen(OFFER)),), ["pick_reverted", "advice"])

    def test_new_game_at_offer(self):
        """上一局结束后的三选一界面上开新的一局：新的一局还没认出角色（hero_needed），或这一帧就认出了（advice）。"""
        def setup(t):
            self.picked(t)
            t.frame(("on_out_of_game", RESULT))

        self.check(setup, (("begin_offer",), ("on_frame", frame_seen(OFFER2))),
                   ["newgame", "hero_needed", "panel_prompt", "status"])
        self.check(setup, (("begin_offer",), ("on_hero", SimpleNamespace(hero=HERO)), ("on_frame", frame_seen(OFFER2))),
                   ["newgame", "status", "advice", "panel_prompt", "status"])

    def test_game_over_and_status(self):
        self.check(self.picked, (("on_frame", None), ("on_out_of_game", RESULT)), ["game_over", "status"])

    def test_missed_pick_and_panel_read(self):
        def setup(t):
            t.frame(("on_frame", frame_seen(OFFER)))
            t.frame(("on_frame", None))

        reading = SimpleNamespace(is_panel=True, self_tab=True, owned=[OFFER[0], OFFER2[2]], unknown_slots=0,
                                  confidence=0.95)
        self.check(setup, (("on_frame", None), ("on_panel", reading)), ["missed_pick", "panel_read"])


class CollapsedCardTest(unittest.TestCase):
    """收起的卡片记着收起时已在显示的提示（例如角色待识别时收起「筹码方案」）：卡片要不要自动展开看每一次刷新时有哪些提示，
    一帧里先消失、又出现的同一条提示要让它照旧展开。这时中间那次刷新照做，和逐个处理一样。"""

    def test_hero_card_reopens_for_next_offer(self):
        twin = Twin(self)
        twin.frame(("on_hud", hud(4)))
        twin.frame(("on_hud", hud(4)))
        twin.frame(("on_stars", stars(1)))
        twin.frame(("on_stars", stars(1)))  # 还没认出角色
        self.assertEqual(twin.frame(("on_frame", frame_seen(OFFER))), ["hero_needed", "status"])
        for w in twin.windows():
            self.assertTrue(w.card_open("scheme"))
            w.cards["scheme"].toggle.click()  # 收起「筹码方案」
            self.assertFalse(w.card_open("scheme"))
        twin.frame(("on_frame", frame_seen(OFFER, selected=0)))
        # 紧接着的另一次三选一（概率行换了）：上一次在这一帧里结算，新的这次角色仍待识别。上一次没给推荐（角色待识别），
        # 关掉时另发一条 unadvised（推荐日志、对局报告记没给推荐的那几次），排在这一帧的事件后面
        self.assertEqual(twin.frame(("on_frame", frame_seen(OFFER2, row=1))), ["picked", "hero_needed", "unadvised"])
        merged, single = twin.looks()
        self.assertEqual(merged, single)
        for w in twin.windows():
            self.assertTrue(w.card_open("scheme"), "新的一次三选一，收起的「筹码方案」照旧自动展开")
        self.assertEqual(twin.merged_refreshes, 2, "卡片记着收起时的提示：中间那次刷新照做")


if __name__ == "__main__":
    unittest.main()
