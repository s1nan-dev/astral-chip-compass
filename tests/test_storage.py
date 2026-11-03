"""设置和筹码方案的位置（advisor/paths.py）：开发版在程序目录；发行版在个人目录 %APPDATA%\\AstralChipCompass，
个人目录建不了、写不进时退回程序目录另起名的文件（state.fallback.json、schemes.fallback.json）；第一次启动、
从 0.12.0 升上来、现在的设置读写一遍、运行中设置文件和方案文件存不进、下次启动把退回期间存的较新的那份写回个人目录
并标记为用过、诊断包里的设置目录。

下载的识别图片的位置（paths.download_dir，DownloadDirTest 起的几组）：开发版在项目的 assets；发行版在个人目录
%LOCALAPPDATA%\\AstralChipCompass\\assets（图片、两份下载记录、立绘模板），随发行版带的星数横条模板和皮肤缩略图留在程序
文件夹；没有 LOCALAPPDATA、建不了、写不进时退回程序文件夹；换新版本照样沿用、只下新出的和改过的；导入时就算好的路径不定在
决定之前；画缩略图的子进程只读主程序交来的那一处（和主程序看同一批图片，缩略图不会每次都重画）、不建个人目录；诊断包和
自检里写明在哪；测试沙箱把 LOCALAPPDATA 也指进沙箱、核对真实的那一处。

发行版用 mock 模拟（paths.is_frozen 为 True），程序目录、APPDATA 和 LOCALAPPDATA 都指到项目 .tmp 下的临时文件夹：不碰真实的
个人目录，项目目录下的 state.json 只读、复制副本来用。下载换成假的回话，不联网。
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import stat
import tempfile
import time
import unittest
import urllib.error
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock

from PySide6.QtGui import QColor, QImage

# 以下几个模块在模拟发行版之前导入（模块里有按程序目录算好的常量；悬浮窗导入时按开发版决定设置位置）。识别、下载、画缩略图
# 的几个模块也是：下载的识别图片的位置要在用到时才取，不能在导入时就按开发版定死（ImportTimeTest）
from advisor import assets, clock
from advisor import diagnostics, paths, prefs, version  # prefs：设置的读写（替换 _write_settings 要换这个模块里的）
from advisor import schemes as S
from advisor.session import GAME_FIELDS, Session
from advisor.ui import skin_preview, theme, widgets
from advisor.ui.overlay_parts import option_widgets
from advisor.ui.overlay_parts.app import UiSettings, load_settings  # 悬浮窗的设置（含只有悬浮窗用的几项）、启动时读设置
from advisor.vision import chip_panel, hero, stars
from tests.support import qtenv, sandbox

ROOT = paths.app_dir()

# 0.12.0 发行版留在程序文件夹里的 state.json 的内容
OLD_STATE = {"hero": "阿尔", "window_pos": [1446, 68], "manual": False, "hotkey": None,
             "window_rel": [0.7293, 0.0431, "tr"], "collapsed_cards": []}


def _tmp_base() -> Path:
    base = paths.temp_dir() / "tests-storage"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


def _scheme(hero: str = "芬妮", name: str = "治愈坦克") -> S.Scheme:
    return S.Scheme(hero=hero, name=name, tiers={"缓冲盾牌": "core", "大碗炖肉": "first"})


def _check_round_trip(test: unittest.TestCase, original: bytes, saved: Path) -> None:
    """读写一遍后的设置文件：原有各项（只少了已取消的 allow_capture）逐项相同、一项不少；多出来的只能是以后新加的
    设置项，而且等于默认值（例如导入/导出窗口的格式 share_format）。这会儿不写的项（UiSettings.hidden_fields：发行版皮肤
    功能解锁以前的 skin）原来有也不写。"""
    hidden = set(UiSettings().hidden_fields())
    before = {k: v for k, v in json.loads(original.decode("utf-8")).items() if k != "allow_capture" and k not in hidden}
    after = json.loads(saved.read_text(encoding="utf-8"))
    test.assertEqual(hidden & set(after), set())
    test.assertEqual({k: after.get(k, "（缺少这一项）") for k in before}, before)
    defaults = {k: v for k, v in asdict(UiSettings()).items() if k not in GAME_FIELDS}
    added = {k: v for k, v in after.items() if k not in before}
    test.assertEqual(added, {k: defaults.get(k, "（不是设置项）") for k in added})


def _read_only(test: unittest.TestCase, path: Path) -> None:
    """把文件设成只读（写不进、替换不了）；测试结束时改回去，临时文件夹才删得掉。"""
    os.chmod(path, stat.S_IREAD)
    test.addCleanup(lambda: path.exists() and os.chmod(path, stat.S_IREAD | stat.S_IWRITE))


def _used(folder: Path) -> dict[str, bytes]:
    """folder 里标记为用过的退回文件：原来的文件名 → 内容（「state.fallback.json.used-时间」→ state.fallback.json）。"""
    return {p.name.split(paths.USED_MARK)[0]: p.read_bytes() for p in sorted(folder.iterdir()) if paths.USED_MARK in p.name}


class DevLayoutTest(unittest.TestCase):
    def setUp(self):
        self.base = _tmp_base()
        self.addCleanup(shutil.rmtree, self.base, True)
        sandbox.use_real_paths(self)  # 核对开发版的真实位置（只取位置；要写的都换成 .tmp 下的假程序目录）
        paths._reset_for_tests()
        self.addCleanup(paths._reset_for_tests)

    def test_dev_keeps_settings_in_program_dir(self):
        """开发版：设置和方案在项目目录下；不看 APPDATA，不试写、不建个人目录，没有退回。"""
        roaming = self.base / "Roaming"
        with mock.patch.dict(os.environ, {"APPDATA": str(roaming)}):
            self.assertFalse(paths.is_frozen())
            self.assertFalse(diagnostics.is_frozen())  # 诊断包「运行方式」和设置位置用同一个判断
            self.assertEqual(paths.state_path(), ROOT / "state.json")
            self.assertEqual(paths.schemes_path(), ROOT / "schemes.json")
            self.assertEqual(paths.settings_dir(), ROOT)
            self.assertIsNone(paths.storage_fallback())
            self.assertIsNone(paths.schemes_fallback())
            self.assertEqual(paths.user_dir(), roaming / "AstralChipCompass")  # 只算路径
        self.assertFalse(roaming.exists())

    def test_same_check_as_diagnostics(self):
        with mock.patch.object(paths, "is_frozen", return_value=True):
            self.assertTrue(diagnostics.is_frozen())

    def test_dev_save_error_does_not_raise(self):
        """开发版设置文件写不进（只读）：不抛错（识别线程里保存设置时抛错会丢掉这一帧的识别结果），记警告，返回 False（没写成），
        没有可退回的地方，也不另起名保存；文件不坏，临时文件不留下。"""
        with mock.patch.object(paths, "app_dir", return_value=self.base):
            state = paths.state_path()
            UiSettings(hero="芬妮").save(state)
            before = state.read_bytes()
            _read_only(self, state)
            with self.assertLogs("advisor.prefs", level="WARNING"):
                self.assertIs(UiSettings(hero="凛").save(state), False)
            self.assertIsNone(paths.storage_fallback())
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["state.json"])

    def test_broken_file_is_replaced(self):
        """设置文件坏了（不是 UTF-8，例如写到一半中断）、没有备份：读的时候坏文件另存成 state.json.broken-时间，按默认设置；
        保存时照常整份写好，不抛错。"""
        path = self.base / "state.json"
        broken = b'{"hero": "\xe8\x8a'  # 汉字只写了一半
        path.write_bytes(broken)
        self.assertEqual(UiSettings.load(path), UiSettings())
        kept = [p for p in self.base.iterdir() if p.name.startswith("state.json.broken-")]
        self.assertEqual([p.read_bytes() for p in kept], [broken])
        UiSettings(hero="芬妮").save(path)
        self.assertEqual(UiSettings.load(path).hero, "芬妮")

    def test_folder_name_written_once(self):
        """个人目录的名字在代码里只写一处（paths.FOLDER_NAME）；版本信息里的英文名去掉空格就是它。
        查本项的几个文件里的字符串（不算文档字符串和注释）。"""
        self.assertEqual(paths.FOLDER_NAME, "AstralChipCompass")
        self.assertEqual(version.EN_NAME.replace(" ", ""), paths.FOLDER_NAME)
        self.assertEqual(version.SHORT_NAME, "ACC")
        hits = []
        for rel in ("advisor/paths.py", "advisor/version.py", "advisor/diagnostics.py", "advisor/schemes.py",
                    "tools/build_release.py", "packaging/chip_advisor.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                        docs.add(id(node.body[0].value))
            hits += [f"{rel}:{n.lineno}" for n in ast.walk(tree) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str) and id(n) not in docs and paths.FOLDER_NAME in n.value]
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("advisor/paths.py:"), hits)


class _Release(unittest.TestCase):
    """模拟发行版：程序文件夹 app、APPDATA（Roaming）和 LOCALAPPDATA（Local）都在 .tmp 下的临时文件夹里。
    方案存储一律传入开发目录的资料（cat）：假的程序文件夹里没有 data，按它读会把空的编号表缓存下来。"""

    @classmethod
    def setUpClass(cls):
        cls.cat = S.get_catalog()

    def store(self) -> S.SchemeStore:
        """默认位置的方案存储（和悬浮窗一样不传 path）。"""
        return S.SchemeStore(catalog=self.cat)

    def setUp(self):
        self.base = _tmp_base()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.app = self.base / "AstralChipCompass"  # 解压出来的发行版文件夹
        self.app.mkdir()
        self.roaming = self.base / "Roaming"
        self.roaming.mkdir()
        self.user = self.roaming / "AstralChipCompass"
        self.local = self.base / "Local"  # 和真实的用户目录一样，LOCALAPPDATA 本身是在的
        self.local.mkdir()
        self.images = self.local / "AstralChipCompass" / "assets"  # 发行版下载的识别图片的位置
        for patcher in (mock.patch.object(paths, "is_frozen", return_value=True),
                        mock.patch.object(paths, "app_dir", return_value=self.app),
                        mock.patch.dict(os.environ, {"APPDATA": str(self.roaming), "LOCALAPPDATA": str(self.local)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(paths.DOWNLOADS_ENV, None)  # 子进程那一支按各测试自己给的（上面的补丁结束时连同它一起换回）
        paths._reset_for_tests()
        self.addCleanup(paths._reset_for_tests)
        quiet, logger = logging.NullHandler(), logging.getLogger("advisor.paths")  # 退回时的警告不打到测试输出里
        logger.addHandler(quiet)
        self.addCleanup(logger.removeHandler, quiet)

    def restart(self) -> None:
        """模拟重新启动程序：重新判断设置的位置。"""
        paths._reset_for_tests()

    def names(self, folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

    def unwritable(self):
        """个人目录写不进（权限）：试写时报拒绝访问。"""
        return mock.patch.object(paths.tempfile, "mkstemp", side_effect=PermissionError(13, "拒绝访问"))


class ReleaseLayoutTest(_Release):
    def test_files_in_their_places(self):
        """发行版：设置和方案在个人目录（Roaming）；下载的识别图片和立绘模板在个人目录（Local）的 assets；推荐日志、识别记录、
        诊断包、随发行版带的星数横条模板、皮肤缩略图、错误记录和临时文件在程序文件夹。"""
        self.assertEqual(paths.user_dir(), self.user)
        self.assertEqual(paths.settings_dir(), self.user)
        self.assertEqual(paths.state_path(), self.user / "state.json")
        self.assertEqual(paths.schemes_path(), self.user / "schemes.json")
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(paths.schemes_fallback(), self.app / "schemes.fallback.json")
        for got, rel in ((paths.logs_dir(), "logs"), (paths.games_dir(), "logs/games"), (paths.reports_dir(), "reports"),
                         (paths.assets_dir("stars"), "assets/stars"), (paths.assets_dir("skins"), "assets/skins"),
                         (paths.temp_dir(), ".tmp"), (paths.data_dir(), "data")):
            with self.subTest(rel=rel):
                self.assertEqual(got, self.app / rel)
        for got, rel in ((paths.download_dir("heroes"), "heroes"), (hero.user_dir(), "heroes/user"),
                         (paths.download_dir(), "")):
            with self.subTest(rel=rel):
                self.assertEqual(got, self.images / rel)
        self.assertIsNone(paths.download_fallback())
        self.assertEqual(self.names(self.user), [])  # 试写用的临时文件已删掉
        self.assertEqual(self.names(self.images), [])

    def test_first_start(self):
        """第一次启动、个人目录还没有：照常启动、默认设置；第一次保存设置、方案时文件才写进个人目录。
        个人目录本身在第一次用到设置位置时（启动时，试写那一步）就建好（空的）；保存时文件夹不在也会再建。"""
        self.assertFalse(self.user.exists())
        settings = UiSettings.load(paths.state_path())
        self.assertEqual(settings, UiSettings())  # 默认设置
        # 悬浮窗启动时读设置：没有设置文件（新用户）时「已有筹码」卡片默认收起，其余照默认设置
        self.assertEqual(load_settings(paths.state_path()), UiSettings(collapsed_cards=["owned"]))
        store = self.store()
        self.assertEqual((store.path, store.schemes(), store.notice), (self.user / "schemes.json", [], None))
        self.assertTrue(self.user.is_dir())
        self.assertEqual(self.names(self.user), [])
        settings.hero, settings.window_pos = "芬妮", [10, 20]
        settings.save(paths.state_path())
        store.add(_scheme())
        self.assertEqual(self.names(self.user), ["schemes.json", "state.json"])
        self.assertEqual(self.names(self.app), [])  # 程序文件夹里不写设置和方案
        self.restart()
        self.assertEqual(UiSettings.load(paths.state_path()).hero, "芬妮")
        self.assertEqual([s.name for s in self.store().schemes("芬妮")], ["治愈坦克"])

    def test_new_version_in_new_folder_keeps_settings(self):
        """换新版本（解压到新的文件夹）：设置和方案在个人目录里，新文件夹直接沿用，不用搬（含悬浮窗背景不透明）。"""
        settings = UiSettings(hero="芬妮", manual=True, collapsed_cards=["owned"], opaque=True)
        settings.save(paths.state_path())
        self.store().add(_scheme())
        new_app = self.base / "AstralChipCompass-new"
        new_app.mkdir()
        with mock.patch.object(paths, "app_dir", return_value=new_app):
            self.restart()
            self.assertEqual(UiSettings.load(paths.state_path()), settings)
            self.assertEqual(len(self.store().schemes()), 1)
        self.assertEqual(self.names(new_app), [])

    def test_unwritable_falls_back_to_program_dir(self):
        """个人目录写不进：退回程序文件夹另起名的文件保存，storage_fallback() 给出原因（悬浮窗据此提示一次）；
        设置和方案不丢：下次启动仍写不进时读这份。"""
        with self.unwritable():
            reason = paths.storage_fallback()
            self.assertEqual((reason.kind, reason.detail), (paths.FALLBACK_UNWRITABLE, "PermissionError [Errno 13]"))
            self.assertIs(paths.storage_fallback(), reason)  # 启动时决定一次，之后一直是这个
            self.assertEqual(paths.settings_dir(), self.app)
            self.assertEqual(paths.state_path(), self.app / "state.fallback.json")
            self.assertEqual(paths.schemes_path(), self.app / "schemes.fallback.json")
            self.assertIsNone(paths.schemes_fallback())  # 已经在程序文件夹里，没有再退的地方
            UiSettings(hero="芬妮", hotkey="F8").save(paths.state_path())
            self.store().add(_scheme())
            self.assertEqual(self.names(self.app), ["schemes.fallback.json", "state.fallback.json"])
            self.restart()
            self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F8")
            self.assertEqual(len(self.store().schemes()), 1)
        self.assertEqual(self.names(self.user), [])

    def test_cannot_create_personal_dir(self):
        """个人目录建不了（同名的文件占着位置）：同样退回；原因里只有出错的类型和错误代码，不含路径（路径里有账户名）。"""
        self.user.write_bytes(b"x")
        reason = paths.storage_fallback()
        self.assertEqual(reason.kind, paths.FALLBACK_UNWRITABLE)
        self.assertTrue(reason.detail.startswith("FileExistsError"), reason.detail)
        self.assertNotIn(str(self.base), reason.detail)
        self.assertEqual(paths.state_path(), self.app / "state.fallback.json")
        self.assertEqual(self.user.read_bytes(), b"x")

    def test_no_appdata(self):
        """没有环境变量 APPDATA（或为空、是相对路径）：退回程序文件夹。"""
        for value in (None, "", "  ", "AppData\\Roaming"):
            env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
            if value is not None:
                env["APPDATA"] = value
            with self.subTest(value=value), mock.patch.dict(os.environ, env, clear=True):
                self.restart()
                self.assertIsNone(paths.user_dir())
                self.assertEqual(paths.storage_fallback(), paths.Fallback(paths.FALLBACK_NO_APPDATA))
                self.assertEqual(paths.state_path(), self.app / "state.fallback.json")
        self.assertEqual(self.names(self.app), [])

    def test_next_start_takes_back_fallback(self):
        """退回后下次启动、个人目录能写了：个人目录里还没有时，把退回期间存的那份写进个人目录，然后把它改名为
        「原名.used-时间」（标记为用过，不删）；以后改个人目录里的、甚至个人目录被删掉，它都不再被写回。"""
        with self.unwritable():
            UiSettings(hotkey="F7").save(paths.state_path())
            self.store().add(_scheme())
        fallback_state = (self.app / "state.fallback.json").read_bytes()
        fallback_schemes = (self.app / "schemes.fallback.json").read_bytes()
        self.restart()  # 个人目录能写了，里面还没有文件
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(paths.state_path(), self.user / "state.json")
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F7")
        self.assertEqual([s.name for s in self.store().schemes()], ["治愈坦克"])
        self.assertEqual((self.user / "state.json").read_bytes(), fallback_state)
        self.assertEqual(self.names(self.user), ["schemes.json", "state.json"])  # 原来没有：不留 .bak
        self.assertEqual(_used(self.app), {"schemes.fallback.json": fallback_schemes, "state.fallback.json": fallback_state})
        self.assertFalse((self.app / "state.fallback.json").exists())
        # 改了个人目录里的：再启动时用它；用过的那份不读、不改
        UiSettings(hotkey="F8").save(paths.state_path())
        self.restart()
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F8")
        # 个人目录被删掉了：从默认设置开始，用过的那份不再写回
        shutil.rmtree(self.user)
        self.restart()
        self.assertEqual(UiSettings.load(paths.state_path()), UiSettings())
        self.assertEqual(self.store().schemes(), [])
        self.assertEqual(self.names(self.user), [])
        self.assertEqual(len(_used(self.app)), 2)

    def stamp(self, path: Path, seconds: float) -> None:
        """把文件的修改时间设成固定的时刻（比较退回的那份和个人目录里的哪个新，不靠写入的先后）。"""
        ns = int((1_790_000_000 + seconds) * 1e9)
        os.utime(path, ns=(ns, ns))

    def fallback_period(self, personal_hotkey: str, fallback_hotkey: str) -> tuple[Path, Path]:
        """个人目录里有设置（personal_hotkey），之后有一段时间个人目录写不进、改存在程序文件夹（fallback_hotkey）。
        返回（个人目录里的设置文件, 退回时存的设置文件）。"""
        personal = self.user / "state.json"
        UiSettings(hotkey=personal_hotkey).save(paths.state_path())
        with self.unwritable():
            self.restart()
            UiSettings(hotkey=fallback_hotkey).save(paths.state_path())
        return personal, self.app / "state.fallback.json"

    def test_newer_fallback_written_back(self):
        """退回的那份比个人目录里的新：写进个人目录（被替换的那份留作 state.json.bak），退回的那份标记为用过。"""
        personal, fallback = self.fallback_period("F6", "F7")
        old, new = personal.read_bytes(), fallback.read_bytes()
        self.stamp(personal, 0)
        self.stamp(fallback, 10)
        self.restart()
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F7")
        self.assertEqual((personal.read_bytes(), (self.user / "state.json.bak").read_bytes()), (new, old))
        self.assertEqual(self.names(self.user), ["state.json", "state.json.bak"])
        self.assertEqual(_used(self.app), {"state.fallback.json": new})
        self.assertFalse(fallback.exists())

    def test_personal_newer_only_marks(self):
        """个人目录里的比退回的那份新（例如退回期间另一个版本在个人目录里存过）：不覆盖，只把退回的那份标记为用过。"""
        personal, fallback = self.fallback_period("F6", "F7")
        old, new = personal.read_bytes(), fallback.read_bytes()
        self.stamp(fallback, 0)
        self.stamp(personal, 10)
        self.restart()
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F6")
        self.assertEqual(personal.read_bytes(), old)
        self.assertEqual(self.names(self.user), ["state.json"])
        self.assertEqual(_used(self.app), {"state.fallback.json": new})

    def test_same_content_only_marks(self):
        """内容相同（退回期间没改过设置）：不重写个人目录里的、不留 .bak，只标记。"""
        personal, fallback = self.fallback_period("F6", "F6")
        self.stamp(personal, 0)
        self.stamp(fallback, 10)
        self.restart()
        self.assertEqual(paths.state_path(), personal)  # 启动时决定位置（悬浮窗导入时就是这一步）
        self.assertEqual(self.names(self.user), ["state.json"])
        self.assertEqual(personal.stat().st_mtime_ns, int((1_790_000_000 + 0) * 1e9))
        self.assertEqual(list(_used(self.app)), ["state.fallback.json"])

    def test_take_back_fails_uses_fallback(self):
        """退回的那份较新、却写不回个人目录（个人目录里的设置文件只读）：这次运行照旧用程序文件夹里的那份（较新的设置
        不丢），提示一次；退回的那份不标记，个人目录里的不动、不留临时文件。能写了以后再启动就写回去。"""
        personal, fallback = self.fallback_period("F6", "F7")
        old = personal.read_bytes()
        self.stamp(personal, 0)
        self.stamp(fallback, 10)
        _read_only(self, personal)
        self.restart()
        reason = paths.storage_fallback()
        self.assertEqual(reason.kind, paths.FALLBACK_UNWRITABLE)
        self.assertEqual(paths.state_path(), fallback)
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F7")
        self.assertTrue(fallback.is_file())
        self.assertEqual(_used(self.app), {})
        self.assertEqual(personal.read_bytes(), old)
        self.assertNotIn("state.json.tmp", self.names(self.user))
        os.chmod(personal, stat.S_IREAD | stat.S_IWRITE)
        self.restart()
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F7")
        self.assertEqual(list(_used(self.app)), ["state.fallback.json"])

    def test_fallback_starts_from_readable_personal_files(self):
        """个人目录读得到、写不进：退回的文件还没有时先复制个人目录里的那份，设置和方案照常沿用；退回的文件已有时不覆盖。"""
        UiSettings(hero="芬妮").save(paths.state_path())
        self.store().add(_scheme())
        with self.unwritable():
            self.restart()
            self.assertEqual(paths.storage_fallback().kind, paths.FALLBACK_UNWRITABLE)
            self.assertEqual(UiSettings.load(paths.state_path()).hero, "芬妮")
            self.assertEqual(len(self.store().schemes()), 1)
            UiSettings(hero="凛").save(paths.state_path())  # 退回期间改的存在程序文件夹里
            self.restart()
            self.assertEqual(UiSettings.load(paths.state_path()).hero, "凛")
        self.assertEqual(UiSettings.load(self.user / "state.json").hero, "芬妮")  # 个人目录里的没被改

    def test_upgrade_from_0_12_0(self):
        """从 0.12.0 升上来：程序文件夹里留着 0.12.0 的 state.json（OLD_STATE），不读（设置恢复默认一次）、
        不报错、不删、不改；个人目录写不进、退回程序文件夹时也不读它（退回的文件另起名）。"""
        old = json.dumps(OLD_STATE, indent=1).encode()
        (self.app / "state.json").write_bytes(old)
        self.assertNotEqual(UiSettings.load(self.app / "state.json"), UiSettings())  # 读了的话就不是默认设置
        self.assertEqual(paths.state_path(), self.user / "state.json")
        settings = UiSettings.load(paths.state_path())
        self.assertEqual(settings, UiSettings())
        # 个人目录里还没有设置文件：悬浮窗按新用户处理（「已有筹码」卡片默认收起）
        self.assertEqual(load_settings(paths.state_path()).collapsed_cards, ["owned"])
        settings.save(paths.state_path())
        with self.unwritable():
            self.restart()
            self.assertEqual(paths.state_path(), self.app / "state.fallback.json")
            settings = UiSettings.load(paths.state_path())
            self.assertEqual(settings, UiSettings())
            settings.hero = "芬妮"
            settings.save(paths.state_path())
        self.assertEqual((self.app / "state.json").read_bytes(), old)
        # 退回的文件是从个人目录复制来的，改存时原来的内容留作它的 .bak
        self.assertEqual(self.names(self.app), ["state.fallback.json", "state.fallback.json.bak", "state.json"])

    def test_current_settings_round_trip(self):
        """开发版正在用的设置（项目目录下 state.json 的副本）放进个人目录，读写一遍：原有各项不变（只少了已取消的
        allow_capture），新加的设置项按默认值补上（_check_round_trip）；再存一次内容没变，不写文件。"""
        original = (ROOT / "state.json").read_bytes() if (ROOT / "state.json").is_file() else None
        if original is None:
            self.skipTest("项目目录下没有 state.json")
        paths.settings_dir()  # 个人目录建好
        (self.user / "state.json").write_bytes(original)
        settings = UiSettings.load(paths.state_path())
        settings.save(paths.state_path())
        _check_round_trip(self, original, self.user / "state.json")
        # 皮肤功能解锁以前不写 skin（hidden_fields）：读回来是默认皮肤
        self.assertEqual(UiSettings.load(paths.state_path()),
                         replace(settings, **{k: getattr(UiSettings(), k) for k in settings.hidden_fields()}))
        before = (self.user / "state.json").stat()
        time.sleep(0.02)
        UiSettings.load(paths.state_path()).save(paths.state_path())  # 内容没变不写
        self.assertEqual((self.user / "state.json").stat().st_mtime_ns, before.st_mtime_ns)


class DevSettingsRoundTripTest(unittest.TestCase):
    def test_current_settings_round_trip_in_dev(self):
        """开发版读写一遍正在用的设置（项目目录下 state.json 的副本，放在 .tmp 下的假程序目录里）：原有各项不变
        （只少了已取消的 allow_capture），新加的设置项按默认值补上，真实的文件不动。"""
        real = ROOT / "state.json"
        if not real.is_file():
            self.skipTest("项目目录下没有 state.json")
        original = real.read_bytes()
        base = _tmp_base()
        self.addCleanup(shutil.rmtree, base, True)
        (base / "state.json").write_bytes(original)
        paths._reset_for_tests()
        self.addCleanup(paths._reset_for_tests)
        with mock.patch.object(paths, "app_dir", return_value=base):
            self.assertEqual(paths.state_path(), base / "state.json")
            UiSettings.load(paths.state_path()).save(paths.state_path())
        _check_round_trip(self, original, base / "state.json")
        self.assertEqual(real.read_bytes(), original)

    def test_round_trip_check_is_strict(self):
        """上面的比较本身：少了原有的一项、改了原有的一项、多出不是设置项的、多出不等于默认值的，都算不通过。"""
        base = _tmp_base()
        self.addCleanup(shutil.rmtree, base, True)
        original = json.dumps({"hero": "芬妮", "allow_capture": True, "hotkey": "F9"}).encode("utf-8")
        cases = {
            "通过": ({"hero": "芬妮", "hotkey": "F9", "share_format": "short"}, True),
            "少了一项": ({"hero": "芬妮", "share_format": "short"}, False),
            "改了一项": ({"hero": "凛", "hotkey": "F9"}, False),
            "多出不认识的": ({"hero": "芬妮", "hotkey": "F9", "allow_capture": True}, False),
            "新项不是默认值": ({"hero": "芬妮", "hotkey": "F9", "share_format": "text"}, False),
        }
        for label, (saved, ok) in cases.items():
            path = base / "saved.json"
            path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            with self.subTest(label=label):
                if ok:
                    _check_round_trip(self, original, path)
                else:
                    with self.assertRaises(AssertionError):
                        _check_round_trip(self, original, path)


class SchemesFallbackTest(_Release):
    def test_save_error_moves_schemes_to_program_dir(self):
        """运行中方案文件在个人目录里存不进：改存到程序文件夹的 schemes.fallback.json，之后都存在那里；
        storage_fallback() 变为退回（悬浮窗刷新时据此提示一次），设置的位置不变；下次启动个人目录能写时，
        个人目录里没有方案文件就先复制这份，方案不丢。"""
        store = self.store()
        self.assertEqual(store.fallback, self.app / "schemes.fallback.json")
        a = store.add(_scheme())
        real = S._atomic_write

        def personal_fails(path, data, old):
            if path.parent == self.user:
                raise PermissionError(13, "拒绝访问")
            real(path, data, old)

        (self.user / "schemes.json").unlink()  # 个人目录里的方案文件没了（之后又写不进）
        with mock.patch.object(S, "_atomic_write", personal_fails), self.assertLogs("advisor.schemes", level="WARNING"):
            b = store.add(_scheme(name="标记速攻"))
        self.assertIsNone(store.save_error)
        self.assertEqual(store.path, self.app / "schemes.fallback.json")
        self.assertIsNone(store.fallback)
        reason = paths.storage_fallback()
        self.assertEqual((reason.kind, reason.detail), (paths.FALLBACK_SAVE_FAILED, "PermissionError [Errno 13]"))
        self.assertEqual(paths.schemes_path(), self.app / "schemes.fallback.json")
        self.assertEqual(paths.state_path(), self.user / "state.json")  # 设置文件的位置不变
        self.assertIsNone(paths.schemes_fallback())
        store.update(replace(b, note="之后都存在程序文件夹"))
        self.assertEqual(json.loads((self.app / "schemes.fallback.json").read_text(encoding="utf-8"))["schemes"][1]["note"],
                         "之后都存在程序文件夹")
        self.restart()
        self.assertIsNone(paths.storage_fallback())
        again = self.store()
        self.assertEqual(again.path, self.user / "schemes.json")
        self.assertEqual([s.id for s in again.schemes()], [a.id, b.id])

    def test_newer_fallback_replaces_personal_file(self):
        """个人目录里原来的方案文件还在、运行中改存到了程序文件夹：下次启动个人目录能写时，改存的那份较新，写回个人目录
        （原来那份留作 schemes.json.bak），改存期间的改动不丢；改存的那份标记为用过、不删。"""
        store = self.store()
        a = store.add(_scheme())
        personal = self.user / "schemes.json"
        before = personal.read_bytes()
        real = S._atomic_write

        def personal_fails(path, data, old):
            if path.parent == self.user:
                raise PermissionError(13, "拒绝访问")
            real(path, data, old)

        with mock.patch.object(S, "_atomic_write", personal_fails), self.assertLogs("advisor.schemes", level="WARNING"):
            b = store.add(_scheme(name="标记速攻"))
        fallback = self.app / "schemes.fallback.json"
        moved = fallback.read_bytes()
        mtime = personal.stat().st_mtime_ns + 10**9  # 改存的那份晚一秒（同一秒内写的也比得出先后）
        os.utime(fallback, ns=(mtime, mtime))
        self.restart()
        self.assertIsNone(paths.storage_fallback())
        again = self.store()
        self.assertEqual(again.path, personal)
        self.assertEqual([s.id for s in again.schemes()], [a.id, b.id])
        self.assertEqual(personal.read_bytes(), moved)
        self.assertEqual(S.backup_path(personal).read_bytes(), before)
        self.assertFalse(fallback.exists())
        self.assertEqual(_used(self.app), {"schemes.fallback.json": moved})

    def test_both_fail(self):
        """改存也不成：不抛出，错误留在 save_error，内存里的改动照旧有效，不算退回。"""
        store = self.store()
        with mock.patch.object(S, "_atomic_write", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor.schemes", level="WARNING"):
            a = store.add(_scheme())
        self.assertIsInstance(store.save_error, PermissionError)
        self.assertEqual(store.schemes(), [a])
        self.assertEqual(store.path, self.user / "schemes.json")
        self.assertIsNone(paths.storage_fallback())
        self.assertTrue(store.save())  # 恢复以后照常存在个人目录
        self.assertTrue((self.user / "schemes.json").is_file())


class SettingsFallbackTest(_Release):
    """运行中设置文件在个人目录里存不进（启动时试写通过，之后 state.json 变成只读、磁盘满等）：和方案文件一样改存到程序
    文件夹的 state.fallback.json，告诉 paths（悬浮窗下次刷新时提示一次），之后都存在那里；不抛错。"""

    def test_runtime_save_falls_back(self):
        state = paths.state_path()
        UiSettings(hero="芬妮").save(state)
        personal = self.user / "state.json"
        before = personal.read_bytes()
        _read_only(self, personal)
        session = Session(self.cat.db, UiSettings.load(state), state)
        session.settings.hotkey = "F9"
        with self.assertLogs("advisor.prefs", level="WARNING"):
            session.save()  # 不抛错
        fallback = self.app / "state.fallback.json"
        self.assertEqual(session.path, fallback)
        self.assertEqual(UiSettings.load(fallback).hotkey, "F9")
        self.assertEqual(personal.read_bytes(), before)  # 个人目录里的那份不动
        self.assertEqual(self.names(self.user), ["state.json"])  # 没有留下临时文件
        reason = paths.storage_fallback()  # 悬浮窗下次刷新时据此提示一次
        self.assertEqual((reason.kind, reason.detail), (paths.FALLBACK_STATE_FAILED, "PermissionError [WinError 5]"))
        self.assertEqual((paths.state_path(), paths.settings_dir()), (fallback, self.app))
        self.assertEqual(paths.schemes_path(), self.user / "schemes.json")  # 方案文件的位置不变
        self.assertIsNone(paths.state_fallback(fallback))  # 已经在程序文件夹里，没有再退的地方
        # 之后直接存在程序文件夹里，不再先试个人目录
        session.settings.hotkey = "F10"
        with mock.patch.object(prefs, "_write_settings", wraps=prefs._write_settings) as write:
            session.save()
        self.assertEqual([c.args[0] for c in write.call_args_list], [fallback])
        self.assertEqual(UiSettings.load(fallback).hotkey, "F10")
        # 诊断包带的是正在用的那份；「设置目录」一行写原因
        with mock.patch.object(paths, "reports_dir", return_value=self.base / "reports"):
            out = diagnostics.export(None, {}, [], "")
        self.assertEqual((out / "settings.json").read_bytes(), fallback.read_bytes())
        line = diagnostics.SETTINGS_DIR_FALLBACK.format(
            path=self.app, reason=f"设置文件无法写入 %APPDATA%\\{paths.FOLDER_NAME}（PermissionError [WinError 5]）")
        self.assertIn(line, (out / "version.txt").read_text(encoding="utf-8").splitlines())
        # 下次启动：个人目录里的仍是只读、退回的那份较新：写不回去，照旧用程序文件夹里的（设置不丢）
        self.restart()
        self.assertEqual(paths.state_path(), fallback)
        self.assertEqual(UiSettings.load(paths.state_path()).hotkey, "F10")

    def test_runtime_fallback_after_schemes_fell_back(self):
        """方案文件先在运行中退回了、设置文件后来也存不进：设置文件同样改存，原因保持第一次的（方案文件那条）。"""
        store = self.store()
        real = S._atomic_write

        def personal_fails(path, data, old):
            if path.parent == self.user:
                raise PermissionError(13, "拒绝访问")
            real(path, data, old)

        with mock.patch.object(S, "_atomic_write", personal_fails), self.assertLogs("advisor.schemes", level="WARNING"):
            store.add(_scheme())
        first = paths.storage_fallback()
        self.assertEqual(first.kind, paths.FALLBACK_SAVE_FAILED)
        state = paths.state_path()
        self.assertEqual(state, self.user / "state.json")  # 方案退回时设置文件的位置不变
        UiSettings(hero="芬妮").save(state)
        _read_only(self, state)
        with self.assertLogs("advisor.prefs", level="WARNING"):
            moved = UiSettings(hero="凛", hotkey="F9").save(state)
        self.assertEqual(moved, self.app / "state.fallback.json")
        self.assertIs(paths.storage_fallback(), first)
        self.assertEqual((paths.state_path(), paths.schemes_path()), (moved, self.app / "schemes.fallback.json"))
        self.assertEqual(UiSettings.load(moved).hotkey, "F9")

    def test_save_creates_missing_folder(self):
        """保存设置前文件夹不在就建（例如个人目录在运行中被删掉了）；不算退回。"""
        state = paths.state_path()
        shutil.rmtree(self.user)
        self.assertEqual(UiSettings(hero="芬妮").save(state), state)
        self.assertEqual(UiSettings.load(self.user / "state.json").hero, "芬妮")
        self.assertIsNone(paths.storage_fallback())

    def test_both_fail_does_not_raise(self):
        """改存也不成：不抛错，记警告，返回 False（没写成；内存里的设置照旧有效），不算退回。"""
        state = paths.state_path()
        with mock.patch.object(prefs, "_write_settings", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(UiSettings(hero="芬妮").save(state), False)
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(paths.state_path(), state)

    def test_other_files_never_redirected(self):
        """另给的文件（测试、工具）写不进时不改存、不改全局状态。"""
        other = self.base / "elsewhere" / "state.json"
        other.parent.mkdir()
        other.write_text("{}", encoding="utf-8")
        _read_only(self, other)
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(UiSettings(hero="芬妮").save(other), False)
        self.assertIsNone(paths.storage_fallback())
        self.assertFalse((self.app / "state.fallback.json").exists())


class DiagnosticsInReleaseTest(_Release):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(paths, "reports_dir", return_value=self.base / "reports")
        patcher.start()
        self.addCleanup(patcher.stop)

    def version_lines(self, out: Path) -> list[str]:
        return (out / "version.txt").read_text(encoding="utf-8").splitlines()

    def test_settings_dir_line_and_files(self):
        """诊断包：version.txt 有「设置目录」一行（在「运行方式」下面）；带上个人目录里的设置（改名 settings.json）和方案文件。"""
        UiSettings(hero="芬妮").save(paths.state_path())
        store = self.store()
        store.add(_scheme())
        store.add(_scheme(name="第二套"))  # 有备份 schemes.json.bak
        out = diagnostics.export(None, {"hero": "芬妮"}, [], "")
        lines = self.version_lines(out)
        # 设置目录在 APPDATA 下面：诊断包里脱敏成 %APPDATA%（不带出账户名或用户目录）
        scrubbed = f"%APPDATA%\\{paths.FOLDER_NAME}"
        self.assertIn(diagnostics.SETTINGS_DIR_LINE.format(path=scrubbed), lines)
        self.assertEqual(lines.index(diagnostics.SETTINGS_DIR_LINE.format(path=scrubbed)),
                         next(i for i, s in enumerate(lines) if s.startswith("运行方式：发行版")) + 1)
        self.assertEqual((out / "settings.json").read_bytes(), (self.user / "state.json").read_bytes())
        self.assertEqual((out / "schemes.json").read_bytes(), (self.user / "schemes.json").read_bytes())
        self.assertEqual((out / "schemes.json.bak").read_bytes(), (self.user / "schemes.json.bak").read_bytes())
        self.assertEqual(json.loads((out / "state.json").read_text(encoding="utf-8"))["hero"], "芬妮")  # 诊断包自己的状态

    def test_settings_dir_line_when_fallen_back(self):
        with self.unwritable():
            UiSettings(hero="芬妮").save(paths.state_path())
            out = diagnostics.export(None, {}, [], "")
        line = diagnostics.SETTINGS_DIR_FALLBACK.format(
            path=self.app, reason=f"无法写入 %APPDATA%\\{paths.FOLDER_NAME}（PermissionError [Errno 13]）")
        self.assertIn(line, self.version_lines(out))
        self.assertEqual((out / "settings.json").read_bytes(), (self.app / "state.fallback.json").read_bytes())
        self.restart()
        with mock.patch.dict(os.environ, {"APPDATA": ""}):
            out = diagnostics.export(None, {}, [], "")
        self.assertIn(diagnostics.SETTINGS_DIR_FALLBACK.format(path=self.app, reason="未设置环境变量 APPDATA"),
                      self.version_lines(out))

    def test_fallback_reasons_cover_every_kind(self):
        self.assertEqual(set(diagnostics.FALLBACK_REASONS),
                         {paths.FALLBACK_NO_APPDATA, paths.FALLBACK_UNWRITABLE, paths.FALLBACK_SAVE_FAILED,
                          paths.FALLBACK_STATE_FAILED})

    def test_self_check_reports_settings_dir(self):
        """自检结果写上设置目录和退回的原因（打包试启动时核对它在指定的个人目录里），以及程序目录里的打包信息（build.json 的
        写法；没有这个文件时为 None）。"""
        with mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}):
            result = diagnostics.self_check()
        self.assertTrue(result["frozen"])
        self.assertEqual((result["settings_dir"], result["storage_fallback"]), (str(self.user), None))
        self.assertIsNone(result[diagnostics.BUILD_STATE])
        self.restart()
        built = {"built": "2030-01-02T03:04:05Z", "commit": "1a2b3c4", "dirty": False}
        (self.app / version.BUILD_INFO_NAME).write_text(json.dumps(built), encoding="utf-8")
        with self.unwritable(), mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                mock.patch.object(diagnostics, "window_capture_check", return_value={}):
            result = diagnostics.self_check()
        self.assertEqual(result["settings_dir"], str(self.app))
        self.assertEqual(result["storage_fallback"], f"无法写入 {self.user}（PermissionError [Errno 13]）")
        self.assertEqual(result[diagnostics.BUILD_STATE], built)


# ---------------------------------------------------------------- 下载的识别图片的位置


def _png(payload: bytes) -> bytes:
    return assets.PNG_MAGIC + payload


def _item(kind: str, name: str, data: bytes) -> assets.AssetItem:
    """清单里一张照原样下载的图片（地址 https://img/<文件名>）。"""
    return assets.AssetItem(kind, name, name, f"https://img/{name}", len(data), hashlib.sha1(data).hexdigest())


