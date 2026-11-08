"""推荐引擎的健全性测试：35 个角色、固定种子随机生成的局面下，advise() 的输出满足基本约束，并且不出现
显而易见的错误（例如给辅助推荐星币锤而不推荐金色飞星）。

随机局面覆盖：星数 0~3、轮次（含待识别）、已有筹码 0~10 个、地图已知或待识别（含候选地图）、普通 / 困难及以上、
难度档位（不知道、困难、噩梦及以上、困难及以上）、来源升星 / 筹码商店 / 任务及等级、刷新次数 0~5、队友有无、畸变组件有无、资料库未收录的候选筹码、进度条读到的进度上限和当前格、
画面上读到的概率行。局面按游戏规则生成：已有筹码和候选筹码都取自当时的筹码池（不超过两个流派、不出别的地图的专属筹码）。
被同系列严格上位替代的候选（data/series.toml）不参与比较，推荐度为 0：首推和「适配度均偏低」的检查都不算它（compared）。
本次之后预计没有筹码三选一时，「刷新」按最多连续刷新到剩余次数用完估值（RerollEval.chain），不低于只算刷新一次的估值。
"""
import contextlib
import dataclasses
import importlib
import json
import math
import os
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import advisor.engine as engine_mod
from advisor.data import (
    LEVEL_HARD,
    LEVEL_HARD_PLUS,
    LEVEL_NIGHTMARE,
    LEVEL_NORMAL,
    NORMAL,
    RARITIES,
    SCHOOLS,
    UNRECORDED,
    load_db,
)
from advisor.engine import LOW_NOTE, NORMAL_NOTE, REROLL, UNKNOWN_DIFFICULTY_LEAD, Scorer, advise, default_rerolls
from advisor.pool import State, Teammate, owned_schools, player_pool
from tests.support.player_layer import with_player_layer
from tests.test_difficulty import as_known_difficulty

ROOT = Path(__file__).resolve().parents[1]
# 悬浮窗上的文字不写分值：总分、中性分、平均分、推荐线、推荐分，以及「50 分」「（… 分）」这类写法。
# 畸变组件附注的结尾是「（仅提示，未计入推荐度）」，不用为「不计分）」留例外
SCORE_WORDING = re.compile(r"总分|中性分|平均分|推荐线|推荐分|\d\s*分|分）")
SEED = 20261003
CASES_PER_HERO = 10  # 每个角色的随机局面数；第 0、1 个固定为已有 10 个筹码（「查看筹码」面板一页正好满）
OFFICIAL = {"基础分", "成长分", "角色分", "标签分", "关键词分"}
ALLOWED_LABELS = OFFICIAL | {
    "玩家思路", "角色专属规则", "特性", "其他特性", "推荐流派", "组合", "队友", "难度", "收入折算", "循环往复", "职业倾向", "未收录",
}
# 适配表给的加分（玩家思路层，或没有列入玩家思路时的自动层）
SYNERGY_LAYER = {"玩家思路", "角色专属规则", "特性", "其他特性"}
SUPPORT = ("芬妮", "蓝海晴", "超天酱", "米米", "阿尔", "吉尔", "帕露南", "恋", "远野汉娜", "潘大猛")


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def fingerprint(adv):
    """一次推荐的全部输出（比较「同样的输入两次结果完全相同」用）。"""
    r = adv.reroll
    return (
        adv.best, adv.top, tuple(adv.notes), adv.maps, tuple(adv.unrecorded), adv.tendency,
        dataclasses.astuple(adv.picks) if adv.picks else None,
        (r.expected, r.cost, r.value, r.p_better, r.percent, r.chain) if r else None,
        tuple(
            (e.chip.name, e.total, e.immediate, e.lookahead, e.percent, e.official, e.official_raw,
             e.lookahead_reason, e.lookahead_short, tuple((p.label, p.score, p.reason, p.origin, p.short) for p in e.parts),
             e.superseded_by, e.superseded_tip, tuple(e.versus))
            for e in adv.evals
        ),
    )


def compared(adv):
    """参与比较的候选：被同系列严格上位替代的（data/series.toml，推荐度为 0）不算。"""
    return [e for e in adv.evals if not e.superseded_by]


def repro(st: State, offer, rarity=None) -> str:
    """失败信息里的复现代码：可以直接贴进 Python 运行。"""
    args = f"{list(offer)!r}" + (f", rarity={list(rarity)!r}" if rarity else "")
    return f"advise(load_db(), {st!r}, {args})"


# ------------------------------------------------------------ 随机局面


def make_owned(db, rng: random.Random, true_map: str, k: int) -> list[str]:
    """按游戏规则凑 k 个已有筹码：每次从当时的筹码池里取（流派占满两个后其余流派不再出现）；
    龙宫游乐园有时带一个地图发放的筹码；偶尔混进一个资料库未收录的名称（识别到但不在筹码表里）。"""
    owned: list[str] = []
    granted = [c.name for c in db.chips.values() if c.granted and true_map in c.maps]
    if granted and k and rng.random() < 0.5:
        owned.append(rng.choice(granted))
    if k >= 3 and rng.random() < 0.1:
        owned.append("某个新筹码")
    while len(owned) < k:
        pool = [c.name for c in player_pool(db, owned, true_map)]
        if not pool:
            break
        owned.append(rng.choice(pool))
    return owned


def random_level(rng: random.Random, difficulty) -> tuple[str | None, str | None]:
    """难度档位和难度名：普通难度时为不知道或普通；困难及以上时为不知道、困难、噩梦及以上（有或没有具体难度名）、困难及以上。"""
    if difficulty == NORMAL:
        return rng.choice(((None, None), (LEVEL_NORMAL, "普通")))
    return rng.choice((
        (None, None), (LEVEL_HARD, "困难"), (LEVEL_NIGHTMARE, None), (LEVEL_NIGHTMARE, "噩梦"), (LEVEL_NIGHTMARE, "疯狂"),
        (LEVEL_NIGHTMARE, "极限"), (LEVEL_HARD_PLUS, None),
    ))


def random_case(db, rng: random.Random, hero: str, i: int):
    """返回（State，候选筹码，稀有度或 None）。第 0、1 个局面已有 10 个筹码，第 0 个地图已知、刷新 0 次，第 1 个地图待识别。"""
    star = rng.randint(0, 3)
    difficulty = rng.choice((None, NORMAL))
    true_map = rng.choice(db.maps)
    known = i == 0 or (i != 1 and rng.random() < 0.55)
    k = 10 if i in (0, 1) else rng.choice((0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10))
    owned = make_owned(db, rng, true_map, k)
    rnd = None if rng.random() < 0.2 else rng.randint(1, 20)
    source = rng.choice(("levelup", "shop", "task"))
    tier = rng.choice((None, 1, 2, 3)) if source == "task" else None
    rerolls = 0 if i == 0 else rng.randint(0, 5)
    others = [h for h in db.heroes if h != hero]
    teammates = tuple(
        Teammate(hero=rng.choice(others + [None]), bot=rng.random() < 0.5) for _ in range(rng.choice((0, 0, 1, 2, 3)))
    )
    mutators = tuple(rng.sample(sorted(db.mutators), rng.choice((0, 0, 1, 2))))
    candidates = ()
    if not known and rng.random() < 0.4:
        candidates = tuple(dict.fromkeys([true_map] + rng.sample(list(db.maps), rng.randint(1, 3))))
    probabilities = left_rarity = None
    if rng.random() < 0.25:
        rows = db.rules["rarity"]["normal" if difficulty == NORMAL else "rows"]
        probabilities = tuple(rows[max(1, tier or star) if source == "task" else star])
        if source != "task":
            left_rarity = db.rules["rarity"]["fixed"][star]
    # 难度档位用单独的随机数（按种子、角色和序号），不改变其余各维度的随机序列
    level, level_name = random_level(random.Random(f"{SEED}-{hero}-{i}"), difficulty)
    st = State(
        hero=hero, star=star, round=rnd, map=true_map if known else None, owned=tuple(owned), source=source, tier=tier,
        rerolls=rerolls, probabilities=probabilities, left_rarity=left_rarity, map_candidates=candidates,
        teammates=teammates, mutators=mutators, tasks_done=rng.choice((None, None, 0, 1, 2, 3)), difficulty=difficulty,
        progress_cap=rng.randint(14, 20) if rng.random() < 0.1 else None,
        progress=rnd + rng.randint(0, 4) if rnd is not None and rng.random() < 0.15 else None,
        difficulty_level=level, difficulty_name=level_name,
    )
    pool = [c.name for c in player_pool(db, owned, true_map)]
    offer = rng.sample(pool, 3)
    rarity = None
    if rng.random() < 0.15:
        rarity = [db.chips[n].rarity for n in offer]
        slot = rng.randrange(3)
        offer[slot] = f"未收录筹码{rng.randint(1, 99)}"
        rarity[slot] = rng.choice((*RARITIES, None))
    elif rng.random() < 0.3:
        rarity = [db.chips[n].rarity for n in offer]  # 识别模块照常会传图标边框颜色
    return st, offer, rarity


