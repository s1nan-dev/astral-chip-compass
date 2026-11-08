"""个别识别图片一直下不了时的处理（advisor/assets.py 模块说明「个别图片一直下不了」）。

- 下载模块：一轮下完、属于个别图片下不了（isolated）时，这一张自己的问题的那几张（is_own_failure：网络出错、5xx、卡面
  裁不出等）记进本地覆盖清单的 failed（今天的日期）；当天 missing 不算它们，第二天算；点「重试」（retry_items）照样算；
  记录坏了、日期写得不对、比今天还晚的当没记；下好了清掉这笔；这张别的记录（wiki 上更新过的地址）照留；覆盖清单写不进时
  不出错，下次照旧再下。点「重试」时去记号（forget_recent）：只去给的那几张的 failed、gone，别的记录、别的图片照留，没有
  要去的不写盘，写不进时不出错、这次运行里记在内存里；missing、retry_items 不动记号。
- 不记的：一轮失败超过 FAILED_MANY 张（按总张数，写不进、已删除的也算进去：像断网、几百张写不进）；这一轮被拦下（暂停）、
  取消了、有建不了下载会话的（NO_SESSION，连同同一轮里别的失败）；写不进、没接上裁图、wiki 上已删除（另记日期）、最后报成
  限流（429、503：站点那边忙）的。wiki 上已删除的日期和个别下载失败的一样：比今天还晚、写得不对的当没记。
- 悬浮窗：要下的不多于 DOWNLOAD_PAUSE_OVER 张时下载期间识别不暂停（热键照常、不写「识别 暂不可用」），多的照旧暂停；个别
  图片下不了时下载那一行换成其余识别照常、明天自动再试的那一句（1 张、多张两种写法；混着 wiki 上已删除、写不进等别的失败
  时写「之后会自动再试」），带「重试」和「关闭本条提示」，关掉以后这次运行不再显示、再下载再失败时又出现；全部或大部分没
  下下来、建不了下载会话时照旧说检查网络、不能关（失败的全是卡面裁不出时换成下载后处理失败的那一句，写明错误记录的位置）；
  悬停说明按情形写（大部分没下下来、里面有 wiki 上已删除的，写下次启动时重新下载、已删除的 7 天后再试的那一句，「7 天」和
  GONE_RETRY_DAYS 一致，使用说明里的也是）；错误记录写明哪几张明天再自动下载；当天跳过的那几张不下载、不提示；点「重试」
  开始下载之前先叫下载模块去记号（启动时不去；交不出这个接口、出错时照旧下载）。

不联网：下载换成假的 opener，识别图片的文件夹指到测试沙箱的临时文件夹，等待换成不真等的 sleep，「今天」由 ACC_TODAY 钉死
（tests/__init__.py，第二天另设）。悬浮窗离屏，下载模块换成假的，分「个别」「大部分」照用下载模块自己的 isolated、deferred。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from advisor import assets, clock, fileio, paths
from advisor.data import load_db
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import background as background_mod
from advisor.ui.overlay_parts import notices
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.wording import (ASSETS_TIP_FAILED, ASSETS_TIP_FEW, ASSETS_TIP_GONE, ASSETS_TIP_LATER,
                                              ASSETS_TIP_PAUSED, ASSETS_TIP_QUIET, ASSETS_TIP_SLOW, BLOCKED_BIT,
                                              BLOCKED_HOTKEY, DISMISS, DOWNLOAD_CROP_FAILED, DOWNLOAD_FAILED, ERR_RECORD,
                                              FOLDER_UNWRITABLE, NO_DOWNLOAD_ENV)
from advisor.ui.services import Services
from tests.support import sandbox
from tests.support.errors import capture_errors
from tests.support.overlay_cases import FakeWatcher, tip_text  # 导入时建好 QApplication

NET = "网络错误：连不上（测试）"
WRITE = f"{assets.WRITE_FAILED}：PermissionError: [WinError 5] 拒绝访问。"
# 个别图片下不了时下载那一行的写法（多张时写「有 N 张」「用到这些图片的」），逐字核对
ONE_TEXT = ("⚠ 有 1 张识别图片下载失败，用到这张图片的立绘、头像或筹码暂时无法识别，其余识别照常。明天会自动再试，"
            "也可以点击右侧「重试」按钮。")
THREE_TEXT = ("⚠ 有 3 张识别图片下载失败，用到这些图片的立绘、头像或筹码暂时无法识别，其余识别照常。明天会自动再试，"
              "也可以点击右侧「重试」按钮。")
# 混着别的失败（wiki 上已删除、写不进等，只有已删除的也算）：「明天」改成「之后」，其余字不变
THREE_LATER = ("⚠ 有 3 张识别图片下载失败，用到这些图片的立绘、头像或筹码暂时无法识别，其余识别照常。之后会自动再试，"
               "也可以点击右侧「重试」按钮。")
ONE_LATER = ("⚠ 有 1 张识别图片下载失败，用到这张图片的立绘、头像或筹码暂时无法识别，其余识别照常。之后会自动再试，"
             "也可以点击右侧「重试」按钮。")
NO_SESSION = f"{assets.NO_SESSION}：OSError: 读不了系统的证书（测试）"


def png(name: str) -> bytes:
    return assets.PNG_MAGIC + name.encode() * 4


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", headers or {}, io.BytesIO(b""))


class Web:
    """假的 opener：按网址的开头回内容（bytes 或异常），没有的回 404；记下每次请求的网址。"""

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


def down(url: str):
    """网络出错（连不上这张图所在的图床）。"""
    raise urllib.error.URLError("连不上（测试）")


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def next_day():
    """「今天」拨到明天（改 ACC_TODAY，离开时换回）。"""
    return mock.patch.dict(os.environ, {clock.ENV: date.fromordinal(clock.today().toordinal() + 1).isoformat()})


class AssetsCase(unittest.TestCase):
    """识别图片的文件夹指到沙箱里的临时文件夹；等待不真等；「今天」是 tests/__init__.py 钉死的 ACC_TODAY。"""

    def setUp(self):
        self.base = sandbox.temp_dir(self, "test-dlfail-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": self.base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.today = clock.today().isoformat()

    def item(self, name: str, kind: str = "heroes", **kw) -> assets.AssetItem:
        data = png(name)
        return assets.AssetItem(kind, name, name, f"https://img/{name}", len(data), sha1(data), **kw)

    def download(self, items, routes: dict, **kw):
        kw.setdefault("workers", 1)
        kw.setdefault("sleep", lambda seconds: None)
        return assets.download(items, opener=Web(routes), **kw)

    def failed_records(self) -> dict:
        return assets.load_local()["failed"]

    def write_local(self, data) -> None:
        (self.base / assets.LOCAL_MANIFEST).write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------ 下载模块：记下、跳过、隔天再试


class RecordTest(AssetsCase):
    def test_one_network_failure_recorded_and_skipped_today(self):
        """一张网络出错（试满 RETRIES 次）、另一张下好：失败的那张记下今天的日期；当天 missing 不算它（下次启动不再等它），
        点「重试」时算（retry_items）；第二天 missing 又算它，自动再下。"""
        good, bad = self.item("good.png"), self.item("bad.png")
        manifest = assets.Manifest([good, bad])
        web_routes = {"https://img/good.png": png("good.png"), "https://img/bad.png": down}
        ok, failed = self.download([good, bad], web_routes)
        self.assertEqual(ok, [good.name])
        self.assertEqual([(n, why.startswith("网络错误")) for n, why in failed], [(bad.name, True)])
        self.assertEqual(self.failed_records(), {bad.name: self.today})
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing(manifest, retry_failed=True), [bad])
        with mock.patch.object(assets, "load_manifest", return_value=manifest):
            self.assertEqual(assets.retry_items(), [bad])
        with next_day():
            self.assertEqual(assets.missing(manifest), [bad])

    def test_other_own_failures_recorded(self):
        """这一张自己的问题都记：5xx 试满次数、单独一次不带页面的 403、问过接口仍对不上、卡面裁不出。"""

        def broken(data, crop):
            raise ValueError("测试")

        card = self.item("card.png", crop=(0.5, 0, 10, 0, 0.5, 10))
        items = [self.item("e500.png"), self.item("e403.png"), self.item("bad_sha.png"), card]
        routes = {"https://img/e500.png": http_error(500), "https://img/e403.png": http_error(403),
                  "https://img/bad_sha.png": png("别的内容"), "https://img/card.png": png("card.png"),
                  assets.API: b"{}"}
        ok, failed = self.download(items, routes, cropper=broken)
        self.assertEqual(ok, [])
        reasons = dict(failed)
        self.assertTrue(reasons[card.name].startswith(assets.CROP_FAILED), reasons)
        self.assertTrue(all(assets.is_own_failure(why) for why in reasons.values()), reasons)
        self.assertEqual(self.failed_records(), {i.name: self.today for i in items})

    def test_many_failures_not_recorded(self):
        """这一轮失败超过 FAILED_MANY 张（像整个网络不通、第一次运行时断网）：一张都不记，下次启动照常全下；正好
        FAILED_MANY 张的照记。"""
        many = [self.item(f"m{i:02d}.png") for i in range(assets.FAILED_MANY + 1)]
        ok, failed = self.download(many, {"https://img/": down}, workers=2)
        self.assertEqual((ok, len(failed)), ([], len(many)))
        self.assertEqual(self.failed_records(), {})
        self.assertEqual(len(assets.missing(assets.Manifest(many))), len(many))
        edge = many[:assets.FAILED_MANY]
        self.download(edge, {"https://img/": down}, workers=2)
        self.assertEqual(sorted(self.failed_records()), sorted(i.name for i in edge))

    def test_many_write_errors_count_toward_limit(self):
        """失败按总张数比 FAILED_MANY，不分哪一类：300 张写不进（例如磁盘满了）混着 1 张网络出错，不算个别图片下不了，那一张
        网络出错的也不记（下次启动照常再下）；20 张以内混着写不进的（19 张写不进 + 1 张网络出错）照旧算个别，只记网络出错的
        那一张。"""
        writes = [self.item(f"w{i:03d}.png") for i in range(300)]
        net = self.item("n.png")
        real = fileio.write_bytes

        def full(path, data, **kwargs):
            if Path(path).name.startswith("w"):
                raise OSError(28, "磁盘空间不足（测试）", str(path))
            return real(path, data, **kwargs)

        routes = {"https://img/w": lambda url: png(url.rsplit("/", 1)[1]), "https://img/n.png": down}
        with mock.patch.object(fileio, "write_bytes", side_effect=full):
            ok, failed = self.download([*writes, net], routes, workers=2)
            self.assertEqual((ok, len(failed)), ([], 301))
            self.assertEqual(sum(1 for _, why in failed if assets.is_write_error(why)), 300)
            self.assertEqual((assets.isolated(failed), assets.deferred(failed)), (False, []))
            self.assertEqual(self.failed_records(), {})
            self.assertEqual(assets.missing(assets.Manifest([net])), [net])
            ok, failed = self.download([*writes[:assets.FAILED_MANY - 1], net], routes, workers=2)
        self.assertEqual(len(failed), assets.FAILED_MANY)
        self.assertEqual((assets.isolated(failed), assets.deferred(failed)), (True, [net.name]))
        self.assertEqual(self.failed_records(), {net.name: self.today})

    def test_blocked_round_not_recorded(self):
        """这一轮被站点防护拦下（567）：暂停的不记（冷却另管），同一轮里先出了网络错误的那张也不记（没下完，分不清是个别
        图片下不了还是整个连不上）。"""
        a, b = self.item("a.png"), self.item("b.png")
        ok, failed = self.download([a, b], {"https://img/a.png": down, "https://img/b.png": http_error(567)})
        reasons = dict(failed)
        self.assertTrue(reasons[a.name].startswith("网络错误"), reasons)
        self.assertTrue(assets.is_paused(reasons[b.name]), reasons)
        self.assertEqual(self.failed_records(), {})
        self.assertIsNotNone(assets.cooldown())

    def test_cancelled_round_not_recorded(self):
        """取消了（退出程序）：取消的不记，同一轮里已经网络出错的那张也不记。第二张一直被限流、按 Retry-After 等 60 秒，
        第一张处理完时置停止标志，等待马上结束。"""
        a, b = self.item("a.png"), self.item("b.png")
        stop = threading.Event()
        routes = {"https://img/a.png": down, "https://img/b.png": http_error(429, {"Retry-After": "60"})}
        with mock.patch.object(assets, "BACKOFF", (0.0, 0.0)), mock.patch.object(assets, "RATE", math.inf):
            ok, failed = assets.download([a, b], lambda done, total, name: stop.set(), stop, opener=Web(routes),
                                         workers=1)
        self.assertEqual(dict(failed)[b.name], assets.CANCELLED)
        self.assertTrue(dict(failed)[a.name].startswith("网络错误"), failed)
        self.assertEqual(self.failed_records(), {})

    def test_not_own_failures_not_recorded(self):
        """写不进（换个位置才写得进）、没接上裁图（程序的问题）、wiki 上已删除（另记日期，7 天后再试）都不记 failed；
        同一轮里网络出错的那张照记。"""
        unwritable, net = self.item("w.png"), self.item("n.png")
        crop = self.item("c.png", crop=(0.5, 0, 10, 0, 0.5, 10))
        gone = self.item("g.png")
        real = fileio.write_bytes

        def flaky(path, data, **kwargs):
            if Path(path).name == unwritable.file:
                raise PermissionError(13, "拒绝访问", str(path))
            return real(path, data, **kwargs)

        missing_page = json.dumps({"query": {"pages": [{"title": "File:g.png", "missing": True}]}}).encode()
        routes = {"https://img/w.png": png("w.png"), "https://img/n.png": down, "https://img/g.png": http_error(404),
                  assets.API: missing_page}
        with mock.patch.object(fileio, "write_bytes", side_effect=flaky), mock.patch.object(assets, "_cropper", None):
            ok, failed = self.download([unwritable, net, crop, gone], routes, cropper=None)
        reasons = dict(failed)
        self.assertTrue(assets.is_write_error(reasons[unwritable.name]), reasons)
        self.assertEqual((reasons[crop.name], reasons[gone.name]), (assets.NO_CROPPER, assets.GONE))
        self.assertEqual(self.failed_records(), {net.name: self.today})
        self.assertIn(gone.name, assets.load_local()["gone"])

    def test_no_session_not_recorded(self):
        """下载线程建不了下载会话（读不了系统的证书、代理设置不对等）：每张的原因以 NO_SESSION 开头、后接出错的原文，不是哪
        一张图的问题，一张都不记（照全部或大部分没下下来算），下次启动照常再下；同一轮里别的线程网络出错的那张也不记。
        取图换成真的（不给 opener），建会话的那一步换成出错的替身：不发任何请求。"""
        items = [self.item("a.png"), self.item("b.png")]
        with mock.patch.object(assets, "_KeepAlive", side_effect=OSError("读不了系统的证书（测试）")):
            ok, failed = assets.download(items, sleep=lambda seconds: None, workers=2)
        self.assertEqual(ok, [])
        self.assertEqual(sorted(n for n, _ in failed), sorted(i.name for i in items))
        for _, why in failed:
            self.assertTrue(why.startswith(assets.NO_SESSION), why)
            self.assertIn("OSError: 读不了系统的证书（测试）", why)
            self.assertFalse(assets.is_own_failure(why))
        self.assertFalse(assets.isolated(failed))
        self.assertEqual(self.failed_records(), {})
        self.assertEqual(assets.missing(assets.Manifest(items)), items)
        # 混着一张网络出错的（另一条下载线程建好了会话）：照样一张都不记
        mixed = [(items[0].name, NET), (items[1].name, NO_SESSION)]
        self.assertEqual((assets.isolated(mixed), assets.deferred(mixed)), (False, []))

    def test_success_clears_record_and_other_records_kept(self):
        """下好了清掉这一笔；记下失败时这张别的记录（wiki 上更新过的地址和校验值）照留。"""
        a, b = self.item("a.png"), self.item("b.png")
        moved = png("b-new.png")
        self.write_local({"failed": {a.name: self.today},
                          "updated": {b.name: {"url": "https://new/b.png", "size": len(moved), "sha1": sha1(moved)}}})
        ok, failed = self.download([a, b], {"https://img/a.png": png("a.png"), "https://new/b.png": down})
        self.assertEqual((ok, [n for n, _ in failed]), ([a.name], [b.name]))
        local = assets.load_local()
        self.assertEqual(local["failed"], {b.name: self.today})
        self.assertEqual(local["updated"][b.name]["url"], "https://new/b.png")
        ok, failed = self.download([b], {"https://new/b.png": moved})
        self.assertEqual((ok, failed), ([b.name], []))
        self.assertEqual(assets.load_local()["failed"], {})

    def test_diagnostics_still_count_deferred(self):
        """诊断包 version.txt 的「识别图片：缺 N 张」照旧把今天记了失败、先不自动下载的那几张算进去，不写成齐全（启动时的
        missing 不算它们）。"""
        from advisor import diagnostics

        item = self.item("a.png")
        with mock.patch.object(assets, "load_manifest", return_value=assets.Manifest([item])):
            assets._note_failed([item.name])
            self.assertEqual(assets.missing(), [])
            self.assertIn("识别图片：缺 1 张", diagnostics.environment_text(machine={"error": "测试"}))

    def test_throttled_to_the_end_not_recorded(self):
        """一张图试满 RETRIES 次都是限流（429；几条下载线程交替收到正常回话、凑不到连着 REJECT_LIMIT 次被拒时会这样，这里把
        界线调大来造）：最后报成「HTTP 429」，这说明的是站点那边忙，不是这一张的问题，不记日期，下次启动照常再下；同一轮里
        网络出错的那张照记。"""
        busy, net = self.item("busy.png"), self.item("net.png")
        routes = {"https://img/busy.png": http_error(429, {"Retry-After": "1"}), "https://img/net.png": down}
        with mock.patch.object(assets, "REJECT_LIMIT", assets.RETRIES + 1):
            ok, failed = self.download([busy, net], routes)
        reasons = dict(failed)
        self.assertEqual(reasons[busy.name], "HTTP 429")
        self.assertFalse(assets.is_own_failure(reasons[busy.name]))
        self.assertEqual(self.failed_records(), {net.name: self.today})
        self.assertEqual(assets.missing(assets.Manifest([busy, net])), [busy])

    def test_unwritable_local_manifest(self):
        """覆盖清单写不进：记不下来就算了，不出错；下次启动照旧再下。"""
        a = self.item("a.png")
        with mock.patch.object(fileio, "write_text", side_effect=PermissionError(13, "拒绝访问")):
            ok, failed = self.download([a], {"https://img/a.png": down})
        self.assertEqual([n for n, _ in failed], [a.name])
        self.assertEqual(self.failed_records(), {})
        self.assertEqual(assets.missing(assets.Manifest([a])), [a])


class SkipTest(AssetsCase):
    def test_bad_records_ignored(self):
        """覆盖清单里失败的日期：不是文字的丢掉；写得不对（不是 YYYY-MM-DD、没有这一天）、比今天还晚的当没记（照常下载，
        不至于一直等）；这一节整个写坏了、文件读不出来时都照常下载。"""
        names = ["bad_day.png", "no_dash.png", "number.png", "future.png", "today.png"]
        items = [self.item(n) for n in names]
        manifest = assets.Manifest(items)
        with next_day():
            future = clock.today().isoformat()
        self.write_local({"failed": {
            "heroes/bad_day.png": "2026-13-45", "heroes/no_dash.png": self.today.replace("-", ""),
            "heroes/number.png": 5, "heroes/future.png": future, "heroes/today.png": self.today}})
        self.assertNotIn("heroes/number.png", assets.load_local()["failed"])
        self.assertEqual([i.file for i in assets.missing(manifest)], names[:4])
        for broken in ({"failed": ["heroes/today.png"]}, {"failed": "坏了"}):
            with self.subTest(broken=broken):
                self.write_local(broken)
                self.assertEqual(assets.load_local()["failed"], {})
                self.assertEqual(len(assets.missing(manifest)), len(items))
        (self.base / assets.LOCAL_MANIFEST).write_text("{坏", encoding="utf-8")
        self.assertEqual(len(assets.missing(manifest)), len(items))

    def test_old_local_manifest_without_section(self):
        """以前的覆盖清单（没有 failed 这一节）、传进来的旧格式记录：当没记。"""
        item = self.item("a.png")
        self.write_local({"updated": {}, "gone": {}})
        self.assertEqual(assets.load_local()["failed"], {})
        self.assertFalse(assets._recently_failed(item, {"updated": {}, "gone": {}, "cropped": {}}))
        self.assertEqual(assets.missing(assets.Manifest([item])), [item])

    def test_gone_dates_like_failed(self):
        """wiki 上已删除的日期和个别下载失败的一样：比今天还晚（系统时钟往回拨过、手改过）、写得不对（不是 YYYY-MM-DD）的当
        没记，照常下载，不至于一直跳过到那天以后再过 7 天；从记下那天起 7 天以内的照旧跳过（现在的写法、以前只有日期的写法
        都一样）。"""
        with next_day():
            future = clock.today().isoformat()
        base = clock.today().toordinal()

        def ago(days: int) -> str:
            return date.fromordinal(base - days).isoformat()

        records = {
            "future.png": {"date": future, "title": "future.png"}, "future_old.png": future,
            "basic.png": {"date": self.today.replace("-", ""), "title": "basic.png"},
            "today.png": {"date": self.today, "title": "today.png"}, "today_old.png": self.today,
            "six_days.png": {"date": ago(assets.GONE_RETRY_DAYS - 1), "title": "six_days.png"},
            "seven_days.png": {"date": ago(assets.GONE_RETRY_DAYS), "title": "seven_days.png"},
        }
        items = [self.item(name) for name in records]
        self.write_local({"gone": {f"heroes/{name}": record for name, record in records.items()}})
        self.assertEqual([i.file for i in assets.missing(assets.Manifest(items))],
                         ["future.png", "future_old.png", "basic.png", "seven_days.png"])

    def test_record_lasts_one_day(self):
        """记下的当天不算，第二天起算（FAILED_RETRY_DAYS = 1）；wiki 上已删除的照旧 7 天（两样互不影响）。"""
        failed, gone = self.item("f.png"), self.item("g.png")
        self.write_local({"failed": {failed.name: self.today}, "gone": {gone.name: self.today}})
        manifest = assets.Manifest([failed, gone])
        self.assertEqual(assets.missing(manifest), [])
        self.assertEqual(assets.missing(manifest, retry_gone=True), [gone])
        self.assertEqual(assets.missing(manifest, retry_gone=True, retry_failed=True), [failed, gone])
        with next_day():
            self.assertEqual(assets.missing(manifest), [failed])
        self.assertEqual(assets.FAILED_RETRY_DAYS, 1)


class ForgetTest(AssetsCase):
    """用户点「重试」时去掉近期不再自动下载的记号（forget_recent）：只去给的这几张的 failed、gone，这几张别的记录、别的图片
    的记号都照留；没有要去的不写盘；写不进时不出错、这次运行里在内存里记着；missing、retry_items 这类查询不动记号。悬浮窗那边
    的整个过程（点「重试」正好被拦下、到点接着下）见 tests/test_download_block.py 的 RetryBlockedTest。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(assets, "_forgotten", set())  # 内存里记着的：每条测试从空的开始
        patcher.start()
        self.addCleanup(patcher.stop)

    def local_bytes(self) -> bytes:
        return (self.base / assets.LOCAL_MANIFEST).read_bytes()

    def test_only_given_marks_removed(self):
        """去 a（今天个别下载失败）、b（wiki 上已删除）的记号：a 的更新记录、现裁记录照留；c（个别下载失败）、d（已删除）
        不在给的里面，记号照留；去了以后 missing 照常算 a、b。"""
        a, b, c, d = (self.item(name) for name in ("a.png", "b.png", "c.png", "d.png"))
        moved = png("a-new.png")
        updated = {"url": "https://new/a.png", "size": len(moved), "sha1": sha1(moved)}
        cropped = {"card": "0" * 40, "crop": [0.5, 0, 10, 0, 0.5, 10], "size": 10}
        self.write_local({
            "failed": {a.name: self.today, c.name: self.today},
            "gone": {b.name: {"date": self.today, "title": b.title}, d.name: {"date": self.today, "title": d.title}},
            "updated": {a.name: updated}, "cropped": {a.name: cropped},
        })
        manifest = assets.Manifest([a, b, c, d])
        self.assertEqual(assets.missing(manifest), [])
        self.assertTrue(assets.forget_recent([a.name, b.name]))
        local = assets.load_local()
        self.assertEqual(local["failed"], {c.name: self.today})
        self.assertEqual(local["gone"], {d.name: {"date": self.today, "title": d.title}})
        self.assertEqual((local["updated"], local["cropped"]), ({a.name: updated}, {a.name: cropped}))
        self.assertEqual(assets.missing(manifest), [a, b])
        self.assertEqual(assets._forgotten, set())

    def test_nothing_to_forget_no_write(self):
        """给的几张都没有记号（没记过、只有别的记录）：不写盘，覆盖清单原样（没有这个文件时也不建）。"""
        a, b = self.item("a.png"), self.item("b.png")
        self.assertTrue(assets.forget_recent([a.name]))
        self.assertFalse((self.base / assets.LOCAL_MANIFEST).exists())
        self.write_local({"failed": {b.name: self.today}})
        before = self.local_bytes()
        self.assertTrue(assets.forget_recent([a.name]))
        self.assertTrue(assets.forget_recent([]))
        self.assertEqual(self.local_bytes(), before)

    def test_queries_do_not_forget(self):
        """missing、retry_items（点「重试」时要下的）只读：调用以后覆盖清单一个字节都不变，启动时照旧不算记了号的几张。"""
        a, b = self.item("a.png"), self.item("b.png")
        self.write_local({"failed": {a.name: self.today}, "gone": {b.name: {"date": self.today, "title": b.title}}})
        before = self.local_bytes()
        manifest = assets.Manifest([a, b])
        self.assertEqual(assets.missing(manifest, retry_gone=True, retry_failed=True), [a, b])
        with mock.patch.object(assets, "load_manifest", return_value=manifest):
            self.assertEqual(assets.retry_items(), [a, b])
            self.assertEqual(assets.missing(), [])
        self.assertEqual(self.local_bytes(), before)
        self.assertEqual(assets.missing(manifest), [])

    def test_unwritable_kept_in_memory(self):
        """覆盖清单写不进：不出错，记号原样留在文件里；这次运行里 missing 当 a 没有记号（b 没给，照旧不算）；重启以后（内存
        里的没了）照文件里的记号。这次运行里 a 又失败（重新记今天）以后照记号算；存好了从内存里拿掉。"""
        a, b = self.item("a.png"), self.item("b.png")
        self.write_local({"failed": {a.name: self.today, b.name: self.today}})
        before = self.local_bytes()
        manifest = assets.Manifest([a, b])
        with mock.patch.object(fileio, "write_text", side_effect=PermissionError(13, "拒绝访问（测试）")):
            self.assertFalse(assets.forget_recent([a.name]))
            self.assertEqual(self.local_bytes(), before)
            self.assertEqual(assets.missing(manifest), [a])
            with mock.patch.object(assets, "_forgotten", set()):  # 重启
                self.assertEqual(assets.missing(manifest), [])
            assets._note_failed([a.name])  # 又失败了：写不进也照记号算
            self.assertEqual(assets.missing(manifest), [])
            self.assertFalse(assets.forget_recent([a.name]))
            ok, failed = self.download([a], {"https://img/a.png": png("a.png")})
        self.assertEqual((ok, failed), ([a.name], []))
        self.assertEqual(assets._forgotten, set())
        self.assertEqual(assets.missing(manifest), [])
        self.assertTrue(assets.forget_recent([a.name, b.name]))  # 写得进了：照常去掉
        self.assertEqual(assets.load_local()["failed"], {})
        self.assertEqual(assets.missing(manifest), [b])


