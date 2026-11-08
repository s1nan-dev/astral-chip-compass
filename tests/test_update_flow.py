"""软件内更新从发版到下好走一遍，两边都用真的代码（不用替身、不联网）。

发版这一边：打包脚本（tools/build_release.py 的 make_zip、make_files_manifest，自检 files_manifest_problems 照跑）把一个小的
假发行版文件夹压成发行包、在旁边生成文件清单；上传脚本准备上传（tools/upload_r2.py 的 Upload.prepare：核对本地的发行包和
清单，算好要传的几样，latest.json 也在里面；令牌文件是这里造的假值，不连 R2，也不核对 GitHub）。这几样照上传脚本用的名字放到
127.0.0.1 上的三台假服务器：下载镜像（latest.json、发行包、文件清单、改动说明）、GitHub（接口回发布的这一版，附件是发行包和
清单；附件的地址跳到文件服务器）、文件服务器。发行包和清单都认 Range（bytes=起-止，回 206 和 Content-Range）。

程序这一边：悬浮窗的控制模块（UpdateControl）用真的 advisor/update.py 查、下，用真的 advisor/update_apply.py 认下好的版本；
下载线程是真的线程（同悬浮窗的 run_background），结果经信号排队回到界面线程。本机是旧版本（几个文件不一样，另有新版本里
没有的旧文件和用户数据）。
- 有文件清单：查到新版本（清单的三项都认出来，「更新」写成最多约多少）→ 点「更新」→ 比对本机的文件 → 只按段取变了的那几个
  → 最后核对 → 下好；这次运行没换上（例如没等到自动重启就退出了）、下次启动再查到这一版时直接是「重启更新」，不再下载；
- 没有清单（加清单以前的 latest.json，GitHub 上也没有清单附件）：下整包 → 解压核对 → 下好；
- 有清单、镜像不认 Range：换 GitHub 的地址（跳到文件服务器，Range 头照样带着）按段取，照样只下变了的那几个；
- 有清单、两边都不认 Range：比对以后改下整包（记一条错误记录，不提示），照样下好；
- 按段取的时候点「停止」：回到有新版本那一条，什么都不留下，不改下整包；
- 复刻的版本（不用下载镜像）：镜像上照样放着这一版，却一次都不请求，查、下清单、按段取都只走 GitHub，照样下好。
每一遍都核对界面线程收到的 phase 和进度，提示行和设置窗口那一行的文字和先后，update\\new 和整包解开的一字不差，换版本的模块
认这份 ready.json（update_apply.pending），update 以外的没被碰，以及每台服务器收到了哪些请求。

advisor/update.py 里指向下载镜像、GitHub 的几个地址换成本机的（发布页照真的写）。「是不是发行版」：控制模块给 frozen；换版本的
模块认 ready.json 时临时换掉 paths.is_frozen（同 tests/test_update_apply.py）。造的 ACC.exe 不是真的程序：exe_version 换成假的
（同 tests/test_update.py）。
"""
from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import random
import re
import threading
import time
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QEventLoop, QObject, QTimer, Slot

from advisor import paths as app_paths
from advisor import update, update_apply, update_files, winhttp
from advisor import version as app_version
from advisor.ui.overlay_parts import wording as W
from advisor.ui.overlay_parts.app import UiSettings
from advisor.ui.overlay_parts.notices import update_rows
from advisor.ui.overlay_parts.updates import UpdateControl
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tools import build_release, upload_r2

APP = qtenv.app()
NEW_VERSION = "99.0.0"  # 新版本：比现在的版本号大得多，改了版本号照样成立
ZIP = upload_r2.zip_name(NEW_VERSION)
FILES = update_files.manifest_name(NEW_VERSION)
REPO = urllib.parse.urlsplit(app_version.SOURCE_URL).path  # 仓库在 GitHub 上的路径
API_PATH = f"/repos{REPO}/releases/latest"  # update.GITHUB_API 的路径
DOWNLOAD_PATH = f"{REPO}/releases/download/"  # 发布页附件的地址前缀（update.DOWNLOAD_PREFIX）的路径
PAGE = f"{app_version.SOURCE_URL}/releases/tag/v{NEW_VERSION}"
NOTES_TEXT = "这一版改了什么\n- 改了一处\n"
MB = W.MB
WAIT_MS = 30000
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")


def rand(seed: int, n: int) -> bytes:
    """压不动的数据（随机字节），同一个 seed 每次一样。"""
    return random.Random(seed).randbytes(n)


def text(seed: int, n: int) -> bytes:
    """压得动的文字（中英文混着），正好 n 字节。"""
    rnd = random.Random(seed)
    words = ("筹码", "角色", "推荐", "回合", "alpha", "beta", "0123", "\n")
    out = bytearray()
    while len(out) < n:
        out += rnd.choice(words).encode("utf-8") + b" "
    return bytes(out[:n])


