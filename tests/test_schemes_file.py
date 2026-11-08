"""筹码方案文件的存储一侧（advisor/schemes.py 的 SchemeStore：_load、save、_atomic_write、_keep_orphan，读写经
advisor/fileio.py）：方案文件不在、只剩备份时不读备份，第一次保存前把备份另存为 .bak.orphan-时间、不覆盖；save 返回写没写成，
改动即存的那几个方法看 saved，编辑窗口据此写「已保存」或写不进的提示；读不了、坏文件没能另存时这次运行不写方案文件
（blocked）；用备份恢复方案文件没成时 save_error 有值、下次保存照常写；原来的内容读不出时不拿它盖掉 .bak；写的时候刷进硬盘。

文件都写在测试沙箱的临时文件夹里；另存不成、恢复不成、写不进用 mock 造（换掉 advisor.fileio 里的函数）。
"""
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime
from unittest import mock

from advisor import fileio
from advisor import schemes as S
from tests.support import sandbox

NOW = datetime(2026, 10, 10, 12, 0, 0)
STAMP = "20261010-120000"
ORPHAN_NAME = f"schemes.json.bak.orphan-{STAMP}"


def scheme(hero: str = "芬妮", name: str = "治愈坦克", **kw) -> S.Scheme:
    return S.Scheme(hero=hero, name=name, tiers={"缓冲盾牌": "core", "大碗炖肉": "first"}, **kw)


class _Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = S.get_catalog()

    def setUp(self):
        self.dir = sandbox.temp_dir(self, "test-schemes-file-")
        self.path = self.dir / "schemes.json"
        self.bak = S.backup_path(self.path)

    def open(self) -> S.SchemeStore:
        return S.SchemeStore(self.path, self.cat, now=lambda: NOW)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def saved_file(self, n: int = 1) -> bytes:
        """另一个文件夹里存了 n 套方案的方案文件内容（当作备份、坏文件以外的「好内容」用）。"""
        other = sandbox.temp_dir(self, "test-schemes-src-") / "schemes.json"
        store = S.SchemeStore(other, self.cat, now=lambda: NOW)
        for i in range(n):
            store.add(scheme(name=f"备份里的{i + 1}"))
        return other.read_bytes()


# ---------------------------------------------------------------- 只剩备份


class OrphanTest(_Case):
    def test_backup_kept_before_first_save(self):
        """方案文件不在、只剩 .bak：不读备份（可能是用户自己删的），没有提示；第一次保存前把备份另存为 .bak.orphan-时间，
        之后的保存不再碰它，下次启动照常读新的方案文件。"""
        orphan = self.saved_file(2)
        self.bak.write_bytes(orphan)
        store = self.open()
        self.assertEqual((store.schemes(), store.notice, store.saved), ([], None, True))
        self.assertEqual(self.names(), ["schemes.json.bak"])  # 读的时候什么都不动
        with self.assertLogs("advisor.schemes", level="WARNING"):
            a = store.add(scheme())
        self.assertEqual(self.names(), ["schemes.json", ORPHAN_NAME])
        self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), orphan)
        first = self.path.read_bytes()
        store.update(replace(a, note="第二版"))  # 之后照常留上一版作备份
        self.assertEqual(self.names(), ["schemes.json", "schemes.json.bak", ORPHAN_NAME])
        self.assertEqual(self.bak.read_bytes(), first)
        self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), orphan)
        again = self.open()
        self.assertEqual([s.note for s in again.schemes()], ["第二版"])

    def test_name_taken(self):
        """另存的名字已经有了（同一秒里又遇到一次）：接 -2，不覆盖已有的。"""
        (self.dir / ORPHAN_NAME).write_bytes(b"earlier")
        self.bak.write_bytes(self.saved_file())
        with self.assertLogs("advisor.schemes", level="WARNING"):
            self.open().add(scheme())
        self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), b"earlier")
        self.assertTrue((self.dir / f"{ORPHAN_NAME}-2").is_file())

    def test_set_aside_fails(self):
        """备份另存不成（被占用等）：这次照常写方案文件，但不碰 .bak（不拿新内容盖掉它）；下次保存前再试。"""
        orphan = self.saved_file()
        self.bak.write_bytes(orphan)
        store = self.open()
        with mock.patch.object(fileio, "move_aside", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            a = store.add(scheme())
            store.update(replace(a, note="第二版"))
        self.assertTrue(store.saved)
        self.assertEqual(self.names(), ["schemes.json", "schemes.json.bak"])
        self.assertEqual(self.bak.read_bytes(), orphan)  # 两次保存都没碰它
        second = self.path.read_bytes()
        with self.assertLogs("advisor.schemes", level="WARNING"):
            store.update(replace(a, note="第三版"))
        self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), orphan)
        self.assertEqual(self.bak.read_bytes(), second)

    def test_backup_gone_before_save(self):
        """读的时候只剩备份，保存前备份已经不在了（用户删了）：照常保存，不另存、不报错。"""
        self.bak.write_bytes(self.saved_file())
        store = self.open()
        self.bak.unlink()
        a = store.add(scheme())
        store.update(replace(a, note="第二版"))
        self.assertEqual(self.names(), ["schemes.json", "schemes.json.bak"])

    def test_nothing_to_write_leaves_backup(self):
        """只剩备份、又没有任何内容要存（只是选回默认推荐）：不建文件，也不动备份。"""
        orphan = self.saved_file()
        self.bak.write_bytes(orphan)
        store = self.open()
        store.set_current("芬妮", None)
        self.assertTrue(store.save())
        self.assertEqual(self.names(), ["schemes.json.bak"])
        self.assertEqual(self.bak.read_bytes(), orphan)

    def test_set_aside_before_fallback(self):
        """方案文件写不进、改存到 fallback：只剩的备份在第一次写之前已经另存（写的是哪个文件都一样）；之后写的是 fallback，
        fallback 自己留上一版作备份。"""
        orphan = self.saved_file()
        self.bak.write_bytes(orphan)
        fallback = self.dir / "app" / "schemes.fallback.json"
        store = S.SchemeStore(self.path, self.cat, now=lambda: NOW, fallback=fallback)
        real = S._atomic_write

        def main_fails(path, data, check):
            if path == self.path:
                raise PermissionError(13, "拒绝访问")
            real(path, data, check)

        with mock.patch.object(S, "_atomic_write", main_fails), self.assertLogs("advisor.schemes", level="WARNING"):
            a = store.add(scheme())
            store.update(replace(a, note="第二版"))
        self.assertEqual(store.path, fallback)
        self.assertTrue(S.backup_path(fallback).is_file())
        self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), orphan)  # 第一次写之前已另存
        self.assertFalse(self.bak.exists())


