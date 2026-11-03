"""识别图片的下载被站点防护拦下（advisor/assets.py 模块说明「被拦就全部停下」「冷却」）。

- 认出被拦：HTTP 567；拿到网页（拦截页）而不是图片也算（状态码 200、带页面的 403），不去问接口；不带页面的 403 单独一次
  只算那一张失败，连着两次按被拦算；限流（429、503）先按 Retry-After 等（没写时 60 秒），连着两次、要求等得太久按被拦算；
  Retry-After 写成 HTTP 日期也认。同一阵被拒只算一次：6 条连接上一起发出去、一起回 429 的只按单独一次限流等过再下，一起回
  403 的各算那一张失败；之后新发出去的又被拒才按被拦算；同一阵里有 567、拦截页面的照旧一次就停。
- 全部停下：一条连接认出被拦，别的连接马上停，之后不再有请求；没下的算暂停（is_paused），已经下好的照常留着。
- 冷却记录：写得进、重启以后照样认（每次都从文件读）；到点以后按慢速下（1 条连接，每次隔 1 秒）；再被拦翻倍、最多 3 小时，
  上一次被拦太久以前的从头算；Retry-After 更长时按它；下全了清掉；格式不对的不认，时刻离谱的按冷却分钟数算。
- 冷却期间再下载（悬浮窗上点「重试」）只发一个请求：仍被拒、没得到回话时不改冷却记录；通了就按慢速接着下完，下全了清掉。
- 冷却记录记着认出被拦的是哪一张：接着下时它排到最后，试探请求也不用它；某一张一直回网页、另一个 wiki 上的那张一直被拦时，
  别的图照样下到，冷却照旧翻倍。
- 接口合批：内容对不上、404 的攒起来，50 张一批问接口，请求之间隔 2 秒；对得上新校验值的不再下载；接口被拦一样整体停下。
  三段（先下一遍、问接口、按新地址再下）之间也隔开。
- 平时的疏密：整个一轮每秒最多开始 RATE 个请求（各条连接、各段合在一起算：领到的时刻两两相隔不小于 1/RATE，每个请求都不早于
  领到的时刻开始），同时最多 WORKERS 条连接；每张图都要等一会儿
  才回话时，几条连接把等待叠起来，一轮下完接近「张数 ÷ 每秒上限」；等请求时刻的时候取消，马上收手；限流时领了时刻、还在等的
  也一起等；6 条连接里一条认出被拦，别的连接上已经发出去的回话到了也不写文件，正等着时刻的不再发；被拦过以后照旧 1 条连接、
  每次隔 1 秒，冷却期间照旧只试一个请求。接口和开发用的取图工具用的取图器照旧按固定间隔，不按每秒的上限。
- 取消：限流等待中取消（退出程序），照旧约 STOP_POLL＋STOP_GRACE 秒内返回，不记冷却。
- 下载的先后：角色照、Q 版头像、筹码图标。
- 悬浮窗：被拦时下载那一行的写法（几点几分接着下、其余几张只数到点要下的、不说检查网络、⚠，「重试」的悬停说明）、识别
  不暂停、错误记录；冷却记录写不进时照 35 分钟算、进到整分；启动时还在冷却不下载；到点自动接着下（假的钟和计时器）；冷却
  期间点「重试」交给下载模块，10 秒内连点只试一次；拒绝过以后接着下时识别不暂停；冷却期间退出不用等下载。
- 下载失败那一行点「重试」、这一下正好被拦（个别下载失败记了今天的、wiki 上已删除记了日期的）：「其余 N 张」算上点过
  「重试」的那几张，到点真的接着下，下好了这一行消失、还下不了回到下载失败那一行；中途重启、当天冷却结束后照样接着下；
  覆盖清单写不进时不出错、这次运行里照样接着下。没点「重试」的（启动时、冷却到点）照旧不下近期记了号的，冷却期间点「重试」
  照旧只试一个请求。这几条用真的下载模块（只有取图、钟和计时器是假的）。

不联网：下载换成假的 opener（同 tests/test_release.py 的 FakeWeb），识别图片的文件夹指到沙箱里的临时文件夹；等待大多换成
记下来、不真等的 sleep，钟换成假的（量疏密、一起等限流的几条真的等，见 PaceTest；疏密按领到的时刻核对，机器忙时线程醒得晚
也不误报，见 Claims）。悬浮窗离屏，下载模块换成假的（点「重试」正好被拦的那几条用真的下载模块，见 RetryBlockedTest）。
"""
from __future__ import annotations

import email.utils
import hashlib
import io
import json
import math
import os
import threading
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from unittest import mock

from advisor import assets, clock, fileio, paths
from advisor.data import load_db
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import background as background_mod
from advisor.ui.overlay_parts import notices
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.wording import (ASSETS_TIP_BLOCKED, ASSETS_TIP_PAUSED, ASSETS_TIP_SLOW, BLOCKED_BIT,
                                              DOWNLOAD_BLOCKED, DOWNLOAD_BLOCKED_RETRY, DOWNLOAD_DONE, NO_DOWNLOAD_ENV,
                                              resume_time)
from advisor.ui.services import Services
from tests.support import sandbox
from tests.support.errors import capture_errors
from tests.support.overlay_cases import FakeWatcher, tip_text  # 导入时建好 QApplication

T0 = 1_800_000_000.0  # 假的钟的起点（整分）
STOP_LIMIT = 2.0  # 秒：置停止标志以后 download() 最晚多久返回（程序里约半秒，机器忙时放宽，同 tests/test_download_stop.py）
# 秒：真的等的几条里，请求开始的时刻可以比约好的时刻（领到的时刻、限流等到的时刻、间隔到的时刻）早这么多（计时的舍入；带时限
# 的等待只会醒得晚）。醒得晚、机器忙时晚得多，只会让请求更稀，不留余量：量疏密按领到的时刻核对（Claims），不按相邻两个请求
# 实际开始的时刻相隔多久
EARLY = 0.005
# 图片所在的另一个 wiki 的接口（清单项的 api）：现在的清单里没有这样的项，下载照样支持
OTHER_API = "https://other-wiki.example/w/api.php"


def png(payload: bytes) -> bytes:
    return assets.PNG_MAGIC + payload


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", headers or {}, io.BytesIO(b""))


class Web:
    """假的 opener：按网址的开头回内容。值可以是 bytes、异常、按次序取的列表（取到最后一个就一直用它），或者函数（收网址、
    回 bytes 或异常）。记下每次请求的网址和发请求的线程。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.times: list[float] = []  # 每次请求开始的时刻（time.monotonic）
        self.threads: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url
        with self.lock:
            self.calls.append(url)
            self.times.append(time.monotonic())
            self.threads.append(threading.current_thread().name)
            key = next((k for k in self.routes if url.startswith(k)), None)
            value = self.routes[key] if key is not None else http_error(404)
            if isinstance(value, list):
                value = value.pop(0) if len(value) > 1 else value[0]
        if callable(value) and not isinstance(value, BaseException):
            value = value(url)
        if isinstance(value, BaseException):
            raise value
        return Resp(value)

    def api_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith((assets.API, OTHER_API))]


def titles_of(url: str) -> list[str]:
    """imageinfo 请求里问的文件页名（不带「File:」）。"""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    return [t.removeprefix("File:") for t in query["titles"][0].split("|")]


def imageinfo(infos: dict):
    """假的 imageinfo 接口：infos 是 {文件页名: (地址, 内容)}，问到的里面有的照回，没有的当作已删除。"""

    def answer(url: str) -> bytes:
        pages = []
        for title in titles_of(url):
            if title in infos:
                link, data = infos[title]
                pages.append({"title": "File:" + title, "imageinfo": [{"url": link, "size": len(data), "sha1": sha1(data)}]})
            else:
                pages.append({"title": "File:" + title, "missing": True})
        return json.dumps({"query": {"pages": pages}}).encode()

    return answer


def by_name(url: str) -> bytes:
    """假的图床：回网址里那个文件名的图片（同 AssetsCase.item 默认的内容）。"""
    return png(url.rsplit("/", 1)[1].encode())


class Claims:
    """记下一轮下载里 _Pace 领出去的时刻（claim：哪个下载线程、领到哪个时刻）和按固定间隔下的请求记进去的开始时刻
    （started）。领到的时刻在锁里算，不看线程醒得早晚，量疏密按它核对、不留余量；请求实际开始得晚（机器忙时线程醒得晚）只会
    更稀。with 里生效。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.got: list[float] = []  # 领到的时刻，按领的先后
        self.marks: list[float] = []  # 按固定间隔下的请求记进去的开始时刻（_Pace.started）
        self._mine: dict[threading.Thread, float] = {}  # 各下载线程最近领到的时刻
        claim, started = assets._Pace.claim, assets._Pace.started

        def claimed(pace):
            at = claim(pace)
            with self.lock:
                self.got.append(at)
                self._mine[threading.current_thread()] = at
            return at

        def marked(pace, when):
            started(pace, when)
            with self.lock:
                self.marks.append(when)

        self._patches = (mock.patch.object(assets._Pace, "claim", claimed),
                         mock.patch.object(assets._Pace, "started", marked))

    def __enter__(self) -> Claims:
        for patcher in self._patches:
            patcher.start()
        return self

    def __exit__(self, *exc) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()

    def current(self) -> float | None:
        """这个下载线程最近领到的时刻：发请求的那一刻取，就是这个请求领到的（等的时候被限流、重新领过的，是最后领的那个）。"""
        with self.lock:
            return self._mine.get(threading.current_thread())


class Span(NamedTuple):
    """Timed 记下的一个请求。"""

    url: str
    start: float  # 开始的时刻（time.monotonic）
    end: float
    claim: float | None  # 这个请求领到的时刻（Claims）；不按每秒的上限领时刻的（问接口、慢速）、没给 Claims 时为 None


class Timed:
    """包在假的 opener 外面：记下每个请求开始、结束的时刻（time.monotonic）、领到的时刻（给了 claims 时）和同时在路上的
    请求最多有几个。delay：每个请求回话之前等多久（秒，像图床节点上没有缓存、回源站取图）。"""

    def __init__(self, web: Web, delay: float = 0.0, claims: Claims | None = None):
        self.web, self.delay, self.claims = web, delay, claims
        self.lock = threading.Lock()
        self.spans: list[Span] = []
        self.busy = self.peak = 0

    def __call__(self, req, timeout=None):
        start = time.monotonic()
        claim = self.claims.current() if self.claims is not None else None
        with self.lock:
            self.busy += 1
            self.peak = max(self.peak, self.busy)
        try:
            if self.delay:
                time.sleep(self.delay)
            return self.web(req, timeout)
        finally:
            with self.lock:
                self.busy -= 1
                self.spans.append(Span(req.full_url, start, time.monotonic(), claim))

    def ordered(self) -> list[Span]:
        """各请求，按开始的先后排好。"""
        with self.lock:
            return sorted(self.spans, key=lambda span: span.start)


def gaps(starts: list[float]) -> list[float]:
    """相邻两个请求开始的时刻相隔多久（starts 按先后排好）。"""
    return [b - a for a, b in zip(starts, starts[1:])]


