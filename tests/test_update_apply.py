"""换版本（advisor/update_apply.py）：下好的新版本把旧版本换掉，起不来就退回。

在沙箱的临时文件夹里搭发行版文件夹的样子（Layout：根目录的启动程序和使用说明，app 里的旧版本和每一种用户数据，update\\new
里的新版本，ready.json），程序文件的内容写成「old program」「new program」，按内容认是哪个版本。起进程、等事件、等旧进程
这几个小函数换成替身（Fake），等待的时长调小；临时文件夹里另放一个 outside 文件夹，每个用例结束时核对它没被碰。另有几个
用真进程的例子（RealProcessTest：当新版本、旧版本、换版本程序的是跑一小段脚本的 Python），核对命名事件、等进程那一套真的通。

  换版本程序  换好了（事件触发、等满时间还在运行、以退出码 0 请已在运行的那个显示）；位置不对、参数不对、ready.json 不对、
              旧版本的程序不在、另一个换版本程序在换：什么都不改；旧进程等不到：not_applied，别的不改、不起程序；复制出错、
              两次改名改不成：退回原样、not_applied、起旧版本；新版本悬浮窗出来前就退出、起不来、换过来以后出错：退回（数据
              搬回、app.failed、根目录文件换回、rolled_back、起旧版本）；退不回时留着新版本，最坏时什么都不删；最后起的那一次经
              根目录的启动程序（它不在、起不来时直接起）；update\\new\\app 里的用户数据不复制；之前留下的 app.next、app.old
              （有没有用户数据）、根目录的旧文件；次数怎么记
  pending     几种情况（没换上的结果还没读走时不交）；查的时候什么都不删
  launch      等换版本程序回话：接手了才为 True；核对没通过、出错退出、一直不回话、起不来记一次没换上，另一个在换时不记；
              没换上以后读走结果才再起，到 2 次不再起
  take_result 只交一次；换版本程序还在换时那一份「没做完」不交；换的就是正在运行的版本的「没做完」不交；标记不上不交
  after_start 收尾：换版本程序还没退出时不删；结果还没读过时不删；app.old 里还有用户数据时不删；app.failed 只留最近的一份；
              有别的下载时不删 update；没换上、还能再换时留着 update；记录接到 app\\logs\\update.log（有上限）；根目录的正式
              文件不在时把 .old 改回去；删不掉的留着结果下次再收；链接只删链接本身；别的名字一概不碰；两次改名以后中途退出的
              补完（搬用户数据、不覆盖，结果改成 ok）
  开发版      对外的几个函数什么都不做
  删除范围    删、另起名只认根下那几个名字，别的报错、什么都不删
  名字        和防多开、启动程序、打包脚本约好的名字相同；导入它、跑入口的换版本参数都不带进界面库和识别库
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import subprocess
import sys
import textwrap
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from advisor import paths, version
from advisor import update_apply as ua
from advisor.errors import EXIT_REPORTED
from tests.support import sandbox

PROJECT = Path(__file__).resolve().parents[1]
ENTRY_FILE = PROJECT / "packaging" / "chip_advisor.py"
WINDOWS = os.name == "nt"
OLD = version.VERSION  # 旧版本：当前的版本号
_MAJOR, _MINOR, _PATCH = (int(x) for x in OLD.split("."))
NEW = f"{_MAJOR}.{_MINOR}.{_PATCH + 1}"  # 要换上的新版本
NEWER = f"{_MAJOR}.{_MINOR}.{_PATCH + 2}"  # 再新的一版（别的下载）
LAUNCHER, MANUAL = ua.ROOT_FILES
OLD_PID = 4321  # 替身里旧进程的进程号
REAL_FILE_VERSION = ua._file_version  # 各用例换成替身以前的那一个

# 旧版本的程序目录里的用户数据（相对 app）：paths.USER_DATA_NAMES 每一项都有（LayoutTest 核对）
USER_FILES = {
    "logs/recommend.log": b"recommend log",
    "reports/report.zip": b"report",
    ".tmp/overlay_err.txt": b"error log",
    "assets/heroes/hero.png": b"hero",
    "assets/profiles/profile.png": b"profile",
    "assets/chips/chip.png": b"chip",
    "assets/skins/skin.png": b"skin",
    "assets/manifest-local.json": b"{}",
    "assets/download-cooldown.json": b"[]",
    "state.json": b"old settings",
    "state.fallback.json": b"fallback settings",
    "schemes.json.bak": b"schemes backup",
    "schemes.fallback.json": b"fallback schemes",
}
# 两个版本的程序文件（相对 app）：随发行版带的 assets 和用户数据的 assets 在同一个文件夹里
OLD_PROGRAM = {"ACC.exe": b"old program", "runtime/core.dll": b"old runtime", "assets/stars/star_bar.png": b"old star"}
NEW_PROGRAM = {"ACC.exe": b"new program", "runtime/core.dll": b"new runtime", "assets/stars/star_bar.png": b"new star",
               "data/extra.json": b"new data"}
OLD_ROOT = {LAUNCHER: b"old launcher", MANUAL: b"old manual"}
NEW_ROOT = {LAUNCHER: b"new launcher", MANUAL: b"new manual"}

# 真进程的例子里当 app\ACC.exe 的脚本（RealProcessTest）：按工作目录里 ACC.exe 的内容认自己是哪个版本，记一行（工作目录、
# 环境变量里的事件名、程序内容）到 argv[1]；新版本在 argv[2] 为 ready 时打开、触发 ACC_READY_EVENT 里的事件，为 exit 时
# 以 5 退出（悬浮窗出来之前就结束）。argv[3] 为 launcher 时当根目录的启动程序：先记下自己的工作目录，再像它起 ACC.exe 那样
# 换到 app 里
APP_SCRIPT = r'''
import ctypes, json, os, sys
from pathlib import Path

launcher_cwd = None
if sys.argv[3:] == ["launcher"]:
    launcher_cwd = os.getcwd()
    os.chdir("app")
program = Path("ACC.exe").read_bytes().decode()
name = os.environ.get("ACC_READY_EVENT", "")
info = {"cwd": os.getcwd(), "program": program, "event": name, "started": "ACC_UPDATE_STARTED" in os.environ,
        "launcher_cwd": launcher_cwd}
if program == "new program" and sys.argv[2] == "ready":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenEventW(0x0002, False, name)
    info["signaled"] = bool(handle) and bool(kernel.SetEvent(handle))
    kernel.CloseHandle(handle)
with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(json.dumps(info) + "\n")
if program == "new program" and sys.argv[2] == "exit":
    sys.exit(5)
'''
# 当换版本程序的脚本：argv[1] 为 ok 时打开、触发 ACC_UPDATE_STARTED 里的事件（接手了），是数字时以它退出
UPDATE_SCRIPT = r'''
import ctypes, os, sys

mode = sys.argv[1]
if mode == "ok":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenEventW(0x0002, False, os.environ["ACC_UPDATE_STARTED"])
    kernel.SetEvent(handle)
    kernel.CloseHandle(handle)
    sys.exit(0)
sys.exit(int(mode))
'''


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_files(base: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def files_under(base: Path) -> dict[str, bytes]:
    """base 下全部文件（相对路径，/ 分隔）和内容；base 不在时为空。"""
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def root_files(root: Path) -> dict[str, bytes]:
    """根目录这一层的文件和内容。"""
    return {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def snapshot(base: Path) -> dict[str, bytes | str]:
    """base 下的全部文件（内容）、文件夹（"dir"）和链接（"link"，不跟进去）：核对「什么都没改」用。"""
    found: dict[str, bytes | str] = {}

    def walk(folder: Path) -> None:
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                path = Path(entry.path)
                rel = path.relative_to(base).as_posix()
                if entry.is_symlink() or entry.is_junction():
                    found[rel] = "link"
                elif entry.is_dir():
                    found[rel] = "dir"
                    walk(path)
                else:
                    found[rel] = path.read_bytes()

    if base.is_dir():
        walk(base)
    return found


def file_version(path) -> str | None:
    """_file_version 的替身：搭的发行版里程序文件不是真的程序，按内容认版本。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return {b"old program": OLD, b"new program": NEW}.get(data)


def patch(test: unittest.TestCase, target, name: str, value) -> None:
    """在这个用例里把 target.name 换成 value。"""
    patcher = mock.patch.object(target, name, value)
    patcher.start()
    test.addCleanup(patcher.stop)


def frozen(test: unittest.TestCase) -> None:
    """这个用例里当成在发行版里运行。"""
    patch(test, paths, "is_frozen", lambda: True)


class Layout:
    """沙箱里的一个发行版文件夹（根）和它旁边的 outside 文件夹（用例结束时核对没被碰）。"""

    def __init__(self, test: unittest.TestCase):
        self.base = sandbox.temp_dir(test)
        self.root = self.base / paths.FOLDER_NAME
        self.outside = self.base / "outside"
        self.app = self.root / ua.APP
        self.update = self.root / ua.UPDATE_DIR
        self.new = self.update / ua.NEW_DIR
        self.new_app = self.new / ua.APP
        self.old_slot = self.root / ua.APP_OLD
        self.failed = self.root / ua.APP_FAILED
        self.next = self.root / ua.APP_NEXT
        write_files(self.outside, {"keep.txt": b"outside", f"{ua.APP_OLD}/logs/x.log": b"not ours",
                                   f"{LAUNCHER}.old": b"not ours"})
        write_files(self.root, OLD_ROOT)
        write_files(self.app, {**OLD_PROGRAM, **USER_FILES})
        write_files(self.new, NEW_ROOT)
        write_files(self.new_app, NEW_PROGRAM)
        self.write_ready()
        self.mark_outside()
        test.addCleanup(self.check_outside, test)

    def mark_outside(self) -> None:
        self.outside_before = snapshot(self.outside)

    def check_outside(self, test: unittest.TestCase) -> None:
        test.assertEqual(snapshot(self.outside), self.outside_before, "临时文件夹里根外面的东西被动了")

    def write_ready(self, ver: str = NEW, **changes) -> None:
        record = {"format": 1, "version": ver, "sha256": "0" * 64, "prepared": now(), **changes}
        (self.update / ua.READY_NAME).write_text(json.dumps(record), encoding="utf-8")

    def write_result(self, **changes) -> None:
        record = {"format": 1, "status": ua.OK, "version": NEW, "from": OLD, "reason": "", "time": now(), "pid": 0,
                  "attempts": 0, "done": True, **changes}
        self.update.mkdir(parents=True, exist_ok=True)
        (self.update / ua.RESULT_NAME).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def result(self) -> dict | None:
        path = self.update / ua.RESULT_NAME
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def log(self) -> str:
        path = self.update / ua.LOG_NAME
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def apply(self, argv: list[str] | None = None) -> int:
        return ua.apply_main(argv if argv is not None else [ua.APPLY_ARG, str(OLD_PID)], app=self.new_app,
                             own_version=NEW)


