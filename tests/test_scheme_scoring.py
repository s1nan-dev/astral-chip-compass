"""筹码方案的打分：引擎按自己的方案计算（advise 的 scheme）、Session 按当前角色的方案算和切换后立即重算、推荐日志、
命令行的推荐全文。

规则见 advisor/engine/__init__.py 开头的说明和 data/rules.toml [scheme]
（自己的方案和默认推荐互斥、必选推荐的顺位、刷新什么时候排第一、同系列按档位、流派限制和软锁、走偏以后、极端的方案组合）。
选「默认推荐」时一条都不能变：这里只查几处不变量，完整的对照（推荐日志、测试里的实战局面、随机局面）另用基准工具比。
"""
import dataclasses
import io
import json
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import cli, engine
from advisor.data import LEVEL_NIGHTMARE, TIERS, load_db
from advisor.engine import (
    BASIS_TIER,
    BASIS_UNMARKED,
    CORE_LEAD,
    LOSS_NOW,
    LOSS_ONE_LEFT,
    LOSS_TIP,
    LOST_CORE,
    LOST_FIRST,
    MOVE_AHEAD,
    MOVE_BEHIND,
    MOVE_LAST,
    MOVE_LEAD,
    MOVE_MIXED,
    MOVE_NEAR,
    NEED_ORIGIN,
    NOTE_LOST,
    NOTE_STRAYED,
    PLACE_LOCKED,
    PLACE_MARKED,
    PLACE_STRAYED,
    PLACE_UNMARKED,
    REROLL,
    REROLL_SLOT,
    SCHEME_CORE_ORIGIN,
    SCHEME_LABEL,
    SCHEME_LOCK_ORIGIN,
    SCHEME_LOCKED_ORIGIN,
    SCHEME_TARGET_ORIGIN,
    SCHEME_TIER_ORIGINS,
    VERDICT_BETTER,
    VERDICT_EVEN,
    VERDICT_EVEN_BETTER,
    VERDICT_WORSE,
    Part,
    Place,
    Plan,
    Scorer,
    advise,
    place_text,
    plan_of,
    shown_percent_texts,
    shown_percents,
)
from advisor.engine.context import harmonic
from advisor.engine.parts import default_player_match, income_part
from advisor.engine.reroll import chance_before
from advisor.engine.scheme_rank import lost_text, place_class, place_level
from advisor.pool import State, Teammate
from advisor.session import Session, Settings, _event_record
from tests.test_engine_sanity import random_case
from tests.support import sandbox
from tests.support.player_layer import with_player_layer

PROJECT = Path(__file__).resolve().parents[1]
REPLACED = {"玩家思路", "角色专属规则", "特性", "其他特性", "推荐流派", "循环往复"}  # 标了档位后不再出现的分项
SAME = {"基础分", "成长分", "角色分", "标签分", "关键词分", "队友", "难度"}  # 标了档位后照算、和默认相同的分项
DEFAULT_ONLY = {"玩家思路", "推荐流派"}  # 默认推荐才有的分项：自己的方案里一项都不出现
NAME = "治愈坦克"
# 芬妮的两套例子：列了治愈、标记两个流派（星光、充能被锁），和列了治愈、标记、充能三个流派（星光被锁）
TWO = (("缓冲盾牌", "core"), ("鹰眼瞄具", "first"), ("标靶", "second"), ("大碗炖肉", "second"))
THREE = (("缓冲盾牌", "core"), ("鹰眼瞄具", "first"), ("永动机", "first"), ("标靶", "second"))


def plan(*marks, name=NAME, hero="芬妮") -> Plan:
    """测试用的方案：marks 是（筹码名, 档位）按方案的先后（同一档的先后就是顺位）。"""
    return Plan(name=name, hero=hero, tiers=tuple(marks))


def labels(parts) -> list[str]:
    return [p.label for p in parts]


def scheme_parts(parts, origin=None) -> list[Part]:
    return [p for p in parts if p.label == SCHEME_LABEL and (origin is None or p.origin == origin)]


def guessed(p: Part) -> bool:
    """按之后会不会选别的筹码估出来的分项（开套预判、美工刀预判、治愈来源折算）和循环往复、收入折算：它们用到别的筹码的
    分值，标了档位的筹码多了、推荐流派不加了，跟着变，比较分项时不比它们。"""
    return (p.label == "组合" and (p.reason.startswith("预计之后获得") or p.origin == engine.HEAL_DISCOUNT_ORIGIN)
            or p.label in ("循环往复", "收入折算"))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.cfg = cls.db.rules["scheme"]

    def fenny(self, **kw) -> State:
        """芬妮、异变图书馆、噩梦；关键字参数覆盖。"""
        base = dict(hero="芬妮", star=1, round=4, map="异变图书馆", rerolls=3, difficulty_level=LEVEL_NIGHTMARE,
                    difficulty_name="噩梦")
        return State(**{**base, **kw})

    def parts(self, st: State, chip: str, scheme=None, owned=None) -> list[Part]:
        owned = st.owned if owned is None else owned
        return Scorer(self.db, st, scheme=scheme).parts(chip, owned, st.round)

    def by_name(self, adv):
        return {e.chip.name: e for e in adv.evals}

    def order_names(self, adv) -> list[str]:
        """卡片上从上到下的选项名（刷新写 REROLL）。"""
        return [REROLL if k == REROLL_SLOT else adv.evals[k].chip.name for k in adv.ranking.order]

    def place_of(self, adv, chip: str) -> Place:
        return adv.ranking.places[next(i for i, e in enumerate(adv.evals) if e.chip.name == chip)]

    def loss_of(self, adv, chip: str):
        return adv.ranking.losses[next(i for i, e in enumerate(adv.evals) if e.chip.name == chip)]

    def everything(self, tier: str, offer, extra=()) -> list[tuple[str, str]]:
        """除候选以外的全部筹码都标成这一档（不算地图发放的）。"""
        return [(c.name, tier) for c in self.db.chips.values()
                if not c.granted and c.name not in offer and c.name not in extra]


class WordingTest(Base):
    """理由标签、悬停整句、附注、位次和这里写的逐字相同（例子用方案「银行卡流」）。"""

    def test_texts(self):
        e = engine
        self.assertEqual(e.SCHEME_TIER_SHORT.format(tier="必选"), "筹码方案：必选")
        self.assertEqual(e.SCHEME_TIER_REASON.format(name=e.DEFAULT_SCHEME, tier="推荐"), "筹码方案「默认推荐」：推荐")
        self.assertEqual(CORE_LEAD, "，优先于本次未列入筹码方案的筹码")
        self.assertEqual(e.PLAYER_HEAD, "筹码方案：")  # 资料里规则的开头
        self.assertEqual(e.SCHEME_TIER_REASON.format(name="银行卡流", tier="推荐"), "筹码方案「银行卡流」：推荐")
        self.assertEqual(e.SCHEME_TARGET_SHORT.format(school="星光"), "筹码方案：星光流派")
        self.assertEqual(e.SCHEME_TARGET_REASON.format(school="星光", name="银行卡流"), "「星光」是筹码方案「银行卡流」的目标流派")
        self.assertEqual(e.SCHEME_STRAYED_NOTE.format(schools="「充能」"), "已进入方案外的流派「充能」")
        self.assertEqual(e.SCHEME_STRAYED_NOTE.format(schools="「星光」「充能」"), "已进入方案外的流派「星光」「充能」")
        self.assertEqual(
            e.SCHEME_LOST_NOTE.format(entered="「标记」「治愈」", chips="必选「银行卡-用不完」、推荐「ATM机」"),
            "已进入「标记」「治愈」流派，筹码方案中的必选「银行卡-用不完」、推荐「ATM机」本局不再出现")

    def test_place_text(self):
        # 四档都带数字；走偏后改按未标记算的写「未标记」；被锁的写「其他流派」
        marked = [Place(PLACE_MARKED, t, r) for t, r in (("core", 1), ("first", 2), ("second", 3), ("avoid", 1))]
        self.assertEqual([place_text(p) for p in marked], ["必选 1", "推荐 2", "可选 3", "不选 1"])
        self.assertEqual(place_text(Place(PLACE_UNMARKED)), "未标记")
        self.assertEqual(place_text(Place(PLACE_STRAYED, school="充能")), "未标记")
        self.assertEqual(place_text(Place(PLACE_LOCKED, school="充能")), "其他流派")

    def test_lost_text(self):
        # 附注里拿不到的筹码按档位写，超过 4 个时写前 4 个加「等 N 个」
        lost = [("银行卡-用不完", "core"), ("ATM机", "first"), ("“8面”骰子", "first")]
        self.assertEqual(lost_text(lost), "必选「银行卡-用不完」、推荐「ATM机」「“8面”骰子」")
        many = [(f"甲{i}", "core") for i in range(3)] + [(f"乙{i}", "first") for i in range(3)]
        self.assertEqual(lost_text(many), "必选「甲0」「甲1」「甲2」、推荐「乙0」等 6 个")
        self.assertEqual(lost_text(many[:4]), "必选「甲0」「甲1」「甲2」、推荐「乙0」")

    def test_removed_wording(self):
        # 没有仅限目标流派、锁定保护的标签、前瞻句尾、自己方案的必选补分这几项
        for name in ("SCHEME_ONLY_SHORT", "SCHEME_ONLY_REASON", "SCHEME_ONLY_ORIGIN", "SCHEME_LOCK_SHORT",
                     "SCHEME_LOCK_REASON", "SCHEME_LOCK_TAIL", "SCHEME_LOCKED_NOTE", "SCHEME_CORE_LEAD"):
            self.assertFalse(hasattr(engine, name), name)

    def test_tier_words_match_schemes(self):
        # 档位的叫法和方案存储（advisor.schemes）、资料库的档名表同一套
        from advisor import schemes
        from advisor.data import TIER_LABEL
        self.assertEqual(engine.SCHEME_TIER_WORD, schemes.TIER_LABEL)
        self.assertEqual(engine.SCHEME_TIER_WORD, TIER_LABEL)

    def test_values(self):
        # 必选 = 默认推荐的必选、推荐 = 推荐……（rules.toml [scheme] 和 [synergy] 同一把尺子）；没有仅限目标流派的两项分值
        syn = self.db.rules["synergy"]
        for tier, key in engine.SCHEME_TIER_KEY.items():
            self.assertEqual(self.cfg[key], syn[tier], tier)
        self.assertEqual((self.cfg["target"], self.cfg["lock"]), (25, -60))
        self.assertNotIn("only_target", self.cfg)
        self.assertNotIn("only_other", self.cfg)


class TierPartsTest(Base):
    """方案里标了档位的筹码：档位代替玩家思路和角色特性，游戏内置分、组合、队友、难度、收入折算照算；不加目标流派、锁定保护。"""

    def test_tiers_replace_player_and_traits(self):
        st = self.fenny(owned=("银行卡-余额少", "ATM机"), teammates=(Teammate("邦妮"), Teammate("凛")))
        chips = [c.name for c in self.db.chips.values() if not c.granted and c.name not in st.owned]
        seen = set()
        for tier, key in engine.SCHEME_TIER_KEY.items():
            sch = plan(*((c, tier) for c in chips))
            sc_default, sc_scheme = Scorer(self.db, st), Scorer(self.db, st, scheme=sch)
            for chip in chips:
                default = sc_default.parts(chip, st.owned, st.round)
                parts = sc_scheme.parts(chip, st.owned, st.round)
                with self.subTest(tier=tier, chip=chip):
                    tiers = [p for p in parts if p.origin in SCHEME_TIER_ORIGINS]
                    word = engine.SCHEME_TIER_WORD[tier]
                    self.assertEqual([(p.label, p.score, p.reason, p.short) for p in tiers],
                                     [(SCHEME_LABEL, self.cfg[key], f"筹码方案「{NAME}」：{word}", f"筹码方案：{word}")])
                    self.assertFalse(REPLACED & set(labels(parts)))
                    # 标了档位的不加目标流派倾向、不扣锁定保护
                    self.assertEqual(scheme_parts(parts), tiers)
                    keep = [(p.label, p.score, p.reason) for p in default if p.label in SAME]
                    self.assertEqual([(p.label, p.score, p.reason) for p in parts if p.label in SAME], keep)
                    _, cond = default_player_match(sc_default, chip, frozenset(st.owned), st.round, st.map)
                    if not cond:  # 默认推荐里带「已持有」条件的玩家思路规则不叠加组合；标了档位后组合照算
                        fixed = lambda ps: [(p.score, p.reason) for p in ps if p.label == "组合" and not guessed(p)]  # noqa: E731
                        self.assertEqual(fixed(parts), fixed(default))
                    self.assertEqual("收入折算" in labels(parts), "收入折算" in labels(default))
                    seen.update(set(labels(default)) & {"组合", "队友", "难度", "收入折算"})
        self.assertEqual(seen, {"组合", "队友", "难度", "收入折算"})  # 这几种分项都查到了

    def test_must_exempt_from_income(self):
        # 必选和默认推荐的必选同一口径：收入折算不扣必选那一项（只按其余的正分折算）
        st = self.fenny(star=3, round=10)
        sc = Scorer(self.db, st, scheme=plan(("银行卡-余额多", "core")))
        parts = sc.parts("银行卡-余额多", (), 10)
        income = next(p for p in parts if p.label == "收入折算")
        chip = self.db.chips["银行卡-余额多"]
        base = [p for p in parts if p.label != "收入折算"]
        self.assertAlmostEqual(income_part(sc, chip, base, 10).score, income.score)
        plain = [Part(p.label, p.score, p.reason, "x", p.short) if p.origin == SCHEME_CORE_ORIGIN else p for p in base]
        self.assertLess(income_part(sc, chip, plain, 10).score, income.score - 1)


class MutualExclusionTest(Base):
    """自己的方案和默认推荐互斥：没标的筹码不用默认推荐的任何部分——四档、带条件的规则、推荐流派、必选补分，
    只按角色特性、组合、队友、难度、收入折算、前瞻等算。默认推荐的四档和带条件的规则用测试用的那一份
    （tests/support/player_layer.toml）：要有这一层才查得出它没被用上。"""

    HEROES = ("凛", "芬妮", "南希露", "梅加斯", "阿兰娜", "多萝西", "照")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.db = with_player_layer(cls.db)

    def test_unmarked_use_traits_only(self):
        # 方案只标了一个通用筹码（没有流派倾向）：没标的筹码的分项和只用自动层（use_player=False）时相同，只是没有推荐流派
        for hero in self.HEROES:
            for owned in ((), ("复仇之戟", "医疗箱-完备治疗", "普通瞄具")):
                st = State(hero=hero, star=1, round=4, map="水乡古镇", owned=owned, rerolls=2)
                sch = plan(("书签", "avoid"), hero=hero)
                auto, sc = Scorer(self.db, st, use_player=False), Scorer(self.db, st, scheme=sch)
                for chip in self.db.chips.values():
                    if chip.granted or chip.name == "书签" or chip.name in owned:
                        continue
                    parts = sc.parts(chip.name, owned, st.round)
                    with self.subTest(hero=hero, owned=owned, chip=chip.name):
                        self.assertFalse(DEFAULT_ONLY & set(labels(parts)))
                        self.assertFalse(scheme_parts(parts))
                        want = [(p.label, p.score, p.reason) for p in auto.parts(chip.name, owned, st.round)
                                if p.label != "推荐流派" and not guessed(p)]
                        self.assertEqual([(p.label, p.score, p.reason) for p in parts if not guessed(p)], want)

    def test_default_rules_not_used(self):
        # 默认推荐的四档（凛的银行卡不选、大背包必选）、带条件的规则（凛的星光规则，照的轮次规则、已持有规则，
        # 姬梦枫的已持有规则）都不用
        rin = State(hero="凛", rerolls=2)
        sch = plan(("标靶", "core"), hero="凛")
        for chip in ("银行卡-余额少", "银行卡-余额多", "ATM机", "星币锤", "大背包", "忍术飞镖", "标记喷罐"):
            self.assertFalse(DEFAULT_ONLY & set(labels(self.parts(rin, chip, sch))), chip)
        self.assertIn("玩家思路", labels(self.parts(rin, "大背包")))  # 默认推荐照旧是必选
        for hero, st, chip, owned in (("照", State(hero="照", round=2), "银行卡-余额少", ()),
                                      ("照", State(hero="照"), "电磁炮", ("永动机",)),
                                      ("姬梦枫", State(hero="姬梦枫"), "美工刀-锋利", ("医疗箱-完备治疗",))):
            with self.subTest(hero=hero, chip=chip):
                self.assertIn("玩家思路", labels(self.parts(st, chip, owned=owned)))
                self.assertFalse(DEFAULT_ONLY & set(labels(self.parts(st, chip, plan(("书签", "avoid"), hero=hero), owned))))

    def test_no_default_lift(self):
        # 凛的方案里没标的大背包（默认推荐的必选）不补分，整句里没有「优先于……」；自己方案的必选也不补分
        rin = State(hero="凛", rerolls=2)
        offer = ["大背包", "额外电池", "探天卫星"]
        for sch in (plan(("标靶", "core"), hero="凛"), plan(("大背包", "core"), hero="凛")):
            adv = advise(self.db, rin, offer, scheme=sch)
            sc = Scorer(self.db, rin, scheme=sch)
            with self.subTest(scheme=sch.tiers):
                for e in adv.evals:
                    self.assertFalse(any("优先于" in p.reason for p in e.parts))
                    self.assertAlmostEqual(e.total, sc.score(e.chip.name, (), None) + sc.lookahead(e.chip.name, ()))

    def test_default_unchanged_by_scheme_engine(self):
        # 同一个局面按默认推荐算两次（中间按方案算一次）结果相同：方案的计算不影响默认推荐
        st = self.fenny(owned=("紫色飞星",))
        offer = ["标靶", "大碗炖肉", "银行卡-余额多"]
        before = advise(self.db, st, offer)
        advise(self.db, st, offer, scheme=plan(*TWO))
        after = advise(self.db, st, offer)
        self.assertIsNone(after.ranking)
        self.assertEqual((before.best, before.notes, [(e.total, e.percent) for e in before.evals]),
                         (after.best, after.notes, [(e.total, e.percent) for e in after.evals]))


