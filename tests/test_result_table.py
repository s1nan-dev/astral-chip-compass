"""结算数据表识别（advisor/vision/result_table.py）的回归测试。

读截图的 TableTest 需要 ACC_TABLE_SAMPLES_DIR 指向的截图和 answers.json（含玩家昵称，不随仓库发布）；没设时跳过。
RowCheckTest、BadgePieceTest、BadgeNumberTest、BadgeIconsTest 不读截图，总会跑。
名字只用来当场认自己：这里只核对「读出来没有、是不是电脑」，不比对、也不打印名字。
"""
import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.vision import io, result_table
from advisor.vision.result_table import BADGES, STATS, looks_like_table, read_table
from tests.support import samples


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(samples.TABLE_SAMPLES_DIR / name), dtype=np.uint8), cv2.IMREAD_COLOR)


TABLE_FRAMES = (
    "rt_20261105-010644_1732_0.png", "rt_20261105-010644_1734_5.png", "rt_20261105-010644_1737_0.png",
    "rt_20261106-235502_1389_0.png", "rt_20261106-235502_1389_8.png", "rt_20261106-235502_1390_4.png",
)
OTHER_FRAMES = (
    "rt_20261105-010644_1511.png", "rt_20261105-010644_1730_8.png", "rt_20261105-010644_1737_4.png",
    "rt_20261106-235502_1114.png", "rt_20261106-235502_1388_4.png", "rt_20261106-235502_1390_8.png",
)
FADE_FRAMES = ("rt_20261105-010644_1731_0.png", "rt_20261106-235502_1388_6.png")


