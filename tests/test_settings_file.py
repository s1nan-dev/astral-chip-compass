"""设置文件 state.json 的读写（advisor/prefs.py 的 Settings.read、Settings.save、_write_settings，悬浮窗的 UiSettings、
load_settings）：写的时候刷进硬盘、原来的内容留作 state.json.bak（原来的内容读不出时不拿它盖掉 .bak）；读坏了另存成
.broken-时间、改读备份并用它恢复，没有能用的备份时按默认设置；某一项的类型不对只这一项按默认；读不了、坏文件没能另存时这次
运行不写这个文件；读坏了、备份又没能用上时第一次保存前把备份另存为 .bak.orphan-时间；save 写成时返回存进的文件、没写成时
返回 False，写不进以后同样的内容不在调用的线程里再试、交给后台到点自动再试，警告只记第一次，退出时强制再存一次（force，
main()）；新用户按读的结果判断；热键写成数字时设置窗口照常打开；解锁记录解不开、这次又读不到 MachineGuid 时不盖掉它，也照
原样写回读到的皮肤。文件被占用时交给后台的情形另见 tests/test_settings_background.py。

文件都写在测试沙箱的临时文件夹里。读不了、另存不成、恢复不成用 mock 造（换掉 advisor.fileio 里的函数），不真的去占用文件。
"""
import json
import os
import stat
import sys
import threading
import time
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import fileio, prefs, unlock  # prefs：设置的读写（替换 _write_settings 等要换这个模块里的）
from advisor.data import load_db
from advisor.session import GAME_FIELDS, Session, Settings
from advisor.ui.overlay_parts import app as overlay_app
from advisor.ui import lang
from advisor.ui import settings as st
from advisor.ui import single_instance as si
from advisor.ui import theme
from advisor.ui import overlay as overlay_mod
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import RESTART_FAILED
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings, load_settings
from advisor.ui.services import Services
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tests.support.widgets import dispose

APP = qtenv.app()
U = unlock

# 程序自己写出的一份设置（各项都是合法的值；和 0.12.0 起写出的格式一样）
GOOD = {"hero": "芬妮", "window_pos": [1446, 68],
        "report_enabled": True, "report_install": "0123456789abcdef0123456789abcdef",
        "report_secret": "fedcba9876543210fedcba9876543210",
        "report_since": "2026-01-02T03:04:05",
        "manual": True, "hotkey": "Ctrl+F9",
        "window_rel": [0.7293, 0.0431, "tr"], "collapsed_cards": ["owned"],
        "share_format": "text", "opaque": True,
        "skin": theme.DEFAULT_KEY, "aux": "", "free_notice_closed": True, "hant_notice_closed": False,
        "lowend": True, "big_cores": False, "platform": "taptap",
        "platform_seen": "taptap", "ui_lang": lang.HANS, "lanes": {}, "update_auto": False,
        "update_skipped": "1.0.1", "ocr_check": {}}


def filled_lang() -> str:
    """load_settings 给没选过的界面语言填的值：繁体界面打开（lang.HANT_ENABLED）时是这次启动的语言，关着时不填（空串）。"""
    return lang.CURRENT if lang.HANT_ENABLED else ""


def dump(data: dict) -> bytes:
    """照程序写设置文件的写法（json.dumps 缩进 1、Windows 的换行）。"""
    return json.dumps(data, ensure_ascii=False, indent=1).replace("\n", os.linesep).encode("utf-8")


