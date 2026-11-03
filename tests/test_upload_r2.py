"""发版上传脚本 tools/upload_r2.py：签名、令牌文件、上传的顺序和中途停下。不联网，不读真的令牌文件（令牌一律是这里造的
假值，环境变量 ACC_R2_TOKEN_FILE 在每个用例里先清掉）。

  SigV4Test       签名照 AWS 文档里公开的 S3 签名示例逐字核对（GET Object、PUT Object、带查询串的两个），上传请求签了哪些头
  TokenFileTest   令牌文件：CRLF、空行、首尾空白、BOM、# 说明行；缺项、写法不对时只报名字和行号；endpoint 只认 R2 的 https
                  地址（本机地址除外）
  RedactTest      令牌的值（连同 endpoint 的主机名、账号那一段）换成占位，不分大小写
  RedirectTest    重定向不从 https 降级成明文 http；签了名的上传（PUT）遇到重定向不跟
  UploadFlowTest  本机的假服务器同时当 R2 的上传接口、公开地址和 GitHub 的接口（FakeCloud）：上传的顺序、每个文件的请求头、
                  签名、latest.json 的内容（含文件清单的三项）；读公开地址和 GitHub 的请求不带签名；GitHub 对不上（压缩包、
                  文件清单两个附件）、公开地址下回来不对、R2 回错、R2 回重定向时停在哪一步、报的 R2 状态；最后按段取发行包：
                  回 206 时通过，回 200、Content-Range 或长度不对时只打警告；试运行不发上传请求；本地的东西不对（含文件清单
                  不在、和压缩包对不上）、压缩包大小超出上下限时一个请求都不发；任何输出（标准输出、标准错误、异常的说明）里都
                  没有令牌的值
  AddressTest     写死的公开地址、GitHub 接口和程序查更新用的一样（advisor/update.py 的 MIRROR_BASE、GITHUB_API）
脚本发往本机以外的请求在测试里直接算失败（UploadFlowTest.local_only），不会真的发出去。
单独跑：python -B tools/run_tests.py --only test_upload_r2
"""
from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import socketserver
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from advisor import paths, update, update_files
from tests.support import sandbox
from tools import upload_r2

REAL_OPEN = upload_r2._open
EMPTY = hashlib.sha256(b"").hexdigest()

# AWS 文档里 S3 签名示例用的数据（文档公开的示例值，不是真的令牌）
AWS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_DATE = "20130524T000000Z"
AWS_HOST = "examplebucket.s3.amazonaws.com"

# 这里造的假令牌：一眼认得出，任何输出里都不许出现
FAKE_KEY_ID = "FAKE-KEY-ID-0123456789abcdef"
FAKE_SECRET = "FAKE-SECRET-must-never-be-printed-9876"
ACCOUNT = "0123456789abcdef0123456789abcdef"
R2_ENDPOINT = f"https://{ACCOUNT}.r2.cloudflarestorage.com"

VERSION = "1.0.1"
SYNCED = "2030-01-02"
AMZ_DATE = "20300102T030405Z"
NOTES_TEXT = "这一版改了什么\r\n- 改了一处\r\n"


def make_zip(path: Path, names=upload_r2.REQUIRED_IN_ZIP, payload: int = (1 << 20) + 4096) -> Path:
    """造一个发行包：第一个文件是 payload 字节的随机内容（不压缩，整个包比 1 MB 大），其余几个很小。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for index, name in enumerate(names):
            archive.writestr(name, os.urandom(payload if index == 0 else 16))
    return path


def write_token(path: Path, *lines: str, bom: bool = False) -> Path:
    """令牌文件：各行用 CRLF 连起来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + "\r\n".join(lines).encode("utf-8"))
    return path


def github_release(version: str, size: int, sha: str | None, files: tuple[int, str | None] | None = None) -> bytes:
    """GitHub 接口回的最新正式版：附件里另有一个不相干的文件；sha 为 None 时压缩包的附件没有 digest。files 是文件清单附件
    ACC-<版本>.files.json 的（大小, SHA-256）：SHA-256 为 None 时这个附件没有 digest；files 为 None 时没有这个附件。"""
    def asset(name: str, size: int, sha: str | None) -> dict:
        item = {"name": name, "size": size,
                "browser_download_url": f"https://github.com/s1nan-dev/astral-chip-compass/releases/download/"
                                        f"v{version}/{name}"}
        if sha is not None:
            item["digest"] = f"sha256:{sha}"
        return item

    assets = [{"name": "notes.txt", "size": 3, "digest": "sha256:" + "0" * 64}, asset(f"ACC-{version}.zip", size, sha)]
    if files is not None:
        assets.append(asset(f"ACC-{version}.files.json", *files))
    return json.dumps({"tag_name": f"v{version}",
                       "html_url": f"https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v{version}",
                       "assets": assets}).encode()


