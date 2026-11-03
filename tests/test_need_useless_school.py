"""队友刚需排第二的前提「不会把自己锁进对自己没用的流派」：
只有拿了以后流派名额占满、有用的流派（默认推荐是推荐流派，按方案计算时是列进方案的流派）再也进不去，才算锁进没用的流派、
才不提；默认推荐和方案模式同一个口径。首推不变，只影响第二、三位。

不看拿了以后还有没有空位的话：默认推荐里流派不在推荐流派里就一律算没用（没有推荐流派的角色永远提不上），方案模式里
不在列进方案的流派里一律算没用。下面每条写明两种口径下的先后。
"""
import unittest
from dataclasses import replace

from advisor.data import NORMAL, load_db
from advisor.engine import NEED_ORIGIN, REROLL, REROLL_SLOT, Plan, Scorer, advise
from advisor.pool import State, Teammate


def lifted(adv) -> list[str]:
    return [e.chip.name for e in adv.evals if any(p.origin == NEED_ORIGIN for p in e.parts)]


def order(adv) -> list[str]:
    """卡片上的先后（默认推荐按推荐度，平局时候选在前、刷新在后；方案按 Advice.ranking）。"""
    if adv.ranking is not None:
        return [REROLL if k == REROLL_SLOT else adv.evals[k].chip.name for k in adv.ranking.order]
    rows = [(e.percent, i, e.chip.name) for i, e in enumerate(adv.evals)]
    if adv.reroll is not None:
        rows.append((adv.reroll.percent, len(adv.evals), REROLL))
    return [name for _, _, name in sorted(rows, key=lambda r: (-r[0], r[1]))]


class DefaultAdviceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_hero_without_school_lifted(self):
        # 没有推荐流派的角色、真人队友邦妮刚需标记：普通瞄具本身 93 分，拿了以后还有空位
        # 一律算没用时 拳击手套-高级、精品剑盾、普通瞄具、刷新；看空位时 拳击手套-高级、普通瞄具、精品剑盾、刷新
        # 适配表里每个角色都写了推荐流派：这里把美咲、墨影的推荐流派去掉来测
        heroes = ("美咲", "墨影")
        db = replace(self.db, hero_synergy={h: replace(s, schools=(), keep_schools=False) if h in heroes else s
                                            for h, s in self.db.hero_synergy.items()})
        mates = (Teammate("邦妮"), Teammate("凛", bot=True), Teammate("米米", bot=True))
        offer = ["拳击手套-高级", "精品剑盾", "普通瞄具"]
        for hero in heroes:
            with self.subTest(hero=hero):
                st = State(hero=hero, star=1, round=4, map="魔法学院", rerolls=3, teammates=mates)
                self.assertEqual(Scorer(db, st).pref_schools, ())
                adv = advise(db, st, offer)
                self.assertEqual(lifted(adv), ["普通瞄具"])
                self.assertEqual(order(adv), ["拳击手套-高级", "普通瞄具", "精品剑盾", REROLL])
                self.assertEqual(adv.best, "拳击手套-高级")

    def test_last_slot_keeps_recommended_school(self):
        # 阿兰娜（推荐流派治愈、标记，只保治愈）已进入治愈：拿普通瞄具占满流派名额（治愈、标记），治愈已经在手上，不算锁进没用的流派
        # 一律算没用时 拳击手套-高级、精品剑盾、刷新、普通瞄具；看空位时 拳击手套-高级、普通瞄具、精品剑盾、刷新
        mates = (Teammate("邦妮"), Teammate("吉尔"), Teammate("芬妮"))
        st = State(hero="阿兰娜", star=3, round=8, owned=("维生素药丸", "缓冲盾牌", "书签"), rerolls=5, teammates=mates)
        adv = advise(self.db, st, ["拳击手套-高级", "精品剑盾", "普通瞄具"])
        self.assertEqual(lifted(adv), ["普通瞄具"])
        self.assertEqual(order(adv), ["拳击手套-高级", "普通瞄具", "精品剑盾", REROLL])
        # 已进入星光（不是推荐流派）时拿它，治愈再也进不去：两种口径都不提；阿兰娜要保住治愈，普通瞄具另扣分，排到刷新后面
        strayed = replace(st, owned=("紫色飞星", "书签"))
        adv = advise(self.db, strayed, ["拳击手套-高级", "精品剑盾", "普通瞄具"])
        self.assertEqual(lifted(adv), [])
        self.assertEqual(order(adv), ["拳击手套-高级", "精品剑盾", REROLL, "普通瞄具"])

    def test_two_schools_one_lost(self):
        # 两个推荐流派只进入了一个：赛克斯（测试用的默认推荐档位，推荐流派星光、治愈）已进入治愈，拿标记喷罐占满流派名额
        # （治愈、标记），星光本局再也进不去，同样算锁进没用的流派：标记喷罐本身够推荐线、排第三也不提（两种口径都不提）
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        st = State(hero="赛克斯", star=3, round=9, owned=("可口糖果",), rerolls=4, teammates=(Teammate("凛"),),
                   map_candidates=("异变图书馆", "魔法学院", "星趴·梦想号", "水乡古镇"), tasks_done=0, difficulty=NORMAL)
        offer = ["标记喷罐", "夹心饼干-美味", "手持风扇-大"]
        scorer = Scorer(db, st, offer=offer)
        self.assertEqual(scorer.pref_schools, ("星光", "治愈"))
        self.assertTrue(scorer.locks_useless_school("标记喷罐"))
        adv = advise(db, st, offer)
        self.assertEqual(lifted(adv), [])
        # 剩 4 次刷新、之后约 1.4 次三选一：按 2 次留、再多留 1 次以防估少，多出来的 1 次不计代价、只刷一次，不如
        # 手持风扇-大：刷新排第二（只影响刷新的位置）
        self.assertEqual((adv.reroll.kept, adv.reroll.spare, adv.reroll.chain), (3, 1, 1))
        self.assertEqual(order(adv), ["手持风扇-大", REROLL, "标记喷罐", "夹心饼干-美味"])


