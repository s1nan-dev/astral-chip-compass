"""设置文件写不成时交给后台（advisor/prefs.py 的 SettingsWriter、Settings.save、Session.save，advisor/fileio.py 的 wait=False、
Busy、backup_later、write_backup，悬浮窗退出时的 _save_on_exit）。

- 设置文件被别的程序独占打开时，调用 save 的线程（界面线程、持着共用锁的识别线程）很快返回，不等磁盘；后台按平常的写法写，
  写不进时警告只记第一次、按退避到点自动再试，放开以后写进最新的一份，不用再存一次。
- 退避期间改了几次，只写最后一份；当场写成以后，排着的旧内容不再写；后台正要写旧内容时当场那一次先写进了新的，后台不再写。
- 改回上一次存成的样子（Session.save）：排着的那份不再写进去。
- 只有 .bak 被占用时不卡：当场写好设置文件，.bak 由后台补成换下来的那一版。
- 发行版的个人目录里写不进：后台照平常的写法等过以后改存到程序目录，之后的保存直接存在那里，Session 换到那个文件；改存的
  那一刻又交来的新内容跟着挪过去。
- 退出时不管退避，把排着的限时写完；一直被占用时到点就不等了，记进错误记录。退出以前就开始的那一次、试的不是排着的最新内容
  的那一次在退出时没写成，不算试过：照样马上再试最新的内容。
- 后台写的线程是守护线程，排着的都写完就自己退出；交来内容的那份设置没人用了（工具、测试里建了又扔掉的），排着的那一份
  不再替它写。
- 写盘工具的 wait=False：被占用马上报 Busy、文件不动；只读的照原样报；只有 .bak 被占用时交给 backup_later，write_backup 补。

占用用 Windows 的独占打开模拟（CreateFileW 不给共享，读、写、改名、替换都不行，同杀毒软件扫描、编辑器开着文件）。每条测试
一份自己的 SettingsWriter（换掉 prefs.WRITER），被占用时稍等再试的间隔、退避时长都改小，不真的等 30 秒；测试完把排着的
写完（文件都放开了）。文件都写在测试沙箱的临时文件夹里，不起进程、不开窗口。
"""
from __future__ import annotations

import ctypes
import gc
import json
import logging
import os
import sys
import threading
import time
import unittest
import weakref
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import fileio, paths, prefs
from advisor.data import load_db
from advisor.prefs import Settings
from advisor.session import Session
from advisor.ui.overlay_parts import app as overlay_app
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors

APP = qtenv.app()  # 退出那一步在悬浮窗的模块里（导入 Qt）
EXIT = sys.modules[overlay_app._save_on_exit.__module__]  # _save_on_exit 实际所在的模块（替换 EXIT_FLUSH_S 用）
REAL_DELAYS = fileio.RETRY_DELAYS  # 平常被占用时稍等再试的间隔（合计约 1 秒）
FAST_S = 0.05  # 调用 save 的线程「很快返回」：50 毫秒以内
# 持着共用锁存设置的线程放开锁、另一个线程拿到锁：中间隔着线程切换（还要起后台写的线程），机器忙时要几十毫秒；在调用的线程里
# 照平常的写法等被占用的文件，要等约 2 秒（读、替换各约 1 秒）
HANDOFF_S = 0.5
SHORT_DELAYS = (0.01, 0.02, 0.02)  # 测试里后台被占用时稍等再试的间隔（合计 0.05 秒）
SHORT_RETRY_S = 0.2  # 测试里的退避时长


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


@contextmanager
def locked(path: Path):
    """别的程序独占打开着 path（CreateFileW 不给共享：别人读、写、改名、替换都报拒绝访问），离开 with 时关掉。"""
    k32 = _kernel32()
    handle = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)  # GENERIC_READ、不共享、OPEN_EXISTING
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        k32.CloseHandle(handle)


def wait_for(condition, timeout: float = 5.0) -> bool:
    """等到 condition() 为真（后台写的线程在另一个线程里写），超时返回假。"""
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


def timed(fn, *args, **kwargs):
    """调用 fn，返回（结果, 用了几秒）。"""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


def hero_of(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["hero"]


def writer_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == prefs.WRITER_THREAD and t.is_alive()]


