"""对局外画面的识别（advisor/vision/screens.py）：按 samples/labels.json 的 screen 标注逐张核对原图和缩小版
（缩小版同 test_vision_matrix.shrink：宽、高缩到一半，但不低于最小窗口）。

  整幅识别  hud.read_pregame（离开对局后找加载画面地图名的那次识别，顺带读画面种类）：标了 screen 的样本读数和标注
            完全相同。组队房间开始匹配后整块变灰的两张，难度读不出也可以，不能读错
  下方区域  hud.is_select_screen(img, db, info)（读不到轮次时判断选角界面的那次识别）：结算页、主菜单要读全；
            其余画面要么读不出，要么读到的项和标注相同，不能读错
  反例      对局、三选一、面板、任务栏、选角、载入、卡牌商店等各挑一两张，两种识别都读不出
  解析本身  用构造的文字识别结果核对几条规则（不读样本，很快）
奖励页「星币」那一格的数（coins）是后加的读数：标注里没写这一项时不比（读出来的照样不能出现在别的画面上），简体客户端
那张奖励页样本另核对读出 45。
全部样本的反例扫描在 test_vision_matrix 里（打包前和全量矩阵一起跑）。识别在 setUpClass 里分给几个子进程一次读完。
"""
from __future__ import annotations

import dataclasses
import json
import multiprocessing
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor
from unittest import mock

import numpy as np

from advisor.data import DIFFICULTY_NAMES, load_db
from advisor.vision import anchors, hud, io, screens
from advisor.vision.chip_select import Line
from advisor.vision.screens import OutOfGame, parse_out_of_game
from tests.test_vision_matrix import HAVE_SAMPLES, SAMPLES, VARIANTS, WORKERS, _db, load, shrink
from tests.support import samples
from tests.test_vision_matrix import SCREEN_GREYED as GREYED  # 变灰的组队房间：难度读不出可以，读错不行

# 反例：对局里的各种画面和选角、载入等对局外画面（不是结算页、匹配成功页、房间、奖励页）
NEGATIVES = (
    "20261002_012918_hud_round1.png",
    "20261002_012947_task_panel.png",
    "20261002_013522_chipselect_levelup_star1_badge_on.png",
    "20261002_021351_battle_scene.png",
    "20261002_144036_loading_2560x1057.jpg",
    "20261002_191418_select_screen_2397x1798.jpg",
    "20261002_211623_skin_select_matchmaking_1600x855.jpg",
    "20261002_212105_player_detail_popup_1600x855.jpg",
    "20261002_214207_card_shop_transfer_button_1600x855.jpg",
    "20261003_124944_hud_levelup_banner_al_real.jpg",
    "20261003_132134_chips_panel_eight_chips_selected_2560x1369.jpg",
    "20261003_132306_card_give_al_skill_2560x1369.jpg",
)
RESULT_SCENES = {"result": {screens.RESULT}, "reward": {screens.REWARD}, "menu": {screens.MAIN_MENU},
                 "lobby": {screens.MATCH_FOUND, screens.TEAM_ROOM, screens.CREATE_ROOM, screens.CUSTOM_ROOM}}
# 后加的读数：标注里可以不写，不写时不比（见模块说明）
LATER_FIELDS = ("coins",)
REWARD_SAMPLE = "20261003_132534_reward_2560x1369.jpg"  # 简体客户端的奖励页：帐号经验在前、星币在后，星币 +45
# 判选角界面那次识别找字时短边只放大到 anchors.SELECT_DET_SIDE（SelectDetSideTest）：三张选角界面，主菜单、结算页各几张
DET_SIDE_PICK = (
    "20261002_191418_select_screen_2397x1798.jpg",
    "20261002_211559_select_screen_matchmaking_1600x855.jpg",
    "20261002_015300_select_screen_lowres.jpg",
)
DET_SIDE_OTHER = (
    "20261003_132548_menu_main_2560x1369.jpg",
    "20261002_151251_result_2560x1057.jpg",
    "20261003_132518_result_portraits_2560x1369.jpg",
    "20261002_215421_result_1600x855.jpg",
)


