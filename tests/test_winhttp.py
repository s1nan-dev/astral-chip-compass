"""下载用的 WinHTTP 客户端（advisor/winhttp.py）：状态码、分块传输、重定向、连接中断、超时、停止、并行会话、长连接被
服务器关掉时重发、句柄都关掉、代理的写法、Windows 11 起建会话时开 TLS 1.2 和 1.3；边读边交的 stream（StreamTest）；按字节段
取时 stream 的 head、limit（RangeTest：206、回 200、Content-Range 对不上、回得比要的长、跳转以后 Range 头还在）。

代理的口径和 urllib 一致：用哪个代理按 urllib.request.getproxies()（环境变量优先，一个都没有时看注册表的手动代理）、例外
主机每个请求按 urllib.request.proxy_bypass、代理地址里的用户名密码发 Proxy-Authorization（Basic）、cookie 不收不带、
跳转过去的每一跳也带认证（回 407 时在同一个请求句柄上重设凭据重发一次）、winhttp.dll 只从 System32 加载。

不联网：本机 127.0.0.1 上自己起 http.server（WinHTTP 也连 http），只测明文；HTTPS 本机测不了，要联网实测（直连下载几张、
系统代理、环境变量代理、断网时的报错文字）。走代理的几条用本机的假代理，请求的主机名用本机的计算机名：走了代理时请求
送到假代理，没走代理时按本机名字直连本机一个没人听的端口，都不出本机。

和上层（advisor/assets.py）的配合也一并测：_KeepAlive 把 winhttp.Stopped 转成 assets._Stopped，download() 见到停止
标志很快收手、不留文件。
"""
from __future__ import annotations

import ast
import base64
import collections
import fnmatch
import http.client
import http.server
import os
import socket
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from advisor import assets, paths, winhttp
from tests.support import sandbox

OK_BODY = b"ok-body-" * 500
BIG_BODY = bytes(range(256)) * 1200  # 300 KiB 上下：比 winhttp.CHUNK 大好几倍，stream 要分几块交
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    trickle = None  # 由测试设成 threading.Event：/trickle 一直慢慢地发，直到它置位
    trickle_started = None  # 由测试设成 threading.Event：/trickle 发出响应头、开始发正文时置位（见 stop_when_trickling）
    seen_uas: list = []  # 每次请求带的 User-Agent（同名的头有几个记几个）
    seen_cookies: list = []  # 每次请求带的 Cookie（没有时记 None）
    paths: list = []  # 收到的请求路径
    posts: list = []  # 收到的 POST：{"path", "body", "headers"}
    sends: list = []  # /send/ 下收到的 GET、PUT、DELETE：{"method", "path", "body", "auth"（Authorization 有几个记几个）}

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端提前断开（超时、停止）：不往错误输出里打
            pass

    def end_headers(self):
        # 一回一个请求，回完就关连接，并在响应头里写明（Connection: close）：不写的话 WinHTTP 以为连接还能复用，下一个
        # 请求可能正好发在刚关掉的连接上，触发重发，请求数、句柄数就不确定了（重发另有 ResendTest）
        self.send_header("Connection", "close")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/send/"):
            return self._sent()
        Handler.seen_uas.append(self.headers.get_all("User-Agent"))
        Handler.seen_cookies.append(self.headers.get_all("Cookie"))
        Handler.paths.append(self.path)
        path = self.path
        if path == "/ok":
            self._send(200, OK_BODY)
        elif path == "/big":
            self._send(200, BIG_BODY)
        elif path == "/cookie":  # 回话带 Set-Cookie：测下一回请求里有没有把 cookie 带回来
            self.send_response(200)
            self.send_header("Set-Cookie", "a=b")
            self.send_header("Content-Length", str(len(OK_BODY)))
            self.end_headers()
            self.wfile.write(OK_BODY)
        elif path == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in (b"a" * 1000, b"b" * 37):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n")
        elif path == "/404":
            self._send(404, b"")
        elif path == "/500":
            self._send(500, b"")
        elif path in ("/301", "/302"):
            self.send_response(int(path[1:]))
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/r/"):  # 跳 N 次才到 /ok：/r/N → /r/N-1 → … → /r/0 → /ok
            n = int(path[3:])
            self.send_response(302)
            self.send_header("Location", f"/r/{n - 1}" if n > 1 else "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/short":  # 声明了 1000 字节还没发就断开
            body = b"z" * 5000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body) + 1000))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        elif path == "/trickle":  # 一直慢慢地发（不卡住，只是慢），直到测试停掉
            self.send_response(200)
            self.send_header("Content-Length", str(1 << 20))
            self.end_headers()
            if self.trickle_started is not None:
                self.trickle_started.set()
            while not (self.trickle and self.trickle.is_set()):
                try:
                    self.wfile.write(b"t" * 512)
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.02)
        elif path == "/slow":  # 收下请求后先不回响应头（测超时）
            time.sleep(10)
            self._send(200, b"late")
        elif path == "/stall":  # 回了响应头和一点正文以后不再发（正文读到一半超时）
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"xx")
            self.wfile.flush()
            time.sleep(10)
        else:
            self._send(404, b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Handler.paths.append(self.path)
        Handler.posts.append({"path": self.path, "body": body, "headers": dict(self.headers.items())})
        if self.path == "/post/ok":
            self._send(200, b'{"ok":true}')
        elif self.path == "/post/400":
            self._send(400, b'{"ok":false,"error":"bad_field:offers[0].state.star"}')
        elif self.path == "/post/302":
            self.send_response(302)
            self.send_header("Location", "/post/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/post/big":  # 比 post 读回话的上限大：只读前 64 KiB
            self._send(200, b"z" * (70 * 1024))
        else:
            self._send(404, b"")

    def do_PUT(self):
        self._sent()

    def do_DELETE(self):
        self._sent()

    def _sent(self):
        """Session.send 用的几个地址（GET、PUT、DELETE 都走这里）。"""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Handler.paths.append(self.path)
        Handler.sends.append({"method": self.command, "path": self.path, "body": body,
                              "auth": self.headers.get_all("Authorization")})
        if self.path == "/send/204":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/send/429":
            self.send_response(429)
            self.send_header("Retry-After", "12")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        elif self.path == "/send/302":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(200, b'{"ok":true}')

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server:
    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RawServer:
    """127.0.0.1 上的原始 TCP 服务，每条连接交给 handler(self, 连接, 第几条)；requests 记下收到的请求行。"""

    def __init__(self, handler):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.handler = handler
        self.connections = 0
        self.requests: list[str] = []
        self.heads: list[str] = []  # 每个请求的完整头（请求行 + 各头：测 Proxy-Authorization 用）
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._run, args=(client, self.connections), daemon=True).start()

    def _run(self, client, index):
        try:
            self.handler(self, client, index)
        except OSError:
            pass
        finally:
            client.close()

    def read_request(self, client) -> str | None:
        """读一个请求的头，返回请求行；对方关了连接时返回 None。完整头（含各头）另记在 self.heads。"""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                return None
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        line = head.split("\r\n", 1)[0]
        self.requests.append(line)
        self.heads.append(head)
        return line

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.sock.close()


def reply(client, body: bytes, code: int = 200, reason: str = "OK") -> None:
    client.sendall(b"HTTP/1.1 %d %s\r\nContent-Length: %d\r\n\r\n%s" % (code, reason.encode(), len(body), body))


def read_with_body(srv: RawServer, client) -> tuple[str, bytes] | None:
    """读一个带正文的请求（正文按 Content-Length 读完，不留在连接里搅乱下一个请求）：返回（请求行, 正文）；对方关了
    连接、正文没收全时返回 None。请求行同 read_request 记进 srv.requests。"""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(65536)
        if not chunk:
            return None
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    text = head.decode("latin-1")
    length = 0
    for row in text.split("\r\n")[1:]:
        name, _, value = row.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    while len(rest) < length:
        chunk = client.recv(65536)
        if not chunk:
            return None
        rest += chunk
    line = text.split("\r\n", 1)[0]
    srv.requests.append(line)
    srv.heads.append(text)
    return line, rest[:length]


def no_proxy_env(case: unittest.TestCase) -> None:
    """本机测试不经过代理（测完还原）：清掉环境变量里的代理，getproxies 也当没有（不看注册表里的手动代理，
    本机设置不影响结果）。"""
    for name in PROXY_VARS:
        if name in os.environ:
            case.addCleanup(os.environ.__setitem__, name, os.environ[name])
            del os.environ[name]
    patcher = mock.patch.object(winhttp.urllib.request, "getproxies", return_value={})
    patcher.start()
    case.addCleanup(patcher.stop)


class WinHttpCase(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.close)
        no_proxy_env(self)
        self.session = winhttp.Session()
        self.addCleanup(self.session.close)

    def get(self, path, timeout=10, stop=None):
        return self.session(urllib.request.Request(self.server.url(path)), timeout, stop)


class BasicTest(WinHttpCase):
    def test_content_length_body(self):
        with self.get("/ok") as resp:
            self.assertEqual(resp.read(), OK_BODY)

    def test_chunked_body(self):
        with self.get("/chunked") as resp:
            self.assertEqual(resp.read(), b"a" * 1000 + b"b" * 37)

    def test_user_agent_is_sent(self):
        # 请求带的 User-Agent 要真的发到服务器，而且只有这一个（WinHttpOpen 的会话名只是兜底，不另发一个）
        Handler.seen_uas.clear()
        request = urllib.request.Request(self.server.url("/ok"), headers={"User-Agent": assets.USER_AGENT})
        with self.session(request, 10) as resp:
            resp.read()
        self.assertEqual(Handler.seen_uas, [[assets.USER_AGENT]])

    def test_http_error(self):
        for path, code in (("/404", 404), ("/500", 500)):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            caught.exception.close()
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(caught.exception.reason)
            self.assertIsNotNone(caught.exception.headers)

    def test_redirects_followed(self):
        for path in ("/301", "/302"):
            with self.subTest(path=path), self.get(path) as resp:
                self.assertEqual(resp.read(), OK_BODY)

    def test_cookies_not_sent(self):
        # 不收不带 cookie（同没装 HTTPCookieProcessor 的 urllib）：第一次回话带 Set-Cookie，第二次请求里没有 Cookie 头
        Handler.seen_cookies.clear()
        for _ in range(2):
            with self.get("/cookie") as resp:
                self.assertEqual(resp.read(), OK_BODY)
        self.assertEqual(Handler.seen_cookies, [None, None])

    def test_redirect_limit(self):
        # 同 urllib：最多跟 10 次（共 11 个请求），第 11 次不跟，按网络错误报
        Handler.paths.clear()
        with self.get("/r/10") as resp:
            self.assertEqual(resp.read(), OK_BODY)
        self.assertEqual(len(Handler.paths), 11)
        Handler.paths.clear()
        with self.assertRaises(urllib.error.URLError) as caught:
            self.get("/r/11")
        self.assertNotIsInstance(caught.exception, urllib.error.HTTPError)
        self.assertEqual(caught.exception.reason, "HTTP 重定向失败（WinHTTP 12156）")
        self.assertEqual(len(Handler.paths), 11)


class PostTest(WinHttpCase):
    """Session.post（对局报告发送用）：正文经 lpOptional 真的发出去，头照传；任何状态码都交回、不抛 HTTPError；
    3xx 原样交回、不跟重定向；回话正文最多读 64 KiB；连接、发送、接收的超时分开设。"""

    HEADERS = {"Content-Type": "application/json", "Content-Encoding": "gzip", "User-Agent": "test-agent"}

    def post(self, path, data=b"{}", timeout=10, connect_timeout=2, stop=None):
        return self.session.post(self.server.url(path), data, dict(self.HEADERS), timeout=timeout,
                                 connect_timeout=connect_timeout, stop=stop)

    def test_body_and_headers(self):
        Handler.posts.clear()
        status, body = self.post("/post/ok", b"hello-body")
        self.assertEqual((status, body), (200, b'{"ok":true}'))
        sent = Handler.posts[-1]
        self.assertEqual(sent["body"], b"hello-body")
        self.assertEqual(sent["headers"].get("Content-Type"), "application/json")
        self.assertEqual(sent["headers"].get("Content-Encoding"), "gzip")
        self.assertEqual(sent["headers"].get("User-Agent"), "test-agent")
        self.assertEqual(sent["headers"].get("Content-Length"), "10")

    def test_error_status_returned(self):
        status, body = self.post("/post/400", b"x")
        self.assertEqual(status, 400)
        self.assertIn(b"bad_field:offers[0].state.star", body)

    def test_redirect_not_followed(self):
        Handler.paths.clear()
        status, _body = self.post("/post/302", b"x")
        self.assertEqual(status, 302)
        self.assertEqual(Handler.paths, ["/post/302"])

    def test_reply_capped(self):
        status, body = self.post("/post/big", b"x")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 64 * 1024)

    def test_timeouts_set_separately(self):
        with mock.patch.object(winhttp.winhttp, "WinHttpSetTimeouts") as set_timeouts:
            self.post("/post/ok", b"x", timeout=10, connect_timeout=2)
        self.assertEqual(set_timeouts.call_args[0][1:], (10000, 2000, 10000, 10000))

    def test_stopped(self):
        with self.assertRaises(winhttp.Stopped):
            self.post("/post/ok", b"x", stop=lambda: True)


