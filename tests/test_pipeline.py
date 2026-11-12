"""FrameProcessor 省掉重复的文字识别，用真的样本截图：

  同一个三选一界面上只查绿框、不每帧做文字识别（_read_select）：读得完整时一直沿用，筹码名那一条变了才再认；读得不完整时
  半秒后再完整识别；
  过得了预筛、却不是三选一的画面，画面没变就不再重复识别（_Unchanged）；真的战斗画面预筛就拦下，一直在动也不做三选一的
  文字识别，静止时一样记下「不是」、沿用（读不到东西的轮次不再读）；三选一已经认出来时标题带一时被盖住不算关掉；
  三选一期间也读进度条，读数变了下一帧马上补读（ProgressInSelectTest）。
读轮次、读进度条、认角色、读星数、选角界面判断换成假的（这里不测它们，ProgressInSelectTest 另有一条用真的），
三选一识别用真的。
三选一出来 2 秒内给出推荐（FastAdviceTest、IdleOneReadTest，识别函数换成假的）：手上的轮次、进度条够新时先给
推荐、下一帧再读一次核对，不同就重算；手动模式、刚从暂停恢复、本局还没读到过轮次、太久没读到轮次时照旧先读完；推荐给出以后
只盯绿框；不在三选一时一帧最多做一项读数（pending），读数、先后和一帧做完时一样。只盯绿框的两个例外（SelectRereadTest、
HudRetryTest）：刚出现那一帧读得可疑（刷新次数读成 0、概率行不在概率表里）时半秒后补认一次；读左上角出错、读不出轮次时隔约
1 秒再读，每次三选一最多 3 次。刚出现那一帧有筹码没读出来、还没给过推荐时先完整重读三张卡、核对排到重读之后，读不全的那一帧
留一张给诊断包（FirstRereadTest）。刷新那一帧不读左上角、只因为读不出轮次而再读时不重读进度条、先读完那一次不认角色、
三选一上读星数只走快的那一路（SelectTopTest）。
轮次数字被头像挡住（读轮次时看见了标记、数字读不出）：不认选角界面，隔一会儿只在标记左边那一小块认字，隔久一点才完整读，
一直算在对局里，三选一出来照样先给推荐（OccludedIdleTest、OccludedSelectTest）；地图未知、任务栏没开时隔一会儿才连带读
整块（MapBlockTest）；两个客户端同时识别时另一路的三选一显示着，自己暂停要做文字识别的读数（SelectBoardTest、DualYieldTest）。
一张截图代表画面在那一刻的样子：照截图走的几组用 FrameProcessor.settle 把到期的读数做完（悬浮窗一帧只做一项、紧接着取下一帧
接着做，同一张截图就是接着用这一张）。
对局外的画面（结算画面、房间等）交给 Session、上一局结束后第一次出现三选一（OutOfGameFlowTest），识别函数全部换成假的；
对局中看见轮次标记以后第二次起读不到时先不判选角界面，第一次读不到、过了时限、按热键、刚从暂停恢复、看见打完时的横幅颜色
以后照判（同一组的 test_pick_grace_*）。
去掉画面四周的黑边：先看靠边一圈就判完的结果和整套算完全相同（BordersAndTaskPanelTest）。
对局中少找字：三选一记下位置以后先快读、退回时完整读（SelectFastTest，真的文字识别）；读轮次先在标记左边那一小块只认字
（RoundQuickTest，识别函数换成假的）；进度条那一块没变就沿用上次的读数（ProgressReuseTest），那一块的样子够灵敏
（ProgressLookTest，真截图）。
另外核对 rules.toml 写明的 coins_ms、occluded_recheck_ms、occluded_full_ms、blank_full_ms、round_retry_gap_ms、
map_block_ms、select_fast、pick_grace_ms 和默认值一致。
对局中读到过轮次以后连标记都没看见时每 blank_full_s 才完整读一次（BlankFullTest）：只认字照旧、照常认选角界面，按热键、
连带读整块、三选一上照旧完整读；新一局还没读到轮次、本局已结束、离上次看见标记满 round_retry_gap_s 时不等。完整读轮次时
什么时候不换大小多认两遍（HudRetryArgumentTest；和等不等是同一个条件，FrameProcessor._mid_game）。
去黑边记住上一帧的范围（BordersAndTaskPanelTest 的 test_keep_*、test_processor_*）：黑边里零星亮点照旧去、亮成一片照新算。
"""
import contextlib
import dataclasses
import itertools
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor.data import LEVEL_NIGHTMARE, load_db
from advisor.pool import difficulty_of
from advisor.session import Session, Settings
from advisor.vision import chip_select, hud as hud_mod, pipeline
from advisor.vision.hud import Hud, Pregame, Progress, progress_look, same_progress_look
from advisor.vision.screens import CUSTOM_ROOM, MAIN_MENU, MATCH_FOUND, RESULT, REWARD, TEAM_ROOM, OutOfGame
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
REROLL1 = "20261002_013552_chipselect_levelup_star1_after_reroll1.png"  # 会员推荐信 / 手电筒-强光 / 优雅之羽
REROLL2 = "20261002_013608_chipselect_levelup_star1_after_reroll2.png"  # 医疗箱-紧急治疗 / 书签 / 能量回收
SELECTED = "20261002_013617_chipselect_selected_state.png"  # 同上，书签被选中
BATTLE = "20261002_021351_battle_scene.png"  # 战斗画面：上方也有黑带，过得了预筛第一段，第二段（标题带的位置）拦下
# 10-03 实机的全队任务三选一（2 星、2 级任务）：出场动画里图标还没加载（稀有度读不出），两秒后图标加载完、最左格蓝色
ICONS_LATE = "20261003_130848_chipselect_icons_not_loaded_team_task_real.jpg"
ICONS_LOADED = "20261003_130850_chipselect_team_task_tier2_star2_left_blue_real.jpg"
# 10-03 实机第 10 轮的任务三选一：任务奖励「进度 -1」已生效，进度条当前格由 11 退回 10
PROGRESS_BACK = "20261003_131934_chipselect_team_task_tier3_star3_left_blue_real.jpg"
# 第 4 轮，玩家列表第一行头像的天线挡住了数字（2560×1600，上下有黑边）：看得见轮次标记，数字读不出
OCCLUDED_ANTENNA = "20261018_164911_hud_round4_antenna_2560x1600.png"


def load(name: str) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.resize(img, (1920, round(img.shape[0] * 1920 / img.shape[1])), interpolation=cv2.INTER_AREA)


def band_rows(img: np.ndarray, margin: int = 0) -> tuple[int, int]:
    """三选一标题带占的行（位置见 chip_select.SELECT_BAND_EDGES；上下各多 margin 个界面单位）。"""
    h, w = img.shape[:2]
    unit = chip_select.ui_scale(w, h)
    top, bottom = chip_select.SELECT_BAND_EDGES
    return round(h / 2 + (top - margin) * unit), round(h / 2 + (bottom + margin) * unit)


def with_title_band(img: np.ndarray) -> np.ndarray:
    """照三选一的标题带画一条横贯全宽的纯黑带，带子里没有字：过得了预筛，文字识别认不出是三选一。"""
    top, bottom = band_rows(img)
    out = img.copy()
    out[top:bottom] = 0
    return out


@unittest.skipUnless(samples.samples_ok(SELECTED), samples.samples_reason(SELECTED))
class QuickRecheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.images = {name: load(name) for name in (REROLL1, REROLL2, SELECTED, ICONS_LATE, ICONS_LOADED)}

    def setUp(self):
        self.session = Session(self.db, Settings(hero="凛", round=3, star=1, star_read=True))
        # 认了几次三张卡：完整识别的次数，加上快读（记下位置以后只认字，见 FrameProcessor._select_read）读成的次数（快读退回时
        # 紧接着完整识别，只算那一次）
        self.calls = 0
        self.incomplete_once = False  # 第一次识别到三选一时去掉概率行（假装读错了）
        real, real_fast = pipeline.read_chip_select, pipeline.read_chip_select_fast

        def counted(img, db, **kw):
            self.calls += 1
            seen = real(img, db, **kw)
            if self.incomplete_once and seen is not None:
                self.incomplete_once = False
                seen = dataclasses.replace(seen, probabilities=None)
            return seen

        def counted_fast(img, db, layout):
            got = real_fast(img, db, layout)
            self.calls += got[0] is not None
            return got

        patches = [
            mock.patch.object(pipeline, "read_chip_select", side_effect=counted),
            mock.patch.object(pipeline, "read_chip_select_fast", side_effect=counted_fast),
            mock.patch.object(pipeline, "read_hud", return_value=Hud(round=3)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def process(self, name, now):
        img = self.images[name] if name else np.zeros((1080, 1920, 3), np.uint8) + 90
        return self.proc.process(img, now)

    def test_selected_frame_found_without_ocr(self):
        events = self.process(REROLL2, 0.0)
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])
        self.assertEqual(self.calls, 1)
        self.process(SELECTED, 0.2)  # 同一组候选，书签被选中：只查绿框
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.proc.chip_select_visible)
        self.process(None, 0.4)
        events = self.process(None, 0.6)
        self.assertEqual([(e.kind, e.chip) for e in events], [("picked", "书签")])

    def test_band_same_as_full(self):
        """快速盯绿框只截那一块（识别流程放上的要盯的那一份，pick_watch.Target：region 是它在客户区里的位置）：在真截图上
        和整幅画面上的快速复查结果相同——同一组候选认出选中的书签，没选中时没有，重选以后对不上。"""
        self.process(REROLL2, 0.0)
        target = self.proc.pick_watch.target
        self.assertIsNotNone(target)
        x, y, w, h = target.region
        for name, want in ((SELECTED, (True, 1)), (REROLL2, (True, None)), (REROLL1, (False, None))):
            img = self.images[name]
            full = pipeline.reread_selected(img, target.seen, target.strip)
            with self.subTest(image=name):
                self.assertEqual(target.check(img[y:y + h, x:x + w]), want)
                self.assertEqual((full is not None, full.selected if full is not None else None), want)
        self.assertEqual(self.calls, 1)  # 只完整识别了一次

    def test_reroll_triggers_full_read_not_timer(self):
        """重选了、筹码名变了才重新识别；推荐给出以后只盯绿框，隔多久都不再定时整页重认。"""
        self.process(REROLL1, 0.0)
        self.process(REROLL2, 0.2)  # 重选了，筹码名变了：重新识别
        self.assertEqual(self.calls, 2)
        self.process(REROLL2, 0.4)
        self.assertEqual(self.calls, 2)
        for t in (3.3, 6.5, 30.0):  # 隔多久都不定时完整识别
            self.process(REROLL2, t)
        self.assertEqual(self.calls, 2)

    def test_window_resize_triggers_full_read(self):
        self.process(REROLL2, 0.0)
        small = cv2.resize(self.images[REROLL2], (1280, 684), interpolation=cv2.INTER_AREA)
        self.proc.process(small, 0.2)
        self.assertEqual(self.calls, 2)

    def test_incomplete_read_retried(self):
        """读得不完整（这里是概率行读不到）：只沿用 SELECT_RETRY_S，之后马上完整识别；读完整以后一直沿用。"""
        self.incomplete_once = True
        events = self.process(REROLL2, 0.0)
        self.assertEqual(self.calls, 1)
        self.assertEqual([e.state.probabilities for e in events if e.kind == "advice"], [None])
        self.process(REROLL2, 0.2)
        self.assertEqual(self.calls, 1)
        events = self.process(REROLL2, 0.6)
        self.assertEqual(self.calls, 2)
        self.assertEqual([e.state.probabilities for e in events if e.kind == "advice"], [(60, 37, 3)])
        self.process(REROLL2, 1.0)
        self.process(REROLL2, 2.0)
        self.assertEqual(self.calls, 2)

    def test_icons_not_loaded_then_loaded(self):
        """10-03 实机：任务三选一刚出现时图标还没加载，三格稀有度都读不出（不完整），半秒后再完整识别；
        出场动画结束后筹码名的位置变了，马上完整识别，推荐按读到的最左格稀有度（蓝色）计算。"""
        events = self.process(ICONS_LATE, 0.0)
        self.assertEqual(self.calls, 1)
        first = [e for e in events if e.kind == "advice"]
        self.assertEqual([(e.state.source, e.state.tier, e.state.left_rarity) for e in first], [("task", 2, None)])
        self.process(ICONS_LATE, 0.2)
        self.assertEqual(self.calls, 1)
        self.process(ICONS_LATE, 0.6)  # 还是同一帧画面：仍不完整，再识别一次
        self.assertEqual(self.calls, 2)
        events = self.process(ICONS_LOADED, 0.8)
        self.assertEqual(self.calls, 3)
        advice = [e for e in events if e.kind == "advice"]
        self.assertEqual([(e.state.source, e.state.tier, e.state.left_rarity) for e in advice], [("task", 2, "蓝")])
        self.process(ICONS_LOADED, 1.4)  # 读完整了：照常沿用
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.session.settings.owned, [])  # 同一次三选一，没有记下筹码


@unittest.skipUnless(samples.samples_ok(REROLL2, PROGRESS_BACK), samples.samples_reason(REROLL2, PROGRESS_BACK))
class ProgressInSelectTest(unittest.TestCase):
    """三选一期间也读进度条（任务奖励「进度 -1」常在三选一出现前生效）：读到和已采用的不同的格时，下一帧马上补读一次，
    两次一致就采用，这一次三选一的推荐随即按新的当前格计算；每次只补读一次。这几条的识别流程是新建的（还没读到过轮次），
    三选一出现时先读完再给推荐；先给推荐、下一帧再核对的见 FastAdviceTest。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.images = {name: load(name) for name in (REROLL2, PROGRESS_BACK)}

    def setUp(self):
        self.session = Session(self.db, Settings(hero="凛", round=10, star=1, star_read=True))
        self.session.on_progress(11), self.session.on_progress(11)
        self.current = 10  # 假的进度条读数
        self.reads = 0
        self.patches = {
            "read_hud": mock.patch.object(pipeline, "read_hud", return_value=Hud(round=10)),
            "read_progress": mock.patch.object(pipeline, "read_progress", side_effect=self.fake_progress),
            "read_hero": mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero="凛")),
            "read_own_stars": mock.patch.object(pipeline, "read_own_stars", return_value=None),
            "is_select_screen": mock.patch.object(pipeline, "is_select_screen", return_value=False),
            "read_pregame": mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            "read_players": mock.patch.object(pipeline, "read_players", return_value=None),
            "read_chip_panel": mock.patch.object(pipeline, "read_chip_panel", None),
            "mutator_catalog": mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        }

    def start(self, real_progress: bool = False):
        for name, p in self.patches.items():
            if real_progress and name == "read_progress":
                continue
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def fake_progress(self, img):
        self.reads += 1
        return Progress(self.current, None)

    def process(self, name, now):
        img = self.images[name] if name else np.zeros((1080, 1920, 3), np.uint8) + 90
        return self.proc.process(img, now)

    @staticmethod
    def advice_progress(events) -> list:
        return [e.state.progress for e in events if e.kind == "advice"]

    def test_changed_reading_rechecked_at_once(self):
        self.start()
        events = self.process(REROLL2, 0.0)
        self.assertEqual(self.reads, 1)
        self.assertEqual(self.session.game.progress, 11)  # 只读到一次，还不采用
        self.assertEqual(self.advice_progress(events), [11])
        events = self.process(REROLL2, 0.2)  # 不到 hud_ms，但读数待确认：马上补读
        self.assertEqual(self.reads, 2)
        self.assertEqual(self.session.game.progress, 10)
        self.assertEqual(self.advice_progress(events), [10])  # 这一帧的推荐就按新的当前格
        self.process(REROLL2, 0.4)
        self.assertEqual(self.reads, 2)
        # 推荐给出、核对过以后只盯绿框：到三选一关掉都不再读进度条，过了 hud_ms 也不读
        self.process(REROLL2, 0.2 + self.proc.hud_s)
        self.process(REROLL2, 0.2 + 3 * self.proc.hud_s)
        self.assertEqual(self.reads, 2)

    def test_same_reading_not_rechecked(self):
        self.current = 11
        self.start()
        self.process(REROLL2, 0.0)
        self.process(REROLL2, 0.2)
        self.assertEqual(self.reads, 1)
        self.assertEqual(self.session.game.progress, 11)

    def test_recheck_only_once(self):
        """补读的结果又不一样：不再接着补读（这一次三选一里也不再读）。"""
        self.start()
        self.process(REROLL2, 0.0)
        self.current = 9
        self.process(REROLL2, 0.2)
        self.process(REROLL2, 0.4)
        self.assertEqual(self.reads, 2)
        self.assertEqual(self.session.game.progress, 11)

    def test_leaving_select_clears_recheck(self):
        self.start()
        self.process(REROLL2, 0.0)
        self.assertTrue(self.proc._recheck_progress)
        self.process(None, 0.2)
        self.assertFalse(self.proc._recheck_progress)
        self.assertEqual(self.reads, 1)

    def test_real_frame_00872(self):
        """真实的进度条识别：10-03 实机第 10 轮的任务三选一，两帧后当前格由 11 改为 10。"""
        self.start(real_progress=True)
        self.process(PROGRESS_BACK, 0.0)
        self.assertEqual(self.session.game.progress, 11)
        events = self.process(PROGRESS_BACK, 0.2)
        self.assertEqual(self.session.game.progress, 10)
        self.assertEqual(self.advice_progress(events), [10])


@unittest.skipUnless(samples.samples_ok(BATTLE, REROLL2, SELECTED), samples.samples_reason(BATTLE, REROLL2, SELECTED))
class UnchangedScreenTest(unittest.TestCase):
    """过得了预筛、却不是三选一的画面（战斗画面上画一条没有字的标题带）：认定「不是三选一」「不是选角界面」、轮次也读不到
    以后，画面没变就不再重复识别。真的战斗画面预筛就拦下：上方有黑带、标题带不在，不做文字识别也算认过「不是三选一」，静止时
    照样记下、沿用（test_real_battle_never_read 等）；例外是按热键的那一帧、三选一已经认出来的时候。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.real_battle = load(BATTLE)
        cls.real_moved = np.roll(cls.real_battle, cls.real_battle.shape[1] // 20, axis=1)
        cls.battle = with_title_band(cls.real_battle)
        cls.select = load(REROLL2)
        cls.selected = load(SELECTED)  # 同一组候选，书签被选中
        cls.moved = np.roll(cls.battle, cls.battle.shape[1] // 20, axis=1)  # 画面明显变了：左右平移 5%
        cls.blank = np.zeros_like(cls.battle) + 90  # 过不了预筛的画面
        # 淡入到九成的三选一：和完整的三选一只差一点（指纹算「没变」），这里假装它没被认出来
        cls.partial = cv2.addWeighted(cls.select, 0.9, cls.battle, 0.1, 0)

    def setUp(self):
        self.session = Session(self.db, Settings(
            hero="凛", round=3, star=1, star_read=True, map="异变图书馆", map_source="tasks", map_confidence="high",
        ))
        self.session.game.seen_hero = "凛"
        self.select_calls = 0
        self.hud_calls: list[bool] = []  # 每次读轮次时的 full 参数
        self.pick_calls = 0
        real = pipeline.read_chip_select

        def counted(img, db, **kw):
            self.select_calls += 1
            return None if img is self.partial else real(img, db, **kw)

        def fake_hud(img, db, full, **kw):
            self.hud_calls.append(full)
            return Hud()  # 战斗画面上没有轮次

        def fake_pick(img, db, **kw):
            self.pick_calls += 1
            return False

        patches = [
            mock.patch.object(pipeline, "read_chip_select", side_effect=counted),
            mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", side_effect=fake_pick),
            mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def test_real_battle_never_read(self):
        """真的战斗画面：上方的黑条不在三选一标题带的位置，预筛就拦下，画面一直在动也不做三选一的文字识别。"""
        self.assertTrue(chip_select.looks_like_overlay_screen(self.battle))  # 其余几条用的画面过得了预筛
        self.assertTrue(chip_select.dark_band(self.real_battle))
        self.assertFalse(chip_select.looks_like_overlay_screen(self.real_battle))
        for t in range(6):
            self.proc.process(self.real_moved if t % 2 else self.real_battle, float(t))
        self.assertEqual(self.select_calls, 0)
        self.assertFalse(self.proc.chip_select_visible)

    def test_dark_band_once_per_frame(self):
        """预筛没过时要分「连暗行都没有」和「标题带不在」两种情况：第一段同一帧只算一次（取样落在阈值附近的帧一次要整幅缩放）。"""
        for name, img in (("上方有黑带", self.real_battle), ("上方没有暗行", self.blank)):
            with self.subTest(name), mock.patch.object(chip_select, "dark_band", wraps=chip_select.dark_band) as dark:
                self.proc.process(img, 0.0)
                self.assertEqual(dark.call_count, 1)

    def test_real_battle_remembered_while_still(self):
        """静止的真战斗画面没做文字识别也记下「不是三选一」；第一段都没过的画面（压根没有暗行）不算看过。"""
        self.proc.process(self.real_battle, 0.0)
        self.assertIsNone(self.proc._not_select.matched)  # 第 1 帧还不知道画面静不静止
        self.proc.process(self.real_battle, 1.0)
        remembered = self.proc._not_select.matched
        self.assertIsNotNone(remembered)
        self.proc.process(self.real_battle, 2.0)
        self.assertIs(self.proc._not_select.matched, remembered)
        self.proc.process(self.real_moved, 3.0)  # 画面变了：不沿用，也不算静止
        self.assertIsNone(self.proc._not_select.matched)
        self.proc.process(self.blank, 4.0)
        self.assertIsNone(self.proc._not_select.matched)
        self.assertIsNone(self.proc._not_select._last)
        self.assertEqual(self.select_calls, 0)

    def test_chip_select_after_real_battle_read_at_once(self):
        """静止的真战斗画面记下了「不是」，三选一一出来马上完整识别、给推荐。"""
        for t in range(5):
            self.proc.process(self.real_battle, float(t))
        self.assertIsNotNone(self.proc._not_select.matched)
        self.assertEqual(self.select_calls, 0)
        events = self.proc.process(self.select, 5.0)
        self.assertEqual(self.select_calls, 1)
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])

    def test_title_band_back_not_taken_as_unchanged(self):
        """标题带不在的画面记下了「不是」，标题带回来时不沿用：带子只占判定区域的一小块，其余差不多时指纹算「没变」，不能因此
        把三选一跳过去。"""
        top, bottom = band_rows(self.select)
        faded = self.select.copy()
        faded[top:bottom] = np.maximum(faded[top:bottom], chip_select.SELECT_BAND_BLACK + 10)  # 带子灰了：第一段照样过，第二段不过
        self.assertTrue(chip_select.dark_band(faded))
        self.assertFalse(chip_select.looks_like_overlay_screen(faded))
        self.assertTrue(pipeline._same(
            pipeline._fingerprint(faded, (0.0, 0.0, 1.0, pipeline.OCR_TOP)),
            pipeline._fingerprint(self.select, (0.0, 0.0, 1.0, pipeline.OCR_TOP)),
        ))
        for t in range(3):
            self.proc.process(faded, float(t))
        self.assertIsNotNone(self.proc._not_select.matched)
        self.assertEqual(self.select_calls, 0)
        events = self.proc.process(self.select, 3.0)
        self.assertEqual(self.select_calls, 1)
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])

    def _check_select_with_cover(self, frames: int) -> None:
        """三选一已经认出来了，标题带一时被盖住不算界面关了：筹码名那一条没变就只查绿框，不结算（session.selection.GONE_FRAMES
        连着两帧就会结算）、不重新给推荐、不算新的一次。"""
        top, bottom = band_rows(self.selected, margin=10)
        covered = self.selected.copy()
        w = covered.shape[1]
        covered[top:bottom, round(w * 0.1):round(w * 0.9)] = (200, 200, 220)
        self.assertTrue(chip_select.dark_band(covered))
        self.assertFalse(chip_select.looks_like_overlay_screen(covered))
        kinds: list[str] = []
        for i, img in enumerate([self.selected] * 3 + [covered] * frames + [self.selected] * 3):
            kinds += [e.kind for e in self.proc.process(img, i * 0.15)]
            self.assertTrue(self.proc.chip_select_visible, i)
        self.assertEqual(self.select_calls, 1)
        self.assertEqual(kinds.count("advice"), 1)
        for kind in ("picked", "missed_pick", "pick_reverted"):
            self.assertNotIn(kind, kinds)
        self.assertEqual(self.session.select._select_no, 1)

    def test_title_band_covered_one_frame(self):
        self._check_select_with_cover(1)

    def test_title_band_covered_two_frames(self):
        self._check_select_with_cover(2)

    def test_title_band_covered_four_frames(self):
        self._check_select_with_cover(4)

    def test_select_closed_into_dark_top_read_once_more(self):
        """三选一真的关了、进到上方有黑带的画面：完整识别一次否定，缓存清掉，之后不再认，也不一直等绿框。"""
        self.proc.process(self.select, 0.0)
        self.assertEqual(self.select_calls, 1)
        self.assertIsNotNone(self.proc._select_cache)
        for t in range(1, 6):
            self.proc.process(self.real_battle, float(t))
        self.assertEqual(self.select_calls, 2)
        self.assertIsNone(self.proc._select_cache)
        self.assertFalse(self.proc.chip_select_visible)

    def test_select_closed_into_light_screen_not_read(self):
        """三选一关了、进到上方没有暗行的画面：不用文字识别就知道不是。"""
        self.proc.process(self.select, 0.0)
        self.proc.process(self.blank, 1.0)
        self.assertEqual(self.select_calls, 1)
        self.assertIsNone(self.proc._select_cache)
        self.assertFalse(self.proc.chip_select_visible)

    def test_battle_read_once_until_screen_changes(self):
        for t in range(6):
            self.proc.process(self.battle, float(t))
        # 第 1 帧识别；第 2 帧和上一帧一样（画面静止），识别后记下「不是三选一」；之后都沿用
        self.assertEqual(self.select_calls, 2)
        self.assertFalse(self.proc.chip_select_visible)
        self.proc.process(self.moved, 6.0)
        self.assertEqual(self.select_calls, 3)

    def test_chip_select_after_battle_read_at_once(self):
        for t in range(5):
            self.proc.process(self.battle, float(t))
        calls = self.select_calls
        events = self.proc.process(self.select, 5.0)
        self.assertEqual(self.select_calls, calls + 1)
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])

    def test_rechecked_after_max_time(self):
        for t in (0.0, 1.0, 2.0):
            self.proc.process(self.battle, t)
        self.assertEqual(self.select_calls, 2)  # 第 2 帧（1.0 秒）记下
        limit = self.proc._not_select.max_s
        self.proc.process(self.battle, 1.0 + limit - 0.1)
        self.assertEqual(self.select_calls, 2)
        self.proc.process(self.battle, 1.0 + limit + 0.1)
        self.assertEqual(self.select_calls, 3)

    def test_fading_in_frame_not_remembered(self):
        """画面还在变的那一帧没认出来，不能记成「不是」：下一帧显示完整了要马上再认。"""
        self.assertTrue(pipeline._same(
            pipeline._fingerprint(self.partial, (0.0, 0.0, 1.0, 0.62)),
            pipeline._fingerprint(self.select, (0.0, 0.0, 1.0, 0.62)),
        ))
        self.proc.process(self.blank, 0.0)
        self.proc.process(self.partial, 1.0)
        events = self.proc.process(self.select, 2.0)
        self.assertEqual(self.select_calls, 2)
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])

    def test_hotkey_forces_read(self):
        for t in range(4):
            self.proc.process(self.battle, float(t))
        calls = self.select_calls
        self.proc.process(self.battle, 4.0, force=True)
        self.assertEqual(self.select_calls, calls + 1)

    def test_hotkey_reads_dark_top_without_band(self):
        """第二段只靠游戏的排版：按热键的那一帧上方有黑带就照样认；上方连暗行都没有的画面按热键也不认。"""
        for t in range(4):
            self.proc.process(self.real_battle, float(t))
        self.assertEqual(self.select_calls, 0)
        self.proc.process(self.real_battle, 4.0, force=True)
        self.assertEqual(self.select_calls, 1)
        self.proc.process(self.real_battle, 5.0)
        self.assertEqual(self.select_calls, 1)
        self.proc.process(self.blank, 6.0, force=True)
        self.assertEqual(self.select_calls, 1)

    def test_hotkey_found_select_kept_when_band_not_where_expected(self):
        """排版变了、第二段一直认不出三选一：按热键认出来以后接着只查绿框，不当作关掉；按热键之前不认。"""
        with mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False):
            self.proc.process(self.select, 0.0)
            self.proc.process(self.select, 0.5)
            self.assertEqual(self.select_calls, 0)
            self.assertFalse(self.proc.chip_select_visible)
            events = self.proc.process(self.select, 1.0, force=True)
            self.assertEqual(self.select_calls, 1)
            self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])
            for t in (1.5, 2.0, 2.5):
                self.proc.process(self.select, t)
                self.assertTrue(self.proc.chip_select_visible)
            self.assertEqual(self.select_calls, 1)

    def _check_round_not_reread(self, still: np.ndarray, moved: np.ndarray) -> None:
        self.proc._not_select.max_s = self.proc._not_pick.max_s = 100.0  # 这里不测「最多沿用多久」
        for t in (0.0, 3.0, 6.0, 9.0, 12.0):
            self.proc.settle(still, t)
        # 第 1、2 次照常读；第 2 次时画面已经静止、记下了，之后不再读
        self.assertEqual(self.hud_calls, [False, False])
        self.assertEqual(self.pick_calls, 2)
        # 地图变成未知：上次只读了轮次，没读任务栏，要连任务栏一起再读一次
        self.session.set_map(None)
        self.proc.settle(still, 15.0)
        self.proc.settle(still, 18.0)
        self.assertEqual(self.hud_calls, [False, False, True])
        # 画面变了：重新读
        self.proc.settle(moved, 21.0)
        self.assertEqual(self.hud_calls, [False, False, True, True])
        self.assertEqual(self.pick_calls, 3)

    def test_round_and_select_screen_not_reread_while_unchanged(self):
        """每 3 秒一轮（读轮次、认选角界面……）：每个时刻的这一轮都做完再看下一个时刻（settle，悬浮窗一帧一项、紧接着几帧
        做完）。真的战斗画面标题带不在，三选一的文字识别一次也不做，读轮次、认选角界面照样沿用。"""
        self._check_round_not_reread(self.real_battle, self.real_moved)
        self.assertEqual(self.select_calls, 0)

    def test_round_not_reread_after_text_says_not_select(self):
        """过了预筛、文字识别否定的画面（转场等）也一样。"""
        self._check_round_not_reread(self.battle, self.moved)

    def test_real_battle_round_reread_after_max_time(self):
        """静止的真战斗画面上读不到东西的轮次也只沿用 max_s：记下的那一条换新以后再读一次，不会一直不读。"""
        limit = self.proc._not_select.max_s
        self.assertEqual(limit, 10.0)
        for t in range(0, 30, 3):
            self.proc.settle(self.real_battle, float(t))
        # 第 0、3 秒照常读，3 秒那次记下；过了 10 秒的第一轮（15 秒）换新一条、读一次；27 秒同样
        self.assertEqual(len(self.hud_calls), 4)
        self.assertEqual(self.select_calls, 0)