class ClassifyTest(unittest.TestCase):
    def test_is_own_failure(self):
        for reason in (NET, "HTTP 500", "HTTP 403", "HTTP 404", "校验值不符", "不是 PNG 图片", f"{assets.CROP_FAILED}：测试",
                       "清单里没有下载地址", "OSError: 读不了证书"):
            with self.subTest(reason=reason):
                self.assertTrue(assets.is_own_failure(reason))
        for reason in (assets.PAUSED, f"{assets.PAUSED}（HTTP 567）", assets.CANCELLED, WRITE, assets.NO_CROPPER,
                       assets.GONE, NO_SESSION, "HTTP 429", "HTTP 503", None, 3):
            with self.subTest(reason=reason):
                self.assertFalse(assets.is_own_failure(reason))

    def test_is_crop_failure(self):
        """is_crop_failure 只认卡面裁不出角色照（CROP_FAILED 开头）：悬浮窗按它认出失败的全是这一种的那一轮。"""
        self.assertTrue(assets.is_crop_failure(f"{assets.CROP_FAILED}：卡面解不开（测试）"))
        for reason in (NET, WRITE, assets.NO_CROPPER, assets.NO_CARD_SHA1, assets.GONE, f"x{assets.CROP_FAILED}", None, 3):
            with self.subTest(reason=reason):
                self.assertFalse(assets.is_crop_failure(reason))

    def test_isolated_and_deferred(self):
        """个别图片下不了：原因都知道、没被拦下也没取消、失败的总张数不超过 FAILED_MANY 张（按缺了几张算：写不进、已删除的
        也算进去）；隔天再试的只是其中这一张自己的问题的那几张。只缺一张、那一张也没下好的照样算个别（不按「一张都没下好」
        分）。300 张写不进混着 1 张网络出错、40 张已删除都不算个别；20 张以内混着写不进的照旧算。"""
        many = [(f"heroes/{i}.png", NET) for i in range(assets.FAILED_MANY + 1)]
        writes = [(f"heroes/w{i}.png", WRITE) for i in range(300)]
        gone = [(f"heroes/g{i}.png", assets.GONE) for i in range(40)]
        cases = [
            ([("heroes/a.png", NET)], True, ["heroes/a.png"]),
            ([("heroes/a.png", WRITE), ("heroes/b.png", NET)], True, ["heroes/b.png"]),
            ([("heroes/g.png", assets.GONE)], True, []),
            (many[:-1], True, [n for n, _ in many[:-1]]),
            (many, False, []),
            ([*writes, ("chips/c.png", NET)], False, []),  # 磁盘满了这类：不能说「其余识别照常」
            (gone, False, []),
            ([*writes[:assets.FAILED_MANY - 1], ("chips/c.png", NET)], True, ["chips/c.png"]),  # 正好 FAILED_MANY 张
            ([*writes[:assets.FAILED_MANY], ("chips/c.png", NET)], False, []),  # 多一张写不进的就不算个别
            ([("heroes/a.png", NET), ("heroes/b.png", assets.CANCELLED)], False, []),
            ([("heroes/a.png", NET), ("heroes/b.png", assets.PAUSED)], False, []),
            ([("heroes/a.png", NO_SESSION)], False, []),  # 建不了下载会话：照全部或大部分没下下来算
            ([("heroes/a.png", NET), ("heroes/b.png", NO_SESSION)], False, []),
            ([], False, []),
            (["heroes/a.png"], False, []),  # 只给了文件名（下载整个出错）：原因不明
            ([("heroes/a.png", None)], False, []),
            (3, False, []),
        ]
        for failed, isolated, deferred in cases:
            with self.subTest(failed=str(failed)[:60]):
                self.assertIs(assets.isolated(failed), isolated)
                self.assertEqual(assets.deferred(failed), deferred)


