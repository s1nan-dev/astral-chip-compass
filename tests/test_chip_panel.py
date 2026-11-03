"""「查看筹码」面板识别（advisor/vision/chip_panel.py）的回归测试。合成的面板画面用 tools/panel_synth.py 画。

需要 samples/ 里的面板截图和 assets/chips/ 里的筹码图标（tools/fetch_chip_icons.py 下载）；缺了就跳过。
"""
import json
import unittest
from unittest import mock

import cv2
import numpy as np

from advisor.data import load_db
from advisor.paths import assets_dir
from advisor.session import Session, Settings
from advisor.vision import chip_panel, templates
from advisor.vision.chip_panel import PanelReading, read_chip_panel
from tests.support import samples
from tools import panel_synth

SAMPLES = samples.SAMPLES_DIR
EMPTY = "20261002_012956_chips_panel_empty.png"
ONE = "20261002_013927_chips_panel_one_chip.png"
TWO = "20261002_020436_chips_panel_two_chips.png"
THREE = "20261002_021435_chips_panel_three_chips.png"
KNOCKED_OUT = "20261002_193857_chips_panel_knocked_out_2397x1798.jpg"  # 照处于「昏迷」，头像上叠着「昏迷」字样
# 10-03 真人对局（阿尔）：点选过的格子外圈为黄绿色
TWO_SELECTED = "20261003_125650_chips_panel_two_chips_al_2560x1369.jpg"  # 点选了第 1 格
SIX_SELECTED = "20261003_131252_chips_panel_six_chips_real.jpg"  # 点选了第 6 格
EIGHT = "20261003_132016_chips_panel_eight_chips_2560x1369.jpg"  # 没有点选
EIGHT_SELECTED = "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg"  # 点选了第 8 格
SELECTED_RIM_HSV = (49, 194, 249)  # 实机量到的黄绿色外圈（OpenCV HSV）

# 10-15 更新把书签从蓝色改成紫色：这以前拍的面板截图上是蓝底，不再和现在的资料对得上（单独一条测试盯着）
STALE_SAMPLES = {ONE}


def load(name: str, width: int | None = None) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)
    if width is not None and img.shape[1] > width:
        f = width / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


