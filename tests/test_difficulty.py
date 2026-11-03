"""本局难度：难度档位和难度名（advisor.data）、按地图和进度上限推算难度（data.infer_difficulty），
以及噩梦及以上时摩托头盔-高级的「难度」分项（engine.parts._survival_parts）。

噩梦及以上难度，摩托头盔-高级对所有角色提高
（「需要生存」特性分值 × 1.5，带该特性的角色另加 0.5 倍，合计同样是 1.5 倍）；困难难度和读不到难度时维持现状；
摩托头盔-中级、-一般不变。园林中庭的进度上限已按实测改正，四个难度都按资料推算（rules.toml [difficulty] cap_untrusted
的这一项还在，随程序发布的资料里没有地图用它）。
"""
import dataclasses
import io
import json
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from advisor import cli
from advisor.data import (
    DIFFICULTIES,
    DIFFICULTY_NAMES,
    LEVEL_HARD,
    LEVEL_HARD_PLUS,
    LEVEL_LABEL,
    LEVEL_NIGHTMARE,
    LEVEL_NORMAL,
    NORMAL,
    classify_difficulties,
    infer_difficulty,
    level_of_name,
    load_db,
)
from advisor.engine import (
    NIGHTMARE_REASON, NORMAL_NOTE, SHORT_LIMIT, UNKNOWN_DIFFICULTY_LEAD, UNKNOWN_DIFFICULTY_SUBJECT, Scorer, advise,
)
from advisor.engine.parts import nightmare_plus
from advisor.pool import State, Teammate
from tests.test_engine import BANNED_WORDING

ROOT = Path(__file__).resolve().parents[1]
HELMETS = ("摩托头盔-高级", "摩托头盔-中级", "摩托头盔-一般")
# 维持困难及以上算法的档位：读不到难度、确定是困难、分不出困难还是噩梦
NOT_NIGHTMARE = (None, LEVEL_HARD, LEVEL_HARD_PLUS)


def as_known_difficulty(reason: str) -> str:
    """读不到难度时「难度」的整句换回知道难度（困难、困难及以上）时的写法：去掉开头的「难度待识别，按困难及以上计算：」，
    后半句的主语「这几档难度」换回「困难及以上难度」。不是读不到难度的整句原样返回。"""
    rest = reason.removeprefix(UNKNOWN_DIFFICULTY_LEAD)
    if rest == reason:
        return reason
    assert rest.startswith(UNKNOWN_DIFFICULTY_SUBJECT), reason
    return "困难及以上难度" + rest.removeprefix(UNKNOWN_DIFFICULTY_SUBJECT)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()


# ------------------------------------------------------------ 难度名和档位


class LevelTest(Base):
    def test_names(self):
        # DIFFICULTIES 仍是地图资料里有进度上限的四个难度（test_data 断言 progress_caps 的键与它相同）；全部难度名另列
        self.assertEqual(DIFFICULTIES, ("普通", "困难", "噩梦", "疯狂"))
        self.assertEqual(DIFFICULTY_NAMES, ("普通", "困难", "噩梦", "疯狂", "极限"))
        self.assertEqual(LEVEL_NORMAL, NORMAL)
        self.assertEqual(len({LEVEL_NORMAL, LEVEL_HARD, LEVEL_NIGHTMARE, LEVEL_HARD_PLUS}), 4)
        self.assertEqual(LEVEL_LABEL, {LEVEL_NORMAL: "普通", LEVEL_HARD: "困难", LEVEL_NIGHTMARE: "噩梦及以上",
                                       LEVEL_HARD_PLUS: "困难及以上"})

    def test_level_of_name(self):
        expected = {"普通": LEVEL_NORMAL, "困难": LEVEL_HARD, "噩梦": LEVEL_NIGHTMARE, "疯狂": LEVEL_NIGHTMARE,
                    "极限": LEVEL_NIGHTMARE}
        self.assertEqual({n: level_of_name(n) for n in DIFFICULTY_NAMES}, expected)
        for name in (None, "", "困难及以上", "噩梦及以上", "hard"):
            self.assertIsNone(level_of_name(name))

    def test_classify(self):
        cases = [
            ({"普通"}, (LEVEL_NORMAL, "普通")),
            ({"困难"}, (LEVEL_HARD, "困难")),
            ({"噩梦"}, (LEVEL_NIGHTMARE, "噩梦")),
            ({"极限"}, (LEVEL_NIGHTMARE, "极限")),
            ({"噩梦", "疯狂"}, (LEVEL_NIGHTMARE, None)),
            ({"噩梦", "疯狂", "极限"}, (LEVEL_NIGHTMARE, None)),
            ({"困难", "噩梦"}, (LEVEL_HARD_PLUS, None)),
            ({"困难", "噩梦", "疯狂"}, (LEVEL_HARD_PLUS, None)),
            ({"普通", "困难"}, (None, None)),
            ({"普通", "噩梦"}, (None, None)),
            (set(), (None, None)),
            ({"困难", "不存在的难度"}, (None, None)),
        ]
        for names, expected in cases:
            with self.subTest(names=names):
                self.assertEqual(classify_difficulties(names), expected)
                self.assertEqual(classify_difficulties(tuple(sorted(names))), expected)  # 也接受元组


