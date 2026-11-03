"""识别图片的下载用时统计（advisor/assets.py 的 download 的 stats）。

- 给了 stats 时：张数、总秒数、最慢的 5 张（按秒数从慢到快，不足 5 张时有几张写几张）都填对；慢速下载时记下连接数和
  slow；同一张下过两遍的用时加在一起；问接口那一步直接收下的图片也有字节数。
- 取消、被站点防护拦下时也填，没在下载的（没有字节数、用时不到 0.1 秒）不算最慢的那几张；每张都有了结果以后才置上停止
  标志的不算提前停下（paused 为假）；不给 stats 时返回值不变。

不联网：下载换成假的 opener，钟换成假的（每张的用时由假的回话拨钟决定）；识别图片的文件夹指到沙箱里的临时文件夹。
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import unittest
import urllib.error
from unittest import mock

from advisor import assets, paths
from tests.support import sandbox

T0 = 1_800_000_000.0


def png(name: str) -> bytes:
    return assets.PNG_MAGIC + name.encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Web:
    """假的 opener：按网址的开头回内容；值可以是 bytes、异常或函数（函数里可以拨假钟、置停止标志）。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url
        with self.lock:
            self.calls.append(url)
        value = next((v for k, v in self.routes.items() if url.startswith(k)), http_error(404))
        if callable(value) and not isinstance(value, BaseException):
            value = value(url)
        if isinstance(value, BaseException):
            raise value
        return io.BytesIO(value)


