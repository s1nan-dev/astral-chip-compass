"""资料写坏时的载入：降级成警告、跳过写坏的一项，不直接出错退出；推荐离不开的三份
整份读不出来时抛 DataError，写明文件名和位置；rules.toml 里引用的名字、要用到的键和会除零的取值载入时核对；名单以外的
稀有度、流派、职业记警告；地图清单取专属筹码和 maps.json 的并集；循环往复的筹码名读配置。

每条测试的注释写明不这样处理时会怎样（出错退出，或悄悄不记警告）。资料都复制到沙箱里的临时文件夹再改，不碰 data/。
"""
import dataclasses
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from advisor import paths
from advisor.data import DataError, load_db
from advisor.engine import default_rerolls

FILES = ("chips.json", "heroes.json", "rules.toml", "synergy.toml", "series.toml", "maps.json", "mutators.json", "meta.json")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def copy_data(self, **edits) -> Path:
        """把 data/ 的资料复制到沙箱的临时文件夹。edits：文件名里的点换成下划线（rules_toml）→ 新的全文，或改全文的函数。"""
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in FILES:
            src = paths.data_dir() / name
            if src.exists():
                shutil.copy(src, tmp / name)
        for key, edit in edits.items():
            path = tmp / key.replace("_", ".", 1)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(edit(text) if callable(edit) else edit, encoding="utf-8")
        return tmp

    def load(self, expect_log: bool = True, **edits):
        """按 edits 改过的资料载入。有警告时同时写日志（expect_log 为真时核对写了），内容看 DB.warnings。"""
        if not expect_log:
            return load_db(str(self.copy_data(**edits)))
        with self.assertLogs("advisor.data", level="WARNING"):
            return load_db(str(self.copy_data(**edits)))

    @staticmethod
    def edit_json(change):
        """改 JSON 的函数：读出、交给 change 改（就地改），再写回。"""
        def run(text: str) -> str:
            data = json.loads(text)
            change(data)
            return json.dumps(data, ensure_ascii=False, indent=1)
        return run

    @staticmethod
    def replace(old: str, new: str, count: int = 1):
        def run(text: str) -> str:
            assert old in text, old
            return text.replace(old, new, count)
        return run


