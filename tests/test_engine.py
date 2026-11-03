import dataclasses
import io
import itertools
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from advisor import cli
from advisor.data import (
    GENERAL_TRAIT,
    HARD,
    LEVEL_NIGHTMARE,
    NORMAL,
    SOURCE_GUIDE,
    Combo,
    Rule,
    Source,
    TeammateRule,
    _merge_players,
    load_db,
)
from advisor.engine import (
    CLASS_SHIFT_REASON,
    CLASS_SHIFT_SHOP_REASON,
    HEAL_DISCOUNT_ORIGIN,
    LOW_NOTE,
    NORMAL_NOTE,
    REROLL,
    SHORT_LIMIT,
    Scorer,
    advise,
    default_rerolls,
    hero_classes,
    hero_tendency,
    mutator_notes,
    reward_rounds_left,
)
from advisor.engine.advice_text import free_short
from advisor.engine.chances import heal_chance, heal_discount_score, heal_knife, knife_chance, partner_chance
from advisor.engine.context import endless_sources, harmonic, mixed_sources, next_sources, split_picks
from advisor.engine.lookahead import future, future_setup, lookahead_on
from advisor.engine.parts import endless_value, teammate_parts
from advisor.engine.reroll import reroll_dists
from advisor.pool import (
    State,
    Teammate,
    appear_prob,
    class_tags,
    difficulty_of,
    expected_best,
    owned_schools,
    player_pool,
    prob_above,
    rarity_row,
    slot_dists,
)
from tests.support import samples
from tests.support.player_layer import with_player_layer

OFFICIAL = {"基础分", "成长分", "角色分", "标签分", "关键词分"}
ROOT = Path(__file__).resolve().parents[1]


def character_blocks() -> list[str]:
    """mechanics/characters.md 里每个角色的一段（「**名字（称号）**」起，到下一个角色为止）。heroes.json 不存技能
    说明和等级奖励，按机制文档核对（各段的第一行含角色名）。"""
    blocks: list[list[str]] = []
    for line in (ROOT / "mechanics" / "characters.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("**"):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return ["\n".join(b) for b in blocks]


ALLOWED_LABELS = OFFICIAL | {
    "玩家思路", "角色专属规则", "特性", "其他特性", "推荐流派", "组合", "队友", "难度", "收入折算", "循环往复", "职业倾向", "未收录",
}
# 悬浮窗上的文字不写分值：总分、中性分、平均分、推荐线、推荐分，以及「50 分」「（… 分）」这类写法。
# 畸变组件附注的结尾是「（仅提示，未计入推荐度）」，不用为「不计分）」留例外。
SCORE_WORDING = re.compile(r"总分|中性分|平均分|推荐线|推荐分|\d\s*分|分）")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def official(self, hero, chip, owned=(), rnd=None):
        """游戏内置推荐分各项的原值（游戏公式，未乘 official.scale）。"""
        scorer = Scorer(self.db, State(hero=hero, owned=tuple(owned), round=rnd))
        parts = scorer.official_parts(chip, tuple(owned), rnd, scale=1.0)
        return {p.label: p.score for p in parts if p.label in OFFICIAL}

    def run_case(self, hero, offer, db=None, **kw):
        """db 不给时用这个测试类的资料库（self.db）。"""
        db = db or self.db
        kw.setdefault("rerolls", default_rerolls(db, kw.get("star", 0), kw.get("owned", ())))
        return advise(db, State(hero=hero, **kw), list(offer))

    def by_name(self, adv):
        return {e.chip.name: e for e in adv.evals}


class PlayerBase(Base):
    """资料库换上测试用的默认推荐档位（tests/support/player_layer.toml，见 tests.support.player_layer）：
    data/synergy.toml 现在没有角色写玩家思路层，测这一层的机制（四档、必选补分、带条件的规则）用这一份。"""

    @classmethod
    def setUpClass(cls):
        cls.db = with_player_layer(load_db())


class OfficialScoreTest(Base):
    def test_fit_and_tag(self):
        # 基础 60 + 适配角色 100 + 攻击标签 40
        self.assertEqual(
            self.official("邦妮", "拳击手套-高级"), {"基础分": 60, "角色分": 100, "标签分": 40}
        )

    def test_minor_fit(self):
        self.assertEqual(self.official("橘雪莉", "电磁炮")["角色分"], 50)

    def test_growth_depends_on_round(self):
        self.assertEqual(self.official("阿尔", "银行卡-余额多", rnd=2)["成长分"], 50)
        self.assertNotIn("成长分", self.official("阿尔", "银行卡-余额多", rnd=3))
        self.assertNotIn("成长分", self.official("阿尔", "银行卡-余额多"))

    def test_keyword_needs_same_school(self):
        self.assertNotIn("关键词分", self.official("邦妮", "普通瞄具"))
        self.assertEqual(self.official("邦妮", "普通瞄具", owned=["标靶"])["关键词分"], 30)
        self.assertNotIn("关键词分", self.official("邦妮", "普通瞄具", owned=["缓冲盾牌"]))

    def test_keyword_skips_charge_school(self):
        # 同游戏的算法：关键词分只算治愈、星光、标记，充能流派的筹码不管已持有几张充能筹码都没有这一项
        self.assertEqual(self.db.rules["official"]["keyword_schools"], ["治愈", "星光", "标记"])
        for owned in (["电流核心"], ["永动机", "电流剑"]):
            with self.subTest(owned=owned):
                self.assertNotIn("关键词分", self.official("照", "电磁炮", owned=owned))
                self.assertNotIn("关键词分", self.official("照", "能量回收", owned=owned))
        # 照只持有电流核心时的电磁炮：只剩基础分 30，不到推荐线（实机样本上没有推荐角标）
        self.assertEqual(self.official("照", "电磁炮", owned=["电流核心"]), {"基础分": 30})
        self.assertEqual(self.official("邦妮", "美工刀-初级", owned=["缓冲盾牌"])["关键词分"], 30)  # 其余流派照算

    def test_fit_and_minor_fit_add_up(self):
        # 同游戏的算法：适配、普通适配两张名单分别算，同一个角色两张都在时两项相加。
        # 现有资料里没有同时在两张名单里的角色，造一个
        self.assertFalse([c.name for c in self.db.chips.values() if c.fit & c.fit_minor])
        chip = dataclasses.replace(self.db.chips["电磁炮"], fit=frozenset({"橘雪莉"}), fit_minor=frozenset({"橘雪莉"}))
        db = dataclasses.replace(self.db, chips={**self.db.chips, "电磁炮": chip})
        scorer = Scorer(db, State(hero="橘雪莉"))
        roles = [p.score for p in scorer.official_parts("电磁炮", (), None, scale=1.0) if p.label == "角色分"]
        self.assertEqual(roles, [100, 50])
        # 真实资料里只在普通适配名单里（+50），两张都在时再加适配的 +100
        before = Scorer(self.db, State(hero="橘雪莉")).official_raw("电磁炮", (), None)
        self.assertEqual(scorer.official_raw("电磁炮", (), None), before + 100)

    def test_no_tag_for_other_class(self):
        self.assertNotIn("标签分", self.official("邦妮", "会员推荐信"))

    def test_class_follows_chip_points(self):
        # 英文 wiki：职业分值 100 以上才算属于该职业。可以同时属于几个，也可以一个都不属于
        self.assertEqual(self.db.rules["official"]["class_threshold"], 100)
        self.assertEqual(hero_classes(self.db, "邦妮", ()), {"攻击"})
        self.assertEqual(hero_classes(self.db, "恋", ()), set())  # 卡牌 60、辅助 60
        self.assertEqual(hero_classes(self.db, "恋", ("大背包",)), set())  # 卡牌 90
        self.assertEqual(hero_classes(self.db, "恋", ("大背包", "会员推荐信")), {"卡牌"})  # 卡牌 120
        self.assertEqual(hero_classes(self.db, "帕露南", ("ATM机", "探天卫星")), {"卡牌", "辅助"})
        self.assertEqual(hero_classes(self.db, "凛", ()), set())  # 卡牌 80
        self.assertEqual(hero_classes(self.db, "凛", ("标记喷罐",)), {"卡牌"})  # 卡牌 130

    def test_tag_score_needs_class_threshold(self):
        # 凛的卡牌只有 80：没有筹码时，卡牌标签的筹码拿不到标签分；拿了标记喷罐（卡 50）以后才有
        self.assertNotIn("标签分", self.official("凛", "书签"))
        self.assertNotIn("标签分", self.official("凛", "小猪存钱罐"))
        self.assertEqual(self.official("凛", "书签", owned=["标记喷罐"])["标签分"], 40)

    def test_official_scale_in_total(self):
        # 内置推荐分计入总分时整体 ×0.5；推荐角标仍按游戏公式的原值判断
        scale = self.db.rules["official"]["scale"]
        self.assertEqual(scale, 0.5)
        scorer = Scorer(self.db, State(hero="邦妮"))
        parts = {p.label: p.score for p in scorer.parts("拳击手套-高级", (), None) if p.label in OFFICIAL}
        self.assertEqual(parts, {"基础分": 30, "角色分": 50, "标签分": 20})
        self.assertEqual(scorer.official_raw("拳击手套-高级", (), None), 200)
        adv = self.run_case("邦妮", ["拳击手套-高级", "夹心饼干-可口", "会员推荐信"])
        glove = self.by_name(adv)["拳击手套-高级"]
        self.assertEqual(glove.official, 100)
        self.assertEqual(glove.official_raw, 200)

    def test_badges_on_real_samples(self):
        # 实机样本上的游戏推荐角标，按游戏的判法（mechanics/chips.md 第四节）：这一组有内置推荐分 ≥ 100 的候选时，这些都有
        # 「强烈推荐」角标（不只最高的一张）；没有时，≥ 50、而且是这一组三个候选里最高的（并列最高都有）才有「推荐」角标
        # （凛、汉娜、阿尔共 8 次三选一；10-02 美咲 12 次、照 6 次；10-02 第三局真人对局芬妮 12 张；10-03 阿尔 6 张，
        # 共 132 个筹码）。
        # 只看「≥ 50」时美咲 2 星、3 星有 5 个 50~60 分的筹码对不上，加上「最高的」以后都对得上；只看「≥ 50 且最高」时阿尔的
        # 银行卡-余额多（220，同组的银行卡-用不完 240）对不上，加上「≥ 100 的都有」以后对得上。
        # badge_unexplained 是推算和实机不一致、原因待查的筹码（见 labels.json 的 badge_note）：按现在的判法对得上的照样核对
        # （那一处标注已经查清），还对不上的才不计入。两种的个数都写进断言，判法或样本变了时一起改
        samples.require_samples("labels.json")
        labels = json.loads((samples.SAMPLES_DIR / "labels.json").read_text(encoding="utf-8"))["samples"]
        line, strong = self.db.rules["official"]["recommend"], self.db.rules["official"]["strong"]
        checked = skipped = 0
        for s in labels:
            if "badge" not in s:
                continue
            hero = self.db.resolve_hero(s["hero"]).name
            scores = {chip: sum(self.official(hero, chip, s.get("owned", ()), s.get("round")).values())
                      for chip in s["offer"]}
            top = max(scores.values())
            for chip in s["offer"]:
                score = scores[chip]
                badge = score >= strong or (score >= line and score == top)
                if chip in s.get("badge_unexplained", ()) and badge != (chip in s["badge"]):
                    skipped += 1
                    continue
                with self.subTest(file=s["file"], chip=chip):
                    self.assertEqual(badge, chip in s["badge"], (score, top))
                    checked += 1
        self.assertEqual((checked, skipped), (132, 0))


class PoolTest(Base):
    def test_owned_and_granted_not_in_pool(self):
        pool = {c.name for c in player_pool(self.db, ("标靶",), "龙宫游乐园")}
        self.assertNotIn("标靶", pool)
        self.assertNotIn("交错双鲤", pool)
        self.assertIn("小蛇玩偶", pool)

    def test_map_chips_only_on_their_map(self):
        pool = {c.name for c in player_pool(self.db, (), "魔法学院")}
        self.assertIn("精品剑盾", pool)
        self.assertNotIn("大铜锣", pool)
        self.assertFalse(any(c.maps for c in player_pool(self.db, (), None)))

    def test_two_schools_lock_the_rest(self):
        owned = ("标靶", "缓冲盾牌")
        self.assertEqual(owned_schools(self.db, owned), ("标记", "治愈"))
        schools = {c.school for c in player_pool(self.db, owned, None)}
        self.assertEqual(schools, {None, "标记", "治愈"})
        one = {c.school for c in player_pool(self.db, ("标靶",), None)}
        self.assertEqual(one, {None, "星光", "治愈", "标记", "充能"})

    def test_leftmost_slot(self):
        db, hero = self.db, self.db.heroes["邦妮"]
        pool = player_pool(db, (), None)
        left, general = slot_dists(db, hero, pool, (), star=2)
        self.assertAlmostEqual(sum(left.values()), 1)
        self.assertAlmostEqual(sum(general.values()), 1)
        for name in left:
            self.assertEqual(db.chips[name].rarity, "紫", name)
            self.assertTrue(db.chips[name].pools, name)
        self.assertNotIn("额外电池", left)  # 标签是通用，不在任何职业池
        # 攻击权重 100，其余各 15：攻击池的筹码占大头
        attack = sum(p for n, p in left.items() if "攻击" in db.chips[n].pools)
        self.assertGreater(attack, 0.65)

    def test_leftmost_slot_uses_listed_pools_only(self):
        # 英文 wiki：没列在职业筹码池表里的筹码不会出现在最左格，即使它带攻击或卡牌标签
        db = self.db
        for hero, chip in (("邦妮", "磨刀石"), ("凛", "电击手套")):
            with self.subTest(hero=hero, chip=chip):
                self.assertFalse(db.chips[chip].pools)
                left, general = slot_dists(db, db.heroes[hero], player_pool(db, (), None), (), star=2)
                self.assertNotIn(chip, left)
                self.assertIn(chip, general)  # 其余两格照常能出
        left, _ = slot_dists(db, db.heroes["凛"], player_pool(db, (), None), (), star=3)
        self.assertNotIn("电磁炮", left)

    def test_reroll_leftmost_has_its_own_switch(self):
        # 刷新后的最左格单独设（rules.toml [pool] reroll_leftmost），第一次刷出的三选一不受影响
        db, hero = self.db, self.db.heroes["邦妮"]
        pool = player_pool(db, (), None)
        switches = db.rules["pool"]
        self.assertEqual(switches["reroll_leftmost"], "class_tag")
        self.assertIsNotNone(slot_dists(db, hero, pool, (), 1, reroll=True)[0])
        saved = switches["reroll_leftmost"]
        try:
            switches["reroll_leftmost"] = "random"
            self.assertIsNone(slot_dists(db, hero, pool, (), 1, reroll=True)[0])
            self.assertIsNotNone(slot_dists(db, hero, pool, (), 1)[0])  # 第一次刷出的三选一不受影响
            switches["reroll_leftmost"] = "class_pool"
            left, _ = slot_dists(db, hero, pool, (), 2, reroll=True)
            self.assertTrue(all(db.chips[n].pools for n in left))
            self.assertNotIn("磨刀石", left)
            # 旧的出法：固定稀有度、不按职业权重，从该稀有度的全部筹码里按普通权重抽
            switches["reroll_leftmost"] = "rarity"
            left, _ = slot_dists(db, hero, pool, (), 2, reroll=True)
            self.assertIn("磨刀石", left)
            self.assertIn("额外电池", left)  # 标签只有通用的也能出
            self.assertEqual(len(set(left.values())), 1)  # 没有已有筹码时同流派加权不起作用：人人相同
            switches["reroll_leftmost"] = "none-such"
            with self.assertRaises(ValueError):
                slot_dists(db, hero, pool, (), 2, reroll=True)
        finally:
            switches["reroll_leftmost"] = saved

    def test_reroll_leftmost_class_tag(self):
        # 刷新后最左格（class_tag）：稀有度按星数固定，像第一次刷出那样按职业权重选职业筹码池，但池按筹码的职业标签建。
        # 实机：美咲 2 星（已持有普通瞄具、速度轮滑-中级）刷新后最左格出现磨刀石，它不在任何职业筹码池表里（标签是攻击）
        db, hero = self.db, self.db.heroes["美咲"]
        owned = ("普通瞄具", "速度轮滑-中级")
        pool = player_pool(db, owned, "星趴·梦想号")
        schools = owned_schools(db, owned)
        left, _ = slot_dists(db, hero, pool, schools, 2, reroll=True, class_owned=owned)
        self.assertAlmostEqual(sum(left.values()), 1)
        self.assertTrue(all(db.chips[n].rarity == "紫" for n in left))
        self.assertFalse(db.chips["磨刀石"].pools)
        self.assertIn("磨刀石", left)
        # 只出带职业标签的：标签只有通用的额外电池不出（和第一次刷出一样）
        self.assertTrue(all(class_tags(db.chips[n]) for n in left))
        self.assertNotIn("额外电池", left)
        # 按职业权重：美咲的攻击职业分值最高，攻击池的筹码占大头（不按职业权重的 rarity 出法里不到两成）
        attack = sum(p for n, p in left.items() if "攻击" in class_tags(db.chips[n]))
        self.assertGreater(attack, 0.5)
        self.assertGreater(left["美工刀-初级"] + left["拳击手套-中级"], 0.2)
        # 磨刀石和攻击池表里的同色筹码同一个池、按同一份权重抽（同流派加权照常）
        self.assertAlmostEqual(left["磨刀石"], left["拳击手套-中级"])
        # 第一次刷出时仍按池表出：没有磨刀石，攻击池的几张份额更大
        first, _ = slot_dists(db, hero, pool, schools, 2, class_owned=owned)
        self.assertNotIn("磨刀石", first)
        self.assertGreater(first["拳击手套-中级"], left["拳击手套-中级"])
        # 3 星刷新后最左格固定金色，池表外的电磁炮（标签是攻击）也能出；任务三选一仍没有特殊的最左格
        left3, _ = slot_dists(db, hero, pool, schools, 3, reroll=True, class_owned=owned)
        self.assertTrue(left3 and all(db.chips[n].rarity == "金" for n in left3))
        self.assertFalse(db.chips["电磁炮"].pools)
        self.assertIn("电磁炮", left3)
        self.assertNotIn("电磁炮", slot_dists(db, hero, pool, schools, 3, class_owned=owned)[0])
        self.assertIsNone(slot_dists(db, hero, pool, schools, 2, "task", 2, reroll=True)[0])

    def test_class_tags_cover_pool_tables(self):
        # 池表里的筹码，职业标签和池表一致：class_tag 只比池表多出带职业标签、不在池表里的那些筹码
        for chip in self.db.chips.values():
            if chip.pools:
                with self.subTest(chip=chip.name):
                    self.assertEqual(set(class_tags(chip)), set(chip.pools))

    def test_task_has_no_special_slot(self):
        db, hero = self.db, self.db.heroes["邦妮"]
        left, general = slot_dists(db, hero, player_pool(db, (), None), (), 1, "task", 3)
        self.assertIsNone(left)
        gold = sum(p for n, p in general.items() if db.chips[n].rarity == "金")
        self.assertAlmostEqual(gold, 0.25)

    def test_same_school_is_boosted(self):
        db, hero = self.db, self.db.heroes["邦妮"]
        before = slot_dists(db, hero, player_pool(db, (), None), (), 2)
        owned = ("标靶",)
        after = slot_dists(db, hero, player_pool(db, owned, None), ("标记",), 2)
        self.assertGreater(appear_prob(*after, "普通瞄具"), appear_prob(*before, "普通瞄具"))

    def test_expected_best(self):
        general = {"a": 0.5, "b": 0.5}
        value = {"a": 0.0, "b": 100.0}
        self.assertAlmostEqual(expected_best(None, general, value), 100 * (1 - 0.5**3))
        self.assertAlmostEqual(expected_best({"a": 1.0}, general, value), 100 * (1 - 0.5**2))


class AdviceTest(Base):
    def test_percentages(self):
        adv = self.run_case("邦妮", ["标靶", "夹心饼干-可口", "会员推荐信"], star=1, round=4)
        total = sum(e.percent for e in adv.evals) + adv.reroll.percent
        self.assertAlmostEqual(total, 1)
        again = self.run_case("邦妮", ["标靶", "夹心饼干-可口", "会员推荐信"], star=1, round=4)
        self.assertEqual([e.total for e in adv.evals], [e.total for e in again.evals])

    def test_default_rerolls(self):
        self.assertEqual(default_rerolls(self.db, 0), 2)
        self.assertEqual(default_rerolls(self.db, 2), 4)
        self.assertEqual(default_rerolls(self.db, 1, ("循环往复",)), 6)

    def test_bonnie_takes_mark_chip_for_the_sights(self):
        adv = self.run_case("邦妮", ["标靶", "夹心饼干-可口", "会员推荐信"], star=1, round=4)
        self.assertEqual(adv.best, "标靶")
        target = self.by_name(adv)["标靶"]
        self.assertGreater(target.percent, 0.8)
        self.assertGreater(target.lookahead, 0)
        text = "".join(target.reasons())
        self.assertIn("标记", text)
        self.assertIn("瞄具", text)

    def test_unsuitable_offer_means_reroll(self):
        # 三张都是对派德曼没用的卡牌类筹码（不属于他的推荐流派，没有推荐流派的加分）
        adv = self.run_case("派德曼", ["会员推荐信", "小猪存钱罐", "书签"], star=1, round=4)
        self.assertEqual(adv.best, REROLL)
        self.assertTrue(adv.notes)
        self.assertGreater(adv.reroll.percent, max(e.percent for e in adv.evals))

    def test_no_reroll_option_without_rerolls(self):
        adv = self.run_case("派德曼", ["会员推荐信", "小猪存钱罐", "书签"], rerolls=0)
        self.assertIsNone(adv.reroll)
        self.assertNotEqual(adv.best, REROLL)
        self.assertAlmostEqual(sum(e.percent for e in adv.evals), 1)

    def test_papara_prefers_half_hp_chip(self):
        adv = self.run_case("帕帕拉", ["美工刀-初级", "磨刀石", "夹心饼干-可口"], star=2, rerolls=0)
        evals = self.by_name(adv)
        self.assertEqual(adv.best, "磨刀石")
        self.assertGreater(evals["磨刀石"].total, evals["美工刀-初级"].total)
        self.assertIn("与半血加成冲突", "".join(evals["美工刀-初级"].reasons(limit=5)))

    def test_second_school_locks_and_says_so(self):
        adv = self.run_case("邦妮", ["缓冲盾牌", "拳击手套-初级", "速度轮滑-初级"], owned=("标靶",))
        text = self.by_name(adv)["缓冲盾牌"].lookahead_reason
        self.assertIn("选择后锁定「标记」「治愈」流派", text)
        self.assertIn("「星光」「充能」流派的筹码本局不再出现", text)

    def test_locked_chips_not_repeated_as_zero_rate(self):
        # 选择后锁定流派、又有职业分值、前瞻为负：锁掉的流派里的高适配度筹码已写在「本局不再出现的高适配度筹码」里，
        # 职业分值那一句后面不再写「X下次出现率 8% → 0%」（同一件事说两遍）。凛 1 星第 13 轮、已有 ATM机，拿维生素药丸
        # 锁定星光、治愈，魔法箭袋、忍术飞镖本局不再出现
        st = State(hero="凛", star=1, round=13, map="星趴·梦想号", owned=("ATM机",), rerolls=0, difficulty=NORMAL)
        vitamin = self.by_name(advise(self.db, st, ["鹰眼瞄具", "优雅之羽", "维生素药丸"]))["维生素药丸"]
        reason = vitamin.lookahead_reason
        self.assertLess(vitamin.lookahead, 0)
        self.assertIn("本局不再出现的高适配度筹码：魔法箭袋、忍术飞镖", reason)
        self.assertIn(CLASS_SHIFT_REASON.format(points="卡牌职业分值 +30", classes="卡牌"), reason)
        locked = {"标记", "充能"}
        for bit in reason.split("；"):
            m = re.match(r"^(.+?)下次出现率 \d+% → \d+%$", bit)
            if m:
                with self.subTest(bit=bit):
                    self.assertNotIn(self.db.chips[m.group(1)].school, locked)
        self.assertEqual(vitamin.lookahead_short, "前瞻：魔法箭袋等不再出现")  # 短句照旧

    def test_lookahead_ignores_losing_the_chip_itself(self):
        # 拿走的筹码以后不会再出现，这不是损失。凛拿标记喷罐，前瞻是进入标记流派的好处
        spray = self.by_name(self.run_case("凛", ["标靶", "标记喷罐", "鹰眼瞄具"]))["标记喷罐"]
        self.assertGreater(spray.lookahead, 0)
        self.assertNotIn("不多", spray.lookahead_reason)
        # 前瞻和筹码自己的分值无关：「不拿」的一边它的分值记为 0，自己分值再高，拿走它也不算损失。
        # 把它在下一次三选一里的分值抬到 1000：分值缓存的键是（筹码, 已有筹码, 轮次, 地图），轮次未知时为 None，
        # 每张候选地图各一个。已有循环往复，下一次三选一里没有它，这里只看下一次三选一这一层；经循环往复额外那次
        # 三选一的那一层见 test_lookahead_ignores_own_value_through_endless_cycle
        endless = self.db.rules["endless_cycle"]["chip"]
        st = State(hero="凛", star=1, owned=(endless,))
        for name in ("大背包", "标记喷罐"):
            with self.subTest(chip=name):
                plain, inflated = Scorer(self.db, st), Scorer(self.db, st)
                owned = plain.known_owned
                for m in inflated.maps:
                    inflated.memo.score[(name, frozenset(owned), None, m)] = 1000.0
                    # 抬高的分值确实用上了：下一次三选一里它的分值是 1000（只是拿和不拿两边都不计它）
                    self.assertEqual(future_setup(inflated, owned, m)[2][name], 1000.0)
                    self.assertNotIn(endless, future_setup(plain, owned, m)[2])
                self.assertAlmostEqual(plain.lookahead(name, owned), inflated.lookahead(name, owned))
                self.assertGreaterEqual(plain.lookahead(name, owned), 0)

    def test_lookahead_ignores_own_value_through_endless_cycle(self):
        # 循环往复还会出现时同样：它的分值里有额外那次三选一的估计，这一张也在那次的筹码池里。拿了它的
        # 一边，那次的筹码池里没有它；不拿的一边，它在那次里同样记 0。所以这一张自己的分值抬得再高，前瞻也不变。
        # 轮次未知时，下一次三选一和额外那次的分值都按轮次 None 取；轮次已知（第 4 轮，升星）时，下一次三选一按
        # 第 4 + round_advance 轮，额外那次再晚 delay 回合，两处都抬
        endless = self.db.rules["endless_cycle"]["chip"]
        advance = self.db.rules["lookahead"]["round_advance"]
        for rnd in (None, 4):
            st = State(hero="凛", star=1, round=rnd)
            later = None if rnd is None else rnd + advance  # 下一次三选一的轮次
            for name in ("大背包", "标记喷罐"):
                with self.subTest(round=rnd, chip=name):
                    plain, inflated = Scorer(self.db, st), Scorer(self.db, st)
                    rounds = {later, None if later is None else later + plain.endless_delay()}
                    for m in inflated.maps:
                        for r in rounds:
                            inflated.memo.score[(name, frozenset(), r, m)] = 1000.0
                        # 循环往复在下一次的筹码池里，它的分值确实用上了抬高的值
                        base, high = future_setup(plain, (), m)[2], future_setup(inflated, (), m)[2]
                        self.assertGreater(high[endless], base[endless] + 1)
                    self.assertAlmostEqual(plain.lookahead(name, ()), inflated.lookahead(name, ()))
                    self.assertGreaterEqual(plain.lookahead(name, ()), 0)

    def test_endless_value_in_hypothetical(self):
        # 前瞻假设的局面和现在相同（没有多拿的筹码、也没有记 0 的）时，循环往复照常是它本身的总分；按筹码方案计算、
        # 方案标了循环往复的档位时，档位那一项代替了循环往复那一项，额外那次三选一不在它的分值里，不调整（None）
        from advisor.engine import Plan

        endless = self.db.rules["endless_cycle"]["chip"]
        st = State(hero="凛", star=1, round=4)
        sc = Scorer(self.db, st)
        for m in sc.maps:
            with self.subTest(map=m):
                self.assertEqual(endless_value(sc, 7, m), sc.score(endless, (), 7, m))
                self.assertLess(endless_value(sc, 7, m, zero="大背包"), sc.score(endless, (), 7, m))
        marked = Scorer(self.db, st, scheme=Plan(name="测试", hero="凛", tiers=((endless, "first"),)))
        self.assertIsNone(endless_value(marked, 7, marked.maps[0], ("大背包",)))

    def test_lookahead_counts_freed_share(self):
        # 每个筹码一局只能拿一次：拿走后它在职业筹码池和同稀有度里的份额让给其他筹码。
        # 手持风扇-大和魔法箭袋同在卡牌职业筹码池的紫色一栏，凛 1 星拿走风扇-大，2 星时箭袋在最左格的份额变大
        scorer = Scorer(self.db, State(hero="凛", star=1))
        _, ((_, left0, gen0),), _ = future_setup(scorer, (), None)  # 还能升星：只有升星一种来源
        _, ((_, left1, gen1),), _ = future_setup(scorer, ("手持风扇-大",), None)
        self.assertGreater(left1["魔法箭袋"], left0["魔法箭袋"])
        self.assertGreater(appear_prob(left1, gen1, "魔法箭袋"), appear_prob(left0, gen0, "魔法箭袋"))
        adv = self.run_case("凛", ["手持风扇-大", "标靶", "夹心饼干-一般"], star=1)
        fan = self.by_name(adv)["手持风扇-大"]
        self.assertGreater(fan.lookahead, 0)
        self.assertIn("魔法箭袋下次出现率", fan.lookahead_reason)
        # 不带来新流派的筹码也有这部分：大背包同在卡牌和辅助的紫色一栏，拿走后箭袋的份额同样变大
        _, ((_, left2, _),), _ = future_setup(scorer, ("大背包",), None)
        self.assertGreater(left2["魔法箭袋"], left0["魔法箭袋"])
        self.assertGreater(scorer.lookahead("大背包", ()), 0)
        self.assertIn("魔法箭袋下次出现率", scorer.lookahead_reason("大背包", ()))

    def test_negative_lookahead_names_the_crowded_out_chip(self):
        # 阿尔进治愈流派是真的吃亏：治愈筹码对他分值低，还会挤掉高分的星光筹码
        adv = self.run_case("阿尔", ["缓冲盾牌", "夹心饼干-一般", "循环往复"], star=1, round=1)
        shield = self.by_name(adv)["缓冲盾牌"]
        self.assertLess(shield.lookahead, 0)
        self.assertIn("其他高适配度筹码出现率下降", shield.lookahead_reason)
        self.assertIn("银行卡-余额多下次出现率", shield.lookahead_reason)

    def test_reroll_cost_follows_rerolls_and_picks(self):
        offer = ["会员推荐信", "小猪存钱罐", "书签"]

        def cost(star, rerolls):
            return self.run_case("邦妮", offer, star=star, rerolls=rerolls).reroll.cost

        # 开局也不一律顶到同一个上限：剩得越少越贵
        self.assertGreater(cost(0, 1), cost(0, 2))
        self.assertGreater(cost(0, 2), cost(0, 3))
        self.assertGreater(cost(0, 3), cost(0, 4))
        self.assertGreater(cost(1, 2), cost(1, 3))
        # 重选次数相同时，后面还要选的次数越多（星数越低）越贵
        self.assertGreater(cost(0, 3), cost(1, 3))
        self.assertGreater(cost(1, 3), cost(2, 3))
        self.assertGreater(cost(2, 3), cost(3, 3))

    def test_note_uses_final_total(self):
        line = self.db.rules["advice"]["recommend_total"]
        # 附注按总分是否低于推荐线判断，但文字不写分值和推荐线（悬浮窗不显示分数）
        self.assertEqual(LOW_NOTE, "三个候选筹码的适配度均偏低")
        # 银行卡-余额多 的内置分（原值）够「推荐」线，但凛的玩家思路不推荐，三个总分都低，应当提示
        adv = self.run_case("凛", ["银行卡-余额多", "会员推荐信", "优雅之羽"])
        self.assertTrue(all(e.total < line for e in adv.evals))
        self.assertTrue(any(e.official_raw >= self.db.rules["official"]["recommend"] for e in adv.evals))
        self.assertIn(LOW_NOTE, adv.notes)
        # 标靶 内置分很低，但对凛总分够高，不提示
        adv = self.run_case("凛", ["标靶", "会员推荐信", "优雅之羽"])
        self.assertTrue(all(e.official_raw < self.db.rules["official"]["recommend"] for e in adv.evals))
        self.assertTrue(any(e.total >= line for e in adv.evals))
        self.assertNotIn(LOW_NOTE, adv.notes)

    def test_low_note_only_when_reroll_first(self):
        """「三个候选筹码的适配度均偏低」只在首推是刷新时出现（这条附注用来解释为什么推荐刷新）：总分都低于推荐线、
        首推却是某个筹码时不加；其余条件不变。命令行的附注取同一个 Advice.notes。"""
        line = self.db.rules["advice"]["recommend_total"]
        # 首推筹码的一组只剩 1 次刷新（刷新的代价高，首推循环往复）
        chip_first = self.run_case("凛", ["夹心饼干-美味", "循环往复", "医疗箱-紧急治疗"], star=1, round=5, rerolls=1)
        reroll_first = self.run_case("凛", ["星币锤", "美工刀-初级", "大碗炖肉"], star=1, round=5, rerolls=2)
        for adv in (chip_first, reroll_first):
            self.assertTrue(all(e.total < line for e in adv.evals))  # 两组都是总分都低于推荐线
        self.assertNotEqual(chip_first.best, REROLL)
        self.assertNotIn(LOW_NOTE, chip_first.notes)
        self.assertEqual(reroll_first.best, REROLL)
        self.assertEqual(reroll_first.notes[0], LOW_NOTE)
        self.assertIn(f"⚠ {LOW_NOTE}", cli.render(self.db, reroll_first))
        self.assertNotIn(LOW_NOTE, cli.render(self.db, chip_first))
        # 没有刷新次数时首推一定是筹码，不加
        no_reroll = self.run_case("凛", ["星币锤", "美工刀-初级", "大碗炖肉"], star=1, round=5, rerolls=0)
        self.assertIsNone(no_reroll.reroll)
        self.assertNotIn(LOW_NOTE, no_reroll.notes)


class LookaheadTopStarTest(Base):
    """3 星以后的前瞻（context.next_sources）：不再升星，下一次三选一按任务和筹码商店在一局里的占比混合，占比和剩余
    三选一次数同一个口径（rules.toml [picks]：本地图的筹码任务数 × task_rate 对 shop）。任务三格全随机、稀有度按 3 级任务
    那一行；筹码商店稀有度按 3 星那一行、最左格出职业筹码池。0～2 星下一次三选一的主体照旧是下一星的升星。"""

    HERO, MAP = "小町", "园林中庭"
    OWNED = ("标记喷罐", "大背包", "循环往复")  # 已有循环往复：下一次三选一里没有它，不经它额外那次三选一调分
    OFFER = ["探天卫星", "忍术飞镖", "大碗炖肉"]

    def shares(self, map_name, db=None) -> tuple[float, float]:
        """（任务的占比, 筹码商店的占比）：本地图的筹码任务数 × task_rate 对 shop，没有地图资料时按 default_tasks。"""
        db = db or self.db
        cfg = db.rules["picks"]
        info = db.map_info.get(map_name)
        task = (info.chip_tasks if info else cfg["default_tasks"]) * cfg["task_rate"]
        return task / (task + cfg["shop"]), cfg["shop"] / (task + cfg["shop"])

    def dists(self, sc, have, map_name):
        """持有 have 时下一次三选一的筹码池和两种来源的分布：任务（3 级）、筹码商店（3 星），照 pool 的模型直接算。"""
        db = self.db
        pool = player_pool(db, have, map_name)
        schools = owned_schools(db, have)
        task = slot_dists(db, sc.hero, pool, schools, 3, "task", 3, class_owned=have)
        shop = slot_dists(db, sc.hero, pool, schools, 3, "shop", class_owned=have)
        return pool, task, shop

    def test_sources_by_star(self):
        for star in (0, 1, 2):
            sc = Scorer(self.db, State(hero=self.HERO, star=star, map=self.MAP))
            with self.subTest(star=star):
                self.assertEqual(next_sources(sc, self.MAP), ((1.0, "levelup", star + 1, None),))
        sc = Scorer(self.db, State(hero=self.HERO, star=3, map=self.MAP))
        task, shop = self.shares(self.MAP)
        self.assertAlmostEqual(task, 0.8)  # 园林中庭最高难度 4 个筹码任务，筹码商店 1 次
        self.assertEqual(next_sources(sc, self.MAP), ((task, "task", 3, 3), (shop, "shop", 3, None)))
        # 地图待识别：每张候选地图按各自的任务数（星趴·梦想号 3 个）；不知道地图时按 default_tasks
        cands = Scorer(self.db, State(hero=self.HERO, star=3, map_candidates=("星趴·梦想号", self.MAP)))
        self.assertEqual(cands.maps, ("星趴·梦想号", self.MAP))
        self.assertAlmostEqual(next_sources(cands, "星趴·梦想号")[0][0], 0.75)
        self.assertAlmostEqual(next_sources(cands, self.MAP)[0][0], 0.8)
        self.assertAlmostEqual(next_sources(cands, None)[0][0], self.shares(None)[0])
        # 只剩一种时只列一种：没有筹码商店（shop 为 0）时只有任务
        rules = dict(self.db.rules)
        rules["picks"] = {**rules["picks"], "shop": 0}
        no_shop = Scorer(dataclasses.replace(self.db, rules=rules), State(hero=self.HERO, star=3, map=self.MAP))
        self.assertEqual(next_sources(no_shop, self.MAP), ((1.0, "task", 3, 3),))

    def test_no_task_and_no_shop(self):
        """任务和筹码商店的占比都为 0（rules.toml [picks] 的 task_rate、shop 都写 0；现在的资料碰不到：shop 为 1，每张地图
        都有筹码任务）：mixed_sources 不除以 0，按筹码商店给一种占满；一局的任务和商店三选一为 0，剩余次数只有剩下的升星
        （3 星以后也不加下限），按混合算的次数为 0，这一种用不上。3 星以后前瞻记 0，推荐照常算得出来。"""
        rules = dict(self.db.rules)
        rules["picks"] = {**rules["picks"], "task_rate": 0, "shop": 0}
        db = dataclasses.replace(self.db, rules=rules)
        for star in (1, 2, 3):
            sc = Scorer(db, State(hero=self.HERO, star=star, round=5, map=self.MAP))
            with self.subTest(star=star):
                self.assertEqual(mixed_sources(sc, self.MAP), ((1.0, "shop", star, None),))
                self.assertEqual((sc.picks.levelups, sc.picks.extras, sc.picks.total), (3 - star, 0, 3 - star))
                self.assertEqual(split_picks(sc, sc.picks.total), (3 - star, 0, 0))
                self.assertEqual(split_picks(sc, sc.lookahead_times())[2], 0)
        adv = advise(db, State(hero=self.HERO, star=3, round=5, map=self.MAP, owned=self.OWNED, rerolls=2), self.OFFER)
        self.assertEqual([e.lookahead for e in adv.evals], [0, 0, 0])
        self.assertAlmostEqual(sum(e.percent for e in adv.evals) + (adv.reroll.percent if adv.reroll else 0), 1)

    def test_future_mixes_task_and_shop(self):
        """3 星时下一次三选一的期望最高分 = 任务占比 × 三格全随机的期望 + 筹码商店占比 × 最左格出职业筹码池的期望，
        拿和不拿两边都这样算；出现率同样按占比加权。小町已有标记喷罐和大背包时拿忍术飞镖，金色的卡牌筹码只剩探天卫星：
        筹码商店的最左格里它的出现率翻了几倍，任务三格全随机，几乎不变，所以前瞻比只按筹码商店算时小得多。"""
        db, m = self.db, self.MAP
        sc = Scorer(db, State(hero=self.HERO, star=3, map=m, owned=self.OWNED), offer=self.OFFER)
        task, shop = self.shares(m)
        owned = sc.known_owned
        rates = []
        for extra in ((), ("忍术飞镖",)):
            have = sc.ordered((*owned, *extra))
            pool, (t_left, t_gen), (s_left, s_gen) = self.dists(sc, have, m)
            self.assertIsNone(t_left)  # 任务没有固定稀有度的最左格
            self.assertTrue(s_left)
            value = {c.name: sc.score(c.name, owned, None, m) for c in pool}
            with self.subTest(extra=extra):
                _, dists, _ = future_setup(sc, have, m)
                self.assertEqual(dists, ((task, t_left, t_gen), (shop, s_left, s_gen)))
                expect = task * expected_best(t_left, t_gen, value) + shop * expected_best(s_left, s_gen, value)
                self.assertAlmostEqual(sc.future(have, map_name=m), expect)
            rates.append((appear_prob(t_left, t_gen, "探天卫星"), appear_prob(s_left, s_gen, "探天卫星")))
        (t0, s0), (t1, s1) = rates
        self.assertGreater(s1, 2 * s0)  # 筹码商店的最左格：翻倍以上
        self.assertLess(t1 - t0, 0.01)  # 任务：几乎不变
        p0, p1 = task * t0 + shop * s0, task * t1 + shop * s1
        reason = sc.lookahead_reason("忍术飞镖", owned)
        self.assertIn(f"探天卫星下次出现率 {p0:.0%} → {p1:.0%}", reason)
        self.assertLess(p1, s1 / 2)
        # 前瞻 = weight × 影响的选择次数 ×（拿 − 不拿），两边都按混合的期望
        delta = sc.future((*owned, "忍术飞镖"), map_name=m) - sc.future(owned, zero="忍术飞镖", map_name=m)
        self.assertAlmostEqual(sc.lookahead("忍术飞镖", owned),
                               db.rules["lookahead"]["weight"] * sc.lookahead_times() * delta)
        # 小町 3 星、已有标记喷罐和大背包：探天卫星眼前的分高，前瞻不再把忍术飞镖抬过它
        adv = self.run_case(self.HERO, self.OFFER, star=3, map=m, owned=self.OWNED)
        self.assertEqual(adv.best, "探天卫星")

    def test_low_stars_levelup_only(self):
        """0～2 星下一次三选一的主体（future_setup、future 不带 mixed）照旧只按下一星的升星（最左格出职业筹码池）：分布和
        期望最高分都和只按升星算逐位相同。2 星前瞻里多出的半次按混合，见 LookaheadSplitTest。"""
        db = self.db
        for star in (0, 1, 2):
            sc = Scorer(db, State(hero=self.HERO, star=star, owned=self.OWNED))
            owned = sc.known_owned
            have = sc.ordered((*owned, "忍术飞镖"))
            for m in sc.maps:
                pool = player_pool(db, have, m)
                left, general = slot_dists(db, sc.hero, pool, owned_schools(db, have), star + 1, class_owned=have)
                value = {c.name: sc.score(c.name, owned, None, m) for c in pool}
                with self.subTest(star=star, map=m):
                    self.assertEqual(future_setup(sc, have, m)[1], ((1.0, left, general),))
                    self.assertEqual(sc.future(have, map_name=m), expected_best(left, general, value))

    def test_zero_when_no_picks_left(self):
        """3 星、预计之后没有三选一（这次是任务、已有筹码多于一局的任务和商店三选一，又到了进度上限：魔法学院困难及以上
        16，第 16 轮）：前瞻记 0，不写说明和短句。还没到进度上限时 3 星以后有下限，不估成 0（PicksLeftTest）。"""
        plain = [c.name for c in self.db.chips.values() if not c.maps and not c.granted and c.name != "循环往复"]
        st = State(hero=self.HERO, star=3, round=16, map="魔法学院", owned=tuple(plain[:8]), source="task", rerolls=2)
        offer = [c for c in plain[8:] if c not in st.owned][:3]
        sc = Scorer(self.db, st, offer=offer)
        self.assertEqual(sc.picks.total, 0)
        self.assertEqual(sc.lookahead_times(), 0)
        for e in advise(self.db, st, offer).evals:
            with self.subTest(chip=e.chip.name):
                self.assertEqual((e.lookahead, e.lookahead_reason, e.lookahead_short), (0, "", ""))

    def test_reason_names_shop_not_levelup(self):
        """3 星以后职业分值只影响筹码商店的最左格：前瞻的整句写「之后筹码商店的三选一更常出现……」，不再写「升星」；
        短句写「前瞻：筹码商店更常出现辅助职业的筹码」，两个职业放不下时照写「前瞻：更常出现攻击、辅助职业的筹码」。
        之后只剩任务时（没有筹码商店）不写职业分值这一句。0～2 星照旧写「之后升星和筹码商店的三选一……」。"""
        early = Scorer(self.db, State(hero="邦妮", star=1, round=3)).lookahead_reason("ATM机", ())
        self.assertIn(CLASS_SHIFT_REASON.format(points="辅助职业分值 +50", classes="辅助"), early)
        late = Scorer(self.db, State(hero="邦妮", star=3, round=9))
        reason = late.lookahead_reason("ATM机", ())
        self.assertIn(CLASS_SHIFT_SHOP_REASON.format(points="辅助职业分值 +50", classes="辅助"), reason)
        self.assertEqual(CLASS_SHIFT_SHOP_REASON.format(points="辅助职业分值 +50", classes="辅助"),
                         "选择后辅助职业分值 +50，之后筹码商店的三选一更常出现辅助职业的筹码")
        self.assertNotIn("升星", reason)
        for classes, short in (("辅助", "前瞻：筹码商店更常出现辅助职业的筹码"),
                               ("攻击、辅助", "前瞻：更常出现攻击、辅助职业的筹码")):
            sc = Scorer(self.db, State(hero="邦妮", star=3, round=9))
            sc.memo.lookahead_keys[("星币锤", ())] = {"classes": classes}  # 只剩职业这一个要点
            with self.subTest(classes=classes):
                self.assertEqual(sc.lookahead_short("星币锤", ()), short)
                self.assertLessEqual(len(short), SHORT_LIMIT)
        rules = dict(self.db.rules)
        rules["picks"] = {**rules["picks"], "shop": 0}
        no_shop = Scorer(dataclasses.replace(self.db, rules=rules), State(hero="邦妮", star=3, round=9))
        self.assertNotIn("职业分值", no_shop.lookahead_reason("ATM机", ()))


class ShownPercentTest(Base):
    """推荐度的显示：同一次推荐各选项取整时凑满 100%（最大余数法，被同系列上位替代的不算），
    排在前面的显示值不低于后面的；取整后为 0 写「<1%」、为 100 写「>99%」。悬浮窗、诊断包、命令行都用 engine.shown_percents。"""

    @staticmethod
    def adv(*percents, reroll=None, superseded=()):
        from types import SimpleNamespace
        evals = [SimpleNamespace(percent=p, superseded_by="X" if i in superseded else None) for i, p in enumerate(percents)]
        rr = SimpleNamespace(percent=reroll) if reroll is not None else None
        return SimpleNamespace(evals=evals, reroll=rr)

    def test_largest_remainder(self):
        from advisor.engine import REROLL_SLOT, shown_percents
        # 各自四舍五入为 86、7、3、3（合计 99）：差的 1% 给小数部分最大的（第一、二项都是 .45，按显示顺序给第一项）
        self.assertEqual(shown_percents(self.adv(0.8645, 0.0745, 0.0305, reroll=0.0305)),
                         {0: 87, 1: 7, 2: 3, REROLL_SLOT: 3})
        # 各自四舍五入为 34、34、33（合计 101）：只有一项补上
        self.assertEqual(shown_percents(self.adv(0.335, 0.335, 0.33)), {0: 34, 1: 33, 2: 33})
        # 小数部分不同时给最大的那项，不一定是第一项
        self.assertEqual(shown_percents(self.adv(0.502, 0.168, 0.049, reroll=0.281)), {0: 50, 1: 17, 2: 5, REROLL_SLOT: 28})
        # 被同系列上位替代的（推荐度 0）不算在内
        self.assertEqual(shown_percents(self.adv(0.0, 0.6, 0.4, superseded=(0,))), {1: 60, 2: 40})
        # 并列时显示顺序靠前的（候选在前、刷新在后）先补，靠后的不会比靠前的高
        self.assertEqual(shown_percents(self.adv(0.205, 0.205, 0.385, reroll=0.205)), {0: 21, 1: 20, 2: 39, REROLL_SLOT: 20})

    def test_percent_text(self):
        from advisor.engine import percent_text, shown_percent_texts
        self.assertEqual([percent_text(v) for v in (0, 1, 50, 99, 100)], ["<1%", "1%", "50%", "99%", ">99%"])
        self.assertEqual(percent_text(100, alone=True), "100%")
        self.assertEqual(shown_percent_texts(self.adv(0.9965, 0.002, 0.0015)), {0: ">99%", 1: "<1%", 2: "<1%"})
        # 只有一个选项参与比较（三张同系列、没有刷新次数）时推荐度就是 100%，照写
        self.assertEqual(shown_percent_texts(self.adv(0.0, 0.0, 1.0, superseded=(0, 1))), {2: "100%"})

    def test_real_advice(self):
        """各角色实际的三选一：合计 100%，按显示顺序不增，每项和没取整的值相差不到 1，最多 len - 1 项和四舍五入不同；
        option_views、摘要、诊断包、命令行显示的数一样。"""
        import random
        from types import SimpleNamespace

        from advisor.engine import REROLL_SLOT, shown_percent_texts, shown_percents, tie_key
        from advisor.ui import text as ui_text
        rng = random.Random(1004)
        names = [c.name for c in self.db.chips.values() if not c.granted and not c.maps]
        changed = 0
        for hero in sorted(self.db.hero_synergy)[::3]:
            for star, rnd, rerolls in ((0, 1, 2), (2, 8, 3), (3, 13, 0)):
                adv = advise(self.db, State(hero=hero, star=star, round=rnd, rerolls=rerolls), rng.sample(names, 3))
                shares = shown_percents(adv)
                with self.subTest(hero=hero, star=star):
                    self.assertEqual(sum(shares.values()), 100)
                    rows = [(e.percent, i) for i, e in enumerate(adv.evals) if not e.superseded_by]
                    rows += [(adv.reroll.percent, REROLL_SLOT)] if adv.reroll else []
                    order = [k for _, k in sorted(rows, key=lambda r: -tie_key(r[0]))]
                    shown = [shares[k] for k in order]
                    self.assertEqual(shown, sorted(shown, reverse=True))
                    for p, k in rows:
                        self.assertLess(abs(shares[k] - p * 100), 1)
                    changed += sum(shares[k] != round(p * 100) for p, k in rows) > 0
                    texts = shown_percent_texts(adv)
                    views = ui_text.option_views(adv)
                    self.assertEqual({v.slot: v.percent_text for v in views if not v.superseded}, texts)
                    best = REROLL_SLOT if adv.best == REROLL else next(i for i, e in enumerate(adv.evals)
                                                                     if e.chip.name == adv.best)
                    self.assertEqual(ui_text.advice_summary(adv).split("（")[-1].rstrip("）"), texts[best])
                    diag = ui_text.advice_text(self.db, SimpleNamespace(advice=adv, problems=[]))
                    out = cli.render(self.db, adv)
                    for k, t in texts.items():
                        name = REROLL if k == REROLL_SLOT else adv.evals[k].chip.name
                        self.assertIn(f"{t}  {name}", diag)
                        self.assertIn(f"{t:>4}  {name}", out)
        self.assertGreater(changed, 0)  # 确实有和各自四舍五入不同的（免得这条测试空跑）

    def test_notes_have_no_score_wording(self):
        # 推荐区的附注（Advice.notes）不写分值：不出现「总分」「中性分」「平均分」「推荐线」和「N 分」「（… 分）」。
        # 一次推荐里凑齐各类附注：三个候选筹码的适配度均偏低、候选筹码未收录、地图待识别、普通难度、畸变组件
        adv = advise(
            self.db, State(hero="凛", round=5, rerolls=2, difficulty=NORMAL, mutators=("暴雨",)),
            ["银行卡-余额多", "新筹码甲", "优雅之羽"], rarity=[None, "蓝", None],
        )
        notes = adv.notes
        self.assertIn(LOW_NOTE, notes)
        self.assertIn("第 2 个候选筹码「新筹码甲」资料库未收录：按平均水平计算，不计前瞻", notes)
        self.assertTrue(any(n.startswith("地图：待识别") for n in notes))
        self.assertIn(NORMAL_NOTE, notes)
        self.assertTrue(any(n.startswith("畸变组件「暴雨」") for n in notes))
        # 角色未收录、适配表缺角色的附注
        notes = notes + advise(self.db, State(hero="新角色", rerolls=2), ["标靶", "缓冲盾牌", "书签"]).notes
        syn = {h: hs for h, hs in self.db.hero_synergy.items() if h != "邦妮"}
        notes = notes + advise(dataclasses.replace(self.db, hero_synergy=syn), State(hero="邦妮", rerolls=2),
                               ["标靶", "缓冲盾牌", "书签"]).notes
        self.assertEqual(sum(n.endswith("只按游戏内置推荐和通用规则计算") for n in notes), 2)
        for n in notes:
            with self.subTest(note=n):
                self.assertIsNone(SCORE_WORDING.search(n))
                self.assertNotIn("不计分", n)


class BaselineScenarioTest(PlayerBase):
    """锁定的三组结果（0 星、没有已有筹码，除非注明）。以后改规则不能改掉它们的第一名。
    凛的两组按测试用的默认推荐档位（必选大背包、推荐标记喷罐），阿尔这一组不涉及档位。"""

    def test_rin_takes_mark_spray(self):
        for rerolls in (2, 3):  # 默认剩 2 次；锁定这组结果时是剩 3 次
            with self.subTest(rerolls=rerolls):
                adv = self.run_case("凛", ["标靶", "标记喷罐", "鹰眼瞄具"], rerolls=rerolls)
                self.assertEqual(adv.best, "标记喷罐")

    def test_rin_takes_backpack(self):
        for rerolls in (2, 3):
            with self.subTest(rerolls=rerolls):
                adv = self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"], rerolls=rerolls)
                self.assertEqual(adv.best, "大背包")

    def test_al_takes_buffer_shield_not_reroll(self):
        offer = ["缓冲盾牌", "夹心饼干-一般", "循环往复"]
        screenshot = dict(map="园林中庭", probabilities=(60, 37, 3), left_rarity="蓝", rerolls=3)
        for star, extra in ((0, {}), (1, {}), (1, screenshot)):  # 最后一组按一张实拍截图的局面
            with self.subTest(star=star, **extra):
                adv = self.run_case("阿尔", offer, star=star, round=1, **extra)
                self.assertEqual(adv.best, "缓冲盾牌")
                shield = self.by_name(adv)["缓冲盾牌"]
                self.assertGreater(shield.percent, adv.reroll.percent)

    def test_player_layer_overrides_auto_layer(self):
        # 自动层的「不靠战斗」给远野汉娜的鹰眼瞄具扣分；她的玩家思路另有一条扣分（标记流派对她几乎没有提升），
        # 命中玩家思路时只按玩家思路计，自动层的规则不再列出
        st = State(hero="远野汉娜")
        auto = Scorer(self.db, st, use_player=False).parts("鹰眼瞄具", (), None)
        # 先确认这个例子仍然成立：自动层确实有特性规则（不成立时换一个例子，否则这条测试测不出覆盖）
        self.assertTrue(any(p.label.startswith("特性") and p.score < 0 for p in auto))
        labels = {p.label: p.score for p in Scorer(self.db, st).parts("鹰眼瞄具", (), None)}
        self.assertEqual(labels["玩家思路"], -30)
        self.assertFalse(any(k.startswith("特性") for k in labels))
        # 照的电磁炮：自动层是她的角色专属规则（追击按她的攻击力计）和「物理输出」加分，已持有永动机时另有组合
        # 「永动机与充能消耗」；玩家思路在已持有充能来源时给 +40。带「已持有」条件的玩家思路规则已经考虑了组合关系，
        # 自动层（角色专属规则、特性）和组合规则都不再叠加
        auto_labels = ("特性", "角色专属规则", "其他特性")
        owned = ("永动机",)
        st = State(hero="照", owned=owned)
        auto = Scorer(self.db, st, use_player=False).parts("电磁炮", owned, None)
        self.assertTrue(any(p.label.startswith(auto_labels) for p in auto))
        self.assertIn("组合", {p.label for p in auto})
        labels = {p.label: p.score for p in Scorer(self.db, st).parts("电磁炮", owned, None)}
        self.assertEqual(labels["玩家思路"], 40)
        self.assertFalse(any(k.startswith(auto_labels) for k in labels))
        self.assertNotIn("组合", labels)

    def test_conditional_player_rules(self):
        # 已持有（needs_owned）：照的电磁炮，没有充能来源时扣分，已持有永动机时加分
        st = State(hero="照")
        alone = Scorer(self.db, st).parts("电磁炮", (), None)
        paired = Scorer(self.db, st).parts("电磁炮", ("永动机",), None)
        self.assertLess([p.score for p in alone if p.label == "玩家思路"][0], 0)
        self.assertGreater([p.score for p in paired if p.label == "玩家思路"][0], 0)
        # 轮次（max_round）：照的银行卡-余额少只在第 2 轮以前加分，之后按角色特性计算
        early = Scorer(self.db, State(hero="照", round=2)).parts("银行卡-余额少", (), 2)
        late = Scorer(self.db, State(hero="照", round=3)).parts("银行卡-余额少", (), 3)
        self.assertEqual([p.score for p in early if p.label == "玩家思路"], [40])
        self.assertFalse(any(p.label == "玩家思路" for p in late))
        # 地图（maps）：现在的默认推荐没有限定地图的规则，用测试里造的一段玩家思路
        ps = _merge_players("邦妮", [{"hero": "邦妮", "source": "攻略", "rule": [
            {"chips": ["大背包"], "maps": ["水乡古镇"], "score": 40, "reason": "筹码方案：测试用的限定地图规则"},
            {"chips": ["大背包"], "score": -20, "reason": "筹码方案：测试用的不限地图规则"},
        ]}], self.fail)
        db = dataclasses.replace(self.db, player_synergy={**self.db.player_synergy, "邦妮": ps})
        on_map = Scorer(db, State(hero="邦妮", map="水乡古镇")).parts("大背包", (), None)
        off_map = Scorer(db, State(hero="邦妮", map="魔法学院")).parts("大背包", (), None)
        self.assertEqual([p.score for p in on_map if p.label == "玩家思路"], [40])
        self.assertEqual([p.score for p in off_map if p.label == "玩家思路"], [-20])


class PlayerCoreTest(PlayerBase):
    """玩家思路的「必拿」和「不推荐」在一次三选一里的表现（测试用的默认推荐档位）。"""

    def test_core_beats_unlisted_in_same_offer(self):
        # 凛的必拿大背包，单看分数低于玩家思路没列入的探天卫星（游戏内置分全额计入）
        lead = self.db.rules["synergy"]["core_lead"]
        before = Scorer(self.db, State(hero="凛")).score("大背包", (), None)
        self.assertLess(before, Scorer(self.db, State(hero="凛")).score("探天卫星", (), None))
        adv = self.run_case("凛", ["大背包", "额外电池", "探天卫星"])
        ev = self.by_name(adv)
        self.assertEqual(adv.best, "大背包")
        self.assertGreaterEqual(ev["大背包"].total, ev["探天卫星"].total + lead - 1e-6)
        self.assertAlmostEqual(ev["大背包"].immediate, sum(p.score for p in ev["大背包"].parts))
        self.assertIn("必选", ev["大背包"].reasons(limit=1)[0])
        # 补分只在这次三选一里，筹码本身的分（前瞻、重选都用它）不变
        self.assertEqual(Scorer(self.db, State(hero="凛")).score("大背包", (), None), before)

    def test_two_cores_keep_their_order(self):
        adv = self.run_case("凛", ["大背包", "忍术飞镖", "探天卫星"])
        ev = self.by_name(adv)
        scorer = Scorer(self.db, adv.state)  # 同一个状态：前瞻里循环往复的分值和剩余刷新次数有关
        raw = {n: scorer.score(n, (), None) + scorer.lookahead(n, ()) for n in ev}
        self.assertEqual(adv.best, "忍术飞镖")
        self.assertAlmostEqual(ev["忍术飞镖"].total - ev["大背包"].total, raw["忍术飞镖"] - raw["大背包"])
        self.assertGreater(ev["大背包"].total, ev["探天卫星"].total)

    def test_rin_player_tiers(self):
        # 凛的玩家思路分档
        tiers = self.db.player_synergy["凛"].tiers
        want = {
            "core": {"忍术飞镖", "魔法箭袋", "大背包"},
            "first": {"标记喷罐"},
            "second": {"手持风扇-小", "手持风扇-大"},
            "avoid": {"银行卡-余额少", "银行卡-余额多", "银行卡-用不完"},
        }
        for tier, names in want.items():
            self.assertEqual({n for n, t in tiers.items() if t == tier}, names, tier)
        # 星光流派的其余 6 个扣 40；小猪存钱罐获得的是星币、不属于星光流派，不扣分
        scorer = Scorer(self.db, State(hero="凛"))
        star = [c.name for c in self.db.chips.values() if c.school == "星光" and c.name not in tiers]
        self.assertEqual(len(star), 6)
        for name in star:
            with self.subTest(chip=name):
                part = next(p for p in scorer.parts(name, (), None) if p.label == "玩家思路")
                self.assertEqual(part.score, -40)
                self.assertEqual(part.reason, "筹码方案：星光流派对凛无收益")  # 理由固定写这一句
        self.assertIsNone(self.db.chips["小猪存钱罐"].school)
        # 小猪存钱罐不属于星光流派，按流派本不扣分；按攻略视频另扣 20（凛不缺星币，前期有更需要的蓝色筹码；
        # 攻略视频和「攻略」来源的观点有冲突时以视频为准）
        self.assertEqual(scorer.player_score("小猪存钱罐", (), None), -20)
        part = next(p for p in scorer.parts("小猪存钱罐", (), None) if p.label == "玩家思路")
        self.assertEqual((part.reason, part.short), ("筹码方案：凛不缺星币，前期有更需要的蓝色筹码", "筹码方案：凛不缺星币"))
        self.assertNotIn("不在此列", self.db.player_synergy["凛"].note)

    def test_no_lift_without_player_layer(self):
        # 没有玩家思路的角色不受影响
        adv = self.run_case("邦妮", ["标靶", "夹心饼干-可口", "会员推荐信"], star=1, round=4)
        scorer = Scorer(self.db, State(hero="邦妮", star=1, round=4))
        for e in adv.evals:
            self.assertAlmostEqual(e.immediate, scorer.score(e.chip.name, (), 4))

    def test_rin_avoids_bank_cards(self):
        # 凛不应该拿银行卡：三张银行卡都在不推荐档，对上金色鹰眼瞄具时排最后
        tiers = self.db.player_synergy["凛"].tiers
        for bank in ("银行卡-余额少", "银行卡-余额多", "银行卡-用不完"):
            self.assertEqual(tiers.get(bank), "avoid", bank)
        adv = self.run_case("凛", ["银行卡-用不完", "鹰眼瞄具", "夹心饼干-一般"], star=2, rerolls=0)
        ev = self.by_name(adv)
        # 鹰眼瞄具和夹心饼干-一般本来就几乎持平（46% 对 45%）；职业分值累加以后，鹰眼瞄具的攻击 +30 使之后升星的最左格
        # 偏向攻击池（探天卫星等卡牌池的金色筹码变少），前瞻由正转负，首推换成夹心饼干-一般。银行卡照旧排最后
        self.assertNotEqual(adv.best, "银行卡-用不完")
        self.assertEqual(min(ev, key=lambda n: ev[n].total), "银行卡-用不完")

    def test_komachi_cookie_series(self):
        # 小町的三种夹心饼干：默认推荐没有列入，按角色特性计算。同系列里稀有度高的一张不应排在低一级后面
        ps = self.db.player_synergy["小町"]
        self.assertFalse({"夹心饼干-一般", "夹心饼干-可口", "夹心饼干-美味"} & set(ps.tiers))
        offer = ["夹心饼干-一般", "夹心饼干-可口", "夹心饼干-美味"]
        # 职业分值累加以后，夹心饼干-美味带攻击 +10，选择后之后的最左格略偏向攻击池，对小町（卡牌 100）前瞻为负，
        # 总分可能比夹心饼干-可口略低。同系列两张总分相差不到 3 时稀有度高的排前
        # （rules.toml [advice] series_near，只改先后、不改分数），所以按推荐卡片上的最终先后断言美味排在可口、一般前面
        from advisor.ui import text as ui_text
        for star in range(4):
            for rnd in (1, 8):
                adv = self.run_case("小町", offer, star=star, round=rnd)
                ev = self.by_name(adv)
                order = [item.chip.name for _, _, item in ui_text.ordered_options(adv) if item is not adv.reroll]
                with self.subTest(star=star, round=rnd):
                    self.assertLess(order.index("夹心饼干-美味"), order.index("夹心饼干-可口"))
                    self.assertLess(order.index("夹心饼干-美味"), order.index("夹心饼干-一般"))
                    self.assertGreater(ev["夹心饼干-美味"].percent, ev["夹心饼干-可口"].percent)
                    self.assertNotIn(adv.best, ("夹心饼干-可口", "夹心饼干-一般"))
                    self.assertEqual(adv.evals[adv.top].chip.name, "夹心饼干-美味")
                    self.assertGreater(ev["夹心饼干-美味"].immediate, ev["夹心饼干-可口"].immediate)
                    self.assertGreater(ev["夹心饼干-美味"].total, ev["夹心饼干-一般"].total)


class SynergyTableTest(Base):
    """适配表本身的一致性：横向检查（tools/audit_synergy.py）会查的几类问题，用测试锁住。"""

    def test_heal_sources_listed_together(self):
        # 看重治愈层数的规则，要把同样每回合产出治愈层数的维生素药丸、可口糖果一起列上（容易漏掉）：同一个角色、同一来源、
        # 同样的条件和加减方向的规则里要有这两张。分值可以分开写（多萝西的紧急治疗之后每回合开始恢复一次生命，比只在出牌、
        # 获得卡牌时给治愈的两张多加；米米的维生素药丸另有一条扣得更多）
        def key(r):
            return (r.origin, r.score > 0, r.maps, r.needs_owned, r.max_round, r.difficulty, r.needs_source, r.without_source)

        for hero, hs in self.db.hero_synergy.items():
            for rule in hs.rules:
                if "医疗箱-紧急治疗" not in rule.chips:
                    continue
                covered = set().union(*(r.chips for r in hs.rules if key(r) == key(rule)))
                with self.subTest(hero=hero, origin=rule.origin, reason=rule.reason):
                    self.assertIn("维生素药丸", covered)
                    self.assertIn("可口糖果", covered)

    def test_dorothy_heal_chips_not_buried(self):
        # 多萝西的治愈来源（维生素药丸、可口糖果）：恢复生命时获得温暖，治愈层数越多越好；只用自动层时也要排进前 25（不能埋到第 41、42）
        scorer = Scorer(self.db, State(hero="多萝西", star=1), use_player=False)
        general = [c.name for c in self.db.chips.values() if not c.granted and not c.maps]
        order = sorted(general, key=lambda n: -scorer.score(n, (), None))
        for chip in ("维生素药丸", "可口糖果"):
            self.assertLess(order.index(chip), 25, chip)

    def all_rules(self):
        for hero, hs in self.db.hero_synergy.items():
            for rule in hs.rules:
                yield hero, rule
        for hero, ps in self.db.player_synergy.items():
            for rule in ps.rules:
                yield hero, rule

    def test_granted_chips_not_scored_as_candidates(self):
        # 只靠发放、不进三选一的筹码（granted：龙宫游乐园的四张，决赛大赛场的畸变组件发放的电击枪、原初意识）不作为候选
        # 给分：特性规则、角色专属规则、默认推荐、组合和队友调分的候选里都没有它们。已持有它们时给别的
        # 筹码加分的规则照写（组合的 owned）：发放的筹码同样在已有筹码里
        granted = {c.name for c in self.db.chips.values() if c.granted}
        self.assertLessEqual({"电击枪", "原初意识"}, granted)
        found = [(h, r.origin, sorted(r.chips & granted)) for h, r in self.all_rules() if r.chips & granted]
        found += [(h, "档位", c) for h, ps in self.db.player_synergy.items() for c in ps.tiers if c in granted]
        found += [("组合", c.name, sorted(c.chips & granted)) for c in self.db.combos if c.chips & granted]
        found += [("队友", r.reason, sorted(set(r.chips) & granted)) for r in self.db.teammate_rules
                  if set(r.chips) & granted]
        self.assertEqual(found, [])
        self.assertTrue(any(c.owned & granted for c in self.db.combos))  # 已持有惊涛御守时的组合照留

    def test_reasons_use_glossary_wording(self):
        # 理由用书面语，写触发条件和数值；「星光」「星币」不混用，不写「金币」
        banned = re.compile(
            "挨打|上标记|打标记|回血|补血|收人头|打怪|谁拿|人人|用得上|想要的|身板|容错|本来就|好歹|反而|才稳"
            "|金币|星光币|星光系|升星点|紫拳击|攒|(?<!必)拿"
        )
        texts = [(f"{h} {r.origin}", r.reason) for h, r in self.all_rules()]
        texts += [(f"{h} why", hs.why) for h, hs in self.db.hero_synergy.items()]
        for where, text in texts:
            with self.subTest(where=where, text=text):
                self.assertIsNone(banned.search(text))

    def test_starlight_and_star_coins_not_mixed(self):
        # 「星光」是叠层状态，「星币」是货币：提到星光的特性规则和角色规则，至少有一个筹码的效果里有星光
        for hero, rule in self.all_rules():
            if rule.origin == "玩家思路" or "星光" not in rule.reason:
                continue
            with self.subTest(hero=hero, origin=rule.origin, reason=rule.reason):
                self.assertTrue(any("星光" in self.db.chips[c].effect for c in rule.chips))
        # 小猪存钱罐只获得星币、没有流派：它的理由里不出现星光
        for hero, rule in self.all_rules():
            if rule.chips == {"小猪存钱罐"}:
                self.assertNotIn("星光", rule.reason)

    def test_engine_reasons_use_glossary_wording(self):
        # 前瞻写「前瞻」不写「后续」；标签分的理由里保留职业名（悬浮窗的标签从这句里找职业名）
        adv = self.run_case("邦妮", ["标靶", "夹心饼干-可口", "会员推荐信"], star=1, round=4)
        target = self.by_name(adv)["标靶"]
        tag = next(p for p in target.parts if p.label == "标签分")
        self.assertEqual(tag.reason, "筹码标签「攻击」与角色当前职业相符")
        self.assertTrue(any("（前瞻 +" in r for r in target.reasons(limit=5)))  # reasons() 给命令行和诊断包，照写分值
        adv = self.run_case("凛", ["银行卡-余额多", "会员推荐信", "优雅之羽"])
        self.assertEqual(adv.notes, [LOW_NOTE, "地图：待识别，按候选地图平均计算"])
        # 必选的整句是凛的玩家思路原文（测试用的默认推荐档位），补分后另起一句「优先于本次未列入筹码方案的筹码」
        db = with_player_layer(self.db)
        core = self.by_name(self.run_case("凛", ["大背包", "额外电池", "探天卫星"], db=db))["大背包"]
        note = db.player_synergy["凛"].note
        self.assertEqual(core.reasons(limit=1)[0].rsplit("（", 1)[0], f"{note}。优先于本次未列入筹码方案的筹码")
        for e in (*adv.evals, core, target):
            for r in e.reasons(limit=10):
                self.assertNotIn("后续", r)
                self.assertNotIn("想要的", r)

    def test_star_attack_trait_follows_level_rewards(self):
        # 「升星加攻」按等级奖励划分：达到 2 星时攻 +2 的角色都带，其余都不带（等级奖励写在 mechanics/characters.md）
        blocks = character_blocks()
        for name in self.db.heroes:
            with self.subTest(hero=name):
                block = next((b for b in blocks if name in b.splitlines()[0]), "")
                self.assertTrue(block, f"mechanics/characters.md 里找不到「{name}」那一段")  # 找不到时不能当成「不带」
                expected = "达到 2 星时攻 +2" in block
                self.assertEqual("升星加攻" in self.db.hero_synergy[name].traits, expected, name)

    def test_torch_needs_starlight_source(self):
        # 手电筒-强光的 +15 只在已持有能攒星光的筹码时才加，没有就不加。
        # 没有星光来源时它每击中一次才攒 1 层，打 4 次才 +1 攻击力；能攒星光的筹码 8 张，
        # 和 rules.toml [income] starlight 的星光那一组一致，不含手电筒-强光自己。
        rule = next(r for r in self.db.trait_rules["物理输出"] if "手电筒-强光" in r.chips)
        self.assertEqual(rule.score, 15)
        self.assertEqual(rule.needs_owned, {
            "银行卡-余额少", "银行卡-余额多", "银行卡-用不完", "“8面”骰子",
            "ATM机", "星币锤", "紫色飞星", "金色飞星",
        })

        def torch(owned):
            owned = tuple(owned)
            return Scorer(self.db, State(hero="美咲", star=1, round=4, owned=owned)).parts("手电筒-强光", owned, 4)

        # 没有星光来源（美咲带「物理输出」）：拿不到这 15 分
        self.assertEqual([p for p in torch(("拳击手套-高级",)) if p.origin == "物理输出"], [])
        # 任意一张星光来源在手就拿到
        for source in sorted(rule.needs_owned):
            with self.subTest(source=source):
                self.assertEqual([p.score for p in torch(("拳击手套-高级", source)) if p.origin == "物理输出"], [15])
        # 手电筒-强光自己、以及不属于星光来源的筹码都不算
        for not_source in ("手电筒-强光", "缓冲盾牌"):
            with self.subTest(not_source=not_source):
                self.assertEqual([p for p in torch(("拳击手套-高级", not_source)) if p.origin == "物理输出"], [])
        # 16:34:37 那个局面（美咲、1 星、第 4 轮、已有拳击手套-高级）：没有星光来源，首推不是手电筒-强光
        adv = self.run_case("美咲", ["拳击手套-初级", "手电筒-强光", "标记喷罐"], star=1, round=4,
                            map="异变图书馆", owned=("拳击手套-高级",), rerolls=2, progress=5, difficulty=NORMAL)
        self.assertNotEqual(adv.best, "手电筒-强光")


class SourceRuleTest(Base):
    """靠某种体系才起作用的筹码，没有来源时不照加分：现在是充能那两张，电磁炮的「聚怪」、电流核心的
    「技能依赖」。标记那三张（魔法箭袋、忍术飞镖、鹰眼瞄具）不看有没有标记来源，照常加分。

    来源名单在 rules.toml [sources]：已持有其中的筹码，或自己的角色、任一队友是其中的角色时算有来源；适配表规则写
    needs_source（有来源时才生效）、without_source（没有来源时才生效）。默认推荐（玩家思路）列了这几张的角色按默认推荐
    计算，不受影响。"""

    CHARGE = ("永动机", "能量回收", "电流剑", "电击手套", "跃迁引擎")
    AUTO = ("特性", "其他特性", "角色专属规则")

    def auto(self, hero, chip, owned=(), mates=(), use_player=True, scheme=None):
        """自动层的分项（特性、其他特性、角色专属规则）：[(来源, 分值)]，按分项的先后。「其他特性」的来源是几条规则的特性名
        用「、」连起来；mates 写角色名或 Teammate。"""
        owned = tuple(owned)
        teammates = tuple(m if isinstance(m, Teammate) else Teammate(m) for m in mates)
        st = State(hero=hero, star=1, round=4, owned=owned, teammates=teammates)
        parts = Scorer(self.db, st, use_player=use_player, scheme=scheme).parts(chip, owned, 4)
        return [(p.origin, p.score) for p in parts if p.label in self.AUTO]

    def rest(self, *scores, top):
        """「其他特性」那一项：其余加分合计 × extra_rule_factor，不超过 cap − 最高那条（engine.parts._auto_parts）。"""
        s = self.db.rules["synergy"]
        return round(min(s["extra_rule_factor"] * sum(scores), s["cap"] - top))

    def test_source_lists(self):
        self.assertEqual(sorted(self.db.sources), sorted(["充能", "标记来源", "持续治愈", "轨道炮", "补牌", "出牌数",
                                                          "预先标记", "多拿牌", "星光筹码", "治愈来源", "治愈筹码"]))
        charge = self.db.sources["充能"]
        self.assertEqual(charge.chips, set(self.CHARGE))
        self.assertEqual(charge.heroes, frozenset())
        # 和组合规则的已持有一栏是同一批筹码（改一处时另一处跟着改）
        combos = {c.name: c for c in self.db.combos}
        self.assertEqual(charge.chips, combos["永动机与充能消耗"].owned | combos["充能来源与充能消耗"].owned)
        # 另几项给角色专属规则用：不必发起战斗就能施加标记的（队友邦妮的技能也算）、每回合给治愈的（会为队友恢复生命的
        # 角色也算，同「治疗友方」特性的几名）、获得轨道炮的、补牌的、提高出牌数的
        sources = self.db.sources
        self.assertEqual((sources["标记来源"].chips, sources["标记来源"].heroes),
                         ({"标靶", "标记喷罐", "手持风扇-小", "手持风扇-大"}, {"邦妮"}))
        healers = {h for h, hs in self.db.hero_synergy.items() if "治疗友方" in hs.traits}
        self.assertEqual((sources["持续治愈"].chips, sources["持续治愈"].heroes),
                         ({"医疗箱-完备治疗", "医疗箱-紧急治疗", "大碗炖肉"}, healers))
        self.assertEqual({k: (sources[k].chips, sources[k].heroes) for k in ("轨道炮", "补牌", "出牌数")}, {
            "轨道炮": ({"探天卫星"}, frozenset()), "补牌": ({"探天卫星", "魔法秘典"}, frozenset()),
            "出牌数": ({"大背包", "忍术飞镖", "古老法杖", "可口糖果", "电流核心"}, frozenset())})
        self.assertLessEqual(sources["持续治愈"].chips, set(self.db.rules["heal_knife"]["layers"]))
        # 只给个别角色的专属规则用的几项：出牌前就施加标记的（凛、邦妮的技能也算）、每回合多获得卡牌的、星光流派的全部筹码、
        # 治愈来源（[heal_knife] layers 的 7 张，加上会为队友恢复生命、施加治愈的角色；「治愈筹码」只列这 7 张）
        layers = set(self.db.rules["heal_knife"]["layers"])
        self.assertEqual({k: (sources[k].chips, sources[k].heroes) for k in ("预先标记", "多拿牌", "治愈来源", "治愈筹码")}, {
            "预先标记": ({"标靶", "手持风扇-小", "手持风扇-大"}, {"凛", "邦妮"}),
            "多拿牌": ({"探天卫星", "智能手表", "手持风扇-大", "无限之蛇"}, frozenset()),
            "治愈来源": (layers, healers), "治愈筹码": (layers, frozenset())})
        starlight = {c.name for c in self.db.chips.values() if c.school == "星光" and not c.granted}
        self.assertEqual((sources["星光筹码"].chips, sources["星光筹码"].heroes), (starlight, frozenset()))
        # 特性规则里带来源条件的：（特性, 筹码, 分值, 有来源才生效, 没有来源才生效）
        found = sorted((t, tuple(sorted(r.chips)), r.score, r.needs_source or "", r.without_source or "")
                       for t, rules in self.db.trait_rules.items() for r in rules if r.needs_source or r.without_source)
        self.assertEqual(found, sorted([
            ("技能依赖", ("电流核心",), 30, "充能", ""),
            ("聚怪", ("电磁炮",), 60, "充能", ""),
            ("通用", ("电磁炮",), -20, "", "充能"),
        ]))
        # 理由照手电筒那条的写法：有来源才加的写「已有X来源，……」
        for _, rules in self.db.trait_rules.items():
            for r in rules:
                if r.needs_source:
                    with self.subTest(chips=sorted(r.chips), score=r.score):
                        self.assertTrue(r.reason.startswith(f"已有{r.needs_source}来源，"), r.reason)
                        self.assertTrue(r.short.startswith(f"已有{r.needs_source}来源，"), r.short)
                        self.assertLessEqual(len(r.short), SHORT_LIMIT)

    def test_rule_conditions(self):
        # Rule.applies 的 sources（已有来源的体系名）：needs_source 有这种来源时才生效，without_source 没有时才生效；
        # 不给时按一种来源都没有算（这里的「甲」「乙」只是名字，和资料里的来源无关）
        need = Rule(chips=frozenset({"书签"}), score=10, reason="", origin="测试", needs_source="甲")
        lack = Rule(chips=frozenset({"书签"}), score=5, reason="", origin="测试", without_source="甲")
        self.assertFalse(need.applies(None, frozenset(), None))
        self.assertTrue(lack.applies(None, frozenset(), None))
        self.assertTrue(need.applies(None, frozenset(), None, None, frozenset({"甲"})))
        self.assertFalse(lack.applies(None, frozenset(), None, None, frozenset({"甲"})))
        self.assertFalse(need.applies(None, frozenset(), None, None, frozenset({"乙"})))
        self.assertTrue(lack.applies(None, frozenset(), None, None, frozenset({"乙"})))

    def test_mark_chips_score_without_source(self):
        # 标记那三张不写 needs_source / without_source：不管有没有标记来源都照常加分
        for trait, chips, score in (("物理输出", ("鹰眼瞄具",), 45), ("效果牌伤害", ("忍术飞镖",), 60),
                                   ("效果牌伤害", ("魔法箭袋",), 50), ("标记受益", ("魔法箭袋", "忍术飞镖"), 30)):
            with self.subTest(trait=trait, chips=chips):
                rules = [r for r in self.db.trait_rules[trait] if r.chips == set(chips)]
                self.assertEqual([(r.score, r.needs_source, r.without_source) for r in rules], [(score, None, None)])
        # 鹰眼瞄具的理由、短句写它自己的效果，分值和普通瞄具不同
        eye = next(r for r in self.db.trait_rules["物理输出"] if r.chips == {"鹰眼瞄具"})
        self.assertEqual(eye.reason, "攻击时先施加 1 层标记，本次攻击的攻击力增加目标标记层数的 2 倍")
        self.assertEqual(eye.short, "攻击时按标记层数提高攻击力")
        plain = next(r for r in self.db.trait_rules["物理输出"] if r.chips == {"普通瞄具"})
        self.assertEqual((plain.score, plain.needs_source), (35, None))
        # 有没有标记来源（已持有施加标记的筹码、队友里是凛或邦妮）分项完全一样
        for hero, chip, use_player in (("美咲", "鹰眼瞄具", True), ("赛克斯", "魔法箭袋", False),
                                       ("小町", "忍术飞镖", True)):
            with self.subTest(chip=chip):
                bare = self.auto(hero, chip, use_player=use_player)
                self.assertTrue(bare)  # 没有标记来源也照常加分
                for owned, mates in ((("标靶",), ()), ((), ("邦妮",))):
                    self.assertEqual(self.auto(hero, chip, owned, mates, use_player=use_player), bare)
        self.assertEqual(self.auto("美咲", "鹰眼瞄具"), [("物理输出", 45)])
        # 赛克斯还没有探天卫星（rules.toml [sources] 轨道炮）时角色专属规则另扣 55：她的技能不产生效果牌，复制不到什么
        self.assertEqual(self.auto("赛克斯", "魔法箭袋", use_player=False),
                         [("效果牌伤害", 50), ("标记受益", self.rest(30, top=50)), ("角色", -55)])
        self.assertEqual(self.auto("小町", "忍术飞镖"), [("效果牌伤害", 60), ("多出效果牌", self.rest(30, top=60))])
        # 实例（由推荐基准 offers35/01341 改的）：阿兰娜 3 星、第 11 轮，已有三叉戟：不管队友里有没有邦妮都首推鹰眼瞄具。
        # 基准里那一局另有紫色飞星（已进入星光）：选择鹰眼瞄具以后治愈流派不再出现，推荐流派扣分，两种队友都首推夹心饼干-美味。
        # 基准里剩 5 次刷新；这一局之后的三选一不到 1 次，多出来的刷新按可以连着刷估值、首推会是刷新，这里比的是候选之间，
        # 改为只剩 1 次刷新（没有多出来的）
        offer = ["鹰眼瞄具", "魔法箭袋", "夹心饼干-美味"]
        for owned, best in ((("三叉戟",), "鹰眼瞄具"), (("三叉戟", "紫色飞星"), "夹心饼干-美味")):
            kw = dict(star=3, round=11, owned=owned, rerolls=1)
            with self.subTest(owned=owned):
                self.assertEqual(self.run_case("阿兰娜", offer, **kw).best, best)
                self.assertEqual(self.run_case("阿兰娜", offer, teammates=(Teammate("邦妮"),), **kw).best, best)

    def test_hero_source_from_role(self):
        # 通用写法：rules.toml [sources] 的 heroes 写角色时，自己的角色或任一队友是其中的角色算有这种来源。
        # 真实资料里现在只有充能这一项、不靠角色提供，这条写法用自己造的一份资料试
        db = dataclasses.replace(self.db, sources={
            "测试体系": Source(name="测试体系", chips=frozenset({"书签"}), heroes=frozenset({"美咲"}))})
        self.assertEqual(Scorer(db, State(hero="美咲", star=1, round=4)).held_sources(frozenset()), {"测试体系"})
        for st in (State(hero="小町", star=1, round=4),
                   State(hero="小町", star=1, round=4, teammates=(Teammate(None),))):
            with self.subTest(teammates=st.teammates):
                self.assertEqual(Scorer(db, st).held_sources(frozenset()), frozenset())
        mate = Scorer(db, State(hero="小町", star=1, round=4, teammates=(Teammate("美咲", bot=True),)))
        self.assertEqual(mate.held_sources(frozenset()), {"测试体系"})
        self.assertEqual(mate.held_sources(frozenset({"书签"})), {"测试体系"})  # chips 那一半也算

    def test_charge_chips_need_charge_source(self):
        # 电磁炮：「聚怪」+60 要有充能来源（玲玲、橘雪莉）；「物理输出」+35 照旧；没有充能来源时对所有角色另扣 20
        # （「通用」，范围伤害难以触发），和「物理输出」那条合成一个标签
        self.assertEqual(self.auto("玲玲", "电磁炮"), [("物理输出", 35), ("通用", -20)])
        for source in self.CHARGE:
            with self.subTest(source=source):
                self.assertEqual(self.auto("玲玲", "电磁炮", [source]), [("聚怪", 60), ("物理输出", self.rest(35, top=60))])
        # 电流核心：「技能依赖」+30（消耗 5 层充能使冷却 -1）要有充能来源；「多出效果牌」+20（出牌数 +1）照旧
        # 姬梦朝没有充能来源时角色专属规则另扣 10（她平时的出牌数常用不完），和「多出效果牌」那条合成一个标签
        self.assertEqual(self.auto("姬梦朝", "电流核心"), [("多出效果牌", 20), ("角色", -10)])
        self.assertEqual(self.auto("姬梦朝", "电流核心", ["电流剑"]), [("技能依赖", 30), ("多出效果牌", self.rest(20, top=30))])
        self.assertEqual(self.auto("美咲", "电流核心"), [])
        for source in self.CHARGE:
            with self.subTest(source=source):
                self.assertEqual(self.auto("美咲", "电流核心", [source]), [("技能依赖", 30)])
        # 安全气囊（受到伤害时才获得 1 层）、只消耗充能的电磁炮、别的流派的筹码都不算；充能没有靠角色的来源
        for not_source in ("安全气囊", "电磁炮", "标靶"):
            with self.subTest(not_source=not_source):
                self.assertEqual(self.auto("美咲", "电流核心", [not_source]), [])
        self.assertEqual(self.auto("美咲", "电流核心", mates=["橘雪莉", "照", "凛"]), [])
        # 一张充能筹码都没有时的开套预判（rules.toml [charge_build]）照常
        parts = Scorer(self.db, State(hero="美咲", star=1, round=4)).parts("电流核心", (), 4)
        self.assertEqual([p.label for p in parts if p.origin == "charge_build"], ["组合"])
        # 实例（推荐基准 offers35/01002）：阿兰娜 0 星、第 1 轮、星趴·梦想号，没有充能来源：电流核心没有「技能依赖」加分，
        # 首推拳击手套-初级
        adv = self.run_case("阿兰娜", ["拳击手套-初级", "电流核心", "跃迁引擎"], round=1, map="星趴·梦想号")
        self.assertEqual(adv.best, "拳击手套-初级")
        self.assertFalse([p for p in self.by_name(adv)["电流核心"].parts if p.origin == "技能依赖"])

    def test_railgun_without_charge(self):
        # 没有充能来源时电磁炮的范围伤害难以触发：对所有角色扣 20（「通用」），星魅琉华例外（主动技能对经过的怪物造成
        # 「2 + 攻击力」的伤害，攻击力 +2 同样生效）；有充能来源时不扣
        rule = next(r for r in self.db.trait_rules[GENERAL_TRAIT] if r.chips == {"电磁炮"})
        self.assertEqual((rule.score, rule.without_source, rule.exclude), (-20, "充能", {"星魅琉华"}))
        self.assertLessEqual(len(rule.short), SHORT_LIMIT)
        self.assertIsNone(BANNED_WORDING.search(rule.reason + rule.short))
        for hero in ("美咲", "照", "凛", "米米"):
            with self.subTest(hero=hero):
                self.assertIn((GENERAL_TRAIT, -20), self.auto(hero, "电磁炮"))
                self.assertNotIn((GENERAL_TRAIT, -20), self.auto(hero, "电磁炮", ["永动机"]))
        self.assertEqual(self.auto("星魅琉华", "电磁炮"), [("物理输出", 35)])
        # 物理输出角色：「物理输出」+35 是最高一条加分，两条合成一个标签（攻击力加成那一半照样生效）
        parts = [p for p in Scorer(self.db, State(hero="美咲", star=1, round=4)).parts("电磁炮", (), 4) if p.net]
        self.assertEqual([(p.origin, p.score) for p in parts], [("物理输出", 35), (GENERAL_TRAIT, -20)])
        self.assertEqual({p.net for p in parts}, {"攻击力加成（缺充能，范围伤害难触发）"})

    def test_assumed_owned_counts_as_source(self):
        # 来源按计算分项时的已有筹码判断，不按局面里现在的已有筹码：推算「拿了以后」（开套预判、选了美工刀以后获得治愈来源
        # 的概率）给之后的候选打分时，假设拿到的筹码也算来源。例：美咲现在一张充能筹码都没有，给永动机算开套预判要按
        # 「已有筹码 + 永动机」给配套的电流核心打分，这时电流核心照常有「技能依赖」+30
        sc = Scorer(self.db, State(hero="美咲", star=1, round=4))

        def auto(owned):
            return [(p.origin, p.score) for p in sc.parts("电流核心", owned, 4) if p.label in self.AUTO]

        self.assertEqual(sc.held_sources(frozenset()), frozenset())
        self.assertEqual(sc.held_sources(frozenset({"永动机"})), {"充能"})
        self.assertEqual(auto(()), [])
        self.assertEqual(auto(("永动机",)), [("技能依赖", 30)])

    def test_player_layer_unaffected(self):
        # 默认推荐（玩家思路，这里用测试用的档位）列了这几张的角色照旧按默认推荐计算：自动层整块不用，有没有来源都一样
        db = with_player_layer(self.db)
        cases = (
            ("凛", "魔法箭袋", "core"), ("凛", "忍术飞镖", "core"), ("小町", "魔法箭袋", "first"),
            ("小町", "电流核心", "first"), ("赛克斯", "魔法箭袋", "second"), ("赛克斯", "忍术飞镖", "second"),
            ("橘雪莉", "电磁炮", "first"), ("橘雪莉", "鹰眼瞄具", "second"), ("姬梦枫", "鹰眼瞄具", "first"),
            ("真梦梓", "鹰眼瞄具", "first"), ("照", "鹰眼瞄具", "second"), ("照", "电流核心", "second"),
            ("远野汉娜", "电流核心", "second"),
        )
        for hero, chip, tier in cases:
            for owned in ((), ("标靶", "永动机")):
                with self.subTest(hero=hero, chip=chip, owned=owned):
                    parts = Scorer(db, State(hero=hero, star=1, round=4, owned=owned)).parts(chip, owned, 4)
                    self.assertEqual([p.score for p in parts if p.label == "玩家思路"], [db.rules["synergy"][tier]])
                    self.assertFalse([p for p in parts if p.label in self.AUTO])
        # 带条件的默认推荐规则照旧：远野汉娜的标记流派 -30；照的电磁炮按已持有电流剑、永动机、能量回收计（+40 / -20）
        for chip in ("鹰眼瞄具", "魔法箭袋", "忍术飞镖"):
            self.assertEqual(Scorer(db, State(hero="远野汉娜")).player_score(chip, ("标靶",), None), -30)
        self.assertEqual(Scorer(db, State(hero="照")).player_score("电磁炮", (), None), -20)
        self.assertEqual(Scorer(db, State(hero="照")).player_score("电磁炮", ("永动机",), None), 40)
        self.assertEqual(Scorer(db, State(hero="照")).player_score("电磁炮", ("电击手套",), None), -20)


class RefreshWordingTest(Base):
    """界面和命令行一律叫「刷新」，次数照游戏写「刷新 ×N」。"""

    def test_option_is_called_refresh(self):
        self.assertEqual(REROLL, "刷新")
        adv = self.run_case("派德曼", ["会员推荐信", "小猪存钱罐", "书签"], star=1, round=4)
        self.assertEqual(adv.best, "刷新")

    def test_cli_wording(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--hero", "派德曼", "--star", "1", "--round", "4",
                      "--offer", "会员推荐信", "小猪存钱罐", "书签"])
        text = out.getvalue()
        self.assertIn("刷新 ×3", text)
        self.assertIn("星数：1 星", text)
        self.assertIn("推荐：刷新", text)
        self.assertIn("刷新后最高总分约", text)
        self.assertIn("扣除刷新代价", text)
        self.assertIn("刷新后出现总分高于当前候选筹码最高总分的筹码的概率", text)
        self.assertIn("地图：待识别，按候选地图平均计算", text)
        self.assertIn("预计之后还有筹码三选一", text)
        self.assertNotIn("重选", text)

    def test_cli_json_and_extras(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--hero", "凛", "--offer", "标靶", "大背包", "缓冲盾牌", "--teammates", "邦妮", "凛:电脑",
                      "--mutators", "暴雨", "--map-candidates", "魔法学院", "水乡古镇", "--json"])
        data = json.loads(out.getvalue())
        # 首推是推荐度最高的选项：候选里最高的那张，或者刷新
        top = max(data["chips"], key=lambda c: c["percent"])
        self.assertEqual(data["top"], "标靶")
        self.assertEqual(data["top"], top["name"])
        self.assertEqual(data["best"], REROLL if data["reroll"]["percent"] > top["percent"] else top["name"])
        # 队友两名都带「标记受益」（邦妮真人、凛电脑，「凛:电脑」也认得出）：标靶的「队友」分项按两名计（每名 +10，最多 20），
        # 加上推荐流派，首推标靶
        self.assertEqual(data["best"], "标靶")
        chips = {c["name"]: c for c in data["chips"]}
        self.assertEqual([p["score"] for p in chips["标靶"]["parts"] if p["label"] == "队友"], [20])
        self.assertEqual(data["maps"], ["魔法学院", "水乡古镇"])
        self.assertTrue(any(n.startswith("畸变组件「暴雨」") for n in data["notes"]))
        self.assertTrue(all(c["parts"] for c in data["chips"]))


class PicksLeftTest(Base):
    """剩余三选一次数的推算。"""

    # 一局的任务和筹码商店三选一（data/maps.json 的 chip_tasks 按现役地图取平均，见 engine/context.py 的 _picks_left）：
    # 按测试钉死的「今天」2026-10-10（tests/__init__.py 的 ACC_TODAY，不是资料日期）算，现役 9 张图 —— 异变图书馆 4、
    # 龙宫游乐园 4、幽魂暗巷 4、园林中庭 4、森秋幻境 4、星趴·梦想号 3、御魂庆典 3、水乡古镇 3、魔法学院 3，合计 32，平均 32/9。
    # 异变图书馆开放到 2026-10-14（until），2026-10-15 起现役 8 张图、28/8 = 3.5。
    TASKS_AVG = 32 / 9
    PER_GAME = TASKS_AVG * 1.0 + 1.0  # × task_rate + 筹码商店

    def picks(self, **kw):
        kw.setdefault("hero", "邦妮")
        return Scorer(self.db, State(**kw)).picks

    def test_calibrated_to_7_to_8_chips_per_game(self):
        # 实际对局的经验：一局 7–8 个筹码。开局（0 星、无筹码、轮次待识别）：升星 3 次 + 任务和商店 4.5556 = 7.5556
        p = self.picks(star=0)
        self.assertEqual(p.levelups, 3)
        self.assertAlmostEqual(p.total, 3 + self.PER_GAME)
        cfg = self.db.rules["picks"]
        self.assertEqual(cfg["end_round"], 13)

    def test_counts_down_with_stars_and_owned(self):
        self.assertAlmostEqual(self.picks(star=1).total, 2 + self.PER_GAME)  # 刚升到 1 星的三选一
        # 2 星、已有 3 个筹码（1 次升星 + 2 次任务或商店）：之后升星 1 次 + 任务和商店 4.5556 − 2
        p = self.picks(star=2, owned=("标靶", "缓冲盾牌", "书签"))
        self.assertAlmostEqual(p.total, 1 + self.PER_GAME - 2)
        # 任务来源的三选一本身也算一次任务：2 星、已有 2 个筹码（两次升星），这一次之后任务和商店还剩 4.5556 − 1
        p = self.picks(star=2, owned=("标靶", "缓冲盾牌"), source="task")
        self.assertAlmostEqual(p.total, 1 + self.PER_GAME - 1)

    def test_direct_chips_not_counted_as_picks(self):
        """不经三选一拿到的筹码（飞羽手环定期给的、神秘宝藏的宝箱等）不算拿过一次三选一：State.direct 个照旧留给
        之后的任务和筹码商店。direct 不超过已有筹码数。"""
        owned = ("标靶", "缓冲盾牌", "书签")
        base = self.picks(star=2, owned=owned)
        one = self.picks(star=2, owned=owned, direct=1)
        self.assertAlmostEqual(one.total, base.total + 1)
        self.assertAlmostEqual(one.extras, base.extras + 1)
        self.assertEqual(State(hero="邦妮").direct, 0)  # 旧记录里没有这一项时按 0
        self.assertAlmostEqual(self.picks(star=2, owned=owned, direct=2).total, 1 + self.PER_GAME)
        self.assertAlmostEqual(self.picks(star=2, owned=owned, direct=10).total, 1 + self.PER_GAME)

    def test_capped_by_rounds_left(self):
        # 第 14 轮：疯狂难度进度上限 16（候选地图的常见值），只剩 2 轮
        p = self.picks(star=0, round=14)
        self.assertEqual(p.cap, 16)
        self.assertEqual(p.rounds_left, 2)
        self.assertTrue(p.capped)
        self.assertEqual(p.total, 2)
        # 地图已知时用这张地图的进度上限（御魂庆典疯狂 19）
        p = self.picks(star=0, round=14, map="御魂庆典")
        self.assertEqual((p.cap, p.rounds_left), (19, 5))
        # 轮次越晚，任务和商店三选一越少
        self.assertGreater(self.picks(star=1, round=2).total, self.picks(star=1, round=9).total)

    def test_not_capped_by_owned_count(self):
        """持有的筹码数不设上限（一局能拿超过 10 个，「查看筹码」面板一页 10 格、多了翻页）：剩余三选一次数
        不按已有筹码的个数封顶，已有 8 个、9 个、12 个时都照常按之后的升星和任务、商店估。"""
        plain = [c.name for c in self.db.chips.values() if not c.maps and not c.granted]
        for n in (8, 9, 12):
            with self.subTest(owned=n):
                p = self.picks(star=1, owned=tuple(plain[:n]), source="task")
                # 已有筹码多于一局的任务和商店三选一，只剩之后的 2 次升星
                self.assertEqual((p.levelups, p.extras, p.total), (2, 0.0, 2.0))
        self.assertFalse(hasattr(self.picks(star=0), "slots"))  # 不记空着的格数

    def test_still_counts_after_ten_owned(self):
        """已有 9 个、这次拿的是第 10 个：之后照常有三选一，前瞻、刷新代价和循环往复的额外三选一照常计分
        （不按「满 10 个就不能再拿」都记 0）。"""
        plain = [c.name for c in self.db.chips.values() if not c.maps and not c.granted and c.name != "循环往复"]
        owned = tuple(plain[:9])
        offer = [c for c in plain[9:] if c != "循环往复"][:2] + ["循环往复"]
        st = State(hero="邦妮", star=1, round=8, owned=owned, source="task", rerolls=2)
        adv = advise(self.db, st, offer)
        self.assertEqual(adv.picks.total, 2)  # 之后的 2 次升星
        self.assertEqual(Scorer(self.db, st, offer=offer).lookahead_times(), self.db.rules["lookahead"]["picks"][1])
        endless = next(e for e in adv.evals if e.chip.name == "循环往复")
        part = next(p for p in endless.parts if p.label == "循环往复")
        self.assertGreater(part.score, 0)
        self.assertEqual(part.reason, "增加 3 次刷新；3 回合后获得 1 次筹码三选一")
        self.assertEqual(part.short, "3 次刷新和 1 次三选一")
        self.assertIsNotNone(adv.reroll)
        self.assertGreater(adv.reroll.cost, 0)

    def test_lookahead_times_not_capped_by_owned_count(self):
        """前瞻影响的选择次数（0~1 星为 2 次）不按已有筹码的个数封顶：已有 8 个时也按 2 次算（不按只剩 1 格算成 1 次）。"""
        plain = [c.name for c in self.db.chips.values() if not c.maps and not c.granted and c.name != "循环往复"]
        times = self.db.rules["lookahead"]["picks"][1]
        self.assertGreater(times, 1)
        for n in (8, 5):
            owned = tuple(plain[:n])
            scorer = Scorer(self.db, State(hero="邦妮", star=1, round=5, owned=owned, source="task"))
            m = scorer.maps[0]
            name = next(c for c in plain[n:] if lookahead_on(scorer, c, owned, m))
            delta = scorer.future((*owned, name), map_name=m) - scorer.future(owned, zero=name, map_name=m)
            with self.subTest(owned=n):
                self.assertAlmostEqual(lookahead_on(scorer, name, owned, m),
                                       self.db.rules["lookahead"]["weight"] * times * delta)

    def test_map_task_counts(self):
        # 星趴·梦想号最高难度 3 个筹码任务，园林中庭 4 个
        self.assertAlmostEqual(self.picks(star=0, map="星趴·梦想号").total, 3 + 3 + 1)
        self.assertAlmostEqual(self.picks(star=0, map="园林中庭").total, 3 + 4 + 1)
        # 地图待识别：按候选地图平均
        p = self.picks(star=0, map_candidates=("星趴·梦想号", "园林中庭"))
        self.assertAlmostEqual(p.total, 3 + 3.5 + 1)

    def test_reroll_cost_uses_estimate(self):
        scorer = Scorer(self.db, State(hero="邦妮", star=1, rerolls=3))
        # m = 预计剩余三选一次数 ÷ 剩余刷新次数：1 星、没写明来源（按升星算）、没有地图资料时是 2 + （32/9 + 1）
        self.assertAlmostEqual(scorer.reroll_cost(), self.db.rules["reroll_cost"]["gain"] * harmonic((2 + PicksLeftTest.PER_GAME) / 3))
        late = Scorer(self.db, State(hero="邦妮", star=3, round=12, rerolls=5))
        self.assertLess(late.reroll_cost(), scorer.reroll_cost())


class LateFloorTest(Base):
    """3 星以后的下限（context.Context._late_floor）：轮次已知、已满 3 星时，任务和筹码商店三选一至少为
    late_rate ×（H + 0.5），在按 end_round 收窄之后取大；H 是存活条件下预计还剩几轮：C = Context.cap − 1（读到当前格时
    Context.cap 是本轮 +（进度上限 − 当前格）），C 不大于本轮时 0，否则 (min(max(late_from ×（进度上限 − 1）, 本轮), C)
    + C) ÷ 2 − 本轮。之后照旧按「进度上限 − 当前轮次」封顶。轮次读不出、0～2 星时不加。"""

    MAP = "魔法学院"  # 困难及以上进度上限 16，3 个筹码任务：一局的任务和商店三选一 4 次
    # 3 星（3 次升星）、6 个筹码：已经有过 4 次任务和商店，一局的份额用完，下限起作用
    OWNED = ("标靶", "标记喷罐", "大背包", "缓冲盾牌", "书签", "维生素药丸")

    def picks(self, **kw):
        kw = {"hero": "凛", "star": 3, "map": self.MAP, "owned": self.OWNED, **kw}
        return Scorer(self.db, State(**kw)).picks

    def floor(self, h: float) -> float:
        return self.db.rules["picks"]["late_rate"] * (h + 0.5)

    def test_parameters(self):
        cfg = self.db.rules["picks"]
        self.assertEqual((cfg["late_rate"], cfg["late_from"], cfg["end_round"]), (0.2, 0.5, 13))

    def test_without_progress(self):
        # 没读到当前格：C = 16 − 1 = 15。第 10 轮：(max(0.5 × 15, 10) + 15) ÷ 2 − 10 = 2.5；第 5 轮：最早按半个进度上限
        # 7.5 算，(7.5 + 15) ÷ 2 − 5 = 6.25
        for rnd, h in ((10, 2.5), (5, 6.25), (13, 1.0)):
            p = self.picks(round=rnd)
            with self.subTest(round=rnd):
                self.assertAlmostEqual(p.extras, self.floor(h))
                self.assertAlmostEqual(p.total, self.floor(h))
                self.assertEqual((p.levelups, p.rounds_left, p.capped), (0, 16 - rnd, False))

    def test_with_progress(self):
        # 读到当前格 12（第 10 轮）：Context.cap = 10 +（16 − 12）= 14，C = 13，H = (10 + 13) ÷ 2 − 10 = 1.5；
        # 进度比轮次快很多（第 6 轮第 14 格）：C = 7，起点被 C 截住，H = (7 + 7) ÷ 2 − 6 = 1
        p = self.picks(round=10, progress=12)
        self.assertAlmostEqual(p.total, self.floor(1.5))
        self.assertEqual(p.rounds_left, 4)
        self.assertAlmostEqual(self.picks(round=6, progress=14).total, self.floor(1.0))

    def test_at_and_past_last_round(self):
        # 正好在进度上限前最后能打的一轮（第 15 轮，C = 15）：H = 0，还有 late_rate × 0.5；到了进度上限（第 16 轮）：
        # 剩余轮数 0，封顶到 0；读到进度条走到头时同样
        self.assertAlmostEqual(self.picks(round=15).total, self.floor(0))
        p = self.picks(round=16)
        self.assertEqual((p.total, p.rounds_left, p.capped), (0.0, 0, True))
        self.assertEqual(self.picks(round=12, progress=16).total, 0.0)

    def test_round_unknown_and_lower_stars(self):
        # 轮次读不出：不加下限（同不按 end_round 收窄），份额用完就是 0
        self.assertEqual(self.picks(round=None).total, 0.0)
        # 0～2 星不加：2 星第 12 轮只剩 1 次升星（份额用完、按第 13 轮收窄）
        p = self.picks(star=2, round=12, owned=self.OWNED[:5])
        self.assertEqual((p.levelups, p.extras, p.total), (1, 0.0, 1.0))

    def test_only_raises(self):
        # 份额还没用完、收窄以后仍高于下限时照旧：3 星第 9 轮、只有 3 个筹码（3 次升星），收窄后 4 × 4/12 = 1.33 > 下限
        p = self.picks(round=9, owned=self.OWNED[:3])
        self.assertAlmostEqual(p.extras, 4 * (13 - 9) / 12)
        self.assertGreater(p.extras, self.floor((max(7.5, 9) + 15) / 2 - 9))

    def test_reroll_and_lookahead_follow(self):
        # 不再估成之后没有三选一：读到 4 次刷新时留 2 次、多出 2 次（不是 4 次全部连着用），前瞻照常算
        st = State(hero="凛", star=3, round=13, map=self.MAP, owned=self.OWNED, rerolls=4)
        adv = advise(self.db, st, ["摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"])
        self.assertAlmostEqual(adv.picks.total, self.floor(1.0))
        self.assertEqual((adv.reroll.kept, adv.reroll.spare, adv.reroll.chain), (2, 2, 2))
        self.assertGreater(Scorer(self.db, st).lookahead_times(), 0)


class EndlessPendingPicksTest(Base):
    """已拿循环往复、它给的额外那次三选一还没给（State.endless_pending）：剩余三选一次数另加 1 次（PicksLeft.endless），
    「进度上限 − 当前轮次」的封顶也加 1（封顶多加的那 1 次留给它，升星只占剩余的轮数）；到期那一轮打不到时不加。按次数分开算
    时这一次排在剩下的升星后面，按任务来源（三格全随机，等级按到期时预计的星数，不知道到期轮次时取当前星数）：前瞻、三个
    概率都这样算；3 星以后下一次三选一的主体就是它。"""

    BASE = dict(hero="阿尔", map="魔法学院", owned=("缓冲盾牌", "循环往复"))

    def scorer(self, **kw):
        return Scorer(self.db, State(**{**self.BASE, **kw}))

    def test_adds_one(self):
        for kw in (dict(star=1, round=4), dict(star=2, round=7), dict(star=3, round=11), dict(star=2)):
            plain, pending = self.scorer(**kw).picks, self.scorer(**kw, endless_pending=True).picks
            with self.subTest(**kw):
                self.assertEqual((plain.endless, pending.endless), (0, 1))
                self.assertAlmostEqual(pending.total, plain.total + 1)
                self.assertEqual((pending.levelups, pending.extras), (plain.levelups, plain.extras))
        self.assertFalse(State(hero="阿尔").endless_pending)  # 旧记录里没有这一项时按没有

    def test_cap_plus_one(self):
        # 到了进度上限（第 16 轮）：封顶也加 1，之后还有这 1 次；第 15 轮：下限 0.1 + 1
        p = self.scorer(star=3, round=16, endless_pending=True).picks
        self.assertEqual((p.total, p.rounds_left, p.capped, p.endless), (1.0, 0, True, 1))
        p = self.scorer(star=1, round=15, endless_pending=True).picks
        self.assertEqual((p.total, p.capped), (2.0, True))  # 只剩 1 轮：1 + 1

    def test_split_and_sources(self):
        m = self.BASE["map"]
        # 2 星前瞻影响 1.5 次：1 次升到 3 星的升星，半次按循环往复那一次（不按混合）
        sc = self.scorer(star=2, round=7, endless_pending=True)
        self.assertEqual(split_picks(sc, sc.lookahead_times()), (1, 0.5, 0.0))
        n = sc.picks.total
        self.assertEqual(split_picks(sc, n), (1, 1, n - 2))
        self.assertEqual(next_sources(sc, m), ((1.0, "levelup", 3, None),))
        # 3 星：下一次三选一的主体是循环往复那一次（任务来源，3 级）；影响的 1 次全按它
        sc = self.scorer(star=3, round=11, endless_pending=True)
        self.assertEqual(endless_sources(sc), ((1.0, "task", 3, 3),))
        self.assertEqual(next_sources(sc, m), endless_sources(sc))
        self.assertEqual(split_picks(sc, sc.lookahead_times()), (0, 1, 0.0))
        self.assertNotEqual(next_sources(self.scorer(star=3, round=11), m), endless_sources(sc))

    def test_capped_keeps_endless_pick(self):
        """被剩余轮次封顶时，封顶多加的那 1 次是留给循环往复那一次的（context.levelups_ahead），升星只占剩余的轮数。
        1 星第 15 轮、进度条走到头（剩余 0 轮）、那一次还没给：剩余 = 0 + 1，这 1 次是循环往复那一次（按任务来源），不是升星；
        下一次三选一的主体也是它。"""
        m = self.BASE["map"]
        sc = self.scorer(star=1, round=15, progress=16, endless_pending=True)
        self.assertEqual((sc.picks.total, sc.picks.rounds_left, sc.picks.capped), (1.0, 0, True))
        self.assertEqual(split_picks(sc, sc.picks.total), (0, 1, 0.0))
        self.assertEqual(split_picks(sc, sc.lookahead_times()), (0, 1, 0.0))
        self.assertEqual(next_sources(sc, m), endless_sources(sc))
        # 前瞻只有一类，就是循环往复那一次（_split_on 按主体算）
        name, owned = "标靶", sc.known_owned
        weight = self.db.rules["lookahead"]["weight"]
        delta = (future(sc, (*owned, name), map_name=m, endless=True)
                 - future(sc, owned, zero=name, map_name=m, endless=True))
        self.assertAlmostEqual(lookahead_on(sc, name, owned, m), weight * sc.lookahead_times() * delta)
        # 剩 1 轮、两次升星：升星只占 1 次，那 1 次照留
        sc = self.scorer(star=1, round=14, progress=15, endless_pending=True)
        self.assertEqual((sc.picks.total, sc.picks.rounds_left, sc.picks.capped), (2.0, 1, True))
        self.assertEqual(split_picks(sc, sc.picks.total), (1, 1, 0.0))
        self.assertEqual(next_sources(sc, m), ((1.0, "levelup", 2, None),))
        # 没有那一次时照旧先算升星：同是 1 星第 14 轮、进度 15（剩 1 轮），封顶成 1 次，这 1 次按升星；剩余轮数不少于剩余
        # 升星时照旧（常见情形逐位不变，剩余升星照样是整数）
        sc = self.scorer(star=1, round=14, progress=15)
        self.assertEqual((sc.picks.total, sc.picks.capped), (1.0, True))
        self.assertEqual(split_picks(sc, sc.picks.total), (1.0, 0, 0.0))
        self.assertEqual(next_sources(sc, m), ((1.0, "levelup", 2, None),))
        sc = self.scorer(star=1, round=10, progress=14, endless_pending=True)  # 剩 2 轮：2 + 1 + 1 封顶成 2 + 1
        self.assertEqual((sc.picks.total, sc.picks.rounds_left, sc.picks.capped), (3.0, 2, True))
        ups, endless, rest = split_picks(sc, sc.picks.total)
        self.assertEqual((ups, endless, rest), (2, 1, 0.0))
        self.assertIs(type(ups), int)

    def test_only_endless_left_no_class_shift(self):
        """之后的三选一只剩循环往复给的那一次时（进度条走到头、那一次还没给），前瞻说明不写职业分值那一句：那一次是任务来源，
        三格全随机，不看职业，之后也不再有升星和筹码商店。还剩 1 轮时照写。选大碗炖肉：生存职业分值 +50。"""
        upgrade = CLASS_SHIFT_REASON.format(points="生存职业分值 +50", classes="生存")
        shop = CLASS_SHIFT_SHOP_REASON.format(points="生存职业分值 +50", classes="生存")
        for star in (2, 3):
            with self.subTest(star=star):
                sc = self.scorer(star=star, round=15, progress=16, endless_pending=True)
                self.assertEqual((sc.picks.total, sc.picks.endless), (1.0, 1))
                reason = sc.lookahead_reason("大碗炖肉", sc.known_owned)
                self.assertTrue(reason)  # 前瞻照写（那一次里拿走它，同稀有度筹码出现率提高）
                self.assertNotIn("职业分值", reason)
                self.assertNotIn("更常出现生存职业", sc.lookahead_short("大碗炖肉", sc.known_owned))
        # 还剩 1 轮：之后还有升星（0～2 星）或筹码商店（3 星以后），照写
        self.assertIn(upgrade, self.scorer(star=2, round=14, progress=15, endless_pending=True)
                      .lookahead_reason("大碗炖肉", ("缓冲盾牌", "循环往复")))
        self.assertIn(shop, self.scorer(star=3, round=14, progress=15, endless_pending=True)
                      .lookahead_reason("大碗炖肉", ("缓冲盾牌", "循环往复")))

    def test_due_after_cap(self):
        """到期轮次（State.endless_due）不早于进度上限时，那一轮打不到，不多算那 1 次；和给循环往复估值时（parts._endless_parts：
        拿到以后过 delay 回合已到进度上限，额外那次记 0）同一个口径。魔法学院进度上限 16。"""
        # 第 14 轮在任务里拿到循环往复：到期第 17 轮。第 14～16 轮都不多算（第 16 轮剩余 0 轮时也一样）
        for rnd in (14, 15, 16):
            with self.subTest(round=rnd):
                p = self.scorer(star=3, round=rnd, endless_pending=True, endless_due=17).picks
                plain = self.scorer(star=3, round=rnd).picks
                self.assertEqual(p.endless, 0)
                self.assertEqual((p.total, p.capped), (plain.total, plain.capped))
        # 同一局面给候选循环往复估值时，额外那次也记 0（14 + 3 回合 = 17，不早于 16）
        sc = Scorer(self.db, State(hero="阿尔", map="魔法学院", star=3, round=14, source="task", tier=3,
                                   owned=("缓冲盾牌",)))
        part = next(p for p in sc.parts("循环往复", sc.known_owned, 14) if p.label == "循环往复")
        self.assertIn("已到进度上限", part.reason)
        # 到期正好是进度上限那一轮也打不到；早一轮的照算
        self.assertEqual(self.scorer(star=3, round=13, endless_pending=True, endless_due=16).picks.endless, 0)
        self.assertEqual(self.scorer(star=3, round=13, endless_pending=True, endless_due=15).picks.endless, 1)
        # 进度比轮次走得快时按读到的当前格：第 12 轮、当前格 14（还剩 2 轮，按轮次算到第 14 轮），到期第 14 轮也打不到
        self.assertEqual(self.scorer(star=3, round=12, progress=14, endless_pending=True, endless_due=14).picks.endless, 0)
        # 不知道到期轮次（旧记录、冻结的局面）照原来多算
        self.assertEqual(self.scorer(star=3, round=14, endless_pending=True).picks.endless, 1)
        self.assertIsNone(State(hero="阿尔").endless_due)
        # 进度上限读不出（地图待识别、进度条也没读到）时照原来多算；读到了进度条上的进度上限就按它
        loose = dict(hero="阿尔", owned=self.BASE["owned"], star=3, round=14, endless_pending=True, endless_due=17)
        self.assertEqual(Scorer(self.db, State(**loose)).picks.endless, 1)
        self.assertEqual(Scorer(self.db, State(**loose, progress_cap=16)).picks.endless, 0)

    def test_endless_tier_at_due(self):
        """0～2 星时那一次三选一的等级按到期时预计的星数（context.endless_sources，和给循环往复估值时 parts.endless_tiers 同一个
        算法）：1 星第 4 轮、到期第 6 轮 → 预计 1 + 0.25 × 2 = 1.5 星，1 级、2 级各占一半。"""
        m = self.BASE["map"]
        sc = self.scorer(star=1, round=4, endless_pending=True, endless_due=6)
        self.assertEqual(endless_sources(sc), ((0.5, "task", 1, 1), (0.5, "task", 1, 2)))
        self.assertEqual(sc.endless_tiers(2), [(1, 0.5), (2, 0.5)])
        self.assertEqual([d[0] for d in future_setup(sc, sc.known_owned, m, endless=True)[1]], [0.5, 0.5])
        # 已经到期、不知道到期轮次或当前轮次时按当前星数（至少 1 级）；3 星以后都是 3 级
        for kw in (dict(star=1, round=6, endless_due=6), dict(star=1, round=4), dict(star=1, endless_due=6)):
            with self.subTest(**kw):
                self.assertEqual(endless_sources(self.scorer(**kw, endless_pending=True)), ((1.0, "task", 1, 1),))
        self.assertEqual(endless_sources(self.scorer(star=3, round=9, endless_pending=True, endless_due=11)),
                         ((1.0, "task", 3, 3),))

    def test_lookahead_uses_endless_pick(self):
        # 2 星：前瞻 = weight ×（1 × 升星那一次的变化 + 0.5 × 循环往复那一次的变化）
        sc = self.scorer(star=2, round=7, endless_pending=True)
        m, owned, name = self.BASE["map"], sc.known_owned, "标靶"
        weight = self.db.rules["lookahead"]["weight"]
        up = sc.future((*owned, name), map_name=m) - sc.future(owned, zero=name, map_name=m)
        mid = (future(sc, (*owned, name), map_name=m, endless=True)
               - future(sc, owned, zero=name, map_name=m, endless=True))
        self.assertAlmostEqual(lookahead_on(sc, name, owned, m), weight * (1 * up + 0.5 * mid))
        self.assertEqual(future_setup(sc, owned, m, endless=True)[1][0][0], 1.0)  # 一种来源
        # 3 星：只有一类，就是下一次三选一的主体（循环往复那一次）
        sc = self.scorer(star=3, round=11, endless_pending=True)
        times = sc.lookahead_times()
        delta = sc.future((*sc.known_owned, name), map_name=m) - sc.future(sc.known_owned, zero=name, map_name=m)
        self.assertAlmostEqual(lookahead_on(sc, name, sc.known_owned, m), weight * times * delta)
        self.assertEqual(future_setup(sc, sc.known_owned, m)[1], future_setup(sc, sc.known_owned, m, endless=True)[1])

    def test_chances_count_endless_pick(self):
        # 之后选到某筹码的概率：升星 L 次 × 循环往复那 1 次（任务来源）× 混合 E 次，一个都不出现的概率相乘
        sc = self.scorer(star=2, round=7, endless_pending=True)
        m = self.BASE["map"]
        owned = sc.known_owned
        take = ["医疗箱-紧急治疗", "大碗炖肉"]
        pool = player_pool(self.db, owned, m)
        schools = owned_schools(self.db, owned)

        def miss(left, general):
            out = 1.0
            for k in take:
                out *= 1 - appear_prob(left, general, k)
            return out

        n = sc.picks.total
        ups, endless, rest = split_picks(sc, n)
        ml = miss(*slot_dists(self.db, sc.hero, pool, schools, 3, class_owned=owned))
        me = miss(*slot_dists(self.db, sc.hero, pool, schools, 2, "task", 2, class_owned=owned))
        (t, _, _, _), (s, _, _, _) = mixed_sources(sc, m)
        mm = (t * me + s * miss(*slot_dists(self.db, sc.hero, pool, schools, 2, "shop", class_owned=owned)))
        from advisor.engine.chances import take_chance
        self.assertAlmostEqual(take_chance(sc, owned, take, (m,)), 1 - ml ** ups * me ** endless * mm ** rest,
                               places=12)
        plain = self.scorer(star=2, round=7)
        self.assertGreater(take_chance(sc, owned, take, (m,)), take_chance(plain, owned, take, (m,)))

    def test_reroll_keeps_one_more(self):
        # 3 星第 11 轮、读到 3 次刷新：之后约 0.67 次 → 留 2 次、多出 1 次；循环往复那一次还没给：约 1.67 次 → 留 3 次
        offer = ["摩托头盔-一般", "夹心饼干-一般", "速度轮滑-中级"]
        plain = advise(self.db, State(**self.BASE, star=3, round=11, rerolls=3), offer).reroll
        pending = advise(self.db, State(**self.BASE, star=3, round=11, rerolls=3, endless_pending=True), offer).reroll
        self.assertEqual((plain.kept, plain.spare), (2, 1))
        self.assertEqual((pending.kept, pending.spare), (3, 0))
        self.assertGreater(pending.cost, 0)

    def test_given_is_not_task_or_shop(self):
        """额外那次给了以后（State.endless_given）：它不是任务和筹码商店的三选一，推算「已经有过几次」时扣掉。轮次待识别
        （不按第 13 轮收窄），魔法学院一局的任务和商店是 3 × task_rate + shop = 4 次。"""
        cfg = self.db.rules["picks"]
        per_game = 3 * cfg["task_rate"] + cfg["shop"]
        # 2 星、这次是升星：已有缓冲盾牌、循环往复和额外那次里选的标靶，升过 1 次星（这次升到 2 星不算）→ 已经有过 2 次；
        # 标靶是额外那次的，只有 1 次
        kw = dict(hero="阿尔", map="魔法学院", star=2, owned=("缓冲盾牌", "循环往复", "标靶"))
        self.assertAlmostEqual(self.scorer(**kw).picks.extras, per_game - 2)
        given = self.scorer(**kw, endless_given=1).picks
        self.assertAlmostEqual(given.extras, per_game - 1)
        self.assertEqual((given.endless, given.levelups), (0, 1))
        # 这一次就是额外那次（来源判成任务，候选还在界面上）：这一次不算成任务的一次
        kw = dict(hero="阿尔", map="魔法学院", star=2, owned=("缓冲盾牌", "循环往复"), source="task", tier=2)
        self.assertAlmostEqual(self.scorer(**kw).picks.extras, per_game - 1)
        self.assertAlmostEqual(self.scorer(**kw, endless_given=1).picks.extras, per_game)
        # 不会扣成负数；旧记录没有这一项时按 0
        self.assertAlmostEqual(self.scorer(**kw, endless_given=5).picks.extras, per_game)
        self.assertEqual(State(hero="阿尔").endless_given, 0)

    def test_given_round_known(self):
        """轮次已知时照旧按第 13 轮收窄：扣掉额外那次只在份额比收窄的值小时起作用。2 星第 3 轮（收窄到 4 × 10/12）。"""
        kw = dict(hero="阿尔", map="魔法学院", star=2, round=3, owned=("缓冲盾牌", "循环往复", "标靶"))
        plain, given = self.scorer(**kw).picks, self.scorer(**kw, endless_given=1).picks
        self.assertAlmostEqual(plain.extras, 2.0)
        self.assertAlmostEqual(given.extras, 3.0)
        self.assertAlmostEqual(given.total - plain.total, 1.0)
        kw["round"] = 9  # 收窄到 4 × 4/12：两边一样
        self.assertAlmostEqual(self.scorer(**kw).picks.total, self.scorer(**kw, endless_given=1).picks.total)


class DifficultyCapTest(Base):
    """进度条读不出时，进度上限按地图资料（data.MapInfo.cap_for）：画面上读到了具体难度（State.difficulty_name）取那一难度的
    一列，普通难度取普通一列，困难及以上、不知道具体难度时取疯狂一列；读到极限、地图有极限难度时取极限的进度上限，一局的
    筹码任务数也取极限的（MapInfo.tasks_for），其余难度、不知道难度时照旧（极限多出的任务不让别的难度的估计跟着变）。"""

    def picks(self, **kw):
        return Scorer(self.db, State(**{"hero": "凛", "star": 1, "round": 4, **kw})).picks

    def test_named_column(self):
        # 星趴·梦想号：普通、困难、噩梦 20，疯狂 18
        for name, cap in (("困难", 20), ("噩梦", 20), ("疯狂", 18), ("极限", 18), (None, 18)):
            with self.subTest(name=name):
                self.assertEqual(self.picks(map="星趴·梦想号", difficulty_name=name).cap, cap)
        self.assertEqual(self.picks(map="星趴·梦想号", difficulty=NORMAL).cap, 20)
        # 读到了进度条上的进度上限时照旧用它
        self.assertEqual(self.picks(map="星趴·梦想号", difficulty_name="困难", progress_cap=17).cap, 17)
        # 地图没有这个难度的资料（没有极限）：照不知道具体难度，取疯狂一列
        self.assertEqual(self.picks(map="魔法学院", difficulty_name="极限").cap, 16)
        self.assertEqual(self.picks(map="魔法学院", difficulty_name="困难").cap, 17)

    def test_extreme_tasks_only_when_extreme(self):
        # 御魂庆典：疯狂 3 个筹码任务、进度上限 19，极限 4 个、17；水乡古镇极限 5 个、16（轮次待识别：不按轮次收窄，
        # 任务和商店就是一局的份额）
        cfg = self.db.rules["picks"]
        per_game = cfg["task_rate"] * 3 + cfg["shop"]
        for name in (None, "困难", "噩梦", "疯狂"):
            with self.subTest(name=name):
                self.assertAlmostEqual(self.picks(map="御魂庆典", difficulty_name=name, round=None).extras, per_game)
        p = self.picks(map="御魂庆典", difficulty_name="极限", round=None)
        self.assertEqual(p.cap, 17)
        self.assertAlmostEqual(p.extras, cfg["task_rate"] * 4 + cfg["shop"])
        p = self.picks(map="水乡古镇", difficulty_name="极限", round=None)
        self.assertEqual(p.cap, 16)
        self.assertAlmostEqual(p.extras, cfg["task_rate"] * 5 + cfg["shop"])
        # 任务和筹码商店的占比跟着用同一个任务数
        sc = Scorer(self.db, State(hero="凛", star=3, map="御魂庆典", difficulty_name="极限"))
        task = cfg["task_rate"] * 4
        self.assertAlmostEqual(mixed_sources(sc, "御魂庆典")[0][0], task / (task + cfg["shop"]))


class MapAverageTest(Base):
    """地图待识别时按候选地图平均。"""

    def test_note_and_candidates(self):
        adv = self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"])
        self.assertIn("地图：待识别，按候选地图平均计算", adv.notes)
        self.assertEqual(set(adv.maps), set(self.db.active_maps()))
        # 已结束的限时地图不在默认候选里
        self.assertNotIn("海选赛运动场", adv.maps)
        adv = self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"], round=5)
        self.assertIn("地图：待识别，按候选地图平均计算（进度上限取常见值 16）", adv.notes)
        adv = self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"], map="水乡古镇")
        self.assertFalse(any(n.startswith("地图：") for n in adv.notes))
        self.assertEqual(adv.maps, ("水乡古镇",))

    def test_lookahead_is_mean_over_maps(self):
        cands = ("水乡古镇", "魔法学院")
        st = State(hero="凛", star=1, map_candidates=cands)
        avg = Scorer(self.db, st).lookahead("手持风扇-大", ())
        each = [Scorer(self.db, State(hero="凛", star=1, map=m)).lookahead("手持风扇-大", ()) for m in cands]
        self.assertAlmostEqual(avg, sum(each) / 2)

    def test_reroll_is_mean_over_maps(self):
        offer = ["会员推荐信", "小猪存钱罐", "书签"]
        cands = ("水乡古镇", "魔法学院")
        both = self.run_case("邦妮", offer, star=1, map_candidates=cands).reroll
        each = [self.run_case("邦妮", offer, star=1, map=m).reroll for m in cands]
        self.assertAlmostEqual(both.expected, sum(r.expected for r in each) / 2)

    def test_map_chip_narrows_candidates(self):
        # 候选里有地图专属筹码时，只按包含它的地图计算
        adv = self.run_case("梅加斯", ["大铜锣", "标靶", "书签"])
        self.assertEqual(adv.maps, ("水乡古镇",))

    def test_retired_map_chip_uses_fallback_maps(self):
        # 专属筹码只在已结束的限时地图（运动场系列）出现：从全部地图里取包含它的，不按现役地图平均
        chip = self.db.chips["运动手环"]
        self.assertTrue(chip.maps and not set(chip.maps) & set(self.db.active_maps()))
        adv = self.run_case("美咲", ["运动手环", "大背包", "标靶"], star=1)
        self.assertEqual(set(adv.maps), set(chip.maps))
        self.assertEqual(adv.picks.cap, 15)
        # 已经按一个专属筹码缩小过：对不上的已有筹码（记错的）不再改换候选
        adv = self.run_case("梅加斯", ["大铜锣", "标靶", "书签"], owned=("运动手环",))
        self.assertEqual(adv.maps, ("水乡古镇",))


class EndlessCycleTest(Base):
    """循环往复按刷新模型估值。"""

    def part(self, **kw):
        kw.setdefault("hero", "邦妮")
        st = State(**kw)
        parts = Scorer(self.db, st).parts("循环往复", st.owned, st.round)
        return next(p for p in parts if p.label == "循环往复")

    def test_no_fixed_score_left(self):
        rules = [r for r in self.db.general_rules() if "循环往复" in r.chips]
        self.assertEqual(rules, [])

    def test_value_falls_late(self):
        early = self.part(star=1, round=2, rerolls=3)
        late = self.part(star=3, round=11, rerolls=5)
        self.assertGreater(early.score, late.score)
        # 升星时拿到：当回合计 1 回合、再等 2 回合（rules.toml [endless_cycle] delay，攻略视频）
        self.assertIn("2 回合后获得 1 次筹码三选一", early.reason)
        self.assertIn("3 回合后获得 1 次筹码三选一", self.part(star=1, round=2, rerolls=3, source="task").reason)

    def test_extra_pick_zero_at_progress_cap(self):
        # 升星时拿到：第 14 轮 + 2 = 16 = 进度上限，额外那次三选一记 0；任务里拿到：第 13 轮 + 3 = 16
        p = self.part(star=2, round=14, rerolls=4)
        self.assertEqual(p.reason, "增加 3 次刷新；2 回合后已到进度上限，无法获得额外的筹码三选一")
        self.assertEqual(p.short, "3 次刷新")
        before = self.part(star=2, round=13, rerolls=4)
        self.assertEqual(before.reason, "增加 3 次刷新；2 回合后获得 1 次筹码三选一")
        self.assertEqual(before.short, "3 次刷新和 1 次三选一")
        self.assertGreater(before.score, p.score)
        task = self.part(star=2, round=13, rerolls=4, source="task")
        self.assertEqual(task.reason, "增加 3 次刷新；3 回合后已到进度上限，无法获得额外的筹码三选一")
        self.assertEqual(task.short, "3 次刷新")

    def test_reason_has_no_score(self):
        # 悬浮窗不显示分数：整句只写增加的刷新次数和额外的三选一，不写「价值」「期望最高分」和分值。
        # 分值随剩余刷新次数变化（刷新越多越不值钱），整句不变
        rules = self.db.rules
        allowed = {str(rules["reroll"]["endless_cycle"]), "1"} | {str(d) for d in rules["endless_cycle"]["delay"].values()}
        cases = (
            dict(star=1, round=2), dict(star=3, round=11), dict(star=2, round=13), dict(star=1),
            dict(star=0, round=4, map="水乡古镇"), dict(star=2, round=6, difficulty=NORMAL),
        )
        for kw in cases:
            few, many = self.part(rerolls=1, **kw), self.part(rerolls=5, **kw)
            with self.subTest(**kw):
                self.assertGreater(few.score, many.score + 0.5)
                self.assertEqual(few.reason, many.reason)
                self.assertEqual(few.short, many.short)
                for p in (few, many):
                    for word in ("价值", "期望最高分", "分"):
                        self.assertNotIn(word, p.reason)
                        self.assertNotIn(word, p.short)
                    self.assertLessEqual(set(re.findall(r"\d+(?:\.\d+)?", p.reason)), allowed)
                    self.assertLessEqual(set(re.findall(r"\d+(?:\.\d+)?", p.short)), allowed)

    def test_more_rerolls_in_hand_worth_less(self):
        self.assertGreater(self.part(star=1, rerolls=1).score, self.part(star=1, rerolls=5).score)

    def test_weight_from_rules(self):
        cfg = self.db.rules["endless_cycle"]
        # 等待的回合数按来源分：升星、筹码商店 2 回合，任务 3 回合（取自攻略视频）
        self.assertEqual((cfg["chip"], cfg["delay"]), ("循环往复", {"levelup": 2, "shop": 2, "task": 3}))
        self.assertLess(cfg["weight"], 0.31)  # 保持锁定的阿尔基准（见 rules.toml 的说明）

    def test_al_benchmark_with_rerolls_used(self):
        # 阿尔基准在本局已用掉刷新时也成立（循环往复增加的刷新价值随剩余次数减少而升高）。
        # 实拍条件（园林中庭、概率 60/37/3、最左格蓝色）和地图待识别、三种来源都查。
        offer = ["缓冲盾牌", "夹心饼干-一般", "循环往复"]
        shot = dict(map="园林中庭", probabilities=(60, 37, 3), left_rarity="蓝")
        for star in (0, 1):
            for rerolls in (0, 1, 2, 3):
                for extra in (shot, dict(source="levelup"), dict(source="task"), dict(source="shop"),
                              dict(map="决赛大赛场")):
                    with self.subTest(star=star, rerolls=rerolls, **extra):
                        adv = self.run_case("阿尔", offer, star=star, round=1, rerolls=rerolls, **extra)
                        self.assertEqual(adv.best, "缓冲盾牌")


class IncomeTest(Base):
    """收入类筹码按剩余兑现次数折算。"""

    def income(self, hero, chip, **kw):
        st = State(hero=hero, **kw)
        return next((p for p in Scorer(self.db, st).parts(chip, (), st.round) if p.label == "收入折算"), None)

    def test_reward_rounds(self):
        self.assertEqual(reward_rounds_left(1, 16), 5)  # 第 3、6、9、12、15 轮
        self.assertEqual(reward_rounds_left(10, 16), 2)  # 第 12、15 轮
        self.assertEqual(reward_rounds_left(15, 16), 0)

    def test_bank_card_discounted_late(self):
        self.assertIsNone(self.income("恋", "银行卡-余额多", round=1))
        self.assertIsNone(self.income("恋", "银行卡-余额多"))  # 轮次待识别：不折算
        mid = self.income("恋", "银行卡-余额多", round=7)
        late = self.income("恋", "银行卡-余额多", round=13)
        self.assertLess(mid.score, 0)
        self.assertLess(late.score, mid.score)
        self.assertIn("星光收入按剩余兑现次数折算", late.reason)
        self.assertIn("到进度上限（16）前还有 1 次轮次奖励", late.reason)

    def test_three_stars_non_economy(self):
        # 满 3 星：非经济型角色（邦妮）的星币收入再降分；经济型角色（芬妮）不降
        bonnie = self.income("邦妮", "小猪存钱罐", star=3)
        self.assertIsNotNone(bonnie)
        self.assertIn("已达到 3 星，星币不再用于升星", bonnie.reason)
        self.assertIsNone(self.income("芬妮", "小猪存钱罐", star=3))

    def test_starlight_growth_hero_discounted_less(self):
        # 阿尔的星光层数直接换成属性：同样的轮次，星光收入的折算比例减半
        al = self.income("阿尔", "银行卡-余额多", round=10)
        ren = self.income("恋", "银行卡-余额多", round=10)
        al_base = sum(p.score for p in Scorer(self.db, State(hero="阿尔", round=10)).parts("银行卡-余额多", (), 10)
                      if p.score > 0 and p.label != "成长分")
        ren_base = sum(p.score for p in Scorer(self.db, State(hero="恋", round=10)).parts("银行卡-余额多", (), 10)
                       if p.score > 0 and p.label != "成长分")
        self.assertAlmostEqual(al.score / al_base, ren.score / ren_base / 2)

    def test_non_income_chip_untouched(self):
        self.assertIsNone(self.income("邦妮", "标靶", round=13, star=3))


class ReasonWordingTest(Base):
    """理由不出现特性名，引用书面化的特性说明；分项名称可以直接显示。"""

    def test_trait_descriptions_written(self):
        self.assertEqual(len(self.db.traits), 26)  # 含辅助角色的「辅助定位」
        for name, desc in self.db.traits.items():
            with self.subTest(trait=name):
                self.assertTrue(desc)
                self.assertNotIn(f"「{name}」", desc)
                self.assertNotRegex(desc, "挨打|回血|身板|弄到|不打|满血的")

    def test_no_trait_names_in_reasons(self):
        names = [n for n in self.db.traits]
        for hero in self.db.heroes:
            scorer = Scorer(self.db, State(hero=hero, star=1, map=None))
            for chip in self.db.chips.values():
                if chip.granted:
                    continue
                for p in scorer.parts(chip.name, (), None, chip.maps[0] if chip.maps else None):
                    with self.subTest(hero=hero, chip=chip.name, label=p.label):
                        self.assertIn(p.label, ALLOWED_LABELS)
                        self.assertNotIn("特性「", p.reason)
                        for n in names:
                            self.assertNotIn(f"「{n}」", p.reason)
                        if p.label == "其他特性":
                            self.assertTrue(p.reason.startswith("另符合："))

    def test_other_traits_cite_descriptions(self):
        scorer = Scorer(self.db, State(hero="邦妮", star=1))
        part = next(p for p in scorer.parts("鹰眼瞄具", (), None) if p.label == "其他特性")
        descs = [self.db.traits[t] for t in part.origin.split("、") if t in self.db.traits]
        self.assertTrue(descs)
        for d in descs:
            self.assertIn(d, part.reason)

    def test_zero_score_part_has_no_number(self):
        adv = self.run_case("凛", ["书签", "大背包", "标靶"])
        book = self.by_name(adv)["书签"]
        text = [p.text() for p in book.parts if p.label == "职业倾向"][0]
        self.assertNotIn("（+0）", text)
        self.assertTrue(text.endswith("暂不加分"), text)
        self.assertNotIn("不计分", text)


class TendencyTest(Base):
    """职业倾向：状态行写「职业：无（倾向：卡牌 80）」，标签相符的筹码加一条不加分的理由。"""

    def test_tendency(self):
        self.assertEqual(hero_tendency(self.db, "凛"), ("卡牌", 80))
        self.assertEqual(hero_tendency(self.db, "照"), ("攻击", 80))
        self.assertEqual(hero_tendency(self.db, "帕露南"), ("卡牌、辅助", 60))
        self.assertEqual(hero_tendency(self.db, "凛", ("标记喷罐",)), ("卡牌", 130))

    def test_zero_score_reason(self):
        scorer = Scorer(self.db, State(hero="凛"))
        parts = scorer.parts("书签", (), None)
        part = next(p for p in parts if p.label == "职业倾向")
        self.assertEqual(part.score, 0)
        self.assertEqual(part.reason, "筹码标签「卡牌」与角色的职业倾向（卡牌 80）相符；职业分值未达到 100，暂不加分")
        self.assertEqual(part.short, "职业倾向：卡牌（暂不加分）")
        # 总分不变：去掉这一项和不去掉一样
        self.assertEqual(scorer.score("书签", (), None), sum(p.score for p in parts if p.label != "职业倾向"))
        # 攻击标签与倾向不符：没有这一条
        self.assertFalse(any(p.label == "职业倾向" for p in scorer.parts("拳击手套-初级", (), None)))
        # 达到门槛（已有标记喷罐，卡牌 130）后是标签分，不再写倾向
        owned = ("标记喷罐",)
        parts = Scorer(self.db, State(hero="凛", owned=owned)).parts("书签", owned, None)
        self.assertTrue(any(p.label == "标签分" for p in parts))
        self.assertFalse(any(p.label == "职业倾向" for p in parts))
        # 已属于某个职业的角色（邦妮：攻击 100）不写倾向
        self.assertFalse(any(p.label == "职业倾向" for p in Scorer(self.db, State(hero="邦妮")).parts("书签", (), None)))


class PlayerSourceTest(PlayerBase):
    """玩家思路的来源（测试用的默认推荐档位）；同一角色的几段按先后合并（合并用测试里造的数据测）。"""

    def test_sources(self):
        # 「攻略」来源的思路：凛，以及芬妮（必拿：银行卡-用不完、紫色飞星、金色飞星）；
        # 按攻略视频整理的 8 名角色同属「攻略」来源（攻略视频不单设来源）
        user = ("凛", "芬妮", "帕露南", "小町", "赛克斯", "橘雪莉", "远野汉娜", "姬梦枫", "真梦梓", "照")
        for hero in user:
            ps = self.db.player_synergy[hero]
            self.assertEqual(ps.sources, (SOURCE_GUIDE,), hero)
            self.assertTrue(all(s == SOURCE_GUIDE for s in ps.tier_source.values()), hero)
        # 小町一段只列了攻略视频实战里抢着要、一路在找的五个筹码，推荐流派沿用角色自动层的「标记」
        ps = self.db.player_synergy["小町"]
        self.assertEqual(ps.tiers, {"大背包": "first", "额外电池": "first", "电流核心": "first", "探天卫星": "first",
                                    "魔法箭袋": "first"})
        self.assertEqual(Scorer(self.db, State(hero="小町")).pref_schools, ("标记",))

    def test_merge_in_order(self):
        warnings = []
        first = {
            "hero": "X", "source": "攻略", "schools": ["治愈"], "first": ["A"], "second": ["B"],
            "rule": [{"chips": ["B", "C"], "score": 50, "reason": "第一段的规则"}],
        }
        second = {"hero": "X", "avoid": ["B"], "rule": [{"chips": ["D"], "score": -10, "reason": "第二段的规则"}]}
        ps = _merge_players("X", [first, second], warnings.append)
        # 同一筹码的档位以后写的一段为准；没写 source 的一段按「攻略」处理
        self.assertEqual(ps.tiers, {"A": "first", "B": "avoid"})
        self.assertEqual(ps.tier_source, {"A": SOURCE_GUIDE, "B": SOURCE_GUIDE})
        self.assertEqual(ps.sources, (SOURCE_GUIDE,))
        self.assertEqual(ps.schools, ("治愈",))  # 第二段没写推荐流派：取第一段的
        # 规则按先后排，写在前面的先匹配
        self.assertEqual([(set(r.chips), r.source) for r in ps.rules], [({"B", "C"}, SOURCE_GUIDE), ({"D"}, SOURCE_GUIDE)])
        self.assertEqual(warnings, [])
        # 原文（note）依次用「；」连接，没写的一段不占位
        self.assertEqual(ps.note, "")
        noted = _merge_players("X", [{**first, "note": "第一段的原文"}, second], warnings.append)
        self.assertEqual(noted.note, "第一段的原文")
        noted = _merge_players("X", [{**first, "note": "第一段的原文"}, {**second, "note": "第二段的原文"}], warnings.append)
        self.assertEqual(noted.note, "第一段的原文；第二段的原文")
        self.assertEqual(warnings, [])

    def test_unknown_source_warns(self):
        # source 写了「攻略」以外的值：记警告，按「攻略」处理，档位和规则照常生效
        warnings = []
        ps = _merge_players("X", [{"hero": "X", "source": "其他", "first": ["A"],
                                   "rule": [{"chips": ["B"], "score": 40, "reason": "规则"}]}], warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("「其他」", warnings[0])
        self.assertEqual((ps.tiers, ps.tier_source, ps.sources), ({"A": "first"}, {"A": SOURCE_GUIDE}, (SOURCE_GUIDE,)))
        self.assertEqual([r.source for r in ps.rules], [SOURCE_GUIDE])


class DegradedTest(Base):
    """资料库未收录的内容降级运行。"""

    def test_match_chip(self):
        db = self.db
        self.assertEqual(db.match_chip("缓冲盾牌"), ("缓冲盾牌", 1.0, False))
        self.assertEqual(db.match_chip(""), (None, 0.0, False))
        name, score, unrec = db.match_chip("完全不同的东西")
        self.assertEqual((name, unrec), (None, True))
        self.assertLess(score, 0.5)
        # 错一个字（相似度 0.75）：给的稀有度和资料库一致时按它算，不一致时判为未收录
        name, score, unrec = db.match_chip("缓仲盾牌", "蓝")
        self.assertEqual((name, unrec), ("缓冲盾牌", False))
        self.assertTrue(0.5 <= score < 0.8)
        self.assertEqual(db.match_chip("缓仲盾牌", "金")[0::2], (None, True))
        self.assertEqual(db.match_chip("缓仲盾牌")[0], "缓冲盾牌")  # 没有稀有度信息：按它算
        # 相似度 ≥ 0.8：稀有度不一致也按它算（由识别模块另行提示）
        self.assertEqual(db.match_chip("拳击手套-高级", "蓝")[0], "拳击手套-高级")

    def test_match_chip_series_by_rarity(self):
        # 同系列筹码名称没读全：相似度并列或只差一点，按图标边框颜色挑稀有度相同的那一个
        db = self.db
        for text in ("夹心饼干", "医疗箱", "美工刀", "拳击手套"):
            series = [n for n in db.chips if n.startswith(text + "-")]
            self.assertGreaterEqual(len(series), 2, text)
            for rarity in ("蓝", "紫", "金"):
                fit = [n for n in series if db.chips[n].rarity == rarity]
                name, score, unrec = db.match_chip(text, rarity)
                with self.subTest(text=text, rarity=rarity):
                    if fit:
                        self.assertEqual((name, unrec), (fit[0], False))
                        self.assertGreaterEqual(score, 0.5)
                    else:
                        # 同系列里没有这个稀有度：不配成别的系列的筹码
                        self.assertFalse(name and not name.startswith(text))
        self.assertEqual(db.match_chip("夹心饼干", "蓝")[0], "夹心饼干-一般")
        self.assertEqual(db.match_chip("医疗箱", "金")[0], "医疗箱-完备治疗")
        self.assertEqual(db.match_chip("美工刀", "紫")[0], "美工刀-初级")
        self.assertEqual(db.match_chip("拳击手套", "紫")[0], "拳击手套-中级")
        self.assertEqual(db.match_chip("缓冲盾牌")[0], "缓冲盾牌")

    def test_unrecorded_slots_fix_series(self):
        from advisor.vision.chip_select import _unrecorded_slots
        names, scores = ["美工刀-锋利"], [0.667]
        self.assertEqual(_unrecorded_slots(self.db, names, ["美工刀"], scores, ["紫"]), [False])
        self.assertEqual(names, ["美工刀-初级"])
        # 真正没有收录的文字照常判为未收录
        names, scores = [None], [0.0]
        self.assertEqual(_unrecorded_slots(self.db, names, ["星辰回响之冠"], scores, ["金"]), [True])
        self.assertEqual(names, ["星辰回响之冠"])

    def test_unrecorded_chip_in_offer(self):
        adv = advise(self.db, State(hero="邦妮", star=1, rerolls=3), ["新筹码甲", "标靶", "书签"], rarity=["紫", "蓝", "紫"])
        new = adv.evals[0]
        self.assertTrue(new.unrecorded)
        self.assertEqual(new.chip.rarity, "紫")
        self.assertEqual(new.lookahead, 0)
        self.assertEqual([p.label for p in new.parts], ["未收录"])
        # 整句、短句、附注都不写「中性分」「平均分」和分值（悬浮窗不显示分数）
        self.assertEqual(new.parts[0].reason, "资料库未收录：按紫色筹码的平均水平计算")
        self.assertEqual(new.parts[0].short, "未收录：按平均水平")
        self.assertEqual(adv.unrecorded, [0])
        self.assertIn("第 1 个候选筹码「新筹码甲」资料库未收录：按平均水平计算，不计前瞻", adv.notes)
        # 不知道稀有度时按筹码池里全部筹码的平均水平
        anon = advise(self.db, State(hero="邦妮", star=1, rerolls=3), ["新筹码甲", "标靶", "书签"]).evals[0]
        self.assertEqual(anon.parts[0].reason, "资料库未收录：按筹码池里各筹码的平均水平计算")
        self.assertEqual(anon.parts[0].short, "未收录：按平均水平")
        # 中性分（计算用的值）= 同稀有度筹码的平均分
        scorer = Scorer(self.db, adv.state)
        self.assertAlmostEqual(new.total, scorer.neutral_score("紫", (), None))
        self.assertAlmostEqual(sum(e.percent for e in adv.evals) + adv.reroll.percent, 1)

    def test_unrecorded_owned_ignored(self):
        adv = self.run_case("邦妮", ["标靶", "书签", "缓冲盾牌"], owned=("某个新筹码",))
        self.assertEqual(adv.best, "标靶")

    def test_unrecorded_hero(self):
        adv = advise(self.db, State(hero="新角色", rerolls=2), ["标靶", "缓冲盾牌", "书签"])
        self.assertIn("角色「新角色」资料库未收录：只按游戏内置推荐和通用规则计算", adv.notes)
        shield = self.by_name(adv)["缓冲盾牌"]
        labels = {p.label for p in shield.parts}
        self.assertIn("特性", labels)  # 通用规则：缓冲盾牌 +40
        self.assertFalse(labels & {"玩家思路", "角色专属规则", "职业倾向"})

    def test_hero_missing_from_synergy(self):
        syn = dict(self.db.hero_synergy)
        del syn["邦妮"]
        db = dataclasses.replace(self.db, hero_synergy=syn)
        adv = advise(db, State(hero="邦妮", rerolls=2), ["标靶", "缓冲盾牌", "书签"])
        self.assertIn("适配表缺少角色「邦妮」：只按游戏内置推荐和通用规则计算", adv.notes)
        self.assertTrue(all(p.origin in ("", GENERAL_TRAIT) for e in adv.evals for p in e.parts if p.label == "特性"))


class ComboTest(Base):
    """组合规则的引擎机制。内容由适配表填写；这里用临时规则测试。"""

    def db_with(self, *combos):
        return dataclasses.replace(self.db, combos=tuple(combos))

    def test_combo_applies_when_owned(self):
        combo = Combo(
            name="美工刀与治愈来源", owned=frozenset({"医疗箱-完备治疗", "医疗箱-紧急治疗"}),
            chips=frozenset({"美工刀-初级"}), score=20, reason="已持有「{owned}」，美工刀的生命值为满条件更易满足",
        )
        db = self.db_with(combo)
        none = Scorer(db, State(hero="邦妮")).parts("美工刀-初级", (), None)
        # 没有治愈来源时另有一项「组合」是美工刀的治愈来源折算（rules.toml [heal_knife] ③），不是这条组合规则
        self.assertFalse(any(p.label == "组合" and p.origin != HEAL_DISCOUNT_ORIGIN for p in none))
        owned = ("医疗箱-紧急治疗",)
        part = next(p for p in Scorer(db, State(hero="邦妮", owned=owned)).parts("美工刀-初级", owned, None) if p.label == "组合")
        self.assertEqual(part.score, 20)
        self.assertEqual(part.reason, "已持有「医疗箱-紧急治疗」，美工刀的生命值为满条件更易满足")
        self.assertEqual(part.origin, "美工刀与治愈来源")

    def test_combo_filters(self):
        base = dict(name="t", owned=frozenset({"标靶"}), chips=frozenset({"普通瞄具"}), score=10, reason="r")
        owned = ("标靶",)

        def has(db, hero):
            return any(p.label == "组合" for p in Scorer(db, State(hero=hero, owned=owned)).parts("普通瞄具", owned, None))

        self.assertTrue(has(self.db_with(Combo(**base, heroes=frozenset({"邦妮"}))), "邦妮"))
        self.assertFalse(has(self.db_with(Combo(**base, heroes=frozenset({"邦妮"}))), "凛"))
        self.assertFalse(has(self.db_with(Combo(**base, exclude=frozenset({"邦妮"}))), "邦妮"))
        self.assertTrue(has(self.db_with(Combo(**base, traits=frozenset({"物理输出"}))), "邦妮"))
        self.assertFalse(has(self.db_with(Combo(**base, traits=frozenset({"物理输出"}))), "梅加斯"))
        self.assertFalse(has(self.db_with(Combo(**base, exclude_traits=frozenset({"物理输出"}))), "邦妮"))
        self.assertTrue(has(self.db_with(Combo(**base, exclude_traits=frozenset({"物理输出"}))), "梅加斯"))

    def test_player_conditional_rule_wins(self):
        # 姬梦枫的玩家思路（测试用的默认推荐档位）：已持有治愈来源时美工刀 +80（带 needs_owned 的规则），组合规则不再叠加
        combo = Combo(name="t", owned=frozenset({"医疗箱-紧急治疗"}), chips=frozenset({"美工刀-初级"}), score=20, reason="r")
        db = with_player_layer(self.db_with(combo))
        owned = ("医疗箱-紧急治疗",)
        parts = Scorer(db, State(hero="姬梦枫", owned=owned)).parts("美工刀-初级", owned, None)
        self.assertIn(80, [p.score for p in parts if p.label == "玩家思路"])
        self.assertFalse(any(p.label == "组合" for p in parts))


class TeammateTest(Base):
    """队友调分的引擎机制。内容由适配表填写；这里用临时规则测试。"""

    def parts(self, rules, teammates, chip="友情徽章", hero="姬梦朝"):
        db = dataclasses.replace(self.db, teammate_rules=tuple(rules))
        st = State(hero=hero, teammates=teammates)
        return [p for p in Scorer(db, st).parts(chip, (), None) if p.label == "队友"]

    def test_counts_teammates(self):
        bots = TeammateRule(chips=frozenset({"友情徽章"}), who="bot", per=-10, reason="{n} 名队友为电脑")
        mates = [{"hero": "邦妮", "bot": True}, {"hero": None, "bot": True}, Teammate("凛", False)]
        part = self.parts([bots], mates)[0]
        self.assertEqual((part.score, part.reason), (-20, "2 名队友为电脑"))
        self.assertEqual(self.parts([bots], []), [])  # 不知道队友：不调分
        self.assertEqual(self.parts([bots], [Teammate("凛", False)]), [])

    def test_trait_filter_and_cap(self):
        healers = TeammateRule(chips=frozenset({"大碗炖肉"}), who="human", per=-15, reason="{n} 名队友可为队友恢复生命",
                               traits=frozenset({"治疗友方"}), cap=20)
        mates = [Teammate("潘大猛"), Teammate("吉尔"), Teammate("邦妮"), ]
        part = self.parts([healers], mates, chip="大碗炖肉")[0]
        self.assertEqual(part.score, -20)  # 2 × -15 = -30，上限 20
        self.assertEqual(part.reason, "2 名队友可为队友恢复生命")
        need_two = dataclasses.replace(healers, min_count=3)
        self.assertEqual(self.parts([need_two], mates, chip="大碗炖肉"), [])

    def test_self_filters_and_names(self):
        link = TeammateRule(chips=frozenset({"额外电池"}), who="any", per=10, reason="队友「{mates}」：{n} 名",
                            heroes=frozenset({"姬梦朝", "吉尔"}), self_heroes=frozenset({"姬梦枫"}))
        mates = [Teammate("吉尔"), Teammate("姬梦朝"), Teammate("邦妮")]
        part = self.parts([link], mates, chip="额外电池", hero="姬梦枫")[0]
        self.assertEqual((part.score, part.reason), (20, "队友「吉尔」「姬梦朝」：2 名"))
        self.assertEqual(self.parts([link], mates, chip="额外电池", hero="墨影"), [])
        by_trait = dataclasses.replace(link, self_heroes=frozenset(), self_traits=frozenset({"物理输出"}))
        self.assertEqual(len(self.parts([by_trait], mates, chip="额外电池", hero="墨影")), 1)
        self.assertEqual(self.parts([by_trait], mates, chip="额外电池", hero="姬梦朝"), [])

    def test_short_placeholders(self):
        # 规则的 short 可写 {mates}、{n}（因队友加的分，短句写明是哪几名队友，写法「配合队友：X」）
        rule = TeammateRule(chips=frozenset({"额外电池"}), who="any", per=5, reason="队友「{mates}」……",
                            short="配合队友：{mates}")
        two = self.parts([rule], [Teammate("吉尔"), Teammate("姬梦朝")], chip="额外电池", hero="墨影")[0]
        self.assertEqual(two.short, "配合队友：吉尔、姬梦朝")
        self.assertEqual(two.reason, "队友「吉尔」「姬梦朝」……")
        three = self.parts([rule], [Teammate("吉尔"), Teammate("姬梦朝"), Teammate("凛")], chip="额外电池", hero="墨影")[0]
        self.assertEqual(three.short, "配合队友：吉尔等 3 名")
        unknown = self.parts([rule], [Teammate(None), Teammate(None)], chip="额外电池", hero="墨影")[0]
        self.assertEqual(unknown.short, "配合队友：2 名")
        count = dataclasses.replace(rule, short="{n} 名队友")
        self.assertEqual(self.parts([count], [Teammate("吉尔"), Teammate("凛")], chip="额外电池", hero="墨影")[0].short,
                         "2 名队友")
        # 没写 short 时同样写「配合队友：……」，只按电脑队友计的后接「（电脑）」
        plain = dataclasses.replace(rule, short="")
        self.assertEqual(self.parts([plain], [Teammate("吉尔")], chip="额外电池", hero="墨影")[0].short, "配合队友：吉尔")
        bots = dataclasses.replace(rule, short="", who="bot")
        self.assertEqual(self.parts([bots], [Teammate("吉尔", True)], chip="额外电池", hero="墨影")[0].short,
                         "配合队友：吉尔（电脑）")


BANNED_WORDING = re.compile(
    "挨打|上标记|打标记|回血|补血|收人头|打怪|谁拿|人人|用得上|想要的|身板|容错|本来就|好歹|反而|才稳"
    "|金币|星光币|星光系|升星点|紫拳击|攒|(?<!必)拿"
)


class ComboContentTest(Base):
    """适配表里的组合规则：美工刀与治愈来源、半血与满血冲突、标记来源、充能来源四组；
    另有飞星叠加（见 SupportFlyingStarTest）和转账筹码叠加。"""

    def combo_parts(self, hero, chip, owned, db=None, **kw):
        """组合规则给的分项（不含美工刀的治愈来源折算：分项名也是「组合」，见 HealDiscountTest）。db 不给时用 self.db。"""
        owned = tuple(owned)
        st = State(hero=hero, owned=owned, **kw)
        return [p for p in Scorer(db or self.db, st).parts(chip, owned, None)
                if p.label == "组合" and p.origin != HEAL_DISCOUNT_ORIGIN]

    def test_four_groups_recorded(self):
        names = {c.name for c in self.db.combos}
        for name in ("美工刀与每回合治愈", "美工刀与条件治愈", "半血筹码与美工刀冲突", "美工刀与半血筹码冲突",
                     "魔法箭袋与标记来源", "忍术飞镖与标记来源", "鹰眼瞄具与标记来源",
                     "永动机与充能消耗", "充能来源与充能消耗", "转账筹码叠加"):
            self.assertIn(name, names)

    def test_combo_reasons_wording(self):
        # 理由以「已持有「…」：」开头，写机制和数值
        for combo in self.db.combos:
            with self.subTest(combo=combo.name):
                self.assertTrue(combo.reason.startswith("已持有「"), combo.reason)
                self.assertIsNone(BANNED_WORDING.search(combo.reason))
                self.assertTrue(combo.owned and combo.chips)
                # 每个筹码只能获得一次，已持有的不会再成为候选：owned 与 chips 不相交；
                # 互相加分的一组（转账筹码叠加）owned 与 chips 相同，候选筹码自身不会命中
                self.assertTrue(not (combo.owned & combo.chips) or combo.owned == combo.chips)
                self.assertTrue(all(c in self.db.chips for c in combo.owned | combo.chips))

    def test_knife_with_heal_sources(self):
        none = self.combo_parts("美咲", "美工刀-锋利", ())
        self.assertEqual(none, [])
        one = self.combo_parts("美咲", "美工刀-锋利", ["医疗箱-紧急治疗"])
        self.assertEqual([p.score for p in one], [35])
        self.assertTrue(one[0].reason.startswith("已持有「医疗箱-紧急治疗」："))
        # 两类治愈来源都持有时两条都加；每个筹码名各加一对「」
        both = self.combo_parts("美咲", "美工刀-锋利", ["大碗炖肉", "医疗箱-紧急治疗", "维生素药丸"])
        self.assertEqual(sorted(p.score for p in both), [15, 35])
        self.assertTrue(any(p.reason.startswith("已持有「医疗箱-紧急治疗」「大碗炖肉」：") for p in both))
        # 反过来：已持有美工刀时，治愈来源加分。物理输出角色按治愈层数缩放（完备治疗计 6 层 × 7.5 = 45，见 HealKnifeTest），
        # 其余角色按组合规则的原分值
        self.assertEqual([p.score for p in self.combo_parts("美咲", "医疗箱-完备治疗", ["美工刀-初级"])], [45])
        self.assertEqual([p.score for p in self.combo_parts("米米", "医疗箱-完备治疗", ["美工刀-初级"])], [25])
        # 有玩家思路的角色照样叠加（测试用的默认推荐档位：赛克斯的玩家思路没列美工刀）；姬梦枫带「已持有治愈来源」条件的
        # 规则命中时不叠加
        db = with_player_layer(self.db)
        self.assertEqual([p.score for p in self.combo_parts("赛克斯", "美工刀-初级", ["医疗箱-完备治疗"], db=db)], [35])
        self.assertEqual(self.combo_parts("姬梦枫", "美工刀-初级", ["医疗箱-完备治疗"], db=db), [])

    def test_half_hp_conflict_both_ways(self):
        self.assertEqual([p.score for p in self.combo_parts("墨影", "美工刀-锋利", ["磨刀石"])], [-30])
        self.assertEqual([p.score for p in self.combo_parts("墨影", "可口糖果", ["肾上腺素-高效"])], [-10])
        self.assertEqual([p.score for p in self.combo_parts("墨影", "肾上腺素-高效", ["美工刀-初级"])], [-30])
        self.assertEqual([p.score for p in self.combo_parts("墨影", "磨刀石", ["可口糖果"])], [-10])
        # 帕帕拉（半血特性：潜能使主动技能期间视为生命值不超过一半）不参与半血、满血冲突的组合，冲突由半血特性的规则处理
        for chip, owned in (("美工刀-锋利", ["磨刀石"]), ("磨刀石", ["美工刀-初级"])):
            with self.subTest(chip=chip):
                self.assertEqual(self.combo_parts("帕帕拉", chip, owned), [])
        # 美工刀和治愈来源的组合照常计：技能回血以后常是满血，美工刀在那几个回合同样生效
        self.assertEqual([p.score for p in self.combo_parts("帕帕拉", "美工刀-初级", ["医疗箱-紧急治疗"])], [35])
        # 持有医疗箱又持有磨刀石：两条相加
        mixed = self.combo_parts("墨影", "美工刀-锋利", ["磨刀石", "医疗箱-紧急治疗"])
        self.assertEqual(sum(p.score for p in mixed), 5)

    def test_mark_sources(self):
        # 魔法箭袋、忍术飞镖只对主要使用卡牌的角色计分；鹰眼瞄具只对物理输出角色计分
        self.assertEqual([p.score for p in self.combo_parts("赛克斯", "魔法箭袋", ["手持风扇-大"])], [30])
        self.assertEqual([p.score for p in self.combo_parts("小町", "忍术飞镖", ["标记喷罐"])], [15])
        self.assertEqual(self.combo_parts("邦妮", "魔法箭袋", ["标靶"]), [])
        self.assertEqual([p.score for p in self.combo_parts("邦妮", "鹰眼瞄具", ["标靶"])], [20])
        self.assertEqual(self.combo_parts("赛克斯", "鹰眼瞄具", ["标靶"]), [])
        self.assertEqual([p.score for p in self.combo_parts("赛克斯", "标靶", ["魔法箭袋"])], [15])
        self.assertEqual([p.score for p in self.combo_parts("邦妮", "普通瞄具", ["鹰眼瞄具"])], [10])
        # 魔法箭袋只在怪物已带标记时再施加 1 层，不能单独提供标记，不算标记来源
        self.assertEqual(self.combo_parts("小町", "忍术飞镖", ["魔法箭袋"]), [])
        self.assertEqual(self.combo_parts("美咲", "鹰眼瞄具", ["魔法箭袋"]), [])
        self.assertEqual(self.combo_parts("邦妮", "鹰眼瞄具", ["魔法箭袋"]), [])

    def test_charge_sources(self):
        self.assertEqual([p.score for p in self.combo_parts("茉莉", "电流核心", ["永动机"])], [25])
        self.assertEqual([p.score for p in self.combo_parts("茉莉", "安全气囊", ["永动机"])], [20])
        self.assertEqual([p.score for p in self.combo_parts("茉莉", "电流核心", ["能量回收"])], [15])
        self.assertEqual([p.score for p in self.combo_parts("茉莉", "永动机", ["电流核心"])], [20])
        self.assertEqual([p.score for p in self.combo_parts("茉莉", "跃迁引擎", ["电磁炮"])], [10])
        self.assertEqual(self.combo_parts("茉莉", "电流核心", ["安全气囊"]), [])  # 受到伤害才获得 1 层，不作为来源

    def test_transfer_chips(self):
        # ATM机、友情徽章、汉娜人偶、节日红包都在转账时触发：已持有其中一个时其余几个 +25
        one = self.combo_parts("阿尔", "友情徽章", ["ATM机"])
        self.assertEqual([(p.origin, p.score) for p in one], [("转账筹码叠加", 25)])
        self.assertTrue(one[0].reason.startswith("已持有「ATM机」："), one[0].reason)
        self.assertEqual(one[0].short, "组合：ATM机")
        # 已持有两个时只加一次，理由里每个筹码名各加一对「」
        two = self.combo_parts("阿尔", "汉娜人偶", ["ATM机", "友情徽章"], map="异变图书馆")
        self.assertEqual([p.score for p in two], [25])
        self.assertTrue(two[0].reason.startswith("已持有「ATM机」「友情徽章」："), two[0].reason)
        self.assertEqual([p.score for p in self.combo_parts("米米", "节日红包", ["友情徽章"], map="水乡古镇")], [25])
        # 「转账」特性的角色（帕露南、远野汉娜）的特性规则已按频繁转账计分，不再叠加
        for hero in ("帕露南", "远野汉娜"):
            with self.subTest(hero=hero):
                self.assertEqual(self.combo_parts(hero, "友情徽章", ["ATM机"]), [])
        self.assertEqual(self.combo_parts("阿尔", "友情徽章", []), [])

    def test_owned_count_does_not_scale(self):
        # 每条组合规则只加一次分值，命中一个已持有筹码和命中两个时相同；
        # 例外只有 rules.toml [heal_knife] combos 的两条（物理输出角色按美工刀数计分，见 HealKnifeTest）
        scaled = set(self.db.rules["heal_knife"]["combos"])
        checked = []
        for combo in self.db.combos:
            if combo.name in scaled or len(combo.owned) < 2:
                continue
            held = sorted(combo.owned)[:2]
            candidates = sorted(combo.chips - set(held))
            if not candidates:
                continue
            chip = candidates[0]
            maps = self.db.chips[chip].maps
            kw = dict(map=maps[0]) if maps else {}
            for hero, hs in self.db.hero_synergy.items():
                if not combo.applies_to(hero, hs.traits):
                    continue
                one = [p.score for p in self.combo_parts(hero, chip, held[:1], **kw) if p.origin == combo.name]
                if not one:
                    continue  # 玩家思路里带「已持有」条件的规则命中，不叠加组合规则
                with self.subTest(combo=combo.name, hero=hero):
                    two = [p.score for p in self.combo_parts(hero, chip, held, **kw) if p.origin == combo.name]
                    self.assertEqual(one, [combo.score])
                    self.assertEqual(two, [combo.score])
                checked.append(combo.name)
                break
        self.assertIn("转账筹码叠加", checked)
        self.assertGreaterEqual(len(checked), 10)
        # 适配表审查清单（tools/make_review.py）的说明照此写，并列出例外
        from tools import make_review

        note = "\n".join(make_review.combo_section(self.db)).split("| 名称 |")[0]
        self.assertIn("每条组合规则只加一次分值，已持有其中几个都一样", note)
        for name in scaled:
            self.assertIn(f"「{name}」", note)

    def test_shared_pairs(self):
        # 对所有角色的几组配合（较少挑战怪物的角色不加给攻击力的那几组）：
        # 已持有夹心饼干-美味时可口 +25、一般 +20；已持有电流剑时永动机 +15；御魂庆典的诅咒之剑 → 复仇之戟 +35、
        # 复仇之戟 → 诅咒之剑 +20；友情徽章、大碗炖肉互相 +15
        cases = (("夹心饼干-可口", ["夹心饼干-美味"], 25), ("夹心饼干-一般", ["夹心饼干-美味"], 20),
                 ("永动机", ["电流剑"], 15), ("复仇之戟", ["诅咒之剑"], 35), ("诅咒之剑", ["复仇之戟"], 20))
        for chip, owned, score in cases:
            maps = self.db.chips[chip].maps
            kw = dict(map=maps[0]) if maps else {}
            for hero in ("美咲", "邦妮", "潘大猛", "璐璐"):
                with self.subTest(chip=chip, hero=hero):
                    got = [p for p in self.combo_parts(hero, chip, owned, **kw) if p.origin != "充能消耗与永动机"]
                    self.assertIn(score, [p.score for p in got])
                    self.assertTrue(any(p.reason.startswith(f"已持有「{owned[0]}」：") for p in got))
            for hero in ("凛", "米米", "阿尔"):  # 带「不靠战斗」
                with self.subTest(chip=chip, hero=hero):
                    self.assertFalse([p for p in self.combo_parts(hero, chip, owned, **kw) if p.score == score
                                      and p.origin in {c.name for c in self.db.combos if c.chips == {chip}}])
        for hero in ("潘大猛", "米米", "美咲"):  # 不分角色
            with self.subTest(hero=hero):
                self.assertIn(("友情徽章与大碗炖肉", 15), [(p.origin, p.score) for p in
                                                       self.combo_parts(hero, "大碗炖肉", ["友情徽章"])])
                self.assertIn(("大碗炖肉与友情徽章", 15), [(p.origin, p.score) for p in
                                                       self.combo_parts(hero, "友情徽章", ["大碗炖肉"])])
        # 小蛇玩偶与惊涛御守（龙宫游乐园）：璐璐不加（受到伤害后的被动要靠这 1 点伤害触发，小蛇玩偶会把它减到 0）
        snake = dict(map="龙宫游乐园")
        self.assertEqual([p.score for p in self.combo_parts("美咲", "小蛇玩偶", ["惊涛御守"], **snake)], [25])
        self.assertEqual(self.combo_parts("璐璐", "小蛇玩偶", ["惊涛御守"], **snake), [])

    def test_baseline_results_hold_with_combos(self):
        # 锁定的三组结果：已持有会触发组合规则的筹码时，第一名也不变（凛按测试用的默认推荐档位）
        db = with_player_layer(self.db)
        for owned in ((), ("魔法箭袋",), ("手持风扇-小",), ("医疗箱-紧急治疗",), ("永动机",)):
            with self.subTest(owned=owned):
                self.assertEqual(self.run_case("凛", ["标靶", "标记喷罐", "鹰眼瞄具"], db=db, owned=owned).best, "标记喷罐")
                self.assertEqual(self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"], db=db, owned=owned).best, "大背包")


class HealKnifeTest(Base):
    """美工刀与治愈来源（rules.toml [heal_knife]）。

    物理输出角色还没有美工刀时，治愈来源按「之后获得美工刀的概率 × 组合分」预先加分；已有美工刀时组合分按治愈层数
    缩放（每层 7.5：完备治疗计 6 层，25 → 45；每回合 1 层的来源计 2 层，15；两把美工刀加倍）；其余角色维持组合规则的
    原分值。预判分打折（anticipation_factor = 0.7）；之后的几次三选一怎么算见 AnticipationPicksTest。
    治愈在回合结束时减少一半层数、减少的层数向上取整（3 层剩 1 层，1 层剩 0 层），每回合开始时获得 g 层的来源在自己
    回合里稳定在 2g − 1 层；计分的层数另加 1 层，计治愈来源使美工刀「生命值为满」的加成更易保住。
    """

    def combos(self, hero, chip, owned=(), **kw):
        owned = tuple(owned)
        st = State(hero=hero, owned=owned, **kw)
        return [p for p in Scorer(self.db, st).parts(chip, owned, None) if p.label == "组合"]

    def test_layers_when_knife_owned(self):
        self.assertEqual(self.db.rules["heal_knife"]["traits"], ["物理输出"])
        self.assertEqual(self.db.rules["heal_knife"]["layers"],
                         {"医疗箱-完备治疗": 6, "医疗箱-紧急治疗": 2, "大碗炖肉": 2, "维生素药丸": 2, "可口糖果": 2,
                          "缓冲盾牌": 2, "友情徽章": 2})
        self.assertEqual([p.score for p in self.combos("美咲", "医疗箱-完备治疗", ["美工刀-初级"])], [45])
        # 两把美工刀都持有时治愈层数计两次
        self.assertEqual([p.score for p in self.combos("美咲", "医疗箱-完备治疗", ["美工刀-初级", "美工刀-锋利"])], [90])
        self.assertEqual([p.score for p in self.combos("美咲", "医疗箱-紧急治疗", ["美工刀-锋利"])], [15])
        self.assertEqual([p.score for p in self.combos("美咲", "缓冲盾牌", ["美工刀-初级", "美工刀-锋利"])], [30])
        # 理由仍用组合规则的原文
        self.assertTrue(self.combos("美咲", "医疗箱-完备治疗", ["美工刀-初级"])[0].reason.startswith("已持有「美工刀-初级」："))
        # 不是物理输出角色：原分值，两把美工刀也不加倍
        self.assertEqual([p.score for p in self.combos("米米", "医疗箱-完备治疗", ["美工刀-初级"])], [25])
        self.assertEqual([p.score for p in self.combos("米米", "医疗箱-完备治疗", ["美工刀-初级", "美工刀-锋利"])], [25])
        # 美工刀一侧（已持有治愈来源时美工刀加分）不变
        self.assertEqual([p.score for p in self.combos("美咲", "美工刀-锋利", ["医疗箱-完备治疗"])], [35])

    def test_anticipation_before_knife(self):
        kw = dict(star=1, round=5)
        emergency = self.combos("美咲", "医疗箱-紧急治疗", **kw)
        self.assertEqual(len(emergency), 1)
        part = emergency[0]
        self.assertRegex(part.reason, r"^预计之后获得美工刀的概率约 \d+%：")
        self.assertIsNone(BANNED_WORDING.search(part.reason))
        self.assertGreater(part.score, 0)
        self.assertLess(part.score, 15)
        # 同一个概率：完备治疗计 6 层、紧急治疗计 2 层
        full = self.combos("美咲", "医疗箱-完备治疗", **kw)
        self.assertAlmostEqual(full[0].score, 3 * part.score)
        # 不是物理输出角色没有预判；半血角色帕帕拉照常预判（技能回血以后常是满血，美工刀在那几个回合同样生效）
        self.assertEqual(self.combos("米米", "医疗箱-紧急治疗", **kw), [])
        papara = self.combos("帕帕拉", "医疗箱-紧急治疗", **kw)
        self.assertEqual(len(papara), 1)
        self.assertRegex(papara[0].reason, r"^预计之后获得美工刀的概率约 \d+%：")
        # 已经持有一把美工刀时按层数计分，不再预判另一把
        self.assertEqual([p.score for p in self.combos("美咲", "医疗箱-紧急治疗", ["美工刀-初级"], **kw)], [15])
        # 剩余三选一为 0 时概率为 0，不加分
        self.assertEqual(self.combos("美咲", "医疗箱-紧急治疗", star=3, round=16, map="星趴·梦想号"), [])

    def test_anticipation_follows_remaining_picks(self):
        # 后面的三选一越多，之后获得美工刀的概率越高
        early = self.combos("美咲", "医疗箱-完备治疗", star=1, round=5, map="星趴·梦想号")[0].score
        late = self.combos("美咲", "医疗箱-完备治疗", star=2, round=9, map="星趴·梦想号", owned=("普通瞄具", "速度轮滑-中级"))
        self.assertGreater(early, late[0].score)

    def test_anticipation_factor(self):
        # 预判分 = 概率 × 按层数的组合分（紧急治疗计 2 层 × 7.5）× anticipation_factor（0.7）
        cfg = self.db.rules["heal_knife"]
        self.assertEqual(cfg["anticipation_factor"], 0.7)
        st = State(hero="美咲", star=1, round=5, map="星趴·梦想号")
        scorer = Scorer(self.db, st)
        p = knife_chance(scorer, frozenset(), heal_knife(scorer))
        self.assertGreater(p, 0)
        part = [x for x in scorer.parts("医疗箱-紧急治疗", (), None) if x.label == "组合"]
        self.assertEqual(len(part), 1)
        self.assertAlmostEqual(part[0].score, p * cfg["per_layer"] * cfg["layers"]["医疗箱-紧急治疗"] * 0.7)
        # 已持有美工刀后按层数计分，不打折
        self.assertEqual([x.score for x in self.combos("美咲", "医疗箱-紧急治疗", ["美工刀-初级"], star=1, round=5)], [15])

    def test_anticipation_factor_basis(self):
        # 系数的依据（rules.toml [heal_knife] anticipation_factor）：梦想号实机第 5 轮（美咲 1 星，已有普通瞄具），实际很晚才
        # 拿到美工刀。把之后每一次三选一都按下一星的升星算时约 80%，乘 0.5 计入约 40%；按次数分开算约 56%，乘 0.7 计入约 39%，
        # 和原来相当。改了概率的算法、这里的数对不上时，系数要照这个办法重新定
        st = State(hero="美咲", star=1, round=5, map="星趴·梦想号", owned=("普通瞄具",), rerolls=3,
                   probabilities=(60, 37, 3), left_rarity="蓝")
        sc = Scorer(self.db, st)
        p = knife_chance(sc, frozenset(sc.known_owned), heal_knife(sc))
        self.assertAlmostEqual(p, 0.56, delta=0.01)
        self.assertAlmostEqual(p * self.db.rules["heal_knife"]["anticipation_factor"], 0.40, delta=0.02)

    def test_misaki_round5_keeps_first_choice(self):
        # 梦想号实机第 5 轮：预判分打折后医疗箱-紧急治疗不首推（打折的依据），紧急治疗仍有预判分。
        # 美咲的默认推荐没有固定档位，三个候选都按角色特性计算，首推是速度轮滑-中级（电流剑几乎并列第二：
        # 她的推荐流派是治愈、标记，已有普通瞄具时再进充能，治愈就进不来了）
        adv = self.run_case("美咲", ["电流剑", "医疗箱-紧急治疗", "速度轮滑-中级"], star=1, round=5, map="星趴·梦想号",
                            owned=("普通瞄具",), rerolls=3, probabilities=(60, 37, 3), left_rarity="蓝")
        self.assertEqual(adv.best, "速度轮滑-中级")
        kit = self.by_name(adv)["医疗箱-紧急治疗"]
        self.assertTrue(any(p.label == "组合" and p.score > 0 for p in kit.parts))
        self.assertGreater(self.by_name(adv)["速度轮滑-中级"].percent, kit.percent)


class SurvivalDifficultyTest(Base):
    """难度（rules.toml [survival]）。

    困难及以上难度，生存类筹码加「需要生存」特性分值的一半；普通难度不加，不随轮次变化。
    物理输出角色的摩托头盔再加一半（合计全额）。
    """

    def parts(self, hero, chip, **kw):
        return [p for p in Scorer(self.db, State(hero=hero, **kw)).parts(chip, (), None) if p.label == "难度"]

    def test_hard_and_above_only(self):
        # 凛不是物理输出角色，按一半计
        hard = self.parts("凛", "摩托头盔-中级")
        self.assertEqual([p.score for p in hard], [12.5])  # 「需要生存」25 × 0.5
        self.assertEqual([p.score for p in self.parts("凛", "摩托头盔-中级", round=2)], [12.5])  # 不随轮次变化
        self.assertEqual([p.score for p in self.parts("凛", "摩托头盔-中级", round=15)], [12.5])
        self.assertEqual(self.parts("凛", "摩托头盔-中级", difficulty=NORMAL), [])
        self.assertEqual(self.parts("凛", "标靶"), [])  # 不是生存类筹码
        # 已带「需要生存」特性的角色不再加（自动层已按全额计入）
        self.assertEqual(self.parts("茉莉", "摩托头盔-中级"), [])
        # 理由写机制，不写特性名
        self.assertNotIn("需要生存", hard[0].reason)
        self.assertIsNone(BANNED_WORDING.search(hard[0].reason))

    def test_values_follow_trait_rules(self):
        cfg = self.db.rules["survival"]
        self.assertEqual((cfg["trait"], cfg["factor"]), ("需要生存", 0.5))
        values = {}
        for rule in self.db.trait_rules[cfg["trait"]]:
            if rule.score > 0:
                for chip in rule.chips:
                    values[chip] = max(values.get(chip, 0), rule.score)
        self.assertEqual(values["医疗箱-完备治疗"], 30)
        fighter_chips = set(cfg["fighter_chips"])
        for hero in ("凛", "美咲"):  # 凛不是物理输出角色，美咲是
            fighter = hero == "美咲"
            scorer = Scorer(self.db, State(hero=hero))
            for chip, value in values.items():
                with self.subTest(hero=hero, chip=chip):
                    got = [p.score for p in scorer.parts(chip, (), None) if p.label == "难度"]
                    extra = cfg["fighter_factor"] if fighter and chip in fighter_chips else 0.0
                    self.assertEqual(got, [value * (cfg["factor"] + extra)])

    def test_fighter_helmets(self):
        # 物理输出角色（经常挑战怪物）在困难及以上，摩托头盔的「难度」分项按「需要生存」分值全额计
        cfg = self.db.rules["survival"]
        self.assertEqual((cfg["fighter_traits"], cfg["fighter_factor"]), (["物理输出"], 0.5))
        self.assertEqual(set(cfg["fighter_chips"]), {"摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"})
        self.assertEqual([p.score for p in self.parts("美咲", "摩托头盔-高级")], [35.0])
        self.assertEqual([p.score for p in self.parts("美咲", "摩托头盔-中级")], [25.0])
        self.assertEqual([p.score for p in self.parts("美咲", "摩托头盔-一般")], [15.0])
        # 普通难度不加；已带「需要生存」特性的物理输出角色（茉莉）也不加
        self.assertEqual(self.parts("美咲", "摩托头盔-高级", difficulty=NORMAL), [])
        self.assertEqual(self.parts("茉莉", "摩托头盔-高级"), [])
        # 头盔以外的生存类筹码不变：医疗箱-完备治疗 30 × 0.5
        self.assertEqual([p.score for p in self.parts("美咲", "医疗箱-完备治疗")], [15.0])
        # 理由写机制，不写特性名
        reason = self.parts("美咲", "摩托头盔-高级")[0].reason
        self.assertIn("反击伤害", reason)
        for word in ("物理输出", "需要生存"):
            self.assertNotIn(word, reason)
        self.assertIsNone(BANNED_WORDING.search(reason))

    def test_stacks_on_player_layer(self):
        # 叠加在玩家思路之上：姬梦枫的玩家思路（测试用的默认推荐档位）把医疗箱-完备治疗列为推荐，困难及以上另加「难度」分项
        db = with_player_layer(self.db)
        labels = [p.label for p in Scorer(db, State(hero="姬梦枫")).parts("医疗箱-完备治疗", (), None)]
        self.assertIn("玩家思路", labels)
        self.assertIn("难度", labels)


class ChargeBuildTest(Base):
    """开套预判（rules.toml [charge_build]）。

    还没有充能筹码时，充能筹码加「之后出现并会选择的配套筹码的概率 × 组合分 × 0.5」（之后的几次三选一怎么算见
    AnticipationPicksTest）；已持有充能筹码后由组合规则计分，不再预判。
    """

    def charge_parts(self, hero, chip, owned=(), **kw):
        owned = tuple(owned)
        kw.setdefault("star", 1)
        sc = Scorer(self.db, State(hero=hero, owned=owned, **kw))
        return sc, [p for p in sc.parts(chip, owned, None) if p.origin == "charge_build"]

    def test_anticipation_before_first_charge_chip(self):
        cfg = self.db.rules["charge_build"]
        self.assertEqual((cfg["school"], cfg["factor"]), ("充能", 0.5))
        sc, parts = self.charge_parts("美咲", "永动机")
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part.label, "组合")
        self.assertRegex(part.reason, r"^预计之后获得配套充能筹码的概率约 \d+%：")
        self.assertIsNone(BANNED_WORDING.search(part.reason))
        # 分值 = 概率 × 以永动机为「已持有」一方的组合规则里最高的组合分（25）× 0.5
        combos = [c for c in self.db.combos if "永动机" in c.owned]
        value = max(c.score for c in combos)
        partners = frozenset().union(*(c.chips for c in combos)) - {"永动机"}
        p = partner_chance(sc, "永动机", partners, frozenset())
        self.assertGreater(p, 0)
        self.assertAlmostEqual(part.score, p * value * 0.5)
        self.assertLessEqual(part.score, value * 0.5)

    def test_only_charge_chips_and_only_before_owning_one(self):
        # 不是充能筹码：没有预判
        self.assertEqual(self.charge_parts("美咲", "标靶")[1], [])
        # 已持有充能筹码：不再预判，组合规则照常计分（持有电流剑时电流核心 +15）
        sc, parts = self.charge_parts("美咲", "电流核心", owned=("电流剑",))
        self.assertEqual(parts, [])
        self.assertTrue(any(p.label == "组合" and p.origin == "充能来源与充能消耗"
                            for p in sc.parts("电流核心", ("电流剑",), None)))
        # 持有的是别的流派的筹码时照常预判
        self.assertEqual(len(self.charge_parts("美咲", "永动机", owned=("标靶",))[1]), 1)

    def test_every_charge_chip_with_partners(self):
        # 8 个充能筹码都有以自己为「已持有」一方的组合规则，都会预判；剩余三选一为 0 时概率为 0，不加分（3 星、星趴·梦想号
        # 困难及以上进度上限 18：第 18 轮到了进度上限）
        charge = [n for n, c in self.db.chips.items() if c.school == "充能"]
        self.assertEqual(len(charge), 8)
        for chip in charge:
            with self.subTest(chip=chip):
                has_combo = any(chip in c.owned for c in self.db.combos)
                parts = self.charge_parts("玲玲", chip)[1]
                self.assertEqual(len(parts), 1 if has_combo else 0)
        self.assertEqual(self.charge_parts("玲玲", "永动机", star=3, round=18, map="星趴·梦想号")[1], [])


class AnticipationPicksTest(Base):
    """美工刀预判、治愈来源折算、开套预判的概率（chances.take_chance）按次数分开算，和前瞻同一个口径（context.split_picks）：
    剩余 n 次三选一里，剩下的升星 L 次（min(剩余升星次数, n)）按下一星的升星；其余 n − L 次是任务和筹码商店，按两者在一局里
    的占比混合，稀有度按当前星数（任务等级取当前星数、至少 1 级）。一次三选一的三格出自同一种来源，一次里一个都不出现的
    概率按来源分别算、再按占比加权（Σ 占比 × Π(1 − 这种来源下的出现率)），不把两种来源的分布先平均再算；n 次都不出现是
    升星那一类的 L 次方乘混合那一类的 n − L 次方。3 星以后 L = 0。"""

    HERO, MAP = "美咲", "园林中庭"  # 园林中庭最高难度 4 个筹码任务、筹码商店 1 次：任务占 80%
    OWNED = ("普通瞄具",)
    CHARGE = "永动机"

    def misses(self, sc, owned, take, m):
        """持有 owned 时在地图 m 上一次三选一里 take 一个都不出现的概率：（下一星的升星, 当前星数的任务, 当前星数的筹码商店），
        照 pool 的模型直接算。"""
        db = self.db
        pool = player_pool(db, owned, m)
        schools = owned_schools(db, owned)

        def miss(left, general):
            out = 1.0
            for k in take:
                out *= 1 - appear_prob(left, general, k)
            return out

        star = sc.st.star
        return (miss(*slot_dists(db, sc.hero, pool, schools, min(3, star + 1), class_owned=owned,
                                 difficulty=sc.st.difficulty)),
                miss(*slot_dists(db, sc.hero, pool, schools, star, "task", max(1, star), class_owned=owned,
                                 difficulty=sc.st.difficulty)),
                miss(*slot_dists(db, sc.hero, pool, schools, star, "shop", class_owned=owned,
                                 difficulty=sc.st.difficulty)))

    def shares(self, m) -> tuple[float, float]:
        """（任务的占比, 筹码商店的占比）：本地图的筹码任务数 × task_rate 对 shop。"""
        cfg = self.db.rules["picks"]
        info = self.db.map_info.get(m)
        task = (info.chip_tasks if info else cfg["default_tasks"]) * cfg["task_rate"]
        return task / (task + cfg["shop"]), cfg["shop"] / (task + cfg["shop"])

    def takes(self, sc):
        """三个预判各自「会选的筹码」（只含总分达到推荐线的）和按哪一组已有筹码算：美工刀不含正在评估的治愈来源、
        治愈来源按「已有筹码 + 美工刀-锋利」、配套筹码按「已有筹码 + 永动机」。"""
        db, rnd = self.db, sc.st.round
        line = db.rules["advice"]["recommend_total"]
        cfg = db.rules["heal_knife"]
        owned = sc.ordered(frozenset(sc.known_owned))
        knives = [k for k in cfg["knives"]
                  if sc.score(k, owned, rnd) - heal_discount_score(sc, k, owned, rnd) >= line]
        with_knife = owned + ("美工刀-锋利",)
        heals = [h for h in cfg["layers"] if h not in with_knife and sc.score(h, with_knife, rnd) >= line]
        with_charge = owned + (self.CHARGE,)
        partners = self.partners(sc)
        mates = [k for k in sorted(partners) if sc.score(k, with_charge, rnd) >= line]
        return (owned, knives), (with_knife, heals), (with_charge, mates)

    def partners(self, sc) -> frozenset:
        db = self.db
        school = db.rules["charge_build"]["school"]
        combos = [c for c in db.combos if self.CHARGE in c.owned and c.applies_to(sc.hero.name, sc.traits)]
        return frozenset(x for c in combos for x in c.chips if db.chips[x].school == school) - {self.CHARGE}

    def chances(self, sc):
        owned_set = frozenset(sc.known_owned)
        return (knife_chance(sc, owned_set, heal_knife(sc)), heal_chance(sc, "美工刀-锋利", owned_set),
                partner_chance(sc, self.CHARGE, self.partners(sc), owned_set))

    def test_split_by_picks(self):
        """0～3 星：三个概率都等于「升星 L 次按下一星的升星、其余按任务和筹码商店分别算再加权」的结果；地图待识别时每张
        候选地图按各自的占比算再平均（星趴·梦想号 3 个筹码任务，任务占 75%）。会选的筹码不止一个时，和把两种来源的出现率
        先平均再算不同。美工刀在升星、筹码商店的最左格出自职业筹码池：1、2 星时比把剩下的每一次都按下一星的升星算低。"""
        for star in (0, 1, 2, 3):
            for kw in (dict(map=self.MAP), dict(map_candidates=("星趴·梦想号", self.MAP))):
                sc = Scorer(self.db, State(hero=self.HERO, star=star, round=4 + 2 * star, owned=self.OWNED, **kw))
                n = sc.picks.total
                ups = min(sc.picks.levelups, n)
                self.assertEqual(split_picks(sc, n), (ups, 0, n - ups))  # 没有循环往复还没给的那一次
                self.assertEqual(ups, 3 - star)  # 没被剩余轮次封顶：剩下的升星都算满
                got = self.chances(sc)
                for i, (owned, take) in enumerate(self.takes(sc)):
                    self.assertGreaterEqual(len(take), 2)  # 不止一个：先平均再算的写法和按来源分别算的写法才分得出
                    split, levelup, averaged = [], [], []
                    for m in sc.maps:
                        t, s = self.shares(m)
                        self.assertEqual(mixed_sources(sc, m), ((t, "task", star, max(1, star)), (s, "shop", star, None)))
                        ml, mt, ms = self.misses(sc, owned, take, m)
                        split.append(1 - ml ** ups * (t * mt + s * ms) ** (n - ups))
                        levelup.append(1 - ml ** n)
                        # 先把两种来源的出现率按占比平均、再当成一种分布算（不该这样算）
                        pool = player_pool(self.db, owned, m)
                        schools = owned_schools(self.db, owned)
                        task = slot_dists(self.db, sc.hero, pool, schools, star, "task", max(1, star), class_owned=owned)
                        shop = slot_dists(self.db, sc.hero, pool, schools, star, "shop", class_owned=owned)
                        one = 1.0
                        for k in take:
                            one *= 1 - (t * appear_prob(*task, k) + s * appear_prob(*shop, k))
                        averaged.append(1 - ml ** ups * one ** (n - ups))
                    expect = sum(split) / len(split)
                    with self.subTest(star=star, kw=kw, chance=("knife", "heal", "partner")[i]):
                        self.assertAlmostEqual(got[i], expect, places=12)
                        self.assertGreater(got[i], 0)
                        self.assertNotAlmostEqual(got[i], sum(averaged) / len(averaged), places=6)
                        if i == 0 and star in (1, 2):
                            self.assertLess(got[i], sum(levelup) / len(levelup))

    def test_capped_by_rounds_levelup_only(self):
        """剩余三选一次数被剩余轮次封顶、少于剩余升星次数时，全按下一星的升星算（剩下的升星先算满）。"""
        st = State(hero=self.HERO, star=1, round=15, map=self.MAP, owned=self.OWNED, progress_cap=16, progress=15)
        sc = Scorer(self.db, st)
        n = sc.picks.total
        self.assertTrue(sc.picks.capped)
        self.assertLess(n, sc.picks.levelups)
        self.assertEqual(split_picks(sc, n), (n, 0, 0.0))
        got = self.chances(sc)
        for i, (owned, take) in enumerate(self.takes(sc)):
            with self.subTest(chance=("knife", "heal", "partner")[i]):
                self.assertAlmostEqual(got[i], 1 - self.misses(sc, owned, take, self.MAP)[0] ** n, places=12)

    def test_zero_without_picks_left(self):
        """3 星、这次是任务、已有筹码多于一局的任务和商店三选一：第 9 轮还没到进度上限，之后的三选一按 3 星的下限算
        （late_rate ×（H + 0.5），园林中庭困难及以上进度上限 17：H = (9 + 16) ÷ 2 − 9 = 3.5），三个概率都大于 0；第 17 轮
        到了进度上限、预计之后没有三选一，三个概率都为 0，没有美工刀预判和开套预判。"""
        owned = ("拳击手套-初级", "拳击手套-中级", "优雅之羽", "标靶", "普通瞄具", "摩托头盔-一般", "夹心饼干-一般",
                 "小猪存钱罐")
        st = State(hero=self.HERO, star=3, round=9, map=self.MAP, owned=owned, source="task", rerolls=2)
        sc = Scorer(self.db, st)
        self.assertAlmostEqual(sc.picks.total, self.db.rules["picks"]["late_rate"] * (3.5 + 0.5))
        self.assertTrue(all(p > 0 for p in self.chances(sc)))
        st = dataclasses.replace(st, round=17)
        sc = Scorer(self.db, st)
        self.assertEqual(sc.picks.total, 0)
        self.assertEqual(self.chances(sc), (0.0, 0.0, 0.0))
        for chip in ("医疗箱-紧急治疗", self.CHARGE):
            with self.subTest(chip=chip):
                parts = sc.parts(chip, sc.known_owned, st.round)
                self.assertFalse([p for p in parts if p.reason.startswith("预计之后获得")])

    def test_factors_all_stars(self):
        """预判分 = 概率 × 组合分 × 系数，0～3 星同一个系数：美工刀预判 anticipation_factor = 0.7，开套预判 factor = 0.5。"""
        knife_cfg, charge_cfg = self.db.rules["heal_knife"], self.db.rules["charge_build"]
        self.assertEqual((knife_cfg["anticipation_factor"], charge_cfg["factor"]), (0.7, 0.5))
        layers = knife_cfg["per_layer"] * knife_cfg["layers"]["医疗箱-紧急治疗"]
        for star in (1, 2, 3):
            sc = Scorer(self.db, State(hero=self.HERO, star=star, round=9, map=self.MAP, owned=self.OWNED))
            owned = sc.known_owned
            knife, _, partner = self.chances(sc)
            # 开套预判的组合分：以永动机为「已持有」一方、配的是充能筹码的组合规则里最高的一条
            value = max(c.score for c in self.db.combos if self.CHARGE in c.owned
                        and c.applies_to(sc.hero.name, sc.traits)
                        and any(self.db.chips[x].school == charge_cfg["school"] for x in c.chips))
            with self.subTest(star=star):
                kit = [p for p in sc.parts("医疗箱-紧急治疗", owned, 9) if p.label == "组合"]
                self.assertEqual(len(kit), 1)
                self.assertAlmostEqual(kit[0].score, knife * layers * 0.7)
                charge = [p for p in sc.parts(self.CHARGE, owned, 9) if p.origin == "charge_build"]
                self.assertEqual(len(charge), 1)
                self.assertAlmostEqual(charge[0].score, partner * value * 0.5)


class LookaheadSplitTest(Base):
    """前瞻影响的几次三选一按次数分开算（lookahead.lookahead_on、context.split_picks）：剩下的升星一定会来，先算满，
    min(剩余升星次数, 次数) 次按下一星的升星，其余按任务和筹码商店混合（future 的 mixed，稀有度按当前星数）。0～1 星全按
    升星，3 星以后全按混合，只有 2 星影响 1.5 次时 1 次按升星、半次按混合。说明里的「下次出现率」照旧按下一次三选一的
    主体（0～2 星是升星）。"""

    def delta(self, sc, name, owned, m, mixed=False):
        return (sc.future((*owned, name), map_name=m) if not mixed
                else future(sc, (*owned, name), map_name=m, mixed=True)) - (
            sc.future(owned, zero=name, map_name=m) if not mixed else future(sc, owned, zero=name, map_name=m, mixed=True))

    def test_low_stars_levelup_only(self):
        """0～1 星剩下的升星不少于影响的次数：前瞻 = weight × 次数 × 升星那一次的变化，没有混合的部分。"""
        weight = self.db.rules["lookahead"]["weight"]
        for star in (0, 1):
            sc = Scorer(self.db, State(hero="梅加斯", star=star, round=3, map="龙宫游乐园", owned=("手持风扇-大",)))
            times = sc.lookahead_times()
            self.assertEqual(split_picks(sc, times), (times, 0, 0.0))
            owned = sc.known_owned
            for name in ("忍术飞镖", "维生素药丸", "拳击手套-中级"):
                with self.subTest(star=star, chip=name):
                    self.assertEqual(lookahead_on(sc, name, owned, "龙宫游乐园"),
                                     weight * times * self.delta(sc, name, owned, "龙宫游乐园"))

    def test_two_stars_split(self):
        """2 星影响 1.5 次：1 次按升到 3 星的升星，多出的半次按 2 星时的任务（2 级）和筹码商店（2 星）混合，各自的期望最高分
        按来源分别算再加权。混合的那一份和照 pool 的模型直接算的分布相同。"""
        db = self.db
        sc = Scorer(db, State(hero="梅加斯", star=2, round=5, map="龙宫游乐园", owned=("手持风扇-大",), rerolls=4))
        times = sc.lookahead_times()
        self.assertEqual(times, db.rules["lookahead"]["picks"][2])
        self.assertEqual(split_picks(sc, times), (1, 0, times - 1))
        owned = sc.known_owned
        have = sc.ordered((*owned, "忍术飞镖"))
        pool = player_pool(db, have, "龙宫游乐园")
        schools = owned_schools(db, have)
        t, s = (db.map_info["龙宫游乐园"].chip_tasks * db.rules["picks"]["task_rate"],
                db.rules["picks"]["shop"])
        task = slot_dists(db, sc.hero, pool, schools, 2, "task", 2, class_owned=have)
        shop = slot_dists(db, sc.hero, pool, schools, 2, "shop", class_owned=have)
        self.assertEqual(future_setup(sc, have, "龙宫游乐园", mixed=True)[1],
                         ((t / (t + s), *task), (s / (t + s), *shop)))
        self.assertEqual(future_setup(sc, have, "龙宫游乐园")[1],
                         ((1.0, *slot_dists(db, sc.hero, pool, schools, 3, class_owned=have)),))
        weight = db.rules["lookahead"]["weight"]
        for name in ("忍术飞镖", "维生素药丸"):
            up = self.delta(sc, name, owned, "龙宫游乐园")
            mix = self.delta(sc, name, owned, "龙宫游乐园", mixed=True)
            with self.subTest(chip=name):
                self.assertNotAlmostEqual(up, mix, places=3)
                self.assertAlmostEqual(lookahead_on(sc, name, owned, "龙宫游乐园"), weight * (1 * up + (times - 1) * mix))

    def test_megas_two_stars_keeps_dart(self):
        """梅加斯 2 星第 5 轮、龙宫游乐园、已有手持风扇-大，在拳击手套-中级、维生素药丸、忍术飞镖之间首推忍术飞镖：
        3 星时的探天卫星（金色卡牌筹码只剩它）更常出现在升到 3 星那次的最左格。多出的半次按混合以后前瞻变小，仍是首推。"""
        adv = self.run_case("梅加斯", ["拳击手套-中级", "维生素药丸", "忍术飞镖"], star=2, round=5, map="龙宫游乐园",
                            owned=("手持风扇-大",), rerolls=4)
        self.assertEqual(adv.best, "忍术飞镖")
        dart = self.by_name(adv)["忍术飞镖"]
        self.assertGreater(dart.lookahead, 0)
        self.assertIn("探天卫星下次出现率", dart.lookahead_reason)


class TeammateContentTest(Base):
    """适配表里的队友调分。"""

    def mate_parts(self, hero, chip, mates, **kw):
        st = State(hero=hero, teammates=tuple(mates), **kw)
        return [p for p in Scorer(self.db, st).parts(chip, (), None) if p.label == "队友"]

    def test_reasons_wording(self):
        # 凡是因为队友加的分，理由里都写明是哪几名队友（标签不写「为了队友」，
        # 写「配合队友：X」；整句不加前缀，直接从「队友「{mates}」……」写起）。扣分规则（电脑队友不配合站位）写缺点
        longest = ("远野汉娜、星魅琉华", "星魅琉华等 3 名")
        for i, rule in enumerate(self.db.teammate_rules):
            with self.subTest(i=i, reason=rule.reason):
                self.assertIsNone(BANNED_WORDING.search(rule.reason))
                self.assertTrue("{n}" in rule.reason or "{mates}" in rule.reason)
                self.assertNotIn("为了队友", rule.reason + rule.short)
                # 分值取得小：一条规则的调整量绝对值不超过 20（队友最多 3 名，限定角色时最多为这些角色的人数）。
                # 例外是三名队友都满足时才生效的一条（照的三名队友都较少挑战怪物时额外电池合计 -45）
                most = abs(rule.per) * (min(3, len(rule.heroes)) if rule.heroes else 3)
                limit = 45 if rule.min_count >= 3 else 20
                self.assertLessEqual(most if rule.cap is None else min(most, rule.cap), limit)
                if rule.per > 0:
                    self.assertTrue(rule.reason.startswith("队友「{mates}」"), rule.reason)
                    self.assertNotEqual(rule.reason[len("队友「{mates}」")], "：", rule.reason)  # 不写成前缀加冒号
                    self.assertTrue(rule.short.startswith("配合队友："), rule.short)
                    if rule.who == "bot":
                        self.assertTrue(rule.short.endswith("（电脑）"), rule.short)
                    for fill in longest:
                        self.assertLessEqual(len(rule.short.replace("{mates}", fill)), SHORT_LIMIT, rule.short)
                elif rule.who == "bot":
                    self.assertEqual(rule.short, "电脑队友不配合站位")
                else:
                    # 大碗炖肉对半血打法的队友扣分（攻略视频）、照的三名队友都较少挑战怪物时的额外电池；整句同样从队友写起，
                    # 短句写缺点
                    self.assertIn(rule.short, ("与队友的半血打法冲突", "队友都较少挑战怪物"))
                    self.assertTrue(rule.reason.startswith("队友「{mates}」"), rule.reason)
                self.assertNotIn("物理输出", rule.reason)  # 整句不写特性名
                self.assertNotIn("标记受益", rule.reason)

    def test_mark_link(self):
        # 标记类筹码按施加标记的方式区分，理由写明作用范围。
        # 标靶、手持风扇所有角色都加；标记喷罐只给常用效果牌造成伤害的角色；瞄具只给物理输出角色
        mates = [Teammate("邦妮"), Teammate("凛"), Teammate("阿尔")]
        part = self.mate_parts("美咲", "标靶", mates)
        self.assertEqual([p.score for p in part], [20])
        self.assertTrue(part[0].reason.startswith("队友「邦妮」「凛」在怪物带有标记时技能收益更高；"), part[0].reason)
        self.assertIn("4 格内最近的怪物", part[0].reason)
        self.assertIn("每个回合结束时减 1 层", part[0].reason)
        self.assertEqual(part[0].short, "配合队友：邦妮、凛")
        self.assertEqual(self.mate_parts("美咲", "标靶", [Teammate("阿尔"), Teammate("米米"), Teammate(None)]), [])
        self.assertEqual(self.mate_parts("美咲", "标靶", []), [])  # 不知道队友：不调分
        bonnie = [Teammate("邦妮")]
        # 辅助角色（阿尔）：只有标靶、手持风扇
        self.assertEqual([p.score for p in self.mate_parts("阿尔", "标靶", bonnie)], [10])
        for fan in ("手持风扇-小", "手持风扇-大"):
            with self.subTest(fan=fan):
                fan_parts = self.mate_parts("阿尔", fan, bonnie)
                self.assertEqual([p.score for p in fan_parts], [10])
                self.assertIn("6 格内", fan_parts[0].reason)
        for chip in ("标记喷罐", "普通瞄具", "鹰眼瞄具"):
            with self.subTest(hero="阿尔", chip=chip):
                self.assertEqual(self.mate_parts("阿尔", chip, bonnie), [])
        # 常用效果牌造成伤害的角色：标记喷罐加分，瞄具不加
        for hero in ("凛", "小町", "姬梦朝"):
            with self.subTest(hero=hero):
                self.assertEqual([p.score for p in self.mate_parts(hero, "标记喷罐", bonnie)], [10])
        self.assertEqual(self.mate_parts("凛", "普通瞄具", bonnie), [])
        # 物理输出角色：瞄具加分，标记喷罐不加
        self.assertEqual(self.mate_parts("美咲", "标记喷罐", bonnie), [])
        self.assertEqual([p.score for p in self.mate_parts("美咲", "普通瞄具", bonnie)], [10])
        # 队友施加标记：需要标记的筹码，按自己的角色分卡牌和攻击两类
        card = self.mate_parts("小町", "魔法箭袋", [Teammate("凛"), Teammate("米米"), Teammate("恋")])
        self.assertEqual([p.score for p in card], [10])
        self.assertTrue(card[0].reason.startswith("队友「凛」的技能可对怪物施加标记"), card[0].reason)
        self.assertEqual(self.mate_parts("美咲", "魔法箭袋", [Teammate("凛")]), [])
        # 鹰眼瞄具两个方向都成立：它施加的标记对凛有利，凛施加的标记也让它按层数增加攻击力
        eagle = self.mate_parts("美咲", "鹰眼瞄具", [Teammate("凛")])
        self.assertEqual([p.score for p in eagle], [10, 10])
        self.assertEqual(len({p.reason for p in eagle}), 2)
        self.assertEqual([p.score for p in self.mate_parts("美咲", "鹰眼瞄具", [Teammate("赛克斯")])], [10])

    def test_bank_card_for_human_mates(self):
        # 银行卡-用不完按真人队友人数加分（所有角色的轮次奖励星币 +3）。和「升星加攻」那条可以同时计
        mates = [Teammate("帕露南"), Teammate("茉莉"), Teammate("邦妮")]
        parts = self.mate_parts("阿尔", "银行卡-用不完", mates)
        self.assertEqual(sorted(p.score for p in parts), [5, 15])
        human = next(p for p in parts if p.score == 15)
        self.assertTrue(human.reason.startswith("队友「帕露南」「茉莉」「邦妮」的轮次奖励星币同样 +3"), human.reason)
        self.assertEqual(human.short, "配合队友：帕露南等 3 名")
        bots = [Teammate(h, True) for h in ("帕露南", "茉莉", "邦妮")]
        self.assertEqual([p.score for p in self.mate_parts("阿尔", "银行卡-用不完", bots)], [5])
        mixed = [Teammate("帕露南", True), Teammate("茉莉"), Teammate("邦妮")]
        self.assertEqual(sorted(p.score for p in self.mate_parts("阿尔", "银行卡-用不完", mixed)), [5, 10])
        self.assertEqual(self.mate_parts("阿尔", "银行卡-余额多", mates), [])

    def test_bots_and_area(self):
        mates = [Teammate("墨影"), Teammate("邦妮", True), Teammate("美咲", True)]
        gong = self.mate_parts("帕露南", "大铜锣", mates, map="水乡古镇")
        self.assertEqual(sorted(p.score for p in gong), [-10, 10])
        stew = self.mate_parts("帕露南", "大碗炖肉", [Teammate("凛", True)])
        self.assertEqual([p.score for p in stew], [-5])
        self.assertIn("5 格内", stew[0].reason)
        # 转账类筹码不按电脑调分
        self.assertEqual(self.mate_parts("帕露南", "ATM机", [Teammate("凛", True)] * 3), [])

    def test_hero_links_need_self(self):
        self.assertEqual([p.score for p in self.mate_parts("姬梦枫", "额外电池", [Teammate("姬梦朝")])], [10])
        self.assertEqual(self.mate_parts("墨影", "额外电池", [Teammate("姬梦朝")]), [])
        self.assertEqual([p.score for p in self.mate_parts("超天酱", "额外电池", [Teammate("糖糖")])], [10])
        self.assertEqual([p.score for p in self.mate_parts("恋", "额外电池", [Teammate("超天酱"), Teammate("茉莉")])], [10])
        self.assertEqual([p.score for p in self.mate_parts("照", "额外电池", [Teammate("墨影")])], [5])
        # 姬梦朝与姬梦枫：符卡-福计入效果牌、用来治疗友方；糖糖与超天酱互相（缩短冷却的几张）；
        # 芬妮与凛（触发事件给活体书页）；真梦梓与以手牌为资源的梅加斯、娜蒂斯、米米（每名 +5，最多 +10）
        kaede = [Teammate("姬梦枫")]
        for chip in ("魔法秘典", "小猪存钱罐", "友情徽章"):
            with self.subTest(chip=chip):
                self.assertEqual([p.score for p in self.mate_parts("姬梦朝", chip, kaede)], [10])
                self.assertEqual(self.mate_parts("吉尔", chip, kaede), [])  # 只对姬梦朝
        for chip in ("额外电池", "梦想号模型", "糖果会员证"):
            with self.subTest(chip=chip):
                maps = self.db.chips[chip].maps
                kw = dict(map=maps[0]) if maps else {}
                self.assertEqual([p.score for p in self.mate_parts("超天酱", chip, [Teammate("糖糖")], **kw)], [10])
        self.assertEqual([p.score for p in self.mate_parts("糖糖", "额外电池", [Teammate("超天酱")])], [10])
        self.assertEqual([p.score for p in self.mate_parts("芬妮", "额外电池", [Teammate("凛")])], [10])
        self.assertEqual(self.mate_parts("蓝海晴", "额外电池", [Teammate("凛")]), [])
        hands = [Teammate("梅加斯"), Teammate("娜蒂斯", True), Teammate("米米")]
        self.assertEqual([p.score for p in self.mate_parts("真梦梓", "额外电池", hands)], [10])
        self.assertEqual([p.score for p in self.mate_parts("真梦梓", "额外电池", hands[:1])], [5])

    def test_zhao_without_attackers(self):
        # 照的三名队友都较少挑战怪物（梅加斯、凛、赛克斯这类）：降神借不到多少攻击力、很少追击，额外电池 -45；
        # 只要有一名队友常挑战怪物就不扣，照旧按物理输出的队友每名 +5
        casters = [Teammate("梅加斯"), Teammate("凛"), Teammate("赛克斯", True)]
        parts = self.mate_parts("照", "额外电池", casters)
        self.assertEqual([(p.score, p.short) for p in parts], [(-45, "队友都较少挑战怪物")])
        self.assertTrue(parts[0].reason.startswith("队友「梅加斯」「凛」「赛克斯」都较少挑战怪物"), parts[0].reason)
        mixed = [Teammate("梅加斯"), Teammate("凛"), Teammate("墨影")]
        self.assertEqual([p.score for p in self.mate_parts("照", "额外电池", mixed)], [5])
        self.assertEqual(self.mate_parts("照", "额外电池", casters[:2]), [])  # 只知道两名
        self.assertEqual(self.mate_parts("墨影", "额外电池", casters), [])  # 只对照

    def test_fengshui_heal_sources(self):
        # 队友有姬梦朝、自己是物理输出角色时，
        # 治愈来源按稳定治愈层数加分（每层：真人 2.5、电脑 1.25；完备治疗 5 层：真人 +12.5、电脑 +6.25；其余 1 层）
        bot, human = [Teammate("姬梦朝", True)], [Teammate("姬梦朝", False)]
        self.assertEqual([p.score for p in self.mate_parts("美咲", "医疗箱-完备治疗", human)], [12.5])
        self.assertEqual([p.score for p in self.mate_parts("美咲", "医疗箱-完备治疗", bot)], [6.25])
        self.assertEqual([p.score for p in self.mate_parts("美咲", "医疗箱-紧急治疗", human)], [2.5])
        self.assertEqual([p.score for p in self.mate_parts("美咲", "可口糖果", bot)], [1.25])
        part = self.mate_parts("美咲", "医疗箱-完备治疗", bot)[0]
        self.assertTrue(part.reason.startswith("队友「姬梦朝」的白泽赐福"), part.reason)
        self.assertEqual(part.short, "配合队友：姬梦朝（电脑）")  # 只计电脑队友的规则
        self.assertEqual(self.mate_parts("美咲", "医疗箱-完备治疗", human)[0].short, "配合队友：姬梦朝")
        self.assertNotIn("物理输出", part.reason)  # 理由不写特性名
        # 自己不是物理输出角色时不加；队友里没有姬梦朝时不加
        self.assertEqual(self.mate_parts("米米", "医疗箱-完备治疗", human), [])
        self.assertEqual(self.mate_parts("美咲", "医疗箱-完备治疗", [Teammate("吉尔", False)]), [])
        # 不是治愈来源的筹码不加
        self.assertEqual(self.mate_parts("美咲", "标靶", human), [])

    # 「吃攻击力」的角色：资料里带「物理输出」特性的 19 名
    ATTACKERS = {"Z3000", "南希露", "墨影", "姬梦枫", "娜蒂斯", "帕帕拉", "摩西", "星魅琉华", "橘雪莉", "派德曼", "照", "玲玲",
                 "真梦梓", "糖糖", "美咲", "茉莉", "邦妮", "阿兰娜", "多萝西"}

    def test_baize_only_for_attackers(self):
        # 姬梦朝的技能是给友军加攻击力，不吃攻击力的角色不标「配合队友：姬梦朝」。
        # 35 名角色、队友为真人或电脑姬梦朝：和白泽赐福有关的加分（整句提到白泽赐福）只给物理输出角色；不吃攻击力的角色
        # 在任何筹码上都没有「配合队友：姬梦朝」，只有银行卡-用不完（所有角色的轮次奖励星币 +3，与攻击力无关，只计真人）除外
        attackers = {h for h, hs in self.db.hero_synergy.items() if "物理输出" in hs.traits}
        self.assertEqual(attackers, self.ATTACKERS)
        self.assertEqual(len(self.db.heroes), 35)
        heal = {"医疗箱-完备治疗", "医疗箱-紧急治疗", "大碗炖肉", "维生素药丸", "可口糖果", "缓冲盾牌", "友情徽章"}
        for bot in (False, True):
            mates = (Teammate("姬梦朝", bot),)
            for hero in sorted(h for h in self.db.heroes if h != "姬梦朝"):
                scorer = Scorer(self.db, State(hero=hero, teammates=mates))
                for chip in sorted(self.db.chips):
                    # 只看队友分项（Scorer.parts 里「队友」那几项就是它给的；逐个算全部分项太慢）
                    parts = [p for p in teammate_parts(scorer, chip) if p.score > 0]
                    with self.subTest(bot=bot, hero=hero, chip=chip):
                        baize = [p for p in parts if "白泽赐福" in p.reason]
                        if hero in self.ATTACKERS and chip in heal:
                            self.assertEqual(len(baize), 1)
                            self.assertEqual(baize[0].short, "配合队友：姬梦朝" + ("（电脑）" if bot else ""))
                        else:
                            self.assertEqual(baize, [])
                        if hero not in self.ATTACKERS:
                            named = [p for p in parts if "姬梦朝" in p.short]
                            expect = ["配合队友：姬梦朝"] if chip == "银行卡-用不完" and not bot else []
                            self.assertEqual([p.short for p in named], expect)
        # 排位：刚需名单里没有姬梦朝，治愈来源不会因为姬梦朝提到第二位（TeammateNeedTest.test_zhao_heal_sources_not_lifted）
        self.assertNotIn("姬梦朝", self.db.needs)

    def test_baseline_results_hold_with_teammates(self):
        # 锁定的三组结果：常见的真人队友组合下第一名也不变（凛按测试用的默认推荐档位）
        db = with_player_layer(self.db)
        for names in (("邦妮", "赛克斯", "美咲"), ("米米", "恋", "阿尔"), ("糖糖", "潘大猛", "多萝西")):
            for bots in (False, True):
                mates = [Teammate(h, bots) for h in names]
                with self.subTest(mates=names, bots=bots):
                    rin = [Teammate(h, bots) for h in names if h != "凛"]
                    self.assertEqual(self.run_case("凛", ["标靶", "标记喷罐", "鹰眼瞄具"], db=db, teammates=rin).best,
                                     "标记喷罐")
                    self.assertEqual(self.run_case("凛", ["标靶", "大背包", "缓冲盾牌"], db=db, teammates=rin).best,
                                     "大背包")
                    adv = self.run_case("阿尔", ["缓冲盾牌", "夹心饼干-一般", "循环往复"], star=1, round=1,
                                        teammates=[m for m in mates if m.hero != "阿尔"])
                    self.assertEqual(adv.best, "缓冲盾牌")


class TeammateNeedTest(Base):
    """队友刚需排第二（名单邦妮、凛（标记），阿尔（星光）。姬梦朝（治愈层数）已移出名单）。"""

    BONNIE = (Teammate("邦妮"), Teammate("茉莉"), Teammate("帕露南"))

    def ranking(self, adv):
        rows = [(e.percent, e.chip.name) for e in adv.evals if not e.superseded_by]
        if adv.reroll:
            rows.append((adv.reroll.percent, REROLL))
        return [n for _, n in sorted(rows, key=lambda r: -r[0])]

    def need_parts(self, adv):
        return [(e.chip.name, p) for e in adv.evals for p in e.parts if p.origin == "need"]

    def test_need_list(self):
        # 赛克斯、真梦梓不列入。姬梦朝已移出（选了糖果类筹码并不能直接帮到姬梦朝），治愈来源只留白泽赐福那几条队友调分
        self.assertEqual(set(self.db.needs), {"邦妮", "凛", "阿尔"})
        self.assertEqual(self.db.needs["阿尔"].what, "星光")
        self.assertIn("6 层星光", self.db.needs["阿尔"].why)

    def test_zhao_heal_sources_not_lifted(self):
        # 队友只有真人姬梦朝时治愈来源不提到第二位（两组随机局面，不看这一条时都会提到第二位）。
        # 自己是物理输出角色（帕帕拉）：医疗箱-紧急治疗照常有白泽赐福的「配合队友：姬梦朝」加分，只是不写「刚需」、不提
        zhao = (Teammate("姬梦朝"),)
        st = State(hero="帕帕拉", star=3, round=13, map="水乡古镇", owned=("美工刀-锋利", "摩托头盔-中级"), rerolls=5,
                   teammates=zhao)
        adv = advise(self.db, st, ["肾上腺素-高效", "医疗箱-紧急治疗", "手持风扇-大"])
        self.assertEqual(self.need_parts(adv), [])
        mates = [p for p in self.by_name(adv)["医疗箱-紧急治疗"].parts if p.label == "队友"]
        self.assertEqual([(p.score, p.short) for p in mates], [(2.5, "配合队友：姬梦朝")])
        self.assertTrue(mates[0].reason.startswith("队友「姬梦朝」的白泽赐福"), mates[0].reason)
        self.assertNotIn("刚需", mates[0].reason + mates[0].short)
        # 不吃攻击力的角色（米米）：大碗炖肉和姬梦朝无关，不写「配合队友：姬梦朝刚需治愈层数」、不提到第二
        st = State(hero="米米", star=3, round=8, map="魔法学院", owned=("电流核心", "大背包"), rerolls=5, teammates=zhao)
        offer = ["大碗炖肉", "夹心饼干-一般", "医疗箱-完备治疗"]
        adv = advise(self.db, st, offer)
        self.assertEqual(self.need_parts(adv), [])
        self.assertEqual([p for p in self.by_name(adv)["大碗炖肉"].parts if p.label == "队友"], [])
        # 排位和没有队友时完全相同：姬梦朝不把大碗炖肉提上来（职业分值累加时大碗炖肉凭自身的分就排第二）
        plain = advise(self.db, dataclasses.replace(st, teammates=()), offer)
        self.assertEqual(self.ranking(adv), self.ranking(plain))

    def test_mark_gives_match_teammate_rules(self):
        # 邦妮、凛能提供的筹码分组和「标记」那四条队友调分一致（只对个别角色的那几条另算，例如梅加斯的标记喷罐）
        rules = {(r.chips, r.self_traits) for r in self.db.teammate_rules
                 if r.traits == frozenset({"标记受益"}) and not r.self_heroes}
        for hero in ("邦妮", "凛"):
            with self.subTest(hero=hero):
                gives = {(g.chips, g.self_traits) for g in self.db.needs[hero].gives}
                self.assertEqual(gives, rules)

    def test_lifted_to_second(self):
        st = State(hero="凛", star=1, round=4, rerolls=3, teammates=self.BONNIE)
        adv = advise(self.db, st, ["探天卫星", "魔法秘典", "标记喷罐"])
        self.assertEqual(adv.best, "探天卫星")
        self.assertEqual(self.ranking(adv), ["探天卫星", "标记喷罐", "魔法秘典", REROLL])
        lifted = self.need_parts(adv)
        self.assertEqual([name for name, _ in lifted], ["标记喷罐"])
        part = lifted[0][1]
        self.assertEqual((part.label, part.short), ("队友", "配合队友：邦妮刚需标记"))
        self.assertTrue(part.reason.startswith("队友「邦妮」刚需标记（邦妮攻击带标记的怪物时攻击力 +3"), part.reason)
        self.assertTrue(part.reason.endswith("；该筹码可提供，排在第二位，第一位仍按自身收益"), part.reason)
        self.assertLessEqual(len(part.short), SHORT_LIMIT)
        self.assertIsNone(SCORE_WORDING.search(part.short))

    # 首推是刷新、刚需把手持风扇-大提到原来总分最高的探天卫星（照的专属规则：每回合获得轨道炮）之上（队友邦妮、凛、
    # 姬梦朝都是真人；已持有美工刀-初级）。要求之后还有三选一（刷新期望里有前瞻），并且刷新后可能出现的筹码里有总分
    # 落在探天卫星和提升后的手持风扇-大之间的，两种比较对象算出的概率才不同
    LIFT = dict(hero="照", star=3, round=11, owned=("美工刀-初级",), rerolls=4,
                teammates=(Teammate("邦妮"), Teammate("凛"), Teammate("姬梦朝")))
    LIFT_OFFER = ["手持风扇-大", "探天卫星", "大铁锚"]

    def test_reroll_compares_with_lifted(self):
        # 刚需提到第二、首推是刷新时，刷新的结论和概率都和被提的那张比（含提升后的
        # 总分），三条标签、悬停、诊断包、命令行（含 --json 的 top、p_better）、推荐日志里的 Advice.top 同一个比较对象
        # 照的默认推荐档位用测试用的那一份（这一组局面是按有档位时挑的）
        from advisor.ui import text as ui_text
        db = with_player_layer(self.db)
        st = State(**self.LIFT)
        adv = advise(db, st, self.LIFT_OFFER)
        self.assertEqual(adv.best, REROLL)
        self.assertEqual(self.ranking(adv), [REROLL, "手持风扇-大", "探天卫星", "大铁锚"])
        self.assertEqual([n for n, _ in self.need_parts(adv)], ["手持风扇-大"])
        fan, sat = self.by_name(adv)["手持风扇-大"], self.by_name(adv)["探天卫星"]
        self.assertIs(adv.evals[adv.top], fan)
        self.assertIs(ui_text.best_chip(adv), fan)
        self.assertGreater(fan.total, sat.total)
        need = sum(p.score for name, p in self.need_parts(adv) if name == "手持风扇-大")
        self.assertLess(fan.total - need, sat.total)  # 没有刚需的提升时排在探天卫星之后
        # 概率按被提的那张（提升后的总分）重算：比按探天卫星算的小，显示的数也不同（「90%」→「85%」）。这一局剩 4 次刷新、
        # 之后的三选一不到 1 次：留 2 次（1 次，再多留 1 次以防估少），多出来的 2 次按最多连着刷 2 次估值（概率按 2 次之内）
        scorer = Scorer(db, st, offer=self.LIFT_OFFER)
        self.assertEqual((adv.reroll.chain, adv.reroll.kept, adv.reroll.spare), (2, 2, 2))
        self.assertEqual(adv.reroll.p_better, scorer.reroll_p_better(self.LIFT_OFFER, fan.total))
        old = scorer.reroll_p_better(self.LIFT_OFFER, sat.total)
        self.assertLess(adv.reroll.p_better, old)
        self.assertEqual((ui_text.chance_text(old), ui_text.chance_text(adv.reroll.p_better)), ("90%", "85%"))
        pills = ui_text.reroll_pills(adv.reroll, ui_text.best_chip(adv), st.rerolls)
        self.assertEqual([p.text for p in pills],
                         ["预计优于手持风扇-大", "85% 概率刷出优于手持风扇-大的筹码", "留 2 次，多出 2 次不必留"])
        for pill in pills[:2]:
            self.assertIn("「手持风扇-大」", pill.tip)
            self.assertNotIn("探天卫星", pill.tip)
        # 诊断包和命令行：比较对象就是候选里总分最高的（被提的那张），写「当前候选筹码最高总分」，概率和标签同一个数
        diag = ui_text.reroll_reason(adv.reroll, adv)
        self.assertEqual(diag, "最多连续刷新 2 次时，出现总分高于当前候选筹码最高总分的筹码的概率：85%")
        self.assertEqual(cli.reroll_p_text(adv, adv.reroll), "出现总分高于当前候选筹码最高总分的筹码的概率：85%")
        self.assertIn("出现总分高于当前候选筹码最高总分的筹码的概率：85%", cli.render(db, adv))
        data = cli.to_json(adv)
        self.assertEqual((data["top"], data["reroll"]["p_better"]), ("手持风扇-大", round(adv.reroll.p_better, 4)))
        # 推荐日志记的是整个 Advice（logbook.record_offer → diagnostics._jsonable）：top 和 p_better 同上
        from advisor.diagnostics import _jsonable
        record = _jsonable(adv)
        self.assertEqual(record["evals"][record["top"]]["chip"]["name"], "手持风扇-大")
        self.assertEqual(record["reroll"]["p_better"], adv.reroll.p_better)
        # 没有刚需提升时（队友邦妮、凛是电脑，姬梦朝照旧是真人）一切照旧：和探天卫星比
        bots = tuple(Teammate(t.hero, t.hero in ("邦妮", "凛")) for t in st.teammates)
        plain = advise(db, dataclasses.replace(st, teammates=bots), self.LIFT_OFFER)
        self.assertEqual(self.need_parts(plain), [])
        self.assertEqual(plain.evals[plain.top].chip.name, "探天卫星")
        top = plain.evals[plain.top]
        self.assertEqual(plain.reroll.p_better, Scorer(db, plain.state, offer=self.LIFT_OFFER)
                         .reroll_p_better(self.LIFT_OFFER, top.total))

    def test_reroll_compare_unchanged_when_chip_first(self):
        # 首推是筹码时刚需只提到第二，提不到首推之上：比较对象照旧是首推（test_lifted_to_second 那一组）
        st = State(hero="凛", star=1, round=4, rerolls=3, teammates=self.BONNIE)
        offer = ["探天卫星", "魔法秘典", "标记喷罐"]
        adv = advise(self.db, st, offer)
        self.assertEqual(adv.evals[adv.top].chip.name, "探天卫星")
        self.assertEqual(adv.reroll.p_better,
                         Scorer(self.db, st, offer=offer).reroll_p_better(offer, adv.evals[adv.top].total))

    def test_bot_or_unknown_mate_not_counted(self):
        offer = ["探天卫星", "魔法秘典", "标记喷罐"]
        plain = advise(self.db, State(hero="凛", star=1, round=4, rerolls=3), offer)
        for mate in (Teammate("邦妮", True), Teammate(None)):
            with self.subTest(mate=mate):
                st = State(hero="凛", star=1, round=4, rerolls=3, teammates=(mate, Teammate("茉莉"), Teammate("帕露南")))
                adv = advise(self.db, st, offer)
                self.assertEqual(self.need_parts(adv), [])
                self.assertEqual(self.ranking(adv), self.ranking(plain))

    def test_can_pass_refresh(self):
        # 刷新也参加排名：标靶越过刷新排到第二
        mates = (Teammate("邦妮"), Teammate("吉尔"), Teammate("芬妮"))
        st = State(hero="派德曼", star=3, round=10, map="幽魂暗巷", owned=("摩托头盔-一般", "普通瞄具", "可口糖果"),
                   rerolls=default_rerolls(self.db, 3), teammates=mates)
        adv = advise(self.db, st, ["鹰眼瞄具", "维生素药丸", "标靶"])
        self.assertEqual(self.ranking(adv)[:3], ["鹰眼瞄具", "标靶", REROLL])

    def test_useless_school_not_lifted(self):
        # 不能把自己锁进对自己没用的流派：阿兰娜（推荐流派治愈）已进入星光，拿普通瞄具会占满流派名额（星光、标记），
        # 治愈本局再也进不去：普通瞄具排第三、本身够推荐线也不提；已持有标记喷罐后可以提。
        # 只进入了治愈时拿它不耽误治愈，照样提（tests/test_need_useless_school.py）
        mates = (Teammate("邦妮"), Teammate("吉尔"), Teammate("芬妮"))
        offer = ["拳击手套-高级", "精品剑盾", "普通瞄具"]
        st = State(hero="阿兰娜", star=3, round=8, owned=("紫色飞星", "书签"),
                   rerolls=default_rerolls(self.db, 3), teammates=mates)
        self.assertEqual(self.need_parts(advise(self.db, st, offer)), [])
        marked = dataclasses.replace(st, owned=(*st.owned, "标记喷罐"))
        adv = advise(self.db, marked, offer)
        self.assertEqual([name for name, _ in self.need_parts(adv)], ["普通瞄具"])
        self.assertEqual(self.ranking(adv)[1], "普通瞄具")

    def test_needs_recommend_line_without_teammate_parts(self):
        # 本身的分（不计为正的「队友」分项）要达到推荐线；把推荐线提到它之上就不提
        st = State(hero="凛", star=1, round=4, rerolls=3, teammates=self.BONNIE)
        offer = ["探天卫星", "魔法秘典", "标记喷罐"]
        adv = advise(self.db, st, offer)
        spray = self.by_name(adv)["标记喷罐"]
        own = spray.total - sum(p.score for p in spray.parts if p.label == "队友" and p.score > 0)
        rules = dict(self.db.rules, advice={**self.db.rules["advice"], "recommend_total": own + 1})
        high = dataclasses.replace(self.db, rules=rules)
        self.assertEqual([n for n, _ in self.need_parts(advise(high, st, offer))], [])

    def test_no_need_in_live_game(self):
        # 10-03 那一局的 11 次三选一：队友邦妮在名单里，但候选里没有标记类筹码，一次都没有提
        live = AlLiveReviewTest("test_offers_follow_picks")
        live.db = self.db
        for key in AlLiveReviewTest.OFFERS:
            with self.subTest(offer=key):
                self.assertEqual(self.need_parts(live.advise_at(key)), [])


class DeterminismTest(Base):
    """同一个局面每次启动的结果一样（下面两处容易随已有筹码的先后、哈希种子变，
    正常情况下的分值不变）。"""

    # 已有筹码记成 4 个流派（按获得顺序：治愈、星光、充能、标记；只有识别有误或手动补录错时才会这样），
    # 最后一次三选一（刷新按连续刷新估值）
    MESSY = dict(hero="阿兰娜", star=3, round=12, map="园林中庭",
                 owned=("佐茶蛋糕", "友情徽章", "贯穿之铳", "速度轮滑-高级", "紫色飞星", "电磁炮", "糖果会员证", "智能手表", "标靶"),
                 rerolls=2)
    MESSY_OFFER = ("拳击手套-初级", "书签", "摩托头盔-一般")
    # 刷新后的筹码池里，跃迁引擎和比较对象能量回收的分值相等（15.21…，不是整数，求和顺序不同时末位会不同）。
    # 另两个候选的分值都要比能量回收低，比较对象才是能量回收：取小猪存钱罐和孔雀扇（孔雀扇是园林中庭的专属筹码）
    TIE = dict(hero="梅加斯", star=2, owned=("摩托头盔-高级", "手电筒-强光", "电磁炮"), source="task", tier=2, rerolls=5,
               difficulty_level=LEVEL_NIGHTMARE, difficulty_name="疯狂")
    TIE_OFFER = ("小猪存钱罐", "能量回收", "孔雀扇")
    # 子进程里算一遍这两个局面：各候选的总分、刷新的期望和概率、首推
    CODE = (
        "import json, sys\n"
        "from advisor.data import load_db\n"
        "from advisor.engine import advise\n"
        "from advisor.pool import State\n"
        "db = load_db()\n"
        "out = []\n"
        "for st, offer in json.loads(sys.argv[1]):\n"
        "    adv = advise(db, State(**st), offer)\n"
        "    r = adv.reroll\n"
        "    out.append([[round(e.total, 9) for e in adv.evals], round(r.expected, 9), round(r.p_better, 9), adv.best])\n"
        "print(json.dumps(out))\n"
    )

    def test_parts_ignore_owned_order(self):
        # 分值按已有筹码的集合缓存，计算也只看集合：调用方传进来的顺序不同（有的从集合转回元组，随哈希种子变化）时结果一样。
        # 流派按获得顺序取前两个（治愈、星光），所以美工刀-初级（治愈流派）有关键词分。
        # 按传进来的顺序取的话，倒过来传时取到标记、充能，关键词分就没了（155 分或 140 分）
        st = State(**self.MESSY)
        owned = st.owned
        self.assertEqual(owned_schools(self.db, owned), ("治愈", "星光"))
        perms = list(itertools.islice(itertools.permutations(owned), 0, 50000, 4999))
        orders = [owned, owned[::-1], tuple(sorted(owned)), tuple(frozenset(owned)), *perms]
        results = set()
        for order in orders:
            scorer = Scorer(self.db, st)
            parts = scorer.parts("美工刀-初级", order, st.round)
            results.add(tuple((p.label, round(p.score, 9)) for p in parts))
            self.assertIn("关键词分", [p.label for p in parts], order)
            # 「拿了之后」的推算（已有筹码加一个假设拿到的）也一样：假设拿到的排在最后
            scorer.parts("美工刀-初级", (*order, "书签"), st.round)
        self.assertEqual(len(results), 1)

    def test_same_result_across_hash_seeds(self):
        # 子进程用不同的哈希种子（0、1、11）各算一遍，结果要逐位相同：不稳定时刷新的期望、概率会随种子变
        # （MESSY 可差到 125.2 对 119.7）
        cases = [[dict(self.MESSY, owned=list(self.MESSY["owned"])), list(self.MESSY_OFFER)],
                 [dict(self.TIE, owned=list(self.TIE["owned"])), list(self.TIE_OFFER)]]
        outs = []
        for seed in (0, 1, 11):
            env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(ROOT)}
            done = subprocess.run([sys.executable, "-W", "ignore", "-c", self.CODE, json.dumps(cases)], capture_output=True,
                                  text=True, env=env, cwd=str(ROOT), timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            outs.append(json.loads(done.stdout))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])
        here = advise(self.db, State(**self.MESSY), list(self.MESSY_OFFER))
        self.assertAlmostEqual(here.reroll.expected, outs[0][0][1], places=6)
        self.assertEqual(f"{outs[0][1][2]:.0%}", "96%")

    def test_prob_above_ignores_last_digit(self):
        # 分值和比较对象按 tie_key 比（舍到 9 位小数）：分值相等的筹码不算「高于」，末位误差不影响结果
        x = 45.33435891706853
        general = {"甲": 0.5, "乙": 0.5}
        for noise in (0.0, 1e-14, -1e-14):
            with self.subTest(noise=noise):
                self.assertEqual(prob_above(None, general, {"甲": 10.0, "乙": x + noise}, x), 0.0)
        self.assertAlmostEqual(prob_above(None, general, {"甲": 10.0, "乙": x + 1e-6}, x), 1 - 0.5 ** 3)
        left = {"甲": 1.0}
        self.assertEqual(prob_above(left, general, {"甲": 10.0, "乙": x + 1e-14}, x), 0.0)

    def test_reroll_chance_stable_at_tie(self):
        # 比较对象的总分加减末位误差，刷出更好筹码的概率不变（跃迁引擎和它相等，不算更好）
        st = State(**self.TIE)
        adv = advise(self.db, st, list(self.TIE_OFFER))
        x = adv.evals[adv.top]
        self.assertEqual(x.chip.name, "能量回收")
        scorer = Scorer(self.db, st, offer=self.TIE_OFFER)
        tied = [n for _, _, value in reroll_dists(scorer, self.TIE_OFFER) for n, v in value.items()
                if abs(v - x.total) < 1e-9]
        self.assertIn("跃迁引擎", tied)  # 这一组确实有平局（免得这条测试空跑）
        for noise in (0.0, 1e-12, -1e-12):
            self.assertEqual(scorer.reroll_p_better(self.TIE_OFFER, x.total + noise), adv.reroll.p_better)
        # 刷出的筹码的分值含前瞻（2 星前瞻里多出的半次按任务和筹码商店混合，见 LookaheadSplitTest）
        self.assertEqual(f"{adv.reroll.p_better:.0%}", "96%")


class CoreVsRefreshTest(PlayerBase):
    """必拿只和同一次三选一里未列入的筹码比，不和「刷新」比（必拿在场时也可能首推刷新）。测试用的默认推荐档位。"""

    def test_core_not_lifted_over_refresh(self):
        owned = ("跃迁引擎", "手持风扇-小")
        adv = self.run_case("凛", ["大背包", "会员推荐信", "智能手表"], star=2, round=5, owned=owned, rerolls=4)
        ev = self.by_name(adv)
        # 大背包的总分没有补到刷新之上：分值与筹码本身的分加前瞻相同，首推按原分比较
        scorer = Scorer(self.db, adv.state)
        self.assertAlmostEqual(ev["大背包"].total, scorer.score("大背包", owned, 5) + scorer.lookahead("大背包", owned))
        self.assertAlmostEqual(ev["大背包"].immediate, sum(p.score for p in ev["大背包"].parts))
        self.assertFalse(any("优先于" in r for e in adv.evals for r in e.reasons(limit=5)))
        self.assertEqual(adv.best, REROLL if adv.reroll.value > ev["大背包"].total else "大背包")

    def test_core_lifted_over_unlisted_only(self):
        # 必拿相对本次未列入的筹码照常补分，理由只写这一项
        adv = self.run_case("凛", ["大背包", "额外电池", "探天卫星"])
        ev = self.by_name(adv)
        lead = self.db.rules["synergy"]["core_lead"]
        self.assertGreaterEqual(ev["大背包"].total, max(ev["额外电池"].total, ev["探天卫星"].total) + lead - 1e-6)
        # 整句是凛的玩家思路原文，后面另起一句写补分；理由标签的短句照旧「筹码方案：必选」
        part = next(p for p in ev["大背包"].parts if p.label == "玩家思路")
        self.assertEqual(part.reason, f"{self.db.player_synergy['凛'].note}。优先于本次未列入筹码方案的筹码")
        self.assertEqual(part.short, "筹码方案：必选")
        self.assertEqual(ev["大背包"].reasons(limit=1)[0].rsplit("（", 1)[0], part.reason)

    def test_core_rule_lift_sentence(self):
        # 规则把筹码列为必选（分值达到必选档）时补到没列入的拳击手套-高级之上，整句直接接「，优先于……」，
        # 不写成「……列为必选。优先于……」；同一次补分的探天卫星（必选档，整句是攻略原文）另起一句。
        # 现在的默认推荐没有分值达到必选档的规则，用测试里造的一段玩家思路
        ps = _merge_players("墨影", [{
            "hero": "墨影", "source": "攻略", "note": "测试用的攻略原文", "core": ["探天卫星"],
            "rule": [{"chips": ["鹰眼瞄具"], "needs_owned": ["普通瞄具"], "score": 110,
                      "reason": "筹码方案：测试用的带条件规则，分值达到必选档", "short": "筹码方案：测试用的规则"}],
        }], self.fail)
        db = dataclasses.replace(self.db, player_synergy={**self.db.player_synergy, "墨影": ps})
        owned = ("普通瞄具",)
        adv = advise(db, State(hero="墨影", star=1, round=4, owned=owned, rerolls=default_rerolls(db, 1, owned)),
                     ["鹰眼瞄具", "拳击手套-高级", "探天卫星"])
        ev = self.by_name(adv)
        part = next(p for p in ev["鹰眼瞄具"].parts if p.label == "玩家思路")
        self.assertGreater(part.score, self.db.rules["synergy"]["core"])  # 补过分
        self.assertEqual((part.reason, part.short),
                         ("筹码方案：测试用的带条件规则，分值达到必选档，优先于本次未列入筹码方案的筹码", "筹码方案：测试用的规则"))
        note = next(p for p in ev["探天卫星"].parts if p.label == "玩家思路").reason
        self.assertEqual(note, "测试用的攻略原文。优先于本次未列入筹码方案的筹码")
        # 没写短句的默认推荐规则，自动短句按「筹码方案：」开头取第一个分句（advice_text.free_short）
        sc = Scorer(self.db, State(hero="帕露南"))
        self.assertEqual(free_short(sc.db, "筹码方案：已持有治愈来源，治愈层数多时可以选择美工刀"), "筹码方案：已持有治愈来源")

    def test_no_lift_without_core(self):
        adv = self.run_case("派德曼", ["会员推荐信", "小猪存钱罐", "书签"], star=1, round=4)
        self.assertEqual(adv.best, REROLL)
        self.assertFalse(any("优先于" in r for e in adv.evals for r in e.reasons(limit=5)))

    def test_parunan_case_from_review(self):
        # 帕露南这组（3 星第 10 轮）：ATM机 列为推荐、不是必选，没有补分；收入折算照常计入推荐档的加分（只有必选档的
        # 加分不计入，见 FennyLiveReviewTest.test_core_income_excludes_player_bonus）。星光只剩 2 次兑现，
        # ATM机 排在列为可选的大碗炖肉后面。这一局剩 8 次刷新（含循环往复的 3 次），比之后的三选一多出好几次，多出来的
        # 按可以连着刷估值，首推是刷新；候选之间大碗炖肉排最前
        adv = self.run_case("帕露南", ["星币锤", "大碗炖肉", "ATM机"], star=3, round=10,
                            owned=("小猪存钱罐", "手电筒-强光", "循环往复", "会员推荐信"))
        self.assertEqual((adv.best, adv.evals[adv.top].chip.name), (REROLL, "大碗炖肉"))
        self.assertGreater(adv.reroll.spare, 1)
        atm = self.by_name(adv)["ATM机"]
        self.assertEqual([p.score for p in atm.parts if p.label == "玩家思路"], [self.db.rules["synergy"]["first"]])
        income = next(p for p in atm.parts if p.label == "收入折算")
        self.assertLess(income.score, -self.db.rules["synergy"]["first"] / 2)  # 推荐档的加分一并折算
        self.assertFalse(any("优先于" in r for e in adv.evals for r in e.reasons(limit=5)))


class MutatorTest(Base):
    """畸变组件只提示、未计入推荐度。"""

    def test_notes_only(self):
        plain = self.run_case("帕帕拉", ["美工刀-初级", "磨刀石", "夹心饼干-可口"], star=2, rerolls=0)
        rain = self.run_case("帕帕拉", ["美工刀-初级", "磨刀石", "夹心饼干-可口"], star=2, rerolls=0, mutators=("暴雨", "雷云", "不存在"))
        self.assertEqual([e.total for e in plain.evals], [e.total for e in rain.evals])
        notes = [n for n in rain.notes if n.startswith("畸变组件")]
        self.assertEqual(len(notes), 1)  # 雷云没有选筹码的提示，不认识的组件忽略
        self.assertIn("与美工刀冲突", notes[0])
        self.assertTrue(notes[0].endswith("（仅提示，未计入推荐度）"))


class EdgeWordingTest(Base):
    """剩余三选一为 0 时刷新代价为 0（不显示 -0）；轮次未知时收入折算不写「按剩余兑现次数」；畸变组件提示不重复句号。"""

    def test_zero_picks_zero_cost(self):
        self.assertEqual(harmonic(0), 0.0)
        self.assertAlmostEqual(harmonic(1), 1.0, places=5)  # 渐近展开约有 1e-6 的误差
        # 3 星、第 19 轮（御魂庆典困难及以上的进度上限 19：到了进度上限）：之后没有三选一，刷新没有代价
        adv = self.run_case("凛", ["大背包", "额外电池", "标靶"], star=3, round=19, map="御魂庆典")
        self.assertEqual(adv.picks.total, 0)
        self.assertEqual(adv.reroll.cost, 0.0)
        self.assertEqual(f"{adv.reroll.cost:.0f}", "0")

    def test_income_wording_without_round(self):
        adv = self.run_case("梅加斯", ["小猪存钱罐", "会员推荐信", "智能手表"], star=3, rerolls=0)
        part = next(p for p in self.by_name(adv)["小猪存钱罐"].parts if p.label == "收入折算")
        self.assertTrue(part.reason.startswith("星币收入折算：已达到 3 星"), part.reason)
        with_round = self.run_case("梅加斯", ["小猪存钱罐", "会员推荐信", "智能手表"], star=3, round=6, rerolls=0)
        part = next(p for p in self.by_name(with_round)["小猪存钱罐"].parts if p.label == "收入折算")
        self.assertIn("按剩余兑现次数折算", part.reason)

    def test_mutator_note_punctuation(self):
        for name, m in self.db.mutators.items():
            if m.hint:
                note = mutator_notes(self.db, State(hero="凛", mutators=(name,)))[0]
                with self.subTest(name=name):
                    self.assertNotIn("。（", note)
                    self.assertTrue(note.endswith("（仅提示，未计入推荐度）"))


class StateTest(Base):
    def test_lists_become_tuples(self):
        st = State(hero="凛", owned=["标靶"], map_candidates=["魔法学院"], teammates=[{"hero": "邦妮", "bot": False}],
                   mutators=["暴雨"])
        self.assertEqual(st.owned, ("标靶",))
        self.assertEqual(st.teammates, (Teammate("邦妮", False),))
        self.assertEqual(hash(st), hash(State(hero="凛", owned=("标靶",), map_candidates=("魔法学院",),
                                              teammates=(Teammate("邦妮"),), mutators=("暴雨",))))


class NormalDifficultyTest(Base):
    """普通难度：稀有度概率和进度上限按普通难度取。
    困难、噩梦、疯狂、极限的筹码概率相同，不区分，进度上限取疯狂一列。"""

    OFFER = ["会员推荐信", "小猪存钱罐", "书签"]

    def test_tables_tell_difficulty_apart(self):
        rarity = self.db.rules["rarity"]
        self.assertEqual(rarity["rows"], [[60, 37, 3], [60, 37, 3], [40, 45, 15], [25, 50, 25]])
        self.assertEqual(rarity["normal"], [[60, 30, 10], [60, 30, 10], [40, 40, 20], [20, 50, 30]])
        # 两张表没有相同的行：一次三选一的概率行就能分辨是不是普通难度
        self.assertFalse({tuple(r) for r in rarity["rows"]} & {tuple(r) for r in rarity["normal"]})
        self.assertEqual(difficulty_of(self.db, (40, 40, 20)), NORMAL)
        self.assertEqual(difficulty_of(self.db, [40, 45, 15]), HARD)
        self.assertIsNone(difficulty_of(self.db, (50, 40, 10)))
        self.assertIsNone(difficulty_of(self.db, None))

    def test_rarity_row_by_difficulty(self):
        def row(*args, **kw):
            probs, fixed = rarity_row(self.db, *args, **kw)
            return [round(p, 6) for p in probs], fixed

        self.assertEqual(row(1, "levelup", None, difficulty=NORMAL), ([0.6, 0.3, 0.1], "蓝"))
        self.assertEqual(row(1, "levelup", None), ([0.6, 0.37, 0.03], "蓝"))
        self.assertEqual(row(2, "shop", None, difficulty=NORMAL), ([0.4, 0.4, 0.2], "紫"))
        # 任务筹码：等级 1~3 级对应同一张表的第 1~3 行，没有固定稀有度的格子
        self.assertEqual(row(0, "task", 3, difficulty=NORMAL), ([0.2, 0.5, 0.3], None))
        self.assertEqual(row(0, "task", 3), ([0.25, 0.5, 0.25], None))
        db, hero = self.db, self.db.heroes["邦妮"]
        _, general = slot_dists(db, hero, player_pool(db, (), None), (), 1, "task", 3, difficulty=NORMAL)
        self.assertAlmostEqual(sum(p for n, p in general.items() if db.chips[n].rarity == "金"), 0.30)

    def test_reroll_uses_normal_table(self):
        kw = dict(star=1, round=4, map="魔法学院")
        hard = self.run_case("邦妮", self.OFFER, **kw)
        normal = self.run_case("邦妮", self.OFFER, difficulty=NORMAL, **kw)
        self.assertNotAlmostEqual(hard.reroll.expected, normal.reroll.expected, places=3)
        # 按普通难度的概率表（1 星：60/30/10）重算一遍刷新后的期望最高分，和引擎的结果一致
        scorer = Scorer(self.db, normal.state, offer=self.OFFER)
        pool = player_pool(self.db, (), "魔法学院", exclude=self.OFFER)
        left, general = slot_dists(self.db, scorer.hero, pool, (), 1, reroll=True, difficulty=NORMAL)
        self.assertAlmostEqual(sum(p for n, p in general.items() if self.db.chips[n].rarity == "金"), 0.10)
        value = {c.name: scorer.score(c.name, (), 4) + lookahead_on(scorer, c.name, (), "魔法学院") for c in pool}
        self.assertAlmostEqual(expected_best(left, general, value), normal.reroll.expected)
        self.assertIn(NORMAL_NOTE, normal.notes)
        self.assertNotIn(NORMAL_NOTE, hard.notes)

    def test_lookahead_uses_normal_table(self):
        hard = Scorer(self.db, State(hero="邦妮", star=1, map="魔法学院"))
        normal = Scorer(self.db, State(hero="邦妮", star=1, map="魔法学院", difficulty=NORMAL))
        # 前瞻看的是下一次三选一（2 星）：普通难度金色 20%，困难及以上 15%
        for scorer, gold in ((hard, 0.15), (normal, 0.20)):
            _, ((_, _, general),), _ = future_setup(scorer, (), "魔法学院")
            self.assertAlmostEqual(sum(p for n, p in general.items() if self.db.chips[n].rarity == "金"), gold)
        self.assertNotAlmostEqual(hard.lookahead("标靶", ()), normal.lookahead("标靶", ()), places=3)

    def test_endless_cycle_uses_normal_table(self):
        def part(**kw):
            st = State(hero="邦妮", star=1, round=2, rerolls=3, map="魔法学院", **kw)
            return next(p for p in Scorer(self.db, st).parts("循环往复", (), 2) if p.label == "循环往复")

        self.assertNotAlmostEqual(part().score, part(difficulty=NORMAL).score, places=3)

    def test_progress_cap_normal_column(self):
        def picks(**kw):
            return Scorer(self.db, State(hero="邦妮", star=0, round=14, **kw)).picks

        self.assertEqual(picks(map="星趴·梦想号").cap, 18)  # 困难及以上：疯狂一列
        p = picks(map="星趴·梦想号", difficulty=NORMAL)
        self.assertEqual((p.cap, p.rounds_left), (20, 6))
        self.assertEqual(picks(map="幽魂暗巷", difficulty=NORMAL).cap, 18)  # 困难、噩梦没有数据，普通一列照常用
        # 地图待识别：取候选地图疯狂一列的常见值（候选三张：星趴·梦想号 18、御魂庆典 19、园林中庭 17，取较小的 17）
        cands = ("星趴·梦想号", "御魂庆典", "园林中庭")
        self.assertEqual(picks(map_candidates=cands, difficulty=NORMAL).cap, 20)
        self.assertEqual(picks(map_candidates=cands).cap, 17)
        adv = self.run_case("邦妮", self.OFFER, round=5, difficulty=NORMAL, map_candidates=cands)
        self.assertIn("地图：待识别，按候选地图平均计算（进度上限取常见值 20）", adv.notes)

    def test_cli_shows_normal_difficulty(self):
        args = ["--hero", "凛", "--star", "1", "--round", "4", "--offer", "标靶", "标记喷罐", "鹰眼瞄具"]
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*args, "--difficulty", "normal"])
        text = out.getvalue()
        self.assertIn("｜地图：未指定｜难度：普通｜", text)
        self.assertIn(f"⚠ {NORMAL_NOTE}", text)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(args)
        self.assertNotIn("难度：", out.getvalue())  # 困难及以上不显示
        self.assertNotIn(NORMAL_NOTE, out.getvalue())


class SupportFlyingStarTest(Base):
    """辅助角色的金色飞星与星币锤，以及芬妮的玩家思路。

    大部分辅助角色不能和首领硬碰硬，金色飞星优于星币锤；照可以直接攻击怪物，但不适合星光流派，不在此列。
    职业权重表里辅助高于 15 的钱辅、奶辅、功能辅带「辅助定位」特性（照和法术输出的凛、梅加斯、赛克斯不带）：
    金色飞星 +60、紫色飞星 +50（与「放过怪物」相同），星币锤 -20；另有组合「飞星叠加」：已持有一张飞星时另一张 +30
    （所有角色）。手电筒-强光 -25：两项效果都要攻击怪物才触发，与星币锤同理；「不靠战斗」给它的扣分是同一个原因，
    两个特性都有的角色不重复扣。梅加斯不带「辅助定位」，星币锤的扣分写在她的角色专属规则里。
    芬妮的默认推荐档位（必拿银行卡-用不完、紫色飞星、金色飞星）用测试用的那一份。
    阿尔的星光常是全队最多，金色飞星放过首领时按星光层数加伤，另有角色专属规则加分，三种局面也都排在星币锤前面。
    """

    SUPPORT = ("芬妮", "蓝海晴", "超天酱", "米米", "阿尔", "吉尔", "帕露南", "恋", "远野汉娜", "潘大猛")
    OFFER = ["星币锤", "书签", "金色飞星"]
    # 10-02 第一局真人对局第 10 轮芬妮的已有筹码（推荐日志 offer_id 20261002-215028-10）
    LATE = ("“8面”骰子", "紫色飞星", "银行卡-用不完", "ATM机", "银行卡-余额多", "医疗箱-紧急治疗")
    CASES = {"1 星第 3 轮": dict(star=1, round=3), "3 星第 10 轮": dict(star=3, round=10, owned=LATE),
             "3 星第 13 轮": dict(star=3, round=13)}

    def test_trait_holders(self):
        holders = {h for h, hs in self.db.hero_synergy.items() if "辅助定位" in hs.traits}
        self.assertEqual(holders, set(self.SUPPORT))
        by_weight = {n for n, h in self.db.heroes.items() if h.weights["辅助"] > 15}
        self.assertEqual(by_weight - holders, {"照", "凛", "梅加斯", "赛克斯"})
        self.assertEqual(holders - by_weight, set())
        # 特性和分组对得上：带「辅助定位」的都在钱辅、奶辅、功能辅里；法术C 的梅加斯、赛克斯不带
        self.assertEqual({self.db.heroes[h].group for h in holders}, {"钱辅", "奶辅", "功能辅"})
        self.assertEqual({self.db.heroes[h].group for h in ("梅加斯", "赛克斯")}, {"法术C"})

    def test_trait_rules(self):
        rules = {c: r.score for r in self.db.trait_rules["辅助定位"] for c in r.chips}
        # 攻略视频的结论：辅助也要移速（三种速度轮滑 +15，帕露南不加），转账类 ATM机 +30、友情徽章 +20
        self.assertEqual(rules, {"金色飞星": 60, "紫色飞星": 50, "星币锤": -20, "手电筒-强光": -25,
                                 "速度轮滑-初级": 15, "速度轮滑-中级": 15, "速度轮滑-高级": 15, "ATM机": 30, "友情徽章": 20})
        shoes = next(r for r in self.db.trait_rules["辅助定位"] if "速度轮滑-初级" in r.chips)
        self.assertEqual(shoes.exclude, {"帕露南"})
        # 「放过怪物」（只有姬梦枫带）的两张飞星比这里低：她放过怪物主要为了叠养精蓄锐，飞星的伤害只有 1～2 点，
        # 还会把星光占成第二个流派
        pass_by = {c: r.score for r in self.db.trait_rules["放过怪物"] for c in r.chips}
        self.assertEqual(pass_by, {"金色飞星": 35, "紫色飞星": 20})
        self.assertEqual({h for h, hs in self.db.hero_synergy.items() if "放过怪物" in hs.traits}, {"姬梦枫"})
        # 推荐卡片的理由标签写规则的短句（short）：筹码在该角色身上起作用的要点，不写特性名，不超过 engine.SHORT_LIMIT 个字
        part = next(p for p in Scorer(self.db, State(hero="蓝海晴")).parts("金色飞星", (), None) if p.origin == "辅助定位")
        rule = next(r for r in self.db.trait_rules["辅助定位"] if "金色飞星" in r.chips)
        self.assertEqual(part.short, rule.short)
        self.assertEqual(part.short, "无需战斗即可伤害首领")
        self.assertLessEqual(len(part.short), SHORT_LIMIT)
        self.assertNotIn("辅助定位", part.short)
        self.assertIsNone(BANNED_WORDING.search(part.reason))
        self.assertIsNone(BANNED_WORDING.search(part.short))

    def test_auto_layer_parts(self):
        # 只看自动层：10 个角色的金色飞星最高一条都是「辅助定位」+60（阿尔另有角色专属规则 +65，最高一条是它，
        # 「辅助定位」按三成叠加；潘大猛还没有星光的筹码时另有角色专属规则 -15，星光不是他的推荐流派），星币锤都有 -20 一项
        for hero in self.SUPPORT:
            scorer = Scorer(self.db, State(hero=hero), use_player=False)
            gold = [p for p in scorer.parts("金色飞星", (), None) if p.label in ("特性", "角色专属规则")]
            hammer = [p for p in scorer.parts("星币锤", (), None) if p.origin == "辅助定位"]
            with self.subTest(hero=hero):
                top = {"阿尔": [("角色", 65)], "潘大猛": [("辅助定位", 60), ("角色", -15)]}.get(hero, [("辅助定位", 60)])
                self.assertEqual([(p.origin, p.score) for p in gold], top)
                self.assertEqual([p.score for p in hammer], [-20])
        # 梅加斯不带「辅助定位」：星币锤照旧 -20（她的角色专属规则，同一句理由），飞星不再按「辅助定位」加分
        parts = Scorer(self.db, State(hero="梅加斯")).parts("星币锤", (), None)
        rule = next(r for r in self.db.trait_rules["辅助定位"] if "星币锤" in r.chips)
        self.assertEqual([(p.label, p.score, p.reason, p.short) for p in parts if p.score < 0 and p.label != "收入折算"],
                         [("角色专属规则", -20, rule.reason, rule.short)])
        for hero in ("梅加斯", "赛克斯"):
            with self.subTest(hero=hero):
                gold = Scorer(self.db, State(hero=hero)).parts("金色飞星", (), None)
                self.assertFalse(any(p.origin == "辅助定位" for p in gold))

    def test_torch_needs_attacks(self):
        # 手电筒-强光：攻击怪物时按星光层数提高攻击力、击中怪物后获得星光，两项都要攻击怪物才触发。
        # 只看自动层：10 个辅助角色都有且只有一条「辅助定位」-25，扣分单列，不影响取最高一条加分
        for hero in self.SUPPORT:
            parts = Scorer(self.db, State(hero=hero), use_player=False).parts("手电筒-强光", (), None)
            torch = [p for p in parts if p.origin == "辅助定位"]
            with self.subTest(hero=hero):
                self.assertEqual([(p.label, p.score) for p in torch], [("特性", -25)])
                self.assertEqual(torch[0].short, "较少挑战怪物，两项效果难触发")
                self.assertLessEqual(len(torch[0].short), SHORT_LIMIT)
                self.assertNotIn("辅助定位", torch[0].reason + torch[0].short)
                self.assertIsNone(BANNED_WORDING.search(torch[0].reason))
        # 照、凛、梅加斯、赛克斯不带这个特性
        for hero in ("照", "凛", "梅加斯", "赛克斯"):
            for use_player in (True, False):
                parts = Scorer(self.db, State(hero=hero), use_player=use_player).parts("手电筒-强光", (), None)
                with self.subTest(hero=hero, use_player=use_player):
                    self.assertFalse(any(p.origin == "辅助定位" for p in parts))
        # 默认推荐没有列入手电筒-强光时按角色特性计算，同样扣分（测试用的默认推荐档位里帕露南没列它，米米、恋没有档位）
        db = with_player_layer(self.db)
        for hero in ("帕露南", "米米", "恋"):
            parts = Scorer(db, State(hero=hero)).parts("手电筒-强光", (), None)
            with self.subTest(hero=hero):
                self.assertNotIn("手电筒-强光", getattr(db.player_synergy.get(hero), "tiers", {}))
                self.assertEqual([p.score for p in parts if p.origin == "辅助定位"], [-25])
        # 「不靠战斗」也给手电筒-强光扣分（攻击力加成的收益低），和这一条是同一个原因：两个特性都有的角色只扣这一条，
        # 只带「不靠战斗」的角色（小町、姬梦朝、梅加斯、赛克斯）扣那一条，不重复扣
        both = set()
        for hero, hs in self.db.hero_synergy.items():
            parts = Scorer(self.db, State(hero=hero), use_player=False).parts("手电筒-强光", (), None)
            fewer = [p.origin for p in parts if p.score < 0 and (p.short or "").startswith("较少挑战怪物")]
            expect = ["辅助定位"] if "辅助定位" in hs.traits else ["不靠战斗"] if "不靠战斗" in hs.traits else []
            if {"辅助定位", "不靠战斗"} <= set(hs.traits):
                both.add(hero)
            with self.subTest(hero=hero):
                self.assertEqual(fewer, expect)
        self.assertEqual(len(both), 9)
        for hero in ("小町", "姬梦朝", "梅加斯", "赛克斯"):
            parts = Scorer(self.db, State(hero=hero), use_player=False).parts("手电筒-强光", (), None)
            with self.subTest(hero=hero):
                self.assertEqual([(p.origin, p.score) for p in parts if p.score < 0], [("不靠战斗", -25)])

    def test_gold_star_over_hammer(self):
        # 三种局面下，10 个辅助角色的金色飞星总分都高于星币锤（阿尔见下一条）。
        for hero in self.SUPPORT:
            for case, kw in self.CASES.items():
                ev = self.by_name(self.run_case(hero, self.OFFER, **kw))
                with self.subTest(hero=hero, case=case):
                    self.assertGreater(ev["金色飞星"].total, ev["星币锤"].total)

    def test_al_hammer_later(self):
        # 星币锤的适配角色只有阿尔（角色分 +50），星光层数又直接换成他的属性（「星光成长」给星币锤 +40）；但他的星光常是
        # 全队最多，金色飞星放过首领时按星光层数加伤，角色专属规则另 +65：第 3 轮和第 13 轮都是金色飞星在前，
        # 第 13 轮没有成长分，差距缩小（约 105 对 91）
        early = self.by_name(self.run_case("阿尔", self.OFFER, **self.CASES["1 星第 3 轮"]))
        self.assertGreater(early["金色飞星"].total, early["星币锤"].total)
        late = self.run_case("阿尔", self.OFFER, **self.CASES["3 星第 13 轮"])
        ev = self.by_name(late)
        self.assertGreater(ev["金色飞星"].total, ev["星币锤"].total)
        self.assertLess(ev["金色飞星"].total - ev["星币锤"].total, early["金色飞星"].total - early["星币锤"].total)
        self.assertEqual(late.best, "金色飞星")

    def test_fenny_real_game(self):
        # 真实对局里的局面：异变图书馆、3 星第 10 轮、已持有紫色飞星，三名真人队友；首推是金色飞星，不是星币锤
        kw = dict(star=3, round=10, map="异变图书馆", owned=self.LATE, source="levelup", rerolls=2,
                  probabilities=(25, 50, 25), left_rarity="金",
                  teammates=(Teammate("帕露南"), Teammate("Z3000"), Teammate("凛")))
        adv = self.run_case("芬妮", self.OFFER, **kw)
        self.assertEqual(adv.best, "金色飞星")
        gold = self.by_name(adv)["金色飞星"]
        self.assertEqual({p.label: p for p in gold.parts}["组合"].origin, "金色飞星与紫色飞星")
        # 测试用的默认推荐档位把金色飞星列为必选时同样首推它，必选那一项按必选档计
        adv = self.run_case("芬妮", self.OFFER, db=with_player_layer(self.db), **kw)
        self.assertEqual(adv.best, "金色飞星")
        labels = {p.label: p for p in self.by_name(adv)["金色飞星"].parts}
        self.assertEqual(labels["玩家思路"].score, 110)  # 必拿
        self.assertEqual(labels["组合"].origin, "金色飞星与紫色飞星")

    def test_fenny_player_tiers(self):
        ps = with_player_layer(self.db).player_synergy["芬妮"]
        self.assertEqual(ps.tiers, {"银行卡-用不完": "core", "紫色飞星": "core", "金色飞星": "core"})
        self.assertEqual(ps.sources, (SOURCE_GUIDE,))
        self.assertEqual(ps.schools, ("星光",))

    def test_zhao_and_rin_unaffected(self):
        # 照、凛的辅助权重也高于 15，但不带「辅助定位」：照可直接攻击怪物且不适合星光流派，凛是法术输出。
        # 飞星叠加的组合规则对所有角色生效，照、凛已持有一张飞星时另一张同样 +30。
        for hero in ("照", "凛"):
            self.assertNotIn("辅助定位", self.db.hero_synergy[hero].traits)
            for use_player in (True, False):
                scorer = Scorer(self.db, State(hero=hero), use_player=use_player)
                for chip in ("金色飞星", "紫色飞星", "星币锤"):
                    with self.subTest(hero=hero, use_player=use_player, chip=chip):
                        self.assertFalse(any("辅助定位" in p.origin for p in scorer.parts(chip, (), None)))
        # 测试用的默认推荐档位里凛的星光流派扣分照旧
        db = with_player_layer(self.db)
        part = next(p for p in Scorer(db, State(hero="凛")).parts("金色飞星", (), None) if p.label == "玩家思路")
        self.assertEqual(part.score, -40)

    def test_flying_star_combo(self):
        def combos(hero, chip, owned):
            owned = tuple(owned)
            return [(p.origin, p.score) for p in Scorer(self.db, State(hero=hero, owned=owned)).parts(chip, owned, None)
                    if p.label == "组合"]

        for hero in ("美咲", "蓝海晴", "芬妮", "照"):  # 物理输出、辅助、钱辅、照
            with self.subTest(hero=hero):
                self.assertEqual(combos(hero, "金色飞星", ["紫色飞星"]), [("金色飞星与紫色飞星", 30)])
                self.assertEqual(combos(hero, "紫色飞星", ["金色飞星"]), [("紫色飞星与金色飞星", 30)])
                self.assertEqual(combos(hero, "金色飞星", []), [])
                self.assertEqual(combos(hero, "紫色飞星", ["星币锤"]), [])
        reason = next(c.reason for c in self.db.combos if c.name == "金色飞星与紫色飞星")
        self.assertTrue(reason.startswith("已持有「紫色飞星」："))
        # 组合分计入总分：已持有紫色飞星时金色飞星高出 30（扣去按比例折算的星光收入）
        st = dict(star=1, round=3)
        plain = self.by_name(self.run_case("蓝海晴", self.OFFER, **st))["金色飞星"]
        paired = self.by_name(self.run_case("蓝海晴", self.OFFER, owned=("紫色飞星",), **st))["金色飞星"]
        self.assertGreater(paired.immediate, plain.immediate)


class AlLiveReviewTest(Base):
    """10-03 真人对局（异变图书馆、阿尔，三名真人队友）复盘后的推荐规则。

    OFFERS 是这一局 11 次三选一的局面，取自推荐日志（键为 offer_id「20261003-HHMMSS-N」去掉日期的部分），
    每项为（已有筹码个数，State 的其余字段，三个候选筹码）；已有筹码按 GOT 的获得顺序取前几个，PICKS 是对局中实际的选择，
    LIVE_BEST 是对局当时推荐日志里的首推。
    对局中没有识别到难度，局面里不写难度；第 1 次三选一时地图还没有识别出来（map=None）。
    后续用 state()、advise_at() 复用这些局面：关键字参数覆盖局面里的字段（例如按噩梦难度重算时传入难度字段）。
    """

    MATES = (Teammate("帕露南"), Teammate("茉莉"), Teammate("邦妮"))
    GOT = ("“8面”骰子", "ATM机", "摩托头盔-高级", "友情徽章", "银行卡-用不完", "星币锤", "银行卡-余额多", "金色飞星")
    OFFERS = {
        "124947-1": (0, dict(star=1, round=2, map=None, source="levelup", rerolls=3, probabilities=(60, 37, 3),
                             left_rarity="蓝", progress=2),
                     ("“8面”骰子", "循环往复", "摩托头盔-一般")),
        "125351-2": (1, dict(star=1, round=3, source="task", tier=1, rerolls=3, probabilities=(60, 37, 3),
                             left_rarity="蓝", progress=3),
                     ("小猪存钱罐", "手电筒-强光", "ATM机")),
        "130113-3": (2, dict(star=1, round=5, source="task", tier=2, rerolls=3, probabilities=(40, 45, 15),
                             left_rarity="紫", progress=5),
                     ("额外电池", "摩托头盔-高级", "速度轮滑-中级")),
        "130412-4": (3, dict(star=2, round=7, source="levelup", rerolls=4, probabilities=(40, 45, 15),
                             left_rarity="紫", progress=7),
                     ("友情徽章", "魔法秘典", "手电筒-强光")),
        "130849-5": (4, dict(star=2, round=7, source="task", tier=2, rerolls=4, probabilities=(40, 45, 15),
                             left_rarity="蓝", progress=7),
                     ("摩托头盔-一般", "银行卡-余额少", "夹心饼干-一般")),
        "130853-6": (4, dict(star=2, round=7, source="task", tier=2, rerolls=3, probabilities=(40, 45, 15),
                             left_rarity="金", progress=7),
                     ("银行卡-用不完", "银行卡-余额多", "小猪存钱罐")),
        "131242-7": (5, dict(star=3, round=9, source="levelup", rerolls=4, probabilities=(25, 50, 25),
                             left_rarity="金", progress=10),
                     ("星币锤", "速度轮滑-初级", "夹心饼干-可口")),
        "131934-8": (6, dict(star=3, round=10, source="task", tier=3, rerolls=4, probabilities=(25, 50, 25),
                             left_rarity="蓝", progress_cap=17, progress=11),
                     ("医疗箱-紧急治疗", "维生素药丸", "银行卡-余额多")),
        "131952-9": (7, dict(star=3, round=11, source="levelup", rerolls=4, probabilities=(25, 50, 25),
                             left_rarity="金", progress_cap=17, progress=11),
                     ("探天卫星", "摩托头盔-中级", "书签")),
        "131956-10": (7, dict(star=3, round=11, source="levelup", rerolls=3, probabilities=(25, 50, 25),
                              left_rarity="金", progress_cap=17, progress=11),
                      ("金色飞星", "额外电池", "速度轮滑-初级")),
        "132328-11": (8, dict(star=3, round=12, source="levelup", rerolls=3, probabilities=(25, 50, 25),
                              left_rarity="金", progress_cap=17, progress=12),
                      ("汉娜人偶", "大背包", "缓冲盾牌")),
    }
    PICKS = {
        "124947-1": "“8面”骰子", "125351-2": "ATM机", "130113-3": "摩托头盔-高级", "130412-4": "友情徽章",
        "130849-5": REROLL, "130853-6": "银行卡-用不完", "131242-7": "星币锤", "131934-8": "银行卡-余额多",
        "131952-9": REROLL, "131956-10": "金色飞星", "132328-11": "缓冲盾牌",
    }
    LIVE_BEST = {
        "124947-1": "“8面”骰子", "125351-2": "ATM机", "130113-3": "额外电池", "130412-4": "手电筒-强光",
        "130849-5": "银行卡-余额少", "130853-6": "银行卡-用不完", "131242-7": "星币锤", "131934-8": "银行卡-余额多",
        "131952-9": REROLL, "131956-10": "金色飞星", "132328-11": "缓冲盾牌",
    }

    def al_state(self, **kw) -> State:
        """阿尔、异变图书馆、三名真人队友的状态；关键字参数覆盖这几项和其余字段。"""
        return State(**{"hero": "阿尔", "map": "异变图书馆", "teammates": self.MATES, **kw})

    def state(self, key: str, **kw) -> State:
        """OFFERS[key] 那次三选一的状态（key 例如 "130113-3"）；关键字参数覆盖局面里的字段。"""
        count, fields, _ = self.OFFERS[key]
        return self.al_state(**{"owned": self.GOT[:count], **fields, **kw})

    def advise_at(self, key: str, **kw):
        """按 OFFERS[key] 的局面重算这次三选一的推荐。"""
        return advise(self.db, self.state(key, **kw), list(self.OFFERS[key][2]))

    @staticmethod
    def ranking(adv) -> list[str]:
        """按推荐度从高到低排列的选项名（含刷新，不含被上位替代的筹码）。"""
        options = [(e.percent, e.chip.name) for e in adv.evals if not e.superseded_by]
        if adv.reroll is not None:
            options.append((adv.reroll.percent, REROLL))
        return [name for _, name in sorted(options, key=lambda o: -o[0])]

    def test_offers_follow_picks(self):
        # 局面表自洽：选了筹码时下一次的已有筹码多出这一个；选刷新时已有筹码不变，剩余刷新少 1 次
        keys = list(self.OFFERS)
        for prev, nxt in zip(keys, keys[1:]):
            pick, before, after = self.PICKS[prev], self.state(prev), self.state(nxt)
            with self.subTest(offer=prev, pick=pick):
                self.assertIn(pick, (*self.OFFERS[prev][2], REROLL))
                if pick == REROLL:
                    self.assertEqual(after.owned, before.owned)
                    self.assertEqual(after.rerolls, before.rerolls - 1)
                else:
                    self.assertEqual(after.owned, (*before.owned, pick))
        self.assertIsNone(self.state("124947-1").map)
        self.assertEqual(self.state("130113-3", map="园林中庭").map, "园林中庭")

    def test_al_battery(self):
        # 阿尔的主动技能把手牌给予其他角色，冷却缩短后手牌常常不足：额外电池 -30（「技能依赖」+45，实得 +15）。
        # 130113-3 实际选的是摩托头盔-高级，额外电池不该排第一；额外电池仍排在速度轮滑-中级前面
        adv = self.advise_at("130113-3")
        ev = self.by_name(adv)
        battery = ev["额外电池"]
        rule = [p for p in battery.parts if p.label == "角色专属规则"]
        self.assertEqual([(p.score, p.short) for p in rule], [(-30, "手牌常不足，缩短冷却收益低")])
        self.assertLessEqual(len(rule[0].short), SHORT_LIMIT)
        self.assertIsNone(BANNED_WORDING.search(rule[0].reason))
        self.assertGreater(battery.total, ev["速度轮滑-中级"].total)
        self.assertLess(battery.total, adv.reroll.value)
        self.assertNotEqual(adv.best, "额外电池")
        # 只对阿尔：凛的额外电池没有这一项
        for use_player in (True, False):
            parts = Scorer(self.db, State(hero="凛"), use_player=use_player).parts("额外电池", (), None)
            with self.subTest(use_player=use_player):
                self.assertFalse(any(p.label == "角色专属规则" and p.score == -30 for p in parts))

    def test_torch_not_first(self):
        # 手电筒-强光对较少挑战怪物的角色 -25（两项效果都要攻击怪物才触发）。
        # 130412-4 原来首推手电筒-强光，实际选的是友情徽章；125351-2 实际选的 ATM机 仍是首推
        adv = self.advise_at("130412-4")
        ev = self.by_name(adv)
        self.assertNotEqual(adv.best, "手电筒-强光")
        self.assertLess(ev["手电筒-强光"].total, adv.reroll.value)
        self.assertEqual(self.advise_at("125351-2").best, "ATM机")

    def test_friend_badge_after_atm(self):
        # 已持有 ATM机：友情徽章同样在转账时触发，组合「转账筹码叠加」+25，排到手电筒-强光前面
        ev = self.by_name(self.advise_at("130412-4"))
        badge = ev["友情徽章"]
        self.assertIn(("转账筹码叠加", 25), [(p.origin, p.score) for p in badge.parts if p.label == "组合"])
        self.assertGreater(badge.total, ev["手电筒-强光"].total)

    def test_al_vitamin(self):
        # 阿尔给予手牌、自身额外获得的卡牌不多：维生素药丸 -40（「抽牌多」+55，实得 +15）。131934-8 原来排第二，
        # 现在排在刷新后面
        adv = self.advise_at("131934-8")
        ev = self.by_name(adv)
        vitamin = ev["维生素药丸"]
        rule = [p for p in vitamin.parts if p.label == "角色专属规则"]
        self.assertEqual([(p.score, p.short) for p in rule], [(-40, "自身获得卡牌不多，治愈收益低")])
        self.assertLessEqual(len(rule[0].short), SHORT_LIMIT)
        self.assertIsNone(BANNED_WORDING.search(rule[0].reason))
        self.assertLess(vitamin.total, adv.reroll.value)
        self.assertEqual(adv.best, "银行卡-余额多")
        # 只对阿尔：同样带「抽牌多」的吉尔、梅加斯照旧 +55
        for hero in ("吉尔", "梅加斯"):
            parts = Scorer(self.db, State(hero=hero), use_player=False).parts("维生素药丸", (), None)
            with self.subTest(hero=hero):
                self.assertIn(("特性", "抽牌多", 55), [(p.label, p.origin, p.score) for p in parts])
                self.assertFalse(any(p.label == "角色专属规则" for p in parts))

    def test_bank_card_team(self):
        # 银行卡-用不完按真人队友人数加分：三名真人队友 +15，加上茉莉升星加攻的 +5，共 +20。这一局实际选的就是用不完
        adv = self.advise_at("130853-6")
        card = self.by_name(adv)["银行卡-用不完"]
        self.assertEqual(adv.best, "银行卡-用不完")
        self.assertEqual(sum(p.score for p in card.parts if p.label == "队友"), 20)
        self.assertTrue(all(p.short.startswith("配合队友：") for p in card.parts if p.label == "队友"))

    def test_nightmare_helmet(self):
        # 噩梦及以上难度时摩托头盔-高级对所有角色提高。
        # 这一局期望的排序是金头盔 ＞ 额外电池 ＞ 速度轮滑；按噩梦重算为 金头盔 41% ＞ 刷新 29% ＞ 额外电池 17% ＞ 速度轮滑 14%
        # （辅助角色的速度轮滑 +15），排序和期望的一样
        adv = self.advise_at("130113-3", difficulty_level=LEVEL_NIGHTMARE)
        ev = self.by_name(adv)
        self.assertEqual(adv.best, "摩托头盔-高级")
        for got, want in ((ev["摩托头盔-高级"].percent, 0.41), (adv.reroll.percent, 0.29),
                          (ev["额外电池"].percent, 0.17), (ev["速度轮滑-中级"].percent, 0.14)):
            self.assertAlmostEqual(got, want, delta=0.02)
        self.assertGreater(ev["额外电池"].percent, ev["速度轮滑-中级"].percent)
        # 困难和读不到难度时维持现状：金头盔不是首推
        self.assertNotEqual(self.advise_at("130113-3").best, "摩托头盔-高级")

    def test_nightmare_recompute(self):
        # 这一局是噩梦难度。按噩梦及以上重算 11 次三选一，和对局当时的首推相比变三次：130113-3 改为摩托头盔-高级
        # （噩梦金头盔、阿尔额外电池调低）；130412-4 改为刷新，实际选的友情徽章第二（手电筒-强光扣分、转账筹码叠加、辅助
        # 角色的友情徽章 +20；刷新后最左格按职业权重出，阿尔职业分值高的辅助池里有银行卡、紫色飞星等，刷新的估值高）；
        # 最后一次 132328-11 改为刷新，实际选的缓冲盾牌第二（已持有友情徽章，刷新后的大碗炖肉另有组合 +15）；其余 8 次不变。
        # 130849-5 剩 4 次刷新、之后约 3 次三选一：每次留 1 次、再多留 1 次以防估少，4 次都留着，没有多出来的，照旧首推
        # 银行卡-余额少（实际选了刷新：这一局之后还有 4 次三选一，估的 3 次少了 1 次）
        changed = {}
        for key in self.OFFERS:
            night = self.advise_at(key, difficulty_level=LEVEL_NIGHTMARE)
            with self.subTest(offer=key):
                self.assertEqual(self.ranking(night)[0], night.best)  # 首推就是推荐度最高的选项
                if key != "130113-3":
                    # 噩梦只提高摩托头盔-高级：别的局面和读不到难度时首推相同
                    self.assertEqual(night.best, self.advise_at(key).best)
            if night.best != self.LIVE_BEST[key]:
                changed[key] = night.best
        self.assertEqual(changed, {"130113-3": "摩托头盔-高级", "130412-4": REROLL, "132328-11": REROLL})
        self.assertEqual(self.PICKS["130849-5"], REROLL)
        later = self.advise_at("130849-5", difficulty_level=LEVEL_NIGHTMARE)
        self.assertEqual((later.reroll.kept, later.reroll.spare, later.reroll.need), (4, 0, 3))
        self.assertGreater(later.reroll.cost, 0)
        self.assertEqual(self.ranking(self.advise_at("130412-4", difficulty_level=LEVEL_NIGHTMARE))[:2],
                         [REROLL, "友情徽章"])
        self.assertEqual(self.ranking(self.advise_at("132328-11", difficulty_level=LEVEL_NIGHTMARE))[:2],
                         [REROLL, "缓冲盾牌"])


class FennyLiveReviewTest(PlayerBase):
    """10-04 真人对局（芬妮、异变图书馆、噩梦，三名真人队友）复盘后定的推荐规则：
    前瞻按预计剩余三选一次数封顶；同一效果一正一负的两条理由合成一条；已持有金色飞星时星光收入的折算减半；
    必拿筹码的收入折算不扣必拿加分、悬停写玩家思路原文。局面取自这一局的推荐日志（offer_id 去掉日期的部分）。
    芬妮当时的默认推荐有档位（必拿银行卡-用不完、紫色飞星、金色飞星），这里用测试用的默认推荐档位。"""

    MATES = (Teammate("姬梦朝"), Teammate("照"), Teammate("邦妮"))
    BASE = dict(hero="芬妮", map="异变图书馆", teammates=MATES, probabilities=(25, 50, 25),
                difficulty_level=LEVEL_NIGHTMARE, difficulty_name="噩梦")
    GOT = ("银行卡-余额少", "缓冲盾牌", "银行卡-用不完", "银行卡-余额多", "紫色飞星", "摩托头盔-高级", "金色飞星")
    OFFERS = {
        "135348-7": (6, dict(star=3, round=9, source="levelup", rerolls=5, left_rarity="金", progress=9),
                     ["星币锤", "维生素药丸", "摩托头盔-中级"]),
        "140011-11": (7, dict(star=3, round=10, source="task", tier=3, rerolls=2, left_rarity="紫", progress=10),
                      ["智能手表", "友情徽章", "医疗箱-完备治疗"]),
        "140015-12": (7, dict(star=3, round=10, source="task", tier=3, rerolls=1, left_rarity="紫", progress=9),
                      ["大背包", "星币锤", "医疗箱-紧急治疗"]),
    }

    def at(self, key, **kw):
        count, fields, offer = self.OFFERS[key]
        st = State(**{**self.BASE, "owned": self.GOT[:count], **fields, **kw})
        return advise(self.db, st, offer)

    def test_lookahead_capped_by_picks_left(self):
        # 第 12 次是这一局最后一次三选一，读到的是第 10 轮、进度第 9 格（噩梦，进度上限 17）：3 星以后、还没到进度上限，
        # 之后的三选一按下限算（late_rate ×（H + 0.5），H = (max(0.5 × 16, 10) + 17) ÷ 2 − 10 = 3.5），前瞻照常算
        adv = self.at("140015-12")
        self.assertAlmostEqual(adv.picks.total, self.db.rules["picks"]["late_rate"] * (3.5 + 0.5))
        self.assertTrue(any(e.lookahead for e in adv.evals))
        # 进度读成走到头（剩余轮数 0）时预计之后没有三选一：前瞻记 0，卡片和悬停明细都不写前瞻，和「之后无需保留刷新」一致。
        # 星币锤不写「前瞻：医疗箱-完备治疗 10%→11%」这类
        from advisor.ui import text as ui_text
        adv = self.at("140015-12", progress=17)
        self.assertEqual(adv.picks.total, 0)
        for e in adv.evals:
            with self.subTest(chip=e.chip.name):
                self.assertEqual((e.lookahead, e.lookahead_reason, e.lookahead_short), (0, "", ""))
                self.assertFalse(any(p.text.startswith("前瞻") for p in ui_text.option_pills(e, True)))
        reroll = ui_text.reroll_pills(adv.reroll, ui_text.best_chip(adv), 1)
        self.assertEqual(reroll[-1].text, "之后无需保留刷新")
        # 影响的选择次数 = min(按星数, 预计剩余三选一次数)（持有的筹码数不设上限，不按空着的格数封顶）：剩余不到 1 次时
        # 按剩余次数折算
        sc = Scorer(self.db, State(hero="芬妮", star=3, round=11, map="异变图书馆", owned=self.GOT[:5], source="task"))
        self.assertGreater(sc.picks.total, 0)
        self.assertLess(sc.picks.total, self.db.rules["lookahead"]["picks"][3])
        self.assertEqual(sc.lookahead_times(), sc.picks.total)
        m = sc.maps[0]
        name = "星币锤"
        delta = sc.future((*sc.known_owned, name), map_name=m) - sc.future(sc.known_owned, zero=name, map_name=m)
        self.assertAlmostEqual(lookahead_on(sc, name, sc.known_owned, m), sc.picks.total * delta)

    def test_net_effect_hammer(self):
        # 星币锤对芬妮：「经济」+40「获得星光，星币多时提高攻击力」和「辅助定位」-20「较少挑战怪物，攻击力加成难触发」
        # 说的是同一个效果（攻击时花费星币增加攻击力），卡片上合成「获得星光（攻击加成难触发）」，悬停写两句整句
        from advisor.ui import text as ui_text
        for key in ("135348-7", "140015-12"):
            adv = self.at(key)
            hammer = self.by_name(adv)["星币锤"]
            paired = [p for p in hammer.parts if p.net]
            with self.subTest(offer=key):
                self.assertEqual([(p.origin, p.score) for p in paired], [("经济", 40), ("辅助定位", -20)])
                self.assertEqual({p.net for p in paired}, {"获得星光（攻击加成难触发）"})
                pills = ui_text.option_pills(hammer, hammer.chip.name == adv.best)
                merged = [p for p in pills if p.text == "获得星光（攻击加成难触发）"]
                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0].tone, ui_text.PLUS)  # +40 − 20 = +20
                self.assertEqual(merged[0].tip, f"{paired[0].reason}。{paired[1].reason}。")
                texts = [p.text for p in pills]
                self.assertNotIn("获得星光，星币多时提高攻击力", texts)
                self.assertNotIn("较少挑战怪物，攻击力加成难触发", texts)
                # 筹码名按钮的悬停明细里仍是两条
                rows = [r[1] for r in ui_text.breakdown_rows(hammer)]
                self.assertIn(paired[0].reason, rows)
                self.assertIn(paired[1].reason, rows)
        # 第 7 次星币锤不是首推（只显示一条理由）：合成的那条照样在卡片上（在 +40 那条的位置上）
        adv = self.at("135348-7")
        self.assertNotEqual(adv.best, "星币锤")
        pills = ui_text.option_pills(self.by_name(adv)["星币锤"], False)
        self.assertEqual(pills[0].text, "获得星光（攻击加成难触发）")

    # 物理输出的角色：孔雀扇（糖糖不扣温焰的伤害，摩西最高一条是「反击」）、没有充能来源时的电磁炮（星魅琉华不扣；
    # 测试用的默认推荐档位列了电磁炮的照、橘雪莉按档位算）
    ATTACKERS = {"Z3000", "南希露", "墨影", "多萝西", "姬梦枫", "娜蒂斯", "帕帕拉", "星魅琉华", "橘雪莉", "派德曼", "照", "玲玲",
                 "真梦梓", "美咲", "茉莉", "邦妮", "阿兰娜", "摩西", "糖糖"}
    NET_MERGES = {  # 玩家思路照常生效时，全部角色、全部筹码里会合成一条的组合：（筹码, 净效果的短句）→ 角色
        ("星币锤", "获得星光（攻击加成难触发）"): {"芬妮", "蓝海晴", "超天酱", "远野汉娜", "帕露南", "米米", "恋", "吉尔"},
        ("手电筒-强光", "较少挑战怪物，两项效果难触发"): {"阿尔"},
        ("美工刀-初级", "满血时加攻（与半血有冲突）"): {"帕帕拉"},
        ("美工刀-锋利", "满血时加攻（与半血有冲突）"): {"帕帕拉"},
        ("额外电池", "技能冷却缩短（手牌常不足）"): {"阿尔"},
        ("额外电池", "技能冷却缩短（多用 1～2 次）"): {"凛"},
        ("额外电池", "技能冷却缩短（只靠它时收益小）"): {"派德曼"},
        ("维生素药丸", "获得卡牌时获得治愈（自身获得卡牌少）"): {"阿尔"},
        ("鹰眼瞄具", "只在战斗中加攻击力"): {"星魅琉华"},
        ("友情徽章", "转账、大碗炖肉时双方获得治愈"): {"璐璐"},
        ("小蛇玩偶", "受伤后减伤（被动常不再触发）"): {"璐璐"},
        ("运动手环", "目标被减速时加攻（只在技能回合）"): {"璐璐"},
        ("大背包", "出牌数加成（平时出牌数用不完）"): {"姬梦朝"},
        ("古老法杖", "出牌数和射程加成（平时用不完）"): {"姬梦朝"},
        ("忍术飞镖", "出牌数加成（平时用不完）"): {"姬梦朝"},
        ("可口糖果", "出牌后获得治愈（出牌数加成用不上）"): {"姬梦朝"},
        ("电流核心", "使用技能后出牌数加成（平时用不完）"): {"姬梦朝"},
        ("电击手套", "技能后范围伤害（攻击力加成用不上）"): {"吉尔"},
        ("大铁锚", "受伤时攻击力加成（较少挑战怪物）"): {"超天酱"},
        ("孔雀扇", "被挑战时可获得反击（难以获得温焰）"): {"超天酱"},
        ("孔雀扇", "击中后获得温焰（会伤及自己）"): ATTACKERS - {"糖糖", "摩西"},
        ("孔雀扇", "被挑战时可获得反击（温焰伤及自己）"): {"摩西"},
        ("电磁炮", "攻击力加成（缺充能，范围伤害难触发）"): ATTACKERS - {"星魅琉华", "照", "橘雪莉"},
    }

    def test_net_merges_listed(self):
        # 35 个角色 × 全部筹码：合成一条的组合正好是这些，每组一加一减、合在同一个 net 上
        self.assertEqual(len(self.db.heroes), 35)
        found: dict = {}
        for hero in self.db.heroes:
            sc = Scorer(self.db, State(hero=hero))
            for chip in self.db.chips:
                if chip == self.db.rules["endless_cycle"]["chip"]:
                    continue
                paired = [p for p in sc.parts(chip, (), None) if p.net]
                if not paired:
                    continue
                with self.subTest(hero=hero, chip=chip):
                    self.assertEqual(len(paired), 2)
                    self.assertEqual(sorted(p.score > 0 for p in paired), [False, True])
                    self.assertEqual(len({p.net for p in paired}), 1)
                found.setdefault((chip, paired[0].net), set()).add(hero)
        self.assertEqual(found, self.NET_MERGES)
        # 净效果的短句：不超过 SHORT_LIMIT 个字，不写特性名、分值和口语（「半血」「反击」等特性名本身是游戏机制的叫法，
        # 适配表的短句里照常用，不算特性名）
        traits = set(self.db.traits) - {"半血", "反击", "闪避", "减速", "转账", "通用"}
        for _, short in self.NET_MERGES:
            with self.subTest(short=short):
                self.assertLessEqual(len(short), SHORT_LIMIT)
                self.assertFalse([t for t in traits if t in short])
                self.assertIsNone(BANNED_WORDING.search(short))
                self.assertIsNone(SCORE_WORDING.search(short))

    def test_net_only_with_top_positive(self):
        # 阿尔的星币锤：最高一条加分是「星光成长」的「获得星光」（不讲攻击），说攻击的「经济」只在「其他特性」里：不合成
        parts = Scorer(self.db, State(hero="阿尔")).parts("星币锤", (), None)
        self.assertFalse(any(p.net for p in parts))
        self.assertEqual([p.origin for p in parts if p.label == "特性"], ["星光成长", "辅助定位"])

    def test_bad_net_is_ignored(self):
        # 适配表里 net 写在加分规则上、键不是特性名：记警告并去掉，推荐照常
        from advisor.data import _check_net, _rule
        warns = []
        good = {"chips": ["星币锤"], "score": -20, "reason": "x", "net": {"经济": "短句"}}
        self.assertEqual(_check_net(_rule(good, "辅助定位"), good, "w", {"经济"}, warns.append).net, {"经济": "短句"})
        for raw in ({"chips": ["星币锤"], "score": 20, "reason": "x", "net": {"经济": "短句"}},
                    {"chips": ["星币锤"], "score": -20, "reason": "x", "net": {"不存在的特性": "短句"}},
                    {"chips": ["星币锤"], "score": -20, "reason": "x", "net": {"经济": ""}}):
            with self.subTest(raw=raw):
                self.assertEqual(_check_net(_rule(raw, "辅助定位"), raw, "w", {"经济"}, warns.append).net, {})
        self.assertEqual(len(warns), 3)
        self.assertFalse([w for w in self.db.warnings if "net" in w])

    def test_gold_star_halves_starlight_income(self):
        # 已持有金色飞星：星光层数还会增加对首领的伤害，星光收入的折算减半（借用「星光成长」那一档 exempt_factor），
        # 整句写明原因；短句不变。第 12 次星币锤的扣分由 25.2 减到 12.6
        adv = self.at("140015-12")
        hammer = self.by_name(adv)["星币锤"]
        income = next(p for p in hammer.parts if p.label == "收入折算")
        self.assertEqual(income.short, "星光剩余 2 次兑现")
        self.assertTrue(income.reason.endswith("；已持有「金色飞星」，星光层数还会增加对首领的伤害，折算幅度减半"),
                        income.reason)
        without = self.at("140015-12", owned=self.GOT[:6])
        plain = next(p for p in self.by_name(without)["星币锤"].parts if p.label == "收入折算")
        self.assertAlmostEqual(income.score, plain.score * self.db.rules["income"]["exempt_factor"])
        self.assertNotIn("金色飞星", plain.reason)
        # 已带「星光成长」特性的阿尔本来就按一半计：持有金色飞星时不再多乘一次，也不写这一句
        def al_income(owned):
            parts = Scorer(self.db, State(hero="阿尔", star=3, round=10, owned=owned)).parts("星币锤", owned, 10)
            return next(p for p in parts if p.label == "收入折算")
        self.assertAlmostEqual(al_income(("金色飞星",)).score, al_income(("紫色飞星",)).score)
        self.assertNotIn("金色飞星", al_income(("金色飞星",)).reason)
        # 只折算星光收入：纯星币收入的汉娜人偶不受影响
        def doll(owned):
            parts = Scorer(self.db, State(hero="芬妮", star=3, round=9, owned=owned)).parts("汉娜人偶", owned, 9)
            return [p.score for p in parts if p.label == "收入折算"]
        self.assertEqual(doll(("金色飞星",)), doll(("紫色飞星",)))

    def test_core_income_excludes_player_bonus(self):
        # 必拿筹码的收入折算只按筹码本身的分算，不扣玩家思路给的必拿加分；首选、次选照旧计入
        sc = Scorer(self.db, State(hero="芬妮", star=2, round=7, owned=self.GOT[:4]))
        parts = sc.parts("紫色飞星", self.GOT[:4], 7)
        player = next(p for p in parts if p.label == "玩家思路")
        income = next(p for p in parts if p.label == "收入折算")
        self.assertEqual(player.score, self.db.rules["synergy"]["core"])
        base = sum(p.score for p in parts if p.score > 0 and p.label not in ("成长分", "玩家思路"))
        cfg = self.db.rules["income"]
        cap = sc.cap
        f = reward_rounds_left(7, cap, cfg["reward_every"]) / reward_rounds_left(1, cap, cfg["reward_every"])
        self.assertAlmostEqual(-income.score, base * cfg["starlight"]["紫色飞星"] * (1 - f))
        # 推荐档照旧计入：远野汉娜的 ATM机（推荐 +80），第 6 轮
        sc = Scorer(self.db, State(hero="远野汉娜", star=1, round=6))
        parts = sc.parts("ATM机", (), 6)
        self.assertIn(self.db.rules["synergy"]["first"], [p.score for p in parts if p.label == "玩家思路"])
        income = next(p for p in parts if p.label == "收入折算")
        base = sum(p.score for p in parts if p.score > 0 and p.label != "成长分")
        f = reward_rounds_left(6, sc.cap, cfg["reward_every"]) / reward_rounds_left(1, sc.cap, cfg["reward_every"])
        self.assertAlmostEqual(-income.score, base * cfg["starlight"]["ATM机"] * (1 - f))

    def test_core_tip_is_player_note(self):
        # 必选标签「筹码方案：必选」的悬停（整句）是该角色玩家思路的原文，短句不变；所有有必选的角色都有原文，原文用书面语
        from advisor.ui import text as ui_text
        part = next(p for p in Scorer(self.db, State(hero="芬妮")).parts("金色飞星", (), None) if p.label == "玩家思路")
        self.assertEqual(part.reason, "推荐流派为星光。必选：银行卡-用不完（金色银行卡）、紫色飞星、金色飞星。"
                                      "较少与首领正面交战，两张飞星在放过怪物时触发，效果叠加")
        self.assertEqual(ui_text.part_pill(part), ui_text.ReasonPill("筹码方案：必选", ui_text.PLUS, part.reason))
        with_core = {h: ps for h, ps in self.db.player_synergy.items() if "core" in ps.tiers.values()}
        # 赛克斯：按攻略视频整理的玩家思路（两张飞星必选）
        self.assertEqual(set(with_core), {"凛", "芬妮", "赛克斯"})
        for hero, ps in with_core.items():
            chip = next(c for c, t in ps.tiers.items() if t == "core")
            p = next(p for p in Scorer(self.db, State(hero=hero)).parts(chip, (), None) if p.label == "玩家思路")
            with self.subTest(hero=hero):
                self.assertEqual((p.reason, p.short), (ps.note, "筹码方案：必选"))
                self.assertIsNone(BANNED_WORDING.search(ps.note))
                self.assertIsNone(SCORE_WORDING.search(ps.note))
                for word in ("不怎么", "很好", "吃筹码", "吃反击", "面板", "治愈系", "没有刚需", "逛商店"):
                    self.assertNotIn(word, ps.note)
        # 其余三档：标签「筹码方案：推荐」，悬停整句「筹码方案「默认推荐」：推荐」
        tier = next(p for p in Scorer(self.db, State(hero="远野汉娜")).parts("速度轮滑-中级", (), None) if p.label == "玩家思路")
        self.assertEqual((tier.reason, tier.short), ("筹码方案「默认推荐」：推荐", "筹码方案：推荐"))

    def test_core_lift_after_note_ending_with_period(self):
        # 必选补分时原文另起一句接「优先于……」；原文本身以句号结尾时不写成「……。。优先于……」
        from types import SimpleNamespace

        from advisor.engine import CORE_LEAD, Part
        from advisor.engine.advice import lift_core
        self.assertEqual(CORE_LEAD, "，优先于本次未列入筹码方案的筹码")
        for note in ("测试用的攻略原文", "测试用的攻略原文。"):
            e = SimpleNamespace(parts=[Part("玩家思路", 100.0, note, "攻略", "筹码方案：必选")], immediate=0.0, total=0.0)
            lift_core([e], 5.0, CORE_LEAD)
            with self.subTest(note=note):
                self.assertEqual(e.parts[0].reason, "测试用的攻略原文。优先于本次未列入筹码方案的筹码")
                self.assertEqual((e.parts[0].score, e.total), (105.0, 5.0))
        # 程序自己写的整句（以「筹码方案」开头：规则「筹码方案：……」、没有原文的档位「筹码方案「默认推荐」：必选」）直接接「，优先于……」
        for reason in ("筹码方案：已持有充能来源，电磁炮的范围伤害可以稳定触发", "筹码方案「默认推荐」：必选"):
            e = SimpleNamespace(parts=[Part("玩家思路", 100.0, reason, "攻略", "筹码方案：必选")], immediate=0.0, total=0.0)
            lift_core([e], 5.0, CORE_LEAD)
            with self.subTest(reason=reason):
                self.assertEqual(e.parts[0].reason, reason + "，优先于本次未列入筹码方案的筹码")

    def test_live_game_bests(self):
        # 这一局 12 次推荐按现在的规则重算，和对局当时相比首推变了一次：第 11 次医疗箱-完备治疗 → 友情徽章（当时实际选了刷新）。
        # 第 12 次（这一局实际的最后一次）照旧星币锤：读到的进度第 9 格（噩梦，进度上限 17），3 星以后、还没到进度上限，之后的
        # 三选一按下限约 0.8 次，剩的 1 次刷新要留着，刷新扣掉代价后不如星币锤（之后预计没有三选一时首推刷新：她不挑战
        # 怪物，星币锤只剩 5 层星光，芬妮的角色专属规则另扣分）
        want = {"135348-7": REROLL, "140011-11": "友情徽章", "140015-12": "星币锤"}
        for key, best in want.items():
            with self.subTest(offer=key):
                self.assertEqual(self.at(key).best, best)
        self.assertEqual(self.at("140015-12", progress=17).best, REROLL)


class KeepSchoolsTest(Base):
    """推荐流派都要尽量保住（适配表 [[hero]] 的 keep_schools、keep_schools_penalty，rules.toml [synergy] keep_schools）：
    默认推荐里，筹码属于要保的流派以外、还没进入的流派，选择后剩下的流派名额少于还没进入的要保的流派个数时扣分（分项名
    「推荐流派」）；已进入其他流派、要保的流派本局已经进不全时，已进入的那个流派按推荐流派加分。keep_schools 写 true 时
    推荐流派都保（南希露：标记、治愈），写流派名的列表时只保列出的（没列出的推荐流派也算其他流派，用测试里造的角色数据测）。
    南希露另有普通难度下生存类筹码的扣分（适配表里带 difficulty 条件的角色专属规则）。"""

    def keep(self, hero, chip, owned=(), db=None, scheme=None, **kw):
        owned = tuple(owned)
        parts = Scorer(db or self.db, State(hero=hero, owned=owned, **kw), scheme=scheme).parts(chip, owned, None)
        return [p for p in parts if p.label == "推荐流派"]

    def hero_like(self, hero, **changes):
        """资料库的副本：角色 hero 的自动层改几项（schools、keep_schools、keep_schools_penalty），其余不变。
        测试里自己造角色数据，不依赖适配表里各角色实际的写法。"""
        hs = dataclasses.replace(self.db.hero_synergy[hero], **changes)
        return dataclasses.replace(self.db, hero_synergy={**self.db.hero_synergy, hero: hs})

    def test_nancy_schools(self):
        hs = self.db.hero_synergy["南希露"]
        self.assertTrue(hs.keep_schools)
        self.assertEqual(Scorer(self.db, State(hero="南希露")).pref_schools, ("标记", "治愈"))
        self.assertNotIn("南希露", self.db.player_synergy)  # 不用玩家思路，只按技能和数值推
        self.assertEqual(self.db.rules["synergy"]["keep_schools"], -40)

    def test_penalty_wording(self):
        value = self.db.rules["synergy"]["keep_schools"]
        # 还没有流派：选了星光的紫色飞星只剩一个名额，标记、治愈只能再进入一个
        (one,) = self.keep("南希露", "紫色飞星")
        self.assertEqual((one.score, one.reason, one.short),
                         (value, "选择后进入「星光」流派，只剩一个流派名额：推荐流派「标记」「治愈」只能再进入一个",
                          "选择后标记、治愈只能再进入一个"))
        # 已进入星光：选了充能的电磁炮流派名额占满，标记、治愈都不再出现
        (both,) = self.keep("南希露", "电磁炮", owned=("银行卡-余额多",))
        self.assertEqual((both.score, both.reason, both.short),
                         (value, "选择后进入「充能」流派，流派名额占满：推荐流派「标记」「治愈」的筹码本局不再出现",
                          "选择后标记、治愈流派不再出现"))
        # 已进入标记：选了星光的银行卡只剩治愈进不来
        (heal,) = self.keep("南希露", "银行卡-余额多", owned=("普通瞄具",))
        self.assertEqual((heal.score, heal.reason, heal.short),
                         (value, "选择后进入「星光」流派，流派名额占满：推荐流派「治愈」的筹码本局不再出现", "选择后治愈流派不再出现"))
        for p in (one, both, heal):
            with self.subTest(short=p.short):
                self.assertLessEqual(len(p.short), SHORT_LIMIT)
                self.assertIsNone(SCORE_WORDING.search(p.reason + p.short))
                self.assertIsNone(BANNED_WORDING.search(p.reason + p.short))

    def test_no_penalty_cases(self):
        # 推荐流派的筹码照旧加分；不属于流派的加攻筹码不扣；已进入的流派不再扣（按推荐流派加分，见 test_entered_school_adopted）
        self.assertEqual([p.score for p in self.keep("南希露", "普通瞄具")], [self.db.rules["synergy"]["school"]])
        self.assertEqual([p.score for p in self.keep("南希露", "医疗箱-紧急治疗")], [self.db.rules["synergy"]["school"]])
        self.assertEqual(self.keep("南希露", "拳击手套-高级"), [])
        self.assertEqual(self.keep("南希露", "精品剑盾", map="魔法学院"), [])
        self.assertTrue(all(p.score > 0 for p in self.keep("南希露", "星币锤", owned=("银行卡-余额多",))))
        # 流派名额早已占满（手工输入的状态）：本不该出现的第三个流派不扣
        self.assertEqual(self.keep("南希露", "紫色飞星", owned=("电磁炮", "普通瞄具")), [])

    def test_entered_school_adopted(self):
        # 已进入推荐流派以外的流派、标记和治愈本局已经进不全：已进入的那个流派和推荐流派一样加分
        school = self.db.rules["synergy"]["school"]
        # 已进入星光、还剩一个名额：星光的筹码加分；推荐流派照旧加分；还没进入的充能照旧扣分
        (star,) = self.keep("南希露", "星币锤", owned=("银行卡-余额多",))
        self.assertEqual((star.score, star.reason, star.short),
                         (school, "已进入「星光」流派，推荐流派「标记」「治愈」只能再进入一个：「星光」流派的筹码同样按推荐流派计算",
                          "推荐流派：星光（已进入）"))
        self.assertEqual([p.score for p in self.keep("南希露", "普通瞄具", owned=("银行卡-余额多",))], [school])
        self.assertEqual([p.score for p in self.keep("南希露", "电磁炮", owned=("银行卡-余额多",))],
                         [self.db.rules["synergy"]["keep_schools"]])
        # 已进入标记和星光：治愈不再出现，星光的筹码同样加分
        (gone,) = self.keep("南希露", "银行卡-用不完", owned=("普通瞄具", "银行卡-余额多"))
        self.assertEqual((gone.score, gone.reason),
                         (school, "已进入「星光」流派，推荐流派「治愈」的筹码本局不再出现：「星光」流派的筹码同样按推荐流派计算"))
        # 星光、充能占满名额，标记、治愈一个也进不了：没有要保的推荐流派，不加分
        self.assertEqual(self.keep("南希露", "星币锤", owned=("银行卡-余额多", "电磁炮")), [])
        self.assertEqual(self.keep("南希露", "安全气囊", owned=("银行卡-余额多", "电磁炮")), [])
        # 推荐流派还进得全（没进入其他流派）时不加，选择后进入星光照旧扣分；没写 keep_schools 的角色（测试里造的墨影，
        # 推荐流派同样是标记、治愈）、自己的方案不加
        self.assertEqual([p.score for p in self.keep("南希露", "星币锤")], [self.db.rules["synergy"]["keep_schools"]])
        plain = self.hero_like("墨影", schools=("标记", "治愈"))
        self.assertEqual(self.keep("墨影", "星币锤", owned=("银行卡-余额多",), db=plain), [])
        from advisor.engine import Plan
        plan = Plan(name="测试", hero="南希露", tiers=(("拳击手套-高级", "core"),))
        self.assertEqual(self.keep("南希露", "星币锤", owned=("银行卡-余额多",), scheme=plan), [])
        for p in (star, gone):
            with self.subTest(short=p.short):
                self.assertLessEqual(len(p.short), SHORT_LIMIT)
                self.assertIsNone(SCORE_WORDING.search(p.reason + p.short))
                self.assertIsNone(BANNED_WORDING.search(p.reason + p.short))

    SURVIVAL = ("安全气囊", "时间怀表", "重生人偶", "摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级")

    def normal_parts(self, hero, chip, **kw):
        m = self.db.chips[chip].maps
        st = State(hero=hero, map=m[0] if m else None, **kw)
        return [p for p in Scorer(self.db, st).parts(chip, (), None) if p.label == "角色专属规则" and p.score < 0]

    def test_nancy_normal_difficulty_survival(self):
        # 普通难度：抵消致命伤害、被击倒后复活、单纯提高防御力的筹码对南希露扣分；困难及以上、难度未知时不扣
        rule = next(r for r in self.db.hero_synergy["南希露"].rules if r.difficulty is not None)
        self.assertEqual(set(rule.chips), set(self.SURVIVAL))
        self.assertEqual(rule.difficulty, NORMAL)
        for chip in self.SURVIVAL:
            with self.subTest(chip=chip):
                (part,) = self.normal_parts("南希露", chip, difficulty=NORMAL)
                self.assertEqual((part.score, part.short), (rule.score, "普通难度生存压力小，收益低"))
                self.assertLessEqual(len(part.short), SHORT_LIMIT)
                self.assertIsNone(BANNED_WORDING.search(part.reason + part.short))
                self.assertEqual(self.normal_parts("南希露", chip), [])  # 难度未知
                self.assertEqual(self.normal_parts("南希露", chip, difficulty_level=LEVEL_NIGHTMARE), [])  # 噩梦及以上
        # 治愈来源、加生命值的筹码不扣；别的角色普通难度也不扣
        for chip in ("缓冲盾牌", "医疗箱-完备治疗", "夹心饼干-可口"):
            with self.subTest(chip=chip):
                self.assertEqual(self.normal_parts("南希露", chip, difficulty=NORMAL), [])
        self.assertEqual(self.normal_parts("美咲", "摩托头盔-高级", difficulty=NORMAL), [])
        # 困难及以上照旧有「难度」加分（生存类筹码、物理输出角色的头盔），普通难度没有
        hard = Scorer(self.db, State(hero="南希露")).parts("摩托头盔-高级", (), None)
        self.assertTrue(any(p.label == "难度" and p.score > 0 for p in hard))
        normal = Scorer(self.db, State(hero="南希露", difficulty=NORMAL)).parts("摩托头盔-高级", (), None)
        self.assertFalse(any(p.label == "难度" for p in normal))

    def test_rule_difficulty_condition(self):
        # 规则的 difficulty 条件：只认「普通」；写了别的难度名时记警告，这条规则不生效
        from advisor.data import _check_difficulty, _rule
        ok = _rule({"chips": ["安全气囊"], "score": -30, "reason": "x", "difficulty": "普通"}, "角色")
        self.assertTrue(ok.applies(None, frozenset(), None, NORMAL))
        self.assertFalse(ok.applies(None, frozenset(), None, None))
        self.assertFalse(ok.applies(None, frozenset(), None))
        plain = _rule({"chips": ["安全气囊"], "score": -30, "reason": "x"}, "角色")
        self.assertTrue(plain.applies(None, frozenset(), None, NORMAL) and plain.applies(None, frozenset(), None))
        warns = []
        raw = {"chips": ["安全气囊"], "score": -30, "reason": "x", "difficulty": "困难"}
        _check_difficulty(raw, "w", warns.append)
        self.assertEqual(len(warns), 1)
        bad = _rule(raw, "角色")
        self.assertFalse(bad.applies(None, frozenset(), None, NORMAL) or bad.applies(None, frozenset(), None, None))
        self.assertFalse([w for w in self.db.warnings if "difficulty" in w])

    def test_normal_difficulty_rule_with_scheme(self):
        # 选了自己的方案时：没标档位的筹码照角色特性计算，角色专属规则（含普通难度的扣分）照常；标了档位的按档位，不再扣
        from advisor.engine import Plan
        rule = next(r for r in self.db.hero_synergy["南希露"].rules if r.difficulty is not None)
        plan = Plan(name="测试", hero="南希露", tiers=(("摩托头盔-高级", "first"),))
        sc = Scorer(self.db, State(hero="南希露", difficulty=NORMAL), scheme=plan)
        self.assertEqual([p.score for p in sc.parts("安全气囊", (), None) if p.label == "角色专属规则"], [rule.score])
        self.assertFalse([p for p in sc.parts("摩托头盔-高级", (), None) if p.label == "角色专属规则"])
        # 难度未知时没标的也不扣
        sc = Scorer(self.db, State(hero="南希露"), scheme=plan)
        self.assertFalse([p for p in sc.parts("安全气囊", (), None) if p.label == "角色专属规则"])

    def test_only_with_flag(self):
        # 关掉开关时不扣；推荐流派同样是两个、但没写 keep_schools 的角色不扣
        db = dataclasses.replace(self.db, hero_synergy={
            **self.db.hero_synergy, "南希露": dataclasses.replace(self.db.hero_synergy["南希露"], keep_schools=False)})
        self.assertEqual(self.keep("南希露", "紫色飞星", db=db), [])
        plain = self.hero_like("墨影", schools=("标记", "治愈"))  # 测试里造的：推荐流派两个，没写 keep_schools
        self.assertEqual(Scorer(plain, State(hero="墨影")).pref_schools, ("标记", "治愈"))
        self.assertEqual(self.keep("墨影", "紫色飞星", db=plain), [])
        # 选了自己的方案时不用默认推荐的任何部分：推荐流派既不加分也不扣分
        from advisor.engine import Plan
        plan = Plan(name="测试", hero="南希露", tiers=(("拳击手套-高级", "core"),))
        self.assertEqual(self.keep("南希露", "紫色飞星", scheme=plan), [])
        self.assertEqual(self.keep("南希露", "普通瞄具", scheme=plan), [])

    def test_knife_needs_heal_source(self):
        # 美工刀的治愈来源折算（rules.toml [heal_knife] ③）对南希露照常起作用，推荐流派的加分一并折算：
        # 还没有治愈来源时少算（攻击力加成 + 推荐流派加分）×（1 − 之后获得治愈来源的概率）
        sc = Scorer(self.db, State(hero="南希露", star=3, round=10))
        parts = sc.parts("美工刀-锋利", (), 10)
        trait = next(p for p in parts if p.origin == "物理输出")
        school = next(p for p in parts if p.label == "推荐流派")
        discount = next(p for p in parts if p.origin == HEAL_DISCOUNT_ORIGIN)
        p = heal_chance(sc, "美工刀-锋利", frozenset())
        self.assertGreater(p, 0)
        self.assertAlmostEqual(-discount.score, (trait.score + school.score) * (1 - p))
        # 已持有治愈来源时照常全额
        owned = ("医疗箱-紧急治疗",)
        parts = Scorer(self.db, State(hero="南希露", owned=owned)).parts("美工刀-锋利", owned, None)
        self.assertFalse(any(p.origin == HEAL_DISCOUNT_ORIGIN for p in parts))
        # 没写 keep_schools 的物理输出角色（测试里造的阿兰娜，推荐流派治愈）只折算攻击力加成那一项；
        # 只保治愈（keep_schools = ["治愈"]）时和南希露一样，推荐流派的加分一并折算
        for keep in (False, ("治愈",)):
            db = self.hero_like("阿兰娜", schools=("治愈",), keep_schools=keep)
            sc = Scorer(db, State(hero="阿兰娜", star=3, round=10))
            parts = sc.parts("美工刀-锋利", (), 10)
            self.assertIn(self.db.rules["synergy"]["school"], [p.score for p in parts if p.label == "推荐流派"])
            rule = next(r for r in sc.auto_rules["美工刀-锋利"] if r.origin == "物理输出")
            discount = next(p for p in parts if p.origin == HEAL_DISCOUNT_ORIGIN)
            positive = sorted((r for r in sc.auto_rules["美工刀-锋利"] if r.score > 0), key=lambda r: -r.score)
            extra = next((p for p in parts if p.label == "其他特性"), None)
            counted = rule.score if positive[0] is rule else extra.score * rule.score / sum(r.score for r in positive[1:])
            school = self.db.rules["synergy"]["school"] if keep else 0
            with self.subTest(keep=keep):
                self.assertAlmostEqual(-discount.score,
                                       (counted + school) * (1 - heal_chance(sc, "美工刀-锋利", frozenset())))

    def test_keep_listed_school_only(self):
        # keep_schools 写流派名的列表：只保列出的流派。测试里造的邦妮（推荐流派标记、充能，只保标记，扣 60）
        db = self.hero_like("邦妮", schools=("标记", "充能"), keep_schools=("标记",), keep_schools_penalty=-60.0)
        school = self.db.rules["synergy"]["school"]
        sc = Scorer(db, State(hero="邦妮"))
        self.assertEqual((sc.pref_schools, sc.kept_schools, sc.keep_penalty), (("标记", "充能"), ("标记",), -60.0))
        # 还没有流派：先进充能、星光都还留一个名额给标记，不扣；充能照旧按推荐流派加分
        self.assertEqual([p.score for p in self.keep("邦妮", "电流剑", db=db)], [school])
        self.assertEqual(self.keep("邦妮", "紫色飞星", db=db), [])
        # 已进入治愈（缓冲盾牌）：没列出的推荐流派充能也算其他流派，选择后标记进不去：推荐流派的加分照加、另扣 60；
        # 星光同样扣 60
        owned = ("缓冲盾牌",)
        bonus, penalty = self.keep("邦妮", "电流剑", owned, db=db)
        self.assertEqual((bonus.score, penalty.score), (school, -60.0))
        self.assertEqual((penalty.reason, penalty.short),
                         ("选择后进入「充能」流派，流派名额占满：推荐流派「标记」的筹码本局不再出现", "选择后标记流派不再出现"))
        self.assertEqual([p.score for p in self.keep("邦妮", "紫色飞星", owned, db=db)], [-60.0])
        # 要保的流派、已进入的流派照常
        self.assertEqual([p.score for p in self.keep("邦妮", "标靶", owned, db=db)], [school])
        self.assertEqual(self.keep("邦妮", "医疗箱-紧急治疗", owned, db=db), [])
        # 只保一个流派：已进入的其他流派不按推荐流派加分（要保的流派还进得了时不加；进不了时流派名额已被别的流派占满）
        for owned in (("缓冲盾牌",), ("缓冲盾牌", "电流剑"), ("缓冲盾牌", "银行卡-余额多")):
            with self.subTest(owned=owned):
                self.assertEqual(self.keep("邦妮", "医疗箱-紧急治疗", owned, db=db), [])
        # 扣分没写时用 rules.toml [synergy] keep_schools；keep_schools = true 时也可以按角色写扣分
        default = self.hero_like("邦妮", schools=("标记", "充能"), keep_schools=("标记",))
        self.assertEqual([p.score for p in self.keep("邦妮", "紫色飞星", ("缓冲盾牌",), db=default)],
                         [self.db.rules["synergy"]["keep_schools"]])
        heavy = self.hero_like("南希露", keep_schools_penalty=-60.0)
        self.assertEqual([p.score for p in self.keep("南希露", "紫色飞星", db=heavy)], [-60.0])
        # 推荐流派两个都写进列表，和写 true 一样（扣分、已进入的流派同样加分）
        both = self.hero_like("南希露", keep_schools=("标记", "治愈"))
        for chip, owned in (("紫色飞星", ()), ("电磁炮", ("银行卡-余额多",)), ("星币锤", ("银行卡-余额多",)),
                            ("银行卡-用不完", ("普通瞄具", "银行卡-余额多"))):
            with self.subTest(chip=chip, owned=owned):
                self.assertEqual([(p.score, p.reason) for p in self.keep("南希露", chip, owned, db=both)],
                                 [(p.score, p.reason) for p in self.keep("南希露", chip, owned)])
        # 选了自己的方案时不用：推荐流派既不加分也不扣分
        from advisor.engine import Plan
        plan = Plan(name="测试", hero="邦妮", tiers=(("拳击手套-高级", "core"),))
        self.assertEqual(self.keep("邦妮", "电流剑", ("缓冲盾牌",), db=db, scheme=plan), [])

    def test_review_note(self):
        # 适配表审查清单（tools/make_review.py）「推荐流派」一行后面的说明照引擎实际的算法写：要保的流派、按角色写了的扣分；
        # 要保的流派只有一个时（列表只写一个，或写 true 而推荐流派只有一个）引擎里没有「已进入的其他流派同样加分」，不写
        from tools import make_review

        adopted = "已进入的其他流派同样按推荐流派加分"
        nancy = make_review.keep_note(self.db, "南希露")
        self.assertTrue(nancy.startswith("（都要尽量保住：") and adopted in nancy, nancy)
        self.assertNotIn("每张扣", nancy)
        # 测试里造的蓝海晴：推荐流派只有星光、写 true。已进入别的流派以后，星光还进得了、进不了都不加分
        single = self.hero_like("蓝海晴", schools=("星光",), keep_schools=True)
        for owned in (("缓冲盾牌",), ("缓冲盾牌", "标靶")):
            with self.subTest(owned=owned):
                self.assertEqual(self.keep("蓝海晴", "医疗箱-紧急治疗", owned, db=single), [])
        note = make_review.keep_note(single, "蓝海晴")
        self.assertTrue(note.startswith("（都要尽量保住："), note)
        self.assertNotIn(adopted, note)
        listed = self.hero_like("邦妮", schools=("标记", "充能"), keep_schools=("标记",), keep_schools_penalty=-60.0)
        self.assertEqual(make_review.keep_note(listed, "邦妮"),
                         "（要尽量保住「标记」：选择后会让它进不全的其他流派筹码扣分，每张扣 60 分；没列出的推荐流派也算其他流派）")
        self.assertEqual(make_review.keep_note(self.hero_like("墨影", schools=("标记", "治愈")), "墨影"), "")

    def test_knife_school_bonus_follows_kept(self):
        # 美工刀的治愈来源折算一并折算推荐流派的加分，只在治愈是要保的流派时：只保标记的物理输出角色，治愈的美工刀照常
        # 加推荐流派的分、不折算（和不保的角色一样）；都保、或只保治愈时折算
        from advisor.engine.chances import knife_school_bonus

        school = float(self.db.rules["synergy"]["school"])
        for keep, want in ((("标记",), 0.0), (("治愈",), school), (True, school), (False, 0.0)):
            with self.subTest(keep=keep):
                db = self.hero_like("美咲", schools=("治愈", "标记"), keep_schools=keep)
                self.assertEqual(knife_school_bonus(Scorer(db, State(hero="美咲")), "美工刀-初级"), want)


class NancyLiveReviewTest(Base):
    """南希露一局（魔法学院、普通，三名电脑队友）的 14 次三选一，局面取自推荐日志（offer_id 去掉日期的部分）。
    按南希露的推荐规则（不用玩家思路、推荐流派标记和治愈两个都尽量保住）重算的首推。"""

    BASE = dict(hero="南希露", map="魔法学院", teammates=(Teammate("帕帕拉", True), Teammate("美咲", True), Teammate("米米", True)),
                difficulty=NORMAL, difficulty_level=NORMAL, difficulty_name="普通")
    TASK5 = dict(star=2, round=5, owned=("拳击手套-初级", "额外电池"), source="task", tier=2, probabilities=(40, 40, 20), progress=5)
    LATE = dict(star=3, probabilities=(20, 50, 30), left_rarity="金", source="levelup")
    OFFERS = {
        # 第 5 轮出现银行卡-余额多的两次任务三选一：都不首推它
        "203108-4": (dict(TASK5, rerolls=3, left_rarity="金"), ["大碗炖肉", "银行卡-余额多", "金色飞星"], REROLL),
        "203127-6": (dict(TASK5, rerolls=1, left_rarity="蓝"), ["医疗箱-紧急治疗", "银行卡-余额多", "会员推荐信"],
                     "医疗箱-紧急治疗"),
        # 同一轮（剩 2 次刷新）：摩托头盔-中级按普通难度扣分，首推优雅之羽（物理输出的条件攻击力加成）
        "203125-5": (dict(TASK5, rerolls=2, left_rarity="蓝"), ["能量回收", "摩托头盔-中级", "优雅之羽"], "优雅之羽"),
        # 第 10 轮金色美工刀：还没有治愈来源，仍首推刷新
        "203913-7": (dict(LATE, round=10, progress=10, rerolls=2, owned=("拳击手套-初级", "额外电池", "银行卡-余额多")),
                     ["美工刀-锋利", "银行卡-余额少", "友情徽章"], REROLL),
        # 不属于流派的加攻大件不受影响
        "203949-8": (dict(LATE, round=10, progress=10, rerolls=1, owned=("拳击手套-初级", "额外电池", "银行卡-余额多")),
                     ["摩托头盔-高级", "夹心饼干-一般", "精品剑盾"], "精品剑盾"),
        # 第 11 轮（已有银行卡-余额多、剩 1 次刷新）：星币锤属于已进入的星光流派，不扣分；安全气囊（充能）选择后
        # 标记、治愈不再出现。首推星币锤
        "204322-9": (dict(LATE, round=11, progress=11, rerolls=1,
                          owned=("拳击手套-初级", "额外电池", "银行卡-余额多", "精品剑盾")),
                     ["星币锤", "安全气囊", "维生素药丸"], "星币锤"),
        # 第 11 轮（已有银行卡-余额多、没有刷新）：不再首推电磁炮
        "204351-10": (dict(LATE, round=11, progress=11, rerolls=0,
                           owned=("拳击手套-初级", "额外电池", "银行卡-余额多", "精品剑盾")),
                      ["电磁炮", "手电筒-强光", "ATM机"], "手电筒-强光"),
        # 第 15 轮（已进入星光、充能，普通难度，没有刷新）：安全气囊、摩托头盔-高级按普通难度扣分，首推银行卡-用不完
        "205330-14": (dict(LATE, round=15, progress_cap=18, progress=15, rerolls=0,
                           owned=("拳击手套-初级", "额外电池", "银行卡-余额多", "精品剑盾", "电磁炮", "速度轮滑-中级",
                                  "拳击手套-中级", "星币锤")),
                      ["银行卡-用不完", "安全气囊", "摩托头盔-高级"], "银行卡-用不完"),
    }

    def test_bests(self):
        for key, (fields, offer, best) in self.OFFERS.items():
            with self.subTest(offer=key):
                adv = advise(self.db, State(**{**self.BASE, **fields}), offer)
                self.assertEqual(adv.best, best)

    def test_magnet_cannon_says_why(self):
        # 电磁炮的扣分写明原因：已进入星光，选了充能以后标记、治愈都不再出现；同一次的手电筒-强光属于已进入的星光，
        # 按推荐流派加分
        fields, offer, _ = self.OFFERS["204351-10"]
        ev = self.by_name(advise(self.db, State(**{**self.BASE, **fields}), offer))
        part = next(p for p in ev["电磁炮"].parts if p.label == "推荐流派")
        self.assertEqual(part.short, "选择后标记、治愈流派不再出现")
        torch = next(p for p in ev["手电筒-强光"].parts if p.label == "推荐流派")
        self.assertEqual((torch.score, torch.short), (self.db.rules["synergy"]["school"], "推荐流派：星光（已进入）"))

    def test_last_pick_normal_difficulty(self):
        # 第 15 轮：标记、治愈都已进不来（星光、充能占满名额），已进入的流派不再按推荐流派加分；
        # 安全气囊、摩托头盔-高级按普通难度扣分
        fields, offer, _ = self.OFFERS["205330-14"]
        ev = self.by_name(advise(self.db, State(**{**self.BASE, **fields}), offer))
        for name in offer:
            with self.subTest(chip=name):
                self.assertFalse([p for p in ev[name].parts if p.label == "推荐流派"])
        value = next(r.score for r in self.db.hero_synergy["南希露"].rules if r.difficulty is not None)
        for name in ("安全气囊", "摩托头盔-高级"):
            with self.subTest(chip=name):
                self.assertIn(value, [p.score for p in ev[name].parts if p.label == "角色专属规则"])
        # 困难及以上（含难度未知）时不扣分，物理输出角色的头盔另有「难度」加分：首推摩托头盔-高级
        hard = {**self.BASE, **fields, "difficulty": None, "difficulty_level": None, "difficulty_name": None}
        self.assertEqual(advise(self.db, State(**hard), offer).best, "摩托头盔-高级")


if __name__ == "__main__":
    unittest.main()