@unittest.skipUnless(os.name == "nt", "独占打开文件用的是 Windows 的 CreateFileW")
class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "settings-bg-")
        self.path = self.dir / "state.json"
        self.bak = self.dir / "state.json.bak"
        self.writer = prefs.SettingsWriter()
        for patcher in (mock.patch.object(prefs, "WRITER", self.writer),
                        mock.patch.object(fileio, "RETRY_DELAYS", SHORT_DELAYS),
                        mock.patch.object(prefs, "SETTINGS_RETRY_S", SHORT_RETRY_S)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.writer.flush, 5.0)  # 测试完把排着的写完（先于上面几样换回、临时文件夹删掉）
        for name in ("advisor.prefs", "advisor.fileio"):  # 后台写不进时的警告不打到测试输出里（要核对的用 assertLogs）
            quiet, logger = logging.NullHandler(), logging.getLogger(name)
            logger.addHandler(quiet)
            self.addCleanup(logger.removeHandler, quiet)

    def save_now(self, hero: str) -> None:
        """准备：照常当场写一份设置（没被占用），确认写成了。"""
        self.assertEqual(Settings(hero=hero).save(self.path), self.path)


# ---------------------------------------------------------------- 被占用时交给后台


class LockedFileTest(_Case):
    def test_returns_fast_and_written_when_free(self):
        """设置文件被独占打开：save 很快返回 False（不在调用的线程里等），交给后台；放开以后后台写进去，不用再存一次；.bak 是
        换下来的那一版。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        with locked(self.path):
            result, took = timed(settings.save, self.path)
            self.assertIs(result, False)
            self.assertLess(took, FAST_S)
            self.assertEqual(self.writer.pending(), [self.path])
        self.assertTrue(wait_for(lambda: self.writer.pending() == []))
        self.assertEqual((hero_of(self.path), hero_of(self.bak)), ("芬妮", "凛"))

    def test_latest_written_after_backoff(self):
        """一直被占用：后台照平常的写法试过还写不进，警告只记一次，按退避到点自动再试；退避期间又改了几次（每次都很快返回），
        放开以后只写最后一份，中间的几份没写进文件，.bak 是被占用以前的那一版。"""
        self.save_now("凛")
        before = self.path.read_bytes()
        settings = Settings(hero="芬妮")
        real, here = prefs._write_settings, threading.get_ident()
        tried, written = [], []  # 后台每次试写；写成的（设置里的角色）

        def record(path, text, **kwargs):
            if threading.get_ident() != here:
                tried.append(path)
            real(path, text, **kwargs)
            written.append(json.loads(text)["hero"])

        with mock.patch.object(prefs, "_write_settings", side_effect=record):
            with locked(self.path), self.assertLogs("advisor.prefs", level="WARNING") as logs:
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(wait_for(lambda: logs.output))  # 后台试过、写不进
                for hero in ("邦妮", "远野汉娜", "阿尔"):
                    settings.hero = hero
                    result, took = timed(settings.save, self.path)
                    self.assertEqual((result, took < FAST_S), (False, True))
                count = len(tried)
                self.assertTrue(wait_for(lambda: len(tried) >= count + 1))  # 到点又试了一次，还是不成
            self.assertEqual(len(logs.output), 1)  # 警告只记这一回的第一次
            self.assertTrue(wait_for(lambda: self.writer.pending() == []))  # 放开以后到点自动写进
        self.assertEqual(written, ["阿尔"])
        self.assertEqual(hero_of(self.path), "阿尔")
        self.assertEqual(self.bak.read_bytes(), before)

    def test_written_now_drops_queued(self):
        """交给后台、在退避的内容，之后当场写成了新的：排着的旧内容不再写（不拿旧内容盖掉新内容），.bak 是新内容换下来的那一版。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        with locked(self.path), self.assertLogs("advisor.prefs", level="WARNING") as logs:
            self.assertIs(settings.save(self.path), False)
            self.assertTrue(wait_for(lambda: logs.output))  # 后台试过、写不进，在退避
        settings.hero = "邦妮"
        self.assertEqual(settings.save(self.path), self.path)  # 放开了：内容变了的当场再试，写成
        self.assertEqual(self.writer.pending(), [])  # 排着的「芬妮」不再写
        time.sleep(SHORT_RETRY_S * 2)  # 过了退避的时刻
        self.assertEqual((hero_of(self.path), hero_of(self.bak)), ("邦妮", "凛"))

    def test_late_background_write_skipped(self):
        """交错：退避正好到点、后台挑出排着的旧内容正要写的时候，当场那一次先拿到了这个文件的锁、写进了新内容；后台拿到锁以后
        不再写旧的（按序号）。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        with locked(self.path), self.assertLogs("advisor.prefs", level="WARNING") as logs:
            self.assertIs(settings.save(self.path), False)
            self.assertTrue(wait_for(lambda: logs.output))  # 后台试过、写不进，在退避
        real, here = prefs._write_settings, threading.get_ident()

        def racing(path, text, **kwargs):
            if threading.get_ident() == here:  # 当场那一次正拿着这个文件的锁：这时退避到点，后台挑出「芬妮」来等锁
                self.writer.flush(0.3)
            real(path, text, **kwargs)

        settings.hero = "邦妮"
        with mock.patch.object(prefs, "_write_settings", side_effect=racing):
            self.assertEqual(settings.save(self.path), self.path)
            self.assertTrue(wait_for(lambda: self.writer.pending() == []))
        self.assertEqual(hero_of(self.path), "邦妮")

    def test_writing_in_background_then_newer(self):
        """后台正在写（等着被占用的文件）的时候又交来了新内容：不在调用的线程里试、很快返回，后台写完这一份接着写新的。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        with mock.patch.object(fileio, "RETRY_DELAYS", REAL_DELAYS):  # 后台照平常的写法等（约 1 秒）
            with locked(self.path):
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(wait_for(lambda: writer_threads() != []))
                time.sleep(0.05)  # 后台已经挑出「芬妮」，正等着这个文件
                settings.hero = "邦妮"
                result, took = timed(settings.save, self.path)
                self.assertEqual((result, took < FAST_S), (False, True))
            self.assertTrue(wait_for(lambda: self.writer.pending() == []))
        self.assertEqual(hero_of(self.path), "邦妮")


