"""界面语言（advisor/ui/lang.py）：Windows 语言代号怎么对到简体、繁体；设置文件里存的语言怎么读（文件不在、读坏了、不是
JSON 对象、没选过、写了别的值，都按没选过；只读不写）；主进程的 init（设置文件优先，没选过时按 Windows 的显示语言）、画皮肤
缩略图的子进程的 init_from_env 和 child_env。

不读真的设置文件：设置文件写在项目 .tmp 下的临时文件夹里；Windows 的显示语言一律换成假的。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from advisor import paths
from advisor.ui import lang


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-ui-lang"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class LangTestCase(unittest.TestCase):
    """讲的是繁体界面打开（lang.HANT_ENABLED）以后的定法；关着时一律简体，见 DisabledTest。"""

    def setUp(self):
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        switch = mock.patch.object(lang, "HANT_ENABLED", True)
        switch.start()
        self.addCleanup(switch.stop)


class LangIdTest(LangTestCase):
    def test_traditional_chinese(self):
        for langid in (0x0404, 0x0C04, 0x1404, 0x7C04):  # 台湾、香港、澳门、不分地区的繁体
            self.assertEqual(lang.from_langid(langid), lang.HANT, hex(langid))

    def test_others_are_simplified(self):
        for langid in (0x0804, 0x1004, 0x0004, 0x0409, 0x0411, 0x0412, 0x0C09):  # 大陆、新加坡、不分地区的简体、英语、日语等
            self.assertEqual(lang.from_langid(langid), lang.HANS, hex(langid))

    def test_system_language_failure_is_simplified(self):
        with mock.patch("ctypes.windll", create=True, new=None):
            self.assertEqual(lang.system_language(), lang.HANS)


class SavedLanguageTest(LangTestCase):
    def write(self, folder: str, content: bytes) -> Path:
        path = Path(folder) / "state.json"
        path.write_bytes(content)
        return path

    def test_valid_values(self):
        with tmpdir() as d:
            for value in lang.LANGUAGES:
                path = self.write(d, json.dumps({lang.SETTING: value, "skin": "default"}).encode("utf-8"))
                self.assertEqual(lang.saved_language(path), value)

    def test_not_chosen_or_invalid(self):
        with tmpdir() as d:
            for raw in ({}, {lang.SETTING: ""}, {lang.SETTING: "zh-TW"}, {lang.SETTING: 1}, {lang.SETTING: None}):
                path = self.write(d, json.dumps(raw).encode("utf-8"))
                self.assertIsNone(lang.saved_language(path), raw)

    def test_broken_files(self):
        with tmpdir() as d:
            deep = b"[" * 100000 + b"]" * 100000  # 嵌套太深：json 报 RecursionError
            for content in (b"", b"{", b"[1, 2]", "\"zh-Hant\"".encode("utf-8"), b"\xff\xfe{}", deep):
                path = self.write(d, content)
                self.assertIsNone(lang.saved_language(path), content)
            self.assertIsNone(lang.saved_language(Path(d) / "missing.json"))

    def test_read_only(self):
        """读坏了也不另存、不改名、不写备份：文件夹里还是原来那一个文件，内容不变。"""
        with tmpdir() as d:
            path = self.write(d, b"{broken")
            self.assertIsNone(lang.saved_language(path))
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["state.json"])
            self.assertEqual(path.read_bytes(), b"{broken")


class InitTest(LangTestCase):
    def test_saved_language_wins(self):
        with tmpdir() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps({lang.SETTING: lang.HANT}), encoding="utf-8")
            with mock.patch.object(lang, "system_language", return_value=lang.HANS):
                self.assertEqual(lang.init(path), lang.HANT)
            self.assertTrue(lang.is_hant())

    def test_not_chosen_follows_windows(self):
        with tmpdir() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps({lang.SETTING: ""}), encoding="utf-8")
            for system in lang.LANGUAGES:
                with mock.patch.object(lang, "system_language", return_value=system):
                    self.assertEqual(lang.init(path), system)
                    self.assertEqual(lang.CURRENT, system)

    def test_no_settings_file_follows_windows(self):
        with tmpdir() as d, mock.patch.object(lang, "system_language", return_value=lang.HANT):
            self.assertEqual(lang.init(Path(d) / "state.json"), lang.HANT)

    def test_default_path_is_state_path(self):
        with tmpdir() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps({lang.SETTING: lang.HANT}), encoding="utf-8")
            with mock.patch.object(paths, "state_path", return_value=path), \
                    mock.patch.object(lang, "system_language", return_value=lang.HANS):
                self.assertEqual(lang.init(), lang.HANT)

    def test_state_path_failure_follows_windows(self):
        with mock.patch.object(paths, "state_path", side_effect=OSError("no folder")), \
                mock.patch.object(lang, "system_language", return_value=lang.HANT):
            self.assertEqual(lang.init(), lang.HANT)


class ChildTest(LangTestCase):
    def test_env_round_trip(self):
        for value in lang.LANGUAGES:
            lang.CURRENT = value
            env = lang.child_env({"PATH": "x"})
            self.assertEqual(env, {"PATH": "x", lang.ENV: value})
            lang.CURRENT = lang.HANS if value == lang.HANT else lang.HANT
            with mock.patch.dict(os.environ, {lang.ENV: env[lang.ENV]}):
                self.assertEqual(lang.init_from_env(), value)

    def test_env_missing_or_invalid_is_simplified(self):
        lang.CURRENT = lang.HANT
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(lang.ENV, None)
            self.assertEqual(lang.init_from_env(), lang.HANS)
        for bad in ("", "zh-TW", "繁體"):
            lang.CURRENT = lang.HANT
            with mock.patch.dict(os.environ, {lang.ENV: bad}):
                self.assertEqual(lang.init_from_env(), lang.HANS)

    def test_child_env_copies(self):
        base = {"A": "1"}
        lang.CURRENT = lang.HANT
        self.assertIsNot(lang.child_env(base), base)
        self.assertEqual(base, {"A": "1"})


class DisabledTest(unittest.TestCase):
    """繁体界面关着（lang.HANT_ENABLED 为 False，繁体界面还没做完的版本）：主进程、子进程都定成简体，设置文件里存着繁體、
    Windows 是繁体、环境变量写着繁體也一样；主进程不读设置文件。"""

    def setUp(self):
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        switch = mock.patch.object(lang, "HANT_ENABLED", False)
        switch.start()
        self.addCleanup(switch.stop)

    def test_main_process_is_simplified(self):
        with tmpdir() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps({lang.SETTING: lang.HANT}), encoding="utf-8")
            with mock.patch.object(lang, "system_language", return_value=lang.HANT), \
                    mock.patch.object(lang, "saved_language", wraps=lang.saved_language) as saved, \
                    mock.patch.object(paths, "state_path", return_value=path):
                for args in ((path,), ()):
                    lang.CURRENT = lang.HANT
                    self.assertEqual(lang.init(*args), lang.HANS)
                    self.assertFalse(lang.is_hant())
            saved.assert_not_called()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {lang.SETTING: lang.HANT})  # 原样留着

    def test_child_process_is_simplified(self):
        lang.CURRENT = lang.HANT
        with mock.patch.dict(os.environ, {lang.ENV: lang.HANT}):
            self.assertEqual(lang.init_from_env(), lang.HANS)
        self.assertFalse(lang.is_hant())


if __name__ == "__main__":
    unittest.main()
