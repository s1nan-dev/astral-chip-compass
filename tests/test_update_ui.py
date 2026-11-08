"""软件内更新在界面这一边：控制模块（advisor/ui/overlay_parts/updates.py 的 UpdateControl）、悬浮窗上的提示行
（notices.py 的 update_rows）、设置窗口里「启动时自动检查更新」那一行，以及启动时交给换版本的程序（advisor/ui/__init__.py
的 hand_over_update）。

- 设置文件里的两项（自动检查的开关、跳过的版本）：默认值、旧设置文件没有这两项、存了再读、类型不对；旧设置文件里上次
  检查的日期（update_checked）读的时候忽略、保存以后消失；
- 文字逐字核对；各种状态排出的提示行（键、文字、级别、按钮和动作）；
- 控制模块：开发版不查、不下、不换（「检查更新」只说一句）；每次启动都自动检查、带上跳过的版本、查不到不出声；手动检查
  （跳过的版本照样提示；已经是最新、连不上、程序出错各说一句）；跳过、以后再说；对局中攒着；下载（update.obtain）的几步：
  比对本机的文件、只下变了的部分（要下载的是 0 时写用本机的文件拼出新版本）、下整包、只下变了的部分没走通改下整包（进度
  从 0 重新数）、解压核对，进度每多 1 MB 交回一次，停止、下好了；下载前的检查给了原因、下载和核对出错、程序出错；
  「重启更新」先起换版本的程序、起来了才退出，起不来提示原因（同一个版本没换上的次数到了上限时换一种说法、记进跳过）；
  启动时读上一次换版本的结果（换上了说一句，结果里不是正在运行的版本时不说；已退回的、没换上的次数到了上限的记进跳过）、
  收尾放到后台、收尾没做完时下载先等它（等的上限比收尾等换版本程序的长）；只打开发布页面和下载镜像下的地址（判法和
  advisor/update.py 的一样），「手动下载」按认下的这一版有没有下载镜像上的地址开压缩包或发布页面；这个版本不用下载镜像
  （复刻的改版）时「手动下载」一律开发布页面、镜像上的地址一概不开，源代码地址推不出查更新的地址时手动检查说一句；
  退出时叫停下载；
- 启动（run）：发行版里有下好的新版本时交给换版本的程序、本进程直接返回；没有、起不来、出错时照常启动；开发版不查；
- 悬浮窗：按钮接到控制模块上，按钮多的排在文字下面，对局中不显示，下好以后自动重启看各张悬浮窗在不在对局中（两张时另一张
  在对局中也不重启），放在标题栏下方、排在「免费软件」声明前面；设置窗口的开关和「检查更新」；退出时下载没停下记一笔；
  main() 里悬浮窗出来以后才读结果、排自动检查；
- 设置窗口：七套皮肤都有这一行（开关和「检查更新」在这一行里、看得见，分页的在「关于」页），检查的结果代替说明显示，
  忙的时候「检查更新」不能点。

查和下载（advisor/update.py）、换版本（advisor/update_apply.py）一律换成假的（对外的名字和参数照真的），不联网、不起
进程、不碰程序文件夹。用 Qt 的 offscreen 平台，不会弹出窗口；设置文件写在测试沙箱的临时文件夹里。
"""
import inspect
import json
import os
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QLabel, QStackedWidget  # noqa: E402

import advisor  # noqa: E402
import advisor.ui as ui  # noqa: E402
from advisor import paths as app_paths  # noqa: E402
from advisor import update as real_update  # noqa: E402  只拿常量和判地址的规则来核对，不联网
from advisor import update_apply as real_apply  # noqa: E402  只拿常量来核对
from advisor import version as app_version  # noqa: E402
from advisor import winhttp  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.session import GAME_FIELDS  # noqa: E402
from advisor.ui import chrome, glyphs, lang, startup, theme  # noqa: E402
from advisor.ui import overlay as overlay_module  # noqa: E402
from advisor.ui import settings as st  # noqa: E402
from advisor.ui import single_instance as si  # noqa: E402
from advisor.ui.overlay import Overlay  # noqa: E402
from advisor.ui.overlay_parts import app as overlay_app  # noqa: E402
from advisor.ui.overlay_parts import lanes as overlay_lanes  # noqa: E402
from advisor.ui.overlay_parts import updates  # noqa: E402
from advisor.ui.overlay_parts import wording as W  # noqa: E402
from advisor.ui.overlay_parts.app import OverlaySession, SimpleCapture, UiSettings  # noqa: E402
from advisor.ui.overlay_parts.notices import STACKED_NOTICES, update_rows  # noqa: E402
from advisor.ui.overlay_parts.updates import UpdateControl, UpdateNotice  # noqa: E402
from advisor.ui.services import Services  # noqa: E402
from tests.support import qtenv, sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.widgets import dispose, settle  # noqa: E402

APP = qtenv.app()
VERSION = app_version.VERSION
NEW = "99.0.0"  # 测试里的新版本：比现在的版本号大得多，改了版本号照样成立
MB = W.MB
MIRROR = "https://dl.astral-chip-compass.com/"  # 下载镜像（advisor/update.py 的 MIRROR_BASE）
PAGE = f"{app_version.SOURCE_URL}/releases/tag/v{NEW}"
NOTES = f"{MIRROR}notes/{NEW}.txt"
SIZE = 89_000_000  # 压缩包的大小：约 85 MB


# ------------------------------------------------------------ 查和下载、换版本的替身


class UpdateError(Exception):
    """同 advisor/update.py 的 UpdateError：str(e) 是给用户看的白话原因。"""


@dataclass(frozen=True)
class Release:
    """同 advisor/update.py 的 Release（默认没有文件清单，只能下整包）。"""

    version: str = NEW
    sha256: str = "0" * 64
    size: int = SIZE
    urls: tuple = (f"{MIRROR}releases/ACC-{NEW}.zip",)
    page: str = PAGE
    notes: str | None = NOTES
    data_date: str | None = "2030-01-15"
    auto: bool = True
    files_sha256: str | None = None
    files_size: int | None = None
    files_urls: tuple = ()


@dataclass(frozen=True)
class CheckResult:
    """同 advisor/update.py 的 CheckResult。默认是两边都拿到了、已经是最新。"""

    release: Release | None = None
    latest: str | None = VERSION
    reachable: bool = True
    mirror_ok: bool = True
    problem: str | None = None


REL = Release()
GITHUB_ZIP = f"{app_version.SOURCE_URL}/releases/download/v{NEW}/ACC-{NEW}.zip"  # GitHub 上的附件（只认 GitHub 那一版时）
# 有文件清单的一版：点「更新」以后先比对本机的文件、只下变了的部分
DELTA = Release(files_sha256="1" * 64, files_size=4096, files_urls=(f"{MIRROR}releases/ACC-{NEW}.files.json",))
PART = 12 * MB + 345  # 只下变了的部分时要下载的压缩数据（约 12 MB）


class FakeNet:
    """advisor/update.py 的替身：记下调用，按测试给的结果回。

    obtain 按 steps 依次做（每做一步调用 on_step(这一步)）：("phase", 步, 字节数) 经 phase 告诉做到哪一步，("progress", 已下载)
    按上一步的字节数报进度（再调用 on_chunk），("error", 错) 抛出。steps 为 None 时照没有文件清单的样子下整包：("full", 压缩包
    的大小)、chunks 依次报进度、download_error（有的话）、("prepare", None)、prepare_error（有的话）。照真的一样每一步之前见到
    停止标志就抛 winhttp.Stopped（honor_stop 为假时不看）；做完了照常返回。不再分开调 download、prepare（没有这两个方法：界面
    调了就报错）。"""

    MIRROR_BASE = MIRROR
    LATEST_ZIP = MIRROR + "ACC-latest.zip"
    USE_MIRROR = True  # 官方版：源代码地址就是下载镜像上那个仓库（复刻的改版为假）
    UpdateError = UpdateError

    def __init__(self):
        self.calls = []
        self.manual = None
        self.result = CheckResult()
        self.check_error = None
        self.reason = None  # preflight 给的原因
        self.preflight_error = None
        self.steps = None
        self.on_step = None
        self.chunks = ()
        self.on_chunk = None
        self.honor_stop = True
        self.download_error = None
        self.prepare_error = None

    def check(self, current, skipped=None, *, session=None, manual=False):
        self.calls.append(("check", current, skipped))
        self.manual = manual
        if self.check_error is not None:
            raise self.check_error
        return self.result

    def preflight(self, root):
        self.calls.append(("preflight", root))
        if self.preflight_error is not None:
            raise self.preflight_error
        return self.reason

    def full_steps(self, release) -> list:
        """没有文件清单时 obtain 做的几步（下整包）。"""
        steps = [("phase", "full", release.size), *(("progress", got) for got in self.chunks)]
        if self.download_error is not None:
            steps.append(("error", self.download_error))
        steps.append(("phase", "prepare", None))
        if self.prepare_error is not None:
            steps.append(("error", self.prepare_error))
        return steps

    def obtain(self, release, root, *, progress=None, stop=None, session=None, phase=None):
        self.calls.append(("obtain", release.version, root))
        total = 0
        for step in (self.steps if self.steps is not None else self.full_steps(release)):
            if self.honor_stop and stop is not None and stop.is_set():
                raise winhttp.Stopped()
            if step[0] == "phase":
                _, kind, total = step
                if phase is not None:
                    phase(kind, total)
            elif step[0] == "progress":
                if progress is not None:
                    progress(step[1], total or 0)
                if self.on_chunk is not None:
                    self.on_chunk(step[1])
            else:
                raise step[1]
            if self.on_step is not None:
                self.on_step(step)


class FakeApply:
    """advisor/update_apply.py 的替身：记下调用（起换版本的程序时另记进 log，核对和退出的先后），按测试给的结果回；
    上一次的结果同真的一样只交一次。"""

    MAX_FAILED = 2  # 同真的（ResultTest 核对）

    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.calls = []
        self.ready = None  # pending 给的（下好的新版本）
        self.pending_error = None
        self.launched = True
        self.launch_error = None
        self.result = None  # take_result 给的
        self.result_error = None
        self.after_error = None

    def pending(self, root, current):
        self.calls.append(("pending", root, current))
        if self.pending_error is not None:
            raise self.pending_error
        return self.ready

    def launch(self, root, *, old_pid):
        self.calls.append(("launch", root, old_pid))
        self.log.append("launch")
        if self.launch_error is not None:
            raise self.launch_error
        return self.launched

    def take_result(self, root):
        self.calls.append(("take_result", root))
        if self.result_error is not None:
            raise self.result_error
        result, self.result = self.result, None
        return result

    def after_start(self, root):
        self.calls.append(("after_start", root))
        if self.after_error is not None:
            raise self.after_error


class Harness:
    """一个控制模块和它的替身：后台跑的直接调用（defer 为真时先排着，drain 再跑），过一会儿再做的记下来（fire 到点），
    刷新时记下悬浮窗上那时有哪几条提示。改设置、存设置要在锁里，记识别记录时不能拿着锁（悬浮窗记的时候要再拿一次锁）。"""

    def __init__(self, test: unittest.TestCase, *, frozen: bool = True, defer: bool = False, settings=None):
        self.test = test
        self.root = sandbox.temp_dir(test, "test-update-ui-")  # 程序文件夹（替身不往里写）
        self.settings = settings if settings is not None else UiSettings()
        self.lock = threading.Lock()
        self.session = SimpleNamespace(settings=self.settings, save=self._save)
        self.saved = []  # 每次存设置时的两项
        self.in_game = False  # 悬浮窗那一边说的对局中没有（UpdateControl 的 playing）
        self.playing_error = None  # 问对局中没有时抛的错
        self.windows = False  # 开着拾星助手的其他窗口没有（UpdateControl 的 windows_open）
        self.windows_error = None  # 问的时候抛的错
        self.hidden = ()  # 收在托盘里的几张（UpdateControl 的 hidden_lanes）
        self.defer = defer
        self.queue = []
        self.timers = []
        self.log = []  # 刷新（refresh）、更新设置窗口（settings）、起换版本的程序（launch）、退出（quit）的先后
        self.shown = []  # 每次刷新时悬浮窗上的几条（种类）
        self.traces = []
        self.opened = []
        self.open_ok = True
        self.net = FakeNet()
        self.apply = FakeApply(self.log)
        self.ctl = UpdateControl(
            session=self.session, lock=self.lock, run_background=self._run, later=self._later, refresh=self._refresh,
            update_settings=lambda: self.log.append("settings"), trace_user=self._trace,
            quit_app=lambda: self.log.append("quit"), playing=self._playing, windows_open=self._windows,
            hidden_lanes=lambda: self.hidden, open_url=self._open, frozen=lambda: frozen,
            root=lambda: self.root, net=self.net, apply=self.apply)

    def _windows(self):
        if self.windows_error is not None:
            raise self.windows_error
        return self.windows

    def _save(self):
        self.test.assertTrue(self.lock.locked(), "改设置、存设置要在锁里")
        s = self.settings
        self.saved.append((s.update_auto, s.update_skipped))

    def _playing(self):
        if self.playing_error is not None:
            raise self.playing_error
        return self.in_game

    def _run(self, fn):
        if self.defer:
            self.queue.append(fn)
        else:
            fn()

    def drain(self) -> None:
        while self.queue:
            self.queue.pop(0)()

    def _later(self, ms, fn):
        self.timers.append((ms, fn))

    def fire(self, ms: int) -> None:
        """到点：做排在 ms 毫秒以后的那几件（做完从记录里拿掉）。"""
        due = [fn for m, fn in self.timers if m == ms]
        self.timers = [(m, fn) for m, fn in self.timers if m != ms]
        for fn in due:
            fn()

    def _refresh(self):
        self.log.append("refresh")
        self.shown.append(tuple(n.kind for n in self.ctl.notices(self.in_game)))

    def _trace(self, action, **data):
        self.test.assertFalse(self.lock.locked(), "记识别记录时不能拿着锁")
        self.traces.append((action, data))

    def _open(self, url):
        self.opened.append(url)
        return self.open_ok

    def notices(self, playing: bool = False) -> list:
        return list(self.ctl.notices(playing))

    def rows(self) -> list:
        return update_rows(self.ctl.notices())

    def available(self, release: Release = REL) -> None:
        """手动查一次，查到 release。"""
        self.net.result = CheckResult(release=release, latest=release.version)
        self.ctl.check_now()
        self.drain()
        self.test.assertEqual(self.ctl.state, "available")

    def say(self) -> tuple:
        """现在的状态、设置窗口那一行的一句、悬浮窗上更新那一条的文字（没有时为 None）。"""
        rows = [r for r in self.rows() if r[0] == "update"]
        return self.ctl.state, self.ctl.settings_note(), rows[0][1] if rows else None


CLOSE = ("关闭本条提示", "", "update_close", "关闭本条提示")
MANUAL = ("手动下载", "primary", "update_manual", "在浏览器中下载最新版拾星助手的压缩包")
MANUAL_PAGE = ("手动下载", "primary", "update_manual", "在浏览器中打开最新版拾星助手的发布页面")
STOP = ("停止", "", "update_stop", "停止下载，已下载的部分不保留")
COMPARING = "正在比对本机的文件，找出新版本 99.0.0 变了的部分…"
ASSEMBLING = "正在用本机的文件拼出新版本 99.0.0…"
PREPARING = "正在解压、核对新版本 99.0.0…"


class ErrorsCase(unittest.TestCase):
    """错误记录指到这个测试自己的文件；结束时还有没核对过的就算失败。"""

    def setUp(self):
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), [], "出错（错误记录）"))

    def expect_errors(self, *titles: str) -> None:
        self.assertEqual([title for title, _ in self.errors.entries()], list(titles))
        self.errors.clear()


# ------------------------------------------------------------ 设置文件里的两项


class SettingsFieldsTest(unittest.TestCase):
    def test_defaults_and_old_file(self):
        """默认开着自动检查、没跳过；旧设置文件没有这两项时照默认、不记警告；存了再读一样。两项都跨对局沿用。"""
        s = UiSettings()
        self.assertEqual((s.update_auto, s.update_skipped), (True, ""))
        path = sandbox.temp_dir(self, "test-update-ui-") / "state.json"
        path.write_text(json.dumps({"hero": "芬妮", "lowend": True}), encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            old = UiSettings.load(path)
        self.assertEqual((old.hero, old.update_auto, old.update_skipped), ("芬妮", True, ""))
        self.assertFalse(hasattr(old, "update_checked"))
        old.update_auto, old.update_skipped = False, NEW
        self.assertEqual(old.save(path), path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["update_auto"], saved["update_skipped"]), (False, NEW))
        self.assertNotIn("update_checked", saved)
        again = UiSettings.load(path)
        self.assertEqual((again.update_auto, again.update_skipped), (False, NEW))
        for name in ("update_auto", "update_skipped"):
            self.assertNotIn(name, GAME_FIELDS)

    def test_old_checked_date_dropped(self):
        """旧设置文件里上次检查更新记的日期：读的时候忽略（不影响自动检查），下次保存时去掉。"""
        path = sandbox.temp_dir(self, "test-update-ui-") / "state.json"
        path.write_text(json.dumps({"update_checked": date.today().isoformat(), "update_skipped": NEW}),
                        encoding="utf-8")
        with self.assertNoLogs("advisor", level="WARNING"):
            s = UiSettings.load(path)
        self.assertEqual((s.update_auto, s.update_skipped), (True, NEW))
        s.save(path)
        self.assertNotIn("update_checked", path.read_text(encoding="utf-8"))

    def test_bad_values(self):
        """类型不对的按默认（自动检查按开）。"""
        self.assertIs(UiSettings(update_auto="no").update_auto, True)
        self.assertIs(UiSettings(update_auto=0).update_auto, True)
        self.assertEqual(UiSettings(update_skipped=None).update_skipped, "")