class SigV4Test(unittest.TestCase):
    """照 AWS 文档 Amazon S3 API Reference「Signature Calculations for the Authorization Header: Transferring Payload in a
    Single Chunk (AWS Signature Version 4)」一节的示例（区域 us-east-1、服务 s3、虚拟主机式地址）逐字核对规范请求、待签
    字符串和签名。文档里的 Authorization 头各段之间只有逗号，本脚本按逗号加空格写（两种 S3 都认），比较时去掉空格。"""

    def sign(self, method, path, query, headers, payload=EMPTY):
        return upload_r2.sign(method, path, query, headers, payload, access_key_id=AWS_KEY_ID, secret=AWS_SECRET,
                              amz_date=AWS_DATE, region="us-east-1", service="s3")

    @staticmethod
    def compact(authorization: str) -> str:
        return re.sub(r",\s+", ",", authorization)

    def test_get_object(self):
        # 文档的请求里 Range 的值后面带了一个空格：规范请求里去掉首尾空白
        sig = self.sign("GET", "/test.txt", "", {"Host": AWS_HOST, "Range": "bytes=0-9 ",
                                                 "x-amz-content-sha256": EMPTY, "x-amz-date": AWS_DATE})
        self.assertEqual(sig.canonical_request, "\n".join([
            "GET", "/test.txt", "",
            f"host:{AWS_HOST}", "range:bytes=0-9", f"x-amz-content-sha256:{EMPTY}", f"x-amz-date:{AWS_DATE}", "",
            "host;range;x-amz-content-sha256;x-amz-date", EMPTY]))
        self.assertEqual(sig.string_to_sign, "\n".join([
            "AWS4-HMAC-SHA256", AWS_DATE, "20130524/us-east-1/s3/aws4_request",
            "7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972"]))
        self.assertEqual(sig.signature, "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
        self.assertEqual(self.compact(sig.authorization),
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request,"
                         "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
                         "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")

    def test_put_object(self):
        """PUT 的示例：对象名里的「$」在路径里编码成 %24，Date 头也签；正文「Welcome to Amazon S3.」。"""
        body = b"Welcome to Amazon S3."
        payload = hashlib.sha256(body).hexdigest()
        self.assertEqual(payload, "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072")
        path = "/" + upload_r2.uri_encode("test$file.text", keep_slash=True)
        self.assertEqual(path, "/test%24file.text")
        sig = self.sign("PUT", path, "", {
            "Host": AWS_HOST, "Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-date": f"{AWS_DATE} ",
            "x-amz-storage-class": "REDUCED_REDUNDANCY", "x-amz-content-sha256": payload}, payload)
        self.assertEqual(sig.canonical_request, "\n".join([
            "PUT", "/test%24file.text", "",
            "date:Fri, 24 May 2013 00:00:00 GMT", f"host:{AWS_HOST}", f"x-amz-content-sha256:{payload}",
            f"x-amz-date:{AWS_DATE}", "x-amz-storage-class:REDUCED_REDUNDANCY", "",
            "date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class", payload]))
        self.assertEqual(sig.string_to_sign, "\n".join([
            "AWS4-HMAC-SHA256", AWS_DATE, "20130524/us-east-1/s3/aws4_request",
            "9e0e90d9c76de8fa5b200d8c849cd5b8dc7a3be3951ddb7f6a76b4158342019d"]))
        self.assertEqual(sig.signature, "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")
        self.assertEqual(self.compact(sig.authorization),
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request,"
                         "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class,"
                         "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")

    def test_query_examples(self):
        """GET Bucket Lifecycle（?lifecycle，只有名字的参数写成「lifecycle=」）、List Objects（?max-keys=2&prefix=J）：
        路径是空的，规范请求里写「/」。"""
        headers = {"Host": AWS_HOST, "x-amz-date": AWS_DATE, "x-amz-content-sha256": EMPTY}
        for query, canonical, request_hash, signature in (
            ("lifecycle", "lifecycle=", "9766c798316ff2757b517bc739a67f6213b4ab36dd5da2f94eaebf79c77395ca",
             "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"),
            ("max-keys=2&prefix=J", "max-keys=2&prefix=J",
             "df57d21db20da04d7fa30298dd4488ba3a2b47ca3a489c74750e0f1e7df1b9b7",
             "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"),
        ):
            with self.subTest(query=query):
                sig = self.sign("GET", "", query, headers)
                self.assertEqual(sig.canonical_request, "\n".join([
                    "GET", "/", canonical, f"host:{AWS_HOST}", f"x-amz-content-sha256:{EMPTY}",
                    f"x-amz-date:{AWS_DATE}", "", "host;x-amz-content-sha256;x-amz-date", EMPTY]))
                self.assertEqual(sig.string_to_sign.rsplit("\n", 1)[1], request_hash)
                self.assertEqual(sig.signature, signature)

    def test_canonical_query_order_and_encoding(self):
        # 文档说明里的例子：按编码后的名字排序
        self.assertEqual(upload_r2.canonical_query("prefix=somePrefix&marker=someMarker&max-keys=20"),
                         "marker=someMarker&max-keys=20&prefix=somePrefix")
        self.assertEqual(upload_r2.canonical_query("b=x%20y&a=1+2"), "a=1%2B2&b=x%20y")
        self.assertEqual(upload_r2.canonical_query(""), "")

    def test_uri_encode(self):
        encode = upload_r2.uri_encode
        self.assertEqual(encode("releases/ACC-1.0.1.zip", keep_slash=True), "releases/ACC-1.0.1.zip")
        self.assertEqual(encode("a b/c$d~e_.-", keep_slash=True), "a%20b/c%24d~e_.-")
        self.assertEqual(encode("a/b"), "a%2Fb")
        self.assertEqual(encode("说明 1+1=2"), "%E8%AF%B4%E6%98%8E%201%2B1%3D2")

    def test_put_request(self):
        """上传请求：路径式地址 <endpoint>/acc-update/<key>；Host、x-amz-date、x-amz-content-sha256（正文真实的
        SHA-256）、Content-Type、Cache-Control 都签上，签的就是发出去的这几个头；区域 auto、服务 s3。"""
        token = upload_r2.Token(FAKE_KEY_ID, FAKE_SECRET, R2_ENDPOINT)
        body = "改动说明\r\n".encode()
        sha = hashlib.sha256(body).hexdigest()
        request = upload_r2.put_request(R2_ENDPOINT, token, "notes/1.0.1.txt", body, len(body), sha,
                                        {"Content-Type": upload_r2.NOTES_TYPE, "Cache-Control": upload_r2.CACHE_SHORT},
                                        AMZ_DATE)
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, f"{R2_ENDPOINT}/acc-update/notes/1.0.1.txt")
        self.assertEqual(request.data, body)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(
            (headers["host"], headers["x-amz-date"], headers["x-amz-content-sha256"], headers["content-length"]),
            (f"{ACCOUNT}.r2.cloudflarestorage.com", AMZ_DATE, sha, str(len(body))))
        found = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=(.+)/20300102/auto/s3/aws4_request, "
                             r"SignedHeaders=(\S+), Signature=([0-9a-f]{64})", headers["authorization"])
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), FAKE_KEY_ID)
        self.assertEqual(found.group(2), "cache-control;content-type;host;x-amz-content-sha256;x-amz-date")
        again = upload_r2.sign("PUT", "/acc-update/notes/1.0.1.txt", "",
                               {name: headers[name] for name in found.group(2).split(";")}, sha,
                               access_key_id=FAKE_KEY_ID, secret=FAKE_SECRET, amz_date=AMZ_DATE)
        self.assertEqual(again.signature, found.group(3))
        self.assertNotIn(FAKE_SECRET, repr(request.header_items()))


