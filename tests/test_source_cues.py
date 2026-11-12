"""三选一来源的两样画面线索（advisor/vision/source_cues.py）：升星横幅「LV UP.」和筹码商店的购买窗。

A. 颜色预筛（glance，造的画面，不做文字识别）：粉色斜带占比够、长宽和斜度对才算像横幅；横着的带子、太短太细的带子、
   方块、整屏粉色都不算（这几种都确实走到了形状判断）。左边钮的位置上有一块饱和的蓝或绿、右边钮的位置上有一块饱和的
   粉或绿才算像购买窗；左边粉色、两个都蓝、只有一块、暖色、整屏铺满都不算。宽屏（21:9）、窄屏（4:3）的窗口上，横幅和
   两个钮照界面缩放从画面中心摆、照画面比例摆，都认得出。带透明通道的照常看，灰度图、空图不像；识别流程交的 `Frame` 也认。
B. 认名次（read_rank，文字识别换成假的）：只认字母和数字，1ST 被读成 IST／LST、GOT 被读成 COT、O 读成 0、T 读成 7
   都认；只有名次没有 GOT、只有 GOT 没有名次、名次不在开头、5TH 都不算；前一行是别的字、后一行是横幅的照样认出。
C. 认购买窗（read_purchase，文字识别换成假的）：「离开／離開」和「购买／購買」两个都读到才算；价格取「购买！」下面
   那一带里的 1～3 位数（O 读成 0 也认），读不出价格时 price 为 None；读到「转账／轉帳／转帐／轉賬」「返回」「卡牌商店」
   里任何一个都不算；缺一个钮的字不算；钮下面别处的数字不当价格。
D. 录像截图（samples/source/、samples/pages/ 和 samples/ 第一层，不随仓库发布，缺了跳过）：横幅 8 张读出各自的名次；
   购买窗 5 张读出价格；FIGHT! 过场、通关红底、星币转账窗、卡牌商店都不是这两样；普通对局画面、三选一、「查看筹码」
   面板、任务面板、通关红底上的大字、打输的蓝带也不是。对的样本再缩到 1280x720、放大到 2560x1440 各试一次；
   再在两边、上下补边模拟 21:9、4:3 的窗口（界面照界面缩放摆在中间），横幅读出名次、购买窗的颜色预筛照样说像。
"""
from __future__ import annotations

import unittest
from unittest import mock

import cv2
import numpy as np

from advisor.vision import io, source_cues
from advisor.vision.frame import Frame
from tests.support import samples

# 横幅（名次）；来源：录像帧，简体、繁体两种客户端
BANNERS = (
    ("source/20261017_012808_f007590_levelup_banner_4th_own_1920x1080.png", 4),
    ("source/20261018_170836_f004485_levelup_banner_4th_own_1920x1080.png", 4),
    ("source/20261105_010644_f017985_levelup_banner_1st_own_1920x1080.png", 1),
    ("source/20261105_010644_f040185_levelup_banner_1st_own_1920x1080.png", 1),
    ("source/20261105_010644_f009486_levelup_banner_3rd_teammate_1920x1080.png", 3),
    ("source/20261105_010644_f018720_levelup_banner_2nd_teammate_1920x1080.png", 2),
    ("source/20261017_012808_f017595_levelup_banner_1st_teammate_1920x1080.png", 1),
    ("20261003_124944_hud_levelup_banner_al_real.jpg", 1),
)
# 购买窗（价格）
SHOPS = (
    ("source/20261017_012808_f006786_chipshop_buy_10_1920x1080.png", 10),
    ("source/20261017_012808_f026616_chipshop_buy_hover_15_1920x1080.png", 15),
    ("source/20261105_010644_f017739_chipshop_buy_hant_10_1920x1080.png", 10),
    ("source/20261018_170836_f012999_chipshop_buy_1920x1080.png", 10),
    ("20261002_015454_chipshop_prompt.png", 10),
)
# 也不是这两样的画面：FIGHT! 过场、通关红底、星币转账窗、卡牌商店
NOT_CUES = (
    "source/20261017_012808_f016788_fight_transition_1920x1080.png",
    "source/20261105_010644_f051270_victory_arrow_1920x1080.png",
    "source/20261105_010644_f005310_coin_transfer_hant_1920x1080.png",
    "20261002_214211_coin_transfer_real.jpg",
    "source/20261017_012808_f007461_card_shop_1920x1080.png",
    "20261002_214207_card_shop_transfer_button_1600x855.jpg",
)
# 普通对局画面和三选一（反例）；后六张两个钮的位置上都有大块饱和色（「查看筹码」面板、任务面板、棋盘上「继续移动／停留」
# 的提示、通关红底上的大字、打输的蓝带），两个框都收蓝、粉、绿三种颜色的话颜色预筛会说像购买窗
NEGATIVES = (
    "20261002_012918_hud_round1.png",
    "20261002_013522_chipselect_levelup_star1_badge_on.png",
    "20261002_013617_chipselect_selected_state.png",
    "20261002_144039_hud_round1_2560x1057.jpg",
    "20261002_212245_chipselect_levelup_star1_real.jpg",
    "20261002_213905_hud_round7_real.jpg",
    "20261003_124642_hud_round1_skull_cell_2560x1369.jpg",
    "20261003_130112_chipselect_team_task_tier2_star1_al_real.jpg",
    "20261002_021351_battle_scene.png",
    "20261002_144036_loading_2560x1057.jpg",
    "20261003_132534_reward_2560x1369.jpg",
    "20261002_012947_task_panel.png",
    "20261002_012956_chips_panel_empty.png",
    "20261003_131252_chips_panel_six_chips_real.jpg",
    "20261003_131958_hud_round11_digits_missed_real.jpg",
    "pages/20261105_010644_f051294_victory_text_1920x1080.png",
    "pages/20261106_235502_f041328_game_over_banner_1920x1080.png",
)
ALL_SAMPLES = tuple(name for name, _ in BANNERS) + tuple(name for name, _ in SHOPS) + NOT_CUES + NEGATIVES


