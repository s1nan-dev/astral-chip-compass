"""统一的写盘（advisor/fileio.py）的单元测试：整份写入在每一步出错、被结束时，目标文件和 .bak 至少有一份完整、临时文件
删掉；先 flush、刷盘再替换，刷盘报错照常换上、只记一次警告；原来的内容不能用（backup_check）时不进 .bak；临时文件的
后缀不能是空的或 .bak；读坏了另存（不覆盖已有的）、改读备份并恢复，恢复时先给坏文件留一份、文件名一直在；追加时补
换行、编码出错不动文件；被占用时重试、重试用完报错、只读或文件夹写不进时马上报；文件夹先写到 .partial 再改名；只用
标准库。

文件都写在沙箱的临时文件夹里。出错、被占用、程序被结束都用 mock 造：换掉模块里的 open 和 os.fsync、os.replace、
os.rename、os.link、os.unlink（Disk）。
"""
from __future__ import annotations

import ast
import errno
import json
import os
import re
import stat
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from advisor import fileio
from tests.support import sandbox

NOW = datetime(2026, 10, 10, 15, 30, 0)
STAMP = "20261010-153000"
PREV = b'{"hero": "\xe5\x87\x9b", "v": 0}'  # .bak 里原有的（上一代）
OLD = b'{"hero": "\xe8\x8a\xac\xe5\xa6\xae", "v": 1}'  # 文件里现有的
NEW = b'{"hero": "\xe5\x8d\x97\xe5\xb8\x8c\xe9\x9c\xb2", "v": 2}'  # 要写的
NEWER = b'{"v": 3}'


class Fault(OSError):
    """测试造的写盘错误（磁盘满、移动硬盘被拔掉这类）。"""


class Crash(BaseException):
    """测试造的「程序在这一步被结束」：不是 Exception，写盘代码里的 except OSError 接不住它。"""


def fault() -> Fault:
    return Fault(errno.EIO, "模拟的写盘错误")


def busy() -> PermissionError:
    """测试造的「别的程序正开着这个文件」。"""
    return PermissionError(errno.EACCES, "另一个程序正在使用此文件")


def denied() -> PermissionError:
    """测试造的「文件夹写不进」（没有权限、被受控文件夹访问拦下）：和被占用报的是同一种错。"""
    return PermissionError(errno.EACCES, "拒绝访问")


class Disk:
    """换掉写盘用到的底层操作，按顺序记下每一步，并在指定的那一次出错。with Disk() as disk 期间生效。

    步骤：open 打开文件写、write 写内容、fsync 刷盘、replace 替换、rename 改名、link 建硬链接；log 里每项是（步骤,
    文件名），文件名是写的、换的、改名的、链的那个源文件。
    write 这一步出错时先写进一半再报（留下写到一半的文件）。unsafe 记下替换、改名时还没刷盘的源文件：先刷盘再替换的话
    它一直是空的。fsync 只刷得到已经 flush 给系统的内容：还留在文件对象缓冲里的（没先 flush 就 fsync），照样算没刷盘。
    keep_temps 为真时 os.unlink 什么都不做：程序被结束时收尾的代码来不及跑，临时文件会留下。"""

    def __init__(self):
        self.log: list[tuple[str, str]] = []
        self.unsafe: list[str] = []
        self.keep_temps = False
        self._plan: dict[tuple[str, int], object] = {}
        self._counts: dict[str, int] = {}
        self._dirty: set[str] = set()  # 写过以后还没刷盘的文件名
        self._ends: dict[str, int] = {}  # 文件名 → 写到的位置（含还在缓冲里的）
        self._names: dict[int, str] = {}  # 打开着写的文件：句柄 → 文件名
        self._real = {"open": open, "fsync": os.fsync, "replace": os.replace, "rename": os.rename, "unlink": os.unlink,
                      "link": os.link}
        self._patches = [mock.patch("advisor.fileio.open", create=True, new=self._open),
                         mock.patch.object(os, "fsync", self._fsync), mock.patch.object(os, "replace", self._replace),
                         mock.patch.object(os, "rename", self._rename), mock.patch.object(os, "unlink", self._unlink),
                         mock.patch.object(os, "link", self._link)]

    def __enter__(self) -> Disk:
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> bool:
        for p in reversed(self._patches):
            p.stop()
        return False

    def fail(self, step: str, *nths: int, error=fault) -> Disk:
        """第 nths 次（从 1 数）做 step 时报 error() 造出的错。"""
        for n in nths:
            self._plan[(step, n)] = error
        return self

    def count(self, step: str) -> int:
        return sum(1 for s, _ in self.log if s == step)

    def unsynced(self) -> set[str]:
        """写过、还没刷进硬盘的文件名。"""
        return set(self._dirty)

    def step(self, step: str, name: str) -> BaseException | None:
        """记下这一步；这一次要出错时返回造出的错（由调用的地方报）。"""
        n = self._counts[step] = self._counts.get(step, 0) + 1
        self.log.append((step, name))
        make = self._plan.get((step, n))
        return make() if make else None

    def wrote(self, name: str, end: int) -> None:
        """name 写进了内容（写到 end 为止，可能还在文件对象的缓冲里）、还没刷盘。"""
        self._dirty.add(name)
        self._ends[name] = end

    def _open(self, file, mode="r", *args, **kwargs):
        name = Path(file).name
        error = self.step("open", name)
        if error is not None:
            raise error
        f = self._real["open"](file, mode, *args, **kwargs)
        self._names[f.fileno()] = name
        return _Tracked(f, self, name)

    def _fsync(self, fd: int) -> None:
        name = self._names.get(fd, "?")
        error = self.step("fsync", name)
        if error is not None:
            raise error
        self._real["fsync"](fd)
        if os.fstat(fd).st_size >= self._ends.get(name, 0):  # 写的内容都已经交给系统了，这次刷盘才刷得到
            self._dirty.discard(name)

    def _replace(self, src, dst) -> None:
        self._move("replace", src, dst)

    def _rename(self, src, dst) -> None:
        self._move("rename", src, dst)

    def _move(self, step: str, src, dst) -> None:
        s, d = Path(src).name, Path(dst).name
        error = self.step(step, s)
        if error is not None:
            raise error
        self._real[step](src, dst)
        if s in self._dirty:
            self.unsafe.append(s)
            self._dirty.add(d)
        else:
            self._dirty.discard(d)
        self._dirty.discard(s)

    def _link(self, src, dst, *args, **kwargs) -> None:
        error = self.step("link", Path(src).name)
        if error is not None:
            raise error
        self._real["link"](src, dst, *args, **kwargs)

    def _unlink(self, path, *args, **kwargs) -> None:
        if not self.keep_temps:
            self._real["unlink"](path, *args, **kwargs)