# ------------------------------------------------------------ 文字


class WordingTest(unittest.TestCase):
    def test_failed_texts(self):
        """出错的那一句：按种类选；原因去掉末尾的句号，没有原因时写「原因不明」（没换上、退回的几种原因另起一句「原因：……。」，
        没有原因时不写这一句）；设置窗口用短的。"""
        f = W.update_failed_text
        self.assertEqual(f("check", "连不上下载镜像，也连不上发布页面。"), "检查更新失败：连不上下载镜像，也连不上发布页面。")
        self.assertEqual(f("check"), "检查更新失败：原因不明。")
        self.assertEqual(f("download", "剩余空间不够."), "新版本下载失败：剩余空间不够。")
        self.assertEqual(f("prepare", "压缩包里缺少程序文件"), "新版本解压、核对失败：压缩包里缺少程序文件。")
        self.assertEqual(f("launch"), "没能启动换版本的程序，新版本没有换上，拾星助手照常运行。下次启动时会再试一次，"
                                      "也可以点击「手动下载」。")
        self.assertEqual(f("rolled_back", "", NEW), "新版本 99.0.0 启动失败，已换回原来的版本。自动检查不再提示这一版，"
                                                   "需要时请点击「手动下载」。")
        self.assertEqual(f("rolled_back", "新版本没有打开。", NEW),
                         "新版本 99.0.0 启动失败，已换回原来的版本。原因：新版本没有打开。自动检查不再提示这一版，"
                         "需要时请点击「手动下载」。")
        self.assertEqual(f("not_applied", "", NEW), "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。下次启动时会再试一次，"
                                                   "也可以点击「手动下载」手动更新。")
        self.assertEqual(f("not_applied", "等不到拾星助手退出", NEW),
                         "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。原因：等不到拾星助手退出。下次启动时会再试一次，"
                         "也可以点击「手动下载」手动更新。")
        # 换版本程序真写的原因（括号里带细节的）：结论只说一次，原因另起一句
        self.assertEqual(f("rolled_back", real_apply.REASON_EXITED.format(detail="退出码 1"), NEW),
                         "新版本 99.0.0 启动失败，已换回原来的版本。原因：新版本启动后，悬浮窗还没出来就退出了（退出码 1）。"
                         "自动检查不再提示这一版，需要时请点击「手动下载」。")
        self.assertEqual(f("not_applied", real_apply.REASON_LOCKED, NEW),
                         "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。原因：程序文件夹里有文件正被别的程序占用（例如打开着"
                         "里面的文件或文件夹）。下次启动时会再试一次，也可以点击「手动下载」手动更新。")
        # 没换上的次数到了上限：不说下次启动时会再试，指到设置窗口的「检查更新」（按钮的字照设置窗口里的写）
        self.assertEqual(f("launch_final"), "没能启动换版本的程序，新版本没有换上，拾星助手照常运行。自动检查不再提示这一版，"
                                            "需要时请点击「设置」窗口中的「检查更新」按钮再试，或者点击「手动下载」。")
        self.assertEqual(f("not_applied_final", "", NEW),
                         "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。自动检查不再提示这一版，需要时请点击「设置」窗口中的"
                         "「检查更新」按钮再试，或者点击「手动下载」手动更新。")
        self.assertEqual(f("not_applied_final", "等不到拾星助手退出。", NEW),
                         "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。原因：等不到拾星助手退出。自动检查不再提示这一版，"
                         "需要时请点击「设置」窗口中的「检查更新」按钮再试，或者点击「手动下载」手动更新。")
        # 换版本程序写的原因只写原因：结论（没有换上、已换回）由提示行写，原因里再写一遍就重复了
        reasons = {name: value for name, value in vars(real_apply).items() if name.startswith("REASON_")}
        self.assertGreater(len(reasons), 10)
        for name, value in reasons.items():
            with self.subTest(name):
                for conclusion in ("这次没有换", "退回旧版本", "没有换上", "换回原来的版本"):
                    self.assertNotIn(conclusion, value)
        for kind in ("launch_final", "not_applied_final"):
            self.assertIn(f"「{st.UPDATE_BUTTON}」按钮", W.UPDATE_FAILED[kind])
            self.assertNotIn("下次启动", W.UPDATE_FAILED[kind])
        # 起不来、又读不到这一次的结果：说不准下次启动会不会再试，不许诺
        self.assertEqual(f("launch_unrecorded"), "没能启动换版本的程序，新版本没有换上，拾星助手照常运行，可以点击「手动下载」。")
        self.assertNotIn("下次启动", W.UPDATE_FAILED["launch_unrecorded"])
        self.assertEqual(f("别的", "连不上"), "新版本下载失败：连不上。")  # 不认识的种类按下载失败
        self.assertEqual(f("launch", short=True), "没能启动换版本的程序，新版本没有换上。")
        self.assertEqual(f("launch_final", short=True), "没能启动换版本的程序，新版本没有换上。")
        self.assertEqual(f("launch_unrecorded", short=True), "没能启动换版本的程序，新版本没有换上。")
        self.assertEqual(f("rolled_back", "新版本没有打开", NEW, short=True), "新版本 99.0.0 启动失败，已换回原来的版本。")
        self.assertEqual(f("not_applied", "等不到", NEW, short=True), "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。")
        self.assertEqual(f("not_applied_final", "等不到", NEW, short=True),
                         "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。")
        self.assertEqual(f("check", "连不上", short=True), "检查更新失败：连不上。")  # 这几种和悬浮窗上的一样
        self.assertEqual(set(W.UPDATE_FAILED), {"check", "download", "prepare", "launch", "launch_final", "launch_unrecorded",
                                                "rolled_back", "not_applied", "not_applied_final"})
        self.assertLessEqual(set(W.UPDATE_FAILED_NOTE), set(W.UPDATE_FAILED))

    def test_numbers(self):
        """大小按整数 MB 写（至少 1）；有文件清单时写成上限；下载中已下载的往下取整；资料同步日期没有时不写那一段。"""
        self.assertEqual((W.update_mb(SIZE), W.update_mb(1), W.update_mb(None)), ("85", "1", "?"))
        self.assertEqual(W.update_size_text(SIZE), "约 85 MB")
        self.assertEqual(W.update_size_text(SIZE, True), "最多约 85 MB，只下载变了的部分时更少")
        self.assertEqual(W.update_progress_text(NEW, 34 * MB - 1, SIZE), "正在下载新版本 99.0.0（33 / 85 MB）")
        self.assertEqual(W.update_progress_text(NEW, None, SIZE), "正在下载新版本 99.0.0（0 / 85 MB）")
        self.assertEqual(W.update_date_text("2030-01-15"), "（资料同步到 2030-01-15）")
        self.assertEqual((W.update_date_text(None), W.update_date_text("")), ("", ""))

    def test_download_steps(self):
        """下载中的那一句按下到哪一步选：比对本机的文件不写大小；只下变了的部分写清是变了的部分、带 x / y MB（y 是要下载的）；
        要下载的是 0 时写用本机的文件拼出新版本，不写「0 / 0 MB」；下整包（含空的一步）同 update_progress_text。"""
        t = W.update_download_text
        self.assertEqual(t(NEW, 0, 0, "compare"), COMPARING)
        self.assertEqual(t(NEW, 5 * MB + 1, PART, "delta"), "正在下载新版本 99.0.0 变了的部分（5 / 12 MB）")
        self.assertEqual(t(NEW, 100, 300 * 1024, "delta"), "正在下载新版本 99.0.0 变了的部分（0 / 1 MB）")
        for total in (0, None, "x"):
            self.assertEqual(t(NEW, 0, total, "delta"), ASSEMBLING)
        for step in ("full", ""):
            self.assertEqual(t(NEW, 3 * MB, SIZE, step), W.update_progress_text(NEW, 3 * MB, SIZE))
        for text in (COMPARING, ASSEMBLING):
            self.assertNotIn("MB", text)

    def test_sentences(self):
        self.assertEqual(W.UPDATE_RESTART, ("重启更新", "关闭拾星助手，换上新版本后自动重新打开"))
        self.assertEqual([b[0] for b in (W.UPDATE_START, W.UPDATE_NOTES, W.UPDATE_SKIP, W.UPDATE_LATER, W.UPDATE_STOP,
                                         W.UPDATE_MANUAL)],
                         ["更新", "看看改了什么", "跳过这个版本", "以后再说", "停止", "手动下载"])
        # 「看看改了什么」没有更新说明时打开的是发布页面：悬停说明写明
        self.assertEqual(W.UPDATE_NOTES[1], "在浏览器中打开这一版的更新说明；没有更新说明时打开发布页面")
        self.assertEqual(W.UPDATE_CHECKING, "正在检查更新…")
        self.assertEqual(W.UPDATE_DEV, "开发版不检查更新。")
        self.assertEqual(W.UPDATE_FOUND.format(version=NEW), "有新版本 99.0.0，悬浮窗上有更新的提示（对局中不显示）。")
        self.assertEqual(W.UPDATE_READY_NOTE.format(version=NEW), "新版本 99.0.0 已下好，下次启动拾星助手时自动换上。")
        self.assertEqual(W.UPDATE_READY_WINDOWS.format(version=NEW),
                         "新版本 99.0.0 已下好，关掉拾星助手的其他窗口后自动重启换上。")
        self.assertEqual(lang.to_hant(W.UPDATE_READY_WINDOWS.format(version=NEW)),
                         "新版本 99.0.0 已下好，關掉拾星助手的其他視窗後自動重啟換上。")
        self.assertEqual(W.UPDATE_DONE.format(version="1.2.3"), "拾星助手已更新到 1.2.3。")
        self.assertEqual(W.UPDATE_ERROR.format(err_file="app\\.tmp\\overlay_err.txt"),
                         "出了意外的错误，详细信息已写入错误记录（app\\.tmp\\overlay_err.txt）")
        self.assertEqual((W.NOTICE_AREA["update"], W.NOTICE_AREA["updated"]), ("global", "global"))
        # 识别出错、识别图片下载后处理失败的提示指向设置窗口的「检查更新」（按钮的字照设置窗口里的写）
        tail = f"多次出现请点击「设置」窗口中的「{st.UPDATE_BUTTON}」按钮，更新到最新版拾星助手。"
        for text in (W.RECOG_ITEM, W.RECOG_FRAME, W.RECOG_CAPTURE, W.RECOG_STARTUP, W.DOWNLOAD_CROP_FAILED):
            self.assertTrue(text.endswith(tail), text)
        self.assertIn(st.UPDATE_BUTTON, W.SETTINGS_TIP)

    def test_news_version(self):
        """换上新版本以后列的主要改动是这一版的：定版本号时这几项要跟着改，忘了改时这里先报出来。"""
        self.assertEqual(W.UPDATE_NEWS_VERSION, VERSION,
                         "定版本号时照软件维护说明「版本号」一节，把换上新版本以后列的主要改动（wording.py 的 UPDATE_NEWS）"
                         "和它的版本号 UPDATE_NEWS_VERSION 一起改好")


# ------------------------------------------------------------ 提示行


class RowsTest(unittest.TestCase):
    def test_available(self):
        """有新版本：四个按钮（「更新」是主按钮），下好后自动重启换上；这一版只能手动下载时「更新」换成「手动下载」；没有
        资料同步日期时不写那一段。"""
        note = UpdateNotice("available", version=NEW, data_date="2030-01-15", size=SIZE)
        self.assertEqual(update_rows([note]), [(
            "update", "拾星助手有新版本 99.0.0（资料同步到 2030-01-15）。点击「更新」开始下载（约 85 MB），下载期间照常使用，"
                      "下好后自动重启换上。",
            "info", [("更新", "primary", "update_start",
                      "下载新版本（约 85 MB），下好后自动重启换上；对局中下好的，等对局结束再重启"),
                     ("看看改了什么", "", "update_notes", "在浏览器中打开这一版的更新说明；没有更新说明时打开发布页面"),
                     ("跳过这个版本", "", "update_skip", "不更新到 99.0.0：自动检查不再提示这一版，有更新的版本时照常提示"),
                     ("以后再说", "", "update_later", "关闭本条提示，下次自动检查时再提示")])])
        text = update_rows([UpdateNotice("available", version=NEW, size=SIZE)])[0][1]
        self.assertTrue(text.startswith("拾星助手有新版本 99.0.0。点击「更新」"), text)
        manual = update_rows([UpdateNotice("available", version=NEW, auto=False, size=SIZE)])[0]
        self.assertEqual(manual[1], "拾星助手有新版本 99.0.0。这一版需要手动更新：点击「手动下载」，在浏览器中下载压缩包。")
        self.assertEqual([b[:3] for b in manual[3]], [MANUAL[:3], ("看看改了什么", "", "update_notes"),
                                                      ("跳过这个版本", "", "update_skip"), ("以后再说", "", "update_later")])

    def test_available_with_manifest(self):
        """这一版有文件清单（只下变了的部分）：那一句和「更新」的悬停说明里大小写成上限。"""
        row = update_rows([UpdateNotice("available", version=NEW, data_date="2030-01-15", delta=True, size=SIZE)])[0]
        self.assertEqual(row[1], "拾星助手有新版本 99.0.0（资料同步到 2030-01-15）。点击「更新」开始下载（最多约 85 MB，只下载"
                                 "变了的部分时更少），下载期间照常使用，下好后自动重启换上。")
        self.assertEqual(row[3][0], ("更新", "primary", "update_start",
                                     "下载新版本（最多约 85 MB，只下载变了的部分时更少），下好后自动重启换上；对局中下好的，"
                                     "等对局结束再重启"))

    def test_downloading_steps(self):
        """下载中的几步：比对本机的文件、只下变了的部分、用本机的文件拼出新版本（要下载的是 0）、下整包，都带「停止」。"""
        cases = [
            (UpdateNotice("downloading", version=NEW, step="compare"), COMPARING),
            (UpdateNotice("downloading", version=NEW, done=MB, size=PART, step="delta"),
             "正在下载新版本 99.0.0 变了的部分（1 / 12 MB）"),
            (UpdateNotice("downloading", version=NEW, step="delta"), ASSEMBLING),
            (UpdateNotice("downloading", version=NEW, done=MB, size=SIZE, step="full"), "正在下载新版本 99.0.0（1 / 85 MB）"),
        ]
        for note, text in cases:
            with self.subTest(text=text):
                self.assertEqual(update_rows([note]), [("update", text, "info", [STOP])])

    def test_other_kinds(self):
        """下载中、解压核对中、下好了（自动重启的两种没有按钮、上次已下好的带「重启更新」）、已经是最新、出错（⚠ 橙色，
        「手动下载」打开压缩包或发布页面）、刚换上新版本；都不让边框变色；刚换上的那一条在前。"""
        cases = [
            (UpdateNotice("downloading", version=NEW, done=34 * MB + 5, size=SIZE), "正在下载新版本 99.0.0（34 / 85 MB）",
             "info", [STOP]),
            (UpdateNotice("preparing", version=NEW), "正在解压、核对新版本 99.0.0…", "info", []),
            (UpdateNotice("ready", version=NEW, restart=updates.RESTART_SOON), "新版本 99.0.0 已下好，马上重启换上。",
             "info", []),
            (UpdateNotice("ready", version=NEW, restart=updates.RESTART_WAIT),
             "新版本 99.0.0 已下好，对局结束后自动重启换上。", "info", []),
            (UpdateNotice("ready", version=NEW), "新版本 99.0.0 已下好：点击「重启更新」马上换上；不点的话，下次启动拾星助手"
                                                 "时自动换上。", "info",
             [("重启更新", "primary", "update_restart", "关闭拾星助手，换上新版本后自动重新打开"), CLOSE]),
            (UpdateNotice("latest", version="1.2.3"), "已经是最新版本（1.2.3）。", "info", [CLOSE]),
            (UpdateNotice("failed", version=NEW, failure="download", reason="连不上"), "⚠ 新版本下载失败：连不上。", "warn",
             [MANUAL, CLOSE]),
            (UpdateNotice("failed", failure="check", reason="连不上", page=True), "⚠ 检查更新失败：连不上。", "warn",
             [MANUAL_PAGE, CLOSE]),
        ]
        for note, text, level, buttons in cases:
            with self.subTest(kind=note.kind):
                self.assertEqual(update_rows([note]), [("update", text, level, buttons)])
        self.assertEqual(update_rows([]), [])
        rows = update_rows([UpdateNotice("updated", version="1.2.3"), UpdateNotice("latest", version="1.2.3")])
        self.assertEqual(rows[0], ("updated", "拾星助手已更新到 1.2.3。", "info",
                                   [("关闭本条提示", "", "update_close_done", "关闭本条提示")]))
        self.assertEqual([r[0] for r in rows], ["updated", "update"])
        self.assertIn("update", STACKED_NOTICES)

    def test_updated_news(self):
        """刚换上新版本：换上的正是 UPDATE_NEWS_VERSION 这一版时列出这一版的主要改动；版本号对不上、列表空着时只写第一句；
        繁体界面上显示时对着改动按繁体转。"""
        def row(version=W.UPDATE_NEWS_VERSION):
            return update_rows([UpdateNotice("updated", version=version)])[0]

        expected = [W.UPDATE_DONE.format(version=W.UPDATE_NEWS_VERSION),
                    *(W.UPDATE_NEWS_ITEM.format(item=item) for item in W.UPDATE_NEWS)]
        if W.UPDATE_NEWS_SOON:
            expected.append(W.UPDATE_NEWS_SOON)
        self.assertEqual(row()[1], "\n".join(expected))
        other = "0.0.1"
        self.assertEqual(row(other)[1], W.UPDATE_DONE.format(version=other))
        with mock.patch.object(W, "UPDATE_NEWS", ()):
            self.assertEqual(row()[1], W.UPDATE_DONE.format(version=W.UPDATE_NEWS_VERSION))
        # 逐字核对排法：一条一行、前面加「· 」，即将上线的写在最后一行
        with mock.patch.object(W, "UPDATE_NEWS", ("甲", "乙")), mock.patch.object(W, "UPDATE_NEWS_SOON", "即将上线：丙"):
            self.assertEqual(row()[1], f"拾星助手已更新到 {W.UPDATE_NEWS_VERSION}。\n· 甲\n· 乙\n即将上线：丙")
        hant = lang.to_hant(row()[1])
        changed = [item for item in W.UPDATE_NEWS if lang.to_hant(item) != item]
        self.assertTrue(changed, "繁简对照表里应有要转的改动")
        for item in changed:
            self.assertIn(lang.to_hant(item), hant)


