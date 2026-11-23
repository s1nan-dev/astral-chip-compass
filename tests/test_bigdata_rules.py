"""大数据规则：照真实玩家对局的统计给筹码加减的分。分两层：角色的（适配表 [[hero]] 下的 [[hero.bigdata]]，
HeroSynergy.bigdata_rules）和全体的（最外层的 [[bigdata]]，DB.bigdata_rules，可以带 hero_groups、own_school 两个只看角色的条件）。

核对：
- 载入：进 bigdata_rules、不进自动层的 rules；写错的一条、筹码不存在、写了 net（不支持）时记警告；全体的一层另核对
  exclude（不支持）、分组名写错、own_school 写在没有流派的筹码上，角色的一层写了这两个条件时记警告；
- 算分：适用的每条各成一项「大数据」，总分正好加减这条的分；不进自动层的合并（不改「其他特性」、不受 cap 影响），
  不算进收入折算的对象；条件照 Rule.applies；和自动层在同样的情形下算（默认推荐的档位、方案标了档位的筹码、
  循环往复都不算）；全体的一层对每个角色都算（包括适配表里没有这名角色的），只看角色的条件照 Rule.fits_hero，
  两层命中的各条各成一项、相加（先全体的、后角色的）；
- 显示：只计分，推荐卡片的理由标签、悬停明细都不列；命令行、诊断包的推荐全文照写，两层各一行；
- 随程序发布的规则（ShippedRulesTest）：带着读进来没有警告，筹码存在、分组名合法，每个角色每张筹码合起来的加减在
  约定的范围里、单条不超过 25，同一层里同一张筹码同样的条件不写两条。

这套机制用测试自己造的规则来测，和资料库里现在有哪些规则无关：载入的测试复制资料到沙箱里，先去掉副本里原有的大数据
规则（strip_bigdata_rules）再插进自己的、带着大数据规则读；算分和显示的测试在不读大数据规则的资料库（load_db(bigdata_rules=
False)）的副本上换进测试用的规则。
"""
from __future__ import annotations

import dataclasses
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from advisor import cli, paths
from advisor.data import BIGDATA_ORIGIN, HERO_GROUPS, Rule, load_db
from advisor.engine import Plan, Scorer, advise
from advisor.engine.advice_text import SHORT_LIMIT
from advisor.pool import State
from advisor.ui import text as ui_text
from tests.support.player_layer import with_player_layer

HERO = "凛"
FILES = ("chips.json", "heroes.json", "rules.toml", "synergy.toml", "series.toml", "maps.json", "mutators.json", "meta.json")


def data_rule(chip: str, score: float, reason: str = "玩这名角色的玩家很少选它", **kw) -> Rule:
    return Rule(chips=frozenset({chip}), score=score, reason=reason, origin=BIGDATA_ORIGIN, **kw)


def with_data(db, hero: str, *rules: Rule):
    """db 的副本：hero 的大数据规则换成 rules，其余不变。"""
    hs = dataclasses.replace(db.hero_synergy[hero], bigdata_rules=tuple(rules))
    return dataclasses.replace(db, hero_synergy={**db.hero_synergy, hero: hs})


def with_global(db, *rules: Rule):
    """db 的副本：全体的大数据规则换成 rules，其余不变。"""
    return dataclasses.replace(db, bigdata_rules=tuple(rules))


def bigdata_parts(parts) -> list:
    return [p for p in parts if p.label == BIGDATA_ORIGIN]


_BIGDATA_HEAD = re.compile(r"\[\[(bigdata|hero\.bigdata)\]\]\s*")


def strip_bigdata_rules(text: str) -> str:
    """适配表的文字去掉全部大数据规则：以 [[bigdata]]、[[hero.bigdata]] 开头的每一段（到空行或下一个表头为止），连同它上面
    紧挨着的注释行。文件开头说明格式的注释里那两条示例是注释（行首是 #），不动。"""
    lines = text.splitlines(keepends=True)
    drop = [False] * len(lines)
    for i, line in enumerate(lines):
        if not _BIGDATA_HEAD.fullmatch(line.rstrip("\r\n")):
            continue
        j = i
        while j < len(lines) and (j == i or not (lines[j].strip() == "" or lines[j].startswith("["))):
            drop[j] = True
            j += 1
        k = i - 1
        while k >= 0 and lines[k].startswith("#"):
            drop[k] = True
            k -= 1
    return "".join(line for line, gone in zip(lines, drop) if not gone)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db(bigdata_rules=False)  # 不带资料库里的大数据规则：算分、显示的测试换进自己的规则
        cls.chips = [c.name for c in cls.db.chips.values()
                     if not c.granted and not c.maps and c.name != cls.db.rules["endless_cycle"]["chip"]]

    def parts(self, db, chip: str, owned=(), st: State | None = None, **kw):
        st = st or State(hero=HERO, star=1, rerolls=3)
        return Scorer(db, st, **kw).parts(chip, owned, st.round)