class SessionTest(_Case):
    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def test_reverted_change_not_written_later(self):
        """交给后台的改动又改回了上一次存成的样子（Session.save 这时内容和上次存成的相同）：排着的那份不再写进去，文件里留的是
        改回去的那一份（和内存里的一样）。"""
        self.save_now("凛")
        session = Session(self.db, Settings.load(self.path), self.path)
        session.save()  # 内容和文件里的相同：算存成
        with locked(self.path):
            session.settings.hero = "芬妮"
            session.save()
            session.settings.hero = "凛"  # 改回去了
            session.save()
        self.assertEqual(self.writer.flush(5.0), [])
        self.assertEqual(hero_of(self.path), "凛")

    def test_lock_holder_does_not_wait(self):
        """识别线程持着共用锁存设置时文件被占用：它很快放开共用锁，界面线程拿锁不用跟着等磁盘。"""
        self.save_now("凛")
        session = Session(self.db, Settings.load(self.path), self.path)
        shared, inside = threading.Lock(), threading.Event()

        def recognition():
            with shared:
                inside.set()
                session.settings.hero = "芬妮"
                session.save()

        with mock.patch.object(fileio, "RETRY_DELAYS", REAL_DELAYS), locked(self.path):
            worker = threading.Thread(target=recognition)
            worker.start()
            self.assertTrue(inside.wait(5.0))  # 识别线程拿着共用锁，正在存设置
            got, took = timed(shared.acquire, timeout=5.0)
            self.assertTrue(got)
            shared.release()
            worker.join(5.0)
            self.assertLess(took, HANDOFF_S)
        self.assertEqual(self.writer.flush(5.0), [])
        self.assertEqual(hero_of(self.path), "芬妮")