class SchemeTest(unittest.TestCase):
    """方案只列了治愈（缓冲盾牌标了可选，没有目标流派，不扣锁定保护）：同一个口径，有用的流派是列进方案的流派。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.plan = Plan(name="测试方案", hero="阿兰娜", tiers=(("缓冲盾牌", "second"),))
        cls.st = State(hero="阿兰娜", star=1, round=4, map="异变图书馆", teammates=(Teammate("邦妮"),), rerolls=2)
        cls.offer = ["拳击手套-高级", "速度轮滑-高级", "普通瞄具"]

    def test_room_left_lifted(self):
        # 还没进入流派：拿标靶以后还有空位、不耽误治愈
        # 一律算没用时 拳击手套-高级、速度轮滑-高级、标靶、刷新；看空位时 拳击手套-高级、标靶、速度轮滑-高级、刷新
        adv = advise(self.db, self.st, ["拳击手套-高级", "速度轮滑-高级", "标靶"], scheme=self.plan)
        self.assertEqual(lifted(adv), ["标靶"])
        self.assertEqual(order(adv), ["拳击手套-高级", "标靶", "速度轮滑-高级", REROLL])

    def test_listed_school_lost_not_lifted(self):
        # 已进入方案外的星光：拿了以后名额占满，治愈再也进不去，两种口径都不提
        st = replace(self.st, owned=("紫色飞星",))
        self.assertTrue(Scorer(self.db, st, offer=self.offer, scheme=self.plan).locks_useless_school("普通瞄具"))
        adv = advise(self.db, st, self.offer, scheme=self.plan)
        self.assertEqual(lifted(adv), [])
        self.assertEqual(order(adv)[:3], self.offer)

    def test_no_listed_school_never_blocks(self):
        # 方案一个流派都没列：哪个流派都不算没用（两种口径相同）
        st = replace(self.st, owned=("紫色飞星",))
        empty = Plan(name="测试方案", hero="阿兰娜", tiers=(("书签", "avoid"),))
        self.assertFalse(Scorer(self.db, st, offer=self.offer, scheme=empty).locks_useless_school("普通瞄具"))


if __name__ == "__main__":
    unittest.main()