class DownloadStatsTest(unittest.TestCase):
    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-dlstats-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()

    def item(self, name: str, kind: str = "heroes") -> assets.AssetItem:
        data = png(name)
        return assets.AssetItem(kind, name, name, f"https://img/{name}", len(data), hashlib.sha1(data).hexdigest())

    def run_download(self, items, routes: dict, **kw) -> tuple[tuple[list, list], dict]:
        stats: dict = {}
        kw.setdefault("workers", 1)
        result = assets.download(items, opener=Web(routes), sleep=lambda seconds: None, clock_now=self.clock,
                                 stats=stats, **kw)
        return result, stats

    def slow_route(self, name: str, seconds: float):
        """回这张图的内容，回话之前把假钟拨过 seconds 秒（模拟这一张下了这么久）。"""

        def route(url: str) -> bytes:
            self.clock.t += seconds
            return png(name)

        return route

    def test_fills_counts_seconds_and_slowest_five(self):
        """7 张都下好：张数、总秒数（墙上时间）填对；最慢的 5 张按秒数从慢到快，每张带 dir/file、秒数、字节数。"""
        names = ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png", "g.png"]
        seconds = [3.0, 1.0, 2.0, 0.5, 4.0, 1.5, 0.2]
        items = [self.item(name) for name in names]
        routes = {item.url: self.slow_route(name, sec) for item, name, sec in zip(items, names, seconds)}
        (ok, failed), stats = self.run_download(items, routes)
        self.assertEqual((sorted(ok), failed), (sorted(item.name for item in items), []))
        self.assertEqual(stats["total"], 7)
        self.assertEqual((stats["ok"], stats["failed"]), (7, 0))
        self.assertFalse(stats["blocked"])
        self.assertFalse(stats["paused"])
        self.assertEqual((stats["threads"], stats["slow"]), (1, False))
        self.assertAlmostEqual(stats["seconds"], sum(seconds), places=1)
        self.assertEqual([row["name"] for row in stats["slowest"]],
                         ["heroes/e.png", "heroes/a.png", "heroes/c.png", "heroes/f.png", "heroes/b.png"])
        self.assertEqual([row["s"] for row in stats["slowest"]], [4.0, 3.0, 2.0, 1.5, 1.0])
        self.assertEqual(stats["slowest"][0]["bytes"], len(png("e.png")))
        self.assertNotIn("url", stats["slowest"][0])

    def test_slowest_when_fewer_than_five(self):
        """不足 5 张时有几张写几张（这里 2 张）。"""
        items = [self.item("a.png"), self.item("b.png")]
        routes = {items[0].url: self.slow_route("a.png", 1.0), items[1].url: self.slow_route("b.png", 2.0)}
        (ok, _), stats = self.run_download(items, routes)
        self.assertEqual(len(ok), 2)
        self.assertEqual(stats["slowest"], [
            {"name": "heroes/b.png", "s": 2.0, "bytes": len(png("b.png"))},
            {"name": "heroes/a.png", "s": 1.0, "bytes": len(png("a.png"))},
        ])

    def test_two_passes_added_together(self):
        """同一张下了两遍（旧地址 404，问过接口按新地址再下）：两遍的用时加在一起算这一张的（这里 1 秒 + 2 秒，排在只下了
        一遍、2.5 秒的那张前面），字节数是第二遍写好的文件的；问接口的那一下不算在哪一张上。"""
        a, b = self.item("a.png"), self.item("b.png")
        data = png("a.png")

        def gone(url: str):
            self.clock.t += 1.0
            raise http_error(404)

        answer = json.dumps({"query": {"pages": [{"title": "File:a.png", "imageinfo": [
            {"url": "https://img/new/a.png", "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}]}]}}).encode()
        routes = {assets.API: answer, a.url: gone, "https://img/new/a.png": self.slow_route("a.png", 2.0),
                  b.url: self.slow_route("b.png", 2.5)}
        (ok, failed), stats = self.run_download([a, b], routes)
        self.assertEqual((sorted(ok), failed), (sorted([a.name, b.name]), []))
        self.assertEqual([(row["name"], row["s"]) for row in stats["slowest"]],
                         [("heroes/a.png", 3.0), ("heroes/b.png", 2.5)])
        self.assertEqual(stats["slowest"][0]["bytes"], len(data))
        self.assertAlmostEqual(stats["seconds"], 5.5, places=1)

    def test_cancel_still_fills(self):
        """取消：没下的算「已取消」，统计照样填（paused 为真），没有开始过的不进最慢的那几张。"""
        items = [self.item("a.png"), self.item("b.png")]
        stop = threading.Event()
        stop.set()
        (ok, failed), stats = self.run_download(items, {"https://img/": png("x")}, stop=stop)
        self.assertEqual(ok, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual((stats["total"], stats["ok"], stats["failed"]), (2, 0, 2))
        self.assertTrue(stats["paused"])
        self.assertFalse(stats["blocked"])
        self.assertLessEqual(len(stats["slowest"]), 2)  # 没开始过的不记；开始了、没写完的没有字节数
        self.assertTrue(all("bytes" not in row for row in stats["slowest"]), stats["slowest"])

    def test_blocked_still_fills(self):
        """被站点防护拦下：统计照样填（blocked、paused 都为真），没下的算暂停、不进最慢的那几张。"""
        items = [self.item("a.png"), self.item("b.png")]
        routes = {items[0].url: http_error(567), "https://img/": png("x")}
        (ok, failed), stats = self.run_download(items, routes)
        self.assertEqual(ok, [])
        self.assertTrue(all(assets.is_paused(reason) for _, reason in failed), failed)
        self.assertTrue(stats["blocked"])
        self.assertTrue(stats["paused"])
        self.assertEqual((stats["total"], stats["ok"], stats["failed"]), (2, 0, 2))
        self.assertEqual([row["name"] for row in stats["slowest"]], [])  # 刚开始就被拦下：没在下载，不算最慢的

    def test_accepted_after_refresh_has_bytes(self):
        """旧地址下到的内容和清单对不上（wiki 上的图片更新过），问过接口后按新的校验值直接收下、不再下第二遍：这一张照样有
        字节数，是写好的新图片的（问接口那一步不另算用时）。"""
        a = self.item("a.png")  # 清单记的是旧图片的大小和校验值
        data = png("a.png（更新过）")
        answer = json.dumps({"query": {"pages": [{"title": "File:a.png", "imageinfo": [
            {"url": a.url, "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}]}]}}).encode()

        def updated(url: str) -> bytes:
            self.clock.t += 1.5
            return data

        web = Web({assets.API: answer, a.url: updated})
        stats: dict = {}
        ok, failed = assets.download([a], opener=web, sleep=lambda seconds: None, clock_now=self.clock, workers=1,
                                     stats=stats)
        self.assertEqual((ok, failed), ([a.name], []))
        self.assertEqual(sum(url.startswith(a.url) for url in web.calls), 1)  # 收下的是第一遍下到的，没有再下
        self.assertTrue(any(url.startswith(assets.API) for url in web.calls))
        self.assertEqual(a.path.read_bytes(), data)
        self.assertEqual([(row["name"], row["s"], row.get("bytes")) for row in stats["slowest"]],
                         [("heroes/a.png", 1.5, len(data))])

    def test_stop_after_all_handled_not_paused(self):
        """每张都有了结果以后才置上停止标志（例如刚下完就退出）：不算提前停下，paused 为假；有一张是它自己的失败（不带页面的
        403）时也一样。还有没下的就置上了停止标志时照旧为真（test_cancel_still_fills）。"""
        for case in ("all_ok", "own_failure"):
            with self.subTest(case=case):
                items = [self.item(f"{case}_a.png"), self.item(f"{case}_b.png")]
                routes = {items[0].url: png(items[0].file),
                          items[1].url: png(items[1].file) if case == "all_ok" else http_error(403)}
                stop = threading.Event()

                def progress(done: int, total: int, name: str, stop=stop) -> None:
                    if done == total:
                        stop.set()

                (ok, failed), stats = self.run_download(items, routes, stop=stop, progress=progress)
                self.assertTrue(stop.is_set())
                self.assertEqual(len(ok), 2 if case == "all_ok" else 1)
                self.assertEqual(len(failed), 0 if case == "all_ok" else 1)
                for _, why in failed:  # 这一张自己的失败，不是取消、暂停
                    self.assertIn("403", why)
                    self.assertNotEqual(why, assets.CANCELLED)
                self.assertFalse(stats["paused"])
                self.assertFalse(stats["blocked"])

    def test_slow_mode_reported(self):
        """冷却记录还在时按慢速下：记下连接数是 SLOW_WORKERS、slow 为真。"""
        (self.base / assets.COOLDOWN_FILE).write_text(json.dumps(
            {"until": T0 + 600, "minutes": 35, "at": T0, "reason": "HTTP 567"}), encoding="utf-8")
        items = [self.item("a.png")]
        (ok, failed), stats = self.run_download(items, {items[0].url: png("a.png")})
        self.assertEqual((ok, failed), ([items[0].name], []))
        self.assertEqual((stats["threads"], stats["slow"]), (assets.SLOW_WORKERS, True))

    def test_without_stats_same_result(self):
        """不给 stats 时返回值不变（不传这个参数也不会出错）。"""
        items = [self.item("a.png")]
        result = assets.download(items, opener=Web({items[0].url: png("a.png")}), sleep=lambda seconds: None,
                                 clock_now=self.clock)
        self.assertEqual(result, ([items[0].name], []))


if __name__ == "__main__":
    unittest.main()