class LoadTest(Base):
    def load(self, block: str, top: bool = False, warns: bool = True, outer: bool = False):
        """资料库的副本先去掉原有的大数据规则（strip_bigdata_rules），再在 HERO 那一段的末尾（下一个 [[hero]] 之前；top 为真时
        紧接在 name 那一行后面，给只写一个键的 block）插入 block，带着大数据规则载入；outer 为真时插在文件最前面（最外层的键
        和 [[bigdata]]）。warns：会记警告（同时写日志，核对写了）。"""
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in FILES:
            src = paths.data_dir() / name
            if src.exists():
                shutil.copy(src, tmp / name)
        text = strip_bigdata_rules((tmp / "synergy.toml").read_text(encoding="utf-8"))
        head = f'[[hero]]\nname = "{HERO}"\n'
        start = text.index(head)
        if outer:
            text = block + "\n" + text
        elif top:
            at = start + len(head)
            text = text[:at] + block + text[at:]
        else:
            at = text.index("\n[[hero]]\n", start + 1)
            text = text[:at] + "\n" + block + text[at:]
        (tmp / "synergy.toml").write_text(text, encoding="utf-8")
        if not warns:
            return load_db(str(tmp), bigdata_rules=True)
        with self.assertLogs("advisor.data", level="WARNING"):
            return load_db(str(tmp), bigdata_rules=True)

    def test_stripped_copy_has_no_rules(self):
        """去掉原有大数据规则的副本：两层都空、没有警告，其余（自动层、全体的特性、角色）和资料库一样。"""
        db = self.load("", warns=False)
        self.assertEqual(db.warnings, [])
        self.assertEqual(db.bigdata_rules, ())
        self.assertEqual({name: hs.bigdata_rules for name, hs in db.hero_synergy.items() if hs.bigdata_rules}, {})
        # Rule 按对象比相等（eq=False），逐个字段比
        self.assertEqual({name: [dataclasses.astuple(r) for r in hs.rules] for name, hs in db.hero_synergy.items()},
                         {name: [dataclasses.astuple(r) for r in hs.rules] for name, hs in self.db.hero_synergy.items()})
        # 文件开头说明格式的注释没动
        text = strip_bigdata_rules((paths.data_dir() / "synergy.toml").read_text(encoding="utf-8"))
        self.assertIn("#                 [[hero.bigdata]]\n", text)
        self.assertRegex(text, r"(?m)^#   \[\[bigdata\]\] ")

    def test_loaded_into_bigdata_rules(self):
        db = self.load('[[hero.bigdata]]\nchips = ["可口糖果"]\nscore = -20\nreason = "玩这名角色的玩家很少选它"\n'
                       'short = "玩家少选"\nmax_round = 6\n', warns=False)
        self.assertEqual(db.warnings, [])
        hs = db.hero_synergy[HERO]
        self.assertEqual([(set(r.chips), r.score, r.reason, r.short, r.origin, r.max_round) for r in hs.bigdata_rules],
                         [({"可口糖果"}, -20.0, "玩这名角色的玩家很少选它", "玩家少选", BIGDATA_ORIGIN, 6)])
        self.assertFalse(any(r.origin == BIGDATA_ORIGIN for r in hs.rules))
        self.assertEqual(len(hs.rules), len(self.db.hero_synergy[HERO].rules))  # 自动层的规则不变

    def test_net_not_supported(self):
        db = self.load('[[hero.bigdata]]\nchips = ["可口糖果"]\nscore = -20\nreason = "少选"\n'
                       'net = { "角色" = "净效果" }\n')
        self.assertEqual(db.warnings, [f"角色 {HERO} 的大数据规则（可口糖果）：大数据规则不支持 net，已忽略这一项"])
        self.assertEqual(db.hero_synergy[HERO].bigdata_rules[0].net, {})

    def test_bad_entries_warned(self):
        db = self.load('[[hero.bigdata]]\nchips = ["不存在的筹码"]\nscore = 10\nreason = "x"\n'
                       '[[hero.bigdata]]\nchips = ["可口糖果"]\nreason = "缺分值"\n'
                       '[[hero.bigdata]]\nchips = ["书签"]\nscore = 5\nreason = "y"\nneeds_source = "没有这种来源"\n')
        self.assertIn(f"角色 {HERO} 的大数据规则：筹码「不存在的筹码」不存在", db.warnings)
        self.assertIn(f"角色 {HERO} 的大数据规则的第 2 条规则：score 应为数，已忽略", db.warnings)
        self.assertIn(f"角色 {HERO} 的大数据规则（书签）：needs_source 写的来源「没有这种来源」在 rules.toml [sources] "
                      "里没有，这条规则不会生效", db.warnings)
        self.assertEqual([sorted(r.chips) for r in db.hero_synergy[HERO].bigdata_rules], [["不存在的筹码"], ["书签"]])

    def test_not_array(self):
        db = self.load("bigdata = 1\n", top=True)
        self.assertEqual(db.warnings, [f"角色 {HERO} 的大数据规则：bigdata 应写成规则的数组，已忽略"])
        self.assertEqual(db.hero_synergy[HERO].bigdata_rules, ())


