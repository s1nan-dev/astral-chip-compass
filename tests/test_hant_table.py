"""界面文字的简体 → 繁体（advisor/ui/lang.py 的 to_hant、t、localize）和对照表（data/zh_hant.json、
data/zh_hant_extra.toml，由 tools/hant_table.py 生成和维护）。

核对：
- 转换：词组优先于单字、最长匹配、繁体用词、繁体字形、资料库里的繁体名和人补词组（含最终写法自身）先锁住、不在表
  里的字原样、表缺了时原样返回不抛异常；
- 转几遍都一样：资料库里的名字、人补的词组（简体词和最终写法）、调了 localize 的模块的常量、advisor/ui 下全部字符串
  字面量，to_hant 转两遍和一遍相同；
- 一字多繁：调了 localize 的模块里要转的文字和 t() 里写死的文字 strict 转一遍，报出模块、文字、位置和候选；界面
  文字里的这些字都有 data/zh_hant_extra.toml 的词组盖住（KNOWN_BLOCKED 为空），新写的文字一被拦下就报出来；另用
  反例确认新的一字多繁报得出来；
- 常用字默认：CHAR_DEFAULTS 的 27 个字转成定的写法、不被拦下；NO_DEFAULT 里的字照旧拦下；
- 表：收 5 份词典的全部条目、digest 只随资料库里的名字变；show --all 和繁体字体子集收字的范围（all_texts）；
- 从 advisor.ui 以外导进调了 localize 的模块、值有汉字的大写常量，必须写在 HANT_KEEP 里（只看模块这一层的导入，包的
  __init__ 按包算相对导入）；另用反例确认漏写的报得出来；
- t()：简体界面原样、繁体界面转、不是字符串原样、表坏了原样；结果有缓存，几个线程同时用不出错；
- localize：只转大写的常量、HANT_KEEP 不转、字典的键不转、dataclass 和嵌套的转、简体界面下什么都不改、返回值；
- 设置窗口：在繁体界面下重新导入（子进程）以后几处常量是繁体、ITEMS 的分组和 GROUP_* 一致、LANG_CHOICES 没变，
  离屏平台把窗口建出来不出错。
"""
from __future__ import annotations

import ast
import collections
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from unittest import mock

from advisor import paths
from advisor.ui import lang
from tools import hant_table
from tools.make_font_subset import source_strings

PROJECT = Path(__file__).resolve().parents[1]
TABLE = json.loads((PROJECT / "data" / lang.HANT_TABLE).read_text(encoding="utf-8"))


class Case(unittest.TestCase):
    def setUp(self):
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        lang._reset_tables()
        self.addCleanup(lang._reset_tables)


@dataclass(frozen=True)
class FakeItem:
    title: str
    count: int
    tags: tuple[str, ...] = ()


class FakePill(NamedTuple):
    text: str
    tone: str = ""


class Unequal:
    """和别的对象比较时报错的对象（同数组：比较的结果不是真假值）。"""

    def __eq__(self, other):
        raise ValueError("比较的结果不是真假值")

    __ne__ = __eq__
    __hash__ = object.__hash__


@dataclass
class WithDerived:
    title: str
    other: object = None
    label: str = field(init=False, default="软件")


