"""下载并备好（advisor/update.py 的 obtain）：有文件清单时先只下变了的文件（对照本机，没变的复制、变了的按字节段从整包里取），
走不通改下整包；phase 的先后、进度、停止、出错的规矩，做完以后和 download + prepare 一样（update\\new、最后写 ready.json）。

不联网：127.0.0.1 上自己起的假服务器（标准库的 http.server 不认 Range，这里自己处理「bytes=起-止」），用真的 WinHTTP 会话
去取（不经代理）。服务器能按路径故意不认 Range（一律回 200）、Content-Range 写错、回得比要的长、按段取时给改过的包、跳转。
造的发行包照发行版的样子：顶层 AstralChipCompass/，时间戳写死，名字有中文，有 deflate 也有存储方式，带一个空目录和一个
空文件；清单由 update_files.build_manifest 生成。「本机」是临时文件夹里按旧版本放的文件（另有新版本里没有的旧文件和用户
数据）。exe_version 换成假的（造的 ACC.exe 不是真的程序）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.server
import io
import json
import os
import random
import re
import shutil
import struct
import threading
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from advisor import paths, update, update_files, update_log, winhttp
from tests.support import sandbox
from tests.support.errors import capture_errors

VERSION = "1.0.1"
TOP = "AstralChipCompass/"
STAMP = (1980, 1, 1, 0, 0, 0)
PAGE = f"https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v{VERSION}"
ZIP = f"ACC-{VERSION}.zip"
FILES = f"ACC-{VERSION}.files.json"
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


# 新版本：大头（运行库、界面库、模型）没变，主程序和一份资料变了，另有一个新文件
NEW = {
    "拾星助手.exe": rand(1, 30_000),
    "使用说明.html": text(2, 20_000),
    "app/ACC.exe": rand(3, 60_000),
    "app/python314.dll": rand(4, 400_000),
    "app/qt/界面库.dll": rand(5, 300_000),
    "app/models/det.onnx": rand(6, 200_000),
    "app/data/角色.json": text(7, 20_000),
    "app/data/新文件.json": text(8, 5_000),
    "app/empty.txt": b"",
}
STORED = ("app/models/det.onnx",)
DIRS = ("app/空目录",)
# 本机的旧版本：主程序变了（大小也变了），一份资料大小一样、内容变了，新文件本机没有；另有新版本里没有的旧文件和用户数据
OLD = {**{name: data for name, data in NEW.items() if name != "app/data/新文件.json"},
       "app/ACC.exe": rand(13, 61_000),
       "app/data/角色.json": text(14, 20_000),
       "app/old.dll": rand(15, 1_000),
       "app/logs/today.log": b"log\n"}
CHANGED = ("app/ACC.exe", "app/data/角色.json", "app/data/新文件.json")


def write_zip(files: dict[str, bytes]) -> bytes:
    """照发行包的样子造压缩包（顶层文件夹、时间戳写死、按名字排序），返回它的字节。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in DIRS:
            info = zipfile.ZipInfo(TOP + name + "/", date_time=STAMP)
            info.CRC = 0
            info.external_attr = (0o40755 << 16) | 0x10  # 目录（同 ZipFile.mkdir 用名字建的）
            zf.mkdir(info)
        for name in sorted(files):
            info = zipfile.ZipInfo(TOP + name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_STORED if name in STORED else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name])
    return buf.getvalue()


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
    out: dict[str, bytes | None] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            rel = info.filename[len(TOP):].rstrip("/")
            parts = rel.split("/")
            for depth in range(1, len(parts)):
                out["/".join(parts[:depth])] = None
            out[rel] = None if info.is_dir() else zf.read(info)
    return out


def compressed(blob: bytes, names) -> int:
    """这几个文件在整包里的压缩字节数（只下变了的文件时要取的总数）。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return sum(zf.getinfo(TOP + name).compress_size for name in names)


def tampered(blob: bytes, name: str) -> bytes:
    """改掉 name 的压缩数据中间的一个字节（造按段取时拿到的不对的包）。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        info = zf.getinfo(TOP + name)
    name_len, extra_len = struct.unpack_from("<2H", blob, info.header_offset + 26)
    at = info.header_offset + 30 + name_len + extra_len + info.compress_size // 2
    out = bytearray(blob)
    out[at] ^= 0xFF
    return bytes(out)