class ShippedRulesTest(unittest.TestCase):
    """随程序发布的大数据规则（data/synergy.toml 里的 [[bigdata]]、[[hero.bigdata]]）带着载入，写得对：规则每个版本照新的玩家
    数据重算，分值本身不在测试里写死，这里只查写法。"""

    # 每个角色每张筹码合起来的加减范围：扣的最多扣 25，加的最多加 15（写法见适配表里大数据规则一节开头的说明）
    LOW, HIGH = -25, 15
    CONDITIONS = ("maps", "needs_owned", "max_round", "difficulty", "needs_source", "without_source", "hero_groups",
                  "own_school")

    @classmethod
    def setUpClass(cls):
        cls.db = load_db(bigdata_rules=True)
        cls.layers = {"全体": cls.db.bigdata_rules,
                      **{f"角色 {name}": hs.bigdata_rules for name, hs in cls.db.hero_synergy.items() if hs.bigdata_rules}}

    def test_has_rules(self):
        # 两层都有规则（没有时下面几条都是空跑）；不读大数据规则时两层都空
        self.assertTrue(self.db.bigdata_rules)
        self.assertGreater(len(self.layers), 1)
        bare = load_db(bigdata_rules=False)
        self.assertEqual((bare.bigdata_rules, [n for n, hs in bare.hero_synergy.items() if hs.bigdata_rules]), ((), []))

    def test_loads_without_warnings(self):
        self.assertEqual(self.db.warnings, [])
        self.assertEqual(self.db.problems, [])

    def test_well_formed(self):
        """origin 是 BIGDATA_ORIGIN、不进自动层的 rules，写了 short 时不超过 SHORT_LIMIT 个字；不写 net、exclude。"""
        for name, hs in self.db.hero_synergy.items():
            self.assertFalse(any(r.origin == BIGDATA_ORIGIN for r in hs.rules), name)
        for layer, rules in self.layers.items():
            for r in rules:
                with self.subTest(layer=layer, chips=sorted(r.chips)):
                    self.assertEqual(r.origin, BIGDATA_ORIGIN)
                    self.assertLessEqual(len(r.short), SHORT_LIMIT, r.short)
                    self.assertTrue(r.reason)
                    self.assertEqual((r.net, r.exclude), ({}, frozenset()))

    def test_chips_and_groups_exist(self):
        for layer, rules in self.layers.items():
            for r in rules:
                with self.subTest(layer=layer, chips=sorted(r.chips)):
                    self.assertTrue(r.chips)
                    self.assertLessEqual(r.chips | r.needs_owned, set(self.db.chips))
                    self.assertLessEqual(r.hero_groups, set(HERO_GROUPS))
                    if layer != "全体":  # 只看角色的两个条件只有全体的一层能写
                        self.assertEqual((r.hero_groups, r.own_school), (frozenset(), False))

    def test_single_rule_scores(self):
        """单条规则：分值不为 0，绝对值不超过 25。单条可以超出 LOW～HIGH：某一类角色或某个角色自己的数据和全体那一条方向
        相反时，在分组那一层或角色那一层写一条反向的分把它抵掉，最多抵掉一条扣 25 的。"""
        for layer, rules in self.layers.items():
            for r in rules:
                with self.subTest(layer=layer, chips=sorted(r.chips)):
                    self.assertNotEqual(r.score, 0)
                    self.assertLessEqual(abs(r.score), -self.LOW)

    def test_net_scores_in_range(self):
        """每个角色、每张筹码：两层里对这个角色生效的规则（全体的一层按 Rule.fits_hero，角色的一层直接算）合起来的加减在
        LOW～HIGH 之间。规则带了局面条件（maps、difficulty 等）时不看条件、全都算上。"""
        for name, hero in self.db.heroes.items():
            hs = self.db.hero_synergy.get(name)
            schools, own = (hs.schools, hs.bigdata_rules) if hs else ((), ())
            for chip in self.db.chips.values():
                net = sum(r.score for r in self.db.bigdata_rules
                          if chip.name in r.chips and r.fits_hero(hero.group, schools, chip.school))
                net += sum(r.score for r in own if chip.name in r.chips)
                if net:
                    with self.subTest(hero=name, chip=chip.name):
                        self.assertTrue(self.LOW <= net <= self.HIGH, net)

    def test_no_duplicates_in_a_layer(self):
        """同一层里，同一张筹码、同样的条件只写一条（重算时漏删旧的一条会让两条相加）。"""
        for layer, rules in self.layers.items():
            seen: dict[tuple, str] = {}
            for r in rules:
                cond = tuple(getattr(r, key) for key in self.CONDITIONS)
                for chip in sorted(r.chips):
                    with self.subTest(layer=layer, chip=chip):
                        self.assertNotIn((chip, cond), seen, f"和「{seen.get((chip, cond))}」那一条重复")
                    seen[(chip, cond)] = r.reason