class RowCheckTest(unittest.TestCase):
    """逐行核 BEST 和数，和「电脑的名字」的写法：不读截图，用手搭的表。"""

    @staticmethod
    def column(seat: int, stats: dict, best=()) -> result_table.TableColumn:
        return result_table.TableColumn(seat=seat, name=None, bot=None, muted=None, stats=stats,
                                        best=frozenset(best), coins_all=None, coins_transferred=None,
                                        chips=None, chips_unknown=None, badges=None)

    @classmethod
    def table(cls, row: list, marked: set, other=0) -> tuple:
        """四栏：row 是某一项四个数（None 是没读到），marked 是标了 BEST 的栏号（0～3），其余四项照常数 0、不标 BEST。"""
        columns = []
        for c in range(4):
            stats = {stat: other for stat in STATS}
            stats["kills"] = row[c]
            columns.append(cls.column(c + 1, stats, {"kills"} if c in marked else ()))
        return tuple(columns)

    def test_bot_name(self):
        """电脑：整串就是 Bot 加编号，括号可有可无、不分全角半角和大小写。整串对不上、名字里又找得到这种写法时是
        None（拿不准，不把电脑当真人）；都对不上才是 False。"""
        for name in ("【Bot9】", "[Bot 9]", "bot9", "BOT 12", "［ｂｏｔ９］", "【Bot 9】"):
            self.assertIs(result_table._is_bot(name), True, name)
        for name in ("【Bot9", "Robot1", "阿bot9", "bot9abc"):
            self.assertIsNone(result_table._is_bot(name), name)
        for name in ("普通名字", "Bot", "9bot"):
            self.assertIs(result_table._is_bot(name), False, name)
        self.assertIsNone(result_table._is_bot(""))

    def test_blank_wrong_rows_keeps_agreeing_row(self):
        """四个数都在、最多那几栏正好是 BEST：一行都不动。"""
        columns = self.table([1, 5, 3, 2], {1})
        self.assertEqual([c.stats["kills"] for c in result_table._blank_wrong_rows(columns)], [1, 5, 3, 2])

    def test_blank_wrong_rows_full_row_mismatch(self):
        """四个数都在、最多那几栏和 BEST 不一样：四栏这一项写空，BEST 也去掉，别的项不动。"""
        columns = result_table._blank_wrong_rows(self.table([1, 5, 3, 2], {2}))
        for column in columns:
            self.assertIsNone(column.stats["kills"])
            self.assertEqual(column.stats["heal"], 0)
            self.assertEqual(column.best, frozenset())

    def test_blank_wrong_rows_tie(self):
        """并列最大的几栏都要标：只标了一个就算对不上，两个都标才算对得上。"""
        columns = result_table._blank_wrong_rows(self.table([5, 5, 3, 2], {0}))
        self.assertTrue(all(c.stats["kills"] is None for c in columns))
        columns = self.table([5, 5, 3, 2], {0, 1})
        self.assertEqual([c.stats["kills"] for c in result_table._blank_wrong_rows(columns)], [5, 5, 3, 2])

    def test_blank_wrong_rows_missing_values(self):
        """有数没读到：标了 BEST 的栏比别的栏小、或者读到 0，算确定对不上；看不出矛盾的照旧。"""
        for row, marked in (([None, 5, 3, 2], {2}), ([0, None, 3, 2], {0})):
            columns = result_table._blank_wrong_rows(self.table(row, marked))
            self.assertTrue(all(c.stats["kills"] is None for c in columns), (row, marked))
        columns = self.table([None, 5, 3, 2], {1})
        self.assertEqual([c.stats["kills"] for c in result_table._blank_wrong_rows(columns)],
                         [None, 5, 3, 2])

    def test_blank_wrong_rows_unmarked_tie_with_missing(self):
        """有数没读到时，没标 BEST 的栏和标了的栏一样大也算确定对不上：并列最大的都要标。"""
        columns = result_table._blank_wrong_rows(self.table([5, 5, None, 2], {0}))
        self.assertTrue(all(c.stats["kills"] is None for c in columns))

    def test_blank_wrong_rows_nothing_marked(self):
        """一栏都没标、却有不为 0 的数：每一格都看过标没标（绿框都在）时算确定对不上。没读到数的那一格找不到绿框、
        或者不知道找没找到（不给网格）时照旧：BEST 可能就标在那一格上。全是 0 的本来就不标。"""
        cells = [[(0, 0, 1, 1)] * 4 for _ in STATS]
        got = result_table._blank_wrong_rows(self.table([None, 3, 0, 0], set()), cells)
        self.assertTrue(all(c.stats["kills"] is None for c in got))
        hidden = [list(line) for line in cells]
        hidden[STATS.index("kills")][0] = None
        for got in (result_table._blank_wrong_rows(self.table([None, 3, 0, 0], set()), hidden),
                    result_table._blank_wrong_rows(self.table([None, 3, 0, 0], set()))):
            self.assertEqual([c.stats["kills"] for c in got], [None, 3, 0, 0])
        got = result_table._blank_wrong_rows(self.table([None, 0, 0, 0], set()), cells)
        self.assertEqual([c.stats["kills"] for c in got], [None, 0, 0, 0])

    def test_row_wrong_is_exactly_the_contradictions(self):
        """_row_wrong 为真，当且仅当没读到的数怎么补、看不了的格子标没标 BEST 怎么定，都和 BEST 的规矩对不上：
        读到的数取 0～2，没读到的补 0～3（比读到的都大也试到），逐个试。"""
        def subsets(items):
            items = sorted(items)
            return [set(s) for k in range(len(items) + 1) for s in itertools.combinations(items, k)]

        for values in itertools.product((None, 0, 1, 2), repeat=4):
            unknown = [c for c, v in enumerate(values) if v is None]
            for unseen in subsets(unknown):
                seen = set(range(4)) - unseen
                for marked in subsets(seen):
                    possible = False
                    for fill in itertools.product(range(4), repeat=len(unknown)):
                        filled = list(values)
                        for c, v in zip(unknown, fill):
                            filled[c] = v
                        top = max(filled)
                        if {c for c in seen if filled[c] == top and top != 0} == marked:
                            possible = True
                            break
                    self.assertEqual(result_table._row_wrong(list(values), marked, unseen), not possible,
                                     (values, marked, unseen))


class BadgePieceTest(unittest.TestCase):
    """称号数字的裁图（_badge_piece）：手画的粉色方块当数字，不读截图。槽位中心 (200, 100)、h = 40，
    找数字的范围是 x 196～246、y 112～148。"""

    PINK = (180, 60, 255)  # BGR，色相约 161

    def piece(self, *blocks):
        image = np.full((300, 400, 3), 30, np.uint8)
        for x0, y0, x1, y1 in blocks:
            image[y0:y1, x0:x1] = self.PINK
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        return result_table._badge_piece(image, hsv, 200.0, 100.0, 40.0)

    def test_digits_inside(self):
        """两位数都在范围里：裁出两位数（加上四周的边）。"""
        got = self.piece((201, 118, 213, 140), (216, 118, 228, 140))
        self.assertIsNotNone(got)
        self.assertGreaterEqual(got.shape[1], 27)

    def test_icon_piece_on_left_edge_ignored(self):
        """贴着左边的粉色块是图标的一部分，不算进数字，数字照样裁得出。"""
        got = self.piece((190, 118, 199, 140), (201, 118, 213, 140), (216, 118, 228, 140))
        self.assertIsNotNone(got)

    def test_number_longer_than_range(self):
        """第四位伸出了范围右边：读出来的只是前三位，整个不收。"""
        self.assertIsNone(self.piece((201, 118, 213, 140), (215, 118, 227, 140), (229, 118, 241, 140),
                                     (243, 118, 255, 140)))

    def test_far_block_on_right_edge_ignored(self):
        """顶到右边、但离数字远（立绘上的粉色）：不影响数字。"""
        got = self.piece((201, 118, 213, 140), (216, 118, 228, 140), (242, 118, 256, 140))
        self.assertIsNotNone(got)