class BackupLockedTest(_Case):
    def test_bak_locked_does_not_stall(self):
        """只有 .bak 被占用（设置文件本身能写）：save 当场写好、很快返回，不在 .bak 那一步等；.bak 由后台补成换下来的那一版。"""
        self.save_now("凛")
        self.save_now("芬妮")  # .bak 是「凛」
        settings = Settings(hero="邦妮")
        with mock.patch.object(fileio, "RETRY_DELAYS", REAL_DELAYS):  # 后台补 .bak 时照平常的写法等（约 1 秒）
            with locked(self.bak):
                result, took = timed(settings.save, self.path)
                self.assertEqual((result, took < FAST_S), (self.path, True))
                self.assertEqual(hero_of(self.path), "邦妮")  # 当场写好了
            self.assertTrue(wait_for(lambda: self.writer.pending() == []))
        self.assertEqual(hero_of(self.bak), "芬妮")

    def test_orphan_backup_locked(self):
        """读的时候设置文件读坏了、备份没能用上（第一次保存前要把备份另存走，Settings._keep_orphan）：备份被独占打开时 save
        很快返回，不在另存那一步等，交给后台照常另存（放开以后另存好）再写设置文件；备份没有被新内容盖掉。"""
        self.save_now("凛")
        self.save_now("芬妮")
        good = self.bak.read_bytes()  # 备份里是还能用的设置（凛）
        self.path.write_bytes(b"\0" * 30)  # 断电后的零字节
        real = fileio._read

        def read(path, *args, **kwargs):  # 读的时候备份被占用
            if path == self.bak:
                raise PermissionError(13, "拒绝访问")
            return real(path, *args, **kwargs)

        with mock.patch.object(fileio, "_read", read), self.assertLogs("advisor", level="WARNING"):
            settings, existed = Settings.read(self.path)
        self.assertEqual((settings, existed), (Settings(), True))
        settings.hero = "邦妮"
        with mock.patch.object(fileio, "RETRY_DELAYS", REAL_DELAYS):  # 后台另存时照平常的写法等（约 1 秒）
            with locked(self.bak):
                result, took = timed(settings.save, self.path)
                self.assertEqual((result, took < FAST_S), (False, True))
            self.assertTrue(wait_for(lambda: self.writer.pending() == []))
        orphans = sorted(p for p in self.dir.iterdir() if p.name.startswith("state.json.bak.orphan-"))
        self.assertEqual([p.read_bytes() for p in orphans], [good])
        self.assertEqual(hero_of(self.path), "邦妮")


# ---------------------------------------------------------------- 发行版：后台改存到程序目录


@unittest.skipUnless(os.name == "nt", "独占打开文件用的是 Windows 的 CreateFileW")
class FallbackInBackgroundTest(unittest.TestCase):
    """模拟发行版（同 tests/test_storage.py 的 _Release：程序文件夹、APPDATA 都在沙箱的临时文件夹里）：个人目录里的设置文件
    一直被独占打开，后台照平常的写法等过还写不进，改存到程序目录的 state.fallback.json、告诉 paths；之后的保存直接存在那里，
    Session 换到那个文件，个人目录里的那份不动。"""

    @classmethod
    def setUpClass(cls):
        cls.db = load_db()

    def setUp(self):
        base = sandbox.temp_dir(self, "settings-bg-release-")
        self.app = base / "AstralChipCompass"
        self.app.mkdir()
        roaming = base / "Roaming"
        roaming.mkdir()
        self.user = roaming / "AstralChipCompass"
        self.writer = prefs.SettingsWriter()
        for patcher in (mock.patch.object(paths, "is_frozen", return_value=True),
                        mock.patch.object(paths, "app_dir", return_value=self.app),
                        mock.patch.dict(os.environ, {"APPDATA": str(roaming)}),
                        mock.patch.object(prefs, "WRITER", self.writer),
                        mock.patch.object(fileio, "RETRY_DELAYS", SHORT_DELAYS)):
            patcher.start()
            self.addCleanup(patcher.stop)
        paths._reset_for_tests()
        self.addCleanup(paths._reset_for_tests)
        self.addCleanup(self.writer.flush, 5.0)
        quiet, logger = logging.NullHandler(), logging.getLogger("advisor.paths")
        logger.addHandler(quiet)
        self.addCleanup(logger.removeHandler, quiet)

    def test_falls_back_in_background(self):
        state = paths.state_path()
        self.assertEqual(state, self.user / "state.json")
        self.assertEqual(Settings(hero="凛").save(state), state)
        before = state.read_bytes()
        session = Session(self.db, Settings.load(state), state)
        session.settings.window_pos = [10, 20]
        fallback = self.app / paths.STATE_FALLBACK_NAME
        with locked(state), self.assertLogs("advisor.prefs", level="WARNING") as logs:
            result, took = timed(session.save)
            self.assertLess(took, FAST_S)
            self.assertEqual(session.path, state)  # 当场没写成，也还没改存
            self.assertTrue(wait_for(lambda: paths.storage_fallback() is not None))  # 后台改存了
        self.assertEqual(len(logs.output), 1)
        self.assertIn("已改存到", logs.output[0])
        self.assertEqual(Settings.load(fallback).window_pos, [10, 20])
        self.assertEqual(paths.storage_fallback().kind, paths.FALLBACK_STATE_FAILED)
        self.assertEqual(paths.state_path(), fallback)
        session.settings.window_pos = [30, 40]
        self.assertIsNone(session.save())  # 个人目录里的那份已经放开了：照样存在改存的文件里
        self.assertEqual(session.path, fallback)  # Session 换到改存的文件
        self.assertEqual(Settings.load(fallback).window_pos, [30, 40])
        self.assertEqual(state.read_bytes(), before)  # 个人目录里的那份不动
        self.assertEqual(self.writer.pending(), [])

    def test_newer_while_falling_back(self):
        """后台正往程序目录改存的时候又交来了新内容（这时还排在个人目录的那一份上）：改存以后跟着挪过去、接着写进改存的文件，
        不用再存一次。"""
        state = paths.state_path()
        self.assertEqual(Settings(hero="凛").save(state), state)
        session = Session(self.db, Settings.load(state), state)
        session.settings.window_pos = [10, 20]
        fallback = self.app / paths.STATE_FALLBACK_NAME
        real, here = prefs._write_settings, threading.get_ident()

        def newer():  # 别的线程改了设置、存一次（后台正在写：排上）
            session.settings.window_pos = [30, 40]
            session.save()

        def write(path, text, **kwargs):
            if threading.get_ident() != here and Path(path) == fallback and json.loads(text)["window_pos"] == [10, 20]:
                other = threading.Thread(target=newer)  # 后台正改存的这一刻
                other.start()
                other.join(5.0)
            return real(path, text, **kwargs)

        with mock.patch.object(prefs, "_write_settings", side_effect=write), locked(state), \
                self.assertLogs("advisor.prefs", level="WARNING"):
            session.save()
            self.assertTrue(wait_for(lambda: paths.storage_fallback() is not None))  # 后台改存了
        self.assertEqual(self.writer.flush(5.0), [])
        self.assertEqual(Settings.load(fallback).window_pos, [30, 40])


