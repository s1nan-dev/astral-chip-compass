"""按文件更新（advisor/update_files.py）：清单的生成和核对，对照本机只取变了的文件，按字节段从整包里取出来拼成新版本。

不联网：本机 127.0.0.1 上起一个认 Range 的小 http 服务器提供造出来的发行包（标准库 http.server 不认 Range，这里自己处理：
「Range: bytes=a-b」回 206 和 Content-Range；还能故意不认 Range 一律回 200、回得比要的长）。测试用的 fetch（RangeFetch）
用 urllib 带 Range 头去取，核对回 206、Content-Range 的起止和总长，Content-Length 和要的不一样时不往下读，同时记下每次请求
的起止：算请求了几次、取了多少字节。它只是测试替身，写法就是调用方的 fetch 要满足的那几条；模块本身不联网。

造的发行包照发行版的样子：顶层是 AstralChipCompass/，时间戳写死，文件有 deflate 也有存储方式，名字有中文，带一个空目录和
一个空文件。「本机」是临时文件夹里按旧版本放的文件，dest 照实际的位置放在它的 update\\new.part。
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.server
import io
import json
import random
import re
import struct
import os
import shutil
import threading
import unittest
import urllib.request
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from advisor import update_files as uf
from advisor import winhttp
from tests.support import sandbox

VERSION = "1.0.1"
TOP = "AstralChipCompass/"
STAMP = (1980, 1, 1, 0, 0, 0)


def rand(seed: int, n: int) -> bytes:
    """压不动的数据（随机字节），同一个 seed 每次一样。"""
    return random.Random(seed).randbytes(n)


def text(seed: int, n: int) -> bytes:
    """压得动的文字（中英文混着），正好 n 字节。"""
    rnd = random.Random(seed)
    words = ("筹码", "角色", "推荐", "回合", "alpha", "beta", "gamma", "0123", "\n")
    out = bytearray()
    while len(out) < n:
        out += rnd.choice(words).encode("utf-8") + b" "
    return bytes(out[:n])


# 新版本（要拼出来的）：主程序、启动程序、运行库、模型（存储方式）、资料、说明、空文件，外加一个空目录
NEW = {
    "拾星助手.exe": rand(1, 40_000),
    "使用说明.html": text(2, 30_000),
    "app/ACC.exe": rand(3, 200_000) + text(4, 100_000),
    "app/python314.dll": rand(5, 300_000),
    "app/qt/界面库.dll": rand(6, 250_000),
    "app/models/det.onnx": rand(7, 150_000),
    "app/data/角色.json": text(8, 20_000),
    "app/data/maps.json": text(9, 8_000),
    "app/data/新文件.json": text(10, 6_000),
    "app/LICENSES/LICENSE.txt": text(11, 5_000),
    "app/empty.txt": b"",
}
STORED = ("app/models/det.onnx",)
DIRS = ("app/空目录",)
# 本机的旧版本：主程序变了（大小也变了），一份资料大小一样、内容变了，新文件本机没有；另有新版本里没有的旧文件和用户数据
OLD = {**{name: data for name, data in NEW.items() if name != "app/data/新文件.json"},
       "app/ACC.exe": rand(13, 210_000) + text(4, 100_000),
       "app/data/角色.json": text(14, 20_000),
       "app/old.dll": rand(15, 1_000),
       "app/logs/today.log": b"log\n"}
CHANGED = ("app/ACC.exe", "app/data/角色.json", "app/data/新文件.json")


class _Unseekable(io.RawIOBase):
    """不能回头改的输出流：zipfile 往里写时每个文件后面跟一个数据描述符，本地文件头里的大小、CRC 都是 0。"""

    def __init__(self):
        super().__init__()
        self.buf = io.BytesIO()

    def writable(self):
        return True

    def write(self, data):
        return self.buf.write(data)


def write_zip(path: Path, files: dict[str, bytes], *, stored=STORED, dirs=DIRS, top=TOP,
              method=zipfile.ZIP_DEFLATED, seekable=True, loose: dict[str, bytes] | None = None,
              entry_comment: bytes = b"", archive_comment: bytes = b"") -> bytes:
    """照发行包的样子写压缩包（顶层文件夹、时间戳写死、按名字排序），返回压缩包的字节。loose 是不加顶层文件夹的条目；
    entry_comment 写进每个文件在中央目录里的注释，archive_comment 是整包末尾的注释。"""
    out = path.open("wb") if seekable else _Unseekable()
    try:
        with zipfile.ZipFile(out, "w") as zf:
            zf.comment = archive_comment
            for name in dirs:
                info = zipfile.ZipInfo(top + name + "/", date_time=STAMP)
                info.CRC = 0
                info.external_attr = (0o40755 << 16) | 0x10  # 目录（同 ZipFile.mkdir 用名字建的）
                zf.mkdir(info)
            for name in sorted(files):
                info = zipfile.ZipInfo(top + name, date_time=STAMP)
                info.compress_type = zipfile.ZIP_STORED if name in stored else method
                info.external_attr = 0o644 << 16
                info.comment = entry_comment
                zf.writestr(info, files[name])
            for name, data in (loose or {}).items():
                zf.writestr(zipfile.ZipInfo(name, date_time=STAMP), data)
    finally:
        if seekable:
            out.close()
    if not seekable:
        path.write_bytes(out.buf.getvalue())
    return path.read_bytes()


def make_root(folder: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        path = folder.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def unpacked(blob: bytes) -> tuple[dict[str, bytes], set[str]]:
    """整包解压出来（AstralChipCompass/ 下面）的文件和各级目录：拼出来的要和它一字不差。"""
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            rel = info.filename[len(TOP):]
            if info.is_dir():
                if rel.rstrip("/"):
                    dirs.add(rel.rstrip("/"))
            else:
                files[rel] = zf.read(info)
    for rel in [*files, *dirs]:
        parts = rel.split("/")
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    return files, dirs


def tree(folder: Path, skip: Path | None = None) -> tuple[dict[str, bytes], set[str]]:
    """文件夹里的文件（相对路径 → 内容）和各级目录；skip 下面的不算。"""
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    for path in sorted(folder.rglob("*")):
        if skip is not None and (path == skip or skip in path.parents):
            continue
        rel = path.relative_to(folder).as_posix()
        if path.is_dir():
            dirs.add(rel)
        else:
            files[rel] = path.read_bytes()
    return files, dirs


def info_of(blob: bytes, rel: str) -> zipfile.ZipInfo:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.getinfo(TOP + rel)


def compressed(blob: bytes, names) -> int:
    """这几个文件在整包里的压缩字节数（要取的总数）。"""
    return sum(info_of(blob, name).compress_size for name in names)


def central_size(blob: bytes) -> int:
    """中央目录连同末尾的记录有多少字节。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return len(blob) - zf.start_dir