def dim(img: np.ndarray, k: float) -> np.ndarray:
    """整幅画面的亮度乘 k（模拟筹码三选一界面打开时游戏把画面压暗）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hsv[..., 2] = (hsv[..., 2].astype(np.float32) * k).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


PANEL_LABELS = "panel_labels.json"
SAMPLE_SET = (EMPTY, ONE, TWO, THREE)
SELECTED_SET = (TWO_SELECTED, SIX_SELECTED, EIGHT, EIGHT_SELECTED)
HAVE_RAW = samples.samples_ok(*SAMPLE_SET)
HAVE_SAMPLES = samples.samples_ok(*SAMPLE_SET, PANEL_LABELS)
HAVE_SELECTED = samples.samples_ok(*SELECTED_SET)
HAVE_ICONS = (assets_dir("chips") / "UT_Relic_50081.png").exists()
ICON_SKIP = "没有下载筹码图标（tools/fetch_chip_icons.py 下载）"


def _skip_reason(*names: str) -> str:
    """缺样本或筹码图标时的跳过原因。"""
    return samples.samples_reason(*names) if not samples.samples_ok(*names) else ICON_SKIP


class FilesTest(unittest.TestCase):
    """首次运行要下载的文件清单。"""

    def test_icon_files_cover_every_chip(self):
        db = load_db()
        names = chip_panel.icon_files(db)
        self.assertEqual(names[:3], list(chip_panel.BASE_FILES))
        for chip in db.chips.values():
            self.assertIn(chip_panel.icon_file(chip.icon), names, chip.name)
        self.assertIn("UT_Relic_50080.png", names)  # 时间怀表触发后的破碎图案
        self.assertEqual(len(names), len(set(names)))

    def test_shared_art_differs_by_rarity(self):
        """同一个图案的几个筹码稀有度各不相同：面板上靠底色能区分。"""
        db = load_db()
        seen = {}
        for chip in db.chips.values():
            key = (chip.icon, chip.rarity)
            self.assertNotIn(key, seen, f"{chip.name} 和 {seen.get(key)} 图案、稀有度都相同")
            seen[key] = chip.name

    @unittest.skipUnless(HAVE_ICONS, "没有下载筹码图标")
    def test_downloaded(self):
        self.assertEqual(chip_panel.missing_files(load_db()), [])


class InputTest(unittest.TestCase):
    def test_tiny_or_empty_frame(self):
        db = load_db()
        self.assertIsNone(read_chip_panel(np.zeros((50, 80, 3), np.uint8), db))
        self.assertIsNone(read_chip_panel(None, db))

    def test_blank_frame_is_not_panel(self):
        got = read_chip_panel(np.full((720, 1280, 3), 40, np.uint8), load_db())
        self.assertIsInstance(got, PanelReading)
        self.assertFalse(got.is_panel)
        self.assertEqual(got.owned, [])


@unittest.skipUnless(HAVE_SAMPLES and HAVE_ICONS, _skip_reason(*SAMPLE_SET, PANEL_LABELS))
class SampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.labels = {
            it["file"]: it for it in json.loads((SAMPLES / "panel_labels.json").read_text(encoding="utf-8"))["samples"]
        }

    def setUp(self):
        templates.assets._last_fit = None

    def test_reads_owned_chips(self):
        """panel_labels.json 里的面板截图：原图和缩到 1280 宽，读出的筹码、页签、点选的格子都和真值一致；筹码都不到一页，
        两端的翻页箭头都不亮（只有一页，见 tests/test_panel_pages.py）。
        只有一个筹码的那张拍在书签还是蓝底的时候，见 test_stale_blue_bookmark_sample。"""
        for name, item in self.labels.items():
            if name in STALE_SAMPLES:
                continue
            for width in (None, 1280):
                with self.subTest(name=name, width=width):
                    got = read_chip_panel(load(name, width), self.db, self_row=item["self_index"])
                    self.assertTrue(got.is_panel)
                    self.assertEqual(got.owned, item["owned"])
                    self.assertEqual(got.unknown_slots, 0)
                    self.assertEqual((got.tab_count, got.selected_tab), (item["tab_count"], item["selected_tab"]))
                    self.assertTrue(got.self_tab, got.reason)
                    self.assertFalse(got.page_full)
                    self.assertEqual((got.page, got.arrows), ("only", ("dim", "dim")))
                    self.assertGreaterEqual(got.confidence, 0.6)
                    self.assertEqual(got.problems, [])
                    self.assertEqual(got.selected_slot, item.get("selected_slot"))

    def test_stale_blue_bookmark_sample(self):
        """20261002_013927（只有一个筹码）拍在书签还是蓝底的时候（10-15 起书签改为紫底）：蓝色底和现在的资料对不上，
        这一格算认不出，不猜成别的筹码；面板本身和页签照常认出来。"""
        item = self.labels[ONE]
        got = read_chip_panel(load(ONE), self.db, self_row=item["self_index"])
        self.assertTrue(got.is_panel)
        self.assertEqual(got.owned, [])
        self.assertEqual(got.unknown_slots, 1)
        self.assertEqual(got.problems, ["第 1 格：图案对应「书签」，底色为蓝色，筹码表中没有这个稀有度的筹码"])
        self.assertEqual((got.tab_count, got.selected_tab), (item["tab_count"], item["selected_tab"]))

    @unittest.skipUnless(HAVE_SELECTED, samples.samples_reason(*SELECTED_SET))
    def test_selected_slot(self):
        """玩家点过的格子外圈从灰色变成黄绿色，照常读出筹码，标为点选（selected），其余格子不标；
        没有点选时 selected_slot 为 None。画面被三选一界面压暗（亮度 x0.35）时也照常读。"""
        for name in (TWO_SELECTED, SIX_SELECTED, EIGHT_SELECTED, EIGHT):
            item = self.labels[name]
            want = item.get("selected_slot")
            for width in (None, 1280):
                with self.subTest(name=name, width=width):
                    got = read_chip_panel(load(name, width), self.db, self_row=0)
                    self.assertEqual(got.selected_slot, want)
                    self.assertEqual([s.index for s in got.slots if s.selected], [] if want is None else [want])
                    if want is not None:
                        self.assertEqual((got.slots[want].state, got.slots[want].chip), ("chip", item["owned"][want]))
                    if width is None:
                        # 点选的格子照样算外圈对上的格子，不拉低面板定位的置信度（只按 9 格算时为 0.75）。
                        # 缩小后图案相关度变低，置信度由图案决定，不在这里比
                        self.assertEqual(got.confidence, 1.0)
        for name in (TWO_SELECTED, SIX_SELECTED, EIGHT_SELECTED):
            item = self.labels[name]
            with self.subTest(name=name, dimmed=0.35):
                templates.assets._last_fit = None
                got = read_chip_panel(dim(load(name), 0.35), self.db, self_row=0)
                self.assertTrue(got.is_panel, got.reason)
                self.assertEqual((got.owned, got.unknown_slots), (item["owned"], 0))
                self.assertEqual(got.selected_slot, item["selected_slot"])

    @unittest.skipUnless(HAVE_SELECTED, samples.samples_reason(*SELECTED_SET))
    def test_selected_flag_not_cached(self):
        """同一格的图案比对结果会沿用（_slot_cache），点选与否仍以这一帧的外圈为准。"""
        templates.assets._slot_cache.clear()
        before = read_chip_panel(load(EIGHT), self.db, self_row=0)
        after = read_chip_panel(load(EIGHT_SELECTED), self.db, self_row=0)
        self.assertEqual((before.slots[7].chip, before.slots[7].selected), ("金色飞星", False))
        self.assertEqual((after.slots[7].chip, after.slots[7].selected), ("金色飞星", True))
        # 同一帧同一格再读：命中缓存（缓存不再增加），点选标记照传入的外圈状态
        img = load(EIGHT)
        a = before.unit
        off = chip_panel._slot_offsets()
        ox, oy = before.slots[0].center[0] - off[0, 0] * a, before.slots[0].center[1] - off[0, 1] * a
        hsv = chip_panel._hsv_near(img, ox, oy, a)
        args = (img, hsv, 7, *before.slots[7].center, a, self.db, chip_panel._chips_by_icon(self.db), True)
        cached = len(templates.assets._slot_cache)
        got = [chip_panel._read_slot(*args, rim_state=(1.0, 1.0, flag)) for flag in (True, False)]
        self.assertEqual([(s.chip, s.selected) for s in got], [("金色飞星", True), ("金色飞星", False)])
        self.assertEqual(len(templates.assets._slot_cache), cached)

    def test_rarity_from_fill(self):
        """底色决定稀有度：维生素药丸、友情徽章、速度轮滑-中级是紫色（速度轮滑三档共用图案，靠底色区分）。
        只有一个筹码的那张拍在书签还是蓝底的时候，蓝底书签按「图案和稀有度对不上」算认不出，见 test_stale_blue_bookmark_sample。"""
        got = read_chip_panel(load(ONE), self.db, self_row=3)
        self.assertEqual([(s.chip, s.rarity) for s in got.slots if s.state == "chip"], [])
        got = read_chip_panel(load(THREE), self.db, self_row=0)
        self.assertEqual(
            [(s.chip, s.rarity) for s in got.slots if s.state == "chip"],
            [("维生素药丸", "紫"), ("友情徽章", "紫"), ("速度轮滑-中级", "紫")],
        )

    def test_self_tab_from_player_list(self):
        """给出自己的角色：在玩家列表里找到自己那一行，对应选中的页签。"""
        got = read_chip_panel(load(TWO), self.db, hero_key="远野汉娜")
        self.assertEqual((got.self_index, got.self_tab), (0, True), got.reason)
        self.assertIn("玩家列表", got.self_how)

    def test_bottom_sprite_not_used(self):
        """画面底部的像素小人是当前行动的玩家（真人对局实测），不用来判断自己的页签：
        不知道角色、也没有记住的行时无法确定，self_tab=False 并说明原因，筹码照样读出。"""
        got = read_chip_panel(load(ONE), self.db)
        self.assertEqual((got.self_index, got.self_tab), (None, False))
        got = read_chip_panel(load(TWO), self.db)
        self.assertIsNone(got.self_index)
        self.assertFalse(got.self_tab)
        self.assertIn("无法确定自己是第几个页签", got.reason)
        self.assertEqual(got.owned, ["维生素药丸", "友情徽章"])  # 筹码照样读出，由调用方决定用不用

    @unittest.skipUnless(samples.samples_ok(KNOCKED_OUT), samples.samples_reason(KNOCKED_OUT))
    def test_knocked_out_self_tab(self):
        """「昏迷」字样盖住了自己的头像。没有记住的行时按名字认（其余三行都是「【BotN】」）；
        给出本局已确认的行时直接用它；不给角色、也没有行号和名字时无法确定（不靠名字乱猜）。"""
        img = load(KNOCKED_OUT)
        got = read_chip_panel(img, self.db, hero_key="照")
        self.assertEqual((got.self_index, got.self_tab), (0, True), got.reason)
        self.assertIn("唯一的真人玩家", got.self_how)
        self.assertEqual(got.owned, ["电流核心", "电磁炮", "孔雀扇", "普通瞄具", "拳击手套-高级"])
        got = read_chip_panel(img, self.db, hero_key="照", self_row=0)
        self.assertEqual((got.self_tab, got.self_how), (True, "本局已确认的行"))
        got = read_chip_panel(img, self.db)
        self.assertEqual((got.self_index, got.self_tab), (None, False))

    def test_avatar_wins_over_remembered_row(self):
        """头像认得出时以头像为准：记住的行号（例如上一局的）对不上也不用。"""
        got = read_chip_panel(load(TWO), self.db, hero_key="远野汉娜", self_row=2)
        self.assertEqual((got.self_index, got.self_tab, got.self_how), (0, True, "玩家列表"))

    def test_teammate_tab(self):
        """把橙色改画到另一个页签上（模拟点开了队友的页签）：判为不是自己，并写明是第几个页签。"""
        tool = panel_synth
        img = load(THREE)
        first = read_chip_panel(img, self.db, self_row=0)
        swapped = tool.swap_selected_tab(img, first, 2)
        got = read_chip_panel(swapped, self.db, self_row=0)
        self.assertEqual(got.selected_tab, 2)
        self.assertFalse(got.self_tab)
        self.assertIn("队友", got.reason)

    def test_sprite_does_not_override_list(self):
        """底部像素小人是别人时（轮到队友行动），照玩家列表的结论。"""
        got = read_chip_panel(load(THREE), self.db, self_row=2)
        self.assertEqual((got.self_index, got.self_how), (2, "本局已确认的行"))

    def test_real_game_panel_during_own_turn(self):
        """真人对局（1600x855 预览图）：按玩家列表的头像认出自己的页签。"""
        for name, owned in (
            ("20261002_213305_chips_panel_two_chips_1600x855.jpg", ["“8面”骰子", "紫色飞星"]),
            ("20261002_213909_chips_panel_four_chips_1600x855.jpg", ["“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机"]),
        ):
            with self.subTest(name=name):
                got = read_chip_panel(load(name), self.db, hero_key="芬妮")
                self.assertEqual((got.self_index, got.self_tab, got.owned), (0, True, owned), got.reason)

    def test_not_panel(self):
        """被战斗画面打断的反例、三选一、平常的对局画面都不是面板。"""
        for name in (
            "20261002_021351_battle_scene.png",
            "20261002_013522_chipselect_levelup_star1_badge_on.png",
            "20261002_012918_hud_round1.png",
            "20261002_012947_task_panel.png",
        ):
            with self.subTest(name=name):
                got = read_chip_panel(load(name, 1920), self.db)
                self.assertFalse(got.is_panel)
                self.assertFalse(got.self_tab)
                self.assertEqual(got.owned, [])

    def test_reuses_last_position(self):
        """同一尺寸的画面连续读：第二次沿用上次的位置，结果相同。"""
        img = load(THREE, 1920)
        a = read_chip_panel(img, self.db, self_row=0)
        self.assertIsNotNone(templates.assets._last_fit)
        b = read_chip_panel(img, self.db, self_row=0)
        self.assertEqual(a.owned, b.owned)
        self.assertAlmostEqual(a.unit, b.unit, places=6)
        # 换成不是面板的画面，位置记录作废
        read_chip_panel(load("20261002_021351_battle_scene.png", 1920), self.db)
        self.assertIsNone(templates.assets._last_fit)

    def test_missing_icons(self):
        """没有图标文件时仍能认出面板和有筹码的格子，但都算无法识别。"""
        with mock.patch.object(chip_panel, "_load_icons", return_value={}):
            templates.assets._slot_cache.clear()
            got = read_chip_panel(load(TWO, 1920), self.db, self_row=0)
        templates.assets._slot_cache.clear()
        self.assertTrue(got.is_panel)
        self.assertEqual(got.owned, [])
        self.assertEqual(got.unknown_slots, 2)
        self.assertTrue(any("缺少筹码图标" in p for p in got.problems))


@unittest.skipUnless(HAVE_RAW and HAVE_ICONS, _skip_reason(*SAMPLE_SET))
class SyntheticTest(unittest.TestCase):
    """把 wiki 图标按稀有度底色画进空面板，检验共用图案的系列、金色、满页和无法识别的图案。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.tool = panel_synth
        templates.assets._last_fit = None
        cls.base = load(EMPTY)
        cls.empty = read_chip_panel(cls.base, cls.db, self_row=3)

    def setUp(self):
        templates.assets._last_fit = None

    def draw(self, names):
        return self.tool.draw_chips(self.base, self.empty, [self.db.chips[n] for n in names])

    def test_shared_art_series(self):
        names = ["拳击手套-初级", "拳击手套-中级", "拳击手套-高级", "医疗箱-紧急治疗", "医疗箱-完备治疗",
                 "美工刀-初级", "美工刀-锋利", "紫色飞星", "金色飞星", "鹰眼瞄具"]
        for width in (None, 1280):
            with self.subTest(width=width):
                img = self.draw(names)
                if width:
                    img = cv2.resize(img, None, fx=width / img.shape[1], fy=width / img.shape[1], interpolation=cv2.INTER_AREA)
                got = read_chip_panel(img, self.db, self_row=3)
                self.assertEqual(got.owned, names)
                self.assertTrue(got.page_full)  # 这一页 10 格都满（是不是全部筹码看 page）
                self.assertEqual(got.unknown_slots, 0)

    def test_empty_slots_keep_confidence(self):
        """只有一两个筹码、其余都是空格子（例如翻到只有一个筹码的最后一页）：空格子不拉低置信度，和 10 格都满时一样只看认出的
        筹码；两端的箭头都不亮，是只有一页。"""
        for names in (["书签"], ["书签", "大背包"]):
            with self.subTest(names=names):
                got = read_chip_panel(self.draw(names), self.db, self_row=3)
                self.assertEqual((got.owned, got.unknown_slots, got.page), (names, 0, "only"))
                self.assertGreaterEqual(got.confidence, 0.9)

    def test_gong_is_doubtful(self):
        """队友的大铜锣触发后，自己背包里会出现无效的大铜锣图标：照样读出，但列入 doubtful 并给出提示。"""
        got = read_chip_panel(self.draw(["书签", "大铜锣"]), self.db, self_row=3)
        self.assertEqual(got.owned, ["书签", "大铜锣"])
        self.assertEqual(got.doubtful, ["大铜锣"])
        self.assertTrue(any("无效" in p for p in got.problems))

    def test_broken_pocket_watch(self):
        """时间怀表触发后图标换成破碎版本（50080），仍认作时间怀表。"""
        img = self.draw(["书签"])
        cx, cy = self.empty.slots[1].center
        a = self.empty.unit
        colors = self.tool.rarity_bgr()
        cv2.circle(img, (round(cx), round(cy)), round(66.3 * a), colors["金"], -1)
        icon = cv2.imdecode(np.fromfile(str(assets_dir("chips") / "UT_Relic_50080.png"), np.uint8), cv2.IMREAD_UNCHANGED)
        size = round(2 * chip_panel.ICON_HALF * a)
        icon = cv2.resize(icon, (size, size), interpolation=cv2.INTER_AREA)
        x0, y0 = round(cx - size / 2), round(cy - size / 2)
        alpha = icon[:, :, 3:4] / 255.0
        img[y0 : y0 + size, x0 : x0 + size] = (icon[:, :, :3] * alpha + img[y0 : y0 + size, x0 : x0 + size] * (1 - alpha)).astype(np.uint8)
        got = read_chip_panel(img, self.db, self_row=3)
        self.assertEqual(got.owned, ["书签", "时间怀表"])

    def test_unknown_art_and_wrong_rarity(self):
        """图案不在图标库里、或图案和稀有度对不上（大铜锣只有蓝色）时算无法识别，不猜成别的筹码。"""
        img = self.draw(["大铜锣", "大背包"])
        a = self.empty.unit
        colors = self.tool.rarity_bgr()
        # 第 3 格：紫色底上画一个图标库里没有的图案
        cx, cy = self.empty.slots[2].center
        cv2.circle(img, (round(cx), round(cy)), round(66.3 * a), colors["紫"], -1)
        cv2.rectangle(img, (round(cx - 30 * a), round(cy - 30 * a)), (round(cx + 30 * a), round(cy + 30 * a)), (255, 255, 255), -1)
        cv2.line(img, (round(cx - 30 * a), round(cy - 30 * a)), (round(cx + 30 * a), round(cy + 30 * a)), (0, 0, 0), max(1, round(8 * a)))
        # 第 4 格：大铜锣的图案画在紫色底上
        fake = self.tool.draw_chips(self.base, self.empty, [self.db.chips[n] for n in ("大铜锣", "大背包", "大铜锣", "大铜锣")])
        cx4, cy4 = self.empty.slots[3].center
        r = round(85 * a)
        img[round(cy4) - r : round(cy4) + r, round(cx4) - r : round(cx4) + r] = fake[round(cy4) - r : round(cy4) + r, round(cx4) - r : round(cx4) + r]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
        disk = (np.hypot(xx - cx4, yy - cy4) <= 66 * a) & (np.abs(hsv[..., 0].astype(int) - 96) <= 8) & (hsv[..., 1] > 150)
        img[disk] = colors["紫"]
        got = read_chip_panel(img, self.db, self_row=3)
        self.assertEqual(got.owned, ["大铜锣", "大背包"])
        self.assertEqual(got.unknown_slots, 2)
        self.assertEqual([s.state for s in got.slots[:5]], ["chip", "chip", "unknown", "unknown", "empty"])
        self.assertEqual(got.slots[3].icon, "50070")
        self.assertIn("底色为紫色，筹码表中没有这个稀有度的筹码", got.slots[3].note)
        self.assertTrue(any(p.startswith("第 3 格") for p in got.problems))

    def ring(self, img, index, hsv_color):
        """在第 index 格画一圈外圈（半径 78 R、线宽 10 R），颜色按 OpenCV HSV 给出。"""
        cx, cy = self.empty.slots[index].center
        a = self.empty.unit
        bgr = tuple(int(v) for v in cv2.cvtColor(np.uint8([[hsv_color]]), cv2.COLOR_HSV2BGR)[0, 0])
        cv2.circle(img, (round(cx), round(cy)), round(78 * a), bgr, max(1, round(10 * a)), lineType=cv2.LINE_AA)

    def test_selected_rim_synthetic(self):
        """第 2 格画上实机的黄绿色外圈：原图和缩到 1280 宽都照常读出，并标为点选；
        第 3 格画上别的颜色（蓝色）的外圈仍算被遮挡，不当成点选。"""
        names = ["书签", "大背包", "标靶"]
        img = self.draw(names)
        self.ring(img, 1, SELECTED_RIM_HSV)
        other = img.copy()
        self.ring(other, 2, (100, 194, 249))
        for width in (None, 1280):
            with self.subTest(width=width):
                f = 1.0 if width is None else width / img.shape[1]
                scaled = img if width is None else cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
                got = read_chip_panel(scaled, self.db, self_row=3)
                self.assertEqual((got.owned, got.unknown_slots), (names, 0))
                self.assertEqual([s.selected for s in got.slots[:4]], [False, True, False, False])
                self.assertEqual(got.selected_slot, 1)
                scaled = other if width is None else cv2.resize(other, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
                got = read_chip_panel(scaled, self.db, self_row=3)
                self.assertEqual((got.owned, got.unknown_slots), (names[:2], 1))
                self.assertEqual((got.slots[2].state, got.slots[2].selected), ("hidden", False))


@unittest.skipUnless(HAVE_SAMPLES and HAVE_ICONS and HAVE_SELECTED, _skip_reason(*SAMPLE_SET, *SELECTED_SET, PANEL_LABELS))
class PanelOrderTest(unittest.TestCase):
    """真实面板上有格子读不出（外圈被挡住）时，Session 保留的原记录放回那一格的位置，已有筹码的顺序和面板一致。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        labels = json.loads((SAMPLES / "panel_labels.json").read_text(encoding="utf-8"))["samples"]
        cls.owned = next(it["owned"] for it in labels if it["file"] == EIGHT)
        templates.assets._last_fit = None
        cls.img = load(EIGHT)
        cls.clean = read_chip_panel(cls.img, cls.db, self_row=0)

    def setUp(self):
        templates.assets._last_fit = None

    def covered(self, indexes) -> np.ndarray:
        """把这几格的外圈（74~84 R 的圆环）涂黑，模拟被像素小人、弹出卡片挡住。"""
        img = self.img.copy()
        a = self.clean.unit
        yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
        for i in indexes:
            cx, cy = self.clean.slots[i].center
            d = np.hypot(xx - cx, yy - cy)
            img[(d >= 74 * a) & (d <= 84 * a)] = 0
        return img

    def test_recorded_chip_keeps_its_slot(self):
        self.assertEqual(self.clean.owned, self.owned)
        for indexes in ((3,), (0,), (0, 6)):
            with self.subTest(covered=indexes):
                reading = read_chip_panel(self.covered(indexes), self.db, self_row=0)
                self.assertEqual([s.index for s in reading.slots if s.state == "hidden"], list(indexes))
                self.assertEqual(reading.unknown_slots, len(indexes))
                session = Session(self.db, Settings(hero="阿尔"))
                session.set_owned(self.owned)
                ev = session.on_panel(reading)
                self.assertEqual(session.settings.owned, self.owned)
                self.assertEqual((ev.kind, ev.owned, ev.count), ("panel_read", self.owned, len(indexes)))


if __name__ == "__main__":
    unittest.main()