def hsv_bgr(hue: int, sat: int = 220, val: int = 230) -> tuple[int, int, int]:
    return tuple(int(v) for v in cv2.cvtColor(np.uint8([[[hue, sat, val]]]), cv2.COLOR_HSV2BGR)[0, 0])


PINK, BLUE, GREEN, ORANGE = hsv_bgr(166), hsv_bgr(122), hsv_bgr(46), hsv_bgr(20)
LEFT_CENTER, RIGHT_CENTER = (117, 171), (187, 171)  # 480x270 上两个钮的中心（照录像量的比例）
# 造的宽屏、窄屏画面：和 480x270 一样每个点是 4 个界面单位（界面缩放 0.25），多出来的宽或高是补的边
WIDE, NARROW = (640, 270), (480, 360)


def banner_frame(angle: float = 17.0, long: float = 300.0, thick: float = 96.0, size=(480, 270)) -> np.ndarray:
    """深色底的画面中间画一条粉色斜带：长、短边和斜度照录像里量到的来（480x270 上的点数）。"""
    w, h = size
    img = np.full((h, w, 3), 40, np.uint8)
    rad = np.radians(angle)
    along, normal = np.array([np.cos(rad), -np.sin(rad)]), np.array([np.sin(rad), np.cos(rad)])
    center = np.array([w / 2, h / 2])
    a, n = along * long / 2, normal * thick / 2
    # 四个角按绕一圈的顺序给：顺序错了会画成自交的蝴蝶结，只填一半
    points = np.array([center - a - n, center + a - n, center + a + n, center - a + n], np.int32)
    cv2.fillPoly(img, [points], PINK)
    return img


def shop_frame(left=BLUE, right=PINK, size=(480, 270), centered: bool = True) -> np.ndarray:
    """深色底上、两个钮的位置各画一个圆钮。不是 480x270 时：centered 为真照界面缩放从画面中心摆（钮的大小不变），
    为假照 16:9 的比例摆。"""
    w, h = size
    img = np.full((h, w, 3), 40, np.uint8)
    for (cx, cy), radius, color in ((LEFT_CENTER, 21, left), (RIGHT_CENTER, 25, right)):
        if centered:
            x, y = round(w / 2 + cx - 240), round(h / 2 + cy - 135)
        else:
            x, y = round(cx * w / 480), round(cy * h / 270)
        cv2.circle(img, (x, y), radius, color, -1)
    return img


