"""按攻略视频结论定的打分规则（攻略视频的结论、职业分值累加、玩家思路的来源）：每条规则一个小局面。"""
import contextlib
import dataclasses
import math
import re
import unittest
from unittest import mock

from advisor.data import LEVEL_NIGHTMARE, NORMAL, SOURCE_GUIDE, _merge_players, load_db
from advisor.engine import (
    EXTREME_SHORT,
    HEAL_DISCOUNT_ORIGIN,
    HEAL_DISCOUNT_SHORT,
    LATE_INCOME_NOTE,
    REROLL,
    SERIES_NEAR_TIP,
    SERIES_NEAR_TIP_SCHEME,
    Plan,
    RerollEval,
    Scorer,
    advise,
    reward_rounds_left,
    shown_percents,
)
from advisor.engine.advice import series_near
from advisor.engine.chances import heal_chance
from advisor.pool import State, Teammate, appear_prob, class_weights, owned_schools, player_pool, slot_dists
from tests.support.player_layer import with_player_layer

HEAL = ("医疗箱-完备治疗", "医疗箱-紧急治疗", "大碗炖肉", "维生素药丸", "可口糖果", "缓冲盾牌", "友情徽章")
KNIVES = ("美工刀-初级", "美工刀-锋利")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def scorer(self, hero, owned=(), use_player=True, db=None, **kw):
        return Scorer(db or self.db, State(hero=hero, owned=tuple(owned), **kw), use_player=use_player)

    def parts(self, hero, chip, owned=(), use_player=True, db=None, **kw):
        return self.scorer(hero, owned, use_player, db, **kw).parts(chip, tuple(owned), kw.get("round"))

    def labelled(self, hero, chip, label, owned=(), **kw):
        return [p for p in self.parts(hero, chip, owned, **kw) if p.label == label]

    def rule_scores(self, trait):
        return {c: r.score for r in self.db.trait_rules[trait] for c in r.chips}

    def with_rules(self, section, **values):
        rules = dict(self.db.rules)
        rules[section] = {**rules[section], **values}
        return dataclasses.replace(self.db, rules=rules)


class ClassPointsTest(Base):
    """职业分值累加：升星和筹码商店最左格选职业池的权重 = 角色初始职业分值 + 已拿筹码的职业分值（pool.class_weights）。"""

    def test_class_weights(self):
        hero = self.db.heroes["邦妮"]
        self.assertEqual(class_weights(self.db, hero), {"攻击": 100, "卡牌": 15, "辅助": 15, "生存": 15})
        self.assertEqual(class_weights(self.db, hero, ()), class_weights(self.db, hero))
        # ATM机 辅助 +50；资料库未收录的筹码不计；同一个筹码写两次只算一次
        self.assertEqual(class_weights(self.db, hero, ("ATM机", "某个新筹码", "ATM机"))["辅助"], 65)
        self.assertEqual(class_weights(self.db, hero, ("美工刀-初级",)), {"攻击": 130, "卡牌": 15, "辅助": 15, "生存": 25})
        self.assertTrue(self.db.rules["pool"]["accumulate_class_points"])
        off = self.with_rules("pool", accumulate_class_points=False)
        self.assertEqual(class_weights(off, hero, ("ATM机",)), class_weights(off, hero))

    def test_default_unchanged_for_editor(self):
        # 不传 class_owned 时只按初始职业分值（编辑窗口的「较难出现」就是这样，不传已有筹码）
        db, hero = self.db, self.db.heroes["米米"]
        owned = ("ATM机", "缓冲盾牌")
        pool = player_pool(db, owned, None)
        schools = owned_schools(db, owned)
        plain = slot_dists(db, hero, pool, schools, 2)
        self.assertEqual(plain, slot_dists(db, hero, pool, schools, 2, class_owned=None))
        self.assertEqual(plain, slot_dists(db, hero, pool, schools, 2, class_owned=()))
        accumulated = slot_dists(db, hero, pool, schools, 2, class_owned=owned)
        self.assertNotEqual(plain[0], accumulated[0])  # 最左格变了
        self.assertEqual(plain[1], accumulated[1])  # 其余两格不受职业权重影响

    def test_video_orderings(self):
        # 攻略视频（1 星筹码怎么选）给的排序只有按累加才算得出来：邦妮要普通瞄具时，已有标靶 ＞ 手持风扇-小 ＞ 标记喷罐
        # （不累加时三者完全相同）；凛要大背包或忍术飞镖（3 星）时，已有标靶 ＜ 手持风扇-小 ＜ 标记喷罐
        def chance(hero, owned, targets, star, accumulate):
            pool = player_pool(self.db, owned, None)
            left, general = slot_dists(self.db, self.db.heroes[hero], pool, owned_schools(self.db, owned), star,
                                       class_owned=owned if accumulate else None)
            miss = 1.0
            for t in targets:
                miss *= 1 - appear_prob(left, general, t)
            return 1 - miss

        for accumulate in (False, True):
            sights = [chance("邦妮", (c,), ["普通瞄具"], 2, accumulate) for c in ("标靶", "手持风扇-小", "标记喷罐")]
            cards = [chance("凛", (c,), ["大背包", "忍术飞镖"], 3, accumulate) for c in ("标靶", "手持风扇-小", "标记喷罐")]
            with self.subTest(accumulate=accumulate):
                if accumulate:
                    self.assertGreater(sights[0], sights[1])
                    self.assertGreater(sights[1], sights[2])
                    self.assertLess(cards[0], cards[1])
                    self.assertLess(cards[1], cards[2])
                else:
                    self.assertAlmostEqual(min(sights), max(sights))
                    self.assertAlmostEqual(min(cards), max(cards))

    def test_lookahead_penalises_cross_class_chip(self):
        # 主力输出拿辅助类筹码（ATM机 辅助 +50）：之后最左格更常出现辅助池的筹码，前瞻比不累加时低
        st = dict(star=1, round=3)
        off = self.with_rules("pool", accumulate_class_points=False)
        with_acc = self.scorer("邦妮", **st).lookahead("ATM机", ())
        without = self.scorer("邦妮", db=off, **st).lookahead("ATM机", ())
        self.assertLess(with_acc, without - 1)

    def test_lookahead_explains_class_shift(self):
        # 主力输出拿 ATM机：前瞻为负，整句写明职业分值的变化；短句写出现率下降最多的高适配度筹码或「更常出现辅助职业的筹码」，
        # 短句的要点都取自整句（不带来新流派的筹码前瞻为负时短句也不空）
        sc = self.scorer("邦妮", star=1, round=3)
        self.assertLess(sc.lookahead("ATM机", ()), 0)
        reason = sc.lookahead_reason("ATM机", ())
        self.assertIn("选择后辅助职业分值 +50，之后升星和筹码商店的三选一更常出现辅助职业的筹码", reason)
        short = sc.lookahead_short("ATM机", ())
        self.assertTrue(short.startswith("前瞻："), short)
        point = short[len("前瞻："):]
        rate = re.fullmatch(r"(.+) (\d+)%→(\d+)%", point)
        if rate:
            name, a, b = rate.groups()
            self.assertIn(f"{name}下次出现率 {a}% → {b}%", reason)
            self.assertLess(int(b), int(a))
        else:
            self.assertEqual(point, "更常出现辅助职业的筹码")
            self.assertIn(point, reason)
        # 两个职业：美工刀-初级（攻击 +30、生存 +10）
        self.assertIn("选择后攻击职业分值 +30、生存职业分值 +10，", sc.lookahead_reason("美工刀-初级", ()))
        # 关掉累加时不写
        off = self.with_rules("pool", accumulate_class_points=False)
        self.assertNotIn("职业分值", self.scorer("邦妮", db=off, star=1, round=3).lookahead_reason("ATM机", ()))

    def test_charge_partners_same_school(self):
        # 以充能筹码为「已持有」一方、配的不是充能筹码的组合规则（优雅之羽与安全气囊、探天卫星与出牌数）不算开套预判的配套筹码
        names = ("优雅之羽与安全气囊", "探天卫星与出牌数")
        plain = dataclasses.replace(self.db, combos=tuple(c for c in self.db.combos if c.name not in names))
        for chip in ("安全气囊", "电流核心"):
            a = [p.score for p in self.parts("美咲", chip, star=1) if p.origin == "charge_build"]
            b = [p.score for p in self.parts("美咲", chip, db=plain, star=1) if p.origin == "charge_build"]
            with self.subTest(chip=chip):
                self.assertTrue(a)
                self.assertEqual(a, b)

    def test_locked_out_chip_has_no_lookahead(self):
        # 已占满两个流派时第三个流派的筹码不会出现，拿了也不改变之后的筹码池：前瞻记 0，不按它的职业分值推算
        owned = ("缓冲盾牌", "标靶")
        sc = self.scorer("姬梦朝", owned=owned, star=1, round=4, map="异变图书馆")
        for chip in ("紫色飞星", "ATM机", "电流剑"):
            with self.subTest(chip=chip):
                self.assertEqual(sc.lookahead(chip, owned), 0.0)
                self.assertEqual(sc.lookahead_reason(chip, owned), "")