class GlobalLoadTest(Base):
    """全体的大数据规则（最外层的 [[bigdata]]）的载入。"""

    load = LoadTest.load

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        playable = [c for c in cls.db.chips.values() if not c.granted]
        cls.schooled = next(c.name for c in playable if c.school)
        cls.plain = next(c.name for c in playable if not c.school)

    def test_loaded_into_db(self):
        db = self.load(f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = -16\nreason = "玩家实际拿得比推荐的少"\n'
                       'short = "玩家少拿"\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = 14\nhero_groups = ["法术C", "辅C"]\n'
                       'reason = "这一类角色的玩家照常拿它"\nmax_round = 6\n'
                       f'[[bigdata]]\nchips = ["{self.schooled}"]\nscore = 5\nown_school = true\nreason = "本流派"\n',
                       outer=True, warns=False)
        self.assertEqual(db.warnings, [])
        self.assertEqual(
            [(set(r.chips), r.score, r.reason, r.short, r.origin, r.max_round, set(r.hero_groups), r.own_school)
             for r in db.bigdata_rules],
            [({self.plain}, -16.0, "玩家实际拿得比推荐的少", "玩家少拿", BIGDATA_ORIGIN, None, set(), False),
             ({self.plain}, 14.0, "这一类角色的玩家照常拿它", "", BIGDATA_ORIGIN, 6, {"法术C", "辅C"}, False),
             ({self.schooled}, 5.0, "本流派", "", BIGDATA_ORIGIN, None, set(), True)])
        # 不进任何角色的自动层和角色的大数据规则
        for name, hs in db.hero_synergy.items():
            self.assertEqual(hs.bigdata_rules, (), name)
            self.assertEqual(len(hs.rules), len(self.db.hero_synergy[name].rules), name)

    def test_not_array(self):
        db = self.load("bigdata = 1\n", outer=True)
        self.assertEqual(db.warnings, ["全体的大数据规则：bigdata 应写成规则的数组，已忽略"])
        self.assertEqual(db.bigdata_rules, ())
        self.assertTrue(db.chips and db.hero_synergy)  # 程序照常起来

    def test_bad_entries_warned(self):
        db = self.load('[[bigdata]]\nchips = ["不存在的筹码"]\nscore = 10\nreason = "x"\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nreason = "缺分值"\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = -3\nreason = "y"\nnet = {{ "角色" = "净效果" }}\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = -4\nreason = "z"\nexclude = ["{HERO}"]\n',
                       outer=True)
        self.assertEqual(db.warnings, [
            "全体的大数据规则的第 2 条规则：score 应为数，已忽略",
            "全体的大数据规则：筹码「不存在的筹码」不存在",
            f"全体的大数据规则（{self.plain}）：大数据规则不支持 net，已忽略这一项",
            f"全体的大数据规则（{self.plain}）：全体的大数据规则不支持 exclude，已忽略这一项",
        ])
        self.assertEqual([(sorted(r.chips), r.score) for r in db.bigdata_rules],
                         [(["不存在的筹码"], 10.0), ([self.plain], -3.0), ([self.plain], -4.0)])
        self.assertEqual([r.net for r in db.bigdata_rules][1], {})
        self.assertEqual(db.bigdata_rules[2].exclude, frozenset())

    def test_hero_conditions_warned(self):
        db = self.load(f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = 10\nhero_groups = ["法术c"]\nreason = "a"\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = 10\nhero_groups = "法术C"\nreason = "b"\n'
                       f'[[bigdata]]\nchips = ["{self.plain}"]\nscore = 10\nown_school = 1\nreason = "c"\n'
                       f'[[bigdata]]\nchips = ["{self.schooled}", "{self.plain}"]\nscore = 10\nown_school = true\n'
                       'reason = "d"\n', outer=True)
        groups = "」「".join(("物理主C", "法术C", "辅C", "钱辅", "奶辅", "功能辅"))
        self.assertEqual(db.warnings, [
            f"全体的大数据规则（{self.plain}）：hero_groups 里的分组「法术c」不存在（只能写「{groups}」），这条规则不生效",
            f"全体的大数据规则（{self.plain}）：hero_groups 应为角色分组的列表，这条规则不生效",
            f"全体的大数据规则（{self.plain}）：own_school 应写 true 或 false，这条规则不生效",
            f"全体的大数据规则（{self.schooled}、{self.plain}）：筹码「{self.plain}」不属于任何流派，"
            "own_school 对它永远不生效",
        ])
        # 写错的三条不收；own_school 写在没有流派的筹码上的那条照收（那张筹码上不生效）
        self.assertEqual([r.reason for r in db.bigdata_rules], ["d"])

    def test_conditions_in_hero_bigdata_ignored(self):
        db = self.load(f'[[hero.bigdata]]\nchips = ["{self.schooled}"]\nscore = 10\nreason = "x"\n'
                       'hero_groups = ["法术C"]\nown_school = true\n')
        self.assertEqual(db.warnings, [
            f"角色 {HERO} 的大数据规则（{self.schooled}）：hero_groups 只能写在最外层的 [[bigdata]] 里，已忽略这一项",
            f"角色 {HERO} 的大数据规则（{self.schooled}）：own_school 只能写在最外层的 [[bigdata]] 里，已忽略这一项",
        ])
        rule = db.hero_synergy[HERO].bigdata_rules[0]
        self.assertEqual((rule.hero_groups, rule.own_school), (frozenset(), False))
        self.assertEqual(db.bigdata_rules, ())