def pad(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """补深色的边到 size（宽、高），原图放在正中：模拟同样大小的界面摆在更宽或更高的窗口中间。"""
    h, w = img.shape[:2]
    left, top = (size[0] - w) // 2, (size[1] - h) // 2
    return cv2.copyMakeBorder(img, top, size[1] - h - top, left, size[0] - w - left, cv2.BORDER_CONSTANT,
                              value=(40, 40, 40))


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def resized(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    interp = cv2.INTER_AREA if size[0] <= img.shape[1] else cv2.INTER_CUBIC
    return cv2.resize(img, size, interpolation=interp)


def crop_size() -> tuple[int, int]:
    x0, y0, x1, y1 = source_cues._box(source_cues.PURCHASE_BOX, 1920, 1080)
    return x1 - x0, y1 - y0


def line(text: str, cx: float = 0.5, cy: float = 0.5) -> io.Line:
    w, h = crop_size()
    return io.Line(text, cx * w, cy * h, 60.0, 50.0)


class GlanceTest(unittest.TestCase):
    def test_banner(self):
        self.assertTrue(source_cues.glance(banner_frame()).banner)
        self.assertFalse(source_cues.glance(banner_frame()).shop)  # 斜带盖住钮也不算购买窗
        self.assertTrue(source_cues.glance(Frame(banner_frame())).banner)  # 识别流程交的是 Frame

    def test_banner_shape(self):
        # 粉色占比都到了 BANNER_SHARE，形状判断确实做了、说不像
        thin = banner_frame(thick=40.0)
        for x, y in ((5, 5), (40, 5), (5, 35), (40, 35), (410, 5), (445, 5), (410, 35), (445, 35)):
            cv2.rectangle(thin, (x, y), (x + 24, y + 24), PINK, -1)  # 补几块小的粉色凑够占比，最大一块仍是细带子
        square = np.full((270, 480, 3), 40, np.uint8)
        cv2.rectangle(square, (165, 60), (315, 210), PINK, -1)
        cases = (
            ("横着的带子", banner_frame(angle=0.0)),
            ("长边不够", banner_frame(long=200.0)),
            ("短边不够", thin),
            ("方块：长边不到参考窗口的一半宽", square),
            ("整屏粉：斜度 0", np.full((270, 480, 3), PINK, np.uint8)),
        )
        for label, img in cases:
            with self.subTest(label), mock.patch.object(
                source_cues, "_banner_shape", wraps=source_cues._banner_shape
            ) as shape:
                self.assertFalse(source_cues.glance(img).banner)
                shape.assert_called_once()

    def test_shop(self):
        self.assertTrue(source_cues.glance(shop_frame()).shop)
        self.assertFalse(source_cues.glance(shop_frame()).banner)
        for left, right in ((BLUE, GREEN), (GREEN, PINK)):  # 鼠标指着的钮变绿
            with self.subTest(left=left, right=right):
                self.assertTrue(source_cues.glance(shop_frame(left, right)).shop)
        self.assertTrue(source_cues.glance(Frame(shop_frame())).shop)

    def test_shop_negatives(self):
        for label, left, right in (
            ("左边粉色（「查看筹码」面板、通关红底这类）", PINK, PINK),
            ("两个都蓝（打输的蓝带）", BLUE, BLUE),
            ("左边粉、右边蓝", PINK, BLUE),
            ("右边暖色", BLUE, ORANGE),
            ("左边暖色", ORANGE, PINK),
        ):
            with self.subTest(label):
                self.assertFalse(source_cues.glance(shop_frame(left, right)).shop)
        only_left = np.full((270, 480, 3), 40, np.uint8)
        cv2.circle(only_left, LEFT_CENTER, 21, BLUE, -1)
        self.assertFalse(source_cues.glance(only_left).shop)  # 只有一块
        for color in (PINK, GREEN):  # 整屏铺满
            with self.subTest(color=color):
                self.assertFalse(source_cues.glance(np.full((270, 480, 3), color, np.uint8)).shop)

    def test_wide_and_narrow_windows(self):
        # 宽屏、窄屏的窗口：界面照界面缩放摆在中间（三选一界面就是这样摆的），或者跟着画面比例摆
        for size in (WIDE, NARROW):
            with self.subTest(size=size):
                self.assertTrue(source_cues.glance(banner_frame(size=size)).banner)
                self.assertTrue(source_cues.glance(shop_frame(size=size)).shop)
                self.assertTrue(source_cues.glance(shop_frame(size=size, centered=False)).shop)
                self.assertFalse(source_cues.glance(shop_frame(PINK, PINK, size=size)).shop)
        # 跟着画面比例拉伸的横幅：21:9 上压平到约 13°，4:3 上立起到约 22°，都还在斜度范围里
        stretched = cv2.resize(banner_frame(), WIDE, interpolation=cv2.INTER_NEAREST)
        self.assertTrue(source_cues.glance(stretched).banner)
        stretched = cv2.resize(banner_frame(), NARROW, interpolation=cv2.INTER_NEAREST)
        self.assertTrue(source_cues.glance(stretched).banner)

    def test_alpha_grey_and_empty(self):
        bgra = cv2.cvtColor(banner_frame(), cv2.COLOR_BGR2BGRA)
        self.assertTrue(source_cues.glance(bgra).banner)
        self.assertTrue(source_cues.glance(cv2.cvtColor(shop_frame(), cv2.COLOR_BGR2BGRA)).shop)
        self.assertEqual(source_cues.glance(np.full((270, 480), 200, np.uint8)), source_cues.NOTHING)
        self.assertEqual(source_cues.glance(np.zeros((0, 0, 3), np.uint8)), source_cues.NOTHING)


class ReadRankTest(unittest.TestCase):
    def read(self, *texts: str) -> int | None:
        lines = [io.Line(t, 400.0, 200.0, 300.0, 80.0) for t in texts]
        with mock.patch.object(io, "ocr", return_value=lines):
            return source_cues.read_rank(np.zeros((1080, 1920, 3), np.uint8))

    def test_names(self):
        for texts, want in ((("4TH GOT A NEW STAR",), 4), (("4THGOTANEW",), 4), (("3RDGOTANEL",), 3),
                            (("2ND GOT A NEW STAE",), 2), (("1ST GOT A NEW STAR",), 1),
                            (("IST COT ANET",), 1), (("lST GOT A NEW",), 1), (("ATH GOT A NEW",), 4),
                            (("4TH GO7 A NEW",), 4), (("4TH G0T A NEW",), 4)):
            with self.subTest(texts=texts):
                self.assertEqual(self.read(*texts), want)

    def test_not_banner(self):
        for texts in (("FIGHT!",), ("GOT A NEW STAR",), ("4TH NEW STAR",), ("5TH GOT A NEW STAR",),
                      ("VICTORY",), ("",), (None,), ()):
            with self.subTest(texts=texts):
                self.assertIsNone(self.read(*texts))

    def test_other_line_before_banner(self):
        self.assertEqual(self.read("FIGHT", "4TH GOT A NEW STAR"), 4)

    def test_grey_and_empty(self):
        with mock.patch.object(io, "ocr", side_effect=AssertionError("不该认字")):
            self.assertIsNone(source_cues.read_rank(np.full((1080, 1920), 200, np.uint8)))
            self.assertIsNone(source_cues.read_rank(np.zeros((0, 0, 3), np.uint8)))


class ReadPurchaseTest(unittest.TestCase):
    def read(self, *lines: io.Line) -> source_cues.Purchase | None:
        with mock.patch.object(io, "ocr", return_value=list(lines)):
            return source_cues.read_purchase(np.zeros((1080, 1920, 3), np.uint8))

    def test_purchase(self):
        for lines, want in (
            ((line("离开", 0.30, 0.45), line("购买！", 0.67, 0.45), line("10", 0.66, 0.66)), 10),
            ((line("離開", 0.30, 0.45), line("購買！", 0.67, 0.45), line("15", 0.66, 0.66)), 15),
            ((line("购 买 ！", 0.67, 0.45), line("离 开", 0.30, 0.45), line("1O", 0.66, 0.66)), 10),
            ((line("离开", 0.30, 0.45), line("购买！", 0.67, 0.45)), None),  # 价格读不出，仍是购买窗
            ((line("离开", 0.30, 0.45), line("购买！", 0.67, 0.45), line("5", 0.70, 0.22)), None),
        ):
            with self.subTest(lines=[ln.text for ln in lines]):
                self.assertEqual(self.read(*lines), source_cues.Purchase(want))

    def test_not_purchase(self):
        buttons = (line("离开", 0.30, 0.45), line("购买！", 0.67, 0.45), line("10", 0.66, 0.66))
        for word in ("转账", "轉帳", "转帐", "轉賬", "返回", "卡牌商店"):
            with self.subTest(word=word):
                self.assertIsNone(self.read(line(word, 0.50, 0.20), *buttons))
        self.assertIsNone(self.read(line("购买！", 0.67, 0.45), line("10", 0.66, 0.66)))  # 缺「离开」
        self.assertIsNone(self.read(line("离开", 0.30, 0.45), line("10", 0.66, 0.66)))  # 缺「购买」
        self.assertIsNone(self.read(line("转账", 0.42, 0.62), line("离开", 0.39, 0.62)))  # 星币转账窗
        # 森秋幻境的植物建筑商店：两个钮和筹码商店一样，价格 5 星币，低于筹码商店的起价
        self.assertIsNone(self.read(line("离开", 0.30, 0.45), line("购买！", 0.67, 0.45), line("5", 0.66, 0.66)))

    def test_grey_and_empty(self):
        with mock.patch.object(io, "ocr", side_effect=AssertionError("不该认字")):
            self.assertIsNone(source_cues.read_purchase(np.full((1080, 1920), 200, np.uint8)))
            self.assertIsNone(source_cues.read_purchase(np.zeros((0, 0, 3), np.uint8)))


@unittest.skipUnless(samples.samples_ok(*ALL_SAMPLES), samples.samples_reason(*ALL_SAMPLES))
class SampleTest(unittest.TestCase):
    """录像截图：横幅读名次、购买窗读价格，别的画面都不是；对的样本再换两种窗口大小、两种窗口比例各试一次。"""

    def test_banners(self):
        for name, want in BANNERS:
            img = load(name)
            for size in (None, (1280, 720), (2560, 1440)):
                with self.subTest(name=name, size=size):
                    frame = img if size is None else resized(img, size)
                    self.assertTrue(source_cues.glance(frame).banner)
                    self.assertFalse(source_cues.glance(frame).shop)
                    self.assertEqual(source_cues.read_rank(frame), want)
                    self.assertIsNone(source_cues.read_purchase(frame))

    def test_shops(self):
        for name, price in SHOPS:
            img = load(name)
            for size in (None, (1280, 720), (2560, 1440)):
                with self.subTest(name=name, size=size):
                    frame = img if size is None else resized(img, size)
                    self.assertTrue(source_cues.glance(frame).shop)
                    self.assertFalse(source_cues.glance(frame).banner)
                    self.assertEqual(source_cues.read_purchase(frame), source_cues.Purchase(price))
                    self.assertIsNone(source_cues.read_rank(frame))

    def test_wide_and_narrow_windows(self):
        # 补边模拟 21:9、4:3 的窗口：界面大小不变、摆在中间（三选一界面在这两种窗口上是这样摆的）。购买窗在这种画面上
        # 只核颜色预筛：两个钮的字、价格的位置按比例取，还没有宽屏、窄屏窗口的实机样本
        for name, want in BANNERS + SHOPS:
            img = load(name)
            h, w = img.shape[:2]
            for size in ((round(h * 64 / 27), h), (w, round(w * 3 / 4))):
                with self.subTest(name=name, size=size):
                    frame = pad(img, size)
                    seen = source_cues.glance(frame)
                    if (name, want) in BANNERS:
                        self.assertEqual((seen.banner, seen.shop), (True, False))
                        self.assertEqual(source_cues.read_rank(frame), want)
                    else:
                        self.assertEqual((seen.banner, seen.shop), (False, True))

    def test_not_cues(self):
        for name in NOT_CUES:
            with self.subTest(name=name):
                frame = load(name)
                self.assertFalse(source_cues.glance(frame).banner)
                self.assertFalse(source_cues.glance(frame).shop)
                self.assertIsNone(source_cues.read_rank(frame))
                self.assertIsNone(source_cues.read_purchase(frame))

    def test_negatives(self):
        for name in NEGATIVES:
            with self.subTest(name=name):
                frame = load(name)
                self.assertFalse(source_cues.glance(frame).banner)
                self.assertFalse(source_cues.glance(frame).shop)
                self.assertIsNone(source_cues.read_rank(frame))
                self.assertIsNone(source_cues.read_purchase(frame))


if __name__ == "__main__":
    unittest.main()
