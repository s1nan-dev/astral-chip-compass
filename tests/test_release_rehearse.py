"""换版本演练（tools/build_release.py 的 rehearse、rehearse_problems、apply_rehearsal 和它们用到的几个函数）：打包时试启动之后，
拿刚打好的压缩包真演练一次软件内更新的换版本（advisor/update_apply.py 的换版本程序）。这里不起真的程序：

- 压缩包是一个很小的假发行包（根目录的启动程序和使用说明、app\\ACC.exe、一份数据）。advisor.update.prepare 照真的来，只把
  核对版本号的 exe_version 换成假的。
- 换版本程序是假的进程（FakeApply，代替 subprocess.Popen 起的那一个）：记下命令行、工作目录、环境变量和起它时文件夹里的样子；
  每查一次（poll）往下走一步，照换版本程序对外的约定改「旧版本」的文件夹，写 update\\result.json 和 update\\apply.log。写结果、
  写记录、收尾时接走记录用的是 update_apply 自己的函数，写出来的格式和真的一样。剧本有换好了、退出码不是 0、退回了、没换、
  一直不结束、记号文件没搬过去、换好以后新版本收尾删了 update 等几种。
- 进程快照（_all_processes）是一张假的进程表：除了演练起的（换版本程序和它起的程序），还有别处的拾星助手（发行版文件夹里的、
  试启动文件夹里的、文件夹名字只差几个字的）；结束进程（_end_process）只记下结束了谁。逐个核对别处的一个都没碰、演练文件夹
  以外的文件一个都没动。
- 旧进程号、「已经接手」的事件、窗口标题、游戏和拾星助手在不在运行都是假的；时钟、等待由参数注入，不真的等。
另有几项直接用 Windows 的函数核对（只起一个马上就退出的 Python 进程，不起 ACC.exe）：交给换版本程序的旧进程号在换版本程序
看来已经退出、进程表里不列；换版本程序触发得了演练建的事件。
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import io
import itertools
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from advisor import paths, update, update_apply, version
from tools import build_release as br

OLD_PID, OLD_HANDLE = 4242, 7001  # 交给换版本程序的旧进程号（早已退出的进程）和留着的句柄
EVENT_HANDLE = 7002  # 「已经接手」的事件
EVENT_NAME = f"Local\\{paths.FOLDER_NAME}.RehearseStarted.test"
APPLY_PID, NEW_PID, LAUNCHER_PID, OLD_APP_PID = 5151, 6161, 6262, 6363  # 换版本程序、换上的新版本、退回后起的启动程序和旧版本
TITLE = version.APP_NAME  # 新版本悬浮窗的窗口标题
FAILED_TITLE = f"{version.APP_NAME}启动失败"
ROLLBACK_REASON = update_apply.REASON_EXITED.format(detail="退出码 1")
ON_WINDOWS = os.name == "nt"


def tmpdir() -> tempfile.TemporaryDirectory:
    base = paths.temp_dir() / "tests-release"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


def release_zip(path: Path) -> bytes:
    """一个很小的假发行包（prepare 认得的样子：都在 AstralChipCompass 下，有 app\\ACC.exe 和根目录的启动程序），返回它的字节。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    top = paths.FOLDER_NAME
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{top}/{br.LAUNCHER_EXE}", b"launcher")
        zf.writestr(f"{top}/{br.MANUAL_NAME}", "<p>使用说明</p>".encode("utf-8"))
        zf.writestr(f"{top}/{br.APP_SUB}/{br.EXE}", b"program")
        zf.writestr(f"{top}/{br.APP_SUB}/data/meta.json", b"{}")
    return path.read_bytes()


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def move(source: Path, target: Path) -> None:
    """改名（文件、文件夹）；刚写的文件被杀毒软件扫着、改不了名时稍等再试（同 advisor.update）。"""
    update._retrying(lambda: os.replace(source, target))