# 新版本的发行版文件夹（AstralChipCompass 下面）：主程序和一份资料变了，另有一个新文件；运行库、界面库、模型没变。合计约
# 7 MB；主程序 2.6 MB，只下变了的部分时进度跨过几个整 MB，界面上的数一格一格往上走
NEW = {
    f"{app_version.APP_NAME}.exe": rand(1, 40_000),
    "使用说明.html": text(2, 20_000),
    "app/ACC.exe": rand(3, 2_600_000),
    "app/python314.dll": rand(4, 3_000_000),
    "app/qt/界面库.dll": rand(5, 300_000),
    "app/models/det.onnx": rand(6, 1_200_000),
    "app/data/角色.json": text(7, 30_000),
    "app/data/新文件.json": text(8, 5_000),
}
# 本机的旧版本：主程序变了（大小也变了），一份资料大小一样、内容变了，新文件本机没有；另有新版本里没有的旧文件和用户数据
OLD = {**{name: data for name, data in NEW.items() if name != "app/data/新文件.json"},
       "app/ACC.exe": rand(13, 2_500_000),
       "app/data/角色.json": text(14, 30_000),
       "app/old.dll": rand(15, 1_000),
       "app/logs/today.log": b"log\n"}
CHANGED = ("app/ACC.exe", "app/data/角色.json", "app/data/新文件.json")


