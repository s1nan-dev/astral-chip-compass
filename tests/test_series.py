"""同系列筹码的上位替代和对比行（data/series.toml、data.SeriesPair、data.strict_problems、engine.advice.mark_series），
以及检查数据时同系列筹码表的完整性检查（tools/check_data.py 的 series_problems）。

同系列同时出现时，严格上位替代的低一级不参与推荐度，
显示「被X上位替代」；对比行只写「比X多……」，例如紫色下面写「比金色多 4 层星光」。
严格上位替代的资料条件是标签不计「通用」（「通用」不算职业，要求标签完全相同会漏掉手持风扇-小 / -大）、流派相同、
高一级没有低一级没有的职业分值；手持风扇-小 / -大算，夹心饼干-美味（多出攻击的职业分值）和瞄具（各有所长）不算，
紫色、金色飞星算。每一对写明算或不算的理由；图标相同的筹码两两都要写进表，漏写一对或漏写理由时检查数据报错。
20 对里 15 对严格上位替代、5 对不算（银行卡-余额少 / -余额多对 -用不完、夹心饼干-可口 / -一般对 -美味、普通 / 鹰眼瞄具）。
只在本次三选一里调整：各候选的分值、前瞻和刷新的期望不变。
"""
import dataclasses
import io
import json
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from advisor import cli, paths
from advisor.data import (
    GENERAL_TAG, LEVEL_NORMAL, NORMAL, RARITIES, RARITY_WORD, STARLIGHT_USE, SeriesPair, load_db, strict_problems,
)
from advisor.engine import LOW_NOTE, REROLL, STARLIGHT_NO_USE, Scorer, advise, compare_text, shown_percents, tie_key
from advisor.pool import State, Teammate
from tests.test_engine import BANNED_WORDING, SCORE_WORDING
from tools import check_data

ROOT = Path(__file__).resolve().parents[1]

# 名单连同理由一起核对：每一对的理由（series.toml 的 reason）以「算：」或「不算：」开头，并且说到这里写的要点
STRICT = {
    ("银行卡-余额少", "银行卡-余额多"): "星光更多（4 层 → 7 层）",
    ("摩托头盔-一般", "摩托头盔-中级"): "防御力加成更高（+1 → +2）",
    ("摩托头盔-中级", "摩托头盔-高级"): "防御力加成更高（+2 → +3）",
    ("摩托头盔-一般", "摩托头盔-高级"): "防御力加成更高（+1 → +3）",
    ("速度轮滑-初级", "速度轮滑-中级"): "移动加成更多（+1 → +2）",
    ("速度轮滑-中级", "速度轮滑-高级"): "移动加成更多（+2 → +4）",
    ("速度轮滑-初级", "速度轮滑-高级"): "移动加成更多（+1 → +4）",
    ("拳击手套-初级", "拳击手套-中级"): "攻击力加成更高（+1 → +2）",
    ("拳击手套-中级", "拳击手套-高级"): "攻击力加成更高（+2 → +5）",
    ("拳击手套-初级", "拳击手套-高级"): "攻击力加成更高（+1 → +5）",
    ("夹心饼干-一般", "夹心饼干-可口"): "最大生命值加成更高（+2 → +3）",
    ("医疗箱-紧急治疗", "医疗箱-完备治疗"): "治愈更多（1 层 → 3 层）",
    ("美工刀-初级", "美工刀-锋利"): "攻击力加成更高（+2 → +4）",
    ("紫色飞星", "金色飞星"): "效果明显更强",
    ("手持风扇-小", "手持风扇-大"): "包含小的效果，另获得 1 张卡牌",
}
NOT_STRICT = {
    ("银行卡-余额少", "银行卡-用不完"): "低一级的星光更多（4 层对 3 层）",
    ("银行卡-余额多", "银行卡-用不完"): "低一级的星光更多（7 层对 3 层）",
    ("夹心饼干-可口", "夹心饼干-美味"): "多出攻击的职业分值",
    ("夹心饼干-一般", "夹心饼干-美味"): "多出攻击的职业分值",
    ("普通瞄具", "鹰眼瞄具"): "各有所长",
}
# 不算上位替代的 5 对里，资料条件（strict_problems）就不满足的：夹心饼干-美味多出攻击的职业分值。
# 其余 3 对资料条件满足，是按效果判断不算（银行卡低一级星光更多、瞄具各有所长）
BLOCKED_BY_DATA = {("夹心饼干-可口", "夹心饼干-美味"), ("夹心饼干-一般", "夹心饼干-美味")}
# 实测一局（阿兰娜）的最后一次三选一，照推荐日志的局面；手持风扇-小 / -大不算上位替代时推荐度为医疗箱-紧急治疗 63%、
# 手持风扇-大 28%、手持风扇-小 9%
ALANA_LAST = dict(
    hero="阿兰娜", star=3, round=8, map="异变图书馆", owned=("速度轮滑-中级", "鹰眼瞄具", "美工刀-初级", "忍术飞镖", "缓冲盾牌"),
    source="task", tier=2, rerolls=0, probabilities=(40, 40, 20), left_rarity="蓝",
    teammates=(Teammate("潘大猛", bot=True), Teammate("派德曼", bot=True), Teammate("帕露南", bot=True)),
    difficulty=NORMAL, progress=9, difficulty_level=LEVEL_NORMAL, difficulty_name="普通",
)
ALANA_OFFER = ["医疗箱-紧急治疗", "手持风扇-大", "手持风扇-小"]
ALANA_RARITY = ["蓝", "紫", "蓝"]
# 数字和汉字之间要有半角空格
CJK_DIGIT = re.compile(r"[一-鿿][0-9]|[0-9][一-鿿]")

# 10-03 真人对局 130853-6 的局面（阿尔、异变图书馆、三名真人队友；同 tests/test_engine.py AlLiveReviewTest.OFFERS）
AL_130853 = dict(
    hero="阿尔", map="异变图书馆", teammates=(Teammate("帕露南"), Teammate("茉莉"), Teammate("邦妮")),
    owned=("“8面”骰子", "ATM机", "摩托头盔-高级", "友情徽章"), star=2, round=7, source="task", tier=2, rerolls=3,
    probabilities=(40, 45, 15), left_rarity="金", progress=7,
)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()
        cls.plain = dataclasses.replace(cls.db, series=())  # 没有同系列筹码表时（不标上位替代）

    def pair(self, low, high) -> SeriesPair:
        return next(p for p in self.db.series if (p.low, p.high) == (low, high))

    def by_name(self, adv):
        return {e.chip.name: e for e in adv.evals}

    def options(self, adv) -> list[float]:
        return [e.percent for e in adv.evals] + ([adv.reroll.percent] if adv.reroll else [])

    def copy_data(self, series_text: str | None) -> Path:
        """把 data/ 复制到程序目录的临时文件夹里，换上给定的 series.toml（None = 没有这个文件）。"""
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in ("chips.json", "heroes.json", "rules.toml", "synergy.toml", "maps.json", "mutators.json", "meta.json"):
            shutil.copy(paths.data_dir() / name, tmp / name)
        if series_text is not None:
            (tmp / "series.toml").write_text(series_text, encoding="utf-8")
        return tmp


def series_text(pairs) -> str:
    """按（low, high, strict, reason, low_gain, high_gain）写一份 series.toml；reason 为 None 时不写这一项。"""
    out = []
    for low, high, strict, reason, low_gain, high_gain in pairs:
        lines = [f'low = "{low}"', f'high = "{high}"', f"strict = {'true' if strict else 'false'}"]
        if reason is not None:
            lines.append(f'reason = "{reason}"')
        lines += [f'{k} = "{v}"' for k, v in (("low_gain", low_gain), ("high_gain", high_gain)) if v]
        out.append("[[pair]]\n" + "\n".join(lines) + '\ntip = "x。"\n')
    return "\n".join(out)


# ------------------------------------------------------------ 资料：data/series.toml


