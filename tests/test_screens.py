"""对局外画面的识别（advisor/vision/screens.py）：按 samples/labels.json 的 screen 标注逐张核对原图和缩小版
（缩小版同 test_vision_matrix.shrink：宽、高缩到一半，但不低于最小窗口）。

  整幅识别  hud.read_pregame（离开对局后找加载画面地图名的那次识别，顺带读画面种类）：标了 screen 的样本读数和标注
            完全相同。组队房间开始匹配后整块变灰的两张，难度读不出也可以，不能读错
  下方区域  hud.is_select_screen(img, db, info)（读不到轮次时判断选角界面的那次识别）：结算页要读全；
            其余画面要么读不出，要么读到的项和标注相同，不能读错
  反例      对局、三选一、面板、任务栏、选角、载入、卡牌商店等各挑一两张，两种识别都读不出
  解析本身  用构造的文字识别结果核对几条规则（不读样本，很快）
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

from advisor.data import DIFFICULTY_NAMES, load_db
from advisor.vision import hud, io, screens
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
RESULT_SCENES = {"result": {screens.RESULT}, "reward": {screens.REWARD}, "menu": set(),
                 "lobby": {screens.MATCH_FOUND, screens.TEAM_ROOM, screens.CREATE_ROOM, screens.CUSTOM_ROOM}}


def _as_dict(screen: OutOfGame | None) -> dict | None:
    return None if screen is None else dataclasses.asdict(screen)


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
                self.assertEqual(set(screen), {f.name for f in dataclasses.fields(OutOfGame)})
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
                self.assertEqual(full, want)
                if want is not None:
                    self.assertIsNone(got["map"])  # 房间设置里的「游玩地图」、结算页的地图都不是加载画面的地图名

    def test_bottom_reading(self):
        """下方区域：结算页读全；其余画面读不出可以，读到的项不能和标注不同。"""
        for file, variant, got in self.cases(self.items):
            want = self.items[file]["screen"]
            with self.subTest(file=file, variant=variant):
                bottom = got["bottom"]
                self.assertFalse(got["pick"])
                if want is not None and want["kind"] == screens.RESULT:
                    self.assertEqual(bottom, want)
                elif bottom is not None:
                    self.assertIsNotNone(want, bottom)
                    self.assertEqual(bottom["kind"], want["kind"])
                    for key, value in bottom.items():
                        self.assertIn(value, (None, want[key]), key)

    def test_team_room_difficulty(self):
        """组队房间：没变灰的三张整幅识别读出勾选的难度（普通 1 张、噩梦 2 张）。"""
        read = {}
        for file, variant, got in self.cases(f for f in self.items if f not in GREYED):
            if self.items[file]["screen"] and self.items[file]["screen"]["kind"] == screens.TEAM_ROOM:
                read[(file, variant)] = got["full"]["difficulty"]
        self.assertEqual(sorted(read.values()), ["噩梦"] * 4 + ["普通"] * 2)

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

    def test_pregame_default_screen(self):
        """Pregame 的新字段有默认值：现有的两参数写法照常可用。"""
        self.assertIsNone(hud.Pregame(None, ["狂风"]).screen)


if __name__ == "__main__":
    unittest.main()
