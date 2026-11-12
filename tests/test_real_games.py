"""真人对局里发现的问题：全队三选一、刷新按钮被挡、进度上限、新一局判定的依据；
以及 10-03 真人对局复盘发现的识别问题：轮次和进度条（名牌下方的血量、检测漏掉的数字、第 1 轮的骷髅格、三选一画面上的
进度条），三选一的概率行读错。"""
import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from advisor.data import load_db
from advisor.engine import Scorer
from advisor.pool import State
from advisor.session import Session, Settings
from tests.support import samples

PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = samples.SAMPLES_DIR


def frame(names, rerolls=2, probabilities=(60, 37, 3), rarity=None, team=False, selected=None):
    f = SimpleNamespace(
        names=list(names), raw=list(names), rerolls=rerolls, probabilities=probabilities,
        selected=selected, problems=[], team=team,
    )
    if rarity is not None:
        f.rarity = list(rarity)
    return f


def own(stars, confidence=0.9):
    return SimpleNamespace(stars=stars, confidence=confidence)


def hud(round=None, map=None):
    return SimpleNamespace(round=round, map=map)


def ready(db, hero="芬妮") -> Session:
    s = Session(db, Settings(hero=hero))
    s.game.seen_hero = hero
    return s


def load(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def sample(pattern: str) -> Path:
    """samples 里和 pattern 对得上的第一张截图（同 next(SAMPLES.glob(pattern))）。没有时跳过这一条：截图不随仓库发布，
    同一类里不用截图的几条照跑。"""
    found = next(SAMPLES.glob(pattern), None)
    if found is None:
        raise unittest.SkipTest(samples.samples_reason(pattern))
    return found


class TeamSelectTest(unittest.TestCase):
    """标题上方「……正在选择强化筹码…」时为全队同时三选一，一定按任务来源算。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_banner_overrides_levelup_guess(self):
        # 真人局里看到的：2 星时概率行 40/45/15、最左格紫色，和 2 星升星完全一样，只有标题上方那一行能分辨
        s = self.session
        s.on_stars(own(2))
        offer = ["手持风扇-大", "手电筒-强光", "磨刀石"]
        ev = s.on_frame(frame(offer, probabilities=(40, 45, 15), rarity=["紫", "紫", "紫"]))
        self.assertEqual((ev.state.source, ev.state.tier), ("levelup", None))
        s.on_frame(None), s.on_frame(None)
        ev = s.on_frame(frame(offer, probabilities=(40, 45, 15), rarity=["紫", "紫", "紫"], team=True, rerolls=1))
        self.assertEqual((ev.state.star, ev.state.source, ev.state.tier), (2, "task", 2))
        self.assertEqual(s.settings.star, 2)  # 星数不变

    def test_banner_sticks_through_rerolls(self):
        """真人局（第 9 轮）：其他玩家选完后这一行就消失了，刷新后的候选也还是同一次任务三选一。"""
        s = self.session
        s.on_stars(own(2))
        purple = ["紫", "紫", "紫"]
        ev = s.on_frame(frame(["能量回收", "智能手表", "普通瞄具"], probabilities=(40, 45, 15), rarity=purple,
                              team=True, rerolls=3))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 2))
        ev = s.on_frame(frame(["放大镜", "大背包", "电击手套"], probabilities=(40, 45, 15), rarity=purple, rerolls=2))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 2))
        ev = s.on_frame(frame(["银行卡-余额多", "速度轮滑-初级", "磨刀石"], probabilities=None, rarity=purple, rerolls=1))
        self.assertEqual(ev.state.source, "task")
        # 界面关了：下一次三选一没有这一行，照最左格的规则（2 星、最左格紫色 → 升星）
        s.on_frame(None), s.on_frame(None)
        ev = s.on_frame(frame(["手电筒-强光", "电流剑", "忍术飞镖"], probabilities=(40, 45, 15), rarity=purple))
        self.assertEqual(ev.state.source, "levelup")

    def test_new_probabilities_end_team_select(self):
        """界面没来得及关就换了一次三选一（概率行变了）：不再沿用。"""
        s = self.session
        s.on_stars(own(2))
        s.on_frame(frame(["能量回收", "智能手表", "普通瞄具"], probabilities=(40, 45, 15), rarity=["紫"] * 3, team=True))
        ev = s.on_frame(frame(["速度轮滑-高级", "鹰眼瞄具", "书签"], probabilities=(25, 50, 25), rarity=["金", "金", "蓝"]))
        self.assertEqual((ev.state.star, ev.state.source), (3, "levelup"))  # 刚升到 3 星、最左格金色

    def test_back_to_back_selects(self):
        """两个任务接连完成，两次三选一之间没有棋盘画面（真人对局第 9 轮 944 → 946）：概率行换了，先结算上一次。"""
        s = self.session
        s.on_stars(own(2))
        first = ["银行卡-余额多", "速度轮滑-初级", "磨刀石"]
        s.on_frame(frame(first, probabilities=(40, 45, 15), rarity=["紫", "蓝", "紫"], rerolls=1, selected=0))
        ev = s.on_frame(frame(["医疗箱-紧急治疗", "鹰眼瞄具", "速度轮滑-初级"], probabilities=(25, 50, 25),
                              rarity=["蓝", "金", "蓝"], rerolls=1, team=True))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 3))
        closed = [e for e in s.take_events() if e.kind in ("picked", "missed_pick")]
        self.assertEqual([(e.kind, e.chip) for e in closed], [("picked", "银行卡-余额多")])
        self.assertEqual(s.settings.owned, ["银行卡-余额多"])

    def test_back_to_back_same_row(self):
        """2 级任务的三选一选完，紧接着升到 2 星又出一次（两次都是概率表第 2 行）：三张全换、刷新次数没少（升星
        还多了 1 次），是另一次，先结算上一次，第二次按刚升星算。"""
        s = self.session
        s.on_stars(own(1))
        first = ["银行卡-余额多", "速度轮滑-初级", "磨刀石"]
        ev = s.on_frame(frame(first, probabilities=(40, 45, 15), rarity=["蓝", "蓝", "紫"], rerolls=3, selected=0))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 2))
        ev = s.on_frame(frame(["手电筒-强光", "鹰眼瞄具", "大背包"], probabilities=(40, 45, 15),
                              rarity=["紫", "金", "蓝"], rerolls=4))
        self.assertEqual(ev.kind, "advice")
        self.assertEqual((ev.state.star, ev.state.source), (2, "levelup"))
        closed = [e for e in s.take_events() if e.kind in ("picked", "missed_pick")]
        self.assertEqual([(e.kind, e.chip) for e in closed], [("picked", "银行卡-余额多")])
        self.assertEqual(s.settings.owned, ["银行卡-余额多"])

    def test_back_to_back_without_green_box(self):
        s = self.session
        s.on_stars(own(2))
        s.on_frame(frame(["银行卡-余额多", "速度轮滑-初级", "磨刀石"], probabilities=(40, 45, 15), rerolls=1))
        s.on_frame(frame(["医疗箱-紧急治疗", "鹰眼瞄具", "速度轮滑-初级"], probabilities=(25, 50, 25), rerolls=1))
        closed = [e for e in s.take_events() if e.kind in ("picked", "missed_pick")]
        self.assertEqual([e.kind for e in closed], ["missed_pick"])
        self.assertEqual(s.game.missed_offer, ["银行卡-余额多", "速度轮滑-初级", "磨刀石"])

    def test_reroll_is_not_a_new_select(self):
        """刷新只换候选、不换概率行；概率行读错（不是表里的行）也不算换了一次三选一。"""
        s = self.session
        s.on_frame(frame(["能量回收", "智能手表", "普通瞄具"], probabilities=(40, 45, 15), rerolls=3, selected=1))
        s.on_frame(frame(["放大镜", "大背包", "电击手套"], probabilities=(40, 45, 15), rerolls=2))
        s.on_frame(frame(["银行卡-余额多", "速度轮滑-初级", "磨刀石"], probabilities=(40, 46, 15), rerolls=1))
        self.assertEqual([e for e in s.take_events() if e.kind in ("picked", "missed_pick")], [])
        self.assertEqual(s.settings.owned, [])

    def test_banner_without_probabilities(self):
        s = self.session
        s.on_stars(own(1))
        ev = s.on_frame(frame(["电流核心", "紫色飞星", "优雅之羽"], probabilities=None, team=True))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", None))

    def test_offtable_row_keeps_team_select(self):
        """10-03 实机（第 3 轮）：有横幅的那一帧把概率行读错（不是概率表里的行），下一帧横幅已消失、概率行读对：
        仍是同一次全队三选一，按任务来源算（读错的行不当成换了一次三选一）。"""
        s = self.session
        offer, rarity = ["小猪存钱罐", "手电筒-强光", "ATM机"], ["蓝", "紫", "蓝"]
        s.on_frame(frame(offer, probabilities=(60, 37, 13), rarity=rarity, team=True, rerolls=3))
        ev = s.on_frame(frame(offer, probabilities=(60, 37, 3), rarity=rarity, rerolls=3))
        self.assertEqual((ev.state.source, ev.state.tier), ("task", 1))
        # 概率表里的行照常用来分辨另一次三选一
        s.on_frame(None), s.on_frame(None)
        ev = s.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"], probabilities=(60, 37, 3), rarity=["蓝", "紫", "紫"]))
        self.assertEqual(ev.state.source, "levelup")

    def test_judge_source_direct(self):
        s = self.session
        s.on_stars(own(3))
        self.assertEqual(s.judge_source((25, 50, 25), ["大背包"], "金", team=True), (3, "task", 3))
        self.assertEqual(s.judge_source((25, 50, 25), ["大背包"], "金"), (3, "levelup", None))


class TeamBannerVisionTest(unittest.TestCase):
    """全队任务三选一：识别标题上方的那一行。任务样本都有、其余三选一样本都没有。"""

    @classmethod
    def setUpClass(cls):
        from advisor.vision import chip_select

        cls.db = load_db()
        cls.read = staticmethod(chip_select.read_chip_select)

    def selects(self, team: bool) -> list[Path]:
        samples.require_samples("labels.json")  # 没有样本标注时只跳过用样本的这两条
        labels = json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))
        return [
            SAMPLES / s["file"] for s in labels["samples"]
            if s.get("scene", "").startswith("chip_select") and bool(s.get("team")) == team
        ]

    def test_team_samples_have_banner(self):
        files = self.selects(True)
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            img = load(path)
            for scale in (1.0, 0.5):
                im = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                seen = self.read(im, self.db)
                with self.subTest(file=path.name, scale=scale):
                    self.assertIsNotNone(seen)
                    self.assertTrue(seen.team)

    def test_other_samples_have_no_banner(self):
        # 只有自己在选的任务三选一（144715、192342）也没有这一行：照最左格的规则判断来源
        files = self.selects(False)
        self.assertTrue(files)
        for path in files:
            seen = self.read(load(path), self.db)
            if seen is None:
                continue
            with self.subTest(file=path.name):
                self.assertFalse(seen.team)

    def test_banner_line_rule(self):
        from advisor.vision.chip_select import Line, _team_banner

        title = Line("请选择一枚战斗筹码", 1000, 190, 450, 40)
        unit = 476
        above = Line("玩家丁正在选择强化筹码…", 1100, 137, 300, 14)
        self.assertTrue(_team_banner([title, above], title, unit))
        # 名字里碰巧有「强化」但不在标题上方：不算
        far = Line("正在选择强化筹码", 1100, 900, 300, 14)
        self.assertFalse(_team_banner([title, far], title, unit))
        # 玩家名单独一段、后半句读丢了：不算
        names_only = Line("Player*B、玩家丙", 900, 137, 300, 14)
        self.assertFalse(_team_banner([title, names_only], title, unit))


class ProbabilityLineTest(unittest.TestCase):
    """三选一的概率行：三项之和不是 100 的读数按读不到处理（10-03 实机：界面刚出现时把「/」读成数字，60%/37%/3% 读成
    60%/37%13%，刷新的估值跟着算错）。"""

    @staticmethod
    def line(text: str, cy: float = 200.0):
        from advisor.vision.chip_select import Line

        return Line(text, 300.0, cy, 400.0, 30.0)

    def test_sum_must_be_100(self):
        from advisor.vision.chip_select import _probabilities

        line = self.line
        self.assertEqual(_probabilities([line("筹码获取概率：60%/37%/3%")], 500), (60, 37, 3))
        for text in ("筹码获取概率：60%/37%13%", "60%/737%/3%", "40%/45%/115%"):
            with self.subTest(text=text):
                self.assertIsNone(_probabilities([line(text)], 500))
        # 一行读错、一行读对：取读对的那一行（不论先后）
        self.assertEqual(_probabilities([line("60%/37%13%"), line("60%/37%/3%", 210)], 500), (60, 37, 3))
        self.assertEqual(_probabilities([line("60%/37%/3%"), line("60%/37%13%", 210)], 500), (60, 37, 3))
        # 筹码名那一行（below_y）下方的文字不算
        self.assertIsNone(_probabilities([line("40%/45%/15%", 600)], 500))
        self.assertIsNone(_probabilities([], 500))

    def test_samples(self):
        """10-03 实机的任务三选一（含图标还没加载的出场动画帧）：概率行照常读出。"""
        from advisor.vision.chip_select import read_chip_select

        db = load_db()
        for key, probs in (("20261003_130848", (40, 45, 15)), ("20261003_130850", (40, 45, 15)),
                           ("20261003_131934", (25, 50, 25))):
            seen = read_chip_select(load(sample(f"{key}_*")), db)
            with self.subTest(file=key):
                self.assertEqual(seen.probabilities, probs)


class OfferRerollsTest(unittest.TestCase):
    """同一组候选时刷新按钮突然找不到（被表情气泡挡住等），沿用刚才读到的次数。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)
        self.offer = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]

    def test_same_offer_keeps_count(self):
        s = self.session
        ev = s.on_frame(frame(self.offer, rerolls=3))
        self.assertEqual(ev.state.rerolls, 3)
        self.assertIsNone(s.on_frame(frame(self.offer, rerolls=0)))  # 推荐不变：不重新给
        ev = s.on_frame(frame(self.offer, rerolls=0, selected=1))
        self.assertTrue(ev is None or ev.state.rerolls == 3)
        self.assertEqual(s.build_state(frame(self.offer, rerolls=3)).rerolls, 3)

    def test_new_offer_reads_again(self):
        s = self.session
        s.on_frame(frame(self.offer, rerolls=1))
        ev = s.on_frame(frame(["会员推荐信", "手电筒-强光", "优雅之羽"], rerolls=0))  # 用掉最后一次
        self.assertEqual(ev.state.rerolls, 0)

    def test_count_drops_by_reroll_only(self):
        s = self.session
        s.on_frame(frame(self.offer, rerolls=3))
        ev = s.on_frame(frame(self.offer, rerolls=2))  # 同一组候选读到更少的次数（不是 0）：照读到的
        self.assertEqual(ev.state.rerolls, 2)

    def test_reopened_offer_starts_fresh(self):
        s = self.session
        s.on_frame(frame(self.offer, rerolls=3))
        s.on_frame(None), s.on_frame(None)
        ev = s.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"], rerolls=0))  # 次数用完时没有刷新按钮
        self.assertEqual(ev.state.rerolls, 0)