# ------------------------------------------------------------ 悬浮窗


class OverlayCase(unittest.TestCase):
    """离屏悬浮窗，下载模块换成假的：download 按 self.rounds 依次交回结果（函数时调用它），分「个别」「大部分」用下载模块
    自己的 isolated、deferred。下载期间记下识别暂停没有、热键能不能用（self.during）。"""

    def setUp(self):
        self.db = load_db()
        self.errors = capture_errors(self)
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.missing = ["heroes/a.png", "heroes/b.png", "chips/c.png"]
        self.retry = ["heroes/a.png"]
        self.rounds: list = []
        self.calls: list = []
        self.during: list = []
        self.fake = SimpleNamespace(
            missing=lambda: list(self.missing), retry_items=lambda: list(self.retry), download=self.fake_download,
            is_write_error=assets.is_write_error, is_paused=assets.is_paused, isolated=assets.isolated,
            deferred=assets.deferred, is_crop_failure=assets.is_crop_failure, GONE=assets.GONE, cooldown=lambda: None)
        patcher = mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""})  # 外面设了「试启动不下载」时也照常下载
        patcher.start()
        self.addCleanup(patcher.stop)
        self.w = self.overlay()

    def overlay(self) -> Overlay:
        w = Overlay(self.db, self.session, threading.Lock(), services=Services(assets=self.fake))
        w.run_background = lambda fn: fn()
        w.watcher = FakeWatcher()
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def fake_download(self, items, progress, stop, stats=None):
        w = self.w
        requested = []

        def asked():
            requested.append(1)

        w.capture_requested.connect(asked)
        w.on_hotkey()  # 下载期间按热键：能用时请识别一次，暂停时提示「识别功能暂不可用」
        w.capture_requested.disconnect(asked)
        self.during.append(SimpleNamespace(
            blocked=w.background.blocked, paused=w.watcher.paused.get("assets"), state=w.background.assets_state,
            capsule=BLOCKED_BIT in w.status.summary_plain, hotkey=bool(requested), message=w.message.text,
            tip=self.tip()))
        self.calls.append(list(items))
        outcome = self.rounds.pop(0)
        return outcome() if callable(outcome) else outcome

    def text(self) -> str:
        return self.w.notice_model.texts().get("assets", "")

    def tip(self) -> str:
        model = self.w.notice_model
        return model.tips(logs="", assets_folder="assets", capture=None, assets=self.w.background.assets_state)["assets"]

    def buttons(self) -> list:
        return [(b.text(), tip_text(b.toolTip())) for b in self.w.notices.buttons("assets")]


