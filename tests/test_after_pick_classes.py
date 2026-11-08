"""推算「拿了这个筹码以后」时游戏内置推荐分里的职业部分（标签分）也按拿了以后的职业算。

选了美工刀以后获得治愈来源的概率、开套预判里获得配套筹码的概率按「已有筹码 + 这一张」给之后的候选打分：关键词分、流派、
组合按拿了以后算，标签分也一样，不按现在的职业（Scorer.classes）算，这样才和游戏到那时打角标的算法一致。

例：潘大猛（辅助 60、生存 60）2 星第 9 轮，已有拳击手套-中级、拳击手套-初级、优雅之羽，现在不属于任何职业；拿了电流剑
（攻击 +30）以后属于攻击。配套筹码电磁炮带攻击标签：内置分计入总分时（[official] scale 0.5）拿了以后它的分值多 20
（标签分 40 × 0.5），够到推荐线，算进「之后会选的配套筹码」，电流剑的开套预判从「之后获得配套筹码 43%」（标签分按现在的
职业算）变成 50%（总分多约 0.47），首推和显示的推荐度不变。现在内置分不计入总分（scale 0）：标签分不影响打分，这一条用
scale 0.5 的资料库副本查；公式的原值照样按拿了以后的职业算。
"""
import dataclasses
import unittest
from unittest import mock

from advisor.data import load_db
from advisor.engine import Scorer, advise, hero_classes, shown_percent_texts
from advisor.engine import parts
from advisor.pool import State

OWNED = ("拳击手套-中级", "拳击手套-初级", "优雅之羽")
OFFER = ["友情徽章", "银行卡-余额少", "电流剑"]


class AfterPickClassesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.st = State(hero="潘大猛", star=2, round=9, owned=OWNED, rerolls=4)

    def test_classes_follow_assumed_owned(self):
        db = self.db
        self.assertEqual(hero_classes(db, "潘大猛", OWNED), frozenset())
        self.assertEqual(hero_classes(db, "潘大猛", (*OWNED, "电流剑")), frozenset({"攻击"}))
        sc = Scorer(db, self.st, offer=OFFER)
        now = [p.label for p in sc.official_parts("电磁炮", OWNED, 9, scale=1.0)]
        after = {p.label: p.score for p in sc.official_parts("电磁炮", (*OWNED, "电流剑"), 9, scale=1.0)}
        self.assertNotIn("标签分", now)  # 按现在的已有筹码算时不变
        self.assertEqual(after.get("标签分"), db.rules["official"]["tag"])  # 按拿了以后的职业算：有标签分（公式的原值）

    def test_school_anticipation_follows(self):
        # 内置分照旧计入的资料库副本：电磁炮要靠标签分才够推荐线，现在内置分不计入总分
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in self.db.rules.items()}
        rules["official"]["scale"] = 0.5
        db = dataclasses.replace(self.db, rules=rules)
        adv = advise(db, self.st, OFFER)
        sword = adv.evals[2]
        combo = next(p for p in sword.parts if p.origin == "school_build")
        self.assertEqual(combo.short, "之后获得配套筹码 50%")
        self.assertAlmostEqual(sword.total, 8.26, places=2)
        self.assertEqual(adv.best, "友情徽章")
        # 剩 4 次刷新、之后约 2.5 次三选一：按 3 次留、再多留 1 次以防估少，4 次都留着，没有多出来的，刷新按公式扣代价（3%）
        self.assertEqual((adv.reroll.kept, adv.reroll.spare), (4, 0))
        self.assertEqual(shown_percent_texts(adv), {0: "96%", 1: "<1%", 2: "1%", -1: "3%"})
        # 标签分按现在的职业算时，电磁炮够不到推荐线，不算进配套筹码
        with mock.patch.object(parts, "_classes_with", lambda sc, owned: sc.classes):
            old = advise(db, self.st, OFFER)
        before = next(p for p in old.evals[2].parts if p.origin == "school_build")
        self.assertEqual(before.short, "之后获得配套筹码 43%")
        # 两份资料的其余分项相同：标签分按现在的职业算时少 0.47 左右（总分还随其他筹码的分项小幅变动）
        self.assertAlmostEqual(sword.total - old.evals[2].total, 0.47, delta=0.02)
        self.assertEqual((old.best, shown_percent_texts(old)), (adv.best, shown_percent_texts(adv)))


if __name__ == "__main__":
    unittest.main()
