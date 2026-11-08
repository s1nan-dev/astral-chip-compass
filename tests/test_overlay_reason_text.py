"""理由的短句和悬停说明的文字排版：短句的取舍、换行（text.tip_lines）、名称不拆开。用真的资料库和推荐引擎；断行的
名称表在用例里按需设置（tip_names_as），不靠同一个进程里先建过悬浮窗。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import contextlib
import dataclasses
import random
import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from PySide6.QtGui import QFont, QFontMetrics, QTextDocument, QTextLayout  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402
from advisor.data import GENERAL_TRAIT, load_db  # noqa: E402
from advisor.engine import Scorer, CORE_LEAD, Part, advise  # noqa: E402
from advisor.engine.advice import lift_core  # noqa: E402
from advisor.engine.advice_text import free_short  # noqa: E402
from advisor.pool import Teammate, State  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import OPEN_TASKS
from advisor.ui.text import breakdown_html
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.overlay_parts.factory import WrapLabel, _text_label
from advisor.ui.text import part_pill, lookahead_pill, tag_pill, tip_lines, tip_html, DIM_COLOR, TIP_TERMS, option_views, plain_words, breakdown_rows, FONT_M, reroll_pills, short_text, set_tip_names, SHORT_LIMIT, PERCENT_TIP, best_chip, keep_words, TIP_LINE, tip_names, FONT_S, KEEP_JOINER, KEEP_SPACE, part_label  # noqa: E402
from advisor.ui import text as ui_text  # noqa: E402
from tests.test_engine import BANNED_WORDING  # noqa: E402  口语字样、计分用语（适配表理由的检查，界面文字同样适用）
from tests.support.overlay_cases import APP, plain_html, score_marks
from tests.support.player_layer import with_player_layer


@contextlib.contextmanager
def tip_names_as(names):
    """临时设置悬停说明断行的名称表（text.set_tip_names 是模块级的设置，建悬浮窗时也会设置），结束后恢复原来的。"""
    old = set_tip_names(names)
    try:
        yield
    finally:
        set_tip_names(old)


def tip_breaks(lines) -> list[int]:
    """tip_lines 的结果拼回原句时，每个断行位置（第 p 个字之前）。"""
    out, pos = [], 0
    for line in lines[:-1]:
        pos += len(line)
        out.append(pos)
    return out


class ReasonShortTest(unittest.TestCase):
    """理由标签的短句：标签上只写要点，悬停显示原来的整句；
    两处都不写分值。

    每类理由都检查：长度、不写分值、悬停的就是原来的整句、短句里的数字和名字取自整句、
    不出现角色特性的内部名（「需要生存」「物理输出」等）和口语字样。
    """

    LONGEST = 24  # 短句的上限（一般 10～18 个字）；前瞻里写筹码名和出现率，另按最长的筹码名算

    @classmethod
    def setUpClass(cls):
        cls.db = db = load_db()
        texts = [r.reason for rules in db.trait_rules.values() for r in rules]
        texts += [r.reason for hs in db.hero_synergy.values() for r in hs.rules]
        texts += [r.reason for ps in db.player_synergy.values() for r in ps.rules]
        texts += [c.reason for c in db.combos] + [t.reason for t in db.teammate_rules]
        texts += [c.effect for c in db.chips.values()]
        # 角色特性的内部名：资料里的理由和筹码效果都用不到的特性名（「通用」是通用规则的键，理由里写「通用规则」）
        cls.internal = [t for t in db.traits if t != GENERAL_TRAIT and not any(t in x for x in texts)]
        cls.longest_name = max(len(n) for n in db.chips)

    def check(self, pill, part=None):
        """所有理由标签共同的要求。part 给定时，标签上就是这个分项的短句、悬停的就是它的整句 Part.reason，
        都不附加分值（整句里本来就有的游戏数值照写，所以用相等来查，不用正则）；part 不给时（前瞻、刷新）查不出分值写法。"""
        limit = self.LONGEST
        if pill.text.startswith("前瞻："):
            limit = max(limit, len("前瞻：") + self.longest_name + len(" 100%→100%"))
        self.assertLessEqual(len(pill.text), limit, pill.text)
        self.assertTrue(pill.tip, pill.text)
        self.assertLessEqual(len(pill.text), len(pill.tip), pill.text)  # 不比整句长
        for name in self.internal:
            self.assertNotIn(name, pill.text)
            self.assertNotIn(name, pill.tip)
        self.assertIsNone(BANNED_WORDING.search(pill.text), pill.text)
        if part is None:
            self.assertEqual(score_marks(pill.text), [], pill.text)
            self.assertEqual(score_marks(pill.tip), [], pill.tip)
            return
        # 引擎给的短句；没有时整句不超过 SHORT_LIMIT 个字照写，超过时写分项名
        expected = part.short or (part.reason if len(part.reason) <= SHORT_LIMIT else part_label(part.label))
        self.assertEqual(pill.text, expected)
        self.assertEqual(pill.text, short_text(part))
        self.assertEqual(pill.tip, part.reason)  # 整句，不带「（+N）」
        # 资料库未收录的那一项只作说明，灰色；其余按分值
        tone = "plus" if part.score >= 0.5 else "minus" if part.score <= -0.5 else "info"
        self.assertEqual(pill.tone, "info" if part.label == "未收录" else tone)

    def scaled(self, scale=0.5):
        """official.scale 改回 scale 的资料库副本：查内置分参与总分、分项和理由时的行为（现在取 0，不列这几项）。"""
        rules = {k: dict(v) if isinstance(v, dict) else v for k, v in self.db.rules.items()}
        rules["official"]["scale"] = scale
        return dataclasses.replace(self.db, rules=rules)

    def parts(self, hero, chip, owned=(), use_player=True, db=None, **kw):
        owned = tuple(owned)
        st = State(hero=hero, owned=owned, **kw)
        return Scorer(db or self.db, st, use_player=use_player).parts(chip, owned, kw.get("round"))

    def part(self, label, hero, chip, owned=(), db=None, **kw):
        found = [p for p in self.parts(hero, chip, owned, db=db, **kw) if p.label == label]
        self.assertTrue(found, (label, hero, chip))
        return found[0]

    def test_internal_trait_names_known(self):
        self.assertIn("需要生存", self.internal)
        self.assertIn("物理输出", self.internal)

    def test_official_parts(self):
        """内置分：成长分「成长分：至第 5 轮」、角色分「角色分：适配」写要点；标签分「标签：攻击 ✓」；
        关键词分「已持有标记流派」、推荐流派「推荐流派：标记」（流派名不加「」）。"""
        db = self.scaled(0.5)  # 内置分不计入总分时这几项不列，这里用内置分照旧计入的副本
        chip = next(c for c in self.db.chips.values() if c.growth_round and c.growth_round >= 2)
        growth = self.part("成长分", "凛", chip.name, db=db, round=1)
        self.assertEqual(part_pill(growth).text, f"成长分：至第 {chip.growth_round} 轮")
        self.assertIn(f"（第 {chip.growth_round} 轮）", growth.reason)
        self.check(part_pill(growth), growth)
        fit = next(c for c in self.db.chips.values() if "凛" in c.fit)
        part = self.part("角色分", "凛", fit.name, db=db)
        self.assertEqual(part_pill(part).text, "角色分：适配")
        self.check(part_pill(part), part)
        hero, minor = next((h, c.name) for c in self.db.chips.values() for h in sorted(c.fit_minor) if h in self.db.heroes)
        part = self.part("角色分", hero, minor, db=db)
        self.assertEqual(part_pill(part).text, "角色分：普通适配")
        self.check(part_pill(part), part)
        marks = [c.name for c in self.db.chips.values() if c.school == "标记" and not c.maps and not c.granted]
        keyword = self.part("关键词分", "美咲", marks[1], owned=[marks[0]], db=db)
        self.assertEqual(keyword.reason, "已持有「标记」流派的筹码")  # 整句照旧，在悬停说明里
        self.assertEqual(part_pill(keyword).text, "已持有标记流派")
        self.check(part_pill(keyword), keyword)
        e = next(x for x in advise(db, State(hero="美咲", rerolls=2), ["标靶", "大背包", "缓冲盾牌"]).evals
                 if x.chip.name == "标靶")
        tag = tag_pill(e)
        self.assertEqual(tag.text, "标签：攻击 ✓")
        self.check(tag)
        self.assertEqual(tag.tip, next(p for p in e.parts if p.label == "标签分").reason)
        school = self.part("推荐流派", "凛", "标靶")
        self.assertEqual(school.reason, "「标记」是该角色的推荐流派")
        self.assertEqual(part_pill(school).text, "推荐流派：标记")
        self.check(part_pill(school), school)

    def test_player_layer(self):
        """默认推荐（内部名「玩家思路」）：四档的标签写「筹码方案：推荐」，悬停整句「筹码方案「默认推荐」：推荐」；必选补分后
        整句加了后缀「，优先于本次未列入筹码方案的筹码」、短句不变；规则写适配表里的短句（short），以「筹码方案：」开头，
        扣分的写缺点（「筹码方案：没有充能来源」）。悬停明细里的分项名显示成「筹码方案」，界面上不出现「玩家思路」。
        默认推荐的四档和规则用测试用的那一份（tests/support/player_layer.toml）。"""
        self.db = with_player_layer(self.db)
        self.assertEqual((part_label("玩家思路"), part_label("方案")), ("筹码方案", "筹码方案"))
        tier = self.part("玩家思路", "远野汉娜", "速度轮滑-中级")
        self.assertEqual(part_pill(tier).text, "筹码方案：推荐")
        self.assertEqual(part_pill(tier).tip, "筹码方案「默认推荐」：推荐")
        self.check(part_pill(tier), tier)
        adv = advise(self.db, State(hero="凛", rerolls=2), ["大背包", "书签", "标靶"])
        e = next(x for x in adv.evals if x.chip.name == "大背包")
        # 必选：悬停说明是该角色默认推荐的攻略原文（[[player]] 的 note），短句照旧
        note = self.db.player_synergy["凛"].note
        core = next(p for p in e.parts if p.label == "玩家思路")
        self.assertEqual((part_pill(core).text, part_pill(core).tip), ("筹码方案：必选", note))
        lift_core([e], 10, CORE_LEAD)
        lifted = next(p for p in e.parts if p.label == "玩家思路")
        self.assertEqual(lifted.reason, f"{note.rstrip('。')}。优先于本次未列入筹码方案的筹码")  # 原文后面另起一句
        self.assertEqual(part_pill(lifted).text, "筹码方案：必选")
        self.check(part_pill(lifted), lifted)
        self.assertIn("筹码方案", [label for label, _, _ in breakdown_rows(e)])
        self.assertNotIn("玩家思路", plain_html(breakdown_html(e, best=True)))
        # 没有原文的默认推荐（测试里去掉 note）写「筹码方案「默认推荐」：必选」，补分时接在后面
        import dataclasses
        bare = dataclasses.replace(self.db, player_synergy={
            **self.db.player_synergy, "凛": dataclasses.replace(self.db.player_synergy["凛"], note="")})
        e = next(x for x in advise(bare, State(hero="凛", rerolls=2), ["大背包", "书签", "标靶"]).evals
                 if x.chip.name == "大背包")
        lift_core([e], 10, CORE_LEAD)
        self.assertEqual(next(p for p in e.parts if p.label == "玩家思路").reason,
                         "筹码方案「默认推荐」：必选，优先于本次未列入筹码方案的筹码")
        rule = self.part("玩家思路", "远野汉娜", "鹰眼瞄具")
        self.assertGreater(len(rule.reason), SHORT_LIMIT)
        # 写要点（标记流派提升小），整句在悬停说明里
        self.assertEqual(part_pill(rule).text, "筹码方案：标记流派提升小")
        self.check(part_pill(rule), rule)
        # 扣分的写缺点
        long = self.part("玩家思路", "照", "电磁炮")
        self.assertEqual(long.reason, "筹码方案：没有充能来源时电磁炮难以触发，一般不选")
        self.assertEqual(part_pill(long).text, "筹码方案：没有充能来源")
        self.check(part_pill(long), long)
        # 没写短句、整句本来就是要点（不超过 SHORT_LIMIT 个字）的照写整句；现在的资料里每条规则都写了短句，用造的一项
        short = Part("玩家思路", -20.0, "筹码方案：测试用的短理由", "攻略", "")
        self.assertEqual(part_pill(short).text, "筹码方案：测试用的短理由")
        self.check(part_pill(short), short)
        # 适配表里全部的玩家思路规则：标签以「筹码方案」开头，符合所有理由标签共同的要求；
        # 写了短句的不超过 SHORT_LIMIT 个字、末尾不写数值
        sc = Scorer(self.db, State(hero="凛"))
        count = 0
        for hero, ps in self.db.player_synergy.items():
            for r in ps.rules:
                p = Part("玩家思路", r.score, r.reason, r.source, r.short or free_short(sc.db, r.reason))
                with self.subTest(hero=hero, reason=r.reason):
                    self.assertTrue(part_pill(p).text.startswith("筹码方案"), part_pill(p).text)
                    self.check(part_pill(p), p)
                    if r.short:
                        self.assertLessEqual(len(r.short), SHORT_LIMIT, r.short)
                        self.assertNotRegex(r.short, r"\d$")
                count += 1
        self.assertGreater(count, 10)

    def origin_short(self, origin: str) -> str:
        """「另符合：……」里一项的短说明：特性写 [[trait]] 的 short（没写时写特性说明的第一个分句），
        通用规则、角色专属规则照写。"""
        if origin == "角色":
            return "角色专属规则"
        if origin == GENERAL_TRAIT:
            return "通用规则"
        return self.db.trait_shorts.get(origin) or re.split("[，；（]", self.db.traits[origin])[0]

    def test_auto_layer_free_text(self):
        """特性、角色专属规则、其他特性（适配表里的规则）：特性和角色专属规则写规则的短句（short），即筹码在该角色身上
        起作用的要点（「受击时获得治愈和星币」）；其他特性写「另符合：第一项的短说明」（[[trait]] 的 short，通用规则、
        角色专属规则照写；有多项时加「等」），不退成分项名。规则没写短句时的退路：不超过 SHORT_LIMIT 个字照写，
        超过时特性写「特性：书面说明的第一个分句」，其余只写分项名。"""
        seen = set()
        for hero in ("Z3000", "凛", "美咲", "茉莉", "梅加斯", "小町"):
            scorer = Scorer(self.db, State(hero=hero), use_player=False)
            rules = self.db.hero_synergy[hero].rules
            for chip in self.db.chips.values():
                if chip.granted or chip.maps or chip.name == "循环往复":
                    continue
                for p in scorer.parts(chip.name, (), None):
                    if p.label in ("特性", "角色专属规则"):
                        rule = next(r for r in rules if chip.name in r.chips and r.origin == p.origin
                                    and r.reason == p.reason and r.score == p.score)
                        if rule.short:
                            kind, expected = "短句", rule.short
                        elif len(p.reason) <= SHORT_LIMIT:
                            kind, expected = "照写", p.reason
                        else:
                            kind, expected = "退路", free_short(scorer.db, p.reason, p.origin) or part_label(p.label)
                    elif p.label == "其他特性":
                        points = list(dict.fromkeys(self.origin_short(o) for o in p.origin.split("、")))
                        kind, expected = "另符合", f"另符合：{points[0]}{'等' if len(points) > 1 else ''}"
                        self.assertLessEqual(len(expected), SHORT_LIMIT, expected)
                    else:
                        continue
                    with self.subTest(hero=hero, chip=chip.name, label=p.label):
                        self.assertEqual(part_pill(p).text, expected)
                        self.check(part_pill(p), p)
                    seen.add((p.label, kind))
        self.assertLessEqual({("特性", "短句"), ("角色专属规则", "短句"), ("其他特性", "另符合")}, seen)
        # 规则没写短句时的退路（engine.advice_text.free_short）：特性写书面说明的第一个分句，不出现特性名；通用规则、角色专属规则
        # 不出短句（界面写分项名）；「另符合：……」拼出来超过 SHORT_LIMIT 个字时只留第一项说明的第一个分句
        sc = Scorer(self.db, State(hero="凛"))
        self.assertEqual(free_short(sc.db, "x" * 30, "受伤触发"), "特性：经常被攻击")
        self.assertEqual(free_short(sc.db, "x" * 30, "技能依赖"), "特性：收益主要来自主动技能")
        self.assertEqual(free_short(sc.db, "x" * 30, GENERAL_TRAIT), "")
        self.assertEqual(free_short(sc.db, "x" * 30, "角色"), "")
        self.assertEqual(free_short(sc.db, "另符合：主要通过挑战怪物造成伤害；每回合可打出多张效果牌"), "另符合：主要通过挑战怪物造成伤害等")
        # 适配表里全部的特性规则和角色专属规则（按引擎的写法逐条构造分项）：短句不超过 SHORT_LIMIT 个字，末尾不写数值
        # （标签上带分值时会连成「攻击力 +5 +50」；分值去掉后仍沿用这条写法），状态名、卡牌名不加「」
        count = 0
        for hs in self.db.hero_synergy.values():
            for r in hs.rules:
                p = Part("角色专属规则" if r.origin == "角色" else "特性", r.score, r.reason, r.origin,
                         r.short or free_short(sc.db, r.reason, r.origin))
                with self.subTest(reason=r.reason):
                    self.check(part_pill(p), p)
                    if r.short:
                        self.assertLessEqual(len(r.short), SHORT_LIMIT, r.short)
                        self.assertNotRegex(r.short, r"\d$")
                        self.assertNotRegex(r.short, "[「」]")
                count += 1
        self.assertGreater(count, 100)
        # [[trait]] 的短说明：拼成「另符合：X等」也不超过 SHORT_LIMIT 个字，不出现特性的内部名
        for trait, text in self.db.trait_shorts.items():
            with self.subTest(trait=trait):
                self.assertLessEqual(len(f"另符合：{text}等"), SHORT_LIMIT, text)
                for name in self.internal:
                    self.assertNotIn(name, text)

    def test_combos(self):
        """组合：加分的写「组合：触发规则的已有筹码」，扣分的写「与……冲突」（不加「组合：」），
        两个以上时写第一个加「等」。"""
        part = self.part("组合", "美咲", "美工刀-锋利", owned=["医疗箱-紧急治疗"])
        self.assertEqual(part_pill(part).text, "组合：医疗箱-紧急治疗")
        self.check(part_pill(part), part)
        conflict = self.part("组合", "墨影", "美工刀-锋利", owned=["磨刀石"])
        self.assertEqual(part_pill(conflict).text, "与磨刀石冲突")
        self.assertEqual(part_pill(conflict).tone, "minus")
        self.check(part_pill(conflict), conflict)
        both = [p for p in self.parts("美咲", "美工刀-锋利", ["大碗炖肉", "医疗箱-紧急治疗", "维生素药丸"]) if p.label == "组合"]
        multi = next(p for p in both if p.reason.startswith("已持有「医疗箱-紧急治疗」「大碗炖肉」"))
        self.assertEqual(part_pill(multi).text, "组合：医疗箱-紧急治疗等")
        for p in both:
            self.check(part_pill(p), p)

    def test_anticipations(self):
        """美工刀预判、配套筹码预判：写被预判的事件和概率「之后获得美工刀 79%」「之后获得配套筹码 87%」，
        概率和整句里的相同。"""
        knife = self.part("组合", "美咲", "医疗箱-紧急治疗", star=1, round=5)
        self.assertGreaterEqual(knife.score, 0.5)
        m = re.fullmatch(r"之后获得美工刀 (\d+%)", part_pill(knife).text)
        self.assertTrue(m, part_pill(knife).text)
        self.assertIn(f"概率约 {m.group(1)}：", knife.reason)
        self.check(part_pill(knife), knife)
        if "school_build" in self.db.rules:
            charge = [p for p in self.parts("邦妮", "永动机", star=1, round=3) if p.origin == "school_build"]
            self.assertTrue(charge)
            m = re.fullmatch(r"之后获得配套筹码 (\d+%)", part_pill(charge[0]).text)
            self.assertTrue(m, part_pill(charge[0]).text)
            self.assertIn(f"概率约 {m.group(1)}：", charge[0].reason)
            self.check(part_pill(charge[0]), charge[0])

    def test_teammates(self):
        """队友：加分的短句写「配合队友：邦妮、凛」——不超过 2 名写角色名，多的写第一个和人数；
        只按电脑队友计的规则后接「（电脑）」；电脑队友的扣分写缺点「电脑队友不配合站位」。整句不加前缀，
        直接从「队友「邦妮」「凛」……」写起。"""
        part = self.part("队友", "美咲", "标靶", teammates=(Teammate("邦妮"), Teammate("凛"), Teammate("阿尔")))
        self.assertEqual(part_pill(part).text, "配合队友：邦妮、凛")
        self.assertTrue(part.reason.startswith("队友「邦妮」「凛」在怪物带有标记时"), part.reason)
        self.check(part_pill(part), part)
        bot = self.part("队友", "帕露南", "大碗炖肉", teammates=(Teammate("凛", True),))
        self.assertLess(bot.score, 0)
        self.assertEqual(part_pill(bot).text, "电脑队友不配合站位")
        self.check(part_pill(bot), bot)
        # 只按电脑队友计的加分规则（白泽赐福）：短句后接「（电脑）」
        baize = self.part("队友", "美咲", "医疗箱-完备治疗", teammates=(Teammate("姬梦朝", True),))
        self.assertIn("白泽赐福", baize.reason)
        self.assertEqual(part_pill(baize).text, "配合队友：姬梦朝（电脑）")
        self.check(part_pill(baize), baize)
        # 写了短句的队友规则：按最长的写法填入队友后不超过 SHORT_LIMIT 个字；末尾不写数值（角色名本身的数字除外，
        # 例如 Z3000，所以只查短句的写法本身）。队友只能是规则限定的角色（heroes；同一个角色一队只有一名），
        # 不限定时取名字最长的两名角色和「等 3 名」
        rules = [r for r in self.db.teammate_rules if r.short]
        self.assertTrue(rules)
        for r in rules:
            self.assertNotRegex(r.short, r"\d$")
            names = sorted(r.heroes or self.db.heroes, key=len)
            fills = [names[-1]] if len(names) == 1 else ["、".join(names[-2:]), f"{names[-1]}等 3 名"]
            for mates in fills:
                filled = r.short.replace("{mates}", mates).replace("{n}", "3")
                with self.subTest(short=r.short, mates=mates):
                    self.assertLessEqual(len(filled), SHORT_LIMIT, filled)
        mates = (Teammate("墨影"), Teammate("邦妮"), Teammate("美咲"))
        three = [p for p in self.parts("帕露南", "大铜锣", teammates=mates, map="水乡古镇")
                 if p.reason.startswith("队友「墨影」「邦妮」「美咲」")]
        self.assertTrue(three)
        self.assertEqual(part_pill(three[0]).text, "配合队友：墨影等 3 名")
        self.check(part_pill(three[0]), three[0])

    def test_teammates_bot_mark(self):
        """不分真人电脑的队友规则（大碗炖肉、大铜锣等）匹配到电脑队友时，短句标出电脑（「配合队友：潘大猛」和
        「电脑队友不配合站位」同时出现时看不出潘大猛是电脑）：写角色名的在每名电脑队友后面接「（电脑）」，和悬停说明里的
        队友列表「队友：邦妮、凛（电脑）」、只按电脑队友计的规则同一写法；写人数的全是电脑时末尾加「（电脑）」，
        真人和电脑都有时加「（含电脑）」。整句和分值不变。"""
        def mates_part(chip, mates):
            return [p for p in self.parts("帕露南", chip, teammates=mates, map="水乡古镇")
                    if p.label == "队友" and p.score > 0]

        bot = mates_part("大碗炖肉", (Teammate("潘大猛", True),))
        human = mates_part("大碗炖肉", (Teammate("潘大猛"),))
        self.assertEqual([part_pill(p).text for p in bot], ["配合队友：潘大猛（电脑）"])
        self.assertEqual([part_pill(p).text for p in human], ["配合队友：潘大猛"])
        self.assertEqual([(p.score, p.reason) for p in bot], [(p.score, p.reason) for p in human])  # 只改短句
        self.check(part_pill(bot[0]), bot[0])
        # 同一选项上照旧还有电脑队友的扣分「电脑队友不配合站位」
        stew = self.parts("帕露南", "大碗炖肉", teammates=(Teammate("潘大猛", True),), map="水乡古镇")
        self.assertIn("电脑队友不配合站位", [part_pill(p).text for p in stew if p.label == "队友"])
        cases = (
            ((Teammate("墨影", True), Teammate("美咲", True)), "配合队友：墨影（电脑）、美咲（电脑）"),
            ((Teammate("墨影"), Teammate("美咲", True)), "配合队友：墨影、美咲（电脑）"),
            ((Teammate("墨影", True), Teammate("美咲")), "配合队友：墨影（电脑）、美咲"),
            ((Teammate("墨影"), Teammate("邦妮", True), Teammate("美咲", True)), "配合队友：墨影等 3 名（含电脑）"),
            ((Teammate("墨影", True), Teammate("邦妮", True), Teammate("美咲", True)), "配合队友：墨影等 3 名（电脑）"),
            ((Teammate("墨影"), Teammate("邦妮"), Teammate("美咲")), "配合队友：墨影等 3 名"),
        )
        for mates, text in cases:
            with self.subTest(mates=mates):
                parts = mates_part("大铜锣", mates)
                self.assertEqual([part_pill(p).text for p in parts], [text])
                self.assertLessEqual(len(text), SHORT_LIMIT)
        # 只按电脑队友计的规则（白泽赐福）照旧只在末尾写一次「（电脑）」，只按真人计的规则不写
        baize = self.part("队友", "美咲", "医疗箱-完备治疗", teammates=(Teammate("姬梦朝", True),))
        self.assertEqual(part_pill(baize).text, "配合队友：姬梦朝（电脑）")
        bank = [p for p in self.parts("阿尔", "银行卡-用不完", teammates=(Teammate("帕露南", True), Teammate("茉莉")))
                if p.label == "队友" and p.origin == "human"]
        self.assertEqual([part_pill(p).text for p in bank], ["配合队友：茉莉"])

    def test_difficulty_income_endless(self):
        """难度「难度：生存类」；收入折算写缺点本身：有星币收入时写剩余轮次「收入剩余 14 轮」，只有星光收入时写
        剩余的兑现次数「星光剩余 2 次兑现」，轮次待识别时「星币不再用于升星」；循环往复写增加的刷新和三选一。
        收入折算是扣分（橙色），标签上不写分值。"""
        for chip in ("医疗箱-完备治疗", "摩托头盔-中级"):  # 摩托头盔：经常挑战怪物的角色另有说明，短句相同
            hard = self.part("难度", "美咲", chip)
            self.assertEqual(part_pill(hard).text, "难度：生存类")
            self.check(part_pill(hard), hard)
        # 纯收入筹码的正分只有内置分，现在没有可折的分：这一条用内置分照旧计入的副本
        db = self.scaled(0.5)
        coins = self.part("收入折算", "凛", "小猪存钱罐", db=db, round=3, map="异变图书馆")
        m = re.fullmatch(r"收入剩余 (\d+) 轮", part_pill(coins).text)
        self.assertTrue(m, part_pill(coins).text)
        self.assertIn(f"前还有 {m.group(1)} 轮", coins.reason)
        self.assertEqual(part_pill(coins).tone, "minus")
        self.check(part_pill(coins), coins)
        star = self.part("收入折算", "茉莉", "银行卡-余额少", db=db, star=3, round=12)
        m = re.fullmatch(r"星光剩余 (\d+) 次兑现", part_pill(star).text)
        self.assertTrue(m, part_pill(star).text)
        self.assertIn(f"前还有 {m.group(1)} 次轮次奖励", star.reason)
        self.check(part_pill(star), star)
        full = self.part("收入折算", "茉莉", "小猪存钱罐", db=db, star=3)  # 轮次待识别：只有满 3 星这一项
        self.assertIn("已达到 3 星，星币不再用于升星", full.reason)
        self.assertEqual(part_pill(full).text, "星币不再用于升星")
        self.check(part_pill(full), full)
        cycle = self.part("循环往复", "凛", "循环往复", star=1, round=5, rerolls=2)
        self.assertEqual(part_pill(cycle).text, "3 次刷新和 1 次三选一")
        self.check(part_pill(cycle), cycle)
        late = self.part("循环往复", "凛", "循环往复", star=2, round=15, rerolls=2)  # 3 回合后已到进度上限
        self.assertEqual(part_pill(late).text, "3 次刷新")
        self.check(part_pill(late), late)

    def test_tendency_and_unrecorded(self):
        """职业倾向（0 分，灰色）「职业倾向：卡牌（暂不加分）」；资料库未收录以分项名开头「未收录：按平均水平」，
        整句仍写全称「资料库未收录：按筹码池里各筹码的平均水平计算」（都不写中性分的数值）。"""
        part = self.part("职业倾向", "凛", "小猪存钱罐", db=self.scaled(0.5), round=1)
        self.assertEqual(part_pill(part).text, "职业倾向：卡牌（暂不加分）")
        self.assertEqual(part_pill(part).tone, "info")
        self.assertTrue(part.reason.endswith("职业分值未达到 100，暂不加分"), part.reason)
        self.check(part_pill(part), part)
        e = advise(self.db, State(hero="凛", rerolls=2), ["星辰回响徽记", "书签", "标靶"]).evals[0]
        self.assertEqual(e.parts[0].reason, "资料库未收录：按筹码池里各筹码的平均水平计算")
        self.assertEqual(part_pill(e.parts[0]).text, "未收录：按平均水平")
        self.check(part_pill(e.parts[0]), e.parts[0])
        blue = advise(self.db, State(hero="凛", rerolls=2), ["星辰回响徽记", "书签", "标靶"], rarity=["蓝"]).evals[0]
        self.assertEqual(blue.parts[0].reason, "资料库未收录：按蓝色筹码的平均水平计算")  # 知道稀有度时按同稀有度算
        self.assertEqual(part_pill(blue.parts[0]).text, "未收录：按平均水平")
        self.assertIsNone(lookahead_pill(e))  # 未收录的不计前瞻
        # 未收录那一项的分值是中性分（通常为正），只作说明：标签和悬停明细都是灰色（之前按分值是绿色）
        for item in (e, blue):
            self.assertGreaterEqual(item.parts[0].score, 0.5)
            self.assertEqual(part_pill(item.parts[0]).tone, "info")
            self.assertEqual([tone for _, _, tone in breakdown_rows(item)], ["info"])
            self.assertIn(f"color:{DIM_COLOR}\">资料库未收录", breakdown_html(item))

    def test_lookahead_short_choice(self):
        """前瞻的短句取哪一句：为正时取出现率上升最多的高适配度筹码（「前瞻：标记喷罐 19%→24%」），
        为负时取本局不再出现的高适配度筹码（「前瞻：美工刀-锋利等不再出现」）；没有具体筹码时写流派或概括的一句。"""
        scorer = Scorer(self.db, State(hero="凛"))
        lock = (("标记", "治愈"), ["星光", "充能"])
        for keys, value, expected in (
            ({"pool": True, "gain": ("标记喷罐", 0.19, 0.24)}, 8.0, "前瞻：标记喷罐 19%→24%"),
            ({"locked": lock, "lost": ["美工刀-锋利", "美工刀-初级"], "gain": ("星币锤", 0.16, 0.29)}, -11.0,
             "前瞻：美工刀-锋利等不再出现"),
            ({"locked": lock, "lost": ["星币锤"]}, -3.0, "前瞻：星币锤不再出现"),
            # 分值最高的是本次的另一个候选：写「放弃本次的X」；「等」只算不是本次候选的
            ({"locked": lock, "lost": ["金色飞星", "星币锤"], "lost_here": ["金色飞星"]}, -9.0, "前瞻：放弃本次的金色飞星"),
            ({"locked": lock, "lost": ["星币锤", "金色飞星"], "lost_here": ["金色飞星"]}, -9.0, "前瞻：星币锤不再出现"),
            ({"locked": lock, "lost": ["星币锤", "金色飞星", "紫色飞星"], "lost_here": ["金色飞星"]}, -9.0,
             "前瞻：星币锤等不再出现"),
            ({"locked": lock, "lost": ["医疗箱-紧急治疗"], "lost_here": ["医疗箱-紧急治疗"]}, -9.0,
             "前瞻：放弃本次的医疗箱-紧急治疗"),  # 最长的筹码名：16 个字，不超过 SHORT_LIMIT
            ({"locked": lock, "lost": ["星币锤"]}, 28.0, "前瞻：锁定标记、治愈流派"),
            ({"locked": lock}, -5.0, "前瞻：星光、充能流派不再出现"),
            ({"diluted": ("拳击手套-高级", 0.14, 0.12)}, -4.0, "前瞻：拳击手套-高级 14%→12%"),
            ({"diluted": None}, -6.0, "前瞻：其他高适配度筹码出现率下降"),
            ({"school": "治愈"}, 5.0, "前瞻：进入治愈流派"),
            ({"pool": True}, 4.0, "前瞻：同稀有度筹码出现率提高"),
            ({}, 4.0, ""),
        ):
            scorer.memo.lookahead_keys[("标靶", ())] = keys
            with mock.patch.object(scorer, "lookahead", return_value=value):
                self.assertEqual(scorer.lookahead_short("标靶", ()), expected, keys)
                self.assertLessEqual(len(expected), SHORT_LIMIT)

    def test_lookahead_lost_offer_chip(self):
        """前瞻「本局不再出现的高适配度筹码：X」的 X 是本次的另一个候选时（例如芬妮选电流剑会锁掉星光流派，
        金色飞星就在同一张卡片上），整句改写「放弃本次的金色飞星，之后也不再出现」，标签短句「前瞻：放弃本次的金色飞星」；
        不是本次候选的照旧写在「本局不再出现的高适配度筹码：」后面。写法只影响文字，前瞻分不变。
        芬妮的默认推荐档位用测试用的那一份（tests/support/player_layer.toml：金色飞星是必选，丢掉它前瞻为负）。"""
        self.db = with_player_layer(self.db)
        st = State(hero="芬妮", star=2, round=7, rerolls=3, owned=("医疗箱-紧急治疗",))
        offer = ["金色飞星", "电流剑", "书签"]
        adv = advise(self.db, st, offer)
        sword = adv.evals[1]
        self.assertIn("放弃本次的金色飞星，之后也不再出现", sword.lookahead_reason.split("；"))
        self.assertNotIn("金色飞星", sword.lookahead_reason.split("本局不再出现的高适配度筹码：")[-1]
                         if "本局不再出现的高适配度筹码：" in sword.lookahead_reason else "")
        self.assertLess(sword.lookahead, 0)
        self.assertEqual(sword.lookahead_short, "前瞻：放弃本次的金色飞星")
        # 不知道本次候选（Scorer 没给 offer）时照旧写法；前瞻分相同
        plain = Scorer(self.db, st)
        self.assertIn("本局不再出现的高适配度筹码：金色飞星", plain.lookahead_reason("电流剑", st.owned))
        self.assertAlmostEqual(plain.lookahead("电流剑", st.owned), sword.lookahead, places=9)

    def test_lookahead_short_from_reason(self):
        """实际的三选一：前瞻短句里的筹码名、出现率、流派都取自整句，前瞻为正写上升的、为负写下降的。"""
        cases = [
            ("凛", ["小猪存钱罐", "电流核心", "银行卡-余额多"], dict(round=3, map="异变图书馆")),
            ("美咲", ["手电筒-强光", "手持风扇-小", "魔法箭袋"],
             dict(star=2, round=9, map="星趴·梦想号", owned=("普通瞄具", "速度轮滑-中级"))),
            ("美咲", ["电流剑", "医疗箱-紧急治疗", "速度轮滑-中级"], dict(star=1, round=5, map="星趴·梦想号", owned=("普通瞄具",))),
            ("茉莉", ["小猪存钱罐", "银行卡-余额少", "永动机"], dict(star=3, round=12)),
            ("南希露", ["鹰眼瞄具", "美工刀-初级", "诅咒之剑"], dict(star=1, round=4, owned=("普通瞄具",))),
        ]
        shown = 0
        for hero, offer, kw in cases:
            for e in advise(self.db, State(hero=hero, rerolls=2, **kw), offer).evals:
                pill = lookahead_pill(e)
                if pill is None:
                    continue
                shown += 1
                self.check(pill)
                self.assertEqual(pill.tip, e.lookahead_reason)  # 整句，不带「（前瞻 +N）」
                self.assertEqual(pill.text, e.lookahead_short)
                self.assertEqual(pill.tone, "plus" if e.lookahead >= 0.5 else "minus")
                head = re.fullmatch(r"前瞻：(.+)", pill.text)
                self.assertTrue(head, pill.text)
                point, reason = head.group(1), e.lookahead_reason
                self.assertNotRegex(point, r"\s[+-]\d+$")  # 末尾没有分值
                rate = re.fullmatch(r"(.+) (\d+)%→(\d+)%", point)
                if rate:
                    name, a, b = rate.groups()
                    self.assertIn(f"{name}下次出现率 {a}% → {b}%", reason)
                    self.assertEqual(int(b) > int(a), e.lookahead >= 0, pill.text)
                elif point.startswith("锁定"):
                    self.assertGreaterEqual(e.lookahead, 0)
                    schools = point[len("锁定"):-len("流派")].split("、")
                    self.assertTrue(reason.startswith("选择后锁定" + "".join(f"「{s}」" for s in schools)), reason)
                elif point.endswith("流派不再出现"):
                    self.assertLess(e.lookahead, 0)
                    schools = point[:-len("流派不再出现")].split("、")
                    self.assertIn("".join(f"「{s}」" for s in schools) + "流派的筹码本局不再出现", reason)
                elif point.endswith("不再出现"):
                    self.assertLess(e.lookahead, 0)
                    listed = reason.split("本局不再出现的高适配度筹码：")[1].split("；")[0].split("、")
                    self.assertEqual(point, f"{listed[0]}{'等' if len(listed) > 1 else ''}不再出现")
                    self.assertNotIn(listed[0], offer)  # 本次的候选写「放弃本次的X」
                elif point.startswith("放弃本次的"):
                    self.assertIn(point[len("放弃本次的"):], offer)
                    self.assertIn(point, reason)
                elif point.startswith("进入"):
                    self.assertIn(f"进入「{point[len('进入'):-len('流派')]}」流派，", reason)
                else:
                    self.assertIn(point, reason)  # 「其他高适配度筹码出现率下降」「同稀有度筹码出现率提高」
        self.assertGreaterEqual(shown, 4)  # 现在是 7 个；留出余量，打分规则调整后前瞻分可能不到 3

    def test_reroll_pills(self):
        adv = advise(self.db, State(hero="凛", rerolls=2), ["标靶", "大背包", "缓冲盾牌"])
        pills = reroll_pills(adv.reroll, best_chip(adv), adv.state.rerolls)
        self.assertEqual(len(pills), 3)
        for pill in pills:
            self.check(pill)
        # 用最长的筹码名比较时也不超过短句的上限
        longest = max(self.db.chips.values(), key=lambda c: len(c.name))
        best = SimpleNamespace(chip=longest, total=60.0)
        for value in (70.0, 50.0, 60.0):
            for pill in reroll_pills(SimpleNamespace(expected=value + 9, cost=9.0, value=value, p_better=1.0), best):
                self.check(pill)

    def test_most_pills_fit_one_line(self):
        """一般 10～18 个字：各角色的三选一里，九成左右的理由标签不超过 18 个字；全部不超过上限，悬停都有整句。
        分项的标签按分项本身检查（整句里可能本来就有「主动技能冷却 -1」这样的游戏数值），标签分、前瞻、刷新的标签查不出分值写法。"""
        rng = random.Random(3)
        names = [c.name for c in self.db.chips.values() if not c.granted and not c.maps]
        lengths = []
        for hero in sorted(self.db.hero_synergy)[::3]:
            adv = advise(self.db, State(hero=hero, star=1, round=5, rerolls=2), rng.sample(names, 3))
            for v in option_views(adv):
                e = None if v.reroll else adv.evals[v.slot]
                for pill in v.pills:
                    part = None
                    if e is not None and pill not in (tag_pill(e), lookahead_pill(e)):
                        # 同一效果一正一负的两条合成一个标签（净效果）时，悬停是两条整句，按合成后的一项查
                        merged = [p for _, p in ui_text.net_parts([p for p in e.parts if p.reason])]
                        part = next(p for p in merged if p.reason == pill.tip and p.label != "标签分")
                    self.check(pill, part)
                    lengths.append(len(pill.text))
        self.assertGreaterEqual(sum(n <= 18 for n in lengths) / len(lengths), 0.85, lengths)

    def test_tip_html(self):
        """悬停说明：短句照写成一行；长句自己分行（tip_lines），放进按最长一行定宽的表格里，去掉换行后和整句相同。
        按悬浮窗的做法设置名称表（TipLinesTest）。"""
        short = "已持有「标记」流派的筹码"
        long = ("选择后锁定「标记」「充能」流派，「星光」「治愈」流派的筹码本局不再出现；"
                "本局不再出现的高适配度筹码：美工刀-锋利、美工刀-初级")
        with tip_names_as(tip_names(self.db)):
            self.assertEqual(tip_html(short), short)
            self.assertGreater(len(long), TIP_LINE + 4)
            html_text = tip_html(long)
            self.assertRegex(html_text, r'^<table width="\d+"')
            self.assertLessEqual(int(re.search(r'width="(\d+)"', html_text).group(1)), 600)
            doc = QTextDocument()
            doc.setHtml(html_text)
            self.assertEqual(doc.toPlainText().strip().replace("\n", ""), long)
            self.assertEqual(tip_lines(long), ["选择后锁定「标记」「充能」流派，「星光」「治愈」流派的筹码本局不再出现；",
                                               "本局不再出现的高适配度筹码：美工刀-锋利、美工刀-初级"])

    def test_tip_lines_break_after_punctuation(self):
        """悬停说明的断行优先落在标点之后（避免「刷 / 新」这样被拆在两行）：「刷新」的悬停整句、推荐度的说明；
        没有标点可断时不拆开数字和字母、不拆开常见的双字词，行首不是收尾的标点，各行拼起来就是原句。
        设置了名称表（悬浮窗的做法）和没设置（基本的规则）时都是这样。"""
        for names in (tip_names(self.db), None):
            with self.subTest(names="已设置" if names else "未设置"), tip_names_as(names):
                self.check_punctuation_first()

    def check_punctuation_first(self):
        reroll = "综合刷新后可能出现的筹码和消耗 1 次刷新的影响，刷新预计优于「标记喷罐」。"
        self.assertEqual(tip_lines(reroll), ["综合刷新后可能出现的筹码和消耗 1 次刷新的影响，", "刷新预计优于「标记喷罐」。"])
        for line in tip_lines(PERCENT_TIP)[:-1]:
            self.assertIn(line[-1], "，。；：、")
        no_marks = "攻击力加成攻击力加成攻击力加成攻击力加成攻击力加成攻击力加成攻击力加成攻击力加成 19% → 31% 防御力加成防御力加成"
        lines = tip_lines(no_marks)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), no_marks)
        for a, b in zip(lines, lines[1:]):
            joint = a[-1] + b[0]
            self.assertFalse(joint.isascii() and joint.strip(), joint)  # 「19% → 31%」不拆开
            self.assertNotIn(joint, ("攻击", "击力", "防御", "御力"))
        rng = random.Random(5)
        texts = [r.reason for rules in self.db.trait_rules.values() for r in rules]
        texts += [t.reason for t in self.db.teammate_rules] + [p.tip for p in self.db.series]
        long = [t for t in texts if len(t) > TIP_LINE + 4]
        marks = breaks = 0
        for text in rng.sample(long, min(60, len(long))):
            lines = tip_lines(text)
            self.assertEqual("".join(lines), text)
            for a, b in zip(lines, lines[1:]):
                breaks += 1
                marks += a[-1] in "，。；：、！？）」"
                self.assertNotIn(b[0], "，。；：、！？）」%", text)
        self.assertGreaterEqual(marks / breaks, 0.8)  # 没设置名称表时约九成落在标点之后，设置后更多


class TipLinesTest(unittest.TestCase):
    """悬停说明断行（text.tip_lines）：设置名称表后，筹码名、角色名、地图名、
    畸变组件名、常用术语（text.TIP_TERMS）和「X」流派整体不拆，断点优先落在标点之后、其次是左引号左括号之前。
    此外：两行放得下就不分三行，行首不放「的」等虚词。名称表由悬浮窗建好资料库后设置一次
    （tip_lines 本身不接触资料库）；没设置时不保护名称和术语，其余规则相同。"""

    # 不设置名称表时会拆开名称、术语、「X」流派，或断在两个汉字之间的几句（引擎和适配表里的实际句式）
    BANK = "队友「邦妮」「Z3000」「蓝海晴」的轮次奖励星币同样 +3（银行卡-用不完对所有角色生效），真人队友可用于升星或在商店购买卡牌"
    RATE = ("进入「星光」流派后同流派筹码出现率提高，其他高适配度筹码出现率下降；拳击手套-高级下次出现率 12% → 11%；"
            "星币锤下次出现率 16% → 25%")
    SCHOOL = ("选择后锁定「充能」「星光」流派，「治愈」「标记」流派的筹码本局不再出现；ATM机下次出现率 13% → 26%；"
              "银行卡-余额多下次出现率 3% → 7%")
    DART = "已持有「忍术飞镖」：怪物带有标记时才返还卡牌或增加伤害，该筹码可对怪物施加标记"

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.names = tip_names(cls.db)

    def protected(self, text) -> list[tuple[int, int, str]]:
        """text 里名称、术语、「X」流派每次出现占的 [起, 止) 和原文（可重叠，逐个名称查）。"""
        spans = []
        for word in (*self.names, *TIP_TERMS):
            spans += [(m.start(), m.end(), word) for m in re.finditer(re.escape(word), text)]
        for m in re.finditer(r"(?:「[^」]{1,6}」)+流派", text):
            spans.append((text.rfind("「", m.start(), m.end()), m.end(), m.group()))
        return spans

    def split_words(self, text, lines) -> list[str]:
        """被拆到两行的名称、术语、「X」流派。"""
        self.assertEqual("".join(lines), text)
        spans = self.protected(text)
        return [w for p in tip_breaks(lines) for a, b, w in spans if a < p < b]

    def test_names_from_db(self):
        """名称表（text.tip_names）：筹码名、角色的名称和别名、地图名、畸变组件名，两个字及以上；一个字的角色名不收。"""
        for name in ("银行卡-用不完", "肾上腺素-高效", "邦妮", "蓝海晴", "Z3000", "异变图书馆", "暴雨"):
            self.assertIn(name, self.names)
        self.assertNotIn("凛", self.names)
        self.assertEqual(len(self.names), len(set(self.names)))

    def test_three_choose_one_kept(self):
        """「三选一」整体不拆（设置窗口自动模式的说明「出现筹码三选一界面时显示推荐」不排成「……三 / 选一……」）：
        卡片里自动换行的文字（keep_words）在它内部插入不换行的字符，悬停说明（tip_lines）也不从中间断开。"""
        self.assertIn("三选一", TIP_TERMS)
        old = set_tip_names(self.names)
        self.addCleanup(set_tip_names, old)
        kept = keep_words("后台持续识别游戏画面，出现筹码三选一界面时显示推荐")
        self.assertIn(f"三{KEEP_JOINER}选{KEEP_JOINER}一", kept)
        text = "手动模式：后台不截图，仅在按 F8 时识别；识别到筹码三选一界面后持续识别至界面关闭，以记录选择的筹码。"
        self.assertEqual([w for w in self.split_words(text, tip_lines(text)) if w == "三选一"], [])
        for term in ("出现率", "下次出现率", "攻击力", "生命值"):
            self.assertIn(term, TIP_TERMS)

    def test_big_cores_terms_kept(self):
        """「大小核」「受影响」整体不拆（设置窗口「只用大核」一项的说明在窄的窗口里、处理器名称长时不排成「有大小 / 核」
        「可能略受影 / 响」）：卡片里自动换行的文字插入不换行的字符，悬停说明也不从中间断开。"""
        for term in ("大小核", "受影响"):
            self.assertIn(term, TIP_TERMS)
        old = set_tip_names(self.names)
        self.addCleanup(set_tip_names, old)
        kept = keep_words("这台电脑的处理器是AMD Ryzen AI 9 HX 370 w/ Radeon 890M，有大小核\n"
                          "目前拾星助手运行在小核上，打开此选项后文字识别更快，游戏帧数可能略受影响")
        self.assertIn(f"大{KEEP_JOINER}小{KEEP_JOINER}核", kept)
        self.assertIn(f"受{KEEP_JOINER}影{KEEP_JOINER}响", kept)
        text = "这台电脑有大小核。打开后拾星助手只用大核，文字识别更快，但和游戏抢同一批核，游戏帧数可能略受影响。"
        self.assertEqual([w for w in self.split_words(text, tip_lines(text)) if w in ("大小核", "受影响")], [])

    def test_update_notice_terms_kept(self):
        """「拾星助手」「下好后自动重启换上」整体不拆（标题栏下方软件内更新的提示在窄的皮肤上不排成「下次启动拾 / 星助手」，
        「有新版本」那一句末行不只剩「上。」）：自动换行的文字（keep_words）在它们内部插入不换行的字符。"""
        from advisor.ui.overlay_parts.wording import UPDATE_AVAILABLE, UPDATE_READY
        for term in ("拾星助手", "下好后自动重启换上"):
            self.assertIn(term, TIP_TERMS)
        with tip_names_as(self.names):
            kept = keep_words(UPDATE_AVAILABLE.format(version="1.2.0", date="", size="最多约 85 MB"))
            self.assertIn(KEEP_JOINER.join("下好后自动重启换上"), kept)
            self.assertIn(KEEP_JOINER.join("拾星助手"), kept)
            self.assertIn(KEEP_JOINER.join("拾星助手"), keep_words(UPDATE_READY.format(version="1.2.0")))

    def test_scheme_tiers_kept(self):
        """「必选、推荐」整体不拆：档位前瞻的悬停整句、同一档先后的悬停（出现机会那一句）不排成「……必选、 / 推荐……」。"""
        from advisor.engine.lookahead import TIER_AHEAD_REASON
        from advisor.ui.text import MOVE_RATES
        self.assertIn("必选、推荐", TIP_TERMS)
        texts = (
            TIER_AHEAD_REASON.format(words="必选、推荐", before="12%", after="27%") + "（魔法箭袋 5% → 10%、标靶 4% → 9%）",
            MOVE_RATES.format(words="必选、推荐", before="12%",
                              choices="选「手持风扇-小」以后 27%、选「摩托头盔-中级」以后 12%"),
        )
        with tip_names_as(self.names):
            for text in texts:
                lines = tip_lines(text)
                self.assertEqual("".join(lines), text)
                self.assertEqual([w for w in self.split_words(text, lines) if w == "必选、推荐"], [], lines)

    def test_names_and_terms_kept_whole(self):
        """设置名称表后：筹码名（「银行卡-用不完」）、术语（「下次出现率」）、「X」流派（「「标记」流派」）不拆开，
        改断在附近的标点后或左括号之前；「「治愈」「标记」流派」可以断在两个引号之间。
        BANK 不分成「队友「邦妮」「Z3000」「蓝海晴」/ 的轮次奖励星币……」这样的三行（第二行以「的」开头）。"""
        with tip_names_as(self.names):
            self.assertEqual(tip_lines(self.BANK), ["队友「邦妮」「Z3000」「蓝海晴」的轮次奖励星币同样 +3",
                                                    "（银行卡-用不完对所有角色生效），真人队友可用于升星或在商店购买卡牌"])
            self.assertEqual(tip_lines(self.RATE), ["进入「星光」流派后同流派筹码出现率提高，其他高适配度筹码出现率下降；",
                                                    "拳击手套-高级下次出现率 12% → 11%；",
                                                    "星币锤下次出现率 16% → 25%"])
            self.assertEqual(tip_lines(self.SCHOOL), ["选择后锁定「充能」「星光」流派，「治愈」「标记」流派的筹码本局不再出现；",
                                                      "ATM机下次出现率 13% → 26%；",
                                                      "银行卡-余额多下次出现率 3% → 7%"])
            for text in (self.BANK, self.RATE, self.SCHOOL, self.DART):
                self.assertEqual(self.split_words(text, tip_lines(text)), [], text)
            # 附近只有名称内部可断时往外找（宁可这一行长短不一），仍不拆开名称
            same = "肾上腺素-高效" * 6
            self.assertEqual(tip_lines(same), ["肾上腺素-高效" * 3] * 2)

    def test_punctuation_first(self):
        """标点离均分点稍远也优先断在标点之后：三分之一行内找不到标点时不断在两个汉字之间
        （「返还 / 卡牌」），断在稍远的逗号之后。"""
        with tip_names_as(self.names):
            self.assertEqual(tip_lines(self.DART), ["已持有「忍术飞镖」：怪物带有标记时才返还卡牌或增加伤害，", "该筹码可对怪物施加标记"])

    def test_real_texts(self):
        """适配表里的理由、同系列的悬停说明，以及随机局面下推荐区选项块上的全部悬停说明：设置名称表后没有一处拆开名称、
        术语和「X」流派，九成五以上的断行落在标点之后或左引号、左括号之前，各行拼起来就是原句、行首不是收尾的标点，
        也不是「的」等虚词（text._SOFT_NO_START）。"""
        db = self.db
        texts = {r.reason for rules in db.trait_rules.values() for r in rules}
        texts |= {r.reason for hs in db.hero_synergy.values() for r in hs.rules}
        texts |= {r.reason for ps in db.player_synergy.values() for r in ps.rules}
        texts |= {c.reason for c in db.combos} | {t.reason for t in db.teammate_rules} | {p.tip for p in db.series}
        rng = random.Random(1003)
        heroes, chips, maps = sorted(db.heroes), sorted(db.chips), sorted(db.maps)
        for _ in range(40):
            offer = rng.sample(chips, 3)
            owned = tuple(c for c in rng.sample(chips, rng.randint(0, 6)) if c not in offer)
            st = State(hero=rng.choice(heroes), star=rng.randint(0, 3), round=rng.randint(1, 15),
                       map=rng.choice([None, *maps]), owned=owned, rerolls=rng.randint(0, 3),
                       teammates=tuple(Teammate(h) for h in rng.sample(heroes, 2)))
            for view in option_views(advise(db, st, offer)):
                texts |= {pill.tip for pill in [*view.pills, *view.compare] if pill.tip}
                if view.superseded_tip:
                    texts.add(view.superseded_tip)
        long = sorted(t for t in texts if t and "{" not in t and len(t) > TIP_LINE + 4)
        self.assertGreater(len(long), 100)  # 现在约 160 条
        marks = breaks = 0
        with tip_names_as(self.names):
            for text in long:
                lines = tip_lines(text)
                with self.subTest(text=text):
                    self.assertEqual(self.split_words(text, lines), [])
                    for b in lines[1:]:
                        self.assertNotIn(b[0], "，。；：、！？）」%")
                        self.assertNotIn(b[0], "的了着们")
                breaks += len(lines) - 1
                marks += sum(a[-1] in "，。；：、！？）」』" or b[0] in "「（" for a, b in zip(lines, lines[1:]))
        self.assertGreaterEqual(marks / breaks, 0.95, (marks, breaks))

    def test_without_names(self):
        """没设置名称表时（tip_lines 不接触资料库）名称、术语、「X」流派不受保护，其余断行规则相同（不另用一套
        规则断行）：SCHOOL 断在「「标记」/ 流派」之间（设置后不拆），不涉及名称的几句和设置后相同。
        set_tip_names 返回原来的名称表，测试用来恢复。"""
        with tip_names_as(self.names):
            named = {text: tip_lines(text) for text in (self.BANK, self.RATE, self.DART)}
        with tip_names_as(None):
            self.assertIsNone(set_tip_names(None))
            self.assertEqual(tip_lines(self.SCHOOL), ["选择后锁定「充能」「星光」流派，「治愈」「标记」",
                                                      "流派的筹码本局不再出现；ATM机下次出现率 13% → 26%；",
                                                      "银行卡-余额多下次出现率 3% → 7%"])
            self.assertEqual(self.split_words(self.SCHOOL, tip_lines(self.SCHOOL)), ["「治愈」「标记」流派"])
            for text, lines in named.items():
                self.assertEqual(tip_lines(text), lines)
            # 设置后返回原来的（None），再设置时返回上一次设置的名称表
            self.assertIsNone(set_tip_names(self.names))
            self.assertEqual(set_tip_names(None), tuple(sorted(set(self.names))))

    def test_overlay_sets_names(self):
        """悬浮窗建好资料库后设置名称表：之后的悬停说明（tip_html）按名称整体不拆断行（分行结果按名称表缓存，
        设置时清空）。"""
        with tip_names_as(None):
            self.assertNotEqual(self.split_words(self.SCHOOL, tip_lines(self.SCHOOL)), [])
            window = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock())
            try:
                self.assertEqual(self.split_words(self.SCHOOL, tip_lines(self.SCHOOL)), [])
                self.assertIn("「治愈」「标记」流派的筹码本局不再出现；<br>", tip_html(self.SCHOOL))
            finally:
                window.close()

    # 实战中出现过的两处（星币锤效果整句分成 21 / 11 / 20 字三行；刷新概率的悬停第二行以「的」开头）
    HAMMER = "获得 5 层星光；持有星币多于 20 时，攻击时花费 6 星币，本次战斗攻击力增加持有星币总数的 30%"
    CHANCE = "刷新后新的三个候选筹码中，出现优于「银行卡-余额少」的筹码的概率为 40%（不计消耗 1 次刷新的影响）。"

    def test_hammer_chance_and_path_breaks(self):
        """两行放得下就不分三行；行首不放「的」，改断在左引号之前。设置和没设置名称表时一样。
        一长串连写的路径排得下（整条说明不宽过 600 像素）时中间不断开：前面有可断处时断在那里，一处都没有时整段一行；
        太长时在文件夹分隔符「\\」处换行，字一个不少、弹出宽度不超过 600。"""
        short = r"X:\Games\AstralChipCompass\app\reports\20261002-213045"
        path = r"C:\Users\Player\OneDrive\Desktop\Tools\ACC-0.13.0\reports\20261002-213045"
        for names in (self.names, None):
            with self.subTest(names="已设置" if names else "未设置"), tip_names_as(names):
                self.assertEqual(tip_lines(self.HAMMER), ["获得 5 层星光；持有星币多于 20 时，",
                                                          "攻击时花费 6 星币，本次战斗攻击力增加持有星币总数的 30%"])
                self.assertEqual(tip_lines(self.CHANCE), ["刷新后新的三个候选筹码中，出现优于",
                                                          "「银行卡-余额少」的筹码的概率为 40%（不计消耗 1 次刷新的影响）。"])
                self.assertEqual(tip_lines(short), [short])
                self.assertEqual(tip_lines("诊断包：" + short), ["诊断包：", short])
                # 长的路径（照写一行会宽过 600 像素）：在「\」处换行（字一个不少），每一行都不宽于 600 像素
                lines = tip_lines(path)
                self.assertGreater(len(lines), 1)
                self.assertEqual("".join(lines), path)
                self.assertTrue(all(line.endswith("\\") for line in lines[:-1]))
                self.assertLessEqual(int(re.search(r'width="(\d+)"', tip_html(path)).group(1)), 600)
                # 路径前面还有字：断点落在标点之后和「\」之后，字一个不少
                self.assertTrue(tip_lines("诊断包：" + path)[0].startswith("诊断包："))
                self.assertEqual("".join(tip_lines("诊断包：" + path)), "诊断包：" + path)
                for text in (self.HAMMER, self.CHANCE):
                    self.assertTrue(all(len(line) <= TIP_LINE + 4 for line in tip_lines(text)))

    # 65～72 个字、按每行约 32 字算是三行，但断在标点之后分成两行也放得下的两句
    INCOME = ("星光收入按剩余兑现次数折算：到进度上限（16）前还有 1 次轮次奖励；"
              "已持有「金色飞星」，星光层数还会增加对首领的伤害，折算幅度减半")
    LOCK = ("选择后锁定「充能」「标记」流派，「星光」「治愈」流派的筹码本局不再出现；"
            "本局不再出现的高适配度筹码：ATM机；额外电池下次出现率 4% → 5%")

    def test_two_lines_when_they_fit(self):
        """两行放得下就两行：65～72 个字的说明断在标点之后能分成每行不超过 36 字的两行时不分三行
        （之前收入折算那句分成「……到进度上限（16）/ 前还有 1 次轮次奖励；……」23 / 22 / 21 字三行）。
        两行要超过 36 字时照旧分三行。"""
        with tip_names_as(self.names):
            self.assertEqual(tip_lines(self.INCOME), ["星光收入按剩余兑现次数折算：到进度上限（16）前还有 1 次轮次奖励；",
                                                      "已持有「金色飞星」，星光层数还会增加对首领的伤害，折算幅度减半"])
            self.assertEqual(tip_lines(self.LOCK), ["选择后锁定「充能」「标记」流派，「星光」「治愈」流派的筹码本局不再出现；",
                                                    "本局不再出现的高适配度筹码：ATM机；额外电池下次出现率 4% → 5%"])
            # 两行分不出不超过 36 字的（第二段 38 字）：照旧三行
            rate = ("进入「星光」流派后同流派筹码出现率提高，其他高适配度筹码出现率下降；"
                    "美工刀-初级下次出现率 19% → 16%；星币锤下次出现率 2% → 3%")
            self.assertEqual(len(tip_lines(rate)), 3)
            self.assertTrue(all(len(line) <= TIP_LINE + 4 for line in tip_lines(rate)))

    # 卡片里自动换行的两句（不插不换行的字符时 Qt 会拆成「打 / 开任务栏」「不 / 计前瞻」）
    NOTE = "⚠ 第 3 个候选筹码「星辰回响徽记」资料库未收录：按平均水平计算，不计前瞻"

    @staticmethod
    def qt_breaks(text: str, font: QFont, width: float) -> list[int]:
        """按 Qt 的自动换行（QLabel 的 wordWrap 用的同一套断行规则）把 text 排成 width 宽，返回各行开头的位置（第一行除外）。"""
        layout = QTextLayout(text, font)
        layout.beginLayout()
        starts = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            starts.append(line.textStart())
        layout.endLayout()
        return starts[1:]

    def test_effect_words_kept(self):
        """「已有筹码」小卡片上的筹码说明照游戏原文写，里面常见的这几个词和状态名同样整体不拆（不会拆成「拥 / 有」
        「【温 / 焰】」）。"""
        with tip_names_as(self.names):  # 状态名和别的术语一样，悬浮窗设置了名称表以后才保护
            for word in ("少于", "售价", "叠加", "目标", "拥有", "复活", "葫芦", "特殊", "温焰", "彩羽"):
                with self.subTest(word=word):
                    kept = keep_words(f"每次{word}时")
                    self.assertIn(KEEP_JOINER.join(word), kept)
                    self.assertEqual(plain_words(kept), f"每次{word}时")

    def test_card_labels_keep_words(self):
        """卡片里自动换行的提示和附注（overlay.WrapLabel，由 Qt 断行，不经过 tip_lines）：text.keep_words 在名称、术语和
        常用双字词的内部插入不显示、不占宽度的 WORD JOINER，Qt 换行时不在它两边断开，不会拆成「打 / 开任务栏」
        「不 / 计前瞻」；plain_words 取回原文。按卡片的两档字号、从窄到宽逐个宽度排一遍：没插入时确实会拆开这几个词，
        插入后一处都不拆。"""
        with tip_names_as(self.names):
            for text, words, seen in ((OPEN_TASKS, ("打开", "任务栏", "进度条", "游戏"), "打开"),
                                      (self.NOTE, ("不计", "前瞻", "资料库"), "不计")):
                kept = keep_words(text)
                self.assertEqual(plain_words(kept), text)
                self.assertEqual(keep_words(kept), kept)  # 重复调用不再插入
                for word in words:
                    self.assertIn(KEEP_JOINER.join(word), kept, word)
                inside = text.index(seen) + 1  # 「打 / 开」「不 / 计」的断开处
                for size in (FONT_S, FONT_M):
                    font = QFont(APP.font())
                    font.setPointSizeF(size)
                    fm = QFontMetrics(font)
                    self.assertEqual(fm.horizontalAdvance(kept), fm.horizontalAdvance(text))  # 不占宽度
                    split_before = False
                    for width in range(fm.horizontalAdvance("请点击游戏"), fm.horizontalAdvance(text), 2):
                        split_before |= inside in self.qt_breaks(text, font, width)
                        for p in self.qt_breaks(kept, font, width):
                            with self.subTest(text=text, size=size, width=width):
                                self.assertNotIn(KEEP_JOINER, kept[p - 1:p + 1])  # 不在插入的字符两边断开
                    self.assertTrue(split_before, (text, size))  # 没插入时，有的宽度下确实会拆开
        # 悬浮窗里的这几种标签：显示时带 WORD JOINER，text() 照旧是原文
        with tip_names_as(self.names):
            label = _text_label(self.NOTE, FONT_S, DIM_COLOR, wrap=True)
            self.assertIsInstance(label, WrapLabel)
            self.assertEqual(label.text(), self.NOTE)
            self.assertIn(KEEP_JOINER, QLabel.text(label))
            self.assertNotIsInstance(_text_label("推荐", FONT_S), WrapLabel)  # 不换行的标签照旧
            window = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock())
            try:
                for w in (window.map_prompt, window.missed_label, window.recog_msg):
                    self.assertIsInstance(w, WrapLabel)
                window.map_prompt.setText(OPEN_TASKS)
                self.assertEqual(window.map_prompt.text(), OPEN_TASKS)
                self.assertIn("打" + KEEP_JOINER + "开", QLabel.text(window.map_prompt))
            finally:
                window.close()

    # 换行用的样本：一段夹着长串英文的文字（界面上不显示报错原文；这里只拿这种长串英文核对换行）
    ERROR = "识别出错：OpenCV(4.10.0) error: (-215:Assertion failed) !_src.empty() in function 'cvtColor'"

    def test_english_message_still_wraps(self):
        """不换行空格只放在挨着数字、数值符号的空格上：两边都是字母的空格（英文）照旧可以断开。
        之前挨着字母的空格也换成不换行空格，整段英文连成一个词，提示行的最小宽度被撑到 500 多像素，卡片跟着变宽。"""
        kept = keep_words(self.ERROR)
        self.assertEqual(plain_words(kept), self.ERROR)
        self.assertIn("Assertion failed", kept)  # 字母之间的空格不换
        self.assertNotIn(KEEP_SPACE, kept.split("(-215")[1])
        self.assertEqual(keep_words("持有星币多于 20 时，攻击力 +2；（防御 -2）；心智 ≥3").count(KEEP_SPACE), 5)
        width = 320
        label = WrapLabel(self.ERROR)
        self.assertLessEqual(label.minimumSizeHint().width(), width)
        font = QFont(label.font())
        layout = QTextLayout(kept, font)
        layout.beginLayout()
        widest = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            widest = max(widest, line.naturalTextWidth())
        layout.endLayout()
        self.assertLessEqual(widest, width)


if __name__ == "__main__":
    unittest.main()