class _Web:
    """假的取图（不联网）：按网址交回内容，记下请求过的网址；没有的回 404。"""

    def __init__(self, routes: dict):
        self.routes, self.calls = dict(routes), []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        key = next((k for k in self.routes if req.full_url.startswith(k)), None)
        if key is None:
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b""))
        return io.BytesIO(self.routes[key])


def _download(items, web) -> tuple:
    return assets.download(items, opener=web, sleep=lambda seconds: None)


class DownloadDirTest(_Release):
    """发行版下载的识别图片放在哪（paths.download_dir）：个人目录 %LOCALAPPDATA%\\AstralChipCompass\\assets，用不了时退回程序
    文件夹的 app\\assets。"""

    def test_personal_dir(self):
        """个人目录能写：下载的识别图片、两份下载记录、立绘模板都在 %LOCALAPPDATA%\\AstralChipCompass\\assets 下（第一次用到时
        建好，试写用的临时文件不留下），子文件夹名照旧；随发行版带的星数横条模板、皮肤缩略图在程序文件夹；没有退回的原因。"""
        self.assertFalse(self.images.exists())
        self.assertEqual(paths.local_dir(), self.local / "AstralChipCompass")
        self.assertEqual(paths.download_dir(), self.images)  # 第一次用到：试写、建好
        self.assertTrue(self.images.is_dir())
        self.assertEqual(self.names(self.images), [])
        self.assertIsNone(paths.download_fallback())
        photo = assets.AssetItem("heroes", "UT_Hero_RolePhoto_101.png")
        bar = assets.AssetItem("stars", "star_bar.png", bundled=True)
        self.assertEqual(photo.path, self.images / "heroes" / "UT_Hero_RolePhoto_101.png")
        self.assertEqual(assets.AssetItem("chips", "UT_Relic_50010.png").path, self.images / "chips" / "UT_Relic_50010.png")
        self.assertEqual(bar.path, self.app / "assets" / "stars" / "star_bar.png")
        self.assertEqual(assets._local_path(), self.images / assets.LOCAL_MANIFEST)
        self.assertEqual(assets._cooldown_path(), self.images / assets.COOLDOWN_FILE)
        self.assertEqual(hero.user_dir(), self.images / "heroes" / "user")
        self.assertEqual(skin_preview.default_folder(), self.app / "assets" / "skins")
        self.assertEqual(paths.personal_shown(paths.download_dir()), f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets")
        self.assertEqual(paths.child_environ()[paths.DOWNLOADS_ENV], str(self.images))  # 交给画缩略图的子进程的位置
        self.assertIn(self.images, paths.program_dirs())  # 启动时试写的是图片实际要写的那一处
        self.assertNotIn(self.app / "assets", paths.program_dirs())
        self.assertEqual(self.names(self.app), [])  # 程序文件夹里一样都没写

    def test_decided_once(self):
        """启动时决定一次、整个运行期间不变（同设置的位置）；重新启动以后按那时的情况重新决定。"""
        first = paths.download_dir()
        other = self.base / "Other"
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(other)}):
            self.assertEqual(paths.download_dir(), first)
            self.restart()
            self.assertEqual(paths.download_dir(), other / "AstralChipCompass" / "assets")

    def test_no_localappdata(self):
        """没有环境变量 LOCALAPPDATA（或为空、是相对路径）：这次运行退回程序文件夹的 app\\assets（两份下载记录、立绘模板也在
        那里），记下原因；设置和方案的个人目录不受影响；不建、不写个人目录。"""
        for value in (None, "", "  ", "AppData\\Local"):
            env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
            if value is not None:
                env["LOCALAPPDATA"] = value
            with self.subTest(value=value), mock.patch.dict(os.environ, env, clear=True):
                self.restart()
                self.assertIsNone(paths.local_dir())
                self.assertEqual(paths.download_dir("chips"), self.app / "assets" / "chips")
                self.assertEqual(paths.download_fallback(), paths.Fallback(paths.FALLBACK_NO_LOCALAPPDATA))
                self.assertEqual(assets._local_path(), self.app / "assets" / assets.LOCAL_MANIFEST)
                self.assertEqual(hero.user_dir(), self.app / "assets" / "heroes" / "user")
                self.assertEqual(paths.settings_dir(), self.user)
                self.assertIsNone(paths.personal_shown(paths.download_dir()))
        self.assertEqual(self.names(self.local), [])

    def test_unwritable(self):
        """个人目录写不进（权限）：退回程序文件夹，原因只写出错的类型和错误代码（不含路径），记一条警告；设置和方案照常在
        个人目录（另一处，照样能写）。不另出提示（悬浮窗那一侧见 tests/test_runtime_notices.py 的 FolderProbeTest）。"""
        real = paths.tempfile.mkstemp

        def mkstemp(*args, dir=None, **kwargs):
            if Path(dir) == self.images:
                raise PermissionError(13, "拒绝访问", str(Path(dir) / "x.tmp"))
            return real(*args, dir=dir, **kwargs)

        with mock.patch.object(paths.tempfile, "mkstemp", side_effect=mkstemp), \
                self.assertLogs("advisor.paths", level="WARNING") as logged:
            self.assertEqual(paths.download_dir(), self.app / "assets")
            reason = paths.download_fallback()
        self.assertEqual((reason.kind, reason.detail), (paths.FALLBACK_UNWRITABLE, "PermissionError [Errno 13]"))
        self.assertEqual(len(logged.output), 1)
        self.assertNotIn(str(self.base), logged.output[0])
        self.assertIsNone(paths.storage_fallback())
        self.assertEqual(paths.settings_dir(), self.user)

    def test_cannot_create(self):
        """个人目录建不了（同名的文件占着位置）：同样退回；占着位置的文件不动。"""
        blocker = self.local / "AstralChipCompass"
        blocker.write_bytes(b"x")
        self.assertEqual(paths.download_dir("heroes"), self.app / "assets" / "heroes")
        reason = paths.download_fallback()
        self.assertEqual(reason.kind, paths.FALLBACK_UNWRITABLE)
        self.assertTrue(reason.detail.startswith(("FileExistsError", "NotADirectoryError", "FileNotFoundError")),
                        reason.detail)
        self.assertEqual(blocker.read_bytes(), b"x")

    def test_dev_unchanged(self):
        """开发版：照旧全在项目的 assets（同 assets_dir），不看 LOCALAPPDATA、不试写、不建个人目录，没有退回的原因。"""
        with mock.patch.object(paths, "is_frozen", return_value=False):
            self.restart()
            self.assertEqual(paths.download_dir("chips"), self.app / "assets" / "chips")
            self.assertEqual(paths.download_dir(), paths.assets_dir())
            self.assertIsNone(paths.download_fallback())
        self.assertEqual(self.names(self.local), [])