class FakeApply:
    """假的换版本程序：subprocess.Popen 的替身（rehearse 只用 pid、returncode、poll、kill、wait）。起它时记下命令行、工作
    目录、环境变量和那时文件夹里的样子（test.started）。之后每 poll 一次往下走一步，按剧本（test.script）：
    1. 写「没做完」的结果，触发「已经接手」的事件；
    2. 换文件夹：app 改名成 app.old、复制 update\\new 里的 app、把用户数据（logs）搬过去、换根目录的两个文件；写 ok 的结果，
       起新版本；
    3. 记下「新版本的悬浮窗出来了」「换好了」，退出码 0。
    剧本不同的地方写在各步里；一直不结束的（hang）只做第 1 步。kill 以后退出码 1。"""

    def __init__(self, test: RehearseTest, cmd, cwd=None, env=None):
        self.test, self.script = test, test.script
        self.cmd, self.cwd, self.env = list(cmd), cwd, dict(env or {})
        self.pid, self.returncode, self.steps, self.killed = APPLY_PID, None, 0, False
        exe = Path(cmd[0])
        self.root = exe.parents[3]  # <根>\update\new\app\ACC.exe
        self.app, self.old = self.root / br.APP_SUB, self.root / update_apply.APP_OLD
        self.new = self.root / update_apply.UPDATE_DIR / update_apply.NEW_DIR
        self.log = update_apply._Log(self.root, "换版本程序")
        test.apply = self
        test.started = self.look()
        test.procs[self.pid] = (os.getpid(), str(exe))

    def look(self) -> dict:
        """起它的时候文件夹里的样子：update 里备好的（ready.json 用换版本程序自己的读法）、两个记号文件、演练文件夹里有什么。"""
        return {"cmd": self.cmd, "cwd": self.cwd, "env": self.env, "ready": update_apply._read_ready(self.root),
                "new_files": sorted(p.relative_to(self.new).as_posix() for p in self.new.rglob("*") if p.is_file()),
                "mark": read(self.app / "logs" / br.REHEARSE_MARK), "old_mark": read(self.app / br.REHEARSE_OLD_MARK),
                "folder": sorted(p.name for p in self.root.parent.iterdir()), "zip_there": self.test.zip.is_file()}

    def poll(self):
        if self.returncode is None:
            self.steps += 1
            if self.steps == 1:
                self.begin()
            elif self.script != "hang":
                (self.middle if self.steps == 2 else self.end)()
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.exit(1)

    def wait(self, timeout=None):
        return self.returncode

    def exit(self, code: int) -> None:
        self.returncode = code
        self.test.procs.pop(self.pid, None)

    def write(self, status: str, reason: str, done: bool = True) -> None:
        """写结果（update_apply._write_result，格式同换版本程序），写一行记录。"""
        record = {"format": update_apply.FORMAT, "status": status, "version": br.VERSION, "from": br.VERSION,
                  "reason": reason, "time": update_apply._now(), "pid": self.pid,
                  "attempts": 1 if status == update_apply.NOT_APPLIED else 0, "done": done}
        update_apply._write_result(self.root, record)
        if done:
            self.log(f"结果：{status}" + (f"（{reason}）" if reason else ""))

    def begin(self) -> None:
        self.log(f"开始：{br.VERSION} 换成 {br.VERSION}，旧版本的进程号 {self.cmd[-1]}")
        if self.script == "refused":
            self.log("核对没有通过，什么都不改：没有「已备好」记号")
            self.exit(update_apply.EXIT_REFUSED)
            return
        self.write(update_apply.NOT_APPLIED, update_apply.REASON_INTERRUPTED, done=False)
        if self.script != "silent":
            self.test.signalled = True
        self.log("旧版本已经退出")

    def middle(self) -> None:
        if self.script == "crashed":
            self.exit(1)
            return
        if self.script == "not_applied":
            self.write(update_apply.NOT_APPLIED, update_apply.REASON_LOCKED)
            self.start_old()
            self.exit(0)
            return
        if self.script != "no_swap":
            self.swap()
        self.write(update_apply.OK, "")
        self.test.procs[NEW_PID] = (self.pid, str(self.app / br.EXE))
        self.test.titles[NEW_PID] = FAILED_TITLE if self.script == "failed_window" else TITLE
        self.log(f"起了新版本（进程号 {NEW_PID}），等悬浮窗出来，最多 {update_apply.READY_WAIT_S:g} 秒")

    def end(self) -> None:
        if self.script == "rolled_back":
            self.test.procs.pop(NEW_PID)
            self.log("新版本在悬浮窗出来之前就结束了（退出码 1）")
            self.rollback()
            self.exit(0)
            return
        if self.script == "fallback":
            self.log(f"等满 {update_apply.READY_WAIT_S:g} 秒新版本还在运行，算换好了")
        else:
            self.log("新版本的悬浮窗出来了")
        self.log("换好了")
        if self.script == "gone":
            self.test.procs.pop(NEW_PID)
        self.exit(0)
        if self.script == "cleaned":
            self.after_start()

    def swap(self) -> None:
        move(self.app, self.old)
        shutil.copytree(self.new / br.APP_SUB, self.app)
        if self.script != "no_move":
            move(self.old / "logs", self.app / "logs")
        mark = self.app / "logs" / br.REHEARSE_MARK
        if self.script == "lost_mark":
            mark.unlink()
        elif self.script == "changed_mark":
            mark.write_text("changed", encoding="utf-8")
        for name in update_apply.ROOT_FILES:
            move(self.root / name, self.root / f"{name}{update_apply.OLD_SUFFIX}")
            if not (self.script == "no_manual" and name == br.MANUAL_NAME):
                shutil.copy2(self.new / name, self.root / name)
        self.log(f"改好了名：{update_apply.APP} → {update_apply.APP_OLD}，{update_apply.APP_NEXT} → {update_apply.APP}")

    def rollback(self) -> None:
        failed = self.root / update_apply.APP_FAILED
        move(self.app, failed)
        move(self.old, self.app)
        move(failed / "logs", self.app / "logs")
        for name in update_apply.ROOT_FILES:
            move(self.root / f"{name}{update_apply.OLD_SUFFIX}", self.root / name)
        self.write(update_apply.ROLLED_BACK, ROLLBACK_REASON)
        self.start_old()

    def start_old(self) -> None:
        """经根目录的启动程序起旧版本（不等它）：启动程序和它带起的 app\\ACC.exe 都在运行。"""
        self.test.procs[LAUNCHER_PID] = (self.pid, str(self.root / br.LAUNCHER_EXE))
        self.test.procs[OLD_APP_PID] = (LAUNCHER_PID, str(self.app / br.EXE))

    def after_start(self) -> None:
        """换上的新版本在换版本程序退出以后收尾：记录接到 app\\logs\\update.log 后面（update_apply._keep_log），删掉 app.old、
        根目录的 .old 和 update。"""
        update_apply._keep_log(self.root)
        shutil.rmtree(self.old)
        for name in update_apply.ROOT_FILES:
            (self.root / f"{name}{update_apply.OLD_SUFFIX}").unlink()
        shutil.rmtree(self.root / update_apply.UPDATE_DIR)


