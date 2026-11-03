"""识别模块共用的小工具（advisor/vision/io.py）和对局外画面解析器的登记表。

  识别结果转 Line   没有识别框、缺字段时为空表；坐标按缩放倍数和裁剪的左上角换回原图像素；
  按范围识别        空画面不调用识别引擎；范围夹在画面内（超出画面、落在最后一行一列上照样裁出一块）；坐标换回原图像素；
  刷新次数的重读    三选一右侧「刷新」按钮上、按钮下方都没读到数字时放大重读：重读什么都没读到时是 None（按钮在、数字读不出），
                    重读到的「×N」按裁剪位置换回原图后照常认；按钮上或第一遍就读到数字时不重读；
  对局外画面的先后  几种画面的字样同时出现时按登记表（screens.PARSERS）的先后认：结算页、匹配成功页、房间、组队房间、奖励页。
识别引擎一律换成假的，不加载模型。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.vision import chip_select, io, screens
from advisor.vision.chip_select import TITLE, TITLE_WIDTH_U, Line, ocr_scale
from advisor.vision.screens import OutOfGame, parse_out_of_game

DB = load_db()
NOTHING = SimpleNamespace(boxes=None, txts=None)  # 识别引擎什么都没读到时的结果


def quad(x0, y0, x1, y1):
    """识别结果里的一个框：四个角的 (x, y)，和识别引擎给的一样是 float32。"""
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


class ResultLinesTest(unittest.TestCase):
    def test_nothing_read(self):
        self.assertEqual(io.result_lines(NOTHING), [])
        self.assertEqual(io.result_lines(object()), [])  # 字段缺了照旧当没读到
        self.assertEqual(io.result_lines(SimpleNamespace(boxes=np.array([quad(0, 0, 10, 10)]), txts=None)), [])

    def test_back_to_frame_pixels(self):
        result = SimpleNamespace(boxes=np.array([quad(0, 0, 40, 10)]), txts=("刷新",))
        self.assertEqual(io.result_lines(result), [Line("刷新", 20.0, 5.0, 40.0, 10.0)])
        self.assertEqual(io.result_lines(result, 2.0, (10, 20)), [Line("刷新", 20.0, 22.5, 20.0, 5.0)])


class OcrRangeTest(unittest.TestCase):
    def setUp(self):
        self.img = np.zeros((1080, 1920, 3), np.uint8)

    def test_empty_frame_skips_engine(self):
        engine = mock.Mock(return_value=NOTHING)
        with mock.patch.object(io, "_engine", return_value=engine):
            self.assertEqual(io.ocr(np.zeros((0, 10, 3), np.uint8), (0, 0, 10, 10)), [])
            self.assertEqual(io.ocr(np.zeros((10, 0, 3), np.uint8), (0, 0, 10, 10)), [])
        engine.assert_not_called()

    def test_box_clamped_into_frame(self):
        """范围超出画面、整个在画面外、取整后上下颠倒时都夹进画面，交给识别引擎的不是空图。"""
        engine = mock.Mock(return_value=NOTHING)
        boxes = ((1920, 1080, 2000, 1200), (-50, -50, -10, -10), (100.4, 200.6, 99, 150), (0, 0, 1920, 1080))
        with mock.patch.object(io, "_engine", return_value=engine):
            for box in boxes:
                self.assertEqual(io.ocr(self.img, box), [])
        self.assertEqual(engine.call_count, len(boxes))
        for call in engine.call_args_list:
            self.assertGreater(call.args[0].size, 0)

    def test_lines_in_frame_pixels(self):
        s = ocr_scale(1920, 1080)
        # 原图上 (300, 400)~(340, 420) 的一段字；裁剪的左上角是 (200, 300)，识别图上的框是相对它的坐标乘以缩放倍数 s
        result = SimpleNamespace(boxes=np.array([quad(100 * s, 100 * s, 140 * s, 120 * s)]), txts=("03",))
        with mock.patch.object(io, "_engine", return_value=mock.Mock(return_value=result)):
            (line,) = io.ocr(self.img, (200, 300, 600, 700))
        self.assertEqual(line.text, "03")
        for got, want in zip((line.cx, line.cy, line.width, line.height), (320, 410, 40, 20)):
            self.assertAlmostEqual(got, want, places=3)


class RerollRereadTest(unittest.TestCase):
    """三选一右侧「刷新」按钮的剩余次数。画面 1920×1080，相邻两个筹码相距 UNIT，按钮在标题右边。"""

    UNIT = 200.0
    BUTTON = (1500.0, 300.0)

    def read(self, lines, reread=NOTHING):
        """第一遍识别读到标题和 lines；放大重读按钮附近那一小块时识别引擎返回 reread。返回（剩余次数, 重读了几次）。"""
        title = Line(TITLE, 960.0, 200.0, TITLE_WIDTH_U * self.UNIT, 40.0)
        engine = mock.Mock(return_value=reread)
        with mock.patch.object(chip_select, "_ocr_lines", return_value=[title, *lines]), \
                mock.patch.object(chip_select, "_engine", return_value=engine):
            got = chip_select.read_chip_select(np.zeros((1080, 1920, 3), np.uint8), DB)
        return got.rerolls, engine.call_count

    def button(self, text="刷新"):
        return Line(text, *self.BUTTON, 60.0, 30.0)

    def test_count_below_button(self):
        self.assertEqual(self.read([self.button(), Line("×3", 1500.0, 330.0, 30.0, 24.0)]), (3, 0))

    def test_count_on_button(self):
        """按钮上读到数字时第一遍就有结果、不重读：所以重读只在按钮上没有数字时发生。"""
        self.assertEqual(self.read([self.button("刷新×2")]), (2, 0))

    def test_reread_reads_nothing(self):
        """重读那一小块什么都没读到：按钮在、数字读不出，是 None（Session 退回默认值），不是 0。"""
        self.assertEqual(self.read([self.button()]), (None, 1))

    def test_reread_count_back_to_frame_pixels(self):
        """重读读到按钮下方的「×2」：识别图上的框按放大倍数和裁剪的左上角换回原图，落在按钮正下方才认。"""
        bx, by = self.BUTTON
        x0, y0 = round(bx - 0.25 * self.UNIT), round(by - 0.12 * self.UNIT)  # 重读的裁剪左上角
        s = 2 * ocr_scale(1920, 1080)
        # 原图上 (1490, 320)~(1510, 340)：按钮正下方 30 像素
        box = quad((1490 - x0) * s, (320 - y0) * s, (1510 - x0) * s, (340 - y0) * s)
        found = SimpleNamespace(boxes=np.array([box]), txts=("×2",))
        self.assertEqual(self.read([self.button()], found), (2, 1))


class OutOfGameOrderTest(unittest.TestCase):
    """几种画面的字样同时出现时，按登记表的先后认第一个认得出的。"""

    def test_order(self):
        def ln(text, cx, cy, h=20.0):
            return Line(text, float(cx), float(cy), h * len(text), float(h))

        layers = [
            ([ln("异变图书馆(合作挑战)", 400, 1000), ln("噩梦", 900, 1000), ln("12轮", 1000, 1002)],
             OutOfGame(screens.RESULT, "噩梦", "异变图书馆", 12)),
            ([ln("匹配成功！", 960, 200), ln("异变图书馆·噩梦", 960, 300)],
             OutOfGame(screens.MATCH_FOUND, "噩梦", "异变图书馆")),
            ([ln("游玩地图", 300, 150), ln("星趴·梦想号", 420, 150), ln("难度", 300, 250), ln("普通", 420, 250)],
             OutOfGame(screens.CREATE_ROOM, "普通", "星趴·梦想号")),
            ([ln("选择难度", 200, 800, 30), ln("普通", 200, 880), ln("困难", 400, 880)], OutOfGame(screens.TEAM_ROOM)),
            ([ln("获得奖励", 300, 400), ln("每周对局获取上限 365/1000", 300, 700)], OutOfGame(screens.REWARD)),
        ]
        for i, (_, want) in enumerate(layers):
            lines = [line for part, _ in layers[i:] for line in part]  # 这一种和排在它后面的几种都在画面上
            with self.subTest(kind=want.kind):
                self.assertEqual(parse_out_of_game(None, lines, DB), want)
                # 文字倒过来排也一样：认哪一种由登记表的先后定，不由识别结果里文字的先后定
                self.assertEqual(parse_out_of_game(None, lines[::-1], DB), want)


if __name__ == "__main__":
    unittest.main()