@unittest.skipUnless(samples.samples_ok(REROLL2), samples.samples_reason(REROLL2))
class BordersAndTaskPanelTest(unittest.TestCase):
    """画面四周的黑边（上下或左右加黑边的窗口）和「任务栏是否打开」的判断（不做文字识别）。"""

    def test_trim_borders(self):
        img = load(REROLL2)
        h, w = img.shape[:2]
        for top, bottom, left, right in ((90, 90, 0, 0), (0, 0, 240, 240), (60, 20, 0, 300), (40, 40, 40, 40)):
            with self.subTest(pad=(top, bottom, left, right)):
                padded = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0))
                got, offset = pipeline.trim_borders(padded)
                self.assertEqual((got.shape[:2], offset), ((h, w), (left, top)))
                self.assertTrue(np.array_equal(got, img))
        dark = np.zeros((1080, 1920, 3), np.uint8)  # 整幅都暗（转场）：不当成黑边
        self.assertIs(pipeline.trim_borders(dark)[0], dark)
        nearly = cv2.copyMakeBorder(img, 4, 4, 0, 0, cv2.BORDER_CONSTANT)  # 不到 1%：不去
        self.assertIs(pipeline.trim_borders(nearly)[0], nearly)

    def test_keep_ignores_speck_in_border(self):
        """上一帧的黑边里新冒出一块小亮点（16:10 屏幕上游戏画在黑边里的小图标）：带上 keep 时照旧去同样的黑边；
        不带 keep 时和原来一样，被这点亮块引着少去了。"""
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        keep = (0, 90, w, 90 + h)
        flagged = padded.copy()
        flagged[20:26, 100:106] = 255
        got, offset = pipeline.trim_borders(flagged, keep)
        self.assertEqual((got.shape[:2], offset), ((h, w), (0, 90)))
        self.assertTrue(np.array_equal(got, img))
        got, offset = pipeline.trim_borders(flagged)
        self.assertEqual((got.shape[:2], offset), ((h + 90 - 20, w), (0, 20)))  # 少去了：上边只去掉 20 行

    def test_keep_uses_new_edge_when_border_is_filled(self):
        """黑边里亮的一大片（超过 BORDER_SPECK）：照新算的。"""
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        keep = (0, 90, w, 90 + h)
        filled = padded.copy()
        filled[0:70] = 255
        got, offset = pipeline.trim_borders(filled, keep)
        self.assertEqual(offset, (0, 0))  # 上边照新算的（不去）；下边的黑边照旧去掉
        self.assertEqual(got.shape[:2], (90 + h, w))

    def test_keep_full_or_thicker_border(self):
        """keep 是整幅（原来没有黑边）：照旧原样返回；黑边比 keep 厚的那一边照新算的。"""
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        got, offset = pipeline.trim_borders(padded, (0, 0, w, h + 180))
        self.assertEqual((got.shape[:2], offset), ((h, w), (0, 90)))
        got, offset = pipeline.trim_borders(padded, (0, 40, w, h + 140))
        self.assertEqual((got.shape[:2], offset), ((h, w), (0, 90)))

    def test_keep_turns_off_edge_shortcut(self):
        """小亮点碰到最外一圈（_edges_lit 为真）：不给 keep 时那条捷径原样返回，给了 keep 时躲得过。"""
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        edge = padded.copy()
        edge[0:4, 500:504] = 255
        edge[h + 180 - 4:h + 180, 500:504] = 255
        step = max(1, min(edge.shape[:2]) // 270)
        self.assertTrue(pipeline._edges_lit(edge, step))
        self.assertEqual(pipeline.trim_borders(edge)[1], (0, 0))  # 捷径：原样返回
        got, offset = pipeline.trim_borders(edge, (0, 90, w, 90 + h))
        self.assertEqual((got.shape[:2], offset), ((h, w), (0, 90)))
        self.assertTrue(np.array_equal(got, img))

    def test_processor_keeps_border_across_frames(self):
        """连着两帧（先干净、再带亮块）去掉的黑边不变；换了画面大小就不带 keep。"""
        db = load_db()
        proc = pipeline.FrameProcessor(db, Session(db, Settings(hero="凛")))
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        flagged = padded.copy()
        flagged[20:26, 100:106] = 255
        self.assertEqual(proc._trim(padded)[1], (0, 90))
        self.assertEqual(proc._trim(flagged)[1], (0, 90))
        self.assertEqual(proc._border, ((h + 180, w), (0, 90, w, 90 + h)))
        other = cv2.resize(padded, (w // 2, (h + 180) // 2))
        with mock.patch.object(pipeline, "trim_borders", return_value=(other, (0, 0))) as trim:
            proc._trim(other)
        self.assertIsNone(trim.call_args.args[1])

    def test_keep_speck_threshold(self):
        """多出来的那一条里亮像素的占比在 BORDER_SPECK 两边：低一点照 keep 去，高一点照这一帧自己算的（和不给 keep 一样）。
        亮块从第 0 行画到黑边底下（第 90 行），这一帧算出的上边是 0，那一条里亮像素的占比就是亮块的宽 / 画面宽。"""
        img = load(REROLL2)
        h, w = img.shape[:2]
        padded = cv2.copyMakeBorder(img, 90, 90, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        keep = (0, 90, w, 90 + h)
        for ratio, kept in ((0.9, True), (1.1, False)):
            flagged = padded.copy()
            flagged[0:90, 100:100 + round(pipeline.BORDER_SPECK * ratio * w)] = 255
            with self.subTest(ratio=ratio):
                got, offset = pipeline.trim_borders(flagged, keep)
                if kept:
                    self.assertEqual((got.shape[:2], offset), ((h, w), (0, 90)))
                else:
                    own, own_offset = pipeline.trim_borders(flagged)
                    self.assertEqual((got.shape[:2], offset), (own.shape[:2], own_offset))
                    self.assertEqual(offset, (0, 0))

    def test_keep_never_leaves_less_than_half(self):
        """keep 和这一帧各暗在一边：各边分别照 keep 去完剩不到一半（这里什么都不剩）时，照这一帧自己算的去。"""
        img = np.zeros((400, 400, 3), np.uint8)
        img[1, 200] = 255  # 最上面一个亮点：这一帧自己算的上边是 0
        img[197:200] = 255  # 上半幅最底下三行亮着（占上半幅 1.5%，不到 BORDER_SPECK）
        own, own_offset = pipeline.trim_borders(img)
        self.assertEqual((own.shape[:2], own_offset), ((200, 400), (0, 0)))
        got, offset = pipeline.trim_borders(img, (0, 200, 400, 400))  # 上一帧只留了下半幅
        self.assertEqual((got.shape[:2], offset), ((200, 400), (0, 0)))

    # 平常的画面四条边上都有亮点、只看靠边一圈就判完（_edges_lit）的代表：4K 的三选一、战斗画面（上方也有黑带）、
    # 对局画面，21:9、4:3 的实机截图。只对这几张断言走了这条快路：以后加进四周偏暗、但没有黑边的样本（转场画面等）时，
    # 整套算下来照样原样返回，只是慢一点，不该让这条测试失败
    FAST_PATH_SAMPLES = (SELECTED, BATTLE, "20261002_012918_hud_round1.png", ICONS_LOADED,
                         "20261002_144039_hud_round1_2560x1057.jpg", "20261002_193639_hud_round11_2397x1798.jpg")
    # 故意带着上下黑边的实机截图（2560×1600 的屏幕上开 16:9 的游戏，上下各一条纯黑）：头像挡住轮次的两张和大厅（主菜单）
    # 三张，读轮次的用例（tests/test_real_games.py 的 RoundSanityTest）、主菜单空转的用例（tests/test_pipeline_sequences.py）
    # 照流程先去黑边再读，截图要保持原样。所以不算在「样本都没有黑边」里，另由 test_letterboxed_samples 核对去掉的正是上下的
    # 黑边。值是去掉黑边以后的（高, 宽）：大厅这三张上边 80 行纯黑，下边连着 86 行纯黑（主菜单底栏最下面几行本来就是纯黑），
    # 一起去掉，所以剩 1434 行
    LETTERBOXED = {
        "20261106_211543_hud_round3_ears_hant_2560x1600.png": (1440, 2560),
        "20261018_164911_hud_round4_antenna_2560x1600.png": (1440, 2560),
        "20261106_211618_lobby_dates_hant_2560x1600.png": (1434, 2560),
        "20261108_014416_lobby_banner_pass_hant_2560x1600.png": (1434, 2560),
        "20261108_045522_lobby_banner_gift_hant_2560x1600.png": (1434, 2560),
    }

    def test_samples_have_no_borders(self):
        for f in sorted(SAMPLES.glob("2026*.*")):
            if f.name in self.LETTERBOXED:
                continue
            img = cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR)
            with self.subTest(f.name):
                self.assertIs(pipeline.trim_borders(img)[0], img)
                if f.name in self.FAST_PATH_SAMPLES:
                    self.assertTrue(pipeline._edges_lit(img, max(1, min(img.shape[:2]) // 270)))
        self.assertEqual([n for n in self.FAST_PATH_SAMPLES if not (SAMPLES / n).exists()], [])

    def test_letterboxed_samples(self):
        """上下带黑边的那几张实机截图（LETTERBOXED）：去掉的是上下各一条纯黑、左右不动，剩下的是 16:9 的游戏画面，
        第一行、最后一行都有亮点（没多切进游戏画面）。"""
        samples.require_samples(*self.LETTERBOXED)
        for name, size in self.LETTERBOXED.items():
            img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
            got, (x, y) = pipeline.trim_borders(img)
            h, w = got.shape[:2]
            with self.subTest(name):
                self.assertEqual(img.shape[:2], (1600, 2560))
                self.assertEqual(((h, w), (x, y)), (size, (0, 80)))
                self.assertAlmostEqual(w / h, 16 / 9, delta=0.01)
                self.assertTrue(np.array_equal(got, img[y:y + h, x:x + w]))
                self.assertLessEqual(int(img[:y].max()), pipeline.BORDER_DARK)
                self.assertLessEqual(int(img[y + h:].max()), pipeline.BORDER_DARK)
                self.assertGreater(int(got[0].max()), pipeline.BORDER_DARK)
                self.assertGreater(int(got[-1].max()), pipeline.BORDER_DARK)

    def full_trim(self, img):
        """整套算（不先看靠边一圈）的结果。"""
        with mock.patch.object(pipeline, "_edges_lit", return_value=False):
            return pipeline.trim_borders(img)

    def assert_same_as_full(self, img, label=""):
        """先看靠边一圈的结果和整套算的完全相同：左上角、是不是原图本身、切出来的是不是同一块（数据地址、形状、步长）。
        两边都出错时错误种类相同。"""
        def outcome(fn):
            try:
                got, offset = fn(img)
            except Exception as e:  # noqa: BLE001
                return type(e)
            return offset, got is img, (got.__array_interface__["data"][0], got.shape, got.strides)

        self.assertEqual(outcome(pipeline.trim_borders), outcome(self.full_trim), label)

    def test_edges_first_same_as_full(self):
        """合成的黑边：只有一边、上下、左右、四边；宽 1 像素、粗看的间隔前后、刚好不到和刚好到 1%（不到不去）、
        去掉后剩不到一半（不去）；黑边亮度 12（算黑）和 13（不算黑）；黑边里有一个亮点，在粗看的点上或不在。"""
        img = load(REROLL2)  # 1920 宽
        h, w = img.shape[:2]
        step = max(1, min(h, w) // 270)
        for width in sorted({1, step - 1, step, step + 1, int(0.01 * h), int(0.01 * h) + 1, round(0.05 * h),
                             round(0.26 * h), round(0.3 * h)}):
            for fill in (0, 12, 13):
                for pad in ((width, 0, 0, 0), (0, width, 0, 0), (0, 0, width, 0), (0, 0, 0, width),
                            (width, width, 0, 0), (0, 0, width, width), (width, width, width, width)):
                    padded = cv2.copyMakeBorder(img, *pad, cv2.BORDER_CONSTANT, value=(fill, fill, fill))
                    self.assert_same_as_full(padded, f"pad={pad} fill={fill}")
        letterbox = cv2.copyMakeBorder(img, 54, 54, 0, 0, cv2.BORDER_CONSTANT)
        H, W = letterbox.shape[:2]
        st = max(1, min(H, W) // 270)
        for y, x in ((0, 0), (st, 5 * st), (1, 3), (st + 1, W - 1), (H - 1, W // 2), (H - 1 - st, 7)):
            spot = letterbox.copy()
            spot[y, x] = (0, 0, 200)
            self.assert_same_as_full(spot, f"spot={y},{x}")
        for blank in (np.zeros((1080, 1920, 3), np.uint8), np.full((1080, 1920, 3), 12, np.uint8),
                      np.full((1080, 1920, 3), 13, np.uint8)):
            self.assert_same_as_full(blank, f"blank={blank[0, 0, 0]}")
        block = np.zeros((1080, 1920, 3), np.uint8)
        block[400:700, 600:1300] = 90  # 中间一小块亮的：去掉后剩不到一半，不去
        self.assert_same_as_full(block, "center block")
        for odd in (np.zeros((0, 10, 3), np.uint8), np.full((1080, 1920), 90, np.uint8),
                    np.full((1, 1, 3), 200, np.uint8), np.full((1080, 1920, 4), 5, np.uint8)):
            self.assert_same_as_full(odd, f"odd {odd.shape}")

    def test_random_same_as_full(self):
        rng = np.random.default_rng(6)
        for i in range(400):
            h, w = int(rng.integers(1, 700)), int(rng.integers(1, 900))
            img = np.zeros((h, w, 3), np.uint8)
            t, b = (int(rng.integers(0, h // 2 + 1)) if rng.random() < 0.4 else 0 for _ in range(2))
            l, r = (int(rng.integers(0, w // 2 + 1)) if rng.random() < 0.4 else 0 for _ in range(2))
            if t + b < h and l + r < w:
                img[t:h - b, l:w - r] = rng.integers(0, 256, (h - b - t, w - r - l, 3), np.uint8)
            img[:] = np.maximum(img, int(rng.integers(0, 14)) if rng.random() < 0.3 else 0)
            for _ in range(int(rng.integers(0, 4))):
                img[rng.integers(h), rng.integers(w)] = int(rng.integers(0, 256))
            self.assert_same_as_full(img, f"#{i} {w}x{h} pad={t},{b},{l},{r}")

    def test_task_panel_visible(self):
        from advisor.vision.hud import task_panel_visible

        open_ = {"20261002_012947_task_panel.png", "20261002_012956_chips_panel_empty.png"}  # 后一张任务栏也开着
        for f in sorted(SAMPLES.glob("2026*.*")):
            img = cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR)
            for width in (None, 1280):
                small = img if width is None or img.shape[1] <= width else cv2.resize(
                    img, None, fx=width / img.shape[1], fy=width / img.shape[1], interpolation=cv2.INTER_AREA)
                with self.subTest(f.name, width=width):
                    self.assertEqual(task_panel_visible(small), f.name in open_)


class FlowTest(unittest.TestCase):
    """FrameProcessor 接通 Session 的新流程：不支持的情况、任务栏立即读、筹码面板、队友、存角色模板、畸变组件、游戏语言。
    识别函数全部换成假的。按轮读的几条每个时刻用 settle 把这一轮做完（悬浮窗一帧一项，紧接着几帧做完）。"""

    def setUp(self):
        self.db = load_db()
        self.session = Session(self.db, Settings(hero="凛"))
        self.round, self.hero, self.stars, self.players, self.panel = None, None, None, None, None
        self.tasks_open, self.select_info, self.pregame = False, None, Pregame()
        self.hud_calls, self.saved = [], []

        def fake_hud(img, db, full, **kw):
            self.hud_calls.append(full)
            return Hud(round=self.round)

        def fake_select(img, db, info=None):
            if info is not None and self.select_info is not None:
                info["foreign"] = self.select_info
            return None

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.select_info is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=fake_select),
            mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero=self.hero)),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: self.stars),
            mock.patch.object(pipeline, "read_players", side_effect=lambda img, hero, **kw: self.players),
            mock.patch.object(pipeline, "read_chip_panel", side_effect=lambda img, db, **kw: self.panel),
            mock.patch.object(pipeline, "task_panel_visible", side_effect=lambda img: self.tasks_open),
            mock.patch.object(pipeline, "read_pregame", side_effect=lambda img, db, width, catalog: self.pregame),
            mock.patch.object(pipeline, "save_template", side_effect=lambda img, hero: self.saved.append(hero) or "x.png"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.img = np.zeros((1080, 1920, 3), np.uint8) + 60

    def kinds(self, events):
        return [ev.kind for ev in events]

    def test_small_window_and_fullscreen(self):
        with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=True):
            events = self.proc.process(np.zeros((540, 960, 3), np.uint8) + 60, 0.0)
        self.assertIn("status", self.kinds(events))
        texts = self.session.unsupported
        self.assertEqual(len(texts), 2)
        self.assertIn("独占全屏", texts[0])
        self.assertIn("960×540 像素", texts[1])
        with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False):
            self.proc.process(self.img, 10.0)
        self.assertEqual(self.session.unsupported, [])

    def test_small_window_judged_by_ui_scale(self):
        """比 16:9 宽的窗口按高度算界面缩放。1280×528（21:9）宽度够 1000，界面却比 960×540 还小，要提示；
        2560×1057（实机 21:9 最大化）不提示。"""
        with mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False):
            self.proc.process(np.zeros((528, 1280, 3), np.uint8) + 60, 0.0)
            self.assertEqual(len(self.session.unsupported), 1)
            self.assertIn("1280×528 像素", self.session.unsupported[0])
            self.proc.process(np.zeros((1057, 2560, 3), np.uint8) + 60, 1.0)
            self.assertEqual(self.session.unsupported, [])

    def test_task_panel_read_at_once(self):
        """任务栏一打开就读（不等 hud_ms），读到地图发 map_read。"""
        self.round = 2
        self.proc.settle(self.img, 0.0)
        self.assertEqual(self.hud_calls, [True])  # 地图未知：连任务栏一起读
        self.proc.settle(self.img, 1.0)
        self.assertEqual(len(self.hud_calls), 1)  # hud_ms 还没到
        self.tasks_open = True
        with mock.patch.object(pipeline, "read_hud", return_value=Hud(
                round=2, map="异变图书馆", map_confidence="high", map_candidates=["异变图书馆"], tasks_seen=["击倒看守"])):
            events = self.proc.settle(self.img, 1.5)
        self.assertIn("map_read", self.kinds(events))
        self.assertEqual(self.session.settings.map, "异变图书馆")

    def test_panel_prompt_and_reading(self):
        self.round = 3
        events = self.proc.settle(self.img, 0.0)
        self.assertIn("panel_prompt", self.kinds(events))
        self.panel = SimpleNamespace(is_panel=True, self_tab=True, owned=["书签"], unknown_slots=0, confidence=0.9)
        events = self.proc.settle(self.img, 1.0)  # 已有筹码未知时每帧都看面板
        self.assertIn("panel_read", self.kinds(events))
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertTrue(self.proc.panel_visible)

    def test_teammates(self):
        from advisor.vision.stars import Player, Players

        self.round, self.hero = 3, "凛"
        ids = {name: self.db.heroes[name].id for name in ("阿兰娜", "恋", "凛")}
        self.players = Players([
            Player(0, True, ids["阿兰娜"], ids["阿兰娜"], 0.8, 0.5, "【Bot9】", True),
            Player(1, True, None, None, 0.3, 0.0, "【Bot1】", True),
            Player(2, True, ids["恋"], ids["恋"], 0.7, 0.4, "【Bot10】", True),
            Player(3, True, ids["凛"], ids["凛"], 0.8, 0.5, "玩家", False, own=True),
        ], own_row=3)
        for t in (0.0, 4.0):
            self.proc.settle(self.img, t)
        self.assertEqual([t["hero"] for t in self.session.game.teammates], ["阿兰娜", None, "恋"])
        self.assertTrue(all(t["bot"] for t in self.session.game.teammates))
        # 自己那一行和名字记下来（按名字找自己时用）
        self.assertEqual((self.session.game.own_row, self.session.player_name), (3, "玩家"))

    def test_own_row_remembered_for_panel(self):
        """读星数时按头像认出自己那一行，本局记住；之后读「查看筹码」面板时作为 self_row 传过去
        （头像认不出时沿用），记住的名字作为 own_name。新的一局清空行号。"""
        self.round, self.hero = 3, "凛"
        self.stars = SimpleNamespace(stars=1, confidence=0.9, row=3, how="头像")
        self.panel = SimpleNamespace(is_panel=True, self_tab=True, owned=["书签"], unknown_slots=0, confidence=0.9,
                                     self_index=3, self_how="本局已确认的行")
        self.session.player_name = "玩家"
        calls = []
        with mock.patch.object(pipeline, "read_chip_panel", side_effect=lambda img, db, **kw: calls.append(kw) or self.panel):
            self.proc.settle(self.img, 0.0)  # 面板在读星数之前读：这时还不知道自己那一行
            self.proc.settle(self.img, 1.0)
        self.assertIsNone(calls[0]["self_row"])
        self.assertEqual((calls[-1]["self_row"], calls[-1]["own_name"]), (3, "玩家"))
        self.assertEqual(self.session.game.own_row, 3)
        self.session.new_game()
        self.assertIsNone(self.session.game.own_row)

    def test_panel_teaches_own_row(self):
        """面板按名字认出了自己的页签：本局记住这一行，只给之后读面板用；读星数、读队友不用它
        （选转账对象的画面上玩家列表去掉了自己那一行，按记住的行读到了队友的星数，误判成新的一局）。"""
        self.round, self.hero = 3, "凛"
        self.panel = SimpleNamespace(is_panel=True, self_tab=True, owned=["书签"], unknown_slots=0, confidence=0.9,
                                     self_index=1, self_how="玩家列表（名字）")
        calls = []
        with mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: calls.append(kw) or None):
            self.proc.settle(self.img, 0.0)
            self.proc.settle(self.img, 4.0)
        self.assertEqual(self.session.game.own_row, 1)
        self.assertTrue(calls)
        self.assertTrue(all(kw == {} for kw in calls))

    def test_template_saved_when_portrait_visible(self):
        """手动指定角色、立绘未识别：左下角能看到「查看筹码」按钮的对局画面上存模板。"""
        from advisor.vision import io
        from advisor.vision.chip_select import Line

        self.round = 3
        self.session.set_hero("凛")
        with mock.patch.object(io, "ocr", return_value=[]):  # 左下角被挡住
            self.proc.settle(self.img, 0.0)
        self.assertEqual(self.saved, [])
        with mock.patch.object(io, "ocr", return_value=[Line("查看筹码", 300, 1050, 160, 40)]):
            self.proc.settle(self.img, 4.0)
        self.assertEqual(self.saved, ["凛"])
        self.assertIsNone(self.session.game.template_wanted)

    def test_mutators_out_of_game(self):
        self.pregame = Pregame(None, ["狂风", "暴雨"])
        self.proc.settle(self.img, 0.0)
        self.assertEqual(self.session.game.mutators, ["狂风", "暴雨"])

    def test_language_hint(self):
        self.select_info = True  # 像三选一，但读不到简体或繁体的标题
        self.proc.process(self.img, 0.0, force=True)
        self.assertEqual(self.session.unsupported, [])
        self.proc.process(self.img + 1, 1.0, force=True)
        self.assertIn("简体中文", " ".join(self.session.unsupported))
        self.select_info = False  # 读到了简体或繁体的标题（假的识别仍返回 None）
        self.proc.process(self.img + 2, 2.0, force=True)
        self.assertEqual(self.session.unsupported, [])


def offer(names, rerolls=2, probabilities=(60, 37, 3)):
    """三选一的识别结果（advisor.vision.chip_select 的样子）。"""
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=probabilities,
                           selected=None, problems=[])


class OutOfGameFlowTest(unittest.TestCase):
    """对局外的画面（判断对局已结束的管线部分）：选角界面判断那次的下方区域认出结算画面，离开对局后的整幅识别（read_pregame）
    认出房间、「匹配成功」画面、奖励画面，读数交给 Session.on_out_of_game；上一局结束后第一次出现三选一时，
    这一帧读到的轮次、角色和星数用进这次推荐；本局已结束时不读星数；[map_detect] 各开关关掉后整幅识别做不做。
    停在主菜单（不在对局里、最近认出的是主菜单）时底栏和开始按钮那一块没变就不认字（主菜单空转），背景动画、横幅轮播不算；
    这两块变了（选角界面、房间、回到对局等）、窗口大小变了、从暂停恢复、按热键时先看一眼下方区域、接着整幅识别，空转满一分钟
    照常认一次；对局还没结束时认出的主菜单不空转；两开时让路照旧；两块里有一块自己在动时防来回进出（只看底栏、暂停空转）。
    识别函数全部换成假的。"""

    RESULT_SCREEN = OutOfGame(RESULT, "噩梦", "异变图书馆", 12)

    def setUp(self):
        self.db = load_db()
        self.seen, self.round, self.hero, self.stars = None, None, "凛", None
        self.picking, self.bottom, self.pregame = False, None, Pregame()  # 选角界面、下方区域和整幅识别的读数
        self.star_calls, self.pregame_at = 0, []  # 读星数的次数、每次整幅识别的时刻
        self.pick_at = []  # 每次选角界面判断（下方区域的识别）的时刻
        # 认字的三项按先后记下（哪一项, 时刻）：hud 读轮次、pick 下方区域（选角界面判断）、pregame 整幅识别
        self.ocr = []

        def fake_hud(img, db, full, **kw):
            self.ocr.append(("hud", self.clock[0]))
            return Hud(round=self.round)

        def fake_pick(img, db, info=None):
            self.pick_at.append(self.clock[0])
            self.ocr.append(("pick", self.clock[0]))
            if info is not None:
                info["screen"] = None if self.picking else self.bottom
            return self.picking

        def fake_stars(img, hero, **kw):
            self.star_calls += 1
            return self.stars

        def fake_pregame(img, db, width, catalog):
            self.pregame_at.append(self.clock[0])
            self.ocr.append(("pregame", self.clock[0]))
            return self.pregame

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", side_effect=fake_pick),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero=self.hero)),
            mock.patch.object(pipeline, "read_own_stars", side_effect=fake_stars),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "read_pregame", side_effect=fake_pregame),
            # 没有畸变组件清单：对局外的画面照样要认（结算画面没截到时，房间等也能结束这一局）
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.n = 0
        self.make()

    def make(self, detect: dict | None = None, **settings) -> None:
        """重新建 Session 和 FrameProcessor。detect：改写 rules.toml [map_detect] 的几项（开关等）。"""
        self.clock = [0.0]
        db = self.db
        if detect:
            db = dataclasses.replace(db, rules={**db.rules, "map_detect": {**db.rules.get("map_detect", {}), **detect}})
        self.session = Session(db, Settings(hero="凛", **settings))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(db, self.session)

    def step(self, t: float, force: bool = False) -> list:
        """处理 t 秒处的一帧，到期的读数都做完（settle；force 为按热键的那一帧）。每帧的亮度都和前 7 帧不同：整幅识别前的
        缩略图检查、选角界面判断用的指纹都看得出画面变了。"""
        self.clock[0] = t
        self.n += 1
        return self.proc.settle(np.full((1080, 1920, 3), 10 + 30 * (self.n % 8), np.uint8), t, force=force)

    def playing(self) -> None:
        """一局打到第 12 轮、3 星，认出角色凛（第 0、4、8 秒各读一次轮次）。"""
        self.round, self.stars = 12, SimpleNamespace(stars=3, confidence=0.9)
        for t in (0.0, 4.0, 8.0):
            self.step(t)
        self.assertEqual((self.session.settings.round, self.session.game.seen_hero), (12, "凛"))

    def finish(self, t: float, kind: str = "banner") -> list:
        """t 秒处一帧打完时的画面：棋盘上那条蓝色斜带（打赢、打输都有，kind 为 banner）或通关的整屏红底（coral）。只换颜色预筛的
        结果（vision.victory.glance），认字一律认不出（这里不看胜负）。实机上结算画面紧跟在它后面出来，看见以后 pick_grace_ms
        以内读不到轮次时照常判选角界面（FrameProcessor._ending_seen_at）。"""
        seen = pipeline.victory.Glance(0.9 if kind == "coral" else 0.0, 0.0, banner=kind == "banner")
        with mock.patch.object(pipeline.victory, "glance", return_value=seen), \
                mock.patch.object(pipeline.victory, "read", return_value=False), \
                mock.patch.object(pipeline.victory, "read_game_over", return_value=False):
            return self.step(t)

    @staticmethod
    def whys(events, kind: str) -> list:
        return [e.why for e in events if e.kind == kind]

    def test_result_on_bottom_area(self):
        """下方区域认出结算画面的底栏：本局结束；这一帧不再读星数；马上开始离开对局后的整幅识别（不等 after_game_ms）。"""
        self.playing()
        self.round, self.bottom = None, self.RESULT_SCREEN
        stars = self.star_calls
        events = self.step(12.0)
        self.assertEqual(self.whys(events, "game_over"), ["检测到结算画面（异变图书馆、噩梦、12 轮）"])
        self.assertTrue(self.session.game.game_over)
        self.assertEqual(self.session.difficulty_info.level, LEVEL_NIGHTMARE)
        self.assertEqual(self.proc._mark_seen_at, float("-inf"))
        self.assertEqual(self.star_calls, stars)
        self.assertEqual(self.pregame_at, [12.0])
        rec = [r for r in self.session.recent_events(60) if r["kind"] == "read:screen"]
        self.assertEqual([(r["screen"], r["difficulty"], r["map"], r["rounds"], r["full"]) for r in rec],
                         [("result", "噩梦", "异变图书馆", 12, False)])
        events = self.step(15.0)  # 结算画面停留期间：不再发
        self.assertEqual(self.whys(events, "game_over"), [])
        self.assertEqual((self.session.settings.round, self.session.settings.star), (12, 3))  # 这一局的状态保留

    def test_between_games_every_loading_ms(self):
        """两局之间（本局已结束）：上一局的地图已知也按 loading_ms 看（Session.wants_loading 在结束后为真，这时找到的
        加载画面地图属于下一局）；对局状态还在、地图已知时只按 mutators_ms 看（见 test_room_ends_game_without_catalog）。
        奖励画面不再结束一次。"""
        self.make(map="异变图书馆", map_source="tasks", map_confidence="high")
        self.playing()
        self.round, self.bottom = None, self.RESULT_SCREEN
        self.step(12.0)
        self.assertEqual(self.session.settings.map, "异变图书馆")  # 这一局的地图保留
        self.assertTrue(self.session.wants_loading)
        self.bottom, self.pregame = None, Pregame(screen=OutOfGame(REWARD))
        self.step(12.0 + self.proc.loading_s / 2)
        self.assertEqual(self.pregame_at, [12.0])
        events = self.step(12.0 + self.proc.loading_s)
        self.assertEqual(self.pregame_at, [12.0, 12.0 + self.proc.loading_s])
        self.assertEqual(self.whys(events, "game_over"), [])
        self.assertTrue(self.session.game.game_over)

    def test_room_ends_game_without_catalog(self):
        """对局状态还在、地图已知、没有畸变组件清单：离开对局 after_game_ms 以后照样按 mutators_ms 做整幅识别，
        隔一会儿两次读到组队房间就结束这一局（结算画面没截到时）；房间上的难度留给下一局。结束后按 loading_ms 看。"""
        self.make(map="异变图书馆", map_source="tasks", map_confidence="high")
        self.playing()
        self.round = None
        self.step(20.0)
        self.assertEqual(self.pregame_at, [])  # 最后一次读到轮次之后还不到 after_game_ms
        t0 = 8.0 + self.proc.after_game_s
        self.step(t0)  # 战斗等画面：什么都没认出
        self.step(t0 + self.proc.loading_s)
        self.assertEqual(self.pregame_at, [t0])
        m = self.proc.mutators_s
        self.pregame, self.stars = Pregame(screen=OutOfGame(TEAM_ROOM, "噩梦")), None  # 房间里没有玩家列表
        events = self.step(t0 + m)
        self.assertEqual(self.pregame_at, [t0, t0 + m])
        self.assertEqual(self.whys(events, "game_over"), [])  # 只读到一次：还不算结束
        events = self.step(t0 + 2 * m)
        self.assertEqual(self.whys(events, "game_over"), ["检测到组队房间"])
        self.assertEqual(self.session.pending._next_difficulty, {"team_room": "噩梦"})
        rec = [r for r in self.session.recent_events(60) if r["kind"] == "read:screen"]
        self.assertEqual([(r["screen"], r["full"]) for r in rec], [("team_room", True)] * 2)
        self.step(t0 + 2 * m + self.proc.loading_s)
        self.assertEqual(len(self.pregame_at), 4)

    def test_static_result_screen_no_stars(self):
        """结算画面停着不动：认出以后，同样的画面不再做下方区域的识别（_Unchanged 沿用「不是选角界面」），
        这些帧也不读星数（本局已结束，读到的星数不记进这一局）。"""
        self.playing()
        self.round, self.bottom = None, self.RESULT_SCREEN
        img = np.full((1080, 1920, 3), 200, np.uint8)  # 和 playing() 的各帧亮度不同，之后每帧都一样
        stars = self.star_calls
        for t in (12.0, 16.0, 20.0, 24.0):
            self.clock[0] = t
            self.proc.settle(img, t)
        self.assertTrue(self.session.game.game_over)
        self.assertEqual([t for t in self.pick_at if t >= 12.0], [12.0, 16.0])  # 第 20、24 秒沿用结论
        self.assertEqual(self.star_calls, stars)

    def test_switches(self):
        """[map_detect] 的 loading、mutators 都关掉：离开对局后照样按 mutators_ms 做整幅识别、认对局外的画面
        （两局之间也按 mutators_ms）。再关掉 out_of_game：离开对局后不做整幅识别，下方区域读到的结算画面也不采用，
        照旧读星数（这时只按选角界面、轮次变小等判断新的一局）。"""
        off = {"loading": False, "mutators": False}
        self.make(detect=off, map="异变图书馆", map_source="tasks", map_confidence="high")
        self.playing()
        self.round = None
        m = self.proc.mutators_s
        t0 = 8.0 + self.proc.after_game_s
        self.step(t0)
        self.step(t0 + m / 2)
        self.pregame, self.stars = Pregame(screen=OutOfGame(TEAM_ROOM, "噩梦")), None
        self.step(t0 + m)  # 第一次读到组队房间
        events = self.step(t0 + 2 * m)  # 隔 mutators_ms 再认一次：结束这一局
        self.assertEqual(self.whys(events, "game_over"), ["检测到组队房间"])
        self.step(t0 + 2 * m + self.proc.loading_s)
        self.step(t0 + 3 * m)
        self.assertEqual(self.pregame_at, [t0, t0 + m, t0 + 2 * m, t0 + 3 * m])

        self.pregame_at = []
        self.make(detect={**off, "out_of_game": False}, map="异变图书馆", map_source="tasks", map_confidence="high")
        self.playing()
        self.round = None
        for i in range(5):
            events = self.step(t0 + i * m / 2)
            self.assertEqual(self.whys(events, "game_over"), [])
        self.assertEqual(self.pregame_at, [])
        self.bottom = self.RESULT_SCREEN
        stars = self.star_calls
        events = self.step(t0 + 3 * m)
        self.assertEqual(self.whys(events, "game_over"), [])
        self.assertFalse(self.session.game.game_over)
        self.assertEqual(self.star_calls, stars + 1)
        self.assertEqual(self.pregame_at, [])

    def test_match_found_map_for_next_game(self):
        """隔一会儿两次读到「匹配成功」画面结束这一局；画面上的地图和难度留给下一局：选角界面开新的一局（选角界面只在
        开局时出现，不可撤销），第一次读到轮次时用上地图。"""
        self.playing()
        self.round = None
        t0 = 8.0 + self.proc.after_game_s
        self.pregame, self.stars = Pregame(screen=OutOfGame(MATCH_FOUND, "噩梦", "水乡古镇")), None
        self.assertEqual(self.whys(self.step(t0), "game_over"), [])  # 只读到一次：还不算结束
        events = self.step(t0 + self.proc.loading_s)
        self.assertEqual(self.whys(events, "game_over"), ["检测到「匹配成功」画面"])
        self.pregame, self.picking = Pregame(), True
        events = self.step(t0 + 4)
        self.assertEqual([(e.undo, e.cause) for e in events if e.kind == "newgame"], [(False, "select")])
        self.picking, self.round = False, 1
        events = self.step(t0 + 8)
        st, info = self.session.settings, self.session.difficulty_info
        self.assertEqual((st.round, st.map, self.session.map_source), (1, "水乡古镇", "match"))
        self.assertEqual((info.level, info.name, info.source), (LEVEL_NIGHTMARE, "噩梦", "match_found"))
        self.assertEqual(self.whys(events, "panel_prompt"), [])  # 从选角界面开始看到的一局

    def test_room_before_game(self):
        """启动时在自建房间里（还没有对局状态）：不结束什么；房间设置上的地图和难度用到之后的一局。"""
        self.pregame = Pregame(screen=OutOfGame(CUSTOM_ROOM, "困难", "星趴·梦想号"))
        events = self.step(0.0)
        self.assertEqual(self.whys(events, "game_over"), [])
        self.assertFalse(self.session.has_game_state)
        self.pregame, self.round = Pregame(), 1
        self.step(4.0)
        st, info = self.session.settings, self.session.difficulty_info
        self.assertEqual((st.map, self.session.map_source), ("星趴·梦想号", "room"))
        self.assertEqual((info.name, info.source), ("困难", "custom_room"))

    def test_first_offer_after_game_over(self):
        """上一局结束后（结算画面），切到后台期间开了下一局，回来时正停在第 7 轮的三选一上、2 星：这一帧先开新的一局，
        再读轮次、角色和星数，这次推荐就用上（回来前 1 秒刚读过一次轮次，也不等 hud_ms）；新一局排在推荐前面。"""
        self.playing()
        self.round, self.bottom = None, self.RESULT_SCREEN
        self.step(12.0)
        self.assertTrue(self.session.game.game_over)
        self.bottom = None
        self.proc._last_hud = 299.0
        self.seen = offer(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"])
        self.round, self.stars = 7, SimpleNamespace(stars=2, confidence=0.9)
        events = self.step(300.0)
        kinds = [e.kind for e in events]
        self.assertLess(kinds.index("newgame"), kinds.index("advice"))
        ev = events[kinds.index("newgame")]
        self.assertEqual((ev.undo, ev.cause, ev.why), (False, "offer", "上一局已结束，检测到筹码三选一界面"))
        self.assertEqual([(e.state.round, e.state.star, e.state.hero, e.state.owned) for e in events if e.kind == "advice"],
                         [(7, 2, "凛", ())])
        self.assertIn("panel_prompt", kinds)  # 先看到三选一的一局：开局后可能已经拿过筹码
        self.assertFalse(self.session.game.game_over)
        self.assertEqual(self.session.games, 1)
        events = self.step(300.3)  # 同一次三选一的下一帧：不再开新的一局
        self.assertNotIn("newgame", [e.kind for e in events])

    def test_main_menu_ends_game(self):
        """对局中途退出、回到主菜单：读不到轮次时下方区域认出主菜单，隔一个 hud_ms 又认出一次就结束这一局，依据「检测到
        主菜单」；只认出一次就回到了对局画面的那一次作废。这一局的状态保留。每次都是看见轮次标记以后第一次读不到、或刚认出
        主菜单（看见标记的时刻已清掉），不先不判。"""
        menu = OutOfGame("main_menu")
        self.playing()
        self.round, self.bottom = None, menu
        self.assertEqual(self.whys(self.step(12.0), "game_over"), [])  # 只认出一次：还不算结束
        self.round, self.bottom = 12, None
        self.step(15.0)  # 回到了对局画面
        self.round, self.bottom = None, menu
        self.assertEqual(self.whys(self.step(18.0), "game_over"), [])
        events = self.step(18.0 + self.proc.hud_s)
        self.assertEqual(self.whys(events, "game_over"), ["检测到主菜单"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [12.0, 18.0, 18.0 + self.proc.hud_s])
        self.assertTrue(self.session.game.game_over)
        self.assertEqual((self.session.settings.round, self.session.settings.star), (12, 3))
        rec = [r for r in self.session.recent_events(60) if r["kind"] == "read:screen"]
        self.assertEqual([(r["screen"], r["full"]) for r in rec], [("main_menu", False)] * 3)

    def test_rejoin_rereads_hero(self):
        """主菜单结束这一局以后又连回了同一局（status 的 cause 为 rejoined）：和开新的一局一样马上再认一次角色，不等
        hero_ms（这里改成 100 秒，不马上认的话第 21 秒不会认）；认回这一局的角色，就不再算刚连回来。"""
        menu = OutOfGame("main_menu")
        reads = []

        def hero(img, db):
            reads.append(self.clock[0])
            return SimpleNamespace(hero=self.hero)

        with mock.patch.object(pipeline, "read_hero", side_effect=hero), \
                mock.patch.object(self.proc, "hero_s", 100.0):
            self.playing()
            self.assertEqual(reads, [0.0])
            self.round, self.bottom = None, menu
            self.step(12.0)
            self.assertEqual(self.whys(self.step(15.0), "game_over"), ["检测到主菜单"])
            self.round, self.bottom = 12, None
            self.step(18.0)
            events = self.step(21.0)
        self.assertEqual([e.cause for e in events if e.kind == "status" and e.cause], ["rejoined"])
        self.assertFalse(self.session.game.game_over)
        self.assertEqual(reads, [0.0, 21.0])
        self.assertFalse(self.session.game._rejoined)

    # ---- 对局中看见轮次标记以后第二次起读不到时先不判选角界面（pick_grace_ms，见 FrameProcessor._pick_grace）

    def grace_run(self, times, force_at=()) -> list:
        """playing() 以后在 times 这几秒各一帧，读不到轮次、也没有标记（战斗等）；force_at 里的那几秒是按热键的那一帧。返回这几帧
        里先不判的次数（perfstats 的 pick_grace）。"""
        counted = []
        self.round, self.bottom = None, None
        # 这里只看先不判选角界面的计数；读轮次那边的 round_no_retry、hud_full_wait 不在这一组的核对范围里
        with mock.patch.object(pipeline.perfstats, "count",
                               side_effect=lambda name: counted.append(name) if name.startswith("pick_") else None):
            for t in times:
                self.step(t, force=t in force_at)
        return counted

    def test_pick_grace_skips_after_mark(self):
        """最后一次读到轮次（第 8 秒）以后第一次读不到（第 11 秒）照常认下方区域；之后 pick_grace_ms（10 秒）以内读不到轮次时
        不认，照旧读星数（和认了、不是选角界面时一样），每次数一笔 pick_grace；第 20 秒（离第 8 秒 12 秒）照常认。"""
        self.assertEqual(self.proc.pick_grace_s, 10.0)
        self.playing()
        stars = self.star_calls
        counted = self.grace_run((11.0, 14.0, 17.0))
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0])
        self.assertEqual(self.star_calls - stars, 3)
        self.assertEqual(counted, ["pick_check_first", "pick_grace", "pick_grace"])
        self.assertEqual(self.grace_run((20.0,)), ["pick_check_old"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 20.0])

    def test_pick_grace_first_miss_each_mark(self):
        """每看见一次轮次标记，之后第一次读不到都照认：第 11 秒照认、第 14 秒先不认；连标记都没看见时每 blank_full_s（6 秒）
        才完整读一次，第 20 秒又读到轮次，第 23 秒（这次标记以后第一次读不到）照认，第 26、29 秒先不认，第 32 秒（离第 20 秒
        12 秒）照认。"""
        self.playing()
        counted = self.grace_run((11.0, 14.0))
        self.round = 12
        self.step(20.0)
        counted += self.grace_run((23.0, 26.0, 29.0, 32.0))
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 23.0, 32.0])
        self.assertEqual(counted, ["pick_check_first", "pick_grace", "pick_check_first", "pick_grace", "pick_grace",
                                   "pick_check_old"])

    def test_pick_grace_same_reads_as_not_pick(self):
        """先不判的那一次和原来认了下方区域、不是选角界面的那一次，这一轮排的读数一样（只读星数），读到的状态也一样。"""
        def run(grace: float):
            self.make()
            self.proc.pick_grace_s = grace
            self.playing()
            stars = self.star_calls
            self.grace_run((11.0, 14.0))  # 第 14 秒：时限为 0 时认，为 10 秒时先不判
            st = self.session.settings
            return self.star_calls - stars, (st.round, st.star), self.session.game.game_over

        old, new = run(0.0), run(10.0)
        self.assertEqual(old, new)
        self.assertEqual(old[0], 2)

    def test_pick_grace_hotkey(self):
        """按热键的那一帧照判（这一轮不沿用任何结论）：离上次读到轮次只有 6 秒（第 14 秒）也认下方区域；下一次不按热键的又
        先不判。按热键的那一次不算看见标记以后的「第一次」：第一次读不到时就按了热键，下一次自动读不到还照认一次。"""
        self.playing()
        counted = self.grace_run((11.0, 14.0, 17.0), force_at=(14.0,))
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 14.0])
        self.assertEqual(counted, ["pick_check_first", "pick_check_force", "pick_grace"])
        self.make()
        self.pick_at.clear()
        self.playing()
        counted = self.grace_run((11.0, 14.0, 17.0), force_at=(11.0,))
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 14.0])
        self.assertEqual(counted, ["pick_check_force", "pick_check_first", "pick_grace"])

    def test_pick_grace_zero(self):
        """pick_grace_ms 改成 0：和原来一样，读不到轮次就认下方区域；看见打完时的颜色也不记识别记录 read:finish_look（「离上次
        看见隔了 pick_grace_ms 以上」每帧都满足，记的话颜色像的每一帧都记一条）。"""
        self.proc.pick_grace_s = 0.0
        self.playing()
        self.assertEqual(self.grace_run((11.0, 14.0)), ["pick_check_old", "pick_check_old"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 14.0])
        for t in (15.0, 15.2, 15.4):
            self.finish(t)
        self.assertEqual([r for r in self.session.recent_events(60) if r["kind"] == "read:finish_look"], [])

    def test_pick_grace_lowend(self):
        """低配模式（倍数 2）：读轮次隔 6 秒，时限也乘成 20 秒：第一次读不到（第 14 秒）照认，之后先不判的还是两次（和不开低配
        时一样）；第 32 秒（离第 8 秒 24 秒）才认。"""
        self.playing()
        self.proc.set_pace(2)
        self.assertEqual((self.proc.hud_s, self.proc.pick_grace_s), (6.0, 20.0))
        self.assertEqual(self.grace_run((14.0, 20.0, 26.0, 32.0)),
                         ["pick_check_first", "pick_grace", "pick_grace", "pick_check_old"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [14.0, 32.0])

    def test_pick_grace_after_resume(self):
        """刚从暂停恢复（resumed）、还没读到轮次时照认：暂停期间可能早就离开了对局。读到轮次以后回到原样：这次标记以后第一次
        读不到照认，第二次起先不判。"""
        self.playing()
        self.assertEqual(self.grace_run((11.0,)), ["pick_check_first"])
        self.proc.resumed()
        self.assertEqual(self.grace_run((14.0, 17.0)),  # 离第 8 秒不到 10 秒，不是刚恢复的话这两次先不判
                         ["pick_check_stale", "pick_check_stale"])
        self.round = 12
        self.step(20.0)
        self.assertEqual(self.grace_run((23.0, 26.0)), ["pick_check_first", "pick_grace"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 14.0, 17.0, 23.0])

    def test_pick_grace_ends_with_banner(self):
        """看见打完时的颜色（蓝色斜带、通关的整屏红底）以后 pick_grace_ms 以内，读不到轮次时照常认下方区域：第一次读不到没落在
        结算画面上、结算画面后面才出来时也马上认出、结束这一局（不认的话要晚到离最后一次读到轮次满 pick_grace_ms）。下一局
        （选角界面开的）离看见颜色满 pick_grace_ms 以后又照常先不判。"""
        for kind in ("banner", "coral"):
            with self.subTest(kind):
                self.make()
                self.pick_at.clear()
                self.playing()
                self.assertEqual(self.grace_run((11.0, 14.0)), ["pick_check_first", "pick_grace"])
                self.finish(15.0, kind)
                self.assertEqual(self.proc._ending_seen_at, 15.0)
                self.bottom = self.RESULT_SCREEN
                with mock.patch.object(pipeline.perfstats, "count") as count:
                    events = self.step(17.0)
                names = [c.args[0] for c in count.call_args_list if c.args[0].startswith("pick_")]
                self.assertTrue(names)
                self.assertEqual(set(names), {"pick_check_ending"})  # 刚看见像打完时的颜色：照判
                self.assertEqual(self.whys(events, "game_over"), ["检测到结算画面（异变图书馆、噩梦、12 轮）"])
                self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 17.0])
                # 下一局：选角界面开新的一局，读到第 1 轮以后第一次读不到照认，第二次起又先不判
                self.bottom, self.picking = None, True
                events = self.step(19.0)
                self.assertEqual([(e.undo, e.cause) for e in events if e.kind == "newgame"], [(False, "select")])
                self.picking, self.round = False, 1
                self.step(23.0)
                picks = len(self.pick_at)
                # 第 29 秒离看见颜色 14 秒
                self.assertEqual(self.grace_run((26.0, 29.0)), ["pick_check_first", "pick_grace"])
                self.assertEqual(len(self.pick_at), picks + 1)

    def test_pick_grace_back_after_stray_color(self):
        """对局中途看见一次像打完时的颜色、其实没打完（又读到了轮次，这一局接着打）：看见以后 pick_grace_ms 以内照常认下方区域，
        过了这么久又照常先不判，不是这一局剩下的时间都照判。第一次看见时记一条识别记录 read:finish_look（哪种、占比），
        pick_grace_ms 以内接着看见的不再记。"""
        for kind in ("banner", "coral"):
            with self.subTest(kind):
                self.make()
                self.pick_at.clear()
                self.playing()
                self.finish(9.0, kind)
                self.finish(9.5, kind)
                self.round = 12
                self.step(11.0)  # 回到对局画面，读到轮次
                # 第 14 秒是这次标记以后第一次读不到，照认；第 17 秒离看见颜色（第 9.5 秒）不到 10 秒，照认；第 20 秒满了，先不判
                counted = self.grace_run((14.0, 17.0, 20.0))
                self.assertEqual([t for t in self.pick_at if t > 11.0], [14.0, 17.0])
                self.assertEqual(counted, ["pick_check_first", "pick_check_ending", "pick_grace"])
                looks = [(r["look"], r["coral"]) for r in self.session.recent_events(60) if r["kind"] == "read:finish_look"]
                self.assertEqual(looks, [(kind, 0.9 if kind == "coral" else 0.0)])

    def test_pick_grace_not_after_left_game(self):
        """第一次读不到落在战斗等画面上（照认，什么都不是），之后才回到主菜单：离最后一次读到轮次（第 8 秒）不到 pick_grace_ms 的
        第 14、17 秒先不认，第 20 秒才第一次认出主菜单（最多晚到离那次标记满 pick_grace_ms 以后的第一次读轮次）；认出以后看见
        标记的时刻清掉，隔一个 hud_ms 照常再认一次、结束这一局，不再先不判。"""
        self.playing()
        self.grace_run((11.0,))
        self.bottom = OutOfGame("main_menu")
        for t in (14.0, 17.0):
            self.assertEqual(self.whys(self.step(t), "game_over"), [])
        self.assertEqual(self.whys(self.step(20.0), "game_over"), [])
        with mock.patch.object(pipeline.perfstats, "count") as count:
            events = self.step(23.0)
        names = [c.args[0] for c in count.call_args_list if c.args[0].startswith("pick_")]
        self.assertTrue(names)
        self.assertEqual(set(names), {"pick_check_no_mark"})  # 离开对局时把标记清掉了：照判
        self.assertEqual(self.whys(events, "game_over"), ["检测到主菜单"])
        self.assertEqual([t for t in self.pick_at if t > 8.0], [11.0, 20.0, 23.0])

    def test_pick_grace_counts_each_reason(self):
        """照判、先不判各按命中的那一条数一次（perfstats 的 count）：按热键、刚从暂停恢复、还没看见标记、被挡住很久、
        还没有对局状态、这次标记以后第一次读不到、刚看见像打完时的颜色，以及先不判的 pick_grace。"""
        def counted(*args) -> tuple:
            got: list[str] = []
            with mock.patch.object(pipeline.perfstats, "count", side_effect=got.append):
                result = self.proc._pick_grace(*args)
            return result, got

        self.playing()
        self.assertEqual(counted(9.0, True), (False, ["pick_check_force"]))
        self.proc._stale = True
        self.assertEqual(counted(9.0, False), (False, ["pick_check_stale"]))
        self.proc._stale = False
        self.assertEqual(counted(9.0, False), (False, ["pick_check_first"]))
        self.proc._ending_seen_at = 5.0
        self.assertEqual(counted(9.0, False), (False, ["pick_check_ending"]))
        self.proc._ending_seen_at = float("-inf")
        self.assertEqual(counted(9.0, False), (True, ["pick_grace"]))
        self.make()
        self.assertEqual(counted(5.0, False), (False, ["pick_check_no_mark"]))
        self.proc._mark_seen_at = 1.0
        self.assertEqual(counted(12.0, False), (False, ["pick_check_old"]))
        self.proc._mark_seen_at = 4.0
        self.assertEqual(counted(5.0, False), (False, ["pick_check_no_game"]))

    # ---- 对局中途像一局打完的那一帧（FrameProcessor._check_victory、_note_finish_look、finish_shot）

    def look(self, t: float, kind: str, img: np.ndarray | None = None) -> None:
        """t 秒处一帧颜色像打完时的画面（只换颜色预筛的结果，认字一律认不出），交给 _check_victory。"""
        seen = pipeline.victory.Glance(0.9 if kind == "coral" else 0.0, 0.25, banner=kind == "banner")
        img = np.full((1080, 1920, 3), 30, np.uint8) if img is None else img
        with mock.patch.object(pipeline.victory, "glance", return_value=seen), \
                mock.patch.object(pipeline.victory, "read", return_value=False), \
                mock.patch.object(pipeline.victory, "read_game_over", return_value=False):
            self.proc._check_victory(pipeline.as_frame(img), t, [])

    def test_finish_stray_color_kept_and_resized(self):
        """对局中途看见像打完时的颜色：留下待转正的一帧，长边缩到 1280；情况里是当时的读数、轮次和原图尺寸。"""
        self.playing()
        self.look(9.0, "coral", np.full((2160, 3840, 3), 40, np.uint8))
        pending = self.proc._finish_pending
        self.assertIsNotNone(pending)
        _, image, details, at, games = pending
        self.assertEqual((at, games), (9.0, self.session.games))
        self.assertEqual(image.shape, (720, 1280, 3))
        self.assertEqual(details, {"look": "coral", "coral": 0.9, "white": 0.25, "round": 12, "size": [3840, 2160]})
        self.assertIsNone(self.proc.finish_shot)

    def test_finish_stray_color_promoted_after_mark(self):
        """颜色之后这一局又读到轮次、满了 pick_grace_s：转正成 finish_shot。"""
        self.playing()
        self.look(9.0, "banner")
        self.round = 12
        self.step(11.0)  # 又读到轮次：这一局接着打下去
        self.look(19.0, None)  # 离留下满 10 秒：转正
        shot = self.proc.finish_shot
        self.assertIsNotNone(shot)
        _, image, details = shot
        self.assertEqual((image.shape, details["look"], details["round"]), ((720, 1280, 3), "banner", 12))
        self.assertIsNone(self.proc._finish_pending)

    def test_finish_stray_color_dropped_when_game_over_or_left(self):
        """本局结束、离开对局时丢掉待转正的那一张，不转正。"""
        self.playing()
        self.look(9.0, "coral")
        self.assertIsNotNone(self.proc._finish_pending)
        self.session.game.game_over = True
        self.proc.hints = None  # 读的提示是上一次进锁时取的：改过记账的状态要重新取一份
        self.look(19.0, None)
        self.assertIsNone(self.proc._finish_pending)
        self.assertIsNone(self.proc.finish_shot)
        self.session.game.game_over = False
        self.proc.hints = None
        self.look(21.0, "banner")
        self.assertIsNotNone(self.proc._finish_pending)
        self.proc._left_game()
        self.assertIsNone(self.proc._finish_pending)

    def test_finish_stray_color_not_promoted_into_next_game(self):
        """像打完时的颜色其实是这一局真的打完了，之后的结算画面、选角界面都没看到（游戏切到后台、窗口最小化期间打完又开了
        一局），回来先读到的是新一局的第 1 轮：第一次读到时记账还在等再读一次，先不转正；连读两次判了新的一局，丢掉那一帧。"""
        self.playing()
        self.look(9.0, "coral")
        self.assertIsNotNone(self.proc._finish_pending)
        self.round, self.stars = 1, None  # 一分钟以后回来：新一局的第 1 轮，星数还没读到
        self.step(60.0)  # 离留下满 10 秒，也看见了标记；读到的轮次变小，记账在等再读一次
        self.assertTrue(self.session.round_drop_pending)
        self.assertIsNone(self.proc.finish_shot)
        self.assertIsNotNone(self.proc._finish_pending)
        games = self.session.games
        events = self.step(63.0)
        self.assertEqual([e.kind for e in events if e.kind == "newgame"], ["newgame"])
        self.assertEqual(self.session.games, games + 1)
        self.assertIsNone(self.proc.finish_shot)
        self.assertIsNone(self.proc._finish_pending)
        self.step(66.0)
        self.assertIsNone(self.proc.finish_shot)

    def test_finish_stray_color_dropped_after_new_game(self):
        """留下以后判了新的一局（不论经哪一路判的）：那一帧是上一局的，丢掉、不转正。"""
        self.playing()
        self.look(9.0, "banner")
        self.session.new_game()
        self.session.settings.round = 1  # 新的一局读到了轮次：有对局状态
        self.proc.hints = None  # 读的提示是上一次进锁时取的：改过记账的状态要重新取一份
        self.proc._mark_seen_at = 20.0
        self.look(21.0, None)
        self.assertIsNone(self.proc.finish_shot)
        self.assertIsNone(self.proc._finish_pending)

    def test_finish_stray_color_waits_for_round_drop(self):
        """记账还在等再读一次变小的轮次时先不转正；那一次是读错（读回手上的轮次）时照常转正。"""
        self.playing()
        self.look(9.0, "coral")
        self.round = 3
        self.step(20.0)  # 读错成第 3 轮：记账在等再读一次
        self.assertTrue(self.session.round_drop_pending)
        self.assertIsNone(self.proc.finish_shot)
        self.round = 12
        self.step(23.0)  # 读回第 12 轮：不是新的一局
        self.assertFalse(self.session.round_drop_pending)
        shot = self.proc.finish_shot
        self.assertIsNotNone(shot)
        self.assertEqual(shot[2]["look"], "coral")
        self.assertIsNone(self.proc._finish_pending)

    def test_finish_stray_color_not_kept_when_grace_zero(self):
        """pick_grace_s 为 0（先不判关着）时不留。"""
        self.proc.pick_grace_s = 0.0
        self.playing()
        self.look(9.0, "coral")
        self.assertIsNone(self.proc._finish_pending)

    def test_finish_stray_color_copy_error_ignored(self):
        """复制、缩小出错时这一次不留，之前留的照旧，不算识别出错。"""
        self.playing()
        self.look(9.0, "coral")
        before = self.proc._finish_pending
        errors = list(self.proc.errors)
        with mock.patch.object(pipeline, "_copy_frame", side_effect=RuntimeError("没有内存")):
            self.look(19.0, "coral", np.full((540, 960, 3), 40, np.uint8))  # 长边不超过 1280：走复制
        self.assertIs(self.proc._finish_pending, before)
        self.assertEqual(self.proc.errors, errors)

    # ---- 不在对局里时（还没有对局状态、本局已结束）每 select_ms 另外看一眼下方区域：选角界面只显示几秒

    def steps(self, start: float, end: float, dt: float = 0.5) -> None:
        """从 start 到 end（含）每 dt 秒一帧（比 select_ms 短）。"""
        t = start
        while t <= end + 1e-9:
            self.step(t)
            t += dt

    def test_glance_outside(self):
        """还没有对局状态（刚打开时在大厅）：除了每 hud_ms 读轮次（读不到时顺带认一次下方区域），另外每 select_ms 看一眼；
        这一眼不顺带读星数。"""
        self.assertEqual((self.proc.hud_s, self.proc.select_s), (3.0, 1.0))
        self.step(0.0)
        stars = self.star_calls
        self.steps(0.5, 2.5)
        self.assertEqual(self.star_calls, stars)
        self.steps(3.0, 6.0)
        self.assertEqual(self.pick_at, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_glance_catches_short_select_screen(self):
        """刚打开时在大厅，选角界面只显示了两秒（第 3.5～5.5 秒，正好落在两次读轮次之间）：看的那一眼认出它，这一局算从开局
        看起，读到第 1 轮时不请用户打开「查看筹码」面板。不看那一眼的话（select_ms 无限长）两次读轮次都错过它。"""
        def run():
            self.steps(0.0, 3.0)
            self.picking = True
            self.steps(3.5, 5.0)
            self.picking = False
            return [r for r in self.session.recent_events(60) if r["kind"] == "read:hero_select"]

        self.assertEqual(len(run()), 1)
        self.assertIn(4.0, self.pick_at)
        self.assertTrue(self.session.game.whole_game)
        self.round = 1
        self.assertEqual([e.kind for e in self.step(6.0) if e.kind == "panel_prompt"], [])
        self.assertFalse(self.session.game.panel_needed)

        self.make()
        self.picking, self.round = False, None
        self.proc.select_s = float("inf")
        self.assertEqual(run(), [])

    def test_glance_after_game_over(self):
        """结算画面结束这一局以后也每 select_ms 看一眼：下一局的选角界面只显示了两秒（落在两次读轮次之间）照样认得出，
        开新的一局（不可撤销）。"""
        self.playing()
        self.round, self.bottom = None, self.RESULT_SCREEN
        self.step(12.0)
        self.assertTrue(self.session.game.game_over)
        self.bottom = None
        self.steps(12.5, 13.0)
        self.picking = True
        found = []
        for t in (13.5, 14.0, 14.5):
            found += [(e.undo, e.cause) for e in self.step(t) if e.kind == "newgame"]
        self.assertEqual(found, [(False, "select")])
        self.assertEqual([t for t in self.pick_at if t > 12.0], [13.0, 14.0])

    def test_glance_static_screen(self):
        """不在对局里、画面停着不动（大厅）：下方区域和上次认定「不是」时一样就不认字，最多隔 unchanged_max_ms 认一次。"""
        img = np.full((1080, 1920, 3), 90, np.uint8)
        t = 0.0
        while t <= 22.0:
            self.clock[0] = t
            self.proc.settle(img, t)
            t += 0.5
        m = self.proc._not_pick.max_s
        self.assertEqual(m, 10.0)
        self.assertEqual(self.pick_at, [0.0, 1.0, 1.0 + m, 1.0 + 2 * m])

    def test_no_glance_in_game(self):
        """对局中读不到轮次（战斗、面板挡住等）：不另外看那一眼，照旧只在每 hud_ms 读不到轮次时顺带认一次（最后一次读到轮次
        以后第一次读不到照认，第二次起 pick_grace_ms 以内的几次先不认）；连着 quiet_ms 看不到对局读数（当作不在对局里了）以后
        也一样。"""
        self.playing()
        self.round, self.stars = None, None
        end = 8.0 + self.proc.quiet_s + 6.0
        self.steps(8.5, end)
        self.assertTrue(self.session.game_quiet)
        hud = [8.0 + 3.0 * k for k in range(1, int((end - 8.0) // 3.0) + 1)]
        self.assertEqual([t for t in self.pick_at if t > 8.0],
                         hud[:1] + [t for t in hud if t - 8.0 >= self.proc.pick_grace_s])
        self.assertEqual(hud[:3], [11.0, 14.0, 17.0])  # 第 11 秒照认，第 14、17 秒先不认

    def test_no_glance_while_yielding(self):
        """两开时另一路的三选一显示着（让路）：不在对局里也不看那一眼（要认字），那边关掉以后接着看。"""
        self.proc.board = SimpleNamespace(others=lambda proc, now: True, note=lambda *args: None)
        self.steps(0.0, 3.0)
        self.assertEqual(self.pick_at, [])
        self.proc.board = None
        self.step(3.5)
        self.assertEqual(self.pick_at, [3.5])

    # ---- 中途退出以后认不出主菜单（弹窗挡住了等）：连着 quiet_ms 看不到对局里的读数就当作不在对局里（Session.game_quiet）

    def quiet_records(self) -> list[dict]:
        return [r for r in self.session.recent_events(600) if r["kind"] == "read:quiet"]

    def walk(self, start: float, end: float) -> None:
        """从 start 到 end（含）每 4 秒一帧（比 hud_ms 长，每帧都读一次轮次），每一帧以后 game_quiet 都还没置上。"""
        t = start
        while t <= end + 1e-9:
            self.step(t)
            self.assertFalse(self.session.game_quiet, t)
            t += 4.0

    def test_quiet_after_quiet_ms(self):
        """中途退出、认不出主菜单：读不到轮次、星数，连着 quiet_ms 以后当作不在对局里（识别记录记一条），这一局的状态不动；
        又读到轮次时马上改回在对局里。"""
        self.playing()
        self.round, self.stars = None, None
        end = 8.0 + self.proc.quiet_s
        self.walk(12.0, end - 4.0)
        self.step(end)
        self.assertTrue(self.session.game_quiet)
        self.assertEqual((self.session.settings.round, self.session.settings.star), (12, 3))
        self.assertTrue(self.session.has_game_state)
        self.assertFalse(self.session.game.game_over)
        self.assertEqual([(r["on"], r.get("seconds")) for r in self.quiet_records()], [(True, round(self.proc.quiet_s))])
        self.step(end + 4.0)  # 满了以后不再重复记
        self.assertEqual(len(self.quiet_records()), 1)
        self.round = 12
        self.step(end + 8.0)
        self.assertFalse(self.session.game_quiet)
        self.assertEqual([r["on"] for r in self.quiet_records()], [True, False])

    def test_quiet_counts_only_looked_time(self):
        """只算看着画面的时间：两帧之间隔得再久（暂停、手动模式不看画面、截不到画面）也最多算 QUIET_STEP_S，刚从暂停恢复的
        第一帧不算。"""
        self.playing()
        self.round, self.stars = None, None
        self.step(12.0)  # 算 4 秒
        self.step(300.0)  # 中间没有帧：只算 QUIET_STEP_S
        self.proc.resumed()
        self.step(600.0)  # 刚从暂停恢复：不算
        counted = 4.0 + pipeline.QUIET_STEP_S
        self.assertFalse(self.session.game_quiet)
        steps = int(-(-(self.proc.quiet_s - counted) // 4.0))  # 还要每 4 秒一帧走几帧才满
        self.walk(604.0, 600.0 + 4.0 * (steps - 1))
        self.step(600.0 + 4.0 * steps)
        self.assertTrue(self.session.game_quiet)

    def test_list_keeps_in_game(self):
        """读不到轮次、玩家列表上的星数照样读得到（轮次那一块认不出字的电脑等）：一直算在对局里。"""
        self.playing()
        self.round = None
        self.walk(12.0, 8.0 + 2 * self.proc.quiet_s)

    def test_select_keeps_in_game(self):
        """三选一一直显示着（轮次、星数都读不到）：在对局里；三选一关掉以后从最后一帧三选一起重新算。"""
        self.playing()
        self.round, self.stars = None, None
        self.seen = offer(["标靶", "大背包", "缓冲盾牌"])
        self.walk(12.0, 8.0 + 2 * self.proc.quiet_s)
        last = 8.0 + 2 * self.proc.quiet_s
        self.seen = None
        self.walk(last + 4.0, last + self.proc.quiet_s - 4.0)
        self.step(last + self.proc.quiet_s)
        self.assertTrue(self.session.game_quiet)

    def test_yielding_not_counted(self):
        """两开时给另一路让路的几帧不算（那几帧不读轮次，不是看不到）；不再让路以后接着算。"""
        self.playing()
        self.round, self.stars = None, None
        self.proc.board = SimpleNamespace(others=lambda proc, now: True, note=lambda *args: None)
        last = 8.0 + 2 * self.proc.quiet_s
        self.walk(12.0, last)
        self.proc.board = None  # 从让路的最后一帧接着算，让路的那一段不补进来
        self.walk(last + 4.0, last + self.proc.quiet_s - 4.0)
        self.step(last + self.proc.quiet_s)
        self.assertTrue(self.session.game_quiet)

    # ---- 停在主菜单（不在对局里、最近认出的对局外画面是主菜单）：底栏和开始按钮那一块没变就不认字（主菜单空转）

    MENU = OutOfGame(MAIN_MENU)

    @staticmethod
    def menu_frame(n: int, banner: int = 0, start: int = 150, bar: int = 120, size=(1920, 1080)) -> np.ndarray:
        """主菜单的一帧：上方、中间按 n 换亮度（背景动画：整幅缩略图每帧都变，离开对局后的整幅识别每次到点都要做），左下角的
        横幅按 banner 换（轮播），底下那一条和右下角开始按钮那一块（MENU_REGIONS 的两块）的亮度是 bar、start。"""
        w, h = size
        (_, bar_top, _, _), (left, top, _, bottom) = pipeline.MENU_REGIONS
        bar_top, top, bottom, left = round(bar_top * h), round(top * h), round(bottom * h), round(left * w)
        img = np.empty((h, w, 3), np.uint8)
        img[:top] = 60 + 50 * (n % 3)
        img[top:bottom, :left] = 90 + 40 * (banner % 3)
        img[top:bottom, left:] = start
        img[bar_top:] = bar
        return img

    def menu(self, t: float, force: bool = False, **kw) -> list:
        """t 秒处的一帧主菜单（menu_frame，背景每帧都变），到期的读数都做完（settle）。"""
        self.clock[0] = t
        self.n += 1
        return self.proc.settle(self.menu_frame(self.n, **kw), t, force=force)

    def menus(self, start: float, end: float, banner_s: float | None = None, **kw) -> list:
        """从 start 到 end（不含）每 0.5 秒一帧主菜单；banner_s 为数时左下角的横幅每这么多秒换一张。返回这些帧的事件。"""
        events, t = [], start
        while t < end - 1e-9:
            events += self.menu(t, banner=int(t // banner_s) if banner_s else 0, **kw)
            t += 0.5
        return events

    def other(self, t: float, value: int = 30) -> list:
        """t 秒处的一帧别的画面（整幅一个颜色：底栏、开始按钮那一块都和主菜单不同），到期的读数都做完。"""
        self.clock[0] = t
        return self.proc.settle(np.full((1080, 1920, 3), value, np.uint8), t)

    def ocr_at(self, t: float) -> list[str]:
        """t 秒那一张画面上依次认字的几项。"""
        return [kind for kind, at in self.ocr if at == t]

    def idle_records(self) -> list[tuple]:
        return [(r["on"], r.get("why")) for r in self.session.recent_events(600) if r["kind"] == "read:menu_idle"]

    def idle_parts(self) -> list[tuple]:
        """同 idle_records，另带 part（因为哪一块变了不再空转）。"""
        return [(r["on"], r.get("why"), r.get("part")) for r in self.session.recent_events(2000)
                if r["kind"] == "read:menu_idle"]

    def flapping(self, start: float, end: float, part: str, **kw) -> None:
        """从 start 到 end（不含）每 0.5 秒一帧主菜单（背景照旧每帧都变），part 那一块（start 开始按钮、bar 底栏）自己在动：
        亮度每帧在两个值之间来回换。"""
        values = {"start": (150, 180), "bar": (120, 150)}[part]
        t, k = start, 0
        while t < end - 1e-9:
            self.menu(t, **{part: values[k % 2]}, **kw)
            t, k = t + 0.5, k + 1

    def flap_trips(self, part: str, last: tuple) -> list[tuple]:
        """开始空转以后 part 那一块来回 MENU_FLAPS 次的识别记录：每次不再空转（changed，带 part）、紧接着又开始空转，最后一次
        重新空转那一条换成 last。"""
        trips = [(False, "changed", part), (True, None, None)] * pipeline.MENU_FLAPS
        return [(True, None, None), *trips[:-1], last]

    def end_on_menu(self) -> float:
        """一局打到第 12 轮以后中途退出、回到主菜单（背景一直在动），下方区域隔一个 hud_ms 两次认出，结束这一局，之后停在主菜单上
        空转。返回结束这一局的时刻。"""
        self.playing()
        self.round, self.bottom = None, self.MENU
        end = 12.0 + self.proc.hud_s
        self.menus(12.0, end)
        self.assertEqual(self.whys(self.menu(end), "game_over"), ["检测到主菜单"])
        return end

    def test_menu_idle_one_minute(self):
        """刚打开时停在主菜单 60 秒，背景每帧都变（整幅缩略图一直在变）、左下角的横幅每 5 秒换一张，底栏和开始按钮那一块
        不变：第一帧认出主菜单以后就空转，这 60 秒只读了一次轮次、认了一次下方区域（都在第一帧），识别记录记一条开始空转。
        不空转（menu_idle 关掉，同时识别两个客户端的性能测试这样用）的话读轮次、下方区域、整幅识别这 60 秒一共几十次。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        self.assertTrue(self.proc.menu_idle)
        self.proc.menu_idle = False
        self.menus(0.0, 60.0, banner_s=5.0)
        self.assertGreaterEqual(len(self.ocr), 40)
        self.assertEqual(self.idle_records(), [])
        self.ocr = []
        self.make()
        self.menus(0.0, 60.0, banner_s=5.0)
        self.assertEqual(self.ocr, [("hud", 0.0), ("pick", 0.0)])
        self.assertEqual(self.idle_records(), [(True, None)])

    def test_menu_idle_recheck(self):
        """空转满 menu_idle_s（MENU_IDLE_MS，低配模式也不乘倍数）照常看一眼下方区域（不沿用「画面没变」的结论），还是主菜单就
        接着空转：150 秒里只在第 0、60、120 秒认下方区域，读轮次只有第一帧那一次，整幅识别一次都没做。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        m = self.proc.menu_idle_s
        self.assertEqual(m, pipeline.MENU_IDLE_MS / 1000)
        self.proc.set_pace(2)
        self.assertEqual(self.proc.menu_idle_s, m)
        self.proc.set_pace(1)
        self.menus(0.0, 2.5 * m)
        self.assertEqual(self.ocr, [("hud", 0.0), ("pick", 0.0), ("pick", m), ("pick", 2 * m)])
        self.assertEqual(self.idle_records(),
                         [(True, None), (False, "recheck"), (True, None), (False, "recheck"), (True, None)])

    def test_menu_in_game_not_idle_until_over(self):
        """对局中途回到主菜单：第一次认出时这一局还没结束，不空转、照常读，隔 hud_ms 再认一次；第二次认出、结束了这一局以后
        才空转，之后不再认字。"""
        self.playing()
        self.round, self.bottom = None, self.MENU
        self.menus(12.0, 15.0)
        self.assertFalse(self.session.game.game_over)
        self.assertEqual(self.idle_records(), [])
        self.assertEqual(self.ocr_at(12.0)[:2], ["hud", "pick"])
        self.assertEqual(self.pregame_at, [12.0, 14.0])
        self.assertEqual(self.whys(self.menu(15.0), "game_over"), ["检测到主菜单"])
        self.assertEqual(self.idle_records(), [(True, None)])
        count = len(self.ocr)
        self.menus(15.5, 45.0, banner_s=5.0)
        self.assertEqual(len(self.ocr), count)

    def test_menu_idle_then_select_screen(self):
        """回到主菜单空转着，下一局的选角界面出来（底栏、开始按钮那一块都变了）：这一帧先认下方区域、不先读轮次，认出选角界面
        开新的一局（不可撤销）；接着整幅识别（选角界面上可能公布畸变组件），再读轮次。"""
        end = self.end_on_menu()
        self.menus(end + 0.5, end + 30.0)
        self.picking, self.bottom = True, None
        t = end + 30.0
        events = self.other(t)
        self.assertEqual([(e.undo, e.cause) for e in events if e.kind == "newgame"], [(False, "select")])
        self.assertEqual(self.ocr_at(t)[:3], ["pick", "pregame", "hud"])
        self.assertEqual(self.idle_records(), [(True, None), (False, "changed")])

    def test_menu_idle_then_room(self):
        """回到主菜单空转着，进了组队房间：先认下方区域（认不出），紧接着整幅识别认出组队房间、交给记账（房间上勾选的难度留给
        下一局），不等读轮次。"""
        end = self.end_on_menu()
        self.menus(end + 0.5, end + 20.0)
        self.bottom, self.pregame = None, Pregame(screen=OutOfGame(TEAM_ROOM, "噩梦"))
        t = end + 20.0
        self.other(t)
        self.assertEqual(self.ocr_at(t)[:2], ["pick", "pregame"])
        self.assertEqual(self.session.pending._next_difficulty, {"team_room": "噩梦"})
        rec = [r for r in self.session.recent_events(60) if r["kind"] == "read:screen"]
        self.assertEqual((rec[-1]["screen"], rec[-1]["full"]), ("team_room", True))

    def test_menu_idle_small_change_full_read(self):
        """只有整幅识别认得出主菜单（下方区域认不出）、背景不动：认出以后空转；之后只有开始按钮那一块变了，整幅缩略图变化不到
        loading_change，照样紧接着整幅识别，认出「匹配成功」画面，画面上的难度和地图留给下一局。不丢掉空转之前主菜单那张
        缩略图的话，这次整幅识别会因为画面没怎么变而不做。"""
        self.bottom, self.pregame = None, Pregame(screen=self.MENU)
        still = self.menu_frame(0)
        for k in range(20):
            self.clock[0] = k * 0.5
            self.proc.settle(still, k * 0.5)
        self.assertEqual(self.pregame_at, [0.0])
        self.assertEqual(self.idle_records(), [(True, None)])
        changed = self.menu_frame(0, start=200)
        diff = np.abs(pipeline._thumb(changed).astype(np.int16) - pipeline._thumb(still)).mean() / 255
        self.assertLess(diff, self.proc.loading_change)
        self.pregame = Pregame(screen=OutOfGame(MATCH_FOUND, "噩梦", "水乡古镇"))
        self.clock[0] = 10.0
        self.proc.settle(changed, 10.0)
        self.assertEqual(self.pregame_at, [0.0, 10.0])
        self.assertEqual(self.session.pending._next_difficulty, {"match_found": "噩梦"})
        self.assertEqual(self.session.pending._next_map, ("水乡古镇", "match"))

    def test_menu_idle_then_game(self):
        """回到主菜单空转着，回到了对局画面（第 1 轮）：先认下方区域、整幅识别，接着读轮次；隔 hud_ms 又读到第 1 轮（比离开时
        的第 12 轮小）就开新的一局（可以撤销）。"""
        end = self.end_on_menu()
        self.menus(end + 0.5, end + 20.0)
        self.round, self.bottom = 1, None
        t = end + 20.0
        self.other(t)
        self.assertEqual(self.ocr_at(t)[:3], ["pick", "pregame", "hud"])
        events = self.other(t + self.proc.hud_s)
        self.assertEqual([(e.undo, e.cause) for e in events if e.kind == "newgame"], [(True, "round")])

    def test_menu_idle_rejoin(self):
        """回到主菜单空转着，断线以后又连回了同一局（连着两次读到不比离开时小的轮次）：撤回那次结束、接着记（status 的 cause
        为 rejoined）。"""
        end = self.end_on_menu()
        self.menus(end + 0.5, end + 20.0)
        self.round, self.bottom = 12, None
        t = end + 20.0
        self.other(t)
        events = self.other(t + self.proc.hud_s)
        self.assertEqual([e.cause for e in events if e.kind == "status" and e.cause], ["rejoined"])
        self.assertFalse(self.session.game.game_over)

    def test_menu_idle_resize_resume_hotkey(self):
        """空转中窗口大小变了、从暂停恢复、按了热键：那一帧都照常认（先看一眼下方区域），还是主菜单就接着空转。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        small = (1600, 900)
        self.menus(0.0, 5.0)
        self.menus(5.0, 10.0, size=small)  # 窗口大小变了
        self.proc.resumed()
        self.menus(10.0, 15.0, size=small)
        self.menu(15.0, force=True, size=small)  # 按热键
        self.menus(15.5, 20.0, size=small)
        self.assertEqual(self.pick_at, [0.0, 5.0, 10.0, 15.0])
        self.assertEqual(self.idle_records(), [(True, None), (False, "resized"), (True, None), (False, "resumed"),
                                               (True, None), (False, "hotkey"), (True, None)])

    def test_menu_idle_state_changed(self):
        """空转中界面上改了对局状态（手动选了地图：有了对局状态，不在对局外了）：不再空转，这一帧照常认。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        self.menus(0.0, 5.0)
        self.session.settings.map = "异变图书馆"
        self.menu(5.0)
        self.assertEqual(self.idle_records(), [(True, None), (False, "in_game")])
        self.assertIn(("pick", 5.0), self.ocr)

    def test_menu_idle_yielding(self):
        """空转中两开的另一路三选一显示着（让路），自己离开了主菜单：让路期间不看那一眼（要认字），那边不再显示以后的第一帧
        先看那一眼、接着整幅识别。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        self.menus(0.0, 5.0)
        self.proc.board = SimpleNamespace(others=lambda proc, now: True, note=lambda *args: None)
        self.bottom, self.pregame = None, Pregame()
        self.other(5.0)
        self.other(5.5, value=40)
        self.assertEqual([at for _, at in self.ocr if at >= 5.0], [])
        self.assertEqual(self.idle_records(), [(True, None), (False, "changed")])
        self.proc.board = None
        self.other(6.0, value=50)
        self.assertEqual(self.ocr_at(6.0)[:2], ["pick", "pregame"])

    # ---- 主菜单空转防「来回进出」（MENU_FLAPS）：固定界面那两块里有一块自己在动

    def test_menu_flap_start_bar_only(self):
        """开始按钮那一块自己在动（每帧都变）、底栏不动：每次空转刚开始就因为它变了不再空转，看一眼下方区域又认回主菜单、紧接着
        重新空转；来回 MENU_FLAPS 次以后这次停在主菜单期间只拿底栏判断，接着空转。两分钟里只认了这几次字：第一帧读轮次、认下方
        区域，每次来回看一眼，满一分钟照常认一次。不防来回的话每帧都要看一眼（一分钟约 120 次，比不空转时的 57 次还多一倍）。
        识别记录：判之前的来回照记（changed 带 part 为 start），最后一次重新空转那一条的 why 为 bar_only，满一分钟照常认的
        那一次照记。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        flaps, m = pipeline.MENU_FLAPS, self.proc.menu_idle_s
        self.flapping(0.0, 2 * m, "start")
        trips = [("pick", 0.5 * k) for k in range(1, flaps + 1)]
        self.assertEqual(self.ocr, [("hud", 0.0), ("pick", 0.0), *trips, ("pick", 0.5 * flaps + m)])
        self.assertEqual(self.idle_parts(),
                         [*self.flap_trips("start", (True, "bar_only", None)), (False, "recheck", None), (True, None, None)])

    def test_menu_flap_bar_pause(self):
        """底栏自己在动（每帧都变）：来回 MENU_FLAPS 次以后 MENU_FLAP_MS 以内不再空转、照不空转时读，过了再试，还在来回就再停：
        两分钟里每分钟认字的次数比不空转（menu_idle 关掉）时最多多 MENU_FLAPS 次（再试时每次来回看一眼）。不防来回的话每帧都要
        看一眼，一分钟约 120 次。识别记录：判之前的来回照记（part 为 bar），停空转时记一条 on 为 false、why 为 flapping；再试时
        开始空转那一条照记，之后的来回不记，再停又记一条 flapping。"""
        self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
        m = pipeline.MENU_FLAP_MS / 1000

        def per_minute() -> list[int]:
            return [sum(1 for _, at in self.ocr if lo <= at < lo + m) for lo in (0.0, m)]

        self.proc.menu_idle = False
        self.flapping(0.0, 2 * m, "bar")
        base = per_minute()
        self.ocr = []
        self.make()
        self.flapping(0.0, 2 * m, "bar")
        for before, now in zip(base, per_minute()):
            self.assertLessEqual(now, before + pipeline.MENU_FLAPS)
        self.assertEqual(self.idle_parts(), [*self.flap_trips("bar", (False, "flapping", None)), (True, None, None),
                                             (False, "flapping", None)])

    def test_menu_bar_only_still_leaves(self):
        """只拿底栏判断以后（开始按钮那一块来回满 MENU_FLAPS 次），离开主菜单照样马上认得出（底栏变了）：选角界面出来的那一帧
        先认下方区域、认出选角界面（这一局从选角开始看），接着整幅识别；进组队房间、「匹配成功」画面出来的那一帧先看一眼下方区域、
        紧接着整幅识别认出来，难度留给下一局。判过来回以后先没记的这一次不再空转（part 为 both），认出别的画面时补记。"""
        cases = {"select": None, "room": OutOfGame(TEAM_ROOM, "噩梦"), "match": OutOfGame(MATCH_FOUND, "噩梦", "水乡古镇")}
        for case, screen in cases.items():
            with self.subTest(case):
                self.make()
                self.ocr, self.picking = [], False
                self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
                self.flapping(0.0, 20.0, "start")
                self.assertEqual(self.idle_parts(), self.flap_trips("start", (True, "bar_only", None)))
                t = 20.0
                if screen is None:
                    self.picking, self.bottom, self.pregame = True, None, Pregame()
                    self.other(t)
                    self.assertTrue(self.session.game.whole_game)
                    self.assertEqual(self.ocr_at(t)[:3], ["pick", "pregame", "hud"])
                else:
                    self.bottom, self.pregame = None, Pregame(screen=screen)
                    self.other(t)
                    self.assertEqual(self.ocr_at(t)[:2], ["pick", "pregame"])
                    self.assertEqual(self.session.pending._next_difficulty, {screen.kind: "噩梦"})
                self.assertEqual(self.idle_parts()[-1], (False, "changed", "both"))

    def test_menu_bar_only_until_stay_over(self):
        """只拿底栏判断只管这次停在主菜单：认出别的画面（房间）、窗口大小变了、从暂停恢复以后回到主菜单，开始按钮那一块一变照旧
        不再空转（part 为 start；看一眼又认回主菜单，接着空转）。"""
        small = (1600, 900)
        for case in ("room", "resized", "resumed"):
            with self.subTest(case):
                self.make()
                self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
                self.flapping(0.0, 5.0, "start")
                self.assertEqual(self.idle_parts()[-1], (True, "bar_only", None))
                size = small if case == "resized" else (1920, 1080)
                if case == "room":
                    self.bottom, self.pregame = None, Pregame(screen=OutOfGame(TEAM_ROOM, "噩梦"))
                    self.other(5.0)
                    self.bottom, self.pregame = self.MENU, Pregame(screen=self.MENU)
                elif case == "resumed":
                    self.proc.resumed()
                self.menus(5.5, 10.0, size=size)
                self.assertEqual(self.idle_parts()[-1], (True, None, None))
                self.menu(10.0, start=200, size=size)
                self.assertEqual(self.idle_parts()[-2:], [(False, "changed", "start"), (True, None, None)])
                self.assertEqual(self.pick_at[-1], 10.0)


class TimedReadsTest(unittest.TestCase):
    """各项读数各自计时（read:名字）：识别函数换成假的，看记下了哪些名字（现有的 trace 内容另由
    其他测试逐项核对）。"""

    def setUp(self):
        from advisor.vision import io

        self.db = load_db()
        self.session = Session(self.db, Settings(hero="凛", star=1, star_read=True))
        self.session.game.seen_hero = "凛"
        self.seen, self.round, self.picking = None, 3, False
        self.io = io
        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full=False, **kw: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "read_own_coins",
                              return_value=SimpleNamespace(value=10, reason="", text="10", score=1.0)),
            mock.patch.object(pipeline, "read_own_stars_by_name",
                              return_value=SimpleNamespace(result=None, reason="", rows=())),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", return_value=None),
            mock.patch.object(pipeline, "is_select_screen", side_effect=lambda img, db, info=None: self.picking),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "save_template", return_value="x.png"),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(io, "ocr", return_value=[]),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.names: list[str] = []
        recorder = mock.patch.object(pipeline.perfstats, "record",
                                    side_effect=lambda kind, ms: self.names.append(kind))
        recorder.start()
        self.addCleanup(recorder.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.img = np.zeros((1080, 1920, 3), np.uint8) + 60

    def test_names(self):
        self.proc.settle(self.img, 0.0)  # 对局画面：hud、hero、stars、coins、progress、panel、players
        self.round = None
        self.proc.settle(self.img, 4.0)  # 读不到轮次：pick
        self.picking = True
        self.proc.settle(self.img, 8.0)
        self.seen = offer(["标靶", "大背包", "缓冲盾牌"])
        self.proc.settle(self.img, 12.0)  # 三选一：select
        self.proc.hints = None  # 换一份提示：下面直接用当前状态（不沿用上一帧的快照）
        self.session.player_name = "玩家"
        self.proc._own_by_name(self.img)  # 头像认不出自己那一行：own_by_name
        self.proc.hints = None
        self.session.game.template_wanted = "凛"
        self.proc._save_template(self.img, [])  # template
        self.proc._check_loading(self.img, 100.0, [])  # 离开对局后的整幅识别：pregame
        got = set(self.names)
        for name in ("select", "hud", "progress", "hero", "stars", "coins", "players", "panel", "pick",
                     "pregame", "own_by_name", "template"):
            with self.subTest(name=name):
                self.assertIn(f"read:{name}", got, sorted(got))
        # _timed 记的是 read:名字；只有推荐那一次（不在这里测）不是 read: 开头
        self.assertEqual([k for k in self.names if not k.startswith("read:")], ["advice"])

    def test_keyword_names_passed_through(self):
        """被计时的函数有叫 name、fn 的关键字参数时照样原样交给它（_timed 的 name、fn 只按位置传）。"""
        got = pipeline._timed("x", lambda **kw: kw, name="甲", fn="乙")
        self.assertEqual(got, {"name": "甲", "fn": "乙"})
        self.assertEqual(self.names, ["read:x"])


class _FakeReads(unittest.TestCase):
    """识别函数全部换成假的、每调一次记进 log（名字按先后）：三选一（select）、读轮次（hud）、读进度条（progress）、认角色
    （hero）、读星数（stars）、读星币（coins）、读队友（players）、认选角界面（pick）、离开对局后的整幅识别（pregame）。
    只查绿框（reread_selected）不记：画面上还是同一组候选（筹码名那一条没变）时沿用上一次完整识别的结果。
    读数由 seen（三选一，None 为不是）、round、current（进度条当前格）给。"""

    def setUp(self):
        self.db = load_db()
        self.seen, self.round, self.current = None, 5, 9
        self.log: list[str] = []

        def logged(name, result):
            def call(*args, **kwargs):
                self.log.append(name)
                return result()
            return call

        def fake_reread(img, seen, strip):
            now = self.seen
            if now is None or tuple(now.names) != strip:
                return None
            return SimpleNamespace(**{**vars(seen), "selected": now.selected, "reread": True})

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=logged("select", lambda: self.seen)),
            mock.patch.object(pipeline, "name_strip", side_effect=lambda img, seen: tuple(seen.names)),
            mock.patch.object(pipeline, "reread_selected", side_effect=fake_reread),
            mock.patch.object(pipeline, "read_hud", side_effect=logged("hud", lambda: Hud(round=self.round))),
            mock.patch.object(pipeline, "read_progress", side_effect=logged("progress", lambda: Progress(self.current))),
            mock.patch.object(pipeline, "read_hero", side_effect=logged("hero", lambda: SimpleNamespace(hero="凛"))),
            mock.patch.object(pipeline, "read_own_stars", side_effect=logged("stars", lambda: None)),
            mock.patch.object(pipeline, "read_own_coins", side_effect=logged(
                "coins", lambda: SimpleNamespace(value=None, reason="没有认出玩家列表上自己那一行", text="", score=0.0))),
            mock.patch.object(pipeline, "read_players", side_effect=logged("players", lambda: None)),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "is_select_screen", side_effect=logged("pick", lambda: False)),
            mock.patch.object(pipeline, "read_pregame", side_effect=logged("pregame", lambda: Pregame())),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.session = Session(self.db, Settings(hero="凛", map="异变图书馆", map_source="tasks", map_confidence="high"))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.img = np.full((1080, 1920, 3), 90, np.uint8)

    def frame(self, t: float, force: bool = False) -> tuple[list, list]:
        """t 秒处的一帧（不做完排到下一帧的读数）：返回（这一帧的事件, 这一帧调了哪些识别函数）。"""
        self.clock[0] = t
        self.log.clear()
        events = self.proc.process(self.img, t, force)
        return events, list(self.log)

    def settle(self, t: float) -> list:
        self.clock[0] = t
        return self.proc.settle(self.img, t)

    @staticmethod
    def advice(events) -> list:
        """事件里的推荐：[(轮次, 进度条当前格)]。"""
        return [(e.state.round, e.state.progress) for e in events if e.kind == "advice"]


OFFER_A = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]
OFFER_B = ["会员推荐信", "手电筒-强光", "优雅之羽"]
OFFER_C = ["标靶", "大背包", "缓冲盾牌"]


class FastAdviceTest(_FakeReads):
    """三选一出来 2 秒内给出推荐：手上的轮次、进度条够新（自动模式、本局读到过轮次、最近 after_game_ms 里
    读到过轮次、没刚从暂停恢复）时，认出三张卡就用它们给推荐，这一帧不读轮次、进度条；下一帧再读一次核对，读到的不同就
    重算，相同不重算；核对完以后到三选一关掉都只盯绿框。手动模式、刚从暂停恢复、本局还没读到过轮次、太久没读到轮次时照旧
    先读完再给推荐。识别函数换成假的（_FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.session.game.seen_hero = "凛"
        self.settle(0.0)
        self.settle(3.0)  # 对局画面读了两轮：第 5 轮，进度条第 9 格（连读两次才采用）
        self.assertEqual((self.session.settings.round, self.session.game.progress), (5, 9))

    def test_advice_before_round_read(self):
        """三选一出现时上次读到轮次才 1 秒：这一帧只认卡、读星数就给推荐，不读轮次、进度条；下一帧读一次核对，读到的
        一样：不重算；之后只查绿框，什么都不读。"""
        self.seen = offer(OFFER_A)
        events, log = self.frame(4.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.advice(events), [(5, 9)])
        events, log = self.frame(4.15)
        self.assertEqual(log, ["hud", "progress", "stars"])  # 核对（星数照旧读前几帧）
        self.assertEqual(self.advice(events), [])  # 读到的一样：不重算、不重发
        later = []
        for i in range(2, 100):
            events, log = self.frame(4.0 + 0.15 * i)
            later += log
            self.assertEqual(self.advice(events), [])
        self.assertEqual(later, ["stars"])  # 只剩星数的第 3 次；过了 hud_ms 也不再读轮次、进度条，不认角色

    def test_check_differs_recomputed(self):
        """核对读到的轮次和用来算推荐的不同（往前一轮，直接采用）：这一帧按新的轮次重算，推荐变一次；之后不再读。"""
        self.seen = offer(OFFER_A)
        self.assertEqual(self.advice(self.frame(4.0)[0]), [(5, 9)])
        self.round = 6
        events, log = self.frame(4.15)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])
        for i in range(2, 40):
            events, log = self.frame(4.0 + 0.15 * i)
            self.assertNotIn("hud", log)
            self.assertEqual(self.advice(events), [])

    def test_progress_changed_needs_two_reads(self):
        """核对读到的进度条和已采用的不同：照旧连读两次才采用（下一帧马上补读），补读一致就按新的当前格重算，推荐变一次。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.current = 8  # 任务奖励「进度 -1」
        events, log = self.frame(4.15)
        self.assertEqual(self.advice(events), [])  # 只读到一次，先不采用
        events, log = self.frame(4.3)
        self.assertEqual(log[:1], ["progress"])  # 补读一次（不读轮次）
        self.assertNotIn("hud", log)
        self.assertEqual(self.advice(events), [(5, 8)])
        events, log = self.frame(4.45)
        self.assertNotIn("progress", log)

    def test_round_jump_needs_two_reads(self):
        """核对读到往前大幅跳变的轮次：照旧连读两次才采用（下一帧马上补读），补读一致就按新的轮次重算。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.round = 8
        events, log = self.frame(4.15)
        self.assertEqual(self.advice(events), [])
        events, log = self.frame(4.3)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.advice(events), [(8, 9)])
        self.assertNotIn("hud", self.frame(4.45)[1])

    def test_round_drop_rechecked_after_hud_ms(self):
        """核对读到变小的轮次（可能开了新的一局，也要连读两次）：不马上补读，隔 hud_ms 再读一次（宁可确认得慢一点，按
        三选一期间平常的 hud_ms 重读），之后不再读。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.round = 2
        self.assertIn("hud", self.frame(4.15)[1])
        reads = [t for t in (4.3, 4.45, 6.0, 7.0, 7.2, 7.35, 9.0, 12.0) if "hud" in self.frame(t)[1]]
        self.assertEqual(reads, [7.2])

    def read_first(self, t: float) -> None:
        """三选一在 t 秒出现、画面上已经是第 6 轮：这一帧先读轮次、进度条，推荐用上第 6 轮；下一帧不再核对。"""
        self.seen = offer(OFFER_A)
        self.round = 6
        events, log = self.frame(t)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])
        events, log = self.frame(t + 0.15)
        self.assertNotIn("hud", log)
        self.assertNotIn("progress", log)

    def test_manual_mode_reads_first(self):
        self.proc.set_manual(True)
        self.read_first(4.0)

    def test_resumed_reads_first(self):
        """刚从暂停恢复：之后第一次读到轮次以前出现的三选一先读完；读到过轮次以后又能先给推荐。"""
        self.proc.resumed()
        self.read_first(4.0)
        self.seen = None
        self.settle(4.3)
        self.settle(4.45)  # 三选一关了
        self.settle(7.0)  # 对局画面上读到轮次
        self.seen = offer(OFFER_B)
        events, log = self.frame(8.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.advice(events), [(6, 9)])

    def test_select_across_pause_reads_first(self):
        """暂停前显示着三选一（已核对过），恢复后的第一帧还是三选一：暂停期间可能已经换成了另一次（概率行相同，Session 当作
        同一次里刷新了），当作刚出现，先读完轮次、进度条再给推荐，不沿用暂停前的读数；之后照旧只盯绿框。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.frame(4.15)  # 核对
        self.proc.resumed()
        self.seen = offer(OFFER_B)
        self.round = 6
        events, log = self.frame(60.0)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])
        self.assertNotIn("hud", self.frame(60.15)[1])

    def test_round_unknown_reads_first(self):
        """本局还没读到过轮次（新的一局刚开始）：先读完。"""
        self.session.new_game()
        self.session.game.seen_hero = "凛"
        self.seen = offer(OFFER_A)
        self.round = 1
        events, log = self.frame(4.0)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertEqual([r for r, _ in self.advice(events)], [1])

    def battle(self, until: float) -> None:
        """从 6 秒起每 3 秒一轮，到 until 为止：画面上读不到轮次（战斗、面板挡住左上角）。"""
        self.round = None
        t = 6.0
        while t <= until:
            self.settle(t)
            t += 3.0

    def test_round_hidden_a_while_still_fast(self):
        """战斗等挡住轮次十几秒（还在 after_game_ms 以内，轮次通常没变）：照样先给推荐、下一帧核对。"""
        self.battle(18.0)
        self.round = 5
        self.seen = offer(OFFER_A)
        events, log = self.frame(20.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.advice(events), [(5, 9)])

    def test_stale_round_reads_first(self):
        """最近一次读到轮次（第 3 秒）已经过了 after_game_ms：先读完。"""
        self.battle(33.0)
        self.assertGreaterEqual(34.0 - 3.0, self.proc.after_game_s)
        self.read_first(34.0)

    def test_reroll_full_read_no_check(self):
        """推荐给出、核对过以后刷新了（筹码名变了）：马上完整识别、马上给推荐，不读轮次、进度条；之后照旧只查绿框。"""
        self.seen = offer(OFFER_A)
        for t in (4.0, 4.15, 4.3):  # 推荐、核对、星数读满三帧
            self.frame(t)
        self.seen = offer(OFFER_B, rerolls=1)
        events, log = self.frame(6.0)
        self.assertEqual(log, ["select"])
        self.assertEqual(self.advice(events), [(5, 9)])
        self.assertEqual(self.frame(6.15)[1], [])

    def test_another_select_checked_again(self):
        """紧接着出现的另一次三选一（概率行换了、没有棋盘画面）：也先给推荐、下一帧再核对。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.frame(4.15)
        self.seen = offer(OFFER_C, probabilities=(40, 45, 15))
        events, log = self.frame(5.0)
        self.assertEqual(log, ["select", "stars"])  # 这一次也读几帧星数
        self.assertEqual(self.advice(events), [(5, 9)])
        self.round = 6
        events, log = self.frame(5.15)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])

    def test_same_row_select_checked_again(self):
        """紧接着出现的另一次三选一、概率行一样（三张全换、刷新次数没少）：和概率行换了的一样，先结算上一次，
        这一次重新读几帧星数、先给推荐、下一帧再读轮次和进度条核对。"""
        self.seen = offer(OFFER_A)
        for t in (4.0, 4.15, 4.3):  # 推荐、核对、星数读满三帧
            self.frame(t)
        self.seen = offer(OFFER_C)  # 概率行一样、都是刷新 ×2
        events, log = self.frame(5.0)
        self.assertEqual(log, ["select", "stars"])  # 这一次也读几帧星数
        self.assertEqual([e.kind for e in events if e.kind in ("picked", "missed_pick")], ["missed_pick"])
        self.assertEqual(self.advice(events), [(5, 9)])
        self.round = 6
        events, log = self.frame(5.15)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])
        self.assertNotIn("hud", self.frame(5.3)[1])

    def test_same_row_refresh_not_checked(self):
        """概率行一样、三张全换，刷新次数却少了 1（真刷新）或读不出（拿不准）：照旧是同一次里刷新，马上给推荐，不读轮次、进度条、
        星数，不结算；之后照旧只查绿框。"""
        for label, rerolls in (("少了 1", 1), ("读不出", None)):
            with self.subTest(label):
                self.setUp()
                self.seen = offer(OFFER_A)
                for t in (4.0, 4.15, 4.3):
                    self.frame(t)
                self.seen = offer(OFFER_C, rerolls=rerolls)
                events, log = self.frame(6.0)
                self.assertEqual(log, ["select"])
                self.assertEqual([e.kind for e in events if e.kind in ("picked", "missed_pick", "idle")], [])
                self.assertEqual(len(self.advice(events)), 1)
                self.assertEqual(self.frame(6.15)[1], [])

    def test_check_postponed_by_select_error(self):
        """核对那一帧认三选一出错：这一帧到此为止，核对留到下一帧。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        with mock.patch.object(pipeline, "reread_selected", side_effect=RuntimeError("认三选一出错")):
            events, log = self.frame(4.15)
        self.assertTrue(self.proc.select_failed)
        self.assertNotIn("hud", log)
        self.assertEqual(self.frame(4.3)[1][:2], ["hud", "progress"])


class _InSelect(_FakeReads):
    """三选一期间的几组（补认、再读左上角）共用：对局画面先读两轮（第 5 轮，进度条第 9 格，同 FastAdviceTest），三选一出现以后
    按三选一期间的节奏（每 0.15 秒）一帧一帧走。读轮次可以换成一直出错（hud_error）。"""

    STEP = 0.15

    def setUp(self):
        super().setUp()
        self.hud_error: Exception | None = None  # 读轮次时抛的异常；None 为照常读（照样记进 log）

        def fake_hud(img, db, full=False, **kw):
            self.log.append("hud")
            if self.hud_error is not None:
                raise self.hud_error
            return Hud(round=self.round)

        patch = mock.patch.object(pipeline, "read_hud", side_effect=fake_hud)
        patch.start()
        self.addCleanup(patch.stop)
        self.session.game.seen_hero = "凛"
        self.settle(0.0)
        self.settle(3.0)
        self.assertEqual((self.session.settings.round, self.session.game.progress), (5, 9))

    def frames(self, start: float, end: float) -> list[tuple[float, list, list, list]]:
        """从 start 到 end（含）每 STEP 秒一帧：[(时刻, 这一帧的事件, 这一帧调了哪些识别函数, 这一帧报出的错误的名字)]。"""
        out = []
        for i in range(10000):
            t = round(start + self.STEP * i, 2)
            if t > end:
                break
            events, log = self.frame(t)
            out.append((t, events, log, [name for name, _, _ in self.proc.errors]))
        return out

    @staticmethod
    def when(got, name: str) -> list[float]:
        """这几帧里调了 name 这项识别的时刻。"""
        return [t for t, _, log, _ in got if name in log]

    @staticmethod
    def events_of(got, kind: str) -> list:
        return [e for _, events, _, _ in got for e in events if e.kind == kind]

    def assert_gaps(self, times: list[float], gap: float) -> None:
        """相邻两次之间隔 gap 秒：到点以后的第一帧就做（不早于 gap，也不晚过一帧）。"""
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(b - a, gap - 1e-6, times)
            self.assertLess(b - a, gap + self.STEP + 1e-6, times)


class SelectRereadTest(_InSelect):
    """补认：三选一刚出现那一帧读得可疑——刷新次数读成 0（刷新按钮正好被表情气泡挡住）、概率行三项加起来是
    100 却不是概率表里的行——时，过 reread_s（半秒，低配模式乘倍数）再把三张卡完整认一次：认出的不同就照新的重算推荐，相同
    不动；没有可对的账时每次三选一只补这一次。刷新次数读成 0、账上又不该是 0（这一局上一次读到还有剩，或同一次三选一里刷新前
    读到 2 次以上）时，隔 reread_s 接着补认，最多 SELECT_ZERO_REREADS 回，读到不是 0 的数就停。读得正常的不补认。
    识别函数换成假的（_FakeReads，「select」是完整识别，只查绿框不记）。"""

    @staticmethod
    def rerolls(events) -> list:
        """事件里的推荐：[(刷新次数, 有没有「刷新」那一行)]。"""
        return [(e.state.rerolls, e.advice.reroll is not None) for e in events if e.kind == "advice"]

    def test_zero_rerolls_reread_and_fixed(self):
        """刚出现那一帧刷新次数读成 0：推荐里没有「刷新」那一行；半秒后补认一次，气泡已经消失、读到 2 次，按新的重算推荐，
        有了「刷新」那一行（同一组候选，推荐日志按同一次三选一记）；之后照旧只盯绿框，不再补认。"""
        self.seen = offer(OFFER_A, rerolls=0)
        events, log = self.frame(4.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.rerolls(events), [(0, False)])
        self.seen = offer(OFFER_A)  # 气泡消失了：刷新 ×2
        got = self.frames(4.15, 12.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 1)
        self.assertGreaterEqual(reads[0], 4.0 + self.proc.reread_s)
        self.assertLess(reads[0], 4.0 + self.proc.reread_s + self.STEP)
        advice = self.events_of(got, "advice")
        self.assertEqual(self.rerolls(advice), [(2, True)])
        self.assertEqual([tuple(e.chip.name for e in ev.advice.evals) for ev in advice], [tuple(OFFER_A)])
        self.assertEqual(self.events_of(got, "picked") + self.events_of(got, "missed_pick"), [])
        self.assertEqual(self.when(got, "hud"), [4.15])  # 核对照旧只读一次

    def test_reread_same_result_no_change(self):
        """补认的结果和原来一样（刷新次数确实是 0，例如用完了）：不重算、不重发推荐；也只补这一次。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        got = self.frames(4.15, 12.0)
        self.assertEqual(len(self.when(got, "select")), 1)
        self.assertEqual(self.events_of(got, "advice"), [])

    def test_zero_after_refresh_reread_until_readable(self):
        """刷新前读到 2 次、刷新后读成 0（刷新按钮还在动画里）：账上至少该有 1 次，隔 reread_s 再完整识别；第 1 次补认仍是 0，
        第 2 次读到 1 次，按新的重算推荐（有了「刷新」那一行），之后不再补认。记账照旧算同一次三选一里刷新：不结算、
        不多出 missed_pick。"""
        self.seen = offer(OFFER_A)  # 刷新 ×2
        events, _ = self.frame(4.0)
        self.assertEqual(self.rerolls(events), [(2, True)])
        self.seen = offer(OFFER_B, rerolls=0)  # 刷新，按钮还在动画里
        events, log = self.frame(5.0)
        self.assertIn("select", log)
        self.assertEqual(self.rerolls(events), [(0, False)])
        self.assertEqual(self.when(self.frames(5.05, 5.5), "select"), [5.5])  # 第 1 次补认：按钮还挡着，仍是 0
        self.seen = offer(OFFER_B, rerolls=1)  # 第 2 次补认读到 1
        got = self.frames(5.6, 8.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 1)
        self.assertGreaterEqual(reads[0], 5.5 + self.proc.reread_s)
        self.assertLess(reads[0], 5.5 + self.proc.reread_s + self.STEP)
        self.assertEqual(self.rerolls(self.events_of(got, "advice")), [(1, True)])
        self.assertEqual(self.events_of(got, "picked") + self.events_of(got, "missed_pick"), [])

    def test_zero_after_refresh_three_rereads(self):
        """刷新前读到 2 次、刷新后一直读成 0（气泡一直挡着）：隔 reread_s 补认 3 回，间隔都不小于 reread_s，之后不再补认；
        推荐里刷新次数一直是 0。"""
        self.seen = offer(OFFER_A)  # 刷新 ×2
        self.frame(4.0)
        self.seen = offer(OFFER_B, rerolls=0)  # 刷新后按钮一直被挡着
        got = self.frames(5.0, 10.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 1 + pipeline.SELECT_ZERO_REREADS)  # 刷新那一次 + 3 回补认
        self.assert_gaps(reads, self.proc.reread_s)
        self.assertEqual({r for r, _ in self.rerolls(self.events_of(got, "advice"))}, {0})

    def test_zero_after_refresh_when_one_left(self):
        """刷新前只读到 1 次、刷新后读成 0：刷新一次就用完了，账上本来就该是 0，不多认，只有刷新那一次完整识别。"""
        self.seen = offer(OFFER_A, rerolls=1)
        self.frame(4.0)
        self.seen = offer(OFFER_B, rerolls=0)
        got = self.frames(5.0, 10.0)
        self.assertEqual(self.when(got, "select"), [5.0])
        self.assertEqual([r for r, _ in self.rerolls(self.events_of(got, "advice"))], [0])

    def test_zero_at_appearance_after_two_left(self):
        """这一局上一次三选一读到 2 次、关掉；下一次三选一刚出现读成 0：补认的几回把刚出现时排的那一次算在内（最多 3 回）；
        第 1 回补认读到 2 次就停，按新的重算推荐。"""
        self.seen = offer(OFFER_A)  # 刷新 ×2
        self.frame(4.0)
        self.seen = None
        self.frames(4.15, 4.3)  # 三选一关掉
        self.seen = offer(OFFER_B, rerolls=0)  # 下一次三选一刚出现，按钮被挡住
        events, _ = self.frame(6.0)
        self.assertEqual(self.rerolls(events), [(0, False)])
        self.seen = offer(OFFER_B)  # 第 1 回补认读到 2 次
        got = self.frames(6.15, 10.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 1)
        self.assertGreaterEqual(reads[0], 6.0 + self.proc.reread_s)
        self.assertEqual(self.rerolls(self.events_of(got, "advice")), [(2, True)])

    def test_zero_at_appearance_counts_first_reread(self):
        """这一局上一次三选一读到 2 次；下一次三选一刚出现读成 0、之后一直是 0：刚出现时排的那一次补认算第 1 回，一共补认
        SELECT_ZERO_REREADS 回（不是另加 3 回），间隔都不小于 reread_s；之后当真是 0，不再补认，下一次三选一不再按 2 次对账。"""
        self.seen = offer(OFFER_A)  # 刷新 ×2
        self.frame(4.0)
        self.seen = None
        self.frames(4.15, 4.3)  # 三选一关掉
        self.seen = offer(OFFER_B, rerolls=0)  # 下一次三选一刚出现，按钮一直被挡住
        reads = self.when(self.frames(6.0, 12.0), "select")
        self.assertEqual(len(reads), 1 + pipeline.SELECT_ZERO_REREADS)  # 刚出现那一次 + 3 回补认
        self.assertEqual(reads[0], 6.0)
        self.assert_gaps(reads, self.proc.reread_s)
        records = [r for r in self.session.recent_events(600) if r["kind"] == "read:select_zero"]
        self.assertEqual([(r["expected"], r["attempt"]) for r in records],
                         [(2, n) for n in range(1, pipeline.SELECT_ZERO_REREADS + 1)])
        self.seen = None
        self.frames(12.15, 12.3)  # 三选一关掉
        self.seen = offer(OFFER_C, rerolls=0)  # 当真是 0 以后的下一次：只照旧补认刚出现那一次
        self.assertEqual(len(self.when(self.frames(14.0, 18.0), "select")), 2)

    def test_zero_reread_covered_tries_again(self):
        """接着补认的那几回里，补认那一帧像是被临时盖住（第 3 张读不出、稀有度读不出，不采用）：这一回不算，隔 reread_s 再认；
        盖住的东西走了、按钮也露出来了，读到 1 次就按新的重算推荐。"""
        self.seen = offer(OFFER_A)  # 刷新 ×2
        self.frame(4.0)
        self.seen = offer(OFFER_B, rerolls=0)  # 刷新，按钮还在动画里
        self.frames(5.0, 5.35)
        covered = offer([*OFFER_B[:2], None], rerolls=0)
        covered.rarity = [self.db.chips[n].rarity for n in OFFER_B[:2]] + [None]
        self.seen = covered
        events, log = self.frame(5.5)  # 第 1 回补认：被盖住
        self.assertIn("select", log)
        self.assertEqual(self.rerolls(events), [])
        self.assertEqual(len([r for r in self.session.recent_events(600) if r["kind"] == "read:select_covered"]), 1)
        self.seen = offer(OFFER_B, rerolls=1)
        got = self.frames(5.65, 9.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 1)
        self.assertGreaterEqual(reads[0], 5.5 + self.proc.reread_s)
        self.assertLess(reads[0], 5.5 + self.proc.reread_s + self.STEP)
        self.assertEqual(self.rerolls(self.events_of(got, "advice")), [(1, True)])
        records = [r for r in self.session.recent_events(600) if r["kind"] == "read:select_zero"]
        self.assertEqual([r["attempt"] for r in records], [1])  # 被盖住的那一回不算

    def test_same_offer_zero_after_count_keeps_account(self):
        """同一组候选读到过 2 次以后，又一次完整识别（概率行是表外的行，刚出现时排的补认）正赶上按钮被挡、读成 0：同一组的次数
        变不了，不当成可信的 0（记账照旧按 2 次），也不补认；之后刷新出的新一组读成 0 时照样对账（账上至少该有 1 次），
        补认读到 1 次就按新的重算推荐。"""
        self.seen = offer(OFFER_A, probabilities=(60, 31, 9))  # 刷新 ×2，概率行是表外的行：半秒后补认
        self.frame(4.0)
        self.seen = offer(OFFER_A, rerolls=0)  # 补认那一下按钮被挡住
        got = self.frames(4.15, 5.0)
        self.assertEqual(len(self.when(got, "select")), 1)  # 只有刚出现时排的那一次，不因为这个 0 再补
        self.assertEqual({r for r, _ in self.rerolls(self.events_of(got, "advice"))}, {2})  # 记账照旧按 2 次
        self.assertEqual([r for r in self.session.recent_events(600) if r["kind"] == "read:select_zero"], [])
        self.seen = offer(OFFER_B, rerolls=0)  # 刷新，按钮还在动画里
        self.frames(5.15, 5.55)
        self.seen = offer(OFFER_B, rerolls=1)
        got = self.frames(5.7, 8.0)
        self.assertEqual(len(self.when(got, "select")), 1)  # 补认一回就读到了
        self.assertEqual(self.rerolls(self.events_of(got, "advice")), [(1, True)])
        records = [r for r in self.session.recent_events(600) if r["kind"] == "read:select_zero"]
        self.assertEqual([(r["expected"], r["attempt"]) for r in records], [(1, 1)])

    def test_zero_at_appearance_after_new_game(self):
        """两次三选一之间换了一局（games 变了）：上一局的次数不算，只照旧补认一次。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.seen = None
        self.frames(4.15, 4.3)  # 三选一关掉
        self.session.new_game()  # 换了一局
        self.seen = offer(OFFER_B, rerolls=0)
        self.frame(6.0)
        reads = self.when(self.frames(6.15, 10.0), "select")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([6.0, *reads], self.proc.reread_s)

    def test_zero_recorded_with_expected_and_attempt(self):
        """刷新次数读成 0、账上不该是 0 时补认的话，识别记录里记一条 select_zero，带至少该有几次（expected）和第几回（attempt）。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.seen = offer(OFFER_B, rerolls=0)
        self.frames(5.0, 5.4)
        records = [r for r in self.session.recent_events(600) if r["kind"] == "read:select_zero"]
        self.assertEqual([(r["expected"], r["attempt"]) for r in records], [(1, 1)])

    def test_reread_after_zero_new_select(self):
        """补认仍读成 0（上一组只读到过 0 次）以后，三张全换的一组又读成 0：0 次刷不了，算紧接着出现的另一次三选一，
        先结算上一次；这一次刚出现就读成 0，照样补认一次。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.assertEqual(len(self.when(self.frames(4.15, 5.0), "select")), 1)  # 补认，仍是 0 次
        self.seen = offer(OFFER_B, rerolls=0)
        got = self.frames(5.15, 8.0)
        reads = self.when(got, "select")
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[0], 5.15)
        self.assert_gaps(reads, self.proc.reread_s)
        self.assertEqual(len(self.events_of(got, "missed_pick")), 1)

    def test_normal_reading_not_reread(self):
        """读得正常（刷新 2 次、概率行是概率表里的一行）：不补认，三选一期间只认刚出现那一次。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.assertEqual(self.when(self.frames(4.15, 12.0), "select"), [])

    def test_unknown_probability_row_reread(self):
        """概率行三项加起来是 100、却不是概率表里的行（「60%/37%/3%」读成「60%/31%/9%」）：同样半秒后补认一次，读对了就按
        概率表里的这一行重算推荐；仍是同一次三选一（不当成紧接着出现的另一次：不结算、不请补录）。"""
        self.assertIsNone(difficulty_of(self.db, (60, 31, 9)))
        self.seen = offer(OFFER_A, probabilities=(60, 31, 9))
        events, _ = self.frame(4.0)
        self.assertEqual([e.state.probabilities for e in events if e.kind == "advice"], [(60, 31, 9)])
        self.seen = offer(OFFER_A)
        got = self.frames(4.15, 8.0)
        self.assertEqual(len(self.when(got, "select")), 1)
        self.assertEqual([e.state.probabilities for e in self.events_of(got, "advice")], [(60, 37, 3)])
        for kind in ("picked", "missed_pick", "idle"):
            self.assertEqual(self.events_of(got, kind), [], kind)

    def test_lowend_waits_longer(self):
        """低配模式：补认之前等的时间乘倍数（半秒 → 1 秒）。"""
        factor = self.db.rules["watch"]["lowend_factor"]
        self.proc.set_pace(factor)
        self.assertEqual(self.proc.reread_s, self.db.rules["watch"]["select_reread_ms"] / 1000 * factor)
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        reads = self.when(self.frames(4.15, 8.0), "select")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([4.0, *reads], self.proc.reread_s)

    def test_judged_at_first_complete_reading(self):
        """刚出现那一帧没读全（出场动画里概率行还读不到）：先不判可疑；读得不完整时照旧过 SELECT_RETRY_S 再完整识别，这一次
        读全了却可疑（概率行是表外的行、或刷新次数读成 0）：这时判为可疑，再过 reread_s 补认一次，读对了就按新的重算推荐。
        每次三选一仍只补认这一次。"""
        cases = {  # 刚出现那一帧、读全的那一次各读成什么
            "概率行是表外的行": ({"probabilities": None}, {"probabilities": (60, 31, 9)}),
            "刷新次数读成 0": ({"probabilities": None, "rerolls": 0}, {"rerolls": 0}),
        }
        for label, (start, wrong) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.seen = offer(OFFER_A, **start)
                self.assertEqual(self.frame(4.0)[1], ["select", "stars"])
                self.assertIsNone(self.proc._reread_at)  # 没读全：先不判
                self.seen = offer(OFFER_A, **wrong)
                first = self.when(self.frames(4.15, 4.6), "select")  # 读全的那一次
                self.assertEqual(len(first), 1)
                self.assertGreaterEqual(first[0], 4.0 + pipeline.SELECT_RETRY_S)
                self.seen = offer(OFFER_A)  # 补认时读对了
                got = self.frames(4.75, 12.0)
                reads = self.when(got, "select")
                self.assertEqual(len(reads), 1)
                self.assert_gaps([*first, *reads], self.proc.reread_s)
                self.assertEqual([(e.state.probabilities, e.state.rerolls) for e in self.events_of(got, "advice")],
                                 [((60, 37, 3), 2)])

    def test_incomplete_then_normal_not_reread(self):
        """刚出现那一帧没读全、读全的那一次读得正常：不补认，三选一期间只有读全的那一次完整识别。"""
        self.seen = offer(OFFER_A, probabilities=None)
        self.frame(4.0)
        self.seen = offer(OFFER_A)
        reads = self.when(self.frames(4.15, 12.0), "select")
        self.assertEqual(len(reads), 1)
        self.assertGreaterEqual(reads[0], 4.0 + pipeline.SELECT_RETRY_S)

    def test_reread_moved_after_check(self):
        """慢电脑：刚出现那一帧完整识别花了很久，下一帧时补认已经到点、核对（读轮次、进度条）也要做：这一帧只核对，补认挪到
        下一帧，一帧里不既完整识别、又读左上角；之后照旧只盯绿框。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.assertEqual(self.frame(4.0)[1], ["select", "stars"])
        self.seen = offer(OFFER_A)  # 气泡消失了：刷新 ×2
        late = 4.0 + self.proc.reread_s + 0.1  # 下一帧取到时补认已经到点
        self.assertEqual(self.frame(late)[1], ["hud", "progress", "stars"])  # 只核对
        events, log = self.frame(late + self.STEP)
        self.assertEqual(log, ["select", "stars"])  # 补认
        self.assertEqual(self.rerolls(events), [(2, True)])
        got = self.frames(late + 2 * self.STEP, 12.0)
        self.assertEqual(self.when(got, "select") + self.when(got, "hud"), [])

    def test_reread_moved_after_hud_retry(self):
        """核对出错、隔 retry_s 再读和补认同一帧到点（中间一直没取到画面）：同样先再读左上角，补认挪到下一帧。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.assertEqual(self.frame(4.15)[1][:1], ["hud"])  # 核对出错：隔 retry_s 再读
        self.hud_error = None
        self.seen = offer(OFFER_A)
        late = 4.15 + self.proc.retry_s + 0.05
        self.assertGreater(late, 4.0 + self.proc.reread_s)
        _, log = self.frame(late)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertNotIn("select", log)
        events, log = self.frame(late + self.STEP)
        self.assertEqual(log, ["select"])
        self.assertEqual(self.rerolls(events), [(2, True)])

    def test_retry_moved_after_check(self):
        """慢电脑：刚出现那一帧没读全（出场动画里概率行还读不到），下一帧时读得不完整的再认（SELECT_RETRY_S）已经到点、核对
        也要做：和补认一样，这一帧只核对（只查绿框），再认挪到下一帧。只有再认到点（核对已经做过）时照旧当帧
        完整识别。"""
        self.seen = offer(OFFER_A, probabilities=None)
        self.assertEqual(self.frame(4.0)[1], ["select", "stars"])
        self.seen = offer(OFFER_A)  # 出场动画放完了
        late = 4.0 + pipeline.SELECT_RETRY_S + 0.1  # 下一帧取到时再认已经到点
        self.assertEqual(self.frame(late)[1], ["hud", "progress", "stars"])  # 只核对
        events, log = self.frame(late + self.STEP)
        self.assertEqual(log, ["select", "stars"])  # 再认
        self.assertEqual([e.state.probabilities for e in events if e.kind == "advice"], [(60, 37, 3)])
        self.setUp()
        self.seen = offer(OFFER_A, probabilities=None)
        self.frame(4.0)
        self.seen = offer(OFFER_A)
        self.assertNotIn("select", self.frame(4.15)[1])  # 核对，再认还没到点：只查绿框
        got = self.frames(4.3, 5.0)
        self.assertEqual(self.when(got, "select"), [4.6])  # 只有再认到点：当帧完整识别
        self.assertEqual(self.when(got, "hud"), [])

    def test_fixed_rerolls_level_up(self):
        """0 星时升到 1 星的那一次三选一，刚出现那一帧刷新次数读成 0：记账拿不准是不是刚升星；补认读到 3 次（开局 2 次 + 升星
        1 次）以后记账重新判，推荐按 1 星算；仍是同一次三选一。"""
        self.session.on_stars(SimpleNamespace(stars=0, confidence=0.9))  # 玩家列表上读到 0 星
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.assertEqual(self.session.select._star_check, "unsure")
        self.seen = offer(OFFER_A, rerolls=3)
        got = self.frames(4.15, 8.0)
        self.assertEqual(len(self.when(got, "select")), 1)
        self.assertEqual([(e.state.star, e.state.rerolls) for e in self.events_of(got, "advice")], [(1, 3)])
        self.assertEqual((self.session.select._star_check, self.session.game._rerolls_seen), ("up", (1, 3)))
        for kind in ("picked", "missed_pick", "idle"):
            self.assertEqual(self.events_of(got, kind), [], kind)

    def test_pending_judgment_cleared(self):
        """还没判可疑（刚出现那一帧没读全）时从暂停恢复、手动模式开关变了：清掉，不带过去；恢复后的第一帧当作刚出现，读全了就
        当帧判。"""
        self.seen = offer(OFFER_A, probabilities=None)
        self.frame(4.0)
        self.assertTrue(self.proc._suspect_due)
        self.proc.resumed()
        self.assertFalse(self.proc._suspect_due)
        self.seen = offer(OFFER_A, probabilities=(60, 31, 9))
        self.frame(60.0)
        self.assertEqual((self.proc._suspect_due, self.proc._reread_at), (False, 60.0 + self.proc.reread_s))
        self.seen = None
        self.frames(60.15, 60.3)  # 三选一关掉
        self.seen = offer(OFFER_B, probabilities=None)
        self.frame(62.0)
        self.assertTrue(self.proc._suspect_due)
        self.proc.set_manual(True)
        self.assertEqual((self.proc._suspect_due, self.proc._reread_at), (False, None))


