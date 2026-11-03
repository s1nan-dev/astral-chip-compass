"""推荐日志的写盘加固（advisor/logbook.py）：末尾半行、零字节先补换行；写不进以后隔 WRITE_RETRY_S 秒再试（这期间的
记录丢掉，同一回只报一次错）；含 U+2028/U+2029/U+0085 的记录不被按换行切开，写到一半的多字节字不让整个月份读不出。
临时文件放在 .tmp 下，写不进和时钟用 mock 造。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from advisor import logbook, paths
from advisor.pool import State


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-logbook-durability"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class LogbookCase(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.logs = Path(self._dir.name)
        patcher = mock.patch.object(paths, "logs_dir", return_value=self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        logbook._reset_for_tests()
        self.addCleanup(logbook._reset_for_tests)

    def month_file(self) -> Path:
        return next(self.logs.glob("????-??.jsonl"))

    def records(self) -> list[dict]:
        return list(logbook.iter_records())


class HalfLineTest(LogbookCase):
    def test_record_after_half_written_line(self):
        """日志末尾是半行时，下一条先补换行再写：新的记录读得回来，半行仍被跳过。"""
        logbook.record_pick("o1", "大背包")
        with self.month_file().open("a", encoding="utf-8", newline="") as f:
            f.write('{"t": "pick", "offer_id": "o2", "chosen": "标记')  # 没有换行
        logbook.new_game()
        picks = [r for r in self.records() if r["t"] == "pick"]
        self.assertEqual([p["chosen"] for p in picks], ["大背包"])
        self.assertEqual([r["t"] for r in self.records() if r["t"] == "newgame"], ["newgame"])


class WriteRetryTest(LogbookCase):
    def test_retries_after_a_pause_and_reports_once(self):
        """写不进（文件被独占、没有权限）以后不一停到底：这一回第一次写不进照常报错（调用方记进错误记录）；之后
        WRITE_RETRY_S 秒内的记录直接丢掉，不去写、不报错；到时再试一次，还写不进也不再报错；写进了这一回就结束，
        下一回第一次写不进再报一次。"""
        clock = [1000.0]
        broken = [True]
        tried: list = []
        real = logbook.fileio.append_lines

        def append(path, lines, **kwargs):
            tried.append(lines)
            if broken[0]:
                raise PermissionError(13, "拒绝访问")
            return real(path, lines, **kwargs)

        with mock.patch.object(logbook, "_monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(logbook.fileio, "append_lines", side_effect=append):
            with self.assertRaises(OSError):
                logbook.record_pick("o1", "大背包")
            clock[0] += logbook.WRITE_RETRY_S - 1
            logbook.record_pick("o2", "标记")  # 还没到再试的时候：不去写、不报错，这一条丢掉
            self.assertEqual(len(tried), 1)
            clock[0] += 1
            logbook.record_pick("o3", "标记")  # 到时再试一次：还写不进，同一回不再报错
            self.assertEqual(len(tried), 2)
            logbook.record_pick("o4", "标记")  # 又要等 WRITE_RETRY_S 秒
            self.assertEqual(len(tried), 2)
            clock[0] += logbook.WRITE_RETRY_S
            broken[0] = False
            logbook.record_pick("o5", "标记")  # 再试：写进了，这一回结束
            logbook.record_pick("o6", "标记")
            self.assertEqual(len(tried), 4)
            broken[0] = True
            with self.assertRaises(OSError):  # 新的一回第一次写不进：再报一次
                logbook.record_pick("o7", "标记")
        self.assertEqual([r["offer_id"] for r in self.records() if r["t"] == "pick"], ["o5", "o6"])


class SplitLineTest(LogbookCase):
    def test_line_separator_chars_do_not_split_a_record(self):
        logbook.record_feedback("o1", "地图因素", "note\u2028here")
        logbook.record_feedback("o2", "推荐有误", "note\u2029here")
        logbook.record_feedback("o3", "照顾队友", "note" + chr(0x85) + "here")  # U+0085
        *notes, nel = [r["note"] for r in self.records() if r["t"] == "feedback"]
        self.assertEqual(nel, "note" + chr(0x85) + "here")
        self.assertEqual(notes, ["note\u2028here", "note\u2029here"])


class DamagedTailTest(LogbookCase):
    def test_nul_inside_a_value_survives(self):
        """正文里的零字节写成转义：读的时候只去掉行首尾的零字节，不影响它。"""
        logbook.record_feedback("o1", "推荐有误", "前\x00后")
        self.assertEqual([r["note"] for r in self.records() if r["t"] == "feedback"], ["前\x00后"])

    def test_zero_bytes_at_end(self):
        """断电后末尾是一串零字节：下一条先补换行再写，前后的记录都读得回来。"""
        logbook.record_pick("o1", "大背包")
        with self.month_file().open("ab") as f:
            f.write(b"\x00" * 16)
        logbook.record_pick("o2", "标记")
        self.assertEqual([r["offer_id"] for r in self.records() if r["t"] == "pick"], ["o1", "o2"])

    def test_torn_multibyte_char_does_not_hide_the_month(self):
        """最后一条写到一半、断在一个汉字的中间：只丢这半条，这个月的其余记录照样读得出。"""
        logbook.record_pick("o1", "大背包")
        with self.month_file().open("ab") as f:
            f.write('{"t": "pick", "offer_id": "o2", "chosen": "大'.encode("utf-8")[:-1])
        logbook.record_pick("o3", "标记")
        self.assertEqual([r["offer_id"] for r in self.records() if r["t"] == "pick"], ["o1", "o3"])


if __name__ == "__main__":
    unittest.main()
