"""FrameProcessor 省掉重复的文字识别，用真的样本截图：

  同一个三选一界面上只查绿框、不每帧做文字识别（_read_select）：读得完整时一直沿用，筹码名那一条变了才再认；读得不完整时
  半秒后再完整识别；
  战斗等过得了预筛、却不是三选一的画面，画面没变就不再重复识别（_Unchanged）；
  三选一期间也读进度条，读数变了下一帧马上补读（ProgressInSelectTest）。
读轮次、读进度条、认角色、读星数、选角界面判断换成假的（这里不测它们，ProgressInSelectTest 另有一条用真的），
三选一识别用真的。
三选一出来 2 秒内给出推荐（FastAdviceTest、IdleOneReadTest，识别函数换成假的）：手上的轮次、进度条够新时先给
推荐、下一帧再读一次核对，不同就重算；手动模式、刚从暂停恢复、本局还没读到过轮次、太久没读到轮次时照旧先读完；推荐给出以后
只盯绿框；不在三选一时一帧最多做一项读数（pending），读数、先后和一帧做完时一样。只盯绿框的两个例外（SelectRereadTest、
HudRetryTest）：刚出现那一帧读得可疑（刷新次数读成 0、概率行不在概率表里）时半秒后补认一次；读左上角出错、读不出轮次时隔约
1 秒再读，每次三选一最多 3 次。刚出现那一帧有筹码没读出来、还没给过推荐时先完整重读三张卡、核对排到重读之后，读不全的那一帧
留一张给诊断包（FirstRereadTest）。
一张截图代表画面在那一刻的样子：照截图走的几组用 FrameProcessor.settle 把到期的读数做完（悬浮窗一帧只做一项、紧接着取下一帧
接着做，同一张截图就是接着用这一张）。
对局外的画面（结算画面、房间等）交给 Session、上一局结束后第一次出现三选一（OutOfGameFlowTest），识别函数全部换成假的。
去掉画面四周的黑边：先看靠边一圈就判完的结果和整套算完全相同（BordersAndTaskPanelTest）。
另外核对 rules.toml 写明的 coins_ms 和默认值一致。
"""
import contextlib
import dataclasses
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor.data import LEVEL_NIGHTMARE, load_db
from advisor.pool import difficulty_of
from advisor.session import Session, Settings
from advisor.vision import pipeline
from advisor.vision.hud import Hud, Pregame, Progress
from advisor.vision.screens import CUSTOM_ROOM, MATCH_FOUND, RESULT, REWARD, TEAM_ROOM, OutOfGame
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
REROLL1 = "20261002_013552_chipselect_levelup_star1_after_reroll1.png"  # 会员推荐信 / 手电筒-强光 / 优雅之羽
REROLL2 = "20261002_013608_chipselect_levelup_star1_after_reroll2.png"  # 医疗箱-紧急治疗 / 书签 / 能量回收
SELECTED = "20261002_013617_chipselect_selected_state.png"  # 同上，书签被选中
BATTLE = "20261002_021351_battle_scene.png"  # 战斗画面：上方也有黑带，过得了三选一的预筛
# 10-03 实机的全队任务三选一（2 星、2 级任务）：出场动画里图标还没加载（稀有度读不出），两秒后图标加载完、最左格蓝色
ICONS_LATE = "20261003_130848_chipselect_icons_not_loaded_team_task_real.jpg"
ICONS_LOADED = "20261003_130850_chipselect_team_task_tier2_star2_left_blue_real.jpg"
# 10-03 实机第 10 轮的任务三选一：任务奖励「进度 -1」已生效，进度条当前格由 11 退回 10
PROGRESS_BACK = "20261003_131934_chipselect_team_task_tier3_star3_left_blue_real.jpg"


def load(name: str) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.resize(img, (1920, round(img.shape[0] * 1920 / img.shape[1])), interpolation=cv2.INTER_AREA)