# ------------------------------------------------------------ 控制模块


class DevTest(ErrorsCase):
    def test_dev_version(self):
        """开发版：启动时不读结果、不收尾、不排自动检查；「检查更新」只说一句开发版不检查更新（不联网、不记日期）。"""
        h = Harness(self, frozen=False)
        h.apply.result = {"status": "ok", "version": VERSION, "reason": ""}
        h.ctl.startup()
        self.assertEqual((h.apply.calls, h.timers, h.ctl.updated), ([], [], None))
        h.ctl.check_now()
        self.assertEqual(h.net.calls, [])
        self.assertEqual(h.ctl.settings_note(), "开发版不检查更新。")
        self.assertEqual((h.log, h.saved, h.notices()), (["settings"], [], []))
        self.assertEqual(h.traces, [("update_check", {})])
        h.ctl.schedule_auto()
        self.assertEqual(h.timers, [])


class AutoCheckTest(ErrorsCase):
    def test_every_start(self):
        """发行版启动时读结果、后台收尾，过十几秒自动查一次（不记日期）；查过以后再启动（再调用 schedule_auto）照样排；
        旧设置文件里记着上次检查的日期时也照排；关了自动检查不查。"""
        h = Harness(self)
        h.ctl.startup()
        self.assertEqual(h.apply.calls, [("take_result", h.root), ("after_start", h.root)])
        self.assertEqual([ms for ms, _ in h.timers], [updates.AUTO_CHECK_DELAY_MS])
        self.assertEqual(h.net.calls, [])
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls, [("check", VERSION, None)])
        self.assertIs(h.net.manual, False)
        self.assertEqual(h.saved, [])  # 自动检查不写设置
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.settings_note()), ("idle", [], ""))  # 已经是最新：不出声
        self.assertEqual(h.traces, [])
        h.ctl.schedule_auto()
        self.assertEqual([ms for ms, _ in h.timers], [updates.AUTO_CHECK_DELAY_MS])
        # 旧设置文件里记着上次检查的日期（今天）：照样排上自动检查
        path = sandbox.temp_dir(self, "test-update-ui-") / "state.json"
        path.write_text(json.dumps({"update_checked": date.today().isoformat()}), encoding="utf-8")
        old = Harness(self, settings=UiSettings.load(path))
        old.ctl.schedule_auto()
        self.assertEqual([ms for ms, _ in old.timers], [updates.AUTO_CHECK_DELAY_MS])
        h.settings.update_auto = False
        h.ctl.schedule_auto()
        self.assertEqual(len(h.timers), 1)

    def test_found_and_skipped(self):
        """自动检查带上跳过的版本；查到新版本时悬浮窗上一条、设置窗口写一句。"""
        h = Harness(self, settings=UiSettings(update_skipped="98.0.0"))
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls, [("check", VERSION, "98.0.0")])
        self.assertIs(h.net.manual, False)
        self.assertEqual(h.notices(), [UpdateNotice("available", version=NEW, data_date="2030-01-15", auto=True, size=SIZE)])
        self.assertEqual(h.ctl.settings_note(), "有新版本 99.0.0，悬浮窗上有更新的提示（对局中不显示）。")
        self.assertFalse(h.ctl.busy)
        self.assertEqual(h.shown[-1], ("available",))

    def test_quiet_when_failing(self):
        """自动检查连不上、程序出错都不出声（程序出错照样记进错误记录）；到点时已经不是没事（手动查过了）时不查。"""
        h = Harness(self)
        h.net.result = CheckResult(latest=None, reachable=False, mirror_ok=False, problem="连不上下载镜像，也连不上发布页面")
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.settings_note()), ("idle", [], ""))
        h.net.check_error = RuntimeError("测试造的错")
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual((h.ctl.state, h.notices()), ("idle", []))
        self.expect_errors("检查更新出错（拾星助手仍在运行）")
        h.net.check_error = None
        h.ctl.schedule_auto()
        h.available()
        calls = len(h.net.calls)
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(len(h.net.calls), calls)

    def test_module_missing(self):
        """发行版里缺了查和下载的模块（程序文件不完整）：自动检查不出声，手动检查说一句；都记进错误记录。"""
        h = Harness(self)
        with mock.patch.object(h.ctl, "net_module", side_effect=ImportError("测试造的错")):
            h.ctl.schedule_auto()
            h.fire(updates.AUTO_CHECK_DELAY_MS)
            self.assertEqual(h.ctl.state, "idle")
            h.ctl.check_now()
        self.assertEqual((h.ctl.state, h.ctl.failure), ("failed", "check"))
        self.assertTrue(h.ctl.reason.startswith("出了意外的错误，详细信息已写入错误记录（"), h.ctl.reason)
        self.expect_errors("检查更新出错（拾星助手仍在运行）", "检查更新出错（拾星助手仍在运行）")


class ManualCheckTest(ErrorsCase):
    def test_check_now(self):
        """「检查更新」：马上在后台查，跳过的版本照样提示（不写设置）；检查中、下载中、解压核对中、下好了时不再查。"""
        h = Harness(self, defer=True, settings=UiSettings(update_skipped=NEW))
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.check_now()
        self.assertEqual((h.ctl.state, h.ctl.settings_note(), h.ctl.busy), ("checking", "正在检查更新…", True))
        self.assertEqual(h.notices(), [])
        h.ctl.check_now()
        h.drain()
        self.assertEqual(h.net.calls, [("check", VERSION, None)])
        self.assertIs(h.net.manual, True)
        self.assertEqual(h.ctl.state, "available")
        self.assertEqual(h.saved, [])
        self.assertEqual(h.traces, [("update_check", {})] * 2)
        for state in ("downloading", "preparing", "ready"):
            h.ctl.state = state
            h.ctl.check_now()
            h.drain()
        self.assertEqual(len(h.net.calls), 1)

    def test_latest(self):
        """已经是最新：悬浮窗上说一句，一会儿以后收起；到点之前又查到了新版本时不收起新的那一条。"""
        h = Harness(self)
        h.ctl.check_now()
        self.assertEqual(h.notices(), [UpdateNotice("latest", version=VERSION)])
        self.assertEqual(h.ctl.settings_note(), f"已经是最新版本（{VERSION}）。")
        self.assertEqual([ms for ms, _ in h.timers], [updates.LATEST_MS])
        h.fire(updates.LATEST_MS)
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.settings_note()), ("idle", [], ""))
        h.ctl.check_now()
        h.available()
        h.fire(updates.LATEST_MS)
        self.assertEqual(h.ctl.state, "available")

    def test_unreachable(self):
        """两边都连不上：说原因（⚠ 橙色），带「手动下载」（打开下载镜像上的压缩包）。"""
        h = Harness(self)
        problem = "连不上下载镜像，也连不上发布页面（网络不通，或者被防火墙拦下了）"
        h.net.result = CheckResult(latest=None, reachable=False, mirror_ok=False, problem=problem)
        h.ctl.check_now()
        self.assertEqual(h.notices(), [UpdateNotice("failed", failure="check", reason=problem)])
        text = f"检查更新失败：{problem}。"
        self.assertEqual(h.rows(), [("update", "⚠ " + text, "warn", [MANUAL, CLOSE])])
        self.assertEqual(h.ctl.settings_note(), text)
        self.assertFalse(h.ctl.busy)
        h.ctl.open_manual()
        self.assertEqual(h.opened, [FakeNet.LATEST_ZIP])

    def test_no_source(self):
        """源代码地址推不出查更新的地址（复刻到别的网站的改版，update.NO_SOURCE）：手动检查说这一句（套进「检查更新失败：」，
        句号只有一个；不说连不上、不说已是最新），「手动下载」开源代码仓库的发布页面；自动检查不出声。"""
        h = Harness(self)
        h.net.USE_MIRROR = False
        h.net.result = CheckResult(latest=None, reachable=False, mirror_ok=False, problem=real_update.NO_SOURCE)
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.settings_note()), ("idle", [], ""))
        h.ctl.check_now()
        text = "检查更新失败：这个版本没有设置查更新的地址，查不了新版本。"
        self.assertEqual(h.rows(), [("update", "⚠ " + text, "warn", [MANUAL_PAGE, CLOSE])])
        self.assertEqual(h.ctl.settings_note(), text)
        h.ctl.open_manual()
        self.assertEqual(h.opened, [updates.RELEASES_PAGE])
        self.assertEqual(h.net.calls, [("check", VERSION, None), ("check", VERSION, None)])

    def test_unverifiable(self):
        """拿到了版本信息、却一版都认不下（GitHub 上的附件没有校验值，镜像上也没有对得上的，update.NO_DIGEST）：手动检查说这一句，
        不说已经是最新版本；自动检查不出声。"""
        h = Harness(self)
        h.net.result = CheckResult(latest=None, reachable=True, mirror_ok=True, problem=real_update.NO_DIGEST)
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.settings_note()), ("idle", [], ""))
        h.ctl.check_now()
        text = "检查更新失败：" + real_update.NO_DIGEST
        self.assertEqual(h.rows(), [("update", "⚠ " + text, "warn", [MANUAL, CLOSE])])
        self.assertEqual(h.ctl.settings_note(), text)
        self.assertNotEqual(h.ctl.state, "latest")

    def test_program_error(self):
        """查的时候程序出错：说一句写明错误记录的位置，原文只进错误记录。"""
        h = Harness(self)
        h.net.check_error = RuntimeError("测试造的错")
        h.ctl.check_now()
        reason = W.UPDATE_ERROR.format(err_file=app_paths.shown(W.ERR_RECORD))
        self.assertEqual(h.notices(), [UpdateNotice("failed", failure="check", reason=reason)])
        self.assertNotIn("测试造的错", h.rows()[0][1])
        self.expect_errors("检查更新出错（拾星助手仍在运行）")

    def test_skip_and_later(self):
        """「跳过这个版本」记进设置、收起，以后自动检查带上它；「以后再说」只收起，不存什么。"""
        h = Harness(self)
        h.available()
        h.ctl.skip()
        self.assertEqual((h.ctl.state, h.ctl.release, h.notices()), ("idle", None, []))
        self.assertEqual((h.settings.update_skipped, h.saved[-1]), (NEW, (True, NEW)))
        self.assertIn(("update_skip", {"version": NEW}), h.traces)
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls[-1], ("check", VERSION, NEW))
        h.available(Release(version="99.1.0"))
        saved = list(h.saved)
        h.ctl.dismiss()
        self.assertEqual((h.ctl.state, h.notices(), h.saved, h.settings.update_skipped), ("idle", [], saved, NEW))
        self.assertIn(("update_later", {}), h.traces)

    def test_held_in_game(self):
        """对局中什么都不给（先攒着），下载在后台照常进行；对局结束后提示马上重启。"""
        h = Harness(self)
        h.available()
        h.in_game = True
        self.assertEqual(h.ctl.notices(True), ())
        h.ctl.start_download()
        self.assertEqual(h.ctl.state, "ready")
        self.assertEqual(h.ctl.notices(True), ())
        h.in_game = False
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW, restart=updates.RESTART_SOON)])
        h.ctl.updated = VERSION
        self.assertEqual(h.ctl.notices(True), ())

    def test_set_auto(self):
        """设置窗口的开关：存进设置、记识别记录、更新设置窗口；和现在相同时什么都不做。"""
        h = Harness(self)
        self.assertTrue(h.ctl.auto_on())
        h.ctl.set_auto(False)
        self.assertFalse(h.ctl.auto_on())
        self.assertEqual((h.saved, h.traces, h.log), ([(False, "")], [("update_auto", {"on": False})], ["settings"]))
        h.ctl.set_auto(False)
        self.assertEqual(len(h.saved), 1)
        h.ctl.set_auto(True)
        self.assertEqual(h.saved[-1], (True, ""))

    def test_already_prepared(self):
        """查到的这一版已经下好、核对过（update_apply.pending 给的就是它：上次没换上、下次启动还会再换）：直接提示「重启更新」，
        不再下载；只能手动下载的版本、备好的是别的版本、没有备好的、查备好的出错时照常提示下载。"""
        h = Harness(self)
        h.apply.ready = {"format": 1, "version": NEW}
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.check_now()
        self.assertEqual((h.ctl.state, h.notices()), ("ready", [UpdateNotice("ready", version=NEW)]))
        self.assertEqual(h.apply.calls, [("pending", h.root, VERSION)])
        self.assertEqual(h.ctl.settings_note(), "新版本 99.0.0 已下好，下次启动拾星助手时自动换上。")
        h.ctl.restart()
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.assertNotIn("preflight", [c[0] for c in h.net.calls])
        cases = {"manual": (Release(auto=False), {"format": 1, "version": NEW}, None),
                 "other": (REL, {"format": 1, "version": "98.0.0"}, None), "none": (REL, None, None),
                 "error": (REL, {"format": 1, "version": NEW}, OSError("测试造的错"))}
        for name, (release, ready, error) in cases.items():
            with self.subTest(case=name):
                h = Harness(self)
                h.apply.ready, h.apply.pending_error = ready, error
                h.available(release)

    def test_diagnostic(self):
        h = Harness(self)
        self.assertEqual(h.ctl.diagnostic(), {"state": "idle", "version": None, "failure": None, "reason": None})
        h.available()
        h.net.reason = "剩余空间不够"
        h.ctl.start_download()
        self.assertEqual(h.ctl.diagnostic(), {"state": "failed", "version": NEW, "failure": "download",
                                              "reason": "剩余空间不够"})