def corpus(db):
    rng = random.Random(SEED)
    return [(hero, *random_case(db, rng, hero, i)) for hero in db.heroes for i in range(CASES_PER_HERO)]


def engine_pool_users() -> list:
    """推荐引擎里推算筹码池（pool.player_pool）的模块。引擎是一个包，各模块各自导入 player_pool，要替换就得替换在这些
    模块上（只换 advisor.engine 上的名字，各模块照样用原来的）。源码里写到 player_pool、却不是按名字导入的模块报出来：
    换了导入的写法以后替换会悄悄打空。"""
    users = []
    for path in sorted(Path(engine_mod.__file__).parent.glob("*.py")):
        module = engine_mod if path.stem == "__init__" else importlib.import_module(f"advisor.engine.{path.stem}")
        if getattr(module, "player_pool", None) is player_pool:
            users.append(module)
        elif "player_pool" in path.read_text(encoding="utf-8"):
            raise AssertionError(f"advisor/engine/{path.name} 写到了 player_pool，但没有按名字导入，替换不到它")
    return users


def unrelated_blues(db, hero: str) -> list[str]:
    """与该角色无关的蓝色筹码：不分地图、不是地图发放的、不是循环往复；不是游戏内置推荐的适配筹码，标签和初始职业不相符；
    玩家思路没有提到，自动层也没有加减分的规则；不属于该角色的推荐流派。按筹码表的顺序，没有流派的排在前面。"""
    plain = Scorer(db, State(hero=hero))
    auto = Scorer(db, State(hero=hero), use_player=False)
    endless = db.rules["endless_cycle"]["chip"]
    out = []
    for c in db.chips.values():
        if c.rarity != "蓝" or c.maps or c.granted or c.name == endless:
            continue
        if hero in c.fit or hero in c.fit_minor or set(c.tags) & plain.classes:
            continue
        if plain.player_score(c.name, (), None) is not None:
            continue
        if any(p.label in SYNERGY_LAYER and p.score != 0 for p in auto.parts(c.name, (), None)):
            continue
        if c.school and c.school in plain.pref_schools:
            continue
        out.append(c.name)
    return sorted(out, key=lambda n: db.chips[n].school is not None)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def by_name(self, adv):
        return {e.chip.name: e for e in adv.evals}


# ------------------------------------------------------------ 1、2：随机局面的基本约束