class SchoolPartsTest(Base):
    """没标的筹码和流派有关的分项：目标流派倾向（加标签）；锁定保护、被锁的流派按不选（内部的分值，整句为空，不显示）。"""

    def test_target_bonus(self):
        sch = plan(("缓冲盾牌", "core"), ("可口糖果", "avoid"))
        st = self.fenny()
        target = scheme_parts(self.parts(st, "大碗炖肉", sch), SCHEME_TARGET_ORIGIN)
        self.assertEqual([(p.score, p.short, p.reason) for p in target],
                         [(self.cfg["target"], "筹码方案：治愈流派", f"「治愈」是筹码方案「{NAME}」的目标流派")])
        # 标了档位的（必选、不选）不加；推荐流派加分（星光 +15）只在默认推荐时出现
        for chip in ("缓冲盾牌", "可口糖果"):
            self.assertEqual([p.origin for p in scheme_parts(self.parts(st, chip, sch))],
                             [engine.SCHEME_ORIGIN.format(tier=sch.marks()[chip])], chip)
        self.assertIn("推荐流派", labels(self.parts(st, "紫色飞星")))
        self.assertNotIn("推荐流派", labels(self.parts(st, "紫色飞星", sch)))
        # 只标了可选的流派不是目标流派
        self.assertFalse(scheme_parts(self.parts(st, "大碗炖肉", plan(("缓冲盾牌", "second"))), SCHEME_TARGET_ORIGIN))

    def test_lock_protection_internal(self):
        lock = self.cfg["lock"]
        sch = plan(("缓冲盾牌", "core"))  # 只列了治愈：其余流派不锁
        # 已有星光：选标靶会占满星光、标记，治愈本局不再出现：锁定保护，整句为空（卡片上由「选了之后拿不到」说明）
        parts = self.parts(self.fenny(owned=("紫色飞星",)), "标靶", sch)
        self.assertEqual([(p.score, p.reason, p.short) for p in scheme_parts(parts, SCHEME_LOCK_ORIGIN)], [(lock, "", "")])
        # 已进入治愈、还没有流派、已进入的流派的筹码、标了档位的：不扣
        self.assertFalse(scheme_parts(self.parts(self.fenny(owned=("缓冲盾牌",)), "标靶", sch), SCHEME_LOCK_ORIGIN))
        self.assertFalse(scheme_parts(self.parts(self.fenny(), "标靶", sch), SCHEME_LOCK_ORIGIN))
        self.assertFalse(scheme_parts(self.parts(self.fenny(owned=("紫色飞星",)), "星币锤", sch), SCHEME_LOCK_ORIGIN))
        for tier in TIERS:
            parts = self.parts(self.fenny(owned=("紫色飞星",)), "标靶", plan(("缓冲盾牌", "core"), ("标靶", tier)))
            self.assertFalse(scheme_parts(parts, SCHEME_LOCK_ORIGIN), tier)

    def test_locked_school_counts_as_avoid(self):
        # 列了治愈、标记：星光、充能里没标的筹码按不选计分（内部的分值，整句为空）；手标的不选照旧是档位那一项
        sch = plan(*TWO, ("星币锤", "avoid"))
        st = self.fenny()
        locked = scheme_parts(self.parts(st, "紫色飞星", sch))
        self.assertEqual([(p.origin, p.score, p.reason) for p in locked], [(SCHEME_LOCKED_ORIGIN, self.cfg["skip"], "")])
        self.assertEqual([p.origin for p in scheme_parts(self.parts(st, "星币锤", sch))], ["scheme_avoid"])
        # 进了星光以后（走偏）星光的筹码改按未标记算，没有这一项；充能照旧
        st = self.fenny(owned=("紫色飞星",))
        self.assertFalse(scheme_parts(self.parts(st, "金色飞星", sch)))
        self.assertEqual([p.origin for p in scheme_parts(self.parts(st, "电流剑", sch))], [SCHEME_LOCKED_ORIGIN])

    def test_locks_useless_school(self):
        # 「会把自己锁进对自己没用的流派」（队友刚需用）：拿了以后流派名额占满、有用的流派再也进不去才算。
        # 有用的流派：默认推荐是推荐流派（芬妮：星光），按方案计算时是列进方案的流派；方案没列流派时哪个都不算没用。
        # 不看拿了以后还有没有空位的话，不在有用的流派里就算：还没有流派的芬妮拿标靶、按方案 TWO 拿紫色飞星都会算
        st = self.fenny()
        healed = self.fenny(owned=("缓冲盾牌",))  # 已进入治愈：再进一个流派就占满
        self.assertFalse(Scorer(self.db, st).locks_useless_school("标靶"))  # 还有空位
        self.assertTrue(Scorer(self.db, healed).locks_useless_school("标靶"))  # 占满（治愈、标记），星光进不去
        self.assertFalse(Scorer(self.db, st, scheme=plan(*TWO)).locks_useless_school("标靶"))
        self.assertFalse(Scorer(self.db, st, scheme=plan(*TWO)).locks_useless_school("紫色飞星"))  # 还有空位
        self.assertTrue(Scorer(self.db, healed, scheme=plan(*TWO)).locks_useless_school("紫色飞星"))  # 标记进不去
        self.assertFalse(Scorer(self.db, healed, scheme=plan(("书签", "core"))).locks_useless_school("紫色飞星"))


class OrderTest(Base):
    """卡片上严格按档位和顺位排：必选 > 推荐 > 可选 > 未标记 > 不选 > 其他流派，同一档按顺位，未标记之间按总分。"""

    def test_rank_decides_within_tier(self):
        st = self.fenny()
        offer = ["缓冲盾牌", "医疗箱-完备治疗", "紫色飞星"]
        for marks in ((("医疗箱-完备治疗", "core"), ("缓冲盾牌", "core")), (("缓冲盾牌", "core"), ("医疗箱-完备治疗", "core"))):
            adv = advise(self.db, st, offer, scheme=plan(*marks))
            with self.subTest(first=marks[0][0]):
                self.assertEqual(self.order_names(adv)[:2], [marks[0][0], marks[1][0]])
                self.assertEqual(adv.best, marks[0][0])
                self.assertEqual(adv.evals[adv.top].chip.name, marks[0][0])
                self.assertEqual([place_text(self.place_of(adv, c)) for c, _ in marks], ["必选 1", "必选 2"])
                self.assertNotIn(REROLL, self.order_names(adv)[:1])

    def test_tiers_beat_scores(self):
        # 可选的标靶（总分低）排在未标记的金色飞星（总分高）前面，不选的书签排在未标记后面
        st = self.fenny(rerolls=0)
        adv = advise(self.db, st, ["金色飞星", "标靶", "书签"], scheme=plan(("标靶", "second"), ("书签", "avoid")))
        ev = self.by_name(adv)
        self.assertGreater(ev["金色飞星"].total, ev["标靶"].total)
        self.assertEqual(self.order_names(adv), ["标靶", "金色飞星", "书签"])
        self.assertEqual([place_text(self.place_of(adv, c)) for c in ("标靶", "金色飞星", "书签")], ["可选 1", "未标记", "不选 1"])
        # 被锁的（其他流派）排在不选后面，哪怕总分更高
        adv = advise(self.db, st, ["紫色飞星", "书签", "额外电池"], scheme=plan(*TWO, ("书签", "avoid")))
        self.assertGreater(self.by_name(adv)["紫色飞星"].total, self.by_name(adv)["书签"].total)
        self.assertEqual(self.order_names(adv), ["额外电池", "书签", "紫色飞星"])
        self.assertEqual([place_text(self.place_of(adv, c)) for c in ("额外电池", "书签", "紫色飞星")],
                         ["未标记", "不选 1", "其他流派"])
        self.assertEqual(self.place_of(adv, "紫色飞星"), Place(PLACE_LOCKED, school="星光"))

    def test_unmarked_by_score(self):
        st = self.fenny(rerolls=0)
        adv = advise(self.db, st, ["书签", "额外电池", "夹心饼干-一般"], scheme=plan(("缓冲盾牌", "core")))
        totals = [adv.evals[k].total for k in adv.ranking.order]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertTrue(all(p.kind == PLACE_UNMARKED for p in adv.ranking.places))


class RerollRuleTest(Base):
    """自己方案里「刷新」什么时候排第一：① 有必选不首推刷新；② 手上最好的是推荐、可选时按档位算平均、扣掉代价后
    比手上这张高才刷；③ 手上最好的是未标记时刷出排在它前面的概率过半就刷；④ 三张全是不选、有刷新次数时首推刷新；
    ⑤ 之后预计没有三选一时代价为 0、连着刷。刷新那一行排在它胜过的候选前面。"""

    OFFER = ["标靶", "书签", "夹心饼干-一般"]

    def view(self, adv):
        self.assertIsNotNone(adv.ranking.reroll)
        return adv.ranking.reroll

    @staticmethod
    def reroll_texts(adv, st) -> list[str]:
        """推荐卡片上刷新那一行的理由标签（ui.text 只拼字符串，不导入 Qt）。"""
        from advisor.ui import text as ui_text
        return [p.text for p in ui_text.scheme_reroll_pills(adv, st.rerolls)]

    @staticmethod
    def cost_text(adv) -> str:
        return "之后无需保留刷新" if adv.reroll.cost < 0.5 else "消耗 1 次刷新"

    def test_1_must_never_rerolled_first(self):
        # 最后一次三选一（第 17 轮：异变图书馆困难及以上进度上限 17，到了进度上限）、剩 4 次刷新、治愈筹码全是必选：刷新的
        # 平均级数很高（连着刷），仍不排在必选前面。选普通瞄具会占满流派名额、治愈的必选拿不到，但之后预计没有三选一，
        # 不算拿不到，不往后排（Loss.final）
        heal = [(c.name, "core") for c in self.db.chips.values() if c.school == "治愈" and not c.granted]
        st = State(hero="芬妮", star=3, round=17, map="异变图书馆", rerolls=4, owned=("银行卡-余额少",))
        adv = advise(self.db, st, ["普通瞄具", "书签", "夹心饼干-一般"], scheme=plan(("普通瞄具", "core"), *heal))
        v = self.view(adv)
        self.assertEqual(adv.best, "普通瞄具")
        self.assertEqual(self.order_names(adv)[:2], ["普通瞄具", REROLL])
        self.assertGreater(v.expected, 3)
        self.assertEqual((v.basis, v.level, v.verdict, v.first, v.p_up), (BASIS_TIER, 4, VERDICT_WORSE, False, None))
        self.assertEqual(adv.reroll.percent, 0.0)

    def test_2_tier_hand_average(self):
        st = self.fenny()
        # 别的筹码全标必选时，开局选标靶（标记）会让必选更多的流派进不去，按「拿不到的往后」排到后面。这几处要看的是
        # 手上的可选、推荐和刷新比，所以先进好治愈、标记两个流派（选什么都不改变流派，没有拿不到的）
        both = self.fenny(owned=("缓冲盾牌", "标记喷罐"))
        # 手上是可选，刷新后全是必选（4 级）：扣掉代价仍比可选高，首推刷新；刷出必选、推荐的概率 100%
        adv = advise(self.db, both, self.OFFER, scheme=plan(("标靶", "second"), *self.everything("core", self.OFFER)))
        v = self.view(adv)
        self.assertEqual((adv.best, v.first, v.basis, v.level, v.verdict), (REROLL, True, BASIS_TIER, 2, VERDICT_BETTER))
        self.assertEqual((v.up_tiers, round(v.p_up, 6)), (("core", "first"), 1.0))
        self.assertAlmostEqual(v.diff, v.expected - v.cost - 2)
        # 手上是可选，其余都没标（刷新后最好的平均 1 级）：不刷，刷新排在可选后面、比它弱的未标记前面
        adv = advise(self.db, st, self.OFFER, scheme=plan(("标靶", "second")))
        v = self.view(adv)
        self.assertEqual((adv.best, v.first, v.verdict, v.p_up), ("标靶", False, VERDICT_WORSE, 0.0))
        self.assertEqual(self.order_names(adv)[:2], ["标靶", REROLL])
        # 刷出必选、推荐的概率正好为 0（方案里没有必选、推荐）：推荐卡片上不写这条概率，悬停里各档的比例也就不显示
        self.assertEqual(self.reroll_texts(adv, st), ["预计不如标靶", self.cost_text(adv)])
        # 手上是推荐：刷新后全是必选时刷（更高一档只有必选）；全是推荐时和推荐相当（略优才刷，正好相等不刷）
        adv = advise(self.db, both, self.OFFER, scheme=plan(("标靶", "first"), *self.everything("core", self.OFFER)))
        v = self.view(adv)
        self.assertEqual((adv.best, v.level, v.verdict, v.up_tiers), (REROLL, 3, VERDICT_BETTER, ("core",)))
        adv = advise(self.db, st, self.OFFER, scheme=plan(("标靶", "first"), *self.everything("first", self.OFFER)))
        v = self.view(adv)
        self.assertEqual((adv.best, v.verdict, v.p_up), ("标靶", VERDICT_EVEN, 0.0))
        self.assertEqual(self.reroll_texts(adv, st), ["与标靶相当", self.cost_text(adv)])
        # 概率不为 0 时照写（这里是 100%）
        adv = advise(self.db, both, self.OFFER, scheme=plan(("标靶", "first"), *self.everything("core", self.OFFER)))
        self.assertEqual(self.reroll_texts(adv, both)[1], "超过 99% 概率刷出必选筹码")

    def test_2_cost_model(self):
        # 刷新代价（级数）= 这个局面里刷新一次平均多拿的级数 × 调和数（剩余三选一次数 ÷ 剩余刷新次数）。
        # 方案是复制测试用的凛的默认推荐档位（tests/support/player_layer.toml）：四档都有，刷新一次平均多拿的级数不为 0
        from advisor.schemes import scheme_from_default
        db = with_player_layer(self.db)
        st = State(hero="凛", star=1, round=4, map="异变图书馆", rerolls=3)
        offer = ["额外电池", "书签", "手持风扇-小"]
        adv = advise(db, st, offer, scheme=scheme_from_default(db, "凛"))
        v = self.view(adv)
        levels = [v.levels[k] for k in ("avoid", "unmarked", "second", "first", "core")]
        self.assertAlmostEqual(sum(levels), 1.0)
        mean = sum(k * p for k, p in enumerate(levels))
        gain = sum(p * max(0.0, mean - k) for k, p in enumerate(levels))
        self.assertAlmostEqual(v.expected, mean)
        self.assertAlmostEqual(v.cost, gain * harmonic(adv.picks.total / st.rerolls))
        self.assertGreater(v.cost, 0)

    def test_3_unmarked_hand_probability(self):
        st = self.fenny()
        # 手上最好的是未标记、刷新后全是可选：一定刷出排在它前面的，首推刷新
        adv = advise(self.db, st, self.OFFER, scheme=plan(*self.everything("second", self.OFFER)))
        v = self.view(adv)
        self.assertEqual((adv.best, v.first, v.basis, v.level, v.verdict), (REROLL, True, BASIS_UNMARKED, 1, VERDICT_BETTER))
        self.assertAlmostEqual(v.p_before, 1.0)
        self.assertIsNone(v.p_up)
        # 手上是总分很高的未标记（金色飞星）、方案没标别的：刷出更高的概率不到一半，不刷
        adv = advise(self.db, st, ["金色飞星", "书签", "夹心饼干-一般"], scheme=plan(("大背包", "avoid")))
        v = self.view(adv)
        self.assertEqual((adv.best, v.first, v.verdict), ("金色飞星", False, VERDICT_WORSE))
        self.assertLess(v.p_before, 0.5)
        # 同一手牌，弱的未标记（夹心饼干-一般）刷出排在它前面的过半，扣掉代价以后的刷新也高于它的总分：刷新排在它前面
        weak = adv.evals[2]
        self.assertGreater(adv.ranking.order.index(2), adv.ranking.order.index(REROLL_SLOT))
        sc = Scorer(self.db, st, scheme=plan(("大背包", "avoid")))
        self.assertGreater(chance_before(sc, ["金色飞星", "书签", "夹心饼干-一般"], weak.total, 1), 0.5)
        self.assertGreater(adv.reroll.value, weak.total)

    def test_3_unmarked_needs_worth(self):
        # 排在最前的是未标记时，刷出排在它前面的过半还不够，扣掉刷新代价以后的期望（按方案的分值）也要高于它的总分。
        # 阿兰娜 0 星第 2 轮、只剩 1 次刷新（之后约 6.7 次三选一）、一张都没标的方案：刷出排在可口糖果前面的约 92%
        # （刷新后最左格按职业权重出、池按职业标签建），但用掉这次刷新的代价很高，不首推刷新；刷新那一行的结论是
        # 「预计不如」，悬停写明是因为刷新次数紧
        st = State(hero="阿兰娜", star=0, round=2, map="魔法学院", rerolls=1)
        offer = ["安全气囊", "可口糖果", "夹心饼干-一般"]
        empty = Plan(name="空方案", hero="阿兰娜", tiers=())
        adv = advise(self.db, st, offer, scheme=empty)
        v = self.view(adv)
        top = adv.evals[v.compare]
        self.assertEqual((top.chip.name, v.basis, v.first, v.verdict), ("可口糖果", BASIS_UNMARKED, False, VERDICT_WORSE))
        self.assertGreater(v.p_before, 0.5)
        self.assertLess(adv.reroll.value, top.total)
        self.assertEqual(adv.best, "可口糖果")
        self.assertNotIn(REROLL_SLOT, adv.ranking.order[:3])  # 刷新不在三张未标记前面（都胜不过）
        texts = self.reroll_texts(adv, st)
        self.assertEqual(texts[:2], ["预计不如可口糖果", "92% 概率刷出排在可口糖果前面的筹码"])
        from advisor.ui import text as ui_text
        tip = ui_text.scheme_reroll_pills(adv, st.rerolls)[0].tip
        self.assertIn("刷新次数紧", tip)
        self.assertIn("算上用掉一次刷新的代价", tip)
        # 同一手牌刷新宽时（3 次，之后的三选一少）照旧：过半、扣掉代价也划算就首推刷新
        late = State(hero="阿兰娜", star=3, round=12, map="魔法学院", rerolls=3)
        adv = advise(self.db, late, offer, scheme=empty)
        v = self.view(adv)
        self.assertGreater(v.p_before, 0.5)
        self.assertGreater(adv.reroll.value, adv.evals[v.compare].total)
        self.assertEqual((adv.best, v.verdict), (REROLL, engine.VERDICT_BETTER))

    def test_3_unmarked_free_but_worse(self):
        # 刷新没有代价（留 2 次、多出 3 次连着刷）时也可能过半却不划算：刷新会放弃本次的三个候选，刷出来的按分值平均下来
        # 不如直接选它。不首推刷新，「预计不如」的悬停写的是这个原因，不写「刷新次数紧」（代价那条是「多出 3 次不必留」）。
        # 茉莉 3 星第 11 轮、5 次刷新（之后不到 1 次三选一：留 1 次、再多留 1 次）、一张都没标的方案
        st = State(hero="茉莉", star=3, round=11, map="魔法学院", rerolls=5)
        adv = advise(self.db, st, ["速度轮滑-中级", "小猪存钱罐", "银行卡-用不完"],
                     scheme=Plan(name="空方案", hero="茉莉", tiers=()))
        v = self.view(adv)
        top = adv.evals[v.compare]
        self.assertEqual((top.chip.name, v.basis, v.verdict, adv.best),
                         ("速度轮滑-中级", BASIS_UNMARKED, VERDICT_WORSE, "速度轮滑-中级"))
        self.assertGreater(v.p_before, 0.5)
        self.assertEqual((adv.reroll.cost, adv.reroll.kept, adv.reroll.spare, adv.reroll.chain), (0.0, 2, 3, 3))
        self.assertLess(adv.reroll.value, top.total)
        from advisor.ui import text as ui_text
        pills = ui_text.scheme_reroll_pills(adv, st.rerolls)
        self.assertEqual([p.text for p in pills],
                         ["预计不如速度轮滑-中级", "58% 概率刷出排在速度轮滑-中级前面的筹码", "留 2 次，多出 3 次不必留"])
        self.assertIn("但刷新会放弃本次的三个候选筹码：综合最多连续刷新 3 次可能出现的筹码", pills[0].tip)
        self.assertIn("（之后的筹码三选一留 2 次刷新，含以防估少多留的 1 次；多出来的 3 次可以连续使用）", pills[0].tip)
        self.assertNotIn("刷新次数紧", pills[0].tip)

    def test_4_all_avoid(self):
        sch = plan(*((c, "avoid") for c in self.OFFER))
        # 有刷新次数：首推刷新，刷新 100%、三张 0%，带「三张都是不选」的依据
        adv = advise(self.db, self.fenny(), self.OFFER, scheme=sch)
        v = self.view(adv)
        self.assertEqual((adv.best, v.first, v.all_avoid), (REROLL, True, True))
        self.assertEqual(self.order_names(adv), [REROLL, *self.OFFER])
        self.assertEqual(shown_percent_texts(adv), {REROLL_SLOT: "100%", 0: "0%", 1: "0%", 2: "0%"})
        # 没有刷新次数：三张都写 0%（合计不为 100% 的唯一情形），按不选的顺位排，排第一的是首推
        for marks in (self.OFFER, list(reversed(self.OFFER))):
            adv = advise(self.db, self.fenny(rerolls=0), self.OFFER, scheme=plan(*((c, "avoid") for c in marks)))
            with self.subTest(order=marks):
                self.assertIsNone(adv.reroll)
                self.assertIsNone(adv.ranking.reroll)
                self.assertEqual(self.order_names(adv), list(marks))
                self.assertEqual(adv.best, marks[0])
                self.assertEqual([e.percent for e in adv.evals], [0.0, 0.0, 0.0])
                self.assertEqual(set(shown_percent_texts(adv).values()), {"0%"})
                self.assertEqual(sum(shown_percents(adv).values()), 0)
        # 被锁的（其他流派）也算不选：两张不选加一张被锁，有刷新次数时同样首推刷新
        adv = advise(self.db, self.fenny(), ["紫色飞星", "书签", "夹心饼干-一般"],
                     scheme=plan(*TWO, ("书签", "avoid"), ("夹心饼干-一般", "avoid")))
        self.assertEqual((adv.best, self.view(adv).all_avoid), (REROLL, True))

    def test_5_last_pick_chain(self):
        # 本次之后预计没有三选一（第 17 轮，到了进度上限）：代价为 0，剩下的刷新连着刷（平均级数按刷到满意为止递推），
        # 刷新 3 次时首推刷新、只剩 1 次时不刷
        marks = [("标靶", "second")] + [(c.name, "first") for c in self.db.chips.values() if c.school == "治愈" and not c.granted]
        st = State(hero="芬妮", star=3, round=17, map="异变图书馆", rerolls=3, owned=("银行卡-余额少",))
        many = advise(self.db, st, self.OFFER, scheme=plan(*marks))
        once = advise(self.db, dataclasses.replace(st, rerolls=1), self.OFFER, scheme=plan(*marks))
        self.assertEqual(many.picks.total, 0)
        v3, v1 = self.view(many), self.view(once)
        self.assertEqual((v3.chain, v3.cost, v1.chain, v1.cost), (3, 0.0, 1, 0.0))
        self.assertGreater(v3.expected, v1.expected)
        self.assertGreater(v3.p_up, v1.p_up)
        self.assertEqual((many.best, once.best), (REROLL, "标靶"))

    def test_position_after_beaten(self):
        # 刷新排在它胜过的第一张候选前面：必选（胜不过）> 刷新 > 刷出排在它前面过半的两张未标记
        offer = ["普通瞄具", "书签", "夹心饼干-一般"]
        adv = advise(self.db, self.fenny(), offer, scheme=plan(("普通瞄具", "core"), *self.everything("second", offer)))
        self.assertEqual(self.order_names(adv), ["普通瞄具", REROLL, "夹心饼干-一般", "书签"])


