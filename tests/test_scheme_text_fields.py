"""筹码方案的名称、作者、说明：编辑窗口里三个文字框的限长，和它们在可读文字里的写法（导出再导入要完整还原）。

- 限长按字数算（一个表情算一个字，和方案数据层同一个算法）：满了以后在中间打字、粘贴、输入法上屏，多出来的字进不去，
  原有的字一个不挤掉；光标停在刚输入的字后面（Qt 的光标位置按 UTF-16 码元数，表情占两个）。
- 可读文字（advisor/schemes.py 的 to_text、parse_share）：说明、作者里的【】｜换成近似字〖〗丨，原文里本来就有的〖〗丨
  （和紧跟在这几个字前面的转义符＼）前面另加＼，导入时都照原样还原。

中文用 insert() 和输入法事件输入（QTest.keyClicks 打中文会让 Qt 直接中止进程）。
"""
import random
import unittest

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from advisor import schemes as S  # noqa: E402
from tests.test_scheme_editor import _EditorCase, settle  # noqa: E402


def commit(edit, text: str) -> None:
    """输入法上屏（在光标处提交 text）。"""
    event = QInputMethodEvent("", [])
    event.setCommitString(text)
    QApplication.sendEvent(edit, event)


class LimitTest(_EditorCase):
    def test_full_name_keeps_text(self):
        """名称满了 12 个字：在中间打字、粘贴，都进不去，后面的字不被挤掉；离开输入框存进去的仍是原来的名称。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        self.type_text(ed.name_edit, "abcdefghijkl")
        edit = ed.name_edit
        edit.setFocus()
        edit.setCursorPosition(3)
        QTest.keyClick(edit, Qt.Key_X)
        self.assertEqual((edit.text(), edit.cursorPosition()), ("abcdefghijkl", 3))
        edit.insert("一二三")
        self.assertEqual((edit.text(), edit.cursorPosition()), ("abcdefghijkl", 3))
        commit(edit, "甲乙")
        self.assertEqual((edit.text(), edit.cursorPosition()), ("abcdefghijkl", 3))
        edit.editingFinished.emit()
        settle()
        self.assertEqual(self.store.get(s.id).name, "abcdefghijkl")
        self.assertEqual(self.reopen().get(s.id).name, "abcdefghijkl")

    def test_insert_cut_to_room(self):
        """还差几个字满时在中间粘贴、输入法上屏：只进去放得下的那几个（刚输入的末尾去掉），光标停在进去的字后面。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        edit = ed.name_edit
        self.type_text(edit, "0123456789")
        edit.setCursorPosition(5)
        edit.insert("ABCDE")
        self.assertEqual((edit.text(), edit.cursorPosition()), ("01234AB56789", 7))
        self.type_text(edit, "一二三四五六七八九十")
        edit.setCursorPosition(2)
        commit(edit, "甲乙丙")
        self.assertEqual((edit.text(), edit.cursorPosition()), ("一二甲乙三四五六七八九十", 4))
        edit.editingFinished.emit()
        settle()
        self.assertEqual(self.reopen().get(s.id).name, "一二甲乙三四五六七八九十")

    def test_emoji_cursor_counts_code_units(self):
        """粘贴 14 个表情：留 12 个，光标在最后（UTF-16 码元 24，不是 12：那会停在第 6 个表情后面，接着打的字插到中间、
        把末尾的表情挤掉）；再打字进不去。作者、说明同样。"""
        ed = self.editor()
        self.new_scheme(ed)
        for edit, limit in ((ed.author_edit, S.AUTHOR_MAX), (ed.note_edit, S.NOTE_MAX)):
            with self.subTest(limit=limit):
                edit.setFocus()
                edit.selectAll()
                edit.insert("🔥" * (limit + 2))
                self.assertEqual((edit.text(), edit.cursorPosition()), ("🔥" * limit, 2 * limit))
                QTest.keyClick(edit, Qt.Key_A)
                self.assertEqual(edit.text(), "🔥" * limit)
                edit.setCursorPosition(4)  # 第 2 个表情后面
                edit.insert("ab")
                self.assertEqual((edit.text(), edit.cursorPosition()), ("🔥" * limit, 4))

    def test_mixed_text_cut_keeps_tail(self):
        """说明里表情和汉字混着、离满还差 2 个字：在开头粘贴 5 个字，只进去前 2 个，后面原有的字（含末尾的表情）都在。"""
        ed = self.editor()
        s = self.new_scheme(ed)
        edit = ed.note_edit
        original = ("说明🔥" * 20)[:S.NOTE_MAX - 2]
        self.type_text(edit, original)
        edit.setCursorPosition(0)
        edit.insert("甲乙丙丁戊")
        self.assertEqual(edit.text(), "甲乙" + original)
        self.assertEqual(edit.cursorPosition(), 2)
        edit.editingFinished.emit()
        settle()
        self.assertEqual(self.reopen().get(s.id).note, "甲乙" + original)