class FirstRereadTest(_InSelect):
    """三选一刚出现那一帧有筹码没读出来、还没给过推荐（记账先不提示无法识别，见 test_session.FirstUnreadTest）：先完整重读
    三张卡，读左上角（核对等）排到重读之后，两样不在同一帧；提示过无法识别、给过推荐以后照旧先读左上角
    （SelectRereadTest.test_retry_moved_after_check）。画面上筹码名那一条和读不出来的时候看着一样（文字被入场动画、特效
    挡着），只查绿框照常对得上（strip_same；关掉时照 _FakeReads 按筹码名比）。另核对识别流程留下的读不全的那一帧
    （unread_shot：导出诊断时另存）。"""

    HIDDEN = [None, None, OFFER_A[2]]

    def setUp(self):
        super().setUp()
        self.strip_same = True

        def strip_check(img, seen, strip):
            now = self.seen
            if now is None or (not self.strip_same and tuple(now.names) != strip):
                return None
            return SimpleNamespace(**{**vars(seen), "selected": now.selected, "reread": True})

        patch = mock.patch.object(pipeline, "reread_selected", side_effect=strip_check)
        patch.start()
        self.addCleanup(patch.stop)

    def hidden(self, rerolls=2):
        """前两张卡还没显示出来的那一次读数。"""
        seen = offer(self.HIDDEN, rerolls=rerolls)
        seen.raw = ["", "", OFFER_A[2]]
        seen.problems = ["第 1 个候选筹码无法识别", "第 2 个候选筹码无法识别"]
        return seen

    @staticmethod
    def told(events) -> list:
        """这一帧发出的推荐、无法识别、正在刷新（照先后）。"""
        return [e.kind for e in events if e.kind in ("advice", "unreadable", "refreshing")]

    def test_reread_first_then_check(self):
        """慢电脑：刚出现那一帧前两张卡还没显示出来，记账先不提示；下一帧时再认（SELECT_RETRY_S）和核对（读轮次、进度条）
        都到点了：这一帧先完整重读三张卡，读全了马上给推荐，不读左上角；再下一帧才核对。整个过程没有提示过无法识别。"""
        self.seen = self.hidden()
        events, log = self.frame(4.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.told(events), [])  # 不提示无法识别，悬浮窗什么都不变
        self.assertTrue(self.session.holding)
        self.seen = offer(OFFER_A)  # 前两张显示出来了
        late = 4.0 + pipeline.SELECT_RETRY_S + 0.1  # 下一帧取到时再认、核对都到点了
        events, log = self.frame(late)
        self.assertEqual(log[:1], ["select"])
        self.assertNotIn("hud", log)
        self.assertNotIn("progress", log)
        self.assertEqual(self.told(events), ["advice"])
        self.assertEqual([tuple(e.chip.name for e in ev.advice.evals) for ev in events if ev.kind == "advice"],
                         [tuple(OFFER_A)])
        events, log = self.frame(late + self.STEP)
        self.assertEqual(log[:2], ["hud", "progress"])  # 再下一帧核对
        self.assertNotIn("select", log)
        self.assertEqual(self.told(events), [])

    def test_check_waits_for_reread(self):
        """下一帧来得早、再认还没到点：核对也先不读，这几帧照旧只查绿框；再认到点的那一帧完整重读三张卡，再下一帧才核对。"""
        self.seen = self.hidden()
        self.frame(4.0)
        got = self.frames(4.15, 4.45)  # 再认还没到点
        self.assertEqual(self.when(got, "select") + self.when(got, "hud") + self.when(got, "progress"), [])
        self.seen = offer(OFFER_A)
        events, log = self.frame(4.6)  # 再认到点
        self.assertEqual(log[:1], ["select"])
        self.assertNotIn("hud", log)
        self.assertEqual(self.told(events), ["advice"])
        self.assertEqual(self.frame(4.75)[1][:2], ["hud", "progress"])

    def test_unreadable_after_wait_then_check(self):
        """真读不出来：再认到点的那一帧完整重读仍读不全，离刚出现已过半秒以上，记账这时才提示无法识别（之前的帧都没有，
        之后不重复）；提示过以后不再算刚出现，下一帧照常核对，再认照旧隔 SELECT_RETRY_S。"""
        self.seen = self.hidden()
        first, _ = self.frame(4.0)
        self.assertEqual(self.told(first), [])
        got = self.frames(4.15, 5.5)
        told = [t for t, events, _, _ in got if "unreadable" in self.told(events)]
        self.assertEqual(told, [4.6])
        self.assertEqual(self.when(got, "hud")[:1], [4.75])
        self.assertEqual(self.when(got, "select"), [4.6, 5.2])

    def test_manual_mode(self):
        """手动模式（按热键才看）：三选一刚出现那一帧照旧先读完轮次、进度条（没有新的读数可用）；有筹码没读出来时同样先不
        提示，记账等着（holding：悬浮窗不跟随三选一时也接着截图），再认到点那一帧读全了就给推荐。"""
        self.proc.set_manual(True)
        self.seen = self.hidden()
        events, log = self.frame(4.0, force=True)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertEqual(self.told(events), [])
        self.assertTrue(self.session.holding)
        self.seen = offer(OFFER_A)
        events, log = self.frame(4.6)
        self.assertEqual(log[:1], ["select"])
        self.assertEqual(self.told(events), ["advice"])

    def test_unread_frame_kept(self):
        """读不全的那一帧（新读的一次，记账说有筹码没读出来）留一张：时刻是当时的本地时刻，画面复制一份——交来识别的那块
        内存后来被改了（截取的缓冲被下一帧复用）、之后又来了只查绿框的帧和读全的帧，都不改它。"""
        when = datetime(2026, 1, 2, 12, 0, 1)
        self.img = np.full((1080, 1920, 3), 11, np.uint8)
        self.seen = self.hidden()
        with mock.patch.object(pipeline, "_wall", return_value=when):
            self.frame(4.0)
        shot = self.proc.unread_shot
        self.assertEqual(shot[0], when)
        self.assertEqual((shot[1].shape, int(shot[1].min()), int(shot[1].max())), ((1080, 1920, 3), 11, 11))
        self.img[:] = 22
        self.assertEqual(int(self.proc.unread_shot[1].max()), 11)
        self.frames(4.15, 4.45)  # 只查绿框
        self.assertIs(self.proc.unread_shot, shot)
        self.seen = offer(OFFER_A)
        self.frames(4.6, 5.0)  # 读全了、给了推荐、核对
        self.assertIs(self.proc.unread_shot, shot)
        self.seen = None
        self.frames(5.15, 5.3)  # 三选一关掉
        self.assertIs(self.proc.unread_shot, shot)

    def test_refresh_frames_not_kept(self):
        """刷新动画的中间帧（刚点了刷新、新的候选还没读全，记账标「正在刷新」）是意料之中的，不留：留下了会把要看的那一张
        换掉。刷新后等满了还读不全、照常提示无法识别的那一帧照样留。给过推荐以后读得不完整的再认照旧隔 SELECT_RETRY_S。"""
        self.strip_same = False
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.frames(4.15, 4.3)  # 核对
        self.assertIsNone(self.proc.unread_shot)
        middle = offer([None, None, OFFER_B[2]], rerolls=1)
        middle.raw = ["", "", OFFER_B[2]]
        self.seen = middle
        events, _ = self.frame(5.0)
        self.assertEqual(self.told(events), ["refreshing"])
        self.assertIsNone(self.proc.unread_shot)
        got = self.frames(5.15, 6.0)
        told = [t for t, events, _, _ in got if "unreadable" in self.told(events)]
        self.assertEqual(told, [5.6])  # 再认到点（隔 SELECT_RETRY_S）的那一帧仍读不全：等满了，提示
        self.assertEqual(self.when(got, "select"), [5.6])
        self.assertIsNotNone(self.proc.unread_shot)

    def test_copy_error_keeps_old_shot(self):
        """留那一帧时复制出错（内存不够等）：这一张只给诊断用，这一次不留、之前留的照旧；这一帧的读数照常交给记账、事件照常
        交出（这里是等满了提示无法识别），不算识别出错（errors 里没有、不记识别出错的记录，悬浮窗不提示「识别出错」）。"""
        self.seen = self.hidden()
        self.frame(4.0)
        old = self.proc.unread_shot
        self.assertIsNotNone(old)
        with mock.patch.object(pipeline, "_copy_frame", side_effect=MemoryError):
            events, log = self.frame(4.6)  # 再认到点、仍读不全
        self.assertEqual(log[:1], ["select"])
        self.assertEqual(self.told(events), ["unreadable"])
        self.assertEqual(self.proc.errors, [])
        self.assertNotIn("read:failed", [r["kind"] for r in self.session.recent_events(600)])
        self.assertIs(self.proc.unread_shot, old)

    # 读左上角排到重读之后有上限：提示过无法识别、给了推荐、请选择角色、三选一关掉以后都照常先读左上角（上面几条和下面几条）

    def test_check_not_held_after_close(self):
        """等着的时候三选一关掉了：关掉以后不再排着；下一次三选一照常先给推荐、下一帧核对。"""
        self.seen = self.hidden()
        self.frame(4.0)
        self.seen = None
        self.frames(4.15, 4.3)  # 连着两帧看不到：按关掉处理
        self.assertFalse(self.session.holding)
        self.strip_same = False
        self.seen = offer(OFFER_B)
        events, log = self.frame(6.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.told(events), ["advice"])
        self.assertEqual(self.frame(6.15)[1][:2], ["hud", "progress"])

    def test_another_select_rereads_first(self):
        """两次三选一紧接着出现（概率行换了，中间没有棋盘画面）：上一次给过推荐、核对过，换上来的这一次刚出现就有筹码没读出来，
        同样算刚出现：先等、核对排到完整重读三张卡之后，读全给了推荐以后下一帧才核对。上一次照常结算（没看到选中：请补录）。"""
        self.strip_same = False
        self.seen = offer(OFFER_A)
        self.frames(4.0, 4.3)  # 推荐、核对
        second = self.hidden()
        second.probabilities = (40, 45, 15)
        self.seen = second
        events, log = self.frame(5.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable", "missed_pick")], ["missed_pick"])
        self.assertTrue(self.session.holding)
        self.strip_same = True  # 前两张还被挡着
        got = self.frames(5.15, 5.45)
        self.assertEqual(self.when(got, "select") + self.when(got, "hud"), [])
        self.seen = offer(OFFER_C, probabilities=(40, 45, 15))
        events, log = self.frame(5.6)  # 再认到点
        self.assertEqual(log[:1], ["select"])
        self.assertNotIn("hud", log)
        self.assertEqual(self.told(events), ["advice"])
        self.assertEqual(self.frame(5.75)[1][:2], ["hud", "progress"])

    def test_hero_pending_prompt_then_check(self):
        """角色还没认出来：刚出现时读不全同样先等、先完整重读三张卡（认角色照旧一出现就认一次）；读全了请选择角色（也算发过了，
        不再算刚出现），下一帧照常核对。"""
        self.session.game.seen_hero = None  # 角色待识别
        hero = mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: self.log.append("hero"))
        hero.start()
        self.addCleanup(hero.stop)
        self.seen = self.hidden()
        events, log = self.frame(4.0)
        self.assertEqual(log, ["select", "hero", "stars"])
        self.assertTrue(self.session.holding)
        self.assertNotIn("hud", self.frame(4.15)[1])
        self.seen = offer(OFFER_A)
        events, log = self.frame(4.6)
        self.assertNotIn("hud", log)
        self.assertEqual([e.kind for e in events if e.kind in ("advice", "unreadable", "hero_needed")], ["hero_needed"])
        self.assertEqual(self.frame(4.75)[1][:2], ["hud", "progress"])

    def test_hidden_while_waiting(self):
        """等着的时候玩家点了「隐藏」（隐藏期间每帧看那行字，不算看不到三选一）：点「显示」回来时已经等满了，仍读不全就当场
        提示无法识别（不再另等一回），下一帧照常核对。"""
        self.hidden_text = True
        patch = mock.patch.object(pipeline, "hidden_ui_visible", side_effect=lambda img: self.hidden_text)
        patch.start()
        self.addCleanup(patch.stop)
        self.seen = self.hidden()
        self.frame(4.0)
        self.seen = None
        got = self.frames(4.15, 5.8)
        self.assertTrue(self.proc.select_hidden)
        self.assertEqual([e.kind for _, events, _, _ in got for e in events
                          if e.kind in ("unreadable", "missed_pick", "picked", "idle")], [])
        self.hidden_text, self.seen = False, self.hidden()  # 点了「显示」，前两张仍读不出
        events, log = self.frame(6.0)
        self.assertEqual(log[:1], ["select"])
        self.assertEqual(self.told(events), ["unreadable"])
        self.assertEqual(self.frame(6.15)[1][:2], ["hud", "progress"])

    def test_manual_recheck_after_reread(self):
        """手动模式：刚出现那一帧先读完的轮次往前大幅跳变（要连读两次才采用）：补读也排到完整重读三张卡之后（星数照旧读前几帧）；
        重读读全，先按手上的轮次给推荐，下一帧补读、采用新的轮次，按它重算推荐。"""
        self.proc.set_manual(True)
        self.round = 8
        self.seen = self.hidden()
        _, log = self.frame(4.0, force=True)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertTrue(self.session.holding)
        _, log = self.frame(4.15)
        self.assertEqual(log, ["stars"])  # 补读排到重读之后
        self.seen = offer(OFFER_A)
        events, log = self.frame(4.6)
        self.assertEqual(log[:1], ["select"])
        self.assertNotIn("hud", log)
        self.assertEqual(self.advice(events), [(5, 9)])
        events, log = self.frame(4.75)
        self.assertEqual(log[:1], ["hud"])
        self.assertEqual(self.advice(events), [(8, 9)])

    def test_check_after_reread_recomputes(self):
        """核对排到重读之后，读到的轮次和推荐用的不同（往前一轮，直接采用）：照「先给推荐、下一帧核对」重算，推荐变一次，和刚出现
        就读全时一样；星数照旧读前几帧，不排到后面。"""
        self.seen = self.hidden()
        got = self.frames(4.0, 4.45)
        self.assertEqual(self.when(got, "stars"), [4.0, 4.15, 4.3])
        self.seen = offer(OFFER_A)
        self.assertEqual(self.advice(self.frame(4.6)[0]), [(5, 9)])
        self.round = 6
        events, log = self.frame(4.75)
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.advice(events), [(6, 9)])