class DownloadTest(ErrorsCase):
    def test_progress_and_ready(self):
        """「更新」（这一版没有文件清单）：先看程序文件夹写不写得进、空间够不够，再在后台下载、备好（只调 update.obtain：下整包，
        每多 1 MB 交回一次进度，进度那一句变了才刷新）、解压核对，下好了提示马上重启换上；这时设置窗口的「检查更新」不能点。"""
        h = Harness(self, defer=True)
        h.available()
        h.net.chunks = (MB // 2, MB, MB + 10, 2 * MB, 3 * MB, SIZE)
        got = []
        h.ctl.progressed.connect(lambda done, total: got.append(done))
        start = len(h.shown)
        h.ctl.start_download()
        self.assertEqual((h.ctl.state, h.ctl.busy), ("downloading", True))
        self.assertEqual(h.notices(), [UpdateNotice("downloading", version=NEW, done=0, size=SIZE, step="full")])
        self.assertEqual(h.ctl.settings_note(), "正在下载新版本 99.0.0（0 / 85 MB）")
        self.assertEqual(h.net.calls[-1], ("preflight", h.root))
        self.assertIn(("update_start", {"version": NEW}), h.traces)
        h.drain()
        self.assertEqual(got, [MB // 2, MB, 2 * MB, 3 * MB, SIZE])
        self.assertEqual(h.net.calls[-1], ("obtain", NEW, h.root))
        self.assertEqual(h.shown[start:], [("downloading",)] * 5 + [("preparing",), ("ready",)])
        self.assertEqual(h.ctl.progress, (SIZE, SIZE))
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW, restart=updates.RESTART_SOON)])
        self.assertEqual(h.ctl.settings_note(), W.UPDATE_RESTARTING.format(version=NEW))
        self.assertTrue(h.ctl.busy)
        self.assertTrue(h.ctl.dl_done.is_set())
        self.assertTrue(h.ctl.shutdown(0.1))

    def test_delta(self):
        """有文件清单：一开始就写比对本机的文件那一句（不带大小），只下变了的部分时写清是变了的部分、带 x / y MB（y 是要下载的，
        不是压缩包的大小），再解压核对、下好了。悬浮窗的提示行和设置窗口那一行写同一句，都带「停止」；那一句没变时不另刷新。"""
        h = Harness(self, defer=True)
        h.available(DELTA)
        h.net.steps = [("phase", "compare", None), ("phase", "delta", PART), ("progress", 0), ("progress", MB),
                       ("progress", 5 * MB + 7), ("progress", PART), ("phase", "prepare", None)]
        seen = []
        h.net.on_step = lambda step: seen.append(h.say())
        start = len(h.shown)
        h.ctl.start_download()
        self.assertEqual(h.say(), ("downloading", COMPARING, COMPARING))
        self.assertEqual(h.notices(), [UpdateNotice("downloading", version=NEW, step="compare")])
        self.assertEqual(h.rows()[0][3], [STOP])
        h.drain()
        delta = "正在下载新版本 99.0.0 变了的部分（{} / 12 MB）"
        self.assertEqual([text for _, text, _ in seen],
                         [COMPARING, delta.format(0), delta.format(0), delta.format(1), delta.format(5), delta.format(12),
                          PREPARING])
        self.assertEqual([(state, note == row) for state, note, row in seen], [("downloading", True)] * 6 + [("preparing", True)])
        self.assertEqual(h.ctl.state, "ready")
        self.assertEqual(h.shown[start:], [("downloading",)] * 5 + [("preparing",), ("ready",)])

    def test_delta_nothing_to_fetch(self):
        """本机的文件都用得上（要下载的是 0）：写用本机的文件拼出新版本，哪里都不出现「0 / 0 MB」。"""
        h = Harness(self, defer=True)
        h.available(DELTA)
        h.net.steps = [("phase", "compare", None), ("phase", "delta", 0), ("progress", 0), ("phase", "prepare", None)]
        seen = []
        h.net.on_step = lambda step: seen.append(h.say())
        h.ctl.start_download()
        h.drain()
        self.assertEqual(seen, [("downloading", COMPARING, COMPARING), ("downloading", ASSEMBLING, ASSEMBLING),
                                ("downloading", ASSEMBLING, ASSEMBLING), ("preparing", PREPARING, PREPARING)])
        self.assertEqual(h.ctl.state, "ready")

    def test_delta_falls_back_to_full(self):
        """只下变了的部分没走通、改下整包：先后收到「只下变了的部分」和「下整包」，按后一个写，进度从 0 重新数（下载线程交回的
        进度也从头数：下整包的第一次照样交回）；最后核对时才改下整包的，从解压核对回到下载中。"""
        h = Harness(self, defer=True)
        h.available(DELTA)
        h.net.steps = [("phase", "compare", None), ("phase", "delta", PART), ("progress", 3 * MB), ("phase", "full", SIZE),
                       ("progress", 3 * MB + 7), ("progress", 4 * MB), ("phase", "prepare", None)]
        seen = []
        h.net.on_step = lambda step: seen.append((h.ctl.progress, h.say()[1]))
        h.ctl.start_download()
        h.drain()
        self.assertEqual(seen[2:5], [((3 * MB, PART), "正在下载新版本 99.0.0 变了的部分（3 / 12 MB）"),
                                     ((0, SIZE), "正在下载新版本 99.0.0（0 / 85 MB）"),
                                     ((3 * MB + 7, SIZE), "正在下载新版本 99.0.0（3 / 85 MB）")])
        self.assertEqual(h.ctl.state, "ready")
        # 最后核对时改下整包（总数没给时按压缩包的大小）
        h = Harness(self, defer=True)
        h.available(DELTA)
        h.net.steps = [("phase", "compare", None), ("phase", "delta", PART), ("progress", PART), ("phase", "prepare", None),
                       ("phase", "full", None), ("progress", MB), ("phase", "prepare", None)]
        seen = []
        h.net.on_step = lambda step: seen.append(h.say()[:2])
        h.ctl.start_download()
        h.drain()
        self.assertEqual(seen[3:6], [("preparing", PREPARING), ("downloading", "正在下载新版本 99.0.0（0 / 85 MB）"),
                                     ("downloading", "正在下载新版本 99.0.0（1 / 85 MB）")])
        self.assertEqual(h.ctl.state, "ready")

    def test_stop(self):
        """「停止」：下载停下（比对本机的文件、只下变了的部分、下整包时都能停），回到有新版本那一条（能再点「更新」），不再往下做。
        obtain 已经做完（新版本已经备好）才看到停止的，照实提示下好了。不在下载时什么都不做。"""
        h = Harness(self, defer=True)
        h.available()
        h.net.chunks = (MB, 2 * MB, 3 * MB)
        h.net.on_chunk = lambda done: h.ctl.stop_download() if done == 2 * MB else None
        prepared = []
        h.ctl.phased.connect(lambda phase, detail: prepared.append(phase) if phase == "preparing" else None)
        h.ctl.start_download()
        h.drain()
        self.assertEqual((h.ctl.state, h.notices()[0].kind), ("available", "available"))
        self.assertEqual(prepared, [])
        self.assertTrue(h.ctl.dl_done.is_set())
        for stop_at in ("compare", "delta"):
            with self.subTest(stop_at=stop_at):
                h.available(DELTA)
                h.net.steps = [("phase", "compare", None), ("phase", "delta", PART), ("progress", MB),
                               ("phase", "prepare", None)]
                h.net.on_step = lambda step, at=stop_at: h.ctl.stop_download() if step[1] == at else None
                h.ctl.start_download()
                h.drain()
                self.assertEqual(h.ctl.state, "available")
                self.assertEqual(prepared, [])
        h.net.steps, h.net.on_step = None, None
        h.net.honor_stop = False
        h.net.on_chunk = lambda done: h.ctl.stop_download() if done == 3 * MB else None
        h.ctl.start_download()
        h.drain()
        self.assertEqual(h.ctl.state, "ready")
        h.ctl.stop_download()
        self.assertEqual(h.traces.count(("update_stop", {})), 4)

    def test_preflight_reason(self):
        """下载之前的检查给了原因（写不进、空间不够）：不下载，说原因；检查本身出错时说一句程序出错。"""
        h = Harness(self, defer=True)
        h.available()
        h.net.reason = "拾星助手所在的盘剩余空间不到 800 MB"
        h.ctl.start_download()
        self.assertEqual(h.queue, [])
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="download", reason=h.net.reason)])
        self.assertEqual(h.rows()[0][1], "⚠ 新版本下载失败：拾星助手所在的盘剩余空间不到 800 MB。")
        h.available()
        h.net.reason, h.net.preflight_error = None, OSError("测试造的错")
        h.ctl.start_download()
        self.assertEqual(h.ctl.reason, W.UPDATE_ERROR.format(err_file=app_paths.shown(W.ERR_RECORD)))
        self.expect_errors("下载新版本之前的检查出错（拾星助手仍在运行）")

    def test_errors(self):
        """下载、解压核对出错：UpdateError 的原因照写；别的错是程序出错（原文进错误记录）。出错算哪一步按 obtain 最后告诉的：
        解压核对以后的算解压核对失败，之前的（比对本机的文件、只下变了的部分、下整包）算下载失败。出错以后能再「检查更新」。"""
        cases = {
            ("download", "UpdateError"): ("download_error", UpdateError("下载的文件和发布的不一致（SHA-256 对不上），已删掉。"),
                                          "⚠ 新版本下载失败：下载的文件和发布的不一致（SHA-256 对不上），已删掉。"),
            ("prepare", "UpdateError"): ("prepare_error", UpdateError("压缩包里缺少程序文件"),
                                         "⚠ 新版本解压、核对失败：压缩包里缺少程序文件。"),
            ("download", "other"): ("download_error", RuntimeError("测试造的错"), None),
            ("prepare", "other"): ("prepare_error", RuntimeError("测试造的错"), None),
        }
        for (phase, kind), (attr, error, text) in cases.items():
            with self.subTest(phase=phase, kind=kind):
                h = Harness(self)
                h.available()
                setattr(h.net, attr, error)
                h.ctl.start_download()
                self.assertEqual((h.ctl.state, h.ctl.failure, h.ctl.failed_version), ("failed", phase, NEW))
                if text is None:
                    self.assertEqual(h.ctl.reason, W.UPDATE_ERROR.format(err_file=app_paths.shown(W.ERR_RECORD)))
                    self.expect_errors("下载新版本出错（拾星助手仍在运行）")
                else:
                    self.assertEqual(h.rows()[0][1], text)
                    self.assertEqual(h.ctl.settings_note(), text[2:])
                self.assertFalse(h.ctl.busy)
        # 有文件清单的：比对、只下变了的部分时出错算下载失败，最后核对时出错算解压核对失败
        for steps, failure in (([("phase", "compare", None)], "download"),
                               ([("phase", "compare", None), ("phase", "delta", PART), ("progress", MB)], "download"),
                               ([("phase", "compare", None), ("phase", "delta", 0), ("phase", "prepare", None)], "prepare")):
            with self.subTest(steps=steps):
                h = Harness(self)
                h.available(DELTA)
                h.net.steps = [*steps, ("error", UpdateError("磁盘满了"))]
                h.ctl.start_download()
                self.assertEqual((h.ctl.state, h.ctl.failure, h.ctl.reason), ("failed", failure, "磁盘满了"))

    def test_waits_longer_than_cleanup(self):
        """下载之前等启动时的收尾的上限，比收尾等换版本程序退出的上限（update_apply.AFTER_WAIT_S）至少多 30 秒：收尾等满了
        还要删文件，不在它还没删完时开始下载。"""
        self.assertGreaterEqual(updates.AFTER_START_WAIT_S, real_apply.AFTER_WAIT_S + 30)

    def test_manual_only(self):
        """这一版只能手动下载（auto 为假）：「手动下载」打开下载镜像上的压缩包，不下载。"""
        h = Harness(self)
        h.available(Release(auto=False))
        self.assertFalse(h.notices()[0].auto)
        h.ctl.start_download()
        self.assertEqual((h.opened, h.ctl.state), ([FakeNet.LATEST_ZIP], "available"))
        self.assertNotIn("preflight", [c[0] for c in h.net.calls])

    def test_waits_for_cleanup(self):
        """启动时的收尾（会删掉更新的工作文件夹）还没做完时点了「更新」：下载线程先等它做完再下载。"""
        h = Harness(self, defer=True)
        h.ctl.startup()
        cleanup = h.queue.pop(0)
        h.available()
        h.ctl.start_download()
        worker = threading.Thread(target=h.queue.pop(0), daemon=True)
        with mock.patch.object(updates, "AFTER_START_WAIT_S", 10):
            worker.start()
            time.sleep(0.3)
            self.assertNotIn("obtain", [c[0] for c in h.net.calls])
            cleanup()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        settle()  # 下载线程发的信号排队回到界面线程
        self.assertEqual([c[0] for c in h.net.calls][-1], "obtain")
        self.assertEqual(h.ctl.state, "ready")

    def test_stop_while_waiting(self):
        """等启动时的收尾的时候点了「停止」、退出（shutdown）：马上停下、不下载，回到有新版本那一条；退出不用等满收尾。"""
        h = Harness(self, defer=True)
        h.ctl.startup()
        cleanup = h.queue.pop(0)  # 收尾一直没做完
        self.addCleanup(cleanup)
        h.available()
        with mock.patch.object(updates, "AFTER_START_WAIT_S", 30):
            for how in ("stop", "shutdown"):
                with self.subTest(how=how):
                    h.ctl.start_download()
                    worker = threading.Thread(target=h.queue.pop(0), daemon=True)
                    worker.start()
                    time.sleep(0.3)
                    if how == "stop":
                        h.ctl.stop_download()
                    else:
                        self.assertTrue(h.ctl.shutdown(5))
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    settle()
                    self.assertEqual(h.ctl.state, "available")
        self.assertNotIn("obtain", [c[0] for c in h.net.calls])

    def test_shutdown(self):
        """退出时叫停下载、等它停下（返回停下了没有）；没有在下载时为真。"""
        h = Harness(self, defer=True)
        self.assertTrue(h.ctl.shutdown(0.1))
        h.available()
        h.net.chunks = (MB, 2 * MB)
        h.net.on_chunk = lambda done: h.ctl.stop_flag.wait(10)  # 下着：等到叫停
        h.ctl.start_download()
        worker = threading.Thread(target=h.queue.pop(0), daemon=True)
        worker.start()
        self.assertTrue(h.ctl.shutdown(10))
        worker.join(5)
        settle()
        self.assertEqual(h.ctl.state, "available")
        gate = threading.Event()
        self.addCleanup(gate.set)
        h.net.honor_stop = False  # 停不下来：卡在下载里，也不看停止标志
        h.net.on_chunk = lambda done: gate.wait(10)
        h.ctl.start_download()
        worker = threading.Thread(target=h.queue.pop(0), daemon=True)
        worker.start()
        self.assertFalse(h.ctl.shutdown(0.2))
        gate.set()
        worker.join(5)
        settle()