# ---------------------------------------------------------------- 写没写成


class SavedTest(_Case):
    def test_saved_after_each_change(self):
        """改动即存：写进去了、内容没变，save 都返回 True，saved 为真。"""
        store = self.open()
        self.assertTrue(store.saved)
        a = store.add(scheme())
        self.assertTrue(store.saved)
        self.assertTrue(store.save())
        store.update(a)  # 内容没变
        self.assertTrue(store.saved)

    def test_write_fails(self):
        """写不进去（改存也不成）：save 返回 False，saved 为假、save_error 有值；内存里的改动照旧有效；好了以后照常。"""
        store = self.open()
        store.add(scheme())
        with mock.patch.object(fileio, "write_bytes", side_effect=OSError(28, "磁盘已满")), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            b = store.add(scheme(name="第二套"))
            self.assertFalse(store.save())
        self.assertFalse(store.saved)
        self.assertIsInstance(store.save_error, OSError)
        self.assertEqual(len(store.schemes()), 2)
        self.assertEqual(len(self.open().schemes()), 1)
        store.update(replace(b, note="好了"))
        self.assertTrue(store.saved)
        self.assertEqual(len(self.open().schemes()), 2)

    def test_read_only(self):
        """来自更新版本（只读）：save 返回 False，saved 为假（不能改，不是「已保存」）。"""
        self.path.write_text(json.dumps({"version": S.FILE_VERSION + 1, "schemes": [], "last_used": {}}), encoding="utf-8")
        store = self.open()
        self.assertTrue(store.read_only)
        self.assertEqual((store.save(), store.saved, store.blocked), (False, False, False))

    def test_flushed_to_disk(self):
        """写的时候刷进硬盘（os.fsync）再替换。"""
        with mock.patch.object(os, "fsync", wraps=os.fsync) as sync:
            self.open().add(scheme())
        self.assertGreaterEqual(sync.call_count, 1)


# ---------------------------------------------------------------- 读坏了