class ConvertTest(Case):
    def test_phrase_before_character(self):
        """繁体用词、繁体字形：按词典里的条目转（期望值从表里取，确认词典里确实是这样）。"""
        for key, source in (("設置", "设置"), ("軟件", "软件"), ("默認", "默认"), ("鼠標", "鼠标")):
            with self.subTest(key=key):
                self.assertIn(key, TABLE["tw_phrases"])
                self.assertEqual(lang.to_hant(source), TABLE["tw_phrases"][key][0])
        self.assertIn("里面", TABLE["st_phrases"])
        self.assertEqual(lang.to_hant("里面"), "裡面")

    def test_longest_match(self):
        """最长匹配：词组盖住的这一段不再按单字转。"""
        self.assertEqual(lang.to_hant("拾星助手设置"), "拾星助手設定")
        self.assertEqual(lang.to_hant("头发"), "頭髮")  # 「发」单字写「發」，词组「头发」盖住

    def test_first_step_priority(self):
        """第一步先锁 names、人补词组（同样长时 names 优先），再在空隙里按 STPhrases 最长匹配：锁住的开头不会被
        更长的 STPhrases 词组吞掉。"""
        table = {"names": {"甲乙": "名"}, "st_phrases": {"甲乙": ["词"], "甲乙丙": ["长词"]}}
        self.assertEqual(lang._Converter(table, {"甲乙": "补"}).convert("甲乙")[0], "名")
        self.assertEqual(lang._Converter({"st_phrases": table["st_phrases"]}, {"甲乙": "补"}).convert("甲乙")[0], "补")
        self.assertEqual(lang._Converter(table, {"甲乙": "补"}).convert("甲乙丙")[0], "名丙")
        self.assertEqual(lang._Converter({"st_phrases": table["st_phrases"]}).convert("甲乙丙")[0], "长词")
        self.assertEqual(lang._Converter({}, {"甲乙丙丁": "补长"}).convert("甲乙丙丁")[0], "补长")

    def test_final_form_locks_itself(self):
        """人补词组的最终写法自身也是键：转好的文字再转一遍，不会再被繁体用词改掉（「錯誤代碼」不会变成
        「錯誤程式碼」）。"""
        conv = lang._Converter({"tw_phrases": {"代碼": ["程式碼"]}}, {"错误代码": "錯誤代碼"})
        once = conv.convert("错误代码")[0]
        self.assertEqual(once, "錯誤代碼")
        self.assertEqual(conv.convert(once)[0], once)

    def test_extra_locks_before_opencc_phrase(self):
        """人补词组先锁：OpenCC 的「时后→時後」不会吞掉人补的「后台→背景」，「退出时后台识别」转成
        「退出時背景識別」。"""
        table = {"st_phrases": {"时后": ["時後"]},
                 "st_chars": {"退": ["退"], "出": ["出"], "时": ["時"], "识": ["識"], "别": ["別"]}}
        conv = lang._Converter(table, {"后台": "背景"})
        self.assertEqual(conv.convert("退出时后台识别")[0], "退出時背景識別")

    def test_game_names_are_verbatim(self):
        """资料库里的繁体名照游戏原文，不再经过繁体用词、字形（包括简繁混写的）。"""
        name, hant = next((n, h) for n, h in TABLE["names"].items() if n != h)
        self.assertEqual(lang.to_hant(name), hant)
        conv = lang._Converter({"names": {"测试名": "裏面"}, "tw_variants": {"裏": ["裡"]}})
        self.assertEqual(conv.convert("测试名")[0], "裏面")

    def test_extra_is_verbatim(self):
        """人补的词组写的是最终写法，不再经过后面两步。"""
        conv = lang._Converter({"tw_variants": {"裏": ["裡"]}}, {"测试词": "裏面"})
        self.assertEqual(conv.convert("测试词")[0], "裏面")

    def test_characters_outside_table_stay(self):
        self.assertEqual(lang.to_hant("龘"), "龘")
        self.assertEqual(lang.to_hant("abc 123"), "abc 123")

    def test_strict_reports_ambiguous_character(self):
        """strict：单字表有几个候选、又没有被词组、常用字默认盖住的字被拦下（位置从 0 数、候选照词典）。"""
        extra = lang._load_extra()
        covered = (set(TABLE["char_defaults"]) | set(TABLE["names"]) | set(TABLE["names"].values())
                   | set(extra) | set(extra.values()))
        char = next(key for key, values in TABLE["st_chars"].items()
                    if len(values) > 1 and key not in covered and key not in TABLE["st_phrases"])
        converted, blocked = lang.to_hant(char, strict=True)
        self.assertEqual(converted, TABLE["st_chars"][char][0])
        self.assertEqual(len(blocked), 1)
        self.assertEqual((blocked[0].index, blocked[0].char, blocked[0].candidates),
                         (0, char, tuple(TABLE["st_chars"][char])))

    def test_missing_table_returns_simplified(self):
        """表缺了、坏了时原样返回简体，不抛异常。"""
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as folder, \
                mock.patch.object(paths, "data_dir", return_value=Path(folder)):
            lang._reset_tables()
            self.assertEqual(lang.to_hant("软件 设置"), "软件 设置")
            self.assertEqual(lang.to_hant("软件", strict=True), ("软件", []))
            # 写坏的表（不是 JSON、不是对象）
            for broken in ("{不是 JSON", "[1, 2]"):
                with self.subTest(table=broken):
                    (Path(folder) / lang.HANT_TABLE).write_text(broken, encoding="utf-8")
                    lang._reset_tables()
                    self.assertEqual(lang.to_hant("软件 设置"), "软件 设置")

    def test_broken_extra_keeps_table(self):
        """补充词组写坏了：照样按表转，只是不用补充词组。"""
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as folder, \
                mock.patch.object(paths, "data_dir", return_value=Path(folder)):
            (Path(folder) / lang.HANT_TABLE).write_text(json.dumps(TABLE, ensure_ascii=False), encoding="utf-8")
            (Path(folder) / lang.HANT_EXTRA).write_text('"后台" = ', encoding="utf-8")
            lang._reset_tables()
            self.assertEqual(lang.to_hant("软件"), TABLE["tw_phrases"]["軟件"][0])


def _ui_literals() -> list[str]:
    """advisor/ui 下全部 .py 里写的字符串字面量（不含文档字符串；f-string 里写死的那几段各算一条）。"""
    out: list[str] = []
    for path in sorted((PROJECT / "advisor" / "ui").rglob("*.py")):
        out.extend(source_strings(path))
    return out