class AutoRestartTest(ErrorsCase):
    """点了「更新」、下好以后不用再点：在对局外自动重启换上（UpdateControl._arm_restart、_auto_restart）。"""

    SOON = "新版本 99.0.0 已下好，马上重启换上。"
    WAIT = "新版本 99.0.0 已下好，对局结束后自动重启换上。"

    def download(self, in_game: bool = False) -> Harness:
        h = Harness(self)
        h.available()
        h.in_game = in_game
        h.ctl.start_download()
        self.assertEqual(h.ctl.state, "ready")
        return h

    def test_out_of_game(self):
        """下好时在对局外：提示行写马上重启（没有按钮），设置窗口那一行也写这一句；过 RESTART_NOTICE_MS 才起换版本的程序
        （不在刷新的半路上退出），起来了再照常退出；识别记录里那一笔另带 auto。"""
        h = self.download()
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW, restart=updates.RESTART_SOON)])
        self.assertEqual(h.rows(), [("update", self.SOON, "info", [])])
        self.assertEqual(h.ctl.settings_note(), self.SOON)
        self.assertEqual(sorted(ms for ms, _ in h.timers), [0, updates.RESTART_NOTICE_MS])
        self.assertNotIn("launch", h.log)
        h.notices()  # 每次刷新都会问：已经排上的不再排
        self.assertEqual(len(h.timers), 2)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.apply.calls[-1], ("launch", h.root, os.getpid()))
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.assertIn(("update_restart", {"version": NEW, "auto": True}), h.traces)

    def test_ready_in_game(self):
        """下好时在对局中：不排（提示行照旧不显示，设置窗口写等对局结束）；回到对局外的那次刷新才排上，到点重启。"""
        h = self.download(in_game=True)
        self.assertEqual(h.timers, [])
        self.assertEqual(h.notices(True), [])
        self.assertEqual(h.ctl.settings_note(), self.WAIT)
        h.in_game = False
        self.assertEqual(h.rows(), [("update", self.SOON, "info", [])])
        self.assertEqual(sorted(ms for ms, _ in h.timers), [0, updates.RESTART_NOTICE_MS])
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])

    def test_back_in_game_when_due(self):
        """到点时又进了对局：不重启，接着等；这一张在对局外、另一张还在对局中时写等对局结束、不排。都回到对局外再排，到点才重启。"""
        h = self.download()
        h.in_game = True
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertNotIn("launch", h.log)
        self.assertEqual((h.ctl.state, h.ctl.restart_due, h.ctl.settings_note()), ("ready", False, self.WAIT))
        h.timers.clear()
        self.assertEqual(h.rows(), [("update", self.WAIT, "info", [])])  # playing 说还有一张在对局中
        self.assertEqual(h.timers, [])
        h.in_game = False
        h.notices()
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])

    def test_launch_failed(self):
        """自动重启时起不来换版本的程序：同「重启更新」起不来，提示原因、带「手动下载」，不退出，也不再自动重启。"""
        h = self.download()
        h.apply.result = {"status": "not_applied", "version": NEW, "reason": "新版本的程序核对没有通过", "attempts": 1}
        h.apply.launched = False
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertNotIn("quit", h.log)
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="launch")])
        self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
        self.assertFalse(h.ctl.auto_restart)

    def test_playing_error(self):
        """问对局中没有时出错：按在对局中算，不重启；错误记录只记一次。"""
        h = Harness(self)
        h.available()
        h.playing_error = RuntimeError("测试造的错")
        h.ctl.start_download()
        h.notices()
        self.assertEqual((h.ctl.state, h.timers, h.ctl.settings_note()), ("ready", [], self.WAIT))
        self.expect_errors("查是不是对局中出错，新版本先不自动重启（拾星助手仍在运行）")

    def test_prepared_needs_click(self):
        """上次已下好的那一版（检查时直接是下好了）：不自动重启，点一下「重启更新」就换。"""
        h = Harness(self)
        h.apply.ready = {"format": 1, "version": NEW}
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.check_now()
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW)])
        self.assertEqual(h.timers, [])
        self.assertEqual([b[0] for b in h.rows()[0][3]], ["重启更新", "关闭本条提示"])
        h.ctl.restart()
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.assertIn(("update_restart", {"version": NEW}), h.traces)

    WINDOWS = "新版本 99.0.0 已下好，关掉拾星助手的其他窗口后自动重启换上。"

    def test_windows_open(self):
        """下好时开着拾星助手的其他窗口：不排，提示行和设置窗口那一行写等那些窗口关掉；隔 WINDOWS_POLL_MS 看一次，还开着就
        接着等（不刷新）；都关掉了刷新一次，照常排上（提示 3 秒），到点才重启。"""
        h = Harness(self)
        h.available()
        h.windows = True
        h.ctl.start_download()
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW, restart=updates.RESTART_WINDOWS)])
        self.assertEqual(h.rows(), [("update", self.WINDOWS, "info", [])])
        self.assertEqual(h.ctl.settings_note(), self.WINDOWS)
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in h.timers])
        self.assertEqual([ms for ms, _ in h.timers].count(updates.WINDOWS_POLL_MS), 1)  # 刷新几次也只排一个
        refreshes = h.log.count("refresh")
        h.fire(updates.WINDOWS_POLL_MS)  # 还开着
        self.assertEqual(h.log.count("refresh"), refreshes)
        self.assertEqual([ms for ms, _ in h.timers], [updates.WINDOWS_POLL_MS])
        h.windows = False
        h.fire(updates.WINDOWS_POLL_MS)  # 关掉了：刷新一次（Harness 的刷新调 notices），照常排上
        self.assertEqual(h.log.count("refresh"), refreshes + 1)
        self.assertEqual(h.shown[-1], ("ready",))
        self.assertEqual(sorted(ms for ms, _ in h.timers), [0, updates.RESTART_NOTICE_MS])
        self.assertEqual(h.rows(), [("update", self.SOON, "info", [])])
        self.assertNotIn("launch", h.log)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])

    def test_windows_opened_when_due(self):
        """排上以后、到点以前开了别的窗口：到点不重启，改写等窗口关掉；关掉以后照常排上、到点重启。"""
        h = self.download()
        h.windows = True
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertNotIn("launch", h.log)
        self.assertEqual((h.ctl.restart_due, h.ctl.settings_note()), (False, self.WINDOWS))
        self.assertEqual(h.rows(), [("update", self.WINDOWS, "info", [])])
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in h.timers])
        h.windows = False
        h.fire(updates.WINDOWS_POLL_MS)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])

    def test_game_before_windows(self):
        """在对局中又开着其他窗口：照旧写等对局结束（提示行、设置窗口那一行），不看窗口。"""
        h = Harness(self)
        h.available()
        h.windows = True
        h.in_game = True
        h.ctl.start_download()
        self.assertEqual(h.ctl.settings_note(), self.WAIT)
        self.assertEqual(h.rows(), [("update", self.WAIT, "info", [])])  # playing 说还有一张在对局中
        self.assertEqual(h.timers, [])

    def test_windows_error(self):
        """问开着哪些窗口时出错：按开着算，不重启；错误记录只记一次。"""
        h = Harness(self)
        h.available()
        h.windows_error = RuntimeError("测试造的错")
        h.ctl.start_download()
        h.notices()
        self.assertEqual(h.ctl.settings_note(), self.WINDOWS)
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in h.timers])
        self.expect_errors("查拾星助手开着哪些窗口出错，新版本先不自动重启（拾星助手仍在运行）")

    def test_stop_then_ready(self):
        """点了「停止」、下载线程看到停止标志以前刚好备好了：照旧是下好了，但不自动重启，要玩家点「重启更新」。"""
        h = Harness(self, defer=True)
        h.available()
        h.drain()
        h.ctl.start_download()
        self.assertEqual(h.ctl.state, "downloading")
        h.ctl.stop_download()
        self.assertFalse(h.ctl.auto_restart)
        h.ctl._on_phase("ready", None)  # 下载线程刚好做完
        self.assertEqual(h.notices(), [UpdateNotice("ready", version=NEW)])
        self.assertEqual([b[0] for b in h.rows()[0][3]], ["重启更新", "关闭本条提示"])
        self.assertEqual(h.timers, [])

    def test_launched_once(self):
        """换版本程序起来了：不再自动重启，也不再起第二次（照常退出期间的刷新、再点「重启更新」）；提示行和设置窗口写马上重启。"""
        h = self.download()
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.assertFalse(h.ctl.auto_restart)
        h.timers.clear()
        self.assertEqual(h.rows(), [("update", self.SOON, "info", [])])
        self.assertEqual(h.ctl.settings_note(), self.SOON)
        h.ctl.restart()
        h.ctl.restart(auto=True)
        self.assertEqual((h.log.count("launch"), h.timers), (1, []))

    def test_hidden_marker(self):
        """自动重启时有悬浮窗收在托盘里：起换版本程序之前写记号（要换上的版本、换之前的版本、收着的几张）；起不来时删掉；没有
        收着的、手动点「重启更新」的不写。"""
        from advisor.ui import hidden_start

        seen = []

        def launch_seeing(h):
            real = h.apply.launch

            def launch(root, *, old_pid):
                path = hidden_start.marker_path(root)
                seen.append(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)
                return real(root, old_pid=old_pid)
            h.apply.launch = launch

        h = self.download()
        h.hidden = ("secondary", "primary", "别的")
        launch_seeing(h)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(seen[-1]["hidden"], ["primary", "secondary"])
        self.assertEqual((seen[-1]["version"], seen[-1]["from"], seen[-1]["format"]), (NEW, VERSION, 1))
        self.assertLess(abs(time.time() - seen[-1]["time"]), 60)
        marker = hidden_start.marker_path(h.root)
        self.assertTrue(marker.is_file())  # 新版本启动时读走
        # 起不来：删掉
        h = self.download()
        h.hidden = ("primary",)
        h.apply.launched = False
        launch_seeing(h)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(seen[-1]["hidden"], ["primary"])
        self.assertFalse(hidden_start.marker_path(h.root).exists())
        # 没有收着的：不写
        h = self.download()
        launch_seeing(h)
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertIsNone(seen[-1])
        # 手动「重启更新」（上次已下好的那一版）：不写
        h = Harness(self)
        h.hidden = ("primary",)
        h.apply.ready = {"format": 1, "version": NEW}
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.check_now()
        launch_seeing(h)
        h.ctl.restart()
        self.assertIsNone(seen[-1])
        self.assertEqual(h.log[-2:], ["launch", "quit"])

    def test_hidden_marker_error(self):
        """记号写不进：记进错误记录，照常重启（新版本照常显示）。"""
        h = self.download()
        h.hidden = ("primary",)
        (h.root / "update").write_text("挡着文件夹的文件", encoding="utf-8")
        h.fire(updates.RESTART_NOTICE_MS)
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.expect_errors("写「更新后收在托盘里启动」的记号出错，新版本启动时照常显示（拾星助手仍在运行）")


class HiddenStartTest(ErrorsCase):
    """收在托盘里启动的记号（advisor/ui/hidden_start.py）：写、读走（读了就删）、认不出的一律照常显示。"""

    def setUp(self):
        super().setUp()
        from advisor.ui import hidden_start

        self.hs = hidden_start
        self.root = sandbox.temp_dir(self, "test-hidden-start-")

    def put(self, **fields) -> None:
        record = {"format": 1, "version": VERSION, "from": "1.0.0", "time": time.time(), "hidden": ["primary"]}
        record.update(fields)
        path = self.hs.marker_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_round_trip(self):
        """写了以后新版本读走：拿到收着的几张，记号删掉，再读是空的。"""
        self.assertTrue(self.hs.write(self.root, version=VERSION, current="1.0.0", hidden=["secondary", "primary"]))
        self.assertEqual(self.hs.take(self.root, VERSION), frozenset({"primary", "secondary"}))
        self.assertFalse(self.hs.marker_path(self.root).exists())
        self.assertEqual(self.hs.take(self.root, VERSION), frozenset())

    def test_nothing_hidden(self):
        """没有收着的：不写，旧的删掉。"""
        self.put()
        self.assertFalse(self.hs.write(self.root, version=VERSION, current="1.0.0", hidden=()))
        self.assertFalse(self.hs.marker_path(self.root).exists())

    def test_rejected(self):
        """版本对不上（退回旧版本）、过时了、格式认不出、内容坏了、太大：照常显示（空），记号照样删掉；不认的名字去掉。"""
        cases = {
            "version": dict(version="0.0.1"),
            "stale": dict(time=time.time() - self.hs.TTL_S - 5),
            "future": dict(time=time.time() + self.hs.TTL_S + 5),
            "format": dict(format=2),
            "format_bool": dict(format=True),
            "time_text": dict(time="现在"),
            "hidden_text": dict(hidden="primary"),
        }
        for name, fields in cases.items():
            with self.subTest(name):
                self.put(**fields)
                self.assertEqual(self.hs.take(self.root, VERSION), frozenset())
                self.assertFalse(self.hs.marker_path(self.root).exists())
        for name, raw in {"broken": b"{", "big": b" " * (self.hs.LIMIT + 1), "bytes": b"\xff\xfe"}.items():
            with self.subTest(name):
                self.hs.marker_path(self.root).write_bytes(raw)
                self.assertEqual(self.hs.take(self.root, VERSION), frozenset())
                self.assertFalse(self.hs.marker_path(self.root).exists())
        self.put(hidden=["primary", "第三张", 3])
        self.assertEqual(self.hs.take(self.root, VERSION), frozenset({"primary"}))

    def test_same_folder_as_update(self):
        """记号放在更新的工作文件夹里（新版本起来以后的收尾会删掉整个文件夹，记号在那之前读走），收尾不把它当成别的下载。"""
        self.assertEqual(self.hs.UPDATE_DIR, real_apply.UPDATE_DIR)
        self.assertFalse(self.hs.NAME.lower().endswith(".part"))
        self.assertNotEqual(self.hs.NAME.lower(), real_apply.READY_NAME)
        self.assertNotEqual(self.hs.NAME.lower(), real_apply.RESULT_NAME)


class RestartTest(ErrorsCase):
    def ready(self) -> Harness:
        h = Harness(self)
        h.available()
        h.ctl.start_download()
        self.assertEqual(h.ctl.state, "ready")
        return h

    def prepared(self) -> Harness:
        """上次已下好的那一版（检查时直接是下好了）：要点「重启更新」才换。"""
        h = Harness(self)
        h.apply.ready = {"format": 1, "version": NEW}
        h.net.result = CheckResult(release=REL, latest=NEW)
        h.ctl.check_now()
        self.assertEqual(h.ctl.state, "ready")
        return h

    def test_restart(self):
        """「重启更新」：先起换版本的程序（交给它本进程号），起来了再照常退出。"""
        h = self.ready()
        h.ctl.restart()
        self.assertEqual(h.apply.calls[-1], ("launch", h.root, os.getpid()))
        self.assertEqual(h.log[-2:], ["launch", "quit"])
        self.assertIn(("update_restart", {"version": NEW}), h.traces)

    def test_launch_failed(self):
        """起不来（返回假、出错）：不退出，提示原因，带「手动下载」。返回假时换版本的模块把这一次记成了没换上：这里已经
        提示了，把那个结果读走（下次启动照常再换，不先再提示一遍没换上），次数没到上限时说下次启动时会再试一次；出错时没有
        结果可读，说不准下次启动会不会再试，不许诺（同读不到结果，见 test_launch_failed_unrecorded）。"""
        for how in ("false", "error"):
            with self.subTest(how=how):
                h = self.ready()
                h.apply.result = {"status": "not_applied", "version": NEW, "reason": "新版本的程序核对没有通过",
                                  "attempts": 1}
                if how == "error":
                    h.apply.launch_error = OSError("测试造的错")
                else:
                    h.apply.launched = False
                h.ctl.restart()
                self.assertNotIn("quit", h.log)
                taken = h.apply.calls[-1] == ("take_result", h.root)
                self.assertEqual((taken, h.apply.result is None), (how == "false",) * 2)
                failure = "launch" if how == "false" else "launch_unrecorded"
                self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure=failure)])
                if how == "false":
                    self.assertEqual(h.rows()[0][1], "⚠ 没能启动换版本的程序，新版本没有换上，拾星助手照常运行。下次启动时会"
                                                     "再试一次，也可以点击「手动下载」。")
                else:
                    self.assertEqual(h.rows()[0][1], "⚠ 没能启动换版本的程序，新版本没有换上，拾星助手照常运行，可以点击"
                                                     "「手动下载」。")
                self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
                self.assertEqual(h.ctl.settings_note(), "没能启动换版本的程序，新版本没有换上。")
                self.assertEqual(h.settings.update_skipped, "")
                if how == "error":
                    self.expect_errors("启动换版本的程序出错（拾星助手仍在运行）")

    def test_launch_failed_unrecorded(self):
        """起不来，又读不到这一次的结果（换版本的模块没记下、已经读走过、读的时候出错，或者读到的不是这一版没换上的结果）：
        说不准下次启动会不会再试，不说「下次启动时会再试一次」，只指到「手动下载」；不记进跳过。"""
        text = "⚠ 没能启动换版本的程序，新版本没有换上，拾星助手照常运行，可以点击「手动下载」。"
        cases = (("没有结果", None, None), ("读的时候出错", None, OSError("读不了")),
                 ("别的版本的", {"status": "not_applied", "version": "98.0.0", "reason": "", "attempts": 2}, None),
                 ("不是没换上", {"status": "ok", "version": NEW, "reason": "", "attempts": 0}, None))
        for label, result, error in cases:
            with self.subTest(label):
                h = self.ready()
                h.apply.launched = False
                h.apply.result, h.apply.result_error = result, error
                h.ctl.restart()
                self.assertNotIn("quit", h.log)
                self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="launch_unrecorded")])
                self.assertEqual(h.rows()[0][1], text)
                self.assertNotIn("下次启动", h.rows()[0][1])
                self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
                self.assertEqual(h.ctl.settings_note(), "没能启动换版本的程序，新版本没有换上。")
                self.assertEqual(h.settings.update_skipped, "")
                if error is not None:
                    self.expect_errors("读换版本的结果出错（拾星助手仍在运行）")

    def test_launch_failed_too_often(self):
        """起不来、读走的结果是这一版没换上的次数到了上限（update_apply.MAX_FAILED）：不说下次启动时会再试，换成指到设置窗口的
        「检查更新」的那一句，这一版记进跳过（自动检查不再提示它，手动检查照样能查到）。次数没到、次数认不出的照旧（结果是别的
        版本的见 test_launch_failed_unrecorded）。"""
        reason = "新版本的程序启动后出错退出（退出码 1）"
        h = self.ready()
        h.apply.launched = False
        h.apply.result = {"status": "not_applied", "version": NEW, "reason": reason, "attempts": 2}
        h.ctl.restart()
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="launch_final")])
        self.assertEqual(h.rows()[0][1], "⚠ 没能启动换版本的程序，新版本没有换上，拾星助手照常运行。自动检查不再提示这一版，"
                                         "需要时请点击「设置」窗口中的「检查更新」按钮再试，或者点击「手动下载」。")
        self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
        self.assertEqual(h.ctl.settings_note(), "没能启动换版本的程序，新版本没有换上。")
        self.assertEqual((h.settings.update_skipped, h.saved[-1][1]), (NEW, NEW))
        self.assertNotIn("quit", h.log)
        h.ctl.close()
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls[-1], ("check", VERSION, NEW))  # 自动检查带上跳过的版本
        h.ctl.check_now()
        self.assertEqual(h.net.calls[-1], ("check", VERSION, None))  # 手动检查照样查
        for result in ({"status": "not_applied", "version": NEW, "reason": reason, "attempts": 1},
                       {"status": "not_applied", "version": NEW, "reason": reason},
                       {"status": "not_applied", "version": NEW, "reason": reason, "attempts": True}):
            with self.subTest(result=result):
                h = self.ready()
                h.apply.launched = False
                h.apply.result = result
                h.ctl.restart()
                self.assertEqual((h.ctl.failure, h.settings.update_skipped), ("launch", ""))
                self.assertIn("下次启动时会再试一次", h.rows()[0][1])

    def test_only_when_ready(self):
        h = Harness(self)
        h.available()
        h.ctl.restart()
        self.assertEqual(([c[0] for c in h.apply.calls], h.ctl.state), (["pending"], "available"))

    def test_close(self):
        """「关闭本条提示」：上次已下好的那一条这次运行不再显示（照旧等下次启动换上）；出错、已经是最新的回到没事。"""
        h = self.prepared()
        h.ctl.close()
        self.assertEqual((h.ctl.state, h.notices(), h.ctl.busy), ("ready", [], True))
        self.assertEqual(h.ctl.settings_note(), W.UPDATE_READY_NOTE.format(version=NEW))
        for state in ("failed", "latest"):
            h.ctl.state = state
            h.ctl.close()
            self.assertEqual((h.ctl.state, h.notices()), ("idle", []))


