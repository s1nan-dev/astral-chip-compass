"""画皮肤缩略图的子进程放进作业对象（advisor/ui/skin_preview.py 的 _start_child、PreviewStore._spawn、_kill、stop；Windows）：
- 子进程在作业里，它再起的进程也在（开发版的 python.exe 是个启动器，会再起真正的解释器：先挂起着起，放进作业再恢复）；
- 作业的句柄关掉（主程序不管怎么没的，系统都会收回句柄）时，作业里的进程都跟着结束；stop 同样结束它们；
- 放不进作业时照常运行（照样回话）；挂起着起的恢复不了时结束它，照常另起一个。
子进程换成一个很小的假程序（不导入界面库）：说一句「开始画」以后一直不动，同画到一半卡住。只结束测试自己起的进程：
核对进程在不在作业里都按句柄（IsProcessInJob），万一测试失败、进程还在，收尾时只结束核对过在作业里的。
"""
import ctypes
import json
import os
import subprocess
import sys
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

from advisor.ui import skin_preview
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tests.support.waits import wait_until

APP = qtenv.app()
WAIT_MS = 60000  # 等子进程说第一句话最多多久（机器忙时起进程慢）

if os.name == "nt":
    K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p]
    K32.QueryInformationJobObject.restype = wintypes.BOOL
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    K32.IsProcessInJob.restype = wintypes.BOOL
    K32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    K32.WaitForSingleObject.restype = wintypes.DWORD
    K32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    K32.TerminateProcess.restype = wintypes.BOOL
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    K32.CloseHandle.restype = wintypes.BOOL


class PidList(ctypes.Structure):  # JOBOBJECT_BASIC_PROCESS_ID_LIST（最多列 64 个）
    _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * 64)]


def stuck_command(keys, mark: Path) -> list[str]:
    """假的画图子进程：把自己的进程号写进 mark（开发版里是启动器再起的真正的解释器），说一句开始画第一套，然后一直不动
    （同画到一半卡住）。"""
    line = skin_preview.CHILD_TAG + json.dumps({"start": keys[0]}).encode("ascii") + b"\n"
    script = (f"import os, sys, time\nopen({str(mark)!r}, 'w').write(str(os.getpid()))\n"
              f"sys.stdout.buffer.write({line!r})\nsys.stdout.flush()\ntime.sleep(600)\n")
    return [sys.executable, "-c", script]


def is_member(handle, job) -> bool:
    inside = wintypes.BOOL()
    return bool(K32.IsProcessInJob(handle, job, ctypes.byref(inside))) and bool(inside.value)


