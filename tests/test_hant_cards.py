"""繁体界面下推荐卡片、已有筹码、方案卡片的显示文字和 text.py 的断行词表。

核对（子进程里先定语言再导入，同一个进程里重新导入会换掉类、影响别的测试）：
- text.py 不调 localize：常量照旧简体，诊断包的推荐全文也照旧简体；
- text.chip_tip、text.breakdown_html 这类只给显示用的函数，繁体界面下转成繁体；
- 断行用的双字词、术语在繁体界面下用转过的一份（逐字词转，转出来不是两个字的丢掉）；
- 卡片模块里拿来和引擎、会话比对或写进日志的常量（REROLL、PLACE_*、SOURCE_LABEL、REROLL_LABEL、
  FEEDBACK_REASONS）照旧简体，显示用的常量转成繁体；
- owned_card、scheme_card 不加 localize，显示处用 t() 转。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from advisor.ui import lang

PROJECT = Path(__file__).resolve().parents[1]

CARDS_SCRIPT = r"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from advisor.ui import lang

lang.CURRENT = lang.HANT

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(["acc-test"])

from advisor.ui import text
from advisor.ui.overlay_parts import advice_card, owned_card, scheme_card

chip = SimpleNamespace(name="测试筹码", hant="測試籌碼", rarity="普通", school="治愈",
                       effect="获得 2 个筹码，提高攻击力", tags=["攻击"])
item = SimpleNamespace(
    chip=chip, percent=0.5, lookahead=0.0, lookahead_reason="",
    parts=[SimpleNamespace(label="角色特点", score=2.0, reason="提高攻击力", short="", origin="", net="")],
)


class FakeDB:
    def __init__(self):
        self.chips = {"测试筹码": chip}
        self.heroes = {}
        self.mutators = {}
        self.player_synergy = set()


owned = owned_card.OwnedCard.__new__(owned_card.OwnedCard)
owned.db = FakeDB()
source = owned.extra_chip_source(SimpleNamespace(owned=["测试筹码"], mutators=[]))

scheme = scheme_card.SchemeCard.__new__(scheme_card.SchemeCard)
scheme.db = FakeDB()
scheme_tip = scheme.scheme_tip(None, "资料库里没有的角色")

event = SimpleNamespace(advice=None, kind="筹码三选一", raw=["测试筹码"], problems=["匹配度低"])

print(json.dumps({
    "chip_tip": text.chip_tip(chip),
    "breakdown": text.breakdown_html(item),
    "owned_source": source,
    "scheme_tip": scheme_tip,
    "advice_text": text.advice_text(None, event),
    "keep_words": text.keep_words("防禦力 +2"),
    "terms": list(text._terms_for(True)),
    "advice_keep": {
        "REROLL": advice_card.REROLL,
        "PLACE_LOCKED": advice_card.PLACE_LOCKED_TEXT,
        "PLACE_UNMARKED": advice_card.PLACE_UNMARKED_TEXT,
        "REROLL_LABEL": advice_card.REROLL_LABEL,
        "SOURCE": advice_card.SOURCE_LABEL,
        "FEEDBACK": list(advice_card.FEEDBACK_REASONS),
    },
    "display_constant": advice_card.FIX_HINT,
    "text_constants": {
        "REROLL_LABEL": text.REROLL_LABEL,
        "TAG_LABEL": text.TAG_LABEL,
        "UNRECORDED": text.UNRECORDED_LABEL,
        "MATE": text.MATE_LABEL,
        "option_name": text.option_name("刷新"),
        "rerolls_text": text.rerolls_text(3),
    },
    "owned_constants": {"GENERAL_SCHOOL": owned_card.GENERAL_SCHOOL},
    "scheme_constants": {"SCHEME_DEFAULT": scheme_card.SCHEME_DEFAULT},
}, ensure_ascii=False))
"""