class DegradeTest(Base):
    """写坏的一项记警告、跳过，其余照常载入。不这样处理时这几种都在 load_db 里抛出原始的异常（KeyError、AttributeError 等），
    悬浮窗启动失败。"""

    def test_bad_rules_skipped(self):
        # 规则缺 score、缺 chips：不抛 KeyError: 'score'、KeyError: 'chips'
        extra = ('\n[[trait]]\nname = "测试特性"\ndesc = "测试"\n'
                 '[[trait.rule]]\nchips = ["标靶"]\nreason = "缺分值"\n'
                 '[[trait.rule]]\nscore = 10\nreason = "缺筹码"\n'
                 '[[trait.rule]]\nchips = ["书签"]\nscore = 5\nreason = "写对的一条"\n')
        db = self.load(synergy_toml=lambda text: text + extra)
        self.assertEqual(db.problems, [])
        self.assertIn("特性 测试特性的第 1 条规则：score 应为数，已忽略", db.warnings)
        self.assertIn("特性 测试特性的第 2 条规则：chips 应为筹码名的列表，已忽略", db.warnings)
        self.assertEqual([sorted(r.chips) for r in db.trait_rules["测试特性"]], [["书签"]])
        self.assertEqual(len(db.hero_synergy), len(self.db.hero_synergy))

    def test_section_not_array(self):
        # [[trait]] 误写成 [trait]：不抛 AttributeError（不按表的键逐个当成特性）
        db = self.load(synergy_toml='[trait]\nname = "通用"\n')
        self.assertEqual(db.problems, [])
        self.assertIn("适配表：trait 应写成 [[trait]] 段落，已忽略", db.warnings)
        self.assertIn("适配表缺少「通用」特性", db.warnings)

    def test_player_tier_written_as_text(self):
        # 档位误写成字符串：不按字拆开（拆开会记三条看不出原因的警告：筹码「大」「背」「包」不存在），这一档忽略
        db = self.load(synergy_toml=lambda text: text + '\n[[player]]\nhero = "凛"\ncore = "大背包"\nfirst = ["标记喷罐"]\n')
        self.assertIn("玩家思路 凛「必选」档：应写成筹码名的列表，已忽略这一档", db.warnings)
        self.assertFalse([w for w in db.warnings if re.search("筹码「.」不存在", w)], db.warnings)
        self.assertEqual(db.player_synergy["凛"].tiers, {"标记喷罐": "first"})  # 写对的一档照常

    def test_combo_names_written_as_text(self):
        # owned 误写成字符串：不按字拆开、不载入这条组合规则（拆开会每个字记一条「不存在」）
        extra = '\n[[combo]]\nname = "测试组合"\nowned = "标靶"\nchips = ["书签"]\nscore = 5\n'
        db = self.load(synergy_toml=lambda text: text + extra)
        self.assertIn("组合规则「测试组合」：owned 的类型不对，已忽略", db.warnings)
        self.assertNotIn("测试组合", [c.name for c in db.combos])
        self.assertEqual(len(db.combos), len(self.db.combos))

    def test_maps_json_missing_tasks(self):
        # maps.json 缺 tasks：不抛 KeyError: 'tasks'，这张地图照常载入，按没有任务处理
        db = self.load(maps_json=self.edit_json(lambda data: data["魔法学院"].pop("tasks")))
        self.assertIn("maps.json 地图「魔法学院」：tasks 应为任务文字的列表，按没有任务处理", db.warnings)
        self.assertEqual(db.map_tasks["魔法学院"], ())
        self.assertEqual(db.map_info["魔法学院"].progress_cap, self.db.map_info["魔法学院"].progress_cap)
        self.assertEqual(db.maps, self.db.maps)

    def test_maps_json_bad_extreme(self):
        # maps.json 的 extreme（极限难度的进度上限和筹码任务数）写坏了：记警告，这张地图按没有极限资料处理，其余照常
        def edit(data):
            data["御魂庆典"]["extreme"] = {"progress_cap": "十七", "chip_tasks": 4}
            data["水乡古镇"]["extreme"] = [16, 5]
            data["魔法学院"]["extreme"] = {"chip_tasks": -1}
        db = self.load(maps_json=self.edit_json(edit))
        for name in ("御魂庆典", "水乡古镇", "魔法学院"):
            with self.subTest(map=name):
                self.assertIn(f"maps.json 地图「{name}」：extreme 的类型不对，按没有资料处理", db.warnings)
                self.assertEqual((db.map_info[name].extreme_cap, db.map_info[name].extreme_tasks), (None, None))
                self.assertEqual(db.map_info[name].tasks_for("极限"), self.db.map_info[name].chip_tasks)
        self.assertEqual(db.map_info["星趴·梦想号"].extreme_cap, 18)  # 写对的照常
        # 只写一项也行：没写的那一项按没有资料
        db = self.load(expect_log=False, maps_json=self.edit_json(lambda d: d["御魂庆典"].update(extreme={"chip_tasks": 4})))
        self.assertEqual((db.map_info["御魂庆典"].extreme_cap, db.map_info["御魂庆典"].extreme_tasks), (None, 4.0))
        self.assertEqual(db.map_info["御魂庆典"].cap_for(None, "极限"), 19)

    def test_optional_files_unreadable(self):
        # 可以没有的文件读不出：不抛 json.JSONDecodeError，当作没有这个文件
        db = self.load(mutators_json="{坏掉的", meta_json="[]")
        self.assertIn("mutators.json 格式错误（第 1 行第 2 列），已忽略", db.warnings)
        self.assertIn("meta.json 的最外层应为表，已忽略", db.warnings)
        self.assertEqual((db.mutators, db.meta), ({}, {}))

    def test_chip_missing_field(self):
        # 某个筹码缺字段：不抛 KeyError: 'base'，跳过这个筹码，其余照常
        def drop(data):
            next(c for c in data["chips"] if c["name"] == "会员推荐信").pop("base")
        db = self.load(chips_json=self.edit_json(drop))
        index = [c["name"] for c in json.loads((paths.data_dir() / "chips.json").read_text(encoding="utf-8"))["chips"]]
        self.assertIn(f"chips.json 第 {index.index('会员推荐信') + 1} 个筹码（「会员推荐信」）：缺少 base，已忽略", db.warnings)
        self.assertNotIn("会员推荐信", db.chips)
        self.assertEqual(len(db.chips), len(self.db.chips) - 1)

    def test_required_files_unreadable(self):
        # 推荐离不开的三份整份读不出来：抛 DataError，写明文件名和位置，不抛 json.JSONDecodeError、
        # tomllib.TOMLDecodeError（消息里没有文件名）和 AttributeError
        cases = (
            (dict(chips_json='{"chips": [}'), r"资料文件 chips\.json 格式错误（第 1 行第 \d+ 列）"),
            (dict(rules_toml="[picks\nend_round = 13\n"), r"资料文件 rules\.toml 格式错误：.*line 1"),
            (dict(heroes_json="{}"), r"资料文件 heroes\.json 的最外层应为列表"),
            (dict(chips_json='{"removed": []}'), r"资料文件 chips\.json 里没有筹码的列表"),
        )
        for edits, pattern in cases:
            with self.subTest(edits=list(edits)):
                with self.assertRaisesRegex(DataError, pattern):
                    load_db(str(self.copy_data(**edits)))