class TokenFileTest(unittest.TestCase):
    """令牌文件：容忍 CRLF、空行、首尾空白、BOM；出错时只说缺哪一项、第几行，不带任何值。"""

    def path(self) -> Path:
        return sandbox.temp_dir(self) / "token.txt"

    def assert_error(self, path: Path, message: str) -> None:
        with self.assertRaises(upload_r2.UploadError) as caught:
            upload_r2.read_token(path)
        self.assertEqual(str(caught.exception), message)

    def test_crlf_blank_lines_spaces_bom(self):
        path = write_token(self.path(), "", f"  access_key_id = {FAKE_KEY_ID}  ", "",
                           f"\tsecret_access_key={FAKE_SECRET}", f"endpoint={R2_ENDPOINT} \t", "", "", bom=True)
        token = upload_r2.read_token(path)
        self.assertEqual((token.access_key_id, token.secret_access_key, token.endpoint),
                         (FAKE_KEY_ID, FAKE_SECRET, R2_ENDPOINT))

    def test_lf_comments_and_other_names(self):
        path = self.path()
        path.write_text(f"# R2 的令牌\nENDPOINT={R2_ENDPOINT}\naccount_id=x\nsecret_access_key=a=b=\n"
                        f"access_key_id={FAKE_KEY_ID}\n", encoding="utf-8")
        token = upload_r2.read_token(path)
        self.assertEqual((token.access_key_id, token.secret_access_key, token.endpoint),
                         (FAKE_KEY_ID, "a=b=", R2_ENDPOINT))

    def test_missing_items(self):
        for lines, missing in (
            ((f"access_key_id={FAKE_KEY_ID}", f"secret_access_key={FAKE_SECRET}"), "endpoint"),
            ((f"endpoint={R2_ENDPOINT}",), "access_key_id、secret_access_key"),
            ((f"access_key_id={FAKE_KEY_ID}", "secret_access_key=  ", f"endpoint={R2_ENDPOINT}"), "secret_access_key"),
            (("",), "access_key_id、secret_access_key、endpoint"),
        ):
            with self.subTest(missing=missing):
                self.assert_error(write_token(self.path(), *lines), f"令牌文件缺 {missing}")

    def test_bad_lines(self):
        self.assert_error(write_token(self.path(), f"access_key_id={FAKE_KEY_ID}", FAKE_SECRET,
                                      f"endpoint={R2_ENDPOINT}"), "令牌文件第 2 行不是「名字=值」的写法")
        self.assert_error(write_token(self.path(), f"access_key_id={FAKE_KEY_ID}", f"=={FAKE_SECRET}"),
                          "令牌文件第 2 行不是「名字=值」的写法")
        self.assert_error(write_token(self.path(), f"access_key_id={FAKE_KEY_ID}", f"access_key_id={FAKE_SECRET}"),
                          "令牌文件里 access_key_id 写了不止一次")
        path = self.path()
        path.write_bytes(f"access_key_id={FAKE_KEY_ID}".encode("utf-16"))
        self.assert_error(path, "令牌文件不是 UTF-8 文本")
        missing = self.path().with_name("没有这个文件.txt")
        self.assert_error(missing, f"找不到令牌文件：{missing}")

    def test_repr_hides_values(self):
        token = upload_r2.Token(FAKE_KEY_ID, FAKE_SECRET, R2_ENDPOINT)
        for text in (repr(token), str(token)):
            for value in (FAKE_KEY_ID, FAKE_SECRET, ACCOUNT):
                self.assertNotIn(value, text)

    def test_endpoint_rules(self):
        def base(endpoint):
            return upload_r2.endpoint_base(upload_r2.Token(FAKE_KEY_ID, FAKE_SECRET, endpoint))

        self.assertEqual(base(R2_ENDPOINT), R2_ENDPOINT)
        self.assertEqual(base(R2_ENDPOINT + "/"), R2_ENDPOINT)
        eu = f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"  # 按地区划分的存储桶的地址
        self.assertEqual(base(eu), eu)
        self.assertEqual(base("http://127.0.0.2:8080"), "http://127.0.0.2:8080")  # 本机的假服务器
        for endpoint in (f"http://{ACCOUNT}.r2.cloudflarestorage.com", f"https://{ACCOUNT}.example.com",
                         "https://r2.cloudflarestorage.com", f"{R2_ENDPOINT}/acc-update", f"{R2_ENDPOINT}?x=1",
                         f"https://user:pw@{ACCOUNT}.r2.cloudflarestorage.com", f"{R2_ENDPOINT}:port",
                         f"{ACCOUNT}.r2.cloudflarestorage.com", "http://example.com"):
            with self.subTest(endpoint=endpoint), self.assertRaises(upload_r2.UploadError) as caught:
                base(endpoint)
            self.assertNotIn(ACCOUNT, str(caught.exception))
            self.assertNotIn(endpoint, str(caught.exception))


class RedactTest(unittest.TestCase):
    def test_values_replaced(self):
        redact = upload_r2.Redactor()
        self.assertEqual(redact(f"还没有令牌 {FAKE_SECRET}"), f"还没有令牌 {FAKE_SECRET}")
        redact.add(upload_r2.Token(FAKE_KEY_ID, FAKE_SECRET, R2_ENDPOINT + "/"))
        text = redact(f"a {FAKE_SECRET} b {FAKE_KEY_ID.lower()} c {R2_ENDPOINT.upper()}/acc-update/x "
                      f"d https://{ACCOUNT}.R2.cloudflarestorage.com:443 e {ACCOUNT} f")
        self.assertEqual(text, "a <令牌已隐去> b <令牌已隐去> c <令牌已隐去>/acc-update/x d <令牌已隐去>:443 e <令牌已隐去> f")
        public = "https://dl.astral-chip-compass.com/latest.json"
        self.assertEqual(redact(public), public)


class RedirectTest(unittest.TestCase):
    """重定向不从 https 降级成明文 http（只调重定向的判断，不真的发请求）。"""

    def test_no_downgrade(self):
        handler = upload_r2._NoDowngrade()
        request = urllib.request.Request("https://api.github.com/repos/x/y/releases/latest")
        fp = io.BytesIO(b"")
        with self.assertRaises(urllib.error.URLError) as caught:
            handler.redirect_request(request, fp, 302, "Found", {}, "http://example.com/latest")
        self.assertEqual(caught.exception.reason, "重定向到了不是 https 的地址")
        self.assertTrue(fp.closed)
        followed = handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "https://example.com/latest")
        self.assertEqual(followed.full_url, "https://example.com/latest")

    def test_signed_put_not_followed(self):
        """签了名的上传（PUT）遇到哪种重定向都不跟，连 https 的也不跟：Authorization 不会被带到别的主机。"""
        handler = upload_r2._NoDowngrade()
        request = urllib.request.Request(f"{R2_ENDPOINT}/acc-update/latest.json", data=b"{}", method="PUT",
                                         headers={"Authorization": "AWS4-HMAC-SHA256 Credential=x"})
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as caught:
                handler.redirect_request(request, io.BytesIO(b""), code, "Moved", {}, "https://example.com/elsewhere")
            caught.exception.close()