class ResultTest(ErrorsCase):
    def test_updated(self):
        """上一次换上了：说一句已更新到现在的版本（排在新版本那一条前面），点「关闭本条提示」收起。"""
        h = Harness(self)
        h.apply.result = {"status": "ok", "version": VERSION, "reason": ""}
        h.ctl.startup()
        self.assertEqual((h.ctl.updated, h.ctl.state), (VERSION, "idle"))
        self.assertEqual(h.rows(), [("updated", W.update_done_text(VERSION), "info",
                                     [("关闭本条提示", "", "update_close_done", "关闭本条提示")])])
        self.assertIn(("update_result", {"status": "ok", "version": VERSION}), h.traces)
        h.available()
        self.assertEqual([n.kind for n in h.notices()], ["updated", "available"])
        h.ctl.close_updated()
        self.assertEqual([n.kind for n in h.notices()], ["available"])

    def test_rolled_back(self):
        """新版本启动失败、已退回：这一版记进跳过，提示原因，带「手动下载」；设置窗口写短的一句。"""
        h = Harness(self)
        h.apply.result = {"status": "rolled_back", "version": NEW, "reason": "新版本没有打开"}
        h.ctl.startup()
        self.assertEqual((h.settings.update_skipped, h.saved), (NEW, [(True, NEW)]))
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="rolled_back", reason="新版本没有打开")])
        self.assertEqual(h.rows()[0][1], "⚠ 新版本 99.0.0 启动失败，已换回原来的版本。原因：新版本没有打开。自动检查不再提示"
                                         "这一版，需要时请点击「手动下载」。")
        self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
        self.assertEqual(h.ctl.settings_note(), "新版本 99.0.0 启动失败，已换回原来的版本。")
        self.assertIn(("update_result", {"status": "rolled_back", "version": NEW}), h.traces)

    def test_updated_other_version(self):
        """读到换好了（ok）的结果，结果里却不是正在运行的这一版（换版本程序退回途中断了电这类）：不说已更新到，什么都不提示。"""
        h = Harness(self)
        h.apply.result = {"status": "ok", "version": NEW, "reason": ""}
        h.ctl.startup()
        self.assertEqual((h.ctl.updated, h.ctl.state, h.notices(), h.settings.update_skipped), (None, "idle", [], ""))
        self.assertIn(("update_result", {"status": "ok", "version": NEW}), h.traces)

    def test_not_applied(self):
        """没换上、次数还没到上限：说原因，下次启动时会再试一次，不记进跳过。"""
        h = Harness(self)
        h.apply.result = {"status": "not_applied", "version": NEW, "reason": "等不到拾星助手退出", "attempts": 1}
        h.ctl.startup()
        self.assertEqual(h.settings.update_skipped, "")
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="not_applied",
                                                    reason="等不到拾星助手退出")])
        self.assertEqual(h.rows()[0][1], "⚠ 新版本 99.0.0 没有换上，现在运行的仍是原来的版本。原因：等不到拾星助手退出。下次启动时"
                                         "会再试一次，也可以点击「手动下载」手动更新。")
        self.assertEqual(h.ctl.settings_note(), "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。")

    def test_not_applied_too_often(self):
        """同一个版本没换上的次数到了上限（update_apply.MAX_FAILED）：像退回一样记进跳过，自动检查不再提示它；换一种说法，指到
        设置窗口的「检查更新」（手动检查照样能查到、能再试）。次数认不出、结果是别的种类时照旧。"""
        self.assertEqual(FakeApply.MAX_FAILED, real_apply.MAX_FAILED)
        h = Harness(self)
        h.apply.result = {"status": "not_applied", "version": NEW, "reason": "等不到拾星助手退出",
                          "attempts": real_apply.MAX_FAILED}
        h.ctl.startup()
        self.assertEqual((h.settings.update_skipped, h.saved), (NEW, [(True, NEW)]))
        self.assertEqual(h.notices(), [UpdateNotice("failed", version=NEW, failure="not_applied_final",
                                                    reason="等不到拾星助手退出")])
        self.assertEqual(h.rows()[0][1], "⚠ 新版本 99.0.0 没有换上，现在运行的仍是原来的版本。原因：等不到拾星助手退出。自动检查"
                                         "不再提示这一版，需要时请点击「设置」窗口中的「检查更新」按钮再试，或者点击「手动下载」"
                                         "手动更新。")
        self.assertEqual(h.rows()[0][3], [MANUAL, CLOSE])
        self.assertEqual(h.ctl.settings_note(), "新版本 99.0.0 没有换上，现在运行的仍是原来的版本。")
        self.assertIn(("update_result", {"status": "not_applied", "version": NEW}), h.traces)
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls, [])  # 出错的那一条还在：不自动查
        h.ctl.close()
        h.ctl.schedule_auto()
        h.fire(updates.AUTO_CHECK_DELAY_MS)
        self.assertEqual(h.net.calls[-1], ("check", VERSION, NEW))
        for attempts in (3, None, "2", True):
            with self.subTest(attempts=attempts):
                h = Harness(self)
                h.apply.result = {"status": "not_applied", "version": NEW, "reason": "", "attempts": attempts}
                h.ctl.startup()
                skipped = NEW if attempts == 3 else ""
                self.assertEqual(h.settings.update_skipped, skipped)
                self.assertEqual(h.ctl.failure, "not_applied_final" if skipped else "not_applied")

    def test_nothing_or_odd(self):
        """没有结果、认不出的结果：什么都不提示；版本号不是文字的不记进跳过；读结果出错只记错误记录，收尾照做。"""
        for result in (None, {"status": "别的"}, ["ok"], {"status": "rolled_back", "version": 5, "reason": None}):
            with self.subTest(result=result):
                h = Harness(self)
                h.apply.result = result
                h.ctl.startup()
                self.assertEqual((h.settings.update_skipped, h.ctl.updated), ("", None))
                self.assertEqual(h.apply.calls[-1], ("after_start", h.root))
        h = Harness(self)
        h.apply.result_error = OSError("测试造的错")
        h.ctl.startup()
        self.assertEqual((h.ctl.state, h.ctl.updated, h.notices()), ("idle", None, []))
        self.assertEqual([c[0] for c in h.apply.calls], ["take_result", "after_start"])
        self.expect_errors("读上一次换版本的结果出错（拾星助手仍在运行）")

    def test_cleanup_in_background(self):
        """换版本以后的收尾放在后台做；出错只记错误记录。"""
        h = Harness(self, defer=True)
        h.ctl.startup()
        self.assertEqual([c[0] for c in h.apply.calls], ["take_result"])
        self.assertFalse(h.ctl.after_done.is_set())
        h.drain()
        self.assertEqual(h.apply.calls[-1], ("after_start", h.root))
        self.assertTrue(h.ctl.after_done.is_set())
        h = Harness(self)
        h.apply.after_error = OSError("测试造的错")
        h.ctl.startup()
        self.assertTrue(h.ctl.after_done.is_set())
        self.expect_errors("换版本以后的收尾出错（拾星助手仍在运行）")


class OpenTest(ErrorsCase):
    def test_urls(self):
        """「看看改了什么」打开改动说明，没有时打开发布页面；「手动下载」按认下的这一版的下载地址定：里面有下载镜像上的（两边
        一致时镜像在前、GitHub 在后）打开下载镜像上的压缩包，没有（镜像没拿到，或者和 GitHub 对不上，只有 GitHub 的附件）时
        改开发布页面（悬停说明跟着换），不看这次检查镜像拿没拿到；没有发布页面时照旧开压缩包。只打开发布页面和下载镜像下的
        地址，别的不开（记进错误记录）；浏览器打不开的记进错误记录。"""
        h = Harness(self)
        h.available()
        h.ctl.open_notes()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [NOTES, FakeNet.LATEST_ZIP])
        self.assertIn(("update_notes", {"version": NEW}), h.traces)
        for urls, page in (((REL.urls[0], GITHUB_ZIP), False), ((GITHUB_ZIP,), True), ((), True),
                           ((MIRROR[:-1] + ".example/releases/ACC-99.0.0.zip",), True)):
            with self.subTest(urls=urls):
                h.net.result = CheckResult(release=Release(notes=None, urls=urls), latest=NEW, mirror_ok=not page)
                h.ctl.check_now()
                self.assertEqual(h.notices()[0].page, page)
                h.opened.clear()
                h.ctl.open_notes()
                h.ctl.open_manual()
                self.assertEqual(h.opened, [PAGE, PAGE if page else FakeNet.LATEST_ZIP])
        # 这次检查镜像拿到了，认下的却只有 GitHub 的那一版（两边对不上）：开发布页面；只能手动下载的那一版「手动下载」的悬停说明
        # 照着写
        h.net.result = CheckResult(release=Release(urls=(GITHUB_ZIP,), auto=False), latest=NEW, mirror_ok=True)
        h.ctl.check_now()
        self.assertEqual(h.rows()[0][3][0], MANUAL_PAGE)
        h.opened.clear()
        h.ctl.start_download()
        self.assertEqual(h.opened, [PAGE])
        h.available(Release(page="", urls=(GITHUB_ZIP,)))
        h.opened.clear()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [FakeNet.LATEST_ZIP])
        # 不在两个前缀下的；前缀下面带「.」「..」段的（浏览器会折算掉，跑出前缀；含 %2e 的写法）、带空白、控制字符、反斜杠的
        page = updates.PAGE_PREFIX
        bad_urls = (MIRROR[:-1] + ".example/notes.txt", app_version.SOURCE_URL + ".example/x", "file:///C:/x.txt",
                    page + "../../别的仓库", page + "%2e%2e/%2E%2E/x", page + "releases/./x", MIRROR + "notes/../../x",
                    MIRROR + "notes/..%2f..%2fx", MIRROR + "notes/a b.txt", MIRROR + "notes\\x.txt",
                    MIRROR + "x\x00.txt", page[:-1], MIRROR + "\u3000x")
        for bad in bad_urls:
            with self.subTest(url=bad):
                h.available(Release(notes=bad))
                h.opened.clear()
                h.ctl.open_notes()
                self.assertEqual(h.opened, [])
                self.expect_errors("更新的地址不对，没有打开（拾星助手仍在运行）")
        # 查询、片段里的「..」不算（浏览器不折算）；前缀下面普通的地址照开
        for good in (page + "releases/tag/v1.2.3?x=../y", MIRROR + "notes/1.2.3.txt#..", MIRROR + "notes/v1..2.txt"):
            with self.subTest(url=good):
                h.available(Release(notes=good))
                h.opened.clear()
                h.ctl.open_notes()
                self.assertEqual(h.opened, [good])
        # 下载镜像不是 https 时不开（没有发布页面的那一版：「手动下载」开的是下载镜像上的压缩包）
        h.available(Release(page=""))
        with mock.patch.object(h.net, "MIRROR_BASE", "http://dl.astral-chip-compass.com/"):
            h.opened.clear()
            h.ctl.open_manual()
            self.assertEqual(h.opened, [])
            self.expect_errors("更新的地址不对，没有打开（拾星助手仍在运行）")
        h.open_ok = False
        h.ctl.open_manual()
        self.expect_errors("打不开更新的地址（拾星助手仍在运行）")
        self.assertEqual(updates.PAGE_PREFIX, app_version.SOURCE_URL + "/")
        self.assertTrue(updates.PAGE_PREFIX.startswith("https://"))

    def test_without_mirror(self):
        """这个版本不用下载镜像（update.USE_MIRROR 为假：源代码地址换成了复刻的仓库）：「手动下载」一律开发布页面（悬停说明
        跟着换）——认下的这一版的发布页面；认下的版本没有发布页面、没有认下的版本（检查失败）时开源代码仓库的发布页面
        （RELEASES_PAGE）。镜像上的压缩包（LATEST_ZIP）、改动说明这些镜像上的地址一概不开（记进错误记录）。"""
        h = Harness(self)
        h.net.USE_MIRROR = False
        # 认下的只有 GitHub 上的那一版（复刻的版本只问 GitHub）：开它的发布页面
        h.available(Release(urls=(GITHUB_ZIP,), notes=None, data_date=None))
        self.assertTrue(h.notices()[0].page)
        h.ctl.open_notes()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [PAGE, PAGE])
        # 只能手动下载的那一版：「更新」换成「手动下载」，悬停说明是开发布页面的那一句，点了开发布页面
        h.available(Release(urls=(GITHUB_ZIP,), notes=None, auto=False))
        self.assertEqual(h.rows()[0][3][0], MANUAL_PAGE)
        h.opened.clear()
        h.ctl.start_download()
        self.assertEqual(h.opened, [PAGE])
        # 下载地址里就算有镜像上的（复刻的版本不问镜像，不会有），也开发布页面
        h.available(Release(urls=(REL.urls[0], GITHUB_ZIP)))
        self.assertTrue(h.notices()[0].page)
        h.opened.clear()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [PAGE])
        # 认下的版本没有发布页面：开源代码仓库的发布页面
        h.available(Release(page="", urls=(GITHUB_ZIP,)))
        self.assertTrue(h.notices()[0].page)
        h.opened.clear()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [updates.RELEASES_PAGE])
        # 检查失败（没有认下的版本）：「手动下载」开源代码仓库的发布页面
        problem = "查不到版本信息（GitHub：没有找到正式版（HTTP 404））。请检查网络连接，稍后再试。"
        h.net.result = CheckResult(latest=None, reachable=False, mirror_ok=False, problem=problem)
        h.ctl.check_now()
        self.assertEqual(h.notices(), [UpdateNotice("failed", failure="check", reason=problem, page=True)])
        self.assertEqual(h.rows()[0][3], [MANUAL_PAGE, CLOSE])
        h.opened.clear()
        h.ctl.open_manual()
        self.assertEqual(h.opened, [updates.RELEASES_PAGE])
        self.assertEqual(updates.RELEASES_PAGE, app_version.SOURCE_URL + "/releases")
        # 镜像上的地址一概不开：改动说明、镜像上的压缩包
        for bad in (NOTES, FakeNet.LATEST_ZIP, REL.urls[0]):
            with self.subTest(url=bad):
                h.available(Release(notes=bad))
                h.opened.clear()
                h.ctl.open_notes()
                self.assertEqual(h.opened, [])
                self.expect_errors("更新的地址不对，没有打开（拾星助手仍在运行）")

    def test_same_rule_as_update_module(self):
        """界面打开地址之前的核对（updates._url_under）和认下版本文件时的核对（advisor/update.py 的 _url_under）是两份：对
        一批好地址、坏地址，在下载用到的每个前缀下都判得一样。前缀和下载镜像的地址也和 advisor/update.py 的一致。"""
        self.assertEqual(updates.PAGE_PREFIX, real_update.PAGE_PREFIX)
        self.assertEqual((FakeNet.MIRROR_BASE, FakeNet.LATEST_ZIP, FakeNet.USE_MIRROR),
                         (real_update.MIRROR_BASE, real_update.LATEST_ZIP, real_update.USE_MIRROR))
        prefixes = (real_update.PAGE_PREFIX, real_update.NOTES_PREFIX, real_update.DOWNLOAD_PREFIX, real_update.MIRROR_BASE)
        tails = ("releases/tag/v1.2.3", "notes/1.2.3.txt", "ACC-latest.zip", "", "x/", "a//b", "v1..2.txt", "..x", "x..",
                 "x?y=../z", "x#..", "x?a=%2e%2e/b", "../../别的仓库", "..", ".", "./x", "a/./b", "a/../b", "a/..", "a/.",
                 "%2e%2e/x", "%2E%2E/x", "%2e/x", "a/%2e", "..%2fx", "%2e%2e%2fx", "a%5c..", "a b", "a\tb", "a\nb", "a\\b",
                 "a\x00b", "a\x7fb", "\u3000x", "a\u200bb", "a\u2028b", "%00", "%20x")
        values = [prefix + tail for prefix in prefixes for tail in tails]
        values += [prefix[:-1] for prefix in prefixes] + [prefix.upper() for prefix in prefixes]
        values += ["http://dl.astral-chip-compass.com/x", "https://dl.astral-chip-compass.com.example/x",
                   app_version.SOURCE_URL + ".example/x", "file:///C:/x.txt", "", None, 5, b"https://x/"]
        good = bad = 0
        for prefix in prefixes:
            for value in values:
                with self.subTest(prefix=prefix, value=value):
                    verdict = updates._url_under(value, prefix)
                    self.assertEqual(verdict, real_update._url_under(value, prefix))
                    good, bad = good + verdict, bad + (not verdict)
        self.assertGreater(good, 40)  # 好的、坏的都有不少
        self.assertGreater(bad, 100)


