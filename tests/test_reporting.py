"""对局报告的开关和接线（advisor/ui/overlay_parts/reporting.py 的 Reporting，以及 Overlay 上接它的几个方法）：
设置里的四项（开关、本机编号、随机值、打开开关的时刻）在启动时理好，坏编号重新生成、缺随机值只补随机值；关掉时马上
不再拼、不再发，删掉编号、随机值和还没发出的报告（在后台线程里）；再打开换新的编号、随机值和时刻。一局结束只在开着时
拼这一局，胜负定了才拼，没定的先等（定了、等满、下一局开始、退出时再拼）；启动时补拼、发送整个程序只做一次，开发版只存
不发。用假的 game_report，不读推荐日志、不联网。"""

import threading
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from advisor import prefs
from advisor.ui.overlay_parts import reporting, updates
from advisor.ui.overlay_parts.app import UiSettings
from tests.support import sandbox


class Session:
    """Reporting 用的 Session：设置对象和记下存了几次。"""

    def __init__(self, settings=None):
        self.settings = settings or UiSettings()
        self.saved = 0

    def save(self, **_kwargs):
        self.saved += 1


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = sandbox.temp_dir(self, "test-reporting-")
        self.session = Session()
        self.report = reporting.Reporting(
            session=self.session, lock=threading.Lock(), run_background=lambda fn: fn(),
            later=self._later, delay_ms=updates.AUTO_CHECK_DELAY_MS, frozen=lambda: False)
        self.later = []
        self.calls = []
        patcher = mock.patch.object(reporting, "game_report", mock.MagicMock())
        self.game_report = patcher.start()
        self.addCleanup(patcher.stop)
        self.game_report.new_install.side_effect = lambda: "0123456789abcdef0123456789abcdef"
        self.game_report.new_secret.side_effect = lambda: "fedcba9876543210fedcba9876543210"
        self.game_report.pending_count.return_value = 2

    def _later(self, ms, fn):
        self.later.append((ms, fn))


class SettingsTest(Case):
    def test_defaults(self):
        settings = self.session.settings
        self.assertIs(settings.report_enabled, True)
        self.assertEqual((settings.report_install, settings.report_secret, settings.report_since), ("", "", ""))

    def test_ensure_generates_once(self):
        self.report.ensure()
        settings = self.session.settings
        self.assertTrue(prefs.valid_install(settings.report_install))
        self.assertTrue(prefs.valid_secret(settings.report_secret))
        self.assertIsNotNone(prefs.install_time(settings.report_since))
        self.assertEqual(self.session.saved, 1)
        self.report.ensure()
        self.assertEqual(self.session.saved, 1)  # 已经是好的：不重写
        self.report.ensure()
        self.assertEqual(settings.report_install, "0123456789abcdef0123456789abcdef")
        self.assertEqual(settings.report_secret, "fedcba9876543210fedcba9876543210")

    def test_ensure_regenerates_bad_install(self):
        self.session.settings.report_install = "NOT-A-VALID-ID"
        self.session.settings.report_since = "2020-01-01T00:00:00"
        self.report.ensure()
        self.assertEqual(self.session.settings.report_install, "0123456789abcdef0123456789abcdef")
        self.assertEqual(self.session.settings.report_secret, "fedcba9876543210fedcba9876543210")
        self.assertNotEqual(self.session.settings.report_since, "2020-01-01T00:00:00")
        self.assertEqual(self.session.saved, 1)

    def test_ensure_adds_missing_secret_only(self):
        """已经在用的电脑升级上来：有本机编号、没有随机值（或格式不对），只补随机值，编号和时刻不变。"""
        settings = self.session.settings
        settings.report_install = "0123456789abcdef0123456789abcdef"
        settings.report_since = "2026-01-02T03:04:05"
        self.report.ensure()
        self.assertEqual(settings.report_install, "0123456789abcdef0123456789abcdef")
        self.assertEqual(settings.report_since, "2026-01-02T03:04:05")
        self.assertEqual(settings.report_secret, "fedcba9876543210fedcba9876543210")
        self.assertEqual(self.session.saved, 1)

    def test_ensure_clears_when_off(self):
        self.session.settings.report_enabled = False
        self.session.settings.report_install = "0123456789abcdef0123456789abcdef"
        self.session.settings.report_secret = "fedcba9876543210fedcba9876543210"
        self.session.settings.report_since = "2026-01-02T03:04:05"
        self.report.ensure()
        self.assertEqual((self.session.settings.report_install, self.session.settings.report_secret,
                          self.session.settings.report_since), ("", "", ""))
        self.assertEqual(self.session.saved, 1)

    def test_off_clears_reports_and_ids(self):
        self.session.settings.report_install = "0123456789abcdef0123456789abcdef"
        self.session.settings.report_secret = "fedcba9876543210fedcba9876543210"
        self.session.settings.report_since = "2026-01-02T03:04:05"
        self.assertTrue(self.report.set_enabled(False))
        self.assertFalse(self.report.enabled())  # enabled 回调马上变假
        self.assertEqual((self.session.settings.report_install, self.session.settings.report_secret,
                          self.session.settings.report_since), ("", "", ""))
        self.game_report.clear.assert_called_once_with()

    def test_on_again_gets_new_ids_and_time(self):
        self.report.ensure()
        first = (self.session.settings.report_install, self.session.settings.report_secret,
                 self.session.settings.report_since)
        self.report.set_enabled(False)
        self.game_report.new_install.side_effect = lambda: "fedcba9876543210fedcba9876543210"
        self.game_report.new_secret.side_effect = lambda: "0123456789abcdef0123456789abcdef"
        self.assertTrue(self.report.set_enabled(True))
        second = (self.session.settings.report_install, self.session.settings.report_secret,
                  self.session.settings.report_since)
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])
        self.assertTrue(prefs.valid_install(second[0]))
        self.assertTrue(prefs.valid_secret(second[1]))
        self.assertIsNotNone(prefs.install_time(second[2]))
        self.assertEqual(self.session.saved, 3)

    def test_set_enabled_fixes_bad_secret(self):
        """开着而随机值坏了：再点一次打开要能修好（三项一起换新）。"""
        self.report.ensure()
        self.session.settings.report_secret = "NOT-A-VALID-SECRET"
        self.game_report.new_install.side_effect = lambda: "fedcba9876543210fedcba9876543210"
        self.game_report.new_secret.side_effect = lambda: "0123456789abcdef0123456789abcdef"
        self.assertTrue(self.report.set_enabled(True))
        self.assertEqual(self.session.settings.report_install, "fedcba9876543210fedcba9876543210")
        self.assertEqual(self.session.settings.report_secret, "0123456789abcdef0123456789abcdef")
        self.assertFalse(self.report.set_enabled(True))  # 已经是好的：什么都不做