class GlobalScoreTest(Base):
    """全体的大数据规则的算分：对每个角色都算，只看角色的条件照 Rule.fits_hero，和角色的一层相加。"""

    def hero_parts(self, db, hero: str, chip: str, **kw):
        return self.parts(db, chip, st=State(hero=hero, star=1, rerolls=3), **kw)

    def test_every_hero(self):
        chip = self.chips[0]
        db = with_global(self.db, data_rule(chip, -16, "玩家实际拿得比推荐的少", short="玩家少拿"))
        for hero in self.db.heroes:
            with self.subTest(hero=hero):
                before, after = self.hero_parts(self.db, hero, chip), self.hero_parts(db, hero, chip)
                self.assertEqual([(p.score, p.reason, p.origin, p.short) for p in bigdata_parts(after)],
                                 [(-16, "玩家实际拿得比推荐的少", BIGDATA_ORIGIN, "玩家少拿")])
                self.assertEqual([(p.label, p.score) for p in after if p.label != BIGDATA_ORIGIN],
                                 [(p.label, p.score) for p in before])
                self.assertAlmostEqual(sum(p.score for p in after) - sum(p.score for p in before), -16)
        # 适配表里没有这名角色（没有 [[hero]] 一节）时照样算
        bare = dataclasses.replace(db, hero_synergy={k: v for k, v in db.hero_synergy.items() if k != HERO})
        self.assertEqual([p.score for p in bigdata_parts(self.hero_parts(bare, HERO, chip))], [-16])
        # 别的筹码不受影响
        self.assertEqual(bigdata_parts(self.hero_parts(db, HERO, self.chips[1])), [])

    def test_hero_groups(self):
        chip = self.chips[0]
        db = with_global(self.db, data_rule(chip, 14, hero_groups=frozenset({"法术C", "奶辅"})))
        groups = {h.group for h in self.db.heroes.values()}
        self.assertTrue({"法术C", "奶辅"} < groups)
        for hero in self.db.heroes.values():
            with self.subTest(hero=hero.name):
                n = 1 if hero.group in ("法术C", "奶辅") else 0
                self.assertEqual(len(bigdata_parts(self.hero_parts(db, hero.name, chip))), n)

    def test_own_school(self):
        schools = self.db.hero_synergy[HERO].schools
        inside = next(c for c in self.chips if self.db.chips[c].school in schools)
        outside = next(c for c in self.chips if self.db.chips[c].school and self.db.chips[c].school not in schools)
        none = next(c for c in self.chips if not self.db.chips[c].school)
        rule = Rule(chips=frozenset({inside, outside, none}), score=5, reason="本流派", origin=BIGDATA_ORIGIN,
                    own_school=True)
        db = with_global(self.db, rule)
        # 一条规则列了几张筹码：逐张对，只有属于这个角色推荐流派的那张生效
        for chip, n in ((inside, 1), (outside, 0), (none, 0)):
            with self.subTest(chip=chip):
                self.assertEqual(len(bigdata_parts(self.hero_parts(db, HERO, chip))), n)
        # 推荐流派不同的角色按自己的推荐流派算
        other = next(h for h, hs in self.db.hero_synergy.items()
                     if self.db.chips[outside].school in hs.schools and self.db.chips[inside].school not in hs.schools)
        self.assertEqual(len(bigdata_parts(self.hero_parts(db, other, outside))), 1)
        self.assertEqual(bigdata_parts(self.hero_parts(db, other, inside)), [])
        # 适配表里没有这名角色：没有推荐流派，不生效
        bare = dataclasses.replace(db, hero_synergy={k: v for k, v in db.hero_synergy.items() if k != HERO})
        self.assertEqual(bigdata_parts(self.hero_parts(bare, HERO, inside)), [])

    def test_layers_add(self):
        """几条同时命中（不带条件的、带分组的、带本流派的、这个角色自己的）：各成一项，先全体的后角色的，分值相加。"""
        group = self.db.heroes[HERO].group
        schools = self.db.hero_synergy[HERO].schools
        chip = next(c for c in self.chips if self.db.chips[c].school in schools)
        other_group = next(g for g in (h.group for h in self.db.heroes.values()) if g != group)
        db = with_global(self.db,
                         data_rule(chip, -16, "全体"),
                         data_rule(chip, 14, "分组", hero_groups=frozenset({group})),
                         data_rule(chip, 99, "别的分组", hero_groups=frozenset({other_group})),
                         data_rule(chip, 5, "本流派", own_school=True))
        db = with_data(db, HERO, data_rule(chip, -3, "角色自己的"))
        before, after = self.parts(self.db, chip), self.parts(db, chip)
        self.assertEqual([(p.score, p.reason) for p in bigdata_parts(after)],
                         [(-16, "全体"), (14, "分组"), (5, "本流派"), (-3, "角色自己的")])
        self.assertAlmostEqual(sum(p.score for p in after) - sum(p.score for p in before), -16 + 14 + 5 - 3)

    def test_conditions(self):
        chip = self.chips[0]
        place, elsewhere = list(self.db.maps)[:2]
        db = with_global(self.db, data_rule(chip, -20, maps=frozenset({place})))
        for map_name, n in ((place, 1), (elsewhere, 0)):
            with self.subTest(map=map_name):
                st = State(hero=HERO, star=1, map=map_name, rerolls=3)
                self.assertEqual(len(bigdata_parts(self.parts(db, chip, st=st))), n)

    def test_skipped_like_auto_layer(self):
        marked, unmarked = self.chips[0], self.chips[1]
        db = with_global(self.db, data_rule(marked, -20), data_rule(unmarked, -20))
        plan = Plan(name="测试方案", hero=HERO, tiers=((marked, "first"),))
        self.assertEqual(bigdata_parts(self.parts(db, marked, scheme=plan)), [])
        self.assertEqual(len(bigdata_parts(self.parts(db, unmarked, scheme=plan))), 1)

        layered = with_player_layer(self.db, (HERO,))
        tiered = next(iter(layered.player_synergy[HERO].tiers))
        db = with_global(layered, data_rule(tiered, -20))
        self.assertEqual(bigdata_parts(self.parts(db, tiered)), [])
        self.assertEqual(len(bigdata_parts(self.parts(db, tiered, use_player=False))), 1)

        endless = self.db.rules["endless_cycle"]["chip"]
        db = with_global(self.db, data_rule(endless, -20))
        self.assertEqual(bigdata_parts(self.parts(db, endless)), [])

    def test_not_in_income_base(self):
        income = self.db.rules["income"]
        chip = next(c for c in self.chips if income["coins"].get(c) or income["starlight"].get(c))
        st = State(hero=HERO, star=1, round=10, rerolls=3)
        before = [p for p in self.parts(self.db, chip, st=st) if p.label == "收入折算"]
        after = [p for p in self.parts(with_global(self.db, data_rule(chip, 200)), chip, st=st)
                 if p.label == "收入折算"]
        self.assertEqual([(p.score, p.reason) for p in after], [(p.score, p.reason) for p in before])


