import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import (
    HARD,
    LEVEL_HARD,
    LEVEL_HARD_PLUS,
    LEVEL_NIGHTMARE,
    LEVEL_NORMAL,
    NORMAL,
    infer_difficulty,
    load_db,
)
from advisor.engine import NORMAL_NOTE, REROLL
from advisor.gamelog import GameRecorder, read_segment
from advisor.pool import State
from advisor.session import (
    DIFFICULTY_SOURCE_LABEL,
    END_ROUND_CONFIRM_S,
    GAME_FIELDS,
    MAP_SOURCE_LABEL,
    OUT_OF_GAME_LABEL,
    ROOM_KINDS,
    UNSUPPORTED_TEXT,
    DifficultyInfo,
    Event,
    Session,
    Settings,
    _event_record,
)
from advisor.vision import hud as hud_module
from advisor.vision import pipeline
from advisor.vision.chip_select import Line
from advisor.vision.hud import Hud, _exact_map_name, _guess_map, _parse_round, retired_maps
from tests.support import sandbox

PROJECT = Path(__file__).resolve().parents[1]


def frame(names, rerolls=2, probabilities=(60, 37, 3), selected=None, problems=()):
    return SimpleNamespace(
        names=list(names), raw=[n or "???" for n in names], rerolls=rerolls,
        probabilities=probabilities, selected=selected, problems=list(problems),
    )


def hud(round=None, map=None, **kw):
    return SimpleNamespace(round=round, map=map, **kw)


def own(stars, confidence=0.9):
    """自己星数的识别结果（advisor.vision.stars.OwnStars 的样子）。"""
    return SimpleNamespace(stars=stars, confidence=confidence)


def screen(kind, difficulty=None, map=None, rounds=None):
    """对局外画面的识别结果（advisor.vision.screens.OutOfGame 的样子，不依赖 screens.py）。"""
    return SimpleNamespace(kind=kind, difficulty=difficulty, map=map, rounds=rounds)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="远野汉娜"))
        self.session.game.seen_hero = "远野汉娜"  # 角色已识别（角色待识别时不给推荐，见 HeroNeededTest）
        self.offer = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]

    def fresh(self) -> Session:
        """角色还没识别的 Session（测角色识别本身时用）。"""
        self.session = Session(self.db, Settings(hero="远野汉娜"))
        return self.session

    def test_advice_once_per_offer(self):
        ev = self.session.on_frame(frame(self.offer))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.state.rerolls, 2)
        self.assertIsNone(self.session.on_frame(frame(self.offer)))  # 同一画面不重复给

    def test_reroll_gives_new_advice(self):
        self.session.on_frame(frame(self.offer, rerolls=2))
        ev = self.session.on_frame(frame(["会员推荐信", "手电筒-强光", "优雅之羽"], rerolls=1))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.state.rerolls, 1)

    def test_picked_chip_is_recorded(self):
        self.session.on_frame(frame(self.offer))
        self.session.on_frame(frame(self.offer, selected=1))
        self.assertIsNone(self.session.on_frame(None))  # 只消失一帧还不算
        ev = self.session.on_frame(None)
        self.assertEqual((ev.kind, ev.chip), ("picked", "友情徽章"))
        self.assertEqual(self.session.settings.owned, ["友情徽章"])
        # 下一次三选一时，已有筹码进入计算
        ev = self.session.on_frame(frame(["摩托头盔-一般", "摩托头盔-中级", "速度轮滑-中级"]))
        self.assertEqual(ev.state.owned, ("友情徽章",))

    def test_selection_before_reroll_is_dropped(self):
        self.session.on_frame(frame(self.offer, selected=0))
        self.session.on_frame(frame(["会员推荐信", "手电筒-强光", "优雅之羽"], rerolls=1))
        self.session.on_frame(None)
        ev = self.session.on_frame(None)
        # 重选后的三个一直没看到选中：不记，并提示用户补记重选后的那三个
        self.assertEqual(ev.kind, "missed_pick")
        self.assertEqual(ev.offer, ["会员推荐信", "手电筒-强光", "优雅之羽"])
        self.assertEqual(self.session.settings.owned, [])

    def test_missed_pick_asks_user(self):
        self.session.on_frame(frame(self.offer))
        self.session.on_frame(None)
        ev = self.session.on_frame(None)
        self.assertEqual((ev.kind, ev.offer), ("missed_pick", self.offer))
        self.assertEqual(ev.raw, self.offer)
        self.assertEqual(self.session.game.missed_offer, self.offer)
        self.assertEqual(self.session.settings.owned, [])
        # 用户在悬浮窗里点了其中一个
        self.assertTrue(self.session.resolve_missed("友情徽章"))
        self.assertEqual(self.session.settings.owned, ["友情徽章"])
        self.assertIsNone(self.session.game.missed_offer)
        # 不补也可以；手改已有筹码同样会收起提示
        self.session.on_frame(frame(["会员推荐信", "手电筒-强光", "优雅之羽"]))
        self.session.on_frame(None)
        self.assertEqual(self.session.on_frame(None).kind, "missed_pick")
        self.assertFalse(self.session.resolve_missed(None))
        self.assertIsNone(self.session.game.missed_offer)
        self.assertEqual(self.session.settings.owned, ["友情徽章"])

    def test_nothing_recognised_is_not_a_missed_pick(self):
        self.session.on_frame(frame([None, None, None]))
        self.session.on_frame(None)
        self.assertEqual(self.session.on_frame(None).kind, "idle")
        self.assertIsNone(self.session.game.missed_offer)

    # ------------------------------------------------------------ 星数和三选一的来源

    def plain_chip(self, rarity):
        """一个这种稀有度的普通筹码（不是地图专属，也不和下面测试里的另两个候选重名）。"""
        return next(
            c.name for c in self.db.chips.values()
            if c.rarity == rarity and not c.maps and not c.granted and c.name not in ("友情徽章", "书签")
        )

    def test_unknown_star_is_guessed_from_probabilities_but_not_written_back(self):
        """读不到星数时，按概率行估的星数只用于这一次推荐（任务等级的金色概率不能写回星数）。"""
        purple = self.plain_chip("紫")  # 2 星升星的最左格是紫色（最左格不对时按任务算，见 LeftmostRarityTest）
        ev = self.session.on_frame(frame([purple, "友情徽章", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "levelup", None))
        self.assertEqual((self.session.settings.star, self.session.settings.star_read), (0, False))
        ev = self.session.on_frame(frame(self.offer, probabilities=(60, 37, 3), rerolls=1))
        self.assertEqual(ev.state.star, 0)

    def test_read_star_tells_zero_from_one(self):
        """0 星和 1 星的概率行相同，由读到的星数决定。"""
        self.assertEqual(self.session.on_stars(own(1)).kind, "status")
        ev = self.session.on_frame(frame(self.offer))
        self.assertEqual((ev.state.star, ev.state.source), (1, "levelup"))
        self.assertEqual(ev.state.rerolls, 2)  # 画面上读到的重选次数优先

    def test_row_not_matching_star_means_task(self):
        """知道星数时，概率行和星数对不上就是任务（或循环往复）来源，等级按概率行，星数不改。"""
        library = "异变图书馆"
        self.session.on_hud(hud(round=5, map=library))
        self.session.on_stars(own(1))
        self.session.on_stars(own(1), in_select=True)  # 三选一界面上也读到了 1 星：不是刚升星
        for probabilities, tier in (((40, 45, 15), 2), ((25, 50, 25), 3)):
            with self.subTest(probabilities=probabilities):
                ev = self.session.on_frame(frame(self.offer, probabilities=probabilities, rerolls=tier))
                self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "task", tier))
        self.assertEqual(self.session.settings.star, 1)
        # 2 星时任务等级 1 的三选一（概率行是 0~1 星那一行）
        self.session.on_stars(own(2))
        ev = self.session.on_frame(frame(self.offer, probabilities=(60, 37, 3), rerolls=4))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "task", 1))
        # 星数对得上、最左格也是 2 星的固定稀有度（紫）：按升星（筹码商店）算
        purple = self.plain_chip("紫")
        ev = self.session.on_frame(frame([purple, "友情徽章", "书签"], probabilities=(40, 45, 15), rerolls=5))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "levelup", None))
        # 最左格是蓝色：升星和筹码商店的最左格一定是固定稀有度，所以是任务 2 级
        ev = self.session.on_frame(frame(self.offer, probabilities=(40, 45, 15), rerolls=6))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "task", 2))

    def test_levelup_while_own_row_is_hidden(self):
        """升星的三选一出现时列表上已经亮了新的星；自己那一行被标题带挡住时，记下的还是升星前的星数。
        概率行高一星、最左格正好是那一星的固定稀有度（2 星是紫）时按升星算，否则是任务。"""
        purple, blue = self.plain_chip("紫"), self.plain_chip("蓝")
        self.session.on_stars(own(1))  # 升星前在对局画面上读到的
        self.session.on_stars(None, in_select=True)  # 三选一里自己那一行被挡住
        ev = self.session.on_frame(frame([purple, "友情徽章", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source), (2, "levelup"))
        self.assertEqual(self.session.settings.star, 1)  # 只用于这一次推荐，星数等关掉后再读
        ev = self.session.on_frame(frame([blue, "友情徽章", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "task", 2))
        # 关掉后读到 2 星：升星确实发生了，直接采用
        self.session.on_frame(None)
        self.session.on_frame(None)
        self.assertEqual(self.session.on_stars(own(2)).kind, "status")
        self.assertEqual(self.session.settings.star, 2)

    def test_fresh_star_in_chip_select_is_trusted(self):
        """三选一界面上读到了自己的星数：升星的那一颗已经算上，概率行高一星就是任务。"""
        purple = self.plain_chip("紫")
        self.session.on_stars(own(1))
        self.session.on_stars(own(1), in_select=True)
        ev = self.session.on_frame(frame([purple, "友情徽章", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "task", 2))
        # 升星：三选一里读到的已经是新的星数
        self.session.on_frame(None)
        self.session.on_frame(None)
        self.session.on_stars(own(2), in_select=True)
        ev = self.session.on_frame(frame([purple, "友情徽章", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source), (2, "levelup"))

    def test_unsure_star_reading_is_ignored(self):
        self.assertIsNone(self.session.on_stars(own(2, confidence=0.4)))
        self.assertIsNone(self.session.on_stars(None))
        self.assertFalse(self.session.settings.star_read)

    def test_new_game_detected_by_fewer_stars(self):
        self.session.on_hud(hud(round=9, map="异变图书馆"))
        self.session.set_owned(["书签"])
        self.session.on_stars(own(2))
        self.session.on_stars(own(2))  # 读到过两次：确认过了
        self.assertIsNone(self.session.on_stars(own(0)))  # 一次不算
        self.assertEqual(self.session.settings.owned, ["书签"])
        ev = self.session.on_stars(own(0))
        self.assertEqual(ev.kind, "newgame")
        self.assertEqual(ev.why, "星数由 2 星减少为 0 星")
        s = self.session.settings
        self.assertEqual((s.owned, s.map, s.star, s.star_read, s.round), ([], None, 0, True, 9))

    def test_fewer_stars_then_round_drop_is_one_new_game(self):
        """星星先变少、判了新一局，之后才读到这一局的轮次：留下的旧轮次直接改过来，不再清一次。"""
        clock = [0.0]
        self.session._clock = lambda: clock[0]
        self.session.on_hud(hud(round=12))
        self.session.on_hud(hud(round=12))
        self.session.on_stars(own(3))
        self.session.on_stars(own(3))
        clock[0] = 90.0  # 上一局的轮次是很久以前读到的（结算、选角、加载之后）
        self.session.on_stars(own(0))
        self.assertEqual(self.session.on_stars(own(0)).kind, "newgame")
        self.session.set_owned(["书签"])  # 新一局里记下的筹码
        self.assertIsNone(self.session.on_hud(hud(round=1)))
        self.assertEqual(self.session.on_hud(hud(round=1)).kind, "status")
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (1, ["书签"]))

    def test_misread_star_does_not_reset(self):
        """只读到过一次的星数之后变少，是改正读错，不是新一局；跳两颗以上要连续读到。"""
        self.session.on_stars(own(1))
        self.session.on_stars(own(1))
        self.session.set_owned(["书签"])
        self.assertEqual(self.session.on_stars(own(2)).kind, "status")  # 多一颗：直接采用，但只读到一次
        self.assertIsNone(self.session.on_stars(own(1)))
        self.assertEqual(self.session.on_stars(own(1)).kind, "status")
        self.assertEqual((self.session.settings.star, self.session.settings.owned), (1, ["书签"]))
        self.assertIsNone(self.session.on_stars(own(3)))  # 跳两颗：一次不算
        self.assertEqual(self.session.settings.star, 1)
        self.assertEqual(self.session.on_stars(own(3)).kind, "status")
        self.assertEqual(self.session.settings.star, 3)

    def test_repeated_misread_star_jump_does_not_reset(self):
        """星数同理：1 → 3（读错，连读了几次）→ 1 → 1 只是改正，不清已有筹码。"""
        self.session.on_stars(own(1))
        self.session.on_stars(own(1))
        self.session.set_owned(["书签"])
        self.assertIsNone(self.session.on_stars(own(3)))
        self.assertEqual(self.session.on_stars(own(3)).kind, "status")
        self.assertIsNone(self.session.on_stars(own(3)))
        self.assertIsNone(self.session.on_stars(own(1)))
        self.assertEqual(self.session.on_stars(own(1)).kind, "status")
        self.assertEqual((self.session.settings.star, self.session.settings.owned), (1, ["书签"]))

    def test_fewer_stars_in_chip_select_counts(self):
        """队友不能和自己同角色，三选一界面上读到的星星变少和对局画面上的一样算数（纯手动模式几乎只看三选一）。"""
        self.session.on_stars(own(2))
        self.session.on_stars(own(2))
        self.session.set_owned(["书签"])
        self.assertIsNone(self.session.on_stars(own(1), in_select=True))  # 一次不算
        self.assertIsNone(self.session.on_stars(None))  # 读不到不打断确认
        ev = self.session.on_stars(own(1), in_select=True)
        self.assertEqual(ev.kind, "newgame")
        self.assertEqual((self.session.settings.star, self.session.settings.owned), (1, []))

    def test_fewer_stars_ends_hero_lock_like_round(self):
        """手动锁定角色时，「星星变少」和「轮次变小」一样算新一局，并解除锁定。"""
        self.session.set_hero("邦妮")
        self.assertEqual(self.session.stars_hero, "邦妮")
        self.session.on_stars(own(3))
        self.session.on_stars(own(3))
        self.session.on_stars(own(0))
        self.assertEqual(self.session.on_stars(own(0)).kind, "newgame")
        self.assertFalse(self.session.game.hero_locked)

    def test_stars_hero_only_when_hero_is_known(self):
        """角色还没认出来时不拿上一局的角色去认自己那一行（可能正好是队友的角色）。"""
        self.fresh()
        self.assertIsNone(self.session.stars_hero)
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.assertEqual(self.session.stars_hero, "凛")

    def test_restored_star(self):
        """启动参数（命令行的 --star）给的、读到过的星数算确认过；没标「读到过」的不算。"""
        read = Session(self.db, Settings(star=2, star_read=True, owned=["书签"]))
        read.on_stars(own(0))
        self.assertEqual(read.on_stars(own(0)).kind, "newgame")
        old = Session(self.db, Settings(star=2, owned=["书签"]))
        self.assertEqual(old.on_stars(own(0)).kind, "status")
        self.assertEqual((old.settings.star, old.settings.owned), (0, ["书签"]))

    def test_unreadable_offer(self):
        """三选一刚出现就有一格读不出：先等一等（不发事件），隔半秒以上再完整识别一次仍读不出才提示无法识别；同一组读数
        不重复提示（等的规矩见 FirstUnreadTest）。"""
        clock = [100.0]
        self.session._clock = lambda: clock[0]
        self.assertIsNone(self.session.on_frame(frame(["小猪存钱罐", None, "书签"])))
        clock[0] += 0.6
        ev = self.session.on_frame(frame(["小猪存钱罐", None, "书签"]))
        self.assertEqual(ev.kind, "unreadable")
        self.assertIsNone(self.session.on_frame(frame(["小猪存钱罐", None, "书签"])))

    def test_hud_updates_round_and_map(self):
        h = hud(round=3, map="异变图书馆")
        self.assertEqual(self.session.on_hud(h).kind, "status")
        self.assertIsNone(self.session.on_hud(h))  # 没变化就不重复报
        ev = self.session.on_frame(frame(self.offer))
        self.assertEqual((ev.state.round, ev.state.map), (3, "异变图书馆"))
        self.assertEqual(self.session.map_source, "tasks")
        # 读不到（None）时沿用上一次的值
        self.assertIsNone(self.session.on_hud(hud()))
        self.assertEqual(self.session.settings.round, 3)

    def test_manual_map_is_not_overwritten(self):
        self.session.set_map("魔法学院")
        self.assertFalse(self.session.wants_map)
        self.assertEqual((self.session.map_source, self.session.map_confidence), ("manual", "high"))
        self.session.on_hud(hud(round=2, map="异变图书馆"))
        self.assertEqual(self.session.settings.map, "魔法学院")
        library = next(c.name for c in self.db.chips.values() if c.maps == ("异变图书馆",))
        self.session.on_frame(frame([library, "友情徽章", "书签"]))  # 专属筹码反推也不覆盖手动选的
        self.assertEqual(self.session.settings.map, "魔法学院")
        self.session.set_map(None)
        self.assertTrue(self.session.wants_map)
        self.assertIsNone(self.session.map_source)

    # ------------------------------------------------------------ 轮次和新的一局

    def test_new_game_detected_by_round_going_down(self):
        self.session.set_owned(["书签"])
        self.session.on_hud(hud(round=7, map="异变图书馆"))
        self.session.on_hud(hud(round=7))  # 第 7 轮读到过两次：确认过了
        self.assertIsNone(self.session.on_hud(hud(round=3)))  # 一次不算
        self.assertEqual(self.session.settings.owned, ["书签"])
        ev = self.session.on_hud(hud(round=3))
        self.assertEqual(ev.kind, "newgame")
        self.assertEqual(ev.why, "轮次由第 7 轮变为第 3 轮")
        self.assertEqual((self.session.settings.owned, self.session.settings.map), ([], None))
        self.assertEqual(self.session.settings.round, 3)

    def test_misread_round_does_not_reset(self):
        self.session.set_owned(["书签"])
        self.session.on_hud(hud(round=7))
        self.session.on_hud(hud(round=1))
        self.session.on_hud(hud(round=8))
        self.session.on_hud(hud(round=1))
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertEqual(self.session.settings.round, 8)

    def test_misread_as_bigger_round_is_ignored(self):
        """实测过的情形：3 → 8（读错）→ 3 → 3。既不能当成新一局，也不能把轮次改成 8。"""
        self.session.on_hud(hud(round=3, map="异变图书馆"))
        self.session.on_hud(hud(round=3))
        self.session.set_owned(["书签"])
        self.assertIsNone(self.session.on_hud(hud(round=8)))
        self.assertEqual(self.session.settings.round, 3)
        self.assertIsNone(self.session.on_hud(hud(round=3)))
        self.assertIsNone(self.session.on_hud(hud(round=3)))
        s = self.session.settings
        self.assertEqual((s.round, s.owned, s.map), (3, ["书签"], "异变图书馆"))

    def test_misread_round_seen_once_is_corrected_not_new_game(self):
        """往前一小步直接采用，但只读到一次；之后连续读回原来的数，是改正读错，不是新一局。"""
        self.session.on_hud(hud(round=3))
        self.session.on_hud(hud(round=3))
        self.session.set_owned(["书签"])
        self.assertEqual(self.session.on_hud(hud(round=4)).kind, "status")
        self.assertIsNone(self.session.on_hud(hud(round=3)))
        self.assertEqual(self.session.on_hud(hud(round=3)).kind, "status")
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (3, ["书签"]))

    def test_repeated_misread_jump_does_not_reset(self):
        """3 → 8 → 8 → 3 → 3：同一个静止画面读错一次，往往会原样再错几次。靠连续读到采用的大幅跳变
        读到再多次也不算这一局确实到过；之后读回真实的轮次只是改正，不是新一局。"""
        for extra in (0, 3):
            with self.subTest(extra_reads=extra):
                session = Session(self.db, Settings(hero="远野汉娜"))
                session.on_hud(hud(round=3, map="异变图书馆"))
                session.on_hud(hud(round=3))
                session.set_owned(["书签"])
                self.assertIsNone(session.on_hud(hud(round=8)))
                self.assertEqual(session.on_hud(hud(round=8)).kind, "status")  # 连读两次：先采用
                for _ in range(extra):
                    self.assertIsNone(session.on_hud(hud(round=8)))
                self.assertIsNone(session.on_hud(hud(round=3)))
                self.assertEqual(session.on_hud(hud(round=3)).kind, "status")  # 改正，不是新一局
                s = session.settings
                self.assertEqual((s.round, s.owned, s.map), (3, ["书签"], "异变图书馆"))

    def test_new_game_after_round_jumps(self):
        """纯手动模式下轮次常常一跳好几轮。跳之前确实到过（一步步走到、读到过两次）的轮次算：新一局的轮次比它小就认得出。
        跳变来的轮次再往前跳时不算确实到过（静止画面上连着读错两次也是这个样子）：比它小、比确实到过的大的按改正读错处理。"""
        self.session.set_owned(["书签"])
        for rnd in (2, 2, 5, 5, 9, 9):
            self.session.on_hud(hud(round=rnd))
        self.assertEqual(self.session.settings.round, 9)
        self.assertIsNone(self.session.on_hud(hud(round=4)))
        self.assertEqual(self.session.on_hud(hud(round=4)).kind, "status")  # 第 5 轮是跳变来的：不算确实到过
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (4, ["书签"]))
        for rnd in (9, 9):
            self.session.on_hud(hud(round=rnd))
        self.assertIsNone(self.session.on_hud(hud(round=1)))
        self.assertEqual(self.session.on_hud(hud(round=1)).kind, "newgame")  # 比确实到过的第 4 轮小
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (1, []))

    def test_hero_change_then_round_drop_is_one_new_game(self):
        """角色换了判新一局后，留下的旧轮次算没确认过：之后读到这一局的小轮次直接改过来，不再清一次。"""
        self.fresh()
        clock = [0.0]
        self.session._clock = lambda: clock[0]
        self.session.on_hud(hud(round=9))
        self.session.on_hud(hud(round=9))
        self.session.on_hero(SimpleNamespace(hero="凛"))
        clock[0] = 90.0  # 结算、大厅、选角、加载之后
        self.session.on_hud(hud(round=1))  # 新一局第一次读到第 1 轮（等确认）
        self.session.on_hero(SimpleNamespace(hero="阿尔"))
        self.assertEqual(self.session.on_hero(SimpleNamespace(hero="阿尔")).kind, "newgame")
        self.session.set_owned(["缓冲盾牌"])  # 新一局里记下的筹码
        self.assertEqual(self.session.on_hud(hud(round=1)).kind, "status")
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (1, ["缓冲盾牌"]))

    def test_round_zero_is_ignored(self):
        """「04」读成「00」：第 0 轮不存在，读到再多次也不判新一局（实例：1560x834 的静止画面）。"""
        self.session.set_owned(["书签"])
        for _ in range(3):
            self.session.on_hud(hud(round=4))
        for _ in range(3):
            self.assertIsNone(self.session.on_hud(hud(round=0)))
        self.assertIsNone(self.session.on_hud(hud(round=88)))
        self.assertIsNone(self.session.on_hud(hud(round=88)))
        self.assertEqual((self.session.settings.round, self.session.settings.owned), (4, ["书签"]))

    def test_round_drop_to_large_value_is_not_new_game(self):
        """新的一局从第 1 轮开始：变小后的轮次较大时多半是读错一位数字（14 → 11），按改正处理，不清空记录。"""
        self.session.set_owned(["书签"])
        for rnd in (13, 13, 14, 14):
            self.session.on_hud(hud(round=rnd))
        self.assertIsNone(self.session.on_hud(hud(round=11)))
        self.assertEqual(self.session.on_hud(hud(round=11)).kind, "status")
        self.assertEqual(self.session.settings.owned, ["书签"])

    def test_big_round_jump_needs_two_reads(self):
        self.session.on_hud(hud(round=3))
        self.assertIsNone(self.session.on_hud(hud(round=9)))
        self.assertIsNone(self.session.on_hud(hud()))  # 读不到不打断确认
        self.assertEqual(self.session.on_hud(hud(round=9)).kind, "status")
        self.assertEqual(self.session.settings.round, 9)
        # 一步一步往前走的轮次直接采用
        self.assertEqual(self.session.on_hud(hud(round=10)).kind, "status")
        self.assertEqual(self.session.settings.round, 10)

    def test_round_restored_from_file_counts_as_confirmed(self):
        session = Session(self.db, Settings(round=9, owned=["书签"]))
        session.on_hud(hud(round=1))
        self.assertEqual(session.on_hud(hud(round=1)).kind, "newgame")
        self.assertEqual(session.settings.owned, [])

    def test_new_game_detected_by_select_screen(self):
        self.fresh()
        self.assertIsNone(self.session.on_select_screen())  # 还没有任何对局状态：不用清
        self.session.set_owned(["书签"])
        ev = self.session.on_select_screen()
        self.assertEqual(ev.kind, "newgame")
        self.assertEqual(self.session.settings.owned, [])
        self.assertIsNone(self.session.on_select_screen())  # 停在选角界面不会反复触发

    def test_new_game_detected_by_hero_change(self):
        self.fresh()
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.session.set_owned(["书签"])
        self.assertIsNone(self.session.on_hero(SimpleNamespace(hero="远野汉娜")))  # 一次不算
        self.assertEqual(self.session.settings.owned, ["书签"])
        ev = self.session.on_hero(SimpleNamespace(hero="远野汉娜"))
        self.assertEqual(ev.kind, "newgame")
        self.assertEqual((self.session.settings.hero, self.session.settings.owned), ("远野汉娜", []))
        self.assertEqual(self.session.hero_source, "auto")

    def test_hero_guess(self):
        self.fresh()
        self.assertEqual(self.session.hero_source, "pending")
        self.assertIsNone(self.session.on_hero(SimpleNamespace(hero=None)))  # 没把握：不改
        self.assertEqual(self.session.hero_source, "pending")
        self.assertEqual(self.session.on_hero(SimpleNamespace(hero="凛")).kind, "status")
        self.assertEqual(self.session.settings.hero, "凛")
        self.assertEqual(self.session.hero_source, "auto")

    def test_manual_hero_wins_and_can_be_released(self):
        self.fresh()
        self.session.set_hero("邦妮")
        self.assertEqual(self.session.hero_source, "manual")
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.assertEqual(self.session.settings.hero, "邦妮")  # 手动选的不被覆盖
        self.assertEqual(self.session.game.seen_hero, "凛")  # 但识别结果记着，界面上能提示
        self.session.release_hero()
        self.assertEqual((self.session.settings.hero, self.session.hero_source), ("凛", "auto"))

    def test_locked_hero_ignores_hero_change(self):
        """手动锁定时，识别连续认成别的角色也不算新一局，锁定也不解除。"""
        self.fresh()
        self.session.set_hero("邦妮")
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.session.set_owned(["书签"])
        self.session.on_hud(hud(round=4, map="异变图书馆"))
        for _ in range(3):
            ev = self.session.on_hero(SimpleNamespace(hero="远野汉娜"))
            self.assertTrue(ev is None or ev.kind == "status")
        s = self.session.settings
        self.assertEqual((s.hero, s.owned, s.round, s.map), ("邦妮", ["书签"], 4, "异变图书馆"))
        self.assertEqual(self.session.hero_source, "manual")
        self.assertEqual(self.session.game.seen_hero, "远野汉娜")  # 识别结果更新了，界面上提示用
        # 选角界面仍然算新一局，并解除锁定
        self.assertEqual(self.session.on_select_screen().kind, "newgame")
        self.assertEqual(self.session.hero_source, "pending")
        # 解除后，「角色换了」重新是新一局的信号
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.session.set_owned(["书签"])
        self.session.on_hero(SimpleNamespace(hero="远野汉娜"))
        self.assertEqual(self.session.on_hero(SimpleNamespace(hero="远野汉娜")).kind, "newgame")

    def test_locked_hero_released_by_round_going_down(self):
        self.session.set_hero("邦妮")
        self.session.on_hud(hud(round=6))
        self.session.on_hud(hud(round=6))
        self.session.on_hud(hud(round=1))
        self.assertEqual(self.session.on_hud(hud(round=1)).kind, "newgame")
        self.assertFalse(self.session.game.hero_locked)

    def test_hero_lock_not_kept_after_restart(self):
        """手动指定的角色只在本次运行中锁定：重启后从上次的角色开始，但一律重新识别。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            path = Path(tmp) / "state.json"
            session = Session(self.db, Settings.load(path), path)
            session.set_hero("邦妮")
            session.on_hero(SimpleNamespace(hero="凛"))
            self.assertEqual((session.settings.hero, session.hero_source), ("邦妮", "manual"))  # 本次运行中有效
            again = Session(self.db, Settings.load(path), path)
            self.assertEqual((again.settings.hero, again.hero_source), ("邦妮", "pending"))
            self.assertFalse(again.game.hero_locked)
            again.on_hero(SimpleNamespace(hero="凛"))
            self.assertEqual((again.settings.hero, again.hero_source), ("凛", "auto"))
            # 旧版 state.json 里存着的锁定也不再生效
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("hero_locked", data)
            data["hero_locked"] = True
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(Session(self.db, Settings.load(path), path).game.hero_locked)

    # ------------------------------------------------------------ 地图

    def test_map_from_exclusive_chip_is_written_back(self):
        """第③层：候选里有魔法学院专属筹码，反推出的地图记成当前地图，不再请用户点任务栏。"""
        chip = next(c.name for c in self.db.chips.values() if c.maps == ("魔法学院",))
        self.assertTrue(self.session.wants_map)
        ev = self.session.on_frame(frame(["小猪存钱罐", chip, "书签"]))
        self.assertEqual(ev.state.map, "魔法学院")
        s = self.session
        self.assertEqual((s.settings.map, s.map_source, s.map_confidence), ("魔法学院", "chips", "high"))
        self.assertFalse(s.wants_map)
        self.assertFalse(s.wants_loading)
        # 之后任务栏的读数改不掉它
        s.on_hud(hud(round=3, map="异变图书馆"))
        self.assertEqual(s.settings.map, "魔法学院")

    def test_shared_chip_narrows_candidates(self):
        arena = [c.name for c in self.db.chips.values() if len(c.maps) == 3]
        self.session.on_frame(frame(["小猪存钱罐", arena[0], "书签"]))
        self.assertIsNone(self.session.settings.map)
        self.assertEqual(set(self.session.game.map_candidates), set(self.db.chips[arena[0]].maps))
        # 再看到只属于决赛大赛场的筹码：交叉后只剩一张
        final = next(c.name for c in self.db.chips.values() if c.maps == ("决赛大赛场",))
        self.session.set_owned([arena[0]])
        self.session.on_frame(frame(["小猪存钱罐", final, "书签"]))
        self.assertEqual((self.session.settings.map, self.session.map_source), ("决赛大赛场", "chips"))

    def test_chip_overrides_wrong_task_guess(self):
        """任务栏「较有把握」地认成魔法学院，但已有筹码是运动场专属的：以筹码为准。"""
        arena = next(c.name for c in self.db.chips.values() if len(c.maps) == 3)
        self.session.on_hud(hud(round=2, map="魔法学院", map_confidence="medium",
                                map_candidates=["魔法学院", "海选赛运动场"], tasks_seen=["击倒魔法茶壶"]))
        self.assertEqual(self.session.map_confidence, "medium")
        self.assertTrue(self.session.wants_map)  # 任务栏还开着：继续读，可能读到更准的
        self.session.on_frame(frame(["小猪存钱罐", arena, "书签"]))
        self.assertEqual((self.session.settings.map, self.session.map_source), ("海选赛运动场", "chips"))

    def test_map_ask_when_task_panel_is_inconclusive(self):
        """第④层：任务栏读过了仍然打平，请用户从候选里选。"""
        candidates = ["龙宫游乐园", "淘汰赛运动场"]
        ev = self.session.on_hud(hud(round=2, map_candidates=candidates, map_confidence="low",
                                     tasks_seen=["击倒虾布罗", "击倒蟹布罗"]))
        self.assertEqual(ev.kind, "status")
        self.assertTrue(self.session.game.map_ask)
        self.assertEqual(self.session.game.map_candidates, candidates)
        self.assertIsNone(self.session.map_source)
        self.assertTrue(self.session.wants_map)
        self.assertIsNone(self.session.on_hud(hud(round=2, map_candidates=candidates, tasks_seen=["击倒虾布罗"])))
        self.session.set_map("龙宫游乐园")
        self.assertFalse(self.session.game.map_ask)
        self.assertEqual(self.session.map_source, "manual")

    def test_task_panel_tie_resolved_by_owned_chip(self):
        arena = next(c.name for c in self.db.chips.values() if len(c.maps) == 3)
        self.session.set_owned([arena])
        self.session.on_hud(hud(round=2, map_candidates=["龙宫游乐园", "淘汰赛运动场"], map_confidence="low",
                                tasks_seen=["击倒虾布罗"]))
        self.assertEqual((self.session.settings.map, self.session.map_confidence), ("淘汰赛运动场", "medium"))
        self.assertFalse(self.session.game.map_ask)

    def test_marker_alone_does_not_ask(self):
        self.session.on_hud(hud(round=1, map_candidates=["异变图书馆"], map_confidence="low", tasks_seen=[]))
        self.assertFalse(self.session.game.map_ask)
        self.assertIsNone(self.session.settings.map)

    def test_medium_match_is_used_without_asking(self):
        """匹配度中（魔法学院的任务全读到了，和海选赛运动场条数打平）：直接采用，不请用户选。"""
        ev = self.session.on_hud(hud(round=2, map="魔法学院", map_confidence="medium",
                                     map_candidates=["魔法学院", "海选赛运动场"], tasks_seen=["击倒魔法茶壶"]))
        self.assertEqual(ev.kind, "status")
        s = self.session
        self.assertEqual((s.settings.map, s.map_source, s.map_confidence), ("魔法学院", "tasks", "medium"))
        self.assertFalse(s.game.map_ask)
        self.assertEqual(s.game.map_candidates, ["魔法学院", "海选赛运动场"])  # 界面的悬停说明用来写「任务部分重叠」

    def test_single_weak_match_asks(self):
        """任务栏开着但只对上一条任务（匹配度低）：请用户确认这一张。"""
        self.session.on_hud(hud(round=2, map_candidates=["御魂庆典"], map_confidence="low", tasks_seen=["击倒狐火"]))
        self.assertTrue(self.session.game.map_ask)
        self.assertEqual(self.session.game.map_candidates, ["御魂庆典"])
        self.assertIsNone(self.session.settings.map)

    def test_low_match_after_reliable_map_keeps_it(self):
        """已经有可靠的地图后，任务栏又读成打平：保留原来的地图，不请用户选。"""
        self.session.on_hud(hud(round=2, map="淘汰赛运动场", map_confidence="high",
                                map_candidates=["淘汰赛运动场"], tasks_seen=["击倒虾布罗", "击倒裁判"]))
        self.session.on_hud(hud(round=2, map_candidates=["龙宫游乐园", "淘汰赛运动场"], map_confidence="low",
                                tasks_seen=["击倒虾布罗"]))
        self.assertEqual(self.session.settings.map, "淘汰赛运动场")
        self.assertFalse(self.session.game.map_ask)

    def test_chip_contradicting_map_asks_among_chip_maps(self):
        """已有的地图和专属筹码对不上、专属筹码又对应几张地图：几张地图打平，请用户从中选。"""
        self.session.on_hud(hud(round=2, map="异变图书馆", map_candidates=["异变图书馆"], tasks_seen=["击倒魔法茶壶"]))
        arena = next(c.name for c in self.db.chips.values() if len(c.maps) == 3)
        self.session.on_frame(frame(["小猪存钱罐", arena, "书签"]))
        self.assertIsNone(self.session.settings.map)
        self.assertTrue(self.session.game.map_ask)
        self.assertEqual(set(self.session.game.map_candidates), set(self.db.chips[arena].maps))

    def test_loading_screen_map(self):
        self.assertTrue(self.session.wants_loading)
        self.assertEqual(self.session.on_loading("园林中庭").kind, "status")
        s = self.session
        self.assertEqual((s.settings.map, s.map_source, s.map_confidence), ("园林中庭", "loading", "high"))
        self.assertFalse(s.wants_loading)
        self.assertFalse(s.wants_map)  # 第①层认准了，不再提示点任务栏
        self.assertIsNone(s.on_loading("魔法学院"))  # 地图已知时不再采用
        self.assertEqual(s.settings.map, "园林中庭")
        s.new_game()
        self.assertTrue(s.wants_loading)

    def test_game_state_not_restored_on_restart(self):
        """启动时不沿用 state.json 里的对局状态（已有筹码、轮次、星数、地图、来源），全部重新识别；
        角色（作为待识别时的显示）、窗口位置这些设置照常保存。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            path = Path(tmp) / "state.json"
            session = Session(self.db, Settings(hero="凛", window_pos=[10, 20]), path)
            session.on_loading("水乡古镇")
            session.on_hud(hud(round=5))
            session.on_stars(own(1))
            session.set_owned(["书签"])
            session.set_map("魔法学院")
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("owned", "round", "star", "star_read", "map", "map_source", "map_confidence", "source"):
                self.assertNotIn(key, data)
            again = Session(self.db, Settings.load(path), path)
            s = again.settings
            self.assertEqual((s.owned, s.round, s.star, s.star_read, s.map, s.map_source), ([], None, 0, False, None, None))
            self.assertEqual((s.hero, s.window_pos), ("凛", [10, 20]))
            self.assertFalse(again.map_locked)
            self.assertTrue(again.game.panel_needed)  # 已有筹码要重新读「查看筹码」面板
            # 旧版 state.json 里存着的对局状态也不沿用
            old = dict(data, owned=["书签"], round=7, star=2, star_read=True, map="水乡古镇", map_source="manual")
            path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            s = Settings.load(path)
            self.assertEqual((s.owned, s.round, s.star, s.map, s.map_source), ([], None, 0, None, None))

    def test_unchanged_settings_not_rewritten(self):
        """设置没变时不重写 state.json（文件放在同步盘里时，少写一次就少同步一次）；对局状态变了也不写。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            path = Path(tmp) / "state.json"
            session = Session(self.db, Settings(hero="凛"), path)
            session.save()
            stamp = path.stat().st_mtime_ns
            with mock.patch.object(Path, "write_text") as write:
                session.on_hud(hud(round=3))
                session.set_owned(["书签"])
                write.assert_not_called()
            self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_task_probabilities_are_used_for_reroll(self):
        ev = self.session.on_frame(frame(self.offer, probabilities=(25, 50, 25)))
        self.assertEqual(ev.state.probabilities, (25, 50, 25))

    def test_set_owned_and_new_game(self):
        self.assertEqual(self.session.set_owned(["友情微章", "8面骰子", "不存在的"]), ["不存在的"])
        self.assertEqual(self.session.settings.owned, ["友情徽章", "“8面”骰子"])
        self.assertEqual(self.session.games, 0)
        self.session.new_game()
        self.assertEqual(self.session.settings.owned, [])
        self.assertEqual(self.session.games, 1)  # 悬浮窗靠它发现「输入期间换了局」

    def test_settings_round_trip(self):
        """跨对局的设置原样存取；对局状态不存。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            path = Path(tmp) / "state.json"
            s = Settings(hero="邦妮", owned=["标靶"], window_pos=[10, 20],
                         map="园林中庭", map_source="tasks", map_confidence="medium")
            s.save(path)
            self.assertEqual(Settings.load(path), Settings(hero="邦妮", window_pos=[10, 20]))
            self.assertEqual(Settings.load(Path(tmp) / "missing.json"), Settings())
            path.write_text("[1, 2]", encoding="utf-8")  # 内容不对时用默认值
            self.assertEqual(Settings.load(path), Settings())


