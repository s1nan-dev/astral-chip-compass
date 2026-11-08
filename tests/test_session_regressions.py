"""记账（advisor.session.Session）几处判定的回归测试，以及识别流程里交给记账的两处（接连两次三选一时读星数、
认出对局外画面以后再认一次）。

每条都是一串读数：按识别流程的顺序喂给 Session（假时钟，读数之间隔几秒），核对判没判新的一局、能不能撤销、地图和星数
怎么记、事件和存盘。识别流程的两条把识别函数换成假的，只看 FrameProcessor 什么时候读、读到的交给了谁。
推荐引擎用真的（只断言局面和事件，不断言推荐结果）。
"""
import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.session import Event, Session, Settings, _event_record
from advisor.vision import pipeline
from advisor.vision.hud import Hud, Pregame, Progress
from advisor.vision.screens import TEAM_ROOM, OutOfGame
from tests.support import sandbox

DB = load_db()
HERO = "远野汉娜"
OFFER_A = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]
OFFER_B = ["会员推荐信", "手电筒-强光", "优雅之羽"]
OFFER_C = ["拳击手套-初级", "摩托头盔-一般", "夹心饼干-一般"]
# 地图专属筹码：前三个只在星趴·梦想号出现，最后一个只在御魂庆典出现
DREAM, DREAM_2, DREAM_3 = "大铁锚", "三叉戟", "梦想号模型"
DREAM_MAP, LOADED_MAP = "星趴·梦想号", "御魂庆典"


def hud(rnd=None, map=None, **kw):
    return SimpleNamespace(round=rnd, map=map, **kw)


def own(stars, confidence=0.9):
    return SimpleNamespace(stars=stars, confidence=confidence)


def guess(hero):
    return SimpleNamespace(hero=hero)


def screen(kind, difficulty=None, map=None, rounds=None):
    return SimpleNamespace(kind=kind, difficulty=difficulty, map=map, rounds=rounds)


def offer(names, rerolls=2, probabilities=(60, 37, 3), selected=None, rarity=None, unrecorded=None, problems=(),
          raw=None):
    seen = SimpleNamespace(names=list(names), raw=list(raw) if raw is not None else [n or "？" for n in names],
                           rerolls=rerolls, probabilities=probabilities, selected=selected, problems=list(problems))
    if rarity is not None:
        seen.rarity = list(rarity)
    if unrecorded is not None:
        seen.unrecorded = list(unrecorded)
    return seen


def kinds(events) -> list:
    return [e.kind for e in events if e is not None]


class Clocked(unittest.TestCase):
    """新建一个角色已认出的 Session，挂上假时钟；tick 拨时钟。"""

    def setUp(self):
        self.t = 1000.0
        self.s = Session(DB, Settings(hero=HERO))
        self.s._clock = lambda: self.t
        self.s.on_hero(guess(HERO))

    def tick(self, seconds=3.0):
        self.t += seconds

    def rounds(self, last, first=1):
        """从第 first 轮一轮一轮走到第 last 轮，每轮读两次（隔 3 秒）。"""
        for r in range(first, last + 1):
            for _ in range(2):
                self.s.on_hud(hud(r))
                self.tick()

    def stars(self, n, times=2):
        out = []
        for _ in range(times):
            out.append(self.s.on_stars(own(n)))
        return out

    def heroes(self, name, times=2):
        return [self.s.on_hero(guess(name)) for _ in range(times)]

    def pick(self, names, slot=1):
        """一次三选一：看到、选中第 slot 个、界面关闭。"""
        self.s.on_frame(offer(names))
        self.s.on_frame(offer(names, selected=slot))
        self.s.on_frame(None)
        return self.s.on_frame(None)


# ====================================================================== 撤销的快照


class UndoTwiceTest(Clocked):
    """同一局里撤销过一次新一局以后，又撤销了另一种迹象判出的新一局：前一次撤销留下的「不再当成新一局的迹象」照样在。"""

    def test_hero_ignore_survives_second_undo(self):
        self.rounds(3)
        self.stars(1)
        self.tick(30)  # 超过互相印证的 20 秒
        self.assertIn("newgame", kinds(self.heroes("芬妮")))  # 立绘认错成芬妮：判了新的一局
        self.assertTrue(self.s.undo_new_game())  # 之后不再把芬妮当成新一局的迹象
        self.assertEqual(self.pick(OFFER_A).kind, "picked")
        self.tick(30)
        self.assertIn("newgame", kinds(self.stars(0)))  # 星数读错成 0：又判了一次
        self.assertTrue(self.s.undo_new_game())
        self.tick(30)
        self.assertEqual(kinds(self.heroes("芬妮")), [])  # 同一个认错的角色再出现：不再判新一局
        self.assertEqual((self.s.game.seen_hero, self.s.settings.owned), (HERO, [OFFER_A[1]]))

    def test_select_guard_survives_second_undo(self):
        self.rounds(1)
        self.stars(1)
        self.tick(30)
        self.assertEqual(self.s.on_select_screen().kind, "newgame")
        self.assertTrue(self.s.undo_new_game())  # 回到对局画面之前不再看选角界面
        self.assertIn("newgame", kinds(self.stars(0)))
        self.assertTrue(self.s.undo_new_game())
        self.tick(30)
        self.assertIsNone(self.s.on_select_screen())  # 还没回到对局画面：像选角界面的画面不再判新一局
        self.assertFalse(self.s.can_undo_new_game)


# ====================================================================== 轮次、星数的待定读数


class JumpTwiceTest(Clocked):
    """大幅跳变来的读数不算「确实到过」：从跳变来的值再跳一次时，前一个值照样不算。"""

    def test_round_jump_after_jump(self):
        self.rounds(3)
        for wrong in (13, 18):  # 静止画面上连着读错往前跳了两次，每次都连续读到两次
            for _ in range(2):
                self.s.on_hud(hud(wrong))
                self.tick()
        self.assertEqual(self.s.settings.round, 18)
        events = []
        for _ in range(2):  # 画面恢复，读到正确的第 4 轮
            events.append(self.s.on_hud(hud(4)))
            self.tick()
        self.assertEqual(kinds(events), ["status"])  # 改正读错，不是新的一局
        self.assertEqual(self.s.settings.round, 4)
        self.assertFalse(self.s.can_undo_new_game)

    def test_star_jump_after_jump(self):
        self.stars(0)
        for wrong in (2, 4):
            self.stars(wrong)
        self.assertEqual(kinds(self.stars(1)), ["status"])  # 改正读错
        self.assertEqual(self.s.settings.star, 1)
        self.assertFalse(self.s.can_undo_new_game)


class PendingWindowTest(Clocked):
    """「连续读到两次」：中间看过画面却没读到、又隔了 20 秒以上的两次同样的读错不算连续；中间根本没有读过的
    （手动模式两次按热键之间）照旧算。"""

    def test_round_reads_minutes_apart(self):
        self.rounds(12)
        self.s.on_hud(hud(3))  # 读错一次
        for _ in range(100):  # 之后 5 分钟只有读不到轮次的画面
            self.tick()
            self.assertIsNone(self.s.on_hud(hud(None)))
        self.assertIsNone(self.s.on_hud(hud(3)))  # 又读错一次：不算连续两次
        self.assertEqual(self.s.settings.round, 12)
        self.tick()
        self.assertEqual(self.s.on_hud(hud(3)).kind, "newgame")  # 20 秒内又读到：照常算两次

    def test_star_reads_minutes_apart(self):
        self.stars(2)
        self.s.on_stars(own(0))
        for _ in range(100):  # 玩家列表一直被挡住
            self.tick()
            self.assertIsNone(self.s.on_stars(None))
        self.assertIsNone(self.s.on_stars(own(0)))
        self.assertEqual(self.s.settings.star, 2)
        self.tick()
        self.assertEqual(self.s.on_stars(own(0)).kind, "newgame")

    def test_manual_presses_still_count(self):
        """手动模式：两次按热键之间没有读过画面，隔一分钟的两次照样算连续两次。"""
        self.rounds(12)
        self.s.on_hud(hud(3))
        self.tick(60)
        self.assertEqual(self.s.on_hud(hud(3)).kind, "newgame")


# ====================================================================== 三选一


class OfferKeyTest(Clocked):
    """同一组候选不重复给推荐，但资料库未收录的候选的稀有度（按它定中性分）变了要重算；已收录的边框颜色读得不稳不重算。"""

    NAMES = ["友情徽章", "某个新筹码", "小猪存钱罐"]

    def test_unrecorded_rarity_change_recomputes(self):
        first = self.s.on_frame(offer(self.NAMES, unrecorded=[False, True, False], rarity=["蓝", "紫", "蓝"]))
        self.assertEqual(first.kind, "advice")
        second = self.s.on_frame(offer(self.NAMES, unrecorded=[False, True, False], rarity=["蓝", "金", "蓝"]))
        self.assertIsNotNone(second)
        self.assertEqual(second.kind, "advice")
        totals = [{e.chip.name: e.total for e in ev.advice.evals} for ev in (first, second)]
        self.assertNotEqual(totals[0]["某个新筹码"], totals[1]["某个新筹码"])

    def test_known_rarity_flicker_ignored(self):
        self.assertEqual(self.s.on_frame(offer(OFFER_A, rarity=["蓝", "蓝", "紫"])).kind, "advice")
        self.assertIsNone(self.s.on_frame(offer(OFFER_A, rarity=["蓝", None, "紫"])))