class MechanicsTest(Base):
    """机制类：真梦梓的运动手环、赛克斯的减速和手持风扇、循环往复的等待回合和等级、大碗炖肉与半血打法。"""

    def test_mamushi_wristband(self):
        # 龙之咆哮不能直接满足运动手环的条件：没有角色专属规则，只有物理输出的通用加攻
        parts = self.parts("真梦梓", "运动手环", star=1)
        self.assertFalse(any(p.label == "角色专属规则" for p in parts))
        self.assertNotIn("运动手环", {c for r in self.db.hero_synergy["真梦梓"].rules if r.origin == "角色" for c in r.chips})

    def test_sykes_slow_and_fans(self):
        hs = self.db.hero_synergy["赛克斯"]
        self.assertIn("减速", hs.traits)
        self.assertIn("下次移动 -2", hs.why)
        own = {c: r.score for r in hs.rules if r.origin == "角色" for c in r.chips}
        self.assertEqual((own["标靶"], own["手持风扇-大"], own["手持风扇-小"], own["电击手套"]), (60, 30, 30, 40))
        # 电击手套在使用主动技能后触发，范围伤害也施加渊蚀印记：角色专属规则 +40，高于「技能依赖」的 +30，
        # 自动层最高的一条是角色专属规则，「技能依赖」按三成叠加
        gloves = self.parts("赛克斯", "电击手套", use_player=False)
        self.assertEqual([(p.origin, p.score) for p in gloves if p.label == "角色专属规则"], [("角色", 40)])
        self.assertEqual([(p.origin, p.score) for p in gloves if p.label == "其他特性"], [("技能依赖", 9)])
        top = [p for p in self.parts("赛克斯", "运动手环", map="决赛大赛场") if p.label == "特性" and p.score > 0]
        self.assertEqual([(p.origin, p.score) for p in top], [("减速", 50)])
        # 风扇的角色专属规则是 30，低于「标记受益」的通用分值，自动层最高的一条是「标记受益」
        fan = self.parts("赛克斯", "手持风扇-大", use_player=False)
        self.assertEqual([(p.origin, p.score) for p in fan if p.label == "特性"], [("标记受益", 45)])
        self.assertEqual(sum(p.score for p in fan if p.label in ("特性", "其他特性")), 59)

    def test_endless_delay_and_tier(self):
        # 升星、筹码商店拿到的再等 2 回合，任务拿到的等 3 回合；等级按给的时候预计的星数（当前星数 + 0.25 × 等待回合数）
        for source, delay in (("levelup", 2), ("shop", 2), ("task", 3)):
            self.assertEqual(self.scorer("邦妮", star=1, source=source).endless_delay(), delay)
        self.assertEqual(self.scorer("邦妮", star=0).endless_tiers(2), [(1, 1.0)])  # 0.5 星按 1 级
        self.assertEqual(self.scorer("邦妮", star=1).endless_tiers(2), [(1, 0.5), (2, 0.5)])
        self.assertEqual(self.scorer("邦妮", star=2, source="task").endless_tiers(3), [(2, 0.25), (3, 0.75)])
        self.assertEqual(self.scorer("邦妮", star=3).endless_tiers(2), [(3, 1.0)])
        # 1 星：按预计的星数插值，比按拿的时候的星数（star_rate 为 0）高；权重 0.3 不变
        self.assertEqual(self.db.rules["endless_cycle"]["weight"], 0.3)
        flat = self.with_rules("endless_cycle", star_rate=0.0)
        kw = dict(star=1, round=3, rerolls=3, map="魔法学院")
        now = self.labelled("邦妮", "循环往复", "循环往复", **kw)[0].score
        old_tier = [p for p in self.parts("邦妮", "循环往复", db=flat, **kw) if p.label == "循环往复"][0].score
        self.assertGreater(now, old_tier)
        # 同一局面，升星拿到的比任务拿到的早一回合给，额外那次三选一的时间折算更高
        task = self.labelled("邦妮", "循环往复", "循环往复", source="task", **kw)[0]
        self.assertIn("3 回合后获得 1 次筹码三选一", task.reason)

    def test_stew_breaks_half_hp(self):
        # 自己已持有磨刀石、肾上腺素-高效：大碗炖肉 -20（帕帕拉不参与，见「半血」特性）
        for owned in (("磨刀石",), ("肾上腺素-高效",)):
            combo = [p for p in self.labelled("墨影", "大碗炖肉", "组合", owned=owned) if p.origin == "半血筹码与大碗炖肉冲突"]
            self.assertEqual([(p.score, p.short) for p in combo], [(-20, f"与{owned[0]}冲突")])
        self.assertFalse([p for p in self.labelled("帕帕拉", "大碗炖肉", "组合", owned=("磨刀石",))
                          if p.origin == "半血筹码与大碗炖肉冲突"])
        # 反方向：已持有大碗炖肉时，候选的磨刀石、肾上腺素-高效同样 -20，帕帕拉不计
        for chip in ("磨刀石", "肾上腺素-高效"):
            with self.subTest(chip=chip):
                combo = [p for p in self.labelled("墨影", chip, "组合", owned=("大碗炖肉",))
                         if p.origin == "大碗炖肉与半血筹码冲突"]
                self.assertEqual([(p.score, p.short) for p in combo], [(-20, "与大碗炖肉冲突")])
                self.assertTrue(combo[0].reason.startswith("已持有「大碗炖肉」："), combo[0].reason)
                self.assertIn("该筹码需生命值不超过一半", combo[0].reason)
                self.assertFalse([p for p in self.labelled("帕帕拉", chip, "组合", owned=("大碗炖肉",))
                                  if p.origin == "大碗炖肉与半血筹码冲突"])
                # 没有大碗炖肉时不扣
                self.assertFalse([p for p in self.labelled("墨影", chip, "组合") if p.origin == "大碗炖肉与半血筹码冲突"])
        # 队友带「半血」特性（帕帕拉）：每名 -10，真人电脑都算；短句写缺点，整句从队友写起
        for bot in (False, True):
            mates = [p for p in self.labelled("美咲", "大碗炖肉", "队友", teammates=(Teammate("帕帕拉", bot),))
                     if p.score < 0 and "半血" in p.reason]
            with self.subTest(bot=bot):
                self.assertEqual([(p.score, p.short) for p in mates], [(-10, "与队友的半血打法冲突")])
                self.assertTrue(mates[0].reason.startswith("队友「帕帕拉」"), mates[0].reason)
        self.assertFalse([p for p in self.labelled("美咲", "大碗炖肉", "队友", teammates=(Teammate("墨影"),)) if p.score < 0])