OFFER_A = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]
OFFER_B = ["会员推荐信", "手电筒-强光", "优雅之羽"]


def ready(db, hero="远野汉娜") -> Session:
    """角色已识别的 Session。"""
    session = Session(db, Settings(hero=hero))
    session.game.seen_hero = hero
    return session


def kinds(session, kind=None) -> list:
    """取走待取的事件；给了 kind 时只留这一种。"""
    return [e for e in session.take_events() if kind is None or e.kind == kind]


def close(session):
    """三选一界面关掉（连续两帧看不到），返回关掉时的事件。"""
    session.on_frame(None)
    return session.on_frame(None)


class StartupPanelTest(unittest.TestCase):
    """启动时不知道已有筹码。进入对局后请用户打开「查看筹码」面板，读到自己那一页后写入已有筹码。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db, "凛")

    def reading(self, owned, self_tab=True, unknown=0, confidence=0.9, is_panel=True):
        return SimpleNamespace(is_panel=is_panel, self_tab=self_tab, owned=list(owned), unknown_slots=unknown,
                               confidence=confidence)

    def test_prompt_once_when_entering_game(self):
        s = self.session
        self.assertTrue(s.game.panel_needed)
        self.assertEqual(kinds(s), [])
        s.on_hud(hud(round=4))
        self.assertEqual([e.kind for e in kinds(s)], ["panel_prompt"])
        s.on_hud(hud(round=5))
        s.on_stars(own(1))
        s.on_frame(frame(OFFER_A))
        self.assertEqual(kinds(s, "panel_prompt"), [])  # 只提示一次
        self.assertTrue(s.game.panel_needed)  # 读到面板之前状态行一直提示
        s.skip_panel()  # 用户关闭提示
        self.assertFalse(s.game.panel_needed)

    def test_no_prompt_when_game_seen_from_select_screen(self):
        s = Session(self.db, Settings(hero="凛"))
        s.on_select_screen()
        s.on_hud(hud(round=1))
        self.assertEqual(kinds(s, "panel_prompt"), [])
        self.assertFalse(s.game.panel_needed)

    def test_no_prompt_when_owned_given(self):
        s = Session(self.db, Settings(hero="凛", owned=["书签"]))  # 命令行的 --owned
        s.on_hud(hud(round=4))
        self.assertEqual(kinds(s, "panel_prompt"), [])
        self.assertEqual(s.game.chip_sources, {"书签": "given"})

    def test_panel_reading_writes_owned(self):
        s = self.session
        s.on_hud(hud(round=4))
        kinds(s)
        self.assertIsNone(s.on_panel(None))
        self.assertIsNone(s.on_panel(self.reading(["书签"], is_panel=False)))
        self.assertIsNone(s.on_panel(self.reading(["书签"], self_tab=False)))  # 队友那一页不算
        self.assertIsNone(s.on_panel(self.reading(["书签"], confidence=0.2)))
        # 资料库里没有的名字、重复的那一格都算认不出（那两格里真正的筹码说不准）
        ev = s.on_panel(self.reading(["书签", "标靶", "不存在的", "书签"]))
        self.assertEqual((ev.kind, ev.owned, ev.count), ("panel_read", ["书签", "标靶"], 2))
        self.assertEqual(s.settings.owned, ["书签", "标靶"])
        self.assertEqual(s.game.chip_sources, {"书签": "panel", "标靶": "panel"})
        self.assertFalse(s.game.panel_needed)
        self.assertIsNone(s.on_panel(self.reading(["书签", "标靶", "不存在的", "书签"])))  # 同样的读数不重复报
        ev = s.on_frame(frame(OFFER_A))
        self.assertEqual(ev.state.owned, ("书签", "标靶"))  # 推荐用上
        close(s)

    def test_unknown_slots_keep_recorded_chips(self):
        """面板上有没认出的格子：原来记着、面板上没认出的筹码保留（最多保留没认出的格数）；其余以面板为准。"""
        s = self.session
        s.set_owned(["书签", "标靶", "大背包", "小猪存钱罐"])
        ev = s.on_panel(self.reading(["书签"], unknown=1))
        self.assertEqual(ev.count, 1)
        self.assertEqual(s.settings.owned, ["书签", "标靶"])
        self.assertEqual(s.game.chip_sources["书签"], "panel")
        self.assertEqual(s.game.chip_sources["标靶"], "manual")
        s.on_panel(self.reading(["书签"]))
        self.assertEqual(s.settings.owned, ["书签"])

    def test_unknown_slot_keeps_position(self):
        """保留的原记录放回它原来的位置（插在原记录里排在它前面的筹码之后），不接在末尾。"""
        s = self.session
        s.set_owned(["书签", "标靶", "大背包"])
        ev = s.on_panel(self.reading(["书签", "大背包"], unknown=1))
        self.assertEqual(s.settings.owned, ["书签", "标靶", "大背包"])
        self.assertEqual((ev.owned, ev.count), (["书签", "标靶", "大背包"], 1))
        s.set_owned(["书签", "标靶"])
        s.on_panel(self.reading(["标靶"], unknown=1))
        self.assertEqual(s.settings.owned, ["书签", "标靶"])

    def test_unknown_slot_position_from_slots(self):
        """读数带每一格的结果（PanelReading.slots）时按格子位置放：认不出的格子（被遮挡、和前面重复）
        按顺序放保留的原记录，认出的格子放认出的筹码。"""
        def slot(index, state, chip=None):
            return SimpleNamespace(index=index, state=state, chip=chip)

        def empty(start):
            return [slot(i, "empty") for i in range(start, 10)]

        s = self.session
        s.set_owned(["书签", "标靶"])
        reading = self.reading(["标靶", "大背包"], unknown=1)
        reading.slots = [slot(0, "hidden"), slot(1, "chip", "标靶"), slot(2, "chip", "大背包")] + empty(3)
        s.on_panel(reading)
        self.assertEqual(s.settings.owned, ["书签", "标靶", "大背包"])
        # 原记录的先后和面板不同时以面板的格子为准：第 1 格是标靶，认不出的第 2 格放保留的书签
        s.set_owned(["书签", "标靶"])
        reading = self.reading(["标靶", "大背包"], unknown=1)
        reading.slots = [slot(0, "chip", "标靶"), slot(1, "hidden"), slot(2, "chip", "大背包")] + empty(3)
        s.on_panel(reading)
        self.assertEqual(s.settings.owned, ["标靶", "书签", "大背包"])
        # 第 3 格被认成和第 2 格相同（重复的那一格也算认不出）：按格子位置放回保留的小猪存钱罐
        s.set_owned(["书签", "标靶", "小猪存钱罐", "大背包"])
        reading = self.reading(["标靶", "标靶", "大背包"], unknown=1)
        reading.slots = [slot(0, "hidden"), slot(1, "chip", "标靶"), slot(2, "chip", "标靶"),
                         slot(3, "chip", "大背包")] + empty(4)
        ev = s.on_panel(reading)
        self.assertEqual(s.settings.owned, ["书签", "标靶", "小猪存钱罐", "大背包"])
        self.assertEqual(ev.count, 2)

    def test_full_page_is_everything(self):
        """只有一页（读数没有页码时按只有一页算）：10 格都满时面板也是全部，面板上没有的、原来记着的筹码不保留；
        有认不出的格子时照常最多保留认不出的格数。面板的识别提示随事件交给悬浮窗。"""
        s = self.session
        names = [c.name for c in self.db.chips.values() if not c.maps and not c.granted][:12]
        s.set_owned(names[10:])
        reading = self.reading(names[:10])
        reading.page_full = True
        reading.problems = ["「大铜锣」可能为无效图标"]
        ev = s.on_panel(reading)
        self.assertEqual(s.settings.owned, names[:10])
        self.assertEqual(ev.problems, ["「大铜锣」可能为无效图标"])
        s.set_owned(names[2:11])
        reading = self.reading(names[:9])
        reading.page_full, reading.unknown_slots = True, 1
        s.on_panel(reading)
        self.assertEqual(s.settings.owned, names[:9] + [names[9]])
        # 10 格都满，其中一格被认成和另一格相同（识别提示「识别到重复的筹码」）：那一格算认不出，原来记着的第 10 个留着
        s.set_owned(names[:10])
        reading = self.reading(names[:9] + names[:1])
        reading.page_full = True
        s.on_panel(reading)
        self.assertEqual(s.settings.owned, names[:10])

    def test_panel_clears_missed_prompt(self):
        s = self.session
        s.on_frame(frame(OFFER_A))
        self.assertEqual(close(s).kind, "missed_pick")
        s.on_panel(self.reading(["友情徽章"]))
        self.assertIsNone(s.game.missed_offer)
        self.assertEqual(s.settings.owned, ["友情徽章"])


class LobbyStartupPanelTest(unittest.TestCase):
    """刚打开时在大厅（还没有对局状态时读到过主菜单、房间、「匹配成功」画面、结算画面、奖励画面），之后才进的对局：进对局时还在
    开局（第 1、2 轮，没先看到三选一）就算从开局看起，不请用户打开「查看筹码」面板。先读到星数这类不带轮次的读数时先不提示、
    等读到轮次再定；等的时候先看到三选一就提示；一直读不到轮次，等满 PANEL_WAIT_S 秒照旧提示。没见过这些画面的照旧：第一次
    对局内读数就提示。"""

    def setUp(self):
        self.db = load_db()
        self.t = 1000.0
        self.session = Session(self.db, Settings(hero="凛"))
        self.session._clock = lambda: self.t

    def lobby(self, kind="main_menu") -> Session:
        s = self.session
        self.assertIsNone(s.on_out_of_game(screen(kind)))
        self.assertFalse(s.has_game_state)
        return s

    def prompts(self) -> int:
        return len(kinds(self.session, "panel_prompt"))

    def test_stars_then_round_one(self):
        """见过主菜单，先读到 0 星：先不提示；接着读到第 1 轮：从开局看起，不提示，之后也不提示。"""
        s = self.lobby()
        s.on_stars(own(0))
        self.assertEqual(self.prompts(), 0)
        self.assertTrue(s.game.panel_needed)
        self.t += 3
        s.on_hud(hud(round=1))
        self.assertEqual(self.prompts(), 0)
        self.assertEqual((s.game.panel_needed, s.game.whole_game), (False, True))
        self.t += 3
        s.on_hud(hud(round=2))
        s.on_frame(frame(OFFER_A))
        self.assertEqual(self.prompts(), 0)

    def test_other_lobby_screens(self):
        """房间、「匹配成功」画面、结算画面、奖励画面也算：之后第一次读到第 2 轮时不提示。"""
        for kind in ("team_room", "match_found", "result", "reward"):
            with self.subTest(kind=kind):
                self.setUp()
                s = self.lobby(kind)
                s.on_hud(hud(round=2))
                self.assertEqual(self.prompts(), 0)
                self.assertFalse(s.game.panel_needed)

    def test_without_lobby_as_before(self):
        """没见过对局外的画面（刚打开时就在对局里）：照旧第一次对局内读数（这里是星数）就提示。"""
        s = self.session
        s.on_stars(own(0))
        self.assertEqual(self.prompts(), 1)
        s.on_hud(hud(round=1))
        self.assertEqual(self.prompts(), 0)
        self.assertTrue(s.game.panel_needed)

    def test_offer_first_prompts(self):
        """见过主菜单，等轮次的时候先看到了三选一（开局后可能已经拿过筹码）：提示。"""
        s = self.lobby()
        s.on_stars(own(0))
        self.assertEqual(self.prompts(), 0)
        self.t += 1
        s.on_frame(frame(OFFER_A))
        self.assertEqual(self.prompts(), 1)
        self.assertTrue(s.game.panel_needed)

    def test_offer_frame_before_round(self):
        """三选一那一帧（识别流程先调 begin_offer、再交这一帧的轮次）：轮次再小也提示。"""
        s = self.lobby()
        self.assertIsNone(s.begin_offer())
        s.on_hud(hud(round=1))
        self.assertEqual(self.prompts(), 1)

    def test_later_round_prompts(self):
        """见过主菜单，第一次读到的轮次是第 4 轮（开局后可能已经拿过筹码）：提示。"""
        s = self.lobby()
        s.on_hud(hud(round=4))
        self.assertEqual(self.prompts(), 1)
        self.assertTrue(s.game.panel_needed)

    def test_no_round_for_long_prompts(self):
        """见过主菜单，一直只读到星数、读不到轮次：等满 PANEL_WAIT_S 秒照旧提示；提示过以后读到小的轮次也不收回。"""
        from advisor.session.core import PANEL_WAIT_S

        s = self.lobby()
        s.on_stars(own(1))
        self.t += PANEL_WAIT_S - 1
        s.on_stars(own(1))
        self.assertEqual(self.prompts(), 0)
        self.t += 1
        s.on_stars(own(1))
        self.assertEqual(self.prompts(), 1)
        self.t += 3
        s.on_hud(hud(round=1))
        self.assertTrue(s.game.panel_needed)
        self.assertEqual(self.prompts(), 0)


class PanelFixesPickTest(unittest.TestCase):
    """思考时间用完时游戏自动拿最左边的筹码，绿框选中的不一定是实际拿到的。
    之后读到「查看筹码」面板、和记录对不上时以面板为准，并说明改正了哪一次的选择记录（悬浮窗据此改推荐日志）。"""

    OFFER = ["孔雀扇", "速度轮滑-初级", "速度轮滑-高级"]

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db, "照")
        self.session.set_owned(["电流核心", "电磁炮"])

    def reading(self, owned):
        return SimpleNamespace(is_panel=True, self_tab=True, owned=list(owned), unknown_slots=0, confidence=0.99)

    def pick(self, chip):
        s = self.session
        s.on_frame(frame(self.OFFER, rerolls=4, probabilities=(40, 40, 20), selected=self.OFFER.index(chip)))
        ev = close(s)
        self.assertEqual((ev.kind, ev.chip), ("picked", chip))

    def test_timeout_pick_fixed_by_panel(self):
        s = self.session
        self.pick("速度轮滑-高级")  # 绿框选中，但没确认就超时了
        ev = s.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇"]))
        self.assertEqual(ev.kind, "panel_read")
        self.assertEqual(ev.fixes, [("速度轮滑-高级", "孔雀扇")])
        self.assertEqual(s.settings.owned, ["电流核心", "电磁炮", "孔雀扇"])
        self.assertEqual(s.game.chip_sources["孔雀扇"], "panel")
        # 同样的读数不再报；之后的面板读数也不会再改一次
        self.assertIsNone(s.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇"])))
        ev = s.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇", "普通瞄具"]))
        self.assertEqual(ev.fixes, [])

    def test_no_fix_when_panel_agrees(self):
        self.pick("孔雀扇")
        ev = self.session.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇"]))
        self.assertEqual(ev.fixes, [])

    def test_no_fix_for_chips_outside_the_offer(self):
        """面板上多出来的筹码不在那一组候选里（例如其他来源的筹码）：不当成那一次的选择，只按面板更新已有筹码。"""
        self.pick("速度轮滑-高级")
        ev = self.session.on_panel(self.reading(["电流核心", "电磁炮", "普通瞄具"]))
        self.assertEqual(ev.fixes, [])
        self.assertEqual(self.session.settings.owned, ["电流核心", "电磁炮", "普通瞄具"])

    def test_no_fix_when_ambiguous(self):
        """同一组候选里有两个都在面板上、之前又都没记着：分不出拿的是哪个，不改。"""
        self.pick("速度轮滑-高级")
        ev = self.session.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇", "速度轮滑-初级"]))
        self.assertEqual(ev.fixes, [])

    def test_manual_chips_not_fixed(self):
        """手动加的筹码面板上没有：照常以面板为准，但不算三选一的选择记录有误。"""
        s = self.session
        s.set_owned(["电流核心", "电磁炮", "速度轮滑-高级"])
        ev = s.on_panel(self.reading(["电流核心", "电磁炮", "孔雀扇"]))
        self.assertEqual(ev.fixes, [])

    def test_new_game_forgets_offers(self):
        self.pick("速度轮滑-高级")
        self.session.new_game()
        self.assertEqual(self.session.game._pick_offers, {})


class OwnRowTest(unittest.TestCase):
    """本局确认过的自己在玩家列表第几行（= 面板上自己的页签），以及本次运行中认出的自己的名字。"""

    def test_row_is_per_game_and_name_is_per_run(self):
        s = ready(load_db(), "照")
        self.assertIsNone(s.game.own_row)
        s.note_own(0, "玩家foobar")
        s.note_own(0, "玩家fooba")  # 文字识别偶尔少读一个字：取出现次数最多的
        s.note_own(0, "玩家foobar")
        self.assertEqual((s.game.own_row, s.player_name), (0, "玩家foobar"))
        s.note_own(None, "x")  # 太短的不算名字；没有行号时不改行号
        self.assertEqual((s.game.own_row, s.player_name), (0, "玩家foobar"))
        s.new_game()
        self.assertIsNone(s.game.own_row)  # 新的一局玩家顺序可能不同
        self.assertEqual(s.player_name, "玩家foobar")  # 名字留到程序关闭

    def test_undo_new_game_restores_row(self):
        s = ready(load_db(), "照")
        s.on_hud(hud(round=5))
        s.note_own(2)
        s.on_select_screen()
        self.assertIsNone(s.game.own_row)
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.game.own_row, 2)


class HeroNeededTest(unittest.TestCase):
    """角色待识别时暂停推荐，请用户选择角色。"""

    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="凛"))  # 「凛」只是上一次用的角色

    def test_no_advice_until_hero_chosen(self):
        s = self.session
        ev = s.on_frame(frame(OFFER_A))
        self.assertEqual((ev.kind, ev.offer), ("hero_needed", OFFER_A))
        self.assertIsNone(s.on_frame(frame(OFFER_A)))  # 不重复
        s.set_hero("阿尔")  # 用户在角色下拉框里选了
        ev = s.on_frame(frame(OFFER_A))
        self.assertEqual((ev.kind, ev.state.hero), ("advice", "阿尔"))

    def test_recognised_hero_resumes_advice(self):
        s = self.session
        self.assertEqual(s.on_frame(frame(OFFER_A)).kind, "hero_needed")
        s.on_hero(SimpleNamespace(hero="凛"))
        ev = s.on_frame(frame(OFFER_A))
        self.assertEqual((ev.kind, ev.state.hero), ("advice", "凛"))

    def test_unreadable_candidates_still_reported(self):
        """角色待识别时候选筹码读不出：照样先等一等，等满以后提示无法识别（不是请选择角色）。"""
        clock = [100.0]
        self.session._clock = lambda: clock[0]
        self.assertIsNone(self.session.on_frame(frame(["小猪存钱罐", None, "书签"])))
        clock[0] += 1.0
        ev = self.session.on_frame(frame(["小猪存钱罐", None, "书签"]))
        self.assertEqual(ev.kind, "unreadable")

    def test_template_wanted_when_portrait_unrecognised(self):
        """手动指定角色、而立绘无法识别（或识别成别的角色）时，请 FrameProcessor 把立绘存成模板。"""
        s = self.session
        s.set_hero("凛")
        self.assertEqual(s.game.template_wanted, "凛")
        s.on_hero(SimpleNamespace(hero="凛"))  # 立绘认出来了：不用存
        self.assertIsNone(s.game.template_wanted)
        s.set_hero("邦妮")  # 和识别结果不同
        self.assertEqual(s.game.template_wanted, "邦妮")
        s.template_saved()
        self.assertIsNone(s.game.template_wanted)
        s.set_hero("凛")  # 和识别结果相同
        self.assertIsNone(s.game.template_wanted)


class RevertPickTest(unittest.TestCase):
    """同一组候选在三选一关闭后又出现（中间没有别的三选一）：撤销刚记下的筹码，之后照常记账。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_same_offer_again_reverts_pick(self):
        s = self.session
        s.on_frame(frame(OFFER_A, selected=1))
        ev = close(s)
        self.assertEqual((ev.kind, ev.chip, ev.offer), ("picked", "友情徽章", OFFER_A))
        kinds(s)
        ev = s.on_frame(frame(OFFER_A))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.state.owned, ())  # 撤销后重新给推荐
        self.assertEqual([(e.chip, e.offer) for e in kinds(s, "pick_reverted")], [("友情徽章", OFFER_A)])
        self.assertEqual(s.settings.owned, [])
        s.on_frame(frame(OFFER_A, selected=2))
        self.assertEqual(close(s).chip, "医疗箱-紧急治疗")  # 之后照常记账
        self.assertEqual(s.settings.owned, ["医疗箱-紧急治疗"])

    def test_other_offer_in_between_keeps_pick(self):
        s = self.session
        s.on_frame(frame(OFFER_A, selected=1))
        close(s)
        s.on_frame(frame(OFFER_B))
        close(s)
        s.on_frame(frame(OFFER_A))
        self.assertEqual(kinds(s, "pick_reverted"), [])
        self.assertEqual(s.settings.owned, ["友情徽章"])

    def test_missed_pick_then_same_offer(self):
        """关闭时没看到选中项照旧请用户补记；同一组候选又出现时，补记提示收起，等这一次再记。"""
        s = self.session
        s.on_frame(frame(OFFER_A))
        self.assertEqual(close(s).kind, "missed_pick")
        self.assertEqual(s.game.missed_offer, OFFER_A)
        s.on_frame(frame(OFFER_A))
        self.assertIsNone(s.game.missed_offer)
        self.assertEqual(kinds(s, "pick_reverted"), [])

    def test_resolved_missed_pick_is_reverted(self):
        s = self.session
        s.on_frame(frame(OFFER_A))
        close(s)
        self.assertTrue(s.resolve_missed("友情徽章"))
        s.on_frame(frame(OFFER_A))
        self.assertEqual([e.chip for e in kinds(s, "pick_reverted")], ["友情徽章"])
        self.assertEqual(s.settings.owned, [])

    def test_chip_from_panel_not_reverted(self):
        s = self.session
        s.on_frame(frame(OFFER_A, selected=1))
        close(s)
        s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=["友情徽章"], unknown_slots=0, confidence=0.9))
        s.on_frame(frame(OFFER_A))
        self.assertEqual(kinds(s, "pick_reverted"), [])
        self.assertEqual(s.settings.owned, ["友情徽章"])