def data_span(blob: bytes, rel: str) -> tuple[int, int]:
    """rel 的压缩数据在整包里的起止（不含止）。"""
    info = info_of(blob, rel)
    name_len, extra_len = struct.unpack_from("<2H", blob, info.header_offset + 26)
    start = info.header_offset + 30 + name_len + extra_len
    return start, start + info.compress_size


def flip(blob: bytes, pos: int) -> bytes:
    """改掉一个字节。"""
    out = bytearray(blob)
    out[pos] ^= 0xFF
    return bytes(out)


def patch_size(blob: bytes, rel: str, size: int) -> bytes:
    """把 rel 在本地文件头和中央目录里写的解压后大小都改成 size（造「解压出来比写的大」的包）。"""
    out = bytearray(blob)
    info = info_of(blob, rel)
    struct.pack_into("<I", out, info.header_offset + 22, size)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        pos = zf.start_dir
    name = (TOP + rel).encode("utf-8")
    while out[pos:pos + 4] == b"PK\x01\x02":
        name_len, extra_len, comment_len = struct.unpack_from("<3H", out, pos + 28)
        if bytes(out[pos + 46:pos + 46 + name_len]) == name:
            struct.pack_into("<I", out, pos + 24, size)
            return bytes(out)
        pos += 46 + name_len + extra_len + comment_len
    raise AssertionError(f"中央目录里没有 {rel}")


def dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- 认 Range 的服务器和测试用的 fetch


class RangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端没读完就断开（测试的 fetch 发现不对时不往下读）
            pass

    def do_GET(self):
        owner = self.server.owner
        blob = owner.blob
        if self.path != "/" + owner.name:
            self._send(404, b"")
            return
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        if owner.mode == "full" or match is None:  # 不认 Range：回 200 和整个文件
            self._send(200, blob)
            return
        start, end = int(match.group(1)), int(match.group(2))
        if start > end or end >= len(blob):
            self._send(416, b"", {"Content-Range": f"bytes */{len(blob)}"})
            return
        body = blob[start:end + 1]
        if owner.mode == "long":  # 起止写得对，正文却比要的长
            body += b"\0" * 100
        self._send(206, body, {"Content-Range": f"bytes {start}-{end}/{len(blob)}"})

    def _send(self, code: int, body: bytes, headers: dict | None = None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # 一回一个请求，回完就关
        self.end_headers()
        self.wfile.write(body)


class RangeServer:
    """127.0.0.1 上只提供一个文件的服务器。mode：normal 认 Range；full 不认 Range、一律回 200 和整个文件；long 回 206、
    起止写得对，正文却多 100 字节。blob 可以随时换（造改过的包）。"""

    def __init__(self, blob: bytes, name: str = f"ACC-{VERSION}.zip"):
        self.blob = blob
        self.name = name
        self.mode = "normal"
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.httpd.owner = self
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/{name}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


class RangeError(OSError):
    """测试用的 fetch 认出服务器回的不对（不是 206、Content-Range 或长度对不上）。"""


class RangeFetch:
    """测试用的 fetch：urllib 带 Range 头去取第 start～end 字节（含 end）。check 为真时核对回 206、Content-Range 的起止和
    总长，Content-Length 和要的不一样时不往下读；为假时不管回的什么，读完整个正文交回。calls 记下每次请求的起止。"""

    def __init__(self, url: str, total: int, *, check: bool = True):
        self.url = url
        self.total = total
        self.check = check
        self.calls: list[tuple[int, int]] = []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不走代理：只连本机

    @property
    def fetched(self) -> int:
        """一共请求了多少字节。"""
        return sum(end - start + 1 for start, end in self.calls)

    def __call__(self, start: int, end: int) -> bytes:
        self.calls.append((start, end))
        want = end - start + 1
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        with self.opener.open(request, timeout=10) as resp:
            if not self.check:
                return resp.read()
            if resp.status != 206:
                raise RangeError(f"服务器回了 {resp.status}，不是 206：不支持按段取")
            if resp.headers.get("Content-Range") != f"bytes {start}-{end}/{self.total}":
                raise RangeError(f"Content-Range 对不上：{resp.headers.get('Content-Range')}")
            if resp.headers.get("Content-Length") != str(want):
                raise RangeError(f"回话有 {resp.headers.get('Content-Length')} 字节，要的是 {want}：不往下读")
            data = resp.read(want)
        if len(data) != want:
            raise RangeError(f"只读到 {len(data)} 字节，要的是 {want}")
        return data


class Release:
    """一个造出来的发行包：压缩包、清单（build_manifest 生成、parse_manifest 读回）、提供它的服务器和测试用的 fetch。"""

    def __init__(self, case: unittest.TestCase, files: dict[str, bytes] = NEW, **zip_args):
        self.folder = sandbox.temp_dir(case)
        self.zip_path = self.folder / uf.zip_name(VERSION)
        self.blob = write_zip(self.zip_path, files, **zip_args)
        self.data = uf.build_manifest(self.zip_path, VERSION)
        self.manifest = self.parse(self.data)
        self.server = RangeServer(self.blob)
        case.addCleanup(self.server.close)
        self.fetch = RangeFetch(self.server.url, len(self.blob))

    def parse(self, data: bytes, blob: bytes | None = None) -> uf.Manifest:
        blob = self.blob if blob is None else blob
        return uf.parse_manifest(data, version=VERSION, zip_size=len(blob),
                                 zip_sha256=hashlib.sha256(blob).hexdigest())


# ---------------------------------------------------------------- 生成清单


class BuildManifestTest(unittest.TestCase):
    def setUp(self):
        self.folder = sandbox.temp_dir(self)

    def build(self, files=NEW, **zip_args) -> tuple[Path, bytes, bytes]:
        path = self.folder / uf.zip_name(VERSION)
        blob = write_zip(path, files, **zip_args)
        return path, blob, uf.build_manifest(path, VERSION)

    def test_layout(self):
        """中文名、存储和压缩两种方式、空文件、空目录都记对；写法固定（UTF-8、中文不转义、键排序、LF 结尾）。"""
        path, blob, data = self.build()
        with zipfile.ZipFile(path) as zf:
            methods = {info.compress_type for info in zf.infolist() if not info.is_dir()}
        self.assertEqual(methods, {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
        obj = json.loads(data)
        self.assertEqual((obj["format"], obj["version"], obj["zip"]), (1, VERSION, "ACC-1.0.1.zip"))
        self.assertEqual((obj["zip_size"], obj["zip_sha256"]), (len(blob), hashlib.sha256(blob).hexdigest()))
        self.assertEqual([item["path"] for item in obj["files"]], sorted(NEW))
        for item in obj["files"]:
            content = NEW[item["path"]]
            self.assertEqual((item["size"], item["sha256"]), (len(content), hashlib.sha256(content).hexdigest()))
        self.assertEqual(obj["dirs"], ["app/空目录"])
        self.assertEqual(data.decode("utf-8"), json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
        self.assertIn("拾星助手.exe".encode("utf-8"), data)
        self.assertNotIn(b"\\u", data)
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"}\n"))
        self.assertEqual(uf.manifest_name(VERSION), "ACC-1.0.1.files.json")

    def test_same_bytes_twice(self):
        """同一个包生成两次字节一样；换个文件名、换个地方也一样（清单里的 zip 按版本号写）。"""
        path, blob, data = self.build()
        self.assertEqual(uf.build_manifest(path, VERSION), data)
        other = self.folder / "copy.zip"
        other.write_bytes(blob)
        self.assertEqual(uf.build_manifest(other, VERSION), data)

    def test_only_empty_dirs(self):
        """目录项下面有文件的不记（不分大小写），只记空的。"""
        _, _, data = self.build(dirs=("app", "APP/data", "app/空目录", "app/空目录/更深"))
        self.assertEqual(json.loads(data)["dirs"], ["app/空目录", "app/空目录/更深"])

    def test_data_descriptor(self):
        """写在不能回头改的流里的包（每个文件后面跟数据描述符）照样生成。"""
        path, blob, data = self.build(seekable=False)
        with zipfile.ZipFile(path) as zf:
            self.assertTrue(all(info.flag_bits & 0x08 for info in zf.infolist() if not info.is_dir()))
        self.assertEqual(json.loads(data)["files"], json.loads(self.build()[2])["files"])

    def test_rejects(self):
        """不在顶层文件夹下面、缺主程序、bzip2 压缩、不分大小写重名、版本号写法不对：都不生成。"""
        cases = {
            "顶层不对": dict(top="Other/"),
            "顶层外面还有": dict(loose={"readme.txt": b"x"}),
            "缺主程序": dict(files={k: v for k, v in NEW.items() if k != "app/ACC.exe"}),
            "缺启动程序": dict(files={k: v for k, v in NEW.items() if k != "拾星助手.exe"}),
            "bzip2": dict(method=zipfile.ZIP_BZIP2),
            "大小写重名": dict(files={**NEW, "APP/acc.EXE": b"x"}),
        }
        for label, args in cases.items():
            with self.subTest(label), self.assertRaises(uf.DeltaError):
                self.build(**args)
        path, _, _ = self.build()
        # 版本号的写法同 advisor/update.py 认的：数字.数字.数字，每段 1～9 位 ASCII 数字
        for version in ("1.0", "v1.0.1", "1.0.1 ", "", "1.0.1.0", "1234567890.0.1", "1.0.1234567890", "１.0.1", "1..1"):
            with self.subTest(version=version), self.assertRaises(uf.DeltaError):
                uf.build_manifest(path, version)
        self.assertEqual(json.loads(uf.build_manifest(path, "123456789.0.1"))["zip"], "ACC-123456789.0.1.zip")


# ---------------------------------------------------------------- 解析、核对清单

ZIP_SIZE = 89_001_969
ZIP_SHA = "0123456789abcdef" * 4
BAD_PATHS = (
    "../x.dll", "app/../../x.dll", "app/..", "app/./x.dll", ".", "/app/x.dll", "C:/x.dll", "c:x.dll", "D:\\x.dll",
    "app\\x.dll", "app//x.dll", "app/", "", "app/x.dll:stream", "app/NUL", "app/con.txt", "app/COM1.log", "app/lpt9",
    "app/x.dll.", "app/x.dll ", "app/a\tb", "app/a\x00b", "app/a?b", "app/a*b", "app/a|b", 'app/a"b', "app/a<b",
)


def manifest_obj(**changes) -> dict:
    obj = {
        "format": 1, "version": VERSION, "zip": f"ACC-{VERSION}.zip", "zip_size": ZIP_SIZE, "zip_sha256": ZIP_SHA,
        "files": [
            {"path": "app/ACC.exe", "size": 10, "sha256": "1" * 64},
            {"path": "app/data/角色.json", "size": 0, "sha256": "2" * 64},
            {"path": "拾星助手.exe", "size": 5, "sha256": "3" * 64},
        ],
        "dirs": ["app/空目录"],
    }
    obj.update(changes)
    return obj


def parse(data: bytes, **given) -> uf.Manifest:
    args = {"version": VERSION, "zip_size": ZIP_SIZE, "zip_sha256": ZIP_SHA}
    args.update(given)
    return uf.parse_manifest(data, **args)


class ParseManifestTest(unittest.TestCase):
    def assert_rejected(self, data: bytes, **given):
        with self.assertRaises(uf.DeltaError):
            parse(data, **given)

    def test_good(self):
        manifest = parse(dump(manifest_obj()))
        self.assertEqual(manifest, uf.Manifest(
            VERSION, "ACC-1.0.1.zip", ZIP_SIZE, ZIP_SHA,
            (uf.FileEntry("app/ACC.exe", 10, "1" * 64), uf.FileEntry("app/data/角色.json", 0, "2" * 64),
             uf.FileEntry("拾星助手.exe", 5, "3" * 64)),
            ("app/空目录",)))

    def test_unknown_fields_ignored(self):
        obj = manifest_obj(note="以后加的字段")
        obj["files"][0]["mode"] = 0o644
        self.assertEqual(parse(dump(obj)).files[0], uf.FileEntry("app/ACC.exe", 10, "1" * 64))

    def test_given_sha_any_case(self):
        """认下的 SHA-256 写成大写也认（清单里的必须是小写）。"""
        self.assertEqual(parse(dump(manifest_obj()), zip_sha256=ZIP_SHA.upper()).zip_sha256, ZIP_SHA)
        self.assert_rejected(dump(manifest_obj(zip_sha256=ZIP_SHA.upper())), zip_sha256=ZIP_SHA.upper())

    def test_format(self):
        for fmt in (2, 0, "1", 1.0, True, None):
            with self.subTest(fmt=fmt):
                self.assert_rejected(dump(manifest_obj(format=fmt)))
        obj = manifest_obj()
        del obj["format"]
        self.assert_rejected(dump(obj))

    def test_missing_fields(self):
        for key in ("version", "zip", "zip_size", "zip_sha256", "files", "dirs"):
            with self.subTest(key=key):
                obj = manifest_obj()
                del obj[key]
                self.assert_rejected(dump(obj))
        for key in ("path", "size", "sha256"):
            with self.subTest(file_key=key):
                obj = manifest_obj()
                del obj["files"][0][key]
                self.assert_rejected(dump(obj))

    def test_bad_types(self):
        bad = [
            manifest_obj(zip_size=str(ZIP_SIZE)), manifest_obj(zip_size=float(ZIP_SIZE)), manifest_obj(files={}),
            manifest_obj(dirs="app/空目录"), manifest_obj(dirs=[1]), manifest_obj(files=["app/ACC.exe"]),
        ]
        for field, value in (("size", -1), ("size", True), ("size", 1.5), ("size", "5"), ("sha256", "1" * 63),
                             ("sha256", "A" * 64), ("sha256", "g" * 64), ("sha256", None), ("path", 5)):
            obj = manifest_obj()
            obj["files"][0][field] = value
            bad.append(obj)
        for obj in bad:
            with self.subTest(obj=obj):
                self.assert_rejected(dump(obj))

    def test_bad_paths(self):
        """路径有 ..、绝对路径、盘符、冒号、反斜杠、空段，或者是 Windows 上不能当文件名的：文件、空目录都不认。"""
        for path in BAD_PATHS:
            with self.subTest(file=path):
                obj = manifest_obj()
                obj["files"].append({"path": path, "size": 1, "sha256": "4" * 64})
                self.assert_rejected(dump(obj))
            with self.subTest(dir=path):
                self.assert_rejected(dump(manifest_obj(dirs=[path])))

    def test_duplicates_ignoring_case(self):
        same_file = manifest_obj()
        same_file["files"].append({"path": "APP/acc.EXE", "size": 1, "sha256": "4" * 64})
        exact_twice = manifest_obj()
        exact_twice["files"].append({"path": "app/ACC.exe", "size": 10, "sha256": "1" * 64})
        under_file = manifest_obj()
        under_file["files"].append({"path": "app/ACC.exe/x.dll", "size": 1, "sha256": "4" * 64})
        cases = {
            "文件只差大小写": same_file,
            "文件写了两遍": exact_twice,
            "文件在文件下面": under_file,
            "空目录只差大小写": manifest_obj(dirs=["app/空目录", "APP/空目录"]),
            "空目录和文件同名": manifest_obj(dirs=["App/acc.exe"]),
            "空目录在文件下面": manifest_obj(dirs=["拾星助手.exe/sub"]),
        }
        for label, obj in cases.items():
            with self.subTest(label):
                self.assert_rejected(dump(obj))

    def test_limits(self):
        """文件数、空目录数、解压后合计、清单本身的大小，超上限都不认；正好等于上限的认。"""
        with mock.patch.object(uf, "MAX_FILES", 3):
            parse(dump(manifest_obj()))
            obj = manifest_obj()
            obj["files"].append({"path": "app/one.more", "size": 1, "sha256": "4" * 64})
            self.assert_rejected(dump(obj))
            self.assert_rejected(dump(manifest_obj(dirs=["a", "b", "c", "d"])))
        obj = manifest_obj()
        obj["files"][0]["size"] = uf.MAX_TOTAL - 5
        parse(dump(obj))
        obj["files"][0]["size"] = uf.MAX_TOTAL - 4
        self.assert_rejected(dump(obj))
        data = dump(manifest_obj())
        with mock.patch.object(uf, "MAX_MANIFEST", len(data) - 1):
            self.assert_rejected(data)

    def test_required_programs(self):
        for name in ("app/ACC.exe", "拾星助手.exe"):
            with self.subTest(name=name):
                obj = manifest_obj()
                obj["files"] = [item for item in obj["files"] if item["path"] != name]
                self.assert_rejected(dump(obj))
        obj = manifest_obj()
        obj["files"][0]["path"] = "APP/acc.exe"  # 写法要一字不差
        self.assert_rejected(dump(obj))

    def test_must_match_release(self):
        """版本号、整包的文件名、大小、SHA-256 要和认下的那一版一样。"""
        good = dump(manifest_obj())
        self.assert_rejected(good, version="1.0.2")
        self.assert_rejected(good, zip_size=ZIP_SIZE + 1)
        self.assert_rejected(good, zip_sha256="f" * 64)
        self.assert_rejected(dump(manifest_obj(version="1.0.2", zip="ACC-1.0.2.zip")))
        self.assert_rejected(dump(manifest_obj(zip="ACC-1.0.1.7z")))
        self.assert_rejected(dump(manifest_obj(zip="releases/ACC-1.0.1.zip")))

    def test_not_a_manifest(self):
        text_ok = dump(manifest_obj()).decode("utf-8")
        cases = {
            "不是 JSON": b"not json",
            "不是 UTF-8": text_ok.encode("utf-16"),
            "带 BOM": b"\xef\xbb\xbf" + text_ok.encode("utf-8"),
            "不是对象": b"[]",
            "键重复": text_ok.replace('"format": 1,', '"format": 1, "format": 1,').encode("utf-8"),
            "NaN": text_ok.replace('"size": 10', '"size": NaN').encode("utf-8"),
            "不是字节串": text_ok,
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assert_rejected(data)


# ---------------------------------------------------------------- 拼新版本


class AssembleCase(unittest.TestCase):
    FILES = NEW
    ZIP_ARGS: dict = {}

    def setUp(self):
        self.rel = Release(self, self.FILES, **self.ZIP_ARGS)
        self.root_dir = sandbox.temp_dir(self) / "AstralChipCompass"
        (self.root_dir / "update").mkdir(parents=True)
        self.dest = self.root_dir / "update" / "new.part"
        self.calls: list[tuple[int, int]] = []  # progress 的每一次

    def root(self, files: dict[str, bytes]) -> Path:
        return make_root(self.root_dir, files)

    def run_assemble(self, root: Path, fetch=None, **kw) -> bool:
        kw.setdefault("progress", lambda done, total: self.calls.append((done, total)))
        return uf.assemble(self.rel.manifest, root, self.dest, fetch or self.rel.fetch, **kw)

    def assert_matches_zip(self):
        """拼出来的和整包解压的一字不差（文件和各级目录）。"""
        files, dirs = tree(self.dest)
        want_files, want_dirs = unpacked(self.rel.blob)
        self.assertEqual(sorted(files), sorted(want_files))
        for name, content in want_files.items():
            self.assertEqual(files[name], content, name)
        self.assertEqual(dirs, want_dirs)

    def assert_progress(self, total: int):
        """progress：先报 (0, 总数)，单调不减，总数不变，最后等于总数。"""
        self.assertEqual(self.calls[0], (0, total))
        self.assertEqual(self.calls[-1], (total, total))
        done = [d for d, _ in self.calls]
        self.assertEqual(done, sorted(done))
        self.assertEqual({t for _, t in self.calls}, {total})

    def assert_root_untouched(self, files: dict[str, bytes]):
        """本机的文件一个没动，update 里除了 dest 什么都没多。"""
        self.assertEqual(tree(self.root_dir, skip=self.root_dir / "update")[0], files)
        self.assertLessEqual({p.name for p in (self.root_dir / "update").iterdir()}, {"new.part"})


class AssembleTest(AssembleCase):
    def test_all_same(self):
        """本机全都一样：全部复制，一个字节都不取，中央目录也不读。"""
        root = self.root(NEW)
        self.assertTrue(self.run_assemble(root))
        self.assert_matches_zip()
        self.assertEqual(self.rel.fetch.calls, [])
        self.assertEqual(self.calls, [(0, 0)])
        self.assert_root_untouched(NEW)

    def test_partial(self):
        """部分不一样（大小变了、大小一样内容变了、本机没有）：只取这几个，其余复制；请求次数、取的字节在范围内。"""
        root = self.root(OLD)
        self.assertTrue(self.run_assemble(root))
        self.assert_matches_zip()
        need = compressed(self.rel.blob, CHANGED)
        self.assert_progress(need)
        calls = self.rel.fetch.calls
        # 读中央目录一次，变了的三个文件各一次（都比一段小）
        self.assertEqual(len(calls), 1 + len(CHANGED))
        # 每次请求（中央目录那次除外）都从变了的文件的本地文件头开始
        self.assertEqual(sorted(start for start, _ in calls[1:]),
                         sorted(info_of(self.rel.blob, name).header_offset for name in CHANGED))
        # 取的字节：要取的压缩数据加中央目录，多出来的在要取的几个百分点、外加每个文件几 KB 以内
        extra = self.rel.fetch.fetched - need - central_size(self.rel.blob)
        self.assertGreaterEqual(extra, 0)
        self.assertLessEqual(extra, 0.05 * need + 4096 * len(CHANGED))
        # 本机的旧文件、用户数据不进新版本；本机一个文件没动
        self.assertFalse((self.dest / "app" / "old.dll").exists())
        self.assertFalse((self.dest / "app" / "logs").exists())
        self.assert_root_untouched(OLD)

    def test_missing_files(self):
        """本机缺了一部分文件：缺的都取。"""
        gone = sorted(NEW)[::3]
        root = self.root({name: data for name, data in NEW.items() if name not in gone})
        self.assertTrue(self.run_assemble(root, max_ratio=1.0))
        self.assert_matches_zip()
        self.assertEqual(len(self.rel.fetch.calls), 1 + len(gone))
        self.assert_progress(compressed(self.rel.blob, gone))

    def test_same_size_different_content(self):
        """大小一样、内容不一样的也取（算了 SHA-256 才知道）。"""
        root = self.root({**NEW, "app/python314.dll": rand(99, 300_000)})
        self.assertTrue(self.run_assemble(root))
        self.assert_matches_zip()
        self.assertEqual([start for start, _ in self.rel.fetch.calls[1:]],
                         [info_of(self.rel.blob, "app/python314.dll").header_offset])

    def test_not_worth_it(self):
        """要取的超过整包的六成：不值得，返回 False，dest 不建，只读了中央目录，progress 一次没报。"""
        root = self.root({"拾星助手.exe": NEW["拾星助手.exe"]})
        self.assertFalse(self.run_assemble(root))
        self.assertFalse(self.dest.exists())
        self.assertEqual(len(self.rel.fetch.calls), 1)
        self.assertEqual(self.calls, [])
        self.assert_root_untouched({"拾星助手.exe": NEW["拾星助手.exe"]})

    def test_everything_when_allowed(self):
        """本机什么都没有、max_ratio 放到 1：全部从整包里取，和整包解压的一字不差。"""
        root = self.root_dir
        self.assertTrue(self.run_assemble(root, max_ratio=1.0))
        self.assert_matches_zip()
        self.assertEqual(len(self.rel.fetch.calls), 1 + len(NEW))
        self.assert_progress(compressed(self.rel.blob, NEW))

    def test_dest_empty_folder_ok(self):
        self.dest.mkdir()
        self.assertTrue(self.run_assemble(self.root(OLD)))
        self.assert_matches_zip()

    def test_dest_not_empty(self):
        self.dest.mkdir()
        (self.dest / "x.txt").write_bytes(b"x")
        with self.assertRaises(uf.DeltaError):
            self.run_assemble(self.root(OLD))
        self.assertEqual(self.rel.fetch.calls, [])

    def test_bad_path_in_manifest_object(self):
        """Manifest 不是 parse_manifest 造的、路径不对时：一个文件都不写，dest 也不建。"""
        bad = uf.FileEntry("../evil.txt", 1, hashlib.sha256(b"x").hexdigest())
        manifest = dataclasses.replace(self.rel.manifest, files=self.rel.manifest.files + (bad,))
        with self.assertRaises(uf.DeltaError):
            uf.assemble(manifest, self.root(OLD), self.dest, self.rel.fetch)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.rel.fetch.calls, [])
        self.assert_root_untouched(OLD)


class DataDescriptorTest(AssembleCase):
    """每个文件后面跟数据描述符的包（本地文件头里的大小是 0）：按中央目录照样取，多取的只是那十几个字节。"""
    ZIP_ARGS = {"seekable": False}

    def test_partial(self):
        self.assertTrue(self.run_assemble(self.root(OLD)))
        self.assert_matches_zip()
        self.assertEqual(len(self.rel.fetch.calls), 1 + len(CHANGED))
        self.assert_progress(compressed(self.rel.blob, CHANGED))


class EntryCommentTest(AssembleCase):
    """每个文件在中央目录里带注释：中央目录比按清单估的大，补取一次（前面差的那一截），照样拼得出。"""
    ZIP_ARGS = {"entry_comment": b"c" * 300}

    def test_partial(self):
        self.assertTrue(self.run_assemble(self.root(OLD)))
        self.assert_matches_zip()
        self.assertEqual(len(self.rel.fetch.calls), 2 + len(CHANGED))
        self.assertEqual(self.rel.fetch.calls[1][1] + 1, self.rel.fetch.calls[0][0])  # 补的正好接在第一次前面
        self.assert_progress(compressed(self.rel.blob, CHANGED))


class ArchiveCommentTest(AssembleCase):
    """整包末尾带注释（尾部记录不在最后）：zipfile 往前找尾部记录时补取一次，照样拼得出。"""
    ZIP_ARGS = {"archive_comment": "整包末尾的注释".encode("utf-8") * 20}

    def test_partial(self):
        self.assertTrue(self.run_assemble(self.root(OLD)))
        self.assert_matches_zip()
        self.assertEqual(len(self.rel.fetch.calls), 2 + len(CHANGED))
        self.assert_progress(compressed(self.rel.blob, CHANGED))


class BigFileTest(AssembleCase):
    """大文件分段取：每次请求不超过一段（测试里把一段改成 256 KB，文件约 3 MB），不整个读进内存。"""
    FILES = {**NEW, "app/big.bin": rand(20, 3_000_000)}

    def test_segments(self):
        segment = 256 * 1024
        root = self.root(NEW)  # 本机只缺 big.bin
        with mock.patch.object(uf, "SEGMENT", segment):
            self.assertTrue(self.run_assemble(root, max_ratio=1.0))
        self.assert_matches_zip()
        lengths = [end - start + 1 for start, end in self.rel.fetch.calls]
        self.assertTrue(all(n <= segment for n in lengths), lengths)
        info = info_of(self.rel.blob, "app/big.bin")
        csize = info.compress_size
        header = data_span(self.rel.blob, "app/big.bin")[0] - info.header_offset
        self.assertEqual(len(lengths), 1 + -(-(header + csize) // segment))  # 中央目录一次，其余每段一次
        self.assert_progress(csize)
        self.assertGreater(len(self.calls), 10)  # 每取到一段报一次


class StopTest(AssembleCase):
    def test_stop_midway(self):
        """取到第一个文件的数据以后按停止：抛 Stopped，之后不再取。"""
        root = self.root({name: data for name, data in NEW.items()
                          if name not in ("app/ACC.exe", "app/python314.dll", "app/qt/界面库.dll")})
        stop = threading.Event()
        late = []

        def fetch(start, end):
            if stop.is_set():
                late.append((start, end))
            return self.rel.fetch(start, end)

        def progress(done, total):
            if done:
                stop.set()

        with self.assertRaises(winhttp.Stopped):
            uf.assemble(self.rel.manifest, root, self.dest, fetch, progress=progress, stop=stop, max_ratio=1.0)
        self.assertEqual(late, [])
        self.assertEqual(len(self.rel.fetch.calls), 2)  # 中央目录、第一个文件

    def test_stop_before_start(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(winhttp.Stopped):
            self.run_assemble(self.root(OLD), stop=stop)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.rel.fetch.calls, [])

    def test_stop_while_comparing(self):
        """stop 也可以是返回 bool 的函数：对照本机文件时置位，同样抛 Stopped，什么都没取。"""
        asked = []

        def stop():
            asked.append(1)
            return len(asked) > 3

        with self.assertRaises(winhttp.Stopped):
            self.run_assemble(self.root(OLD), stop=stop)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.rel.fetch.calls, [])


class FailTest(AssembleCase):
    """走不通的几种：都抛 DeltaError，dest 以外什么都没多、本机文件一个没动。"""

    def assert_fails(self, root_files=OLD, *, manifest=None, fetch=None, contains: str = "") -> uf.DeltaError:
        root = self.root(root_files)
        with self.assertRaises(uf.DeltaError) as caught:
            uf.assemble(manifest or self.rel.manifest, root, self.dest, fetch or self.rel.fetch)
        self.assertIn(contains, str(caught.exception))
        self.assert_root_untouched(root_files)
        return caught.exception

    def test_server_ignores_range(self):
        """服务器不认 Range、回 200：测试的 fetch 认出来报错，转成 DeltaError。"""
        self.rel.server.mode = "full"
        self.assert_fails(contains="206")

    def test_whole_file_returned(self):
        """fetch 没核对状态码、把 200 的整个文件交回来：长度对不上，照样走不通。"""
        self.rel.server.mode = "full"
        naive = RangeFetch(self.rel.server.url, len(self.rel.blob), check=False)
        self.assert_fails(fetch=naive, contains="交回的是")

    def test_reply_longer_than_asked(self):
        self.rel.server.mode = "long"
        self.assert_fails(contains="不往下读")

    def test_server_down(self):
        self.rel.server.close()
        self.assert_fails(contains="没成")

    def test_tampered_stored_member(self):
        """回的字节被改过（存储方式的文件）：SHA-256 对不上。"""
        start, end = data_span(self.rel.blob, "app/models/det.onnx")
        self.rel.server.blob = flip(self.rel.blob, (start + end) // 2)
        self.assert_fails({name: data for name, data in NEW.items() if name != "app/models/det.onnx"},
                          contains="SHA-256")

    def test_tampered_deflated_member(self):
        """回的字节被改过（deflate 的文件）：解不开或解出来对不上。"""
        start, _ = data_span(self.rel.blob, "app/ACC.exe")
        self.rel.server.blob = flip(self.rel.blob, start + 100)
        self.assert_fails(contains="app/ACC.exe")

    def test_tampered_local_header(self):
        offset = info_of(self.rel.blob, "app/ACC.exe").header_offset
        self.rel.server.blob = flip(self.rel.blob, offset)
        self.assert_fails(contains="本地文件头")

    def test_member_bigger_than_manifest(self):
        """中央目录和清单都说 19000 字节，解压出来却更多：超过就停，写出去的不超过清单的大小。"""
        name = "app/data/角色.json"
        cut = len(NEW[name]) - 1000
        blob = patch_size(self.rel.blob, name, cut)
        obj = json.loads(self.rel.data)
        for item in obj["files"]:
            if item["path"] == name:
                item.update(size=cut, sha256=hashlib.sha256(NEW[name][:cut]).hexdigest())
        obj["zip_sha256"] = hashlib.sha256(blob).hexdigest()
        manifest = self.rel.parse(dump(obj), blob)
        self.rel.server.blob = blob
        self.assert_fails({k: v for k, v in NEW.items() if k != name}, manifest=manifest, contains="比清单写的大")
        self.assertLessEqual((self.dest / "app" / "data" / "角色.json").stat().st_size, cut)

    def test_missing_in_central_directory(self):
        """清单上有、整包的中央目录里没有：走不通，dest 不建。"""
        obj = json.loads(self.rel.data)
        obj["files"].append({"path": "app/data/extra.json", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()})
        obj["files"].sort(key=lambda item: item["path"])
        self.assert_fails(NEW, manifest=self.rel.parse(dump(obj)), contains="中央目录里没有")
        self.assertFalse(self.dest.exists())

    def test_local_file_changed_while_copying(self):
        """对照完、复制之前本机文件被改了（大小不变）：复制时算出来的 SHA-256 对不上。"""
        root = self.root(NEW)
        victim = root / "app" / "python314.dll"
        changed = rand(77, len(NEW["app/python314.dll"]))
        with self.assertRaises(uf.DeltaError) as caught:
            self.run_assemble(root, progress=lambda done, total: victim.write_bytes(changed))
        self.assertIn("app/python314.dll", str(caught.exception))

    def test_local_file_grew_while_copying(self):
        root = self.root(NEW)
        victim = root / "app" / "data" / "maps.json"
        with self.assertRaises(uf.DeltaError) as caught:
            self.run_assemble(root, progress=lambda done, total: victim.write_bytes(NEW["app/data/maps.json"] + b"!"))
        self.assertIn("比清单写的大", str(caught.exception))


# ---------------------------------------------------------------- 手写的压缩包：zipfile 写不出来的毛病

LOCAL_HEADER = struct.Struct("<4s2B4HL2L2H")  # 本地文件头（30 字节）
CENTRAL_ENTRY = struct.Struct("<4s4B4H3L5H2L")  # 中央目录的一个条目（46 字节）
END_RECORD = struct.Struct("<4s4H2LH")  # 尾部记录（22 字节）


def deflate(data: bytes) -> bytes:
    """不带 zlib 头尾的 deflate 数据，同 zipfile 写的。"""
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return packer.compress(data) + packer.flush()


def raw_zip(entries: list[dict]) -> bytes:
    """一个字节一个字节写出来的压缩包（名字用 UTF-8、带标志），能造出 zipfile 写不出来的毛病。entries 每项有 name、
    content（解压后的内容）、method（默认 deflate），另可带：data（压缩数据，默认按 method 由 content 算）、local_name、
    local_method、local_extra_len（本地文件头里写的名字、方式、扩展字段长度；扩展字段本身不写）、cd_csize、cd_size、
    cd_offset（中央目录里写的压缩后大小、解压后大小、本地文件头的位置，"cd" 是中央目录的开头）、cd_twice（中央目录里
    写两遍）。"""
    body = bytearray()
    placed = []
    for e in entries:
        method = e.get("method", zipfile.ZIP_DEFLATED)
        content = e["content"]
        data = e.get("data", deflate(content) if method == zipfile.ZIP_DEFLATED else content)
        crc = zlib.crc32(content)
        local_name = (TOP + e.get("local_name", e["name"])).encode("utf-8")
        placed.append((e, method, crc, data, len(body)))
        body += LOCAL_HEADER.pack(b"PK\x03\x04", 20, 0, 0x0800, e.get("local_method", method), 0, 0x21, crc,
                                  len(data), len(content), len(local_name), e.get("local_extra_len", 0))
        body += local_name + data
    start = len(body)
    central = bytearray()
    count = 0
    for e, method, crc, data, offset in placed:
        name = (TOP + e["name"]).encode("utf-8")
        where = start if e.get("cd_offset") == "cd" else e.get("cd_offset", offset)
        record = CENTRAL_ENTRY.pack(b"PK\x01\x02", 20, 0, 20, 0, 0x0800, method, 0, 0x21, crc,
                                    e.get("cd_csize", len(data)), e.get("cd_size", len(e["content"])), len(name),
                                    0, 0, 0, 0, 0o644 << 16, where) + name
        for _ in range(2 if e.get("cd_twice") else 1):
            central += record
            count += 1
    return bytes(body + central + END_RECORD.pack(b"PK\x05\x06", 0, 0, count, count, len(central), start, 0))


class UntrustedZipTest(unittest.TestCase):
    """整包的中央目录、本地文件头都当不可信的数据：手写的包造出各种毛病，都抛 DeltaError、原因对得上，dest 以外什么都没写。
    本机什么都没有（全部要取）；清单按真实的内容写（不经 build_manifest：有的毛病 zipfile 读不了），fetch 直接切字节。"""

    CONTENT = {"app/ACC.exe": text(32, 50_000), "app/data/x.json": text(33, 8_000), "拾星助手.exe": rand(31, 3_000)}

    def setUp(self):
        base = sandbox.temp_dir(self)
        self.root = base / "AstralChipCompass"
        (self.root / "update").mkdir(parents=True)
        self.dest = self.root / "update" / "new.part"
        self.outside = base / "outside"
        self.outside.mkdir()

    def entries(self, **defect) -> list[dict]:
        """三个文件（app/ACC.exe 在最前面，后面跟着别的条目），app/ACC.exe 带上 defect 里的毛病。"""
        out = []
        for name in sorted(self.CONTENT):
            e = {"name": name, "content": self.CONTENT[name],
                 "method": zipfile.ZIP_STORED if name == "拾星助手.exe" else zipfile.ZIP_DEFLATED}
            if name == "app/ACC.exe":
                e.update(defect)
            out.append(e)
        return out

    def assemble(self, blob: bytes, **kw) -> bool:
        files = tuple(uf.FileEntry(name, len(data), hashlib.sha256(data).hexdigest())
                      for name, data in sorted(self.CONTENT.items()))
        manifest = uf.Manifest(VERSION, uf.zip_name(VERSION), len(blob), hashlib.sha256(blob).hexdigest(), files, ())
        return uf.assemble(manifest, self.root, self.dest, lambda start, end: blob[start:end + 1], max_ratio=1.0, **kw)

    def assert_fails(self, blob: bytes, contains: str, **kw) -> None:
        with self.assertRaises(uf.DeltaError) as caught:
            self.assemble(blob, **kw)
        self.assertIn(contains, str(caught.exception))
        self.assertEqual([p.name for p in self.root.iterdir()], ["update"])
        self.assertEqual(list(self.outside.iterdir()), [])
        if self.dest.exists():  # 同调用方：走不通就删掉 dest，下一次从空的开始
            shutil.rmtree(self.dest)

    def test_good(self):
        """没毛病的手写包拼得出（下面几条的对照）。"""
        self.assertTrue(self.assemble(raw_zip(self.entries())))
        self.assertEqual(tree(self.dest)[0], self.CONTENT)

    def test_central_entry_overlaps_next(self):
        """中央目录写的压缩后大小伸进了下一个条目：不取。"""
        size = len(deflate(self.CONTENT["app/ACC.exe"]))
        self.assert_fails(raw_zip(self.entries(cd_csize=size + 40)), "范围和别的条目重叠")

    def test_local_extra_runs_past_entry(self):
        """本地文件头写的扩展字段长度让压缩数据越过了这个条目的范围：不取。"""
        self.assert_fails(raw_zip(self.entries(local_extra_len=4000)), "压缩数据和别的条目重叠")

    def test_local_header_disagrees(self):
        """本地文件头的名字、压缩方式和中央目录不一样：不取（数据本身没毛病也不取）。"""
        self.assert_fails(raw_zip(self.entries(local_name="app/ACC.exf")), "名字不一样")
        self.assert_fails(raw_zip(self.entries(local_method=zipfile.ZIP_STORED)), "方式、标志")

    def test_offset_not_before_central_directory(self):
        """中央目录写的本地文件头位置不在中央目录前面：不取。"""
        self.assert_fails(raw_zip(self.entries(cd_offset="cd")), "位置不对")

    def test_name_twice_in_central_directory(self):
        self.assert_fails(raw_zip(self.entries(cd_twice=True)), "有两个")

    def test_sizes_in_central_directory(self):
        """中央目录写的解压后大小和清单不一样、存储方式的压缩前后大小不一样：不取（内容对得上清单也不取）。"""
        content = self.CONTENT["app/ACC.exe"]
        self.assert_fails(raw_zip(self.entries(cd_size=len(content) + 1)), "解压后是")
        self.assert_fails(raw_zip(self.entries(method=zipfile.ZIP_STORED, cd_csize=len(content) - 1)), "存储方式")

    def test_data_after_deflate_stream(self):
        """压缩数据流结束以后还有多的：同一截里的、下一截才轮到的（一截只喂 1 字节）都不认。"""
        blob = raw_zip(self.entries(data=deflate(self.CONTENT["app/ACC.exe"]) + b"junk" * 8))
        self.assert_fails(blob, "结束以后还有多的")
        with mock.patch.object(uf, "FEED", 1):
            self.assert_fails(blob, "结束以后还有多的")

    def test_deflate_stream_cut_short(self):
        self.assert_fails(raw_zip(self.entries(data=deflate(self.CONTENT["app/ACC.exe"])[:-6])), "不完整")

    def test_central_directory_too_big(self):
        """中央目录（连同末尾的记录）超过上限：不读。"""
        with mock.patch.object(uf, "MAX_CENTRAL", 100):
            self.assert_fails(raw_zip(self.entries()), "中央目录超过")

    def test_range_outside_zip(self):
        """要取的字节不在整包里：不交给 fetch。"""
        asked = []
        manifest = uf.Manifest(VERSION, uf.zip_name(VERSION), 1000, "0" * 64, (), ())
        job = uf._Assembly(manifest, self.root, self.dest, lambda start, end: asked.append((start, end)), None, None)
        for start, end in ((-1, 10), (10, 9), (0, 1000), (999, 1000)):
            with self.subTest(start=start, end=end), self.assertRaises(uf.DeltaError):
                job._get(start, end)
        self.assertEqual(asked, [])

    def test_extra_file_in_dest(self):
        """拼的时候 dest 里多出了清单上没有的：拼完核对时认出来。"""

        def progress(done, total):
            if not done:
                (self.dest / "extra.txt").write_bytes(b"x")

        with self.assertRaises(uf.DeltaError) as caught:
            self.assemble(raw_zip(self.entries()), progress=progress)
        self.assertIn("extra.txt", str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "目录链接只在 Windows 上有")
    def test_link_inside_dest_not_followed(self):
        """拼的时候 dest 里的 app 成了指向别处的目录链接：解析以后不在 dest 下面，不往里写。"""
        import _winapi

        def progress(done, total):
            if not done:
                _winapi.CreateJunction(str(self.outside), str(self.dest / "app"))

        with self.assertRaises(uf.DeltaError) as caught:
            self.assemble(raw_zip(self.entries()), progress=progress)
        self.assertIn("不在", str(caught.exception))
        self.assertEqual(list(self.outside.iterdir()), [])
        os.unlink(self.dest / "app")  # 只删链接本身


if __name__ == "__main__":
    unittest.main()