class FuzzTest(Base):
    """35 个角色 × CASES_PER_HERO 个随机局面，每个局面只算一次推荐（setUpClass），各条约束分开检查。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cases = corpus(cls.db)
        cls.results = []  # （角色，State，候选，稀有度，Advice 或 None）
        cls.errors = []
        for hero, st, offer, rarity in cls.cases:
            try:
                adv = advise(cls.db, st, offer, rarity)
            except Exception as e:  # noqa: BLE001  异常留到 test_no_exception 里报告
                cls.errors.append(f"{type(e).__name__}: {e}\n  {repro(st, offer, rarity)}")
                adv = None
            cls.results.append((hero, st, offer, rarity, adv))

    def each(self):
        for hero, st, offer, rarity, adv in self.results:
            if adv is not None:
                yield hero, st, offer, rarity, adv

    def given_up_cases(self):
        """前瞻里「放弃本次的X」要被锁掉的高适配度筹码正好是同一次的另一个候选：只按角色特性算时这批随机局面里碰不到，
        另加一个用测试用的默认推荐档位的局面（tests/support/player_layer.toml：芬妮的金色飞星是必选，选电流剑会锁掉星光）。"""
        db = with_player_layer(self.db)
        st = State(hero="芬妮", star=2, round=7, rerolls=3, owned=("医疗箱-紧急治疗",))
        offer = ["金色飞星", "电流剑", "书签"]
        yield "芬妮", st, offer, None, advise(db, st, offer)

    def test_corpus_covers_dimensions(self):
        # 随机局面确实覆盖了要测的各个维度（改了种子或生成方法以后，这条保证覆盖面没有缩水）
        states = [st for _, st, _, _ in self.cases]
        self.assertEqual({st.hero for st in states}, set(self.db.heroes))
        self.assertEqual(len(self.db.heroes), 35)
        self.assertEqual({st.star for st in states}, {0, 1, 2, 3})
        counts = {len(st.owned) for st in states}
        self.assertTrue({0, 10} <= counts and len(counts) >= 8, counts)
        self.assertEqual({st.map is None for st in states}, {True, False})
        self.assertTrue(any(st.map is None and st.map_candidates for st in states))
        self.assertEqual({st.difficulty for st in states}, {None, NORMAL})
        self.assertEqual({st.difficulty_level for st in states if st.difficulty is None},
                         {None, LEVEL_HARD, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS})
        self.assertEqual({st.source for st in states}, {"levelup", "shop", "task"})
        self.assertEqual({st.tier for st in states if st.source == "task"}, {None, 1, 2, 3})
        self.assertEqual({st.rerolls for st in states}, set(range(6)))
        self.assertTrue(any(st.round is None for st in states) and max(st.round or 0 for st in states) >= 18)
        self.assertEqual({bool(st.teammates) for st in states}, {True, False})
        self.assertTrue(any(t.bot for st in states for t in st.teammates) and any(t.hero is None for st in states for t in st.teammates))
        self.assertEqual({bool(st.mutators) for st in states}, {True, False})
        self.assertTrue(any(st.progress_cap for st in states) and any(st.progress for st in states))
        self.assertTrue(any(st.probabilities for st in states))
        self.assertTrue(any(n not in self.db.chips for _, _, offer, _ in self.cases for n in offer))
        self.assertTrue(any(self.db.chips[n].granted for st in states for n in st.owned if n in self.db.chips))
        self.assertTrue(any(n not in self.db.chips for st in states for n in st.owned))
        # 每个角色都有已有 10 个筹码的局面：地图已知和待识别各一个
        for hero in self.db.heroes:
            full = [st for st in states if st.hero == hero and len(st.owned) == 10]
            self.assertEqual({st.map is None for st in full}, {True, False}, hero)

    def test_no_exception(self):
        self.assertEqual(self.errors, [], "\n".join(self.errors[:5]))

    def test_shape(self):
        # 三个候选同序返回；best 是三个候选之一或「刷新」；未收录的候选按序号列出
        for hero, st, offer, rarity, adv in self.each():
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertEqual([e.chip.name for e in adv.evals], offer)
                self.assertIn(adv.best, [*offer, REROLL])
                self.assertEqual(adv.unrecorded, [i for i, n in enumerate(offer) if n not in self.db.chips])
                self.assertIs(adv.state, st)

    def test_numbers_finite(self):
        # 所有分值、百分比、刷新的估计、剩余三选一次数都是有限数
        for hero, st, offer, rarity, adv in self.each():
            numbers = [adv.picks.total, adv.picks.extras]
            for e in adv.evals:
                numbers += [e.total, e.immediate, e.lookahead, e.percent, e.official, e.official_raw]
                numbers += [p.score for p in e.parts]
            if adv.reroll:
                r = adv.reroll
                numbers += [r.expected, r.cost, r.value, r.p_better, r.percent]
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertTrue(all(finite(x) for x in numbers), numbers)
                self.assertGreaterEqual(adv.picks.total, 0)
                if adv.reroll:
                    self.assertGreaterEqual(adv.reroll.cost, 0)
                    self.assertTrue(0 <= adv.reroll.p_better <= 1)

    def test_percent_sum(self):
        # 推荐度（百分比）在 0~1 之间，三个候选和「刷新」合计为 100%
        for hero, st, offer, rarity, adv in self.each():
            percents = [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertTrue(all(0 <= p <= 1 for p in percents), percents)
                self.assertAlmostEqual(sum(percents), 1.0, places=9)

    def test_best_has_top_percent(self):
        # 首推 = 推荐度最高的选项：首推的推荐度和总分都不低于其余参与比较的选项
        # （被同系列严格上位替代的不参与比较，它的总分可能更高）。
        # 例外：不算上位替代的同系列两张总分相差不到 rules.toml [advice] series_near 时稀有度高的排前，
        # 首推的总分可以比同系列的低一级低一点（不到 series_near），推荐度仍是最高的
        near = self.db.rules["advice"]["series_near"]
        for hero, st, offer, rarity, adv in self.each():
            options = {e.chip.name: (e.percent, e.total) for e in compared(adv)}
            if adv.reroll:
                options[REROLL] = (adv.reroll.percent, adv.reroll.value)
            p, v = options[adv.best]
            lows = {pair.low for pair in self.db.series if pair.high == adv.best}
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertTrue(all(p >= q - 1e-12 and (v >= w - 1e-9 or name in lows and w - v < near)
                                    for name, (q, w) in options.items()), options)

    def test_zero_rerolls_never_refresh(self):
        # 刷新次数为 0：没有「刷新」选项，不会首推刷新；有刷新次数而首推刷新时，刷新的期望确实高于最好的候选
        seen = 0
        for hero, st, offer, rarity, adv in self.each():
            with self.subTest(case=repro(st, offer, rarity)):
                if st.rerolls == 0:
                    seen += 1
                    self.assertIsNone(adv.reroll)
                    self.assertNotEqual(adv.best, REROLL)
                elif adv.best == REROLL:
                    self.assertGreater(adv.reroll.value, max(e.total for e in compared(adv)))
        self.assertGreaterEqual(seen, 35)

    def test_reroll_chain_follows_picks(self):
        # 刷新的估值按最多连续刷新几次：剩余刷新比之后的三选一（次数向上取整，再多留 1 次以防估少；之后没有三选一时
        # 留 0 次）多出来的次数，没有多出来的为 1；有多出来的时代价为 0。之后预计没有三选一时多出来的就是剩余刷新次数
        # （随机局面的刷新次数都按读到了算）
        chained = kept_some = 0
        for hero, st, offer, rarity, adv in self.each():
            if adv.reroll is None:
                continue
            need = math.ceil(adv.picks.total - 1e-9)
            kept = min(st.rerolls, need + 1) if need > 0 else 0
            spare = st.rerolls - kept
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertEqual((adv.reroll.kept, adv.reroll.spare), (kept, spare))
                self.assertEqual(adv.reroll.chain, spare if spare >= 1 else 1)
                if spare >= 1:
                    self.assertEqual(adv.reroll.cost, 0.0)
                if adv.picks.total <= 0:
                    self.assertEqual(adv.reroll.chain, st.rerolls)
                chained += adv.reroll.chain > 1
                kept_some += spare >= 1 and kept > 0
        self.assertGreater(chained, 0)  # 随机局面里确实有按连续刷新估值的
        self.assertGreater(kept_some, 0)  # 也有留了几次给之后、多出来的连着刷的

    def test_full_owned(self):
        # 已有 10 个筹码时也照常给结果（持有的筹码数不设上限，之后照常有三选一）
        full = [r for r in self.results if len(r[1].owned) == 10]
        self.assertGreaterEqual(len(full), 70)
        for hero, st, offer, rarity, adv in full:
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertIsNotNone(adv)
                self.assertEqual(len(adv.evals), 3)
                self.assertAlmostEqual(sum(e.percent for e in adv.evals) + (adv.reroll.percent if adv.reroll else 0), 1.0)

    def test_parts_add_up(self):
        # 不计前瞻的分值 = 各分项之和，总分 = 不计前瞻的分值 + 前瞻分；分项名都在规定的名称里
        for hero, st, offer, rarity, adv in self.each():
            for e in adv.evals:
                with self.subTest(case=repro(st, offer, rarity), chip=e.chip.name):
                    self.assertAlmostEqual(e.immediate, sum(p.score for p in e.parts), places=6)
                    self.assertAlmostEqual(e.total, e.immediate + e.lookahead, places=6)
                    self.assertLessEqual({p.label for p in e.parts}, ALLOWED_LABELS)

    def test_deterministic(self):
        # 同样的输入再算一次，全部输出（分值、百分比、理由、附注）完全相同
        for hero, st, offer, rarity, adv in self.each():
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertEqual(fingerprint(advise(self.db, st, offer, rarity)), fingerprint(adv))

    def test_notes_follow_state(self):
        # 附注随局面出现：普通难度、地图待识别、资料库未收录的候选各有一条；首推是刷新、参与比较的候选（被同系列上位替代的
        # 不算）总分又都低于推荐线时有一条（首推是筹码时不加）
        line = self.db.rules["advice"]["recommend_total"]
        low_but_chip = 0  # 总分都低于推荐线、首推却是筹码（这时不加附注）的局面数
        for hero, st, offer, rarity, adv in self.each():
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertEqual(NORMAL_NOTE in adv.notes, st.difficulty == NORMAL)
                self.assertEqual(any(n.startswith("地图：待识别") for n in adv.notes), st.map is None)
                low = all(e.total < line for e in compared(adv))
                self.assertEqual(LOW_NOTE in adv.notes, low and adv.best == REROLL)
                low_but_chip += low and adv.best != REROLL
                for i in adv.unrecorded:
                    self.assertIn(f"第 {i + 1} 个候选筹码「{offer[i]}」{UNRECORDED}：按平均水平计算，不计前瞻", adv.notes)
        self.assertGreater(low_but_chip, 0)  # 随机局面里确实有「都偏低但首推是筹码」的情形（这条分支不是空跑）

    def test_no_score_wording_in_overlay_text(self):
        # 悬浮窗不显示分数：推荐区的附注不写分值说法；未收录、循环往复的整句和短句也不写。
        # 畸变组件附注写「（仅提示，未计入推荐度）」，也不出现「不计分」「不计入」
        seen = set()
        for hero, st, offer, rarity, adv in self.each():
            texts = list(adv.notes)
            for e in adv.evals:
                for p in e.parts:
                    if p.label in ("未收录", "循环往复"):
                        seen.add(p.label)
                        texts += [p.reason, p.short]
            seen.update(n for n in (LOW_NOTE, NORMAL_NOTE) if n in adv.notes)
            seen.update("畸变组件" for n in adv.notes if n.startswith("畸变组件"))
            with self.subTest(case=repro(st, offer, rarity)):
                for t in texts:
                    self.assertIsNone(SCORE_WORDING.search(t), t)
                    self.assertNotIn("价值", t)
                    self.assertNotIn("期望最高分", t)
                    self.assertNotIn("不计分", t)
                    self.assertNotIn("不计入", t)
        # 随机局面里确实出现过这几类文字（免得这条测试悄悄变成空跑）
        self.assertEqual(seen, {"未收录", "循环往复", LOW_NOTE, NORMAL_NOTE, "畸变组件"})

    def test_map_candidates_follow_map_chips(self):
        # 地图已知时只按这张地图算；待识别时，候选和已有筹码里的地图专属筹码把候选地图缩小到包含它的地图
        for hero, st, offer, rarity, adv in self.each():
            with self.subTest(case=repro(st, offer, rarity)):
                if st.map:
                    self.assertEqual(adv.maps, (st.map,))
                    continue
                self.assertTrue(adv.maps)
                for name in (*offer, *st.owned):
                    chip = self.db.chips.get(name)
                    if chip and chip.maps:
                        self.assertTrue(set(adv.maps) <= set(chip.maps), (name, adv.maps))

    def test_lookahead_reason_names_pool_chips(self):
        # 前瞻理由里「X下次出现率」「本局不再出现的高适配度筹码：X」提到的筹码，都在本局的筹码池里：
        # 不是已有筹码、不是候选筹码自己、不是地图发放的，地图专属筹码属于计算用的地图
        pattern = re.compile(r"^(.+?)下次出现率 \d+% → \d+%$")
        lost_prefix = "本局不再出现的高适配度筹码："
        # 被锁掉的高适配度筹码是本次的另一个候选时写「放弃本次的X，之后也不再出现」，不再写进上一句
        here = re.compile(r"^放弃本次的(.+)，之后也不再出现$")
        lost_seen = here_seen = 0
        for hero, st, offer, rarity, adv in [*self.each(), *self.given_up_cases()]:
            maps = set(adv.maps) or {None}
            for e in adv.evals:
                names, lost, given_up = [], [], []
                self.assertNotIn("高分筹码", e.lookahead_reason)  # 界面上写「高适配度筹码」
                for bit in filter(None, e.lookahead_reason.split("；")):
                    m = pattern.match(bit)
                    if m:
                        names.append(m.group(1))
                    elif bit.startswith(lost_prefix):
                        lost += bit.split("：", 1)[1].split("、")
                        lost_seen += 1
                    elif here.match(bit):
                        given_up += here.match(bit).group(1).split("、")
                        here_seen += 1
                with self.subTest(case=repro(st, offer, rarity), chip=e.chip.name):
                    self.assertEqual([n for n in lost if n in offer], [])
                    self.assertEqual([n for n in given_up if n not in offer or n == e.chip.name], [])
                    # 写了本局不再出现的，不再另写一句「X下次出现率 8% → 0%」
                    self.assertEqual([n for n in names if n in lost or n in given_up], [])
                    if e.lookahead_short.startswith("前瞻：放弃本次的"):  # 标签短句写的就是整句里放弃的那一张
                        self.assertIn(e.lookahead_short[len("前瞻：放弃本次的"):], given_up)
                    self.assertNotRegex(e.lookahead_short, "|".join(f"^前瞻：{re.escape(n)}(等)?不再出现$" for n in offer))
                    for name in names + lost:
                        chip = self.db.chips.get(name)
                        self.assertIsNotNone(chip, name)
                        self.assertNotIn(name, st.owned)
                        self.assertNotEqual(name, e.chip.name)
                        self.assertFalse(chip.granted, name)
                        if chip.maps:
                            self.assertTrue(maps & set(chip.maps), (name, maps))
        # 随机局面里确实出现过「本局不再出现的高适配度筹码」和「放弃本次的X」（免得改了句式后这条分支悄悄不再检查）
        self.assertGreater(lost_seen, 0)
        self.assertGreater(here_seen, 0)


class CrossLaunchTest(Base):
    """「同样的输入两次结果完全相同」也要跨启动成立：总分由遍历集合累加，求和顺序随哈希种子变化（engine.tie_key）。
    用两个不同的 PYTHONHASHSEED 各起一个进程，同一批局面的首推必须相同，分值只允许浮点末位的差别。"""

    CODE = (
        "import json, sys\n"
        "from advisor.data import load_db\n"
        "from advisor.engine import advise\n"
        "from advisor.pool import State\n"
        "db = load_db()\n"
        "out = []\n"
        "for c in json.loads(sys.stdin.read()):\n"
        "    adv = advise(db, State(**c['state']), c['offer'], c['rarity'])\n"
        "    out.append([adv.best, [e.total for e in adv.evals], [e.percent for e in adv.evals]])\n"
        "print(json.dumps(out))\n"
    )

    def run_with_seed(self, seed: str, payload: str):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [sys.executable, "-c", self.CODE], input=payload, capture_output=True, text=True, encoding="utf-8",
            cwd=ROOT, env=env, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout)

    def test_same_result_across_hash_seeds(self):
        cases = corpus(self.db)[::5]  # 每个角色 2 个局面
        payload = json.dumps(
            [{"state": dataclasses.asdict(st), "offer": offer, "rarity": rarity} for _, st, offer, rarity in cases],
            ensure_ascii=False,
        )
        a = self.run_with_seed("1", payload)
        b = self.run_with_seed("20261003", payload)
        self.assertEqual(len(a), len(cases))
        for (hero, st, offer, rarity), x, y in zip(cases, a, b):
            with self.subTest(case=repro(st, offer, rarity)):
                self.assertEqual(x[0], y[0])
                for u, v in zip(x[1] + x[2], y[1] + y[2]):
                    self.assertAlmostEqual(u, v, places=6)


class InconsistentInputTest(Base):
    """识别或记账有误、手工输入时，局面可能不合游戏规则：已有筹码占了三四个流派、带着别的地图的专属筹码，
    候选里有重复的筹码或已有筹码，轮次超过进度上限。推荐照常给出，不抛异常，约束照旧成立。"""

    def test_still_gives_valid_advice(self):
        rng = random.Random(SEED + 3)
        names = list(self.db.chips)
        errors = []
        for hero in self.db.heroes:
            for _ in range(3):
                owned = rng.sample(names, rng.randint(3, 10))
                offer = rng.sample(names, 3)
                if rng.random() < 0.3:
                    offer[1] = offer[0]
                if rng.random() < 0.3:
                    offer[2] = owned[0]
                st = State(
                    hero=hero, star=rng.randint(0, 3), round=rng.choice((None, 1, 9, 17, 25)), map=rng.choice((None, *self.db.maps)),
                    owned=tuple(owned), source=rng.choice(("levelup", "shop", "task")), tier=rng.choice((None, 1, 3)),
                    rerolls=rng.randint(0, 5), difficulty=rng.choice((None, NORMAL)), progress_cap=rng.choice((None, 3, 16)),
                    progress=rng.choice((None, 1, 30)),
                    # 档位可能和 difficulty 矛盾（普通难度却是噩梦及以上），以 difficulty 为准
                    difficulty_level=rng.choice((None, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS, "未知档位")),
                )
                try:
                    adv = advise(self.db, st, offer)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}\n  {repro(st, offer)}")
                    continue
                percents = [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])
                with self.subTest(case=repro(st, offer)):
                    self.assertTrue(all(finite(x) for e in adv.evals for x in (e.total, e.percent)))
                    self.assertAlmostEqual(sum(percents), 1.0, places=9)
                    self.assertIn(adv.best, [*offer, REROLL])
                    if st.rerolls == 0:
                        self.assertNotEqual(adv.best, REROLL)
        self.assertEqual(errors, [], "\n".join(errors[:5]))


class NightmareHelmetTest(Base):
    """噩梦及以上（State.difficulty_level 为 LEVEL_NIGHTMARE）只提高摩托头盔-高级（rules.toml [survival] nightmare_*）：
    困难及以上的随机局面里，同一局面按噩梦及以上算时，它的「难度」分项更高、总分不低于读不到难度时；
    同时出现的其他候选筹码的「难度」分项不变。其他候选的分值本身可能随之变化（必拿补分按同一次三选一里未列入玩家思路的
    筹码计，循环往复按之后三选一的期望最高分计），这里不比较。之后任务三选一的等级另按难度那一列取（噩梦及以上按
    噩梦那一列，context.task_level_column），和「难度」分项无关：按噩梦及以上算时也取读不到难度时那一列，只比「难度」
    分项带来的差别。
    [survival] skip_maps 里的地图（水乡古镇）摩托头盔不加「难度」分，两种算法都是 0。"""

    @staticmethod
    def difficulty_parts(e):
        return [(p.score, p.reason, p.short) for p in e.parts if p.label == "难度"]

    def test_gold_helmet_not_lower(self):
        rng = random.Random(SEED + 5)
        helmet = "摩托头盔-高级"
        checked = 0
        for hero in self.db.heroes:
            for _ in range(3):
                st, _, _ = random_case(self.db, rng, hero, 2)
                pool = [c.name for c in player_pool(self.db, st.owned, st.map) if c.name != helmet]
                if helmet in st.owned or len(pool) < 2:
                    continue
                offer = [helmet, *rng.sample(pool, 2)]
                plain = dataclasses.replace(st, difficulty=None, difficulty_level=None, difficulty_name=None)
                night = dataclasses.replace(plain, difficulty_level=LEVEL_NIGHTMARE)
                a = self.by_name(advise(self.db, plain, offer))
                with mock.patch.object(engine_mod.context, "task_level_column", lambda s: None):
                    b = self.by_name(advise(self.db, night, offer))
                with self.subTest(case=repro(night, offer)):
                    checked += 1
                    before = sum(s for s, _, _ in self.difficulty_parts(a[helmet]))
                    after = sum(s for s, _, _ in self.difficulty_parts(b[helmet]))
                    if helmet in self.db.rules["survival"]["skip_maps"].get(st.map, ()):
                        self.assertEqual((before, after), (0, 0))
                    else:
                        self.assertGreater(after, before)
                    self.assertGreaterEqual(b[helmet].immediate, a[helmet].immediate - 1e-9)
                    self.assertGreaterEqual(b[helmet].total, a[helmet].total - 1e-9)
                    for name in offer[1:]:
                        # 读不到难度时整句前面多「难度待识别，按困难及以上计算：」、后半句主语写「这几档难度」，
                        # 分值和短句相同
                        plain_parts = self.difficulty_parts(a[name])
                        self.assertTrue(all(r.startswith(UNKNOWN_DIFFICULTY_LEAD) for _, r, _ in plain_parts))
                        self.assertEqual(self.difficulty_parts(b[name]),
                                         [(s, as_known_difficulty(r), t) for s, r, t in plain_parts])
        self.assertGreaterEqual(checked, 70)


class SeriesSanityTest(Base):
    """同系列严格上位替代（data/series.toml）：随机局面里强行放进一对严格上位替代的筹码，第三个候选随机。
    低一级从不首推、推荐度为 0、不是 Advice.top；其余候选和刷新的推荐度合计 100%，首推的总分不低于其余参与比较的选项；
    前瞻、刷新的期望和代价与没有同系列筹码表时相同，各候选的分值也相同（必选补分只在参与比较的候选之间算，补过分的不比）。

    「没有同系列筹码表时低一级总分更高」的局面本来就少，随机走法碰不碰得到要看严格上位替代有几对，所以另外固定检查两个已知的
    这种局面（INVERTED，和 tests/test_series.py 的 test_inversion_fixed 同一组），再跑随机的。"""

    LIFT = "优先于本次未列入筹码方案的筹码"  # engine.advice._core_first 补分时加在理由后面的说明
    # （局面, 候选, 低一级, 高一级）：没有同系列筹码表时低一级总分更高的已知局面
    INVERTED = (
        (State(hero="璐璐", star=0, rerolls=2), ("夹心饼干-一般", "夹心饼干-可口", "书签"), "夹心饼干-一般", "夹心饼干-可口"),
        # 邦妮那一组（美工刀-初级 / -锋利）在推荐流派只留标记、内置分不计入以后不再倒挂
    )

    def check_pair(self, st: State, offer, low_name: str, high_name: str, plain_db) -> bool:
        """一个局面的检查；返回没有同系列筹码表时低一级的总分是否更高。"""
        adv = advise(self.db, st, list(offer))
        plain = advise(plain_db, st, list(offer))
        ev, before = self.by_name(adv), self.by_name(plain)
        low = ev[low_name]
        with self.subTest(case=repro(st, offer)):
            self.assertIn(low.superseded_by, offer)
            self.assertEqual(low.percent, 0.0)
            self.assertNotEqual(adv.best, low_name)
            self.assertIsNone(adv.evals[adv.top].superseded_by)
            self.assertIn(adv.best, (adv.evals[adv.top].chip.name, REROLL))
            percents = [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])
            self.assertAlmostEqual(sum(percents), 1.0, places=9)
            best_value = adv.reroll.value if adv.best == REROLL else ev[adv.best].total
            self.assertTrue(all(best_value >= e.total - 1e-9 for e in compared(adv)))
            for a, b in zip(adv.evals, plain.evals):
                self.assertEqual(a.lookahead, b.lookahead)
                if not any(self.LIFT in part.reason for part in (*a.parts, *b.parts)):
                    self.assertEqual(a.total, b.total)
            if adv.reroll:
                self.assertEqual((adv.reroll.expected, adv.reroll.cost), (plain.reroll.expected, plain.reroll.cost))
        return before[low_name].total > before[high_name].total

    def test_low_never_first(self):
        rng = random.Random(SEED + 7)
        strict = [p for p in self.db.series if p.strict]
        plain_db = dataclasses.replace(self.db, series=())
        fixed = [self.check_pair(st, offer, low, high, plain_db) for st, offer, low, high in self.INVERTED]
        self.assertEqual(fixed, [True] * len(self.INVERTED))  # 固定的两个局面确实是低一级更高
        checked = 0
        for hero in self.db.heroes:
            for _ in range(4):
                st, _, _ = random_case(self.db, rng, hero, 2)
                p = rng.choice(strict)
                pool = [c.name for c in player_pool(self.db, st.owned, st.map) if c.name not in (p.low, p.high)]
                if p.low in st.owned or p.high in st.owned or not pool:
                    continue
                offer = [p.low, p.high, rng.choice(pool)]
                rng.shuffle(offer)
                checked += 1
                self.check_pair(st, offer, p.low, p.high, plain_db)
        self.assertGreaterEqual(checked, 100)


class RerollChainSanityTest(Base):
    """最后一次三选一（本次之后预计没有筹码三选一）时，「刷新」按最多连续刷新到剩余次数用完估值（engine.Scorer.reroll）。
    随机局面改成 3 星、第 13 轮以后（[picks] end_round）、进度条走到头（剩余轮数 0：3 星以后还没到进度上限时之后的三选一
    有下限，不估成 0）、剩余刷新 2~5 次：和同一局面只剩 1 次刷新时比，刷新的估值和出现更好筹码的概率都不降低，三个候选的
    分值不变；推荐度合计 100%，首推的估值不低于其余参与比较的选项。"""

    def test_chain_not_lower(self):
        rng = random.Random(SEED + 9)
        end = self.db.rules["picks"]["end_round"]
        checked = chained_first = 0
        for hero in self.db.heroes:
            for _ in range(3):
                st, offer, rarity = random_case(self.db, rng, hero, 2)
                st = dataclasses.replace(st, star=3, round=rng.randint(end, end + 4), progress_cap=16, progress=16,
                                         rerolls=rng.randint(2, 5))
                adv = advise(self.db, st, offer, rarity)
                if adv.picks.total > 0 or adv.reroll is None:
                    continue
                single = advise(self.db, dataclasses.replace(st, rerolls=1), offer, rarity)
                checked += 1
                chained_first += adv.best == REROLL and single.best != REROLL
                with self.subTest(case=repro(st, offer, rarity)):
                    self.assertEqual(adv.reroll.chain, st.rerolls)
                    self.assertEqual(single.reroll.chain, 1)
                    self.assertEqual(adv.reroll.cost, 0)
                    self.assertGreaterEqual(adv.reroll.value, single.reroll.value - 1e-9)
                    self.assertGreaterEqual(adv.reroll.p_better, single.reroll.p_better - 1e-12)
                    self.assertTrue(0 <= adv.reroll.p_better <= 1)
                    for a, b in zip(adv.evals, single.evals):
                        self.assertAlmostEqual(a.total, b.total, places=9)
                    percents = [e.percent for e in adv.evals] + [adv.reroll.percent]
                    self.assertAlmostEqual(sum(percents), 1.0, places=9)
                    best_value = adv.reroll.value if adv.best == REROLL else self.by_name(adv)[adv.best].total
                    self.assertTrue(all(best_value >= e.total - 1e-9 for e in compared(adv)))
                    self.assertGreaterEqual(best_value, adv.reroll.value - 1e-9)
        self.assertGreaterEqual(checked, 70)
        self.assertGreater(chained_first, 0)  # 随机局面里确实有因连续刷新改为首推刷新的


class TeammateNeedSanityTest(Base):
    """队友刚需排第二（engine.advice._need_second）：随机局面加一名在刚需名单里的真人队友，候选里换进一张能提供其刚需的筹码
    （按原来的分布，能被提的局面只占 0.4% 左右）。
    首推和不设刚需名单（db.needs 为空）时相同；「刚需」分项最多一个；被提的筹码按推荐度正好排第二；推荐度合计 100%。
    默认推荐的四档用测试用的那一份（tests/support/player_layer.toml）：只按角色特性算时，这一批随机局面里一个被提的都没有，
    「确实有被提到第二位的」查不到。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.db = with_player_layer(cls.db)

    def test_second_only(self):
        rng = random.Random(SEED + 11)
        no_needs = dataclasses.replace(self.db, needs={})
        names = sorted(self.db.needs)
        checked = lifted = 0
        for hero in self.db.heroes:
            for _ in range(6):
                st, offer, rarity = random_case(self.db, rng, hero, 2)
                mate = rng.choice([n for n in names if n != hero])
                others = [t for t in st.teammates if t.hero not in self.db.needs][:2]
                st = dataclasses.replace(st, teammates=(Teammate(mate), *others))
                gives = sorted(c for g in self.db.needs[mate].gives for c in g.chips if c not in st.owned and c not in offer)
                if gives:
                    slot = rng.randrange(3)
                    offer = list(offer)
                    offer[slot] = rng.choice(gives)
                    if rarity is not None:
                        rarity = list(rarity)
                        rarity[slot] = self.db.chips[offer[slot]].rarity
                adv = advise(self.db, st, offer, rarity)
                plain = advise(no_needs, st, offer, rarity)
                checked += 1
                with self.subTest(case=repro(st, offer, rarity)):
                    self.assertEqual(adv.best, plain.best)
                    needs = [e for e in adv.evals if any(p.origin == "need" for p in e.parts)]
                    self.assertLessEqual(len(needs), 1)
                    percents = [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])
                    self.assertAlmostEqual(sum(percents), 1.0, places=9)
                    if needs:
                        lifted += 1
                        ranked = sorted(percents, reverse=True)
                        self.assertAlmostEqual(needs[0].percent, ranked[1], places=12)
        self.assertGreaterEqual(checked, 100)
        self.assertGreater(lifted, 0)  # 随机局面里确实有被提到第二位的