class DownloadInPersonalDirTest(_Release):
    """下载、还缺几张、齐不齐、冷却、覆盖记录在个人目录里照常；换新版本（新的程序文件夹）照样沿用，只下新出的、改过的。
    下载换成假的回话（_Web），不联网。"""

    def test_download_into_personal_dir(self):
        """下载到个人目录（.part 写完改名，不留半截文件）；下好的不再算缺；程序文件夹里一样都不写。"""
        a = _png(b"hero")
        item = _item("heroes", "UT_Hero_RolePhoto_101.png", a)
        manifest = assets.Manifest([item])
        self.assertEqual(assets.missing(manifest), [item])
        ok, failed = _download([item], _Web({item.url: a}))
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual((self.images / "heroes" / item.file).read_bytes(), a)
        self.assertEqual(list(self.images.rglob("*.part")), [])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(self.names(self.app), [])

    def test_new_version_reuses_images(self):
        """换新版本（解压到新的文件夹）、删掉旧的程序文件夹：个人目录里下好的图片照常用、不重下，只下新出的和清单里改过的
        （按大小逐张核对）；wiki 上更新过、记在覆盖清单里的照覆盖清单算齐（不重下）；手动指定角色时存的立绘模板照样在；
        冷却记录照样认。新的程序文件夹里不写识别图片和记录。"""
        a, b, w_old, w_new = _png(b"a"), _png(b"bb"), _png(b"old"), _png(b"wiki-updated")
        items = [_item("heroes", "A.png", a), _item("profiles", "B.png", b), _item("chips", "W.png", w_old)]
        web = _Web({"https://img/A.png": a, "https://img/B.png": b, "https://img/W.png": w_new,
                    assets.API: json.dumps({"query": {"pages": [{"title": "File:W.png", "imageinfo": [
                        {"url": "https://img/W.png", "size": len(w_new),
                         "sha1": hashlib.sha1(w_new).hexdigest()}]}]}}).encode()})
        ok, failed = _download(items, web)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        self.assertIn("chips/W.png", assets.load_local()["updated"])  # wiki 上更新过：记在个人目录里的覆盖清单
        self.assertTrue((self.images / assets.LOCAL_MANIFEST).is_file())
        template = hero.user_dir() / f"{hero.USER_PREFIX}101_1.png"
        template.parent.mkdir(parents=True)
        template.write_bytes(_png(b"template"))
        assets._write_cooldown(assets.Cooldown(time.time() + 600, assets.COOLDOWN_MINUTES, time.time()))
        new_app = self.base / "AstralChipCompass-new"
        new_app.mkdir()
        shutil.rmtree(self.app)
        c, b2 = _png(b"ccc"), _png(b"bb-changed")
        newer = [_item("heroes", "A.png", a), _item("profiles", "B.png", b2), items[2], _item("chips", "C.png", c)]
        with mock.patch.object(paths, "app_dir", return_value=new_app):
            self.restart()
            self.assertEqual(paths.download_dir(), self.images)
            todo = assets.missing(assets.Manifest(newer))
            self.assertEqual([i.name for i in todo], ["profiles/B.png", "chips/C.png"])
            self.assertIsNotNone(assets.cooldown())  # 冷却记录照样认
            assets._write_cooldown(None)
            web = _Web({"https://img/B.png": b2, "https://img/C.png": c})
            ok, failed = _download(todo, web)
            self.assertEqual((sorted(ok), failed), (["chips/C.png", "profiles/B.png"], []))
            self.assertEqual(sorted(web.calls), ["https://img/B.png", "https://img/C.png"])  # A、W 不重下
            self.assertEqual(assets.missing(assets.Manifest(newer)), [])
            self.assertTrue(template.is_file())
            self.assertEqual([f.name for f in hero.user_dir().iterdir()], [template.name])
        self.assertEqual(self.names(new_app), [])

    def test_program_folder_images_not_moved(self):
        """程序文件夹 app\\assets 里已有的图片（放进个人目录以前的版本下的、退回期间下的）不读、不搬、不删：个人目录能用时照
        个人目录里的算缺几张，换过来时在个人目录里下载一次。"""
        a = _png(b"hero")
        item = _item("heroes", "A.png", a)
        old = self.app / "assets" / "heroes" / "A.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(a)
        record = json.dumps({"failed": {item.name: clock.today().isoformat()}})  # 程序文件夹里记着今天下载失败
        (self.app / "assets" / assets.LOCAL_MANIFEST).write_text(record, encoding="utf-8")
        manifest = assets.Manifest([item])
        self.assertEqual(assets.missing(manifest), [item])  # 个人目录里还没有；程序文件夹里的失败记号也不认
        ok, failed = _download([item], _Web({item.url: a}))
        self.assertEqual((ok, failed), ([item.name], []))
        self.assertEqual((self.images / "heroes" / "A.png").read_bytes(), a)
        self.assertEqual(old.read_bytes(), a)
        self.assertEqual((self.app / "assets" / assets.LOCAL_MANIFEST).read_text(encoding="utf-8"), record)
        self.assertEqual(sorted(p.relative_to(self.app).as_posix() for p in self.app.rglob("*") if p.is_file()),
                         sorted([f"assets/{assets.LOCAL_MANIFEST}", "assets/heroes/A.png"]))

    def test_bundled_stays_in_program_folder(self):
        """随发行版带的（bundled）在程序文件夹里：缺了算发行版不完整，不下载，也不到个人目录里找。"""
        bar = assets.AssetItem("stars", "star_bar.png", bundled=True, size=3)
        manifest = assets.Manifest([bar])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing_bundled(manifest), [bar])
        (self.images / "stars").mkdir(parents=True)
        (self.images / "stars" / "star_bar.png").write_bytes(b"bar")
        self.assertEqual(assets.missing_bundled(manifest), [bar])  # 个人目录里的不算
        (self.app / "assets" / "stars").mkdir(parents=True)
        (self.app / "assets" / "stars" / "star_bar.png").write_bytes(b"bar")
        self.assertEqual(assets.missing_bundled(manifest), [])