class EscapeTest(unittest.TestCase):
    """可读文字里说明、作者的近似字和转义（schemes.to_text、parse_share）。"""

    @classmethod
    def setUpClass(cls):
        cls.cat = S.get_catalog()

    def round_trip(self, note: str, author: str) -> S.Scheme:
        s = S.Scheme(hero="芬妮", name="甲", note=note, author=author, tiers={"缓冲盾牌": "core"})
        text = S.to_text(s)
        result = S.parse_share(text, self.cat)
        self.assertEqual(len(result.items), 1, text)
        item = result.items[0]
        self.assertEqual(item.problems, (), text)
        self.assertEqual((item.scheme.note, item.scheme.author, item.scheme.tiers), (s.note, s.author, s.tiers), text)
        return s

    def test_lookalikes_in_original_kept(self):
        """说明、作者里本来就有〖〗丨、＼：导出时前面加＼，导入时照原样留下，不换成【】｜。"""
        cases = [
            ("前期攒钱丨后期输出", "某丨某"),
            ("〖原创〗【分享】", "〖作者〗"),
            ("＼〖", "＼丨"),
            ("a＼b", "＼"),
            ("＼【｜】", "＼＼｜"),
            ("〖X〗筹码方案 v2", "丨说明：x"),
        ]
        for note, author in cases:
            with self.subTest(note=note, author=author):
                self.round_trip(note, author)
        text = S.to_text(S.Scheme(hero="芬妮", name="甲", note="前期丨〖后期〗", author="a＼b"))
        self.assertTrue(text.endswith("｜说明：前期＼丨＼〖后期＼〗｜作者：a＼b"), text)

    def test_plain_text_unchanged(self):
        """没有这几个字的说明、作者，导出的文字照原样（不加转义）。"""
        text = S.to_text(S.Scheme(hero="芬妮", name="甲", note="以星光为主，治愈为辅 (v1)", author="某人"))
        self.assertTrue(text.endswith("｜说明：以星光为主，治愈为辅 (v1)｜作者：某人"), text)

    def test_random_round_trip(self):
        """随机拼的说明、作者（【】｜〖〗丨＼、分段的字样、另一套的开头、表情）导出再导入都完整还原。"""
        pieces = ["【", "】", "｜", "〖", "〗", "丨", "＼", "＼＼", "a", " ", "说明：", "作者：", "｜说明：", "｜作者：",
                  "【X】筹码方案 v2", "【Y】CA2-", "〖Z〗", "丨作者：", "＼丨", "说", "明", "：", "🔥"]
        rng = random.Random(20261010)
        for _ in range(2000):
            note = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
            author = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 5)))
            self.round_trip(note, author)

    def test_hand_written_backslash(self):
        """手写的可读文字里＼后面不是近似字时照原样留下；单独的近似字换回【】｜。"""
        text = "【甲】筹码方案 v2｜芬妮｜必选：缓冲盾牌｜说明：a＼b〖c〗丨d｜作者：＼"
        item = S.parse_share(text, self.cat).items[0]
        self.assertEqual((item.scheme.note, item.scheme.author), ("a＼b【c】｜d", "＼"))


if __name__ == "__main__":
    unittest.main()