class IdempotentTest(Case):
    def test_corpus(self):
        """资料库里的名字、人补的词组（简体词和最终写法）、调了 localize 的模块的常量、advisor/ui 以外会显示出来的常量
        （shown_texts）、advisor/ui 下的字符串字面量：转两遍和一遍相同。

        这样模块重新导出、显示处再包一层 t() 都不会把已经转好的文字转坏。最终写法也放进来：一条词组的最终写法碰巧是
        另一条的简体词、两边写的又不一样时，第二遍会换掉它。
        """
        texts = []
        for key, value in [*TABLE["names"].items(), *lang._load_extra().items()]:
            texts += [key, value]
        texts += [text for _, text in hant_table.localized_texts()]
        texts += [text for _, text in hant_table.shown_texts()]
        texts += _ui_literals()
        bad = []
        for text in texts:
            once = lang.to_hant(text)
            twice = lang.to_hant(once)
            if twice != once:
                bad.append(f"{text!r}：一遍 {once!r}，两遍 {twice!r}")
        self.assertEqual(bad, [], "转两遍不一样：改转换规则，或在 data/zh_hant_extra.toml 补词组\n"
                         + "\n".join(bad[:20]))


# 一字多繁、写法要看词才定得下的字：不给默认写法（不进 CHAR_DEFAULTS），strict 照旧拦下，由 data/zh_hant_extra.toml
# 的词组解决
NO_DEFAULT = frozenset("并台发制御注闲签于布链须象占")
# BlockedTest 不算作新问题的字。界面文字里 NO_DEFAULT 的这些字都有 data/zh_hant_extra.toml 的词组盖住，这里不再放过任何
# 字：新写的文字一被拦下就让测试失败，提醒补词组
KNOWN_BLOCKED = frozenset()


class DefaultsTest(Case):
    def test_default_characters(self):
        """CHAR_DEFAULTS 的 27 个常用字转成定的写法，strict 不拦；表里有这一项。"""
        self.assertEqual({key: values[0] for key, values in TABLE["char_defaults"].items()},
                         hant_table.CHAR_DEFAULTS)
        self.assertEqual(len(hant_table.CHAR_DEFAULTS), 27)
        for char, want in hant_table.CHAR_DEFAULTS.items():
            with self.subTest(char=char):
                self.assertEqual(lang.to_hant(char, strict=True), (want, []))

    def test_other_ambiguous_characters_still_blocked(self):
        """NO_DEFAULT 里的字（写法要看词）：转第一个候选，strict 照旧拦下，由词组解决。"""
        self.assertFalse(NO_DEFAULT & set(hant_table.CHAR_DEFAULTS))
        for char in sorted(NO_DEFAULT):
            with self.subTest(char=char):
                converted, blocked = lang.to_hant(char, strict=True)
                self.assertEqual(converted, TABLE["st_chars"][char][0])
                self.assertEqual(len(blocked), 1, char)
                self.assertEqual((blocked[0].index, blocked[0].char), (0, char))


def blocked_messages(entries) -> list[str]:
    """entries（[(标签, 文字)]）各 strict 转一遍：被拦下、又不在 KNOWN_BLOCKED 里的字每处一条，写出标签、第几个字、
    候选、原文和现在转成的样子。KNOWN_BLOCKED 已经清空，被拦下的都算新问题。"""
    out: list[str] = []
    for label, text in entries:
        converted, blocked = lang.to_hant(text, strict=True)
        for item in blocked:
            if item.char not in KNOWN_BLOCKED:
                out.append(f"{label}：第 {item.index + 1} 个字「{item.char}」候选 {' / '.join(item.candidates)}"
                           f"（原文：{text}；现在转成：{converted}）")
    return out


class BlockedTest(Case):
    def test_only_known_characters_blocked(self):
        """调了 localize 的模块里要转的常量，advisor/ 下 t() 里写死的文字，加上 advisor/ui 以外会显示的常量
        （hant_table.SHOWN_CONSTANTS）：strict 转一遍，被拦下的只能是 KNOWN_BLOCKED 里的字（已经清空，现在一处都不该有）；
        有的话报出模块、文字、第几个字和候选。"""
        entries = [*hant_table.localized_texts(),
                   *((f"{rel}:{line} 的 t()", text) for rel, line, text in hant_table.t_literals()),
                   *hant_table.shown_texts()]
        problems = blocked_messages(entries)
        self.assertEqual(problems, [], "新拦下的字要在 data/zh_hant_extra.toml 补词组\n" + "\n".join(problems))

    def test_reports_new_ambiguous_character(self):
        """反例：t()、lang.t() 里写死的文字（含 f-string 写死的那几段）都取得到；有一字多繁、又没被词组盖住的字
        （「干」）时报出来，常用字默认（「个」）和词组盖住的（「干净」）不报，别的函数里的文字不取；NO_DEFAULT 里的字
        单独写时照旧报出来（写法要看词，由词组解决）。"""
        tree = ast.parse('from advisor.ui import lang\n'
                         'lang.t("干")\n'
                         't(f"{n} 个就好")\n'
                         'other("干")\n'
                         'lang.t("干净")\n')
        found = hant_table.t_call_literals(tree)
        self.assertEqual(found, [(2, "干"), (3, " 个就好"), (5, "干净")])
        messages = blocked_messages([(f"probe.py:{line}", text) for line, text in found])
        self.assertEqual(len(messages), 1, messages)
        self.assertTrue(messages[0].startswith("probe.py:2：第 1 个字「干」"), messages[0])
        for char in sorted(NO_DEFAULT):
            with self.subTest(char=char):
                messages = blocked_messages([("probe.py:9", char)])
                self.assertEqual(len(messages), 1, messages)
                self.assertTrue(messages[0].startswith(f"probe.py:9：第 1 个字「{char}」"), messages[0])