class SendTest(WinHttpCase):
    """Session.send（作业站的接口用；post 也由它实现，见 PostTest）：方法照传，正文和请求头真的发出去（Authorization 只有
    一个），DELETE、GET 不带正文；任何状态码都交回、回话头一并交出（读 Retry-After），3xx 不跟。"""

    def send(self, method, path, data=b"", headers=None):
        return self.session.send(method, self.server.url(path), data, dict(headers or {"User-Agent": "test-agent"}),
                                 timeout=10, connect_timeout=2)

    def test_methods(self):
        Handler.sends.clear()
        headers = {"User-Agent": "test-agent", "Authorization": "Bearer t-1"}
        status, head, body = self.send("PUT", "/send/ok", b'{"a":1}', headers)
        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(head.get("content-length"), "11")
        self.assertEqual(self.send("DELETE", "/send/204", b"", headers)[::2], (204, b""))
        self.assertEqual(self.send("GET", "/send/ok", b"", headers)[0], 200)
        self.assertEqual([(s["method"], s["path"], s["body"], s["auth"]) for s in Handler.sends],
                         [("PUT", "/send/ok", b'{"a":1}', ["Bearer t-1"]),
                          ("DELETE", "/send/204", b"", ["Bearer t-1"]),
                          ("GET", "/send/ok", b"", ["Bearer t-1"])])

    def test_status_and_headers_returned(self):
        status, head, body = self.send("GET", "/send/429")
        self.assertEqual((status, body), (429, b"{}"))
        self.assertEqual(head.get("Retry-After"), "12")

    def test_redirect_not_followed(self):
        Handler.paths.clear()
        status, head, _body = self.send("GET", "/send/302")
        self.assertEqual(status, 302)
        self.assertEqual(head.get("Location"), "/ok")
        self.assertEqual(Handler.paths, ["/send/302"])


class RedirectTest(WinHttpCase):
    """重定向没跟、回话是 3xx 时按 HTTPError 报（code 用那个 3xx、reason 用状态行文字），不把 3xx 的正文当图片交回。

    真 https→http 本机造不出（要真的 HTTPS 服务、要联网）。用「重定向策略改成一律不跟」造同一种回话：本机 http 服务回
    302，WinHTTP 把 302 原样交回，走的就是同一条报错路径。
    """

    def test_unfollowed_redirect_is_http_error(self):
        with mock.patch.object(winhttp, "WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP", 0):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get("/302")
        caught.exception.close()
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.reason, "Found")
        self.assertIsNotNone(caught.exception.headers)

    def test_stream_redirects_off(self):
        """stream 给 redirects=False 时一项重定向都不跟（按编号取方案用）：3xx 按 HTTPError 报，只发了一个请求，sink 什么都
        没收到；不给时照旧跟到底。"""
        Handler.paths.clear()
        chunks = []
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.session.stream(urllib.request.Request(self.server.url("/302")), chunks.append, 10, redirects=False)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual((Handler.paths, chunks), (["/302"], []))
        self.assertEqual(self.session.stream(urllib.request.Request(self.server.url("/302")), chunks.append, 10),
                         len(OK_BODY))


