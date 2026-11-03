"""同一帧里沿用的识别结果（advisor/vision/frame.py 的 Frame）和每次都从头算的结果相同。

  认自己那一行  stars.diagnose 按角色记在 Frame 上。它还取决于沿用的皮肤（先只试上次认出时的那套）：同一帧里前一次刚认出
                自己、记下了皮肤时，后一次按新的皮肤重算，和新包一个 Frame 从头算的结果逐位相同；皮肤没变时直接用记下的。
                名字、别名、编号是同一个角色时共用；认不得的角色按原文分开（原因里写着这个名字）。
  查看筹码面板  收 BGRA 时，找自己那一行用的也是去掉透明通道的图（透明通道为 0 的截图交给文字识别会变成一片白）；
                收 Frame 时把同一个 Frame 交给玩家列表的识别，和读星数共用这一帧找到的玩家列表。
  Frame 本身    收 ndarray 时包一层、已经是 Frame 时原样返回；界面缩放、玩家列表的布局同一帧只算一次。
除 Frame 本身那两条（全黑的画面）以外用实机截图；不做文字识别（按名字找自己那一行换成假的）。
"""
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.vision import chip_select, reset_hints, stars
from advisor.vision.chip_panel import read_chip_panel
from advisor.vision.frame import Frame, as_frame
from tests.support import samples

SAMPLES = samples.SAMPLES_DIR
OWN_TURN = "20261002_213811_hud_round7_own_turn_real.jpg"  # 芬妮在第 1 行，轮到自己行动（头像伸缩着）
ONE_CHIP = "20261002_013927_chips_panel_one_chip.png"  # 凛在第 4 行（1 星），其余三行 0 星
KNOCKED_OUT = "20261002_193857_chips_panel_knocked_out_2397x1798.jpg"  # 照处于「昏迷」，「查看筹码」面板开着
DB = load_db()


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def summary(report) -> tuple:
    """比较用：结果、原因、缩放，以及各行的读数和头像相关度。"""
    own = report.result
    got = None if own is None else (own.stars, own.confidence, own.row, own.how)
    rows = [(r.visible, r.stars, r.self_score) for r in report.rows]
    return got, report.reason, report.scale, rows


@unittest.skipUnless(samples.samples_ok(OWN_TURN, ONE_CHIP), samples.samples_reason(OWN_TURN, ONE_CHIP))
class SameFrameDiagnoseTest(unittest.TestCase):
    def setUp(self):
        reset_hints()
        self.addCleanup(reset_hints)

    def test_second_call_follows_learned_skin(self):
        """第一次（还没记下皮肤，各套皮肤全试）认出自己后记下皮肤；同一帧里再认一次（例如面板之后读星数）时先试这一套，
        这张图上把握更高。后一次要和新包一个 Frame 从头算的一样，不能沿用第一次的结果。"""
        img = load(OWN_TURN)
        frame = Frame(img)
        first = stars.diagnose(frame, "芬妮")
        second = stars.diagnose(frame, "芬妮")
        fresh = stars.diagnose(img.copy(), "芬妮")  # 新的 Frame，沿用的皮肤和上一次相同
        self.assertEqual(summary(second), summary(fresh))
        self.assertNotEqual(first.result.confidence, second.result.confidence)  # 这张图上两次确实不同，才测得出来
        self.assertIs(stars.diagnose(frame, "芬妮"), second)  # 皮肤没再变：直接用记下的

    def test_same_hero_shared_unknown_names_apart(self):
        frame = Frame(load(ONE_CHIP))  # 各行星数不全一样：不知道是哪个角色时没有「全员相同」的结果，原因照写
        stars.diagnose(frame, "凛")  # 认出自己，记下皮肤
        again = stars.diagnose(frame, "凛")
        self.assertIsNotNone(again.result)
        self.assertIs(stars.diagnose(frame, stars._hero_id("凛")), again)  # 编号和名字是同一个角色
        self.assertEqual(stars.diagnose(frame, None).reason, "不知道自己是哪个角色")
        self.assertEqual(stars.diagnose(frame, "甲乙").reason, "认不得角色「甲乙」")
        self.assertEqual(stars.diagnose(frame, "丙丁").reason, "认不得角色「丙丁」")