class PauseTest(OverlayCase):
    def test_few_images_keep_recognition(self):
        """只补少量图片（不多于 DOWNLOAD_PAUSE_OVER 张）：下载期间后台识别不暂停，热键照常请识别（不提示「识别功能暂不
        可用」），状态胶囊不写「识别 暂不可用」；下载那一行的悬停说明写识别照常；下完照常重新加载图片。"""
        self.missing = [f"heroes/{i}.png" for i in range(background_mod.DOWNLOAD_PAUSE_OVER)]
        self.rounds = [(list(self.missing), [])]
        self.assertTrue(self.w.check_assets())
        seen = self.during[0]
        self.assertEqual((seen.blocked, seen.paused, seen.capsule, seen.hotkey), (False, False, False, True))
        self.assertNotEqual(seen.message, BLOCKED_HOTKEY)
        self.assertEqual(seen.state[4], "few")
        self.assertTrue(seen.tip.endswith(ASSETS_TIP_QUIET), seen.tip)
        self.assertEqual(self.w.watcher.reloaded, 1)
        self.assertNotIn("assets", self.w.notice_model.texts())

    def test_many_images_pause(self):
        """要下的多（第一次运行、整个文件夹没了，多于 DOWNLOAD_PAUSE_OVER 张）：照旧暂停识别，热键提示「识别功能暂不可用」，
        状态胶囊写「识别 暂不可用」；下完恢复。"""
        self.missing = [f"heroes/{i}.png" for i in range(background_mod.DOWNLOAD_PAUSE_OVER + 1)]
        self.rounds = [(list(self.missing), [])]
        self.w.check_assets()
        seen = self.during[0]
        self.assertEqual((seen.blocked, seen.paused, seen.capsule, seen.hotkey), (True, True, True, False))
        self.assertEqual(seen.message, BLOCKED_HOTKEY)
        self.assertEqual(seen.state[4], "paused")
        self.assertTrue(seen.tip.endswith(ASSETS_TIP_PAUSED), seen.tip)
        self.assertEqual((self.w.background.blocked, self.w.watcher.paused), (False, {"assets": False}))