def no_proxy(case: unittest.TestCase) -> None:
    """本机测试不经过代理（同 tests/test_update.py）：清掉环境变量里的代理，getproxies 也当没有。"""
    for name in PROXY_VARS:
        if name in os.environ:
            case.addCleanup(os.environ.__setitem__, name, os.environ[name])
            del os.environ[name]
    patcher = mock.patch.object(winhttp.urllib.request, "getproxies", return_value={})
    patcher.start()
    case.addCleanup(patcher.stop)


class Handler(http.server.BaseHTTPRequestHandler):
    """本机的假服务器（见 Server）：带「Range: bytes=起-止」的请求回 206 和 Content-Range，不带的回 200 和整个文件。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端认出回话不对、不往下读就断开
            pass

    def do_GET(self):
        owner = self.server.owner
        asked = self.headers.get("Range")
        owner.seen.append((self.path, asked))
        if self.path in owner.redirects:
            self._reply(302, b"", {"Location": owner.redirects[self.path]})
            return
        body = owner.files.get(self.path)
        if body is None:
            self._reply(404, b"")
            return
        mode = owner.modes.get(self.path, "normal")
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", asked or "")
        if match is None or mode == "full":
            self._reply(200, body)
            return
        source = owner.ranged.get(self.path, body)
        start, end = int(match[1]), int(match[2])
        if start > end or end >= len(source):
            self._reply(416, b"", {"Content-Range": f"bytes */{len(source)}"})
            return
        part = source[start:end + 1]
        shown = (start + 1, end + 1) if mode == "badrange" else (start, end)
        if mode == "long":
            part += b"\0" * 100
        self._reply(206, part, {"Content-Range": f"bytes {shown[0]}-{shown[1]}/{len(source)}"})

    def _reply(self, code: int, body: bytes, headers: dict | None = None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # 一回一个请求，回完就关
        self.end_headers()
        self.wfile.write(body)


class Server:
    """127.0.0.1 上的假服务器。files 按路径给内容；redirects 按路径给跳转到的地址（302）；modes 按路径定按段取的回法：normal
    认 Range，full 不认 Range（一律回 200 和整个文件），badrange 回 206、Content-Range 的起止各错一位，long 回 206、起止写得
    对，正文却多 100 字节；ranged 按路径另给按段取时用的内容（改过的包；整个下载照样拿到对的）。seen 记下每个请求的（路径,
    Range 头）。"""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.modes: dict[str, str] = {}
        self.ranged: dict[str, bytes] = {}
        self.seen: list[tuple[str, str | None]] = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ObtainCase(unittest.TestCase):
    """本机按旧版本放好；服务器上两个「地址」（/m 像下载镜像、/g 像 GitHub）都有整包和清单；release 带清单。"""

    def setUp(self):
        no_proxy(self)
        self.errors = capture_errors(self)
        self.work = sandbox.temp_dir(self, "update-obtain-")
        self.root = self.work / "AstralChipCompass"
        self.root.mkdir()
        make_files(self.root, OLD)
        self.folder = self.root / update.UPDATE_DIR
        self.blob = write_zip(NEW)
        zip_path = self.work / ZIP
        zip_path.write_bytes(self.blob)
        self.manifest = update_files.build_manifest(zip_path, VERSION)
        self.server = Server()
        self.addCleanup(self.server.close)
        for side in ("/m", "/g"):
            self.server.files[f"{side}/{ZIP}"] = self.blob
            self.server.files[f"{side}/{FILES}"] = self.manifest
        self.release = update.Release(
            VERSION, hashlib.sha256(self.blob).hexdigest(), len(self.blob),
            (self.server.url(f"/m/{ZIP}"), self.server.url(f"/g/{ZIP}")), PAGE, None, None, True,
            files_sha256=hashlib.sha256(self.manifest).hexdigest(), files_size=len(self.manifest),
            files_urls=(self.server.url(f"/m/{FILES}"), self.server.url(f"/g/{FILES}")))
        self.version = (1, 0, 1, 0)  # 假的 exe_version 交回的
        patcher = mock.patch.object(update, "exe_version", side_effect=lambda path: self.version)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.phases: list[tuple[str, int | None]] = []
        self.progress: list[tuple[int, int]] = []
        self.outside = self.snapshot()

    def obtain(self, release: update.Release | None = None, **kwargs) -> None:
        kwargs.setdefault("phase", lambda kind, total: self.phases.append((kind, total)))
        kwargs.setdefault("progress", lambda done, total: self.progress.append((done, total)))
        update.obtain(self.release if release is None else release, self.root, **kwargs)

    def snapshot(self) -> dict:
        """update 以外的东西。"""
        return {k: v for k, v in tree(self.root).items() if not k.startswith(update.UPDATE_DIR)}

    def left(self) -> list[str]:
        return sorted(p.name for p in self.folder.iterdir()) if self.folder.exists() else []

    def zip_requests(self) -> list[tuple[str, str | None]]:
        return [(path, asked) for path, asked in self.server.seen if path.endswith(".zip")]

    def full_downloads(self) -> list[str]:
        """整个下载的压缩包（请求里没有 Range 的）。"""
        return [path for path, asked in self.zip_requests() if asked is None]

    def titles(self) -> list[str]:
        return [title for title, _ in self.errors.entries()]

    def assert_ready(self) -> None:
        """备好了：update\\new 和整包解开的一字不差，ready.json 写对，update 里只有这两样，update 以外的没被碰。"""
        self.assertEqual(tree(self.folder / update.NEW_DIR), unpacked(self.blob))
        ready = json.loads((self.folder / update.READY_FILE).read_text(encoding="utf-8"))
        self.assertEqual((ready["format"], ready["version"], ready["sha256"]), (1, VERSION, self.release.sha256))
        self.assertEqual(self.left(), sorted([update.NEW_DIR, update.READY_FILE]))
        self.assertEqual(self.snapshot(), self.outside)

    def assert_counted(self, total: int, calls: list[tuple[int, int]]) -> None:
        """一段进度：从 (0, 总数) 开始，只增不减，总数不变，最后等于总数。"""
        self.assertEqual(calls[0], (0, total))
        self.assertEqual(calls[-1], (total, total))
        done = [d for d, _ in calls]
        self.assertEqual(done, sorted(done))
        self.assertEqual({t for _, t in calls}, {total})

    def assert_fell_back(self, *contains: str) -> str:
        """改下整包了：写了一条错误记录（标题是 DELTA_FAILED），原因里有 contains 这几段；返回原因。"""
        self.assertEqual(self.titles(), [update.DELTA_FAILED])
        detail = self.errors.entries()[0][1]
        for part in contains:
            self.assertIn(part, detail)
        return detail


class DeltaTest(ObtainCase):
    """走通差分：只取变了的，其余从本机复制，不下整包。"""

    def test_copies_and_fetches(self):
        self.obtain()
        self.assert_ready()
        need = compressed(self.blob, CHANGED)
        self.assertEqual(self.phases, [("compare", None), ("delta", need), ("prepare", None)])
        self.assert_counted(need, self.progress)
        # 清单只下了第一个地址的；压缩包只按段取第一个地址的，没有整个下载
        self.assertEqual([path for path, _ in self.server.seen if path.endswith(".json")], [f"/m/{FILES}"])
        zips = self.zip_requests()
        self.assertTrue(zips)
        self.assertEqual({path for path, _ in zips}, {f"/m/{ZIP}"})
        self.assertEqual(self.full_downloads(), [])
        # 取的字节：变了的那几个的压缩数据，加上中央目录和每个文件的本地文件头，多不了几 KB
        fetched = 0
        for _, asked in zips:
            start, end = (int(n) for n in asked.removeprefix("bytes=").split("-"))
            fetched += end - start + 1
        self.assertGreaterEqual(fetched, need)
        self.assertLess(fetched, need + 16 * 1024)
        self.assertEqual(self.errors.entries(), [])

    def test_everything_local(self):
        # 本机的文件全都用得上：total 为 0，压缩包一个字节都不取
        make_files(self.root, NEW)
        self.outside = self.snapshot()
        self.obtain()
        self.assert_ready()
        self.assertEqual(self.phases, [("compare", None), ("delta", 0), ("prepare", None)])
        self.assertEqual(self.progress, [(0, 0)])
        self.assertEqual(self.zip_requests(), [])
        self.assertEqual(self.errors.entries(), [])

    def test_flushed_before_ready(self):
        # 拼好的文件（复制的、取的都算）逐个刷进硬盘以后才改名、写 ready.json（同整包那条路解压时每个文件写完刷盘）：
        # 断电以后不会有 ready.json 在、文件却没写进硬盘
        flushed = []
        real = update._sync

        def sync(f):
            self.assertFalse((self.folder / update.READY_FILE).exists())
            self.assertFalse((self.folder / update.NEW_DIR).exists())
            flushed.append(Path(f.name).relative_to(self.folder / update.NEW_PART).as_posix())
            real(f)

        with mock.patch.object(update, "_sync", side_effect=sync):
            self.obtain()
        self.assert_ready()
        self.assertEqual(sorted(flushed), sorted(NEW))

    def test_first_address_without_range(self):
        # 第一个地址不认 Range（回 200）：换第二个，之后都用它；不改下整包，不写错误记录
        self.server.modes[f"/m/{ZIP}"] = "full"
        self.obtain()
        self.assert_ready()
        self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta", "prepare"])
        zips = self.zip_requests()
        self.assertEqual(zips[0][0], f"/m/{ZIP}")
        self.assertEqual({path for path, _ in zips[1:]}, {f"/g/{ZIP}"})
        self.assertEqual(self.full_downloads(), [])
        self.assertEqual(self.errors.entries(), [])

    def test_first_manifest_bad(self):
        # 第一个地址的清单下不到、对不上：换第二个
        for name, change in (("下不到", lambda: self.server.files.pop(f"/m/{FILES}")),
                             ("改过", lambda: self.server.files.update({f"/m/{FILES}": self.manifest[:-1] + b" "}))):
            with self.subTest(name):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.server.seen.clear()
                self.phases.clear()
                change()
                self.obtain()
                self.assert_ready()
                self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta", "prepare"])
                self.assertEqual([path for path, _ in self.server.seen if path.endswith(".json")],
                                 [f"/m/{FILES}", f"/g/{FILES}"])
                self.assertEqual(self.errors.entries(), [])

    def test_progress_while_fetching(self):
        # 大文件分几段取（这里把一段改小），取的中途也按收到的字节报进度：只增不减，不超过总数，最后等于总数
        with mock.patch.object(update_files, "SEGMENT", 16 * 1024), mock.patch.object(update, "PROGRESS_STEP", 4096):
            self.obtain()
        self.assert_ready()
        need = compressed(self.blob, CHANGED)
        self.assert_counted(need, self.progress)
        self.assertGreaterEqual(len(self.progress), 1 + 4 + 2)  # 开头一次，主程序 60 KB 分四段，另两个文件
        # 中央目录一次，主程序四次，另两个文件各一次
        self.assertEqual(len(self.zip_requests()), 1 + 4 + 2)
        self.assertEqual(self.errors.entries(), [])

    def test_redirected_address(self):
        # 压缩包的地址跳转（发布页附件的地址会跳到文件服务器）：跳过去以后 Range 头还在，照样只取变了的
        self.server.redirects[f"/r/{ZIP}"] = f"/m/{ZIP}"
        release = dataclasses.replace(self.release, urls=(self.server.url(f"/r/{ZIP}"),))
        self.obtain(release, progress=None, phase=None)
        self.assert_ready()
        zips = self.zip_requests()
        self.assertEqual({path for path, _ in zips}, {f"/r/{ZIP}", f"/m/{ZIP}"})
        self.assertTrue(all(asked is not None for _, asked in zips))
        self.assertEqual([asked for path, asked in zips if path == f"/r/{ZIP}"],
                         [asked for path, asked in zips if path == f"/m/{ZIP}"])
        self.assertEqual(self.errors.entries(), [])

    def test_replaces_previous(self):
        # 之前备好的版本（旧的 new、ready.json）和上次没做完的 new.part：换成这一版
        (self.folder / update.NEW_DIR / "app").mkdir(parents=True)
        (self.folder / update.NEW_DIR / "app" / "stale.dll").write_bytes(b"stale")
        (self.folder / update.READY_FILE).write_text('{"format": 1, "version": "1.0.0"}', encoding="utf-8")
        (self.folder / update.NEW_PART).mkdir()
        (self.folder / update.NEW_PART / "junk").write_bytes(b"junk")
        self.obtain()
        self.assert_ready()
        self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta", "prepare"])

    def test_own_session_closed(self):
        # 不给会话时自己建一个（差分、整包两条路共用），用完关掉；建不了时抛 UpdateError
        closed = []

        class Tracking(winhttp.Session):
            def close(self):
                closed.append(True)
                super().close()

        with mock.patch.object(update.winhttp, "Session", Tracking):
            self.obtain()
        self.assert_ready()
        self.assertEqual(closed, [True])
        with mock.patch.object(update.winhttp, "Session", side_effect=urllib.error.URLError("WinHTTP 错误 87")), \
                self.assertRaises(update.UpdateError) as caught:
            self.obtain()
        self.assertIn("建不了联网会话", str(caught.exception))


class FallbackTest(ObtainCase):
    """差分走不通：删掉 new.part，写一条错误记录，改下整包；界面先后收到 "delta"（走到了的话）和 "full"。"""

    def assert_full_after(self, *before: str) -> None:
        """phase 是 before 这几步以后 ("full", 整包大小)、("prepare", None)；"full" 以后的进度从 0 重新数。"""
        size = len(self.blob)
        self.assertEqual([kind for kind, _ in self.phases], [*before, "full", "prepare"])
        self.assertEqual(self.phases[-2:], [("full", size), ("prepare", None)])
        start = self.progress.index((0, size))
        self.assert_counted(size, self.progress[start:])
        self.assertEqual(self.full_downloads(), [f"/m/{ZIP}"])

    def test_range_not_supported(self):
        # 两个地址都不认 Range（回 200）：读中央目录那一次就走不通，还没到 "delta"
        for side in ("/m", "/g"):
            self.server.modes[f"{side}/{ZIP}"] = "full"
        self.obtain()
        self.assert_ready()
        self.assert_full_after("compare")
        self.assert_fell_back("不是 206", "127.0.0.1")

    def test_content_range_wrong(self):
        for side in ("/m", "/g"):
            self.server.modes[f"{side}/{ZIP}"] = "badrange"
        self.obtain()
        self.assert_ready()
        self.assert_full_after("compare")
        self.assert_fell_back("Content-Range 对不上")

    def test_reply_longer_than_asked(self):
        for side in ("/m", "/g"):
            self.server.modes[f"{side}/{ZIP}"] = "long"
        self.obtain()
        self.assert_ready()
        self.assert_full_after("compare")
        self.assert_fell_back("比要的")

    def test_fetched_member_wrong(self):
        # 按段取到的主程序和清单对不上（两个地址都是）：走到一半改下整包，"delta" 之后收到 "full"
        bad = tampered(self.blob, "app/ACC.exe")
        for side in ("/m", "/g"):
            self.server.ranged[f"{side}/{ZIP}"] = bad
        self.obtain()
        self.assert_ready()
        self.assert_full_after("compare", "delta")
        self.assert_fell_back("app/ACC.exe")
        self.assertFalse((self.folder / update.NEW_PART).exists())

    def test_manifest_unavailable(self):
        # 清单两个地址都下不到、都对不上：没碰压缩包就改下整包
        for name, change in (("404", lambda side: self.server.files.pop(f"{side}/{FILES}")),
                             ("对不上", lambda side: self.server.files.update({f"{side}/{FILES}": b"{}" * 10}))):
            with self.subTest(name):
                shutil.rmtree(self.folder, ignore_errors=True)
                self.server.seen.clear()
                self.phases.clear()
                self.progress.clear()
                self.errors.clear()
                for side in ("/m", "/g"):
                    change(side)
                self.obtain()
                self.assert_ready()
                self.assert_full_after("compare")
                self.assertEqual(self.zip_requests(), [(f"/m/{ZIP}", None)])
                self.assert_fell_back("文件清单下不到", "HTTP 404" if name == "404" else "大小不对")

    def test_not_worth_it(self):
        # 本机一个文件都用不上：要取的超过整包的六成，不值得，直接下整包（只读了一次中央目录）
        for item in self.root.iterdir():
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        self.outside = self.snapshot()
        self.obtain()
        self.assert_ready()
        self.assert_full_after("compare")
        self.assertEqual(len([asked for _, asked in self.zip_requests() if asked is not None]), 1)
        self.assert_fell_back("不如下整包")

    def test_final_check_fails(self):
        # 拼好以后核对主程序的版本号不对：也改下整包；整包同样不对时照旧抛 UpdateError（给用户看的原因）
        self.version = (1, 0, 0, 0)
        with self.assertRaises(update.UpdateError) as caught:
            self.obtain()
        self.assertIn("应为 1.0.1", str(caught.exception))
        self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta", "prepare", "full", "prepare"])
        self.assert_fell_back("应为 1.0.1")
        self.assertEqual(self.left(), [ZIP])  # 没备好：没有 new、new.part、ready.json，下好的整包留着
        self.assertEqual(self.snapshot(), self.outside)

    def test_log_without_user_folder(self):
        # 错误记录里的原因去掉用户目录（paths.scrub_paths），也不交给界面：obtain 照常下完整包，不往外抛
        local = self.root / "app" / "ACC.exe"
        error = update_files.DeltaError(f"读不了本机的 app/ACC.exe：[Errno 13] Permission denied: '{local}'")
        with mock.patch.dict(os.environ, {"USERPROFILE": str(self.work)}), \
                mock.patch.object(update.update_files, "assemble", side_effect=error):
            self.obtain()
        self.assert_ready()
        detail = self.assert_fell_back("读不了本机的 app/ACC.exe", "%USERPROFILE%")
        self.assertNotIn(str(self.work), detail)

    def test_unexpected_error(self):
        # 想不到的错也只算这条路走不通：改下整包，错误记录里写出错的类型和位置
        with mock.patch.object(update_files, "parse_manifest", side_effect=ValueError("boom")):
            self.obtain()
        self.assert_ready()
        self.assert_full_after("compare")
        self.assert_fell_back("ValueError: boom", "Traceback")


class NoManifestTest(ObtainCase):
    def test_full_only(self):
        # 没有清单：直接下整包，不下清单，不写错误记录
        release = dataclasses.replace(self.release, files_sha256=None, files_size=None, files_urls=())
        self.obtain(release)
        self.assert_ready()
        self.assertEqual(self.phases, [("full", len(self.blob)), ("prepare", None)])
        self.assert_counted(len(self.blob), self.progress)
        self.assertEqual([path for path, _ in self.server.seen], [f"/m/{ZIP}"])
        self.assertEqual(self.errors.entries(), [])

    def test_full_path_errors(self):
        # 整包这条路的错照旧抛 UpdateError；版本号不对的什么都不做
        release = dataclasses.replace(self.release, files_sha256=None, files_size=None, files_urls=(),
                                      urls=(self.server.url("/missing.zip"),))
        with self.assertRaises(update.UpdateError) as caught:
            self.obtain(release)
        self.assertIn("HTTP 404", str(caught.exception))
        self.assertEqual(self.phases, [("full", len(self.blob))])
        self.server.seen.clear()
        with self.assertRaises(update.UpdateError):
            self.obtain(dataclasses.replace(self.release, version="../1.0.1"))
        self.assertEqual(self.server.seen, [])


class LogTest(ObtainCase):
    """obtain 往 update.log 记的几行：差分成功、差分退回整包、没有清单只下整包、镜像换 GitHub、核对失败、写记录失败；不留地址和
    用户目录。"""

    def setUp(self):
        super().setUp()
        self.log = paths.logs_dir() / update_log.KEPT_LOG
        self.log.unlink(missing_ok=True)
        self.addCleanup(self.log.unlink, missing_ok=True)

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def assert_no_addresses(self) -> None:
        text = self.text()
        self.assertNotIn("127.0.0.1", text)
        self.assertNotRegex(text, r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")

    def test_delta(self):
        self.obtain()
        self.assert_ready()
        text = self.text()
        for part in ("1.0.1：有文件清单，先试只下变了的文件", "只下变了的文件，来源 其他地址", "清单", "要取", "实际取",
                     "已备好（差分"):
            self.assertIn(part, text)
        self.assert_no_addresses()

    def test_mirror_to_github(self):
        # 第一个地址（像下载镜像）不支持按段取：记一笔换下一个地址，用第二个取完
        self.server.modes[f"/m/{ZIP}"] = "full"
        self.obtain()
        self.assert_ready()
        text = self.text()
        self.assertIn("按段取没成", text)
        self.assertIn("换下一个地址", text)
        self.assertIn("来源 其他地址", text)
        self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta", "prepare"])
        self.assert_no_addresses()

    def test_fallback_full_and_new_source(self):
        for side in ("/m", "/g"):
            self.server.modes[f"{side}/{ZIP}"] = "full"
        self.obtain()
        self.assert_ready()
        text = self.text()
        for part in ("按段取没成", "不是 206", "只下变了的文件没成", f"改下整包（{len(self.blob)} 字节）", "整包下载好",
                     "已备好（整包"):
            self.assertIn(part, text)
        self.assert_no_addresses()

    def test_no_manifest(self):
        # 没有清单：记下这次下整包、要下多少字节
        release = dataclasses.replace(self.release, files_sha256=None, files_size=None, files_urls=())
        self.obtain(release)
        self.assert_ready()
        text = self.text()
        self.assertIn(f"1.0.1：没有文件清单，下整包（{len(self.blob)} 字节）", text)
        self.assertIn("已备好（整包", text)
        self.assert_no_addresses()

    def test_prepare_failure(self):
        # 程序文件的版本号是四段数字（写法和 IP 一样）：照写，看得出核对出来的是哪一版
        self.version = (1, 0, 0, 0)
        with self.assertRaises(update.UpdateError):
            self.obtain()
        text = self.text()
        for part in ("只下变了的文件没成", "改下整包", "核对、解压失败", "版本号是 1.0.0.0，应为 1.0.1"):
            self.assertIn(part, text)

    def test_no_user_folder(self):
        # 程序文件夹在用户目录里（例如解压在桌面上）：本机文件的路径写成相对程序目录的，不出现用户目录
        local = self.root / "app" / "ACC.exe"
        error = update_files.DeltaError(f"读不了本机的 app/ACC.exe：[Errno 13] Permission denied: '{local}'")
        with mock.patch.dict(os.environ, {"USERPROFILE": str(self.work)}), \
                mock.patch.object(update_log.paths, "program_folder", return_value=self.root), \
                mock.patch.object(update.update_files, "assemble", side_effect=error):
            self.obtain()
        self.assert_ready()
        text = self.text()
        self.assertNotIn(str(self.work), text)
        self.assertIn("Permission denied: '%程序目录%\\app\\ACC.exe'", text)

    def test_write_failure_does_not_break_obtain(self):
        blocker = sandbox.temp_dir(self, "update-obtain-blocked-") / "logs"
        blocker.write_bytes(b"not a folder")
        with mock.patch.object(update_log.paths, "logs_dir", return_value=blocker):
            self.obtain()
        self.assert_ready()


class StopTest(ObtainCase):
    """停止：抛 winhttp.Stopped，不留下 new.part、.part，不改下整包，不写错误记录。"""

    def assert_stopped_clean(self) -> None:
        self.assertFalse((self.folder / update.NEW_PART).exists())
        self.assertFalse((self.folder / update.READY_FILE).exists())
        self.assertEqual([name for name in self.left() if name.endswith(update.PART_SUFFIX)], [])
        self.assertEqual(self.full_downloads(), [])
        self.assertNotIn("full", [kind for kind, _ in self.phases])
        self.assertEqual(self.errors.entries(), [])
        self.assertEqual(self.snapshot(), self.outside)

    def test_stop_midway(self):
        stop = threading.Event()

        def progress(done, total):
            self.progress.append((done, total))
            if done:
                stop.set()

        with self.assertRaises(winhttp.Stopped):
            self.obtain(progress=progress, stop=stop)
        self.assert_stopped_clean()
        self.assertEqual([kind for kind, _ in self.phases], ["compare", "delta"])

    def test_stop_while_comparing(self):
        stop = threading.Event()

        def phase(kind, total):
            self.phases.append((kind, total))
            if kind == "compare":
                stop.set()

        with self.assertRaises(winhttp.Stopped):
            self.obtain(phase=phase, stop=stop)
        self.assert_stopped_clean()
        self.assertEqual(self.server.seen, [])

    def test_stopped_before_start(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(winhttp.Stopped):
            self.obtain(stop=stop)
        self.assertEqual((self.phases, self.server.seen), ([], []))
        self.assertFalse(self.folder.exists())


class MeterTest(unittest.TestCase):
    """差分这条路的进度（_DeltaMeter）：assemble 第一次报进度时先报 phase "delta"；取的中途按收到的字节报，可能比 assemble
    后来报的多出本地文件头那几十个字节，交出去的只增不减、不超过总数。"""

    def test_reports(self):
        phases, calls = [], []
        meter = update._DeltaMeter(lambda done, total: calls.append((done, total)),
                                   lambda kind, total: phases.append((kind, total)))
        with mock.patch.object(update, "PROGRESS_STEP", 100):
            meter.received(5000)  # 读中央目录时：还没有总数，不报
            meter.report(0, 1000)
            meter.received(150)  # 这一次收到 150（含文件头）：报 150
            meter.received(170)  # 比上次多不到一步：不报
            meter.report(120, 1000)  # assemble 算的是压缩数据 120：比交出去的少，不报
            meter.received(400)  # 下一次请求收到 400：120 + 400
            meter.received(2000)  # 不超过总数
            meter.report(1000, 1000)
        self.assertEqual(phases, [("delta", 1000)])
        self.assertEqual(calls, [(0, 1000), (150, 1000), (520, 1000), (1000, 1000), (1000, 1000)])

    def test_nothing_to_fetch(self):
        phases, calls = [], []
        meter = update._DeltaMeter(lambda done, total: calls.append((done, total)),
                                   lambda kind, total: phases.append((kind, total)))
        meter.report(0, 0)
        self.assertEqual((phases, calls), ([("delta", 0)], [(0, 0)]))
        update._DeltaMeter(None, None).report(0, 10)  # 不给 progress、phase 也行


class InterfaceTest(unittest.TestCase):
    def test_signature(self):
        # 界面按这个接口调用：名字、参数、phase 的几种都不改
        import inspect

        params = inspect.signature(update.obtain).parameters
        self.assertEqual(list(params), ["release", "root", "progress", "stop", "session", "phase"])
        self.assertTrue(all(params[name].kind is inspect.Parameter.KEYWORD_ONLY and params[name].default is None
                            for name in ("progress", "stop", "session", "phase")))


if __name__ == "__main__":
    unittest.main()
