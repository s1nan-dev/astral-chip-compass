"""悬浮窗不常走的路径（出错、并发、后台）：

- 查看游戏窗口出错（Win32 调用失败等）：这一次不算数，不当成找不到游戏窗口（不暂停识别、不提示「未找到游戏窗口」），
  错误记进错误记录。
- 刷新时一块出错：只记进错误记录，其余几块照画；之后不出错了照常画回来。
- 事件种类：Session 和识别流程发的每一种都在悬浮窗认识的种类（EVENT_KINDS）里；不认识的种类只刷新、记一笔。
- 导出诊断：识别记录的写盘和读回放在后台线程里（界面线程上一次都不调用）；正在写诊断包时撤销了误判的新一局，撤销时的
  那一份排着，等前一份写完接着存（不直接跳过）。后台任务中途抛出时照样告诉界面（按失败提示，「导出诊断」又能用，
  排着的照存），错误记进错误记录。
- 「需要您操作」：needs_attention 和边框用同一个判断。

除了拨假时钟、换假的推荐日志和诊断模块，只用公开探针；离屏、不找真实的游戏窗口、不写用户的文件。
"""
from __future__ import annotations

import ast
import threading
import unittest
from pathlib import Path
from unittest import mock

from advisor.data import load_db
from advisor.session import Event
from advisor.ui.overlay_parts import owned_card
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.dispatch import EVENT_KINDS
from advisor.ui.overlay_parts.wording import NO_GAME, UNDONE_SAVED
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services
from advisor.ui.winapi import GameProbe
from tests.support import qtenv, sandbox
from tests.support.errors import capture_errors
from tests.support.waits import wait_until
from tests.test_overlay_behavior import OFFER, BehaviorBase

APP = qtenv.app()
ROOT = Path(__file__).resolve().parents[1]
RECT = (100, 50, 1600, 900)


class FakeWatcher:
    def __init__(self):
        self.paused: dict = {}

    def set_paused(self, reason, on, why=""):
        self.paused[reason] = on

    def reload(self):
        pass


class FakeDiagnostics:
    """诊断模块的假对象：记下每次 export 收到的东西，返回一个沙箱里的文件夹（不写文件）。"""

    LOGBOOK = "logbook.jsonl"

    def __init__(self, folder: Path):
        self.folder = folder
        self.exports: list[dict] = []

    def export(self, frame, snapshot, events, text, extra=None, capture=None, auto=False):
        self.exports.append({"snapshot": snapshot, "events": events, "text": text, "auto": auto})
        return self.folder / f"诊断包{len(self.exports)}"


class FlakyDiagnostics(FakeDiagnostics):
    """第一次取推荐日志的文件名时出错（导出诊断的后台任务中途抛出），之后正常。"""

    def __init__(self, folder: Path):
        super().__init__(folder)
        self.failures = 1

    @property
    def LOGBOOK(self):  # noqa: N802  同 diagnostics.LOGBOOK
        if self.failures:
            self.failures -= 1
            raise RuntimeError("取不到推荐日志的文件名")
        return "logbook.jsonl"


class FakeRecorder:
    """识别记录器的假对象：flush、collect 记下是哪个线程调用的；Session 写记录、换段的几个方法只接住调用。"""

    SCOPE = {"segments": [{"file": "20261010-120000.jsonl", "reason": "startup"}], "since": "2026-10-10T12:00:00.000"}

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def flush(self):
        self.calls.append(("flush", threading.get_ident()))
        return True

    def collect(self):
        self.calls.append(("collect", threading.get_ident()))
        return [{"time": "2026-10-10T12:00:00.000", "kind": "segment"}], dict(self.SCOPE)

    def collect_stats(self, hours=12.0):
        """导出诊断时连带读最近 hours 小时的统计记录（gamelog.GameRecorder.collect_stats）：交回空的，不记进 calls。"""
        return [], {"hours": hours, "since": None, "segments": 0, "lines": 0}

    def close(self):
        pass

    def add(self, t, record):
        pass

    def new_segment(self, reason, **info):
        pass

    def undo_segment(self):
        return True

    def mark(self, kind, **info):
        pass


