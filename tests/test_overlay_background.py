"""后台任务的界面：导出诊断、首次下载识别图片、托盘、关于窗口，以及推荐日志和识别记录的转接；logbook、diagnostics、assets、version 用假对象代替。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import shutil
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import shiboken6  # noqa: E402
from PySide6.QtCore import QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from advisor import paths as app_paths  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import REROLL  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.background import DOWNLOAD_PAUSE_OVER
from advisor.ui.overlay_parts.wording import BLOCKED_BIT
from advisor.ui.overlay_parts.wording import FEEDBACK_REASONS, CARD_KEYS
from advisor.ui.settings.dialog import EXPORT_TIP
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import tip_paragraphs_html  # noqa: E402
from advisor.ui.widgets import chip_icon  # noqa: E402
from advisor.ui import widgets as ui_widgets  # noqa: E402
from tests.support import sandbox  # noqa: E402
from tests.support.errors import capture_errors  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.waits import wait_until  # noqa: E402
from tests.support.overlay_cases import APP, FakeHotkey, FakeRecorder, FakeWatcher, PROJECT, button, buttons, capture_state, frame, probe, shown, tip_text


class OverlayServicesTest(unittest.TestCase):
    """推荐日志、未选择首推的原因、校准提醒、导出诊断、图片下载、关于。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.logbook = FakeLogbook()
        self.exports = []
        self.extras = []  # 每次导出的 extra（另外写进诊断包的记录，例如推荐日志）
        self.unreads = []  # 每次导出另带的三选一读不全时的那一帧（select_unreadable；没带时为 None）
        self.shots = []  # 每次导出另带的记下选中项用的那一帧、不采用的那一帧（select_picked、select_covered）
        self.finishes = []  # 每次导出另带的对局中途像一局打完的那一帧（finish_look）
        self.lanes = []  # 每次导出带的另一张悬浮窗的画面和识别记录（lanes；没带时为 None）
        self.downloads = []

        def export(frame_bgr, snapshot, events, advice_text_, extra=None, auto=False, select_unreadable=None,
                   select_picked=None, select_covered=None, finish_look=None, lanes=None):
            self.exports.append((frame_bgr, snapshot, events, advice_text_))
            self.extras.append(extra)
            self.unreads.append(select_unreadable)
            self.shots.append((select_picked, select_covered))
            self.finishes.append(finish_look)
            self.lanes.append(lanes)
            return str(PROJECT / "reports" / "20261002-120000")

        self.missing = ["heroes/a.png", "heroes/b.png"]

        def download(items, progress, stop, stats=None):
            progress(1, len(items), items[0])
            # 下载期间：提示行、停止标志、后台识别暂停没有（watcher 换成 FakeWatcher 以前为 None）
            watcher = self.window.watcher
            paused = watcher.paused.get("assets") if watcher is not None else None
            self.downloads.append((list(items), self.window.notice_model.texts().get("assets"), stop(), paused))
            return (len(items) - 1, items[1:])

        self.services = Services(
            logbook=self.logbook,
            diagnostics=SimpleNamespace(export=export),
            assets=SimpleNamespace(missing=lambda: list(self.missing), download=download),
            version=SimpleNamespace(
                VERSION="1.0.0",
                about_sections=lambda: [("版本", "拾星助手 1.0.0"), ("资料来源与授权", "Astral Party Wiki https://example.org/wiki")],
            ),
        )
        self.window = Overlay(self.db, self.session, threading.Lock(), services=self.services,
                              frame_source=lambda: "画面")
        self.window.run_background = lambda fn: fn()
        self.window.watcher = FakeWatcher()

    def tearDown(self):
        self.window.hide()

    def test_snapshot_hero_locked(self):
        """诊断包的 state.json 里写上「角色是不是手动指定的」（Background.snapshot 从 session.game 取）。"""
        self.session.game.hero_locked = True
        self.assertTrue(self.window.background.snapshot()["hero_locked"])
        self.session.game.hero_locked = False
        self.assertFalse(self.window.background.snapshot()["hero_locked"])

    def pick(self, offer, chip, window=None):
        """三选一界面上选择了 chip，界面关闭。"""
        w = window or self.window
        self.session.on_frame(frame(offer, selected=offer.index(chip)))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))

    def test_offer_pick_and_refresh_logged(self):
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"], rerolls=3)))
        self.assertEqual(len(self.logbook.offers), 1)
        first = self.logbook.offers[0][0]
        # 刷新：剩余次数少了一次、候选换了 → 上一组记为刷新，新的一组换编号
        w.handle(self.session.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"], rerolls=2)))
        self.assertEqual(self.logbook.picks, [(first, "刷新")])  # 日志里记作 logbook.PICK_REROLL
        second = self.logbook.offers[1][0]
        self.assertNotEqual(first, second)
        self.session.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"], rerolls=2, selected=1))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        self.assertEqual(self.logbook.picks[-1], (second, "标记喷罐"))

    def test_new_game_logged_and_undone(self):
        """判定为新的一局时告诉推荐日志；撤销时接回原来那一局。"""
        w = self.window
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        self.assertEqual(self.logbook.games_log, [("new", "g1")])
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(self.logbook.games_log[-1], ("undo", "g1"))
        self.assertEqual(self.logbook.game, "g1")

    def test_undo_saves_record_without_frame(self):
        """点「撤销」说明判定有误，自动存一份诊断包（不含截图），记下误判时的状态和识别记录。"""
        w = self.window
        self.session.set_owned(["书签"])
        self.session.recent_events = lambda seconds: [{"kind": "read:stars", "stars": 0}]
        w.handle(self.session._auto_new_game("star", None, "星数由 2 星变为 0 星"))
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(len(self.exports), 1)
        frame_bgr, snapshot, events, _ = self.exports[0]
        self.assertIsNone(frame_bgr)  # 不含截图
        self.assertIn("撤销新的一局的判定", snapshot["reason"])
        self.assertEqual(snapshot["settings"]["owned"], [])  # 误判时（撤销前）的状态
        self.assertEqual(events, [{"kind": "read:stars", "stars": 0}])
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertIn("误判时的识别记录已保存（不含截图）", w.status.status_text())
        self.assertIn("export", w.notice_model.texts())  # 「打开文件夹」可以找到

    def test_undo_without_diagnostics_still_works(self):
        w = Overlay(self.db, self.session, threading.Lock())
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        w.undo_new_game()
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertIn("已撤销新的一局的判定", w.status.status_text())
        w.hide()

    def test_select_new_game_undo_also_saves(self):
        w = self.window
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("select", None, "检测到选角界面"))
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(len(self.exports), 1)
        self.assertIsNone(self.exports[0][0])

    def test_missed_pick_logged_after_record(self):
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        offer = self.logbook.offers[0][0]
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        button(w.missed, "大背包").click()
        self.assertEqual(self.logbook.picks, [(offer, "大背包", "supplement")])

    def test_question_when_best_not_picked(self):
        """没选首推：「推荐」卡片显示「已选：X」和「未选择「首推」的原因？」加四个原因按钮（一行显示时也显示）；
        点一个记入推荐日志，这一行换成「已记录：X」；下一次三选一出现时消失。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        best = w.advice.last_ev.advice.best
        other = next(n for n in offer if n != best)
        self.pick(offer, other)
        self.assertFalse(w.expanded)
        self.assertFalse(w.advice_card.isHidden())
        self.assertFalse(w.question.isHidden())
        advice = shown(w, w.advice_card)
        best_name = "刷新" if best == REROLL else best
        self.assertIn(f"未选择「{best_name}」的原因？", advice)
        self.assertIn(f"已选：{other}", advice)
        self.assertEqual([b.text() for b in w.reason_buttons], list(FEEDBACK_REASONS))
        self.assertEqual(FEEDBACK_REASONS, ("照顾队友", "地图因素", "推荐有误", "想试别的玩法"))
        self.assertFalse(any(b.isHidden() for b in w.reason_buttons))
        w.reason_buttons[2].click()
        self.assertEqual(self.logbook.feedback, [(offer_id, "推荐有误", "")])
        self.assertIn("已记录：推荐有误", shown(w, w.advice_card))
        self.assertTrue(all(b.isHidden() for b in w.reason_buttons))
        w.advice.submit_feedback("照顾队友")  # 已记录：不重复记
        self.assertEqual(len(self.logbook.feedback), 1)
        # 下一次三选一出现：这一行消失
        w.handle(self.session.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"])))
        self.assertTrue(w.question.isHidden())
        self.assertNotIn("原因", shown(w, w.advice_card))

    def test_export_zip(self):
        """导出诊断：导出的文件夹旁边另打一个同名的压缩包（diagnostics.zip_report，在导出的后台线程里打）：提示里写压缩包的
        位置，「打开文件夹」在资源管理器里选中它（winapi.reveal_file）。压缩包打不成：文件夹照旧可用，提示照实说没打成（原因
        进错误记录），「打开文件夹」打开文件夹（winapi.open_folder）。"""
        from advisor.ui import winapi
        from advisor.ui.overlay_parts import background
        from advisor.ui.overlay_parts.wording import short_path

        w = self.window
        folder = sandbox.temp_dir(self, "export-") / "20261024-120000"
        folder.mkdir()
        (folder / "state.json").write_text("{}", encoding="utf-8")
        zipped = []

        def zip_report(path):
            zipped.append(path)
            target = Path(f"{path}.zip")
            target.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
            return target

        self.services.diagnostics.export = lambda *a, **k: str(folder)
        self.services.diagnostics.zip_report = zip_report
        w.export_diagnostics()
        self.assertEqual(zipped, [str(folder)])
        zip_path = f"{folder}.zip"
        self.assertEqual(w.notice_model.texts()["export"], f"已导出诊断包：{short_path(zip_path)}")
        self.assertEqual(tip_text(w.notices.buttons("export")[0].toolTip()), "在资源管理器中打开诊断包所在的文件夹，并选中压缩包")
        with mock.patch.object(winapi, "reveal_file", return_value=True) as reveal, \
                mock.patch.object(winapi, "open_folder", return_value=True) as open_folder:
            w.notices.buttons("export")[0].click()
        reveal.assert_called_once_with(zip_path)
        open_folder.assert_not_called()
        # 打不成：文件夹照旧，提示照实说，原因进错误记录
        Path(zip_path).unlink()
        errors = []

        def broken(path):
            raise OSError("磁盘空间不足")

        self.services.diagnostics.zip_report = broken
        with mock.patch.object(background, "log_error", side_effect=lambda title, detail, **kw: errors.append((title, detail))):
            w.export_diagnostics()
        self.assertEqual(w.notice_model.texts()["export"], f"已导出诊断包：{short_path(str(folder))}（压缩包没有打成）")
        self.assertEqual([t for t, _ in errors], ["诊断包的压缩包没打成（诊断包文件夹照旧可用，拾星助手仍在运行）"])
        self.assertIn("磁盘空间不足", errors[0][1])
        self.assertTrue(folder.is_dir())
        with mock.patch.object(winapi, "reveal_file", return_value=True) as reveal, \
                mock.patch.object(winapi, "open_folder", return_value=True) as open_folder:
            w.notices.buttons("export")[0].click()
        open_folder.assert_called_once_with(str(folder))
        reveal.assert_not_called()

    def test_export_usage_probe_and_records(self):
        """导出诊断时：先在后台线程里现量一小段（usage.probe，早于读识别记录），写进 state.json 的 usage_probe；内存里
        每分钟那条占用记录（usage.records）经 extra 写成 usage.jsonl。"""
        order = []
        records = [{"at": "2026-11-07T15:00:00", "s": 60.0}]
        self.services.usage = SimpleNamespace(
            probe=lambda seconds=None: (order.append("probe"), {"thread_count": 2, "threads": []})[1],
            records=lambda: (order.append("records"), records)[1],
        )
        self.services.diagnostics.USAGE = "usage.jsonl"
        self.session.recent_events = lambda seconds: (order.append("events"), [])[1]
        self.window.export_diagnostics()
        self.assertEqual(order[0], "probe")
        self.assertIn("events", order)
        self.assertEqual(self.exports[0][1]["usage_probe"], {"thread_count": 2, "threads": []})
        self.assertEqual(self.extras[0], {"usage.jsonl": records, "stats.jsonl": []})
        self.assertEqual(self.exports[0][1]["stats_scope"],
                         {"hours": 12.0, "since": None, "segments": 0, "lines": 0, "source": "none"})

    def test_export_without_usage_still_works(self):
        """没有占用模块、或 probe 返回 None 时照常导出：不带 usage_probe，也不带 usage.jsonl。"""
        self.window.export_diagnostics()
        self.assertEqual(len(self.exports), 1)
        self.assertNotIn("usage_probe", self.exports[0][1])
        self.window.background.export_done(None)
        self.services.usage = SimpleNamespace(probe=lambda seconds=None: None, records=lambda: [])
        self.window.export_diagnostics()
        self.assertNotIn("usage_probe", self.exports[1][1])
        self.assertEqual(self.extras[1] or {}, {"stats.jsonl": []})

    def test_undo_does_not_probe_usage(self):
        """撤销误判时自动保存的诊断包要快、最多留 20 份：不现量、不带每分钟的记录。"""
        calls = []
        self.services.usage = SimpleNamespace(probe=lambda *a, **k: calls.append("probe"),
                                              records=lambda: calls.append("records"))
        self.window.background.save_undo_record({}, [], "")
        self.assertEqual(calls, [])
        self.assertEqual(len(self.exports), 1)
        self.assertTrue(self.exports[0][1]["reason"].startswith("撤销新的一局的判定"))

    def test_feedback_reasons_match_logbook(self):
        """界面上给出的原因和写进推荐日志的原因一字不差；校准时不算推荐不准的那一个（FEEDBACK_TRY）两边也一样，而且在原因里。"""
        from advisor import logbook
        from advisor.ui.overlay_parts.wording import FEEDBACK_TRY

        self.assertEqual(FEEDBACK_REASONS, logbook.FEEDBACK_REASONS)
        self.assertEqual(FEEDBACK_TRY, logbook.FEEDBACK_TRY)
        self.assertIn(FEEDBACK_TRY, FEEDBACK_REASONS)
        self.assertIn(logbook.FEEDBACK_TRY, logbook.FEEDBACK_REASONS)

    def test_reason_tips(self):
        """原因按钮的悬停说明：「想试别的玩法」写不算推荐不准的那一句（FEEDBACK_TRY_TIP），其余照 FEEDBACK_TIP 填原因；
        「×」写不选原因、收起这一问。"""
        from advisor.ui.overlay_parts.wording import (FEEDBACK_DISMISS, FEEDBACK_DISMISS_TIP, FEEDBACK_TIP, FEEDBACK_TRY,
                                                      FEEDBACK_TRY_TIP)

        w = self.window
        tips = {b.text(): tip_text(b.toolTip()) for b in w.reason_buttons}
        self.assertEqual(list(tips), list(FEEDBACK_REASONS))
        for reason, tip in tips.items():
            with self.subTest(reason=reason):
                want = FEEDBACK_TRY_TIP if reason == FEEDBACK_TRY else FEEDBACK_TIP.format(reason=reason)
                self.assertEqual(tip, want)
        self.assertIn("不算作推荐不准", tips[FEEDBACK_TRY])
        self.assertEqual(w.feedback_dismiss.text(), FEEDBACK_DISMISS)
        self.assertEqual(tip_text(w.feedback_dismiss.toolTip()), FEEDBACK_DISMISS_TIP)

    def test_question_line_collapses(self):
        """一行显示时「已选：X」「未选择「首推」的原因？」那一块只挂 QUESTION_LINE_MS（20 秒）：到点不点就收起，这一次不记原因；
        全部展开时照旧显示（挂到下一次三选一），点了照常记。到点以后再收回一行也不再出现。"""
        from advisor.ui.overlay_parts.wording import QUESTION_LINE_MS

        self.assertEqual(QUESTION_LINE_MS, 20000)
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        self.assertFalse(w.expanded)
        self.assertFalse(w.question.isHidden())
        self.assertGreater(w.advice.ask_until, 0)
        w.advice.ask_until -= QUESTION_LINE_MS / 1000 + 1  # 到点
        w.refresh()
        self.assertTrue(w.advice_card.isHidden())  # 一行显示：整块收起
        self.assertTrue(w.question.isHidden())
        self.assertEqual(self.logbook.feedback, [])  # 不点不记
        self.assertEqual(w.advice.question_state(), "ask")
        w.set_expanded(True)  # 全部展开：照旧问
        self.assertFalse(w.question.isHidden())
        self.assertFalse(any(b.isHidden() for b in w.reason_buttons))
        w.set_expanded(False)
        self.assertTrue(w.question.isHidden())
        w.set_expanded(True)
        w.reason_buttons[0].click()
        self.assertEqual(self.logbook.feedback, [(offer_id, "照顾队友", "")])

    def test_dismiss_question_line(self):
        """一行显示时要问原因，点问句右边的「×」：「已选」和问原因这一块马上收起，卡片回到一行；
        推荐日志没有这一次的 feedback，识别记录里有 dismiss_feedback；过多久都不再出现，也不出现「已记录」。
        和 20 秒不点就收起的不同：之后全部展开也不再问（只写「已选：X」）。"""
        from advisor.ui.overlay_parts.wording import QUESTION_LINE_MS

        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        self.assertFalse(w.expanded)
        self.assertFalse(w.question.isHidden())
        self.assertFalse(w.feedback_dismiss.isHidden())
        w.feedback_dismiss.click()
        self.assertTrue(w.question.isHidden())
        self.assertTrue(w.picked_label.isHidden())
        self.assertTrue(w.advice_card.isHidden())
        self.assertEqual(w.advice.question_state(), None)
        self.assertEqual(self.logbook.feedback, [])
        users = [e for e in self.session.recent_events(600) if e["kind"] == "read:user"]
        self.assertEqual([e["action"] for e in users].count("dismiss_feedback"), 1)
        self.assertNotIn("已记录", shown(w, w.advice_card))
        w.advice.dismiss_feedback()  # 再点一次也不变，不再记一条
        self.assertEqual(self.logbook.feedback, [])
        users = [e for e in self.session.recent_events(600) if e["kind"] == "read:user"]
        self.assertEqual([e["action"] for e in users].count("dismiss_feedback"), 1)
        w.advice.ask_until -= QUESTION_LINE_MS / 1000 + 1  # 过了 20 秒
        w.refresh()
        self.assertTrue(w.advice_card.isHidden())
        w.set_expanded(True)
        self.assertTrue(w.question.isHidden())
        self.assertTrue(all(b.isHidden() for b in w.reason_buttons))
        self.assertTrue(w.feedback_dismiss.isHidden())
        advice = shown(w, w.advice_card)
        self.assertIn(f"已选：{other}", advice)
        self.assertNotIn("的原因？", advice)

    def test_dismiss_expanded_keeps_picked(self):
        """全部展开时点「×」：问原因这一块不见了，「已选：X」照旧显示。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        w.set_expanded(True)
        self.assertFalse(w.question.isHidden())
        w.feedback_dismiss.click()
        self.assertTrue(w.question.isHidden())
        self.assertFalse(w.picked_label.isHidden())
        self.assertIn(f"已选：{other}", shown(w, w.advice_card))
        self.assertEqual(self.logbook.feedback, [])

    def test_dismiss_stops_later_feedback_and_new_pick_asks_again(self):
        """点了「×」以后再调 submit_feedback 也不记录；下一次三选一选的又不是首推时照常再问。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        w.feedback_dismiss.click()
        w.advice.submit_feedback("照顾队友")
        self.assertEqual(self.logbook.feedback, [])
        self.assertIsNone(w.advice.question_state())
        w.handle(self.session.on_frame(frame(["书签", "标记喷罐", "鹰眼瞄具"])))
        second = ["书签", "标记喷罐", "鹰眼瞄具"]
        other2 = next(n for n in second if n != w.advice.last_ev.advice.best)
        self.pick(second, other2)
        self.assertEqual(w.advice.question_state(), "ask")
        self.assertFalse(w.question.isHidden())
        self.assertFalse(w.feedback_dismiss.isHidden())
        w.feedback_dismiss.click()  # 第二次照样能收起
        self.assertTrue(w.question.isHidden())

    def test_dismiss_hidden_after_recorded(self):
        """已记录原因以后「×」不显示；调 dismiss_feedback() 不改变什么。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        w.set_expanded(True)
        w.reason_buttons[0].click()
        self.assertEqual(w.advice.question_state(), "done")
        self.assertTrue(w.feedback_dismiss.isHidden())
        w.advice.dismiss_feedback()
        self.assertEqual(w.advice.question_state(), "done")
        self.assertEqual(self.logbook.feedback, [(offer_id, "照顾队友", "")])

    def test_feedback_try_other_school(self):
        """点「想试别的玩法」：记进推荐日志，这一行换成「已记录：想试别的玩法」。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other)
        self.assertEqual(w.reason_buttons[-1].text(), "想试别的玩法")
        w.reason_buttons[-1].click()
        self.assertEqual(self.logbook.feedback, [(offer_id, "想试别的玩法", "")])
        w.set_expanded(True)
        self.assertIn("已记录：想试别的玩法", shown(w, w.advice_card))

    def test_no_question_when_best_picked(self):
        """选的就是首推：不出现任何反馈入口（没有「不认同」按钮）。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        best = w.advice.last_ev.advice.best
        self.assertIn(best, offer)
        self.assertEqual([b for b in buttons(w) if b.text() in FEEDBACK_REASONS + ("不认同",)], [])
        self.pick(offer, best)
        self.assertIsNone(w.advice.question_state())
        self.assertTrue(w.advice_card.isHidden())
        w.set_expanded(True)
        self.assertTrue(w.question.isHidden())
        self.assertIn(f"已选：{best}（首推）", shown(w, w.advice_card))
        self.assertEqual([b for b in buttons(w) if b.text() in FEEDBACK_REASONS + ("不认同",)], [])

    def test_question_after_missed_pick_recorded(self):
        """未检测到选择、用补录行补录的不是首推：同样问原因，记在那一次推荐上。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        best = w.advice.last_ev.advice.best
        other = next(n for n in offer if n != best)
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))  # 未检测到选择
        self.assertIsNone(w.advice.question_state())
        button(w.missed, other).click()
        self.assertEqual(w.advice.question_state(), "ask")
        w.advice.submit_feedback("地图因素")
        self.assertEqual(self.logbook.feedback, [(offer_id, "地图因素", "")])

    def test_panel_fixes_logged_pick(self):
        """三选一时记下的是绿框里的筹码，之后「查看筹码」面板上实际是同一组里的另一个（思考时间用完、
        游戏自动拿了最左边的）：推荐日志里那一次的选择改记成面板上的（带 via=panel），「已选」跟着改，
        不问未选择首推的原因，提示里写明改了什么。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        offer_id = self.logbook.offers[0][0]
        best = w.advice.last_ev.advice.best
        wrong = best if best in offer else offer[1]
        right = next(n for n in offer if n != wrong)
        self.pick(offer, wrong)
        self.assertEqual(self.logbook.picks, [(offer_id, wrong)])
        reading = SimpleNamespace(is_panel=True, self_tab=True, owned=[right], unknown_slots=0, confidence=0.99)
        w.handle(self.session.on_panel(reading))
        self.assertEqual(self.logbook.picks[-1], (offer_id, right, "panel"))
        self.assertEqual(self.session.settings.owned, [right])
        self.assertEqual(w.advice.pick["chip"], right)
        self.assertIsNone(w.advice.question_state())
        self.assertIn(f"已按面板改正选择记录：{wrong} → {right}", w.message.text)

    def test_feedback_without_logbook(self):
        """没有推荐日志（第二张悬浮窗）：不问未选择首推的原因，点了也记不下来。"""
        w = Overlay(self.db, self.session, threading.Lock())  # 没有推荐日志模块
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer)))
        other = next(n for n in offer if n != w.advice.last_ev.advice.best)
        self.pick(offer, other, window=w)
        self.assertIsNone(w.advice.question_state())
        before = w.message.text
        w.advice.submit_feedback("照顾队友")
        self.assertEqual(w.advice.feedback_done, "")
        self.assertEqual(w.message.text, before)
        self.assertNotIn("推荐日志不可用", w.status.status_text())

    def test_calibration_reminder(self):
        w = self.window
        self.logbook.games = 19
        w.notices.check_calibration()
        w.refresh()
        self.assertNotIn("calibrate", w.notice_model.texts())
        self.logbook.games = 20
        w.handle(Event("newgame", why="检测到选角界面"))
        self.assertEqual(w.notice_model.texts()["calibrate"], "推荐日志已满 20 局，可以校准")
        self.assertFalse(w.global_area.isHidden())  # 标题栏下方的全局提示
        w.notices.buttons("calibrate")[0].click()
        self.assertNotIn("calibrate", w.notice_model.texts())

    def test_no_calibration_reminder_in_release(self):
        """发行版不带校准工具：满 20 局也不提示。"""
        w = self.window
        self.logbook.games = 25
        self.services.diagnostics.is_frozen = lambda: True
        w.notices.check_calibration()
        w.refresh()
        self.assertNotIn("calibrate", w.notice_model.texts())

    def test_reverted_pick_logged_on_original_offer(self):
        """同一组候选关闭后又出现：撤销记在原来那一组的编号上，这一组沿用同一个编号。"""
        from advisor.vision.pipeline import FrameProcessor

        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer, selected=1)))
        first = self.logbook.offers[0][0]
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))  # 记下大背包
        self.assertEqual(self.logbook.picks, [(first, "大背包")])
        # 同一组候选又出现：FrameProcessor 把 pick_reverted 放在推荐前面交出
        events: list = []
        FrameProcessor(self.db, self.session)._call(events, self.session.on_frame, frame(offer))
        self.assertEqual([e.kind for e in events][:2], ["pick_reverted", "advice"])
        for ev in events:
            w.handle(ev)
        self.assertEqual(self.logbook.picks[-1], (first, None))
        self.assertEqual({o[0] for o in self.logbook.offers}, {first})
        # 这一次选了标靶：仍记在同一个编号上（以后一条为准）
        self.session.on_frame(frame(offer, selected=0))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        self.assertEqual(self.logbook.picks[-1], (first, "标靶"))

    def test_reverted_pick_when_advice_arrives_first(self):
        """事件顺序是先推荐、后撤销时（不经 FrameProcessor），也改记回原来的编号。"""
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer, selected=1)))
        first = self.logbook.offers[0][0]
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        w.handle(self.session.on_frame(frame(offer)))  # 推荐先到（新编号）
        for ev in self.session.take_events():
            w.handle(ev)
        self.assertEqual(self.logbook.picks[-1], (first, None))
        self.assertEqual(self.logbook.offers[-1][0], first)
        self.assertEqual(w.offers.offer_id, first)

    def test_export_diagnostics(self):
        """设置窗口「诊断包」那一行的「导出诊断」：写诊断包（后台线程）期间按钮不可点，写完恢复；结果在悬浮窗的提示里。"""
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.set_expanded(True)
        w.dialogs.show_settings()
        self.addCleanup(w.dialogs.settings.close)
        pending = []
        w.run_background = pending.append  # 先不写，看写的期间按钮的样子
        w.dialogs.settings.export_btn.click()
        self.assertFalse(w.dialogs.settings.export_btn.isEnabled())
        self.assertIn("正在导出诊断包…", w.status.status_text())
        w.dialogs.settings.export_btn.click()  # 正在写：再点不另写一份
        self.assertEqual(len(pending), 1)
        pending[0]()
        self.assertEqual(len(self.exports), 1)
        frame_bgr, snapshot, events, text = self.exports[0]
        self.assertEqual(frame_bgr, "画面")
        self.assertEqual(snapshot["settings"]["hero"], "凛")
        self.assertIn("overlay", snapshot)
        self.assertIn("来源 升星或筹码商店", snapshot["overlay"]["status"])
        self.assertEqual(set(snapshot["overlay"]["cards"]), set(CARD_KEYS))
        self.assertTrue(any(e.get("kind") == "advice" for e in events))
        self.assertIn("标靶", text)
        self.assertIn("已导出诊断包：", w.notice_model.texts()["export"])
        self.assertEqual([b.text() for b in w.notices.buttons("export")], ["打开文件夹", "关闭本条提示"])
        self.assertTrue(w.dialogs.settings.export_btn.isEnabled())

    def test_export_uses_session_events(self):
        """没有识别记录器时退回最近的识别事件，玩家名字同样换成代号；推荐日志从最早一条的时刻取。"""
        self.session.recent_events = lambda seconds: [
            {"time": "2026-10-03T13:00:01.000", "kind": "hud", "round": 3},
            {"time": "2026-10-03T13:00:02.000", "kind": "read:players", "rows": [{"name": "某玩家", "bot": False}]},
        ]
        self.logbook.records = [{"t": "offer", "time": "2026-10-03T12:59:00"}, {"t": "pick", "time": "2026-10-03T13:00:05"}]
        self.window.export_diagnostics()
        events = self.exports[0][2]
        self.assertEqual(events[0], {"time": "2026-10-03T13:00:01.000", "kind": "hud", "round": 3})
        self.assertRegex(events[1]["rows"][0]["name"], r"^名字#[0-9a-f]{6}$")
        self.assertNotIn("某玩家", repr(events))
        self.assertEqual(self.logbook.between, ["2026-10-03T13:00:01.000"])
        self.assertEqual(self.extras[0], {"logbook.jsonl": [{"t": "pick", "time": "2026-10-03T13:00:05"}],
                                          "stats.jsonl": []})
        self.assertEqual(self.exports[0][1]["export_scope"]["source"], "recent_events")

    def test_export_whole_game_from_recorder(self):
        """有识别记录器时：先写盘再取本局和上一局两段，原样交给诊断包；state.json 带上导出范围；
        推荐日志按两段里最早一条的时刻取（logbook.records_between），写成 logbook.jsonl。"""
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        self.session.recent_events = lambda seconds: self.fail("有识别记录器时不应退回最近的识别事件")
        self.logbook.records = [{"t": "offer", "time": "2026-10-03T12:45:00", "offer_id": "a"}]
        self.window.export_diagnostics()
        self.assertEqual(recorder.calls, ["flush", "collect", "collect_stats"])
        _, snapshot, events, _ = self.exports[0]
        self.assertEqual(events, FakeRecorder.SEGMENTS)
        self.assertEqual(snapshot["export_scope"], FakeRecorder.SCOPE)
        self.assertEqual(self.logbook.between, [FakeRecorder.SCOPE["since"]])
        self.assertEqual(self.extras[0], {"logbook.jsonl": self.logbook.records, "stats.jsonl": FakeRecorder.STATS})
        self.assertEqual(snapshot["stats_scope"], FakeRecorder.STATS_SCOPE)
        self.window.dialogs.show_settings()  # 「导出诊断」按钮在设置窗口里
        self.addCleanup(self.window.dialogs.settings.close)
        export_btn = self.window.dialogs.settings.export_btn
        tip = EXPORT_TIP.format(reports="reports")  # 诊断包所在的文件夹：开发版写 reports（发行版 app\reports，paths.shown）
        self.assertEqual(export_btn.toolTip(), tip_paragraphs_html(tip))
        self.assertEqual(tip_text(export_btn.toolTip()), tip)
        self.assertIn("保存为诊断包（reports 文件夹）", tip)
        self.assertIn("本局和上一局的识别记录与推荐日志", EXPORT_TIP)
        self.assertIn("对局结束后再点击也可以", EXPORT_TIP)

    def test_export_stats_reader_records_scope(self):
        """导出诊断时另读最近 12 小时的统计记录（stats_reader），经 extra 写成 stats.jsonl，范围写进 state.json 的
        stats_scope。"""
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        self.window.export_diagnostics()
        self.assertEqual(recorder.calls, ["flush", "collect", "collect_stats"])
        self.assertEqual(self.extras[0]["stats.jsonl"], FakeRecorder.STATS)
        self.assertEqual(self.exports[0][1]["stats_scope"], FakeRecorder.STATS_SCOPE)

    def test_export_stats_error_keeps_package(self):
        """统计记录读不出来：错误记录里有一条，诊断包照常导出，不带 stats.jsonl，stats_scope 里写着 error。"""
        log = capture_errors(self)
        recorder = FakeRecorder()

        def broken(hours=12.0):
            raise OSError("读不出来")

        recorder.collect_stats = broken
        self.session.eventlog.recorder = recorder
        self.window.export_diagnostics()
        self.assertEqual(len(self.exports), 1)
        self.assertEqual(self.exports[0][1]["stats_scope"]["error"], "OSError: 读不出来")
        self.assertNotIn("stats.jsonl", self.extras[0] or {})
        titles = [title for title, _ in log.entries()]
        self.assertTrue(any("统计记录" in title for title in titles), titles)

    def test_undo_does_not_carry_stats(self):
        """撤销误判时自动保存的那一份要快：不读统计记录，也不带 stats.jsonl。"""
        recorder = FakeRecorder()
        calls = []

        def collect_stats(hours=12.0):
            calls.append("stats")
            return [], {}

        recorder.collect_stats = collect_stats
        self.session.eventlog.recorder = recorder
        self.session.set_owned(["书签"])
        self.window.handle(self.session._auto_new_game("star", None, "星数由 2 星变为 0 星"))
        self.window.notices.buttons("newgame")[0].click()
        self.assertEqual(calls, [])
        self.assertNotIn("stats.jsonl", self.extras[0] or {})
        self.assertNotIn("stats_scope", self.exports[0][1])

    def test_export_lane_stats_read_in_background(self):
        """另一张的统计记录同样在后台那一步里读（先攒着 run_background 时还没读），原样交给诊断包的 lanes，
        范围写进 state.json 的 lanes.<端名>.stats_scope。"""
        calls = []
        lane_events = [{"time": "2026-10-03T13:00:07.000", "kind": "read:hud", "round": 1}]
        lane_stats = [{"time": "2026-10-03T13:00:07.000", "kind": "recog_stats", "period_s": 60.0}]
        lane_stats_scope = {"hours": 12.0, "since": "2026-10-03T13:00:07.000", "segments": 1, "lines": 1}

        def read():
            return lane_events, {"segments": [], "since": "2026-10-03T13:00:07.000"}

        def read_stats():
            calls.append("stats")
            return lane_stats, lane_stats_scope

        w = self.window
        pending = []
        w.run_background = pending.append
        w.background.export_diagnostics(lanes={"taptap": {"frame": None, "read": read, "stats": read_stats}})
        self.assertEqual(calls, [])
        pending[0]()
        self.assertEqual(calls, ["stats"])
        self.assertEqual(self.lanes[0]["taptap"]["stats"], lane_stats)
        self.assertEqual(self.exports[0][1]["lanes"]["taptap"]["stats_scope"], lane_stats_scope)

    def test_export_without_logbook_interface(self):
        """推荐日志模块没有 records_between（旧版）时不带推荐日志，统计记录照带，诊断包照常导出。"""
        w = self.window
        w.services = Services(logbook=SimpleNamespace(), diagnostics=self.services.diagnostics)
        self.session.eventlog.recorder = FakeRecorder()
        w.export_diagnostics()
        self.assertEqual(len(self.exports), 1)
        self.assertEqual(self.extras[0], {"stats.jsonl": FakeRecorder.STATS})

    def test_undo_record_uses_recorder(self):
        """撤销误判的新一局：自动保存的诊断包用同一份收集（撤销前的两段，含刚判定的新一局那一段）和推荐日志。"""
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        self.session.set_owned(["书签"])
        self.window.handle(self.session._auto_new_game("star", None, "星数由 2 星变为 0 星"))
        self.window.notices.buttons("newgame")[0].click()
        frame_bgr, snapshot, events, _ = self.exports[0]
        self.assertIsNone(frame_bgr)
        self.assertEqual(events, FakeRecorder.SEGMENTS)
        self.assertEqual(snapshot["export_scope"], FakeRecorder.SCOPE)
        self.assertIn("logbook.jsonl", self.extras[0])

    def test_records_flushed_when_paused(self):
        """切出游戏（游戏和悬浮窗都不在前台）或找不到游戏窗口时，识别记录写盘。"""
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        w = self.window
        w.hotkey_thread = FakeHotkey()
        w.follower.update_activity(probe("game"))
        self.assertEqual(recorder.calls, [])
        w.follower.update_activity(probe("other"))
        self.assertEqual(recorder.calls, ["flush"])
        w.follower.update_activity(probe(found=False))
        self.assertEqual(recorder.calls, ["flush", "flush"])

    def test_user_actions_traced(self):
        """用户在悬浮窗上的操作记进识别记录（read:user），复盘时能看出什么时候做了什么。"""
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.advice.open_fix(1)
        w.fix_box.setEditText("书签")
        w.advice.fix_confirm()
        w.add_owned("标记喷罐")
        w.remove_owned("标记喷罐")
        w.on_capture_changed(capture_state("screen", "blank"))
        w.notices.dismiss_capture()
        w.set_manual(True)
        w.set_opaque(True)
        users = [e for e in self.session.recent_events(600) if e["kind"] == "read:user"]
        self.assertEqual([e["action"] for e in users], ["fix", "add", "remove", "dismiss_capture", "mode", "opaque"])
        self.assertEqual((users[0]["slot"], users[0]["chip"], users[0]["applied"]), (1, "书签", True))
        self.assertEqual((users[3]["problem"], users[4]["manual"], users[5]["on"]), ("blank", True, True))
        self.assertFalse(hasattr(w, "set_allow_capture"))  # 「允许截图」已取消

    def test_export_carries_unread_frame(self):
        """后台识别留着最近一次三选一有筹码没读出来时的那一帧（Watcher.unread_shot，悬浮窗的 unread_source 现取）：「导出
        诊断」把它交给诊断包另存（select_unreadable）；还没有、取的时候出错、没接上时不带，诊断包照常导出。撤销误判时自动
        保存的那一份不含截图，有这一张也不带。"""
        w = self.window
        shot = (datetime(2026, 1, 2, 12, 0, 1), "读不全的那一帧")
        w.unread_source = lambda: shot
        w.export_diagnostics()
        self.assertEqual(self.unreads, [shot])
        self.assertEqual(self.exports[0][0], "画面")  # 当时的画面照旧

        def broken():
            raise RuntimeError("取不到")

        for source in (lambda: None, broken, None):
            w.unread_source = source
            w.export_diagnostics()
            self.assertIsNone(self.unreads[-1])
        self.assertEqual(len(self.exports), 4)
        self.assertIn("已导出诊断包：", w.notice_model.texts()["export"])
        w.unread_source = lambda: shot
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("star", None, "星数由 2 星变为 0 星"))
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(len(self.exports), 5)
        self.assertEqual((self.exports[-1][0], self.unreads[-1]), (None, None))  # 自动保存的：不含截图

    def test_export_carries_select_shots(self):
        """后台识别留着记下选中项用的那一帧、被临时盖住而没有采用的那一帧、对局中途像一局打完的那一帧（Watcher.pick_shot、
        covered_shot、finish_shot，悬浮窗的 pick_source、covered_source、finish_source 现取）：「导出诊断」把它们交给诊断包
        另存（select_picked、select_covered、finish_look）；还没有、取的时候出错、没接上的那一张不带，其余照常。撤销误判时
        自动保存的那一份不带。"""
        w = self.window
        picked = (datetime(2026, 1, 2, 12, 0, 1), "记下选中的那一帧", {"slot": 1, "chip": "标靶"})
        covered = (datetime(2026, 1, 2, 12, 0, 2), "被盖住的那一帧", {"slots": [2]})
        finish = (datetime(2026, 1, 2, 12, 0, 3), "对局中途像打完的一帧", {"look": "coral"})
        w.pick_source, w.covered_source, w.finish_source = (lambda: picked), (lambda: covered), (lambda: finish)
        w.export_diagnostics()
        self.assertEqual(self.shots, [(picked, covered)])
        self.assertEqual(self.finishes, [finish])
        self.assertIsNone(self.unreads[-1])

        def broken():
            raise RuntimeError("取不到")

        w.pick_source, w.covered_source, w.finish_source = broken, None, broken
        w.export_diagnostics()
        self.assertEqual(self.shots[-1], (None, None))
        self.assertIsNone(self.finishes[-1])
        w.pick_source, w.finish_source = lambda: picked, lambda: finish
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("star", None, "星数由 2 星变为 0 星"))
        w.notices.buttons("newgame")[0].click()
        self.assertEqual(len(self.exports), 3)
        self.assertEqual(self.shots[-1], (None, None))  # 自动保存的：不含截图
        self.assertIsNone(self.finishes[-1])

    def test_export_lanes_read_in_background(self):
        """另一张悬浮窗的识别记录在后台那一步里读（先攒着 run_background 时还没读），画面和识别记录原样交给
        diagnostics.export 的 lanes；它的导出范围写进 state.json 的 lanes.<端名>.export_scope。第一张的识别记录、推荐日志、
        导出范围照旧。"""
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        self.logbook.records = [{"t": "pick", "time": "2026-10-03T13:00:05"}]
        calls = []
        lane_events = [{"time": "2026-10-03T13:00:07.000", "kind": "read:hud", "round": 1}]
        lane_scope = {"segments": [{"file": "20261003-130000.jsonl"}], "since": "2026-10-03T13:00:07.000"}

        def read():
            calls.append("read")
            return lane_events, lane_scope

        w = self.window
        pending = []
        w.run_background = pending.append
        w.background.export_diagnostics(lanes={"taptap": {"frame": "第二张画面", "read": read}})
        self.assertEqual(calls, [])  # 还在界面线程里：没读
        pending[0]()
        self.assertEqual(calls, ["read"])
        _, snapshot, events, _ = self.exports[0]
        self.assertEqual(events, FakeRecorder.SEGMENTS)
        self.assertEqual(self.lanes[0], {"taptap": {"frame": "第二张画面", "events": lane_events, "stats": []}})
        self.assertEqual(snapshot["lanes"]["taptap"]["export_scope"], lane_scope)
        self.assertEqual(snapshot["lanes"]["taptap"]["stats_scope"],
                         {"hours": 12.0, "since": None, "segments": 0, "lines": 0, "source": "none"})
        self.assertEqual(snapshot["export_scope"], FakeRecorder.SCOPE)
        self.assertEqual(self.extras[0], {"logbook.jsonl": self.logbook.records, "stats.jsonl": FakeRecorder.STATS})
        self.assertEqual(self.logbook.between, [FakeRecorder.SCOPE["since"]])

    def test_export_lane_read_error_keeps_package(self):
        """另一张的识别记录读不出来：错误记录里有一条，诊断包照常导出，这一端的识别记录为空、export_scope 带 error；
        read 为 None 时当成空的、不报错。"""
        log = capture_errors(self)

        def broken():
            raise OSError("读不出来")

        w = self.window
        w.background.export_diagnostics(lanes={"taptap": {"frame": None, "read": broken}})
        self.assertEqual(len(self.exports), 1)
        _, snapshot, _, _ = self.exports[0]
        self.assertEqual(self.lanes[0], {"taptap": {"frame": None, "events": [], "stats": []}})
        self.assertEqual(snapshot["lanes"]["taptap"]["export_scope"],
                         {"segments": [], "since": None, "error": "OSError: 读不出来"})
        titles = [title for title, _ in log.entries()]
        self.assertTrue(any("第二张" in title and "识别记录" in title for title in titles), titles)

        log.clear()
        w.background.export_diagnostics(lanes={"taptap": {"frame": None, "read": None}})
        self.assertEqual(len(self.exports), 2)
        self.assertEqual(self.lanes[-1], {"taptap": {"frame": None, "events": [], "stats": []}})
        self.assertEqual(self.exports[-1][1]["lanes"]["taptap"]["export_scope"],
                         {"segments": [], "since": None})
        self.assertEqual(log.entries(), [])

    def test_export_without_lanes_unchanged(self):
        """不给另一张悬浮窗的东西（只有一张时）：diagnostics.export 收到的参数和现在一样，不带 lanes。"""
        w = self.window
        w.background.export_diagnostics()
        self.assertEqual(len(self.exports), 1)
        self.assertIsNone(self.lanes[0])
        w.background.export_diagnostics(lanes={})
        self.assertIsNone(self.lanes[-1])

    def test_events_reader_and_current_frame(self):
        """events_reader 在界面线程里当场取好记录器和界面处理过的事件：之后换掉记录器、再往界面的事件里加，读的还是那一份；
        current_frame 有画面时返回它，没接上、取的时候出错时返回 None。"""
        bg = self.window.background
        recorder = FakeRecorder()
        self.session.eventlog.recorder = recorder
        read = bg.events_reader()
        self.session.eventlog.recorder = None
        self.assertEqual(read(), (FakeRecorder.SEGMENTS, FakeRecorder.SCOPE))
        self.assertEqual(recorder.calls, ["flush", "collect"])

        self.session.recent_events = lambda seconds: []
        bg.live.recent.append({"kind": "before", "time": "2026-10-03T13:00:01.000"})
        read = bg.events_reader()
        bg.live.recent.append({"kind": "after", "time": "2026-10-03T13:00:02.000"})
        self.assertEqual(read()[0], [{"kind": "before", "time": "2026-10-03T13:00:01.000"}])

        self.assertEqual(bg.current_frame(), "画面")
        with mock.patch.object(bg, "frame_source", return_value=None):
            self.assertIsNone(bg.current_frame())
        with mock.patch.object(bg, "frame_source", side_effect=RuntimeError("取不到")):
            self.assertIsNone(bg.current_frame())

        def broken():
            raise OSError("取不到")

        with mock.patch.object(bg, "frame_source", return_value=broken):
            self.assertIsNone(bg.current_frame())

    def test_export_unavailable(self):
        w = Overlay(self.db, self.session, threading.Lock())
        w.export_diagnostics()
        self.assertIn("导出诊断不可用", w.status.status_text())

    def test_export_frame_by_capture_method(self):
        """导出诊断的原图用后台识别最近取到的一帧。没有时：直接截取下不另截，诊断包里不带原图；截取屏幕下照旧现截一张
        （和后台识别一样经取帧入口贴补悬浮窗遮挡的区域）。对局状态里记截取方式和窗口层次，不记「允许截图」。"""
        w = self.window
        w.frame_source = lambda: None
        for method, want in (("window", None), ("screen", "现截")):
            with self.subTest(method=method):
                self.exports.clear()
                if method == "window":
                    w.on_capture_changed(capture_state("window"))
                else:
                    w.on_capture_changed(capture_state("screen", "failed", "0x887A0005", step="CreateForWindow"))
                calls = []
                with mock.patch("advisor.ui.overlay_parts.background._grab_screen",
                                side_effect=lambda: calls.append(1) or "现截"):
                    w.export_diagnostics()
                self.assertEqual(len(calls), 0 if method == "window" else 1)
                frame_bgr, snapshot, _, _ = self.exports[0]
                self.assertEqual(frame_bgr, want)
                state = snapshot["overlay"]
                self.assertNotIn("allow_capture", state)
                self.assertEqual(state["capture"]["method"], method)
                if method == "screen":
                    self.assertEqual((state["capture"]["problem"], state["capture"]["detail"], state["capture"]["step"]),
                                     ("failed", "0x887A0005", "CreateForWindow"))
                for key in ("foreground", "layer", "paused"):
                    self.assertIn(key, state)

    def test_download_progress_failure_and_retry(self):
        w = self.window
        self.assertTrue(w.check_assets())
        items, during, stopped, paused = self.downloads[0]
        self.assertEqual(items, self.missing)
        self.assertEqual(during, "正在下载识别所需的图片（1/2）")
        self.assertFalse(stopped)
        # 只补两张：下载期间也不暂停识别（要下的多时见下一条），下完照旧不暂停
        self.assertIs(paused, False)
        self.assertEqual(w.watcher.paused, {"assets": False})
        self.assertEqual(w.watcher.reloaded, 1)
        self.assertIn("1 张下载失败", w.notice_model.texts()["assets"])
        self.assertEqual([b.text() for b in w.notices.buttons("assets")], ["重试"])
        self.missing = ["heroes/b.png"]
        w.notices.buttons("assets")[0].click()
        self.assertEqual(self.downloads[1][0], ["heroes/b.png"])

    def test_download_logs_stats(self):
        """下完把这一轮的用时记进识别记录（download_stats）：下载模块填的各项原样带上，另加这一轮的模式和是不是点「重试」
        开始的；没有识别记录器时不写，也不出错。"""
        added = []

        class Recorder(FakeRecorder):
            def add(self, t, record):
                added.append(dict(record))

        self.session.eventlog.recorder = Recorder()

        def download(items, progress, stop, stats=None):
            stats.update({
                "seconds": 12.3, "total": 2, "ok": 1, "failed": 1, "blocked": False, "paused": False,
                "threads": 6, "slow": False,
                "slowest": [{"name": "heroes/a.png", "s": 3.2, "bytes": 120}],
            })
            return 1, [(items[1], "网络错误：测试")]

        self.services.assets.download = download
        w = self.window
        w.check_assets()
        record = added[-1]
        self.assertEqual(record["kind"], "download_stats")
        for key, value in (("seconds", 12.3), ("total", 2), ("ok", 1), ("failed", 1), ("mode", "few"),
                           ("retry", False), ("blocked", False), ("paused", False), ("threads", 6), ("slow", False)):
            self.assertEqual(record[key], value, record)
        self.assertEqual(record["slowest"], [{"name": "heroes/a.png", "s": 3.2, "bytes": 120}])
        self.assertNotIn("url", str(record))
        # 点「重试」开始的这一轮记下 retry
        w.notices.buttons("assets")[0].click()
        self.assertTrue(added[-1]["retry"])
        # 没有识别记录器：不写，也不出错
        self.session.eventlog.recorder = None
        before = len(added)
        w.check_assets()
        self.assertEqual(len(added), before)

    def test_download_blocks_recognition(self):
        """要下的多（多于 DOWNLOAD_PAUSE_OVER 张，第一次运行）：下载期间识别暂停，摘要写「识别 暂不可用」；下完恢复。"""
        w = self.window
        seen = []
        self.missing = [f"heroes/{i}.png" for i in range(DOWNLOAD_PAUSE_OVER + 1)]

        def download(items, progress, stop, stats=None):
            seen.append((w.background.blocked, w.watcher.paused.get("assets"), BLOCKED_BIT in w.status.summary_plain))
            return (len(items), [])

        self.services.assets.download = download
        w.check_assets()
        self.assertEqual(seen, [(True, True, True)])
        self.assertNotIn("assets", w.notice_model.texts())
        self.assertIn("识别所需的图片已下载完成", w.status.status_text())

    def test_no_missing_assets(self):
        self.missing = []
        self.assertFalse(self.window.check_assets())
        self.assertEqual(self.downloads, [])

    def test_icons_redrawn_after_download(self):
        """第一次启动下载完之前画过的筹码图标（缺图时的稀有度色圈），下载好以后换成真的图标，不用重启：
        widgets.chip_icon 记着画的时候缺了哪几个图片文件，文件后来到了再取时重画；还缺着时照旧用画好的，图片都在的
        不再查文件。下载完时悬浮窗整个重画（图标格子、圆形图标重画时按 chip_icon 取图），编辑方案窗口开着时筹码列表
        各行的图标重做。"""
        chip = self.db.chips["标靶"]
        real = app_paths.download_dir("chips")
        files = [real / ui_widgets.RARITY_BASE[chip.rarity], real / f"UT_Relic_{chip.icon}.png"]
        if not all(f.exists() for f in files):
            self.skipTest("没有下载好的筹码图标（assets\\chips）")
        folder = sandbox.temp_dir(self, "test-icons-")
        (folder / "chips").mkdir()
        with mock.patch.dict(ui_widgets._icon_cache, clear=True), \
                mock.patch.object(ui_widgets, "download_dir", lambda kind: folder / kind):
            first = chip_icon(chip.name, chip.rarity, chip.icon, 40)
            self.assertIs(chip_icon(chip.name, chip.rarity, chip.icon, 40), first)  # 还缺着：不重画
            for f in files:
                shutil.copy2(f, folder / "chips" / f.name)
            second = chip_icon(chip.name, chip.rarity, chip.icon, 40)
            self.assertIsNot(second, first)
            self.assertNotEqual(second, first)
            ui_widgets._icon_cache.clear()
            self.assertEqual(chip_icon(chip.name, chip.rarity, chip.icon, 40), second)  # 和一开始就有图片时画的一样
            again = chip_icon(chip.name, chip.rarity, chip.icon, 40)
            with mock.patch.object(Path, "exists", side_effect=AssertionError("图片都在时不该再查文件")):
                self.assertIs(chip_icon(chip.name, chip.rarity, chip.icon, 40), again)
        # 下载完：悬浮窗整个重画，开着的编辑方案窗口里筹码列表的图标重做
        w = self.window
        calls = []
        editor = QWidget()
        self.addCleanup(editor.deleteLater)
        editor.chip_list = SimpleNamespace(refresh_icons=lambda: calls.append("list"))
        w.dialogs.editor = editor
        with mock.patch.object(w, "update", side_effect=lambda *a: calls.append("update")):
            w.check_assets()
        self.assertIn("update", calls)
        self.assertEqual(calls.count("list"), 1)
        w.dialogs.editor = None
        calls.clear()
        with mock.patch.object(w, "update", side_effect=lambda *a: calls.append("update")):
            w._download_finished(2, [])  # 没有编辑方案窗口时只重画悬浮窗
        self.assertEqual(calls.count("list"), 0)
        self.assertIn("update", calls)

    def test_flash_timer_tied_to_window(self):
        """一次性提示（_flash）的计时器挂在悬浮窗上：到点时收起提示；悬浮窗在这之前删掉了（例如离屏画完就删的悬浮窗）
        时不再收起，不去碰已经删掉的部件，不报错。"""
        w = self.window
        w._flash("一次性提示", ms=20)
        self.assertEqual(w.message.text, "一次性提示")
        self.assertTrue(wait_until(lambda: w.message.text != "一次性提示"))
        gone = Overlay(self.db, self.session, threading.Lock(), services=self.services)
        gone._flash("一次性提示", ms=20)
        later = []  # 比它晚定、时长是它 5 倍的计时器：这个到点了，悬浮窗的那个要是还在也早该到点了
        QTimer.singleShot(100, lambda: later.append(1))
        called, errors = [], []
        real = Overlay._unflash
        # 只数删掉的那个：前面几个测试留下的悬浮窗的一次性提示（15 秒）可能正好在这时到点
        with mock.patch.object(Overlay, "_unflash",
                               lambda self, seq, text: (self is gone and called.append(self), real(self, seq, text))), \
                mock.patch.object(sys, "excepthook", lambda *a: errors.append(a)):
            gone.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertTrue(wait_until(lambda: later))
            APP.processEvents()
        self.assertFalse(shiboken6.isValid(gone))
        self.assertEqual(called, [])
        self.assertEqual(errors, [])

    def test_about(self):
        w = self.window
        w.show_about()
        text = w.dialogs.about.text()
        for part in ("版本", "拾星助手 1.0.0", "资料来源与授权", "https://example.org/wiki"):
            self.assertIn(part, text)
        # 网址链接和其他可点击元素一样有底色
        from advisor.ui.text import LINK_STYLE, linkify

        self.assertIn(f'style="{LINK_STYLE}"', linkify("见 https://example.org/wiki 。"))
        self.assertIn("background-color", LINK_STYLE)
        w.dialogs.about.close()
        bare = Overlay(self.db, self.session, threading.Lock())
        bare.show_about()
        self.assertIn("版本信息不可用", bare.dialogs.about.text())
        bare.dialogs.about.close()

    def test_close_to_tray(self):
        """×：收到托盘。每次运行第一次时贴着托盘图标弹一个自己画的小提示框（TrayToast）：不用 Windows 的系统通知
        （showMessage 一次都不调用：系统通知会放提示音），文字照旧；不抢焦点、不进任务栏、总在最前，约 4 秒后自己消失，点一下
        也消失。第二次点 × 不再弹。"""
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest

        from advisor.ui.overlay_parts.wording import TRAY_TOAST, TRAY_TOAST_TITLE
        from advisor.ui.tray_toast import TOAST_MS

        w = self.window
        messages = []
        w.tray_part.tray = SimpleNamespace(showMessage=lambda *a: messages.append(a), setToolTip=lambda t: None,
                                           geometry=lambda: QRect())  # 拿不到托盘图标的位置
        w.show()
        w.tray_part.close_clicked()
        self.assertTrue(w.isHidden())
        toast = w.tray_part.toast
        self.addCleanup(toast.hide)
        self.assertTrue(toast.isVisible())
        self.assertEqual((toast.title.text(), toast.text.text()), (TRAY_TOAST_TITLE, TRAY_TOAST))
        self.assertEqual(TRAY_TOAST, "已收到系统托盘，识别继续进行。点击托盘图标可重新显示悬浮窗。")
        flags = toast.windowFlags()
        for flag in (Qt.Tool, Qt.FramelessWindowHint, Qt.WindowStaysOnTopHint, Qt.WindowDoesNotAcceptFocus):
            self.assertTrue(flags & flag, flag)
        self.assertTrue(toast.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertTrue(toast.timer.isActive())
        self.assertEqual((toast.timer.interval(), TOAST_MS), (4000, 4000))
        w.tray_part.close_clicked()
        self.assertEqual(messages, [])  # 不用系统通知
        self.assertIs(w.tray_part.toast, toast)
        QTest.mouseClick(toast, Qt.LeftButton)  # 点一下就收起
        self.assertFalse(toast.isVisible())
        w.tray_part.show_from_tray()
        self.assertFalse(w.isHidden())
        w.tray_part.close_clicked()  # 这次运行已经提示过：不再弹
        self.assertFalse(toast.isVisible())
        w.hide()

    def test_toast_follows_skin_colors(self):
        """小提示框的颜色照当前皮肤悬浮窗的悬停说明（底色、字、框线），每次弹出时现取。"""
        from advisor.ui.theme import OVERLAY as O
        from advisor.ui.tray_toast import TrayToast

        toast = TrayToast()
        self.addCleanup(toast.deleteLater)
        toast.popup("拾星助手", "一句话")
        self.addCleanup(toast.hide)
        sheet = toast.frame.styleSheet()
        for color in (O.TIP_BG, O.TIP_TEXT, O.TIP_BORDER):
            self.assertIn(color, sheet)
        toast.popup("", "只有正文")
        self.assertTrue(toast.title.isHidden())

    def test_toast_position(self):
        """提示框贴着托盘图标：在图标上方、横向对着图标的中心，挪进那块屏幕的可用区域（任务栏在下沿、上沿、左边）；图标的
        位置拿不到、不在任何一块屏幕上时放在主屏幕可用区域的右下角。"""
        from PySide6.QtCore import QPoint, QRect, QSize

        from advisor.ui.tray_toast import TOAST_GAP, toast_position

        size = QSize(300, 60)
        full, avail = QRect(0, 0, 1920, 1080), QRect(0, 0, 1920, 1040)  # 任务栏在下沿，高 40
        screens = [(full, avail)]
        icon = QRect(1700, 1050, 24, 24)
        pos = toast_position(size, icon, screens, avail)
        self.assertEqual(pos, QPoint(icon.center().x() - 150, 1040 - 60 - TOAST_GAP))  # QRect 的中心按 Qt 的算法（1711）
        right = QRect(1900, 1050, 24, 24)  # 靠右的图标：挪进可用区域
        self.assertEqual(toast_position(size, right, screens, avail).x(), 1920 - 300 - TOAST_GAP)
        top_avail = QRect(0, 40, 1920, 1040)  # 任务栏在上沿
        self.assertEqual(toast_position(size, QRect(1700, 8, 24, 24), [(full, top_avail)], top_avail).y(),
                         40 + TOAST_GAP)
        left_avail = QRect(60, 0, 1860, 1080)  # 任务栏在左边
        self.assertEqual(toast_position(size, QRect(10, 1000, 24, 24), [(full, left_avail)], left_avail).x(),
                         60 + TOAST_GAP)
        corner = QPoint(1920 - 300 - TOAST_GAP, 1040 - 60 - TOAST_GAP)
        for missing in (None, QRect(), QRect(5000, 5000, 24, 24)):
            with self.subTest(icon=missing):
                self.assertEqual(toast_position(size, missing, screens, avail), corner)

    def test_tray_menu(self):
        """托盘菜单：「显示」「重置悬浮窗」「设置」「导出诊断」「打开作业站」，隔一条线「退出」。「设置」「导出诊断」先把
        收在托盘里的悬浮窗显示出来（同「显示」），再打开设置窗口、导出诊断（结果照旧写在悬浮窗上）；「打开作业站」打开首页。"""
        from PySide6.QtWidgets import QSystemTrayIcon

        from advisor import hub
        self.enterContext(mock.patch.object(hub, "ENABLED", True))
        w = self.window
        icon = SimpleNamespace(setContextMenu=lambda m: None, activated=SimpleNamespace(connect=lambda f: None),
                               setToolTip=lambda t: None, show=lambda: None)
        w.tray_part.make_icon = lambda: icon
        self.addCleanup(APP.setQuitOnLastWindowClosed, APP.quitOnLastWindowClosed())  # setup_tray 会改它
        with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            self.assertTrue(w.tray_part.setup_tray())
        self.addCleanup(setattr, w.tray_part, "tray", None)
        actions = w.tray_part.menu.actions()
        self.assertEqual([a.text() if not a.isSeparator() else None for a in actions],
                         ["显示", "重置悬浮窗", "设置", "导出诊断", "打开作业站", None, "退出"])
        w.hide()
        actions[2].trigger()  # 设置
        self.assertFalse(w.isHidden())
        self.assertTrue(w.dialogs.settings.isVisible())
        w.dialogs.settings.close()
        w.hide()
        actions[3].trigger()  # 导出诊断
        self.assertFalse(w.isHidden())
        self.assertEqual(len(self.exports), 1)
        self.assertIn("已导出诊断包：", w.notice_model.texts()["export"])
        w.hide()
        opened = []
        w.open_url = lambda url: opened.append(url) or True
        actions[4].trigger()  # 打开作业站
        self.assertEqual(opened, ["https://astral-chip-compass.com/app/"])
        self.assertTrue(w.isHidden())  # 打开网址不显示悬浮窗

    def test_open_manual(self):
        """悬浮窗打开使用说明（open_manual）：用系统默认的程序打开（不显示悬浮窗）；文件不在时显示悬浮窗、提示重新解压；
        系统打不开时提示去文件夹里直接打开。识别记录记一条 manual。"""
        from advisor.ui import overlay as overlay_mod

        w = self.window
        folder = sandbox.temp_dir(self, "manual-")
        page = folder / "使用说明.html"
        page.write_text("<p>使用说明</p>", encoding="utf-8")
        opened = []
        with mock.patch.object(app_paths, "manual_path", return_value=page), \
                mock.patch.object(overlay_mod, "open_with_system", side_effect=lambda p: opened.append(p) or True):
            w.hide()
            w.open_manual()
            self.assertEqual(opened, [page])
            self.assertTrue(w.isHidden())  # 打开了：不显示悬浮窗
        missing = folder / "不在" / "使用说明.html"
        with mock.patch.object(app_paths, "manual_path", return_value=missing), \
                mock.patch.object(overlay_mod, "open_with_system", side_effect=AssertionError("不该打开")):
            w.open_manual()
        self.assertFalse(w.isHidden())
        self.assertEqual(w.message.text, "找不到「使用说明.html」：请把拾星助手的压缩包整个重新解压。")
        with mock.patch.object(app_paths, "manual_path", return_value=page), \
                mock.patch.object(overlay_mod, "open_with_system", return_value=False):
            w.open_manual()
        self.assertEqual(w.message.text, "无法打开「使用说明.html」：请在拾星助手的文件夹中直接双击打开它。")
        records = [(e.get("ok"), e.get("missing")) for e in self.session.recent_events(600)
                   if e.get("kind") == "read:user" and e.get("action") == "manual"]
        self.assertEqual(records, [(True, None), (False, True), (False, False)])
        w.hide()

    def test_manual_path(self):
        """「使用说明」打开哪一个：发行版是程序文件夹（解压出来的那一层）里的「使用说明.html」，文件名和打包时生成的同一个；
        开发版是项目里的原稿 docs/使用说明.md（开发版没有生成好的网页）。"""
        source = (PROJECT / "tools" / "build_release.py").read_text(encoding="utf-8")
        self.assertIn("MANUAL_NAME = paths.MANUAL_NAME", source)  # 打包生成的和悬浮窗打开的（open_manual）是同一个文件名
        self.assertEqual(app_paths.MANUAL_NAME, "使用说明.html")
        self.assertEqual(app_paths.manual_path(), app_paths.app_dir() / "docs" / "使用说明.md")
        self.assertTrue(app_paths.manual_path().is_file())
        with mock.patch.object(app_paths, "is_frozen", return_value=True), \
                mock.patch.object(app_paths, "app_dir", return_value=Path("C:/解压的地方/AstralChipCompass/app")):
            self.assertEqual(app_paths.manual_path(), Path("C:/解压的地方/AstralChipCompass/使用说明.html"))


class QuietTest(unittest.TestCase):
    """收到托盘的提示不出声：程序代码（advisor 下）里不调用 QSystemTrayIcon.showMessage（Windows 10、11 弹系统通知时放
    提示音）、MessageBeep、QApplication.beep。启动失败的弹窗（MessageBoxW 的错误图标）照旧，不在这里管。"""

    def test_no_sound_calls(self):
        import ast

        calls = []
        for path in sorted((PROJECT / "advisor").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name in ("showMessage", "MessageBeep", "beep"):
                        calls.append(f"{path.relative_to(PROJECT).as_posix()}:{node.lineno} {name}")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