# ---------------------------------------------------------------- 退出


class ExitFlushTest(_Case):
    """退出时（悬浮窗的 _save_on_exit）：交给后台、在退避的内容不管退避马上写完；一直被占用时到点就不等了，记进错误记录。"""

    def exit_save(self) -> float:
        """照退出时的那一步做一次（Session 换成只记调用的假的），返回用了几秒。"""
        session = SimpleNamespace(eventlog=SimpleNamespace(recorder=None), save=mock.Mock())
        _, took = timed(overlay_app._save_on_exit, session, threading.Lock())
        session.save.assert_called_once_with(force=True)
        return took

    def test_queued_written_on_exit(self):
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        errors = capture_errors(self)
        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 30.0):  # 退避 30 秒：不是到点自动写进的
            with locked(self.path), self.assertLogs("advisor.prefs", level="WARNING") as logs:
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(wait_for(lambda: logs.output))  # 后台试过、写不进，退避着
            self.assertLess(self.exit_save(), 1.0)
        self.assertEqual(hero_of(self.path), "芬妮")
        self.assertEqual(self.writer.pending(), [])
        self.assertEqual(errors.entries(), [])

    def test_gives_up_on_time_when_locked(self):
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        errors = capture_errors(self)
        with mock.patch.object(fileio, "RETRY_DELAYS", REAL_DELAYS), mock.patch.object(EXIT, "EXIT_FLUSH_S", 0.5), \
                locked(self.path):
            self.assertIs(settings.save(self.path), False)
            took = self.exit_save()
        self.assertGreaterEqual(took, 0.45)
        self.assertLess(took, 1.0)
        entries = errors.entries()
        self.assertEqual([title for title, _ in entries], ["退出时设置没能存进去"])
        self.assertIn(str(self.path), entries[0][1])

    def test_unwritable_gives_up_at_once(self):
        """写不进、再试也一样（只读）：退出时试过一次就不再等满 EXIT_FLUSH_S，记进错误记录。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o666)
        errors = capture_errors(self)
        with self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(settings.save(self.path), False)
        self.assertLess(self.exit_save(), EXIT.EXIT_FLUSH_S / 2)
        self.assertEqual([title for title, _ in errors.entries()], ["退出时设置没能存进去"])
        self.assertEqual(hero_of(self.path), "凛")

    def failing_once(self, hero: str, during=None):
        """换掉 prefs._write_settings：后台写的线程第一次写 hero 那一份时先做 during()（给了的话），再等 exiting（退出时的
        flush 开始了）以后报写不进；别的照常写。返回（换的补丁, writing：后台正在写那一份, exiting, 后台每次试写的角色）。"""
        real, here = prefs._write_settings, threading.get_ident()
        writing, exiting, tried = threading.Event(), threading.Event(), []

        def write(path, text, **kwargs):
            if threading.get_ident() != here:
                tried.append(json.loads(text)["hero"])
                if tried.count(hero) == 1 and tried[-1] == hero:
                    writing.set()
                    if during is not None:
                        during()
                    exiting.wait(5.0)
                    time.sleep(0.1)  # 退出时的 flush 已经开始
                    raise PermissionError(13, "拒绝访问")
            return real(path, text, **kwargs)

        return mock.patch.object(prefs, "_write_settings", side_effect=write), writing, exiting, tried

    def test_write_before_exit_not_counted(self):
        """退出以前就开始的那一次后台写（按平常的写法等着被占用的文件），在退出时才没写成：不算退出这一轮试过，照样不管退避
        马上再试一次，放开了就写进去。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        patcher, writing, exiting, tried = self.failing_once("芬妮")
        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 30.0), patcher:
            with locked(self.path):
                self.assertIs(settings.save(self.path), False)  # 当场被占用：交给后台
                self.assertTrue(writing.wait(5.0))
            exiting.set()
            self.assertEqual(self.writer.flush(3.0), [])
        self.assertEqual((hero_of(self.path), tried), ("芬妮", ["芬妮", "芬妮"]))

    def test_newer_while_writing_tried_on_exit(self):
        """后台正在写的时候又交来了新内容（排着），正在写的那一次在退出时没写成：退出这一步照样马上试写新内容，放开了就写进去
        （试过的是旧内容，不算排着的最新内容试过了）。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        patcher, writing, exiting, tried = self.failing_once("芬妮")
        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 30.0), patcher:
            with locked(self.path):
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(writing.wait(5.0))
            settings.hero = "邦妮"
            self.assertIs(settings.save(self.path), False)  # 后台正在写：排上，不当场试
            exiting.set()
            self.assertEqual(self.writer.flush(3.0), [])
        self.assertEqual((hero_of(self.path), tried), ("邦妮", ["芬妮", "邦妮"]))

    def test_saved_during_exit_tried(self):
        """退出这一步正在试写排着的内容时（没停下的识别线程）又交来了新内容：这一次没写成以后照样马上试写新内容。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")

        def newer():  # 别的线程交来新内容（后台正在写：排上）
            settings.hero = "邦妮"
            other = threading.Thread(target=settings.save, args=(self.path,))
            other.start()
            other.join(5.0)

        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 30.0):
            with locked(self.path), self.assertLogs("advisor.prefs", level="WARNING") as logs:
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(wait_for(lambda: logs.output))  # 后台试过、写不进，退避 30 秒
            patcher, writing, exiting, tried = self.failing_once("芬妮", during=newer)
            exiting.set()
            with patcher:
                self.assertEqual(self.writer.flush(3.0), [])
        self.assertEqual((hero_of(self.path), tried), ("邦妮", ["芬妮", "邦妮"]))


