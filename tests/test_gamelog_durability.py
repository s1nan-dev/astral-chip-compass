"""识别记录的写盘加固（advisor/gamelog.py）：写不成 UTF-8 的一条只丢这一条，末尾半行先补换行（撤销换段接回上一段时
也一样），含 U+2028/U+2029/U+0085 的记录不被按换行切开。用假时钟，临时文件放在 .tmp 下。
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from advisor import paths
from advisor.gamelog import GameRecorder, read_segment


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-gamelog-durability"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class Clock:
    def __init__(self, start: datetime = datetime(2026, 10, 3, 12, 40, 0)):
        self.now = start.timestamp()

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class RecorderCase(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "games"
        self.clock = Clock()
        self.errors: list[tuple[str, str]] = []

    def recorder(self, **kwargs) -> GameRecorder:
        kwargs.setdefault("clock", self.clock)
        kwargs.setdefault("on_error", lambda title, detail: self.errors.append((title, detail)))
        return GameRecorder(self.folder, **kwargs)

    def hud(self, rec: GameRecorder, rnd: int, seconds: float = 3.5) -> None:
        rec.add(self.clock.tick(seconds), {"kind": "read:hud", "round": rnd, "map": None})

    def rounds(self, path: Path) -> list[int]:
        # 用真正的读法（read_segment）：写到一半的半行、零字节等本身会被跳过
        return [r["round"] for r in read_segment(path) if r.get("kind") == "read:hud"]


class EncodingTest(RecorderCase):
    def test_undecodable_record_drops_only_that_line(self):
        """一个写不成 UTF-8 的字只丢掉这一条：其余记录照常写盘，读回来也在；错误只记一次。"""
        rec = self.recorder().open()
        self.hud(rec, 1)
        rec.add(self.clock.tick(1), {"kind": "read:layer", "cover_class": "坏\ud800字"})
        self.hud(rec, 2)
        self.assertTrue(rec.flush())
        self.assertEqual(self.rounds(rec.path), [1, 2])
        self.assertEqual(len(self.errors), 1)
        self.assertIn("UTF-8", self.errors[0][0])
        # 之后照常记：坏行没有残留在队列里，不会每帧都出错
        self.hud(rec, 3)
        rec.add(self.clock.tick(1), {"kind": "picked", "chip": "大背包"})
        self.assertEqual(self.rounds(rec.path), [1, 2, 3])
        self.assertEqual(len(self.errors), 1)


class HalfLineTest(RecorderCase):
    def test_new_line_after_half_written_line(self):
        """文件末尾是写到一半的半行时，下一条先补换行再写：新记录能读回来，半行仍被跳过。"""
        rec = self.recorder().open()
        self.hud(rec, 4)
        rec.flush()
        with rec.path.open("a", encoding="utf-8", newline="") as f:
            f.write('{"time": "2026-10-03T12:40:05.000", "kind": "read:hu')  # 没有换行
        self.hud(rec, 5)
        rec.flush()
        self.assertEqual(self.rounds(rec.path), [4, 5])

    def test_zero_bytes_at_end(self):
        """断电后末尾是一串零字节：同样先补换行，新记录读得回来。"""
        rec = self.recorder().open()
        self.hud(rec, 7)
        rec.flush()
        with rec.path.open("ab") as f:
            f.write(b"\x00\x00\x00")
        self.hud(rec, 8)
        rec.flush()
        self.assertEqual(self.rounds(rec.path), [7, 8])


class SplitLineTest(RecorderCase):
    def test_line_separator_chars_do_not_split_a_record(self):
        """U+2028、U+2029、U+0085 不是真的换行：含它们的记录整条读回来（splitlines 会把它切开丢掉）。"""
        rec = self.recorder().open()
        for i, sep in enumerate(("\u2028", "\u2029", "\u0085")):
            rec.add(self.clock.tick(1), {"kind": "read:layer", "cover_class": f"类{sep}名{i}"})
        rec.flush()
        got = [r["cover_class"] for r in read_segment(rec.path) if r.get("kind") == "read:layer"]
        self.assertEqual(got, ["类\u2028名0", "类\u2029名1", "类\u0085名2"])


class NulTest(RecorderCase):
    def test_nul_inside_a_value_survives(self):
        """正文里的零字节写成转义：读的时候只去掉行首尾的零字节，不影响它。"""
        rec = self.recorder().open()
        rec.add(self.clock.tick(1), {"kind": "read:layer", "cover_class": "前\x00后"})
        rec.flush()
        got = [r["cover_class"] for r in read_segment(rec.path) if r.get("kind") == "read:layer"]
        self.assertEqual(got, ["前\x00后"])


class UndoAppendTest(RecorderCase):
    def test_undo_after_half_line_of_previous_run(self):
        """撤销换段时接回的上一段是上次运行留下的、末尾是写到一半的半行：同样先补换行，撤销那一行和移过去的记录都在。"""
        first = self.recorder().open()
        self.hud(first, 1)
        first.flush()
        with first.path.open("a", encoding="utf-8", newline="") as f:
            f.write('{"time": "2026-10-03T12:40:05.000", "kind": "read:hu')  # 上次运行在这里被结束
        second = self.recorder()  # 这次运行开的第一段就是判定的新一局
        self.clock.tick(5)
        second.new_segment("newgame", cause="轮次变小")
        self.hud(second, 2)
        self.assertTrue(second.undo_segment())
        self.assertIn("segment_undo", [r.get("kind") for r in read_segment(first.path)])
        self.assertEqual(self.rounds(first.path), [1, 2])


if __name__ == "__main__":
    unittest.main()
