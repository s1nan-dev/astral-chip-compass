"""离开很久再回来（游戏切到后台、识别暂停）时的新一局判定。

场景：上一局结束后直接切到后台，下一局（常常是同一个角色、同一个位次）开始后才切回来。回来时下一局可能已经过了
好几轮，轮次、星数、进度条都没有回到开头。期望：判出新的一局，上一局的筹码不带进下一局；只看画面分不出时，
至少请用户打开「查看筹码」面板（why="resume"），打开后按面板判出（cause=panel）。
对照：同一局离开再回来，绝不判成新的一局（误判会清掉已有筹码，影响最大），离开期间游戏代选的筹码按面板补进来。

Session 层用假时钟（session._clock）和假读数，按真实节奏和顺序喂：每 2~3 秒一次，轮次 → 星数 → 进度条，
角色每 20 秒复查一次。另测悬浮窗的两处：Watcher.set_paused 恢复时调用 on_resume，提示文字和「对局中途」的显示。
"""
import random
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from advisor.data import load_db  # noqa: E402
from advisor.session import (  # noqa: E402
    NEWGAME_PROGRESS_DROP,
    NEWGAME_ROUND_MAX,
    RESUME_CHECK_S,
    Event,
    Session,
    Settings,
)
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import PROMPT_PANEL, PROMPT_PANEL_RESUME
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.overlay_parts import watcher as watcher_mod
from advisor.ui.winapi import GameProbe  # noqa: E402
from tests.support import qtenv  # noqa: E402

APP = qtenv.app()
DB = load_db()

SEED = 20261003
CAP = 17  # 进度上限（红叉所在的格）
MAP = "异变图书馆"
HERO, OTHER_HERO = "芬妮", "凛"
TICK = (2.0, 3.0)  # 两次对局画面读数之间的秒数
HERO_S = 20.0  # 角色复查的间隔
# 上一局的筹码（都是普通筹码，不影响地图推定）。下一局的筹码只和它重合「紫色飞星」一个（同一个角色常拿的）
OLD_POOL = ["“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机", "银行卡-余额多", "医疗箱-紧急治疗", "书签", "能量回收",
            "小猪存钱罐", "友情徽章"]
NEXT_POOL = ["紫色飞星", "金色飞星", "银行卡-余额少", "磨刀石", "标靶", "大背包", "智能手表", "鹰眼瞄具", "缓冲盾牌"]
AUTO_POOL = ["星币锤", "手电筒-强光", "电流剑", "魔法箭袋"]  # 同一局离开期间游戏代选的筹码
READ_CAUSES = ("round", "star", "progress", "hero")  # 靠对局画面读数判出的依据


def hud(rnd, map=None):
    return SimpleNamespace(round=rnd, map=map)


def own(stars, confidence=0.9):
    return SimpleNamespace(stars=stars, confidence=confidence)


def guess(hero):
    return SimpleNamespace(hero=hero)


def panel(owned, unknown=0, full=False, self_tab=True, self_index=None, selected_tab=None, confidence=0.95):
    """「查看筹码」面板的识别结果（advisor.vision.chip_panel.PanelReading 的样子）。"""
    return SimpleNamespace(is_panel=True, self_tab=self_tab, self_index=self_index, selected_tab=selected_tab,
                           owned=list(owned), unknown_slots=unknown, confidence=confidence, page_full=full,
                           problems=[])


def fake_advise(db, state, names, rarity=None, **kw):
    """假的推荐引擎：这里只测记账。"""
    return SimpleNamespace(best=names[0], evals=(), reroll=None, notes=[])


# 记账里调用推荐引擎的地方（按名字导入了 engine.advise 的那个模块）：换成 fake_advise 要换这里
ADVISE = "advisor.session.selection.advise"


def select(names, selected=None):
    """一帧三选一的识别结果。"""
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=1, probabilities=(40, 45, 15),
                           rarity=["紫"] * 3, team=False, selected=selected, problems=[])


class Table:
    """模拟识别流程：假时钟，按真实顺序喂读数，收集 Session 发出的全部事件（主要事件在前，待取事件在后）。"""

    def __init__(self, hero=HERO, row=1, seed=SEED, session=None):
        self.s = session if session is not None else Session(DB, Settings(hero=hero))
        self.t = 1000.0
        self.s._clock = lambda: self.t
        self.rng = random.Random(seed)
        self.hero, self.row = hero, row
        self.hero_at = None
        self.events: list[Event] = []

    def keep(self, ev):
        if ev is not None:
            self.events.append(ev)
        return ev

    def drain(self):
        self.events += self.s.take_events()

    def mark(self) -> int:
        return len(self.events)

    def read(self, rnd, star, prog, cap=CAP, map=None, hero=None, row=None, dt=None):
        """一次对局画面的读数：轮次 → 星数 → 进度条；角色每 20 秒复查一次。读不到的项传 None。"""
        self.t += dt if dt is not None else self.rng.uniform(*TICK)
        s = self.s
        self.keep(s.on_hud(hud(rnd, map)))
        self.keep(s.on_stars(own(star) if star is not None else None))
        self.keep(s.on_progress(prog, cap if prog is not None else None))
        if self.hero_at is None or self.t - self.hero_at >= HERO_S:
            self.hero_at = self.t
            self.keep(s.on_hero(guess(hero or self.hero)))
        s.note_own(row if row is not None else self.row)
        self.drain()

    def open_panel(self, owned, **kw):
        ev = self.keep(self.s.on_panel(panel(owned, **kw)))
        self.drain()
        return ev

    def close_panel(self):
        self.keep(self.s.on_panel(None))
        self.drain()

    def away(self, seconds, resume=True):
        """切到后台 seconds 秒；resume 时像悬浮窗那样在恢复识别时调用 on_resume。"""
        self.t += seconds
        if resume:
            self.s.on_resume(seconds)

    def play_old(self, end_round, end_star, end_prog, chips, map=MAP):
        """上一局（或离开前的这一局）：第 1 轮打到 end_round，每轮读 3 次；星数逐步升到 end_star；
        进度比轮次快，最后到 end_prog（等于红叉时这一局打满结束）。开局照提示打开一次面板（还没有筹码），
        倒数第二轮打开面板记下已有筹码。"""
        ups = [round(end_round * k / (end_star + 1)) for k in range(1, end_star + 1)]
        for r in range(1, end_round + 1):
            prog = max(r, min(end_prog, round(r * end_prog / end_round)))
            star = sum(r >= u for u in ups)
            for _ in range(3):
                self.read(r, star, prog, map=map)
            if r == 1:
                self.open_panel([])
                self.close_panel()
            if r == max(1, end_round - 1):
                self.open_panel(chips)
                self.close_panel()

    def kinds(self, kind, since=0):
        return [e for e in self.events[since:] if e.kind == kind]

    def newgames(self, since=0):
        return self.kinds("newgame", since)

    def prompts(self, since=0):
        return [e.why for e in self.kinds("panel_prompt", since)]