def _wait_for(condition, timeout: float = 5.0) -> bool:
    """等到 condition() 为真（后台写的线程在另一个线程里写），超时返回假。"""
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "test-settings-file-")
        self.path = self.dir / "state.json"
        self.bak = self.dir / "state.json.bak"

    def names(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def broken_copies(self, name: str = "state.json") -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.name.startswith(f"{name}.broken-"))

    def read_only(self, path: Path) -> None:
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(lambda: path.exists() and os.chmod(path, stat.S_IREAD | stat.S_IWRITE))

    def no_delays(self) -> None:
        """写盘工具被占用时的重试不等（只读、写不进的这几条用不着等）。"""
        patcher = mock.patch.object(fileio, "RETRY_DELAYS", (0.0,) * len(fileio.RETRY_DELAYS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def own_writer(self) -> prefs.SettingsWriter:
        """这条测试自己的一份设置写盘（prefs.WRITER）：排着的、退避的不和别的测试混；测试完限时写完排着的（登记在 read_only
        之前，文件改回能写以后才写）。"""
        writer = prefs.SettingsWriter()
        patcher = mock.patch.object(prefs, "WRITER", writer)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(writer.flush, 3.0)
        return writer


# ---------------------------------------------------------------- 读


class ReadTest(_Case):
    def test_good_file_read_and_not_rewritten(self):
        """程序写出的设置文件：各项照读；原样再存不写（文件逐字节不变、不建 .bak），返回这个文件。"""
        self.path.write_bytes(dump(GOOD))
        settings, existed = UiSettings.read(self.path)
        self.assertTrue(existed)
        self.assertEqual({k: v for k, v in asdict(settings).items() if k in GOOD}, GOOD)
        stamp = self.path.stat().st_mtime_ns
        time.sleep(0.02)
        self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual(self.path.read_bytes(), dump(GOOD))
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.names(), ["state.json"])

    def test_bad_field_only_that_field(self):
        """某一项的类型不对（热键写成数字、模式写成文字）：只这一项按默认，其余照用，记警告；保存时那几项写成默认值，
        原来的文件留作 .bak。"""
        raw = dict(GOOD, hotkey=8, manual="yes")
        original = dump(raw)
        self.path.write_bytes(original)
        with self.assertLogs("advisor.prefs", level="WARNING") as logs:
            settings = UiSettings.load(self.path)
        self.assertIn("manual、hotkey", "\n".join(logs.output))
        self.assertEqual((settings.hotkey, settings.manual), (None, False))
        self.assertEqual({k: v for k, v in asdict(settings).items() if k in GOOD and k not in ("hotkey", "manual")},
                         {k: v for k, v in GOOD.items() if k not in ("hotkey", "manual")})
        self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), dict(GOOD, hotkey=None, manual=False))
        self.assertEqual(self.bak.read_bytes(), original)

    def test_every_field_checked(self):
        """每一项（对局状态以外）写成 JSON 对象（任何设置项都不该是对象）时都按默认：以后新加的设置项用了 _fits 不认识的
        类型时，这一条会报出来。另有几种容易混的：true 不算数字、数字不算 true / false、列表里混了别的类型。"""
        names = [k for k in UiSettings.__dataclass_fields__ if k not in GAME_FIELDS]
        self.path.write_text(json.dumps({k: {"x": 1} for k in names}), encoding="utf-8")
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertEqual(UiSettings.load(self.path), UiSettings())
        cases = {"opaque": 1, "free_notice_closed": 0, "hant_notice_closed": "yes", "manual": "true",
                 "window_pos": [True, 2], "hero": None,
                 "hotkey": ["F8"], "skin": 3, "aux": 7, "share_format": 1, "collapsed_cards": "owned", "lowend": 1,
                 "platform": 1, "platform_seen": ["steam"],
                 "update_auto": 1, "update_skipped": ["1.0.1"]}
        for name, value in cases.items():
            with self.subTest(name=name):
                self.path.write_text(json.dumps({"hero": "邦妮", name: value}), encoding="utf-8")
                with self.assertLogs("advisor.prefs", level="WARNING"):
                    got = UiSettings.load(self.path)
                self.assertEqual(getattr(got, name), getattr(UiSettings(), name) if name != "hero" else "凛")
                if name != "hero":
                    self.assertEqual(got.hero, "邦妮")  # 其余照用

    def test_missing_fields(self):
        """缺项（0.12.0 的旧文件、手改删掉了几项）：没写的项按默认，写了的照用，不记警告；保存时补上缺的项。"""
        self.path.write_text(json.dumps({"hero": "芬妮", "hotkey": "F9"}), encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            settings = UiSettings.load(self.path)
        self.assertEqual(settings, UiSettings(hero="芬妮", hotkey="F9"))
        self.assertEqual(settings.save(self.path), self.path)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((saved["hero"], saved["hotkey"], saved["manual"]), ("芬妮", "F9", False))

    def test_float_numbers_kept(self):
        """数字写成了浮点（手改过、别的工具写的）：读得出、不记警告（用到的地方自己取整），原样再存
        不重写；32 位整数的边上照读。NaN、无穷大（JSON 的 NaN、Infinity、1e400）、超出 32 位整数的（交给 Qt 时报
        OverflowError，启动时就出错）按默认。"""
        raw = dict(GOOD, window_pos=[1446.0, 68.5])
        self.path.write_bytes(dump(raw))
        with self.assertNoLogs("advisor", level="WARNING"):
            settings = UiSettings.load(self.path)
        self.assertEqual(settings.window_pos, [1446.0, 68.5])
        self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual((self.path.read_bytes(), self.names()), (dump(raw), ["state.json"]))
        self.path.write_text('{"window_pos": [-2147483648, 2147483647.0]}', encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            self.assertEqual(UiSettings.load(self.path).window_pos, [-2147483648, 2147483647.0])
        for text in ("[NaN, 68]", "[1446, Infinity]", "[1e400, 68]", "[2147483648, 68]", "[3e9, 68]",
                     "[1446, -2147483649]"):
            with self.subTest(text=text):
                self.path.write_text('{"hero": "邦妮", "window_pos": %s}' % text, encoding="utf-8")
                with self.assertLogs("advisor.prefs", level="WARNING"):
                    got = UiSettings.load(self.path)
                self.assertEqual((got.hero, got.window_pos), ("邦妮", None))

    def test_lenient_values_kept(self):
        """类型对、值看不懂的照读（各用到的地方自己兜底）：没登记的皮肤、看不懂的窗口位置、不认识的卡片名。"""
        raw = {"window_rel": [1, 2], "collapsed_cards": ["owned", "以后的卡片"], "hotkey": "Ctrl+Foo"}
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        got = UiSettings.load(self.path)
        self.assertEqual((got.window_rel, got.collapsed_cards, got.hotkey), ([1, 2], ["owned", "以后的卡片"], "Ctrl+Foo"))

    def test_broken_file_uses_backup(self):
        """断电后设置文件整份变成零字节：坏文件另存成 .broken-时间，改读 .bak 并用它恢复设置文件；原来有设置文件（不算新用户）；
        这次运行照常写。"""
        UiSettings(hero="凛", hotkey="F7").save(self.path)
        UiSettings(hero="芬妮", hotkey="F9").save(self.path)
        bak = self.bak.read_bytes()
        size = self.path.stat().st_size
        self.path.write_bytes(b"\0" * size)
        with self.assertLogs("advisor.fileio", level="WARNING"):
            settings, existed = UiSettings.read(self.path)
        self.assertTrue(existed)  # 不算新用户
        self.assertEqual((settings.hero, settings.hotkey), ("凛", "F7"))
        self.assertEqual(self.path.read_bytes(), bak)  # 用备份恢复了
        self.assertEqual([p.read_bytes() for p in self.broken_copies()], [b"\0" * size])
        settings.hero = "邦妮"
        self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual(UiSettings.load(self.path).hero, "邦妮")

    def test_broken_file_without_backup(self):
        """读坏了、没有备份（不是 JSON、不是 JSON 对象）：坏文件另存走，按默认设置；仍算原来有设置文件（「已有筹码」卡片不按新用户
        收起）；之后照常保存。"""
        for label, data in {"不是 JSON": b'{"hero": "\xe8\x8a', "不是对象": b"[1, 2, 3]", "空文件": b""}.items():
            with self.subTest(label=label):
                for p in self.dir.iterdir():
                    p.unlink()
                self.path.write_bytes(data)
                with self.assertLogs("advisor.fileio", level="WARNING"):
                    settings = load_settings(self.path)
                # collapsed_cards 是 []：不是新用户；界面语言没选过，按这次启动的语言（繁体界面关着时不填）
                self.assertEqual(settings, UiSettings(ui_lang=filled_lang()))
                self.assertFalse(self.path.exists())
                self.assertEqual([p.read_bytes() for p in self.broken_copies()], [data])
                self.assertEqual(settings.save(self.path), self.path)
                self.assertEqual(UiSettings.load(self.path), UiSettings(ui_lang=filled_lang()))

    def test_broken_file_and_backup(self):
        """设置文件和备份都读坏了：两份都另存（以后的保存会写新的 .bak，不能盖掉它），按默认设置。"""
        self.path.write_bytes(b"x")
        self.bak.write_bytes(b"y")
        with self.assertLogs("advisor.fileio", level="WARNING"):
            self.assertEqual(UiSettings.load(self.path), UiSettings())
        self.assertEqual([p.read_bytes() for p in self.broken_copies()], [b"x"])
        self.assertEqual([p.read_bytes() for p in self.broken_copies("state.json.bak")], [b"y"])
        self.assertFalse(self.bak.exists())

    def test_new_user_only_without_file(self):
        """新用户（没有设置文件）：「已有筹码」卡片默认收起；文件不在、只剩 .bak 时同样按新用户（不读备份）。"""
        self.assertEqual(load_settings(self.path).collapsed_cards, ["owned"])
        self.bak.write_bytes(dump(GOOD))
        self.assertEqual(load_settings(self.path), UiSettings(collapsed_cards=["owned"], ui_lang=filled_lang()))
        self.assertEqual(self.bak.read_bytes(), dump(GOOD))

    def test_unreadable_file_not_written(self):
        """文件在、读不了（被占用、没有权限）：按默认设置（不算新用户），这次运行不写这个文件：save 直接返回 False，不再记警告，
        不碰文件（不然默认设置会盖掉读不了的那份）。"""
        self.path.write_bytes(dump(GOOD))
        with mock.patch.object(fileio, "read_with_backup", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor.prefs", level="WARNING"):
            settings = load_settings(self.path)
        self.assertEqual(settings, UiSettings(ui_lang=filled_lang()))
        settings.hero = "邦妮"
        with self.assertNoLogs("advisor", level="WARNING"), \
                mock.patch.object(prefs, "_write_settings", wraps=prefs._write_settings) as write:
            for _ in range(3):
                self.assertIs(settings.save(self.path), False)
        write.assert_not_called()
        self.assertEqual(self.path.read_bytes(), dump(GOOD))
        # 别的文件照常能存（只管读不了的那一个）
        other = self.dir / "other.json"
        self.assertEqual(settings.save(other), other)

    def test_broken_file_not_set_aside_is_not_written(self):
        """读坏了、坏文件没能另存（被占用等）、也没有备份：按默认设置，这次运行不写（坏文件没留下一份就会被盖掉）。"""
        self.path.write_bytes(b"\0" * 40)
        with mock.patch.object(fileio, "move_aside", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor", level="WARNING"):
            settings, existed = UiSettings.read(self.path)
        self.assertEqual((settings, existed), (UiSettings(), True))
        self.assertIs(settings.save(self.path), False)
        self.assertEqual(self.path.read_bytes(), b"\0" * 40)

    def test_broken_file_not_kept_uses_backup_values(self):
        """读坏了、坏文件没能留一份、备份能用：这次运行照备份的设置，但不写（坏文件和备份都不动）。"""
        UiSettings(hero="凛").save(self.path)
        UiSettings(hero="芬妮").save(self.path)
        bak = self.bak.read_bytes()
        self.path.write_bytes(b"{")
        with mock.patch.object(fileio, "_keep_copy", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor", level="WARNING"):
            settings = UiSettings.load(self.path)
        self.assertEqual(settings.hero, "凛")
        self.assertIs(settings.save(self.path), False)
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes()), (b"{", bak))
        self.assertEqual(self.broken_copies(), [])

    def test_restore_failed_keeps_backup(self):
        """读坏了、用备份恢复设置文件没成（坏文件已经留了一份）：照备份的设置；下次保存照常写，坏内容不拿去盖掉 .bak。"""
        UiSettings(hero="凛").save(self.path)
        UiSettings(hero="芬妮").save(self.path)
        bak = self.bak.read_bytes()
        self.path.write_bytes(b"{")
        with mock.patch.object(fileio, "write_bytes", side_effect=OSError(28, "磁盘已满")), \
                self.assertLogs("advisor.fileio", level="WARNING"):
            settings = UiSettings.load(self.path)
        self.assertEqual(settings.hero, "凛")
        self.assertEqual(self.path.read_bytes(), b"{")  # 没恢复成
        self.assertEqual([p.read_bytes() for p in self.broken_copies()], [b"{"])
        settings.hero = "邦妮"
        with self.assertLogs("advisor.fileio", level="WARNING"):  # 原来的内容读不出，不拿它盖掉备份
            self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual(UiSettings.load(self.path).hero, "邦妮")
        self.assertEqual(self.bak.read_bytes(), bak)


class PlatformFieldTest(_Case):
    """游戏平台两项（platform、platform_seen）的读写：默认、读到的值、旧文件没有这两项、读到别的值（设置文件被改坏）。"""

    def test_defaults(self):
        self.assertIsNone(UiSettings().platform)  # 没选过：自动识别
        self.assertEqual(UiSettings().platform_seen, "steam")  # 从没认出过：Steam

    def test_same_values_as_capture(self):
        """设置文件认的取值（overlay_parts/app.py 的 PLATFORMS，那里不导入 capture，单写了一份）和找窗口那一层的端名
        相同：加了新的端只改了 capture 时，选了新的端存不进设置文件（读回来按自动识别）。"""
        from advisor.vision import capture

        self.assertEqual(overlay_app.PLATFORMS, (capture.STEAM, capture.TAPTAP, capture.BOTH))
        self.assertEqual((overlay_app.STEAM, overlay_app.TAPTAP, overlay_app.BOTH),
                         (capture.STEAM, capture.TAPTAP, capture.BOTH))

    def test_read_and_write(self):
        for platform, seen in ((None, "steam"), ("steam", "taptap"), ("taptap", "steam"), ("both", "steam")):
            with self.subTest(platform=platform, seen=seen):
                self.path.write_text(json.dumps({"platform": platform, "platform_seen": seen}), encoding="utf-8")
                got = UiSettings.load(self.path)
                self.assertEqual((got.platform, got.platform_seen), (platform, seen))
                self.assertEqual(got.save(self.path), self.path)
                again = UiSettings.load(self.path)
                self.assertEqual((again.platform, again.platform_seen), (platform, seen))

    def test_missing_fields(self):
        """旧设置文件没有这两项：按默认（自动识别、Steam），不记警告。"""
        self.path.write_text(json.dumps({"hero": "芬妮", "lowend": True}), encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            got = UiSettings.load(self.path)
        self.assertEqual((got.platform, got.platform_seen), (None, "steam"))

    def test_bad_values(self):
        """类型对、值不认识的按默认：platform 只认 capture 的三种，别的按自动识别；platform_seen 别的按 Steam。"""
        for raw, want in (("bogus", None), ("", None), ("Steam", None), (1, None), (True, None), (["steam"], None),
                          ("steam", "steam"), ("taptap", "taptap"), ("both", "both")):
            with self.subTest(raw=raw):
                self.path.write_text(json.dumps({"platform": raw}), encoding="utf-8")
                with self.assertLogs("advisor.prefs", level="WARNING") if not isinstance(raw, str) else \
                        self.assertNoLogs("advisor", level="WARNING"):
                    self.assertEqual(UiSettings.load(self.path).platform, want)
        for raw, want in (("bogus", "steam"), ("", "steam"), ("Steam端", "steam"), (2, "steam"), ("taptap", "taptap")):
            with self.subTest(seen=raw):
                self.path.write_text(json.dumps({"platform_seen": raw}), encoding="utf-8")
                with self.assertLogs("advisor.prefs", level="WARNING") if not isinstance(raw, str) else \
                        self.assertNoLogs("advisor", level="WARNING"):
                    self.assertEqual(UiSettings.load(self.path).platform_seen, want)


class OrphanBackupTest(_Case):
    """设置文件读坏了、坏文件已另存走，备份却没能用上而还在原处（读的时候被占用；或者也读坏了、又没能另存）：第一次保存前
    把备份另存为 state.json.bak.orphan-时间（同方案文件），之后的保存不会把它盖掉。用户自己删了设置文件、只剩备份的照旧
    按新用户，不另存。"""

    def setUp(self):
        super().setUp()
        UiSettings(hero="凛", hotkey="F7").save(self.path)
        UiSettings(hero="芬妮", hotkey="F9").save(self.path)
        self.good = self.bak.read_bytes()  # 备份里是还能用的设置（凛、F7）

    def orphans(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.name.startswith("state.json.bak.orphan-"))

    def bak_locked(self):
        """读的时候备份被占用。"""
        real = fileio._read

        def read(path):
            if path == self.bak:
                raise PermissionError(13, "拒绝访问")
            return real(path)

        return mock.patch.object(fileio, "_read", read)

    def bak_stays(self):
        """读坏的备份另存不成，还在原处。"""
        real = fileio.move_aside

        def move(path, tag=fileio.BROKEN, **kw):
            if path == self.bak:
                raise PermissionError(13, "拒绝访问")
            return real(path, tag, **kw)

        return mock.patch.object(fileio, "move_aside", move)

    def read_broken(self, patcher) -> UiSettings:
        """设置文件读坏了（断电后的零字节），读的时候 patcher 让备份用不上：按默认设置，不算新用户。"""
        self.path.write_bytes(b"\0" * 30)
        with patcher, self.assertLogs("advisor", level="WARNING"):
            settings, existed = UiSettings.read(self.path)
        self.assertEqual((settings, existed), (UiSettings(), True))
        return settings

    def test_backup_kept_before_first_save(self):
        """备份读不了、或也读坏了却没能另存：第一次保存前把它另存为 .bak.orphan-时间，再存几次都不会盖掉它；之后照常留上一版
        作 .bak。"""
        cases = {"备份读不了": (self.good, self.bak_locked), "备份也读坏了、没能另存": (b"\0" * 20, self.bak_stays)}
        for label, (backup, patcher) in cases.items():
            with self.subTest(label=label):
                for p in self.dir.iterdir():
                    p.unlink()
                self.bak.write_bytes(backup)
                settings = self.read_broken(patcher())
                settings.hero = "邦妮"
                with self.assertLogs("advisor.prefs", level="WARNING"):  # 另存了备份
                    self.assertEqual(settings.save(self.path), self.path)
                first = self.path.read_bytes()
                settings.hotkey = "F10"
                self.assertEqual(settings.save(self.path), self.path)
                self.assertEqual([p.read_bytes() for p in self.orphans()], [backup])  # 原来的备份留着，没被盖掉
                self.assertEqual([p.read_bytes() for p in self.broken_copies()], [b"\0" * 30])
                self.assertEqual(self.bak.read_bytes(), first)
                again = UiSettings.load(self.path)
                self.assertEqual((again.hero, again.hotkey), ("邦妮", "F10"))

    def test_name_taken(self):
        """另存的名字已经有了（同一秒里又遇到一次）：接 -2，不覆盖已有的。"""
        name = "state.json.bak.orphan-20261010-120000"
        (self.dir / name).write_bytes(b"earlier")
        settings = self.read_broken(self.bak_locked())
        with mock.patch.object(fileio, "datetime") as clock, self.assertLogs("advisor.prefs", level="WARNING"):
            clock.now.return_value = datetime(2026, 10, 10, 12, 0, 0)
            settings.save(self.path)
        self.assertEqual((self.dir / name).read_bytes(), b"earlier")
        self.assertEqual((self.dir / f"{name}-2").read_bytes(), self.good)

    def test_set_aside_fails(self):
        """备份另存不成（被占用等）：照常写设置文件，但不碰 .bak；SETTINGS_RETRY_S 秒内的保存不再去另存（备份一直被占用时
        不至于每存一次都等约 1 秒）、同样不碰 .bak，警告只记一次；过了这段时间再试，另存好了以后照常留上一版作 .bak。"""
        settings = self.read_broken(self.bak_locked())
        with mock.patch.object(fileio, "move_aside", side_effect=PermissionError(13, "拒绝访问")) as move, \
                self.assertLogs("advisor.prefs", level="WARNING") as logs:
            settings.hero = "邦妮"
            self.assertEqual(settings.save(self.path), self.path)
            settings.hotkey = "F10"
            self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual((move.call_count, len(logs.output)), (1, 1))
        self.assertEqual((self.bak.read_bytes(), self.orphans()), (self.good, []))  # 两次保存都没碰它
        second = self.path.read_bytes()
        later = time.monotonic() + prefs.SETTINGS_RETRY_S + 1
        settings.hotkey = "F11"
        with mock.patch.object(prefs.time, "monotonic", return_value=later), \
                self.assertLogs("advisor.prefs", level="WARNING"):  # 另存好了
            self.assertEqual(settings.save(self.path), self.path)
        self.assertEqual([p.read_bytes() for p in self.orphans()], [self.good])
        self.assertEqual(self.bak.read_bytes(), second)

    def test_user_deleted_file_not_kept(self):
        """用户自己删了设置文件、只剩备份：按新用户，不读也不另存备份（之后的保存照常换掉它）。"""
        self.path.unlink()
        settings = load_settings(self.path)
        self.assertEqual(settings.collapsed_cards, ["owned"])  # 新用户
        settings.hero = "邦妮"
        settings.save(self.path)
        settings.hotkey = "F10"
        settings.save(self.path)
        self.assertEqual(self.orphans(), [])
        self.assertNotEqual(self.bak.read_bytes(), self.good)


# ---------------------------------------------------------------- 写


class SaveTest(_Case):
    def test_backup_is_previous_version(self):
        """第一次存（原来没有文件）不建 .bak；之后每次真写时，原来的内容留作 .bak（只留一份：上一个版本）。"""
        self.assertEqual(Settings(hero="凛").save(self.path), self.path)
        self.assertEqual(self.names(), ["state.json"])
        first = self.path.read_bytes()
        Settings(hero="芬妮").save(self.path)
        self.assertEqual(self.bak.read_bytes(), first)
        second = self.path.read_bytes()
        Settings(hero="邦妮").save(self.path)
        self.assertEqual(self.bak.read_bytes(), second)
        self.assertEqual(self.names(), ["state.json", "state.json.bak"])

    def test_flushed_to_disk(self):
        """写的时候刷进硬盘（os.fsync）再替换：蓝屏、断电后不会整份变成零字节。"""
        with mock.patch.object(os, "fsync", wraps=os.fsync) as sync:
            Settings(hero="芬妮").save(self.path)
        self.assertGreaterEqual(sync.call_count, 1)
        self.assertEqual(Settings.load(self.path).hero, "芬妮")

    def test_same_text_other_newlines_not_rewritten(self):
        """文件里的文字和要写的相同、只是换行不同（别的工具改过、0.12.0 写的 \\n）：照旧不重写。"""
        data = {k: v for k, v in asdict(Settings(hero="芬妮")).items() if k not in GAME_FIELDS}
        text = json.dumps(data, ensure_ascii=False, indent=1)
        self.path.write_bytes(text.encode("utf-8"))  # \n 换行
        before = self.path.read_bytes()
        self.assertEqual(Settings(hero="芬妮").save(self.path), self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.names(), ["state.json"])

    def test_externally_broken_file_does_not_replace_backup(self):
        """运行中设置文件被外面改坏了：下次保存照常写好，坏内容不拿去盖掉 .bak（.bak 留着上一份还能用的）。"""
        Settings(hero="凛").save(self.path)
        Settings(hero="芬妮").save(self.path)
        bak = self.bak.read_bytes()
        for broken in (b"\0" * 10, b"not json"):  # 断电后的零字节（不记警告）、改成了别的内容
            with self.subTest(broken=broken):
                self.path.write_bytes(broken)
                with mock.patch.object(fileio.log, "warning") as warned:
                    self.assertEqual(Settings(hero="邦妮").save(self.path), self.path)
                self.assertEqual(warned.call_count, 0 if broken.startswith(b"\0") else 1)
                self.assertEqual((Settings.load(self.path).hero, self.bak.read_bytes()), ("邦妮", bak))

    def test_returns_path_or_false(self):
        """返回值：写成（写进去了、内容没变不用写）时是存进的文件；写不进、改存也不成时是 False（不会是 True），记警告。"""
        self.assertEqual(Settings(hero="芬妮").save(self.path), self.path)  # 新建
        self.assertEqual(Settings(hero="芬妮").save(self.path), self.path)  # 内容没变
        self.assertEqual(Settings(hero="凛").save(str(self.path)), self.path)  # 给的是文字也返回 Path
        self.no_delays()
        self.read_only(self.path)
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(Settings(hero="邦妮").save(self.path), False)
        self.assertEqual(Settings.load(self.path).hero, "凛")

    def test_failed_save_not_retried_at_once(self):
        """写不进以后再存同样的内容：不在调用的线程里再试，直接返回 False，警告只记第一次；后台隔 SETTINGS_RETRY_S 秒自动再试
        （不用再存一次），还是不成时不再记警告，写得进了就写进去；之后再写不进时重新记警告。"""
        writer = self.own_writer()
        Settings(hero="凛").save(self.path)
        self.no_delays()
        self.read_only(self.path)
        settings = Settings(hero="芬妮")
        here, real, calls = threading.get_ident(), prefs._write_settings, []

        def counted(path, text, **kwargs):  # 记下每次试写是不是在调用 save 的线程里
            calls.append(threading.get_ident() == here)
            return real(path, text, **kwargs)

        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 0.05), \
                mock.patch.object(prefs, "_write_settings", side_effect=counted):
            with self.assertLogs("advisor.prefs", level="WARNING") as logs:
                self.assertIs(settings.save(self.path), False)
                self.assertIs(settings.save(self.path), False)  # 同样的内容：不在这里再试
                self.assertEqual(calls, [True])
                self.assertTrue(_wait_for(lambda: calls.count(False) >= 2))  # 后台到点再试了两次，还是不成
            self.assertEqual(len(logs.output), 1)
            os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
            self.assertTrue(_wait_for(lambda: Settings.load(self.path).hero == "芬妮"))  # 写得进了：后台写进去
            self.assertTrue(_wait_for(lambda: writer.pending() == []))
            self.assertEqual(calls.count(True), 1)
            self.read_only(self.path)
            settings.hero = "邦妮"
            with self.assertLogs("advisor.prefs", level="WARNING"):  # 新的一回写不进：照记警告
                self.assertIs(settings.save(self.path), False)

    def test_lone_surrogate_kept(self):
        """文件里有单独的代理项（\\udXXX 转义）：保存不抛错，写回同一个转义，读回来不变；再存不重写。"""
        self.path.write_text('{"hero": "芬妮", "hotkey": "F\\ud800"}', encoding="utf-8")
        settings = UiSettings.load(self.path)
        self.assertEqual(settings.hotkey, "F\ud800")
        settings.hero = "凛"
        self.assertEqual(settings.save(self.path), self.path)
        self.assertIn('"F\\ud800"', self.path.read_text(encoding="utf-8"))
        again = UiSettings.load(self.path)
        self.assertEqual((again.hero, again.hotkey), ("凛", "F\ud800"))
        stamp = self.path.stat().st_mtime_ns
        time.sleep(0.02)
        self.assertEqual(again.save(self.path), self.path)
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)


class ForceSaveTest(_Case):
    """强制保存（Settings.save 的 force，Session.save 透传；退出时用）：写不进以后 SETTINGS_RETRY_S 秒内也照常去写，写不进
    照旧返回 False；读的时候读不了的文件照旧不写；force 只能按关键字传。"""

    def test_force_skips_retry_wait(self):
        Settings(hero="凛").save(self.path)
        self.no_delays()
        self.read_only(self.path)
        settings = Settings(hero="芬妮")
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(settings.save(self.path), False)
        with self.assertNoLogs("advisor.prefs", level="WARNING"):
            self.assertIs(settings.save(self.path, force=True), False)  # 还是写不进：照旧 False，不再记警告
        os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
        self.assertIs(settings.save(self.path), False)  # 不强制：这段时间里不去试
        self.assertEqual(Settings.load(self.path).hero, "凛")
        self.assertEqual(settings.save(self.path, force=True), self.path)
        self.assertEqual(Settings.load(self.path).hero, "芬妮")
        with self.assertRaises(TypeError):
            settings.save(self.path, True)  # 只能按关键字传

    def test_force_keeps_unreadable_file(self):
        """读的时候读不了的文件（这次运行不写它），强制也不写：不拿默认设置盖掉它。"""
        self.path.write_bytes(dump(GOOD))
        with mock.patch.object(fileio, "read_with_backup", side_effect=PermissionError(13, "拒绝访问")), \
                self.assertLogs("advisor.prefs", level="WARNING"):
            settings = load_settings(self.path)
        self.assertIs(settings.save(self.path, force=True), False)
        self.assertEqual(self.path.read_bytes(), dump(GOOD))

    def test_session_passes_force(self):
        """Session.save(force=True)：写不进、交给后台退避着的内容（平时的 save 内容没变时这时不去试），强制保存时当场再试、
        存进去，排着的那份不再写；退避当中改了设置的，平时的 save 也当场再试一次（不等）。"""
        writer = self.own_writer()
        Settings(hero="凛").save(self.path)
        session = Session(load_db(), Settings.load(self.path), self.path)
        session.settings.hero = "芬妮"
        self.no_delays()
        self.read_only(self.path)
        with self.assertLogs("advisor.prefs", level="WARNING"):
            session.save()
        os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
        session.save()
        self.assertEqual(Settings.load(self.path).hero, "凛")  # 内容没变、在退避：不当场再试
        session.save(force=True)
        self.assertEqual(Settings.load(self.path).hero, "芬妮")
        self.assertEqual(writer.pending(), [])
        self.read_only(self.path)
        session.settings.window_pos = [10, 20]
        with self.assertLogs("advisor.prefs", level="WARNING"):  # 新的一回写不进
            session.save()
        os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
        session.settings.window_pos = [30, 40]
        session.save()  # 退避当中改了设置：当场再试一次，写得进就存进去
        got = Settings.load(self.path)
        self.assertEqual((got.hero, got.window_pos), ("芬妮", [30, 40]))
        self.assertEqual(writer.pending(), [])


class ExitSaveTest(unittest.TestCase):
    """main() 退出时强制再存一次设置（Session.save(force=True)）：设置写不进以后那段时间里改的，退出前存进去。后台识别万一
    一直占着锁，等一会儿拿不到就不存（不和它同时写同一个文件），记进错误记录。main() 用到的界面、识别、热键、等待线程全换成
    假的（同 tests/test_exit_flow.py 的进程内那条）。"""

    def run_main(self, session, lock) -> None:
        main_mod = sys.modules[overlay_app.main.__module__]
        app = mock.Mock()
        app.exec.return_value = 0
        watcher = mock.MagicMock()  # 假的识别线程：按已经停下回答
        watcher.stop.return_value = True
        watcher.isRunning.return_value = False
        watcher.wait.return_value = True
        with mock.patch.object(main_mod, "build", return_value=(app, mock.MagicMock(), session, lock)), \
                mock.patch.object(main_mod, "Watcher", return_value=watcher), \
                mock.patch.object(main_mod, "Hotkey", return_value=mock.MagicMock()), \
                mock.patch.object(main_mod, "QTimer"), \
                mock.patch.object(main_mod, "expected_capture", return_value="window"), \
                mock.patch.object(main_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", return_value=mock.MagicMock()), \
                mock.patch.object(main_mod, "declare_high_performance"), \
                mock.patch.object(os, "_exit", side_effect=AssertionError("进程内的 main() 不许直接结束进程")), \
                mock.patch.object(main_mod, "terminate_process",
                                  side_effect=AssertionError("进程内的 main() 不许直接结束进程")), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit) as raised:
                overlay_app.main()
        self.assertEqual(raised.exception.code, 0)

    def test_saves_once_with_force(self):
        errors = capture_errors(self)
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=mock.Mock()), save=mock.Mock(),
                                  settings=SimpleNamespace(big_cores=False))
        lock = threading.Lock()
        self.run_main(session, lock)
        session.save.assert_called_once_with(force=True)
        session.eventlog.recorder.close.assert_called()
        self.assertFalse(lock.locked())
        self.assertEqual(errors.entries(), [])

    def test_lock_held_skips_save(self):
        errors = capture_errors(self)
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=mock.Mock()), save=mock.Mock(),
                                  settings=SimpleNamespace(big_cores=False))
        lock = threading.Lock()
        lock.acquire()  # 后台识别一直占着锁
        self.addCleanup(lock.release)
        with mock.patch.object(sys.modules[overlay_app.main.__module__], "EXIT_SAVE_WAIT_S", 0.05):
            self.run_main(session, lock)
        session.save.assert_not_called()
        session.eventlog.recorder.close.assert_called()  # 后面的收尾照做
        self.assertEqual([title for title, _ in errors.entries()], ["退出时没有再存一次设置"])


