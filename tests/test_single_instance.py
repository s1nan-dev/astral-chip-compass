"""防多开（advisor/ui/single_instance.py）和它在 run()、悬浮窗 main()、发行版入口里的接线；已在运行的那个没有响应时的
处理和弹窗（advisor/ui/stuck_prompt.py）。

测试里一律用带随机后缀的专用名字（random_names），绝不用正式的名字（single_instance.NAMES）：用户可能正开着拾星助手，
用了正式的名字，测试会把用户的悬浮窗弹到前面，或者被用户的那个拦住，甚至结束用户的那个。允许别的进程把窗口放到前台的那一步
换成假的，不抢焦点。弹窗一律换成假的（single_instance 的 _ask_end、_tell，stuck_prompt 的 _show），或者只在离屏平台上真的
弹（不在桌面上出窗口）；时钟换成假的。不起悬浮窗，不启动开发版和发行版；子进程只跑拿锁、放锁这类最小的脚本，用完确认已经结束；结束的只有测试自己起的子进程。
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor.ui.overlay_parts import app as overlay_app
from advisor import paths  # noqa: E402
from advisor.ui import single_instance as si  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32" and si._api is not None
SKIP_REASON = "命名互斥体和事件只在 Windows 上有"


def random_names() -> si.Names:
    """测试专用的一组名字：前缀和正式的不同，带随机后缀，每次都不一样。"""
    return si.Names.under(f"Local\\ChipAdvisorTest-{uuid.uuid4().hex}")


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-single-instance"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


def child_env() -> dict:
    return dict(os.environ, PYTHONPATH=str(ROOT))


class NamesTest(unittest.TestCase):
    def test_official_names(self):
        """正式的名字：Local\\ 下（每个登录会话一份），带英文名（paths.FOLDER_NAME）；前三个（锁、两个事件）不变，记着拿锁的
        是谁的共享内存另起名字；四个各不相同（几种对象共用一个命名空间）。开发版和发行版用同一组。再次启动的进程最多等 2 秒；
        已在运行的那个拿到锁满 15 秒还不回应才算卡住（刚拿到锁、还在加载的不算），弹窗显示期间每 0.2 秒试一次拿锁。
        共享内存的格式：标记、进程号、创建时间、拿到锁的时刻，换过格式标记跟着换（只记前两样的旧格式 ACC1 不认）。"""
        names = si.NAMES
        self.assertEqual(names, si.Names("Local\\AstralChipCompass.Overlay", "Local\\AstralChipCompass.Show",
                                         "Local\\AstralChipCompass.Shown", "Local\\AstralChipCompass.Owner"))
        every = (names.mutex, names.show, names.shown, names.owner)
        self.assertTrue(all(n.startswith(f"Local\\{paths.FOLDER_NAME}.") for n in every))
        self.assertEqual(len(set(every)), 4)
        self.assertEqual(si.Names("a", "b", "c").owner, "")  # 只给前三个时不记、不查
        self.assertLessEqual(si.WAIT_S, 2.0)
        self.assertLess(si.POLL_S, si.WAIT_S)
        self.assertEqual(si.STUCK_S, 15.0)
        self.assertEqual(si.DIALOG_POLL_S, 0.2)
        self.assertEqual((si.OWNER_RECORD.format, si.OWNER_RECORD.size, si.OWNER_MAGIC), ("<4sIQQ", 24, b"ACC2"))
        test_names = random_names()
        self.assertFalse(test_names.mutex.startswith(f"Local\\{paths.FOLDER_NAME}"))
        self.assertTrue(test_names.owner)
        self.assertNotEqual(random_names(), test_names)


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class LockTest(unittest.TestCase):
    def test_second_acquire_fails(self):
        """拿锁成功；同一个名字第二次拿不到（同一进程里也一样）。拿不到时马上关掉句柄：第一个放锁后锁就没了，可以重新拿。"""
        names = random_names()
        first = si.acquire(names)
        self.addCleanup(first.close)
        self.assertTrue(first.held)
        self.assertIsNone(si.acquire(names))
        self.assertIsNone(si.acquire(names))
        first.close()
        self.assertFalse(first.held)
        again = si.acquire(names)
        self.addCleanup(again.close)
        self.assertTrue(again.held)

    def test_running_only_checks(self):
        """running()（打包脚本用）只打开互斥体看在不在：不建、不拿，查完不留句柄。"""
        names = random_names()
        self.assertFalse(si.running(names.mutex))
        first = si.acquire(names)  # 上面的查询没有建出互斥体
        self.addCleanup(first.close)
        self.assertTrue(first.held)
        self.assertTrue(si.running(names.mutex))
        self.assertTrue(si.running(names.mutex))
        self.assertIsNone(si.acquire(names))  # 查过以后锁仍是第一个的
        first.close()
        self.assertFalse(si.running(names.mutex))  # 查询没有留下句柄，互斥体随第一个放锁消失

    def test_owner_record(self):
        """拿到锁时把本进程的进程号、创建时间和拿到锁的时刻记进共享内存；放锁后这块共享内存随之消失，不留残留。
        查的时候记的是本进程自己（同一进程里拿锁，只在测试里会有）当作查不到，不会问要不要结束自己。只给前三个名字时不记。"""
        api, names = si._api, random_names()
        before = api.now()
        instance = si.acquire(names)
        self.addCleanup(instance.close)
        self.assertTrue(instance.owner_map)
        pid, created, acquired = read_record(names)
        self.assertEqual((pid, created), (os.getpid(), api.current_process()[1]))
        self.assertGreater(created, 0)
        self.assertTrue(created <= before <= acquired <= api.now())  # 拿到锁的时刻是拿锁的时候取的
        self.assertEqual(si._find_owner(api, names), si.Owner())
        instance.close()
        self.assertEqual(api.open_mapping(names.owner), (0, ERROR_FILE_NOT_FOUND))
        again = si.acquire(si.Names(names.mutex, names.show, names.shown))
        self.addCleanup(again.close)
        self.assertTrue(again.held)
        self.assertEqual(again.owner_map, 0)
        self.assertEqual(api.open_mapping(names.owner), (0, ERROR_FILE_NOT_FOUND))


ERROR_FILE_NOT_FOUND = 2


def read_record(names: si.Names) -> tuple[int, int, int] | None:
    """共享内存里记着的（进程号, 创建时间, 拿到锁的时刻）；没有这块共享内存、格式不对时为 None。"""
    api = si._api
    handle, _ = api.open_mapping(names.owner)
    if not handle:
        return None
    try:
        magic, pid, created, acquired = si.OWNER_RECORD.unpack(api.read_mapping(handle, si.OWNER_RECORD.size))
    finally:
        api.close(handle)
    return (pid, created, acquired) if magic == si.OWNER_MAGIC else None


def write_record(instance: si.Instance, pid: int, created: int, acquired: int | None = None) -> None:
    """把拿着锁的 instance 记着的改成别的进程（测试里假装拿锁的是那个进程）；拿到锁的时刻默认同创建时间。"""
    data = si.OWNER_RECORD.pack(si.OWNER_MAGIC, pid, created, created if acquired is None else acquired)
    if not si._api.write_mapping(instance.owner_map, data):
        raise AssertionError("改不了共享内存")


HOLDER = r"""
import os, sys, time
from advisor.ui import single_instance as si
time.sleep(float(sys.argv[5]))  # 起来以后过这么多秒才拿锁
instance = si.acquire(si.Names(*sys.argv[1:5]))
print("READY", os.getpid(), int(instance is not None and instance.held), flush=True)
sys.stdin.read()  # 等父进程关掉标准输入（正常退出），或被父进程结束
"""

PROCESS_TERMINATE, SYNCHRONIZE, WAIT_OBJECT_0 = 0x0001, 0x00100000, 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # 取退出码要用


def _kernel32():
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


class ChildProcesses(unittest.TestCase):
    """起最小的子进程拿锁（HOLDER，只拿锁、不回应「显示」请求）。

    虚拟环境的 python.exe 是个转启动器，真正拿锁的是它启动的解释器进程：子进程报出自己的进程号，按它开句柄、结束、等它
    结束；测试结束时再确认一遍，没结束的结束掉（只结束这里起的子进程）。"""

    def setUp(self):
        self.k = _kernel32()
        self.handles: list[int] = []  # 子进程里真正拿锁的解释器进程的句柄

    def spawn(self, names: si.Names, delay: float = 0.0):
        """起一个拿锁的子进程（起来以后过 delay 秒才拿锁）：返回（Popen, 解释器进程的句柄, 拿到锁没有, 解释器进程的进程号）。"""
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, names.mutex, names.show, names.shown, names.owner,
                                 str(delay)],
                                cwd=str(ROOT), env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        self.addCleanup(self.cleanup, proc)
        lines: list[str] = []
        reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(60)
        if not (lines and lines[0].startswith("READY ")):
            proc.kill()
            errors: list[str] = []
            reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
            reader.start()
            reader.join(5)
            self.fail(f"子进程没有报告拿锁：{lines} {''.join(errors)[-2000:]}")
        _, pid, held = lines[0].split()
        handle = self.k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        self.assertTrue(handle)
        self.handles.append(handle)
        return proc, handle, held == "1", int(pid)

    def alive(self, handle) -> bool:
        return self.k.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0

    def exit_code(self, handle) -> int:
        from ctypes import wintypes

        code = wintypes.DWORD()
        self.assertTrue(self.k.GetExitCodeProcess(handle, ctypes.byref(code)))
        return code.value

    @staticmethod
    def created(pid: int) -> int:
        """进程的创建时间（single_instance 的计法）。"""
        handle, error = si._api.open_process(pid)
        if not handle:
            raise AssertionError(f"打不开进程 {pid}：{error}")
        try:
            return si._api.process_created(handle)
        finally:
            si._api.close(handle)

    def cleanup(self, proc) -> None:
        for handle in self.handles:
            if self.k.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0:
                self.k.TerminateProcess(handle, 1)
                self.k.WaitForSingleObject(handle, 10000)
            self.k.CloseHandle(handle)
        self.handles = []
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def wait_free(self, names: si.Names, seconds: float = 5.0) -> si.Instance | None:
        """锁释放后拿下它（最多等 seconds 秒）。"""
        deadline = time.monotonic() + seconds
        while True:
            instance = si.acquire(names)
            if instance is not None or time.monotonic() > deadline:
                return instance
            time.sleep(0.05)


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class ProcessLockTest(ChildProcesses):
    """子进程结束以后锁随之释放（正常退出、被结束都一样，不留残留的锁）；记着它的共享内存也随之消失，下一次启动不受影响。"""

    def test_lock_released_when_killed(self):
        names = random_names()
        proc, handle, held, pid = self.spawn(names)
        self.assertTrue(held)
        self.assertIsNone(si.acquire(names))  # 子进程拿着
        self.assertTrue(si.running(names.mutex))
        self.assertEqual(read_record(names)[:2], (pid, self.created(pid)))
        self.assertTrue(self.k.TerminateProcess(handle, 1))  # 同任务管理器「结束任务」、程序崩溃
        self.assertEqual(self.k.WaitForSingleObject(handle, 10000), WAIT_OBJECT_0)
        proc.wait(timeout=30)
        instance = self.wait_free(names)
        self.assertIsNotNone(instance)
        self.addCleanup(instance.close)
        self.assertTrue(instance.held)
        self.assertEqual(read_record(names)[0], os.getpid())

    def test_lock_released_on_normal_exit(self):
        """正常退出：锁和共享内存都不残留；之前查得到的是它（进程号、创建时间对得上，开了多久），之后再启动的照常拿锁、
        记上自己。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(names)
        self.assertTrue(held)
        self.assertIsNone(si.acquire(names))
        owner = si._find_owner(si._api, names)
        self.assertEqual((owner.pid, owner.created, owner.denied), (pid, self.created(pid), False))
        self.assertTrue(0 <= owner.age < 120)
        proc.stdin.close()  # 子进程读到结尾，正常退出（没有显式放锁，由系统关掉句柄）
        self.assertEqual(self.k.WaitForSingleObject(handle, 30000), WAIT_OBJECT_0)
        self.assertEqual(proc.wait(timeout=30), 0)
        instance = self.wait_free(names)
        self.assertIsNotNone(instance)
        self.addCleanup(instance.close)
        self.assertTrue(instance.held)
        self.assertEqual(read_record(names)[:2], (os.getpid(), si._api.current_process()[1]))


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class StuckProcessTest(ChildProcesses):
    """已在运行的那个没有响应，用真的子进程走：子进程只拿锁、不回应「显示」请求（同卡住的悬浮窗）。弹窗换成
    假的，时钟换成假的（子进程刚起，按需要假装它已经运行了多久），放开前台权限那一步换成假的，错误记录换成假的。结束的只有
    测试自己起的子进程。"""

    def setUp(self):
        super().setUp()
        self.asked, self.told, self.reports = [], [], []
        self.answer = True  # 问的时候用户点「结束它并重新打开」
        self.on_ask = None  # 问的时候另外要做的事
        for target, attr, kwargs in (
                (si._api, "allow_foreground", {}),
                (si, "RECHECK_S", {"new": 0.2}),
                (si, "_ask_end", {"side_effect": self.ask}),
                (si, "_tell", {"side_effect": lambda kind, released=None: self.told.append(kind)}),
                (si, "_report", {"side_effect": lambda title, detail: self.reports.append((title, detail))})):
            patcher = mock.patch.object(target, attr, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ask(self, age: float, released=None) -> bool:
        self.asked.append(age)
        if self.on_ask is not None:
            self.on_ask()
        return self.answer

    def clock(self, names: si.Names, age: float):
        """假装记在 names 里的那个拿到锁已经 age 秒了。"""
        acquired = read_record(names)[2]
        return mock.patch.object(si._api, "now", return_value=acquired + int(age * si.FILETIME_PER_S))

    def test_end_and_reopen(self):
        """没回应、拿到锁已经满 15 秒：问；点「结束它并重新打开」：结束它（只结束它，旁边另一个拿着别的锁的子进程不动），
        等它结束后拿下锁照常启动，记上自己；错误记录里记一笔。退出码换成少见的值，证明是这里结束的。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(names)
        other_proc, other, other_held, _ = self.spawn(random_names())
        self.assertTrue(held and other_held)
        with self.clock(names, 20), mock.patch.object(si, "ENDED_CODE", 0x5EED):
            start = time.monotonic()
            mine = si.start(names, wait=0.3)
            elapsed = time.monotonic() - start
        self.assertIsNotNone(mine)
        self.addCleanup(mine.close)
        self.assertTrue(mine.held)
        self.assertLess(elapsed, 15)
        self.assertEqual(len(self.asked), 1)
        self.assertAlmostEqual(self.asked[0], 20, places=3)
        self.assertEqual(self.told, [])
        self.assertEqual(self.k.WaitForSingleObject(handle, 10000), WAIT_OBJECT_0)
        self.assertEqual(self.exit_code(handle), 0x5EED)
        self.assertTrue(self.alive(other))
        self.assertEqual(read_record(names)[:2], (os.getpid(), si._api.current_process()[1]))
        self.assertEqual([title for title, _ in self.reports], ["结束了没有响应的拾星助手（用户选了「结束它并重新打开」）"])
        self.assertIn(f"进程号 {pid}", self.reports[0][1])

    def test_just_started_quits_quietly(self):
        """它拿到锁还不满 15 秒（刚开、还在加载）：照旧安静退出，不问、不弹窗、不结束它。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(names)
        with self.clock(names, 5):
            self.assertIsNone(si.start(names, wait=0.3))
        self.assertEqual((self.asked, self.told, self.reports), ([], [], []))
        self.assertTrue(self.alive(handle))
        self.assertIsNone(si.acquire(names))

    def test_age_counts_from_taking_the_lock(self):
        """开了多久按拿到锁的时刻算，不按进程的创建时间：子进程起来以后过一会儿才拿锁（同结束了没有响应的那个
        以后接手、刚开始加载悬浮窗的进程），拿到锁不满 15 秒时照旧安静退出，哪怕按创建时间算已经满了。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(names, delay=1.0)
        self.assertTrue(held)
        _, created, acquired = read_record(names)
        self.assertEqual(created, self.created(pid))
        self.assertGreaterEqual(acquired - created, int(0.9 * si.FILETIME_PER_S))  # 起来以后约 1 秒才拿锁
        with self.clock(names, 14.5):  # 拿到锁 14.5 秒，按创建时间算已经满了 15 秒
            self.assertIsNone(si.start(names, wait=0.3))
        self.assertEqual((self.asked, self.told, self.reports), ([], [], []))
        self.assertTrue(self.alive(handle))

    def test_cancel_keeps_it(self):
        """点「取消」：退出，不结束它。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(names)
        self.answer = False
        with self.clock(names, 60):
            self.assertIsNone(si.start(names, wait=0.3))
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(self.alive(handle))
        self.assertEqual(self.reports, [])

    def test_reused_pid_is_not_ended(self):
        """记的进程号对得上一个活着的进程、创建时间对不上（那个早已退出，进程号被重用了）：当作查不到，弹说明窗，不问、
        不结束那个进程。这里由测试自己拿锁，把记着的改成子进程的进程号和错的创建时间。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(random_names())
        instance = si.acquire(names)
        self.addCleanup(instance.close)
        write_record(instance, pid, self.created(pid) - 1)
        with self.clock(names, 60):
            self.assertIsNone(si._unanswered(si._api, names))
        self.assertEqual((self.asked, self.told), ([], [si.UNKNOWN]))
        self.assertTrue(self.alive(handle))

    def test_answered_while_asking_is_not_ended(self):
        """问的这段时间里它回应了（「已显示」触发了）：点了「结束它并重新打开」也不结束，直接退出。这里由测试自己拿锁，把
        记着的改成子进程（进程号、创建时间都对），问的时候测试这边回应：结束了就会结束那个子进程。"""
        names = random_names()
        proc, handle, held, pid = self.spawn(random_names())
        instance = si.acquire(names)
        self.addCleanup(instance.close)
        write_record(instance, pid, self.created(pid))
        self.on_ask = instance.acknowledge
        with self.clock(names, 60):
            self.assertIsNone(si._unanswered(si._api, names))
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(self.alive(handle))
        self.assertTrue(instance.held)
        self.assertEqual(self.reports, [])


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class ShowRequestTest(unittest.TestCase):
    """已在运行的那个（这里是测试自己拿的锁和假的回调）和再次启动的那个（start）之间的「显示」请求。"""

    def setUp(self):
        self.names = random_names()
        self.instance = si.acquire(self.names)
        self.assertTrue(self.instance.held)
        self.addCleanup(self.instance.close)
        patcher = mock.patch.object(si._api, "allow_foreground")  # 不真的放开前台权限
        self.allow = patcher.start()
        self.addCleanup(patcher.stop)
        # 等满了没回应时的弹窗换成假的（这里拿锁的是测试进程自己，按查不到处理，不会问要不要结束它）
        for name in ("_ask_end", "_tell"):
            patcher = mock.patch.object(si, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def listen(self, on_show) -> si.Listener:
        listener = self.instance.listen(on_show)
        self.addCleanup(listener.stop)
        return listener

    def test_request_reaches_callback(self):
        """再次启动：放开前台权限、触发「显示」；等待线程里调用回调，回调回应以后新启动的那个马上退出（返回 None）。"""
        calls = []

        def on_show():
            calls.append(threading.current_thread() is not threading.main_thread())
            self.instance.acknowledge()

        listener = self.listen(on_show)
        self.assertTrue(listener.running)
        start = time.monotonic()
        # 最多等 20 秒：5 秒内返回就是收到回应退出的，不是等满了退出（机器忙时线程调度晚，上限不能贴着 2 秒）
        self.assertIsNone(si.start(self.names, wait=20))
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(calls, [True])  # 在等待线程里调用，不在调用方的线程里
        self.allow.assert_called_once_with()
        self.assertTrue(self.instance.held)  # 已在运行的那个照旧拿着锁

    def test_request_before_listening_is_handled(self):
        """「显示」在等待线程开始等之前就被触发（悬浮窗还没建好）：保持触发状态，开始等时马上处理。"""
        self.assertTrue(si._ask(si._api, self.names))
        got = threading.Event()
        self.listen(got.set)
        self.assertTrue(got.wait(2.0))

    def test_not_ready_quits_within_wait(self):
        """已在运行的那个还没开始等（刚启动）：新启动的约 2 秒内安静退出，锁仍是已在运行的那个的；这次请求留在事件上，
        悬浮窗建好、开始等时照样处理。"""
        start = time.monotonic()
        self.assertIsNone(si.start(self.names))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, si.WAIT_S - 0.15)
        self.assertLess(elapsed, si.WAIT_S + 4.0)  # 等满就退出，上限只防一直不退出；机器忙时会晚一些
        self.assertTrue(self.instance.held)
        self.assertIsNone(si.acquire(self.names))
        got = threading.Event()
        self.listen(got.set)
        self.assertTrue(got.wait(2.0))

    def test_released_while_waiting_starts_normally(self):
        """等的时候锁释放了（已在运行的那个正在退出）：拿下锁照常启动；自己等的时候发出的请求不留在新的事件上。"""
        timer = threading.Timer(0.3, self.instance.close)
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        mine = si.start(self.names, wait=20)  # 等得够久：机器忙、放锁的线程晚了也照样等到
        elapsed = time.monotonic() - start
        self.assertIsNotNone(mine)  # 拿到了锁（等满了没拿到会返回 None）
        self.addCleanup(mine.close)
        self.assertTrue(mine.held)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 5)
        self.assertIsNone(si.acquire(self.names))  # 现在是它拿着
        got = threading.Event()
        listener = mine.listen(got.set)
        self.addCleanup(listener.stop)
        self.assertFalse(got.wait(0.3))

    def test_stale_answer_is_ignored(self):
        """「已显示」上留着上一次没人等到的回应（新启动的那个已经等不及退出了）：下一次请求不把它当成回应，照样等。"""
        self.instance.acknowledge()
        start = time.monotonic()
        self.assertIsNone(si.start(self.names, wait=0.5))
        self.assertGreaterEqual(time.monotonic() - start, 0.45)

    def test_two_requests_at_once(self):
        """两个同时再次启动：都退出（回应的那个马上退出，没等到回应的等满后安静退出），没有一个拿到锁。"""
        def on_show():
            time.sleep(0.05)
            self.instance.acknowledge()

        self.listen(on_show)
        results = []
        threads = [threading.Thread(target=lambda: results.append(si.start(self.names))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(results, [None, None])
        self.assertTrue(self.instance.held)

    def test_listener_stops_promptly(self):
        """等待线程按时停下（立即，不等超时）；停下以后不再处理请求；再停一次也没关系。"""
        calls = []
        listener = self.instance.listen(lambda: calls.append(1))
        self.assertTrue(listener.running)
        start = time.monotonic()
        self.assertTrue(listener.stop())
        self.assertLess(time.monotonic() - start, 1.5)  # 不等超时（STOP_S 2 秒）；机器忙时线程收尾晚一些
        self.assertFalse(listener.running)
        self.assertTrue(listener.stop())
        self.assertTrue(si._ask(si._api, self.names))
        time.sleep(0.2)
        self.assertEqual(calls, [])

    def test_callback_error_keeps_listening(self):
        """回调出错只记错误记录（换成假的，不写真的错误文件），等待线程照常等下一次。"""
        reports, calls = [], []
        first = threading.Event()

        def on_show():
            calls.append(1)
            if len(calls) == 1:
                first.set()
                raise RuntimeError("回调出错")
            self.instance.acknowledge()

        with mock.patch.object(si, "_report", side_effect=lambda title, detail: reports.append((title, detail))):
            listener = self.listen(on_show)
            self.assertTrue(si._ask(si._api, self.names))
            self.assertTrue(first.wait(2.0))
            self.assertIsNone(si.start(self.names, wait=2.0))
            self.assertTrue(listener.running)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(reports), 1)
        self.assertIn("RuntimeError", reports[0][1])


class FakeApi:
    """假的 Win32 调用：建、开互斥体按给定的结果，事件一律打不开（请不动已在运行的那个显示）。"""

    def __init__(self, create=(0, 0), opened=(0, 2)):
        self.create, self.opened = create, opened
        self.closed, self.asked_names = [], []

    def create_mutex(self, name):
        return self.create

    def open_mutex(self, name):
        self.asked_names.append(name)
        return self.opened

    def create_event(self, name=None, manual=False):
        return 0

    def open_event(self, name):
        return 0

    def open_mapping(self, name):
        return 0, ERROR_FILE_NOT_FOUND

    def close(self, handle):
        if handle:
            self.closed.append(handle)


class FallbackTest(unittest.TestCase):
    def test_no_win32_does_not_block(self):
        """非 Windows 或 ctypes 用不了：不拦，照常启动；不起等待线程，查询一律为「没在运行」。"""
        names = random_names()
        with mock.patch.object(si, "_api", None), mock.patch.object(si, "_held", None), \
                mock.patch.object(si, "NAMES", names):
            instance = si.acquire(names)
            self.assertIsNotNone(instance)
            self.assertFalse(instance.held)
            self.assertFalse(si.start(names).held)
            self.assertTrue(si.claim())
            self.assertFalse(si.running(names.mutex))
            listener = si.listen(lambda: None)
            self.assertFalse(listener.running)
            self.assertTrue(listener.stop())
            si.acknowledge()  # 什么都不做

    def test_load_without_windll(self):
        """ctypes 没有 WinDLL（非 Windows）或加载出错：Win32 调用为 None。"""
        with mock.patch.object(si, "ctypes", SimpleNamespace()):
            self.assertIsNone(si._load())
        with mock.patch.object(si.ctypes, "WinDLL", side_effect=OSError("加载出错")):
            self.assertIsNone(si._load())

    def test_access_denied_counts_as_running(self):
        """以管理员身份运行的那个建的互斥体，普通权限的进程打不开（拒绝访问）：也算已有一个在运行；请不动它显示，
        等满后弹说明窗（它以管理员身份运行，查不到它开了多久），退出；打包脚本的查询同样算在运行。"""
        names = random_names()
        api = FakeApi(create=(0, si.ERROR_ACCESS_DENIED), opened=(0, si.ERROR_ACCESS_DENIED))
        with mock.patch.object(si, "_api", api), mock.patch.object(si, "_tell") as tell, \
                mock.patch.object(si, "_ask_end", side_effect=AssertionError("不该问")):
            self.assertIsNone(si.acquire(names))
            self.assertTrue(si.running(names.mutex))
            start = time.monotonic()
            self.assertIsNone(si.start(names, wait=0.3))
            self.assertGreaterEqual(time.monotonic() - start, 0.25)
        tell.assert_called_once()
        self.assertEqual(tell.call_args.args[0], si.ELEVATED)

    def test_other_errors_do_not_block(self):
        """建互斥体出其他错（例如同名的是别种对象）：查不了，不拦；打包脚本的查询为「没在运行」。"""
        names = random_names()
        api = FakeApi(create=(0, 6), opened=(0, 6))
        with mock.patch.object(si, "_api", api):
            instance = si.acquire(names)
            self.assertIsNotNone(instance)
            self.assertFalse(instance.held)
            self.assertFalse(si.running(names.mutex))

    def test_already_exists_closes_handle(self):
        """互斥体已存在：拿到的是已有那个的句柄，马上关掉（开着时已在运行的那个退出后互斥体也不会消失）。"""
        api = FakeApi(create=(42, si.ERROR_ALREADY_EXISTS))
        with mock.patch.object(si, "_api", api):
            self.assertIsNone(si.acquire(random_names()))
        self.assertEqual(api.closed, [42])

    def test_running_default_name(self):
        """打包脚本不给名字时查正式的名字（这里是假的 Win32 调用，不碰真的互斥体），查完关掉句柄。"""
        api = FakeApi(opened=(7, 0))
        with mock.patch.object(si, "_api", api):
            self.assertTrue(si.running())
        self.assertEqual(api.asked_names, [si.NAMES.mutex])
        self.assertEqual(api.closed, [7])


class _AdminOnly:
    """按「只给管理员组和系统」的权限建命名对象（以管理员身份运行的进程建对象时默认就是这个权限），以及查本进程是不是以管理员
    身份运行。只建测试自己的随机名字。"""

    SDDL = "D:(A;;GA;;;BA)(A;;GA;;;SY)"
    TOKEN_QUERY, TOKEN_ELEVATION = 0x0008, 20

    def __init__(self):
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]

        self.advapi = advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = wintypes.HANDLE
        for fn, args, result in (
                (advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW,
                 [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p], wintypes.BOOL),
                (advapi.OpenProcessToken, [handle, wintypes.DWORD, ctypes.POINTER(handle)], wintypes.BOOL),
                (advapi.GetTokenInformation, [handle, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
                (kernel.GetCurrentProcess, [], handle),
                (kernel.CreateMutexW, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], handle),
                (kernel.CreateFileMappingW, [handle, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                             wintypes.LPCWSTR], handle),
                (kernel.CloseHandle, [handle], wintypes.BOOL),
                (kernel.LocalFree, [ctypes.c_void_p], ctypes.c_void_p)):
            fn.argtypes, fn.restype = args, result
        self.handle_type, self.sa_type = handle, SecurityAttributes

    def elevated(self) -> bool:
        """本进程以管理员身份运行（这时权限挡不住）；查不了时也按是算。"""
        from ctypes import wintypes

        token = self.handle_type()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), self.TOKEN_QUERY, ctypes.byref(token)):
            return True
        try:
            value, size = wintypes.DWORD(), wintypes.DWORD()
            ok = self.advapi.GetTokenInformation(token, self.TOKEN_ELEVATION, ctypes.byref(value),
                                                 ctypes.sizeof(value), ctypes.byref(size))
            return not ok or bool(value.value)
        finally:
            self.kernel.CloseHandle(token)

    def create(self, names: si.Names) -> list[int]:
        """按这个权限建锁（互斥体）和记着是谁的共享内存，返回句柄（用完由调用方 close）。"""
        sd = ctypes.c_void_p()
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.SDDL, 1, ctypes.byref(sd), None):
            raise AssertionError(f"建不了权限：{ctypes.get_last_error()}")
        try:
            sa = self.sa_type(ctypes.sizeof(self.sa_type), sd, False)
            mutex = self.kernel.CreateMutexW(ctypes.byref(sa), False, names.mutex)
            mapping = self.kernel.CreateFileMappingW(self.handle_type(-1).value, ctypes.byref(sa), si.PAGE_READWRITE, 0,
                                                     si.OWNER_RECORD.size, names.owner)
        finally:
            self.kernel.LocalFree(sd)
        handles = [int(h) for h in (mutex, mapping) if h]
        if len(handles) != 2:
            self.close(handles)
            raise AssertionError("建不了锁或共享内存")
        return handles

    def close(self, handles: list[int]) -> None:
        for h in handles:
            self.kernel.CloseHandle(h)


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class ElevatedOwnerTest(unittest.TestCase):
    """已在运行的那个以管理员身份运行：它建的锁和共享内存，普通权限的进程打开时拒绝访问。这里由测试按那种权限
    自己建（随机名字），核对普通权限的本进程：算已在运行（打包脚本的查询也算）；查不到它是谁、开了多久，所以不管它开了多久
    都弹「以管理员身份运行」的说明窗，不问、不结束。本进程以管理员身份运行时权限挡不住，跳过。"""

    def setUp(self):
        self.admin = _AdminOnly()
        if self.admin.elevated():
            self.skipTest("本进程以管理员身份运行，权限挡不住")
        self.names = random_names()
        self.handles = self.admin.create(self.names)
        self.addCleanup(lambda: self.admin.close(self.handles))  # 测试里已经关掉的不再关
        for name, kwargs in (("_ask_end", {"side_effect": AssertionError("不该问")}), ("_tell", {}),
                             ("_report", {"side_effect": AssertionError("不该记错误记录")})):
            patcher = mock.patch.object(si, name, **kwargs)
            setattr(self, name.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(si._api, "allow_foreground")  # 不真的放开前台权限
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_denied_is_told_as_elevated(self):
        """锁和共享内存都拒绝访问：拿不到锁、算在运行；查到的是「打不开」（denied），没有进程号和开了多久；等满后弹说明窗。"""
        api, names = si._api, self.names
        self.assertIsNone(si.acquire(names))
        self.assertTrue(si.running(names.mutex))
        self.assertEqual(api.open_mutex(names.mutex), (0, si.ERROR_ACCESS_DENIED))
        self.assertEqual(api.open_mapping(names.owner), (0, si.ERROR_ACCESS_DENIED))
        self.assertEqual(si._find_owner(api, names), si.Owner(denied=True))
        self.assertIsNone(si.start(names, wait=0.3))
        self.tell.assert_called_once()
        self.assertEqual(self.tell.call_args.args[0], si.ELEVATED)

    def test_exits_while_telling_starts(self):
        """说明窗显示期间它退出了（锁和共享内存随之消失）：说明窗定时试拿锁，拿到了就关掉，本进程照常启动、记上自己。"""
        def tell(kind, released):
            self.assertEqual(kind, si.ELEVATED)
            self.assertFalse(released())  # 它还在：拿不到
            self.admin.close(self.handles)  # 它退出了
            self.handles = []
            self.assertTrue(released())
            self.assertTrue(released())  # 拿到以后再调用也只是报「拿到了」

        self.tell.side_effect = tell
        mine = si.start(self.names, wait=0.3)
        self.assertTrue(mine is not None and mine.held)
        self.addCleanup(mine.close)
        self.assertIsNone(si.acquire(self.names))
        self.assertEqual(read_record(self.names)[:2], (os.getpid(), si._api.current_process()[1]))


S = si.FILETIME_PER_S
OWNER_PID, OWNER_CREATED = 4242, 1000 * S  # 拿着锁、不回应的那个
OWNER_ACQUIRED = OWNER_CREATED + 2 * S  # 它起来以后 2 秒拿到锁
OTHER_PID = 777  # 旁边另一个进程：不该被结束
SELF_PID, SELF_CREATED = 100, 5 * S  # 本进程
WAIT_TIMEOUT, ERROR_INVALID_PARAMETER = 258, 87


class StuckWorld:
    """假的 Win32，等满了没回应以后那条慢路（_unanswered）上用到的全部调用：锁被一个假的进程（OWNER_PID）拿着、不回应，
    共享内存里记着它；进程表、时钟、结束进程都是假的，不碰真的进程和内核对象。结束它以后它拿着的锁随之释放。
    age：它拿到锁以后过了多少秒。on_terminate：请系统结束它时怎样，exit 随即退出，linger 结束得了但一直没退出，already
    结束之前它刚好自己退出了（结束不了）。"""

    def __init__(self, names: si.Names, age: float = 20.0):
        self.names = names
        self.processes = {OWNER_PID: OWNER_CREATED, OTHER_PID: 2000 * S, SELF_PID: SELF_CREATED}  # 进程号 → 创建时间
        self.holder: int | None = OWNER_PID  # 拿着锁的进程
        # None：没有共享内存
        self.record: bytes | None = si.OWNER_RECORD.pack(si.OWNER_MAGIC, OWNER_PID, OWNER_CREATED, OWNER_ACQUIRED)
        self.clock = OWNER_ACQUIRED + int(age * S)
        self.denied: set[str] = set()  # 打不开的：mutex（锁和事件）、mapping、process、terminate
        self.answered = False  # 「已显示」触发着
        self.answer_on_ask = False  # 再请它显示时马上回应
        self.on_terminate = "exit"
        self.terminated: list[tuple[int, int]] = []
        self.handles: dict[int, tuple] = {}
        self._next = 0

    def exit(self, pid: int = OWNER_PID) -> None:
        """进程 pid 退出了：它拿着的锁随之释放（以管理员身份运行的那个的锁也一起消失）。"""
        self.processes.pop(pid, None)
        if self.holder == pid:
            self.holder = None
            self.denied.discard("mutex")

    def _new(self, *what) -> int:
        self._next += 1
        self.handles[self._next] = what
        return self._next

    def create_mutex(self, name):
        if "mutex" in self.denied:
            return 0, si.ERROR_ACCESS_DENIED
        if self.holder is not None:
            return self._new("mutex"), si.ERROR_ALREADY_EXISTS
        self.holder = SELF_PID
        return self._new("mutex"), 0

    def open_mutex(self, name):
        if "mutex" in self.denied:
            return 0, si.ERROR_ACCESS_DENIED
        return (self._new("mutex"), 0) if self.holder is not None else (0, ERROR_FILE_NOT_FOUND)

    def create_event(self, name=None, manual=False):
        return self._new("event", name)

    def open_event(self, name):
        return 0 if "mutex" in self.denied or self.holder is None else self._new("event", name)

    def reset_event(self, handle):
        if self.handles.get(handle) == ("event", self.names.shown):
            self.answered = False
        return True

    def set_event(self, handle):
        if self.handles.get(handle) == ("event", self.names.show) and self.answer_on_ask:
            self.answered = True
        return True

    def allow_foreground(self):
        pass

    def wait(self, handle, ms):
        what = self.handles[handle]
        if what[0] == "process":
            return si.WAIT_OBJECT_0 if self.processes.get(what[1]) != what[2] else WAIT_TIMEOUT
        if what == ("event", self.names.shown) and self.answered:
            self.answered = False
            return si.WAIT_OBJECT_0
        time.sleep(min(ms, 20) / 1000)
        return WAIT_TIMEOUT

    def close(self, handle):
        self.handles.pop(handle, None)

    def create_mapping(self, name, size):
        return self._new("mapping"), 0

    def write_mapping(self, handle, data):
        self.record = data
        return True

    def open_mapping(self, name):
        if "mapping" in self.denied:
            return 0, si.ERROR_ACCESS_DENIED
        return (self._new("mapping"), 0) if self.record is not None else (0, ERROR_FILE_NOT_FOUND)

    def read_mapping(self, handle, size):
        return self.record[:size]

    def current_process(self):
        return SELF_PID, SELF_CREATED

    def open_process(self, pid, terminate=False):
        if pid not in self.processes:
            return 0, ERROR_INVALID_PARAMETER
        if "process" in self.denied or (terminate and "terminate" in self.denied):
            return 0, si.ERROR_ACCESS_DENIED
        return self._new("process", pid, self.processes[pid]), 0

    def process_created(self, handle):
        return self.handles[handle][2]

    def terminate(self, handle, code):
        _, pid, created = self.handles[handle]
        if self.processes.get(pid) != created:
            return False  # 已经退出的进程不让再结束
        if self.on_terminate == "already":
            self.exit(pid)
            return False
        self.terminated.append((pid, code))
        if self.on_terminate == "exit":
            self.exit(pid)
        return True

    def now(self):
        return self.clock


class StuckDecisionTest(unittest.TestCase):
    """等满了没回应、锁也没释放以后怎么办（single_instance._unanswered），用假的 Win32（StuckWorld）、假的弹窗
    逐个情形核对：什么时候问、什么时候安静退出、什么时候弹说明窗；点了「结束它并重新打开」以后只结束对得上的那个。"""

    def setUp(self):
        self.names = random_names()
        self.world = StuckWorld(self.names)
        self.asked, self.told, self.reports, self.polled = [], [], [], []
        self.answer = True
        self.on_ask = None
        self.while_shown = None  # 弹窗显示期间发生的事：（弹的是哪个窗, 定时试拿锁的 released）→ 弹窗是不是因此关掉了
        for attr, kwargs in (("_api", {"new": self.world}), ("RECHECK_S", {"new": 0.05}), ("TAKE_S", {"new": 0.2}),
                             ("_ask_end", {"side_effect": self.ask}), ("_tell", {"side_effect": self.tell}),
                             ("_report", {"side_effect": lambda title, detail: self.reports.append((title, detail))})):
            patcher = mock.patch.object(si, attr, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ask(self, age: float, released=None) -> bool:
        """假的问窗：记下问了（带着开了多久）；弹窗期间它自己退出、弹窗关掉了时同没点「结束它并重新打开」。"""
        self.asked.append(age)
        if self.on_ask is not None:
            self.on_ask()
        if self.while_shown is not None and self.while_shown("ask", released):
            return False
        return self.answer

    def tell(self, kind: str, released=None) -> None:
        """假的说明窗。"""
        self.told.append(kind)
        if self.while_shown is not None:
            self.while_shown(kind, released)

    def exits_while(self, shown: str):
        """弹窗 shown 显示期间：定时试拿锁一次（它还在，拿不到），它自己退出了，再试一次（拿到了，弹窗关掉）。"""
        def run(kind, released) -> bool:
            if kind != shown:
                return False
            self.polled.append(released())
            self.world.exit()
            self.polled.append(released())
            return self.polled[-1]

        return run

    def decide(self) -> si.Instance | None:
        return si._unanswered(self.world, self.names)

    def test_old_enough_asks(self):
        """查得到它、已经运行满 15 秒（这里 20 秒）：问，问的时候带上它运行了多久；点「取消」：退出，不结束它。"""
        self.answer = False
        self.assertIsNone(self.decide())
        self.assertEqual((self.asked, self.told, self.world.terminated), ([20.0], [], []))
        self.assertEqual(self.world.holder, OWNER_PID)

    def test_just_started_quits_quietly(self):
        """拿到锁还不满 15 秒（刚开、还在加载）：照旧安静退出，不问、不弹说明窗。刚好满 15 秒算卡住。"""
        for age, asked in ((0.0, []), (14.9, []), (15.0, [15.0])):
            with self.subTest(age=age):
                self.world.clock = OWNER_ACQUIRED + int(age * S)
                self.asked.clear()
                self.answer = False
                self.assertIsNone(self.decide())
                self.assertEqual((self.asked, self.told), (asked, []))
        self.assertEqual(self.world.terminated, [])

    def test_takeover_loading_quits_quietly(self):
        """接手的那个刚拿到锁、正在加载时再次启动（它起来以后在弹窗上等用户点了 10 分钟，结束了没有响应的那个才拿到锁）：
        开了多久按拿到锁的时刻算，3 秒，不问、不弹说明窗、不结束，安静退出。"""
        self.world.clock = OWNER_CREATED + 600 * S
        self.world.record = si.OWNER_RECORD.pack(si.OWNER_MAGIC, OWNER_PID, OWNER_CREATED, self.world.clock - 3 * S)
        self.assertEqual(si._find_owner(self.world, self.names), si.Owner(OWNER_PID, OWNER_CREATED, 3.0))
        self.assertIsNone(self.decide())
        self.assertEqual((self.asked, self.told, self.world.terminated, self.reports), ([], [], [], []))
        self.assertEqual(self.world.holder, OWNER_PID)

    def test_end_only_the_matching_one(self):
        """点「结束它并重新打开」：只结束对得上的那个（旁边另一个进程不动），它结束以后拿下锁照常启动，记上自己（拿到锁的
        时刻是现在）；错误记录里记一笔是谁、运行了多久。"""
        mine = self.decide()
        self.assertIsNotNone(mine)
        self.assertTrue(mine.held)
        self.assertEqual(self.world.terminated, [(OWNER_PID, si.ENDED_CODE)])
        self.assertIn(OTHER_PID, self.world.processes)
        self.assertEqual(self.world.holder, SELF_PID)
        self.assertEqual(si.OWNER_RECORD.unpack(self.world.record),
                         (si.OWNER_MAGIC, SELF_PID, SELF_CREATED, self.world.clock))
        self.assertEqual(self.told, [])
        (title, detail), = self.reports
        self.assertEqual(title, "结束了没有响应的拾星助手（用户选了「结束它并重新打开」）")
        self.assertIn(f"进程号 {OWNER_PID}，已运行约 20 秒", detail)

    def test_answered_while_asking(self):
        """问的这段时间里它回应了（「已显示」触发了）：点了「结束它并重新打开」也不结束，直接退出。"""
        self.on_ask = lambda: setattr(self.world, "answered", True)
        self.assertIsNone(self.decide())
        self.assertEqual(self.world.terminated, [])
        self.assertEqual(self.world.holder, OWNER_PID)

    def test_answers_when_asked_again(self):
        """问的时候没回应，点了以后再请它显示一次，它回应了（刚缓过来，或者它的回应被同时在等的别的进程取走了）：不结束。"""
        self.on_ask = lambda: setattr(self.world, "answer_on_ask", True)
        self.assertIsNone(self.decide())
        self.assertEqual(self.world.terminated, [])

    def test_exited_while_asking(self):
        """问的这段时间里它自己退出了（锁释放了）：不用结束谁，拿下锁照常启动。"""
        def gone():
            del self.world.processes[OWNER_PID]
            self.world.holder = None

        self.on_ask = gone
        mine = self.decide()
        self.assertTrue(mine is not None and mine.held)
        self.assertEqual(self.world.terminated, [])
        self.assertEqual(self.reports, [])

    def test_reused_pid_is_not_ended(self):
        """记的进程号对得上一个活着的进程、创建时间对不上（被重用了）：当作查不到，弹说明窗，不问、不结束。"""
        self.world.processes[OWNER_PID] = OWNER_CREATED + 1
        self.assertIsNone(self.decide())
        self.assertEqual((self.asked, self.told, self.world.terminated), ([], [si.UNKNOWN], []))

    def test_replaced_while_asking_is_not_ended(self):
        """问的时候核对过；问的这段时间里它退出了、进程号被别的进程重用，锁也被别的拿走：结束之前再核对一次创建时间，对不上
        就不结束，拿不到锁，退出。"""
        def replaced():
            self.world.processes[OWNER_PID] = OWNER_CREATED + 5 * S
            self.world.holder = OTHER_PID

        self.on_ask = replaced
        self.assertIsNone(self.decide())
        self.assertEqual(self.world.terminated, [])
        self.assertEqual((self.told, self.reports), ([], []))

    def test_not_found_tells(self):
        """查不到（更早的版本没有这块共享内存、读出来不是这个格式、只记进程号和创建时间的旧格式、进程已经不在、记的是本进程
        自己）：弹说明窗，不问、不结束。"""
        def no_record():
            self.world.record = None

        def garbage():
            self.world.record = b"\x00" * si.OWNER_RECORD.size

        def old_format():  # 旧格式：标记 ACC1、进程号、创建时间，16 个字节
            self.world.record = struct.pack("<4sIQ", b"ACC1", OWNER_PID, OWNER_CREATED)

        def old_magic():  # 长度对上了，标记还是旧的
            self.world.record = si.OWNER_RECORD.pack(b"ACC1", OWNER_PID, OWNER_CREATED, OWNER_ACQUIRED)

        def gone():
            del self.world.processes[OWNER_PID]

        def myself():
            self.world.record = si.OWNER_RECORD.pack(si.OWNER_MAGIC, SELF_PID, SELF_CREATED, OWNER_ACQUIRED)

        for case in (no_record, garbage, old_format, old_magic, gone, myself):
            with self.subTest(case=case.__name__):
                self.world = StuckWorld(self.names)
                case()
                self.told.clear()
                with mock.patch.object(si, "_api", self.world):
                    self.assertIsNone(self.decide())
                self.assertEqual((self.asked, self.told, self.world.terminated), ([], [si.UNKNOWN], []))

    def test_elevated_tells(self):
        """它以管理员身份运行（锁、共享内存或进程打不开）：已经运行满 15 秒或者查不到开了多久时，弹说明窗（写明以管理员身份
        运行），不问、不结束；查得到它刚启动时照旧安静退出。"""
        for denied, age, told in (({"mutex"}, 20, [si.ELEVATED]), ({"mutex", "mapping"}, 20, [si.ELEVATED]),
                                  ({"process"}, 20, [si.ELEVATED]), ({"mutex"}, 5, [])):
            with self.subTest(denied=denied, age=age):
                self.world = StuckWorld(self.names, age=age)
                self.world.denied = set(denied)
                self.told.clear()
                with mock.patch.object(si, "_api", self.world):
                    self.assertIsNone(self.decide())
                self.assertEqual((self.asked, self.told, self.world.terminated), ([], told, []))

    def test_cannot_end_tells(self):
        """点了「结束它并重新打开」却结束不了（打不开它的进程，权限不够）：弹说明窗，退出；错误记录里记一笔。"""
        self.world.denied = {"terminate"}
        self.assertIsNone(self.decide())
        self.assertEqual((self.told, self.world.terminated), ([si.CANNOT_END], []))
        self.assertEqual([title for title, _ in self.reports], ["没能结束没有响应的拾星助手"])

    def test_exits_while_shown_starts(self):
        """问的窗、说明窗（查不到、以管理员身份运行、结束不了）显示期间它自己退出了（正常退出时收尾慢、缓过来以后退出、用户在
        任务管理器里结束了它）：弹窗定时试拿锁，它还在时拿不到，退出以后拿到了，弹窗关掉，本进程照常启动、记上自己，不结束
        谁。"""
        def question():
            pass

        def unknown():
            self.world.record = None

        def elevated():
            self.world.denied = {"mutex", "mapping"}

        def cannot_end():
            self.world.denied = {"terminate"}

        for setup, shown in ((question, "ask"), (unknown, si.UNKNOWN), (elevated, si.ELEVATED),
                             (cannot_end, si.CANNOT_END)):
            with self.subTest(shown=shown):
                self.world = StuckWorld(self.names)
                setup()
                self.polled.clear()
                self.while_shown = self.exits_while(shown)
                with mock.patch.object(si, "_api", self.world):
                    mine = self.decide()
                self.assertTrue(mine is not None and mine.held)
                self.assertEqual(self.polled, [False, True])
                self.assertEqual(self.world.holder, SELF_PID)
                self.assertEqual(si.OWNER_RECORD.unpack(self.world.record)[1], SELF_PID)
                self.assertEqual(self.world.terminated, [])

    def test_exits_while_asking_through_claim(self):
        """从 claim() 走：问的窗显示期间它自己退出了，claim() 返回 True（run() 照常启动悬浮窗）；没有问出结果也不结束谁。"""
        self.answer = False
        self.while_shown = self.exits_while("ask")
        with mock.patch.object(si, "NAMES", self.names), mock.patch.object(si, "_held", None):
            self.assertTrue(si.claim())  # 先等满 WAIT_S
            self.assertTrue(si._held.held)
        self.assertEqual((len(self.asked), self.polled, self.world.terminated), (1, [False, True], []))

    def test_released_counts_only_the_lock(self):
        """弹窗期间试拿锁（_Released）：它还在时拿不到；查不了、不拦时的空 Instance 不算（那时它可能还在运行，不能另开一个）；
        试的时候出错记一笔，之后不再试；它退出以后拿到了，之后再调用也是「拿到了」，用的是同一个。"""
        released = si._Released(self.names)
        self.assertFalse(released())
        with mock.patch.object(si, "acquire", return_value=si.Instance()):
            self.assertFalse(released())
        with mock.patch.object(si, "acquire", side_effect=OSError("出错")) as acquire:
            self.assertFalse(released())
            self.assertFalse(released())
        self.assertEqual(acquire.call_count, 1)
        self.assertEqual([title for title, _ in self.reports], ["弹窗显示期间试拿锁时出错（不再试，弹窗照常等用户点）"])
        self.world.exit()
        self.assertFalse(released())  # 出过错的不再试
        fresh = si._Released(self.names)
        self.assertTrue(fresh())
        instance = fresh.instance
        self.assertTrue(instance.held)
        self.assertTrue(fresh())
        self.assertIs(fresh.instance, instance)

    def test_end_results_are_logged_as_they_are(self):
        """结束的结果按实际记：请系统结束它以后没等到它退出，另记一笔（不记「结束了」），锁等不到释放就退出；结束之前它刚好
        自己退出了（结束不了），不记「结束了」，拿下锁照常启动。"""
        self.world.on_terminate = "linger"
        self.assertIsNone(self.decide())
        self.assertEqual(self.world.terminated, [(OWNER_PID, si.ENDED_CODE)])
        (title, detail), = self.reports
        self.assertEqual(title, "已请系统结束没有响应的拾星助手，但它没有及时退出（用户选了「结束它并重新打开」）")
        self.assertIn(f"进程号 {OWNER_PID}", detail)
        self.assertEqual(self.world.holder, OWNER_PID)
        self.reports.clear()
        self.world = StuckWorld(self.names)
        self.world.on_terminate = "already"
        with mock.patch.object(si, "_api", self.world):
            mine = self.decide()
        self.assertTrue(mine is not None and mine.held)
        self.assertEqual((self.world.terminated, self.reports, self.told), ([], [], []))

    def test_through_start_and_claim(self):
        """从 start() 走：等满了没回应，问；点了「结束它并重新打开」，拿到锁。从 claim() 走（等满 WAIT_S）同样，claim()
        返回 True（run() 照常启动悬浮窗）。"""
        with mock.patch.object(si, "NAMES", self.names), mock.patch.object(si, "_held", None):
            mine = si.start(self.names, wait=0.05)
            self.assertTrue(mine is not None and mine.held)
            self.world = StuckWorld(self.names)
            with mock.patch.object(si, "_api", self.world):
                self.assertTrue(si.claim())
                self.assertTrue(si._held.held)
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.world.terminated, [(OWNER_PID, si.ENDED_CODE)])

    def test_error_quits(self):
        """处理时出错（例如弹窗出错）：照旧退出，不另开一个；错误记录里记下调用栈。"""
        self.on_ask = mock.Mock(side_effect=RuntimeError("弹窗出错"))
        self.assertIsNone(si.start(self.names, wait=0.05))
        self.assertEqual(self.world.terminated, [])
        (title, detail), = self.reports
        self.assertEqual(title, "处理没有响应的拾星助手时出错（新启动的照旧退出）")
        self.assertIn("RuntimeError: 弹窗出错", detail)


class StuckPromptTest(unittest.TestCase):
    """没有响应时的弹窗（advisor/ui/stuck_prompt.py）：离屏建好、不显示，核对文字、按钮、默认按钮、图标和总在最前；点哪个
    按钮由假的 _show 替用户点。显示期间定时试拿锁的那一条用真的 _show，只在离屏平台上弹（不在桌面上出窗口）。"""

    @classmethod
    def setUpClass(cls):
        from tests.support import qtenv

        cls.app = qtenv.app()
        from advisor.ui import stuck_prompt

        cls.sp = stuck_prompt

    def click(self, text: str | None):
        """假的 _show：点文字是 text 的按钮（None：什么都不点，同直接关掉窗口）。"""
        shown = []

        def show(box, released=None):
            shown.append(box)
            if text is not None:
                next(b for b in box.buttons() if b.text() == text).click()

        return mock.patch.object(self.sp, "_show", side_effect=show), shown

    def test_docs_quote_dialog(self):
        """使用说明「常见问题」引用的弹窗文字和按钮名是弹窗上实际显示的。"""
        manual = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
        faq = manual[manual.index("## 常见问题"):manual.index("## 详细说明")]
        for text in (self.sp.STUCK, self.sp.END_BUTTON, self.sp.CANCEL_BUTTON):
            self.assertIn(f"「{text}」", faq)

    def test_closes_itself_when_released(self):
        """显示期间每 0.2 秒调用一次 released（试拿锁）：返回 True（它自己退出了、已经拿到锁）就关掉弹窗，问的窗按没点
        「结束它并重新打开」返回 False；关掉以后不再调用。说明窗一样。弹窗真的显示（离屏），5 秒还没关掉时由保险的定时器
        替用户点掉，测试照样报错。"""
        from PySide6.QtCore import QTimer

        if self.app.platformName() != "offscreen":
            self.skipTest("只在离屏平台上真的弹窗")
        show = self.sp._show

        def guarded(box, released=None):
            guard = QTimer(box)  # 保险：跟着弹窗一起销毁
            guard.setSingleShot(True)
            guard.timeout.connect(box.reject)
            guard.start(5000)
            try:
                show(box, released)
            finally:
                guard.stop()

        for name, call, want in (("问的窗", lambda released: self.sp.ask_end(100, released), False),
                                 ("说明窗", lambda released: self.sp.tell(si.UNKNOWN, released), None)):
            with self.subTest(name=name):
                calls = []

                def released() -> bool:
                    calls.append(time.monotonic())
                    return len(calls) >= 3

                start = time.monotonic()
                with mock.patch.object(self.sp, "_show", side_effect=guarded):
                    result = call(released)
                elapsed = time.monotonic() - start
                self.assertIs(result, want)
                self.assertEqual(len(calls), 3)
                self.assertGreaterEqual(elapsed, 3 * si.DIALOG_POLL_S - 0.05)
                self.assertLess(elapsed, 4)
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.02)
                self.assertEqual(len(calls), 3)

    def assert_box(self, box):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        self.assertEqual(box.windowTitle(), "拾星助手")
        self.assertEqual(box.icon(), QMessageBox.Icon.NoIcon)  # 不设图标类型：Qt 不放系统提示音
        self.assertFalse(box.iconPixmap().isNull())  # 照样有警告图案
        self.assertFalse(box.windowIcon().isNull())
        self.assertTrue(box.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_question(self):
        """「拾星助手已在运行，但没有响应。」接运行了多久、点了以后会怎样；「结束它并重新打开」「取消」两个按钮，默认按钮和
        Esc 都是「取消」。"""
        box, end = self.sp.question(20.4)
        self.assert_box(box)
        self.assertEqual(box.text(), "拾星助手已在运行，但没有响应。")
        self.assertEqual(box.informativeText(), "它已运行约 20 秒。点击「结束它并重新打开」将结束它，然后重新打开拾星助手。")
        self.assertEqual(sorted(b.text() for b in box.buttons()), sorted(["结束它并重新打开", "取消"]))
        self.assertEqual(end.text(), "结束它并重新打开")
        self.assertEqual(box.defaultButton().text(), "取消")
        self.assertEqual(box.escapeButton().text(), "取消")

    def test_ask_end(self):
        """点「结束它并重新打开」为 True；点「取消」、直接关掉窗口为 False。每次只弹一个窗。"""
        for text, want in (("结束它并重新打开", True), ("取消", False), (None, False)):
            with self.subTest(text=text):
                patcher, shown = self.click(text)
                with patcher:
                    self.assertIs(self.sp.ask_end(100), want)
                self.assertEqual(len(shown), 1)

    def test_notices(self):
        """说明窗只有「确定」（默认按钮、Esc 都是它）；请用户在任务管理器中结束，发行版写 ACC.exe，开发版另写 Python；
        除了程序的文件名，没有英文。"""
        expected = {
            si.UNKNOWN: ("拾星助手已在运行，但没有响应。",
                         "如果它刚刚打开、还在加载，请稍等片刻；否则请在任务管理器的「详细信息」页中结束 ACC.exe 后重新打开。"),
            si.ELEVATED: ("拾星助手正以管理员身份运行，无法从这里显示或结束它。",
                          "请点击托盘图标显示悬浮窗；如果它没有响应，请在任务管理器的「详细信息」页中结束 ACC.exe 后重新打开。"),
            si.CANNOT_END: ("无法结束没有响应的拾星助手。",
                            "它可能正以管理员身份运行。请在任务管理器的「详细信息」页中结束 ACC.exe 后重新打开。"),
        }
        with mock.patch.object(self.sp.paths, "is_frozen", return_value=True):
            for kind, (text, detail) in expected.items():
                with self.subTest(kind=kind):
                    box = self.sp.notice(kind)
                    self.assert_box(box)
                    self.assertEqual((box.text(), box.informativeText()), (text, detail))
                    self.assertEqual([b.text() for b in box.buttons()], ["确定"])
                    self.assertEqual(box.defaultButton().text(), "确定")
                    self.assertEqual(box.escapeButton().text(), "确定")
                    self.assertNotRegex((text + detail).replace("ACC.exe", ""), "[A-Za-z]")
        with mock.patch.object(self.sp.paths, "is_frozen", return_value=False):
            self.assertIn("ACC.exe（开发版是 pythonw.exe 或 python.exe）", self.sp.notice(si.UNKNOWN).informativeText())
        patcher, shown = self.click("确定")
        with patcher:
            self.assertIsNone(self.sp.tell(si.CANNOT_END))
        self.assertEqual(len(shown), 1)

    def test_duration(self):
        cases = ((15, "约 15 秒"), (59.4, "约 59 秒"), (60, "约 1 分钟"), (90, "约 2 分钟"), (3599, "约 60 分钟"),
                 (3600, "约 1 小时"), (5400, "约 2 小时"), (86400 * 2.4, "约 2 天"), (0, "约 1 秒"))
        for seconds, text in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(self.sp.duration(seconds), text)

    def test_reuses_application(self):
        """已有 QApplication 时用那一个（悬浮窗的 build() 同样先取已有的，两边是同一个）。"""
        from PySide6.QtWidgets import QApplication

        self.assertIs(self.sp._application(), QApplication.instance())


class RunWiringTest(unittest.TestCase):
    """run()（开发版和发行版的入口）：已有一个在运行时不调用悬浮窗的 main()、正常返回（退出码 0）、不弹窗、不写
    overlay_err.txt；没有时照常调用 main()；查的时候出错不拦。claim 和 main 换成假的，不起悬浮窗。"""

    def setUp(self):
        import advisor.ui as ui
        from advisor.ui import overlay

        self.ui, self.overlay = ui, overlay
        # main() 在 overlay_parts.app 里：按它实际所在的模块打桩（overlay 只再导出一份）
        self.main_mod = sys.modules[overlay_app.main.__module__]
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.err = Path(self._dir.name) / "overlay_err.txt"

    def run_ui(self, claim) -> tuple[list, mock.Mock]:
        calls = []
        logged = mock.Mock()
        with mock.patch.object(si, "claim", claim), mock.patch.object(self.main_mod, "main", lambda: calls.append("main")), \
                mock.patch.object(self.ui, "ERR_FILE", self.err), mock.patch.object(self.ui, "log_error", logged), \
                mock.patch.object(self.ui, "report_startup_error", side_effect=AssertionError("不该弹窗")):
            self.assertIsNone(self.ui.run())
        return calls, logged

    def test_already_running_returns_quietly(self):
        calls, logged = self.run_ui(lambda: False)
        self.assertEqual(calls, [])
        logged.assert_not_called()
        self.assertFalse(self.err.exists())

    def test_not_running_starts_overlay(self):
        calls, logged = self.run_ui(lambda: True)
        self.assertEqual(calls, ["main"])
        logged.assert_not_called()

    def test_check_error_starts_overlay(self):
        calls, logged = self.run_ui(mock.Mock(side_effect=OSError("查询出错")))
        self.assertEqual(calls, ["main"])
        logged.assert_called_once()
        self.assertTrue(logged.call_args.kwargs.get("append"))


RELAUNCH = r"""
import sys, time, types
from pathlib import Path
import advisor.ui as ui
from advisor.ui import single_instance as si
si.NAMES = si.Names(*sys.argv[1:5])  # 测试专用的名字
_start = si.start
si.start = lambda names, wait=20.0: _start(names, wait)  # 最多等 20 秒：几秒内退出就是收到了回应，不是等满了
ui.ERR_FILE = sys.modules[ui.log_error.__module__].ERR_FILE = Path(sys.argv[5])  # log_error 按它所在模块的 ERR_FILE 写
si._api.allow_foreground = lambda: None  # 不真的放开前台权限
# 万一等满了没回应，弹窗也换成假的，不显示窗口
si._ask_end = lambda age, released=None: print("ASK", flush=True) and False
si._tell = lambda kind, released=None: print("TELL", kind, flush=True)
# 万一没拦住，也只调用这个假的 main()，不起悬浮窗（run() 从 overlay_parts.app 导入 main，两处都换掉）
fake = types.ModuleType("advisor.ui.overlay")
fake.main = lambda: print("MAIN", flush=True)
sys.modules["advisor.ui.overlay"] = sys.modules["advisor.ui.overlay_parts.app"] = fake
start = time.monotonic()
ui.run()
print("RESULT", int("PySide6" in sys.modules), round(time.monotonic() - start, 3), flush=True)
"""


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class RelaunchProcessTest(unittest.TestCase):
    def test_second_launch_asks_and_exits(self):
        """真的再起一个进程走 run()（名字换成测试专用的）：已有一个在运行时请它显示（回调被调用一次），收到回应后马上退出，
        退出码 0，没有导入 PySide6，没写 overlay_err.txt。"""
        names = random_names()
        instance = si.acquire(names)
        self.addCleanup(instance.close)
        calls = []

        def on_show():
            calls.append(1)
            instance.acknowledge()

        listener = instance.listen(on_show)
        self.addCleanup(listener.stop)
        tmp = tmpdir()
        self.addCleanup(tmp.cleanup)
        err = Path(tmp.name) / "overlay_err.txt"
        proc = subprocess.run([sys.executable, "-c", RELAUNCH, names.mutex, names.show, names.shown, names.owner,
                               str(err)], cwd=str(ROOT), env=child_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertNotIn("MAIN", proc.stdout.splitlines())  # 没走到悬浮窗的 main()
        self.assertFalse([s for s in proc.stdout.splitlines() if s.startswith(("ASK", "TELL"))])  # 没弹窗
        line = next(s for s in proc.stdout.splitlines() if s.startswith("RESULT "))
        _, pyside, seconds = line.split()
        self.assertEqual(pyside, "0")
        self.assertLess(float(seconds), 5)  # 收到回应就退出，不等满（子进程里等满是 20 秒）
        self.assertEqual(calls, [1])
        self.assertFalse(err.exists())
        self.assertTrue(instance.held)


STUCK_RUN = r"""
import sys, time, types
from pathlib import Path
import advisor.ui as ui
from advisor.ui import single_instance as si
si.NAMES = si.Names(*sys.argv[1:5])  # 测试专用的名字
choice = sys.argv[5]  # 弹窗上替用户点的按钮；空的：不点，等弹窗自己关掉（20 秒还没关时保险起见点「取消」）
ui.ERR_FILE = sys.modules[ui.log_error.__module__].ERR_FILE = Path(sys.argv[6])  # log_error 按它所在模块的 ERR_FILE 写
si._api.allow_foreground = lambda: None  # 不真的放开前台权限
now = si._api.now
si._api.now = lambda: now() + 60 * si.FILETIME_PER_S  # 假装拿锁的子进程已经运行了 1 分钟
si.ENDED_CODE = 0x5EED  # 少见的退出码：证明拿锁的子进程是这里结束的
ask_end = si._ask_end

def asked(age, released=None):
    # 弹窗用真的 stuck_prompt（真的建 QApplication、真的 exec()、真的定时试拿锁），只是由定时器替用户点按钮；不是离屏平台
    # 就不弹
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from advisor.ui import stuck_prompt as sp
    show = sp._show

    def click_later(box, released=None):
        if QGuiApplication.platformName() != "offscreen":
            raise SystemExit("不是离屏平台：" + QGuiApplication.platformName())
        text, ms = (choice, 200) if choice else ("取消", 20000)
        timer = QTimer(box)  # 跟着弹窗一起销毁
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: next(b for b in box.buttons() if b.text() == text).click())
        timer.start(ms)
        show(box, released)
        timer.stop()

    sp._show = click_later
    print("ASK", flush=True)
    return ask_end(age, released)