class RehearseTest(unittest.TestCase):
    """演练换版本整个走一遍（rehearse），换版本程序、进程表、事件都是假的（见模块说明）。"""

    def setUp(self):
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.smoke = self.base / "release-test"
        self.folder = self.smoke / br.REHEARSE_SUBDIR
        self.root = self.folder / paths.FOLDER_NAME
        self.zip = self.base / "dist" / br.ZIP.name
        self.zip_bytes = release_zip(self.zip)
        # 试启动文件夹里别的东西（试启动解出来的发行版、个人目录、名字相近的）：演练文件夹以外的都不能动
        self.keep = {self.smoke / paths.FOLDER_NAME / br.APP_SUB / br.EXE: b"smoke",
                     self.smoke / "appdata" / "Roaming" / paths.FOLDER_NAME / paths.STATE_NAME: b"{}",
                     self.smoke / f"{br.REHEARSE_SUBDIR}-other" / "keep.txt": b"keep",
                     self.smoke / f"{br.REHEARSE_SUBDIR}.txt": b"keep"}
        for path, data in self.keep.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # 别处的拾星助手：发行版文件夹里的、试启动的、文件夹名字只差几个字的（演练一个都不碰）
        self.others = [(9001, 1, str(br.APP_DIR / br.APP_SUB / br.EXE)), (9002, 1, str(br.APP_DIR / br.LAUNCHER_EXE)),
                       (9003, 1, str(self.smoke / paths.FOLDER_NAME / br.APP_SUB / br.EXE)),
                       (9004, 1, str(self.smoke / f"{br.REHEARSE_SUBDIR}-other" / br.APP_SUB / br.EXE)),
                       (9005, 1, str(self.smoke / f"{br.REHEARSE_SUBDIR}.old-1" / br.APP_SUB / br.EXE))]
        self.smoke_env = {"APPDATA": str(self.smoke / "appdata" / "Roaming"),
                          "LOCALAPPDATA": str(self.smoke / "appdata" / "Local"),
                          "HTTP_PROXY": br.BLOCKED_PROXY, "HTTPS_PROXY": br.BLOCKED_PROXY}
        self.reset()
        for patch in (
            mock.patch.object(br, "SMOKE_DIR", self.smoke),
            mock.patch.object(br, "SMOKE_APPDATA", self.smoke / "appdata"),
            mock.patch.object(br, "ZIP", self.zip),
            mock.patch.object(br, "game_running", return_value=False),
            mock.patch.object(br, "overlay_running", return_value=False),
            mock.patch.object(br, "smoke_env", side_effect=lambda: dict(self.smoke_env)),
            mock.patch.object(br, "_exited_process", return_value=(OLD_PID, OLD_HANDLE)),
            mock.patch.object(br, "_new_event", return_value=(EVENT_HANDLE, EVENT_NAME)),
            mock.patch.object(br, "_event_set", side_effect=lambda handle: handle == EVENT_HANDLE and self.signalled),
            mock.patch.object(br, "_close_handle", side_effect=lambda handle: self.closed.append(handle)),
            mock.patch.object(br, "_all_processes", side_effect=self.table),
            mock.patch.object(br, "_end_process", side_effect=self.end_process),
            mock.patch.object(br, "_visible_window_of", side_effect=lambda pid: self.titles.get(pid)),
            mock.patch.object(br.subprocess, "Popen", side_effect=self.popen),
            mock.patch.object(update, "exe_version", return_value=update._four(br.VERSION)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.enterContext(patch)

    def reset(self) -> None:
        """每次演练之前：假的进程表、记录清空；演练文件夹里放一些上一次留下的东西（演练前要先清空）。"""
        self.script, self.apply, self.started, self.signalled = "ok", None, None, False
        self.procs: dict[int, tuple[int, str]] = {}
        self.titles: dict[int, str] = {}
        self.ended: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self.popen_calls: list[list[str]] = []
        self.stubborn: set[int] = set()
        (self.root / br.APP_SUB).mkdir(parents=True, exist_ok=True)
        (self.folder / "stale.txt").write_text("上一次留下的", encoding="utf-8")

    def table(self) -> list[tuple[int, int, str]]:
        """假的进程快照：演练起的（self.procs）和别处的（self.others）。"""
        return [(pid, parent, image) for pid, (parent, image) in self.procs.items()] + list(self.others)

    def end_process(self, pid: int, image: str) -> bool:
        """假的结束进程：记下结束了谁；stubborn 里的结束不了。"""
        self.ended.append((pid, image))
        if pid in self.stubborn:
            return False
        if self.apply is not None and pid == self.apply.pid:
            self.apply.kill()
        self.procs.pop(pid, None)
        return True

    def popen(self, cmd, cwd=None, env=None, **kw):
        self.popen_calls.append(list(cmd))
        return FakeApply(self, cmd, cwd, env)

    def run_rehearse(self, script: str = "ok", step: float = 0.01) -> tuple[dict, list[str]]:
        """按剧本 script 演练一次：假时钟每读一次走 step 秒，等待只记下来（self.slept）。返回（结果, 问题）。"""
        self.script = script
        ticks = itertools.count()
        self.slept: list[float] = []
        result = br.rehearse(clock=lambda: next(ticks) * step, sleep=self.slept.append)
        json.dumps(result)  # 摘要里写得出
        return result, br.rehearse_problems(result)

    def assert_removed(self) -> None:
        """演练文件夹删掉了（连同删到一半留下的「名字.old-时间」）。"""
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.smoke.glob(f"{br.REHEARSE_SUBDIR}.old-*")), [])

    def assert_untouched(self, handles: bool = True) -> None:
        """演练文件夹以外一样没动：试启动文件夹里别的文件、压缩包；别处的拾星助手一个都没结束，结束的都在演练文件夹里；
        handles 时另核对旧进程的句柄、事件都关了。"""
        for path, data in self.keep.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertEqual(self.zip.read_bytes(), self.zip_bytes)
        others = {pid for pid, _, _ in self.others}
        self.assertEqual([pid for pid, _ in self.ended if pid in others], [])
        self.assertEqual([image for _, image in self.ended if not br._under(image, self.folder)], [])
        if handles:
            self.assertLessEqual({OLD_HANDLE, EVENT_HANDLE}, set(self.closed))

    def test_swap_passes(self):
        """换好了：起换版本程序之前已经清掉上一次留下的、解好「旧版本」、放好两个记号文件、备好 update\\new 和 ready.json（换版本
        程序认得：版本号是 VERSION，SHA-256 是这个压缩包的）；压缩包不在 update 里，没删。命令行和旧版本起它时一样，工作目录是它
        自己的文件夹，环境同试启动、不下载识别图片、带着「已经接手」的事件名。换好以后逐项核对都通过；换上的新版本结束了，演练
        文件夹删掉了，别的一样没动。"""
        result, problems = self.run_rehearse("ok")
        self.assertEqual(problems, [])
        exe = self.root / update_apply.UPDATE_DIR / update_apply.NEW_DIR / br.APP_SUB / br.EXE
        seen = self.started
        self.assertEqual(self.popen_calls, [update_apply._update_command(exe, OLD_PID)])
        self.assertEqual(seen["cwd"], str(exe.parent))
        self.assertEqual(seen["env"], {**self.smoke_env, "CHIP_ADVISOR_NO_DOWNLOAD": "1",
                                       update_apply.STARTED_ENV: EVENT_NAME})
        self.assertEqual((seen["ready"]["version"], seen["ready"]["sha256"]),
                         (br.VERSION, hashlib.sha256(self.zip_bytes).hexdigest()))
        self.assertEqual(seen["new_files"], sorted([br.LAUNCHER_EXE, br.MANUAL_NAME, f"{br.APP_SUB}/{br.EXE}",
                                                    f"{br.APP_SUB}/data/meta.json"]))
        self.assertEqual(seen["folder"], [paths.FOLDER_NAME])  # 上一次留下的清掉了
        self.assertRegex(seen["mark"], r"^[0-9a-f]{16}$")
        self.assertEqual(seen["old_mark"], seen["mark"])
        self.assertTrue(seen["zip_there"])
        for sub in ("Roaming", "Local"):
            self.assertTrue((self.smoke / "appdata" / sub).is_dir())
        want = {"old_pid": OLD_PID, "apply_pid": APPLY_PID, "timeout": False, "exit_code": 0, "result_cleaned": False,
                "log_done": True, "log_shown": True, "started_signal": True, "new_pid": NEW_PID,
                "window_title": TITLE, "app_exe": True, "app_is_new": True, "app_old_kept": True, "marker": "moved",
                "root_files": {name: True for name in br.ROOT_FILES}, "ended": [f"{br.EXE}（进程号 {NEW_PID}）"],
                "left_running": [], "cleanup_error": None}
        self.assertEqual({key: result.get(key) for key in want}, want)
        self.assertEqual((result["result"]["status"], result["result"]["version"]), (update_apply.OK, br.VERSION))
        self.assertTrue(result["log_tail"][-1].endswith("：换好了"))
        self.assertNotIn("error", result)
        self.assertIn("seconds", result)
        self.assert_removed()
        self.assert_untouched()

    def test_swap_passes_after_new_version_cleans_up(self):
        """换上的新版本在换版本程序退出以后马上收了尾（删了 update、app.old，记录接到 app\\logs\\update.log 后面）：结果用等的
        时候读到的那一份，记录读 update.log，照样算换好了。"""
        result, problems = self.run_rehearse("cleaned")
        self.assertEqual(problems, [])
        self.assertTrue(result["result_cleaned"])
        self.assertEqual(result["result"]["status"], update_apply.OK)
        self.assertEqual((result["log_done"], result["log_shown"], result["app_old_kept"]), (True, True, False))
        self.assert_removed()
        self.assert_untouched()

    def test_exit_code_not_zero(self):
        """换版本程序的退出码不是 0：只报这一条（带上它写的结果和原因），后面的不再核对；演练文件夹照样删掉。"""
        cases = (
            ("refused", "换版本程序的退出码是 0xacd1（核对没通过，什么都没改）"),
            ("crashed", "换版本程序的退出码是 1（出错退出，例如导入出错），"
                        f"结果是 {update_apply.NOT_APPLIED}：{update_apply.REASON_INTERRUPTED}"),
        )
        for script, problem in cases:
            with self.subTest(script=script):
                self.reset()
                result, problems = self.run_rehearse(script)
                self.assertEqual(problems, [problem])
                self.assert_removed()
                self.assert_untouched()

    def test_rolled_back_or_not_applied(self):
        """退出码 0，结果却是退回了、没换：只报结果和它写的原因。它起的旧版本（经根目录的启动程序）也结束了：先结束启动程序、
        后结束 app 里的。"""
        cases = (("rolled_back", update_apply.ROLLED_BACK, ROLLBACK_REASON),
                 ("not_applied", update_apply.NOT_APPLIED, update_apply.REASON_LOCKED))
        for script, status, reason in cases:
            with self.subTest(script=script):
                self.reset()
                result, problems = self.run_rehearse(script)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(problems, [f"换版本的结果是 {status}：{reason}"])
                self.assertEqual([pid for pid, _ in self.ended], [LAUNCHER_PID, OLD_APP_PID])
                self.assert_removed()
                self.assert_untouched()

    def test_timeout(self):
        """换版本程序一直不结束：到时间（按注入的时钟，不真的等）就结束它，只报超时这一条；演练文件夹照样删掉。"""
        result, problems = self.run_rehearse("hang", step=5.0)
        self.assertEqual(problems, [f"换版本程序 {br.REHEARSE_TIMEOUT:g} 秒内没有结束（已经结束它）"])
        self.assertTrue(self.apply.killed)
        self.assertEqual((result["timeout"], result["exit_code"]), (True, None))
        self.assertLess(len(self.slept), 100)
        self.assert_removed()
        self.assert_untouched()

    def test_marker_not_moved(self):
        """记号文件（模拟的用户数据）没搬进新的 app\\logs、丢了、内容变了：各报一条。"""
        cases = (
            ("no_move", "left",
             f"用户数据没搬过去：记号文件还在 {update_apply.APP_OLD}\\logs 里，新的 {br.APP_SUB}\\logs 里没有"),
            ("lost_mark", "missing", f"用户数据丢了：「旧版本」{br.APP_SUB}\\logs 里的记号文件哪里都找不到"),
            ("changed_mark", "changed", f"新的 {br.APP_SUB}\\logs 里的记号文件内容变了"),
        )
        for script, marker, problem in cases:
            with self.subTest(script=script):
                self.reset()
                result, problems = self.run_rehearse(script)
                self.assertEqual(result["marker"], marker)
                self.assertEqual(problems, [problem])
                self.assert_removed()
                self.assert_untouched()

    def test_other_checks(self):
        """退出码 0、结果 ok，别的几项不对时各报一条：没触发「已经接手」的事件、记录里没有「新版本的悬浮窗出来了」（等满时间
        算换好了的那种）、app 还是原来那一份、根目录少了使用说明、新版本没在运行、弹了「失败」的窗口。"""
        cases = (
            ("silent", f"换版本程序没有触发「已经接手」的事件（环境变量 {update_apply.STARTED_ENV}）：已经发出去的旧版本起它时"
                       "等不到回话，会当成没换上"),
            ("fallback", "换版本的记录里没有「新版本的悬浮窗出来了」：换上的新版本没在等的时间里显示悬浮窗、送出信号"),
            ("no_swap", f"换好以后 {br.APP_SUB} 还是原来那一份（「旧版本」{br.APP_SUB} 里的记号文件还在），不是新复制的"),
            ("no_manual", f"换好以后根目录没有 {br.MANUAL_NAME}"),
            ("gone", f"换版本程序结束时，换上的新版本（{br.APP_SUB}\\{br.EXE}）没在运行"),
            ("failed_window", f"换上的新版本弹出了「{FAILED_TITLE}」"),
        )
        for script, problem in cases:
            with self.subTest(script=script):
                self.reset()
                _, problems = self.run_rehearse(script)
                self.assertEqual(problems, [problem])
                self.assert_removed()
                self.assert_untouched()

    def test_left_running_keeps_folder(self):
        """演练起的程序结束不了：算没通过，演练文件夹留着不删（里面的程序还开着）。"""
        self.stubborn.add(NEW_PID)
        result, problems = self.run_rehearse("ok", step=1.0)
        label = f"{br.EXE}（进程号 {NEW_PID}）"
        self.assertEqual(result["left_running"], [label])
        self.assertEqual(problems, [f"演练起的程序结束不了：{label}（演练文件夹留着）"])
        self.assertEqual(result["cleanup_error"], "演练起的程序没有都结束：演练文件夹留着")
        self.assertTrue(self.folder.is_dir())
        self.assert_untouched()

    def test_process_table_error_keeps_folder(self):
        """查不了进程表（不知道演练起的程序结束了没有）：报出错的那一条（后面没核对完的不报）和结束不了，演练文件夹留着。"""
        with mock.patch.object(br, "_all_processes", side_effect=OSError("查不了")):
            result, problems = self.run_rehearse("ok")
        self.assertEqual(result["left_running"], ["（没查完）"])
        self.assertEqual(problems, ["演练换版本时出错：OSError: 查不了", "演练起的程序结束不了：（没查完）（演练文件夹留着）"])
        self.assertTrue(self.folder.is_dir())
        self.assert_untouched()

    def test_skipped_while_game_or_overlay_runs(self):
        """游戏开着、拾星助手正在运行时不演练：算没通过，什么都不起、不动（演练文件夹也不清空）。"""
        for name, words in (("game_running", "游戏窗口开着"), ("overlay_running", "拾星助手正在运行")):
            with self.subTest(name=name), mock.patch.object(br, name, return_value=True):
                self.reset()
                result, problems = self.run_rehearse("ok")
                self.assertTrue(result["skipped"].startswith(words))
                self.assertEqual(problems, [f"没演练换版本：{result['skipped']}"])
                self.assertEqual(self.popen_calls, [])
                self.assertTrue((self.folder / "stale.txt").is_file())
                self.assert_untouched(handles=False)

    def test_folder_outside_tmp_is_refused(self):
        """演练文件夹不在项目的 .tmp 下面：不清空、不删、不演练，算没通过。"""
        with mock.patch.object(br, "TMP", self.base / "elsewhere"):
            reason = br.remove_rehearsal()
            result, problems = self.run_rehearse("ok")
        self.assertIn("不清空、不删", reason)
        self.assertEqual(result["error"], reason)
        self.assertEqual(problems, [f"演练换版本时出错：{reason}"])
        self.assertEqual(self.popen_calls, [])
        self.assertTrue((self.folder / "stale.txt").is_file())
        self.assert_untouched(handles=False)

    @unittest.skipUnless(ON_WINDOWS, "只在 Windows 上有")
    def test_folder_link_is_refused(self):
        """演练文件夹本身是目录链接（这里指到 .tmp 里另一个文件夹）：不清空、不删、不演练，算没通过；链接指着的那个文件夹原样
        不动（照着链接删的话，删掉的是它）。"""
        import _winapi

        victim = self.base / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_bytes(b"keep")
        shutil.rmtree(self.folder)
        _winapi.CreateJunction(str(victim), str(self.folder))
        self.addCleanup(lambda: os.path.lexists(self.folder) and os.rmdir(self.folder))  # 只删链接本身
        reason = br.remove_rehearsal()
        result, problems = self.run_rehearse("ok")
        self.assertEqual((victim / "keep.txt").read_bytes(), b"keep")
        self.assertTrue(os.path.isjunction(self.folder))
        self.assertIn("是链接", reason or "")
        self.assertEqual(result["error"], reason)
        self.assertEqual(problems, [f"演练换版本时出错：{reason}"])
        self.assertEqual(self.popen_calls, [])
        self.assert_untouched(handles=False)

    def test_prepare_error(self):
        """备不好新版本（这里是版本号对不上）：不起换版本程序，报 prepare 说的原因，演练文件夹删掉。"""
        with mock.patch.object(update, "exe_version", return_value=(9, 9, 9, 0)):
            result, problems = self.run_rehearse("ok")
        self.assertIn("9.9.9.0", result["prepare_error"])
        self.assertEqual(problems, [f"备好新版本时出错（advisor.update.prepare）：{result['prepare_error']}"])
        self.assertEqual(self.popen_calls, [])
        self.assert_removed()
        self.assert_untouched(handles=False)

    def test_apply_program_cannot_start(self):
        """换版本程序起不来：报出错，照样收拾（关句柄、删演练文件夹）。"""
        with mock.patch.object(br.subprocess, "Popen", side_effect=FileNotFoundError(2, "找不到程序")):
            result, problems = self.run_rehearse("ok")
        self.assertEqual(problems, ["演练换版本时出错：FileNotFoundError: [Errno 2] 找不到程序"])
        self.assertNotIn("apply_pid", result)
        self.assert_removed()
        self.assert_untouched()

    def test_remove_only_rehearsal_folder(self):
        """删演练文件夹只删它和它删到一半留下的「名字.old-时间」：试启动文件夹里别的（包括名字相近的）都留着。"""
        trash = self.smoke / f"{br.REHEARSE_SUBDIR}.old-{time.strftime('%Y%m%d-%H%M%S')}-9"
        (trash / "x").mkdir(parents=True)
        self.assertIsNone(br.remove_rehearsal(clock=itertools.count().__next__, sleep=lambda s: None))
        self.assertFalse(trash.exists())
        self.assert_removed()
        self.assert_untouched(handles=False)
        self.assertIsNone(br.remove_rehearsal())  # 本来就没有

    def test_remove_retries_then_gives_up(self):
        """删不掉（文件还被拿着）隔 0.5 秒再试，删掉了就停；一直删不掉，到时间返回原因。"""
        busy = RuntimeError("无法移走（可能里面的程序正在运行）")
        ticks = itertools.count()
        slept: list[float] = []
        with mock.patch.object(br, "_safe_rmtree", side_effect=[busy, busy, None]):
            self.assertIsNone(br.remove_rehearsal(clock=lambda: next(ticks), sleep=slept.append))
        self.assertEqual(slept, [0.5, 0.5])
        with mock.patch.object(br, "_safe_rmtree", side_effect=busy):
            reason = br.remove_rehearsal(seconds=3, clock=lambda: next(ticks), sleep=slept.append)
        self.assertEqual(reason, f"RuntimeError: {busy}")