def _as_dict(screen: OutOfGame | None) -> dict | None:
    return None if screen is None else dataclasses.asdict(screen)


def _as_labelled(got: dict | None, want: dict | None) -> dict | None:
    """读数里去掉标注没写的后加项（LATER_FIELDS），好和标注整个比。"""
    if got is None or want is None:
        return got
    return {k: v for k, v in got.items() if k not in LATER_FIELDS or k in want}


def read_screens(file: str) -> dict:
    """在子进程里读一张样本的原图和缩小版：整幅识别（read_pregame）和下方区域（is_select_screen）的画面读数。"""
    db = _db()
    original = load(file)
    out = {"file": file, "variants": {}}
    for variant, img in zip(VARIANTS, (original, shrink(original))):
        if img is None:
            continue
        got: dict = {"size": [img.shape[1], img.shape[0]]}
        try:
            pre = hud.read_pregame(img, db)
            info: dict = {}
            got["pick"] = bool(hud.is_select_screen(img, db, info))
            got.update(full=_as_dict(pre.screen), map=pre.map, bottom=_as_dict(info.get("screen")))
        except Exception:  # noqa: BLE001  交给测试报告
            got["error"] = traceback.format_exc()
        out["variants"][variant] = got
    return out


def labelled() -> list[dict]:
    items = json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))["samples"]
    return [s for s in items if "screen" in s]


class LabelsTest(unittest.TestCase):
    """screen 标注本身：字段齐全、取值合法，和 scene 对得上（不读图）。"""

    def test_screen_labels(self):
        samples.require_samples("labels.json")
        db = load_db()
        items = labelled()
        self.assertTrue(items)
        for item in items:
            with self.subTest(file=item["file"]):
                screen = item["screen"]
                self.assertIn(item["scene"], RESULT_SCENES)
                if screen is None:
                    continue
                fields = {f.name for f in dataclasses.fields(OutOfGame)}
                self.assertEqual(set(screen) | set(LATER_FIELDS), fields)
                self.assertLessEqual(set(screen), fields)
                self.assertIn(screen["kind"], RESULT_SCENES[item["scene"]])
                self.assertIn(screen["difficulty"], (None, *DIFFICULTY_NAMES))
                self.assertIn(screen["map"], (None, *db.maps))
                self.assertTrue(screen["rounds"] is None or isinstance(screen["rounds"], int))
                if screen["kind"] == screens.RESULT:
                    self.assertTrue(screen["difficulty"] and screen["map"] and screen["rounds"])
        kinds = {item["screen"]["kind"] for item in items if item["screen"]}
        self.assertEqual(kinds, set(screens.KINDS))  # 每种画面都有样本


