"""玩家列表（自己的星数、队友）和左下角立绘（用户模板、斜着摆的皮肤）的回归测试：挑几张有代表性的样本。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.vision import stars as stars_module
from advisor.vision.frame import Frame
from advisor.vision.stars import name_key, own_row_by_name, read_own_stars, read_players
from tests.private_data import MISSING, OWN_NAME  # 截图上玩家自己的名字只写在本机的 tests/data/private.json 里
from tests.support import samples, sandbox

PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR
HUD = "20261002_012918_hud_round1.png"  # 凛，对局画面
TASKS = "20261002_012947_task_panel.png"  # 凛，任务栏开着
RIN_SELECT = "20261002_013522_chipselect_levelup_star1_badge_on.png"  # 凛，三选一（画面变暗）
HANNA = "20261002_015454_chipshop_prompt.png"  # 远野汉娜，自己在第一行
DREAM_DIM = "20261002_150206_chipselect_levelup_star3_2560x1057.jpg"  # 美咲在第 4 行，三选一压暗，头像相关度只有 0.53
GARDEN_SELECT = "20261002_191851_chipselect_levelup_star1_normal_2397x1798.jpg"  # 照在第 1 行，被标题带挡住
GARDEN_HUD = "20261002_193639_hud_round11_2397x1798.jpg"  # 照在第 1 行，对局画面
KNOCKED_OUT = "20261002_193857_chips_panel_knocked_out_2397x1798.jpg"  # 照处于「昏迷」，头像上叠着「昏迷」字样
TRANSFER = "20261002_214211_coin_transfer_real.jpg"  # 选转账对象：列表去掉了自己那一行，只剩三行队友
BATTLE = "20261002_021351_battle_scene.png"  # 战斗画面，没有玩家列表
# 斜着摆的皮肤：阿兰娜泳装在对局左下角是斜着摆的整张原画（约逆时针 17°）。样本是录像帧（3840×2160）里认角色看的左下角
# 那一块（tests/data/hero，不随仓库发布），贴回同样大小的画面左下角再认：界面缩放按画面大小算
ALANA_SWIMSUIT = "20261017_012808_f001000_alana_swimsuit_3840x2160.png"
ALANA_FRAME = (3840, 2160)
TILTED_PHOTO = "UT_Hero_RolePhoto_103_01.png"  # 阿兰娜泳装的角色照（data/photo_tilt.json 里转 17°）
# 阿兰娜泳装的健全版：从卡面现裁的角色照（data/card_crops.json），对局里同样斜着摆，photo_tilt.json 里转 17.1°
TILTED_SFW = "UT_Hero_RolePhoto_103_01_sfw.png"
# 派德曼「酷暑难耐」：BWIKI 上没有角色照（官方版本从备用地址下游戏内的图，复刻的版本从卡面现裁），对局里显示斜着约 42° 的
# 整张卡面，photo_tilt.json 里转 -42.3°
TILTED_PADMAN = "UT_Hero_RolePhoto_105_01.png"


def load(name: str, width: int | None = None) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    if width is not None and img.shape[1] > width:
        f = width / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


class _FreshCache(unittest.TestCase):
    """每条测试前清掉星数识别沿用的缓存（上次的界面缩放、皮肤）：别的测试读过不同大小的画面后，
    沿用的缩放会让相关度差一点点，贴着门槛的断言就随测试顺序时过时不过。"""

    def setUp(self):
        stars_module.reset_cache()


@unittest.skipUnless(samples.samples_ok(RIN_SELECT), samples.samples_reason(RIN_SELECT))
class OwnStarsTest(_FreshCache):
    def test_reads_own_row_in_chip_select(self):
        """三选一界面上，凛在第 4 行（没被标题带挡住）：1 星。缩到 1280 宽也一样。"""
        for width in (None, 1280):
            own = read_own_stars(load(RIN_SELECT, width), "凛")
            self.assertIsNotNone(own, width)
            self.assertEqual((own.stars, own.row, own.how), (1, 3, "头像"), width)
            self.assertGreaterEqual(own.confidence, 0.6)

    def test_blocked_row_gives_none(self):
        """阿尔在第 1 行，被三选一的标题带挡住：读不到，不猜。"""
        self.assertIsNone(read_own_stars(load("20261002_030000_chipselect_al_garden_badge.png"), "阿尔"))

    def test_no_player_list(self):
        """战斗画面没有玩家列表。"""
        self.assertIsNone(read_own_stars(load("20261002_021351_battle_scene.png", 1920), "远野汉娜"))


@unittest.skipUnless(samples.samples_ok(RIN_SELECT, DREAM_DIM, KNOCKED_OUT),
                     samples.samples_reason(RIN_SELECT, DREAM_DIM, KNOCKED_OUT))
class QuickPathTest(_FreshCache):
    """三选一上读星数只走快的那一路（read_own_stars 的 quick）：头像按平时的几种摇摆认得出时和全试的结果一样；认不出时不再按
    「轮到自己行动」的伸缩试（伸缩的那组模板一次都不取），这一帧当读不到；全试的照旧按伸缩再试。同一帧上两种分开记。"""

    def wobble_calls(self, read):
        """调 read()，返回（结果, 取脸部模板时的 wobble 参数，按先后）。"""
        real = stars_module._face_templates
        calls = []

        def spy(hero_id, f, wobble=False):
            calls.append(wobble)
            return real(hero_id, f, wobble=wobble)

        with mock.patch.object(stars_module, "_face_templates", side_effect=spy):
            got = read()
        return got, calls

    def test_same_as_full_when_recognized(self):
        for name, hero in ((RIN_SELECT, "凛"), (DREAM_DIM, "美咲")):
            with self.subTest(name):
                stars_module.reset_cache()
                full = read_own_stars(load(name), hero)
                stars_module.reset_cache()
                quick, calls = self.wobble_calls(lambda: read_own_stars(load(name), hero, quick=True))
                self.assertIsNotNone(quick)
                self.assertEqual((quick.stars, quick.row, quick.how, quick.confidence),
                                 (full.stars, full.row, full.how, full.confidence))
                self.assertNotIn(True, calls)

    def test_unrecognized_skips_wobble(self):
        img = load(KNOCKED_OUT)  # 「昏迷」字样盖住了头像
        got, calls = self.wobble_calls(lambda: read_own_stars(img, "照", quick=True))
        self.assertIsNone(got)
        self.assertTrue(calls)
        self.assertNotIn(True, calls)
        got, calls = self.wobble_calls(lambda: read_own_stars(img, "照"))
        self.assertIsNone(got)
        self.assertIn(True, calls)

    def test_cached_apart(self):
        frame = Frame(load(KNOCKED_OUT))
        quick = stars_module.diagnose(frame, "照", quick=True)
        full = stars_module.diagnose(frame, "照")
        self.assertIsNot(quick, full)
        self.assertIs(stars_module.diagnose(frame, "照", quick=True), quick)
        self.assertIs(stars_module.diagnose(frame, "照"), full)


@unittest.skipUnless(samples.samples_ok(DREAM_DIM, GARDEN_SELECT, GARDEN_HUD, KNOCKED_OUT),
                     samples.samples_reason(DREAM_DIM, GARDEN_SELECT, GARDEN_HUD, KNOCKED_OUT))
class OwnRowFallbackTest(_FreshCache):
    """头像认不出自己那一行时（「昏迷」等），按头像读不出星数（read_own_stars）；「查看筹码」面板按名字找自己那一行。
    读星数、星币时按本局记住的名字找那一行见 OwnStarsByNameTest。"""

    def test_dimmed_chip_select_reads_by_avatar(self):
        """三选一压暗时自己的头像相关度只有 0.53：仍按头像认出第 4 行，3 星，把握够会话采用（0.6 以上）。"""
        own = read_own_stars(load(DREAM_DIM), "美咲")
        self.assertEqual((own.stars, own.row, own.how), (3, 3, "头像"))
        self.assertGreaterEqual(own.confidence, 0.6)

    def test_knocked_out_gives_none(self):
        """「昏迷」字样盖住了头像：读不出，不猜（选转账对象等画面上玩家列表会整体上移，不能按记住的行读）。"""
        self.assertIsNone(read_own_stars(load(KNOCKED_OUT), "照"))
        self.assertIsNone(read_own_stars(load(GARDEN_SELECT), "照"))  # 自己那一行被三选一的标题带挡住

    def test_reads_own_row_by_avatar(self):
        own = read_own_stars(load(GARDEN_HUD), "照")
        self.assertEqual((own.stars, own.row, own.how), (3, 0, "头像"))

    def own_name(self) -> str:
        """这两局里截图上玩家自己的名字（name_key 处理前），只写在本机的 tests/data/private.json 里；
        没有这个文件时跳过用到它的这一步（排在各条测试的最后，其余断言照常跑）。"""
        if not OWN_NAME:
            self.skipTest(f"{MISSING}：截图上玩家自己的名字只写在这个文件里，按名字找自己那一行的这一步跳过")
        return OWN_NAME

    def test_own_row_by_name(self):
        img = load(KNOCKED_OUT)
        self.assertEqual(own_row_by_name(img), (0, "唯一的真人玩家"))  # 其余三行都是「【BotN】」
        self.assertEqual(own_row_by_name(img, "someoneelse"), (0, "唯一的真人玩家"))  # 记住的名字对不上时退回
        self.assertEqual(own_row_by_name(img, name_key(self.own_name())), (0, "自己的名字"))

    def test_own_row_by_name_needs_all_rows(self):
        """标题带挡住了第一行：看不全四行，「唯一的真人玩家」不下结论；记住的名字照样能找。"""
        img = load(DREAM_DIM)
        self.assertIsNone(own_row_by_name(img)[0])
        self.assertEqual(own_row_by_name(img, name_key(self.own_name())), (3, "自己的名字"))

    def test_name_key(self):
        """文字识别连带读进来的首尾数字（星币数、血量）、空白和标点不算名字的一部分。"""
        self.assertEqual(name_key("98玩家foobar"), "玩家foobar")
        self.assertEqual(name_key(" 11 Foo-Bar "), "foobar")
        self.assertEqual(name_key(""), "")


@unittest.skipUnless(samples.samples_ok(KNOCKED_OUT, GARDEN_SELECT, TRANSFER, BATTLE),
                     samples.samples_reason(KNOCKED_OUT, GARDEN_SELECT, TRANSFER, BATTLE))
class OwnStarsByNameTest(_FreshCache):
    """read_own_stars_by_name：头像认不出自己那一行时，按本局记住的名字找那一行再读星数。规矩：玩家列表四行都看得见，
    只有一行读到的名字里含着整个记住的名字才算；两行以上对得上、一行都对不上时不读；不用「其余三行都是电脑」那一条。

    各行读到的名字换成假的（不做文字识别，测试里不出现真实的名字）；画面用「昏迷」时的实机截图（照在第 1 行，3 星，
    其余三行各 2 星）。按真实名字读的样本见 tests/test_coins.py（名字从标注文件读）。"""

    KEY = "测试玩家abc"  # 记住的名字（7 个字）
    BOTS = ["【Bot9】16", ".17【Bot7】", "【Bot5】"]

    def find(self, names, own_name=KEY, img=None):
        """各行读到的名字换成 names（按行；None 表示这一行没读出名字），按 own_name 找。返回（结果, 做了几次名字的识别）。"""

        def fake(frame, lay, players):
            for p in players:
                if p.visible and p.row < len(names) and names[p.row] is not None:
                    p.name = names[p.row]

        frame = load(KNOCKED_OUT) if img is None else img
        with mock.patch.object(stars_module, "_read_names", side_effect=fake) as reads:
            report = stars_module.read_own_stars_by_name(frame, own_name)
        return report, reads.call_count

    def test_unique_name(self):
        """只有一行读到了完整的名字：读那一行（名字前后连带读进来的星币数、血量，以及空白、标点不影响）；
        自己在哪一行就读哪一行。"""
        report, reads = self.find(["36测试玩家abc", *self.BOTS])
        own = report.result
        self.assertEqual((own.row, own.stars, own.how, reads), (0, 3, "名字", 1))
        self.assertGreaterEqual(own.confidence, 0.6)  # Session 采用星数、coins 读星币数都要 0.6 以上
        self.assertLessEqual(own.confidence, stars_module.NAME_CONFIDENCE)
        self.assertEqual(len(own.rows), 4)
        self.assertEqual(report.reason, "")
        report, _ = self.find(["【Bot9】", "【Bot7】", "测试 玩家-abc 9", "【Bot5】"])
        self.assertEqual((report.result.row, report.result.stars), (2, 2))

    def test_name_shared_with_teammate(self):
        """两行以上对得上（和队友同名、队友的名字里含着记住的名字、四行都一样）：不知道哪一行是自己，不读。
        只差一个字的队友不算对上（名字要认全），读自己那一行。"""
        for names in (["测试玩家abc", "【Bot9】", "测试玩家abc", "【Bot5】"],
                      ["测试玩家abc", "【Bot9】", "测试玩家abc的朋友", "【Bot5】"],
                      [self.KEY] * 4):
            with self.subTest(names=names):
                report, _ = self.find(names)
                self.assertIsNone(report.result)
                self.assertIn("都和记住的名字对得上", report.reason)
        report, _ = self.find(["测试玩家abc", "【Bot9】", "测试玩家abc", "【Bot5】"])
        self.assertIn("第 1、3 行", report.reason)
        report, _ = self.find(["测试玩家abc", "测试玩家abd", "【Bot7】", "【Bot5】"])
        self.assertEqual(report.result.row, 0)

    def test_half_name(self):
        """只认出半个名字时同样按「只有一行对得上」处理。
        画面上只读出半个名字（或读错一个字）：名字没认全，这一行对不上；别的行也对不上时不读。只差一两个字的队友也
        不算对上：自己那一行的名字没读全、队友的名字和自己只差一个字时，不会把队友那一行当成唯一对上的一行。
        记住的名字只有半个：画面上完整的名字里含着它，算对上；队友的名字里也含着它时两行都对得上，不读。"""
        for shown in ("测试玩", "测试玩家", "家abc", "测试玩家ab", "试玩家abc", "测试玩家abd"):
            with self.subTest(shown=shown):
                report, _ = self.find([shown, *self.BOTS])
                self.assertIsNone(report.result)
                self.assertIn("没有和记住的名字对得上", report.reason)
        report, _ = self.find(["测试玩家", "测试玩家abd", "【Bot7】", "【Bot5】"])  # 自己的没读全，队友只差一个字
        self.assertIsNone(report.result)
        report, _ = self.find(["36测试玩家abc", *self.BOTS], own_name="测试玩")
        self.assertEqual(report.result.row, 0)
        report, _ = self.find(["36测试玩家abc", "测试玩具", "【Bot7】", "【Bot5】"], own_name="测试玩")
        self.assertIsNone(report.result)
        self.assertIn("第 1、2 行", report.reason)

    def test_own_name_unreadable(self):
        """自己那一行的名字没读出来、其余各行都对不上：不读。"""
        report, _ = self.find([None, *self.BOTS])
        self.assertIsNone(report.result)
        self.assertIn("没有和记住的名字对得上", report.reason)

    def test_teammate_contains_name_own_row_partly_read(self):
        """名字和队友撞上（队友的名字里含着整个记住的名字）、同时自己那一行的名字没读全：对上的只有队友那一行，
        但自己那一行（没读出来、只读出数字、只读出一部分）可能才是自己，分不出来，不读。
        本局还没认出自己在第几行时（Session.own_row 为 None），识别流程没有别的办法发现这一行是队友。"""
        for own_row_text in (None, "36", "测试玩", "测试玩家ab", "家abc"):
            for mate in ("测试玩家abc的朋友", "测试玩家abc2", "【测试玩家abc】"):
                with self.subTest(own=own_row_text, mate=mate):
                    report, _ = self.find([own_row_text, mate, "【Bot7】", "【Bot5】"])
                    self.assertIsNone(report.result)
                    self.assertIn("第 1 行的名字没读出来或只读出了记住的名字的一部分", report.reason)
        # 自己那一行读全了、队友的名字只是记住的名字的一部分：和上面的情形分不出来（读全的那一行也可能是队友），同样不读
        report, _ = self.find(["测试玩家abc", "玩家abc", "【Bot7】", "【Bot5】"])
        self.assertIsNone(report.result)
        self.assertIn("第 2 行", report.reason)

    def test_other_rows_clearly_not_own(self):
        """其余各行的名字读出来了、也不是记住的名字的一部分：电脑的名字读得不全（「【60】」「【B】」，只剩一个字）、
        真人队友的名字和记住的名字不沾边（两个字、一个字的名字也算）、队友的名字和记住的名字只差一个字（不是它的一部分）：
        照常读对上的那一行。"""
        for names in (["测试玩家abc", "【60】", "【B】", "b"],
                      ["测试玩家abc", "小明", "x", "【Bot5】16"],
                      ["测试玩家abc", "测试玩家abd", "【Bot7】", "别人"]):
            with self.subTest(names=names):
                report, _ = self.find(names)
                self.assertIsNotNone(report.result, report.reason)
                self.assertEqual(report.result.row, 0)

    def test_no_remembered_name(self):
        """没记住名字（或名字去掉标点、首尾数字后不到 2 个字）：不找，也不做文字识别。"""
        for own_name in (None, "", "a", "12", "【】"):
            with self.subTest(own_name=own_name):
                report, reads = self.find(["测试玩家abc", *self.BOTS], own_name=own_name)
                self.assertIsNone(report.result)
                self.assertEqual((report.reason, reads), ("没有记住自己的名字", 0))

    def test_needs_all_four_rows(self):
        """玩家列表看不全四行时不按名字找（也不做文字识别）：选转账对象的画面去掉了自己那一行、只剩三行队友，即使队友的
        名字碰巧和记住的名字对得上也不读；三选一的标题带挡住第一行时也不读。"""
        for name in (TRANSFER, GARDEN_SELECT):
            with self.subTest(name):
                report, reads = self.find([self.KEY] * 4, img=load(name))
                self.assertIsNone(report.result)
                self.assertEqual(reads, 0)
                self.assertIn("只看得见 3 行", report.reason)

    def test_no_player_list(self):
        report, reads = self.find([self.KEY], img=load(BATTLE, 1920))
        self.assertIsNone(report.result)
        self.assertEqual((report.reason, reads), ("画面上没有玩家列表", 0))

    def test_unclear_stars_not_read(self):
        """按名字找到的那一行星星分不清亮没亮：不读。"""
        frame = Frame(load(KNOCKED_OUT))
        found = stars_module._layout(frame)
        found[0].rows[0].stars = None
        report, _ = self.find(["测试玩家abc", *self.BOTS], img=frame)
        self.assertIsNone(report.result)
        self.assertIn("分不清", report.reason)

    def test_reuses_layout_of_same_frame(self):
        """同一个画面（Frame）刚按头像读过（diagnose）时沿用那次找到的玩家列表，不再找一遍；换了 Frame 照常找。"""
        frame = Frame(load(KNOCKED_OUT))
        self.assertIsNone(stars_module.diagnose(frame, "照").result)  # 头像认不出
        real = stars_module._find_layout
        with mock.patch.object(stars_module, "_find_layout", side_effect=real) as layout:
            report, _ = self.find(["测试玩家abc", *self.BOTS], img=frame)
            self.assertEqual((report.result.row, layout.call_count), (0, 0))
            report, _ = self.find(["测试玩家abc", *self.BOTS], img=Frame(frame.image.copy()))
            self.assertEqual((report.result.row, layout.call_count), (0, 1))

    def test_stricter_than_own_row_by_name(self):
        """比「查看筹码」面板用的 own_row_by_name 严：记住的名字谁都对不上时不读，不退回「其余三行都是电脑、只有一行是
        真人」那一条；只差一个字（相似度约 0.86）也不算对上。own_row_by_name 的这两条照旧。"""
        for names, expected in ((["别人的名字", *self.BOTS], (0, "唯一的真人玩家")),
                                (["测试玩家abd", *self.BOTS], (0, "自己的名字"))):
            with self.subTest(names=names):
                report, _ = self.find(names)
                self.assertIsNone(report.result)

                def fake(frame, lay, players):
                    for p in players:
                        p.name = names[p.row]

                with mock.patch.object(stars_module, "_read_names", side_effect=fake):
                    self.assertEqual(own_row_by_name(load(KNOCKED_OUT), self.KEY), expected)


@unittest.skipUnless(samples.samples_ok(TASKS, HANNA), samples.samples_reason(TASKS, HANNA))
class TeammatesTest(unittest.TestCase):
    """玩家列表上其余各行的角色（头像比对）和是否电脑（名字里有「Bot」）。"""

    @classmethod
    def setUpClass(cls):
        cls.by_id = {h.id: h.name for h in load_db().heroes.values()}

    def names(self, players):
        return [self.by_id.get(p.hero_id) for p in players.rows]

    def test_rin_game(self):
        for width in (None, 1280):
            with self.subTest(width=width):
                got = read_players(load(TASKS, width), "凛")
                self.assertEqual(got.own_row, 3)
                self.assertEqual(self.names(got)[:3], ["阿兰娜", "派德曼", "恋"])
                self.assertEqual([p.bot for p in got.rows], [True, True, True, False])
                self.assertEqual([p.row for p in got.teammates], [0, 1, 2])
                self.assertTrue(got.rows[3].own)

    def test_hanna_game(self):
        got = read_players(load(HANNA), "远野汉娜")
        self.assertEqual(got.own_row, 0)
        self.assertEqual(self.names(got)[1:], ["恋", "帕帕拉", "璐璐"])
        self.assertEqual([p.bot for p in got.rows], [False, True, True, True])

    def test_no_names_mode(self):
        got = read_players(load(TASKS, 1280), "凛", names=False)
        self.assertEqual([p.bot for p in got.rows], [None] * 4)
        self.assertEqual(self.names(got)[0], "阿兰娜")

    def test_ui_scale_off_by_five_percent_keeps_rows(self):
        """界面比按窗口估计的大 5%（最下面一行的横条可能找不到）：不能往上补一行虚行、把各行整体错开。"""
        from advisor.vision import templates

        expected = ["阿兰娜", "派德曼", "恋", "凛"]
        for name in (TASKS, "20261002_013927_chips_panel_one_chip.png"):
            if not (SAMPLES / name).exists():
                continue
            base = load(name, 1920)
            h, w = base.shape[:2]
            big = cv2.resize(base, None, fx=1.05, fy=1.05, interpolation=cv2.INTER_CUBIC)[:h, :w]
            templates.assets._last_u = None
            got = read_players(big, "凛", names=False)
            templates.assets._last_u = None
            with self.subTest(name=name):
                self.assertIsNotNone(got)
                for p in got.rows:
                    who = self.by_id.get(p.hero_id)
                    self.assertTrue(who is None or who == expected[p.row], (p.row, who))
                self.assertTrue(got.rows[0].visible)  # 第一行是真实的一行，不是补出来的虚行

    def test_band_check(self):
        from advisor.vision.stars import _covered_by_band

        gray = np.full((100, 100), 200, np.uint8)
        gray[20:40] = 10  # 黑色的标题带
        self.assertTrue(_covered_by_band(gray, 10, 20, 50, 20))
        self.assertFalse(_covered_by_band(gray, 10, 60, 50, 20))
        self.assertTrue(_covered_by_band(gray, 10, -50, 50, 20))  # 整块在画面外


@unittest.skipUnless(samples.samples_ok(HUD), samples.samples_reason(HUD))
class UserTemplateTest(unittest.TestCase):
    """资料库里没有的立绘，用户手动指定角色后存成模板（assets/heroes/user/），之后一并比对。"""

    def test_saved_template_recognises_later_frames(self):
        from advisor.vision import hero

        db = load_db()
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            tmp = Path(tmp)
            (tmp / "wiki").mkdir()
            patches = [
                mock.patch.object(hero, "heroes_dir", return_value=tmp / "wiki"),  # 假装资料库里没有任何角色照
                mock.patch.object(hero, "user_dir", return_value=tmp / "user"),
            ]
            for p in patches:
                p.start()
            try:
                hero.reload_templates()
                self.assertIsNone(hero.read_hero(load(HUD), db))  # 没有模板
                path = hero.save_template(load(HUD), "凛")
                self.assertTrue(Path(path).exists())
                self.assertTrue(Path(path).name.startswith(f"{hero.USER_PREFIX}{db.heroes['凛'].id}_"))
                for name, width in ((RIN_SELECT, None), (TASKS, 1280)):  # 别的画面（变暗、缩小）也认得出
                    guess = hero.read_hero(load(name, width), db)
                    self.assertEqual(guess.hero, "凛", (name, guess))
                    self.assertTrue(guess.image.startswith("user/"))
                guess = hero.read_hero(load(HANNA), db)  # 别的角色：不能认成凛
                self.assertIsNone(guess.hero)
                for _ in range(hero.USER_KEEP + 1):
                    hero.save_template(load(HUD, 1920), "凛")
                self.assertEqual(len(list((tmp / "user").glob("*.png"))), hero.USER_KEEP)
                self.assertIsNone(hero.save_template(load(HUD), "不存在的角色"))
            finally:
                for p in patches:
                    p.stop()
                hero.reload_templates()


def _png(gray: np.ndarray) -> bytes:
    ok, data = cv2.imencode(".png", gray)
    assert ok
    return data.tobytes()


class ProfilePhotoNameTest(unittest.TestCase):
    """Q 版头像的文件名认角色编号（stars.profile_photo_hero）：编号后面只能跟 _<皮肤> 或 .png，编号前面补了 0 的不算；认某个
    角色的头像只读编号对得上的（_hero_photos），编号四位数的（账号头像一类）不读。生成识别图片清单用的是同一个判断
    （tests/test_build_tools.py 的 MakeManifestTest）。"""

    def test_names(self):
        from advisor.vision.stars import profile_photo_hero

        cases = {
            "UT_Hero_ProfilePhoto_105.png": 105, "UT_Hero_ProfilePhoto_105_01.png": 105,
            "UT_Hero_ProfilePhoto_105_Max.png": 105, "UT_Hero_ProfilePhoto_105_01_sfw.png": 105,
            "UT_Hero_ProfilePhoto_306.png": 306, "UT_Hero_ProfilePhoto_1050_01.png": 1050,
            "UT_Hero_ProfilePhoto_1001.png": 1001, "UT_Hero_ProfilePhoto_0105.png": None,
            "UT_Hero_ProfilePhoto_105x.png": None, "UT_Hero_ProfilePhoto_105_.png": None,
            "UT_Hero_ProfilePhoto_.png": None, "UT_Hero_ProfilePhoto_105.jpg": None,
            "UT_Hero_RolePhoto_105.png": None, "UT_Hero_ProfilePhoto_١٠٥.png": None,
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(profile_photo_hero(name), want)

    def test_hero_photos_reads_only_matching(self):
        """认 105 的头像：读 105、105_01，不读 1050_01、1051、0105。"""
        from advisor.vision import templates

        rng = np.random.default_rng(5)
        names = ("UT_Hero_ProfilePhoto_105.png", "UT_Hero_ProfilePhoto_105_01.png", "UT_Hero_ProfilePhoto_1050_01.png",
                 "UT_Hero_ProfilePhoto_1051.png", "UT_Hero_ProfilePhoto_0105.png")
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            folder = Path(tmp)
            for i, name in enumerate(names):
                img = np.dstack([rng.integers(0, 256, (40, 40, 3), dtype=np.uint8), np.full((40, 40), 255, np.uint8)])
                img[0, 0, 0] = i  # 每张分得出是哪一张
                (folder / name).write_bytes(_png(img))
            with mock.patch.object(stars_module, "photo_dirs", return_value=(folder,)):
                templates.assets._photos.clear()
                try:
                    got = stars_module._hero_photos(105)
                finally:
                    templates.assets._photos.clear()
        self.assertEqual(sorted(int(p[0, 0, 0]) for p in got), [0, 1])


def _face(photo: np.ndarray) -> np.ndarray:
    """角色照里脸部那一块（同 hero._load_templates 的取法）。"""
    from advisor.vision import hero

    h, w = photo.shape[:2]
    x0, y0, x1, y1 = hero.HERO_FACE
    return photo[round(y0 * h): round(y1 * h), round(x0 * w): round(x1 * w)]


class TiltedPhotoTest(unittest.TestCase):
    """斜着摆的皮肤（data/photo_tilt.json：角色照文件名 → 角度）：表的读法（读不出来、写错的项不转）；建模板时只把表里的
    角色照绕脸部区域的中心转过来再取脸部（正数逆时针），别的角色照、用户模板逐字节不变；录像帧上的阿兰娜泳装转了才认得出。"""

    def test_table(self):
        from advisor.vision import hero

        self.assertEqual(hero.tilts().get(TILTED_PHOTO), 17.0)  # 随程序的表
        self.assertEqual(hero.tilts().get(TILTED_SFW), 17.1)
        self.assertEqual(hero.tilts().get(TILTED_PADMAN), -42.3)
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp, \
                mock.patch.object(hero.paths, "data_dir", return_value=Path(tmp)):
            path = Path(tmp) / hero.TILT_FILE
            self.assertEqual(hero.tilts(), {})  # 没有这个文件：都照原样比对
            for text in ("{ 坏掉的", json.dumps({"photos": [17]}), "[]"):
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    self.assertEqual(hero.tilts(), {})
            photos = {"a.png": 17, "b.png": -45, "c.png": 12.5, "d.png": 60, "e.png": "17", "f.png": True, "g.png": None}
            path.write_text(json.dumps({"photos": photos}), encoding="utf-8")
            self.assertEqual(hero.tilts(), {"a.png": 17.0, "b.png": -45.0, "c.png": 12.5})  # 写错的项不转

    def test_turn_direction(self):
        """转的中心是脸部区域的中心，正数逆时针：脸部中心右边的一点转 90° 以后到了中心上方。"""
        from advisor.vision import hero

        photo = np.zeros((200, 200), np.uint8)
        x0, y0, x1, y1 = hero.HERO_FACE
        cx, cy = round((x0 + x1) / 2 * 200), round((y0 + y1) / 2 * 200)
        photo[cy - 2: cy + 3, cx + 38: cx + 43] = 255  # 中心右边 40 像素的一小块
        ys, xs = np.nonzero(hero.turn_photo(photo, 90) > 128)
        self.assertLessEqual(abs(xs.mean() - cx), 1)
        self.assertLessEqual(abs(ys.mean() - (cy - 40)), 1)  # 到了中心上方 40 像素
        self.assertTrue(np.array_equal(hero.turn_photo(photo, 0), photo))

    def test_only_listed_photos_turned(self):
        from advisor.vision import hero

        rng = np.random.default_rng(7)
        listed, other = "UT_Hero_RolePhoto_901_01.png", "UT_Hero_RolePhoto_902.png"
        photos = {name: rng.integers(0, 256, (240, 200), dtype=np.uint8) for name in (listed, other)}
        user_face = rng.integers(0, 256, (160, 230), dtype=np.uint8)
        user_name = f"{hero.USER_PREFIX}902_20261017_000000_000000.png"
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:
            wiki, user = Path(tmp) / "wiki", Path(tmp) / "user"
            wiki.mkdir()
            user.mkdir()
            for name, photo in photos.items():
                (wiki / name).write_bytes(_png(photo))
            (user / user_name).write_bytes(_png(user_face))
            with mock.patch.object(hero, "heroes_dir", return_value=wiki), \
                    mock.patch.object(hero, "user_dir", return_value=user), \
                    mock.patch.object(hero, "tilts", return_value={listed: 17.0, user_name: 17.0}):
                hero.reload_templates()
                try:
                    got = {t.name: t for t in hero._load_templates()}
                finally:
                    hero.reload_templates()
        self.assertTrue(np.array_equal(got[listed].face, _face(hero.turn_photo(photos[listed], 17.0))))
        self.assertFalse(np.array_equal(got[listed].face, _face(photos[listed])))
        self.assertEqual(got[listed].photo_width, 200)  # 转的时候大小不变，缩放照整张角色照的宽度算
        self.assertTrue(np.array_equal(got[other].face, _face(photos[other])))  # 没列出的：照原样
        self.assertTrue(np.array_equal(got[f"user/{user_name}"].face, user_face))  # 用户模板存的就是对局里的样子，不转

    def test_cropped_sfw_turned(self):
        """从卡面现裁的健全版（随程序的 photo_tilt.json 里转 17.1°）：建模板时照样先转过来再取脸部，大小不变，缩放照整张角色照
        的宽度算；原版那张照旧转 17°。"""
        from advisor.vision import hero

        for name in (TILTED_SFW, TILTED_PHOTO):
            if not (hero.heroes_dir() / name).is_file():
                self.skipTest(f"没有识别图片 assets\\heroes\\{name}（第一次运行拾星助手时下载）")
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp, \
                mock.patch.object(hero, "user_dir", return_value=Path(tmp) / "user"):
            hero.reload_templates()
            try:
                got = {t.name: t for t in hero._load_templates()}
            finally:
                hero.reload_templates()
        for name, angle in ((TILTED_SFW, 17.1), (TILTED_PHOTO, 17.0)):
            with self.subTest(name):
                photo = cv2.imdecode(np.fromfile(str(hero.heroes_dir() / name), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                self.assertTrue(np.array_equal(got[name].face, _face(hero.turn_photo(photo, angle))))
                self.assertEqual(got[name].photo_width, photo.shape[1])

    def test_cropped_padman_turned(self):
        """派德曼「酷暑难耐」（从卡面现裁，随程序的 photo_tilt.json 里转 -42.3°，转得比别的几套多）：建模板时照样先转过来再
        取脸部，大小不变，缩放照整张角色照的宽度算。"""
        from advisor.vision import hero

        path = hero.heroes_dir() / TILTED_PADMAN
        if not path.is_file():
            self.skipTest(f"没有识别图片 assets\\heroes\\{TILTED_PADMAN}（第一次运行拾星助手时下载）")
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp, \
                mock.patch.object(hero, "user_dir", return_value=Path(tmp) / "user"):
            hero.reload_templates()
            try:
                got = {t.name: t for t in hero._load_templates()}
            finally:
                hero.reload_templates()
        photo = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        face = got[TILTED_PADMAN].face
        self.assertTrue(np.array_equal(face, _face(hero.turn_photo(photo, -42.3))))
        self.assertFalse(np.array_equal(face, _face(photo)))
        self.assertEqual(got[TILTED_PADMAN].photo_width, photo.shape[1])
        self.assertEqual(face.shape, _face(photo).shape)

    @unittest.skipUnless(samples.hero_ok(ALANA_SWIMSUIT), samples.hero_reason(ALANA_SWIMSUIT))
    def test_tilted_skin_recognised(self):
        """录像帧上的阿兰娜泳装：角色照转过来以后认得出（相关度不低于 MIN_SCORE、比第二名高出 MIN_MARGIN 以上，配上的
        就是这一套的角色照：原版或健全版）；不转时认不出。只比 assets\\heroes 里的角色照，不用这台电脑上
        存过的用户模板。"""
        from advisor.vision import hero

        if not (hero.heroes_dir() / TILTED_PHOTO).is_file():
            self.skipTest(f"没有识别图片 assets\\heroes\\{TILTED_PHOTO}（第一次运行拾星助手时下载）")
        crop = cv2.imdecode(np.fromfile(str(samples.HERO_SAMPLES_DIR / ALANA_SWIMSUIT), dtype=np.uint8), cv2.IMREAD_COLOR)
        frame = np.zeros((ALANA_FRAME[1], ALANA_FRAME[0], 3), np.uint8)
        frame[-crop.shape[0]:, : crop.shape[1]] = crop
        db = load_db()
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp, \
                mock.patch.object(hero, "user_dir", return_value=Path(tmp) / "user"):
            try:
                hero.reload_templates()
                guess = hero.read_hero(frame, db)
                with mock.patch.object(hero, "tilts", return_value={}):
                    hero.reload_templates()
                    plain = hero.read_hero(frame, db)
            finally:
                hero.reload_templates()
        # 配上的是泳装这一套的角色照：原版，或从卡面现裁的健全版（脸部区域两版一样，健全版按照游戏内核对的裁法裁，配得更准：
        # 0.98 对 0.96）
        self.assertEqual(guess.hero, "阿兰娜", guess)
        self.assertIn(guess.image, (TILTED_PHOTO, TILTED_SFW), guess)
        self.assertGreaterEqual(guess.score, hero.MIN_SCORE, guess)
        self.assertGreaterEqual(guess.score - guess.runner_up, hero.MIN_MARGIN, guess)
        self.assertIsNone(plain.hero, plain)


if __name__ == "__main__":
    unittest.main()