class ProbeErrorTest(unittest.TestCase):
    def test_probe_error_keeps_last_state(self):
        errors = capture_errors(self)
        db = load_db()
        w = Overlay(db, OverlaySession(db, UiSettings(hero="凛")), threading.Lock(), services=Services())
        self.addCleanup(w.hide)
        w.watcher = FakeWatcher()
        w.probe = lambda: GameProbe(True, RECT, False, True, False, hwnd=0x1001)
        w.follower.poll()
        before = (w.pos(), w.status.summary_plain, w.paused)

        def broken():
            raise OSError("[WinError 1400] 无效的窗口句柄。")

        w.probe = broken
        for _ in range(3):
            w.follower.poll()
        self.assertNotIn(NO_GAME, w.status.status_text())
        self.assertEqual((w.pos(), w.status.summary_plain, w.paused), before)
        self.assertFalse(w.watcher.paused.get("focus"))  # 识别照常进行
        titles = [t for t, _ in errors.entries()]
        self.assertEqual(titles, ["查看游戏窗口的位置和前台状态出错（拾星助手仍在运行）"])  # 同一个错只整条记一次


class RefreshGuardTest(BehaviorBase):
    def test_one_block_failing_does_not_stop_others(self):
        """「已有筹码」那一块出错：别的几块照画（轮次跟着变），错误记进错误记录；不出错以后已有筹码再变时照常画。"""
        errors = capture_errors(self)
        self.enter(rnd=3)
        self.panel(["星币锤"])
        with mock.patch.object(owned_card, "chip_tip", side_effect=RuntimeError("画不出来")):
            self.panel(["星币锤", "维生素药丸"])  # 已有筹码变了：「已有筹码」那一块要重画，出错
            self.hud(4)
            self.hud(4)
        self.assertIn("第 4 轮", self.w.status.status_text())  # 状态胶囊照画
        self.assertIn("第 4 轮", self.w.status.summary_tip_text)  # 摘要的悬停说明照画（摘要这时是面板读到的一次性提示）
        titles = {t for t, _ in errors.entries()}
        self.assertEqual(titles, {"刷新悬浮窗的「已有筹码」出错（拾星助手仍在运行）"})
        # 测试沙箱按这个标题认出漏接的分块刷新出错（退出码 96）：标题改了写法，那边要一起改
        self.assertTrue(all(sandbox.REFRESH_TITLE.match(t) for t in titles))
        errors.clear()
        self.panel(["星币锤", "维生素药丸", "摩托头盔-中级"])
        self.w.set_expanded(True)
        owned = "\n".join(self.texts("owned"))
        for name in ("星币锤", "维生素药丸", "摩托头盔-中级"):
            self.assertIn(name, owned)
        self.assertEqual(errors.entries(), [])