def next_stage(rnd, high):
    """下一局第 rnd 轮时合理的星数（high 取范围里大的那个）。"""
    lo, hi = (0, 0) if rnd <= 2 else (0, 1) if rnd <= 5 else (1, 2) if rnd <= 9 else (2, 3)
    return hi if high else lo


def old_chips(n, k=0):
    """上一局的 n 个筹码，从 OLD_POOL 第 k 个起取（轮换，和下一局的重合有时有、有时没有）。"""
    return [OLD_POOL[(k + i) % len(OLD_POOL)] for i in range(n)]


def why_ok(test, ev, old, now):
    """新一局的依据写得合理：写明是哪一项、从多少变成多少。old=(轮次, 星数, 进度, 角色)，now 同。"""
    old_round, old_star, old_prog, old_hero = old
    rnd, star, prog, hero = now
    if ev.cause == "round":
        test.assertIn(f"轮次由第 {old_round} 轮变为第 {rnd} 轮", ev.why)
    elif ev.cause == "star":
        test.assertIn(f"星数由 {old_star} 星减少为 {star} 星", ev.why)
    elif ev.cause == "progress":
        test.assertTrue(ev.why.startswith(f"进度条由第 {old_prog} 格退回第 {prog} 格"), ev.why)
        test.assertTrue("轮次变小" in ev.why or "星数变少" in ev.why, ev.why)
    elif ev.cause == "hero":
        test.assertIn(f"识别到的角色由「{old_hero}」变为「{hero}」", ev.why)
    else:
        test.fail(f"意外的依据：{ev.cause}（{ev.why}）")


# ====================================================================== 1. 上一局结束、下一局开始后回来


class NextGameAfterResumeTest(unittest.TestCase):
    """上一局结束后切到后台，下一局开始后才回来：判出新的一局；判不出时已请用户打开面板，打开后判出。"""

    OLD_ENDS = (
        (12, 3, 17), (13, 2, 17), (14, 3, 17), (15, 2, 17), (16, 3, 17),  # 打满进度（到红叉 17）
        (9, 2, 11), (9, 3, 11),  # 提前打赢：第 9 轮、进度 11
    )
    # （角色, 位次）：同一个角色同一个位次（主场景）、换了角色、换了位次（头像行变了）、都换了
    VARIANTS = ((HERO, 1), (OTHER_HERO, 1), (HERO, 3), (OTHER_HERO, 2))
    AWAYS = (180, 420, 900, 1200)  # 离开 3~20 分钟

    def run_case(self, old, n_old, k, away, rnd, star, prog, hero, row):
        end_round, end_star, end_prog = old
        chips = old_chips(n_old, k)
        nxt = NEXT_POOL[:min(len(NEXT_POOL), star + rnd // 3)]
        t = Table(seed=SEED + k)
        t.play_old(end_round, end_star, end_prog, chips)
        self.assertEqual(t.newgames(), [])  # 上一局之内没有误判（前提）
        self.assertEqual(sorted(t.s.settings.owned), sorted(chips))
        mark = t.mark()
        t.away(away)
        for _ in range(12):  # 回来后读 30 秒左右（其间复查两次角色）
            t.read(rnd, star, prog, hero=hero, row=row)
        # 回来后第一次进入对局画面就请用户打开面板核对（不管之后判没判出）
        self.assertEqual(t.prompts(mark)[:1], ["resume"])
        found = t.newgames(mark)
        self.assertLessEqual(len(found), 1)
        if rnd <= NEWGAME_ROUND_MAX:
            # 前几轮：轮次变小直接判出
            self.assertEqual([e.cause for e in found], ["round"])
        if prog <= end_prog - NEWGAME_PROGRESS_DROP and (rnd < end_round or star < end_star):
            # 进度条退回 3 格以上、轮次或星数也退回：读数就能判出
            self.assertEqual(len(found), 1)
        if found:
            ev = found[0]
            self.assertIn(ev.cause, READ_CAUSES)
            self.assertTrue(ev.undo)
            why_ok(self, ev, (end_round, end_star, end_prog, HERO), (rnd, star, prog, hero))
            self.assertEqual(t.s.settings.owned, [])  # 上一局的筹码已清空
            self.assertIsNone(t.s.settings.map)
            # 判出后照常请用户打开面板（不再是「离开后核对」的说法）
            self.assertIn("", t.prompts(t.events.index(ev)))
            ev = t.open_panel(nxt)
            self.assertEqual(ev.kind, "panel_read")
        else:
            # 只看画面分不出：用户照提示打开面板，面板上少了之前记下的筹码 → 判出
            ev = t.open_panel(nxt)
            self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
            missing = [c for c in chips if c not in nxt]
            self.assertTrue(ev.why.startswith(f"离开后打开的「查看筹码」面板上没有之前记下的 {len(missing)} 个筹码"),
                            ev.why)
            self.assertTrue(ev.undo)
            self.assertEqual(t.events[-1].kind, "panel_read")  # 先发新一局，再发这一局的面板读数
        self.assertEqual(t.s.settings.owned, nxt)
        t.close_panel()
        for _ in range(10):  # 再读 20 秒以上（其间复查一次角色）
            t.read(rnd, star, prog, hero=hero, row=row)
        self.assertEqual(len(t.newgames(mark)), 1)  # 只判一次
        s = t.s
        self.assertEqual(s.settings.owned, nxt)  # 上一局的筹码没有进入下一局
        self.assertEqual((s.settings.round, s.settings.star, s.game.progress), (rnd, star, prog))
        self.assertIsNone(s.settings.map)  # 上一局的地图也不沿用
        self.assertEqual((s.game.seen_hero, s.game.own_row), (hero, row))

    def test_matrix(self):
        """上一局的几种结束状态 × 下一局第 1~14 轮回来 × 星数高低、进度快慢 × 是否换角色、换位次。"""
        i = 0
        for old in self.OLD_ENDS:
            for rnd in range(1, 15):
                for high in (False, True):
                    for fast in (0, 3):  # 进度 ≥ 轮次；击倒多时快几格
                        prog = min(CAP - 1, rnd + fast)
                        star = next_stage(rnd, high)
                        for hero, row in self.VARIANTS:
                            i += 1
                            n_old, away = 4 + i % 4, self.AWAYS[i % len(self.AWAYS)]
                            desc = (f"上一局 {old}（轮次, 星数, 进度）、{n_old} 个筹码；离开 {away} 秒；"
                                    f"回来第 {rnd} 轮 {star} 星 进度 {prog}；角色 {hero} 第 {row} 行")
                            with self.subTest(desc):
                                self.run_case(old, n_old, i, away, rnd, star, prog, hero, row)

    def test_same_hero_same_row_late_stage_needs_panel(self):
        """主场景里最难的：同一个角色、同一个位次，回来时下一局正好和上一局同轮次同星数、进度也没退回 3 格。
        只看画面分不出，只要求提示打开面板，打开后判出。"""
        t = Table()
        chips = old_chips(6)
        t.play_old(12, 3, 17, chips)
        mark = t.mark()
        t.away(600)
        for _ in range(20):
            t.read(12, 3, 16)
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.prompts(mark), ["resume"])  # 只提示一次
        self.assertTrue(t.s.game.panel_needed)
        self.assertEqual(sorted(t.s.settings.owned), sorted(chips))  # 还没打开面板：先保留
        ev = t.open_panel(NEXT_POOL[:6])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertTrue(t.s.can_undo_new_game)  # 面板读数写进了已有筹码，仍保留撤销（万一面板读错）
        self.assertEqual([e.kind for e in t.events[-2:]], ["newgame", "panel_read"])
        self.assertEqual(t.events[-1].owned, NEXT_POOL[:6])
        self.assertFalse(t.s.game.panel_needed)
        mark = t.mark()
        for _ in range(5):
            t.read(12, 3, 16)
        self.assertEqual(t.prompts(mark), [])  # 面板已经读过，不再请用户打开

    def test_classic_progress_case(self):
        """上一局第 12 轮打满（进度 17），回来时下一局第 7 轮、2 星、第 8 格：轮次超过第 5 轮，靠进度条退回判出。"""
        t = Table()
        t.play_old(12, 3, 17, old_chips(6))
        mark = t.mark()
        t.away(300)
        for _ in range(4):
            t.read(7, 2, 8)
        found = t.newgames(mark)
        self.assertEqual([e.cause for e in found], ["progress"])
        self.assertEqual(found[0].why, "进度条由第 17 格退回第 8 格，轮次变小")
        self.assertEqual((t.s.game.progress, t.s.settings.owned), (8, []))

    def test_back_on_select_screen(self):
        """回来时正好在下一局的选角界面：按选角界面判出，从选角开始看到的一局不用再请用户打开面板。"""
        t = Table()
        t.play_old(12, 3, 17, old_chips(5))
        mark = t.mark()
        t.away(400)
        ev = t.keep(t.s.on_select_screen())
        self.assertEqual((ev.kind, ev.cause), ("newgame", "select"))
        for rnd in (1, 1, 1, 2, 2, 2):
            t.read(rnd, 0, rnd)
        self.assertEqual(len(t.newgames(mark)), 1)
        self.assertEqual(t.prompts(mark), [])
        self.assertFalse(t.s.game.panel_needed)
        self.assertEqual(t.s.settings.owned, [])

    def test_next_game_panel_with_unknown_slot(self):
        """下一局的面板上有 1 格认不出：上一局记下的 6 个里有 5 个不在面板上，就算认不出的那格是上一局的，
        也至少少了 4 个，判为新的一局（同一局里筹码只增不减），上一局的筹码不留进下一局的已有筹码。"""
        t = Table()
        chips = old_chips(6)
        t.play_old(12, 3, 17, chips)
        mark = t.mark()
        t.away(300)
        for _ in range(10):
            t.read(12, 3, 15)
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.prompts(mark), ["resume"])
        nxt = NEXT_POOL[:4]
        ev = t.open_panel(nxt, unknown=1)
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertTrue(ev.why.endswith("，另有 1 格未能识别"), ev.why)
        self.assertFalse(set(t.s.settings.owned) & (set(chips) - set(nxt)))  # 上一局的筹码不进入下一局