class AssembleTest(Case):
    def test_calls_with_install_secret_and_since(self):
        self.report.ensure()
        settings = self.session.settings
        self.report.assemble("g1")
        args, kwargs = self.game_report.assemble.call_args
        self.assertEqual(args, ("g1",))
        self.assertEqual(kwargs["install"], settings.report_install)
        self.assertEqual(kwargs["secret"], settings.report_secret)
        self.assertEqual(kwargs["since"], prefs.install_time(settings.report_since))
        self.assertTrue(kwargs["enabled"]())

    def test_missing_secret_does_not_assemble(self):
        """本机编号、随机值有一个为空就不拼。"""
        self.report.ensure()
        with mock.patch.object(reporting.Reporting, "secret", return_value=""):
            self.report.assemble("g1")
        self.game_report.assemble.assert_not_called()

    def test_off_does_not_assemble(self):
        self.session.settings.report_enabled = False
        self.report.assemble("g1")
        self.game_report.assemble.assert_not_called()

    def test_assemble_after_turning_off_is_stopped_by_enabled(self):
        self.report.ensure()
        result = {}
        self.game_report.assemble.side_effect = lambda *a, **k: result.update(k)
        self.report.assemble("g1")
        self.report.set_enabled(False)
        self.assertFalse(result["enabled"]())  # 后台拼到一半关掉：再问一次是假

    def test_off_then_on_stops_old_install(self):
        """关掉以后马上又打开（编号换了新的）：后台手上按旧编号拼的那份，写之前再问一次也是假，旧编号的报告不留下。"""
        self.report.ensure()
        result = {}
        self.game_report.assemble.side_effect = lambda *a, **k: result.update(k)
        self.report.assemble("g1")
        self.report.set_enabled(False)
        self.game_report.new_install.side_effect = lambda: "fedcba9876543210fedcba9876543210"
        self.report.set_enabled(True)
        self.assertTrue(self.report.enabled())
        self.assertFalse(result["enabled"]())