class UnrecordedGainTest(unittest.TestCase):
    """星数增加了，却没有对应的三选一记录（断线重连后被代选等）：提示补录。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)
        self.session.on_stars(own(1))
        self.session.on_stars(own(1))
        kinds(self.session)

    def gains(self):
        return kinds(self.session, "unrecorded_gain")

    def test_star_up_without_chip_select(self):
        s = self.session
        self.assertEqual(s.on_stars(own(2)).kind, "status")
        s.on_stars(own(2))
        self.assertEqual(self.gains(), [])  # 三选一可能还在出现的路上
        s.on_stars(own(2))
        got = self.gains()
        self.assertEqual([(e.count, e.why) for e in got], [(1, "星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面")])
        self.assertEqual(s.game.unrecorded, 1)
        s.on_stars(own(2))
        self.assertEqual(self.gains(), [])  # 只提示一次
        self.assertTrue(s.resolve_unrecorded("书签"))
        self.assertEqual((s.game.unrecorded, s.settings.owned, s.game.chip_sources["书签"]), (0, ["书签"], "added"))

    def test_chip_select_seen_while_own_row_hidden(self):
        """升星的三选一时自己那一行被挡住：关掉后才在对局画面上读到新的星数。"""
        s = self.session
        s.on_frame(frame(OFFER_A, selected=0))
        close(s)
        for _ in range(4):
            s.on_stars(own(2))
        self.assertEqual(self.gains(), [])

    def test_chip_select_appears_after_star_read(self):
        s = self.session
        s.on_stars(own(2))  # 新的星先亮，三选一随后出现
        s.on_frame(frame(OFFER_A, selected=0))
        close(s)
        for _ in range(4):
            s.on_stars(own(2))
        self.assertEqual(self.gains(), [])

    def test_star_read_inside_chip_select(self):
        s = self.session
        s.on_frame(frame(OFFER_A, selected=0))
        s.on_stars(own(2), in_select=True)
        close(s)
        for _ in range(4):
            s.on_stars(own(2))
        self.assertEqual(self.gains(), [])

    def test_reopened_offer_counts_once(self):
        """同一组候选关闭后又出现只算一次三选一：星数加两颗时仍提示补录 1 个。"""
        s = self.session
        s.on_frame(frame(OFFER_A))
        close(s)  # 没选就关了
        s.on_frame(frame(OFFER_A, selected=0))  # 同一组又出现，选了
        close(s)
        self.assertEqual(s.game._selects_since_read, 1)
        for _ in range(4):
            s.on_stars(own(3))
        self.assertEqual([e.count for e in self.gains()], [1])
        self.assertEqual(s.game.unrecorded, 1)

    def test_two_stars_one_chip_select(self):
        s = self.session
        s.on_frame(frame(OFFER_A, selected=0))
        close(s)
        s.on_stars(own(3))
        s.on_stars(own(3))  # 跳两颗：连续读到两次才采用
        self.assertEqual(s.settings.star, 3)
        s.on_stars(own(3))
        s.on_stars(own(3))
        self.assertEqual([e.count for e in self.gains()], [1])

    def test_misread_increase_is_not_a_gain(self):
        s = self.session
        s.on_stars(own(2))  # 读错一次
        s.on_stars(own(1))
        s.on_stars(own(1))  # 改正
        for _ in range(4):
            s.on_stars(own(1))
        self.assertEqual(self.gains(), [])

    def test_task_select_does_not_cover_later_level_up(self):
        """同一星数下的任务三选一，之后在对局画面上读到过这个星数，就不再算作下一次升星的三选一。"""
        s = self.session
        s.on_frame(frame(OFFER_A, selected=0))
        close(s)
        s.on_stars(own(1))  # 三选一之后星数没变：那次不是升星
        s.on_stars(own(2))
        s.on_stars(own(2))
        s.on_stars(own(2))
        self.assertEqual([e.count for e in self.gains()], [1])


class DirectChipTest(unittest.TestCase):
    """补录时记为不经三选一获得的筹码（飞羽手环定期给的、神秘宝藏的宝箱等）：面板核对不改，移出已有筹码时去掉，
    新的一局清空、撤销恢复，build_state 只数这些里不是地图发放的，诊断里的来源也只把这些写成 direct。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db, "凛")

    def reading(self, owned):
        return SimpleNamespace(is_panel=True, self_tab=True, owned=list(owned), unknown_slots=0, confidence=0.9)

    def test_survives_panel_and_removal(self):
        s = self.session
        s.set_owned(["飞羽手环"])
        self.assertTrue(s.add_chip("书签", "added", direct=True))
        self.assertEqual(s.game.direct, {"书签"})
        s.on_panel(self.reading(["飞羽手环", "书签"]))
        self.assertEqual(s.game.direct, {"书签"})  # 面板核对只把来源改成「查看筹码」面板
        self.assertEqual(s.game.chip_sources["书签"], "panel")
        s.remove_chip("书签")  # 小卡片上的「×」
        self.assertEqual(s.game.direct, set())

    def test_panel_removal_drops_it(self):
        s = self.session
        s.set_owned(["飞羽手环"])
        s.add_chip("书签", direct=True)
        s.on_panel(self.reading(["飞羽手环"]))  # 面板上没有书签
        self.assertEqual(s.game.direct, set())

    def test_set_owned_keeps_only_present(self):
        s = self.session
        s.set_owned(["飞羽手环"])
        s.add_chip("书签", direct=True)
        s.add_chip("标靶", direct=True)
        s.set_owned(["飞羽手环", "书签"])
        self.assertEqual(s.game.direct, {"书签"})

    def test_unrecorded_gain_not_direct(self):
        """待补录的升星（星数增加了、没看到那次三选一）走同一条补录，但那是三选一拿到的，不能记。"""
        s = self.session
        self.assertTrue(s.resolve_unrecorded("书签"))
        self.assertEqual(s.game.direct, set())

    def test_new_game_clears_and_undo_restores(self):
        s = self.session
        s.set_owned(["飞羽手环"])
        s.add_chip("书签", direct=True)
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
        self.assertEqual(s.game.direct, set())
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.game.direct, {"书签"})

    def test_build_state_counts_direct(self):
        s = self.session
        s.set_owned(["飞羽手环"])
        s.add_chip("书签", direct=True)
        s.add_chip("标靶")  # 补录的是三选一拿到的：不算
        st = s.build_state(frame(OFFER_A))
        self.assertEqual(st.direct, 1)
        self.assertEqual(s.snapshot()["chip_sources"]["书签"], "direct")

    def test_granted_chip_not_direct(self):
        """地图直接发放的筹码（龙宫游乐园的交错双鲤）补录时也可能带上这个记号（持有飞羽手环时点发放的筹码按钮）：它本来
        就不算三选一，build_state 不数它，诊断里的来源也照写补录、不写 direct。"""
        s = self.session
        self.assertTrue(s.db.chips["交错双鲤"].granted)
        s.set_owned(["飞羽手环"])
        self.assertTrue(s.add_chip("交错双鲤", direct=True))
        self.assertEqual(s.game.direct, {"交错双鲤"})
        self.assertEqual(s.build_state(frame(OFFER_A)).direct, 0)
        self.assertEqual(s.snapshot()["chip_sources"]["交错双鲤"], "added")


class EndlessPendingTest(unittest.TestCase):
    """循环往复给的额外那次三选一（Session.endless_pending，交给引擎的 State.endless_pending）：在三选一里看到选中循环往复时
    记下到期的轮次（那时的轮次 + rules.toml [endless_cycle] delay：升星和筹码商店拿到的 2 回合、任务拿到的 3 回合）；到期以后
    第一次判成任务来源（三格全随机）、又不是全队同时三选一的三选一当作给了；过了到期那一轮再过 ENDLESS_GRACE 轮还没认出来
    不再算；循环往复不在已有筹码里、新的一局、拿到时轮次读不出、不是在三选一里选的都不算。

    2 星（读到了）、概率行是 2 星那一行：最左格是紫色（2 星升星和筹码商店的固定稀有度）时判成升星，是别的颜色时判成任务。"""

    LEVELUP = ["友情徽章", "循环往复", "小猪存钱罐"]  # 第一个紫色：判成升星
    TASK = ["小猪存钱罐", "循环往复", "友情徽章"]  # 第一个蓝色：判成任务
    OTHER_UP = ["大背包", "书签", "标靶"]
    OTHER_TASK = ["标靶", "大背包", "书签"]
    ROW2 = (40, 45, 15)

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "凛")
        self.s.settings.star, self.s.settings.star_read = 2, True

    def offer(self, names, rnd=None, pick=None, team=False, probabilities=ROW2, rerolls=2):
        """一次三选一：先按 rnd 改现在的轮次（不给时不改），给推荐；pick 给了时选中第 pick 个、关掉。返回推荐事件。"""
        if rnd is not None:
            self.s.settings.round = rnd

        def seen(selected=None):
            f = frame(names, rerolls=rerolls, probabilities=probabilities, selected=selected)
            f.team = team
            return f

        ev = self.s.on_frame(seen())
        if pick is not None:
            self.s.on_frame(seen(pick))
            self.assertEqual(close(self.s).kind, "picked")
        else:
            close(self.s)
        return ev

    def test_levelup_then_given_by_task(self):
        s = self.s
        ev = self.offer(self.LEVELUP, rnd=6, pick=1)
        self.assertEqual(ev.state.source, "levelup")
        self.assertFalse(ev.state.endless_pending)
        self.assertEqual(s.game._endless_due, 8)  # 第 6 轮升星拿到：再过 2 回合
        self.assertTrue(self.offer(self.OTHER_UP, rnd=7, pick=0).state.endless_pending)
        # 到期那一轮先来的升星、筹码商店不是它
        ev = self.offer(self.OTHER_UP[1:] + ["夹心饼干-一般"], rnd=8, pick=0)
        self.assertEqual(ev.state.source, "levelup")
        self.assertTrue(ev.state.endless_pending)
        # 到期以后第一次判成任务的：当作额外那次给了，这一次本身不再多算
        ev = self.offer(["优雅之羽", "电磁炮", "维生素药丸"], rnd=8, pick=0)
        self.assertEqual(ev.state.source, "task")
        self.assertFalse(ev.state.endless_pending)
        self.assertEqual(s.game._endless_given, ("优雅之羽", "电磁炮", "维生素药丸"))
        self.assertFalse(s.endless_pending)
        self.assertFalse(self.offer(["标记喷罐", "手电筒-强光", "医疗箱-紧急治疗"], rnd=9).state.endless_pending)

    def test_task_pick_waits_three(self):
        ev = self.offer(self.TASK, rnd=6, pick=1)
        self.assertEqual(ev.state.source, "task")
        self.assertEqual(self.s.game._endless_due, 9)  # 任务拿到：过 3 回合
        # 到期以前判成任务的是普通的任务，不当作它
        self.assertTrue(self.offer(self.OTHER_TASK, rnd=8, pick=0).state.endless_pending)
        self.assertTrue(self.s.endless_pending)

    def test_grace_then_dropped(self):
        self.offer(self.LEVELUP, rnd=6, pick=1)
        self.assertTrue(self.offer(self.OTHER_UP, rnd=9, pick=0).state.endless_pending)  # 到期后一轮：还算
        self.assertFalse(self.offer(["标记喷罐", "大碗炖肉", "夹心饼干-一般"], rnd=10).state.endless_pending)
        self.assertFalse(self.s.endless_pending)

    def test_team_pick_is_not_it(self):
        self.offer(self.LEVELUP, rnd=6, pick=1)
        ev = self.offer(self.OTHER_TASK, rnd=8, pick=0, team=True)  # 全队同时三选一：一定是任务
        self.assertEqual(ev.state.source, "task")
        self.assertTrue(ev.state.endless_pending)
        self.assertIsNone(self.s.game._endless_given)

    def test_unknown_round_or_not_from_pick(self):
        s = self.s
        s.settings.round = None
        self.offer(self.LEVELUP, pick=1)
        self.assertIsNone(s.game._endless_due)  # 拿到时轮次读不出：不知道什么时候给，不多算
        self.assertFalse(self.offer(self.OTHER_UP, rnd=7).state.endless_pending)
        other = ready(self.db, "凛")
        other.settings.round = 6
        self.assertTrue(other.add_chip("循环往复"))  # 补录：不知道拿到的轮次
        self.assertIsNone(other.game._endless_due)
        self.assertFalse(other.build_state(frame(OFFER_A)).endless_pending)

    def test_removed_or_new_game(self):
        s = self.s
        self.offer(self.LEVELUP, rnd=6, pick=1)
        self.assertTrue(s.endless_pending)
        self.assertTrue(s.remove_chip("循环往复"))  # 移出已有筹码（面板改正、手动）：不算
        self.assertFalse(s.endless_pending)
        self.assertTrue(s.add_chip("循环往复", "pick"))
        self.assertTrue(s.endless_pending)  # 记着的到期轮次还在、又回到已有筹码里
        # 新的一局清掉，撤销恢复
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
        self.assertIsNone(s.game._endless_due)
        self.assertFalse(s.endless_pending)
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.game._endless_due, 8)
        self.assertTrue(s.endless_pending)

    def test_rejudged_source(self):
        """同一组候选第一次的概率行读不出（不在概率表里，照记下的来源按升星算）、后来读对了判成任务：改判时当作额外那次；
        当作了它的一组改判成别的来源时撤回。"""
        s = self.s
        self.offer(self.LEVELUP, rnd=6, pick=1)
        s.settings.round = 8
        names = ["优雅之羽", "电磁炮", "维生素药丸"]
        first = s.on_frame(frame(names, probabilities=(1, 2, 97)))
        self.assertEqual(first.state.source, "levelup")
        self.assertTrue(first.state.endless_pending)
        again = s.on_frame(frame(names, probabilities=self.ROW2))
        self.assertEqual(again.state.source, "task")
        self.assertFalse(again.state.endless_pending)
        self.assertEqual(again.state.endless_given, 1)  # 这一次就是额外那次：不算成任务的一次
        self.assertEqual(s.game._endless_given, tuple(names))
        s._endless_judged(tuple(names), "levelup", False)  # 同一组改判成升星：撤回
        self.assertIsNone(s.game._endless_given)
        self.assertTrue(s.endless_pending)
        self.assertEqual(s.endless_given, 0)

    def test_old_state_and_engine(self):
        self.assertFalse(State(hero="凛").endless_pending)  # 旧记录、冻结的局面没有这一项：按没有
        self.assertEqual(State(hero="凛").endless_given, 0)
        self.offer(self.LEVELUP, rnd=6, pick=1)
        st = self.s.build_state(frame(self.OTHER_UP, probabilities=self.ROW2))
        self.assertTrue(st.endless_pending)
        self.assertEqual(st.endless_given, 0)
        self.assertEqual(st.endless_due, 8)  # 到期的轮次一并交给引擎
        self.assertIsNone(State(hero="凛").endless_due)
        from advisor.engine import Scorer
        self.assertEqual(Scorer(self.db, st).picks.endless, 1)

    def test_due_after_cap(self):
        """到期在进度上限以后：第 14 轮升星拿到循环往复（到期第 16 轮），魔法学院进度上限 16。Session 照记还没给
        （endless_pending），到期轮次交给引擎（State.endless_due），引擎按那一轮打不到不多算；还没给以外（给了、收尾以后）
        不带到期轮次。"""
        s = self.s
        s.settings.map = "魔法学院"
        self.offer(self.LEVELUP, rnd=14, pick=1)
        self.assertEqual(s.game._endless_due, 16)
        st = s.build_state(frame(self.OTHER_UP, probabilities=self.ROW2))
        self.assertEqual((st.endless_pending, st.endless_due), (True, 16))
        from advisor.engine import Scorer
        self.assertEqual(Scorer(self.db, st).picks.endless, 0)
        s.settings.round = 18  # 过了到期那一轮再过一轮：不再算，也不带
        st = s.build_state(frame(self.OTHER_UP, probabilities=self.ROW2))
        self.assertEqual((st.endless_pending, st.endless_due), (False, None))

    def test_given_extra_pick_and_its_chip(self):
        """额外那次给了以后（Session.endless_given → State.endless_given，引擎不把它算成任务和筹码商店的三选一）：认出的
        那一次本身、刷新换了候选以后都是 1；那次选中的筹码记进已有筹码以后，之后的三选一照旧 1；筹码移出已有筹码时 0。"""
        s = self.s
        self.offer(self.LEVELUP, rnd=6, pick=1)
        self.assertEqual(self.offer(self.OTHER_UP, rnd=7, pick=0).state.endless_given, 0)  # 还没给
        s.settings.round = 8
        ev = s.on_frame(frame(["优雅之羽", "电磁炮", "维生素药丸"], probabilities=self.ROW2))
        self.assertEqual((ev.state.source, ev.state.endless_pending, ev.state.endless_given), ("task", False, 1))
        # 刷新：三张全换、次数少 1，还是这一次
        refreshed = ["标记喷罐", "大碗炖肉", "夹心饼干-一般"]
        ev = s.on_frame(frame(refreshed, rerolls=1, probabilities=self.ROW2))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.state.endless_given, 1)
        s.on_frame(frame(refreshed, rerolls=1, probabilities=self.ROW2, selected=1))
        self.assertEqual(close(s).kind, "picked")
        self.assertEqual(s.game._endless_chip, "大碗炖肉")
        ev = self.offer(["手电筒-强光", "书签", "医疗箱-紧急治疗"], rnd=9)  # 新的一次三选一：不是它，选中的筹码还在
        self.assertFalse(s.game._endless_open)
        self.assertEqual(ev.state.endless_given, 1)
        self.assertTrue(s.remove_chip("大碗炖肉"))
        self.assertEqual(s.endless_given, 0)
        self.assertTrue(s.add_chip("大碗炖肉", direct=True))  # 又按「不经三选一获得」补录：由 direct 扣，这里不再扣一次
        self.assertEqual(s.endless_given, 0)

    def test_missed_pick_of_endless(self):
        """漏记（三选一关了却没看到选中）、用户补录时点了循环往复：按漏记的那次三选一当时的轮次和来源记额外那次到期的轮次，
        补录时已经到了下一轮也一样；那时轮次读不出照旧不记。用「＋ 补录筹码」手动加的不记（test_unknown_round_or_not_from_pick）。"""
        s = self.s
        self.assertEqual(self.offer(self.LEVELUP, rnd=6).state.source, "levelup")  # 没看到选中：请用户补录
        self.assertIsNotNone(s.game.missed_offer)
        s.settings.round = 7
        self.assertTrue(s.resolve_missed("循环往复"))
        self.assertEqual(s.game._endless_due, 8)  # 第 6 轮升星那次拿到：再过 2 回合
        self.assertTrue(s.endless_pending)
        self.assertTrue(self.offer(self.OTHER_UP, rnd=7).state.endless_pending)
        # 漏记那时轮次读不出：不记
        other = ready(self.db, "凛")
        other.settings.star, other.settings.star_read = 2, True
        other.on_frame(frame(self.TASK, probabilities=self.ROW2))
        self.assertEqual(close(other).kind, "missed_pick")
        other.settings.round = 7
        self.assertTrue(other.resolve_missed("循环往复"))
        self.assertIsNone(other.game._endless_due)
        self.assertFalse(other.endless_pending)

    def test_missed_extra_pick_records_chip(self):
        """额外那次本身漏记、用户补录：补的就是那次选中的筹码，记下它（endless_given 照常为 1）。"""
        s = self.s
        self.offer(self.LEVELUP, rnd=6, pick=1)
        ev = self.offer(["优雅之羽", "电磁炮", "维生素药丸"], rnd=8)  # 额外那次，没看到选中
        self.assertEqual(ev.state.endless_given, 1)
        missed = list(s.game.missed_offer)
        # 补录之前又来了一次三选一（看到了选中）：额外那次选了什么还不知道，不扣
        self.assertEqual(self.offer(["手电筒-强光", "书签", "医疗箱-紧急治疗"], rnd=8, pick=0).state.endless_given, 0)
        self.assertEqual(s.game.missed_offer, missed)
        self.assertTrue(s.resolve_missed("电磁炮"))
        self.assertEqual(s.game._endless_chip, "电磁炮")
        self.assertEqual(s.endless_given, 1)