@unittest.skipUnless(os.name == "nt", "作业对象只在 Windows 上有")
class ChildJobTest(unittest.TestCase):
    def setUp(self):
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), [], "出错（错误记录）"))
        self.folder = sandbox.temp_dir(self, "test-skinjob-")
        self.mark = self.folder / "pid.txt"
        patcher = mock.patch.object(skin_preview, "child_command", side_effect=lambda keys: stuck_command(keys, self.mark))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handles: list[int] = []
        self.addCleanup(self._close_handles)

    def store(self) -> skin_preview.PreviewStore:
        store = skin_preview.PreviewStore(None, self.folder / "skins", isolated=True)
        self.addCleanup(store.deleteLater)
        self.addCleanup(store.stop)
        return store

    def started(self, store: skin_preview.PreviewStore) -> subprocess.Popen:
        """起子进程（画 default），等它说了第一句话。"""
        store._spawn(["default"])
        proc = store._child
        self.assertIsNotNone(proc)
        self.assertTrue(wait_until(lambda: store._child_now == "default", WAIT_MS), "子进程没有回话")
        return proc

    def drawer(self) -> int:
        """真正在画（这里是卡着）的那个进程的进程号：开发版里是启动器再起的解释器，发行版里就是子进程自己。"""
        return int(self.mark.read_text(encoding="utf-8"))

    def members(self, job) -> dict[int, int]:
        """作业里的进程：{进程号: 句柄}。按进程号打开以后核对确实在这个作业里（进程号万一被复用了就不在），不在的不要。
        句柄留到测试结束再关（收尾时还没退出的，核对过在作业里，是这个测试起的，结束掉）。"""
        ids = PidList()
        self.assertTrue(K32.QueryInformationJobObject(job, 3, ctypes.byref(ids), ctypes.sizeof(ids), None))  # 3：进程号列表
        found = {}
        for pid in ids.ProcessIdList[:ids.NumberOfProcessIdsInList]:
            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
            handle = K32.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
            if not handle:
                continue
            if is_member(handle, job):
                found[pid] = handle
                self.handles.append(handle)
            else:
                K32.CloseHandle(handle)
        return found

    def ended(self, handle, ms: int = 30000) -> bool:
        return K32.WaitForSingleObject(handle, ms) == 0  # WAIT_OBJECT_0

    def _close_handles(self) -> None:
        for handle in self.handles:
            if K32.WaitForSingleObject(handle, 0) != 0:  # 测试失败时还留着的：在作业里核对过，是这个测试起的
                K32.TerminateProcess(handle, 1)
            K32.CloseHandle(handle)

    def finish(self, store: skin_preview.PreviewStore) -> None:
        """收尾：同程序退出时（不当成画不出来），等子进程那边的收尾做完。"""
        store.stop()
        self.assertTrue(wait_until(lambda: not store.busy, WAIT_MS))

    def test_closing_job_ends_child_and_its_children(self):
        """子进程（开发版里连同启动器再起的真正的解释器）都在作业里；作业的句柄关掉时都跟着结束：主程序被强制结束时系统
        收回句柄，同这里。"""
        store = self.store()
        proc = self.started(store)
        job = store._job
        self.assertTrue(job, "子进程没放进作业")
        members = self.members(job)
        self.assertIn(proc.pid, members)
        self.assertIn(self.drawer(), members)  # 启动器再起的解释器也在：先挂起着起、放进作业再恢复
        store._drop_job()  # 同主程序被强制结束：系统收回作业的句柄（不经 stop、不直接结束子进程）
        for pid, handle in members.items():
            self.assertTrue(self.ended(handle), f"作业的句柄关掉以后进程 {pid} 还在")
        self.assertIsNotNone(proc.wait(30))
        self.finish(store)

    def test_stop_ends_the_whole_tree(self):
        """程序退出时（stop）：子进程和它再起的进程都结束。"""
        store = self.store()
        proc = self.started(store)
        members = self.members(store._job)
        self.assertIn(proc.pid, members)
        self.assertIn(self.drawer(), members)
        self.finish(store)
        for pid, handle in members.items():
            self.assertTrue(self.ended(handle), f"stop 以后进程 {pid} 还在")
        self.assertIsNone(store._job)

    def test_runs_normally_when_not_in_job(self):
        """建不了作业、放不进作业（系统、权限不允许等）：照常运行（挂起着起的照常恢复），照样回话，stop 照样结束它。"""
        for name, value in (("_new_job", None), ("_join_job", False)):
            with self.subTest(failing=name):
                store = self.store()
                with mock.patch.object(skin_preview, name, return_value=value):
                    proc = self.started(store)
                self.assertIsNone(store._job)
                self.finish(store)
                self.assertIsNotNone(proc.wait(30))

    def test_resume_failure_starts_again(self):
        """挂起着起的恢复不了（照理不会）：结束它，照常另起一个（不挂起、不放进作业），照样回话。"""
        real = subprocess.Popen
        started = []

        def popen(*args, **kwargs):
            started.append(real(*args, **kwargs))
            return started[-1]

        store = self.store()
        with mock.patch.object(skin_preview, "_resume", return_value=False), \
                mock.patch.object(subprocess, "Popen", side_effect=popen) as spy:
            proc = self.started(store)
        flags = [call.kwargs.get("creationflags", 0) for call in spy.call_args_list]
        self.assertEqual(len(started), 2)
        self.assertTrue(flags[0] & skin_preview.CREATE_SUSPENDED)
        self.assertFalse(flags[1] & skin_preview.CREATE_SUSPENDED)
        self.assertIs(proc, started[1])
        self.assertIsNotNone(started[0].wait(30))  # 挂起着的那个结束了
        self.assertIsNone(store._job)
        self.finish(store)


if __name__ == "__main__":
    unittest.main()