class Fake:
    """换掉 update_apply 里碰进程、事件的小函数，记下调用：
    - 旧进程：old 为 "exits"（等得到它退出）、"stays"（等不到）、"gone"（打开时已经不在）；给了 opens 时 _open_process 先按它
      的先后返回；
    - 起的程序：每次 _start 记下命令、工作目录、环境变量、quiet，和起的那个程序文件的内容（按它认起的是哪个版本；经根目录的
      启动程序起的，记它会起的 app\\ACC.exe 的内容，另在 launcher 里记启动程序的内容，直接起的 launcher 为 None）；第一次起
      （换版本程序起新版本、launch 起换版本程序）时给了 start_error 就报它，给了 on_start 就以子进程的进程号调用它（模拟它运行时
      做的事）；给了 launcher_error 时起启动程序都报它；
    - ready 是 _wait_ready 的结果（READY、RUNNING、EXITED），退出码是 code；instance：防多开的锁在不在；event：建不建得了事件。
    句柄都是假的数，_close 也换掉了（假句柄不能交给真的 CloseHandle）。"""

    def __init__(self, test: unittest.TestCase, *, old: str = "exits", opens=None, ready: str = ua.READY,
                 code: int | None = None, start_error: OSError | None = None, on_start=None, instance: bool = False,
                 event: bool = True, launcher_error: OSError | None = None):
        self.old, self.opens = old, list(opens or [])
        self.ready, self.code, self.start_error, self.on_start = ready, code, start_error, on_start
        self.launcher_error = launcher_error
        self.instance, self.event = instance, event
        self.starts: list[dict] = []
        self.events: list[str] = []
        self.signals = 0
        self.terminated: list[int] = []
        self._handle = 1000
        for name, fn in (("_open_process", self.open_process), ("_wait_process", self.wait_process),
                         ("_signal_started", self.signal_started), ("_new_event", self.new_event),
                         ("_start", self.start), ("_wait_ready", self.wait_ready), ("_exit_code", self.exit_code),
                         ("_terminate", self.terminate), ("_close", self.close),
                         ("_instance_running", lambda: self.instance), ("_file_version", file_version)):
            patch(test, ua, name, fn)

    def handle(self) -> int:
        self._handle += 1
        return self._handle

    def open_process(self, pid: int) -> tuple[int, bool]:
        if self.opens:
            return self.opens.pop(0)
        return (0, True) if self.old == "gone" else (self.handle(), False)

    def wait_process(self, handle: int, seconds: float) -> bool:
        return self.old == "exits"

    def signal_started(self) -> None:
        self.signals += 1

    def new_event(self, kind: str) -> tuple[int, str]:
        if not self.event:
            return 0, ""
        name = f"Local\\{paths.FOLDER_NAME}.{kind}.test.{len(self.events)}"
        self.events.append(name)
        return self.handle(), name

    def start(self, command, cwd, env, *, quiet):
        program = Path(command[0])
        launcher = None
        if program.name == LAUNCHER:  # 根目录的启动程序：它起的是同一文件夹里的 app\ACC.exe
            if self.launcher_error is not None:
                raise self.launcher_error
            launcher = program.read_bytes()
            program = program.parent / ua.APP / ua.EXE
        self.starts.append({"command": list(command), "cwd": Path(cwd), "env": dict(env), "quiet": quiet,
                            "program": program.read_bytes() if program.is_file() else None, "launcher": launcher})
        pid = 5000 + len(self.starts)
        if len(self.starts) == 1:
            if self.start_error is not None:
                raise self.start_error
            if self.on_start is not None:
                self.on_start(pid)
        return ua._Child(self.handle(), pid)

    def wait_ready(self, event: int, child, seconds: float) -> str:
        return self.ready

    def exit_code(self, child) -> int | None:
        return self.code

    def terminate(self, child) -> None:
        self.terminated.append(child.pid)

    def close(self, handle: int) -> None:
        pass

    def started(self) -> list[bytes | None]:
        """起过的程序（程序文件的内容），按先后。"""
        return [s["program"] for s in self.starts]

    def launchers(self) -> list[bytes | None]:
        """每次起程序经的启动程序（它的内容；直接起的为 None），按先后。"""
        return [s["launcher"] for s in self.starts]


def busy() -> PermissionError:
    return PermissionError(13, "另一个程序正在使用此文件，进程无法访问。", None, 32)


def failing_rename(test: unittest.TestCase, rules: dict, error=busy) -> list[tuple[str, str, bool]]:
    """把 update_apply._rename 换成：（原名, 新名）在 rules 里的改名报 error()（默认是被占着），值是报几次（None 为一直报）；
    新名以 * 结尾的按开头比。别的照常改名。返回记下的每一次改名（原名, 新名, 成没成）。"""
    real = ua._rename
    calls: list[tuple[str, str, bool]] = []

    def match(src: str, dst: str):
        for key in rules:
            if key[0] == src and (key[1] == dst or (key[1].endswith("*") and dst.startswith(key[1][:-1]))):
                return key
        return None

    def fake(src, dst):
        names = (Path(src).name, Path(dst).name)
        key = match(*names)
        if key is not None and (rules[key] is None or rules[key] > 0):
            if rules[key] is not None:
                rules[key] -= 1
            calls.append((*names, False))
            raise error()
        real(src, dst)
        calls.append((*names, True))

    patch(test, ua, "_rename", fake)
    return calls


class Base(unittest.TestCase):
    def setUp(self):
        # 等待的时长调小：替身不真的等，只有被占着时的再试真的隔一会儿
        for name, value in (("OLD_WAIT_S", 0.5), ("READY_WAIT_S", 0.5), ("RENAME_WAIT_S", 0.3),
                            ("ROLLBACK_WAIT_S", 0.3), ("LAUNCH_WAIT_S", 0.5), ("AFTER_WAIT_S", 0.3), ("RETRY_S", 0.01)):
            patch(self, ua, name, value)

    def assert_swapped(self, layout: Layout) -> None:
        """换好了的样子（换版本程序刚退出、还没收尾）：新的 app 里是新版本的程序和全部用户数据，app.old 里只剩旧版本的
        程序，根目录两个文件换成新的、旧的改名成 .old，update\\new 原样留着。"""
        self.assertEqual(files_under(layout.app), {**NEW_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.old_slot), OLD_PROGRAM)
        self.assertFalse(os.path.lexists(layout.next))
        self.assertFalse(os.path.lexists(layout.failed))
        self.assertEqual(root_files(layout.root), {**NEW_ROOT, f"{LAUNCHER}.old": b"old launcher",
                                                   f"{MANUAL}.old": b"old manual"})
        self.assertEqual(files_under(layout.new), {**NEW_ROOT, **{f"app/{k}": v for k, v in NEW_PROGRAM.items()}})

    def assert_untouched(self, layout: Layout) -> None:
        """没换的样子：app 里还是旧版本和全部用户数据，没有 app.next、app.old、app.failed，根目录两个文件是旧的。"""
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()), sorted([LAUNCHER, MANUAL, ua.APP, ua.UPDATE_DIR]))
        self.assertEqual(root_files(layout.root), OLD_ROOT)

    def assert_result(self, layout: Layout, status: str, reason: str, attempts: int) -> dict:
        result = layout.result()
        self.assertIsNotNone(result)
        self.assertEqual({k: result[k] for k in ("format", "status", "version", "reason", "attempts", "done")},
                         {"format": 1, "status": status, "version": NEW, "reason": reason, "attempts": attempts,
                          "done": True})
        return result


class LayoutTest(unittest.TestCase):
    def test_user_files_cover_every_name(self):
        """搭的用户数据里 paths.USER_DATA_NAMES 每一项都有：清单加了新的一项时这里提醒一起加上（换版本照清单搬）。"""
        for name in paths.USER_DATA_NAMES:
            with self.subTest(name=name):
                if any(c in name for c in "*?["):
                    hit = any("/" not in rel and fnmatch.fnmatch(rel, name) for rel in USER_FILES)
                else:
                    hit = any(rel == name or rel.startswith(name + "/") for rel in USER_FILES)
                self.assertTrue(hit)