# ------------------------------------------------------------ 启动时交给换版本的程序


class HandOverTest(ErrorsCase):
    """hand_over_update 和 run()：换版本的模块换成假的（挂在 advisor 包上，函数里的 from-import 取到的就是它），程序文件夹
    换成临时文件夹，发行版靠换掉 paths.is_frozen 模拟。"""

    def setUp(self):
        super().setUp()
        self.folder = sandbox.temp_dir(self, "test-update-ui-")
        self.apply = FakeApply()
        for patcher in (mock.patch.object(advisor, "update_apply", self.apply, create=True),
                        mock.patch.object(app_paths, "program_folder", lambda: self.folder)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def frozen(self, on: bool = True) -> None:
        patcher = mock.patch.object(app_paths, "is_frozen", lambda: on)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hand_over(self):
        """发行版里有下好的新版本：起换版本的程序（交给它本进程号），起来了返回真；没有、起不来时返回假；开发版不查。"""
        self.frozen(False)
        self.apply.ready = {"format": 1, "version": NEW}
        self.assertFalse(ui.hand_over_update())
        self.assertEqual(self.apply.calls, [])
        self.frozen(True)
        self.assertTrue(ui.hand_over_update())
        self.assertEqual(self.apply.calls, [("pending", self.folder, VERSION), ("launch", self.folder, os.getpid())])
        self.apply.calls.clear()
        self.apply.launched = False
        self.assertFalse(ui.hand_over_update())
        self.apply.calls.clear()
        self.apply.ready = None
        self.assertFalse(ui.hand_over_update())
        self.assertEqual(self.apply.calls, [("pending", self.folder, VERSION)])

    def test_error(self):
        """查、起的时候出错：记进错误记录，照常启动现在的版本。"""
        self.frozen()
        self.apply.ready = {"format": 1, "version": NEW}
        for attr in ("pending_error", "launch_error"):
            with self.subTest(step=attr):
                setattr(self.apply, attr, OSError("测试造的错"))
                self.assertFalse(ui.hand_over_update())
                setattr(self.apply, attr, None)
                self.expect_errors("启动时换上新版本出错，照常启动现在的版本")

    def run_ui(self, claim=True) -> list:
        """走一遍 run()：防多开、悬浮窗的 main()、预加载换成假的（记下调用了哪几个），不弹窗。"""
        calls = []
        with mock.patch.object(si, "claim", lambda: claim), \
                mock.patch.object(overlay_app, "main", lambda: calls.append("main")), \
                mock.patch.object(startup, "preload", lambda: calls.append("preload")), \
                mock.patch.object(ui, "report_startup_error", side_effect=AssertionError("不该弹窗")):
            self.assertIsNone(ui.run())
        return calls

    def test_run(self):
        """run()：查过防多开以后、起悬浮窗以前交给换版本的程序，交出去了就直接返回（退出码 0），不起悬浮窗；没有要换的、
        起不来时照常启动；已有一个在运行时不查。"""
        self.frozen()
        self.apply.ready = {"format": 1, "version": NEW}
        self.assertEqual(self.run_ui(claim=False), [])
        self.assertEqual(self.apply.calls, [])
        self.assertEqual(self.run_ui(), [])
        self.assertEqual([c[0] for c in self.apply.calls], ["pending", "launch"])
        self.apply.launched = False
        self.assertEqual(self.run_ui(), ["preload", "main"])
        self.apply.ready = None
        self.assertEqual(self.run_ui(), ["preload", "main"])

    def test_hidden_start(self):
        """「收在托盘里启动」的记号：发行版里 run() 查过防多开以后读走（读了就删），交给悬浮窗（main 的 start_hidden）；
        没有记号时照旧调 main()；这次启动转去换上次没换上的版本时也先读走、删掉（换好以后照常显示）；已有一个在运行、开发版
        不读。"""
        from advisor.ui import hidden_start

        self.frozen()
        got = []
        marker = hidden_start.marker_path(self.folder)

        def put():
            hidden_start.write(self.folder, version=VERSION, current="1.0.0", hidden=["primary"])

        def run(claim=True):
            with mock.patch.object(si, "claim", lambda: claim), \
                    mock.patch.object(overlay_app, "main", lambda **kw: got.append(kw)), \
                    mock.patch.object(startup, "preload", lambda: None), \
                    mock.patch.object(ui, "report_startup_error", side_effect=AssertionError("不该弹窗")):
                self.assertIsNone(ui.run())

        put()
        run(claim=False)
        self.assertEqual((got, marker.exists()), ([], True))
        run()
        self.assertEqual(got, [{"start_hidden": frozenset({"primary"})}])
        self.assertFalse(marker.exists())
        run()
        self.assertEqual(got[-1], {})
        put()
        self.apply.ready = {"format": 1, "version": NEW}
        run()
        self.assertEqual((len(got), marker.exists()), (2, False))
        self.apply.ready = None
        put()
        self.frozen(False)
        run()
        self.assertEqual((got[-1], marker.exists()), ({}, True))

    def test_hidden_start_error(self):
        """读记号时出错：记进错误记录，照常显示。"""
        from advisor.ui import hidden_start

        self.frozen()
        with mock.patch.object(hidden_start, "take", side_effect=OSError("测试造的错")):
            self.assertEqual(ui.take_hidden_start(), frozenset())
        self.expect_errors("读「更新后收在托盘里启动」的记号出错，照常显示悬浮窗")


# ------------------------------------------------------------ 悬浮窗和设置窗口

_saved_listeners: list = []
_db = None


def setUpModule():
    """同 tests/test_lowend_settings.py：运行期间换一张空的通知名单，只通知这里建的窗口；先装好随软件带的字体。"""
    global _saved_listeners
    _saved_listeners = theme._state.listeners
    theme._state.listeners = []
    for skin in theme.skins():
        theme.load_fonts(skin)


def tearDownModule():
    theme.set_skin(theme.DEFAULT_KEY)
    theme._state.listeners = _saved_listeners + theme._state.listeners
    theme.unload_fonts()


def database():
    global _db
    if _db is None:
        _db = load_db()
    return _db


def notice_row(w: Overlay, key: str):
    """悬浮窗上键为 key 的提示行（排在框里的那一个；换下来、还没删掉的不算）。"""
    for box in w.notices.boxes.values():
        for i in range(box.count()):
            row = box.itemAt(i).widget()
            if row is not None and row.objectName() == f"notice-{key}":
                return row
    return None


def click(w: Overlay, text: str) -> None:
    next(b for b in w.notices.buttons("update") if b.text() == text).click()


def fits(widget) -> bool:
    if not widget.hasHeightForWidth():
        return widget.height() >= widget.sizeHint().height()
    return widget.height() >= widget.heightForWidth(widget.width())


class OverlayTest(ErrorsCase):
    """悬浮窗上的接线：控制模块的查和下载、换版本换成假的，模拟发行版（frozen），后台跑的直接调用。"""

    def setUp(self):
        super().setUp()
        self.folder = sandbox.temp_dir(self, "test-update-ui-")
        self.path = self.folder / "state.json"

    def make(self, frozen: bool = True) -> Overlay:
        session = OverlaySession(database(), UiSettings(hero="凛"), self.path)
        session.game.seen_hero = session.settings.hero
        w = Overlay(database(), session, threading.Lock(), services=Services())
        w.run_background = lambda fn: fn()
        self.addCleanup(dispose, w)
        u = w.updates
        self.net, self.apply = FakeNet(), FakeApply()
        u.net, u.apply = self.net, self.apply
        u.frozen = lambda: frozen
        u.root = lambda: self.folder
        self.opened = []
        u.open_url = lambda url: self.opened.append(url) or True
        return w

    def test_buttons(self):
        """提示行的按钮接到控制模块上：「看看改了什么」「以后再说」「跳过这个版本」（存进设置文件）「更新」（下好以后自动
        重启，没有按钮）；上次已下好的那一版点「重启更新」。"""
        w = self.make()
        u = w.updates
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.check_updates()
        self.assertEqual(w.notice_model.texts()["update"], update_rows(u.notices())[0][1])
        self.assertEqual([b.text() for b in w.notices.buttons("update")], ["更新", "看看改了什么", "跳过这个版本", "以后再说"])
        click(w, "看看改了什么")
        self.assertEqual(self.opened, [NOTES])
        click(w, "以后再说")
        self.assertNotIn("update", w.notice_model.texts())
        w.check_updates()
        click(w, "跳过这个版本")
        self.assertNotIn("update", w.notice_model.texts())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["update_skipped"], NEW)
        w.check_updates()  # 手动检查：跳过的版本照样提示
        u.later = lambda ms, fn: None  # 自动重启排在稍后，测试里不做
        click(w, "更新")
        self.assertEqual(u.state, "ready")
        self.assertEqual(w.notices.buttons("update"), [])  # 下好以后自动重启，没有按钮
        # 上次已下好的那一版：要点「重启更新」才换
        u.state = "available"
        u.hidden = False
        self.apply.ready = {"format": 1, "version": NEW}
        w.check_updates()
        self.assertEqual([b.text() for b in w.notices.buttons("update")], ["重启更新", "关闭本条提示"])
        quits = []
        u.quit_app = lambda: quits.append(1)
        click(w, "重启更新")
        self.assertEqual(([c[0] for c in self.apply.calls if c[0] != "pending"], quits), (["launch"], [1]))
        u.state = "failed"
        w.refresh()
        click(w, "手动下载")
        self.assertEqual(self.opened[-1], FakeNet.LATEST_ZIP)
        click(w, "关闭本条提示")
        self.assertNotIn("update", w.notice_model.texts())

    def test_layout(self):
        """有新版本（四个按钮）、下好了、出错（两个按钮）：文字占一行，按钮排在文字下面；下载中（比对本机的文件、只下变了的部分、
        下整包几句）、已经是最新照旧跟在文字右边。按钮都在悬浮窗里，文字放得下。"""
        w = self.make()
        w.show()
        u = w.updates
        u.release = DELTA
        u.failure, u.reason = "download", "下载的文件和发布的不一致（SHA-256 对不上），已删掉"
        for state, below, step, progress in (
                ("available", True, "", (0, 0)), ("ready", True, "", (0, 0)), ("failed", True, "", (0, 0)),
                ("downloading", False, "full", (3 * MB, SIZE)), ("downloading", False, "compare", (0, 0)),
                ("downloading", False, "delta", (3 * MB, PART)), ("downloading", False, "delta", (0, 0)),
                ("latest", False, "", (0, 0))):
            with self.subTest(state=state, step=step):
                u.state, u.step, u.progress = state, step, progress
                w.refresh()
                settle()
                row = notice_row(w, "update")
                label = next(lb for lb in row.findChildren(QLabel) if lb.objectName().startswith("notice_"))
                buttons = w.notices.buttons("update")
                self.assertTrue(buttons)
                for b in buttons:
                    if below:
                        self.assertGreaterEqual(b.mapTo(row, QPoint(0, 0)).y(), label.mapTo(row, QPoint(0, label.height())).y())
                    else:
                        self.assertGreaterEqual(b.mapTo(row, QPoint(0, 0)).x(), label.mapTo(row, QPoint(label.width(), 0)).x())
                    self.assertLessEqual(b.mapTo(w, QPoint(b.width(), 0)).x(), w.width())
                self.assertTrue(fits(label), label.text())

    def test_held_in_game(self):
        """本局进行中（读到了轮次）先攒着不显示，设置窗口那一句照写；找不到游戏窗口时不算在对局中；中途退出、认不出主菜单
        （连着一阵子看不到对局里的读数）也不算；本局结束后照常显示。"""
        w = self.make()
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.session.settings.round = 5
        w.check_updates()
        self.assertEqual(w.updates.state, "available")
        self.assertNotIn("update", w.notice_model.texts())
        self.assertEqual(w.settings_values().update_note, W.UPDATE_FOUND.format(version=NEW))
        w.follower.activity = "nogame"
        w.refresh()
        self.assertIn("update", w.notice_model.texts())
        w.follower.activity = None
        w.refresh()
        self.assertNotIn("update", w.notice_model.texts())
        w.session.game_quiet = True
        w.refresh()
        self.assertIn("update", w.notice_model.texts())
        w.session.game_quiet = False
        w.refresh()
        self.assertNotIn("update", w.notice_model.texts())
        w.session.settings.round = None
        w.refresh()
        self.assertIn("update", w.notice_model.texts())

    def hold_restart(self, w: Overlay) -> tuple[list, list]:
        """自动重启排的定时器记下来（到点由 fire_restart 做），退出换成记一笔。返回（定时器, 退出）。"""
        timers, quits = [], []
        w.updates.later = lambda ms, fn: timers.append((ms, fn))
        w.updates.quit_app = lambda: quits.append(1)
        return timers, quits

    @staticmethod
    def fire_restart(timers: list) -> None:
        due = [fn for ms, fn in timers if ms == updates.RESTART_NOTICE_MS]
        timers[:] = [(ms, fn) for ms, fn in timers if ms != updates.RESTART_NOTICE_MS]
        for fn in due:
            fn()

    def launches(self) -> int:
        return [c[0] for c in self.apply.calls].count("launch")

    def test_auto_restart_waits_for_game(self):
        """点了「更新」、下好时本局进行中（读到了轮次）：不排自动重启，提示行照旧不显示，设置窗口写等对局结束；本局结束后的
        那次刷新才排上、提示行写马上重启（没有按钮）；到点前又进了对局就不重启、接着等，回到对局外再排，到点才起换版本的程序、
        退出。"""
        w = self.make()
        timers, quits = self.hold_restart(w)
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.check_updates()
        w.session.settings.round = 5
        w.refresh()
        w.updates.start_download()  # 下载在对局中照常进行（「更新」是对局外点的）
        self.assertEqual(w.updates.state, "ready")
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
        self.assertNotIn("update", w.notice_model.texts())
        self.assertEqual(w.settings_values().update_note, "新版本 99.0.0 已下好，对局结束后自动重启换上。")
        w.session.settings.round = None
        w.refresh()
        self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，马上重启换上。")
        self.assertEqual(w.notices.buttons("update"), [])
        self.assertEqual([ms for ms, _ in timers].count(updates.RESTART_NOTICE_MS), 1)
        w.session.settings.round = 6  # 到点之前又进了对局
        w.refresh()
        self.fire_restart(timers)
        self.assertEqual((self.launches(), quits, w.updates.state), (0, [], "ready"))
        w.session.settings.round = None
        w.refresh()
        self.fire_restart(timers)
        self.assertEqual((self.launches(), quits), (1, [1]))

    def test_auto_restart_waits_while_quiet(self):
        """本局还没结束、连着一阵子看不到对局里的读数（game_quiet：按 Esc 停在菜单上等）时下好：提示行照常显示、写等对局结束，
        不排自动重启，再刷新几次也不排；认出这一局结束了（game_over）、或者游戏窗口关了（nogame）以后的那次刷新才排上，
        提示行写马上重启，到点起换版本的程序、退出。"""
        for case in ("over", "nogame"):
            with self.subTest(case):
                w = self.make()
                timers, quits = self.hold_restart(w)
                self.net.result = CheckResult(release=REL, latest=NEW)
                w.check_updates()
                w.session.settings.round = 5
                w.session.game_quiet = True
                w.refresh()
                w.updates.start_download()
                self.assertEqual(w.updates.state, "ready")
                for _ in range(3):
                    w.refresh()
                self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，对局结束后自动重启换上。")
                self.assertEqual(w.settings_values().update_note, "新版本 99.0.0 已下好，对局结束后自动重启换上。")
                self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
                if case == "over":
                    w.session.game.game_over = True
                else:
                    w.follower.activity = "nogame"
                w.refresh()
                self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，马上重启换上。")
                self.assertEqual([ms for ms, _ in timers].count(updates.RESTART_NOTICE_MS), 1)
                self.fire_restart(timers)
                self.assertEqual((self.launches(), quits), (1, [1]))

    def test_auto_restart_two_lanes(self):
        """两个客户端同时识别：第二张还在对局中时，第一张在对局外也不自动重启（提示行写等对局结束）；第二张回到对局外的那次
        刷新排上，到点起换版本的程序、退出（更新只有第一张那一份，两张一起关）。"""
        w = self.make()
        timers, quits = self.hold_restart(w)
        session = OverlaySession(database(), UiSettings(hero="凛"), self.folder / "state-taptap.json")
        session.game.seen_hero = session.settings.hero
        second = Overlay(database(), session, threading.Lock(), services=Services(), primary=w,
                         platform=overlay_app.TAPTAP)
        second.run_background = lambda fn: fn()
        self.addCleanup(dispose, second)
        self.assertIs(second.updates, w.updates)
        lanes = SimpleNamespace(all=lambda: [SimpleNamespace(window=win, session=win.session, watcher=None,
                                                             primary=win is w) for win in (w, second)])
        w.lanes = second.lanes = lanes
        second.session.settings.round = 5
        second.refresh()
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.check_updates()
        click(w, "更新")
        self.assertEqual(w.updates.state, "ready")
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
        self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，对局结束后自动重启换上。")
        w.refresh()  # 第一张再刷新几次也不排
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
        second.session.settings.round = None
        second.refresh()
        self.assertEqual([ms for ms, _ in timers].count(updates.RESTART_NOTICE_MS), 1)
        self.fire_restart(timers)
        self.assertEqual((self.launches(), quits), (1, [1]))

    def test_auto_restart_read_error(self):
        """取快照时有几项读出错的那一轮（view.read_error 不空）、整块出错按默认快照画的那一轮：自动重启当对局中，不排；提示行
        照常显示（写等对局结束）。读对了的下一轮照常排上，到点重启。"""
        import dataclasses

        real = overlay_module.take_view

        def partly(*args, **kwargs):
            return dataclasses.replace(real(*args, **kwargs), read_error="测试造的错")

        for case in ("partly", "whole"):
            with self.subTest(case):
                w = self.make()
                timers, quits = self.hold_restart(w)
                self.net.result = CheckResult(release=REL, latest=NEW)
                w.check_updates()
                if case == "partly":
                    broken = mock.patch.object(overlay_module, "take_view", partly)
                else:
                    broken = mock.patch.object(w, "_take_view", side_effect=RuntimeError("测试造的错"))
                with broken:
                    w.refresh()
                    w.updates.start_download()
                    w.refresh()
                self.assertEqual(w.updates.state, "ready")
                self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
                self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，对局结束后自动重启换上。")
                self.errors.clear()  # 读出错照常记进错误记录（这里不核对标题）
                w.refresh()
                self.assertEqual([ms for ms, _ in timers].count(updates.RESTART_NOTICE_MS), 1)
                self.fire_restart(timers)
                self.assertEqual((self.launches(), quits), (1, [1]))
                self.apply.calls.clear()

    def test_auto_restart_waits_for_windows(self):
        """开着拾星助手的其他窗口（这里是一个对话框）时不自动重启，提示行和设置窗口那一行写等那些窗口关掉；关掉以后隔一会儿
        看到了，刷新一次照常排上，到点重启。"""
        from PySide6.QtWidgets import QDialog

        w = self.make()
        w.show()
        settle()
        timers, quits = self.hold_restart(w)
        dialog = QDialog()
        self.addCleanup(dispose, dialog)
        dialog.show()
        settle()
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.check_updates()
        click(w, "更新")
        self.assertEqual(w.updates.state, "ready")
        self.assertNotIn(updates.RESTART_NOTICE_MS, [ms for ms, _ in timers])
        text = "新版本 99.0.0 已下好，关掉拾星助手的其他窗口后自动重启换上。"
        self.assertEqual(w.notice_model.texts()["update"], text)
        self.assertEqual(w.settings_values().update_note, text)
        dialog.hide()
        due = [fn for ms, fn in timers if ms == updates.WINDOWS_POLL_MS]
        timers[:] = [(ms, fn) for ms, fn in timers if ms != updates.WINDOWS_POLL_MS]
        for fn in due:
            fn()
        self.assertEqual(w.notice_model.texts()["update"], "新版本 99.0.0 已下好，马上重启换上。")
        self.fire_restart(timers)
        self.assertEqual((self.launches(), quits), (1, [1]))

    def test_other_windows(self):
        """开着其他窗口按窗口的种类认：看得见的对话框（最小化的也算）算；悬浮窗自己、藏着的、工具提示、弹出的菜单和列表、
        不接收焦点的小提示框（收到托盘时的提示）不算。"""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog, QWidget

        w = self.make()
        w.show()
        settle()
        self.assertFalse(w._other_windows_open())
        dialog = QDialog()
        self.addCleanup(dispose, dialog)
        dialog.show()
        settle()
        self.assertTrue(w._other_windows_open())
        dialog.showMinimized()
        settle()
        self.assertTrue(w._other_windows_open())
        dialog.hide()
        self.assertFalse(w._other_windows_open())
        for flags in (Qt.ToolTip, Qt.Popup, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus):
            with self.subTest(flags=flags):
                tip = QWidget(None, flags)
                self.addCleanup(dispose, tip)
                tip.show()
                settle()
                self.assertFalse(w._other_windows_open())
                tip.hide()

    def test_hidden_lanes(self):
        """自动重启时哪几张收在托盘里：两张按各自藏没藏着算；没有 Lanes 时只看自己。"""
        w = self.make()
        second = mock.Mock()
        second.isHidden.return_value = False
        lanes = SimpleNamespace(all=lambda: [SimpleNamespace(window=w, primary=True),
                                             SimpleNamespace(window=second, primary=False)])

        def hidden():
            w.lanes = lanes  # 只在问的时候接上：显示、藏起悬浮窗时别的地方也会看 lanes
            try:
                return w._hidden_lanes()
            finally:
                w.lanes = None

        self.assertTrue(w.isHidden())  # 没显示过：同收在托盘里
        self.assertEqual(hidden(), ("primary",))
        second.isHidden.return_value = True
        self.assertEqual(hidden(), ("primary", "secondary"))
        w.show()
        settle()
        self.assertEqual(hidden(), ("secondary",))
        self.assertEqual(w._hidden_lanes(), ())
        w.hide()
        self.assertEqual(w._hidden_lanes(), ("primary",))

    def test_updated_kept_while_hidden(self):
        """收在托盘里启动时，刚换上新版本那一条留着：收着时刷新几次都不算显示过，显示出来照样看得到，点了关闭才收起。"""
        w = self.make()
        self.apply.result = {"status": "ok", "version": VERSION, "reason": ""}
        w.updates.later = lambda ms, fn: None
        w.updates.startup()
        self.assertTrue(w.isHidden())
        for _ in range(3):
            w.refresh()
        self.assertEqual(w.updates.updated, VERSION)
        w.show()
        settle()
        w.refresh()
        self.assertIn(W.UPDATE_DONE.format(version=VERSION), "\n".join(w.notice_model.texts().values()))

    def test_area_and_order(self):
        """标题栏下方的全局提示里：刚换上新版本的、有新版本的，排在「免费软件」声明前面；都是说明（不让边框变色）。"""
        w = self.make()
        self.apply.result = {"status": "ok", "version": VERSION, "reason": ""}
        w.updates.startup()
        w.notice_model.free_notice = "免费软件"
        self.net.result = CheckResult(release=REL, latest=NEW)
        w.check_updates()
        keys = [k for k in w.notice_model.texts() if k in ("updated", "update", "free")]
        self.assertEqual(keys, ["updated", "update", "free"])
        for key in ("updated", "update"):
            self.assertEqual(w.notice_model.area(key), "global")
            self.assertEqual(w.notice_model.notices[key][2], "info")
        self.assertEqual(w.diagnostic_state()["update"], {"state": "available", "version": NEW, "failure": None,
                                                          "reason": None})

    def test_settings_window(self):
        """设置窗口：开关存进设置文件；「检查更新」查一次，结果代替说明写在这一行，悬浮窗上那一条关了就收起；下载中不能点。"""
        w = self.make()
        u = w.updates
        w.dialogs.show_settings()
        d = w.dialogs.settings
        self.addCleanup(dispose, d)
        settle()
        parts = d.rows["update"]
        self.assertEqual((d.update_switch.isChecked(), d.update_btn.isEnabled()), (True, True))
        self.assertEqual((parts.desc.isVisibleTo(d), parts.note.isVisibleTo(d)), (True, False))
        d.update_switch.click()
        self.assertFalse(u.auto_on())
        self.assertIs(json.loads(self.path.read_text(encoding="utf-8"))["update_auto"], False)
        self.assertFalse(d.update_switch.isChecked())
        d.update_btn.click()
        self.assertEqual(parts.note.text(), f"已经是最新版本（{VERSION}）。")
        self.assertEqual((parts.desc.isVisibleTo(d), parts.note.isVisibleTo(d)), (False, True))
        self.assertIn("update", w.notice_model.texts())
        click(w, "关闭本条提示")
        self.assertEqual((parts.desc.isVisibleTo(d), parts.note.isVisibleTo(d)), (True, False))
        u.release, u.state, u.progress = REL, "downloading", (MB, SIZE)
        w.dialogs.update_settings_dialog()
        self.assertFalse(d.update_btn.isEnabled())
        self.assertEqual(parts.note.text(), "正在下载新版本 99.0.0（1 / 85 MB）")

    def test_dev_version(self):
        """开发版：「检查更新」只说一句开发版不检查更新；启动时不读结果、不排自动检查。"""
        w = self.make(frozen=False)
        w.dialogs.show_settings()
        d = w.dialogs.settings
        self.addCleanup(dispose, d)
        d.update_btn.click()
        self.assertEqual(d.rows["update"].note.text(), "开发版不检查更新。")
        w.updates.startup()
        self.assertEqual((self.net.calls, self.apply.calls), ([], []))

    def test_exit(self):
        """退出时（shutdown）叫停新版本的下载、等一会儿，没停下记一笔；「重启更新」的照常退出是退出事件循环。"""
        w = self.make()
        waited = []
        w.updates.shutdown = lambda timeout: waited.append(timeout) or False
        self.assertTrue(w.shutdown())
        self.assertEqual(waited, [overlay_module.SHUTDOWN_DOWNLOAD_S])
        self.expect_errors(f"退出时新版本的下载没有在 {overlay_module.SHUTDOWN_DOWNLOAD_S:g} 秒内停下")
        fake = mock.Mock()
        with mock.patch.object(overlay_module, "QApplication", fake):
            w.updates.quit_app()  # 悬浮窗交给控制模块的「照常退出」
        fake.instance.return_value.quit.assert_called_once_with()

    def test_main_order(self):
        """main()：悬浮窗显示出来、启动时的几项检查做完以后才读上一次的结果、排自动检查。悬浮窗在每一张的接线
        （Lanes._wire_lane）里显示，第一张的接线 Lanes.wire_primary 排在读上一次的结果以前。"""
        source = inspect.getsource(overlay_app.main)
        self.assertIn("window.show()", inspect.getsource(overlay_lanes.Lanes._wire_lane))
        self.assertIn("self._wire_lane(self._primary)", inspect.getsource(overlay_lanes.Lanes.wire_primary))
        self.assertLess(source.index("lanes.wire_primary()"), source.index("window.updates.startup()"))
        self.assertLess(source.index("window.notices.check_calibration()"), source.index("window.updates.startup()"))
        self.assertLess(source.index("window.updates.startup()"), source.index("app.exec()"))
        # 收在托盘里启动：托盘图标建好以后才定（建不出来时照常显示），在事件循环转起来以前
        self.assertLess(source.index("window.tray_part.setup_tray()"), source.index("lanes.finish_hidden_start(tray)"))
        self.assertLess(source.index("lanes.finish_hidden_start(tray)"), source.index("app.exec()"))
        self.assertIn("hidden=start_hidden", source)