class PercentTest(Base):
    """推荐度（命令行、诊断包排查用）和卡片上的先后一致；按规矩定下来的 0 和 100% 照写「0%」「100%」。"""

    def test_must_takes_all(self):
        adv = advise(self.db, self.fenny(), ["缓冲盾牌", "医疗箱-完备治疗", "紫色飞星"],
                     scheme=plan(("缓冲盾牌", "core"), ("医疗箱-完备治疗", "core")))
        self.assertEqual([e.percent for e in adv.evals], [1.0, 0.0, 0.0])
        self.assertEqual(adv.reroll.percent, 0.0)
        self.assertEqual(shown_percent_texts(adv), {0: "100%", 1: "0%", 2: "0%", REROLL_SLOT: "0%"})

    def test_follow_order(self):
        # 其余情形：按总分算出的推荐度从高到低依次分给卡片上的选项，不选、被锁的 0%
        adv = advise(self.db, self.fenny(), ["金色飞星", "标靶", "书签"], scheme=plan(*TWO, ("书签", "avoid")))
        shares = [adv.reroll.percent if k == REROLL_SLOT else adv.evals[k].percent for k in adv.ranking.order]
        self.assertEqual(shares, sorted(shares, reverse=True))
        self.assertAlmostEqual(sum(shares), 1.0)
        ev = self.by_name(adv)
        self.assertEqual((ev["书签"].percent, ev["金色飞星"].percent), (0.0, 0.0))  # 不选、被锁
        self.assertEqual(sum(shown_percents(adv).values()), 100)

    def test_default_texts_unchanged(self):
        # 默认推荐照旧：取整后为 0 / 100 写「<1%」「>99%」，合计 100%
        adv = advise(self.db, State(hero="凛", rerolls=0), ["银行卡-余额多", "大背包", "探天卫星"])
        shown = shown_percents(adv)
        self.assertEqual(sum(shown.values()), 100)
        texts = shown_percent_texts(adv)
        self.assertEqual(texts, {k: engine.percent_text(v, alone=len(shown) == 1) for k, v in shown.items()})


class SeriesTierTest(Base):
    """同系列按档位：低一级标得比高一级高（含高一级没标）时不算上位替代，两张照档位比；
    档位相同或高一级更高时照常替代。和资料里算作上位替代的手持风扇-小 / 大交叉着查。"""

    OFFER = ["手持风扇-小", "手持风扇-大", "会员推荐信"]  # 第三张只是陪衬：没标时排在手持风扇-大之后

    def check(self, marks, superseded: bool, first: str):
        adv = advise(self.db, State(hero="凛", rerolls=2), self.OFFER, scheme=plan(*marks, hero="凛"))
        ev = self.by_name(adv)
        with self.subTest(marks=marks):
            self.assertEqual(ev["手持风扇-小"].superseded_by, "手持风扇-大" if superseded else None)
            self.assertIsNone(ev["手持风扇-大"].superseded_by)
            names = [n for n in self.order_names(adv) if n != REROLL]
            self.assertEqual(names[0], first)
            if superseded:
                self.assertEqual(names[-1], "手持风扇-小")  # 被替代的排在最后
                self.assertEqual(ev["手持风扇-小"].percent, 0.0)

    def test_by_tier(self):
        self.check((("手持风扇-小", "first"),), False, "手持风扇-小")  # 小推荐、大没标：不替代，小排前
        self.check((("手持风扇-小", "core"), ("手持风扇-大", "second")), False, "手持风扇-小")
        self.check((("手持风扇-小", "second"), ("手持风扇-大", "first")), True, "手持风扇-大")
        self.check((("手持风扇-小", "second"), ("手持风扇-大", "second")), True, "手持风扇-大")  # 同一档：小的顺位在前也替代
        self.check((("手持风扇-大", "core"), ("手持风扇-小", "core")), True, "手持风扇-大")
        self.check((("会员推荐信", "avoid"),), True, "手持风扇-大")  # 两张都没标：照资料替代
        self.check((("手持风扇-小", "avoid"),), True, "手持风扇-大")  # 小不选、大没标：大更高
        self.check((("手持风扇-小", "avoid"), ("手持风扇-大", "avoid")), True, "会员推荐信")  # 两张都不选：同一档
        # 小没标、大不选：照档位先后未标记排在不选前面，不算上位替代（编辑窗口这种情形不提醒，同系列的提醒只管标了可选及以上的低一级）
        self.check((("手持风扇-大", "avoid"),), False, "手持风扇-小")

    def test_every_strict_pair(self):
        # 全部严格上位替代的对 × 两张各五种标法（四档和未标记）：低一级排得比高一级前时（只看档位）不替代，否则替代
        order = {"core": 0, "first": 1, "second": 2, None: 3, "avoid": 4}
        st = State(hero="凛", rerolls=0)
        pairs = [p for p in self.db.series if p.strict]
        self.assertEqual(len(pairs), 15)
        for p in pairs:
            for low in order:
                for high in order:
                    marks = [(c, t) for c, t in ((p.low, low), (p.high, high)) if t] or [("书签", "avoid")]
                    adv = advise(self.db, st, [p.low, p.high, "会员推荐信"], scheme=plan(*marks, hero="凛"))
                    with self.subTest(low=(p.low, low), high=(p.high, high)):
                        hidden = self.by_name(adv)[p.low].superseded_by == p.high
                        self.assertEqual(hidden, order[high] <= order[low])

    def test_three_level_chain(self):
        # 三级的同系列每一对各判一次：一般标了必选（比中级、高级都高，不替代）；中级、高级都没标，中级照旧被高级替代
        offer = ["摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"]
        adv = advise(self.db, State(hero="璐璐", rerolls=0), offer, scheme=plan(("摩托头盔-一般", "core"), hero="璐璐"))
        ev = self.by_name(adv)
        self.assertEqual([ev[c].superseded_by for c in offer], [None, "摩托头盔-高级", None])
        self.assertEqual(self.order_names(adv), ["摩托头盔-一般", "摩托头盔-高级", "摩托头盔-中级"])

    def test_avoid_over_locked(self):
        # 低一级手标了不选、高一级在被锁的流派里没标（其他流派，排在不选之后）：照档位先后不替代，两张都在不选那一块之后
        sch = plan(*TWO, ("紫色飞星", "avoid"))
        adv = advise(self.db, self.fenny(rerolls=0), ["紫色飞星", "金色飞星", "额外电池"], scheme=sch)
        ev = self.by_name(adv)
        self.assertIsNone(ev["紫色飞星"].superseded_by)
        self.assertEqual(self.order_names(adv), ["额外电池", "紫色飞星", "金色飞星"])
        self.assertEqual([place_text(self.place_of(adv, c)) for c in ("紫色飞星", "金色飞星")], ["不选 1", "其他流派"])

    def test_default_still_superseded(self):
        # 默认推荐照资料（手持风扇算严格上位替代）
        adv = advise(self.db, State(hero="凛", rerolls=2), self.OFFER)
        self.assertEqual(self.by_name(adv)["手持风扇-小"].superseded_by, "手持风扇-大")

    def test_other_pair(self):
        # 别的严格上位替代同样按档位：摩托头盔-一般标了必选、中级没标时不替代，一般排第一
        offer = ["摩托头盔-一般", "摩托头盔-中级", "书签"]
        adv = advise(self.db, State(hero="璐璐", rerolls=2), offer, scheme=plan(("摩托头盔-一般", "core"), hero="璐璐"))
        self.assertIsNone(self.by_name(adv)["摩托头盔-一般"].superseded_by)
        self.assertEqual(adv.best, "摩托头盔-一般")


class TeammateNeedTest(Base):
    """队友刚需排第二在自己的方案里只在按总分排先后的未标记筹码之间调，不越过档位；「对自己没用的流派」按列进方案的流派判断
    （拿了以后流派名额占满、列进方案的流派再也进不去才不提）。"""

    def test_need_only_among_unmarked(self):
        st = State(hero="阿兰娜", star=1, round=4, map="异变图书馆", teammates=(Teammate("邦妮"),), rerolls=2)
        offer = ["拳击手套-高级", "速度轮滑-高级", "标靶"]

        def need(adv):
            return [(e.chip.name, p.short) for e in adv.evals for p in e.parts if p.origin == NEED_ORIGIN]

        # 列了治愈、标记（标记只标了可选，不是目标流派）：三张都没标，标靶排第三，按邦妮的刚需提到第二
        adv = advise(self.db, st, offer, scheme=plan(("缓冲盾牌", "core"), ("普通瞄具", "second"), hero="阿兰娜"))
        self.assertEqual(need(adv), [("标靶", "配合队友：邦妮刚需标记")])
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["拳击手套-高级", "标靶", "速度轮滑-高级"])
        # 排第一的是标了推荐的：不提
        adv = advise(self.db, st, offer, scheme=plan(("缓冲盾牌", "core"), ("普通瞄具", "second"), ("拳击手套-高级", "first"),
                                                     hero="阿兰娜"))
        self.assertEqual(need(adv), [])
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["拳击手套-高级", "速度轮滑-高级", "标靶"])
        # 方案只列了治愈、还没进入流派：拿标靶以后还有空位、不耽误治愈，照样提到第二（不看空位的话方案外的流派
        # 一律算没用，不提）
        adv = advise(self.db, st, offer, scheme=plan(("缓冲盾牌", "core"), hero="阿兰娜"))
        self.assertEqual(need(adv), [("标靶", "配合队友：邦妮刚需标记")])
        # 已进入方案外的星光：拿普通瞄具会占满流派名额，治愈本局再也进不去，排第三、本身够推荐线也不提
        strayed = dataclasses.replace(st, owned=("紫色飞星",))
        blocked = ["拳击手套-高级", "速度轮滑-高级", "普通瞄具"]
        adv = advise(self.db, strayed, blocked, scheme=plan(("缓冲盾牌", "second"), hero="阿兰娜"))
        self.assertEqual(need(adv), [])
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], blocked)

    def test_need_reason_follows_card(self):
        # 提的时候排到未标记里的第二位；之后「刷新」插在它前面时，「队友」那一项的整句写卡片上实际的位次（第三位），不写
        # 「排在第二位，第一位仍按自身收益」。在第二位时照旧
        st = State(hero="娜蒂斯", star=3, round=12, map="魔法学院", owned=("大碗炖肉", "友情徽章", "标靶"), rerolls=2,
                   teammates=(Teammate("凛"), Teammate("阿尔")))
        offer = ["鹰眼瞄具", "普通瞄具", "忍术飞镖"]
        adv = advise(self.db, st, offer, scheme=Plan(name="空方案", hero="娜蒂斯", tiers=()))
        self.assertEqual(self.order_names(adv), ["鹰眼瞄具", REROLL, "普通瞄具", "忍术飞镖"])
        reasons = [p.reason for e in adv.evals for p in e.parts if p.origin == NEED_ORIGIN]
        self.assertEqual(len(reasons), 1)
        self.assertTrue(reasons[0].endswith("；该筹码可提供，排在第三位"), reasons[0])
        # 没有刷新次数时刷新那一行不在，提的那张在第二位：照旧写
        adv = advise(self.db, dataclasses.replace(st, rerolls=0), offer, scheme=Plan(name="空方案", hero="娜蒂斯", tiers=()))
        self.assertEqual(self.order_names(adv), ["鹰眼瞄具", "普通瞄具", "忍术飞镖"])
        reasons = [p.reason for e in adv.evals for p in e.parts if p.origin == NEED_ORIGIN]
        self.assertTrue(reasons[0].endswith("；该筹码可提供，排在第二位，第一位仍按自身收益"), reasons[0])