class ExtraPhrasesTest(Case):
    def test_blocked_characters_have_phrases(self):
        """NO_DEFAULT 里的字各挑一条界面文字里的典型词组：界面文字里的这些字都有词组盖住，strict 不再拦下，转成繁体
        的写法（依据见 data/zh_hant_extra.toml 各组的注释）。"""
        pairs = (
            ("照旧于", "照舊於"),
            ("并选中", "並選中"),
            ("发方案", "發方案"),
            ("发布页面", "發布頁面"),
            ("页签", "頁籤"),
            ("游戏机制", "遊戲機制"),
            ("御力", "禦力"),
            ("注：", "註："),
            ("空闲", "空閒"),
            ("须附上", "須附上"),
            ("取帧对象", "取幀物件"),
            ("工具链", "工具鏈"),
            ("占整机", "佔整機"),
            ("游戏平台", "遊戲平台"),
            ("这台电脑", "這台電腦"),
        )
        for source, want in pairs:
            with self.subTest(source=source):
                self.assertEqual(lang.to_hant(source, strict=True), (want, []))

    def test_object_only_where_listed(self):
        """「对象」只在程序的对象（取帧对象、JSON 对象、应为对象）锁成「物件」；别的意思的「对象」照旧拦下（繁体用词那一步
        会把「對象」一概换成「物件」，适用对象、推荐对象这类不一定对），新写的时候测试报出来、再定写法。"""
        for source, want in (("取帧对象", "取幀物件"), ("不是 JSON 对象", "不是 JSON 物件"), ("方案应为对象", "方案應為物件")):
            with self.subTest(source=source):
                self.assertEqual(lang.to_hant(source, strict=True), (want, []))
        for source in ("适用对象", "推荐对象"):
            with self.subTest(source=source):
                _converted, blocked = lang.to_hant(source, strict=True)
                self.assertEqual([item.char for item in blocked], ["象"])

    def test_game_wording(self):
        """照游戏繁体客户端的写法：显示模式「全螢幕」「視窗化」，帐号、帐户写「帳」，转账照游戏写「轉賬」；「发布」一律
        「發布」（OpenCC 有的地方会换成「釋出」）。"""
        pairs = (
            ("「全屏」或「窗口化」", "「全螢幕」或「視窗化」"),
            ("独占全屏模式", "獨佔全螢幕模式"),
            ("全屏幕", "全螢幕"),
            ("账号风险", "帳號風險"),
            ("您的账户", "您的帳戶"),
            ("给队友转账", "給隊友轉賬"),
            ("免费发布", "免費發布"),
            ("官方发布", "官方發布"),
            ("发布页面", "發布頁面"),
        )
        for source, want in pairs:
            with self.subTest(source=source):
                self.assertEqual(lang.to_hant(source), want)

    def test_known_wrong_conversions(self):
        """OpenCC 的词组跨过词的边界、转错的地方：锁住正确的词组以后转成定的写法。"""
        pairs = (
            ("三选一里", "三選一裡"),
            ("没有采用", "沒有採用"),
            ("7 天后再试", "7 天後再試"),
            ("屏幕上游戏窗口", "螢幕上遊戲視窗"),
            ("三选一出推荐", "三選一出推薦"),
            ("清单只从", "清單只從"),
            ("新版本里没有", "新版本裡沒有"),
            ("神秘宝藏", "神祕寶藏"),
        )
        for source, want in pairs:
            with self.subTest(source=source):
                self.assertEqual(lang.to_hant(source), want)

    def test_data_text_phrases(self):
        """资料库里的筹码效果、适配表里的理由会原样显示在悬浮窗上：句子里这些字按词定写法，strict 不再拦下；
        「多半只损失」的「只」是仅仅（OpenCC 的「多半只」会按量词转成「隻」），复制写「複製」。"""
        pairs = (
            ("吃下食物", "吃下食物"),
            ("前几轮技能用得越勤", "前幾輪技能用得越勤"),
            ("每回合一次", "每回合一次"),
            ("不会配合站位", "不會配合站位"),
            ("层治愈并恢复 1 点生命", "層治癒並恢復 1 點生命"),
            ("其中一张并对 6 格内的怪物", "其中一張並對 6 格內的怪物"),
            ("每击中一次并获得 1 层星光", "每擊中一次並獲得 1 層星光"),
            ("保持生命值为满并提高美工刀的攻击力", "保持生命值為滿並提高美工刀的攻擊力"),
            ("复制该牌并返回手牌", "複製該牌並返回手牌"),
            ("复制该牌并加入手牌", "複製該牌並加入手牌"),
            ("获得卡牌并施加标记", "獲得卡牌並施加標記"),
            ("易满足，并按治愈层数额外增加攻击力", "易滿足，並按治癒層數額外增加攻擊力"),
            ("出牌数 +1，并消耗 5 层充能", "出牌數 +1，並消耗 5 層充能"),
            ("攻击力 +2，并额外增加等同治愈层数的攻击力", "攻擊力 +2，並額外增加等同治癒層數的攻擊力"),
            ("3 层星光，并使所有角色", "3 層星光，並使所有角色"),
            ("4 星币，并早进入星光流派", "4 星幣，並早進入星光流派"),
            ("另一张效果牌并另获得 1 张卡牌", "另一張效果牌並另獲得 1 張卡牌"),
            ("复制最后使用的那张效果牌", "複製最後使用的那張效果牌"),
            ("每逢第 3 轮多发 5 星币", "每逢第 3 輪多發 5 星幣"),
            ("收益低，占流派名额", "收益低，佔流派名額"),
            ("标靶、手持风扇等标记来源", "標靶、手持風扇等標記來源"),
            ("经常扎进怪物堆里挑战", "經常扎進怪物堆裡挑戰"),
            ("几乎抵消这次伤害", "幾乎抵消這次傷害"),
            ("虚弱印记更常挂着", "虛弱印記更常掛著"),
            ("施加的是同一种标记", "施加的是同一種標記"),
            ("最多 3 种新春祝福", "最多 3 種新春祝福"),
            ("受到攻击多半只损失 1 点生命", "受到攻擊多半只損失 1 點生命"),
            # 先锁的「种」「并X」不能把相邻的词切开
            ("这几种标记", "這幾種標記"),
            ("合并按钮", "合併按鈕"),
            ("一并对待", "一併對待"),
        )
        for source, want in pairs:
            with self.subTest(source=source):
                self.assertEqual(lang.to_hant(source, strict=True), (want, []))


