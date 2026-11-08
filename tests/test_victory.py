"""通关画面「VICTORY」和打输横幅「GAME OVER」的识别（advisor/vision/victory.py）和识别流程里接它的那一处
（FrameProcessor._check_victory）。

A. 颜色预筛（glance，造的画面，不做文字识别）：整屏珊瑚红是底色，中间一带白色多了才算大字可能出来了；灰底、只有一小块红、
   红的偏暗都不算；带透明通道的照常看，灰度图、空图不像。蓝色斜带只在占比够了才看形状：斜的（17°、占比够）算，
   横的整屏蓝（斜度 0）、蓝色占比不够的不算。
B. 认字（read，造的画面）：珊瑚红底上画的 VICTORY 读得到；只有底色、只有别的字时读不到。
C. 录像裁图（samples/pages/，不随仓库发布，缺了跳过）：繁体中文客户端 3840x2160 录像里大字读得全的那一帧，缩到 1920x1080
   以后只裁了大字那一行（没有玩家名、聊天头像），贴回同样颜色的整屏底上：预筛两项都是真，认字读得到；整幅缩到 1280x720
   也读得到。打输那一帧的蓝色斜带：形状是真、不是通关的红底，转正以后读得到 GAME OVER，缩到 1280x720、1000x563 也读得到；
   通关的斜带形状也是真，但读不出 GAME OVER。
D. 样本截图（samples/，缺了跳过）：结算页、奖励页、加载画面、战斗画面都不像通关画面，也没有蓝色斜带。
E. 识别流程（假的 glance、read、read_game_over，真的 Session）：这一局还在进行时才看；像底色时紧接着取下一帧（pending），
   白色多时认字，读到了交给 Session 记下时刻、不再急着取下一帧；蓝色斜带读到了交给 on_game_over_banner；同一幅画面（settle
   接着处理，时刻相同）不再看、不再认字、不再要下一帧，settle 处理两遍就停；底色连着超过 RUN_S 秒、从一种换成另一种时
   重新算；本局已结束、没有对局状态时不看；整个 process 接着这一处，造的通关画面一帧就记下。
"""
from __future__ import annotations

import unittest
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.session import Session, Settings
from advisor.vision import io, pipeline, victory
from advisor.vision.frame import Frame
from tests.support import samples