class BadgeNumberTest(unittest.TestCase):
    """称号的数：种类对得上同一栏某一项时拿那一项核；那一项没有数（写空或没读出来）时照核不了的种类那样，
    放大 3 倍、4 倍两次一样才收。不读截图，换掉比图和读数的函数。"""

    CELLS = [[(0, 0, 100, 100), None, None, None]]
    PIECE = np.zeros((8, 8), np.uint8)

    def read(self, kind, same=None, number=None, twice_value=None, stat="kills"):
        stats = [[None] * 4 for _ in STATS]
        coins_all = [None] * 4
        coins_transferred = [None] * 4
        if stat in STATS:
            stats[STATS.index(stat)][0] = same
        elif stat == "coins_all":
            coins_all[0] = same
        else:
            coins_transferred[0] = same
        with mock.patch.object(result_table, "_badge_kind", return_value=kind), \
                mock.patch.object(result_table, "_badge_piece", return_value=self.PIECE), \
                mock.patch.object(result_table, "_badge_number", return_value=number) as once, \
                mock.patch.object(result_table, "_badge_number_twice", return_value=twice_value) as twice:
            got = result_table._read_badges(np.zeros((4, 4, 3), np.uint8), np.zeros((4, 4, 3), np.uint8),
                                            self.CELLS, 10.0, stats, coins_all, coins_transferred, {})
        return got[0], once, twice

    def test_missing_stat_uses_two_scales(self):
        """那一项被写空：退回两个尺度的读法。"""
        badges, once, twice = self.read("破坏王", same=None, number=7, twice_value=3)
        self.assertEqual(badges, (("破坏王", 3),) * 3)
        once.assert_not_called()
        self.assertTrue(twice.called)

    def test_matching_stat_kept(self):
        badges, once, twice = self.read("破坏王", same=5, number=5, twice_value=3)
        self.assertEqual(badges, (("破坏王", 5),) * 3)
        once.assert_called()
        twice.assert_not_called()

    def test_mismatching_stat_dropped(self):
        badges, once, twice = self.read("破坏王", same=5, number=4, twice_value=4)
        self.assertEqual(badges, (("破坏王", None),) * 3)
        twice.assert_not_called()

    def test_unchecked_kind_uses_two_scales(self):
        badges, once, _twice = self.read("天选之星", number=7, twice_value=3)
        self.assertEqual(badges, (("天选之星", 3),) * 3)
        once.assert_not_called()

    def test_missing_coin_uses_two_scales(self):
        badges, once, _twice = self.read("大富翁", same=None, number=7, twice_value=12, stat="coins_all")
        self.assertEqual(badges, (("大富翁", 12),) * 3)
        once.assert_not_called()