class ProgressCapTest(unittest.TestCase):
    """进度条上红叉所在的格为进度上限，连续两次读到同一个值才采用，按局记住。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_two_equal_reads(self):
        s = self.session
        self.assertTrue(s.wants_progress_cap)
        self.assertIsNone(s.on_progress_cap(17))
        self.assertIsNone(s.on_progress_cap(18))  # 不一样：重新数
        self.assertIsNone(s.on_progress_cap(None))  # 读不到不影响
        self.assertEqual(s.on_progress_cap(18).kind, "status")
        self.assertEqual(s.game.progress_cap, 18)
        self.assertFalse(s.wants_progress_cap)
        self.assertIsNone(s.on_progress_cap(17))  # 定下后不再改
        self.assertEqual(s.build_state(frame(["书签", "标记喷罐", "鹰眼瞄具"])).progress_cap, 18)

    def test_reset_on_new_game_and_undo(self):
        s = self.session
        s.on_hud(hud(round=7))
        s.on_hud(hud(round=7))
        s.on_progress_cap(17), s.on_progress_cap(17)
        s.on_hud(hud(round=1))
        ev = s.on_hud(hud(round=1))
        self.assertEqual((ev.kind, ev.cause), ("newgame", "round"))
        self.assertIsNone(s.game.progress_cap)
        s.undo_new_game()
        self.assertEqual(s.game.progress_cap, 17)

    def test_engine_uses_cap(self):
        db = self.db
        base = State(hero="芬妮", star=1, round=10, map="异变图书馆")
        capped = State(hero="芬妮", star=1, round=10, map="异变图书馆", progress_cap=12)
        self.assertEqual(Scorer(db, capped).cap, 12)
        self.assertNotEqual(Scorer(db, base).cap, 12)

    def test_map_note_names_source(self):
        from advisor.engine import advise

        offer = ["书签", "标记喷罐", "鹰眼瞄具"]
        adv = advise(self.db, State(hero="芬妮", star=1, round=6, progress_cap=17), offer)
        self.assertIn("地图：待识别，按候选地图平均计算（进度上限 17，读自进度条）", adv.notes)
        adv = advise(self.db, State(hero="芬妮", star=1, round=6), offer)
        self.assertTrue(any("进度上限取常见值" in n for n in adv.notes))

    def test_reads_sample(self):
        from advisor.vision.hud import read_progress_cap

        path = sample("*193639_hud_round11*")
        img = load(path)
        self.assertEqual(read_progress_cap(img), 18)
        half = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        self.assertEqual(read_progress_cap(half), 18)
        # 进度上限已知的三局里，每张样本要么读不出（没有红叉、画面变暗读不清等），要么读对，不能读错
        caps = {"2026100214": 20, "2026100215": 20, "2026100219": 18, "2026100221": 17}
        seen = []
        for path in sorted(SAMPLES.glob("20261002_*")):
            cap = caps.get(path.name[:8] + path.name[9:11])
            if cap is None or path.suffix not in (".jpg", ".png"):
                continue
            got = read_progress_cap(load(path))
            with self.subTest(file=path.name):
                self.assertIn(got, (None, cap))
            seen.append(got)
        self.assertGreaterEqual(sum(v is not None for v in seen), 3)


class ProgressCurrentTest(unittest.TestCase):
    """进度条上当前所在的格：一局在进度到达红叉时结束，进度比轮次走得快（真人对局第 9 轮已到第 14 格），剩余轮数按它算。"""

    # 样本（文件名开头的日期和时间）→（当前格, 红叉）；红叉不在可见范围内的为 None
    TRUTH = {
        "20261002_145448": (9, None), "20261002_193639": (13, 18), "20261002_194039": (17, 18),
        "20261002_213757": (11, 17), "20261002_213811": (12, 17), "20261002_214219": (13, 17),
        "20261002_214923": (14, 17), "20261002_214927": (13, 17), "20261002_215027": (14, 17),
        # 第 1 轮：当前格是骷髅格，蓝底被图标隔成两半、紧挨左端的「任务」按钮（hud.PROGRESS_STRIP_H 一段的说明）
        "20261002_012918": (1, None), "20261002_015508": (1, None), "20261002_211713": (1, None),
        "20261003_124642": (1, None),
        "20261003_125350": (3, None), "20261003_130926": (8, None), "20261003_131958": (11, 17),
        # 三选一画面上的进度条（悬浮窗在三选一上也读）；131934 是任务奖励「进度 -1」生效后、由第 11 格退回的第 10 格
        "20261003_130848": (7, None), "20261003_130850": (7, None), "20261003_131934": (10, None),
    }

    def setUp(self):
        self.db = load_db()

    def test_reads_samples(self):
        from advisor.vision.hud import read_progress

        for key, (current, cap) in self.TRUTH.items():
            path = sample(f"{key}_*")
            got = read_progress(load(path))
            with self.subTest(file=path.name):
                self.assertEqual((got.current, got.cap), (current, cap))

    def test_blue_cells_fragments(self):
        """当前格的蓝底：碎块只在合起来不超过一格宽时合并，扁蓝条先去掉（按界面单位，几种窗口尺寸一样）。"""
        from advisor.vision import hud
        from advisor.vision.chip_select import ui_scale

        bgr = cv2.cvtColor(np.uint8([[[105, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()

        def cells(w, h, rects):
            """rects：（x, y, 宽, 高），界面单位，x 从进度条区域的左边算起。返回 _blue_cells 的中心 x（界面单位）。"""
            u = ui_scale(w, h)
            box = (w / 2 - hud.PROGRESS_HALF_WIDTH * u, 0, w / 2 + hud.PROGRESS_HALF_WIDTH * u, hud.PROGRESS_DOWN * u)
            img = np.zeros((h, w, 3), np.uint8)
            for x, y, rw, rh in rects:
                p0 = (round(box[0] + x * u), round(y * u))
                p1 = (round(box[0] + (x + rw) * u) - 1, round((y + rh) * u) - 1)
                cv2.rectangle(img, p0, p1, bgr, -1)
            return [(cx - box[0]) / u for cx, _ in hud._blue_cells(img, box)]

        button = (25, 3, 103, 39)  # 「任务」按钮
        skull = [(130, 2, 30, 38), (170, 2, 30, 38)]  # 骷髅格被图标隔成的两半，左边一半和按钮只隔 2
        strip = (130, 42, 70, 6)  # 格子下方的扁蓝条
        for w, h in ((1920, 1080), (3840, 2054), (2560, 1057), (2397, 1798)):
            with self.subTest(size=(w, h)):
                got = cells(w, h, [button, *skull, strip])
                self.assertEqual(len(got), 1, got)
                self.assertAlmostEqual(got[0], 165, delta=2)
                # 完整的一格贴着一条扁蓝条（21:9 窗口里常见）：去掉扁条，合并后不超高
                got = cells(w, h, [(300, 2, 70, 39), (313, 43, 44, 6)])
                self.assertEqual(len(got), 1, got)
                self.assertAlmostEqual(got[0], 335, delta=2)
                self.assertEqual(cells(w, h, [button]), [])

    def test_session_adopts_after_two_reads(self):
        s = ready(self.db)
        s.on_progress(11, 17)
        self.assertIsNone(s.game.progress)
        self.assertEqual(s.on_progress(11, 17).kind, "status")
        self.assertEqual((s.game.progress, s.game.progress_cap), (11, 17))
        s.on_progress(14), s.on_progress(14)
        s.on_progress(13), s.on_progress(13)  # 任务奖励「进度-1」：往回退也照样采用
        self.assertEqual(s.game.progress, 13)
        self.assertEqual(s.build_state(frame(["书签", "标记喷罐", "鹰眼瞄具"])).progress, 13)
        s.new_game()
        self.assertIsNone(s.game.progress)

    def test_progress_pending(self):
        """读到和已采用的不同的格、还在等再读到一次时为 True（三选一上据此马上补读，见 pipeline._read_progress）。"""
        s = ready(self.db)
        s.on_progress(11), s.on_progress(11)
        self.assertFalse(s.progress_pending)
        s.on_progress(None)  # 读不出不算
        self.assertFalse(s.progress_pending)
        s.on_progress(10)
        self.assertTrue(s.progress_pending)
        self.assertEqual(s.game.progress, 11)
        s.on_progress(10)
        self.assertFalse(s.progress_pending)
        self.assertEqual(s.game.progress, 10)

    def test_engine_rounds_left_from_progress(self):
        from advisor.engine import advise

        offer = ["星币锤", "书签", "金色飞星"]
        by_round = advise(self.db, State(hero="芬妮", star=3, round=9, map="异变图书馆", progress_cap=17), offer)
        by_cell = advise(self.db, State(hero="芬妮", star=3, round=9, map="异变图书馆", progress_cap=17, progress=14), offer)
        self.assertEqual((by_round.picks.cap, by_round.picks.rounds_left), (17, 8))
        self.assertEqual((by_cell.picks.cap, by_cell.picks.rounds_left), (17, 3))
        # 收入类筹码（星币锤）剩下的兑现机会少了，分数更低；显示的进度上限仍是 17
        hammer = lambda adv: next(e for e in adv.evals if e.chip.name == "星币锤")  # noqa: E731
        self.assertLess(hammer(by_cell).total, hammer(by_round).total)
        income = [p for p in hammer(by_cell).parts if p.label == "收入折算"]
        # 第 9 轮、第 14 格：再过 3 轮（第 10、11 轮）就到第 17 格，其间没有第 3 的倍数轮，星光收入不再兑现
        self.assertTrue(income and "到进度上限（17）前还有 0 次轮次奖励" in income[0].reason, income)

    def test_progress_vetoes_false_new_game(self):
        """星数被读成 0、轮次读不出时，进度条还在第 13 格：不判新的一局。"""
        s = ready(self.db)
        t = [0.0]
        s._clock = lambda: t[0]
        for _ in range(2):
            s.on_stars(own(2))
            s.on_progress(13, 17)
        s.set_owned(["书签"])
        t[0] = 40.0  # 轮次、星数很久没读到
        for _ in range(3):
            t[0] += 2
            s.on_progress(13)
            ev = s.on_stars(own(0))
            self.assertTrue(ev is None or ev.kind != "newgame")
        self.assertEqual(s.settings.owned, ["书签"])


class RoundSanityTest(unittest.TestCase):
    """轮次读数的两道防线：只按位置读到的两位数要两次一致才用；远超进度条当前格的轮次按读错处理。
    另有 10-03 实机样本上的两处：名牌下方的血量不当成轮次；数字检测不出时在「轮」字左边补读。"""

    def test_menu_edge_read_as_three(self):
        """4:3 小窗口里「13」连同菜单按钮的边缘读成「3.13」或「313」：去掉前缀。"""
        from advisor.vision.chip_select import Line
        from advisor.vision.hud import _parse_round

        w, h = 2560, 1057
        mark = Line("轮", 165, 43, 30, 30)
        for text in ("3.13", "313"):
            self.assertEqual(_parse_round([Line(text, 94, 37, 130, 71), mark], w, h), 13, text)
        self.assertIsNone(_parse_round([Line("3.13", 140, 37, 60, 71), mark], w, h))  # 左边没伸进菜单按钮

    def test_weak_reading_needs_agreement(self):
        """「轮」字认不出、只按位置读到的数：三个大小里至少两次一致才采用。"""
        from unittest import mock

        from advisor.vision import hud as hud_module, io

        img = np.zeros((899, 1199, 3), np.uint8)
        readings = iter([(2, False), (20, False), (None, False)])
        with mock.patch.object(io, "ocr", return_value=[]), \
                mock.patch.object(hud_module, "_parse_round_ex", side_effect=lambda *a: next(readings)):
            self.assertIsNone(hud_module.read_round(img))
        readings = iter([(2, False), (2, False)])
        with mock.patch.object(io, "ocr", return_value=[]), \
                mock.patch.object(hud_module, "_parse_round_ex", side_effect=lambda *a: next(readings)):
            self.assertEqual(hud_module.read_round(img), 2)
        readings = iter([(20, False), (2, True)])  # 换个大小读到了「02轮」：直接用
        with mock.patch.object(io, "ocr", return_value=[]), \
                mock.patch.object(hud_module, "_parse_round_ex", side_effect=lambda *a: next(readings)):
            self.assertEqual(hud_module.read_round(img), 2)

    def test_small_4_3_window_never_reads_20(self):
        """照那一局第 2 轮的三选一画面缩成几种 4:3 窗口：读成 2 或读不出，不能读成别的轮次。"""
        from advisor.vision.hud import read_round

        img = load(sample("20261002_191851_*"))
        for size in ((1199, 899), (1439, 1079), (1000, 750)):
            small = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
            with self.subTest(size=size):
                self.assertIn(read_round(small), (None, 2))

    def test_hp_below_mark_is_not_round(self):
        """怪物名牌下方的红色血量「17/17」读成「17」，在「轮」字左下方：数字框没盖住「轮」字中心，不是轮次（10-03 实机）。"""
        from advisor.vision.chip_select import Line, ui_scale
        from advisor.vision.hud import _parse_round_ex

        w, h = 2560, 1369
        u = ui_scale(w, h)
        mark = Line("轮", 168 * u, 43 * u, 31 * u, 28 * u)
        rank = Line("1st", 65 * u, 105 * u, 57 * u, 31 * u)
        hp = Line("17", 113 * u, 67 * u, 46 * u, 35 * u)
        self.assertEqual(_parse_round_ex([mark, hp, rank], w, h), (None, False))
        digits = Line("08", 114 * u, 34 * u, 87 * u, 63 * u)
        self.assertEqual(_parse_round_ex([mark, digits, rank], w, h), (8, True))

    def test_hp_frame(self):
        """名牌和血量叠在「08 轮」上的实机画面：读出 8，不读成 17（原图和缩到 1280 宽）。"""
        from advisor.vision.hud import read_round

        img = load(sample("20261003_130926_*"))
        self.assertEqual(read_round(img), 8)
        small = cv2.resize(img, (1280, round(img.shape[0] * 1280 / img.shape[1])), interpolation=cv2.INTER_AREA)
        self.assertEqual(read_round(small), 8)

    def test_digits_left_of_mark(self):
        """「03」「11」看得清，文字检测却漏掉（检测框切掉了首位数字的描边）：在「轮」字左边补读。
        原图和缩到最小窗口（pipeline.MIN_UI_SCALE）的都读得出。"""
        from advisor.vision.chip_select import ui_scale
        from advisor.vision.hud import read_round
        from advisor.vision.pipeline import MIN_UI_SCALE

        for key, want in (("20261003_125350", 3), ("20261003_131958", 11)):
            img = load(sample(f"{key}_*"))
            h, w = img.shape[:2]
            f = MIN_UI_SCALE / ui_scale(w, h)
            small = cv2.resize(img, (math.ceil(w * f - 1e-6), math.ceil(h * f - 1e-6)), interpolation=cv2.INTER_AREA)
            for name, im in (("原图", img), ("最小窗口", small)):
                with self.subTest(file=key, variant=name):
                    self.assertEqual(read_round(im), want)

    def test_recognize_only_restores_engine(self):
        """只做识别会改引擎上的开关（rapidocr_lite）：用完要还原，之后的识别照常做检测。"""
        from advisor.vision import hud, io
        from advisor.vision.chip_select import _engine, recognize_only

        engine = _engine()
        before = {key: getattr(engine, key) for key in ("use_det", "use_cls", "use_rec")}
        self.assertEqual(before, {"use_det": True, "use_cls": True, "use_rec": True})
        recognize_only(np.full((40, 120, 3), 128, np.uint8))
        self.assertEqual({key: getattr(engine, key) for key in before}, before)
        img = load(sample("20261003_125350_*"))
        lines = io.ocr(img, hud.round_box(img.shape[1], img.shape[0]))
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(ln.cx, float) for ln in lines))

    def test_mark_fallback_needs_agreement(self):
        """「轮」字左边的补读：两种大小都读成同一个两位数、置信度够高才采用；第一遍什么都没读到时不补读。"""
        from unittest import mock

        from advisor.vision import hud as hud_module, io
        from advisor.vision.chip_select import Line, ui_scale

        w, h = 2560, 1369
        u = ui_scale(w, h)
        img = np.zeros((h, w, 3), np.uint8)
        mark = [Line("轮", 168 * u, 43 * u, 31 * u, 28 * u)]
        cases = (
            ([[("03", 0.99)], [("08", 0.99)]], None, 2),
            ([[("03", 0.99)], [("03", 0.98)]], 3, 2),
            ([[("03", 0.5)], [("03", 0.5)]], None, 1),
            ([[("00", 0.99)], [("00", 0.99)]], None, 1),
            ([[], []], None, 1),
        )
        for reads, want, calls in cases:
            with self.subTest(reads=reads), mock.patch.object(io, "ocr", return_value=mark), \
                    mock.patch.object(hud_module, "recognize_only", side_effect=reads) as rec:
                self.assertEqual(hud_module.read_round(img), want)
                self.assertEqual(rec.call_count, calls)
        with mock.patch.object(io, "ocr", return_value=[]), \
                mock.patch.object(hud_module, "recognize_only") as rec:
            self.assertIsNone(hud_module.read_round(img))
            rec.assert_not_called()

    def test_covered_by_portrait(self):
        """玩家列表第一行的头像挡住轮次数字（2560×1600 笔记本上的实机截图，上下有黑边，先去掉）：繁体截图上兔耳朵顶到
        「03」下沿、耳朵和「Round」连成「ARound」，照样认出标记，在它左边裁一小块读出 3；简体截图上天线插进「04」，
        读不出可以、不能读错。两张都看得见标记，只做一遍带找字的识别。大厅左上角的活动日期、通行证名不当成轮次，也不重读。"""
        from advisor.vision.hud import read_round_info
        from advisor.vision.pipeline import trim_borders

        cases = (
            ("20261106_211543_hud_round3_ears_hant_*", (3,), True),
            ("20261018_164911_hud_round4_antenna_*", (None, 4), True),
            ("20261106_211618_lobby_dates_hant_*", (None,), False),
        )
        for pattern, want, mark in cases:
            img, _ = trim_borders(load(sample(pattern)))
            got = read_round_info(img)
            with self.subTest(file=pattern):
                self.assertIn(got.value, want)
                self.assertEqual(got.mark, mark)
                self.assertEqual(got.passes, 1)
                self.assertEqual(got.crop is not None, mark)

    def test_covered_digits_not_misread(self):
        """贴一条白块挡住「04」的下沿两成（模拟头像顶上来）：放大 1.5 倍重读会读成「10」；看见了标记就先裁小块只认字，
        读出 4（或读不出），不换大小重读，不读错。白块按第一遍读到的数字框放（界面单位，量自这张 2560×1057 截图）。"""
        from advisor.vision.chip_select import ui_scale
        from advisor.vision.hud import read_round_info

        img = load(sample("20261002_144715_*"))
        u = ui_scale(img.shape[1], img.shape[0])
        x0, y1, x1, y2 = 70.5, 54.9, 157.7, 67.3  # 数字框 x 70.5~157.7、y 5.3~67.3 的下面两成
        cv2.rectangle(img, (int(x0 * u), int(y1 * u)), (int(x1 * u), int(y2 * u)), (250, 250, 250), -1)
        got = read_round_info(img)
        self.assertIn(got.value, (None, 4))
        self.assertEqual((got.mark, got.passes), (True, 1))

    def test_first_pass_checked_by_crop(self):
        """第一遍靠标记读到的数用标记左边那一块只认字核对：这张截图是「04 轮」，带找字的第一遍换成读成「10」（数字被
        挡住一部分时会这样读错，贴白块挡住下沿一成半时实测读成过「10」），标记左边那一块只认字读出 4，两边对不上，
        按读不出处理（宁可读不出，也不读错）；第一遍读成「04」时两边一致，照用。只换掉第一遍的文字识别，裁小块那一遍
        用真的。文字框是这张 3840×2054 截图第一遍读到的位置（界面单位：数字 x 78.1~153.8、y 6.3~64.1，
        「轮」x 153.5~185.1、y 29.2~57.6）。"""
        from unittest import mock

        from advisor.vision import hud as hud_module, io
        from advisor.vision.chip_select import Line, ui_scale

        img = load(sample("20261002_020436_*"))
        u = ui_scale(img.shape[1], img.shape[0])

        def box(text, x0, y0, x1, y1):
            return Line(text, (x0 + x1) / 2 * u, (y0 + y1) / 2 * u, (x1 - x0) * u, (y1 - y0) * u)

        mark = box("轮", 153.5, 29.2, 185.1, 57.6)
        for text, want in (("04", 4), ("10", None)):
            first = [box(text, 78.1, 6.3, 153.8, 64.1), mark]
            with self.subTest(text=text), mock.patch.object(io, "ocr", return_value=first) as ocr:
                got = hud_module.read_round_info(img)
                self.assertEqual((got.value, got.mark, got.passes, ocr.call_count), (want, True, 1, 1))
                self.assertEqual(hud_module.read_round_crop(img, got.crop), 4)

    def test_crop_reread_with_menu_prefix(self):
        """21:9 梦想号截图上，标记左边那一块读成「109」（菜单按钮的边缘连带读成「1」）：去掉前缀读出 9。
        read_round_info 交回的那一块交给 read_round_crop（隔一会儿只认字再看一眼）也读得出。"""
        from advisor.vision.hud import read_round_crop, read_round_info

        img = load(sample("20261002_145427_*"))
        got = read_round_info(img)
        self.assertEqual((got.value, got.mark), (9, True))
        self.assertEqual(read_round_crop(img, got.crop), 9)
        h, w = img.shape[:2]
        self.assertIsNone(read_round_crop(img, (w + 10, 0, w + 50, 40)))  # 裁到画面外：读不出，不报错

    def test_round_far_above_progress_is_ignored(self):
        db = load_db()
        s = ready(db)
        s.on_progress(2), s.on_progress(2)
        s.on_hud(hud(round=2)), s.on_hud(hud(round=2))
        for _ in range(3):
            s.on_hud(hud(round=20))
        self.assertEqual(s.settings.round, 2)
        self.assertTrue(any(e["kind"] == "read:round_implausible" for e in s.recent_events(60)))
        s.on_hud(hud(round=3))  # 正常往前走照常采用
        self.assertEqual(s.settings.round, 3)

    def round_jump_session(self):
        """第 9 轮、进度条第 10 格采用，进度读到第 11 格。返回记账和时钟。"""
        s = ready(load_db())
        t = [0.0]
        s._clock = lambda: t[0]
        s.on_progress(10), s.on_progress(10)
        s.on_hud(hud(round=9)), s.on_hud(hud(round=9))
        self.assertEqual((s.settings.round, s.game.progress), (9, 10))
        self.assertEqual(s.game._round_at_progress, (9, 10))
        s.on_progress(11), s.on_progress(11)
        self.assertEqual(s.game.progress, 11)
        return s, t

    def jumps(self, s):
        return [e for e in s.recent_events(600) if e["kind"] == "read:round_jump_implausible"]

    def test_round_jump_far_ahead_of_progress_is_ignored(self):
        """轮次涨得比进度条走的多出 2 格以上时按读错处理；进度跟上来以后照常采用。"""
        s, _ = self.round_jump_session()
        for _ in range(2):
            s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 9)
        self.assertTrue(self.jumps(s))
        s.on_progress(12), s.on_progress(12)
        self.assertEqual(s.game.progress, 12)
        for _ in range(2):
            s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 13)  # 跳 4、进度走 2：多出 2，不超过
        s.on_hud(hud(round=14))  # 往前走一步照常直接采用
        self.assertEqual(s.settings.round, 14)

    def test_round_jump_unchecked_at_progress_end(self):
        """进度条到了红叉前一格（再走一格就判负，进度不会再跟着轮次往前走）：轮次再往前跳也不比，照原样采用。"""
        s = ready(load_db())
        s.on_progress(16, 17), s.on_progress(16, 17)
        s.on_hud(hud(round=14)), s.on_hud(hud(round=14))
        for _ in range(2):
            s.on_hud(hud(round=18))
        self.assertEqual(s.settings.round, 18)
        self.assertFalse(self.jumps(s))

    def test_round_jump_unchecked_when_progress_stale(self):
        """进度条超过 NEWGAME_VETO_S 秒没读到：进度是旧的，不比，照原样采用。"""
        from advisor.session import NEWGAME_VETO_S

        s, t = self.round_jump_session()
        t[0] += NEWGAME_VETO_S + 1
        for _ in range(2):
            s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 13)
        self.assertFalse(self.jumps(s))

    def test_round_jump_unchecked_when_round_changed(self):
        """记下的轮次后来被别的路径改过（_round_at_progress 对不上）时不比，照原样采用。"""
        s, _ = self.round_jump_session()
        s.settings.round = 10
        for _ in range(2):
            s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 13)
        self.assertFalse(self.jumps(s))

    def test_round_jump_unchecked_while_progress_catching_up(self):
        """轮次和进度条一起往前跳（游戏切到后台好几轮、回来时）：进度条新读到的格数还没采用（连读两次才采用）时不比，
        轮次照常连读两次采用。拿还没跟上的旧进度比的话，读对的轮次会被拦下（回放 21:9 那一局时第 9 轮的三选一记成了第 5 轮）。"""
        from advisor.session import NEWGAME_VETO_S

        s, t = self.round_jump_session()  # 第 9 轮，进度第 11 格
        t[0] += NEWGAME_VETO_S + 1  # 切到后台好一阵：进度读数已经旧了
        s.on_hud(hud(round=13))
        s.on_progress(14)  # 同一帧里进度条也跳了，这一次还没采用
        self.assertEqual(s.game.progress, 11)
        s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 13)
        self.assertFalse(self.jumps(s))
        s.on_progress(14)
        self.assertEqual(s.game.progress, 14)

    def test_round_jump_after_round_newgame_not_checked_against_last_game(self):
        """轮次变小判成新的一局时，上一局的进度条当前格不带进新的一局：新的一局里头一次往前跳照原样采用（拿上一局的
        进度去比的话，这一局的进度一直比它小，轮次再也跳不上去）。"""
        from advisor.session import NEWGAME_VETO_S

        s, t = self.round_jump_session()  # 第 9 轮，进度第 11 格
        t[0] += NEWGAME_VETO_S + 1  # 上一局的进度读数已经旧了：不否决新的一局
        s.on_hud(hud(round=1)), s.on_hud(hud(round=1))
        self.assertEqual((s.settings.round, s.game.progress), (1, None))  # 开了新的一局
        self.assertIsNone(s.game._round_at_progress)
        s.on_progress(4), s.on_progress(4)
        for _ in range(2):
            s.on_hud(hud(round=3))
        self.assertEqual(s.settings.round, 3)
        self.assertFalse(self.jumps(s))

    def test_round_back_up_after_lower_misread_adopted(self):
        """「14」连读成「11」、按改正读错采用了以后，读回 14 两次就改回来：采用的轮次比原来的小时不记进度，下一次往前跳
        不比（读错的正是变小的那一次，进度没多走）。"""
        s = ready(load_db())
        t = [0.0]
        s._clock = lambda: t[0]

        def read(rnd, prog):
            t[0] += 3
            s.on_hud(hud(round=rnd))
            s.on_progress(prog, 20)

        for rnd in range(1, 15):  # 一步一步打到第 14 轮，第 15 格
            for _ in range(3):
                read(rnd, rnd + 1)
        self.assertEqual((s.settings.round, s.game.progress), (14, 15))
        read(11, 15), read(11, 15)
        self.assertEqual(s.settings.round, 11)
        self.assertIsNone(s.game._round_at_progress)
        read(14, 15), read(14, 15)
        self.assertEqual(s.settings.round, 14)
        self.assertFalse(self.jumps(s))

    def test_round_progress_comes_back_with_undo(self):
        """撤销新的一局时，采用轮次时记下的进度跟着那一局回来，照旧拿它核对往前跳。"""
        from advisor.session import NEWGAME_VETO_S

        s, t = self.round_jump_session()  # 第 9 轮时第 10 格，进度第 11 格
        t[0] += NEWGAME_VETO_S + 1
        s.on_hud(hud(round=1)), s.on_hud(hud(round=1))
        self.assertIsNone(s.game._round_at_progress)
        self.assertTrue(s.undo_new_game())
        self.assertEqual((s.settings.round, s.game._round_at_progress), (9, (9, 10)))
        s.on_progress(11), s.on_progress(11)
        for _ in range(2):
            s.on_hud(hud(round=13))
        self.assertEqual(s.settings.round, 9)
        self.assertTrue(self.jumps(s))


class NewGameVetoTest(unittest.TestCase):
    """对局中途的新一局判定要互相印证：另一项读数 20 秒内还说这一局在继续，就按读错处理（真人对局里的误判）。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)
        self.t = [0.0]
        self.session._clock = lambda: self.t[0]
        s = self.session
        for _ in range(2):
            s.on_hud(hud(round=12))
            s.on_stars(own(3))
        s.set_owned(["书签", "标靶"])

    def tick(self, seconds):
        self.t[0] += seconds

    def vetoes(self):
        return [e for e in self.session.recent_events(600) if e["kind"] == "read:newgame_vetoed"]

    def test_transfer_screen_star_drop(self):
        """星币转账画面去掉了自己那一行、玩家列表上移：读到的「自己」是 0 星的队友，轮次照常是第 12 轮。"""
        s = self.session
        for _ in range(4):
            self.tick(2)
            s.on_hud(hud(round=12))
            self.assertIsNone(s.on_stars(own(0)))
        self.assertEqual((s.settings.owned, s.settings.star), (["书签", "标靶"], 3))
        self.assertTrue(self.vetoes())
        self.assertEqual(self.vetoes()[-1]["by"], "star")

    def test_round_misread_with_stars(self):
        """名牌叠在轮次上读成「01」：星数刚读到 3 星，不判。"""
        s = self.session
        for _ in range(3):
            self.tick(2)
            s.on_stars(own(3))
            ev = s.on_hud(hud(round=1))
            self.assertTrue(ev is None or ev.kind != "newgame")
        self.assertEqual(s.settings.owned, ["书签", "标靶"])
        self.assertEqual(s.settings.round, 12)

    def test_hero_misread(self):
        s = self.session
        for _ in range(3):
            self.tick(2)
            s.on_hud(hud(round=12))
            ev = s.on_hero(SimpleNamespace(hero="凛"))
            self.assertTrue(ev is None or ev.kind != "newgame")
        self.assertEqual((s.game.seen_hero, s.settings.owned), ("芬妮", ["书签", "标靶"]))

    def test_select_like_screen_mid_game(self):
        s = self.session
        self.tick(3)
        self.assertIsNone(s.on_select_screen())
        self.assertEqual(s.settings.owned, ["书签", "标靶"])
        self.tick(30)  # 真的回到了选角界面：轮次、星数都很久没读到
        self.assertEqual(s.on_select_screen().kind, "newgame")

    def test_real_new_game_round_first(self):
        s = self.session
        self.tick(90)  # 结算、大厅、选角、加载
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")
        self.assertEqual(s.settings.owned, [])

    def test_real_new_game_stars_and_round_together(self):
        """新的一局里轮次和星数同时读到：两项都说新的一局，互不否决。"""
        s = self.session
        self.tick(60)
        events = []
        for _ in range(2):
            self.tick(2)
            events += [s.on_stars(own(0)), s.on_hud(hud(round=1))]
        self.assertIn("newgame", [e.kind for e in events if e])
        self.assertEqual(s.settings.owned, [])
        self.assertEqual([e.kind for e in events if e].count("newgame"), 1)  # 只清一次

    def test_veto_expires(self):
        """轮次读不到了（被弹窗挡住等）超过 20 秒：只有星数可依，照常判定。"""
        s = self.session
        self.tick(25)
        s.on_stars(own(0))
        self.assertEqual(s.on_stars(own(0)).kind, "newgame")

    def test_early_game_not_vetoed(self):
        """前几轮（不超过 NEWGAME_ROUND_MAX）、0 星时没有可印证的：照旧只靠一项判定。"""
        s = ready(self.db)
        s._clock = lambda: 0.0
        for _ in range(2):
            s.on_hud(hud(round=4))
            s.on_stars(own(0))
        s.on_hud(hud(round=1))
        self.assertEqual(s.on_hud(hud(round=1)).kind, "newgame")