class SeriesTableTest(Base):
    def test_pairs(self):
        got = [(p.low, p.high) for p in self.db.series]
        self.assertEqual(len(got), 20)
        self.assertEqual(len(set(got)), 20)
        self.assertEqual({(p.low, p.high) for p in self.db.series if p.strict}, set(STRICT))
        self.assertEqual({(p.low, p.high) for p in self.db.series if not p.strict}, set(NOT_STRICT))
        self.assertEqual((len(STRICT), len(NOT_STRICT)), (15, 5))
        self.assertFalse([w for w in self.db.warnings if "同系列" in w])

    def test_reasons(self):
        """每一对都写了理由：算的以「算：」开头、不算的以「不算：」开头，并说到名单里写的要点（名单连理由一起核对）。"""
        for p in self.db.series:
            key = (p.low, p.high)
            want = STRICT[key] if p.strict else NOT_STRICT[key]
            with self.subTest(pair=key):
                self.assertTrue(p.reason.startswith("算：" if p.strict else "不算："), p.reason)
                self.assertIn(want, p.reason)
                self.assertNotIn("\n", p.reason)
                self.assertIsNone(BANNED_WORDING.search(p.reason), p.reason)
                self.assertIsNone(CJK_DIGIT.search(p.reason), p.reason)
        # 判断规矩的几处要点：手持风扇按「通用」不算职业，算上位替代；飞星职业分值不同也算
        fan = self.pair("手持风扇-小", "手持风扇-大").reason
        self.assertIn("「通用」不是职业", fan)
        self.assertIn("同属标记流派，都没有职业分值", fan)
        stars = self.pair("紫色飞星", "金色飞星").reason
        self.assertIn("职业分值都只有辅助（紫色 50、金色 30", stars)
        self.assertIn("累加的影响游戏里还没有实测", stars)

    def test_chips_and_rarity(self):
        # 筹码都在筹码表里，稀有度由低到高，两张的图标相同（同一系列）；严格上位替代的两张满足资料条件（strict_problems）
        rank = {r: i for i, r in enumerate(RARITIES)}
        for p in self.db.series:
            a, b = self.db.chips[p.low], self.db.chips[p.high]
            with self.subTest(pair=(p.low, p.high)):
                self.assertLess(rank[a.rarity], rank[b.rarity])
                self.assertEqual(a.icon, b.icon)
                if p.strict:
                    self.assertEqual(strict_problems(a, b), [])
                    self.assertEqual(a.school, b.school)
        # 不算上位替代的 5 对里，资料条件就不满足的只有夹心饼干-美味的两对（多出攻击的职业分值，标签也多一个「攻击」）；
        # 银行卡、瞄具满足资料条件，是按效果判断不算
        blocked = {(p.low, p.high) for p in self.db.series if not p.strict
                   and strict_problems(self.db.chips[p.low], self.db.chips[p.high])}
        self.assertEqual(blocked, BLOCKED_BY_DATA)
        chips = self.db.chips
        for low in ("夹心饼干-可口", "夹心饼干-一般"):
            self.assertEqual(strict_problems(chips[low], chips["夹心饼干-美味"]), ["高一级多出职业分值「攻击」"])
        self.assertEqual(set(chips["夹心饼干-美味"].tags) - set(chips["夹心饼干-可口"].tags), {"攻击"})
        # 手持风扇两张标签不同（「通用」「卡牌」），不计「通用」后小的标签大都有：算
        self.assertEqual((chips["手持风扇-小"].tags, chips["手持风扇-大"].tags), ((GENERAL_TAG,), ("卡牌",)))
        self.assertEqual(strict_problems(chips["手持风扇-小"], chips["手持风扇-大"]), [])

    def test_texts(self):
        for p in self.db.series:
            with self.subTest(pair=(p.low, p.high)):
                if p.strict:
                    self.assertEqual(p.low_gain, "")
                    self.assertTrue(p.high_gain)
                self.assertTrue(p.low_gain or p.high_gain)
                self.assertTrue(p.tip.endswith("。"), p.tip)
                for text in (p.low_gain, p.high_gain, p.tip):
                    self.assertFalse(text.startswith("比"), text)
                    self.assertIsNone(SCORE_WORDING.search(text), text)
                    self.assertIsNone(BANNED_WORDING.search(text), text)
                    self.assertIsNone(CJK_DIGIT.search(text), text)
                    self.assertNotIn("上位替换", text)
                # 悬停说明对照两张的效果：两张的筹码名（或「两张」）都出现
                self.assertTrue(p.low in p.tip or "两张" in p.tip, p.tip)

    def test_compare_text_examples(self):
        # 对比行的例子：紫色下面写「比金色多 4 层星光」，金色下面写「比紫色多全队轮次星币 +3」
        p = self.pair("银行卡-余额多", "银行卡-用不完")
        self.assertEqual(compare_text(RARITY_WORD["金"], p.low_gain), "比金色多 4 层星光")
        self.assertEqual(compare_text(RARITY_WORD["紫"], p.high_gain), "比紫色多全队轮次星币 +3")
        self.assertEqual(compare_text("蓝色", self.pair("银行卡-余额少", "银行卡-余额多").high_gain), "比蓝色多 3 层星光")
        self.assertEqual(compare_text("紫色", self.pair("摩托头盔-中级", "摩托头盔-高级").high_gain), "比紫色多防御力 +1")
        self.assertEqual(RARITY_WORD, {"蓝": "蓝色", "紫": "紫色", "金": "金色"})

    def test_al_starlight_six(self):
        # 阿尔的被动按每 6 层星光写
        text = (ROOT / "data" / "series.toml").read_text(encoding="utf-8")
        self.assertNotIn("7 层星光攻击力", text)
        self.assertIn("阿尔每 6 层星光", text)

    def test_player_tiers_follow_series(self):
        # 玩家思路里（适配表里写了的，和测试用的默认推荐档位），严格上位替代的高一级不低于低一级（低一级列入了玩家思路时，
        # 高一级也列入，并且分值不低）
        from tests.support.player_layer import with_player_layer
        db = with_player_layer(self.db)
        for hero in db.player_synergy:
            scorer = Scorer(db, State(hero=hero))
            for p in self.db.series:
                if not p.strict:
                    continue
                low = scorer.player_score(p.low, (), None)
                high = scorer.player_score(p.high, (), None)
                if low is None:
                    continue
                with self.subTest(hero=hero, pair=(p.low, p.high)):
                    self.assertIsNotNone(high)
                    self.assertGreaterEqual(high, low)