class UndoNewGameTest(unittest.TestCase):
    """判定新的一局前存快照，到这一局第一次记录筹码为止可以撤销。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_round_drop_undo(self):
        s = self.session
        s.set_owned(["书签"])
        s.on_hud(hud(round=7, map="异变图书馆"))
        s.on_hud(hud(round=7))
        self.assertFalse(s.can_undo_new_game)
        s.on_hud(hud(round=3))
        ev = s.on_hud(hud(round=3))
        self.assertEqual((ev.kind, ev.undo), ("newgame", True))
        self.assertTrue(s.can_undo_new_game)
        self.assertTrue(s.undo_new_game())
        st = s.settings
        self.assertEqual((st.owned, st.round, st.map, s.game.seen_hero), (["书签"], 7, "异变图书馆", "远野汉娜"))
        self.assertFalse(s.can_undo_new_game)
        self.assertFalse(s.undo_new_game())
        # 撤销后同一个读数不再判成新的一局：连续读到算改正读错
        s.on_hud(hud(round=3))
        self.assertEqual(s.on_hud(hud(round=3)).kind, "status")
        self.assertEqual((st.round, st.owned), (3, ["书签"]))

    def test_undo_available_until_first_record(self):
        s = self.session
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=1))
        s.on_hud(hud(round=1))
        self.assertTrue(s.can_undo_new_game)
        s.on_frame(frame(OFFER_A))
        close(s)  # 没记下筹码（漏记）：仍可撤销
        self.assertTrue(s.can_undo_new_game)
        s.resolve_missed("书签")
        self.assertFalse(s.can_undo_new_game)

    def test_undo_select_screen(self):
        s = self.session
        s.set_owned(["书签"])
        self.assertEqual(s.on_select_screen().kind, "newgame")
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.settings.owned, ["书签"])
        self.assertIsNone(s.on_select_screen())  # 还停在选角界面：不再判
        s.on_hud(hud(round=4))  # 回到对局画面
        self.assertEqual(s.on_select_screen().kind, "newgame")

    def test_undo_hero_change(self):
        s = Session(self.db, Settings(hero="凛"))
        s.on_hero(SimpleNamespace(hero="凛"))
        s.set_owned(["书签"])
        s.on_hero(SimpleNamespace(hero="邦妮"))
        self.assertEqual(s.on_hero(SimpleNamespace(hero="邦妮")).kind, "newgame")
        self.assertTrue(s.undo_new_game())
        self.assertEqual((s.game.seen_hero, s.settings.hero, s.settings.owned), ("凛", "凛", ["书签"]))
        for _ in range(3):
            self.assertIsNone(s.on_hero(SimpleNamespace(hero="邦妮")))  # 不再当成新一局的迹象

    def test_undo_star_drop(self):
        s = self.session
        s.set_owned(["书签"])
        s.on_stars(own(2))
        s.on_stars(own(2))
        s.on_stars(own(0))
        self.assertEqual(s.on_stars(own(0)).kind, "newgame")
        s.undo_new_game()
        self.assertEqual((s.settings.star, s.settings.owned), (2, ["书签"]))
        s.on_stars(own(0))
        self.assertEqual(s.on_stars(own(0)).kind, "status")  # 改正读错
        self.assertEqual(s.settings.owned, ["书签"])

    def test_undo_restores_panel_and_mates(self):
        s = self.session
        s.on_teammates([{"row": 0, "hero": "恋", "bot": True}] * 2)
        s.set_owned(["书签"])
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=1))
        s.on_hud(hud(round=1))
        self.assertEqual(s.game.teammates, [])
        s.undo_new_game()
        self.assertEqual([t["hero"] for t in s.game.teammates], ["恋"])


class MapReadAndMutatorsTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_map_read_when_task_panel_identifies_map(self):
        """读到任务栏、识别出地图时发 map_read（悬浮窗提示可以关闭任务栏）。"""
        s = self.session
        h = hud(round=2, map="异变图书馆", map_confidence="high", map_candidates=["异变图书馆"], tasks_seen=["击倒魔法茶壶"])
        self.assertEqual(s.on_hud(h).kind, "status")
        self.assertEqual([e.map for e in kinds(s, "map_read")], ["异变图书馆"])
        s.on_hud(h)
        self.assertEqual(kinds(s, "map_read"), [])  # 没变化不重复

    def test_mutators_read_before_select_screen_belong_to_next_game(self):
        """选角前公布的畸变组件。上一局的状态还在时读到的，留到判定新的一局时生效。"""
        s = self.session
        s.on_hud(hud(round=12))
        self.assertIsNone(s.on_mutators(["狂风", "暴雨"], pregame=True))
        self.assertEqual(s.game.mutators, [])
        s.on_select_screen()
        self.assertEqual(s.game.mutators, ["狂风", "暴雨"])
        s.on_hud(hud(round=1))
        s.on_hero(SimpleNamespace(hero="远野汉娜"))
        ev = s.on_frame(frame(OFFER_A))
        if "mutators" in State.__dataclass_fields__:
            self.assertEqual(tuple(ev.state.mutators), ("狂风", "暴雨"))

    def test_mutators_without_previous_game(self):
        s = Session(self.db, Settings(hero="凛"))
        self.assertEqual(s.on_mutators(["狂风"], pregame=True).kind, "status")
        self.assertEqual(s.game.mutators, ["狂风"])
        self.assertIsNone(s.on_mutators(["狂风"]))


class UnsupportedTest(unittest.TestCase):
    """不支持的情况写进 Session.unsupported（状态行提示），每条写明影响和处理方法。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_texts_and_order(self):
        s = self.session
        self.assertTrue(s.set_unsupported("small_window", True, size="800×450"))
        self.assertFalse(s.set_unsupported("small_window", True, size="800×450"))
        self.assertTrue(s.set_unsupported("fullscreen", True))
        texts = s.unsupported
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].startswith("检测到独占全屏模式"))
        self.assertIn("800×450 像素", texts[1])
        self.assertIn("请调大游戏窗口：16:9 窗口宽度 1000 像素以上，更宽的窗口高度 563 像素以上", texts[1])
        for text in texts:
            self.assertRegex(text, "：.*请")  # 写明影响和处理方法
        s.set_unsupported("small_window", False)
        self.assertEqual(len(s.unsupported), 1)

    def test_language_needs_repeated_signals(self):
        s = self.session
        self.assertIsNone(s.on_language(True))
        self.assertIsNone(s.on_language(None))  # 不确定的不算
        self.assertEqual(s.on_language(True).kind, "status")
        self.assertIn("简体中文或繁体中文", s.unsupported[0])
        self.assertEqual(s.on_language(False).kind, "status")  # 读到了简体或繁体的标题
        self.assertEqual(s.unsupported, [])

    def test_normal_difficulty_is_not_unsupported(self):
        """普通难度按概率行判定本局难度（见 DifficultyTest），不列为不支持的情况。"""
        s = self.session
        self.assertNotIn("difficulty", UNSUPPORTED_TEXT)
        for offer in (OFFER_A, OFFER_B):
            ev = s.on_frame(frame(offer, probabilities=(60, 30, 10)))
            self.assertEqual(ev.kind, "advice")
            close(s)
        s.on_frame(frame(OFFER_A, probabilities=(50, 40, 10)))  # 两张表都对不上的概率行也不提示
        self.assertEqual(s.unsupported, [])
        self.assertFalse(any("普通难度" in t for t in UNSUPPORTED_TEXT.values()))


class DifficultyTest(unittest.TestCase):
    """本局难度按三选一的概率行判定：普通难度时按普通难度的概率表和进度上限计算。
    困难、噩梦、疯狂、极限的筹码概率相同，不区分。"""

    NORMAL_ROW = (60, 30, 10)
    HARD_ROW = (60, 37, 3)
    ODD_ROW = (50, 40, 10)  # 两张表里都没有（文字识别读错等）

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_each_offer_decides_by_rows(self):
        s = self.session
        self.assertIsNone(s.settings.difficulty)  # 本局还没有三选一：尚未判定，按困难及以上计算
        ev = s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW))
        self.assertEqual(s.settings.difficulty, NORMAL)
        self.assertEqual(ev.state.difficulty, NORMAL)
        self.assertIn(NORMAL_NOTE, ev.advice.notes)
        self.assertEqual(len(kinds(s, "status")), 1)  # 状态行要显示「难度：普通」
        close(s)
        # 之后的三选一读到困难及以上的概率行：按最新读到的改为困难及以上
        ev = s.on_frame(frame(OFFER_B, probabilities=self.HARD_ROW))
        self.assertEqual(s.settings.difficulty, HARD)
        self.assertIsNone(ev.state.difficulty)
        self.assertNotIn(NORMAL_NOTE, ev.advice.notes)
        self.assertEqual(len(kinds(s, "status")), 1)  # 「难度：普通」收起
        # 困难及以上的概率行再读到多次，状态行不变，不再发 status
        s.on_frame(frame(OFFER_B, probabilities=(40, 45, 15), rerolls=1))
        self.assertEqual(kinds(s, "status"), [])

    def test_unmatched_rows_keep_this_games_difficulty(self):
        """概率行和两张表都对不上（或读不到）：沿用本局已判定的难度；本局还没判定时按困难及以上。"""
        s = self.session
        ev = s.on_frame(frame(OFFER_A, probabilities=self.ODD_ROW))
        self.assertIsNone(s.settings.difficulty)
        self.assertIsNone(ev.state.difficulty)
        self.assertNotIn(NORMAL_NOTE, ev.advice.notes)
        ev = s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW, rerolls=1))
        self.assertEqual(ev.state.difficulty, NORMAL)
        for i, probabilities in enumerate((self.ODD_ROW, None)):
            with self.subTest(probabilities=probabilities):
                ev = s.on_frame(frame(OFFER_B, probabilities=probabilities, rerolls=i))
                self.assertEqual((s.settings.difficulty, ev.state.difficulty), (NORMAL, NORMAL))
                self.assertIn(NORMAL_NOTE, ev.advice.notes)

    def test_new_game_resets_and_undo_restores(self):
        s = self.session
        s.on_hud(hud(round=6))
        s.on_hud(hud(round=6))
        s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW))
        close(s)
        self.assertEqual(s.settings.difficulty, NORMAL)
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
        self.assertIsNone(s.settings.difficulty)  # 新的一局重新判定
        s.on_hero(SimpleNamespace(hero="远野汉娜"))  # 新的一局重新认出角色
        ev = s.on_frame(frame(OFFER_B, probabilities=self.ODD_ROW))
        self.assertIsNone(ev.state.difficulty)  # 上一局的普通难度不带进新的一局
        close(s)
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.settings.difficulty, NORMAL)  # 撤销新一局的判定：恢复上一局的难度
        s.new_game()
        self.assertIsNone(s.settings.difficulty)

    def test_select_screen_clears_difficulty_alone(self):
        """只判定了难度（角色、轮次、星数都还没读到）时看到选角界面，也判为新的一局，难度不带进下一局。"""
        s = Session(self.db, Settings(hero="凛"))
        self.assertEqual(s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW)).kind, "hero_needed")
        self.assertEqual(s.settings.difficulty, NORMAL)  # 推荐暂停时难度照样判定
        self.assertEqual(s.on_select_screen().kind, "newgame")
        self.assertIsNone(s.settings.difficulty)

    def test_not_saved_nor_restored(self):
        """难度是对局状态：不写进 state.json，启动时不沿用（和 GAME_FIELDS 的其余各项一样）。"""
        self.assertIn("difficulty", GAME_FIELDS)
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            path = Path(tmp) / "state.json"
            session = Session(self.db, Settings(hero="凛", window_pos=[1, 2]), path)
            session.game.seen_hero = "凛"
            session.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW))
            self.assertEqual(session.settings.difficulty, NORMAL)
            session.save()
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("difficulty", data)
            self.assertEqual(data["window_pos"], [1, 2])
            path.write_text(json.dumps(dict(data, difficulty=NORMAL), ensure_ascii=False), encoding="utf-8")
            again = Session(self.db, Settings.load(path), path)
            self.assertIsNone(again.settings.difficulty)

    def test_judge_source_looks_in_this_games_table(self):
        """按概率行反推星数、来源、任务等级时，在本局难度的那张表里找。"""
        s = self.session
        purple = next(c.name for c in self.db.chips.values()
                      if c.rarity == "紫" and not c.maps and not c.granted and c.name not in OFFER_B)
        # 读不到星数：普通表 2 星那一行，最左格紫色 → 按 2 星升星估
        ev = s.on_frame(frame([purple, *OFFER_B[1:]], probabilities=(40, 40, 20)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "levelup", None))
        self.assertEqual(ev.state.difficulty, NORMAL)
        close(s)
        # 读到 1 星（三选一界面上也读到）：普通表 2、3 星那两行是任务 2、3 级，0~1 星那一行是升星
        s.on_hud(hud(round=5))
        s.on_stars(own(1))
        s.on_stars(own(1), in_select=True)
        for probabilities, tier in (((40, 40, 20), 2), ((20, 50, 30), 3)):
            with self.subTest(probabilities=probabilities):
                ev = s.on_frame(frame(OFFER_A, probabilities=probabilities, rerolls=tier))
                self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "task", tier))
                self.assertEqual(ev.state.difficulty, NORMAL)
        ev = s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW, rerolls=4))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (1, "levelup", None))
        self.assertEqual(s.settings.star, 1)  # 星数只认玩家列表上读到的

    def test_judge_source_decides_difficulty_when_unknown(self):
        """本局难度还没判定时两张表都找，对上哪张就同时判定本局难度；判定后只在这张表里找。"""
        s = self.session
        s.on_stars(own(2))
        purple = "友情徽章"
        self.assertIsNone(s.settings.difficulty)
        self.assertEqual(s.judge_source((40, 40, 20), [purple, "书签", "标靶"]), (2, "levelup", None))
        self.assertEqual(s.settings.difficulty, NORMAL)
        # 困难及以上 3 星那一行不在普通表里：按记下的星数和来源算，本局难度不变
        self.assertEqual(s.judge_source((25, 50, 25), [purple, "书签", "标靶"]), (2, "levelup", None))
        self.assertEqual(s.settings.difficulty, NORMAL)
        s.new_game()
        s.on_stars(own(2))
        self.assertEqual(s.judge_source((25, 50, 25), [purple, "书签", "标靶"]), (2, "task", 3))
        self.assertEqual(s.settings.difficulty, HARD)


