"""识别图片下载的停止（advisor/assets.py 的 download、_get_image、_Fetcher、_KeepAlive）：退出程序时下载很快停下、
停得干净。

- 网络卡住（收下请求一直不回、回了一半不再发）时置停止标志：download() 不等卡住的请求，很快返回，没下完的算「已取消」；
  下载线程是守护线程（退出程序时不等它们）；卡住的请求后来断开了，下载线程也不再重连重试；不留下任何文件（包括半截的 .part）。
- 正文一直在慢慢地来（没卡住，只是慢）：按块读，每块之间看停止标志，下载线程自己很快收手，不等读完。
- 子进程里照退出程序的做法（下载在后台线程里，置停止标志后马上退出）：进程很快结束，不等卡住的请求超时。
- 平时照常：同一条连接接着下几张、按块读完正文、分块传输的正文；读到一半连接断了照旧按网络错误重试，最后算失败；
  下载线程建不了会话时每张按出错的原因算失败，不算「已取消」。

不联网：只连本机回环地址上自己起的假服务（FakeServer），走的是 WinHTTP（明文 http，本机的假服务不加密）。识别图片的
文件夹指到沙箱里的临时文件夹。
"""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from advisor import assets, paths, winhttp
from tests.support import qtenv, sandbox
from tests.support.waits import wait_until

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 20  # 秒：测试里请求的超时。停止要在 STOP_LIMIT 内生效，和超时无关；超时设得不长，改坏了也不会等太久
# 秒：置停止标志以后 download() 最晚多久返回。程序里约半秒（STOP_POLL＋STOP_GRACE）；几个测试进程同时跑、机器很忙时线程
# 醒得晚一些，上限放宽，但和要等卡住的请求超时（TIMEOUT 秒以上）分得很开
STOP_LIMIT = 2.0
EXIT_LIMIT = 8.0  # 秒：子进程里置停止标志以后，进程最晚多久退出（等请求超时、重试再超时要约两倍的 TIMEOUT）
REPLY = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\n\r\n"


def setUpModule():
    qtenv.app()  # wait_until 要处理 Qt 的事件


def png(size: int) -> bytes:
    return assets.PNG_MAGIC + bytes(i % 251 for i in range(size))


class FakeServer:
    """本机回环地址上的假图片服务（不联网，明文 http）。mode：
    - head：收下请求，一直不回（网络卡住）；
    - body：回了响应头（声明 100000 字节）和开头几个字节以后不再发；
    - trickle：回了响应头（声明 10 MB）以后正文一点一点地发，每次 512 字节，一直发到连接断开；
    - serve：照常回 body（声明长度，长连接：同一个连接上接着回下一个请求）；
    - chunked：同 serve，正文分块传输（Transfer-Encoding: chunked），每块 10000 字节；
    - short：声明的长度比 body 多 1000 字节，发完 body 就断开（读到一半连接断了）。
    connections 接受了几个连接，requests 收到几个请求，sent 发出的正文字节数。drop() 断开现有的连接（还接受新的连接），
    close() 连监听一起关。"""

    def __init__(self, mode: str, body: bytes = b""):
        self.mode, self.body = mode, body
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.lock = threading.Lock()
        self.clients: list[socket.socket] = []
        self.connections = self.requests = self.sent = 0
        self.closed = threading.Event()
        threading.Thread(target=self._accept, name="fake-server", daemon=True).start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.clients.append(client)
                self.connections += 1
            threading.Thread(target=self._serve, args=(client,), name="fake-client", daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        buf = b""
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    data = client.recv(65536)
                    if not data:
                        return
                    buf += data
                _, _, buf = buf.partition(b"\r\n\r\n")
                with self.lock:
                    self.requests += 1
                if self.mode == "head":
                    return  # 不回；连接留着，drop() 时才断开
                if self.mode == "body":
                    client.sendall(REPLY % 100000 + assets.PNG_MAGIC + b"xx")
                    return
                if self.mode == "trickle":
                    client.sendall(REPLY % (10 << 20) + assets.PNG_MAGIC)
                    while not self.closed.wait(0.02):  # 一直慢慢地发，直到连接断开（发不出去）或关掉服务
                        client.sendall(b"x" * 512)
                        with self.lock:
                            self.sent += 512
                    return
                if self.mode == "short":
                    client.sendall(REPLY % (len(self.body) + 1000) + self.body)
                    client.shutdown(socket.SHUT_RDWR)
                    return
                if self.mode == "chunked":
                    parts = [self.body[i:i + 10000] for i in range(0, len(self.body), 10000)]
                    client.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nTransfer-Encoding: chunked\r\n\r\n"
                                   + b"".join(b"%x\r\n%s\r\n" % (len(p), p) for p in parts) + b"0\r\n\r\n")
                else:  # serve
                    client.sendall(REPLY % len(self.body) + self.body)
                with self.lock:
                    self.sent += len(self.body)
        except OSError:
            return

    def drop(self) -> None:
        with self.lock:
            clients, self.clients = self.clients, []
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()

    def close(self) -> None:
        self.closed.set()
        self.sock.close()
        self.drop()