BEFORE = ["星币锤", "维生素药丸", "摩托头盔-中级"]
MIDDLE = dict(names=[None, None, "优雅之羽"], raw=["高效", "2", "优雅"],
              problems=["第 1 个候选筹码无法识别（识别文字：「高效」）", "第 2 个候选筹码无法识别（识别文字：「2」）"])
MIDDLE_2 = dict(names=[None, "美工刀-锋利", None], raw=["", "美工刀-锋利", "放"],
                problems=["第 1 个候选筹码无法识别", "第 3 个候选筹码无法识别"])


def chips(names=None, raw=None, rerolls=2, problems=()):
    names = list(names)
    return SimpleNamespace(names=names, raw=list(raw if raw is not None else [n or "" for n in names]), rerolls=rerolls,
                           probabilities=(25, 50, 25), selected=None, problems=list(problems), team=False,
                           partial=[False] * len(names), reread=False)


class RefreshAgainTest(Clocked):
    """刷新后一直读不全、等满约 1 秒提示无法识别以后，同一次三选一里又点了一次刷新：新的中间帧照样标「正在刷新」。"""

    def at(self, t, seen):
        self.t = 2000.0 + t
        return self.s.on_frame(seen)

    def test_second_refresh_after_expired_wait(self):
        self.stars(3)
        self.assertEqual(self.at(0.0, chips(BEFORE, rerolls=5)).kind, "advice")
        self.assertEqual(self.at(1.0, chips(rerolls=4, **MIDDLE)).kind, "refreshing")
        self.assertEqual(self.at(1.55, chips(rerolls=4, **MIDDLE)).kind, "unreadable")  # 等满了
        ev = self.at(3.0, chips(rerolls=3, **MIDDLE_2))  # 又点了一次刷新（次数 4 → 3）
        self.assertEqual(ev.kind, "refreshing")
        self.assertTrue(self.s.holding)
        ev = self.at(3.6, chips(["星币锤", "美工刀-锋利", "放大镜"], rerolls=3))
        self.assertEqual(ev.kind, "advice")
        self.assertFalse(self.s.holding)

    def test_same_count_does_not_restart(self):
        """次数没变（还是同一次刷新的中间帧）：不再等，不重复提示。"""
        self.stars(3)
        self.at(0.0, chips(BEFORE, rerolls=5))
        self.at(1.0, chips(rerolls=4, **MIDDLE))
        self.assertEqual(self.at(1.55, chips(rerolls=4, **MIDDLE)).kind, "unreadable")
        self.assertIsNone(self.at(2.2, chips(rerolls=4, **MIDDLE)))
        self.assertFalse(self.s.holding)


class OfferKeyOwnershipTest(Clocked):
    """去重键只归三选一管：三选一以外的操作（局面没变）不让同一个画面重发一次推荐。"""

    def test_same_hero_same_map_no_duplicate(self):
        self.s.on_hud(hud(3))
        self.s.on_hud(hud(3))
        self.s.set_map("异变图书馆")
        self.assertEqual(self.s.on_frame(offer(OFFER_A)).kind, "advice")
        self.s.set_map("异变图书馆")  # 选的和现在的相同
        self.assertIsNone(self.s.on_frame(offer(OFFER_A)))
        self.s.set_hero(HERO)
        self.assertIsNone(self.s.recompute())
        self.s.set_owned(["书签"])  # 局面变了：照常重算
        self.assertEqual(self.s.on_frame(offer(OFFER_A)).kind, "advice")

    def test_changed_situation_recomputes(self):
        """换了角色、地图，交回自动识别后角色变回认出的那个：局面变了，同一个画面照样重新给推荐。"""
        self.s.on_hud(hud(3))
        self.s.on_hud(hud(3))
        self.assertEqual(self.s.on_frame(offer(OFFER_A)).kind, "advice")
        self.s.set_hero("芬妮")
        self.assertEqual(self.s.recompute().state.hero, "芬妮")
        self.s.set_map("异变图书馆")
        self.assertEqual(self.s.recompute().state.map, "异变图书馆")
        self.s.release_hero()
        self.assertEqual(self.s.recompute().state.hero, HERO)
        self.s.set_map(None)
        self.assertIsNone(self.s.recompute().state.map)

    def test_resend_after_new_game_and_undo(self):
        """判定新的一局、撤销新一局时悬浮窗清掉了显示着的三选一：局面没变（角色一直待识别）也重发一次。"""
        s = Session(DB, Settings(hero=HERO))  # 角色一直认不出来
        s._clock = lambda: self.t
        s.on_stars(own(2))
        s.on_stars(own(2))
        self.assertEqual(s.on_frame(offer(OFFER_A)).kind, "hero_needed")
        self.assertIsNone(s.on_frame(offer(OFFER_A)))
        s.on_stars(own(0), in_select=True)
        self.assertEqual(s.on_stars(own(0), in_select=True).kind, "newgame")
        self.assertEqual(s.on_frame(offer(OFFER_A)).kind, "hero_needed")
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.on_frame(offer(OFFER_A)).kind, "hero_needed")


class ConsecutiveSelectStarTest(Clocked):
    """两个任务接连完成、两次三选一之间没有棋盘画面：第二次三选一上读到的星数算这一次的（不被上一次的结算清掉）。"""

    def test_star_read_on_second_select_kept(self):
        self.stars(1)
        first = offer(OFFER_A, probabilities=(60, 37, 3), rarity=["蓝", "蓝", "紫"])
        self.s.on_stars(own(1), in_select=True)
        self.assertEqual(self.s.on_frame(first).state.source, "levelup")
        self.s.take_events()
        # 第二次三选一：概率行是 2 星那一行、最左格紫色。这一帧在三选一界面上读到的仍是 1 星：是 2 级任务，不是升到 2 星
        second = offer(OFFER_B, probabilities=(40, 45, 15), rarity=["紫", "蓝", "金"])
        self.s.on_stars(own(1), in_select=True)
        ev = self.s.on_frame(second)
        self.assertEqual(ev.kind, "advice")
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "task", 2))
        self.assertEqual(kinds(self.s.take_events()), ["missed_pick"])  # 上一次三选一照常结算

    def test_no_star_read_keeps_old_rule(self):
        """第二次三选一上没读到星数（自己那一行被挡住）：照旧按刚升星算，上一次三选一读到的星数不带过来。"""
        self.stars(1)
        self.s.on_stars(own(1), in_select=True)
        self.s.on_frame(offer(OFFER_A, probabilities=(60, 37, 3), rarity=["蓝", "蓝", "紫"]))
        ev = self.s.on_frame(offer(OFFER_B, probabilities=(40, 45, 15), rarity=["紫", "蓝", "金"]))
        self.assertEqual((ev.state.star, ev.state.source), (2, "levelup"))

    def test_another_select_query(self):
        """概率行一样时看候选和刷新次数：三张全换、次数少了 1 是刷新；三张全换、次数没少是另一次。"""
        self.assertFalse(self.s.starts_another_select(offer(OFFER_A)))  # 没有三选一显示着
        self.s.on_frame(offer(OFFER_A, probabilities=(60, 37, 3)))
        self.assertFalse(self.s.starts_another_select(offer(OFFER_A, probabilities=(60, 37, 3))))  # 同一个画面
        self.assertFalse(self.s.starts_another_select(offer(OFFER_B, rerolls=1, probabilities=(60, 37, 3))))  # 刷新
        self.assertTrue(self.s.starts_another_select(offer(OFFER_B, probabilities=(60, 37, 3))))  # 同一行的另一次
        self.assertTrue(self.s.starts_another_select(offer(OFFER_B, probabilities=(40, 45, 15))))  # 另一次
        self.assertFalse(self.s.starts_another_select(None))