class HealDiscountTest(Base):
    """美工刀：物理输出给美工刀的加分按之后获得治愈来源的概率折算（rules.toml [heal_knife] ③）。"""

    def test_discount_without_heal_source(self):
        st = dict(star=1, round=4)
        sc = self.scorer("邦妮", **st)
        parts = sc.parts("美工刀-锋利", (), 4)
        rule = next(p for p in parts if p.label == "特性" and p.origin == "物理输出")
        self.assertEqual(rule.score, 35)  # 特性规则本身照常列出
        disc = [p for p in parts if p.origin == HEAL_DISCOUNT_ORIGIN]
        self.assertEqual(len(disc), 1)
        p = heal_chance(sc, "美工刀-锋利", frozenset())
        self.assertTrue(0 < p < 1)
        self.assertAlmostEqual(disc[0].score, -35 * (1 - p))
        self.assertEqual((disc[0].label, disc[0].short), ("组合", HEAL_DISCOUNT_SHORT))
        self.assertIn(f"概率约 {p:.0%}", disc[0].reason)
        # 排在组合规则之后（组合规则的扣分仍是第一个「组合」）
        labels = [x.origin for x in self.parts("墨影", "美工刀-锋利", owned=("磨刀石",), star=1, round=4) if x.label == "组合"]
        self.assertEqual(labels, ["半血筹码与美工刀冲突", HEAL_DISCOUNT_ORIGIN])

    def test_full_with_heal_source_or_healer(self):
        for kw in (dict(owned=("缓冲盾牌",)), dict(owned=("医疗箱-紧急治疗",)), dict(teammates=(Teammate("潘大猛", True),)),
                   dict(teammates=(Teammate("姬梦朝"),))):
            with self.subTest(**kw):
                self.assertFalse([p for p in self.parts("邦妮", "美工刀-初级", star=1, round=4, **kw)
                                  if p.origin == HEAL_DISCOUNT_ORIGIN])
        # 队友不会治疗（邦妮）时照样折算
        self.assertTrue([p for p in self.parts("Z3000", "美工刀-初级", star=1, round=4, teammates=(Teammate("邦妮"),))
                         if p.origin == HEAL_DISCOUNT_ORIGIN])

    def test_only_physical_and_exclude_traits(self):
        # 不是物理输出角色没有这条特性加分，不折算
        self.assertFalse([p for p in self.parts("米米", "美工刀-初级", star=1, round=4) if p.origin == HEAL_DISCOUNT_ORIGIN])
        # 帕帕拉（半血）：潜能只在主动技能那一轮视为半血，美工刀照样要满血，没有治愈来源时和其他物理输出角色一样折算
        # （rules.toml [heal_knife] 的 exclude_traits 不写「半血」）
        for chip in KNIVES:
            with self.subTest(chip=chip):
                disc = [p for p in self.parts("帕帕拉", chip, star=1, round=4) if p.origin == HEAL_DISCOUNT_ORIGIN]
                self.assertEqual(len(disc), 1)
                self.assertLess(disc[0].score, 0)
        self.assertEqual(self.db.rules["heal_knife"]["exclude_traits"], [])
        # exclude_traits 写了的特性照旧不折算：只排除带这条特性的角色
        off = self.with_rules("heal_knife", exclude_traits=["半血"])
        self.assertFalse([p for p in self.parts("帕帕拉", "美工刀-初级", db=off, star=1, round=4)
                          if p.origin == HEAL_DISCOUNT_ORIGIN])
        self.assertTrue([p for p in self.parts("邦妮", "美工刀-初级", db=off, star=1, round=4)
                         if p.origin == HEAL_DISCOUNT_ORIGIN])
        # 玩家思路列入美工刀的角色按玩家思路，不折算。现在的默认推荐只在已持有治愈来源时列入美工刀，
        # 用测试里造的一段玩家思路（邦妮把美工刀-初级列为可选）
        ps = _merge_players("邦妮", [{"hero": "邦妮", "source": "攻略", "second": ["美工刀-初级"]}], self.fail)
        db = dataclasses.replace(self.db, player_synergy={**self.db.player_synergy, "邦妮": ps})
        self.assertTrue([p for p in self.parts("邦妮", "美工刀-初级", star=1, round=4) if p.origin == HEAL_DISCOUNT_ORIGIN])
        self.assertFalse([p for p in self.parts("邦妮", "美工刀-初级", db=db, star=1, round=4)
                          if p.origin == HEAL_DISCOUNT_ORIGIN])

    def test_no_future_picks_no_bonus(self):
        # 预计之后没有三选一时概率为 0，物理输出的加分全部折掉（星趴·梦想号困难及以上的进度上限 18，第 18 轮到了进度上限；
        # 3 星以后还没到进度上限时之后的三选一有下限，不估成 0）
        parts = self.parts("邦妮", "美工刀-锋利", star=3, round=18, map="星趴·梦想号")
        disc = [p for p in parts if p.origin == HEAL_DISCOUNT_ORIGIN]
        self.assertEqual([p.score for p in disc], [-35])
        self.assertIn("概率不到 1%", disc[0].reason)

    def test_knife_anticipation_unchanged(self):
        # 治愈来源预判美工刀（①）时，「会不会选美工刀」按不计治愈来源折算的总分判断：和关掉折算时相同
        off = self.with_rules("heal_knife", exclude_traits=["半血", "物理输出"])
        for hero in ("邦妮", "墨影", "Z3000"):
            for chip in ("医疗箱-紧急治疗", "医疗箱-完备治疗"):
                a = [p.score for p in self.parts(hero, chip, star=1, round=5) if p.label == "组合"]
                b = [p.score for p in self.parts(hero, chip, db=off, star=1, round=5) if p.label == "组合"]
                with self.subTest(hero=hero, chip=chip):
                    self.assertEqual(a, b)