class ApplyTest(Base):
    """换版本程序（apply_main）。"""

    def test_success(self):
        """悬浮窗出来了（事件触发）：换好了。结果 ok（次数 0、旧版本的版本号、写它的进程号）；只起了一次新版本：不带参数、
        工作目录 app、不弹严重错误窗口，环境变量里是等悬浮窗的事件、不带 launch 的那个；每一行记录带时间。"""
        layout = Layout(self)
        fake = Fake(self)
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        result = self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual((result["from"], result["pid"]), (OLD, os.getpid()))
        datetime.fromisoformat(result["time"])
        self.assertEqual(fake.signals, 1)
        self.assertEqual(fake.started(), [b"new program"])
        start = fake.starts[0]
        self.assertEqual(start["command"], [str(layout.app / ua.EXE)])
        self.assertEqual(start["cwd"], layout.app)
        self.assertTrue(start["quiet"])
        self.assertEqual(start["env"][ua.READY_ENV], fake.events[0])
        self.assertTrue(fake.events[0].startswith(f"Local\\{paths.FOLDER_NAME}.UpdateReady."))
        self.assertNotIn(ua.STARTED_ENV, start["env"])
        log = layout.log()
        self.assertIn("换好了", log)
        for line in log.splitlines():
            if not line.startswith("    "):
                self.assertRegex(line, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 换版本程序：")

    def test_still_running_counts(self):
        """等满时间新版本还在运行（没触发事件）：也算换好了。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.RUNNING)
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(fake.started(), [b"new program"])

    def test_exit_zero_with_other_instance(self):
        """新版本以退出码 0 结束、防多开的锁在别的拾星助手手里：它是请那一个显示悬浮窗以后照常退出的，算换好了。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=0, instance=True)
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(fake.started(), [b"new program"])

    def test_wrong_place_changes_nothing(self):
        """换版本程序不在 <根>\\update\\new\\app 里：什么都不改（记录也不写），以 EXIT_REFUSED 退出，不起程序。"""
        layout = Layout(self)
        fake = Fake(self)
        before = snapshot(layout.root)
        for app in (layout.app, layout.root, layout.new, layout.update / "new.part" / ua.APP, layout.new / "app2"):
            with self.subTest(app=app.relative_to(layout.base).as_posix()):
                self.assertEqual(ua.apply_main([ua.APPLY_ARG, str(OLD_PID)], app=app, own_version=NEW),
                                 ua.EXIT_REFUSED)
                self.assertEqual(snapshot(layout.root), before)
        self.assertEqual((fake.starts, fake.signals), ([], 0))

    def test_bad_arguments_change_nothing(self):
        """命令行里没有旧进程号、不是正整数：什么都不改，以 EXIT_REFUSED 退出。"""
        layout = Layout(self)
        fake = Fake(self)
        before = snapshot(layout.root)
        for argv in ([], [ua.APPLY_ARG], [ua.APPLY_ARG, "abc"], [ua.APPLY_ARG, "0"], [ua.APPLY_ARG, "-5"],
                     [ua.APPLY_ARG, "99999999999"], [f"{ua.APPLY_ARG}=x"]):
            with self.subTest(argv=argv):
                self.assertEqual(layout.apply(argv), ua.EXIT_REFUSED)
                self.assertEqual(snapshot(layout.root), before)
        self.assertEqual(fake.starts, [])
        self.assertEqual(ua._old_pid(["x", ua.APPLY_ARG, "7"]), 7)
        self.assertEqual(ua._old_pid([f"{ua.APPLY_ARG}=12"]), 12)

    def test_bad_ready_changes_nothing(self):
        """ready.json 不在、读不出、格式不对、版本不是自己的，旧版本的程序不在：什么都不改（只记一行），以 EXIT_REFUSED
        退出，不起程序。"""
        cases = {
            "没有 ready.json": lambda l: (l.update / ua.READY_NAME).unlink(),
            "读不出": lambda l: (l.update / ua.READY_NAME).write_text("{", encoding="utf-8"),
            "格式不对": lambda l: l.write_ready(NEW, format=2),
            "版本不是自己的": lambda l: l.write_ready(NEWER),
            "旧版本的程序不在": lambda l: (l.app / ua.EXE).unlink(),
        }
        for label, spoil in cases.items():
            with self.subTest(label):
                layout = Layout(self)
                fake = Fake(self)
                spoil(layout)
                before = snapshot(layout.root)
                self.assertEqual(layout.apply(), ua.EXIT_REFUSED)
                after = snapshot(layout.root)
                self.assertIn("核对没有通过，什么都不改", after.pop(f"{ua.UPDATE_DIR}/{ua.LOG_NAME}").decode("utf-8"))
                self.assertEqual(after, before)
                self.assertEqual((fake.starts, fake.signals), ([], 0))

    def test_another_running_changes_nothing(self):
        """另一个换版本程序正在换这个文件夹（锁在别人手里）：什么都不改，以 EXIT_BUSY 退出。"""
        layout = Layout(self)
        fake = Fake(self)
        lock = ua._take_update_lock(layout.root)
        self.addCleanup(ua._release_lock, lock)
        self.assertTrue(lock)
        before = snapshot(layout.root)
        self.assertEqual(layout.apply(), ua.EXIT_BUSY)
        after = snapshot(layout.root)
        after.pop(f"{ua.UPDATE_DIR}/{ua.LOG_NAME}")
        self.assertEqual(after, before)
        self.assertEqual((fake.starts, fake.signals), ([], 0))

    def test_old_process_stays(self):
        """旧进程一直不退出：结果 not_applied（原因写等了多久），别的什么都不改，不另起程序。"""
        layout = Layout(self)
        fake = Fake(self, old="stays")
        before = snapshot(layout.root)
        self.assertEqual(layout.apply(), 0)
        after = snapshot(layout.root)
        for name in (ua.LOG_NAME, ua.RESULT_NAME):
            after.pop(f"{ua.UPDATE_DIR}/{name}")
        self.assertEqual(after, before)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_OLD_RUNNING.format(seconds="0.5"), 1)
        self.assertEqual(fake.signals, 1)
        self.assertEqual(fake.starts, [])

    def test_old_process_gone_or_late(self):
        """旧进程打开时已经不在了：照常换。一时打不开（拒绝访问）：隔一会儿再试着打开，打开了或者不在了照常换，到时间还
        打不开算等不到。"""
        for label, kwargs, status in (("已经不在", {"old": "gone"}, ua.OK),
                                      ("打开以后等到了", {"opens": [(0, False), (0, False)]}, ua.OK),
                                      ("后来不在了", {"opens": [(0, False), (0, True)]}, ua.OK),
                                      ("一直打不开", {"opens": [(0, False)] * 1000}, ua.NOT_APPLIED)):
            with self.subTest(label):
                layout = Layout(self)
                Fake(self, **kwargs)
                self.assertEqual(layout.apply(), 0)
                self.assertEqual(layout.result()["status"], status)
                if status == ua.OK:
                    self.assert_swapped(layout)
                else:
                    self.assert_untouched(layout)

    def test_unfinished_record_first(self):
        """一开始（等旧进程之前）先写一份「没做完」的结果：not_applied、done 为 false、次数加一。中途被结束时它就是结果。"""
        layout = Layout(self)
        Fake(self)
        seen: dict = {}

        def wait(handle, seconds):
            seen.update(layout.result())
            return True

        patch(self, ua, "_wait_process", wait)
        self.assertEqual(layout.apply(), 0)
        self.assertEqual({k: seen[k] for k in ("status", "reason", "done", "attempts", "version", "from")},
                         {"status": ua.NOT_APPLIED, "reason": ua.REASON_INTERRUPTED, "done": False, "attempts": 1,
                          "version": NEW, "from": OLD})
        self.assert_result(layout, ua.OK, "", 0)

    def test_first_rename_fails(self):
        """app 改不了名（里面有文件一直被占着）：退回原样（删掉复制出来的 app.next），not_applied，起旧版本：经根目录的启动程序
        （不带参数、工作目录是根，它照用户双击时那样等悬浮窗、出错弹窗），环境变量里没有等悬浮窗的事件。"""
        layout = Layout(self)
        fake = Fake(self)
        failing_rename(self, {(ua.APP, ua.APP_OLD): None})
        self.assertEqual(layout.apply(), 0)
        self.assert_untouched(layout)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_LOCKED, 1)
        self.assertEqual(fake.started(), [b"old program"])
        self.assertEqual(fake.launchers(), [b"old launcher"])
        start = fake.starts[0]
        self.assertEqual((start["command"], start["cwd"], start["quiet"]),
                         ([str(layout.root / LAUNCHER)], layout.root, False))
        self.assertNotIn(ua.READY_ENV, start["env"])
        self.assertIn("经启动程序起了旧版本", layout.log())

    def test_start_without_launcher(self):
        """最后起程序时根目录的启动程序不在、起不来：直接起 app\\ACC.exe（不带参数、工作目录 app），记一笔。"""
        for label, spoil, kwargs in (("不在", lambda l: (l.root / LAUNCHER).unlink(), {}),
                                     ("起不来", lambda l: None,
                                      {"launcher_error": OSError(13, "拒绝访问。", None, 5)})):
            with self.subTest(label):
                layout = Layout(self)
                fake = Fake(self, **kwargs)
                spoil(layout)
                failing_rename(self, {(ua.APP, ua.APP_OLD): None})
                self.assertEqual(layout.apply(), 0)
                self.assertEqual((fake.started(), fake.launchers()), ([b"old program"], [None]))
                start = fake.starts[-1]
                self.assertEqual((start["command"], start["cwd"], start["quiet"]),
                                 ([str(layout.app / ua.EXE)], layout.app, False))
                self.assertIn("起了旧版本（进程号", layout.log())
                if kwargs:
                    self.assertIn("经启动程序起旧版本失败（PermissionError [WinError 5]）", layout.log())

    def test_second_rename_fails(self):
        """app.next 改不成 app：每次都马上把 app.old 改回 app，隔一会儿两次一起再试；一直不行就退回原样、not_applied、
        起旧版本。"""
        layout = Layout(self)
        fake = Fake(self)
        calls = failing_rename(self, {(ua.APP_NEXT, ua.APP): None})
        self.assertEqual(layout.apply(), 0)
        self.assert_untouched(layout)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_LOCKED, 1)
        self.assertEqual(fake.started(), [b"old program"])
        failures = [i for i, call in enumerate(calls) if call == (ua.APP_NEXT, ua.APP, False)]
        self.assertGreater(len(failures), 1)  # 再试过
        for i in failures:
            self.assertEqual(calls[i - 1], (ua.APP, ua.APP_OLD, True))
            self.assertEqual(calls[i + 1], (ua.APP_OLD, ua.APP, True))  # 马上改回来

    def test_rename_other_error(self):
        """改名报的不是被占着：不再试，退回原样，原因里带错误代码。"""
        layout = Layout(self)
        fake = Fake(self)
        calls = failing_rename(self, {(ua.APP, ua.APP_OLD): None}, error=lambda: OSError(22, "参数错误。", None, 87))
        self.assertEqual(layout.apply(), 0)
        self.assert_untouched(layout)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_RENAME.format(error="OSError [WinError 87]"), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(fake.started(), [b"old program"])

    def test_renames_busy_for_a_while(self):
        """改名一时被占着（例如杀毒软件在扫刚复制的文件）：隔一会儿再试，试成了照常换。"""
        layout = Layout(self)
        Fake(self)
        failing_rename(self, {(ua.APP, ua.APP_OLD): 2, (ua.APP_NEXT, ua.APP): 2})
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)

    def test_copy_fails(self):
        """复制新版本出错（磁盘满等）：删掉复制了一半的 app.next，什么都不换，not_applied，起旧版本。"""
        layout = Layout(self)
        fake = Fake(self)

        def broken(src, dst):
            write_files(dst, {"ACC.exe": b"half"})
            raise OSError(28, "磁盘空间不足。", None, 112)

        patch(self, ua, "_copy_tree", broken)
        self.assertEqual(layout.apply(), 0)
        self.assert_untouched(layout)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_COPY.format(error="OSError [WinError 112]"), 1)
        self.assertEqual(fake.started(), [b"old program"])

    def test_new_version_exits_rolls_back(self):
        """新版本在悬浮窗出来之前就退出了：退回。用户数据（连同新版本运行时写下的）搬回 app，app.failed 里只有新版本的程序，
        根目录两个文件换回旧的，结果 rolled_back（原因带退出码），先起新版本、再起旧版本。"""
        layout = Layout(self)

        def run_new(pid):
            write_files(layout.app, {"logs/new-run.log": b"new run", ".tmp/crash.txt": b"crash"})

        fake = Fake(self, ready=ua.EXITED, code=1, on_start=run_new)
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES, "logs/new-run.log": b"new run",
                                                   ".tmp/crash.txt": b"crash"})
        self.assertEqual(files_under(layout.failed), NEW_PROGRAM)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()),
                         sorted([LAUNCHER, MANUAL, ua.APP, ua.APP_FAILED, ua.UPDATE_DIR]))
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_EXITED.format(detail="退出码 1"), 0)
        self.assertEqual(fake.started(), [b"new program", b"old program"])
        self.assertEqual(fake.launchers(), [None, b"old launcher"])  # 等悬浮窗的那一次直接起；最后经换回来的旧启动程序起
        old = fake.starts[1]
        self.assertEqual((old["command"], old["cwd"], old["quiet"]),
                         ([str(layout.root / LAUNCHER)], layout.root, False))
        self.assertNotIn(ua.READY_ENV, old["env"])

    def test_exit_details(self):
        """退回的原因按怎么结束的写：自己弹过窗的、Windows 的错误代码、退出码 0（没有别的拾星助手在运行）、取不到退出码。"""
        for code, detail in ((EXIT_REPORTED, "它弹窗说明了原因"), (0xC0000135, "错误代码 0xC0000135"), (0, "退出码 0"),
                             (None, "退出码不明")):
            with self.subTest(code=code):
                layout = Layout(self)
                fake = Fake(self, ready=ua.EXITED, code=code)
                self.assertEqual(layout.apply(), 0)
                self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_EXITED.format(detail=detail), 0)
                self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
                self.assertEqual(fake.started(), [b"new program", b"old program"])

    def test_new_version_cannot_start(self):
        """新版本起不来（CreateProcess 报错）：退回，原因里带错误代码，再起旧版本。"""
        layout = Layout(self)
        fake = Fake(self, start_error=OSError(8, "不是有效的 Win32 应用程序。", None, 193))
        self.assertEqual(layout.apply(), 0)
        self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_START.format(detail="OSError [WinError 193]"), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.failed), NEW_PROGRAM)
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assertEqual(fake.started(), [b"new program", b"old program"])

    def test_error_after_swap_rolls_back(self):
        """换过来以后出了没想到的错：退回，原因里带错误的类型，起旧版本（新版本没起）。"""
        layout = Layout(self)
        fake = Fake(self)
        patch(self, ua._Apply, "swap_root_files", mock.Mock(side_effect=RuntimeError("模拟出错")))
        self.assertEqual(layout.apply(), 0)
        self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_ERROR.format(error="RuntimeError"), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.failed), NEW_PROGRAM)
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assertEqual(fake.started(), [b"old program"])
        self.assertIn("RuntimeError: 模拟出错", layout.log())  # 记录里有调用栈

    def test_rollback_cannot_move_new(self):
        """退回时新版本的文件夹改不了名（一直被占着）：退不回，留着新版本（用户数据都在里面），结果照旧是 ok，再起一次新版本。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=1)
        failing_rename(self, {(ua.APP, ua.APP_FAILED): None})
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(fake.started(), [b"new program", b"new program"])
        self.assertEqual(fake.launchers(), [None, b"new launcher"])  # 再起一次经启动程序：起不来时它弹窗说明
        self.assertIn("退不回", layout.log())

    def test_rollback_cannot_restore_old(self):
        """退回时 app.old 改不回 app：把新版本改回 app、起新版本，用户数据都在。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=1)
        failing_rename(self, {(ua.APP_OLD, ua.APP): None})
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(fake.started(), [b"new program", b"new program"])

    def test_rollback_lost_deletes_nothing(self):
        """最坏的情形：退回时 app.old、app.failed 都改不回 app。app 不在了，但什么都不删：旧版本在 app.old、新版本和全部
        用户数据在 app.failed；结果写明两个版本在哪，不起程序。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=1)
        failing_rename(self, {(ua.APP_OLD, ua.APP): None, (ua.APP_FAILED, ua.APP): None})
        self.assertEqual(layout.apply(), 0)
        self.assertFalse(os.path.lexists(layout.app))
        self.assertEqual(files_under(layout.old_slot), OLD_PROGRAM)
        self.assertEqual(files_under(layout.failed), {**NEW_PROGRAM, **USER_FILES})
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_LOST, 1)
        self.assertEqual(fake.started(), [b"new program"])

    def last_resort_after(self, rules: dict, lifted: list) -> None:
        """改名按 rules 失败；走到最后一道（_Apply.last_resort）时去掉 lifted 里的几条（又改得了名了）。"""
        failing_rename(self, rules)
        real = ua._Apply.last_resort

        def last_resort(job, error):
            for key in lifted:
                rules.pop(key, None)
            real(job, error)

        patch(self, ua._Apply, "last_resort", last_resort)

    def test_last_resort_restores_old(self):
        """退回时两个文件夹一时都改不回 app，最后一道再试时改得回了：旧版本回到 app，用户数据从 app.failed 搬回来，根目录
        文件换回旧的，结果照样是 rolled_back，起旧版本。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=1)
        keys = [(ua.APP_OLD, ua.APP), (ua.APP_FAILED, ua.APP)]
        self.last_resort_after(dict.fromkeys(keys), keys)
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.failed), NEW_PROGRAM)
        self.assertFalse(os.path.lexists(layout.old_slot))
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_EXITED.format(detail="退出码 1"), 0)
        self.assertEqual(fake.started(), [b"new program", b"old program"])

    def test_last_resort_after_half_swap(self):
        """第二次改名失败、第一次又改不回来（app 不在了），最后一道再试时改得回了：旧版本回到 app，结果 not_applied（原因
        写哪个改不回），起旧版本；复制出来的 app.next 留给收尾删。"""
        layout = Layout(self)
        fake = Fake(self)
        keys = [(ua.APP_NEXT, ua.APP), (ua.APP_OLD, ua.APP)]
        self.last_resort_after(dict.fromkeys(keys), keys)
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.next), NEW_PROGRAM)
        result = layout.result()
        self.assertEqual((result["status"], result["attempts"]), (ua.NOT_APPLIED, 1))
        self.assertIn(f"{ua.APP_OLD} 改不回 {ua.APP}", result["reason"])
        self.assertEqual(fake.started(), [b"old program"])

    def test_last_resort_uses_next(self):
        """同上，但 app.old 怎么也改不回 app：把 app.next 改成 app（新版本），用户数据从 app.old 搬进去，结果 ok，起新版本。"""
        layout = Layout(self)
        fake = Fake(self)
        keys = [(ua.APP_NEXT, ua.APP), (ua.APP_OLD, ua.APP)]
        self.last_resort_after(dict.fromkeys(keys), keys[:1])
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(files_under(layout.app), {**NEW_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.old_slot), OLD_PROGRAM)
        self.assertFalse(os.path.lexists(layout.next))
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(fake.started(), [b"new program"])

    def test_leftover_app_next(self):
        """之前留下的 app.next（上次没做完的）：先删掉再复制；里面有用户数据的（不该有，万一）改名成「app.next-时间」留着。"""
        for leftover, kept in (({"ACC.exe": b"stale"}, None), ({"logs/x.log": b"x"}, {"logs/x.log": b"x"})):
            with self.subTest(kept=kept is not None):
                layout = Layout(self)
                Fake(self)
                write_files(layout.next, leftover)
                self.assertEqual(layout.apply(), 0)
                self.assert_result(layout, ua.OK, "", 0)
                self.assertEqual(files_under(layout.app), {**NEW_PROGRAM, **USER_FILES})
                aside = [p for p in layout.root.iterdir() if p.name.startswith(f"{ua.APP_NEXT}-")]
                if kept is None:
                    self.assertEqual(aside, [])
                else:
                    self.assertEqual(len(aside), 1)
                    self.assertRegex(aside[0].name, rf"^{re.escape(ua.APP_NEXT)}-\d{{8}}-\d{{6}}$")
                    self.assertEqual(files_under(aside[0]), kept)

    def test_leftover_app_old(self):
        """之前留下的 app.old：没有用户数据的（只剩空文件夹也算没有）删掉；有用户数据的改名成「app.old-时间」留着，同名的
        已经有了时接 -2，不覆盖。"""
        layout = Layout(self)
        Fake(self)
        write_files(layout.old_slot, {"ACC.exe": b"older program"})
        (layout.old_slot / "logs").mkdir()
        (layout.old_slot / "assets" / "heroes" / "user").mkdir(parents=True)
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assertEqual([p.name for p in layout.root.iterdir() if p.name.startswith(f"{ua.APP_OLD}-")], [])

        layout = Layout(self)
        Fake(self)
        stamp = "1" * 8 + "-" + "1" * 6
        patch(self, ua, "_stamp", lambda: stamp)
        write_files(layout.old_slot, {"ACC.exe": b"older program", "state.json": b"older settings"})
        write_files(layout.root, {f"{ua.APP_OLD}-{stamp}/state.json": b"even older"})
        self.assertEqual(layout.apply(), 0)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(files_under(layout.app), {**NEW_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.old_slot), OLD_PROGRAM)
        self.assertEqual(files_under(layout.root / f"{ua.APP_OLD}-{stamp}"), {"state.json": b"even older"})
        self.assertEqual(files_under(layout.root / f"{ua.APP_OLD}-{stamp}-2"),
                         {"ACC.exe": b"older program", "state.json": b"older settings"})

    def test_leftover_app_old_stuck(self):
        """之前留下的 app.old 有用户数据、又改不了名：什么都不换（删掉 app.next），not_applied（原因写是哪个文件夹），起旧版本。"""
        layout = Layout(self)
        fake = Fake(self)
        write_files(layout.old_slot, {"state.json": b"older settings"})
        failing_rename(self, {(ua.APP_OLD, f"{ua.APP_OLD}-*"): None})
        self.assertEqual(layout.apply(), 0)
        self.assert_result(layout, ua.NOT_APPLIED, ua.REASON_SLOT.format(name=ua.APP_OLD), 1)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.old_slot), {"state.json": b"older settings"})
        self.assertFalse(os.path.lexists(layout.next))
        self.assertEqual(root_files(layout.root), OLD_ROOT)
        self.assertEqual(fake.started(), [b"old program"])

    def test_user_data_in_new_app_not_copied(self):
        """update\\new\\app 里有用户数据（有人直接运行过那里的 ACC.exe）：复制成 app.next 时不复制（不分大小写，带通配符的
        只看第一层），旧版本的用户数据照常整份搬进新的 app，app.old 里只剩旧版本的程序；update\\new\\app 原样不动。"""
        layout = Layout(self)
        Fake(self)
        stray = {"STATE.json": b"stray settings", "Logs/stray.log": b"stray", ".tmp/crash.txt": b"crash",
                 "assets/Skins/s.png": b"skin", "schemes.json.tmp": b"half", "assets/manifest-local.json": b"[]",
                 "runtime/state.json": b"program file"}
        write_files(layout.new_app, stray)
        self.assertEqual(layout.apply(), 0)
        self.assert_result(layout, ua.OK, "", 0)
        self.assertEqual(files_under(layout.app), {**NEW_PROGRAM, **USER_FILES, "runtime/state.json": b"program file"})
        self.assertEqual(files_under(layout.old_slot), OLD_PROGRAM)
        self.assertEqual(files_under(layout.new_app), {**NEW_PROGRAM, **stray})
        self.assertNotIn("不覆盖", layout.log())

    def test_root_files_cases(self):
        """根目录两个文件：之前留下的 .old 先删；旧的不在时直接放上新的；新版本里没有的留着旧的。"""
        layout = Layout(self)
        Fake(self)
        write_files(layout.root, {f"{LAUNCHER}.old": b"older launcher"})
        (layout.root / MANUAL).unlink()
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(root_files(layout.root), {**NEW_ROOT, f"{LAUNCHER}.old": b"old launcher"})

        layout = Layout(self)
        Fake(self)
        (layout.new / MANUAL).unlink()
        self.assertEqual(layout.apply(), 0)
        self.assertEqual(root_files(layout.root), {LAUNCHER: b"new launcher", f"{LAUNCHER}.old": b"old launcher",
                                                   MANUAL: b"old manual"})

    def test_root_file_busy(self):
        """根目录的启动程序一时改不了名（还没退出）：隔一会儿再试；一直不行就留着旧的、不留 .new，换版本照常算换好了。
        新的改不上去时把旧的换回来。"""
        for rule, launcher in (((LAUNCHER, f"{LAUNCHER}.old"), 3), ((LAUNCHER, f"{LAUNCHER}.old"), None),
                               ((f"{LAUNCHER}.new", LAUNCHER), None)):
            with self.subTest(rule=rule, times=launcher):
                layout = Layout(self)
                Fake(self)
                failing_rename(self, {rule: launcher})
                self.assertEqual(layout.apply(), 0)
                self.assert_result(layout, ua.OK, "", 0)
                expected = {MANUAL: b"new manual", f"{MANUAL}.old": b"old manual"}
                if launcher is None:
                    expected[LAUNCHER] = b"old launcher"
                else:
                    expected.update({LAUNCHER: b"new launcher", f"{LAUNCHER}.old": b"old launcher"})
                self.assertEqual(root_files(layout.root), expected)

    def test_attempts(self):
        """次数：之前这个版本没换上 1 次的，这次又没换上记 2 次、换上了照旧记 1；之前的结果是别的版本的、或者不是没换上的，
        从 0 算起。"""
        for before, old, attempts in (({"status": ua.NOT_APPLIED, "attempts": 1}, "stays", 2),
                                      ({"status": ua.NOT_APPLIED, "attempts": 1}, "exits", 1),
                                      ({"status": ua.NOT_APPLIED, "attempts": 1, "version": NEWER}, "stays", 1),
                                      ({"status": ua.ROLLED_BACK, "attempts": 0}, "stays", 1)):
            with self.subTest(before=before, old=old):
                layout = Layout(self)
                Fake(self, old=old)
                layout.write_result(**before)
                self.assertEqual(layout.apply(), 0)
                self.assertEqual(layout.result()["attempts"], attempts)

    def test_cwd_inside_app(self):
        """当前目录在 app 里（app 就改不了名）：先换到自己的文件夹，照常换。"""
        layout = Layout(self)
        Fake(self)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(layout.app / "logs")
        self.assertEqual(layout.apply(), 0)
        self.assert_swapped(layout)
        self.assertEqual(Path.cwd(), layout.new_app)