class HudRetryTest(_InSelect):
    """三选一开着时读左上角没成功：核对先给的推荐、确认变小的轮次（先读完再给推荐、补读的那一次也一样）出错
    或读不出轮次时，隔 retry_s（约 1 秒）再读，每次三选一最多再读 HUD_RETRIES（3）次；一直出错也不会每一帧都读。再读的次数在
    新出现一次三选一、新的一局、从暂停恢复、手动模式开关变了时清零。识别函数换成假的（_FakeReads）。"""

    def test_check_error_retried_at_most_three_times(self):
        """核对那一帧读轮次出错：隔 retry_s 再核对，还出错就再隔 retry_s，最多再读 3 次，每次照样报出；之后到三选一关掉都
        不再读。三选一关掉、下一次三选一出现时又能再读。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        got = self.frames(4.15, 20.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 1 + pipeline.HUD_RETRIES)
        self.assertEqual(reads[0], 4.15)
        self.assert_gaps(reads, self.proc.retry_s)
        self.assertEqual([errors for t, _, _, errors in got if t in reads], [["hud"]] * len(reads))
        self.seen = None
        self.frames(20.15, 20.3)
        self.seen = offer(OFFER_B, probabilities=(40, 45, 15))
        self.frame(21.0)  # 下一次三选一：手上的轮次还算新（4.15 那一次出错，最近读到是第 3 秒），先给推荐
        self.assertEqual(len(self.when(self.frames(21.15, 30.0), "hud")), 1 + pipeline.HUD_RETRIES)

    def test_check_error_then_read(self):
        """核对那一帧出错，隔 retry_s 再核对时读到了第 6 轮：按新的轮次重算推荐；之后不再读。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.assertEqual(self.frame(4.15)[1][:1], ["hud"])
        self.hud_error, self.round = None, 6
        got = self.frames(4.3, 12.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([4.15, *reads], self.proc.retry_s)
        self.assertEqual(self.advice(self.events_of(got, "advice")), [(6, 9)])

    def test_check_unreadable_retried(self):
        """核对那一帧左上角被挡住、读不出轮次：同样隔 retry_s 再核对；挡了一下就移开的，再核对时读到了，不再读。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.round = None
        self.assertEqual(self.frame(4.15)[1][:2], ["hud", "progress"])
        self.round = 5
        reads = self.when(self.frames(4.3, 12.0), "hud")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([4.15, *reads], self.proc.retry_s)

    def test_read_first_unreadable_retried(self):
        """先读完再给推荐的那一次（这里是手动模式）读不出轮次：同样隔 retry_s 再读，读到了（第 6 轮）就按它重算推荐。"""
        self.proc.set_manual(True)
        self.seen = offer(OFFER_A)
        self.round = None
        events, log = self.frame(4.0)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertEqual(self.advice(events), [(5, 9)])
        self.round = 6
        got = self.frames(4.15, 8.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([4.0, *reads], self.proc.retry_s)
        self.assertEqual(self.advice(self.events_of(got, "advice")), [(6, 9)])

    def test_round_drop_unreadable_retried_new_game(self):
        """核对读到变小的轮次（第 2 轮），隔 hud_ms 确认的那一次左上角被挡住、读不出：隔 retry_s 再读，读到第 2 轮就判出新的
        一局（不等三选一关掉）；之后选中的筹码直接记进新的一局（上一局记下的筹码不带过来）。进度条读不出（不靠它否决、
        也不靠它印证）。"""
        self.session.settings.owned = [OFFER_C[0]]  # 上一局记下的筹码
        self.seen = offer(OFFER_A)
        self.frame(20.0)  # 最近读到轮次是第 3 秒：还算新，先给推荐
        self.round, self.current = 2, None
        self.assertEqual(self.frame(20.15)[1][:1], ["hud"])  # 核对：读到第 2 轮，等确认
        self.assertTrue(self.session.round_drop_pending)
        self.assertEqual(self.when(self.frames(20.3, 23.0), "hud"), [])
        self.round = None  # 左上角被挡住
        got = self.frames(23.15, 24.0)
        confirm = self.when(got, "hud")
        self.assertEqual(len(confirm), 1)
        self.assert_gaps([20.15, *confirm], self.proc.hud_s)
        self.assertEqual(self.events_of(got, "newgame"), [])
        self.round = 2
        got = self.frames(24.15, 30.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 1)
        self.assert_gaps([*confirm, *reads], self.proc.retry_s)
        self.assertEqual([(e.cause, t) for t, events, _, _ in got for e in events if e.kind == "newgame"],
                         [("round", reads[0])])
        self.assertEqual((self.session.games, self.session.settings.round, self.session.settings.owned), (1, 2, []))
        self.seen.selected = 1  # 选中友情徽章，关掉三选一
        self.frames(30.15, 30.3)
        self.seen = None
        got = self.frames(30.45, 30.6)
        self.assertEqual([e.chip for e in self.events_of(got, "picked")], [OFFER_A[1]])
        self.assertEqual((self.session.games, self.session.settings.owned), (1, [OFFER_A[1]]))  # 记进新的一局

    def test_round_drop_unreadable_at_most_three_times(self):
        """确认变小的轮次那一次和之后再读的都读不出：最多再读 3 次，之后不再读（新的一局等三选一关掉以后照常判）。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.round = 2
        self.frame(4.15)
        self.round = None
        reads = self.when(self.frames(4.3, 30.0), "hud")
        self.assertEqual(len(reads), 1 + pipeline.HUD_RETRIES)
        self.assert_gaps([4.15, reads[0]], self.proc.hud_s)
        self.assert_gaps(reads, self.proc.retry_s)

    def test_errors_not_every_frame(self):
        """一直出错时不会每一帧都读（确认变小的轮次、补读跳变的轮次、补读进度条那一次出错时都一样）：
        出错的那一次加上再读 3 次，每次隔 retry_s。"""
        cases = {
            "确认变小的轮次": (2, 9, "hud"),
            "补读往前大幅跳变的轮次": (8, 9, "hud"),
            "补读和已采用的不同的进度条": (5, 8, "progress"),
        }
        for label, (rnd, current, broken) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.seen = offer(OFFER_A)
                self.frame(4.0)
                self.round, self.current = rnd, current
                self.frame(4.15)  # 核对：读到待确认的读数
                error = RuntimeError(f"{label}出错")
                if broken == "hud":
                    self.hud_error = error
                    patch = contextlib.nullcontext()
                else:
                    def fail(img, _error=error):
                        self.log.append("progress")
                        raise _error
                    patch = mock.patch.object(pipeline, "read_progress", side_effect=fail)
                with patch:
                    got = self.frames(4.3, 30.0)
                reads = self.when(got, broken)
                self.assertEqual(len(reads), 1 + pipeline.HUD_RETRIES)
                self.assert_gaps(reads[1:], self.proc.retry_s)
                self.assert_gaps(reads[:2], self.proc.retry_s)
                self.assertEqual(sum(1 for _, _, _, errors in got if errors), len(reads))

    def test_new_game_resets_count(self):
        """三选一开着时开了新的一局（界面上点了、或别的读数判出的）：再读的次数重新数，还能再读 3 次。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        before = self.when(self.frames(4.15, 5.5), "hud")  # 核对、再读 1 次
        self.assertEqual((len(before), self.proc._hud_retries), (2, 1))
        self.session.new_game()
        self.frame(5.65)
        self.assertEqual(self.proc._hud_retries, 0)
        after = self.when(self.frames(5.8, 20.0), "hud")
        self.assertEqual(len(after), pipeline.HUD_RETRIES)
        self.assert_gaps([before[-1], *after], self.proc.retry_s)

    def test_hero_change_resets_count(self):
        """三选一开着时换了角色（界面上手动指定）：再读的次数重新数。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.frames(4.15, 7.5)  # 核对、再读 3 次：用完了
        self.assertEqual((self.proc._hud_retries, self.proc._hud_retry_at), (pipeline.HUD_RETRIES, None))
        self.session.set_hero("芬妮")
        self.frame(7.65)
        self.assertEqual(self.proc._hud_retries, 0)

    def pending(self) -> tuple:
        """（再读了几次, 排着再读没有, 排着补认没有）。"""
        p = self.proc
        return p._hud_retries, p._hud_retry_at is not None, p._reread_at is not None

    def test_manual_toggle_clears(self):
        """手动模式开关变了：排着的再读、补认和再读的次数清掉（手动模式不跟随三选一时，两次按热键之间看不到界面关掉，不能把
        这一次的带到下一次）；开关没变时不清。"""
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.frames(4.15, 5.3)  # 核对出错、再读 1 次也出错：排着第 2 次
        self.assertEqual(self.pending(), (1, True, False))
        self.proc.set_manual(False)  # 没变：不清
        self.assertEqual(self.pending(), (1, True, False))
        self.proc.set_manual(True)
        self.assertEqual(self.pending(), (0, False, False))
        self.seen = None
        self.frames(5.45, 5.6)
        self.seen = offer(OFFER_B, rerolls=0)
        self.frame(6.0)  # 手动模式：先读完（出错，排上再读）；刷新次数读成 0，排上补认
        self.assertEqual(self.pending(), (0, True, True))
        self.proc.set_manual(False)
        self.assertEqual(self.pending(), (0, False, False))

    def test_resume_clears(self):
        """从暂停恢复：排着的再读、补认和再读的次数清掉；恢复后的第一帧照旧当作刚出现（先读完再给推荐），读得可疑照样补认。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.frame(4.15)  # 核对出错：排上再读；补认还没到点
        self.assertEqual(self.pending(), (0, True, True))
        self.proc.resumed()
        self.assertEqual(self.pending(), (0, False, False))
        self.hud_error = None
        _, log = self.frame(60.0)  # 恢复后的第一帧（还是这个三选一，只查绿框）：当作刚出现，先读完再给推荐
        self.assertEqual(log[:2], ["hud", "progress"])
        self.assertEqual(self.pending(), (0, False, True))  # 读到了，不用再读；刷新次数读成 0：这一次也补认

    def test_another_select_clears(self):
        """紧接着出现的另一次三选一（概率行换成概率表里的另一行，中间没有棋盘画面）：上一次排着的补认、再读和再读的次数清掉，
        这一次从头算；这一次读得正常，不补认，核对一次读到了就不再读。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.hud_error = RuntimeError("读轮次出错")
        self.frame(4.15)  # 核对出错：排上再读；补认还没到点
        self.assertEqual(self.pending(), (0, True, True))
        self.seen = offer(OFFER_C, probabilities=(40, 45, 15))
        self.assertEqual(self.frame(4.3)[1], ["select", "stars"])  # 另一次：手上的轮次还算新，先给推荐
        self.assertEqual(self.pending(), (0, False, False))
        reads = self.when(self.frames(4.45, 5.5), "hud")  # 这一次的核对出错、再读 1 次也出错
        self.assertEqual((len(reads), self.proc._hud_retries), (2, 1))
        self.seen = offer(OFFER_B, probabilities=(25, 50, 25))
        self.hud_error = None
        self.frame(5.65)  # 又紧接着出现一次
        self.assertEqual(self.pending(), (0, False, False))
        got = self.frames(5.8, 12.0)
        self.assertEqual(self.when(got, "hud"), [5.8])
        self.assertEqual(self.when(got, "select"), [])


class IdleOneReadTest(_FakeReads):
    """不在三选一时一帧最多做一项读数，其余到期的排到接下来的几帧（pending），每一帧开头照旧先看是不是
    三选一：交给 Session 的先后、各项的读数和一帧做完时一样；三选一出来时没做完的这一轮作废；没到点、不用读的项不占一帧。
    识别函数换成假的（_FakeReads）。"""

    def frames(self, t: float, limit: int = 12) -> list[tuple[list, bool]]:
        """从 t 秒起一帧接一帧（像悬浮窗那样不等），直到没有排着的读数：[(这一帧调了哪些识别函数, 这一帧以后还有没有排着的)]。"""
        out = []
        for i in range(limit):
            _, log = self.frame(t + 0.01 * i)
            out.append((log, self.proc.pending))
            if not self.proc.pending:
                break
        return out

    def test_one_read_per_frame_same_order(self):
        """对局画面上读一轮：读轮次、认角色、读星数（同一项里读星币）、读队友、读进度条，一帧一项，先后和交给 Session 的
        先后都和一帧做完时一样。"""
        calls = []
        for name in ("on_hud", "on_hero", "on_stars", "on_list", "on_coins", "on_teammates", "on_progress"):
            real = getattr(self.session, name)
            patch = mock.patch.object(self.session, name, side_effect=lambda *a, _n=name, _f=real, **k: (
                calls.append(_n), _f(*a, **k))[1])
            patch.start()
            self.addCleanup(patch.stop)
        got = self.frames(0.0)
        self.assertEqual(got, [(["hud"], True), (["hero"], True), (["stars", "coins"], True), (["players"], True),
                               (["progress"], False)])
        self.assertEqual(calls, ["on_hud", "on_hero", "on_stars", "on_list", "on_coins", "on_progress"])
        self.assertEqual(self.session.settings.round, 5)
        self.assertEqual(self.session.game.seen_hero, "凛")

    def test_settle_same_as_frames(self):
        """settle（回放截图用：同一幅画面接着做完）和一帧一帧做完，交给 Session 的结果相同。"""
        self.frames(0.0)
        self.frames(3.0)
        by_frames = (self.session.snapshot(), [r["kind"] for r in self.session.recent_events(60)])
        self.setUp()
        self.settle(0.0)
        self.settle(3.0)
        by_settle = (self.session.snapshot(), [r["kind"] for r in self.session.recent_events(60)])
        self.assertEqual(by_frames, by_settle)

    def test_not_due_items_take_no_frame(self):
        """认出角色以后不到 hero_ms 不再认、队友读够了不再按 hud_ms 读：下一轮这几项不占一帧，读星数紧跟着读轮次的下一帧。"""
        self.frames(0.0)
        with mock.patch.object(self.proc, "hero_s", 100.0):
            self.session.game.teammates = [{"row": 0, "hero": "恋", "bot": True}]
            self.session.game._mate_rows = {0}
            self.session.game._mate_bots = {0: True}
            got = self.frames(3.0)
        self.assertEqual([log for log, _ in got], [["hud"], ["stars"], ["progress"]])

    def test_round_missing_pick_then_stars(self):
        """读不到轮次：下一帧认选角界面，不是时再下一帧读星数；离开对局后的整幅识别排在这一轮后面。"""
        self.round = None
        got = self.frames(0.0)
        self.assertEqual(got, [(["hud"], True), (["pick"], True), (["stars", "coins"], True), (["pregame"], False)])

    def test_select_between_reads(self):
        """读了轮次、这一轮还没做完时三选一出来了：下一帧开头就认出三选一、给推荐，没做完的这一轮作废，关掉以后照各项的
        间隔再读。"""
        self.session.game.seen_hero = "凛"
        _, log = self.frame(0.0)
        self.assertEqual((log, self.proc.pending), (["hud"], True))
        self.seen = offer(OFFER_A)
        events, log = self.frame(0.3)
        self.assertEqual(log[0], "select")
        self.assertEqual([e.kind for e in events if e.kind == "advice"], ["advice"])
        self.assertNotIn("hero", log)
        self.assertNotIn("players", log)
        self.assertFalse(self.proc.pending)
        self.seen = None
        for t in (0.45, 0.6, 1.5, 2.5):
            _, log = self.frame(t)
            self.assertEqual(log, [], t)  # 没到 hud_ms：不补没做完的那几项
        self.assertEqual(self.frame(3.0)[1], ["hud"])

    def test_task_panel_opened_mid_cycle(self):
        """这一轮读数还没做完时任务栏刚打开：这一帧就读轮次连同任务栏，不等这一轮做完（任务栏可能很快又关上）；没做完的
        那几项从读轮次起重新排，照样做完。"""
        self.session.set_map(None)  # 地图未知：读轮次时连任务栏一起读，任务栏刚打开时立即读
        tasks = [False]
        fulls = []

        def hud(img, db, full, **kw):
            self.log.append("hud")
            fulls.append(full)
            if tasks[0]:
                return Hud(round=self.round, map="异变图书馆", map_confidence="high", map_candidates=["异变图书馆"],
                           tasks_seen=["击倒看守"])
            return Hud(round=self.round)

        with mock.patch.object(pipeline, "read_hud", side_effect=hud), \
                mock.patch.object(pipeline, "task_panel_visible", side_effect=lambda img: tasks[0]):
            _, log = self.frame(0.0)
            self.assertEqual((log, self.proc.pending), (["hud"], True))  # 这一轮还有认角色、读星数等几项
            tasks[0] = True
            events, log = self.frame(0.05)
            self.assertEqual(log, ["hud"])
            self.assertIn("map_read", [e.kind for e in events])
            tasks[0] = False  # 很快又关上了
            rest = [name for log, _ in self.frames(0.1) for name in log]
        self.assertEqual(fulls, [True, True])
        self.assertEqual((self.session.settings.map, self.session.map_source), ("异变图书馆", "tasks"))
        self.assertNotIn("hud", rest)
        self.assertEqual([n for n in rest if n in ("hero", "stars", "progress")], ["hero", "stars", "progress"])

    def test_hotkey_cycle_not_reused(self):
        """按热键开始的一轮：后面几帧不是按热键要求的，认选角界面照旧不沿用「画面没变、上次认定不是」的结论。"""
        self.round = None
        self.settle(0.0)
        self.settle(3.0)  # 同样的画面认过两次：记下「不是选角界面」
        self.settle(6.0)
        self.log.clear()
        self.settle(9.0)
        self.assertNotIn("pick", self.log)  # 画面没变：沿用
        _, log = self.frame(12.0, force=True)
        self.assertEqual(log, ["hud"])
        _, log = self.frame(12.01)
        self.assertEqual(log, ["pick"])  # 这一轮是按热键开始的：一律重新认

    def test_errors_take_the_frame(self):
        """一项出错：照样报出，也算这一帧的一项；读轮次出错时这一轮作废，别的项出错下一帧接着做下一项。"""
        got = []
        with mock.patch.object(pipeline, "read_hero", side_effect=RuntimeError("认角色出错")):
            for i in range(6):
                _, log = self.frame(0.01 * i)
                got.append((log, [name for name, _, _ in self.proc.errors]))
                if not self.proc.pending:
                    break
        # 认角色出错的那一帧报出来，算一项；没认出角色（角色待识别）时不读队友
        self.assertEqual(got, [(["hud"], []), ([], ["hero"]), (["stars", "coins"], []), (["progress"], [])])
        with mock.patch.object(pipeline, "read_hud", side_effect=RuntimeError("读轮次出错")):
            _, log = self.frame(3.0)
            self.assertEqual([name for name, _, _ in self.proc.errors], ["hud"])
            self.assertFalse(self.proc.pending)


class SelectTopTest(_InSelect):
    """三选一上读左上角的几处先后：刷新（筹码名变了、这一帧要完整识别）的那一帧不读左上角，核对挪到下一帧，新推荐不等它；只因为
    读不出轮次而再读时不重读进度条，读进度条出错时再读连同；先读完再给推荐的那一次不认角色，推荐给出以后的下一帧按间隔认；
    三选一上读星数只走快的那一路（read_own_stars 的 quick），平时那一轮、手动模式照常全试。识别函数换成假的（_FakeReads）。"""

    def test_refresh_defers_check(self):
        self.seen = offer(OFFER_A)
        self.frame(4.0)  # 先给推荐，下一帧核对
        self.seen = offer(OFFER_B, rerolls=1)  # 核对那一帧刷新了
        events, log = self.frame(4.15)
        self.assertEqual(log[:1], ["select"])
        self.assertNotIn("hud", log)
        self.assertEqual(len(self.advice(events)), 1)
        self.assertEqual(self.frame(4.3)[1][:2], ["hud", "progress"])  # 核对挪到下一帧

    def test_full_read_every_frame_check_not_starved(self):
        """只查绿框一直对不上（筹码名那一条一直被什么盖着，每一帧都要完整识别）：读左上角只挪一帧，下一帧照常核对。"""
        with mock.patch.object(pipeline, "reread_selected", return_value=None):
            self.seen = offer(OFFER_A)
            self.frame(4.0)
            _, log = self.frame(4.15)
            self.assertEqual(log[:1], ["select"])
            self.assertNotIn("hud", log)
            self.assertEqual(self.frame(4.3)[1][:3], ["select", "hud", "progress"])

    def test_unreadable_retry_skips_progress(self):
        self.seen = offer(OFFER_A)
        self.frame(4.0)
        self.round = None
        self.assertEqual(self.frame(4.15)[1][:2], ["hud", "progress"])  # 核对：读不出轮次，进度条读到了
        got = self.frames(4.3, 9.0)
        self.assertEqual(len(self.when(got, "hud")), pipeline.HUD_RETRIES)
        self.assertEqual(self.when(got, "progress"), [])

    def test_progress_error_retry_reads_progress(self):
        self.seen = offer(OFFER_A)
        self.frame(4.0)

        def fail(img):
            self.log.append("progress")
            raise RuntimeError("读进度条出错")

        with mock.patch.object(pipeline, "read_progress", side_effect=fail):
            self.assertEqual(self.frame(4.15)[1][:2], ["hud", "progress"])
        got = self.frames(4.3, 6.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.when(got, "progress"), reads)

    def test_read_first_hero_next_frame(self):
        """手动模式（先读完再给推荐），认角色又到了间隔：先读完的那一次不认角色，下一帧认一次，之后不再认。"""
        self.proc.set_manual(True)
        self.seen = offer(OFFER_A)
        t = self.proc.hero_s + 1.0  # 上次认角色是第 0 秒
        events, log = self.frame(t)
        self.assertEqual(log[:3], ["select", "hud", "progress"])
        self.assertNotIn("hero", log)
        self.assertEqual(len(self.advice(events)), 1)
        self.assertEqual(self.frame(t + self.STEP)[1][:1], ["hero"])
        self.assertNotIn("hero", self.frame(t + 2 * self.STEP)[1])

    def test_select_stars_quick(self):
        calls = []
        with mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: calls.append(kw)):
            self.seen = offer(OFFER_A)
            self.frames(4.0, 4.3)  # 三选一上读满三帧
            self.seen = None
            self.frames(4.45, 4.6)
            self.settle(4.15 + self.proc.hud_s)  # 对局画面上的一轮（上次读轮次是核对那一帧）
        self.assertEqual(calls, [{"quick": True}] * pipeline.SELECT_STAR_TRIES + [{}])

    def test_select_stars_full_in_manual(self):
        """手动模式平时不看画面，没有「平时那一轮」：三选一上照旧全试（换了皮肤以后快的那一路一直认不出）。"""
        calls = []
        self.proc.set_manual(True)
        with mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: calls.append(kw)):
            self.seen = offer(OFFER_A)
            self.frames(4.0, 4.3)
        self.assertEqual(calls, [{}] * pipeline.SELECT_STAR_TRIES)