si._ask_end = asked
si._tell = lambda kind, released=None: print("TELL", kind, flush=True)

def main():
    # 假的悬浮窗 main()：同 build() 先取已有的 QApplication，再跑一小会儿事件循环（弹窗关掉时没有留下让它马上退出的请求）
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from advisor.ui import stuck_prompt as sp
    app = QApplication.instance() or QApplication(sys.argv)
    QTimer.singleShot(300, app.quit)
    start = time.monotonic()
    app.exec()
    print("MAIN", int(app is sp._app), round(time.monotonic() - start, 3), flush=True)

fake = types.ModuleType("advisor.ui.overlay_parts.app")
fake.main = main
sys.modules["advisor.ui.overlay"] = sys.modules["advisor.ui.overlay_parts.app"] = fake  # 不起悬浮窗
ui.run()
print("RESULT", int("PySide6" in sys.modules), flush=True)
"""


@unittest.skipUnless(WINDOWS, SKIP_REASON)
class StuckRelaunchProcessTest(ChildProcesses):
    """真的再起一个进程走 run()（名字换成测试专用的）：拿锁的子进程不回应、假装它已经拿到锁 1 分钟了。弹窗用
    真的 stuck_prompt，只在离屏平台上建（不在桌面上出窗口），由定时器替用户点按钮（或者不点，等拿锁的子进程自己退出、弹窗
    自己关掉）；悬浮窗的 main() 换成假的。结束的只有测试自己起的拿锁子进程。"""

    def launch(self, names: si.Names, choice: str) -> tuple[subprocess.Popen, Path]:
        """起再次启动的那个进程（STUCK_RUN），返回（Popen, 它的错误记录文件）。"""
        tmp = tmpdir()
        self.addCleanup(tmp.cleanup)
        err = Path(tmp.name) / "overlay_err.txt"
        env = child_env()
        if not env.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
            env["QT_QPA_PLATFORM"] = "offscreen"
        proc = subprocess.Popen([sys.executable, "-c", STUCK_RUN, names.mutex, names.show, names.shown, names.owner,
                                 choice, str(err)], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace")
        self.addCleanup(self.reap, proc)
        return proc, err

    @staticmethod
    def reap(proc: subprocess.Popen) -> None:
        """测试出错时它可能还在：结束它（测试自己起的），关掉管道。"""
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def relaunch(self, names: si.Names, choice: str) -> tuple[subprocess.CompletedProcess, Path]:
        """起再次启动的那个进程，等它退出。"""
        proc, err = self.launch(names, choice)
        out, errors = proc.communicate(timeout=120)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, errors), err

    def test_cancel_exits_with_zero(self):
        """点「取消」：新启动的退出，退出码 0（启动程序看到 0 不另弹窗），不起悬浮窗；拿锁的那个不动，不写错误记录。
        这条慢路上才导入 PySide6，建了 QApplication 也照样正常退出。"""
        names = random_names()
        _, handle, held, _ = self.spawn(names)
        self.assertTrue(held)
        proc, err = self.relaunch(names, "取消")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = proc.stdout.splitlines()
        self.assertIn("ASK", lines)
        self.assertFalse([s for s in lines if s.startswith(("MAIN", "TELL"))], lines)
        self.assertIn("RESULT 1", lines)
        self.assertTrue(self.alive(handle))
        self.assertFalse(err.exists())

    def test_end_reopens_on_same_application(self):
        """点「结束它并重新打开」：结束拿锁的子进程（退出码换成少见的值，证明是这里结束的），拿下锁照常启动；悬浮窗用的是
        弹窗时建的同一个 QApplication，事件循环照常跑到自己退出；退出码 0，错误记录里记一笔。"""
        names = random_names()
        _, handle, held, pid = self.spawn(names)
        self.assertTrue(held)
        proc, err = self.relaunch(names, "结束它并重新打开")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = proc.stdout.splitlines()
        self.assertIn("ASK", lines)
        self.assertFalse([s for s in lines if s.startswith("TELL")], lines)
        _, same, seconds = next(s for s in lines if s.startswith("MAIN ")).split()
        self.assertEqual(same, "1")
        self.assertGreaterEqual(float(seconds), 0.25)
        self.assertEqual(self.k.WaitForSingleObject(handle, 10000), WAIT_OBJECT_0)
        self.assertEqual(self.exit_code(handle), 0x5EED)
        log = err.read_text(encoding="utf-8")
        self.assertIn("结束了没有响应的拾星助手", log)
        self.assertIn(f"进程号 {pid}", log)

    def test_exits_by_itself_while_asking(self):
        """问的窗显示期间，拿锁的子进程自己正常退出了（同退出时收尾超过 2 秒）：弹窗定时试拿锁，拿到了就自己关掉（不替用户
        点任何按钮），接着照常启动（假的 main()），用的是同一个 QApplication；不结束谁，不写错误记录，退出码 0。"""
        names = random_names()
        holder, handle, held, _ = self.spawn(names)
        self.assertTrue(held)
        proc, err = self.launch(names, "")
        lines: list[str] = []
        errors: list[str] = []
        asked = threading.Event()

        def read():
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                if line.startswith("ASK"):
                    asked.set()

        readers = [threading.Thread(target=read, daemon=True),
                   threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)]
        for reader in readers:
            reader.start()
        self.assertTrue(asked.wait(60), lines)
        time.sleep(0.5)  # 弹窗已经在等，先试过几次（它还在，拿不到）
        holder.stdin.close()  # 子进程读到结尾，正常退出
        self.assertEqual(self.k.WaitForSingleObject(handle, 30000), WAIT_OBJECT_0)
        self.assertEqual(self.exit_code(handle), 0)
        code = proc.wait(timeout=60)
        for reader in readers:
            reader.join(10)
        self.assertEqual(code, 0, "".join(errors)[-2000:])
        self.assertFalse([s for s in lines if s.startswith("TELL")], lines)
        _, same, seconds = next(s for s in lines if s.startswith("MAIN ")).split()
        self.assertEqual(same, "1")
        self.assertGreaterEqual(float(seconds), 0.25)
        self.assertIn("RESULT 1", lines)
        self.assertFalse(err.exists())


class RelaunchOverlayTest(unittest.TestCase):
    """已在运行的悬浮窗收到「显示」请求（Overlay.on_relaunch）：和点托盘图标一样，收到托盘的悬浮窗显示出来；显示之后才回应
    新启动的那个（relaunch_ack）；识别记录里记一条 relaunch（hidden：显示之前是否收在托盘里）。悬浮窗用 offscreen 平台建，
    不显示在屏幕上，不起识别。"""

    def setUp(self):
        from advisor.data import load_db
        from advisor.ui.overlay import Overlay
        from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
        from tests.support import qtenv

        self.app = qtenv.app()
        db = load_db()
        self.session = OverlaySession(db, UiSettings(hero="凛"))
        window = self.window = Overlay(db, self.session, threading.Lock())
        self.addCleanup(window.hide)
        window.tray_part.tray = SimpleNamespace(showMessage=lambda *a: None, setToolTip=lambda t: None)
        window.show()
        window.tray_part.close_clicked()  # 收到托盘
        self.assertTrue(window.isHidden())

    def traced(self) -> list:
        return [e for e in self.session.recent_events(600) if e["kind"] == "read:user" and e.get("action") == "relaunch"]

    def test_signal_handled_in_ui_thread(self):
        """请求从等待线程（这里是测试起的线程）经 relaunch_requested 信号转到界面线程处理：等待线程里不直接碰界面；
        在界面线程里、显示之后才回应。已显示着时照样显示到最前、记录、回应。回应出错只记错误记录（换成假的，不写真的
        错误文件），照常显示；没接回应时（测试、预览）照常显示。"""
        w = self.window
        acks = []
        w.relaunch_ack = lambda: acks.append((w.isHidden(), threading.current_thread() is threading.main_thread()))
        sender = threading.Thread(target=w.relaunch_requested.emit)
        sender.start()
        sender.join(5)
        self.assertTrue(w.isHidden())  # 还没到界面线程
        deadline = time.monotonic() + 5
        while not acks and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(w.isHidden())
        self.assertEqual(acks, [(False, True)])
        w.on_relaunch()
        self.assertEqual(len(acks), 2)
        self.assertEqual([e["hidden"] for e in self.traced()], [True, False])
        errors = []
        w.hide()
        w.relaunch_ack = mock.Mock(side_effect=OSError("回应出错"))
        with mock.patch("advisor.ui.overlay_parts.follow.log_error", side_effect=lambda *a, **k: errors.append(a[0])):
            w.on_relaunch()
        self.assertFalse(w.isHidden())
        self.assertEqual(len(errors), 1)
        w.relaunch_ack = None
        w.hide()
        w.on_relaunch()
        self.assertFalse(w.isHidden())

    @unittest.skipUnless(WINDOWS, SKIP_REASON)
    def test_request_shows_overlay_and_answers(self):
        """真的走一遍（名字换成测试专用的，放开前台权限那一步换成假的）：再次启动的请求经等待线程、信号到界面线程，收到
        托盘的悬浮窗显示出来、记一条 relaunch、回应，再次启动的那一侧收到回应后马上返回（退出）。"""
        app, window = self.app, self.window
        names = random_names()
        instance = si.acquire(names)
        self.addCleanup(instance.close)
        window.relaunch_ack = instance.acknowledge
        listener = instance.listen(window.relaunch_requested.emit)
        self.addCleanup(listener.stop)
        results = []
        with mock.patch.object(si._api, "allow_foreground"), mock.patch.object(si, "_ask_end", return_value=False), \
                mock.patch.object(si, "_tell"):  # 万一等满了没回应，弹窗也是假的
            # 最多等 20 秒：5 秒内返回就是收到回应退出的，不是等满了退出（机器忙时上限不能贴着 2 秒）
            asker = threading.Thread(target=lambda: results.append(si.start(names, wait=20)), daemon=True)
            start = time.monotonic()
            asker.start()
            while asker.is_alive() and time.monotonic() - start < 20:
                app.processEvents()
                time.sleep(0.01)
            asker.join(1)
        self.assertEqual(results, [None])
        self.assertLess(time.monotonic() - start, 5)
        self.assertFalse(window.isHidden())
        self.assertEqual([e["hidden"] for e in self.traced()], [True])


class OverlayMainWiringTest(unittest.TestCase):
    """悬浮窗 main()：显示完悬浮窗后的回应接 single_instance.acknowledge；悬浮窗建好以后才开始等「显示」请求，回调是
    relaunch_requested 信号（转到界面线程）；退出时和其他线程一起停下等待线程。界面、识别、热键都换成假的。"""

    def test_listener_started_after_build_and_stopped_on_exit(self):
        from advisor.ui import overlay

        main_mod = sys.modules[overlay_app.main.__module__]  # main()、build() 都在 overlay_parts.app 里
        order = []
        app = mock.Mock()
        app.exec.side_effect = lambda: order.append("exec") or 0
        window = mock.MagicMock()
        window.show.side_effect = lambda: order.append("show")
        listener = mock.Mock()
        listener.stop.side_effect = lambda *a, **k: order.append("stop") or True
        hotkey = mock.Mock()
        hotkey.stop.side_effect = lambda: order.append("hotkey.stop")

        def fake_listen(callback, on_start=None):
            order.append("listen")
            self.assertIs(callback, window.relaunch_requested.emit)
            return listener

        with mock.patch.object(main_mod, "build", return_value=(app, window, SimpleNamespace(
                    eventlog=SimpleNamespace(recorder=None), save=lambda force=False: None,
                    settings=SimpleNamespace(big_cores=False)), threading.Lock())), \
                mock.patch.object(main_mod, "Watcher"), mock.patch.object(main_mod, "Hotkey", return_value=hotkey), \
                mock.patch.object(main_mod, "QTimer"), \
                mock.patch.object(main_mod, "expected_capture", return_value="window"), \
                mock.patch.object(main_mod.Services, "load", return_value=None), \
                mock.patch.object(si, "listen", side_effect=fake_listen), \
                mock.patch.object(main_mod, "declare_high_performance"), \
                mock.patch.object(sys, "excepthook", sys.excepthook):
            with self.assertRaises(SystemExit) as raised:
                overlay_app.main()
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(order, ["show", "listen", "exec", "stop", "hotkey.stop"])
        self.assertIs(window.relaunch_ack, si.acknowledge)


class SelfCheckEntryTest(unittest.TestCase):
    def test_selfcheck_skips_single_instance(self):
        """ACC.exe --selfcheck 在调用 run() 之前就分出去，不查防多开：有悬浮窗在运行时照样能自检。"""
        spec = importlib.util.spec_from_file_location("chip_advisor_entry", ROOT / "packaging" / "chip_advisor.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        with mock.patch.object(sys, "argv", ["ACC.exe", "--selfcheck"]), \
                mock.patch("advisor.diagnostics.self_check_main", return_value=0) as check, \
                mock.patch.object(si, "claim", side_effect=AssertionError("自检不该查防多开")) as claim, \
                mock.patch("advisor.ui.run", side_effect=AssertionError("自检不该启动悬浮窗")):
            with self.assertRaises(SystemExit) as raised:
                entry.main()
        self.assertEqual(raised.exception.code, 0)
        check.assert_called_once_with()
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