class PendingTest(Base):
    def setUp(self):
        super().setUp()
        frozen(self)

    def test_ready(self):
        """备好了、比当前版本新、新版本的程序在：返回 ready.json 的内容；什么都不删。"""
        layout = Layout(self)
        write_files(layout.root, {f"{LAUNCHER}.old": b"x", f"{ua.APP_OLD}/ACC.exe": b"x", f"{ua.APP_FAILED}/ACC.exe": b"x"})
        before = snapshot(layout.root)
        ready = ua.pending(layout.root, OLD)
        self.assertEqual((ready["format"], ready["version"]), (1, NEW))
        self.assertEqual(snapshot(layout.root), before)

    def test_retry_allowed(self):
        """这个版本没换上 1 次（不到 2 次）、旧版本已经读走了那个结果（显示过提示），之前的结果是别的版本的：照样返回。"""
        for result in ({"status": ua.NOT_APPLIED, "attempts": 1, "taken": True},
                       {"status": ua.NOT_APPLIED, "attempts": 5, "version": NEWER},
                       {"status": ua.ROLLED_BACK, "version": NEWER}):
            with self.subTest(result=result):
                layout = Layout(self)
                layout.write_result(**result)
                self.assertEqual(ua.pending(layout.root, OLD)["version"], NEW)

    def test_not_pending(self):
        """不该换的几种情况都为 None，而且什么都不删、不改。"""
        cases = {
            "没有 ready.json": lambda l: (l.update / ua.READY_NAME).unlink(),
            "读不出": lambda l: (l.update / ua.READY_NAME).write_text("not json", encoding="utf-8"),
            "格式不对": lambda l: l.write_ready(NEW, format=2),
            "版本号不对": lambda l: l.write_ready("1.0"),
            "校验值不对": lambda l: l.write_ready(NEW, sha256="xyz"),
            "没有备好的时间": lambda l: l.write_ready(NEW, prepared=""),
            "不比当前新": lambda l: l.write_ready(OLD),
            "新版本的程序不在": lambda l: (l.new_app / ua.EXE).unlink(),
            "没换上、还没读走（旧版本还没显示提示）": lambda l: l.write_result(status=ua.NOT_APPLIED, attempts=1),
            "没做完、还没读走": lambda l: l.write_result(status=ua.NOT_APPLIED, attempts=1, done=False),
            "没换上 2 次了": lambda l: l.write_result(status=ua.NOT_APPLIED, attempts=2, taken=True),
            "退回过": lambda l: l.write_result(status=ua.ROLLED_BACK),
            "已经换上过": lambda l: l.write_result(status=ua.OK),
        }
        for label, spoil in cases.items():
            with self.subTest(label):
                layout = Layout(self)
                spoil(layout)
                write_files(layout.root, {f"{ua.APP_OLD}/ACC.exe": b"x", f"{MANUAL}.old": b"x"})
                before = snapshot(layout.root)
                self.assertIsNone(ua.pending(layout.root, OLD))
                self.assertEqual(snapshot(layout.root), before)
        layout = Layout(self)
        self.assertIsNone(ua.pending(layout.root, NEW))  # 当前版本就是它
        self.assertIsNone(ua.pending(layout.root, NEWER))

    def test_downloaded_again(self):
        """没换上的结果早于 ready.json（没换上以后又下好了一遍）：不看那份结果，照常交回（次数到了上限、还没读走的都一样）。
        退回过的、结果不早于 ready.json 的、两个时间有一个读不出的，照旧不交；换版本程序正在换时照旧不交。"""
        early, late = "2030-01-20T10:00:00", "2030-01-20T10:05:00"
        for result in ({"status": ua.NOT_APPLIED, "attempts": 2, "taken": True},
                       {"status": ua.NOT_APPLIED, "attempts": 5, "taken": True},
                       {"status": ua.NOT_APPLIED, "attempts": 1},
                       {"status": ua.NOT_APPLIED, "attempts": 2, "done": False, "taken": True}):
            with self.subTest(result=result):
                layout = Layout(self)
                layout.write_result(time=early, **result)
                layout.write_ready(NEW, prepared=late)
                self.assertEqual(ua.pending(layout.root, OLD)["prepared"], late)
        for label, result, prepared in (
                ("退回过", {"status": ua.ROLLED_BACK, "time": early}, late),
                ("结果和备好在同一刻", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": late}, late),
                ("结果比备好晚", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": late}, early),
                ("结果的时间读不出", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": "昨天"}, late),
                ("结果没有时间", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": None}, late),
                ("备好的时间读不出", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": early}, "刚才"),
                ("时间写法不一样", {"status": ua.NOT_APPLIED, "attempts": 2, "taken": True, "time": early},
                 late + "+08:00")):
            with self.subTest(label):
                layout = Layout(self)
                layout.write_result(**result)
                layout.write_ready(NEW, prepared=prepared)
                self.assertIsNone(ua.pending(layout.root, OLD))
        layout = Layout(self)
        layout.write_result(status=ua.NOT_APPLIED, attempts=2, taken=True, time=early)
        layout.write_ready(NEW, prepared=late)
        lock = ua._take_update_lock(layout.root)
        self.addCleanup(ua._release_lock, lock)
        self.assertIsNone(ua.pending(layout.root, OLD))

    def test_update_running(self):
        """换版本程序正在换这个文件夹（锁在）：None。"""
        layout = Layout(self)
        lock = ua._take_update_lock(layout.root)
        self.addCleanup(ua._release_lock, lock)
        self.assertIsNone(ua.pending(layout.root, OLD))
        ua._release_lock(lock)
        self.assertEqual(ua.pending(layout.root, OLD)["version"], NEW)


class LaunchTest(Base):
    def setUp(self):
        super().setUp()
        frozen(self)

    def test_handed_over(self):
        """换版本程序回话了（接手了）：True。起的是 update\\new\\app\\ACC.exe --apply-update <旧进程号>，工作目录是它自己的
        文件夹，环境变量里是回话用的事件、没有等悬浮窗的那个；不写结果。"""
        layout = Layout(self)
        fake = Fake(self)
        with mock.patch.dict(os.environ, {ua.READY_ENV: "inherited", ua.COMPAT_ENV: "~ HIGHDPIAWARE"}):
            self.assertTrue(ua.launch(layout.root, old_pid=777))
        start = fake.starts[0]
        self.assertEqual(start["command"], [str(layout.new_app / ua.EXE), ua.APPLY_ARG, "777"])
        self.assertEqual((start["cwd"], start["quiet"], start["program"]), (layout.new_app, True, b"new program"))
        self.assertEqual(start["env"][ua.STARTED_ENV], fake.events[0])
        self.assertTrue(fake.events[0].startswith(f"Local\\{paths.FOLDER_NAME}.UpdateStarted."))
        self.assertNotIn(ua.READY_ENV, start["env"])
        self.assertNotIn(ua.COMPAT_ENV, start["env"])
        self.assertIsNone(layout.result())
        self.assertEqual(fake.terminated, [])

    def test_not_handed_over(self):
        """没接手：False，记一次没换上（not_applied，次数 1，进程号是那个换版本程序的）。核对没通过、出错退出、一直不回话
        （结束它）、起不来分别写原因；另一个换版本程序在换（EXIT_BUSY）时不记。"""
        cases = (
            ({"ready": ua.EXITED, "code": ua.EXIT_REFUSED}, ua.REASON_LAUNCH_REFUSED, 5001),
            ({"ready": ua.EXITED, "code": 0xC0000135},
             ua.REASON_LAUNCH_EXIT.format(detail="错误代码 0xC0000135"), 5001),
            ({"ready": ua.RUNNING}, ua.REASON_LAUNCH_SILENT, 5001),
            ({"start_error": OSError(13, "拒绝访问。", None, 5)},
             ua.REASON_LAUNCH_START.format(detail="PermissionError [WinError 5]"), 0),
            ({"ready": ua.EXITED, "code": ua.EXIT_BUSY}, None, None),
        )
        for kwargs, reason, pid in cases:
            with self.subTest(reason=reason):
                layout = Layout(self)
                fake = Fake(self, **kwargs)
                self.assertFalse(ua.launch(layout.root, old_pid=777))
                if reason is None:
                    self.assertIsNone(layout.result())
                    continue
                result = self.assert_result(layout, ua.NOT_APPLIED, reason, 1)
                self.assertEqual((result["from"], result["pid"]), (OLD, pid))
                self.assertEqual(fake.terminated, [5001] if reason == ua.REASON_LAUNCH_SILENT else [])

    def test_stops_after_max(self):
        """没接手：结果读走（旧版本显示过提示）以前不再起；读走以后再起一次，次数到了 2 次就不再起换版本程序。"""
        layout = Layout(self)
        fake = Fake(self, ready=ua.EXITED, code=ua.EXIT_REFUSED)
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(layout.result()["attempts"], 1)
        self.assertIsNone(ua.pending(layout.root, OLD))  # 没换上以后不马上再换
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(len(fake.starts), 1)
        self.assertEqual(ua.take_result(layout.root)["attempts"], 1)
        self.assertEqual(ua.pending(layout.root, OLD)["version"], NEW)  # 用户下一次自己启动时再换
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(layout.result()["attempts"], 2)
        self.assertEqual(ua.take_result(layout.root)["attempts"], 2)
        self.assertIsNone(ua.pending(layout.root, OLD))
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(len(fake.starts), 2)

    def test_downloaded_again_after_max(self):
        """同一次运行里「重启更新」起不来两次（旧版本每次都读走结果、显示提示），次数到了上限，pending 不再交、launch 不再起；
        用户又点「检查更新」重新下好一遍（ready.json 比那份结果新）以后，那份结果说的是之前备好的那一份：pending 照常交回，
        launch 照常起换版本程序。次数接着记：这一次再没换上就是第 3 次，又拦下，直到再下好一遍。"""
        layout = Layout(self)
        clock = ["2030-01-20T10:01:00"]
        patch(self, ua, "_now", lambda: clock[0])  # 结果里记的时间
        layout.write_ready(NEW, prepared="2030-01-20T10:00:00")
        fake = Fake(self, ready=ua.EXITED, code=ua.EXIT_REFUSED)
        for attempts in (1, 2):
            clock[0] = f"2030-01-20T10:0{attempts}:00"
            self.assertFalse(ua.launch(layout.root, old_pid=777))
            self.assertEqual(ua.take_result(layout.root)["attempts"], attempts)  # 旧版本读走、显示提示
        self.assertIsNone(ua.pending(layout.root, OLD))
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(len(fake.starts), 2)
        # 又下好了一遍（下载、备好时 ready.json 先删后写，update\\new 换成新解压的）
        layout.write_ready(NEW, prepared="2030-01-20T10:05:00")
        before = snapshot(layout.root)
        ready = ua.pending(layout.root, OLD)
        self.assertEqual((ready["version"], ready["prepared"]), (NEW, "2030-01-20T10:05:00"))
        self.assertEqual(snapshot(layout.root), before)  # 只读
        clock[0] = "2030-01-20T10:06:00"
        self.assertFalse(ua.launch(layout.root, old_pid=777))  # 起了换版本程序（这次它照样核对没通过）
        self.assertEqual(len(fake.starts), 3)
        self.assertEqual(ua.take_result(layout.root)["attempts"], 3)
        self.assertIsNone(ua.pending(layout.root, OLD))  # 这份结果比 ready.json 新：照旧拦下
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual(len(fake.starts), 3)
        # 再下好一遍，这次换版本程序接手了
        layout.write_ready(NEW, prepared="2030-01-20T10:10:00")
        fake.ready = ua.READY
        self.assertTrue(ua.launch(layout.root, old_pid=777))
        self.assertEqual(len(fake.starts), 4)
        self.assertEqual(fake.starts[-1]["command"], [str(layout.new_app / ua.EXE), ua.APPLY_ARG, "777"])

    def test_child_wrote_its_own(self):
        """换版本程序自己已经写过结果（开始时那一份「没做完」，次数已经加过）再退出的：不另写。"""
        layout = Layout(self)

        def wrote(pid):
            layout.write_result(status=ua.NOT_APPLIED, reason=ua.REASON_INTERRUPTED, attempts=1, pid=pid, done=False)

        Fake(self, ready=ua.EXITED, code=1, on_start=wrote)
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        result = layout.result()
        self.assertEqual((result["pid"], result["attempts"], result["reason"], result["done"]),
                         (5001, 1, ua.REASON_INTERRUPTED, False))

    def test_nothing_to_launch(self):
        """没有该换的版本、建不了回话用的事件：False，什么都不起。"""
        layout = Layout(self)
        (layout.update / ua.READY_NAME).unlink()
        fake = Fake(self)
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        layout = Layout(self)
        fake2 = Fake(self, event=False)
        self.assertFalse(ua.launch(layout.root, old_pid=777))
        self.assertEqual((fake.starts, fake2.starts), ([], []))
        self.assertIsNone(layout.result())


class TakeResultTest(Base):
    def setUp(self):
        super().setUp()
        frozen(self)

    def test_only_once(self):
        """读走以后在文件里标记已读，同一个结果只交一次；交出去的不带标记。"""
        for status in (ua.OK, ua.ROLLED_BACK, ua.NOT_APPLIED):
            with self.subTest(status=status):
                layout = Layout(self)
                layout.write_result(status=status, reason="" if status == ua.OK else "原因", attempts=1)
                got = ua.take_result(layout.root)
                self.assertEqual((got["status"], got["version"], got["attempts"]), (status, NEW, 1))
                self.assertNotIn("taken", got)
                self.assertIs(layout.result()["taken"], True)
                self.assertIsNone(ua.take_result(layout.root))

    def test_unfinished_while_running(self):
        """「没做完」的那一份：写它的换版本程序还在运行时不交、不标记；它不在了（中途被结束）才交。"""
        layout = Layout(self)
        layout.write_result(status=ua.NOT_APPLIED, reason=ua.REASON_INTERRUPTED, attempts=1, done=False, pid=6000)
        with mock.patch.object(ua, "_writer_running", return_value=True) as running:
            self.assertIsNone(ua.take_result(layout.root))
        running.assert_called_once_with(layout.root, 6000)
        self.assertNotIn("taken", layout.result())
        with mock.patch.object(ua, "_writer_running", return_value=False):
            got = ua.take_result(layout.root)
        self.assertEqual((got["status"], got["reason"]), (ua.NOT_APPLIED, ua.REASON_INTERRUPTED))

    def test_unfinished_for_running_version(self):
        """「没做完」的那一份换的就是正在运行的这个版本（两次改名以后换版本程序中途退出了）：不交、不标记（不提示没换上，
        由 after_start 补完）。"""
        layout = Layout(self)
        layout.write_result(status=ua.NOT_APPLIED, reason=ua.REASON_INTERRUPTED, attempts=1, done=False, pid=6000)
        patch(self, version, "VERSION", NEW)
        with mock.patch.object(ua, "_writer_running", return_value=False):
            self.assertIsNone(ua.take_result(layout.root))
        self.assertNotIn("taken", layout.result())

    def test_cannot_mark(self):
        """标记不上（文件被占着）：这次不交，下次再交。"""
        layout = Layout(self)
        layout.write_result(status=ua.ROLLED_BACK, reason="原因")
        with mock.patch.object(ua, "_write_result", side_effect=busy()):
            self.assertIsNone(ua.take_result(layout.root))
        self.assertNotIn("taken", layout.result())
        self.assertEqual(ua.take_result(layout.root)["status"], ua.ROLLED_BACK)

    def test_nothing(self):
        """没有结果、读不出、格式不对：None。"""
        layout = Layout(self)
        self.assertIsNone(ua.take_result(layout.root))
        for text in ("{", "[]", json.dumps({"format": 1, "status": "done", "version": NEW, "pid": 0, "attempts": 0}),
                     json.dumps({"format": 2, "status": ua.OK, "version": NEW, "pid": 0, "attempts": 0}),
                     json.dumps({"format": 1, "status": ua.OK, "version": NEW, "pid": True, "attempts": 0})):
            with self.subTest(text=text):
                (layout.update / ua.RESULT_NAME).write_text(text, encoding="utf-8")
                self.assertIsNone(ua.take_result(layout.root))
                self.assertEqual((layout.update / ua.RESULT_NAME).read_text(encoding="utf-8"), text)


class AfterStartTest(Base):
    def setUp(self):
        super().setUp()
        frozen(self)

    def swapped(self) -> Layout:
        """换好了、还没收尾的发行版文件夹（结果 ok，写它的是本进程，已经不拿着换版本的锁）。"""
        layout = Layout(self)
        Fake(self)
        self.assertEqual(layout.apply(), 0)
        return layout

    def test_cleanup_after_ok(self):
        """结果 ok：删根目录的 .old、.new，删 app.next、app.old，app.failed 只留最近的一份（没带时间的），换版本的记录接到
        app\\logs\\update.log，最后删 update；app 里别的和根目录的新文件不动。"""
        layout = self.swapped()
        older = f"{ua.APP_FAILED}-{'1' * 8}-{'1' * 6}"
        write_files(layout.root, {f"{ua.APP_NEXT}/ACC.exe": b"half", f"{ua.APP_FAILED}/ACC.exe": b"failed newest",
                                  f"{older}/ACC.exe": b"failed older", f"{LAUNCHER}.new": b"half launcher"})
        log = layout.log()
        ua.after_start(layout.root, wait=1)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()),
                         sorted([LAUNCHER, MANUAL, ua.APP, ua.APP_FAILED]))
        app = files_under(layout.app)
        kept = app.pop(f"logs/{ua.KEPT_LOG}").decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(app, {**NEW_PROGRAM, **USER_FILES})
        self.assertTrue(kept.startswith(log))  # 换版本程序记的整段在前，收尾记的接在后面
        self.assertIn("收尾：删了 app.old", kept)
        self.assertEqual(files_under(layout.failed), {"ACC.exe": b"failed newest"})
        self.assertEqual(root_files(layout.root), NEW_ROOT)

    def test_kept_log_capped(self):
        """app\\logs\\update.log 已经有了的接在后面；接完超过 KEPT_LOG_MAX 时只留最后约一半，从一行的开头起。"""
        patch(self, ua, "KEPT_LOG_MAX", 4096)
        layout = self.swapped()
        old_lines = "".join(f"[old] 第 {i} 行\n" for i in range(400))
        write_files(layout.app, {f"logs/{ua.KEPT_LOG}": old_lines.encode("utf-8")})
        ua.after_start(layout.root, wait=1)
        kept = (layout.app / "logs" / ua.KEPT_LOG).read_bytes()
        self.assertLessEqual(len(kept), 4096)
        self.assertGreater(len(kept), 1024)
        text = kept.decode("utf-8")
        self.assertTrue(text.startswith("["))  # 从一行的开头起
        self.assertIn("收尾：删了 app.old", text)  # 新接的在最后
        self.assertNotIn("[old] 第 0 行", text)

    def test_failed_keeps_newest(self):
        """app.failed 都带时间时留时间最晚的；时间一样时留后接的序号大的。"""
        layout = Layout(self)
        layout.write_result()
        a, b = f"{ua.APP_FAILED}-{'1' * 8}-{'1' * 6}", f"{ua.APP_FAILED}-{'1' * 8}-{'2' * 6}"
        write_files(layout.root, {f"{a}/x": b"a", f"{b}/x": b"b", f"{b}-2/x": b"c"})
        ua.after_start(layout.root, wait=1)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()),
                         sorted([LAUNCHER, MANUAL, ua.APP, f"{b}-2"]))

    def test_waits_for_writer(self):
        """写结果的换版本程序还在运行：等它，等不到就这次什么都不删；等到了照常收尾。"""
        layout = self.swapped()
        before = snapshot(layout.root)
        with mock.patch.object(ua, "_writer_running", return_value=True):
            ua.after_start(layout.root, wait=0.2)
        self.assertEqual(snapshot(layout.root), before)
        with mock.patch.object(ua, "_writer_running", side_effect=[True, False]):
            ua.after_start(layout.root, wait=5)
        self.assertFalse(os.path.lexists(layout.update))
        self.assertFalse(os.path.lexists(layout.old_slot))

    def test_not_read_yet(self):
        """rolled_back、not_applied 还没读过（旧版本还没显示提示）：什么都不删；读过以后照常收尾（not_applied 已经 2 次了，
        不再换，update 也删）。"""
        for status, attempts in ((ua.ROLLED_BACK, 0), (ua.NOT_APPLIED, 2)):
            with self.subTest(status=status):
                layout = Layout(self)
                layout.write_result(status=status, reason="原因", attempts=attempts)
                write_files(layout.root, {f"{LAUNCHER}.old": b"old launcher", f"{ua.APP_FAILED}/ACC.exe": b"x"})
                before = snapshot(layout.root)
                ua.after_start(layout.root, wait=0.2)
                self.assertEqual(snapshot(layout.root), before)
                self.assertIsNotNone(ua.take_result(layout.root))
                ua.after_start(layout.root, wait=0.2)
                self.assertEqual(sorted(p.name for p in layout.root.iterdir()),
                                 sorted([LAUNCHER, MANUAL, ua.APP, ua.APP_FAILED]))

    def test_retry_keeps_update(self):
        """not_applied 读过了、次数不到 2 次、备好的还是这个版本：收尾时 update 整个留着（新版本、ready.json、读过的结果、
        记录），别的照常收；用户下次启动时 pending 照样交出它。备好的不是这个版本、新版本的程序不在、不比正在运行的新时
        照常删 update。"""
        layout = Layout(self)
        layout.write_result(status=ua.NOT_APPLIED, reason="原因", attempts=1)
        write_files(layout.root, {f"{LAUNCHER}.old": b"old launcher", f"{ua.APP_NEXT}/ACC.exe": b"half"})
        self.assertIsNone(ua.pending(layout.root, OLD))
        self.assertIsNotNone(ua.take_result(layout.root))
        update = snapshot(layout.update)
        ua.after_start(layout.root, wait=0.2)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()),
                         sorted([LAUNCHER, MANUAL, ua.APP, ua.UPDATE_DIR]))
        after = snapshot(layout.update)
        self.assertIn("用户下次启动时再换", after.pop(ua.LOG_NAME).decode("utf-8"))
        self.assertEqual(after, update)
        self.assertEqual(ua.pending(layout.root, OLD)["version"], NEW)
        for label, spoil in (("备好的是别的版本", lambda l: l.write_ready(NEWER)),
                             ("新版本的程序不在", lambda l: (l.new_app / ua.EXE).unlink()),
                             ("不比正在运行的新", lambda l: patch(self, version, "VERSION", NEW))):
            with self.subTest(label):
                layout = Layout(self)
                layout.write_result(status=ua.NOT_APPLIED, reason="原因", attempts=1, taken=True)
                spoil(layout)
                ua.after_start(layout.root, wait=0.2)
                if label == "备好的是别的版本":  # 别的下载：update 留着，只删结果
                    self.assertFalse((layout.update / ua.RESULT_NAME).exists())
                    self.assertTrue(layout.new.is_dir())
                else:
                    self.assertFalse(os.path.lexists(layout.update))

    def test_root_file_missing_restored(self):
        """根目录的正式文件不在（换的时候新旧两个都改不了名、换到一半断了电）：收尾时把「原名.old」改回正式的名字，不删
        最后一份；只剩「原名.new」（可能只写了一半）时留着不删。"""
        layout = self.swapped()
        (layout.root / LAUNCHER).unlink()
        (layout.root / MANUAL).unlink()
        (layout.root / f"{MANUAL}.old").unlink()
        write_files(layout.root, {f"{MANUAL}.new": b"half manual"})
        ua.after_start(layout.root, wait=1)
        self.assertEqual(root_files(layout.root), {LAUNCHER: b"old launcher", f"{MANUAL}.new": b"half manual"})
        self.assertFalse(os.path.lexists(layout.update))
        kept = (layout.app / "logs" / ua.KEPT_LOG).read_text(encoding="utf-8")
        self.assertIn(f"{LAUNCHER} 不在，把 {LAUNCHER}.old 改回了 {LAUNCHER}", kept)
        self.assertIn(f"{MANUAL} 不在，{MANUAL}.new 留着", kept)

    def interrupted(self) -> Layout:
        """换版本程序两次改名都做完以后中途退出了（这里在搬用户数据的那一步让它像被结束一样直接退出）、换上的新版本正在运行
        的发行版文件夹：app 里是新版本、没有用户数据，app.old 里是旧版本和全部用户数据，结果是「没做完」的那一份。"""

        class Killed(BaseException):
            pass

        layout = Layout(self)
        fake = Fake(self)
        patch(self, ua._Apply, "move_data", mock.Mock(side_effect=Killed()))
        with self.assertRaises(Killed):
            layout.apply()
        self.assertEqual(files_under(layout.app), NEW_PROGRAM)
        self.assertEqual(files_under(layout.old_slot), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual({k: layout.result()[k] for k in ("status", "done", "attempts")},
                         {"status": ua.NOT_APPLIED, "done": False, "attempts": 1})
        self.assertEqual(fake.starts, [])
        patch(self, version, "VERSION", NEW)  # 正在运行的是换上的新版本
        return layout

    def test_finish_interrupted(self):
        """两次改名以后换版本程序中途退出了：新版本读结果时不提示没换上；收尾时把 app.old 里的用户数据搬进 app，正在运行的
        程序已经用上了自己那一份的（设置、方案文件和两份下载记录）不搬、留在 app.old 里（有用户数据，app.old 不删），结果
        改成 ok，照常收尾。"""
        layout = self.interrupted()
        self.assertIsNone(ua.take_result(layout.root))
        ua.after_start(layout.root, wait=1)
        late = {k: v for k, v in USER_FILES.items() if any(fnmatch.fnmatch(k, p) for p in ua.LATE_SKIP)}
        self.assertEqual(set(late), {"state.json", "state.fallback.json", "schemes.json.bak", "schemes.fallback.json",
                                     "assets/manifest-local.json", "assets/download-cooldown.json"})
        app = files_under(layout.app)
        kept = app.pop(f"logs/{ua.KEPT_LOG}").decode("utf-8")
        self.assertEqual(app, {**NEW_PROGRAM, **{k: v for k, v in USER_FILES.items() if k not in late}})
        self.assertEqual(files_under(layout.old_slot), {**OLD_PROGRAM, **late})
        self.assertFalse(os.path.lexists(layout.update))  # 结果改成 ok 以后照常收尾
        self.assertIn("补搬用户数据", kept)
        self.assertIn("结果：ok（补完）", kept)
        self.assertIn(f"{ua.APP_OLD} 里还有用户数据", kept)

    def test_finish_interrupted_no_overwrite(self):
        """同上，新版本已经建了自己的 logs、.tmp：不覆盖，旧的整个留在 app.old 里，别的照搬；结果写不进时这次不收尾，
        下次启动再补。"""
        layout = self.interrupted()
        write_files(layout.app, {"logs/games/x.jsonl": b"new run", ".tmp/crash.txt": b"new crash"})
        with mock.patch.object(ua, "_write_result", side_effect=busy()):
            ua.after_start(layout.root, wait=1)
        self.assertEqual(layout.result()["done"], False)
        self.assertTrue(layout.new.is_dir())
        old = files_under(layout.old_slot)
        self.assertEqual(old["logs/recommend.log"], b"recommend log")
        self.assertEqual(old[".tmp/overlay_err.txt"], b"error log")
        self.assertEqual(files_under(layout.app)["reports/report.zip"], b"report")
        self.assertIn("已经有了，不覆盖", layout.log())
        ua.after_start(layout.root, wait=1)
        self.assertFalse(os.path.lexists(layout.update))

    def test_no_result_does_nothing(self):
        """没有结果：什么都不做（留下的 app.old 等也不动）。"""
        layout = Layout(self)
        write_files(layout.root, {f"{ua.APP_OLD}/ACC.exe": b"x", f"{LAUNCHER}.old": b"x"})
        before = snapshot(layout.root)
        ua.after_start(layout.root, wait=0.2)
        self.assertEqual(snapshot(layout.root), before)

    def test_app_old_with_user_data_kept(self):
        """app.old 里还有用户数据（搬的时候新的 app 里已经有同名的）：留着，记一笔；别的照常收。"""
        layout = self.swapped()
        write_files(layout.old_slot, {"state.json": b"left behind"})
        patch(self, ua, "_remove_update", lambda root: None)  # 留着 update，看记录
        ua.after_start(layout.root, wait=1)
        self.assertEqual(files_under(layout.old_slot), {**OLD_PROGRAM, "state.json": b"left behind"})
        self.assertIn(f"{ua.APP_OLD} 里还有用户数据（state.json），留着", layout.log())
        self.assertFalse(os.path.lexists(layout.new))
        self.assertEqual(root_files(layout.root), NEW_ROOT)

    def test_other_download_keeps_update(self):
        """update 里有正在下载、解压的，或者另一个版本的 ready.json、压缩包：update 留着，只删上一次的结果；这个版本自己的
        压缩包不算。"""
        cases = {
            "下载中": lambda l: write_files(l.update, {f"ACC-{NEWER}.zip.part": b"part"}),
            "解压中": lambda l: write_files(l.update, {"new.part/app/ACC.exe": b"x"}),
            "别的版本的压缩包": lambda l: write_files(l.update, {f"ACC-{NEWER}.zip": b"zip"}),
            "别的版本备好了": lambda l: l.write_ready(NEWER),
        }
        for label, add in cases.items():
            with self.subTest(label):
                layout = self.swapped()
                add(layout)
                ua.after_start(layout.root, wait=1)
                self.assertTrue(layout.update.is_dir())
                self.assertFalse((layout.update / ua.RESULT_NAME).exists())
                self.assertTrue(layout.new.is_dir())
                self.assertFalse(os.path.lexists(layout.old_slot))
        layout = self.swapped()
        write_files(layout.update, {f"ACC-{NEW}.zip": b"zip"})
        ua.after_start(layout.root, wait=1)
        self.assertFalse(os.path.lexists(layout.update))

    def test_stuck_keeps_result(self):
        """有删不掉的：结果留着，下次启动再收尾；下次删得掉了收干净。"""
        layout = self.swapped()
        write_files(layout.next, {"ACC.exe": b"half"})
        real = ua._rmtree

        def stuck(path):
            if Path(path).name == ua.APP_NEXT:
                raise PermissionError(13, "拒绝访问。", None, 5)
            real(path)

        with mock.patch.object(ua, "_rmtree", stuck):
            ua.after_start(layout.root, wait=1)
        self.assertTrue((layout.update / ua.RESULT_NAME).is_file())
        self.assertTrue(layout.next.is_dir())
        self.assertFalse(os.path.lexists(layout.old_slot))
        self.assertFalse(os.path.lexists(layout.new))
        self.assertIn("下次启动再收尾", layout.log())
        ua.after_start(layout.root, wait=1)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()), sorted([LAUNCHER, MANUAL, ua.APP]))

    def test_read_only_removed(self):
        """只读的旧文件、文件夹里只读的文件：去掉只读再删。"""
        layout = self.swapped()
        old_launcher = layout.root / f"{LAUNCHER}.old"
        os.chmod(old_launcher, stat.S_IREAD)
        os.chmod(layout.old_slot / "runtime" / "core.dll", stat.S_IREAD)
        ua.after_start(layout.root, wait=1)
        self.assertEqual(sorted(p.name for p in layout.root.iterdir()), sorted([LAUNCHER, MANUAL, ua.APP]))

    @unittest.skipUnless(WINDOWS, "目录链接只在 Windows 上有")
    def test_links_not_followed(self):
        """app.old 是目录链接：不删（记一笔），它指向的地方不碰；update 里的目录链接只删链接本身。"""
        import _winapi

        layout = Layout(self)
        layout.write_result()
        write_files(layout.outside, {"linked/state.json": b"keep", "linked2/keep.txt": b"keep2"})
        layout.mark_outside()
        _winapi.CreateJunction(str(layout.outside / "linked"), str(layout.old_slot))
        _winapi.CreateJunction(str(layout.outside / "linked2"), str(layout.update / "junk"))
        ua.after_start(layout.root, wait=1)
        self.assertTrue(os.path.isjunction(layout.old_slot))
        self.assertFalse(os.path.lexists(layout.update))
        self.assertEqual(files_under(layout.outside / "linked"), {"state.json": b"keep"})
        self.assertEqual(files_under(layout.outside / "linked2"), {"keep.txt": b"keep2"})

    def test_other_names_untouched(self):
        """收尾只删根下那几个名字：用户自己放的、长得像的名字一概不动。"""
        layout = self.swapped()
        mine = {"notes.txt": b"mine", f"{LAUNCHER}.bak": b"bak", f"{ua.APP}.older/ACC.exe": b"x",
                f"{ua.APP_OLD}2/ACC.exe": b"y", f"{ua.APP_FAILED}.zip": b"z", f"{ua.APP_OLD}-1/x": b"w",
                f"{ua.UPDATE_DIR}-notes/x.txt": b"u", f"{ua.APP}/{LAUNCHER}.old": b"in app"}
        write_files(layout.root, mine)
        ua.after_start(layout.root, wait=1)
        for rel, data in mine.items():
            self.assertEqual((layout.root / rel).read_bytes(), data)
        self.assertFalse(os.path.lexists(layout.update))