# 轮次标记左边那一小块（假的识别函数只原样交回、不看它的值）
CROP = (40, 12, 30, 24)


class _Occluded(_InSelect):
    """轮次数字被玩家列表第一行的头像挡住的几组共用：mark 为真时读轮次（read_hud）在数字那一行看见了轮次标记、数字读不出
    （Hud.round_mark，交回标记左边那一小块 CROP）；peek 是在那一小块认出的数（read_round_crop，记作「peek」，None 为认不出）。
    对局画面先读两轮（第 5 轮，进度条第 9 格，同 _InSelect）；不在三选一时像悬浮窗那样隔 idle_ms 取一帧，还有排着的读数时
    紧接着取下一帧（idle）。"""

    def setUp(self):
        super().setUp()
        self.mark, self.peek, self.crops, self.peek_error, self.errs = False, None, [], None, []

        def fake_hud(img, db, full=False, **kw):
            self.log.append("hud")
            if self.hud_error is not None:
                raise self.hud_error
            if self.mark:
                return Hud(round=None, round_mark=True, round_crop=CROP)
            return Hud(round=self.round)

        def fake_peek(img, crop):
            self.log.append("peek")
            self.crops.append(crop)
            if self.peek_error is not None:
                raise self.peek_error
            return self.peek

        for patch in (mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
                      mock.patch.object(pipeline, "read_round_crop", side_effect=fake_peek)):
            patch.start()
            self.addCleanup(patch.stop)

    def idle(self, start: float, end: float, gap: float = 0.5) -> list[tuple[float, list, list]]:
        """从 start 到 end（含）隔 gap 秒取一帧，还有排着的读数（pending）时紧接着（隔 0.01 秒）取下一帧：
        [(时刻, 这一帧的事件, 这一帧调了哪些识别函数)]。各帧报出的出错另记进 errs：[(时刻, 出错那一项的名字)]。"""
        out = []
        for i in range(10000):
            t = round(start + gap * i, 2)
            if t > end:
                break
            for n in range(13):
                at = round(t + 0.01 * n, 2)
                events, log = self.frame(at)
                out.append((at, events, log))
                self.errs.extend((at, name) for name, _, _ in self.proc.errors)
                if not self.proc.pending:
                    break
        return out

    @staticmethod
    def at(got, name: str) -> list[float]:
        """这几帧（idle 的结果）里调了 name 这项识别的时刻。"""
        return [t for t, _, log in got if name in log]

    @staticmethod
    def names(got) -> list[str]:
        return [name for _, _, log in got for name in log]

    def records(self, kind: str) -> list[dict]:
        return [r for r in self.session.recent_events(600) if r["kind"] == kind]