class SoftLockTest(Base):
    """软锁和走偏的几种走法。方案 TWO 列了治愈、标记（星光、充能被锁），THREE 列了治愈、标记、充能（星光被锁）。"""

    def test_start_two_schools(self):
        # 开局：被锁的星光、充能写「其他流派」，排在最后；选了它们以后治愈、标记只能再进一个（橙色标签）；方案里的可选不提醒
        adv = advise(self.db, self.fenny(), ["紫色飞星", "电流剑", "大碗炖肉"], scheme=plan(*TWO))
        r = adv.ranking
        self.assertEqual((r.listed, r.locked, r.strayed, r.targets, r.entered),
                         (("治愈", "标记"), ("星光", "充能"), (), ("治愈", "标记"), ()))
        self.assertEqual(adv.best, "大碗炖肉")
        self.assertEqual(self.place_of(adv, "紫色飞星"), Place(PLACE_LOCKED, school="星光"))
        self.assertIsNone(self.loss_of(adv, "大碗炖肉"))
        for chip, school in (("紫色飞星", "星光"), ("电流剑", "充能")):
            loss = self.loss_of(adv, chip)
            self.assertEqual((loss.kind, loss.warn, loss.enter, loss.gone), (LOSS_ONE_LEFT, True, (school,), ("治愈", "标记")))
            self.assertEqual(loss.chips, (("缓冲盾牌", "core"), ("鹰眼瞄具", "first")))
            self.assertEqual(loss.by_school, (("治愈", ("缓冲盾牌",)), ("标记", ("鹰眼瞄具",))))
        self.assertEqual((adv.ranking.notes, adv.warn_notes), ([], []))

    def test_enter_a_then_b(self):
        # 先进治愈：再进标记（方案里的）没有拿不到的；方案外的星光拿了以后标记的推荐马上拿不到（橙色）
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), ["标靶", "星币锤", "书签"], scheme=plan(*TWO))
        self.assertIsNone(self.loss_of(adv, "标靶"))
        loss = self.loss_of(adv, "星币锤")
        self.assertEqual((loss.kind, loss.warn, loss.enter, loss.gone, loss.chips),
                         (LOSS_NOW, True, ("治愈", "星光"), ("标记",), (("鹰眼瞄具", "first"),)))
        self.assertEqual(adv.ranking.notes, [])

    def test_enter_a_then_outside_d(self):
        # 先进治愈、再进方案外的星光：两条橙色附注；星光的筹码改按未标记算，排在可选后面、不选前面
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌", "紫色飞星")), ["金色飞星", "大碗炖肉", "书签"], scheme=plan(*TWO))
        strayed = "已进入方案外的流派「星光」"
        lost = "已进入「治愈」「星光」流派，筹码方案中的推荐「鹰眼瞄具」本局不再出现"
        self.assertEqual([(n.kind, n.text, n.warn) for n in adv.ranking.notes],
                         [(NOTE_STRAYED, strayed, True), (NOTE_LOST, lost, True)])
        self.assertEqual(adv.warn_notes, [strayed, lost])
        self.assertEqual(adv.notes[:2], [strayed, lost])
        self.assertEqual(self.place_of(adv, "金色飞星"), Place(PLACE_STRAYED, school="星光"))
        self.assertEqual(place_text(self.place_of(adv, "金色飞星")), "未标记")
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["大碗炖肉", "金色飞星", "书签"])
        self.assertEqual(adv.ranking.losses, [None, None, None])  # 两个流派已满，选什么都不改变流派

    def test_stray_once(self):
        # 走偏一次（进了星光）：只剩一个名额，两边的候选都写选了以后拿不到的。选标靶（可选 1，标记）会让必选缓冲盾牌拿不到，
        # 另选大碗炖肉（可选 2，治愈）能保住：标靶算拿不到（橙色），排到大碗炖肉和未标记的金色飞星后面；大碗炖肉让推荐鹰眼瞄具
        # 拿不到，但要保住必选本来就只能进治愈，避免不了（灰色）
        adv = advise(self.db, self.fenny(owned=("紫色飞星",)), ["标靶", "大碗炖肉", "金色飞星"], scheme=plan(*TWO))
        self.assertEqual([n.text for n in adv.ranking.notes], ["已进入方案外的流派「星光」"])
        # 「已进入方案外的流派」橙色（卡片上加 ⚠），和进了方案外流派时的「本局不再出现」同一种写法
        self.assertEqual([(n.kind, n.warn) for n in adv.ranking.notes], [(NOTE_STRAYED, True)])
        self.assertEqual(adv.warn_notes, ["已进入方案外的流派「星光」"])
        self.assertEqual(adv.best, "大碗炖肉")
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["大碗炖肉", "金色飞星", "标靶"])
        heal, mark = self.loss_of(adv, "标靶"), self.loss_of(adv, "大碗炖肉")
        self.assertEqual((heal.kind, heal.warn, heal.gone, heal.chips, heal.lost),
                         (LOSS_NOW, True, ("治愈",), (("缓冲盾牌", "core"),), LOST_CORE))
        self.assertEqual([adv.evals[k].chip.name for k in heal.behind], ["大碗炖肉", "金色飞星"])
        self.assertEqual((mark.kind, mark.warn, mark.gone, mark.chips, mark.lost),
                         (LOSS_NOW, False, ("标记",), (("鹰眼瞄具", "first"),), ""))
        # 还没进的方案外流派（充能）照旧锁着；选了会让列进方案的流派全都进不去（橙色）
        adv = advise(self.db, self.fenny(owned=("紫色飞星",)), ["电流剑", "标靶", "书签"], scheme=plan(*TWO))
        self.assertEqual(self.place_of(adv, "电流剑"), Place(PLACE_LOCKED, school="充能"))
        loss = self.loss_of(adv, "电流剑")
        self.assertEqual((loss.kind, loss.warn, loss.gone), (LOSS_NOW, True, ("治愈", "标记")))

    def test_stray_twice(self):
        adv = advise(self.db, self.fenny(owned=("紫色飞星", "电流剑")), ["金色飞星", "永动机", "书签"], scheme=plan(*TWO))
        self.assertEqual(adv.ranking.strayed, ("星光", "充能"))
        self.assertEqual(adv.ranking.locked, ())
        self.assertEqual(adv.warn_notes, [
            "已进入方案外的流派「星光」「充能」",
            "已进入「星光」「充能」流派，筹码方案中的必选「缓冲盾牌」、推荐「鹰眼瞄具」本局不再出现"])
        self.assertEqual({self.place_of(adv, c).kind for c in ("金色飞星", "永动机")}, {PLACE_STRAYED})

    def test_three_schools(self):
        # 列了三个流派、还没进流派：候选属于列进方案的流派时，不论标没标档位，「只能再进入一个」都只写在悬停里（不做标签）
        adv = advise(self.db, self.fenny(), ["标靶", "大碗炖肉", "电流剑"], scheme=plan(*THREE))
        tip = self.loss_of(adv, "标靶")
        self.assertEqual((tip.kind, tip.warn, tip.enter, tip.gone), (LOSS_TIP, False, ("标记",), ("治愈", "充能")))
        self.assertEqual(tip.chips, (("缓冲盾牌", "core"), ("永动机", "first")))
        for chip, school, gone in (("大碗炖肉", "治愈", ("标记", "充能")), ("电流剑", "充能", ("治愈", "标记"))):
            loss = self.loss_of(adv, chip)  # 未标记
            self.assertEqual((loss.kind, loss.warn, loss.enter, loss.gone), (LOSS_TIP, False, (school,), gone), chip)
        # 手标了不选的同样只写悬停；方案外流派（被锁的星光）的筹码照旧做成橙色标签
        adv = advise(self.db, self.fenny(), ["可口糖果", "紫色飞星", "书签"], scheme=plan(*THREE, ("可口糖果", "avoid")))
        loss = self.loss_of(adv, "可口糖果")
        self.assertEqual((self.place_of(adv, "可口糖果").tier, loss.kind, loss.warn), ("avoid", LOSS_TIP, False))
        loss = self.loss_of(adv, "紫色飞星")
        self.assertEqual(self.place_of(adv, "紫色飞星"), Place(PLACE_LOCKED, school="星光"))
        self.assertEqual((loss.kind, loss.warn, loss.enter, loss.gone), (LOSS_ONE_LEFT, True, ("星光",), ("治愈", "标记", "充能")))
        self.assertEqual(loss.by_school, (("治愈", ("缓冲盾牌",)), ("标记", ("鹰眼瞄具",)), ("充能", ("永动机",))))
        self.assertIsNone(self.loss_of(adv, "书签"))  # 通用筹码不属于流派
        # 进了治愈以后只剩一个名额：标记、充能各有一张推荐，只能保住一张。方案里的标靶挤掉充能、未标记的电流剑挤掉标记，
        # 都是避免不了的（灰色）；被锁的紫色飞星两个都挤掉，另选别的能保住一张（橙色；被锁的本来就排在最后，不另挪）
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), ["标靶", "电流剑", "紫色飞星"], scheme=plan(*THREE))
        got = {c: (self.loss_of(adv, c).kind, self.loss_of(adv, c).warn, self.loss_of(adv, c).gone)
               for c in ("标靶", "电流剑", "紫色飞星")}
        self.assertEqual(got, {"标靶": (LOSS_NOW, False, ("充能",)), "电流剑": (LOSS_NOW, False, ("标记",)),
                               "紫色飞星": (LOSS_NOW, True, ("标记", "充能"))})
        self.assertEqual(self.loss_of(adv, "紫色飞星").lost, LOST_FIRST)
        # 进了治愈、标记（都在方案里）：充能的推荐本局不再出现，灰色附注
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌", "标靶")), ["书签", "大背包", "额外电池"], scheme=plan(*THREE))
        self.assertEqual([(n.text, n.warn) for n in adv.ranking.notes],
                         [("已进入「治愈」「标记」流派，筹码方案中的推荐「永动机」本局不再出现", False)])
        self.assertEqual(adv.warn_notes, [])

    def test_only_must_and_recommend_count(self):
        # 只看必选、推荐：三个流派里只有治愈有必选时，开局进哪个流派都拿得全，不提醒；只列一个流派时不上锁、不提醒
        adv = advise(self.db, self.fenny(), ["紫色飞星", "书签", "额外电池"],
                     scheme=plan(("缓冲盾牌", "core"), ("标靶", "second"), ("电流剑", "second")))
        self.assertEqual(adv.ranking.losses, [None, None, None])
        adv = advise(self.db, self.fenny(), ["大碗炖肉", "标靶", "电流剑"], scheme=plan(("银行卡-用不完", "core")))
        self.assertEqual((adv.ranking.locked, adv.ranking.losses), ((), [None, None, None]))
        self.assertEqual({p.kind for p in adv.ranking.places}, {PLACE_UNMARKED})

    def test_lost_note_truncated(self):
        many = (("缓冲盾牌", "core"), ("永动机", "core"), ("电磁炮", "core"), ("银行卡-用不完", "first"), ("ATM机", "first"),
                ("星币锤", "first"), ("标靶", "second"))
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌", "标靶")), ["书签", "大背包", "额外电池"], scheme=plan(*many))
        self.assertEqual([(n.text, n.warn) for n in adv.ranking.notes], [(
            "已进入「治愈」「标记」流派，筹码方案中的必选「永动机」「电磁炮」、推荐「银行卡-用不完」「ATM机」等 5 个本局不再出现", False)])


def _class(place: Place) -> int:
    return place_class(place)


BONNIE = (("标靶", "core"), ("标记喷罐", "first"), ("魔法箭袋", "first"),
          ("摩托头盔-中级", "second"), ("可口糖果", "second"), ("手持风扇-小", "second"))
# 三张都标可选、都不属于流派（选哪张都不改变流派，没有拿不到的）：同一档排先后的边界用
PLAIN_OPTIONAL = ("书签", "额外电池", "夹心饼干-一般")


def fixed_ahead(values):
    """把 Scorer.tier_lookahead 换成固定值（筹码名 → 档位前瞻，没列的为 0）。"""
    return mock.patch.object(Scorer, "tier_lookahead", lambda self, name, owned: values.get(name, 0.0))


def fixed_rates(values, rates, second=None, words="必选、推荐"):
    """把 Scorer.tier_ahead 换成固定值：档位前瞻 values（没列的为 0）、卡片上写的数（必选、推荐在下一次三选一里至少出现一张的
    机会）「选了以后」rates（「现在」都是 20%，没列的为 20%），给了 second 时另有可选的那个数「选了以后」（「现在」30%，没列的
    为 30%）。words 是这个数说的是哪几档（方案里没有必选、推荐时为「可选」，这时 rates 是可选的）。"""
    def fake(self, name, owned):
        extra = None if second is None else (0.3, second.get(name, 0.3))
        return engine.TierAhead(values.get(name, 0.0), words, 0.2, rates.get(name, 0.2), (), "", "", extra)
    return mock.patch.object(Scorer, "tier_ahead", fake)


class SameTierTest(Base):
    """同一档（推荐、可选）按档位前瞻排先后：先照顺位，后一张两样都更好——档位前瞻高出不少于 [scheme] same_tier_near、卡片上
    写的数（必选、推荐在下一次三选一里至少出现一张的机会，按卡片的取整）也不比前一张低——时对调（冒泡），相差不到线或两样
    方向相反时照顺位；必选照顺位。卡片上：排到前面的「前瞻优于X」（绿），被排到后面的「前瞻不如X」（橙），照顺位排在最前那张
    也写一句（两样也都更好「前瞻优于X」，差不多或方向相反「前瞻差不多，照方案顺位」，之后没有三选一「之后没有三选一，
    照顺位」）。"""

    def names(self, adv) -> list[str]:
        return [n for n in self.order_names(adv) if n != REROLL]

    def moves(self, adv, chip: str) -> list[tuple[str, list[str]]]:
        k = next(i for i, e in enumerate(adv.evals) if e.chip.name == chip)
        return [(m.kind, [adv.evals[j].chip.name for j in m.others]) for m in adv.ranking.moves[k]]

    def plain(self, values, star=1, round=4, marks=PLAIN_OPTIONAL, tier="second"):
        """三张都标同一档的通用筹码（不改变流派），档位前瞻换成固定值。"""
        st = self.fenny(star=star, round=round)
        with fixed_ahead(values):
            return advise(self.db, st, list(PLAIN_OPTIONAL), scheme=plan(*((c, tier) for c in marks)))

    def test_origin_case(self):
        # 邦妮的方案，已有能量回收，水乡古镇 1 星第 6 轮，三张都标可选：选手持风扇-小（可选 3）以后，方案里的必选、推荐
        # （标靶、标记喷罐、魔法箭袋）在下一次三选一里至少出现一张的机会 12% → 27%（三张各自的出现率相加是 12% → 29%），
        # 排到最前；摩托头盔-中级（可选 1）照顺位排第二；可口糖果（可选 2）会占满流派名额、标靶拿不到，排在最后（拿不到的往后）
        st = State(hero="邦妮", star=1, round=6, map="水乡古镇", owned=("能量回收",), rerolls=3)
        offer = ["摩托头盔-中级", "可口糖果", "手持风扇-小"]
        adv = advise(self.db, st, offer, scheme=plan(*BONNIE, hero="邦妮"))
        self.assertEqual(self.names(adv), ["手持风扇-小", "摩托头盔-中级", "可口糖果"])
        self.assertEqual(adv.best, "手持风扇-小")
        fan, helmet, candy = (adv.ranking.aheads[i] for i in (2, 0, 1))
        self.assertGreater(fan.value - helmet.value, self.cfg["same_tier_near"])
        self.assertEqual((fan.words, f"{fan.before:.0%}", f"{fan.after:.0%}"), ("必选、推荐", "12%", "27%"))
        self.assertEqual(fan.short, "前瞻：必选、推荐 12%→27%")
        # 括号里变化最大的一两张照旧是各自的出现率
        self.assertEqual(fan.reason, "选择后，筹码方案里必选、推荐的筹码在下一次三选一里至少出现一张的机会 12% → 27%"
                                     "（魔法箭袋 5% → 10%、标靶 4% → 9%）")
        self.assertEqual((candy.short, f"{candy.after:.0%}"), ("前瞻：必选、推荐 12%→0%", "0%"))
        self.assertEqual(helmet.short, "")  # 取整后没变：卡片上不做这个标签
        self.assertEqual(self.moves(adv, "手持风扇-小"), [(MOVE_AHEAD, ["摩托头盔-中级"])])
        self.assertEqual(self.moves(adv, "摩托头盔-中级"), [(MOVE_BEHIND, ["手持风扇-小"])])
        self.assertEqual(self.moves(adv, "可口糖果"), [])  # 排在后面是拿不到的往后，另有提醒
        self.assertEqual(self.loss_of(adv, "可口糖果").lost, LOST_CORE)
        # 卡片：排第一的也写一句为什么（「前瞻优于可选 1」），档位前瞻的标签代替前瞻那一条
        from advisor.ui import text as ui_text
        views = {v.name: v for v in ui_text.option_views(adv) if not v.reroll}
        texts = {n: [(p.text, p.tone) for p in v.pills] for n, v in views.items()}
        self.assertIn(("前瞻优于可选 1", ui_text.PLUS), texts["手持风扇-小"])
        self.assertIn(("前瞻：必选、推荐 12%→27%", ui_text.PLUS), texts["手持风扇-小"])
        self.assertIn(("前瞻不如可选 3", ui_text.MINUS), texts["摩托头盔-中级"])
        self.assertIn(("前瞻：必选、推荐 12%→0%", ui_text.MINUS), texts["可口糖果"])
        tip = next(p.tip for p in views["手持风扇-小"].pills if p.text == "前瞻优于可选 1")
        self.assertEqual(tip, "和同为可选的「摩托头盔-中级」（顺位 1）相比，选择「手持风扇-小」以后，之后三选一里方案标了档位的"
                              "筹码出现的机会更好，所以排在它前面。方案里必选、推荐的筹码在下一次三选一里至少出现一张的机会"
                              "现在 12%，选「手持风扇-小」以后 27%、选「摩托头盔-中级」以后 12%。")
        tip = next(p.tip for p in views["手持风扇-小"].pills if p.text == "前瞻：必选、推荐 12%→27%")
        self.assertEqual(tip, fan.reason)
        # 命令行：档位前瞻那一行写同一句，同一档的说明写「下一次至少出现一张的机会不更低」
        text = cli.render(self.db, adv)
        self.assertIn(f"档位前瞻 {fan.value:+.1f}：{fan.reason}", text)
        self.assertIn("同一档：档位前瞻高出不少于线、下一次至少出现一张的机会不更低，排在「摩托头盔-中级」前面", text)
        # 档位前瞻、同一档的几行不再写各张出现率的「合计」（只查这几行：别的行正当地出现这两个字时不误报）
        ahead_lines = [line for line in text.splitlines() if "档位前瞻" in line or "同一档：" in line]
        self.assertTrue(ahead_lines)
        for line in ahead_lines:
            self.assertNotIn("合计", line)
        for v in views.values():
            self.assertFalse(any(p.text.startswith("前瞻：") and "→" in p.text and "必选" not in p.text for p in v.pills))
            for p in v.pills:
                self.assertLessEqual(len(p.text), engine.SHORT_LIMIT, p.text)

    def test_line(self):
        near = self.cfg["same_tier_near"]
        a, b, c = PLAIN_OPTIONAL  # 顺位 1、2、3
        # 后一张高出正好等于线：对调；差一点点不到：照顺位
        adv = self.plain({a: 0.0, b: near})
        self.assertEqual(self.names(adv)[:2], [b, a])
        self.assertEqual(self.moves(adv, b), [(MOVE_AHEAD, [a])])
        self.assertEqual(self.moves(adv, a), [(MOVE_BEHIND, [b])])
        adv = self.plain({a: 0.0, b: near - 0.01})
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertEqual(self.moves(adv, a), [(MOVE_NEAR, [b])])  # 照顺位排在最前的那张写一句
        self.assertEqual((self.moves(adv, b), self.moves(adv, c)), ([], []))
        # 排在最前的照顺位、档位前瞻也高出不少于线
        adv = self.plain({a: near, b: 0.0, c: 0.0})
        self.assertEqual(self.moves(adv, a), [(MOVE_LEAD, [b])])
        # 三张：顺位 3 比顺位 2 高出线、比顺位 1 也高出线，排到最前；顺位 1、2 照旧
        adv = self.plain({a: 0.0, b: 2.0, c: 2.0 + near})
        self.assertEqual(self.names(adv), [c, a, b])
        self.assertEqual(self.moves(adv, c), [(MOVE_AHEAD, [a, b])])
        self.assertEqual(self.moves(adv, a), [(MOVE_BEHIND, [c])])
        self.assertEqual(self.moves(adv, b), [(MOVE_BEHIND, [c])])
        # 三张：顺位 3 只比顺位 2 高出线、和顺位 1 差不多：只和顺位 2 对调
        adv = self.plain({a: 2.0, b: 0.0, c: near})
        self.assertEqual(self.names(adv), [a, c, b])
        self.assertEqual(self.moves(adv, a), [(MOVE_NEAR, [c])])
        self.assertEqual(self.moves(adv, c), [(MOVE_AHEAD, [b])])
        # 冒泡到没有可调的：倒序
        adv = self.plain({a: 0.0, b: near, c: 2 * near})
        self.assertEqual(self.names(adv), [c, b, a])
        self.assertEqual(self.moves(adv, b), [(MOVE_AHEAD, [a]), (MOVE_BEHIND, [c])])
        # 推荐同样；必选照顺位
        adv = self.plain({a: 0.0, b: 2 * near}, tier="first")
        self.assertEqual(self.names(adv)[:2], [b, a])
        adv = self.plain({a: 0.0, b: 2 * near}, tier="core")
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertTrue(all(m == () for m in adv.ranking.moves))

    def rated(self, values, rates, second=None, words="必选、推荐"):
        """三张都标可选的通用筹码（PLAIN_OPTIONAL，顺位 1、2、3），档位前瞻和卡片上写的数换成固定值。"""
        with fixed_rates(values, rates, second, words):
            return advise(self.db, self.fenny(), list(PLAIN_OPTIONAL), scheme=plan(*((c, "second") for c in PLAIN_OPTIONAL)))

    def test_rates_must_agree(self):
        near = self.cfg["same_tier_near"]
        a, b, c = PLAIN_OPTIONAL
        # 档位前瞻高出线、卡片上的数也更高：对调
        adv = self.rated({b: near}, {a: 0.12, b: 0.14})
        self.assertEqual(self.names(adv), [b, a, c])
        self.assertEqual(self.moves(adv, b), [(MOVE_AHEAD, [a])])
        # 档位前瞻高出线、卡片上的数却更低（两样方向相反）：照顺位，排在最前的写差不多（MOVE_MIXED），后一张不写
        adv = self.rated({b: near + 1}, {a: 0.14, b: 0.12})
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertEqual((self.moves(adv, a), self.moves(adv, b)), ([(MOVE_MIXED, [b])], []))
        from advisor.ui import text as ui_text
        view = next(v for v in ui_text.option_views(adv) if v.name == a)
        pill = next(p for p in view.pills if p.text == ui_text.MOVE_NEAR_TEXT)
        self.assertEqual(pill.tone, ui_text.INFO)
        self.assertEqual(pill.tip, ui_text.MOVE_MIXED_TIP.format(tier="可选", others=f"「{b}」（顺位 2）")
                         + f"方案里必选、推荐的筹码在下一次三选一里至少出现一张的机会现在 20%，"
                           f"选「{a}」以后 14%、选「{b}」以后 12%。")
        self.assertIn("只看下一次三选一里至少出现一张的机会算方向相反", pill.tip)
        # 取整后相等（14.4%、13.6% 都写「14%」）：按卡片的数不更低，对调
        adv = self.rated({b: near}, {a: 0.144, b: 0.136})
        self.assertEqual(self.names(adv), [b, a, c])
        # 排在最前的档位前瞻高出线、卡片上的数却比下一张低：不写「前瞻优于」，写差不多（MOVE_MIXED）
        adv = self.rated({a: near}, {a: 0.12, b: 0.14})
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertEqual(self.moves(adv, a), [(MOVE_MIXED, [b])])
        # 两样都更好：照旧「前瞻优于」（MOVE_LEAD）；档位前瞻相差不到线：MOVE_NEAR
        self.assertEqual(self.moves(self.rated({a: near}, {a: 0.14, b: 0.12}), a), [(MOVE_LEAD, [b])])
        self.assertEqual(self.moves(self.rated({b: near - 0.01}, {a: 0.12, b: 0.14}), a), [(MOVE_NEAR, [b])])
        # 三张冒泡：顺位 3 两样都比顺位 2 好、和顺位 1 方向相反：只和顺位 2 对调
        adv = self.rated({b: 0.0, c: near}, {a: 0.16, b: 0.12, c: 0.14})
        self.assertEqual(self.names(adv), [a, c, b])
        self.assertEqual(self.moves(adv, c), [(MOVE_AHEAD, [b])])

    def test_rates_second_line(self):
        # 必选、推荐的那个数取整后相等时，卡片的悬停另写一句可选的：可选的也不能更低，否则照顺位
        near = self.cfg["same_tier_near"]
        a, b, c = PLAIN_OPTIONAL
        adv = self.rated({b: near}, {a: 0.14, b: 0.14}, second={a: 0.30, b: 0.25})
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertEqual(self.moves(adv, a), [(MOVE_MIXED, [b])])
        adv = self.rated({b: near}, {a: 0.14, b: 0.14}, second={a: 0.25, b: 0.30})
        self.assertEqual(self.names(adv), [b, a, c])
        from advisor.ui import text as ui_text
        tip = next(p.tip for v in ui_text.option_views(adv) if v.name == b for p in v.pills
                   if p.text == "前瞻优于可选 1")
        self.assertTrue(tip.endswith(f"方案里必选、推荐的筹码在下一次三选一里至少出现一张的机会现在 20%，"
                                     f"选「{b}」以后 14%、选「{a}」以后 14%。"
                                     f"方案里可选的筹码在下一次三选一里至少出现一张的机会现在 30%，"
                                     f"选「{b}」以后 30%、选「{a}」以后 25%。"), tip)

    def test_rates_optional_only(self):
        # 方案里没有必选、推荐时，卡片上写的是可选在下一次三选一里至少出现一张的机会：同样按卡片上的这个数比
        near = self.cfg["same_tier_near"]
        a, b, c = PLAIN_OPTIONAL
        adv = self.rated({b: near}, {a: 0.14, b: 0.12}, words="可选")
        self.assertEqual(self.names(adv), [a, b, c])
        self.assertEqual(self.moves(adv, a), [(MOVE_MIXED, [b])])
        adv = self.rated({b: near}, {a: 0.12, b: 0.14}, words="可选")
        self.assertEqual(self.names(adv), [b, a, c])
        self.assertEqual(self.moves(adv, b), [(MOVE_AHEAD, [a])])

    def test_komachi_case(self):
        # 小町 0 星第 2 轮，方案里手持风扇-小推荐 3、电流剑推荐 4：电流剑的档位前瞻高出线（2.3 对 −1.8），选了以后方案里的
        # 必选、推荐在下一次三选一里至少出现一张的机会却是 11% 对 14%，方向相反：照顺位，手持风扇-小排在前面，写「前瞻差不多，
        # 照方案顺位」
        tiers = (("拳击手套-高级", "core"), ("循环往复", "core"), ("速度轮滑-高级", "first"), ("魔法箭袋", "first"),
                 ("手持风扇-小", "first"), ("电流剑", "first"), ("额外电池", "first"), ("拳击手套-中级", "second"),
                 ("夹心饼干-美味", "second"), ("鹰眼瞄具", "second"), ("拳击手套-初级", "second"), ("安全气囊", "second"),
                 ("魔法秘典", "second"), ("标记喷罐", "avoid"), ("普通瞄具", "avoid"))
        st = State(hero="小町", star=0, round=2, map="魔法学院", rerolls=2)
        adv = advise(self.db, st, ["手持风扇-小", "电流剑", "智能手表"], scheme=plan(*tiers, hero="小町"))
        fan, sword = adv.ranking.aheads[0], adv.ranking.aheads[1]
        self.assertGreaterEqual(sword.value - fan.value, self.cfg["same_tier_near"])
        self.assertEqual((f"{fan.after:.0%}", f"{sword.after:.0%}"), ("14%", "11%"))
        self.assertEqual(self.names(adv)[:2], ["手持风扇-小", "电流剑"])
        self.assertEqual(self.moves(adv, "手持风扇-小"), [(MOVE_MIXED, ["电流剑"])])
        self.assertEqual(self.moves(adv, "电流剑"), [])
        from advisor.ui import text as ui_text
        view = next(v for v in ui_text.option_views(adv) if v.name == "手持风扇-小")
        tip = next(p.tip for p in view.pills if p.text == ui_text.MOVE_NEAR_TEXT)
        self.assertIn("选「手持风扇-小」以后 14%、选「电流剑」以后 11%", tip)

    def test_single_and_mixed(self):
        a, b, c = PLAIN_OPTIONAL
        # 同一档只有一张：不写
        with fixed_ahead({a: 0.0, b: 50.0}):
            adv = advise(self.db, self.fenny(), list(PLAIN_OPTIONAL), scheme=plan((a, "second"), (b, "first")))
        self.assertEqual(self.names(adv)[:2], [b, a])
        self.assertTrue(all(m == () for m in adv.ranking.moves))
        # 不越档：可选的档位前瞻再高也排在推荐后面
        with fixed_ahead({a: 0.0, b: 50.0}):
            adv = advise(self.db, self.fenny(), list(PLAIN_OPTIONAL), scheme=plan((a, "first"), (b, "second")))
        self.assertEqual(self.names(adv)[:2], [a, b])

    def test_no_later_picks(self):
        # 预计之后没有三选一（第 17 轮，到了进度上限）：档位前瞻都是 0，照顺位，排在最前那张写「之后没有三选一，照顺位」
        adv = advise(self.db, self.fenny(star=3, round=17), list(PLAIN_OPTIONAL),
                     scheme=plan(*((c, "second") for c in reversed(PLAIN_OPTIONAL))))
        self.assertEqual(adv.picks.total, 0)
        self.assertEqual(self.names(adv), list(reversed(PLAIN_OPTIONAL)))
        first = PLAIN_OPTIONAL[-1]
        self.assertEqual(self.moves(adv, first), [(MOVE_LAST, [PLAIN_OPTIONAL[1]])])
        self.assertTrue(all(a.value == 0 and a.short == "" for a in adv.ranking.aheads))
        from advisor.ui import text as ui_text
        view = next(v for v in ui_text.option_views(adv) if v.name == first)
        self.assertIn(("之后没有三选一，照顺位", ui_text.INFO), [(p.text, p.tone) for p in view.pills])