@unittest.skipUnless(HAVE_SAMPLES, samples.samples_reason("labels.json"))
class SampleScreensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = {s["file"]: s for s in labelled()}
        files = [*cls.items, *NEGATIVES]
        if WORKERS <= 1:
            results = [read_screens(f) for f in files]
        else:
            with ProcessPoolExecutor(min(WORKERS, len(files)), mp_context=multiprocessing.get_context("spawn")) as pool:
                results = list(pool.map(read_screens, files))
        cls.results = {r["file"]: r for r in results}

    def cases(self, files):
        for file in files:
            for variant, got in self.results[file]["variants"].items():
                if "error" in got:
                    self.fail(f"{file} {variant} 识别时出错：\n{got['error']}")
                yield file, variant, got

    def test_both_variants(self):
        """每张都读了原图和缩小版（缩小版比原图小）。"""
        for file in (*self.items, *NEGATIVES):
            with self.subTest(file=file):
                variants = self.results[file]["variants"]
                self.assertEqual(list(variants), list(VARIANTS))
                self.assertLess(variants["缩小"]["size"][0], variants["原图"]["size"][0])

    def test_full_reading(self):
        """整幅识别：和标注完全相同；变灰的组队房间难度可以读不出。认出这几种画面时不找加载画面的地图名。"""
        for file, variant, got in self.cases(self.items):
            want = self.items[file]["screen"]
            with self.subTest(file=file, variant=variant):
                full = got["full"]
                if file in GREYED and full is not None and full["difficulty"] is None:
                    full = dict(full, difficulty=want["difficulty"])
                if full is not None and full["kind"] != screens.REWARD:
                    self.assertIsNone(full["coins"])  # 星币数只有奖励页上有
                self.assertEqual(_as_labelled(full, want), want)
                if want is not None:
                    self.assertIsNone(got["map"])  # 房间设置里的「游玩地图」、结算页的地图都不是加载画面的地图名

    def test_bottom_reading(self):
        """下方区域：结算页、主菜单读全；其余画面读不出可以，读到的项不能和标注不同。"""
        for file, variant, got in self.cases(self.items):
            want = self.items[file]["screen"]
            with self.subTest(file=file, variant=variant):
                bottom = got["bottom"]
                self.assertFalse(got["pick"])
                if want is not None and want["kind"] in (screens.RESULT, screens.MAIN_MENU):
                    self.assertEqual(_as_labelled(bottom, want), want)
                elif bottom is not None:
                    self.assertIsNotNone(want, bottom)
                    self.assertEqual(bottom["kind"], want["kind"])
                    for key, value in bottom.items():
                        self.assertIn(value, (None, want.get(key)), key)

    def test_main_menu_reading(self):
        """主菜单：原图和缩小版、整幅识别和下方区域都要读成主菜单。对局中回到大厅时，靠下方区域那一路在几秒内发现。"""
        files = [f for f, item in self.items.items()
                 if item["screen"] and item["screen"]["kind"] == screens.MAIN_MENU]
        self.assertTrue(files)
        for file, variant, got in self.cases(files):
            want = self.items[file]["screen"]
            with self.subTest(file=file, variant=variant):
                self.assertEqual(_as_labelled(got["full"], want), want)
                self.assertEqual(_as_labelled(got["bottom"], want), want)
                self.assertIsNone(got["map"])

    def test_team_room_difficulty(self):
        """组队房间：没变灰的三张整幅识别读出勾选的难度（普通 1 张、噩梦 2 张）。"""
        read = {}
        for file, variant, got in self.cases(f for f in self.items if f not in GREYED):
            if self.items[file]["screen"] and self.items[file]["screen"]["kind"] == screens.TEAM_ROOM:
                read[(file, variant)] = got["full"]["difficulty"]
        self.assertEqual(sorted(read.values()), ["噩梦"] * 4 + ["普通"] * 2)

    def test_reward_coins(self):
        """简体客户端的奖励页（帐号经验在前、星币在后）：整幅识别读出星币 +45，原图和缩小版都是。"""
        if REWARD_SAMPLE not in self.results:
            self.skipTest(f"labels.json 里没有 {REWARD_SAMPLE}")
        for file, variant, got in self.cases([REWARD_SAMPLE]):
            with self.subTest(variant=variant):
                self.assertEqual(got["full"]["kind"], screens.REWARD)
                self.assertEqual(got["full"]["coins"], 45)

    def test_negatives(self):
        """反例：两种识别都读不出；选角界面照常判成选角界面。"""
        for file, variant, got in self.cases(NEGATIVES):
            with self.subTest(file=file, variant=variant):
                self.assertIsNone(got["full"])
                self.assertIsNone(got["bottom"])
                self.assertEqual(got["pick"], "select_screen" in file)

    def test_one_ocr_call(self):
        """is_select_screen 传了 info 也只做一次文字识别；read_pregame 也是一次。"""
        db = load_db()
        img = load("20261003_132518_result_portraits_2560x1369.jpg")
        with mock.patch.object(io, "ocr", wraps=io.ocr) as ocr:
            info: dict = {}
            self.assertFalse(hud.is_select_screen(img, db, info))
            self.assertEqual(ocr.call_count, 1)
            self.assertEqual(info["screen"], OutOfGame(screens.RESULT, "噩梦", "异变图书馆", 12))
            ocr.reset_mock()
            self.assertEqual(hud.read_pregame(img, db).screen, info["screen"])
            self.assertEqual(ocr.call_count, 1)