# ---------------------------------------------------------------- 界面


class _Window(_Case):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        super().setUp()
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), [], "出错（错误记录）"))

    def overlay(self, settings: UiSettings) -> Overlay:
        session = OverlaySession(self.db, settings, self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(self.db, session, threading.Lock(), services=Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(lambda: dispose(getattr(w.dialogs, "settings", None), w))
        return w


class HotkeyNumberTest(_Window):
    """设置文件里热键写成了数字：读的时候按默认（rules.toml 的热键），设置窗口照常打开；万一数字到了设置窗口，按未设置显示。"""

    def test_window_opens(self):
        self.path.write_text(json.dumps({"hero": "芬妮", "hotkey": 8}), encoding="utf-8")
        with self.assertLogs("advisor.prefs", level="WARNING"):
            settings = load_settings(self.path)
        self.assertIsNone(settings.hotkey)
        w = self.overlay(settings)
        w.dialogs.show_settings()
        self.assertEqual(w.dialogs.settings.keycap.text(), "F8")

    def test_number_reaching_the_window(self):
        """读设置以外的路子给了数字（例如直接建的设置）：窗口照常打开，键帽写「未设置」。"""
        w = self.overlay(UiSettings(hero="芬妮", hotkey=8))
        w.dialogs.show_settings()
        self.assertEqual(w.dialogs.settings.keycap.text(), st.HOTKEY_UNSET)

    def test_texts_and_keycap(self):
        V = st.SettingsValues
        for ok in (True, False):
            with self.subTest(ok=ok):
                cap, desc, tone = st.hotkey_texts(V(hotkey=8, hotkey_ok=ok))
                self.assertEqual((cap, tone), (st.HOTKEY_UNSET, ""))
        cap = st.KeyCap()
        self.addCleanup(cap.deleteLater)
        cap.set_key(8)
        self.assertEqual(cap.text(), "8")
        cap.set_key(None, "warn")
        self.assertEqual((cap.text(), cap.tone), ("", "warn"))


class PlatformOverlayTest(_Window):
    """悬浮窗上的游戏平台：启动时按设置交给找窗口那一层；设置窗口里选了以后存进设置文件、交给 capture、刷新窗口；
    自动识别认出另一个端时更新 platform_seen，只存一次。"""

    def setUp(self):
        super().setUp()
        from advisor.vision import capture

        self.capture = capture
        self.addCleanup(capture.set_platform, None)

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_startup_uses_setting(self):
        w = self.overlay(UiSettings(hero="凛", platform="taptap", platform_seen="taptap"))
        self.assertEqual((w.platform, w.platform_seen), ("taptap", "taptap"))
        self.assertEqual(self.capture.platform(), "taptap")
        self.assertEqual(self.capture.found_platform(), None)
        self.assertIsNone(self.overlay(UiSettings(hero="凛")).platform)

    def test_set_platform(self):
        w = self.overlay(UiSettings(hero="凛"))
        self.addCleanup(dispose, w.dialogs.settings)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        d.platform_combo.setCurrentIndex(2)  # TapTap端
        self.assertEqual((w.platform, self.capture.platform()), ("taptap", "taptap"))
        self.assertEqual((self.saved()["platform"], d.platform_combo.currentData()), ("taptap", "taptap"))
        # 同一个值不再存（不重写文件）
        before = self.path.stat().st_mtime_ns
        w.set_platform("taptap")
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        w.set_platform("bogus")  # 别的值按自动识别
        self.assertIsNone(w.platform)
        self.assertIsNone(self.capture.platform())
        self.assertEqual(self.saved()["platform"], None)
        self.assertIsNone(d.platform_combo.currentData())
        d.platform_combo.setCurrentIndex(1)  # Steam端
        self.assertEqual((w.platform, self.capture.platform()), ("steam", "steam"))

    def test_seen_updates_once(self):
        from advisor.ui.winapi import GameProbe

        w = self.overlay(UiSettings(hero="凛"))
        self.addCleanup(dispose, w.dialogs.settings)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        w.probe = lambda: GameProbe(False, None, False, False, False)
        with mock.patch.object(self.capture, "found_platform", return_value="taptap"), \
                mock.patch.object(w.session, "save", wraps=w.session.save) as save:
            w.follower.poll()
            w.follower.poll()
        self.assertEqual(save.call_count, 1)  # 只存一次
        self.assertEqual((w.platform_seen, self.saved()["platform_seen"]), ("taptap", "taptap"))
        self.assertEqual(d.platform_combo.itemText(0), "TapTap端（自动识别）")
        self.assertIsNone(w.platform)

    def test_seen_none_kept(self):
        """自动识别还没认出过端（found_platform 为 None）：platform_seen 不动、不写文件。"""
        w = self.overlay(UiSettings(hero="凛"))
        with mock.patch.object(self.capture, "found_platform", return_value=None), \
                mock.patch.object(w.session, "save", wraps=w.session.save) as save:
            w._notice_platform_seen()
        save.assert_not_called()
        self.assertEqual(w.platform_seen, "steam")

    def test_seen_not_updated_for_selected_platform(self):
        w = self.overlay(UiSettings(hero="凛", platform="steam"))
        with mock.patch.object(self.capture, "found_platform", return_value="taptap"), \
                mock.patch.object(w.session, "save", wraps=w.session.save) as save:
            w._notice_platform_seen()
        save.assert_not_called()
        self.assertEqual(w.platform_seen, "steam")


class UnlockRecordTest(_Window):
    """解锁记录（UiSettings.aux）按发行版的规矩存（unlock.all_open 换成假）。原有的记录解不开时：这次运行读不到 MachineGuid
    （多半是电脑标识一时读不到）不盖掉它，新进度只留在内存里；读得到 MachineGuid 还解不开的，照旧整串换掉。"""

    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(unlock, "all_open", return_value=False),
                        mock.patch.object(unlock, "volume_id", return_value="vol:1234abcd")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(unlock.reset, unlock.current())
        theme.set_skin(theme.DEFAULT_KEY)

    def sealed(self, ident: str, record: unlock.Record) -> str:
        """按电脑标识 ident 存的解锁记录。"""
        with mock.patch.object(unlock, "machine_ids", return_value=(ident,)):
            return unlock.seal(record)

    def start(self, aux: str, guid: str) -> Overlay:
        """设置文件里存着 aux，这次运行读到的 MachineGuid 是 guid（空的是读不到）：照启动时的顺序读设置和解锁记录。"""
        patcher = mock.patch.object(unlock, "machine_id", return_value=guid)
        patcher.start()
        self.addCleanup(patcher.stop)
        UiSettings(hero="芬妮", aux=aux).save(self.path)
        settings = load_settings(self.path)
        unlock.load(settings.aux)
        return self.overlay(settings)

    def saved_aux(self) -> str:
        return json.loads(self.path.read_text(encoding="utf-8"))["aux"]

    def test_kept_when_guid_unreadable(self):
        old = self.sealed("guid-old", unlock.Record(opened=True, skins=[U.NEON], games={U.BLACK: ["gA"]}))
        w = self.start(old, "")
        self.assertEqual(unlock.current(), unlock.Record())  # 这次解不开
        w.records._eggs_done(w.records.tracker.open(U.WHITE))
        self.assertEqual(self.saved_aux(), old)  # 没盖掉
        self.assertEqual(unlock.current().skins, [U.WHITE])  # 新进度在内存里
        w.set_manual(True)  # 别的设置照常存，记录照旧
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((raw["manual"], raw["aux"]), (True, old))

    def test_skin_written_back_when_record_kept(self):
        """原有的记录这次解不开、又读不到 MachineGuid：皮肤功能这次算没开（按默认皮肤显示），但设置文件里原有的 skin 照读到的
        原样写回，下次记录解得开时用户选的皮肤还在。只在这一种情形：读得到 MachineGuid、记录解不开（别的电脑的）时照旧按没
        解锁不写 skin。"""
        old = self.sealed("guid-old", unlock.Record(opened=True, skins=[U.NEON]))
        for guid, kept in (("", True), ("guid-this", False)):
            with self.subTest(guid=guid):
                self.path.write_bytes(dump({"hero": "芬妮", "skin": U.NEON, "aux": old}))
                patcher = mock.patch.object(unlock, "machine_id", return_value=guid)
                patcher.start()
                self.addCleanup(patcher.stop)
                settings = load_settings(self.path)
                unlock.load(settings.aux)
                if not unlock.skin_open(settings.skin):  # 同 overlay_app.build：皮肤还没解锁时按默认皮肤
                    settings.skin = theme.DEFAULT_KEY
                self.assertFalse(unlock.feature_open())
                w = self.overlay(settings)
                w.set_manual(True)
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual((raw["manual"], raw["aux"], raw.get("skin")), (True, old, U.NEON if kept else None))

    def test_kept_when_no_identifier_at_all(self):
        """两种标识都读不到：同样不盖掉。"""
        old = self.sealed("guid-old", unlock.Record(opened=True, skins=[U.NEON]))
        with mock.patch.object(unlock, "volume_id", return_value=""):
            w = self.start(old, "")
            w.records._eggs_done(w.records.tracker.open(U.WHITE))
        self.assertEqual(self.saved_aux(), old)

    def test_replaced_when_guid_readable(self):
        """读得到 MachineGuid、记录还是解不开（坏了、别的电脑的）：照旧整串换掉。"""
        old = self.sealed("guid-other", unlock.Record(opened=True, skins=[U.NEON]))
        w = self.start(old, "guid-this")
        w.records._eggs_done(w.records.tracker.open(U.WHITE))
        self.assertNotEqual(self.saved_aux(), old)
        self.assertEqual(unlock.unseal(self.saved_aux()).skins, [U.WHITE])

    def test_readable_record_replaced(self):
        """原有的记录解得开（按卷序列号存的）：读不到 MachineGuid 也照常换成新的。"""
        old = self.sealed("vol:1234abcd", unlock.Record(opened=True, skins=[U.NEON]))
        w = self.start(old, "")
        self.assertEqual(unlock.current().skins, [U.NEON])
        w.records._eggs_done(w.records.tracker.open(U.WHITE))
        self.assertEqual(unlock.unseal(self.saved_aux()).skins, [U.NEON, U.WHITE])

    def test_empty_record_written(self):
        """原来没有记录：读不到 MachineGuid 也照常存（按读得到的卷序列号）。"""
        w = self.start("", "")
        w.records._eggs_done(w.records.tracker.open(U.WHITE))
        self.assertEqual(unlock.unseal(self.saved_aux()).skins, [U.WHITE])


class UiLangFieldTest(_Case):
    """界面语言一项（ui_lang）：默认没选过（空串）；旧设置文件没有这一项、写了不认识的值得按没选过，load_settings 按这次启动
    定好的语言（lang.CURRENT）填上；填的时候不当场写文件，下一次保存设置才写进去。"""

    def setUp(self):
        super().setUp()
        saved = lang.CURRENT
        self.addCleanup(setattr, lang, "CURRENT", saved)
        # 讲的是繁体界面打开以后的填法；关着时不填，见 test_not_filled_when_hant_off
        switch = mock.patch.object(lang, "HANT_ENABLED", True)
        switch.start()
        self.addCleanup(switch.stop)

    def test_not_filled_when_hant_off(self):
        """繁体界面关着（lang.HANT_ENABLED）时没选过的不填：这时这次启动定的一律是简体，填进去就把繁体 Windows 上的用户
        记成了选过简体。保存设置时也不写这一项；设置文件里已经存着的值照读到的留着、照写回去。"""
        with mock.patch.object(lang, "HANT_ENABLED", False):
            lang.CURRENT = lang.HANS
            self.path.write_text(json.dumps({"hero": "芬妮"}), encoding="utf-8")
            got = load_settings(self.path)
            self.assertEqual(got.ui_lang, "")
            self.assertEqual(got.save(self.path), self.path)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")).get("ui_lang", ""), "")
            self.path.write_text(json.dumps({"ui_lang": lang.HANT}), encoding="utf-8")
            got = load_settings(self.path)
            self.assertEqual(got.ui_lang, lang.HANT)
            self.assertEqual(got.save(self.path), self.path)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["ui_lang"], lang.HANT)

    def test_default_and_values(self):
        self.assertEqual(UiSettings().ui_lang, "")
        for value in lang.LANGUAGES:
            with self.subTest(value=value):
                self.path.write_text(json.dumps({"ui_lang": value}), encoding="utf-8")
                self.assertEqual(UiSettings.load(self.path).ui_lang, value)
                self.assertEqual(load_settings(self.path).ui_lang, value)

    def test_load_settings_fills_current(self):
        """没选过、旧文件里没有这一项：load_settings 填成 lang.CURRENT（按 Windows 的显示语言定过），文件不当场重写；
        下一次保存设置时写进去，以后不再跟着 Windows 变。"""
        for current in lang.LANGUAGES:
            with self.subTest(current=current):
                lang.CURRENT = current
                self.path.write_text(json.dumps({"hero": "芬妮"}), encoding="utf-8")
                original = self.path.read_bytes()
                with self.assertNoLogs("advisor", level="WARNING"):
                    got = load_settings(self.path)
                self.assertEqual(got.ui_lang, current)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(got.save(self.path), self.path)
                self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["ui_lang"], current)

    def test_bad_values(self):
        """值不认识（空串、别的语言代号、不是文字）：读的时候按没选过，load_settings 再按这次启动的语言填上；不是文字时
        prefs 记警告、按默认。"""
        lang.CURRENT = lang.HANT
        for raw in ("", "zh-TW", "繁體"):
            with self.subTest(raw=raw):
                self.path.write_text(json.dumps({"ui_lang": raw}), encoding="utf-8")
                with self.assertNoLogs("advisor", level="WARNING"):
                    got = load_settings(self.path)
                self.assertEqual((UiSettings.load(self.path).ui_lang, got.ui_lang), ("", lang.HANT))
        self.path.write_text(json.dumps({"ui_lang": 1}), encoding="utf-8")
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertEqual(UiSettings.load(self.path).ui_lang, "")
        self.assertEqual(load_settings(self.path).ui_lang, lang.HANT)