class SeriesLoadingTest(Base):
    """series.toml 写得不对时只记警告、忽略写错的那一对，不当严重错误（DB.problems 为空）。"""

    def test_missing_file(self):
        db = load_db(str(self.copy_data(None)))
        self.assertEqual(db.series, ())
        self.assertEqual(db.problems, [])
        self.assertFalse([w for w in db.warnings if "同系列" in w])
        # 没有同系列筹码表时照常推荐，不标上位替代
        adv = advise(db, State(hero="凛", star=1, rerolls=2), ["摩托头盔-一般", "摩托头盔-中级", "书签"])
        self.assertTrue(all(e.superseded_by is None and e.versus == [] for e in adv.evals))
        self.assertAlmostEqual(sum(self.options(adv)), 1.0)

    def test_format_error(self):
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data("[[pair]\nlow = ")))
        self.assertEqual(db.series, ())
        self.assertEqual(db.problems, [])
        self.assertTrue(any(w.startswith("同系列筹码表（series.toml）格式错误") for w in db.warnings))

    def test_bad_pairs_skipped(self):
        good = '[[pair]]\nlow = "摩托头盔-一般"\nhigh = "摩托头盔-中级"\nstrict = true\nhigh_gain = "防御力 +1"\ntip = "x。"\n'
        bad = [
            ('low = "不存在的筹码"\nhigh = "摩托头盔-高级"\nstrict = true\nhigh_gain = "a"', "不存在"),
            ('low = "摩托头盔-高级"\nhigh = "摩托头盔-中级"\nstrict = true\nhigh_gain = "a"', "稀有度应由低到高"),
            ('low = "摩托头盔-中级"\nhigh = "摩托头盔-中级"\nstrict = true\nhigh_gain = "a"', "同一个筹码"),
            ('low = "夹心饼干-可口"\nhigh = "夹心饼干-美味"\nstrict = true\nhigh_gain = "a"',
             "不能算严格上位替代（高一级多出职业分值「攻击」）"),
            ('low = "手持风扇-小"\nhigh = "医疗箱-完备治疗"\nstrict = true\nhigh_gain = "a"',
             "不能算严格上位替代（流派不同（标记 / 治愈）；高一级多出职业分值「生存」）"),
            ('low = "摩托头盔-一般"\nhigh = "速度轮滑-高级"\nstrict = true\nhigh_gain = "a"',
             "不能算严格上位替代（低一级的标签「生存」高一级没有）"),
            ('low = "普通瞄具"\nhigh = "鹰眼瞄具"\nstrict = false\nlow_gain = "a"\nreason = 3', "应为文字"),
            ('low = "拳击手套-初级"\nhigh = "拳击手套-中级"\nstrict = true\nlow_gain = "a"\nhigh_gain = "b"', "不应写 low_gain"),
            ('low = "拳击手套-初级"\nhigh = "拳击手套-高级"\nstrict = true', "都是空的"),
            ('low = "普通瞄具"\nhigh = "鹰眼瞄具"\nstrict = false\nlow_gain = "比金色多攻击力 +2"', "不以「比」开头"),
            ('low = "手持风扇-小"\nhigh = "手持风扇-大"\nhigh_gain = "a"', "strict 应为 true 或 false"),
            ('low = "手持风扇-小"\nhigh = "手持风扇-大"\nstrict = false\nhigh_gain = 3', "应为文字"),
            ('low = "摩托头盔-一般"\nhigh = "摩托头盔-中级"\nstrict = true\nhigh_gain = "防御力 +1"', "重复的一对"),
        ]
        text = good + "".join(f"\n[[pair]]\n{body}\n" for body, _ in bad)
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(text)))
        self.assertEqual(db.problems, [])
        self.assertEqual([(p.low, p.high, p.strict, p.high_gain) for p in db.series],
                         [("摩托头盔-一般", "摩托头盔-中级", True, "防御力 +1")])
        warnings = [w for w in db.warnings if w.startswith("同系列筹码表")]
        self.assertEqual(len(warnings), len(bad), warnings)
        for (_, key), w in zip(bad, warnings):
            self.assertIn(key, w)

    def test_strict_chain_must_be_complete(self):
        # 一般 < 中级、中级 < 高级都是严格上位替代时，一般 < 高级 也要写：缺了记警告（两对照常读入）。
        # 三张同时出现时一般记为被中级替代（引擎不出错，只是悬停说明不是一般 / 高级这一对）
        text = "".join(
            f'[[pair]]\nlow = "{a}"\nhigh = "{b}"\nstrict = true\nhigh_gain = "防御力 +1"\ntip = "x。"\n\n'
            for a, b in (("摩托头盔-一般", "摩托头盔-中级"), ("摩托头盔-中级", "摩托头盔-高级"))
        )
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(text)))
        self.assertEqual(len(db.series), 2)
        self.assertIn("同系列筹码表：摩托头盔-一般 / 摩托头盔-中级、摩托头盔-中级 / 摩托头盔-高级 都是严格上位替代，"
                      "缺少 摩托头盔-一般 / 摩托头盔-高级 这一对", db.warnings)
        adv = advise(db, State(hero="凛", star=1, rerolls=2), ["摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"])
        self.assertEqual([e.superseded_by for e in adv.evals], ["摩托头盔-中级", "摩托头盔-高级", None])
        self.assertAlmostEqual(adv.evals[2].percent + adv.reroll.percent, 1.0)
        # 完整的资料没有这条警告
        self.assertFalse([w for w in self.db.warnings if "缺少" in w and "这一对" in w])

    def test_unknown_field_warns_but_keeps_pair(self):
        text = ('[[pair]]\nlow = "普通瞄具"\nhigh = "鹰眼瞄具"\nstrict = false\nreason = "不算：各有所长"\n'
                'low_gain = "攻击力 +2"\nhigh_gian = "x"\n')
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(text)))
        self.assertEqual([(p.low, p.low_gain, p.high_gain, p.reason) for p in db.series],
                         [("普通瞄具", "攻击力 +2", "", "不算：各有所长")])
        self.assertTrue(any("high_gian" in w for w in db.warnings))
        self.assertFalse([w for w in db.warnings if "reason" in w])  # reason 是认得的字段

    def test_reason_optional_when_loading(self):
        # 载入时 reason 可以不写（检查数据时才要求每一对都写，见 SeriesBuildCheckTest），不记警告
        text = '[[pair]]\nlow = "摩托头盔-一般"\nhigh = "摩托头盔-中级"\nstrict = true\nhigh_gain = "防御力 +1"\ntip = "x。"\n'
        db = load_db(str(self.copy_data(text)))
        self.assertEqual([(p.low, p.strict, p.reason) for p in db.series], [("摩托头盔-一般", True, "")])
        self.assertFalse([w for w in db.warnings if w.startswith("同系列")])

    def test_starlight_uses_loading(self):
        """[[starlight_use]]（同系列悬停说明里「星光层数另有用处」的写法）：写得不对的一条记警告后忽略；tip 里写了
        {starlight_use} 以外的占位符时记警告，那一对照常读入。"""
        pair = ('[[pair]]\nlow = "银行卡-余额多"\nhigh = "银行卡-用不完"\nstrict = false\nlow_gain = "4 层星光"\n'
                'tip = "x。{starlight_use}，y。{别的}"\n')
        uses = [
            ('heroes = ["阿尔"]\ntext = "阿尔的用处"', None),
            ('owned = ["金色飞星"]\ntext = "金色飞星的用处"', None),
            ('owned = ["不存在的筹码"]\ntext = "a"', "不存在"),
            ('heroes = ["不存在的角色"]\ntext = "a"', "不存在"),
            ('owned = ["金色飞星"]', "text 应为文字"),
            ('owned = "金色飞星"\ntext = "a"', "名称列表"),
        ]
        text = pair + "".join(f"\n[[starlight_use]]\n{body}\n" for body, _ in uses)
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(text)))
        self.assertEqual(db.problems, [])
        self.assertEqual([(u.text, set(u.heroes), set(u.owned)) for u in db.starlight_uses],
                         [("阿尔的用处", {"阿尔"}, set()), ("金色飞星的用处", set(), {"金色飞星"})])
        self.assertEqual(len(db.series), 1)
        warnings = [w for w in db.warnings if w.startswith("同系列筹码表")]
        self.assertTrue(any("「{别的}」不会被替换" in w for w in warnings), warnings)
        bad = [key for _, key in uses if key]
        use_warnings = [w for w in warnings if "星光用处" in w]
        self.assertEqual(len(use_warnings), len(bad), use_warnings)
        for key, w in zip(bad, use_warnings):
            self.assertIn(key, w)

    def test_starlight_use_single_table_warns(self):
        # 误写成单个 [starlight_use] 表（不是 [[starlight_use]] 段落）：整段忽略并记警告，同系列的对照常读入
        pair = ('[[pair]]\nlow = "银行卡-余额多"\nhigh = "银行卡-用不完"\nstrict = false\nlow_gain = "4 层星光"\n'
                'tip = "x。{starlight_use}，y。"\n')
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(pair + '\n[starlight_use]\nheroes = ["阿尔"]\ntext = "阿尔的用处"\n')))
        self.assertEqual(db.problems, [])
        self.assertEqual(db.starlight_uses, ())
        self.assertEqual(len(db.series), 1)
        self.assertIn("同系列筹码表（series.toml）：starlight_use 应写成 [[starlight_use]] 段落，已忽略", db.warnings)

    def test_current_data_uses(self):
        # 现行资料：阿尔（星光换成属性）、已持有金色飞星、已持有手电筒-强光三种用处，银行卡的两对 tip 都写了占位符
        self.assertEqual([(set(u.heroes), set(u.owned)) for u in self.db.starlight_uses],
                         [({"阿尔"}, set()), (set(), {"金色飞星"}), (set(), {"手电筒-强光"})])
        with_use = [p for p in self.db.series if STARLIGHT_USE in p.tip]
        self.assertEqual({(p.low, p.high) for p in with_use},
                         {("银行卡-余额少", "银行卡-用不完"), ("银行卡-余额多", "银行卡-用不完")})
        self.assertFalse([w for w in self.db.warnings if "星光用处" in w or "不会被替换" in w])
        for u in self.db.starlight_uses:
            with self.subTest(text=u.text):
                self.assertIsNone(BANNED_WORDING.search(u.text))
                self.assertIsNone(SCORE_WORDING.search(u.text))
                self.assertIsNone(CJK_DIGIT.search(u.text))