class OccludedIdleTest(_Occluded):
    """不在三选一时轮次被挡住：完整读看见标记以后不认选角界面、照旧读星数；之后隔 occluded_s 在那一小块认一次字（一帧一项，
    读不到时什么都不排），隔 occluded_full_s 才完整读一次（不按 hud_ms）；认出手上的轮次或往前一轮时照读到轮次处理（交给记账，
    接着认角色、读星数等），认出别的数（跳了不止一轮、变小、手上还没有轮次、本局已结束）不交，隔 hud_ms 完整读来定；地图还要靠
    任务栏认、任务栏开着时照常隔 hud_ms 读；被挡住期间一直算在对局里，不开始离开对局后的整幅识别；窗口大小变了、那一小块认字
    出错时照常完整读，完整读认出选角界面时离开了对局。识别函数换成假的（_Occluded）。"""

    def test_no_pick_then_peeks(self):
        self.mark = True
        got = self.idle(6.0, 12.0)
        self.assertEqual(got[0][2], ["hud"])  # 6 秒完整读：看见标记、数字读不出
        self.assertEqual(got[1][2][:1], ["stars"])  # 下一帧读星数（玩家列表还在），不认选角界面
        self.assertNotIn("pick", self.names(got))
        self.assertNotIn("pregame", self.names(got))
        self.assertEqual(self.at(got, "hud"), [6.0, 12.0])  # 隔 occluded_full_s 才再完整读
        self.assertEqual(self.at(got, "peek"), [round(6.5 + 0.5 * i, 2) for i in range(11)])
        self.assertEqual(set(self.crops), {CROP})
        self.assertEqual(self.session.settings.round, 5)
        self.assertEqual([r.get("mark") for r in self.records("read:hud")][-2:], [True, True])

    def test_peek_read_counts_as_round(self):
        """数字露出来的那一下（第 6 轮）：交给记账（往前一轮，直接采用），接着排这一轮的其余几项；不再算被挡住，下一次完整读
        从这一眼起隔 hud_ms。识别记录记一条 read:round_crop。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.peek = 6
        got = self.idle(6.5, 6.5)
        self.assertEqual(got[0][2], ["peek"])
        self.assertEqual(self.session.settings.round, 6)
        rest = self.names(got[1:])
        self.assertIn("stars", rest)
        self.assertIn("progress", rest)
        self.assertNotIn("hud", rest)
        self.assertIsNone(self.proc._occluded)
        self.assertEqual([r["round"] for r in self.records("read:round_crop")], [6])
        self.mark, self.round = False, 6
        self.assertEqual(self.at(self.idle(7.0, 10.0), "hud"), [9.5])

    def test_peek_jump_waits_for_full_read(self):
        """这一眼认出跳了不止一轮的数（第 5 轮 → 第 8 轮）：不交给记账（只认一小块，和第一遍找字对不上时完整读按读不出处理，
        直接交的话连读两次就绕过了核对），不再只看那一小块，隔 hud_ms 完整读；完整读又看见标记、数字读不出时，下一眼还是第 8 轮
        也不采用。完整读读到第 8 轮才走记账连读两次的规矩。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.peek = 8
        got = self.idle(6.5, 6.5)
        self.assertEqual(self.at(got, "peek"), [6.5])
        self.assertIsNone(self.proc._occluded)
        self.assertEqual(self.session.settings.round, 5)
        self.assertFalse(self.session.round_jump_pending)
        got = self.idle(7.0, 11.5)
        self.assertEqual(self.at(got, "hud"), [9.0])  # 这一眼到完整读之间不看那一小块
        self.assertEqual(self.at(got, "peek"), [9.5])
        self.assertEqual(self.session.settings.round, 5)
        self.assertFalse(self.session.round_jump_pending)
        records = self.records("read:round_crop")
        self.assertEqual([(r["round"], r.get("held")) for r in records], [(8, True), (8, True)])
        self.mark, self.round = False, 8
        self.assertEqual(self.at(self.idle(12.0, 12.0), "hud"), [12.0])
        self.assertEqual(self.session.settings.round, 5)
        self.assertTrue(self.session.round_jump_pending)
        self.assertEqual(self.at(self.idle(15.0, 15.0), "hud"), [15.0])
        self.assertEqual(self.session.settings.round, 8)

    def test_peek_drop_not_delivered(self):
        """这一眼认出变小的数：不交给记账（记账那边可能判成新的一局、清掉这一局的记录），等完整读。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.peek = 1
        games = self.session.games
        self.idle(6.5, 12.0)
        self.assertEqual((self.session.settings.round, self.session.games), (5, games))
        self.assertFalse(self.session.round_drop_pending)
        self.assertEqual({r.get("held") for r in self.records("read:round_crop")}, {True})

    def test_peek_same_round_delivered(self):
        """这一眼认出手上的轮次：照读到轮次处理（确认还在这一轮，接着排这一轮的其余几项）。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.peek = 5
        got = self.idle(6.5, 6.5)
        self.assertEqual(self.session.settings.round, 5)
        self.assertIsNone(self.proc._occluded)
        self.assertIn("stars", self.names(got[1:]))
        self.assertEqual([r.get("held") for r in self.records("read:round_crop")], [None])

    def test_peek_unknown_round_waits_for_full_read(self):
        """手上还没有轮次（程序在对局中途打开、数字一直被挡住）：记账第一次读到什么就采用什么，这一眼认出的不直接交，
        完整读读到了才算。"""
        self.mark = True
        self.session.settings.round = None
        self.idle(6.0, 6.0)
        self.peek = 7
        self.idle(6.5, 6.5)
        self.assertIsNone(self.session.settings.round)
        self.mark, self.round = False, 7
        self.idle(9.0, 9.0)
        self.assertEqual(self.session.settings.round, 7)

    def test_peek_after_game_over_waits_for_full_read(self):
        """本局已结束：记账连读两次同一个数就开新的一局（不经过互相印证），这一眼认出的不直接交。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.session.game.game_over = True
        self.peek = 5
        games = self.session.games
        self.idle(6.5, 12.0)
        self.assertEqual(self.session.games, games)
        self.assertIsNone(self.session.game._end_votes)

    def test_tasks_open_reads_every_hud_s(self):
        """地图还要靠任务栏认、任务栏开着：被挡住也照常隔 hud_ms 连带读整块（任务栏开着的时候不长，刚打开那一帧可能还在动画里），
        不看那一小块（只交轮次的读数没有任务，记账会当成任务栏关了）；关掉以后回到只看一小块。"""
        fulls = []
        real = pipeline.read_hud

        def hud(img, db, full=False, **kw):
            fulls.append(full)
            return real(img, db, full=full)

        self.mark = True
        self.session.set_map(None)
        self.idle(6.0, 6.0)
        with mock.patch.object(pipeline, "read_hud", side_effect=hud), \
                mock.patch.object(pipeline, "task_panel_visible", return_value=True):
            got = self.idle(6.5, 12.5)
        self.assertEqual(self.at(got, "hud"), [6.5, 9.5, 12.5])  # 刚打开那一帧读一次，之后隔 hud_ms
        self.assertEqual(fulls, [True, True, True])
        self.assertEqual(self.at(got, "peek"), [])
        got = self.idle(13.0, 19.0)
        self.assertEqual(self.at(got, "hud"), [18.5])
        self.assertEqual(self.at(got, "peek")[:2], [13.0, 13.5])

    def test_peek_error_reads_normally(self):
        """那一小块认字出错（位置对不上这幅画面等）：这一次报一个「轮次」出错，不再只看那一小块，照常隔 hud_ms 完整读，
        不会隔 occluded_s 就报一次。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.peek_error = RuntimeError("认字出错")
        got = self.idle(6.5, 9.0)
        self.assertEqual(self.at(got, "peek"), [6.5])
        self.assertEqual(self.errs, [(6.5, "hud")])
        self.assertEqual(self.at(got, "hud"), [9.0])

    def test_resized_reads_normally(self):
        """窗口大小变了（那一小块对不上了）：不再看那一小块，照常隔 hud_ms 完整读。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.img = np.full((900, 1600, 3), 90, np.uint8)
        got = self.idle(6.5, 9.0)
        self.assertEqual(self.at(got, "peek"), [])
        self.assertEqual(self.at(got, "hud"), [9.0])

    def test_long_occlusion_stays_in_game(self):
        """一直挡着（超过 after_game_ms）：完整读每次都看见标记，一直算在对局里，不开始离开对局后的整幅识别。对照：读不到
        轮次、也没看见标记时，最后一次读到轮次（第 3 秒）after_game_ms 以后开始整幅识别。"""
        self.mark = True
        got = self.idle(6.0, 45.0)
        self.assertNotIn("pregame", self.names(got))
        self.assertNotIn("pick", self.names(got))
        self.assertEqual(self.at(got, "hud"), [6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0])
        self.setUp()
        self.round = None
        first = self.at(self.idle(6.0, 45.0), "pregame")[0]
        self.assertGreaterEqual(first, 3.0 + self.proc.after_game_s)
        self.assertLess(first, 3.0 + self.proc.after_game_s + 0.5)

    def test_long_occlusion_not_quiet(self):
        """一直挡着、超过 quiet_ms：完整读每次都看见标记，不当作不在对局里（Session.game_quiet）。对照：读不到轮次、也没看见
        标记（星数也读不到）时，最后一次读到轮次（第 3 秒）quiet_ms 以后当作不在对局里了。"""
        quiet = self.proc.quiet_s
        self.mark = True
        self.idle(6.0, 3.0 + quiet + 10.0)
        self.assertFalse(self.session.game_quiet)
        self.setUp()
        self.round = None
        self.idle(6.0, 3.0 + quiet - 0.5)
        self.assertFalse(self.session.game_quiet)
        self.idle(3.0 + quiet, 3.0 + quiet + 1.0)
        self.assertTrue(self.session.game_quiet)

    def test_left_game_seen_by_full_read(self):
        """隔 occluded_full_s 的完整读不再看见标记、也读不到轮次：照常认选角界面（看见标记以后第一次读不到，不先不判）；认出
        选角界面时离开了对局，看见标记的时刻、被挡住的那一小块都清掉，马上开始找加载画面。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.mark, self.round = False, None
        with mock.patch.object(pipeline, "is_select_screen",
                               side_effect=lambda img, db, info=None: self.log.append("pick") or True):
            got = self.idle(12.0, 12.0)
        self.assertEqual(self.names(got)[:2], ["hud", "pick"])
        self.assertEqual(self.proc._mark_seen_at, float("-inf"))
        self.assertIsNone(self.proc._occluded)
        self.assertIn("pregame", self.names(got))