def L(text, cx, cy, h=20.0, w=None):  # noqa: N802  构造一段识别结果
    return Line(text, float(cx), float(cy), float(w if w is not None else h * len(text)), float(h))


class ParseTest(unittest.TestCase):
    """解析规则（构造的文字识别结果，不读样本）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def parse(self, lines, img=None):
        return parse_out_of_game(img, lines, self.db)

    def test_result_needs_two_of_three(self):
        bar = L("异变图书馆(合作挑战)", 400, 1000)
        self.assertIsNone(self.parse([bar, L("噩梦", 900, 1000)]))  # 只对上难度名：可能是入场动画里的残影，不算
        self.assertEqual(self.parse([bar, L("噩梦", 900, 1000), L("12轮", 1000, 1002)]),
                         OutOfGame(screens.RESULT, "噩梦", "异变图书馆", 12))
        self.assertEqual(self.parse([bar, L("12轮", 1000, 1002), L("确定!", 1200, 998)]),
                         OutOfGame(screens.RESULT, None, "异变图书馆", 12))
        # 不在同一行的不算
        self.assertIsNone(self.parse([bar, L("噩梦", 900, 1100), L("12轮", 1000, 1100)]))

    def test_result_map_split_from_anchor(self):
        """地图名和「（合作挑战）」识别成两段时取左边紧挨着的那一段；名字里的「·」读成别的点也认。"""
        got = self.parse([L("星趴・梦想号", 300, 1000), L("（合作挑战）", 500, 1000), L("困难", 900, 1000),
                          L("19轮", 1000, 1000)])
        self.assertEqual(got, OutOfGame(screens.RESULT, "困难", "星趴·梦想号", 19))

    def test_match_found(self):
        """「匹配成功」和「地图名·难度名」两处都要有（读到这一页会结束正在进行的一局，只有一处不算）。"""
        self.assertEqual(self.parse([L("匹配成功！", 960, 200), L("异变图书馆·噩梦", 960, 300)]),
                         OutOfGame(screens.MATCH_FOUND, "噩梦", "异变图书馆"))
        self.assertIsNone(self.parse([L("匹配成功！", 960, 200)]))
        self.assertIsNone(self.parse([L("异变图书馆·噩梦", 960, 300)]))
        # 「地图·难度」那一行要正好是资料库里的地图名加难度名
        self.assertIsNone(self.parse([L("匹配成功！", 960, 200), L("图书馆·噩梦", 960, 300)]))
        self.assertIsNone(self.parse([L("匹配成功！", 960, 200), L("噩梦", 960, 300)]))
        # 只有「匹配成功」几个字时不挡住后面的解析：组队房间照常认
        self.assertEqual(self.parse([L("匹配成功", 960, 100), L("选择难度", 200, 800, 30), L("普通", 200, 880),
                                     L("困难", 400, 880)]), OutOfGame(screens.TEAM_ROOM))

    def test_room_dropdown_list_is_not_current_value(self):
        """下拉框展开时，列表里的其他难度在下面几行，不是当前值。"""
        lines = [L("地图详细:", 300, 100), L("游玩地图", 300, 150), L("星趴·梦想号", 420, 150), L("难度", 300, 250),
                 L("普通", 420, 250), L("普通", 420, 300), L("困难", 420, 340), L("噩梦", 420, 380)]
        self.assertEqual(self.parse(lines), OutOfGame(screens.CREATE_ROOM, "普通", "星趴·梦想号"))
        self.assertEqual(self.parse([L("房间ID:410335", 300, 50), *lines]),
                         OutOfGame(screens.CUSTOM_ROOM, "普通", "星趴·梦想号"))
        # 标签和值识别成一段
        self.assertEqual(self.parse([L("游玩地图园林中庭", 300, 150), L("难度普通", 300, 250)]),
                         OutOfGame(screens.CREATE_ROOM, "普通", "园林中庭"))
        # 没有「游玩地图」「地图详细」的「难度」两个字不算房间
        self.assertIsNone(self.parse([L("难度", 300, 250), L("普通", 500, 250)]))

    def test_team_room_without_image(self):
        lines = [L("选择难度", 200, 800, 30), L("普通", 200, 880), L("困难", 400, 880)]
        self.assertEqual(self.parse(lines), OutOfGame(screens.TEAM_ROOM))

    def test_checked_is_conservative(self):
        """勾选框拿不准时读不出，不猜。"""
        off = (0.0, 1.0, 0.0)
        self.assertEqual(screens._checked({"普通": (0.5, 5.0, 120.0), "困难": off, "噩梦": off}), "普通")
        self.assertIsNone(screens._checked({"普通": (0.5, 5.0, 120.0), "困难": (0.4, 5.0, 110.0), "噩梦": off}))
        self.assertIsNone(screens._checked({"普通": (0.5, 5.0, 120.0), "困难": (0.05, 2.0, 40.0)}))
        grey_mark, grey_flat = (0.0, 17.0, 5.0), (0.0, 1.0, 0.0)
        self.assertEqual(screens._checked({"普通": grey_flat, "噩梦": grey_mark, "疯狂": grey_flat}), "噩梦")
        self.assertIsNone(screens._checked({"普通": grey_mark, "噩梦": grey_mark, "疯狂": grey_flat}))
        self.assertIsNone(screens._checked({"普通": (0.0, 6.0, 5.0), "噩梦": grey_mark}))  # 介于两者之间
        self.assertIsNone(screens._checked({"噩梦": grey_mark}))

    def test_reward(self):
        self.assertEqual(self.parse([L("获得奖励", 300, 400), L("每周对局获取上限 365/1000", 300, 700)]),
                         OutOfGame(screens.REWARD))
        self.assertIsNone(self.parse([L("获得奖励", 300, 400)]))

    def test_main_menu(self):
        """主菜单：开始按钮和底下一排按钮（至少认出 4 个）都要有。只有其中一处、或底下那排只认出三个时不算。"""
        start = L("开始游戏", 2283, 1065, 110)
        buttons = [L(t, 1666 + 118 * i, 1259, 40)
                   for i, t in enumerate(("任务", "好友", "公告", "仓库", "图鉴", "邮件", "设置", "退出"))]
        cases = [
            ("简体", [start, *buttons], OutOfGame(screens.MAIN_MENU)),
            ("繁体", [L("開始遊戲", 2270, 1200, 122),
                    *[L(t, 1596 + 124 * i, 1404, 48)
                      for i, t in enumerate(("任務", "好友", "公會", "圖鑑", "倉庫", "郵件", "公告", "設定"))]],
             OutOfGame(screens.MAIN_MENU)),
            ("只有开始按钮", [start], None),
            ("只有底下那排", buttons, None),
            ("底下那排只认出三个", [start, *buttons[:3]], None),
        ]
        for name, lines, want in cases:
            with self.subTest(name):
                self.assertEqual(self.parse(lines), want)

    def test_result_traditional(self):
        """繁体结算页：地图、难度、轮数、「確定」都认，交回简体内部名。底栏的轮数「12輪」「12 Round」都认。"""
        bar = L("異變圖書館(合作挑戰)", 400, 1000)
        for rounds in ("12輪", "12 Round"):
            with self.subTest(rounds=rounds):
                self.assertEqual(self.parse([bar, L("噩夢", 900, 1000), L(rounds, 1000, 1002)]),
                                 OutOfGame(screens.RESULT, "噩梦", "异变图书馆", 12))
        self.assertEqual(self.parse([L("星趴・夢想號", 300, 1000), L("（合作挑戰）", 500, 1000),
                                     L("瘋狂", 900, 1000), L("確定！", 1200, 1000), L("19輪", 1000, 1000)]),
                         OutOfGame(screens.RESULT, "疯狂", "星趴·梦想号", 19))

    def test_match_found_traditional(self):
        self.assertEqual(self.parse([L("匹配成功！", 960, 200), L("異變圖書館·噩夢", 960, 300)]),
                         OutOfGame(screens.MATCH_FOUND, "噩梦", "异变图书馆"))

    def test_room_traditional(self):
        """创建房间、自建房间的繁体字样：难度、游玩地图、地图详细、房间ID，难度读出繁体也交回简体名。"""
        lines = [L("地圖詳細:", 300, 100), L("遊玩地圖", 300, 150), L("星趴·夢想號", 420, 150),
                 L("難度", 300, 250), L("困難", 420, 250)]
        self.assertEqual(self.parse(lines), OutOfGame(screens.CREATE_ROOM, "困难", "星趴·梦想号"))
        self.assertEqual(self.parse([L("房間ID:410335", 300, 50), *lines]),
                         OutOfGame(screens.CUSTOM_ROOM, "困难", "星趴·梦想号"))
        # 标签和值识别成一段
        self.assertEqual(self.parse([L("遊玩地圖園林中庭", 300, 150), L("難度普通", 300, 250)]),
                         OutOfGame(screens.CREATE_ROOM, "普通", "园林中庭"))

    def test_team_room_traditional(self):
        """组队房间的「選擇難度」和繁体难度名：交回简体内部名，按字数分位置照旧。"""
        lines = [L("選擇難度", 200, 800, 30), L("普通", 200, 880), L("困難", 400, 880), L("噩夢", 600, 880)]
        self.assertEqual(self.parse(lines), OutOfGame(screens.TEAM_ROOM))
        self.assertEqual([name for name, *_ in screens._difficulty_labels(lines, lines[0])],
                         ["普通", "困难", "噩梦"])

    def test_reward_traditional(self):
        self.assertEqual(self.parse([L("獲得獎勵", 300, 400), L("每周對局獲取上限 365/1000", 300, 700)]),
                         OutOfGame(screens.REWARD))
        self.assertIsNone(self.parse([L("獲得獎勵", 300, 400)]))

    def test_reward_coins_layouts(self):
        """奖励页「星币」那一格的 +N：繁体录像上星币在第一格（3840x2160，位置、字高照录），简体样本上帐号经验在前
        （2560x1369）；旁边一格、下一行的 +N 都不算。"""
        head = [L("獲得獎勵", 862, 665, 122), L("每周對局獲取上限45/1000", 1255, 1216, 76)]
        hant = [L("星幣", 453, 875, 63), L("+30", 557, 944, 123), L("帳號經驗", 1179, 864, 50), L("+10", 1271, 944, 123),
                L("鼠鼠籌碼", 468, 1143, 47), L("+35", 561, 1225, 123)]
        self.assertEqual(self.parse([*head, *hant]), OutOfGame(screens.REWARD, coins=30))
        hans = [L("获得奖励", 431, 333, 61), L("每周对局获取上限 365/1000", 628, 608, 38),
                L("账号经验", 353, 546, 26), L("星币", 804, 553, 42), L("+10", 415, 597, 92), L("+45", 868, 597, 94),
                L("+60", 415, 775, 92)]
        self.assertEqual(self.parse(hans), OutOfGame(screens.REWARD, coins=45))

    def test_reward_coins_reading(self):
        """标签和数识别成一段也取；全角加号、没有加号的数也认；标签下面没有数、数离得太远时读不出（奖励页照样认）。"""
        head = [L("获得奖励", 300, 400), L("每周对局获取上限 365/1000", 300, 700)]
        cases = [
            ("同一段", [L("星币+30", 500, 900, 40)], 30),
            ("全角加号", [L("星币", 500, 900, 30), L("＋25", 520, 940, 60)], 25),
            ("没有加号", [L("星币", 500, 900, 30), L("15", 520, 940, 60)], 15),
            ("取最近的", [L("星币", 500, 900, 30), L("+20", 520, 940, 60), L("+99", 520, 980, 60)], 20),
            ("没有数", [L("星币", 500, 900, 30)], None),
            ("在上面", [L("星币", 500, 900, 30), L("+30", 520, 860, 60)], None),
            ("太远", [L("星币", 500, 900, 30), L("+30", 900, 940, 60)], None),
            ("太低", [L("星币", 500, 900, 30), L("+30", 520, 1000, 60)], None),
            ("不是数", [L("星币", 500, 900, 30), L("+三十", 520, 940, 60)], None),
            ("字母 O", [L("星币", 500, 900, 30), L("+1O", 520, 940, 60)], None),
            ("多读出一位", [L("星币", 500, 900, 30), L("+100", 520, 940, 60)], None),
            ("连着旁边的小图标", [L("星币", 500, 900, 30), L("+1010", 520, 940, 60)], None),
            ("同一段多读出一位", [L("星币+100", 500, 900, 40)], None),
        ]
        for name, lines, coins in cases:
            with self.subTest(name):
                self.assertEqual(self.parse([*head, *lines]), OutOfGame(screens.REWARD, coins=coins))

    def test_traditional_recorded_lines(self):
        """繁体中文界面实录的几种画面：3840x2160 录像抽帧，文字识别读到的原样（位置、字高照录，只留解析用得到的几段；
        房间号换成了别的数）。标签后面带全角冒号（「地圖詳細：」「房間ID：」）、难度名后面带圆点（「噩夢●」），结算页底栏写「07輪」，
        奖励页的上限一行数字紧贴着字。都交回简体内部名。"""
        room = [L("地圖詳細：", 424, 599, 101), L("遊玩地圖", 611, 765, 86), L("森秋幻境", 1181, 762, 95),
                L("難度", 672, 1035, 97), L("普通", 1220, 1032, 103)]
        bar = [L("森秋幻境(合作挑戰)", 1433, 2041, 141), L("普通", 2712, 2060, 95), L("確定", 3672, 2047, 86)]
        cases = [
            ("组队房间", [L("選擇地圖", 318, 341, 105), L("森秋幻境", 747, 600, 120), L("選擇難度", 307, 1693, 109),
                      L("普通", 278, 1854, 137), L("困難", 862, 1854, 128), L("噩夢●", 1341, 1853, 126),
                      L("瘋狂●", 1770, 1853, 139)], OutOfGame(screens.TEAM_ROOM)),
            ("创建房间", room, OutOfGame(screens.CREATE_ROOM, "普通", "森秋幻境")),
            ("自建房间", [L("房間ID：410335", 515, 181, 104), *room], OutOfGame(screens.CUSTOM_ROOM, "普通", "森秋幻境")),
            ("匹配成功", [L("匹配成功", 3063, 647, 313), L("森秋幻境·普通", 861, 911, 261)],
             OutOfGame(screens.MATCH_FOUND, "普通", "森秋幻境")),
            ("结算页", [*bar, L("07輪", 3060, 2043, 166)], OutOfGame(screens.RESULT, "普通", "森秋幻境", 7)),
            # 「07輪」偶尔读成两段（「07章」「輪」）：轮数读不出，难度和「確定」两项对上照样认作结算页
            ("结算页拆段", [*bar, L("07章", 3029, 2043, 133), L("輪", 3120, 2058, 95)],
             OutOfGame(screens.RESULT, "普通", "森秋幻境")),
            ("奖励页", [L("獲得獎勵", 862, 665, 122), L("每周對局獲取上限45/1000", 1255, 1216, 76)],
             OutOfGame(screens.REWARD)),
        ]
        for name, lines, want in cases:
            with self.subTest(name):
                self.assertEqual(self.parse(lines), want)
        # 组队房间的难度名带圆点也认，按字数分位置照旧
        team = cases[0][1]
        self.assertEqual([lab[0] for lab in screens._difficulty_labels(team, team[2])], ["普通", "困难", "噩梦", "疯狂"])

    def test_hant_map_names(self):
        maps = screens._maps(self.db)
        self.assertEqual(screens._map_of("異變圖書館", maps), "异变图书馆")
        self.assertEqual(screens._map_of("星趴・夢想號", maps), "星趴·梦想号")

    def test_select_screen_traditional(self):
        """选角界面：下方排着的称号写成繁体也认。"""
        img = np.zeros((1080, 1920, 3), np.uint8)
        titles = [h.hant_title for h in self.db.heroes.values() if len(h.hant_title) >= 2][:8]
        lines = [L(t, 200.0 + 150 * i, 900) for i, t in enumerate(titles)]
        with mock.patch.object(io, "ocr", return_value=lines):
            self.assertTrue(hud.is_select_screen(img, self.db))

    def test_loading_map_name_traditional(self):
        """加载画面上的繁体地图名交回简体内部名。"""
        name = "异变图书馆"
        lines = [L(self.db.map_info[name].hant, 960, 540, 40)]
        self.assertEqual(hud._exact_map_name(self.db, lines, 1920, 1080), name)

    def test_map_marker_traditional(self):
        """地图标记「線索」给异变图书馆加分。"""
        hud_read = hud._guess_map(self.db, [L("線索：0/5", 1800, 100)])
        self.assertEqual(hud_read.map_scores.get("异变图书馆"), 1)

    def test_pregame_default_screen(self):
        """Pregame 的新字段有默认值：现有的两参数写法照常可用。"""
        self.assertIsNone(hud.Pregame(None, ["狂风"]).screen)


class SelectDetSideTest(unittest.TestCase):
    """判选角界面那次识别（hud.is_select_screen）找字时短边只放大到 anchors.SELECT_DET_SIDE（引擎默认 736）：交给 io.ocr 的是
    这个设置；三张选角界面认作选角界面，主菜单、结算页认不作选角界面、认出的画面种类不变，都和默认设置判的一样。"""

    def test_det_side_passed(self):
        with mock.patch.object(io, "ocr", return_value=[]) as ocr:
            hud.is_select_screen(np.zeros((1080, 1920, 3), np.uint8), load_db())
        self.assertEqual(ocr.call_args.kwargs.get("det_side"), anchors.SELECT_DET_SIDE)
        self.assertEqual(anchors.SELECT_DET_SIDE, 320)

    def test_same_as_default(self):
        samples.require_samples(*DET_SIDE_PICK, *DET_SIDE_OTHER)
        db = load_db()
        for name in DET_SIDE_PICK + DET_SIDE_OTHER:
            img = load(name)
            got = {}
            for side in (anchors.SELECT_DET_SIDE, None):
                info: dict = {}
                with mock.patch.object(hud, "SELECT_DET_SIDE", side):
                    picking = hud.is_select_screen(img, db, info)
                got[side] = (picking, getattr(info.get("screen"), "kind", None))
            with self.subTest(file=name):
                self.assertEqual(got[anchors.SELECT_DET_SIDE], got[None])
                self.assertEqual(got[None][0], name in DET_SIDE_PICK)
                if name in DET_SIDE_OTHER:
                    self.assertIsNotNone(got[None][1])


if __name__ == "__main__":
    unittest.main()