def make_files(folder: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = folder.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def tree(folder: Path) -> dict[str, bytes | None]:
    """folder 下的每一项：相对路径 → 文件内容（文件夹为 None）。"""
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def unpacked(blob: bytes) -> dict[str, bytes | None]:
    """整包解开、去掉 AstralChipCompass/ 那一层以后的每一项（写法同 tree，含各级文件夹）：备好的 new 要和它一字不差。"""
    top = update.ZIP_ROOT + "/"
    out: dict[str, bytes | None] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            rel = info.filename[len(top):].rstrip("/")
            parts = rel.split("/")
            for depth in range(1, len(parts)):
                out["/".join(parts[:depth])] = None
            out[rel] = None if info.is_dir() else zf.read(info)
    return out


def compressed(blob: bytes, names) -> int:
    """这几个文件在整包里的压缩字节数（只下变了的文件时要取的总数）。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return sum(zf.getinfo(f"{update.ZIP_ROOT}/{name}").compress_size for name in names)


def pump_until(condition, timeout_ms: int = WAIT_MS) -> bool:
    """跑界面线程的事件循环，直到 condition() 为真；超时返回假。用的是 QEventLoop（同程序里的 QApplication.exec：等的时候放开
    GIL，下载线程照常跑），不用 tests.support.waits.wait_until：它用的 QTest.qWait 等的时候不放开 GIL，下载线程每读一块都要
    等它，几 MB 就要十几秒。"""
    if condition():
        return True
    deadline = time.monotonic() + timeout_ms / 1000
    loop, timer = QEventLoop(), QTimer()
    timer.timeout.connect(lambda: (condition() or time.monotonic() > deadline) and loop.quit())
    timer.start(5)
    loop.exec()
    timer.stop()
    return bool(condition())


def changes(seq: list) -> list:
    """去掉紧挨着的重复（界面刷新时样子没变的那几次）。"""
    out = []
    for item in seq:
        if not out or out[-1] != item:
            out.append(item)
    return out


_BUILT: dict[str, Path] = {}


def built() -> tuple[Path, Path]:
    """打包脚本压出来的发行包和它旁边的文件清单（整个模块只打一次，放在沙箱里，进程结束时删掉）。打包的自检没过就报错。"""
    if not _BUILT:
        base = sandbox.temp_dir(None, "update-flow-build-")
        folder = base / app_paths.FOLDER_NAME
        make_files(folder, NEW)
        zip_path = base / "dist" / ZIP
        zip_path.parent.mkdir()
        with mock.patch.object(build_release, "APP_DIR", folder), mock.patch.object(build_release, "ZIP", zip_path), \
                mock.patch.object(build_release, "VERSION", NEW_VERSION), contextlib.redirect_stdout(io.StringIO()):
            build_release.make_zip()
            record = build_release.make_files_manifest()
            problems = build_release.files_manifest_problems(record)
        if problems:
            raise AssertionError(f"打包的自检没过：{problems}")
        _BUILT.update(zip=zip_path, files=Path(record["path"]))
    return _BUILT["zip"], _BUILT["files"]


def no_proxy(case: unittest.TestCase) -> None:
    """本机测试不经过代理（同 tests/test_update.py）：清掉环境变量里的代理，getproxies 也当没有。"""
    for name in PROXY_VARS:
        if name in os.environ:
            case.addCleanup(os.environ.__setitem__, name, os.environ[name])
            del os.environ[name]
    patch(case, winhttp.urllib.request, "getproxies", lambda: {})


def patch(case: unittest.TestCase, target, name: str, value) -> None:
    """在这个用例里把 target.name 换成 value。"""
    patcher = mock.patch.object(target, name, value)
    patcher.start()
    case.addCleanup(patcher.stop)


class Handler(http.server.BaseHTTPRequestHandler):
    """假服务器（Site）的回法：redirects 里的路径回 302；files 里的回内容，带「Range: bytes=起-止」、服务器又认 Range 时回 206
    和 Content-Range，否则回 200 和整个文件；别的回 404。hold 认出的那一个请求先等着，等测试放行（go）再回。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端认出回话不对、停下了，不往下读就断开
            pass

    def do_GET(self):
        site = self.server.site
        asked = self.headers.get("Range")
        site.seen.append((self.path, asked))
        if site.hold is not None and not site.held.is_set() and site.hold(self.path, asked):
            site.held.set()
            site.go.wait(WAIT_MS / 1000)
        if self.path in site.redirects:
            self._reply(302, b"", {"Location": site.redirects[self.path]})
            return
        body = site.files.get(self.path)
        if body is None:
            self._reply(404, b"")
            return
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", asked or "")
        if match is None or not site.ranges:
            self._reply(200, body)
            return
        start, end = int(match[1]), int(match[2])
        if start > end or end >= len(body):
            self._reply(416, b"", {"Content-Range": f"bytes */{len(body)}"})
            return
        self._reply(206, body[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(body)}"})

    def _reply(self, code: int, body: bytes, headers: dict | None = None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # 一回一个请求，回完就关
        self.end_headers()
        self.wfile.write(body)


class Site:
    """127.0.0.1 上的一台假服务器。files 按路径给内容，redirects 按路径给跳转到的完整地址；ranges 为假时不认 Range（一律回
    200 和整个文件）；hold(路径, Range 头) 认出的第一个请求先等着（held 置位），go 置位再回。seen 记下每个请求的（路径,
    Range 头）。"""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.ranges = True
        self.hold = None
        self.held, self.go = threading.Event(), threading.Event()
        self.seen: list[tuple[str, str | None]] = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.site = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.go.set()
        self.httpd.shutdown()
        self.httpd.server_close()


class Ui(QObject):
    """一个控制模块，接法同悬浮窗（后台跑的放进新的守护线程，程序文件夹是本机的旧版本）。记下每次刷新时界面上的样子（状态、
    悬浮窗上更新那一条的文字、设置窗口那一行），以及界面线程收到的 phase 和进度（接在控制模块的两个信号上：信号从下载线程发出，
    排队到界面线程才到这里）和这些发生在哪个线程。"""

    def __init__(self, test: unittest.TestCase, root: Path):
        super().__init__()
        self.settings = UiSettings()
        self.lock = threading.Lock()
        self.shown: list[tuple[str, str | None, str]] = []
        self.phases: list[tuple[str, object]] = []
        self.progress: list[tuple[int, int]] = []
        self.threads: set[int] = set()
        self.quits: list[bool] = []
        session = SimpleNamespace(settings=self.settings, save=lambda: None)
        self.ctl = UpdateControl(
            session=session, lock=self.lock, run_background=lambda fn: threading.Thread(target=fn, daemon=True).start(),
            later=lambda ms, fn: None, refresh=self._refresh, update_settings=lambda: None,
            trace_user=lambda action, **data: None, quit_app=lambda: self.quits.append(True),
            open_url=lambda url: False, frozen=lambda: True, root=lambda: root)
        self.ctl.phased.connect(self.on_phase)
        self.ctl.progressed.connect(self.on_progress)
        test.addCleanup(self.ctl.shutdown, 10)

    def _refresh(self) -> None:
        self.threads.add(threading.get_ident())
        rows = [row for row in update_rows(self.ctl.notices()) if row[0] == "update"]
        self.shown.append((self.ctl.state, rows[0][1] if rows else None, self.ctl.settings_note()))

    @Slot(str, object)
    def on_phase(self, kind, detail) -> None:
        self.threads.add(threading.get_ident())
        self.phases.append((kind, detail))

    @Slot(object, object)
    def on_progress(self, done, total) -> None:
        self.threads.add(threading.get_ident())
        self.progress.append((done, total))

    def check(self) -> str:
        """设置窗口的「检查更新」，等查完；返回查完的状态。"""
        self.ctl.check_now()
        pump_until(lambda: self.ctl.state != "checking")
        return self.ctl.state

    def finished(self) -> bool:
        """下载线程交回了最后一步（下好了、停下了、出错了），界面线程都收到了。"""
        return bool(self.phases) and self.phases[-1][0] in ("ready", "stopped", "failed")

    def download(self) -> str:
        """提示行的「更新」，等下载线程做完、界面线程收齐；返回做完的状态。"""
        self.ctl.start_download()
        pump_until(self.finished)
        if self.ctl.dl_done is not None:
            self.ctl.dl_done.wait(10)
        return self.ctl.state


class FlowCase(unittest.TestCase):
    """发版生成的几样放在假服务器上（有文件清单），本机按旧版本放好。"""

    def setUp(self):
        no_proxy(self)
        self.errors = capture_errors(self)
        self.addCleanup(lambda: self.assertEqual(self.errors.entries(), [], "错误记录"))
        self.work = sandbox.temp_dir(self, "update-flow-")
        self.root = self.work / app_paths.FOLDER_NAME  # 本机的程序文件夹：旧版本
        make_files(self.root, OLD)
        self.folder = self.root / update.UPDATE_DIR
        self.zip_path, self.files_path = built()
        self.blob = self.zip_path.read_bytes()
        self.manifest = self.files_path.read_bytes()
        self.mirror, self.github, self.objects = Site(), Site(), Site()
        for site in (self.mirror, self.github, self.objects):
            self.addCleanup(site.close)
        base = self.mirror.url("/")
        for name, value in (("MIRROR_BASE", base), ("LATEST_JSON", base + upload_r2.LATEST_JSON_KEY),
                            ("LATEST_ZIP", base + upload_r2.LATEST_ZIP_KEY), ("NOTES_PREFIX", base),
                            ("GITHUB_API", self.github.url(API_PATH)),
                            ("DOWNLOAD_PREFIX", self.github.url(DOWNLOAD_PATH))):
            patch(self, update, name, value)
        self.asked: list[Path] = []
        self.version = update._four(NEW_VERSION)  # 假的 exe_version 交回的
        patch(self, update, "exe_version", self.fake_version)
        self.job = self.prepare_upload()
        self.publish()
        self.outside = self.snapshot()

    def fake_version(self, path) -> tuple[int, int, int, int]:
        """exe_version 的替身：造的 ACC.exe 不是真的程序，交回 self.version（默认是新版本的号）。"""
        self.asked.append(Path(path))
        return self.version

    def prepare_upload(self) -> upload_r2.Upload:
        """上传脚本准备上传（Upload.prepare）：核对本地的发行包和清单，算好要传的几样。令牌文件是这里造的假值，不连 R2。"""
        token = self.work / "token.txt"
        token.write_text("access_key_id=FAKE-KEY-ID-update-flow\nsecret_access_key=FAKE-SECRET-update-flow\n"
                         "endpoint=http://127.0.0.1:9\n", encoding="utf-8")
        notes = self.work / "notes.txt"
        notes.write_text(NOTES_TEXT, encoding="utf-8")
        args = upload_r2.parse_args(["--zip", str(self.zip_path), "--notes", str(notes), "--token-file", str(token),
                                     "--public-base", self.mirror.url("/"), "--github-api", self.github.url(API_PATH)])
        job = upload_r2.Upload(args)
        with mock.patch.object(upload_r2, "VERSION", NEW_VERSION), mock.patch.dict(os.environ):
            os.environ.pop(upload_r2.TOKEN_ENV, None)
            job.prepare()
        return job

    def publish(self, *, manifest: bool = True) -> None:
        """照上传脚本用的名字把要传的几样放到下载镜像上，GitHub 上发布这一版（附件：发行包、清单，地址跳到文件服务器）。
        manifest 为假时是加清单以前的样子：latest.json 没有清单的三项（照上传脚本的写法另写一份），镜像上、GitHub 上都没有清单。"""
        job = self.job
        latest = job.json_item.data
        if not manifest:
            older = {key: value for key, value in job.manifest.items() if key not in ("files", "files_size", "files_sha256")}
            latest = (json.dumps(older, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        mirror = {upload_r2.LATEST_JSON_KEY: latest, job.zip_item.key: self.blob, job.notes_item.key: job.notes_item.data}
        if manifest:
            mirror[job.files_item.key] = job.files_item.data
        self.mirror.files = {"/" + key: data for key, data in mirror.items()}
        assets = [(ZIP, self.blob)] + ([(FILES, self.manifest)] if manifest else [])
        release = {"tag_name": f"v{NEW_VERSION}", "name": NEW_VERSION, "html_url": PAGE, "draft": False,
                   "prerelease": False, "assets": [
                       {"name": name, "size": len(data), "state": "uploaded", "content_type": "application/octet-stream",
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                        "browser_download_url": self.github.url(f"{DOWNLOAD_PATH}v{NEW_VERSION}/{name}")}
                       for name, data in assets]}
        self.github.files = {API_PATH: json.dumps(release, ensure_ascii=False).encode("utf-8")}
        self.github.redirects, self.objects.files = {}, {}
        for name, data in assets:
            self.github.redirects[f"{DOWNLOAD_PATH}v{NEW_VERSION}/{name}"] = self.objects.url(f"/release-assets/{name}")
            self.objects.files[f"/release-assets/{name}"] = data

    def ui(self) -> Ui:
        return Ui(self, self.root)

    def snapshot(self) -> dict:
        """update 以外的东西。"""
        return {k: v for k, v in tree(self.root).items() if not k.startswith(update.UPDATE_DIR)}

    def left(self) -> list[str]:
        return sorted(p.name for p in self.folder.iterdir()) if self.folder.exists() else []

    def expect_errors(self, *titles: str) -> list[str]:
        """核对记下的错误记录（标题），清掉；返回各条的详细信息。"""
        entries = self.errors.entries()
        self.assertEqual([title for title, _ in entries], list(titles))
        self.errors.clear()
        return [detail for _, detail in entries]

    # ---------------------------------------------- 期望的样子

    def release(self, *, manifest: bool = True) -> update.Release:
        """check 应当认下的这一版：下载地址、清单地址都是先镜像后 GitHub，改动说明、资料同步日期取镜像上 latest.json 的。"""
        job = self.job
        files = {}
        if manifest:
            files = {"files_sha256": hashlib.sha256(self.manifest).hexdigest(), "files_size": len(self.manifest),
                     "files_urls": (self.mirror.url("/" + job.files_item.key),
                                    self.github.url(f"{DOWNLOAD_PATH}v{NEW_VERSION}/{FILES}"))}
        return update.Release(
            NEW_VERSION, hashlib.sha256(self.blob).hexdigest(), len(self.blob),
            (self.mirror.url("/" + job.zip_item.key), self.github.url(f"{DOWNLOAD_PATH}v{NEW_VERSION}/{ZIP}")),
            PAGE, self.mirror.url("/" + job.notes_item.key), job.manifest.get("data_date"), True, **files)

    def available(self, delta: bool) -> tuple:
        text = W.UPDATE_AVAILABLE.format(version=NEW_VERSION, date=W.update_date_text(self.job.manifest.get("data_date")),
                                         size=W.update_size_text(len(self.blob), delta))
        return "available", text, W.UPDATE_FOUND.format(version=NEW_VERSION)

    @staticmethod
    def downloading(step: str, total: int | None = None) -> list[tuple]:
        """下载中那一句（提示行和设置窗口那一行一样）：比对本机的文件那一句；或者按整 MB 一格一格往上走（0 到总数的整 MB）。"""
        if step == "compare":
            texts = [W.UPDATE_COMPARING.format(version=NEW_VERSION)]
        else:
            texts = [W.update_download_text(NEW_VERSION, mb * MB, total, step) for mb in range(total // MB + 1)]
        return [("downloading", text, text) for text in texts]

    CHECKING = ("checking", None, W.UPDATE_CHECKING)
    PREPARING = ("preparing", W.UPDATE_PREPARING.format(version=NEW_VERSION), W.UPDATE_PREPARING.format(version=NEW_VERSION))
    # 点了「更新」下好的：在对局外，提示马上重启（这次运行不再等玩家点）
    READY = ("ready", W.UPDATE_RESTARTING.format(version=NEW_VERSION), W.UPDATE_RESTARTING.format(version=NEW_VERSION))
    # 上次已下好、这次检查时直接是下好了的：要点「重启更新」才换
    READY_PREPARED = ("ready", W.UPDATE_READY.format(version=NEW_VERSION), W.UPDATE_READY_NOTE.format(version=NEW_VERSION))

    def assert_counted(self, calls: list[tuple[int, int]], total: int) -> None:
        """界面收到的一段进度：从 (0, 总数) 开始，只增不减，总数不变、没超过，最后等于总数。"""
        self.assertEqual(calls[0], (0, total))
        self.assertEqual(calls[-1], (total, total))
        done = [d for d, _ in calls]
        self.assertEqual(done, sorted(done))
        self.assertEqual({t for _, t in calls}, {total})

    def assert_ready(self, ui: Ui) -> None:
        """下好了：update\\new 和整包解开的一字不差，ready.json 写对，update 里只有这两样，update 以外的没被碰；换版本的模块认这份
        ready.json（发行版里启动时、点「重启更新」时照它换）。界面上的事都在界面线程里做。"""
        self.assertEqual(tree(self.folder / update.NEW_DIR), unpacked(self.blob))
        ready = json.loads((self.folder / update.READY_FILE).read_text(encoding="utf-8"))
        self.assertEqual((ready["format"], ready["version"], ready["sha256"]),
                         (1, NEW_VERSION, hashlib.sha256(self.blob).hexdigest()))
        self.assertEqual(self.left(), sorted([update.NEW_DIR, update.READY_FILE]))
        self.assertEqual(self.snapshot(), self.outside)
        self.assertEqual(self.asked, [self.folder / update.NEW_PART / "app" / "ACC.exe"])
        with mock.patch.object(app_paths, "is_frozen", return_value=True):
            self.assertEqual(update_apply.pending(self.root, app_version.VERSION), ready)
        self.assertEqual(ui.threads, {threading.get_ident()})
        self.assertEqual(ui.quits, [])


class DeltaFlowTest(FlowCase):
    def test_only_changed_files(self):
        # 上传脚本写的 latest.json 带着清单的三项：check 照样认出来（GitHub 上的附件 digest 一样，地址先镜像后 GitHub）
        latest = json.loads(self.job.json_item.data)
        self.assertEqual((latest["files"], latest["files_size"], latest["files_sha256"]),
                         (f"releases/{FILES}", len(self.manifest), hashlib.sha256(self.manifest).hexdigest()))
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.ctl.release, self.release())
        self.assertIn("最多约", ui.shown[-1][1])
        # 点「更新」：比对本机的文件 → 只按段取变了的那几个 → 最后核对 → 下好
        self.assertEqual(ui.download(), "ready")
        need = compressed(self.blob, CHANGED)
        self.assertEqual(ui.phases, [("compare", None), ("delta", need), ("preparing", None), ("ready", None)])
        self.assert_counted(ui.progress, need)
        self.assertEqual(changes(ui.shown), [self.CHECKING, self.available(True), *self.downloading("compare"),
                                             *self.downloading("delta", need), self.PREPARING, self.READY])
        self.assert_ready(ui)
        # 请求：镜像上的 latest.json、清单各一次，发行包只按段取（取到的是变了的那几个的压缩数据，加上中央目录和本地文件头，
        # 多不了几 KB），没有整个下载；GitHub 只问了接口；文件服务器没用上
        self.assertEqual([path for path, _ in self.mirror.seen if not path.endswith(".zip")],
                         ["/" + upload_r2.LATEST_JSON_KEY, "/" + self.job.files_item.key])
        asked = [r for path, r in self.mirror.seen if path.endswith(".zip")]
        self.assertTrue(asked)
        self.assertTrue(all(r is not None for r in asked), asked)
        fetched = sum(int(end) - int(start) + 1 for start, end in (r.removeprefix("bytes=").split("-") for r in asked))
        self.assertGreaterEqual(fetched, need)
        self.assertLess(fetched, need + 32 * 1024)
        self.assertEqual(self.github.seen, [(API_PATH, None)])
        self.assertEqual(self.objects.seen, [])
        # 这次运行没换上（没等到自动重启就退出了），下次启动再查到这一版：已经下好、核对过（换版本的模块认），直接是
        # 「重启更新」，不再下载
        self.mirror.seen.clear()
        later = self.ui()
        with mock.patch.object(app_paths, "is_frozen", return_value=True):
            self.assertEqual(later.check(), "ready")
        self.assertEqual(changes(later.shown), [self.CHECKING, self.READY_PREPARED])
        self.assertEqual([path for path, _ in self.mirror.seen], ["/" + upload_r2.LATEST_JSON_KEY])
        self.assertEqual(self.left(), sorted([update.NEW_DIR, update.READY_FILE]))

    def test_switches_address_for_ranges(self):
        # 镜像不认 Range（回 200）、GitHub 的地址认：读中央目录那一次在镜像上走不通，换 GitHub 的地址以后一直用它（每次都跳到
        # 文件服务器，Range 头原样带过去），照样只取变了的那几个；不改下整包，不记错误记录
        self.mirror.ranges = False
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.download(), "ready")
        need = compressed(self.blob, CHANGED)
        self.assertEqual(ui.phases, [("compare", None), ("delta", need), ("preparing", None), ("ready", None)])
        self.assert_counted(ui.progress, need)
        self.assertEqual(changes(ui.shown), [self.CHECKING, self.available(True), *self.downloading("compare"),
                                             *self.downloading("delta", need), self.PREPARING, self.READY])
        self.assert_ready(ui)
        zips = [r for path, r in self.mirror.seen if path.endswith(".zip")]
        self.assertEqual(len(zips), 1)
        self.assertIsNotNone(zips[0])
        github = [r for path, r in self.github.seen if path != API_PATH]
        self.assertTrue(github)
        self.assertEqual({path for path, _ in self.github.seen if path != API_PATH}, {f"{DOWNLOAD_PATH}v{NEW_VERSION}/{ZIP}"})
        objects = [r for _, r in self.objects.seen]
        self.assertEqual(objects, github)
        self.assertTrue(all(r is not None for r in objects), objects)
        fetched = sum(int(end) - int(start) + 1 for start, end in (r.removeprefix("bytes=").split("-") for r in objects))
        self.assertGreaterEqual(fetched, need)
        self.assertLess(fetched, need + 32 * 1024)

    def test_fallback_when_ranges_unsupported(self):
        # 两边都不认 Range（回 200）：读中央目录那一次就走不通（还没到「只下变了的部分」），改下整包，界面上从比对本机的文件
        # 直接换成下整包的进度；记一条错误记录，不提示
        self.mirror.ranges = self.objects.ranges = False
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.download(), "ready")
        size = len(self.blob)
        self.assertEqual(ui.phases, [("compare", None), ("full", size), ("preparing", None), ("ready", None)])
        self.assert_counted(ui.progress, size)
        self.assertEqual(changes(ui.shown), [self.CHECKING, self.available(True), *self.downloading("compare"),
                                             *self.downloading("full", size), self.PREPARING, self.READY])
        detail, = self.expect_errors(update.DELTA_FAILED)
        self.assertIn("不是 206", detail)
        self.assert_ready(ui)
        # 镜像：按段取一次（回 200，不往下读）以后换 GitHub 的地址（跳到文件服务器，同样回 200），最后从镜像下整包
        zips = [r for path, r in self.mirror.seen if path.endswith(".zip")]
        self.assertEqual(len(zips), 2)
        self.assertIsNotNone(zips[0])
        self.assertIsNone(zips[1])
        self.assertEqual([path for path, _ in self.github.seen],
                         [API_PATH, f"{DOWNLOAD_PATH}v{NEW_VERSION}/{ZIP}"])
        self.assertEqual([path for path, _ in self.objects.seen], [f"/release-assets/{ZIP}"])

    def test_stop_while_fetching(self):
        # 按段取的时候点「停止」：下载线程停下，删掉没拼完的，回到有新版本那一条；不改下整包，不记错误记录
        self.mirror.hold = lambda path, asked: path.endswith(".zip") and asked is not None
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        ui.ctl.start_download()
        self.assertTrue(pump_until(self.mirror.held.is_set))
        ui.ctl.stop_download()  # 界面线程里点「停止」
        self.mirror.go.set()
        self.assertTrue(pump_until(ui.finished))
        self.assertTrue(ui.ctl.dl_done.wait(10))
        self.assertEqual(ui.ctl.state, "available")
        self.assertEqual(ui.phases, [("compare", None), ("stopped", None)])
        self.assertEqual(ui.progress, [])
        self.assertEqual(changes(ui.shown), [self.CHECKING, self.available(True), *self.downloading("compare"),
                                             self.available(True)])
        self.assertEqual(self.left(), [])
        self.assertEqual(self.snapshot(), self.outside)
        self.assertEqual(len([path for path, _ in self.mirror.seen if path.endswith(".zip")]), 1)
        self.assertEqual(self.github.seen, [(API_PATH, None)])
        self.assertEqual(ui.threads, {threading.get_ident()})


class FullFlowTest(FlowCase):
    def test_without_manifest(self):
        # 加清单以前的样子：latest.json 没有清单的三项，GitHub 上也没有清单附件。「更新」写成约多少，点了直接下整包
        self.publish(manifest=False)
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.ctl.release, self.release(manifest=False))
        self.assertNotIn("最多", ui.shown[-1][1])
        self.assertEqual(ui.download(), "ready")
        size = len(self.blob)
        self.assertEqual(ui.phases, [("full", size), ("preparing", None), ("ready", None)])
        self.assert_counted(ui.progress, size)
        self.assertEqual(changes(ui.shown), [self.CHECKING, self.available(False), *self.downloading("full", size),
                                             self.PREPARING, self.READY])
        self.assert_ready(ui)
        # 请求：镜像上的 latest.json 和整包（不按段取），GitHub 只问了接口
        self.assertEqual(self.mirror.seen, [("/" + upload_r2.LATEST_JSON_KEY, None), ("/" + self.job.zip_item.key, None)])
        self.assertEqual(self.github.seen, [(API_PATH, None)])
        self.assertEqual(self.objects.seen, [])


class ForkFlowTest(FlowCase):
    def test_github_only(self):
        # 复刻的版本（源代码地址不是下载镜像上那个仓库，update.USE_MIRROR 为假）：认下的是 GitHub 上的那一版，下载地址、清单
        # 地址都只有 GitHub 的（没有改动说明和资料同步日期），「手动下载」开发布页面；点「更新」以后下清单、按段取都走 GitHub
        # （跳到文件服务器），照样只下变了的那几个、下好。镜像上照样放着这一版，却一次都没被请求
        patch(self, update, "USE_MIRROR", False)
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        zip_url = self.github.url(f"{DOWNLOAD_PATH}v{NEW_VERSION}/{ZIP}")
        files_url = self.github.url(f"{DOWNLOAD_PATH}v{NEW_VERSION}/{FILES}")
        self.assertEqual(ui.ctl.release, update.Release(
            NEW_VERSION, hashlib.sha256(self.blob).hexdigest(), len(self.blob), (zip_url,), PAGE, None, None, True,
            hashlib.sha256(self.manifest).hexdigest(), len(self.manifest), (files_url,)))
        self.assertTrue(ui.ctl.notices()[0].page)
        available = W.UPDATE_AVAILABLE.format(version=NEW_VERSION, date="", size=W.update_size_text(len(self.blob), True))
        self.assertEqual(ui.shown[-1], ("available", available, W.UPDATE_FOUND.format(version=NEW_VERSION)))
        self.assertEqual(ui.download(), "ready")
        need = compressed(self.blob, CHANGED)
        self.assertEqual(ui.phases, [("compare", None), ("delta", need), ("preparing", None), ("ready", None)])
        self.assert_counted(ui.progress, need)
        self.assert_ready(ui)
        self.assertEqual(self.mirror.seen, [])
        asked = [path for path, _ in self.github.seen]
        self.assertEqual(asked[:2], [API_PATH, f"{DOWNLOAD_PATH}v{NEW_VERSION}/{FILES}"])
        self.assertEqual(set(asked[2:]), {f"{DOWNLOAD_PATH}v{NEW_VERSION}/{ZIP}"})
        ranges = [r for path, r in self.objects.seen if path.endswith(".zip")]
        self.assertTrue(ranges)
        self.assertTrue(all(r is not None for r in ranges), ranges)


class FailureFlowTest(FlowCase):
    """出错的分法：advisor/update.py 抛的 UpdateError 按出错时做到哪一步算下载失败、解压核对失败，原因照它给的写；只下变了的
    文件那条路走不通只记错误记录，不交给界面。"""

    def failed(self, ui: Ui, failure: str) -> str:
        """界面最后收到的是 failure 这一种出错（提示行带 ⚠，设置窗口那一行同一句），返回原因。"""
        kind, detail = ui.phases[-1]
        self.assertEqual((kind, detail[0], ui.ctl.state, ui.ctl.failure), ("failed", failure, "failed", failure))
        reason = detail[1]
        self.assertEqual(ui.ctl.reason, reason)
        text = W.update_failed_text(failure, reason, NEW_VERSION)
        self.assertEqual(ui.shown[-1], ("failed", "⚠ " + text, W.update_failed_text(failure, reason, NEW_VERSION, short=True)))
        return reason

    def test_download_fails(self):
        # 没有清单、两边的整包都下不到：下载失败，原因是 download 给的；不留下没下完的，不另记错误记录
        self.publish(manifest=False)
        del self.mirror.files["/" + self.job.zip_item.key]
        self.objects.files.clear()
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.download(), "failed")
        size = len(self.blob)
        self.assertEqual(ui.phases[:-1], [("full", size)])
        reason = self.failed(ui, "download")
        self.assertIn("HTTP 404", reason)
        self.assertEqual(ui.shown[-1][1].count("下载失败"), 1)  # 提示行前面的「新版本下载失败：」只出现一次
        self.assertEqual(changes(ui.shown)[:-1], [self.CHECKING, self.available(False), *self.downloading("full", size)[:1]])
        self.assertEqual(self.left(), [])
        self.assertEqual(self.snapshot(), self.outside)

    def test_final_check_fails(self):
        # 拼好、解好以后核对主程序的版本号不对（发错了包）：只下变了的文件那一次改下整包（记一条错误记录），整包同样不对：
        # 解压核对失败，原因是 prepare 给的。界面上先后是比对 → 只下变了的部分 → 核对 → 下整包 → 核对 → 出错。没有 new、
        # new.part、ready.json，下好的整包留着（再点「更新」时整包不用重下）
        self.version = (98, 0, 0, 0)
        ui = self.ui()
        self.assertEqual(ui.check(), "available")
        self.assertEqual(ui.download(), "failed")
        need, size = compressed(self.blob, CHANGED), len(self.blob)
        self.assertEqual(ui.phases[:-1], [("compare", None), ("delta", need), ("preparing", None), ("full", size),
                                          ("preparing", None)])
        reason = self.failed(ui, "prepare")
        self.assertIn("应为 99.0.0", reason)
        self.assertEqual(changes(ui.shown)[:-1], [self.CHECKING, self.available(True), *self.downloading("compare"),
                                                  *self.downloading("delta", need), self.PREPARING,
                                                  *self.downloading("full", size), self.PREPARING])
        detail, = self.expect_errors(update.DELTA_FAILED)
        self.assertIn("应为 99.0.0", detail)
        self.assertEqual(self.asked, [self.folder / update.NEW_PART / "app" / "ACC.exe"] * 2)
        self.assertEqual(self.left(), [ZIP])
        self.assertEqual(self.snapshot(), self.outside)
        with mock.patch.object(app_paths, "is_frozen", return_value=True):
            self.assertIsNone(update_apply.pending(self.root, app_version.VERSION))


if __name__ == "__main__":
    unittest.main()