class OccludedSelectTest(_Occluded):
    """三选一上轮次被挡住：完整读一直看见标记的，手上的轮次只是因为被挡住才旧的，三选一出来照样先给推荐；读左上角（核对、先读完）
    只在那一小块认字、连同进度条；认不出时隔 occluded_s 再看那一小块，不重读进度条，最多再看 HUD_RETRIES 次；认出跳了不止一轮的
    数时不交给记账，隔 retry_s 完整读，同样最多 HUD_RETRIES 次；数字露出来时照读到轮次重算推荐。识别函数换成假的（_Occluded）。"""

    def test_fresh_while_mark_seen(self):
        self.mark = True
        self.idle(6.0, 40.0)  # 完整读在 6、12、…、36 秒都看见标记
        self.assertGreaterEqual(40.1 - 3.0, self.proc.after_game_s)  # 最近一次读到数字是第 3 秒
        self.seen = offer(OFFER_A)
        events, log = self.frame(40.1)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.advice(events), [(5, 9)])
        self.peek = 5
        events, log = self.frame(40.25)
        self.assertEqual(log[:2], ["peek", "progress"])  # 核对：只在那一小块认字
        self.assertNotIn("hud", log)
        self.assertEqual(self.advice(events), [])
        got = self.frames(40.4, 45.0)
        self.assertEqual(self.when(got, "peek") + self.when(got, "hud"), [])  # 核对过了：只盯绿框

    def test_stale_without_mark_reads_first(self):
        """对照：同样过了 after_game_ms、期间一直没看见标记（战斗等挡住了左上角）：先读完再给推荐。"""
        self.round = None
        self.idle(6.0, 40.0)
        self.round = 5
        self.seen = offer(OFFER_A)
        self.assertEqual(self.frame(40.1)[1][:3], ["select", "hud", "progress"])

    def test_read_first_peeks(self):
        """手动模式（先读完再给推荐）、轮次被挡住：先读完的那一次也只在那一小块认字，连同进度条；认不出时隔 occluded_s 再看，
        只看那一小块、不重读进度条，最多再看 HUD_RETRIES 次。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.proc.set_manual(True)
        self.seen = offer(OFFER_A)
        events, log = self.frame(7.0)
        self.assertEqual(log[:3], ["select", "peek", "progress"])
        self.assertEqual(self.advice(events), [(5, 9)])
        got = self.frames(7.15, 12.0)
        peeks = self.when(got, "peek")
        self.assertEqual(len(peeks), pipeline.HUD_RETRIES)
        self.assert_gaps([7.0, *peeks], self.proc.occluded_s)
        self.assertEqual(self.when(got, "progress") + self.when(got, "hud"), [])

    def test_check_retry_peek_then_read(self):
        """核对那一帧那一小块认不出：隔 occluded_s 再看（不重读进度条），数字露出来时读到第 6 轮，按它重算推荐。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.seen = offer(OFFER_A)
        self.assertEqual(self.frame(7.0)[1], ["select", "stars"])
        self.assertEqual(self.frame(7.15)[1][:2], ["peek", "progress"])
        self.peek = 6
        got = self.frames(7.3, 12.0)
        self.assertEqual(self.when(got, "peek"), [7.75])
        self.assertEqual(self.when(got, "progress"), [])
        self.assertEqual(self.advice(self.events_of(got, "advice")), [(6, 9)])


    def test_check_jump_rereads_full(self):
        """核对那一帧那一小块认出跳了不止一轮的数：不交给记账，隔 retry_s 完整读（第一遍和那一小块互相核对）；完整读读到了
        才走连读两次的规矩，下一帧补读一次，按新的轮次重算推荐。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.seen = offer(OFFER_A)
        self.frame(7.0)
        self.peek = 8
        self.assertEqual(self.frame(7.15)[1][:2], ["peek", "progress"])
        self.assertEqual(self.session.settings.round, 5)
        self.assertIsNone(self.proc._occluded)
        self.mark, self.round = False, 8
        got = self.frames(7.3, 9.0)
        reads = self.when(got, "hud")
        self.assertEqual(len(reads), 2)
        self.assertGreaterEqual(reads[0], 7.15 + self.proc.retry_s)
        self.assertLess(reads[0], 7.15 + self.proc.retry_s + self.STEP)
        self.assertEqual(self.when(got, "peek"), [])
        self.assertEqual(self.session.settings.round, 8)
        self.assertEqual(self.advice(self.events_of(got, "advice")), [(8, 9)])

    def test_check_jump_stays_wrong_not_adopted(self):
        """那一小块一直读成跳了不止一轮的数、完整读一直按读不出处理（第一遍和那一小块对不上）：这次三选一里最多再读 HUD_RETRIES
        次，手上的轮次不变。"""
        self.mark = True
        self.idle(6.0, 6.0)
        self.seen = offer(OFFER_A)
        self.frame(7.0)
        self.peek = 8
        self.frame(7.15)
        got = self.frames(7.3, 14.0)
        self.assertEqual(len(self.when(got, "hud") + self.when(got, "peek")), pipeline.HUD_RETRIES)
        self.assertEqual(self.session.settings.round, 5)
        self.assertEqual(self.events_of(got, "advice"), [])


@unittest.skipUnless(samples.samples_ok(OCCLUDED_ANTENNA), samples.samples_reason(OCCLUDED_ANTENNA))
class OccludedRealTest(unittest.TestCase):
    """真的 read_hud、read_round_crop 走一遍被挡住的读法：带上下黑边的截图，头像的天线挡住了「04」。三选一、认角色、读星数
    换成假的。完整读看见标记、数字读不出；交回的那一小块是去掉黑边后的画面上的像素；之后隔 occluded_s 在同一幅画面上认字，
    隔 occluded_full_s 才再完整读；不认选角界面、不做整幅识别；窗口大小变了照常完整读，那一小块重新定。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.img = cv2.imdecode(np.fromfile(str(SAMPLES / OCCLUDED_ANTENNA), dtype=np.uint8), cv2.IMREAD_COLOR)

    def setUp(self):
        self.log = []
        real_hud, real_crop = pipeline.read_hud, pipeline.read_round_crop

        def hud(img, db, full=False, **kw):
            self.log.append("hud")
            return real_hud(img, db, full=full)

        def crop(img, box):
            self.log.append(("peek", img.shape[:2], tuple(box)))
            return real_crop(img, box)

        def forbid(name):
            def call(*args, **kwargs):
                self.log.append(name)
                return Pregame() if name == "pregame" else False
            return call

        patches = [
            mock.patch.object(pipeline, "read_hud", side_effect=hud),
            mock.patch.object(pipeline, "read_round_crop", side_effect=crop),
            mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False),
            mock.patch.object(pipeline, "is_select_screen", side_effect=forbid("pick")),
            mock.patch.object(pipeline, "read_pregame", side_effect=forbid("pregame")),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, who, **kw: None),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.clock = [0.0]
        self.session = Session(self.db, Settings(hero="凛", map="异变图书馆", map_source="tasks", map_confidence="high"))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def run_frames(self, img, start, end):
        for i in range(10000):
            t = round(start + 0.5 * i, 2)
            if t > end:
                break
            for n in range(13):
                at = round(t + 0.01 * n, 2)
                self.clock[0] = at
                self.proc.process(img, at)
                self.assertEqual(self.proc.errors, [])
                if not self.proc.pending:
                    break

    def test_occluded_reads(self):
        self.run_frames(self.img, 0.0, 6.5)
        peeks = [item for item in self.log if isinstance(item, tuple)]
        self.assertEqual(self.log.count("hud"), 2)  # 第 0、6 秒
        self.assertGreaterEqual(len(peeks), 9)
        self.assertNotIn("pick", self.log)
        self.assertNotIn("pregame", self.log)
        self.assertEqual({shape for _, shape, _ in peeks}, {(1440, 2560)})  # 去掉黑边后的那一幅
        x0, y0, x1, y1 = peeks[0][2]
        self.assertTrue(0 <= x0 < x1 <= 2560 and 0 <= y0 < y1 <= 1440)
        self.assertIsNone(self.session.settings.round)  # 这张图上数字读不出：什么都不交
        self.assertEqual(self.proc._occluded[1], (1440, 2560))
        self.assertGreater(self.proc._mark_seen_at, float("-inf"))

    def test_resized(self):
        self.run_frames(self.img, 0.0, 0.0)
        before = self.proc._occluded[0]
        self.log.clear()
        small = cv2.resize(self.img, (1920, 1200), interpolation=cv2.INTER_AREA)
        self.run_frames(small, 6.0, 6.5)
        self.assertEqual(self.log[0], "hud")
        self.assertEqual(self.proc._occluded[1], (1080, 1920))
        self.assertNotEqual(self.proc._occluded[0], before)
        self.assertEqual({item[1] for item in self.log if isinstance(item, tuple)}, {(1080, 1920)})


class MapBlockTest(_FakeReads):
    """地图未知时读轮次连带读画面上方整块（read_hud 的 full）：任务栏开着（task_panel_visible）时每次都连带读，没开时隔
    block_s（[watch] map_block_ms）才连带读一次，其余几次只读轮次；连带读到了任务（按颜色认不出来的任务列表开着）以后，
    读到没有为止每次都连带读。识别函数换成假的（_FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.fulls, self.tasks, self.listed = [], False, False

        def hud(img, db, full=False, **kw):
            self.log.append("hud")
            self.fulls.append((self.clock[0], full))
            # listed：按颜色认不出来的任务列表开着，连带读整块时读得到任务
            return Hud(round=self.round, tasks_seen=["任务"] if full and self.listed else [])

        for patch in (mock.patch.object(pipeline, "read_hud", side_effect=hud),
                      mock.patch.object(pipeline, "task_panel_visible", side_effect=lambda img: self.tasks)):
            patch.start()
            self.addCleanup(patch.stop)
        self.session.set_map(None)

    def test_closed_panel_every_block_s(self):
        self.assertEqual(self.proc.block_s, 2 * self.proc.hud_s)
        for t in (0.0, 3.0, 6.0, 9.0, 12.0, 15.0):
            self.settle(t)
        self.assertEqual(self.fulls, [(0.0, True), (3.0, False), (6.0, True), (9.0, False), (12.0, True), (15.0, False)])

    def test_open_panel_every_read(self):
        self.tasks = True
        for t in (0.0, 3.0, 6.0):
            self.settle(t)
        self.assertEqual(self.fulls, [(0.0, True), (3.0, True), (6.0, True)])

    def test_tasks_seen_keeps_reading(self):
        # 任务列表在 0 秒那次之后打开：6 秒那次（隔够了 block_s）读到任务，之后每次都连带读；12 秒前关掉，12 秒那次读到没有，
        # 回到隔 block_s 一次
        self.settle(0.0)
        self.settle(3.0)
        self.listed = True
        for t in (6.0, 9.0):
            self.settle(t)
        self.listed = False
        for t in (12.0, 15.0, 18.0):
            self.settle(t)
        self.assertEqual(self.fulls, [(0.0, True), (3.0, False), (6.0, True), (9.0, True), (12.0, True), (15.0, False),
                                      (18.0, True)])


class SelectBoardTest(unittest.TestCase):
    """两个客户端同时识别时两路共用的「谁的三选一正显示着」（SelectBoard）：自己的不算，记号超过 BOARD_STALE_S 没更新不算，
    没显示时擦掉。"""

    def test_note_and_others(self):
        board = pipeline.SelectBoard()
        a, b = object(), object()
        self.assertFalse(board.others(a, 0.0))
        board.note(b, True, 10.0)
        self.assertTrue(board.others(a, 10.0 + pipeline.BOARD_STALE_S - 0.01))
        self.assertFalse(board.others(b, 11.0))
        self.assertFalse(board.others(a, 10.0 + pipeline.BOARD_STALE_S))
        board.note(b, True, 12.0)
        board.note(b, False, 12.5)
        self.assertFalse(board.others(a, 12.5))


class DualYieldTest(_FakeReads):
    """两个客户端同时识别：另一路的三选一显示着（两路共用的 SelectBoard 上记着）时，自己暂停要做文字识别的读数（读轮次、
    认选角界面、读队友、读进度条、读星币、离开对局后的整幅识别），在对局里时照旧按 hud_ms 认角色、读星数（模板比对），不设
    pending；看自己有没有三选一照旧，自己的也显示着时各读各的。那边关掉、记号过了 BOARD_STALE_S 没更新、自己暂停
    （watch_idle）以后不再让路或不再记着。识别函数换成假的（_FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.board = pipeline.SelectBoard()
        self.proc.board = self.board
        self.other = object()  # 另一路的识别流程（这里只在牌子上记它）
        self.session.game.seen_hero = "凛"
        self.settle(0.0)
        self.settle(3.0)

    def test_yield_then_resume(self):
        got = []
        for i in range(20):
            t = round(6.0 + 0.5 * i, 2)
            self.board.note(self.other, True, t)
            got.append((t, self.frame(t)[1]))
            self.assertFalse(self.proc.pending)
        names = [name for _, log in got for name in log]
        for name in ("hud", "pick", "players", "progress", "coins", "pregame"):
            self.assertNotIn(name, names)
        self.assertEqual([t for t, log in got if "stars" in log], [6.0, 9.0, 12.0, 15.0])
        self.board.note(self.other, False, 16.0)
        self.assertEqual(self.frame(16.0)[1], ["hud"])  # 那边关掉了：马上接着读

    def test_own_select_while_yielding(self):
        self.board.note(self.other, True, 6.0)
        self.seen = offer(OFFER_A)
        events, log = self.frame(6.0)
        self.assertEqual(log, ["select", "stars"])
        self.assertEqual(self.advice(events), [(5, 9)])
        self.assertTrue(self.board.others(self.other, 6.0))  # 自己也记上了：另一路据此让路
        self.board.note(self.other, True, 6.15)
        self.assertEqual(self.frame(6.15)[1][:2], ["hud", "progress"])  # 两路都显示着：各读各的，照常核对

    def test_not_yielded_after_advice(self):
        """自己的推荐给过以后牌子上不再记着：玩家一直不选时，另一路不用一直让路。"""
        self.seen = offer(OFFER_A)
        events, _ = self.frame(6.0)
        self.assertEqual(self.advice(events), [(5, 9)])
        self.assertTrue(self.board.others(self.other, 6.0))  # 给推荐的这一帧还记着
        self.frame(6.15)
        self.assertFalse(self.board.others(self.other, 6.15))
        self.seen = None
        self.frame(6.3)
        self.assertFalse(self.board.others(self.other, 6.3))

    def test_stale_mark_not_yielded(self):
        self.board.note(self.other, True, 6.0)
        self.assertNotIn("hud", self.frame(6.0)[1])
        self.assertEqual(self.frame(6.0 + pipeline.BOARD_STALE_S)[1], ["hud"])  # 那一路停了、卡住了：不再让路

    def test_watch_idle_clears(self):
        self.seen = offer(OFFER_A)
        self.frame(6.0)
        self.assertTrue(self.board.others(self.other, 6.1))
        self.proc.watch_idle()
        self.assertFalse(self.board.others(self.other, 6.1))


class FullscreenPlatformTest(unittest.TestCase):
    """查独占全屏时只看这个识别流程跟的端（FrameProcessor 的 platform）：None 时和原来一样按游戏平台设置查。"""

    def test_fullscreen_uses_platform(self):
        from advisor.vision import capture

        db = load_db()
        session = Session(db, Settings())
        img = np.zeros((1080, 1920, 3), np.uint8)
        for platform in (None, capture.STEAM, capture.TAPTAP):
            with self.subTest(platform=platform):
                proc = pipeline.FrameProcessor(db, session, platform=platform)
                with mock.patch.object(capture, "exclusive_fullscreen", return_value=False) as full:
                    proc._check_window(img, 0.0, [])
                full.assert_called_once_with(platform=platform)


class WatchRulesTest(unittest.TestCase):
    def test_idle_ms(self):
        """平时看画面的间隔 0.5 秒（三选一画面出来最多晚半秒看到），低配模式乘 2 即 1 秒。"""
        watch = load_db().rules["watch"]
        self.assertEqual((watch["idle_ms"], watch["idle_ms"] * watch["lowend_factor"]), (500, 1000))
        self.assertNotIn("select_full_ms", watch)  # 推荐给出以后只盯绿框，不再定时整页重认

    def test_coins_ms_written_out(self):
        """读星币数的间隔写在 rules.toml [watch] 里，取值和代码里的默认值相同（写上以后行为不变），识别流程按它读。"""
        db = load_db()
        self.assertEqual(db.rules["watch"]["coins_ms"], pipeline.COINS_MS)
        self.assertEqual(pipeline.FrameProcessor(db, Session(db, Settings())).coins_s, pipeline.COINS_MS / 1000)

    def test_occlusion_and_block_written_out(self):
        """轮次被挡住时多久看一眼、多久完整读一次，任务栏没开时多久连带读一次整块，写在 rules.toml [watch] 里，取值和代码里的
        默认值相同；识别流程按它读，改了照改的读。"""
        db = load_db()
        watch = db.rules["watch"]
        self.assertEqual((watch["occluded_recheck_ms"], watch["occluded_full_ms"], watch["map_block_ms"]),
                         (pipeline.OCCLUDED_RECHECK_MS, pipeline.OCCLUDED_FULL_MS, pipeline.MAP_BLOCK_MS))
        proc = pipeline.FrameProcessor(db, Session(db, Settings()))
        self.assertEqual((proc.occluded_s, proc.occluded_full_s, proc.block_s),
                         (watch["occluded_recheck_ms"] / 1000, watch["occluded_full_ms"] / 1000,
                          watch["map_block_ms"] / 1000))
        changed = dataclasses.replace(db, rules={**db.rules, "watch": {
            **watch, "occluded_recheck_ms": 250, "occluded_full_ms": 9000, "map_block_ms": 3000}})
        proc = pipeline.FrameProcessor(changed, Session(changed, Settings()))
        self.assertEqual((proc.occluded_s, proc.occluded_full_s, proc.block_s), (0.25, 9.0, 3.0))

    def test_blank_and_retry_gap_written_out(self):
        """连标记都没看见时多久完整读一次、这一局近多久内读到过轮次才算「最近」，写在 rules.toml [watch] 里，取值和代码里的
        默认值相同；识别流程按它读，改了照改的读。blank_full_ms 跟着低配模式乘倍数，round_retry_gap_ms 不乘。"""
        db = load_db()
        watch = db.rules["watch"]
        self.assertEqual((watch["blank_full_ms"], watch["round_retry_gap_ms"]),
                         (pipeline.BLANK_FULL_MS, pipeline.ROUND_RETRY_GAP_MS))
        proc = pipeline.FrameProcessor(db, Session(db, Settings()))
        self.assertEqual((proc.blank_full_s, proc.round_retry_gap_s),
                         (watch["blank_full_ms"] / 1000, watch["round_retry_gap_ms"] / 1000))
        proc.set_pace(2)
        self.assertEqual((proc.blank_full_s, proc.round_retry_gap_s),
                         (watch["blank_full_ms"] / 1000 * 2, watch["round_retry_gap_ms"] / 1000))
        changed = dataclasses.replace(db, rules={**db.rules, "watch": {
            **watch, "blank_full_ms": 9000, "round_retry_gap_ms": 30000}})
        proc = pipeline.FrameProcessor(changed, Session(changed, Settings()))
        self.assertEqual((proc.blank_full_s, proc.round_retry_gap_s), (9.0, 30.0))

    def test_select_ms_written_out(self):
        """不在对局里时多久看一眼下方区域（选角界面、对局外的画面）写在 rules.toml [watch] 里，取值和代码里的默认值相同
        （1 秒）；识别流程按它读，改了照改的读。"""
        db = load_db()
        self.assertEqual((db.rules["watch"]["select_ms"], pipeline.SELECT_MS), (1000, 1000))
        self.assertEqual(pipeline.FrameProcessor(db, Session(db, Settings())).select_s, 1.0)
        changed = dataclasses.replace(db, rules={**db.rules, "watch": {**db.rules["watch"], "select_ms": 1500}})
        self.assertEqual(pipeline.FrameProcessor(changed, Session(changed, Settings())).select_s, 1.5)

    def test_pick_grace_written_out(self):
        """对局中看见轮次标记以后第二次起读不到时，离那次标记多久以内先不判选角界面，写在 rules.toml [watch] 里，取值和代码里的
        默认值相同（10 秒）；识别流程按它读，改了照改的读；低配模式乘倍数。"""
        db = load_db()
        self.assertEqual((db.rules["watch"]["pick_grace_ms"], pipeline.PICK_GRACE_MS), (10000, 10000))
        proc = pipeline.FrameProcessor(db, Session(db, Settings()))
        self.assertEqual(proc.pick_grace_s, 10.0)
        proc.set_pace(2)
        self.assertEqual(proc.pick_grace_s, 20.0)
        changed = dataclasses.replace(db, rules={**db.rules, "watch": {**db.rules["watch"], "pick_grace_ms": 6000}})
        self.assertEqual(pipeline.FrameProcessor(changed, Session(changed, Settings())).pick_grace_s, 6.0)

    def test_select_fast_written_out(self):
        """三选一快读的开关写在 rules.toml [watch] 里、默认开；识别流程按它读，改成 false 照改的读；低配模式不动它。"""
        db = load_db()
        self.assertIs(db.rules["watch"]["select_fast"], True)
        proc = pipeline.FrameProcessor(db, Session(db, Settings()))
        self.assertTrue(proc.select_fast)
        proc.set_pace(2)
        self.assertTrue(proc.select_fast)
        changed = dataclasses.replace(db, rules={**db.rules, "watch": {**db.rules["watch"], "select_fast": False}})
        self.assertFalse(pipeline.FrameProcessor(changed, Session(changed, Settings())).select_fast)


# ---------------------------------------------------------------- 对局中少找字

# 同一窗口大小（2560x1057）的两次三选一：先完整读 A、记下位置，再要认 B（刷新以后，没有刷新按钮、选中了一个）时快读
FAST_A = "20261002_144845_chipselect_levelup_star1_2560x1057.jpg"
FAST_B = "20261002_150218_chipselect_selected_after_reroll_no_reroll_button_2560x1057.jpg"
# 2560x1369、进度条当前格不同的几张（标注的 progress）：第 1、4、8、11、12 格
PROGRESS_SHOTS = {
    1: "20261003_124642_hud_round1_skull_cell_2560x1369.jpg",
    4: "20261003_125650_chips_panel_two_chips_al_2560x1369.jpg",
    8: "20261003_130926_hud_round8_nameplate_hp_2560x1369.jpg",
    11: "20261003_132016_chips_panel_eight_chips_2560x1369.jpg",
    12: "20261003_132306_card_give_al_skill_2560x1369.jpg",
}


def load_native(name: str) -> np.ndarray:
    """原尺寸读一张样本（load 会缩到 1920 宽）。"""
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