class Run:
    """在后台线程里下载（同悬浮窗：下载在后台线程里，界面线程置停止标志）。"""

    def __init__(self, items, stop, workers: int = 2):
        self.result = None
        self.thread = threading.Thread(target=self._run, args=(items, stop, workers), name="test-download", daemon=True)
        self.thread.start()

    def _run(self, items, stop, workers):
        self.result = assets.download(items, stop=stop, workers=workers)

    def wait(self, seconds: float) -> bool:
        self.thread.join(seconds)
        return not self.thread.is_alive()


def files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


class DownloadStopTest(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-dlstop-")
        for name in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):  # 本机测试不经过代理
            if name in os.environ:
                self.addCleanup(os.environ.__setitem__, name, os.environ[name])
                del os.environ[name]
        for patcher in (
            mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind),
            mock.patch.object(assets, "TIMEOUT", TIMEOUT),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.before = set(threading.enumerate())

    def server(self, mode: str, body: bytes = b"") -> FakeServer:
        srv = FakeServer(mode, body)
        self.addCleanup(srv.close)
        return srv

    def item(self, srv: FakeServer, name: str, data: bytes = b"", size: int = 100000) -> assets.AssetItem:
        sha1 = hashlib.sha1(data).hexdigest() if data else ""
        return assets.AssetItem("chips", name, name, srv.url(name), len(data) if data else size, sha1)

    def workers(self) -> list[threading.Thread]:
        """这个测试里 download() 起的下载线程。"""
        return [t for t in threading.enumerate() if t not in self.before and t.name.startswith("assets")]

    def stop_while_stuck(self, mode: str) -> None:
        """两张图的请求都卡住时置停止标志（见 test_stop_while_waiting_for_reply 等）。"""
        srv = self.server(mode)
        items = [self.item(srv, f"x{i}.png") for i in range(2)]
        stop = threading.Event()
        run = Run(items, stop)
        self.assertTrue(wait_until(lambda: srv.requests >= 2, 20000), "两张图的请求没有都发出去")
        # 假服务收到请求的时候，下载线程可能还没卡住：收到回话以后、读正文之前还会看一次停止标志，这时已经置位的话它
        # 自己就收手了，测不到「卡在网络上」的情形。稍等一下，让它们都卡在网络上再置停止标志
        time.sleep(0.2)
        start = time.monotonic()
        stop.set()
        self.assertTrue(run.wait(10), "置了停止标志，download() 还没返回")
        self.assertLess(time.monotonic() - start, STOP_LIMIT)
        ok, failed = run.result
        self.assertEqual(ok, [])
        self.assertEqual(sorted(failed), sorted((item.name, assets.CANCELLED) for item in items))
        self.assertEqual(files(self.base), [])
        workers = self.workers()
        self.assertTrue(workers, "下载线程应当还卡在请求里")
        self.assertTrue(all(t.daemon for t in workers), "下载线程要是守护线程：退出程序时不等它们")
        connections = srv.connections
        srv.drop()  # 卡住的请求这时才断开：下载线程见到已经取消，不再重连重试，自己收尾
        for t in workers:
            t.join(10)
        self.assertEqual([t.name for t in workers if t.is_alive()], [])
        self.assertEqual(srv.connections, connections)
        self.assertEqual(files(self.base), [])

    def test_stop_while_waiting_for_reply(self):
        """请求发出去以后对方一直不回（断网、代理卡死）：置停止标志后 download() 很快返回，两张都算「已取消」，不留文件；
        下载线程是守护线程；卡住的请求后来断开了，不再重连重试。"""
        self.stop_while_stuck("head")

    def test_stop_while_reading_body(self):
        """正文读到一半对方不再发：同上。取消时要是从别的线程关连接，要等这次读超时才关得掉，download() 会跟着被卡住。"""
        self.stop_while_stuck("body")

    def test_stop_between_chunks(self):
        """正文一直在慢慢地来（没卡住，只是慢）：按块读，每块之间看停止标志，下载线程自己很快收手（不等服务断开），
        不留半截文件。"""
        srv = self.server("trickle")
        stop = threading.Event()
        run = Run([self.item(srv, "slow.png", size=10 << 20)], stop, workers=1)
        self.assertTrue(wait_until(lambda: srv.sent >= 4096, 20000), "正文没有开始发")
        start = time.monotonic()
        stop.set()
        self.assertTrue(run.wait(10))
        workers = self.workers()
        for t in workers:
            t.join(10)
        self.assertEqual([t.name for t in workers if t.is_alive()], [])
        self.assertLess(time.monotonic() - start, STOP_LIMIT * 2)
        self.assertEqual(run.result, ([], [("chips/slow.png", assets.CANCELLED)]))
        self.assertEqual(files(self.base), [])
        self.assertEqual(srv.connections, 1)

    def test_keep_alive_reads_whole_body(self):
        """平时照常：同一条连接接着下三张（复用连接），正文比一块大也按块读完，校验对得上就存好，不留 .part。"""
        data = png(300_000)
        srv = self.server("serve", data)
        items = [self.item(srv, f"a{i}.png", data) for i in range(3)]
        ok, failed = assets.download(items, workers=1)
        self.assertEqual((sorted(ok), failed), (sorted(item.name for item in items), []))
        for item in items:
            self.assertEqual(item.path.read_bytes(), data)
        self.assertEqual(srv.connections, 1)
        self.assertEqual(files(self.base), sorted(item.name for item in items))
        self.assertEqual([t.name for t in self.workers() if t.is_alive()], [])  # 都下完了：下载线程也都结束了

    def test_chunked_body(self):
        """正文分块传输（没有声明长度）：照样读完、存好。"""
        data = png(45_000)
        srv = self.server("chunked", data)
        item = self.item(srv, "c.png", data)
        self.assertEqual(assets.download([item], workers=1), ([item.name], []))
        self.assertEqual(item.path.read_bytes(), data)

    def test_connection_cut_mid_body(self):
        """读到一半连接断了（声明的长度没读够）：按网络错误重试，最后算失败，不存半截文件。个别图片下载失败照常记进
        本地覆盖清单（隔天再试，见 tests/test_download_fail.py），图片和临时文件都不留。"""
        data = png(5000)
        srv = self.server("short", data)
        item = self.item(srv, "s.png", data)
        ok, failed = assets.download([item], sleep=lambda s: None, workers=1)
        self.assertEqual(ok, [])
        self.assertEqual([name for name, _ in failed], [item.name])
        self.assertTrue(failed[0][1].startswith("网络错误"), failed)
        self.assertIn("IncompleteRead", failed[0][1])
        self.assertEqual(srv.requests, assets.RETRIES)
        self.assertEqual(files(self.base), [assets.LOCAL_MANIFEST])
        self.assertEqual(list(assets.load_local()["failed"]), [item.name])

    def test_connection_setup_error(self):
        """下载线程建不了会话（例如系统里的证书或代理设置读不了）：每张按出错的原因算失败（以 NO_SESSION 开头，后接出错的
        原文），不当成「已取消」；不发请求、不留文件（不是哪一张图的问题，不记进本地覆盖清单，见 tests/test_download_fail.py），
        下载线程都结束。"""
        srv = self.server("serve", png(1000))
        items = [self.item(srv, f"e{i}.png") for i in range(3)]
        with mock.patch.object(winhttp.Session, "__init__", side_effect=OSError("读不了系统的证书")):
            ok, failed = assets.download(items, workers=2)
        self.assertEqual(ok, [])
        self.assertEqual(sorted(name for name, _ in failed), sorted(item.name for item in items))
        self.assertTrue(all(reason == f"{assets.NO_SESSION}：OSError: 读不了系统的证书" for _, reason in failed), failed)
        self.assertEqual(srv.connections, 0)
        self.assertEqual(files(self.base), [])
        self.assertEqual([t.name for t in self.workers() if t.is_alive()], [])