class EndProcessesTest(unittest.TestCase):
    """结束演练起的程序（end_rehearsal_processes、_end_process）：只认程序文件在演练文件夹里的，先后有讲究，动手前再核对。"""

    def setUp(self):
        self._dir = tmpdir()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.folder = self.base / br.REHEARSE_SUBDIR
        root = self.folder / paths.FOLDER_NAME
        self.apply_exe = str(root / update_apply.UPDATE_DIR / update_apply.NEW_DIR / br.APP_SUB / br.EXE)
        self.launcher = str(root / br.LAUNCHER_EXE)
        self.app_exe = str(root / br.APP_SUB / br.EXE)
        self.outside = [(21, 1, str(self.base / paths.FOLDER_NAME / br.APP_SUB / br.EXE)),
                        (22, 1, str(self.base / f"{br.REHEARSE_SUBDIR}-other" / br.APP_SUB / br.EXE)),
                        (23, 1, str(self.base / f"{br.REHEARSE_SUBDIR}.old-1" / br.APP_SUB / br.EXE))]

    def test_end_order_and_new_children(self):
        """先结束换版本程序，再结束根目录的启动程序，最后是 app 里的；结束时刚起来的接着结束；别处的一个都不碰。结束不了的
        到时间算结束不了。"""
        table = {11: (1, self.app_exe), 12: (1, self.launcher), 13: (1, self.apply_exe)}
        ended: list[int] = []
        stubborn: set[int] = set()

        def end(pid, image):
            ended.append(pid)
            if pid in stubborn:
                return False
            table.pop(pid)
            if pid == 13:  # 换版本程序结束前刚起了一个
                table[14] = (13, self.app_exe)
            return True

        def listing():
            return [(pid, parent, image) for pid, (parent, image) in table.items()] + self.outside

        def run():
            with mock.patch.object(br, "_all_processes", side_effect=listing), \
                    mock.patch.object(br, "_end_process", side_effect=end):
                return br.end_rehearsal_processes(self.folder, clock=itertools.count().__next__, sleep=lambda s: None)

        done, left = run()
        self.assertEqual(ended, [13, 12, 11, 14])
        self.assertEqual(done, [f"{br.EXE}（进程号 13）", f"{br.LAUNCHER_EXE}（进程号 12）", f"{br.EXE}（进程号 11）",
                                f"{br.EXE}（进程号 14）"])
        self.assertEqual(left, [])
        table.update({11: (1, self.app_exe), 12: (1, self.launcher)})
        stubborn.add(11)
        ended.clear()
        done, left = run()
        self.assertEqual(done, [f"{br.LAUNCHER_EXE}（进程号 12）"])
        self.assertEqual(left, [f"{br.EXE}（进程号 11）"])
        self.assertEqual(set(ended), {11, 12})
        self.assertLessEqual(len(ended), 30)  # 按注入的时钟到时间就停

    def test_under(self):
        """认演练文件夹里的程序按完整路径、不分大小写；名字只差几个字的文件夹、演练文件夹本身、上一层都不算。"""
        inside = self.folder / paths.FOLDER_NAME / br.APP_SUB / br.EXE
        self.assertTrue(br._under(inside, self.folder))
        self.assertTrue(br._under(str(inside).upper(), self.folder))
        for path in (self.base / f"{br.REHEARSE_SUBDIR}-other" / br.EXE,
                     self.base / f"{br.REHEARSE_SUBDIR}.old-1" / br.EXE, self.folder, self.base / br.EXE):
            with self.subTest(path=path):
                self.assertFalse(br._under(path, self.folder))

    def test_end_process_checks_image_first(self):
        """结束之前再核对一遍程序文件：还是查到的那一个才结束；进程号换了主人、取不到程序文件时不碰（算原来那个已经不在了）；
        打开时说没有这个进程算结束了，拒绝访问算结束不了。用假的 kernel32，不碰真的进程。"""
        other = str(self.base / "other" / br.EXE)
        cases = (  # (OpenProcess 失败时的错误代码, 取到的程序文件, 返回, 结束了没有)
            (0, self.app_exe, True, True),
            (0, self.app_exe.upper(), True, True),
            (0, other, True, False),
            (0, "", True, False),
            (br.ERROR_INVALID_PARAMETER, None, True, False),
            (5, None, False, False),
        )
        for error, image, ok, killed in cases:
            with self.subTest(error=error, image=image):
                kernel = FakeKernel(error)
                with mock.patch.object(br, "_kernel32", return_value=kernel), \
                        mock.patch.object(br, "_image_of", return_value=image):
                    self.assertIs(br._end_process(1234, self.app_exe), ok)
                self.assertEqual(kernel.terminated, [kernel.handle] if killed else [])
                self.assertEqual(kernel.closed, [] if error else [kernel.handle])


