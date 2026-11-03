"""基准工具的生成文档领域（tools/baseline/docs.py）：比对时不算开头那行生成依据的指纹（改了推荐代码时一定不同，
不算文档内容的变化）；录于加指纹以前、没有这一行的旧基准照样逐行对得上；指纹以外的不同照样报出，行号按 B 那一份
文件算；只差在换行符时照旧报「文字相同，字节不同」。文件都写在测试沙箱的临时文件夹里。
"""
import unittest
from pathlib import Path

from tests.support import sandbox
from tools import make_review
from tools.baseline import docs

BODY = "# 适配表审查清单\r\n\r\n| 筹码 | 适配理由 |\r\n| 电流剑 | 概率约 60% |\r\n"


def head(fp: str) -> str:
    """清单开头那行指纹（带换行）。"""
    return make_review.FINGERPRINT_LINE.format(fp=fp) + "\r\n"


class DocsCompareTest(unittest.TestCase):
    def pair(self, review_a: str, review_b: str) -> tuple[Path, Path]:
        """A、B 两次录下的文档：审查清单按给的写，横向检查两边一样。"""
        root = sandbox.temp_dir(self, "test-baseline-docs-")
        folders = []
        for side, review in (("a", review_a), ("b", review_b)):
            folder = root / side
            folder.mkdir()
            (folder / "synergy-review.md").write_bytes(review.encode("utf-8"))
            (folder / "synergy-audit.md").write_bytes((head("0" * 16) + "# 适配表横向检查\r\n").encode("utf-8"))
            folders.append(folder)
        return folders[0], folders[1]

    def test_fingerprint_line_not_compared(self):
        a, b = self.pair(head("1" * 16) + BODY, head("2" * 16) + BODY)
        self.assertEqual(docs.compare(a, b, None), [])

    def test_old_baseline_without_fingerprint_line(self):
        a, b = self.pair(BODY, head("2" * 16) + BODY)
        self.assertEqual(docs.compare(a, b, None), [])

    def test_real_difference_line_number_in_b(self):
        a, b = self.pair(head("1" * 16) + BODY, head("2" * 16) + BODY.replace("60%", "59%"))
        self.assertEqual(docs.compare(a, b, None),
                         [("synergy-review.md:5", "| 电流剑 | 概率约 60% | / | 电流剑 | 概率约 59% |")])

    def test_line_endings_only(self):
        a, b = self.pair(head("1" * 16) + BODY, head("2" * 16) + BODY.replace("\r\n", "\n"))
        self.assertEqual(docs.compare(a, b, None), [("synergy-review.md", "文字相同，字节不同（换行符或编码）")])


if __name__ == "__main__":
    unittest.main()