class StartupTest(Case):
    def test_off_then_on_stops_sending(self):
        """发到一半关掉又马上打开：这一批按旧编号的发送下一份之前就停（旧编号的报告关掉时已经删掉，不再发）。"""
        self.game_report.target.return_value = "http://127.0.0.1/v1/report"
        self.report.startup()
        self.later[0][1]()
        backfill = self.game_report.backfill.call_args.kwargs["enabled"]
        send = self.game_report.send_pending.call_args.kwargs["enabled"]
        self.assertTrue(backfill() and send())
        self.report.set_enabled(False)
        self.game_report.new_install.side_effect = lambda: "fedcba9876543210fedcba9876543210"
        self.report.set_enabled(True)
        self.assertFalse(backfill())
        self.assertFalse(send())

    def test_backfill_and_send_once(self):
        self.game_report.target.return_value = "http://127.0.0.1/v1/report"
        self.report.startup()
        self.report.startup()  # 再调一次不再排
        self.assertEqual(len(self.later), 1)
        self.assertEqual(self.later[0][0], updates.AUTO_CHECK_DELAY_MS)  # 和自动检查更新同一时刻
        self.later[0][1]()
        self.game_report.backfill.assert_called_once()
        self.game_report.send_pending.assert_called_once()
        self.assertEqual(self.game_report.backfill.call_args.kwargs["secret"],
                         self.session.settings.report_secret)
        self.assertEqual(self.game_report.send_pending.call_args.args, ("http://127.0.0.1/v1/report",))
        self.game_report.target.assert_called_with(False)  # 开发版

    def test_missing_secret_does_not_backfill(self):
        self.game_report.target.return_value = "http://127.0.0.1/v1/report"
        with mock.patch.object(reporting.Reporting, "secret", return_value=""):
            self.report.startup()
            self.later[0][1]()
        self.game_report.backfill.assert_not_called()

    def test_dev_does_not_send(self):
        self.game_report.target.return_value = None
        self.report.startup()
        self.later[0][1]()
        self.game_report.backfill.assert_called_once()
        self.game_report.send_pending.assert_not_called()

    def test_disabled_still_sends_nothing(self):
        self.session.settings.report_enabled = False
        self.report.startup()
        self.game_report.clear.assert_called_once_with()
        self.assertEqual(self.later, [])
        self.game_report.backfill.assert_not_called()
        self.game_report.send_pending.assert_not_called()


class PendingTest(Case):
    def test_pending_tuple(self):
        from advisor.ui.overlay import Overlay

        fake = SimpleNamespace(reporting=self.report)
        with mock.patch("advisor.game_report.pending_count", return_value=2):
            enabled, count, folder = Overlay.report_pending(fake)
        self.assertTrue(enabled)
        self.assertEqual(count, 2)
        self.assertTrue(folder.endswith("logs\\upload"))


class WaitTest(Case):
    """一局只发一份报告，要带上最终的胜负：胜负定了才拼；没定的先等，定了、等满 RESULT_WAIT_MS、下一局开始、退出时再拼。"""

    def setUp(self):
        super().setUp()
        self.report.ensure()
        self.game_report.assemble.side_effect = lambda game, **_k: self.calls.append(game)

    def test_settled_assembles_at_once(self):
        self.report.game_over("g1", True)
        self.assertEqual(self.calls, ["g1"])
        self.assertEqual(self.later, [])
        self.assertEqual(self.report.waiting(), set())

    def test_waits_until_settled(self):
        """没定的先不拼，排一次等满再拼；同一局再叫一次不重排；定了马上拼，之后等满了不再拼。"""
        self.report.game_over("g1", False)
        self.report.game_over("g1", False)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.report.waiting(), {"g1"})
        self.assertEqual([ms for ms, _ in self.later], [reporting.RESULT_WAIT_MS])
        self.report.game_over("g1", True)
        self.assertEqual(self.calls, ["g1"])
        self.later[0][1]()  # 等满了：已经拼过
        self.assertEqual(self.calls, ["g1"])
        self.assertEqual(self.report.waiting(), set())

    def test_timeout_assembles_what_is_known(self):
        """等满了还没定：按推荐日志里现有的拼；之后定了再拼一次，覆盖同一份（下次启动才发）。"""
        self.report.game_over("g1", False)
        self.later[0][1]()
        self.assertEqual(self.calls, ["g1"])
        self.report.settle("g1")  # 下一局开始时又说一次：不再拼
        self.assertEqual(self.calls, ["g1"])
        self.report.game_over("g1", True)
        self.assertEqual(self.calls, ["g1", "g1"])

    def test_settle_all_does_not_hang_exit(self):
        """识别线程卡住、一直占着 Session 的锁：退出最多等 SETTLE_EXIT_S 秒，记一笔错误记录，照常往下走；锁放开以后那个线程
        接着拼完（进程还在时）。"""
        self.report.game_over("g1", False)
        self.report.lock.acquire()
        try:
            with mock.patch.object(reporting, "SETTLE_EXIT_S", 0.05), mock.patch.object(reporting, "log_error") as log:
                begin = time.monotonic()
                self.report.settle_all()
                self.assertLess(time.monotonic() - begin, 5.0)
            log.assert_called_once()
            self.assertEqual(self.calls, [])
        finally:
            self.report.lock.release()
        deadline = time.monotonic() + 5.0
        while not self.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.calls, ["g1"])

    def test_settle_all_on_exit(self):
        """退出时还在等的几局都拼好，拼完才返回（不交给平时的后台线程：它跟着进程结束，可能来不及写完）；拼过的、没在等的不拼。"""
        background = []
        self.report.run_background = background.append
        self.report.game_over("g1", False)
        self.report.game_over("g2", False)
        self.report.settle_all()
        self.assertEqual(self.calls, ["g1", "g2"])
        self.assertEqual(background, [])
        self.assertEqual(self.report.waiting(), set())
        self.report.settle_all()
        self.assertEqual(self.calls, ["g1", "g2"])
        for _ms, fn in self.later:
            fn()  # 等满的计时器晚到：不再拼
        self.assertEqual(self.calls, ["g1", "g2"])

    def test_off_does_not_assemble_waiting(self):
        self.report.game_over("g1", False)
        self.report.set_enabled(False)
        self.report.settle_all()
        self.assertEqual(self.calls, [])

    def test_empty_game_ignored(self):
        self.report.game_over("", False)
        self.report.game_over(None, True)
        self.assertEqual((self.calls, self.later), ([], []))