class RulesCheckTest(Base):
    """rules.toml 载入时核对。不核对的话名字改错、缺键、会除零的值载入时一条警告都没有：名字对不上的那一项悄悄不起作用，
    缺键、除零要到算推荐时才出错。随程序发布的资料没有警告（test_data.test_synergy_is_consistent）。"""

    def test_renamed_names_warned(self):
        edits = (
            ('chip = "循环往复"', 'chip = "循环往复X"'),
            ('knives = ["美工刀-初级", "美工刀-锋利"]', 'knives = ["美工刀-初级X", "美工刀-锋利"]'),
            ('combos = ["完备治疗与美工刀", "治愈来源与美工刀"]', 'combos = ["完备治疗与美工刀", "治愈来源与美工刀X"]'),
            ('trait = "需要生存"', 'trait = "需要生存X"'),
            ('[difficulty]', '[difficulty]\ncap_untrusted = ["园林中庭X"]'),
            ('school = "充能"', 'school = "雷电"'),
        )

        def rename(text: str) -> str:
            for old, new in edits:
                text = self.replace(old, new)(text)
            return text

        db = self.load(rules_toml=rename)
        self.assertEqual(db.warnings, [
            "rules.toml [endless_cycle] chip：筹码「循环往复X」不存在",
            "rules.toml [difficulty] cap_untrusted：地图「园林中庭X」不存在",
            "rules.toml [heal_knife] knives：筹码「美工刀-初级X」不存在",
            "rules.toml [heal_knife] combos：组合规则「治愈来源与美工刀X」不存在",
            "rules.toml [survival] trait：特性「需要生存X」不存在",
            "rules.toml [charge_build] school：应为流派名（星光、治愈、标记、充能）",
        ])

    def test_school_list_names_bad_entry(self):
        # 流派名的列表（[official] keyword_schools）写错一项：点名是哪一项（同筹码名、[[hero]] 的 schools）。只说
        # 「应为流派名（……）的列表」的话，列表长时看不出错在哪一项；不是名称的列表时才这样说
        old = 'keyword_schools = ["治愈", "星光", "标记"]'
        db = self.load(rules_toml=self.replace(old, 'keyword_schools = ["治愈", "星光X", "标记", "雷电"]'))
        self.assertEqual(db.warnings, ["rules.toml [official] keyword_schools：流派「星光X」「雷电」不存在"])
        db = self.load(rules_toml=self.replace(old, 'keyword_schools = "治愈"'))
        self.assertEqual(db.warnings, ["rules.toml [official] keyword_schools：应为流派名（星光、治愈、标记、充能）的列表"])

    def test_missing_key_and_zero_divisors(self):
        # 缺 [picks] end_round：载入时就报，不等到算推荐时才 KeyError
        db = self.load(rules_toml=lambda text: re.sub(r"(?m)^end_round = .*\n", "", text))
        self.assertEqual(db.warnings, ["rules.toml [picks]：缺少 end_round"])
        # 会除零的几项：end_round = 1、temperature = 0、keyword_boost = 0、reward_every = 0、某一行概率全是 0
        edits = (
            (r"(?m)^end_round = 13", "end_round = 1"), (r"(?m)^temperature = 25", "temperature = 0"),
            (r"(?m)^keyword_boost = 2\.0", "keyword_boost = 0"), (r"(?m)^reward_every = 3", "reward_every = 0"),
            (r"(?m)^rows = \[\[60, 37, 3\]", "rows = [[0, 0, 0]"),
        )

        def zero(text: str) -> str:
            for pattern, new in edits:
                self.assertRegex(text, pattern)
                text = re.sub(pattern, new, text, count=1)
            return text

        db = self.load(rules_toml=zero)
        self.assertEqual(sorted(db.warnings), sorted([
            "rules.toml [rarity] rows：应为 4 行、每行蓝 / 紫 / 金 3 个不小于 0 的数，每行合计大于 0",
            "rules.toml [pool] keyword_boost：应为大于 0 的数",
            "rules.toml [picks] end_round：应为大于 1 的数",
            "rules.toml [income] reward_every：应为正整数",
            "rules.toml [softmax] temperature：应为大于 0 的数",
        ]))

    def test_non_finite_numbers_warned(self):
        # TOML 认得 inf、nan，它们也是数：共用的数值（[synergy] keep_schools）、当除数的（temperature）、要大于 1 的
        # （end_round）、按星数的列表（[lookahead] picks）写成 inf、nan 时记警告，同角色的 keep_schools_penalty。不这样
        # 核对时载入一条警告都没有，算推荐时总分、推荐度成了 inf、nan
        edits = (
            (r"(?m)^keep_schools = -40", "keep_schools = -inf"), (r"(?m)^temperature = 25", "temperature = inf"),
            (r"(?m)^end_round = 13", "end_round = nan"), (r"(?m)^picks = \[2\.0, 2\.0", "picks = [2.0, inf"),
        )

        def broken(text: str) -> str:
            for pattern, new in edits:
                self.assertRegex(text, pattern)
                text = re.sub(pattern, new, text, count=1)
            return text

        db = self.load(rules_toml=broken)
        self.assertEqual(sorted(db.warnings), sorted([
            "rules.toml [synergy] keep_schools：应为有限的数（不能写 inf、nan）",
            "rules.toml [softmax] temperature：应为有限的数（不能写 inf、nan）",
            "rules.toml [picks] end_round：应为有限的数（不能写 inf、nan）",
            "rules.toml [lookahead] picks：应为有限的数（不能写 inf、nan）",
        ]))