# ------------------------------------------------------------ 3：资料库未收录的筹码


class UnrecordedTest(Base):
    """资料库未收录的候选筹码按中性分计算（使用说明：「推荐按同稀有度筹码的平均水平计算，不计前瞻」；engine.Scorer.neutral_score）。"""

    STATES = (
        dict(hero="邦妮", star=1, round=4, map="异变图书馆", rerolls=3),
        dict(hero="凛", star=2, round=8, map="水乡古镇", owned=("标记喷罐", "大背包"), rerolls=0),
        dict(hero="芬妮", star=3, round=12, map="园林中庭", owned=("银行卡-余额多", "缓冲盾牌", "紫色飞星"), rerolls=2,
             difficulty=NORMAL),
        dict(hero="阿尔", star=0, round=None, map="星趴·梦想号", rerolls=2),
    )

    def same_rarity_scores(self, st: State, rarity):
        """计算用的每张地图上，筹码池里同稀有度（不知道稀有度时为全部）筹码的分值，不含前瞻。"""
        scorer = Scorer(self.db, st)
        owned = scorer.known_owned
        values = []
        for m in scorer.maps:
            pool = player_pool(self.db, owned, m)
            same = [c for c in pool if not rarity or c.rarity == rarity]
            values += [scorer.score(c.name, owned, st.round, m) for c in same]
        return values

    def test_neutral_is_same_rarity_average(self):
        for kw in self.STATES:
            st = State(**kw)
            for rarity in (*RARITIES, None):
                adv = advise(self.db, st, ["新筹码甲", "缓冲盾牌" if "缓冲盾牌" not in st.owned else "书签", "夹心饼干-一般"],
                             rarity=[rarity, None, None])
                new = adv.evals[0]
                values = self.same_rarity_scores(st, rarity)
                with self.subTest(hero=st.hero, rarity=rarity):
                    self.assertTrue(new.unrecorded)
                    self.assertEqual(adv.unrecorded, [0])
                    self.assertEqual([p.label for p in new.parts], ["未收录"])
                    self.assertEqual((new.lookahead, new.lookahead_reason, new.official), (0.0, "", 0.0))
                    self.assertAlmostEqual(new.total, new.immediate)
                    self.assertAlmostEqual(new.total, sum(values) / len(values), places=6)
                    self.assertTrue(min(values) - 1e-9 <= new.total <= max(values) + 1e-9)
                    self.assertEqual(new.chip.rarity, rarity or "")

    def test_neutral_on_unknown_map_stays_in_range(self):
        # 地图待识别时，中性分按候选地图上的同稀有度筹码平均，落在各地图分值的范围内
        st = State(hero="米米", star=1, round=5, rerolls=2)
        for rarity in RARITIES:
            adv = advise(self.db, st, ["书签", "新筹码乙", "夹心饼干-一般"], rarity=[None, rarity, None])
            values = self.same_rarity_scores(st, rarity)
            with self.subTest(rarity=rarity):
                self.assertGreater(len(adv.maps), 1)
                self.assertAlmostEqual(adv.evals[1].total, sum(values) / len(values), places=6)

    def test_neutral_independent_of_name_and_slot(self):
        # 中性分只看稀有度和局面：换个名字、换个位置，分值不变
        st = State(hero="邦妮", star=1, round=4, map="异变图书馆")
        totals = set()
        for slot in range(3):
            for name in ("新筹码甲", "某个从没见过的筹码"):
                offer = ["书签", "夹心饼干-一般", "会员推荐信"]
                offer[slot] = name
                rarity = [None, None, None]
                rarity[slot] = "紫"
                adv = advise(self.db, st, offer, rarity)
                totals.add(round(adv.evals[slot].total, 9))
        self.assertEqual(len(totals), 1, totals)

    def test_removed_and_all_unrecorded(self):
        # 已移除的筹码（不在筹码表里）同样按未收录处理；三个都未收录时照常给出推荐，百分比合计 100%
        adv = advise(self.db, State(hero="照", star=2, round=6, map="御魂庆典", rerolls=2), ["手电筒-一般", "新筹码甲", "新筹码乙"],
                     rarity=["紫", "金", None])
        self.assertEqual(adv.unrecorded, [0, 1, 2])
        self.assertIn(adv.best, ["手电筒-一般", "新筹码甲", "新筹码乙", REROLL])
        self.assertAlmostEqual(sum(e.percent for e in adv.evals) + adv.reroll.percent, 1.0)
        self.assertTrue(all(finite(e.total) and e.lookahead == 0 for e in adv.evals))

    def test_core_lift_counts_unrecorded_as_unlisted(self):
        # 未收录的筹码不在玩家思路里：同一次三选一里有必拿时，必拿照常补到比它高 core_lead 分；未收录筹码本身的中性分不变
        lead = self.db.rules["synergy"]["core_lead"]
        st = State(hero="凛", star=1, round=3, map="异变图书馆")
        adv = advise(self.db, st, ["大背包", "新筹码甲", "夹心饼干-一般"], rarity=[None, "金", None])
        ev = adv.evals
        self.assertEqual(adv.best, "大背包")
        self.assertGreaterEqual(ev[0].total, ev[1].total + lead - 1e-6)
        values = self.same_rarity_scores(st, "金")
        self.assertAlmostEqual(ev[1].total, sum(values) / len(values), places=6)