class EventKindsTest(BehaviorBase):
    @staticmethod
    def emitted_kinds(path: Path) -> set[str]:
        """一个模块里建的事件的种类：Event("种类", ...)、Event(kind="种类", ...)，以及 Session 带序号建事件的
        self._event("种类", ...)。"""
        found = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(node.func, "id", getattr(node.func, "attr", None)) if isinstance(node, ast.Call) else None
            if name in ("Event", "_event"):
                args = [*node.args[:1], *(k.value for k in node.keywords if k.arg == "kind")]
                found |= {a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        return found

    def test_all_emitted_kinds_known(self):
        # 记账是一个包（advisor/session/），各模块里建的事件都算
        session = set().union(*(self.emitted_kinds(p) for p in sorted((ROOT / "advisor" / "session").glob("*.py"))))
        pipeline = self.emitted_kinds(ROOT / "advisor" / "vision" / "pipeline.py")
        self.assertGreaterEqual(len(session), 15)
        self.assertEqual((session | pipeline) - EVENT_KINDS, set())

    def test_unknown_kind_refreshes_and_logs(self):
        errors = capture_errors(self)
        self.hud(3)
        self.hud(3)
        with self.w.lock:
            self.session.settings.round = 5  # 识别线程改了状态，跟着来的是一个悬浮窗还不认识的事件
        self.w.handle(Event("new_kind_from_session"))
        self.assertIn("第 5 轮", self.w.status.status_text())
        self.assertEqual([t for t, _ in errors.entries()],
                         ["收到不认识的事件种类「new_kind_from_session」，只刷新悬浮窗（拾星助手仍在运行）"])


class ExportTest(BehaviorBase):
    def setUp(self):
        super().setUp()
        self.diagnostics = FakeDiagnostics(sandbox.temp_dir(self, "export-"))
        self.w.services.diagnostics = self.diagnostics
        self.pending: list = []
        self.w.run_background = self.pending.append  # 后台线程的活先排着，测试里一件件做

    def run_pending(self) -> None:
        while self.pending:
            self.pending.pop(0)()

    def test_records_read_in_background(self):
        """点「导出诊断」时界面线程上不写盘、不读识别记录：都在交给后台线程的那件活里做。"""
        recorder = self.session.eventlog.recorder = FakeRecorder()
        self.enter()
        self.offer()
        self.w.export_diagnostics()
        self.assertEqual(recorder.calls, [])
        self.assertIn("正在导出诊断包…", self.w.status.status_text())
        worker = threading.Thread(target=self.run_pending)
        worker.start()
        worker.join(30)
        self.assertEqual([name for name, _ in recorder.calls], ["flush", "collect"])
        self.assertTrue(all(ident == worker.ident for _, ident in recorder.calls))
        self.assertEqual(len(self.diagnostics.exports), 1)
        self.assertEqual(self.diagnostics.exports[0]["snapshot"]["export_scope"], FakeRecorder.SCOPE)
        self.assertTrue(wait_until(lambda: "正在导出诊断包…" not in self.w.status.status_text()))  # 写完的信号送到界面线程

    def test_undo_while_exporting_is_saved_after(self):
        """正在写诊断包时撤销了对局中途误判的新一局：撤销时的那一份等前一份写完接着存，不丢。"""
        self.enter(rnd=8, star=2)
        self.pick_first()
        self.advance(30)
        self.hud(1)
        self.hud(1)  # 第 8 轮 → 第 1 轮：判定为新的一局（可以撤销）
        self.w.export_diagnostics()
        self.w.notices.buttons("newgame")[0].click()  # 撤销
        self.assertIn(OFFER[0], self.session.settings.owned)  # 撤销本身照常做了
        self.run_pending()
        reasons = [e["snapshot"].get("reason") for e in self.diagnostics.exports]
        self.assertEqual(reasons, [None, "撤销新的一局的判定（误判）时自动保存，不含截图"])
        status = self.w.status.status_text()
        self.assertIn(UNDONE_SAVED.split("{path}")[0], status)
        self.assertTrue(status.endswith("诊断包2"), status)

    def test_background_failure_reported(self):
        """后台任务中途抛出（取推荐日志那一步）：照样告诉界面写完了，按失败提示；错误记进错误记录；「导出诊断」又能用。"""
        errors = capture_errors(self)
        diagnostics = self.w.services.diagnostics = FlakyDiagnostics(self.diagnostics.folder)
        self.session.eventlog.recorder = FakeRecorder()
        self.w.export_diagnostics()
        self.run_pending()
        self.assertIn("诊断包导出失败", self.w.status.status_text())
        self.assertNotIn("正在导出诊断包…", self.w.status.status_text())
        self.assertEqual([t for t, _ in errors.entries()], ["导出诊断包出错（拾星助手仍在运行）"])
        self.w.export_diagnostics()  # 不会一直卡在「正在导出」
        self.assertEqual(len(self.pending), 1)
        self.run_pending()
        self.assertEqual(len(diagnostics.exports), 1)

    def test_background_failure_then_queued_undo_saved(self):
        """写诊断包时撤销了误判，写的那一份中途抛出：排着的撤销存档照常接着存。"""
        capture_errors(self)
        diagnostics = self.w.services.diagnostics = FlakyDiagnostics(self.diagnostics.folder)
        self.session.eventlog.recorder = FakeRecorder()
        self.enter(rnd=8, star=2)
        self.pick_first()
        self.advance(30)
        self.hud(1)
        self.hud(1)  # 判定为新的一局（可以撤销）
        self.w.export_diagnostics()
        self.w.notices.buttons("newgame")[0].click()  # 撤销：那一份排着
        self.run_pending()
        reasons = [e["snapshot"].get("reason") for e in diagnostics.exports]
        self.assertEqual(reasons, ["撤销新的一局的判定（误判）时自动保存，不含截图"])
        self.assertIn(UNDONE_SAVED.split("{path}")[0], self.w.status.status_text())


class AttentionTest(BehaviorBase):
    def check(self, want: bool) -> None:
        self.assertEqual(self.w.needs_attention(), want)
        self.assertEqual(bool(self.w.panel.property("attention")), want)  # 边框

    def test_same_as_border(self):
        """needs_attention 就是边框变色用的那个判断：几种局面下两者一致。"""
        self.check(False)
        self.hud(1)
        self.hud(1)  # 请用户打开「查看筹码」面板
        self.check(True)
        self.w.notices.buttons("panel")[0].click()  # 不识别已有筹码，关掉这条
        self.check(False)
        with self.w.lock:
            self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        self.w.refresh()  # 几张地图的匹配度都低：请用户选地图
        self.check(True)
        self.w.scheme.map_buttons_text()  # 候选地图按钮在
        with self.w.lock:
            self.session.set_map("魔法学院")
        self.w.refresh()
        self.check(False)
        self.stars(1)
        self.offer()  # 角色还没认出来：推荐暂停，请用户选角色
        self.check(True)


if __name__ == "__main__":
    unittest.main()