class BrokenTest(_Case):
    def test_not_set_aside_blocks_writing(self):
        """读坏了、坏文件没能另存、也没有备份：提示读坏了（另存的文件名是空的），这次运行不写方案文件（blocked），save 返回
        False、saved 为假，坏文件原样留着。"""
        self.path.write_bytes(b"\0" * 30)
        with mock.patch.object(fileio, "move_aside", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_BROKEN, "schemes.json"))
        self.assertEqual((store.blocked, store.saved), (True, False))
        store.add(scheme())
        self.assertFalse(store.save())
        self.assertEqual(self.path.read_bytes(), b"\0" * 30)
        self.assertEqual(self.names(), ["schemes.json"])

    def test_not_kept_with_backup_blocks_writing(self):
        """读坏了、坏文件没能留一份、备份能用：照备份列出方案（提示改用了备份，另存的文件名是空的），这次运行不写，坏文件和备份
        都不动。"""
        good = self.saved_file()
        self.bak.write_bytes(good)
        self.path.write_bytes(b"{")
        with mock.patch.object(fileio, "_keep_copy", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak"))
        self.assertEqual([s.name for s in store.schemes()], ["备份里的1"])
        self.assertEqual((store.blocked, store.saved), (True, False))
        store.add(scheme())
        self.assertFalse(store.save())
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (b"{", good))

    def test_restore_failed_keeps_good_backup(self):
        """读坏了、用备份恢复方案文件没成（坏文件已经留了一份）：照备份列出方案，save_error 有值、saved 为假（还没写进文件），
        但不是 blocked；下次保存照常写，坏内容不拿去盖掉 .bak。"""
        good = self.saved_file()
        self.bak.write_bytes(good)
        self.path.write_bytes(b"{")
        with mock.patch.object(fileio, "write_bytes", side_effect=OSError(28, "磁盘已满")), \
                self.assertLogs("advisor", level="WARNING"):
            store = self.open()
        self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_USED_BACKUP, "schemes.json", "schemes.json.bak",
                                                    f"schemes.json.broken-{STAMP}"))
        self.assertEqual((store.blocked, store.saved), (False, False))
        self.assertIsInstance(store.save_error, OSError)
        self.assertEqual(self.path.read_bytes(), b"{")
        with self.assertLogs("advisor.fileio", level="WARNING"):  # 原来的内容读不出，不拿它盖掉备份
            store.add(scheme())
        self.assertEqual((store.saved, store.save_error), (True, None))
        self.assertEqual([s.name for s in self.open().schemes()], ["备份里的1", "治愈坦克"])
        self.assertEqual(self.bak.read_bytes(), good)
        self.assertEqual((self.dir / f"schemes.json.broken-{STAMP}").read_bytes(), b"{")

    def test_backup_not_usable_kept_like_orphan(self):
        """读坏了、坏文件已另存走，备份却没能用上而还在原处（读的时候被占用；或者也读坏了、又没能另存）：提示读坏了、从空的
        开始；备份按只剩备份处理，第一次保存前另存为 .bak.orphan-时间，之后的保存不会把它盖掉（备份里可能是还能用的方案）。"""
        good = self.saved_file(2)
        real_read, real_move = fileio._read, fileio.move_aside

        def bak_locked(path):
            if path == self.bak:
                raise PermissionError(13, "拒绝访问")
            return real_read(path)

        def bak_stays(path, tag=fileio.BROKEN, **kw):
            if path == self.bak:
                raise PermissionError(13, "拒绝访问")
            return real_move(path, tag, **kw)

        cases = {"备份读不了": (good, mock.patch.object(fileio, "_read", bak_locked)),
                 "备份也读坏了、没能另存": (b"\0" * 25, mock.patch.object(fileio, "move_aside", bak_stays))}
        for label, (backup, patcher) in cases.items():
            with self.subTest(label=label):
                for p in self.dir.iterdir():
                    p.unlink()
                self.bak.write_bytes(backup)
                self.path.write_bytes(b"{")
                with patcher, self.assertLogs("advisor", level="WARNING"):
                    store = self.open()
                self.assertEqual(store.notice, S.LoadNotice(S.NOTICE_BROKEN, "schemes.json",
                                                            broken=f"schemes.json.broken-{STAMP}"))
                self.assertEqual((store.schemes(), store.blocked), ([], False))
                with self.assertLogs("advisor.schemes", level="WARNING"):
                    a = store.add(scheme())
                store.update(replace(a, note="第二版"))
                store.update(replace(a, note="第三版"))
                self.assertEqual((self.dir / ORPHAN_NAME).read_bytes(), backup)  # 原来的备份留着，没被盖掉
                self.assertEqual((self.dir / f"schemes.json.broken-{STAMP}").read_bytes(), b"{")
                self.assertEqual([s.note for s in self.open().schemes()], ["第三版"])

    def test_externally_broken_file_does_not_replace_backup(self):
        """运行中方案文件被外面改坏了：下次保存照常写好，坏内容不拿去盖掉 .bak（.bak 留着上一份还能用的）。"""
        store = self.open()
        a = store.add(scheme())
        store.update(replace(a, note="第二版"))
        bak = self.bak.read_bytes()
        for i, broken in enumerate((b"\0" * 20, b'{"version": 1}')):  # 断电后的零字节（不记警告）、改成了旧格式
            with self.subTest(broken=broken):
                self.path.write_bytes(broken)
                with mock.patch.object(fileio.log, "warning") as warned:
                    store.update(replace(a, note=f"第{i + 3}版"))
                self.assertEqual(warned.call_count, i)
                self.assertEqual(self.bak.read_bytes(), bak)
                self.assertEqual([s.note for s in self.open().schemes()], [f"第{i + 3}版"])


