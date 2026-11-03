"""按筹码方案计算时，「刷新」按最多连续刷新几次、刷到满意为止估值的递推按每张候选地图分别做、再平均，
和默认推荐（Scorer.reroll）同一个做法：连续刷新刷出来的都是本局那一张地图的筹码池。几张
地图的级数分布要是先混在一起再递推，就等于假设每刷一次都可能换一张地图。

只在「地图待识别、本次之后预计没有三选一、还剩两次以上刷新」时有差别：刷新那一行「预计优于 / 相当 / 预计不如」的结论
在少见的局面里会变。地图已知、只刷新一次时逐位不变。
"""
import unittest

from advisor.data import load_db
from advisor.engine import REROLL, REROLL_SLOT, VERDICT_BETTER, Plan, advise
from advisor.pool import State

# 橘雪莉 3 星第 14 轮、进度条读到走到头（第 16 格，进度上限 16：之后没有三选一；3 星以后还没到进度上限时有下限，不算最后
# 一次）、还剩 4 次刷新，地图在御魂庆典和水乡古镇之间待识别；方案把御魂庆典的专属筹码贯穿之铳标了必选，两张地图刷出必选的
# 机会差得多
PLAN = Plan(name="测试方案", hero="橘雪莉", tiers=(("贯穿之铳", "core"), ("手持风扇-小", "first"),
                                              ("医疗箱-紧急治疗", "second"), ("电磁炮", "second")))
OWNED = ("可口糖果", "速度轮滑-初级", "小猪存钱罐", "磨刀石", "手持风扇-大", "缓冲盾牌")
OFFER = ["医疗箱-紧急治疗", "摩托头盔-一般", "忍术飞镖"]
MAPS = ("御魂庆典", "水乡古镇")


class SchemeRerollPerMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def view(self, **kw):
        st = State(hero="橘雪莉", star=3, round=14, owned=OWNED, rerolls=4, progress_cap=16, progress=16, **kw)
        adv = advise(self.db, st, OFFER, scheme=PLAN)
        return adv, adv.ranking.reroll

    def test_average_of_each_map(self):
        # 两张候选地图时的平均级数 = 两张地图各自（地图已知时）的平均级数取平均
        _, both = self.view(map_candidates=MAPS)
        each = [self.view(map=m)[1] for m in MAPS]
        self.assertEqual(both.chain, 4)
        self.assertEqual(both.cost, 0.0)
        self.assertAlmostEqual(both.expected, sum(v.expected for v in each) / 2, places=12)

    def test_verdict(self):
        # 和排在最前的医疗箱-紧急治疗（可选，2 级）比：两张地图各自递推，御魂庆典 2.57 级（刷得出必选的贯穿之铳；刷新后的
        # 最左格按职业权重出、池按职业标签建）、水乡古镇 1.58 级，平均 2.07 级，差 +0.07，「预计优于」，刷新排在它前面
        adv, view = self.view(map_candidates=MAPS)
        self.assertEqual(adv.evals[view.compare].chip.name, "医疗箱-紧急治疗")
        self.assertEqual(view.level, 2)
        each = [self.view(map=m)[1].expected for m in MAPS]
        self.assertEqual([round(x, 2) for x in each], [2.57, 1.58])
        self.assertAlmostEqual(view.expected, 2.0739, places=4)
        self.assertEqual(view.verdict, VERDICT_BETTER)
        order = [REROLL if k == REROLL_SLOT else adv.evals[k].chip.name for k in adv.ranking.order]
        self.assertEqual(order, [REROLL, "医疗箱-紧急治疗", "忍术飞镖", "摩托头盔-一般"])


if __name__ == "__main__":
    unittest.main()
