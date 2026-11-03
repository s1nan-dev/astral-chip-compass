"""推荐日志开局时倒着读最后一段（advisor/logbook.py 的 _scan_log）：结果和整份按顺序读逐位相同。

参照实现（_reference_records、_reference_scan_log）是整份按顺序读的写法（逐个月份文件读完再逐行解析），和倒着读的逐个
比。固定种子随机造很多日志文件夹：几个月份文件、各种记录、坏行、写到一半的末行、一串零字节、断在一个字中间的字节、空文件、
只有坏行的文件、开头带 BOM（utf-8 不是 utf-8-sig）、\\r\\n 换行、记录里含 U+2028/U+2029/U+0085、一行比读块还长、last 和 latest
落在不同的文件、目录冒充文件（读不了）。块大小临时改小（7、64 字节）再比一遍，专测跨块拼行。月份界限用假的「现在」测。

另有几处把倒着读故意改坏（按块解码、latest 只认最后一条记录、读不了的文件不作废）的样例，核对这样的改动会被上面的比对抓到。
日志文件夹建在测试沙箱里，时钟用假的。
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from advisor import logbook, paths
from tests.support import sandbox

# 逐位比对时用的「现在」和月份文件的名字（覆盖月初、跨年）
MONTHS = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12", "2027-01"]
LAST_KINDS = ("offer", "newgame", "gameover")


# ------------------------------------------------------------ 参照实现（整份按顺序读的 iter_records / _scan_log）


def _reference_records(folder: Path, since: datetime):
    """整份按顺序读的 iter_records（只把 paths.logs_dir() 换成 folder）。"""
    if not folder.is_dir():
        return
    first = f"{since:%Y-%m}" if since else ""
    for f in sorted(folder.glob("????-??.jsonl")):
        if f.stem < first:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line.strip("\x00"))
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def _reference_scan_log(folder: Path, since: datetime) -> tuple:
    """整份按顺序读的 _scan_log。"""
    last = latest = None
    for record in _reference_records(folder, since):
        latest = record.get("time", latest)
        if record.get("t") in LAST_KINDS:
            last = record
    return last, latest


def _run_new(folder: Path, now: datetime, chunk: int | None = None) -> tuple:
    """跑程序里的 _scan_log：logs_dir、_now 换成给定的，块大小给定时临时改小。"""
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(paths, "logs_dir", return_value=folder))
        stack.enter_context(mock.patch.object(logbook, "_now", side_effect=lambda: now))
        if chunk is not None:
            stack.enter_context(mock.patch.object(logbook, "_TAIL_CHUNK", chunk))
        return logbook._scan_log()


# ------------------------------------------------------------ 随机造日志文件夹


def _some_record(rng: random.Random) -> dict:
    kind = rng.choice(["offer", "pick", "feedback", "newgame", "gameover", "calibrated", "other"])
    record: dict = {"t": kind}
    r = rng.random()
    if r < 0.5:  # 带 time 键，值是正常的时刻
        record["time"] = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:00:00"
    elif r < 0.6:  # 带 time 键但值是 null：最后的 latest 可能因此变成 None
        record["time"] = None
    # 其余没有 time 键
    if kind in ("offer", "gameover"):
        record["game"] = f"g{rng.randint(0, 5)}"
    if kind == "offer":
        record["offer_id"] = f"o{rng.randint(0, 9)}"
        record["state"] = {"hero": rng.choice(["凛", "芬妮", "阿尔"]), "round": rng.randint(1, 12)}
    if rng.random() < 0.15:  # U+2028/U+2029/U+0085：只有按「\n」切行才不被切开
        record["note"] = rng.choice(["a\u2028b", "c\u2029d", "e" + chr(0x85) + "f"])
    return record


def _unit(rng: random.Random) -> bytes:
    """一行（字节）：正常记录、坏行、不是字典的 JSON、空行、零字节、断在多字节字中间、超长的一行等。"""
    r = rng.random()
    if r < 0.6:
        return json.dumps(_some_record(rng), ensure_ascii=False).encode("utf-8")
    if r < 0.67:
        return b"{broken"
    if r < 0.73:
        return b"[1, 2, 3]"  # 解析得出来、但不是字典：跳过
    if r < 0.79:
        return b'"just a string"'
    if r < 0.85:
        return b"\x00" * rng.randint(1, 20)
    if r < 0.89:
        return b""  # 空行
    if r < 0.95:
        raw = ("筹码" * rng.randint(1, 3)).encode("utf-8")
        return raw[: max(1, len(raw) - rng.randint(1, 2))]  # 断在一个字的中间
    record = _some_record(rng)
    record["big"] = "长" * rng.randint(200, 700)  # 一行比读块（含改小后的 7、64 字节）长得多
    return json.dumps(record, ensure_ascii=False).encode("utf-8")


# 换行：程序写的是 \r\n；read_text 用 newline=None，单独的 \r 也当换行，一并造出来比
_ENDINGS = [b"\n", b"\n", b"\r\n", b"\r\n", b"\r"]


def _file_body(rng: random.Random) -> bytes:
    out = []
    if rng.random() < 0.12:
        out.append(b"\xef\xbb\xbf")  # 开头带 BOM：第一行解析失败、被跳过
    n = rng.randint(0, 10)
    for i in range(n):
        out.append(_unit(rng))
        if i < n - 1 and rng.random() < 0.92:
            out.append(rng.choice(_ENDINGS))
    if n and rng.random() < 0.5:  # 末尾换行与否随机（末尾是写到一半的半行时没有换行）
        out.append(rng.choice(_ENDINGS + [b"\r"]))
    return b"".join(out)


def _random_now(rng: random.Random) -> datetime:
    return datetime(rng.choice([2026, 2027]), rng.randint(1, 12), rng.randint(1, 28), 12, 0, 0)


def _make_cases(base: Path, seed: int, count: int) -> list[tuple[Path, datetime]]:
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        folder = base / f"c{i:04d}"
        folder.mkdir()
        for month in rng.sample(MONTHS, rng.randint(1, 4)):
            path = folder / f"{month}.jsonl"
            if rng.random() < 0.12:
                path.mkdir()  # 目录冒充文件：读不了，整份跳过
            else:
                path.write_bytes(_file_body(rng))
        cases.append((folder, _random_now(rng)))
    return cases


# ------------------------------------------------------------ 逐位比对


class TailEquivalenceTest(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self, "logbook-tail-")

    def test_random_folders_match_whole_read(self):
        """固定种子造的 160 个日志文件夹，块大小用默认、7、64 三种，倒着读和整份读逐位相同。"""
        cases = _make_cases(self.base, seed=20261011, count=160)
        for folder, now in cases:
            want = _reference_scan_log(folder, now - timedelta(days=31))
            for chunk in (None, 7, 64):
                with self.subTest(folder=folder.name, now=now.isoformat(), chunk=chunk):
                    self.assertEqual(_run_new(folder, now, chunk), want)

    def test_crlf_and_no_trailing_newline(self):
        """\\r\\n 换行、末尾没有换行（写到一半的半行/完整的末行）、开头带 BOM。"""
        folder = self.base / "crlf"
        folder.mkdir()
        offer = json.dumps({"t": "offer", "time": "2026-10-05T10:00:00", "game": "g1"}, ensure_ascii=False)
        pick = json.dumps({"t": "pick", "time": "2026-10-05T10:05:00", "offer_id": "o1"}, ensure_ascii=False)
        (folder / "2026-10.jsonl").write_bytes(
            b"\xef\xbb\xbf" + offer.encode("utf-8") + b"\r\n" + b"{torn\r\n" + pick.encode("utf-8"))
        now = datetime(2026, 10, 10, 12, 0, 0)
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), _reference_scan_log(folder, now - timedelta(days=31)))

    def test_lone_cr_and_mixed_newlines(self):
        """单独的 \\r 也是换行（read_text 的 newline=None）；\\r\\r\\n 是一个空行加一个换行。"""
        folder = self.base / "cr"
        folder.mkdir()
        a = json.dumps({"t": "offer", "time": "2026-10-01T10:00:00", "game": "g1"}, ensure_ascii=False).encode()
        b = json.dumps({"t": "pick", "time": "2026-10-01T10:05:00", "offer_id": "o1"}, ensure_ascii=False).encode()
        c = json.dumps({"t": "gameover", "time": "2026-10-01T11:00:00", "game": "g1"}, ensure_ascii=False).encode()
        now = datetime(2026, 10, 10, 12, 0, 0)
        for body in (a + b"\r" + b, a + b"\r\r\n" + b, a + b"\r\n" + b + b"\r", a + b"\r" + b + b"\n" + c):
            (folder / "2026-10.jsonl").write_bytes(body)
            want = _reference_scan_log(folder, now - timedelta(days=31))
            for chunk in (None, 7, 64):
                with self.subTest(body=body, chunk=chunk):
                    self.assertEqual(_run_new(folder, now, chunk), want)

    def test_zero_bytes_and_only_bad_lines_and_empty(self):
        folder = self.base / "damage"
        folder.mkdir()
        (folder / "2026-09.jsonl").write_bytes(b"")
        (folder / "2026-10.jsonl").write_bytes(b"\x00" * 40)
        (folder / "2026-11.jsonl").write_bytes(b"{bad\n{bad2\n" + b"\x00" * 8)
        now = datetime(2026, 10, 10, 12, 0, 0)
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), _reference_scan_log(folder, now - timedelta(days=31)))

    def test_line_separators_inside_a_record_do_not_split_it(self):
        """记录里含 U+2028/U+2029/U+0085：只有按「\\n」（字节 0x0A）切行才不被切成两半。"""
        folder = self.base / "seps"
        folder.mkdir()
        row = json.dumps({"t": "offer", "time": "2026-10-05T10:00:00", "game": "g1",
                          "note": "前\u2028中\u2029后" + chr(0x85) + "尾"}, ensure_ascii=False)
        (folder / "2026-10.jsonl").write_bytes(row.encode("utf-8") + b"\n")
        now = datetime(2026, 10, 10, 12, 0, 0)
        want = _reference_scan_log(folder, now - timedelta(days=31))
        self.assertEqual(want[0]["note"], "前\u2028中\u2029后" + chr(0x85) + "尾")
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), want)

    def test_unreadable_file_is_skipped_whole(self):
        """目录冒充月份文件：读不了，整份跳过（同 iter_records），照常读更早的文件。"""
        folder = self.base / "unreadable"
        folder.mkdir()
        (folder / "2026-10.jsonl").mkdir()
        offer = {"t": "offer", "time": "2026-09-20T10:00:00", "game": "gold"}
        (folder / "2026-09.jsonl").write_bytes(
            json.dumps(offer, ensure_ascii=False).encode("utf-8") + b"\n")
        now = datetime(2026, 10, 10, 12, 0, 0)
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), (offer, offer["time"]))

    def test_last_and_latest_in_different_files(self):
        """last（三选一那类）和 latest（最后一条带 time 的）落在不同的文件里：本月最后几条没有 time 键。"""
        folder = self.base / "apart"
        folder.mkdir()
        # 更新的文件：只有一条不带 time 的 newgame（定 last），带 time 的在更早的文件里
        (folder / "2026-10.jsonl").write_bytes(
            json.dumps({"t": "newgame", "game": "gnew"}, ensure_ascii=False).encode("utf-8") + b"\n")
        older = {"t": "offer", "time": "2026-09-25T08:00:00", "game": "gold"}
        (folder / "2026-09.jsonl").write_bytes(
            json.dumps(older, ensure_ascii=False).encode("utf-8") + b"\n")
        now = datetime(2026, 10, 10, 12, 0, 0)
        want = _reference_scan_log(folder, now - timedelta(days=31))
        self.assertEqual(want[0]["t"], "newgame")
        self.assertEqual(want[1], older["time"])
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), want)

    def test_missing_logs_dir(self):
        """日志文件夹不在：返回（None, None）。"""
        missing = self.base / "no-such-folder"
        with mock.patch.object(paths, "logs_dir", return_value=missing), \
                mock.patch.object(logbook, "_now", side_effect=lambda: datetime(2026, 10, 10)):
            self.assertEqual(logbook._scan_log(), (None, None))


class TailMonthBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self, "logbook-tail-")

    def _folder(self, months=MONTHS) -> Path:
        folder = self.base / f"m{len(list(self.base.iterdir()))}"
        folder.mkdir()
        for month in months:
            (folder / f"{month}.jsonl").write_bytes(b'{"t": "offer", "time": "x", "game": "g"}\n')
        return folder

    def test_month_cutoff_by_whole_month(self):
        """月初：近 31 天的界限跨到上个月时，本月及上个月都在内，更早的不读。"""
        folder = self._folder()
        now = datetime(2026, 11, 1, 0, 30, 0)  # -31 天 → 10-01，first = 2026-10
        want = _reference_scan_log(folder, now - timedelta(days=31))
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), want)
        # 界限前一个月的记录不被读：最后一条带 time 的来自 2026-10，不是 2026-09
        self.assertEqual(_run_new(folder, now, 7)[1], "x")
        self.assertEqual([f.stem for f in sorted(folder.glob("????-??.jsonl")) if f.stem >= "2026-10"],
                         ["2026-10", "2026-11", "2026-12", "2027-01"])

    def test_year_crossing(self):
        """跨年：2027-01 的近 31 天界限落在 2026-12。"""
        folder = self._folder()
        now = datetime(2027, 1, 5, 9, 0, 0)  # -31 天 → 2026-12-05，first = 2026-12
        want = _reference_scan_log(folder, now - timedelta(days=31))
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), want)

    def test_march_cutoff(self):
        """界限落在 3 月：2 月的记录也算在内。"""
        folder = self._folder(["2026-01", "2026-02", "2026-03", "2026-04"])
        now = datetime(2026, 3, 5, 9, 0, 0)  # -31 天 → 2026-02-02，first = 2026-02
        want = _reference_scan_log(folder, now - timedelta(days=31))
        for chunk in (None, 7, 64):
            with self.subTest(chunk=chunk):
                self.assertEqual(_run_new(folder, now, chunk), want)


def _broken_keep_partial(folder: Path, now: datetime) -> tuple:
    """出错不作废（错误做法）：读某个文件中途出 OSError 时，已经倒着读出来的结果照样算。"""
    last = latest = None
    have_last = have_latest = False
    first = f"{now - timedelta(days=31):%Y-%m}"
    for f in reversed(sorted(folder.glob("????-??.jsonl"))):
        if f.stem < first:
            continue
        try:
            with open(f, "rb") as handle:
                for line in logbook._tail_lines(handle):
                    record = logbook._parse_line(line)
                    if record is None:
                        continue
                    if not have_latest and "time" in record:
                        latest, have_latest = record["time"], True
                    if not have_last and record.get("t") in LAST_KINDS:
                        last, have_last = record, True
                    if have_last and have_latest:
                        return last, latest
        except OSError:
            pass  # 不作废：上面已经直接写进 last / latest 了
    return last, latest


class TailUnreadableMidFileTest(unittest.TestCase):
    """读某个文件中途出 OSError：这个文件里已经倒着读出来的结果也作废，接着读更早的文件。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "logbook-tail-")
        self.folder = self.base / "mid"
        self.folder.mkdir()
        self.offer = {"t": "offer", "time": "2026-09-20T10:00:00", "game": "gold"}
        (self.folder / "2026-09.jsonl").write_bytes(
            json.dumps(self.offer, ensure_ascii=False).encode("utf-8") + b"\n")
        (self.folder / "2026-10.jsonl").write_bytes(b'{"t": "pick", "time": "2026-10-01T00:00:00", "offer_id": "x"}\n')
        self.now = datetime(2026, 10, 10, 12, 0, 0)
        real = logbook._tail_lines

        def failing(f):
            if f.name.endswith("2026-10.jsonl"):
                # 先倒着交出一条带 time 的记录（latest 差一点就定下来），再报错：这个文件要整份作废
                yield b'{"t": "pick", "time": "2026-10-01T00:00:00", "offer_id": "x"}'
                raise OSError("中途读不了")
            yield from real(f)

        self._patch = mock.patch.object(logbook, "_tail_lines", side_effect=failing)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_partial_results_from_a_failing_file_are_dropped(self):
        with mock.patch.object(paths, "logs_dir", return_value=self.folder), \
                mock.patch.object(logbook, "_now", side_effect=lambda: self.now):
            got = logbook._scan_log()
        # 2026-10 作废：last、latest 都来自 2026-09；latest 不是 2026-10 那条的 time
        self.assertEqual(got, (self.offer, self.offer["time"]))
        self.assertNotEqual(got[1], "2026-10-01T00:00:00")

    def test_keep_partial_would_differ(self):
        """作废这一步去掉（出错不作废）时，latest 会留下 2026-10 那条的 time：和现在的做法不同，比对能抓到。"""
        self.assertNotEqual(_broken_keep_partial(self.folder, self.now), (self.offer, self.offer["time"]))