class FailedNoticeTest(OverlayCase):
    def test_one_image(self):
        """一张下不了（网络出错）：下载那一行逐字核对（1 张的写法），⚠ 橙色、边框不变色，带「重试」和「关闭本条提示」；
        悬停说明写今天不再自动下载、明天再试；错误记录写明这张的原因、明天再自动下载。"""
        self.rounds = [(["heroes/b.png", "chips/c.png"], [("heroes/a.png", NET)])]
        self.w.check_assets()
        self.assertEqual(self.text(), ONE_TEXT)
        self.assertEqual(self.w.background.assets_state, ("failed", 1, 2, "few"))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片"), (DISMISS, "关闭本条提示")])
        self.assertEqual(self.w.notice_model.notices["assets"][2], "warn")
        self.assertFalse(self.w.needs_attention())
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FEW), self.tip())
        entries = self.errors.entries()
        self.assertEqual([t for t, _ in entries], ["识别所需的图片有 1 张下载失败（拾星助手仍在运行）"])
        self.assertIn(f"heroes/a.png：{NET}", entries[0][1])
        self.assertIn("这张明天再自动下载（今天再启动时不自动下载，点击「重试」可以马上下载）。", entries[0][1])

    def test_several_images(self):
        """几张下不了：写「有 N 张」「用到这些图片的」，悬停说明写明天再试。混着写不进的：照样是个别图片下不了，「明天」改成
        「之后」（写不进的下次启动时就重下），按钮照旧两个，悬停说明按原因写各自什么时候再试；错误记录写明其中几张明天再自动
        下载。"""
        self.rounds = [([], [("heroes/a.png", NET), ("heroes/b.png", "HTTP 502"), ("chips/c.png", NET)]),
                       ([], [("heroes/a.png", NET), ("heroes/b.png", WRITE), ("chips/c.png", WRITE)])]
        self.w.check_assets()
        self.assertEqual(self.text(), THREE_TEXT)
        self.assertEqual(self.w.background.assets_state, ("failed", 3, 0, "few"))
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FEW), self.tip())
        self.assertIn("这几张明天再自动下载", self.errors.entries()[0][1])
        self.w.notices.buttons("assets")[0].click()  # 重试：这一轮混着写不进的
        self.assertEqual(self.text(), THREE_LATER)
        self.assertEqual(self.w.background.assets_state, ("failed", 3, 0, "few_later"))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片"), (DISMISS, "关闭本条提示")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_LATER), self.tip())
        detail = self.errors.entries()[1][1]
        self.assertIn("写盘出错 2 张，网络等其他原因 1 张", detail)
        self.assertIn("其中 1 张明天再自动下载", detail)

    def test_gone_only_says_later(self):
        """只有 wiki 上已删除的几张失败（7 天后才再试，不记明天）：照样是个别图片下不了的那一句，写「之后会自动再试」，带
        「重试」和「关闭本条提示」；错误记录里不写「明天再自动下载」。"""
        self.rounds = [([], [("heroes/a.png", assets.GONE)])]
        self.w.check_assets()
        self.assertEqual(self.text(), ONE_LATER)
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片"), (DISMISS, "关闭本条提示")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_LATER), self.tip())
        self.assertNotIn("明天再自动下载", self.errors.entries()[0][1])

    def test_no_session_says_check_network(self):
        """下载线程建不了下载会话（NO_SESSION，读不了系统的证书、代理设置不对等）：不是哪一张图的问题，照全部或大部分没下
        下来提示检查网络，只有「重试」、不能关；悬停说明写下次启动时重新下载，错误记录里不写「明天再自动下载」。"""
        self.rounds = [([], [("heroes/a.png", NO_SESSION), ("chips/c.png", NO_SESSION)])]
        self.w.check_assets()
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=2))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FAILED), self.tip())
        detail = self.errors.entries()[0][1]
        self.assertIn(f"heroes/a.png：{NO_SESSION}", detail)
        self.assertNotIn("明天再自动下载", detail)

    def test_most_images_failed(self):
        """全部或大部分没下下来（失败超过 FAILED_MANY 张，像整个网络不通）：照旧说检查网络，只有「重试」、不能关；悬停说明
        写下次启动时重新下载；错误记录里不写「明天再自动下载」。全是写不进的照旧换成写不进那一句。"""
        n = assets.FAILED_MANY + 1
        self.rounds = [([], [(f"heroes/{i}.png", NET) for i in range(n)]),
                       ([], [("heroes/a.png", WRITE)])]
        self.w.check_assets()
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=n))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FAILED), self.tip())
        self.assertNotIn("明天再自动下载", self.errors.entries()[0][1])
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(self.text(), "⚠ " + FOLDER_UNWRITABLE)
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FAILED), self.tip())

    def test_count_all_failures(self):
        """「只缺几张」按缺了几张算，不分哪一类：300 张写不进（磁盘满了这类）混着 1 张网络出错、40 张 wiki 上已删除，都照全部
        或大部分没下下来说检查网络，只有「重试」、不能关，不写「其余识别照常」，错误记录里不写「明天再自动下载」；20 张以内
        混着写不进的（19 张写不进 + 1 张网络出错）照旧是个别图片下不了、说「之后会自动再试」，能关；多一张写不进的就说检查
        网络。"""
        writes = [(f"heroes/w{i}.png", WRITE) for i in range(300)]
        edge = [*writes[:assets.FAILED_MANY - 1], ("chips/c.png", NET)]
        self.rounds = [([], [*writes, ("chips/c.png", NET)]),
                       ([], [(f"heroes/g{i}.png", assets.GONE) for i in range(40)]),
                       ([], edge),
                       ([], [("heroes/w99.png", WRITE), *edge])]
        self.w.check_assets()
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=301))
        self.assertNotIn("其余识别照常", self.text())
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FAILED), self.tip())
        self.assertNotIn("明天再自动下载", self.errors.entries()[0][1])
        self.w.notices.buttons("assets")[0].click()  # 重试：这一轮 40 张已删除
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=40))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        # 悬停说明不只说「下次启动时自动重新下载」（已删除的 7 天后才再试），换成另写已删除的 7 天后再试的那一句
        self.assertEqual(self.w.background.assets_state, ("failed", 40, 0, "gone"))
        self.assertTrue(self.tip().endswith(ASSETS_TIP_GONE), self.tip())
        self.w.notices.buttons("assets")[0].click()  # 重试：19 张写不进 + 1 张网络出错，正好 FAILED_MANY 张
        self.assertEqual(self.text(), "⚠ " + notices.few_failed_text(assets.FAILED_MANY, later=True))
        self.assertEqual(self.w.background.assets_state[3], "few_later")
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片"), (DISMISS, "关闭本条提示")])
        self.w.notices.buttons("assets")[0].click()  # 重试：再多一张写不进的
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=assets.FAILED_MANY + 1))
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])

    def test_many_with_gone_tip(self):
        """全部或大部分没下下来、里面有 wiki 上已删除的：不论混着写不进的还是网络出错的（失败多时都不记日期、下次启动就
        重下），悬停说明都换成「下次启动时自动重新下载，wiki 上已删除的 7 天后再试」那一句；原因不明的、下载模块交不出 GONE
        的，照旧写下次启动时重新下载。下载那一行都照旧说检查网络、只有「重试」。"""
        gone = [(f"heroes/g{i}.png", assets.GONE) for i in range(assets.FAILED_MANY)]
        cases = (
            ("已删除和写不进", [*gone, ("chips/w.png", WRITE)], "gone", ASSETS_TIP_GONE),
            ("已删除和网络出错", [*gone, ("chips/n.png", NET)], "gone", ASSETS_TIP_GONE),
            ("原因不明", [*gone, "chips/x.png"], "", ASSETS_TIP_FAILED),
        )
        for label, failed, kind, tip in cases:
            with self.subTest(label):
                self.rounds = [([], failed)]
                self.w.background.assets_state = None
                self.w.check_assets()
                self.assertEqual(self.w.background.assets_state[3], kind)
                self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=len(failed)))
                self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
                self.assertTrue(self.tip().endswith(tip), self.tip())
        del self.fake.GONE
        self.rounds = [([], gone + [("chips/w.png", WRITE)])]
        self.w.check_assets()
        self.assertEqual(self.w.background.assets_state[3], "")

    def test_many_crop_failures(self):
        """全部或大部分没下下来、失败的全是卡面裁不出角色照（不是网络的问题，多半是程序或数据坏了）：下载那一行换成下载后
        处理失败的那一句，写明错误记录的位置（按用户看到的程序文件夹写）、多次出现请点「检查更新」更新到最新版，
        不说检查网络；只有
        「重试」、不能关，悬停说明同全部或大部分没下下来；前几张的原因照常写进错误记录。混着一张网络出错的、下载模块交不出
        is_crop_failure 的照旧说检查网络；不超过 FAILED_MANY 张的照旧是个别图片下不了（明天再试）。"""
        crop = f"{assets.CROP_FAILED}：卡面解不开（测试）"
        many = [(f"heroes/c{i}.png", crop) for i in range(assets.FAILED_MANY + 1)]
        expected = "⚠ " + DOWNLOAD_CROP_FAILED.format(n=len(many), err_file=paths.shown(ERR_RECORD))
        self.rounds = [([], many)]
        self.w.check_assets()
        self.assertEqual(self.w.background.assets_state, ("failed", len(many), 0, "crop"))
        self.assertEqual(self.text(), expected)
        self.assertIn(f"详细信息已写入错误记录（{paths.shown(ERR_RECORD)}）", self.text())
        self.assertNotIn("检查网络", self.text())
        self.assertEqual(self.buttons(), [("重试", "重新下载失败的图片")])
        self.assertTrue(self.tip().endswith(ASSETS_TIP_FAILED), self.tip())
        self.assertIn(f"heroes/c0.png：{crop}", self.errors.entries()[0][1])
        mixed = [*many[:-1], ("chips/n.png", NET)]
        self.rounds = [([], mixed)]
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual((self.w.background.assets_state[3], self.text()),
                         ("", "⚠ " + DOWNLOAD_FAILED.format(n=len(mixed))))
        self.rounds = [([], many[:assets.FAILED_MANY])]
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(self.w.background.assets_state[3], "few")
        del self.fake.is_crop_failure
        self.rounds = [([], many)]
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual((self.w.background.assets_state[3], self.text()),
                         ("", "⚠ " + DOWNLOAD_FAILED.format(n=len(many))))

    def test_unknown_module_keeps_network_text(self):
        """下载模块没有 isolated（旧的、换成假的）、原因不明（下载整个出错，原样交回图片清单）：照旧说检查网络。"""
        self.rounds = [([], ["heroes/a.png"])]
        self.w.check_assets()
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=1))
        del self.fake.isolated, self.fake.deferred
        self.rounds = [([], [("heroes/a.png", NET)])]
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(self.text(), "⚠ " + DOWNLOAD_FAILED.format(n=1))

    def test_dismiss_then_show_again(self):
        """点「关闭本条提示」：收起，刷新以后也不再显示（这次运行）；点「重试」照样下载（近期不自动重试的也下，retry_items）；
        再下载、再失败时又显示。"""
        self.rounds = [([], [("heroes/a.png", NET)])]
        self.w.check_assets()
        self.rounds = [([], [("heroes/a.png", NET)])]
        self.w.notices.buttons("assets")[1].click()
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.w.refresh()
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.assertEqual(len(self.calls), 1)  # 关掉不会下载
        self.w.check_assets()
        self.assertEqual(self.text(), ONE_TEXT)
        self.rounds = [(["heroes/a.png"], [])]
        self.w.notices.buttons("assets")[0].click()  # 重试
        self.assertEqual(self.calls[-1], self.retry)
        self.assertNotIn("assets", self.w.notice_model.texts())

    def test_retry_forgets_marks_first(self):
        """下载失败那一行点「重试」：开始下载之前先叫下载模块去掉这一轮要下的几张近期不再自动下载的记号（forget_recent，给
        dir/file）；启动时不去。下载模块交不出 forget_recent、调用出错时照旧下载（出错的写进错误记录）。被拦下以后到点接着下
        这几张的整个过程见 tests/test_download_block.py 的 RetryBlockedTest。"""
        events = []
        download = self.fake.download
        self.fake.forget_recent = lambda names: events.append(("forget", list(names)))

        def logged(items, progress, stop, stats=None):
            events.append(("download", list(items)))
            return download(items, progress, stop, stats=stats)

        self.fake.download = logged
        self.rounds = [([], [("heroes/a.png", NET)])] * 3 + [(["heroes/a.png"], [])]
        self.w.check_assets()
        self.assertEqual(events, [("download", self.missing)])
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(events[1:], [("forget", self.retry), ("download", self.retry)])
        del self.fake.forget_recent
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(events[3:], [("download", self.retry)])
        self.fake.forget_recent = mock.Mock(side_effect=OSError("测试"))
        self.w.notices.buttons("assets")[0].click()
        self.assertEqual(events[4:], [("download", self.retry)])
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.assertIn("调用 advisor.assets.forget_recent 出错（拾星助手仍在运行）", [t for t, _ in self.errors.entries()])

    def test_skipped_today_no_download_no_notice(self):
        """当天记下失败的那几张（下载模块的 missing 不算它们）：启动时不下载、不提示；第二天照常下载。用真的 missing。"""
        base = sandbox.temp_dir(self, "test-dlfail-")
        patcher = mock.patch.object(paths, "assets_dir", side_effect=lambda kind="": base / kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        item = assets.AssetItem("heroes", "a.png", "a.png", "https://img/a.png", 10, "0" * 40)
        manifest = assets.Manifest([item])
        self.fake.missing = lambda: assets.missing(manifest)
        assets._note_failed([item.name])
        self.assertFalse(self.w.check_assets())
        self.assertEqual(self.calls, [])
        self.assertNotIn("assets", self.w.notice_model.texts())
        self.rounds = [([item.name], [])]
        with next_day():
            self.assertTrue(self.w.check_assets())
        self.assertEqual(self.calls, [[item]])


class NoticeModelTest(unittest.TestCase):
    """提示行的排法和悬停说明（不经悬浮窗）。"""

    def rows(self, assets_state):
        view = SimpleNamespace(game_over=False, unsupported=[], unrecorded=0, map_switched=False)
        model = notices.NoticeModel()
        rows = model.rows(view, live=False, reverted_chip="", capture=None, unlocked=[], schemes="", assets=assets_state,
                          calibrate_games=20, folder="AstralChipCompass")
        return {key: (text, [b[2] for b in btns]) for key, text, _, btns in rows}["assets"]

    def tip(self, assets_state) -> str:
        return notices.NoticeModel().tips(logs="", assets_folder="assets", capture=None, assets=assets_state)["assets"]

    def test_rows(self):
        self.assertEqual(self.rows(("failed", 1, 0, "few")), (ONE_TEXT, ["retry_download", "dismiss_download"]))
        self.assertEqual(self.rows(("failed", 3, 0, "few")), (THREE_TEXT, ["retry_download", "dismiss_download"]))
        self.assertEqual(self.rows(("failed", 1, 0, "few_later")), (ONE_LATER, ["retry_download", "dismiss_download"]))
        self.assertEqual(self.rows(("failed", 3, 0, "few_later")),
                         (THREE_LATER, ["retry_download", "dismiss_download"]))
        # 没写种类的（旧写法、预览工具）照旧说检查网络；大部分没下下来、里面有已删除的（gone）也是，只是悬停说明不同
        for state in (("failed", 3, 0, ""), ("failed", 3, 0), ("failed", 3, 0, "gone")):
            with self.subTest(state=state):
                self.assertEqual(self.rows(state), ("⚠ " + DOWNLOAD_FAILED.format(n=3), ["retry_download"]))
        # 失败的全是卡面裁不出（crop）：下载后处理失败的那一句，错误记录的位置按用户看到的程序文件夹写，只有「重试」
        crop = "⚠ " + DOWNLOAD_CROP_FAILED.format(n=30, err_file=paths.shown(ERR_RECORD))
        self.assertEqual(self.rows(("failed", 30, 0, "crop")), (crop, ["retry_download"]))

    def test_tips(self):
        cases = {
            ("downloading", 0, 3, "", "few"): ASSETS_TIP_QUIET,
            ("downloading", 0, 3, "", "slow"): ASSETS_TIP_SLOW,
            ("downloading", 0, 300, "", "paused"): ASSETS_TIP_PAUSED,
            ("downloading", 0, 300, ""): ASSETS_TIP_PAUSED,  # 没写的按暂停
            ("failed", 1, 0, "few"): ASSETS_TIP_FEW,
            ("failed", 3, 0, "few_later"): ASSETS_TIP_LATER,
            ("failed", 40, 0, "gone"): ASSETS_TIP_GONE,
            ("failed", 30, 0, ""): ASSETS_TIP_FAILED,
            ("failed", 30, 0, "crop"): ASSETS_TIP_FAILED,
            ("failed", 3, 0, "write"): ASSETS_TIP_FAILED,
            ("failed", 3, 0): ASSETS_TIP_FAILED,
        }
        for state, after in cases.items():
            with self.subTest(state=state):
                self.assertTrue(self.tip(state).endswith(after), self.tip(state))
        for text in (ASSETS_TIP_QUIET, ASSETS_TIP_FEW, ASSETS_TIP_LATER, ASSETS_TIP_GONE, ASSETS_TIP_FAILED):
            self.assertNotIn("检查网络", text)

    def test_gone_days_written_same(self):
        """悬停说明（混着别的失败的、大部分没下下来里面有已删除的两句）和使用说明里写死的「7 天」（wiki 上已删除的图片隔几天
        再自动下载）和下载模块的 GONE_RETRY_DAYS 一致：改了天数，这几处要跟着改。各处都要写到天数，写到的每一处都是这个数。"""
        days = re.compile(r"(\d+) 天(?:内|后)")
        manual = (Path(__file__).resolve().parents[1] / "docs" / "使用说明.md").read_text(encoding="utf-8")
        gone_lines = [line for line in manual.splitlines() if "已删除" in line]
        for label, text in (("悬停说明", ASSETS_TIP_LATER), ("悬停说明（大部分没下下来）", ASSETS_TIP_GONE),
                            ("使用说明", "\n".join(gone_lines))):
            with self.subTest(label):
                found = days.findall(text)
                self.assertTrue(found, text[:80])
                self.assertEqual(set(found), {str(assets.GONE_RETRY_DAYS)}, text[:80])


if __name__ == "__main__":
    unittest.main()
