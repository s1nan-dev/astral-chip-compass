"""「带『撤销』的新一局提示」什么时候自动收起：

- 选角界面判定的那一种（要开一局、玩家还要在选角、加载、开头几轮停好几分钟）：显示 NEWGAME_NOTICE_MS（30 秒）后自动
  收起，「撤销」一起去掉，之后不能再撤销这次判定。
- 对局中途判定的那一种（误判时清空的是正在用的记录）：显示 NEWGAME_MIDGAME_NOTICE_MS（60 秒）后「识别」卡片和
  「已有筹码」卡片的两行一起收起，「撤销」一起去掉。
- 到点时只有还是同一次判定才收起：这期间又判了一次新一局、点了撤销、记下了第一个筹码，前一次的计时到了都什么都不做。

计时不真等 30 秒、60 秒：既有的写法是直接走计时器到点时调用的那一步（overlay_cases.expire_flash，这里用
expire_newgame）；另把上面几种情形按真实计时器（时长换成很小）各走一遍（RealTimerTest）：到点时用的是计时器挂上时
记下的那一次判定的序号，直接调用那一步的写法核对不到这一点。用 Qt 的 offscreen 平台，不会弹出窗口。
"""
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QTimer

from advisor.data import load_db
from advisor.ui import overlay as ov
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.services import Services
from tests.support import qtenv
from tests.support.fakes import FakeLogbook
from tests.support.overlay_cases import expire_newgame
from tests.support.waits import wait_until

APP = qtenv.app()