# ------------------------------------------------------------ 把倒着读故意改坏，核对上面的比对抓得到


def _broken_chunk_decode(folder: Path, now: datetime, chunk: int) -> tuple:
    """按块解码（错误做法）：每一块单独解码再切行，行跨块、多字节字跨块时结果就变了。"""
    last = latest = None
    have_last = have_latest = False
    first = f"{now - timedelta(days=31):%Y-%m}"
    for f in reversed(sorted(folder.glob("????-??.jsonl"))):
        if f.stem < first:
            continue
        try:
            with open(f, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                pos = handle.tell()
                while pos > 0 and not (have_last and have_latest):
                    step = min(chunk, pos)
                    pos -= step
                    handle.seek(pos)
                    for text in reversed(handle.read(step).decode("utf-8", "replace").split("\n")):
                        try:
                            record = json.loads(text.strip("\x00"))
                        except ValueError:
                            continue
                        if not isinstance(record, dict):
                            continue
                        if not have_latest and "time" in record:
                            latest, have_latest = record["time"], True
                        if not have_last and record.get("t") in LAST_KINDS:
                            last, have_last = record, True
        except OSError:
            continue
        if have_last and have_latest:
            break
    return last, latest


def _broken_latest_only_last(folder: Path, since: datetime) -> tuple:
    """latest 只认最后一条记录（错误做法）：最后一条没有 time 键时 latest 就成了 None。"""
    records = list(_reference_records(folder, since))
    last = None
    for record in records:
        if record.get("t") in LAST_KINDS:
            last = record
    latest = records[-1].get("time") if records else None
    return last, latest


class TailSensitivityTest(unittest.TestCase):
    """上面的随机比对能抓到这些改坏：对着同一批随机文件夹，改坏的版本和参照实现有差异。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "logbook-tail-")
        self.cases = _make_cases(self.base, seed=20261011, count=160)

    def test_corpus_distinguishes_broken_variants(self):
        decodes = lates = 0
        for folder, now in self.cases:
            since = now - timedelta(days=31)
            want = _reference_scan_log(folder, since)
            if _broken_chunk_decode(folder, now, 7) != want:
                decodes += 1
            if _broken_latest_only_last(folder, since) != want:
                lates += 1
        self.assertGreater(decodes, 0, "按块解码的改坏没有被抓到")
        self.assertGreater(lates, 0, "latest 只认最后一条记录的改坏没有被抓到")

    def test_new_matches_reference_on_the_same_corpus(self):
        """同一批文件夹上，倒着读和参照实现全部相同（和 test_random_folders_match_whole_read 是同一批数据）。"""
        for folder, now in self.cases:
            since = now - timedelta(days=31)
            want = _reference_scan_log(folder, since)
            for chunk in (None, 7, 64):
                with self.subTest(folder=folder.name, chunk=chunk):
                    self.assertEqual(_run_new(folder, now, chunk), want)


if __name__ == "__main__":
    unittest.main()