class _Tracked:
    """Disk 打开的文件：记下 write 这一步，其余照原样交给真的文件对象。"""

    def __init__(self, f, disk: Disk, name: str):
        self._f, self._disk, self._name = f, disk, name

    def write(self, data):
        error = self._disk.step("write", self._name)
        if error is not None:
            self._f.write(data[: len(data) // 2])
            self._f.flush()
            self._disk.wrote(self._name, self._f.tell())
            raise error
        written = self._f.write(data)
        self._disk.wrote(self._name, self._f.tell())
        return written

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()
        return False

    def __getattr__(self, name):
        return getattr(self._f, name)


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def parse_json(data: bytes) -> dict:
    """测试用的解析函数：UTF-8 的 JSON 对象，别的一律报 ValueError（算读坏）。"""
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("顶层应为对象")
    return value


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "fileio-")
        self.path = self.dir / "state.json"
        self.bak = self.dir / "state.json.bak"

    def sleeps(self):
        """换掉重试前的等待（time.sleep），记下每次等多久，不真的等。"""
        patcher = mock.patch.object(fileio.time, "sleep")
        sleep = patcher.start()
        self.addCleanup(patcher.stop)
        return sleep

    def no_delays(self):
        """重试不等（RETRY_DELAYS 换成同样次数的 0）。"""
        patcher = mock.patch.object(fileio, "RETRY_DELAYS", (0.0,) * len(fileio.RETRY_DELAYS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def read_only(self, path: Path) -> None:
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(lambda: path.exists() and os.chmod(path, stat.S_IREAD | stat.S_IWRITE))


# ---------------------------------------------------------------- 整份写入


class WriteTest(_Case):
    def test_write_creates_folder_flushes_then_replaces(self):
        """文件夹不在就建；写临时文件、刷盘、再替换，不留临时文件。"""
        path = self.dir / "sub" / "deeper" / "a.json"
        with Disk() as disk:
            self.assertTrue(fileio.write_bytes(path, NEW))
        self.assertEqual(path.read_bytes(), NEW)
        self.assertEqual(names(path.parent), ["a.json"])
        self.assertEqual(disk.log, [("open", "a.json.tmp"), ("write", "a.json.tmp"), ("fsync", "a.json.tmp"),
                                    ("replace", "a.json.tmp")])
        self.assertEqual(disk.unsafe, [])

    def test_overwrite(self):
        self.path.write_bytes(OLD)
        self.assertTrue(fileio.write_bytes(self.path, NEW))
        self.assertEqual(self.path.read_bytes(), NEW)
        self.assertEqual(names(self.dir), ["state.json"])  # 不要备份时不留 .bak

    def test_not_durable_skips_fsync(self):
        """durable=False 不刷盘：替换时内容可能还没写进硬盘（Disk.unsafe 记下了），所以设置、方案都要刷。"""
        with Disk() as disk:
            fileio.write_bytes(self.path, NEW, durable=False)
        self.assertEqual(disk.count("fsync"), 0)
        self.assertEqual(disk.unsafe, ["state.json.tmp"])
        self.assertEqual(self.path.read_bytes(), NEW)

    def test_if_changed(self):
        """if_changed：文件里已经是这些字节时不写（不碰文件），返回 False；不一样时照常写。"""
        self.path.write_bytes(OLD)
        before = self.path.stat().st_mtime_ns
        with Disk() as disk:
            self.assertFalse(fileio.write_bytes(self.path, OLD, if_changed=True))
        self.assertEqual(disk.log, [])
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertTrue(fileio.write_bytes(self.path, NEW, if_changed=True))
        self.assertEqual(self.path.read_bytes(), NEW)
        self.assertTrue(fileio.write_bytes(self.dir / "new.json", b"", if_changed=True))  # 没有文件时照常建
        self.assertEqual((self.dir / "new.json").read_bytes(), b"")

    def test_suffix(self):
        """suffix 换临时文件的后缀（下载的图片用 .part），备份的临时文件跟着换。"""
        path = self.dir / "a.png"
        path.write_bytes(OLD)
        with Disk() as disk:
            fileio.write_bytes(path, NEW, backup=True, suffix=".part")
        self.assertEqual({name for _, name in disk.log}, {"a.png.part", "a.png.bak.part"})
        self.assertEqual(names(self.dir), ["a.png", "a.png.bak"])

    def test_backup_keeps_previous_version(self):
        """backup：第一次（还没有文件）不留 .bak；之后 .bak 是被换下的那一版；内容没变时不拿它盖掉 .bak。"""
        fileio.write_bytes(self.path, OLD, backup=True)
        self.assertEqual(names(self.dir), ["state.json"])
        fileio.write_bytes(self.path, NEW, backup=True)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))
        self.assertTrue(fileio.write_bytes(self.path, NEW, backup=True))  # 照常重写，.bak 留着上一份不一样的
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))
        fileio.write_bytes(self.path, NEWER, backup=True)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEWER, NEW))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_backup_steps_flush_before_replace(self):
        """留备份时目标和 .bak 都是先写临时文件、刷盘、再替换；先换目标、后写备份。"""
        self.path.write_bytes(OLD)
        self.bak.write_bytes(PREV)
        with Disk() as disk, self.assertNoLogs("advisor.fileio"):
            fileio.write_bytes(self.path, NEW, backup=True)
        tmp, bak_tmp = "state.json.tmp", "state.json.bak.tmp"
        self.assertEqual(disk.log, [("open", tmp), ("write", tmp), ("fsync", tmp), ("replace", tmp),
                                    ("open", bak_tmp), ("write", bak_tmp), ("fsync", bak_tmp), ("replace", bak_tmp)])
        self.assertEqual(disk.unsafe, [])

    def test_backup_skips_zeros_and_empty(self):
        """原来的内容是全零（断电后的坏样子）或空的：不拿它盖掉还能用的 .bak。"""
        for broken in (b"\0" * 37, b""):
            with self.subTest(broken=broken):
                self.path.write_bytes(broken)
                self.bak.write_bytes(PREV)
                fileio.write_bytes(self.path, NEW, backup=True)
                self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, PREV))

    def test_backup_when_old_unreadable(self):
        """读不出原来的内容（一直被占用）时照常写、不动 .bak；if_changed 也照常写。"""
        self.path.write_bytes(OLD)
        self.bak.write_bytes(PREV)
        real, path = Path.read_bytes, self.path

        def locked(p):
            if p == path:
                raise busy()
            return real(p)

        self.no_delays()
        with mock.patch.object(Path, "read_bytes", locked):
            self.assertTrue(fileio.write_bytes(self.path, NEW, backup=True, if_changed=True))
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, PREV))

    def test_stale_temp_files(self):
        """上次中途被结束留下的临时文件照常覆盖，写完不留。"""
        (self.dir / "state.json.tmp").write_bytes(b"half")
        (self.dir / "state.json.bak.tmp").write_bytes(b"\0\0")
        self.path.write_bytes(OLD)
        fileio.write_bytes(self.path, NEW, backup=True)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))

    def test_target_is_folder(self):
        """目标是文件夹：马上报错（不等），不留临时文件。"""
        folder = self.dir / "state.json"
        folder.mkdir()
        sleep = self.sleeps()
        with self.assertRaises(OSError):
            fileio.write_bytes(folder, NEW)
        sleep.assert_not_called()
        self.assertEqual(names(self.dir), ["state.json"])
        self.assertTrue(folder.is_dir())

    def test_backup_path(self):
        self.assertEqual(fileio.backup_path(self.path), self.bak)
        self.assertEqual(fileio.backup_path(str(self.path)), self.bak)

    def test_bad_suffix(self):
        """suffix 是空的（临时文件就是目标本身，会先截断再写）或 .bak（临时文件就是备份）：报 ValueError，什么都不碰。"""
        path = self.dir / "sub" / "a.json"
        for suffix in ("", ".bak"):
            with self.subTest(suffix=suffix), Disk() as disk:
                with self.assertRaises(ValueError):
                    fileio.write_bytes(path, NEW, suffix=suffix)
                with self.assertRaises(ValueError):
                    fileio.write_text(path, "x", suffix=suffix, backup=True)
            self.assertEqual(disk.log, [])
        self.assertFalse(path.parent.exists())