class SourceCheckTest(Base):
    """rules.toml [sources]（体系来源）和适配表规则的 needs_source、without_source 写错时记警告：
    名字对不上时带条件的规则悄悄一直不生效（或一直生效），载入时就要报出来。"""

    def test_source_names_warned(self):
        def rules(text: str) -> str:
            old = '"充能" = { chips = ["永动机", "能量回收", "电流剑", "电击手套", "跃迁引擎"] }'
            return self.replace(old, '"充能" = { chips = ["永动机", "能量回收", "电流剑", "电击手套", '
                                      '"跃迁引擎", "永动机X"], heroes = ["凛", "邦妮X"] }')(text)

        db = self.load(rules_toml=rules)
        self.assertEqual(db.warnings, [
            "rules.toml [sources] 充能 chips：筹码「永动机X」不存在",
            "rules.toml [sources] 充能 heroes：角色「邦妮X」不存在",
        ])
        self.assertIn("永动机X", db.sources["充能"].chips)  # 照常载入：对不上的名字永远不算来源
        self.assertEqual(db.sources["充能"].heroes, frozenset({"凛", "邦妮X"}))

    def test_bad_source_tables_warned(self):
        def rules(text: str) -> str:
            old = '"充能" = { chips = ["永动机", "能量回收", "电流剑", "电击手套", "跃迁引擎"] }'
            text = self.replace(old, '"充能" = { chips = "永动机", traits = ["技能依赖"] }\n"空的" = {}\n"文字" = "永动机"')(text)
            return text

        db = self.load(rules_toml=rules)
        self.assertEqual(db.warnings, [
            "rules.toml [sources] 充能：只能写 chips、heroes，「traits」已忽略",
            "rules.toml [sources] 充能 chips：应为筹码名的列表，按没写处理",
            "rules.toml [sources] 充能：chips、heroes 都没写，按一直没有这种来源计算",
            "rules.toml [sources] 空的：chips、heroes 都没写，按一直没有这种来源计算",
            "rules.toml [sources] 文字：应写成一张表（chips、heroes），已忽略",
        ])
        self.assertEqual(db.sources["充能"].chips, frozenset())  # 一直没有：带 needs_source = "充能" 的规则都不生效
        self.assertNotIn("文字", db.sources)

    def test_rule_source_names_warned(self):
        # 规则引用的来源名不在 [sources] 里：needs_source 的那条永远不生效，without_source 的那条永远生效
        # （规则里另外加几条自己造的，试这两种写法）
        def syn(text: str) -> str:
            old = 'chips = ["电流核心"]\nneeds_source = "充能"'
            text = self.replace(old, 'chips = ["电流核心"]\nneeds_source = "充能X"')(text)
            return text + ('\n[[trait]]\nname = "测试特性"\ndesc = "测试"\n'
                           '[[trait.rule]]\nchips = ["标靶"]\nscore = 10\nneeds_source = 1\n'
                           '[[trait.rule]]\nchips = ["书签"]\nscore = 5\nneeds_source = "甲"\nwithout_source = "甲"\n'
                           '[[trait.rule]]\nchips = ["大背包"]\nscore = 5\nwithout_source = "缺"\n')

        db = self.load(synergy_toml=syn)
        self.assertEqual(db.warnings, [
            "特性 技能依赖 的规则（电流核心）：needs_source 写的来源「充能X」在 rules.toml [sources] 里没有，这条规则不会生效",
            "特性 测试特性的第 1 条规则：needs_source 应为来源名（rules.toml [sources] 里的名字），已忽略",
            "特性 测试特性的第 2 条规则：needs_source 和 without_source 写了同一个来源，这条规则永远不会生效，已忽略",
            "特性 测试特性 的规则（大背包）：without_source 写的来源「缺」在 rules.toml [sources] 里没有，按一直没有这种来源计算",
        ])
        # 引用不到名字的规则照常载入：without_source 的那条按「一直没有这种来源」算，needs_source 的只会永远不生效
        self.assertEqual([(sorted(r.chips), r.score, r.needs_source, r.without_source)
                          for r in db.trait_rules["测试特性"]], [(["大背包"], 5.0, None, "缺")])

    def test_no_sources_section(self):
        # 整节没写：带 needs_source 的规则都不生效、带 without_source 的都生效，每条引用记一条警告
        db = self.load(rules_toml=lambda text: re.sub(r'(?ms)^\[sources\]\n.*?(?=^\[lookahead\])', "", text))
        self.assertEqual(db.sources, {})
        refs = sum(1 for rules in self.db.trait_rules.values() for r in rules for k in (r.needs_source, r.without_source) if k)
        refs += sum(1 for hs in self.db.hero_synergy.values() for r in hs.rules
                    if r.origin == "角色" for k in (r.needs_source, r.without_source) if k)
        self.assertGreaterEqual(refs, 3)  # 现在至少有充能那三条（电流核心、电磁炮有来源时加分，电磁炮没有来源时扣分）
        self.assertEqual(len([w for w in db.warnings if "在 rules.toml [sources] 里没有" in w]), refs)