class TierAheadTest(Base):
    """档位前瞻（lookahead.tier_lookahead）：和前瞻同一套（影响的次数、拿 / 不拿两边、候选地图平均），只把之后三选一里每个
    筹码值多少换成档位的分（必选、推荐、可选、不选取 [scheme]，没标的取 unmarked_lookahead，被锁的流派里没标的按不选）；
    缓存和前瞻分开；说明只说本次候选以外、还没持有的必选、推荐（没有时说可选）。"""

    def test_values(self):
        sch = plan(*TWO, ("书签", "avoid"))
        sc = Scorer(self.db, self.fenny(), scheme=sch)
        values = engine.lookahead.tier_values(sc)
        cfg = self.cfg
        self.assertEqual((values["缓冲盾牌"], values["鹰眼瞄具"], values["标靶"], values["书签"]),
                         (cfg["must"], cfg["recommend"], cfg["optional"], cfg["skip"]))
        self.assertEqual(values["额外电池"], cfg["unmarked_lookahead"])  # 没标、不属于流派
        self.assertEqual(values["紫色飞星"], cfg["skip"])  # 被锁的星光里没标的按不选
        self.assertEqual(values["医疗箱-完备治疗"], cfg["unmarked_lookahead"])  # 列进方案的治愈里没标的
        # 走偏进了星光：星光的筹码不再算被锁
        sc = Scorer(self.db, self.fenny(owned=("紫色飞星",)), scheme=sch)
        self.assertEqual(engine.lookahead.tier_values(sc)["金色飞星"], cfg["unmarked_lookahead"])
        self.assertEqual(engine.lookahead.tier_values(sc)["电流剑"], cfg["skip"])

    def test_same_algorithm_separate_cache(self):
        # 档位前瞻 = 前瞻的算法换成档位的分：分值表全换成档位分时和前瞻逐位相同（换一份分值表算前瞻），缓存各放各的
        st = self.fenny(star=2, round=7)  # 2 星：1 次按升星、半次按混合，两种来源都算到
        sch = plan(*TWO)
        offer = ["标靶", "大碗炖肉", "书签"]
        sc = Scorer(self.db, st, offer=offer, scheme=sch)
        tier = {n: sc.tier_lookahead(n, ()) for n in offer}
        self.assertTrue(sc.memo.tier_future)
        self.assertNotEqual(set(tier.values()), {0.0})
        values = engine.lookahead.tier_values(sc)
        twin = Scorer(self.db, st, offer=offer, scheme=sch)
        original = engine.lookahead.future_setup

        def setup(scorer, owned, map_name, mixed=False):
            pool, dists, _ = original(scorer, owned, map_name, mixed)
            return pool, dists, {c.name: values[c.name] for c in pool}

        with mock.patch.object(engine.lookahead, "future_setup", setup), \
                mock.patch.object(engine.lookahead, "_endless_adjusted", lambda *a: None):
            same = {n: twin.lookahead(n, ()) for n in offer}
        for n in offer:
            self.assertAlmostEqual(tier[n], same[n], places=9)
        # 前瞻照旧：两个缓存的键一样、值不一样，互不借用
        for key in sc.memo.tier_future:
            if key in sc.memo.future:
                self.assertIsNot(sc.memo.tier_future[key], sc.memo.future[key])
        self.assertEqual({n: Scorer(self.db, st, offer=offer, scheme=sch).lookahead(n, ()) for n in offer},
                         {n: sc.lookahead(n, ()) for n in offer})

    def test_default_and_edge(self):
        # 默认推荐、已持有的、资料库没有的、之后没有三选一：记 0
        sc = Scorer(self.db, self.fenny())
        self.assertEqual(sc.tier_lookahead("标靶", ()), 0.0)
        sc = Scorer(self.db, self.fenny(owned=("标靶",)), scheme=plan(*TWO))
        self.assertEqual((sc.tier_lookahead("标靶", ("标靶",)), sc.tier_lookahead("不存在", ())), (0.0, 0.0))
        sc = Scorer(self.db, self.fenny(star=3, round=17), scheme=plan(*TWO))  # 到了进度上限
        self.assertEqual(sc.picks.total, 0)
        self.assertEqual(sc.tier_lookahead("标靶", ()), 0.0)
        self.assertEqual(sc.tier_ahead("标靶", ()).reason, "")

    def test_words(self):
        # 说的是本次候选以外、还没持有的必选、推荐；方案里没有时说可选
        st = self.fenny()
        sc = Scorer(self.db, st, offer=["标靶", "缓冲盾牌", "书签"], scheme=plan(*TWO))
        ahead = sc.tier_ahead("标靶", ())
        self.assertEqual(ahead.words, "推荐")  # 必选缓冲盾牌就在本次候选里，不算
        sc = Scorer(self.db, st, offer=["标靶", "大碗炖肉", "书签"], scheme=plan(("标靶", "second"), ("大碗炖肉", "second"),
                                                                           ("鹰眼瞄具", "second")))
        self.assertEqual(sc.tier_ahead("标靶", ()).words, "可选")
        # 同一次三选一的几张，「现在」的机会相同
        sc = Scorer(self.db, st, offer=["标靶", "大碗炖肉", "书签"], scheme=plan(*TWO))
        befores = {f"{sc.tier_ahead(n, ()).before:.6f}" for n in ("标靶", "大碗炖肉", "书签")}
        self.assertEqual(len(befores), 1)


class TierAheadChanceTest(Base):
    """档位前瞻卡片上的数（lookahead.tier_ahead 的 before、after、second）：方案里这几档的筹码在下一次三选一里至少出现一张的
    机会。和逐张的出现率同一套出法（pool.appear_any、appear_any_mix）：每种来源 1 −（1 − L）（1 − G）²，L、G 是这几张在
    最左格、其余两格分布里的概率之和（最左格没有固定稀有度时三格都按其余两格），按来源的占比加权，地图待识别时按候选地图
    平均；不超过 100%。只说一张时就是那一张的出现率（逐位相同）；变化最大的一两张照旧写各自的出现率。"""

    # 芬妮的一套方案：必选 2、推荐 4、可选 5（流派筹码和通用筹码都有）；三张候选标靶、大碗炖肉、书签都标了档位，
    # 卡片上说的是另外 5 张必选、推荐（second 是另外 3 张可选）
    WIDE = (("缓冲盾牌", "core"), ("医疗箱-完备治疗", "core"), ("鹰眼瞄具", "first"), ("标靶", "first"),
            ("魔法箭袋", "first"), ("额外电池", "first"), ("大碗炖肉", "second"), ("书签", "second"),
            ("夹心饼干-一般", "second"), ("手持风扇-小", "second"), ("摩托头盔-中级", "second"))
    OFFER = ["标靶", "大碗炖肉", "书签"]
    WANTED = ["缓冲盾牌", "医疗箱-完备治疗", "鹰眼瞄具", "魔法箭袋", "额外电池"]
    LOWER = ["夹心饼干-一般", "手持风扇-小", "摩托头盔-中级"]

    def states(self) -> list[State]:
        """1 星、地图已知（升星，最左格是固定稀有度）；3 星、地图待识别（任务和筹码商店两种来源，几张候选地图平均）；
        3 星、循环往复给的那次还没给（下一次是任务来源，最左格没有固定稀有度）。"""
        return [self.fenny(), self.fenny(star=3, round=9, map=None),
                self.fenny(star=3, round=9, owned=("循环往复",), endless_pending=True)]

    def test_sources_covered(self):
        # 三个局面分别用到：一种来源、最左格有分布；几张地图、两种来源（任务的最左格没有分布，筹码商店的有）；一种来源、
        # 最左格没有分布
        shapes = []
        for st in self.states():
            sc = Scorer(self.db, st, offer=self.OFFER, scheme=plan(*self.WIDE))
            _, dists, _ = engine.lookahead.future_setup(sc, sc.known_owned, sc.maps[0])
            shapes.append((len(sc.maps) > 1, tuple(left is None for _, left, _ in dists)))
            self.assertGreater(sc.lookahead_times(), 0)
        self.assertEqual(shapes, [(False, (False,)), (True, (True, False)), (False, (True,))])

    def test_single_chip_is_its_appear_prob(self):
        # 只说一张时，卡片上的数就是那一张的出现率（lookahead._avg_probs，pool.appear_prob_mix），逐位相同
        from advisor.pool import appear_any, appear_any_mix, appear_prob, appear_prob_mix
        for st in self.states():
            sc = Scorer(self.db, st, offer=self.OFFER, scheme=plan(("鹰眼瞄具", "first"),
                                                                 *((c, "second") for c in self.OFFER)))
            owned = sc.known_owned
            with self.subTest(star=st.star, map=st.map, endless=st.endless_pending):
                for name in self.OFFER:
                    ahead = sc.tier_ahead(name, owned)
                    p0, _ = engine.lookahead._avg_probs(sc, owned)
                    p1, _ = engine.lookahead._avg_probs(sc, (*owned, name))
                    self.assertEqual(ahead.words, "推荐")
                    self.assertEqual((ahead.before, ahead.after), (p0.get("鹰眼瞄具", 0.0), p1.get("鹰眼瞄具", 0.0)))
                    self.assertIsNone(ahead.second)
                # 筹码池里的每一张、每种来源都逐位相同；不在分布里的为 0
                for m in sc.maps:
                    _, dists, _ = engine.lookahead.future_setup(sc, owned, m)
                    names = {n for _, left, general in dists for n in (*general, *(left or ()))}
                    for n in names:
                        self.assertEqual(appear_any_mix(dists, [n]), appear_prob_mix(dists, n), n)
                        for _, left, general in dists:
                            self.assertEqual(appear_any(left, general, [n]), appear_prob(left, general, n), n)
                    self.assertEqual(appear_any_mix(dists, ["不存在"]), 0.0)
                    self.assertEqual(appear_any_mix(dists, []), 0.0)

    def test_between_largest_and_sum(self):
        # 几张时不小于其中最大的一张，不大于各张出现率之和，也不超过 100%（可选那一个同样）
        for st in self.states():
            sc = Scorer(self.db, st, offer=self.OFFER, scheme=plan(*self.WIDE))
            owned = sc.known_owned
            p0, _ = engine.lookahead._avg_probs(sc, owned)
            for name in self.OFFER:
                ahead = sc.tier_ahead(name, owned)
                p1, _ = engine.lookahead._avg_probs(sc, (*owned, name))
                self.assertEqual(ahead.words, "必选、推荐")
                for got, probs, chips in ((ahead.before, p0, self.WANTED), (ahead.after, p1, self.WANTED),
                                          (ahead.second[0], p0, self.LOWER), (ahead.second[1], p1, self.LOWER)):
                    each = [probs.get(c, 0.0) for c in chips]
                    with self.subTest(star=st.star, map=st.map, name=name, chips=chips[0]):
                        self.assertGreaterEqual(got, max(each) - 1e-12)
                        self.assertLessEqual(got, min(1.0, sum(each)) + 1e-12)
                        self.assertGreater(sum(each) - got, 0.0)  # 几张都有机会出现时比相加少

    def test_independent_algorithm(self):
        # 另写一份（直接从 future_setup 的分布算：先按占比加权「一张都不出现」，再用 1 减），核对固定的方案局面
        def chance(sc, owned, chips):
            total = 0.0
            for m in sc.maps:
                _, dists, _ = engine.lookahead.future_setup(sc, owned, m)
                miss = 0.0
                for share, left, general in dists:
                    g = sum(general.get(c, 0.0) for c in chips)
                    first = g if left is None else sum(left.get(c, 0.0) for c in chips)
                    miss += share * (1 - first) * (1 - g) * (1 - g)
                total += 1 - miss
            return total / len(sc.maps)

        for st in self.states():
            sc = Scorer(self.db, st, offer=self.OFFER, scheme=plan(*self.WIDE))
            owned = sc.known_owned
            for name in self.OFFER:
                ahead = sc.tier_ahead(name, owned)
                taken = (*owned, name)
                with self.subTest(star=st.star, map=st.map, endless=st.endless_pending, name=name):
                    self.assertAlmostEqual(ahead.before, chance(sc, owned, self.WANTED), places=12)
                    self.assertAlmostEqual(ahead.after, chance(sc, taken, self.WANTED), places=12)
                    self.assertAlmostEqual(ahead.second[0], chance(sc, owned, self.LOWER), places=12)
                    self.assertAlmostEqual(ahead.second[1], chance(sc, taken, self.LOWER), places=12)

    def test_never_over_100(self):
        # 方案里标得多时，各张出现率相加会超过 100%（下一次三选一可能见到的筹码几乎都标了），至少出现一张的机会不会
        st = self.fenny(star=0, round=2)
        probe = Scorer(self.db, st, offer=self.OFFER)
        pool, _, _ = engine.lookahead.future_setup(probe, (), probe.maps[0])
        many = [c.name for c in pool if c.name not in self.OFFER]
        sc = Scorer(self.db, st, offer=self.OFFER,
                    scheme=plan(*((c, "first") for c in many), *((c, "second") for c in self.OFFER)))
        p0, _ = engine.lookahead._avg_probs(sc, ())
        self.assertGreater(sum(p0.get(c, 0.0) for c in many), 2.0)
        for name in self.OFFER:
            ahead = sc.tier_ahead(name, ())
            self.assertEqual(ahead.words, "推荐")
            self.assertLessEqual(ahead.before, 1.0)
            self.assertLessEqual(ahead.after, 1.0)
            self.assertGreater(ahead.before, 0.9)
            if ahead.short:
                self.assertLessEqual(len(ahead.short), engine.SHORT_LIMIT)
                self.assertNotRegex(ahead.short, r"\d{3,}%")  # 不会写出超过 100% 的数

    def test_wording(self):
        # 卡片短句照旧「前瞻：可选 X%→Y%」；悬停整句写「至少出现一张的机会」，方案里没有还没持有的必选、推荐时说可选
        st = self.fenny()
        sc = Scorer(self.db, st, offer=self.OFFER, scheme=plan(("标靶", "second"), ("大碗炖肉", "second"),
                                                             ("书签", "second"), ("鹰眼瞄具", "second"),
                                                             ("魔法箭袋", "second")))
        for name in self.OFFER:
            ahead = sc.tier_ahead(name, ())
            before, after = f"{ahead.before:.0%}", f"{ahead.after:.0%}"
            self.assertEqual(ahead.words, "可选")
            self.assertTrue(ahead.reason.startswith(
                f"选择后，筹码方案里可选的筹码在下一次三选一里至少出现一张的机会 {before} → {after}"), ahead.reason)
            self.assertEqual(ahead.short, "" if before == after else f"前瞻：可选 {before}→{after}")
            self.assertNotIn("合计", ahead.reason)
            for chip, q0, q1 in ahead.items:  # 括号里是各自的出现率
                self.assertIn(f"{chip} {q0:.0%} → {q1:.0%}", ahead.reason)

    def test_one_chip(self):
        # 说的那几档只有一张（方案里还没持有、不在本次候选里的必选、推荐只有鹰眼瞄具）：至少出现一张就是它自己的出现率，
        # 取整后变了时括号里只写它的名字，不把同一个数再写一遍；取整后没变时和几张时一样不写括号
        sc = Scorer(self.db, self.fenny(), offer=self.OFFER,
                    scheme=plan(("鹰眼瞄具", "core"), *((c, "second") for c in self.OFFER)))
        kinds = set()
        for name in self.OFFER:
            ahead = sc.tier_ahead(name, ())
            self.assertEqual(ahead.words, "必选")
            head = engine.lookahead.TIER_AHEAD_REASON.format(words="必选", before=f"{ahead.before:.0%}",
                                                             after=f"{ahead.after:.0%}")
            self.assertEqual(ahead.reason, head + ("（鹰眼瞄具）" if ahead.items else ""))
            # 只说一张时它自己的出现率取整后变不变，就是整句的数变不变（卡片上做不做标签）；items 照旧记着它的出现率
            self.assertEqual(bool(ahead.items), bool(ahead.short))
            self.assertEqual([c for c, _, _ in ahead.items], ["鹰眼瞄具"] if ahead.items else [])
            kinds.add(bool(ahead.items))
        self.assertEqual(kinds, {True, False})  # 标靶 2% → 3%（写名字）、大碗炖肉和书签 2% → 2%（不写括号）都有