class ScoreTest(Base):
    def mixed_chip(self) -> str:
        """HERO 身上同时命中角色专属规则和特性规则（单独一项或合成「其他特性」）的一张筹码。"""
        for chip in self.chips:
            labels = {p.label for p in self.parts(self.db, chip)}
            if "角色专属规则" in labels and labels & {"特性", "其他特性"}:
                return chip
        self.fail(f"{HERO} 没有同时命中角色专属规则和特性规则的筹码")

    def test_added_as_own_part(self):
        chip = self.mixed_chip()
        before = self.parts(self.db, chip)
        for score in (-20, 15, 500):  # 500 比自动层最高那条还高：照样单独一项，不当成「取最高」的那条
            with self.subTest(score=score):
                db = with_data(self.db, HERO, data_rule(chip, score, short="玩家少选"))
                after = self.parts(db, chip)
                data = bigdata_parts(after)
                self.assertEqual([(p.score, p.reason, p.origin, p.short) for p in data],
                                 [(score, "玩这名角色的玩家很少选它", BIGDATA_ORIGIN, "玩家少选")])
                # 其余分项（角色专属规则、特性、其他特性）原样不动，总分正好加减这条的分
                self.assertEqual([(p.label, p.score, p.reason) for p in after if p not in data],
                                 [(p.label, p.score, p.reason) for p in before])
                self.assertAlmostEqual(sum(p.score for p in after) - sum(p.score for p in before), score)

    def test_each_rule_separate(self):
        chip = self.chips[0]
        db = with_data(self.db, HERO, data_rule(chip, 10, "甲"), data_rule(chip, -4, "乙"),
                       data_rule(self.chips[1], 99, "别的筹码"))
        self.assertEqual([(p.score, p.reason) for p in bigdata_parts(self.parts(db, chip))], [(10, "甲"), (-4, "乙")])

    def test_short_falls_back_to_free_short(self):
        chip = self.chips[0]
        db = with_data(self.db, HERO, data_rule(chip, 10, "短"), data_rule(chip, 10, "很" * (SHORT_LIMIT + 5)))
        self.assertEqual([p.short for p in bigdata_parts(self.parts(db, chip))], ["", ""])

    def test_conditions(self):
        chip, other = self.chips[0], self.chips[1]
        db = with_data(self.db, HERO, data_rule(chip, -20, needs_owned=frozenset({other})))
        self.assertEqual(bigdata_parts(self.parts(db, chip)), [])
        self.assertEqual([p.score for p in bigdata_parts(self.parts(db, chip, owned=(other,)))], [-20])
        db = with_data(self.db, HERO, data_rule(chip, -20, max_round=6))
        for rnd, n in ((5, 1), (6, 1), (7, 0), (None, 1)):  # 轮次未知时视为满足
            with self.subTest(round=rnd):
                st = State(hero=HERO, star=1, round=rnd, rerolls=3)
                self.assertEqual(len(bigdata_parts(self.parts(db, chip, st=st))), n)

    def test_more_conditions(self):
        """地图、难度、有没有来源（含靠角色本身提供的来源）的条件和自动层一样按 Rule.applies 判断。"""
        holder = "永动机"  # 「充能」来源的筹码；凛本身不提供充能
        chip = next(c for c in self.chips if c != holder)
        place, elsewhere = list(self.db.maps)[:2]
        cases = (
            ({"maps": frozenset({place})}, State(hero=HERO, star=1, map=place, rerolls=3), (), 1),
            ({"maps": frozenset({place})}, State(hero=HERO, star=1, map=elsewhere, rerolls=3), (), 0),
            ({"difficulty": "normal"}, State(hero=HERO, star=1, difficulty="normal", rerolls=3), (), 1),
            ({"difficulty": "normal"}, State(hero=HERO, star=1, difficulty=None, rerolls=3), (), 0),
            ({"needs_source": "充能"}, None, (), 0),
            ({"needs_source": "充能"}, None, (holder,), 1),
            ({"without_source": "充能"}, None, (), 1),
            ({"without_source": "充能"}, None, (holder,), 0),
            ({"needs_source": "预先标记"}, None, (), 1),  # 凛本身就是预先标记的来源
            ({"without_source": "预先标记"}, None, (), 0),
        )
        for cond, st, owned, n in cases:
            with self.subTest(cond=cond, st=st, owned=owned):
                db = with_data(self.db, HERO, data_rule(chip, -20, **cond))
                self.assertEqual(len(bigdata_parts(self.parts(db, chip, owned=owned, st=st))), n)

    def test_other_heroes_unaffected(self):
        chip = self.chips[0]
        db = with_data(self.db, HERO, data_rule(chip, -20))
        other = next(h for h in self.db.hero_synergy if h != HERO)
        st = State(hero=other, star=1, rerolls=3)
        self.assertEqual(bigdata_parts(self.parts(db, chip, st=st)), [])

    def test_not_in_income_base(self):
        """收入类筹码：大数据的加分不算进收入折算的对象（和必选同理，是对整体结果的校正）。"""
        income = self.db.rules["income"]
        chip = next(c for c in self.chips if income["coins"].get(c) or income["starlight"].get(c))
        st = State(hero=HERO, star=1, round=10, rerolls=3)
        before = [p for p in self.parts(self.db, chip, st=st) if p.label == "收入折算"]
        after = [p for p in self.parts(with_data(self.db, HERO, data_rule(chip, 200)), chip, st=st)
                 if p.label == "收入折算"]
        self.assertEqual([(p.score, p.reason) for p in after], [(p.score, p.reason) for p in before])

    def test_skipped_like_auto_layer(self):
        """自动层不算的情形大数据也不算（engine.parts._build_parts）：方案标了档位的筹码、默认推荐的档位、循环往复。"""
        marked, unmarked = self.chips[0], self.chips[1]
        db = with_data(self.db, HERO, data_rule(marked, -20), data_rule(unmarked, -20))
        plan = Plan(name="测试方案", hero=HERO, tiers=((marked, "first"),))
        self.assertEqual(bigdata_parts(self.parts(db, marked, scheme=plan)), [])
        self.assertEqual(len(bigdata_parts(self.parts(db, unmarked, scheme=plan))), 1)  # 没标的按自动层算，大数据照算

        layered = with_player_layer(self.db, (HERO,))
        tiered = next(iter(layered.player_synergy[HERO].tiers))
        db = with_data(layered, HERO, data_rule(tiered, -20))
        self.assertEqual(bigdata_parts(self.parts(db, tiered)), [])
        self.assertEqual(len(bigdata_parts(self.parts(db, tiered, use_player=False))), 1)

        endless = self.db.rules["endless_cycle"]["chip"]
        db = with_data(self.db, HERO, data_rule(endless, -20))
        self.assertEqual(bigdata_parts(self.parts(db, endless)), [])