class BackupCheckTest(_Case):
    """backup_check：原来的内容判断为不能用时不拿它盖掉 .bak（记警告）；能用时照常留作备份；它出程序错误时什么都不写。"""

    def setUp(self):
        super().setUp()
        self.bak.write_bytes(PREV)

    def test_bad_old_content_keeps_backup(self):
        """文件被外面改坏了（不是全零的乱码）：照常换上新内容，.bak 留着上一份还能用的，记一条警告。"""
        self.path.write_bytes(b'{"hero": ')
        with self.assertLogs("advisor.fileio", "WARNING") as logs:
            self.assertTrue(fileio.write_bytes(self.path, NEW, backup=True, backup_check=parse_json))
        self.assertEqual(len(logs.records), 1)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, PREV))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_good_old_content_becomes_backup(self):
        self.path.write_bytes(OLD)
        with self.assertNoLogs("advisor.fileio"):
            fileio.write_bytes(self.path, NEW, backup=True, backup_check=parse_json)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))

    def test_without_check_bad_content_goes_to_backup(self):
        """不传时照旧：只拦空的和全零的，乱码照样进 .bak（目标已经是好的新内容）。"""
        self.path.write_bytes(b'{"hero": ')
        fileio.write_bytes(self.path, NEW, backup=True)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, b'{"hero": '))

    def test_program_error_writes_nothing(self):
        """backup_check 报 BAD_CONTENT 以外的错（程序出错）：照原样抛出，什么都还没写，目标、.bak 都不动。"""
        self.path.write_bytes(OLD)

        def buggy(data):
            raise TypeError("程序写错了")

        with Disk() as disk, self.assertRaises(TypeError):
            fileio.write_bytes(self.path, NEW, backup=True, backup_check=buggy)
        self.assertEqual(disk.log, [])
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (OLD, PREV))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_only_called_when_backing_up(self):
        """只在真要拿原来的内容做备份时才看：还没有文件、不留备份、内容没变、原来是全零的时候都不调用。"""
        check = mock.Mock(side_effect=parse_json)
        fileio.write_bytes(self.dir / "new.json", NEW, backup=True, backup_check=check)
        self.path.write_bytes(OLD)
        fileio.write_bytes(self.path, NEW, backup_check=check)
        fileio.write_bytes(self.path, NEW, backup=True, backup_check=check)
        self.path.write_bytes(b"\0" * 8)
        fileio.write_bytes(self.path, NEW, backup=True, backup_check=check)
        check.assert_not_called()
        fileio.write_bytes(self.path, NEWER, backup=True, backup_check=check)
        check.assert_called_once_with(NEW)
        self.assertEqual(self.bak.read_bytes(), NEW)

    def test_write_text_passes_it_on(self):
        self.path.write_bytes(b"not json")
        with self.assertLogs("advisor.fileio", "WARNING"):
            fileio.write_text(self.path, "{}", backup=True, backup_check=parse_json)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (b"{}", PREV))


class WriteTextTest(_Case):
    TEXT = '{\n "hero": "凛",\n "owned": []\n}'

    def test_default_newline_same_as_path_write_text(self):
        """newline 默认同 open()：换成系统的换行，写出的字节和 Path.write_text 相同（Windows 上是 \\r\\n）。"""
        other = self.dir / "other.json"
        other.write_text(self.TEXT, encoding="utf-8")
        fileio.write_text(self.path, self.TEXT)
        self.assertEqual(self.path.read_bytes(), other.read_bytes())
        self.assertEqual(self.path.read_bytes(), self.TEXT.replace("\n", os.linesep).encode("utf-8"))

    def test_newline(self):
        for newline, end in (("\n", b"\n"), ("", b"\n"), ("\r\n", b"\r\n"), ("\r", b"\r")):
            with self.subTest(newline=newline):
                fileio.write_text(self.path, "a\nb\n", newline=newline)
                self.assertEqual(self.path.read_bytes(), b"a" + end + b"b" + end)
        with self.assertRaises(ValueError):
            fileio.write_text(self.dir / "bad.txt", "a", newline="\t")
        self.assertFalse((self.dir / "bad.txt").exists())

    def test_encode_error_leaves_file(self):
        """写不成 UTF-8 的字（单独的代理项）：编码时就报错，文件、文件夹都不动；errors 可以换成转义。"""
        self.path.write_bytes(OLD)
        with Disk() as disk, self.assertRaises(UnicodeEncodeError):
            fileio.write_text(self.path, "坏字\udc80", backup=True)
        self.assertEqual(disk.log, [])
        self.assertEqual(names(self.dir), ["state.json"])
        self.assertEqual(self.path.read_bytes(), OLD)
        fileio.write_text(self.path, "坏字\udc80", errors="backslashreplace")
        self.assertEqual(self.path.read_bytes(), "坏字".encode("utf-8") + b"\\udc80")

    def test_text_options_passed_on(self):
        self.path.write_text("x", encoding="utf-8")
        self.assertFalse(fileio.write_text(self.path, "x", if_changed=True))
        fileio.write_text(self.path, "y", backup=True)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (b"y", b"x"))


