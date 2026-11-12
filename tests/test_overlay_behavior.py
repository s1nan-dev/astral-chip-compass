"""悬浮窗的「行为级」测试：只用公开探针（summary_text、status_text、summary_tip、visible_texts、advice_texts、
notice_texts、notice_buttons、missed_buttons_text、card_open、needs_attention 等）断言屏幕上能看到的文字和卡片的开合。

喂进来的是真实的 Session（OverlaySession）产生的一串识别事件（Overlay.handle 认识的每一种 kind 各至少一条），按
FrameProcessor 的顺序交给悬浮窗，不手搓事件对象（字段和实际运行时一致）；必要时先喂前置事件：进对局、认出角色、
读到星数、给出三选一……断言摘要、状态、提示行、各卡片在一行显示和全部展开两种方式下的样子。

这批测试刻画的是屏幕上看得到的行为，不碰内部结构：拆分、整理模块时一行都不用改，一直通过，
就说明屏幕上显示的东西没变。另有专门测试的几类情形（旧推荐晚到、游戏还原后的「已最小化」提示、
单击悬浮窗算不算拖动、退出时识别线程卡住、错误记录的上限）不在这里固定：相关场景要么不写，要么只断言
和它们无关的部分。

和字体、宽度无关：一行显示的摘要放不下时从末尾省略，三选一期间的摘要按宽度换行，所以摘要只断言开头的关键字和
单独的一项；整句在状态行（status_text）、悬停说明（summary_tip）、提示行和卡片里核对。
和推荐结果无关：不断言哪个筹码是首推、推荐度多少。选中首推和选中别的，显示不一样（后者要问原因），要选首推时
从推荐日志的假对象里取这一次的首推。

搭场景不碰 Session 的私有成员：拨时钟经构造参数交给 Session（见 BehaviorBase.setUp）。
全程用 Qt 的 offscreen 平台，不弹窗口、不截屏、不写文件（Session 不给设置文件的路径，推荐日志是假对象）。
"""
import threading
import unittest
from types import SimpleNamespace

from advisor.data import load_db
from advisor.session import split_extra
from advisor.session.selection import HOLD_MAX_S
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import DISMISS, FEEDBACK_QUESTION, FEEDBACK_REASONS, GAIN_TEXT, GAME_OVER_WAITING, HERO_PAUSED, MAP_READ, MISSED_PROMPT, NEWGAME_MIDGAME, NEWGAME_OWNED, NEWGAME_TEXT, PANEL_READ, PANEL_TAB, PICKED_LABEL, PROMPT_HERO, PROMPT_PANEL, REVERTED_NOTICE, REVERTED_PROMPT, STALE_CAPTION
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services
from tests.support import qtenv

APP = qtenv.app()

# 三选一用的候选筹码：都是资料库里的普通筹码，不牵涉同系列上位替代那种特殊显示；芬妮 1 星第 3 轮、剩 3 次刷新时首推是
# 筹码（银行卡-余额少），「选中首推」的几处要用
OFFER = ["星币锤", "维生素药丸", "银行卡-余额少"]
OFFER2 = ["维生素药丸", "摩托头盔-中级", "优雅之羽"]  # 下一次三选一：和 OFFER 不是同一组
HERO = "芬妮"
WAITING = "等待筹码三选一界面…"  # 自动模式、对局进行中的等待文字
RESULT = SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=10)  # 结算画面


