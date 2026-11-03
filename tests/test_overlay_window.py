"""悬浮窗整体：「已有筹码」卡片、补录、改正栏、状态胶囊、地图和角色、窗口位置、在设置窗口里更换热键和识别模式
（OverlayWidgetTest）；一行显示与展开、拖动（OverlayModeTest）；开发版的标题（DevLabelTest）；「拾星助手设置」窗口
（SettingsTest）。推荐日志、识别线程、热键线程和诊断模块用假对象代替。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import dataclasses
import html
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtGui import QGuiApplication, QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QApplication, QWidget, QComboBox, QPushButton  # noqa: E402
from advisor.data import load_db  # noqa: E402
from advisor.engine import advise  # noqa: E402
from advisor.pool import State  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui.overlay_parts.notices import short_path
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import RUNNING_BIT, PAUSED_BIT
from advisor.ui.overlay_parts.wording import MAP_ASK, OPEN_TASKS, RUNNING_TIP, TITLE_DEV, PAUSED_MIN_TIP, AUTO_SWITCHED, OWNED_EMPTY, HOTKEY_CHANGE_FAILED, TITLE, SETTINGS_TIP, MANUAL_SWITCHED, PAUSED_TIP
from advisor.ui.settings.dialog import SETTINGS_TITLE, SettingsDialog
from advisor.ui.hotkey import busy_text
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.overlay_parts.factory import WrapTaggedText
from advisor.ui.hotkey import startup_busy_text  # noqa: E402
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.settings import CAPTURE_TITLE, HOTKEY_WAIT, SETTINGS_METHOD, HOTKEY_WAIT_TIP, SETTINGS_REASON, SETTINGS_NO_GAME, WIDTH as SETTINGS_WIDTH, HOTKEY_DESC, settings_texts, SETTINGS_RETRY  # noqa: E402
from advisor.ui.text import tip_html, DIM_COLOR, option_views, plain_words, breakdown_rows, FONT_M, keep_words, FONT_S, KEEP_JOINER, KEEP_SPACE, LOOKAHEAD_MIN, SOFT_COLOR  # noqa: E402
from advisor.ui.widgets import TaggedText, Pill  # noqa: E402
from tests.support import sandbox  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, BANNED, FakeHotkey, FakeWatcher, SCORE_WORDS, button, buttons, capture_state, choose_hero, flat, frame, name_buttons, plain_html, probe, score_marks, shown, tip_score_marks, tip_text, unreadable


def fits(widget) -> bool:
    """部件的高度够放下它在当前宽度下需要的内容（文字没被截掉）。"""
    if not widget.hasHeightForWidth():
        return widget.height() >= widget.sizeHint().height()
    return widget.height() >= widget.heightForWidth(widget.width())


class OverlayWidgetTest(unittest.TestCase):
    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"  # 角色已识别（角色待识别时 Session 暂停推荐，发 hero_needed）
        self.window = Overlay(self.db, self.session, threading.Lock())

    def tearDown(self):
        self.window.hide()

    def status(self) -> str:
        return self.window.status.status_text()

    # ---- 「已有筹码」卡片（图标格子和筹码小卡片）

    def test_owned_card_lists_chips(self):
        """已有筹码按获得顺序填进 3-4-3 图标格子；每个筹码一张小卡片：筹码名，流派标签后面紧跟效果（不截断）。"""
        w = self.window
        self.session.set_owned(["普通瞄具", "速度轮滑-中级", "标靶"])
        w.set_expanded(True)
        self.assertEqual(w.chip_grid.names(), ["普通瞄具", "速度轮滑-中级", "标靶"])
        self.assertIn("3 个", shown(w, w.owned_card))  # 卡片头「已有筹码 3 个」（持有的筹码数不设上限，只写个数）
        cards = w.owned.cards_text()
        self.assertEqual(len(cards), 3)
        self.assertTrue(cards[0].startswith("普通瞄具｜标记 "))
        self.assertIn(self.db.chips["普通瞄具"].effect, cards[0])  # 效果全文
        self.assertTrue(cards[1].startswith("速度轮滑-中级｜通用 "))  # 不属于任何流派：「通用」
        self.assertTrue(w.owned_empty.isHidden())
        self.assertFalse(w.owned_scroll.isHidden())
        w.show()
        APP.processEvents()
        lines = w.owned_body.findChildren(TaggedText)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertTrue(fits(line), line.text())  # 效果长时在小卡片内换行，最后一行没被截掉
            right = line.mapTo(w.owned_body, QPoint(line.width(), 0)).x()
            self.assertLessEqual(right, w.owned_body.width())

    def test_owned_card_more_than_ten(self):
        """一局能持有超过 10 个筹码（「查看筹码」面板一页 10 格、多了翻页）：卡片头只写个数「12 个」，
        筹码小卡片 12 张都列出；图标格子照面板一页的 3-4-3 排列，第一页画前 10 个，翻到第二页画剩下的。"""
        w = self.window
        names = [c.name for c in self.db.chips.values() if not c.maps and not c.granted][:12]
        self.session.set_owned(names)
        w.set_expanded(True)
        self.assertEqual(self.session.settings.owned, names)
        self.assertIn("12 个", shown(w, w.owned_card))
        self.assertNotIn("/ 10", shown(w, w.owned_card))
        self.assertEqual(len(w.owned.cards_text()), 12)
        self.assertEqual((w.chip_grid.page(), w.chip_grid.page_count()), (1, 2))
        self.assertEqual(w.chip_grid.names(), names[:10])
        w.chip_grid.set_page(2)
        self.assertEqual(w.chip_grid.names(), names[10:])
        self.assertEqual(w.chip_grid.all_names(), names)

    def test_owned_mini_card_font_sizes(self):
        """字号收小（筹码多时窗口不被撑得太长）：筹码小卡片的筹码名用正文字号 FONT_M，流派标签和效果描述用最小一档
        FONT_S（不小于 9pt），小卡片之间 3；字号变小后效果描述照常换行，不截断。"""
        w = self.window
        names = ["普通瞄具", "速度轮滑-中级", "标靶", "大背包", "速度轮滑-高级", "美工刀-锋利", "医疗箱-紧急治疗",
                 "忍术飞镖", "安全气囊", "磨刀石"]
        self.session.set_owned(names)
        w._screen_height = lambda: 5000  # 不滚动
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        minis = [w.mini_box.itemAt(i).widget() for i in range(w.mini_box.count())]
        self.assertEqual(len(minis), 10)
        self.assertEqual(w.mini_box.spacing(), 3)
        for card, chip in zip(minis, names):
            name = card.findChild(QLabel)
            self.assertEqual(name.text(), chip)
            self.assertIn(f"font-size:{FONT_M}pt", name.styleSheet())
            line = card.findChild(TaggedText)
            self.assertEqual(line.font().pointSizeF(), FONT_S)
            self.assertGreaterEqual(line.font().pointSizeF(), 9)
            self.assertTrue(fits(line), line.text())
        w.hide()

    def test_owned_mini_card_keeps_words(self):
        """筹码小卡片的效果描述换行时词不从中间拆开（overlay.WrapTaggedText；不拆成「所有角色轮 / 次奖励星币 +3」）：
        和卡片里的提示同一套 text.keep_words，挨着数字的空格换成宽度相同的不换行空格（不出现「大于 / 20」）。
        text() 照旧是原文；插入的字符不占宽度，这几张的小卡片高度和不插入时相同（全部筹码里只有“8面”骰子多一行）。"""
        w = self.window
        names = ["银行卡-用不完", "星币锤", "金色飞星", "紫色飞星"]
        self.session.set_owned(names)
        w._screen_height = lambda: 5000
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        minis = [w.mini_box.itemAt(i).widget() for i in range(w.mini_box.count())]
        self.assertEqual(len(minis), len(names))
        for card, name in zip(minis, names):
            chip = self.db.chips[name]
            line = card.findChild(TaggedText)
            school = chip.school or "通用"
            self.assertIsInstance(line, WrapTaggedText)
            self.assertEqual(line.text(), f"{school} {chip.effect}")
            self.assertIn(chip.effect, w.owned.cards_text()[names.index(name)])
            plain = TaggedText(school, "info", chip.effect, SOFT_COLOR, point_size=FONT_S)
            self.assertEqual(line.sizeHint(), plain.sizeHint())  # 不占宽度
            self.assertEqual(line.heightForWidth(line.width()), plain.heightForWidth(line.width()))
            raw = line._text
            layout, _, _ = line._shape(line.width())
            for i in range(1, layout.lineCount()):
                p = layout.lineAt(i).textStart()
                with self.subTest(chip=name, line=i):
                    self.assertNotIn(KEEP_JOINER, raw[p - 1:p + 1])  # 不在插入的字符两边断开
                    self.assertNotEqual(raw[p - 1], KEEP_SPACE)
        bank = minis[0].findChild(TaggedText)
        layout, _, _ = bank._shape(bank.width())
        cuts = [len(plain_words(bank._text[:layout.lineAt(i).textStart()])) for i in range(1, layout.lineCount())]
        at = self.db.chips["银行卡-用不完"].effect.index("轮次奖励")
        self.assertTrue(cuts)  # 这个宽度下分成两行
        self.assertFalse(any(at < c < at + len("轮次奖励") for c in cuts), cuts)
        kept = keep_words("持有星币多于 20 时，攻击力 +2；⚠ 第 3 个")
        self.assertEqual(kept.count(KEEP_SPACE), 5)  # 「⚠ 第」之间的空格两边都不是数字、数值符号，照旧
        self.assertEqual(plain_words(kept), "持有星币多于 20 时，攻击力 +2；⚠ 第 3 个")
        w.hide()

    def test_owned_empty_takes_one_line(self):
        w = self.window
        w.set_expanded(True)
        self.assertFalse(w.owned_empty.isHidden())
        self.assertEqual(w.owned_empty.text(), OWNED_EMPTY)
        self.assertTrue(w.owned_scroll.isHidden())
        self.assertIn("0 个", shown(w, w.owned_card))

    def test_remove_owned_chip(self):
        """筹码小卡片右上角的「×」：移出记录有误的筹码；补录行等其他提示照旧保留。"""
        w = self.window
        self.session.set_owned(["书签", "标靶"])
        self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        self.assertFalse(w.missed.isHidden())
        w.set_expanded(True)
        remove = [b for b in buttons(w.mini_list) if b.text() == "×"]
        self.assertEqual(len(remove), 2)
        self.assertEqual(remove[0].toolTip(), "移出已有筹码（记录有误时点击）")
        remove[1].click()
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertEqual(w.chip_grid.names(), ["书签"])
        # 「已有筹码」卡片里一条提示（只作说明，边框不变色），带「撤销」
        self.assertEqual(w.notice_model.texts()["removed"], "已从已有筹码中移出「标靶」")
        self.assertIn("已从已有筹码中移出「标靶」", shown(w, w.owned_card))
        self.assertEqual([b.text() for b in w.notices.buttons("removed")], ["撤销"])
        self.assertEqual(tip_text(w.notices.buttons("removed")[0].toolTip()), "把「标靶」按原来的位置和来源放回已有筹码")
        self.assertFalse(w.missed.isHidden())
        self.assertIsNotNone(self.session.game.missed_offer)

    def test_undo_remove_owned_chip(self):
        """移出以后点「撤销」：按原来的位置、来源放回（Session.restore_chip），那一条收起，提示「已撤销移出……」；识别记录记
        remove、remove_undo 各一条。一行显示时那一条在「已有筹码」卡片里照样看得到（卡片只显示这几行提示）。"""
        w = self.window
        self.session.set_owned(["书签", "标靶", "大背包"])
        sources = dict(self.session.game.chip_sources)
        w.refresh()
        self.assertTrue(w.remove_owned("标靶"))
        self.assertFalse(w.expanded)
        self.assertFalse(w.owned_card.isHidden())  # 一行显示：「已有筹码」卡片只显示这一条提示
        self.assertIn("已从已有筹码中移出「标靶」", shown(w, w.owned_card))
        self.assertFalse(w.needs_attention())
        w.notices.buttons("removed")[0].click()
        self.assertEqual(self.session.settings.owned, ["书签", "标靶", "大背包"])
        self.assertEqual(self.session.game.chip_sources, sources)
        self.assertNotIn("removed", w.notice_model.texts())
        self.assertIn("已撤销移出：「标靶」已放回已有筹码", self.status())
        w.set_expanded(True)
        self.assertEqual(w.chip_grid.names(), ["书签", "标靶", "大背包"])
        records = [(e.get("action"), e.get("chip"), e.get("ok")) for e in self.session.recent_events(600)
                   if e.get("kind") == "read:user"]
        self.assertEqual(records, [("remove", "标靶", None), ("remove_undo", "标靶", True)])

    def test_undo_remove_expires_or_goes_stale(self):
        """「撤销」只在移出后 REMOVE_UNDO_MS（15 秒）内有：到点连同那一条收起。移出以后又记下了筹码（已有筹码有了别的变化）
        时下一次刷新就收起；按钮还没来得及收起就点了的，放不回、什么都不改，提示「无法撤销移出……」。又移出了别的：那一条换成
        新移出的那个，撤销只放回它。"""
        from advisor.ui.overlay_parts.wording import REMOVE_UNDO_MS

        w = self.window
        self.assertEqual(REMOVE_UNDO_MS, 15000)
        self.session.set_owned(["书签", "标靶"])
        self.assertTrue(w.remove_owned("标靶"))
        seq = w._removal_seq
        w._expire_removal(seq - 1)  # 别的一次的计时：不管
        self.assertIn("removed", w.notice_model.texts())
        w._expire_removal(seq)  # 到点
        self.assertNotIn("removed", w.notice_model.texts())
        # 移出以后又补录了一个：已有筹码变了，下一次刷新收起
        self.assertTrue(w.remove_owned("书签"))
        self.assertTrue(w.add_owned("大背包"))
        self.assertNotIn("removed", w.notice_model.texts())
        self.assertEqual(self.session.settings.owned, ["大背包"])
        # 赶上变化的那一下：点了也放不回
        self.session.set_owned(["书签", "标靶", "大背包"])
        self.assertTrue(w.remove_owned("标靶"))
        with w.lock:
            self.session.game.unrecorded = 1  # 识别线程刚记下一个待补录，界面还没刷新
        w.undo_remove()
        self.assertEqual(self.session.settings.owned, ["书签", "大背包"])
        self.assertIn("无法撤销移出「标靶」：之后已有筹码或对局有了变化", self.status())
        self.assertNotIn("removed", w.notice_model.texts())
        # 又移出了别的：只放回最近移出的那个
        self.session.game.unrecorded = 0
        self.assertTrue(w.remove_owned("书签"))
        self.assertTrue(w.remove_owned("大背包"))
        self.assertEqual(w.notice_model.texts()["removed"], "已从已有筹码中移出「大背包」")
        w.notices.buttons("removed")[0].click()
        self.assertEqual(self.session.settings.owned, ["大背包"])

    def test_owned_cards_extend_downward_then_scroll(self):
        """筹码越多，「已有筹码」卡片和整个悬浮窗越往下延伸（这里试到 10 个）；
        总高度超出所在屏幕的可用高度时，卡片主体改在卡片内滚动，其余部分不动。"""
        w = self.window
        w._screen_height = lambda: 5000
        w.set_expanded(True)
        names = ["普通瞄具", "速度轮滑-中级", "标靶", "大背包", "速度轮滑-高级", "美工刀-锋利", "医疗箱-紧急治疗",
                 "忍术飞镖", "安全气囊", "磨刀石"]
        heights = []
        for n in range(len(names) + 1):
            self.session.set_owned(names[:n])
            w.refresh()
            heights.append(w.height())
        self.assertEqual(heights, sorted(heights))
        self.assertGreater(heights[-1], heights[1] + 300)
        self.assertEqual(w.owned_scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)  # 屏幕够高：不滚动
        w.show()
        APP.processEvents()
        recog = w.recog_card.height()
        w._screen_height = lambda: 600
        w.refresh()
        APP.processEvents()
        self.assertLessEqual(w.height(), 600)
        self.assertEqual(w.owned_scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOn)
        self.assertLess(w.owned_scroll.height(), w.owned_body.heightForWidth(w._card_width()))
        self.assertGreaterEqual(w.owned_scroll.height(), w.chip_grid.sizeHint().height())
        self.assertEqual(w.recog_card.height(), recog)  # 其余部分不动

    def test_place_off_screen_position(self):
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        self.session.settings.window_pos = [-30000, -30000]
        self.window.follower.place()
        rect = self.window.frameGeometry()
        self.assertTrue(any(s.contains(rect) for s in screens), (rect, screens))

    def test_place_keeps_visible_position(self):
        s = QGuiApplication.primaryScreen().availableGeometry()
        pos = [s.left() + 10, s.top() + 20]
        self.session.settings.window_pos = pos
        self.window.follower.place()
        self.assertEqual([self.window.x(), self.window.y()], pos)

    def test_place_pulls_half_hidden_window_back(self):
        s = QGuiApplication.primaryScreen().availableGeometry()
        self.session.settings.window_pos = [s.right() - 100, s.top() + 20]
        self.window.follower.place()
        self.assertTrue(s.contains(self.window.frameGeometry()))

    def test_missed_pick_prompt(self):
        """三选一关了却没检测到选择了哪个：「已有筹码」卡片里列出候选筹码按钮，点一下补录（一行显示时也显示）。"""
        w = self.window
        self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.session.on_frame(None)
        ev = self.session.on_frame(None)
        self.assertEqual(ev.kind, "missed_pick")
        w.handle(ev)
        self.assertFalse(w.missed.isHidden())
        self.assertFalse(w.owned_card.isHidden())
        self.assertTrue(w.owned_scroll.isHidden() and w.owned_empty.isHidden())  # 一行显示时只显示提示
        self.assertIn("请点击下方对应的筹码按钮补录", w.missed_label.text())
        self.assertEqual(w.owned.missed_buttons_text(), ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗", "关闭本条提示"])
        button(w.missed, "友情徽章").click()
        self.assertEqual(self.session.settings.owned, ["友情徽章"])
        self.assertEqual(w.chip_grid.names(), ["友情徽章"])
        self.assertTrue(w.missed.isHidden())
        self.assertIn("已补录：友情徽章", self.status())
        self.assertIn("已补录：友情徽章", w.status.summary_plain)

    def test_missed_unknown_chip_is_not_a_button(self):
        """无法识别的候选只显示原文，不做成按钮。"""
        self.session.on_frame(frame([None, "友情徽章", "书签"], raw=["某某某", "友情徽章", "书签"]))
        self.session.on_frame(None)
        self.window.handle(self.session.on_frame(None))
        self.assertEqual(self.window.owned.missed_buttons_text(), ["「某某某」无法识别", "友情徽章", "书签", "关闭本条提示"])
        self.assertEqual([b.text() for b in buttons(self.window.missed)], ["友情徽章", "书签", "关闭本条提示"])

    def test_missed_prompt_survives_next_advice(self):
        """补录行不会被下一次三选一的推荐冲掉；点「关闭本条提示」才收起，且不动正在显示的推荐。"""
        self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.session.on_frame(None)
        self.window.handle(self.session.on_frame(None))
        ev = self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"]))
        self.window.handle(ev)
        self.assertFalse(self.window.body.isHidden())
        self.assertFalse(self.window.missed.isHidden())
        button(self.window.missed, "关闭本条提示").click()
        self.assertTrue(self.window.missed.isHidden())
        self.assertFalse(self.window.body.isHidden())
        self.assertEqual(self.session.settings.owned, [])
        self.assertIn("来源 升星或筹码商店", self.status())  # 候选筹码那一行没被改掉
        self.assertIn("刷新 ×2", flat(self.window.status.summary_plain))

    def test_fix_row_closes_when_offer_changes(self):
        """改正栏开着时刷新了：改正栏收起，不会改到新一组候选的同一格。"""
        self.window.handle(self.session.on_frame(frame(["标靶", "标记喷罐", "鹰眼瞄具"], rerolls=3)))
        self.window.advice.open_fix(1)  # 想改「标记喷罐」
        self.assertFalse(self.window.fix_row.isHidden())
        self.assertEqual(self.window.fix_label.text(), "第 2 个候选筹码改为")
        self.window.handle(self.session.on_frame(frame(["大背包", "缓冲盾牌", "书签"], rerolls=2)))
        self.assertTrue(self.window.fix_row.isHidden())
        self.assertIn("候选筹码已变化，改正栏已关闭", self.status())
        self.assertEqual(self.session.fixes, {})

    def test_fix_confirm_checks_offer_is_unchanged(self):
        """新的候选已经到了 Session、界面还没来得及处理时点了确定：不改，提示重新点；候选没换时照常改。"""
        self.window.handle(self.session.on_frame(frame(["标靶", "标记喷罐", "鹰眼瞄具"], rerolls=3)))
        self.window.advice.open_fix(1)
        self.session.on_frame(frame(["大背包", "缓冲盾牌", "书签"], rerolls=2))  # 事件还没送到界面
        self.window.fix_box.setEditText("魔法箭袋")
        self.window.advice.fix_confirm()
        self.assertEqual(self.session.last_seen.names, ["大背包", "缓冲盾牌", "书签"])
        self.assertEqual(self.session.fixes, {})
        self.assertIn("候选筹码已变化，本次改正未生效", self.status())
        self.window.advice.open_fix(1)
        self.window.fix_box.setEditText("魔法箭袋")
        self.window.advice.fix_confirm()
        self.assertEqual(self.session.last_seen.names, ["大背包", "魔法箭袋", "书签"])
        self.assertIn("候选筹码 第 2 个手动指定", self.status())
        self.assertIn("（手动指定）", shown(self.window, self.window.body))

    def test_fix_unknown_name(self):
        self.window.handle(self.session.on_frame(frame(["标靶", "标记喷罐", "鹰眼瞄具"])))
        self.window.advice.open_fix(0)
        self.window.fix_box.setEditText("完全不是筹码")
        self.window.advice.fix_confirm()
        self.assertIn("「完全不是筹码」不在筹码表中，请输入完整的筹码名或从列表中选择", self.status())

    def test_status_pills_name_subjects(self):
        """状态胶囊：数值自带单位的不写主体（第 3 轮、1 星），其余写明主体（主体暗色、数值亮色）；
        对局中待识别的标橙色，还没进入对局时用暗色。角色胶囊在状态胶囊里，不是按钮。角色待识别时不写职业（按上一局的
        角色算的不一定是这一局的），认出角色以后写。"""
        s = self.session.settings
        self.session.game.seen_hero = None
        self.window.refresh()
        text = self.status()
        for part in ("轮次 待识别", "星数 待识别", "地图 待识别"):
            self.assertIn(part, text)
        self.assertNotIn("职业 ", text)
        tones = {b.key: b.tone for b in self.window.status.shown_bits}
        self.assertEqual((tones["round"], tones["star"], tones["map"]), ("muted",) * 3)  # 还没进入对局
        self.assertEqual(self.window.hero_state.text(), "角色 待识别")
        self.assertNotIsInstance(self.window.hero_state, QPushButton)
        s.round = 3
        self.window.refresh()
        tones = {b.key: b.tone for b in self.window.status.shown_bits}
        self.assertEqual((tones["star"], tones["map"]), ("warn", "warn"))  # 对局中：橙色
        self.assertEqual(self.window.hero_state.tone, "warn")
        s.star, s.star_read = 1, True
        self.session.on_hero(SimpleNamespace(hero="凛"))
        self.window.refresh()
        text = self.status()
        self.assertIn("第 3 轮", text)
        self.assertIn("1 星", text)
        self.assertNotIn("轮次", text)
        self.assertNotIn("星数", text)
        self.assertEqual(self.window.hero_state.text(), "角色 已识别")
        self.assertTrue(self.window.hero_restore.isHidden())
        pill = next(p for p in self.window.status_box.findChildren(Pill) if p.text().startswith("职业"))
        self.assertEqual(pill._segments[0], ("职业 ", DIM_COLOR))
        self.assertNotEqual(pill._segments[1][1], DIM_COLOR)

    def test_hero_manual_has_restore_button(self):
        self.session.game.seen_hero = None
        self.session.on_hero(SimpleNamespace(hero="邦妮"))
        self.window.refresh()
        self.assertEqual(self.window.hero_state.text(), "角色 已识别")
        choose_hero(self.window, "凛")  # 用户手动选了凛
        self.assertEqual(self.window.hero_state.text(), "角色 手动指定")
        self.assertFalse(self.window.hero_restore.isHidden())
        self.assertIn("自动识别结果：「", tip_text(self.window.hero_state.toolTip()))
        self.window.set_expanded(True)
        self.window.show()
        APP.processEvents()
        state = self.window.hero_state
        self.assertEqual(state.height(), state.sizeHint().height())  # 一行写完，没有被挤成两行
        self.assertGreaterEqual(state.width(), state.sizeHint().width())
        self.window.hide()
        self.window.hero_restore.click()
        self.assertEqual((self.session.settings.hero, self.session.hero_source), ("邦妮", "auto"))
        self.assertEqual(self.window.hero_state.text(), "角色 已识别")
        self.assertTrue(self.window.hero_restore.isHidden())

    def test_reliable_map_shows_name_only(self):
        """任务栏匹配度高或中、加载画面、专属筹码、手动指定：都只显示地图名，不显示置信度，也不请用户选。"""
        cases = [
            (("异变图书馆", "tasks", "high"), "异变图书馆"),
            (("魔法学院", "tasks", "medium", ["魔法学院", "海选赛运动场"]), "魔法学院"),
            (("园林中庭", "loading", "high"), "园林中庭"),
            (("决赛大赛场", "chips", "high"), "决赛大赛场"),
        ]
        for args, name in cases:
            with self.subTest(args[1:3]):
                self.session._set_auto_map(*args)
                self.window.refresh()
                text = self.status()
                self.assertIn(name, text)
                self.assertEqual(self.window.map_box.display_text(), name)  # 地图胶囊只写地图名
                self.assertTrue(self.window.map_row.isHidden())
                for word in BANNED + ("匹配度", "来源"):
                    self.assertNotIn(word, text)
                self.assertEqual(self.window.map_box.currentText(), args[0])
        # 识别依据和任务重叠的说明放在悬停说明里
        self.session._set_auto_map("魔法学院", "tasks", "medium", ["魔法学院", "海选赛运动场"])
        self.window.refresh()
        tip = self.window.status.status_tip_text
        self.assertIn("依据任务栏中的任务文字识别", tip)
        self.assertIn("「魔法学院」的任务与「海选赛运动场」部分重叠", tip)
        self.assertIn("依据任务栏中的任务文字识别", self.window.map_box.toolTip())
        for word in BANNED:
            self.assertNotIn(word, tip)
        self.session.set_map("园林中庭")
        self.window.refresh()
        self.assertIn("园林中庭（手动指定）", self.status())
        self.assertIn("「地图：自动识别」", self.window.status.status_tip_text)  # 恢复自动识别的方法写明下拉框里的那一项
        self.assertIn("「识别」卡片中的地图下拉框", self.window.status.status_tip_text)  # 写明在哪里
        # 下拉框选第一项「地图：自动识别」：交回自动识别
        self.window.map_box.setCurrentIndex(0)
        self.assertEqual(self.window.map_box.currentText(), "地图：自动识别")
        self.assertIsNone(self.session.settings.map)
        self.assertIsNone(self.session.map_source)
        self.assertIn("地图 待识别", self.status())

    def test_map_ask_offers_candidate_buttons(self):
        """几张地图的匹配度都低：提示语一字不差，候选地图做成按钮放在提示语右侧，点一下记为手动指定。"""
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        self.window.refresh()
        self.assertFalse(self.window.map_row.isHidden())
        self.assertFalse(self.window.recog_card.isHidden())  # 地图选择行在「识别」卡片里，一行显示时也显示
        self.assertEqual(self.window.map_prompt.text(), MAP_ASK)
        self.assertEqual(MAP_ASK, "请点击右侧按钮选择当前地图")
        self.assertEqual(self.window.scheme.map_buttons_text(), ["魔法学院", "海选赛运动场"])
        self.assertIn("地图 无法确定", self.status())
        self.assertEqual(self.window.map_box.currentText(), "地图：自动识别")  # 下拉框也写明主体
        self.window.show()
        APP.processEvents()
        prompt = self.window.map_prompt.geometry()
        for b in buttons(self.window.map_row):
            self.assertEqual(b.property("accent"), "map")  # 有底色和框线的样式
            self.assertGreaterEqual(b.geometry().left(), prompt.right())  # 在提示语右侧
        self.window.hide()
        button(self.window.map_row, "海选赛运动场").click()
        self.assertEqual(self.session.settings.map, "海选赛运动场")
        self.assertEqual(self.session.map_source, "manual")
        self.assertTrue(self.window.map_row.isHidden())
        self.assertIn("地图已手动指定为「海选赛运动场」", self.status())

    def test_map_ask_many_candidates_still_on_the_right(self):
        self.session.game.map_candidates = ["海选赛运动场", "淘汰赛运动场", "决赛大赛场", "龙宫游乐园"]
        self.session.game.map_ask = True
        self.window.refresh()
        self.window.show()
        APP.processEvents()
        prompt = self.window.map_prompt.geometry()
        self.assertEqual(len(buttons(self.window.map_row)), 4)
        for b in buttons(self.window.map_row):
            self.assertGreaterEqual(b.geometry().left(), prompt.right())
            self.assertLessEqual(b.geometry().right(), self.window.map_row.width())  # 没有超出窗口
        self.window.hide()

    def test_map_unknown_asks_to_open_task_panel(self):
        """还没读到任务栏：提示打开任务栏（手动模式下还要按热键），不列候选地图。
        进入对局（读到轮次）之后才显示这一行，一行显示、全部展开都一样（还没进对局时打不开任务栏）。"""
        self.window.refresh()
        self.assertTrue(self.window.map_row.isHidden())  # 还没进对局：一行显示时不占地方
        self.window.set_expanded(True)
        self.assertTrue(self.window.map_row.isHidden())  # 全部展开时也不显示
        self.session.settings.round = 1
        self.window.refresh()
        self.assertFalse(self.window.map_row.isHidden())
        self.window.set_expanded(False)
        self.assertFalse(self.window.map_row.isHidden())
        self.assertEqual(self.window.map_prompt.text(), OPEN_TASKS)
        self.assertEqual(self.window.scheme.map_buttons_text(), [])
        self.window.set_manual(True)
        self.assertEqual(self.window.map_prompt.text(), f"请打开游戏中的任务栏，然后按 {self.window.hotkeys.hotkey}")

    def test_advice_line_shows_source(self):
        """三选一显示期间，状态胶囊写出这次三选一的来源和剩余刷新次数；星数没读到或和记下的不同时写出按几星计算。"""
        names = ["标靶", "大背包", "缓冲盾牌"]
        st = State(hero="凛", star=0, rerolls=2)
        self.window.handle(Event("advice", advice=advise(self.db, st, names), state=st))
        text = self.status()
        for part in ("来源 升星或筹码商店", "按 0 星计算", "刷新 ×2"):
            self.assertIn(part, text)
        self.assertNotIn("候选筹码", text)  # 三个都识别了、没有改正：不另写
        self.session.settings.star, self.session.settings.star_read = 1, True
        st = State(hero="凛", star=1, source="task", tier=2, rerolls=2)
        self.window.handle(Event("advice", advice=advise(self.db, st, names), state=st))
        text = self.status()
        self.assertIn("来源 任务（2 级）", text)
        self.assertIn("刷新 ×2", text)
        self.assertIn("1 星", text)
        self.assertNotIn("星计算", text)
        # 胶囊的顺序：第几轮、星数、刷新次数、地图、来源
        keys = [b.key for b in self.window.status.shown_bits]
        self.assertLess(keys.index("star"), keys.index("rerolls"))
        self.assertLess(keys.index("rerolls"), keys.index("map"))
        self.assertLess(keys.index("map"), keys.index("source"))

    def test_unread_rerolls_bit(self):
        """刷新次数没读出来、按「一次都没用过」补的（State.rerolls_known 为 False）：状态胶囊、一行显示的摘要、「识别」卡片
        收起时的摘要都和读不到其他数字时一样写橙色的「刷新次数 待识别」，不写估计的次数；状态胶囊和摘要的悬停说明写按几次
        估计、不按多出来的刷新算（和「消耗 1 次刷新」悬停的开头同一句）。读到了照旧写「刷新 ×N」，悬停里没有这一句。"""
        from advisor.ui.text import UNREAD_COST_TIP
        names = ["标靶", "大背包", "缓冲盾牌"]
        st = State(hero="凛", star=2, round=6, rerolls=4, rerolls_known=False)
        self.window.handle(Event("advice", advice=advise(self.db, st, names), state=st))
        text = self.status()
        self.assertIn("刷新次数 待识别", text)
        self.assertNotIn("刷新 ×", text)
        bit = next(b for b in self.window.status.shown_bits if b.key == "rerolls")
        self.assertEqual((bit.value, bit.subject, bit.tone), ("待识别", "刷新次数", "warn"))
        tip = UNREAD_COST_TIP.format(rerolls=4)
        self.assertEqual(tip, "刷新次数没读出来，按一次都没用过估计为 4 次，不按多出来的刷新算。")
        self.assertIn(tip, self.window.status.status_tip_text.split("\n"))
        self.assertIn(tip, self.window.status.summary_tip_text)
        self.assertIn("刷新次数 待识别", self.window.status.summary_plain)
        self.assertIn("刷新次数 待识别", "".join(t for t, _ in self.window._recog_summary()))
        # 读到了：照旧写次数，悬停里没有这一句
        st = State(hero="凛", star=2, round=6, rerolls=4)
        self.window.handle(Event("advice", advice=advise(self.db, st, names), state=st))
        self.assertIn("刷新 ×4", self.status())
        self.assertNotIn("刷新次数没读出来", self.window.status.status_tip_text)

    def test_unread_rerolls_bit_no_later_picks(self):
        """刷新次数没读出来、本次之后预计没有筹码三选一（3 星、到了进度上限）：剩余刷新一次都不留、全部可以连着用，悬停照实写
        （UNREAD_FREE_TIP），不写「不按多出来的刷新算」；那一格照旧是橙色的「刷新次数 待识别」。"""
        from advisor.ui.text import UNREAD_FREE_TIP
        names = ["标靶", "大背包", "缓冲盾牌"]
        st = State(hero="凛", star=3, round=16, map="魔法学院", rerolls=6, rerolls_known=False)
        adv = advise(self.db, st, names)
        self.assertEqual((adv.picks.total, adv.reroll.kept, adv.reroll.spare), (0, 0, 6))
        self.window.handle(Event("advice", advice=adv, state=st))
        tip = UNREAD_FREE_TIP.format(rerolls=6)
        self.assertEqual(tip, "刷新次数没读出来，按一次都没用过估计为 6 次；本次之后预计没有筹码三选一，剩余刷新可以连续使用。")
        self.assertIn(tip, self.window.status.status_tip_text.split("\n"))
        self.assertIn(tip, self.window.status.summary_tip_text)
        self.assertNotIn("不按多出来的刷新算", self.window.status.status_tip_text)
        self.assertIn("刷新次数 待识别", self.status())

    def test_unreadable_candidates(self):
        """有候选筹码无法识别：状态胶囊写明第几个，推荐卡片列出三个候选筹码（都是按钮），无法识别的标橙色。"""
        seen = frame([None, "友情徽章", "书签"], raw=["某某某", "友情徽章", "书签"],
                     problems=["第 1 个候选筹码无法识别（识别文字：「某某某」）"])
        self.window.handle(unreadable(self.session, seen))  # 三选一刚出现时先等一等，等满了才提示
        self.assertIn("候选筹码 第 1 个无法识别", self.status())
        body = shown(self.window, self.window.body)
        self.assertIn("「某某某」无法识别", body)
        self.assertIn("请点击标为橙色的筹码名按钮进行改正", body)
        self.assertNotIn("⚠ 第 1 个", body)  # 已经在列表里标出来了，不再重复提示
        names = name_buttons(self.window)
        self.assertEqual(len(names), 3)
        self.assertIn("rgba(255,184,107", names[0].styleSheet())  # 橙色框线
        names[0].click()
        self.assertFalse(self.window.fix_row.isHidden())
        self.assertEqual(self.window.fix_label.text(), "第 1 个候选筹码改为")

    # ---- 识别热键：在「拾星助手设置」窗口里更换（键帽和「更改」），更换的过程和结果写在「识别热键」的说明处

    def settings(self, window=None) -> SettingsDialog:
        """打开设置窗口（离屏平台上显示也不会出现在桌面上），测试结束时关掉。"""
        w = window or self.window
        w.dialogs.show_settings()
        self.addCleanup(w.dialogs.settings.close)
        return w.dialogs.settings

    def test_hotkey_unavailable(self):
        """热键登记不上（被其他程序占用）：标题栏下方一条提示（短句由热键线程给，⚠ 橙色、边框不变色），带「更换热键」和
        「关闭本条提示」；设置窗口里键帽橙色框线，说明写原因和「更改」按钮。占用它的程序退出、热键登记上了时那一条收起。"""
        w = self.window
        d = self.settings()
        self.assertEqual((d.keycap.text(), d.keycap.tone), (w.hotkeys.hotkey, ""))
        text = startup_busy_text("F8")
        self.assertEqual(text, "热键 F8 已被其他程序占用，热键识别不可用。")
        w.on_hotkey_failed(text)
        self.assertEqual(w.notice_model.texts()["hotkey"], "⚠ " + text)
        self.assertEqual([b.text() for b in w.notices.buttons("hotkey")], ["更换热键", "关闭本条提示"])
        self.assertEqual(tip_text(w.notices.buttons("hotkey")[0].toolTip()),
                         "打开「拾星助手设置」窗口，按下新的组合键更换热键")
        self.assertIn(text, shown(w, w.global_area))  # 一行显示时也看得到
        self.assertFalse(w.needs_attention())
        self.assertNotIn(text, self.status())  # 不再占一次性提示
        self.assertEqual((d.keycap.text(), d.keycap.tone), ("F8", "warn"))
        self.assertEqual(d.rows["hotkey"].desc.text(), "热键 F8 已被其他程序占用，热键识别不可用。请点击「更改」按钮更换热键。")
        w.on_hotkey_restored("F8")  # 占用它的程序退出了
        self.assertEqual((d.keycap.tone, d.rows["hotkey"].desc.text()), ("", HOTKEY_DESC))
        self.assertNotIn("hotkey", w.notice_model.texts())

    def test_hotkey_change_button(self):
        """热键不可用那一条的「更换热键」：打开「拾星助手设置」窗口，进入等按新热键的状态（同「更改」）；按下的组合键交给
        热键线程，换成了以后那一条收起。写法无效的那一句同样带按钮；「关闭本条提示」关掉以后同一句不再出现。"""
        w = self.window
        requested = []
        w.hotkey_change_requested.connect(requested.append)
        w.on_hotkey_failed(startup_busy_text("F8"))
        w.notices.buttons("hotkey")[0].click()
        d = w.dialogs.settings
        self.addCleanup(d.close)
        self.assertTrue(d.isVisible())
        self.assertTrue(w.hotkeys.capturing)
        self.assertEqual((d.keycap.text(), d.change_btn.text()), ("请按键", "取消"))
        QTest.keyClick(d.change_btn, Qt.Key_F9, Qt.ControlModifier)
        self.assertEqual(requested, ["Ctrl+F9"])
        w.on_hotkey_changed("Ctrl+F9")
        self.assertNotIn("hotkey", w.notice_model.texts())
        records = [e.get("action") for e in self.session.recent_events(600) if e.get("kind") == "read:user"]
        self.assertIn("change_hotkey", records)
        invalid = "热键「Ctrl+?」无效：「?」不是可用的按键（可用 F1~F24、字母、数字、Pause 等）。"
        w.on_hotkey_failed(invalid)
        self.assertEqual(w.notice_model.texts()["hotkey"], "⚠ " + invalid)
        self.assertEqual([b.text() for b in w.notices.buttons("hotkey")], ["更换热键", "关闭本条提示"])
        w.notices.buttons("hotkey")[1].click()  # 关掉
        self.assertNotIn("hotkey", w.notice_model.texts())
        w.on_hotkey_failed(invalid)  # 同一句：不再出现
        self.assertNotIn("hotkey", w.notice_model.texts())
        w.on_hotkey_failed(startup_busy_text("Ctrl+F9"))  # 别的问题：照常出现
        self.assertIn("hotkey", w.notice_model.texts())

    def test_long_hotkey_in_settings(self):
        """较长的热键在设置窗口的键帽里完整写出（不省略、不被挤掉）；标题栏只有标题、「全部折叠」、×，不受热键影响。"""
        self.window.on_hotkey_changed("Ctrl+Shift+F12")
        d = self.settings()
        APP.processEvents()
        self.assertEqual(d.keycap.text(), "Ctrl+Shift+F12")
        self.assertGreaterEqual(d.keycap.width(), d.keycap.sizeHint().width())
        self.assertLessEqual(d.change_btn.mapTo(d, QPoint(d.change_btn.width(), 0)).x(), d.width())
        self.window.set_expanded(True)
        self.window.show()
        APP.processEvents()
        self.assertTrue(self.window.title.isVisible())
        for w in (self.window.toggle_btn, self.window.close_btn):
            self.assertTrue(w.isVisible())
            self.assertGreaterEqual(w.width(), w.sizeHint().width(), w.text())
            self.assertLessEqual(w.geometry().right(), self.window.panel.width())
        self.window.hide()

    def test_hotkey_capture_and_change(self):
        """设置窗口点「更改」→ 键帽「请按键」、按钮「取消」、说明「请按下新的热键；按 Esc 取消」→ 按 Ctrl+F9 → 交给热键线程
        →「正在设置热键 Ctrl+F9…」→ 成功后「热键已保存：Ctrl+F9」，存进 state.json。这些都写在「识别热键」的说明处，标题栏下方的
        提示不变；手动模式那块方块的说明跟着写新热键；关了再打开时说明恢复平时的；重启后用保存的热键。"""
        with tempfile.TemporaryDirectory(dir=sandbox.work_dir()) as tmp:  # 临时文件放在测试沙箱里
            path = Path(tmp) / "state.json"
            session = OverlaySession(self.db, UiSettings(hero="凛"), path)
            window = Overlay(self.db, session, threading.Lock())
            requested = []
            window.hotkey_change_requested.connect(requested.append)
            d = self.settings(window)
            before = window.status.status_text()
            d.change_btn.click()
            self.assertTrue(window.hotkeys.capturing)
            self.assertEqual((d.keycap.text(), d.keycap.tone, d.change_btn.text()), ("请按键", "capture", "取消"))
            self.assertEqual(d.rows["hotkey"].desc.text(), HOTKEY_WAIT)
            self.assertEqual(HOTKEY_WAIT, "请按下新的热键；按 Esc 取消")
            self.assertEqual(tip_text(d.change_btn.toolTip()), HOTKEY_WAIT_TIP)
            QTest.keyClick(d.change_btn, Qt.Key_Control, Qt.ControlModifier)  # 只按修饰键：继续等
            self.assertTrue(window.hotkeys.capturing)
            QTest.keyClick(d.change_btn, Qt.Key_F9, Qt.ControlModifier)
            self.assertFalse(window.hotkeys.capturing)
            self.assertEqual(requested, ["Ctrl+F9"])
            self.assertEqual(window.hotkeys.hotkey, "F8")  # 热键线程确认前不改
            self.assertEqual(d.rows["hotkey"].desc.text(), "正在设置热键 Ctrl+F9…")
            window.on_hotkey_changed("Ctrl+F9")
            self.assertEqual(window.hotkeys.hotkey, "Ctrl+F9")
            self.assertEqual((d.keycap.text(), d.change_btn.text()), ("Ctrl+F9", "更改"))
            self.assertEqual(d.rows["hotkey"].desc.text(), "热键已保存：Ctrl+F9")
            self.assertEqual(d.mode_tiles[True].desc.text(), "后台不截图，仅在按 Ctrl+F9 时识别")
            self.assertEqual(window.status.status_text(), before)
            self.assertEqual(UiSettings.load(path).hotkey, "Ctrl+F9")
            d.close()
            window.dialogs.show_settings()
            self.assertIs(window.dialogs.settings, d)  # 关了再开用同一个窗口
            self.assertEqual(d.rows["hotkey"].desc.text(), HOTKEY_DESC)
            # 重启后用保存的热键
            again = Overlay(self.db, OverlaySession(self.db, UiSettings.load(path), path), threading.Lock())
            self.assertEqual(again.hotkeys.hotkey, "Ctrl+F9")

    def test_hotkey_capture_escape_and_invalid_key(self):
        """按了不能用的键：说明处写原因、接着等；Esc 取消：说明恢复平时的，标题栏下方的提示从头到尾不变；
        等待时再点一次按钮（「取消」）也是取消。"""
        d = self.settings()
        requested = []
        self.window.hotkey_change_requested.connect(requested.append)
        before = self.status()
        d.change_btn.click()
        QTest.keyClick(d.change_btn, Qt.Key_Tab)
        self.assertTrue(self.window.hotkeys.capturing)
        self.assertTrue(d.rows["hotkey"].desc.text().startswith("「Tab」不能用作热键"), d.rows["hotkey"].desc.text())
        self.assertEqual(self.status(), before)
        QTest.keyClick(d.change_btn, Qt.Key_Escape)
        self.assertFalse(self.window.hotkeys.capturing)
        self.assertTrue(d.isVisible())  # Esc 只取消等待，不关窗口
        self.assertEqual(d.rows["hotkey"].desc.text(), HOTKEY_DESC)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.window.message.area, "advice")
        self.assertEqual(requested, [])
        self.assertEqual((d.keycap.text(), d.change_btn.text()), ("F8", "更改"))
        d.change_btn.click()
        d.change_btn.click()
        self.assertFalse(self.window.hotkeys.capturing)
        # 等待时关了窗口：不再等待
        d.change_btn.click()
        d.close()
        self.assertFalse(self.window.hotkeys.capturing)

    def test_hotkey_capture_same_key(self):
        d = self.settings()
        requested, captured = [], []
        self.window.hotkey_change_requested.connect(requested.append)
        self.window.capture_requested.connect(lambda: captured.append(1))
        d.change_btn.click()
        QTest.keyClick(d.change_btn, Qt.Key_F8)
        self.assertEqual(requested, [])
        self.assertEqual(d.rows["hotkey"].desc.text(), "热键未更改：F8")
        # 设置中按下的正是当前热键（系统交给了热键线程）：不换，也不触发识别
        d.change_btn.click()
        self.window.on_hotkey()
        self.assertFalse(self.window.hotkeys.capturing)
        self.assertEqual(captured, [])
        self.assertEqual(d.rows["hotkey"].desc.text(), "热键未更改：F8")
        self.assertNotIn("热键未更改", self.status())

    def test_hotkey_busy_keeps_old(self):
        """新热键被占用：保留旧热键。设置窗口在前台时原因写在说明处、回到等待按键；不在前台时标题栏下方提示（写明到设置窗口里
        点「更改」重新设置）。"""
        d = self.settings()
        d.change_btn.click()
        QTest.keyClick(d.change_btn, Qt.Key_F9, Qt.ControlModifier)
        self.window.on_hotkey_change_failed("Ctrl+F9", busy_text("Ctrl+F9"), active=True)
        self.assertEqual(self.window.hotkeys.hotkey, "F8")
        self.assertTrue(self.window.hotkeys.capturing)
        self.assertEqual(d.rows["hotkey"].desc.text(), "热键 Ctrl+F9 已被其他程序占用，请更换")
        self.assertEqual(d.keycap.text(), "请按键")
        self.assertNotIn("已被其他程序占用", self.status())
        QTest.keyClick(d.change_btn, Qt.Key_Escape)
        self.assertEqual(d.keycap.text(), "F8")
        self.window.on_hotkey_change_failed("Ctrl+F9", busy_text("Ctrl+F9"), active=False)
        self.assertFalse(self.window.hotkeys.capturing)
        text = HOTKEY_CHANGE_FAILED.format(reason=busy_text("Ctrl+F9"), key="F8")
        self.assertEqual(text, "热键 Ctrl+F9 已被其他程序占用，请更换（当前热键仍为 F8）。请点击「设置」窗口中的「更改」按钮重新设置")
        self.assertIn(text, self.status())
        self.assertEqual(d.rows["hotkey"].desc.text(), "热键 Ctrl+F9 已被其他程序占用，请更换")

    def test_hotkey_capture_numpad(self):
        """设置热键时按小键盘数字键：记为「Num5」，不提示「「5」不能用作热键」。"""
        d = self.settings()
        requested = []
        self.window.hotkey_change_requested.connect(requested.append)
        d.change_btn.click()
        QTest.keyClick(d.change_btn, Qt.Key_5, Qt.KeypadModifier)
        self.assertEqual(requested, ["Num5"])
        self.assertNotIn("不能用作热键", d.rows["hotkey"].desc.text())

    def test_prompts_keep_chip_line(self):
        """三选一显示期间，热键、地图等一次性提示不会挤掉候选筹码那一行：来源和剩余刷新次数一直在状态胶囊里；
        一行显示时提示接在摘要里候选筹码那一行的后面。更换热键的过程写在设置窗口里，不进摘要。"""
        offer = frame(["标靶", "大背包", "缓冲盾牌"], rerolls=3)
        self.window.handle(self.session.on_frame(offer))
        chip = "来源 升星或筹码商店"

        def check(note: str | None):
            text = self.status()
            self.assertIn(chip, text)
            self.assertIn("刷新 ×3", text)
            summary = flat(self.window.status.summary_plain)
            self.assertIn("刷新 ×3", summary)
            if note:
                self.assertIn(note, text)
                self.assertGreater(summary.index(note), summary.index("刷新 ×3"))  # 接在后面

        d = self.settings()
        d.change_btn.click()
        check(None)
        QTest.keyClick(d.change_btn, Qt.Key_F9, Qt.ControlModifier)
        self.window.on_hotkey_changed("Ctrl+F9")
        check(None)
        self.assertTrue(flat(self.window.status.summary_plain).endswith("刷新 ×3"))
        self.window.on_hotkey()
        check("已按下 Ctrl+F9，正在识别…")
        # 同一个三选一界面再识别一次：Session 不发事件，靠 Watcher.capture_done 收起「正在识别…」
        self.assertIsNone(self.session.on_frame(offer))
        self.window.on_capture_done()
        check(None)
        self.assertNotIn("正在识别", self.status())
        self.assertTrue(flat(self.window.status.summary_plain).endswith("刷新 ×3"))
        self.window._choose_map("魔法学院")
        check("地图已手动指定为「魔法学院」")
        # 下一次三选一出推荐：一次性提示清掉，只留新的候选筹码那一行。最左格放蓝色的标记喷罐：升星、筹码商店三选一的
        # 最左格是这个星数的固定稀有度（0、1 星为蓝色），放紫色的书签就会按任务来源判断
        self.window.handle(self.session.on_frame(frame(["标记喷罐", "书签", "鹰眼瞄具"], rerolls=3)))
        self.assertNotIn("地图已手动指定", self.status())
        check(None)

    def test_capture_done_after_new_result_keeps_it(self):
        """按热键后识别出了新的结果（事件先到）：capture_done 不再改提示；不是三选一界面时恢复等待提示。"""
        self.window.on_hotkey()
        self.window.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        before = self.status()
        self.window.on_capture_done()
        self.assertEqual(self.status(), before)
        self.window.handle(Event("idle"))  # 三选一界面关了
        self.window.on_hotkey()
        self.window.set_flash_status("已截图识别：当前画面不是筹码三选一界面")  # Watcher 在 capture_done 之前发来的
        self.window.on_capture_done()
        self.assertIn("已截图识别：当前画面不是筹码三选一界面", self.status())

    def test_advice_during_hotkey_capture(self):
        """设置热键时来了新的推荐：照常显示推荐，设置窗口里接着等待按键；按 Esc 取消后悬浮窗的提示不受影响。"""
        self.assertIn("等待筹码三选一界面", self.status())
        d = self.settings()
        d.change_btn.click()
        self.window.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"], rerolls=3)))
        self.assertTrue(self.window.hotkeys.capturing)
        self.assertEqual(d.rows["hotkey"].desc.text(), HOTKEY_WAIT)
        self.assertIn("来源 升星或筹码商店", self.status())
        self.assertNotIn("请按下新的热键", self.status())
        QTest.keyClick(d.change_btn, Qt.Key_Escape)
        self.assertNotIn("等待筹码三选一界面", self.status())
        self.assertTrue(flat(self.window.status.summary_plain).endswith("刷新 ×3"))

    def test_manual_mode_saved_in_settings(self):
        """切换识别模式（设置窗口里，set_manual）：存进 state.json；标题栏下方提示一次；「推荐」卡片的等待提示照旧。"""
        w = self.window
        w.set_manual(True)
        self.assertTrue(self.session.settings.manual)
        self.assertTrue(w.manual)
        self.assertIn(MANUAL_SWITCHED.format(key="F8"), self.status())
        self.assertEqual(MANUAL_SWITCHED.format(key="F8"), "已切换到手动模式：后台不截图，仅在按 F8 时识别。")
        w.set_expanded(True)
        self.assertIn("手动模式：后台不截图。出现筹码三选一界面时，请按 F8。", shown(w, w.advice_card))
        self.assertIn("manual", UiSettings.__dataclass_fields__)
        w.set_manual(False)
        self.assertIn(AUTO_SWITCHED, self.status())
        self.assertIn("已切换到自动模式", self.status())

    def test_new_game_message(self):
        self.window.handle(Event("newgame", why="检测到选角界面"))
        self.assertIn("已判定为新的一局（依据：检测到选角界面）", self.status())

    def test_clickable_elements_have_frame(self):
        """所有按钮都有框线（STYLE 里统一设置）；推荐卡片的筹码名按钮带稀有度颜色的框线；地图胶囊是下拉框；
        不能点的（角色状态、状态胶囊、理由标签、提示）不做成按钮。"""
        style = self.window.styleSheet()
        rule = re.search(r"QPushButton \{([^}]*)\}", style).group(1)
        self.assertRegex(rule, r"border: 1px solid")
        self.assertRegex(rule, r"background:")
        for widget in (self.window.hero_state, self.window.status_box, self.window.map_prompt):
            self.assertNotIsInstance(widget, QPushButton)
        self.assertIsInstance(self.window.map_box, QComboBox)  # 地图胶囊点开是下拉列表
        self.window.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        names = name_buttons(self.window)
        self.assertEqual(sorted(b.text().lstrip("★ ") for b in names), sorted(["标靶", "大背包", "缓冲盾牌"]))
        for b in names:
            self.assertIn("border-color", b.styleSheet())
        self.assertTrue(self.window.body.findChildren(Pill))
        for pill in self.window.body.findChildren(Pill):
            self.assertNotIsInstance(pill, QPushButton)
        # 有候选筹码无法识别时，三个候选筹码也都是按钮
        self.window.handle(self.session.on_frame(frame([None, "书签", "标靶"], raw=["某某某", "书签", "标靶"])))
        self.assertEqual(len(name_buttons(self.window)), 3)

    def test_reasons_fit_in_window(self):
        st = State(hero="凛", star=1, round=3, rerolls=1)
        ev = Event("advice", advice=advise(self.db, st, ["医疗箱-紧急治疗", "书签", "能量回收"]), state=st)
        self.window.handle(ev)
        self.window.show()
        APP.processEvents()
        body = self.window.body
        self.assertTrue(fits(body))  # 最后一行理由没被截掉
        for pill in body.findChildren(Pill):
            self.assertTrue(fits(pill), pill.text())  # 长理由在标签内换行
            self.assertLessEqual(pill.mapTo(body, QPoint(pill.width(), 0)).x(), body.width())  # 不超出卡片
        self.window.hide()

    def test_all_rows_fit_before_show(self):
        """推荐、地图选择行、补录行同时出现，而且是在窗口显示之前变的：每块都不能被截掉。"""
        self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.session.on_frame(None)
        self.window.handle(self.session.on_frame(None))
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        self.session.set_owned(["书签", "标靶"])
        self.session.game.missed_offer = ["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]  # set_owned 清掉了，接回来
        self.window.handle(self.session.on_frame(frame(["标记喷罐", "额外电池", "探天卫星"])))
        self.window.set_expanded(True)  # 全部展开：状态胶囊、已有筹码也显示
        self.window.show()
        APP.processEvents()
        w = self.window
        for widget in (w.status_box, w.map_row, w.map_prompt, w.body, w.missed, w.missed_label, w.owned_body):
            self.assertFalse(widget.isHidden())
            self.assertTrue(fits(widget), type(widget).__name__)
        for widget in w.panel.findChildren(QWidget):
            if widget.isVisible() and isinstance(widget, (Pill, TaggedText)):
                self.assertTrue(fits(widget), widget.text())
        for b in w.findChildren(QPushButton):
            if b.isVisible():
                self.assertGreaterEqual(b.width(), b.sizeHint().width(), b.text())  # 按钮文字没被截掉
        self.window.hide()

    def test_no_colloquial_words_in_ui(self):
        """几种常见状态下，各张卡片、摘要、按钮和各个悬停说明里都不出现口语和置信度字样。"""
        w = self.window
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        self.session.on_frame(frame(["小猪存钱罐", "友情徽章", "医疗箱-紧急治疗"]))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        w.set_manual(True)
        w.set_expanded(True)
        texts = [w.status.status_text(), w.status.summary_plain, *w.visible_texts(),
                 *(tip_text(x.toolTip()) for x in (w.status_box, w.map_prompt, w.map_box, w.hero_box, w.hero_state,
                                                   w.settings_btn, w.edit_btn, w.share_btn, w.body, w.add_btn,
                                                   w.summary)),
                 *(card.toggle.toolTip() for card in w.cards.values()),
                 *w.owned.missed_buttons_text(), *w.scheme.map_buttons_text()]
        for text in texts:
            for word in BANNED:
                self.assertNotIn(word, text)


class OverlayModeTest(unittest.TestCase):
    """平时一行、三选一时显示推荐卡片、关闭后收回一行（摘要不写上一次推荐；全部展开时在「推荐」卡片里
    回看）、点标题栏的「全部展开」全部显示。"""

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.session.game.seen_hero = "凛"
        self.logbook = FakeLogbook()
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook))
        self.window.run_background = lambda fn: fn()

    def tearDown(self):
        self.window.hide()

    def test_map_hint_can_be_dismissed(self):
        """进入对局、地图待识别时，一行显示也出现「打开任务栏」的提示，右侧有「关闭本条提示」：点了这一局不再提示，
        新的一局照常提示。"""
        w, s = self.window, self.session
        s.settings.round = 3
        w.refresh()
        self.assertTrue(w.map_row.isVisibleTo(w.panel))
        self.assertIn("打开任务栏", w.map_prompt.text())
        self.assertTrue(w.map_dismiss.isVisibleTo(w.panel))
        self.assertEqual(w.map_dismiss.text(), "关闭本条提示")
        w.map_dismiss.click()
        self.assertFalse(w.map_row.isVisibleTo(w.panel))
        s.settings.round = 4
        w.refresh()
        self.assertFalse(w.map_row.isVisibleTo(w.panel))  # 这一局不再提示
        s.new_game()
        s.settings.round = 1
        w.refresh()
        self.assertTrue(w.map_row.isVisibleTo(w.panel))  # 新的一局照常提示

    def test_drag_by_text(self):
        """按在文字上也能拖动悬浮窗：一行显示时标题栏的「等待筹码三选一界面…」、卡片头的摘要（富文本标签默认会接走鼠标）。"""
        w = self.window
        w.show()
        QApplication.processEvents()

        def drag(widget) -> QPoint:
            w.move(300, 300)
            QApplication.processEvents()
            start = widget.rect().center()
            QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, start)
            for k in range(1, 6):
                QTest.mouseMove(widget, start + QPoint(10 * k, 5 * k))
            QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, start + QPoint(50, 25))
            QApplication.processEvents()
            return w.pos()

        self.assertIn("等待筹码三选一界面", w.summary.text())
        moved = drag(w.summary)
        self.assertTrue(moved.x() > 300 and moved.y() > 300, moved)
        w.toggle_expanded()  # 全部展开：卡片头的区域名
        QApplication.processEvents()
        moved = drag(w.recog_card.title)
        self.assertTrue(moved.x() > 300 and moved.y() > 300, moved)
        for label in w.findChildren(QLabel):
            with self.subTest(label=label.objectName() or label.text()[:20]):
                self.assertEqual(label.textInteractionFlags(), Qt.NoTextInteraction)

    def test_one_line_by_default(self):
        w = self.window
        self.assertFalse(w.expanded)
        self.assertFalse(w.summary.isHidden())
        self.assertNotIn("\n", w.status.summary_plain)  # 平时只有一行
        for widget in (w.status_box, w.toolbar, w.body, w.title, w.head_spacer, *w.cards.values()):
            self.assertTrue(widget.isHidden(), widget)
        self.assertEqual(w.toggle_btn.text(), "全部展开")
        # 还没进入对局：轮次、星数、地图都是待识别，摘要里不列
        self.assertEqual(w.status.summary_plain, "等待筹码三选一界面…")

    def test_expands_on_chip_select_and_keeps_summary(self):
        w = self.window
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer, rerolls=3)))
        self.assertTrue(w.live_state.on)
        self.assertFalse(w.body.isHidden())
        self.assertTrue(w.card_open("advice"))
        self.assertTrue(w.recog_card.isHidden() and w.owned_card.isHidden())  # 一行显示：只有推荐卡片
        self.assertIn("刷新 ×3", flat(w.status.summary_plain))
        best = w.advice.last_ev.advice.best
        self.assertIn(best, offer)
        # 选了首推：收回一行；选的是首推，不问原因。摘要不写「上一次推荐」，还没读到轮次、星数时是等待提示
        self.session.on_frame(frame(offer, rerolls=3, selected=offer.index(best)))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        self.assertFalse(w.live_state.on)
        self.assertTrue(w.body.isHidden())
        w.handle(Event("idle"))
        self.assertNotIn("上一次推荐", w.status.summary_plain)
        self.assertEqual(w.status.summary_plain, "等待筹码三选一界面…")
        self.assertTrue(w.advice_card.isHidden())
        # 点标题栏有框线的「全部展开」按钮：回看上一次推荐，筹码名不再是按钮
        self.assertEqual(w.toggle_btn.text(), "全部展开")
        self.assertIn("可在「推荐」卡片中回看上一次推荐", tip_text(w.toggle_btn.toolTip()))
        w.toggle_btn.click()
        self.assertTrue(w.expanded)
        self.assertFalse(w.stale.isHidden())
        advice = shown(w, w.advice_card)
        self.assertIn("上一次推荐（筹码三选一界面已关闭）", advice)
        self.assertIn(f"已选：{best}（首推）", advice)
        self.assertEqual(buttons(w.stale), [])
        for widget in (w.status_box, w.scheme_body, w.hero_box, w.toolbar, *w.cards.values()):
            self.assertFalse(widget.isHidden())
        # 全部展开时同一个按钮写「全部折叠」（管整个悬浮窗的按钮都在标题栏）
        self.assertFalse(w.toggle_btn.isHidden())
        self.assertEqual(w.toggle_btn.text(), "全部折叠")
        w.toggle_btn.click()
        self.assertFalse(w.expanded)
        self.assertTrue(w.advice_card.isHidden())
        self.assertFalse(w.toggle_btn.isHidden())
        self.assertEqual(w.toggle_btn.text(), "全部展开")

    def test_reason_pills_hover_full_sentence(self):
        """理由标签上是短句，悬停显示整句：三选一显示期间（一行显示）和全部展开时回看上一次推荐都一样。"""
        w = self.window
        self.assertTrue(w.testAttribute(Qt.WA_AlwaysShowToolTips))  # 游戏窗口在前台（悬浮窗不是活动窗口）时也显示
        offer = ["标靶", "大背包", "缓冲盾牌"]
        w.handle(self.session.on_frame(frame(offer, rerolls=3)))
        self.assertFalse(w.expanded)
        views = option_views(w.advice.last_ev.advice)
        expected = sorted((p.text, tip_html(p.tip)) for v in views for p in v.pills)
        self.assertTrue(all(tip for _, tip in expected))  # 每个理由标签都有悬停说明
        live = [p for p in w.body.findChildren(Pill) if p.isVisibleTo(w.body)]
        self.assertEqual(sorted((p.text(), p.toolTip()) for p in live), expected)
        reasons = {part.reason for e in w.advice.last_ev.advice.evals for part in e.parts}
        for p in live:  # 标签和悬停说明都不写分值（理由原文末尾的游戏数值照写）
            self.assertEqual(score_marks(p.text()), [], p.text())
            self.assertEqual(tip_score_marks(plain_html(p.toolTip()), reasons), [], p.toolTip())
        # 关闭三选一后点「全部展开」：回看上一次推荐，悬停同样显示整句
        self.session.on_frame(frame(offer, rerolls=3, selected=0))
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None))
        w.handle(Event("idle"))
        w.toggle_btn.click()
        self.assertTrue(w.expanded)
        stale = [p for p in w.stale.findChildren(Pill) if p.isVisibleTo(w.stale)]
        self.assertEqual(sorted((p.text(), p.toolTip()) for p in stale), expected)
        # 回看的选项块同样只有推荐度、名称和理由标签，不写总分
        for word in SCORE_WORDS:
            self.assertNotIn(word, shown(w, w.stale))

    def test_live_layout_not_clipped(self):
        """三选一显示（摘要两行）后不显示就画：推荐的最后一行、摘要第二行都不能被截掉（嵌套布局的高度缓存）。"""
        w = self.window
        w.handle(self.session.on_frame(frame(["医疗箱-紧急治疗", "书签", "能量回收"], rerolls=1)))
        w.on_hotkey()  # 摘要后面接上「已按下 F8，正在识别…」，排成两行
        self.assertIn("\n", w.status.summary_plain)
        w.grab()
        self.assertTrue(fits(w.body))
        self.assertGreaterEqual(w.summary.height(), w.summary.heightForWidth(w.summary.width()))
        for pill in w.body.findChildren(Pill):
            self.assertTrue(fits(pill), pill.text())
        self.assertGreater(w.advice_card.geometry().top(), w.summary.geometry().bottom())

    def test_in_game_summary_shows_status(self):
        """对局中：摘要写难度（不知道时不写）、轮次、星数（星币数读不到时不写），不写地图；各项之间「 · 」。"""
        w = self.window
        self.session.settings.round = 4
        w.refresh()
        self.assertEqual(w.status.summary_plain, "第 4 轮 · 星数 待识别")  # 对局中待识别的写明主体
        self.assertNotIn("地图", w.status.summary_plain)
        self.assertIn("地图 待识别", w.status.summary_tip_text)  # 完整的状态（含地图）在悬停说明里
        w.set_manual(True)  # 切到手动模式：先提示一次，之后写「请按热键」
        self.assertTrue(w.status.summary_plain.startswith("已切换到手动模式"), w.status.summary_plain)
        w.handle(Event("idle"))
        self.assertTrue(w.status.summary_plain.startswith("手动模式"), w.status.summary_plain)

    def test_export_path_shown_relative(self):
        from advisor.paths import app_dir

        text = short_path(str(app_dir() / "reports" / "20261002-120000"))
        self.assertEqual(text.replace("​", ""), "程序文件夹\\reports\\20261002-120000")

    def test_hover_shows_breakdown(self):
        """悬停在筹码名按钮上：全部理由（含筹码自身的标签、效果）、前瞻和推荐度，不写分值和总分。
        前瞻和理由标签同一门槛：前瞻分的绝对值不到 LOOKAHEAD_MIN 时不列。"""
        w = self.window
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        adv = w.advice.last_ev.advice
        shown = 0
        # 最后一行的推荐度和选项块第一行显示同一个数（同一次推荐合计 100%）
        texts = {v.slot: v.percent_text for v in option_views(adv)}
        for slot in range(3):
            tip = w.advice.name_tooltip(slot)
            e = adv.evals[slot]
            for part in ("效果：", html.escape(f"推荐度 {texts[slot]}")):
                self.assertIn(part, tip)
            line = next(ln for ln in plain_html(tip).splitlines() if ln.startswith("推荐度 "))
            self.assertEqual(line.split("（")[0], f"推荐度 {texts[slot]}")
            self.assertTrue(e.lookahead_reason)
            for label, reason, _ in breakdown_rows(e):
                self.assertIn(html.escape(reason), tip)
            ahead = abs(e.lookahead) >= LOOKAHEAD_MIN
            self.assertEqual("前瞻" in [label for label, _, _ in breakdown_rows(e)], ahead, e.chip.name)
            self.assertEqual(html.escape(e.lookahead_reason) in tip, ahead, e.chip.name)
            shown += ahead
            text = plain_html(tip).replace(e.chip.effect, "")  # 筹码效果是游戏原文，其中的数值不是分值
            self.assertNotIn("总分", text)
            for p in e.parts:
                self.assertNotIn(f"（{p.score:+.0f}）", text)
            for line in text.splitlines():
                self.assertNotRegex(line, r"\s[+-]\d+$", line)
        self.assertGreaterEqual(shown, 1)  # 标靶进入标记流派，前瞻分远超门槛
        self.assertEqual(w.advice.name_tooltip(9), "")
        self.assertEqual({b.toolTip() for b in name_buttons(w)}, {w.advice.name_tooltip(s) for s in range(3)})

    def test_one_line_summary_when_paused(self):
        """截取屏幕时（直接截取用不了）：游戏和悬浮窗都不在前台时暂停识别，摘要和状态胶囊以「识别 已暂停」开头，
        边框虚线，热键注销；切回游戏恢复。悬浮窗自己在前台照常识别，但热键不登记；找不到游戏窗口时暂停。"""
        w = self.window
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        w.on_capture_changed(capture_state("screen", "denied", "0x80070005", step="CreateForWindow"))
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.follower.update_activity(probe("background"))
        self.assertTrue(w.paused)
        self.assertFalse(w.body.isHidden())  # 保留上一次推荐
        self.assertEqual(PAUSED_BIT, "识别 已暂停")
        self.assertTrue(w.status.summary_plain.startswith(PAUSED_BIT))
        self.assertIn(PAUSED_BIT, w.status.status_text())
        self.assertIn(PAUSED_TIP, w.status.summary_tip_text)
        self.assertTrue(w.panel.property("paused"))
        self.assertEqual(w.watcher.paused, {"focus": True})
        self.assertEqual(w.watcher.why, "screen-background")
        self.assertEqual(w.hotkey_thread.calls, [False])
        w.follower.update_activity(probe("game"))
        self.assertFalse(w.paused)
        self.assertEqual(w.watcher.paused, {"focus": False})
        self.assertEqual(w.hotkey_thread.calls, [False, True])
        # 悬浮窗自己在前台：照常识别，但热键不登记（只在游戏前台生效）
        w.follower.update_activity(probe("own"))
        self.assertFalse(w.paused)
        self.assertEqual(w.hotkey_thread.calls[-1], False)
        w.follower.update_activity(None)
        self.assertTrue(w.paused)
        self.assertEqual(w.watcher.why, "nogame")
        self.assertIn("未找到游戏窗口", w.status.status_text())
        self.assertNotIn(PAUSED_BIT, w.status.status_text())  # 找不到游戏窗口时写这一句，不加胶囊
        w.follower.update_activity(probe("game"))
        self.assertNotIn("未找到游戏窗口", w.status.status_text())

    def test_direct_capture_keeps_recognizing(self):
        """直接截取（默认）：切出游戏照常识别、照常置顶，「识别」卡片状态胶囊第一项是暗色的「识别 照常进行」，一行显示的摘要
        开头是「识别中」（三选一期间摘要里不写），边框不变虚线，热键不登记；只有游戏窗口最小化时暂停（悬停写最小化）。"""
        w = self.window
        w.watcher, w.hotkey_thread = FakeWatcher(), FakeHotkey()
        self.assertEqual(w.capture_method, "window")
        w.handle(self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"])))
        w.follower.update_activity(probe("background"))
        self.assertFalse(w.paused)
        self.assertEqual(w.watcher.paused, {"focus": False})
        self.assertEqual(w.hotkey_thread.calls, [False])
        self.assertEqual(RUNNING_BIT, "识别 照常进行")
        bit = w.status.shown_bits[0]
        self.assertEqual((bit.key, bit.plain(), bit.tone), ("running", RUNNING_BIT, "muted"))  # 暗色，不标橙
        self.assertNotIn("识别 照常进行", flat(w.status.summary_plain))  # 三选一期间摘要让给候选筹码那一行
        self.assertNotIn("识别中", w.status.summary_plain)
        self.assertFalse(w.panel.property("paused"))
        self.assertEqual(w.status.status_tip_text.split("\n")[0], RUNNING_TIP)
        self.assertIn(RUNNING_TIP, w.status.summary_tip_text)
        # 三选一关闭：摘要开头写「识别中」（不写主体「识别」），悬停说明照写完整的「识别 照常进行」
        self.session.on_frame(None)
        w.handle(self.session.on_frame(None) or Event("idle"))
        w.handle(Event("idle"))
        self.assertEqual(w.status.summary_plain, "识别中 · 等待筹码三选一界面…")
        self.assertTrue(w.status.summary_tip_text.startswith(RUNNING_BIT), w.status.summary_tip_text)
        # 「识别」卡片收起时卡片头的摘要也以它开头
        w.set_expanded(True)
        w.recog_card.toggle.click()
        self.assertTrue(plain_html(w.recog_card.summary.text()).startswith(RUNNING_BIT))
        w.recog_card.toggle.click()
        w.set_expanded(False)
        # 最小化：暂停，「识别 已暂停」的悬停说明写最小化
        w.follower.update_activity(probe(minimized=True))
        self.assertTrue(w.paused)
        self.assertEqual((w.watcher.paused, w.watcher.why), ({"focus": True}, "minimized"))
        self.assertTrue(w.status.summary_plain.startswith(PAUSED_BIT))
        self.assertEqual(w.status.status_tip_text.split("\n")[0], PAUSED_MIN_TIP)
        self.assertNotIn(RUNNING_BIT, w.status.status_text())
        self.assertTrue(w.panel.property("paused"))
        w.follower.update_activity(probe("game"))
        self.assertFalse(w.paused)
        self.assertEqual(w.hotkey_thread.calls[-1], True)
        self.assertFalse(w.status.summary_plain.startswith("识别"))  # 游戏在前台时不写识别本身的状态
        self.assertNotEqual(w.status.shown_bits[0].key, "running")

    def test_hotkey_ignored_while_blocked(self):
        w = self.window
        captured = []
        w.capture_requested.connect(lambda: captured.append(1))
        w.background.blocked = True
        w.on_hotkey()
        self.assertEqual(captured, [])
        self.assertIn("识别功能暂不可用", w.status.status_text())


class DevLabelTest(unittest.TestCase):
    """开发版（不是打包的发行版，diagnostics.is_frozen 为 False）全部展开时标题栏写「拾星助手 开发版」；发行版是
    「拾星助手」；一行显示时标题栏只有摘要，不加任何东西。热键不在标题栏里，标题不用让位。
    窗口标题、托盘提示不改。预览工具传 dev_label=False（按发行版画）。字体和 overlay_app.build() 一样（微软雅黑 UI 10pt）。"""

    def setUp(self):
        self.db = load_db()
        self.windows = []
        self.old_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei UI", 10))  # 和 overlay_app.build() 一样

    def tearDown(self):
        for w in self.windows:
            w.hide()
        APP.setFont(self.old_font)

    def make(self, frozen=None, dev_label=None) -> Overlay:
        session = OverlaySession(self.db, UiSettings(hero="凛"))
        diagnostics = SimpleNamespace() if frozen is None else SimpleNamespace(is_frozen=lambda: frozen)
        w = Overlay(self.db, session, threading.Lock(), services=Services(logbook=FakeLogbook(), diagnostics=diagnostics),
                    dev_label=dev_label)
        self.windows.append(w)
        return w

    def test_dev_and_release(self):
        self.assertEqual((TITLE, TITLE_DEV), ("拾星助手", "拾星助手 开发版"))
        for frozen, dev_label, want in ((False, None, TITLE_DEV), (True, None, TITLE), (None, None, TITLE),
                                        (False, False, TITLE)):
            with self.subTest(frozen=frozen, dev_label=dev_label):
                w = self.make(frozen, dev_label)
                self.assertEqual(w.title.text(), want)
                self.assertEqual(w.windowTitle(), "拾星助手")
                w.set_expanded(True)
                w.show()
                APP.processEvents()
                self.assertTrue(w.title.isVisibleTo(w.panel))  # 默认热键 F8：标题放得下
                w.set_expanded(False)
                self.assertFalse(w.title.isVisibleTo(w.panel))  # 一行显示：标题栏只有摘要
                self.assertNotIn("开发版", w.status.summary_plain)
                tray = []
                w.tray_part.tray = SimpleNamespace(setToolTip=tray.append)
                try:
                    w.refresh()
                finally:
                    w.tray_part.tray = None
                self.assertTrue(tray[-1].startswith("拾星助手\n"))
                self.assertNotIn("开发版", tray[-1])

    def test_title_bar_fits_with_dev_label(self):
        """开发版的标题较长：自动、手动模式下标题栏仍排在一行，按钮不被挤掉；热键较长时标题照常显示（热键在设置窗口里）。"""
        w = self.make(False)
        w.show()
        for manual in (False, True):
            w.set_manual(manual)
            w.set_expanded(True)
            APP.processEvents()
            self.assertTrue(w.title.isVisibleTo(w.panel))
            for b in (w.toggle_btn, w.close_btn):
                self.assertGreaterEqual(b.width(), b.sizeHint().width(), b.text())
                self.assertLessEqual(b.geometry().right(), w.panel.width(), b.text())
            self.assertLess(w.title.geometry().right(), w.toggle_btn.geometry().left())
        w.hotkeys.hotkey = "Ctrl+Alt+Shift+F12"
        w.refresh()
        APP.processEvents()
        self.assertTrue(w.title.isVisibleTo(w.panel))
        self.assertEqual(w.title.text(), TITLE_DEV)


class SettingsTest(unittest.TestCase):
    """底栏的「设置」按钮打开「拾星助手设置」窗口：识别画面那一行只读显示截取方式
    （右边状态标签，说明照原文），截取状态变了即时更新；窗口里没有「关闭」按钮（改动即时生效，用窗口自带的 ×）。
    「允许截图」开关已取消：state.json 里旧的 allow_capture 读的时候忽略、下次保存时去掉。窗口里其余各项见
    tests/test_settings_window.py。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=sandbox.work_dir())  # 临时文件放在测试沙箱里
        self.path = Path(self.tmp.name) / "state.json"
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="凛"), self.path)
        self.session.game.seen_hero = "凛"
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=FakeLogbook()))
        self.window.watcher = FakeWatcher()
        # 按内容的高度量，不受测试屏幕的高度限制（屏幕矮时窗口内滚动另有测试）
        patcher = mock.patch.object(SettingsDialog, "max_height", lambda dialog: 5000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for dialog in (self.window.dialogs.settings, self.window.dialogs.about):
            if dialog is not None:
                dialog.close()
        self.window.hide()
        self.tmp.cleanup()

    def open_settings(self) -> SettingsDialog:
        w = self.window
        w.set_expanded(True)
        btn = button(w.toolbar, "设置")
        self.assertEqual(tip_text(btn.toolTip()), SETTINGS_TIP)
        btn.click()
        self.assertIsInstance(w.dialogs.settings, SettingsDialog)
        self.assertTrue(w.dialogs.settings.isVisible())
        return w.dialogs.settings

    def assert_fits(self, dialog) -> None:
        """窗口宽度固定，各段文字（含截取屏幕时识别画面的说明）不被截掉，窗口高度等于内容在这个宽度下需要的高度。"""
        APP.processEvents()
        self.assertEqual(dialog.width(), SETTINGS_WIDTH)
        for label in dialog.page.findChildren(QLabel):
            if label.isVisibleTo(dialog):
                self.assertTrue(fits(label), (label.text(), label.height(), label.heightForWidth(label.width())))
        self.assertEqual(dialog.height(), dialog.page.layout().totalHeightForWidth(dialog.width()))
        chip = dialog.capture_chip
        if chip is not None:  # 「识别画面」只在截取屏幕时有
            self.assertGreaterEqual(chip.width(), chip.sizeHint().width(), chip.text())  # 状态标签的字没被挤掉
            self.assertLessEqual(chip.mapTo(dialog, QPoint(chip.width(), 0)).x(), dialog.width())

    def test_dialog_direct_capture(self):
        """直接截取（Windows 11；Windows 10 版本 2004 起也一样，黄框的说明只写在使用说明里）：设置窗口里没有「识别画面」一项
        （只显示、不能改，平时一直是同一个值）；窗口里的按钮只有「更改」「更换」（「皮肤」一行）「导出诊断」「检查更新」
        （没有「关闭」）。
        窗口开着时改为截取屏幕：同一个窗口当场加上「识别画面」，排在识别热键和低配模式之间；恢复直接截取时又去掉。"""
        w = self.window
        for border, build in ((True, 22631), (False, 19045)):
            with self.subTest(border_requested=border):
                w.on_capture_changed(capture_state("window", border_requested=border, build=build))
                dialog = self.open_settings()
                self.assertEqual(dialog.windowTitle(), SETTINGS_TITLE)
                self.assertEqual(SETTINGS_TITLE, "拾星助手设置")
                self.assertEqual(sorted(b.text() for b in dialog.findChildren(QPushButton)),
                                 sorted(["更改", "更换", "导出诊断", "检查更新"]))
                self.assertFalse(hasattr(dialog, "close_btn") or hasattr(dialog, "allow_box"))
                self.assertNotIn("capture", dialog.rows)
                self.assertIsNone(dialog.capture_chip)
                self.assertNotIn(CAPTURE_TITLE, [lb.text() for lb in dialog.page.findChildren(QLabel)])
                self.assertEqual(dialog.texts(), (SETTINGS_METHOD["window"], ""))
                self.assert_fits(dialog)
        # 窗口开着时改为截取屏幕：当场加上这一项（同一个窗口），排在识别热键和低配模式之间
        w.on_capture_changed(capture_state("screen", "failed", "0x887A0005", step="CreateForWindow"))
        self.assertIs(w.dialogs.settings, dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIn("capture", dialog.rows)
        self.assertEqual((dialog.capture_chip.text(), dialog.capture_chip.tone), ("截取屏幕", "warn"))
        self.assertEqual(dialog.rows["capture"].title.text(), CAPTURE_TITLE)
        top = lambda key: dialog.rows[key].row.mapTo(dialog.page, QPoint(0, 0)).y()  # noqa: E731
        self.assertLess(top("hotkey"), top("capture"))
        self.assertLess(top("capture"), top("lowend"))
        self.assert_fits(dialog)
        # 恢复直接截取：又去掉
        w.on_capture_changed(capture_state("window", border_requested=True))
        self.assertIs(w.dialogs.settings, dialog)
        self.assertNotIn("capture", dialog.rows)
        self.assertIsNone(dialog.capture_chip)
        self.assert_fits(dialog)
        dialog.close()
        self.assertFalse(dialog.isVisible())

    def test_dialog_screen_capture(self):
        """截取屏幕：状态标签「截取屏幕」（橙色），说明以原因开头（有错误代码时写上），写明不在前台时暂停、悬浮窗会出现在截图里、
        录屏会闪；系统不支持时不写「重新启动……会再次尝试」（换了版本才支持）。"""
        w = self.window
        cases = (
            ("unsupported", "", "", "原因：当前 Windows 版本不支持直接截取游戏窗口。识别用的画面是"),
            ("denied", "", "", "原因：Windows 未允许直接截取游戏窗口（错误代码 0x80070005）。"),
            ("failed", "0x887A0005", "CreateForWindow", "原因：直接截取游戏窗口出错（错误代码 0x887A0005）。"),
            ("failed", "", "TryGetNextFrame", "原因：直接截取游戏窗口出错。识别用的画面是"),
            ("blank", "", "", "原因：直接截取的游戏画面为全黑。"),
        )
        for problem, detail, step, start in cases:
            with self.subTest(problem=problem, detail=detail):
                w.on_capture_changed(capture_state("screen", problem, detail, step=step))
                dialog = self.open_settings()
                lead, body = dialog.texts()
                self.assertEqual(lead, SETTINGS_METHOD["screen"])
                self.assertEqual(lead, "截取屏幕")
                self.assertEqual((dialog.capture_chip.text(), dialog.capture_chip.tone), ("截取屏幕", "warn"))
                self.assertEqual(dialog.rows["capture"].desc.text(), body)
                self.assertTrue(body.startswith(start), body)
                self.assertIn(SETTINGS_REASON[problem], body)
                for part in ("游戏窗口不在前台时暂停识别", "拾星助手会出现在截图和屏幕录制中",
                             "录屏中悬浮窗会随识别闪烁（平时约每秒 2 次，筹码三选一期间约每秒 5 次）"):
                    self.assertIn(part, body)
                self.assertEqual(body.endswith(SETTINGS_RETRY), problem != "unsupported")
                self.assertTrue(dialog.rows["capture"].note.isHidden())
                self.assertNotIn("允许截图", body)
                self.assert_fits(dialog)
                dialog.close()

    def test_no_game_window(self):
        """还没找到游戏窗口：按预判的截取方式排。预判为直接截取时没有「识别画面」一项；预判为截取屏幕（系统不支持）时状态标签
        写「截取屏幕」，下面一行暗色「（尚未找到游戏窗口）」，原因按系统不支持写；找到后即时去掉那一行暗色字。"""
        w = self.window
        w.set_expected_capture("window")
        w.follower.update_activity(probe(found=False))
        dialog = self.open_settings()
        self.assertEqual(dialog.texts()[0], SETTINGS_METHOD["window"] + SETTINGS_NO_GAME)
        self.assertNotIn("capture", dialog.rows)
        self.assert_fits(dialog)
        dialog.close()
        # 预判为截取屏幕（系统不支持）：原因按系统不支持写
        w.set_expected_capture("screen")
        self.assertEqual(settings_texts(w.dialogs.settings_view(), True)[0], SETTINGS_METHOD["screen"])
        self.assertIn(SETTINGS_REASON["unsupported"], settings_texts(w.dialogs.settings_view(), True)[1])
        dialog = self.open_settings()
        self.assertEqual(dialog.texts()[0], SETTINGS_METHOD["screen"] + SETTINGS_NO_GAME)
        self.assertIn(SETTINGS_REASON["unsupported"], dialog.texts()[1])
        self.assertEqual(dialog.capture_chip.text(), SETTINGS_METHOD["screen"])
        self.assertTrue(dialog.no_game.isVisibleTo(dialog))
        self.assertEqual(dialog.no_game.text(), "（尚未找到游戏窗口）")
        self.assert_fits(dialog)
        w.follower.update_activity(probe("game"))
        self.assertEqual(dialog.texts()[0], SETTINGS_METHOD["screen"])
        self.assertFalse(dialog.no_game.isVisibleTo(dialog))

    def test_no_win32_in_preview(self):
        """测试和预览（没有打开 native）：打开设置窗口、切换截取方式都不调用 Win32。"""
        w = self.window
        w.show()
        with mock.patch("advisor.ui.winapi.set_capture_allowed") as affinity, \
                mock.patch("advisor.ui.winapi.set_topmost") as topmost:
            w.on_capture_changed(capture_state("screen", "failed", "0x80004005"))
            self.open_settings()
            w.follower.update_activity(probe("background"))
        affinity.assert_not_called()
        topmost.assert_not_called()

    def test_old_state_file_with_allow_capture(self):
        """旧 state.json 里的 allow_capture：读的时候忽略，其他设置照常读出；不在启动时重写文件；
        下次保存设置时去掉。设置类里没有这一项。"""
        old = {"hero": "芬妮", "window_pos": [1466, 28], "manual": False, "hotkey": None,
               "window_rel": [0.7406, 0.0037, "tr"], "collapsed_cards": [], "allow_capture": True}
        self.path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        self.assertNotIn("allow_capture", {f.name for f in dataclasses.fields(UiSettings)})
        settings = UiSettings.load(self.path)
        self.assertEqual((settings.hero, settings.window_pos, settings.window_rel), ("芬妮", [1466, 28],
                                                                                    [0.7406, 0.0037, "tr"]))
        self.assertFalse(hasattr(settings, "allow_capture"))
        session = OverlaySession(self.db, settings, self.path)
        again = Overlay(self.db, session, threading.Lock())
        try:
            self.assertEqual(self.path.read_text(encoding="utf-8"), before)  # 启动时不为此重写文件
            again.set_manual(True)  # 下次保存设置（切换模式）
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertNotIn("allow_capture", saved)
            self.assertEqual((saved["hero"], saved["window_rel"], saved["manual"]), ("芬妮", [0.7406, 0.0037, "tr"], True))
        finally:
            again.hide()


if __name__ == "__main__":
    unittest.main()
