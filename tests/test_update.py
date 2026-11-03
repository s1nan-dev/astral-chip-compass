"""软件内更新的前一半（advisor/update.py）：版本号比较、版本文件和 GitHub 回话的核对、两边怎么认（压缩包和文件清单）、问哪几边
（由源代码地址推出：官方仓库、复刻的仓库、不在 GitHub 上的、写法不对的；复刻的版本只问 GitHub，镜像一次都不请求）、下载
（先镜像后 GitHub、停止、大小和 SHA-256 不对、重定向、收到的比应有的多、写不进）、解压备好（名字不对、缺文件、超上限、版本号
不对、ready.json 最后写、出错收拾干净、update 以外的不碰）、开始下载之前的检查（写不进、空间不够）、读 exe 的文件版本号。
下载并备好（obtain：只下变了的文件、走不通改下整包）另见 tests/test_update_obtain.py。

不联网：查和认用假的会话（FakeSession：按地址给回话或抛错）；下载、端到端的检查用 127.0.0.1 上自己起的 http.server（同
tests/test_winhttp.py，不经代理），换掉 LATEST_JSON、GITHUB_API 指过去。读版本号拿本机 Python 的 python.exe 试真的；备好时
换掉 exe_version（这里造的压缩包里的 ACC.exe 不是真的程序）。GitHub 的回话照它文档里 releases/latest 的格式造一份 1.0.0 的
样子（GITHUB_SAMPLE），日期、编号、校验值都是编的。
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from advisor import update, winhttp
from advisor import version as app_version
from tests.support import sandbox
from tests.support.errors import capture_errors

SHA = "ab" * 32
SIZE = 89001969
DROP = object()  # latest()、github() 里表示去掉这一项
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")

# GitHub releases/latest 的回话（照它文档里的格式；编号、日期、校验值都是编的）：要用的只有 tag_name、html_url 和附件
# ACC-1.0.0.zip 的 size、browser_download_url、digest，其余的照样带着，核对认不认识的字段都不碍事
GITHUB_SAMPLE = {
    "url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/100001",
    "assets_url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/100001/assets",
    "upload_url": "https://uploads.github.com/repos/s1nan-dev/astral-chip-compass/releases/100001/assets{?name,label}",
    "html_url": "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.0",
    "id": 100001,
    "author": {"login": "s1nan-dev", "id": 200001, "node_id": "U_kgDOAAAAAQ", "type": "User", "site_admin": False},
    "node_id": "RE_kwDOAAAAAc4AAAAB",
    "tag_name": "v1.0.0",
    "target_commitish": "main",
    "name": "1.0.0",
    "draft": False,
    "immutable": False,
    "prerelease": False,
    "created_at": "2000-01-01T00:00:00Z",
    "updated_at": "2000-01-01T00:00:00Z",
    "published_at": "2000-01-01T00:00:00Z",
    "assets": [
        {
            "url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/assets/300001",
            "id": 300001,
            "node_id": "RA_kwDOAAAAAc4AAAAB",
            "name": "ACC-1.0.0.zip",
            "label": "",
            "uploader": {"login": "s1nan-dev", "id": 200001, "type": "User", "site_admin": False},
            "content_type": "application/x-zip-compressed",
            "state": "uploaded",
            "size": 89001969,
            "digest": "sha256:" + "c0ffee" * 10 + "c0de",
            "download_count": 0,
            "created_at": "2000-01-01T00:00:00Z",
            "updated_at": "2000-01-01T00:00:00Z",
            "browser_download_url":
                "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.0/ACC-1.0.0.zip",
        },
        {
            "url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/assets/300002",
            "id": 300002,
            "name": "SHA256SUMS.txt",
            "content_type": "text/plain",
            "state": "uploaded",
            "size": 80,
            "digest": "sha256:" + "11" * 32,
            "browser_download_url":
                "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.0/SHA256SUMS.txt",
        },
    ],
    "tarball_url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/tarball/v1.0.0",
    "zipball_url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/zipball/v1.0.0",
    "body": "（发布说明）",
}


def latest(version="1.0.1", size=SIZE, sha=SHA, **changes) -> dict:
    """下载镜像的版本文件；changes 改某几项（值为 DROP 时去掉那一项）。"""
    data = {
        "format": 1,
        "version": version,
        "zip": f"releases/ACC-{version}.zip",
        "size": size,
        "sha256": sha,
        "page": f"https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v{version}",
        "notes": f"https://dl.astral-chip-compass.com/notes/{version}.txt",
        "data_date": "2000-01-02",
        "auto": True,
    }
    for key, value in changes.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def github(version="1.0.1", size=SIZE, sha=SHA, digest=None, **changes) -> dict:
    """GitHub 的回话（按 GITHUB_SAMPLE 改版本号、大小、SHA-256）。digest 给了就用它（DROP 时去掉），不给时是 sha256:<sha>。"""
    data = copy.deepcopy(GITHUB_SAMPLE)
    data.update(tag_name=f"v{version}", name=version,
                html_url=f"https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v{version}")
    asset = data["assets"][0]
    asset.update(name=f"ACC-{version}.zip", size=size,
                 browser_download_url=f"https://github.com/s1nan-dev/astral-chip-compass/releases/download/"
                                      f"v{version}/ACC-{version}.zip",
                 digest=f"sha256:{sha}" if digest is None else digest)
    if digest is DROP:
        del asset["digest"]
    for key, value in changes.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


FILES_SHA = "ef" * 32
FILES_SIZE = 12345
# 版本文件里清单的三项（1.0.1 的）
FILES = {"files": "releases/ACC-1.0.1.files.json", "files_size": FILES_SIZE, "files_sha256": FILES_SHA}
MIRROR_FILES = "https://dl.astral-chip-compass.com/releases/ACC-1.0.1.files.json"
GITHUB_FILES = "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.1/ACC-1.0.1.files.json"


def with_files(data: dict, size=FILES_SIZE, sha=FILES_SHA, digest=None, **changes) -> dict:
    """GitHub 的回话加上这一版的清单附件 ACC-<版本号>.files.json（写法照 GITHUB_SAMPLE 里的附件）。digest 给了就用它（DROP 时
    去掉），不给时是 sha256:<sha>；changes 改附件的某几项。"""
    version = data["tag_name"][1:]
    asset = {
        "url": "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/assets/300003",
        "id": 300003,
        "name": f"ACC-{version}.files.json",
        "content_type": "application/json",
        "state": "uploaded",
        "size": size,
        "digest": f"sha256:{sha}" if digest is None else digest,
        "browser_download_url":
            f"https://github.com/s1nan-dev/astral-chip-compass/releases/download/v{version}/ACC-{version}.files.json",
    }
    if digest is DROP:
        del asset["digest"]
    asset.update(changes)
    data["assets"].append(asset)
    return data


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/", code, "error", {}, None)


def timeout() -> urllib.error.URLError:
    """WinHTTP 超时时 winhttp.Session 抛的（原因是中文加错误码）。"""
    return urllib.error.URLError("操作超时（WinHTTP 12002）")


class FakeSession:
    """假的联网会话（只有 check、download 用到的 stream 和 close）：按地址给回话（字节，或交给 json.dumps 的对象），值是异常时
    抛它；没登记的地址回 404。回话按 1000 字节一块交给 sink。记下每个请求。"""

    def __init__(self, replies: dict):
        self.replies = dict(replies)
        self.requests: list[urllib.request.Request] = []
        self.closed = False

    def stream(self, req, sink, timeout=None, stop=None):
        self.requests.append(req)
        reply = self.replies.get(req.full_url, http_error(404))
        if isinstance(reply, BaseException):
            raise reply
        data = reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8")
        for start in range(0, len(data), 1000):
            sink(data[start:start + 1000])
        return len(data)

    def close(self):
        self.closed = True


def no_proxy(case: unittest.TestCase) -> None:
    """本机测试不经过代理（同 tests/test_winhttp.py 的 no_proxy_env）：清掉环境变量里的代理，getproxies 也当没有。"""
    for name in PROXY_VARS:
        if name in os.environ:
            case.addCleanup(os.environ.__setitem__, name, os.environ[name])
            del os.environ[name]
    patcher = mock.patch.object(winhttp.urllib.request, "getproxies", return_value={})
    patcher.start()
    case.addCleanup(patcher.stop)


class Handler(http.server.BaseHTTPRequestHandler):
    """本机的假服务器：routes 按路径给回话——字节（200）、("status", 码)、("redirect", 路径)、("trickle", 声明的长度：一直慢慢地
    发，直到 trickle 置位）；没登记的回 404。seen 记下每个请求的（路径, 请求头）。"""

    protocol_version = "HTTP/1.1"
    routes: dict = {}
    seen: list = []
    trickle = threading.Event()

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端提前断开（停止）：不往错误输出里打
            pass

    def end_headers(self):
        self.send_header("Connection", "close")  # 一回一个请求，回完就关（同 tests/test_winhttp.py）
        super().end_headers()

    def do_GET(self):
        Handler.seen.append((self.path, self.headers))
        route = Handler.routes.get(self.path)
        if isinstance(route, bytes):
            self._send(200, route)
        elif route is None:
            self._send(404, b"")
        elif route[0] == "status":
            self._send(route[1], b"")
        elif route[0] == "redirect":
            self.send_response(302)
            self.send_header("Location", route[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif route[0] == "trickle":
            self.send_response(200)
            self.send_header("Content-Length", str(route[1]))
            self.end_headers()
            while not Handler.trickle.is_set():
                try:
                    self.wfile.write(b"t" * 512)
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.02)

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server:
    def __init__(self):
        Handler.routes, Handler.seen = {}, []
        Handler.trickle = threading.Event()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        Handler.trickle.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def start_server(case: unittest.TestCase) -> Server:
    no_proxy(case)
    server = Server()
    case.addCleanup(server.close)
    return server


FORK = "https://github.com/someone/acc-fork"  # 复刻的仓库：改版的源代码地址换成了它
FORK_API = "https://api.github.com/repos/someone/acc-fork/releases/latest"


def forked(data: dict) -> dict:
    """GitHub 的回话（github()、with_files() 造的）换成复刻的仓库发的：地址里的仓库换成 FORK 的。"""
    return json.loads(json.dumps(data).replace("s1nan-dev/astral-chip-compass", "someone/acc-fork"))


def as_fork(case: unittest.TestCase, source_url: str = FORK) -> None:
    """在这个用例里把 advisor/update.py 当成源代码地址是 source_url 的版本：由源代码地址算出的几个常量（sources 给的
    USE_MIRROR、GITHUB_API，以及 PAGE_PREFIX、DOWNLOAD_PREFIX）照它换掉。"""
    use_mirror, api = update.sources(source_url)
    for name, value in (("USE_MIRROR", use_mirror), ("GITHUB_API", api), ("PAGE_PREFIX", source_url + "/"),
                        ("DOWNLOAD_PREFIX", source_url + "/releases/download/")):
        patcher = mock.patch.object(update, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)


# ====================================================================== 版本号


class VersionTest(unittest.TestCase):
    def test_is_newer(self):
        for a, b, newer in (
            ("1.0.1", "1.0.0", True),
            ("1.0.0", "1.0.0", False),
            ("1.0.0", "1.0.1", False),
            ("1.0.10", "1.0.9", True),  # 按数字比，不按文字比
            ("1.10.0", "1.9.9", True),
            ("2.0.0", "1.99.99", True),
            ("1.1", "1.1.0", False),  # 段数不同时短的补 0
            ("1.1.0.1", "1.1", True),
            ("1.0.01", "1.0.1", False),
        ):
            with self.subTest(a=a, b=b):
                self.assertIs(update.is_newer(a, b), newer)

    def test_unreadable_is_not_newer(self):
        # 认不出就不算新：前缀 v、后缀、空的、不是文字、全角数字
        for a, b in (("v1.0.1", "1.0.0"), ("1.0.1-beta", "1.0.0"), ("", "1.0.0"), ("1.0.1", ""), (None, "1.0.0"),
                     ("1.0.1", None), ("１.0.1", "1.0.0"), ("1..1", "1.0"), ("1.0.1 ", "1.0.0")):
            with self.subTest(a=a, b=b):
                self.assertFalse(update.is_newer(a, b))

    def test_same_version(self):
        self.assertTrue(update._same_version("1.0.1", "1.0.1"))
        self.assertTrue(update._same_version("1.0.1", "1.0.01"))
        self.assertFalse(update._same_version("1.0.1", "1.0.2"))
        self.assertFalse(update._same_version("1.0.1", None))
        self.assertFalse(update._same_version("1.0.1", ""))

    def test_four_parts(self):
        self.assertEqual(update._four("1.0.1"), (1, 0, 1, 0))
        self.assertEqual(update._four("2.10.3"), (2, 10, 3, 0))


# ====================================================================== 版本文件、GitHub 的回话


class MirrorFormatTest(unittest.TestCase):
    """下载镜像的版本文件（_mirror_release）：必填、类型、各字段的要求不对时整份不认；notes、data_date 不对时当没有。"""

    def test_good(self):
        release = update._mirror_release(latest())
        self.assertEqual(release, update.Release(
            "1.0.1", SHA, SIZE, ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",),
            "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1",
            "https://dl.astral-chip-compass.com/notes/1.0.1.txt", "2000-01-02", True))

    def test_optional_and_unknown_fields(self):
        release = update._mirror_release(latest(notes=DROP, data_date=DROP, channel="stable", extra={"a": 1}))
        self.assertEqual((release.notes, release.data_date), (None, None))
        self.assertIs(update._mirror_release(latest(auto=False)).auto, False)
        for size in (update.MIN_SIZE, update.MAX_SIZE):  # 上下限本身可以
            with self.subTest(size=size):
                self.assertEqual(update._mirror_release(latest(size=size)).size, size)
        # 和发版的上传脚本（tools/upload_r2.py）是同一对数：1 MiB～1 GiB
        self.assertEqual((update.MIN_SIZE, update.MAX_SIZE), (1 << 20, 1 << 30))

    def test_bad_notes_and_date_dropped(self):
        for notes in ("https://evil.example/notes/1.0.1.txt", "http://dl.astral-chip-compass.com/notes/1.0.1.txt",
                      "https://dl.astral-chip-compass.com.evil.example/x.txt", "https://dl.astral-chip-compass.com/a b",
                      "https://dl.astral-chip-compass.com/notes/../x.txt", 5, None):
            with self.subTest(notes=notes):
                release = update._mirror_release(latest(notes=notes))
                self.assertIsNone(release.notes)
                self.assertEqual(release.version, "1.0.1")
        for day in ("2000-13-01", "2000-02-30", "2000/01/02", "20000102", 20000102, ""):
            with self.subTest(day=day):
                self.assertIsNone(update._mirror_release(latest(data_date=day)).data_date)

    def test_rejected(self):
        cases = {
            "format 2": latest(format=2),
            "format 文字": latest(format="1"),
            "format true": latest(format=True),
            "format 1.0": latest(format=1.0),
            "缺 format": latest(format=DROP),
            "缺 version": latest(version=DROP),
            "缺 zip": latest(zip=DROP),
            "缺 size": latest(size=DROP),
            "缺 sha256": latest(sha256=DROP),
            "缺 page": latest(page=DROP),
            "缺 auto": latest(auto=DROP),
            "version 数字": latest(version=101),
            "size 文字": latest(size=str(SIZE)),
            "size true": latest(size=True),
            "size 小数": latest(size=float(SIZE)),
            "sha256 null": latest(sha256=None),
            "page null": latest(page=None),
            "auto 文字": latest(auto="true"),
            "auto 1": latest(auto=1),
            "version 两段": latest(version="1.0", zip="releases/ACC-1.0.zip"),
            "version 四段": latest(version="1.0.1.0", zip="releases/ACC-1.0.1.0.zip"),
            "version 带 v": latest(version="v1.0.1", zip="releases/ACC-v1.0.1.zip"),
            "version 全角": latest(version="１.0.1", zip="releases/ACC-１.0.1.zip"),
            "version 带空格": latest(version="1.0.1 ", zip="releases/ACC-1.0.1 .zip"),
            "version 带路径": latest(version="../1.0.1", zip="releases/ACC-../1.0.1.zip"),
            "zip 版本号不对": latest(zip="releases/ACC-1.0.2.zip"),
            "zip 少了 releases": latest(zip="ACC-1.0.1.zip"),
            "zip 开头带斜杠": latest(zip="/releases/ACC-1.0.1.zip"),
            "zip 带参数": latest(zip="releases/ACC-1.0.1.zip?x=1"),
            "size 太小": latest(size=update.MIN_SIZE - 1),
            "size 太大": latest(size=update.MAX_SIZE + 1),
            "size 0": latest(size=0),
            "sha256 大写": latest(sha256=SHA.upper()),
            "sha256 63 位": latest(sha256=SHA[:63]),
            "sha256 不是十六进制": latest(sha256="zz" * 32),
            "page 别的仓库": latest(page="https://github.com/someone/astral-chip-compass/releases/tag/v1.0.1"),
            "page 同名前缀": latest(page="https://github.com/s1nan-dev/astral-chip-compass-evil/releases/tag/v1.0.1"),
            "page http": latest(page="http://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1"),
            "page 别的网站": latest(page="https://dl.astral-chip-compass.com/releases/tag/v1.0.1"),
            "page 带空格": latest(page="https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1 x"),
            "page 带反斜杠": latest(page="https://github.com/s1nan-dev/astral-chip-compass/\\evil.example"),
            # 浏览器打开时会把「..」段折算掉（%2e 的写法也算），落到别的仓库
            "page 点段": latest(page="https://github.com/s1nan-dev/astral-chip-compass/../../evil/x/releases/tag/v1.0.1"),
            "page 点段在后面": latest(page="https://github.com/s1nan-dev/astral-chip-compass/releases/../../../evil/x"),
            "page 转义的点段": latest(page="https://github.com/s1nan-dev/astral-chip-compass/%2e%2E/.%2e/evil/x"),
            "page 转义的斜杠": latest(page="https://github.com/s1nan-dev/astral-chip-compass/..%2F..%2Fevil/x"),
            "page 单点段": latest(page="https://github.com/s1nan-dev/astral-chip-compass/./releases/tag/v1.0.1"),
            "不是对象": [latest()],
            "null": None,
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(update._Invalid):
                update._mirror_release(data)

    def test_manifest_fields(self):
        # 清单的三项都对：带上清单，地址只有镜像上的。三项都没有（旧的写法）或都是 null：没有清单，不算不对
        release = update._mirror_release(latest(**FILES))
        self.assertEqual((release.files_sha256, release.files_size, release.files_urls),
                         (FILES_SHA, FILES_SIZE, (MIRROR_FILES,)))
        for data in (latest(), latest(files=None, files_size=None, files_sha256=None)):
            problems = []
            release = update._mirror_release(data, problems)
            self.assertEqual((release.files_sha256, release.files_size, release.files_urls, problems),
                             (None, None, (), []))
        for size in (1, update.MAX_MANIFEST):  # 上下限本身可以：1 B～16 MiB
            with self.subTest(size=size):
                self.assertEqual(update._mirror_release(latest(**{**FILES, "files_size": size})).files_size, size)
        self.assertEqual(update.MAX_MANIFEST, 16 << 20)

    def test_manifest_fields_rejected(self):
        # 有一项不对（缺一两项也算）：这一版当没有清单，版本文件别的照常认，problems 里写明哪里不对
        cases = {
            "files 版本号不对": {"files": "releases/ACC-1.0.2.files.json"},
            "files 少了 releases": {"files": "ACC-1.0.1.files.json"},
            "files 是压缩包": {"files": "releases/ACC-1.0.1.zip"},
            "files 带参数": {"files": "releases/ACC-1.0.1.files.json?x=1"},
            "files 带点段": {"files": "releases/../ACC-1.0.1.files.json"},
            "files 是完整地址": {"files": MIRROR_FILES},
            "files 不是文字": {"files": 5},
            "缺 files": {"files": DROP},
            "files_size 0": {"files_size": 0},
            "files_size 太大": {"files_size": update.MAX_MANIFEST + 1},
            "files_size 文字": {"files_size": str(FILES_SIZE)},
            "files_size true": {"files_size": True},
            "files_size 小数": {"files_size": float(FILES_SIZE)},
            "缺 files_size": {"files_size": DROP},
            "files_sha256 大写": {"files_sha256": FILES_SHA.upper()},
            "files_sha256 63 位": {"files_sha256": FILES_SHA[:63]},
            "files_sha256 不是十六进制": {"files_sha256": "zz" * 32},
            "files_sha256 null": {"files_sha256": None},
            "缺 files_sha256": {"files_sha256": DROP},
        }
        for name, change in cases.items():
            with self.subTest(name):
                problems = []
                release = update._mirror_release(latest(**{**FILES, **change}), problems)
                self.assertEqual((release.files_sha256, release.files_size, release.files_urls), (None, None, ()))
                self.assertEqual(release, update._mirror_release(latest()))  # 别的照常认
                self.assertEqual(len(problems), 1)
                self.assertTrue(problems[0].startswith(("files ", "files_size ", "files_sha256 ")), problems[0])


class GitHubFormatTest(unittest.TestCase):
    """GitHub 的回话（_github_release）：照文档格式造的 1.0.0；版本号、发布页、附件、下载地址不对时整份不认；digest 认不出
    当没有。"""

    def test_sample(self):
        found = update._github_release(GITHUB_SAMPLE)
        self.assertEqual(found, update._Found(
            "1.0.0", 89001969, "c0ffee" * 10 + "c0de",
            "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.0/ACC-1.0.0.zip",
            "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.0"))

    def test_digest_forms(self):
        self.assertEqual(update._github_release(github(digest="SHA256:" + SHA.upper())).sha256, SHA)  # 转成小写
        for digest in (DROP, None, "", "sha512:" + "ab" * 64, "sha256:" + SHA[:63], "sha256:" + "zz" * 32, SHA, 5):
            with self.subTest(digest=digest):
                data = github()
                if digest is DROP:
                    del data["assets"][0]["digest"]
                else:
                    data["assets"][0]["digest"] = digest
                self.assertIsNone(update._github_release(data).sha256)

    def test_rejected(self):
        def asset(**changes):
            data = github()
            data["assets"][0].update(changes)
            return data

        cases = {
            "tag 不带 v": github(tag_name="1.0.1"),
            "tag 两段": github(tag_name="v1.0"),
            "tag 预发布": github(tag_name="v1.0.1-beta"),
            "缺 tag": github(tag_name=DROP),
            "html_url 别的仓库": github(html_url="https://github.com/someone/x/releases/tag/v1.0.1"),
            "html_url 点段": github(html_url="https://github.com/s1nan-dev/astral-chip-compass/../../someone/x"),
            "下载地址点段": asset(browser_download_url="https://github.com/s1nan-dev/astral-chip-compass/releases/download/"
                                                    "%2e%2e/%2e%2e/%2e%2e/someone/x/ACC-1.0.1.zip"),
            "缺 html_url": github(html_url=DROP),
            "缺 assets": github(assets=DROP),
            "assets 不是列表": github(assets={}),
            "附件名字对不上": asset(name="ACC-1.0.0.zip"),
            "附件 size 太小": asset(size=1000),
            "附件 size 文字": asset(size=str(SIZE)),
            "附件 size true": asset(size=True),
            "下载地址别的仓库": asset(browser_download_url="https://github.com/someone/x/releases/download/v1.0.1/"
                                                    "ACC-1.0.1.zip"),
            "下载地址文件服务器": asset(browser_download_url="https://objects.githubusercontent.com/ACC-1.0.1.zip"),
            "下载地址 http": asset(browser_download_url="http://github.com/s1nan-dev/astral-chip-compass/releases/"
                                                      "download/v1.0.1/ACC-1.0.1.zip"),
            "不是对象": [github()],
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(update._Invalid):
                update._github_release(data)

    def test_manifest_asset(self):
        # 清单附件 ACC-<版本号>.files.json：digest、size、browser_download_url 都对时带上（digest 转成小写）
        found = update._github_release(with_files(github()))
        self.assertEqual((found.files_sha256, found.files_size, found.files_url), (FILES_SHA, FILES_SIZE, GITHUB_FILES))
        self.assertEqual((found.version, found.sha256, found.size), ("1.0.1", SHA, SIZE))
        self.assertEqual(update._github_release(with_files(github(), digest="SHA256:" + FILES_SHA.upper())).files_sha256,
                         FILES_SHA)
        # 没有这个附件（别的版本的清单不算）：没有清单，不算不对
        other = with_files(github())
        other["assets"][-1]["name"] = "ACC-1.0.0.files.json"
        for data in (github(), other):
            problems = []
            found = update._github_release(data, problems)
            self.assertEqual((found.files_sha256, found.files_size, found.files_url, problems), (None, None, None, []))

    def test_manifest_asset_unusable(self):
        # 有清单附件、却用不上（没有 digest、size 不对、地址不在发布页的附件下面）：这一版没有清单，压缩包那一项照常认，
        # problems 里写明哪里不对
        no_digest = with_files(github())
        no_digest["assets"][-1]["digest"] = None
        cases = {
            "没有 digest": with_files(github(), digest=DROP),
            "digest null": no_digest,
            "digest 认不出": with_files(github(), digest="sha512:" + "ab" * 64),
            "size 0": with_files(github(), size=0),
            "size 太大": with_files(github(), size=update.MAX_MANIFEST + 1),
            "size 文字": with_files(github(), size=str(FILES_SIZE)),
            "size true": with_files(github(), size=True),
            "地址别的仓库": with_files(github(), browser_download_url="https://github.com/someone/x/releases/download/"
                                                                 "v1.0.1/ACC-1.0.1.files.json"),
            "地址点段": with_files(github(), browser_download_url="https://github.com/s1nan-dev/astral-chip-compass/"
                                                               "releases/download/%2e%2e/%2e%2e/%2e%2e/someone/x/y"),
            "地址文件服务器": with_files(github(), browser_download_url="https://objects.githubusercontent.com/x.json"),
            "没有地址": with_files(github(), browser_download_url=None),
        }
        for name, data in cases.items():
            with self.subTest(name):
                problems = []
                found = update._github_release(data, problems)
                self.assertEqual((found.files_sha256, found.files_size, found.files_url), (None, None, None))
                self.assertEqual(found, update._github_release(github()))
                self.assertEqual(len(problems), 1)
                self.assertIn("ACC-1.0.1.files.json", problems[0])


# ====================================================================== 两边怎么认


class CheckCase(unittest.TestCase):
    """用假的会话调 check（两边各给一个回话），错误记录指到自己的临时文件。"""

    def setUp(self):
        self.errors = capture_errors(self)

    def check(self, mirror, gh, current="1.0.0", skipped=None) -> update.CheckResult:
        self.session = FakeSession({update.LATEST_JSON: mirror, update.GITHUB_API: gh})
        result = update.check(current, skipped, session=self.session)
        self.assertFalse(self.session.closed)  # 调用方给的会话不替它关
        self.assertEqual([r.full_url for r in self.session.requests], [update.LATEST_JSON, update.GITHUB_API])
        return result

    def titles(self) -> list[str]:
        return [title for title, _ in self.errors.entries()]


class CheckTest(CheckCase):
    """check：两边都问，按模块说明「认哪一版」定下最新的那一版。用假的会话。"""

    def test_consistent(self):
        result = self.check(latest(auto=False), github())
        self.assertEqual(result, update.CheckResult(
            update.Release("1.0.1", SHA, SIZE,
                           ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",
                            "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.1/ACC-1.0.1.zip"),
                           "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1",
                           "https://dl.astral-chip-compass.com/notes/1.0.1.txt", "2000-01-02", False),
            "1.0.1", True, True, None))
        self.assertEqual(self.errors.entries(), [])

    def test_mismatch_trusts_github(self):
        # 版本号、大小、SHA-256 有一样对不上：只认 GitHub 那一版（下载地址只有 GitHub，auto 当真，没有改动说明和资料同步
        # 日期），写一条错误记录
        github_only = update.Release(
            "1.0.1", SHA, SIZE,
            ("https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.1/ACC-1.0.1.zip",),
            "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1", None, None, True)
        for name, mirror in (("版本号不同", latest("1.0.0", auto=False)), ("镜像更新", latest("1.0.2")),
                             ("SHA-256 不同", latest(sha="cd" * 32)), ("大小不同", latest(size=SIZE + 1))):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(mirror, github())
                self.assertEqual(result, update.CheckResult(github_only, "1.0.1", True, True, None))
                self.assertEqual(len(self.titles()), 1)
                self.assertIn("对不上，只认 GitHub", self.titles()[0])
                self.assertIn(SHA, self.errors.entries()[0][1])  # 两边各是什么写在详细信息里

    def test_mirror_missing(self):
        # 镜像没拿到：只认 GitHub。连不上、404 不记错误记录；版本文件不对（整份不认）记一条
        for name, mirror, logged in (("404", http_error(404), False), ("超时", timeout(), False),
                                     ("500", http_error(500), False), ("format 不对", latest(format=2), True),
                                     ("不是 JSON", b"<html>oops</html>", True),
                                     ("太大", b" " * (update.LATEST_LIMIT + 1), True)):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(mirror, github())
                self.assertEqual(result.release.urls, (github()["assets"][0]["browser_download_url"],))
                self.assertEqual((result.latest, result.reachable, result.mirror_ok, result.problem),
                                 ("1.0.1", True, False, None))
                self.assertIs(result.release.auto, True)
                if logged:
                    self.assertEqual(len(self.titles()), 1)
                    self.assertIn("下载镜像上的版本文件不对", self.titles()[0])
                else:
                    self.assertEqual(self.titles(), [])

    def test_github_missing(self):
        # GitHub 没拿到（404、403、429、5xx、超时、格式不对）：认镜像那一版，下载地址只有镜像。格式不对的记一条
        mirror_only = update._mirror_release(latest())
        for name, gh, logged in (("404", http_error(404), False), ("403", http_error(403), False),
                                 ("429", http_error(429), False), ("502", http_error(502), False),
                                 ("超时", timeout(), False), ("没有附件", github(assets=[]), True),
                                 ("tag 不对", github(tag_name="latest"), True), ("不是 JSON", b"not json", True)):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(latest(), gh)
                self.assertEqual(result, update.CheckResult(mirror_only, "1.0.1", True, True, None))
                self.assertEqual(result.release.urls, ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",))
                if logged:
                    self.assertEqual(len(self.titles()), 1)
                    self.assertIn("GitHub 回的内容不对", self.titles()[0])
                else:
                    self.assertEqual(self.titles(), [])

    def test_both_missing(self):
        result = self.check(timeout(), http_error(403))
        self.assertEqual((result.release, result.latest, result.reachable, result.mirror_ok), (None, None, False, False))
        self.assertIn("下载镜像：网络错误：操作超时（WinHTTP 12002）", result.problem)
        self.assertIn("GitHub：查询太频繁，暂时被限制（HTTP 403）", result.problem)
        result = self.check(http_error(404), http_error(404))
        self.assertIn("下载镜像：上面没有版本文件（HTTP 404）", result.problem)
        self.assertIn("GitHub：没有找到正式版（HTTP 404）", result.problem)
        result = self.check(latest(format=2), b"[]")
        self.assertEqual((result.reachable, result.mirror_ok), (False, False))
        self.assertIn("版本文件的内容不对", result.problem)
        self.assertIn("回的内容不对", result.problem)

    def test_both_missing_text(self):
        # 两边都问、都没拿到时的整句：两边的原因按问的先后（镜像在前）写在一对括号里，用分号隔开；只问一边时只写那一边
        # （ForkCheckTest.test_github_missing）
        result = self.check(timeout(), http_error(403))
        self.assertEqual(result.problem, "查不到版本信息（下载镜像：网络错误：操作超时（WinHTTP 12002）；"
                                         "GitHub：查询太频繁，暂时被限制（HTTP 403））。请检查网络连接，稍后再试。")

    def test_no_digest_same_version_and_size(self):
        # GitHub 的附件没有 digest、版本号和大小都和镜像一样：用镜像的 SHA-256，记一条说明，下载地址先镜像后 GitHub
        for digest in (DROP, "sha512:" + "ab" * 64):
            with self.subTest(digest=digest):
                self.errors.clear()
                result = self.check(latest(sha="ef" * 32, auto=False), github(digest=digest))
                self.assertEqual((result.release.sha256, result.release.auto, result.problem), ("ef" * 32, False, None))
                self.assertEqual(len(result.release.urls), 2)
                self.assertEqual(len(self.titles()), 1)
                self.assertIn("没有附 SHA-256，按下载镜像上的核对", self.titles()[0])

    def test_no_digest_not_accepted(self):
        # 没有 digest，镜像的版本号、大小对不上或镜像没拿到：这一版不认（下到的包没法核对），problem 写明，记一条
        for name, mirror, mirror_ok in (("大小不同", latest(size=SIZE + 1), True), ("版本不同", latest("1.0.2"), True),
                                        ("镜像没拿到", timeout(), False)):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(mirror, github(digest=DROP))
                self.assertEqual(result, update.CheckResult(None, None, True, mirror_ok, update.NO_DIGEST))
                self.assertEqual(len(self.titles()), 1)
                self.assertIn("这一版不认", self.titles()[0])

    def test_newer_and_skipped(self):
        # 比当前新、又不是跳过的那个版本才算「有新版本」；latest 不管新不新都写
        for current, skipped, has in (("1.0.0", None, True), ("1.0.0", "", True), ("1.0.0", "1.0.0", True),
                                      ("1.0.0", "1.0.1", False), ("1.0.0", "1.0.01", False), ("1.0.1", None, False),
                                      ("1.1.0", None, False), ("0.14.0", None, True)):
            with self.subTest(current=current, skipped=skipped):
                result = self.check(latest(), github(), current=current, skipped=skipped)
                self.assertEqual((result.release is not None, result.latest, result.problem), (has, "1.0.1", None))

    def test_request_headers(self):
        # 请求只带 User-Agent（工具名、版本号、源代码地址），GitHub 另带它要的两个头
        self.check(latest(), github())
        mirror, gh = (dict(r.header_items()) for r in self.session.requests)
        self.assertEqual(mirror, {"User-agent": update.USER_AGENT})
        self.assertEqual(gh, {"User-agent": update.USER_AGENT, "Accept": "application/vnd.github+json",
                              "X-github-api-version": "2022-11-28"})
        self.assertRegex(update.USER_AGENT, r"^AstralChipCompass/[0-9.]+ \(\+https://github\.com/s1nan-dev/"
                                            r"astral-chip-compass\)$")

    def test_own_session(self):
        # 不给会话时自己建、用完关掉；建不了时两边都算没拿到，原因写进 problem
        fake = FakeSession({update.LATEST_JSON: latest(), update.GITHUB_API: github()})
        with mock.patch.object(update.winhttp, "Session", return_value=fake):
            result = update.check("1.0.0")
        self.assertEqual(result.latest, "1.0.1")
        self.assertTrue(fake.closed)
        with mock.patch.object(update.winhttp, "Session", side_effect=urllib.error.URLError("WinHTTP 错误 87")):
            result = update.check("1.0.0")
        self.assertEqual((result.release, result.latest, result.reachable, result.mirror_ok), (None, None, False, False))
        self.assertIn("建不了联网会话", result.problem)

    def test_stop(self):
        # 停止标志置位：抛 winhttp.Stopped，不当成没拿到
        session = FakeSession({update.LATEST_JSON: winhttp.Stopped(), update.GITHUB_API: github()})
        with self.assertRaises(winhttp.Stopped):
            update.check("1.0.0", session=session)
        stop = threading.Event()
        stop.set()
        session = FakeSession({})
        session.stream = mock.Mock(side_effect=winhttp.Stopped)
        with self.assertRaises(winhttp.Stopped):
            update.check("1.0.0", session=session, stop=stop)
        self.assertIs(session.stream.call_args.args[3], stop)


class CheckManifestTest(CheckCase):
    """认清单（模块说明「认清单」）：能拿到 GitHub 的那一版时以它的清单附件为准，镜像上的一样时清单地址先镜像后 GitHub；GitHub
    没拿到时用镜像的。压缩包照「认哪一版」那一套认，不受清单影响。"""

    BOTH_ZIPS = ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",
                 "https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.1/ACC-1.0.1.zip")

    @staticmethod
    def files_of(result: update.CheckResult) -> tuple:
        release = result.release
        return release.files_sha256, release.files_size, release.files_urls

    def test_both_sides_same(self):
        # 两边的压缩包、清单都对得上：清单地址先镜像后 GitHub，不写错误记录
        result = self.check(latest(**FILES), with_files(github()))
        self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (MIRROR_FILES, GITHUB_FILES)))
        self.assertEqual(result.release.urls, self.BOTH_ZIPS)
        self.assertEqual(self.errors.entries(), [])

    def test_mirror_manifest_differs(self):
        # 压缩包对得上、镜像的清单 SHA-256 或大小和 GitHub 的不一样：清单只从 GitHub 下，写一条错误记录；压缩包照样先镜像
        for name, change in (("SHA-256 不同", {"files_sha256": "cd" * 32}), ("大小不同", {"files_size": FILES_SIZE + 1})):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(latest(**{**FILES, **change}), with_files(github()))
                self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (GITHUB_FILES,)))
                self.assertEqual(result.release.urls, self.BOTH_ZIPS)
                self.assertEqual(len(self.titles()), 1)
                self.assertIn("文件清单和 GitHub 上的对不上", self.titles()[0])
                self.assertIn(FILES_SHA, self.errors.entries()[0][1])

    def test_only_github_has_manifest(self):
        # 镜像的版本文件里没有清单（旧的写法）、或清单那几项不对：用 GitHub 的；后一种写一条错误记录
        result = self.check(latest(), with_files(github()))
        self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (GITHUB_FILES,)))
        self.assertEqual(self.errors.entries(), [])
        result = self.check(latest(**{**FILES, "files_size": 0}), with_files(github()))
        self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (GITHUB_FILES,)))
        self.assertEqual(result.release.urls, self.BOTH_ZIPS)
        self.assertEqual(len(self.titles()), 1)
        self.assertIn("文件清单的几项不对", self.titles()[0])

    def test_github_cannot_vouch(self):
        # GitHub 拿到了这一版，却没有清单附件、附件没有 digest：这一版不走差分（镜像上的清单对照不了 GitHub，不信）。
        # 镜像上有清单时写一条错误记录（附件用不上的另写一条）；压缩包照常认
        for name, gh, titles in (("没有附件", github(), ["GitHub 上却没有能核对它的"]),
                                 ("附件没有 digest", with_files(github(), digest=DROP),
                                  ["文件清单附件用不上", "GitHub 上却没有能核对它的"])):
            with self.subTest(name):
                self.errors.clear()
                result = self.check(latest(**FILES), gh)
                self.assertEqual(self.files_of(result), (None, None, ()))
                self.assertEqual((result.release.urls, result.release.sha256), (self.BOTH_ZIPS, SHA))
                self.assertEqual(len(self.titles()), len(titles))
                for title, part in zip(self.titles(), titles):
                    self.assertIn(part, title)
        # 两边都没有清单：没有清单，不写错误记录
        self.errors.clear()
        result = self.check(latest(), github())
        self.assertEqual(self.files_of(result), (None, None, ()))
        self.assertEqual(self.errors.entries(), [])

    def test_github_missing_uses_mirror(self):
        # GitHub 没拿到：用镜像的三项（地址只有镜像上的）；三项不对时没有清单、版本照常认，写一条错误记录
        for gh in (http_error(404), http_error(429), timeout()):
            with self.subTest(gh=gh):
                result = self.check(latest(**FILES), gh)
                self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (MIRROR_FILES,)))
        self.assertEqual(self.errors.entries(), [])
        result = self.check(latest(**{**FILES, "files": "releases/ACC-1.0.0.files.json"}), http_error(404))
        self.assertEqual((result.latest, self.files_of(result)), ("1.0.1", (None, None, ())))
        self.assertEqual(len(self.titles()), 1)
        self.assertIn("文件清单的几项不对", self.titles()[0])

    def test_github_only_release(self):
        # 压缩包对不上（只认 GitHub 那一版）、镜像没拿到：清单也只用 GitHub 的，镜像上的就算一样也不用
        for name, mirror in (("SHA-256 不同", latest(sha="cd" * 32, **FILES)),
                             ("镜像是旧版本", latest("1.0.0", files="releases/ACC-1.0.0.files.json",
                                                    files_size=FILES_SIZE, files_sha256=FILES_SHA)),
                             ("镜像没拿到", timeout())):
            with self.subTest(name):
                result = self.check(mirror, with_files(github()))
                self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (GITHUB_FILES,)))
                self.assertEqual(result.release.urls, (self.BOTH_ZIPS[1],))
        # 只认 GitHub 那一版、它又没有清单附件：没有清单
        result = self.check(latest(sha="cd" * 32, **FILES), github())
        self.assertEqual(self.files_of(result), (None, None, ()))

    def test_zip_without_digest(self):
        # GitHub 的压缩包没有 digest、版本号和大小和镜像一样（按镜像的 SHA-256 核对）：清单照样以 GitHub 的附件为准
        result = self.check(latest(**FILES), with_files(github(digest=DROP)))
        self.assertEqual(result.release.sha256, SHA)
        self.assertEqual(self.files_of(result), (FILES_SHA, FILES_SIZE, (MIRROR_FILES, GITHUB_FILES)))
        self.assertEqual(len(self.titles()), 1)
        self.assertIn("没有附 SHA-256，按下载镜像上的核对", self.titles()[0])


class SourcesTest(unittest.TestCase):
    """sources：由源代码地址推出问哪几边。下载镜像只认和 MIRROR_SOURCE 一字不差的；GitHub 只认 https://github.com/<owner>/<repo>
    这一种写法（发布页、附件地址的前缀也是按源代码地址原样拼的），owner、repo 的大小写照原样。"""

    @staticmethod
    def api(owner_repo: str) -> str:
        return f"https://api.github.com/repos/{owner_repo}/releases/latest"

    def test_official(self):
        self.assertEqual(update.sources(update.MIRROR_SOURCE), (True, self.api("s1nan-dev/astral-chip-compass")))
        self.assertEqual((update.USE_MIRROR, update.GITHUB_API), update.sources(app_version.SOURCE_URL))
        self.assertEqual((update.USE_MIRROR, update.GITHUB_API), (True, self.api("s1nan-dev/astral-chip-compass")))

    def test_other_repos(self):
        # 复刻的仓库：不用镜像，GitHub 的接口照它的 owner、repo 拼。GitHub 许可的字符（字母、数字、-、_、.）都认，大小写照原样；
        # 和 MIRROR_SOURCE 只差大小写的也不是镜像上那个仓库
        for source, owner_repo in ((FORK, "someone/acc-fork"), ("https://github.com/A-b_9/x.y_z-1", "A-b_9/x.y_z-1"),
                                   ("https://github.com/someone/.github", "someone/.github"),
                                   ("https://github.com/someone/v1...x", "someone/v1...x"),
                                   ("https://github.com/S1nan-Dev/Astral-Chip-Compass", "S1nan-Dev/Astral-Chip-Compass")):
            with self.subTest(source=source):
                use_mirror, api = update.sources(source)
                self.assertEqual((use_mirror, api), (False, self.api(owner_repo)))
                parts = urllib.parse.urlsplit(api)
                self.assertEqual((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment),
                                 ("https", "api.github.com", f"/repos/{owner_repo}/releases/latest", "", ""))

    def test_other_spellings(self):
        # 末尾带「/」或「.git」、协议和主机名不是小写的：不问 GitHub（GitHub 回的地址对不上原样拼的前缀，问了也认不下）；
        # 官方仓库这样写也不用镜像（只认一字不差）
        official = update.MIRROR_SOURCE
        for source in (official + "/", official + ".git", official + ".GIT", official + ".git/", official + "//",
                       official.upper(), official.replace("github.com", "GitHub.com"), official.replace("https", "HTTPS"),
                       FORK + "/", FORK + ".git"):
            with self.subTest(source=source):
                self.assertEqual(update.sources(source), (False, None))

    def test_not_github(self):
        # 不在 GitHub 上的（复刻到别的网站）、不是仓库地址的：两边都不问
        for source in ("https://gitee.com/someone/acc-fork", "https://codeberg.org/someone/acc-fork",
                       "http://github.com/someone/acc-fork", "https://www.github.com/someone/acc-fork",
                       "https://api.github.com/repos/someone/acc-fork", "https://github.com/someone",
                       "https://github.com/someone/acc-fork/releases", "https://github.com.example/someone/acc-fork",
                       "github.com/someone/acc-fork", "", None, 5, b"https://github.com/someone/acc-fork"):
            with self.subTest(source=source):
                self.assertEqual(update.sources(source), (False, None))

    def test_unsafe(self):
        # 拼进接口地址会跑到别的主机、别的路径，或者不是 GitHub 许可的字符：不认
        for source in ("https://github.com/../acc-fork", "https://github.com/someone/..", "https://github.com/./acc-fork",
                       "https://github.com/someone/.", "https://github.com/someone/acc-fork?x=1",
                       "https://github.com/someone/acc-fork#x", "https://user@github.com/someone/acc-fork",
                       "https://github.com@evil.example/someone/acc-fork", "https://github.com:443/someone/acc-fork",
                       "https://github.com/some one/acc-fork", "https://github.com/someone/acc-fork ",
                       "https://github.com/someone/acc-fork%2F..%2F..", "https://github.com/someone/%2e%2e",
                       "https://github.com/someone/仓库", "https://github.com/someone/ａcc", "https://github.com/someone/acc​",
                       "https://github.com/someone/acc-fork\n", "https://github.com/someone\\acc-fork",
                       "https://github.com/someone/acc-fork/../x", "https://github.com//acc-fork"):
            with self.subTest(source=source):
                self.assertEqual(update.sources(source), (False, None))


class ForkCheckTest(CheckCase):
    """复刻的版本（源代码地址换成了复刻的仓库，as_fork）：只问 GitHub，下载镜像一次都不请求；认下的版本只有 GitHub 上的地址
    （下载地址、清单地址里都没有镜像）；GitHub 没拿到时原因只写 GitHub。源代码地址推不出 GitHub 的接口、又不用镜像时两边都不问。"""

    def setUp(self):
        super().setUp()
        as_fork(self)

    def ask(self, gh) -> update.CheckResult:
        """镜像上照样放着版本文件（官方的那一版，带清单），GitHub 回 gh：只该问 GitHub。"""
        self.session = FakeSession({update.LATEST_JSON: latest(**FILES), FORK_API: gh})
        result = update.check("1.0.0", session=self.session)
        self.assertEqual([r.full_url for r in self.session.requests], [FORK_API])
        return result

    def test_only_github(self):
        result = self.ask(forked(with_files(github())))
        zip_url = f"{FORK}/releases/download/v1.0.1/ACC-1.0.1.zip"
        files_url = f"{FORK}/releases/download/v1.0.1/ACC-1.0.1.files.json"
        self.assertEqual(result, update.CheckResult(
            update.Release("1.0.1", SHA, SIZE, (zip_url,), f"{FORK}/releases/tag/v1.0.1", None, None, True,
                           FILES_SHA, FILES_SIZE, (files_url,)),
            "1.0.1", True, False, None))
        mirror_host = urllib.parse.urlsplit(update.MIRROR_BASE).hostname
        for url in (*result.release.urls, *result.release.files_urls, result.release.page):
            self.assertNotEqual(urllib.parse.urlsplit(url).hostname, mirror_host, url)
        self.assertEqual(self.errors.entries(), [])
        # 没有清单附件时只能下整包，同样只有 GitHub 的地址
        result = self.ask(forked(github()))
        self.assertEqual((result.release.urls, result.release.files_urls), ((zip_url,), ()))

    def test_github_missing(self):
        # GitHub 没拿到：两边都没拿到，原因只写 GitHub（镜像没问，不写）
        for gh, why in ((http_error(404), "没有找到正式版（HTTP 404）"), (http_error(429), "查询太频繁，暂时被限制（HTTP 429）"),
                        (timeout(), "网络错误：操作超时（WinHTTP 12002）"), (b"not json", "回的内容不对")):
            with self.subTest(why=why):
                result = self.ask(gh)
                self.assertEqual(result, update.CheckResult(
                    None, None, False, False, f"查不到版本信息（GitHub：{why}）。请检查网络连接，稍后再试。"))
                self.assertNotIn("镜像", result.problem)

    def test_no_source(self):
        # 源代码地址推不出 GitHub 的接口（复刻到别的网站、写法不对），又不用镜像：两边都不问，不联网（会话也不建），原因是
        # NO_SOURCE；一条错误记录都不写
        for source in ("https://gitee.com/someone/acc-fork", FORK + ".git"):
            with self.subTest(source=source):
                as_fork(self, source)
                session = FakeSession({update.LATEST_JSON: latest(), FORK_API: forked(github())})
                self.assertEqual(update.check("1.0.0", session=session),
                                 update.CheckResult(None, None, False, False, update.NO_SOURCE))
                self.assertEqual(session.requests, [])
                with mock.patch.object(update.winhttp, "Session", side_effect=AssertionError("不该建会话")):
                    self.assertEqual(update.check("1.0.0").problem, update.NO_SOURCE)
        self.assertEqual(self.errors.entries(), [])
        # 写法同 check 别的 problem：句号结尾（界面套进「检查更新失败：{reason}。」时去掉这个句号）；不说连不上、不说已是最新
        self.assertTrue(update.NO_SOURCE.endswith("。"))
        for word in ("连不上", "网络", "最新"):
            self.assertNotIn(word, update.NO_SOURCE)

    def test_own_mirror_without_github(self):
        # 改版架了自己的镜像（MIRROR_SOURCE 换成了自己的源代码地址）、源代码地址却推不出 GitHub 的接口：只问镜像，没拿到时原因
        # 只写镜像
        with mock.patch.object(update, "USE_MIRROR", True), mock.patch.object(update, "GITHUB_API", None):
            self.session = FakeSession({update.LATEST_JSON: timeout(), FORK_API: forked(github())})
            result = update.check("1.0.0", session=self.session)
        self.assertEqual([r.full_url for r in self.session.requests], [update.LATEST_JSON])
        self.assertEqual((result.reachable, result.mirror_ok, result.problem),
                         (False, False, "查不到版本信息（下载镜像：网络错误：操作超时（WinHTTP 12002））。请检查网络连接，稍后再试。"))


class CheckLiveTest(unittest.TestCase):
    """check 用真的 winhttp.Session 问本机的假服务器（LATEST_JSON、GITHUB_API 换成它的地址）：请求头真的发出去了，回话
    按块收齐、太大时不收。"""

    def setUp(self):
        self.server = start_server(self)
        self.errors = capture_errors(self)
        for name, path in (("LATEST_JSON", "/latest.json"), ("GITHUB_API", "/api")):
            patcher = mock.patch.object(update, name, self.server.url(path))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_real_session(self):
        Handler.routes = {"/latest.json": json.dumps(latest()).encode(), "/api": json.dumps(github()).encode()}
        result = update.check("1.0.0")
        self.assertEqual(result.release.urls, ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",
                                               github()["assets"][0]["browser_download_url"]))
        (path1, mirror), (path2, gh) = Handler.seen
        self.assertEqual((path1, path2), ("/latest.json", "/api"))
        self.assertEqual(mirror.get_all("User-Agent"), [update.USER_AGENT])
        self.assertIsNone(mirror.get("X-GitHub-Api-Version"))
        self.assertEqual(gh.get_all("User-Agent"), [update.USER_AGENT])
        self.assertEqual(gh.get("Accept"), "application/vnd.github+json")
        self.assertEqual(gh.get("X-GitHub-Api-Version"), "2022-11-28")
        self.assertEqual(self.errors.entries(), [])

    def test_oversized_and_missing(self):
        # 版本文件超过 LATEST_LIMIT 不收（记一条），GitHub 回 404：两边都没拿到
        Handler.routes = {"/latest.json": b"{" + b" " * update.LATEST_LIMIT + b"}"}
        result = update.check("1.0.0")
        self.assertEqual((result.reachable, result.mirror_ok), (False, False))
        self.assertIn("没有找到正式版（HTTP 404）", result.problem)
        self.assertEqual(len(self.errors.entries()), 1)
        self.assertIn("超过", self.errors.entries()[0][1])


# ====================================================================== 下载


PAYLOAD = bytes(range(256)) * 1200 + b"tail"  # 300 KiB 上下：比一块（winhttp.CHUNK）大好几倍


def release_for(urls, payload=PAYLOAD, version="1.0.1") -> update.Release:
    return update.Release(version, hashlib.sha256(payload).hexdigest(), len(payload), tuple(urls),
                          "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v" + version, None, None, True)


class DownloadTest(unittest.TestCase):
    """download：按 release.urls 的先后下到 update\\ACC-X.Y.Z.zip，边下边写、边算 SHA-256（真的 WinHTTP、本机的假服务器）。"""

    def setUp(self):
        self.server = start_server(self)
        self.root = sandbox.temp_dir(self, "update-download-")
        (self.root / "app").mkdir()
        (self.root / "app" / "keep.txt").write_bytes(b"old program")
        self.folder = self.root / update.UPDATE_DIR
        self.target = self.folder / "ACC-1.0.1.zip"

    def download(self, *paths, payload=PAYLOAD, **kwargs) -> Path:
        return update.download(release_for([self.server.url(p) for p in paths], payload), self.root, **kwargs)

    def left(self) -> list[str]:
        return sorted(p.name for p in self.folder.iterdir()) if self.folder.exists() else []

    def assert_root_untouched(self):
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(["app", update.UPDATE_DIR]))
        self.assertEqual((self.root / "app" / "keep.txt").read_bytes(), b"old program")

    def test_first_url_fails_second_used(self):
        # 先镜像后 GitHub：第一个地址回 404、内容不对（大小一样、SHA-256 不同）时换第二个
        Handler.routes = {"/good": PAYLOAD, "/wrong": bytes(len(PAYLOAD))}
        for first in ("/missing", "/wrong"):
            with self.subTest(first=first):
                Handler.seen.clear()
                self.assertEqual(self.download(first, "/good"), self.target)
                self.assertEqual(self.target.read_bytes(), PAYLOAD)
                self.assertEqual([path for path, _ in Handler.seen], [first, "/good"])
                self.assertEqual(self.left(), ["ACC-1.0.1.zip"])  # .part 改了名，不留
                self.target.unlink()
        self.assertEqual(Handler.seen[0][1].get_all("User-Agent"), [update.USER_AGENT])
        self.assert_root_untouched()

    def test_all_fail(self):
        # 每个地址都不行：抛 UpdateError，写明每个地址为什么不行；update 里不留东西
        Handler.routes = {"/short": PAYLOAD[:-1], "/wrong": bytes(len(PAYLOAD)), "/big": PAYLOAD + b"x" * 300000,
                          "/teapot": ("status", 418)}
        with self.assertRaises(update.UpdateError) as caught:
            self.download("/short", "/wrong", "/big", "/teapot", "/missing")
        text = str(caught.exception)
        for part in (f"大小不对（下到 {len(PAYLOAD) - 1} 字节，应为 {len(PAYLOAD)} 字节）", "校验值（SHA-256）对不上",
                     f"下到的比应有的 {len(PAYLOAD)} 字节多", "HTTP 418", "HTTP 404"):
            self.assertIn(part, text)
        self.assertIn("127.0.0.1：", text)
        self.assertEqual(self.left(), [])
        self.assert_root_untouched()

    def test_redirect_followed(self):
        Handler.routes = {"/r2": ("redirect", "/files/ACC-1.0.1.zip"), "/files/ACC-1.0.1.zip": PAYLOAD}
        self.assertEqual(self.download("/r2").read_bytes(), PAYLOAD)
        self.assertEqual([path for path, _ in Handler.seen], ["/r2", "/files/ACC-1.0.1.zip"])

    def test_progress(self):
        Handler.routes = {"/good": PAYLOAD}
        calls = []
        with mock.patch.object(update, "PROGRESS_STEP", 64 * 1024):
            self.download("/missing", "/good", progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls[0], (0, len(PAYLOAD)))  # 每个地址从 0 开始（第一个回 404，只报了开头）
        self.assertEqual(calls[1], (0, len(PAYLOAD)))
        self.assertEqual(calls[-1], (len(PAYLOAD), len(PAYLOAD)))
        done = [d for d, _ in calls[1:]]
        self.assertEqual(done, sorted(done))
        self.assertGreater(len(calls), 4)
        self.assertEqual({total for _, total in calls}, {len(PAYLOAD)})

    def test_stop_mid_download(self):
        # 停止：删掉 .part，抛 winhttp.Stopped，不再换下一个地址
        Handler.routes = {"/slow": ("trickle", len(PAYLOAD)), "/good": PAYLOAD}
        stop = threading.Event()

        def progress(done, total):
            if done:
                stop.set()

        with mock.patch.object(update, "PROGRESS_STEP", 1), self.assertRaises(winhttp.Stopped):
            self.download("/slow", "/good", progress=progress, stop=stop)
        self.assertEqual([path for path, _ in Handler.seen], ["/slow"])
        self.assertEqual(self.left(), [])
        self.assert_root_untouched()

    def test_stopped_before_start(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(winhttp.Stopped):
            self.download("/good", stop=stop)
        self.assertEqual(Handler.seen, [])
        self.assertFalse(self.folder.exists())

    def test_stop_after_last_chunk(self):
        # 收齐的那一刻停下（进度报到最后一块时置位，假的会话收完不再看停止标志）：照样作废，不留压缩包，不换地址
        session = FakeSession({"https://a.example/x.zip": PAYLOAD, "https://b.example/x.zip": PAYLOAD})
        release = update.Release("1.0.1", hashlib.sha256(PAYLOAD).hexdigest(), len(PAYLOAD),
                                 ("https://a.example/x.zip", "https://b.example/x.zip"), "p", None, None, True)
        stop = threading.Event()
        with self.assertRaises(winhttp.Stopped):
            update.download(release, self.root, session=session, stop=stop,
                            progress=lambda done, total: done == total and stop.set())
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(self.left(), [])

    def test_existing_zip_reused(self):
        # 上次已经下好的同一个包直接用，不再下；在、却不对的重新下
        self.folder.mkdir()
        self.target.write_bytes(PAYLOAD)
        self.assertEqual(self.download("/good"), self.target)
        self.assertEqual(Handler.seen, [])
        self.target.write_bytes(bytes(len(PAYLOAD)))
        Handler.routes = {"/good": PAYLOAD}
        self.assertEqual(self.download("/good").read_bytes(), PAYLOAD)
        self.assertEqual([path for path, _ in Handler.seen], ["/good"])

    def test_stale_part_overwritten(self):
        self.folder.mkdir()
        (self.folder / "ACC-1.0.1.zip.part").write_bytes(b"left from last time" * 100000)
        Handler.routes = {"/good": PAYLOAD}
        self.assertEqual(self.download("/good").read_bytes(), PAYLOAD)
        self.assertEqual(self.left(), ["ACC-1.0.1.zip"])

    def test_unwritable_not_retried_elsewhere(self):
        # .part 写不进（这里它被一个同名文件夹占着）：换地址也一样，直接抛 UpdateError，不说网络的事，也不发请求
        self.folder.mkdir()
        (self.folder / "ACC-1.0.1.zip.part").mkdir()
        Handler.routes = {"/good": PAYLOAD}
        with self.assertRaises(update.UpdateError) as caught:
            self.download("/good", "/good")
        self.assertIn("存不进", str(caught.exception))
        self.assertNotIn("下载失败", str(caught.exception))
        self.assertEqual(Handler.seen, [])

    def test_write_error_mid_download(self):
        # 写到一半写不进（磁盘满了）：抛 UpdateError 说空间不够，删掉 .part，不换地址
        session = FakeSession({"https://a.example/x.zip": PAYLOAD, "https://b.example/x.zip": PAYLOAD})
        full = OSError(28, "No space left on device")
        real_open = open

        class Full:
            def __init__(self, f):
                self.f = f

            def write(self, data):
                raise full

            def __getattr__(self, name):
                return getattr(self.f, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()

        def fake_open(path, mode="r", *args, **kwargs):
            f = real_open(path, mode, *args, **kwargs)
            return Full(f) if str(path).endswith(".part") else f

        release = update.Release("1.0.1", hashlib.sha256(PAYLOAD).hexdigest(), len(PAYLOAD),
                                 ("https://a.example/x.zip", "https://b.example/x.zip"), "p", None, None, True)
        with mock.patch("builtins.open", fake_open), self.assertRaises(update.UpdateError) as caught:
            update.download(release, self.root, session=session)
        self.assertIn("磁盘空间不够", str(caught.exception))
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(self.left(), [])

    def test_own_session_closed(self):
        fake = FakeSession({"https://a.example/x.zip": PAYLOAD})
        release = update.Release("1.0.1", hashlib.sha256(PAYLOAD).hexdigest(), len(PAYLOAD),
                                 ("https://a.example/x.zip",), "p", None, None, True)
        with mock.patch.object(update.winhttp, "Session", return_value=fake):
            self.assertEqual(update.download(release, self.root).read_bytes(), PAYLOAD)
        self.assertTrue(fake.closed)
        self.target.unlink()
        with mock.patch.object(update.winhttp, "Session", side_effect=urllib.error.URLError("WinHTTP 错误 87")), \
                self.assertRaises(update.UpdateError) as caught:
            update.download(release, self.root)
        self.assertIn("建不了联网会话", str(caught.exception))

    def test_bad_release(self):
        for release in (release_for(["http://127.0.0.1:1/x"], version="../1.0.1"), release_for([])):
            with self.subTest(release=release), self.assertRaises(update.UpdateError):
                update.download(release, self.root)
        self.assertFalse(self.folder.exists())

    def test_source_names(self):
        self.assertEqual(update._source("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip"), "下载镜像")
        self.assertEqual(update._source("https://github.com/s1nan-dev/astral-chip-compass/releases/download/v1.0.1/"
                                        "ACC-1.0.1.zip"), "GitHub")
        self.assertEqual(update._source("https://release-assets.githubusercontent.com/x"), "GitHub")
        self.assertEqual(update._source("http://127.0.0.1:8000/x"), "127.0.0.1")


# ====================================================================== 备好


GOOD = [
    ("AstralChipCompass/拾星助手.exe", b"launcher"),
    ("AstralChipCompass/使用说明.html", "<html>使用说明</html>".encode()),
    ("AstralChipCompass/app/ACC.exe", b"MZ program"),
    ("AstralChipCompass/app/data/meta.json", b"{}"),
    ("AstralChipCompass/app/LICENSES/AGPL-3.0.txt", b"license" * 1000),
]


def make_zip(path: Path, entries, compression=zipfile.ZIP_DEFLATED) -> Path:
    """按 entries（[(名字, 内容)]，名字以「/」结尾的是文件夹）造压缩包。"""
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def tree(folder: Path) -> dict[str, bytes | None]:
    """folder 下的每一项：相对路径 → 文件内容（文件夹为 None）。"""
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


class PrepareTest(unittest.TestCase):
    """prepare：核对、解到 update\\new.part、改名成 update\\new、删压缩包，最后写 ready.json。exe_version 换成假的。"""

    def setUp(self):
        self.root = sandbox.temp_dir(self, "update-prepare-")
        (self.root / "app").mkdir()
        (self.root / "app" / "ACC.exe").write_bytes(b"old program")
        (self.root / "拾星助手.exe").write_bytes(b"old launcher")
        self.folder = self.root / update.UPDATE_DIR
        self.folder.mkdir()
        self.zip = self.folder / "ACC-1.0.1.zip"
        self.release = release_for(["https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip"])
        self.version = (1, 0, 1, 0)
        self.asked: list[Path] = []

        def fake_version(path):
            self.asked.append(Path(path))
            return self.version

        patcher = mock.patch.object(update, "exe_version", side_effect=fake_version)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.outside = self.snapshot()

    def snapshot(self) -> dict:
        """update 以外的东西。"""
        return {k: v for k, v in tree(self.root).items() if not k.startswith(update.UPDATE_DIR)}

    def prepare(self, entries=GOOD, compression=zipfile.ZIP_DEFLATED):
        make_zip(self.zip, entries, compression)
        update.prepare(self.zip, self.release, self.root)

    def assert_failed_clean(self, ready_gone=True):
        """出错以后：new.part 删干净，没写 ready.json，update 以外的东西没被碰。"""
        self.assertFalse((self.folder / update.NEW_PART).exists())
        if ready_gone:
            self.assertFalse((self.folder / update.READY_FILE).exists())
        self.assertEqual(self.snapshot(), self.outside)

    def test_prepared(self):
        self.prepare(GOOD + [("AstralChipCompass/", b""), ("AstralChipCompass/app/models/", b"")])
        new = self.folder / update.NEW_DIR
        self.assertEqual(tree(new), {
            "app": None, "app/ACC.exe": b"MZ program", "app/LICENSES": None, "app/LICENSES/AGPL-3.0.txt": b"license" * 1000,
            "app/data": None, "app/data/meta.json": b"{}", "app/models": None, "使用说明.html": "<html>使用说明</html>".encode(),
            "拾星助手.exe": b"launcher"})
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [update.NEW_DIR, update.READY_FILE])  # 压缩包删了
        ready = json.loads((self.folder / update.READY_FILE).read_text(encoding="utf-8"))
        self.assertEqual({k: ready[k] for k in ("format", "version", "sha256")},
                         {"format": 1, "version": "1.0.1", "sha256": self.release.sha256})
        self.assertRegex(ready["prepared"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(self.asked, [self.folder / update.NEW_PART / "app" / "ACC.exe"])  # 解在 new.part 里时核对
        self.assertEqual(self.snapshot(), self.outside)

    def test_stored_zip(self):
        self.prepare(compression=zipfile.ZIP_STORED)
        self.assertTrue((self.folder / update.NEW_DIR / "app" / "ACC.exe").is_file())

    def test_ready_written_last(self):
        # ready.json 是最后写的：写的那一刻 new 已经换好、new.part 和压缩包都没了
        real = update._write_ready
        seen = []

        def write_ready(path, release):
            seen.append(sorted(p.name for p in self.folder.iterdir()))
            real(path, release)

        with mock.patch.object(update, "_write_ready", side_effect=write_ready):
            self.prepare()
        self.assertEqual(seen, [[update.NEW_DIR]])

    def test_replaces_previous(self):
        # 之前备好的版本：先作废 ready.json，换成新的 new
        old = self.folder / update.NEW_DIR
        (old / "app").mkdir(parents=True)
        (old / "app" / "stale.dll").write_bytes(b"stale")
        (self.folder / update.READY_FILE).write_text('{"format": 1, "version": "1.0.0"}', encoding="utf-8")
        (self.folder / update.NEW_PART).mkdir()  # 上次没做完的
        (self.folder / update.NEW_PART / "junk").write_bytes(b"junk")
        self.prepare()
        self.assertFalse((old / "app" / "stale.dll").exists())
        self.assertTrue((old / "app" / "ACC.exe").is_file())
        self.assertEqual(json.loads((self.folder / update.READY_FILE).read_text(encoding="utf-8"))["version"], "1.0.1")
        self.assertFalse((self.folder / update.NEW_PART).exists())

    def test_bad_names(self):
        # 名字不对的：一个都不解，update 以外的东西不碰，什么都不写到 AstralChipCompass/ 外面
        for name in ("AstralChipCompass/../evil.txt", "AstralChipCompass/app/../../evil.txt", "../evil.txt",
                     "/AstralChipCompass/app/evil.txt", "C:/AstralChipCompass/evil.txt", "C:evil.txt",
                     "AstralChipCompass/C:/evil.txt", "AstralChipCompass/app/a:b.txt", "AstralChipCompass/app/x.txt:ads",
                     "Other/evil.txt", "evil.txt", "astralchipcompass/evil.txt", "AstralChipCompass//evil.txt",
                     "AstralChipCompass/./evil.txt", "AstralChipCompass/app/CON", "AstralChipCompass/app/nul.txt",
                     "AstralChipCompass/app/COM1.log", "AstralChipCompass/app/evil.", "AstralChipCompass/app/evil ",
                     "AstralChipCompass/app/a<b.txt", "AstralChipCompass/app/a\x01b.txt", "AstralChipCompass\\..\\evil.txt",
                     "AstralChipCompass"):
            with self.subTest(name=name):
                with self.assertRaises(update.UpdateError):
                    self.prepare(GOOD + [(name, b"evil")])
                self.assert_failed_clean()
                self.assertFalse((self.folder / update.NEW_DIR).exists())
                found = [k for k, v in tree(self.root).items() if v == b"evil"]
                self.assertEqual(found, [])

    def test_case_insensitive_duplicate(self):
        with self.assertRaises(update.UpdateError) as caught:
            self.prepare(GOOD + [("AstralChipCompass/App/acc.exe", b"other")])
        self.assertIn("重名", str(caught.exception))
        self.assert_failed_clean()

    def test_missing_required(self):
        for missing in ("AstralChipCompass/app/ACC.exe", "AstralChipCompass/拾星助手.exe"):
            with self.subTest(missing=missing):
                entries = [e for e in GOOD if e[0] != missing]
                with self.assertRaises(update.UpdateError) as caught:
                    self.prepare(entries)
                self.assertIn(missing.split("/", 1)[1], str(caught.exception))
                self.assert_failed_clean()
        # 只有同名的文件夹不算
        entries = [e for e in GOOD if e[0] != "AstralChipCompass/app/ACC.exe"] + [("AstralChipCompass/app/ACC.exe/", b"")]
        with self.assertRaises(update.UpdateError):
            self.prepare(entries)
        self.assert_failed_clean()

    def test_file_count_limit(self):
        with mock.patch.object(update, "MAX_FILES", len(GOOD) - 1), self.assertRaises(update.UpdateError) as caught:
            self.prepare()
        self.assertIn("超过", str(caught.exception))
        self.assert_failed_clean()
        with mock.patch.object(update, "MAX_FILES", len(GOOD)):
            self.prepare()

    def test_size_limit(self):
        total = sum(len(data) for _, data in GOOD)
        with mock.patch.object(update, "MAX_UNPACKED", total - 1), self.assertRaises(update.UpdateError):
            self.prepare()
        self.assert_failed_clean()
        with mock.patch.object(update, "MAX_UNPACKED", total):
            self.prepare()

    def test_version_mismatch(self):
        for found in ((1, 0, 0, 0), (1, 0, 1, 1), (1, 1, 0, 0), None):
            with self.subTest(found=found):
                self.version = found
                with self.assertRaises(update.UpdateError) as caught:
                    self.prepare()
                self.assertIn("应为 1.0.1", str(caught.exception))
                self.assert_failed_clean()
                self.assertFalse((self.folder / update.NEW_DIR).exists())
                self.assertTrue(self.zip.exists())  # 没备好时压缩包留着

    def test_failure_drops_previous_ready(self):
        # 出错时之前备好的版本已经作废（ready.json 第一步就删了），旧的 new 留着等收尾时删
        (self.folder / update.NEW_DIR / "app").mkdir(parents=True)
        (self.folder / update.READY_FILE).write_text('{"format": 1, "version": "1.0.0"}', encoding="utf-8")
        self.version = None
        with self.assertRaises(update.UpdateError):
            self.prepare()
        self.assert_failed_clean()
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["ACC-1.0.1.zip", update.NEW_DIR])

    def test_file_and_folder_same_name(self):
        # app/data 是文件，又有 app/data/meta.json（不分大小写）：解的时候建不出来，解之前就不认
        for name in ("AstralChipCompass/app/DATA", "AstralChipCompass/app/data/meta.json/x"):
            with self.subTest(name=name):
                with self.assertRaises(update.UpdateError) as caught:
                    self.prepare(GOOD + [(name, b"evil")])
                self.assertIn("同名", str(caught.exception))
                self.assert_failed_clean()

    def test_encrypted_and_other_compression(self):
        # 加密的（标志位第 0、6 位）、打过补丁的（第 5 位）、不压缩和 deflate 以外的压缩方式：解之前就不认（_members 看的是
        # 中央目录里的这几项，这里直接造 ZipInfo）
        def infos(**changes):
            out = []
            for name, data in GOOD + [("AstralChipCompass/app/x.bin", b"x" * 100)]:
                info = zipfile.ZipInfo(name)
                info.compress_type, info.file_size = zipfile.ZIP_DEFLATED, len(data)
                out.append(info)
            for key, value in changes.items():
                setattr(out[-1], key, value)
            return out

        self.assertEqual(len(update._members(infos())), len(GOOD) + 1)
        self.assertEqual(len(update._members(infos(compress_type=zipfile.ZIP_STORED))), len(GOOD) + 1)
        for bits in (0x1, 0x20, 0x40, 0x41):
            with self.subTest(bits=bits), self.assertRaises(update.UpdateError) as caught:
                update._members(infos(flag_bits=bits))
            self.assertIn("加密", str(caught.exception))
        self.assertEqual(len(update._members(infos(flag_bits=0x808))), len(GOOD) + 1)  # UTF-8 名字、数据描述符：照常
        for kind in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, 93, 99):
            with self.subTest(kind=kind), self.assertRaises(update.UpdateError) as caught:
                update._members(infos(compress_type=kind))
            self.assertIn("压缩方式", str(caught.exception))

    def test_corrupt_zip(self):
        # 不是压缩包；CRC 对不上（改掉存储的内容里的一个字节）：抛 UpdateError，new.part 删掉。给用户看的是固定的一句
        # （ZIP_CORRUPT，界面原样显示），库的报错原文（类型和说明）只进错误记录
        errors = capture_errors(self)
        self.zip.write_bytes(b"PK\x03\x04 not a zip" * 100)
        with self.assertRaises(update.UpdateError) as caught:
            update.prepare(self.zip, self.release, self.root)
        self.assertEqual(str(caught.exception), update.ZIP_CORRUPT)
        self.assert_failed_clean()
        marker = b"UNIQUE-CONTENT-" * 50
        make_zip(self.zip, GOOD + [("AstralChipCompass/app/z.bin", marker)], zipfile.ZIP_STORED)
        data = bytearray(self.zip.read_bytes())
        at = data.index(marker)
        data[at + 10] ^= 0xFF
        self.zip.write_bytes(bytes(data))
        with self.assertRaises(update.UpdateError) as caught:
            update.prepare(self.zip, self.release, self.root)
        self.assertEqual(str(caught.exception), update.ZIP_CORRUPT)
        self.assert_failed_clean()
        self.assertEqual([title for title, _ in errors.entries()], [update.ZIP_CORRUPT_TITLE] * 2)
        not_zip, bad_crc = (detail for _, detail in errors.entries())
        self.assertTrue(not_zip.startswith("ACC-1.0.1.zip\nBadZipFile: "), not_zip)
        self.assertTrue(bad_crc.startswith("ACC-1.0.1.zip\nBadZipFile: Bad CRC-32"), bad_crc)
        errors.clear()
        with self.assertRaises(update.UpdateError):  # 读不了（不在）：写明出错的类型和错误代码，不记错误记录
            update.prepare(self.folder / "not-there.zip", self.release, self.root)
        self.assert_failed_clean()
        self.assertEqual(errors.entries(), [])

    def test_bad_release_version(self):
        # 版本号认不出（不是「数字.数字.数字」）：什么都不动，之前备好的照旧
        make_zip(self.zip, GOOD)
        (self.folder / update.READY_FILE).write_text('{"format": 1, "version": "1.0.0"}', encoding="utf-8")
        before = tree(self.root)
        for version in ("../1.0.1", "1.0", "", None):
            with self.subTest(version=version), self.assertRaises(update.UpdateError):
                update.prepare(self.zip, dataclasses.replace(self.release, version=version), self.root)
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.asked, [])

    def test_zip_outside_update_kept(self):
        elsewhere = make_zip(sandbox.temp_dir(self, "update-zip-") / "ACC-1.0.1.zip", GOOD)
        update.prepare(elsewhere, self.release, self.root)
        self.assertTrue(elsewhere.exists())
        self.assertTrue((self.folder / update.READY_FILE).exists())


# ====================================================================== 开始下载之前


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.root = sandbox.temp_dir(self, "update-preflight-")
        self.folder = self.root / update.UPDATE_DIR

    def free(self, value):
        return mock.patch.object(update, "free_space", return_value=value)

    def test_ok(self):
        with self.free(10 << 30):
            self.assertIsNone(update.preflight(self.root))
        self.assertTrue(self.folder.is_dir())  # 试写时建好了
        self.assertEqual(list(self.folder.iterdir()), [])  # 试写的临时文件删了
        with self.free(None):  # 查不了空间时不拦
            self.assertIsNone(update.preflight(self.root))

    def test_unwritable(self):
        # 试写 <root>\update 写不进：这里 update 被一个同名的文件占着；或者建临时文件时被拒绝
        self.folder.write_bytes(b"")
        with self.free(10 << 30):
            problem = update.preflight(self.root)
        self.assertIn("程序文件夹写不进（FileExistsError", problem)
        self.folder.unlink()
        with self.free(10 << 30), mock.patch("tempfile.mkstemp", side_effect=PermissionError(13, "denied")):
            problem = update.preflight(self.root)
        self.assertIn("程序文件夹写不进（PermissionError [Errno 13]）", problem)

    def test_low_space(self):
        with self.free(100 << 20):
            problem = update.preflight(self.root)
        self.assertIn("剩余空间不到 800 MB（还剩 100 MB）", problem)
        self.assertFalse(self.folder.exists())  # 这次才建的空文件夹删掉
        self.folder.mkdir()
        (self.folder / "ACC-1.0.1.zip.part").write_bytes(b"x")
        with self.free(update.FREE_SPACE - 1):
            self.assertIsNotNone(update.preflight(self.root))
        self.assertTrue(self.folder.is_dir())  # 本来就有的不删
        with self.free(update.FREE_SPACE):
            self.assertIsNone(update.preflight(self.root))

    def test_free_space_real(self):
        self.assertGreater(update.free_space(self.root), 0)
        self.assertIsNone(update.free_space(self.root / "not-there" / "x"))


# ====================================================================== exe 的版本号、导入


class ExeVersionTest(unittest.TestCase):
    def test_python_exe(self):
        # 本机 Python 的 python.exe 带版本资源：前两段是 Python 的大、小版本号
        found = update.exe_version(Path(sys.base_prefix) / "python.exe")
        self.assertIsNotNone(found)
        self.assertEqual(len(found), 4)
        self.assertEqual(found[:2], tuple(sys.version_info[:2]))

    def test_no_version(self):
        folder = sandbox.temp_dir(self, "update-version-")
        (folder / "plain.exe").write_bytes(b"MZ not a program")
        self.assertIsNone(update.exe_version(folder / "plain.exe"))
        self.assertIsNone(update.exe_version(folder / "missing.exe"))

    def test_loaded_from_system32_only(self):
        # version.dll 只从 System32 加载（LOAD_LIBRARY_SEARCH_SYSTEM32），同 winhttp.dll
        tree_ = ast.parse(Path(update.__file__).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree_) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "WinDLL"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.literal_eval(calls[0].args[0]), "version")
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in calls[0].keywords}
        self.assertEqual(kwargs.get("winmode"), 0x00000800)


class ImportTest(unittest.TestCase):
    def test_no_heavy_imports(self):
        # 导入本模块不带进界面库、识别用的库（换版本、检查更新都在它们之外）
        code = ("import sys, advisor.update; "
                "print([m for m in ('PySide6', 'numpy', 'cv2', 'onnxruntime') if m in sys.modules])")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ACC_EXCLUDED")}
        proc = subprocess.run([sys.executable, "-B", "-c", code], cwd=sandbox.ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip(), "[]")

    def test_does_not_look_at_is_frozen(self):
        # 给了参数就干活：开发版里不查、不下、不换由调用的一方先看 paths.is_frozen
        names = {node.attr for node in ast.walk(ast.parse(Path(update.__file__).read_text(encoding="utf-8")))
                 if isinstance(node, ast.Attribute)}
        self.assertNotIn("is_frozen", names)

    def test_public_names(self):
        for name in ("MIRROR_BASE", "MIRROR_SOURCE", "LATEST_JSON", "LATEST_ZIP", "GITHUB_API", "USE_MIRROR", "UpdateError",
                     "Release", "CheckResult", "is_newer", "sources", "check", "preflight", "download", "prepare", "obtain"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(update, name))
        self.assertEqual(update.MIRROR_BASE, "https://dl.astral-chip-compass.com/")
        self.assertEqual(update.LATEST_JSON, "https://dl.astral-chip-compass.com/latest.json")
        self.assertEqual(update.LATEST_ZIP, "https://dl.astral-chip-compass.com/ACC-latest.zip")
        # 官方版：源代码地址就是下载镜像上那个仓库，两边都问；GitHub 的接口由源代码地址推出
        self.assertEqual(update.MIRROR_SOURCE, "https://github.com/s1nan-dev/astral-chip-compass")
        self.assertIs(update.USE_MIRROR, True)
        self.assertEqual(update.GITHUB_API, "https://api.github.com/repos/s1nan-dev/astral-chip-compass/releases/latest")
        self.assertEqual(list(update.Release.__dataclass_fields__),
                         ["version", "sha256", "size", "urls", "page", "notes", "data_date", "auto",
                          "files_sha256", "files_size", "files_urls"])
        # 清单的三项带默认值：只写前八项（不带清单）照样能建，只能下整包
        release = update.Release("1.0.1", SHA, SIZE, ("https://dl.astral-chip-compass.com/releases/ACC-1.0.1.zip",),
                                 "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1", None, None, True)
        self.assertEqual((release.files_sha256, release.files_size, release.files_urls), (None, None, ()))
        self.assertEqual(list(update.CheckResult.__dataclass_fields__)[:5],
                         ["release", "latest", "reachable", "mirror_ok", "problem"])
        self.assertEqual((update.ZIP_ROOT, update.APP_EXE, update.LAUNCHER_EXE),
                         ("AstralChipCompass", ("app", "ACC.exe"), "拾星助手.exe"))
        # 清单那一边（advisor/update_files.py）认的版本号和这里同一个写法
        self.assertEqual(update.update_files._VERSION_RE.pattern, update._VERSION.pattern)


if __name__ == "__main__":
    unittest.main()