class LostTest(Base):
    """拿不到的往后：K =（还能拿到的必选个数, 推荐个数），已持有的算拿到，属于流派的要在之后最好的流派搭配里（先保必选、
    再保推荐）。必选、推荐候选只看必选个数，可选、未标记、不选、被锁的按字典序比；比现在少就算拿不到，排到保得住的候选
    后面（没有拿不到的 → 只少了推荐的 → 少了必选的，稳定分组；不选、被锁的照旧在最后）。之后预计没有三选一时不算。"""

    CROSS = (("标靶", "core"), ("星币锤", "first"), ("标记喷罐", "first"), ("手持风扇-小", "second"))

    def test_cross_tier_example(self):
        # 邦妮的方案（必选标靶，推荐星币锤、标记喷罐，可选手持风扇-小），已有可口糖果：星币锤让标靶拿不到，算拿不到；
        # 手持风扇-小虽然让星币锤拿不到，但要保住标靶本来就拿不到星币锤，不算；摩托头盔-中级不改流派
        sch = plan(*self.CROSS, hero="邦妮")
        for star, rnd in ((1, 4), (2, 7)):
            st = State(hero="邦妮", star=star, round=rnd, map="魔法学院", owned=("可口糖果",), rerolls=3)
            adv = advise(self.db, st, ["星币锤", "手持风扇-小", "摩托头盔-中级"], scheme=sch)
            with self.subTest(star=star):
                self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["手持风扇-小", "摩托头盔-中级", "星币锤"])
                coin, fan = self.loss_of(adv, "星币锤"), self.loss_of(adv, "手持风扇-小")
                self.assertEqual((coin.kind, coin.warn, coin.lost), (LOSS_NOW, True, LOST_CORE))
                self.assertEqual([adv.evals[k].chip.name for k in coin.behind], ["手持风扇-小", "摩托头盔-中级"])
                self.assertEqual((fan.kind, fan.warn, fan.lost, fan.chips), (LOSS_NOW, False, "", (("星币锤", "first"),)))
                self.assertIsNone(self.loss_of(adv, "摩托头盔-中级"))
                self.assertNotEqual(adv.best, "星币锤")
        # K 的取值
        sc = Scorer(self.db, State(hero="邦妮", star=1, round=4, owned=("可口糖果",)), scheme=sch)
        reach = engine.scheme_rank.reach
        self.assertEqual(reach(sc, ("可口糖果",)), (1, 1))  # 治愈 + 标记：标靶、标记喷罐
        self.assertEqual(reach(sc, ("可口糖果", "星币锤")), (0, 1))
        self.assertEqual(reach(sc, ("可口糖果", "手持风扇-小")), (1, 1))
        self.assertEqual(reach(sc, ("可口糖果", "摩托头盔-中级")), (1, 1))
        self.assertEqual(reach(sc, ()), (1, 2))  # 还没进流派：标记 + 星光

    def test_must_vs_must(self):
        # 必选照顺位；会让另一张必选拿不到时才往后排：已进治愈，只剩一个名额，星光有两张必选、标记一张：选标靶（必选 1）
        # 会让星光的两张必选拿不到，排到紫色飞星（必选 2）和书签后面
        sch = plan(("标靶", "core"), ("紫色飞星", "core"), ("星币锤", "core"))
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), ["标靶", "紫色飞星", "书签"], scheme=sch)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["紫色飞星", "书签", "标靶"])
        self.assertEqual(adv.best, "紫色飞星")
        self.assertEqual(self.loss_of(adv, "标靶").lost, LOST_CORE)
        # 两张必选都不会让别的必选拿不到：照顺位
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), ["星币锤", "紫色飞星", "书签"], scheme=sch)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL][:2], ["紫色飞星", "星币锤"])

    def test_recommend_only(self):
        # 只少了推荐：可选、未标记的候选算拿不到（排到没有拿不到的后面）；推荐、必选候选只看必选，不算
        sch = plan(("标记喷罐", "first"), ("鹰眼瞄具", "first"), ("紫色飞星", "first"), ("金色飞星", "second"))
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), ["金色飞星", "书签", "标记喷罐"], scheme=sch)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["标记喷罐", "书签", "金色飞星"])
        self.assertEqual(self.loss_of(adv, "金色飞星").lost, LOST_FIRST)
        sc = Scorer(self.db, self.fenny(owned=("缓冲盾牌",)), scheme=sch)
        star = SimpleNamespace(chip=self.db.chips["紫色飞星"], unrecorded=False)
        lost_kind = engine.scheme_rank.lost_kind
        self.assertEqual(lost_kind(sc, star, Place(PLACE_MARKED, "first", 3)), "")  # 推荐：只看必选
        self.assertEqual(lost_kind(sc, star, Place(PLACE_UNMARKED)), LOST_FIRST)  # 同一张若是未标记：算
        # 不属于流派的、已进入的流派的、资料库没有的：不算
        for name in ("书签", "缓冲盾牌"):
            chip = SimpleNamespace(chip=self.db.chips[name], unrecorded=False)
            self.assertEqual(lost_kind(sc, chip, Place(PLACE_UNMARKED)), "", name)

    def test_keepers_only_last_or_none(self):
        # 已进治愈、只剩一个名额；方案必选紫色飞星、金色飞星（星光）、标靶（标记），可选标记喷罐，不选书签。
        # 选标记喷罐会进标记、星光的两张必选拿不到（橙色），能保住的只有不选的书签：书签照旧排在最后，标记喷罐仍排在前面，
        # 悬停写明这一点（Loss.keep_last）。被锁的电流剑三张必选都拿不到（橙色），本来就排在最后，不另写
        sch = plan(("紫色飞星", "core"), ("金色飞星", "core"), ("标靶", "core"), ("缓冲盾牌", "second"),
                   ("标记喷罐", "second"), ("书签", "avoid"))
        names = ["标记喷罐", "书签", "电流剑"]
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), names, scheme=sch)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], names)
        spray, sword = self.loss_of(adv, "标记喷罐"), self.loss_of(adv, "电流剑")
        self.assertEqual((spray.warn, spray.lost, spray.behind, spray.keep_last, spray.alone),
                         (True, LOST_CORE, (), (1,), False))
        self.assertEqual((sword.warn, sword.lost, sword.behind, sword.keep_last, sword.alone),
                         (True, LOST_CORE, (), (), False))
        self.assertIsNone(self.loss_of(adv, "书签"))
        from advisor.ui import text as ui_text
        tip = ui_text.loss_pills(spray, NAME, names)[0].tip
        self.assertTrue(tip.endswith("另选「书签」能保住必选，但它按不选排在最后，所以这张仍排在前面。"), tip)
        self.assertTrue(ui_text.loss_pills(sword, NAME, names)[0].tip.endswith("「标靶」。"))  # 只写拿不到什么
        self.assertIn("（橙色；另选「书签」能保住必选，但它按不选排在最后）", cli.loss_line(spray, names))
        # 换成未标记的鹰眼瞄具（标记）：三张选哪张都会少拿必选，没有能保住的，照原来的先后排，悬停写明（Loss.alone）
        names = ["标记喷罐", "鹰眼瞄具", "电流剑"]
        adv = advise(self.db, self.fenny(owned=("缓冲盾牌",)), names, scheme=sch)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], names)
        for chip in names[:2]:
            loss = self.loss_of(adv, chip)
            with self.subTest(chip=chip):
                self.assertEqual((loss.warn, loss.lost, loss.behind, loss.keep_last, loss.alone),
                                 (True, LOST_CORE, (), (), True))
                self.assertTrue(ui_text.loss_pills(loss, NAME, names)[0].tip.endswith(
                    "这次的候选选哪张都会少拿方案里的必选、推荐，所以照原来的先后排。"))
                self.assertIn("（橙色；这次的候选选哪张都会少拿）", cli.loss_line(loss, names))
        self.assertFalse(self.loss_of(adv, "电流剑").alone)

    def test_final_pick(self):
        # 之后预计没有三选一（第 17 轮，到了进度上限）：之后本来就拿不到别的筹码，不算拿不到、不挪（提醒照旧，灰色）
        sch = plan(("标靶", "core"), ("紫色飞星", "core"), ("金色飞星", "core"))
        adv = advise(self.db, self.fenny(star=3, round=17, owned=("缓冲盾牌",)), ["标靶", "紫色飞星", "书签"], scheme=sch)
        self.assertEqual(adv.picks.total, 0)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["标靶", "紫色飞星", "书签"])
        loss = self.loss_of(adv, "标靶")
        self.assertEqual((loss.kind, loss.warn, loss.lost, loss.final), (LOSS_NOW, False, "", True))


class DemotionTest(Base):
    """收入降档：标了推荐、可选的收入类筹码，只按剩下几次兑现折算扣掉的比例（和折算同一个基数；满 3 星后非经济型角色的
    减半、晚拿银行卡的减半不算进去）不小于 [scheme] income_demote 时按低一档排（推荐当可选、可选当未标记），排在那一档已有的
    候选后面，和刷新比也按低一档；位次照写原来的档，另有橙色理由；必选不扣收入折算，也不降档；轮次未知时不降。"""

    PARU = (("银行卡-用不完", "core"), ("ATM机", "first"), ("“8面”骰子", "first"), ("摩托头盔-高级", "second"),
            ("速度轮滑-高级", "second"), ("拳击手套-高级", "second"), ("缓冲盾牌", "second"))
    OFFER = ["ATM机", "速度轮滑-高级", "摩托头盔-高级"]

    def state(self, star, rnd, **kw):
        return State(**{**dict(hero="帕露南", star=star, round=rnd, map="魔法学院",
                               owned=("银行卡-余额多", "夹心饼干-一般"), rerolls=2 + star), **kw})

    def test_example(self):
        sch = plan(*self.PARU, hero="帕露南")
        # 3 星第 12 轮：ATM机（推荐 1）收入折算扣掉 80%，当可选排在两张可选后面，不再是首推
        adv = advise(self.db, self.state(3, 12), self.OFFER, scheme=sch)
        d = adv.ranking.demotions[0]
        self.assertEqual((d.tier, d.brief), ("second", "星光只剩 1 次兑现"))
        self.assertAlmostEqual(d.cut, 0.8, places=6)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL], ["摩托头盔-高级", "速度轮滑-高级", "ATM机"])
        self.assertNotEqual(adv.best, "ATM机")
        self.assertEqual(place_text(self.place_of(adv, "ATM机")), "推荐 1")  # 位次照写原来的档
        # 扣掉的比例和收入折算同一个基数：扣分 ÷ 正分（不含成长分）
        atm = adv.evals[0]
        income = next(p for p in atm.parts if p.label == "收入折算")
        base = sum(p.score for p in atm.parts if p.score > 0 and p.label != "成长分")
        self.assertAlmostEqual(d.cut, -income.score / base, places=9)
        # 卡片：橙色「星光只剩 1 次兑现，按低一档排」，收入折算那一项不再另做标签
        from advisor.ui import text as ui_text
        view = next(v for v in ui_text.option_views(adv) if v.name == "ATM机")
        texts = [(p.text, p.tone) for p in view.pills]
        self.assertIn(("星光只剩 1 次兑现，按低一档排", ui_text.MINUS), texts)
        self.assertFalse(any(p.text.startswith("星光剩余") for p in view.pills))
        tip = next(p.tip for p in view.pills if p.text.startswith("星光只剩"))
        self.assertTrue(tip.endswith("只按剩下的兑现次数算，收入部分的价值也已打折一半以上，按低一档排：当作可选，"
                                     "排在可选的候选后面（位次照写「推荐 1」）。"), tip)
        # 2 星第 8 轮扣得少：照旧排第一、首推
        adv = advise(self.db, self.state(2, 8), self.OFFER, scheme=sch)
        self.assertIsNone(adv.ranking.demotions[0])
        self.assertEqual(adv.best, "ATM机")

    def bonnie(self, star, rnd, chip="ATM机", **kw):
        """非经济型角色（邦妮）、方案里 chip 标推荐 1，候选 chip / 速度轮滑-高级 / 摩托头盔-高级（两张可选）。"""
        sch = plan((chip, "first"), ("速度轮滑-高级", "second"), ("摩托头盔-高级", "second"), hero="邦妮")
        st = State(**{**dict(hero="邦妮", star=star, round=rnd, map="魔法学院",
                             owned=("银行卡-余额多", "夹心饼干-一般"), rerolls=3), **kw})
        return advise(self.db, st, [chip, "速度轮滑-高级", "摩托头盔-高级"], scheme=sch)

    def test_max_star_not_counted(self):
        # 非经济型角色第 7 轮刚满 3 星、还有 3 次兑现：收入折算连同满 3 星的减半共扣 70%，只按剩下的兑现次数算扣 40%，不降
        from advisor.engine.parts import income_cut
        adv = self.bonnie(3, 7)
        atm = adv.evals[0]
        found = income_cut(Scorer(self.db, adv.state, scheme=adv.scheme), atm.chip, atm.parts, 7,
                           frozenset(adv.state.owned))
        self.assertAlmostEqual(found.cut, 0.7, places=9)
        self.assertAlmostEqual(found.left_cut, 0.4, places=9)
        self.assertIsNone(adv.ranking.demotions[0])
        self.assertEqual(adv.best, "ATM机")
        self.assertIn("已达到 3 星，星币不再用于升星", found.part.reason)  # 减半照旧算进分里
        # 进度上限 16（魔法学院）：第 8 轮还有 3 次兑现，不降；第 9 轮只剩 2 次兑现（扣 60%）就降（使用说明里的例子）
        self.assertIsNone(self.bonnie(3, 8).ranking.demotions[0])
        nine = self.bonnie(3, 9).ranking.demotions[0]
        self.assertEqual((nine.tier, nine.brief), ("second", "星光只剩 2 次兑现"))
        self.assertAlmostEqual(nine.cut, 0.6, places=9)
        # 第 12 轮只剩 1 次兑现：照样降
        late = self.bonnie(3, 12)
        self.assertEqual((late.ranking.demotions[0].tier, late.ranking.demotions[0].brief), ("second", "星光只剩 1 次兑现"))
        self.assertAlmostEqual(late.ranking.demotions[0].cut, 0.8, places=9)
        # 轮次读不出：没有剩余兑现那一层，不降（收入折算照旧按满 3 星减半，扣 50%）
        unknown = self.bonnie(3, None)
        self.assertIsNone(unknown.ranking.demotions[0])
        self.assertEqual(unknown.best, "ATM机")

    def test_late_bank_card_not_counted(self):
        # 晚拿的银行卡（[income] late，银行卡-余额多第 7 轮起）收入再减半也不算进降档的比例：第 7 轮还有 3 次兑现，不降
        adv = self.bonnie(2, 7, chip="银行卡-余额多", owned=("夹心饼干-一般",))
        card = adv.evals[0]
        self.assertTrue(any(p.label == "收入折算" and "收入再减半" in p.reason for p in card.parts))
        self.assertIsNone(adv.ranking.demotions[0])

    def test_line_exactly_half(self):
        # 只按剩下的兑现次数正好扣一半（小猪存钱罐，进度上限 17、第 9 轮：星币收入还剩 8 轮 ÷ 16 轮）：降；第 8 轮不降
        adv = self.bonnie(2, 9, chip="小猪存钱罐", progress_cap=17)
        d = adv.ranking.demotions[0]
        self.assertEqual((d.tier, d.brief), ("second", "收入只剩 8 轮"))
        self.assertEqual(d.cut, 0.5)
        self.assertIsNone(self.bonnie(2, 8, chip="小猪存钱罐", progress_cap=17).ranking.demotions[0])

    def test_must_and_reroll_level(self):
        # 必选那一项不扣收入折算（只折算其余的正分），也不降档
        must = plan(("ATM机", "core"), ("书签", "avoid"), ("大背包", "avoid"), hero="帕露南")
        adv = advise(self.db, self.state(3, 12), ["ATM机", "书签", "大背包"], scheme=must)
        self.assertIsNone(adv.ranking.demotions[0])
        self.assertEqual(adv.best, "ATM机")
        # 推荐降成可选、排在最前：和刷新比按可选（2 级）
        first = plan(("ATM机", "first"), ("书签", "avoid"), ("大背包", "avoid"), hero="帕露南")
        adv = advise(self.db, self.state(3, 12), ["ATM机", "书签", "大背包"], scheme=first)
        self.assertEqual(adv.ranking.demotions[0].tier, "second")
        self.assertEqual((adv.ranking.reroll.compare, adv.ranking.reroll.level), (0, 2))
        # 可选降成未标记：排在未标记的后面；排在最前时按未标记和刷新比（刷出排在它前面的概率）
        second = plan(("ATM机", "second"), hero="帕露南")
        adv = advise(self.db, self.state(3, 12), ["ATM机", "书签", "大背包"], scheme=second)
        self.assertEqual(adv.ranking.demotions[0].tier, None)
        self.assertEqual([n for n in self.order_names(adv) if n != REROLL][-1], "ATM机")
        alone = plan(("ATM机", "second"), ("书签", "avoid"), ("大背包", "avoid"), hero="帕露南")
        adv = advise(self.db, self.state(3, 12), ["ATM机", "书签", "大背包"], scheme=alone)
        self.assertEqual((adv.ranking.reroll.basis, adv.ranking.reroll.level), (BASIS_UNMARKED, 1))


