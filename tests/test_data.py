import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from advisor import paths
from advisor.data import (
    CLASSES, DIFFICULTIES, NORMAL, RARITIES, SCHOOLS, SOURCE_GUIDE, TIER_LABEL, TIERS, load_db, task_key,
)


def _data(name: str):
    """data/ 下某个 JSON 文件的内容（核对载入结果和文件本身一致）。"""
    return json.loads((paths.data_dir() / name).read_text(encoding="utf-8"))


class DataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_counts(self):
        """载入的筹码、角色数和 data 里的 JSON 一致（没有因为字段写错被跳过、记警告）；分组、发放筹码也一致。"""
        self.assertEqual(self.db.warnings, [])
        chips, heroes = _data("chips.json")["chips"], _data("heroes.json")
        self.assertEqual(len(self.db.chips), len(chips))
        self.assertEqual(len(self.db.heroes), len(heroes))
        self.assertEqual({c.name for c in self.db.chips.values() if c.granted},
                         {c["name"] for c in chips if c["granted"]})
        by_group: dict[str, int] = {}
        for c in chips:
            by_group[c["group"]] = by_group.get(c["group"], 0) + 1
        loaded: dict[str, int] = {}
        for c in self.db.chips.values():
            loaded[c.group] = loaded.get(c.group, 0) + 1
        self.assertEqual(loaded, by_group)

    def test_fields(self):
        for c in self.db.chips.values():
            self.assertIn(c.rarity, RARITIES, c.name)
            self.assertTrue(c.tags, c.name)
            self.assertTrue(set(c.pools) <= set(CLASSES), c.name)
            self.assertEqual(c.school in SCHOOLS, c.group in SCHOOLS, c.name)
            self.assertEqual(bool(c.maps), c.group == "地图专属", c.name)
            self.assertTrue((c.fit | c.fit_minor) <= set(self.db.heroes), c.name)
        for h in self.db.heroes.values():
            self.assertEqual(set(h.weights), set(CLASSES), h.name)

    def test_spot_checks(self):
        """抽查几个筹码，和 data/chips.json 逐项对。"""
        chips = self.db.chips
        c = chips["银行卡-余额多"]
        self.assertEqual((c.rarity, c.school, c.base, c.growth_round), ("紫", "星光", 50, 2))
        self.assertEqual(c.fit, {"阿尔"})

        c = chips["拳击手套-高级"]
        self.assertEqual((c.rarity, c.base, c.class_points), ("金", 60, {"攻击": 30}))
        self.assertEqual(len(c.fit), 17)
        self.assertIn("邦妮", c.fit)
        self.assertIn("远野汉娜", c.fit)

        c = chips["电磁炮"]
        self.assertEqual(c.fit, {"玲玲"})
        self.assertEqual(c.fit_minor, {"橘雪莉"})

        c = chips["大铜锣"]
        self.assertEqual(c.maps, ("水乡古镇",))
        self.assertEqual(c.fit_minor, {"帕露南", "璐璐", "星魅琉华", "多萝西"})
        self.assertFalse(c.fit)

        c = chips["运动手环"]
        self.assertEqual(len(c.maps), 3)

        c = chips["会员推荐信"]
        self.assertEqual((c.base, c.tags, c.class_points), (10, ("卡牌",), {"卡牌": 30}))

        self.assertTrue(chips["交错双鲤"].granted)
        self.assertEqual(set(chips["缓冲盾牌"].pools), {"生存", "辅助"})

    def test_class_pools_only_from_table(self):
        """资料里的池表只用 data/chips.json 的 pools 字段，不按标签补全（wiki 池表收的地图专属筹码只到前几张图，
        之后几张图带职业标签的几枚按标签补进了池表；通用筹码仍然不进）。"""
        chips = self.db.chips
        self.assertEqual(chips["手持风扇-大"].pools, ("卡牌",))
        self.assertEqual(set(chips["小猪存钱罐"].pools), {"卡牌", "辅助"})
        for name in ("电击手套", "磨刀石", "电磁炮", "紫色飞星"):  # 带职业标签，但池表里没有
            with self.subTest(chip=name):
                self.assertTrue(set(chips[name].tags) & set(CLASSES))
                self.assertEqual(chips[name].pools, ())
        for name, pool in (("节日红包", "辅助"), ("孔雀扇", "攻击"), ("运动手环", "攻击"), ("娱乐终端", "攻击"),
                           ("放大镜", "攻击"), ("汉娜人偶", "辅助")):  # 地图专属，按职业标签进了池表
            with self.subTest(chip=name):
                self.assertEqual(chips[name].pools, (pool,))
        # 池表一共 12 格（4 个池 × 蓝紫金），列了 67 个位置、56 个不同的筹码（11 个同时在两个池里）
        in_pools = [c for c in chips.values() if c.pools]
        self.assertEqual(len(in_pools), 56)
        self.assertEqual(sum(len(c.pools) for c in in_pools), 67)

    def test_heroes(self):
        h = self.db.heroes["邦妮"]
        self.assertEqual((h.id, h.atk, h.dfn, h.hp, h.group), (127, 2, 1, 9, "辅C"))
        self.assertEqual(h.weights, {"攻击": 100, "卡牌": 15, "辅助": 15, "生存": 15})
        self.assertTrue(self.db.heroes["帕帕拉"].potential)
        self.assertFalse(self.db.heroes["邦妮"].potential)

    def test_synergy_is_consistent(self):
        self.assertEqual(self.db.problems, [])
        self.assertEqual(self.db.warnings, [])  # 资料对不上时只记警告、降级运行；随程序发布的数据不应有警告
        self.assertEqual(set(self.db.hero_synergy), set(self.db.heroes))
        # 默认推荐按角色自动层（特性、推荐流派、角色专属规则）计算，适配表里没有角色写玩家思路层（四档、带条件的规则）；
        # 这一层的机制用测试用的档位测（tests/support/player_layer.py）
        self.assertEqual(self.db.player_synergy, {})

    def test_tier_names(self):
        """四档在界面上的叫法和筹码方案的档位同一套：必选 / 推荐 / 可选 / 不选（推荐卡片上再写「玩家思路」，
        会被当成另一个概念），内部的档位键不变。玩家思路里会显示出来的文字（原文、规则的理由和短句）不写旧叫法
        「必拿 / 首选 / 次选 / 不推荐」和「玩家思路」，规则的理由和短句以「筹码方案：」开头：适配表里写了的和测试用的档位
        都照这个写。"""
        from tests.support.player_layer import with_player_layer

        self.assertEqual(TIERS, ("core", "first", "second", "avoid"))
        self.assertEqual(TIER_LABEL, {"core": "必选", "first": "推荐", "second": "可选", "avoid": "不选"})
        db = with_player_layer(self.db)
        self.assertIn(SOURCE_GUIDE, db.player_synergy["凛"].sources)
        old = re.compile("必拿|首选|次选|不推荐|玩家思路")
        rules = [r for ps in db.player_synergy.values() for r in ps.rules]
        texts = [ps.note for ps in db.player_synergy.values()] + [t for r in rules for t in (r.reason, r.short)]
        self.assertGreater(len(rules), 10)
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(old.search(text))
        for r in rules:
            with self.subTest(reason=r.reason):
                self.assertTrue(r.reason.startswith("筹码方案：") and r.short.startswith("筹码方案："), (r.reason, r.short))
        # 原文里的档位：凛「必选：……；推荐：……；可选：……」，芬妮「必选：……」
        self.assertIn("必选：忍术飞镖、魔法箭袋、大背包；推荐：标记喷罐；可选：手持风扇-小、手持风扇-大",
                      db.player_synergy["凛"].note)
        self.assertIn("必选：银行卡-用不完（金色银行卡）、紫色飞星、金色飞星", db.player_synergy["芬妮"].note)
        # 当动词用的「不推荐」也换掉：照的电磁炮「一般不选」
        reasons = {r.reason for r in rules}
        self.assertIn("筹码方案：没有充能来源时电磁炮难以触发，一般不选", reasons)

    def test_trait_shorts(self):
        """每个特性（通用规则除外）都写了 [[trait]] 的 short：理由标签「另符合：X等」里的短说明。
        拼成「另符合：X等」不超过 engine.SHORT_LIMIT 个字；末尾不写数值，不加「」，不出现特性的内部名。"""
        from advisor.data import GENERAL_TRAIT
        from advisor.engine import SHORT_LIMIT

        db = self.db
        self.assertEqual(set(db.trait_shorts), set(db.traits) - {GENERAL_TRAIT})
        self.assertEqual(
            {t: db.trait_shorts[t] for t in ("抽牌多", "反击", "需要生存", "不靠战斗")},
            {"抽牌多": "获得卡牌较多", "反击": "技能可带来反击", "需要生存": "生存能力偏低", "不靠战斗": "较少挑战怪物"},
        )
        # 特性的内部名：资料里的理由和筹码效果都用不到的特性名（「反击」「闪避」「转账」也是游戏里的用语，不算内部名）
        texts = [r.reason for rules in db.trait_rules.values() for r in rules]
        texts += [r.reason for hs in db.hero_synergy.values() for r in hs.rules]
        texts += [r.reason for ps in db.player_synergy.values() for r in ps.rules]
        texts += [c.reason for c in db.combos] + [t.reason for t in db.teammate_rules]
        texts += [c.effect for c in db.chips.values()]
        internal = [t for t in db.traits if t != GENERAL_TRAIT and not any(t in x for x in texts)]
        self.assertTrue({"抽牌多", "需要生存", "不靠战斗"} <= set(internal), internal)
        for trait, text in db.trait_shorts.items():
            with self.subTest(trait=trait, short=text):
                self.assertLessEqual(len(f"另符合：{text}等"), SHORT_LIMIT)
                self.assertNotRegex(text, r"\d$")
                self.assertNotRegex(text, "[「」]")
                for name in internal:
                    self.assertNotIn(name, text)

    def test_income_names_exist(self):
        """rules.toml [income] 里写的筹码名、特性名都存在：载入时不校验这几项，写错了只是不起作用、也不记警告
        （例如 exempt_owned 的「金色飞星」写错，持有金色飞星时就不再减半）。"""
        cfg = self.db.rules["income"]
        for key in ("starlight", "coins", "exempt_owned"):
            with self.subTest(key=key):
                self.assertTrue(cfg[key])
                self.assertEqual([n for n in cfg[key] if n not in self.db.chips], [])
        self.assertEqual([t for t in cfg["exempt_traits"] if t not in self.db.traits], [])
        self.assertIn(cfg["economy_trait"], self.db.traits)
        self.assertTrue(all(isinstance(v, str) and v for v in cfg["exempt_owned"].values()))

    def test_al_passive_six_layers(self):
        """阿尔的被动「双赢」：每拥有 6 层星光攻击力和防御力 +1（合作挑战 2026-04-17 平衡调整）。
        技能说明以机制文档 mechanics/characters.md 为准（heroes.json 不存技能说明文字），照游戏原文写「每拥有」；
        星光不衰减，推荐理由写「每累计」「每拥有」都对。"""
        text = (Path(__file__).resolve().parents[1] / "mechanics" / "characters.md").read_text(encoding="utf-8")
        self.assertIn("每拥有 6 层星光", text)
        self.assertNotRegex(text, "每(累计|拥有) ?7 ?层")
        self.assertRegex(self.db.hero_synergy["阿尔"].why, "每(累计|拥有) 6 层星光")
        rules = (Path(__file__).resolve().parents[1] / "data" / "rules.toml").read_text(encoding="utf-8")
        self.assertNotIn("每 7 层星光", rules)

    def test_meta(self):
        """data/meta.json 只写一个资料同步日期 data_date（合法日期），关于窗口显示的日期取它。"""
        meta = _data("meta.json")
        self.assertEqual(list(meta), ["data_date"])
        self.assertEqual(meta["data_date"], "2026-11-12")
        from datetime import date
        date.fromisoformat(meta["data_date"])
        self.assertEqual(self.db.meta, meta)
        from advisor import version
        self.assertEqual(version.data_date(), meta["data_date"])

    def test_map_info(self):
        info = self.db.map_info
        self.assertEqual(set(info), set(self.db.maps))
        # 最高难度（疯狂）的进度上限，见 data/maps.json 的 progress_caps
        self.assertEqual(info["星趴·梦想号"].progress_cap, 18)
        self.assertEqual(info["御魂庆典"].progress_cap, 19)
        self.assertEqual(info["幽魂暗巷"].progress_cap, 16)
        # 四个难度的进度上限（普通 / 困难 / 噩梦 / 疯狂，「—」为 None）；推荐时普通难度用普通一列，
        # 知道具体难度时用那一列，只知道困难及以上时用疯狂一列
        self.assertEqual(info["星趴·梦想号"].progress_caps, {"普通": 20, "困难": 20, "噩梦": 20, "疯狂": 18})
        self.assertEqual(info["幽魂暗巷"].progress_caps, {"普通": 18, "困难": 18, "噩梦": 17, "疯狂": 16})
        self.assertEqual(info["园林中庭"].progress_caps, {"普通": 18, "困难": 18, "噩梦": 17, "疯狂": 17})
        self.assertEqual(info["森秋幻境"].progress_caps, {"普通": 18, "困难": 18, "噩梦": 17, "疯狂": 17})
        self.assertEqual(info["御魂庆典"].progress_caps["疯狂"], 19)  # 括号里的「极限 17」不算
        for name, m in info.items():
            with self.subTest(map=name):
                self.assertEqual(tuple(m.progress_caps), DIFFICULTIES)
                self.assertEqual(m.progress_caps["疯狂"], m.progress_cap)
                self.assertEqual(m.cap_for(None), m.progress_cap)
                self.assertEqual(m.cap_for(NORMAL), m.progress_caps["普通"])
        self.assertEqual((info["水乡古镇"].cap_for(NORMAL), info["水乡古镇"].cap_for(None)), (20, 16))
        self.assertEqual([info["水乡古镇"].cap_for(None, d) for d in ("普通", "困难", "噩梦", "疯狂", "极限")],
                         [20, 18, 17, 16, 16])
        # 极限难度（只有这三张地图有，照游戏内核对）：进度上限和筹码任务数另记（MapInfo.extreme_cap、extreme_tasks）
        extreme = {n: (m.extreme_cap, m.extreme_tasks) for n, m in info.items() if m.extreme_cap or m.extreme_tasks}
        self.assertEqual(extreme, {"星趴·梦想号": (18, 3.0), "御魂庆典": (17, 4.0), "水乡古镇": (16, 5.0)})
        self.assertEqual(info["御魂庆典"].cap_for(None, "极限"), 17)
        self.assertEqual(info["魔法学院"].cap_for(None, "极限"), 16)  # 没有极限难度：照不知道具体难度，取疯狂一列
        # 一局的筹码任务数：普通到疯狂的（chip_tasks，极限才有的任务不算；有路线分支时取平均），知道是极限时才用极限的
        self.assertEqual(info["御魂庆典"].chip_tasks, 3)
        self.assertEqual(info["水乡古镇"].chip_tasks, 3)
        self.assertEqual([info["御魂庆典"].tasks_for(d) for d in (None, "困难", "疯狂", "极限")], [3, 3, 3, 4])
        self.assertEqual(info["魔法学院"].tasks_for("极限"), 3)
        self.assertEqual(info["魔法学院"].chip_tasks, 3)
        self.assertEqual(info["龙宫游乐园"].chip_tasks, 4)  # 通用 2 + 一条路线 2（收集葫芦直接发放筹码，不算）
        self.assertEqual(info["园林中庭"].chip_tasks, 4)
        self.assertEqual(info["幽魂暗巷"].chip_tasks, 4)  # 首次获得星星只给口香糖
        # 任务等级（task_levels）：一局各等级占多少，路线不知道时按各条线平均；没写的难度按困难那一列，整张图没写时为 None
        self.assertEqual(info["森秋幻境"].task_level_shares("困难"), ((1, 0.25), (2, 0.5), (3, 0.25)))
        self.assertEqual(info["森秋幻境"].task_level_shares(None), ((1, 0.25), (2, 0.5), (3, 0.25)))
        self.assertEqual(info["水乡古镇"].task_level_shares("普通"), ((1, 1 / 3), (2, 2 / 3)))
        self.assertEqual(info["水乡古镇"].task_level_shares("噩梦"), ((1, 1 / 3), (2, 1 / 3), (3, 1 / 3)))
        self.assertEqual(info["御魂庆典"].task_level_shares("极限"), ((1, 1 / 3), (2, 1 / 3), (3, 1 / 3)))  # 极限没写，按困难
        self.assertEqual(info["魔法学院"].task_level_shares("噩梦"), ((1, 1 / 6), (2, 2 / 3), (3, 1 / 6)))
        got = info["园林中庭"].task_level_shares("普通")
        for level, want in ((1, 0.25), (2, 5 / 12), (3, 1 / 3)):
            self.assertAlmostEqual(dict(got)[level], want)
        self.assertIsNone(info["海选赛运动场"].task_level_shares("普通"))  # 已结束的运动场没写
        # 限时地图：运动场系列已结束，异变图书馆到 10-14
        for m in ("海选赛运动场", "淘汰赛运动场", "决赛大赛场"):
            self.assertTrue(info[m].ended, m)
        self.assertEqual(info["异变图书馆"].until, "2026-10-14")
        self.assertFalse(info["水乡古镇"].limited)
        from datetime import date

        self.assertNotIn("海选赛运动场", self.db.active_maps(date(2026, 10, 2)))
        self.assertIn("异变图书馆", self.db.active_maps(date(2026, 10, 2)))
        self.assertNotIn("异变图书馆", self.db.active_maps(date(2026, 10, 15)))

    def test_mutators(self):
        m = self.db.mutators
        self.assertEqual(m["暴雨"].scope, "通用")
        self.assertIn("与美工刀冲突", m["暴雨"].hint)
        self.assertEqual(m["狂风"].hint, m["过载模式"].hint)  # 「过载模式」「狂风」共用一条提示
        self.assertEqual(m["危机四伏"].scope, "星趴·梦想号")
        self.assertEqual(m["院长的茶会"].scope, "海选赛运动场")
        self.assertNotIn("智械冲击（Rise of the Robots）", m)  # 组件带来的地图事件不是组件
        self.assertEqual(m["雷云"].hint, "")

    def copy_data(self, synergy_text: str) -> Path:
        """把 data/ 复制到程序目录的临时文件夹里，换上给定的 synergy.toml。"""
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in ("chips.json", "heroes.json", "rules.toml", "maps.json", "mutators.json", "meta.json"):
            shutil.copy(paths.data_dir() / name, tmp / name)
        (tmp / "synergy.toml").write_text(synergy_text, encoding="utf-8")
        return tmp

    def test_degraded_loading(self):
        """适配表缺角色、引用不存在的筹码时照常加载，记警告；格式错误时作为严重错误报告。"""
        text = (paths.data_dir() / "synergy.toml").read_text(encoding="utf-8")  # 换行统一读成 \n
        start = text.index('[[hero]]\nname = "邦妮"')
        end = text.index("[[hero]]", start + 5)
        broken = text[:start] + text[end:] + '\n[[combo]]\nname = "x"\nowned = ["不存在的筹码"]\nchips = ["标靶"]\nscore = 5\n'
        with self.assertLogs("advisor.data", level="WARNING"):  # 警告同时写日志
            db = load_db(str(self.copy_data(broken)))
        self.assertEqual(db.problems, [])
        self.assertIn("角色自动层：缺少角色「邦妮」，该角色只按通用规则计算", db.warnings)
        self.assertIn("组合规则「x」：筹码「不存在的筹码」不存在", db.warnings)
        self.assertNotIn("邦妮", db.hero_synergy)
        db = load_db(str(self.copy_data("[[trait]\nname = ")))
        self.assertEqual(len(db.problems), 1)
        self.assertTrue(db.problems[0].startswith("格式错误，无法读取"))
        self.assertEqual(len(db.chips), len(self.db.chips))

    def test_keep_schools_flag(self):
        """[[hero]] 的 keep_schools（推荐流派都要尽量保住）：南希露写了 true，没写的角色为 false；写流派名的列表时只保
        列出的几个（要在 schools 里，重复的只算一次）；keep_schools_penalty 是这个角色的扣分（负数），没写时为 None
        （按 rules.toml [synergy] keep_schools）。写得不对时记警告：不是 true、false 或流派名的列表时按 false 处理，
        列表里不在 schools 里的流派去掉（一个都不剩时按 false 处理），扣分不是负数时按 [synergy] keep_schools 计算，
        没有要保住的流派却写了扣分时这一项不起作用。"""
        nancy = self.db.hero_synergy["南希露"]
        self.assertIs(nancy.keep_schools, True)
        self.assertEqual(nancy.schools, ("标记", "治愈"))
        self.assertIsNone(nancy.keep_schools_penalty)
        text = (paths.data_dir() / "synergy.toml").read_text(encoding="utf-8")
        start = text.index('[[hero]]\nname = "南希露"')
        at = text.index("\nkeep_schools = true\n", start)
        line = "\nkeep_schools = true\n"
        cases = (  # （南希露这一段换成的写法, 要保住的流派, 扣分, 警告）
            ('keep_schools = "是"', False, None, ["角色 南希露：keep_schools 应为 true、false 或流派名的列表，已按 false 处理"]),
            ("keep_schools = [1, 2]", False, None,
             ["角色 南希露：keep_schools 应为 true、false 或流派名的列表，已按 false 处理"]),
            ('keep_schools = ["治愈", "治愈"]\nkeep_schools_penalty = -60', ("治愈",), -60.0, []),
            ('keep_schools = ["标记", "星光"]', ("标记",), None,
             ["角色 南希露：keep_schools 里的流派「星光」不在 schools（推荐流派）里，已忽略"]),
            ('keep_schools = ["充能"]', False, None,
             ["角色 南希露：keep_schools 里的流派「充能」不在 schools（推荐流派）里，已忽略"]),
            ("keep_schools = true\nkeep_schools_penalty = 60", True, None,
             ["角色 南希露：keep_schools_penalty 应为负数，已按所有角色共用的扣分计算"]),
            ('keep_schools = true\nkeep_schools_penalty = "-60"', True, None,
             ["角色 南希露：keep_schools_penalty 应为负数，已按所有角色共用的扣分计算"]),
            # TOML 的 nan、-inf 也是数：nan 不是负数，-inf 会让总分算不出来，都按写错处理
            ("keep_schools = true\nkeep_schools_penalty = nan", True, None,
             ["角色 南希露：keep_schools_penalty 应为负数，已按所有角色共用的扣分计算"]),
            ("keep_schools = true\nkeep_schools_penalty = -inf", True, None,
             ["角色 南希露：keep_schools_penalty 应为有限的负数（不能写 -inf），已按所有角色共用的扣分计算"]),
            ("keep_schools = false\nkeep_schools_penalty = -60", False, -60.0,
             ["角色 南希露：写了 keep_schools_penalty，没有要保住的推荐流派（keep_schools），这一项不起作用"]),
        )
        for written, keep, penalty, warnings in cases:
            with self.subTest(written=written):
                edited = text[:at] + "\n" + written + "\n" + text[at + len(line):]
                if warnings:
                    with self.assertLogs("advisor.data", level="WARNING"):
                        db = load_db(str(self.copy_data(edited)))
                else:
                    db = load_db(str(self.copy_data(edited)))
                hs = db.hero_synergy["南希露"]
                self.assertEqual((hs.keep_schools, hs.keep_schools_penalty), (keep, penalty))
                self.assertEqual(db.warnings, warnings)

    def test_school_skip(self):
        """[[hero]] 的 school_skip（推荐流派里这几张筹码不加「推荐流派」这一笔分）：要写成筹码名的列表，名单里的筹码
        要在推荐流派里，重复的只算一次。写成别的值时按没写处理；不是筹码名、不属于任何流派、流派不在推荐流派里的
        记警告、去掉。没写的角色为空。"""
        self.assertEqual(self.db.hero_synergy["邦妮"].school_skip, ())
        text = (paths.data_dir() / "synergy.toml").read_text(encoding="utf-8")
        start = text.index('[[hero]]\nname = "南希露"')
        # 南希露在资料里已有一行 school_skip：先去掉，再换成下面各种写法
        own = text.index("\nschool_skip = ", start)
        self.assertLess(own, text.index("\n[[", start + 1))
        text = text[:own] + text[text.index("\n", own + 1):]
        at = text.index("\nkeep_schools = true\n", start)
        line = "\nkeep_schools = true\n"
        cases = (  # （school_skip 的写法, 整理后的名单, 警告）
            ('school_skip = ["标记喷罐", "魔法箭袋"]', ("标记喷罐", "魔法箭袋"), []),
            ('school_skip = ["标记喷罐", "标记喷罐"]', ("标记喷罐",), []),
            ('school_skip = ["标记喷罐", "星币锤"]', ("标记喷罐",),
             ["角色 南希露：school_skip 里的筹码「星币锤」的流派「星光」不在 schools（推荐流派）里，已忽略"]),
            ('school_skip = "标记喷罐"', (), ["角色 南希露：school_skip 应为筹码名的列表，已忽略"]),
            ("school_skip = [1]", (), ["角色 南希露：school_skip 应为筹码名的列表，已忽略"]),
            ('school_skip = ["不存在的筹码"]', (),
             ["角色 南希露：school_skip 里的筹码「不存在的筹码」不存在，已忽略"]),
            ('school_skip = ["拳击手套-高级"]', (),
             ["角色 南希露：school_skip 里的筹码「拳击手套-高级」不属于任何流派，已忽略"]),
        )
        for written, skip, warnings in cases:
            with self.subTest(written=written):
                edited = text[:at] + "\n" + written + "\n" + text[at + len(line):]
                if warnings:
                    with self.assertLogs("advisor.data", level="WARNING"):
                        db = load_db(str(self.copy_data(edited)))
                else:
                    db = load_db(str(self.copy_data(edited)))
                self.assertEqual(db.hero_synergy["南希露"].school_skip, skip)
                self.assertEqual(db.warnings, warnings)

    def test_name_matching(self):
        db = self.db
        self.assertEqual(db.resolve_chip("8面骰子").name, "“8面”骰子")
        self.assertEqual(db.resolve_chip("友情徽章").name, "友情徽章")
        self.assertEqual(db.resolve_chip("拳击手套—高级").name, "拳击手套-高级")
        self.assertEqual(db.resolve_hero("汉娜").name, "远野汉娜")
        self.assertEqual(db.resolve_hero("Bonnie").name, "邦妮")
        self.assertEqual(db.resolve_map("梦想号"), "星趴·梦想号")
        with self.assertRaises(KeyError):
            db.resolve_chip("完全不存在的东西")

    def test_task_key_dashes(self):
        """认任务名时几种连接号当成同一个，和认筹码、角色、地图名一样：资料照游戏写「—」，任务栏读成「-」「－」「–」
        都对得上。"""
        tasks = self.db.map_tasks["幽魂暗巷"]
        for read in ("击倒人工生命体—天崩", "击倒人工生命体-天崩", "击倒人工生命体－天崩", "击倒人工生命体–天崩"):
            with self.subTest(read=read):
                self.assertIn(task_key(read), tasks)
        self.assertEqual(task_key("击倒 3 只圆锯猴助手"), task_key("击倒圆锯猴助手"))

    def test_chip_aliases(self):
        """筹码的 aliases 是改名前的旧名：认名字时一并当成本筹码的名字，方案文件、分享码里的旧名另由
        data/chip_codes.json 的 old_names 认。"""
        db = self.db
        self.assertEqual(db.chips["飞羽手环"].aliases, ("彩羽手环",))
        self.assertEqual(db.resolve_chip("彩羽手环").name, "飞羽手环")
        self.assertEqual(db.match_chip("彩羽手环")[:2], ("飞羽手环", 1.0))
        self.assertEqual(db.chips["大背包"].aliases, ())

    def test_hant_names(self):
        """游戏繁体中文界面里的名字也认：筹码名、角色名、称号、全名、地图名都用简体名做键返回。"""
        db = self.db
        self.assertEqual(db.resolve_chip("拳擊手套-高級").name, "拳击手套-高级")
        self.assertEqual(db.resolve_chip("銀行卡-餘額多").name, "银行卡-余额多")
        self.assertEqual(db.resolve_hero("阿蘭娜").name, "阿兰娜")
        self.assertEqual(db.resolve_hero("垃圾桶").name, "Z3000")
        self.assertEqual(db.resolve_hero("超天醬").name, "超天酱")
        self.assertEqual(db.resolve_hero("超絕最可愛天使醬").name, "超天酱")
        self.assertEqual(db.resolve_map("異變圖書館"), "异变图书馆")
        self.assertEqual(db.resolve_map("龍宮遊樂園"), "龙宫游乐园")
        self.assertEqual(db.chips["拳击手套-高级"].hant, "拳擊手套-高級")
        self.assertIn("异变图书馆", db.maps)
        self.assertNotIn("異變圖書館", db.maps)  # 程序内部仍用简体名做键

    def test_hant_similar_chips(self):
        """相近的繁体筹码名按稀有度取舍：名字只差一个字时，给了图标边框颜色就按颜色挑。"""
        db = self.db
        self.assertEqual(db.match_chip("銀行卡-餘額", "紫")[0], "银行卡-余额多")
        self.assertEqual(db.match_chip("銀行卡-餘額", "蓝")[0], "银行卡-余额少")
        self.assertEqual(db.match_chip("速度輪滑-初", "蓝")[0], "速度轮滑-初级")
        self.assertEqual(db.match_chip("速度輪滑-中", "紫")[0], "速度轮滑-中级")
        self.assertEqual(db.match_chip("速度輪滑-高", "金")[0], "速度轮滑-高级")
        self.assertEqual(db.match_chip("手持風扇", "紫")[0], "手持风扇-大")

    def test_hant_map_tasks(self):
        """地图的任务文字（map_tasks）只放简体的写法，每条任务一个，和资料里只有简体任务时一模一样；繁体任务按列表里的
        位置和简体任务配对，归进 map_task_keys 里那条任务的写法（简体在前），任务栏读到的繁体文字也认得出地图。"""
        db = self.db
        for name, info in _data("maps.json").items():
            with self.subTest(map=name):
                simp = tuple(dict.fromkeys(k for k in (task_key(t) for t in info["tasks"]) if k))
                self.assertEqual(db.map_tasks[name], simp)
                groups = db.map_task_keys[name]
                self.assertEqual(tuple(g[0] for g in groups), simp)
                for group in groups:
                    self.assertEqual(len(group), len(set(group)))  # 简繁写法相同的只留一种
                for task, hant in zip(info["tasks"], info["hant_tasks"]):
                    if hant is not None:
                        self.assertIn(task_key(hant), groups[simp.index(task_key(task))])
        library = dict(zip(db.map_tasks["异变图书馆"], db.map_task_keys["异变图书馆"]))
        self.assertEqual(library[task_key("击倒 3 只魔法茶壶")], (task_key("击倒魔法茶壶"), task_key("擊敗 3 隻魔法茶壺")))
        self.assertEqual(library[task_key("击倒 2 个大盗")], (task_key("击倒大盗"), task_key("擊敗 2 個大盜")))
        self.assertNotIn(task_key("擊敗 3 隻魔法茶壺"), db.map_tasks["异变图书馆"])
        self.assertIn(task_key("輪次7以前舞獅嘎嗚的生命值低於189"), {k for g in db.map_task_keys["水乡古镇"] for k in g})
        self.assertEqual(db.map_info["异变图书馆"].hant_tasks,
                         ("擊敗3隻魔法茶壺", "擊敗2隻罪惡茶壺", "擊敗2個大盜", "擊敗3個看守"))
        # 只差数量的几条简体任务算一条，它们的繁体写法并进这一条；简繁写法相同的这一条只有一种写法
        garden = dict(zip(db.map_tasks["园林中庭"], db.map_task_keys["园林中庭"]))
        self.assertEqual(garden["收集神秘的蛋"], ("收集神秘的蛋",))
        self.assertEqual(garden[task_key("击倒 1 只铜盏孔雀")], (task_key("击倒铜盏孔雀"), task_key("擊倒1隻銅盞孔雀")))

    def test_task_key_hant_quantifiers(self):
        """认任务名时简繁量词都去掉：隻、個、張、條、層、顆 等和简体写法一样处理。"""
        for read, bare in (("擊敗 3 隻魔法茶壺", "擊敗魔法茶壺"),
                           ("擊倒 2 個大盜", "擊倒大盜"),
                           ("使用 2 張卡牌", "使用卡牌"),
                           ("獲得 4 條線索", "獲得線索"),
                           ("罪證達到 3 層", "罪證達到"),
                           ("首次獲得 1 顆星星", "首次獲得星星")):
            with self.subTest(read=read):
                self.assertEqual(task_key(read), task_key(bare))


if __name__ == "__main__":
    unittest.main()