class DevTest(Base):
    def test_dev_does_nothing(self):
        """开发版（从源码运行）：pending、take_result 为 None，launch 为 False，after_start 什么都不做，不起任何程序。"""
        self.assertFalse(paths.is_frozen())
        layout = Layout(self)
        layout.write_result(status=ua.ROLLED_BACK, reason="原因")
        write_files(layout.root, {f"{ua.APP_OLD}/ACC.exe": b"x", f"{LAUNCHER}.old": b"x"})
        fake = Fake(self)
        before = snapshot(layout.root)
        self.assertIsNone(ua.pending(layout.root, OLD))
        self.assertFalse(ua.launch(layout.root, old_pid=1))
        self.assertIsNone(ua.take_result(layout.root))
        self.assertIsNone(ua.after_start(layout.root, wait=0))
        self.assertEqual(snapshot(layout.root), before)
        self.assertEqual(fake.starts, [])


class SafetyTest(Base):
    """删东西的范围。"""

    def test_slot_names(self):
        """可以删、可以另起名的只有 app.next、app.old、app.failed 和它们带时间的另起名（不分大小写）。"""
        for name in ("app.next", "app.old", "app.failed", "APP.Old", "app.old-12345678-123456",
                     "app.failed-12345678-123456-3"):
            self.assertTrue(ua.SLOT.fullmatch(name), name)
        for name in ("app", "update", "app.new", "app.old2", "app.older", "app.old-1", "app.old-12345678",
                     "xapp.old", "app.old.bak", "app.old-12345678-123456-", "app.old/logs"):
            self.assertIsNone(ua.SLOT.fullmatch(name), name)

    def test_refuses_other_paths(self):
        """删、另起名之前核对：不在根下这一层、名字不对的一律报错，什么都不删、不改名。"""
        layout = Layout(self)
        log = ua._Log(layout.root, "测试")
        write_files(layout.root, {f"{ua.APP}.older/x": b"x"})
        before = snapshot(layout.root)
        for path in (layout.app, layout.update, layout.root / f"{ua.APP}.older", layout.outside / ua.APP_OLD,
                     layout.app / ua.APP_OLD, layout.base / ua.APP_OLD, layout.root / ua.APP_OLD / "logs"):
            with self.subTest(path=path.relative_to(layout.base).as_posix()):
                with self.assertRaises(ValueError):
                    ua._discard(layout.root, path, log)
                with self.assertRaises(ValueError):
                    ua._aside(layout.root, path)
        for path in (layout.app / ua.EXE, layout.root / LAUNCHER, layout.root / MANUAL, layout.root / f"{LAUNCHER}.bak",
                     layout.outside / f"{LAUNCHER}.old", layout.app / f"{LAUNCHER}.old"):
            with self.subTest(path=path.relative_to(layout.base).as_posix()):
                with self.assertRaises(ValueError):
                    ua._remove_leftover(layout.root, path)
        self.assertEqual(snapshot(layout.root), before)

    def test_data_left(self):
        """还有没有用户数据：只剩空文件夹的不算，程序文件不算，往下几层有文件的算，名字带通配符的只看第一层。"""
        folder = sandbox.temp_dir(self) / ua.APP_OLD
        (folder / "logs").mkdir(parents=True)
        (folder / "assets" / "heroes" / "user").mkdir(parents=True)
        write_files(folder, {"ACC.exe": b"x", "assets/stars/star_bar.png": b"x", "runtime/state.json": b"x"})
        self.assertEqual(ua._data_left(folder), [])
        write_files(folder, {"assets/heroes/user/a.png": b"a", "schemes.json.bak": b"b"})
        self.assertEqual(ua._data_left(folder), ["assets/heroes", "schemes.json.bak"])