class NameListTest(Base):
    """资料里出现名单（稀有度、流派、职业）以外的值：记警告，照常载入。不记警告的话，按名单分桶、选池的地方会悄悄漏掉它。"""

    def test_unknown_values_warned(self):
        def chips(data):
            by_name = {c["name"]: c for c in data["chips"]}
            by_name["书签"]["rarity"] = "绿"
            by_name["标靶"]["school"] = "火焰"
            by_name["会员推荐信"]["class_points"]["法师"] = 10
            by_name["会员推荐信"]["pools"].append("法师")

        def heroes(data):
            next(h for h in data if h["name"] == "凛")["weights"]["法师"] = 10

        db = self.load(chips_json=self.edit_json(chips), heroes_json=self.edit_json(heroes))
        for text in ("chips.json 筹码「书签」：稀有度「绿」不在名单里", "chips.json 筹码「标靶」：流派「火焰」不在名单里",
                     "chips.json 筹码「会员推荐信」：职业筹码池「法师」、职业分值「法师」不在名单里",
                     "heroes.json 角色「凛」：职业分值「法师」不在名单里"):
            self.assertIn(text, db.warnings)
        self.assertEqual(db.chips["书签"].rarity, "绿")  # 照常载入


class MapListTest(Base):
    def test_maps_from_both_sources(self):
        # maps.json 里有、却没有专属筹码的地图：也在 DB.maps 里，参加「按候选地图平均」，命令行也认得出
        def add(data):
            data["测试新地图"] = {"tasks": ["击倒 3 只测试怪物"], "progress_cap": 16, "chip_tasks": 3,
                              "progress_caps": {"普通": 18, "困难": 18, "噩梦": 16, "疯狂": 16}}
        db = self.load(expect_log=False, maps_json=self.edit_json(add))
        self.assertEqual(db.warnings, [])
        self.assertEqual(db.maps, (*self.db.maps, "测试新地图"))  # 原有的顺序不变，接在后面
        self.assertEqual(db.resolve_map("测试新地图"), "测试新地图")
        self.assertIn("测试新地图", db.active_maps())


class EndlessCycleNameTest(Base):
    def test_default_rerolls_reads_config(self):
        # 循环往复的筹码名按 rules.toml [endless_cycle] chip 认：不写死「循环往复」，免得改名后漏改、少算 3 次刷新
        db = self.db
        self.assertEqual(default_rerolls(db, 1, ("循环往复",)), default_rerolls(db, 1) + db.rules["reroll"]["endless_cycle"])
        renamed = dataclasses.replace(db, rules={**db.rules, "endless_cycle": {**db.rules["endless_cycle"], "chip": "书签"}})
        self.assertEqual(default_rerolls(renamed, 0, ("书签",)), 2 + db.rules["reroll"]["endless_cycle"])
        self.assertEqual(default_rerolls(renamed, 0, ("循环往复",)), 2)


if __name__ == "__main__":
    unittest.main()
