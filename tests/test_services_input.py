"""悬浮窗调外部模块时整理返回值的两个函数（advisor/ui/services.py）：缺字段、类型不对时不出错、不报出离谱的数。

- count：下载结果 (ok, failed) 里的数量。数字、清单照常；负数、算不出长度的对象按 0；一段文字按一项（不按字数算）。
- sections：「关于」窗口的各节。正常的 [(标题, 正文)] 照原样；整段文字算一节；数字这类不能逐项取的按没有（不抛
  TypeError，「关于」窗口照常打开）；缺的标题、正文按空（不写成「None」）；清单里的一项是一段文字时当正文（不把
  文字的 title 方法当成标题）。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from advisor import version
from advisor.ui.services import count, sections


class CountTest(unittest.TestCase):
    def test_normal(self):
        for value, want in ((None, 0), (0, 0), (3, 3), (True, 1), (False, 0), ([], 0), (["a", "b"], 2),
                            (("a",), 1), ({"a": 1}, 1)):
            with self.subTest(value=value):
                self.assertEqual(count(value), want)

    def test_odd_values(self):
        for value, want in ((-2, 0), (2.7, 2), (-1.5, 0), (float("nan"), 0), (float("inf"), 0),
                            ("chips/星币锤.png", 1), ("", 0), (b"x", 1), (object(), 0), (iter([1, 2]), 0)):
            with self.subTest(value=value):
                self.assertEqual(count(value), want)


class SectionsTest(unittest.TestCase):
    def test_real_about_sections_unchanged(self):
        raw = version.about_sections()
        self.assertEqual(sections(raw), [(str(t), str(b)) for t, b in raw])

    def test_accepted_shapes(self):
        self.assertEqual(sections(None), [])
        self.assertEqual(sections({"版本": "1.0"}), [("版本", "1.0")])
        self.assertEqual(sections([["版本", "1.0"], {"title": "许可证", "text": "AGPL"}, {"title": "附注", "body": "说明"},
                                   SimpleNamespace(title="隐私", text="只在本机"), "单独一段"]),
                         [("版本", "1.0"), ("许可证", "AGPL"), ("附注", "说明"), ("隐私", "只在本机"), ("", "单独一段")])
        self.assertEqual(sections(iter([("甲", "乙")])), [("甲", "乙")])  # 生成器、迭代器也行

    def test_odd_shapes(self):
        self.assertEqual(sections(12), [])  # 不能逐项取
        self.assertEqual(sections("整段文字"), [("", "整段文字")])  # 不拆成一个字一节
        self.assertEqual(sections([("标题", None), (None, "正文"), ("只有正文",), (), {"title": None}]),
                         [("标题", ""), ("", "正文"), ("", "只有正文"), ("", "")])
        self.assertEqual(sections({"标题": None}), [("标题", "")])
        self.assertEqual(sections([SimpleNamespace(title="只有标题")]), [("只有标题", "")])


if __name__ == "__main__":
    unittest.main()