CORAL = (99, 65, 253)  # 通关画面底色（BGR），量自录像帧
# 录像帧：打输那一段的蓝色斜带、通关的斜带
GAME_OVER_SAMPLE = "pages/20261106_235502_f041328_game_over_banner_1920x1080.png"
VICTORY_BANNER_SAMPLE = "pages/20261105_010644_f051195_victory_banner_1920x1080.png"
# 录像裁图：1920x1080 画面里大字那一行，左上角在 (BAND_X, BAND_Y)
BAND_SAMPLE = "pages/20261105_010644_f051294_victory_band_1920x1080.png"
BAND_X, BAND_Y = 300, 475
# 不像通关画面的样本截图：结算页（入场、统计）、奖励页、加载画面、战斗画面
NEGATIVES = (
    "20261002_151251_result_2560x1057.jpg",
    "20261003_132518_result_portraits_2560x1369.jpg",
    "20261003_132524_result_stats_chips_2560x1369.jpg",
    "20261003_132534_reward_2560x1369.jpg",
    "20261002_144036_loading_2560x1057.jpg",
    "20261002_021351_battle_scene.png",
)
BANNER_BLUE = tuple(int(v) for v in cv2.cvtColor(np.uint8([[[108, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0])


def coral_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    return np.full((h, w, 3), CORAL, np.uint8)


def banner_frame(angle: float = 17.0, long: float = 528.0, thick: float = 120.0) -> np.ndarray:
    """480x270（glance 看的那一档）的深色底上画一条蓝色斜带：长、短边和斜度照录像里量到的来。"""
    img = np.full((270, 480, 3), 40, np.uint8)
    rad = np.radians(angle)
    along, normal = np.array([np.cos(rad), -np.sin(rad)]), np.array([np.sin(rad), np.cos(rad)])
    center = np.array([240.0, 135.0])
    half = np.array([along * long / 2, normal * thick / 2])
    points = np.array([center + a + n for a in (-half[0], half[0]) for n in (-half[1], half[1])], np.int32)
    cv2.fillPoly(img, [points], BANNER_BLUE)
    return img


def drawn_victory(w: int = 1920, h: int = 1080) -> np.ndarray:
    """珊瑚红底、中间偏下一行白色的 VICTORY（字形和游戏里的不同，只看读不读得出）。"""
    img = coral_frame(w, h)
    k = w / 1920
    cv2.putText(img, "VICTORY", (int(330 * k), int(700 * k)), cv2.FONT_HERSHEY_DUPLEX, 9.0 * k, (255, 255, 255),
                max(1, int(40 * k)), cv2.LINE_AA)
    return img


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


class GlanceTest(unittest.TestCase):
    def test_coral_background_without_text(self):
        got = victory.glance(coral_frame())
        self.assertTrue(got.background)
        self.assertFalse(got.text)  # 大叉、箭头那几帧：底色对，还没有大字

    def test_white_in_band(self):
        img = coral_frame()
        img[540:700, 300:1100] = 255  # 中间一带里一大块白色（约占这一带的 0.18）
        got = victory.glance(img)
        self.assertTrue(got.background and got.text, got)
        self.assertGreaterEqual(got.white, victory.WHITE_SHARE)

    def test_white_outside_band_does_not_count(self):
        img = coral_frame()
        img[:200, :] = 255  # 上面一条白：不在中间那一带
        self.assertFalse(victory.glance(img).text)

    def test_not_coral(self):
        grey = np.full((1080, 1920, 3), 128, np.uint8)
        self.assertFalse(victory.glance(grey).background)
        patch = grey.copy()
        patch[:400, :1000] = CORAL  # 只有一小块红（约占 0.19）
        self.assertFalse(victory.glance(patch).background)
        dark = (coral_frame().astype(np.float32) * 0.5).astype(np.uint8)  # 红的偏暗（像三选一开着时压暗的画面）
        self.assertFalse(victory.glance(dark).background)

    def test_alpha_grey_and_empty(self):
        bgra = cv2.cvtColor(coral_frame(), cv2.COLOR_BGR2BGRA)
        self.assertTrue(victory.glance(bgra).background)
        self.assertEqual(victory.glance(np.full((1080, 1920), 200, np.uint8)), victory.NOTHING)
        self.assertEqual(victory.glance(np.zeros((0, 0, 3), np.uint8)), victory.NOTHING)
        self.assertTrue(victory.glance(Frame(coral_frame())).background)  # 识别流程交的是 Frame

    def test_banner_shape(self):
        """蓝色斜带：占比够、长边短边够、斜度在范围里才算；横的整屏蓝、比例太大的斜块、占比不够的都不算。"""
        self.assertTrue(victory.glance(banner_frame()).banner)
        self.assertFalse(victory.glance(banner_frame()).background)  # 蓝带不是通关的红底
        self.assertFalse(victory.glance(np.full((270, 480, 3), BANNER_BLUE, np.uint8)).banner)  # 斜度 0
        self.assertFalse(victory.glance(banner_frame(angle=0.0)).banner)  # 横着的带子
        self.assertFalse(victory.glance(banner_frame(long=200.0)).banner)  # 长边不够
        self.assertFalse(victory.glance(banner_frame(thick=40.0)).banner)  # 短边不够
        self.assertFalse(victory.glance(banner_frame(thick=10.0)).banner)  # 占比不够（连形状都不看）
        self.assertFalse(victory.glance(coral_frame()).banner)  # 红底上没有蓝带


class ReadTest(unittest.TestCase):
    def test_drawn_word(self):
        img = drawn_victory()
        self.assertTrue(victory.glance(img).text)
        self.assertTrue(victory.read(img))

    def test_background_or_other_words(self):
        self.assertFalse(victory.read(coral_frame()))
        img = coral_frame()
        cv2.putText(img, "GOOD GAME", (330, 700), cv2.FONT_HERSHEY_DUPLEX, 7.0, (255, 255, 255), 30, cv2.LINE_AA)
        self.assertFalse(victory.read(img))

    def test_word_rules(self):
        """只认字母、不分大小写，容许最后的 Y 读错（被聊天头像盖住）；别的词不算。"""
        def lines(*texts):
            return [io.Line(t, 960.0, 600.0, 400.0, 100.0) for t in texts]

        for texts, want in ((("VICTORY",), True), (("Victory!",), True), (("V I C T O R I",), True),
                            (("VICTO",), False), (("VI", "ITRY"), False), (("GAME OVER",), False), ((), False)):
            with self.subTest(texts=texts), mock.patch.object(io, "ocr", return_value=lines(*texts)):
                self.assertEqual(victory.read(coral_frame()), want)


class ReadGameOverTest(unittest.TestCase):
    def test_word_rules(self):
        """只认字母、不分大小写，0 当成 O；G 滑出画面读成「AME OVER」也算，VICTORY 那几个字不算。"""
        def lines(*texts):
            return [io.Line(t, 960.0, 600.0, 400.0, 100.0) for t in texts]

        for texts, want in ((("GAME OVER",), True), (("AME OVER",), True), (("me over",), True),
                            (("GAMEOVER",), True), (("GAME 0VER",), True), (("ME OVER",), True),
                            (("VICTORY",), False), (("OVER",), False), (("",), False), ((), False)):
            with self.subTest(texts=texts), mock.patch.object(io, "ocr", return_value=lines(*texts)):
                self.assertEqual(victory.read_game_over(banner_frame()), want)

    def test_grey_and_empty(self):
        self.assertFalse(victory.read_game_over(np.full((270, 480), 200, np.uint8)))
        self.assertFalse(victory.read_game_over(np.zeros((0, 0, 3), np.uint8)))


@unittest.skipUnless(samples.samples_ok(BAND_SAMPLE), samples.samples_reason(BAND_SAMPLE))
class RecordedBandTest(unittest.TestCase):
    """录像裁图贴回整屏底色上：和那一帧的通关画面一样（大字的位置、大小、颜色照原样）。"""

    @classmethod
    def setUpClass(cls):
        band = load(BAND_SAMPLE)
        img = np.full((1080, 1920, 3), np.median(band[:3].reshape(-1, 3), axis=0).astype(np.uint8), np.uint8)
        img[BAND_Y:BAND_Y + band.shape[0], BAND_X:BAND_X + band.shape[1]] = band
        cls.frame = img

    def test_glance(self):
        got = victory.glance(self.frame)
        self.assertTrue(got.background and got.text, got)

    def test_read(self):
        self.assertTrue(victory.read(self.frame))

    def test_read_smaller_window(self):
        small = cv2.resize(self.frame, (1280, 720), interpolation=cv2.INTER_AREA)
        self.assertTrue(victory.glance(small).text)
        self.assertTrue(victory.read(small))


@unittest.skipUnless(samples.samples_ok(GAME_OVER_SAMPLE, VICTORY_BANNER_SAMPLE),
                     samples.samples_reason(GAME_OVER_SAMPLE, VICTORY_BANNER_SAMPLE))
class RecordedBannerTest(unittest.TestCase):
    """录像帧：打输那一帧的蓝色斜带、通关那一帧的斜带。"""

    @classmethod
    def setUpClass(cls):
        cls.loss = load(GAME_OVER_SAMPLE)
        cls.win = load(VICTORY_BANNER_SAMPLE)

    def test_loss_glance(self):
        got = victory.glance(self.loss)
        self.assertTrue(got.banner, got)
        self.assertFalse(got.background, got)

    def test_loss_read(self):
        self.assertTrue(victory.read_game_over(self.loss))
        for size in ((1280, 720), (1000, 563)):
            with self.subTest(size=size):
                small = cv2.resize(self.loss, size, interpolation=cv2.INTER_AREA)
                self.assertTrue(victory.glance(small).banner, size)
                self.assertTrue(victory.read_game_over(small), size)

    def test_victory_banner_is_not_game_over(self):
        got = victory.glance(self.win)
        self.assertTrue(got.banner, got)  # 颜色和形状几乎一样
        self.assertFalse(victory.read_game_over(self.win))  # 字不一样：认 GAME OVER 认不出来


@unittest.skipUnless(samples.samples_ok(*NEGATIVES), samples.samples_reason(*NEGATIVES))
class SampleNegativesTest(unittest.TestCase):
    def test_not_victory(self):
        for name in NEGATIVES:
            with self.subTest(name=name):
                got = victory.glance(load(name))
                self.assertFalse(got.background, got)
                self.assertFalse(got.banner, got)
                self.assertLess(got.coral, 0.1)


class PipelineTest(unittest.TestCase):
    """识别流程里的那一处（_check_victory）：glance、read、read_game_over 换成假的，Session 是真的（这一局打到第 12 轮）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.clock = [100.0]
        self.session = Session(self.db, Settings(hero="凛", round=12, star=3, star_read=True))
        self.session._clock = lambda: self.clock[0]
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.look = victory.Glance(0.0, 0.0)
        self.word = False
        self.reads = 0
        self.banner_word = False
        self.banner_reads = 0

        def fake_read(img):
            self.reads += 1
            return self.word

        def fake_game_over(img):
            self.banner_reads += 1
            return self.banner_word

        for p in (mock.patch.object(victory, "glance", side_effect=lambda img: self.look),
                  mock.patch.object(victory, "read", side_effect=fake_read),
                  mock.patch.object(victory, "read_game_over", side_effect=fake_game_over)):
            p.start()
            self.addCleanup(p.stop)

    def check(self, now: float) -> list:
        events: list = []
        self.clock[0] = now
        self.proc.pending = False
        self.proc.hints = None
        self.proc._check_victory(Frame(coral_frame(64, 36)), now, events)
        return events

    def test_background_asks_next_frame(self):
        self.look = victory.Glance(0.8, 0.02)  # 大叉、箭头那几帧
        self.check(100.0)
        self.assertTrue(self.proc.pending)
        self.assertEqual(self.reads, 0)
        self.assertIsNone(self.session.game._victory_at)

    def test_read_word(self):
        self.look = victory.Glance(0.85, 0.3)
        self.check(100.0)  # 大字只出来一半：读不到，接着取下一帧
        self.assertEqual(self.reads, 1)
        self.assertTrue(self.proc.pending)
        self.assertIsNone(self.session.game._victory_at)
        self.word = True
        self.check(100.3)
        self.assertEqual(self.reads, 2)
        self.assertFalse(self.proc.pending)  # 读到了：不再急着取下一帧
        self.assertEqual(self.session.game._victory_at, 100.3)
        rec = [r for r in self.session.recent_events(60) if r["kind"] == "read:victory"]
        self.assertEqual(len(rec), 1)

    def test_banner_not_read_asks_next_frame(self):
        """蓝色斜带：形状对了就转正认字，读不到接着取下一帧，记不下胜负。"""
        self.look = victory.Glance(0.0, 0.0, banner=True)
        self.check(100.0)
        self.assertEqual((self.reads, self.banner_reads), (0, 1))
        self.assertTrue(self.proc.pending)
        self.assertIsNone(self.session.game._game_over_at)

    def test_banner_read_word(self):
        self.look = victory.Glance(0.0, 0.0, banner=True)
        self.banner_word = True
        self.check(100.0)
        self.assertEqual(self.banner_reads, 1)
        self.assertFalse(self.proc.pending)  # 读到了：不再急着取下一帧
        self.assertEqual(self.session.game._game_over_at, 100.0)
        self.assertEqual([r["kind"] for r in self.session.recent_events(60) if r["kind"] == "read:game_over_banner"],
                         ["read:game_over_banner"])

    def test_same_image_looked_once(self):
        """settle 拿同一幅画面接着处理（时刻相同）：不再看、不再认字，也不再要下一帧（不然同一幅画面要连着处理 SETTLE_MAX
        遍）；下一幅画面照常看。"""
        self.look = victory.Glance(0.85, 0.3)
        self.check(100.0)
        self.assertTrue(self.proc.pending)
        self.check(100.0)
        self.assertEqual(self.reads, 1)
        self.assertFalse(self.proc.pending)
        self.check(100.1)
        self.assertEqual(self.reads, 2)
        self.assertTrue(self.proc.pending)

    def test_settle_stops_on_same_image(self):
        """settle（回放截图的工具用）在只有底色的通关画面上：第一遍要下一帧，第二遍是同一幅画面、不再要，处理两遍就停，
        不连着处理 SETTLE_MAX 遍。"""
        self.look = victory.Glance(0.8, 0.02)
        patches = [
            mock.patch.object(self.proc, "process", wraps=self.proc.process),
            mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False),
            mock.patch.object(pipeline, "read_chip_select", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
            mock.patch.object(self.proc, "_idle_reads"),
        ]
        process = patches[0].start()
        for p in patches[1:]:
            p.start()
        for p in patches:
            self.addCleanup(p.stop)
        self.clock[0] = 100.0
        self.proc.settle(coral_frame(), 100.0)
        self.assertEqual(process.call_count, 2)
        self.assertFalse(self.proc.pending)
        self.assertEqual(self.reads, 0)

    def test_long_run_stops_asking(self):
        """同一种底色连着超过 RUN_S 秒就不是这两段画面：不再要下一帧、不再认字（蓝色的过场等）。底色消失再出现时重新算。"""
        self.look = victory.Glance(0.0, 0.0, banner=True)
        self.check(100.0)
        self.assertTrue(self.proc.pending)
        self.check(100.0 + victory.RUN_S + 1)
        self.assertEqual((self.banner_reads, self.proc.pending), (1, False))
        self.check(200.0)
        self.look = victory.Glance(0.0, 0.0)  # 蓝带没了
        self.check(200.1)
        self.look = victory.Glance(0.0, 0.0, banner=True)  # 又出现：从这一刻起重新算
        self.check(200.2)
        self.assertEqual(self.banner_reads, 2)
        self.assertTrue(self.proc.pending)

    def test_switch_between_kinds(self):
        """红底换成蓝带（反过来也一样）时重新算：换的那一刻不算连着，照常认字、要下一帧。"""
        self.look = victory.Glance(0.8, 0.02)
        self.check(100.0)
        self.assertTrue(self.proc.pending)
        self.look = victory.Glance(0.0, 0.0, banner=True)
        self.check(100.0 + victory.RUN_S + 1)  # 蓝带才刚开始，不算连着太久
        self.assertEqual((self.reads, self.banner_reads), (0, 1))
        self.assertTrue(self.proc.pending)

    def test_not_looking_when_over_or_idle(self):
        self.look = victory.Glance(0.85, 0.3)
        self.word = self.banner_word = True
        self.session.game.game_over = True
        self.check(100.0)
        self.assertEqual((self.reads, self.banner_reads, self.proc.pending), (0, 0, False))
        self.session = Session(self.db, Settings(hero="凛"))  # 没有对局状态（刚启动、两局之间）
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.look = victory.Glance(0.0, 0.0, banner=True)
        self.check(101.0)
        self.assertEqual((self.reads, self.banner_reads, self.proc.pending), (0, 0, False))
        self.assertIsNone(self.session.game._victory_at)
        self.assertIsNone(self.session.game._game_over_at)

    def test_not_coral(self):
        self.look = victory.Glance(0.02, 0.4)
        self.check(100.0)
        self.assertEqual((self.reads, self.proc.pending), (0, False))


class ProcessTest(unittest.TestCase):
    """整个 process：造的通关画面、蓝色斜带（真的 glance）一帧就记下；认字、三选一、读轮次等换成假的。"""

    @staticmethod
    def patches(proc):
        return [
            mock.patch.object(pipeline, "looks_like_overlay_screen", return_value=False),
            mock.patch.object(pipeline, "read_chip_select", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
            mock.patch.object(proc, "_idle_reads"),  # 读轮次等这一帧不做（只看接通关画面的那一处）
        ]

    def test_frame_records_victory(self):
        db = load_db()
        session = Session(db, Settings(hero="凛", round=12, star=3, star_read=True))
        session._clock = lambda: 50.0
        proc = pipeline.FrameProcessor(db, session)
        for p in self.patches(proc):
            p.start()
            self.addCleanup(p.stop)
        proc.process(drawn_victory(), 50.0)
        self.assertEqual(proc.errors, [])
        self.assertEqual(session.game._victory_at, 50.0)

    def test_frame_records_game_over(self):
        db = load_db()
        session = Session(db, Settings(hero="凛", round=12, star=3, star_read=True))
        session._clock = lambda: 50.0
        proc = pipeline.FrameProcessor(db, session)
        for p in self.patches(proc):
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(victory, "read_game_over", return_value=True):
            proc.process(banner_frame(), 50.0)
        self.assertEqual(proc.errors, [])
        self.assertEqual(session.game._game_over_at, 50.0)


if __name__ == "__main__":
    unittest.main()