@unittest.skipUnless(samples.samples_ok(KNOCKED_OUT), samples.samples_reason(KNOCKED_OUT))
class PanelFrameTest(unittest.TestCase):
    """面板上找自己的页签时交给玩家列表识别（stars.diagnose、stars.own_row_by_name）的画面。"""

    def setUp(self):
        reset_hints()
        self.addCleanup(reset_hints)

    def read(self, img):
        """读面板，返回（读数, 交给玩家列表识别的各个画面）。按名字找的那一步换成假的，不做文字识别。"""
        seen = []
        real = stars.diagnose

        def diagnose(frame, hero_key=None):
            seen.append(frame)
            return real(frame, hero_key)

        def by_name(frame, own_name=None):
            seen.append(frame)
            return None, "按名字找的这一步换成了假的"

        with mock.patch.object(stars, "diagnose", side_effect=diagnose), \
                mock.patch.object(stars, "own_row_by_name", side_effect=by_name):
            got = read_chip_panel(img, DB, hero_key="照")
        return got, seen

    def test_bgra_gives_bgr_to_player_list(self):
        img = load(KNOCKED_OUT)
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = 0  # 有的截图方式透明通道是 0
        got, seen = self.read(bgra)
        self.assertTrue(got.is_panel, got.reason)
        self.assertEqual(len(seen), 2)  # 头像认不出（「昏迷」），再按名字找
        for frame in seen:
            image = as_frame(frame).image
            self.assertEqual(image.shape, img.shape)
            self.assertTrue(np.array_equal(image, img))

    def test_frame_passed_through(self):
        """收 Frame 时把同一个 Frame 交下去：玩家列表在这一帧里只找一次，读星数时接着用。"""
        frame = Frame(load(KNOCKED_OUT))
        got, seen = self.read(frame)
        self.assertTrue(got.is_panel, got.reason)
        self.assertTrue(seen)
        for passed in seen:
            self.assertIs(passed, frame)


@unittest.skipUnless(samples.samples_ok(OWN_TURN, KNOCKED_OUT), samples.samples_reason(OWN_TURN, KNOCKED_OUT))
class LayoutOnceTest(unittest.TestCase):
    """同一帧里读星数、读队友、面板找自己：真正干活的 stars._find_layout 只跑一次（布局记在 Frame 上，见 frame.py）。"""

    def setUp(self):
        reset_hints()
        self.addCleanup(reset_hints)

    def count_find(self, fn) -> int:
        calls = []
        real = stars._find_layout

        def counted(frame):
            calls.append(1)
            return real(frame)

        stars._find_layout = counted
        try:
            fn()
        finally:
            stars._find_layout = real
        return len(calls)

    def test_stars_and_players_share_layout(self):
        """一帧里先读星数再读队友（悬浮窗就是这样）：玩家列表只找一次。"""
        frame = Frame(load(OWN_TURN))
        n = self.count_find(lambda: (stars.read_own_stars(frame, "芬妮"),
                                     stars.read_players(frame, "芬妮", names=False)))
        self.assertEqual(n, 1)

    def test_panel_and_stars_share_layout(self):
        """面板找自己那一页签（_list_owner 经 diagnose）和读星数共用这一帧的玩家列表。"""
        frame = Frame(load(KNOCKED_OUT))
        n = self.count_find(lambda: (read_chip_panel(frame, DB, hero_key="照"), stars.read_own_stars(frame, "照")))
        self.assertEqual(n, 1)


class FrameTest(unittest.TestCase):
    """一帧一个 Frame 对象（advisor/vision/frame.py）：收 ndarray 时包一层；界面缩放、灰度图同一帧只算一次。"""

    def test_as_frame_and_caches(self):
        from advisor.vision.frame import Frame, as_frame

        img = np.zeros((1080, 1920, 3), np.uint8)
        frame = as_frame(img)
        self.assertIsInstance(frame, Frame)
        self.assertIs(as_frame(frame), frame)  # 已经是 Frame 时原样返回
        self.assertIs(frame.image, img)
        self.assertEqual((frame.w, frame.h), (1920, 1080))
        self.assertEqual(frame.ui_scale(), 1.0)
        self.assertIs(frame.ui_scale(), frame.ui_scale())  # 缓存：同一帧只算一次
        self.assertEqual(frame.ui_range(), chip_select.ui_range(1920, 1080))

    def test_frame_layout_and_diagnose_cached(self):
        """玩家列表布局和认自己那一行的结果记在 Frame 上：同一帧里只算一次。"""
        from advisor.vision import stars
        from advisor.vision.frame import Frame

        frame = Frame(np.zeros((1080, 1920, 3), np.uint8))
        with mock.patch.object(stars, "_find_layout", wraps=stars._find_layout) as layout:
            stars.diagnose(frame, "凛")
            self.assertIsNotNone(frame._stars_layout)  # 布局记在 Frame 上
            stars.diagnose(frame, "凛")  # 同一个角色：直接用记下的结果
        self.assertEqual(layout.call_count, 1)


if __name__ == "__main__":
    unittest.main()