class FakeLogbook:
    """推荐日志的假对象：只接住 Overlay 会调用的几个方法，不写文件、不连真日志。每次推荐记下首推。"""

    PICK_REROLL = "刷新"
    game = "g1"

    def __init__(self):
        self.offers, self.picks, self.feedback, self.game_overs, self.tables = [], [], [], [], []
        self.unadvised = []
        self.games_log = []

    def record_table(self, **record):
        self.tables.append(record)
        return self.game

    def current_game(self):
        return self.game

    def new_game(self):
        self.games_log.append(("new", self.game))
        self.game = f"g{len(self.games_log) + 1}"
        return self.game

    def undo_new_game(self, previous):
        self.games_log.append(("undo", previous))
        self.game = previous

    def record_offer(self, state, advice, offer_id, client=None, map_source=None, source_by=None,
                     difficulty_source=None, owned_sources=None, owned_panel=None):
        self.offers.append((offer_id, tuple(e.chip.name for e in advice.evals), advice.best))

    def record_pick(self, offer_id, chosen, via=None):
        self.picks.append((offer_id, chosen))

    def record_unadvised(self, select_no, **item):
        self.unadvised.append({"select_no": select_no, **item})

    def record_feedback(self, offer_id, reason, note):
        self.feedback.append((offer_id, reason, note))

    def record_game_over(self, **info):
        self.game_overs.append(info)
        return self.game

    def records_between(self, start, end=None):
        return []

    def game_count_since_calibration(self):
        return 0


class BehaviorBase(unittest.TestCase):
    """建一个离屏悬浮窗，可以喂识别事件、拨时钟。断言一律走公开探针。"""

    def setUp(self):
        self.db = load_db()
        self.logbook = FakeLogbook()
        self.clock = [1000.0]
        # 假时钟：「对局中途判定新一局」要看另一项 20 秒内的读数，拨过去不能等墙上时间；时钟经构造参数交给 Session
        self.session = OverlaySession(self.db, UiSettings(hero=HERO), clock=lambda: self.clock[0])
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook))
        self.w.run_background = lambda fn: fn()  # 后台线程改为直接调用
        self.addCleanup(self.w.hide)

    # ------------------------------------------------------------ 喂事件（照 FrameProcessor 的顺序）

    def feed(self, fn, *args) -> list:
        """调用一个 Session 方法，把它返回的事件和待取队列里的事件按 FrameProcessor._call 的顺序交给悬浮窗（session.split_extra：
        撤销、上一次三选一的结算在主要事件之前，其余提示类事件在后）。"""
        ev = fn(*args)
        extra = self.session.take_events()
        before, after = split_extra(extra)
        out = [*before, *([ev] if ev is not None else []), *after]
        if out:
            self.session.note(out)  # 和 FrameProcessor 一样记进最近事件
        for e in out:
            self.w.handle(e)
        return out

    def hud(self, rnd, **kw) -> list:
        return self.feed(self.session.on_hud, SimpleNamespace(round=rnd, map=kw.pop("map", None), **kw))

    def hero(self, name) -> list:
        return self.feed(self.session.on_hero, SimpleNamespace(hero=name))

    def stars(self, n) -> list:
        return self.feed(self.session.on_stars, SimpleNamespace(stars=n, confidence=0.9))

    def offer(self, names=None, selected=None, rerolls=3) -> list:
        names = list(OFFER if names is None else names)
        seen = SimpleNamespace(names=names, raw=[n or "？" for n in names], rerolls=rerolls,
                               probabilities=None, rarity=[], team=False, problems=[], selected=selected)
        return self.feed(self.session.on_frame, seen)

    def close(self) -> list:
        """三选一界面关闭（连续两帧看不到）。"""
        return self.feed(self.session.on_frame, None) + self.feed(self.session.on_frame, None)

    def unreadable(self, seen) -> list:
        """三选一刚出现时有筹码没读出来：记账先等一等、不发事件，拨过等的时间（HOLD_MAX_S）再读到同样的画面才发「无法识别」。
        返回这两次交给悬浮窗的事件。"""
        out = self.feed(self.session.on_frame, seen)
        self.clock[0] += HOLD_MAX_S
        return out + self.feed(self.session.on_frame, seen)

    def panel(self, owned, unknown=0) -> list:
        reading = SimpleNamespace(is_panel=True, self_tab=True, owned=list(owned),
                                  unknown_slots=unknown, confidence=0.95)
        return self.feed(self.session.on_panel, reading)

    def enter(self, rnd=3, star=1) -> None:
        """进入对局：连续两次读到轮次，认出角色，连续两次读到星数。"""
        self.hud(rnd)
        self.hud(rnd)
        self.hero(HERO)
        self.stars(star)
        self.stars(star)

    def best(self) -> str:
        """最近一次推荐的首推（推荐日志的假对象记下的）。"""
        return self.logbook.offers[-1][2]

    def pick_first(self) -> str:
        """三选一界面里选中第 1 个候选后关闭，返回选中的筹码名（不管它是不是首推）。"""
        self.offer()
        self.offer(selected=0)
        self.close()
        return OFFER[0]

    def pick_best(self, offered: bool = False) -> str:
        """三选一界面里选中首推后关闭（不问未选择首推的原因），返回选中的筹码名。offered：三选一界面已经显示着。"""
        if not offered:
            self.offer()
        chip = self.best()
        self.assertIn(chip, OFFER, "首推是刷新：这组候选不适合「选中首推」的场景")
        self.offer(selected=OFFER.index(chip))
        self.close()
        return chip

    def advance(self, seconds: float) -> None:
        self.clock[0] += seconds

    @staticmethod
    def kinds(events) -> list:
        return [e.kind for e in events]

    # ------------------------------------------------------------ 看界面

    def modes(self):
        """一行显示和全部展开两种方式，依次切过去；用完回到一行显示。"""
        for expanded in (False, True):
            self.w.set_expanded(expanded)
            yield "全部展开" if expanded else "一行显示"
        self.w.set_expanded(False)

    def texts(self, card: str) -> list[str]:
        """某张卡片里正在显示的文字（标签、胶囊、按钮）。"""
        return self.w.visible_texts(self.w.cards[card])

    def shown(self, card: str) -> str:
        return "\n".join(self.texts(card))