class NewGameTimeoutBase(unittest.TestCase):
    """建一个离屏悬浮窗，可以喂「新一局」事件、走计时器到点那一步。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        self.addCleanup(self.w.hide)

    def select_newgame(self) -> int:
        """选角界面判定的新一局（可撤销、不是对局中途）：返回本次的序号。"""
        self.w.handle(self.session._auto_new_game("select", None, "检测到选角界面"))
        return self.w._newgame_seq

    def midgame_newgame(self) -> int:
        """对局中途（轮次）判定的新一局：可撤销，按「需要您操作」显示。"""
        self.w.handle(self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        return self.w._newgame_seq

    def assert_midgame_rows(self) -> None:
        """对局中途判定的两行都在，各带一个「撤销」。"""
        texts = self.w.notice_model.texts()
        self.assertTrue(texts["newgame"].startswith("⚠ 对局中途判定为新的一局"), texts)
        self.assertIn("newgame_owned", texts)
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame_owned")], ["撤销"])

    def assert_no_newgame_rows(self) -> None:
        texts = self.w.notice_model.texts()
        self.assertNotIn("newgame", texts)
        self.assertNotIn("newgame_owned", texts)
        self.assertEqual(self.w.notices.buttons("newgame"), [])
        self.assertEqual(self.w.notices.buttons("newgame_owned"), [])


class SelectNoticeExpiryTest(NewGameTimeoutBase):
    def test_select_notice_expires_with_undo(self):
        """① 选角界面判定：有这一行、带「撤销」；到点后这一行和「撤销」都没了。"""
        self.assertEqual(ov.NEWGAME_NOTICE_MS, 30000)  # 默认 30 秒
        self.select_newgame()
        self.assertIn("已判定为新的一局（依据：检测到选角界面）", self.w.notice_model.texts()["newgame"])
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        expire_newgame(self.w)
        self.assert_no_newgame_rows()

    def test_second_judgement_keeps_its_own_timer(self):
        """③ 30 秒内又判了一次：第一次的计时到点，不清第二次的。"""
        first = self.select_newgame()
        second = self.select_newgame()
        self.assertGreater(second, first)
        self.w._expire_newgame(first)  # 第一次的计时到点
        self.assertIn("newgame", self.w.notice_model.texts())
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        expire_newgame(self.w)  # 第二次自己的计时到点才收起
        self.assert_no_newgame_rows()

    def test_undo_before_timer_does_nothing(self):
        """④ 30 秒内点了撤销：到点什么都不做。"""
        seq = self.select_newgame()
        self.w.notices.buttons("newgame")[0].click()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        status = self.w.status.status_text()
        self.w._expire_newgame(seq)
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.assertEqual(self.w.status.status_text(), status)

    def test_first_chip_before_timer_does_nothing(self):
        """30 秒内记下了第一个筹码：撤销按钮已经消失，到点也什么都不做。"""
        seq = self.select_newgame()
        self.session.add_chip("书签")
        self.w.refresh()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.w._expire_newgame(seq)
        self.assertNotIn("newgame", self.w.notice_model.texts())

    def test_undoable_without_cause_expires(self):
        """判定依据的种类没写（cause 为空）的可撤销新一局：按 notices.midgame 的约定和选角界面判定的一样排成一行说明、带
        「撤销」，所以也按 30 秒收起，显示和计时不会一边按对局中途、一边按选角界面算。"""
        ev = self.session._auto_new_game("select", None, "检测到选角界面")
        ev.cause = ""
        self.w.handle(ev)
        self.assertEqual(self.w.notice_model.notices["newgame"][2], "info")
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        self.assertEqual(self.w._newgame_seq, 1)  # 挂了计时
        expire_newgame(self.w)
        self.assert_no_newgame_rows()


class MidgameNoticeExpiryTest(NewGameTimeoutBase):
    def test_midgame_notice_expires_with_undo(self):
        """② 对局中途判定：60 秒后两行一起收起，「撤销」一起去掉。"""
        self.assertEqual(ov.NEWGAME_MIDGAME_NOTICE_MS, 60000)  # 默认 60 秒
        self.midgame_newgame()
        self.assert_midgame_rows()
        expire_newgame(self.w)
        self.assert_no_newgame_rows()

    def test_midgame_notice_before_timeout(self):
        """不到 60 秒还在：没有走计时器到点那一步时两行照旧显示。"""
        self.midgame_newgame()
        self.assert_midgame_rows()

    def test_second_judgement_keeps_its_own_timer(self):
        """60 秒内又判了一次（对局中途）：第一次的计时到点，不清第二次的两行。"""
        first = self.midgame_newgame()
        second = self.midgame_newgame()
        self.assertGreater(second, first)
        self.w._expire_newgame(first)
        self.assert_midgame_rows()
        expire_newgame(self.w)  # 第二次自己的计时到点才收起
        self.assert_no_newgame_rows()

    def test_select_timer_keeps_later_midgame_notice(self):
        """选角界面判定以后又按轮次判了一次：选角界面那次的计时到点，不收对局中途的两行（对局中途那次另挂了计时）。"""
        first = self.select_newgame()
        midgame = self.midgame_newgame()
        self.assertGreater(midgame, first)
        self.w._expire_newgame(first)
        self.assert_midgame_rows()

    def test_undo_before_timer_does_nothing(self):
        """60 秒内点了撤销：两行随之消失，到点什么都不做，「已撤销……」照旧显示。"""
        seq = self.midgame_newgame()
        self.w.notices.buttons("newgame")[0].click()
        self.assert_no_newgame_rows()
        status = self.w.status.status_text()
        self.w._expire_newgame(seq)
        self.assert_no_newgame_rows()
        self.assertEqual(self.w.status.status_text(), status)

    def test_first_chip_before_timer_does_nothing(self):
        """60 秒内记下了第一个筹码：撤销按钮已经消失，到点也什么都不做，记下的筹码照旧在。"""
        seq = self.midgame_newgame()
        self.session.add_chip("书签")
        self.w.refresh()
        self.assert_no_newgame_rows()
        self.w._expire_newgame(seq)
        self.assert_no_newgame_rows()
        self.assertEqual(self.session.settings.owned, ["书签"])


class RealTimerTest(NewGameTimeoutBase):
    """按真实计时器走一遍（时长换成很小，不等 30 秒、60 秒）：计时挂在悬浮窗上、到点真的收起；到点时核对的是计时器
    挂上时记下的那一次判定（又判了一次、点了撤销、记下了第一个筹码都不清错）。时长在挂计时的那一刻取，补丁只要包住
    判定的那一下。"""

    def wait_past(self, ms: int = 200) -> None:
        """处理 Qt 事件，直到一个比前面挂的短计时（50 毫秒）晚到的计时器到点：它到了，前面挂的一定已经到过。"""
        later = []
        QTimer.singleShot(ms, lambda: later.append(1))
        self.assertTrue(wait_until(lambda: later))

    def test_timer_expires_select_notice(self):
        """① 选角界面判定：到点自动收起，「撤销」一起去掉。"""
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 50):
            self.select_newgame()
        self.assertIn("newgame", self.w.notice_model.texts())
        self.assertTrue(wait_until(lambda: "newgame" not in self.w.notice_model.texts(), 3000),
                        self.w.notice_model.texts())
        self.assert_no_newgame_rows()

    def test_timer_expires_midgame_notice(self):
        """② 对局中途判定：到点两行一起收起，「撤销」一起去掉。"""
        with mock.patch.object(ov, "NEWGAME_MIDGAME_NOTICE_MS", 50):
            self.midgame_newgame()
        self.assert_midgame_rows()
        self.assertTrue(wait_until(lambda: "newgame" not in self.w.notice_model.texts(), 3000),
                        self.w.notice_model.texts())
        self.assert_no_newgame_rows()

    def test_midgame_notice_not_expired_early(self):
        """② 对局中途判定：不到时长时两行照旧在。"""
        with mock.patch.object(ov, "NEWGAME_MIDGAME_NOTICE_MS", 1000):  # 比下面等的 120 毫秒长得多：机器忙时也不会先到点
            self.midgame_newgame()
        self.wait_past(120)
        self.assert_midgame_rows()
        self.assertTrue(wait_until(lambda: "newgame" not in self.w.notice_model.texts(), 3000))
        self.assert_no_newgame_rows()

    def test_first_timer_keeps_second_judgement(self):
        """③ 30 秒内又判了一次：第一次挂的计时先到点，第二次的提示和「撤销」照旧在；第二次自己的计时到点才收起。"""
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 50):
            self.select_newgame()
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 60000):
            self.select_newgame()
        self.wait_past()
        self.assertIn("newgame", self.w.notice_model.texts())
        self.assertEqual([b.text() for b in self.w.notices.buttons("newgame")], ["撤销"])
        expire_newgame(self.w)  # 第二次自己的计时到点（不等 60 秒）
        self.assert_no_newgame_rows()

    def test_select_timer_keeps_later_midgame_notice(self):
        """选角界面判定以后又按轮次判了一次（对局中途的那一种另挂了计时，序号也变了）：选角界面那次的计时到点，不收
        对局中途的两行。"""
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 50):
            self.select_newgame()
        self.midgame_newgame()
        self.wait_past()
        self.assert_midgame_rows()

    def test_timer_after_undo_does_nothing(self):
        """④ 30 秒内点了「撤销」：计时到点什么都不做，提示不再出现，「已撤销……」照旧显示。"""
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 50):
            self.select_newgame()
        self.w.notices.buttons("newgame")[0].click()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        status = self.w.status.status_text()
        self.wait_past()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.assertEqual(self.w.status.status_text(), status)

    def test_timer_after_first_chip_does_nothing(self):
        """30 秒内记下了第一个筹码（撤销按钮已经消失）：计时到点什么都不做，记下的筹码照旧在。"""
        with mock.patch.object(ov, "NEWGAME_NOTICE_MS", 50):
            self.select_newgame()
        self.session.add_chip("书签")
        self.w.refresh()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.wait_past()
        self.assertNotIn("newgame", self.w.notice_model.texts())
        self.assertEqual(self.session.settings.owned, ["书签"])


if __name__ == "__main__":
    unittest.main()
