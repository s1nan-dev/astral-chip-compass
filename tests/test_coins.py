"""星币数：玩家列表上自己那一行横条左边的数字（advisor/vision/coins.py）、Session 的星币数和下一次升星的花费
（Session.coins、on_coins、next_star_cost），以及识别流程里读星币数那一步（FrameProcessor._read_coins）。
头像认不出自己那一行时（「昏迷」等），识别流程在读星币的那一拍按本局记住的名字找那一行（stars.read_own_stars_by_name、
FrameProcessor._own_by_name），再读这一行的星数、星币数。

样本在 tests/data/coins/（从 samples/ 原样复制，「昏迷」的那张来自诊断包；标注在同一文件夹的 labels.json）：3840x2054、
3840x2160、2560x1369、21:9 的 2560x1057、4:3 的 2397x1798、缩到 1600 宽的预览图；读不到的三种：三选一压暗、选转账对象
（列表去掉了自己那一行）、加载画面；自己「昏迷」、头像认不出、按名字读的三张。自己的名字只写在标注文件里（name），
测试代码里不写，标注文件或样本不在时跳过。
还没有三位数的实机样本：三位数用两位数的样本把最后一位往右复制一位合成（three_digits）。
"""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.session import STAR_COSTS, Event, Session, Settings
from advisor.vision import coins, pipeline, stars
from advisor.vision.coins import CoinsReading, read_coins_at, read_own_coins
from advisor.vision.hud import Hud, Pregame, Progress
from advisor.vision.screens import RESULT, OutOfGame
from advisor.vision.stars import name_key
from tests.support import samples, sandbox

DATA = samples.COIN_SAMPLES_DIR
COIN_LABELS = "labels.json"
LABELS = json.loads((DATA / COIN_LABELS).read_text(encoding="utf-8"))["samples"] if (DATA / COIN_LABELS).exists() else []
MISSING_COINS = [COIN_LABELS] if not (DATA / COIN_LABELS).exists() else samples.missing(DATA, *(s["file"] for s in LABELS))
HAVE_SAMPLES = bool(LABELS) and not MISSING_COINS  # 标注里一张都没有时也跳过：逐张核对的循环一次都不走就不算通过
COINS_SKIP = samples.coins_reason(*MISSING_COINS)
OWN_TURN = "20261002_214219_hud_round8_own_turn_real.jpg"  # 芬妮在第 1 行，5 星币，轮到自己行动
AL = "20261003_131150_hud_round9_star2_hp1_dimmed_2560x1369.jpg"  # 阿尔在第 1 行，21 星币；第 3 行变灰
GARDEN = "20261002_193639_hud_round11_2397x1798.jpg"  # 照在第 1 行，11 星币（4:3）
PREVIEW = "20261002_213305_chips_panel_two_chips_1600x855.jpg"  # 芬妮在第 1 行，26 星币（1600 宽的预览图）
# 诊断包里自己「昏迷」时的画面：阿兰娜在第 1 行，头像认不出，按名字找到第 1 行，3 星、13 星币（第 4 行也是 13）
KNOCKED = "20261005_200116_hud_round8_knocked_out_3840x2160.png"
# 自己和第 2、4 行的玩家都在「昏迷」：芬妮在第 1 行，21 星币；第 2 行是单独一个「0」
KNOCKED_REAL = "20261002_213047_hud_round4_knocked_out_real.jpg"
# 标注文件里有这张画面和记住的名字（名字只写在标注文件里）
HAVE_NAMES = HAVE_SAMPLES and any(s["file"] == KNOCKED and s.get("name") for s in LABELS)


def label(name: str) -> dict:
    return next(s for s in LABELS if s["file"] == name)