class ImportTimeTest(_Release):
    """导入时就算好的路径不会定在决定之前的位置：识别、画图标、画缩略图的模块都在模拟发行版之前导入了（开发版的位置），之后
    照样按发行版的位置（个人目录）找下载的识别图片：都在用到时才取。"""

    def test_modules_follow_download_dir(self):
        qtenv.app()  # 画筹码图标要有 QApplication
        db = self.cat.db
        self.assertFalse(hasattr(stars, "PHOTO_DIRS"))
        self.assertEqual(stars.photo_dirs(), (self.images / "profiles", self.images / "heroes"))
        self.assertEqual(hero.heroes_dir(), self.images / "heroes")
        self.assertTrue(all(p.parent == self.images / "chips" for p in skin_preview.scene_icons(db)))
        # 筹码面板：缺的文件按个人目录里的算
        first = chip_panel.icon_files(db)[0]
        (self.images / "chips").mkdir(parents=True)
        (self.images / "chips" / first).write_bytes(b"x")
        self.assertNotIn(first, chip_panel.missing_files(db))
        self.assertEqual(len(chip_panel.missing_files(db)), len(chip_panel.icon_files(db)) - 1)
        # 画筹码图标（悬浮窗的图标格子、编辑窗口的列表）：缺图时记下的文件都在个人目录里
        self.addCleanup(widgets._icon_cache.clear)
        widgets._icon_cache.clear()
        widgets.chip_icon("测试筹码", "蓝", "50010", 26)
        missing = [path for _img, files in widgets._icon_cache.values() for path in files]
        self.assertTrue(missing)
        self.assertTrue(all(Path(path).parent == self.images / "chips" for path in missing), missing)
        chip = next(c for c in db.chips.values() if c.rarity in widgets.RARITY_BASE and c.icon)
        icon = option_widgets._ChipIcon(chip, False, False, 40)
        self.addCleanup(icon.deleteLater)
        self.assertFalse(icon._ready())
        for name in (widgets.RARITY_BASE[chip.rarity], f"UT_Relic_{chip.icon}.png"):
            (self.images / "chips" / name).write_bytes(b"x")
        self.assertTrue(icon._ready())