class DifficultyInfoTest(unittest.TestCase):
    """本局具体难度（噩梦及以上时摩托头盔-高级对所有角色提高）：合并对局外画面上读到的难度、
    按地图和进度上限推算、概率行（Session.difficulty_info），交给 State.difficulty_level / difficulty_name。"""

    HARD_ROW = DifficultyTest.HARD_ROW
    NORMAL_ROW = DifficultyTest.NORMAL_ROW

    def setUp(self):
        self.db = load_db()

    def lobby(self, *screens, hero="阿尔") -> Session:
        """启动时在对局外（没有对局状态）：读到这些画面，再看到选角界面、进入对局（第 1 轮）、认出角色。"""
        s = Session(self.db, Settings(hero=hero))
        for sc in screens:
            self.assertIsNone(s.on_out_of_game(sc))  # 没有对局状态：不算结束
        s.on_select_screen()
        s.on_hud(hud(round=1))
        s.on_hero(SimpleNamespace(hero=hero))
        return s

    def cap(self, s, cap, current=1):
        """进度条连续两次读到同一个红叉（采用为本局的进度上限）。"""
        s.on_progress(current, cap)
        s.on_progress(current, cap)
        self.assertEqual(s.game.progress_cap, cap)

    def test_room_reading_belongs_to_next_game(self):
        # 房间里读到的难度先留着（这时还没有对局状态，不算本局），选角界面连续几帧也不丢，进入对局时用上
        s = Session(self.db, Settings(hero="阿尔"))
        s.on_out_of_game(screen("team_room", "噩梦"))
        self.assertEqual(s.difficulty_info, DifficultyInfo(note="地图待识别"))
        self.assertEqual(s.pending._next_difficulty, {"team_room": "噩梦"})
        for _ in range(3):
            self.assertIsNone(s.on_select_screen())
        self.assertEqual(s.pending._next_difficulty, {"team_room": "噩梦"})
        self.assertFalse(s.has_game_state)  # 难度读数不算对局状态
        s.on_hud(hud(round=1))
        self.assertEqual(s.pending._next_difficulty, {})
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "噩梦", "team_room"))
        self.assertEqual(s.difficulty_info.label, "噩梦")
        s.on_hero(SimpleNamespace(hero="阿尔"))
        ev = s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual((ev.state.difficulty, ev.state.difficulty_level, ev.state.difficulty_name),
                         (None, LEVEL_NIGHTMARE, "噩梦"))

    def test_pending_used_when_first_read_is_an_offer(self):
        # 第一次对局内读数是三选一（轮次读不出、角色先认出）：同样用上留着的难度和地图
        s = Session(self.db, Settings(hero="阿尔"))
        s.on_out_of_game(screen("match_found", "疯狂", "水乡古镇"))
        s.on_hero(SimpleNamespace(hero="阿尔"))
        self.assertTrue(s.has_game_state)
        ev = s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual((ev.state.difficulty_level, ev.state.difficulty_name, ev.state.map),
                         (LEVEL_NIGHTMARE, "疯狂", "水乡古镇"))
        self.assertEqual(s.map_source, "match")

    def test_room_normal_before_rows_known(self):
        # 房间里读到普通、三选一的概率行读不出：按普通难度计算
        s = self.lobby(screen("team_room", "普通"))
        ev = s.on_frame(frame(OFFER_A, probabilities=None))
        self.assertEqual((ev.state.difficulty, ev.state.difficulty_level, ev.state.difficulty_name),
                         (NORMAL, LEVEL_NORMAL, "普通"))
        self.assertIn(NORMAL_NOTE, ev.advice.notes)
        close(s)
        # 之后的概率行是困难及以上：画面说普通、概率行说困难及以上，按概率行（困难及以上）
        ev = s.on_frame(frame(OFFER_B, probabilities=self.HARD_ROW))
        self.assertEqual((ev.state.difficulty, ev.state.difficulty_level), (None, LEVEL_HARD_PLUS))
        self.assertEqual(s.difficulty_info.source, "rows")
        self.assertIn("screen_vs_rows", [r.get("conflict") for r in s.recent_events(60)])

    def test_rows_normal_wins_over_screen(self):
        s = self.lobby(screen("team_room", "噩梦"))
        ev = s.on_frame(frame(OFFER_A, probabilities=self.NORMAL_ROW))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NORMAL, "普通", "rows"))
        self.assertEqual((ev.state.difficulty, ev.state.difficulty_level), (NORMAL, LEVEL_NORMAL))
        conflicts = [r for r in s.recent_events(60) if r["kind"] == "read:difficulty"]
        self.assertEqual(conflicts[-1]["conflict"], "screen_vs_rows")

    def test_room_against_cap_keeps_status_quo(self):
        # 房间读到噩梦，异变图书馆的红叉却在第 18 格（困难）：房间读数可能过时，按读不准处理（困难及以上，不按噩梦加分）
        s = self.lobby(screen("team_room", "噩梦"))
        s.on_hud(hud(round=1, map="异变图书馆"))
        self.cap(s, 18)
        ev = s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.source, info.candidates), (LEVEL_HARD_PLUS, None, "cap", ("困难",)))
        self.assertEqual(info.label, "困难及以上")
        self.assertEqual(info.note, "组队房间的难度选项为噩梦，与进度上限不符：进度条红叉在第 18 格，地图「异变图书馆」困难难度为 18 格")
        self.assertEqual(ev.state.difficulty_level, LEVEL_HARD_PLUS)
        # 红叉在第 17 格（噩梦、疯狂）时不矛盾：按房间读数
        s = self.lobby(screen("team_room", "噩梦"))
        s.on_hud(hud(round=1, map="异变图书馆"))
        self.cap(s, 17)
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "噩梦", "team_room"))

    def test_room_against_cap_before_rows(self):
        # 同上，但还没有出现筹码三选一（概率行没判定）：推算的候选是普通、困难，是不是普通难度都还不知道，
        # 和读不到时一样（档位 None，悬浮窗不显示难度，结束记录也不写难度）；矛盾照记。概率行判定为困难及以上后按上一条
        s = self.lobby(screen("team_room", "噩梦"))
        s.on_hud(hud(round=1, map="异变图书馆"))
        self.cap(s, 18)
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.source, info.candidates), (None, None, None, ("普通", "困难")))
        self.assertIsNone(info.label)
        self.assertEqual(info.note, "组队房间的难度选项为噩梦，与进度上限不符：进度条红叉在第 18 格，"
                                    "地图「异变图书馆」普通、困难难度均为 18 格")
        self.assertEqual([r.get("conflict") for r in s.recent_events(60) if r["kind"] == "read:difficulty"][-1],
                         "screen_vs_cap")
        state = s.build_state(frame(OFFER_A, probabilities=None))
        self.assertEqual((state.difficulty, state.difficulty_level), (None, None))
        ev = s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual((s.difficulty_info.level, s.difficulty_info.candidates), (LEVEL_HARD_PLUS, ("困难",)))
        self.assertEqual(ev.state.difficulty_level, LEVEL_HARD_PLUS)

    def test_room_against_cap_both_nightmare_plus(self):
        # 房间读数和推算矛盾、但两边都在噩梦及以上：两边都认的这一档不会错，档位按噩梦及以上，难度名不写（分不出哪边对），
        # 悬停照旧写哪里不符，矛盾照记。房间读到疯狂、御魂庆典红叉在第 17 格（只有极限是 17 格）
        s = self.lobby(screen("team_room", "疯狂"))
        s.on_hud(hud(round=1, map="御魂庆典"))
        self.cap(s, 17)
        ev = s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.source, info.candidates), (LEVEL_NIGHTMARE, None, "cap", ("极限",)))
        self.assertEqual(info.label, "噩梦及以上")
        self.assertEqual(info.note, "组队房间的难度选项为疯狂，与进度上限不符：进度条红叉在第 17 格，地图「御魂庆典」极限难度为 17 格")
        self.assertEqual((ev.state.difficulty_level, ev.state.difficulty_name), (LEVEL_NIGHTMARE, None))
        traced = [r for r in s.recent_events(60) if r["kind"] == "read:difficulty"]
        self.assertEqual((traced[-1]["level"], traced[-1]["conflict"]), (LEVEL_NIGHTMARE, "screen_vs_cap"))
        # 房间读到噩梦、幽魂暗巷红叉在第 16 格（只有疯狂是 16 格）：同样按噩梦及以上，还没出现三选一时也是
        s = self.lobby(screen("team_room", "噩梦"))
        s.on_hud(hud(round=1, map="幽魂暗巷"))
        self.cap(s, 16)
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.source, info.candidates), (LEVEL_NIGHTMARE, None, "cap", ("疯狂",)))
        # 一边是困难的照旧按困难及以上：房间读到困难、异变图书馆红叉在第 17 格（噩梦、疯狂）
        s = self.lobby(screen("team_room", "困难"))
        s.on_hud(hud(round=1, map="异变图书馆"))
        self.cap(s, 17)
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.candidates), (LEVEL_HARD_PLUS, None, ("噩梦", "疯狂")))
        self.assertEqual(info.label, "困难及以上")

    def test_result_and_match_found_trusted_over_cap(self):
        # 「匹配成功」画面和结算画面上读到的直接用；和推算不一致时只记识别读数（核对资料用）
        s = self.lobby(screen("match_found", "困难", "异变图书馆"))
        self.assertEqual((s.settings.map, s.map_source), ("异变图书馆", "match"))
        self.cap(s, 17)
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_HARD, "困难", "match_found"))
        self.assertIn("screen_vs_cap", [r.get("conflict") for r in s.recent_events(60)])
        # 结算画面优先于「匹配成功」画面
        s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 12))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "噩梦", "result"))

    def test_latest_room_kind_wins(self):
        s = self.lobby(screen("team_room", "噩梦"), screen("custom_room", "困难"), screen("team_room", "疯狂"))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "疯狂", "team_room"))
        s = self.lobby(screen("team_room", "噩梦"), screen("create_room", "困难", "星趴·梦想号"))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_HARD, "困难", "create_room"))
        self.assertEqual((s.settings.map, s.map_source), ("星趴·梦想号", "room"))

    def test_extreme_not_contradicted_by_cap(self):
        # 画面上读到极限时不和推算比，直接用（梦想号、水乡古镇的极限和疯狂进度上限相同，推算分不出）
        s = self.lobby(screen("team_room", "极限"))
        s.on_hud(hud(round=1, map="御魂庆典"))
        self.cap(s, 20)
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "极限", "team_room"))

    def test_cap_inference(self):
        # 没有画面读数：按地图和进度上限推算（10-03 那局：异变图书馆 17 格，噩梦、疯狂相同）
        s = ready(self.db, "阿尔")
        s.on_hud(hud(round=3, map="异变图书馆"))
        kinds(s)
        self.cap(s, 17, current=3)
        level, name, note, cands = infer_difficulty(self.db, "异变图书馆", 17)
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, None, "cap", note, cands))
        self.assertEqual(s.difficulty_info.label, "噩梦及以上")
        self.assertEqual(len(kinds(s, "status")), 1)  # 胶囊出现「难度 噩梦及以上」
        ev = s.on_frame(frame(["摩托头盔-高级", "额外电池", "速度轮滑-中级"], probabilities=self.HARD_ROW))
        self.assertEqual((ev.state.difficulty, ev.state.difficulty_level, ev.state.difficulty_name),
                         (None, LEVEL_NIGHTMARE, None))
        helmet = next(e for e in ev.advice.evals if e.chip.name == "摩托头盔-高级")
        self.assertIn("难度", [p.label for p in helmet.parts])  # 引擎按噩梦及以上加分
        self.assertEqual(kinds(s, "status"), [])  # 概率行（困难及以上）不改变推算结果
        traced = [r for r in s.recent_events(60) if r["kind"] == "read:difficulty"]
        self.assertEqual((traced[-1]["level"], traced[-1]["source"]), (LEVEL_NIGHTMARE, "cap"))

    def test_cannot_infer(self):
        # 御魂庆典 20 格 + 概率行困难及以上：分不出困难还是噩梦，困难及以上（说明写原因）
        s = ready(self.db)
        s.on_hud(hud(round=3, map="御魂庆典"))
        self.cap(s, 20, current=3)
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        info = s.difficulty_info
        self.assertEqual((info.level, info.source, info.note), (LEVEL_HARD_PLUS, "cap", "地图「御魂庆典」困难与噩梦的进度上限相同"))
        # 只有概率行（地图待识别）：困难及以上，说明写推算不出的原因
        s = ready(self.db)
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_HARD_PLUS, None, "rows", "地图待识别"))
        # 什么都没有
        self.assertEqual(ready(self.db).difficulty_info.level, None)
        self.assertIsNone(ready(self.db).difficulty_info.label)

    def test_cap_mismatch_traced(self):
        # 水乡古镇红叉在第 20 格（只有普通是 20 格），概率行却是困难及以上：资料和读数对不上，记识别读数
        s = ready(self.db)
        s.on_hud(hud(round=3, map="水乡古镇"))
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.cap(s, 20, current=3)
        self.assertEqual(s.difficulty_info.level, LEVEL_HARD_PLUS)
        traced = [r for r in s.recent_events(60) if r["kind"] == "read:difficulty"]
        self.assertEqual(traced[-1]["conflict"], "cap_mismatch")

    def test_extreme_from_cap(self):
        # 御魂庆典红叉在第 17 格：四个难度都对不上、只有极限是 17 格，推算成极限，不再记 cap_mismatch；难度胶囊写「极限」，
        # 交给引擎的难度名也是极限（一局的筹码任务数取极限的 4 个，rules.toml [extreme] 的几个筹码加分）
        s = ready(self.db)
        s.on_hud(hud(round=3, map="御魂庆典"))
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.cap(s, 17, current=3)
        info = s.difficulty_info
        self.assertEqual((info.level, info.name, info.source, info.candidates), (LEVEL_NIGHTMARE, "极限", "cap", ("极限",)))
        self.assertEqual(info.label, "极限")
        traced = [r for r in s.recent_events(60) if r["kind"] == "read:difficulty"]
        self.assertEqual((traced[-1]["name"], traced[-1].get("conflict")), ("极限", None))
        st = s.build_state(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual((st.difficulty_level, st.difficulty_name), (LEVEL_NIGHTMARE, "极限"))

    def test_new_game_clears_and_uses_pending(self):
        s = self.lobby(screen("team_room", "噩梦"))
        clock = [100.0]
        s._clock = lambda: clock[0]
        s.on_hud(hud(round=6))
        s.on_hud(hud(round=6))
        # 对局中隔一会儿两次读到房间：这一局结束，房间上的难度留给下一局
        self.assertIsNone(s.on_out_of_game(screen("team_room", "困难")))
        clock[0] += 3
        self.assertEqual(s.on_out_of_game(screen("team_room", "困难")).kind, "game_over")
        self.assertEqual(s.game.difficulty_evidence, {"team_room": "噩梦"})
        self.assertEqual(s.pending._next_difficulty, {"team_room": "困难"})
        s.new_game()
        self.assertEqual(s.game.difficulty_evidence, {"team_room": "困难"})
        self.assertEqual(s.pending._next_difficulty, {})
        self.assertEqual(s.difficulty_info.name, "困难")
        s.new_game()
        self.assertEqual(s.difficulty_info, DifficultyInfo(note="地图待识别"))

    def test_undo_restores_readings(self):
        s = self.lobby(screen("team_room", "噩梦"))
        for rnd in (2, 2, 3, 3):
            s.on_hud(hud(round=rnd))
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
        self.assertEqual(s.difficulty_info.level, None)  # 新的一局清空
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.game.difficulty_evidence, {"team_room": "噩梦"})
        self.assertEqual(s.difficulty_info.name, "噩梦")

    def test_not_saved(self):
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            path = Path(tmp) / "state.json"
            s = Session(self.db, Settings(hero="阿尔"), path)
            s.on_out_of_game(screen("match_found", "噩梦", "异变图书馆"))
            s.on_select_screen()
            s.on_hud(hud(round=1))
            s.save()
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("difficulty", "difficulty_level", "difficulty_name", "difficulty_evidence", "map", "game_over"):
                self.assertNotIn(key, data)
        self.assertNotIn("difficulty_level", Settings.__dataclass_fields__)
        self.assertNotIn("difficulty_level", GAME_FIELDS)

    def test_status_when_pill_changes(self):
        # 难度胶囊上的写法变了时恰好发 1 个 status（放进待取队列），写法没变时不发
        s = Session(self.db, Settings(hero="阿尔"))
        s.on_out_of_game(screen("team_room", "噩梦"))
        s.on_select_screen()
        kinds(s)
        s.on_hud(hud(round=1, map="异变图书馆"))
        self.assertEqual(len(kinds(s, "status")), 1)  # 「难度 噩梦」
        self.cap(s, 17)
        s.on_hero(SimpleNamespace(hero="阿尔"))
        s.on_frame(frame(OFFER_A, probabilities=self.HARD_ROW))
        self.assertEqual(kinds(s, "status"), [])  # 推算、概率行都和房间读数一致
        s.on_out_of_game(screen("result", "疯狂", "异变图书馆", 12))
        self.assertEqual(len(kinds(s, "status")), 1)  # 「难度 疯狂」

    def test_labels(self):
        self.assertEqual(set(DIFFICULTY_SOURCE_LABEL), {"result", "match_found", *ROOM_KINDS, "cap", "rows"})
        self.assertEqual(set(OUT_OF_GAME_LABEL), {"result", "match_found", *ROOM_KINDS, "reward", "main_menu"})
        self.assertEqual((MAP_SOURCE_LABEL["match"], MAP_SOURCE_LABEL["room"]), ("「匹配成功」画面", "房间设置"))
        self.assertEqual(DifficultyInfo(LEVEL_HARD_PLUS).label, "困难及以上")
        self.assertEqual(DifficultyInfo(LEVEL_NIGHTMARE, "噩梦").label, "噩梦")
        snap = Session(self.db, Settings()).snapshot()
        json.dumps(snap, ensure_ascii=False)
        self.assertEqual(snap["difficulty"]["level"], None)


class GameOverTest(unittest.TestCase):
    """对局结束（识别结算页，标为本局已结束）：结算画面，或对局状态还在时隔一会儿连续两次读到房间、
    「匹配成功」画面、奖励画面。状态保留到下一局；之后的新一局按选角界面、连续两次读到的轮次或三选一开始，
    结算画面结束的不可撤销，房间等结束的可以撤销（tests/test_session_regressions.OutOfGameTwiceTest）。"""

    RESULT = SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=12)

    def setUp(self):
        self.db = load_db()
        self.t = 1000.0

    def tick(self, seconds=3.0):
        self.t += seconds

    def playing(self, map="异变图书馆") -> Session:
        """一局打到第 12 轮、3 星、进度 12/17，有两个筹码。"""
        s = ready(self.db, "阿尔")
        s._clock = lambda: self.t
        s.set_owned(["书签", "标靶"])
        for _ in range(2):
            self.tick()
            s.on_hud(hud(round=12, map=map))
            s.on_stars(own(3))
            s.on_progress(12, 17)
        kinds(s)
        return s

    def test_result_ends_game(self):
        s = self.playing()
        s.on_frame(frame(OFFER_A))
        self.assertEqual(close(s).kind, "missed_pick")
        s.game.unrecorded, s.game.panel_needed = 1, True
        s._undo = (s._undo_point(), "round", 1)
        ev = s.on_out_of_game(self.RESULT)
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到结算画面（异变图书馆、噩梦、12 轮）"))
        self.assertTrue(s.game.game_over)
        self.assertFalse(s.can_undo_new_game)
        self.assertIsNone(s.game.missed_offer)
        self.assertEqual(s.game.unrecorded, 0)
        self.assertFalse(s.game.panel_needed)
        # 这一局的状态保留到下一局开始
        st = s.settings
        self.assertEqual((st.round, st.star, st.map, st.owned, s.game.progress), (12, 3, "异变图书馆", ["书签", "标靶"], 12))
        self.assertEqual(s.game_over_info, {"why": ev.why, "map": "异变图书馆", "difficulty": "噩梦",
                                            "level": LEVEL_NIGHTMARE, "rounds": 12, "cap": 17,
                                            "outcome": None, "how": None})  # 没看到通关画面、还没读到奖励画面
        self.assertEqual(s.difficulty_info.source, "result")
        self.assertIsNone(s.on_out_of_game(self.RESULT))  # 结算画面停留期间不再发
        records = s.recent_events(60)
        self.assertIn("read:game_over", [r["kind"] for r in records])
        cap = next(r for r in records if r["kind"] == "read:difficulty_cap")
        self.assertEqual((cap["map"], cap["cap"], cap["difficulty"]), ("异变图书馆", 17, "噩梦"))
        snap = s.snapshot()
        json.dumps(snap, ensure_ascii=False)
        self.assertEqual((snap["game_over"], snap["ended"]["rounds"]), (True, 12))

    def test_result_without_game_state(self):
        s = Session(self.db, Settings(hero="阿尔"))
        self.assertIsNone(s.on_out_of_game(self.RESULT))
        self.assertFalse(s.game.game_over)
        self.assertIsNone(s.game_over_info)
        self.assertIn("read:result_without_game", [r["kind"] for r in s.recent_events(60)])
        self.assertIsNone(s.on_out_of_game(None))
        self.assertIsNone(s.on_out_of_game(screen("lobby")))  # 不认识的画面

    def test_room_then_select_starts_next_game(self):
        s = self.playing()
        self.assertIsNone(s.on_out_of_game(screen("team_room", "噩梦")))  # 只读到一次：还不算结束
        self.tick()
        ev = s.on_out_of_game(screen("team_room", "噩梦"))
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到组队房间"))
        self.assertEqual(s.pending._next_difficulty, {"team_room": "噩梦"})
        self.assertNotIn("team_room", s.game.difficulty_evidence)  # 房间上的难度属于下一局
        self.tick()
        self.assertIsNone(s.on_out_of_game(screen("team_room", "噩梦")))  # 已结束：不再发
        ev = s.on_select_screen()
        # 选角界面只在开局时出现：确实开了新的一局，不可撤销
        self.assertEqual((ev.kind, ev.undo, ev.cause, ev.why), ("newgame", False, "select", "上一局已结束，检测到选角界面"))
        self.assertFalse(s.can_undo_new_game)
        self.assertFalse(s.game.game_over)
        self.assertEqual((s.settings.owned, s.settings.round, s.settings.map), ([], None, None))
        self.assertIsNone(s.on_select_screen())  # 选角界面的后续几帧
        s.on_hud(hud(round=1))
        self.assertEqual(kinds(s, "panel_prompt"), [])  # 从选角界面开始看到的一局不请用户打开面板
        self.assertEqual(s.difficulty_info.name, "噩梦")

    def test_round_after_game_needs_two_reads(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        self.tick()
        self.assertIsNone(s.on_hud(hud(round=1)))  # 只读到一次：仍是已结束
        self.assertTrue(s.game.game_over)
        self.assertEqual(s.settings.round, 12)
        self.tick()
        # 20 秒内刚读到 3 星（这一局在继续的读数）也不否决：上一局已经结束
        ev = s.on_hud(hud(round=1))
        self.assertEqual((ev.kind, ev.undo, ev.cause), ("newgame", False, "round"))
        self.assertEqual(ev.why, "上一局已结束，检测到第 1 轮的对局画面")
        self.assertEqual(s.settings.round, 1)
        self.assertFalse(s.game.panel_needed)  # 第 1、2 轮：开局后还没拿过筹码
        self.assertEqual(kinds(s, "panel_prompt"), [])
        self.assertFalse(s.can_undo_new_game)

    def test_late_round_after_game_asks_panel(self):
        # 切到后台期间开了下一局、回来时已经第 7 轮：不受 NEWGAME_ROUND_MAX 限制，直接开新的一局，请用户打开面板
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        self.tick(300)
        s.on_hud(hud(round=7))
        self.tick()
        self.assertEqual(s.on_hud(hud(round=7)).kind, "newgame")
        self.assertEqual(s.settings.round, 7)
        self.assertTrue(s.game.panel_needed)
        self.assertEqual([e.why for e in kinds(s, "panel_prompt")], [""])

    def test_round_votes(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        s.on_hud(hud(round=5))
        self.tick(END_ROUND_CONFIRM_S + 1)
        self.assertIsNone(s.on_hud(hud(round=5)))  # 隔得太久的两次不算连续
        self.tick()
        self.assertIsNone(s.on_hud(hud(round=6)))  # 不是同一个数
        self.assertIsNone(s.on_hud(hud(round=None)))
        self.assertIsNone(s.on_hud(hud(round=30)))  # 范围外的读数不算
        self.tick()
        self.assertEqual(s.on_hud(hud(round=6)).kind, "newgame")

    def test_map_from_tasks_with_round(self):
        # 开新一局的那次读数里任务栏开着：地图照常识别（事件放进待取队列）
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        kinds(s)
        s.on_hud(hud(round=2))
        h = hud(round=2, map="水乡古镇", map_confidence="high", map_candidates=["水乡古镇"], tasks_seen=["某任务"])
        self.assertEqual(s.on_hud(h).kind, "newgame")
        self.assertEqual((s.settings.map, s.map_source), ("水乡古镇", "tasks"))
        self.assertEqual([e.kind for e in kinds(s)], ["map_read", "status"])

    def test_offer_after_game(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        self.assertEqual([e.kind for e in kinds(s)], ["status"])  # 难度胶囊由「噩梦及以上」（推算）改为「噩梦」
        ev = s.on_frame(frame(OFFER_B))
        self.assertEqual((ev.kind, ev.undo, ev.cause), ("newgame", False, "offer"))
        self.assertEqual(ev.why, "上一局已结束，检测到筹码三选一界面")
        # 新一局在前，这次三选一的事件在后（角色重新识别前暂停推荐）；先看到三选一，请用户打开面板。
        # 概率行判定为困难及以上：难度胶囊「困难及以上」（status）
        self.assertEqual([e.kind for e in kinds(s)], ["panel_prompt", "status", "hero_needed"])
        s.on_hero(SimpleNamespace(hero="阿尔"))
        ev = s.on_frame(frame(OFFER_B, rerolls=1))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.state.owned, ())  # 上一局的筹码不算进这一局
        self.assertEqual(s.settings.owned, [])

    def test_reads_ignored_after_game(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        kinds(s)
        for _ in range(3):
            self.tick()
            self.assertIsNone(s.on_stars(own(0)))
            self.assertIsNone(s.on_progress(1, 16))
            self.assertIsNone(s.on_hero(SimpleNamespace(hero="凛")))
            self.assertIsNone(s.on_teammates([{"row": 0, "hero": "恋", "bot": True}]))
            self.assertIsNone(s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=["大背包"],
                                                         unknown_slots=0, confidence=0.9)))
            s.note_own(3, "某玩家")
        self.assertEqual(kinds(s), [])
        st = s.settings
        self.assertEqual((st.star, s.game.progress, s.game.progress_cap, s.game.seen_hero, st.owned, s.game.teammates, s.game.own_row),
                         (3, 12, 17, "阿尔", ["书签", "标靶"], [], None))
        self.assertFalse(s.wants_map)

    def test_room_mid_game_ends(self):
        s = self.playing()
        self.assertIsNone(s.on_out_of_game(screen("custom_room", "疯狂", "水乡古镇")))
        self.tick()
        ev = s.on_out_of_game(screen("custom_room", "疯狂", "水乡古镇"))
        self.assertEqual((ev.kind, ev.why), ("game_over", "检测到自建房间"))
        self.assertEqual((s.pending._next_difficulty, s.pending._next_map), ({"custom_room": "疯狂"}, ("水乡古镇", "room")))
        self.assertEqual(s.settings.map, "异变图书馆")  # 这一局的地图不变
        ev = s.on_out_of_game(screen("match_found", "疯狂", "水乡古镇"))
        self.assertIsNone(ev)
        self.assertEqual(s.pending._next_map, ("水乡古镇", "match"))
        self.assertEqual(s.on_select_screen().kind, "newgame")
        # 难度随即用上；地图等到第一次对局内读数（选角界面期间没有对局状态，后续几帧不再判新一局）
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "疯狂", "match_found"))
        self.assertIsNone(s.settings.map)
        for _ in range(2):
            self.assertIsNone(s.on_select_screen())
        s.on_hud(hud(round=1))
        self.assertEqual((s.settings.map, s.map_source, s.map_confidence), ("水乡古镇", "match", "high"))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "疯狂", "match_found"))
        self.assertEqual(s.games, 1)

    def test_match_found_map_then_select_frames(self):
        # 结束后「匹配成功」画面带地图，选角界面连续好几帧：只判一次新的一局（不可撤销），地图、难度都留到进入对局，
        # 识别记录只换一段（地图要是在选角界面那一帧就写进对局状态，第二帧会再判一次可撤销的新一局，
        # 把地图和难度清掉，刚打完的一局也会被挤出诊断包的导出范围）
        s = self.playing()
        rec = s.eventlog.recorder = FakeRecorder()
        s.on_out_of_game(self.RESULT)
        s.on_out_of_game(screen("team_room", "噩梦"))
        s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
        events = [s.on_select_screen() for _ in range(3)]
        self.assertEqual([(e.kind, e.undo, e.cause) for e in events if e is not None], [("newgame", False, "select")])
        self.assertEqual(kinds(s, "newgame"), [])
        self.assertFalse(s.can_undo_new_game)
        self.assertFalse(s.has_game_state)
        self.assertEqual(s.games, 1)
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "噩梦", "match_found"))
        self.tick(30)
        s.on_hud(hud(round=1))
        self.assertEqual((s.settings.map, s.map_source), ("水乡古镇", "match"))
        self.assertFalse(s.wants_map)  # 地图可靠：不用请用户打开任务栏
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "噩梦", "match_found"))
        self.assertEqual([c[0] for c in rec.calls], ["mark", "new_segment"])
        self.assertEqual(s.games, 1)

    def test_match_cancelled_during_select(self):
        # 选角界面期间回到了房间（那次匹配没有开始）：不算一局结束；「匹配成功」画面上的难度和地图作废，
        # 下一次「匹配成功」画面没截到时，按房间上的难度、照常识别地图（不沿用上一次匹配的地图）
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
        self.assertEqual(s.on_select_screen().kind, "newgame")
        kinds(s)
        self.assertIsNone(s.on_out_of_game(screen("team_room", "困难")))
        self.assertFalse(s.game.game_over)
        self.assertEqual(len(kinds(s, "status")), 1)  # 难度胶囊收起（「匹配成功」画面上的噩梦作废）
        self.assertIsNone(s.pending._next_map)
        self.assertIn("read:match_cancelled", [r["kind"] for r in s.recent_events(60)])
        self.assertIsNone(s.on_select_screen())
        s.on_hud(hud(round=1))
        self.assertIsNone(s.settings.map)
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_HARD, "困难", "team_room"))
        self.assertEqual(s.games, 1)
        # 回到房间后又截到了「匹配成功」画面：用新的这一次
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
        s.on_select_screen()
        s.on_out_of_game(screen("team_room", "疯狂"))
        s.on_out_of_game(screen("match_found", "疯狂", "魔法学院"))
        self.assertIsNone(s.on_select_screen())
        s.on_hud(hud(round=1))
        self.assertEqual((s.settings.map, s.map_source), ("魔法学院", "match"))
        self.assertEqual(s.difficulty_info, DifficultyInfo(LEVEL_NIGHTMARE, "疯狂", "match_found"))
        self.assertEqual(s.games, 1)

    def test_begin_offer_before_reads(self):
        # 管线在三选一这一帧读轮次、星数之前调 begin_offer：先开新的一局，这一帧的轮次、角色、星数
        # 读数记进新的一局，这次推荐就用上它们（不调时由 on_frame 开新的一局，见 test_offer_after_game）
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        kinds(s)
        self.tick(300)
        ev = s.begin_offer()
        self.assertEqual((ev.kind, ev.undo, ev.cause, ev.why),
                         ("newgame", False, "offer", "上一局已结束，检测到筹码三选一界面"))
        self.assertIsNone(s.begin_offer())  # 没有结束：什么都不做
        s.on_hud(hud(round=7))
        s.on_hero(SimpleNamespace(hero="阿尔"))
        s.on_stars(own(2), in_select=True)
        ev = s.on_frame(frame(OFFER_B))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual((ev.state.round, ev.state.star, ev.state.owned), (7, 2, ()))
        self.assertEqual(len(kinds(s, "panel_prompt")), 1)  # 先看到三选一：请用户打开面板
        self.assertEqual(s.games, 1)

    def test_offer_open_when_game_ends(self):
        # 三选一消失后 GONE_FRAMES 帧之内读到了结算画面：没看到选中的，不请用户补录；
        # 看到了选中的，照常记进刚结束的这一局
        s = self.playing()
        s.on_frame(frame(OFFER_A))
        s.on_out_of_game(self.RESULT)
        self.assertEqual(close(s).kind, "idle")
        self.assertIsNone(s.game.missed_offer)
        self.assertTrue(s.game.game_over)
        s = self.playing()
        s.on_frame(frame(OFFER_A, selected=1))
        s.on_out_of_game(self.RESULT)
        ev = close(s)
        self.assertEqual((ev.kind, ev.chip), ("picked", OFFER_A[1]))
        self.assertEqual(s.settings.owned, ["书签", "标靶", OFFER_A[1]])
        self.assertTrue(s.game.game_over)

    def test_reward_and_match_found_end_game(self):
        for kind, why in (("reward", "检测到奖励画面"), ("match_found", "检测到「匹配成功」画面"),
                          ("create_room", "检测到创建房间界面")):
            with self.subTest(kind=kind):
                s = self.playing()
                self.assertIsNone(s.on_out_of_game(screen(kind, "噩梦" if kind != "reward" else None)))
                self.tick()
                ev = s.on_out_of_game(screen(kind, "噩梦" if kind != "reward" else None))
                self.assertEqual((ev.kind, ev.why), ("game_over", why))
                self.assertEqual(s.pending._next_difficulty, {} if kind == "reward" else {kind: "噩梦"})

    def test_loading_after_game_belongs_to_next_game(self):
        s = self.playing(map=None)
        self.assertIsNone(s.settings.map)
        s.on_out_of_game(self.RESULT)
        self.assertEqual(s.game_over_info["map"], "异变图书馆")  # 这一局的地图没认出来时用结算画面上的
        self.assertTrue(s.wants_loading)
        self.assertIsNone(s.on_loading("水乡古镇"))
        self.assertIsNone(s.settings.map)
        for _ in range(3):
            s.on_select_screen()
        self.assertEqual(s.games, 1)
        s.on_hud(hud(round=1))
        self.assertEqual((s.settings.map, s.map_source), ("水乡古镇", "loading"))

    def test_loading_after_game_when_map_known(self):
        # 上一局的地图已知（任务栏），或手动指定过：结束后照样找加载画面，读到的地图留给下一局
        for manual in (False, True):
            with self.subTest(manual=manual):
                s = self.playing()
                if manual:
                    s.set_map("异变图书馆")
                self.assertFalse(s.wants_loading)  # 对局中地图已知：不找
                s.on_out_of_game(self.RESULT)
                self.assertTrue(s.wants_loading)
                self.assertIsNone(s.on_loading("水乡古镇"))
                self.assertEqual((s.settings.map, s.pending._next_map), ("异变图书馆", ("水乡古镇", "loading")))
                self.assertIsNone(s.on_loading("不是地图名"))
                self.tick()
                s.on_hud(hud(round=1))
                self.tick()
                self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
                self.assertEqual((s.settings.map, s.map_source), ("水乡古镇", "loading"))
                self.assertFalse(s.wants_loading)

    def test_resume_after_game(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        s.on_resume(300)
        self.assertFalse(s.game.panel_needed)
        self.assertFalse(s.game._resume_check)

    def test_new_game_clears_game_over(self):
        s = self.playing()
        s.on_out_of_game(self.RESULT)
        s.new_game()
        self.assertFalse(s.game.game_over)
        self.assertIsNone(s.game_over_info)
        # 新一局里照常读数、照常判新一局
        s.on_hud(hud(round=1))
        self.assertTrue(s.has_game_state)


class FakeRecorder:
    """识别记录器（gamelog.GameRecorder）的假对象：记下 add 的记录和换段、撤销、标记的调用。"""

    def __init__(self):
        self.added: list[dict] = []
        self.calls: list[tuple] = []

    def add(self, t, record):
        self.added.append(dict(record))

    def new_segment(self, reason, **info):
        self.calls.append(("new_segment", reason, info))

    def undo_segment(self):
        self.calls.append(("undo_segment",))
        return True

    def mark(self, kind, **info):
        self.calls.append(("mark", kind, info))


class RecorderTest(unittest.TestCase):
    """识别记录边识别边写盘。Session 把 note、trace 的每一条交给 recorder，判定新的一局时换段，撤销时接回，
    对局结束时记标记。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)
        self.rec = self.session.eventlog.recorder = FakeRecorder()

    def test_forwarded_like_recent_events(self):
        s = self.session
        s.note([Event("picked", chip="书签", offer=OFFER_A)])
        s.trace("hud", round=3, map=None)
        self.assertEqual(self.rec.added, [{k: v for k, v in r.items() if k != "time"} for r in s.recent_events(60)])
        self.assertEqual([r["kind"] for r in self.rec.added], ["picked", "read:hud"])

    def test_new_segment_and_undo(self):
        s = self.session
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=1))
        ev = s.on_hud(hud(round=1))
        self.assertEqual(self.rec.calls, [("new_segment", "newgame", {"cause": "round", "why": ev.why})])
        self.assertTrue(s.undo_new_game())
        self.assertEqual(self.rec.calls[-1], ("undo_segment",))
        self.assertFalse(s.undo_new_game())
        self.assertEqual(len(self.rec.calls), 2)  # 没有可撤销的：不再接回

    def test_game_over_mark_and_next_segment(self):
        s = self.session
        s.on_hud(hud(round=12, map="异变图书馆"))
        ev = s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 12))
        kind, name, info = self.rec.calls[-1]
        self.assertEqual((kind, name, info["why"], info["rounds"]), ("mark", "game_over", ev.why, 12))
        ev = s.on_select_screen()
        self.assertEqual(self.rec.calls[-1], ("new_segment", "newgame", {"cause": "select", "why": ev.why}))
        self.assertFalse(s.undo_new_game())
        self.assertNotIn(("undo_segment",), self.rec.calls)

    def test_with_game_recorder(self):
        """和真的 GameRecorder 一起：一局、结算画面、带地图的「匹配成功」画面、选角界面连续几帧开新的一局，
        导出时两段都在（刚打完的一局没有被挤出导出范围），结束标记在第一段。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            rec = GameRecorder(Path(tmp) / "games").open("startup")
            s = ready(self.db)
            s.eventlog.recorder = rec
            s.on_hud(hud(round=12, map="异变图书馆"))
            s.note([s.on_out_of_game(screen("result", "噩梦", "异变图书馆", 12))])
            s.on_out_of_game(screen("match_found", "噩梦", "水乡古镇"))
            for _ in range(3):
                s.note([ev for ev in (s.on_select_screen(),) if ev is not None])
            rec.flush()
            records, meta = rec.collect()
            self.assertEqual([seg["reason"] for seg in meta["segments"]], ["startup", "newgame"])
            self.assertIn("mark:game_over", [r["kind"] for r in records])
            self.assertEqual(len(rec.segments()), 2)
            first = read_segment(rec.segments()[0])
            self.assertIn("mark:game_over", [r["kind"] for r in first])
            self.assertIn("game_over", [r["kind"] for r in first])
            second = read_segment(rec.segments()[1])
            self.assertEqual((second[0]["kind"], second[0]["cause"]), ("segment", "select"))
            self.assertEqual(second[-1]["kind"], "newgame")
            rec.close()

    def test_advice_record_has_superseded(self):
        chip = lambda name: SimpleNamespace(name=name)  # noqa: E731
        adv = SimpleNamespace(best="银行卡-余额多", reroll=None, evals=[
            SimpleNamespace(chip=chip("银行卡-余额多"), percent=1.0, superseded_by=None),
            SimpleNamespace(chip=chip("银行卡-余额少"), percent=0.0, superseded_by="银行卡-余额多"),
        ])
        out = _event_record(Event("advice", advice=adv))
        self.assertEqual(out["superseded"], {"银行卡-余额少": "银行卡-余额多"})
        out = _event_record(Event("advice", advice=SimpleNamespace(best="A", reroll=None, evals=[])))
        self.assertNotIn("superseded", out)


class LeftmostRarityTest(unittest.TestCase):
    """升星和筹码商店的最左格一定是这个星数的固定稀有度；不是的就是任务来源，等级按概率行（实机一局）。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def select(self, names, rarity=None, probabilities=(60, 37, 3), rerolls=2):
        f = frame(names, probabilities=probabilities, rerolls=rerolls)
        if rarity is not None:
            f.rarity = list(rarity)  # 画面上读到的图标边框颜色
        return f

    def test_purple_leftmost_at_zero_stars_is_task(self):
        # 实机：第 4 轮、0 星（三选一前后玩家列表都是 0 星，不是升星），概率行 60/37/3，
        # 候选从左到右「磨刀石（紫）/ 普通瞄具（紫）/ 速度轮滑-初级（蓝）」，不能记成升星
        s = self.session
        s.on_hud(hud(round=4))
        s.on_stars(own(0))
        s.on_stars(own(0), in_select=True)
        offer = ["磨刀石", "普通瞄具", "速度轮滑-初级"]
        self.assertEqual([self.db.chips[n].rarity for n in offer], ["紫", "紫", "蓝"])
        ev = s.on_frame(self.select(offer, rarity=["紫", "紫", "蓝"]))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (0, "task", 1))
        self.assertEqual(s.settings.star, 0)
        # 最左格是蓝色（0 星的固定稀有度）：仍按升星算
        ev = s.on_frame(self.select(["速度轮滑-初级", "普通瞄具", "磨刀石"], rarity=["蓝", "紫", "紫"], rerolls=1))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (0, "levelup", None))

    def test_database_rarity_when_border_unread(self):
        """读不到图标边框颜色时，用第 1 个候选筹码在资料库里的稀有度。"""
        s = self.session
        s.on_stars(own(0))
        ev = s.on_frame(self.select(["磨刀石", "普通瞄具", "速度轮滑-初级"]))  # 没有 rarity：磨刀石为紫
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (0, "task", 1))
        ev = s.on_frame(self.select(["磨刀石", "普通瞄具", "速度轮滑-初级"], rarity=[None, "紫", "蓝"], rerolls=1))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 1))
        ev = s.on_frame(self.select(["速度轮滑-初级", "普通瞄具", "磨刀石"], rerolls=0))
        self.assertEqual((ev.state.source, ev.state.tier), ("levelup", None))

    def test_border_color_wins_over_database(self):
        """画面上读到的边框颜色优先（同系列筹码名称没读全时，资料库的稀有度可能是另一个档次的）。"""
        s = self.session
        s.on_stars(own(0))
        ev = s.on_frame(self.select(["速度轮滑-初级", "普通瞄具", "书签"], rarity=["紫", "紫", "蓝"]))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 1))
        ev = s.on_frame(self.select(["磨刀石", "普通瞄具", "书签"], rarity=["蓝", "紫", "蓝"], rerolls=1))
        self.assertEqual((ev.state.source, ev.state.tier), ("levelup", None))

    def test_unknown_star_and_normal_difficulty(self):
        s = self.session
        # 读不到星数：按概率行估星数（2 星），最左格不是 2 星的固定稀有度（紫）→ 任务 2 级
        ev = s.on_frame(self.select(["速度轮滑-初级", "普通瞄具", "书签"], probabilities=(40, 45, 15)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "task", 2))
        close(s)
        # 普通难度同样：0 星、普通表 0~1 星那一行、最左格紫色 → 任务 1 级
        s.on_stars(own(0))
        ev = s.on_frame(self.select(["磨刀石", "普通瞄具", "速度轮滑-初级"], probabilities=(60, 30, 10)))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (0, "task", 1))
        self.assertEqual(ev.state.difficulty, NORMAL)


class TeammatesTest(unittest.TestCase):
    """队友角色（玩家列表上其余各行）。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db, "凛")
        self.rows = [{"row": 0, "hero": "阿兰娜", "bot": True}, {"row": 1, "hero": None, "bot": True},
                     {"row": 2, "hero": "恋", "bot": False}]

    def test_confirmed_after_two_reads(self):
        s = self.session
        self.assertTrue(s.wants_teammates)
        self.assertEqual(s.on_teammates(self.rows).kind, "status")
        self.assertEqual([t["hero"] for t in s.game.teammates], [None, None, None])
        s.on_teammates(self.rows)
        self.assertEqual(s.game.teammates, [
            {"row": 0, "hero": "阿兰娜", "bot": True}, {"row": 1, "hero": None, "bot": True},
            {"row": 2, "hero": "恋", "bot": False},
        ])
        self.assertTrue(s.wants_teammates)  # 第 2 行还没认出来
        self.assertIsNone(s.on_teammates(self.rows))
        s.on_teammates([{"row": 1, "hero": "派德曼", "bot": True}] * 2)
        self.assertFalse(s.wants_teammates)
        ev = s.on_frame(frame(OFFER_A))
        if "teammates" in State.__dataclass_fields__:
            got = [t["hero"] if isinstance(t, dict) else t.hero for t in ev.state.teammates]
            self.assertEqual(got, ["阿兰娜", "派德曼", "恋"])

    def test_own_hero_and_duplicates_rejected(self):
        s = self.session
        for _ in range(2):
            s.on_teammates([{"row": 0, "hero": "凛", "bot": False}])  # 队友不能和自己同角色
        self.assertIsNone(s.game.teammates[0]["hero"])
        for _ in range(3):
            s.on_teammates([{"row": 1, "hero": "恋", "bot": True}])
        for _ in range(2):
            s.on_teammates([{"row": 2, "hero": "恋", "bot": True}])
        heroes = {t["row"]: t["hero"] for t in s.game.teammates}
        self.assertEqual((heroes[1], heroes[2]), ("恋", None))  # 同一个角色只给票数多的那一行

    def test_cleared_on_new_game(self):
        s = self.session
        s.on_teammates(self.rows)
        s.new_game()
        self.assertEqual(s.game.teammates, [])


