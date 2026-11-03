"""一次性提示什么时候收起：

- 「游戏窗口已最小化，无法截图。请还原游戏窗口。」「未找到游戏窗口……」：游戏窗口又看得到了（游戏或本程序在前台、游戏在
  别的程序后面）就收起。识别线程可能抢在悬浮窗看到最小化之前发来，也可能在看到还原以后才送到，两种先后都收得起来；
  游戏窗口还最小化着时照常显示。还原以后不收起的话，一行摘要会一直被这一句占着，看不到轮次、星数。
- 识别出错的提示（Watcher.recog_error 发来的固定中文）：RECOG_ERROR_MS（15 秒）以后自动收起，换回等待的文字；
  别的一次性提示照旧。一帧里只有几项子识别出错（其余读数照常交出）时后台识别线程发来的同样收起。
- 操作结果的一次性提示（切换模式、指定地图、撤销、补录、改正候选、按热键识别的结果等）：FLASH_MS（15 秒）以后
  自动收起；要用户处理的出错提示（筹码名不在筹码表里、热键被占用、导出诊断不可用等）照旧留着。到点时只有
  还是同一条提示才收起：15 秒内又来了一条新提示时，前一条的计时到点不清新的。移出筹码的那一条带「撤销」，是「已有筹码」
  卡片里的提示，另按 REMOVE_UNDO_MS（同样 15 秒）收起。

显示的文字用公开探针（status_text、summary_text）查；操作结果挂没挂计时、计时到点的那一步用悬浮窗的计时序号
（_flash_seq、_unflash，不真等 15 秒）。前台状态由假的探测函数给（winapi.GameProbe），离屏、不找真实的游戏窗口。
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

import numpy as np
from PySide6.QtCore import QTimer

from advisor.data import load_db
from advisor.ui import overlay as ov
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import (
    BLOCKED_HOTKEY,
    MANUAL_SWITCHED,
    NO_GAME,
    RECOG_FRAME,
    RECOG_ITEM,
    REMOVED_TEXT,
)
from advisor.ui.overlay_parts.app import OverlaySession, UiSettings
from advisor.ui.overlay_parts.watcher import Watcher
from advisor.ui.services import Services
from advisor.ui.winapi import GameProbe
from advisor.vision import capture
from tests.support import qtenv
from tests.support.errors import capture_errors
from tests.support.overlay_cases import expire_flash, frame
from tests.support.waits import wait_until

APP = qtenv.app()
RECT = (100, 50, 1600, 900)
WAITING = "等待筹码三选一界面…"
MANUAL_WAITING = "手动模式：后台不截图。出现筹码三选一界面时，请按 F8。"  # 切到手动模式以后的等待提示（热键 F8 可用）
# 要用户处理的出错提示（照旧留着）：筹码名不在筹码表里、导出诊断不可用（BLOCKED_HOTKEY 从文案里取）
CHIP_ERROR = "「某某」不在筹码表中，请输入完整的筹码名或从列表中选择"
EXPORT_ERROR = "导出诊断不可用：程序文件不完整，请把压缩包整个重新解压。"
HOTKEY_RESULT = "已截图识别：当前画面不是筹码三选一界面"  # 按热键识别的结果（Watcher.flash_status 发来的）
OFFER = ["标靶", "标记喷罐", "鹰眼瞄具"]


def probe(state: str) -> GameProbe:
    """前台状态：game / own / background（游戏在别的程序后面）/ minimized / nogame。"""
    if state == "nogame":
        return GameProbe(False, None, False, False, False)
    if state == "minimized":
        return GameProbe(True, None, True, False, False, hwnd=0x1001)
    return GameProbe(True, RECT, False, state == "game", state == "own", hwnd=0x1001)


class StatusExpiryBase(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.state = "game"
        self.w = Overlay(self.db, OverlaySession(self.db, UiSettings(hero="凛")), threading.Lock(), services=Services())
        self.w.probe = lambda: probe(self.state)
        self.addCleanup(self.w.hide)
        self.poll("game")

    def poll(self, state: str) -> None:
        """悬浮窗的定时器查看一次游戏窗口，这时的前台状态是 state。"""
        self.state = state
        self.w.follower.poll()

    def shown(self, text: str) -> bool:
        return text in self.w.status.status_text() or self.w.status.summary_plain.startswith(text)


class WindowGoneTest(StatusExpiryBase):
    def test_minimized_message_cleared_after_restore(self):
        """识别线程抢在悬浮窗看到最小化之前发来：最小化期间照旧显示，还原以后收起。"""
        self.w.set_status(capture.MINIMIZED)  # Watcher 的 status 信号
        self.poll("minimized")
        self.assertTrue(self.shown(capture.MINIMIZED))
        self.poll("game")
        self.assertFalse(self.shown(capture.MINIMIZED), self.w.status.status_text())
        self.assertIn(WAITING, self.w.status.status_text())

    def test_late_message_cleared_on_next_poll(self):
        """在悬浮窗看到还原以后才送到：下一次查看时收起。"""
        self.poll("minimized")
        self.poll("game")
        self.w.set_status(capture.MINIMIZED)
        self.assertTrue(self.shown(capture.MINIMIZED))
        self.poll("game")
        self.assertFalse(self.shown(capture.MINIMIZED))

    def test_kept_while_still_minimized(self):
        self.w.set_status(capture.MINIMIZED)
        for _ in range(3):
            self.poll("minimized")
            self.assertTrue(self.shown(capture.MINIMIZED))

    def test_cleared_for_own_and_background(self):
        """本程序的窗口在前台、游戏在别的程序后面：游戏窗口都看得到，一样收起。"""
        for state in ("own", "background"):
            with self.subTest(state=state):
                self.poll("minimized")
                self.w.set_status(capture.MINIMIZED)
                self.poll(state)
                self.assertFalse(self.shown(capture.MINIMIZED))

    def test_no_game_same_as_capture(self):
        """悬浮窗自己查到找不到游戏窗口时说的（NO_GAME）和后台识别发来的（capture.NOT_FOUND）是同一句：游戏窗口又看得到时
        按这句认出来收起。不写窗口标题（两个标题写全了一行显示放不下，「请先启动游戏」会被省略）。"""
        self.assertEqual(NO_GAME, capture.NOT_FOUND)
        self.assertEqual(NO_GAME, "未找到游戏窗口，请先启动游戏。")

    def test_no_game_message_from_watcher_cleared(self):
        """识别线程报「未找到游戏窗口」（游戏重启的间隙），悬浮窗这边游戏一直在：下一次查看时收起。"""
        self.poll("background")
        self.w.set_status(NO_GAME)
        self.poll("background")
        self.assertFalse(self.shown(NO_GAME))

    def test_no_game_kept_while_missing(self):
        self.poll("nogame")
        for _ in range(3):
            self.poll("nogame")
            self.assertTrue(self.shown(NO_GAME))
        self.poll("game")
        self.assertFalse(self.shown(NO_GAME))

    def test_other_messages_untouched(self):
        """要用户处理的出错提示（识别功能暂不可用，正在下载图片）不因为查看游戏窗口而收起。"""
        self.w.set_status(BLOCKED_HOTKEY)
        self.poll("game")
        self.poll("background")
        self.assertTrue(self.shown(BLOCKED_HOTKEY))


class NoGameFitTest(unittest.TestCase):
    """一行显示时「未找到游戏窗口，请先启动游戏。」整句放得下（不从末尾省略）：句末要用户做的「请先启动游戏」看得见。
    字体同 overlay_app.build() 设给程序的（微软雅黑 UI 10pt），先设字体再建悬浮窗；默认皮肤。"""

    def test_fits_one_line(self):
        from advisor.ui import theme

        self.addCleanup(APP.setFont, APP.font())
        APP.setFont(theme.app_font())
        db = load_db()
        w = Overlay(db, OverlaySession(db, UiSettings(hero="凛")), threading.Lock(), services=Services())
        self.addCleanup(w.hide)
        w.probe = lambda: probe("nogame")
        w.follower.poll()
        self.assertEqual(w.status.summary_plain, NO_GAME)
        self.assertNotIn("elide", w.status.summary_shrink)


class RecognitionErrorTest(StatusExpiryBase):
    ERROR = RECOG_FRAME

    def test_error_collapses_after_a_while(self):
        with mock.patch.object(ov, "RECOG_ERROR_MS", 50):
            self.w.set_recog_error(self.ERROR)
            self.assertTrue(self.shown(self.ERROR))
            self.assertTrue(wait_until(lambda: not self.shown(self.ERROR), 3000), self.w.status.status_text())
        self.assertIn(WAITING, self.w.status.status_text())

    def test_repeated_error_restarts_timer(self):
        """一直出错（每次识别都出错）：每次都重新计时，第一次的计时到了也不收起。"""
        with mock.patch.object(ov, "RECOG_ERROR_MS", 50):
            self.w.set_recog_error(self.ERROR)
        with mock.patch.object(ov, "RECOG_ERROR_MS", 60000):
            self.w.set_recog_error(self.ERROR)
        later = []
        QTimer.singleShot(200, lambda: later.append(1))  # 比第一次的计时晚到：它到了，第一次的一定已经到过
        self.assertTrue(wait_until(lambda: later))
        self.assertTrue(self.shown(self.ERROR))

    def test_default_is_fifteen_seconds(self):
        self.assertEqual(ov.RECOG_ERROR_MS, 15000)

    def test_error_drawn_right_away(self):
        """收到就画出来，不等别的事来刷新：一行显示时摘要的悬停说明里马上就有这一句（同一句去重以后不会紧接着再来，
        等下一次刷新的话可能到收起都没画出来）。status_text 只读一次性提示本身，核对不到有没有画。"""
        self.w.set_recog_error(self.ERROR)
        self.assertIn(self.ERROR, self.w.status.summary_tip_text)

    def test_sub_recognition_error_from_watcher_collapses(self):
        """后台识别线程：一帧里有一项子识别出错（FrameProcessor.errors，其余照常）时发来的提示，同样按「识别出错」
        计时收起；错误记进错误记录。"""
        errors = capture_errors(self)

        class Source:  # 假的取帧对象：每次都取到一幅画面，不截屏
            state = None

            def grab(self):
                return np.zeros((8, 8, 3), np.uint8)

            def close(self):
                pass

        class OneError:  # 假的识别流程：这一帧认角色出错，其余照常（没有事件）
            chip_select_visible = False

            def __init__(self, *args):
                self.errors = []

            def process(self, img, now, force=False):
                self.errors = [("hero", RuntimeError("认角色出错"), "Traceback（假的调用栈）\nRuntimeError: 认角色出错")]
                return []

        text = RECOG_ITEM.format(item="角色")
        with mock.patch("advisor.vision.pipeline.FrameProcessor", OneError), mock.patch.object(ov, "RECOG_ERROR_MS", 50):
            watcher = Watcher(self.db, self.w.session, self.w.lock, manual=True, source=Source())
            watcher.recog_error.connect(self.w.set_recog_error)
            watcher.start()
            try:
                watcher.trigger()
                self.assertTrue(wait_until(lambda: self.shown(text), 10000), self.w.status.status_text())
            finally:
                self.assertTrue(watcher.stop(10000))
            self.assertTrue(wait_until(lambda: not self.shown(text), 3000), self.w.status.status_text())
        self.assertIn(WAITING, self.w.status.status_text())
        self.assertEqual([t for t, _ in errors.entries()], ["识别出错（拾星助手仍在运行）"])


class OperationResultExpiryTest(StatusExpiryBase):
    """操作结果的一次性提示：FLASH_MS（15 秒）后自动收起；要用户处理的出错提示照旧留着。
    expire_flash 收起的是眼下显示着的那一条，不管它挂没挂计时：每条先核对挂了计时（_flash_seq 加了一），才算测到了收起。"""

    def test_default_is_fifteen_seconds(self):
        self.assertEqual(ov.FLASH_MS, 15000)

    def flashed(self, act, text: str) -> None:
        """做 act：显示 text、挂了一个计时；到点收起。"""
        w = self.w
        before = w._flash_seq
        act()
        self.assertEqual(w._flash_seq, before + 1)  # 走了 _flash，挂了计时
        self.assertTrue(self.shown(text), w.status.status_text())
        expire_flash(w)
        self.assertFalse(self.shown(text), w.status.status_text())

    def test_manual_switch_expires(self):
        """切换识别模式的结果：到点收起，换回手动模式的等待提示。"""
        w = self.w
        self.flashed(lambda: w.set_manual(True), MANUAL_SWITCHED.format(key=w.hotkeys.hotkey))
        self.assertEqual(w.message.text, MANUAL_WAITING)
        self.assertIn(MANUAL_WAITING, w.status.status_text())

    def test_map_expires(self):
        """手动指定地图的结果：到点收起。"""
        self.flashed(lambda: self.w._choose_map("魔法学院"), "地图已手动指定为「魔法学院」")
        self.assertEqual(self.w.message.text, WAITING)

    def test_add_and_remove_expire(self):
        """补录的结果：到点收起。移出的那一条带「撤销」，是「已有筹码」卡片里的提示（不走 _flash）：REMOVE_UNDO_MS 到点连同
        按钮收起；到点之前又移出了别的，前一次的计时到点不收新的那一条。"""
        w = self.w
        self.flashed(lambda: w.add_owned("书签"), "已补录：书签")
        self.flashed(lambda: w.add_owned("书签"), "「书签」已在已有筹码中，无需补录")
        w.add_owned("标靶")
        before = w._flash_seq
        self.assertTrue(w.remove_owned("书签"))
        self.assertEqual(w._flash_seq, before)
        self.assertFalse(self.shown(REMOVED_TEXT.format(chip="书签")))  # 不在识别状态那一行
        self.assertEqual(w.notice_model.removed, "书签")
        first = w._removal_seq
        self.assertTrue(w.remove_owned("标靶"))
        w._expire_removal(first)  # 前一次的计时到点：不收新的那一条
        self.assertEqual(w.notice_model.removed, "标靶")
        w._expire_removal(w._removal_seq)
        self.assertIsNone(w.notice_model.removed)

    def test_hotkey_recognition_result_expires(self):
        """按热键识别的结果（Watcher.flash_status → set_flash_status）：到点收起。"""
        self.flashed(lambda: self.w.set_flash_status(HOTKEY_RESULT), HOTKEY_RESULT)

    def test_flash_status_and_undo_expire(self):
        """set_status(..., flash=True)（推荐卡片的改正结果走它）、撤销新一局的结果（没有可撤销的：「已无法撤销」）：
        到点收起。"""
        w = self.w
        self.flashed(lambda: w.set_status("第 2 个候选筹码已是「标靶」", "advice", flash=True), "第 2 个候选筹码已是「标靶」")
        self.flashed(w.undo_new_game, "新的一局的判定已无法撤销（这一局已记录过筹码）")

    def test_fix_outdated_result_expires(self):
        """推荐卡片：新的候选已经到了 Session、界面还没处理时点了确定（「候选筹码已变化，本次改正未生效……」）：到点收起；
        三选一还显示着，收起后不写等待提示。"""
        w, s = self.w, self.w.session
        s.game.seen_hero = "凛"  # 角色已识别（角色待识别时 Session 暂停推荐）
        w.handle(s.on_frame(frame(OFFER, rerolls=3)))
        w.advice.open_fix(1)
        s.on_frame(frame(["大背包", "缓冲盾牌", "书签"], rerolls=2))  # 事件还没送到界面
        w.fix_box.setEditText("魔法箭袋")
        self.flashed(w.advice.fix_confirm, "候选筹码已变化，本次改正未生效")
        self.assertEqual(w.message.text, "")

    def test_fix_bar_closed_expires(self):
        """改正栏开着时候选换了（事件分派写「候选筹码已变化，改正栏已关闭」）：到点收起。"""
        w, s = self.w, self.w.session
        s.game.seen_hero = "凛"
        w.handle(s.on_frame(frame(OFFER, rerolls=3)))
        w.advice.open_fix(1)
        self.flashed(lambda: w.handle(s.on_frame(frame(["大背包", "缓冲盾牌", "书签"], rerolls=2))),
                     "候选筹码已变化，改正栏已关闭")
        self.assertTrue(w.fix_row.isHidden())

    def test_errors_stay(self):
        """要用户处理的出错提示：不挂计时；前一条操作结果的计时到点不清掉它，查看游戏窗口也不收起。"""
        w = self.w
        for text, area in ((CHIP_ERROR, "owned"), (CHIP_ERROR, "advice"), (BLOCKED_HOTKEY, "global"),
                           (EXPORT_ERROR, "global")):
            with self.subTest(text=text, area=area):
                w.set_flash_status(HOTKEY_RESULT)  # 前一条：操作结果，挂了计时
                seq = w._flash_seq
                w.set_status(text, area)
                self.assertEqual(w._flash_seq, seq)  # 出错的不挂计时
                w._unflash(seq, HOTKEY_RESULT)  # 前一条的计时到点
                self.assertTrue(self.shown(text), w.status.status_text())
                self.poll("game")
                self.assertTrue(self.shown(text), w.status.status_text())

    def test_new_result_survives_old_timer(self):
        """15 秒内又来了一条新的操作结果（换了一块）：前一条的计时到点不清新的，新的照样按自己的计时收起。"""
        w = self.w
        w.set_manual(True)
        first = MANUAL_SWITCHED.format(key=w.hotkeys.hotkey)
        first_seq = w._flash_seq
        w._choose_map("魔法学院")
        second = "地图已手动指定为「魔法学院」"
        w._unflash(first_seq, first)  # 前一条的计时到点
        self.assertTrue(self.shown(second), w.status.status_text())
        self.assertFalse(self.shown(first), w.status.status_text())
        w._unflash(first_seq + 1, second)  # 新的那条自己的计时到点
        self.assertFalse(self.shown(second), w.status.status_text())

    def test_same_text_again_restarts_timer(self):
        """同一句操作结果 15 秒内又出一次（例如连着两次补录同一个已在已有筹码中的筹码）：按后一次重新计时，前一次的
        计时到点不清掉。"""
        w = self.w
        w.add_owned("书签")
        text = "「书签」已在已有筹码中，无需补录"
        w.add_owned("书签")
        first_seq = w._flash_seq
        w.add_owned("书签")
        w._unflash(first_seq, text)
        self.assertTrue(self.shown(text), w.status.status_text())
        w._unflash(first_seq + 1, text)
        self.assertFalse(self.shown(text), w.status.status_text())


class OperationResultRealTimerTest(StatusExpiryBase):
    """按真实计时器走一遍（时长换成很小，不等 15 秒）：切换识别模式的结果（_flash）到点真的收起。"""

    def test_timer_expires(self):
        with mock.patch.object(ov.Overlay._flash, "__defaults__", ("global", 50)):
            self.w.set_manual(True)
        text = MANUAL_SWITCHED.format(key=self.w.hotkeys.hotkey)
        self.assertTrue(self.shown(text), self.w.status.status_text())
        self.assertTrue(wait_until(lambda: not self.shown(text), 3000), self.w.status.status_text())
        self.assertIn(MANUAL_WAITING, self.w.status.status_text())


if __name__ == "__main__":
    unittest.main()