class PreviewChildTest(_Release):
    def test_child_reads_only(self):
        """画缩略图的子进程（paths.use_program_dir）、主程序没交来位置时（直接起的子进程，按 _peek_downloads）：下载的识别图片
        只读，不建、不试写个人目录；个人目录里的 assets 还没有时照缺图的样子处理（读程序文件夹的，和主程序退回时一样）；主程序
        启动时建好了的，读个人目录里的。都没有退回的原因（子进程不提示）。"""
        with mock.patch.object(paths.tempfile, "mkstemp", side_effect=AssertionError("子进程不该试写")):
            paths.use_program_dir()
            self.assertEqual(paths.download_dir("chips"), self.app / "assets" / "chips")
            self.assertIsNone(paths.download_fallback())
            self.assertEqual(skin_preview.icons_present(self.cat.db), False)
        self.assertEqual(self.names(self.local), [])
        self.images.mkdir(parents=True)  # 主程序启动时试写、建好了
        paths.use_program_dir()
        self.assertEqual(paths.download_dir("chips"), self.images / "chips")
        self.assertIsNone(paths.download_fallback())
        self.assertEqual(self.names(self.images), [])
        self.assertEqual(paths.settings_dir(), self.app)  # 设置和方案的位置不变

    def test_child_reads_where_main_decided(self):
        """主程序起子进程时把这次运行决定好的位置交过去（paths.child_environ 写进 paths.DOWNLOADS_ENV），子进程只读那一处：
        个人目录在、却写不进、主程序退回了程序文件夹的 app\\assets 时，子进程读的也是 app\\assets，不另去读个人目录（没交来位置
        时按 _peek_downloads 读的是个人目录）。缩略图记下的「图标齐不齐」和主程序看到的一致：PreviewStore.needs 当次运行和下次
        启动都不再判要重画。子进程不试写、不建文件夹。"""
        qtenv.app()  # 预览图的登记、画筹码图标要有 QApplication
        db = self.cat.db
        self.images.mkdir(parents=True)  # 个人目录里的 assets 在（以前建好的），这次却写不进
        real = paths.tempfile.mkstemp

        def mkstemp(*args, dir=None, **kwargs):
            if Path(dir) == self.images:
                raise PermissionError(13, "拒绝访问", str(Path(dir) / "x.tmp"))
            return real(*args, dir=dir, **kwargs)

        def main_decides() -> None:
            with mock.patch.object(paths.tempfile, "mkstemp", side_effect=mkstemp):
                self.assertEqual(paths.download_dir(), self.app / "assets")  # 主程序退回了程序文件夹

        main_decides()
        chips = self.app / "assets" / "chips"
        chips.mkdir(parents=True)
        for f in skin_preview.scene_icons(db):  # 主程序把局面要用的筹码图标下在了 app\assets 里
            (chips / f.name).write_bytes(b"x")
        self.assertTrue(skin_preview.icons_present(db))
        env = paths.child_environ()
        self.assertEqual(env[paths.DOWNLOADS_ENV], str(self.app / "assets"))
        self.assertNotIn(paths.DOWNLOADS_ENV, os.environ)  # 只给子进程，本进程的环境不变
        # 子进程：环境变量是主程序给的
        with mock.patch.dict(os.environ, {paths.DOWNLOADS_ENV: env[paths.DOWNLOADS_ENV]}), \
                mock.patch.object(paths.tempfile, "mkstemp", side_effect=AssertionError("子进程不该试写")):
            paths.use_program_dir()
            self.assertEqual(paths.download_dir("chips"), chips)
            self.assertIsNone(paths.download_fallback())
            icons = skin_preview.icons_present(db)
            self.assertTrue(icons)
            # 没交来位置（直接起的子进程）时照 _peek_downloads：个人目录里的 assets 在，读的是它（那里没有图标）
            del os.environ[paths.DOWNLOADS_ENV]
            paths.use_program_dir()
            self.assertEqual(paths.download_dir("chips"), self.images / "chips")
            self.assertFalse(skin_preview.icons_present(db))
        self.assertEqual(self.names(self.images), [])  # 子进程一样都没写
        # 回到主程序：子进程交回的图标齐不齐记进登记表；主程序这边也是齐的，当次运行、下次启动都不再判要重画
        self.restart()
        main_decides()
        skin = theme.get_skin(theme.DEFAULT_KEY)
        image = QImage(36, 45, QImage.Format_ARGB32)
        image.fill(QColor("red"))
        png = base64.b64encode(skin_preview.png_bytes(image)).decode("ascii")

        def answered(folder: Path, child_icons: bool) -> skin_preview.PreviewStore:
            store = skin_preview.PreviewStore(db, folder=folder, wanted=lambda key: True)
            store._take(skin.key, {"done": skin.key, "sig": skin_preview.fingerprint(skin), "icons": child_icons,
                                   "png": png})
            return store

        folder = self.base / "skins"
        self.assertFalse(answered(folder, icons).needs(skin))
        self.assertFalse(skin_preview.PreviewStore(db, folder=folder, wanted=lambda key: True).needs(skin))
        # 子进程读个人目录（交回图标不齐）、主程序这边却齐了时，才会每次都判要重画：这正是交位置要避开的
        self.assertTrue(answered(self.base / "skins-peek", False).needs(skin))

    def test_passed_location_checked(self):
        """交来的位置为空、不是绝对路径时不用，照 _peek_downloads 判断（个人目录还没有：读程序文件夹的）；开发版的子进程不看它，
        照旧读项目的 assets（同 assets_dir）。"""
        for value in ("", "  ", "AppData\\Local\\AstralChipCompass\\assets"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {paths.DOWNLOADS_ENV: value}):
                paths.use_program_dir()
                self.assertEqual(paths.download_dir("chips"), self.app / "assets" / "chips")
        with mock.patch.dict(os.environ, {paths.DOWNLOADS_ENV: str(self.base / "elsewhere")}), \
                mock.patch.object(paths, "is_frozen", return_value=False):
            paths.use_program_dir()
            self.assertEqual(paths.download_dir("chips"), paths.assets_dir("chips"))
        self.assertEqual(self.names(self.local), [])