class _ModuleImports(ast.NodeVisitor):
    """模块这一层的 from ... import（含写在 if、try 里的）：函数、类里面的导入不进模块的命名空间，localize 碰不到。"""

    def __init__(self):
        self.found: list[ast.ImportFrom] = []

    def visit_ImportFrom(self, node):
        self.found.append(node)

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_ClassDef = visit_Lambda = visit_FunctionDef


def _has_han(value) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for _, text in lang._text_leaves("", value) for ch in text)


def kept_problems(module, tree: ast.AST) -> list[str]:
    """调了 localize 的模块 module（tree 是它的语法树）里，从 advisor.ui 以外的模块导进来、值有汉字、又没写进 HANT_KEEP
    的大写名字：每个一条，写出模块、名字、从哪来。

    相对导入按模块的 __package__ 换成绝对模块名（包的 __init__ 就是这个包本身）；from x import * 按 x 的 __all__（没有
    时是不以下划线开头的名字）。
    """
    visitor = _ModuleImports()
    visitor.visit(tree)
    keep = lang._kept(module.__dict__)
    problems: list[str] = []
    for node in visitor.found:
        try:
            source = importlib.util.resolve_name("." * node.level + (node.module or ""), module.__package__)
        except (ImportError, ValueError):
            continue
        if source == "advisor.ui" or source.startswith("advisor.ui."):
            continue
        try:
            origin = importlib.import_module(source)
        except ImportError:
            continue
        for alias in node.names:
            if alias.name == "*":
                names = getattr(origin, "__all__", None) or [name for name in vars(origin) if not name.startswith("_")]
                pairs = [(name, name) for name in names]
            else:
                pairs = [(alias.asname or alias.name, alias.name)]
            for local, name in pairs:
                if (lang.CONSTANT_NAME.fullmatch(local) and local not in keep
                        and _has_han(getattr(origin, name, None))):
                    problems.append(f"{module.__name__}：{local}（from {source} import {name}）")
    return problems