class FakeKernel:
    """_end_process 用到的几个 kernel32 函数的替身：OpenProcess 按给的错误代码失败，或者给一个假句柄；TerminateProcess 只记下来。"""

    handle = 777

    def __init__(self, open_error: int = 0):
        self.open_error, self.terminated, self.closed, self.dead = open_error, [], [], False

    def OpenProcess(self, access, inherit, pid):  # noqa: N802  同 kernel32 的函数名
        if self.open_error:
            ctypes.set_last_error(self.open_error)
            return None
        return self.handle

    def WaitForSingleObject(self, handle, ms):  # noqa: N802
        return 0 if self.dead else 0x102  # WAIT_TIMEOUT：还在运行

    def GetExitCodeProcess(self, handle, code):  # noqa: N802
        code._obj.value = 1
        return True

    def TerminateProcess(self, handle, code):  # noqa: N802
        self.terminated.append(handle)
        self.dead = True
        return True

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return True


@unittest.skipUnless(ON_WINDOWS, "只在 Windows 上有")
class WindowsTest(unittest.TestCase):
    """直接用 Windows 的函数核对演练和换版本程序之间的两处约定（只起一个马上就退出的 Python 进程）。"""

    def test_old_pid_is_seen_as_exited(self):
        """交给换版本程序的旧进程号（_exited_process）：进程已经退出，句柄留着；换版本程序打开它（update_apply 等旧进程的
        做法）马上看到它已经退出；演练查进程表时不列已经退出的。"""
        pid, handle = br._exited_process()
        try:
            self.assertTrue(handle)
            self.assertEqual(br._kernel32().WaitForSingleObject(handle, 0), 0)
            opened, gone = update_apply._open_process(pid)
            try:
                self.assertTrue(opened or gone)
                if opened:
                    self.assertTrue(update_apply._wait_process(opened, 0))
            finally:
                update_apply._close(opened)
            table = br._all_processes()
            self.assertNotIn(pid, [p for p, _, _ in table])
            self.assertNotIn(os.getpid(), [p for p, _, _ in table])  # 本进程不列
            parent = [image for p, _, image in table if p == os.getppid()]  # 父进程（同一个用户起的，打得开）
            self.assertTrue(parent and Path(parent[0]).is_file(), parent)
        finally:
            br._close_handle(handle)

    def test_started_event_reaches_rehearsal(self):
        """演练建的「已经接手」的事件，换版本程序（update_apply._signal_started，按环境变量里的名字）触发得了；它把环境变量去掉。"""
        handle, name = br._new_event("RehearseStarted")
        try:
            self.assertFalse(br._event_set(handle))
            with mock.patch.dict(os.environ, {update_apply.STARTED_ENV: name}):
                update_apply._signal_started()
                self.assertNotIn(update_apply.STARTED_ENV, os.environ)
            self.assertTrue(br._event_set(handle))
        finally:
            br._close_handle(handle)