class DownloadDiagnosticsTest(_Release):
    """诊断包（state.json）记下识别图片放在哪（经 scrub_paths 写成 %LOCALAPPDATA%\\…）、有没有退回和原因；自检的「识别图片」
    一项按新位置数缺几张，结果里写上文件夹和退回的原因（打包试启动核对）。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(paths, "reports_dir", return_value=self.base / "reports")
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, out: Path) -> dict:
        return json.loads((out / "state.json").read_text(encoding="utf-8"))

    def test_state_json(self):
        out = diagnostics.export(None, {}, [], "")
        self.assertEqual(self.state(out)[diagnostics.IMAGES_STATE],
                         {"folder": f"%LOCALAPPDATA%\\{paths.FOLDER_NAME}\\assets", "fallback": None})
        self.restart()
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            out = diagnostics.export(None, {}, [], "")
        self.assertEqual(self.state(out)[diagnostics.IMAGES_STATE],
                         {"folder": diagnostics.scrub_paths(str(self.app / "assets")),
                          "fallback": "未设置环境变量 LOCALAPPDATA"})
        self.restart()
        with mock.patch.object(paths.tempfile, "mkstemp", side_effect=PermissionError(13, "拒绝访问")):
            reason = diagnostics.download_fallback_reason(paths.download_fallback())
        self.assertEqual(reason, f"无法写入 %LOCALAPPDATA%\\{paths.FOLDER_NAME}（PermissionError [Errno 13]）")
        self.assertNotIn(str(self.local), reason)

    def test_fallback_reasons_cover_every_kind(self):
        self.assertEqual(set(diagnostics.DOWNLOAD_FALLBACK_REASONS),
                         {paths.FALLBACK_NO_LOCALAPPDATA, paths.FALLBACK_UNWRITABLE})

    def test_self_check(self):
        real_data = ROOT / "data"
        with mock.patch.object(paths, "data_dir", return_value=real_data):
            manifest = assets.load_manifest()  # 开发目录的清单（假的程序文件夹里没有 data）
        downloads = [item for item in manifest.items if not item.bundled]
        self.assertTrue(downloads)

        def check() -> dict:
            with mock.patch.object(diagnostics, "_render_text", side_effect=RuntimeError("不画")), \
                    mock.patch.object(diagnostics, "window_capture_check", return_value={}), \
                    mock.patch.object(paths, "data_dir", return_value=real_data):
                return diagnostics.self_check()

        def images_item(result) -> str:
            return next(c for c in result["checks"] if c["name"] == "识别图片")["detail"]

        result = check()
        self.assertEqual((result["images_dir"], result["images_fallback"]), (str(self.images), None))
        self.assertEqual(images_item(result), f"缺 {len(downloads)} 张（首次运行时下载）")
        plain = next(item for item in downloads if item.crop is None)
        target = self.images / plain.dir / plain.file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * plain.size)
        self.assertEqual(images_item(check()), f"缺 {len(downloads) - 1} 张（首次运行时下载）")
        self.restart()
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            result = check()
        self.assertEqual((result["images_dir"], result["images_fallback"]),
                         (str(self.app / "assets"), "未设置环境变量 LOCALAPPDATA"))


class SandboxLocalAppDataTest(unittest.TestCase):
    """测试一律不碰真的 %LOCALAPPDATA%\\AstralChipCompass：测试沙箱把 LOCALAPPDATA 指进沙箱（子进程照样继承），真实的那一处记下
    指纹、测试进程结束时核对（tests/support/sandbox.py）。模拟发行版又没换 LOCALAPPDATA 的测试，下载的识别图片也只落在沙箱里。"""

    def test_localappdata_in_sandbox(self):
        self.assertIn(sandbox.RUN, Path(os.environ["LOCALAPPDATA"]).parents)
        self.assertIn(sandbox.RUN, paths.local_dir().parents)
        real = os.environ.get(sandbox.REAL_LOCALAPPDATA_ENV, "")
        if real:
            self.assertNotIn(sandbox.RUN, Path(real).parents)  # 记下的是外面真实的那一份
            if sandbox.WATCH is not None and Path(real).is_absolute():
                self.assertIn(Path(real) / paths.FOLDER_NAME, sandbox.WATCH.spots)

    def test_release_without_patching_lands_in_sandbox(self):
        paths._reset_for_tests()
        self.addCleanup(paths._reset_for_tests)
        with mock.patch.object(paths, "is_frozen", return_value=True):
            folder = paths.download_dir()
        self.assertIn(sandbox.RUN, folder.parents)
        real = os.environ.get(sandbox.REAL_LOCALAPPDATA_ENV, "")
        if real:
            self.assertNotIn(Path(real), folder.parents)


if __name__ == "__main__":
    unittest.main()