class OpinionValuesTest(Base):
    """已定的观点类：分值照写。"""

    def test_speed(self):
        phys = self.rule_scores("物理输出")
        self.assertEqual([phys[c] for c in ("速度轮滑-初级", "速度轮滑-中级", "速度轮滑-高级")], [20, 40, 45])
        # 辅助定位的角色三种速度轮滑各 +15，帕露南不加；法术输出的赛克斯不带这个特性
        for hero, has in (("蓝海晴", True), ("阿尔", True), ("赛克斯", False), ("帕露南", False), ("邦妮", False)):
            shoes = {c for r in self.db.hero_synergy[hero].rules if r.origin == "辅助定位" for c in r.chips
                     if c.startswith("速度轮滑")}
            with self.subTest(hero=hero):
                self.assertEqual(shoes, {"速度轮滑-初级", "速度轮滑-中级", "速度轮滑-高级"} if has else set())
        blue = self.parts("蓝海晴", "速度轮滑-初级", use_player=False)
        self.assertEqual([(p.origin, p.score) for p in blue if p.label == "特性"], [("辅助定位", 15)])
        self.assertEqual([(p.origin, p.score) for p in self.parts("邦妮", "速度轮滑-初级", use_player=False)
                          if p.label == "特性"], [("物理输出", 20)])

    def test_transfer(self):
        support = self.rule_scores("辅助定位")
        self.assertEqual((support["ATM机"], support["友情徽章"]), (30, 20))
        self.assertEqual(self.rule_scores("通用")["节日红包"], 40)
        # 主力输出不加：邦妮的 ATM机没有「辅助定位」
        self.assertFalse(any("辅助定位" in p.origin for p in self.parts("邦妮", "ATM机", use_player=False)))
        red = [p for p in self.parts("邦妮", "节日红包", use_player=False, map="水乡古镇") if p.label == "特性"]
        self.assertEqual([(p.origin, p.score) for p in red], [("通用", 40)])

    def test_weak_chips(self):
        self.assertEqual(self.rule_scores("效果牌伤害")["书签"], 55)
        self.assertEqual(self.rule_scores("物理输出")["手电筒-强光"], 15)
        self.assertEqual(self.rule_scores("技能依赖")["飞羽手环"], 30)
        self.assertEqual(self.rule_scores("技能依赖")["大铜锣"], 30)
        # 电击手套在使用主动技能后触发，归「技能依赖」（不归「多出效果牌」），+30
        self.assertEqual(self.rule_scores("技能依赖")["电击手套"], 30)
        self.assertNotIn("电击手套", self.rule_scores("多出效果牌"))
        self.assertEqual((self.rule_scores("反击")["孔雀扇"], self.rule_scores("反击")["小鲤鱼玩偶"]), (40, 40))
        self.assertEqual(self.rule_scores("通用")["重生人偶"], 10)

    def test_map_chips(self):
        general = self.rule_scores("通用")
        self.assertEqual((general["贯穿之铳"], general["自适应核心"], general["鸳鸯玉佩"]), (20, 30, 15))
        # 只靠发放、不进三选一的筹码（granted）不作为候选给分
        self.assertNotIn("原初意识", general)
        snake = [p for p in self.labelled("邦妮", "小蛇玩偶", "组合", owned=("惊涛御守",), map="龙宫游乐园")]
        self.assertEqual([(p.origin, p.score) for p in snake], [("小蛇玩偶与惊涛御守", 25)])

    def test_survival_traits(self):
        traits = {h: self.db.hero_synergy[h].traits for h in ("姬梦枫", "橘雪莉", "真梦梓")}
        self.assertNotIn("需要生存", traits["姬梦枫"])
        self.assertIn("需要生存", traits["橘雪莉"])
        self.assertIn("需要生存", traits["真梦梓"])

    def test_extreme_difficulty(self):
        values = {"复仇之戟": 20, "诅咒之剑": 15, "三叉戟": 15}
        for chip, score in values.items():
            m = self.db.chips[chip].maps[0]
            parts = [p for p in self.labelled("阿尔", chip, "难度", map=m, difficulty_level=LEVEL_NIGHTMARE,
                                              difficulty_name="极限") if p.origin == "extreme"]
            with self.subTest(chip=chip):
                self.assertEqual([(p.score, p.short) for p in parts], [(score, EXTREME_SHORT)])
                self.assertTrue(parts[0].reason.startswith("极限难度"))
                # 疯狂、只知道噩梦及以上、普通难度时不加
                for kw in (dict(difficulty_level=LEVEL_NIGHTMARE, difficulty_name="疯狂"),
                           dict(difficulty_level=LEVEL_NIGHTMARE), dict(difficulty=NORMAL, difficulty_name="极限")):
                    self.assertFalse([p for p in self.labelled("阿尔", chip, "难度", map=m, **kw) if p.origin == "extreme"])

    def test_water_town_helmet(self):
        # 水乡古镇不给摩托头盔加「难度」分（含噩梦及以上的加分）；别的地图、地图待识别照常
        for chip in ("摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"):
            for kw in (dict(), dict(difficulty_level=LEVEL_NIGHTMARE)):
                with self.subTest(chip=chip, **kw):
                    self.assertEqual(self.labelled("美咲", chip, "难度", map="水乡古镇", **kw), [])
                    self.assertTrue(self.labelled("美咲", chip, "难度", map="魔法学院", **kw))
                    self.assertTrue(self.labelled("美咲", chip, "难度", **kw))
        self.assertTrue(self.labelled("美咲", "医疗箱-完备治疗", "难度", map="水乡古镇"))  # 只针对摩托头盔

    def test_combos_and_teammates(self):
        feather = self.labelled("美咲", "优雅之羽", "组合", owned=("安全气囊",))
        self.assertEqual([(p.origin, p.score) for p in feather], [("优雅之羽与安全气囊", 25)])
        self.assertEqual(self.labelled("蓝海晴", "优雅之羽", "组合", owned=("安全气囊",)), [])  # 只对物理输出
        ren = self.labelled("美咲", "优雅之羽", "队友", teammates=(Teammate("恋"),))
        self.assertEqual([(p.score, p.short) for p in ren], [(10, "配合队友：恋")])
        self.assertEqual(self.labelled("蓝海晴", "优雅之羽", "队友", teammates=(Teammate("恋"),)), [])
        for card in ("大背包", "忍术飞镖", "古老法杖", "电流核心"):
            sat = [p for p in self.labelled("梅加斯", "探天卫星", "组合", owned=(card,)) if p.origin == "探天卫星与出牌数"]
            with self.subTest(card=card):
                self.assertEqual([p.score for p in sat], [15])

    def test_bank_card_late(self):
        cfg = self.db.rules["income"]
        self.assertEqual((cfg["late"], cfg["late_factor"]),
                         ({"银行卡-余额少": 3, "银行卡-余额多": 7, "银行卡-用不完": 7}, 0.5))

        def income(hero, chip, rnd):
            """（收入折算那一项或 None，折算的基数，按剩余兑现次数的折算系数）。"""
            sc = self.scorer(hero, star=1, round=rnd)
            parts = sc.parts(chip, (), rnd)
            base = sum(p.score for p in parts if p.score > 0 and p.label != "成长分")
            every = cfg["reward_every"]
            f = reward_rounds_left(rnd, sc.cap, every) / reward_rounds_left(1, sc.cap, every)
            return next((p for p in parts if p.label == "收入折算"), None), base, f

        # 美咲（不带「经济」）：从那一轮起再减半，整句接 LATE_INCOME_NOTE；恋（带「经济」）：星币多用于转账给队友，
        # 不再减半，只按剩余兑现次数折算，也不写那一句
        self.assertNotIn(cfg["economy_trait"], self.db.hero_synergy["美咲"].traits)
        self.assertIn(cfg["economy_trait"], self.db.hero_synergy["恋"].traits)
        for hero, factor in (("美咲", cfg["late_factor"]), ("恋", 1.0)):
            for chip, start in cfg["late"].items():
                with self.subTest(hero=hero, chip=chip):
                    before, base, f = income(hero, chip, start - 1)
                    if before is None:  # 还没少兑现一次（第 2 轮）：不折算
                        self.assertLess(base * (1 - f), 0.5)
                    else:
                        self.assertAlmostEqual(-before.score, base * (1 - f))
                        self.assertNotIn("再减半", before.reason)
                    after, base, f = income(hero, chip, start)
                    self.assertGreater(base, 0)
                    self.assertAlmostEqual(-after.score, base * (1 - f * factor))
                    late = after.reason.endswith(LATE_INCOME_NOTE.format(round=start))
                    self.assertEqual(late, factor != 1.0, after.reason)
                    self.assertEqual("再减半" in after.reason, factor != 1.0, after.reason)

    def test_schools_and_komachi(self):
        # 蓝海晴的推荐流派只写星光、尽量保住；电流核心、永动机这条缩短冷却的路由带条件的角色专属规则体现
        self.assertEqual(self.db.hero_synergy["蓝海晴"].schools, ("星光",))
        self.assertIs(self.db.hero_synergy["蓝海晴"].keep_schools, True)
        tiers = with_player_layer(self.db).player_synergy["小町"].tiers  # 测试用的默认推荐档位
        self.assertEqual([tiers[c] for c in ("大背包", "额外电池", "电流核心", "探天卫星", "魔法箭袋")], ["first"] * 5)