# ---------------------------------------------------------------- 后台写的线程


class WriterThreadTest(_Case):
    def test_program_error_recorded(self):
        """后台写的时候程序出错（不是写盘的错）：记进错误记录，这一份按写不进退避、到点照常再试，后台写的线程不死。"""
        self.save_now("凛")
        settings = Settings(hero="芬妮")
        errors = capture_errors(self)
        real, here = prefs._write_settings, threading.get_ident()

        def broken(path, text, **kwargs):  # 只在后台写的线程里出错
            if threading.get_ident() != here:
                raise RuntimeError("模拟的程序错误")
            return real(path, text, **kwargs)

        with mock.patch.object(prefs, "_write_settings", side_effect=broken):
            with locked(self.path):
                self.assertIs(settings.save(self.path), False)
                self.assertTrue(wait_for(lambda: errors.entries() != []))
        self.assertEqual(errors.entries()[0][0], "后台保存设置文件出错（拾星助手仍在运行）")
        self.assertIn("模拟的程序错误", errors.entries()[0][1])
        self.assertTrue(wait_for(lambda: self.writer.pending() == []))  # 到点再试，写进去了
        self.assertEqual(hero_of(self.path), "芬妮")

    def test_abandoned_settings_not_written(self):
        """交来内容的那份设置写不进、交给后台以后没人用了（工具、测试里建了又扔掉的）：回收得了（记下的写不进的原因不连着它），
        排着的那一份到点也不再替它写。"""
        self.save_now("凛")
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o666)
        settings = Settings(hero="芬妮")
        with mock.patch.object(prefs, "SETTINGS_RETRY_S", 30.0), self.assertLogs("advisor.prefs", level="WARNING"):
            self.assertIs(settings.save(self.path), False)  # 只读：写不进，排着退避
        gone = weakref.ref(settings)
        del settings
        gc.collect()
        self.assertIsNone(gone())
        os.chmod(self.path, 0o666)  # 写得进了
        self.assertEqual(self.writer.flush(5.0), [])
        self.assertEqual(hero_of(self.path), "凛")

    def test_daemon_and_exits_when_done(self):
        """后台写的线程是守护线程（进程退出时不等它）；平常当场写成不起线程；排着的都写完就自己退出。"""
        before = set(writer_threads())
        self.save_now("凛")
        self.assertEqual(set(writer_threads()) - before, set())
        settings = Settings(hero="芬妮")
        with locked(self.path):
            self.assertIs(settings.save(self.path), False)
            started = [t for t in writer_threads() if t not in before]
            self.assertEqual(len(started), 1)
            self.assertTrue(started[0].daemon)
        self.assertEqual(self.writer.flush(5.0), [])
        started[0].join(2.0)
        self.assertFalse(started[0].is_alive())
        self.assertEqual(hero_of(self.path), "芬妮")


