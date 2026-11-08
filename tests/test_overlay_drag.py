"""在悬浮窗上按住左键拖动：

- 单击（没有拖动，或只动了不到系统拖动距离的几个像素）不算拖动：不记位置、不存进设置文件，三选一期间照旧自动让开。
  一按一放就记成「拖动过」的话，位置会被固定，固定的角还可能从右上变成右下。
- 拖到屏幕外松手：马上挪回屏幕内（不等下一次刷新）；存下的相对位置夹在 -1～2 之间，下次启动照样认（不夹的话拖得太远时存下
  的位置下次启动不认，会退回默认位置）。

鼠标事件直接交给悬浮窗（QMouseEvent 带屏幕坐标），离屏、不找真实的游戏窗口（前台状态由假的探测函数给）。
"""
from __future__ import annotations

import threading
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from advisor.data import load_db
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.geometry import valid_rel
from advisor.ui.services import Services
from advisor.ui.winapi import GameProbe
from tests.support import qtenv

APP = qtenv.app()
RECT = (100, 50, 1600, 900)  # 游戏客户区（离屏的屏幕缩放为 1：物理像素和逻辑坐标相同）


def mouse(w: Overlay, kind, at: QPoint) -> None:
    """在屏幕坐标 at 处按下 / 移动 / 松开左键（交给悬浮窗）。"""
    button = Qt.LeftButton if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) else Qt.NoButton
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    local = QPointF(at - w.frameGeometry().topLeft())
    QApplication.sendEvent(w, QMouseEvent(kind, local, QPointF(at), button, buttons, Qt.NoModifier))


def drag(w: Overlay, start: QPoint, *moves: QPoint) -> None:
    mouse(w, QEvent.MouseButtonPress, start)
    for at in moves:
        mouse(w, QEvent.MouseMove, at)
    mouse(w, QEvent.MouseButtonRelease, moves[-1] if moves else start)


class DragBase(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.w = Overlay(self.db, self.session, threading.Lock(), services=Services())
        self.addCleanup(self.w.hide)

    def with_game(self) -> None:
        self.w.probe = lambda: GameProbe(True, RECT, False, True, False, hwnd=0x1001)
        self.w.follower.place()
        self.w.show()

    def grip(self) -> QPoint:
        """按在悬浮窗里面的一点（标题栏左边）。"""
        return self.w.frameGeometry().topLeft() + QPoint(12, 8)


class ClickTest(DragBase):
    def test_click_is_not_a_drag(self):
        self.with_game()
        before = self.w.pos()
        drag(self.w, self.grip())  # 按下就松开
        self.assertIsNone(self.session.settings.window_rel)
        self.assertEqual(self.w.pos(), before)

    def test_tiny_move_is_not_a_drag(self):
        """手抖：移动不到系统的拖动距离，算单击，悬浮窗不动、不记位置。"""
        self.with_game()
        before = self.w.pos()
        step = max(QApplication.startDragDistance() - 2, 1)
        start = self.grip()
        drag(self.w, start, start + QPoint(step, 0))
        self.assertIsNone(self.session.settings.window_rel)
        self.assertEqual(self.w.pos(), before)

    def test_real_drag_moves_and_remembers(self):
        self.with_game()
        before = self.w.pos()
        start = self.grip()
        drag(self.w, start, start + QPoint(-30, 0), start + QPoint(-200, 40))
        self.assertEqual(self.w.pos(), before + QPoint(-200, 40))
        rel = self.session.settings.window_rel
        self.assertIsNotNone(valid_rel(rel))


def drag_widget(widget, start: QPoint, *moves: QPoint) -> None:
    """在 widget 上按在屏幕坐标 start 处、移过 moves（事件交给 widget，没接住的照常往上传给悬浮窗）。"""
    origin = widget.mapToGlobal(QPoint(0, 0))

    def send(kind, at: QPoint) -> None:
        button = Qt.LeftButton if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) else Qt.NoButton
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
        local = QPointF(at - origin)
        QApplication.sendEvent(widget, QMouseEvent(kind, local, QPointF(at), button, buttons, Qt.NoModifier))

    send(QEvent.MouseButtonPress, start)
    for at in moves:
        send(QEvent.MouseMove, at)
    send(QEvent.MouseButtonRelease, moves[-1] if moves else start)


class LaneTagDragTest(DragBase):
    def test_drag_from_lane_tag(self):
        """按在标题栏的端标签上也能拖动悬浮窗（标签不接文字交互，鼠标事件照常往上传）。"""
        self.w.set_lane_tag("Steam", "这张悬浮窗跟的是「Steam端」的游戏窗口。")
        self.with_game()
        tag = self.w.lane_tag
        self.assertFalse(tag.isHidden())
        before = self.w.pos()
        start = tag.mapToGlobal(tag.rect().center())
        drag_widget(tag, start, start + QPoint(-30, 0), start + QPoint(-200, 40))
        self.assertEqual(self.w.pos(), before + QPoint(-200, 40))
        self.assertIsNotNone(valid_rel(self.session.settings.window_rel))


class OffScreenTest(DragBase):
    def screen(self):
        return QGuiApplication.primaryScreen().availableGeometry()

    def test_dropped_off_screen_comes_back_at_once(self):
        """拖到屏幕左边外面很远处松手：马上整个挪回屏幕内；存下的比例夹到 -1，下次启动照样认。"""
        self.with_game()
        start = self.grip()
        drag(self.w, start, start + QPoint(-30, 0), start + QPoint(-6000, 0))
        self.assertTrue(self.screen().contains(self.w.frameGeometry()), self.w.frameGeometry())
        rel = self.session.settings.window_rel
        self.assertIsNotNone(valid_rel(rel), rel)
        self.assertEqual(rel[0], -1.0)
        self.assertEqual(self.session.settings.window_pos, [self.w.x(), self.w.y()])  # 存的是挪回以后的位置
        again = Overlay(self.db, OverlaySession(self.db, self.session.settings), threading.Lock(), services=Services())
        self.addCleanup(again.hide)
        again.probe = self.w.probe
        again.follower.place()
        self.assertEqual(again.pos(), self.w.pos())  # 重启后照这个位置放，不退回默认位置

    def test_no_game_window_comes_back(self):
        """没有游戏窗口时拖到屏幕下面外面：同样马上挪回屏幕内。"""
        self.w.follower.place()
        self.w.show()
        start = self.grip()
        drag(self.w, start, start + QPoint(0, 30), start + QPoint(0, 5000))
        self.assertTrue(self.screen().contains(self.w.frameGeometry()), self.w.frameGeometry())
        self.assertEqual(self.session.settings.window_pos, [self.w.x(), self.w.y()])


if __name__ == "__main__":
    unittest.main()
