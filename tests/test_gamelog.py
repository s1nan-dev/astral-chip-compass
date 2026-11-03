"""识别记录（advisor/gamelog.py）的单元测试：分段写盘、导出范围、撤销换段、保留和体积、脱敏、写盘出错、半行。

用假时钟，不等真实时间；临时文件放在项目的 .tmp 下。
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from advisor import gamelog, paths, version
from advisor.gamelog import GameRecorder, mask_record, player_code, read_segment, stamp

TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}$")  # 与 Session.recent_events 的 time 相同


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-gamelog"
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


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


class WriteTest(RecorderCase):
    def test_stamp_format(self):
        t = datetime(2026, 10, 3, 13, 22, 11, 250000).timestamp()
        self.assertEqual(stamp(t), "2026-10-03T13:22:11.250")

    def test_header_and_buffered_writes(self):
        """段头带版本号；不到 60 秒、不到 500 行时不写盘，到时写；picked、newgame 马上写。"""
        rec = self.recorder().open()
        self.assertIs(rec.open(), rec)  # 已经开始记录时不再开段
        files = rec.segments()
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"^20261003-124000\.jsonl$")
        head = lines(files[0])
        self.assertEqual(len(head), 1)
        self.assertEqual((head[0]["kind"], head[0]["reason"], head[0]["version"]), ("segment", "startup", version.VERSION))
        self.assertRegex(head[0]["time"], TIME)

        for rnd in range(1, 6):  # 17.5 秒
            self.hud(rec, rnd)
        self.assertEqual(len(lines(files[0])), 1)
        self.clock.tick(45)
        self.hud(rec, 6)  # 距上次写盘满 60 秒：这一条连同前面的一起写
        written = lines(files[0])
        self.assertEqual(len(written), 7)
        self.assertEqual([r["round"] for r in written[1:]], [1, 2, 3, 4, 5, 6])
        for r in written:
            self.assertRegex(r["time"], TIME)
        self.assertEqual(list(written[1])[:2], ["time", "kind"])  # time 在最前，和 recent_events 一样

        self.hud(rec, 7)
        self.assertEqual(len(lines(files[0])), 7)
        rec.add(self.clock.tick(1), {"kind": "picked", "chip": "标记喷罐"})
        self.assertEqual(len(lines(files[0])), 9)
        for kind in ("newgame", "missed_pick", "panel_read", "game_over"):
            rec.add(self.clock.tick(1), {"kind": kind})
            self.assertEqual(lines(files[0])[-1]["kind"], kind)

    def test_flush_by_line_count_and_explicit_flush(self):
        rec = self.recorder(flush_lines=3).open()
        path = rec.path
        self.hud(rec, 1)
        self.hud(rec, 2)
        self.assertEqual(len(lines(path)), 1)
        self.hud(rec, 3)
        self.assertEqual(len(lines(path)), 4)
        self.hud(rec, 4)
        self.assertTrue(rec.flush())
        self.assertEqual(len(lines(path)), 5)
        self.hud(rec, 5)
        rec.close()
        self.assertEqual(lines(path)[-1]["round"], 5)

    def test_add_before_open_starts_a_segment(self):
        rec = self.recorder()
        self.hud(rec, 1)
        rec.flush()
        records = lines(rec.path)
        self.assertEqual((records[0]["reason"], records[1]["round"]), ("startup", 1))

    def test_record_time_comes_from_caller(self):
        rec = self.recorder().open()
        t = datetime(2026, 10, 3, 13, 0, 0, 500000).timestamp()
        rec.add(t, {"time": "旧的", "kind": "status"})
        records, _ = rec.collect()
        self.assertEqual(records[-1], {"time": "2026-10-03T13:00:00.500", "kind": "status"})

    def test_mark_is_written_at_once_and_keeps_segment(self):
        rec = self.recorder().open()
        self.hud(rec, 12)
        rec.mark("game_over", why="结算页", rounds=12)
        self.assertEqual(len(rec.segments()), 1)
        last = lines(rec.path)[-1]
        self.assertEqual((last["kind"], last["why"], last["rounds"]), ("mark:game_over", "结算页", 12))
        self.assertEqual(len(lines(rec.path)), 3)  # 前面那条读数也一起写了

    def test_threads(self):
        rec = self.recorder(flush_lines=50).open()

        def work(n):
            for i in range(200):
                rec.add(self.clock(), {"kind": "read:stars", "worker": n, "i": i})

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        rec.flush()
        self.assertEqual(len(lines(rec.path)), 1 + 800)


class SegmentTest(RecorderCase):
    def test_collect_previous_and_current(self):
        """换段产生新文件；collect 只取上一段和当前段，时间有序，各段以段头开始。"""
        rec = self.recorder().open()
        self.hud(rec, 15)
        self.clock.tick(60)
        first = rec.new_segment("newgame", cause="select", why="选角界面")
        self.hud(rec, 1)
        self.hud(rec, 2)
        self.clock.tick(60)
        second = rec.new_segment("newgame", cause="round", why="轮次变小（第 9 轮 → 第 1 轮）")
        self.hud(rec, 1)
        self.assertEqual(len(rec.segments()), 3)
        self.assertEqual(rec.path, second)

        records, meta = rec.collect()
        heads = [r for r in records if r["kind"] == "segment"]
        self.assertEqual([h["cause"] for h in heads], ["select", "round"])
        self.assertEqual(heads[1]["why"], "轮次变小（第 9 轮 → 第 1 轮）")
        self.assertEqual(records[0]["kind"], "segment")
        self.assertNotIn(15, [r.get("round") for r in records])  # 更早的一段不导出
        times = [r["time"] for r in records]
        self.assertEqual(times, sorted(times))
        self.assertEqual([s["file"] for s in meta["segments"]], [first.name, second.name])
        self.assertEqual([s["reason"] for s in meta["segments"]], ["newgame", "newgame"])
        self.assertEqual(meta["since"], records[0]["time"])
        self.assertEqual(meta["segments"][1]["end"], records[-1]["time"])
        self.assertEqual(sum(s["lines"] for s in meta["segments"]), len(records))
        self.assertNotIn("write_error", meta)
        # 换段时上一段没写盘的行也写完了
        self.assertEqual([r.get("round") for r in lines(first)], [None, 1, 2])

    def test_restart_joins_last_segment(self):
        """中途重启工具：新的一次运行导出时，上一段是上次运行留下的最后一段。"""
        rec = self.recorder().open()
        self.hud(rec, 7)
        rec.close()
        self.clock.tick(30)
        again = self.recorder().open()
        self.hud(again, 8)
        records, meta = again.collect()
        self.assertEqual([r.get("round") for r in records], [None, 7, None, 8])
        self.assertEqual([s["reason"] for s in meta["segments"]], ["startup", "startup"])

    def test_collect_without_open_reads_newest_two(self):
        rec = self.recorder().open()
        self.hud(rec, 3)
        rec.new_segment("newgame", cause="select")
        self.hud(rec, 1)
        rec.close()
        records, meta = self.recorder().collect()  # 没有 open：不新开文件
        self.assertEqual([r.get("round") for r in records], [None, 3, None, 1])
        self.assertEqual(len(rec.segments()), 2)

    def test_same_second_names(self):
        rec = self.recorder().open()
        a = rec.path
        b = rec.new_segment("newgame", cause="select")
        c = rec.new_segment("newgame", cause="select")
        self.assertEqual([p.name for p in (a, b, c)],
                         ["20261003-124000.jsonl", "20261003-124000-2.jsonl", "20261003-124000-3.jsonl"])
        self.assertEqual(rec.segments(), [a, b, c])  # 「-2」排在不带后缀的之后

    def test_names_follow_opening_order(self):
        """同一秒里删掉过旧段、或系统时间被调回时，新段的文件名仍排在已有的段之后。"""
        rec = self.recorder(keep=3).open()
        made = [rec.path] + [rec.new_segment("newgame", cause="select") for _ in range(4)]
        self.assertEqual(rec.segments(), made[2:])
        self.assertEqual(made[-1].name, "20261003-124000-5.jsonl")
        self.clock.now = datetime(2026, 10, 3, 9, 0, 0).timestamp()
        again = self.recorder(keep=3).open()
        self.assertEqual(again.path.name, "20261003-124000-6.jsonl")
        self.assertEqual(again.segments()[-1], again.path)
        records, _ = again.collect()
        self.assertEqual(records[0]["time"][:10], "2026-10-03")
        self.assertEqual(len(records), 2)  # 上一段（made[-1]）+ 当前段，各只有段头

    def test_undo_segment(self):
        """撤销换段：只剩一个文件，内容是原段 + segment_undo + 新段的记录（不含新段段头）。"""
        rec = self.recorder().open()
        old = rec.path
        self.hud(rec, 9)
        self.clock.tick(5)
        new = rec.new_segment("newgame", cause="round", why="轮次变小")
        self.hud(rec, 1)
        self.hud(rec, 10)
        self.assertTrue(rec.undo_segment())
        self.assertEqual(rec.segments(), [old])
        self.assertFalse(new.exists())
        self.assertEqual(rec.path, old)
        merged = lines(old)
        self.assertEqual([r["kind"] for r in merged], ["segment", "read:hud", "segment_undo", "read:hud", "read:hud"])
        self.assertEqual([r.get("round") for r in merged], [None, 9, None, 1, 10])
        undo = merged[2]
        self.assertEqual((undo["undone"], undo["cause"], undo["why"]), (new.name, "round", "轮次变小"))
        times = [r["time"] for r in merged]
        self.assertEqual(times, sorted(times))
        records, meta = rec.collect()
        self.assertEqual(records, merged)
        self.assertEqual([s["file"] for s in meta["segments"]], [old.name])
        # 之后照常记在原来那一段
        rec.add(self.clock.tick(1), {"kind": "picked", "chip": "大背包"})
        self.assertEqual(lines(old)[-1]["chip"], "大背包")
        self.assertFalse(rec.undo_segment())  # 已经撤销过

    def test_undo_restores_older_previous_segment(self):
        rec = self.recorder().open()
        self.hud(rec, 12)
        rec.new_segment("newgame", cause="select")
        self.hud(rec, 3)
        rec.new_segment("newgame", cause="round")
        self.hud(rec, 1)
        self.assertTrue(rec.undo_segment())
        records, _ = rec.collect()
        self.assertEqual([r.get("round") for r in records if r["kind"] == "read:hud"], [12, 3, 1])

    def test_only_newgame_segments_are_undone(self):
        rec = self.recorder().open()
        self.assertFalse(rec.undo_segment())  # 启动开的段没有可接回的
        rec.new_segment("size")
        self.assertFalse(rec.undo_segment())
        self.assertEqual(len(rec.segments()), 2)
        self.assertFalse(self.recorder().undo_segment())  # 还没开始记录

    def test_size_limit_starts_new_segment(self):
        """单段超过 max_bytes 时自动换段，reason 为 size。"""
        rec = self.recorder(max_bytes=2048).open()
        for i in range(30):
            rec.add(self.clock.tick(1), {"kind": "read:select", "names": ["标记喷罐", "大背包", "缓冲盾牌"], "i": i})
        rec.flush()
        files = rec.segments()
        self.assertGreaterEqual(len(files), 2)
        self.assertEqual(lines(files[1])[0]["reason"], "size")
        for f in files[:-1]:
            self.assertLess(f.stat().st_size, 2048 + 200)
        records, _ = rec.collect()
        self.assertEqual(records[0]["kind"], "segment")


class PruneTest(RecorderCase):
    def make(self, name: str, size: int = 0) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        head = json.dumps({"time": "2026-10-01T00:00:00.000", "kind": "segment", "reason": "startup"})
        path.write_text(head + "\n" + "x" * size, encoding="utf-8")
        return path

    def test_keeps_newest_ten(self):
        """已有 12 段时开新的一段：只留最新 10 段；其他文件不动。"""
        old = [self.make(f"20261001-0000{i:02d}.jsonl") for i in range(12)]
        others = [self.make("README.txt"), self.make("2026-10.jsonl"), self.make("20261001-000000.jsonl.bak")]
        rec = self.recorder().open()
        files = rec.segments()
        self.assertEqual(len(files), 10)
        self.assertEqual(files, old[3:] + [rec.path])
        for p in others:
            self.assertTrue(p.exists(), p.name)
        records, meta = rec.collect()
        self.assertEqual([s["file"] for s in meta["segments"]], [old[-1].name, rec.path.name])

    def test_total_size_limit(self):
        old = [self.make(f"20261001-0000{i:02d}.jsonl", size=1000) for i in range(6)]
        rec = self.recorder(total_bytes=3500).open()
        files = rec.segments()
        # 保留当前段和之前的两段（受保护），其余从最旧的删到总量不超过上限
        self.assertEqual(files, old[3:] + [rec.path])
        self.assertLessEqual(sum(f.stat().st_size for f in files), 3500)

    def test_protected_segments_survive_tiny_limits(self):
        old = [self.make(f"20261001-0000{i:02d}.jsonl", size=1000) for i in range(3)]
        rec = self.recorder(keep=1, total_bytes=10).open()
        self.assertEqual(rec.segments(), old[1:] + [rec.path])

    def test_prune_after_new_segment(self):
        rec = self.recorder(keep=3).open()
        made = [rec.path] + [rec.new_segment("newgame", cause="select") for _ in range(4)]
        self.assertEqual(rec.segments(), made[2:])


class MaskTest(RecorderCase):
    PLAYERS = {"kind": "read:players", "own_row": 0, "rows": [
        {"row": 0, "hero": "阿尔", "name": "星海旅人", "bot": False},
        {"row": 1, "hero": "邦妮", "name": "月下独酌", "bot": False},
        {"row": 2, "hero": "凛", "name": "电脑玩家", "bot": True},
        {"row": 3, "hero": None, "name": "", "bot": False},
    ]}

    def test_player_names(self):
        """玩家名字换成代号：同名同码、不同名不同码，电脑标记保留，不改原记录。"""
        out = mask_record(self.PLAYERS)
        rows = out["rows"]
        for row in rows[:3]:
            self.assertRegex(row["name"], r"^名字#[0-9a-f]{6}$")
        self.assertEqual(rows[3]["name"], "")
        self.assertEqual(len({r["name"] for r in rows[:3]}), 3)
        self.assertEqual(mask_record(self.PLAYERS)["rows"][0]["name"], rows[0]["name"])
        self.assertEqual([r["bot"] for r in rows], [False, False, True, False])
        self.assertEqual([r["hero"] for r in rows], ["阿尔", "邦妮", "凛", None])
        self.assertEqual(self.PLAYERS["rows"][0]["name"], "星海旅人")
        self.assertEqual(mask_record(out), out)  # 已经是代号的不再变
        self.assertEqual(player_code(rows[0]["name"]), rows[0]["name"])

    def test_selecting_line_and_name_keys(self):
        rec = {"kind": "read:select", "raw": ["星海旅人、月下独酌正在选择强化筹码…", "标记喷罐"],
               "problems": ["标题上方有「……正在选择强化筹码…」"], "player_name": "星海旅人",
               "names": ["标记喷罐", "大背包", "缓冲盾牌"]}
        out = mask_record(rec)
        self.assertRegex(out["raw"][0], r"^名字#[0-9a-f]{6}正在选择强化筹码…$")
        self.assertEqual(out["raw"][1], "标记喷罐")
        self.assertEqual(out["problems"], rec["problems"])  # 只有标点的前缀不是名字
        self.assertRegex(out["player_name"], r"^名字#[0-9a-f]{6}$")
        self.assertEqual(out["names"], rec["names"])  # 筹码名不动

    def test_names_not_in_file(self):
        rec = self.recorder().open()
        rec.add(self.clock.tick(1), self.PLAYERS)
        rec.add(self.clock.tick(1), {"kind": "read:select", "raw": ["月下独酌正在选择强化筹码"]})
        rec.flush()
        text = rec.path.read_text(encoding="utf-8")
        for name in ("星海旅人", "月下独酌", "电脑玩家"):
            self.assertNotIn(name, text)
        records, _ = rec.collect()
        self.assertNotIn("月下独酌", json.dumps(records, ensure_ascii=False))
        written = [r for r in lines(rec.path) if r["kind"] == "read:players"][0]
        self.assertEqual(written["rows"][1]["name"], mask_record(self.PLAYERS)["rows"][1]["name"])


class FailureTest(RecorderCase):
    def test_unwritable_folder(self):
        """文件夹不可写（指向一个文件）：不抛异常，只报一次错，导出仍有内存里的记录。"""
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        self.folder.write_text("不是文件夹", encoding="utf-8")
        rec = self.recorder().open()
        self.hud(rec, 1)
        rec.add(self.clock.tick(1), {"kind": "picked", "chip": "大背包"})
        self.assertFalse(rec.flush())
        rec.new_segment("newgame", cause="select")
        self.hud(rec, 1)
        rec.mark("game_over", why="结算页")
        rec.close()
        self.assertEqual(len(self.errors), 1)
        self.assertIn("不再写盘", self.errors[0][0])
        self.assertTrue(rec.error)
        records, meta = rec.collect()
        self.assertEqual([r["kind"] for r in records],
                         ["segment", "read:hud", "picked", "segment", "read:hud", "mark:game_over"])
        self.assertEqual(meta["write_error"], rec.error)

    def test_undo_in_memory_when_unwritable(self):
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        self.folder.write_text("x", encoding="utf-8")
        rec = self.recorder().open()
        self.hud(rec, 9)
        rec.new_segment("newgame", cause="round")
        self.hud(rec, 1)
        self.assertTrue(rec.undo_segment())
        records, _ = rec.collect()
        self.assertEqual([r["kind"] for r in records], ["segment", "read:hud", "segment_undo", "read:hud"])

    def test_half_line_is_skipped(self):
        """程序崩溃时写到一半的行：读回时跳过，下一次运行照样能导出上一段。"""
        rec = self.recorder().open()
        self.hud(rec, 4)
        rec.close()
        with rec.path.open("a", encoding="utf-8") as f:
            f.write('{"time": "2026-10-03T12:40:05.000", "kind": "read:hu')
        self.assertEqual([r.get("round") for r in read_segment(rec.path)], [None, 4])
        self.assertEqual(read_segment(self.folder / "没有这个文件.jsonl"), [])
        again = self.recorder().open()
        records, meta = again.collect()
        self.assertEqual([r.get("round") for r in records], [None, 4, None])
        self.assertEqual(meta["segments"][0]["lines"], 2)

    def test_constants(self):
        self.assertEqual(gamelog.KEEP, 10)
        self.assertEqual(gamelog.FLUSH_S, 60)
        self.assertTrue(gamelog.SEGMENT_NAME.match("20261003-133210-2.jsonl"))
        self.assertFalse(gamelog.SEGMENT_NAME.match("2026-10.jsonl"))
        self.assertEqual(paths.games_dir(), paths.logs_dir() / "games")


if __name__ == "__main__":
    unittest.main()