class ImportKeepTest(Case):
    def test_imported_constants_are_kept(self):
        """调了 localize 的模块里，从 advisor.ui 以外的模块导进来的大写名字、值有汉字的，必须写进 HANT_KEEP：
        转了就和引擎、资料库、写进文件的那份对不上（例如引擎给的首推名「刷新」）。"""
        problems = []
        for rel in hant_table.localized_modules():
            module = importlib.import_module(hant_table._module_name(rel))
            problems += kept_problems(module, ast.parse((PROJECT / rel).read_text(encoding="utf-8")))
        self.assertEqual(problems, [], "值有汉字、转了就和别处对不上，请写进模块的 HANT_KEEP\n"
                         + "\n".join(problems))

    def test_reports_missing_keep(self):
        """反例：包的 __init__ 和包里的模块都一样，从引擎导进来的「刷新」（REROLL）没写进 HANT_KEEP 时报出来；写进了的、
        导进来的是模块的、函数里导入的、从 advisor.ui 导进来的不报。"""
        source = ("from ...engine.advice_text import REROLL\n"
                  "from ...engine.advice_text import REROLL as KEPT\n"
                  "from .. import lang\n"
                  "try:\n"
                  "    from ...engine import advice_text as ADVICE\n"
                  "except ImportError:\n"
                  "    pass\n"
                  "def later():\n"
                  "    from ...engine.advice_text import REROLL as LATER\n")
        for name in ("advisor.ui.settings", "advisor.ui.settings.probe"):
            with self.subTest(module=name):
                module = types.ModuleType(name)
                module.__package__ = "advisor.ui.settings"
                module.HANT_KEEP = ("KEPT",)
                self.assertEqual(kept_problems(module, ast.parse(source)),
                                 [f"{name}：REROLL（from advisor.engine.advice_text import REROLL）"])

    def test_reports_star_import(self):
        """反例：from x import * 带进来的、值有汉字的大写名字照样要写进 HANT_KEEP。"""
        origin = types.ModuleType("acc_probe_origin")
        origin.__all__ = ["LABEL", "CODE", "KEPT"]
        origin.LABEL, origin.CODE, origin.KEPT = "刷新", "x1", "刷新"
        module = types.ModuleType("advisor.ui.probe")
        module.__package__ = "advisor.ui"
        module.HANT_KEEP = ("KEPT",)
        with mock.patch.dict(sys.modules, {"acc_probe_origin": origin}):
            self.assertEqual(kept_problems(module, ast.parse("from acc_probe_origin import *\n")),
                             ["advisor.ui.probe：LABEL（from acc_probe_origin import LABEL）"])


class TableTest(Case):
    def test_has_all_dictionary_entries(self):
        """表收的是 5 份词典的全部条目（抽查界面上用不到的词），不只是界面文字用得到的：显示处现转的文字什么词
        都可能有。"""
        self.assertEqual(TABLE["opencc"], hant_table.OPENCC_VERSION)
        samples = {
            "st_phrases": {"一丝不挂": ["一絲不掛"], "一了百了": ["一了百了"]},
            "st_chars": {"郁": ["鬱", "郁"]},
            "tw_phrases": {"B超": ["超音波"], "SQL注入": ["SQL隱碼攻擊"]},
            "tw_variants_phrases": {"張棟樑": ["張棟樑"]},
            "tw_variants": {"樑": ["梁", "樑"], "裏": ["裡"]},
        }
        for kind, entries in samples.items():
            for key, value in entries.items():
                with self.subTest(kind=kind, key=key):
                    self.assertEqual(TABLE[kind][key], value)

    def test_all_texts_range(self):
        """show --all 和繁体字体子集收字的范围（all_texts）：调了 localize 的模块的常量、界面文字、t() 里写死的文字、
        advisor/ui 以外会显示的常量（找不到游戏窗口的几句、平台名）都在；t() 里新写的文字转成繁体以后的字收进繁体子集的
        收字范围。"""
        texts = {text for _, text in hant_table.all_texts()}
        for label, text in [*hant_table.localized_texts(), *hant_table.shown_texts()]:
            if any("一" <= ch <= "鿿" for ch in text):
                self.assertIn(text, texts, label)
        from advisor.vision import capture

        shown = {text for _, text in hant_table.shown_texts()}
        for text in (capture.NOT_FOUND, capture.NOT_FOUND_PLATFORM, capture.NOT_FOUND_ONE, capture.MINIMIZED,
                     capture.BOTH_NAME, *capture.PLATFORM_NAMES.values()):
            self.assertIn(text, shown)
        from tools import make_font_subset

        literal = [("advisor/probe.py", 3, "鱻干净")]
        with mock.patch.object(hant_table, "t_literals", return_value=literal):
            self.assertIn("鱻干净", {text for _, text in hant_table.all_texts()})
            collected = make_font_subset.collect_text_hant()
        self.assertIn("鱻", collected)
        self.assertIn("乾", collected)

    def test_module_name(self):
        """--module 给 .py 文件路径或模块名都行，看的是同一个模块；包的 __init__.py 是包名本身（不另导入一份
        「包.__init__」）。"""
        self.assertEqual(hant_table._module_name("advisor/ui/settings/dialog.py"), "advisor.ui.settings.dialog")
        self.assertEqual(hant_table._module_name("advisor.ui.settings.dialog"), "advisor.ui.settings.dialog")
        self.assertEqual(hant_table._module_name("advisor/ui/settings/__init__.py"), "advisor.ui.settings")
        self.assertEqual(hant_table._module_name(str(PROJECT / "advisor" / "ui" / "lang.py")), "advisor.ui.lang")
        by_path = hant_table.module_texts("advisor/ui/settings/dialog.py")
        self.assertEqual(by_path[0], "advisor.ui.settings.dialog")
        self.assertEqual(hant_table.module_texts("advisor.ui.settings.dialog"), by_path)


