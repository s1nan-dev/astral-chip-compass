"""处理器占用的采样（advisor/usage.py）：每分钟一条（attach 过时另存进文件）、导出诊断时现量各线程的占用和调用栈。

时钟、处理器读数、电源状况都换掉，不依赖真实的负载；只有 probe 的几个用例真量一次，起一个空转的 Python 线程。存盘的用例
写在测试自己的临时文件夹里。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from advisor import usage
from tests.support import sandbox


class FakeClock:
    """假的时钟：now() 取当前值，advance() 往前拨。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TickTest(unittest.TestCase):
    """tick：第一次只记基准；之后按这一段时长出一条；state 合进去；不到 1 秒不出；读不到、出错时不往外抛；只留最近 KEEP 条。"""

    def setUp(self) -> None:
        usage.reset()
        self.clock = FakeClock()
        self.snapshots = []
        for patcher in (
            mock.patch.object(usage.perfstats, "unbiased_clock", self.clock),
            mock.patch.object(usage.perfstats, "usage_snapshot", side_effect=lambda: self.snapshots.pop(0)),
            mock.patch.object(usage.perfstats, "usage_percent",
                              side_effect=lambda base, now, elapsed: {"process": 12.5, "ui": 3.0, "system": 20.0}),
            mock.patch.object(usage, "_power", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(usage.reset)

    def snapshot(self, process=0):
        return {"process": process, "ui": 0, "system": (0, process)}

    def test_first_tick_only_sets_base(self):
        self.snapshots = [self.snapshot()]
        usage.tick()
        self.assertEqual(usage.records(), [])

    def test_second_tick_records_fields_and_state(self):
        self.snapshots = [self.snapshot(), self.snapshot(1)]
        usage.tick()
        self.clock.advance(60.0)
        usage.tick({"manual": True, "paused": False})
        got = usage.records()
        self.assertEqual(len(got), 1)
        record = got[0]
        self.assertEqual(record["s"], 60.0)
        self.assertEqual((record["process"], record["ui"], record["system"]), (12.5, 3.0, 20.0))
        self.assertEqual((record["manual"], record["paused"]), (True, False))
        self.assertRegex(record["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_short_span_does_not_record_or_move_base(self):
        self.snapshots = [self.snapshot(), self.snapshot(), self.snapshot()]
        usage.tick()
        self.clock.advance(0.5)
        usage.tick()
        self.assertEqual(usage.records(), [])
        self.clock.advance(59.5)  # 从第一次的基准算起刚好 60 秒
        usage.tick()
        self.assertEqual([record["s"] for record in usage.records()], [60.0])

    def test_missing_readings_and_errors_do_not_raise(self):
        self.snapshots = [self.snapshot(), self.snapshot()]
        usage.tick()
        self.clock.advance(60.0)
        with mock.patch.object(usage.perfstats, "usage_snapshot", return_value=None):
            usage.tick()  # 读不到：不出记录，也不抛
        self.assertEqual(usage.records(), [])
        self.snapshots = []
        with mock.patch.object(usage.perfstats, "usage_snapshot", side_effect=RuntimeError("读不了")):
            usage.tick()
        self.assertEqual(usage.records(), [])

    def test_percent_error_is_swallowed(self):
        self.snapshots = [self.snapshot(), self.snapshot()]
        usage.tick()
        self.clock.advance(60.0)
        with mock.patch.object(usage.perfstats, "usage_percent", side_effect=ValueError("坏了")):
            usage.tick()
        self.assertEqual(usage.records(), [])

    def test_keeps_latest(self):
        self.snapshots = [self.snapshot()] + [self.snapshot() for _ in range(usage.KEEP + 5)]
        usage.tick()
        for index in range(usage.KEEP + 5):
            self.clock.advance(60.0)
            usage.tick({"n": index})
        got = usage.records()
        self.assertEqual(len(got), usage.KEEP)
        self.assertEqual(got[-1]["n"], usage.KEEP + 4)


class PowerTest(unittest.TestCase):
    """tick 里的电源状况：取到就原样放进 power，读不到、出错时不带这一项，这一条照记。"""

    def setUp(self) -> None:
        usage.reset()
        self.addCleanup(usage.reset)
        self.clock = FakeClock()
        for patcher in (
            mock.patch.object(usage.perfstats, "unbiased_clock", self.clock),
            mock.patch.object(usage.perfstats, "usage_snapshot",
                              return_value={"process": 0, "ui": 0, "system": (0, 1)}),
            mock.patch.object(usage.perfstats, "usage_percent",
                              return_value={"process": 1.0, "ui": 0.0, "system": 1.0}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tick_once(self) -> None:
        self.clock.advance(60)
        usage.tick()

    def test_power_is_recorded_as_is(self):
        power = {"ac": True, "battery_percent": 80, "saver": False, "mode": "best_performance", "plan": "balanced"}
        with mock.patch.object(usage, "_power", return_value=dict(power)):
            usage.tick()  # 基准
            self.tick_once()
        self.assertEqual(usage.records()[-1]["power"], power)

    def test_missing_or_failing_power_still_records(self):
        with mock.patch.object(usage, "_power", return_value=None):
            usage.tick()
            self.tick_once()
        self.assertNotIn("power", usage.records()[-1])
        with mock.patch.object(usage, "_power", side_effect=RuntimeError("读不了")):
            self.tick_once()
        self.assertEqual(len(usage.records()), 2)
        self.assertNotIn("power", usage.records()[-1])


class PersistTest(unittest.TestCase):
    """存盘：没 attach 不写；attach 读回 12 小时以内的、丢掉更早的和坏行并重写；攒 5 条才写、flush 立刻写；文件太长时
    重写成最近 KEEP 条；写盘出错以后不再写、不抛异常；reset 清掉存盘的状态。"""

    def setUp(self) -> None:
        usage.reset()
        self.addCleanup(usage.reset)
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory(dir=sandbox.work_dir())
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / usage.FILE_NAME
        for patcher in (
            mock.patch.object(usage.perfstats, "unbiased_clock", self.clock),
            mock.patch.object(usage.perfstats, "usage_snapshot",
                              return_value={"process": 0, "ui": 0, "system": (0, 1)}),
            mock.patch.object(usage.perfstats, "usage_percent",
                              return_value={"process": 1.0, "ui": 0.0, "system": 1.0}),
            mock.patch.object(usage, "_power", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def ticks(self, count: int = 1) -> None:
        for _ in range(count):
            self.clock.advance(60)
            usage.tick()

    def lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    def test_without_attach_writes_nothing(self):
        usage.tick()
        self.ticks(6)
        self.assertFalse(self.path.exists())

    def test_attach_keeps_recent_and_drops_old_and_broken(self):
        now = datetime.now()
        recent = {"at": now.isoformat(timespec="seconds"), "s": 60.0, "n": "recent"}
        old = {"at": (now - timedelta(seconds=usage.KEEP * usage.TICK_S + 60)).isoformat(timespec="seconds"),
               "s": 60.0, "n": "old"}
        rows = [json.dumps(old), "{坏的", json.dumps({"s": 1}), json.dumps(recent)]
        self.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        usage.attach(self.path)  # 读回、丢掉更早的和坏行、重写文件
        self.assertEqual(usage.records(), [recent])
        self.assertEqual(self.lines(), [json.dumps(recent)])

    def test_attach_does_not_create_missing_file(self):
        usage.attach(self.path)
        self.assertFalse(self.path.exists())

    def test_attach_skips_deeply_nested_line(self):
        """嵌套太深的一行（json.loads 报 RecursionError）照坏行跳过：attach 不往外抛（启动时调用，抛了程序就起不来），
        文件重写成只剩能用的。"""
        recent = {"at": datetime.now().isoformat(timespec="seconds"), "s": 60.0}
        self.path.write_text(json.dumps(recent) + "\n" + "[" * 100_000 + "\n", encoding="utf-8")
        usage.attach(self.path)
        self.assertEqual(usage.records(), [recent])
        self.assertEqual(self.lines(), [json.dumps(recent)])

    def test_attach_survives_any_read_error(self):
        """读的时候出 OSError 以外的错（例如文件大得读不进内存）：按没有记录处理，不往外抛，文件不动，之后照常追加。"""
        self.path.write_text('{"at": "2000-01-01T00:00:00"}\n', encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=MemoryError):
            usage.attach(self.path)
        self.assertEqual(usage.records(), [])
        self.assertEqual(len(self.lines()), 1)
        usage.tick()
        self.ticks(5)
        self.assertEqual(len(self.lines()), 6)

    def test_attach_rewrite_error_does_not_raise(self):
        """attach 重写文件写不进：不往外抛，读回的记录照样在内存里；本次运行不再写盘，只记一次错误记录。"""
        now = datetime.now()
        recent = {"at": now.isoformat(timespec="seconds"), "s": 60.0}
        old = {"at": (now - timedelta(days=2)).isoformat(timespec="seconds"), "s": 60.0}
        self.path.write_text(json.dumps(old) + "\n" + json.dumps(recent) + "\n", encoding="utf-8")
        for error in (OSError("写不进"), RecursionError("嵌套太深"), RuntimeError("别的错")):
            with self.subTest(error=type(error).__name__):
                usage.reset()
                with mock.patch.object(usage.fileio, "write_text", side_effect=error), \
                        mock.patch.object(usage.fileio, "append_lines") as append, \
                        mock.patch("advisor.errors.log_error") as log_error:
                    usage.attach(self.path)
                    usage.tick()
                    self.ticks(5)
                    usage.flush()
                self.assertEqual(usage.records()[0], recent)
                self.assertEqual(len(usage.records()), 6)
                append.assert_not_called()
                self.assertEqual(log_error.call_count, 1)

    def test_file_io_outside_lock(self):
        """读写文件都不在锁里做：导出诊断的后台线程取记录（records）不用等磁盘。"""
        held = []

        def check(original):
            def wrapper(*args, **kwargs):
                held.append(usage._lock.locked())
                return original(*args, **kwargs)
            return wrapper

        now = datetime.now()
        rows = [{"at": (now - timedelta(days=2)).isoformat(timespec="seconds")}]
        rows += [{"at": now.isoformat(timespec="seconds"), "n": index} for index in range(2 * usage.KEEP)]
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with mock.patch.object(Path, "read_text", check(Path.read_text)), \
                mock.patch.object(usage.fileio, "write_text", check(usage.fileio.write_text)), \
                mock.patch.object(usage.fileio, "append_lines", check(usage.fileio.append_lines)):
            usage.attach(self.path)  # 读、丢掉过期的一行重写
            usage.tick()
            self.ticks(5)  # 追加，文件超过 2 * KEEP 行又重写
            usage.flush()
        self.assertEqual(len(held), 4)  # 读一次、重写两次、追加一次
        self.assertNotIn(True, held)

    def test_writes_every_five_records_and_flush(self):
        usage.attach(self.path)
        usage.tick()  # 基准
        self.ticks(4)
        self.assertFalse(self.path.exists())
        self.ticks(1)  # 第 5 条
        self.assertEqual(len(self.lines()), 5)
        self.ticks(1)
        self.assertEqual(len(self.lines()), 5)
        usage.flush()
        self.assertEqual(len(self.lines()), 6)
        usage.flush()
        self.assertEqual(len(self.lines()), 6)

    def test_rewrites_when_file_gets_too_long(self):
        now = datetime.now().isoformat(timespec="seconds")
        rows = [{"at": now, "n": index} for index in range(2 * usage.KEEP)]
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        usage.attach(self.path)
        self.assertEqual(len(self.lines()), 2 * usage.KEEP)
        usage.tick()
        self.ticks(5)  # 文件超过 2 * KEEP 行：重写成最近 KEEP 条
        got = self.lines()
        self.assertEqual(len(got), usage.KEEP)
        self.assertEqual(json.loads(got[0])["n"], usage.KEEP + 5)  # 最近 KEEP 条里最旧的一条
        self.assertEqual(json.loads(got[-1])["s"], 60.0)

    def test_write_error_stops_writing_and_does_not_raise(self):
        usage.attach(self.path)
        with mock.patch.object(usage.fileio, "append_lines", side_effect=OSError("写不进")) as append, \
                mock.patch("advisor.errors.log_error") as log_error:
            usage.tick()
            self.ticks(10)  # 两次攒满 5 条，只在第一次报错
            usage.flush()
            self.assertEqual(len(usage.records()), 10)  # 内存里照记
        self.assertEqual(append.call_count, 1)
        self.assertEqual(log_error.call_count, 1)
        self.assertFalse(self.path.exists())

    def test_reset_forgets_attach(self):
        usage.attach(self.path)
        usage.tick()
        self.ticks(5)
        size = self.path.stat().st_size
        usage.reset()
        self.assertEqual(usage.records(), [])
        usage.tick()
        self.ticks(6)
        self.assertEqual(self.path.stat().st_size, size)  # 不再写盘
        self.path.unlink()
        usage.tick()
        self.ticks(6)
        self.assertFalse(self.path.exists())


def _busy_loop_for_probe_test(stop: threading.Event) -> None:
    """空转的线程（probe 的用例观察它是不是忙、调用栈里有没有这个函数名）。用 time.sleep(0) 走一圈：一直占着处理器，
    同时让出 GIL，主线程才跑得动。"""
    while not stop.is_set():
        time.sleep(0)


class ProbeTest(unittest.TestCase):
    """probe：真量一次，忙的线程在结果里、名字对、调用栈里有那个函数；文件部分不带路径；调用线程没有调用栈；拿不到
    sys._current_frames 时占用照常。"""

    def test_busy_thread_is_listed_with_stacks(self):
        stop = threading.Event()
        thread = threading.Thread(target=_busy_loop_for_probe_test, args=(stop,), name="usage-probe-test",
                                  daemon=True)
        thread.start()
        try:
            result = usage.probe(0.4)
        finally:
            stop.set()
            thread.join(2.0)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["thread_count"], 1)
        self.assertGreaterEqual(result["stack_threads"], 1)
        busy = next((item for item in result["threads"] if item.get("name") == "usage-probe-test"), None)
        self.assertIsNotNone(busy, result["threads"])
        self.assertGreaterEqual(busy["core"], usage.BUSY_CORE)
        frames = [frame for stack in busy["stacks"] for frame in stack["frames"]]
        self.assertTrue(any("_busy_loop_for_probe_test" in frame for frame in frames), frames)

    def test_frames_have_file_name_only(self):
        stop = threading.Event()
        thread = threading.Thread(target=_busy_loop_for_probe_test, args=(stop,), name="usage-probe-test",
                                  daemon=True)
        thread.start()
        try:
            result = usage.probe(0.3)
        finally:
            stop.set()
            thread.join(2.0)
        busy = next(item for item in result["threads"] if item.get("name") == "usage-probe-test")
        for stack in busy["stacks"]:
            for frame in stack["frames"]:
                path = frame.split(" ", 1)[0].rsplit(":", 1)[0]
                self.assertNotIn("/", path)
                self.assertNotIn("\\", path)
                self.assertNotIn(":", path)
                self.assertFalse(os.path.isabs(path))

    def test_no_module_or_start_keys(self):
        result = usage.probe(0.2)
        self.assertEqual(set(result) - {"seconds", "process", "system", "thread_count", "stack_threads",
                                        "threads", "stacks_error"}, set())
        for item in result["threads"]:
            self.assertLessEqual(set(item), {"tid", "core", "name", "stacks"})
            self.assertNotIn("module", item)
            self.assertNotIn("start", item)

    def test_caller_has_no_stacks(self):
        result = usage.probe(0.2)
        own = next((item for item in result["threads"] if item["tid"] == threading.get_ident()), None)
        if own is not None:
            self.assertNotIn("stacks", own)

    def test_current_frames_error_keeps_usage(self):
        with mock.patch.object(usage.sys, "_current_frames", side_effect=RuntimeError("拿不到")):
            result = usage.probe(0.2)
        self.assertIsNotNone(result)
        self.assertIn("stacks_error", result)
        self.assertIn("RuntimeError", result["stacks_error"])
        self.assertEqual(result["stack_threads"], 0)
        self.assertIsNotNone(result["process"])

    def test_not_windows(self):
        with mock.patch.object(usage, "_kernel32", None):
            self.assertIsNone(usage.probe(0.2))


class SecondsDefaultTest(unittest.TestCase):
    def test_probe_reads_probe_s_when_called(self):
        seen = []

        def fake_probe(span, sleep):
            seen.append(span)
            return {"thread_count": 1}

        with mock.patch.object(usage, "_probe", side_effect=fake_probe):
            usage.probe()
            with mock.patch.object(usage, "PROBE_S", 0.7):
                usage.probe()
        self.assertEqual(seen, [usage.PROBE_S, 0.7])


if __name__ == "__main__":
    unittest.main()