LOST_GROUP = {"": 0, LOST_FIRST: 1, LOST_CORE: 2}


class InvariantTest(Base):
    """按方案计算时的不变量，用随机局面守住：三张候选各取必选、推荐、可选、不选或未标记（5 × 5 × 5 = 125 种组合），
    每种组合有、没有刷新次数各一次（共 250 个局面，角色、候选和方案里另标的筹码随机）。每个局面都查：
    - 位次照方案（档位和顺位）；
    - 卡片上的先后按有效档（收入降档的按低一档）和顺位排；同一档的推荐、可选只有档位前瞻高出不少于线、卡片上写的数
      （下一次三选一里至少出现一张必选、推荐的机会）也不更低时才倒过来，倒过来的两张两样都更好；
    - 拿不到的往后，不选、被锁的在最后；
    - 标得高的不被同系列上位替代藏掉；
    - 自己的方案里不出默认推荐的任何标签和加分；
    - 刷新的位置和首推一致，推荐度和先后一致；
    - 「拿不到」的种类、颜色和附注都对得上方案。"""

    def test_combinations(self):
        rng = random.Random(20261006)
        choices = (*TIERS, None)
        heroes = [h for h in self.db.heroes]
        checked = 0
        for tiers in ((a, b, c) for a in choices for b in choices for c in choices):
            for rerolls in (0, 3):
                hero = rng.choice(heroes)
                st, offer, rarity = random_case(self.db, rng, hero, 2)
                st = dataclasses.replace(st, rerolls=rerolls)
                others = [c.name for c in self.db.chips.values() if not c.granted and c.name not in offer]
                marks = [(c, t) for c, t in zip(offer, tiers) if t and c in self.db.chips]
                marks += [(c, rng.choice(TIERS)) for c in rng.sample(others, rng.randint(0, 8))]
                rng.shuffle(marks)  # 同一档的顺位随机
                sch = plan(*marks, hero=hero)
                adv = advise(self.db, st, offer, rarity, scheme=sch)
                with self.subTest(tiers=tiers, rerolls=rerolls, case=f"advise(load_db(), {st!r}, {offer!r}, {rarity!r}, scheme={sch!r})"):
                    self.check(st, sch, adv)
                checked += 1
        self.assertEqual(checked, 250)

    def check(self, st, sch: Plan, adv):
        db, r = self.db, adv.ranking
        marks, ranks = sch.marks(), sch.ranks()
        evals = adv.evals
        self.assertIsNotNone(r)
        # 位次照方案
        for e, p in zip(evals, r.places):
            if e.chip.name in marks:
                self.assertEqual((p.kind, p.tier, p.rank), (PLACE_MARKED, marks[e.chip.name], ranks[e.chip.name]))
            else:
                self.assertIn(p.kind, (PLACE_UNMARKED, PLACE_STRAYED, PLACE_LOCKED))
                if p.kind != PLACE_UNMARKED:
                    self.assertIn(p.school, r.strayed if p.kind == PLACE_STRAYED else r.locked)
        # 先后：每个选项出现一次，被替代的在最后
        options = [k for k in r.order if k != REROLL_SLOT]
        self.assertEqual(sorted(options), list(range(len(evals))))
        self.assertEqual(r.order.count(REROLL_SLOT), 1 if adv.reroll else 0)
        active = [k for k in options if not evals[k].superseded_by]
        self.assertEqual(options, active + [k for k in options if evals[k].superseded_by])
        # 收入降档：只降标了推荐、可选的，降一档；有效档用来排先后、和刷新比
        eff = {}
        for k in active:
            p, d = r.places[k], r.demotions[k]
            if d is not None:
                self.assertEqual((p.kind, d.tier), (PLACE_MARKED, {"first": "second", "second": None}[p.tier]))
                self.assertGreaterEqual(d.cut, db.rules["scheme"]["income_demote"] - 1e-9)
            eff[k] = engine.scheme_rank.effective(p, d)
        # 不选、被锁的在最后；其余按「拿不到的往后」分组（没有拿不到的 → 只少了推荐 → 少了必选），组内按有效档排
        last = [k for k in active if _class(eff[k]) >= _class(Place(PLACE_MARKED, "avoid", 1))]
        head = [k for k in active if k not in last]
        self.assertEqual(active, head + last)
        groups = [LOST_GROUP[r.losses[k].lost if r.losses[k] else ""] for k in head]
        self.assertEqual(groups, sorted(groups))
        near = db.rules["scheme"]["same_tier_near"]
        # 档位前瞻卡片上的数是「至少出现一张的机会」：在 0～100% 之间，同一次三选一的几张「现在」那个数相同
        shown = [a for a in r.aheads if a is not None and a.words]
        for a in shown:
            for x in (a.before, a.after, *(a.second or ())):
                self.assertTrue(0.0 <= x <= 1.0, a)
        self.assertLessEqual(len({(a.words, a.before, a.second and a.second[0]) for a in shown}), 1)
        for group in set(groups):
            ks = [k for k, g in zip(head, groups) if g == group]
            self.assertEqual([_class(eff[k]) for k in ks], sorted(_class(eff[k]) for k in ks))
            for a, b in zip(ks, ks[1:]):
                pa, pb = r.places[a], r.places[b]
                if _class(eff[a]) != _class(eff[b]):
                    continue
                da, db_ = r.demotions[a], r.demotions[b]
                self.assertFalse(da is not None and db_ is None)  # 降档进来的排在那一档已有的后面
                if da is not None or pa.kind != PLACE_MARKED:
                    continue
                if pa.tier == "core":
                    self.assertLess(pa.rank, pb.rank)  # 必选照顺位
                elif pa.rank > pb.rank:  # 推荐、可选倒过顺位：两样都更好（档位前瞻高出不少于线，卡片上的数不更低）
                    self.assertGreaterEqual(r.aheads[a].value - r.aheads[b].value, near - 1e-9)
                    rate = engine.scheme_rank.rate_key
                    self.assertTrue(engine.scheme_rank._rate_ok(rate(r.aheads[b]), rate(r.aheads[a])))
        gap = db.rules["advice"]["series_near"]
        for a, b in zip(head, head[1:]):
            same = _class(eff[a]) == _class(eff[b]) and r.places[a].kind != PLACE_MARKED and r.demotions[a] is None
            if same and r.demotions[b] is None and LOST_GROUP[r.losses[a].lost if r.losses[a] else ""] == \
                    LOST_GROUP[r.losses[b].lost if r.losses[b] else ""]:
                if evals[a].total < evals[b].total - 1e-9:  # 只有同系列总分接近、稀有度高的排前时可以倒过来
                    self.assertTrue(any(p.low == evals[b].chip.name and p.high == evals[a].chip.name for p in db.series))
                    self.assertLess(evals[b].total - evals[a].total, gap)
        # 标得高的不被上位替代藏掉
        place = {e.chip.name: r.places[i] for i, e in enumerate(evals)}
        for e in evals:
            if e.superseded_by:
                self.assertLessEqual(_class(place[e.superseded_by]), _class(place[e.chip.name]))
        for p in db.series:
            if p.strict and p.low in place and p.high in place and _class(place[p.low]) < _class(place[p.high]):
                self.assertIsNone(next(e for e in evals if e.chip.name == p.low).superseded_by)
        # 首推、比较对象
        top = active[0]
        self.assertEqual(adv.top, top)
        self.assertEqual(adv.best, REROLL if r.order[0] == REROLL_SLOT else evals[r.order[0]].chip.name)
        levels = [place_level(eff[k]) for k in active]  # 和刷新比时按有效档（收入降档的按低一档）
        if levels[0] == 4:
            self.assertEqual(adv.best, evals[top].chip.name)  # 有必选不首推刷新
        if adv.reroll is not None:
            v = r.reroll
            self.assertEqual((v.compare, v.first, v.all_avoid, v.level), (top, adv.best == REROLL, max(levels) == 0,
                                                                          levels[0]))
            if v.all_avoid:
                self.assertEqual(adv.best, REROLL)
            elif v.basis == BASIS_UNMARKED:
                # 刷出排在它前面的过半，并且扣掉代价以后的刷新也高于它的总分
                worth = engine.tie_key(adv.reroll.value) > engine.tie_key(evals[top].total)
                self.assertEqual(v.first, engine.tie_key(v.p_before) > 0.5 and worth)
            else:
                self.assertEqual(v.first, v.verdict in (VERDICT_BETTER, VERDICT_EVEN_BETTER))
            self.assertAlmostEqual(sum(v.levels.values()), 1.0)
        # 推荐度：有必选 100 / 0；全是不选看有没有刷新；其余按先后从高到低，不选和被锁 0
        percents = [adv.reroll.percent if k == REROLL_SLOT else evals[k].percent for k in r.order if
                    k == REROLL_SLOT or not evals[k].superseded_by]
        if max(levels) == 0 and adv.reroll is None:
            self.assertEqual(sum(percents), 0)
        else:
            self.assertAlmostEqual(sum(percents), 1.0)
            self.assertEqual(percents, sorted(percents, reverse=True))
        for k, lv in zip(active, levels):
            if lv == 0 or (levels[0] == 4 and k != top):
                self.assertEqual(evals[k].percent, 0.0)
        # 不出默认推荐的任何标签和加分
        for e in evals:
            self.assertFalse(DEFAULT_ONLY & set(labels(e.parts)))
            self.assertFalse(any("默认推荐" in p.reason or "优先于" in p.reason for p in e.parts))
            tier_parts = [p for p in e.parts if p.origin in SCHEME_TIER_ORIGINS]
            self.assertEqual(len(tier_parts), 1 if e.chip.name in marks else 0)
        # 拿不到的：只算没持有的必选、推荐，种类和颜色照规矩
        owned = set(st.owned)
        sc = Scorer(db, st, offer=[e.chip.name for e in evals], scheme=sch)
        now = engine.scheme_rank.reach(sc, sc.known_owned)
        for i, (e, loss) in enumerate(zip(evals, r.losses)):
            lost = "" if e.superseded_by else engine.scheme_rank.lost_kind(sc, e, r.places[i])
            if lost:  # 算拿不到的一定有「选了之后拿不到……」的提醒（卡片上看得到为什么排在后面）
                self.assertIsNotNone(loss)
                after = engine.scheme_rank.reach(sc, (*sc.known_owned, e.chip.name))
                self.assertLess(after, now)
                self.assertEqual(after[0] < now[0], lost == LOST_CORE)
            if loss is None:
                continue
            self.assertIsNone(e.superseded_by)
            self.assertTrue(loss.chips)
            for chip, tier in loss.chips:
                self.assertIn(tier, ("core", "first"))
                self.assertEqual(marks[chip], tier)
                self.assertNotIn(chip, owned)
            # 颜色按算不算拿不到：算的橙色，不算的（避免不了、之后预计没有三选一）灰色
            self.assertEqual((loss.lost, loss.warn), (lost, bool(lost)))
            self.assertEqual(loss.final, adv.picks.total <= 0)
            if not lost or i in last:  # 不选、被锁的本来就在最后，不另写能保住的
                self.assertEqual((loss.behind, loss.keep_last, loss.alone), ((), (), False))
            else:
                # 能保住的（拿不到的情况都比它好）：排在它前面的几张（behind）；前面没有时是排在最后的不选、被锁的
                # （keep_last）；一张都没有时 alone（这次的候选选哪张都会少拿）
                better = [k for k in active if LOST_GROUP[r.losses[k].lost if r.losses[k] else ""] < LOST_GROUP[lost]]
                before = [k for k in better if active.index(k) < active.index(i)]
                self.assertEqual(list(loss.behind), before)
                self.assertEqual(list(loss.keep_last), [] if before else [k for k in better if k in last])
                self.assertEqual(loss.alone, not better)
                self.assertTrue(set(better) <= set(before) | set(last))
            if loss.kind == LOSS_NOW:
                self.assertTrue(all(db.chips[c].school not in loss.enter for c, _ in loss.chips))
                self.assertEqual(len(loss.enter), db.rules["pool"]["max_schools"])
            else:
                # 只能再进入一个：候选属于列进方案的流派时（这时方案列了三个以上流派）为 LOSS_TIP；候选是方案外流派的
                # 筹码时为 LOSS_ONE_LEFT，一定算拿不到（最好的两个流派只剩一个能进）
                self.assertEqual(len(loss.enter), 1)
                inside = db.chips[e.chip.name].school in r.listed
                self.assertEqual(loss.kind, LOSS_TIP if inside else LOSS_ONE_LEFT)
                if inside:
                    self.assertGreaterEqual(len(r.listed), 3)
                elif adv.picks.total > 0:
                    self.assertTrue(loss.warn)
        # 附注在最前面，橙色的也在 warn_notes 里；「已进入方案外的流派」一律橙色
        texts = [n.text for n in r.notes]
        self.assertEqual(adv.notes[:len(texts)], texts)
        self.assertEqual(adv.warn_notes, [n.text for n in r.notes if n.warn])
        self.assertTrue(all(n.warn for n in r.notes if n.kind == NOTE_STRAYED))