class DigestTest(Case):
    def test_table_is_current(self):
        """资料库里的名字改了以后，表里的 digest 和现在算出来的不一致：提示重新生成。"""
        self.assertEqual(TABLE["digest"], hant_table.digest(),
                         "资料里的名字改了，运行 python -m tools.hant_table build 重新生成")

    def test_digest_only_follows_names(self):
        """digest 只按资料库里的名字算：改界面文字（含拼进去的版本号）不用重做表；名字变了才不一致。"""
        names = hant_table.game_names()
        with mock.patch.object(hant_table, "module_texts", side_effect=AssertionError("不该读界面文字")):
            self.assertEqual(hant_table.digest(names), TABLE["digest"])
        changed = dict(names)
        changed[next(iter(changed))] = "改了"
        self.assertNotEqual(hant_table.digest(changed), TABLE["digest"])


class TTest(Case):
    def test_hans_returns_unchanged(self):
        """简体界面原样返回，也不读表。"""
        lang.CURRENT = lang.HANS
        with mock.patch.object(lang, "to_hant", side_effect=AssertionError("简体界面不读表")):
            self.assertEqual(lang.t("软件"), "软件")
            self.assertEqual(lang.t(3), 3)

    def test_hant_converts(self):
        """繁体界面转成繁体；不是字符串的原样返回。"""
        lang.CURRENT = lang.HANT
        self.assertEqual(lang.t("软件"), lang.to_hant("软件"))
        for value in (3, None, ("软件",), b"x"):
            with self.subTest(value=value):
                self.assertIs(lang.t(value), value)

    def test_broken_table_returns_text(self):
        """表读不了时原样返回，不抛异常。"""
        lang.CURRENT = lang.HANT
        with tempfile.TemporaryDirectory(dir=paths.temp_dir()) as folder, \
                mock.patch.object(paths, "data_dir", return_value=Path(folder)):
            lang._reset_tables()
            self.assertEqual(lang.t("软件"), "软件")

    def test_result_is_cached(self):
        """同一句反复转时用缓存；strict 不走缓存。"""
        class Counting:
            calls = 0

            def convert(self, text, strict=False):
                Counting.calls += 1
                return text, []

        with mock.patch.object(lang, "_tables", return_value=Counting()):
            self.assertEqual(lang.to_hant("软件"), "软件")
            self.assertEqual(lang.to_hant("软件"), "软件")
            self.assertEqual(Counting.calls, 1)
            lang.to_hant("软件", strict=True)
            self.assertEqual(Counting.calls, 2)
            lang._reset_tables()  # 表换了：转好的结果一起忘掉
            lang.to_hant("软件")
            self.assertEqual(Counting.calls, 3)

    def test_error_returns_text(self):
        """转换出错时原样返回、不进缓存（下次照样再转）。"""
        lang.CURRENT = lang.HANT

        class Broken:
            calls = 0

            def convert(self, text, strict=False):
                Broken.calls += 1
                raise RuntimeError("转换出错")

        with mock.patch.object(lang, "_tables", return_value=Broken()):
            self.assertEqual(lang.t("软件"), "软件")
            self.assertEqual(lang.to_hant("软件", strict=True), ("软件", []))
            self.assertEqual(lang.t("软件"), "软件")
            self.assertEqual(Broken.calls, 3)

    def test_cache_is_thread_safe(self):
        """几个线程同时转、缓存满了要丢旧的时候也不出错，结果都对，缓存的条数不超过上限（悬浮窗以外的线程也可能用）。"""
        class Fast:
            def convert(self, text, strict=False):
                return text + "。", []

        errors: list[str] = []

        def work(seed: int) -> None:
            try:
                for i in range(lang._CACHE_LIMIT // 2):
                    text = f"{seed}-{i % 1500}"
                    if lang.to_hant(text) != text + "。":
                        errors.append(f"结果不对：{text}")
            except Exception as e:  # noqa: BLE001  报出来
                errors.append(repr(e))

        switch = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, switch)
        sys.setswitchinterval(1e-5)  # 线程勤换手，容易撞上
        with mock.patch.object(lang, "_tables", return_value=Fast()):
            threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(lang._converted.cache_info().currsize, lang._CACHE_LIMIT)