class SameRowSelectsTest(Clocked):
    """两次三选一紧接着出现、概率行一样：三张候选全换了、刷新次数两边都确实读到而且
    没有减少，就是另一次三选一，和概率行换了的紧接着两次走同一条路——先结算上一次（记下绿框选中的、没看到的请用户补录），
    再开始这一次：推荐事件换成新的一次（推荐日志据此换编号）、补录和星币拆账的计数各算两次。拿不准的照旧当刷新。"""

    ROW = (60, 37, 3)  # 0、1 星的升星和商店、1 级任务都是这一行
    BLUE = ["蓝", "蓝", "紫"]  # 最左格是 1 星的固定稀有度：两次都按升星算（非任务，星币拆账要扣它们）

    def setUp(self):
        super().setUp()
        self.stars(1)
        self.s.take_events()

    def show(self, names, rerolls=2, **kw):
        """一帧三选一（概率行一样），返回（这一帧的主要事件, 先交出的上一次的结算 [(种类, 筹码)]）。"""
        kw.setdefault("rarity", self.BLUE)
        ev = self.s.on_frame(offer(names, rerolls=rerolls, probabilities=self.ROW, **kw))
        closed = [(e.kind, e.chip) for e in self.s.take_events() if e.kind in ("picked", "missed_pick", "idle")]
        return ev, closed

    def counts(self) -> tuple:
        """（上次在对局画面上读到星数以来出现过几次三选一, 这一局非任务三选一的次数, 星币拆账这一段的非任务三选一次数）。"""
        g = self.s.game
        return g._selects_since_read, g._game_opens, g._ledger_opens

    def test_new_select_records_first_pick(self):
        """概率行一样、三张全换、刷新次数一样：第一次绿框选中的记进已有筹码（picked），推荐事件换成新的一次，补录和星币拆账的
        计数各算两次，第二次照常结算。"""
        first, _ = self.show(OFFER_A, selected=1)
        self.assertEqual(first.kind, "advice")
        self.assertEqual(self.counts(), (1, 1, 1))
        ev, closed = self.show(OFFER_B)
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(closed, [("picked", OFFER_A[1])])  # 先结算上一次：记下绿框选中的
        self.assertEqual(self.s.settings.owned, [OFFER_A[1]])
        self.assertEqual(self.s.game.chip_sources, {OFFER_A[1]: "pick"})
        self.assertNotEqual(ev.select_no, first.select_no)  # 推荐日志据此换新编号
        self.assertEqual(ev.state.source, "levelup")
        self.assertEqual(self.counts(), (2, 2, 2))  # 补录、星币拆账都算两次
        self.assertEqual(self.s.game._open_read["names"], tuple(OFFER_B))  # 补认后重新判的依据换成这一次的
        self.show(OFFER_B, selected=0)
        self.s.on_frame(None)
        self.assertEqual(self.s.on_frame(None).kind, "picked")  # 第二次照常结算
        self.assertEqual(self.s.settings.owned, [OFFER_A[1], OFFER_B[0]])

    def test_new_select_without_green_box(self):
        """第一次没看到绿框：紧接着的另一次出现时请用户补录第一次（missed_pick），和概率行换了时一样。"""
        self.show(OFFER_A)
        ev, closed = self.show(OFFER_B, rerolls=3)  # 次数更多（例如升了一星）也算没有减少
        self.assertEqual((ev.kind, closed), ("advice", [("missed_pick", None)]))
        self.assertEqual(self.s.game.missed_offer, OFFER_A)
        self.assertEqual(self.s.settings.owned, [])

    def test_star_gain_matched_by_both(self):
        """星数一下加了 2、紧接着两次三选一概率行一样：两次都对上，之后在对局画面上读到星数也不提示补录（当成刷新时只对上
        一次，会提示补录 1 个）。"""
        self.stars(3)
        self.show(OFFER_A)
        self.show(OFFER_B)
        self.s.on_frame(None)
        self.s.on_frame(None)
        self.s.take_events()
        for _ in range(3):
            self.tick()
            self.s.on_stars(own(3))
        self.assertNotIn("unrecorded_gain", kinds(self.s.take_events()))
        self.assertIsNone(self.s.game._gain)

    def test_refresh_when_count_drops(self):
        """三张全换、刷新次数少了 1：照旧是同一次里刷新，绿框作废，不结算、不换成新的一次、不多算一次三选一。"""
        first, _ = self.show(OFFER_A, selected=1)
        ev, closed = self.show(OFFER_B, rerolls=1)
        self.assertEqual((ev.kind, closed), ("advice", []))
        self.assertEqual(ev.select_no, first.select_no)
        self.assertEqual(self.s.settings.owned, [])
        self.assertEqual(self.counts(), (1, 1, 1))
        self.s.on_frame(None)
        self.assertEqual(self.s.on_frame(None).kind, "missed_pick")  # 刷新后的这一组没看到绿框

    def unsure(self) -> dict:
        """拿不准的几种：{说明: (上一组那一帧和默认不同的读数, 紧接着的这一帧)}。上一组默认是 OFFER_A、读到 2 次、选中第 2 个。"""
        def seen(names=OFFER_B, **kw):
            return offer(names, probabilities=self.ROW, rarity=self.BLUE, **kw)

        reread = seen()
        reread.reread = True
        return {
            "这一帧刷新次数读不出": ({}, seen(rerolls=None)),
            "这一帧读成 0、上一组多于 0（刷新按钮可能被挡住）": ({}, seen(rerolls=0)),
            "上一组一直读不出刷新次数": ({"rerolls": None}, seen()),
            "只换了一个": ({}, seen([OFFER_A[0], OFFER_B[1], OFFER_A[2]])),
            "换了两个": ({}, seen([OFFER_B[0], OFFER_B[1], OFFER_A[2]])),
            "这一帧有一个没认出来": ({}, seen([None, OFFER_B[1], OFFER_B[2]])),
            "上一组有一个没认出来": ({"names": [OFFER_A[0], None, OFFER_A[2]]}, seen()),
            "只重查了绿框（沿用上一次完整识别的读数）": ({}, reread),
            "原文没变、只有名字变了（用户改正了候选）": ({}, seen(raw=OFFER_A)),
        }

    def test_unsure_stays_refresh(self):
        """拿不准的一律照旧当刷新：不结算上一次、绿框作废、仍是同一次三选一、不多算一次。识别流程问的
        （starts_another_select）和记账自己的判断一样。"""
        for label, (before, seen) in self.unsure().items():
            with self.subTest(label):
                self.setUp()
                self.show(**{"names": OFFER_A, "selected": 1, **before})
                number = self.s.select._select_no
                self.assertFalse(self.s.starts_another_select(seen))
                ev = self.s.on_frame(seen)
                closed = [e.kind for e in self.s.take_events() if e.kind in ("picked", "missed_pick", "idle")]
                self.assertEqual(closed, [])
                self.assertEqual(self.s.settings.owned, [])
                self.assertEqual(self.s.select._select_no, number)
                self.assertEqual(self.counts(), (1, 1, 1))
                if ev is not None and ev.kind == "advice":
                    self.assertEqual(ev.select_no, number)

    def test_bubble_on_last_frame_of_first(self):
        """上一组最后一帧刷新按钮被挡住（读成 0，推荐沿用刚才读到的 2 次）：比的是确实读到的 2 次，紧接着三张全换、读到 2 次
        的是另一次；读到 1 次的是刷新。"""
        for rerolls, closed_kinds in ((2, ["picked"]), (1, [])):
            with self.subTest(rerolls=rerolls):
                self.setUp()
                self.show(OFFER_A, selected=1)
                ev, _ = self.show(OFFER_A, rerolls=0, selected=1)
                self.assertIsNone(ev)  # 同一组候选：按沿用的 2 次算，局面没变
                self.assertEqual((self.s.select._offer_rerolls, self.s.select._offer_most), (2, 2))
                _, closed = self.show(OFFER_B, rerolls=rerolls)
                self.assertEqual([k for k, _ in closed], closed_kinds)

    def test_count_dropped_before_names(self):
        """点了刷新、新的候选还没换上的那一帧刷新次数已经少了 1（读到的还是上一组的名字）：按这一组确实读到过的多的次数比，
        换上来的一组次数少了，照旧是刷新（按最后读到的比会认成另一次）。"""
        self.show(OFFER_A, rerolls=3, selected=1)
        self.show(OFFER_A, rerolls=2, selected=1)
        self.assertEqual((self.s.select._offer_rerolls, self.s.select._offer_most), (2, 3))
        self.assertFalse(self.s.starts_another_select(offer(OFFER_B, probabilities=self.ROW, rarity=self.BLUE)))
        ev, closed = self.show(OFFER_B)
        self.assertEqual((ev.kind, closed, self.s.settings.owned), ("advice", [], []))

    def test_new_select_after_refresh(self):
        """同一次里刷新过（3 次 → 2 次）以后紧接着出现另一次（读到 2 次）：比的是刷新后这一组读到的次数，不带上刷新前那一组的，
        是另一次，记下刷新后这一组绿框选中的。"""
        self.show(OFFER_A, rerolls=3, selected=1)
        _, closed = self.show(OFFER_B, selected=0)
        self.assertEqual(closed, [])  # 刷新
        _, closed = self.show(OFFER_C)
        self.assertEqual(closed, [("picked", OFFER_B[0])])
        self.assertEqual(self.s.settings.owned, [OFFER_B[0]])

    def test_fix_keeps_count_read(self):
        """用户改正了候选（交给记账的是改正后的名字，原文没变），那一帧刷新按钮正被挡住（读成 0，推荐沿用 2 次）：画面上还是
        这一组，比的仍是确实读到过的 2 次；之后刷新成读到 1 次的一组照旧是刷新，不当成另一次、不记下绿框选中的。"""
        self.show(OFFER_A, selected=1)
        self.show(OFFER_A, rerolls=0, selected=1)
        self.show(["书签", *OFFER_A[1:]], rerolls=0, selected=1, raw=OFFER_A)  # 改正第 1 个
        self.assertEqual(self.s.select._offer_most, 2)
        ev, closed = self.show(OFFER_B, rerolls=1)
        self.assertEqual((ev.kind, closed, self.s.settings.owned), ("advice", [], []))

    def test_zero_then_fixed_not_rejudged(self):
        """已知局限：紧接着的另一次刚出现那一帧刷新按钮被挡住（读成 0、上一组 2 次）：照旧当刷新，第一次的绿框作废；补认读到
        2 次（同一组候选）以后也不改判成另一次。"""
        first, _ = self.show(OFFER_A, selected=1)
        _, closed = self.show(OFFER_B, rerolls=0)
        self.assertEqual(closed, [])
        ev, closed = self.show(OFFER_B)
        self.assertEqual((ev.kind, ev.state.rerolls, closed), ("advice", 2, []))
        self.assertEqual((ev.select_no, self.s.settings.owned), (first.select_no, []))

    def test_zero_before_counts(self):
        """上一组只读到过 0 次（次数用完时没有刷新按钮）：0 次刷不了，三张全换的就是另一次，这一帧读到几次都不算减少。"""
        for rerolls in (0, 2):
            with self.subTest(rerolls=rerolls):
                self.setUp()
                self.show(OFFER_A, rerolls=0, selected=1)
                _, closed = self.show(OFFER_B, rerolls=rerolls)
                self.assertEqual(closed, [("picked", OFFER_A[1])])

    def test_zero_after_refresh_unread(self):
        """刷新成的一组只完整识别一次，正赶上刷新按钮被挡住（读成 0，上一组确实读到过 2 次）：一次刷新到不了 0，这一组按没读到
        次数算；再刷新成三张全换的一组（读成 0，次数真用完了）照旧是刷新，不把这一组绿框点过、没确认的那张记进已有筹码。"""
        self.show(OFFER_A, selected=1)
        _, closed = self.show(OFFER_B, rerolls=0, selected=0)
        self.assertEqual(closed, [])  # 刷新
        self.assertIsNone(self.s.select._offer_most)
        nxt = offer(OFFER_C, rerolls=0, probabilities=self.ROW, rarity=self.BLUE)
        self.assertFalse(self.s.starts_another_select(nxt))  # 识别流程问的和记账的一样
        ev, closed = self.show(OFFER_C, rerolls=0)
        self.assertEqual((ev.kind, closed, self.s.settings.owned), ("advice", [], []))
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_zero_on_next_select_unread(self):
        """紧接着的另一次（概率行换了）刚出现那一帧刷新按钮被挡住（读成 0，上一组 2 次）：另一次也不会比上一组少，按没读到
        次数算；同一组后来读到 2 次（补认）照常记。"""
        self.show(OFFER_A, selected=1)
        self.s.on_frame(offer(OFFER_B, rerolls=0, probabilities=(40, 45, 15), rarity=self.BLUE))
        self.assertEqual([e.kind for e in self.s.take_events() if e.kind in ("picked", "missed_pick")], ["picked"])
        self.assertIsNone(self.s.select._offer_most)
        self.s.on_frame(offer(OFFER_B, probabilities=(40, 45, 15), rarity=self.BLUE))
        self.assertEqual(self.s.select._offer_most, 2)

    def test_zero_after_one_is_real(self):
        """上一组只读到过 1 次：刷新后读成 0 是真的用完了（没有刷新按钮），照旧记 0 次；之后三张全换的是另一次（0 次刷不了），
        记下这一组绿框选中的。"""
        self.show(OFFER_A, rerolls=1, selected=1)
        _, closed = self.show(OFFER_B, rerolls=0, selected=0)
        self.assertEqual(closed, [])  # 刷新
        self.assertEqual(self.s.select._offer_most, 0)
        _, closed = self.show(OFFER_C, rerolls=0)
        self.assertEqual(closed, [("picked", OFFER_B[0])])

    def test_zero_after_close_counts(self):
        """界面关掉以后的下一次三选一、换局以后的第一组不拿上一组的次数来比：刚出现读成 0 照旧记 0 次（之后三张全换的算另一次，
        见已知局限）。"""
        for label in ("界面关掉以后", "换局以后"):
            with self.subTest(label):
                self.setUp()
                self.show(OFFER_A, selected=1)
                if label == "界面关掉以后":
                    self.s.on_frame(None)
                    self.s.on_frame(None)
                else:
                    self.s.new_game()  # 三选一显示着时判了新的一局
                self.s.take_events()
                self.show(OFFER_B, rerolls=0, selected=0)
                self.assertEqual(self.s.select._offer_most, 0)
                _, closed = self.show(OFFER_C, rerolls=0)
                self.assertEqual(closed, [("picked", OFFER_B[0])])