class PlayerLayerTest(Base):
    """按攻略视频整理的玩家思路（同属「攻略」来源）：测试用的默认推荐档位（tests/support/player_layer.toml）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = with_player_layer(load_db())

    DRAFTS = {
        "赛克斯": (("星光", "治愈"), {"core": {"金色飞星", "紫色飞星"},
                                  "first": {"缓冲盾牌", "医疗箱-完备治疗", "大碗炖肉", "大背包"}}),
        "橘雪莉": (("治愈", "充能"), {"first": {"缓冲盾牌", "电流剑", "速度轮滑-中级", "速度轮滑-高级", "医疗箱-完备治疗",
                                             "安全气囊", "电磁炮", "永动机"}}),
        "远野汉娜": (("星光", "治愈"), {"first": {"ATM机", "友情徽章", "速度轮滑-中级", "速度轮滑-高级", "金色飞星",
                                               "紫色飞星", "额外电池"}}),
        "姬梦枫": (("治愈", "充能"), {"avoid": {"标靶"}}),
        "真梦梓": (("标记", "治愈"), {"first": {"缓冲盾牌", "标靶", "拳击手套-中级", "拳击手套-高级", "普通瞄具", "鹰眼瞄具",
                                             "额外电池", "速度轮滑-高级"}}),
        "照": (("充能", "标记"), {"first": {"额外电池", "肾上腺素-高效", "拳击手套-中级", "拳击手套-高级", "标靶", "电流剑",
                                         "速度轮滑-中级", "速度轮滑-高级", "磨刀石"}}),
    }

    def test_drafts(self):
        for hero, (schools, want) in self.DRAFTS.items():
            ps = self.db.player_synergy[hero]
            with self.subTest(hero=hero):
                self.assertEqual(ps.sources, (SOURCE_GUIDE,))
                self.assertEqual(ps.schools, schools)
                for tier, names in want.items():
                    self.assertEqual({c for c, t in ps.tiers.items() if t == tier}, names, tier)
                self.assertTrue(ps.note)
                for word in ("视频", "wiki", "攻略："):
                    self.assertNotIn(word, ps.note)

    def test_conditional_knives(self):
        # 已持有治愈来源时美工刀列为首选（橘雪莉、姬梦枫、真梦梓 +80）或次选（照、帕露南 +40）；没有时按角色特性
        for hero, score in (("橘雪莉", 80), ("姬梦枫", 80), ("真梦梓", 80), ("照", 40), ("帕露南", 40)):
            for knife in KNIVES:
                with self.subTest(hero=hero, knife=knife):
                    sc = self.scorer(hero, owned=("缓冲盾牌",))
                    self.assertEqual(sc.player_score(knife, ("缓冲盾牌",), None), score)
                    self.assertIsNone(self.scorer(hero).player_score(knife, (), None))

    def test_teru_rules(self):
        sc = self.scorer("照")
        self.assertEqual(sc.player_score("电磁炮", (), None), -20)
        self.assertEqual(self.scorer("照", owned=("永动机",)).player_score("电磁炮", ("永动机",), None), 40)
        self.assertEqual(self.scorer("照", round=2).player_score("银行卡-余额少", (), 2), 40)
        self.assertIsNone(self.scorer("照", round=3).player_score("银行卡-余额少", (), 3))
        self.assertEqual(self.scorer("照", round=5).player_score("星币锤", (), 5), 40)
        self.assertIsNone(self.scorer("照", round=6).player_score("银行卡-用不完", (), 6))
        # 紫色银行卡视频没说，严格上位替代蓝色：和金色同一条（第 5 轮以前）
        self.assertEqual(self.scorer("照", round=5).player_score("银行卡-余额多", (), 5), 40)

    def test_series_consistent_all_rounds(self):
        # 同系列严格上位替代的高一级不低于低一级（tests/test_series.py 只查轮次未知时），这里按攻略视频写的玩家思路逐轮查
        strict = [p for p in self.db.series if p.strict]
        for hero in (*self.DRAFTS, "帕露南", "凛", "小町"):
            for rnd in (None, *range(1, 17)):
                sc = self.scorer(hero, round=rnd)
                for p in strict:
                    low = sc.player_score(p.low, (), rnd)
                    if low is None:
                        continue
                    high = sc.player_score(p.high, (), rnd)
                    with self.subTest(hero=hero, round=rnd, pair=(p.low, p.high)):
                        self.assertIsNotNone(high)
                        self.assertGreaterEqual(high, low)

    def test_hanna_marks(self):
        sc = self.scorer("远野汉娜")
        for chip in ("标靶", "标记喷罐", "手持风扇-小", "手持风扇-大", "普通瞄具", "鹰眼瞄具", "魔法箭袋", "忍术飞镖"):
            with self.subTest(chip=chip):
                self.assertEqual(sc.player_score(chip, (), None), -30)

    def test_parunan(self):
        ps = self.db.player_synergy["帕露南"]
        self.assertEqual(ps.sources, (SOURCE_GUIDE,))
        self.assertEqual(ps.schools, ("星光", "治愈"))
        tiers = ps.tiers
        # 没有必选档：ATM机和缓冲盾牌同为一级优先，与友情徽章、金色飞星同列推荐
        self.assertEqual({c for c, t in tiers.items() if t == "core"}, set())
        self.assertEqual({c for c, t in tiers.items() if t == "first"}, {"ATM机", "缓冲盾牌", "友情徽章", "金色飞星"})
        self.assertEqual({c for c, t in tiers.items() if t == "second"},
                         {"大背包", "可口糖果", "紫色飞星", "大碗炖肉", "医疗箱-完备治疗", "医疗箱-紧急治疗"})
        self.assertNotIn("速度轮滑-高级", tiers)  # 视频：她不缺移动
        # 原文写成书面的完整说法，不写来源对比
        self.assertTrue(ps.note.startswith("推荐星光加治愈：一级优先 ATM机、缓冲盾牌；"), ps.note)
        for word in ("视频", "wiki", "攻略：", "核心筹码"):
            self.assertNotIn(word, ps.note)
        atm = next(p for p in self.parts("帕露南", "ATM机") if p.label == "玩家思路")
        self.assertEqual((atm.score, atm.reason, atm.short),
                         (self.db.rules["synergy"]["first"], "筹码方案「默认推荐」：推荐", "筹码方案：推荐"))

    def test_sykes_core_first(self):
        # 必拿的两张飞星对上两个无关的蓝色筹码时首推（没有刷新次数）
        adv = advise(self.db, State(hero="赛克斯", star=2, round=5, rerolls=0), ["会员推荐信", "金色飞星", "夹心饼干-一般"])
        self.assertEqual(adv.best, "金色飞星")


class SeriesNearTest(Base):
    """同系列两张总分相差不到 rules.toml [advice] series_near（3）时稀有度高的一张排在前面：
    只改先后、不改分数，所有角色适用；推荐卡片的先后、首推、推荐度和推荐全文彼此一致。"""

    # 低一级（夹心饼干-可口，紫）排在候选的第一个：按候选顺序时它在前；会员推荐信远低于两者
    OFFER = ["夹心饼干-可口", "会员推荐信", "夹心饼干-美味"]

    def advise_gap(self, gap, db=None, reroll=None, **kw):
        """小町 2 星第 8 轮：把前瞻改成固定值，让夹心饼干-可口的总分正好比 -美味 高 gap。
        reroll 给了时把「刷新」的值固定为「可口的总分 + reroll」（刷新的概率照常计算）。"""
        db = db or self.db
        st = State(hero="小町", star=2, round=8, rerolls=0 if reroll is None else 3, **kw)
        sc = Scorer(db, st)
        imm = {n: sc.score(n, (), 8) for n in self.OFFER}
        la = {"夹心饼干-可口": 0.0, "夹心饼干-美味": imm["夹心饼干-可口"] - imm["夹心饼干-美味"] - gap, "会员推荐信": -40.0}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(Scorer, "lookahead", lambda self, name, owned: la.get(name, 0.0)))
            if reroll is not None:
                value = imm["夹心饼干-可口"] + reroll
                stack.enter_context(mock.patch.object(
                    Scorer, "reroll", lambda self, offer, best: RerollEval(
                        expected=value, cost=0.0, value=value, p_better=self.reroll_p_better(offer, best))))
            return advise(db, st, list(self.OFFER))

    @staticmethod
    def order(adv):
        """推荐卡片上的先后（ui.text.ordered_options：推荐度从高到低，平局时按候选顺序、刷新在后）。"""
        from advisor.ui import text as ui_text
        return [REROLL if item is adv.reroll else item.chip.name for _, _, item in ui_text.ordered_options(adv)]

    def softmax(self, adv):
        """不调整先后时各选项的推荐度（按总分和刷新的值算 softmax）：选项名 → 推荐度。"""
        t = self.db.rules["softmax"]["temperature"]
        values = {e.chip.name: e.total for e in adv.evals if not e.superseded_by}
        if adv.reroll is not None:
            values[REROLL] = adv.reroll.value
        top = max(values.values())
        weights = {k: math.exp((v - top) / t) for k, v in values.items()}
        s = sum(weights.values())
        return {k: w / s for k, w in weights.items()}

    def test_threshold_in_rules(self):
        self.assertEqual(self.db.rules["advice"]["series_near"], 3)

    def test_gap_below_threshold(self):
        # 差 2.9：稀有度高的夹心饼干-美味排在前面、是首推；两张的推荐度对调，分数和其余选项的推荐度不变
        adv = self.advise_gap(2.9)
        ev = {e.chip.name: e for e in adv.evals}
        self.assertAlmostEqual(ev["夹心饼干-可口"].total - ev["夹心饼干-美味"].total, 2.9)
        self.assertEqual(self.order(adv), ["夹心饼干-美味", "夹心饼干-可口", "会员推荐信"])
        self.assertEqual((adv.best, adv.evals[adv.top].chip.name), ("夹心饼干-美味", "夹心饼干-美味"))
        plain = self.softmax(adv)
        self.assertAlmostEqual(ev["夹心饼干-美味"].percent, plain["夹心饼干-可口"])
        self.assertAlmostEqual(ev["夹心饼干-可口"].percent, plain["夹心饼干-美味"])
        self.assertAlmostEqual(ev["会员推荐信"].percent, plain["会员推荐信"])
        shown = shown_percents(adv)
        self.assertGreaterEqual(shown[2], shown[0])  # 显示的整数百分数：美味不低于可口
        # 对比行的悬停说明写明排在前面的原因；可口那一边不加
        near = SERIES_NEAR_TIP.format(x="夹心饼干-美味")
        self.assertTrue(ev["夹心饼干-美味"].versus and ev["夹心饼干-美味"].versus[0][2].endswith(near))
        self.assertFalse(any(near in t for _, _, t in ev["夹心饼干-可口"].versus))
        # 推荐全文：先后、推荐度和推荐卡片相同，首推一行写美味
        from types import SimpleNamespace

        from advisor.ui import text as ui_text
        full = ui_text.advice_text(self.db, SimpleNamespace(advice=adv, problems=[]))
        self.assertIn("推荐：夹心饼干-美味", full)
        self.assertLess(full.index("  夹心饼干-美味  总分"), full.index("  夹心饼干-可口  总分"))
        self.assertIn(near.rstrip("。"), full)

    def test_gap_above_threshold(self):
        # 差 3.1：照分数，可口在前
        adv = self.advise_gap(3.1)
        self.assertEqual(self.order(adv), ["夹心饼干-可口", "夹心饼干-美味", "会员推荐信"])
        self.assertEqual(adv.best, "夹心饼干-可口")
        ev = {e.chip.name: e for e in adv.evals}
        plain = self.softmax(adv)
        for name in self.OFFER:
            self.assertAlmostEqual(ev[name].percent, plain[name])
        self.assertFalse(any("排在前面" in t for e in adv.evals for _, _, t in e.versus))

    def test_rule_off(self):
        # series_near 写 0 时不调整（差 2.9 也照分数）
        rules = dict(self.db.rules)
        rules["advice"] = {**rules["advice"], "series_near": 0}
        adv = self.advise_gap(2.9, db=dataclasses.replace(self.db, rules=rules))
        self.assertEqual(self.order(adv)[:2], ["夹心饼干-可口", "夹心饼干-美味"])

    def test_exact_tie(self):
        # 总分相同：推荐度相同时按候选顺序会把可口排在前面，高一级多记一点推荐度，仍排在前面
        adv = self.advise_gap(0.0)
        ev = {e.chip.name: e for e in adv.evals}
        self.assertEqual(self.order(adv)[:2], ["夹心饼干-美味", "夹心饼干-可口"])
        self.assertEqual(adv.best, "夹心饼干-美味")
        self.assertAlmostEqual(ev["夹心饼干-美味"].percent, ev["夹心饼干-可口"].percent, places=6)

    def test_reroll_between_keeps_score_order(self):
        # 刷新的值夹在两张中间：对调会连带改变刷新和两张的先后，不对调
        adv = self.advise_gap(2.9, reroll=-1.0)
        self.assertEqual(self.order(adv)[:3], ["夹心饼干-可口", REROLL, "夹心饼干-美味"])
        self.assertEqual(adv.best, "夹心饼干-可口")

    def test_reroll_above_compares_with_front_chip(self):
        # 刷新排第一：两张照样对调，「刷新」比较的对象和概率跟着排在前面的美味（卡片上排在最前的筹码）
        adv = self.advise_gap(2.9, reroll=5.0)
        self.assertEqual(self.order(adv)[:3], [REROLL, "夹心饼干-美味", "夹心饼干-可口"])
        self.assertEqual(adv.best, REROLL)
        top = adv.evals[adv.top]
        self.assertEqual(top.chip.name, "夹心饼干-美味")
        sc = Scorer(self.db, adv.state, offer=self.OFFER)
        self.assertEqual(adv.reroll.p_better, sc.reroll_p_better(self.OFFER, top.total))

    def test_reroll_below(self):
        adv = self.advise_gap(2.9, reroll=-10.0)
        self.assertEqual(self.order(adv)[:3], ["夹心饼干-美味", "夹心饼干-可口", REROLL])
        self.assertEqual(adv.best, "夹心饼干-美味")

    def test_lifted_chip_not_swapped(self):
        # 队友刚需提到第二位的那张不参与对调（它的理由写明了「排在第二位」）。按总分判断：可口在前、美味紧随其后
        adv = self.advise_gap(2.9)
        active = [e for e in adv.evals if not e.superseded_by]
        low = next(e for e in adv.evals if e.chip.name == "夹心饼干-可口")
        high = next(e for e in adv.evals if e.chip.name == "夹心饼干-美味")
        self.assertEqual(series_near(self.db, adv.evals, active, None, None), [(high, low)])
        self.assertEqual(series_near(self.db, adv.evals, active, None, high), [])
        self.assertEqual(series_near(self.db, adv.evals, active, None, low), [])

    def test_scheme_wording(self):
        """自己的方案里两张都未标记时同样稀有度高的排前（engine.scheme_rank._series_near_scheme），卡片上不写推荐度：排到前面那张的
        对比行悬停末尾写「两张按角色特性计算的结果接近，稀有度较高的「X」排在前面。」，不写「推荐度」；默认推荐照旧写
        「两张的推荐度接近，……」（test_gap_below_threshold）。"""
        scheme = Plan(name="测试", hero="小町", tiers=(("大背包", "first"),))  # 两张夹心饼干都没标
        st = State(hero="小町", star=2, round=8, rerolls=0)
        sc = Scorer(self.db, st, scheme=scheme)
        imm = {n: sc.score(n, (), 8) for n in self.OFFER}
        la = {"夹心饼干-可口": 0.0, "夹心饼干-美味": imm["夹心饼干-可口"] - imm["夹心饼干-美味"] - 2.9, "会员推荐信": -40.0}
        with mock.patch.object(Scorer, "lookahead", lambda self, name, owned: la.get(name, 0.0)):
            adv = advise(self.db, st, list(self.OFFER), scheme=scheme)
        ev = {e.chip.name: e for e in adv.evals}
        self.assertAlmostEqual(ev["夹心饼干-可口"].total - ev["夹心饼干-美味"].total, 2.9)
        self.assertEqual([adv.evals[k].chip.name for k in adv.ranking.order if k >= 0][:2], ["夹心饼干-美味", "夹心饼干-可口"])
        near = SERIES_NEAR_TIP_SCHEME.format(x="夹心饼干-美味")
        self.assertEqual(near, "两张按角色特性计算的结果接近，稀有度较高的「夹心饼干-美味」排在前面。")
        tips = [t for _, _, t in ev["夹心饼干-美味"].versus]
        self.assertTrue(tips and tips[0].endswith(near), tips)
        self.assertFalse(any("推荐度" in t for t in tips))
        self.assertFalse(any(near in t for _, _, t in ev["夹心饼干-可口"].versus))

    def test_strict_pair_unaffected(self):
        # 严格上位替代的一对：低一级不参与比较、排在最后，不受这条规则影响
        adv = advise(self.db, State(hero="邦妮", star=1, round=4, rerolls=0), ["拳击手套-初级", "拳击手套-中级", "会员推荐信"])
        ev = {e.chip.name: e for e in adv.evals}
        self.assertEqual(ev["拳击手套-初级"].superseded_by, "拳击手套-中级")
        self.assertEqual(self.order(adv)[-1], "拳击手套-初级")


class DataTest(Base):
    def test_trait_rule_exclude(self):
        # 特性规则的 exclude：载入时就不收进这些角色的自动层
        rule = next(r for r in self.db.trait_rules["辅助定位"] if "速度轮滑-中级" in r.chips)
        self.assertEqual(rule.exclude, {"帕露南"})
        self.assertNotIn(rule, self.db.hero_synergy["帕露南"].rules)
        self.assertIn(rule, self.db.hero_synergy["蓝海晴"].rules)
        self.assertEqual(self.db.warnings, [])


if __name__ == "__main__":
    unittest.main()