class DisplayTest(Base):
    def test_hidden_on_overlay_shown_for_maintainers(self):
        offer = self.chips[:3]
        reason = "玩这名角色的玩家很少选它"
        db = with_data(self.db, HERO, data_rule(offer[0], -300, reason, short="玩家少选"))
        st = State(hero=HERO, star=1, round=4, rerolls=3)
        adv = advise(db, st, offer)
        item = adv.evals[0]
        self.assertEqual([p.score for p in item.parts if p.origin == BIGDATA_ORIGIN], [-300])
        # 悬浮窗：理由标签、悬停明细都不列这一项（分值照样算进总分）
        pills = ui_text.reason_pills(item, 20)
        self.assertFalse(any(reason in pill.tip or pill.text == "玩家少选" for pill in pills))
        rows = ui_text.breakdown_rows(item)
        self.assertFalse(any(label == BIGDATA_ORIGIN or text == reason for label, text, _ in rows))
        self.assertNotIn(reason, ui_text.breakdown_html(item))
        self.assertNotIn(BIGDATA_ORIGIN, ui_text.breakdown_html(item))
        base = advise(self.db, st, offer).evals[0]
        self.assertAlmostEqual(item.total - base.total, -300)
        # 诊断包、命令行照写
        diag = ui_text.advice_text(db, SimpleNamespace(advice=adv, problems=[]))
        self.assertIn(f"    {BIGDATA_ORIGIN} -300.0  {reason}", diag)
        self.assertIn(f"{reason}（-300）", cli.render(db, adv))
        self.assertIn({"label": BIGDATA_ORIGIN, "score": -300.0, "reason": reason}, cli.to_json(adv)["chips"][0]["parts"])

    def test_two_layers_shown_for_maintainers(self):
        """全体的和角色的两层：悬浮窗上都不列，命令行、诊断包各写一行、看得出各是多少分。"""
        offer = self.chips[:3]
        db = with_data(with_global(self.db, data_rule(offer[0], -200, "所有角色的玩家都少拿", short="玩家少拿")),
                       HERO, data_rule(offer[0], -100, "玩这名角色的玩家很少选它", short="玩家少选"))
        st = State(hero=HERO, star=1, round=4, rerolls=3)
        adv = advise(db, st, offer)
        item = adv.evals[0]
        self.assertEqual([p.score for p in item.parts if p.origin == BIGDATA_ORIGIN], [-200, -100])
        pills = ui_text.reason_pills(item, 20)
        self.assertFalse(any(pill.text in ("玩家少拿", "玩家少选") for pill in pills))
        html = ui_text.breakdown_html(item)
        self.assertNotIn(BIGDATA_ORIGIN, html)
        self.assertNotIn("所有角色的玩家都少拿", html)
        self.assertAlmostEqual(item.total - advise(self.db, st, offer).evals[0].total, -300)
        diag = ui_text.advice_text(db, SimpleNamespace(advice=adv, problems=[]))
        self.assertIn(f"    {BIGDATA_ORIGIN} -200.0  所有角色的玩家都少拿", diag)
        self.assertIn(f"    {BIGDATA_ORIGIN} -100.0  玩这名角色的玩家很少选它", diag)
        text = cli.render(db, adv)
        self.assertIn("所有角色的玩家都少拿（-200）", text)
        self.assertIn("玩这名角色的玩家很少选它（-100）", text)
        parts = cli.to_json(adv)["chips"][0]["parts"]
        self.assertIn({"label": BIGDATA_ORIGIN, "score": -200.0, "reason": "所有角色的玩家都少拿"}, parts)
        self.assertIn({"label": BIGDATA_ORIGIN, "score": -100.0, "reason": "玩这名角色的玩家很少选它"}, parts)

    def test_review_lists_global(self):
        """审查清单：全体的大数据规则单列一节（筹码、分值、理由、条件），各角色的表里理由前写「大数据：」，两层都列。"""
        from tools import make_review
        chip = self.chips[0]
        self.assertEqual(make_review.data_section(self.db)[-2:], ["（暂无）", ""])
        db = with_global(self.db, data_rule(chip, -400, "全体少拿"),
                         data_rule(chip, 3, "法术C照常拿", hero_groups=frozenset({"法术C"})),
                         data_rule(chip, 2, "本流派照常拿", own_school=True))
        db = with_data(db, HERO, data_rule(chip, -500, "角色少拿"))
        section = "\n".join(make_review.data_section(db))
        self.assertIn(f"| {chip} | -400 | 全体少拿 | 全部角色 |", section)
        self.assertIn(f"| {chip} | +3 | 法术C照常拿 | 只对法术C |", section)
        self.assertIn(f"| {chip} | +2 | 本流派照常拿 | 只在属于这个角色的推荐流派时 |", section)
        row = next(r for r in make_review.rows_for(db, HERO) if r[3].name == chip)
        self.assertTrue(row[4].startswith("大数据：角色少拿（-500）；大数据：全体少拿（-400）"), row[4])
        self.assertIn("大数据：法术C照常拿（+3）", row[4])  # 排不进前两项的大数据照样列
        self.assertEqual(make_review.rule_conditions(
            data_rule(chip, 1, hero_groups=frozenset({"奶辅", "法术C"}), own_school=True, max_round=6), {}),
            "〔第 6 轮及以前；只对法术C、奶辅；只在属于这个角色的推荐流派时〕")

    def test_review_lists(self):
        """审查清单（tools/make_review.py）：表里的理由前写「大数据：」，规则后面写生效条件。"""
        from tools import make_review
        chip, other = self.chips[0], self.chips[1]
        db = with_data(self.db, HERO, data_rule(chip, -500, "玩家少选"))
        row = next(r for r in make_review.rows_for(db, HERO) if r[3].name == chip)
        self.assertTrue(row[4].startswith("大数据：玩家少选（-500）"), row[4])
        rule = data_rule(chip, -20, needs_owned=frozenset({other}), max_round=6, difficulty="normal")
        self.assertEqual(make_review.rule_conditions(rule, {"normal": "普通"}),
                         f"〔已持有：{other}之一；第 6 轮及以前；只在普通难度〕")
        self.assertEqual(make_review.rule_conditions(data_rule(chip, -20), {}), "")


if __name__ == "__main__":
    unittest.main()