# ------------------------------------------------------------ 严格上位替代的资料条件：data.strict_problems


class StrictRuleTest(Base):
    """严格上位替代的资料条件：标签不计「通用」时
    低一级的标签高一级都有，流派相同，高一级没有低一级没有的职业分值。高一级的效果是否包含低一级的全部效果由
    series.toml 的 strict 写明，这三条只是必要条件。"""

    def chip(self, name: str, **changes):
        return dataclasses.replace(self.db.chips[name], **changes)

    def test_tags(self):
        c = self.db.chips
        # 手持风扇：小「通用」、大「卡牌」，不计「通用」后小没有职业标签：满足
        self.assertEqual(strict_problems(c["手持风扇-小"], c["手持风扇-大"]), [])
        # 反过来（低一级有职业标签，高一级只有「通用」）不满足：游戏内置推荐的标签分低一级有、高一级没有
        self.assertEqual(strict_problems(self.chip("手持风扇-小", tags=("卡牌",)), self.chip("手持风扇-大", tags=(GENERAL_TAG,))),
                         ["低一级的标签「卡牌」高一级没有"])
        # 高一级多出职业标签不要紧（标签分只会多给高一级）；两张都是「通用」也满足
        self.assertEqual(strict_problems(c["摩托头盔-一般"], self.chip("摩托头盔-高级", tags=("生存", "卡牌"))), [])
        self.assertEqual(strict_problems(c["速度轮滑-初级"], c["速度轮滑-高级"]), [])
        # 职业标签不同（都不是「通用」）不满足
        self.assertEqual(strict_problems(c["摩托头盔-一般"], self.chip("摩托头盔-高级", tags=("攻击",))),
                         ["低一级的标签「生存」高一级没有"])

    def test_school(self):
        c = self.db.chips
        self.assertEqual(strict_problems(c["医疗箱-紧急治疗"], self.chip("医疗箱-完备治疗", school="星光")),
                         ["流派不同（治愈 / 星光）"])
        self.assertEqual(strict_problems(c["手持风扇-小"], self.chip("手持风扇-大", school=None)), ["流派不同（标记 / 无流派）"])
        self.assertEqual(strict_problems(c["摩托头盔-一般"], c["摩托头盔-高级"]), [])  # 都不属于流派

    def test_class_points(self):
        c = self.db.chips
        # 同一个职业、分值不同照算：紫色飞星辅助 50、金色飞星辅助 30（职业分值累加的影响游戏里还没有实测）；拳击手套攻击 15 → 30
        self.assertEqual((c["紫色飞星"].class_points, c["金色飞星"].class_points), ({"辅助": 50}, {"辅助": 30}))
        self.assertEqual(strict_problems(c["紫色飞星"], c["金色飞星"]), [])
        self.assertEqual(strict_problems(c["拳击手套-初级"], c["拳击手套-高级"]), [])
        # 高一级多出职业分值不满足（夹心饼干-美味多出攻击）；低一级多出的不要紧；分值为 0 的当作没有
        self.assertEqual(strict_problems(c["夹心饼干-一般"], c["夹心饼干-美味"]), ["高一级多出职业分值「攻击」"])
        self.assertEqual(strict_problems(self.chip("拳击手套-初级", class_points={"攻击": 15, "卡牌": 10}), c["拳击手套-高级"]),
                         [])
        self.assertEqual(strict_problems(c["摩托头盔-一般"], self.chip("摩托头盔-高级", class_points={"生存": 0})), [])

    def test_all_problems_listed(self):
        # 三条都不满足时按标签、流派、职业分值的顺序都列出来
        c = self.db.chips
        self.assertEqual(strict_problems(c["银行卡-余额少"], c["医疗箱-完备治疗"]),
                         ["低一级的标签「辅助」高一级没有", "流派不同（星光 / 治愈）", "高一级多出职业分值「生存」"])

    def test_table_against_rule(self):
        """表里每一对和资料条件的交叉：算的 15 对都满足；不算的 5 对里资料条件不满足的只有夹心饼干-美味两对，
        银行卡、瞄具满足资料条件，是按效果判断不算（理由写效果上的差别）。"""
        for p in self.db.series:
            problems = strict_problems(self.db.chips[p.low], self.db.chips[p.high])
            with self.subTest(pair=(p.low, p.high)):
                if p.strict:
                    self.assertEqual(problems, [])
                elif (p.low, p.high) in BLOCKED_BY_DATA:
                    self.assertEqual(problems, ["高一级多出职业分值「攻击」"])
                    self.assertIn("多出攻击的职业分值", p.reason)
                else:
                    self.assertEqual(problems, [])
                    self.assertTrue(any(w in p.reason for w in ("星光更多", "各有所长")), p.reason)

    def test_all_marked_strict(self):
        """极端配置：20 对全写成严格上位替代（去掉 low_gain）。只有资料条件不满足的夹心饼干-美味两对被忽略并记警告；
        其余的照表上写的算（资料条件只是必要条件，效果由表上判断）。"""
        pairs = [(p.low, p.high, True, p.reason, "", p.high_gain or p.low_gain) for p in self.db.series]
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(series_text(pairs))))
        self.assertEqual(db.problems, [])
        self.assertEqual({(p.low, p.high) for p in db.series}, set(STRICT) | (set(NOT_STRICT) - BLOCKED_BY_DATA))
        self.assertTrue(all(p.strict for p in db.series))
        warnings = [w for w in db.warnings if w.startswith("同系列")]
        self.assertEqual(sorted(warnings), sorted(
            f"同系列筹码表第 {i} 对（{lo} / {hi}）：不能算严格上位替代（高一级多出职业分值「攻击」），已忽略"
            for i, (lo, hi, *_) in enumerate(pairs, 1) if (lo, hi) in BLOCKED_BY_DATA))
        # 瞄具按表上写的算：普通瞄具不参与比较
        adv = advise(db, State(hero="南希露", star=1, rerolls=3), ["普通瞄具", "鹰眼瞄具", "书签"])
        self.assertEqual(adv.evals[0].superseded_by, "鹰眼瞄具")

    def test_none_marked_strict(self):
        """极端配置：20 对全写成不算。不记警告，同系列两张都参与推荐度的比较，手持风扇两张都有推荐度。"""
        pairs = [(p.low, p.high, False, p.reason, p.low_gain, p.high_gain) for p in self.db.series]
        db = load_db(str(self.copy_data(series_text(pairs))))
        self.assertEqual(len(db.series), 20)
        self.assertFalse([w for w in db.warnings if w.startswith("同系列")])
        adv = advise(db, State(**ALANA_LAST), ALANA_OFFER, rarity=ALANA_RARITY)
        self.assertTrue(all(e.superseded_by is None for e in adv.evals))
        self.assertTrue(all(e.percent > 0 for e in adv.evals))

    def test_contradictory_marks(self):
        """互相矛盾的写法：同一对写两次（先算后不算）时后一条忽略；三张一组里一般 / 中级、中级 / 高级算，一般 / 高级
        却写成不算时记警告（不说「缺少」）。"""
        pairs = [
            ("手持风扇-小", "手持风扇-大", True, "算：x", "", "使用主动技能后获得 1 张卡牌"),
            ("手持风扇-小", "手持风扇-大", False, "不算：x", "", "使用主动技能后获得 1 张卡牌"),
            ("摩托头盔-一般", "摩托头盔-中级", True, "算：x", "", "防御力 +1"),
            ("摩托头盔-中级", "摩托头盔-高级", True, "算：x", "", "防御力 +1"),
            ("摩托头盔-一般", "摩托头盔-高级", False, "不算：x", "", "防御力 +2"),
        ]
        with self.assertLogs("advisor.data", level="WARNING"):
            db = load_db(str(self.copy_data(series_text(pairs))))
        self.assertEqual([(p.low, p.high, p.strict) for p in db.series], [
            ("手持风扇-小", "手持风扇-大", True), ("摩托头盔-一般", "摩托头盔-中级", True),
            ("摩托头盔-中级", "摩托头盔-高级", True), ("摩托头盔-一般", "摩托头盔-高级", False)])
        self.assertEqual([w for w in db.warnings if w.startswith("同系列")], [
            "同系列筹码表第 2 对（手持风扇-小 / 手持风扇-大）：重复的一对，已忽略",
            "同系列筹码表：摩托头盔-一般 / 摩托头盔-中级、摩托头盔-中级 / 摩托头盔-高级 都是严格上位替代，"
            "摩托头盔-一般 / 摩托头盔-高级 也应写成严格上位替代",
        ])