class DifficultiesAtTest(Base):
    def test_columns(self):
        info = self.db.map_info
        self.assertEqual(info["异变图书馆"].difficulties_at(17), ("噩梦", "疯狂"))
        self.assertEqual(info["异变图书馆"].difficulties_at(18), ("普通", "困难"))
        self.assertEqual(info["异变图书馆"].difficulties_at(19), ())
        self.assertEqual(info["御魂庆典"].difficulties_at(20), ("普通", "困难", "噩梦"))
        self.assertEqual(info["水乡古镇"].difficulties_at(17), ("噩梦",))
        self.assertEqual(info["幽魂暗巷"].difficulties_at(18), ("普通", "困难"))
        self.assertEqual(info["幽魂暗巷"].difficulties_at(17), ("噩梦",))
        self.assertEqual(info["幽魂暗巷"].difficulties_at(16), ("疯狂",))

    def test_extreme_column(self):
        """极限（只有几张地图有，MapInfo.extreme_cap）：四个难度都对不上、只有极限对得上时是极限（御魂庆典 17 格）；和四个
        难度里的某一个相同时分不出来，照旧只列那几个难度（星趴·梦想号 18、水乡古镇 16 都和疯狂相同）。"""
        info = self.db.map_info
        self.assertEqual(info["御魂庆典"].difficulties_at(17), ("极限",))
        self.assertEqual(info["星趴·梦想号"].difficulties_at(18), ("疯狂",))
        self.assertEqual(info["水乡古镇"].difficulties_at(16), ("疯狂",))
        self.assertEqual(info["御魂庆典"].difficulties_at(16), ())
        self.assertEqual(info["异变图书馆"].difficulties_at(19), ())  # 没有极限难度的地图照旧
        # 四个难度的资料不全时照旧推算不出（不看极限）
        caps = {**info["御魂庆典"].progress_caps, "困难": None}
        self.assertIsNone(dataclasses.replace(info["御魂庆典"], progress_caps=caps).difficulties_at(17))


# ------------------------------------------------------------ 按进度上限推算


