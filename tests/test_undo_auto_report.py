"""撤销误判时自动保存的诊断包（advisor/ui/overlay_parts/background.py 的 Background.save_undo_record）：

撤销新的一局的判定（误判）时自动存的诊断包要带自动保存的标记（diagnostics.export 的 auto=True），存好以后按份数清理
（prune_auto_reports），把刚存的那一份作为 spare 传进去（系统时间被调回过时它名字最旧，不传会被当成最旧的删掉）；
存不成时不清理、也不另加提示。手动「导出诊断」照旧不带标记、不清理。自动保存的不含截图：当时的画面、三选一读不全时的那一帧、
记下选中项用的那一帧、被临时盖住而没有采用的那一帧都不带（手动导出带上）。

用 Qt 的 offscreen 平台，不会弹出窗口；diagnostics 用假对象，不写文件。
"""
import threading
import unittest
from datetime import datetime

from advisor.data import load_db  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import UNDONE_SAVED
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services  # noqa: E402
from tests.support import qtenv  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402

APP = qtenv.app()

REPORT = "reports/20261010-120000"


class FakeWatcher:
    def set_paused(self, reason, on, why=""):
        pass

    def reload(self):
        pass


class RecordingDiagnostics:
    """诊断模块的假对象：记下每次 export 收到的 auto、每次 prune_auto_reports 收到的 spare；fail 时 export 抛出。"""

    LOGBOOK = "logbook.jsonl"

    def __init__(self):
        self.exports: list[dict] = []
        self.prunes: list = []
        self.fail = False

    def export(self, frame, snapshot, events, text, extra=None, capture=None, auto=False, select_unreadable=None,
               select_picked=None, select_covered=None):
        self.exports.append({"frame": frame, "snapshot": snapshot, "auto": auto, "unread": select_unreadable,
                             "picked": select_picked, "covered": select_covered})
        if self.fail:
            raise RuntimeError("存不成")
        return REPORT

    def prune_auto_reports(self, keep=20, spare=None):
        self.prunes.append(spare)
        return []


class UndoAutoReportTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.logbook = FakeLogbook()
        self.diagnostics = RecordingDiagnostics()
        self.services = Services(logbook=self.logbook, diagnostics=self.diagnostics)
        self.window = Overlay(self.db, self.session, threading.Lock(), services=self.services,
                              frame_source=lambda: "画面")
        self.window.run_background = lambda fn: fn()
        self.window.watcher = FakeWatcher()
        self.addCleanup(self.window.hide)

    def undo(self) -> None:
        """走一遍撤销误判：判一个新的一局，再点「撤销」。"""
        w = self.window
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        w.notices.buttons("newgame")[0].click()

    def test_undo_export_auto_and_prune_spares_it(self):
        """撤销时 export 收到 auto=True，随后 prune_auto_reports 收到 spare=刚存的那一份。"""
        self.undo()
        self.assertEqual([e["auto"] for e in self.diagnostics.exports], [True])
        self.assertEqual(self.diagnostics.prunes, [REPORT])
        self.assertIn(UNDONE_SAVED.split("{path}")[0], self.window.status.status_text())

    def test_undo_export_failure_no_prune_no_notice(self):
        """导出失败时不清理、不另加提示（撤销本身照常完成）。"""
        self.diagnostics.fail = True
        self.undo()
        self.assertEqual(self.diagnostics.prunes, [])
        self.assertIsNone(self.window.notice_model.export_path)
        self.assertNotIn("export", self.window.notice_model.texts())
        self.assertNotIn(UNDONE_SAVED.split("{path}")[0], self.window.status.status_text())

    def test_manual_export_has_no_auto_and_no_prune(self):
        """手动的「导出诊断」照旧不带自动保存的标记，也不按份数清理。"""
        self.window.export_diagnostics()
        self.assertEqual([e["auto"] for e in self.diagnostics.exports], [False])
        self.assertEqual(self.diagnostics.prunes, [])

    def test_unread_frame_only_in_manual_export(self):
        """后台识别留着三选一读不全时的那一帧：自动保存的这一份不含截图，不带它（也不带当时的画面）；手动导出带上。"""
        shot = (datetime(2026, 1, 2, 12, 0, 1), "读不全的那一帧")
        self.window.unread_source = lambda: shot
        self.undo()
        self.window.export_diagnostics()
        self.assertEqual([(e["auto"], e["frame"], e["unread"]) for e in self.diagnostics.exports],
                         [(True, None, None), (False, "画面", shot)])

    def test_select_shots_only_in_manual_export(self):
        """记下选中项用的那一帧、被临时盖住而没有采用的那一帧：同样只在手动导出时带上，自动保存的不带。"""
        picked = (datetime(2026, 1, 2, 12, 0, 1), "记下选中的那一帧", {"slot": 0, "chip": "书签"})
        covered = (datetime(2026, 1, 2, 12, 0, 2), "被盖住的那一帧", {"slots": [1]})
        self.window.pick_source, self.window.covered_source = (lambda: picked), (lambda: covered)
        self.undo()
        self.window.export_diagnostics()
        self.assertEqual([(e["auto"], e["picked"], e["covered"]) for e in self.diagnostics.exports],
                         [(True, None, None), (False, picked, covered)])


if __name__ == "__main__":
    unittest.main()
