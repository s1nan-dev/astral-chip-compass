"""退回期间存的文件写回个人目录时的写盘保证（advisor/paths.py）：先刷盘再替换、被替换的那份留作 .bak（个人目录里
那份是坏的时不拿它盖掉还能用的 .bak）、退回的那份标记为用过（move_aside，不覆盖同名文件）；退回时复制个人目录里的
那份同样先刷盘再换上。临时文件放在 .tmp 下。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from advisor import fileio, paths


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-paths-durability"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class TakeBackTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.personal = self.base / paths.STATE_NAME
        self.fallback = self.base / paths.STATE_FALLBACK_NAME

    def write(self, path: Path, data: bytes, seconds: int) -> None:
        path.write_bytes(data)
        ns = int((1_790_000_000 + seconds) * 1e9)
        os.utime(path, ns=(ns, ns))

    def test_newer_fallback_is_written_back_durably(self):
        """退回的那份较新：写回个人目录时先刷盘、替换、留 .bak；退回的那份改名为 .used-（不删）。"""
        self.write(self.personal, b'{"hotkey": "F6"}', 0)
        self.write(self.fallback, b'{"hotkey": "F7"}', 10)
        with mock.patch.object(fileio, "_sync", wraps=fileio._sync) as sync:
            self.assertIsNone(paths._take_back(self.fallback, self.personal))
        self.assertTrue(sync.called)  # 先刷盘
        self.assertEqual(self.personal.read_bytes(), b'{"hotkey": "F7"}')
        self.assertEqual((self.base / "state.json.bak").read_bytes(), b'{"hotkey": "F6"}')
        self.assertFalse(self.fallback.exists())
        used = [p for p in self.base.iterdir() if paths.USED_MARK in p.name]
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0].read_bytes(), b'{"hotkey": "F7"}')

    def test_older_fallback_is_only_marked(self):
        """个人目录里的更新：不覆盖、不留 .bak，只把退回的那份标记为用过。"""
        self.write(self.personal, b'{"hotkey": "F6"}', 10)
        self.write(self.fallback, b'{"hotkey": "F7"}', 0)
        self.assertIsNone(paths._take_back(self.fallback, self.personal))
        self.assertEqual(self.personal.read_bytes(), b'{"hotkey": "F6"}')
        self.assertFalse((self.base / "state.json.bak").exists())
        self.assertFalse(self.fallback.exists())
        self.assertEqual(len([p for p in self.base.iterdir() if paths.USED_MARK in p.name]), 1)

    def test_missing_fallback_does_nothing(self):
        self.write(self.personal, b'{"hotkey": "F6"}', 0)
        self.assertIsNone(paths._take_back(self.fallback, self.personal))
        self.assertEqual(self.personal.read_bytes(), b'{"hotkey": "F6"}')

    def test_broken_personal_file_does_not_replace_good_bak(self):
        """个人目录里的那份写坏了（不是完整的 JSON）：写回较新的退回文件时，坏内容不盖掉还能用的 .bak。"""
        bak = self.base / "state.json.bak"
        self.write(self.personal, b'{"hotkey": "F', 0)
        bak.write_bytes(b'{"hotkey": "F5"}')
        self.write(self.fallback, b'{"hotkey": "F7"}', 10)
        with self.assertLogs("advisor.fileio", "WARNING"):  # 记一条「原来的内容读不出，不拿它盖掉备份」
            self.assertIsNone(paths._take_back(self.fallback, self.personal))
        self.assertEqual(self.personal.read_bytes(), b'{"hotkey": "F7"}')
        self.assertEqual(bak.read_bytes(), b'{"hotkey": "F5"}')


class AdoptTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir()
        self.base = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_copy_into_program_dir_is_durable(self):
        """退回程序目录时把个人目录里的那份复制过来：先刷盘再换上，不留临时文件；已经有了的不覆盖。"""
        src = self.base / "personal" / paths.STATE_NAME
        src.parent.mkdir()
        src.write_bytes(b'{"hotkey": "F6"}')
        dst = self.base / paths.STATE_FALLBACK_NAME
        with mock.patch.object(fileio, "_sync", wraps=fileio._sync) as sync:
            paths._adopt(src, dst)
        self.assertTrue(sync.called)
        self.assertEqual(dst.read_bytes(), b'{"hotkey": "F6"}')
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), sorted(["personal", dst.name]))
        src.write_bytes(b'{"hotkey": "F8"}')
        paths._adopt(src, dst)
        self.assertEqual(dst.read_bytes(), b'{"hotkey": "F6"}')


if __name__ == "__main__":
    unittest.main()