class NewGameCauseTest(unittest.TestCase):
    """新一局判定带上依据的种类，悬浮窗据此区分「对局中途」和「选角界面」。"""

    def setUp(self):
        self.db = load_db()
        self.session = ready(self.db)

    def test_causes(self):
        s = self.session
        self.assertEqual(s.on_select_screen().cause, "select")
        s.on_hud(hud(round=4))
        s.on_stars(own(2)), s.on_stars(own(2))
        s.on_stars(own(0))
        ev = s.on_stars(own(0))
        self.assertEqual((ev.kind, ev.cause), ("newgame", "star"))
        s2 = Session(self.db, Settings(hero="凛"))
        s2.on_hero(SimpleNamespace(hero="凛"))
        s2.on_hero(SimpleNamespace(hero="邦妮"))
        self.assertEqual(s2.on_hero(SimpleNamespace(hero="邦妮")).cause, "hero")

    def test_cause_in_event_record(self):
        s = self.session
        s.note([s.on_select_screen()])  # 识别线程每帧把事件记进最近事件
        kinds = [e for e in s.recent_events(60) if e.get("kind") == "newgame"]
        self.assertTrue(kinds)
        self.assertEqual(kinds[-1].get("cause"), "select")


# 10-04 实战局（芬妮，异变图书馆，噩梦）的识别记录里三段三选一的完整识别读数：
# （时刻的秒数, 三个候选筹码的识别文字, 刷新次数）。只查绿框的快速复查不写记录，这里也不喂
REFRESH_0953 = [  # 第 9 轮升星三选一（3 星），连刷三次
    (48.185, ["星币锤", "维生素药丸", "摩托头盔-中级"], 5),
    (49.530, ["星币锤", "维生素药丸", "摩托头盔-中级"], 5),
    (51.139, ["高效", "2", "优雅"], 4),  # 刷新动画的中间帧
    (51.790, ["肾上腺素-高效", "摩托头盔-一般", "优雅之羽"], 4),
    (53.109, ["星币锤", "美工刀-锋利", "放大镜"], 3),
    (53.750, ["星币锤", "美工刀-锋利", "放大镜"], 3),
    (55.569, ["汉娜人偶", "拳击手套-高级", "飞星"], 2),  # 第 3 个只读到共同部分
    (56.226, ["汉娜人偶", "拳击手套-高级", "金色飞星"], 2),
]
REFRESH_1000 = [  # 第 10 轮任务三选一（3 级），刷新一次
    (10.690, ["智能手表", "友情徽章", "医疗箱-完备治疗"], 2),
    (13.781, ["智能手表", "友情徽章", "医疗箱-完备治疗"], 2),
    (15.361, ["", "", "医疗箱"], 1),  # 刷新动画的中间帧
    (15.991, ["大背包", "星币锤", "医疗箱-紧急治疗"], 1),
]
MISREAD_0951 = [  # 第 7 轮任务三选一（2 级），第 3 个候选筹码（拳击手套-中级）读成错字
    (3.187, ["可口糖果", "紫色飞星", "卷キ手套-中级"], 4),
    (5.007, ["可口糖果", "紫色飞星", "卷专手套-中级"], 4),
    (5.893, ["可口糖果", "紫色飞星", "卷夫手套-中级"], 4),
    (6.772, ["可口糖果", "紫色飞星", "套-中级"], 4),
]