class CopyDefaultTest(Base):
    """「复制为我的方案」（advisor.schemes.scheme_from_default）按原样复制，顺位按默认推荐列表的顺序。
    默认推荐的四档用测试用的那一份（tests/support/player_layer.toml）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.db = with_player_layer(cls.db)

    def test_mapping(self):
        from advisor.schemes import default_tiers, scheme_from_default, target_schools
        rin = default_tiers(self.db, "凛")
        self.assertEqual(rin, dict(self.db.player_synergy["凛"].tiers))  # 凛的规则只管星光的另外 6 个，四档全部换算
        teru = default_tiers(self.db, "照")
        ruled = {c for r in self.db.player_synergy["照"].rules for c in r.chips}
        self.assertTrue(ruled)
        self.assertFalse(ruled & set(teru))
        self.assertTrue(teru)
        self.assertEqual(default_tiers(self.db, "阿尔"), {})  # 没有玩家思路
        copy = scheme_from_default(self.db, "凛")
        p = plan_of(self.db, copy)
        self.assertEqual(p.tiers, tuple(copy.tiers.items()))  # 按方案的先后
        self.assertEqual(p.marks(), rin)
        adv = advise(self.db, State(hero="凛", rerolls=2), ["标靶", "标记喷罐", "鹰眼瞄具"], scheme=p)
        self.assertEqual(adv.scheme_schools, target_schools(self.db, copy.tiers))

    def test_copy_keeps_baseline_picks(self):
        # 复制出来的方案仍保持凛的两组基准首推（推荐的标记喷罐、必选的大背包）
        from advisor.schemes import scheme_from_default
        copy = scheme_from_default(self.db, "凛")
        for offer, best in ((["标靶", "标记喷罐", "鹰眼瞄具"], "标记喷罐"), (["标靶", "大背包", "缓冲盾牌"], "大背包")):
            for rerolls in (2, 3):
                with self.subTest(offer=offer, rerolls=rerolls):
                    self.assertEqual(advise(self.db, State(hero="凛", rerolls=rerolls), offer, scheme=copy).best, best)


class PlanTest(Base):
    def test_plan_normalized(self):
        # 按档位重排、同一档保持传入的先后，同一个筹码只留第一次，档位不认识的不要
        p = Plan(name="x", tiers=(("甲", "first"), ("乙", "core"), ("丙", "first"), ("甲", "core"), ("丁", "maybe")))
        self.assertEqual(p.tiers, (("乙", "core"), ("甲", "first"), ("丙", "first")))
        self.assertEqual(p.ranks(), {"乙": 1, "甲": 1, "丙": 2})
        self.assertEqual(p.marks(), {"乙": "core", "甲": "first", "丙": "first"})

    def test_plan_of_scheme(self):
        from advisor.schemes import Scheme, StaleChip
        scheme = Scheme(hero="芬妮", name="治愈坦克", tiers={"大碗炖肉": "first", "缓冲盾牌": "core", "可口糖果": "first"},
                        note="说明", author="作者", id="s1", stale=(StaleChip("旧筹码", "core", 999, True),))
        p = plan_of(self.db, scheme)
        self.assertEqual(p, Plan(name="治愈坦克", hero="芬妮", tiers=(("缓冲盾牌", "core"), ("大碗炖肉", "first"),
                                                                      ("可口糖果", "first")), ident="s1"))
        self.assertIsNone(plan_of(self.db, None))
        self.assertIs(plan_of(self.db, p), p)
        # 说明、作者不参与（改了不重算）；名称、档位、顺位参与
        self.assertEqual(plan_of(self.db, dataclasses.replace(scheme, note="另一段说明", author="另一人")), p)
        self.assertNotEqual(plan_of(self.db, dataclasses.replace(scheme, name="治愈坦克2")), p)
        moved = dataclasses.replace(scheme, tiers=scheme.tiers.moved("可口糖果", "first", 1))
        self.assertNotEqual(plan_of(self.db, moved), p)
        self.assertEqual(plan_of(self.db, moved).ranks()["可口糖果"], 1)
        # 资料库里没有的筹码不参与计算
        odd = SimpleNamespace(hero="芬妮", name="x", tiers={"不存在的筹码": "core", "标靶": "first"}, id="")
        self.assertEqual(plan_of(self.db, odd).tiers, (("标靶", "first"),))
        # advise 也收方案存储的 Scheme
        st = self.fenny()
        self.assertEqual(advise(self.db, st, ["缓冲盾牌", "紫色飞星", "大背包"], scheme=scheme).scheme, p)
        record = p.record(("治愈",))
        self.assertEqual(record, {"id": "s1", "name": "治愈坦克", "hero": "芬妮",
                                  "tiers": {"缓冲盾牌": "必选", "大碗炖肉": "推荐", "可口糖果": "推荐"}, "schools": ["治愈"]})
        self.assertEqual(list(record["tiers"]), ["缓冲盾牌", "大碗炖肉", "可口糖果"])
        json.dumps(record, ensure_ascii=False)

    def test_scheme_schools(self):
        # 目标流派由档位推出来（列进方案的流派里有必选、推荐的，可以超过两个）：Advice.scheme_schools；默认推荐时为空
        st, offer = self.fenny(), ["缓冲盾牌", "紫色飞星", "大背包"]
        self.assertEqual(advise(self.db, st, offer).scheme_schools, ())
        self.assertEqual(advise(self.db, st, offer, scheme=plan(*THREE)).scheme_schools, ("治愈", "标记", "充能"))
        self.assertEqual(advise(self.db, st, offer, scheme=plan(("标靶", "second"), ("缓冲盾牌", "avoid"))).scheme_schools,
                         ())


def frame(names, rerolls=2, probabilities=(60, 37, 3), selected=None, problems=()):
    return SimpleNamespace(names=list(names), raw=list(names), rerolls=rerolls, probabilities=probabilities,
                           selected=selected, problems=list(problems))


class SessionSchemeTest(Base):
    """Session 按当前角色正在用的方案算；切换方案、编辑正在用的方案、改了已有筹码以后按最近一次三选一的画面立即重算。"""

    OFFER = ["医疗箱-完备治疗", "紫色飞星", "大背包"]

    def setUp(self):
        from advisor.schemes import Scheme
        self.Scheme = Scheme
        self.heal = Scheme(hero="芬妮", name="治愈坦克",
                           tiers={"医疗箱-完备治疗": "core", "缓冲盾牌": "core", "紫色飞星": "avoid"})
        self.using: dict = {}
        self.s = Session(self.db, Settings(hero="芬妮"))
        self.s.game.seen_hero = "芬妮"
        self.s.scheme_for = self.using.get

    def test_default_without_store(self):
        s = Session(self.db, Settings(hero="芬妮"))
        s.game.seen_hero = "芬妮"
        ev = s.on_frame(frame(self.OFFER))
        self.assertIsNone(ev.advice.scheme)
        self.assertIsNone(ev.advice.ranking)
        self.assertIsNone(ev.advice.default_best)
        self.assertEqual(ev.advice.best, "紫色飞星")
        self.assertIsNone(s.snapshot()["scheme"])
        self.assertNotIn("scheme", _event_record(ev))
        self.assertNotIn("places", _event_record(ev))

    def test_switch_and_edit_recompute_same_frame(self):
        s = self.s
        ev = s.on_frame(frame(self.OFFER))
        self.assertEqual((ev.kind, ev.advice.best, ev.advice.scheme), ("advice", "紫色飞星", None))
        self.assertIsNone(s.recompute())  # 方案没变：不重复给推荐
        # 切换到治愈坦克：不再送新的画面（手动模式），立即按同一个画面重算
        self.using["芬妮"] = self.heal
        ev = s.recompute()
        self.assertEqual(ev.kind, "advice")
        self.assertEqual(ev.advice.scheme.name, "治愈坦克")
        self.assertEqual(ev.advice.best, "医疗箱-完备治疗")
        self.assertEqual(ev.advice.default_best, "紫色飞星")
        self.assertEqual(ev.state.owned, ())
        self.assertIsNone(s.recompute())
        self.assertIsNone(s.on_frame(frame(self.OFFER)))  # 同一画面、同一方案：不重复
        # 编辑正在用的方案（改档位）：立即重算
        self.using["芬妮"] = dataclasses.replace(self.heal, tiers={"医疗箱-完备治疗": "avoid", "大背包": "core"})
        ev = s.recompute()
        self.assertEqual(ev.advice.best, "大背包")
        # 只改顺位也重算：两张必选换了先后，首推跟着换
        self.using["芬妮"] = dataclasses.replace(self.heal, tiers={"大背包": "core", "医疗箱-完备治疗": "core"})
        self.assertEqual(s.recompute().advice.best, "大背包")
        self.using["芬妮"] = dataclasses.replace(self.heal, tiers={"医疗箱-完备治疗": "core", "大背包": "core"})
        self.assertEqual(s.recompute().advice.best, "医疗箱-完备治疗")
        # 只改说明、作者：不重算
        self.using["芬妮"] = dataclasses.replace(self.using["芬妮"], note="新的说明", author="某人")
        self.assertIsNone(s.recompute())
        # 改回默认推荐
        self.using.pop("芬妮")
        ev = s.recompute()
        self.assertEqual((ev.advice.best, ev.advice.scheme, ev.advice.default_best), ("紫色飞星", None, None))

    def test_next_frame_follows_scheme(self):
        # 自动模式不调 recompute 时，下一帧也按新方案重算（方案内容参与「是不是同一个三选一」的判断）
        s = self.s
        s.on_frame(frame(self.OFFER))
        self.using["芬妮"] = self.heal
        ev = s.on_frame(frame(self.OFFER))
        self.assertEqual((ev.kind, ev.advice.best), ("advice", "医疗箱-完备治疗"))

    def test_switch_scheme_midgame(self):
        # 中途换方案：已进入的治愈在新方案里是方案外的流派（被锁的流派已进入），附注写走偏
        s = self.s
        s.settings.owned = ["缓冲盾牌"]
        self.using["芬妮"] = self.Scheme(hero="芬妮", name="治愈标记", tiers=dict(TWO))
        ev = s.on_frame(frame(["标靶", "紫色飞星", "书签"]))
        self.assertEqual(ev.advice.ranking.strayed, ())
        self.assertEqual(ev.advice.best, "标靶")
        self.using["芬妮"] = self.Scheme(hero="芬妮", name="星光充能", tiers={"紫色飞星": "core", "永动机": "first"})
        ev = s.recompute()
        self.assertEqual((ev.advice.scheme.name, ev.advice.best), ("星光充能", "紫色飞星"))
        self.assertEqual(ev.advice.ranking.strayed, ("治愈",))
        self.assertEqual(ev.advice.warn_notes[0], "已进入方案外的流派「治愈」")

    def test_misread_fixed(self):
        # 识别认错（已有筹码里多了星光的紫色飞星）：按走偏算；在「已有筹码」里改掉以后立即恢复原样
        s = self.s
        self.using["芬妮"] = self.Scheme(hero="芬妮", name="治愈标记", tiers=dict(TWO))
        s.settings.owned = ["紫色飞星"]
        ev = s.on_frame(frame(["金色飞星", "大碗炖肉", "书签"]))
        self.assertEqual(ev.advice.warn_notes, ["已进入方案外的流派「星光」"])
        self.assertEqual(self.place_of(ev.advice, "金色飞星").kind, PLACE_STRAYED)
        s.set_owned(["缓冲盾牌"])
        ev = s.recompute()
        self.assertEqual(ev.advice.warn_notes, [])
        self.assertEqual(self.place_of(ev.advice, "金色飞星").kind, PLACE_LOCKED)
        self.assertEqual(_event_record(ev)["places"], ["其他流派", "可选 2", "未标记"])

    def test_no_recompute_when_closed(self):
        s = self.s
        s.on_frame(frame(self.OFFER))
        s.on_frame(None)
        s.on_frame(None)  # 界面关闭
        self.using["芬妮"] = self.heal
        self.assertIsNone(s.recompute())
        self.assertIsNone(Session(self.db, Settings(hero="芬妮")).recompute())  # 还没有三选一

    def test_no_recompute_while_holding(self):
        s = self.s
        s.on_frame(frame(self.OFFER))
        s.select._hold = {"since": s._clock(), "reads": 0, "refresh": True, "expired": False, "shown": s.select._shown}
        self.using["芬妮"] = self.heal
        self.assertIsNone(s.recompute())

    def test_scheme_follows_hero(self):
        s = self.s
        self.using["芬妮"] = self.heal
        s.on_frame(frame(self.OFFER))
        self.assertEqual(s.scheme_plan().name, "治愈坦克")
        # 换成凛：芬妮的方案不影响凛的推荐
        s.set_hero("凛")
        self.assertIsNone(s.scheme_plan())
        ev = s.recompute()
        self.assertEqual(ev.advice.state.hero, "凛")
        self.assertIsNone(ev.advice.scheme)
        plain = advise(self.db, ev.advice.state, list(self.OFFER))
        self.assertEqual([e.total for e in ev.advice.evals], [e.total for e in plain.evals])
        # 存储给的方案不是当前角色的（不该出现）：按默认推荐
        self.using["凛"] = self.heal
        self.assertIsNone(s.scheme_plan())
        # 资料库未收录的角色：按默认推荐
        s.settings.hero = "不存在的角色"
        self.using["不存在的角色"] = self.Scheme(hero="不存在的角色", name="x")
        self.assertIsNone(s.scheme_plan())

    def test_with_scheme_store(self):
        # 接上方案存储（advisor.schemes.SchemeStore）：切换、编辑、删除正在用的方案后立即重算
        from advisor.schemes import SchemeStore
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as d:
            store = SchemeStore(Path(d) / "schemes.json")
            s = Session(self.db, Settings(hero="芬妮"))
            s.game.seen_hero = "芬妮"
            s.scheme_for = store.current
            self.assertIsNone(s.on_frame(frame(self.OFFER)).advice.scheme)
            added = store.add(self.heal)
            self.assertIsNone(s.recompute())  # 新建了方案，但还没切换过去
            store.set_current("芬妮", added.id)
            ev = s.recompute()
            self.assertEqual((ev.advice.best, ev.advice.scheme.ident), ("医疗箱-完备治疗", added.id))
            store.update(dataclasses.replace(added, tiers={"医疗箱-完备治疗": "avoid", "大背包": "core"}))
            self.assertEqual(s.recompute().advice.best, "大背包")
            store.delete(added.id)  # 删的正是在用的那套：该角色改回默认推荐
            ev = s.recompute()
            self.assertEqual((ev.advice.best, ev.advice.scheme), ("紫色飞星", None))

    def test_records(self):
        s = self.s
        s.on_frame(frame(self.OFFER))
        self.using["芬妮"] = self.heal
        ev = s.recompute()
        record = _event_record(ev)
        self.assertEqual((record["scheme"], record["default_best"]), ("治愈坦克", "紫色飞星"))
        self.assertEqual(record["places"], ["必选 1", "不选 1", "未标记"])
        # 按方案计算的不记推荐度（按卡片的先后重新分的，和各张的总分对不上）：记各张的总分（刷新记扣掉代价后的期望）
        # 和卡片上的先后；默认推荐照旧记推荐度
        self.assertNotIn("options", record)
        adv = ev.advice
        self.assertEqual(record["totals"], [(e.chip.name, round(e.total, 4)) for e in adv.evals]
                         + [("刷新", round(adv.reroll.value, 4))])
        names = [e.chip.name for e in adv.evals]
        self.assertEqual(record["order"], ["刷新" if k < 0 else names[k] for k in adv.ranking.order])
        self.assertEqual(record["order"][0], "医疗箱-完备治疗")
        plain = Session(self.db, Settings(hero="芬妮"))
        plain.game.seen_hero = "芬妮"
        default = _event_record(plain.on_frame(frame(self.OFFER)))
        self.assertTrue({"options"} <= set(default) and not {"totals", "order", "places"} & set(default))
        changes = [r for r in s.recent_events(60) if r["kind"] == "scheme"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["hero"], "芬妮")
        self.assertEqual(changes[0]["scheme"]["name"], "治愈坦克")
        self.assertEqual(changes[0]["scheme"]["tiers"]["紫色飞星"], "不选")
        self.assertEqual(changes[0]["scheme"]["schools"], ["治愈"])
        snap = s.snapshot()
        json.dumps(snap, ensure_ascii=False)
        self.assertEqual(snap["scheme"]["name"], "治愈坦克")
        # 改回默认推荐也记一条
        self.using.pop("芬妮")
        s.recompute()
        changes = [r for r in s.recent_events(60) if r["kind"] == "scheme"]
        self.assertEqual((len(changes), changes[-1]["scheme"]), (2, None))


class LogbookTest(Base):
    """推荐日志记下当时用的方案和默认推荐的首推；校准提醒的局数不计入只有按方案推荐的局；旧日志照常能读。"""

    def setUp(self):
        from advisor import logbook, paths
        self.logbook = logbook
        self._dir = tempfile.TemporaryDirectory(dir=sandbox.work_dir())
        self._patch = mock.patch.object(paths, "logs_dir", return_value=Path(self._dir.name))
        self._patch.start()
        logbook._reset_for_tests()

    def tearDown(self):
        self.logbook._reset_for_tests()
        self._patch.stop()
        self._dir.cleanup()

    def offers(self):
        return [r for r in self.logbook.iter_records() if r["t"] == "offer"]

    def test_records(self):
        lb = self.logbook
        st = self.fenny()
        offer = ["医疗箱-完备治疗", "紫色飞星", "大背包"]
        default = advise(self.db, st, offer)
        lb.record_offer(st, default, "a")
        ruled = advise(self.db, st, offer, scheme=plan(("医疗箱-完备治疗", "core")))
        ruled.default_best = default.best
        lb.record_offer(st, ruled, "b")
        a, b = self.offers()
        # 按默认推荐的记录：没有方案的几项
        self.assertNotIn("scheme", a)
        self.assertNotIn("default_best", a)
        for key in ("scheme", "scheme_schools", "default_best", "warn_notes"):
            self.assertNotIn(key, a["advice"])
            self.assertNotIn(key, b["advice"])
        self.assertEqual(a["advice"]["best"], "紫色飞星")
        self.assertEqual(b["scheme"], {"id": "", "name": NAME, "hero": "芬妮", "tiers": {"医疗箱-完备治疗": "必选"},
                                       "schools": ["治愈"]})
        self.assertEqual((b["default_best"], b["advice"]["best"]), ("紫色飞星", "医疗箱-完备治疗"))
        self.assertEqual([lb.by_scheme(r) for r in (a, b)], [False, True])
        self.assertFalse(lb.by_scheme({"t": "offer", "advice": {"best": "x"}}))  # 旧记录
        # 字典形式的推荐全文也照常记
        lb.record_offer(st, {"best": "大背包", "evals": []}, "c")
        self.assertEqual(self.offers()[-1]["advice"], {"best": "大背包", "evals": []})

    def write_mixed_records(self) -> None:
        """两局推荐日志：第一局按默认推荐的 a、按方案的 b（带一条「不认同」）、先默认后换成方案的 c；第二局只有按方案的 d。"""
        lb = self.logbook
        st = self.fenny()
        offer = ["医疗箱-完备治疗", "紫色飞星", "大背包"]
        default = advise(self.db, st, offer)
        ruled = advise(self.db, st, offer, scheme=plan(("医疗箱-完备治疗", "core")))
        ruled.default_best = default.best
        lb.new_game("g1")
        lb.record_offer(st, default, "a")
        lb.record_pick("a", "医疗箱-完备治疗")
        lb.record_offer(st, ruled, "b")
        lb.record_pick("b", "医疗箱-完备治疗")
        lb.record_feedback("b", "推荐有误", "方案")
        # 同一个三选一中途换成方案：以最后一条为准，不计入
        lb.record_offer(st, default, "c")
        lb.record_offer(st, ruled, "c")
        lb.record_pick("c", "医疗箱-完备治疗")
        lb.new_game("g2")
        lb.record_offer(st, ruled, "d")  # 这一局只有按方案推荐的

    def test_calibration_skips_scheme_records(self):
        """校准用的是默认推荐的记录：校准提醒的局数只算有默认推荐记录的局，只有按筹码方案推荐的那一局不算。"""
        self.write_mixed_records()
        self.assertEqual(self.logbook.game_count_since_calibration(), 1)


class CliTest(Base):
    """命令行的推荐全文（排查用）：按方案计算时写位次和总分、不写推荐度，另写刷新按方案的比较、档位前瞻和拿不到的提醒；
    默认推荐不变。"""

    def test_render_with_scheme(self):
        # 已进入星光、只剩一个名额：标靶（可选 1）让必选缓冲盾牌拿不到，排到能保住的大碗炖肉、金色飞星后面（橙色）
        adv = advise(self.db, self.fenny(owned=("紫色飞星",)), ["标靶", "大碗炖肉", "金色飞星"], scheme=plan(*TWO))
        text = cli.render(self.db, adv)
        self.assertIn(f"筹码方案：{NAME}", text)
        self.assertIn("\n可选 1  标靶（蓝·标记流派）", text)
        self.assertIn("\n未标记  金色飞星（金·星光流派）", text)
        # 几张能保住的用「或」连，同卡片的悬停（另选其中哪一张都能保住）
        self.assertIn("· 选择后无法获得：必选「缓冲盾牌」（橙色；另选「大碗炖肉」或「金色飞星」能保住必选，排在它们后面）", text)
        self.assertIn("· 选择后无法获得：推荐「鹰眼瞄具」（灰色）", text)
        self.assertIn("按筹码方案：和「大碗炖肉」（可选 2，2 级）比", text)
        self.assertIn("· 档位前瞻 ", text)
        self.assertIn("总分", text)
        self.assertNotIn("%  可选", text)  # 不写推荐度
        self.assertIn("⚠ 已进入方案外的流派「星光」", text)
        # 选项的先后照推荐卡片
        at = [text.index(s) for s in ("可选 2  大碗炖肉", "未标记  金色飞星", "可选 1  标靶")]
        self.assertEqual(at, sorted(at))
        data = cli.to_json(adv)
        self.assertEqual(data["scheme"], NAME)
        self.assertEqual([c["place"] for c in data["chips"]], ["可选 1", "可选 2", "未标记"])
        self.assertEqual(data["order"][0], "大碗炖肉")
        self.assertEqual(data["chips"][0]["loss"]["chips"], [["缓冲盾牌", "必选"]])
        self.assertEqual((data["chips"][0]["loss"]["lost"], data["chips"][0]["loss"]["behind"]),
                         (LOST_CORE, ["大碗炖肉", "金色飞星"]))
        self.assertFalse(any("percent" in c for c in data["chips"]))
        self.assertNotIn("percent", data["reroll"])
        self.assertIn("scheme", data["reroll"])
        json.dumps(data, ensure_ascii=False)

    def test_default_render_unchanged(self):
        adv = advise(self.db, self.fenny(), ["标靶", "大碗炖肉", "金色飞星"])
        text = cli.render(self.db, adv)
        self.assertNotIn("筹码方案：", text)
        self.assertNotIn("位次", text)
        data = cli.to_json(adv)
        self.assertFalse({"scheme", "order", "scheme_notes"} & set(data))
        self.assertNotIn("place", data["chips"][0])

    def test_scheme_option(self):
        # --scheme 收方案的分享文字（短字符串同样认）；角色和 --hero 不同时报错
        from advisor.schemes import Scheme, to_short, to_text
        scheme = Scheme(hero="芬妮", name="治愈坦克", tiers=dict(TWO))
        args = ["--hero", "芬妮", "--offer", "标靶", "大碗炖肉", "金色飞星", "--star", "1", "--round", "4",
                "--map", "异变图书馆", "--owned", "紫色飞星"]
        for share in (to_text(scheme), to_short(scheme)):
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main([*args, "--scheme", share])
            with self.subTest(share=share):
                self.assertIn("可选 1  标靶", out.getvalue())
        with self.assertRaises(SystemExit):
            cli.main(["--hero", "凛", "--offer", "标靶", "大碗炖肉", "金色飞星", "--scheme", to_text(scheme)])
        with self.assertRaises(SystemExit):
            cli.main([*args, "--scheme", "随便写的文字"])


if __name__ == "__main__":
    unittest.main()
