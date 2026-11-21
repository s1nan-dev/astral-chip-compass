"""识别图片的最新清单接到悬浮窗上：什么时候取、取到以后怎样接着下缺的图片（advisor/ui/overlay_parts/background.py 的
fetch_manifest、manifest_fetched，updates.py 的 fetch_assets，advisor/ui/services.py 登记的角色编号）。

- 什么时候取：自动检查更新、设置窗口的「检查更新」都取，开发版不检查、也不取；取清单另起一个后台工作，检查的结果照常先交回；
  取的时候出错不影响检查。下载失败那一行点「重试」：先取，取完再算要重试哪些；等的时候再点不算，等久了照现在的清单重试，
  开发版不取、马上重试。环境变量「不下载」时不取。
- 取到以后：存了更新的一份（REMOTE_SAVED）时查缺图、下载；别的结果什么都不做。正在下载时不另起一轮，这一轮下完再查一次；
  冷却中不绕过冷却（只改那一行的张数，到点照常接着下）；失败那一行在时只下上一轮没有的。
- 退出时叫停取清单的线程，之后交回的结果不再管。
- 登记认识的角色编号：导入下载模块时登记交回资料里角色编号的函数，编号是整数、和资料一样；读不出时记错误记录、按没登记算。

下载模块换成假的（取清单、缺哪些、下载都按测试给的交回），不联网；悬浮窗离屏，后台跑的直接调用或先排着、由测试来跑。
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from advisor import assets
from advisor.data import load_db
from advisor.ui import services as services_mod
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts import background as background_mod
from advisor.ui.overlay_parts import updates
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.updates import UpdateControl
from advisor.ui.overlay_parts.wording import NO_DOWNLOAD_ENV
from advisor.ui.services import Services
from tests.support.errors import capture_errors
from tests.support.overlay_cases import FakeWatcher  # 导入时建好 QApplication

SAVED = assets.REMOTE_SAVED
OTHERS = (assets.REMOTE_UNCHANGED, assets.REMOTE_SKIPPED, assets.REMOTE_FAILED, assets.REMOTE_CANCELLED,
          assets.REMOTE_REJECTED, assets.REMOTE_FUTURE, None)
OLD = ["heroes/a.png", "chips/c.png"]
NEW = "heroes/UT_Hero_RolePhoto_131.png"  # 最新清单新带来的一张
T0 = 1_800_000_000.0


class FakeNet:
    """advisor/update.py 的替身：check 交回「已经是最新」，记下调用。"""

    USE_MIRROR = True
    MIRROR_BASE = "https://dl.example/"
    LATEST_ZIP = MIRROR_BASE + "ACC-latest.zip"

    def __init__(self, log: list):
        self.log = log

    def check(self, current, skipped=None, *, session=None, manual=False):
        self.log.append("check")
        return SimpleNamespace(release=None, latest=current, reachable=True, mirror_ok=True, problem=None)


class WireCase(unittest.TestCase):
    """离屏悬浮窗，下载模块换成假的（self.missing 是现在缺的；取清单交回 self.result，取了以后缺的换成 self.after；
    下载按 self.rounds 依次交回，没给时全部下好），模拟发行版（frozen），后台跑的直接调用（self.defer 为真时先排进
    self.queue），计时器记下来由测试触发，钟是假的。self.log 记下取清单、查缺图、下载的先后。"""

    def setUp(self):
        self.errors = capture_errors(self)
        self.db = load_db()
        self.log: list = []
        self.missing = list(OLD)
        self.after: list | None = None
        self.result = SAVED
        self.rounds: list = []
        self.stops: list = []
        self.queue: list = []
        self.defer = False
        self.timers: list = []
        self.pause = None
        self.fake = SimpleNamespace(
            missing=self.fake_missing, retry_items=self.fake_retry, download=self.fake_download,
            fetch_remote_manifest=self.fake_fetch, cooldown=lambda: self.pause, REMOTE_SAVED=SAVED,
            is_write_error=assets.is_write_error, is_paused=assets.is_paused, PAUSED=assets.PAUSED,
            COOLDOWN_MINUTES=assets.COOLDOWN_MINUTES, GONE=assets.GONE)
        patcher = mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.w = self.overlay()

    def overlay(self, frozen: bool = True) -> Overlay:
        w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock(),
                    services=Services(assets=self.fake))
        w.run_background = self.run_background
        w.watcher = FakeWatcher()
        b = w.background
        b.later = lambda ms, fn: self.timers.append((ms, fn))
        b.now = lambda: T0
        b.frozen = lambda: frozen
        u = w.updates
        u.net = FakeNet(self.log)
        u.frozen = lambda: frozen
        u.later = lambda ms, fn: self.timers.append((ms, fn))
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.hide)
        return w

    def run_background(self, fn):
        if self.defer:
            self.queue.append(fn)
        else:
            fn()

    def drain(self) -> None:
        self.defer = False
        while self.queue:
            self.queue.pop(0)()

    def fake_fetch(self, stop):
        self.log.append("fetch")
        self.stops.append(stop)
        if stop is not None and stop():
            return assets.REMOTE_CANCELLED
        if self.result == SAVED and self.after is not None:
            self.missing = list(self.after)
        return self.result

    def fake_missing(self):
        self.log.append("missing")
        return list(self.missing)

    def fake_retry(self):
        self.log.append("retry_items")
        return list(self.missing)

    def fake_download(self, items, progress, stop, stats=None):
        self.log.append(("download", list(items)))
        if self.rounds:
            outcome = self.rounds.pop(0)
            return outcome() if callable(outcome) else outcome
        self.missing = [name for name in self.missing if name not in items]
        return list(items), []

    def downloads(self) -> list:
        return [entry[1] for entry in self.log if isinstance(entry, tuple)]

    def fail_round(self, names=None):
        """一轮全部没下下来（网络出错，不记日期：下次照旧算缺的）。"""
        def run():
            todo = list(names if names is not None else self.missing)
            return [], [(name, "网络出错（测试）") for name in todo]
        return run

    def tearDown(self):
        self.assertEqual(self.errors.entries(), [])


class WhenTest(WireCase):
    def test_auto_and_manual_check_fetch(self):
        """自动检查（启动后到点那一次）和设置窗口的「检查更新」都取一次最新清单，都在检查之后；取到没变的什么都不下。"""
        w = self.w
        self.result = assets.REMOTE_UNCHANGED
        w.updates.schedule_auto()
        self.assertEqual([ms for ms, _ in self.timers], [updates.AUTO_CHECK_DELAY_MS])
        self.timers.pop()[1]()
        self.assertEqual(self.log, ["check", "fetch"])
        w.updates.state = "idle"
        w.check_updates()
        self.assertEqual(self.log, ["check", "fetch", "check", "fetch"])
        self.assertEqual(self.downloads(), [])

    def test_dev_does_not_fetch(self):
        """开发版：不检查更新，也不取（「检查更新」只说一句；直接叫 fetch_manifest 也不取）。"""
        w = self.overlay(frozen=False)
        w.updates.schedule_auto()
        w.check_updates()
        self.assertFalse(w.background.fetch_manifest())
        self.assertEqual((self.timers, self.log), ([], []))

    def test_check_result_first(self):
        """取清单另起一个后台工作：检查的结果先交回界面（取清单的工作还排着时检查已经有结果），取完再接着下缺的。"""
        w = self.w
        self.defer = True
        self.after = OLD + [NEW]
        self.missing = []
        w.check_updates()
        self.assertEqual(len(self.queue), 2)
        self.queue.pop(0)()  # 只跑检查
        self.assertEqual(w.updates.state, "latest")
        self.assertEqual(self.log, ["check"])
        self.assertTrue(w.background.fetching)
        self.drain()
        self.assertEqual(self.log[:2], ["check", "fetch"])
        self.assertEqual(self.downloads(), [OLD + [NEW]])
        self.assertFalse(w.background.fetching)

    def test_hook_error_does_not_break_check(self):
        """检查更新时叫取清单出错：只记错误记录，检查照常。"""
        w = self.w
        w.updates.fetch_assets = mock.Mock(side_effect=RuntimeError("测试造的错"))
        w.check_updates()
        self.assertEqual(w.updates.state, "latest")
        self.assertEqual([t for t, _ in self.errors.entries()], ["取识别图片的最新清单出错（拾星助手仍在运行）"])
        self.errors.clear()

    def test_not_twice_and_env(self):
        """上一次还没取完时不另起一次；环境变量「不下载」时不取；没接上交回的信号、下载模块交不出时不取。"""
        b = self.w.background
        self.defer = True
        self.assertTrue(b.fetch_manifest())
        self.assertTrue(b.fetch_manifest())
        self.assertEqual(len(self.queue), 1)
        self.result = assets.REMOTE_UNCHANGED
        self.drain()
        self.assertEqual(self.log, ["fetch"])
        with mock.patch.dict(os.environ, {NO_DOWNLOAD_ENV: "1"}):
            self.assertFalse(b.fetch_manifest())
        emit, b.emit_fetched = b.emit_fetched, None
        self.assertFalse(b.fetch_manifest())
        b.emit_fetched = emit
        del self.fake.fetch_remote_manifest
        self.assertFalse(b.fetch_manifest())
        self.assertEqual(self.log, ["fetch"])


class AfterTest(WireCase):
    def test_saved_downloads_other_results_do_not(self):
        """取到更新的一份（REMOTE_SAVED）：照合好的清单查缺图、下载；别的结果（没变、复刻版不取、没取到、叫停、核对不过、
        以后的格式、调用出错）不查也不下。"""
        for result in OTHERS:
            with self.subTest(result=result):
                self.log.clear()
                self.result = result
                self.w.background.fetch_manifest()
                self.assertEqual(self.log, ["fetch"])
        self.log.clear()
        self.result = SAVED
        self.after = [NEW]
        self.w.background.fetch_manifest()
        self.assertEqual(self.log, ["fetch", "missing", ("download", [NEW])])
        self.assertIsNone(self.w.background.assets_state)

    def test_saved_nothing_missing(self):
        """取到更新的一份，可图片都在：不下载、不提示。"""
        self.missing = []
        self.w.background.fetch_manifest()
        self.assertEqual(self.log, ["fetch", "missing"])
        self.assertIsNone(self.w.background.assets_state)

    def test_while_downloading(self):
        """正在下载时取到更新的一份：不另起一轮，这一轮下完再查一次缺的，接着下新带来的那几张。"""
        b = self.w.background
        self.defer = True
        self.assertTrue(b.check_assets())
        download = self.queue.pop()
        self.after = OLD + [NEW]
        b.fetch_manifest()
        self.queue.pop()()  # 先取完：正在下载
        self.assertEqual(self.downloads(), [])
        self.assertEqual(self.log, ["missing", "fetch"])
        self.assertEqual(b.assets_state[0], "downloading")
        download()  # 这一轮（OLD）下完：再查一次，接着下 NEW
        self.drain()
        self.assertEqual(self.downloads(), [OLD, [NEW]])
        self.assertIsNone(b.assets_state)
        self.assertFalse(b._recheck)

    def test_cooldown_not_bypassed(self):
        """冷却中（上次被站点防护拦下）取到更新的一份：不下载，下载那一行的「其余 N 张」改成现在缺的；到点照常接着下。"""
        b = self.w.background
        self.pause = assets.Cooldown(T0 + 600, 35, T0 - 60, "HTTP 567（测试）")
        self.assertFalse(b.check_assets())
        self.assertEqual(b.assets_state[:2], ("blocked", 2))
        timers = len(self.timers)
        self.after = OLD + [NEW]
        b.fetch_manifest()
        self.assertEqual(self.downloads(), [])
        self.assertEqual(b.assets_state[:2], ("blocked", 3))
        self.assertEqual(len(self.timers), timers)  # 没有另排、没有提前
        b.now = lambda: T0 + 601
        self.pause = None
        self.timers[-1][1]()
        self.assertEqual(self.downloads(), [OLD + [NEW]])

    def test_failed_only_new(self):
        """失败那一行在时取到更新的一份：上一轮下过的不自动再下，只下最新清单新带来的；没有新的就不动那一行。"""
        b = self.w.background
        self.rounds = [self.fail_round()]
        b.check_assets()
        self.assertEqual(b.assets_state[0], "failed")
        self.errors.clear()  # 失败的原因记进了错误记录（照常）
        failed = b.assets_state
        self.missing = list(OLD)  # 没有新的
        b.fetch_manifest()
        self.assertEqual((self.downloads(), b.assets_state), ([OLD], failed))
        self.after = OLD + [NEW]
        b.fetch_manifest()
        self.assertEqual(self.downloads(), [OLD, [NEW]])
        self.assertNotIn("retry_items", self.log)  # 不是点「重试」
        self.assertIsNone(b.assets_state)


class RetryTest(WireCase):
    def failed(self):
        self.rounds = [self.fail_round()]
        self.w.background.check_assets()
        self.assertEqual(self.w.background.assets_state[0], "failed")
        self.errors.clear()  # 失败的原因记进了错误记录（照常）
        self.log.clear()

    def test_fetch_then_retry(self):
        """点「重试」：先取最新清单，取完再算要重试哪些（retry_items 照合好的清单），新带来的那张也下。"""
        self.failed()
        self.after = OLD + [NEW]
        self.w.start_download()
        self.assertEqual(self.log[:2], ["fetch", "retry_items"])
        self.assertEqual(self.downloads(), [OLD + [NEW]])
        self.assertEqual(self.timers, [])

    def test_retry_when_unchanged(self):
        """取到的没变（或没取到）：照现在的清单重试。"""
        self.failed()
        self.result = assets.REMOTE_FAILED
        self.w.start_download()
        self.assertEqual(self.log[:2], ["fetch", "retry_items"])
        self.assertEqual(self.downloads(), [OLD])

    def test_waits_then_gives_up(self):
        """取得慢：等的时候再点不算（不另取、不下）；等到上限照现在的清单重试；之后晚到的「存了更新的一份」照正在下载的规矩，
        这一轮下完再查一次。"""
        b = self.w.background
        self.failed()
        self.defer = True
        self.w.start_download()
        self.w.start_download()
        self.assertEqual(len(self.queue), 1)  # 只取一次
        self.assertEqual([ms for ms, _ in self.timers], [background_mod.RETRY_FETCH_WAIT_MS])
        self.assertEqual(self.log, [])
        self.timers.pop()[1]()  # 等到上限
        self.assertEqual(self.log, ["retry_items"])
        self.assertEqual(b.assets_state[0], "downloading")
        fetch, download = self.queue
        self.queue.clear()
        self.after = OLD + [NEW]
        fetch()  # 晚到：正在下载，记下
        self.assertTrue(b._recheck)
        self.rounds = [(OLD, [])]
        self.missing = [NEW]
        download()
        self.drain()
        self.assertEqual(self.downloads(), [OLD, [NEW]])

    def test_dev_retries_at_once(self):
        """开发版点「重试」：不取，马上照现在的清单重试。"""
        self.w = self.overlay(frozen=False)
        self.failed()
        self.w.start_download()
        self.assertEqual(self.log[0], "retry_items")
        self.assertNotIn("fetch", self.log)

    def test_dismissed_while_waiting(self):
        """等最新清单的时候失败那一行关掉了：取完不下。"""
        b = self.w.background
        self.failed()
        self.defer = True
        self.w.start_download()
        b.dismiss_failed()
        self.drain()
        self.assertEqual(self.downloads(), [])


class StopTest(WireCase):
    def test_stop_flag_and_late_result(self):
        """退出时叫停取清单（停止标志置位），之后交回的结果不再管，也不再取。"""
        b = self.w.background
        self.defer = True
        b.fetch_manifest()
        b.stop_download()
        self.assertTrue(b._fetch_stop is not None and b._fetch_stop.is_set())
        self.result = SAVED
        self.after = OLD + [NEW]
        self.drain()
        self.assertEqual(self.stops[0](), True)
        self.assertEqual(self.downloads(), [])
        self.assertFalse(b.fetch_manifest())

    def test_thread_stops(self):
        """取清单在后台线程里卡着（等网络）：退出时叫停，线程很快结束。"""
        b = self.w.background
        started, finished = threading.Event(), threading.Event()

        def slow(stop):
            started.set()
            stop.wait(10)
            finished.set()
            return assets.REMOTE_CANCELLED

        self.fake.fetch_remote_manifest = slow
        b.run_background = lambda fn: threading.Thread(target=fn, daemon=True).start()
        b.fetch_manifest()
        self.assertTrue(started.wait(5))
        begin = time.monotonic()
        b.stop_download()
        self.assertTrue(finished.wait(5))
        self.assertLess(time.monotonic() - begin, 2.0)


class HeroIdsTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(assets.use_hero_ids, assets._hero_ids, load=assets._hero_ids_load)
        self.addCleanup(assets.use_cropper, assets._cropper, load=assets._cropper_load)

    def test_registered_on_import(self):
        """导入下载模块（Services.load）时登记交回角色编号的函数，不马上读；它交回资料里全部角色的编号（整数）。"""
        assets.use_hero_ids()
        loaded = Services.load(("assets",))
        self.assertIs(loaded.assets, assets)
        self.assertIs(assets._hero_ids_load, services_mod._hero_ids_loader)
        self.assertIsNone(assets._hero_ids)
        ids = services_mod._hero_ids_loader()
        self.assertEqual(ids, sorted(h.id for h in load_db().heroes.values()))
        self.assertTrue(ids and all(type(v) is int for v in ids))
        self.assertEqual(assets._known_hero_ids(), frozenset(ids))
        self.assertIsNone(assets._hero_ids_load)  # 只读一次

    def test_loader_failure(self):
        """资料读不出：记进错误记录，交回 None（按没登记算，最新清单里的图片都下）。"""
        errors = capture_errors(self)
        with mock.patch("advisor.data.load_db", side_effect=RuntimeError("测试：资料读不出")):
            self.assertIsNone(services_mod._hero_ids_loader())
        self.assertEqual([t for t, _ in errors.entries()],
                         ["读角色资料出错，识别图片的最新清单里的图片都会下载（拾星助手仍在运行）"])


if __name__ == "__main__":
    unittest.main()