class Wave:
    """假的图床上「同一阵」的回话（Web 的路由值）：前 size 个请求都到了以后才一起回 answer(网址)（像站点开始拒绝的那一刻，
    路上的几个请求一起被拒）。at：凑齐的时刻（time.monotonic）。"""

    def __init__(self, size: int, answer):
        self.answer, self.at = answer, None
        self.barrier = threading.Barrier(size, action=self._full, timeout=5)

    def _full(self) -> None:
        self.at = time.monotonic()

    def __call__(self, url):
        self.barrier.wait()
        return self.answer(url)


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class AssetsCase(unittest.TestCase):
    """识别图片的文件夹指到沙箱里的临时文件夹；等待记下来不真等；钟是假的。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-dlblock-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sleeps: list[float] = []
        self.clock = Clock()

    def item(self, name: str, data: bytes | None = None, url: str | None = None, api: str = "", kind: str = "heroes"):
        data = png(name.encode()) if data is None else data
        return assets.AssetItem(kind, name, name, url or f"https://img/{name}", len(data), sha1(data), api=api)

    def download(self, items, web, **kw):
        kw.setdefault("workers", 1)
        return assets.download(items, opener=web, sleep=self.sleeps.append, clock_now=self.clock, **kw)

    def record(self) -> assets.Cooldown | None:
        return assets.cooldown(self.clock())

    def write_record(self, until: float, minutes: int = 35, at: float | None = None, item: str | None = None) -> None:
        """写一条冷却记录；item 不给时不写这一项。"""
        at = until - minutes * 60 if at is None else at
        record = {"until": until, "minutes": minutes, "at": at, "reason": "HTTP 567"}
        if item is not None:
            record["item"] = item
        (self.base / assets.COOLDOWN_FILE).write_text(json.dumps(record), encoding="utf-8")

    def round_after_round(self, items, web, rounds: int) -> list[assets.Cooldown]:
        """一轮一轮接着下（每轮只下还缺的，钟拨到上一轮冷却到点以后），返回每轮下完的冷却记录。"""
        records = []
        for _ in range(rounds):
            self.download([i for i in items if not assets.is_present(i)], web)
            records.append(self.record())
            self.clock.t = records[-1].until + 60
        return records

    def assertPaused(self, failed, names):
        self.assertEqual(sorted(n for n, _ in failed), sorted(names))
        self.assertTrue(all(assets.is_paused(why) for _, why in failed), failed)

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob("*.png"))


class BlockedTest(AssetsCase):
    def test_567_stops_every_connection(self):
        """两条连接：一条拿到 567，另一条手上那张收手（不写文件），之后谁都不再发请求；没下的都算暂停，认出被拦的那一张
        带上原因；记下冷却 35 分钟和认出被拦的是哪一张。"""
        names = [f"{c}.png" for c in "abcdef"]
        hit = threading.Event()

        def blocked(url):
            hit.set()
            return http_error(567)

        def slow(url):  # 另一条连接上正在下的：567 到了以后才回来
            hit.wait(5)
            time.sleep(0.3)
            return png(b"b.png")

        web = Web({"https://img/a.png": blocked, "https://img/b.png": slow, "https://img/": png(b"x")})
        ok, failed = self.download([self.item(n) for n in names], web, workers=2)
        self.assertEqual(ok, [])
        self.assertPaused(failed, [f"heroes/{n}" for n in names])
        self.assertIn("HTTP 567", dict(failed)["heroes/a.png"])
        self.assertIn("https://img/a.png", web.calls)
        self.assertLessEqual(set(web.calls), {"https://img/a.png", "https://img/b.png"})
        calls = list(web.calls)
        time.sleep(0.5)
        self.assertEqual(web.calls, calls)  # 返回以后也不再有请求
        self.assertEqual(self.files(), [])
        self.assertEqual(self.record(), assets.Cooldown(T0 + 35 * 60, 35, T0, "HTTP 567（请求被站点的安全策略拦截）",
                                                        "heroes/a.png"))

    def test_page_instead_of_image(self):
        """拿到网页（拦截页、验证页）而不是 PNG：状态码 200、带页面的 403 都按被拦算，不去问接口，后面的不再下。"""
        page = b"<!DOCTYPE html><html><head><title>\xe6\x8b\xa6\xe6\x88\xaa</title></head></html>"
        for label, value in (("200", page), ("403", http_error(403, {"Content-Type": "text/html; charset=utf-8"}))):
            with self.subTest(status=label):
                (self.base / assets.COOLDOWN_FILE).unlink(missing_ok=True)
                web = Web({"https://img/a.png": value, "https://img/": png(b"x"), assets.API: b"{}"})
                ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
                self.assertEqual(ok, [])
                self.assertPaused(failed, ["heroes/a.png", "heroes/b.png"])
                self.assertEqual(web.calls, ["https://img/a.png"])
                self.assertEqual(web.api_calls(), [])
                self.assertIsNotNone(self.record())

    def test_single_403_fails_only_that_image(self):
        """不带页面的 403 单独一次：只算那一张失败（不重试），别的照下，不记冷却；连着两张都是 403 才按被拦算。"""
        web = Web({"https://img/a.png": http_error(403), "https://img/": png(b"x")})
        items = [self.item("a.png"), self.item("b.png", png(b"x")), self.item("c.png", png(b"x"))]
        ok, failed = self.download(items, web)
        self.assertEqual((ok, failed), (["heroes/b.png", "heroes/c.png"], [("heroes/a.png", "HTTP 403")]))
        self.assertEqual(web.calls.count("https://img/a.png"), 1)
        self.assertIsNone(self.record())
        web = Web({"https://img/a.png": http_error(403), "https://img/b.png": http_error(403), "https://img/": png(b"x")})
        ok, failed = self.download(items, web)
        self.assertEqual(ok, [])
        self.assertEqual(failed[0], ("heroes/a.png", "HTTP 403"))
        self.assertPaused(failed[1:], ["heroes/b.png", "heroes/c.png"])
        self.assertNotIn("https://img/c.png", web.calls)
        self.assertEqual(self.record().minutes, 35)

    def test_throttle_waits_retry_after(self):
        """限流单独一次：按 Retry-After 等（没写时等 60 秒）再接着下，不记冷却；中间照常回话的清零，不算连着两次。"""
        for headers, wait in (({"Retry-After": "7"}, 7.0), ({}, assets.RETRY_AFTER)):
            with self.subTest(headers=headers):
                self.sleeps.clear()
                web = Web({"https://img/a.png": [http_error(429, headers), png(b"a.png")]})
                ok, failed = self.download([self.item("a.png")], web)
                self.assertEqual((ok, failed), (["heroes/a.png"], []))
                self.assertEqual(len(web.calls), 2)
                waits = [s for s in self.sleeps if s > 1.5]  # 去掉请求之间的间隔
                self.assertEqual(len(waits), 1)
                self.assertTrue(wait - 0.5 < waits[0] <= wait, waits)
                self.assertIsNone(self.record())
        web = Web({"https://img/a.png": [http_error(429), png(b"a.png")],
                   "https://img/b.png": [http_error(503), png(b"b.png")]})
        ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
        self.assertEqual((ok, failed), (["heroes/a.png", "heroes/b.png"], []))
        self.assertIsNone(self.record())

    def test_throttle_holds_every_connection(self):
        """限流时所有下载线程一起等：一条连接拿到 429 以后，另一条连接下完手上那张，也等 Retry-After 过了才发下一个请求。
        真的等（Retry-After 1 秒），每秒的上限改成不限。领了时刻、还在等的也一起等，见 PaceTest。"""

        def slow(url):  # 另一条连接上正在下的
            time.sleep(0.3)
            return png(url.rsplit("/", 1)[1].encode())

        web = Web({"https://img/a.png": [http_error(429, {"Retry-After": "1"}), png(b"a.png")],
                   "https://img/b.png": slow, "https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        items = [self.item(f"{c}.png") for c in "abcd"]
        with mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download(items, opener=web, workers=2)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        first = web.times[web.calls.index("https://img/a.png")]
        later = [(url, t - first) for url, t in zip(web.calls, web.times) if url != "https://img/b.png" and t > first]
        self.assertEqual(len(later), 3)  # a 再来一次，c、d
        self.assertTrue(all(gap >= 0.95 for _, gap in later), later)
        self.assertIsNone(assets.cooldown())

    def test_throttle_twice_or_too_long_blocks(self):
        """限流连着两次按被拦算；要求等得比 RETRY_AFTER_MAX 还久的第一次就按被拦算、只发这一个请求，冷却按 Retry-After
        （进到整分）。"""
        web = Web({"https://img/": http_error(429, {"Retry-After": "5"})})
        ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
        self.assertEqual((ok, len(web.calls)), ([], 2))
        self.assertPaused(failed, ["heroes/a.png", "heroes/b.png"])
        self.assertEqual(self.record().until, T0 + 35 * 60)
        (self.base / assets.COOLDOWN_FILE).unlink()
        web = Web({"https://img/": http_error(503, {"Retry-After": "4000"})})
        ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
        self.assertEqual(len(web.calls), 1)
        self.assertPaused(failed, ["heroes/a.png", "heroes/b.png"])
        self.assertEqual(self.record().until, T0 + 4020)  # 4000 秒比 35 分钟长：按它，进到整分

    def test_one_wave_of_rejections_counts_once(self):
        """同一阵被拒只算一次：6 条连接上的前 6 个请求都发出去以后才一起回 429（Retry-After 1 秒），按单独一次限流处理：
        所有下载线程一起等过 Retry-After，再接着下，都下到，不记冷却（每个都算的话，第 2 个回来就按被拦算、冷却 35 分钟）。
        同一阵 6 个不带页面的 403 也只算一次：各算那一张失败；那之后才发出去的第 7 个又是 403，才按被拦算，被拦记在它上面
        （同一阵里比它回来得晚的，照这一轮被拦暂停）。真的等，每秒的上限改成不限。"""
        items = [self.item(f"{c}.png") for c in "abcdef"]
        wave = Wave(6, lambda url: http_error(429, {"Retry-After": "1"}))
        web = Web({i.url: [wave, png(i.file.encode())] for i in items})
        with mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download(items, opener=web, clock_now=self.clock)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        self.assertIsNone(self.record())
        self.assertEqual(len(web.calls), 12)  # 每张被拒一次、等过以后再来一次
        self.assertGreaterEqual(min(sorted(web.times)[6:]) - wave.at, 1.0 - EARLY, web.times)  # 都等过了 Retry-After
        items = [self.item(f"{c}.png") for c in "abcdefg"]
        wave = Wave(6, lambda url: http_error(403))
        web = Web({"https://img/g.png": http_error(403), "https://img/": wave})
        with mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download(items, opener=web, clock_now=self.clock)
        self.assertEqual(ok, [])
        reasons = dict(failed)
        # 同一阵的 6 个：各算那一张失败；第 7 个算够被拦以后才回来的，照这一轮被拦暂停
        wave_reasons = [reasons[i.name] for i in items[:6]]
        self.assertIn("HTTP 403", wave_reasons)
        self.assertTrue(all(why == "HTTP 403" or assets.is_paused(why) for why in wave_reasons), reasons)
        self.assertTrue(assets.is_paused(reasons["heroes/g.png"]), reasons)
        self.assertIn("连着 2 次被拒", reasons["heroes/g.png"])
        self.assertEqual((len(web.calls), self.record().item), (7, "heroes/g.png"))

    def test_rejected_again_after_wave_blocks(self):
        """同一阵 6 个 429 只算一次、一起等过 Retry-After；等过以后新发出去的请求又是 429：这才是连着第 2 次，按被拦算，
        全部停下、冷却 35 分钟，之后不再有请求。真的等，每秒的上限改成不限。"""
        items = [self.item(f"{c}.png") for c in "abcdef"]
        wave = Wave(6, lambda url: http_error(429, {"Retry-After": "1"}))
        web = Web({i.url: [wave, lambda url: http_error(429, {"Retry-After": "1"})] for i in items})
        with mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download(items, opener=web, clock_now=self.clock)
        self.assertEqual(ok, [])
        self.assertPaused(failed, [i.name for i in items])
        self.assertEqual(self.record().minutes, 35)
        again = sorted(web.times)[6:]
        self.assertTrue(again, web.calls)
        self.assertGreaterEqual(min(again) - wave.at, 1.0 - EARLY, web.times)  # 等过以后才又发出去
        calls = len(web.calls)
        self.assertLessEqual(calls, 12)
        time.sleep(0.3)
        self.assertEqual(len(web.calls), calls)

    def test_block_in_same_wave_still_stops(self):
        """同一阵里有站点防护的拦截（567、带页面的 403、拿到网页）：照旧一次就按被拦算、马上全部停下。a、b 一起发出去，a 先回
        429（要等 60 秒），算进去以后 b 才回拦截：不因为同一阵里先回来的是限流就放过 b；正按 Retry-After 等着的 a 不等完，
        c 不开始。"""
        page = b"<!DOCTYPE html><html><head><title>blocked</title></head></html>"
        cases = (("567", http_error(567)), ("403", http_error(403, {"Content-Type": "text/html; charset=utf-8"})),
                 ("页面", page))
        real = assets._Gate.rejected
        for label, value in cases:
            with self.subTest(case=label):
                (self.base / assets.COOLDOWN_FILE).unlink(missing_ok=True)
                counted = threading.Event()

                def rejected(gate, error, sent=None, counted=counted):
                    try:
                        real(gate, error, sent)
                    finally:
                        counted.set()

                def late(url, value=value, counted=counted):
                    counted.wait(5)
                    return value

                wave = Wave(2, lambda url, late=late: (http_error(429, {"Retry-After": "60"}) if url.endswith("/a.png")
                                                       else late(url)))
                web = Web({"https://img/a.png": wave, "https://img/b.png": wave, "https://img/": by_name})
                items = [self.item(f"{c}.png") for c in "abc"]
                start = time.monotonic()
                with mock.patch.object(assets._Gate, "rejected", rejected), mock.patch.object(assets, "RATE", math.inf):
                    ok, failed = assets.download(items, opener=web, workers=2, clock_now=self.clock)
                self.assertLess(time.monotonic() - start, STOP_LIMIT)
                self.assertEqual(ok, [])
                self.assertPaused(failed, [i.name for i in items])
                self.assertEqual(sorted(web.calls), ["https://img/a.png", "https://img/b.png"])
                self.assertEqual(self.record().item, "heroes/b.png")

    def test_retry_after_forms(self):
        now = 1_000_000_000.0
        self.assertEqual(assets._retry_after({"Retry-After": "120"}, now), 120.0)
        self.assertEqual(assets._retry_after({"retry-after": "3"}, now), 3.0)
        self.assertAlmostEqual(assets._retry_after({"Retry-After": email.utils.formatdate(now + 90, usegmt=True)}, now),
                               90.0)
        for value in ("", "很快", "-5", "²", "①"):  # 「²」「①」isdigit() 为真，float() 却认不了
            with self.subTest(value=value):
                self.assertIsNone(assets._retry_after({"Retry-After": value}, now))
        self.assertIsNone(assets._retry_after(None, now))

    def test_bad_retry_after_still_blocked(self):
        """567 带的 Retry-After 乱写（「²」这类 isdigit() 为真的字符）：照样认出被拦、整体停下，只发这一个请求，冷却 35 分钟。"""
        web = Web({"https://img/": http_error(567, {"Retry-After": "²"})})
        ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
        self.assertEqual((ok, len(web.calls)), ([], 1))
        self.assertPaused(failed, ["heroes/a.png", "heroes/b.png"])
        self.assertEqual(self.record().until, T0 + 35 * 60)

    def test_downloaded_before_block_kept(self):
        """被拦之前下好的照常存着、算成功；被拦的那一张和后面的暂停。"""
        web = Web({"https://img/c.png": http_error(567), "https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        ok, failed = self.download([self.item(f"{c}.png") for c in "abcd"], web)
        self.assertEqual(ok, ["heroes/a.png", "heroes/b.png"])
        self.assertPaused(failed, ["heroes/c.png", "heroes/d.png"])
        self.assertEqual(self.files(), ["heroes/a.png", "heroes/b.png"])


class CooldownTest(AssetsCase):
    def block_once(self) -> assets.Cooldown:
        ok, failed = self.download([self.item("a.png")], Web({"https://img/": http_error(567)}))
        self.assertPaused(failed, ["heroes/a.png"])
        return self.record()

    def test_record_written_and_read_back(self):
        """冷却记录写进 assets 下的小文件（临时文件、整份替换，不留 .part），每次都从文件读：重启以后照样认；到点以后不再
        冷却，记录还在（之后按慢速下）。"""
        record = self.block_once()
        raw = json.loads((self.base / assets.COOLDOWN_FILE).read_text(encoding="utf-8"))
        self.assertEqual((raw["until"], raw["minutes"], raw["at"]), (T0 + 2100, 35, T0))
        self.assertIn("HTTP 567", raw["reason"])
        self.assertFalse(list(self.base.glob("*.part")))
        self.assertEqual(assets.cooldown(T0 + 60), record)
        self.assertTrue(record.active(T0 + 2099))
        self.assertFalse(record.active(T0 + 2100))
        self.assertIsNotNone(assets.cooldown(T0 + 9999))

    def test_bad_records_ignored(self):
        path = self.base / assets.COOLDOWN_FILE
        for text in ("{坏", "[]", json.dumps({"until": "明天"}), json.dumps({"until": True}), json.dumps({"minutes": 35})):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                self.assertIsNone(assets.cooldown(T0))
        path.write_text(json.dumps({"until": T0 + 600, "minutes": "很久", "item": 5}), encoding="utf-8")
        self.assertEqual((assets.cooldown(T0).until, assets.cooldown(T0).minutes, assets.cooldown(T0).item),
                         (T0 + 600, 35, ""))
        path.write_text(json.dumps({"until": T0 + 30 * 86400, "minutes": 70}), encoding="utf-8")  # 时钟往回拨过
        self.assertEqual(assets.cooldown(T0).until, T0 + 70 * 60)

    def test_far_future_record_rewritten(self):
        """记录里的时刻比现在晚一天以上（时钟往回拨过）：从第一次读到起冷却 minutes 分钟，写回文件，之后再读不再往后挪，
        到点就不再冷却；被拦的时刻改成不晚于现在，被拦的是哪一张照留。"""
        path = self.base / assets.COOLDOWN_FILE
        far = T0 + 30 * 86400
        path.write_text(json.dumps({"until": far, "minutes": 35, "at": far - 2100, "reason": "HTTP 567",
                                    "item": "heroes/x.png"}), encoding="utf-8")
        first = assets.cooldown(T0)
        self.assertEqual((first.until, first.minutes, first.at, first.reason, first.item),
                         (T0 + 2100, 35, T0, "HTTP 567", "heroes/x.png"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["until"], T0 + 2100)
        self.assertEqual(assets.cooldown(T0 + 600), first)
        self.assertFalse(assets.cooldown(T0 + 2100).active(T0 + 2100))
        self.assertFalse(list(self.base.glob("*.part")))

    def test_doubles_and_caps(self):
        """到点以后再被拦，冷却时间翻倍：35、70、140 分钟，最多 3 小时；上一次被拦已过去一天以上的从 35 分钟算起。"""
        got = []
        for _ in range(5):
            record = self.block_once()
            got.append(record.minutes)
            self.assertEqual(record.until, self.clock() + record.minutes * 60)
            self.clock.t = record.until + 60  # 到点以后接着下：又被拦
        self.assertEqual(got, [35, 70, 140, 180, 180])
        self.clock.t += assets.COOLDOWN_FRESH
        self.assertEqual(self.block_once().minutes, 35)

    def test_retry_after_longer_than_cooldown(self):
        web = Web({"https://img/": http_error(567, {"Retry-After": str(3 * 3600 + 10)})})
        self.download([self.item("a.png")], web)
        self.assertEqual(self.record().until, T0 + 3 * 3600 + 60)

    def test_slow_after_cooldown_and_cleared_when_complete(self):
        """冷却过了点：只用 1 条连接（不管 workers 给几），每次隔 1 秒；下全了清掉记录，下一轮照平时的速度。"""
        self.write_record(T0 - 1)
        items = [self.item(f"{c}.png") for c in "abc"]
        web = Web({"https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        ok, failed = self.download(items, web, workers=2)
        self.assertEqual((ok, failed), ([i.name for i in items], []))
        self.assertEqual(len(set(web.threads)), 1)
        long_sleeps = [s for s in self.sleeps if s > 0.6]
        self.assertEqual(len(long_sleeps), 2)
        self.assertTrue(all(s <= assets.SLOW_INTERVAL for s in long_sleeps), long_sleeps)
        self.assertFalse((self.base / assets.COOLDOWN_FILE).exists())
        web = Web({"https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        self.sleeps.clear()
        with Claims() as claims:
            self.download(items, web, workers=1)
        # 平时的速度：每个请求按每秒的上限领时刻（慢速不领，每次隔 1 秒）。等待记下来、不真等：领到的时刻依次往后排 1/RATE，
        # 等得最久的也不到 len(items)/RATE 秒
        self.assertEqual(len(claims.got), len(items))
        self.assertTrue(all(s <= len(items) / assets.RATE for s in self.sleeps), self.sleeps)

    def test_cooldown_not_cleared_while_incomplete(self):
        """过了点、接着下却有别的失败（网络出错）：记录留着（下次照样按慢速），到点的时刻不变。"""
        self.write_record(T0 - 1)
        web = Web({"https://img/a.png": urllib.error.URLError("连不上（测试）"), "https://img/": png(b"b.png")})
        ok, failed = self.download([self.item("a.png"), self.item("b.png")], web)
        self.assertEqual(ok, ["heroes/b.png"])
        self.assertTrue(failed[0][1].startswith("网络错误"))
        self.assertEqual(self.record().until, T0 - 1)


class ProbeTest(AssetsCase):
    """冷却期间再下载（悬浮窗上点「重试」）：只试一个请求。"""

    def setUp(self):
        super().setUp()
        self.write_record(T0 + 1200, minutes=35, at=T0 - 900)
        self.before = self.record()
        self.items = [self.item(f"{c}.png") for c in "abc"]

    def test_still_blocked_keeps_record(self):
        """仍被拒（567、限流、不带页面的 403、拦截页）或没得到回话：只发了这一个请求，冷却记录一字不改（不重算、不翻倍）。"""
        for label, value in (("567", http_error(567)), ("429", http_error(429, {"Retry-After": "5"})),
                             ("403", http_error(403)), ("页面", b"<html>blocked</html>"),
                             ("网络", urllib.error.URLError("连不上（测试）"))):
            with self.subTest(case=label):
                web = Web({"https://img/": value})
                ok, failed = self.download(self.items, web)
                self.assertEqual(len(web.calls), 1)
                self.assertEqual(ok, [])
                self.assertPaused(failed, [i.name for i in self.items])
                self.assertEqual(self.record(), self.before)

    def test_passes_then_continues_slowly(self):
        """通了：按慢速（1 条连接）接着下完，下全了清掉记录。"""
        web = Web({"https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        ok, failed = self.download(self.items, web, workers=2)
        self.assertEqual((ok, failed), ([i.name for i in self.items], []))
        self.assertEqual(len(web.calls), 3)
        self.assertEqual(len(set(web.threads)), 1)
        self.assertIsNone(self.record())

    def test_passes_then_blocked_again_doubles(self):
        """试探通了、接着下的时候又被拦：这是新的一次被拦，冷却时间翻倍。"""
        web = Web({"https://img/a.png": png(b"a.png"), "https://img/": http_error(567)})
        ok, failed = self.download(self.items, web)
        self.assertEqual(ok, ["heroes/a.png"])
        self.assertEqual((self.record().minutes, self.record().until), (70, T0 + 70 * 60))

    def test_passes_then_cancelled_ends_cooldown(self):
        """试探通了、没下完就退出：到点的时刻提前到现在（重启以后不用再等，照旧按慢速下）。试探以后的请求等 download 返回了
        才回话：等待不真等，不这样的话机器忙时下载线程可能赶在取消生效之前把剩下的都下完。"""
        stop = threading.Event()
        done = threading.Event()

        def later(url):  # 取消以后才回话的：回话到了也不写文件
            done.wait(5)
            return png(url.rsplit("/", 1)[1].encode())

        web = Web({"https://img/a.png": png(b"a.png"), "https://img/": later})
        try:
            ok, failed = self.download(self.items, web, stop=stop, progress=lambda *a: stop.set())
        finally:
            done.set()
        self.assertEqual(ok, ["heroes/a.png"])
        record = self.record()
        self.assertEqual((record.until, record.minutes, record.at), (T0, 35, self.before.at))
        self.assertFalse(record.active(T0))


class LastBlockedTest(AssetsCase):
    """冷却记录里记着认出被拦的是哪一张：接着下时（到点、点「重试」、启动时冷却已过）排到最后，冷却期间的试探请求也不用
    它（还缺的不止一张时）。认出被拦只看这一张的回话，也可能是这一张自己的问题。"""

    def test_one_image_always_a_page(self):
        """6 张里第 3 张在图床上一直回网页（带页面的 403，整站其实没拦）：第一轮下到前两张；接着下时它排到最后，其余 3 张
        都下到；只剩它时每轮只发它一个请求。冷却照旧翻倍（35、70、140 分钟），一直记着是它。"""
        items = [self.item(f"{c}.png") for c in "abcdef"]
        web = Web({"https://img/c.png": http_error(403, {"Content-Type": "text/html; charset=utf-8"}),
                   "https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        records = self.round_after_round(items, web, 3)
        self.assertEqual(self.files(), [f"heroes/{c}.png" for c in "abdef"])
        self.assertEqual([r.minutes for r in records], [35, 70, 140])
        self.assertEqual({r.item for r in records}, {"heroes/c.png"})
        self.assertEqual([url.rsplit("/", 1)[1] for url in web.calls],
                         ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png", "c.png", "c.png"])

    def test_other_wiki_image_always_blocked(self):
        """另一个 wiki 上的那张一直被拦（567），BWIKI 照常：同样排到最后，BWIKI 的图都下到，冷却照旧翻倍。"""
        en = self.item("e.png", url="https://en-img/e.png", api=OTHER_API)
        items = [self.item("a.png"), en, self.item("b.png"), self.item("c.png")]
        web = Web({"https://en-img/": http_error(567), "https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
        records = self.round_after_round(items, web, 2)
        self.assertEqual(self.files(), ["heroes/a.png", "heroes/b.png", "heroes/c.png"])
        self.assertEqual([(r.minutes, r.item) for r in records], [(35, "heroes/e.png"), (70, "heroes/e.png")])

    def test_probe_avoids_last_blocked(self):
        """冷却期间点「重试」：试探请求不用上次被拦的那一张（a），仍被拒时记录一字不改（照旧记着 a）；只缺 a 一张时就试 a。"""
        self.write_record(T0 + 1200, at=T0 - 900, item="heroes/a.png")
        before = self.record()
        items = [self.item(f"{c}.png") for c in "abc"]
        web = Web({"https://img/": http_error(567)})
        ok, failed = self.download(items, web)
        self.assertEqual(web.calls, ["https://img/b.png"])
        self.assertPaused(failed, [i.name for i in items])
        self.assertEqual(self.record(), before)
        web = Web({"https://img/": http_error(567)})
        self.download(items[:1], web)
        self.assertEqual(web.calls, ["https://img/a.png"])

    def test_resume_order(self):
        """到点以后接着下：上次被拦的那一张排到最后，其余照原来的先后；下全了清掉记录。记录里没有这一项（这一版以前不会有，
        手改过的）照原来的先后。"""
        items = [self.item(f"{c}.png") for c in "abc"]
        for item, order in (("heroes/a.png", ["b.png", "c.png", "a.png"]), (None, ["a.png", "b.png", "c.png"])):
            with self.subTest(item=item):
                for path in list(self.base.rglob("*.png")):
                    path.unlink()
                self.write_record(T0 - 1, item=item)
                web = Web({"https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())})
                ok, failed = self.download(items, web)
                self.assertEqual([url.rsplit("/", 1)[1] for url in web.calls], order)
                self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
                self.assertIsNone(self.record())


class BatchTest(AssetsCase):
    def test_refresh_in_batches(self):
        """内容和清单对不上的攒起来：所有图片下过一遍以后 50 张一批问接口，接口请求之间隔 2 秒；下到的内容对得上接口给的
        新校验值就直接收下（不再下载），记进本地覆盖清单。"""
        names = [f"x{i:02d}.png" for i in range(60)]
        items = [self.item(n, png(b"old" + n.encode())) for n in names]
        new = {n: png(b"new" + n.encode()) for n in names}
        web = Web({"https://img/": lambda url: new[url.rsplit("/", 1)[1]],
                   assets.API: imageinfo({n: (f"https://img/{n}", new[n]) for n in names})})
        ok, failed = self.download(items, web)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        api = web.api_calls()
        self.assertEqual([len(titles_of(u)) for u in api], [50, 10])
        self.assertEqual(len(web.calls), 62)  # 每张图只下了一次
        self.assertEqual(len(assets.load_local()["updated"]), 60)
        # 接口请求之间至少隔 API_INTERVAL：真的等（间隔改成 0.3 秒，图片请求每秒的上限改成不限），量两次接口请求的间隔
        (self.base / "heroes").rename(self.base / "old")
        (self.base / assets.LOCAL_MANIFEST).unlink()
        web = Web({"https://img/": lambda url: new[url.rsplit("/", 1)[1]],
                   assets.API: imageinfo({n: (f"https://img/{n}", new[n]) for n in names})})
        with mock.patch.object(assets, "API_INTERVAL", 0.3), mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download(items, opener=web, workers=2)
        self.assertEqual(len(ok), 60)
        times = [t for url, t in zip(web.calls, web.times) if url.startswith(assets.API)]
        self.assertEqual(len(times), 2)
        # Windows 上带时限的等待按毫秒计，可能早一两毫秒返回：留 50 毫秒的余量（不按间隔时几乎是 0）
        self.assertGreater(times[1] - times[0], 0.25)
        self.assertEqual(assets.API_INTERVAL, 2.0)

    def test_moved_and_deleted_and_other_wiki(self):
        """旧地址 404 的：接口给了新地址的再下一次；接口里没有的记作已删除；另一个 wiki 上的另问那个 wiki 的接口。"""
        moved, gone = self.item("m.png"), self.item("g.png")
        en = self.item("e.png", api=OTHER_API)
        web = Web({"https://img/m.png": http_error(404), "https://img/g.png": http_error(404),
                   "https://img/e.png": http_error(404),
                   "https://new/": lambda url: png(url.rsplit("/", 1)[1].encode()),
                   assets.API: imageinfo({"m.png": ("https://new/m.png", png(b"m.png"))}),
                   OTHER_API: imageinfo({"e.png": ("https://new/e.png", png(b"e.png"))})})
        ok, failed = self.download([moved, gone, en], web)
        self.assertEqual((sorted(ok), failed), (["heroes/e.png", "heroes/m.png"], [("heroes/g.png", assets.GONE)]))
        self.assertEqual(sorted(u.split("?")[0] for u in web.api_calls()), sorted([assets.API, OTHER_API]))
        self.assertIn("heroes/g.png", assets.load_local()["gone"])

    def test_api_blocked_stops_everything(self):
        """接口被拦（567）：一样整体停下，等着问接口的都算暂停，记下冷却；不是哪一张图的问题，不记是哪一张（接着下时照
        原来的先后）；之前下好的照常留着。"""
        good = self.item("ok.png")
        stale = [self.item(f"s{i}.png", png(b"old")) for i in range(3)]
        web = Web({"https://img/ok.png": png(b"ok.png"), "https://img/": png(b"new"), assets.API: http_error(567)})
        ok, failed = self.download([good, *stale], web)
        self.assertEqual(ok, ["heroes/ok.png"])
        self.assertPaused(failed, [i.name for i in stale])
        self.assertEqual(len(web.api_calls()), 1)
        self.assertEqual((self.record().minutes, self.record().item), (35, ""))

    def test_api_throttled_once_waits(self):
        """接口限流单独一次（429 带 Retry-After）：同下载图片，等过再问一次，问到了照常收下；不记冷却，不算失败。"""
        stale = [self.item(f"s{i}.png", png(b"old")) for i in range(3)]
        new = {i.file: png(b"new" + i.file.encode()) for i in stale}
        web = Web({"https://img/": lambda url: new.get(url.rsplit("/", 1)[1], png(b"old")),
                   assets.API: [http_error(429, {"Retry-After": "5"}),
                                imageinfo({n: (f"https://img/{n}", data) for n, data in new.items()})]})
        ok, failed = self.download(stale, web)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in stale), []))
        self.assertEqual(len(web.api_calls()), 2)
        waits = [s for s in self.sleeps if s > assets.API_INTERVAL]  # 去掉请求之间的间隔
        self.assertEqual(len(waits), 1)
        self.assertTrue(4.5 < waits[0] <= 5, waits)
        self.assertIsNone(self.record())

    def test_api_page_blocks(self):
        """接口回的是网页（拦截页）：同样按被拦算。"""
        stale = self.item("s.png", png(b"old"))
        web = Web({"https://img/": png(b"new"), assets.API: b"<html><body>blocked</body></html>"})
        ok, failed = self.download([stale], web)
        self.assertPaused(failed, ["heroes/s.png"])
        self.assertIsNotNone(self.record())

    def test_phases_spaced(self):
        """三段（先下一遍、问接口、按新地址再下）之间也隔开：问接口的第一个请求离上一个图片请求隔 API_INTERVAL，按新地址
        再下的第一个请求离接口请求隔这一段的间隔（被拦过以后是 SLOW_INTERVAL）。等待记下来、不真等，各段只有一个请求，
        记下的就是段和段之间的这两次。平时的两段共用每秒的上限，见 PaceTest.test_phases_share_pace。"""
        self.write_record(T0 - 1)  # 被拦过、已过了点：按慢速下
        new = png(b"new-s")
        web = Web({"https://img/s.png": png(b"old-s"), "https://new/s.png": new,
                   assets.API: imageinfo({"s.png": ("https://new/s.png", new)})})
        ok, failed = self.download([self.item("s.png", png(b"listed-s"))], web)
        self.assertEqual((ok, failed), (["heroes/s.png"], []))
        self.assertEqual([url.split("?")[0] for url in web.calls], ["https://img/s.png", assets.API, "https://new/s.png"])
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(assets.API_INTERVAL - 0.5 < self.sleeps[0] <= assets.API_INTERVAL, self.sleeps)
        self.assertTrue(assets.SLOW_INTERVAL - 0.5 < self.sleeps[1] <= assets.SLOW_INTERVAL, self.sleeps)

    def test_fetcher_interval(self):
        """取图器的疏密（等待记下来、不真等）：接口用的（interval=API_INTERVAL）两次请求之间至少隔 2 秒；开发用的取图工具
        下图片用的（interval=SLOW_INTERVAL）隔 1 秒。给了 interval 的只按它，和每秒的上限无关（上限调成每 10 秒一个也照样
        只等 interval）。不给 interval、也不给共用的计时（_Pace）的，照样按每秒的上限领时刻：钟不走，第 k 个请求等 k/RATE
        秒上下（上限调成每 10 秒一个，等待和真的耗时分得开）。"""
        for interval in (assets.API_INTERVAL, assets.SLOW_INTERVAL):
            with self.subTest(interval=interval):
                self.sleeps.clear()
                fetcher = assets._Fetcher(Web({"https://x/": b"{}"}), sleep=self.sleeps.append, interval=interval)
                with mock.patch.object(assets, "RATE", 0.1):
                    fetcher.get("https://x/1")
                    fetcher.get("https://x/2")
                self.assertEqual(len(self.sleeps), 1)
                self.assertTrue(interval - 0.1 < self.sleeps[0] <= interval, self.sleeps)
        self.sleeps.clear()
        fetcher = assets._Fetcher(Web({"https://x/": b"{}"}), sleep=self.sleeps.append)
        with mock.patch.object(assets, "RATE", 0.1):
            for i in range(3):
                fetcher.get(f"https://x/{i}")
        self.assertEqual(len(self.sleeps), 2)
        gap = 1 / 0.1
        self.assertTrue(all(k * gap < s <= (k + 1) * gap for k, s in enumerate(self.sleeps)), self.sleeps)


class PaceTest(AssetsCase):
    """平时的疏密（advisor/assets.py 模块说明「不给站点添压力」）：整个一轮每秒最多开始 RATE 个请求（各条连接、各段合在一起
    算），同时最多 WORKERS 条连接，同一条连接上不另隔；被拦、限流、冷却、取消的规矩照旧。这几条真的等（假的 opener 每个请求
    故意等一会儿，像图床节点上没有缓存、回源站取图）。疏密按领到的时刻核对（Claims、assertPaced）：领到的时刻两两相隔不小于
    1/RATE，每个请求都不早于领到的时刻开始；线程醒得晚、请求开始得晚只会更稀，机器忙时也不误报。为了几秒内跑完，有的把每秒
    的上限临时调大。"""

    def assertPaced(self, spans: list[Span], gap: float) -> None:
        """按每秒的上限领时刻的请求（claim 不为 None）：领到的时刻两两相隔不小于 gap，每个请求都不早于领到的时刻开始。"""
        claimed = sorted(span.claim for span in spans if span.claim is not None)
        self.assertTrue(claimed, spans)
        self.assertGreaterEqual(min(gaps(claimed), default=gap), gap - 1e-9, claimed)
        self.assertEqual([span for span in spans if span.claim is not None and span.start < span.claim - EARLY], [])

    def test_rate_and_connections(self):
        """每个请求都要等 0.6 秒、每秒的上限调成 20：每个请求都按上限领了时刻，领到的时刻两两相隔不小于 1/20 秒，都不早于
        领到的时刻开始；同时在路上的请求最多 WORKERS 个，6 条连接都用上（不限连接的话，0.3 秒时第 7 个就发出去了，那时第 1 个
        还没回来）。"""
        self.assertEqual((assets.RATE, assets.WORKERS), (5, 6))  # 平时的速度：每秒 5 个请求、6 条连接（软件维护说明照写）
        items = [self.item(f"r{i}.png") for i in range(9)]
        with Claims() as claims, mock.patch.object(assets, "RATE", 20):
            timed = Timed(Web({"https://img/": by_name}), delay=0.6, claims=claims)
            ok, failed = assets.download(items, opener=timed)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        self.assertEqual([span.url for span in timed.spans if span.claim is None], [])
        self.assertPaced(timed.spans, 1 / 20)
        self.assertEqual(timed.peak, assets.WORKERS)

    def test_waits_overlap(self):
        """连接多了，把等图床的时间叠起来：每张都要等 0.8 秒（像图床节点上没有缓存、回源站取），照平时的速度（每秒 5 个、
        6 条连接）下 8 张：领到的时刻一个紧接一个，第 8 个离第 1 个正好 7/RATE 秒（没有因为等连接空出来往后推），同时在路上的
        有 4 个以上，约 7/RATE + 0.8 = 2.2 秒下完，接近「张数 ÷ 每秒上限」。一条连接上一张接一张地等的话，2 条连接也要
        8 × 0.8 ÷ 2 = 3.2 秒以上（每条连接上再各隔 0.5 秒时约 4.7 秒，同时在路上的最多 2 个）。"""
        delay = 0.8
        items = [self.item(f"w{i}.png") for i in range(8)]
        with Claims() as claims:
            timed = Timed(Web({"https://img/": by_name}), delay=delay, claims=claims)
            start = time.monotonic()
            ok, failed = assets.download(items, opener=timed)
            took = time.monotonic() - start
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        self.assertPaced(timed.spans, 1 / assets.RATE)
        claimed = sorted(span.claim for span in timed.spans)
        self.assertAlmostEqual(claimed[-1] - claimed[0], (len(items) - 1) / assets.RATE, delta=1e-6)
        self.assertGreaterEqual(timed.peak, 4)
        self.assertLessEqual(timed.peak, assets.WORKERS)
        self.assertLess(took, len(items) * delay / 2)

    def test_phases_share_pace(self):
        """两段（先下一遍、按新地址再下）和中间问接口的一段共用每秒的上限：图片请求都按上限领时刻，领到的时刻两两相隔不小于
        1/RATE；接口请求不领（按固定间隔），离上一个图片请求结束隔 API_INTERVAL，放行时把开始的时刻记进共用的计时，按新地址
        再下的第一个请求领到的时刻离它至少 1/RATE（各段新建的连接，照样从上一段的请求算起）。回话都不等：各段要是各算各的，
        按新地址再下的第一个请求会在接口回话以后马上发出去。每秒的上限调成 20、接口的间隔调成 0.3 秒。"""
        moved = [self.item(f"m{i}.png") for i in range(4)]  # 旧地址 404，接口给了新地址
        plain = [self.item(f"p{i}.png") for i in range(2)]
        web = Web({**{f"https://img/{i.file}": http_error(404) for i in moved}, "https://img/": by_name,
                   "https://new/": by_name,
                   assets.API: imageinfo({i.file: (f"https://new/{i.file}", by_name(i.url)) for i in moved})})
        with Claims() as claims, mock.patch.object(assets, "RATE", 20), mock.patch.object(assets, "API_INTERVAL", 0.3):
            timed = Timed(web, claims=claims)
            ok, failed = assets.download(moved + plain, opener=timed)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in moved + plain), []))
        spans = timed.ordered()
        kinds = ["api" if span.url.startswith(assets.API) else span.url.split("/")[2] for span in spans]
        self.assertEqual(kinds, ["img"] * 6 + ["api"] + ["new"] * 4)
        api = spans[6]
        self.assertEqual([span.claim is None for span in spans], [False] * 6 + [True] + [False] * 4)
        self.assertPaced(spans, 1 / 20)
        self.assertGreaterEqual(api.start - max(span.end for span in spans[:6]), 0.3 - EARLY, spans)
        self.assertEqual(len(claims.marks), 1)
        self.assertLessEqual(claims.marks[0], api.start)  # 记进去的是接口请求放行的时刻
        self.assertGreaterEqual(min(span.claim for span in spans[7:]) - claims.marks[0], 1 / 20 - 1e-9, spans)

    def test_stop_while_waiting_for_turn(self):
        """等请求时刻的时候取消（退出程序）：每秒的上限调成 0.5（领到的时刻依次隔 2 秒），第一张处理完就置停止标志：正等着
        的几条下载线程马上收手，download() 约 STOP_POLL＋STOP_GRACE 秒内返回，下载线程也都结束了（不等到领到的时刻），之后
        不再有请求；没下的算「已取消」，不记冷却。"""
        before = set(threading.enumerate())
        items = [self.item(f"s{i}.png") for i in range(6)]
        web = Web({"https://img/": by_name})
        stop = threading.Event()
        with mock.patch.object(assets, "RATE", 0.5):
            start = time.monotonic()
            ok, failed = assets.download(items, lambda *a: stop.set(), stop, opener=web)
            took = time.monotonic() - start
            workers = [t for t in threading.enumerate() if t not in before and t.name.startswith("assets")]
            for thread in workers:
                thread.join(1.0)
        self.assertLess(took, STOP_LIMIT)
        self.assertEqual([t.name for t in workers if t.is_alive()], [])
        self.assertEqual(len(web.calls), 1)
        self.assertEqual(ok, [f"heroes/{web.calls[0].rsplit('/', 1)[1]}"])
        self.assertEqual(sorted(failed), sorted((i.name, assets.CANCELLED) for i in items if i.name not in ok))
        time.sleep(0.3)
        self.assertEqual(len(web.calls), 1)
        self.assertIsNone(assets.cooldown())

    def test_throttle_holds_waiting_turns(self):
        """6 条连接时限流照旧所有下载线程一起等：a 的请求过 0.25 秒才回 429（Retry-After 1 秒），这时后面几个已经领了时刻、
        正等着：限流算进去以后，它们领到的时刻作废、不发，等过 Retry-After 再重新领、接着下。按领到的时刻核对：发出去的请求
        领到的时刻，要么在限流算进去之前（回话之前已经发出去的，照常收回话），要么在 Retry-After 到了以后，中间一个都没有；
        Retry-After 到了以后才领的有 4 个以上（a 再来一次；d 以后的几个回话之前在等时刻），照样相隔 1/RATE。都下完，不记冷却。
        每秒的上限调成 10。"""
        holds: list[tuple[float, float]] = []  # (限流算进去的时刻, 所有下载线程等到什么时候)
        real = assets._Gate.rejected

        def rejected(gate, error, sent=None):
            try:
                real(gate, error, sent)
            finally:
                holds.append((time.monotonic(), gate._not_before))

        def throttled(url):
            time.sleep(0.25)
            return http_error(429, {"Retry-After": "1"})

        items = [self.item(f"{c}.png") for c in "abcdefgh"]
        web = Web({"https://img/a.png": [throttled, png(b"a.png")], "https://img/": by_name})
        with Claims() as claims, mock.patch.object(assets._Gate, "rejected", rejected), \
                mock.patch.object(assets, "RATE", 10):
            timed = Timed(web, claims=claims)
            ok, failed = assets.download(items, opener=timed)
        self.assertEqual((sorted(ok), failed), (sorted(i.name for i in items), []))
        self.assertEqual(len(timed.spans), len(items) + 1)  # a 再来一次
        self.assertEqual(len(holds), 1)
        counted, until = holds[0]
        claimed = sorted(span.claim for span in timed.spans)
        self.assertEqual([at for at in claimed if counted + EARLY <= at < until - EARLY], [], (counted, until, claimed))
        self.assertGreaterEqual(sum(1 for at in claimed if at >= until - EARLY), 4, (counted, until, claimed))
        self.assertPaced(timed.spans, 0.1)
        self.assertIsNone(assets.cooldown(self.clock()))

    def test_block_with_six_connections(self):
        """6 条连接里一条认出被拦（567）：a 的请求等别的 3 个都发出去以后才回 567。认出的那一刻已经在路上的，回话到了也收手
        （不写文件、不重试）；正等着时刻的不再发（领到的时刻在认出被拦以后的，一个都没发出去），排着的 g、h 不开始，之后谁都
        不再发请求；没下的都算暂停，记下冷却和认出被拦的是哪一张。照平时的速度（领到的时刻依次隔 1/RATE）；在路上的那几个等到
        认出被拦以后才回话（_Gate.block 换成记下时刻的）。"""
        flying = threading.Semaphore(0)
        seen: list[float] = []  # 认出被拦的时刻
        hit = threading.Event()
        real = assets._Gate.block

        def block(gate, error, item=""):
            real(gate, error, item)
            seen.append(time.monotonic())
            hit.set()

        def blocked(url):
            for _ in range(3):  # 等别的 3 个发出去
                flying.acquire(timeout=5)
            return http_error(567)

        def slow(url):  # 认出被拦的时候还在路上的
            flying.release()
            hit.wait(5)
            return by_name(url)

        items = [self.item(f"{c}.png") for c in "abcdefgh"]
        web = Web({"https://img/a.png": blocked, "https://img/": slow})
        with Claims() as claims, mock.patch.object(assets._Gate, "block", block):
            timed = Timed(web, claims=claims)
            ok, failed = assets.download(items, opener=timed, clock_now=self.clock)
        self.assertEqual(ok, [])
        self.assertPaused(failed, [i.name for i in items])
        calls = sorted(web.calls)
        self.assertIn("https://img/a.png", calls)
        self.assertGreaterEqual(len(calls), 4)  # a 和在路上的 3 个
        self.assertFalse({"https://img/g.png", "https://img/h.png"} & set(calls), calls)
        self.assertEqual([span for span in timed.spans if span.claim >= seen[0]], [])  # 等着时刻的不再发
        self.assertGreaterEqual(sum(1 for at in claims.got if at > seen[0]), 1, (seen, claims.got))
        time.sleep(0.3)  # 在路上的那几个回话以后也不写文件、不重试，之后谁都不再发请求
        self.assertEqual(sorted(web.calls), calls)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.record().item, "heroes/a.png")

    def test_slow_after_block_unchanged(self):
        """被拦过以后（冷却记录在、已过了点）照旧：给了 6 条连接也只用 1 条，不按每秒的上限领时刻，上一个请求结束到下一个
        开始隔 SLOW_INTERVAL（这里调成 0.2 秒，每个请求等 0.05 秒）；冷却期间（点「重试」）给了 6 条连接也只试一个请求，冷却
        记录不改。"""
        self.write_record(T0 - 1)
        items = [self.item(f"c{i}.png") for i in range(3)]
        web = Web({"https://img/": by_name})
        with Claims() as claims, mock.patch.object(assets, "SLOW_INTERVAL", 0.2):
            timed = Timed(web, delay=0.05, claims=claims)
            ok, failed = assets.download(items, opener=timed, workers=6, clock_now=self.clock)
        self.assertEqual((ok, failed), ([i.name for i in items], []))
        self.assertEqual(len(set(web.threads)), 1)
        self.assertEqual(claims.got, [])
        spans = timed.ordered()
        rests = [nxt.start - cur.end for cur, nxt in zip(spans, spans[1:])]
        self.assertTrue(all(rest >= 0.2 - EARLY for rest in rests), rests)
        self.write_record(T0 + 1200, at=T0 - 900)
        before = self.record()
        web = Web({"https://img/": http_error(567)})
        ok, failed = self.download(items, web, workers=6)
        self.assertEqual((ok, len(web.calls)), ([], 1))
        self.assertPaused(failed, [i.name for i in items])
        self.assertEqual(self.record(), before)


class CancelTest(AssetsCase):
    def test_cancel_during_throttle_wait(self):
        """限流、正在按 Retry-After 等 60 秒时取消（退出程序）：照旧约 STOP_POLL＋STOP_GRACE 秒内返回，算「已取消」，
        不记冷却。"""
        web = Web({"https://img/": http_error(429, {"Retry-After": "60"})})
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        start = time.monotonic()
        ok, failed = assets.download([self.item("a.png"), self.item("b.png")], stop=stop, opener=web, workers=1)
        self.assertLess(time.monotonic() - start - 0.3, STOP_LIMIT)
        self.assertEqual(ok, [])
        self.assertEqual(sorted(failed), [("heroes/a.png", assets.CANCELLED), ("heroes/b.png", assets.CANCELLED)])
        self.assertIsNone(assets.cooldown())


class OrderTest(AssetsCase):
    def test_recognition_first(self):
        """还缺的图片按识别先用到的排：角色照、Q 版头像、筹码图标，同一类里照清单的先后。"""
        items = [self.item("c1.png", kind="chips"), self.item("p1.png", kind="profiles"), self.item("h1.png"),
                 self.item("c2.png", kind="chips"), self.item("h2.png"), assets.AssetItem("stars", "s.png", bundled=True)]
        got = [i.name for i in assets.missing(assets.Manifest(items))]
        self.assertEqual(got, ["heroes/h1.png", "heroes/h2.png", "profiles/p1.png", "chips/c1.png", "chips/c2.png"])


# ------------------------------------------------------------ 悬浮窗


class OverlayCase(unittest.TestCase):
    """离屏悬浮窗，下载模块换成假的：download 按 self.rounds 依次交回结果（函数时调用它），cooldown 交回 self.pause。
    计时器（later）记下来、由测试自己触发，钟是假的。"""

    def setUp(self):
        self.db = load_db()
        self.errors = capture_errors(self)
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.clock = Clock()
        self.pause: assets.Cooldown | None = None
        self.missing = ["heroes/a.png", "heroes/b.png", "chips/c.png"]
        self.rounds: list = []
        self.calls: list = []
        self.timers: list = []
        fake = SimpleNamespace(
            missing=lambda: list(self.missing), retry_items=lambda: list(self.missing), download=self.fake_download,
            is_write_error=assets.is_write_error, is_paused=assets.is_paused, cooldown=lambda: self.pause,
            PAUSED=assets.PAUSED, COOLDOWN_MINUTES=assets.COOLDOWN_MINUTES, GONE=assets.GONE)
        self.services = Services(assets=fake)
        patcher = mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""})  # 外面设了「试启动不下载」时也照常下载
        patcher.start()
        self.addCleanup(patcher.stop)
        # 这里比的是平时的下载（要下的多，下载期间识别暂停）和被拒绝过以后的下载：界线改小，这几张就算多。只补少量图片、
        # 下载期间识别照常的情形见 tests/test_download_fail.py
        patcher = mock.patch.object(background_mod, "DOWNLOAD_PAUSE_OVER", 2)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.w = self.overlay()

    def overlay(self) -> Overlay:
        w = Overlay(self.db, self.session, threading.Lock(), services=self.services)
        w.run_background = lambda fn: fn()
        w.watcher = FakeWatcher()
        w.background.later = lambda ms, fn: self.timers.append((ms, fn))
        w.background.now = self.clock
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def fake_download(self, items, progress, stop, stats=None):
        """记下每次下载（要下的图片、识别暂停没有），再交回 self.rounds 里的下一轮。没接后台识别（watcher 为 None，例如
        退出以后）时照样记下，免得不该开始的下载因为这里出错而看不出来。"""
        w = self.w
        paused = w.watcher.paused.get("assets") if w.watcher is not None else None
        self.calls.append((list(items), w.background.blocked, paused))
        outcome = self.rounds.pop(0)
        return outcome() if callable(outcome) else outcome

    def blocked_round(self, ok=("heroes/a.png",), paused=("heroes/b.png", "chips/c.png"), minutes=35, others=()):
        """假的一轮：被拦下（第一张带括注），下载模块记下冷却；下好的不再缺（missing 照真的下载模块少了这几张）。others 是
        这一轮另外失败的 [(文件名, 原因)]；其中 wiki 上已删除的也不再缺（7 天内不自动下）。"""

        def run():
            self.pause = assets.Cooldown(self.clock() + minutes * 60, minutes, self.clock(), "HTTP 567（测试）")
            failed = [(name, assets.PAUSED + ("（HTTP 567（测试））" if i == 0 else "")) for i, name in enumerate(paused)]
            done = set(ok) | {name for name, why in others if why == assets.GONE}
            self.missing = [name for name in self.missing if name not in done]
            return list(ok), failed + list(others)

        return run

    def text(self) -> str:
        return self.w.notice_model.texts().get("assets", "")

    def fire(self) -> None:
        """触发最近排的那个计时器。"""
        _, fn = self.timers[-1]
        fn()


class BlockedNoticeTest(OverlayCase):
    def test_notice_when_blocked(self):
        """被拦下：下载那一行写明是站点防护拒绝、几点几分自动接着下、浏览器打开也可能被拒绝、已下载的照常识别，不说检查
        网络；⚠ 橙色，边框不变色；「重试」的悬停说明写明只试一张。识别不暂停（下载期间暂停过，交回以后恢复），图片重新
        加载；情形写进错误记录；排好到点接着下。"""
        w = self.w
        self.rounds = [self.blocked_round()]
        self.assertTrue(w.check_assets())
        when = resume_time(T0 + 35 * 60, T0)
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_BLOCKED.format(time=when, n=2))
        self.assertNotIn("检查网络", self.text())
        self.assertIn("吉星派对 WIKI 的站点防护暂时拒绝了这台电脑的下载", self.text())
        self.assertIn(f"{when} 起自动继续下载其余 2 张图片", self.text())
        buttons = w.notices.buttons("assets")
        self.assertEqual([b.text() for b in buttons], ["重试"])
        self.assertEqual(tip_text(buttons[0].toolTip()), DOWNLOAD_BLOCKED_RETRY.format(time=when))
        self.assertFalse(w.needs_attention())
        self.assertEqual(w.notice_model.notices["assets"][2], "warn")
        tips = w.notice_model.tips(logs="", assets_folder="assets", capture=None, assets=w.background.assets_state)
        self.assertTrue(tips["assets"].endswith(ASSETS_TIP_BLOCKED))
        self.assertEqual(self.calls, [(["heroes/a.png", "heroes/b.png", "chips/c.png"], True, True)])
        self.assertFalse(w.background.blocked)
        self.assertEqual(w.watcher.paused, {"assets": False})
        self.assertEqual(w.watcher.reloaded, 1)
        self.assertNotIn(BLOCKED_BIT, w.status.summary_plain)
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], ["识别所需的图片下载被站点防护暂时拒绝，2 张还没有下载（拾星助手仍在运行）"])
        self.assertIn("认出被拦的情形：" + assets.PAUSED + "（HTTP 567（测试））", entries[0][1])
        self.assertIn(f"已下载 1 张，暂停 2 张；{when} 起自动继续下载（冷却 35 分钟）", entries[0][1])
        self.assertEqual(self.timers[-1][0], background_mod.RESUME_CHECK_MS)

    def test_resumes_at_time(self):
        """到点以前计时器来看：不下载，再排一次；到点以后自动接着下（被拒绝过：识别不暂停），下全了提示收起。"""
        w = self.w
        self.rounds = [self.blocked_round()]
        w.check_assets()
        self.clock.t = T0 + 10 * 60
        self.fire()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.timers), 2)
        self.assertEqual(w.background.assets_state[0], "blocked")
        stale = self.timers[0][1]

        def finish():
            self.pause = None  # 下全了：下载模块清掉冷却记录
            return ["heroes/b.png", "chips/c.png"], []

        self.rounds = [finish]
        self.clock.t = T0 + 35 * 60 + 1
        self.fire()
        self.assertEqual(self.calls[1], (["heroes/b.png", "chips/c.png"], False, False))  # 现在还缺的
        self.assertNotIn("assets", w.notice_model.texts())
        self.assertIn(DOWNLOAD_DONE, w.status.status_text())
        stale()  # 早先排的计时器后来才到：不再下载
        self.assertEqual(len(self.calls), 2)

    def test_startup_during_cooldown(self):
        """启动时上一次的冷却还没到点：不下载，下载那一行写明几点几分接着下，识别照常；到点再下。"""
        self.pause = assets.Cooldown(T0 + 120, 35, T0 - 1980, "HTTP 567")
        w = self.w
        self.assertFalse(w.check_assets())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_BLOCKED.format(time=resume_time(T0 + 120, T0), n=3))
        self.assertFalse(w.background.blocked)
        self.assertEqual(self.timers[-1][0], 120 * 1000 + background_mod.RESUME_MARGIN_MS)
        self.rounds = [([], [])]
        self.clock.t = T0 + 121
        self.fire()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.errors.entries(), [])

    def test_retry_during_cooldown(self):
        """冷却期间点「重试」：交给下载模块（它只试一个请求）。仍被拒：到点的时刻照旧（下载模块没改冷却记录），点之前排的
        「到点接着下」作废（后来才到也不下载、不再排，计时器不越排越多）；离上一次试不到 RETRY_GAP_S 秒再点不算（连点只试
        一次，提示不变）；过了这么久再点照常试，通了：照常下完，提示收起。"""
        w = self.w
        self.rounds = [self.blocked_round()]
        w.check_assets()
        before = self.text()
        stale = self.timers[-1][1]

        def still_blocked():
            return [], [("heroes/b.png", assets.PAUSED + "（HTTP 567（测试））"), ("chips/c.png", assets.PAUSED)]

        self.rounds = [still_blocked, (["heroes/b.png", "chips/c.png"], [])]
        self.clock.t = T0 + 5 * 60
        w.notices.buttons("assets")[0].click()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][0], ["heroes/b.png", "chips/c.png"])
        self.assertEqual(self.text(), before)
        timers = len(self.timers)
        stale()
        self.assertEqual((len(self.timers), len(self.calls)), (timers, 2))
        self.pause = None
        for wait in (0.0, background_mod.RETRY_GAP_S - 1):  # 连点：不再试
            with self.subTest(wait=wait):
                self.clock.t = T0 + 5 * 60 + wait
                w.notices.buttons("assets")[0].click()
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(self.text(), before)
        self.clock.t = T0 + 5 * 60 + background_mod.RETRY_GAP_S
        w.notices.buttons("assets")[0].click()
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("assets", w.notice_model.texts())

    def test_rest_counts_only_still_missing(self):
        """被拦下时「其余 N 张」只数到点接着下时要下的（下载模块的 missing）：这一轮下好的、wiki 上已删除的（7 天内不自动下）
        不算，网络出错等别的失败到点照常再下、算进去；下载模块的 missing 不算的（例如今天记了日期、隔天再试的）也不算，张数
        照它交回的，不按这一轮没下好的张数推算。下载模块交不出 missing 时按这一轮没下好的，去掉已删除的。"""
        w = self.w
        self.missing = ["heroes/a.png", "heroes/b.png", "heroes/g.png", "chips/c.png"]
        others = (("heroes/g.png", assets.GONE), ("chips/c.png", "网络错误：连不上（测试）"))
        self.rounds = [self.blocked_round(paused=("heroes/b.png",), others=others)]
        w.check_assets()
        when = resume_time(T0 + 35 * 60, T0)
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_BLOCKED.format(time=when, n=2))
        self.assertEqual(w.background.assets_state[1], 2)
        reasons = [("heroes/b.png", assets.PAUSED), *others]
        self.missing = ["heroes/b.png"]  # c 今天记了日期（隔天再试），到点时不下
        self.assertEqual(w.background.left_after_block(reasons, 3), 1)
        del self.services.assets.missing
        self.assertEqual(w.background.left_after_block(reasons, 3), 2)

    def test_retry_gap_only_while_cooling(self):
        """连点只试一次只管冷却期间点「重试」：到点以后（计时器还没来）点照常下载，离上一次试不到 RETRY_GAP_S 秒也一样。"""
        w = self.w
        self.rounds = [self.blocked_round()]
        w.check_assets()

        def still_blocked():  # 下载模块没改冷却记录：照旧 T0 + 35 分钟到点
            return [], [("heroes/b.png", assets.PAUSED + "（HTTP 567（测试））"), ("chips/c.png", assets.PAUSED)]

        self.rounds = [still_blocked, (["heroes/b.png", "chips/c.png"], [])]
        self.clock.t = T0 + 35 * 60 - 3
        w.notices.buttons("assets")[0].click()  # 冷却期间试一次，仍被拒
        self.assertEqual((len(self.calls), w.background._resume_at), (2, T0 + 35 * 60))
        self.clock.t = T0 + 35 * 60 + 1  # 到点了，计时器还没来；离上一次试只有 4 秒
        w.notices.buttons("assets")[0].click()
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("assets", w.notice_model.texts())

    def test_resumed_download_keeps_recognition(self):
        """平时的下载期间识别暂停；被拒绝过以后（冷却记录还在）接着下时不暂停，下载那一行的悬停说明也照这样写。"""
        w = self.w
        seen = []

        def during():
            seen.append((w.background.blocked, w.background.assets_state))
            return ["heroes/a.png", "heroes/b.png", "chips/c.png"], []

        self.rounds = [during]
        w.check_assets()
        self.pause = assets.Cooldown(T0 - 1, 35, T0 - 2101, "HTTP 567")
        self.rounds = [during]
        w.check_assets()
        self.assertEqual([b for b, _ in seen], [True, False])
        model = w.notice_model
        tip = [model.tips(logs="", assets_folder="assets", capture=None, assets=state)["assets"] for _, state in seen]
        self.assertTrue(tip[0].endswith(ASSETS_TIP_PAUSED))
        self.assertTrue(tip[1].endswith(ASSETS_TIP_SLOW))

    def test_shutdown_during_cooldown(self):
        """冷却期间退出：没有在跑的下载，马上收尾；排着的「到点接着下」作废。"""
        w = self.w
        self.rounds = [self.blocked_round()]
        w.check_assets()
        w.watcher = None  # 不接后台识别（同 tests/test_overlay_shutdown.py 的「没有识别线程」）
        start = time.monotonic()
        self.assertTrue(w.shutdown())
        self.assertLess(time.monotonic() - start, 1.0)
        self.clock.t = T0 + 36 * 60
        self.rounds = [([], [])]  # 万一又开始下载，下载模块照常交回：断言看的是有没有开始
        self.fire()
        self.assertEqual(len(self.calls), 1)

    def test_no_cooldown_without_record(self):
        """下载模块交回暂停的、却没记下冷却（冷却记录写不进）：照 35 分钟算、进到整分（同记得下时下载模块的取法：写几点
        几分时，写的那一分就是到点的时刻），这次运行里照样到点接着下。"""
        w = self.w

        def run():
            return [], [("heroes/a.png", assets.PAUSED + "（HTTP 567（测试））")]

        self.rounds = [run]
        self.clock.t = T0 + 10  # 不在整分上
        w.check_assets()
        self.assertEqual(w.background._resume_at, T0 + 36 * 60)
        self.assertEqual(w.background.assets_state[3], resume_time(T0 + 36 * 60, T0 + 10))
        self.assertNotEqual(w.background.assets_state[3], resume_time(T0 + 10 + 35 * 60, T0 + 10))
        self.assertEqual(w.background.assets_state[0], "blocked")

    def test_resume_time(self):
        """几点几分的写法：今天的只写时刻，明天的写「明天」，更晚的写月日（数字和汉字之间加空格）。"""
        base = time.mktime((2026, 10, 17, 23, 30, 0, 0, 0, -1))
        self.assertEqual(resume_time(base + 600, base), "23:40")
        self.assertEqual(resume_time(base + 3600, base), "明天 00:30")
        self.assertEqual(resume_time(base + 2 * 86400, base), "10 月 19 日 23:30")


# ------------------------------------------------------------ 点「重试」正好被拦下：到点接着下点过「重试」的那几张

RESUMED = T0 + 35 * 60 + 1  # 第一次被拦（T0）冷却 35 分钟，到点以后


def net_down(url: str) -> Exception:
    """网络出错（连不上这张图所在的图床）：每次请求一个新的异常。"""
    return urllib.error.URLError("连不上（测试）")


class LiveAssets:
    """真的下载模块（覆盖清单、冷却记录照真的读写），只把取图换成假的 opener、等待换成不真等的、钟换成假的。"""

    def __init__(self, web: Web, now: Clock):
        self.web, self.now = web, now

    def __getattr__(self, name: str):
        return getattr(assets, name)

    def download(self, items, progress=None, stop=None, stats=None):
        return assets.download(items, progress, stop, opener=self.web, sleep=lambda seconds: None, clock_now=self.now)

    def cooldown(self):
        return assets.cooldown(self.now())


class RetryBlockedTest(unittest.TestCase):
    """个别下载失败（覆盖清单记了今天，今天不再自动下载）或 wiki 上已删除（记了 gone，几天内不再自动下载）的图片，在下载失败
    那一行点「重试」，这一下正好被站点防护拦下：「其余 N 张」算上点过「重试」的那几张，到点真的接着下（用户点「重试」就是
    要现在下，被拒只是要多等一会儿）。
    悬浮窗离屏，下载模块是真的：清单两张（a、b），识别图片的文件夹指到沙箱，取图换成假的 opener，钟和计时器是假的。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-dlretry-")
        self.a, self.b = self.item("a.png"), self.item("b.png")
        self.manifest = assets.Manifest([self.a, self.b])
        for patcher in (mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind),
                        mock.patch.object(assets, "load_manifest", side_effect=lambda: self.manifest),
                        # 下载模块在内存里记着的（覆盖清单写不进时）：每条测试从空的开始，测完换回
                        mock.patch.object(assets, "_forgotten", set()),
                        mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = load_db()
        self.errors = capture_errors(self)
        self.clock = Clock()
        self.timers: list = []
        self.web = Web({})
        self.services = Services(assets=LiveAssets(self.web, self.clock))
        self.today = clock.today().isoformat()
        self.w = self.overlay()

    def item(self, name: str) -> assets.AssetItem:
        data = png(name.encode())
        return assets.AssetItem("heroes", name, name, f"https://img/{name}", len(data), sha1(data))

    def overlay(self) -> Overlay:
        """一个悬浮窗（重启时再建一个：新的 Background，覆盖清单、冷却记录照样从文件读）。"""
        w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock(), services=self.services)
        w.run_background = lambda fn: fn()
        w.watcher = FakeWatcher()
        w.background.later = lambda ms, fn: self.timers.append((ms, fn))
        w.background.now = self.clock
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def text(self, w: Overlay | None = None) -> str:
        return (w or self.w).notice_model.texts().get("assets", "")

    def blocked_text(self, n: int) -> str:
        """被拦下那一行：T0 起冷却 35 分钟，到点接着下其余 n 张。"""
        return "⚠ " + DOWNLOAD_BLOCKED.format(time=resume_time(T0 + 35 * 60, self.clock()), n=n)

    def fail_first(self, value) -> None:
        """启动时下载：a 按 value 回（网络出错，或 404 且接口里也没有），b 下好；交回以后是个别图片下载失败的那一行。"""
        self.web.routes = {"https://img/a.png": value, "https://img/b.png": png(b"b.png"), assets.API: imageinfo({})}
        self.assertTrue(self.w.check_assets())
        self.assertEqual(self.w.background.assets_state[:3], ("failed", 1, 1))

    def retry_blocked(self) -> None:
        """在下载失败那一行点「重试」，这一下正好被站点防护拦下（567）。"""
        self.web.routes = {"https://img/": http_error(567)}
        calls = len(self.web.calls)
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(self.web.calls[calls:], ["https://img/a.png"])

    def resume(self, value) -> None:
        """冷却到点：a 按 value 回，触发最近排的「到点接着下」。"""
        self.web.routes = {"https://img/a.png": value}
        self.clock.t = RESUMED
        _, fn = self.timers[-1]
        fn()

    def test_failed_today_retry_blocked_resumes(self):
        """个别下载失败（记了今天）→ 点「重试」→ 这一下被拦：「其余 1 张」，覆盖清单里这张的记号去掉了；到点真的去下，
        下好了这一行消失，冷却记录清掉。"""
        self.fail_first(net_down)
        self.assertEqual(self.text(), "⚠ " + notices.few_failed_text(1))
        self.assertEqual(assets.load_local()["failed"], {self.a.name: self.today})
        self.retry_blocked()
        self.assertEqual(self.text(), self.blocked_text(1))
        self.assertEqual(self.w.background.assets_state[1], 1)
        self.assertEqual(assets.load_local()["failed"], {})
        self.resume(png(b"a.png"))
        self.assertEqual(self.web.calls[-1], "https://img/a.png")
        self.assertTrue(self.a.path.exists())
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.assertIsNone(assets.cooldown(self.clock()))
        self.assertEqual(assets.load_local()["failed"], {})

    def test_failed_today_retry_blocked_fails_again(self):
        """同上，到点还下不了（网络出错）：回到「有 1 张识别图片下载失败……」那一行，这张重新记今天的日期（今天再启动时不再
        自动下载）。"""
        self.fail_first(net_down)
        self.retry_blocked()
        self.resume(net_down)
        self.assertEqual(self.web.calls[-assets.RETRIES:], ["https://img/a.png"] * assets.RETRIES)
        self.assertEqual(self.w.background.assets_state, ("failed", 1, 0, "few"))
        self.assertEqual(self.text(), "⚠ " + notices.few_failed_text(1))
        self.assertEqual(assets.load_local()["failed"], {self.a.name: self.today})
        self.assertEqual(assets.missing(), [])

    def test_restart_during_cooldown_resumes(self):
        """点「重试」被拦以后中途退出、当天再打开（新的悬浮窗，覆盖清单从文件读）：还在冷却，不下载，下载那一行照样写几点
        几分接着下「其余 1 张」；冷却结束后接着下这张。"""
        self.fail_first(net_down)
        self.retry_blocked()
        self.w.watcher = None
        self.assertTrue(self.w.shutdown())
        calls = len(self.web.calls)
        self.clock.t = T0 + 10 * 60
        w = self.overlay()
        self.assertFalse(w.check_assets())
        self.assertEqual(len(self.web.calls), calls)
        self.assertEqual(self.text(w), self.blocked_text(1))
        self.resume(png(b"a.png"))
        self.assertEqual(self.web.calls[calls:], ["https://img/a.png"])
        self.assertTrue(self.a.path.exists())
        self.assertNotIn("assets", w.notice_model.texts())

    def test_gone_retry_blocked_resumes(self):
        """wiki 上已删除（记了 gone，7 天内不再自动下载）的同样：点「重试」去掉这笔记录，这一下被拦时「其余 1 张」，到点
        接着下；wiki 上又有了这张时下好，这一行消失。"""
        self.fail_first(http_error(404))
        self.assertEqual(self.text(), "⚠ " + notices.few_failed_text(1, later=True))
        self.assertIn(self.a.name, assets.load_local()["gone"])
        self.retry_blocked()
        self.assertEqual(self.text(), self.blocked_text(1))
        self.assertEqual(assets.load_local()["gone"], {})
        self.resume(png(b"a.png"))
        self.assertTrue(self.a.path.exists())
        self.assertNotIn("assets", self.w.notice_model.texts())

    def test_gone_retry_blocked_gone_again(self):
        """wiki 上已删除的点「重试」被拦，到点再下还是删了（404、接口里也没有）：回到下载失败那一行（「之后会自动再试」），
        重新记下已删除的日期。"""
        self.fail_first(http_error(404))
        self.retry_blocked()
        self.web.routes = {"https://img/a.png": http_error(404), assets.API: imageinfo({})}
        self.clock.t = RESUMED
        self.timers[-1][1]()
        self.assertEqual(self.w.background.assets_state, ("failed", 1, 0, "few_later"))
        self.assertEqual(assets.load_local()["gone"][self.a.name]["date"], self.today)
        self.assertEqual(assets.missing(), [])

    def test_marks_unwritable(self):
        """覆盖清单写不进、记号去不掉：不出错（错误记录里只有下载失败、被拦各一条），这次运行里照样「其余 1 张」、到点接着下
        （下载模块在内存里记着点过「重试」的这几张）；覆盖清单里的记号原样留着（重启以后照它，同没点「重试」，见
        tests/test_download_fail.py 的 ForgetTest）。"""
        self.fail_first(net_down)
        real = fileio.write_text

        def locked(path, text, **kwargs):
            if Path(path).name == assets.LOCAL_MANIFEST:
                raise PermissionError(13, "拒绝访问（测试）", str(path))
            return real(path, text, **kwargs)

        with mock.patch.object(fileio, "write_text", side_effect=locked):
            self.retry_blocked()
            self.assertEqual(self.text(), self.blocked_text(1))
            self.assertEqual(assets.load_local()["failed"], {self.a.name: self.today})
            self.assertEqual([title for title, _ in self.errors.entries()],
                             ["识别所需的图片有 1 张下载失败（拾星助手仍在运行）",
                              "识别所需的图片下载被站点防护暂时拒绝，1 张还没有下载（拾星助手仍在运行）"])
            self.resume(png(b"a.png"))
        self.assertTrue(self.a.path.exists())
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.assertEqual(len(self.errors.entries()), 2)

    def test_not_retry_keeps_skipping_marked(self):
        """没点「重试」的照旧不下近期记了号的：a 今天个别下载失败、c 记了 wiki 上已删除，启动时只下 b（被拦，「其余 1 张」）；
        冷却期间点「重试」照旧只试一个请求（b），不去记号；冷却到点也只下 b。a、c 的记号原样留着。"""
        c = self.item("c.png")
        self.manifest = assets.Manifest([self.a, self.b, c])
        assets._note_failed([self.a.name])
        assets._update_local(c.name, gone={"date": self.today, "title": c.title})
        marks = assets.load_local()
        self.web.routes = {"https://img/": http_error(567)}
        self.assertTrue(self.w.check_assets())
        self.assertEqual(self.web.calls, ["https://img/b.png"])
        self.assertEqual(self.text(), self.blocked_text(1))
        self.clock.t = T0 + 5 * 60
        self.w.notices.buttons("assets")[0].click()  # 冷却期间点「重试」：只试一个请求，仍被拒
        self.assertEqual(self.web.calls, ["https://img/b.png"] * 2)
        self.assertEqual(self.text(), self.blocked_text(1))
        self.web.routes = {"https://img/": lambda url: png(url.rsplit("/", 1)[1].encode())}
        self.clock.t = RESUMED
        self.timers[-1][1]()
        self.assertEqual(self.web.calls, ["https://img/b.png"] * 3)
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.assertEqual(assets.load_local(), marks)


if __name__ == "__main__":
    unittest.main()