class BadgeIconsTest(unittest.TestCase):
    """称号图标：14 个都读到才用，少一个就当作没有；没读全时不缓存，补齐以后同一次运行里再读就认得出。"""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="badge-icons-"))
        self.addCleanup(shutil.rmtree, str(self.folder), True)
        patcher = mock.patch.object(result_table, "download_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        for i, name in enumerate(result_table.ICON_FILES):
            img = np.zeros((32, 32, 4), np.uint8)
            img[..., :3] = 40 + i * 10
            img[..., 3] = 255
            self.assertTrue(cv2.imwrite(str(self.folder / name), img))
        self.reset()
        self.addCleanup(self.reset)

    @staticmethod
    def reset():
        result_table._BADGE_ICONS = None
        result_table._BADGE_TEMPLATES = None

    def last_icon(self) -> Path:
        return self.folder / result_table.ICON_FILES[-1]

    def test_missing_one_means_no_icons(self):
        """少一个图标：当作没有图标，种类都是 None（比图模板为空）。"""
        self.last_icon().unlink()
        self.reset()
        self.assertEqual(result_table._badge_icons(), {})
        templates = result_table._badge_templates()
        self.assertEqual(templates, {})
        self.assertIsNone(result_table._badge_kind(np.zeros((100, 200, 3), np.uint8), 60.0, 60.0, 10.0, templates))

    def test_incomplete_is_not_cached(self):
        """没读全时每次再读都会重新读文件（可能正在下载）；读全了才缓存。"""
        data = self.last_icon().read_bytes()
        self.last_icon().unlink()
        self.reset()
        real = result_table.io.imread
        calls = []

        def counting(path):
            calls.append(path)
            return real(path)

        with mock.patch.object(result_table.io, "imread", side_effect=counting):
            self.assertEqual(result_table._badge_templates(), {})
            first = len(calls)
            self.assertEqual(result_table._badge_templates(), {})
            self.assertEqual(len(calls), 2 * first)
            self.last_icon().write_bytes(data)
            self.assertEqual(len(result_table._badge_templates()), len(BADGES))
            calls.clear()
            self.assertEqual(len(result_table._badge_templates()), len(BADGES))
            self.assertEqual(calls, [])


@unittest.skipUnless(samples.table_ok("answers.json"), samples.table_reason("answers.json"))
class TableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        samples.require_table("answers.json", *TABLE_FRAMES, *OTHER_FRAMES, *FADE_FRAMES)
        cls.answers = json.loads((samples.TABLE_SAMPLES_DIR / "answers.json").read_text(encoding="utf-8"))
        cls.db = load_db()

    def read(self, name: str):
        return read_table(load(name), self.db)

    def table_truth(self, name: str):
        return self.answers["tables"][self.answers["frames"][name]["table"]]

    def test_looks_like_table(self):
        """数据表都报 True；对局棋盘、称号页、奖励页都报 False。过渡帧不校验（多报一次没关系）。"""
        for name in TABLE_FRAMES:
            with self.subTest(name=name):
                self.assertTrue(looks_like_table(load(name)))
        for name in OTHER_FRAMES:
            with self.subTest(name=name):
                self.assertFalse(looks_like_table(load(name)))

    def test_stats_and_best(self):
        """6 张表：20 个数、BEST、consistent 都和答案一致。"""
        for name in TABLE_FRAMES:
            truth = self.table_truth(name)
            with self.subTest(name=name):
                got = self.read(name)
                self.assertIsNotNone(got)
                self.assertTrue(got.consistent)
                for column, want in zip(got.columns, truth["columns"]):
                    self.assertEqual(column.stats, want["stats"])
                    self.assertEqual(set(column.best), set(want["best"]))

    def test_coins_and_muted(self):
        """星币：读出来的数不能错（描边的数字读不准，允许读不出）；「加好友」灰不灰和答案一致。"""
        for name in TABLE_FRAMES:
            truth = self.table_truth(name)
            got = self.read(name)
            with self.subTest(name=name):
                for column, want in zip(got.columns, truth["columns"]):
                    for value, expected in ((column.coins_all, want["coins_all"]),
                                            (column.coins_transferred, want["coins_transferred"])):
                        if value is not None:
                            self.assertEqual(value, expected)
                    self.assertEqual(column.muted, want["muted"])

    def test_bot(self):
        """电脑的名字写成【BotN】：第二局第 2～4 栏判为电脑，第 1 栏不是。"""
        name = "rt_20261106-235502_1389_0.png"
        got = self.read(name)
        self.assertEqual([c.bot for c in got.columns], [False, True, True, True])
        self.assertIsNotNone(got.columns[0].name)

    def test_chips(self):
        """筹码：认出的名字加认不出的格数等于答案的筹码个数；每栏的筹码格都读到了。"""
        for name in TABLE_FRAMES:
            truth = self.table_truth(name)
            got = self.read(name)
            with self.subTest(name=name):
                for column, want in zip(got.columns, truth["columns"]):
                    self.assertIsNotNone(column.chips)
                    self.assertEqual(len(column.chips) + column.chips_unknown, want["chips_count"])

    def test_badges(self):
        """称号：有多少个、是什么种类、数是多少。答案里还没认定种类的槽位只比「不是错的种类」，其余逐项比。"""
        icons = result_table._badge_icons()
        for name in TABLE_FRAMES:
            truth = self.table_truth(name)
            got = self.read(name)
            with self.subTest(name=name):
                for column, want in zip(got.columns, truth["columns"]):
                    self.assertIsNotNone(column.badges)
                    self.assertEqual(len(column.badges), len(want["badges"]))
                    for kind, _ in column.badges:
                        self.assertIn(kind, (None, *BADGES))
                    for (kind, value), expected in zip(column.badges, want["badges"]):
                        if value is not None:
                            self.assertEqual(value, expected["value"])
                        if icons and expected["kind"] is not None:
                            self.assertEqual(kind, expected["kind"])

    def test_badges_scaled(self):
        """同一个画面缩到 2560、1920、1600 宽：称号种类要么对、要么读不出，不能认成别的种类。"""
        icons = result_table._badge_icons()
        for name in ("rt_20261105-010644_1732_0.png", "rt_20261106-235502_1389_8.png"):
            truth = self.table_truth(name)
            img = load(name)
            for width in (2560, 1920, 1600):
                scaled = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])),
                                    interpolation=cv2.INTER_AREA)
                got = read_table(scaled, self.db)
                with self.subTest(name=name, width=width):
                    self.assertIsNotNone(got)
                    for column, want in zip(got.columns, truth["columns"]):
                        self.assertEqual(len(column.badges), len(want["badges"]))
                        for (kind, _), expected in zip(column.badges, want["badges"]):
                            if icons and expected["kind"] is not None and kind is not None:
                                self.assertEqual(kind, expected["kind"])

    def test_bottom_bar_from_lines(self):
        """调用方给了整幅文字识别时，底栏的地图、难度、轮数照结算页的读法取。"""
        for name in ("rt_20261105-010644_1732_0.png", "rt_20261106-235502_1389_0.png"):
            truth = self.table_truth(name)
            img = load(name)
            lines = io.ocr(img, (0, 0, img.shape[1], img.shape[0]), 1280)
            got = read_table(img, self.db, lines=lines)
            with self.subTest(name=name):
                self.assertEqual((got.map, got.difficulty, got.rounds),
                                 (truth["map"], truth["difficulty"], truth["rounds"]))

    def test_scaled(self):
        """同一个画面缩到 2560、1920 宽：数、BEST、consistent 不变（几何都按表头字高折算）。"""
        name = "rt_20261106-235502_1389_0.png"
        img = load(name)
        base = read_table(img, self.db)
        for width in (2560, 1920):
            scaled = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
            got = read_table(scaled, self.db)
            with self.subTest(width=width):
                self.assertIsNotNone(got)
                self.assertEqual([c.stats for c in got.columns], [c.stats for c in base.columns])
                self.assertEqual([set(c.best) for c in got.columns], [set(c.best) for c in base.columns])
                self.assertTrue(got.consistent)

    def test_looks_like_table_scaled(self):
        """很省的一眼在小窗口里照样认得出：绿框的尺寸下限按画面相对 4K 的比例缩（缩到 1080p 时绿框只有 4K 的一半大），
        别的画面照样不认。"""
        images = {name: load(name) for name in (*TABLE_FRAMES, *OTHER_FRAMES)}
        for width in (2560, 1920, 1600, 1280):
            for name, img in images.items():
                scaled = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
                with self.subTest(width=width, name=name):
                    self.assertEqual(looks_like_table(scaled), name in TABLE_FRAMES)

    def test_other_aspect_ratios(self):
        """比 16:9 更宽（32:9）、更高（4:3）的窗口：界面按宽、高里较小的比例缩，表照样找得到，读出的数不错（读不出的可以
        为 None）。"""
        name = "rt_20261106-235502_1389_0.png"
        img = load(name)
        truth = self.table_truth(name)
        for w, h in ((5120, 1440), (1440, 1080)):
            scale = min(w / img.shape[1], h / img.shape[0])
            small = cv2.resize(img, (round(img.shape[1] * scale), round(img.shape[0] * scale)),
                               interpolation=cv2.INTER_AREA)
            canvas = np.full((h, w, 3), 60, np.uint8)
            y0, x0 = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
            canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
            with self.subTest(size=(w, h)):
                self.assertTrue(looks_like_table(canvas))
                got = read_table(canvas, self.db)
                self.assertIsNotNone(got)
                for column, want in zip(got.columns, truth["columns"]):
                    for stat in STATS:
                        if column.stats[stat] is not None:
                            self.assertEqual(column.stats[stat], want["stats"][stat])

    def test_other_frames_none(self):
        """对局棋盘、称号页、奖励页都不是数据表。过渡帧读不出表头时为 None，读得出时 consistent 为假。"""
        for name in OTHER_FRAMES:
            with self.subTest(name=name):
                self.assertIsNone(self.read(name))
        for name in FADE_FRAMES:
            got = self.read(name)
            if got is not None:
                self.assertFalse(got.consistent)


if __name__ == "__main__":
    unittest.main()