@unittest.skipUnless(samples.samples_ok(FAST_A, FAST_B), samples.samples_reason(FAST_A, FAST_B))
class SelectFastTest(unittest.TestCase):
    """三选一快读接进识别流程（FrameProcessor._select_read），真的文字识别：完整读认出三选一以后记下位置，下一次要认三张卡时
    （同样大小的画面）先快读，读成了就不再完整读，识别记录 how 为 fast、读数和完整读一样；快读退回时当场完整读、重记位置，
    识别记录另有 fast_miss；完整读记不下位置时留着原来那一份；开关关着、按热键的那一帧、补认那一次照旧完整读。
    读轮次、进度条等换成假的。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.images = {name: load_native(name) for name in (FAST_A, FAST_B)}
        info: dict = {}
        cls.full_b = chip_select.read_chip_select(cls.images[FAST_B], cls.db, info=info)
        cls.layout_b = chip_select.select_layout(cls.images[FAST_B].shape, cls.full_b, info["lines"])

    def setUp(self):
        self.session = Session(self.db, Settings(hero="凛", round=3, star=1, star_read=True))
        self.calls: list[str] = []
        real_full, real_fast = pipeline.read_chip_select, pipeline.read_chip_select_fast

        def full(img, db, **kw):
            self.calls.append("full")
            return real_full(img, db, **kw)

        def fast(img, db, layout):
            self.calls.append("fast")
            return real_fast(img, db, layout)

        patches = [
            mock.patch.object(pipeline, "read_chip_select", side_effect=full),
            mock.patch.object(pipeline, "read_chip_select_fast", side_effect=fast),
            mock.patch.object(pipeline, "read_hud", return_value=Hud(round=3)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero="凛")),
            mock.patch.object(pipeline, "read_own_stars", return_value=None),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.proc = pipeline.FrameProcessor(self.db, self.session)

    def records(self) -> list[dict]:
        return [r for r in self.session.recent_events(600) if r["kind"] == "read:select"]

    def test_next_offer_read_fast(self):
        self.proc.process(self.images[FAST_A], 0.0)
        self.assertEqual(self.calls, ["full"])
        self.assertIsNotNone(self.proc._select_layout)
        self.proc.process(self.images[FAST_B], 0.3)
        self.assertEqual(self.calls, ["full", "fast"])
        first, last = self.records()[0], self.records()[-1]
        self.assertNotIn("how", first)
        self.assertEqual(last.get("how"), "fast")
        self.assertNotIn("fast_miss", last)
        cached = self.proc._select_cache[0]
        for key in ("names", "raw", "rarity", "rerolls", "probabilities", "selected", "team"):
            self.assertEqual(getattr(cached, key), getattr(self.full_b, key), key)
        self.assertEqual(self.session._foreign, 0)

    def test_fallback_reads_full_and_relearns(self):
        self.proc.process(self.images[FAST_A], 0.0)
        first = self.proc._select_layout

        def refuse(img, db, layout):
            self.calls.append("fast")
            return None, chip_select.FAST_NO_TITLE

        with mock.patch.object(pipeline, "read_chip_select_fast", side_effect=refuse):
            self.proc.process(self.images[FAST_B], 0.3)
        self.assertEqual(self.calls, ["full", "fast", "full"])
        last = self.records()[-1]
        self.assertEqual(last.get("fast_miss"), chip_select.FAST_NO_TITLE)
        self.assertNotIn("how", last)
        self.assertEqual(self.proc._select_layout, self.layout_b or first)

    def test_fast_error_reads_full(self):
        """快读出错：这一次改用完整读（三选一照样认出），那一份位置丢掉、完整读重记，出错写进错误记录、识别记录的 fast_miss
        写明出错，不报成认三选一出错。"""
        self.proc.process(self.images[FAST_A], 0.0)
        with mock.patch.object(pipeline, "read_chip_select_fast", side_effect=RuntimeError("快读出错")), \
                mock.patch.object(pipeline, "log_error") as logged:
            self.proc.process(self.images[FAST_B], 0.3)
        self.assertEqual(self.calls, ["full", "full"])
        self.assertEqual(self.proc.errors, [])
        self.assertEqual(logged.call_count, 1)
        self.assertEqual(self.records()[-1].get("fast_miss"), "出错：RuntimeError")
        self.assertEqual(self.proc._select_cache[0].names, self.full_b.names)
        self.assertEqual(self.proc._select_layout, self.layout_b)

    def test_layout_kept_when_not_learnable(self):
        """完整读记不下位置（读得不完整、卡片还在滑进来等，select_layout 交回 None）时留着原来那一份。"""
        self.proc.process(self.images[FAST_A], 0.0)
        first = self.proc._select_layout
        with mock.patch.object(pipeline, "read_chip_select_fast", return_value=(None, chip_select.FAST_NAME)), \
                mock.patch.object(pipeline, "select_layout", return_value=None):
            self.proc.process(self.images[FAST_B], 0.3)
        self.assertIs(self.proc._select_layout, first)

    def test_switch_off_reads_full(self):
        self.proc.select_fast = False
        self.proc.process(self.images[FAST_A], 0.0)
        self.proc.process(self.images[FAST_B], 0.3)
        self.assertEqual(self.calls, ["full", "full"])
        self.assertFalse([r for r in self.records() if "how" in r or "fast_miss" in r])

    def test_hotkey_reads_full(self):
        self.proc.process(self.images[FAST_A], 0.0)
        self.proc.process(self.images[FAST_B], 0.3, True)
        self.assertEqual(self.calls, ["full", "full"])

    def test_reread_and_other_size_read_full(self):
        """补认到点的那一次、画面大小和记下位置时不同的，不快读。"""
        self.proc.process(self.images[FAST_A], 0.0)
        img = self.images[FAST_B]
        seen, how = self.proc._select_read(img, False, True, {})
        self.assertEqual((self.calls, how), (["full", "full"], {}))
        small = cv2.resize(img, (1920, 793), interpolation=cv2.INTER_AREA)
        self.proc._select_read(small, False, False, {})
        self.assertEqual(self.calls, ["full", "full", "full"])


class RoundQuickTest(_FakeReads):
    """读轮次先只认字（FrameProcessor._round_quick）：完整读读到了轮次、也看见了标记（交回标记左边那一小块 CROP）以后，下一次
    读轮次先在那一小块只认字（read_round_crop，记作「crop」，认出的数是 crop_value）；认出手上的轮次或往前一轮就当作读到了，
    不再完整读（识别记录 how 为 crop），这一轮其余几项照排；认不出、认出别的数、画面尺寸变了、按热键、要连带读任务栏时照旧
    完整读；离开对局时清掉。识别函数换成假的（_FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.crop_value = None

        def fake_hud(img, db, full=False, **kw):
            self.log.append("hud")
            seen = self.round is not None
            return Hud(round=self.round, round_mark=seen, round_crop=CROP if seen else None)

        def fake_crop(img, crop):
            self.log.append("crop")
            return self.crop_value

        for patch in (mock.patch.object(pipeline, "read_hud", side_effect=fake_hud),
                      mock.patch.object(pipeline, "read_round_crop", side_effect=fake_crop)):
            patch.start()
            self.addCleanup(patch.stop)

    def reads(self, t: float, img: np.ndarray | None = None) -> list[str]:
        """t 秒处这一轮读数做完（settle）调了哪些识别函数。"""
        if img is not None:
            self.img = img
        self.log.clear()
        self.settle(t)
        return list(self.log)

    def hud_records(self) -> list[dict]:
        return [r for r in self.session.recent_events(600) if r["kind"] == "read:hud"]

    def test_crop_instead_of_full(self):
        self.assertIn("hud", self.reads(0.0))
        self.assertEqual(self.proc._round_memo, (CROP, (1080, 1920)))
        self.crop_value = 5
        got = self.reads(3.0)
        self.assertEqual(got.count("crop"), 1)
        self.assertNotIn("hud", got)
        self.assertIn("progress", got)  # 和完整读到轮次时一样，接着排这一轮的其余几项
        records = self.hud_records()
        self.assertNotIn("how", records[0])
        self.assertEqual((records[-1]["round"], records[-1].get("how")), (5, "crop"))
        self.assertEqual(self.session.settings.round, 5)

    def test_next_round_taken(self):
        self.reads(0.0)
        self.crop_value = 6
        got = self.reads(3.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (1, 0))
        self.assertEqual(self.hud_records()[-1]["round"], 6)

    def test_other_value_reads_full(self):
        """认出别的数（跳了不止一轮、变小）、认不出：照旧完整读，不把别的数直接交给记账。"""
        self.reads(0.0)
        for i, value in enumerate((8, 4, None)):
            self.crop_value = value
            with self.subTest(value=value):
                got = self.reads(3.0 * (i + 1))
                self.assertEqual((got.count("crop"), got.count("hud")), (1, 1))
                self.assertNotIn("how", self.hud_records()[-1])
        self.assertEqual(self.session.settings.round, 5)

    def test_resized_reads_full(self):
        self.reads(0.0)
        self.crop_value = 5
        got = self.reads(3.0, np.full((900, 1600, 3), 90, np.uint8))
        self.assertEqual((got.count("crop"), got.count("hud")), (0, 1))
        self.assertEqual(self.proc._round_memo, (CROP, (900, 1600)))

    def test_hotkey_reads_full(self):
        self.reads(0.0)
        self.crop_value = 5
        self.log.clear()
        self.clock[0] = 3.0
        self.proc.process(self.img, 3.0, True)
        self.assertEqual((self.log.count("crop"), self.log.count("hud")), (0, 1))

    def test_block_due_reads_full(self):
        """地图未知、到了连带读整块的时候（full）照旧完整读；不连带读的那几次先只认字。"""
        self.session.set_map(None)
        self.reads(0.0)
        self.crop_value = 5
        self.assertEqual(self.reads(3.0).count("hud"), 0)
        got = self.reads(6.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (0, 1))

    def test_in_select_crop(self):
        """三选一上读左上角也先只认字，同样的接受规矩。"""
        self.reads(0.0)
        self.crop_value = 5
        self.log.clear()
        self.clock[0] = 3.0
        self.assertTrue(self.proc._read_hud(self.img, 3.0, [], in_select=True, progress=False))
        self.assertEqual(self.log, ["crop"])
        self.assertEqual(self.proc._hud_round, 5)

    def test_left_game_clears(self):
        self.reads(0.0)
        self.assertIsNotNone(self.proc._round_memo)
        self.proc._left_game()
        self.assertIsNone(self.proc._round_memo)
        self.crop_value = 5
        got = self.reads(3.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (0, 1))

    def test_full_read_after_game_s(self):
        """只认字看不见标记：离最近一次完整读看见标记满 after_game_s（30 秒）时这一次完整读，看见了标记从那时重新算。"""
        self.assertEqual(self.proc.after_game_s, 30.0)
        self.reads(0.0)
        self.crop_value = 5
        for t in range(3, 30, 3):
            with self.subTest(t=t):
                got = self.reads(float(t))
                self.assertEqual((got.count("crop"), got.count("hud")), (1, 0))
        got = self.reads(30.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (0, 1))
        self.assertEqual(self.proc._full_mark_at, 30.0)
        got = self.reads(33.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (1, 0))

    def test_no_mark_keeps_full_reads(self):
        """对局外的画面在那一小块上正好认出手上的轮次：满 after_game_s 的那一次完整读读不到轮次、看不见标记，接着认选角界面和
        对局外的画面（看见标记以后第一次读不到，不先不判）；之后不再只认字、也不再每 hud_ms 完整读，而是每 blank_full_s（6 秒）
        完整读一次（连标记都没看见；只认字那一小块认出的也算看见过标记，离那次不到 round_retry_gap_s，这一局还算正打着），
        直到完整读再看见标记。"""
        self.reads(0.0)
        self.crop_value = 5
        for t in range(3, 30, 3):
            self.reads(float(t))
        self.round = None
        got = self.reads(30.0)
        self.assertEqual((got.count("crop"), got.count("hud")), (0, 1))
        self.assertIn("pick", got)
        for t, want in ((33.0, 0), (36.0, 1), (39.0, 0)):
            with self.subTest(t=t):
                got = self.reads(t)
                self.assertEqual((got.count("crop"), got.count("hud")), (0, want))
        self.round = 5
        self.reads(42.0)  # 隔满 blank_full_s：完整读又看见了标记
        self.assertEqual(self.reads(45.0).count("crop"), 1)


class BlankFullTest(_FakeReads):
    """这一局正打着、刚看见过轮次时（FrameProcessor._mid_game），完整读连标记都没看见以后每 blank_full_s（6 秒）才再完整读
    一次（BLANK_FULL_MS）：只认字照旧每 hud_ms 一次，回到棋盘时只认字马上认出；按热键、连带读整块、三选一上照旧完整读。
    新一局还没读到轮次、本局已结束、离上次看见标记已满 round_retry_gap_s 时不等，照旧每 hud_ms 完整读；不等的时候第一遍
    读不出也照旧换大小重读（read_hud 的 retry，和等不等是同一个条件）。识别函数换成假的（_FakeReads）。"""

    def setUp(self):
        super().setUp()
        self.round, self.mark = None, False
        self.calls: list[float] = []  # 每次完整读的时刻
        self.retries: list[bool] = []  # 每次完整读交给 read_hud 的 retry（没给为真）

        def hud(img, db, full=False, **kw):
            self.calls.append(self.clock[0])
            self.retries.append(kw.get("retry", True))
            self.log.append("hud")
            return Hud(round=self.round, round_mark=self.mark, round_crop=CROP if self.mark else None)

        patch = mock.patch.object(pipeline, "read_hud", side_effect=hud)
        patch.start()
        self.addCleanup(patch.stop)

    def reads(self, t: float) -> list[str]:
        self.log.clear()
        self.settle(t)
        return list(self.log)

    def records(self) -> list[dict]:
        return [r for r in self.session.recent_events(600) if r["kind"] == "read:hud"]

    def playing(self) -> None:
        """第 0 秒完整读读到第 5 轮（没看见标记，不记那一小块）；之后读不到轮次、也没有标记（对决、过场等）。"""
        self.round = 5
        self.assertIn("hud", self.reads(0.0))
        self.assertEqual(self.session.settings.round, 5)
        self.round = None

    def test_new_game_no_wait(self):
        """新一局还没读到轮次（手上没有轮次）时不等：读不到轮次也每 hud_ms 完整读一次，第一遍读不出照旧换大小重读。"""
        for t in (0.0, 3.0, 6.0, 9.0):
            with self.subTest(t=t):
                self.assertIn("hud", self.reads(t))
        self.assertEqual(self.calls, [0.0, 3.0, 6.0, 9.0])
        self.assertEqual(self.retries, [True] * 4)
        self.assertNotIn("wait", [r.get("how") for r in self.records()])

    def test_wait_in_game(self):
        """对局中读到过轮次以后完整读什么都没读到：3 秒后这一帧不再完整读（识别记录 how 为 wait，数 hud_full_wait），和完整读
        什么都没读到时一样排后面几项（离看见标记满 pick_grace_s 时照常认选角界面）；满 6 秒才完整读，第一遍读不出不重读。"""
        self.playing()
        counted = []
        with mock.patch.object(pipeline.perfstats, "count", side_effect=counted.append):
            self.assertIn("hud", self.reads(3.0))  # 什么都没读到
            got = self.reads(6.0)
            self.assertNotIn("hud", got)
            self.assertEqual(self.records()[-1].get("how"), "wait")
            self.assertEqual(counted.count("hud_full_wait"), 1)
            self.assertIn("hud", self.reads(9.0))  # 满 blank_full_s：完整读
            got = self.reads(12.0)  # 又在等；离第 0 秒读到轮次满 pick_grace_s：照常认选角界面
            self.assertNotIn("hud", got)
            self.assertIn("pick", got)
        self.assertEqual(self.calls, [0.0, 3.0, 9.0])
        self.assertEqual(self.retries, [True, False, False])

    def test_no_wait_after_retry_gap(self):
        """离上次看见标记满 round_retry_gap_s 以后不再等：照旧每 hud_ms 完整读，第一遍读不出也照旧换大小重读（两样同一个
        条件，一起变）。"""
        self.proc.round_retry_gap_s = 12.0
        self.playing()
        for t in (3.0, 6.0, 9.0, 12.0, 15.0):
            self.reads(t)
        self.assertEqual(self.calls, [0.0, 3.0, 9.0, 12.0, 15.0])
        self.assertEqual(self.retries, [True, False, False, True, True])

    def test_no_wait_after_game_over(self):
        """本局已结束（对局外）时不等：照旧每 hud_ms 完整读，第一遍读不出照旧换大小重读。"""
        self.playing()
        self.session.game.game_over = True
        self.proc.hints = None  # 直接改了记账：下一次读提示时重取
        for t in (3.0, 6.0, 9.0):
            self.reads(t)
        self.assertEqual(self.calls, [0.0, 3.0, 6.0, 9.0])
        self.assertEqual(self.retries, [True] * 4)

    def test_full_read_sees_mark_restores_cadence(self):
        """完整读看见了标记：不再等。"""
        self.playing()
        self.reads(3.0)  # 什么都没读到
        self.reads(6.0)  # 不完整读
        self.mark = True
        self.assertIn("hud", self.reads(9.0))
        self.assertEqual(self.proc._full_blank_at, float("-inf"))
        self.mark = False
        # 看见了标记以后走「轮次被挡住」那一路（_occluded 隔 occluded_s 只看那一小块）：直接读一次轮次验证不再等 6 秒
        self.clock[0] = 12.0
        self.log.clear()
        self.proc._read_hud(self.img, 12.0, [])
        self.assertIn("hud", self.log)

    def test_full_read_sees_round_restores_cadence(self):
        """完整读读到了轮次：不再等，之后照旧每 hud_ms（3 秒）完整读一次。"""
        self.playing()
        self.reads(3.0)
        self.reads(6.0)
        self.round = 5
        self.assertIn("hud", self.reads(9.0))
        self.assertEqual(self.proc._full_blank_at, float("-inf"))
        self.round = None
        self.assertIn("hud", self.reads(12.0))
        self.assertEqual(self.calls, [0.0, 3.0, 9.0, 12.0])

    def test_crop_reads_round_during_wait(self):
        """回到棋盘时只认字就认出：完整读读到过轮次、看见过标记（记下标记左边那一小块）以后，对决画面上完整读什么都没读到；
        之后先只认字，认不出时才算等的那一次（数 hud_full_wait）；离上次完整读不到 blank_full_s 时回到棋盘，只认字认出手上的
        轮次就当作读到了，不等、不完整读，也不数 hud_full_wait。"""
        board = [True]

        def crop(img, box):
            self.log.append("crop")
            return 5 if board[0] else None

        counted = []
        with mock.patch.object(pipeline, "read_round_crop", side_effect=crop), \
                mock.patch.object(pipeline.perfstats, "count", side_effect=counted.append):
            self.round, self.mark = 5, True
            self.reads(0.0)  # 棋盘：完整读读到第 5 轮、看见标记，记下那一小块
            self.round, self.mark, board[0] = None, False, False
            self.assertEqual(self.reads(3.0).count("hud"), 1)  # 对决：只认字认不出，完整读什么都没读到
            got = self.reads(6.0)  # 还在对决：只认字认不出，不到 blank_full_s 不完整读，算等的一次
            self.assertEqual((got.count("crop"), got.count("hud"), counted.count("hud_full_wait")), (1, 0, 1))
            self.assertEqual(self.reads(9.0).count("hud"), 1)  # 满 blank_full_s：完整读，还是什么都没读到
            board[0] = True
            got = self.reads(12.0)  # 回到棋盘：离上次完整读才 3 秒，只认字认出第 5 轮
            self.assertEqual((got.count("crop"), got.count("hud"), counted.count("hud_full_wait")), (1, 0, 1))
        self.assertEqual(self.calls, [0.0, 3.0, 9.0])
        self.assertEqual(self.proc._full_blank_at, float("-inf"))
        self.assertEqual((self.records()[-1]["round"], self.records()[-1].get("how")), (5, "crop"))
        self.assertEqual(self.session.settings.round, 5)

    def test_select_mark_ends_wait(self):
        """三选一上完整读看见了标记（数字被挡住）：之前那次什么都没读到的不再算数。之后照被挡住的读法隔 occluded_full_s
        完整读一次，比 blank_full_s 短时也照它读，不因为那次什么都没读到而等。"""
        self.proc.occluded_full_s = 3.0
        self.playing()
        self.reads(3.0)  # 什么都没读到
        self.mark = True
        self.clock[0] = 4.0
        self.proc._read_hud(self.img, 4.0, [], in_select=True, progress=False)
        self.assertEqual(self.proc._full_blank_at, float("-inf"))
        self.assertIn("hud", self.reads(7.0))  # 离三选一上那次 3 秒：完整读
        self.assertEqual(self.calls, [0.0, 3.0, 4.0, 7.0])

    def test_hotkey_reads_full(self):
        """按热键的那一次照旧完整读，不等 blank_full_s，第一遍读不出照旧重读。"""
        self.playing()
        self.reads(3.0)  # 什么都没读到
        self.clock[0] = 6.0
        self.log.clear()
        self.proc.process(self.img, 6.0, True)
        self.assertIn("hud", self.log)
        self.assertEqual(self.retries[-1], True)

    def test_block_due_reads_full(self):
        """地图未知、到了连带读整块（full）时照旧完整读，读轮次那一部分照样不重读。"""
        self.playing()
        self.reads(3.0)  # 什么都没读到
        self.session.set_map(None)
        self.assertIn("hud", self.reads(6.0))
        self.assertEqual(self.retries[-1], False)

    def test_select_reads_full(self):
        """三选一上读左上角照旧完整读（只管不在三选一时的自动读），第一遍读不出照旧重读。"""
        self.playing()
        self.reads(3.0)  # 什么都没读到
        self.clock[0] = 6.0
        self.log.clear()
        self.assertTrue(self.proc._read_hud(self.img, 6.0, [], in_select=True, progress=False))
        self.assertIn("hud", self.log)
        self.assertEqual(self.retries[-1], True)


class HudRetryArgumentTest(unittest.TestCase):
    """完整读轮次时传不传 retry=False（见 FrameProcessor._read_hud、_mid_game）：这一局近一分钟内看见过轮次标记、手上有轮次、
    本局没结束、不在三选一、不是按热键时不再换大小多认两遍，其余照旧。"""

    def read(self, *, round_now=3, game_over=False, mark_seen=5.0, now=10.0, in_select=False, force=False):
        db = load_db()
        session = Session(db, Settings(hero="凛", round=round_now))
        session.game.game_over = game_over
        proc = pipeline.FrameProcessor(db, session)
        proc._mark_seen_at = mark_seen
        seen = []

        def hud(img, db, full=False, **kw):
            seen.append(kw.get("retry", True))
            return Hud()

        with mock.patch.object(pipeline, "read_hud", side_effect=hud):
            proc._read_hud(np.full((1080, 1920, 3), 90, np.uint8), now, [],
                           in_select=in_select, force=force, progress=False)
        return seen

    def test_no_retry_with_recent_round(self):
        """手上有轮次、近一分钟内看见过标记：不再多认两遍。"""
        self.assertEqual(self.read(), [False])

    def test_retry_without_round(self):
        """手上有没轮次：照旧多认两遍。"""
        self.assertEqual(self.read(round_now=None), [True])

    def test_retry_after_gap(self):
        """离最近一次看见标记超过一分钟：照旧多认两遍。"""
        self.assertEqual(self.read(mark_seen=10.0 - 60.001), [True])

    def test_retry_in_select(self):
        """三选一上照旧多认两遍（读轮次是给推荐核对用的）。"""
        self.assertEqual(self.read(in_select=True), [True])

    def test_retry_on_hotkey(self):
        """按热键照旧多认两遍。"""
        self.assertEqual(self.read(force=True), [True])

    def test_retry_when_game_over(self):
        """本局已结束：照旧多认两遍。"""
        self.assertEqual(self.read(game_over=True), [True])


@unittest.skipUnless(samples.samples_ok(*PROGRESS_SHOTS.values()), samples.samples_reason(*PROGRESS_SHOTS.values()))
class ProgressLookTest(unittest.TestCase):
    """进度条那一块的样子（hud.progress_look、same_progress_look）够灵敏：进度不同的两张都算变了，蓝色当前格挪一格（合成）也算
    变了；同一张加录像压缩那种程度的噪声算没变；进度条本体以外露着的场景变了（合成）算没变；看不到蓝色当前格时一律不算一样。"""

    @classmethod
    def setUpClass(cls):
        cls.images = {p: load_native(name) for p, name in PROGRESS_SHOTS.items()}

    def test_different_progress_changed(self):
        for (p, a), (q, b) in itertools.combinations(self.images.items(), 2):
            with self.subTest(p=p, q=q):
                self.assertFalse(same_progress_look(progress_look(a), progress_look(b)))

    def test_noise_same(self):
        rng = np.random.default_rng(7)
        for p, img in self.images.items():
            h, w = img.shape[:2]
            noisy = np.clip(img + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
            jpeg = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1], cv2.IMREAD_COLOR)
            scaled = cv2.resize(cv2.resize(img, (w * 3 // 4, h * 3 // 4), interpolation=cv2.INTER_AREA), (w, h),
                                interpolation=cv2.INTER_LINEAR)
            for kind, other in (("noise", noisy), ("jpeg", jpeg), ("scaled", scaled)):
                with self.subTest(p=p, kind=kind):
                    self.assertTrue(same_progress_look(progress_look(img), progress_look(other)))

    def test_blue_cell_moved_changed(self):
        """把蓝色当前格和右边一格对调（当前格往前挪一格）：只有这两格变了，也算变了。只用画面上只有一块蓝色的几张。"""
        for p in (1, 4, 8):
            img = self.images[p]
            h, w = img.shape[:2]
            unit = chip_select.ui_scale(w, h)
            (x, y), = hud_mod._blue_cells(img, hud_mod.progress_box(w, h))
            x0, y0 = round(x - 35 * unit), round(y - 19 * unit)
            x1, y1, d = round(x + 35 * unit), round(y + 19 * unit), round(73 * unit)
            moved = img.copy()
            moved[y0:y1, x0:x1], moved[y0:y1, x0 + d:x1 + d] = img[y0:y1, x0 + d:x1 + d], img[y0:y1, x0:x1]
            with self.subTest(p=p):
                self.assertFalse(same_progress_look(progress_look(img), progress_look(moved)))
                self.assertTrue(same_progress_look(progress_look(img), progress_look(img.copy())))

    def test_scene_around_bar_ignored(self):
        """progress_box 里进度条本体以外的地方（上下两条、两头圆角外露着场景的地方）换成另一张截图的：进度条没变、只是背后的
        场景变了，算没变；反过来只把本体换成进度不同的另一张的，算变了。配对的两张进度条两头的样子一样（同在第 10 格以前或
        以后：第 10 格以后两头的蓝色也可能算成当前格）。"""
        for p, q in ((1, 4), (8, 1), (11, 12)):
            img, other = self.images[p], self.images[q]
            h, w = img.shape[:2]
            unit = chip_select.ui_scale(w, h)
            box = hud_mod.progress_box(w, h)
            x0, y0, x1, y1 = (int(round(v)) for v in box)
            body = np.zeros((h, w), bool)
            body[int(round(box[1] + hud_mod.PROGRESS_LOOK_TOP * unit)):int(round(box[1] + hud_mod.PROGRESS_LOOK_BOTTOM * unit)),
                 int(round(box[0] + hud_mod.PROGRESS_LOOK_INSET * unit)):int(round(box[2] - hud_mod.PROGRESS_LOOK_INSET * unit))] = True
            around = np.zeros((h, w), bool)
            around[max(0, y0):y1, max(0, x0):x1] = True
            around &= ~body
            scene, swapped = img.copy(), img.copy()
            scene[around], swapped[body] = other[around], other[body]
            with self.subTest(p=p, q=q):
                # 换上的那一圈和原来的差得很多，不然这一条没有意义
                changed = int((np.abs(img.astype(np.int16) - other)[around].max(axis=-1) > 40).sum())
                self.assertGreater(changed, int(around.sum()) // 4)
                self.assertTrue(same_progress_look(progress_look(img), progress_look(scene)))
                self.assertFalse(same_progress_look(progress_look(img), progress_look(swapped)))

    def test_no_blue_never_same(self):
        gray = np.full((1080, 1920, 3), 90, np.uint8)
        self.assertFalse(same_progress_look(progress_look(gray), progress_look(gray)))
        self.assertFalse(same_progress_look(None, progress_look(self.images[8])))


@unittest.skipUnless(samples.samples_ok(PROGRESS_SHOTS[1], PROGRESS_SHOTS[8]),
                     samples.samples_reason(PROGRESS_SHOTS[1], PROGRESS_SHOTS[8]))
class ProgressReuseTest(_FakeReads):
    """进度条没变就不认字（FrameProcessor._read_progress）：真的对局截图（进度条第 8 格），读进度条换成假的（读数 current）。
    记账采用了这个数以后，那一块和上次认字时一样就不认字、把上次的数原样再交（「最近一次读到进度」的时刻跟着更新，识别记录
    reused 为 true）；记账在等再读一次、那一块变了、画面尺寸变了、隔了 progress_s 以上、三选一上时照常认字。"""

    def setUp(self):
        super().setUp()
        self.img, self.current = load_native(PROGRESS_SHOTS[8]), 8

    def reads(self, t: float, img: np.ndarray | None = None) -> list[str]:
        if img is not None:
            self.img = img
        self.log.clear()
        self.settle(t)
        return list(self.log)

    def adopted(self) -> None:
        """读两轮（第 0、3 秒）：记账连读两次采用第 8 格。"""
        self.assertIn("progress", self.reads(0.0))
        self.assertIn("progress", self.reads(3.0))  # 记账在等再读一次：照常认字
        self.assertEqual(self.session.game.progress, 8)

    def test_unchanged_reused(self):
        self.adopted()
        got = self.reads(6.0)
        self.assertIn("hud", got)
        self.assertNotIn("progress", got)
        record = [r for r in self.session.recent_events(600) if r["kind"] == "read:progress"][-1]
        self.assertEqual((record["current"], record.get("reused")), (8, True))
        self.assertEqual(self.session.judge._last_progress_read, (8, 6.0))
        self.assertNotIn("progress", self.reads(9.0))
        self.assertIn("progress", self.reads(13.5))  # 离上次认字（第 3 秒）满 progress_s（10 秒）：照常认一次

    def test_pending_reads(self):
        self.adopted()
        self.session.game._progress_pending = 9  # 读到了和已采用的不同的格，在等再读一次
        self.assertIn("progress", self.reads(6.0))

    def test_cap_needs_two_reads(self):
        """红叉（进度上限）也要连读两次才采用：认字读到了红叉、记账还没采用时，那一块没变也不沿用，下一轮照常认字（不会一次认字
        加一次沿用就凑成「连读两次」）；采用以后照常沿用。"""
        self.adopted()
        cap = [17]

        def fake_progress(img):
            self.log.append("progress")
            return Progress(self.current, cap[0])

        with mock.patch.object(pipeline, "read_progress", side_effect=fake_progress):
            # 换一张图（那一块变了）：照常认字，第一次读到红叉，记账先不采用
            self.assertIn("progress", self.reads(6.0, load_native(PROGRESS_SHOTS[1])))
            self.assertIsNone(self.session.game.progress_cap)
            self.assertIn("progress", self.reads(9.0))  # 那一块没变，但读到过红叉、记账还没采用：照常认字
            self.assertEqual(self.session.game.progress_cap, 17)
            self.assertNotIn("progress", self.reads(12.0))  # 采用以后照常沿用

    def test_changed_or_resized_reads(self):
        self.adopted()
        self.assertIn("progress", self.reads(6.0, load_native(PROGRESS_SHOTS[1])))
        self.assertIn("progress", self.reads(9.0, cv2.resize(load_native(PROGRESS_SHOTS[1]), (1920, 1027))))

    def test_in_select_reads(self):
        self.adopted()
        self.log.clear()
        self.clock[0] = 6.0
        self.proc._read_progress(self.img, [], in_select=True)
        self.proc._read_progress(self.img, [])  # 没给时刻（三选一上补读那几处）也照常认字
        self.assertEqual(self.log, ["progress", "progress"])

    def counted(self, t: float, img: np.ndarray | None = None, in_select: bool = False,
                now: bool = True) -> list[str]:
        """读一次进度条，返回 perfstats.count 数了哪些（沿用的、按原因照常认字的各一种）。"""
        got: list[str] = []
        with mock.patch.object(pipeline.perfstats, "count", side_effect=got.append):
            self.proc._read_progress(self.img if img is None else img, [], in_select=in_select,
                                     now=t if now else None)
        return got

    def test_counts_reused(self):
        self.adopted()
        self.assertEqual(self.counted(6.0), ["progress_reused"])

    def test_counts_look_and_size(self):
        self.adopted()
        other = load_native(PROGRESS_SHOTS[1])
        self.assertEqual(self.counted(6.0, other), ["progress_read_look"])
        self.assertEqual(self.counted(6.0, cv2.resize(other, (1920, 1027))), ["progress_read_size"])

    def test_counts_age(self):
        self.adopted()
        self.assertEqual(self.counted(13.5), ["progress_read_age"])

    def test_counts_pending(self):
        self.adopted()
        self.session.game._progress_pending = 9
        self.proc.hints = None  # 读的提示是上一次进锁时取的：改过记账的状态要重新取一份
        self.assertEqual(self.counted(6.0), ["progress_read_pending"])

    def test_counts_select_and_no_time(self):
        self.adopted()
        self.assertEqual(self.counted(6.0, in_select=True), ["progress_read_select"])
        self.assertEqual(self.counted(6.0, now=False), ["progress_read_no_time"])

    def test_counts_first(self):
        self.assertEqual(self.counted(0.0), ["progress_read_first"])

    def test_counts_no_current(self):
        other = load_native(PROGRESS_SHOTS[1])
        self.current = None
        self.assertEqual(self.counted(0.0, other), ["progress_read_first"])
        self.assertEqual(self.counted(3.0, other), ["progress_read_no_current"])

    def test_counts_cap(self):
        self.adopted()
        with mock.patch.object(pipeline, "read_progress", side_effect=lambda img: Progress(self.current, 17)):
            other = load_native(PROGRESS_SHOTS[1])
            self.assertEqual(self.counted(6.0, other), ["progress_read_look"])
            self.assertIsNone(self.session.game.progress_cap)
            self.assertEqual(self.counted(9.0, other), ["progress_read_cap"])

    def test_max_age_follows_pace(self):
        self.assertEqual(self.proc.progress_s, load_db().rules["watch"]["unchanged_max_ms"] / 1000)
        self.proc.set_pace(2)
        self.assertEqual(self.proc.progress_s, 2 * load_db().rules["watch"]["unchanged_max_ms"] / 1000)


if __name__ == "__main__":
    unittest.main()