class RealGameRecognitionTest(unittest.TestCase):
    """用 10-04 实战局的识别读数复现两次刷新时闪出「无法识别」和「匹配度低」提示，
    按现在的做法核对：刷新后没读全时保留推荐、标「正在刷新」；只对得上一个筹码时不提示；只读到共同部分时等读全。
    识别文字按现在的识别模块重新核对（chip_select.check_names）；记录没有存图标边框颜色，按读对了算。"""

    def setUp(self):
        from advisor.vision import chip_select

        self.db = load_db()
        self.cs = chip_select

    def seen(self, raw, rerolls, probabilities, team):
        matched = [self.cs._match_chip(self.db, r) for r in raw]
        names, scores = [m[0] for m in matched], [m[1] for m in matched]
        rarity = [self.db.chips[n].rarity if n in self.db.chips else None for n in names]
        unrecorded, partial, problems = self.cs.check_names(self.db, names, list(raw), scores, rarity)
        return self.cs.ChipSelect(names, list(raw), scores, rarity, rerolls, probabilities, None, problems,
                                  unrecorded=unrecorded, partial=partial, team=team)

    def replay(self, reads, star, probabilities, team=False, owned=()):
        s = ready(self.db)
        s.on_stars(own(star))
        s.set_owned(list(owned))
        clock = [0.0]
        s._clock = lambda: clock[0]
        out = []
        for t, raw, rerolls in reads:
            clock[0] = t
            ev = s.on_frame(self.seen(raw, rerolls, probabilities, team))
            out.append((t, ev.kind if ev is not None else None, list(ev.problems) if ev is not None else None))
        return out

    def test_refresh_middle_frame_0953(self):
        got = self.replay(REFRESH_0953, 3, (25, 50, 25))
        self.assertEqual([k for _, k, _ in got],
                         ["advice", None, "refreshing", "advice", "advice", None, "refreshing", "advice"])
        # 刷出金色飞星那一次：读全以后的推荐不带「匹配度低」的提示（不一直留到界面关闭）
        self.assertEqual(got[-1][2], [])
        self.assertNotIn("unreadable", [k for _, k, _ in got])

    def test_refresh_middle_frame_1000(self):
        got = self.replay(REFRESH_1000, 3, (25, 50, 25), team=True, owned=["金色飞星"])
        self.assertEqual([k for _, k, _ in got], ["advice", None, "refreshing", "advice"])

    def test_misread_only_one_chip_fits_0951(self):
        got = self.replay(MISREAD_0951, 2, (40, 45, 15), team=True)
        self.assertEqual(got[0][1:], ("advice", []))  # 不提示「匹配度低：识别文字「卷キ手套-中级」……」、不请用户改正
        self.assertEqual([k for _, k, _ in got[1:]], [None, None, None])


if __name__ == "__main__":
    unittest.main()
