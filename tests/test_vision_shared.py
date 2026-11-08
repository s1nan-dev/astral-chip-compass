"""识别模块共用的小工具（advisor/vision/io.py）和对局外画面解析器的登记表。

  识别结果转 Line   没有识别框、缺字段时为空表；坐标按缩放倍数和裁剪的左上角换回原图像素；
  按范围识别        空画面不调用识别引擎；范围夹在画面内（超出画面、落在最后一行一列上照样裁出一块）；坐标换回原图像素；
  刷新次数的重读    三选一右侧「刷新」按钮上、按钮下方都没读到数字时放大重读：重读什么都没读到时是 None（按钮在、数字读不出），
                    重读到的「×N」按裁剪位置换回原图后照常认；按钮上或第一遍就读到数字时不重读；
  对局外画面的先后  几种画面的字样同时出现时按登记表（screens.PARSERS）的先后认：结算页、匹配成功页、房间、组队房间、奖励页、
                    主菜单。
  共用的模板缓存    两个识别线程共用 templates.assets：一个线程重建缓存（清空各张表）时，另一个线程的读法不抛 KeyError。
识别引擎一律换成假的，不加载模型。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from advisor.data import load_db
from advisor.vision import chip_select, hud, io, screens
from advisor.vision.anchors import MARK_CROP, MENU_RIGHT, TITLE
from advisor.vision.chip_select import TITLE_WIDTH_U, Line, ocr_scale
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

    def test_det_side_restored(self):
        """det_side、pad：只在这一次识别里改找字一步的放大设置（text_det.limit_side_len）和补边的宽高比
        （width_height_ratio，-1 是不按宽高比补边），识别完（出错时也一样）还原；不给时不动它们。"""
        seen = []
        engine = mock.Mock(side_effect=lambda img: seen.append(
            (engine.text_det.limit_side_len, engine.width_height_ratio)) or NOTHING)
        engine.text_det = SimpleNamespace(limit_side_len=736)
        engine.width_height_ratio = 8
        with mock.patch.object(io, "_engine", return_value=engine):
            io.ocr(self.img, (0, 0, 860, 50), det_side=288, pad=False)
            io.ocr(self.img, (0, 0, 860, 50))
            io.ocr(self.img, (0, 0, 860, 50), det_side=368)
            self.assertEqual(seen, [(288, -1), (736, 8), (368, 8)])
            self.assertEqual((engine.text_det.limit_side_len, engine.width_height_ratio), (736, 8))
            engine.side_effect = RuntimeError("识别出错")
            with self.assertRaises(RuntimeError):
                io.ocr(self.img, (0, 0, 860, 50), det_side=288, pad=False)
        self.assertEqual((engine.text_det.limit_side_len, engine.width_height_ratio), (736, 8))

    def test_missing_det_settings(self):
        """引擎上没有这两个设置（换了版本）时照原样识别，不报错。"""
        engine = mock.Mock(spec=["__call__"], return_value=NOTHING)
        with mock.patch.object(io, "_engine", return_value=engine):
            self.assertEqual(io.ocr(self.img, (0, 0, 860, 50), det_side=288, pad=False), [])
        engine.assert_called_once()


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


class RoundMarkTest(unittest.TestCase):
    """左上角的轮次：简体的「轮」、繁体的「Round」（大小写、读错一个字母都认）和「輪」。坐标量自繁体截图，
    换成 1080p 的界面单位。"""

    def test_simplified(self):
        self.assertEqual(hud._parse_round_ex([Line("03 轮", 170, 60, 130, 50)], 1920, 1080), (3, True))
        self.assertEqual(hud._parse_round_ex([Line("03", 134, 58, 77, 60), Line("轮", 190, 60, 30, 40)],
                                             1920, 1080), (3, True))

    def test_traditional_round(self):
        """繁体的「Round」是数字右下方的小字，和数字分成两段；同一段也能认。"""
        for word in ("Round", "round", "ROUND", "Rounc"):
            with self.subTest(word=word):
                lines = [Line("01", 134, 58, 77, 60), Line(word, 206, 68, 56, 21)]
                self.assertEqual(hud._parse_round_ex(lines, 1920, 1080), (1, True))
        self.assertEqual(hud._parse_round_ex([Line("02 Round", 170, 60, 130, 50)], 1920, 1080), (2, True))

    def test_traditional_chinese_word(self):
        self.assertEqual(hud._parse_round_ex([Line("03 輪", 170, 60, 130, 50)], 1920, 1080), (3, True))

    def test_by_position_when_mark_not_read(self):
        """标记认不出时按位置兜底：左上角字很大的两位数照常读到。"""
        self.assertEqual(hud._parse_round_ex([Line("02", 134, 58, 77, 60)], 1920, 1080), (2, False))

    def test_name_tag_is_not_a_mark(self):
        """轮次框的下边盖到玩家列表第一行的名牌（中心离上边约 128 个界面单位）：昵称里带「Round」「輪」时不当成标记，
        标记认不出时照常按位置读到数字，也不在名牌左边裁图重读。数字那一行的「Round」照常是标记。"""
        digits = Line("02", 114, 40, 77, 60)
        for nick in ("Ground", "【Round】", "Background", "輪迴", "Round"):
            with self.subTest(nick=nick):
                tag = Line(nick, 224, 128, 120, 32)
                self.assertEqual(hud._parse_round_ex([digits, tag], 1920, 1080), (2, False))
                self.assertIsNone(hud._round_mark([digits, tag], 1920, 1080))
                lines = [digits, Line("Round", 185, 50, 56, 21), tag]
                self.assertEqual(hud._parse_round_ex(lines, 1920, 1080), (2, True))
                self.assertIs(hud._round_mark(lines, 1920, 1080), lines[1])

    def test_mark_crop_covers_round_digits(self):
        """数字检测不出时在标记左边裁的那一块（MARK_CROP，左边到菜单按钮为止）按「Round」的位置算，也要盖住数字
        （量自繁体截图，去掉了截图四周的黑边）。"""
        left, cy = 157.0, 50.0  # 「Round」左边缘、中心 y
        x0, x1 = max(MENU_RIGHT, left + MARK_CROP[0]), left + MARK_CROP[2]
        y0, y1 = cy + MARK_CROP[1], cy + MARK_CROP[3]
        self.assertTrue(x0 <= 76 and x1 >= 153 and y0 <= 10 and y1 >= 70)

    def test_mark_inside_segment(self):
        """标记和头像、数字连成一段时也认：繁体截图上兔耳朵挡在数字和「Round」之间，整段读成「ARound」；数字和标记连成
        「03轮」。标记的左边缘按这一段的右端减去标记的宽度推，裁出的那一块照样盖住数字、不伸进标记；段首的标记照旧取
        这一段的左边缘。坐标是 1080p 的界面单位（量自繁体截图：数字框 x 约 76~153、y 约 10~70，「Round」框 157~213）。"""
        ears = Line("ARound", 178, 48, 71, 29)  # 右端 213.5
        self.assertIs(hud._round_mark([Line("1st", 54, 106, 40, 25), ears], 1920, 1080), ears)
        self.assertAlmostEqual(hud._mark_left(ears, 1.0), 213.5 - 56)
        x0, y0, x1, y1 = hud._mark_crop(ears, 1920, 1080)
        self.assertTrue(x0 <= 76 and 153 <= x1 <= 213.5 - 56 + 5 and y0 <= 10 and y1 >= 70, (x0, y0, x1, y1))
        glued = Line("03轮", 115, 36, 95, 63)  # 「轮」字右端 162.5
        self.assertAlmostEqual(hud._mark_left(glued, 1.0), 162.5 - 30)
        x0, y0, x1, y1 = hud._mark_crop(glued, 1920, 1080)
        self.assertTrue(y0 <= 4.5 and y1 >= 67.5, (y0, y1))  # 连成一段时上下放到这一段的上下边
        head = Line("轮19/16", 200, 54, 80, 50)  # 园林中庭：「轮」字和右边的进度糊成一段
        self.assertAlmostEqual(hud._mark_left(head, 1.0), 160)
        # 数字那一行以外（玩家列表第一行的名牌）带标记的不算，简体也一样
        for nick in ("轮回", "【Round】", "A輪"):
            with self.subTest(nick=nick):
                self.assertIsNone(hud._round_mark([Line(nick, 224, 128, 120, 32)], 1920, 1080))

    def test_crop_menu_prefix(self):
        """裁的那一块左边贴着菜单按钮时，按钮边缘读成的「1」「3.」前缀去掉（同 _round_digits）；没贴着时照原样。"""
        self.assertEqual(hud._crop_digits("109", True), "09")
        self.assertEqual(hud._crop_digits("3.13", True), "13")
        self.assertEqual(hud._crop_digits("1 1", True), "11")
        self.assertEqual(hud._crop_digits("109", False), "109")
        self.assertEqual(hud._crop_digits("20", True), "20")

    def test_round_like_needs_digit_look(self):
        """数字的位置上没读成轮次的文字，像读错的两位数（带数字，或只有一两个形近的字母、一个形近的汉字）才换大小重读；
        大厅左上角的活动通行证名、房间的「返回」「MODE」、盖在数字上的怪物名牌不重读。"""
        def like(text):
            return hud._round_like([Line(text, 114, 40, 77, 60)], 1.0)

        for text in ("t0", "£O", "以", "LO", "3.13", "109", "60", "2", "00"):
            with self.subTest(text=text):
                self.assertTrue(like(text))
        for text in ("晨暑驚眠通行證", "小红帽通行证", "返回", "MODE", "茶壶", "LOE"):
            with self.subTest(text=text):
                self.assertFalse(like(text))


class RoundReadTest(unittest.TestCase):
    """read_round_info 的先后：第一遍靠标记读到的数在标记左边那一块只认字核对一次，读出另一个数时作废；看见标记、
    数字没读出来时先在标记左边裁一小块只认字，读不出按读不出处理、不再换大小重读；没看见标记、数字的位置上有像读错的
    两位数时才放大、缩小各再读一次。识别引擎换成假的（io.ocr、recognize_only）。"""

    W, H = 1920, 1080
    MARK = Line("轮", 168, 43, 31, 28)

    def setUp(self):
        self.img = np.zeros((self.H, self.W, 3), np.uint8)
        self.calls = []

    def read(self, passes, reads, retry=True):
        """passes：每一遍 io.ocr 交回的 Line 列表（依次）；reads：recognize_only 依次交回的结果。"""
        def fake_ocr(img, box, width=None):
            self.calls.append(width)
            return passes[min(len(self.calls), len(passes)) - 1]

        with mock.patch.object(io, "ocr", side_effect=fake_ocr), \
                mock.patch.object(hud, "recognize_only", side_effect=reads) as rec:
            got = hud.read_round_info(self.img, retry=retry)
        return got, rec.call_count

    def test_strong_first_pass(self):
        """第一遍靠标记读到的数，在交回的那一块上只认字核对一次：读出同一个数、读不出（挡住了）、置信度不够、两种大小
        不一致时照用；读出另一个数时按读不出处理（mark、crop 照旧），也不换大小重读。"""
        digits = [Line("03", 114, 40, 77, 60), self.MARK]
        crop = hud._mark_crop(self.MARK, self.W, self.H)
        cases = (
            ([[("03", 0.99)], [("03", 0.97)]], 3, 2),
            ([[]], 3, 1),
            ([[("08", 0.5)]], 3, 1),
            ([[("08", 0.99)], [("05", 0.99)]], 3, 2),
            ([[("08", 0.99)], [("08", 0.98)]], None, 2),
        )
        for reads, want, recs in cases:
            self.calls.clear()
            with self.subTest(reads=reads):
                got, rec = self.read([digits], reads)
                self.assertEqual((got.value, got.mark, got.crop, got.passes, rec, len(self.calls)),
                                 (want, True, crop, 1, recs, 1))
        # 「轮」字不在数字那一行（_round_mark 不认）：没有那一块，不核对
        self.calls.clear()
        low = Line("轮", 168, 110, 31, 28)
        got, rec = self.read([[Line("03", 114, 105, 77, 60), low]], [])
        self.assertEqual((got.value, got.mark, got.crop, rec), (3, False, None, 0))

    def test_mark_seen_digits_missed(self):
        got, rec = self.read([[self.MARK]], [[("03", 0.99)], [("03", 0.97)]])
        self.assertEqual((got.value, got.mark, got.passes, rec, len(self.calls)), (3, True, 1, 2, 1))
        self.assertIsNotNone(got.crop)
        # 裁小块读不出（挡住了）：按读不出处理，不换大小重读
        self.calls.clear()
        got, rec = self.read([[self.MARK], [Line("10", 114, 40, 77, 60), self.MARK]], [[("03", 0.99)], [("08", 0.99)]])
        self.assertEqual((got.value, got.mark, got.passes, rec, len(self.calls)), (None, True, 1, 2, 1))

    def test_glued_mark_crop(self):
        """兔耳朵和「Round」连成「ARound」：照样认出标记，在它左边裁一小块读数字。"""
        got, rec = self.read([[Line("ARound", 178, 48, 71, 29)]], [[("03", 0.95)], [("03", 0.95)]])
        self.assertEqual((got.value, got.mark, got.passes), (3, True, 1))

    def test_nothing_read(self):
        got, rec = self.read([[]], [])
        self.assertEqual((got.value, got.mark, got.crop, got.passes, rec), (None, False, None, 1, 0))

    def test_round_like_rereads(self):
        """没看见标记、数字的位置上读成「t0」：放大、缩小各再读一次，重读时靠标记读到的直接用；重读时才看见标记、
        数字仍没读出时裁小块。"""
        t0 = Line("t0", 114, 40, 77, 60)
        got, rec = self.read([[t0], [Line("04", 114, 40, 77, 60), self.MARK]], [])
        self.assertEqual((got.value, got.mark, got.passes), (4, True, 2))
        self.assertEqual(self.calls[1:], [round(1920 * 1.5)])
        self.calls.clear()
        got, rec = self.read([[t0], [t0, self.MARK]], [[("04", 0.99)], [("04", 0.99)]])
        self.assertEqual((got.value, got.mark, got.passes, rec), (4, True, 2, 2))
        self.calls.clear()
        got, rec = self.read([[t0], [t0], [t0]], [])
        self.assertEqual((got.value, got.mark, got.passes, rec), (None, False, 3, 0))

    def test_retry_false_reads_only_once(self):
        """retry=False：第一遍没看见标记、数字的位置上读成像认错的两位数（M7）时不再放大、缩小重读，只认一遍，
        第一遍按位置读到的数也不采用；retry=True 时照旧重读两遍。靠标记读到的那一路不受 retry 影响。"""
        m7 = Line("M7", 114, 40, 77, 60)
        self.calls.clear()
        got, rec = self.read([[m7]], [], retry=False)
        self.assertEqual((got.value, got.mark, got.crop, got.passes, rec, len(self.calls)),
                         (None, False, None, 1, 0, 1))
        self.calls.clear()
        got, rec = self.read([[m7]], [], retry=True)
        self.assertEqual((got.value, got.mark, got.passes, len(self.calls)), (None, False, 3, 3))
        # 看见标记、数字没读出来时照旧裁一小块只认字，retry 不影响
        self.calls.clear()
        got, rec = self.read([[self.MARK]], [[("03", 0.99)], [("03", 0.97)]], retry=False)
        self.assertEqual((got.value, got.mark, got.passes), (3, True, 1))

    def test_no_retry_counted_only_when_it_skips(self):
        """round_no_retry 只数真的因为 retry 为假跳过了重读的那一次：第一遍什么都不像（本来就不会重读）不数，
        retry 为真时照旧重读也不数。"""
        m7 = Line("M7", 114, 40, 77, 60)
        for passes, retry, want in (([[m7]], False, ["round_no_retry"]), ([[]], False, []), ([[m7]], True, [])):
            self.calls.clear()
            counted = []
            with mock.patch.object(hud.perfstats, "count", side_effect=counted.append):
                self.read(passes, [], retry=retry)
            with self.subTest(retry=retry, passes=passes):
                self.assertEqual(counted, want)

    def test_hud_carries_mark(self):
        """read_hud 照 read_round_info 填 round_mark、round_crop（只读轮次、连任务栏一起读都填），retry 原样传下去
        （不给时为真）。"""
        info = hud.RoundRead(None, True, (65.0, 0.0, 160.0, 70.0), 1)
        with mock.patch.object(hud, "read_round_info", return_value=info) as round_info, \
                mock.patch.object(io, "ocr", return_value=[]):
            for full in (False, True):
                for retry in (None, True, False):
                    with self.subTest(full=full, retry=retry):
                        kw = {} if retry is None else {"retry": retry}
                        got = hud.read_hud(self.img, DB, full=full, **kw)
                        self.assertEqual((got.round, got.round_mark, got.round_crop),
                                         (None, True, (65.0, 0.0, 160.0, 70.0)))
                        self.assertEqual(round_info.call_args.kwargs.get("retry"), retry is not False)


class TitleLanguageTest(unittest.TestCase):
    """三选一的标题简繁两种写法都认；其他语言的标题照旧判为「像是其他语言的三选一」。"""

    UNIT = 200.0

    def read(self, title, names=()):
        lines = [Line(title, 960.0, 200.0, TITLE_WIDTH_U * self.UNIT, 40.0)]
        for i, name in enumerate(names):
            lines.append(Line(name, 960.0 + (i - 1) * self.UNIT, 200.0 + 0.243 * self.UNIT, 100.0, 30.0))
        lines.append(Line("刷新×2", 1500.0, 300.0, 60.0, 30.0))
        info = {}
        with mock.patch.object(chip_select, "_ocr_lines", return_value=lines), \
                mock.patch.object(chip_select, "_engine", return_value=mock.Mock(return_value=NOTHING)):
            seen = chip_select.read_chip_select(np.zeros((1080, 1920, 3), np.uint8), DB, info)
        return seen, info

    def test_traditional_title(self):
        """繁体标题认得出，三张候选交回简体名，info["foreign"] 为假。"""
        chips = [c for c in DB.chips.values() if c.hant and c.hant != c.name][:3]
        seen, info = self.read("請選擇一枚戰鬥籌碼", [c.hant for c in chips])
        self.assertIsNotNone(seen)
        self.assertEqual(seen.names, [c.name for c in chips])
        self.assertIs(info["foreign"], False)
        self.assertIs(seen.hant, True)

    def test_simplified_title(self):
        chips = [c for c in DB.chips.values() if c.name][:3]
        seen, info = self.read(TITLE, [c.name for c in chips])
        self.assertIsNotNone(seen)
        self.assertEqual(seen.names, [c.name for c in chips])
        self.assertIs(info["foreign"], False)
        self.assertIs(seen.hant, False)

    def test_japanese_title_is_foreign(self):
        """其他语言的标题读不出简体、繁体标题：info["foreign"] 为真（照旧提示改语言）。"""
        seen, info = self.read("バトルチップを選択")
        self.assertIsNone(seen)
        self.assertIs(info["foreign"], True)


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
            ([ln("开始游戏", 2283, 1065, 110),
              *[ln(t, 1666 + 118 * i, 1259)
                for i, t in enumerate(("任务", "好友", "公告", "仓库", "图鉴", "邮件", "设置", "退出"))]],
             OutOfGame(screens.MAIN_MENU)),
        ]
        for i, (_, want) in enumerate(layers):
            lines = [line for part, _ in layers[i:] for line in part]  # 这一种和排在它后面的几种都在画面上
            with self.subTest(kind=want.kind):
                self.assertEqual(parse_out_of_game(None, lines, DB), want)
                # 文字倒过来排也一样：认哪一种由登记表的先后定，不由识别结果里文字的先后定
                self.assertEqual(parse_out_of_game(None, lines[::-1], DB), want)


class TemplateCacheThreadTest(unittest.TestCase):
    """两个识别线程共用的模板缓存（templates.assets）：一边反复重建（reload），一边反复读，不抛 KeyError。"""

    def test_reload_while_reading(self):
        import threading
        import time
        import traceback

        from advisor.vision import stars, templates

        self.addCleanup(templates.assets.reload)
        stop = threading.Event()
        failures = []
        reads = [0]

        def read():
            try:
                while not stop.is_set():
                    # 没有头像、没有角色：建出来是空表，够快；要撞的是「存进表里」和「取出来」之间被清空
                    stars._hero_photos(1)
                    stars._face_templates(1, 0.1)
                    stars._still_faces(0.1)
                    reads[0] += 1
            except BaseException:  # noqa: BLE001  记下来交给主线程断言
                failures.append(traceback.format_exc())
                stop.set()

        with mock.patch.object(stars, "photo_dirs", return_value=()), \
                mock.patch.object(stars, "_all_hero_ids", return_value=(1,)):
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                start = time.monotonic()
                while time.monotonic() - start < 1.0:
                    templates.assets.reload()
            finally:
                stop.set()
                reader.join(3)
        self.assertEqual(failures, [])
        self.assertGreater(reads[0], 0)

    def test_last_skin_read_once(self):
        """认自己那一行时上次用的那套皮肤（_last_skin）只取一次：取完以后表正好被另一个识别线程清空（重建缓存），照样先试
        取到的那一套，不抛 KeyError。表换成取一次就清空自己的，不靠两个线程碰巧撞上。"""
        from advisor.vision import stars, templates

        class ClearedAfterGet(dict):
            def get(self, key, default=None):
                value = super().get(key, default)
                self.clear()
                return value

        layout = SimpleNamespace(rows=[], f=0.1, gray=None, anchors=[], u=1.0)
        frame = stars.as_frame(np.zeros((8, 8, 3), np.uint8))
        with mock.patch.object(stars, "_layout", return_value=(layout, "", 1.0)), \
                mock.patch.object(stars, "_face_templates", return_value=[[]]), \
                mock.patch.object(templates.assets, "_last_skin", ClearedAfterGet({1: 0})):
            report = stars._diagnose(frame, 1)
        self.assertIsNone(report.result)
        self.assertTrue(report.reason.startswith("认不出自己是哪一行"), report.reason)


if __name__ == "__main__":
    unittest.main()