class CardsHantTest(unittest.TestCase):
    _data = None

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.pop(lang.ENV, None)
        result = subprocess.run([sys.executable, "-B", "-c", CARDS_SCRIPT], cwd=PROJECT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls._data = json.loads(result.stdout.strip().splitlines()[-1])

    def hant(self, text: str) -> str:
        return lang.to_hant(text)

    def test_display_helpers_are_hant(self):
        """只给显示用的函数（chip_tip、breakdown_html）在繁体界面转成繁体。"""
        tip = self._data["chip_tip"]
        self.assertIn("測試籌碼", tip)
        self.assertIn(self.hant("获得 2 个筹码，提高攻击力"), tip)
        self.assertNotIn("获得", tip)
        breakdown = self._data["breakdown"]
        self.assertIn(self.hant("角色专属规则"), breakdown)
        self.assertIn(self.hant("提高攻击力"), breakdown)
        self.assertIn(self.hant("推荐度 50%"), breakdown)

    def test_card_display_uses_t(self):
        """owned_card、scheme_card 不加 localize，显示处包 t()：来源说明和方案悬停说明是繁体。"""
        self.assertIn(self.hant("已持有的「"), self._data["owned_source"])
        self.assertIn("測試籌碼", self._data["owned_source"])
        self.assertIn(self.hant("拾星助手自带的筹码方案"), self._data["scheme_tip"])
        self.assertIn(self.hant("无"), self._data["scheme_tip"])

    def test_keep_constants_are_simplified(self):
        """和引擎、会话比对或写进推荐日志的常量照旧简体；显示用的常量转成繁体。"""
        keep = self._data["advice_keep"]
        self.assertEqual(keep["REROLL"], "刷新")
        self.assertEqual(keep["PLACE_LOCKED"], "其他流派")
        self.assertEqual(keep["PLACE_UNMARKED"], "未标记")
        self.assertEqual(keep["REROLL_LABEL"], "刷新")
        self.assertEqual(keep["SOURCE"], {"levelup": "升星", "shop": "筹码商店", "task": "任务"})
        self.assertEqual(keep["FEEDBACK"], ["照顾队友", "地图因素", "推荐有误", "想试别的玩法"])
        self.assertNotIn("识别", self._data["display_constant"])
        self.assertIn(self.hant("识别有误"), self._data["display_constant"])

    def test_text_module_stays_simplified(self):
        """text.py 不调 localize：常量、诊断包的推荐全文都照旧简体。"""
        constants = self._data["text_constants"]
        self.assertEqual(constants["REROLL_LABEL"], "刷新")
        self.assertEqual(constants["TAG_LABEL"], "标签分")
        self.assertEqual(constants["UNRECORDED"], "未收录")
        self.assertEqual(constants["MATE"], "队友")
        self.assertEqual(constants["option_name"], "刷新")
        self.assertEqual(constants["rerolls_text"], "刷新 ×3")
        diag = self._data["advice_text"]
        self.assertIn("事件：筹码三选一", diag)
        self.assertIn("识别文字：「测试筹码」", diag)
        self.assertIn("识别提示：匹配度低", diag)
        self.assertNotIn("籌碼三選一", diag)

    def test_other_card_modules_not_localized(self):
        """owned_card、scheme_card 没有自己的界面文字常量、不调 localize：text.py 的常量照样是简体。"""
        self.assertEqual(self._data["owned_constants"]["GENERAL_SCHOOL"], "通用")
        self.assertEqual(self._data["scheme_constants"]["SCHEME_DEFAULT"], "默认推荐")

    def test_word_tables_have_hant_copy(self):
        """繁体界面下断行用的双字词、术语是转过的一份（_WORDS 转出来不是两个字的丢掉）。"""
        self.assertIn(self.hant("移动点数"), self._data["terms"])
        words = self._data["keep_words"]
        self.assertIn(text_joiner(), words)
        # 「防禦」在繁体词表里：防和禦之间插了 WORD JOINER
        self.assertIn("防" + text_joiner() + "禦", words)


def text_joiner() -> str:
    """text.keep_words 插入的 WORD JOINER（不显示、不占宽度）。"""
    from advisor.ui import text

    return text.KEEP_JOINER


if __name__ == "__main__":
    unittest.main()