# ---------------------------------------------------------------- 适用地图


class MapStoreTest(_Case):
    def raw(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, raw: dict) -> None:
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def test_maps_saved_and_read_back(self):
        """带地图存了再读回来一致：文件里按编号排、同时记编号和名称；读时先认编号，再认名称。"""
        store = self.open()
        store.add(scheme(maps=("龙宫游乐园", "魔法学院")))
        raw = self.raw()
        self.assertEqual(raw["schemes"][0]["maps"], [{"code": 1, "name": "龙宫游乐园"},
                                                     {"code": 5, "name": "魔法学院"}])
        self.assertEqual(self.open().schemes()[0].maps, ("龙宫游乐园", "魔法学院"))
        # 编号认得、名称是改名前或繁体写法时按编号认；编号不认识时按名称认
        raw["schemes"][0]["maps"] = [{"code": 5, "name": "旧名字"}, {"code": 99, "name": "园林中庭"}]
        self.write(raw)
        self.assertEqual(self.open().schemes()[0].maps, ("园林中庭", "魔法学院"))

    def test_missing_maps_is_generic(self):
        """没有 "maps" 这个键：算全地图通用，文件不当坏文件。"""
        self.open().add(scheme())
        raw = self.raw()
        del raw["schemes"][0]["maps"]
        self.write(raw)
        reopened = self.open()
        self.assertIsNone(reopened.notice)
        self.assertEqual(reopened.schemes()[0].maps, ())

    def test_bad_maps_is_generic_not_broken(self):
        """maps 的类型不对：这一套算通用，文件不当坏文件，别的方案照常；改了再存时写成空列表。"""
        store = self.open()
        store.add(scheme(name="第一套"))
        store.add(scheme(name="第二套", maps=("魔法学院",)))
        for i, bad in enumerate(("x", [1], [{"code": "1", "name": "龙宫游乐园"}],
                                 [{"code": 1, "name": "龙宫游乐园"}, 3])):
            with self.subTest(bad=bad):
                raw = self.raw()
                raw["schemes"][0]["maps"] = bad
                self.write(raw)
                reopened = self.open()
                self.assertIsNone(reopened.notice)
                self.assertEqual([s.name for s in reopened.schemes()], ["第一套", "第二套"])
                self.assertEqual([s.maps for s in reopened.schemes()], [(), ("魔法学院",)])
                first = next(s for s in reopened.schemes() if s.name == "第一套")
                reopened.update(replace(first, note=f"改过{i}"))
                self.assertEqual(self.raw()["schemes"][0]["maps"], [])

    def test_unknown_map_rows_kept(self):
        """认不出的地图行（编号、名称本版本都不认识）不参与计算；改了这套方案再存时原样留在认出的几行后面。"""
        store = self.open()
        store.add(scheme(maps=("龙宫游乐园",)))
        raw = self.raw()
        raw["schemes"][0]["maps"] += [{"code": 40, "name": "未来的地图"},
                                      {"code": 41, "name": "另一张", "extra": [1, 2]}]
        self.write(raw)
        reopened = self.open()
        first = reopened.schemes()[0]
        self.assertEqual(first.maps, ("龙宫游乐园",))
        reopened.update(replace(first, note="改过"))
        self.assertEqual(self.raw()["schemes"][0]["maps"],
                         [{"code": 1, "name": "龙宫游乐园"}, {"code": 40, "name": "未来的地图"},
                          {"code": 41, "name": "另一张", "extra": [1, 2]}])

    def test_unchanged_entry_written_as_is(self):
        """没改过的方案原样写回：认出的地图行上本版本不认识的字段、认不出的行都照原样。"""
        store = self.open()
        store.add(scheme(maps=("龙宫游乐园",)))
        raw = self.raw()
        raw["schemes"][0]["maps"] = [{"code": 1, "name": "龙宫游乐园", "extra": True},
                                     {"code": 40, "name": "未来的地图"}]
        self.write(raw)
        before = raw["schemes"][0]
        reopened = self.open()
        reopened.add(scheme(name="第二套"))
        self.assertEqual(self.raw()["schemes"][0], before)


if __name__ == "__main__":
    unittest.main()