# ====================================================================== 移出一个筹码


class RemoveChipTest(Clocked):
    """小卡片的「×」移出一个筹码（Session.remove_chip），和撤销移出（restore_chip）：放回以后和没移出时逐项相同；移出以后
    这一局和已有筹码有关的状态又变了时放不回。"""

    def test_only_this_chip(self):
        self.s.set_owned(["书签", "标靶"])
        self.s.on_frame(offer(OFFER_A))
        self.s.on_frame(None)
        self.assertEqual(self.s.on_frame(None).kind, "missed_pick")
        self.s.game.unrecorded = 1
        self.s.game.panel_needed = True
        self.assertTrue(self.s.remove_chip("标靶"))
        self.assertEqual(self.s.settings.owned, ["书签"])
        self.assertNotIn("标靶", self.s.game.chip_sources)
        # 补录行、待补录的数目、请打开面板的提示和它无关，照旧保留
        self.assertEqual((self.s.game.missed_offer, self.s.game.unrecorded, self.s.game.panel_needed), (OFFER_A, 1, True))
        self.assertFalse(self.s.remove_chip("标靶"))
        self.assertFalse(self.s.remove_chip("不存在的筹码"))

    def state_of(self) -> tuple:
        """撤销移出前后要逐项相同的：设置（已有筹码等）、这一局的对局状态（GameState 的每一项）、新一局的撤销点、局数。"""
        s = self.s
        return dataclasses.asdict(s.settings), dataclasses.asdict(s.game), s._undo, s.games

    def test_restore_puts_back_exactly(self):
        """「×」移出以后撤销：已有筹码的先后、记录来源、三选一里选中时的候选、不经三选一获得的记号、新一局的撤销点，以及
        这一局的其余对局状态，都和移出之前逐项相同；局面版本加了两次（移出、放回各一次，在锁外算的推荐作废）。只放回一次。"""
        s = self.s
        self.rounds(3)
        self.assertEqual(kinds([self.pick(OFFER_A, slot=1)]), ["picked"])  # 三选一选的：来源 pick，记着那一组候选
        self.assertTrue(s.add_chip("书签", "added"))  # 补录的
        self.assertTrue(s.add_chip("标靶", "added", direct=True))  # 不经三选一获得的
        self.assertTrue(s.add_chip("大背包", "added"))
        s._undo = undo = ("撤销点", "panel", None)  # 新一局的撤销点还在（按面板判的新一局，已有筹码来自面板）
        s.take_events()
        before = self.state_of()
        edits = s._edits
        self.assertTrue(s.remove_chip("标靶"))
        self.assertEqual(s.settings.owned, [OFFER_A[1], "书签", "大背包"])
        self.assertNotIn("标靶", s.game.direct)
        self.assertNotIn("标靶", s.game.chip_sources)
        self.assertIsNone(s._undo)  # 还剩筹码：新一局的撤销到此为止（同手改已有筹码）
        self.assertTrue(s.can_restore_chip)
        self.assertEqual(s.restore_chip(), "标靶")
        self.assertEqual(self.state_of(), before)
        self.assertIs(s._undo, undo)
        self.assertEqual(s._edits, edits + 2)
        self.assertFalse(s.can_restore_chip)
        self.assertIsNone(s.restore_chip())  # 只放回一次
        self.assertEqual(self.state_of(), before)

    def test_restore_first_and_last(self):
        """移出的是第一个、最后一个，移出以后一个都不剩：放回原来的位置。"""
        for chip, rest in (("书签", ["标靶", "大背包"]), ("大背包", ["书签", "标靶"])):
            with self.subTest(chip=chip):
                self.setUp()
                self.s.set_owned(["书签", "标靶", "大背包"])
                before = self.state_of()
                self.assertTrue(self.s.remove_chip(chip))
                self.assertEqual(self.s.settings.owned, rest)
                self.assertEqual(self.s.restore_chip(), chip)
                self.assertEqual(self.state_of(), before)
        self.setUp()
        self.s.set_owned(["书签"])
        before = self.state_of()
        self.assertTrue(self.s.remove_chip("书签"))
        self.assertEqual(self.s.settings.owned, [])
        self.assertEqual(self.s.restore_chip(), "书签")
        self.assertEqual(self.state_of(), before)

    def test_no_restore_after_other_changes(self):
        """移出以后这一局和已有筹码有关的状态又变了（又出现了三选一、新的一局、又记下或补录了筹码、手改了已有筹码、漏记了一次
        三选一、待补录变了、面板核对过、本局结束了）：放不回，restore_chip 什么都不改。"""
        cases = {
            "又出现了三选一": lambda s: s.on_frame(offer(OFFER_B)),
            "新的一局": lambda s: s.new_game(),
            "又记下了它": lambda s: s.add_chip("标靶", "added"),
            "补录了别的": lambda s: s.add_chip("大背包", "added"),
            # 手改成和移出以后一模一样的不算变了（放回去照样是没移出时的样子）：这里换了先后
            "手改了已有筹码": lambda s: s.set_owned(["友情徽章", "书签"]),
            "漏记了一次三选一": lambda s: setattr(s.game, "missed_offer", list(OFFER_C)),
            "待补录变了": lambda s: setattr(s.game, "unrecorded", 1),
            "面板核对过": lambda s: setattr(s.game, "_last_panel", (("书签", "友情徽章"), 0, "only", ("书签", "友情徽章"))),
            "本局结束了": lambda s: setattr(s.game, "game_over", True),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.setUp()
                s = self.s
                s.set_owned(["书签", "标靶", "友情徽章"])
                self.assertTrue(s.remove_chip("标靶"))
                change(s)
                s.take_events()
                after = self.state_of()
                self.assertFalse(s.can_restore_chip)
                self.assertIsNone(s.restore_chip())
                self.assertEqual(self.state_of(), after)

    def test_only_latest_removal(self):
        """又移出了别的：只放回最近移出的那一个，之前移出的不再放回。"""
        s = self.s
        s.set_owned(["书签", "标靶", "友情徽章"])
        self.assertTrue(s.remove_chip("标靶"))
        self.assertTrue(s.remove_chip("书签"))
        self.assertTrue(s.can_restore_chip)
        self.assertEqual(s.restore_chip(), "书签")
        self.assertEqual(s.settings.owned, ["书签", "友情徽章"])
        self.assertIsNone(s.restore_chip())

    def test_no_restore_after_undo_new_game(self):
        """新的一局里移出、又撤销了这次新一局的判定（换回判定前的那一局）：放不回。"""
        s = self.s
        s.set_owned(["书签"])
        self.assertEqual(s._auto_new_game("star", 0, "星数由 1 星减少为 0 星").kind, "newgame")
        s.settings.owned.append("标靶")  # 新的一局里记着一个（不经记账：撤销点还在）
        self.assertTrue(s.remove_chip("标靶"))
        self.assertTrue(s.can_undo_new_game)
        self.assertTrue(s.undo_new_game())
        self.assertFalse(s.can_restore_chip)
        self.assertIsNone(s.restore_chip())
        self.assertEqual(s.settings.owned, ["书签"])

    def test_restore_while_same_offer_open(self):
        """三选一显示着时移出：同一次三选一里（候选没换、界面没关）照样能放回；放回以后下一帧按放回的已有筹码重算推荐。"""
        s = self.s
        self.rounds(3)
        s.set_owned(["书签", "标靶"])
        self.assertEqual(kinds([s.on_frame(offer(OFFER_A))]), ["advice"])
        self.assertTrue(s.remove_chip("标靶"))
        ev = s.on_frame(offer(OFFER_A))
        self.assertEqual((ev.kind, ev.state.owned), ("advice", ("书签",)))
        self.assertTrue(s.can_restore_chip)
        self.assertEqual(s.restore_chip(), "标靶")
        ev = s.on_frame(offer(OFFER_A))
        self.assertEqual((ev.kind, ev.state.owned), ("advice", ("书签", "标靶")))

    def test_panel_prompt_active(self):
        self.assertFalse(self.s.panel_prompt_active)  # 还没进入对局：没提示过
        self.s.on_hud(hud(3))
        self.assertIn("panel_prompt", kinds(self.s.take_events()))
        self.assertTrue(self.s.panel_prompt_active)
        self.s.skip_panel()
        self.assertFalse(self.s.panel_prompt_active)


# ====================================================================== 存盘


class SaveWhenChangedTest(unittest.TestCase):
    """只在要存的设置真的变了时才存：记账里改的多是对局状态（不写进设置文件）。"""

    def test_saves(self):
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            s = Session(DB, Settings(hero=HERO), Path(tmp) / "state.json")
            calls = []
            real = Settings.save

            def counted(settings, path, **kwargs):
                calls.append(settings.hero)
                return real(settings, path, **kwargs)

            with mock.patch.object(Settings, "save", autospec=True, side_effect=counted):
                s.save()
                self.assertEqual(len(calls), 1)  # 第一次照常存
                s.on_hud(hud(3))
                s.on_hud(hud(3))
                s.on_stars(own(1))
                s.set_owned(["书签"])
                s.set_map("异变图书馆")
                s.new_game()
                s.save()
                self.assertEqual(len(calls), 1)  # 只改了对局状态
                s.on_hero(guess("芬妮"))  # 认出了角色：角色要存
                self.assertEqual(calls, [HERO, "芬妮"])
                s.settings.window_pos = [10, 20]
                s.save()
                s.settings.window_pos[0] = 30  # 原地改的也看得出
                s.save()
                s.save()
                self.assertEqual(len(calls), 4)
            self.assertIn('"hero": "芬妮"', (Path(tmp) / "state.json").read_text(encoding="utf-8"))

    def test_failed_save_retried(self):
        """Settings.save 返回 False（没写成）：不记下这次的内容，内容没变也再试；返回 True、None 算写成，存盘位置不变。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            path = Path(tmp) / "state.json"
            s = Session(DB, Settings(hero=HERO), path)
            results = iter([False, False, True, None])
            calls = []

            def fake(settings, target, **kwargs):
                calls.append(target)
                return next(results)

            with mock.patch.object(Settings, "save", autospec=True, side_effect=fake):
                s.save()
                s.save()  # 上一次没写成：内容没变也再试
                s.save()  # 这一次写成了
                s.save()  # 内容没变：不再存
                self.assertEqual(len(calls), 3)
                s.settings.window_pos = [1, 2]
                s.save()
                s.save()
                self.assertEqual(len(calls), 4)
            self.assertEqual((calls, s.path), ([path] * 4, path))


# ====================================================================== 识别记录、事件序号


class BrokenRecorder:
    """识别记录器出错（写不成 UTF-8 的字等）：每个方法都抛出。"""

    def add(self, t, record):
        raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")

    def new_segment(self, reason, **info):
        raise RuntimeError("坏了")

    def undo_segment(self):
        raise RuntimeError("坏了")

    def mark(self, kind, **info):
        raise RuntimeError("坏了")


class RecorderErrorTest(Clocked):
    def test_recorder_errors_do_not_stop_ledger(self):
        self.s.eventlog.recorder = BrokenRecorder()
        with self.assertLogs("advisor.session", level="WARNING") as logs:
            self.s.note([Event("status")])
            self.s.trace("hud", round=3)
            self.rounds(7)
            self.s.on_hud(hud(1))
            ev = self.s.on_hud(hud(1))
            self.assertEqual((ev.kind, ev.undo), ("newgame", True))
            self.assertTrue(self.s.undo_new_game())
            self.assertEqual(self.s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 7)).kind, "game_over")
        self.assertEqual(len(logs.records), 1)  # 只在第一次出错时记一条
        self.assertEqual([r["kind"] for r in self.s.recent_events(60)][:2], ["status", "read:hud"])
        self.assertEqual(self.s.settings.round, 7)


class EventSeqTest(Clocked):
    """Session 发出的每个事件带递增的序号（悬浮窗据此认出晚到的旧推荐），诊断记录里也写上。"""

    def test_increasing(self):
        s = Session(DB, Settings(hero=HERO))
        first = s.on_hero(guess(HERO))
        self.assertEqual(first.seq, 1)  # 每个 Session 从 1 数起
        status = s.on_hud(hud(3))
        pushed = s.take_events()  # 进入对局：请用户打开「查看筹码」面板
        advice = s.on_frame(offer(OFFER_A))
        seqs = [first.seq, status.seq, *(e.seq for e in pushed), advice.seq]
        self.assertEqual(len(set(seqs)), len(seqs))
        self.assertTrue(pushed)
        self.assertTrue(all(e.seq > first.seq for e in (status, *pushed)))
        self.assertGreater(advice.seq, max(status.seq, *(e.seq for e in pushed)))  # 后发生的序号大
        self.assertIsNone(s.recompute())  # 画面没变：不发
        s.set_owned(["书签"])
        again = s.recompute()
        self.assertGreater(again.seq, advice.seq)
        self.assertEqual(_event_record(again)["seq"], again.seq)
        self.assertNotIn("seq", _event_record(Event("status")))  # Session 以外建的事件没有序号

    def test_select_no_in_record(self):
        """推荐事件带记账里的第几次三选一（select_no），诊断记录（最近事件、识别记录）里也写上；别的事件、Session 以外建的
        事件没有这一项。"""
        s = self.s
        self.stars(1)
        first = s.on_frame(offer(OFFER_A))
        s.on_frame(None)
        closed = s.on_frame(None)
        second = s.on_frame(offer(OFFER_B))
        self.assertEqual((first.select_no, second.select_no), (1, 2))
        s.note([first, closed, second])
        recs = [r for r in s.recent_events(60) if r["kind"] in ("advice", closed.kind)]
        self.assertEqual([(r["kind"], r.get("select_no")) for r in recs],
                         [("advice", 1), (closed.kind, None), ("advice", 2)])
        self.assertNotIn("select_no", _event_record(Event("advice")))

    def test_undo_keeps_counting(self):
        self.rounds(7)
        self.s.on_hud(hud(1))
        newgame = self.s.on_hud(hud(1))
        self.s.undo_new_game()
        self.s.on_hud(hud(8))
        self.assertGreater(self.s.on_hud(hud(9)).seq, newgame.seq)


# ====================================================================== 对局外画面结束本局


class OutOfGameTwiceTest(Clocked):
    """对局状态还在时，房间、「匹配成功」画面、奖励画面要隔一会儿（同一帧上认出两次只算一次）、20 秒内连续两次读到
    （中间没有对局内的读数）才结束本局；结算画面照旧读到一次就结束。之后开的新一局：结算画面结束的不能撤销，其余的能撤销，
    撤销时连同「已结束」一起撤回。"""

    ROOM = screen("team_room", "噩梦")

    def playing(self):
        """第 8 轮、2 星、进度 9/17，记下一个筹码。"""
        self.rounds(8)
        self.stars(2)
        self.s.on_progress(9, 17)
        self.s.on_progress(9, 17)
        self.assertEqual(self.pick(OFFER_A).kind, "picked")
        self.s.take_events()

    def test_single_reading_does_not_end(self):
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(self.ROOM))
        self.assertFalse(self.s.game.game_over)
        self.assertEqual(self.s.next_star_cost(), 25)  # 还在记这一局（星数照常）
        self.tick()
        self.s.on_hud(hud(8))  # 回到了对局画面：上一次读数作废
        self.tick()
        self.assertIsNone(self.s.on_out_of_game(self.ROOM))
        self.tick()
        ev = self.s.on_out_of_game(self.ROOM)
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到组队房间"))
        self.assertTrue(self.s.game.game_over)

    def test_same_frame_counts_once(self):
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(self.ROOM))
        self.tick(0.2)  # 同一帧画面上整幅识别又认出一次
        self.assertIsNone(self.s.on_out_of_game(self.ROOM))
        self.assertFalse(self.s.game.game_over)
        self.tick(2.8)
        self.assertEqual(self.s.on_out_of_game(self.ROOM).kind, "game_over")

    def test_readings_too_far_apart(self):
        self.playing()
        self.s.on_out_of_game(self.ROOM)
        self.tick(25)
        self.assertIsNone(self.s.on_out_of_game(self.ROOM))  # 隔得太久：重新算第一次
        self.tick(3)
        self.assertEqual(self.s.on_out_of_game(self.ROOM).kind, "game_over")

    def test_different_screens_count(self):
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(screen("reward")))
        self.tick()
        ev = self.s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到「匹配成功」画面"))

    def test_result_once(self):
        self.playing()
        self.assertEqual(self.s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 8)).kind, "game_over")

    def end_by_room(self):
        self.playing()
        self.s.on_out_of_game(self.ROOM)
        self.tick()
        self.assertEqual(self.s.on_out_of_game(self.ROOM).kind, "game_over")
        self.s.take_events()

    def test_next_game_by_round_undoable(self):
        """误认出的房间结束了本局，同一局接着打（第 8 轮连续读到两次）：新的一局可以撤销，撤销后接着记这一局。"""
        self.end_by_room()
        self.tick()
        self.assertIsNone(self.s.on_hud(hud(8)))
        self.tick()
        ev = self.s.on_hud(hud(8))
        self.assertEqual((ev.kind, ev.undo, ev.cause, ev.why), ("newgame", True, "round", "上一局已结束，检测到第 8 轮的对局画面"))
        self.assertTrue(self.s.can_undo_new_game)
        self.assertEqual(self.s.settings.owned, [])
        self.assertTrue(self.s.undo_new_game())
        st = self.s.settings
        self.assertEqual((st.owned, st.round, st.star, self.s.game.progress_cap), ([OFFER_A[1]], 8, 2, 17))
        self.assertFalse(self.s.game.game_over)
        self.assertIsNone(self.s.game_over_info)
        self.tick()
        self.assertEqual(self.s.on_stars(own(3)).kind, "status")  # 照常记这一局
        self.assertEqual(self.pick(OFFER_B).kind, "picked")

    def test_next_game_by_select_not_undoable(self):
        """房间画面结束本局以后看到选角界面：选角界面只在开局时出现，确实开了新的一局，不存快照、不可撤销。"""
        self.end_by_room()
        ev = self.s.on_select_screen()
        self.assertEqual((ev.kind, ev.undo, ev.cause), ("newgame", False, "select"))
        self.assertFalse(self.s.can_undo_new_game)
        self.assertFalse(self.s.undo_new_game())
        self.assertEqual(self.s.settings.owned, [])
        self.assertFalse(self.s.game.game_over)
        self.assertIsNone(self.s.on_select_screen())  # 选角界面的后续几帧：不再判一次

    def test_next_game_by_offer_undoable(self):
        self.end_by_room()
        ev = self.s.begin_offer()
        self.assertEqual((ev.kind, ev.undo, ev.cause), ("newgame", True, "offer"))
        self.assertTrue(self.s.can_undo_new_game)

    def test_next_game_after_result_not_undoable(self):
        self.playing()
        self.s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 8))
        ev = self.s.on_select_screen()
        self.assertEqual((ev.kind, ev.undo), ("newgame", False))
        self.assertFalse(self.s.can_undo_new_game)

    def test_undo_restores_prompts(self):
        """误认的房间结束本局时收起的提示，撤销之后开的新一局时放回：漏记补录、待补录、「查看筹码」面板提示、
        还在宽限次数里的那次升星，以及刚关掉的那组候选（同一组又出现时照样撤销刚记下的筹码）。"""
        self.playing()
        self.s.on_frame(offer(OFFER_B, probabilities=(40, 45, 15)))
        self.s.on_frame(None)
        self.assertEqual(self.s.on_frame(None).kind, "missed_pick")
        self.assertEqual(self.pick(OFFER_C).kind, "picked")
        for n in (2, 3, 3, 3):  # 升了一星，却一直没有对应的三选一：提示补录
            self.s.on_stars(own(n))
        self.assertIn("unrecorded_gain", kinds(self.s.take_events()))
        self.s.on_stars(own(4))  # 又升一星，还在宽限的次数里
        before = (self.s.game.missed_offer, self.s.game.unrecorded, self.s.game.panel_needed, list(self.s.settings.owned))
        self.assertEqual(before[:3], (OFFER_B, 1, True))
        self.s.on_out_of_game(self.ROOM)
        self.tick()
        self.assertEqual(self.s.on_out_of_game(self.ROOM).kind, "game_over")
        self.assertEqual((self.s.game.missed_offer, self.s.game.unrecorded, self.s.game.panel_needed), (None, 0, False))
        self.tick()
        self.s.on_hud(hud(8))
        self.tick()
        self.s.on_hud(hud(8))
        self.assertTrue(self.s.undo_new_game())
        self.assertEqual((self.s.game.missed_offer, self.s.game.unrecorded, self.s.game.panel_needed, self.s.settings.owned), before)
        self.assertTrue(self.s.panel_prompt_active)
        self.s.take_events()
        self.s.on_stars(own(4))
        self.s.on_stars(own(4))
        self.assertIn("unrecorded_gain", kinds(self.s.take_events()))
        self.assertEqual(self.s.game.unrecorded, 2)
        self.s.on_frame(offer(OFFER_C))
        self.assertIn("pick_reverted", kinds(self.s.take_events()))

    def test_voided_reading_takes_back_next_game_readings(self):
        """只读到一次（误认）就回到了对局画面：那一次连同同一帧上又认出的，留给下一局的难度和地图一起撤回。"""
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(screen("create_room", "疯狂", "水乡古镇")))
        self.tick(0.2)
        self.assertIsNone(self.s.on_out_of_game(screen("match_found", "噩梦", DREAM_MAP)))  # 同一帧的整幅识别
        self.tick()
        self.s.on_hud(hud(8))
        self.assertEqual((self.s.pending._next_difficulty, self.s.pending._next_map), ({}, None))
        self.tick()
        self.s.on_out_of_game(screen("result", None, None, 9))
        self.assertEqual(self.s.on_select_screen().kind, "newgame")
        self.assertEqual(self.s.game.difficulty_evidence, {})  # 下一局不用误认的那两次读到的难度
        self.s.on_hud(hud(1))
        self.assertIsNone(self.s.settings.map)

    def test_undo_takes_back_next_game_readings(self):
        """撤销房间等画面结束以后开的新一局：结束那一下（和之前待确认的那一次）读到、留给下一局的难度和地图一起撤回。"""
        self.playing()
        self.s.on_out_of_game(screen("create_room", "疯狂", "水乡古镇"))
        self.tick()
        self.assertEqual(self.s.on_out_of_game(screen("match_found", "噩梦", DREAM_MAP)).kind, "game_over")
        self.tick()
        self.s.on_hud(hud(8))
        self.tick()
        self.s.on_hud(hud(8))
        self.assertEqual(self.s.settings.map, DREAM_MAP)  # 新的一局用上了「匹配成功」画面上的地图
        self.assertTrue(self.s.undo_new_game())
        self.assertEqual((self.s.pending._next_difficulty, self.s.pending._next_map), ({}, None))
        self.assertIsNone(self.s.settings.map)

    def test_result_after_room_end(self):
        """房间等画面结束了本局，随后又读到结算画面：确实打完了，之后的新一局不能撤销，依据改成结算画面。"""
        self.end_by_room()
        self.tick()
        self.assertIsNone(self.s.on_out_of_game(screen("result", None, "异变图书馆", 9)))
        info = self.s.game_over_info
        self.assertEqual((info["why"], info["rounds"], info["map"]), ("检测到结算画面（异变图书馆、9 轮）", 9, "异变图书馆"))
        self.assertEqual(kinds(self.s.take_events()), ["status"])  # 难度没变也发一次：悬浮窗按新的依据重画
        ev = self.s.on_select_screen()
        self.assertEqual((ev.kind, ev.undo), ("newgame", False))
        self.assertFalse(self.s.can_undo_new_game)

    def test_undo_restores_and_can_end_again(self):
        """撤销恢复结束前的地图、难度和角色；撤销以后又误认两次、又开局，照样能撤销；最后读到结算画面结束，之后的新一局
        不能撤销。"""
        self.s.on_loading("异变图书馆")
        self.playing()
        before = (self.s.settings.map, self.s.map_source, self.s.difficulty_info, self.s.game.seen_hero)
        self.s.on_out_of_game(self.ROOM)
        self.tick()
        self.s.on_out_of_game(self.ROOM)
        for _ in range(2):
            self.tick()
            self.s.on_hud(hud(8))
            self.tick()
            ev = self.s.on_hud(hud(8))
            self.assertEqual((ev.kind, ev.undo), ("newgame", True))
            self.assertTrue(self.s.undo_new_game())
            st = self.s.settings
            self.assertEqual((self.s.game.game_over, st.owned, st.round, st.star), (False, [OFFER_A[1]], 8, 2))
            self.assertEqual((st.map, self.s.map_source, self.s.difficulty_info, self.s.game.seen_hero), before)
            self.tick()
            self.assertIsNone(self.s.on_out_of_game(self.ROOM))
            self.tick()
            self.assertEqual(self.s.on_out_of_game(self.ROOM).kind, "game_over")
        self.tick()
        self.s.on_hud(hud(8))
        self.tick()
        self.s.on_hud(hud(8))
        self.assertTrue(self.s.undo_new_game())
        self.tick()
        self.assertEqual(self.s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 8)).kind, "game_over")
        ev = self.s.on_select_screen()
        self.assertEqual((ev.kind, ev.undo), ("newgame", False))
        self.assertFalse(self.s.can_undo_new_game)


class MainMenuTest(Clocked):
    """对局中途回到主菜单（中途退出、断线）：和房间一样，对局状态还在时隔一会儿、20 秒内连续两次读到才结束本局，依据
    「检测到主菜单」；只读到一次就回到了对局画面的不算。之后连续两次读到的轮次不比离开时小、中间没读到别的对局外画面、
    离开不到 REJOIN_MAX_S（断线以后连回了同一局）时不开新的一局：撤回那次结束、接着记（status 的 cause 为 rejoined，
    识别记录 read:rejoined）；之后认出别的角色照常判新的一局。读到了房间等别的画面、轮次比离开时小、离开太久的照常开新的一局。"""

    MENU = screen("main_menu")

    def playing(self, last=6):
        """打到第 last 轮、2 星、进度 last+1/17，记下一个筹码。"""
        self.rounds(last)
        self.stars(2)
        self.s.on_progress(last + 1, 17)
        self.s.on_progress(last + 1, 17)
        self.assertEqual(self.pick(OFFER_A).kind, "picked")
        self.s.take_events()

    def end_by_menu(self, last=6):
        self.playing(last)
        self.assertIsNone(self.s.on_out_of_game(self.MENU))
        self.tick(1.5)
        ev = self.s.on_out_of_game(self.MENU)
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到主菜单"))
        self.s.take_events()

    def round_twice(self, rnd, gap=16.0):
        """离开 gap 秒以后连续两次读到第 rnd 轮（隔 3 秒），返回两次的结果。"""
        self.tick(gap)
        first = self.s.on_hud(hud(rnd))
        self.tick()
        return first, self.s.on_hud(hud(rnd))

    def records(self, kind):
        return [r for r in self.s.recent_events(600) if r["kind"] == kind]

    def test_menu_twice_ends_game(self):
        """第 6 轮时隔 1.5 秒两次读到主菜单：本局结束，依据「检测到主菜单」；主菜单上没有难度和地图，不留给下一局。"""
        self.end_by_menu()
        self.assertTrue(self.s.game.game_over)
        self.assertEqual(self.s.game_over_info["why"], "检测到主菜单")
        self.assertEqual(self.s.game_over_info["rounds"], 6)
        self.assertEqual((self.s.pending._next_difficulty, self.s.pending._next_map), ({}, None))
        self.assertEqual(self.records("read:game_over")[-1]["why"], "检测到主菜单")

    def test_single_menu_does_not_end(self):
        """只读到一次主菜单就回到了对局画面：不算结束，那一次作废；之后再读到一次也还只是第一次。"""
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(self.MENU))
        self.tick()
        self.assertIsNone(self.s.on_hud(hud(6)))
        self.tick()
        self.assertIsNone(self.s.on_out_of_game(self.MENU))
        self.assertFalse(self.s.game.game_over)
        self.assertEqual(self.s.settings.owned, [OFFER_A[1]])

    def test_rejoin_keeps_game(self):
        """主菜单结束（第 6 轮）以后连续两次读到第 7 轮：同一局接着记，筹码、星数、进度上限都在，没有新的一局；
        之后照常记筹码。"""
        self.end_by_menu()
        first, ev = self.round_twice(7)
        self.assertIsNone(first)
        self.assertEqual((ev.kind, ev.cause), ("status", "rejoined"))
        self.assertNotIn("newgame", kinds(self.s.take_events()))
        st = self.s.settings
        self.assertEqual((st.owned, st.round, st.star, self.s.game.progress_cap), ([OFFER_A[1]], 7, 2, 17))
        self.assertFalse(self.s.game.game_over)
        self.assertIsNone(self.s.game_over_info)
        self.assertFalse(self.s.can_undo_new_game)
        self.assertEqual(self.s.games, 0)
        rec = self.records("read:rejoined")
        self.assertEqual([(r["left"], r["round"], r["seconds"]) for r in rec], [(6, 7, 19)])
        self.assertEqual(_event_record(ev)["cause"], "rejoined")
        self.tick()
        self.assertIsNone(self.s.on_hud(hud(7)))  # 第 7 轮照常
        self.assertEqual(self.pick(OFFER_B).kind, "picked")
        self.assertEqual(self.s.settings.owned, [OFFER_A[1], OFFER_B[1]])

    def test_rejoin_then_other_hero(self):
        """接着记以后认出了别的角色（其实是开了新的一局，房间和选角界面都没截到）：照常判新的一局，刚接回来时不拿轮次
        否决它；认回这一局的角色以后，轮次照旧否决认错的角色。"""
        self.end_by_menu()
        self.round_twice(7)
        self.tick()
        ev = self.heroes("凛")[-1]
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "hero", True))
        self.assertEqual(self.s.settings.owned, [])
        self.assertTrue(self.s.undo_new_game())
        self.assertEqual(self.s.settings.owned, [OFFER_A[1]])

        self.setUp()
        self.end_by_menu()
        self.round_twice(7)
        self.heroes(HERO, times=1)  # 认回了这一局的角色
        self.tick()
        self.assertEqual(self.heroes("凛"), [None, None])  # 第 7 轮还在：按认错处理
        self.assertEqual(self.s.settings.owned, [OFFER_A[1]])

    def test_room_after_menu_starts_new_game(self):
        """主菜单结束以后读到了组队房间（确实离开了那一局），再连续两次读到第 1 轮：新的一局，从开局看起，不请用户打开
        「查看筹码」面板。"""
        self.end_by_menu()
        self.tick()
        self.assertIsNone(self.s.on_out_of_game(screen("team_room", "噩梦")))
        first, ev = self.round_twice(1)
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "round", True))
        self.assertEqual(self.s.settings.owned, [])
        self.assertFalse(self.s.game.panel_needed)
        self.assertTrue(self.s.game.whole_game)
        self.assertNotIn("panel_prompt", kinds(self.s.take_events()))
        self.assertEqual(self.records("read:rejoined"), [])

    def test_room_then_menu_not_rejoined(self):
        """第 6 轮时先认出一次组队房间、1.5 秒后认出主菜单：按主菜单结束本局，但先认出的那一次是房间（结束之前已经离开过
        这一局），之后连续两次读到离开时的第 6 轮也不接着记，开新的一局（可撤销）。"""
        self.playing()
        self.assertIsNone(self.s.on_out_of_game(screen("team_room", "噩梦")))
        self.tick(1.5)
        ev = self.s.on_out_of_game(self.MENU)
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到主菜单"))
        self.s.take_events()
        first, ev = self.round_twice(6)
        self.assertIsNone(first)
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "round", True))
        self.assertEqual(self.s.settings.owned, [])
        self.assertEqual(self.records("read:rejoined"), [])

    def test_round1_menu_rejoins(self):
        """第 1 轮就回到主菜单，没截到房间和选角界面又读到两次第 1 轮：当成同一局接着记（已知代价：第 1 轮退出、马上开了
        新的一局时会这样，新一局开头的筹码记进上一局；第 1 轮最多拿一两个筹码，之后认出别的角色照常判新的一局）。"""
        self.end_by_menu(last=1)
        first, ev = self.round_twice(1)
        self.assertEqual((ev.kind, ev.cause), ("status", "rejoined"))
        self.assertEqual(self.s.settings.owned, [OFFER_A[1]])
        self.assertFalse(self.s.game.game_over)

    def test_lower_round_starts_new_game(self):
        """主菜单结束时第 6 轮，之后连续两次读到第 2 轮：比离开时小，是新的一局，从开局看起、不请用户打开面板。"""
        self.end_by_menu()
        first, ev = self.round_twice(2)
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "round", True))
        self.assertEqual(self.s.settings.owned, [])
        self.assertFalse(self.s.game.panel_needed)
        self.assertTrue(self.s.game.whole_game)
        self.assertNotIn("panel_prompt", kinds(self.s.take_events()))

    def test_too_late_starts_new_game(self):
        """离开超过 REJOIN_MAX_S（同推荐日志两次三选一隔这么久就算新一局的 GAME_GAP）：照常开新的一局。"""
        from advisor import logbook
        from advisor.session import offgame

        self.assertEqual(offgame.REJOIN_MAX_S, logbook.GAME_GAP.total_seconds())
        self.end_by_menu()
        first, ev = self.round_twice(7, gap=offgame.REJOIN_MAX_S + 1)
        self.assertEqual((ev.kind, ev.cause), ("newgame", "round"))
        self.assertTrue(self.s.game.panel_needed)  # 第 7 轮：开局后可能已经拿过筹码

    def test_result_after_menu_not_rejoined(self):
        """主菜单结束以后又读到了结算画面（那一局确实打完了）：之后的轮次不再接着记，开新的一局（不可撤销）。"""
        self.end_by_menu()
        self.tick()
        self.assertIsNone(self.s.on_out_of_game(screen("result", None, None, 6)))
        first, ev = self.round_twice(7)
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "round", False))

    def test_menu_cancels_match_found(self):
        """读到「匹配成功」画面以后回到了主菜单：那次匹配没有开始，它上面的难度和地图作废（同房间）。"""
        self.end_by_menu()
        self.s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
        self.assertEqual(self.s.pending._next_map, ("水乡古镇", "match"))
        self.s.on_out_of_game(self.MENU)
        self.assertEqual((self.s.pending._next_difficulty, self.s.pending._next_map), ({}, None))
        self.assertTrue(self.records("read:match_cancelled"))


# ====================================================================== 地图专属筹码和加载画面的地图冲突


class ChipMapConflictTest(Clocked):
    """加载画面（「匹配成功」画面、房间设置）读到的地图和地图专属筹码推出的地图冲突时：专属筹码拿到了手（三选一记下、
    面板读到），或者两次不同的三选一都指向同一张地图，才改；同一组候选在画面上停多少帧都只算一次。"""

    def setUp(self):
        super().setUp()
        self.s.on_loading(LOADED_MAP)
        self.rounds(3)
        self.s.take_events()

    def map(self):
        return self.s.settings.map, self.s.map_source

    def test_needs_second_offer(self):
        first = [DREAM, OFFER_A[1], OFFER_A[2]]
        for _ in range(5):
            self.s.on_frame(offer(first))
        self.assertEqual(self.map(), (LOADED_MAP, "loading"))
        self.assertEqual(self.s.on_frame(offer(first)), None)
        traced = [r for r in self.s.recent_events(60) if r["kind"] == "read:map_conflict"]
        self.assertEqual(len(traced), 1)  # 冲突只在第一次记一条识别读数
        self.s.on_frame(None)
        self.s.on_frame(None)
        self.s.on_frame(offer([OFFER_B[0], DREAM_2, OFFER_B[2]]))  # 另一组候选也指向梦想号
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_refresh_counts(self):
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]], rerolls=2))
        self.assertEqual(self.map(), (LOADED_MAP, "loading"))
        self.s.on_frame(offer([OFFER_B[0], OFFER_B[1], DREAM_3], rerolls=1))  # 刷新后的候选里又有一个
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_picked_chip(self):
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]], selected=0))
        self.s.on_frame(None)
        self.assertEqual(self.s.on_frame(None).chip, DREAM)
        self.assertEqual(self.map()[0], LOADED_MAP)
        self.s.on_frame(offer(OFFER_C))  # 下一次三选一：已有筹码里的专属筹码就对不上
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_panel_read(self):
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.s.on_frame(None)
        self.s.on_frame(None)
        self.assertEqual(self.map(), (LOADED_MAP, "loading"))
        self.s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=[DREAM], unknown_slots=0, confidence=0.9))
        self.s.on_frame(offer(OFFER_C))
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_owned_chip_alone(self):
        """专属筹码是在面板上读到的（之前没有哪次三选一里出现过它）：下一次三选一照样按它改。"""
        self.s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=[DREAM], unknown_slots=0, confidence=0.9))
        self.s.on_frame(offer(OFFER_C))
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_vote_kept_by_undo(self):
        """冲突的那一票跟着这一局的状态走：误判了新一局又撤销，这一票还在，换一组候选又指向梦想号就改。"""
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.s.on_frame(None)
        self.s.on_frame(None)
        self.assertEqual(self.s.on_select_screen().kind, "newgame")
        self.assertTrue(self.s.undo_new_game())
        self.assertEqual(self.map(), (LOADED_MAP, "loading"))
        self.s.on_frame(offer([OFFER_B[0], DREAM_2, OFFER_B[2]]))
        self.assertEqual(self.map(), (DREAM_MAP, "chips"))

    def test_vote_dropped_by_new_game(self):
        """真的开了新的一局：上一局冲突的那一票作废，新一局加载画面上的地图要重新数两组。"""
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.s.on_frame(None)
        self.s.on_frame(None)
        self.assertEqual(self.s.on_select_screen().kind, "newgame")
        self.s.on_loading(LOADED_MAP)
        self.s.on_frame(offer([OFFER_B[0], DREAM_2, OFFER_B[2]]))
        self.assertEqual(self.map(), (LOADED_MAP, "loading"))

    def test_agreeing_offer_resets(self):
        """中间有一次三选一的专属筹码和加载画面的地图对得上：之前那一次不算。"""
        self.s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.s.on_frame(offer(["诅咒之剑", OFFER_B[1], OFFER_B[2]], probabilities=(40, 45, 15)))
        self.s.on_frame(offer([OFFER_C[0], DREAM_2, OFFER_C[2]], probabilities=(25, 50, 25)))
        self.assertEqual(self.map()[0], LOADED_MAP)

    def test_task_map_changed_at_once(self):
        """任务栏认出的地图：照旧一次就按专属筹码改（第③层排在第②层前面）。"""
        s = Session(DB, Settings(hero=HERO))
        s.on_hero(guess(HERO))
        s.on_hud(hud(3, map=LOADED_MAP, map_confidence="medium", map_candidates=[LOADED_MAP], tasks_seen=["某任务"]))
        self.assertEqual((s.settings.map, s.map_source), (LOADED_MAP, "tasks"))
        s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.assertEqual((s.settings.map, s.map_source), (DREAM_MAP, "chips"))

    def test_unknown_map_set_at_once(self):
        s = Session(DB, Settings(hero=HERO))
        s.on_hero(guess(HERO))
        s.on_frame(offer([DREAM, OFFER_A[1], OFFER_A[2]]))
        self.assertEqual((s.settings.map, s.map_source), (DREAM_MAP, "chips"))


# ====================================================================== 识别流程里交给记账的两处


class FlowBase(unittest.TestCase):
    """FrameProcessor 接真的 Session，识别函数全部换成假的。每个时刻到期的读数都做完（settle：悬浮窗一帧只读一项，紧接着
    几帧接着读）。"""

    def setUp(self):
        self.seen, self.round, self.stars, self.bottom = None, None, None, None
        self.star_calls, self.pick_at = 0, []

        def fake_stars(img, hero, **kw):
            self.star_calls += 1
            return self.stars

        def fake_pick(img, db, info=None):
            self.pick_at.append(self.clock[0])
            if info is not None:
                info["screen"] = self.bottom
            return False

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full, **kw: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", side_effect=fake_pick),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", side_effect=fake_stars),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.session = Session(DB, Settings(hero="凛"))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(DB, self.session)
        self.n = 0

    def step(self, t, img=None):
        self.clock[0] = t
        self.n += 1
        if img is None:
            img = np.full((1080, 1920, 3), 10 + 30 * (self.n % 8), np.uint8)
        return self.proc.settle(img, t)


class ConsecutiveSelectFlowTest(FlowBase):
    def test_second_select_reads_stars(self):
        self.round, self.stars = 3, SimpleNamespace(stars=1, confidence=0.9)
        for t in (0.0, 4.0):
            self.step(t)
        self.round = None
        self.stars = None  # 第一次三选一上自己那一行被挡住：读满三帧就不再读
        self.seen = offer(OFFER_A, probabilities=(60, 37, 3), rarity=["蓝", "蓝", "紫"])
        for t in (8.0, 8.2, 8.4, 8.6):
            self.step(t)
        calls = self.star_calls
        self.step(8.8)
        self.assertEqual(self.star_calls, calls)
        # 两个任务接连完成：没有棋盘画面，紧接着出现另一次三选一。这一次照样读星数，读到的 1 星用进这次推荐
        self.stars = SimpleNamespace(stars=1, confidence=0.9)
        self.seen = offer(OFFER_B, probabilities=(40, 45, 15), rarity=["紫", "蓝", "金"])
        events = self.step(9.0)
        self.assertEqual(self.star_calls, calls + 1)
        advice = [e for e in events if e.kind == "advice"]
        self.assertEqual([(e.state.star, e.state.source) for e in advice], [(1, "task")])
        self.step(9.2)
        self.assertEqual(self.star_calls, calls + 1)  # 读到了：这一次不再读


class RoomRecheckFlowTest(FlowBase):
    """下方区域认出的房间（静止画面）：本局还没结束时不沿用「不是选角界面」的结论，hud_ms 后再认一次，第二次结束本局。
    看见轮次标记（第 8 秒）以后第一次读不到（第 12 秒）照认；第二次（第 15 秒）离那次标记不到 pick_grace_ms（10 秒），先不认，
    第 18 秒起照认。"""

    def test_static_room_read_again(self):
        self.assertEqual(self.proc.pick_grace_s, 10.0)  # 下面的时刻按默认时限排
        self.round, self.stars = 12, SimpleNamespace(stars=3, confidence=0.9)
        for t in (0.0, 4.0, 8.0):
            self.step(t)
        self.round, self.stars = None, None
        room = np.full((1080, 1920, 3), 200, np.uint8)
        self.step(12.0, room)  # 静止画面：这一次还没认出来
        self.step(15.0, room)  # 第二次读不到、离第 8 秒不到 pick_grace_ms：先不认下方区域
        self.assertEqual([t for t in self.pick_at if t >= 12.0], [12.0])
        self.bottom = OutOfGame(TEAM_ROOM, "噩梦")
        events = self.step(18.0, room)  # 认出了组队房间：只记下第一次（同样的画面，这一局还没结束，不沿用第 12 秒的结论）
        self.assertNotIn("game_over", kinds(events))
        self.assertFalse(self.session.game.game_over)
        events = self.step(21.0, room)  # 同样的画面再认一次
        self.assertEqual([e.why for e in events if e.kind == "game_over"], ["检测到组队房间"])
        self.assertEqual([t for t in self.pick_at if t >= 12.0], [12.0, 18.0, 21.0])
        for t in (24.0, 27.0, 30.0):
            self.step(t, room)
        self.assertEqual([t for t in self.pick_at if t >= 12.0], [12.0, 18.0, 21.0, 24.0])  # 结束以后照旧沿用结论


if __name__ == "__main__":
    unittest.main()