class WriteFaultTest(_Case):
    """在整份写入（留备份）的每一步出错、或程序被结束：目标文件和 .bak 至少有一份完整（都是某一版的全部内容）。"""

    def fresh(self) -> Path:
        folder = sandbox.temp_dir(self, "fileio-fault-")
        (folder / "state.json").write_bytes(OLD)
        (folder / "state.json.bak").write_bytes(PREV)
        return folder / "state.json"

    def steps(self) -> list[tuple[str, str]]:
        """没有出错时一次留备份的写入有哪几步。"""
        with Disk() as disk:
            fileio.write_bytes(self.fresh(), NEW, backup=True)
        self.assertEqual(len(disk.log), 8)
        return disk.log

    def runs(self):
        """每一步各出一次错：(第几步, 步骤, 这一步是第几次做, 是否在换上目标以前)。"""
        steps = self.steps()
        swapped = steps.index(("replace", "state.json.tmp"))
        for i, (step, name) in enumerate(steps):
            nth = sum(1 for s, _ in steps[: i + 1] if s == step)
            yield i, f"{step} {name}", step, nth, i <= swapped

    def test_error_at_each_step(self):
        """出错（磁盘满、写不进）：换上目标以前的出错照原样报，目标、.bak 都不动；换上以后的（写备份）不报错、记警告，
        目标是新内容、.bak 停在上一代。都不留临时文件。刷盘报错不算出错，见 SyncErrorTest。"""
        for i, label, step, nth, before in self.runs():
            if step == "fsync":
                continue
            with self.subTest(step=label):
                path = self.fresh()
                with Disk() as disk:
                    disk.fail(step, nth)
                    if before:
                        with self.assertNoLogs("advisor.fileio"), self.assertRaises(Fault):
                            fileio.write_bytes(path, NEW, backup=True)
                    else:
                        with self.assertLogs("advisor.fileio", "WARNING"):
                            self.assertTrue(fileio.write_bytes(path, NEW, backup=True))
                want = (OLD, PREV) if before else (NEW, PREV)
                self.assertEqual((path.read_bytes(), fileio.backup_path(path).read_bytes()), want)
                self.assertEqual(names(path.parent), ["state.json", "state.json.bak"])

    def test_crash_at_each_step(self):
        """程序在任何一步被结束（收尾的代码也来不及跑）：目标文件和 .bak 各是某一版的全部内容，目标没换成时 .bak 不动；
        下次照常写，留下的临时文件被覆盖、写完不留。"""
        for i, label, step, nth, before in self.runs():
            with self.subTest(step=label):
                path = self.fresh()
                with Disk() as disk:
                    disk.keep_temps = True
                    disk.fail(step, nth, error=Crash)
                    with self.assertRaises(Crash):
                        fileio.write_bytes(path, NEW, backup=True)
                got = (path.read_bytes(), fileio.backup_path(path).read_bytes())
                self.assertIn(got, [(OLD, PREV), (NEW, PREV), (NEW, OLD)])
                self.assertEqual(got[0], OLD if before else NEW)
                fileio.write_bytes(path, NEWER, backup=True)
                self.assertEqual((path.read_bytes(), fileio.backup_path(path).read_bytes()), (NEWER, got[0]))
                self.assertEqual(names(path.parent), ["state.json", "state.json.bak"])

    def test_error_without_backup(self):
        """不留备份时同样：替换以前任何一步出错，目标不动、不留临时文件。"""
        for step in ("open", "write", "replace"):
            with self.subTest(step=step):
                path = self.fresh()
                with Disk() as disk, self.assertRaises(Fault):
                    disk.fail(step, 1)
                    fileio.write_bytes(path, NEW)
                self.assertEqual(path.read_bytes(), OLD)
                self.assertEqual(names(path.parent), ["state.json", "state.json.bak"])


class SyncErrorTest(_Case):
    """刷盘报错（个别网络盘、虚拟盘不支持，报的一律是 EBADF）不算写不进：照常替换、记一条警告，同一个文件只记一次。"""

    def setUp(self):
        super().setUp()
        self.path.write_bytes(OLD)
        self.bak.write_bytes(PREV)

    def test_write_goes_on(self):
        """目标和 .bak 的刷盘都报错：照常换上，各记一条警告；这一次不保证落盘（替换时内容还没刷进硬盘）。"""
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING") as logs:
            disk.fail("fsync", 1, 2, error=lambda: OSError(errno.EBADF, "句柄无效"))
            self.assertTrue(fileio.write_bytes(self.path, NEW, backup=True))
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])
        self.assertEqual(disk.unsafe, ["state.json.tmp", "state.json.bak.tmp"])
        self.assertEqual(len(logs.records), 2)
        self.assertIn("state.json.bak", logs.records[1].getMessage())

    def test_warn_once_per_file(self):
        """同一个文件只记一次（每存一次设置都报一条没有用），换一个文件再记。"""
        with Disk() as disk:
            disk.fail("fsync", 1, 2, 3)
            with self.assertLogs("advisor.fileio", "WARNING"):
                fileio.write_bytes(self.path, NEW)
            with self.assertNoLogs("advisor.fileio"):
                fileio.write_bytes(self.path, NEWER)
            with self.assertLogs("advisor.fileio", "WARNING"):
                fileio.write_bytes(self.dir / "other.json", NEW)
        self.assertEqual((self.path.read_bytes(), (self.dir / "other.json").read_bytes()), (NEWER, NEW))

    def test_append_goes_on(self):
        log_file = self.dir / "logs" / "2026-10.jsonl"
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("fsync", 1)
            fileio.append_lines(log_file, ["x"], newline="\n", durable=True)
        self.assertEqual(log_file.read_bytes(), b"x\n")


class RetryTest(_Case):
    """被别的程序占用（PermissionError）时稍等再试，合计不到 1 秒；用完了报错；只读、文件夹马上报。"""

    def setUp(self):
        super().setUp()
        self.path.write_bytes(OLD)
        self.bak.write_bytes(PREV)

    def test_delays(self):
        delays = fileio.RETRY_DELAYS
        self.assertGreaterEqual(len(delays), 3)
        self.assertTrue(all(d > 0 for d in delays))
        self.assertEqual(list(delays), sorted(delays))
        self.assertLessEqual(sum(delays), 1.0)

    def test_busy_then_free(self):
        sleep = self.sleeps()
        with Disk() as disk:
            disk.fail("replace", 1, 2, error=busy)
            self.assertTrue(fileio.write_bytes(self.path, NEW, backup=True))
        self.assertEqual(disk.log.count(("replace", "state.json.tmp")), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(fileio.RETRY_DELAYS[:2]))
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, OLD))

    def test_busy_until_given_up(self):
        """一直被占用：试满 len(RETRY_DELAYS) + 1 次、等满全部间隔以后报 PermissionError；目标、.bak 不动，不留临时文件。"""
        sleep = self.sleeps()
        tries = len(fileio.RETRY_DELAYS) + 1
        with Disk() as disk, self.assertRaises(PermissionError):
            disk.fail("replace", *range(1, tries + 1), error=busy)
            fileio.write_bytes(self.path, NEW, backup=True)
        self.assertEqual(disk.count("replace"), tries)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(fileio.RETRY_DELAYS))
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (OLD, PREV))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_busy_temp_file(self):
        """临时文件一时打不开（杀毒软件正扫着上次留下的）：同样重试。"""
        (self.dir / "state.json.tmp").write_bytes(b"half")
        self.no_delays()
        with Disk() as disk:
            disk.fail("open", 1, error=busy)
            fileio.write_bytes(self.path, NEW)
        self.assertEqual(disk.count("open"), 2)
        self.assertEqual(self.path.read_bytes(), NEW)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_busy_new_file(self):
        """第一次存的新文件（例如下载的图片）：刚写好的临时文件正被杀毒软件扫着，替换报 PermissionError；目标还不在也照样
        重试（目标不在只说明不是它被占用）。"""
        path = self.dir / "a.png"
        self.no_delays()
        with Disk() as disk:
            disk.fail("replace", 1, error=busy)
            self.assertTrue(fileio.write_bytes(path, NEW, suffix=".part"))
        self.assertEqual(disk.count("replace"), 2)
        self.assertEqual(path.read_bytes(), NEW)
        self.assertEqual(names(self.dir), ["a.png", "state.json", "state.json.bak"])

    def test_unwritable_folder_fails_at_once(self):
        """文件夹写不进（没有权限、被受控文件夹访问拦下）：临时文件还不在、新建时报 PermissionError，没有别的程序能开着
        一个还不在的文件，重试也没用，马上报（不等）；目标、.bak 不动。"""
        sleep = self.sleeps()
        with Disk() as disk, self.assertRaises(PermissionError):
            disk.fail("open", 1, error=denied)
            fileio.write_bytes(self.path, NEW, backup=True)
        sleep.assert_not_called()
        self.assertEqual(disk.count("open"), 1)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (OLD, PREV))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_read_only_target_fails_at_once(self):
        """目标是只读的：重试也没用，马上报（不等），什么都不留下：没有临时文件，也不建 .bak。"""
        self.bak.unlink()
        self.read_only(self.path)
        sleep = self.sleeps()
        with self.assertRaises(PermissionError) as raised:
            fileio.write_bytes(self.path, NEW, backup=True)
        sleep.assert_not_called()
        if os.name == "nt":
            self.assertEqual(raised.exception.winerror, 5)  # 和 os.replace 本来报的一样（调用方按错误代码显示原因）
        self.assertEqual(self.path.read_bytes(), OLD)
        self.assertEqual(names(self.dir), ["state.json"])

    def test_busy_read(self):
        """读的时候被占用一下：重试以后照常读到。"""
        real, path, calls = Path.read_bytes, self.path, []

        def flaky(p):
            if p == path and not calls:
                calls.append(p)
                raise busy()
            return real(p)

        self.no_delays()
        with mock.patch.object(Path, "read_bytes", flaky):
            loaded = fileio.read_with_backup(self.path, parse_json)
        self.assertEqual((loaded.source, loaded.value), (fileio.FROM_FILE, parse_json(OLD)))
        self.assertEqual(len(calls), 1)

    def test_busy_read_until_given_up(self):
        """一直读不了：报 PermissionError，不当坏文件（不另存、不读 .bak）。"""
        real, path = Path.read_bytes, self.path

        def locked(p):
            if p == path:
                raise busy()
            return real(p)

        self.no_delays()
        with mock.patch.object(Path, "read_bytes", locked), self.assertRaises(PermissionError):
            fileio.read_with_backup(self.path, parse_json, now=NOW)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])