class FailureTest(WinHttpCase):
    def test_short_body_is_incomplete_read(self):
        with self.assertRaises(urllib.error.URLError) as caught:
            self.get("/short")
        self.assertIsInstance(caught.exception.reason, http.client.IncompleteRead)

    def test_timeout(self):
        """等回话超时、正文读到一半超时都按超时报（不当成连接断了的 IncompleteRead）。WinHTTP 的超时按 4 秒一档，
        两种情况同时跑。"""
        results = {}

        def fetch(path):
            session = winhttp.Session()
            try:
                session(urllib.request.Request(self.server.url(path)), 0.5)
                results[path] = "没有超时"
            except urllib.error.URLError as e:
                results[path] = e.reason
            finally:
                session.close()

        threads = [threading.Thread(target=fetch, args=(path,)) for path in ("/slow", "/stall")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(results, {"/slow": "操作超时（WinHTTP 12002）", "/stall": "操作超时（WinHTTP 12002）"})

    def test_send_error_text(self):
        # 发请求出错（例如解析不了主机名）：中文原因 + 错误码，按网络错误（URLError）报。不联网，换上出错的发送
        with mock.patch.object(winhttp.winhttp, "WinHttpSendRequest", return_value=0), \
                mock.patch.object(winhttp, "_last_error", return_value=12007):
            with self.assertRaises(urllib.error.URLError) as caught:
                self.get("/ok")
        self.assertNotIsInstance(caught.exception, urllib.error.HTTPError)
        self.assertEqual(caught.exception.reason, "无法解析主机名（WinHTTP 12007）")


class ResendTest(unittest.TestCase):
    """复用的长连接在请求途中被服务器关掉（空闲断开和下一个请求擦肩而过）：立刻换一条连接重发一次。"""

    def setUp(self):
        no_proxy_env(self)
        self.session = winhttp.Session()
        self.addCleanup(self.session.close)

    def server(self, handler) -> RawServer:
        srv = RawServer(handler)
        self.addCleanup(srv.close)
        return srv

    def get(self, srv, path, stop=None):
        with self.session(urllib.request.Request(srv.url(path)), 10, stop) as resp:
            return resp.read()

    def test_dropped_keep_alive_is_resent(self):
        for how in ("fin", "rst"):
            def handler(srv, client, index, how=how):
                if index == 1:  # 第一条连接：回了第一个请求，第二个请求到了不回就关（RST 或正常关）
                    srv.read_request(client)
                    reply(client, b"first")
                    srv.read_request(client)
                    if how == "rst":
                        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    return
                while srv.read_request(client):
                    reply(client, b"again")

            with self.subTest(how=how):
                srv = self.server(handler)
                self.assertEqual(self.get(srv, "/a"), b"first")
                self.assertEqual(self.get(srv, "/b"), b"again")
                self.assertEqual(srv.requests, ["GET /a HTTP/1.1", "GET /b HTTP/1.1", "GET /b HTTP/1.1"])
                self.assertEqual(srv.connections, 2)

    def test_stream_resent(self):
        # stream 同样换一条连接重发一次：断在回话之前，sink 还没收到东西，不会交重
        def handler(srv, client, index):
            if index == 1:
                srv.read_request(client)
                reply(client, b"first")
                srv.read_request(client)
                return
            while srv.read_request(client):
                reply(client, b"again")

        srv = self.server(handler)
        self.assertEqual(self.get(srv, "/a"), b"first")
        chunks = []
        self.assertEqual(self.session.stream(urllib.request.Request(srv.url("/b")), chunks.append, 10), 5)
        self.assertEqual(chunks, [b"again"])
        self.assertEqual(srv.requests, ["GET /a HTTP/1.1", "GET /b HTTP/1.1", "GET /b HTTP/1.1"])
        self.assertEqual(srv.connections, 2)

    def test_post_resent_with_body(self):
        # post 同样换一条连接重发一次，重发的那一次照样带着整份正文（对局报告：服务按报告编号去重，重发不会存两份）
        body = b"report-" * 3000  # 比一个 TCP 包大：正文分几次收到也要收全
        for how in ("fin", "rst"):
            got = []

            def handler(srv, client, index, how=how, got=got):
                if index == 1:  # 第一条连接：回了第一个请求，第二个请求收下了不回就关（RST 或正常关）
                    got.append(read_with_body(srv, client))
                    reply(client, b"first")
                    got.append(read_with_body(srv, client))
                    if how == "rst":
                        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    return
                while True:
                    request = read_with_body(srv, client)
                    if request is None:
                        return
                    got.append(request)
                    reply(client, b"again")

            with self.subTest(how=how):
                srv = self.server(handler)
                headers = {"Content-Type": "application/json"}
                self.assertEqual(self.session.post(srv.url("/a"), b"first-body", headers, timeout=10), (200, b"first"))
                self.assertEqual(self.session.post(srv.url("/b"), body, headers, timeout=10), (200, b"again"))
                self.assertEqual(srv.requests, ["POST /a HTTP/1.1", "POST /b HTTP/1.1", "POST /b HTTP/1.1"])
                self.assertEqual(srv.connections, 2)
                self.assertEqual(got, [("POST /a HTTP/1.1", b"first-body"), ("POST /b HTTP/1.1", body),
                                       ("POST /b HTTP/1.1", body)])

    def test_resend_only_once(self):
        # 每条连接都是收下请求不回就关：只重发一次，按网络错误报
        srv = self.server(lambda srv, client, index: srv.read_request(client))
        with self.assertRaises(urllib.error.URLError) as caught:
            self.get(srv, "/x")
        self.assertEqual(caught.exception.reason, "服务器返回了无法识别的响应（WinHTTP 12152）")
        self.assertEqual((len(srv.requests), srv.connections), (2, 2))

    def test_no_resend_after_stop(self):
        # 请求发出去以后、回话来之前连接断了，这一刻正好置了停止标志（取消下载）：不再重发（不再连接），按停止收手
        # 同步方式下连接断开有两处会报出来：发请求（WinHttpSendRequest 等回话来了、连接断了或超时才返回）和读回话
        # （WinHttpReceiveResponse），报在哪一步要看时机，所以两处都模拟「这一刻正好取消」
        stop = threading.Event()
        real_send = winhttp.winhttp.WinHttpSendRequest
        real_receive = winhttp.winhttp.WinHttpReceiveResponse

        def send(*args):
            ok = real_send(*args)
            if not ok:
                stop.set()
            return ok

        def receive(*args):
            ok = real_receive(*args)
            if not ok:
                stop.set()
            return ok

        srv = self.server(lambda srv, client, index: srv.read_request(client))
        with mock.patch.object(winhttp.winhttp, "WinHttpSendRequest", side_effect=send), \
                mock.patch.object(winhttp.winhttp, "WinHttpReceiveResponse", side_effect=receive):
            with self.assertRaises(winhttp.Stopped):
                self.get(srv, "/x", stop)
        time.sleep(0.3)  # 要是重发了，等假服务接下新的连接
        self.assertEqual((srv.connections, len(srv.requests)), (1, 1))


def stop_when_trickling(case: unittest.TestCase, stop: threading.Event) -> None:
    """服务器开始发 /trickle 的正文以后才置停止标志。用固定时长的定时器时，机器忙的时候它可能在请求发出之前就到点，
    那样停在了连接之前（不建句柄），测的就不是「正文读到一半停」。"""
    started = Handler.trickle_started = threading.Event()
    case.addCleanup(setattr, Handler, "trickle_started", None)

    def run():
        if started.wait(30):
            time.sleep(0.1)  # 让客户端读到几块正文
        stop.set()

    threading.Thread(target=run, daemon=True).start()


class StopTest(WinHttpCase):
    def test_stop_mid_body(self):
        Handler.trickle = threading.Event()
        self.addCleanup(setattr, Handler, "trickle", None)
        stop = threading.Event()
        stop_when_trickling(self, stop)
        with self.assertRaises(winhttp.Stopped):
            self.get("/trickle", stop=stop)
        Handler.trickle.set()

    def test_keep_alive_translates_stopped(self):
        # assets._KeepAlive 把 winhttp.Stopped 转成 assets._Stopped，下载的各层照旧收手
        keep = assets._KeepAlive()
        self.addCleanup(keep.close)
        with mock.patch.object(winhttp.Session, "__call__", side_effect=winhttp.Stopped):
            with self.assertRaises(assets._Stopped):
                keep(urllib.request.Request(self.server.url("/ok")), 1, None)

    def test_download_stops_and_leaves_no_files(self):
        # 经 assets.download（真的 _KeepAlive、真的线程）：正文慢慢来，置停止标志后很快收手，不留文件
        Handler.trickle = threading.Event()
        self.addCleanup(setattr, Handler, "trickle", None)
        base = sandbox.temp_dir(self, "test-winhttp-")
        with mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": base / kind):
            item = assets.AssetItem("chips", "slow.png", "slow.png", self.server.url("/trickle"), 1 << 20, "")
            stop = threading.Event()
            threading.Timer(0.4, stop.set).start()
            start = time.monotonic()
            ok, failed = assets.download([item], stop=stop, workers=1)
            self.assertLess(time.monotonic() - start, 3.0)
        Handler.trickle.set()
        self.assertEqual(ok, [])
        self.assertEqual(failed, [("chips/slow.png", assets.CANCELLED)])
        self.assertEqual(list(base.rglob("*")), [])


class HandleTest(WinHttpCase):
    def test_every_path_closes_its_handles(self):
        """成功、HTTP 错误、正文没读够、重定向失败、停止，每条路径建的连接、请求句柄都关掉（会话的留到 close）。"""
        real = {name: getattr(winhttp.winhttp, name)
                for name in ("WinHttpConnect", "WinHttpOpenRequest", "WinHttpCloseHandle")}
        opened, closed = collections.Counter(), collections.Counter()

        def creating(name):
            def call(*args):
                handle = real[name](*args)
                if handle:
                    opened[handle] += 1
                return handle
            return call

        def closing(handle):
            closed[handle] += 1
            return real["WinHttpCloseHandle"](handle)

        Handler.trickle = threading.Event()
        self.addCleanup(setattr, Handler, "trickle", None)
        with mock.patch.object(winhttp.winhttp, "WinHttpConnect", side_effect=creating("WinHttpConnect")), \
                mock.patch.object(winhttp.winhttp, "WinHttpOpenRequest", side_effect=creating("WinHttpOpenRequest")), \
                mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle", side_effect=closing):
            with self.get("/ok") as resp:
                resp.read()
            for path in ("/404", "/short", "/r/11"):
                with self.subTest(path=path), self.assertRaises(urllib.error.URLError) as caught:
                    self.get(path)
                if isinstance(caught.exception, urllib.error.HTTPError):
                    caught.exception.close()
            stop = threading.Event()
            stop_when_trickling(self, stop)
            with self.assertRaises(winhttp.Stopped):
                self.get("/trickle", stop=stop)
            Handler.trickle.set()
        self.assertEqual(sum(opened.values()), 10)  # 5 次请求，各一个连接句柄、一个请求句柄
        self.assertEqual(opened, closed)


class StreamTest(WinHttpCase):
    """stream：正文不整个放进内存，收到一块就交给 sink（大文件边下边写、边算校验值）。读法、出错、停止、重定向都同
    __call__；sink 抛的异常照原样往外抛，句柄照样关掉。重发见 ResendTest.test_stream_resent。"""

    def stream(self, path, sink, stop=None):
        return self.session.stream(urllib.request.Request(self.server.url(path)), sink, 10, stop)

    def test_chunks_in_order(self):
        chunks = []
        total = self.stream("/big", chunks.append)
        self.assertEqual(b"".join(chunks), BIG_BODY)
        self.assertEqual(total, len(BIG_BODY))
        self.assertGreater(len(chunks), 1)  # 真的是一块一块交的
        self.assertTrue(all(0 < len(chunk) <= winhttp.CHUNK for chunk in chunks))

    def test_chunked_body_and_redirect(self):
        for path, body in (("/chunked", b"a" * 1000 + b"b" * 37), ("/302", OK_BODY)):
            with self.subTest(path=path):
                chunks = []
                total = self.stream(path, chunks.append)
                self.assertEqual((b"".join(chunks), total), (body, len(body)))

    def test_user_agent_is_sent(self):
        Handler.seen_uas.clear()
        request = urllib.request.Request(self.server.url("/ok"), headers={"User-Agent": "acc-test/1"})
        self.assertEqual(self.session.stream(request, lambda chunk: None, 10), len(OK_BODY))
        self.assertEqual(Handler.seen_uas, [["acc-test/1"]])

    def test_http_error_leaves_sink_alone(self):
        for path, code in (("/404", 404), ("/500", 500)):
            sink = mock.Mock()
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.stream(path, sink)
            caught.exception.close()
            self.assertEqual(caught.exception.code, code)
            sink.assert_not_called()

    def test_short_body_is_incomplete_read(self):
        # 声明的长度没读够连接就断了：同 __call__ 按 IncompleteRead 报；交出去的块不另留，partial 为空，expected 是还差的
        chunks = []
        with self.assertRaises(urllib.error.URLError) as caught:
            self.stream("/short", chunks.append)
        reason = caught.exception.reason
        self.assertIsInstance(reason, http.client.IncompleteRead)
        self.assertEqual((reason.partial, reason.expected), (b"", 1000))
        self.assertEqual(b"".join(chunks), b"z" * 5000)

    def test_stop_mid_body(self):
        Handler.trickle = threading.Event()
        self.addCleanup(setattr, Handler, "trickle", None)
        stop = threading.Event()
        stop_when_trickling(self, stop)
        with self.assertRaises(winhttp.Stopped):
            self.stream("/trickle", lambda chunk: None, stop)
        Handler.trickle.set()

    def test_stopped_before_connect(self):
        stop = threading.Event()
        stop.set()
        Handler.paths.clear()
        with self.assertRaises(winhttp.Stopped):
            self.stream("/ok", lambda chunk: None, stop)
        self.assertEqual(Handler.paths, [])

    def test_sink_error_propagates_and_closes_handles(self):
        real = {name: getattr(winhttp.winhttp, name)
                for name in ("WinHttpConnect", "WinHttpOpenRequest", "WinHttpCloseHandle")}
        opened, closed = collections.Counter(), collections.Counter()

        def creating(name):
            def call(*args):
                handle = real[name](*args)
                if handle:
                    opened[handle] += 1
                return handle
            return call

        def closing(handle):
            closed[handle] += 1
            return real["WinHttpCloseHandle"](handle)

        class Enough(Exception):
            pass

        def sink(chunk):
            raise Enough

        with mock.patch.object(winhttp.winhttp, "WinHttpConnect", side_effect=creating("WinHttpConnect")), \
                mock.patch.object(winhttp.winhttp, "WinHttpOpenRequest", side_effect=creating("WinHttpOpenRequest")), \
                mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle", side_effect=closing):
            with self.assertRaises(Enough):
                self.stream("/big", sink)
        self.assertEqual(sum(opened.values()), 2)  # 一个连接句柄、一个请求句柄
        self.assertEqual(opened, closed)


RANGE_BODY = bytes(range(256)) * 40  # 10 KiB：按字节段取的那个文件


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """认 Range 的本机服务器（标准库的 http.server 不认 Range，这里自己处理「bytes=起-止」）。路径定回法：
    /ranged         认 Range：回 206、Content-Range 和要的那一段
    /chunked        同 /ranged，正文分块传输（不声明长度）
    /plain          不认 Range：一律回 200 和整个文件
    /wrong          回 206，Content-Range 的起止各错一位（正文照要的给）
    /long           回 206、Content-Range 写得对，正文却多 100 字节（Content-Length 照实写）
    /long-chunked   同 /long，分块传输（不声明长度）
    /hop            302 跳到同一个服务器的 /ranged
    /far            302 跳到另一个服务器（主机名写 localhost、另一个端口）的 /ranged
    其余回 404。服务器的 seen 记下每个请求的（路径, Range 头）。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except OSError:  # 客户端没读完正文就断开（不往下读时）
            pass

    def do_GET(self):
        owner = self.server.owner
        owner.seen.append((self.path, self.headers.get("Range")))
        if self.path in ("/hop", "/far"):
            target = "/ranged" if self.path == "/hop" else f"http://localhost:{owner.far}/ranged"
            self._reply(302, b"", {"Location": target})
            return
        if self.path == "/plain":
            self._reply(200, RANGE_BODY)
            return
        if self.path not in ("/ranged", "/chunked", "/wrong", "/long", "/long-chunked"):
            self._reply(404, b"")
            return
        start, end = (int(n) for n in self.headers["Range"].removeprefix("bytes=").split("-"))
        body = RANGE_BODY[start:end + 1]
        shown = (start + 1, end + 1) if self.path == "/wrong" else (start, end)
        headers = {"Content-Range": f"bytes {shown[0]}-{shown[1]}/{len(RANGE_BODY)}"}
        if self.path.startswith("/long"):
            body += b"\0" * 100
        if self.path.endswith("chunked"):
            self.send_response(206)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            for at in range(0, len(body), 300):
                part = body[at:at + 300]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n")
            return
        self._reply(206, body, headers)

    def _reply(self, code: int, body: bytes, headers: dict | None = None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # 一回一个请求，回完就关（同 Handler）
        self.end_headers()
        self.wfile.write(body)


class RangeServer:
    def __init__(self, far: int | None = None):
        self.seen: list[tuple[str, str | None]] = []
        self.far = far  # /far 跳到的端口
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Refused(Exception):
    """测试里的 head 认出回话不对（例如不是 206）。"""


class RangeTest(unittest.TestCase):
    """按字节段取（stream 的 head、limit）：head 在读正文之前拿到状态码和回话头，它抛异常时正文一个字节都不读；回话比 limit 长时
    不往下读、抛 TooLong（声明的长度超过时一个字节都不读，分块传输时收满了还有就停）。WinHTTP 自动跟随跳转时请求里的 Range 头
    还在（同一个服务器、换了主机名和端口的另一个服务器都试；发布页附件的地址就是跳到别的主机去的）。"""

    def setUp(self):
        no_proxy_env(self)
        self.far = RangeServer()
        self.addCleanup(self.far.close)
        self.near = RangeServer(far=self.far.port)
        self.addCleanup(self.near.close)
        self.session = winhttp.Session()
        self.addCleanup(self.session.close)
        self.heads: list[tuple[int, object]] = []
        self.chunks: list[bytes] = []

    def fetch(self, path, start, end, *, check=None, limit=None, head=True) -> int:
        """带「Range: bytes=start-end」取 path：head 记下每次的（状态码, 回话头），再交给 check 核对；正文的块记进 chunks。"""
        def record(status, headers):
            self.heads.append((status, headers))
            if check is not None:
                check(status, headers)

        request = urllib.request.Request(self.near.url(path), headers={"Range": f"bytes={start}-{end}"})
        return self.session.stream(request, self.chunks.append, 10, None, head=record if head else None, limit=limit)

    @staticmethod
    def want_206(status, headers):
        if status != 206:
            raise Refused(f"回的是 {status}")

    def test_partial_content(self):
        # 206：head 先拿到状态码和回话头（名字不分大小写），正文正好是要的那一段
        self.assertEqual(self.fetch("/ranged", 100, 1099, check=self.want_206, limit=1000), 1000)
        self.assertEqual(b"".join(self.chunks), RANGE_BODY[100:1100])
        (status, headers), = self.heads
        self.assertEqual(status, 206)
        self.assertEqual(headers.get_all("content-range"), [f"bytes 100-1099/{len(RANGE_BODY)}"])
        self.assertEqual(headers["Content-Length"], "1000")
        self.assertEqual(self.near.seen, [("/ranged", "bytes=100-1099")])
        # 分块传输、正好 limit 字节：照常收完
        self.chunks.clear()
        self.assertEqual(self.fetch("/chunked", 0, 999, limit=1000), 1000)
        self.assertEqual(b"".join(self.chunks), RANGE_BODY[:1000])

    def test_server_ignores_range(self):
        # 不认 Range、回 200 和整个文件：head 认出来抛异常，正文一个字节都不读，异常照原样往外抛
        with self.assertRaises(Refused):
            self.fetch("/plain", 0, 999, check=self.want_206, limit=1000)
        self.assertEqual([status for status, _ in self.heads], [200])
        self.assertEqual(self.chunks, [])
        # 不给 head、只给 limit：声明的长度比 limit 长，同样一个字节都不读，抛 TooLong（是 URLError）
        with self.assertRaises(winhttp.TooLong) as caught:
            self.fetch("/plain", 0, 999, limit=1000, head=False)
        self.assertIsInstance(caught.exception, urllib.error.URLError)
        self.assertIn("1000", str(caught.exception.reason))
        self.assertEqual(self.chunks, [])

    def test_content_range_reaches_head(self):
        # Content-Range 对不上：head 拿到的就是服务器写的那一个，调用方据此不要这个回话，正文不读
        def check(status, headers):
            if headers["Content-Range"] != f"bytes 10-19/{len(RANGE_BODY)}":
                raise Refused(headers["Content-Range"])

        with self.assertRaises(Refused) as caught:
            self.fetch("/wrong", 10, 19, check=check, limit=10)
        self.assertEqual(str(caught.exception), f"bytes 11-20/{len(RANGE_BODY)}")
        self.assertEqual(self.chunks, [])

    def test_longer_than_asked(self):
        # 回得比要的长：声明了长度的一个字节都不读；分块传输的收满 limit 就停，交出去的正好 limit 字节
        with self.assertRaises(winhttp.TooLong):
            self.fetch("/long", 0, 999, check=self.want_206, limit=1000)
        self.assertEqual(self.chunks, [])
        with self.assertRaises(winhttp.TooLong):
            self.fetch("/long-chunked", 0, 999, check=self.want_206, limit=1000)
        self.assertEqual(b"".join(self.chunks), RANGE_BODY[:1000])

    def test_range_kept_across_redirects(self):
        # 跳转（302）以后 Range 头还在：同一个服务器的、换了主机名和端口的都是；head 拿到的是最后那一跳的回话
        for path, server in (("/hop", self.near), ("/far", self.far)):
            with self.subTest(path=path):
                self.heads.clear()
                self.chunks.clear()
                self.assertEqual(self.fetch(path, 2000, 2999, check=self.want_206, limit=1000), 1000)
                self.assertEqual(b"".join(self.chunks), RANGE_BODY[2000:3000])
                self.assertEqual([status for status, _ in self.heads], [206])
                self.assertIn(("/ranged", "bytes=2000-2999"), server.seen)
        self.assertEqual(self.near.seen[:2], [("/hop", "bytes=2000-2999"), ("/ranged", "bytes=2000-2999")])
        self.assertEqual(self.far.seen, [("/ranged", "bytes=2000-2999")])

    def test_http_error_skips_head(self):
        # 回话不是 2xx：照旧抛 HTTPError，head 不调用
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/missing", 0, 9, check=self.want_206, limit=10)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(self.heads, [])

    def test_head_error_closes_handles(self):
        real = {name: getattr(winhttp.winhttp, name)
                for name in ("WinHttpConnect", "WinHttpOpenRequest", "WinHttpCloseHandle")}
        opened, closed = collections.Counter(), collections.Counter()

        def creating(name):
            def call(*args):
                handle = real[name](*args)
                if handle:
                    opened[handle] += 1
                return handle
            return call

        def closing(handle):
            closed[handle] += 1
            return real["WinHttpCloseHandle"](handle)

        with mock.patch.object(winhttp.winhttp, "WinHttpConnect", side_effect=creating("WinHttpConnect")), \
                mock.patch.object(winhttp.winhttp, "WinHttpOpenRequest", side_effect=creating("WinHttpOpenRequest")), \
                mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle", side_effect=closing):
            with self.assertRaises(Refused):
                self.fetch("/plain", 0, 999, check=self.want_206)
            with self.assertRaises(winhttp.TooLong):
                self.fetch("/long", 0, 999, limit=1000)
        self.assertEqual(sum(opened.values()), 4)  # 两次请求，各一个连接句柄、一个请求句柄
        self.assertEqual(opened, closed)


class ParallelTest(WinHttpCase):
    def test_sessions_in_parallel_threads(self):
        results, errors = [], []

        def worker():
            session = winhttp.Session()  # 每个线程一个会话
            try:
                with session(urllib.request.Request(self.server.url("/ok")), 10) as resp:
                    results.append(resp.read())
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(results, [OK_BODY] * 4)


class ProxyTest(unittest.TestCase):
    """用哪个代理：会话建立时调一次 urllib.request.getproxies()，取「https」那一项（口径同 urllib）；例外主机不在这里，
    每个请求另判（见 BypassTest）。"""

    def setUp(self):
        self.calls = []

        def fake_open(agent, access, proxy, bypass, flags):
            self.calls.append((agent, access, proxy, bypass, flags))
            return 1  # 假句柄

        for patcher in (mock.patch.object(winhttp.winhttp, "WinHttpOpen", side_effect=fake_open),
                        mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, **values):
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in PROXY_VARS:
            os.environ.pop(name, None)
        os.environ.update(values)

    def test_environment_proxy_wins(self):
        self.env(HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="a.example, .b.example")
        winhttp.Session().close()
        _, access, proxy, bypass, _ = self.calls[-1]
        self.assertEqual((access, proxy, bypass), (winhttp.WINHTTP_ACCESS_TYPE_NAMED_PROXY, "127.0.0.1:9", None))

    def test_lower_case_variable(self):
        self.env(https_proxy="http://127.0.0.1:10")
        winhttp.Session().close()
        _, access, proxy, _, _ = self.calls[-1]
        self.assertEqual((access, proxy), (winhttp.WINHTTP_ACCESS_TYPE_NAMED_PROXY, "127.0.0.1:10"))

    def test_http_proxy_only_is_direct(self):
        # 环境变量里有任何 *_proxy 时只看环境变量：只有 http_proxy、没有 https_proxy 时 https 直连，不看系统的代理
        self.env(http_proxy="http://127.0.0.1:9")
        winhttp.Session().close()
        _, access, proxy, bypass, _ = self.calls[-1]
        self.assertEqual((access, proxy, bypass), (winhttp.WINHTTP_ACCESS_TYPE_NO_PROXY, None, None))

    def test_no_proxy_at_all_is_direct(self):
        # 环境变量一个都没有、注册表也没有手动代理时直连（getproxies 为空；换成假实现直接核对这条路径）
        with mock.patch.object(winhttp.urllib.request, "getproxies", return_value={}):
            winhttp.Session().close()
        _, access, proxy, bypass, _ = self.calls[-1]
        self.assertEqual((access, proxy, bypass), (winhttp.WINHTTP_ACCESS_TYPE_NO_PROXY, None, None))

    def test_default_agent(self):
        # 不给 agent 时 WinHttpOpen 的会话名是本工具的英文名（下载的请求另带 assets.USER_AGENT，见 test_user_agent_is_sent）
        with mock.patch.object(winhttp.urllib.request, "getproxies", return_value={}):
            winhttp.Session().close()
        self.assertEqual(self.calls[-1][0], "AstralChipCompass")

    def test_registry_proxy_used(self):
        # 环境变量一个都没有时按注册表的手动代理（getproxies 内部做的）；换成假实现直接核对用的就是它的「https」项
        with mock.patch.object(winhttp.urllib.request, "getproxies", return_value={"https": "http://127.0.0.1:11"}):
            winhttp.Session().close()
        _, access, proxy, bypass, _ = self.calls[-1]
        self.assertEqual((access, proxy, bypass), (winhttp.WINHTTP_ACCESS_TYPE_NAMED_PROXY, "127.0.0.1:11", None))

    def test_proxy_value_forms(self):
        # urllib 认得的写法都化成 WinHTTP 认的「主机:端口」（带末尾的「/」或用户名、密码时 WinHTTP 建不了会话）
        for value, expected in (
            ("http://127.0.0.1:8080", "127.0.0.1:8080"),
            ("127.0.0.1:8080", "127.0.0.1:8080"),
            ("http://127.0.0.1:8080/", "127.0.0.1:8080"),
            ("  HTTP://Proxy.Example:8080/  ", "proxy.example:8080"),
            ("http://user:pw@proxy.example:3128", "proxy.example:3128"),
            ("https://proxy.example:443", "proxy.example:443"),
            ("socks5://127.0.0.1:1080", "127.0.0.1:1080"),
            ("http://[::1]:8080", "[::1]:8080"),
            ("proxy.example", "proxy.example"),
            ("http://proxy.example:port", "http://proxy.example:port"),  # 认不出的原样交给 WinHTTP
        ):
            with self.subTest(value=value):
                self.assertEqual(winhttp._proxy_server(value), expected)

    def test_proxy_credentials(self):
        # 代理地址里的用户名密码照 urllib 的拆法取（_parse_proxy）、百分号转义照 urllib 解掉（ProxyHandler 用 unquote）；
        # 缺一个时 urllib 不发 Proxy-Authorization，这里也不给（代理要认证时回 407）
        self.assertEqual(winhttp._proxy_credentials("http://user:pw@proxy.example:3128"), ("user", "pw"))
        self.assertEqual(winhttp._proxy_credentials("http://u%40x:p%20w@proxy.example:3128"), ("u@x", "p w"))
        self.assertIsNone(winhttp._proxy_credentials("http://proxy.example:3128"))
        self.assertIsNone(winhttp._proxy_credentials("proxy.example:3128"))
        self.assertIsNone(winhttp._proxy_credentials("http://user@proxy.example:3128"))
        self.assertIsNone(winhttp._proxy_credentials("http://:pw@proxy.example:3128"))
        self.assertIsNone(winhttp._proxy_credentials("http://proxy.example:port"))

    def test_error_text(self):
        self.assertEqual(winhttp._reason(12007), "无法解析主机名（WinHTTP 12007）")
        self.assertEqual(winhttp._reason(12029), "无法建立连接（WinHTTP 12029）")
        self.assertEqual(winhttp._reason(99999), "WinHTTP 错误 99999")


class SecureProtocolTest(unittest.TestCase):
    """建会话时开 TLS 1.2 和 1.3：系统给 WinHTTP 定的默认协议可能只有 TLS 1.2（DefaultSecureProtocols 设成 0x800），
    只收 TLS 1.3 的站点握不成手、报 12175。Windows 11 起才设（更早的 Windows 不认这个标志），设不上时照系统默认、会话照常建。"""

    def setUp(self):
        self.options = []
        self.result = 1

        def fake_set(handle, option, value, size):
            self.options.append((handle, option, value._obj.value))  # value 是 ctypes.byref(c_ulong)
            return self.result

        for patcher in (mock.patch.object(winhttp.winhttp, "WinHttpOpen", return_value=7),
                        mock.patch.object(winhttp.winhttp, "WinHttpSetOption", side_effect=fake_set),
                        mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle"),
                        mock.patch.object(winhttp.urllib.request, "getproxies", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def build(number: int):
        return mock.patch.object(winhttp.sys, "getwindowsversion", return_value=mock.Mock(build=number))

    def test_windows_11_enables_tls13(self):
        for number in (22000, 26200):
            with self.subTest(build=number), self.build(number):
                self.options.clear()
                winhttp.Session().close()
                self.assertEqual(self.options, [(7, winhttp.WINHTTP_OPTION_SECURE_PROTOCOLS, 0x2800)])

    def test_older_windows_keeps_default(self):
        with self.build(19045):
            winhttp.Session().close()
        self.assertEqual(self.options, [])

    def test_option_refused_still_opens(self):
        self.result = 0
        with self.build(26200):
            session = winhttp.Session()
        self.assertEqual([option for _, option, _ in self.options], [winhttp.WINHTTP_OPTION_SECURE_PROTOCOLS])
        session.close()


class SecureProtocolRealTest(unittest.TestCase):
    """真的 WinHttpOpen、WinHttpSetOption（不联网）：这台 Windows 认 TLS 1.3 的标志时，会话句柄上设得上这一项。"""

    def test_real_session_accepts_option(self):
        if not winhttp._tls13_supported():
            self.skipTest("这台 Windows 的 WinHTTP 不认 TLS 1.3 的标志")
        handle = winhttp.winhttp.WinHttpOpen("test", winhttp.WINHTTP_ACCESS_TYPE_NO_PROXY, None, None, 0)
        self.assertTrue(handle)
        try:
            data = winhttp.ctypes.c_ulong(winhttp.WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2
                                          | winhttp.WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3)
            self.assertTrue(winhttp.winhttp.WinHttpSetOption(handle, winhttp.WINHTTP_OPTION_SECURE_PROTOCOLS,
                                                             winhttp.ctypes.byref(data), winhttp.ctypes.sizeof(data)))
        finally:
            winhttp.winhttp.WinHttpCloseHandle(handle)


class ProxySessionTest(unittest.TestCase):
    """环境变量里常见的代理写法交给真的 WinHttpOpen 都建得起会话（建会话不联网）；建不起时下载会全部失败。"""

    def test_common_values_open(self):
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for proxy in (
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8080/",
            "127.0.0.1:8080",
            "http://user:pw@proxy.example:3128/",
            "https://127.0.0.1:8080",
        ):
            with self.subTest(proxy=proxy):
                for name in PROXY_VARS:
                    os.environ.pop(name, None)
                os.environ["HTTPS_PROXY"] = proxy
                winhttp.Session().close()


class BypassTest(unittest.TestCase):
    """哪些主机不走代理：每个请求照 urllib.request.proxy_bypass 判，为真时这个请求直连（本机假代理收不到请求），
    为假时走代理。请求的主机名用本机的计算机名（见模块说明：走了代理时 WinHTTP 不解析它）。"""

    def setUp(self):
        def handler(srv, client, index):
            if srv.read_request(client):
                reply(client, b"via-proxy")

        self.proxy = RawServer(handler)
        self.addCleanup(self.proxy.close)
        unused = socket.create_server(("127.0.0.1", 0))
        self.port = unused.getsockname()[1]  # 没人听的端口：直连时连不上
        unused.close()
        self.host = socket.gethostname().lower()
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in PROXY_VARS:
            os.environ.pop(name, None)
        os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{self.proxy.port}"

    def fetch(self, bypass, scheme="http", timeout=5):
        with mock.patch.object(winhttp.urllib.request, "proxy_bypass", return_value=bypass):
            session = winhttp.Session()
            try:
                return session(urllib.request.Request(f"{scheme}://{self.host}:{self.port}/x.png"), timeout)
            finally:
                session.close()

    def test_bypass_goes_direct(self):
        # http、https 都直连：请求真的发出去了（设直连没出错，不是发之前就报错），假代理一个请求都没收到，
        # 直连没人听的端口，连不上。计算机名的几个地址挨个连、每个约 2 秒，超时给 1 秒（WinHTTP 按 4 秒一档）省时间
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                send = mock.Mock(wraps=winhttp.winhttp.WinHttpSendRequest)
                with mock.patch.object(winhttp.winhttp, "WinHttpSendRequest", send), \
                        self.assertRaises(urllib.error.URLError):
                    self.fetch(True, scheme, timeout=1)
                self.assertTrue(send.called)
                self.assertEqual(self.proxy.requests, [])

    def test_not_bypassed_uses_proxy(self):
        with self.fetch(False) as resp:
            self.assertEqual(resp.read(), b"via-proxy")
        self.assertEqual([line.lower() for line in self.proxy.requests],
                         [f"get http://{self.host}:{self.port}/x.png http/1.1"])


class ProxyAuthTest(unittest.TestCase):
    """代理地址里带用户名密码时，交给 WinHTTP 的 HTTP Basic 会带在发给代理的 CONNECT 里（同 urllib 发
    Proxy-Authorization）；代理一律回 407 时按 HTTP 407 报。请求的主机名用本机的计算机名（走代理时不解析它）。"""

    def setUp(self):
        def handler(srv, client, index):
            if srv.read_request(client):
                client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")

        self.proxy = RawServer(handler)
        self.addCleanup(self.proxy.close)
        unused = socket.create_server(("127.0.0.1", 0))
        self.port = unused.getsockname()[1]
        unused.close()
        self.host = socket.gethostname().lower()
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in PROXY_VARS:
            os.environ.pop(name, None)

    def test_basic_credentials_and_407(self):
        # 用户名密码里带要 unquote 的字符（user:p%20w → 「user」「p w」），假代理要的正是 Basic base64("user:p w")
        os.environ["HTTPS_PROXY"] = f"http://user:p%20w@127.0.0.1:{self.proxy.port}"
        session = winhttp.Session()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                session(urllib.request.Request(f"https://{self.host}:{self.port}/x.png"), 5)
            caught.exception.close()
            self.assertEqual(caught.exception.code, 407)
        finally:
            session.close()
        connects = [head for head in self.proxy.heads if head.startswith("CONNECT")]
        self.assertEqual((len(connects), len(self.proxy.heads)), (1, 1))  # 回 407 就报错，不再重发
        expected = "Proxy-Authorization: Basic " + base64.b64encode(b"user:p w").decode("ascii")
        self.assertIn(expected, connects[0].split("\r\n"))


class ProxyAuthRedirectTest(unittest.TestCase):
    """经要认证的代理下载、遇到跳转（302）到别的主机时，跳过去的那一跳也带认证（同 urllib）：代理回 407 时在同一个请求
    句柄上重设凭据、再发一次（见 winhttp._once），不重走原地址、不重复附加头、每一跳最多一次、不死循环。请求的主机名用
    本机的计算机名（走代理时不解析它，跳到 other-<计算机名> 也不解析）。"""

    FIRST_REDIRECTS = True  # 第一跳带认证时回 302 跳到别的主机；False 时一律回 407（测没跳转的情形）
    REDIRECTED_AUTH_OK = True  # 跳过去那一跳带上认证能不能过；False 时照旧回 407（测代理不认这个用户名密码）

    def setUp(self):
        self.auth: list = []  # 每个请求里带的 Proxy-Authorization（没有时记 None）
        self.host = socket.gethostname().lower()
        self.target = 9  # 请求地址里的端口，只出现在发给代理的绝对地址里，不真的连

        def handler(srv, client, index):
            while True:
                line = srv.read_request(client)
                if line is None:
                    return
                target = line.split(" ")[1]  # 请求行里第 2 段是完整地址（走代理时）
                first = target.endswith("/first")  # 第一跳；其余的是跳过去那跳（/second）
                lines = srv.heads[-1].split("\r\n")  # read_request 只返回请求行，完整头在 srv.heads[-1]
                got = [x for x in lines if x.lower().startswith("proxy-authorization")]
                self.auth.append(got[0] if got else None)
                if not self.FIRST_REDIRECTS or not got or (not self.REDIRECTED_AUTH_OK and not first):
                    client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                   b"Proxy-Authenticate: Basic realm=\"x\"\r\nContent-Length: 0\r\n\r\n")
                elif first:
                    client.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://other-" + self.host.encode()
                                   + b":9/second\r\nContent-Length: 0\r\n\r\n")
                else:
                    reply(client, OK_BODY)

        self.proxy = RawServer(handler)
        self.addCleanup(self.proxy.close)
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in PROXY_VARS:
            os.environ.pop(name, None)
        os.environ["HTTPS_PROXY"] = f"http://user:pw@127.0.0.1:{self.proxy.port}"

    def session(self):
        session = winhttp.Session()
        self.addCleanup(session.close)
        return session

    def url(self, path, port=None):
        return f"http://{self.host}:{self.target if port is None else port}{path}"

    def unused_port(self):
        sock = socket.create_server(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def test_redirected_hop_gets_credentials(self):
        # 第一跳带认证 → 302 跳到别的主机；跳过去那跳不带认证 → 407；重设凭据重发 → 200，正文对
        with self.session()(urllib.request.Request(self.url("/first")), 5) as resp:
            self.assertEqual(resp.read(), OK_BODY)
        basic = "Proxy-Authorization: Basic " + base64.b64encode(b"user:pw").decode("ascii")
        self.assertEqual(self.auth, [basic, None, basic])  # 重发那跳带上了认证
        self.assertEqual([line.split(" ")[1] for line in self.proxy.requests],
                         [self.url("/first"), f"http://other-{self.host}:9/second",
                          f"http://other-{self.host}:9/second"])

    def test_redirected_hop_rejected_resent_once(self):
        # 跳过去那跳带了认证仍回 407（代理不认这个用户名密码）：重发只有一次，按 HTTP 407 报，不死循环
        self.REDIRECTED_AUTH_OK = False
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.session()(urllib.request.Request(self.url("/first")), 5)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 407)
        basic = "Proxy-Authorization: Basic " + base64.b64encode(b"user:pw").decode("ascii")
        self.assertEqual(self.auth, [basic, None, basic])
        self.assertEqual(len(self.proxy.requests), 3)

    def test_no_redirect_407_not_resent(self):
        # 没跳转、带了认证仍回 407：照旧只发一次，不重发
        self.FIRST_REDIRECTS = False
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.session()(urllib.request.Request(self.url("/x")), 5)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 407)
        self.assertEqual(len(self.proxy.requests), 1)

    def test_resent_request_headers_once(self):
        # 重发那跳里附加请求头每个只出现一次（重发不再传一遍，头已经在请求句柄上了）
        request = urllib.request.Request(self.url("/first"),
                                         headers={"User-Agent": assets.USER_AGENT, "X-Extra": "v"})
        with self.session()(request, 5) as resp:
            self.assertEqual(resp.read(), OK_BODY)
        resent = self.proxy.heads[-1].lower()
        self.assertEqual(resent.count("user-agent:"), 1)
        self.assertEqual(resent.count("x-extra:"), 1)

    def test_current_url_failure_reports_407(self):
        # 取现在的地址失败时照常报 407，不重发（不让下载因为这一步出错）
        with mock.patch.object(winhttp.winhttp, "WinHttpQueryOption", return_value=0):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.session()(urllib.request.Request(self.url("/first")), 5)
            caught.exception.close()
        self.assertEqual(caught.exception.code, 407)
        self.assertEqual(len(self.proxy.requests), 2)  # 第一跳、跳过去那跳；没有重发

    def test_bypass_no_credentials_no_resend(self):
        # 例外主机（proxy_bypass 为真）直连：不设凭据、不重发，假代理收不到请求
        setcred = mock.Mock(wraps=winhttp.winhttp.WinHttpSetCredentials)
        with mock.patch.object(winhttp.urllib.request, "proxy_bypass", return_value=True), \
                mock.patch.object(winhttp.winhttp, "WinHttpSetCredentials", setcred):
            with self.assertRaises(urllib.error.URLError):
                self.session()(urllib.request.Request(self.url("/first", self.unused_port())), 1)
        self.assertFalse(setcred.called)
        self.assertEqual(self.proxy.requests, [])

    def test_bypass_gets_request_host_with_port(self):
        # proxy_bypass 收到的照 urllib 是请求的主机名带端口（req.host），不是不带端口的主机名
        port = self.unused_port()
        seen = []
        with mock.patch.object(winhttp.urllib.request, "proxy_bypass",
                               side_effect=lambda host: seen.append(host) or True):
            with self.assertRaises(urllib.error.URLError):
                self.session()(urllib.request.Request(self.url("/first", port)), 1)
        self.assertEqual(seen, [f"{self.host}:{port}"])


class ProxySetupFailureTest(WinHttpCase):
    """给这个请求设直连、设代理的用户名密码失败时：按网络错误报，原因里没有用户名密码；请求不发出去，连接、请求句柄
    照样都关掉。"""

    def test_failures_close_handles(self):
        real = {name: getattr(winhttp.winhttp, name)
                for name in ("WinHttpConnect", "WinHttpOpenRequest", "WinHttpCloseHandle", "WinHttpSetOption")}

        def set_option(handle, option, buf, size):  # 只有设直连（WINHTTP_OPTION_PROXY）失败
            return 0 if option == winhttp.WINHTTP_OPTION_PROXY else real["WinHttpSetOption"](handle, option, buf, size)

        with mock.patch.object(winhttp.urllib.request, "getproxies",
                               return_value={"https": "http://user:secret@127.0.0.1:9"}):
            session = winhttp.Session()
        self.addCleanup(session.close)
        for bypass, failing in (
            (False, mock.patch.object(winhttp.winhttp, "WinHttpSetCredentials", return_value=0)),
            (True, mock.patch.object(winhttp.winhttp, "WinHttpSetOption", side_effect=set_option)),
        ):
            opened, closed = collections.Counter(), collections.Counter()

            def creating(name, opened=opened):
                def call(*args):
                    handle = real[name](*args)
                    if handle:
                        opened[handle] += 1
                    return handle
                return call

            def closing(handle, closed=closed):
                closed[handle] += 1
                return real["WinHttpCloseHandle"](handle)

            Handler.paths.clear()
            with self.subTest(bypass=bypass):
                with mock.patch.object(winhttp.urllib.request, "proxy_bypass", return_value=bypass), \
                        mock.patch.object(winhttp, "_last_error", return_value=87), \
                        mock.patch.object(winhttp.winhttp, "WinHttpConnect", side_effect=creating("WinHttpConnect")), \
                        mock.patch.object(winhttp.winhttp, "WinHttpOpenRequest",
                                          side_effect=creating("WinHttpOpenRequest")), \
                        mock.patch.object(winhttp.winhttp, "WinHttpCloseHandle", side_effect=closing), \
                        failing, self.assertRaises(urllib.error.URLError) as caught:
                    session(urllib.request.Request(self.server.url("/ok")), 5)
                self.assertNotIsInstance(caught.exception, urllib.error.HTTPError)
                self.assertEqual(caught.exception.reason, "WinHTTP 错误 87")
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(sum(opened.values()), 2)  # 一个连接句柄、一个请求句柄
                self.assertEqual(opened, closed)
                self.assertEqual(Handler.paths, [])


class DllLoadTest(unittest.TestCase):
    """winhttp.dll 只从 System32 加载（LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800），免得程序文件夹里被放一个同名的 DLL。"""

    def test_loaded_from_system32_only(self):
        tree = ast.parse(Path(winhttp.__file__).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "WinDLL"]
        self.assertEqual(len(calls), 1)
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in calls[0].keywords}
        self.assertTrue(kwargs.get("use_last_error"))
        self.assertEqual(kwargs.get("winmode"), 0x00000800)


class LiveProxyTest(unittest.TestCase):
    """环境变量里的代理真的用上：本机的假代理收到请求（http 的地址收到完整地址的 GET，https 的地址收到 CONNECT）。
    请求的主机名用本机的计算机名（见模块说明），走了代理时 WinHTTP 不解析它。"""

    def setUp(self):
        def handler(srv, client, index):
            line = srv.read_request(client)
            if line and line.startswith("CONNECT"):
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            elif line:
                reply(client, b"via-proxy")

        self.proxy = RawServer(handler)
        self.addCleanup(self.proxy.close)
        unused = socket.create_server(("127.0.0.1", 0))
        self.port = unused.getsockname()[1]  # 没人听的端口：没走代理时连不上
        unused.close()
        self.host = socket.gethostname().lower()
        patch = mock.patch.dict(os.environ, {}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        for name in PROXY_VARS:
            os.environ.pop(name, None)

    def test_environment_proxy_forms(self):
        for form in (f"http://127.0.0.1:{self.proxy.port}/", f"http://user:pw@127.0.0.1:{self.proxy.port}"):
            with self.subTest(form=form):
                os.environ["HTTPS_PROXY"] = form
                self.proxy.requests.clear()
                session = winhttp.Session()
                try:
                    with session(urllib.request.Request(f"http://{self.host}:{self.port}/x.png"), 10) as resp:
                        self.assertEqual(resp.read(), b"via-proxy")
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        session(urllib.request.Request(f"https://{self.host}:{self.port}/x.png"), 10)
                    caught.exception.close()
                    self.assertEqual(caught.exception.code, 502)
                finally:
                    session.close()
                self.assertEqual([line.lower() for line in self.proxy.requests],
                                 [f"get http://{self.host}:{self.port}/x.png http/1.1",
                                  f"connect {self.host}:{self.port} http/1.1"])


class ReleaseExclusionTest(unittest.TestCase):
    """发行版不带 OpenSSL：打包脚本不跟进 ssl、_ssl、_hashlib，不带 OpenSSL 的两个 DLL（按本机 Python 实际的文件名核对
    通配符）；程序用到的 http.client（IncompleteRead）、urllib.request（Request）只在 try 里导入 ssl，发行版里导入不了时
    它们照常能用。"""

    def test_openssl_excluded(self):
        from tools import build_release as br

        for name in ("ssl", "_ssl", "_hashlib"):
            with self.subTest(name=name):
                self.assertIn(name, br.NOFOLLOW)
        dlls = sorted(p.name for p in (Path(sys.base_prefix) / "DLLs").glob("*.dll")
                      if p.name.lower().startswith(("libssl", "libcrypto")))
        self.assertTrue(dlls, "本机 Python 的 DLLs 里没找到 OpenSSL")
        for name in dlls:
            with self.subTest(dll=name):
                self.assertTrue(any(fnmatch.fnmatch(name.lower(), p.lower()) for p in br.NOINCLUDE_DLLS))

    def test_stdlib_imports_ssl_only_in_try(self):
        for module in (http.client, urllib.request):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            guarded = {id(node) for t in ast.walk(tree) if isinstance(t, ast.Try) for node in t.body}
            found = [node for node in ast.walk(tree) if isinstance(node, ast.Import)
                     and any(alias.name == "ssl" for alias in node.names)]
            with self.subTest(module=module.__name__):
                self.assertTrue(found)
                self.assertEqual([node.lineno for node in found if id(node) not in guarded], [])


if __name__ == "__main__":
    unittest.main()