class LocalizeTest(Case):
    def namespace(self):
        return {
            "TEXT": "软件",
            "TUPLE": ("默认",),
            "LIST": ["鼠标"],
            "DICT": {"设置": "设置"},
            "ITEM": FakeItem("里面", 3, ("软件",)),
            "NESTED": [{"a": ("鼠标",)}],
            "KEPT": "软件",
            "HANT_KEEP": ("KEPT",),
            "lower": "软件",
            "number": 3,
            "func": lambda: None,
        }

    def test_hant_converts_constants(self):
        lang.CURRENT = lang.HANT
        ns = self.namespace()
        changed = lang.localize(ns)
        self.assertEqual(ns["TEXT"], "軟體")
        self.assertEqual(ns["TUPLE"], ("預設",))
        self.assertEqual(ns["LIST"], ["滑鼠"])
        self.assertEqual(list(ns["DICT"]), ["设置"])  # 键不转
        self.assertEqual(ns["DICT"]["设置"], "設定")
        self.assertEqual(ns["ITEM"], FakeItem("裡面", 3, ("軟體",)))
        self.assertEqual(ns["NESTED"], [{"a": ("滑鼠",)}])
        self.assertEqual(ns["KEPT"], "软件")  # HANT_KEEP 不转
        self.assertEqual(ns["lower"], "软件")  # 不是全大写的常量
        self.assertEqual(ns["number"], 3)
        self.assertNotIn("KEPT", changed)
        self.assertNotIn("HANT_KEEP", changed)
        self.assertLessEqual({"TEXT", "TUPLE", "LIST", "DICT", "ITEM", "NESTED"}, set(changed))
        self.assertNotIn("lower", changed)

    def test_hans_does_nothing(self):
        lang.CURRENT = lang.HANS
        ns = self.namespace()
        before = dict(ns)
        self.assertEqual(lang.localize(ns), [])
        self.assertEqual(ns, before)

    def test_unusual_values(self):
        """具名元组按字段重建、defaultdict 保留类型、不在构造参数里的字段不碰；比较会报错的对象原样留着，
        不抛异常（localize 在模块导入时运行）。"""
        lang.CURRENT = lang.HANT
        odd = Unequal()
        ns = {
            "PILL": FakePill("软件", "warn"),
            "BY_KEY": collections.defaultdict(list, {"k": "鼠标"}),
            "ODD": odd,
            "ITEM": WithDerived("软件", odd),
        }
        changed = lang.localize(ns)
        self.assertEqual(ns["PILL"], FakePill("軟體", "warn"))
        self.assertIsInstance(ns["PILL"], FakePill)
        self.assertIsInstance(ns["BY_KEY"], collections.defaultdict)
        self.assertEqual(dict(ns["BY_KEY"]), {"k": "滑鼠"})
        self.assertIs(ns["ODD"], odd)
        self.assertEqual(ns["ITEM"].title, "軟體")
        self.assertIs(ns["ITEM"].other, odd)
        self.assertEqual(ns["ITEM"].label, "软件")
        self.assertEqual(sorted(changed), ["BY_KEY", "ITEM", "PILL"])


SETTINGS_SCRIPT = r"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from advisor.ui import lang

lang.CURRENT = lang.HANT

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(["acc-test"])

from advisor.ui.settings import dialog

window = dialog.SettingsDialog()
groups = (dialog.GROUP_RECOG, dialog.GROUP_LOOK, dialog.GROUP_DIAG, dialog.GROUP_PRIVACY)
print(json.dumps({
    "title": dialog.SETTINGS_TITLE,
    "page": dialog.PAGE_TITLE,
    "group": dialog.GROUP_RECOG,
    "screen": dialog.SETTINGS_METHOD["screen"],
    "lang_choices": [list(item) for item in dialog.LANG_CHOICES],
    "groups_in_items": all(item.group in groups for item in dialog.ITEMS),
    "window_title": window.windowTitle(),
    "stats_link": window.stats_link.text(),
}, ensure_ascii=False))
"""


class SettingsHantTest(Case):
    def test_reimport_in_hant(self):
        """繁体界面下重新导入设置窗口：常量是繁体，ITEMS 的分组和 GROUP_* 一致，LANG_CHOICES 不变，窗口建得出来，
        「隐私与数据」一组开关说明下面能点的字写繁体（「我的統計」）。

        用子进程：同一个进程里重新导入会换掉类、影响别的测试。
        """
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.pop(lang.ENV, None)
        result = subprocess.run([sys.executable, "-B", "-c", SETTINGS_SCRIPT], cwd=PROJECT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["title"], lang.to_hant("拾星助手设置"))
        self.assertEqual(data["page"], lang.to_hant("设置"))
        self.assertEqual(data["group"], lang.to_hant("识别"))
        self.assertEqual(data["screen"], lang.to_hant("截取屏幕"))
        self.assertTrue(data["groups_in_items"])
        self.assertEqual(data["lang_choices"], [[lang.HANS, "简体中文"], [lang.HANT, "繁體中文"]])
        self.assertEqual(data["window_title"], data["title"])
        self.assertEqual(data["stats_link"], "我的統計")


if __name__ == "__main__":
    unittest.main()
