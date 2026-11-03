"""诊断包的写盘加固和脱敏（advisor/diagnostics.py）：先在 .partial 里写、写完再改名，写一半出错留 .partial 并抛
ExportError；自动保存的包有标记、按份数只留最近 20 份（手动导出的、没有标记的、链接过去的、名字不对的一个都不删，
文件被占用的那一份整份留着）；另带的三选一读不全时的那一帧、记下选中项用的那一帧、被临时盖住而没有采用的那一帧（文件名带
时刻、state.json 记一笔，没有时不多写）；另带换版本的记录 update.log（有才带，读不了的记一笔）；路径脱敏到
%USERPROFILE%/%APPDATA%/%LOCALAPPDATA%/%USERNAME%（只换完整的路径段，不碰 JSON 的转义）；自检的必备数据文件；
「系统」一行不查 WMI，WMI 卡住时导出照常做完。临时文件放在 .tmp 下。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from advisor import diagnostics, fileio, paths, sysinfo

try:
    import _winapi
except ImportError:  # 不是 Windows：目录联接的几条跳过
    _winapi = None


def tmpdir(name: str) -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-diagnostics-reports"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base, prefix=f"{name}-")


class ExportStagingTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir("reports")
        self.reports = Path(self._dir.name)
        self._home = tmpdir("settings")
        self.settings = Path(self._home.name)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._home.cleanup)

    def test_writes_then_renames_to_a_final_name(self):
        out = diagnostics.export(None, {"hero": "凛"}, [], "")
        self.assertEqual(out.parent, self.reports)
        self.assertTrue(out.is_dir())
        self.assertIsNotNone(diagnostics.REPORT_NAME.match(out.name))
        self.assertFalse([p for p in self.reports.iterdir() if fileio.PARTIAL_SUFFIX in p.name])
        self.assertEqual([p.name for p in diagnostics.report_dirs()], [out.name])

    def test_snapshot_direct_chips_written(self):
        """诊断包的 state.json 能看出哪几个是补录（不经三选一获得）（Session.snapshot 的来源写 direct）。"""
        out = diagnostics.export(None, {"chip_sources": {"书签": "direct"}}, [], "")
        state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
        self.assertEqual(state["chip_sources"], {"书签": "direct"})

    def test_partial_is_left_when_writing_fails(self):
        """写到一半出错：正式名字的目录没建出来，.partial 留着，ExportError 给出它的位置。"""

        def boom(path, records):
            raise OSError(28, "磁盘满")

        with mock.patch.object(diagnostics, "_write_jsonl", side_effect=boom):
            with self.assertRaises(diagnostics.ExportError) as ctx:
                diagnostics.export(None, {}, [], "")
        err = ctx.exception
        self.assertIsNotNone(err.partial)
        self.assertTrue(err.partial.is_dir())
        self.assertIn(fileio.PARTIAL_SUFFIX, err.partial.name)
        self.assertEqual(diagnostics.report_dirs(), [])  # 正式产物里没有残缺的
        self.assertEqual([p.name for p in self.reports.iterdir()], [err.partial.name])

    def test_auto_marker_only_when_asked(self):
        auto = diagnostics.export(None, {}, [], "", auto=True)
        self.assertTrue((auto / diagnostics.AUTO_MARK).is_file())
        manual = diagnostics.export(None, {}, [], "")
        self.assertFalse((manual / diagnostics.AUTO_MARK).is_file())

    def test_extra_name_cannot_smuggle_the_marker(self):
        """另带的文件名不能是自动保存的标记，也不能是 Windows 会当成它的写法（大小写不同、末尾带点或空格）：
        手动导出的包里不会混进标记、被当成自动保存的删掉。"""
        for name in (diagnostics.AUTO_MARK, diagnostics.AUTO_MARK.upper(), diagnostics.AUTO_MARK + ".",
                     diagnostics.AUTO_MARK + " ", diagnostics.AUTO_MARK + " ."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostics.export(None, {}, [], "", extra={name: []})
        self.assertEqual(list(self.reports.iterdir()), [])


class UpdateLogTest(unittest.TestCase):
    """诊断包带上换版本的记录（程序目录 logs 里的 update.log，软件内更新换版本以后收尾时写）：写法同设置和方案文件——有才带，
    文字里的用户主目录照样换成 %USERPROFILE%、换行不变；没有时不带、不算问题；读不了、过大时不带，原因记在 state.json 的
    settings_files_error；另带的文件名不能用它。程序目录的 logs 换成临时文件夹。"""

    def setUp(self):
        dirs = [tmpdir(name) for name in ("reports", "settings", "logs")]
        for d in dirs:
            self.addCleanup(d.cleanup)
        self.reports, self.settings, self.logs = (Path(d.name) for d in dirs)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json"),
                        mock.patch.object(paths, "logs_dir", return_value=self.logs)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def state(out: Path) -> dict:
        return json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))

    def test_same_file_as_update_apply(self):
        """收的就是换版本收尾时写的那个文件（advisor/update_apply.py 的 KEPT_LOG，在程序目录的 logs 里）。"""
        from advisor import update_apply

        self.assertEqual(diagnostics.UPDATE_LOG, update_apply.KEPT_LOG)
        self.assertIn(diagnostics.UPDATE_LOG, diagnostics.RESERVED)

    def test_carried(self):
        home = os.path.expanduser("~")
        text = (f"[2030-01-20 10:00:00] 换版本程序：开始\r\n[2030-01-20 10:00:01] 程序文件夹 {home}\\拾星助手\r\n"
                "[2030-01-20 10:00:02] 结果：ok\n")
        (self.logs / "update.log").write_bytes(text.encode("utf-8"))
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["advice.txt", "events.jsonl", "state.json", "update.log", "version.txt"])
        self.assertEqual((out / "update.log").read_bytes(), text.replace(home, "%USERPROFILE%").encode("utf-8"))
        self.assertNotIn(home.lower(), (out / "update.log").read_text(encoding="utf-8").lower())
        self.assertNotIn("settings_files_error", self.state(out))
        (self.logs / "update.log").write_bytes(b"\xff\xfe not utf-8")  # 读坏的：原样带上
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual((out / "update.log").read_bytes(), b"\xff\xfe not utf-8")

    def test_missing(self):
        """没有换版本的记录（从没换过版本、开发版）：不带，不算问题，导出照常。"""
        out = diagnostics.export(None, {}, [], "")
        self.assertNotIn("update.log", [p.name for p in out.iterdir()])
        self.assertNotIn("settings_files_error", self.state(out))
        with mock.patch.object(paths, "logs_dir", return_value=self.logs / "none"):  # 连 logs 文件夹都没有
            out = diagnostics.export(None, {}, [], "")
        self.assertNotIn("update.log", [p.name for p in out.iterdir()])
        self.assertNotIn("settings_files_error", self.state(out))

    def test_unreadable_or_too_big(self):
        """读不了、过大：不带，原因记在 state.json，其余照常带。"""
        (self.logs / "update.log").write_bytes(b"[2030-01-20 10:00:00] x\n")
        (self.settings / "state.json").write_text('{"hero": "凛"}', encoding="utf-8")
        read = Path.read_bytes

        def locked(path: Path) -> bytes:
            if path.name == "update.log":
                raise PermissionError(13, "拒绝访问")
            return read(path)

        with mock.patch.object(Path, "read_bytes", locked):
            out = diagnostics.export(None, {}, [], "")
        names = [p.name for p in out.iterdir()]
        self.assertNotIn("update.log", names)
        self.assertIn("settings.json", names)
        errors = self.state(out)["settings_files_error"]
        self.assertEqual(list(errors), ["update.log"])
        self.assertTrue(errors["update.log"].startswith("PermissionError"), errors)
        (self.settings / "state.json").unlink()
        with mock.patch.object(diagnostics, "USER_FILE_MAX", 10):
            out = diagnostics.export(None, {}, [], "")
        self.assertNotIn("update.log", [p.name for p in out.iterdir()])
        self.assertIn("文件过大", self.state(out)["settings_files_error"]["update.log"])

    def test_extra_name_cannot_take_it(self):
        for name in ("update.log", "UPDATE.LOG", "update.log.", "update.log "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostics.export(None, {}, [], "", extra={name: []})
        self.assertEqual(list(self.reports.iterdir()), [])


class ZipReportTest(unittest.TestCase):
    """手动导出的诊断包旁边另打同名的压缩包（zip_report）：解开是同名的文件夹、文件一个不少、文件夹照旧留着；先写
    「名字.zip.partial」再改名；打的时候出错、改名不成时不留下 .zip 和 .partial，照原样报错；已经有同名的压缩包时不覆盖。
    压缩包不算诊断包（report_dirs 只认文件夹），按份数清理也不碰它。"""

    def setUp(self):
        self._dir = tmpdir("zip")
        self.reports = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        patcher = mock.patch.object(paths, "reports_dir", return_value=self.reports)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = self.reports / "20261024-120000"
        (self.folder / "sub").mkdir(parents=True)
        (self.folder / "state.json").write_text('{"hero": "凛"}', encoding="utf-8")
        (self.folder / "events.jsonl").write_bytes(b"{}\n" * 1000)
        (self.folder / "sub" / "说明.txt").write_text("中文名", encoding="utf-8")

    def test_zip_next_to_folder(self):
        import zipfile

        out = diagnostics.zip_report(self.folder)
        self.assertEqual(out, self.reports / "20261024-120000.zip")
        self.assertTrue(self.folder.is_dir())  # 文件夹照旧留着
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(sorted(zf.namelist()), ["20261024-120000/events.jsonl", "20261024-120000/state.json",
                                                     "20261024-120000/sub/说明.txt"])
            self.assertEqual(zf.read("20261024-120000/sub/说明.txt").decode("utf-8"), "中文名")
            self.assertEqual(zf.read("20261024-120000/events.jsonl"), b"{}\n" * 1000)
            self.assertEqual(zf.getinfo("20261024-120000/events.jsonl").compress_type, zipfile.ZIP_DEFLATED)
            self.assertIsNone(zf.testzip())
        self.assertFalse(list(self.reports.glob("*.partial")))
        self.assertEqual([p.name for p in diagnostics.report_dirs()], ["20261024-120000"])  # 压缩包不算诊断包
        with self.assertRaises(FileExistsError):  # 已经有同名的压缩包：不覆盖
            diagnostics.zip_report(self.folder)
        self.assertTrue(out.is_file())

    def test_failure_leaves_nothing(self):
        """打到一半出错（写不进）、改名一直被占用：不留下 .zip 和 .partial，照原样报错；文件夹照旧。"""
        import zipfile

        with mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError("磁盘空间不足")):
            with self.assertRaises(OSError):
                diagnostics.zip_report(self.folder)
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), ["20261024-120000"])
        with mock.patch.object(diagnostics, "PARTIAL_DELAYS", (0.0, 0.0)), \
                mock.patch.object(os, "rename", side_effect=PermissionError("被占用")) as rename:
            with self.assertRaises(PermissionError):
                diagnostics.zip_report(self.folder)
        self.assertEqual(rename.call_count, 3)  # 稍等再试，用完了照原样报
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), ["20261024-120000"])
        self.assertTrue((self.folder / "state.json").is_file())


class SelectUnreadableTest(unittest.TestCase):
    """诊断包另带最近一次三选一有筹码没读出来时的那一帧（export 的 select_unreadable）：有就另写一张 PNG，文件名里带当时的
    几点几分几秒，内容就是交来的那一帧，state.json 里记下时刻和文件名；没有时什么都不多写；编码出错时同 frame.png，原因记进
    state.json、其余照常；另带的记录不能用这个文件名开头。"""

    WHEN = datetime(2026, 1, 2, 12, 0, 1, 250000)

    def setUp(self):
        self._dir = tmpdir("reports")
        self.reports = Path(self._dir.name)
        self._home = tmpdir("settings")
        self.settings = Path(self._home.name)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._home.cleanup)

    @staticmethod
    def state(out: Path) -> dict:
        return json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))

    def test_written_with_time_in_name(self):
        image = np.zeros((24, 32, 3), np.uint8)
        image[4:8, 5:9] = (10, 200, 30)
        out = diagnostics.export(np.full((24, 32, 3), 90, np.uint8), {}, [], "", select_unreadable=(self.WHEN, image))
        name = "select-unreadable-120001.png"
        self.assertEqual(name, diagnostics.SELECT_UNREADABLE.format(time="120001"))
        self.assertTrue((out / diagnostics.FRAME).is_file())  # 当时的画面照旧
        got = cv2.imdecode(np.fromfile(str(out / name), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertTrue(np.array_equal(got, image))  # PNG 无损：就是交来的那一帧
        state = self.state(out)
        self.assertEqual(state["select_unreadable"], {"time": "2026-01-02T12:00:01.250", "file": name})
        self.assertNotIn("select_unreadable_error", state)

    def test_nothing_extra_without_it(self):
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith(diagnostics.SELECT_UNREADABLE_PREFIX)],
                         [])
        state = self.state(out)
        self.assertNotIn("select_unreadable", state)
        self.assertNotIn("select_unreadable_error", state)

    def test_encode_error_recorded(self):
        """编码不了（交来的不是画面）：原因记进 state.json，不写这张图，其余内容照常写好、目录照常改成正式名字。"""
        out = diagnostics.export(None, {"hero": "凛"}, [], "推荐", select_unreadable=(self.WHEN, "不是画面"))
        state = self.state(out)
        self.assertIn("select_unreadable_error", state)
        self.assertEqual(state["select_unreadable"], {"time": "2026-01-02T12:00:01.250"})  # 没写成：不记文件名
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith(diagnostics.SELECT_UNREADABLE_PREFIX)],
                         [])
        self.assertEqual(state["hero"], "凛")
        self.assertTrue((out / diagnostics.ADVICE).is_file())
        self.assertIsNotNone(diagnostics.REPORT_NAME.match(out.name))

    def test_extra_name_cannot_take_it(self):
        for name in ("select-unreadable-120001.png", "Select-Unreadable-x.jsonl"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostics.export(None, {}, [], "", extra={name: []})
        self.assertEqual(list(self.reports.iterdir()), [])


class SelectShotsTest(unittest.TestCase):
    """诊断包另带记下三选一选中项用的那一帧（select_picked）和被临时盖住、没有采用的那一帧（select_covered）：同
    select_unreadable，有就另写一张 PNG（文件名里带当时的几点几分几秒），state.json 记下时刻、那一次的情况和文件名；没有时什么都
    不多写；一张编码出错只记这一张的原因，其余照常；另带的记录不能用这两个文件名开头。"""

    WHEN = datetime(2026, 1, 2, 12, 0, 1, 250000)
    PICKED = {"slot": 1, "chip": "标靶", "watch": True, "region": [10, 20, 32, 24]}
    COVERED = {"slots": [2], "raw": ["会员推荐信", "标靶", "标记"], "names": ["会员推荐信", "标靶", "标记喷罐"],
               "rarity": ["蓝", "蓝", None], "selected": 2}

    def setUp(self):
        self._dir = tmpdir("reports")
        self.reports = Path(self._dir.name)
        self._home = tmpdir("settings")
        self.settings = Path(self._home.name)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._home.cleanup)

    @staticmethod
    def state(out: Path) -> dict:
        return json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))

    @staticmethod
    def image(value) -> np.ndarray:
        img = np.zeros((24, 32, 3), np.uint8)
        img[4:8, 5:9] = value
        return img

    def test_written_with_details(self):
        picked, covered = self.image((10, 200, 30)), self.image((200, 200, 200))
        out = diagnostics.export(None, {}, [], "", select_picked=(self.WHEN, picked, self.PICKED),
                                 select_covered=(self.WHEN, covered, self.COVERED))
        state = self.state(out)
        names = {"select_picked": "select-picked-120001.png", "select_covered": "select-covered-120001.png"}
        self.assertEqual(names["select_picked"], diagnostics.SELECT_PICKED.format(time="120001"))
        self.assertEqual(names["select_covered"], diagnostics.SELECT_COVERED.format(time="120001"))
        self.assertEqual(state["select_picked"], {"time": "2026-01-02T12:00:01.250", **self.PICKED,
                                                  "file": names["select_picked"]})
        self.assertEqual(state["select_covered"], {"time": "2026-01-02T12:00:01.250", **self.COVERED,
                                                   "file": names["select_covered"]})
        for key, image in (("select_picked", picked), ("select_covered", covered)):
            got = cv2.imdecode(np.fromfile(str(out / names[key]), dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertTrue(np.array_equal(got, image))  # PNG 无损：就是交来的那一帧
            self.assertNotIn(f"{key}_error", state)
        self.assertNotIn("select_unreadable", state)

    def test_nothing_extra_without_them(self):
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith(diagnostics.SHOT_PREFIXES)], [])
        state = self.state(out)
        for key in ("select_picked", "select_covered", "select_picked_error", "select_covered_error"):
            self.assertNotIn(key, state)

    def test_one_encode_error(self):
        """记下选中项的那一张编码不了：只记这一张的原因、不写它；不采用的那一张、其余内容照常写好。"""
        out = diagnostics.export(None, {"hero": "凛"}, [], "推荐", select_picked=(self.WHEN, "不是画面", self.PICKED),
                                 select_covered=(self.WHEN, self.image((1, 2, 3)), self.COVERED))
        state = self.state(out)
        self.assertIn("select_picked_error", state)
        self.assertNotIn("file", state["select_picked"])
        self.assertEqual(state["select_covered"]["file"], "select-covered-120001.png")
        self.assertTrue((out / "select-covered-120001.png").is_file())
        self.assertEqual(state["hero"], "凛")
        self.assertIsNotNone(diagnostics.REPORT_NAME.match(out.name))

    def test_details_cannot_override_time_or_file(self):
        """那一次的情况里有同名的 time、file：盖不掉时刻和文件名；那一张没写成时也不留下情况里的 file。"""
        details = {"slot": 1, "time": "不是时刻", "file": "不是文件名.png"}
        out = diagnostics.export(None, {}, [], "", select_picked=(self.WHEN, self.image((1, 2, 3)), details),
                                 select_covered=(self.WHEN, "不是画面", details))
        state = self.state(out)
        self.assertEqual(state["select_picked"], {"time": "2026-01-02T12:00:01.250", "slot": 1,
                                                  "file": "select-picked-120001.png"})
        self.assertTrue((out / "select-picked-120001.png").is_file())
        self.assertEqual(state["select_covered"], {"time": "2026-01-02T12:00:01.250", "slot": 1})
        self.assertIn("select_covered_error", state)
        self.assertFalse((out / "不是文件名.png").exists())

    def test_extra_name_cannot_take_them(self):
        for name in ("select-picked-120001.png", "Select-Covered-x.jsonl", "SELECT-PICKED-1.jsonl"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                diagnostics.export(None, {}, [], "", extra={name: []})
        self.assertEqual(list(self.reports.iterdir()), [])


class PruneTest(unittest.TestCase):
    def setUp(self):
        self._dir = tmpdir("reports")
        self.reports = Path(self._dir.name)
        patcher = mock.patch.object(paths, "reports_dir", return_value=self.reports)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def make(self, name: str, auto: bool = False) -> Path:
        folder = self.reports / name
        folder.mkdir()
        if auto:
            (folder / diagnostics.AUTO_MARK).write_text("x", encoding="utf-8")
        return folder

    def test_report_dirs_skips_partial(self):
        self.make("20260101-000000")
        (self.reports / "20260101-000001.partial").mkdir()
        (self.reports / "不是诊断包").mkdir()
        self.assertEqual([p.name for p in diagnostics.report_dirs()], ["20260101-000000"])

    def test_prune_keeps_only_recent_autos(self):
        autos = [self.make(f"20260101-{i:06d}", auto=True) for i in range(22)]
        manual = [self.make(f"20251231-{i:06d}") for i in range(3)]  # 手动导出：没有标记，永远不删
        partial = self.reports / "20260102-000000.partial"
        partial.mkdir()
        self.assertEqual(len(diagnostics.report_dirs()), 25)  # .partial 不算
        removed = diagnostics.prune_auto_reports(keep=20)
        self.assertEqual([p.name for p in removed], [a.name for a in autos[:2]])  # 只删最旧的 2 份自动包
        left = {p.name for p in self.reports.iterdir()}
        for a in autos[2:]:
            self.assertIn(a.name, left)
        for m in manual:
            self.assertIn(m.name, left)
        self.assertIn(partial.name, left)

    def test_prune_nothing_to_do(self):
        for i in range(diagnostics.KEEP_AUTO):
            self.make(f"20260101-{i:06d}", auto=True)
        self.assertEqual(diagnostics.prune_auto_reports(), [])
        self.assertEqual(len(diagnostics.report_dirs()), diagnostics.KEEP_AUTO)


class PruneSafetyTest(unittest.TestCase):
    """清理只删 reports 下名字是时间、带自动保存标记、不是链接的文件夹；删不成的那一份整份留着，不报错。"""

    FILES = ("advice.txt", "events.jsonl", "state.json", "version.txt")

    def setUp(self):
        self._dir = tmpdir("prune")
        self.base = Path(self._dir.name)
        self.reports = self.base / "reports"
        self.reports.mkdir()
        patcher = mock.patch.object(paths, "reports_dir", return_value=self.reports)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def make(self, name: str, auto: bool = True) -> Path:
        folder = self.reports / name
        folder.mkdir()
        for f in self.FILES:
            (folder / f).write_text(f, encoding="utf-8")
        if auto:
            (folder / diagnostics.AUTO_MARK).write_text(diagnostics.AUTO_NOTE, encoding="utf-8")
        return folder

    @staticmethod
    def names(folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir())

    @unittest.skipIf(_winapi is None, "目录联接只在 Windows 上有")
    def test_only_marked_report_folders_are_deleted(self):
        """手动导出的、名字不是时间的、写到一半的、同名的文件、指向别处的目录联接（带着标记也算）一个都不删；
        要删的那份里指向外面的联接只拿掉联接本身，外面的东西不动。"""
        outside = self.base / "outside"  # reports 以外，只能经联接摸到
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        (outside / diagnostics.AUTO_MARK).write_text("x", encoding="utf-8")
        linked = self.reports / "20250101-000000"  # 名字最旧、指向 reports 以外的联接
        _winapi.CreateJunction(str(outside), str(linked))
        inner = self.base / "inner"
        inner.mkdir()
        (inner / "keep.txt").write_text("keep", encoding="utf-8")
        autos = [self.make(f"20260101-{i:06d}") for i in range(21)]
        _winapi.CreateJunction(str(inner), str(autos[0] / "link"))
        manual = self.make("20240101-000000", auto=False)
        partial = self.make("20240102-000000.partial")
        misnamed = self.make("我的诊断包")
        (self.reports / "20240103-000000").write_text("x", encoding="utf-8")

        self.assertEqual(diagnostics.prune_auto_reports(keep=20), [autos[0]])

        self.assertFalse(os.path.lexists(autos[0]))
        for folder in autos[1:]:
            self.assertEqual(self.names(folder), sorted((*self.FILES, diagnostics.AUTO_MARK)))
        self.assertTrue(os.path.lexists(linked))
        self.assertEqual(self.names(outside), sorted(("keep.txt", diagnostics.AUTO_MARK)))
        self.assertEqual(self.names(inner), ["keep.txt"])
        self.assertEqual(self.names(manual), sorted(self.FILES))
        for folder in (partial, misnamed):
            self.assertIn(diagnostics.AUTO_MARK, self.names(folder))
        self.assertTrue((self.reports / "20240103-000000").is_file())
        self.assertNotIn(linked, diagnostics.report_dirs())  # 联接不算诊断包

    @unittest.skipUnless(os.name == "nt", "Windows 上开着的文件删不掉")
    def test_open_file_keeps_that_package_whole(self):
        """要删的那份里有文件正被别的程序开着：这一份整份留着（标记也在，下次清理再删），其余照删，不报错。"""
        autos = [self.make(f"20260101-{i:06d}") for i in range(22)]
        with open(autos[0] / "events.jsonl", "rb"):
            self.assertEqual(diagnostics.prune_auto_reports(keep=20), [autos[1]])
            self.assertEqual(self.names(autos[0]), sorted((*self.FILES, diagnostics.AUTO_MARK)))
        self.assertEqual(diagnostics.prune_auto_reports(keep=20), [autos[0]])
        self.assertEqual(self.names(self.reports), [a.name for a in autos[2:]])  # 没有留下改了名的残余

    def test_spared_package_is_never_deleted(self):
        """系统时间被调回过时，刚存的那份名字最旧：传给 spare 的不管排第几都不删，照样算在 keep 份里，多出来的从其余
        最旧的删起。spare 可以是一个路径（Path 或文字），也可以是几个；不传时刚存的那份会被当成最旧的删掉。"""
        future = [self.make(f"20300101-{i:06d}") for i in range(20)]  # 时间被调回以前存的，名字里的时间更晚
        just = self.make("20260101-000000")
        self.assertEqual(diagnostics.prune_auto_reports(keep=20, spare=just), [future[0]])
        self.assertTrue(just.is_dir())
        self.assertEqual(len(diagnostics.report_dirs()), 20)
        newer = self.make("20260101-000001")
        self.assertEqual(diagnostics.prune_auto_reports(keep=20, spare=[str(just), newer]), [future[1]])
        self.assertTrue(just.is_dir() and newer.is_dir())
        self.assertEqual(diagnostics.prune_auto_reports(keep=19), [just])

    def test_same_second_names_sorted_by_number(self):
        """同一秒的几份（-2……-11）按序号排先后：-10 比 -9 新。"""
        names = ["20260101-000000"] + [f"20260101-000000-{n}" for n in range(2, 12)]
        for name in names:
            self.make(name)
        self.assertEqual([p.name for p in diagnostics.report_dirs()], names)
        self.assertEqual([p.name for p in diagnostics.prune_auto_reports(keep=9)], names[:2])


class ScrubTest(unittest.TestCase):
    def test_redirected_profile_dirs_and_account_segment(self):
        account = "真名用户"
        env = {"USERPROFILE": f"C:\\Users\\{account}", "APPDATA": f"D:\\Users\\{account}\\AppData\\Roaming",
               "LOCALAPPDATA": f"D:\\Users\\{account}\\AppData\\Local", "USERNAME": account}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(diagnostics.scrub_paths(env["APPDATA"] + "\\x.json"), "%APPDATA%\\x.json")
            self.assertEqual(diagnostics.scrub_paths(env["LOCALAPPDATA"] + "\\y"), "%LOCALAPPDATA%\\y")
            self.assertEqual(diagnostics.scrub_paths(env["USERPROFILE"] + "\\z"), "%USERPROFILE%\\z")
            escaped = env["APPDATA"].replace("\\", "\\\\")  # JSON 里的转义写法
            self.assertEqual(diagnostics.scrub_paths(escaped + "\\\\x"), "%APPDATA%\\\\x")
            # 账户名作为完整的路径段（个人目录不在 USERPROFILE 下面）
            self.assertEqual(diagnostics.scrub_paths("\\\\server\\profiles$\\" + account + "\\x"),
                             "\\\\server\\profiles$\\%USERNAME%\\x")
            # 账户名出现在普通文字里（不是路径段）不动
            self.assertEqual(diagnostics.scrub_paths("名字" + account + "的一段"), "名字" + account + "的一段")


class ScrubEdgeTest(unittest.TestCase):
    """脱敏只换完整的路径段：账户名很短、是常见词时不误伤正文；大小写、正反斜杠、\\\\?\\ 前缀、个人目录在别的盘或
    网络位置时都对；换过的不再换第二次；JSON 文字里的转义（\\n、\\t……）不当成路径分隔符。"""

    @staticmethod
    def env(account: str, profile: str | None = None, appdata: str | None = None, local: str | None = None):
        profile = profile or f"C:\\Users\\{account}"
        return mock.patch.dict(os.environ, {
            "USERNAME": account, "USERPROFILE": profile,
            "APPDATA": appdata or profile + "\\AppData\\Roaming", "LOCALAPPDATA": local or profile + "\\AppData\\Local"})

    def test_short_account_only_whole_segments(self):
        scrub = diagnostics.scrub_paths
        with self.env("a"):
            for text in ("a", "banana", "a/b", "x\\ab\\y", "x/ba/y", "第 a 轮", "1/2a/3", "D:\\data\\a.b\\x"):
                with self.subTest(text=text):
                    self.assertEqual(scrub(text), text)
            self.assertEqual(scrub("D:\\data\\a\\x"), "D:\\data\\%USERNAME%\\x")
            self.assertEqual(scrub("D:/data/A/x"), "D:/data/%USERNAME%/x")

    def test_common_word_account(self):
        scrub = diagnostics.scrub_paths
        with self.env("user", profile="C:\\Users\\user"):
            for text in ("username\\x", "D:\\users\\x", "D:\\superuser\\x", "记录了 user 的操作", "user/"):
                with self.subTest(text=text):
                    self.assertEqual(scrub(text), text)
            self.assertEqual(scrub("D:\\Games\\User\\x"), "D:\\Games\\%USERNAME%\\x")

    def test_case_slashes_prefix_and_redirected_profile(self):
        scrub = diagnostics.scrub_paths
        roaming = "D:\\Profiles\\Real Name\\AppData\\Roaming"
        with self.env("Real Name", appdata=roaming, local="D:\\Profiles\\Real Name\\AppData\\Local"):
            self.assertEqual(scrub("d:/profiles/real name/appdata/roaming/AstralChipCompass/state.json"),
                             "%APPDATA%/AstralChipCompass/state.json")
            self.assertEqual(scrub("D:\\PROFILES\\REAL NAME\\APPDATA\\LOCAL\\x"), "%LOCALAPPDATA%\\x")
            self.assertEqual(scrub("\\\\?\\" + roaming + "\\x"), "\\\\?\\%APPDATA%\\x")
            self.assertEqual(scrub("E:\\Backup\\real NAME\\x"), "E:\\Backup\\%USERNAME%\\x")
            self.assertEqual(json.loads(scrub(json.dumps({"p": roaming + "\\x"}))), {"p": "%APPDATA%\\x"})
        unc = "\\\\server\\profiles$\\Real Name\\AppData\\Roaming"
        with self.env("Real Name", appdata=unc):
            for text in (unc + "\\x", "\\\\?\\UNC\\server\\profiles$\\Real Name\\AppData\\Roaming\\x",
                         json.dumps({"p": unc + "\\x"})):
                with self.subTest(text=text):
                    self.assertNotIn("real name", scrub(text).lower())

    def test_environ_argument(self):
        """给了 environ 时按那一份环境变量换，不看本进程的（打包试启动时核对自检写出的结果用）；那一份里没有 USERPROFILE
        时按 HOMEDRIVE + HOMEPATH 认主目录（同 Windows 上的 expanduser）。"""
        scrub = diagnostics.scrub_paths
        other = {"APPDATA": "X:\\Smoke\\Roaming", "LOCALAPPDATA": "X:\\Smoke\\Local", "USERPROFILE": "X:\\Home\\bob",
                 "USERNAME": "bob"}
        with self.env("alice"):
            mine = "C:\\Users\\alice\\AppData\\Roaming\\B"
            self.assertEqual(scrub("X:\\Smoke\\Roaming\\A;" + mine + ";D:\\bob\\C", environ=other),
                             "%APPDATA%\\A;" + mine + ";D:\\%USERNAME%\\C")
            self.assertEqual(scrub("X:\\Smoke\\Roaming\\A;" + mine), "X:\\Smoke\\Roaming\\A;%APPDATA%\\B")
            self.assertEqual(scrub("H:\\Users\\bob\\x", environ={"HOMEDRIVE": "H:", "HOMEPATH": "\\Users\\bob"}),
                             "%USERPROFILE%\\x")

    def test_each_path_replaced_once(self):
        scrub = diagnostics.scrub_paths
        with self.env("bob"):
            text = "C:\\Users\\bob\\AppData\\Roaming\\A;C:\\Users\\bob\\AppData\\Local\\B;C:\\Users\\bob\\C;D:\\bob\\E"
            want = "%APPDATA%\\A;%LOCALAPPDATA%\\B;%USERPROFILE%\\C;D:\\%USERNAME%\\E"
            self.assertEqual(scrub(text), want)
            self.assertEqual(scrub(want), want)  # 换过的再换一次不变

    def test_crash_record_escapes(self):
        """崩溃记录（faulthandler 写的）里非 ASCII 字符写成 \\uXXXX：用户目录、账户名这种写法也换；换过的再换一次不变。"""
        scrub = diagnostics.scrub_paths
        with self.env("张三"):
            record = ('  File "C:\\Users\\\\u5f20\\u4e09\\AppData\\Local\\Python\\Lib\\threading.py", line 1 in run\n'
                      '  File "C:\\Users\\\\u5f20\\u4e09\\Desktop\\app\\x.py", line 2 in main\n'
                      '  File "\\\\server\\profiles$\\\\u5f20\\u4e09\\y.py", line 3 in f\n'
                      '  File "D:\\games\\\\u5f20\\u4e09x\\z.py", line 4 in g\n')
            want = ('  File "%LOCALAPPDATA%\\Python\\Lib\\threading.py", line 1 in run\n'
                    '  File "%USERPROFILE%\\Desktop\\app\\x.py", line 2 in main\n'
                    '  File "\\\\server\\profiles$\\%USERNAME%\\y.py", line 3 in f\n'
                    '  File "D:\\games\\\\u5f20\\u4e09x\\z.py", line 4 in g\n')  # 只是名字开头相同的段不动
            self.assertEqual(scrub(record), want)
            self.assertEqual(scrub(want), want)

    def test_json_escapes_are_not_path_separators(self):
        """账户名只有一个字母、又正好是 JSON 的转义字母（n、t、r、b、f，不分大小写）时，转义（\\n\\n 等）不能被当成
        「\\账户名\\」换掉，不然 state.json、识别记录就读不出来了；路径里成对的「\\\\」照样换。"""
        scrub = diagnostics.scrub_paths
        for account in ("n", "N", "t", "r", "b", "f"):
            record = {"note": "第一行\n\n第二行\t\t\r\n\b\b\f\f", "quote": "\n\"引号\"\n\\",
                      "path": f"D:\\Profiles\\{account}\\x"}
            with self.subTest(account=account), self.env(account):
                out = json.loads(scrub(json.dumps(record, ensure_ascii=False)))
                self.assertEqual(out["note"], record["note"])
                self.assertEqual(out["quote"], record["quote"])
                self.assertEqual(out["path"], "D:\\Profiles\\%USERNAME%\\x")
                self.assertEqual(scrub(f"D:/Profiles/{account}/x"), "D:/Profiles/%USERNAME%/x")


class RequiredDataTest(unittest.TestCase):
    def test_covers_the_new_required_files(self):
        for name in ("assets_manifest.json", "meta.json", "mutators.json"):
            self.assertIn(name, diagnostics.REQUIRED_DATA)


class StuckWmi:
    """假的 WMI（platform 模块查系统信息用的 _wmi）：一调用就一直等着（WMI 坏了、卡住的电脑上就是这样），直到测试放开；
    放开后当作查询失败。calls 记下被调用了几次。"""

    def __init__(self):
        self.gate = threading.Event()
        self.calls: list[str] = []

    def exec_query(self, query: str) -> str:
        self.calls.append(query)
        self.gate.wait(60)
        raise OSError("测试里的假 WMI")


def _no_ver(system="", release="", version="", *_args, **_kw):
    """platform 查不到 WMI 时改用「ver」命令（起一个子进程）：换成什么都不返回的，测试里不起子进程。"""
    return system, release, version


class SystemLineTest(unittest.TestCase):
    """诊断包 version.txt 的「系统」一行（system_text）不查 WMI：写法同 platform.platform()（Windows-11-10.0.26200-SP0），
    WMI 一直不返回时导出诊断照常在几秒内做完。WMI 换成假的（StuckWmi），不碰真的系统服务；platform 的缓存测试前后都清掉。"""

    def test_same_wording_as_platform(self):
        """版本号按 platform 模块的同一张表换成名字：工作站版 22000 起是 11，以前是 10；服务器版按各自的起点。取不到时写
        「未知」。"""
        cases = (
            ((10, 0, 26200), 1, "Windows-11-10.0.26200-SP0"),
            ((10, 0, 22000), 1, "Windows-11-10.0.22000-SP0"),
            ((10, 0, 19045), 1, "Windows-10-10.0.19045-SP0"),
            ((10, 0, 17763), 1, "Windows-10-10.0.17763-SP0"),
            ((10, 0, 26100), 3, "Windows-2025Server-10.0.26100-SP0"),
            ((10, 0, 20348), 2, "Windows-2022Server-10.0.20348-SP0"),
            ((10, 0, 17763), 3, "Windows-2019Server-10.0.17763-SP0"),
            ((10, 0, 14393), 3, "Windows-2016Server-10.0.14393-SP0"),
        )
        for (major, minor, build), product, want in cases:
            fake = SimpleNamespace(major=major, minor=minor, build=build, product_type=product, service_pack_major=0)
            with self.subTest(want=want), \
                    mock.patch.object(diagnostics.sys, "getwindowsversion", return_value=fake, create=True):
                self.assertEqual(diagnostics.system_text(), want)
        with mock.patch.object(diagnostics.sys, "getwindowsversion", side_effect=OSError("取不到"), create=True):
            self.assertEqual(diagnostics.system_text(), diagnostics.SYSTEM_UNKNOWN)

    @unittest.skipUnless(sys.platform == "win32", "系统版本只在 Windows 上有")
    def test_export_does_not_wait_for_wmi(self):
        """WMI 卡住时导出诊断照常几秒内做完，version.txt 里照样有「系统」一行，假的 WMI 一次都没被问到。对照：同一个假 WMI
        下 platform.platform() 确实卡在它上面（这个假的拦得住改回 platform.platform() 的写法）。"""
        wmi = StuckWmi()
        reports, home = tmpdir("wmi-reports"), tmpdir("wmi-settings")
        self.addCleanup(reports.cleanup)
        self.addCleanup(home.cleanup)
        settings = Path(home.name)
        patches = [
            mock.patch.object(platform, "_wmi", wmi),
            mock.patch.object(platform, "_syscmd_ver", _no_ver),
            mock.patch.object(paths, "reports_dir", return_value=Path(reports.name)),
            mock.patch.object(paths, "state_path", return_value=settings / "state.json"),
            mock.patch.object(paths, "schemes_path", return_value=settings / "schemes.json"),
        ]
        for p in patches:
            p.start()
        platform.invalidate_caches()  # 已经问过的结果有缓存：清掉，谁再问就会问到假的 WMI
        box: dict = {}
        threads: list[threading.Thread] = []

        def export():
            try:
                box["out"] = diagnostics.export(None, {"hero": "凛"}, [], "")
            except BaseException as e:  # noqa: BLE001  交给测试线程报
                box["error"] = e

        try:
            worker = threading.Thread(target=export, daemon=True)
            threads.append(worker)
            worker.start()
            worker.join(10)
            self.assertFalse(worker.is_alive(), "导出诊断 10 秒内没有做完：又在等 WMI")
            self.assertNotIn("error", box)
            text = (box["out"] / diagnostics.VERSION_FILE).read_text(encoding="utf-8")
            line = next(s for s in text.splitlines() if s.startswith("系统："))
            self.assertRegex(line, r"^系统：Windows-\w+-\d+\.\d+\.\d+-SP\d+$")
            self.assertEqual(wmi.calls, [])
            # 对照：platform.platform() 在同一个假 WMI 上卡住
            probe = threading.Thread(target=platform.platform, daemon=True)
            threads.append(probe)
            probe.start()
            probe.join(1.0)
            self.assertTrue(probe.is_alive())
            self.assertTrue(wmi.calls)
        finally:
            wmi.gate.set()
            for t in threads:
                t.join(10)
            for p in reversed(patches):
                p.stop()
            platform.invalidate_caches()


class MachineInfoExportTest(unittest.TestCase):
    """导出诊断时把这台电脑的配置和运行状况写进 version.txt 和 state.json（只取一次）；读不到时只写一行、导出照常成功。"""

    MACHINE = {
        "cpu": {"name": "X", "cores": 8, "logical": 16,
                "classes": [{"efficiency": 0, "cores": 8, "logical": 16, "processors": "0-15"}]},
        "memory": {"total_gb": 31.7, "available_gb": 12.3},
        "power": {"ac": True, "battery_percent": None, "saver": False, "mode": "balanced", "plan": "balanced"},
        "displays": [{"width": 2560, "height": 1440, "scale": 125, "primary": True}],
        "gpus": ["NVIDIA GeForce RTX 3060"],
        "process": {"priority": "normal", "performance": "high"},
    }

    def setUp(self):
        self._dir = tmpdir("machine-reports")
        self.reports = Path(self._dir.name)
        self._home = tmpdir("machine-settings")
        self.settings = Path(self._home.name)
        for patcher in (mock.patch.object(paths, "reports_dir", return_value=self.reports),
                        mock.patch.object(paths, "state_path", return_value=self.settings / "state.json"),
                        mock.patch.object(paths, "schemes_path", return_value=self.settings / "schemes.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._home.cleanup)

    def exported(self, machine) -> Path:
        with mock.patch.object(sysinfo, "machine_info", machine):
            return diagnostics.export(None, {"hero": "凛"}, [], "")

    def test_version_and_state_include_machine(self):
        """version.txt 里有处理器、内存、电源、运行优先级、显示器、显卡几行；state.json 的 system 就是这一次取到的结果；
        machine_info 只被调用一次（version.txt 和 state.json 共用一份）。"""
        called = mock.Mock(return_value=self.MACHINE)
        out = self.exported(called)
        called.assert_called_once()
        version = (out / diagnostics.VERSION_FILE).read_text(encoding="utf-8")
        for line in ("处理器：X", "内存：共 31.7 GB，可用 12.3 GB", "电源：插着电源；电源计划：平衡；电源模式：平衡",
                     "运行优先级：正常；高性能声明：已声明", "显示器：2560×1440（125%，主屏）",
                     "显卡：NVIDIA GeForce RTX 3060"):
            with self.subTest(line=line):
                self.assertIn(line, version)
        state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
        self.assertEqual(state["system"], self.MACHINE)

    def test_machine_error_does_not_break_export(self):
        """machine_info 抛异常时导出照常成功：version.txt 只有一行「电脑配置：读不到（…）」，state.json 的 system 写成
        {"error": "…"}。"""
        def boom():
            raise RuntimeError("读不到")

        out = self.exported(mock.Mock(side_effect=boom))
        version = (out / diagnostics.VERSION_FILE).read_text(encoding="utf-8")
        self.assertIn("电脑配置：读不到（RuntimeError: 读不到）", version)
        self.assertNotIn("处理器：", version)
        state = json.loads((out / diagnostics.STATE).read_text(encoding="utf-8"))
        self.assertEqual(state["system"], {"error": "RuntimeError: 读不到"})


if __name__ == "__main__":
    unittest.main()