# ---------------------------------------------------------------- 写盘工具的 wait=False


class NoWaitTest(_Case):
    OLD = b'{"hero": "\xe8\x8a\xac\xe5\xa6\xae"}'
    NEW = b'{"hero": "\xe9\x82\xa6\xe5\xa6\xae"}'
    PREV = b'{"hero": "\xe5\x87\x9b"}'

    def names(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def test_busy_raised_at_once(self):
        """wait=False：文件被独占打开时不等，马上报 Busy（PermissionError 的子类）；文件不动，不留临时文件。"""
        self.path.write_bytes(self.OLD)
        with locked(self.path):
            start = time.perf_counter()
            with self.assertRaises(fileio.Busy) as raised:
                fileio.write_text(self.path, "新的", backup=True, if_changed=True, wait=False)
            self.assertLess(time.perf_counter() - start, FAST_S)
        self.assertIsInstance(raised.exception, PermissionError)
        self.assertEqual((self.path.read_bytes(), self.names()), (self.OLD, ["state.json"]))

    def test_hopeless_raised_as_is(self):
        """只读的文件：wait=False 也照原样报 PermissionError（不是 Busy：等也没用，调用方按写不进处理）。"""
        self.path.write_bytes(self.OLD)
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o666)
        with self.assertRaises(PermissionError) as raised:
            fileio.write_bytes(self.path, self.NEW, wait=False)
        self.assertNotIsInstance(raised.exception, fileio.Busy)
        self.assertEqual(self.path.read_bytes(), self.OLD)

    def test_backup_later(self):
        """只有 .bak 被独占打开：wait=False 照常换上新内容，原来的内容交给 backup_later，这次不记警告、.bak 不动、不留临时文件；
        之后 write_backup 补上 .bak。"""
        self.path.write_bytes(self.OLD)
        self.bak.write_bytes(self.PREV)
        later = []
        with locked(self.bak), self.assertNoLogs("advisor.fileio", level="WARNING"):
            self.assertTrue(fileio.write_bytes(self.path, self.NEW, backup=True, wait=False, backup_later=later.append))
        self.assertEqual((self.path.read_bytes(), self.bak.read_bytes(), later), (self.NEW, self.PREV, [self.OLD]))
        self.assertEqual(self.names(), ["state.json", "state.json.bak"])
        fileio.write_backup(self.path, self.OLD)
        self.assertEqual(self.bak.read_bytes(), self.OLD)

    def test_move_aside_busy(self):
        """move_aside 的 wait=False：被独占打开时马上报 Busy，文件留在原处。"""
        self.bak.write_bytes(self.PREV)
        with locked(self.bak), self.assertRaises(fileio.Busy):
            fileio.move_aside(self.bak, prefs.SETTINGS_ORPHAN, wait=False)
        self.assertEqual(self.names(), ["state.json.bak"])


if __name__ == "__main__":
    unittest.main()