def out_of_game(kind, difficulty=None, map=None, rounds=None):
    """对局外画面的识别结果（advisor.vision.screens.OutOfGame 的样子）。"""
    return SimpleNamespace(kind=kind, difficulty=difficulty, map=map, rounds=rounds)


class GameOverResumeTest(unittest.TestCase):
    """看到了上一局的结算画面（或房间、「匹配成功」画面）再切到后台，下一局开始后才回来：
    本局已结束，回来后连续两次读到同一个轮次就开新的一局（不可撤销，不经过互相印证），上一局的筹码不带进下一局；
    轮次大于 2 时请用户打开面板（不是「离开后核对」的说法）。对照 NextGameAfterResumeTest：同样的读数，没有结算画面时
    常常要靠面板才判得出。"""

    OLD_ENDS = ((12, 3, 17), (16, 3, 17), (9, 2, 11))
    VARIANTS = NextGameAfterResumeTest.VARIANTS

    def finish(self, t, end_round):
        ev = t.keep(t.s.on_out_of_game(out_of_game("result", "噩梦", MAP, end_round)))
        self.assertEqual(ev.kind, "game_over")
        self.assertTrue(t.s.game.game_over)

    def run_case(self, old, n_old, k, away, rnd, star, prog, hero, row):
        end_round, end_star, end_prog = old
        chips = old_chips(n_old, k)
        t = Table(seed=SEED + k)
        t.play_old(end_round, end_star, end_prog, chips)
        self.finish(t, end_round)
        mark = t.mark()
        t.away(away)  # 恢复识别时的离开后核对：本局已结束，不核对
        self.assertFalse(t.s.game.panel_needed)
        for _ in range(12):
            t.read(rnd, star, prog, hero=hero, row=row)
        found = t.newgames(mark)
        self.assertEqual([(e.cause, e.undo) for e in found], [("round", False)])
        self.assertEqual(found[0].why, f"上一局已结束，检测到第 {rnd} 轮的对局画面")
        self.assertEqual(t.kinds("game_over", mark), [])
        self.assertEqual(t.prompts(mark), [""] if rnd > 2 else [])  # 不是「离开后核对」的提示
        self.assertEqual(t.s.settings.owned, [])  # 上一局的筹码没有带进来
        nxt = NEXT_POOL[:min(len(NEXT_POOL), star + rnd // 3)]
        if rnd > 2:
            ev = t.open_panel(nxt)
            self.assertEqual(ev.kind, "panel_read")
            t.close_panel()
        else:
            nxt = []
        for _ in range(10):
            t.read(rnd, star, prog, hero=hero, row=row)
        self.assertEqual(len(t.newgames(mark)), 1)  # 只判一次
        s = t.s
        self.assertEqual(s.settings.owned, nxt)
        self.assertEqual((s.settings.round, s.settings.star, s.game.progress), (rnd, star, prog))
        self.assertIsNone(s.settings.map)  # 上一局的地图也不沿用
        self.assertEqual((s.game.seen_hero, s.game.own_row), (hero, row))
        self.assertFalse(s.game.game_over)

    def test_matrix(self):
        """上一局的几种结束状态 × 下一局第 1~14 轮回来 × 星数高低 × 是否换角色、换位次。"""
        i = 0
        for old in self.OLD_ENDS:
            for rnd in range(1, 15):
                for high in (False, True):
                    prog = min(CAP - 1, rnd + (2 if high else 0))
                    star = next_stage(rnd, high)
                    for hero, row in self.VARIANTS:
                        i += 1
                        n_old, away = 4 + i % 4, NextGameAfterResumeTest.AWAYS[i % 4]
                        desc = (f"上一局 {old}（轮次, 星数, 进度）、{n_old} 个筹码；离开 {away} 秒；"
                                f"回来第 {rnd} 轮 {star} 星 进度 {prog}；角色 {hero} 第 {row} 行")
                        with self.subTest(desc):
                            self.run_case(old, n_old, i, away, rnd, star, prog, hero, row)

    def test_same_hero_same_stage(self):
        """NextGameAfterResumeTest 里最难的那种（同一个角色、同一个位次、同轮次同星数、进度没退回 3 格）：
        有结算画面时读数就判得出。"""
        t = Table()
        t.play_old(12, 3, 17, old_chips(6))
        self.finish(t, 12)
        mark = t.mark()
        t.away(600)
        for _ in range(4):
            t.read(12, 3, 16)
        self.assertEqual([e.cause for e in t.newgames(mark)], ["round"])
        self.assertEqual(t.prompts(mark), [""])
        ev = t.open_panel(NEXT_POOL[:6])
        self.assertEqual((ev.kind, ev.owned), ("panel_read", NEXT_POOL[:6]))
        self.assertEqual(len(t.newgames(mark)), 1)

    def test_room_and_match_found_instead_of_result(self):
        """结算画面没看到、回来时在房间里：隔一会儿两次读到房间就结束上一局；「匹配成功」画面上的难度和地图用到下一局。
        选角界面连续出现好几帧：只判一次新的一局（地图等到进入对局才写进对局状态；房间结束的局之后按选角界面判出的新一局
        不可撤销：选角界面只在开局时出现）。"""
        t = Table()
        t.play_old(12, 3, 17, old_chips(5))
        mark = t.mark()
        t.away(400)
        self.assertIsNone(t.keep(t.s.on_out_of_game(out_of_game("team_room", "噩梦"))))  # 只读到一次：还不算结束
        t.t += 3
        ev = t.keep(t.s.on_out_of_game(out_of_game("team_room", "噩梦")))
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到组队房间"))
        self.assertIsNone(t.s.on_out_of_game(out_of_game("match_found", "噩梦", "水乡古镇")))
        ev = t.keep(t.s.on_select_screen())
        self.assertEqual((ev.kind, ev.cause, ev.undo), ("newgame", "select", False))
        for _ in range(4):
            t.t += 1
            self.assertIsNone(t.keep(t.s.on_select_screen()))
        t.drain()
        for rnd in (1, 1, 1, 2, 2, 2):
            t.read(rnd, 0, rnd)
        self.assertEqual(len(t.newgames(mark)), 1)
        self.assertEqual(t.prompts(mark), [])
        s = t.s
        self.assertEqual((s.settings.map, s.map_source), ("水乡古镇", "match"))
        info = s.difficulty_info
        self.assertEqual((info.name, info.source), ("噩梦", "match_found"))


# ====================================================================== 2. 同一局离开再回来（对照）


class SameGameAfterResumeTest(unittest.TestCase):
    """同一局离开 2~10 分钟再回来：绝不判成新的一局；离开期间代选的筹码按面板补进来。"""

    def leave_and_return(self, t, end_round, end_star, end_prog, chips, away, adv, star, prog, reads=8):
        t.play_old(end_round, end_star, end_prog, chips)
        mark = t.mark()
        t.away(away)
        for _ in range(reads):
            t.read(end_round + adv, star, prog)
        return mark

    def test_random_returns(self):
        """随机 600 次：回来时轮次往前走了 0~4 轮，星数不变或多了，进度每轮至少 +1、任务奖励最多再退 2 格；
        面板上是原有筹码加上代选的 0~2 个，有时有认不出的格子，有时 10 格正好满了（只有一页时满了也是全部）。"""
        rng = random.Random(SEED)
        for k in range(600):
            end_round = rng.randint(2, 14)
            end_star = min(3, rng.randint(0, end_round // 3))
            end_prog = min(CAP - 1, end_round + rng.randint(0, 3))
            case = rng.choice(["plain", "unknown", "full"])
            picked = rng.sample(AUTO_POOL, rng.randint(1 if case == "full" else 0, 2))
            # 这一页满了：原有的加上代选的正好 10 个
            n = 10 - len(picked) if case == "full" else min(7, max(1, end_round // 2 + 1))
            chips = rng.sample(OLD_POOL, n)
            away = rng.uniform(120, 600)
            adv = rng.randint(0, 4)
            star = min(3, end_star + rng.choice([0, 0, 1, 2]))
            # 任务奖励「进度-1」一次退 1 格，最多两个任务接连完成；击倒多时多走几格
            prog = min(CAP - 1, end_prog + adv + rng.choice([-2, -1, 0, 0, 1, 2]))
            desc = (f"#{k} 离开前第 {end_round} 轮 {end_star} 星 进度 {end_prog}、{n} 个筹码；离开 {away:.0f} 秒；"
                    f"回来第 {end_round + adv} 轮 {star} 星 进度 {prog}；代选 {picked}；面板 {case}")
            with self.subTest(desc):
                t = Table(seed=k)
                mark = self.leave_and_return(t, end_round, end_star, end_prog, chips, away, adv, star, prog,
                                             reads=rng.randint(4, 12))
                self.assertEqual(t.prompts(mark)[:1], ["resume"])
                shown = chips + picked
                if case == "unknown":
                    hidden = rng.sample(shown, rng.randint(1, min(3, len(shown))))
                    seen = [c for c in shown if c not in hidden]
                    ev = t.open_panel(seen, unknown=len(hidden))
                elif case == "full":
                    seen = shown[:10]
                    ev = t.open_panel(seen, full=True)
                else:
                    seen = shown
                    ev = t.open_panel(seen)
                self.assertEqual(ev.kind, "panel_read")
                t.close_panel()
                for _ in range(4):
                    t.read(end_round + adv, star, prog)
                self.assertEqual(t.newgames(mark), [])
                owned = set(t.s.settings.owned)
                self.assertLessEqual(set(chips), owned)  # 原有筹码一个不少
                self.assertLessEqual(set(seen), owned)  # 面板上认出的代选筹码补进来了
                if case in ("plain", "full"):
                    self.assertEqual(owned, set(shown))
                self.assertEqual((t.s.settings.round, t.s.settings.star), (end_round + adv, star))

    def test_round_unchanged_progress_back_two(self):
        """回来时还在同一轮，进度因两个任务奖励退回 2 格：不判；面板上多了一个代选的筹码，补进来。"""
        t = Table()
        chips = old_chips(5)
        mark = self.leave_and_return(t, 10, 2, 13, chips, away=420, adv=0, star=2, prog=11, reads=10)
        self.assertEqual(t.newgames(mark), [])
        ev = t.open_panel(chips + ["星币锤"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.s.settings.owned, chips + ["星币锤"])
        self.assertEqual(t.s.game.chip_sources["星币锤"], "panel")

    def test_page_full(self):
        """只有一页（两端的翻页箭头都不亮）：原有 8 个、离开期间代选了 2 个，面板 10 格满了就是全部，照常核对：不判新一局，
        已有筹码就是这 10 个。"""
        t = Table()
        chips = OLD_POOL[:8]
        mark = self.leave_and_return(t, 14, 3, 16, chips, away=600, adv=1, star=3, prog=16)
        shown = ["星币锤", *chips, "电流剑"]
        ev = t.open_panel(shown, full=True)
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(set(t.s.settings.owned), set(shown))

    def test_unknown_slots(self):
        """面板上有 2 格认不出（正好是原有的两个）：不下结论，原有筹码保留，认不出的格数照常报给界面。"""
        t = Table()
        chips = old_chips(6)
        mark = self.leave_and_return(t, 9, 2, 11, chips, away=300, adv=2, star=3, prog=13)
        ev = t.open_panel(chips[2:] + ["手电筒-强光"], unknown=2)
        self.assertEqual((ev.kind, ev.count), ("panel_read", 2))
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(set(t.s.settings.owned), set(chips) | {"手电筒-强光"})

    def test_misreads_after_return(self):
        """回来后的读数有噪声：轮次「14」读成「11」连错两次，转账画面把队友的 0 星当成自己的，轮次又被挡住几次。
        离开很久并不放宽互相印证：照旧按读错处理，不判。"""
        t = Table()
        chips = old_chips(6)
        t.play_old(14, 3, 16, chips)
        mark = t.mark()
        t.away(480)
        t.read(14, 3, 16)
        for _ in range(2):
            t.read(11, 0, 16)
        for _ in range(3):
            t.read(None, 0, 16)
        for _ in range(6):
            t.read(15, 3, 16)
        ev = t.open_panel(chips + ["星币锤"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual((t.s.settings.round, t.s.settings.star), (15, 3))
        self.assertEqual(t.s.settings.owned, chips + ["星币锤"])

    def test_select_open_when_leaving(self):
        """离开时三选一界面开着、绿框在第 3 个；离开期间思考时间用完，游戏拿了最左边的。回来后界面已关，
        记下的是绿框那个（错的）。面板上少了它、多了最左边那个：只差 1 个，不判新一局，按面板改正。"""
        with mock.patch(ADVISE, fake_advise):
            t = Table()
            chips = old_chips(4)
            t.play_old(10, 2, 12, chips)
            offer = ["磨刀石", "标靶", "大背包"]
            t.keep(t.s.on_frame(select(offer, selected=2)))
            mark = t.mark()
            t.away(300)
            t.keep(t.s.on_frame(None))
            ev = t.keep(t.s.on_frame(None))
            self.assertEqual((ev.kind, ev.chip), ("picked", "大背包"))
            for _ in range(6):
                t.read(11, 2, 13)
            ev = t.open_panel(chips + ["磨刀石", "书签"])
            self.assertEqual(ev.kind, "panel_read")
            self.assertEqual(ev.fixes, [("大背包", "磨刀石")])
            self.assertEqual(t.newgames(mark), [])
            self.assertEqual(set(t.s.settings.owned), set(chips) | {"磨刀石", "书签"})

    def test_two_wrong_picks(self):
        """同一局里两次三选一都记错了（思考时间用完，游戏拿了最左边的；面板上的同组候选能说明实际拿的是哪个）：
        离开后核对时这两个不算「面板上少了的筹码」，不判新的一局，按面板改正两条选择记录。"""
        with mock.patch(ADVISE, fake_advise):
            t = Table()
            chips = old_chips(4)
            t.play_old(10, 2, 12, chips)
            # 第一次：绿框在第 3 个，思考时间用完，游戏拿了最左边的（之后没有打开过面板）
            first = ["磨刀石", "标靶", "大背包"]
            t.keep(t.s.on_frame(select(first, selected=2)))
            t.keep(t.s.on_frame(None)), t.keep(t.s.on_frame(None))
            t.read(10, 2, 12)
            # 第二次：绿框在第 3 个时切到后台，同样被代选了最左边的
            second = ["智能手表", "书签", "金色飞星"]
            t.keep(t.s.on_frame(select(second, selected=2)))
            mark = t.mark()
            t.away(300)
            t.keep(t.s.on_frame(None)), t.keep(t.s.on_frame(None))
            self.assertEqual(t.s.settings.owned, chips + ["大背包", "金色飞星"])
            for _ in range(6):
                t.read(11, 2, 13)
            ev = t.open_panel(chips + ["磨刀石", "智能手表"])
            self.assertEqual(t.newgames(mark), [])
            self.assertEqual(ev.kind, "panel_read")
            self.assertEqual(ev.fixes, [("大背包", "磨刀石"), ("金色飞星", "智能手表")])


# ====================================================================== 3. 撤销


class UndoAfterResumeTest(unittest.TestCase):
    """每种依据判出新一局后点撤销：恢复判定前的状态；之后同一批读数继续出现，不再判一次（不能反复判、反复撤）。"""

    def setUp(self):
        self.t = Table()
        self.chips = old_chips(6)
        self.t.play_old(12, 3, 17, self.chips)
        self.t.away(420)
        self.mark = self.t.mark()

    def judged(self, cause, reading, hero=HERO, reads=12):
        t = self.t
        for _ in range(reads):
            t.read(*reading, hero=hero)
            if t.newgames(self.mark):
                break
        found = t.newgames(self.mark)
        self.assertEqual([e.cause for e in found], [cause])
        self.assertTrue(t.s.can_undo_new_game)
        return found[0]

    def undo_and_continue(self, reading, hero=HERO, rounds=(), round_after=None):
        """撤销，检查恢复的状态；再读 40 秒同一批读数（其间复查角色），不能再判。"""
        t, s = self.t, self.t.s
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.settings.owned, self.chips)
        self.assertEqual(s.settings.star, 3)
        self.assertEqual(s.settings.round, 12 if round_after is None else round_after)
        self.assertEqual((s.settings.map, s.map_source), (MAP, "tasks"))
        self.assertEqual((s.game.progress_cap, s.game.seen_hero, s.settings.hero), (CAP, HERO, HERO))
        self.assertFalse(s.can_undo_new_game)
        self.assertFalse(s.undo_new_game())  # 不能撤销两次
        after = t.mark()
        for _ in range(16):
            t.read(*reading, hero=hero)
        for rnd, star, prog in rounds:  # 之后照常往前走
            for _ in range(4):
                t.read(rnd, star, prog, hero=hero)
        self.assertEqual(t.newgames(after), [])
        return after

    def test_round(self):
        """回来时下一局第 3 轮：轮次变小判出；撤销后同一批读数按改正读错处理。"""
        ev = self.judged("round", (3, 1, 4))
        self.assertEqual(ev.why, "轮次由第 12 轮变为第 3 轮")
        after = self.undo_and_continue((3, 1, 4), rounds=[(4, 1, 5)])
        self.assertEqual((self.t.s.settings.round, self.t.s.settings.star), (4, 1))  # 撤销后按读数改正
        # 撤销即说明核对过了：再打开面板也不再按「离开后核对」判
        ev = self.t.open_panel(NEXT_POOL[:2])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(self.t.newgames(after), [])

    def test_star(self):
        """轮次被挡住读不出、进度条也读不到，只有星数：0 星少于这一局到过的 3 星，判出。"""
        ev = self.judged("star", (None, 0, None))
        self.assertEqual(ev.why, "星数由 3 星减少为 0 星")
        self.undo_and_continue((None, 0, None))
        self.assertEqual(self.t.s.settings.star, 0)

    def test_progress(self):
        """回来时下一局第 7 轮、第 8 格：进度条和轮次一起往回退判出；撤销后「到过的」降到这批读数，不再判。"""
        ev = self.judged("progress", (7, 2, 8))
        self.assertEqual(ev.why, "进度条由第 17 格退回第 8 格，轮次变小")
        # 判定前轮次已按读数改正成第 7 轮（超过第 5 轮的变小先当改正读错），撤销恢复的就是它
        self.undo_and_continue((7, 2, 8), rounds=[(8, 2, 9), (9, 2, 11)], round_after=7)
        self.assertEqual((self.t.s.settings.round, self.t.s.game.progress), (9, 11))

    def test_hero(self):
        """换了角色、进度条也退回了：按角色判出；撤销后不再把这个角色当成新一局的迹象。"""
        ev = self.judged("hero", (12, 3, 13), hero=OTHER_HERO, reads=20)
        self.assertIn(f"识别到的角色由「{HERO}」变为「{OTHER_HERO}」", ev.why)
        self.undo_and_continue((12, 3, 13), hero=OTHER_HERO)

    def test_panel(self):
        """读数分不出，打开面板判出；撤销后面板仍开着、同一个读数再读到，不再判。"""
        t = self.t
        for _ in range(8):
            t.read(12, 3, 16)
        self.assertEqual(t.newgames(self.mark), [])
        ev = t.open_panel(NEXT_POOL[:5])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        after = self.undo_and_continue((12, 3, 16), rounds=[(13, 3, 16)])
        for _ in range(3):
            t.open_panel(NEXT_POOL[:5])
        t.close_panel()
        self.assertEqual(t.newgames(after), [])
        self.assertEqual(t.s.settings.owned, NEXT_POOL[:5])  # 面板上的就是全部已有筹码


# ====================================================================== 4. 什么时候核对


class ResumeCheckRulesTest(unittest.TestCase):
    """离开不到 120 秒不核对；没有对局状态时不核对；核对只做一次；读到队友的页签不算核对。"""

    def started(self, end=(10, 2, 12), n=5):
        t = Table()
        chips = old_chips(n)
        t.play_old(*end, chips)
        return t, chips

    def test_short_absence(self):
        """离开不到 RESUME_CHECK_S 秒：不提示核对，面板读数少了筹码也不当成新一局的证据。"""
        for seconds in (5, 30, 90, RESUME_CHECK_S - 0.5):
            with self.subTest(seconds=seconds):
                t, chips = self.started()
                mark = t.mark()
                t.away(seconds)
                for _ in range(6):
                    t.read(10, 2, 12)
                self.assertEqual(t.prompts(mark), [])
                self.assertFalse(t.s.game._resume_check)
                # 面板上少了两个（例如读错）：没在核对，不判新一局
                ev = t.open_panel(chips[2:])
                self.assertEqual(ev.kind, "panel_read")
                self.assertEqual(t.newgames(mark), [])

    def test_threshold(self):
        """正好 RESUME_CHECK_S 秒就核对。"""
        t, _ = self.started()
        mark = t.mark()
        t.away(RESUME_CHECK_S)
        t.read(10, 2, 12)
        self.assertEqual(t.prompts(mark), ["resume"])

    def test_no_game_state(self):
        """刚启动、还没有对局状态：不核对（启动时本来就会请用户打开面板，提示是普通的那一种）。"""
        t = Table()
        t.away(900)
        self.assertFalse(t.s.game._resume_check)
        self.assertFalse(any(e["kind"] == "read:resumed" for e in t.s.recent_events(600)))
        for _ in range(3):
            t.read(3, 1, 4)
        self.assertEqual(t.prompts(), [""])
        # 从选角界面判出新的一局后（对局状态已清空）再离开：同样不核对
        t2, _ = self.started()
        t2.t += 60
        self.assertEqual(t2.keep(t2.s.on_select_screen()).cause, "select")
        mark = t2.mark()
        t2.away(900)
        self.assertFalse(t2.s.game._resume_check)
        for _ in range(3):
            t2.read(1, 0, 1)
        self.assertEqual(t2.prompts(mark), [])

    def test_check_only_once(self):
        """第一次读到自己那一页就结束核对（这一页全对得上）：之后面板读数少了筹码（读错）也不再判；提示只发一次。"""
        t, chips = self.started()
        mark = t.mark()
        t.away(600)
        for _ in range(10):
            t.read(11, 2, 13)
        self.assertEqual(t.prompts(mark), ["resume"])
        ev = t.open_panel(chips + ["星币锤"])
        self.assertEqual(ev.kind, "panel_read")
        self.assertFalse(t.s.game._resume_check)
        t.close_panel()
        for _ in range(4):
            t.read(11, 2, 13)
        ev = t.open_panel(chips[3:])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(t.prompts(mark), ["resume"])

    def test_first_read_inconclusive_ends_check(self):
        """第一次读到自己那一页时有认不出的格子：不下结论，核对也就此结束。"""
        t, chips = self.started()
        mark = t.mark()
        t.away(600)
        t.read(11, 2, 13)
        t.open_panel(chips[1:], unknown=1)
        self.assertFalse(t.s.game._resume_check)
        t.close_panel()
        ev = t.open_panel(chips[3:])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(t.newgames(mark), [])

    def test_teammate_tab_is_not_a_check(self):
        """面板停在队友的页签、或把握不够的读数：不算核对，核对留到读到自己那一页。"""
        t, chips = self.started(end=(12, 3, 17), n=6)
        mark = t.mark()
        t.away(600)
        for _ in range(6):
            t.read(12, 3, 16)
        mates = NEXT_POOL[3:8]  # 队友的筹码，和自己记下的完全不同
        ev = t.open_panel(mates, self_tab=False, self_index=2, selected_tab=0)
        self.assertEqual((ev.kind, ev.count), ("panel_tab", 3))
        self.assertIsNone(t.open_panel(mates, self_tab=False))  # 认不出哪个是自己的页签
        self.assertIsNone(t.open_panel(NEXT_POOL[:5], confidence=0.3))
        self.assertTrue(t.s.game._resume_check)
        self.assertEqual(t.newgames(mark), [])
        self.assertEqual(sorted(t.s.settings.owned), sorted(chips))
        ev = t.open_panel(NEXT_POOL[:5])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))
        self.assertEqual([e.kind for e in t.events[-3:]], ["panel_tab", "newgame", "panel_read"])
        self.assertEqual(t.events[-3].count, 0)  # 收起「请点击自己的页签」

    def test_new_absence_rearms_check(self):
        """核对过（同一局）之后又离开很久：再核对一次。"""
        t, chips = self.started(end=(12, 3, 17), n=6)
        mark = t.mark()
        t.away(300)
        t.read(12, 3, 17)
        t.open_panel(chips)
        t.close_panel()
        self.assertFalse(t.s.game._resume_check)
        t.away(900)
        for _ in range(4):
            t.read(12, 3, 16)
        self.assertEqual(t.prompts(mark), ["resume", "resume"])
        ev = t.open_panel(NEXT_POOL[:6])
        self.assertEqual((ev.kind, ev.cause), ("newgame", "panel"))


# ====================================================================== 5. 手动模式：没有 on_resume，只是读数之间隔了很久


class ManualGapTest(unittest.TestCase):
    """手动模式平时不截图（按热键才识别，看到三选一后连续识别到界面关闭）：没有暂停，就没有 on_resume，
    Session 只看到读数之间隔了很久。判得出的靠读数本身；判不出的是已知局限，测试写明现状（不算缺陷）。"""

    def setUp(self):
        self.t = Table()
        self.chips = old_chips(6)
        self.t.play_old(12, 3, 17, self.chips)
        self.t.away(900, resume=False)
        self.mark = self.t.mark()

    def burst(self, rnd, star, prog, n=3, gap=60.0):
        """按一次热键：先隔 gap 秒，再连续读 n 次。"""
        self.t.t += gap
        for _ in range(n):
            self.t.read(rnd, star, prog)

    def test_early_round_detected(self):
        """隔了 15 分钟按热键，读到下一局第 3 轮：轮次变小判出（没有暂停，提示是普通的那一种）。"""
        self.burst(3, 1, 4)
        self.assertEqual([e.cause for e in self.t.newgames(self.mark)], ["round"])
        self.assertNotIn("resume", self.t.prompts(self.mark))

    def test_progress_drop_detected(self):
        """读到下一局第 8 轮、第 10 格：进度条退回 7 格、轮次也变小，判出。"""
        self.burst(8, 2, 10)
        self.assertEqual([e.cause for e in self.t.newgames(self.mark)], ["progress"])

    def test_single_presses_same_round_detected(self):
        """每次按热键只读到一帧、两次隔 60 秒，读到的是同一轮：照样连续两次就判出。"""
        for _ in range(2):
            self.burst(2, 0, 3, n=1)
        self.assertEqual([e.cause for e in self.t.newgames(self.mark)], ["round"])

    def test_single_presses_star_drop_detected(self):
        """每次只读一帧、每次都在不同的轮次（轮次、进度条都凑不齐两次相同）：星数少了、连读两次，判出。"""
        for rnd in (7, 8, 9):
            self.burst(rnd, 2, rnd + 2, n=1)
        self.assertEqual([e.cause for e in self.t.newgames(self.mark)], ["star"])

    def test_same_stage_not_detected(self):
        """已知局限：下一局和上一局同轮次、同星数，进度条也没退回 3 格，又没有暂停可依（没有 on_resume），
        既判不出也不提示核对。之后打开面板时，已有筹码按面板改写（上一局的筹码不会留下），但不判新一局，
        地图仍是上一局的。"""
        for rnd, prog in ((12, 15), (13, 16)):
            self.burst(rnd, 3, prog)
        self.assertEqual(self.t.newgames(self.mark), [])
        self.assertEqual(self.t.prompts(self.mark), [])
        ev = self.t.open_panel(NEXT_POOL[:6])
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(self.t.s.settings.owned, NEXT_POOL[:6])
        self.assertEqual(self.t.s.settings.map, MAP)

    def test_single_presses_changing_rounds_not_detected(self):
        """已知局限：每次只读一帧、每次都在不同的轮次，星数又和上一局一样：轮次、进度条凑不齐两次相同的读数，判不出，
        轮次也一直停在上一局的第 12 轮（一局之内往前走一轮会直接采用，往回只有连续读到两次才采用）。"""
        for rnd in (7, 8, 9, 10):
            self.burst(rnd, 3, rnd + 2, n=1)
        self.assertEqual(self.t.newgames(self.mark), [])
        self.assertEqual(self.t.s.settings.round, 12)


# ====================================================================== 6. 悬浮窗：暂停结束时调用 on_resume


class WatcherResumeTest(unittest.TestCase):
    """Watcher.set_paused：几个暂停原因都解除时算暂停了多久，告诉 Session（on_resume）。不启动线程。"""

    def setUp(self):
        self.clock = [5000.0]
        patcher = mock.patch.object(watcher_mod, "time", SimpleNamespace(monotonic=lambda: self.clock[0]))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def watcher(self, session=None):
        session = session if session is not None else SimpleNamespace(on_resume=self.calls.append,
                                                                      log_state=lambda kind, **data: None)
        return Watcher(DB, session, threading.Lock())

    def test_long_pause(self):
        """暂停 150 秒后恢复：调用一次 on_resume(150)。"""
        w = self.watcher()
        w.set_paused("focus", True)
        self.assertTrue(w.paused)
        self.clock[0] += 150
        w.set_paused("focus", False)
        self.assertFalse(w.paused)
        self.assertEqual(len(self.calls), 1)
        self.assertAlmostEqual(self.calls[0], 150, places=6)

    def test_overlapping_reasons(self):
        """切到后台期间又开始下载图片：两个原因都解除才算恢复，暂停时长从第一个原因开始算。"""
        w = self.watcher()
        w.set_paused("focus", True)
        self.clock[0] += 10
        w.set_paused("focus", True)  # 重复的不重新计时
        self.clock[0] += 40
        w.set_paused("assets", True)
        self.clock[0] += 50
        w.set_paused("focus", False)
        self.assertEqual(self.calls, [])
        self.assertTrue(w.paused)
        self.clock[0] += 130
        w.set_paused("assets", False)
        self.assertEqual(len(self.calls), 1)
        self.assertAlmostEqual(self.calls[0], 230, places=6)
        # 没在暂停时「解除」：不调用
        w.set_paused("focus", False)
        self.assertEqual(len(self.calls), 1)

    def test_with_session(self):
        """暂停 30 秒：调用了 on_resume，但 Session 不请用户核对；暂停 150 秒：回来第一次读到对局画面就提示。"""
        t = Table()
        t.play_old(10, 2, 12, old_chips(5))
        w = self.watcher(t.s)
        mark = t.mark()
        w.set_paused("focus", True)
        self.clock[0] += 30
        t.t += 30
        w.set_paused("focus", False)
        t.read(10, 2, 12)
        self.assertEqual(t.prompts(mark), [])
        self.assertFalse(t.s.game._resume_check)
        w.set_paused("focus", True)
        self.clock[0] += 150
        t.t += 150
        w.set_paused("focus", False)
        self.assertTrue(t.s.game._resume_check)
        t.read(10, 2, 12)
        self.assertEqual(t.prompts(mark), ["resume"])


class OverlayPauseResumeTest(unittest.TestCase):
    """悬浮窗按前台状态暂停（window_plan）：直接截取时切出游戏不暂停，离开再久也不请用户核对（识别一直在看）；只有游戏窗口
    最小化（或关闭）时暂停，最小化很久后还原照旧请用户打开「查看筹码」面板核对。截取屏幕时切出照常暂停、照常核对。
    暂停的细节写 minimized / screen-background。用真的 Watcher（不启动线程）和假时钟。"""

    def setUp(self):
        self.clock = [5000.0]
        patcher = mock.patch.object(watcher_mod, "time", SimpleNamespace(monotonic=lambda: self.clock[0]))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = OverlaySession(DB, UiSettings(hero=HERO))
        self.t = Table(session=self.session)
        self.t.play_old(10, 2, 12, old_chips(5))
        self.window = Overlay(DB, self.session, threading.Lock())
        self.window.watcher = Watcher(DB, self.session, threading.Lock())
        self.records = []
        self.session.log_state = lambda kind, **data: self.records.append((kind, data))
        self.mark = self.t.mark()

    def tearDown(self):
        self.window.hide()

    @staticmethod
    def probe(state):
        if state == "minimized":
            return GameProbe(True, None, True, False, False, hwnd=1)
        return GameProbe(True, (0, 0, 1920, 1080), False, state == "game", False, hwnd=1)

    def away(self, state, seconds):
        """切到 state，过 seconds 秒再切回游戏，然后读一次对局画面。"""
        w = self.window
        w.follower.update_activity(self.probe("game"))
        w.follower.update_activity(self.probe(state))
        self.clock[0] += seconds
        self.t.t += seconds
        w.follower.update_activity(self.probe("game"))
        self.t.read(10, 2, 12)

    def test_direct_capture_background_not_paused(self):
        self.away("other", 600)
        self.assertFalse(self.session.game._resume_check)
        self.assertEqual(self.t.prompts(self.mark), [])
        self.assertNotIn("pause", [k for k, _ in self.records])

    def test_minimized_long_asks_to_check(self):
        self.away("minimized", 150)
        self.assertEqual(self.t.prompts(self.mark), ["resume"])
        pauses = [d for k, d in self.records if k == "pause"]
        self.assertEqual(pauses[0]["why"], {"focus": "minimized"})

    def test_screen_capture_background_paused(self):
        self.window.on_capture_changed(SimpleNamespace(method="screen", problem="denied", detail="",
                                                       border_requested=False, build=26200))
        self.away("other", 150)
        self.assertEqual(self.t.prompts(self.mark), ["resume"])
        pauses = [d for k, d in self.records if k == "pause"]
        self.assertEqual(pauses[0]["why"], {"focus": "screen-background"})


# ====================================================================== 7. 悬浮窗：提示文字和「对局中途」的显示


class OverlayResumeTest(unittest.TestCase):
    def setUp(self):
        self.session = OverlaySession(DB, UiSettings(hero=HERO))
        self.window = Overlay(DB, self.session, threading.Lock())
        self.t = Table(session=self.session)
        self.chips = old_chips(6)
        self.t.play_old(12, 3, 17, self.chips)
        self.shown = 0
        self.feed()

    def tearDown(self):
        self.window.hide()

    def feed(self):
        """把 Session 新发出的事件按顺序交给悬浮窗（Watcher 经信号转到主线程的就是这些）。"""
        for ev in self.t.events[self.shown:]:
            self.window.handle(ev)
        self.shown = len(self.t.events)

    def test_prompt_texts(self):
        """why="resume" 显示核对的说法，普通的显示原来的提示（悬浮窗以 Session.panel_needed 为准，先让它要求打开面板）。"""
        w = self.window
        self.t.away(600)
        self.assertTrue(self.session.game.panel_needed)
        w.handle(Event("panel_prompt", why="resume"))
        self.assertEqual(w.notice_model.texts()["panel"], PROMPT_PANEL_RESUME)
        self.assertTrue(w.panel.property("attention"))
        w.handle(Event("panel_prompt"))
        self.assertEqual(w.notice_model.texts()["panel"], PROMPT_PANEL)

    def midgame_shown(self, why_start):
        w = self.window
        texts = w.notice_model.texts()
        self.assertTrue(texts["newgame"].startswith(f"⚠ 对局中途判定为新的一局（依据：{why_start}"), texts["newgame"])
        self.assertEqual(list(texts)[:2], ["newgame", "newgame_owned"])
        self.assertTrue(w.panel.property("attention"))
        self.assertTrue(w.needs_attention())
        self.assertEqual([b.text() for b in w.notices.buttons("newgame")], ["撤销"])

    def test_panel_newgame_flow(self):
        """离开很久回来：提示写明是核对；打开面板判出新一局，按「对局中途」显示，可以撤销。"""
        t, w = self.t, self.window
        t.away(600)
        for _ in range(6):
            t.read(12, 3, 16)
        self.feed()
        self.assertEqual(w.notice_model.texts()["panel"], PROMPT_PANEL_RESUME)
        self.assertTrue(w.panel.property("attention"))
        t.open_panel(NEXT_POOL[:5])
        self.feed()
        self.midgame_shown("离开后打开的「查看筹码」面板上没有之前记下的")
        self.assertNotIn("panel", w.notice_model.texts())  # 面板已经读过
        self.assertIn("已识别已有筹码 5 个", w.status.status_text())
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(self.session.settings.owned, self.chips)
        self.assertNotIn("newgame", w.notice_model.texts())

    def test_progress_newgame_flow(self):
        """进度条退回判出的新一局同样按「对局中途」显示；之后请打开面板的是普通提示。"""
        t, w = self.t, self.window
        t.away(600)
        for _ in range(6):
            t.read(7, 2, 8)
        self.assertEqual([e.cause for e in t.newgames()], ["progress"])
        self.feed()
        self.midgame_shown("进度条由第 17 格退回第 8 格")
        self.assertEqual(w.notice_model.texts()["panel"], PROMPT_PANEL)


if __name__ == "__main__":
    unittest.main()