class ContractTest(unittest.TestCase):
    """整句对上的认法（演练认的两句取自换版本程序写记录用的常量 update_apply.LOG_DONE、LOG_SHOWN），以及几处小函数。"""

    def test_logged_needs_whole_phrase(self):
        """记录里要有正好是这一句的一行（用换版本程序自己的写法 update_apply._Log 写）：「……算换好了」、多行记录里缩进的
        后几行都不算。"""
        with tmpdir() as d:
            root = Path(d)
            (root / update_apply.UPDATE_DIR).mkdir()
            write = update_apply._Log(root, "换版本程序")
            path = root / update_apply.UPDATE_DIR / update_apply.LOG_NAME

            def lines():
                return path.read_text(encoding="utf-8").splitlines()

            write(f"等满 {update_apply.READY_WAIT_S:g} 秒新版本还在运行，算换好了")
            write("出错：示例\n换好了")
            self.assertFalse(br._logged(lines(), br.REHEARSE_DONE))
            write("换好了")
            self.assertTrue(br._logged(lines(), br.REHEARSE_DONE))
            self.assertFalse(br._logged(lines(), br.REHEARSE_SHOWN))

    def test_apply_exit_text(self):
        cases = ((None, "取不到"), (0, "0"), (1, "1（出错退出，例如导入出错）"),
                 (update_apply.EXIT_REFUSED, "0xacd1（核对没通过，什么都没改）"),
                 (update_apply.EXIT_BUSY, "0xacd2（另一个换版本程序正在换这个文件夹）"),
                 (update_apply.EXIT_FAILED, "0xacd3（连「没做完」的结果都写不进）"), (0xC0000005, "0xc0000005"))
        for code, text in cases:
            with self.subTest(code=code):
                self.assertEqual(br._apply_exit_text(code), text)

    def test_apply_rehearsal_adds_verdict(self):
        """apply_rehearsal：rehearse 看到的，加上问题和结论。"""
        with mock.patch.object(br, "rehearse", return_value={"folder": "x", "skipped": "游戏窗口开着"}):
            out = br.apply_rehearsal()
        self.assertEqual((out["folder"], out["passed"], out["problems"]), ("x", False, ["没演练换版本：游戏窗口开着"]))

    def test_rehearsal_step(self):
        """main 里的那一步：加了 --no-rehearse、试启动没通过时不演练（摘要里写明）；演练没通过时记进没通过的几项。"""
        passed = {"passed": True, "problems": []}
        cases = (  # (加没加 --no-rehearse, 之前没通过的, 演练结果, 演练了没有, 之后没通过的, 摘要里的 skipped)
            (True, [], passed, False, [], f"加了 {br.NO_REHEARSE_ARG}，没有演练换版本"),
            (False, [br.SMOKE_CHECK], passed, False, [br.SMOKE_CHECK], "试启动没通过，没有演练换版本"),
            (False, [], {"passed": False, "problems": ["演练没过"]}, True, [br.REHEARSE_CHECK], None),
            (False, [], passed, True, [], None),
        )
        for skip, failed, record, rehearsed, after, skipped in cases:
            with self.subTest(skip=skip, failed=failed, record=record):
                summary: dict = {}
                failed = list(failed)
                with mock.patch.object(br, "apply_rehearsal", return_value=record) as fake, \
                        contextlib.redirect_stdout(io.StringIO()):
                    br.rehearsal_step(summary, failed, skip)
                self.assertEqual(fake.called, rehearsed)
                self.assertEqual(failed, after)
                self.assertEqual(summary["apply_rehearsal"], record if rehearsed else {"skipped": skipped})


if __name__ == "__main__":
    unittest.main()