class HistoryTest(unittest.TestCase):
    def test_recent_events_and_snapshot_are_json(self):
        db = load_db()
        s = ready(db)
        ev = s.on_frame(frame(OFFER_A))
        s.note([ev, Event("picked", chip="书签", offer=OFFER_A)])
        s.trace("hud", round=3, map=None)
        recs = s.recent_events(60)
        self.assertEqual([r["kind"] for r in recs], ["advice", "picked", "read:hud"])
        self.assertIn(recs[0]["best"], [*OFFER_A, REROLL])
        self.assertEqual(recs[0]["state"]["hero"], "远野汉娜")
        self.assertRegex(recs[0]["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}$")
        json.dumps(recs, ensure_ascii=False)
        json.dumps(s.snapshot(), ensure_ascii=False)
        self.assertEqual(s.recent_events(-1), [])  # 只要「未来」的：没有


def ln(text, cx, cy, w, h):
    return Line(text, cx, cy, w, h)


class HudParseTest(unittest.TestCase):
    """不跑文字识别，直接用构造的文字段检验轮次和地图的判定。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.tasks = {k: v["tasks"] for k, v in json.loads((PROJECT / "data" / "maps.json").read_text(encoding="utf-8")).items()}

    def panel(self, tasks):
        """模拟点开的任务栏：每条任务一段文字，右边一段「0\\N」进度（繁体任务的量词「隻」「個」等也去掉）。"""
        lines = []
        for i, task in enumerate(tasks):
            count = re.search(r"\d+", task)
            text = re.sub(r"\d+\s*[只个艘次张条层名位颗隻個張條層顆]?\s*", "", task)
            lines.append(ln(text, 1400, 140 + 200 * i, 300, 70))
            lines.append(ln(f"0\\{count.group() if count else 1}", 2000, 140 + 200 * i, 80, 65))
        return lines

    def test_every_map_is_recognised_from_its_full_task_panel(self):
        """两两对比所有地图的任务：魔法学院的任务全都包含在海选赛运动场里，龙宫和淘汰赛共用 4 条。
        读全自己的任务栏时，每张地图都要被唯一认出。"""
        for name, tasks in self.tasks.items():
            with self.subTest(map=name):
                guess = _guess_map(self.db, self.panel(tasks))
                self.assertEqual(guess.map, name, guess.map_scores)
                self.assertIn(guess.map_confidence, ("high", "medium"))
                self.assertEqual(guess.map_candidates[0], name)
        self.assertEqual(_guess_map(self.db, self.panel(self.tasks["魔法学院"])).map_confidence, "medium")
        self.assertEqual(_guess_map(self.db, self.panel(self.tasks["海选赛运动场"])).map_confidence, "high")
        self.assertEqual(_guess_map(self.db, self.panel(self.tasks["龙宫游乐园"])).map_confidence, "high")

    SHARED = ["击倒大嗓门和练家子", "击倒 2 只蟹布罗", "击倒 2 只虾布罗", "击倒 3 只金小鱼"]  # 龙宫和淘汰赛共有的任务

    def test_shared_tasks_only_is_a_tie(self):
        """两张现役地图任务打平：匹配度低，请用户选。"""
        with mock.patch.object(hud_module, "retired_maps", return_value=frozenset()):
            guess = _guess_map(self.db, self.panel(self.SHARED))
        self.assertIsNone(guess.map)
        self.assertEqual(guess.map_confidence, "low")
        self.assertEqual(set(guess.map_candidates[:2]), {"龙宫游乐园", "淘汰赛运动场"})
        self.assertTrue(guess.tasks_seen)

    def test_retired_map_loses_ties(self):
        """已结束的限时地图降为后备候选。和现役地图打平时判为现役地图（匹配度中，可靠、不请用户选），
        仍列在候选里；只有它的任务条数领先时才认定为它。"""
        retired = retired_maps(self.db)
        self.assertTrue({"海选赛运动场", "淘汰赛运动场", "决赛大赛场"} <= retired)  # data/maps.json 的标注
        self.assertNotIn("异变图书馆", retired)  # 限时地图，但还没结束（到 2026-10-14）
        guess = _guess_map(self.db, self.panel(self.SHARED))
        self.assertEqual((guess.map, guess.map_confidence), ("龙宫游乐园", "medium"))
        self.assertEqual(guess.map_candidates[:2], ["龙宫游乐园", "淘汰赛运动场"])
        # 换过来：假设龙宫是已结束的，淘汰赛是现役的
        with mock.patch.object(hud_module, "retired_maps", return_value=frozenset({"龙宫游乐园"})):
            guess = _guess_map(self.db, self.panel(self.SHARED))
        self.assertEqual((guess.map, guess.map_candidates[0]), ("淘汰赛运动场", "淘汰赛运动场"))
        # 限时地图的任务条数领先：认定为它
        guess = _guess_map(self.db, self.panel(self.tasks["淘汰赛运动场"]))
        self.assertEqual((guess.map, guess.map_confidence), ("淘汰赛运动场", "high"))

    def test_retired_annotations(self):
        """限时地图的判定用资料库的 MapInfo.active（hud.retired_maps 据此得出）：写明已结束、或限时到期的不算现役。"""
        import datetime

        from advisor.data import MapInfo

        today = datetime.date(2026, 10, 2)
        self.assertFalse(MapInfo("m", None, 0.0, ended=True).active(today))
        self.assertFalse(MapInfo("m", None, 0.0, limited=True, until="2026-09-02").active(today))
        self.assertTrue(MapInfo("m", None, 0.0, limited=True, until="2026-10-14").active(today))
        self.assertFalse(MapInfo("m", None, 0.0, limited=True, until="2026-10-14").active(datetime.date(2026, 10, 15)))
        self.assertTrue(MapInfo("m", None, 0.0).active(today))

    def test_session_asks_only_when_every_match_is_low(self):
        """读全任意一张地图的任务栏：Session 直接采用（匹配度高或中），不请用户选；
        只读到两张地图共用的任务（打平、匹配度低）时才请用户选。"""
        for name, tasks in self.tasks.items():
            with self.subTest(map=name):
                session = Session(self.db, Settings())
                guess = _guess_map(self.db, self.panel(tasks))
                guess.round = 2
                session.on_hud(guess)
                self.assertEqual(session.settings.map, name)
                self.assertFalse(session.game.map_ask)
        session = Session(self.db, Settings())
        with mock.patch.object(hud_module, "retired_maps", return_value=frozenset()):
            guess = _guess_map(self.db, self.panel(self.SHARED))
        guess.round = 2
        session.on_hud(guess)
        self.assertIsNone(session.settings.map)
        self.assertTrue(session.game.map_ask)
        self.assertTrue({"龙宫游乐园", "淘汰赛运动场"} <= set(session.game.map_candidates))
        # 实际数据里淘汰赛运动场已结束：判为龙宫游乐园，不请用户选
        session = Session(self.db, Settings())
        guess = _guess_map(self.db, self.panel(self.SHARED))
        guess.round = 2
        session.on_hud(guess)
        self.assertEqual((session.settings.map, session.game.map_ask), ("龙宫游乐园", False))

    def test_partial_read_of_arena_is_not_high(self):
        """海选赛运动场的任务栏漏读了「裁判艾米」时和魔法学院分不开，不能说「确定」。"""
        tasks = [t for t in self.tasks["海选赛运动场"] if "艾米" not in t]
        self.assertNotEqual(_guess_map(self.db, self.panel(tasks)).map_confidence, "high")

    def test_hant_panel_scores_like_simplified(self):
        """任务栏是繁体时：一条任务的简体、繁体写法算同一条，读全繁体任务栏时各地图的得分、结论、候选和读全简体任务栏
        时相同；只读到其中几条（漏读最后一条、只读到第一条）时也和简体读到同样几条相同（读到几成按任务算，不按写法算，
        魔法学院、海选赛运动场这种靠读到的比例分开的照样分得开）。"""
        maps = json.loads((PROJECT / "data" / "maps.json").read_text(encoding="utf-8"))
        for name, info in maps.items():
            hant = [h if h is not None else s for s, h in zip(info["tasks"], info["hant_tasks"])]
            for picked in (slice(None), slice(None, -1), slice(None, 1)):
                with self.subTest(map=name, picked=picked):
                    simp = _guess_map(self.db, self.panel(info["tasks"][picked]))
                    trad = _guess_map(self.db, self.panel(hant[picked]))
                    self.assertEqual((trad.map, trad.map_confidence, trad.map_scores, trad.map_candidates),
                                     (simp.map, simp.map_confidence, simp.map_scores, simp.map_candidates))
        trad = _guess_map(self.db, self.panel(maps["魔法学院"]["hant_tasks"]))
        self.assertEqual((trad.map, trad.map_confidence), ("魔法学院", "medium"))

    def test_task_spellings_count_once(self):
        """同一条任务的简体、繁体写法都读到了（两行各对上一种写法）：这条任务只算对上一次。"""
        info = json.loads((PROJECT / "data" / "maps.json").read_text(encoding="utf-8"))["异变图书馆"]
        guess = _guess_map(self.db, self.panel([info["tasks"][0], info["hant_tasks"][0]]))
        self.assertEqual(guess.map_scores["异变图书馆"], 1)
        self.assertEqual(len(guess.tasks_seen), 2)

    def test_hud_without_task_panel(self):
        lines = [ln("魔法茶壶", 1160, 130, 120, 80), ln("线索：0/5", 3170, 97, 200, 68), ln("6/11", 1145, 230, 80, 98)]
        guess = _guess_map(self.db, lines)
        self.assertIsNone(guess.map)
        self.assertEqual(guess.tasks_seen, [])
        self.assertIsInstance(guess, Hud)

    def test_round_with_round_mark(self):
        lines = [ln("01", 219, 70, 127, 103), ln("轮", 319, 80, 58, 51), ln("地图说明", 699, 76, 273, 86)]
        self.assertEqual(_parse_round(lines, 3840, 2054), 1)

    def test_round_out_of_range_is_rejected(self):
        """「04」读成「00」：第 0 轮不存在，按读不出处理（read_round 随后在标记左边裁小块再认，没看见标记时放大重读）。"""
        from advisor.vision.hud import _round_like
        for text in ("00", "88"):
            with self.subTest(text=text):
                self.assertIsNone(_parse_round([ln(text, 219, 70, 127, 103), ln("轮", 319, 80, 58, 51)], 3840, 2054))
                self.assertIsNone(_parse_round([ln(f"{text}轮", 260, 75, 190, 103)], 3840, 2054))
                self.assertIsNone(_parse_round([ln(text, 114, 54, 72, 58)], 2000, 1087))  # 「轮」字认不出时的兜底
                self.assertTrue(_round_like([ln(text, 114, 54, 72, 58)], 2000 / 1920))
        self.assertFalse(_round_like([ln("04", 114, 54, 72, 58)], 2000 / 1920))

    def test_menu_edge_read_as_leading_one(self):
        """梦想号 21:9（2560x1057）实机：数字后面是白色立绘时，左边菜单按钮的边缘被连带读成「1」，「09」读成「109」。
        这段文字的左边伸进了菜单按钮的范围时去掉这个 1；左边没伸进去的三位数、去掉后不合理的，仍按读不出处理。"""
        w, h = 2560, 1057
        mark = ln("轮", 165, 43, 30, 30)
        self.assertEqual(_parse_round([ln("109", 94, 37, 130, 71), mark], w, h), 9)
        self.assertEqual(_parse_round([ln("109", 94, 37, 130, 71)], w, h), 9)  # 「轮」字没读出来时的兜底
        self.assertEqual(_parse_round([ln("119", 94, 37, 130, 71), mark], w, h), 19)
        self.assertIsNone(_parse_round([ln("121", 94, 37, 130, 71), mark], w, h))  # 第 21 轮不存在
        self.assertIsNone(_parse_round([ln("109", 120, 37, 80, 71), mark], w, h))  # 左边没伸进菜单按钮
        self.assertIsNone(_parse_round([ln("209", 94, 37, 130, 71), mark], w, h))

    def test_round_retried_when_digits_missed(self):
        """读到了「轮」字却没检测到数字（4:3 窗口缩到 1600×1200 时「11」整段漏检）：先在「轮」字左边裁一小块只认字，
        读到了就用；读不出按读不出处理，不换大小重读。没读到「轮」字、数字的位置上读成「t0」时，放大、缩小各再读一次。"""
        from unittest import mock

        import numpy as np

        from advisor.vision import hud, io

        img = np.zeros((1200, 1600, 3), np.uint8)
        calls = []

        def fake_ocr(img, box, width=None):
            calls.append(width)
            if len(calls) < 2:
                return [first]
            return [ln("11", 85, 30, 45, 55), ln("轮", 121, 36, 25, 25)]

        first = ln("轮", 121, 36, 25, 25)
        for reads, want in (([("11", 0.99)], 11), ([], None)):
            calls.clear()
            with self.subTest(reads=reads), mock.patch.object(io, "ocr", side_effect=fake_ocr), \
                    mock.patch.object(hud, "recognize_only", return_value=reads):
                self.assertEqual(hud.read_round(img), want)
                self.assertEqual(len(calls), 1)  # 只读了原大小那一遍
        first = ln("t0", 85, 30, 45, 55)
        calls.clear()
        with mock.patch.object(io, "ocr", side_effect=fake_ocr), mock.patch.object(hud, "recognize_only") as rec:
            self.assertEqual(hud.read_round(img), 11)
            rec.assert_not_called()
        self.assertEqual(len(calls), 2)  # 原大小、放大 1.5 倍（读到了就不再缩小重读）
        calls.clear()
        with mock.patch.object(io, "ocr", side_effect=lambda img, box, width=None: calls.append(width) or []):
            self.assertIsNone(hud.read_round(img))
        self.assertEqual(len(calls), 1)  # 什么都没读到（不在对局画面）：不重读

    def test_round_when_mark_is_garbled(self):
        """园林中庭（samples/extra 里那张 2000x1087 的三选一截图）：「轮」字和进度「19/16」糊成一团。"""
        lines = [
            ln("AstralParty_CN", 42, 9, 82, 16),
            ln("01", 114, 54, 72, 58),
            ln("ZOL/C砖", 212, 54, 138, 59),
            ln("地图说明", 364, 58, 146, 55),
            ln("1st", 70, 120, 78, 41),
        ]
        self.assertEqual(_parse_round(lines, 2000, 1087), 1)

    def test_progress_is_not_taken_as_round(self):
        w, h = 2000, 1087
        cases = {
            "进度拆成「19」「/16」": [ln("19", 140, 54, 60, 50), ln("/16", 190, 54, 60, 50)],
            "进度拆成「19/」「16」": [ln("19/", 120, 54, 70, 50), ln("16", 145, 54, 50, 50)],
            "斜杠没读出来": [ln("19", 120, 54, 50, 50), ln("16", 170, 54, 50, 50)],
            "进度在更右边": [ln("19", 180, 54, 50, 50)],
            "血量": [ln("14/14", 94, 484, 140, 72)],
            "排名下面的金币": [ln("12", 120, 330, 40, 40)],
            "轮次数字后面紧跟进度": [ln("01", 114, 54, 72, 58), ln("19/16", 190, 54, 100, 50)],
        }
        for why, lines in cases.items():
            with self.subTest(why):
                self.assertIsNone(_parse_round(lines, w, h))

    def test_loading_screen_needs_exact_map_name(self):
        db = self.db
        self.assertEqual(_exact_map_name(db, [ln("园林中庭", 1000, 500, 300, 80)], 2000, 1087), "园林中庭")
        self.assertEqual(_exact_map_name(db, [ln("星趴梦想号", 1000, 500, 300, 80)], 2000, 1087), "星趴·梦想号")
        refuse = {
            "名字只是一部分": [ln("园林中庭的秘密", 1000, 500, 300, 80)],
            "同时有两张地图（选图界面）": [ln("园林中庭", 600, 500, 300, 80), ln("魔法学院", 1400, 500, 300, 80)],
            "对局画面": [ln("园林中庭", 1000, 500, 300, 80), ln("地图说明", 364, 58, 146, 55)],
            "三选一": [ln("园林中庭", 1000, 500, 300, 80), ln("请选择一枚战斗筹码", 1000, 200, 500, 60)],
            "任务栏": [ln("魔法学院", 1000, 500, 300, 80), ln("击倒果冻战士", 700, 300, 300, 60)],
            "左上角有轮次": [ln("魔法学院", 1000, 500, 300, 80), ln("03", 114, 54, 72, 58)],
        }
        for why, lines in refuse.items():
            with self.subTest(why):
                self.assertIsNone(_exact_map_name(db, lines, 2000, 1087))


class LoadingCheckTest(unittest.TestCase):
    """离开对局后的整幅识别（read_pregame：加载画面的地图名、对局外的画面、畸变组件）的节流：不在对局里、画面有变化时
    才做；地图未知时最快 loading_ms 一次，地图已知、对局状态还在时最快 mutators_ms 一次（只为认对局外的画面和畸变组件）。
    每个时刻到期的读数都做完（settle：悬浮窗一帧只读一项，整幅识别排在读轮次那一轮后面）。"""

    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings())
        self.calls = []

        def fake_pregame(img, db, width, catalog):
            self.calls.append(width)
            return hud_module.Pregame(self.answer)

        self.answer = None
        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full, **kw: Hud(round=self.round)),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", return_value=None),
            mock.patch.object(pipeline, "read_pregame", side_effect=fake_pregame),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            # 只测地图名（畸变组件见 tests/test_pipeline.py FlowTest，对局外的画面见同一文件的 OutOfGameFlowTest）
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.round = None
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def test_throttled_and_only_when_needed(self):
        a = np.zeros((90, 160, 3), np.uint8)
        b = a + 200
        self.proc.settle(a, 0.0)
        self.assertEqual(len(self.calls), 1)
        self.proc.settle(a, 0.5)  # 间隔不够
        self.proc.settle(a, 5.0)  # 画面没变
        self.assertEqual(len(self.calls), 1)
        self.proc.settle(b, 6.0)
        self.assertEqual(len(self.calls), 2)
        # 进了对局（读到轮次）：不再找加载画面
        self.round = 1
        self.proc.settle(a, 9.0)
        self.proc.settle(b, 12.0)
        self.assertEqual(len(self.calls), 2)
        # 认出地图后不再按 loading_ms 找：画面变了也要隔 mutators_ms 才再看一次，读到的地图名也不再采用
        self.round = None
        self.answer = "园林中庭"
        self.proc.settle(a, 100.0)
        self.assertEqual(self.session.map_source, "loading")
        self.answer = "异变图书馆"
        self.proc.settle(b, 100.0 + self.proc.loading_s)
        self.assertEqual(len(self.calls), 3)
        self.proc.settle(b, 100.0 + self.proc.mutators_s)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual((self.session.settings.map, self.session.map_source), ("园林中庭", "loading"))


class SelectFrameTest(unittest.TestCase):
    """三选一界面上也读轮次、角色和自己的星数，读完再交给 Session 算推荐（不跑真的识别）。这几条三选一出现时手上没有够新的
    轮次（识别流程新建、或上次读到轮次已经过了 after_game_ms），照旧先读完再给推荐；先给推荐、下一帧核对的见 test_pipeline 的
    FastAdviceTest。"""

    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="远野汉娜"))
        self.seen = None  # 这一帧的三选一识别结果；None 表示不是三选一
        self.round, self.hero, self.stars = None, None, None
        self.star_calls = []  # 每次读星数时传进去的 hero_key
        self.hud_calls = []  # 每次读轮次时的 full 参数

        def fake_hud(img, db, full, **kw):
            self.hud_calls.append(full)
            return Hud(round=self.round)

        def fake_stars(img, hero, row=None, **kw):
            self.star_calls.append(hero)
            return self.stars

        self.hero_calls = 0

        def fake_hero(img, db):
            self.hero_calls += 1
            return SimpleNamespace(hero=self.hero)

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", side_effect=fake_hero),
            mock.patch.object(pipeline, "read_own_stars", side_effect=fake_stars),
            mock.patch.object(pipeline, "read_pregame", return_value=hud_module.Pregame()),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.img = np.zeros((90, 160, 3), np.uint8)

    def test_round_hero_and_stars_are_read_before_advice(self):
        """纯手动模式按热键时往往正停在三选一上：这一次的推荐就要用上画面上的轮次、角色和星数。"""
        self.seen = frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        self.round, self.hero, self.stars = 3, "凛", own(1)
        events = self.proc.process(self.img, 0.0)
        advice = [ev for ev in events if ev.kind == "advice"]
        self.assertEqual(len(advice), 1)
        st = advice[0].state
        self.assertEqual((st.hero, st.round, st.star), ("凛", 3, 1))
        self.assertEqual(self.star_calls, ["凛"])  # 先认出角色，再拿它的头像找自己那一行
        self.assertEqual(self.hud_calls, [False])  # 三选一时任务栏看不到，不读

    def test_round_jump_in_chip_select_is_rechecked_next_frame(self):
        """三选一上读到往前大幅跳变的轮次：下一帧马上再读一次（不等 hud_ms），两次一致就采用、推荐随即重算。
        三选一出现时上次读到轮次已经过了 after_game_ms（第 4 秒读到，第 40 秒出现），先读完再给推荐（先给推荐、下一帧核对时
        读到跳变的见 test_pipeline 的 FastAdviceTest）。对局画面的两个时刻到期的读数都做完（settle）。同一个三选一界面上
        之后只查绿框（快速复查换成假的、照旧对得上；对不上、每一帧都要完整识别时读左上角挪一帧，见 test_pipeline 的
        SelectTopTest）。"""
        self.round, self.hero = 3, "凛"
        self.proc.settle(self.img, 0.0)
        self.proc.settle(self.img, 4.0)  # 第 3 轮读到两次
        self.seen = frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        self.round = 6
        with mock.patch.object(pipeline, "name_strip", side_effect=lambda img, seen: tuple(seen.names)), \
                mock.patch.object(pipeline, "reread_selected", side_effect=lambda img, seen, strip: seen):
            events = self.proc.process(self.img, 40.0)
            self.assertEqual([ev.state.round for ev in events if ev.kind == "advice"], [3])  # 只读到一次：先按旧的算
            calls = len(self.hud_calls)
            events = self.proc.process(self.img, 40.2)
            self.assertEqual(len(self.hud_calls), calls + 1)  # 没等 hud_ms 就又读了一次
            self.assertEqual([ev.state.round for ev in events if ev.kind == "advice"], [6])
            self.proc.process(self.img, 40.4)
            self.assertEqual(len(self.hud_calls), calls + 1)  # 只补读一次，之后这一次三选一里不再读

    def test_stars_tried_a_few_times_per_chip_select(self):
        """自己那一行被标题带挡住时读不到，每次三选一最多试几帧；读到了就不再读。"""
        self.seen = frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        for t in (0.0, 0.5, 1.0, 1.5, 2.0):
            self.proc.process(self.img, t)
        self.assertEqual(len(self.star_calls), pipeline.SELECT_STAR_TRIES)
        self.seen = None
        self.proc.process(self.img, 2.5)  # 三选一关了（读轮次的间隔还没到，这一帧不读星数）
        self.seen = frame(["会员推荐信", "手电筒-强光", "优雅之羽"])
        self.stars = own(1)
        self.proc.process(self.img, 2.7)
        self.proc.process(self.img, 2.9)
        self.assertEqual(len(self.star_calls), pipeline.SELECT_STAR_TRIES + 1)

    def test_hero_read_at_once_when_pending(self):
        """三选一出现而角色待识别：立即认一次角色；认不出就发 hero_needed、不给推荐，之后按 hud_ms 再试。"""
        self.seen = frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        events = self.proc.process(self.img, 0.0)
        self.assertEqual(self.hero_calls, 1)
        self.assertIn("hero_needed", [ev.kind for ev in events])
        self.assertNotIn("advice", [ev.kind for ev in events])
        self.assertEqual(self.proc.process(self.img, 0.2), [])  # 同一个三选一：不重复认、不重复报
        self.assertEqual(self.hero_calls, 1)
        self.hero = "凛"
        events = self.proc.process(self.img, 3.5)
        self.assertEqual(self.hero_calls, 2)
        self.assertEqual([ev.state.hero for ev in events if ev.kind == "advice"], ["凛"])

    def test_events_recorded_for_diagnostics(self):
        self.round, self.hero = 3, "凛"
        self.proc.settle(self.img, 0.0)  # 读轮次那一轮做完（悬浮窗一帧一项，认角色在紧接着的下一帧）
        kinds_seen = [r["kind"] for r in self.session.recent_events(60)]
        self.assertIn("read:hud", kinds_seen)
        self.assertIn("read:hero", kinds_seen)
        self.assertIn("status", kinds_seen)

    def test_hud_frames_read_stars_and_detect_new_game(self):
        """对局画面每个时刻到期的读数都做完（settle：悬浮窗一帧一项，紧接着几帧做完）。"""
        clock = [0.0]
        self.session._clock = lambda: clock[0]
        self.round, self.hero = 6, "凛"
        self.stars = own(2)
        self.proc.settle(self.img, 0.0)
        self.proc.settle(self.img, 4.0)
        self.assertEqual((self.session.settings.star, self.session.settings.star_read), (2, True))
        self.session.set_owned(["书签"])
        # 轮次还读得到、还是第 6 轮时，星数变少按读错处理（真人对局里转账画面等会挡住、挪动玩家列表）
        self.stars = own(0)
        self.proc.settle(self.img, 8.0)
        events = self.proc.settle(self.img, 12.0)
        self.assertNotIn("newgame", [ev.kind for ev in events])
        self.assertEqual(self.session.settings.owned, ["书签"])
        # 轮次已经很久读不到：只有星数可依，星数变少判为新的一局
        self.round = None
        clock[0] = 60.0
        self.proc.settle(self.img, 60.0)
        events = self.proc.settle(self.img, 64.0)
        self.assertIn("newgame", [ev.kind for ev in events])
        self.assertEqual(self.session.settings.owned, [])


def chips(names, raw=None, rerolls=2, probabilities=(25, 50, 25), partial=(), problems=(), reread=False, selected=None,
          team=False):
    """一次三选一识别结果，可写识别原文、只读到共同部分的格子（partial）、是不是只重查绿框的快速复查（reread）。"""
    return SimpleNamespace(
        names=list(names), raw=list(raw if raw is not None else [n or "" for n in names]), rerolls=rerolls,
        probabilities=probabilities, selected=selected, problems=list(problems), team=team,
        partial=[i in partial for i in range(len(names))], reread=reread,
    )


# 10-04 实战局第 9 轮的三选一（芬妮，3 星）：刷新前、刷新动画的中间帧、刷新后
BEFORE = ["星币锤", "维生素药丸", "摩托头盔-中级"]
MIDDLE = dict(names=[None, None, "优雅之羽"], raw=["高效", "2", "优雅"],
              problems=["第 1 个候选筹码无法识别（识别文字：「高效」）", "第 2 个候选筹码无法识别（识别文字：「2」）"])
AFTER = ["肾上腺素-高效", "摩托头盔-一般", "优雅之羽"]


class RefreshHoldTest(unittest.TestCase):
    """点刷新后新的候选筹码还没读全时，保留刷新前的推荐、标「正在刷新」（refreshing），
    约半秒后再读；隔半秒以上再读一次仍读不全、或超过约 1 秒还读不全，才提示无法识别。"""

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.clock = [100.0]
        self.s._clock = lambda: self.clock[0]
        self.s.on_stars(own(3))

    def at(self, t, seen):
        self.clock[0] = 100.0 + t
        return self.s.on_frame(seen)

    def advise_before(self):
        ev = self.at(0.0, chips(BEFORE, rerolls=5))
        self.assertEqual(ev.kind, "advice")

    def test_middle_frame_keeps_advice(self):
        """13:53:51.139「高效」「2」「优雅」：发 refreshing，不发 unreadable；0.65 秒后读全照常给推荐。"""
        self.advise_before()
        ev = self.at(2.9, chips(rerolls=4, **MIDDLE))
        self.assertEqual(ev.kind, "refreshing")
        self.assertEqual(ev.raw, ["高效", "2", "优雅"])
        self.assertTrue(self.s.holding)
        self.assertIsNone(self.at(3.0, chips(rerolls=4, reread=True, **MIDDLE)))  # 只查绿框的复查：不重复发
        ev = self.at(3.55, chips(AFTER, rerolls=4))
        self.assertEqual((ev.kind, [e.chip.name for e in ev.advice.evals]), ("advice", AFTER))
        self.assertFalse(self.s.holding)
        self.assertNotIn("unreadable", [e.kind for e in self.s.take_events()])

    def test_still_unreadable_after_retry(self):
        """隔半秒以上的第二次完整识别仍读不全：提示无法识别；隔不到半秒的那次不算。"""
        self.advise_before()
        self.assertEqual(self.at(1.0, chips(rerolls=4, **MIDDLE)).kind, "refreshing")
        self.assertIsNone(self.at(1.3, chips(rerolls=4, **MIDDLE)))
        ev = self.at(1.55, chips(rerolls=4, **MIDDLE))
        self.assertEqual(ev.kind, "unreadable")
        self.assertEqual(ev.problems, MIDDLE["problems"])
        self.assertFalse(self.s.holding)
        self.assertIsNone(self.at(1.9, chips(rerolls=4, **MIDDLE)))  # 同一组读数不重复提示

    def test_still_unreadable_after_one_second(self):
        """只有快速复查（没有第二次完整识别）：等满约 1 秒也照常提示。"""
        self.advise_before()
        self.assertEqual(self.at(1.0, chips(rerolls=4, **MIDDLE)).kind, "refreshing")
        self.assertIsNone(self.at(1.6, chips(rerolls=4, reread=True, **MIDDLE)))
        self.assertEqual(self.at(2.05, chips(rerolls=4, reread=True, **MIDDLE)).kind, "unreadable")

    def test_refresh_told_by_names_when_count_unreadable(self):
        """刷新次数读不到时，读得出的候选筹码全都换了也算刚点了刷新（14:00:15「」「」「医疗箱」那种只读出一格的）。"""
        self.advise_before()
        ev = self.at(1.0, chips([None, None, "医疗箱-紧急治疗"], raw=["", "", "医疗箱"], rerolls=None))
        self.assertEqual(ev.kind, "refreshing")

    def test_unreadable_after_advice_is_immediate(self):
        """给过推荐以后同一组候选里一格被挡住（不是刷新）：照常当场提示。三选一刚出现时就有一格读不出的先等一等，见
        FirstUnreadTest。"""
        self.advise_before()
        ev = self.at(1.0, chips(["星币锤", None, "摩托头盔-中级"], raw=["星币锤", "", "摩托头盔-中级"], rerolls=5))
        self.assertEqual(ev.kind, "unreadable")
        self.assertFalse(self.s.holding)

    def test_close_while_refreshing(self):
        self.advise_before()
        self.assertEqual(self.at(1.0, chips(rerolls=4, **MIDDLE)).kind, "refreshing")
        self.assertIsNone(self.at(1.2, None))
        self.assertEqual(self.at(1.4, None).kind, "missed_pick")
        self.assertFalse(self.s.holding)
        # 下一次三选一照常给推荐
        self.assertEqual(self.at(20.0, chips(AFTER, rerolls=4)).kind, "advice")

    def test_every_refresh_is_held(self):
        """连刷几次，每次的中间帧都先标「正在刷新」。"""
        self.advise_before()
        self.assertEqual(self.at(1.0, chips(rerolls=4, **MIDDLE)).kind, "refreshing")
        self.assertEqual(self.at(1.6, chips(AFTER, rerolls=4)).kind, "advice")
        self.assertEqual(self.at(3.0, chips([None, "美工刀-锋利", None], raw=["", "美工刀-锋利", "放"], rerolls=3)).kind,
                         "refreshing")
        self.assertEqual(self.at(3.6, chips(["星币锤", "美工刀-锋利", "放大镜"], rerolls=3)).kind, "advice")


class FirstUnreadTest(unittest.TestCase):
    """三选一刚出现（这一次还没发过推荐、无法识别、请选择角色）时有筹码没读出来（入场动画、升星特效挡着）：先不提示无法识别，
    也不发别的事件（悬浮窗什么都不变），等再读一次；隔半秒以上的第二次完整识别仍读不全、或等满约 1 秒，才提示无法识别。
    读全了照常给推荐。只管刚出现的那一下：给过推荐以后读不全的照旧当场提示
    （RefreshHoldTest.test_unreadable_after_advice_is_immediate）。"""

    HIDDEN = dict(names=[None, None, "缓冲盾牌"], raw=["", "", "缓冲盾牌"],
                  problems=["第 1 个候选筹码无法识别", "第 2 个候选筹码无法识别"])
    SHOWN = ["标靶", "大背包", "缓冲盾牌"]

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.clock = [100.0]
        self.s._clock = lambda: self.clock[0]
        self.s.on_stars(own(3))
        self.s.take_events()

    def at(self, t, seen):
        self.clock[0] = 100.0 + t
        return self.s.on_frame(seen)

    def hidden(self, reread=False, rerolls=4):
        return chips(rerolls=rerolls, reread=reread, **self.HIDDEN)

    def test_waits_then_advice(self):
        """刚出现那一帧前两张没读出来：不发事件（holding：悬浮窗接着截图），只查绿框的快速复查也不发，切换方案时的重算也
        先不算；读全了照常给推荐（这一组没给过推荐，照第一次读全处理），整个过程没有提示过无法识别。"""
        got = [self.at(0.0, self.hidden())]
        self.assertTrue(self.s.holding)
        self.assertTrue(self.s.select.fresh)
        got.append(self.at(0.15, self.hidden(reread=True)))
        self.assertIsNone(self.s.recompute())
        self.assertEqual(got, [None, None])
        ev = self.at(0.6, chips(self.SHOWN, rerolls=4))
        self.assertEqual((ev.kind, [e.chip.name for e in ev.advice.evals], ev.problems), ("advice", self.SHOWN, []))
        self.assertFalse(self.s.holding)
        self.assertFalse(self.s.select.fresh)
        self.assertNotIn("unreadable", [e.kind for e in self.s.take_events()])

    def test_still_unreadable_after_retry(self):
        """真读不出来：离刚出现不到半秒的几次完整识别都还在等，隔半秒以上的那一次仍读不全才提示无法识别（不早于它）；
        之后同一组读数不重复提示。"""
        self.assertEqual([self.at(t, self.hidden()) for t in (0.0, 0.3, 0.49)], [None, None, None])
        self.assertTrue(self.s.holding)
        ev = self.at(0.5, self.hidden())
        self.assertEqual((ev.kind, ev.problems), ("unreadable", self.HIDDEN["problems"]))
        self.assertFalse(self.s.holding)
        self.assertIsNone(self.at(1.0, self.hidden()))

    def test_still_unreadable_after_one_second(self):
        """只有快速复查（没有第二次完整识别）：等满约 1 秒也照常提示，不一直不提示。"""
        self.assertIsNone(self.at(0.0, self.hidden()))
        self.assertIsNone(self.at(0.6, self.hidden(reread=True)))
        self.assertIsNone(self.at(0.99, self.hidden(reread=True)))
        self.assertEqual(self.at(1.0, self.hidden(reread=True)).kind, "unreadable")

    def test_told_then_immediate(self):
        """提示过无法识别以后就不算刚出现：同一次三选一里换了一种读不全的读数，当场提示（和给过推荐以后一样）。"""
        self.at(0.0, self.hidden())
        self.assertEqual(self.at(0.6, self.hidden()).kind, "unreadable")
        self.assertFalse(self.s.select.fresh)
        ev = self.at(1.2, chips(["标靶", None, "缓冲盾牌"], raw=["标靶", "", "缓冲盾牌"], rerolls=4))
        self.assertEqual(ev.kind, "unreadable")
        self.assertFalse(self.s.holding)

    def test_hero_needed_counts_as_told(self):
        """角色待识别、读全了发「请选择角色」：也算发过了，之后同一次三选一里读不全的当场提示。"""
        s = Session(self.db, Settings(hero="凛"))  # 角色还没认出来
        s._clock = lambda: self.clock[0]
        self.assertEqual(s.on_frame(chips(self.SHOWN, rerolls=4)).kind, "hero_needed")
        self.assertFalse(s.select.fresh)
        self.clock[0] += 1.0
        self.assertEqual(s.on_frame(self.hidden()).kind, "unreadable")

    def test_another_select_waits_again(self):
        """两次三选一紧接着出现（概率行换成另一行，例如两个任务接连完成）：换上来的这一次也算刚出现，前两张没读出来时同样
        先等，上一次照常先结算（没看到选中：请补录）；读全以后照常给推荐。"""
        self.assertEqual(self.at(0.0, chips(["星币锤", "维生素药丸", "摩托头盔-中级"], rerolls=5)).kind, "advice")
        self.s.take_events()
        self.assertIsNone(self.at(2.0, chips(rerolls=5, probabilities=(40, 45, 15), **self.HIDDEN)))
        kinds_now = [e.kind for e in self.s.take_events()]
        self.assertIn("missed_pick", kinds_now)
        self.assertNotIn("unreadable", kinds_now)
        self.assertTrue(self.s.holding)
        ev = self.at(2.6, chips(self.SHOWN, rerolls=5, probabilities=(40, 45, 15)))
        self.assertEqual(ev.kind, "advice")

    def test_close_while_waiting(self):
        """等着的时候界面关了：不提示无法识别，照常结算（没看到选中：请补录）；下一次三选一照样算刚出现、照样先等。"""
        self.assertIsNone(self.at(0.0, self.hidden()))
        self.assertIsNone(self.at(0.15, None))
        self.assertEqual(self.at(0.3, None).kind, "missed_pick")
        self.assertFalse(self.s.holding)
        self.assertIsNone(self.at(20.0, self.hidden()))
        self.assertEqual((self.s.select.fresh, self.s.holding), (True, True))

    def test_partial_after_hidden_same_wait(self):
        """刚出现时没读出来的那一格接着只读到几个筹码名的共同部分（「飞星」）：还是同一回等待，不另起；等满以后照常按挑出的
        那一个给推荐并提示匹配度低，不提示无法识别。"""
        low = "第 3 个候选筹码匹配度低：识别文字「飞星」，按「金色飞星」计算"
        offer = ["汉娜人偶", "拳击手套-高级", "金色飞星"]
        self.assertIsNone(self.at(0.0, chips([None, "拳击手套-高级", None], raw=["", "拳击手套-高级", ""], rerolls=4)))
        partial = chips(offer, raw=["汉娜人偶", "拳击手套-高级", "飞星"], partial=(2,), problems=[low], rerolls=4)
        self.assertIsNone(self.at(0.3, partial))
        ev = self.at(0.6, partial)
        self.assertEqual((ev.kind, ev.problems, ev.advice.best in offer), ("advice", [low], True))

    def test_partial_then_complete_drops_low_match(self):
        """接着上一条：等满以后按挑出的那一个给了推荐、提示匹配度低；之后读全了，推荐换成读全的、过时的「匹配度低」去掉
        （同一组候选后来读全了，过时的提示自动去掉）：读全是挑出的那一个时照样重发一次、只去掉提示，是另一个时按它给推荐。
        之后同样的读数不重复给。"""
        low = "第 3 个候选筹码匹配度低：识别文字「飞星」，按「金色飞星」计算"
        offer = ["汉娜人偶", "拳击手套-高级", "金色飞星"]
        for full in (offer, ["汉娜人偶", "拳击手套-高级", "紫色飞星"]):
            with self.subTest(full=full[2]):
                self.setUp()
                self.assertIsNone(self.at(0.0, chips([None, "拳击手套-高级", None], raw=["", "拳击手套-高级", ""],
                                                     rerolls=4)))
                partial = chips(offer, raw=["汉娜人偶", "拳击手套-高级", "飞星"], partial=(2,), problems=[low], rerolls=4)
                self.assertIsNone(self.at(0.3, partial))
                self.assertEqual(self.at(0.6, partial).problems, [low])
                ev = self.at(1.0, chips(full, rerolls=4))
                self.assertEqual((ev.kind, [e.chip.name for e in ev.advice.evals], ev.problems), ("advice", full, []))
                self.assertIsNone(self.at(1.2, chips(full, rerolls=4)))

    def test_hidden_while_waiting(self):
        """等着的时候玩家点了「隐藏」（三选一当还开着，见 SelectTracker.hidden）：不提示、不结算。点「显示」回来时读全了照常给
        推荐，整个过程没有提示过无法识别；回来时仍读不全的，已经等满了，当场提示（不再另等一回），也不会一直不提示。"""
        for kind in ("advice", "unreadable"):
            with self.subTest(kind=kind):
                self.setUp()
                self.assertIsNone(self.at(0.0, self.hidden()))
                self.assertIsNone(self.at(0.15, None))  # 消失后第 1 帧：照常算看不到
                for t in (0.3, 0.6, 1.5):  # 之后认出「您当前正处于隐藏UI状态」
                    self.clock[0] = 100.0 + t
                    self.assertIsNone(self.s.on_select_hidden())
                self.assertEqual([e.kind for e in self.s.take_events()
                                  if e.kind in ("unreadable", "missed_pick", "picked", "idle")], [])
                self.assertTrue(self.s.select.fresh)
                back = chips(self.SHOWN, rerolls=4) if kind == "advice" else self.hidden()
                self.assertEqual(self.at(2.0, back).kind, kind)
                self.assertFalse(self.s.holding)

    def test_unread_flag(self):
        """诊断包另带的那一帧看 SelectTracker.unread：新读的一次有筹码没读出来时为真（刚出现时等着的、等满提示无法识别的、
        刷新后等满了还读不全的都算）；只查绿框的快速复查、读全的、刷新动画的中间帧（意料之中）、界面关掉为假。"""
        self.at(0.0, self.hidden())
        self.assertTrue(self.s.select.unread)
        self.at(0.15, self.hidden(reread=True))
        self.assertFalse(self.s.select.unread)
        self.at(0.6, self.hidden())
        self.assertTrue(self.s.select.unread)
        self.at(1.2, chips(self.SHOWN, rerolls=4))
        self.assertFalse(self.s.select.unread)
        self.assertEqual(self.at(3.0, chips(rerolls=3, **MIDDLE)).kind, "refreshing")
        self.assertFalse(self.s.select.unread)
        self.assertEqual(self.at(3.6, chips(rerolls=3, **MIDDLE)).kind, "unreadable")
        self.assertTrue(self.s.select.unread)
        self.at(3.8, None)
        self.assertFalse(self.s.select.unread)


class PartialNameTest(unittest.TestCase):
    """只读到几个筹码名的共同部分（「飞星」对紫色飞星、金色飞星）时当作还没读完，
    约半秒后再读，读全了再给推荐；一直读不全时照旧按图标边框颜色挑的那一个计算并提示匹配度低。同一组候选后来读全了，
    去掉过时的提示。"""

    LOW = "第 3 个候选筹码匹配度低：识别文字「飞星」，按「金色飞星」计算"
    OFFER = ["汉娜人偶", "拳击手套-高级", "金色飞星"]

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.clock = [0.0]
        self.s._clock = lambda: self.clock[0]
        self.s.on_stars(own(3))

    def at(self, t, seen):
        self.clock[0] = t
        return self.s.on_frame(seen)

    def partial(self, **kw):
        return chips(self.OFFER, raw=["汉娜人偶", "拳击手套-高级", "飞星"], partial=(2,), problems=[self.LOW], **kw)

    def test_waits_for_full_name(self):
        self.assertIsNone(self.at(0.0, self.partial()))  # 三选一刚出现：不按挑出的那一个给推荐，也不发事件
        self.assertTrue(self.s.holding)
        ev = self.at(0.65, chips(self.OFFER))
        self.assertEqual((ev.kind, ev.problems), ("advice", []))

    def test_fallback_after_retry_then_stale_hint_removed(self):
        self.assertIsNone(self.at(0.0, self.partial()))
        ev = self.at(0.6, self.partial())  # 再读一次仍是共同部分：照旧按挑出的金色飞星计算，并提示
        self.assertEqual((ev.kind, ev.problems), ("advice", [self.LOW]))
        self.assertEqual(ev.advice.best, "金色飞星")
        ev = self.at(1.2, chips(self.OFFER))  # 后来读全了：重发一次，去掉过时的提示
        self.assertEqual((ev.kind, ev.problems), ("advice", []))
        self.assertIsNone(self.at(1.8, chips(self.OFFER)))
        self.assertIsNone(self.at(2.4, self.partial()))  # 读全以后又读漏一次：不再添上提示

    def test_partial_after_refresh_is_refreshing(self):
        """13:53:55.569：刷新后第一帧只读到「飞星」，和刷新动画的中间帧一样标「正在刷新」。"""
        self.assertEqual(self.at(0.0, chips(["星币锤", "美工刀-锋利", "放大镜"], rerolls=3)).kind, "advice")
        self.assertEqual(self.at(2.5, self.partial(rerolls=2)).kind, "refreshing")
        ev = self.at(3.1, chips(self.OFFER, rerolls=2))
        self.assertEqual((ev.kind, ev.problems), ("advice", []))

    def test_refresh_after_unresolved_partial(self):
        """一直读不全、已按挑出的那一个给了推荐，之后又点了刷新：刷新的中间帧照样标「正在刷新」。"""
        self.assertIsNone(self.at(0.0, self.partial()))
        self.assertEqual(self.at(0.6, self.partial()).problems, [self.LOW])
        self.assertEqual(self.at(2.0, chips(rerolls=1, **MIDDLE)).kind, "refreshing")
        self.assertEqual(self.at(2.6, chips(rerolls=1, **MIDDLE)).kind, "unreadable")
        self.assertIsNone(self.at(3.2, chips(rerolls=1, **MIDDLE)))  # 不再等以后不会又重新开始等

    def test_wrong_pick_corrected(self):
        """边框颜色读不到时挑的是紫色飞星（筹码表的顺序）；读全以后换成金色飞星的推荐。"""
        low = "第 3 个候选筹码匹配度低：识别文字「飞星」，按「紫色飞星」计算"
        wrong = chips(["汉娜人偶", "拳击手套-高级", "紫色飞星"], raw=["汉娜人偶", "拳击手套-高级", "飞星"], partial=(2,),
                      problems=[low])
        self.assertIsNone(self.at(0.0, wrong))
        self.assertEqual(self.at(0.6, wrong).problems, [low])
        ev = self.at(1.2, chips(self.OFFER))
        self.assertEqual(([e.chip.name for e in ev.advice.evals], ev.problems), (self.OFFER, []))

    def test_problems_only_shrink(self):
        """同一组候选：识别提示变少才重发（去掉过时的）；读全以后又多出提示（偶尔读错）不重发。"""
        unsure = ["第 1 个候选筹码匹配度低：识别文字「汉娜人禺」，按「汉娜人偶」计算"]
        self.assertEqual(self.at(0.0, chips(self.OFFER, problems=unsure)).problems, unsure)
        self.assertEqual(self.at(3.0, chips(self.OFFER)).problems, [])
        self.assertIsNone(self.at(6.0, chips(self.OFFER, problems=unsure)))

    def test_stale_hint_removed_when_another_appears(self):
        """上一次提示第 1 个匹配度低，这一帧第 1 个读对了、第 2 个却读错了（提示换成另一条，不是变少）：
        仍然重发，去掉第 1 个的过时提示，第 2 个新冒出的提示不添上。"""
        first = ["第 1 个候选筹码匹配度低：识别文字「汉娜人禺」，按「汉娜人偶」计算"]
        second = ["第 2 个候选筹码匹配度低：识别文字「拳击手套-高圾」，按「拳击手套-高级」计算"]
        self.assertEqual(self.at(0.0, chips(self.OFFER, problems=first)).problems, first)
        ev = self.at(3.0, chips(self.OFFER, problems=second))
        self.assertEqual((ev.kind, ev.problems), ("advice", []))
        self.assertIsNone(self.at(6.0, chips(self.OFFER)))
        self.assertIsNone(self.at(9.0, chips(self.OFFER, problems=second)))


class FirstStarTest(unittest.TestCase):
    """0 星时出现的升星类三选一，三选一界面上读不到新的星数（自己那一行被标题带挡住）。
    0、1 星的概率行和最左格都相同，按刷新次数判断是不是升到 1 星的那一次（每升一星刷新 +1）。"""

    OFFER = ["小猪存钱罐", "银行卡-余额少", "可口糖果"]  # 10-04 实战局，芬妮升到 1 星时的三选一

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.s.on_stars(own(0))

    def frame(self, rerolls, **kw):
        return chips(self.OFFER, rerolls=rerolls, probabilities=(60, 37, 3), **kw)

    def test_one_more_reroll_means_levelup(self):
        ev = self.s.on_frame(self.frame(3))  # 开局 2 次，升到 1 星 +1
        self.assertEqual((ev.state.star, ev.state.source), (1, "levelup"))
        self.assertEqual((self.s.settings.star, self.s.settings.star_read), (0, True))  # 记下的星数不改
        ev = self.s.on_frame(chips(OFFER_B, rerolls=2, probabilities=(60, 37, 3)))  # 刷新后次数少了：仍按 1 星
        self.assertEqual(ev.state.star, 1)
        close(self.s)
        self.assertIsNone(self.s.select._select_up)  # 下一次三选一重新判断

    def test_same_count_keeps_star(self):
        """次数和 0 星时一样（筹码商店、任务，或没有这项加成的账号）：照常按 0 星。"""
        ev = self.s.on_frame(self.frame(2))
        self.assertEqual(ev.state.star, 0)

    def test_uses_last_reading_at_this_star(self):
        """上一次三选一（0 星）用了 1 次刷新：升星前应有的是那时剩下的 1 次。"""
        self.s.on_frame(chips(OFFER_A, rerolls=2, probabilities=(60, 37, 3)))
        self.s.on_frame(chips(OFFER_B, rerolls=1, probabilities=(60, 37, 3)))
        close(self.s)
        self.assertEqual(self.s.on_frame(self.frame(2)).state.star, 1)

    def test_not_for_tasks_or_star_seen_in_select(self):
        ev = self.s.on_frame(self.frame(3, team=True))  # 全队任务三选一
        self.assertEqual((ev.state.star, ev.state.source), (0, "task"))
        close(self.s)
        self.s.on_stars(own(0), in_select=True)  # 三选一界面上读到了 0 星：不是刚升星
        self.assertEqual(self.s.on_frame(self.frame(3)).state.star, 0)

    def test_unsure_computes_both(self):
        """刷新次数读不到、或已推断出没有这项加成：0 星、1 星都算，首推相同按 0 星显示，不同按 1 星。"""
        calls = []

        def fake_advise(db, state, names, rarity=None, **kw):
            calls.append(state.star)
            return SimpleNamespace(best=best[state.star], evals=[], reroll=None, notes=[])

        with mock.patch("advisor.session.selection.advise", fake_advise):  # 记账里调用推荐引擎的地方
            best = {0: "银行卡-余额少", 1: "银行卡-余额少"}
            ev = self.s.on_frame(self.frame(None))
            self.assertEqual((ev.state.star, calls), (0, [0, 1]))
            close(self.s)
            calls.clear()
            best = {0: "小猪存钱罐", 1: "银行卡-余额少"}
            ev = self.s.on_frame(self.frame(None))
            self.assertEqual((ev.state.star, ev.advice.best, calls), (1, "银行卡-余额少", [0, 1]))
            close(self.s)
            calls.clear()
            self.s.game.per_star_rerolls = False  # 没有这项加成：次数看不出升没升星
            # 换一组候选（另一次三选一）：同一组候选关了又出现时按这次三选一之前的记录重新判（见
            # RereadRejudgeTest.test_reopened_keeps_level_up），在这中间改的推断不算
            ev = self.s.on_frame(chips(OFFER_A, rerolls=2, probabilities=(60, 37, 3)))
            self.assertEqual((ev.state.star, calls), (1, [0, 1]))

    def test_star_cap(self):
        """3 星（最高）时不再加。"""
        self.s.on_stars(own(1))
        self.s.on_stars(own(2))
        self.s.on_stars(own(3))
        ev = self.s.on_frame(chips(self.OFFER, rerolls=6, probabilities=(25, 50, 25)))
        self.assertEqual(ev.state.star, 3)


class RereadRejudgeTest(unittest.TestCase):
    """同一组候选第一次读错、后来读对了（识别流程半秒后补认一次）：记账重新判一次「是不是刚升到 1 星」和这次
    三选一的来源，撤回第一次按读错的数留下的记录——最近读到的（星数, 刷新次数）、「每升一星刷新 +1」的推断、这一局非任务
    三选一的次数和星币拆账这一段的次数——推荐按读对的算，和第一次就读对时一样。第一次就读对时不受影响。"""

    OFFER = FirstStarTest.OFFER  # 0 星升 1 星的那一次三选一（0、1 星的概率行和最左格的固定稀有度都相同）
    ROW2 = (40, 45, 15)  # 概率表第 2 行：2 星的升星和商店、2 级任务（0 星时读到它就是任务）
    WRONG_ROW = (60, 31, 9)  # 「60%/37%/3%」读错：三项加起来是 100，却不是概率表里的行
    BLUE_FIRST = ["标靶", "大背包", "缓冲盾牌"]  # 1 星升星的三选一：最左格是蓝色
    PURPLE_FIRST = ["大背包", "标靶", "缓冲盾牌"]  # 2 星升星的三选一：最左格是紫色

    def setUp(self):
        self.db = load_db()
        self.s = self.zero_star()

    def zero_star(self) -> Session:
        s = ready(self.db, "芬妮")
        s.on_stars(own(0))
        return s

    def frame(self, rerolls=2, probabilities=(60, 37, 3), names=None):
        return chips(names or self.OFFER, rerolls=rerolls, probabilities=probabilities)

    @staticmethod
    def books(s) -> tuple:
        """记账里跟这次三选一有关的几项：（「是不是刚升星」的结论, 最近读到的（星数, 刷新次数）, 每升一星刷新 +1,
        这次三选一的来源, 这一局非任务三选一的次数, 星币拆账这一段的非任务三选一次数）。"""
        g = s.game
        return s.select._select_up, g._rerolls_seen, g.per_star_rerolls, g._open_source, g._game_opens, g._ledger_opens

    @staticmethod
    def rejudged(s) -> list[dict]:
        """识别记录里的 read:select_rejudged（去掉时刻和种类）。"""
        return [{k: v for k, v in r.items() if k not in ("time", "kind")} for r in s.recent_events(10 ** 6)
                if r["kind"] == "read:select_rejudged"]

    def test_zero_rerolls_fixed_means_level_up(self):
        """0 星时刚出现那一帧刷新次数读成 0（刷新按钮被表情气泡挡住）：拿不准是不是刚升星（两种都算），记下 (0 星, 0 次)；
        补认读到 3 次（开局 2 次 + 升星 1 次）：改判为刚升到 1 星，推荐按 1 星算，记下 (1 星, 3 次)，局面和记账都和第一次就
        读到 3 次时一样；仍是同一次三选一（不补发上一次的结算）。"""
        s = self.s
        self.assertEqual(s.on_frame(self.frame(rerolls=0)).kind, "advice")
        self.assertEqual(self.books(s)[:2], ("unsure", (0, 0)))
        ev = s.on_frame(self.frame(rerolls=3))
        self.assertEqual((ev.kind, ev.state.star, ev.state.rerolls, ev.state.source), ("advice", 1, 3, "levelup"))
        self.assertEqual(self.books(s), ("up", (1, 3), None, "levelup", 1, 1))
        right = self.zero_star()
        first = right.on_frame(self.frame(rerolls=3))
        self.assertEqual(ev.state, first.state)
        self.assertEqual(self.books(s), self.books(right))
        self.assertEqual(self.rejudged(s), [{"rerolls": [0, 3], "in_table": [True, True],
                                             "source": ["levelup", "levelup"]}])
        self.assertEqual(self.rejudged(right), [])
        self.assertEqual([e.kind for e in kinds(s) if e.kind in ("picked", "missed_pick", "idle")], [])

    def test_fixed_reading_used_at_next_star(self):
        """读对以后记下的是 (1 星, 3 次)：之后 1 星时的三选一读到同样的 3 次（没刷新过），不会拿它当成「升星前最后读到的」、
        判成「每升一星刷新 +1」没有这项加成（读错时记成 (0 星, 3 次)，会判成没有）。"""
        s = self.s
        s.on_frame(self.frame(rerolls=0))
        s.on_frame(self.frame(rerolls=3))
        close(s)
        s.settings.star = 1  # 玩家列表上读到 1 星
        ev = s.on_frame(chips(OFFER_A, rerolls=3, probabilities=(60, 37, 3)))
        self.assertEqual((ev.state.star, ev.state.source), (1, "levelup"))
        self.assertIsNone(s.game.per_star_rerolls)
        self.assertEqual(s.game._rerolls_seen, (1, 3))

    def test_zero_misread_after_level_up(self):
        """1 星时最后读到 0 次（刷新用完了），升到 2 星后第一次三选一刚出现那一帧又读成 0（被挡住）：拿这个 0 会判成「每升一星
        刷新 +1」没有这项加成；补认读到 1 次以后撤回，改判为有，和第一次就读到 1 次时一样。"""
        s = self.s
        s.settings.star = 1
        s.on_frame(chips(self.BLUE_FIRST, rerolls=0, probabilities=(60, 37, 3)))
        close(s)
        s.settings.star = 2  # 玩家列表上读到 2 星
        s.on_frame(chips(self.PURPLE_FIRST, rerolls=0, probabilities=self.ROW2))
        self.assertIs(s.game.per_star_rerolls, False)  # 按读错的 0 判的
        ev = s.on_frame(chips(self.PURPLE_FIRST, rerolls=1, probabilities=self.ROW2))
        self.assertEqual((ev.state.star, ev.state.rerolls, ev.state.source), (2, 1, "levelup"))
        self.assertEqual((s.game.per_star_rerolls, s.game._rerolls_seen), (True, (2, 1)))

    def test_wrong_row_then_task_row(self):
        """刚出现那一帧概率行读成表外的行：来源按记下的（升星）算，这一局非任务三选一记一次、星币拆账这一段记一次；补认读到
        2 级任务的行：来源改成任务，两处各撤回那一次；之后确认星币数时记下的这一段不扣这一次的钱。"""
        s = self.s
        s.on_coins(40)
        s.on_coins(40)  # 确认星币数
        s.on_list(False)  # 之后认不出自己那一行（商店挡住了玩家列表等）：下一次确认时记一段
        ev = s.on_frame(self.frame(probabilities=self.WRONG_ROW))
        self.assertEqual(ev.state.source, "levelup")
        self.assertEqual(self.books(s)[3:], ("levelup", 1, 1))
        ev = s.on_frame(self.frame(probabilities=self.ROW2))
        self.assertEqual((ev.kind, ev.state.source, ev.state.tier), ("advice", "task", 2))
        self.assertEqual(self.books(s)[3:], ("task", 0, 0))
        self.assertEqual(self.rejudged(s), [{"rerolls": [2, 2], "in_table": [False, True], "source": ["levelup", "task"]}])
        close(s)
        s.on_coins(30)
        s.on_coins(30)
        self.assertEqual([(seg["opens"], seg["opens_before"], seg["drop"]) for seg in s.take_ledger()], [(0, 0, 10)])

    def test_reopened_after_coins_confirmed(self):
        """同一组候选关了又出现（仍是同一次三选一），中间在对局画面上确认了星币数（那一段已经记下，算了按升星记的这一次）：
        又出现时读对了任务的行，这一局的非任务次数撤回，新的这一段不再扣（不会扣成 -1）。"""
        s = self.s
        s.on_coins(40)
        s.on_coins(40)
        s.on_list(False)
        s.on_frame(self.frame(probabilities=self.WRONG_ROW))
        close(s)
        s.on_coins(25)
        s.on_coins(25)
        self.assertEqual([(seg["opens"], seg["opens_before"]) for seg in s.take_ledger()], [(1, 0)])
        ev = s.on_frame(self.frame(probabilities=self.ROW2))  # 同一组候选又出现
        self.assertEqual(ev.state.source, "task")
        self.assertEqual(self.books(s)[3:], ("task", 0, 0))

    def test_reopened_keeps_level_up(self):
        """同一组候选关掉以后又出现（切出游戏又切回等）：「是不是刚升到 1 星」按这次三选一之前的刷新记录重新判，和第一次一样。
        0 星时刷新过一次（记下 0 星 1 次），升星的那一次读到 2 次、判成升到 1 星；又出现时推荐仍按 1 星，
        不拿这一次自己刚记下的（1 星, 2 次）来比、判成没升星。"""
        s = self.s
        s.on_frame(chips(OFFER_A, rerolls=2, probabilities=(60, 37, 3)))  # 0 星时的一次三选一
        s.on_frame(chips(OFFER_B, rerolls=1, probabilities=(60, 37, 3)))  # 刷新了一次
        close(s)
        self.assertEqual(s.game._rerolls_seen, (0, 1))
        ev = s.on_frame(self.frame(rerolls=2))
        self.assertEqual((ev.state.star, ev.state.rerolls), (1, 2))  # 升到 1 星的那一次
        close(s)
        kinds(s)
        ev = s.on_frame(self.frame(rerolls=2))  # 同一组候选又出现
        self.assertEqual((ev.kind, ev.state.star, ev.state.rerolls), ("advice", 1, 2))
        self.assertEqual(self.books(s)[:3], ("up", (1, 2), True))

    def test_right_first_time_unchanged(self):
        """第一次就读对（刷新 3 次、概率行在表里）：同一组候选之后读成 0（沿用 3 次）、读不出，都不重新判；刷新以后（候选换了）
        读成 0、再读到 1 次也不再改第一次的判断（刷新后的读数照常按最近一次记），记账和原来一样。"""
        s = self.s
        s.on_frame(self.frame(rerolls=3))
        self.assertEqual(self.books(s), ("up", (1, 3), None, "levelup", 1, 1))
        s.on_frame(self.frame(rerolls=0))
        s.on_frame(self.frame(rerolls=None))
        s.on_frame(self.frame(rerolls=3))
        self.assertEqual(self.books(s), ("up", (1, 3), None, "levelup", 1, 1))
        s.on_frame(self.frame(rerolls=0, names=OFFER_B))  # 刷新
        s.on_frame(self.frame(rerolls=2, names=OFFER_B))
        self.assertEqual(self.books(s), ("up", (1, 2), None, "levelup", 1, 1))
        self.assertIsNone(s.game._open_read)
        self.assertEqual(self.rejudged(s), [])


class ReviewRecordTest(unittest.TestCase):
    """识别记录补记 status 变了哪一项、三选一里选中的变化、认出选角界面、识别暂停和恢复。"""

    def setUp(self):
        self.db = load_db()
        self.s = ready(self.db, "芬妮")
        self.rec = self.s.eventlog.recorder = FakeRecorder()

    def records(self, kind):
        return [r for r in self.rec.added if r["kind"] == kind]

    def test_status_names_what_changed(self):
        s = self.s
        s.note([s.on_hud(hud(round=3))])
        first = self.records("status")[-1]["changed"]
        self.assertEqual(first["round"], [None, 3])
        self.assertEqual(first["hero"], [None, "芬妮"])  # 第一条记下当时已有的各项
        s.note([s.on_hud(hud(round=4))])
        self.assertEqual(self.records("status")[-1]["changed"], {"round": [3, 4]})
        s.note([s.on_stars(own(1))])
        self.assertEqual(self.records("status")[-1]["changed"], {"star": [None, 1]})

    def test_first_status_keeps_zero_values(self):
        """第一条 status 记录里 0 星也要写上（0 == False，不能当成没有值）。"""
        s = self.s
        s.note([s.on_stars(own(0))])
        self.assertEqual(self.records("status")[-1]["changed"]["star"], [None, 0])

    def test_new_segment_starts_with_full_status(self):
        """判定新的一局（识别记录换段）以后，第一条 status 记录重新写当时已有的各项，只看这一段也知道角色等。"""
        s = self.s
        s.note([s.on_hud(hud(round=3))])
        s._begin_segment("select", "检测到选角界面")
        s.note([s.on_hud(hud(round=4))])
        changed = self.records("status")[-1]["changed"]
        self.assertEqual(changed["round"], [None, 4])
        self.assertEqual(changed["hero"], [None, "芬妮"])
        s.note([s.on_hud(hud(round=5))])
        self.assertEqual(self.records("status")[-1]["changed"], {"round": [4, 5]})

    def test_selected_change_recorded(self):
        s = self.s
        s.on_frame(chips(OFFER_A))
        s.on_frame(chips(OFFER_A, reread=True))
        s.on_frame(chips(OFFER_A, reread=True, selected=1))
        s.on_frame(chips(OFFER_A, reread=True, selected=1))
        s.on_frame(chips(OFFER_A, selected=None))
        got = [(r["slot"], r["chip"], r["reread"]) for r in self.records("read:selected")]
        self.assertEqual(got, [(1, "友情徽章", True), (None, None, False)])

    def test_hero_select_recorded_once_per_visit(self):
        s = self.s
        clock = [0.0]
        s._clock = lambda: clock[0]
        for t in (0.0, 3.0, 6.0):
            clock[0] = t
            s.on_select_screen()
        clock[0] = 40.0
        s.on_select_screen()
        got = self.records("read:hero_select")
        self.assertEqual(len(got), 2)
        self.assertEqual((got[0]["game_state"], got[0]["game_over"]), (True, False))

    def test_log_state_kind_as_is(self):
        self.s.log_state("pause", reasons=["focus"], why={"focus": "background"})
        self.assertEqual(self.rec.added[-1], {"kind": "pause", "reasons": ["focus"], "why": {"focus": "background"}})

    def test_select_trace_fields_optional(self):
        """read:select 的 rarity、partial 是可选字段：旧的识别记录没有它们，读记录的代码按缺省处理。"""
        from advisor.vision.pipeline import FrameProcessor

        seen = chips(OFFER_A, partial=(2,))
        seen.rarity = ["蓝", "蓝", "蓝"]
        proc = FrameProcessor(self.db, self.s)
        with mock.patch("advisor.vision.pipeline.looks_like_overlay_screen", return_value=True), \
                mock.patch("advisor.vision.pipeline.read_chip_select", return_value=seen), \
                mock.patch("advisor.vision.pipeline.name_strip", return_value=None):
            proc._read_select(np.zeros((1080, 1920, 3), np.uint8), 0.0, [])
        rec = self.records("read:select")[-1]
        self.assertEqual((rec["rarity"], rec["partial"]), (["蓝", "蓝", "蓝"], [False, False, True]))


if __name__ == "__main__":
    unittest.main()