@unittest.skipUnless(WINDOWS, "只在 Windows 上")
class RealProcessTest(Base):
    """用真进程走一遍：命名事件、等进程、起进程那一套真的通。当程序的是跑一小段脚本的 Python（APP_SCRIPT、UPDATE_SCRIPT）。"""

    def setUp(self):
        super().setUp()
        for name, value in (("OLD_WAIT_S", 15.0), ("READY_WAIT_S", 20.0), ("LAUNCH_WAIT_S", 20.0),
                            ("RENAME_WAIT_S", 5.0), ("ROLLBACK_WAIT_S", 5.0)):
            patch(self, ua, name, value)
        patch(self, ua, "_file_version", file_version)
        self.scripts = sandbox.temp_dir(self, prefix="scripts-")
        (self.scripts / "app_script.py").write_text(APP_SCRIPT, encoding="utf-8")
        (self.scripts / "update_script.py").write_text(UPDATE_SCRIPT, encoding="utf-8")
        self.marker = self.scripts / "marker.txt"
        # 起的每个程序另开一个句柄，用例结束前等它们结束（工作目录在临时文件夹里，没结束时删不掉）
        self.children: list[int] = []
        real_start = ua._start

        def start(command, cwd, env, *, quiet):
            child = real_start(command, cwd, env, quiet=quiet)
            handle, _ = ua._api().open_process(child.pid, ua.SYNCHRONIZE)
            if handle:
                self.children.append(handle)
            return child

        patch(self, ua, "_start", start)
        self.addCleanup(self.wait_children)

    def wait_children(self) -> None:
        api = ua._api()
        while self.children:
            handle = self.children.pop()
            api.wait(handle, 15000)
            api.close(handle)

    def use_app_script(self, mode: str) -> None:
        script = [sys.executable, "-I", str(self.scripts / "app_script.py"), str(self.marker), mode]
        patch(self, ua, "_app_command", lambda app: script)
        patch(self, ua, "_launcher_command", lambda launcher: [*script, "launcher"])

    def old_process(self) -> int:
        """当旧版本的进程：0.5 秒以后自己退出。"""
        proc = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(0.5)"])
        self.addCleanup(proc.wait, 15)
        return proc.pid

    def marks(self, count: int) -> list[dict]:
        """等脚本记下 count 行（最多 15 秒），返回这几行。"""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            lines = self.marker.read_text(encoding="utf-8").splitlines() if self.marker.exists() else []
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
            time.sleep(0.05)
        self.fail(f"脚本没记下 {count} 行")

    def test_success_with_real_event(self):
        """真的等旧进程退出、真的起新版本：它在工作目录 app 里、从环境变量拿到事件名、打开并触发了它，换版本程序认出悬浮窗
        出来了。"""
        layout = Layout(self)
        self.use_app_script("ready")
        pid = self.old_process()
        self.assertEqual(ua.apply_main([ua.APPLY_ARG, str(pid)], app=layout.new_app, own_version=NEW), 0)
        self.wait_children()
        self.assert_swapped(layout)
        self.assert_result(layout, ua.OK, "", 0)
        [mark] = self.marks(1)
        self.assertEqual(Path(mark["cwd"]), layout.app)
        self.assertEqual(mark["program"], "new program")
        self.assertTrue(mark["event"].startswith(f"Local\\{paths.FOLDER_NAME}.UpdateReady."))
        self.assertTrue(mark["signaled"])
        self.assertFalse(mark["started"])
        self.assertIn("新版本的悬浮窗出来了", layout.log())

    def test_rollback_with_real_exit(self):
        """新版本在触发事件之前以 5 退出：认出来、退回，起的旧版本真的在 app 里跑起来了。"""
        layout = Layout(self)
        self.use_app_script("exit")
        pid = self.old_process()
        self.assertEqual(ua.apply_main([ua.APPLY_ARG, str(pid)], app=layout.new_app, own_version=NEW), 0)
        marks = self.marks(2)
        self.wait_children()
        self.assertEqual([m["program"] for m in marks], ["new program", "old program"])
        self.assertEqual({Path(m["cwd"]) for m in marks}, {layout.app})
        self.assertEqual(marks[1]["event"], "")  # 起旧版本时不带等悬浮窗的事件
        # 新版本直接起（等悬浮窗）；旧版本经根目录的启动程序起，启动程序的工作目录是根
        self.assertEqual([m["launcher_cwd"] and Path(m["launcher_cwd"]) for m in marks], [None, layout.root])
        self.assert_result(layout, ua.ROLLED_BACK, ua.REASON_EXITED.format(detail="退出码 5"), 0)
        self.assertEqual(files_under(layout.app), {**OLD_PROGRAM, **USER_FILES})
        self.assertEqual(files_under(layout.failed), NEW_PROGRAM)
        self.assertEqual(root_files(layout.root), OLD_ROOT)

    def test_launch_handshake(self):
        """launch 真的起换版本程序、等它回话：触发了事件为 True；以 EXIT_REFUSED 退出记一次没换上；以 EXIT_BUSY 退出不记。"""
        frozen(self)
        for mode, handed, status in (("ok", True, None), (str(ua.EXIT_REFUSED), False, ua.NOT_APPLIED),
                                     (str(ua.EXIT_BUSY), False, None)):
            with self.subTest(mode=mode):
                layout = Layout(self)
                patch(self, ua, "_update_command",
                      lambda exe, pid, mode=mode: [sys.executable, "-I", str(self.scripts / "update_script.py"), mode])
                self.assertEqual(ua.launch(layout.root, old_pid=os.getpid()), handed)
                self.wait_children()
                result = layout.result()
                self.assertEqual(result and result["status"], status)
                if status:
                    self.assertEqual(result["reason"], ua.REASON_LAUNCH_REFUSED)

    def test_writer_running(self):
        """写结果的换版本程序在不在：换版本的锁在就算在；否则看进程号——不在、在运行但不是 update\\new\\app\\ACC.exe 的都不算。"""
        layout = Layout(self)
        self.assertFalse(ua._writer_running(layout.root, 0))
        self.assertFalse(ua._writer_running(layout.root, os.getpid()))  # 在运行，但不是换版本程序
        proc = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
        proc.wait(15)
        self.assertFalse(ua._writer_running(layout.root, proc.pid))
        with mock.patch.object(ua._api(), "image", return_value=str(layout.new_app / ua.EXE)):
            self.assertTrue(ua._writer_running(layout.root, os.getpid()))
        lock = ua._take_update_lock(layout.root)
        self.addCleanup(ua._release_lock, lock)
        self.assertTrue(lock)
        self.assertIsNone(ua._take_update_lock(layout.root))  # 同一个文件夹只能有一个
        self.assertTrue(ua._update_running(layout.root))
        self.assertTrue(ua._writer_running(layout.root, 0))
        other = Layout(self)
        self.assertFalse(ua._update_running(other.root))  # 别的文件夹各换各的

    def test_file_version(self):
        """读程序文件的版本资源（前三段）；没有版本资源、不在的为 None。"""
        got = REAL_FILE_VERSION(Path(sys.executable))
        self.assertRegex(got, rf"^{sys.version_info.major}\.{sys.version_info.minor}\.\d+$")
        empty = sandbox.temp_dir(self) / "empty.exe"
        empty.write_bytes(b"")
        self.assertIsNone(REAL_FILE_VERSION(empty))
        self.assertIsNone(REAL_FILE_VERSION(empty.with_name("missing.exe")))