class LaneTest(unittest.TestCase):
    def test_second_lane_uses_primary(self):
        """两开时两张各管各的一局（局号不同），都交给第一张那一份设置；胜负定了没有原样交过去，不给时按定了。"""
        from advisor.ui.overlay import Overlay

        primary = SimpleNamespace(primary=None, reporting=mock.MagicMock())
        second = SimpleNamespace(primary=primary, reporting=mock.MagicMock())
        Overlay.report_game_over(primary, "g1")
        Overlay.report_game_over(second, "g2", False)
        self.assertEqual(primary.reporting.game_over.call_args_list,
                         [mock.call("g1", True), mock.call("g2", False)])
        second.reporting.game_over.assert_not_called()


class DispatchTest(unittest.TestCase):
    """推荐日志记完对局结束以后才拼这一局；同一局再记一次不再拼。胜负没定的告诉 Reporting 先等，定了（奖励画面的
    status，没有新的一条也算）、下一局开始时再叫一次，说定了。"""

    def handler(self, logged, settled=True, info=True):
        from advisor.ui.overlay_parts.dispatch import EventHandlers

        self.logged = logged
        offers = SimpleNamespace(game_over=lambda info, games: self.logged, game_id=lambda: "g1")
        self.session = SimpleNamespace(game_over_info={"why": "结算"} if info else None, games=3,
                                       result_settled=settled,
                                       settings=SimpleNamespace(star=0, star_read=False, owned=[]),
                                       game=SimpleNamespace(progress=0))
        return EventHandlers(
            db=None, session=self.session, lock=threading.Lock(), live=None, message=None, notice_model=None,
            advice=None, owned=None, offers=offers, eggs=None, notices=None, flash=None, waiting_text=None,
            newgame_notice=None, refresh=None, report_game=lambda game, settled: self.calls.append((game, settled)))

    def setUp(self):
        self.calls = []

    def test_logged_game_over_assembles(self):
        handler = self.handler(True)
        handler.log_game_over()
        self.assertEqual(self.calls, [("g1", True)])
        self.assertIsNone(handler.report_waiting)

    def test_duplicate_game_over_does_not_assemble(self):
        self.handler(False).log_game_over()
        self.assertEqual(self.calls, [])

    def test_unsettled_waits_for_status(self):
        """结束时胜负没定：先说没定；奖励画面读到了、胜负没变（推荐日志不再记）也说定了，只说一次。"""
        handler = self.handler(True, settled=False)
        handler.log_game_over()
        self.assertEqual(self.calls, [("g1", False)])
        self.assertEqual(handler.report_waiting, "g1")
        self.logged = False
        handler.on_status(None, False, "")  # 状态变了、胜负还没定：不叫
        self.assertEqual(self.calls, [("g1", False)])
        self.session.result_settled = True
        handler.on_status(None, False, "")
        self.assertEqual(self.calls, [("g1", False), ("g1", True)])
        self.assertIsNone(handler.report_waiting)
        handler.on_status(None, False, "")
        self.assertEqual(len(self.calls), 2)

    def test_unsettled_then_new_record(self):
        """奖励画面让胜负变了：推荐日志再记一条，这时说定了。"""
        handler = self.handler(True, settled=False)
        handler.log_game_over()
        self.session.result_settled = True
        handler.on_status(None, False, "")
        self.assertEqual(self.calls, [("g1", False), ("g1", True)])
        self.assertIsNone(handler.report_waiting)

    def test_next_game_settles_waiting(self):
        """下一局开始时还在等：不再等，说定了；没在等的什么都不做。"""
        handler = self.handler(True, settled=False)
        handler.log_game_over()
        handler.settle_report()
        self.assertEqual(self.calls, [("g1", False), ("g1", True)])
        handler.settle_report()
        self.assertEqual(len(self.calls), 2)

    def test_no_end_no_report(self):
        """还没结束（状态变了的 status 天天有）：不记、不拼。"""
        handler = self.handler(False, settled=False, info=False)
        handler.on_status(None, False, "")
        handler.settle_report()
        self.assertEqual(self.calls, [])

    def test_rejoined_logs_and_reports_again(self):
        """主菜单结束的那一局断线以后又连回来（status 的 cause 为 rejoined）：推荐日志（真的 OfferLog）记过的那条结束不再挡着
        去重，这一局真正结束时局数没变、胜负一样也再记一条，报告再拼一次（覆盖同一份，以后一条为准，同撤销）；连回来的那一下
        本身不记、不拼，也不另加提示。"""
        from advisor.ui.overlay_parts.dispatch import EventHandlers
        from advisor.ui.overlay_parts.records import OfferLog
        from advisor.ui.services import Services
        from tests.support.fakes import FakeLogbook

        book = FakeLogbook()
        services = Services(logbook=book)
        offers = OfferLog(lambda: services)
        info = {"why": "检测到主菜单", "outcome": None, "how": None}
        session = SimpleNamespace(game_over_info=info, games=1, result_settled=True, panel_prompt_active=False,
                                  settings=SimpleNamespace(star=0, star_read=False, owned=[]),
                                  game=SimpleNamespace(progress=0))
        said = []
        handler = EventHandlers(
            db=None, session=session, lock=threading.Lock(), live=SimpleNamespace(on=False),
            message=SimpleNamespace(say=lambda text, card: said.append(text)),
            notice_model=SimpleNamespace(panel_prompt=False, panel_tab=None), advice=None, owned=None,
            offers=offers, eggs=None, notices=None, flash=None, waiting_text=lambda: "等待", newgame_notice=None,
            refresh=None, report_game=lambda game, settled: self.calls.append((game, settled)))
        handler.log_game_over()
        handler.log_game_over()  # 同一局同样的结束：不再记
        self.assertEqual(len(book.game_overs), 1)
        session.game_over_info = None  # 连回来了：Session 撤回了那次结束
        handler.on_status(SimpleNamespace(kind="status", cause="rejoined"), False, "")
        self.assertEqual(len(book.game_overs), 1)
        self.assertEqual(said, ["等待"])  # 等下一局的那句换回等三选一的那句
        session.game_over_info = {"why": "检测到结算画面", "outcome": None, "how": None}
        handler.log_game_over()
        self.assertEqual([g["why"] for g in book.game_overs], ["检测到主菜单", "检测到结算画面"])
        self.assertEqual(self.calls, [("g1", True), ("g1", True)])


class InstallValueTest(unittest.TestCase):
    def test_valid_install(self):
        self.assertTrue(prefs.valid_install("0123456789abcdef0123456789abcdef"))
        for bad in ("", "0123456789ABCDEF0123456789ABCDEF", "0123456789abcdef", None, 3):
            with self.subTest(bad=bad):
                self.assertFalse(prefs.valid_install(bad))

    def test_install_time(self):
        self.assertEqual(prefs.install_time("2026-01-02T03:04:05"), datetime(2026, 1, 2, 3, 4, 5))
        for bad in ("", "昨天", None, 3):
            with self.subTest(bad=bad):
                self.assertIsNone(prefs.install_time(bad))


if __name__ == "__main__":
    unittest.main()