# ------------------------------------------------------------ 4：显而易见的错误


class CoreFirstTest(Base):
    """玩家思路里「必拿」的筹码和两个与该角色无关的蓝色筹码一起出现时，首推必拿（engine.advice._core_first）。

    例外：必拿只和同一次三选一里未列入玩家思路的筹码比，不和「刷新」比（必拿在场时也可能首推刷新）。
    所以严格的检查用刷新次数 0；有刷新次数时只要求必拿是三个候选里最高的，首推是必拿或刷新。
    资料里现在没有角色写玩家思路，四档用测试用的那一份（tests/support/player_layer.toml）。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.db = with_player_layer(cls.db)

    def core_cases(self):
        """（角色，必拿筹码，为满足规则条件需要的已有筹码）：四档里的必拿，以及分值不低于必拿档的玩家思路规则。"""
        line = self.db.rules["synergy"]["core"]
        out = []
        for hero, ps in self.db.player_synergy.items():
            for chip, tier in ps.tiers.items():
                if tier == "core":
                    out.append((hero, chip, ()))
            for rule in ps.rules:
                if rule.score < line:
                    continue
                for chip in sorted(rule.chips):
                    need = tuple(sorted(rule.needs_owned - {chip}))[:1]
                    out.append((hero, chip, need))
        return out

    def test_core_cases_listed(self):
        # 测试用的档位里有必拿的角色和筹码（档位变了这里跟着改，免得下面的测试悄悄变成空跑）
        cases = self.core_cases()
        heroes = {h for h, _, _ in cases}
        # 赛克斯：按攻略视频整理的玩家思路（两张飞星必选）。
        # 现在没有分值达到必选档的规则，必选都来自四档（带条件的规则补分见 test_engine 的 CoreVsRefreshTest）
        self.assertEqual(heroes, {"凛", "芬妮", "赛克斯"})
        self.assertEqual(len(cases), 8)
        for hero in heroes:
            self.assertGreaterEqual(len(unrelated_blues(self.db, hero)), 2, hero)

    def test_core_beats_two_unrelated_blues(self):
        rng = random.Random(SEED + 1)
        failures = []
        checked = 0
        for hero, core, need in self.core_cases():
            chip = self.db.chips[core]
            blues = unrelated_blues(self.db, hero)
            for _ in range(12):
                maps = chip.maps or self.db.maps
                true_map = rng.choice(maps)
                owned = list(need)
                for _ in range(rng.randint(0, 3)):
                    pool = [c.name for c in player_pool(self.db, owned, true_map) if c.name != core and c.name not in blues]
                    owned.append(rng.choice(pool))
                pool = {c.name for c in player_pool(self.db, owned, true_map)}
                fillers = [b for b in blues if b in pool]
                if core not in pool or len(fillers) < 2:
                    continue  # 已有筹码占满了两个流派，必拿（或无关的蓝色筹码）不会出现
                pair = rng.sample(fillers, 2)
                offer = pair[:]
                offer.insert(rng.randrange(3), core)
                kw = dict(
                    hero=hero, star=rng.randint(0, 3), round=rng.choice((None, *range(1, 19))), owned=tuple(owned),
                    map=rng.choice((true_map, None)), difficulty=rng.choice((None, NORMAL)),
                    source=rng.choice(("levelup", "shop", "task")),
                )
                st = State(**kw, rerolls=0)
                adv = advise(self.db, st, offer)
                checked += 1
                if adv.best != core:
                    failures.append(f"{hero}：{offer} → {adv.best}\n  {repro(st, offer)}")
                st = State(**kw, rerolls=rng.randint(1, 5))
                adv = advise(self.db, st, offer)
                ev = self.by_name(adv)
                top = max(compared(adv), key=lambda e: e.total).chip.name
                if top != core or adv.best not in (core, REROLL):
                    failures.append(f"{hero}：{offer} → {adv.best}（候选最高 {top}）\n  {repro(st, offer)}")
                elif adv.best == REROLL:
                    self.assertGreater(adv.reroll.value, ev[core].total)
        self.assertGreaterEqual(checked, 80)
        self.assertEqual(failures, [], "\n".join(failures[:10]))


class SupportFlyingStarTest(Base):
    """阿尔以外的 9 名辅助角色在任何星数、轮次下，金色飞星和星币锤同时出现时金色飞星总分更高
    （大部分辅助角色不能和首领硬碰硬，金色飞星优于星币锤）。
    阿尔单独查：他的星光层数直接换成属性，星币锤是他的适配筹码，内置分计入总分时两张接近。

    玩家思路覆盖自动层的角色：芬妮的玩家思路把金色飞星列为必拿（星币锤未列入，按必拿补分），金色飞星照样更高，照常检查。
    """

    OFFER = ["星币锤", "书签", "金色飞星"]
    OWNED = ((), ("紫色飞星",), ("银行卡-余额少", "缓冲盾牌"))

    def states(self, hero: str):
        """星数 0~3 × 普通 / 困难及以上 × 轮次（待识别、第 1 轮到进度上限），地图为异变图书馆（真人对局那张）、没有已有筹码；
        另两组已有筹码取几个关键轮次（第 5 轮是金色飞星成长分的最后一轮，第 6 轮起没有），再加地图待识别时的两个轮次。"""
        cap = max(self.db.map_info["异变图书馆"].progress_caps.values())
        for star in range(4):
            for difficulty in (None, NORMAL):
                for rnd in (None, *range(1, cap + 1)):
                    yield State(hero=hero, star=star, round=rnd, map="异变图书馆", difficulty=difficulty)
                for owned in self.OWNED[1:]:
                    for rnd in (None, 1, 5, 6, 9, 12, 15, cap):
                        yield State(hero=hero, star=star, round=rnd, map="异变图书馆", owned=owned, difficulty=difficulty)
                for rnd in (None, 9):
                    yield State(hero=hero, star=star, round=rnd, difficulty=difficulty)

    def losses(self, hero: str) -> list[str]:
        out = []
        for st in self.states(hero):
            ev = self.by_name(advise(self.db, st, self.OFFER))
            gold, hammer = ev["金色飞星"].total, ev["星币锤"].total
            if gold <= hammer:
                out.append(f"{st.star} 星 第 {st.round} 轮 地图 {st.map} 难度 {st.difficulty} 已有 {st.owned}："
                           f"金色飞星 {gold:.1f} ≤ 星币锤 {hammer:.1f}\n  {repro(st, self.OFFER)}")
        return out

    def test_trait_holders(self):
        holders = {h for h, hs in self.db.hero_synergy.items() if "辅助定位" in hs.traits}
        self.assertEqual(holders, set(SUPPORT))

    def test_gold_star_over_hammer(self):
        # 阿尔单独见下一条
        for hero in SUPPORT:
            if hero == "阿尔":
                continue
            with self.subTest(hero=hero):
                bad = self.losses(hero)
                self.assertEqual(bad, [], f"{len(bad)} 处\n" + "\n".join(bad[:5]))

    # 阿尔：不计收入折算和前瞻时金色飞星高 57 分（金色飞星：角色专属 +65、另叠「辅助定位」「星光成长」+24、推荐流派 +15，
    # 还没有星光筹码时另有开套预判；星币锤：「星光成长」+40 另叠「经济」12、「辅助定位」-20、推荐流派 +15）。
    # 这张网格里金色飞星至少高 54 分（3 星、后面的轮次，前瞻和收入折算都小的时候）。
    def test_al_hammer_far_behind(self):
        margins = []
        for st in self.states("阿尔"):
            ev = self.by_name(advise(self.db, st, self.OFFER))
            margins.append(ev["金色飞星"].total - ev["星币锤"].total)
        self.assertGreater(min(margins), 50)  # 网格里没有星币锤更高的局面，也没有两张接近的局面

    # 内置分按 ×0.5 计入总分、前瞻不打折时两张才接近（星币锤有角色分 +50、前瞻也大）：这时最接近的是 2 星、轮次待识别、
    # 已持有银行卡-余额少的局面（已进入星光，金色飞星不预判），差约 6.3 分，网格里仍没有星币锤更高的局面。这一条查的是
    # 分项加起来的先后，用内置分照旧计入、前瞻不打折的资料库副本
    def test_al_hammer_never_ahead_with_official_score(self):
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in self.db.rules.items()}
        rules["official"]["scale"] = 0.5
        rules["lookahead"]["weight"] = 1.0
        db = dataclasses.replace(self.db, rules=rules)
        margins = []
        for st in self.states("阿尔"):
            ev = self.by_name(advise(db, st, self.OFFER))
            margins.append((ev["金色飞星"].total - ev["星币锤"].total, st))
        closest, st = min(margins, key=lambda x: x[0])
        self.assertEqual((st.star, st.round, st.owned), (2, None, ("银行卡-余额少", "缓冲盾牌")))
        self.assertGreater(closest, 5)
        self.assertGreater(min(m for m, _ in margins), 0)


class PoolTest(Base):
    """已经持有的筹码、不在本局地图的地图专属筹码、锁定两个流派后其余流派的筹码，都不进入筹码池推算
    （pool.player_pool；rules.toml [pool] max_schools：最多占几个流派，占满后其余流派移出筹码池）。"""

    def test_player_pool_rules(self):
        rng = random.Random(SEED + 2)
        line = self.db.rules["pool"]["max_schools"]
        self.assertEqual(line, 2)
        for m in (*self.db.maps, None):
            for _ in range(15):
                owned = make_owned(self.db, rng, m, rng.randint(0, 10))
                schools = owned_schools(self.db, owned)
                pool = player_pool(self.db, owned, m)
                with self.subTest(map=m, owned=owned):
                    self.assertTrue(pool)
                    for c in pool:
                        self.assertNotIn(c.name, owned)
                        self.assertFalse(c.granted, c.name)
                        if c.maps:
                            self.assertIn(m, c.maps)
                        if len(schools) >= line and c.school:
                            self.assertIn(c.school, schools)

    def test_two_schools_lock_the_rest(self):
        # 任意两个流派各持有一张：其余两个流派的筹码在每张地图上都不出现，这两个流派和没有流派的筹码照常出现
        reps = {s: next(c.name for c in self.db.chips.values() if c.school == s and not c.maps) for s in SCHOOLS}
        for a in SCHOOLS:
            for b in SCHOOLS:
                if a >= b:
                    continue
                for m in (*self.db.maps, None):
                    pool = player_pool(self.db, (reps[a], reps[b]), m)
                    with self.subTest(schools=(a, b), map=m):
                        self.assertEqual({c.school for c in pool}, {None, a, b})

    def test_engine_pools_exclude_owned_and_off_map(self):
        # 推荐过程中每一次推算筹码池（前瞻、刷新期望、中性分、循环往复、美工刀和配套筹码的预判）都不含已有筹码、
        # 地图发放的筹码和别的地图的专属筹码；已占满两个流派时也不含其余流派。地图已知时只按这张地图推算
        db = self.db
        users = engine_pool_users()
        calls = []
        callers = set()  # 经过替换的函数取筹码池的模块

        def spy_for(module: str):
            def spy(db_, owned, map_name, exclude=()):
                pool = player_pool(db_, owned, map_name, exclude)
                calls.append((map_name, pool))
                callers.add(module)
                return pool
            return spy

        cases = corpus(db)[::3]
        cases.append(("美咲", State(hero="美咲", star=1, round=5, map="星趴·梦想号", owned=("普通瞄具", "医疗箱-紧急治疗"), rerolls=3),
                      ["美工刀-初级", "大碗炖肉", "循环往复"], None))
        cases.append(("玲玲", State(hero="玲玲", star=2, round=7, rerolls=2), ["永动机", "电磁炮", "新筹码甲"], None))
        with contextlib.ExitStack() as stack:
            for module in users:
                stack.enter_context(mock.patch.object(module, "player_pool", spy_for(module.__name__)))
            for hero, st, offer, rarity in cases:
                calls.clear()
                adv = advise(db, st, offer, rarity)
                locked = owned_schools(db, st.owned)
                locked = locked if len(locked) >= db.rules["pool"]["max_schools"] else None
                with self.subTest(case=repro(st, offer, rarity)):
                    # 预计之后没有三选一（前瞻记 0）又不能刷新时，不推算筹码池
                    if adv.picks.total > 0 or adv.reroll is not None:
                        self.assertTrue(calls)
                    for map_name, pool in calls:
                        if st.map:
                            self.assertEqual(map_name, st.map)
                        else:
                            self.assertTrue(map_name is None or map_name in db.maps)
                        for c in pool:
                            self.assertNotIn(c.name, st.owned)
                            self.assertFalse(c.granted, c.name)
                            if c.maps:
                                self.assertIn(map_name, c.maps, c.name)
                            if locked and c.school:
                                self.assertIn(c.school, locked, c.name)
        # 每个取筹码池的模块都确实换到了（替换打在不起作用的地方时，这个模块的推算就不经过上面的检查）
        self.assertEqual(callers, {m.__name__ for m in users})

    # owned_schools 最多取前两个流派：已占满两个流派后，候选里出现第三个流派的筹码（识别或记账有误、手工输入的已有筹码）时，前瞻不能按「拿了以后该流派
    # 重新回到筹码池」计算（pool.player_pool 要是用 owned_schools 的全部流派判断锁定，三个流派都会保留），充能筹码的开套预判
    # 也不能按配套筹码还会出现计分。该筹码本不在筹码池里，拿了也不会让它的流派回来，前瞻应为 0、不应有开套预判。
    # 例：取全部流派时，姬梦朝已有标靶、电流剑，维生素药丸的前瞻会是 +115（「友情徽章下次出现率 0% → 29%」）；
    # 玲玲已有银行卡-余额少、缓冲盾牌，永动机会另有开套预判约 +10。
    def test_third_school_chip_not_overvalued(self):
        reps = {"星光": "银行卡-余额少", "治愈": "缓冲盾牌", "标记": "标靶", "充能": "电流剑"}
        bad = []
        for hero in self.db.heroes:
            for a, b in (("星光", "治愈"), ("标记", "充能"), ("治愈", "标记"), ("星光", "充能")):
                owned = (reps[a], reps[b])
                st = State(hero=hero, star=1, round=4, map="异变图书馆", owned=owned)
                for c in self.db.chips.values():
                    if not c.school or c.school in (a, b) or c.maps:
                        continue
                    scorer = Scorer(self.db, st, offer=[c.name])
                    la = scorer.lookahead(c.name, owned)
                    charge = [p for p in scorer.parts(c.name, owned, st.round) if p.origin == "school_build"]
                    if abs(la) > 0.5 or charge:  # 前瞻失真可正可负
                        bad.append((la, f"{hero} 已有 {owned}：{c.name} 前瞻 {la:+.1f}"
                                        f"{'，开套预判 %+.1f' % charge[0].score if charge else ''}"
                                        f"（{scorer.lookahead_reason(c.name, owned)}）"))
        bad.sort(reverse=True)
        self.assertEqual(bad, [], f"{len(bad)} 处，最大的几处：\n" + "\n".join(t for _, t in bad[:5]))


# ------------------------------------------------------------ 5：常识局面


class CommonSenseTest(Base):
    """每个角色一组常识局面：1 星第 3 轮、没有已有筹码、困难及以上、刷新次数按默认（3 次），候选为该角色在适配表里加分最高的筹码
    （玩家思路层；没有列入玩家思路时为自动层，含特性和角色专属规则；并列最高的逐个检查）+ 两个与该角色无关的蓝色筹码。
    首推应是加分最高的那个。加分最高的是地图专属筹码时，地图取它所在的地图；否则取异变图书馆（真人对局那张）。"""

    def top_chips(self, hero: str):
        endless = self.db.rules["endless_cycle"]["chip"]
        scorer = Scorer(self.db, State(hero=hero, star=1, round=3))
        bonus = {}
        for c in self.db.chips.values():
            if c.granted or c.name == endless:
                continue
            m = c.maps[0] if c.maps else None
            bonus[c.name] = sum(p.score for p in scorer.parts(c.name, (), 3, m) if p.label in SYNERGY_LAYER)
        top = max(bonus.values())
        return top, [n for n, v in bonus.items() if v == top]

    def test_top_synergy_chip_first(self):
        mismatches = []
        checked = 0
        for hero in self.db.heroes:
            top, names = self.top_chips(hero)
            blues = unrelated_blues(self.db, hero)[:2]
            if top <= 0 or len(blues) < 2:
                mismatches.append(f"{hero}：适配表里没有加分的筹码（{top}）或无关的蓝色筹码不足两个（{blues}）")
                continue
            for name in names:
                chip = self.db.chips[name]
                offer = [blues[0], name, blues[1]]
                st = State(hero=hero, star=1, round=3, map=chip.maps[0] if chip.maps else "异变图书馆",
                           rerolls=default_rerolls(self.db, 1))
                adv = advise(self.db, st, offer)
                checked += 1
                if adv.best != name:
                    ev = self.by_name(adv)
                    mismatches.append(
                        f"{hero}：{name}（加分 {top:+.0f}，总分 {ev[name].total:.1f}）→ 首推 {adv.best}\n  {repro(st, offer)}"
                    )
        self.assertGreaterEqual(checked, 35)
        self.assertEqual(mismatches, [], "\n".join(mismatches))


if __name__ == "__main__":
    unittest.main()