class InferTest(Base):
    def infer(self, map_name, cap, hard=False):
        return infer_difficulty(self.db, map_name, cap, hard)

    def test_every_map_and_difficulty(self):
        # 资料完整、可信的每张地图、每个难度 d：红叉在 d 那一列的格上，概率行按 d 取（普通 / 困难及以上），
        # 推算结果不得和 d 矛盾：困难不得推成噩梦及以上，噩梦、疯狂不得推成困难；推出具体难度名时就是 d
        untrusted = (self.db.rules.get("difficulty") or {}).get("cap_untrusted", ())
        checked = set()
        for map_name, info in self.db.map_info.items():
            if map_name in untrusted or info.difficulties_at(0) is None:
                continue
            for d in DIFFICULTIES:
                cap = info.progress_caps[d]
                level, name, note, cands = self.infer(map_name, cap, hard=d != "普通")
                with self.subTest(map=map_name, difficulty=d, cap=cap):
                    checked.add(map_name)
                    self.assertTrue(note)
                    self.assertFalse(note.endswith("。"))
                    if level is None:
                        self.assertEqual(d, "普通")  # 只有普通和其他难度的进度上限相同时推算不出
                        continue
                    self.assertIn(d, cands)
                    self.assertIn(name, (None, d))
                    if d == "普通":
                        self.assertEqual(level, LEVEL_NORMAL)
                    elif d == "困难":
                        self.assertIn(level, (LEVEL_HARD, LEVEL_HARD_PLUS))
                    else:
                        self.assertIn(level, (LEVEL_NIGHTMARE, LEVEL_HARD_PLUS))
        self.assertGreaterEqual(len(checked), 8, checked)

    def test_known_maps(self):
        cases = [
            # （地图，进度上限，概率行为困难及以上）→（档位，难度名）
            (("御魂庆典", 20, True), (LEVEL_HARD_PLUS, None)),  # 困难、噩梦都是 20 格
            (("御魂庆典", 20, False), (None, None)),  # 普通也是 20 格
            (("御魂庆典", 19, False), (LEVEL_NIGHTMARE, "疯狂")),
            (("御魂庆典", 19, True), (LEVEL_NIGHTMARE, "疯狂")),
            (("御魂庆典", 17, True), (LEVEL_NIGHTMARE, "极限")),  # 只有极限是 17 格
            (("御魂庆典", 17, False), (LEVEL_NIGHTMARE, "极限")),
            (("异变图书馆", 17, True), (LEVEL_NIGHTMARE, None)),  # 噩梦、疯狂都是 17 格（10-03 那局为噩梦）
            (("异变图书馆", 17, False), (LEVEL_NIGHTMARE, None)),
            (("异变图书馆", 18, True), (LEVEL_HARD, "困难")),  # 概率行排除了普通
            (("异变图书馆", 18, False), (None, None)),
            (("水乡古镇", 17, True), (LEVEL_NIGHTMARE, "噩梦")),
            (("水乡古镇", 16, True), (LEVEL_NIGHTMARE, "疯狂")),
            (("水乡古镇", 18, False), (LEVEL_HARD, "困难")),
            (("水乡古镇", 20, False), (LEVEL_NORMAL, "普通")),
            (("星趴·梦想号", 20, True), (LEVEL_HARD_PLUS, None)),  # 普通、困难、噩梦都是 20 格
            (("星趴·梦想号", 18, True), (LEVEL_NIGHTMARE, "疯狂")),
            (("魔法学院", 16, True), (LEVEL_NIGHTMARE, None)),
            (("龙宫游乐园", 16, True), (LEVEL_NIGHTMARE, None)),
            (("海选赛运动场", 15, True), (LEVEL_NIGHTMARE, None)),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(self.infer(*args)[:2], expected)

    def test_notes(self):
        self.assertEqual(self.infer("异变图书馆", 17, True)[2:],
                         ("依据地图「异变图书馆」的进度上限推算：进度条红叉在第 17 格，噩梦、疯狂难度均为 17 格", ("噩梦", "疯狂")))
        self.assertEqual(self.infer("水乡古镇", 17)[2],
                         "依据地图「水乡古镇」的进度上限推算：进度条红叉在第 17 格，噩梦难度为 17 格")
        self.assertEqual(self.infer("异变图书馆", 18, True)[2],
                         "依据地图「异变图书馆」的进度上限推算：进度条红叉在第 18 格，困难难度为 18 格；筹码概率为困难及以上，不是普通难度")
        self.assertEqual(self.infer("御魂庆典", 20, True)[2:], ("地图「御魂庆典」困难与噩梦的进度上限相同", ("困难", "噩梦")))
        self.assertEqual(self.infer("御魂庆典", 20)[2], "地图「御魂庆典」普通、困难与噩梦的进度上限相同")
        self.assertEqual(self.infer("御魂庆典", 17, True)[2:],
                         ("依据地图「御魂庆典」的进度上限推算：进度条红叉在第 17 格，极限难度为 17 格", ("极限",)))

    def test_cannot_infer(self):
        self.assertEqual(self.infer(None, 17, True), (None, None, "地图待识别", ()))
        self.assertEqual(self.infer("不存在的地图", 17, True)[:2], (None, None))
        for cap in (None, 0):
            self.assertEqual(self.infer("异变图书馆", cap, True), (None, None, "尚未读到进度上限", ()))
        # 资料里没有这个进度上限；概率行为困难及以上，而这个上限只有普通难度是
        self.assertEqual(self.infer("星趴·梦想号", 19, True)[:2], (None, None))
        self.assertEqual(self.infer("水乡古镇", 20, True),
                         (None, None, "进度条红叉在第 20 格，与地图「水乡古镇」困难及以上各难度的进度上限资料均不符", ()))
        # 四个难度的进度上限有缺：缺的那一列也可能是这个值，推算不出
        info = self.db.map_info["幽魂暗巷"]
        caps = {**info.progress_caps, "困难": None, "噩梦": None}
        db = dataclasses.replace(self.db, map_info={**self.db.map_info, "幽魂暗巷": dataclasses.replace(info, progress_caps=caps)})
        for cap in (16, 17, 18, None):
            with self.subTest(cap=cap):
                self.assertEqual(infer_difficulty(db, "幽魂暗巷", cap, True),
                                 (None, None, "地图「幽魂暗巷」缺少困难、噩梦的进度上限资料", ()))

    def test_cap_untrusted(self):
        # 进度上限资料与实测不符的地图（rules.toml [difficulty] cap_untrusted）不按进度上限推算，任何值都按读不到处理。
        # 随程序发布的资料里已经没有这样的地图：园林中庭的进度上限已按实测改正
        self.assertNotIn("cap_untrusted", self.db.rules["difficulty"])
        rules = {**self.db.rules, "difficulty": {"cap_untrusted": ["园林中庭"]}}
        db = dataclasses.replace(self.db, rules=rules)
        for cap in (16, 17, 18, 19, None):
            for hard in (False, True):
                with self.subTest(cap=cap, hard=hard):
                    self.assertEqual(infer_difficulty(db, "园林中庭", cap, hard),
                                     (None, None, "地图「园林中庭」的进度上限资料与实测不符", ()))
        # 这一项没有写时照资料推算
        self.assertEqual(infer_difficulty(self.db, "园林中庭", 18, True)[:2], (LEVEL_HARD, "困难"))
        self.assertEqual(infer_difficulty(self.db, "园林中庭", 17, True)[:2], (LEVEL_NIGHTMARE, None))
        # 没有 [difficulty] 一节时不停用任何地图
        db = dataclasses.replace(self.db, rules={k: v for k, v in self.db.rules.items() if k != "difficulty"})
        self.assertEqual(infer_difficulty(db, "园林中庭", 16, True)[:2], (None, None))


# ------------------------------------------------------------ State 和引擎


class StateTest(Base):
    def test_fields(self):
        st = State(hero="阿尔")
        self.assertIsNone(st.difficulty)
        self.assertIsNone(st.difficulty_level)
        self.assertIsNone(st.difficulty_name)
        a = State(hero="阿尔", difficulty_level=LEVEL_NIGHTMARE, difficulty_name="噩梦")
        b = State(hero="阿尔", difficulty_level=LEVEL_NIGHTMARE, difficulty_name="噩梦")
        self.assertEqual({a: 1}[b], 1)  # 可以当字典的键
        self.assertNotEqual(a, st)
        self.assertIsNone(a.difficulty)  # difficulty 的含义不变：困难及以上和尚未判定都是 None


class NightmareTest(Base):
    """噩梦及以上时摩托头盔-高级的「难度」分项（rules.toml [survival] nightmare_*）。"""

    def parts(self, hero, chip, **kw):
        return [p for p in Scorer(self.db, State(hero=hero, **kw)).parts(chip, (), None) if p.label == "难度"]

    def scores(self, hero, chip, **kw):
        return [p.score for p in self.parts(hero, chip, **kw)]

    def trait_heroes(self) -> set[str]:
        trait = self.db.rules["survival"]["trait"]
        return {h for h, syn in self.db.hero_synergy.items() if trait in syn.traits}

    def test_config(self):
        cfg = self.db.rules["survival"]
        self.assertEqual(cfg["nightmare_chips"], ["摩托头盔-高级"])
        self.assertEqual((cfg["nightmare_factor"], cfg["nightmare_trait_factor"]), (1.5, 0.5))

    def test_nightmare_plus(self):
        def flag(**kw):
            return nightmare_plus(Scorer(self.db, State(hero="阿尔", **kw)))

        self.assertTrue(flag(difficulty_level=LEVEL_NIGHTMARE))
        self.assertTrue(flag(difficulty_level=LEVEL_NIGHTMARE, difficulty_name="极限"))
        for level in (*NOT_NIGHTMARE, LEVEL_NORMAL):
            self.assertFalse(flag(difficulty_level=level), level)
        # 概率行判定为普通时以 difficulty 为准
        self.assertFalse(flag(difficulty=NORMAL, difficulty_level=LEVEL_NIGHTMARE))

    def test_helmets_by_level(self):
        # 各难度下摩托头盔三件的「难度」分项：只有高级在噩梦及以上提高。阿尔、凛不是物理输出角色，美咲是；茉莉带「需要生存」特性
        before = {
            ("阿尔", "摩托头盔-高级"): [17.5], ("阿尔", "摩托头盔-中级"): [12.5], ("阿尔", "摩托头盔-一般"): [7.5],
            ("凛", "摩托头盔-高级"): [17.5], ("凛", "摩托头盔-中级"): [12.5], ("凛", "摩托头盔-一般"): [7.5],
            ("美咲", "摩托头盔-高级"): [35.0], ("美咲", "摩托头盔-中级"): [25.0], ("美咲", "摩托头盔-一般"): [15.0],
            ("茉莉", "摩托头盔-高级"): [], ("茉莉", "摩托头盔-中级"): [], ("茉莉", "摩托头盔-一般"): [],
        }
        nightmare = {**before, ("阿尔", "摩托头盔-高级"): [52.5], ("凛", "摩托头盔-高级"): [52.5],
                     ("美咲", "摩托头盔-高级"): [52.5], ("茉莉", "摩托头盔-高级"): [17.5]}
        for (hero, chip), want in before.items():
            for level in NOT_NIGHTMARE:
                with self.subTest(hero=hero, chip=chip, level=level):
                    self.assertEqual(self.scores(hero, chip, difficulty_level=level), want)
            with self.subTest(hero=hero, chip=chip, level=LEVEL_NIGHTMARE):
                self.assertEqual(self.scores(hero, chip, difficulty_level=LEVEL_NIGHTMARE), nightmare[(hero, chip)])
            # 普通难度不加，档位是什么都一样
            for level in (None, LEVEL_NORMAL, LEVEL_NIGHTMARE):
                with self.subTest(hero=hero, chip=chip, difficulty=NORMAL, level=level):
                    self.assertEqual(self.parts(hero, chip, difficulty=NORMAL, difficulty_level=level), [])

    def test_gold_helmet_for_all_heroes(self):
        # 所有角色：不带「需要生存」特性的按特性分值 × 1.5（物理输出角色不另加），带的另加 × 0.5（自动层已按全额计入）
        cfg = self.db.rules["survival"]
        base = max(r.score for r in self.db.trait_rules[cfg["trait"]] if "摩托头盔-高级" in r.chips)
        self.assertEqual(base, 35)
        trait_heroes = self.trait_heroes()
        self.assertTrue(trait_heroes)
        self.assertIn("茉莉", trait_heroes)
        for hero in self.db.heroes:
            with self.subTest(hero=hero):
                want = base * (0.5 if hero in trait_heroes else 1.5)
                self.assertEqual(self.scores(hero, "摩托头盔-高级", difficulty_level=LEVEL_NIGHTMARE), [want])

    def test_others_unchanged(self):
        # 噩梦及以上只改摩托头盔-高级：其他生存类筹码、非生存类筹码的全部分项和分不出困难还是噩梦（困难及以上）时相同；
        # 和读不到难度时比，只差「难度」的整句：前面多「难度待识别，按困难及以上计算：」、后半句主语写「这几档难度」，
        # 分值和短句相同
        chips = ("摩托头盔-中级", "摩托头盔-一般", "医疗箱-完备治疗", "安全气囊", "标靶", "额外电池")
        for hero in ("阿尔", "美咲", "茉莉", "凛"):
            for chip in chips:
                with self.subTest(hero=hero, chip=chip):
                    plain = Scorer(self.db, State(hero=hero, round=5, difficulty_level=LEVEL_HARD_PLUS)).parts(chip, (), 5)
                    night = Scorer(self.db, State(hero=hero, round=5, difficulty_level=LEVEL_NIGHTMARE)).parts(chip, (), 5)
                    self.assertEqual(night, plain)
                    unknown = Scorer(self.db, State(hero=hero, round=5)).parts(chip, (), 5)
                    self.assertEqual([dataclasses.replace(p, reason=as_known_difficulty(p.reason)) for p in unknown], plain)
                    for p in unknown:
                        self.assertEqual(p.reason.startswith(UNKNOWN_DIFFICULTY_LEAD), p.label == "难度")

    def test_unknown_difficulty_tip(self):
        """难度不知道（State.difficulty_level 为 None：推荐按困难及以上计算，识别卡片不显示难度胶囊）时，生存类筹码
        「难度：生存类」的悬停整句前面写「难度待识别，按困难及以上计算：」（只改这条标签的悬停说明），
        后半句的主语写「这几档难度」，不让「困难及以上」连着出现两次；
        标签短句和分值不变，知道难度（困难、困难及以上、噩梦及以上）时完全不变，普通难度照旧没有这一项。"""
        base = "困难及以上难度怪物数值更高、进度上限更短，提高生存能力的收益较高"
        fighter = "困难及以上难度怪物数值更高、进度上限更短；该角色经常挑战怪物，防御力可减少受到的反击伤害"
        unknown_base = "难度待识别，按困难及以上计算：这几档难度怪物数值更高、进度上限更短，提高生存能力的收益较高"
        unknown_fighter = "难度待识别，按困难及以上计算：这几档难度怪物数值更高、进度上限更短；该角色经常挑战怪物，防御力可减少受到的反击伤害"
        self.assertEqual(UNKNOWN_DIFFICULTY_LEAD, "难度待识别，按困难及以上计算：")
        for hero, chip, reason, unknown_reason in (("凛", "摩托头盔-中级", base, unknown_base),
                                                   ("美咲", "摩托头盔-中级", fighter, unknown_fighter),
                                                   ("阿尔", "医疗箱-完备治疗", base, unknown_base)):
            with self.subTest(hero=hero, chip=chip):
                [unknown] = self.parts(hero, chip)
                self.assertEqual((unknown.reason, unknown.short), (unknown_reason, "难度：生存类"))
                self.assertEqual(unknown.reason.count("困难及以上"), 1)
                self.assertEqual(as_known_difficulty(unknown.reason), reason)
                for level in (LEVEL_HARD, LEVEL_HARD_PLUS):
                    [known] = self.parts(hero, chip, difficulty_level=level)
                    self.assertEqual((known.score, known.reason, known.short), (unknown.score, reason, "难度：生存类"))
                self.assertEqual(self.parts(hero, chip, difficulty=NORMAL), [])
        # 噩梦及以上的摩托头盔-高级照旧写噩梦的整句；其余生存类筹码在噩梦及以上时也不加这一句
        [night] = self.parts("凛", "摩托头盔-高级", difficulty_level=LEVEL_NIGHTMARE)
        self.assertEqual(night.reason, NIGHTMARE_REASON)
        [mid] = self.parts("凛", "摩托头盔-中级", difficulty_level=LEVEL_NIGHTMARE)
        self.assertEqual(mid.reason, base)
        # 命令行和诊断包用的是同一个整句（Part.reason）：读不到难度时同样带这一句
        adv = advise(self.db, State(hero="凛", star=1, round=5, rerolls=2), ["摩托头盔-中级", "标靶", "书签"])
        part = next(p for p in adv.evals[0].parts if p.label == "难度")
        self.assertIn(unknown_base, cli.render(self.db, adv))
        self.assertTrue(part.reason.startswith(UNKNOWN_DIFFICULTY_LEAD))

    def test_wording(self):
        # 整句写机制，不写特性名和分值；短句以分项名开头、不写数值
        for hero in ("阿尔", "美咲", "茉莉"):
            [part] = self.parts(hero, "摩托头盔-高级", difficulty_level=LEVEL_NIGHTMARE)
            with self.subTest(hero=hero):
                self.assertEqual(part.reason, NIGHTMARE_REASON)
                self.assertEqual(part.short, "难度：噩梦及以上")
                self.assertEqual(part.origin, "survival_nightmare")
        self.assertIn("噩梦及以上", NIGHTMARE_REASON)
        for word in ("需要生存", "物理输出", "分值", "总分", "（+"):
            self.assertNotIn(word, NIGHTMARE_REASON)
        self.assertIsNone(BANNED_WORDING.search(NIGHTMARE_REASON))
        self.assertIsNone(re.search(r"\d", "难度：噩梦及以上"))
        self.assertLessEqual(len("难度：噩梦及以上"), SHORT_LIMIT)

    def test_advice(self):
        # 10-03 那局（异变图书馆、噩梦、阿尔）130113-3：噩梦及以上时金头盔的不计前瞻分值高 35（17.5 → 52.5），推荐度上升；
        # 分不出困难还是噩梦（困难及以上）时和读不到难度相同。首推和各项百分比还取决于适配表（阿尔的额外电池等），
        # 期望的排序另在 tests/test_engine.py 的 AlLiveReviewTest.test_nightmare_helmet 里断言
        def run(level):
            st = State(hero="阿尔", star=1, round=5, map="异变图书馆", owned=("“8面”骰子", "ATM机"), source="task",
                       tier=2, rerolls=3, probabilities=(40, 45, 15), progress=5,
                       teammates=(Teammate("帕露南"), Teammate("茉莉"), Teammate("邦妮")), difficulty_level=level)
            adv = advise(self.db, st, ["额外电池", "摩托头盔-高级", "速度轮滑-中级"])
            return {e.chip.name: e for e in adv.evals}, adv

        plain, plain_adv = run(None)
        night, night_adv = run(LEVEL_NIGHTMARE)
        unsure, unsure_adv = run(LEVEL_HARD_PLUS)
        helmet = "摩托头盔-高级"
        self.assertAlmostEqual(night[helmet].immediate - plain[helmet].immediate, 35.0)
        self.assertGreater(night[helmet].percent, plain[helmet].percent)
        self.assertEqual([(e.total, e.percent) for e in unsure.values()], [(e.total, e.percent) for e in plain.values()])
        self.assertEqual(unsure_adv.best, plain_adv.best)
        self.assertNotIn(NORMAL_NOTE, night_adv.notes)


# ------------------------------------------------------------ 命令行、规则文件


class CliTest(Base):
    ARGS = ["--hero", "阿尔", "--star", "1", "--round", "5", "--offer", "额外电池", "摩托头盔-高级", "速度轮滑-中级"]

    def run_cli(self, *extra) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*self.ARGS, *extra])
        return out.getvalue()

    def test_nightmare(self):
        text = self.run_cli("--difficulty", "nightmare")
        self.assertIn("｜地图：未指定｜难度：噩梦及以上｜", text)
        self.assertNotIn(NORMAL_NOTE, text)  # 概率表和进度上限同困难及以上
        self.assertIn(f"{NIGHTMARE_REASON}（+52）", text)
        data = json.loads(self.run_cli("--difficulty", "nightmare", "--json"))
        helmet = next(c for c in data["chips"] if c["name"] == "摩托头盔-高级")
        self.assertIn({"label": "难度", "score": 52.5, "reason": NIGHTMARE_REASON}, helmet["parts"])

    def test_other_choices_unchanged(self):
        self.assertIn("｜地图：未指定｜难度：普通｜", self.run_cli("--difficulty", "normal"))
        for extra in ((), ("--difficulty", "hard")):
            text = self.run_cli(*extra)
            self.assertNotIn("难度：", text.splitlines()[0])  # 困难及以上不显示
            self.assertNotIn(NIGHTMARE_REASON, text)


class RulesFileTest(unittest.TestCase):
    def test_al_starlight_layers(self):
        # 阿尔的被动：每 6 层星光攻击力 +1、防御力 +1（照游戏内核对；英文 wiki 的更新日志页也是 6 层）
        text = (ROOT / "data" / "rules.toml").read_text(encoding="utf-8")
        self.assertNotIn("每 7 层星光", text)
        self.assertIn("每 6 层星光", text)


if __name__ == "__main__":
    unittest.main()