class AdviceEventTest(BehaviorBase):
    """三选一给出推荐（advice）。"""

    def test_advice_opens_recommendation_card(self):
        self.enter()
        self.assertEqual(self.kinds(self.offer()), ["advice"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("advice"))
                self.assertIn("刷新 ×3", self.w.status.summary_plain)
                self.assertIn("刷新 ×3", self.w.status.status_text())
                texts = self.w.advice.texts()
                for name in OFFER:
                    self.assertTrue(any(name in t for t in texts), (mode, name))
                self.assertIn(f"★ {self.best()}", texts)  # 首推标 ★

    def test_collapsed_advice_card_reopens_on_next_offer(self):
        """三选一期间收起「推荐」卡片：同一次三选一里的更新不再展开它；下一次三选一出现时自动展开。"""
        self.enter()
        self.offer()
        self.w.set_expanded(True)
        self.w.cards["advice"].toggle.click()
        self.offer()  # 同一个画面
        self.stars(2)  # 同一次三选一期间读到星数
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertFalse(self.w.card_open("advice"))
        self.pick_best(offered=True)  # 选中首推：关闭后不问原因，卡片不会因为这个展开
        self.assertEqual(self.kinds(self.offer(OFFER2)), ["advice"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("advice"))
                self.assertTrue(any("优雅之羽" in t for t in self.w.advice.texts()))


class PickedEventTest(BehaviorBase):
    """选中后关闭（picked）：记下选择；选的不是首推时问原因。"""

    def test_picked_best_closes_card(self):
        """选中首推：一行显示时「推荐」卡片收回；全部展开时写「已选」，可以回看上一次推荐；已有筹码里多了它。"""
        self.enter()
        self.offer()
        chip = self.best()
        self.assertIn(chip, OFFER, "首推是刷新：这组候选不适合「选中首推」的场景")
        self.offer(selected=OFFER.index(chip))
        self.assertEqual(self.kinds(self.close()), ["picked"])
        message = f"已记录选择：{chip}。等待下一次筹码三选一"
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.status.summary_plain.startswith(f"已记录选择：{chip}"), self.w.status.summary_plain)
                self.assertIn(message, self.w.status.status_text())
        self.w.set_expanded(False)
        self.assertFalse(self.w.card_open("advice"))
        self.w.set_expanded(True)
        self.assertTrue(self.w.card_open("advice"))
        advice = self.w.advice.texts()
        self.assertIn(f"{PICKED_LABEL}{chip}（首推）", advice)
        self.assertIn(STALE_CAPTION, advice)
        self.assertIn(message, advice)
        self.assertIn(chip, self.texts("owned"))

    def test_picked_other_asks_why(self):
        """选的不是首推：两种显示方式下「推荐」卡片都开着，写「已选」并问未选择首推的原因，四个原因按钮看得见。"""
        self.enter()
        self.offer()
        best = self.best()
        chip = next(n for n in OFFER if n != best)
        self.offer(selected=OFFER.index(chip))
        self.assertEqual(self.kinds(self.close()), ["picked"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("advice"))
                advice = self.w.advice.texts()
                self.assertIn(f"{PICKED_LABEL}{chip}", advice)
                self.assertIn(FEEDBACK_QUESTION.format(best=best), advice)
                for reason in FEEDBACK_REASONS:
                    self.assertIn(reason, advice)
                self.assertIn(f"已记录选择：{chip}。等待下一次筹码三选一", self.w.status.status_text())


class IdleEventTest(BehaviorBase):
    """三选一没给出推荐就关了（idle）。"""

    def test_idle_after_unreadable_offer(self):
        """候选筹码一个都没认出来就关了：不请补录；一行显示时「推荐」卡片收回，全部展开时写等待文字。"""
        self.enter()
        seen = SimpleNamespace(names=[None, None, None], raw=["高效", "2", "优雅"], rerolls=3, probabilities=None,
                               rarity=[], team=False, selected=None, problems=[])
        self.assertEqual(self.kinds(self.unreadable(seen)), ["unreadable"])
        self.assertEqual(self.kinds(self.close()), ["idle", "unadvised"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertIn(WAITING, self.w.status.status_text())
                self.assertNotIn("无法识别", self.w.status.summary_plain)  # 候选筹码那一行不再写
                self.assertFalse(any("无法识别" in t for t in self.w.advice.texts()))
                self.assertEqual(self.w.owned.missed_buttons_text(), [])  # 没有认出来的候选：不出补录行
        self.w.set_expanded(False)
        self.assertFalse(self.w.card_open("advice"))
        self.w.set_expanded(True)
        self.assertIn(WAITING, self.w.advice.texts())


class UnreadableEventTest(BehaviorBase):
    """候选筹码没读全（unreadable）：列出改正入口。"""

    def test_unreadable_lists_bad_slots(self):
        self.enter(rnd=9, star=3)
        seen = SimpleNamespace(names=[None, None, "优雅之羽"], raw=["高效", "2", "优雅"], rerolls=3,
                               probabilities=None, rarity=[], team=False, selected=None,
                               problems=["第 1 个候选筹码无法识别（识别文字：「高效」）"])
        self.assertEqual(self.kinds(self.unreadable(seen)), ["unreadable"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("advice"))
                self.assertEqual(self.w.status.summary_plain, "候选筹码 第 1、2 个无法识别")  # 只有一项，不会换行
                self.assertIn("候选筹码 第 1、2 个无法识别", self.w.status.status_text())
                self.assertIn("「高效」无法识别", self.shown("advice"))


class RefreshingEventTest(BehaviorBase):
    """点刷新后的中间帧（refreshing）：保留刷新前的推荐，只标「正在刷新」。"""

    def test_refreshing_keeps_previous_advice(self):
        self.enter(rnd=9, star=3)
        self.offer(rerolls=5)
        before = self.w.advice.texts()
        middle = SimpleNamespace(names=[None, None, "优雅之羽"], raw=["高效", "2", "优雅"], rerolls=4,
                                 probabilities=None, rarity=[], team=False, selected=None, problems=[])
        self.assertEqual(self.kinds(self.feed(self.session.on_frame, middle)), ["refreshing"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                after = self.w.advice.texts()
                self.assertIn("正在刷新", after)  # 卡片头标「正在刷新」
                self.assertEqual([t for t in after if t != "正在刷新"], before)  # 刷新前的推荐原样保留
                summary = self.w.status.summary_plain
                self.assertTrue(summary.startswith("正在刷新"), summary)
                self.assertIn("来源 ", summary)
                status = self.w.status.status_text()
                self.assertIn("正在刷新", status)
                self.assertNotIn("刷新 ×", status)  # 「正在刷新」占了「刷新 ×N」的位置


class HeroNeededEventTest(BehaviorBase):
    """角色待识别（hero_needed）：推荐暂停，请用户在「筹码方案」卡片里选角色。"""

    def test_hero_needed_pauses_advice(self):
        self.hud(4)
        self.hud(4)
        self.stars(1)  # 还没认出角色
        self.assertEqual(self.kinds(self.offer()), ["hero_needed"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                summary = self.w.status.summary_plain
                self.assertIn("角色 待识别", summary)
                self.assertIn("推荐已暂停", summary)
                self.assertTrue(self.w.card_open("scheme"))  # 角色下拉框在这张卡片里
                self.assertTrue(self.w.card_open("advice"))
                advice = self.shown("advice")
                self.assertIn(PROMPT_HERO, advice)
                self.assertIn(HERO_PAUSED, advice)
                self.assertTrue(self.w.needs_attention())  # 要用户选角色：边框变色


class MissedPickEventTest(BehaviorBase):
    """漏记（missed_pick）：补录行列出候选筹码按钮。"""

    def test_missed_pick_shows_row(self):
        self.enter()
        self.offer()
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("owned"))
                owned = self.texts("owned")
                self.assertIn(MISSED_PROMPT, owned)
                for name in OFFER:
                    self.assertIn(name, owned)
                self.assertEqual(self.w.owned.missed_buttons_text(), [*OFFER, DISMISS])
                self.assertTrue(self.w.needs_attention())  # 需要用户操作：边框变色

    def test_collapsed_owned_card_reopens_on_missed(self):
        """收起「已有筹码」卡片时里面只有「请打开查看筹码面板」的提示：收起后不再展开；之后漏记时自动展开，补录行看得见。"""
        self.enter()
        self.offer()
        self.w.set_expanded(True)
        self.w.cards["owned"].toggle.click()
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertFalse(self.w.card_open("owned"))
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("owned"))
                self.assertIn(MISSED_PROMPT, self.texts("owned"))
                self.assertEqual(self.w.owned.missed_buttons_text(), [*OFFER, DISMISS])


class PickRevertedEventTest(BehaviorBase):
    """同一组候选再次出现（pick_reverted）：撤销刚记下的筹码。"""

    def test_reverted_after_pick(self):
        self.enter()
        chip = self.pick_first()
        self.assertEqual(self.kinds(self.offer()), ["pick_reverted", "advice"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertEqual(self.w.notice_model.texts().get("reverted"), REVERTED_NOTICE.format(chip=chip))
                self.assertTrue(self.w.card_open("advice"))
                self.assertIn(REVERTED_NOTICE.format(chip=chip), self.w.advice.texts())
        self.w.set_expanded(True)
        self.assertNotIn(chip, self.texts("owned"))  # 刚记下的已从已有筹码中撤销

    def test_reverted_then_missed_row_uses_reverted_prompt(self):
        """撤销之后这一次也没看到选择：补录行的文字写明是「再次出现、已撤销」。"""
        self.enter()
        chip = self.pick_first()
        self.offer()
        self.assertEqual(self.kinds(self.close()), ["missed_pick"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.card_open("owned"))
                self.assertIn(REVERTED_PROMPT.format(chip=chip), self.texts("owned"))
                self.assertEqual(self.w.owned.missed_buttons_text(), [*OFFER, DISMISS])
                self.assertNotIn("reverted", self.w.notice_model.texts())  # 三选一已关闭：「推荐」卡片里那一行收起


class UnrecordedGainEventTest(BehaviorBase):
    """星数增加却没有对应的三选一（unrecorded_gain）：提示补录。"""

    def test_gain_notice_offers_recovery(self):
        self.enter(star=1)
        self.stars(2)  # 1→2：记下差一次三选一
        self.stars(3)  # 2→3：合起来差两个
        self.stars(3)  # 对局画面上再读到：宽限一次
        out = self.stars(3)  # 再读到一次：确认没有三选一
        self.assertIn("unrecorded_gain", self.kinds(out))
        why = "星数由 1 星增加为 3 星，未检测到对应的筹码三选一界面"
        text = GAIN_TEXT.format(why=why, n=" 2 个筹码")
        self.assertEqual(self.w.gain_pending(), 2)
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertEqual(self.w.notice_model.texts().get("gain"), text)
                self.assertTrue(self.w.card_open("owned"))
                self.assertIn(text, self.texts("owned"))
                self.assertEqual([b.text() for b in self.w.notices.buttons("gain")], ["补录筹码", DISMISS])
                self.assertTrue(self.w.needs_attention())


class GameOverEventTest(BehaviorBase):
    """本局结束（game_over）：「对局 已结束」，需要操作的提示收起、边框不变色，上一次推荐和已有筹码保留。"""

    def test_game_over_shows_ended(self):
        self.enter()
        chip = self.pick_best()  # 选中首推：不留「未选择首推的原因」，一行显示时卡片才都收得起来
        out = self.feed(self.session.on_out_of_game, RESULT)
        self.assertIn("game_over", self.kinds(out))
        for mode in self.modes():
            with self.subTest(mode=mode):
                summary = self.w.status.summary_plain
                self.assertTrue(summary.startswith("对局 已结束"), summary)
                status = self.w.status.status_text()
                self.assertIn("对局 已结束", status)
                self.assertIn("难度 噩梦", status)
                self.assertIn(GAME_OVER_WAITING, status)
                self.assertEqual(self.w.notice_model.texts(), {})  # 「请打开查看筹码面板」收起
                self.assertFalse(self.w.needs_attention())
        self.w.set_expanded(False)
        self.assertFalse(any(self.w.card_open(k) for k in self.w.cards))  # 一行显示时四张卡片都收起
        self.w.set_expanded(True)
        self.assertIn(STALE_CAPTION, self.w.advice.texts())  # 上一次推荐可以回看
        self.assertIn(chip, self.texts("owned"))


class NewGameEventTest(BehaviorBase):
    """新的一局（newgame）：对局中途误判的可以撤销，结算画面之后的是不可撤销的说明。"""

    def test_midgame_newgame_undoable(self):
        self.enter(rnd=8, star=2)
        chip = self.pick_first()  # 记下一个已有筹码，撤销时才看得出恢复
        self.advance(30)  # 超过互相印证的 20 秒
        out = self.hud(1) + self.hud(1)  # 第 8 轮 → 第 1 轮：判定为新的一局
        self.assertIn("newgame", self.kinds(out))
        midgame = NEWGAME_MIDGAME.format(why="轮次由第 8 轮变为第 1 轮")
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.needs_attention())
                self.assertTrue(self.w.card_open("recog"))
                self.assertTrue(self.w.card_open("owned"))
                texts = self.w.notice_model.texts()
                self.assertEqual(texts["newgame"], midgame)
                self.assertEqual(texts["newgame_owned"], NEWGAME_OWNED)
                self.assertIn(midgame, self.texts("recog"))
                self.assertIn(NEWGAME_OWNED, self.texts("owned"))
                self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        self.w.set_expanded(True)
        self.assertNotIn(chip, self.texts("owned"))  # 已有筹码随新的一局清空
        self.w.notices.buttons("newgame")[0].click()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.assertIn(chip, self.texts("owned"))  # 撤销后恢复误判前的已有筹码

    def test_newgame_after_result_is_info_only(self):
        self.enter()
        self.pick_first()
        self.feed(self.session.on_out_of_game, RESULT)
        self.assertEqual(self.kinds(self.feed(self.session.on_select_screen)), ["newgame"])
        expected = NEWGAME_TEXT.format(why="上一局已结束，检测到选角界面")
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertEqual(self.w.notice_model.texts(), {})  # 不是提示行，是一句一次性的说明
                self.assertTrue(self.w.status.summary_plain.startswith("已判定为新的一局"), self.w.status.summary_plain)
                self.assertIn(expected, self.w.status.summary_tip_text)  # 摘要放不下时整句在悬停说明里
                self.assertIn(expected, self.w.status.status_text())
                self.assertFalse(self.w.needs_attention())  # 不可撤销：没有可做的操作
        self.w.set_expanded(True)
        self.assertIn(expected, self.texts("recog"))


class PanelEventsTest(BehaviorBase):
    """「查看筹码」面板相关：请打开（panel_prompt）、读到（panel_read）、队友页签（panel_tab）。"""

    def test_panel_prompt_asks_to_open(self):
        out = self.hud(1) + self.hud(1)
        self.assertIn("panel_prompt", self.kinds(out))
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertEqual(self.w.notice_model.texts().get("panel"), PROMPT_PANEL)
                self.assertTrue(self.w.card_open("owned"))
                self.assertIn(PROMPT_PANEL, self.texts("owned"))
                self.assertTrue(self.w.needs_attention())
                self.assertEqual([b.text() for b in self.w.notices.buttons("panel")], [DISMISS])

    def test_panel_read_flashes_result(self):
        self.enter()
        self.assertEqual(self.kinds(self.panel(["星币锤", "维生素药丸"])), ["panel_read"])
        text = PANEL_READ.format(n=2)
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.status.summary_plain.startswith("已识别已有筹码 2 个"), self.w.status.summary_plain)
                self.assertIn(text, self.w.status.status_text())
                self.assertFalse(self.w.needs_attention())
                self.assertNotIn("panel", self.w.notice_model.texts())  # 读到面板以后不再请用户打开
        self.w.set_expanded(True)
        owned = self.texts("owned")
        self.assertIn(text, owned)
        for name in ("星币锤", "维生素药丸"):
            self.assertIn(name, owned)

    def test_panel_read_with_unknown_slots(self):
        self.enter()
        self.panel(["星币锤"], unknown=1)
        self.assertIn("（另有 1 格无法识别", self.w.status.status_text())

    def test_panel_tab_asks_for_own_tab(self):
        self.enter()
        reading = SimpleNamespace(is_panel=True, self_tab=False, self_index=0, selected_tab=2,
                                  owned=[], unknown_slots=0, confidence=0.95)
        self.assertEqual(self.kinds(self.feed(self.session.on_panel, reading)), ["panel_tab"])
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertEqual(self.w.notice_model.texts().get("panel"), PANEL_TAB.format(n=1))
                self.assertTrue(self.w.card_open("owned"))
                self.assertIn(PANEL_TAB.format(n=1), self.texts("owned"))
                self.assertTrue(self.w.needs_attention())


class MapReadEventTest(BehaviorBase):
    """读到任务栏上的地图（map_read）：一次性提示「已识别地图……可以关闭任务栏」。"""

    def test_map_read_flashes_result(self):
        self.hud(1)
        self.hud(1)
        out = self.hud(None, map="异变图书馆", map_confidence="high",
                       map_candidates=["异变图书馆"], tasks_seen=["击倒魔法茶壶"])
        self.assertIn("map_read", self.kinds(out))
        text = MAP_READ.format(map="异变图书馆")
        for mode in self.modes():
            with self.subTest(mode=mode):
                self.assertTrue(self.w.status.summary_plain.startswith("已识别地图：异变图书馆"), self.w.status.summary_plain)
                self.assertIn(text, self.w.status.status_text())
        self.w.set_expanded(True)
        recog = self.texts("recog")
        self.assertIn(text, recog)
        self.assertIn("异变图书馆", recog)  # 地图下拉框显示读到的地图


class StatusEventTest(BehaviorBase):
    """status（只让悬浮窗重新同步 Session 状态、不改变三选一）：摘要跟着轮次变。"""

    def test_status_refreshes_summary(self):
        self.hud(3)
        self.hud(3)
        out = self.hud(4)
        self.assertIn("status", self.kinds(out))
        self.assertIn("第 4 轮", self.w.status.summary_plain)
        self.assertIn("第 4 轮", self.w.status.status_text())


if __name__ == "__main__":
    unittest.main()