class _Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # 不反查主机名（HTTPServer.server_bind 里的 socket.getfqdn）：127.0.0.2 反查不到，要等好几秒
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FakeCloud:
    """本机的假服务器：
    - R2 的上传接口（127.0.0.2）：PUT /acc-update/<key>，核对签名（照收到的请求头重算）、x-amz-content-sha256 是正文的
      SHA-256，记下正文和请求头；fail_put 里的 key 回给定的状态和正文；
      redirect_put 里的 key 回 307，指到 Location 给的地址；
    - 公开地址（127.0.0.1 的 /pub/<key>）：回传上去的东西，tamper 里的 key 回别的内容；带 Range 头（bytes=起-止）时按
      range_mode 回：「206」照规矩回那一段，「ignore」不理 Range、回 200 和整个文件，「bad-total」Content-Range 里的总长
      多 1，「long」正文比要的多 100 字节，「short」正文只回 1000 字节；收到的 Range 记进 ranges（key, Range 头）；
    - GitHub 的接口（127.0.0.1 的 /gh/latest）：回 github 里的状态和正文，记下请求头。
    读的请求（GET）带着签名（Authorization、x-amz-* 头）时记进 problems：读公开地址、GitHub 不该带令牌的任何东西。
    R2 放在 127.0.0.2：令牌里的 endpoint 连同它的主机名要换成占位，和公开地址不在同一个主机上，输出里的公开地址才不会被
    一起换掉（真用时两边本来就是不同的主机）。log 按收到的先后记（方法, 路径）。"""

    def __init__(self, case: unittest.TestCase):
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.log: list[tuple[str, str]] = []
        self.problems: list[str] = []  # 上传请求哪里不对（签名、请求头）
        self.github: tuple[int, bytes] = (404, b"{}")
        self.github_headers: list[dict[str, str]] = []
        self.tamper: dict[str, bytes] = {}
        self.fail_put: dict[str, tuple[int, bytes]] = {}
        self.redirect_put: dict[str, str] = {}
        self.range_mode = "206"
        self.ranges: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.r2 = self._serve("127.0.0.2", "r2")
        self.pub = self._serve("127.0.0.1", "pub")
        case.addCleanup(self.close)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.2:{self.r2.server_address[1]}"

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.pub.server_address[1]}{path}"

    def close(self) -> None:
        for server in (self.r2, self.pub):
            server.shutdown()
            server.server_close()

    def _serve(self, host: str, role: str) -> http.server.ThreadingHTTPServer:
        server = _Server((host, 0), self._handler(role))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return server

    def check(self, path: str, headers: dict[str, str], body: bytes) -> str | None:
        """上传请求哪里不对；都对时返回 None。签名照收到的请求头重算：签的头和发出去的不一样时对不上。"""
        found = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/auto/s3/aws4_request, "
                             r"SignedHeaders=([a-z0-9;-]+), Signature=([0-9a-f]{64})", headers.get("authorization", ""))
        if not found:
            return "没有签名，或者 Authorization 头的写法不对"
        key_id, day, signed, signature = found.groups()
        if key_id != FAKE_KEY_ID:
            return "Credential 里不是令牌的 access_key_id"
        if headers.get("x-amz-content-sha256") != hashlib.sha256(body).hexdigest():
            return "x-amz-content-sha256 不是正文的 SHA-256"
        names = signed.split(";")
        missing = [n for n in ("cache-control", "content-type", "host", "x-amz-content-sha256", "x-amz-date")
                   if n not in names]
        if missing:
            return f"没签 {missing}"
        if headers.get("x-amz-date", "")[:8] != day:
            return "x-amz-date 和 Credential 里的日期不一样"
        expected = upload_r2.sign("PUT", path, "", {n: headers.get(n, "") for n in names},
                                  headers["x-amz-content-sha256"], access_key_id=FAKE_KEY_ID, secret=FAKE_SECRET,
                                  amz_date=headers["x-amz-date"])
        return None if expected.signature == signature else "签名对不上"

    def _handler(cloud, role: str):  # noqa: N805  cloud 是 FakeCloud 自己，self 留给请求处理器
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code: int, body: bytes = b"", headers: dict | None = None) -> None:
                self.send_response(code)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:  # 对方读够了就断开（按段取时回了整个文件）：不算服务器出错
                    pass

            def ranged(self, key: str, data: bytes) -> None:
                """带 Range 头的读：按 cloud.range_mode 回（见 FakeCloud 的说明）。"""
                header = self.headers.get("Range", "")
                with cloud.lock:
                    cloud.ranges.append((key, header))
                found = re.fullmatch(r"bytes=(\d+)-(\d+)", header)
                if cloud.range_mode == "ignore" or not found:
                    return self.reply(200, data)
                start, end = int(found.group(1)), min(int(found.group(2)), len(data) - 1)
                part, total = data[start:end + 1], len(data)
                if cloud.range_mode == "bad-total":
                    total += 1
                elif cloud.range_mode == "long":
                    part = data[start:end + 101]
                elif cloud.range_mode == "short":
                    part = part[:1000]
                self.reply(206, part, {"Content-Range": f"bytes {start}-{end}/{total}"})

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {name.lower(): value for name, value in self.headers.items()}
                with cloud.lock:
                    cloud.log.append(("PUT", self.path))
                prefix = f"/{upload_r2.BUCKET}/"
                if role != "r2" or not self.path.startswith(prefix):
                    cloud.problems.append(f"不该收到的上传：{role} {self.path}")
                    return self.reply(404)
                key = urllib.parse.unquote(self.path[len(prefix):])
                problem = cloud.check(self.path, headers, body)
                if problem:
                    cloud.problems.append(f"{key}：{problem}")
                    return self.reply(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>")
                if key in cloud.fail_put:
                    return self.reply(*cloud.fail_put[key])
                if key in cloud.redirect_put:
                    return self.reply(307, headers={"Location": cloud.redirect_put[key]})
                cloud.objects[key] = (body, headers)
                self.reply(200)

            def do_GET(self):
                with cloud.lock:
                    cloud.log.append(("GET", self.path))
                signed = [name for name in self.headers if name.lower() == "authorization"
                          or name.lower().startswith("x-amz-")]
                if signed:
                    cloud.problems.append(f"读的请求带着签名：{role} {self.path} {signed}")
                if role == "pub" and self.path == "/gh/latest":
                    cloud.github_headers.append({name.lower(): value for name, value in self.headers.items()})
                    code, body = cloud.github
                    return self.reply(code, body, {"Content-Type": "application/json"})
                if role == "pub" and self.path.startswith("/pub/"):
                    key = urllib.parse.unquote(self.path[len("/pub/"):])
                    data = cloud.tamper.get(key, cloud.objects[key][0] if key in cloud.objects else None)
                    if data is not None:
                        return self.ranged(key, data) if "Range" in self.headers else self.reply(200, data)
                self.reply(404)

        return Handler


class UploadFlowTest(unittest.TestCase):
    """经 main() 跑一遍（假服务器见 FakeCloud）：版本号固定成 VERSION，资料同步日期、请求时刻固定。"""

    def setUp(self):
        self.folder = sandbox.temp_dir(self)
        self.cloud = FakeCloud(self)
        self.zip = make_zip(self.folder / f"ACC-{VERSION}.zip")
        self.zip_bytes = self.zip.read_bytes()
        self.size, self.sha = len(self.zip_bytes), hashlib.sha256(self.zip_bytes).hexdigest()
        # 文件清单：打包脚本生成在压缩包旁边的那一份（同一个函数生成）
        self.files = self.folder / f"ACC-{VERSION}.files.json"
        self.files_bytes = update_files.build_manifest(self.zip, VERSION)
        self.files.write_bytes(self.files_bytes)
        self.files_size, self.files_sha = len(self.files_bytes), hashlib.sha256(self.files_bytes).hexdigest()
        self.token = write_token(self.folder / "token.txt", f"access_key_id={FAKE_KEY_ID}",
                                 f"secret_access_key={FAKE_SECRET}", f"endpoint={self.cloud.endpoint}")
        self.notes = self.folder / "notes.txt"
        self.notes.write_bytes(b"\xef\xbb\xbf" + NOTES_TEXT.encode("utf-8"))  # 带 BOM：传上去的不带
        self.cloud.github = (200, self.github(VERSION))
        self.outside: list[str] = []
        self.addCleanup(lambda: self.assertEqual(self.outside, [], "脚本往本机以外的地址发了请求"))
        for patcher in (mock.patch.object(upload_r2, "VERSION", VERSION),
                        mock.patch.object(upload_r2, "data_date", return_value=SYNCED),
                        mock.patch.object(upload_r2, "amz_now", return_value=AMZ_DATE),
                        mock.patch.object(upload_r2, "_open", side_effect=self.local_only),
                        mock.patch.dict(os.environ)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(upload_r2.TOKEN_ENV, None)  # 不读本机设的令牌文件

    def github(self, version: str = VERSION, size: int | None = None, sha: str | None = "", files=()) -> bytes:
        """GitHub 接口回的这一版：不写的项照本地的压缩包和文件清单（sha 写 None 时压缩包的附件没有 digest；files 写 None
        时没有清单的附件，写（大小, SHA-256）时用给的）。"""
        return github_release(version, self.size if size is None else size, self.sha if sha == "" else sha,
                              (self.files_size, self.files_sha) if files == () else files)

    def local_only(self, request, timeout):
        """只许发往假服务器（127.0.0.1、127.0.0.2）。"""
        if urllib.parse.urlsplit(request.full_url).hostname not in ("127.0.0.1", "127.0.0.2"):
            self.outside.append(request.full_url)
            raise AssertionError("测试里不许往本机以外的地址发请求")
        return REAL_OPEN(request, timeout)

    def upload(self, *extra: str, notes: bool = True, token: bool = True, zip_arg: bool = True) -> tuple[int, str, str]:
        """跑 main()，返回（退出码, 标准输出, 标准错误）。每次都核对输出里没有令牌的值。"""
        argv = ["--public-base", self.cloud.url("/pub/"), "--github-api", self.cloud.url("/gh/latest")]
        if zip_arg:
            argv += ["--zip", str(self.zip)]
        if token:
            argv += ["--token-file", str(self.token)]
        if notes:
            argv += ["--notes", str(self.notes)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = upload_r2.main([*argv, *extra])
        self.assert_no_token(out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def assert_no_token(self, text: str) -> None:
        for value in (FAKE_KEY_ID, FAKE_SECRET, self.cloud.endpoint, "127.0.0.2"):
            self.assertNotIn(value.lower(), text.lower())

    def puts(self) -> list[str]:
        return [path for method, path in self.cloud.log if method == "PUT"]

    def test_full_upload(self):
        """顺序：GitHub → 包 → 下回来核对 → 文件清单 → 读回核对 → ACC-latest.zip → notes → latest.json → 读回核对 →
        按段取包的前 1 KB；各文件的请求头（清单的缓存同发行包）、签名、正文；latest.json 的内容（含清单的三项）；摘要。
        大文件边传边报进度（这里把门槛降到 1 字节，经真的 http.client 发出去）。"""
        with mock.patch.object(upload_r2, "PROGRESS_MIN", 1):
            code, out, err = self.upload()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.cloud.log, [
            ("GET", "/gh/latest"),
            ("PUT", "/acc-update/releases/ACC-1.0.1.zip"),
            ("GET", "/pub/releases/ACC-1.0.1.zip"),
            ("PUT", "/acc-update/releases/ACC-1.0.1.files.json"),
            ("GET", "/pub/releases/ACC-1.0.1.files.json"),
            ("PUT", "/acc-update/ACC-latest.zip"),
            ("PUT", "/acc-update/notes/1.0.1.txt"),
            ("PUT", "/acc-update/latest.json"),
            ("GET", "/pub/latest.json"),
            ("GET", "/pub/releases/ACC-1.0.1.zip"),
        ])
        # 只有最后一次读发行包按段取，前面下回来核对的那次读整个
        self.assertEqual(self.cloud.ranges, [("releases/ACC-1.0.1.zip", "bytes=0-1023")])
        self.assertEqual(self.cloud.problems, [])
        objects = self.cloud.objects
        expected_headers = {
            "releases/ACC-1.0.1.zip": ("application/zip", "public, max-age=31536000, immutable", None),
            "releases/ACC-1.0.1.files.json": ("application/json", "public, max-age=31536000, immutable", None),
            "ACC-latest.zip": ("application/zip", "public, max-age=300", 'attachment; filename="ACC-1.0.1.zip"'),
            "notes/1.0.1.txt": ("text/plain; charset=utf-8", "public, max-age=300", None),
            "latest.json": ("application/json", "no-cache", None),
        }
        self.assertEqual(sorted(objects), sorted(expected_headers))
        for key, (content_type, cache, disposition) in expected_headers.items():
            with self.subTest(key=key):
                headers = objects[key][1]
                self.assertEqual((headers.get("content-type"), headers.get("cache-control"),
                                  headers.get("content-disposition")), (content_type, cache, disposition))
                self.assertTrue(headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential="))
                self.assertEqual(headers["x-amz-date"], AMZ_DATE)
                self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(objects[key][0]).hexdigest())
        self.assertEqual(objects["releases/ACC-1.0.1.zip"][0], self.zip_bytes)
        self.assertEqual(objects["releases/ACC-1.0.1.files.json"][0], self.files_bytes)  # 清单照原样传
        self.assertEqual(objects["ACC-latest.zip"][0], self.zip_bytes)
        self.assertEqual(objects["notes/1.0.1.txt"][0], NOTES_TEXT.encode("utf-8"))  # 去掉了 BOM，换行照原样
        manifest = json.loads(objects["latest.json"][0])
        self.assertEqual(manifest, {
            "format": 1, "version": "1.0.1", "zip": "releases/ACC-1.0.1.zip", "size": self.size, "sha256": self.sha,
            "files": "releases/ACC-1.0.1.files.json", "files_size": self.files_size, "files_sha256": self.files_sha,
            "page": "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1",
            "notes": self.cloud.url("/pub/notes/1.0.1.txt"), "data_date": SYNCED, "auto": True})
        self.assertEqual(list(manifest), ["format", "version", "zip", "size", "sha256", "files", "files_size",
                                          "files_sha256", "page", "notes", "data_date", "auto"])
        # 三项说的就是传上去的那份清单，程序照它认得出这一版的清单
        files = objects[manifest["files"]][0]
        self.assertEqual((len(files), hashlib.sha256(files).hexdigest()),
                         (manifest["files_size"], manifest["files_sha256"]))
        update_files.parse_manifest(files, version=VERSION, zip_size=manifest["size"], zip_sha256=manifest["sha256"])
        github = self.cloud.github_headers[0]
        self.assertEqual((github.get("accept"), github.get("x-github-api-version")),
                         ("application/vnd.github+json", "2022-11-28"))
        self.assertTrue(github.get("user-agent", "").startswith("AstralChipCompass/"))
        self.assertIn("已传 100%", out)
        for step in range(1, 11):
            self.assertIn(f"第 {step}/10 步", out)
        for key in expected_headers:
            self.assertIn(self.cloud.url(f"/pub/{key}"), out)
        self.assertIn(f"SHA-256 {self.sha}", out)
        self.assertIn(f"SHA-256 {self.files_sha}", out)
        self.assertIn(f"通过：回 206，Content-Range: bytes 0-1023/{self.size}，正文 1024 字节。", out)
        self.assertNotIn("警告", out)
        self.assertIn("全部完成", out.splitlines()[-1])

    def test_without_notes_no_auto_no_date(self):
        """不带 --notes：不传 notes、latest.json 里没有 notes；--no-auto：auto 是 false；没有资料同步日期：不写 data_date。
        文件清单的三项照写。"""
        with mock.patch.object(upload_r2, "data_date", return_value=None):
            code, out, err = self.upload("--no-auto", notes=False)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.puts(), ["/acc-update/releases/ACC-1.0.1.zip", "/acc-update/releases/ACC-1.0.1.files.json",
                                       "/acc-update/ACC-latest.zip", "/acc-update/latest.json"])
        self.assertEqual(json.loads(self.cloud.objects["latest.json"][0]), {
            "format": 1, "version": "1.0.1", "zip": "releases/ACC-1.0.1.zip", "size": self.size, "sha256": self.sha,
            "files": "releases/ACC-1.0.1.files.json", "files_size": self.files_size, "files_sha256": self.files_sha,
            "page": "https://github.com/s1nan-dev/astral-chip-compass/releases/tag/v1.0.1", "auto": False})
        self.assertIn("没带 --notes", out)

    def test_range_probe_only_warns(self):
        """最后一步按段取发行包：公开地址不理 Range（回 200 和整个文件）、Content-Range 的总长不对、正文比要的长或短时，
        打一行警告、摘要里再提一句，不算失败：前面的都照常传完，退出码 0，最后一行照样是「全部完成」。回 206、各项都对时
        不警告（见 test_full_upload）。"""
        cases = {
            "ignore": "回的是 200，不是 206（不支持按段取，回的是整个文件）",
            "bad-total": f"Content-Range 是 'bytes 0-1023/{self.size + 1}'，应是 'bytes 0-1023/{self.size}'",
            "long": "正文比要的长，应是 1024 字节",
            "short": "正文只有 1000 字节，应是 1024 字节",
        }
        for mode, message in cases.items():
            with self.subTest(mode):
                self.cloud.log.clear()
                self.cloud.objects.clear()
                self.cloud.ranges.clear()
                self.cloud.range_mode = mode
                code, out, err = self.upload()
                self.assertEqual((code, err), (0, ""))
                self.assertIn(f"警告：公开地址按段取发行包没通过：{message}。程序会自动改下整包", out)
                self.assertIn(f"注意：公开地址按段取发行包没通过（{message}）", out)
                self.assertIn("全部完成", out.splitlines()[-1])
                self.assertEqual(self.cloud.ranges, [("releases/ACC-1.0.1.zip", "bytes=0-1023")])
                self.assertEqual(len(self.puts()), 5)
                self.assertIn("latest.json", self.cloud.objects)

    def test_github_mismatch_stops(self):
        """GitHub 上的版本、大小、SHA-256 对不上，没有 digest、没有这个附件、接口回 404、回的不是 JSON：停在第 1 步，
        一个上传请求都没发。文件清单的附件也一样：不在、没有 digest、大小或 SHA-256 和本地的清单不一样都停。"""
        files = "ACC-1.0.1.files.json"
        cases = {
            "不是 v1.0.1": (200, self.github("1.0.0")),
            "大小对不上：GitHub 上的 ACC-1.0.1.zip": (200, self.github(size=self.size + 1)),
            "SHA-256 对不上：GitHub 上的 ACC-1.0.1.zip": (200, self.github(sha="0" * 64)),
            "GitHub 上的 ACC-1.0.1.zip 没有 SHA-256（digest）": (200, self.github(sha=None)),
            "附件里没有 ACC-1.0.1.zip": (200, json.dumps({"tag_name": "v1.0.1", "assets": []}).encode()),
            "GitHub 上没有正式版": (404, b'{"message": "Not Found"}'),
            "限流": (403, b'{"message": "API rate limit exceeded"}'),
            "不是发布的信息": (200, b"<html></html>"),
            f"GitHub 上 v1.0.1 的附件里没有 {files}（文件清单要和压缩包一起挂在发布页面上）":
                (200, self.github(files=None)),
            f"GitHub 上的 {files} 没有 SHA-256（digest）": (200, self.github(files=(self.files_size, None))),
            f"大小对不上：GitHub 上的 {files} 是 {self.files_size + 1} 字节，本地的是 {self.files_size} 字节":
                (200, self.github(files=(self.files_size + 1, self.files_sha))),
            f"SHA-256 对不上：GitHub 上的 {files} 是 {'0' * 64}，本地的是 {self.files_sha}":
                (200, self.github(files=(self.files_size, "0" * 64))),
        }
        for message, reply in cases.items():
            with self.subTest(message):
                self.cloud.log.clear()
                self.cloud.github = reply
                code, out, err = self.upload()
                self.assertEqual(code, 1)
                self.assertEqual(self.cloud.log, [("GET", "/gh/latest")])
                self.assertIn("停在第 1/10 步（核对 GitHub）", err)
                self.assertIn(message, err)
                self.assertIn("R2 上什么都没动。", err)
        self.assertEqual(self.cloud.objects, {})

    def test_skip_github_check(self):
        """--skip-github-check：打一行警告（连文件清单的附件也没核对）、不问 GitHub，照传。"""
        self.cloud.github = (200, github_release("0.9.9", 1, "0" * 64))
        code, out, err = self.upload("--skip-github-check")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("警告：没有核对 GitHub（--skip-github-check）", out)
        self.assertIn("文件清单的附件不在、或者 SHA-256 和本地的清单不一样时，程序只下整包", out)
        self.assertNotIn(("GET", "/gh/latest"), self.cloud.log)
        self.assertEqual(len(self.puts()), 5)
        self.assertIn("latest.json", self.cloud.objects)

    def test_public_download_mismatch_stops(self):
        """公开地址下回来的不对（例如还缓存着以前的同名文件）：停在第 3 步，文件清单、ACC-latest.zip、latest.json 都没传。"""
        for tampered in (b"stale" * 1000, self.zip_bytes + b"x"):
            with self.subTest(size=len(tampered)):
                self.cloud.log.clear()
                self.cloud.objects.clear()
                self.cloud.tamper["releases/ACC-1.0.1.zip"] = tampered
                code, out, err = self.upload()
                self.assertEqual(code, 2)
                self.assertEqual(self.cloud.log, [("GET", "/gh/latest"), ("PUT", "/acc-update/releases/ACC-1.0.1.zip"),
                                                  ("GET", "/pub/releases/ACC-1.0.1.zip")])
                self.assertEqual(sorted(self.cloud.objects), ["releases/ACC-1.0.1.zip"])
                self.assertIn("停在第 3/10 步（从公开地址下回来核对）：公开地址下回来的和传上去的不一样", err)
                self.assertIn("已经传上去的：releases/ACC-1.0.1.zip", err)
                self.assertIn("还没传的：releases/ACC-1.0.1.files.json、ACC-latest.zip（R2 上的还是原来那一份，手动下载拿到的"
                              "不是这一版）", err)
                self.assertIn("latest.json（R2 上的还是原来那一份，程序查更新看不到这一版）", err)
                self.assertIn("没有自动回滚", err)

    def test_files_download_mismatch_stops(self):
        """公开地址读回的文件清单不对：停在第 5 步，ACC-latest.zip、latest.json 都没传。"""
        self.cloud.tamper["releases/ACC-1.0.1.files.json"] = self.files_bytes + b"\n"
        code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertEqual(self.puts(), ["/acc-update/releases/ACC-1.0.1.zip", "/acc-update/releases/ACC-1.0.1.files.json"])
        self.assertEqual(self.cloud.log[-1], ("GET", "/pub/releases/ACC-1.0.1.files.json"))
        self.assertIn("停在第 5/10 步（从公开地址读回文件清单核对）：公开地址下回来的和传上去的不一样", err)
        self.assertIn("已经传上去的：releases/ACC-1.0.1.zip、releases/ACC-1.0.1.files.json", err)
        self.assertIn("还没传的：ACC-latest.zip", err)
        self.assertNotIn("latest.json", self.cloud.objects)

    def test_readback_mismatch(self):
        self.cloud.tamper["latest.json"] = b'{"format": 1, "version": "1.0.0"}'
        code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertIn("停在第 9/10 步（从公开地址读回 latest.json 核对）：公开地址读回的 latest.json 和传上去的不一样", err)
        self.assertIn("已经传上去的：releases/ACC-1.0.1.zip、releases/ACC-1.0.1.files.json、ACC-latest.zip、"
                      "notes/1.0.1.txt、latest.json", err)
        self.assertNotIn("还没传的", err)
        self.assertEqual(self.cloud.ranges, [])  # 停下了：不再按段取

    def test_r2_error_midway(self):
        """R2 在第 6 步回 403，出错说明里带着令牌的 access_key_id 和 endpoint：只报 Code 和 Message，令牌的值换成占位；
        写明哪些传上去了、哪个没传成、哪些还没传。"""
        body = (f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>key {FAKE_KEY_ID} '
                f"at {self.cloud.endpoint} denied</Message><AWSAccessKeyId>{FAKE_KEY_ID}</AWSAccessKeyId>"
                f"<StringToSign>AWS4-HMAC-SHA256</StringToSign></Error>").encode()
        self.cloud.fail_put["ACC-latest.zip"] = (403, body)
        code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertIn("停在第 6/10 步（传 ACC-latest.zip）：传 ACC-latest.zip：R2 回 HTTP 403"
                      "（AccessDenied：key <令牌已隐去> at <令牌已隐去> denied）", err)
        self.assertIn("已经传上去的：releases/ACC-1.0.1.zip、releases/ACC-1.0.1.files.json", err)
        self.assertIn("没传成的：ACC-latest.zip", err)
        self.assertIn("还没传的：notes/1.0.1.txt、latest.json", err)
        self.assertNotIn("StringToSign", err)
        self.assertEqual(self.puts()[-1], "/acc-update/ACC-latest.zip")
        self.assertNotIn("latest.json", self.cloud.objects)

    def test_put_redirect_not_followed(self):
        """R2 那边回重定向、指到别的主机（这里是公开地址那一台）：不跟，签了名的请求只发到令牌里的 endpoint 一次，
        停在第 2 步、报 HTTP 307。"""
        self.cloud.redirect_put["releases/ACC-1.0.1.zip"] = self.cloud.url("/pub/elsewhere")
        code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertEqual(self.cloud.log, [("GET", "/gh/latest"), ("PUT", "/acc-update/releases/ACC-1.0.1.zip")])
        self.assertEqual(self.cloud.problems, [])
        self.assertIn("停在第 2/10 步（传发行包）：传 releases/ACC-1.0.1.zip：R2 回 HTTP 307", err)
        self.assertEqual(self.cloud.objects, {})

    def test_size_limits(self):
        """压缩包的大小上下限和程序那边认的一样（1 MiB～1 GiB），正好在上下限上的可以，超出一个字节就停。"""
        self.assertEqual((upload_r2.MIN_SIZE, upload_r2.MAX_SIZE), (1 << 20, 1 << 30))
        for name, limits, code in (("正好是上限", (1, self.size), 0), ("正好是下限", (self.size, 1 << 30), 0),
                                   ("比上限大", (1, self.size - 1), 1), ("比下限小", (self.size + 1, 1 << 30), 1)):
            with self.subTest(name), mock.patch.object(upload_r2, "MIN_SIZE", limits[0]), \
                    mock.patch.object(upload_r2, "MAX_SIZE", limits[1]):
                got, out, err = self.upload("--dry-run", "--skip-github-check")
                self.assertEqual(got, code)
                if code:
                    self.assertIn(f"压缩包是 {self.size:,} 字节，不在 1 MiB～1 GiB", err)
        self.assertEqual(self.cloud.log, [])

    def test_unexpected_error_hides_token(self):
        """没想到的错（说明里带着令牌的三项）：不打调用栈，令牌的值换成占位，照样写 R2 的状态。"""
        def boom(request, timeout):
            if request.get_method() == "PUT" and request.full_url.endswith("/latest.json"):
                raise RuntimeError(f"boom {FAKE_SECRET} {FAKE_KEY_ID.lower()} {self.cloud.endpoint}")
            return self.local_only(request, timeout)

        with mock.patch.object(upload_r2, "_open", side_effect=boom):
            code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertIn("出了没想到的错：RuntimeError: boom <令牌已隐去> <令牌已隐去> <令牌已隐去>", err)
        self.assertIn("没传成的：latest.json", err)
        self.assertNotIn("Traceback", out + err)

    def test_exception_text_hides_token(self):
        """UploadError 的说明在抛出来的时候就已经去掉了令牌的值（不是打印时才换）。"""
        body = f"<Error><Code>AccessDenied</Code><Message>{FAKE_KEY_ID} {self.cloud.endpoint}</Message></Error>"
        self.cloud.fail_put["ACC-latest.zip"] = (403, body.encode())
        run = upload_r2.Upload(upload_r2.parse_args(["--zip", str(self.zip), "--token-file", str(self.token),
                                                     "--public-base", self.cloud.url("/pub/")]))
        run.prepare()
        with self.assertRaises(upload_r2.UploadError) as caught:
            run.put(run.latest_zip_item)
        self.assertEqual(str(caught.exception), "传 ACC-latest.zip：R2 回 HTTP 403（AccessDenied：<令牌已隐去> <令牌已隐去>）")
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(run.attempted, "ACC-latest.zip")

    def test_interrupted(self):
        """传的时候按了 Ctrl+C：照样写 R2 的状态，退出码 2。"""
        def interrupt(request, timeout):
            if request.get_method() == "PUT":
                raise KeyboardInterrupt
            return self.local_only(request, timeout)

        with mock.patch.object(upload_r2, "_open", side_effect=interrupt):
            code, out, err = self.upload()
        self.assertEqual(code, 2)
        self.assertIn("停在第 2/10 步（传发行包）：按了 Ctrl+C，中断了", err)
        self.assertIn("没传成的：releases/ACC-1.0.1.zip", err)

    def test_dry_run_sends_no_upload(self):
        """--dry-run：核对 GitHub，列出要传的东西（含文件清单、最后的按段取）和 latest.json，一个上传请求都不发；再加
        --skip-github-check 时一个请求都不发。GitHub 对不上时试运行也停下。"""
        code, out, err = self.upload("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.cloud.log, [("GET", "/gh/latest")])
        self.assertIn("试运行", out.splitlines()[0])
        for text in ("releases/ACC-1.0.1.zip", "ACC-latest.zip", 'attachment; filename="ACC-1.0.1.zip"',
                     "notes/1.0.1.txt", "Cache-Control: public, max-age=31536000, immutable", '"version": "1.0.1"',
                     f'"sha256": "{self.sha}"', "令牌文件：三项都在（内容不显示）",
                     f"文件清单：{self.files}", "  4. 传 releases/ACC-1.0.1.files.json",
                     "Content-Type: application/json；Cache-Control: public, max-age=31536000, immutable",
                     "  5. 从公开地址读回文件清单", '"files": "releases/ACC-1.0.1.files.json"',
                     f'"files_size": {self.files_size}', f'"files_sha256": "{self.files_sha}"',
                     "  10. 从公开地址按段取发行包的前 1024 字节"):
            self.assertIn(text, out)
        self.assertIn("试运行结束：R2 上什么都没动", out)
        self.cloud.log.clear()
        code, out, err = self.upload("--dry-run", "--skip-github-check")
        self.assertEqual((code, self.cloud.log), (0, []))
        self.cloud.github = (200, self.github("1.0.0"))
        code, out, err = self.upload("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("试运行停在第 1/10 步", err)
        self.assertEqual(self.cloud.objects, {})

    def test_local_problems_send_nothing(self):
        """本地的东西不对（压缩包的名字、大小、内容，文件清单不在、和压缩包对不上，改动说明，地址不是 https）：一个请求都
        不发，退出码 1。"""
        other = self.folder / "other"
        wrong_name = shutil.copy(self.zip, self.folder / "ACC-0.0.1.zip")
        small = make_zip(other / "small" / f"ACC-{VERSION}.zip", payload=1000)
        not_zip = other / "notzip" / f"ACC-{VERSION}.zip"
        not_zip.parent.mkdir(parents=True)
        not_zip.write_bytes(os.urandom((1 << 20) + 10))
        no_launcher = make_zip(other / "layout" / f"ACC-{VERSION}.zip", names=("AstralChipCompass/app/ACC.exe",))
        # 旁边没有文件清单的压缩包；另一个包（内容不同）的清单
        lonely = other / "lonely" / f"ACC-{VERSION}.zip"
        lonely.parent.mkdir(parents=True)
        shutil.copy(self.zip, lonely)
        stale = other / "stale" / f"ACC-{VERSION}.files.json"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(update_files.build_manifest(make_zip(other / "stale" / f"ACC-{VERSION}.zip"), VERSION))
        bad_notes = other / "bad.txt"
        bad_notes.write_bytes("说明".encode("gbk"))
        empty_notes = other / "empty.txt"
        empty_notes.write_bytes(b"\xef\xbb\xbf \r\n")
        for extra, message in (
            (["--zip", str(wrong_name)], "压缩包的文件名要是 ACC-1.0.1.zip"),
            (["--zip", str(other / "none" / f"ACC-{VERSION}.zip")], "找不到压缩包"),
            (["--zip", str(small)], "不在 1 MiB～1 GiB（1,048,576～1,073,741,824 字节）之间"),
            (["--zip", str(not_zip)], "压缩包打不开"),
            (["--zip", str(no_launcher)], "压缩包里没有 AstralChipCompass/拾星助手.exe"),
            (["--zip", str(lonely)], f"找不到文件清单：{lonely.with_name('ACC-1.0.1.files.json')}。先用打包脚本"),
            (["--files-manifest", str(other / "none.files.json")], "找不到文件清单"),
            (["--files-manifest", str(stale)], "文件清单和压缩包对不上，程序会不认它、只下整包：清单写的整包"),
            (["--notes", str(bad_notes)], "改动说明不是 UTF-8 文本"),
            (["--notes", str(empty_notes)], "改动说明是空的"),
            (["--notes", str(other / "none.txt")], "找不到改动说明"),
            (["--public-base", "http://example.com/"], "公开地址要是 https 地址"),
            (["--github-api", "http://example.com/latest"], "GitHub 的接口要是 https 地址"),
        ):
            with self.subTest(message):
                code, out, err = self.upload(*extra)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("还没开始传就停下了："))
                self.assertIn(message, err)
                self.assertIn("R2 上什么都没动。", err)
        self.assertEqual(self.cloud.log, [])
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            upload_r2.main(["--没有这个参数"])
        self.assertEqual(caught.exception.code, 1)  # 参数写错按「R2 上什么都没动」算

    def test_token_problems_send_nothing(self):
        """没指定令牌文件、令牌缺项、endpoint 不是 R2 的 https 地址：一个请求都不发；没给 --token-file 时用环境变量。"""
        partial = write_token(self.folder / "partial.txt", f"access_key_id={FAKE_KEY_ID}",
                              f"secret_access_key={FAKE_SECRET}")
        elsewhere = write_token(self.folder / "elsewhere.txt", f"access_key_id={FAKE_KEY_ID}",
                                f"secret_access_key={FAKE_SECRET}", "endpoint=https://example.com")
        for extra, message in (
            ([], f"没有指定令牌文件：用 --token-file 给出，或者设环境变量 {upload_r2.TOKEN_ENV}"),
            (["--token-file", str(partial)], "令牌文件缺 endpoint"),
            (["--token-file", str(elsewhere)], "令牌文件里的 endpoint 不是「https://<账号>.r2.cloudflarestorage.com」"),
        ):
            with self.subTest(message):
                code, out, err = self.upload(*extra, token=False)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
        self.assertEqual(self.cloud.log, [])
        os.environ[upload_r2.TOKEN_ENV] = str(self.token)
        code, out, err = self.upload("--dry-run", "--skip-github-check", token=False)
        self.assertEqual((code, err), (0, ""))

    def test_default_zip(self):
        """不给 --zip 时用程序目录下的 dist\\ACC-<版本>.zip，文件清单用它旁边的 ACC-<版本>.files.json。"""
        app = sandbox.temp_dir(self)
        (app / "dist").mkdir()
        shutil.copy(self.zip, app / "dist" / self.zip.name)
        shutil.copy(self.files, app / "dist" / self.files.name)
        with mock.patch.object(paths, "app_dir", return_value=app):
            self.assertEqual(upload_r2.default_zip(), app / "dist" / "ACC-1.0.1.zip")
            code, out, err = self.upload("--dry-run", "--skip-github-check", zip_arg=False)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(str(app / "dist" / "ACC-1.0.1.zip"), out)
        self.assertIn(f"文件清单：{app / 'dist' / 'ACC-1.0.1.files.json'}", out)

    def test_files_manifest_elsewhere(self):
        """--files-manifest 给的清单不在压缩包旁边（文件名也不同）：照它核对、照它传。"""
        elsewhere = sandbox.temp_dir(self) / "清单.json"
        shutil.move(self.files, elsewhere)
        code, out, err = self.upload("--files-manifest", str(elsewhere))
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"文件清单：{elsewhere}", out)
        self.assertEqual(self.cloud.objects["releases/ACC-1.0.1.files.json"][0], self.files_bytes)


class AddressTest(unittest.TestCase):
    def test_same_as_program(self):
        """写死在脚本里的两个地址和程序查更新用的一样：公开地址就是程序的下载镜像（advisor/update.py 的 MIRROR_BASE），核对的
        GitHub 接口就是程序问的那一个（由源代码地址推出的 GITHUB_API）。程序只在源代码地址就是下载镜像上那个仓库
        （MIRROR_SOURCE）时才用镜像，脚本传的正是这个源代码地址的版本（latest.json 的 page 也按它写）。"""
        self.assertEqual(upload_r2.PUBLIC_BASE, update.MIRROR_BASE)
        self.assertEqual(upload_r2.GITHUB_API, update.GITHUB_API)
        self.assertEqual(upload_r2.SOURCE_URL, update.MIRROR_SOURCE)


if __name__ == "__main__":
    unittest.main()