class UiLangOverlayTest(_Window):
    """悬浮窗上的界面语言接线：选了存进设置文件、记一条识别记录、刷新开着的设置窗口；和当前相同、不认识的值什么都不做；
    「现在重开」起得来就退出，起不来不退出、提示一次；设置窗口拿到的值里有这一项。"""

    def test_set_and_save(self):
        w = self.overlay(UiSettings(hero="芬妮", ui_lang=lang.HANS))
        w.set_ui_lang(lang.HANT)
        self.assertEqual(w.session.settings.ui_lang, lang.HANT)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["ui_lang"], lang.HANT)
        self.assertEqual(w.settings_values().ui_lang, lang.HANT)
        w.dialogs.show_settings()
        self.addCleanup(w.dialogs.settings.close)
        self.assertEqual(w.dialogs.settings.lang_combo.currentData(), lang.HANT)
        records = [e for e in w.session.recent_events(600) if e.get("kind") == "read:user"]
        self.assertEqual([(e.get("action"), e.get("ui_lang")) for e in records], [("ui_lang", lang.HANT)])

    def test_same_and_invalid_do_not_write(self):
        w = self.overlay(UiSettings(hero="芬妮", ui_lang=lang.HANS))
        w.set_ui_lang(lang.HANS)
        w.set_ui_lang("zh-TW")
        w.set_ui_lang(None)
        self.assertEqual(w.session.settings.ui_lang, lang.HANS)
        self.assertFalse(self.path.exists())  # 不重写设置文件

    def test_restart_success(self):
        w = self.overlay(UiSettings(hero="芬妮", ui_lang=lang.HANT))
        quit_called = []
        app = SimpleNamespace(quit=lambda: quit_called.append(True))
        with mock.patch.object(overlay_mod, "QApplication", SimpleNamespace(instance=lambda: app)), \
                mock.patch.object(overlay_mod.relaunch, "start", return_value=True) as start:
            w.restart_app()
        start.assert_called_once()
        self.assertEqual(quit_called, [True])

    def test_restart_failure_keeps_running(self):
        w = self.overlay(UiSettings(hero="芬妮", ui_lang=lang.HANT))
        with mock.patch.object(overlay_mod.relaunch, "start", return_value=False), \
                mock.patch.object(w.tray_part, "show_from_tray"):
            w.restart_app()
        self.assertEqual(w.message.text, RESTART_FAILED)
        records = [e for e in w.session.recent_events(600) if e.get("kind") == "read:user"]
        self.assertEqual([e.get("action") for e in records], ["restart_failed"])


if __name__ == "__main__":
    unittest.main()