@unittest.skipUnless(samples.samples_ok(SELECTED), samples.samples_reason(SELECTED))
class QuickRecheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.images = {name: load(name) for name in (REROLL1, REROLL2, SELECTED, ICONS_LATE, ICONS_LOADED)}

    def setUp(self):
        self.session = Session(self.db, Settings(hero="凛", round=3, star=1, star_read=True))
        self.calls = 0
        self.incomplete_once = False  # 第一次识别到三选一时去掉概率行（假装读错了）
        real = pipeline.read_chip_select

        def counted(img, db, **kw):
            self.calls += 1
            seen = real(img, db, **kw)
            if self.incomplete_once and seen is not None:
                self.incomplete_once = False
                seen = dataclasses.replace(seen, probabilities=None)
            return seen

        patches = [
            mock.patch.object(pipeline, "read_chip_select", side_effect=counted),
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


@unittest.skipUnless(samples.samples_ok(BATTLE, REROLL2), samples.samples_reason(BATTLE, REROLL2))
class UnchangedScreenTest(unittest.TestCase):
    """战斗画面：认定「不是三选一」「不是选角界面」、轮次也读不到以后，画面没变就不再重复识别。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.battle = load(BATTLE)
        cls.select = load(REROLL2)
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

        def fake_hud(img, db, full):
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

    def test_round_and_select_screen_not_reread_while_unchanged(self):
        """每 3 秒一轮（读轮次、认选角界面……）：每个时刻的这一轮都做完再看下一个时刻（settle，悬浮窗一帧一项、紧接着几帧
        做完）。"""
        self.proc._not_select.max_s = self.proc._not_pick.max_s = 100.0  # 这里不测「最多沿用多久」
        for t in (0.0, 3.0, 6.0, 9.0, 12.0):
            self.proc.settle(self.battle, t)
        # 第 1、2 次照常读；第 2 次时画面已经静止、记下了，之后不再读
        self.assertEqual(self.hud_calls, [False, False])
        self.assertEqual(self.pick_calls, 2)
        # 地图变成未知：上次只读了轮次，没读任务栏，要连任务栏一起再读一次
        self.session.set_map(None)
        self.proc.settle(self.battle, 15.0)
        self.proc.settle(self.battle, 18.0)
        self.assertEqual(self.hud_calls, [False, False, True])
        # 画面变了：重新读
        self.proc.settle(self.moved, 21.0)
        self.assertEqual(self.hud_calls, [False, False, True, True])
        self.assertEqual(self.pick_calls, 3)


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

    # 平常的画面四条边上都有亮点、只看靠边一圈就判完（_edges_lit）的代表：4K 的三选一、战斗画面（上方也有黑带）、
    # 对局画面，21:9、4:3 的实机截图。只对这几张断言走了这条快路：以后加进四周偏暗、但没有黑边的样本（转场画面等）时，
    # 整套算下来照样原样返回，只是慢一点，不该让这条测试失败
    FAST_PATH_SAMPLES = (SELECTED, BATTLE, "20261002_012918_hud_round1.png", ICONS_LOADED,
                         "20261002_144039_hud_round1_2560x1057.jpg", "20261002_193639_hud_round11_2397x1798.jpg")

    def test_samples_have_no_borders(self):
        for f in sorted(SAMPLES.glob("2026*.*")):
            img = cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR)
            with self.subTest(f.name):
                self.assertIs(pipeline.trim_borders(img)[0], img)
                if f.name in self.FAST_PATH_SAMPLES:
                    self.assertTrue(pipeline._edges_lit(img, max(1, min(img.shape[:2]) // 270)))
        self.assertEqual([n for n in self.FAST_PATH_SAMPLES if not (SAMPLES / n).exists()], [])

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

        def fake_hud(img, db, full):
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
        self.select_info = True  # 像三选一，但读不到简体中文标题
        self.proc.process(self.img, 0.0, force=True)
        self.assertEqual(self.session.unsupported, [])
        self.proc.process(self.img + 1, 1.0, force=True)
        self.assertIn("简体中文", " ".join(self.session.unsupported))
        self.select_info = False  # 读到了简体中文标题（假的识别仍返回 None）
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
    识别函数全部换成假的。"""

    RESULT_SCREEN = OutOfGame(RESULT, "噩梦", "异变图书馆", 12)

    def setUp(self):
        self.db = load_db()
        self.seen, self.round, self.hero, self.stars = None, None, "凛", None
        self.picking, self.bottom, self.pregame = False, None, Pregame()  # 选角界面、下方区域和整幅识别的读数
        self.star_calls, self.pregame_at = 0, []  # 读星数的次数、每次整幅识别的时刻
        self.pick_at = []  # 每次选角界面判断（下方区域的识别）的时刻

        def fake_pick(img, db, info=None):
            self.pick_at.append(self.clock[0])
            if info is not None:
                info["screen"] = None if self.picking else self.bottom
            return self.picking

        def fake_stars(img, hero, **kw):
            self.star_calls += 1
            return self.stars

        def fake_pregame(img, db, width, catalog):
            self.pregame_at.append(self.clock[0])
            return self.pregame

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full: Hud(round=self.round)),
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

    def step(self, t: float) -> list:
        """处理 t 秒处的一帧，到期的读数都做完（settle）。每帧的亮度都和前 7 帧不同：整幅识别前的缩略图检查、选角界面判断
        用的指纹都看得出画面变了。"""
        self.clock[0] = t
        self.n += 1
        return self.proc.settle(np.full((1080, 1920, 3), 10 + 30 * (self.n % 8), np.uint8), t)

    def playing(self) -> None:
        """一局打到第 12 轮、3 星，认出角色凛（第 0、4、8 秒各读一次轮次）。"""
        self.round, self.stars = 12, SimpleNamespace(stars=3, confidence=0.9)
        for t in (0.0, 4.0, 8.0):
            self.step(t)
        self.assertEqual((self.session.settings.round, self.session.game.seen_hero), (12, "凛"))

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
        self.assertEqual(self.proc._last_round_at, float("-inf"))
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
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full=False: Hud(round=self.round)),
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

        def fake_hud(img, db, full=False):
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
    不动；每次三选一只补一次；读得正常的不补认。识别函数换成假的（_FakeReads，「select」是完整识别，只查绿框不记）。"""

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

    def test_reread_once_per_offer(self):
        """每次三选一只补认一次：同一次三选一里刷新了（筹码名变了，马上完整识别）、刷新后又读成 0，不再补认；三选一关掉、
        下一次三选一刚出现又读成 0 时照样补认一次。
        补认时气泡已经消失、读到 2 次：刷新后读成 0 比 2 次少，仍是同一次里刷新。补认仍读成 0 的话，三张全换的一组算紧接着
        出现的另一次三选一（上一组的 0 次刷不了），那一次照样补认一次，见 test_reread_after_zero_new_select。"""
        self.seen = offer(OFFER_A, rerolls=0)
        self.frame(4.0)
        self.seen = offer(OFFER_A)  # 补认时气泡已经消失：刷新 ×2
        self.assertEqual(len(self.when(self.frames(4.15, 5.0), "select")), 1)  # 补认
        self.seen = offer(OFFER_B, rerolls=0)  # 刷新（2 → 1，刷新按钮又被挡住，读成 0）
        self.assertEqual(self.when(self.frames(5.15, 8.0), "select"), [5.15])  # 只有筹码名变了的那一次
        self.seen = None
        self.frames(8.15, 8.3)  # 三选一关掉
        self.seen = offer(OFFER_C, rerolls=0)
        reads = self.when(self.frames(9.0, 12.0), "select")
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[0], 9.0)
        self.assert_gaps(reads, self.proc.reread_s)

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

        def hud(img, db, full):
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


if __name__ == "__main__":
    unittest.main()
