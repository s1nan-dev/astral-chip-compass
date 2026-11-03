"""按 samples/ 的全部人工标注逐张核对各识别模块：原图一遍、缩小版一遍，另核对不该被认出的反例。

每张图每个模块只跑原图和缩小版各一次（更多的变体要的时间长，不放在测试里）：
  缩小版   宽、高缩到原来的一半，但界面缩放不低于使用说明写的最小窗口（16:9 宽 1000 像素、更宽的窗口高 563 像素，
           即 pipeline.MIN_UI_SCALE）；本来就接近下限的样本只缩到下限。
  标注里没有的字段（画面上本来就看不出的）不核对；「读不出」（None）和「读错」分开对待，见各测试的说明。
  哪些画面跑哪些模块照悬浮窗的流程（见 read_sample 里的说明），悬浮窗不会在某种画面上调用的识别不跑；
  对局外画面（结算页、房间等，screens.py）的整幅识别例外，所有样本都跑，核对对局里的画面不会被认成这几种画面。
识别慢（单进程约 4~5 分钟），setUpClass 里把样本分给几个子进程一次读完，各测试只核对缓存的结果。
"""
from __future__ import annotations

import json
import math
import multiprocessing
import os
import re
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from advisor.paths import assets_dir
from advisor.vision.chip_select import ui_scale
from advisor.vision.pipeline import MIN_UI_SCALE
from tests.support import samples

PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR
VARIANTS = ("原图", "缩小")
REF_SIZE = [3840, 2054]  # labels.json 里没写 size 的样本都是这个尺寸（见 _说明）
# 子进程数（逻辑核数的一半，最多 8 个）。子进程里文字识别只用 1 个线程（见 _db）：按悬浮窗的 4 个线程时
# CPU 时间是墙钟时间的 3 倍，几个进程并行时 CPU 先跑满。实测 16 线程的 CPU 上 8 个子进程 × 1 线程约 75 秒，
# 4 个子进程 × 4 线程约 85 秒；两种设置下 72 张样本的读数完全相同
WORKERS = max(1, min(8, (os.cpu_count() or 1) // 2))

SELECT_SCENES = {"chip_select", "chip_select_selected"}
# 三选一、筹码商店提示、战斗画面压暗或盖住了左上角：轮次读不出不算错，读错算错（eval_hud 的口径）
DIMMED = {"chip_select", "chip_select_selected", "chipshop_prompt", "battle"}
# 对局外的画面：左上角没有轮次。读到轮次的话，悬浮窗会当成对局画面，不再判断是不是选角界面
OUT_OF_GAME = {"lobby", "select_screen", "skin_select", "loading", "result", "reward", "menu"}
# 左下角的立绘被挡住或不在画面上：认不出才对（eval_hero 的口径；开着「查看筹码」面板的按 panel_labels 另算）
HERO_HIDDEN = {"battle", "chips_panel", "select_screen"}
# 对局里看得见左下角立绘的画面：要认出标注的角色。其余标了角色的画面（卡牌商店、玩家详情弹窗、转账等）认不出可以，认错不行
HERO_SHOWN = {"hud", "chip_select", "chip_select_selected", "task_panel", "chipshop_prompt"}
# 反例必须覆盖的画面（都不是三选一）
NEGATIVE_SCENES = {"lobby", "select_screen", "skin_select", "loading", "result", "reward", "menu", "card_shop",
                   "player_detail", "coin_transfer", "card_give", "hud"}
# 对局外画面（screens.py）：组队房间开始匹配后选项整块变灰，勾选的难度读不出（None）可以，读错不行
SCREEN_GREYED = {
    "20261002_211547_lobby_room_matching_1600x855.jpg",
    "20261003_124444_lobby_room_matching_2560x1369.jpg",
}
# 进度上限已知的三局（hud.py 进度条失败线的说明：梦想号困难 20、园林中庭普通 18、异变图书馆噩梦 17），按文件名里的拍摄时间分局
GAME_CAPS = (("140000", "160000", 20), ("190000", "200000", 18), ("210000", "220000", 17))

GARDEN_LEVELUP = "20261002_191851_chipselect_levelup_star1_normal_2397x1798.jpg"
GARDEN_PANEL = "20261002_194045_chips_panel_six_chips_2397x1798.jpg"
TRANSFER = "20261002_214211_coin_transfer_real.jpg"
PLAYER_DETAIL = "20261002_212105_player_detail_popup_1600x855.jpg"
PANEL_DURING_SELECT = "20261002_145915_chipselect_task_tier3_no_reroll_button_2560x1057.jpg"
# 疑似缺陷：（文件, 变体, 模块）→ 单独锁定它的测试（标 expectedFailure）。矩阵里这几项跳过，免得重复报。现在没有
SUSPECTED = {
}
# 玩家列表里允许「读不清」（None）的行：文件 → 行号。只许读不出，不许读错。
# 转账画面整屏盖着斜条纹遮罩，第一行（0 星）第三颗灰星内部的饱和度约 30，正好压在「灰色」的上限（stars.SAT_EMPTY）上，
# 按 stars._count_stars 的约定算分不清（同一画面另两行的灰星是 7 和 20）
ROWS_UNCLEAR = {TRANSFER: (0,)}


def load(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(SAMPLES / name), dtype=np.uint8), cv2.IMREAD_COLOR)


def shrink(img: np.ndarray) -> np.ndarray | None:
    """缩小版：宽、高缩到一半，但界面缩放不低于最小窗口（MIN_UI_SCALE）。已经在下限上、缩不了的返回 None。"""
    h, w = img.shape[:2]
    f = max(0.5, MIN_UI_SCALE / ui_scale(w, h))
    if f >= 1:
        return None
    size = (math.ceil(w * f - 1e-6), math.ceil(h * f - 1e-6))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def load_items() -> list[dict]:
    """labels.json 的每张样本，带上 stars_labels.json（stars）和 panel_labels.json（panel）里同一张图的标注。"""
    def read(name):
        return json.loads((SAMPLES / name).read_text(encoding="utf-8"))["samples"]

    stars = {s["file"]: s for s in read("stars_labels.json")}
    panel = {s["file"]: s for s in read("panel_labels.json")}
    return [dict(item, stars=stars.get(item["file"]), panel=panel.get(item["file"])) for item in read("labels.json")]


# ------------------------------------------------------------------ 每张样本要核对什么（子进程和测试共用）


def hud_mode(item: dict) -> bool | None:
    """读不读左上角（read_hud）：None 不读；False 只读轮次；True 连任务栏一起读（核对地图）。

    三选一画面上悬浮窗只读轮次（任务栏这时看不到），这里也一样；标注了地图的其他画面连任务栏一起读，核对不误认地图。
    """
    if "round" not in item and "map" not in item and item["scene"] not in OUT_OF_GAME:
        return None
    return item["scene"] not in SELECT_SCENES and ("map" in item or item["scene"] == "task_panel")


def round_covered(item: dict) -> bool:
    """标注 note 写明左上角的轮次被挡住了（怪物名牌压在上面）。"""
    return "轮次被" in item.get("note", "")


def game_cap(file: str) -> int | None:
    t = file[9:15]
    return next((cap for lo, hi, cap in GAME_CAPS if file.startswith("20261002_") and lo <= t <= hi), None)


def cap_checked(item: dict) -> bool:
    """核对进度上限：上限已知的三局里、对局中的画面（悬浮窗在对局画面和三选一上读进度条）。"""
    return game_cap(item["file"]) is not None and item["scene"] not in OUT_OF_GAME


def visible_cap(item: dict) -> int | None:
    """进度条上看得到红叉时红叉所在的格号：优先取标注的 progress_cap，没有时按 note 里写的「红叉（17）」。"""
    if item.get("progress_cap") is not None:
        return item["progress_cap"]
    m = re.search(r"红叉（(\d+)）", item.get("note", ""))
    return int(m.group(1)) if m else None


def hero_rule(item: dict) -> str | None:
    """角色识别的口径：hidden 应认不出；shown 应认出标注的角色；safe 认不出可以、认错不行；None 不核对（没标角色）。
    标注 hero_covered 的（左下角的立绘被挡住或正在动画中）按 safe。"""
    if item["scene"] in HERO_HIDDEN or (item["panel"] or {}).get("is_panel"):
        return "hidden"
    if not item.get("hero"):
        return None
    return "shown" if item["scene"] in HERO_SHOWN and not item.get("hero_covered") else "safe"


def panel_hero(item: dict) -> str | None:
    return (item["panel"] or {}).get("hero") or item.get("hero")


# ------------------------------------------------------------------ 子进程：识别一张样本（原图和缩小版）

_worker_db = None


def _db():
    global _worker_db
    if _worker_db is None:
        import logging

        from advisor.data import load_db
        from advisor.vision import chip_select

        if multiprocessing.parent_process() is not None:
            # 只在子进程里改，不影响同一次运行里别的测试：
            # 文字识别用 1 个线程（多个子进程已经并行，多线程只多烧 CPU；见 WORKERS 的说明）；
            # 建引擎时的 INFO、识别时「没检测到文字」的警告只是噪声（几个子进程一起刷屏），出错照常报
            chip_select.OCR_THREADS = 1
            logging.disable(logging.WARNING)
        _worker_db = load_db()
        chip_select._engine()
    return _worker_db


def _select(seen) -> dict | None:
    if seen is None:
        return None
    return {
        "names": list(seen.names), "rarity": list(seen.rarity), "rerolls": seen.rerolls,
        "probabilities": list(seen.probabilities) if seen.probabilities else None,
        "selected": seen.selected, "team": bool(seen.team), "problems": list(seen.problems),
    }


def _hud(hud) -> dict:
    return {"round": hud.round, "map": hud.map}


def _progress(progress) -> list:
    return [progress.current, progress.cap]


def _hero(guess) -> dict | None:
    return None if guess is None else {"hero": guess.hero, "best": guess.best, "score": round(float(guess.score), 3)}


def _stars(report) -> dict:
    r = report.result
    return {
        "own": None if r is None else r.stars, "row": None if r is None else r.row, "how": "" if r is None else r.how,
        "rows": [[row.visible, row.stars] for row in report.rows], "reason": report.reason,
    }


def _players(players, names: dict) -> dict | None:
    if players is None:
        return None
    return {
        "own_row": players.own_row,
        "rows": [{"row": p.row, "visible": p.visible, "hero": names.get(p.hero_id), "bot": p.bot} for p in players.rows],
    }


def _screen(screen) -> dict | None:
    """对局外画面的读数（screens.OutOfGame）→ 字典，和 labels.json 的 screen 字段同样写法。"""
    if screen is None:
        return None
    return {"kind": screen.kind, "difficulty": screen.difficulty, "map": screen.map, "rounds": screen.rounds}


def _panel(reading) -> dict | None:
    if reading is None:
        return None
    return {
        "is_panel": reading.is_panel, "self_tab": reading.self_tab, "owned": list(reading.owned),
        "unknown_slots": reading.unknown_slots, "tab_count": reading.tab_count, "selected_tab": reading.selected_tab,
        "self_index": reading.self_index, "self_how": reading.self_how, "problems": list(reading.problems),
        "reason": reading.reason, "selected_slot": reading.selected_slot,
    }


def read_sample(item: dict) -> dict:
    """在子进程里识别一张样本的原图和缩小版，只返回读数（普通的列表、字典）。某个模块抛异常时记下调用栈。"""
    from advisor.vision import reset_hints, stars
    from advisor.vision.chip_panel import read_chip_panel
    from advisor.vision.chip_select import looks_like_overlay_screen, read_chip_select
    from advisor.vision.hero import read_hero
    from advisor.vision.hud import is_select_screen, read_hud, read_pregame, read_progress, read_progress_cap

    db = _db()
    names = {h.id: h.name for h in db.heroes.values()}
    original = load(item["file"])
    out = {"file": item["file"], "size": [original.shape[1], original.shape[0]], "variants": {}}
    full = hud_mode(item)
    star_hero = (item["stars"] or {}).get("hero")
    for variant, img in zip(VARIANTS, (original, shrink(original))):
        if img is None:
            continue
        got: dict = {"size": [img.shape[1], img.shape[0]], "errors": {}}

        def run(key, fn):
            # 每张图都从头找，不沿用上一张的界面缩放、面板位置、皮肤和认过的格子（实际使用时窗口不变，沿用是对的；同 eval_panel）
            reset_hints()
            try:
                got[key] = fn()
            except Exception:  # noqa: BLE001  交给测试报告
                got["errors"][key] = traceback.format_exc()

        # 哪些画面跑哪些模块，照悬浮窗的流程（pipeline.FrameProcessor.process）：
        #   三选一    先过预筛再做文字识别，预筛拦下的画面不会读成三选一
        #   选角界面  三选一画面上不判断（读到三选一就不往下走）
        #   进度上限  在对局画面（读到了轮次）和三选一上读
        #   当前格    标了 progress 的样本都读（核对进度条本身的识别，不论画面）
        #   对局外画面 选角界面判断那次识别顺带读（pick_screen，下方区域）；整幅识别（screen，悬浮窗离开对局后才做）
        #             所有样本都读，核对对局里的画面一张也不会认成结算页、房间等
        run("prefilter", lambda: bool(looks_like_overlay_screen(img)))
        if item["scene"] in SELECT_SCENES or got.get("prefilter"):
            run("select", lambda: _select(read_chip_select(img, db)))
        if item["scene"] not in SELECT_SCENES:
            info: dict = {}
            run("pick", lambda: bool(is_select_screen(img, db, info)))
            got["pick_screen"] = _screen(info.get("screen"))
        run("screen", lambda: _screen(read_pregame(img, db).screen))
        run("panel", lambda: _panel(read_chip_panel(img, db, hero_key=panel_hero(item))))
        if full is not None:
            run("hud", lambda: _hud(read_hud(img, db, full=full)))
        if cap_checked(item):
            run("cap", lambda: read_progress_cap(img))
        if "progress" in item:
            run("progress", lambda: _progress(read_progress(img)))
        if hero_rule(item) is not None:
            run("hero", lambda: _hero(read_hero(img, db)))
        if item["stars"] is not None:
            # read_own_stars 就是 diagnose(...).result；用 diagnose 顺带拿到各行的读数
            run("stars", lambda: _stars(stars.diagnose(img, star_hero)))
            if item["stars"].get("players"):
                run("players", lambda: _players(stars.read_players(img, star_hero), names))
        out["variants"][variant] = got
    return out


def _cost(item: dict) -> tuple:
    """大概的耗时：4:3 窗口认角色时搜的缩放范围大，最慢，排在最前面分给子进程。"""
    w, h = item.get("size", REF_SIZE)
    return h / w > 0.7, w * h


def read_all(items: list[dict]) -> dict[str, dict]:
    order = sorted(items, key=_cost, reverse=True)
    if WORKERS <= 1:
        results = [read_sample(item) for item in order]
    else:
        with ProcessPoolExecutor(WORKERS, mp_context=multiprocessing.get_context("spawn")) as pool:
            results = list(pool.map(read_sample, order))
    return {r["file"]: r for r in results}


HAVE_SAMPLES = samples.samples_ok("labels.json", GARDEN_LEVELUP)
HAVE_ASSETS = (
    any(assets_dir("heroes").glob("UT_Hero_RolePhoto_*.png"))
    and any(assets_dir("profiles").glob("UT_Hero_ProfilePhoto_*.png"))
    and (assets_dir("chips") / "UT_Relic_50081.png").exists()
    and (assets_dir("stars") / "star_bar.png").exists()
)
SAMPLES_SKIP = samples.samples_reason("labels.json", GARDEN_LEVELUP)
ASSETS_SKIP = "没有识别用的图片（assets/）"


@unittest.skipUnless(HAVE_SAMPLES and HAVE_ASSETS, SAMPLES_SKIP if not HAVE_SAMPLES else ASSETS_SKIP)
class VisionMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = load_items()
        cls.by_file = {item["file"]: item for item in cls.items}
        cls.results = read_all(cls.items)

    # ------------------------------------------------------------ 工具

    def cases(self, where=lambda item: True):
        """逐张、逐个变体：（标注, 变体名, 这一变体的读数）。"""
        for item in self.items:
            if where(item):
                for variant, got in self.results[item["file"]]["variants"].items():
                    yield item, variant, got

    def value(self, item: dict, variant: str, got: dict, module: str):
        """某个模块的读数。疑似缺陷另有测试锁定的跳过；识别时抛了异常就让这一项失败，附上子进程里的调用栈。"""
        test = SUSPECTED.get((item["file"], variant, module))
        if test:
            self.skipTest(f"疑似缺陷，见 {test}")
        if module in got["errors"]:
            self.fail(f"{module} 识别时出错：\n{got['errors'][module]}")
        return got[module]

    def reading(self, file: str, variant: str, module: str):
        got = self.results[file]["variants"][variant]
        if module in got["errors"]:
            self.fail(f"{module} 识别时出错：\n{got['errors'][module]}")
        return got[module]

    # ------------------------------------------------------------ 样本本身

    def test_labels_cover_every_file(self):
        """三份标注引用的截图都在，stars_labels / panel_labels 里的每张图 labels.json 都有；反例画面一种不缺。"""
        files = set(self.by_file)
        for item in self.items:
            self.assertTrue((SAMPLES / item["file"]).exists(), item["file"])
        for name in ("stars_labels.json", "panel_labels.json"):
            extra = {s["file"] for s in json.loads((SAMPLES / name).read_text(encoding="utf-8"))["samples"]} - files
            self.assertEqual(extra, set(), name)
        self.assertLessEqual(NEGATIVE_SCENES, {item["scene"] for item in self.items})

    def test_sizes(self):
        """原图尺寸和标注一致；缩小版比原图小，界面缩放不低于最小窗口（使用说明：16:9 宽 1000、更宽的窗口高 563）。"""
        for item in self.items:
            result = self.results[item["file"]]
            with self.subTest(file=item["file"]):
                self.assertEqual(result["size"], item.get("size", REF_SIZE))
                self.assertEqual(list(result["variants"]), list(VARIANTS))
                w, h = result["variants"]["缩小"]["size"]
                self.assertGreaterEqual(ui_scale(w, h), MIN_UI_SCALE - 1e-9, (w, h))
                # 缩到一半；一半会低于最小窗口的，正好缩到下限（多 1 像素的取整余量）
                at_floor = ui_scale(w - 1, h - 1) < MIN_UI_SCALE
                self.assertTrue(w <= math.ceil(result["size"][0] / 2) or at_floor, (w, h))

    # ------------------------------------------------------------ 三选一（chip_select）

    def test_chip_select_matches_labels(self):
        """三选一样本：过得了预筛；筹码名、稀有度、刷新次数、概率、选中项、全队三选一那一行和标注一致，自检提示和标注的
        expect_problems 一致（没写的为没有：截图早于游戏更新、资料已照新版改的样本，自检照资料会报的那几条写在标注里）。"""
        for item, variant, got in self.cases(lambda it: it["scene"] in SELECT_SCENES):
            with self.subTest(file=item["file"], variant=variant):
                self.assertTrue(self.value(item, variant, got, "prefilter"), "预筛把三选一画面漏掉了")
                seen = self.value(item, variant, got, "select")
                self.assertIsNotNone(seen, "没认出三选一界面")
                offer = item["offer"]
                want = {
                    "names": offer,
                    "selected": offer.index(item["selected"]) if "selected" in item else None,
                    "team": bool(item.get("team")),
                    "problems": item.get("expect_problems", []),
                }
                want.update({k: item[k] for k in ("rarity", "rerolls", "probabilities") if k in item})
                self.assertEqual({k: seen[k] for k in want}, want)

    def test_other_screens_are_not_chip_select(self):
        """反例：大厅、选角、选皮肤、载入、结算、卡牌商店、玩家详情弹窗、转账、对局、战斗、面板、任务栏都不能读成三选一。
        预筛就拦下的画面悬浮窗不做文字识别；过了预筛的（战斗、玩家详情弹窗、卡牌商店上方也有黑带）要由文字识别否定。"""
        recognised = 0
        for item, variant, got in self.cases(lambda it: it["scene"] not in SELECT_SCENES):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                if not self.value(item, variant, got, "prefilter"):
                    continue
                recognised += 1
                self.assertIsNone(self.value(item, variant, got, "select"))
        self.assertGreater(recognised, 0)  # 至少有过了预筛、真正做了文字识别的反例

    # ------------------------------------------------------------ 左上角（hud）

    def test_round(self):
        """标注了轮次的样本：读对。三选一等压暗的画面、标注写明轮次被挡住的画面读不出不算错，但不能读错。"""
        for item, variant, got in self.cases(lambda it: "round" in it):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                rnd = self.value(item, variant, got, "hud")["round"]
                if rnd is None and (item["scene"] in DIMMED or round_covered(item)):
                    continue
                self.assertEqual(rnd, item["round"])

    def test_no_round_out_of_game(self):
        """对局外的画面（大厅和房间、选角、选皮肤、载入、结算、奖励页、主界面）读不到轮次：读到了会被当成对局画面，
        看不出新的一局要开始。"""
        for item, variant, got in self.cases(lambda it: it["scene"] in OUT_OF_GAME):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                self.assertIsNone(self.value(item, variant, got, "hud")["round"])

    def test_map(self):
        """任务栏开着时按任务文字认出地图；标注了地图的其他画面（对局、面板、转账等）不能误认成别的地图。"""
        for item, variant, got in self.cases(lambda it: hud_mode(it) is True):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                got_map = self.value(item, variant, got, "hud")["map"]
                if item["scene"] == "task_panel":
                    self.assertEqual(got_map, item["map"])
                else:
                    self.assertIn(got_map, (None, item["map"]))

    def test_progress_cap(self):
        """进度上限已知的三局的对局画面（含三选一）：每张要么读不出（红叉不在可见范围），要么读对；
        标注写明看得到红叉的必须读出。"""
        checked = 0
        for item, variant, got in self.cases(cap_checked):
            with self.subTest(file=item["file"], variant=variant):
                cap = self.value(item, variant, got, "cap")
                must = visible_cap(item)
                if must is not None:
                    self.assertEqual(must, game_cap(item["file"]))  # 标注自己对得上
                    self.assertEqual(cap, must)
                    checked += 1
                else:
                    self.assertIn(cap, (None, game_cap(item["file"])))
        self.assertGreaterEqual(checked, 2)  # 214049 的原图和缩小版

    def test_progress_current(self):
        """标了进度条当前格（progress）的样本：原图和缩小版都读对；标了红叉格号（progress_cap）的，红叉也读对。"""
        checked = 0
        for item, variant, got in self.cases(lambda it: "progress" in it):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                current, cap = self.value(item, variant, got, "progress")
                self.assertEqual(current, item["progress"])
                if "progress_cap" in item:
                    self.assertEqual(cap, item["progress_cap"])
                checked += 1
        self.assertGreater(checked, 0)

    def test_round_not_misread_in_small_4_3_window(self):
        """只按位置读到的两位数要两次一致才采用：4:3 窗口 1199×899（不低于最小窗口）时，照那一局第 2 轮的三选一画面左上角「02 轮」容易读成 20。

        第一遍只读出「2」（不是两位数，不采用），放大 1.5 倍重读时读成「20」，按「左上角单独的两位数」兜底就会采用；
        缩到 0.75 倍其实读得出「02轮」。1439×1079 的 4:3 窗口也一样。三选一画面静止不动，每次都读成 20，
        轮次往前大幅跳变连续读到两次就会被会话采用。应为 2，或读不出（None）。
        """
        self.assertIn(self.reading(GARDEN_LEVELUP, "缩小", "hud")["round"], (None, 2))

    def test_round_read_in_small_4_3_window(self):
        """菜单按钮边缘读成的「3.」前缀也要去掉：4:3 窗口 1199×899 时，照那一局第 13 轮开着「查看筹码」面板的画面，左上角清楚的「13 轮」容易读不出。

        「13」连同左边菜单按钮的边缘读成「3.13」（只去掉连带读成的「1」不够），
        放大 1.5 倍重读只剩「1」、缩小 0.75 倍重读仍是「3.13」。原图和 1280×961 都读得出 13。
        连任务栏一起读（full=True）时靠整块文字兜底能读到；地图已知后悬浮窗只读轮次（full=False），不去掉前缀这一帧就读不到。
        """
        self.assertEqual(self.reading(GARDEN_PANEL, "缩小", "hud")["round"], 13)

    # ------------------------------------------------------------ 选角界面（hud.is_select_screen）

    def test_select_screen(self):
        """只有选角界面判成选角界面；其余画面（含大厅、选皮肤、载入、结算）判成是会被当成新的一局。
        三选一画面上悬浮窗不判断选角界面，不核对。"""
        for item, variant, got in self.cases(lambda it: it["scene"] not in SELECT_SCENES):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                self.assertEqual(self.value(item, variant, got, "pick"), item["scene"] == "select_screen")

    # ------------------------------------------------------------ 对局外画面（screens.parse_out_of_game）

    def test_out_of_game_screens(self):
        """整幅识别：标了 screen 的样本读数和标注完全相同（变灰的组队房间难度可以读不出）；
        没标 screen 的样本（对局里的各种画面、选角、载入等）一张也不能认成结算页、匹配成功页、房间或奖励页。"""
        for item, variant, got in self.cases():
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                seen = self.value(item, variant, got, "screen")
                want = item.get("screen")
                if item["file"] in SCREEN_GREYED and seen is not None and seen["difficulty"] is None:
                    seen = dict(seen, difficulty=want["difficulty"])
                self.assertEqual(seen, want)

    def test_out_of_game_screens_bottom(self):
        """选角界面判断的那次识别（只看下方区域）顺带读到的画面：结算页读全；其余画面读不出可以，读到的项不能和标注不同；
        没标 screen 的样本读不出。"""
        for item, variant, got in self.cases(lambda it: it["scene"] not in SELECT_SCENES):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                self.value(item, variant, got, "pick")
                seen, want = got["pick_screen"], item.get("screen")
                if want is not None and want["kind"] == "result":
                    self.assertEqual(seen, want)
                elif seen is not None:
                    self.assertIsNotNone(want, seen)
                    self.assertEqual(seen["kind"], want["kind"])
                    for key, value in seen.items():
                        self.assertIn(value, (None, want[key]), key)

    # ------------------------------------------------------------ 角色（hero）

    def test_hero(self):
        """对局里看得见立绘的画面认出标注的角色；立绘被挡住（面板、战斗、选角界面）时认不出；其余画面不能认错。"""
        for item, variant, got in self.cases(lambda it: hero_rule(it) is not None):
            rule = hero_rule(item)
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"], rule=rule):
                guess = self.value(item, variant, got, "hero")
                hero = None if guess is None else guess["hero"]
                if rule == "hidden":
                    self.assertIsNone(hero, guess)
                elif rule == "shown":
                    self.assertEqual(hero, item["hero"], guess)
                else:
                    self.assertIn(hero, (None, item["hero"]), guess)

    # ------------------------------------------------------------ 玩家列表（stars）

    def test_own_stars(self):
        """自己的星数（read_own_stars）：画面上读得到的读对，按头像认出的行和标注一致；
        自己那一行被挡住、没有玩家列表、或列表里没有自己（转账画面）时返回 None，不猜。"""
        for item, variant, got in self.cases(lambda it: it["stars"] is not None):
            label = item["stars"]
            with self.subTest(file=item["file"], variant=variant, hero=label["hero"]):
                reading = self.value(item, variant, got, "stars")
                self.assertEqual(reading["own"], label["own"], reading["reason"])
                if reading["row"] is not None:
                    self.assertEqual(reading["row"], label["own_row"])

    def test_star_rows(self):
        """玩家列表每一行亮着几颗星：标注了数的行要看得见、读对；标注之外多出来的行只能是按行距推算的（看不见）。"""
        for item, variant, got in self.cases(lambda it: it["stars"] is not None):
            want = item["stars"]["rows"]
            unclear = ROWS_UNCLEAR.get(item["file"], ())
            with self.subTest(file=item["file"], variant=variant):
                rows = self.value(item, variant, got, "stars")["rows"]
                seen = [stars if visible else "挡" for visible, stars in rows]
                for i, n in enumerate(want):
                    if n is None:
                        continue
                    self.assertLess(i, len(rows), f"第 {i + 1} 行没找到：{seen}，应为 {want}")
                    if i in unclear and seen[i] is None:
                        continue
                    self.assertEqual(seen[i], n, f"第 {i + 1} 行：{seen}，应为 {want}")
                for i in range(len(want), len(rows)):
                    self.assertFalse(rows[i][0], f"多出看得见的第 {i + 1} 行：{seen}，应为 {want}")

    def test_players(self):
        """队友（read_players）：认出的角色不能错，读到名字时「是否电脑」不能错；自己那一行看得见时要认出是哪一行。"""
        for item, variant, got in self.cases(lambda it: it["stars"] is not None and it["stars"].get("players")):
            label = item["stars"]
            with self.subTest(file=item["file"], variant=variant):
                players = self.value(item, variant, got, "players")
                self.assertIsNotNone(players, "没找到玩家列表")
                if label["own"] is not None:
                    self.assertEqual(players["own_row"], label["own_row"])
                else:
                    self.assertIn(players["own_row"], (None, label["own_row"]))
                for p in players["rows"]:
                    if not p["visible"] or p["row"] == label["own_row"] or p["row"] >= len(label["players"]):
                        continue
                    self.assertIn(p["hero"], (None, label["players"][p["row"]]), f"第 {p['row'] + 1} 行")
                    self.assertIn(p["bot"], (None, label["bots"][p["row"]]), f"第 {p['row'] + 1} 行")

    def test_transfer_screen_has_no_own_stars(self):
        """反例：选转账对象的画面上玩家列表去掉了自己那一行、整体上移，读不出自己的星数（不能把队友当成自己）。"""
        self.assertIsNone(self.by_file[TRANSFER]["stars"]["own"])
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                self.assertIsNone(self.reading(TRANSFER, variant, "stars")["own"])

    # ------------------------------------------------------------ 「查看筹码」面板（chip_panel）

    def test_panel(self):
        """面板样本：筹码、页签个数、选中的页签、点选的格子（外圈为黄绿色）和标注一致，没有无法识别的格子和识别提示（截图
        早于游戏更新、资料已照新版改的样本，筹码、认不出的格数、识别提示按标注的 expect_read）；给出角色时认出自己的页签
        （玩家列表没读全时按设计判为「无法确定」，不能判错）。"""
        for item, variant, got in self.cases(lambda it: (it["panel"] or {}).get("is_panel")):
            label = item["panel"]
            with self.subTest(file=item["file"], variant=variant):
                reading = self.value(item, variant, got, "panel")
                self.assertTrue(reading["is_panel"], reading["reason"])
                want = {k: label[k] for k in ("owned", "tab_count", "selected_tab")}
                want.update(unknown_slots=0, problems=[], selected_slot=label.get("selected_slot"))
                want.update(label.get("expect_read", {}))
                self.assertEqual({k: reading[k] for k in want}, want)
                if reading["self_index"] is None and item["file"] == PANEL_DURING_SELECT:
                    # 三选一的标题带盖住了玩家列表第一行：列表没读全时不按行号认页签（chip_panel._list_owner），
                    # 悬浮窗那时用本局已确认的行（self_row）判断
                    self.assertFalse(reading["self_tab"])
                    self.assertIn("玩家列表未完整识别", reading["reason"])
                    continue
                self.assertEqual(reading["self_index"], label["self_index"], reading["reason"])
                self.assertEqual(reading["self_tab"], label["self_index"] == label["selected_tab"], reading["reason"])

    def test_other_screens_are_not_panel(self):
        """反例：其余样本都不是「查看筹码」面板（含玩家详情弹窗里 3-4-3 排列的筹码格、被战斗打断的画面）。"""
        for item, variant, got in self.cases(lambda it: not (it["panel"] or {}).get("is_panel")):
            with self.subTest(file=item["file"], variant=variant, scene=item["scene"]):
                reading = self.value(item, variant, got, "panel")
                self.assertIsNotNone(reading)
                self.assertEqual((reading["is_panel"], reading["self_tab"], reading["owned"]), (False, False, []))

    def test_player_detail_is_not_panel(self):
        """玩家详情弹窗（队友的属性、增益和 10 个筹码格）不是「查看筹码」面板：读进来会把队友的筹码当成自己的。"""
        self.assertEqual(self.by_file[PLAYER_DETAIL]["scene"], "player_detail")
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                self.assertFalse(self.reading(PLAYER_DETAIL, variant, "panel")["is_panel"])


if __name__ == "__main__":
    unittest.main()