# ------------------------------------------------------------ 检查数据时的核对：tools/check_data.py


class SeriesBuildCheckTest(Base):
    """检查数据时同系列筹码表要写全（tools/check_data.py 的 series_problems）：图标相同的筹码两两都要写一对
    （不分哪张写在 low），每一对都要写理由（reason）；漏写时报出来并列出全部要补的地方。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chips = json.loads((paths.data_dir() / "chips.json").read_text(encoding="utf-8"))["chips"]
        cls.pairs = [(p.low, p.high, p.strict, p.reason, p.low_gain, p.high_gain) for p in cls.db.series]

    def write(self, text: str | None) -> Path:
        paths.temp_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=paths.temp_dir()))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "series.toml"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        return path

    def test_current_table_complete(self):
        # 随程序发布的表：10 个系列（图标相同的筹码）、20 对，都写了理由；表里的对就是图标相同的两两组合，不多不少
        self.assertEqual(check_data.SERIES, paths.data_dir() / "series.toml")
        self.assertEqual(check_data.series_problems(self.chips), [])
        icons: dict[str, set] = {}
        for c in self.chips:
            icons.setdefault(c["icon"], set()).add(c["name"])
        same_icon = {frozenset((a, b)) for names in icons.values() for a in names for b in names if a != b}
        self.assertEqual(same_icon, {frozenset((p.low, p.high)) for p in self.db.series})

    def test_missing_pair(self):
        pairs = [p for p in self.pairs if p[:2] != ("手持风扇-小", "手持风扇-大")]
        path = self.write(series_text(pairs))
        problems = check_data.series_problems(self.chips, path)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("缺少「手持风扇-小」/「手持风扇-大」这一对（图标 50030 相同）", problems[0])

    def test_missing_reason(self):
        # 没写 reason、只写了空白的，都写明是第几对、哪两张
        index = {p[:2]: i for i, p in enumerate(self.pairs, 1)}
        blank = {("普通瞄具", "鹰眼瞄具"): None, ("紫色飞星", "金色飞星"): "  "}
        pairs = [(lo, hi, st, blank.get((lo, hi), r), lg, hg) for lo, hi, st, r, lg, hg in self.pairs]
        path = self.write(series_text(pairs))
        problems = check_data.series_problems(self.chips, path)
        self.assertEqual(len(problems), len(blank), problems)
        for k in blank:
            self.assertTrue(any(f"第 {index[k]} 对" in p and "没写理由" in p for p in problems), problems)
        # reason 写成数字也算没写
        path = self.write('[[pair]]\nlow = "手持风扇-小"\nhigh = "手持风扇-大"\nstrict = true\nreason = 3\n')
        fans = [c for c in self.chips if c["icon"] == "50030"]
        problems = check_data.series_problems(fans, path)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("没写理由", problems[0])

    def test_all_reported_at_once(self):
        # 漏写理由、漏写一对同时有时一次都报出来：银行卡-余额少的两对没写理由，摩托头盔一般 / 高级这一对没写
        pairs = [(lo, hi, st, None if lo == "银行卡-余额少" else r, lg, hg) for lo, hi, st, r, lg, hg in self.pairs
                 if (lo, hi) != ("摩托头盔-一般", "摩托头盔-高级")]
        path = self.write(series_text(pairs))
        problems = check_data.series_problems(self.chips, path)
        self.assertEqual(len(problems), 3, problems)
        self.assertEqual(sum("银行卡-余额少" in p and "没写理由" in p for p in problems), 2)
        self.assertTrue(any("缺少「摩托头盔-一般」/「摩托头盔-高级」这一对（图标 Defend 相同）" in p for p in problems),
                        problems)

    def test_new_chip_in_series(self):
        # 游戏新出同一图标的筹码（这里假设一张金色的手持风扇）：和同系列的每一张都要写一对
        new = dict(next(c for c in self.chips if c["name"] == "手持风扇-大"), name="手持风扇-超大", rarity="金")
        problems = check_data.series_problems(self.chips + [new])
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("手持风扇-小" in p and "手持风扇-超大" in p for p in problems), problems)
        self.assertTrue(any("手持风扇-大" in p and "手持风扇-超大" in p for p in problems), problems)
        # 图标独一无二的不算系列；图标一栏空着的不按图标分系列
        self.assertEqual(check_data.series_problems(self.chips + [dict(new, icon="99999")]), [])
        blank = [dict(new, name=f"无图标{i}", icon=icon) for i, icon in enumerate(("", "—", "", "—"))]
        self.assertEqual(check_data.series_problems(self.chips + blank), [])

    def test_pair_order_and_file_problems(self):
        # low / high 写反了也算写进表（稀有度的先后由载入时检查）
        pairs = [(hi, lo, *rest) if (lo, hi) == ("手持风扇-小", "手持风扇-大") else (lo, hi, *rest)
                 for lo, hi, *rest in self.pairs]
        self.assertEqual(check_data.series_problems(self.chips, self.write(series_text(pairs))), [])
        # 没有这个文件：图标相同的 20 对全缺
        problems = check_data.series_problems(self.chips, self.write(None))
        self.assertEqual(len(problems), 20)
        self.assertTrue(all(p.startswith("series.toml：缺少") for p in problems))
        # 格式错误（读不出来）、pair 写成单个表：由 advisor/data.py 载入时的警告报出（check_data 把警告也算问题），
        # series_problems 本身不重复报
        self.assertEqual(check_data.series_problems(self.chips, self.write("[[pair]\nlow = ")), [])
        self.assertEqual(check_data.series_problems(self.chips, self.write('[pair]\nlow = "a"\n')), [])
        # low、high 不是筹码名的这一对不算写进表：图标相同的这一对还是缺
        fans = [c for c in self.chips if c["icon"] == "50030"]
        path = self.write('[[pair]]\nlow = ["手持风扇-小"]\nhigh = "手持风扇-大"\nstrict = true\nreason = "算：x"\n')
        problems = check_data.series_problems(fans, path)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("缺少「手持风扇-小」/「手持风扇-大」这一对", problems[0])


# ------------------------------------------------------------ 推荐：engine.advice.mark_series


class SeriesAdviceTest(Base):
    def assert_shape(self, adv):
        """被替代的推荐度为 0、不是首推、不是 Advice.top；其余候选和刷新的推荐度合计 100%。"""
        percents = self.options(adv)
        self.assertAlmostEqual(sum(percents), 1.0, places=9)
        active = [i for i, e in enumerate(adv.evals) if not e.superseded_by]
        # Advice.top 是推荐卡片上排在最前的候选：推荐度最高的（平局时取靠前的），一般就是总分最高的；不算上位替代的
        # 同系列两张总分相差不到 rules.toml [advice] series_near 时是稀有度高的那张（总分可能略低）
        top = max(active, key=lambda i: (tie_key(adv.evals[i].percent), -i))
        self.assertEqual(adv.top, top)
        for e in adv.evals:
            if e.superseded_by:
                self.assertEqual(e.percent, 0.0)
                self.assertNotEqual(adv.best, e.chip.name)
                self.assertEqual(e.versus, [])
        self.assertIn(adv.best, (adv.evals[top].chip.name, REROLL))

    def test_bank_low_superseded(self):
        # 130853-6 的局面换成 余额少 / 余额多 / 小猪存钱罐：余额少「被银行卡-余额多上位替代」，余额多下面「比蓝色多 3 层星光」
        st = State(**AL_130853)
        adv = advise(self.db, st, ["银行卡-余额少", "银行卡-余额多", "小猪存钱罐"])
        low, high, pig = adv.evals
        self.assert_shape(adv)
        self.assertEqual(low.superseded_by, "银行卡-余额多")
        self.assertEqual(low.percent, 0.0)
        self.assertNotEqual(adv.best, "银行卡-余额少")
        tip = self.pair("银行卡-余额少", "银行卡-余额多").tip
        self.assertEqual(low.superseded_tip, tip)
        self.assertEqual((high.superseded_tip, pig.superseded_tip), ("", ""))
        self.assertEqual(high.versus, [("银行卡-余额少", "3 层星光", tip)])
        self.assertEqual(compare_text(RARITY_WORD[low.chip.rarity], high.versus[0][1]), "比蓝色多 3 层星光")
        self.assertEqual((high.superseded_by, pig.superseded_by, pig.versus), (None, None, []))
        # 余额少原本占的推荐度分给其余选项：余额多的推荐度上升
        before = self.by_name(advise(self.plain, st, ["银行卡-余额少", "银行卡-余额多", "小猪存钱罐"]))
        self.assertGreater(before["银行卡-余额少"].percent, 0)
        self.assertGreater(high.percent, before["银行卡-余额多"].percent)

    def test_live_130853_unchanged(self):
        # 130853-6 原局面（用不完 / 余额多 / 小猪存钱罐）：不是严格上位替代，推荐度与没有同系列筹码表时相同，两张各有一行对比行
        st = State(**AL_130853)
        offer = ["银行卡-用不完", "银行卡-余额多", "小猪存钱罐"]
        adv, before = advise(self.db, st, offer), advise(self.plain, st, offer)
        self.assert_shape(adv)
        self.assertEqual(adv.best, before.best)
        for a, b in zip(self.options(adv), self.options(before)):
            self.assertAlmostEqual(a, b, places=12)
        # 悬停说明里的 {starlight_use} 按当前角色写：阿尔的星光层数换成属性
        tip = self.pair("银行卡-余额多", "银行卡-用不完").tip.replace(
            STARLIGHT_USE, "星光层数另有用处（阿尔每 6 层星光攻击力、防御力各 +1）")
        gold, purple, pig = adv.evals
        self.assertEqual(gold.versus, [("银行卡-余额多", "全队轮次星币 +3", tip)])
        self.assertEqual(purple.versus, [("银行卡-用不完", "4 层星光", tip)])
        self.assertEqual(compare_text("金色", purple.versus[0][1]), "比金色多 4 层星光")
        self.assertEqual(compare_text("紫色", gold.versus[0][1]), "比紫色多全队轮次星币 +3")
        self.assertEqual(pig.versus, [])
        self.assertTrue(all(e.superseded_by is None for e in adv.evals))

    def test_starlight_use_follows_hero(self):
        """两张银行卡对比的悬停说明：星光层数的其他用处按当前角色和已有筹码写（芬妮的悬停里不写
        阿尔的例子），没有相关的就不举例。"""
        offer = ["银行卡-用不完", "银行卡-余额多", "小猪存钱罐"]
        raw = self.pair("银行卡-余额多", "银行卡-用不完").tip
        cases = [
            (dict(hero="芬妮", star=2, round=4), STARLIGHT_NO_USE),
            (dict(hero="芬妮", star=3, round=9, owned=("紫色飞星", "金色飞星")),
             "星光层数另有用处（已持有「金色飞星」：放过首领时，首领额外受到等同星光层数的伤害）"),
            (dict(hero="美咲", star=2, round=5, owned=("手电筒-强光",)),
             "星光层数另有用处（已持有「手电筒-强光」：攻击怪物时每 4 层星光攻击力 +1）"),
            (dict(hero="阿尔", star=3, round=9, owned=("金色飞星",)),
             "星光层数另有用处（阿尔每 6 层星光攻击力、防御力各 +1；已持有「金色飞星」：放过首领时，首领额外受到等同星光层数的伤害）"),
        ]
        for kw, use in cases:
            adv = advise(self.db, State(rerolls=2, **kw), offer)
            gold, purple, _ = adv.evals
            want = raw.replace(STARLIGHT_USE, use)
            with self.subTest(**{k: v for k, v in kw.items() if k != "owned"}, owned=kw.get("owned")):
                self.assertEqual([v[2] for v in gold.versus + purple.versus], [want, want])
                self.assertNotIn("{", want)
                self.assertNotIn("例如", want)
                if kw["hero"] != "阿尔":
                    self.assertNotIn("阿尔", want)
        self.assertEqual(STARLIGHT_NO_USE, "星光层数另有用处时")
        # 严格上位替代的悬停说明里没有占位符，原样写
        adv = advise(self.db, State(hero="芬妮", star=2, rerolls=2), ["银行卡-余额少", "银行卡-余额多", "书签"])
        self.assertEqual(adv.evals[0].superseded_tip, self.pair("银行卡-余额少", "银行卡-余额多").tip)

    def test_inversion_fixed(self):
        # 不标上位替代时低一级排在高一级前面的局面（前瞻偏向低一级）：标了以后首推不是低一级
        cases = [
            (State(hero="璐璐", star=0, rerolls=2), ["夹心饼干-一般", "夹心饼干-可口", "书签"], "夹心饼干-可口"),
            (State(hero="邦妮", star=1, round=1, rerolls=3), ["美工刀-初级", "美工刀-锋利", "书签"], "美工刀-锋利"),
        ]
        for st, offer, high in cases:
            adv = advise(self.db, st, offer)
            with self.subTest(hero=st.hero):
                self.assert_shape(adv)
                self.assertEqual(adv.evals[0].superseded_by, high)
                self.assertEqual(adv.evals[adv.top].chip.name, high)
                self.assertNotEqual(adv.best, offer[0])
                self.assertEqual([v[0] for v in adv.evals[1].versus], [offer[0]])

    def test_forced_inversion(self):
        # 低一级的分值远高于高一级时（这里把测试用的默认推荐档位里凛的玩家思路改成必拿摩托头盔-一般）：低一级照样
        # 不参与比较，首推、Advice.top、刷新比较的对象、「适配度均偏低」的附注都只看其余候选
        from tests.support.player_layer import player_layer
        ps = player_layer(self.db, ("凛",))["凛"]
        boosted = dataclasses.replace(ps, tiers={**ps.tiers, "摩托头盔-一般": "core"})
        db = dataclasses.replace(self.db, player_synergy={**self.db.player_synergy, "凛": boosted})
        st = State(hero="凛", star=1, round=3, map="异变图书馆", rerolls=3)
        offer = ["摩托头盔-一般", "速度轮滑-初级", "摩托头盔-中级"]
        adv = advise(db, st, offer)
        low, other, mid = adv.evals
        line = db.rules["advice"]["recommend_total"]
        self.assert_shape(adv)
        self.assertEqual(low.superseded_by, "摩托头盔-中级")
        self.assertGreater(low.total, line)
        self.assertGreater(low.total, mid.total + 50)
        self.assertNotEqual(adv.top, 0)
        self.assertNotEqual(adv.best, "摩托头盔-一般")
        # 刷新拿来比较的是其余候选里总分最高的（出现更好筹码的概率按它算），不是被替代的低一级
        expected = Scorer(db, st, offer=offer).reroll(offer, adv.evals[adv.top].total)
        self.assertAlmostEqual(adv.reroll.p_better, expected.p_better, places=12)
        self.assertAlmostEqual(adv.reroll.expected, expected.expected, places=9)
        # 其余候选都低于推荐线时，有「适配度均偏低」的附注（被替代的不算）
        self.assertTrue(mid.total < line and other.total < line)
        self.assertIn(LOW_NOTE, adv.notes)
        # 没有同系列筹码表时，必拿的低一级首推，没有这条附注
        plain = advise(dataclasses.replace(db, series=()), st, offer)
        self.assertEqual(plain.best, "摩托头盔-一般")
        self.assertNotIn(LOW_NOTE, plain.notes)

    def test_three_of_a_series(self):
        st = State(**AL_130853)
        # 银行卡三张：余额少被余额多替代；余额多「比金色多 4 层星光」，用不完「比紫色多全队轮次星币 +3」
        adv = advise(self.db, st, ["银行卡-余额少", "银行卡-余额多", "银行卡-用不完"])
        self.assert_shape(adv)
        low, purple, gold = adv.evals
        self.assertEqual(low.superseded_by, "银行卡-余额多")
        self.assertEqual([v[:2] for v in purple.versus], [("银行卡-用不完", "4 层星光")])
        self.assertEqual([v[:2] for v in gold.versus], [("银行卡-余额多", "全队轮次星币 +3")])
        # 摩托头盔三张：一般、中级都被高级替代；高级比被替代的里稀有度最高的中级多「防御力 +1」
        adv = advise(self.db, State(hero="凛", star=1, rerolls=3), ["摩托头盔-一般", "摩托头盔-中级", "摩托头盔-高级"])
        self.assert_shape(adv)
        a, b, c = adv.evals
        self.assertEqual((a.superseded_by, b.superseded_by, c.superseded_by), ("摩托头盔-高级", "摩托头盔-高级", None))
        self.assertEqual((a.superseded_tip, b.superseded_tip), (self.pair("摩托头盔-一般", "摩托头盔-高级").tip,
                                                                self.pair("摩托头盔-中级", "摩托头盔-高级").tip))
        self.assertEqual(c.versus, [("摩托头盔-中级", "防御力 +1", self.pair("摩托头盔-中级", "摩托头盔-高级").tip)])
        self.assertEqual(adv.top, 2)
        self.assertAlmostEqual(c.percent + (adv.reroll.percent if adv.reroll else 0), 1.0)
        # 夹心饼干三张：一般被可口替代；美味比可口多一条，可口和美味之间没有可口那一方的要点，写它比一般多什么
        adv = advise(self.db, State(hero="小町", star=1, rerolls=3), ["夹心饼干-一般", "夹心饼干-可口", "夹心饼干-美味"])
        self.assert_shape(adv)
        plain, tasty, deluxe = adv.evals
        self.assertEqual((plain.superseded_by, tasty.superseded_by, deluxe.superseded_by), ("夹心饼干-可口", None, None))
        self.assertEqual([v[:2] for v in tasty.versus], [("夹心饼干-一般", "最大生命值 +1")])
        self.assertEqual([v[:2] for v in deluxe.versus], [("夹心饼干-可口", "按额外生命值上限加攻击力")])

    def test_fan_superseded(self):
        """手持风扇-小 / -大同时出现：小「被紫色上位替代」、不参与推荐度，大下面写「比蓝色多使用主动技能后获得 1 张卡牌」。
        实测那一局的局面（按现在的适配表，阿兰娜的推荐流派是治愈、标记，两张风扇都加推荐流派的分）：不算上位替代时
        43% / 39% / 18%，算的话小的那份分给其余两项，首推是医疗箱-紧急治疗。"""
        st = State(**ALANA_LAST)
        adv = advise(self.db, st, ALANA_OFFER, rarity=ALANA_RARITY)
        heal, big, small = adv.evals
        self.assert_shape(adv)
        pair = self.pair("手持风扇-小", "手持风扇-大")
        self.assertEqual((small.superseded_by, small.percent, small.superseded_tip), ("手持风扇-大", 0.0, pair.tip))
        self.assertEqual(big.versus, [("手持风扇-小", "使用主动技能后获得 1 张卡牌", pair.tip)])
        self.assertEqual(compare_text(RARITY_WORD[small.chip.rarity], big.versus[0][1]), "比蓝色多使用主动技能后获得 1 张卡牌")
        self.assertEqual((adv.best, adv.top, adv.reroll), ("医疗箱-紧急治疗", 0, None))
        self.assertEqual(shown_percents(adv), {0: 52, 1: 48})  # 卡片上显示的推荐度
        # 写成不算时三项都有推荐度：43% / 39% / 18%
        old = dataclasses.replace(self.db, series=tuple(
            dataclasses.replace(p, strict=False) if p is pair else p for p in self.db.series))
        before = advise(old, st, ALANA_OFFER, rarity=ALANA_RARITY)
        self.assertEqual(shown_percents(before), {0: 43, 1: 39, 2: 18})
        self.assertTrue(all(e.superseded_by is None for e in before.evals))
        # 分值、前瞻不变，只是小的那份推荐度分给其余两项
        for a, b in zip(adv.evals, before.evals):
            self.assertEqual((a.total, a.lookahead), (b.total, b.lookahead))
        self.assertGreater(heal.percent, before.evals[0].percent)
        self.assertGreater(big.percent, before.evals[1].percent)
        # 凛（两张都是可选）：一样被替代
        adv = advise(self.db, State(hero="凛", star=1, rerolls=3), ["手持风扇-小", "手持风扇-大", "书签"])
        self.assert_shape(adv)
        self.assertEqual([e.superseded_by for e in adv.evals], ["手持风扇-大", None, None])

    def test_not_strict(self):
        # 不算上位替代的几对：都有推荐度；要点为空的一方不写对比行（银行卡-余额多 / -用不完见 test_live_130853_unchanged）
        # 小町 0 星：夹心饼干-美味的总分比-可口高约 1.6，不会因为两张接近而对调先后（rules.toml [advice] series_near）
        cases = [
            (State(hero="小町", star=0, rerolls=3), ["夹心饼干-可口", "夹心饼干-美味", "书签"], [0, 1]),
            (State(hero="南希露", star=1, rerolls=3), ["普通瞄具", "鹰眼瞄具", "书签"], [1, 1]),
            (State(hero="小町", star=1, rerolls=3), ["夹心饼干-一般", "夹心饼干-美味", "书签"], [0, 1]),
            (State(hero="芬妮", star=1, rerolls=3), ["银行卡-余额少", "银行卡-用不完", "书签"], [1, 1]),
        ]
        for st, offer, lines in cases:
            adv = advise(self.db, st, offer)
            before = advise(self.plain, st, offer)
            with self.subTest(offer=offer):
                self.assert_shape(adv)
                self.assertTrue(all(e.superseded_by is None for e in adv.evals))
                self.assertEqual([len(e.versus) for e in adv.evals[:2]], lines)
                self.assertEqual(adv.evals[2].versus, [])
                self.assertEqual(adv.best, before.best)
                for a, b in zip(self.options(adv), self.options(before)):
                    self.assertAlmostEqual(a, b, places=12)

    def test_only_this_pick(self):
        # 只在本次三选一里调整：各候选的分值、前瞻，刷新的期望和代价与没有同系列筹码表时相同
        cases = [
            (State(**AL_130853), ["银行卡-余额少", "银行卡-余额多", "小猪存钱罐"]),
            (State(hero="璐璐", star=0, rerolls=2), ["摩托头盔-一般", "摩托头盔-中级", "书签"]),
            (State(hero="邦妮", star=1, round=1, rerolls=3), ["美工刀-初级", "美工刀-锋利", "书签"]),
            (State(hero="凛", star=2, round=6, map="水乡古镇", rerolls=2), ["拳击手套-初级", "拳击手套-高级", "速度轮滑-初级"]),
            (State(hero="蓝海晴", star=1, round=4, rerolls=3), ["紫色飞星", "金色飞星", "医疗箱-紧急治疗"]),
        ]
        for st, offer in cases:
            adv, before = advise(self.db, st, offer), advise(self.plain, st, offer)
            with self.subTest(hero=st.hero, offer=offer):
                self.assertTrue(any(e.superseded_by for e in adv.evals))
                for a, b in zip(adv.evals, before.evals):
                    self.assertEqual((a.total, a.immediate, a.lookahead, a.lookahead_reason), (b.total, b.immediate, b.lookahead,
                                                                                            b.lookahead_reason))
                    self.assertEqual([(p.label, p.score, p.reason) for p in a.parts], [(p.label, p.score, p.reason) for p in b.parts])
                self.assertEqual((adv.reroll.expected, adv.reroll.cost), (before.reroll.expected, before.reroll.cost))
                self.assertEqual(adv.picks, before.picks)

    def test_duplicates_and_unrecorded(self):
        # 识别有误时的输入：同一筹码出现两次、混着未收录的筹码，照常给出结果
        adv = advise(self.db, State(hero="凛", star=1, rerolls=2), ["摩托头盔-一般", "摩托头盔-一般", "摩托头盔-中级"])
        self.assert_shape(adv)
        self.assertEqual([e.superseded_by for e in adv.evals], ["摩托头盔-中级", "摩托头盔-中级", None])
        self.assertEqual(len(adv.evals[2].versus), 1)
        adv = advise(self.db, State(hero="凛", star=1, rerolls=2), ["摩托头盔-高级", "摩托头盔-高级", "摩托头盔-一般"])
        self.assert_shape(adv)
        self.assertEqual([len(e.versus) for e in adv.evals], [1, 1, 0])
        adv = advise(self.db, State(hero="凛", star=1, rerolls=0), ["摩托头盔-一般", "新筹码甲", "摩托头盔-中级"],
                     rarity=[None, "金", None])
        self.assert_shape(adv)
        self.assertEqual([e.superseded_by for e in adv.evals], ["摩托头盔-中级", None, None])
        self.assertEqual(adv.evals[1].versus, [])
        self.assertAlmostEqual(adv.evals[1].percent + adv.evals[2].percent, 1.0)

    def test_no_series_in_offer(self):
        # 没有同系列的候选时，top 就是总分最高的候选，什么都不标
        adv = advise(self.db, State(hero="凛", star=1, rerolls=2), ["标记喷罐", "书签", "大背包"])
        self.assert_shape(adv)
        self.assertTrue(all(e.superseded_by is None and e.versus == [] for e in adv.evals))
        self.assertEqual(adv.top, max(range(3), key=lambda i: (tie_key(adv.evals[i].total), -i)))


# ------------------------------------------------------------ 命令行


class SeriesCliTest(Base):
    ARGS = ["--hero", "阿尔", "--star", "2", "--round", "7", "--map", "异变图书馆", "--source", "task", "--tier", "2",
            "--rerolls", "3", "--offer", "银行卡-余额少", "银行卡-余额多", "银行卡-用不完"]

    def run_cli(self, *extra) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main([*self.ARGS, *extra])
        return buf.getvalue()

    def test_render(self):
        # 命令行写筹码名：「被「银行卡-余额多」上位替代」；被替代的一行用「—」代替推荐度，排在刷新之后；对比行写颜色词
        out = self.run_cli()
        lines = out.splitlines()
        low = next(i for i, t in enumerate(lines) if "银行卡-余额少（" in t)
        reroll = next(i for i, t in enumerate(lines) if t[4:].startswith(f"  {REROLL}  "))
        self.assertGreater(low, reroll)
        self.assertTrue(lines[low].lstrip().startswith("—  银行卡-余额少"), lines[low])
        self.assertIn("被「银行卡-余额多」上位替代，不参与推荐度比较", lines[low])
        self.assertIn("        对比：比金色多 4 层星光（银行卡-余额多获得 7 层星光；", out)
        self.assertIn("        对比：比紫色多全队轮次星币 +3（", out)
        self.assertNotIn("。）", out)

    def test_reroll_against_top(self):
        # 刷新比较的对象是 Advice.top。被替代的低一级总分更高时（璐璐 0 星：夹心饼干-一般高于 -可口），
        # 概率那一行写出比较对象的筹码名和总分，不写「当前候选筹码最高总分」
        adv = advise(self.db, State(hero="璐璐", star=0, rerolls=2), ["夹心饼干-一般", "夹心饼干-可口", "书签"])
        low, top = adv.evals[0], adv.evals[adv.top]
        self.assertEqual((low.superseded_by, top.chip.name), ("夹心饼干-可口", "夹心饼干-可口"))
        self.assertGreater(low.total, top.total)
        text = cli.render(self.db, adv)
        self.assertIn(f"        · 刷新后出现总分高于「夹心饼干-可口」（总分 {top.total:.0f}）的筹码的概率："
                      f"{adv.reroll.p_better:.0%}", text)
        self.assertIn(f"夹心饼干-可口（紫·通用）  总分 {top.total:.0f} =", text)
        self.assertNotIn("当前候选筹码最高总分", text)
        # 有被替代的候选但总分不高于 Advice.top 时照常写「当前候选筹码最高总分」
        adv = advise(self.db, State(**AL_130853), ["银行卡-余额少", "银行卡-余额多", "小猪存钱罐"])
        self.assertTrue(adv.evals[0].superseded_by)
        self.assertEqual(max(e.total for e in adv.evals), adv.evals[adv.top].total)
        self.assertIn(f"        · 刷新后出现总分高于当前候选筹码最高总分的筹码的概率：{adv.reroll.p_better:.0%}",
                      cli.render(self.db, adv))

    def test_json(self):
        data = json.loads(self.run_cli("--json"))
        chips = {c["name"]: c for c in data["chips"]}
        self.assertEqual(chips["银行卡-余额少"]["superseded_by"], "银行卡-余额多")
        self.assertEqual(chips["银行卡-余额少"]["percent"], 0)
        self.assertEqual(chips["银行卡-余额少"]["versus"], [])
        self.assertEqual([(v["chip"], v["gain"]) for v in chips["银行卡-余额多"]["versus"]], [("银行卡-用不完", "4 层星光")])
        self.assertIsNone(chips["银行卡-用不完"]["superseded_by"])
        self.assertIn(data["top"], ("银行卡-余额多", "银行卡-用不完"))
        self.assertNotEqual(data["best"], "银行卡-余额少")


class CompareTextTest(unittest.TestCase):
    def test_spacing(self):
        # 要点以数字或字母开头时加半角空格，以汉字或符号开头时不加
        self.assertEqual(compare_text("金色", "4 层星光"), "比金色多 4 层星光")
        self.assertEqual(compare_text("紫色", "防御力 +1"), "比紫色多防御力 +1")
        self.assertEqual(compare_text("蓝色", "每回合 2 层治愈"), "比蓝色多每回合 2 层治愈")
        self.assertEqual(compare_text("紫色", "+1 移动"), "比紫色多+1 移动")


if __name__ == "__main__":
    unittest.main()