# 子进程里照退出程序的做法走一遍：下载在后台线程里（悬浮窗的 run_background），两张图的请求都卡住时置停止标志
# （stop_download），随即 sys.exit。参数：项目根目录、识别图片的临时文件夹。
CHILD = r"""
import os, socket, sys, threading, time
from pathlib import Path

root, work = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, root)
from advisor import assets, paths

server = socket.create_server(("127.0.0.1", 0))  # 本机的假服务：收下请求，一直不回
requests = []


def serve(client):
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                return
            data += chunk
        requests.append(client)
    except OSError:
        pass


def accept():
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return
        threading.Thread(target=serve, args=(client,), daemon=True).start()


threading.Thread(target=accept, daemon=True).start()
paths.assets_dir = lambda kind="": work / kind
for name in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
    os.environ.pop(name, None)
assets.TIMEOUT = %(timeout)d
port = server.getsockname()[1]
items = [assets.AssetItem("chips", f"x{i}.png", f"x{i}.png", f"http://127.0.0.1:{port}/x{i}.png", 100, "")
         for i in range(2)]
stop = threading.Event()
threading.Thread(target=assets.download, args=(items, None, stop), daemon=True).start()
deadline = time.monotonic() + 30
while len(requests) < 2 and time.monotonic() < deadline:
    time.sleep(0.01)
print("stopping", len(requests), flush=True)
stop.set()
sys.exit(0)
"""