# ---------------------------------------------------------------- 读取并兜底


class ReadTest(_Case):
    GOOD = b'{"hero": "\xe5\x87\x9b"}'
    BACKUP = b'{"hero": "\xe8\x8a\xac\xe5\xa6\xae"}'

    def read(self, **kwargs) -> fileio.Loaded:
        return fileio.read_with_backup(self.path, parse_json, now=NOW, **kwargs)

    def test_good_file(self):
        self.path.write_bytes(self.GOOD)
        self.bak.write_bytes(b"not json")
        with self.assertNoLogs("advisor.fileio"):
            loaded = self.read()
        self.assertEqual(loaded, fileio.Loaded({"hero": "凛"}, fileio.FROM_FILE))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])  # 备份不看、不动
        self.assertEqual(self.bak.read_bytes(), b"not json")

    def test_missing_file_does_not_use_backup(self):
        """文件不在：不去读 .bak（可能是用户自己删的），什么都不动。"""
        self.bak.write_bytes(self.BACKUP)
        loaded = self.read()
        self.assertEqual(loaded, fileio.Loaded())
        self.assertEqual(loaded.source, fileio.NOTHING)
        self.assertEqual(names(self.dir), ["state.json.bak"])
        self.assertEqual(self.bak.read_bytes(), self.BACKUP)

    def test_bad_file_uses_backup_and_restores(self):
        """读坏了（断电后的全零）、.bak 能用：先给坏文件留一份 .broken-时间（硬链接），再用 .bak 整份替换文件，文件名
        一直在（不改名）；.bak 不动，下次直接读到。"""
        zeros = b"\0" * len(self.GOOD)
        self.path.write_bytes(zeros)
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            loaded = self.read()
        broken = self.dir / f"state.json.broken-{STAMP}"
        self.assertEqual((loaded.value, loaded.source, loaded.broken, loaded.stuck, loaded.restore_error),
                         ({"hero": "芬妮"}, fileio.FROM_BACKUP, broken, False, None))
        self.assertIsInstance(loaded.error, ValueError)
        self.assertEqual(broken.read_bytes(), zeros)
        self.assertEqual(self.path.read_bytes(), self.BACKUP)
        self.assertEqual(self.bak.read_bytes(), self.BACKUP)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak", broken.name])
        self.assertEqual(disk.log[0], ("link", "state.json"))
        self.assertEqual(disk.count("rename"), 0)
        self.assertEqual(disk.unsafe, [])  # 恢复同样先刷盘再替换
        self.assertEqual(self.read(), fileio.Loaded({"hero": "芬妮"}, fileio.FROM_FILE))  # 下次直接读到

    def test_crash_while_restoring_keeps_file(self):
        """用备份恢复的任何一步被结束（收尾的代码也来不及跑）：文件一直在（坏的那份或恢复好的），.bak 不动；下次照样
        读到备份的内容（坏的那份再恢复一次），不会变成「文件不在」而不去读 .bak。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, mock.patch.object(fileio, "log"):
            fileio.read_with_backup(self.path, parse_json, now=NOW)
        steps = disk.log
        self.assertEqual([s for s, _ in steps], ["link", "open", "write", "fsync", "replace"])
        for step, name in steps:
            with self.subTest(step=f"{step} {name}"):
                for p in self.dir.iterdir():
                    p.unlink()
                self.path.write_bytes(b"x")
                self.bak.write_bytes(self.BACKUP)
                with Disk() as disk, mock.patch.object(fileio, "log"):
                    disk.keep_temps = True
                    disk.fail(step, 1, error=Crash)
                    with self.assertRaises(Crash):
                        fileio.read_with_backup(self.path, parse_json, now=NOW)
                self.assertIn(self.path.read_bytes(), (b"x", self.BACKUP))
                self.assertEqual(self.bak.read_bytes(), self.BACKUP)
                with mock.patch.object(fileio, "log"):
                    loaded = fileio.read_with_backup(self.path, parse_json, now=NOW)
                self.assertEqual(loaded.value, {"hero": "芬妮"})
                self.assertEqual(self.path.read_bytes(), self.BACKUP)

    def test_no_hard_links_copies(self):
        """盘不支持硬链接（FAT 这类）：照读到的内容另写一份（刷了盘再恢复），再用 .bak 恢复。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("link", 1, error=lambda: OSError(errno.EINVAL, "函数不正确"))
            loaded = self.read()
        broken = self.dir / f"state.json.broken-{STAMP}"
        self.assertEqual((loaded.broken, loaded.source, loaded.stuck, loaded.restore_error),
                         (broken, fileio.FROM_BACKUP, False, None))
        self.assertEqual((broken.read_bytes(), self.path.read_bytes()), (b"x", self.BACKUP))
        self.assertEqual(disk.log[:4], [("link", "state.json"), ("open", broken.name), ("write", broken.name),
                                        ("fsync", broken.name)])
        self.assertNotIn(broken.name, disk.unsynced())

    def test_no_hard_links_names_never_overwrite(self):
        """不支持硬链接、另写一份时同样不覆盖已有的：名字已经有了、或者查过以后才被占的（新建时报 FileExistsError），
        都接下一个。"""
        first = self.dir / f"state.json.broken-{STAMP}"
        first.write_bytes(b"first")
        self.path.write_bytes(b"x")
        self.bak.write_bytes(self.BACKUP)

        def taken():
            return FileExistsError(errno.EEXIST, "当文件已存在时，无法创建该文件")

        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("link", 1, 2, error=lambda: OSError(errno.EINVAL, "函数不正确"))
            disk.fail("open", 1, error=taken)
            loaded = self.read()
        self.assertEqual(loaded.broken, self.dir / f"state.json.broken-{STAMP}-3")
        self.assertEqual((first.read_bytes(), loaded.broken.read_bytes()), (b"first", b"x"))
        self.assertFalse((self.dir / f"state.json.broken-{STAMP}-2").exists())

    def test_half_copy_removed(self):
        """另写一份写到一半出错（磁盘满）：写了一半的那份删掉，算没留下（stuck），坏文件不动。"""
        self.path.write_bytes(b"x" * 100)
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("link", 1, error=fault)
            disk.fail("write", 1)
            loaded = self.read()
        self.assertEqual((loaded.source, loaded.broken, loaded.stuck), (fileio.FROM_BACKUP, None, True))
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])
        self.assertEqual(self.path.read_bytes(), b"x" * 100)

    def test_bad_file_without_backup(self):
        self.path.write_bytes(b'{"hero": ')
        with self.assertLogs("advisor.fileio", "WARNING"):
            loaded = self.read()
        broken = self.dir / f"state.json.broken-{STAMP}"
        self.assertEqual((loaded.value, loaded.source, loaded.broken, loaded.stuck), (None, fileio.NOTHING, broken, False))
        self.assertEqual(names(self.dir), [broken.name])
        self.assertEqual(broken.read_bytes(), b'{"hero": ')

    def test_bad_backup_set_aside_too(self):
        """.bak 也读坏了：同样另存（以后的写入会生成新的 .bak，不能盖掉它），什么都没读到。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(b"[1, 2]")
        with self.assertLogs("advisor.fileio", "WARNING") as logs:
            loaded = self.read()
        self.assertEqual((loaded.source, loaded.value), (fileio.NOTHING, None))
        self.assertEqual(names(self.dir), [f"state.json.bak.broken-{STAMP}", f"state.json.broken-{STAMP}"])
        self.assertEqual((self.dir / f"state.json.bak.broken-{STAMP}").read_bytes(), b"[1, 2]")
        self.assertEqual(len(logs.records), 2)

    def test_backup_unreadable(self):
        """.bak 读不了（这里是个文件夹，读它不用等）：当作没有能用的备份，.bak 不动。"""
        self.path.write_bytes(b"x")
        self.bak.mkdir()
        sleep = self.sleeps()
        with self.assertLogs("advisor.fileio", "WARNING"):
            loaded = self.read()
        sleep.assert_not_called()
        self.assertEqual(loaded.source, fileio.NOTHING)
        self.assertTrue(self.bak.is_dir())
        self.assertEqual(names(self.dir), ["state.json.bak", f"state.json.broken-{STAMP}"])

    def test_empty_and_deep_json_are_bad(self):
        """空文件、嵌套太深的 JSON（RecursionError）也算读坏。"""
        for data in (b"", b"[" * 100_000):
            with self.subTest(size=len(data)):
                for p in self.dir.iterdir():
                    p.unlink()
                self.path.write_bytes(data)
                self.bak.write_bytes(self.BACKUP)
                with self.assertLogs("advisor.fileio", "WARNING"):
                    loaded = self.read()
                self.assertEqual(loaded.source, fileio.FROM_BACKUP)
                self.assertEqual((self.dir / f"state.json.broken-{STAMP}").read_bytes(), data)

    def test_program_error_is_not_a_bad_file(self):
        """解析函数报 ValueError、RecursionError 以外的错（程序出错）：照原样抛出，文件和备份都不动。"""
        self.path.write_bytes(self.GOOD)
        self.bak.write_bytes(self.BACKUP)

        def buggy(data):
            raise TypeError("程序写错了")

        with self.assertRaises(TypeError):
            fileio.read_with_backup(self.path, buggy, now=NOW)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

        def buggy_on_backup(data):  # 文件本身读坏了、解析备份时程序出错：同样照原样抛出，坏文件、备份都不动
            if data == self.BACKUP:
                raise TypeError("程序写错了")
            return parse_json(data)

        self.path.write_bytes(b"x")
        with self.assertNoLogs("advisor.fileio"), self.assertRaises(TypeError):
            fileio.read_with_backup(self.path, buggy_on_backup, now=NOW)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (b"x", self.BACKUP))

    def test_unreadable_file_is_not_a_bad_file(self):
        """文件读不了（这里是个文件夹，读它不用等）：报 OSError，不另存、不读 .bak。"""
        self.path.mkdir()
        self.bak.write_bytes(self.BACKUP)
        sleep = self.sleeps()
        with self.assertRaises(OSError):
            self.read()
        sleep.assert_not_called()
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_broken_names_never_overwrite(self):
        """同一秒已经另存过：接 -2、-3……，已有的不覆盖。"""
        first = self.dir / f"state.json.broken-{STAMP}"
        second = self.dir / f"state.json.broken-{STAMP}-2"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.path.write_bytes(b"third")
        with self.assertLogs("advisor.fileio", "WARNING"):
            loaded = self.read()
        self.assertEqual(loaded.broken, self.dir / f"state.json.broken-{STAMP}-3")
        self.assertEqual((first.read_bytes(), second.read_bytes(), loaded.broken.read_bytes()),
                         (b"first", b"second", b"third"))

    def test_copy_names_never_overwrite(self):
        """有能用的备份、给坏文件留一份时同样：同一秒已经另存过的接 -2、-3……，已有的不覆盖。"""
        first = self.dir / f"state.json.broken-{STAMP}"
        second = self.dir / f"state.json.broken-{STAMP}-2"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.path.write_bytes(b"third")
        self.bak.write_bytes(self.BACKUP)
        with self.assertLogs("advisor.fileio", "WARNING"):
            loaded = self.read()
        self.assertEqual(loaded.broken, self.dir / f"state.json.broken-{STAMP}-3")
        self.assertEqual((first.read_bytes(), second.read_bytes(), loaded.broken.read_bytes()),
                         (b"first", b"second", b"third"))
        self.assertEqual(self.path.read_bytes(), self.BACKUP)

    def test_name_taken_meanwhile(self):
        """查的时候名字还空着、改名或建硬链接时被占了（Windows 上两者都不覆盖，报 FileExistsError）：换下一个名字。"""

        def taken():
            return FileExistsError(errno.EEXIST, "当文件已存在时，无法创建该文件")

        for step, backup in (("rename", None), ("link", self.BACKUP)):
            with self.subTest(step=step):
                for p in self.dir.iterdir():
                    p.unlink()
                self.path.write_bytes(b"x")
                if backup is not None:
                    self.bak.write_bytes(backup)
                with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
                    disk.fail(step, 1, error=taken)
                    loaded = self.read()
                self.assertEqual(loaded.broken, self.dir / f"state.json.broken-{STAMP}-2")
                self.assertEqual(loaded.broken.read_bytes(), b"x")

    def test_stuck_bad_file(self):
        """有能用的备份，坏文件却留不下一份（硬链接、另写一份都不成）：stuck，照样读 .bak 给调用方，但不恢复（坏文件
        还在原处、又没有副本，不盖掉它）。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("link", 1, error=fault)
            disk.fail("open", 1, error=fault)
            loaded = self.read()
        self.assertEqual((loaded.value, loaded.source, loaded.broken, loaded.stuck, loaded.restore_error),
                         ({"hero": "芬妮"}, fileio.FROM_BACKUP, None, True, None))
        self.assertEqual(disk.count("replace"), 0)
        self.assertEqual(self.path.read_bytes(), b"x")
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak"])

    def test_stuck_without_backup(self):
        """没有能用的备份，坏文件又改不了名（一直被占用）：stuck，什么都没读到，坏文件还在原处。"""
        self.path.write_bytes(b"x")
        self.no_delays()
        tries = len(fileio.RETRY_DELAYS) + 1
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("rename", *range(1, tries + 1), error=busy)
            loaded = self.read()
        self.assertEqual((loaded.value, loaded.source, loaded.broken, loaded.stuck),
                         (None, fileio.NOTHING, None, True))
        self.assertIsInstance(loaded.error, ValueError)
        self.assertEqual(names(self.dir), ["state.json"])

    def test_restore_fails(self):
        """用备份恢复没成：照样给出备份的内容，原因在 restore_error；坏文件还在原处（已经留了一份），.bak 不动。下一次
        写入换掉它，传了 backup_check 时坏内容不进 .bak。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(self.BACKUP)
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("replace", 1)
            loaded = self.read()
        broken = self.dir / f"state.json.broken-{STAMP}"
        self.assertEqual((loaded.value, loaded.source, loaded.broken, loaded.stuck),
                         ({"hero": "芬妮"}, fileio.FROM_BACKUP, broken, False))
        self.assertIsInstance(loaded.restore_error, Fault)
        self.assertEqual(names(self.dir), ["state.json", "state.json.bak", broken.name])
        self.assertEqual((self.path.read_bytes(), broken.read_bytes(), self.bak.read_bytes()),
                         (b"x", b"x", self.BACKUP))
        with self.assertLogs("advisor.fileio", "WARNING"):
            fileio.write_bytes(self.path, NEW, backup=True, backup_check=parse_json)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (NEW, self.BACKUP))

    def test_busy_move_aside(self):
        """另存时被占用一下：重试以后照常另存。"""
        self.path.write_bytes(b"x")
        self.no_delays()
        with Disk() as disk, self.assertLogs("advisor.fileio", "WARNING"):
            disk.fail("rename", 1, error=busy)
            loaded = self.read()
        self.assertEqual(loaded.broken, self.dir / f"state.json.broken-{STAMP}")
        self.assertEqual(disk.count("rename"), 2)


class MoveAsideTest(_Case):
    def test_tag(self):
        """只剩的备份另存成 .bak.orphan-时间、用过的退回文件 .used-时间：同样不覆盖已有的。"""
        self.bak.write_bytes(PREV)
        moved = fileio.move_aside(self.bak, "orphan", now=NOW)
        self.assertEqual(moved, self.dir / f"state.json.bak.orphan-{STAMP}")
        self.assertEqual(moved.read_bytes(), PREV)
        self.path.write_bytes(OLD)
        (self.dir / f"state.json.used-{STAMP}").write_bytes(b"earlier")
        self.assertEqual(fileio.move_aside(self.path, "used", now=NOW).name, f"state.json.used-{STAMP}-2")
        self.assertEqual((self.dir / f"state.json.used-{STAMP}").read_bytes(), b"earlier")

    def test_default_time_and_missing(self):
        self.path.write_bytes(OLD)
        moved = fileio.move_aside(self.path)
        self.assertRegex(moved.name, r"^state\.json\.broken-\d{8}-\d{6}$")
        with self.assertRaises(FileNotFoundError):
            fileio.move_aside(self.path)


# ---------------------------------------------------------------- 追加


class AppendTest(_Case):
    def setUp(self):
        super().setUp()
        self.log = self.dir / "logs" / "2026-10.jsonl"

    def test_creates_file_and_folder(self):
        fileio.append_lines(self.log, ['{"a": 1}', '{"b": 2}'], newline="\n")
        self.assertEqual(self.log.read_bytes(), b'{"a": 1}\n{"b": 2}\n')
        fileio.append_lines(self.log, '{"c": 3}', newline="\n")  # 一段文字算一行
        self.assertEqual(self.log.read_bytes(), b'{"a": 1}\n{"b": 2}\n{"c": 3}\n')

    def test_after_half_line(self):
        """上次写到一半（末尾没有换行）：先补一个换行，新的一行不和那半行接在一起。"""
        self.log.parent.mkdir()
        self.log.write_bytes(b'{"a": 1}\n{"b": ')
        fileio.append_lines(self.log, ['{"c": 3}'], newline="\n")
        self.assertEqual(self.log.read_bytes().split(b"\n"), [b'{"a": 1}', b'{"b": ', b'{"c": 3}', b""])

    def test_after_zero_bytes(self):
        """断电后末尾是一串零字节：同样先另起一行。"""
        self.log.parent.mkdir()
        self.log.write_bytes(b'{"a": 1}\n\0\0\0\0')
        fileio.append_lines(self.log, ['{"c": 3}'], newline="\n")
        self.assertEqual(self.log.read_bytes(), b'{"a": 1}\n\0\0\0\0\n{"c": 3}\n')

    def test_empty_file_and_list(self):
        self.log.parent.mkdir()
        self.log.write_bytes(b"")
        fileio.append_lines(self.log, ["x"], newline="\n")
        self.assertEqual(self.log.read_bytes(), b"x\n")  # 空文件前面不补换行
        other = self.dir / "none.jsonl"
        fileio.append_lines(other, [])
        self.assertFalse(other.exists())

    def test_default_newline_same_as_text_mode(self):
        """newline 默认同 open()：换成系统的换行，和以文字方式追加写出的字节相同；末尾是 \\r\\n 的不再补。"""
        other = self.dir / "other.jsonl"
        with other.open("a", encoding="utf-8") as f:
            f.write("甲\n乙\n")
        fileio.append_lines(self.log, ["甲", "乙"])
        self.assertEqual(self.log.read_bytes(), other.read_bytes())
        fileio.append_lines(self.log, ["丙"], newline="\r\n")
        self.assertEqual(self.log.read_bytes(), "甲{0}乙{0}丙\r\n".format(os.linesep).encode("utf-8"))

    def test_encode_error_leaves_file(self):
        """有一行写不成 UTF-8（单独的代理项）：在打开文件以前就报错，一行都不写，文件不动（没有的照旧没有）。"""
        self.log.parent.mkdir()
        self.log.write_bytes(b'{"a": 1}\n')
        missing = self.dir / "missing.jsonl"
        for path, before in ((self.log, b'{"a": 1}\n'), (missing, None)):
            with self.subTest(path=path.name), Disk() as disk, self.assertRaises(UnicodeEncodeError):
                fileio.append_lines(path, ['{"ok": 1}', '{"cover_class": "坏\udc80"}'], newline="\n")
            self.assertEqual(disk.log, [])
            self.assertEqual(path.read_bytes() if before is not None else path.exists(), before or False)

    def test_backslashreplace(self):
        fileio.append_lines(self.log, ['{"c": "坏\udc80"}'], errors="backslashreplace", newline="\n")
        self.assertEqual(self.log.read_bytes(), '{"c": "坏'.encode("utf-8") + b'\\udc80"}\n')

    def test_newline_inside_a_line(self):
        """一行里有换行符：报 ValueError，文件不动。"""
        for bad in ("a\nb", "a\rb"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fileio.append_lines(self.log, ["ok", bad])
        self.assertFalse(self.log.exists())

    def test_durable(self):
        """durable：写完刷盘（先 flush 再 fsync，不然还在缓冲里的内容刷不到）；默认不刷。"""
        with Disk() as disk:
            fileio.append_lines(self.log, ["x"], newline="\n")
        self.assertEqual(disk.count("fsync"), 0)
        self.assertEqual(disk.unsynced(), {self.log.name})
        with Disk() as disk:
            fileio.append_lines(self.log, ["y"], newline="\n", durable=True)
        self.assertEqual([s for s, _ in disk.log], ["open", "write", "fsync"])
        self.assertEqual(disk.unsynced(), set())
        self.assertEqual(self.log.read_bytes(), b"x\ny\n")

    def test_busy_open(self):
        """日志文件被别的程序开着、一时打不开：重试以后照常追加。"""
        self.log.parent.mkdir()
        self.log.write_bytes(b"x\n")
        self.no_delays()
        with Disk() as disk:
            disk.fail("open", 1, error=busy)
            fileio.append_lines(self.log, ["y"], newline="\n")
        self.assertEqual(disk.count("open"), 2)
        self.assertEqual(self.log.read_bytes(), b"x\ny\n")

    def test_unwritable_folder_fails_at_once(self):
        """文件夹写不进（没有权限、被受控文件夹访问拦下）：日志文件还不在、新建时报 PermissionError，不是被占用，马上报
        （推荐日志在界面线程里写，不能每条都卡 1 秒）；文件不建，也不留下别的。"""
        sleep = self.sleeps()
        with Disk() as disk, self.assertRaises(PermissionError):
            disk.fail("open", 1, error=denied)
            fileio.append_lines(self.log, ["x"], newline="\n")
        sleep.assert_not_called()
        self.assertEqual(disk.count("open"), 1)
        self.assertEqual(names(self.log.parent), [])


# ---------------------------------------------------------------- 文件夹先写好再改名


class StagedDirTest(_Case):
    def setUp(self):
        super().setUp()
        self.final = self.dir / "reports" / "20261010-153000"

    def test_rename_when_done(self):
        with fileio.staged_dir(self.final) as work:
            self.assertEqual(work, self.final.with_name("20261010-153000.partial"))
            (work / "state.json").write_text("{}", encoding="utf-8")
            (work / "events.jsonl").write_text("", encoding="utf-8")
            self.assertFalse(self.final.exists())  # 写完以前没有正式的文件夹
        self.assertEqual(names(self.final), ["events.jsonl", "state.json"])
        self.assertEqual(names(self.final.parent), ["20261010-153000"])

    def test_error_keeps_partial(self):
        """中途出错：照原样报错，不改名，.partial 留着（不算正式产物）。"""
        with self.assertRaises(RuntimeError), fileio.staged_dir(self.final) as work:
            (work / "state.json").write_text("{}", encoding="utf-8")
            raise RuntimeError("写到一半出错")
        self.assertFalse(self.final.exists())
        self.assertEqual(names(self.final.parent), ["20261010-153000.partial"])
        self.assertEqual(names(work), ["state.json"])

    def test_leftover_partial_untouched(self):
        """以前出错留下的 .partial 不碰，这次用 .partial-2。"""
        old = self.final.with_name("20261010-153000.partial")
        old.mkdir(parents=True)
        (old / "frame.png").write_bytes(b"png")
        with fileio.staged_dir(self.final) as work:
            self.assertEqual(work.name, "20261010-153000.partial-2")
            (work / "state.json").write_text("{}", encoding="utf-8")
        self.assertEqual(names(self.final), ["state.json"])
        self.assertEqual(names(old), ["frame.png"])

    def test_existing_final(self):
        """正式的文件夹已经有了：一开始就报 FileExistsError，什么都不建。"""
        self.final.mkdir(parents=True)
        with self.assertRaises(FileExistsError), fileio.staged_dir(self.final):
            self.fail("不该进到 with 里")
        self.assertEqual(names(self.final.parent), ["20261010-153000"])

    def test_busy_rename(self):
        """改名时被占用（杀毒软件正扫着刚写的文件）：重试；一直不成就报错，.partial 留着。"""
        self.no_delays()
        with Disk() as disk:
            disk.fail("rename", 1, error=busy)
            with fileio.staged_dir(self.final) as work:
                (work / "a.txt").write_text("a", encoding="utf-8")
        self.assertEqual(names(self.final), ["a.txt"])
        other = self.dir / "reports" / "second"
        tries = len(fileio.RETRY_DELAYS) + 1
        with Disk() as disk, self.assertRaises(PermissionError):
            disk.fail("rename", *range(1, tries + 1), error=busy)
            with fileio.staged_dir(other) as work:
                (work / "a.txt").write_text("a", encoding="utf-8")
        self.assertEqual(disk.count("rename"), tries)
        self.assertFalse(other.exists())
        self.assertEqual(names(work), ["a.txt"])

    def test_rename_delays(self):
        """delays：改名被占用时按给的间隔多等几次（在后台线程里写大图的诊断包，杀毒软件扫得久）。"""
        sleep = self.sleeps()
        delays = (0.5, 1.0, 2.0, 2.0, 2.0)
        with Disk() as disk:
            disk.fail("rename", 1, 2, 3, error=busy)
            with fileio.staged_dir(self.final, delays=delays) as work:
                (work / "frame.png").write_bytes(b"png")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0, 2.0])
        self.assertEqual(names(self.final), ["frame.png"])
        sleep.reset_mock()
        other = self.dir / "reports" / "second"
        with Disk() as disk, self.assertRaises(PermissionError):
            disk.fail("rename", *range(1, len(delays) + 2), error=busy)
            with fileio.staged_dir(other, delays=delays) as work:
                (work / "frame.png").write_bytes(b"png")
        self.assertEqual(disk.count("rename"), len(delays) + 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(delays))
        self.assertEqual(names(work), ["frame.png"])


class ModuleTest(unittest.TestCase):
    def test_only_standard_library(self):
        """写盘工具放在最底层：只导入标准库，不导入 Qt、不导入 advisor 的其他模块（函数里的导入也算）。"""
        tree = ast.parse(Path(fileio.__file__).read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [(a.name, 0) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                found.append((node.module or "", node.level))
        self.assertTrue(found)
        bad = [name for name, level in found
               if level or name.split(".")[0] not in sys.stdlib_module_names | {"__future__"}]
        self.assertEqual(bad, [])

    def test_outline_lists_every_public_function(self):
        """模块开头的提纲写到了每个对外的函数。"""
        public = [n for n, v in vars(fileio).items()
                  if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == fileio.__name__
                  and not isinstance(v, type)]
        self.assertGreaterEqual(len(public), 7)
        for name in public:
            self.assertRegex(fileio.__doc__, re.escape(name) + r"\(")


if __name__ == "__main__":
    unittest.main()