def load(name: str, width: int | None = None) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(DATA / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    img, _ = pipeline.trim_borders(img)
    if width is not None and img.shape[1] != width:
        f = width / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


def layout_rows(img: np.ndarray) -> list:
    lay, _, _ = stars._layout(img)
    return [] if lay is None else lay.rows


def _digit_boxes(img: np.ndarray, bar) -> list:
    """这一行挑出的数字（x, y, w, h，R，相对横条模板左上角），从左到右。"""
    ax, ay, s = bar
    x0, y0, x1, y1 = coins.DIGIT_BOX
    crop = img[round(ay + y0 * s):round(ay + y1 * s), round(ax + x0 * s):round(ax + x1 * s)]
    crop = cv2.resize(crop, (x1 - x0, y1 - y0), interpolation=cv2.INTER_CUBIC if s < 1 else cv2.INTER_AREA)
    digits, _, _ = coins._white_digits(crop)
    return [(x + x0, y + y0, w, h) for x, y, w, h, _ in digits]


def three_digits(img: np.ndarray, bar, shift: float | None = None) -> np.ndarray:
    """把这一行星币数的最后一位（白色和黑色描边，底色不动）往右复制一位，合成多一位的数；shift 是挪多少 R，
    默认是这一位的宽度加上字距。"""
    ax, ay, s = bar
    x, _, w, _ = _digit_boxes(img, bar)[-1]
    if shift is None:
        shift = w + 5
    out = img.copy()
    X0, X1 = round(ax + (x - 6) * s), round(ax + (x + w + 6) * s)
    Y0, Y1 = round(ay + 8 * s), round(ay + 74 * s)
    patch = img[Y0:Y1, X0:X1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    glyph = ((hsv[..., 1] <= 90) & (hsv[..., 2] >= 150)) | (hsv[..., 2] <= 70)
    glyph = cv2.dilate(glyph.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    dx = round(shift * s)
    region = out[Y0:Y1, X0 + dx:X1 + dx]
    region[glyph] = patch[glyph]
    return out


def cover(img: np.ndarray, bar, which: int, part: str, share: float = 0.5) -> np.ndarray:
    """用一块深红色挡住这一行第 which 个数字的一部分（part：right / left / top / bottom，挡住 share 那么多）。"""
    ax, ay, s = bar
    x, y, w, h = _digit_boxes(img, bar)[which]
    if part == "right":
        box = (x + w * (1 - share), y - 3, x + w + 3, y + h + 3)
    elif part == "left":
        box = (x - 3, y - 3, x + w * share, y + h + 3)
    elif part == "top":
        box = (x - 3, y - 3, x + w + 3, y + h * share)
    else:
        box = (x - 3, y + h * (1 - share), x + w + 3, y + h + 3)
    out = img.copy()
    bx0, by0, bx1, by1 = box
    out[round(ay + by0 * s):round(ay + by1 * s), round(ax + bx0 * s):round(ax + bx1 * s)] = (40, 30, 160)
    return out


class _FreshCache(unittest.TestCase):
    """每条测试前清掉星数识别沿用的缓存（上次的界面缩放、皮肤），免得结果随测试顺序变（同 test_stars）。"""

    def setUp(self):
        stars.reset_cache()


# ---------------------------------------------------------------- 样本上的读数


@unittest.skipUnless(HAVE_SAMPLES, COINS_SKIP)
class SampleTest(_FreshCache):
    def test_own_coins(self):
        """按角色认出自己那一行（标注 how 为「名字」的几张头像认不出，按标注里记住的名字找），读出的星币数和标注一致；
        读不到的（三选一压暗、选转账对象、加载画面）为 None，并写明原因。"""
        for s in LABELS:
            with self.subTest(s["file"]):
                img = load(s["file"])
                own = stars.read_own_stars(img, s["hero"])
                if s.get("how") == "名字":
                    self.assertIsNone(own)  # 头像认不出
                    own = stars.read_own_stars_by_name(img, name_key(s["name"])).result
                got = read_own_coins(img, own)
                self.assertEqual(got.value, s["own"], got.reason)
                if s["own"] is None:
                    self.assertTrue(got.reason)
                else:
                    self.assertEqual((own.row, own.how), (s["own_row"], s.get("how", "头像")))
                    self.assertEqual(got.reason, "")

    def test_every_row(self):
        """列表上每一行（含队友）的星币数和标注一致：一位数、两位数，各种分辨率和窗口比例；变灰的行读不出（None），不猜。"""
        for s in LABELS:
            with self.subTest(s["file"]):
                img = load(s["file"])
                rows = layout_rows(img)
                for i, expected in enumerate(s["rows"]):
                    if expected is not None:
                        self.assertTrue(i < len(rows) and rows[i].visible, f"第 {i + 1} 行没找到")
                for i, row in enumerate(rows):
                    if not row.visible:
                        continue
                    expected = s["rows"][i] if i < len(s["rows"]) else None
                    got = read_coins_at(img, row.bar)
                    self.assertEqual(got.value, expected, f"第 {i + 1} 行：{got.reason} {got.text}")

    def test_transfer_screen_never_reads_teammate(self):
        """选转账对象的画面：列表去掉了自己那一行，其余三行都是队友（0、29、11），不能当成自己的星币数。"""
        img = load("20261002_214211_coin_transfer_real.jpg")
        self.assertIsNone(stars.read_own_stars(img, "芬妮"))
        got = read_own_coins(img, None)
        self.assertIsNone(got.value)
        self.assertIn("自己那一行", got.reason)

    def test_small_windows(self):
        """缩到小窗口（宽 1280、1024；最小支持 16:9 宽 1000）读出的和原图一样。"""
        for name in (OWN_TURN, AL, GARDEN):
            for width in (1280, 1024):
                with self.subTest(name, width=width):
                    stars.reset_cache()
                    img = load(name, width)
                    got = read_own_coins(img, stars.read_own_stars(img, label(name)["hero"]))
                    self.assertEqual(got.value, label(name)["own"], got.reason)

    def test_three_digits(self):
        """三位数（合成：两位数的最后一位往右复制一位）：读得出，不会少读一位。"""
        for name, row, value in ((AL, 1, 377), (GARDEN, 3, 266), (PREVIEW, 2, 366), (OWN_TURN, 2, 299)):
            with self.subTest(name, row=row):
                img = load(name)
                bar = layout_rows(img)[row].bar
                got = read_coins_at(three_digits(img, bar), bar)
                self.assertEqual(got.value, value, got.reason)

    def test_cut_or_covered_digits_not_misread(self):
        """数字被挡住一部分、或第三位伸出了裁剪框时宁可读不出，不能读成别的数（「36」挡住「6」的右半边曾读成「31」）。"""
        for name, row in ((PREVIEW, 2), (AL, 3), (OWN_TURN, 2), (GARDEN, 1)):
            img = load(name)
            bar = layout_rows(img)[row].bar
            value = read_coins_at(img, bar).value
            self.assertEqual(value, label(name)["rows"][row])
            cases = {f"第 {i + 1} 位挡住{part}": cover(img, bar, i, part) for i in range(len(str(value)))
                     for part in ("right", "left", "top", "bottom")}
            cases["复制的一位在裁剪框边上"] = three_digits(img, bar, shift=72)
            cases["复制的一位隔得太开"] = three_digits(img, bar, shift=60)
            for why, frame in cases.items():
                with self.subTest(name, why=why):
                    got = read_coins_at(frame, bar)
                    self.assertIn(got.value, (None, value), f"{got.text} {got.reason}")

    def test_partly_covered_digit_not_misread(self):
        """一位数字只被挡住一小截（左、右各 25%~40%，上、下各 15%~20%）时也不能读成别的数。容易读错的：「2」挡住右边三成
        读成「1」（「29」读成「19」、「26」读成「16」），「9」挡住下边两成读成「0」（「29」读成「20」），「6」挡住左边三四成
        读成「5」（「26」读成「25」）。靠数字之间的间隔和各位数字的高度差挡住。"""
        for name, row in ((OWN_TURN, 2), (GARDEN, 3), (PREVIEW, 0), (PREVIEW, 2)):
            img = load(name)
            bar = layout_rows(img)[row].bar
            value = label(name)["rows"][row]
            self.assertEqual(read_coins_at(img, bar).value, value)
            for i in range(len(str(value))):
                for part, shares in (("right", (0.25, 0.3, 0.35, 0.4)), ("left", (0.25, 0.3, 0.35, 0.4)),
                                     ("top", (0.15, 0.2)), ("bottom", (0.15, 0.2))):
                    for share in shares:
                        with self.subTest(name, row=row, digit=i + 1, part=part, share=share):
                            got = read_coins_at(cover(img, bar, i, part, share), bar)
                            self.assertIn(got.value, (None, value), f"{got.text} {got.reason}")

    def test_lone_zero_common_widths(self):
        """单独一个「0」：缩到 1920、1366 宽时原图那一块曾读成字母「O」，和白底黑字读出的「0」对不上，整个数读不出
        （自己的星币花到 0 时会一直显示上一次的数）。识别原文里的「O」当成「0」以后各个宽度都读得出。"""
        for name, row in ((OWN_TURN, 1), (KNOCKED_REAL, 1), ("20261002_214211_coin_transfer_real.jpg", 0)):
            for width in (None, 1920, 1366, 1280):
                with self.subTest(name, width=width):
                    stars.reset_cache()
                    img = load(name, width)
                    got = read_coins_at(img, layout_rows(img)[row].bar)
                    self.assertEqual(got.value, 0, f"{got.text} {got.reason}")

    def test_digit_like_characters(self):
        """识别原文里像数字的字符（字母「O」、全角数字）换成半角数字再比较、核对；换完照常核对每个数字的字宽。"""
        cases = (
            ("原图读成「O」", OWN_TURN, 1, [[("O", 0.52)], [("0", 0.99)]], 0),
            ("全角数字", AL, 0, [[("２１", 0.97)], [("２１", 0.98)]], 21),  # 按字宽核对时全角数字也要认得（不抛 KeyError）
            # 两个「1」都读成「O」：换成「00」后字宽对不上（「1」只有 17~22 R 宽），不读成 0
            ("「1」读成「O」", GARDEN, 0, [[("OO", 0.95)], [("OO", 0.95)]], None),
        )
        for why, name, row, outputs, expected in cases:
            with self.subTest(why):
                img = load(name)
                bar = layout_rows(img)[row].bar
                with mock.patch.object(coins, "recognize_only", side_effect=outputs):
                    got = read_coins_at(img, bar)
                self.assertEqual(got.value, expected, f"{got.text} {got.reason}")
                if expected is None:
                    self.assertIn("宽度", got.reason)

    def test_only_recognition_no_detection(self):
        """只识别数字那一小块，不做文字检测：每次最多识别 3 次（一般 2 次），不调用整幅的文字识别。"""
        img = load(OWN_TURN)
        own = stars.read_own_stars(img, "芬妮")
        calls = []
        real = coins.recognize_only

        def counted(block):
            calls.append(block.shape)
            return real(block)

        with mock.patch.object(coins, "recognize_only", side_effect=counted), \
                mock.patch("advisor.vision.io.ocr", side_effect=AssertionError("不该做整块的文字识别")):
            got = read_own_coins(img, own)
        self.assertEqual(got.value, 5)
        self.assertLessEqual(len(calls), 3)
        self.assertTrue(all(h == coins.OCR_HEIGHT for h, _, _ in calls))


@unittest.skipUnless(HAVE_NAMES, COINS_SKIP if not HAVE_SAMPLES else "没有「昏迷」的样本截图或标注里没有记住的名字（tests/data/coins）")
class ByNameSampleTest(_FreshCache):
    """头像认不出自己那一行时按本局记住的名字找（stars.read_own_stars_by_name），名字从标注文件读。"""

    def test_knocked_out_reads_13(self):
        """诊断包里自己「昏迷」时的画面：头像认不出；按名字找到第 1 行（第 4 行也是 13 星币，所以要核对行号），3 星，
        读出 13 星币。缩到 1920、1280、1024 宽也一样。"""
        s = label(KNOCKED)
        for width in (None, 1920, 1280, 1024):
            with self.subTest(width=width):
                stars.reset_cache()
                img = load(KNOCKED, width)
                self.assertIsNone(stars.read_own_stars(img, s["hero"]))
                report = stars.read_own_stars_by_name(img, name_key(s["name"]))
                own = report.result
                self.assertIsNotNone(own, report.reason)
                self.assertEqual((own.row, own.stars, own.how), (0, 3, "名字"))
                self.assertGreaterEqual(own.confidence, coins.MIN_ROW_CONFIDENCE)
                got = read_own_coins(img, own)
                self.assertEqual(got.value, 13, got.reason)

    def test_name_row_matches_label(self):
        """每张样本按名字找到的行和标注一致；头像认得出的几张，按名字找到的就是头像认出的那一行、星数相同（没有认成队友）。
        选转账对象（列表只剩三行队友）、三选一的标题带挡住第一行时不按名字找。"""
        for s in LABELS:
            if "name" not in s:
                continue
            with self.subTest(s["file"]):
                img = load(s["file"])
                avatar = stars.read_own_stars(img, s["hero"])
                report = stars.read_own_stars_by_name(img, name_key(s["name"]))
                if s["name_row"] is None:
                    self.assertIsNone(report.result)
                    self.assertIn("只看得见 3 行", report.reason)
                    continue
                self.assertIsNotNone(report.result, report.reason)
                self.assertEqual(report.result.row, s["name_row"])
                if avatar is not None:
                    self.assertEqual((avatar.how, avatar.row, avatar.stars),
                                     ("头像", report.result.row, report.result.stars))

    def test_unknown_name_not_read(self):
        """记住的名字和画面上谁都对不上：不读（不退回「其余三行都是电脑、只有一行是真人」那一条）。"""
        report = stars.read_own_stars_by_name(load(KNOCKED), "完全对不上的名字")
        self.assertIsNone(report.result)
        self.assertIn("没有和记住的名字对得上", report.reason)


class DigitLikeTest(unittest.TestCase):
    """识别原文里像数字的字符换成半角数字（coins.DIGIT_LIKE），其余字符不动；标点照旧去掉。不用样本截图。"""

    def test_recognize_maps_digit_like(self):
        block = np.zeros((60, 40, 3), np.uint8) + 255
        for raw, expected in (("O", "0"), ("o", "0"), ("Ｏ", "0"), ("ｏ", "0"), ("〇", "0"), ("O.", "0"), ("1O", "10"),
                              ("２１", "21"), ("０", "0"), ("13", "13"), ("S", "S"), ("l7", "l7"), ("", "")):
            with self.subTest(raw=raw):
                with mock.patch.object(coins, "recognize_only", return_value=[(raw, 0.9)]):
                    self.assertEqual(coins._recognize(block), (expected, 0.9))
        with mock.patch.object(coins, "recognize_only", return_value=[]):
            self.assertEqual(coins._recognize(block), ("", 0.0))


class ReadOwnCoinsInputTest(unittest.TestCase):
    """read_own_coins 的输入不完整时（没认出自己那一行、识别流程测试里假的星数结果）不出错，返回原因。"""

    def own(self, **kw):
        row = SimpleNamespace(visible=True, bar=(400.0, 200.0, 0.5))
        base = dict(stars=1, confidence=0.9, row=0, how="头像", rows=[row])
        return SimpleNamespace(**{**base, **kw})

    def test_missing_pieces(self):
        img = np.zeros((1080, 1920, 3), np.uint8) + 90
        cases = {
            "没认出": None,
            "四行星数相同": self.own(row=None, how="全员相同"),
            "假的星数结果": SimpleNamespace(stars=1, confidence=0.9),
            "只有行号": SimpleNamespace(stars=1, confidence=0.9, row=3, how="头像"),
            "被挡住": self.own(rows=[SimpleNamespace(visible=False, bar=(400.0, 200.0, 0.5))]),
            "没有横条位置": self.own(rows=[SimpleNamespace(visible=True, bar=None)]),
            "把握不够": self.own(confidence=0.4),
            "画面上没有数字": self.own(),
        }
        for why, own in cases.items():
            with self.subTest(why):
                got = read_own_coins(img, own)
                self.assertIsNone(got.value)
                self.assertTrue(got.reason)

    def test_box_outside_frame(self):
        img = np.zeros((300, 300, 3), np.uint8)
        for bar in ((20.0, 100.0, 1.0), (280.0, 100.0, 1.0), (150.0, 260.0, 1.0), (150.0, 100.0, 0.0)):
            with self.subTest(bar=bar):
                got = read_coins_at(img, bar)
                self.assertIsNone(got.value)
                self.assertTrue(got.reason)


# ---------------------------------------------------------------- Session


def ready(db) -> Session:
    s = Session(db, Settings(hero="芬妮"))
    s.on_hero(SimpleNamespace(hero="芬妮"))
    return s


class SessionCoinsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        self.s = ready(self.db)

    def feed(self, *values):
        return [self.s.on_coins(v) for v in values]

    def test_two_same_readings_needed(self):
        s = self.s
        self.assertIsNone(s.game.coins)
        self.assertIsNone(s.on_coins(12))  # 只读到一次：先不采用
        self.assertIsNone(s.game.coins)
        ev = s.on_coins(12)
        self.assertEqual((ev.kind, s.game.coins), ("status", 12))
        self.assertIsNone(s.on_coins(12))  # 没变：不再发 status
        self.assertEqual(self.feed(17, 20, 17), [None, None, None])  # 不是连续两次相同：不改
        self.assertEqual(s.game.coins, 12)
        self.assertEqual(s.on_coins(17).kind, "status")
        self.assertEqual(s.game.coins, 17)

    def test_unreadable_keeps_value(self):
        """读不到（None）时不改、不清零，也不打断「连续两次」。"""
        s = self.s
        self.feed(30, 30)
        self.assertEqual(self.feed(None, None), [None, None])
        self.assertEqual(s.game.coins, 30)
        self.feed(5, None)
        self.assertEqual(s.game.coins, 30)
        self.assertEqual(s.on_coins(5).kind, "status")
        self.assertEqual(s.game.coins, 5)

    def test_bad_values_ignored(self):
        s = self.s
        self.feed(8, 8)
        self.assertEqual(self.feed(-1, -1, True, True, "8", 2.0, 2.0), [None] * 7)
        self.assertEqual(s.game.coins, 8)

    def test_new_game_clears(self):
        s = self.s
        self.feed(40, 40, 41)  # 41 还在等第二次
        s.new_game()
        self.assertIsNone(s.game.coins)
        self.assertIsNone(s.on_coins(41))  # 等的那一次也清掉了：新一局要重新连读两次
        self.assertIsNone(s.game.coins)
        s.on_coins(41)
        self.assertEqual(s.game.coins, 41)

    def test_auto_new_game_and_undo(self):
        """自动判定的新一局清空星币数；撤销后恢复。"""
        s = self.s
        s.on_hud(SimpleNamespace(round=3, map=None))
        s.on_hud(SimpleNamespace(round=3, map=None))
        self.feed(25, 25)
        ev = s.on_select_screen()
        self.assertEqual((ev.kind, ev.undo), ("newgame", True))
        self.assertIsNone(s.game.coins)
        self.assertTrue(s.undo_new_game())
        self.assertEqual(s.game.coins, 25)

    def test_game_over_ignores_readings(self):
        s = self.s
        s.on_hud(SimpleNamespace(round=12, map=None))
        self.feed(60, 60)
        s.on_out_of_game(OutOfGame(RESULT, "噩梦", "异变图书馆", 12))
        self.assertTrue(s.game.game_over)
        self.feed(3, 3)
        self.assertEqual(s.game.coins, 60)  # 这一局的保留到新的一局开始

    def test_not_saved(self):
        """对局状态不存盘：state.json 里没有星币数。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            path = Path(tmp) / "state.json"
            s = Session(self.db, Settings(hero="芬妮"), path)
            s.on_coins(50)
            s.on_coins(50)
            s.save()
            self.assertEqual(s.game.coins, 50)
            self.assertNotIn("coins", json.loads(path.read_text(encoding="utf-8")))
            self.assertIsNone(Session(self.db, Settings.load(path), path).game.coins)

    def test_recorded(self):
        """识别记录：采用的星币数变了时 status 记录写明（changed）；诊断包的对局状态里有星币数。"""
        s = self.s
        s.note([Event("status")])
        self.assertIsNone(s.on_coins(9))
        s.note([s.on_coins(9)])
        status = [r for r in s.recent_events(60) if r["kind"] == "status"]
        self.assertEqual(status[-1]["changed"], {"coins": [None, 9]})
        self.assertEqual(s.snapshot()["coins"], 9)


class NextStarCostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_costs(self):
        self.assertEqual(STAR_COSTS, (15, 20, 25))
        s = ready(self.db)
        self.assertIsNone(s.next_star_cost())  # 星数待识别
        for star, cost in ((0, 15), (1, 20), (2, 25), (3, None)):
            with self.subTest(star=star):
                s.on_stars(SimpleNamespace(stars=star, confidence=0.9))
                self.assertEqual((s.settings.star, s.next_star_cost()), (star, cost))

    def test_unknown_after_new_game(self):
        s = ready(self.db)
        s.on_stars(SimpleNamespace(stars=1, confidence=0.9))
        self.assertEqual(s.next_star_cost(), 20)
        s.new_game()
        self.assertIsNone(s.next_star_cost())

    def test_default_star_not_trusted(self):
        """启动参数里给的星数（star_read 为 False）不算读到：待识别。"""
        s = Session(self.db, Settings(hero="芬妮", star=2))
        self.assertIsNone(s.next_star_cost())


# ---------------------------------------------------------------- 识别流程里读星币那一步


class PipelineCoinsTest(unittest.TestCase):
    """FrameProcessor：对局画面上和读星数同一帧读星币数，隔 coins_ms；三选一期间不读；读不到时记原因；出错不影响其余识别。
    识别函数全部换成假的。每个时刻到期的读数都做完（settle：悬浮窗一帧只读一项，紧接着几帧接着读）。"""

    def setUp(self):
        self.db = load_db()
        self.seen = None  # 三选一的识别结果（None：不是三选一）
        self.round = 5
        self.own = SimpleNamespace(stars=1, confidence=0.9, row=0, how="头像")
        self.reading = CoinsReading(12, "", "12/12", 1.0, 2)
        self.coin_calls = []  # 每次读星币数时传进来的（画面, 星数结果）

        def fake_coins(img, own):
            self.coin_calls.append((img, own))
            if isinstance(self.reading, Exception):
                raise self.reading
            return self.reading

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero="芬妮")),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: self.own),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(pipeline, "read_own_coins", side_effect=fake_coins),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.img = np.zeros((1080, 1920, 3), np.uint8) + 60
        self.make()

    def make(self, coins_ms=None):
        """新建 Session 和 FrameProcessor。coins_ms：改写 rules.toml [watch] coins_ms。"""
        db = self.db
        if coins_ms is not None:
            db = dataclasses.replace(db, rules={**db.rules, "watch": {**db.rules.get("watch", {}), "coins_ms": coins_ms}})
        self.session = Session(db, Settings(hero="芬妮"))
        self.proc = pipeline.FrameProcessor(db, self.session)
        self.coin_calls.clear()

    def run_frames(self, times) -> list:
        events = []
        for t in times:
            events += self.proc.settle(self.img, float(t))
        return events

    def coin_records(self) -> list:
        return [r for r in self.session.recent_events(600) if r["kind"] == "read:coins"]

    def test_every_coins_ms_with_star_reading(self):
        """读星数按 hud_ms（3 秒）读，星币数隔 coins_ms（5 秒）：每隔一次读星数读一次（第 0、6、12 秒）；连续两次读到 12 后采用。"""
        self.assertEqual((self.proc.hud_s, self.proc.coins_s), (3.0, 5.0))
        events = self.run_frames(range(0, 13))
        self.assertEqual(len(self.coin_calls), 3)
        self.assertEqual(self.session.game.coins, 12)
        self.assertIn("status", [e.kind for e in events])
        self.assertTrue(all(own is self.own for _, own in self.coin_calls))  # 用同一帧读星数时认出的自己那一行
        self.assertEqual([(r["coins"], r["text"]) for r in self.coin_records()], [(12, "12/12")] * 3)

    def test_not_in_chip_select(self):
        """三选一期间不读星币数（三选一界面上读星数时也不读）；界面关了以后照常读。"""
        self.seen = SimpleNamespace(names=["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"],
                                    raw=["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"], rerolls=2,
                                    probabilities=(60, 37, 3), selected=None, problems=[])
        self.run_frames([x / 2 for x in range(0, 30)])  # 15 秒的三选一
        self.assertEqual(self.coin_calls, [])
        self.seen = None
        self.run_frames([15, 16, 17, 18])
        self.assertEqual(len(self.coin_calls), 1)

    def test_unreadable_reason_recorded(self):
        """读不到时不改已采用的数，原因写进识别记录（read:coins）。"""
        self.run_frames([0, 3, 6])
        self.assertEqual(self.session.game.coins, 12)
        self.reading = CoinsReading(None, "没有找到白色的数字（画面压暗或数字被挡住）")
        self.run_frames([9, 12])
        self.assertEqual(self.session.game.coins, 12)
        last = self.coin_records()[-1]
        self.assertEqual((last["coins"], last["reason"]), (None, "没有找到白色的数字（画面压暗或数字被挡住）"))

    def test_error_does_not_stop_frame(self):
        """读星币数出错：记下原因，这一帧其余的识别照常（轮次照样采用）。"""
        self.reading = RuntimeError("测试")
        events = self.run_frames([0])
        self.assertEqual(self.session.settings.round, 5)
        self.assertIn("status", [e.kind for e in events])
        self.assertIn("读星币数出错：RuntimeError: 测试", self.coin_records()[-1]["reason"])
        self.assertIsNone(self.session.game.coins)

    def test_coins_ms_setting(self):
        """[watch] coins_ms 可改：改成 1 秒时每次读星数都读。"""
        self.make(coins_ms=1000)
        self.run_frames([0, 3, 6])
        self.assertEqual(len(self.coin_calls), 3)

    def test_game_over_not_read(self):
        """本局已结束（结算画面）后不读星数，也不读星币数。"""
        self.run_frames([0, 3, 6])
        self.session.on_out_of_game(OutOfGame(RESULT, "噩梦", "异变图书馆", 12))
        calls = len(self.coin_calls)
        self.round = None
        self.run_frames([12, 18, 24])
        self.assertEqual(len(self.coin_calls), calls)
        self.assertEqual(self.session.game.coins, 12)

    def test_new_game_clears(self):
        self.run_frames([0, 3, 6])
        self.assertEqual(self.session.game.coins, 12)
        self.session.new_game()
        self.assertIsNone(self.session.game.coins)
        self.reading = CoinsReading(15, "", "15/15", 1.0, 2)
        self.run_frames([12])
        self.assertIsNone(self.session.game.coins)  # 新的一局只读到一次
        self.run_frames([18])
        self.assertEqual(self.session.game.coins, 15)


class PipelineByNameTest(unittest.TestCase):
    """FrameProcessor 按名字找自己那一行（_own_by_name）：头像认不出、读星币的那一拍（隔 coins_ms）、角色已认出、记住了名字
    才找；三选一期间不找；按名字找到的行和本局认出的行对不上时不读；出错不影响其余识别；按名字找到的行不记成本局认出的行；
    识别记录里不写名字。识别函数全部换成假的（按名字找的结果也是假的）。每个时刻到期的读数都做完（settle）。"""

    NAME = "测试玩家abc"

    def setUp(self):
        self.db = load_db()
        self.seen = None  # 三选一的识别结果（None：不是三选一）
        self.round = 5
        self.hero = "芬妮"  # 立绘认出的角色（None：认不出，角色待识别）
        self.avatar = None  # 按头像读星数的结果（None：头像认不出）
        self.row = stars.Row(y=300.0, visible=True, stars=2, bar=(400.0, 200.0, 0.5))
        self.report = self.found(0)  # 按名字找的结果（Exception：找的时候出错）
        self.name_calls = []  # 每次按名字找时传进来的名字
        self.coin_owns = []  # 每次读星币数时传进来的星数结果

        def fake_by_name(img, name):
            self.name_calls.append(name)
            if isinstance(self.report, Exception):
                raise self.report
            return self.report

        def fake_coins(img, own):  # 和 read_own_coins 一样：不知道自己是哪一行时读不到
            self.coin_owns.append(own)
            if own is None:
                return CoinsReading(None, "没有认出玩家列表上自己那一行")
            if own.row is None:
                return CoinsReading(None, "不知道自己是玩家列表的哪一行")
            return CoinsReading(13, "", "13/13", 1.0, 2)

        patches = [
            mock.patch.object(pipeline, "looks_like_overlay_screen", side_effect=lambda img: self.seen is not None),
            mock.patch.object(pipeline, "read_chip_select", side_effect=lambda img, db, **kw: self.seen),
            mock.patch.object(pipeline, "read_hud", side_effect=lambda img, db, full: Hud(round=self.round)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", side_effect=lambda img, db: SimpleNamespace(hero=self.hero)),
            mock.patch.object(pipeline, "read_own_stars", side_effect=lambda img, hero, **kw: self.avatar),
            mock.patch.object(pipeline, "read_own_stars_by_name", side_effect=fake_by_name),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "task_panel_visible", return_value=False),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch.object(pipeline, "read_own_coins", side_effect=fake_coins),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.img = np.zeros((1080, 1920, 3), np.uint8) + 60
        self.make()

    def found(self, row: int):
        """按名字找到了第 row 行（0 起，2 星）的结果。"""
        rows = [self.row] * 4
        return stars.StarsReport(stars.OwnStars(2, 0.8, row, "名字", rows), "", rows, 1.0)

    def not_found(self):
        """列表在、按名字找不到的结果。"""
        return stars.StarsReport(None, "玩家列表上没有和记住的名字对得上的行（名字被挡住、只读出一部分或读错等）",
                                 [self.row] * 4, 1.0)

    def make(self, name=NAME):
        """新建 Session 和 FrameProcessor；name 是本次运行记住的自己的名字（None：还没记住）。"""
        self.session = Session(self.db, Settings(hero="芬妮"))
        self.session.player_name = name
        self.proc = pipeline.FrameProcessor(self.db, self.session)
        self.name_calls.clear()
        self.coin_owns.clear()

    def run_frames(self, times) -> list:
        events = []
        for t in times:
            events += self.proc.settle(self.img, float(t))
        return events

    def records(self, kind: str) -> list:
        return [r for r in self.session.recent_events(600) if r["kind"] == kind]

    def test_only_on_coins_beat(self):
        """头像认不出：读星数按 hud_ms（3 秒），按名字找只在读星币的那一拍（第 0、6、12 秒）；找到的那一行照样读星数、
        星币数，星币数连续两次读到 13 后采用。识别记录里星数写明按名字认的，每次找的结果另记一条，都不写名字。"""
        events = self.run_frames(range(0, 13))
        self.assertEqual(self.name_calls, [self.NAME] * 3)
        self.assertEqual(self.session.game.coins, 13)
        self.assertEqual((self.session.settings.star, self.session.settings.star_read), (2, True))
        self.assertIn("status", [e.kind for e in events])
        self.assertEqual(len(self.coin_owns), 3)
        self.assertTrue(all(own is self.report.result for own in self.coin_owns))
        self.assertEqual([r["how"] for r in self.records("read:stars")], ["名字"] * 3)
        self.assertEqual([(r["row"], r["reason"]) for r in self.records("read:own_by_name")], [(0, "")] * 3)
        self.assertNotIn("测试玩家", json.dumps(self.session.recent_events(600), ensure_ascii=False))

    def test_all_same_stars_also_searched(self):
        """头像认不出、按「全员相同」读出了星数（不知道是哪一行，读不了星币数）：读星币的那一拍也按名字找，找到了读那一行；
        按名字找不到时星数照旧按「全员相同」采用，星币数读不到。"""
        self.avatar = stars.OwnStars(2, 0.8, None, "全员相同", [self.row] * 4)
        self.run_frames([0, 3, 6])
        self.assertEqual(len(self.name_calls), 2)
        self.assertEqual(self.session.game.coins, 13)
        self.assertEqual([r["how"] for r in self.records("read:stars")], ["名字", "全员相同", "名字"])
        self.make()
        self.report = self.not_found()
        self.run_frames([0, 3, 6])
        self.assertEqual([r["how"] for r in self.records("read:stars")], ["全员相同"] * 3)
        self.assertEqual((self.session.settings.star, self.session.game.coins), (2, None))
        self.assertTrue(all(own is self.avatar for own in self.coin_owns))

    def test_not_when_avatar_works(self):
        """头像认得出时照常按头像读：不按名字找，星币数用按头像认出的那一行，那一行照旧记成本局认出的行。"""
        self.avatar = stars.OwnStars(1, 0.9, 0, "头像", [self.row])
        self.run_frames(range(0, 13))
        self.assertEqual(self.name_calls, [])
        self.assertEqual(len(self.coin_owns), 3)
        self.assertTrue(all(own is self.avatar for own in self.coin_owns))
        self.assertEqual(self.records("read:own_by_name"), [])
        self.assertEqual(self.session.game.own_row, 0)

    def test_not_without_name(self):
        """还没记住自己的名字：不找，星币数照旧读不到（读不到的原因照旧记）。"""
        self.make(name=None)
        self.run_frames(range(0, 13))
        self.assertEqual(self.name_calls, [])
        self.assertIsNone(self.session.game.coins)
        self.assertEqual([r["reason"] for r in self.records("read:coins")], ["没有认出玩家列表上自己那一行"] * 3)

    def test_not_while_hero_pending(self):
        """角色还没认出来（头像根本没有比，不算「头像认不出」）：不按名字找。"""
        self.hero = None
        self.make()
        self.run_frames(range(0, 13))
        self.assertIsNone(self.session.stars_hero)
        self.assertEqual(self.name_calls, [])

    def test_not_in_chip_select(self):
        """三选一期间不按名字找（三选一界面上读星数只按头像，星币数照旧不读）；界面关了以后照常找。"""
        self.seen = SimpleNamespace(names=["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"],
                                    raw=["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"], rerolls=2,
                                    probabilities=(60, 37, 3), selected=None, problems=[])
        self.run_frames([x / 2 for x in range(0, 30)])  # 15 秒的三选一
        self.assertEqual((self.name_calls, self.coin_owns), ([], []))
        self.seen = None
        self.run_frames([15, 16, 17, 18])
        self.assertEqual(len(self.name_calls), 1)

    def test_known_row_must_match(self):
        """本局按头像认出过自己在第 3 行，按名字却找到第 1 行（例如自己那一行的名字有字读错、队友的名字里正好含着记住的
        名字）：对不上，不读星数、星币数，原因记进识别记录。找到的正是第 3 行时照常读。"""
        self.session.note_own(2)
        self.run_frames([0, 3, 6])
        self.assertEqual(len(self.name_calls), 2)
        self.assertIsNone(self.session.game.coins)
        self.assertFalse(self.session.settings.star_read)
        self.assertEqual(self.records("read:stars"), [])
        self.assertTrue(self.coin_owns and all(own is None for own in self.coin_owns))
        recs = self.records("read:own_by_name")
        self.assertEqual([r["row"] for r in recs], [None, None])
        self.assertEqual(recs[-1]["reason"], "按名字找到的是第 1 行，本局认出的是第 3 行，不读")
        self.report = self.found(2)
        self.run_frames([12, 18])
        self.assertEqual(self.session.game.coins, 13)
        self.assertEqual(self.session.game.own_row, 2)

    def test_name_row_not_remembered(self):
        """按名字找到的行不记成「本局认出的行」（Session.own_row 只认头像、「查看筹码」面板认出的）。"""
        self.run_frames([0, 6])
        self.assertEqual(self.session.game.coins, 13)
        self.assertIsNone(self.session.game.own_row)

    def test_keeps_last_value_until_two_reads(self):
        """倒地前按头像读到 20 并采用；倒地后头像认不出：按名字找不到时保持 20（不清零）；按名字连续两次读到 13 才改成 13。"""
        self.avatar = stars.OwnStars(2, 0.9, 0, "头像", [self.row])  # 和按名字找到的那一行一样是 2 星
        with mock.patch.object(pipeline, "read_own_coins",
                               side_effect=lambda img, own: CoinsReading(20, "", "20/20", 1.0, 2)):
            self.run_frames([0, 6])
        self.assertEqual(self.session.game.coins, 20)
        self.avatar = None
        self.report = self.not_found()
        self.run_frames([12, 18])
        self.assertEqual(self.session.game.coins, 20)
        self.report = self.found(0)
        self.run_frames([24])
        self.assertEqual(self.session.game.coins, 20)  # 只读到一次 13
        self.run_frames([30])
        self.assertEqual(self.session.game.coins, 13)

    def test_new_game_keeps_name(self):
        """新的一局：本局认出的行清空，记住的名字留着（同一次运行里是同一个玩家），角色认出后照样按名字找；
        星币数要重新连续读到两次。"""
        self.session.note_own(0)
        self.run_frames([0, 6])
        self.assertEqual(self.session.game.coins, 13)
        self.session.new_game()
        self.assertEqual((self.session.game.own_row, self.session.game.coins, self.session.player_name), (None, None, self.NAME))
        calls = len(self.name_calls)
        self.run_frames([12])
        self.assertEqual(len(self.name_calls), calls + 1)
        self.assertIsNone(self.session.game.coins)
        self.run_frames([18])
        self.assertEqual(self.session.game.coins, 13)

    def test_error_does_not_stop_frame(self):
        """按名字找出错：记下原因，这一帧其余的识别照常（轮次照样采用），星币数读不到。"""
        self.report = RuntimeError("测试")
        events = self.run_frames([0])
        self.assertEqual(self.session.settings.round, 5)
        self.assertIn("status", [e.kind for e in events])
        rec = self.records("read:own_by_name")[-1]
        self.assertEqual((rec["row"], rec["reason"]), (None, "按名字找自己那一行出错：RuntimeError: 测试"))
        self.assertEqual(self.coin_owns, [None])
        self.assertIsNone(self.session.game.coins)

    def test_not_found_reason_recorded(self):
        """列表在、按名字找不到（两行都对得上等）：原因记进识别记录，星数、星币数不读。"""
        reason = "第 1、3 行的名字都和记住的名字对得上，不知道哪一行是自己"
        self.report = stars.StarsReport(None, reason, [self.row] * 4, 1.0)
        self.run_frames([0, 6])
        self.assertEqual([(r["row"], r["reason"]) for r in self.records("read:own_by_name")], [(None, reason)] * 2)
        self.assertIsNone(self.session.game.coins)
        self.assertFalse(self.session.settings.star_read)

    def test_no_record_without_list(self):
        """画面上没有玩家列表（战斗画面等）：找不到，也不记按名字找的那一条（读星币数那一条照旧记原因）。"""
        self.report = stars.StarsReport(None, "画面上没有玩家列表", [], None)
        self.run_frames([0, 6])
        self.assertEqual(len(self.name_calls), 2)
        self.assertEqual(self.records("read:own_by_name"), [])
        self.assertEqual(len(self.records("read:coins")), 2)

    def test_game_over_not_searched(self):
        """本局已结束（结算画面）后不读星数，也不按名字找。"""
        self.run_frames([0, 6])
        self.session.on_out_of_game(OutOfGame(RESULT, "噩梦", "异变图书馆", 12))
        calls = len(self.name_calls)
        self.round = None
        self.run_frames([12, 18, 24])
        self.assertEqual(len(self.name_calls), calls)
        self.assertEqual(self.session.game.coins, 13)


@unittest.skipUnless(HAVE_SAMPLES, COINS_SKIP)
class RealFrameFlowTest(_FreshCache):
    """真实的画面经 FrameProcessor 读星数、读星币数（轮次、角色等换成假的）：阿尔 2 星，两次读到 21 后 Session.coins 为 21，
    下一次升星要 25 星币。自己「昏迷」的画面：头像认不出，在读星币的那一拍按记住的名字（从标注文件读）找到第 1 行，
    读出 3 星、13 星币。每个时刻到期的读数都做完（settle）。"""

    def flow(self, hero: str, round_: int, name: str | None = None):
        """（Session, FrameProcessor）。name：本次运行记住的自己的名字。"""
        db = load_db()
        session = Session(db, Settings(hero=hero))
        session.player_name = name
        patches = [
            mock.patch.object(pipeline, "read_chip_select", return_value=None),
            mock.patch.object(pipeline, "read_hud", return_value=Hud(round=round_)),
            mock.patch.object(pipeline, "read_progress", return_value=Progress()),
            mock.patch.object(pipeline, "is_select_screen", return_value=False),
            mock.patch.object(pipeline, "read_hero", return_value=SimpleNamespace(hero=hero)),
            mock.patch.object(pipeline, "read_players", return_value=None),
            mock.patch.object(pipeline, "read_chip_panel", None),
            mock.patch.object(pipeline, "read_pregame", return_value=Pregame()),
            mock.patch.object(pipeline, "mutator_catalog", return_value=()),
            mock.patch("advisor.vision.capture.exclusive_fullscreen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return session, pipeline.FrameProcessor(db, session)

    def frame(self, name: str) -> np.ndarray:
        return cv2.imdecode(np.fromfile(str(DATA / name), dtype=np.uint8), cv2.IMREAD_COLOR)

    def test_real_frame(self):
        session, proc = self.flow("阿尔", 9)
        img = self.frame(AL)
        proc.settle(img, 0.0)
        self.assertIsNone(session.game.coins)
        events = proc.settle(img, 6.0)
        self.assertEqual(session.game.coins, 21)
        self.assertIn("status", [e.kind for e in events])
        self.assertEqual((session.settings.star, session.next_star_cost()), (2, 25))
        rec = [r for r in session.recent_events(60) if r["kind"] == "read:coins"]
        self.assertEqual([r["coins"] for r in rec], [21, 21])

    @unittest.skipUnless(HAVE_NAMES, "标注里没有记住的名字")
    def test_name_known_avatar_still_used(self):
        """记住了名字、头像认得出（阿尔）：照旧按头像读，不按名字找。"""
        session, proc = self.flow("阿尔", 9, name=name_key(label(AL)["name"]))
        img = self.frame(AL)
        with mock.patch.object(pipeline, "read_own_stars_by_name", side_effect=AssertionError("头像认得出时不该按名字找")):
            proc.settle(img, 0.0)
            proc.settle(img, 6.0)
        self.assertEqual(session.game.coins, 21)
        self.assertEqual([r["how"] for r in session.recent_events(60) if r["kind"] == "read:stars"], ["头像", "头像"])
        self.assertEqual(session.game.own_row, 0)

    @unittest.skipUnless(HAVE_NAMES, "标注里没有记住的名字")
    def test_knocked_out_by_name(self):
        """诊断包里自己「昏迷」时的画面：头像认不出，读星币的那一拍（第 0、6 秒）按名字找到第 1 行，读出 3 星；两次读到 13 后
        Session.coins 为 13。按名字找到的行不记成本局认出的行；识别记录里不写名字。"""
        s = label(KNOCKED)
        session, proc = self.flow(s["hero"], 8, name=name_key(s["name"]))
        img = self.frame(KNOCKED)
        for t in (0.0, 3.0, 6.0):
            proc.settle(img, t)
        self.assertEqual(session.game.coins, 13)
        self.assertEqual((session.settings.star, session.settings.star_read), (3, True))
        recs = session.recent_events(60)
        self.assertEqual([r["how"] for r in recs if r["kind"] == "read:stars"], ["名字", "名字"])
        self.assertEqual([r["row"] for r in recs if r["kind"] == "read:own_by_name"], [0, 0])
        self.assertEqual([r["coins"] for r in recs if r["kind"] == "read:coins"], [13, 13])
        self.assertIsNone(session.game.own_row)
        self.assertNotIn(name_key(s["name"]), json.dumps(recs, ensure_ascii=False).lower())

    @unittest.skipUnless(HAVE_NAMES, "标注里没有记住的名字")
    def test_knocked_out_known_row_differs(self):
        """同一张画面，本局认出的行却是第 4 行（第 4 行也是 13 星币）：按名字找到的第 1 行对不上，不读。"""
        s = label(KNOCKED)
        session, proc = self.flow(s["hero"], 8, name=name_key(s["name"]))
        session.note_own(3)
        img = self.frame(KNOCKED)
        for t in (0.0, 3.0, 6.0):
            proc.settle(img, t)
        self.assertIsNone(session.game.coins)
        self.assertFalse(session.settings.star_read)
        rec = [r for r in session.recent_events(60) if r["kind"] == "read:own_by_name"]
        self.assertEqual([r["reason"] for r in rec], ["按名字找到的是第 1 行，本局认出的是第 4 行，不读"] * 2)

    def test_knocked_out_teammate_contains_name(self):
        """同一张画面，本局还没认出自己在第几行；自己那一行（第 1 行）的名字只读出一部分，第 4 行队友的名字里正好含着
        整个记住的名字（各行的名字换成假的）：第 1 行可能才是自己，不读，不能读成第 4 行队友的 0 星、13 星币。"""
        key = "测试玩家abc"
        names = ["测试玩家", "【Bot2】", "【Bot3】", "测试玩家abc2"]

        def fake(frame, lay, players):
            for p in players:
                p.name = names[p.row]

        session, proc = self.flow(label(KNOCKED)["hero"], 8, name=key)
        img = self.frame(KNOCKED)
        with mock.patch.object(stars, "_read_names", side_effect=fake):
            for t in (0.0, 3.0, 6.0):
                proc.settle(img, t)
        self.assertIsNone(session.game.coins)
        self.assertFalse(session.settings.star_read)
        recs = session.recent_events(60)
        rec = [r for r in recs if r["kind"] == "read:own_by_name"]
        self.assertEqual([r["row"] for r in rec], [None, None])
        self.assertIn("第 1 行的名字没读出来或只读出了记住的名字的一部分", rec[-1]["reason"])
        self.assertNotIn("测试玩家", json.dumps(recs, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