class Host:
    """设置窗口的悬浮窗替身：记下按名字调用了什么。"""

    def __init__(self):
        self.calls = []

    def set_update_auto(self, on):
        self.calls.append(("set_update_auto", on))

    def check_updates(self):
        self.calls.append(("check_updates",))


class SkinRowTest(unittest.TestCase):
    """七套皮肤的设置窗口（同 tests/test_lowend_settings.py：正文字体同程序启动时，结束时换回默认皮肤和原来的字体，建窗口、
    换皮肤时出的错都算失败；窗口高度不受测试屏幕的高度限制）。"""

    def setUp(self):
        self.old_font = QFont(APP.font())
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(theme.app_font())
        self.errors = capture_errors(self)
        self.addCleanup(self._restore)
        patcher = mock.patch.object(st.SettingsDialog, "max_height", lambda d: 5000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        theme.set_skin(theme.DEFAULT_KEY)
        APP.setFont(self.old_font)
        settle()
        self.assertEqual(self.errors.entries(), [], "出错（错误记录）")

    def window(self, key: str, host) -> st.SettingsDialog:
        theme.set_skin(key)
        d = st.SettingsDialog(SimpleCapture("window"), host=host)
        self.addCleanup(dispose, d)
        d.show()
        settle()
        return d

    def test_row_in_every_skin(self):
        """每套都有这一行：标题、说明（写当前版本号）照文字常量，开关和「检查更新」在这一行里、看得见、不超出窗口；在诊断包
        后面（分页的在「关于」页，顶上一排页签的那套另有分组标题）；检查的结果代替说明显示、放得下；忙的时候「检查更新」
        不能点；点开关、点按钮按名字调用悬浮窗的方法。"""
        for skin in theme.skins():
            with self.subTest(skin=skin.key):
                host = Host()
                d = self.window(skin.key, host)
                parts = d.rows["update"]
                page = d.page
                if d.tabs:
                    d.tabs[st.TAB_ABOUT].click()
                    settle()
                    page = d.page.findChild(QStackedWidget, "sidePages").currentWidget()
                    self.assertTrue(page.isAncestorOf(parts.row))
                self.assertEqual((parts.title.text(), parts.desc.text()),
                                 (st.UPDATE_TITLE, st.UPDATE_DESC.format(version=VERSION)))
                if parts.icon is not None:
                    self.assertEqual(parts.icon.code, glyphs.ICON_UPDATE)
                for widget in (d.update_switch, d.update_btn):
                    self.assertTrue(parts.row.isAncestorOf(widget))
                    self.assertTrue(widget.isVisible())
                    self.assertLessEqual(widget.mapTo(d, QPoint(widget.width(), 0)).x(), d.width())
                self.assertEqual(d.update_btn.text(), "检查更新")
                self.assertEqual((parts.desc.isVisible(), parts.note.isVisible()), (True, False))
                at = lambda part: (part.mapTo(page, QPoint(0, 0)).y(), part.mapTo(page, QPoint(0, 0)).x())  # noqa: E731
                self.assertGreater(at(parts.row), at(d.rows["export"].row))
                if "about" in d.rows:
                    self.assertGreater(at(d.rows["about"].row)[0], at(parts.row)[0])
                if d.layout_name() == "top_tabs":
                    titles = {t.text(): at(t) for t in page.findChildren(chrome.SectionTitle)}
                    self.assertLess(at(d.rows["export"].row), titles[st.UPDATE_CARD])
                    self.assertLess(titles[st.UPDATE_CARD], at(parts.row))
                # 已经是最新，下载中较长的两句（比对本机的文件、只下变了的部分），和下好以后最长的一句（等拾星助手的其他窗口
                # 关掉：开着设置窗口时写的就是它）
                for note in (W.UPDATE_LATEST.format(version=VERSION), COMPARING,
                             W.update_download_text(NEW, 12 * MB, PART, "delta"),
                             W.UPDATE_READY_WINDOWS.format(version=NEW)):
                    d.set_values(st.SettingsValues(update_note=note))
                    settle()
                    self.assertEqual((parts.desc.isVisible(), parts.note.isVisible()), (False, True))
                    self.assertTrue(fits(parts.note), note)
                    if d.tabs:  # 分页的窗口高度写死：这一行在页里放得下
                        self.assertLessEqual(parts.row.mapTo(page, QPoint(0, parts.row.height())).y(), page.height(),
                                             note)
                d.update_switch.click()
                d.update_btn.click()
                self.assertEqual(host.calls, [("set_update_auto", False), ("check_updates",)])
                d.set_values(st.SettingsValues(update_busy=True))
                self.assertFalse(d.update_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