class NamesTest(unittest.TestCase):
    def test_same_as_elsewhere(self):
        """和别处约好的名字相同：防多开的锁、等悬浮窗的环境变量、打包脚本里的程序名和根目录的两个文件、app 文件夹；退出码不和
        启动程序、打包脚本认的那几个撞。"""
        from advisor.ui import single_instance, startup
        from tools import build_release

        self.assertEqual(ua.INSTANCE_MUTEX, single_instance.NAMES.mutex)
        self.assertEqual(ua.READY_ENV, startup.READY_ENV)
        self.assertEqual(ua.EXE, build_release.EXE)
        self.assertEqual(ua.ROOT_FILES, (build_release.LAUNCHER_EXE, build_release.MANUAL_NAME))
        self.assertEqual(ua.APP, build_release.APP_SUB)
        codes = {ua.EXIT_REFUSED, ua.EXIT_BUSY, ua.EXIT_FAILED, EXIT_REPORTED, build_release.LAUNCHER_NO_APP,
                 build_release.LAUNCHER_START_FAILED, 0, 1}
        self.assertEqual(len(codes), 8)

    def test_same_as_launcher_source(self):
        """和根目录的启动程序（C 写的，共用不了常量）约好的：等悬浮窗的环境变量、去掉的兼容性变量、app 文件夹和程序名。"""
        source = (PROJECT / "packaging" / "launcher.c").read_text(encoding="utf-8")

        def define(name: str) -> str:
            found = re.search(rf"^#define {name}\s+(\S+)", source, re.M)
            self.assertIsNotNone(found, f"launcher.c 里没有 #define {name}")
            return found.group(1)

        self.assertEqual(define("READY_VAR"), f'L"{ua.READY_ENV}"')
        self.assertEqual(define("COMPAT_VAR"), f'L"{ua.COMPAT_ENV}"')
        self.assertEqual(define("APP_DIR"), f'L"{ua.APP}"')
        self.assertEqual(define("APP_EXE"), f'L"{ua.APP}\\\\{ua.EXE}"')

    def test_no_heavy_imports(self):
        """导入换版本模块、跑入口的换版本参数（开发版的布局不对，以 EXIT_REFUSED 退出）都不带进界面库、识别库和界面模块
        （在子进程里核对）。"""
        code = textwrap.dedent(f"""
            import json, runpy, sys
            sys.path.insert(0, {str(PROJECT)!r})
            sys.argv = ["ACC.exe", "--apply-update", "1"]
            code = None
            try:
                runpy.run_path({str(ENTRY_FILE)!r}, run_name="__main__")
            except SystemExit as e:
                code = e.code
            heavy = sorted(m for m in sys.modules
                           if m.split(".")[0] in ("PySide6", "shiboken6", "numpy", "cv2", "onnxruntime", "rapidocr_lite")
                           or m.startswith(("advisor.ui", "advisor.vision")))
            print(json.dumps({{"code": code, "heavy": heavy, "loaded": "advisor.update_apply" in sys.modules}}))
        """)
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120,
                             cwd=PROJECT)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]),
                         {"code": ua.EXIT_REFUSED, "heavy": [], "loaded": True})


if __name__ == "__main__":
    unittest.main()