class ExitTest(unittest.TestCase):
    def test_process_exits_promptly(self):
        """下载卡在网络上时退出程序：置停止标志后进程很快结束（不等卡住的请求超时、再重试一次又超时，那要约两倍的超时）。"""
        work = sandbox.temp_dir(self, "test-dlexit-")
        proc = subprocess.Popen([sys.executable, "-B", "-c", CHILD % {"timeout": TIMEOUT}, str(ROOT), str(work)],
                                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
        self.addCleanup(self._end, proc)
        line = proc.stdout.readline()
        start = time.monotonic()
        try:
            code = proc.wait(timeout=TIMEOUT * 3)
        except subprocess.TimeoutExpired:
            code = None
        elapsed = time.monotonic() - start
        detail = f"{line!r} {proc.stderr.read() if code is not None else ''}"
        self.assertEqual(line.split(), ["stopping", "2"], detail)
        self.assertEqual(code, 0, detail)
        self.assertLess(elapsed, EXIT_LIMIT, detail)
        self.assertEqual(files(work), [])

    @staticmethod
    def _end(proc: subprocess.Popen) -> None:
        """结束这个测试自己起的子进程（还没退出时），关掉管道。"""
        if proc.poll() is None:
            proc.kill()
            proc.wait(30)
        for stream in (proc.stdout, proc.stderr):
            stream.close()


if __name__ == "__main__":
    unittest.main()
