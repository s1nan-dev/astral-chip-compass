"""需要用户操作的提示和本局的开始结束：请打开「查看筹码」面板、请选地图、补录筹码、对局中途判定新一局并撤销、对局结束。
推荐日志用假对象代替，识别结果（面板、对局画面、结算画面的读数）直接构造后交给 Session。

用 Qt 的 offscreen 平台，不会弹出窗口。"""
import html
import json
import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from PySide6.QtWidgets import QPushButton  # noqa: E402
from advisor import hub  # noqa: E402
from advisor.data import load_db, LEVEL_NIGHTMARE  # noqa: E402
from advisor.session import Event  # noqa: E402
from advisor.ui.overlay import Overlay
from advisor.ui.overlay_parts.wording import ADD_TIP, PROMPT_HERO, GAME_OVER_TIP, GAME_OVER_WAITING, OWNED_EMPTY, EXTRA_CHIP_MUTATORS, GAME_OVER_BIT, EXTRA_CHIP_TIP, EXTRA_CHIP_OWNED
from advisor.ui.overlay_parts.wording import OPEN_TASKS
from advisor.ui.overlay_parts.app import UiSettings, OverlaySession
from advisor.ui.services import Services  # noqa: E402
from advisor.ui.text import tip_lines, tip_paragraphs_html, TIP_CHAR, TIP_BLANK_GAP, TIP_LINE  # noqa: E402
from tests.support.fakes import FakeLogbook  # noqa: E402
from tests.support.overlay_cases import APP, BANNED, PROJECT, button, buttons, choose_hero, expire_flash, flat, frame, name_buttons, plain_html, qt_lines, qt_tip, score_marks, shown, tip_text


class OverlayPromptTest(unittest.TestCase):
    """需要操作时边框变色；各个新事件的提示，放在所属的卡片里。"""

    def setUp(self):
        self.db = load_db()
        self.enterContext(mock.patch.object(hub, "ENABLED", True))
        self.session = OverlaySession(self.db, UiSettings(hero="凛"))
        self.window = Overlay(self.db, self.session, threading.Lock())

    def tearDown(self):
        self.window.hide()

    def status(self) -> str:
        return self.window.status.status_text()

    def test_attention_border(self):
        w = self.window
        self.assertFalse(w.panel.property("attention"))
        self.session.game.map_candidates, self.session.game.map_ask = ["魔法学院", "海选赛运动场"], True
        w.refresh()
        self.assertTrue(w.panel.property("attention"))
        self.assertTrue(w.needs_attention())
        self.session.set_map("魔法学院")
        w.refresh()
        self.assertFalse(w.panel.property("attention"))
        self.assertIn('#panel[attention="true"]', w.styleSheet())

    def test_panel_prompt_and_read(self):
        w = self.window
        w.handle(Event("panel_prompt"))
        self.assertIn("panel", w.notice_model.texts())
        self.assertIn("「查看筹码」按钮", w.notice_model.texts()["panel"])
        self.assertTrue(w.panel.property("attention"))
        # 放在「已有筹码」卡片里，一行显示时也看得到（卡片只显示这一行提示）
        self.assertFalse(w.owned_card.isHidden())
        self.assertFalse(w.owned_notices.isHidden())
        self.assertTrue(w.owned_scroll.isHidden() and w.owned_empty.isHidden())
        self.assertIn("「查看筹码」按钮", shown(w, w.owned_card))
        ev = Event("panel_read")
        ev.owned = ["书签", "标靶"]
        self.session.set_owned(ev.owned)
        w.handle(ev)
        self.assertNotIn("panel", w.notice_model.texts())
        self.assertIn("已识别已有筹码 2 个，可以关闭「查看筹码」面板", self.status())
        self.assertIn("已识别已有筹码 2 个", w.status.summary_plain)
        self.assertFalse(w.panel.property("attention"))
        self.assertTrue(w.owned_card.isHidden())

    def test_owned_unknown_while_panel_prompt(self):
        """正在请用户打开「查看筹码」面板时，已有筹码是未知的：卡片主体写「已有筹码待识别」，不写「尚未持有筹码」。"""
        w = self.window
        w.handle(Event("panel_prompt"))
        w.set_expanded(True)
        self.assertEqual(w.owned_empty.text(), "已有筹码待识别")
        w.notices.buttons("panel")[0].click()  # 忽略
        self.assertEqual(w.owned_empty.text(), OWNED_EMPTY)

    def test_map_read(self):
        ev = Event("map_read")
        ev.map = "异变图书馆"
        self.session.set_map("异变图书馆")
        self.window.handle(ev)
        self.assertIn("已识别地图：异变图书馆，可以关闭任务栏", self.status())

    def test_hero_needed_then_choose(self):
        """三选一出现但角色待识别：推荐暂停，请用户在上方「筹码方案」卡片的角色下拉框选择角色；选好后立即计算。
        一行显示时「筹码方案」卡片随之出现；「识别」卡片不出现，状态胶囊里的
        「角色 待识别」（橙色）在全部展开时看得到，悬停说明写明下拉框在下方的「筹码方案」卡片里。"""
        w = self.window
        ev = self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"]))  # 角色待识别：Session 不给推荐
        self.assertEqual(ev.kind, "hero_needed")
        w.handle(ev)
        advice = shown(w, w.advice_card)
        self.assertIn(PROMPT_HERO, advice)
        self.assertEqual(PROMPT_HERO, "请在上方「筹码方案」卡片的角色下拉框中选择当前角色")
        self.assertIn("候选筹码：", advice)
        self.assertFalse(w.scheme_card.isHidden())  # 角色下拉框在上方的「筹码方案」卡片里
        self.assertTrue(w.card_open("scheme"))
        self.assertTrue(w.hero_box.isVisibleTo(w.panel))
        self.assertTrue(w.recog_card.isHidden())  # 一行显示：「识别」卡片没有要操作的提示，不出现
        self.assertFalse(w.body.isHidden())
        self.assertTrue(w.panel.property("attention"))
        self.assertEqual(w.hero_state.text(), "角色 待识别")
        self.assertEqual(w.hero_state.tone, "warn")
        self.assertIn("推荐已暂停", tip_text(w.hero_state.toolTip()))
        self.assertIn("下方「筹码方案」卡片的角色下拉框", tip_text(w.hero_state.toolTip()))
        self.assertNotIn("上一次使用的角色", tip_text(w.hero_state.toolTip()))
        w.show()
        APP.processEvents()
        self.assertLess(w.scheme_card.geometry().top(), w.advice_card.geometry().top())  # 「上方」名副其实
        w.hide()
        choose_hero(w, "阿尔")
        self.assertEqual(self.session.settings.hero, "阿尔")
        self.assertEqual(w.live_state.kind, "advice")
        self.assertEqual(len(name_buttons(w)), 3)
        self.assertTrue(w.scheme_card.isHidden())  # 一行显示：选好角色后不再出现
        self.assertFalse(w.panel.property("attention"))

    def test_hero_needed_choose_same_hero(self):
        """下拉框显示的就是本局的角色（与上一局相同，最常见）：点选同一项也解除暂停、立即计算。"""
        w = self.window
        ev = self.session.on_frame(frame(["标靶", "大背包", "缓冲盾牌"]))
        self.assertEqual(ev.kind, "hero_needed")
        w.handle(ev)
        self.assertEqual(w.hero_box.currentData(), "凛")
        choose_hero(w, "凛")
        self.assertEqual((self.session.settings.hero, self.session.hero_source), ("凛", "manual"))
        self.assertEqual(w.live_state.kind, "advice")
        self.assertFalse(w.live_state.hero_needed)
        self.assertTrue(w.scheme_card.isHidden())

    def test_undo_new_game_restores_panel_prompt_state(self):
        """撤销新一局后，「请打开查看筹码面板」按恢复后的 Session 决定是否显示。"""
        w = self.window
        self.session.set_owned(["书签"])
        self.session.game.panel_needed = False
        self.session.settings.round = 5
        ev = self.session._auto_new_game("star", 0, "星数由 1 星变为 0 星")
        w.handle(ev)
        self.session._entered_game()
        for extra in self.session.take_events():
            w.handle(extra)
        self.assertIn("panel", w.notice_model.texts())
        w.undo_new_game()
        self.assertFalse(self.session.game.panel_needed)
        self.assertNotIn("panel", w.notice_model.texts())
        self.assertFalse(w.panel.property("attention"))

    def test_panel_tab_cleared_only_by_session(self):
        """队友页签的提示不按时间收起：面板仍停在队友页签时一直显示，面板关了才收起。"""
        w = self.window
        w.handle(Event("panel_prompt"))
        tab = SimpleNamespace(is_panel=True, self_tab=False, self_index=2, selected_tab=0, owned=[], unknown_slots=0,
                              confidence=1.0)
        ev = self.session.on_panel(tab)
        self.assertEqual((ev.kind, ev.count), ("panel_tab", 3))
        w.handle(ev)
        self.assertIn("自己的页签（从左数第 3 个）", w.notice_model.texts()["panel"])
        self.assertIsNone(self.session.on_panel(tab))  # 同一个读数不再提示，提示行保持不变
        self.assertIn("自己的页签", w.notice_model.texts()["panel"])
        closed = self.session.on_panel(None)  # 面板关了
        self.assertEqual((closed.kind, closed.count), ("panel_tab", 0))
        w.handle(closed)
        self.assertEqual(w.notice_model.texts()["panel"], "请点击游戏画面左下角的「查看筹码」按钮，打开面板以识别已有筹码")
        self.assertIsNone(self.session.on_panel(None))

    def test_new_game_undo(self):
        w = self.window
        self.session.set_owned(["书签"])
        self.session.settings.round = 5
        ev = self.session._auto_new_game("select", None, "检测到选角界面")  # 判定前存了快照
        self.assertTrue(self.session.can_undo_new_game)
        self.assertEqual(self.session.settings.owned, [])
        w.handle(ev)
        self.assertIn("已判定为新的一局（依据：检测到选角界面）", w.notice_model.texts()["newgame"])
        self.assertIn("已判定为新的一局", shown(w, w.recog_card))  # 新一局（撤销）在「识别」卡片里
        btns = w.notices.buttons("newgame")
        self.assertEqual([b.text() for b in btns], ["撤销"])
        self.assertEqual(btns[0].property("accent"), "primary")  # 有底色
        btns[0].click()
        self.assertEqual(self.session.settings.owned, ["书签"])  # 恢复了上一局的状态
        self.assertEqual(self.session.settings.round, 5)
        self.assertFalse(self.session.can_undo_new_game)
        self.assertNotIn("newgame", w.notice_model.texts())
        self.assertIn("已撤销新的一局的判定", self.status())

    def test_new_game_undo_disappears_after_first_chip(self):
        w = self.window
        ev = self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮")
        w.handle(ev)
        self.assertIn("newgame", w.notice_model.texts())
        self.session.add_chip("书签")  # 这一局第一次记录了筹码
        w.refresh()
        self.assertNotIn("newgame", w.notice_model.texts())

    def test_new_game_without_undo_keeps_message(self):
        self.window.handle(Event("newgame", why="检测到选角界面"))
        self.assertIn("已判定为新的一局（依据：检测到选角界面）", self.status())
        self.assertNotIn("newgame", self.window.notice_model.texts())

    def test_midgame_new_game_needs_action(self):
        """对局中途（轮次、星数、角色）判定的新一局排在最上面、边框变色，「已有筹码」卡片里另有一个「撤销」。"""
        w = self.window
        self.session.set_owned(["书签", "标靶"])
        self.session.settings.round = 8
        w.handle(self.session._auto_new_game("round", 1, "轮次由第 8 轮变为第 1 轮"))
        texts = w.notice_model.texts()
        self.assertTrue(texts["newgame"].startswith("⚠ 对局中途判定为新的一局（依据：轮次由第 8 轮变为第 1 轮）"))
        self.assertEqual(list(texts)[:2], ["newgame", "newgame_owned"])
        self.assertTrue(w.panel.property("attention"))
        self.assertTrue(w.needs_attention())
        # 一行显示时「已有筹码」卡片也出现（只显示这一行提示），里面有「撤销」
        self.assertFalse(w.owned_card.isHidden())
        self.assertIn("已有筹码已随新的一局清空", shown(w, w.owned_card))
        owned_btns = w.notices.buttons("newgame_owned")
        self.assertEqual([b.text() for b in owned_btns], ["撤销"])
        owned_btns[0].click()
        self.assertEqual(self.session.settings.owned, ["书签", "标靶"])
        self.assertNotIn("newgame", w.notice_model.texts())
        self.assertNotIn("newgame_owned", w.notice_model.texts())
        self.assertFalse(w.panel.property("attention"))

    def test_star_and_hero_new_game_are_midgame(self):
        w = self.window
        for kind, why in (("star", "星数由 2 星变为 0 星"), ("hero", "角色由凛变为邦妮")):
            with self.subTest(kind=kind):
                self.session.set_owned(["书签"])
                w.handle(self.session._auto_new_game(kind, None, why))
                self.assertIn("对局中途判定为新的一局", w.notice_model.texts()["newgame"])
                self.assertIn("newgame_owned", w.notice_model.texts())
                w.undo_new_game()

    def test_select_screen_new_game_stays_info(self):
        """选角界面判定的新一局照旧：「识别」卡片里一行说明，不变色，「已有筹码」卡片里没有另一行。"""
        w = self.window
        self.session.set_owned(["书签"])
        w.handle(self.session._auto_new_game("select", None, "检测到选角界面"))
        self.assertNotIn("newgame_owned", w.notice_model.texts())
        self.assertFalse(w.notice_model.texts()["newgame"].startswith("⚠"))
        self.assertFalse(w.panel.property("attention"))

    def test_unrecorded_gain_and_search(self):
        """星数增加却没有三选一记录：「已有筹码」卡片里提示补录；补录栏可以输入筹码名搜索。"""
        w = self.window
        self.session.game.unrecorded = 1  # Session 记下了待补录的筹码数
        ev = Event("unrecorded_gain", why="星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面", count=1)
        w.handle(ev)
        self.assertIn("gain", w.notice_model.texts())
        self.assertIn("星数由 1 星增加为 2 星", w.notice_model.texts()["gain"])
        self.assertIn("星数由 1 星增加为 2 星", shown(w, w.owned_card))
        self.assertTrue(w.panel.property("attention"))
        self.assertEqual([b.text() for b in w.notices.buttons("gain")], ["补录筹码", "关闭本条提示"])
        w.notices.buttons("gain")[0].click()
        self.assertFalse(w.add_row.isHidden())
        w.add_box.setEditText("书签")
        w._add_confirm()
        self.assertEqual(self.session.settings.owned, ["书签"])
        self.assertNotIn("gain", w.notice_model.texts())
        self.assertTrue(w.add_row.isHidden())
        self.assertIn("已补录：书签", self.status())
        w.open_add()
        w.add_box.setEditText("完全不是筹码")
        w._add_confirm()
        self.assertIn("不在筹码表中", self.status())

    def test_pick_reverted(self):
        """同一组候选筹码关闭后又出现：撤销刚记下的筹码；这一次仍未检测到选择时，补录行请用户补录。"""
        w, s = self.window, self.session
        s.game.seen_hero = "凛"
        offer = ["书签", "标靶", "缓冲盾牌"]
        s.on_frame(frame(offer, selected=0))
        s.on_frame(None)
        w.handle(s.on_frame(None))
        self.assertEqual(s.settings.owned, ["书签"])
        w.handle(s.on_frame(frame(offer)))  # 同一组候选又出现
        for ev in s.take_events():
            w.handle(ev)
        self.assertEqual(s.settings.owned, [])
        self.assertIn("已撤销刚记录的「书签」", w.notice_model.texts()["reverted"])
        self.assertIn("已撤销刚记录的「书签」", shown(w, w.advice_card))
        self.assertTrue(w.missed.isHidden())
        s.on_frame(None)
        w.handle(s.on_frame(None))  # 这一次也没看到绿框
        self.assertNotIn("reverted", w.notice_model.texts())
        self.assertFalse(w.missed.isHidden())
        self.assertIn("已撤销刚记录的「书签」", w.missed_label.text())
        self.assertEqual(w.owned.missed_buttons_text(), [*offer, "关闭本条提示"])
        button(w.missed, "标靶").click()
        self.assertEqual(s.settings.owned, ["标靶"])
        self.assertTrue(w.missed.isHidden())

    def test_granted_chips_dragon_palace(self):
        """龙宫游乐园：阵营筹码记下一个后，只留帮助同一方的特殊任务筹码。放在「已有筹码」卡片里。"""
        w = self.window
        self.session.set_map("龙宫游乐园")
        w.refresh()
        self.assertFalse(w.granted_row.isHidden())
        self.assertFalse(w.owned_card.isHidden())
        self.assertEqual(w.scheme.granted_buttons_text(), ["惊涛御守", "迷幻海鲜汤"])
        button(w.granted_row, "惊涛御守").click()
        self.assertEqual(self.session.settings.owned, ["惊涛御守"])
        self.assertEqual(w.scheme.granted_buttons_text(), ["交错双鲤"])
        button(w.granted_row, "交错双鲤").click()
        self.assertTrue(w.granted_row.isHidden())
        self.session.set_map("异变图书馆")
        w.refresh()
        self.assertTrue(w.granted_row.isHidden())

    def assert_quick_add(self, source: str | None) -> None:
        """标题栏的「补录筹码」和「已有筹码」卡片头的「＋ 补录筹码」：有来源时一行显示的标题栏出现前者，两个都突出（黄色），
        悬停说明是同一句（EXTRA_CHIP_TIP）；没有来源时前者不出现，后者不突出、悬停说明是 ADD_TIP。"""
        w = self.window
        if source is None:
            self.assertTrue(w.quick_add.isHidden())
            self.assertEqual(w.add_btn.property("accent") or "", "")
            self.assertEqual(tip_text(w.add_btn.toolTip()), ADD_TIP)
            return
        tip = EXTRA_CHIP_TIP.format(source=source)
        self.assertFalse(w.quick_add.isHidden())
        self.assertTrue(w.quick_add.isVisibleTo(w.panel))
        self.assertEqual(w.quick_add.property("accent"), "primary")
        self.assertEqual(w.add_btn.property("accent"), "primary")
        self.assertEqual(tip_text(w.quick_add.toolTip()), tip)
        self.assertEqual(tip_text(w.add_btn.toolTip()), tip)

    def test_extra_chip_source_highlights_add(self):
        """持有飞羽手环（筹码效果不经三选一获得筹码）：一行显示时标题栏也有突出的「补录筹码」按钮，「已有筹码」卡片头的也突出；
        全部展开时标题栏的不出现。米米不算：潜能给筹码时按筹码商店的规则出三选一（角色说明里的「获得 1 个筹码」不看）。"""
        w, s = self.window, self.session
        self.assert_quick_add(None)
        self.assertEqual(ADD_TIP, "补录不经筹码三选一获得的筹码（击倒宝藏桶、飞羽手环、断线重连期间系统代选等）：输入筹码名搜索")
        s.set_hero("米米")
        w.refresh()
        self.assert_quick_add(None)  # 没有飞羽手环、没有「神秘宝藏」
        s.set_owned(["飞羽手环"])
        w.refresh()
        self.assert_quick_add("已持有的「飞羽手环」")
        self.assertEqual(tip_text(w.quick_add.toolTip()),
                         "已持有的「飞羽手环」可不经筹码三选一获得筹码。获得后请点击此按钮，输入筹码名补录。")
        w.set_expanded(True)
        self.assertTrue(w.quick_add.isHidden())  # 全部展开时只有「已有筹码」卡片头的，照样突出
        self.assertEqual(w.add_btn.property("accent"), "primary")
        w.set_expanded(False)
        self.assert_quick_add("已持有的「飞羽手环」")
        s.set_hero("凛")
        s.set_owned([])
        w.refresh()
        self.assert_quick_add(None)

    def test_mysterious_treasure_quick_add(self):
        """本局识别到畸变组件「神秘宝藏」：击倒宝藏桶得到的筹码不经三选一，所有角色的标题栏都出「补录筹码」按钮。
        识别线程读到畸变组件时 Session 发 status 事件，悬浮窗处理事件时刷新，按钮随即出现；点击打开补录栏。"""
        w, s = self.window, self.session
        # 组件名和 data/mutators.json 一致（改数据后改了名字时，这里先失败）
        names = json.loads((PROJECT / "data" / "mutators.json").read_text(encoding="utf-8"))
        self.assertIn("神秘宝藏", EXTRA_CHIP_MUTATORS)
        self.assertLessEqual(set(EXTRA_CHIP_MUTATORS), set(names))
        self.assertEqual(EXTRA_CHIP_MUTATORS["神秘宝藏"], "击倒宝藏桶（畸变组件「神秘宝藏」）")
        w.handle(s.on_mutators(["暴雨"], pregame=True))  # 其他畸变组件不算
        self.assertEqual(s.game.mutators, ["暴雨"])
        self.assert_quick_add(None)
        ev = s.on_mutators(["暴雨", "神秘宝藏"], pregame=True)
        self.assertEqual(ev.kind, "status")
        w.handle(ev)
        self.assert_quick_add("击倒宝藏桶（畸变组件「神秘宝藏」）")
        self.assertEqual(tip_text(w.quick_add.toolTip()),
                         "击倒宝藏桶（畸变组件「神秘宝藏」）可不经筹码三选一获得筹码。获得后请点击此按钮，输入筹码名补录。")
        s.set_hero("米米")  # 任何角色都一样
        w.refresh()
        self.assert_quick_add("击倒宝藏桶（畸变组件「神秘宝藏」）")
        w.quick_add.click()
        self.assertTrue(w.add_row.isVisibleTo(w.panel))  # 「已有筹码」卡片里的补录栏：输入筹码名搜索
        w.set_expanded(True)
        self.assertTrue(w.quick_add.isHidden())

    def test_extra_chip_sources_joined(self):
        """持有飞羽手环、本局又有「神秘宝藏」：悬停说明里两个来源用「、」连起来，一句说完。"""
        w, s = self.window, self.session
        s.set_owned(["飞羽手环"])
        w.handle(s.on_mutators(["神秘宝藏"], pregame=True))
        self.assert_quick_add(f"{EXTRA_CHIP_OWNED.format(chip='飞羽手环')}、{EXTRA_CHIP_MUTATORS['神秘宝藏']}")
        self.assertEqual(
            tip_text(w.add_btn.toolTip()),
            "已持有的「飞羽手环」、击倒宝藏桶（畸变组件「神秘宝藏」）可不经筹码三选一获得筹码。获得后请点击此按钮，输入筹码名补录。",
        )

    def test_quick_add_marks_direct_only_with_source_and_no_unrecorded(self):
        """补录时有能不经三选一获得筹码的来源、又没有待补录的升星，记为不经三选一获得；有待补录的升星时不记。"""
        w, s = self.window, self.session
        s.set_owned(["飞羽手环"])
        w.refresh()
        self.assertTrue(w.add_owned("书签"))
        self.assertIn("书签", s.game.direct)
        s.set_owned([])  # 没有来源：不记
        self.assertTrue(w.add_owned("大背包"))
        self.assertNotIn("大背包", s.game.direct)
        s.set_owned(["飞羽手环"])
        s.game.unrecorded = 1
        w.notice_model.gain_count = 1
        self.assertTrue(w.add_owned("标靶"))
        self.assertNotIn("标靶", s.game.direct)

    def test_quick_add_direct_follows_session_unrecorded(self):
        """有没有待补录的升星以 Session 的 unrecorded 为准，不看提示行的 gain_count（只是最近一次 unrecorded_gain 差几个）：
        两次升星各差一个时，补录的第二个照样算升星的三选一；面板核对清掉待补录以后（gain_count 不跟着清），补录的照常记为
        不经三选一获得。"""
        w, s = self.window, self.session
        s.set_owned(["飞羽手环"])
        why = "星数由 1 星增加为 2 星，未检测到对应的筹码三选一界面"
        s.game.unrecorded = 2  # 前后两次升星各差一个：Session 记着 2 个，后一次的事件只写 1 个
        w.handle(Event("unrecorded_gain", why=why, count=1))
        self.assertEqual(w.notice_model.gain_count, 1)
        self.assertTrue(w.add_owned("书签"))
        self.assertTrue(w.add_owned("标靶"))
        self.assertEqual((s.game.unrecorded, s.game.direct), (0, set()))
        s.game.unrecorded = 1
        w.handle(Event("unrecorded_gain", why=why, count=1))
        # 「查看筹码」面板只有一页：面板上的就是全部已有筹码，待补录清零
        s.on_panel(SimpleNamespace(is_panel=True, self_tab=True, owned=["飞羽手环", "书签", "标靶"], unknown_slots=0,
                                   confidence=0.9))
        w.refresh()
        self.assertEqual((s.game.unrecorded, w.notice_model.gain_count), (0, 1))
        self.assertTrue(w.add_owned("大背包"))
        self.assertEqual(s.game.direct, {"大背包"})

    def test_quick_add_not_direct_while_missed_pick_pending(self):
        """「未检测到选择」的补录行还在时，补的可能就是那次三选一选的：是那组里认出的一个、或那组有认不出的格子时不记为
        不经三选一获得；那组全认出来、补的又不在其中时照常记。"""
        w, s = self.window, self.session
        s.set_owned(["飞羽手环"])
        s.game.missed_offer = ["书签", None, "标靶"]  # 中间那格认不出：只能用「补录筹码」补
        self.assertTrue(w.add_owned("大背包"))
        self.assertNotIn("大背包", s.game.direct)
        s.game.missed_offer = ["书签", "运动手环", "标靶"]
        self.assertTrue(w.add_owned("标靶"))  # 那组里认出的一个
        self.assertNotIn("标靶", s.game.direct)
        self.assertTrue(w.add_owned("魔法秘典"))  # 那组全认出来，补的不在其中
        self.assertIn("魔法秘典", s.game.direct)

    def test_treasure_read_before_new_game(self):
        """上一局的状态还在时读到的畸变组件留给下一局：读到时按钮不出现，判定新的一局（newgame 事件）后出现；
        撤销这次判定后畸变组件恢复为上一局的，按钮随之消失。"""
        w, s = self.window, self.session
        s.settings.round = 12  # 上一局的状态
        w.refresh()
        self.assertIsNone(s.on_mutators(["神秘宝藏"], pregame=True))
        w.refresh()
        self.assertEqual(s.game.mutators, [])
        self.assert_quick_add(None)
        ev = s.on_select_screen()
        self.assertEqual(ev.kind, "newgame")
        w.handle(ev)
        self.assertEqual(s.game.mutators, ["神秘宝藏"])
        self.assert_quick_add("击倒宝藏桶（畸变组件「神秘宝藏」）")
        w.undo_new_game()
        self.assertEqual(s.game.mutators, [])
        self.assert_quick_add(None)

    def test_no_class_while_hero_pending(self):
        """角色待识别时不显示职业胶囊（Session 记着的是上一次的角色，按它算的职业不一定是这一局的），悬停说明里也不写职业；
        认出角色以后照常显示。"""
        w, s = self.window, self.session
        w.refresh()
        self.assertEqual(s.hero_source, "pending")
        self.assertNotIn("职业", self.status())
        self.assertNotIn("职业：", w.status.status_tip_text)
        s.game.seen_hero = "凛"
        w.refresh()
        self.assertIn("职业 无 · 倾向 卡牌 80", self.status())
        self.assertIn("职业：", w.status.status_tip_text)

    def test_map_hint_waits_for_game(self):
        """地图待识别时「打开任务栏」的提示：还没进对局（没读到轮次）时全部展开也不显示，进了对局以后显示；请用户选地图的
        那种一直显示。"""
        w, s = self.window, self.session
        w.set_expanded(True)
        self.assertIsNone(s.settings.round)
        self.assertTrue(w.map_row.isHidden())
        s.settings.round = 2
        w.refresh()
        self.assertFalse(w.map_row.isHidden())
        self.assertEqual(w.map_prompt.text(), OPEN_TASKS)
        s.settings.round = None
        s.game.map_candidates, s.game.map_ask = ["魔法学院", "海选赛运动场"], True
        w.refresh()
        self.assertFalse(w.map_row.isHidden())  # 请用户选地图：照常显示

    def test_tendency_teammates_mutators_unsupported(self):
        w = self.window
        self.session.game.seen_hero = "凛"  # 角色已认出：职业胶囊照这个角色写
        w.refresh()
        self.assertIn("职业 无 · 倾向 卡牌 80", self.status())
        self.session.game.teammates = [{"hero": "邦妮", "bot": False}, {"hero": None, "bot": True}]
        self.session.game.mutators = ["暴雨"]
        self.session.set_unsupported("small_window", True, size="900×506")
        w.refresh()
        tip = w.status.status_tip_text  # 队友、畸变组件放在状态胶囊的悬停说明里
        self.assertIn("队友：邦妮、待识别（电脑）", tip)
        self.assertIn("畸变组件：暴雨", tip)
        self.assertIn("⚠ 游戏窗口过小（900×506 像素）", " ".join(w.notice_model.texts().values()))
        self.assertIn("游戏窗口过小", shown(w, w.recog_card))  # 不支持的情况在「识别」卡片里
        self.assertIn("队友：邦妮", w.status.summary_tip_text)

    def test_buttons_have_frames(self):
        """悬浮窗上的按钮都是带底色和框线的 QPushButton。"""
        w = self.window
        self.session.game.unrecorded = 1
        w.handle(Event("unrecorded_gain"))
        w.handle(Event("panel_prompt"))
        self.session.set_map("龙宫游乐园")
        w.set_expanded(True)
        for b in w.findChildren(QPushButton):
            self.assertIsInstance(b, QPushButton)
        rule = re.search(r"QPushButton \{([^}]*)\}", w.styleSheet()).group(1)
        self.assertRegex(rule, r"border: 1px solid")
        self.assertRegex(rule, r"background:")
        texts = [b.text() for b in buttons(w)]
        for t in ("补录筹码", "＋ 补录筹码", "设置", "编辑方案", "导入/导出", "作业站", "全部折叠", "▾", "关闭本条提示"):
            self.assertIn(t, texts)
        for t in ("导出诊断", "关于", "导入/导出方案"):  # 在设置窗口和「筹码方案」卡片里
            self.assertNotIn(t, texts)

    def test_no_colloquial_words_in_new_texts(self):
        w = self.window
        self.session.game.unrecorded = 1
        w.handle(Event("unrecorded_gain"))
        w.handle(Event("panel_prompt"))
        w.set_expanded(True)
        texts = [*w.notice_model.texts().values(), w.status.summary_plain, w.status.summary_tip_text, *w.visible_texts(),
                 *(tip_text(b.toolTip()) for b in (w.toggle_btn, w.settings_btn, w.edit_btn, w.share_btn, w.hub_btn,
                                                   w.add_btn, *w.reason_buttons)),
                 *(card.toggle.toolTip() for card in w.cards.values())]
        for text in texts:
            for word in BANNED + ("重选", "不认同"):
                self.assertNotIn(word, text)


class GameOverTest(unittest.TestCase):
    """对局结束：Session 读到结算画面等发 game_over，
    「识别」卡片第一个胶囊和一行显示的摘要以「对局 已结束」开头（不标橙），悬停说明写依据；自动模式的等待文字改为
    GAME_OVER_WAITING；上一次推荐、已有筹码、「未选择首推的原因」保留（不关推荐）；打开面板、星数增加需补录、地图选择行收起，
    边框不变色，不另弹提示；推荐日志按 session.games 只记一次结束。之后的新一局（不可撤销）只作说明。"""

    # 一局实机对局结束时的 9 个筹码（结算页数据视图，samples 20261003_132524）
    OWNED = ["“8面”骰子", "ATM机", "摩托头盔-高级", "友情徽章", "银行卡-用不完", "星币锤", "银行卡-余额多", "金色飞星"]
    OFFER = ["缓冲盾牌", "标靶", "大背包"]
    RESULT = SimpleNamespace(kind="result", difficulty="噩梦", map="异变图书馆", rounds=12)
    WHY = "检测到结算画面（异变图书馆、噩梦、12 轮）"

    def setUp(self):
        self.db = load_db()
        self.session = OverlaySession(self.db, UiSettings(hero="阿尔"))
        self.session.game.seen_hero = "阿尔"
        self.logbook = FakeLogbook()
        self.window = Overlay(self.db, self.session, threading.Lock(), services=Services(logbook=self.logbook))
        self.window.run_background = lambda fn: fn()

    def tearDown(self):
        self.window.hide()

    def deliver(self, ev):
        """主要事件和待取的提示类事件依次交给悬浮窗（和 FrameProcessor 的顺序一样）。"""
        extras = self.session.take_events()
        first = [e for e in extras if e.kind in ("pick_reverted", "picked", "missed_pick")]
        for item in (*first, *([ev] if ev is not None else []), *(e for e in extras if e not in first)):
            self.window.handle(item)
        return ev

    def play_to_end(self) -> str:
        """第 12 轮、3 星、异变图书馆（红叉 17）：最后一次三选一没有选首推（正在问原因），选后共 9 个已有筹码；
        这时「查看筹码」面板和星数增加两条需要操作的提示都在。读到结算画面，返回首推的名字。"""
        s, w = self.session, self.window
        s.settings.round, s.settings.star, s.settings.star_read = 12, 3, True
        s.set_map("异变图书馆")
        s.set_owned(self.OWNED)
        s.game.progress_cap = 17
        ev = self.deliver(s.on_frame(frame(self.OFFER)))
        self.assertEqual(ev.kind, "advice")
        best = ev.advice.best
        chosen = next(n for n in self.OFFER if n != best)
        self.deliver(s.on_frame(frame(self.OFFER, selected=self.OFFER.index(chosen))))
        self.deliver(s.on_frame(None))
        self.deliver(s.on_frame(None))
        self.assertEqual(s.settings.owned, [*self.OWNED, chosen])
        s.game.panel_needed = True
        w.handle(Event("panel_prompt"))
        s.game.unrecorded = 1
        w.handle(Event("unrecorded_gain", why="星数由 2 星增加为 3 星，未检测到对应的筹码三选一界面", count=1))
        self.assertEqual(set(w.notice_model.texts()), {"panel", "gain"})
        self.assertTrue(w.needs_attention())
        game_over = self.deliver(s.on_out_of_game(self.RESULT))
        self.assertEqual((game_over.kind, game_over.why), ("game_over", self.WHY))
        return best

    def test_game_over_display(self):
        w, s = self.window, self.session
        best = self.play_to_end()
        summary = flat(w.status.summary_plain)
        # 结束后摘要不写轮次、星数，难度只写难度名；悬停说明里照旧有完整的状态
        self.assertEqual(summary, "对局 已结束 · 异变图书馆（手动指定） · 噩梦")  # 这个测试里地图是手动指定的
        self.assertTrue(w.status.summary_tip_text.startswith("对局 已结束｜第 12 轮｜3 星｜异变图书馆（手动指定）｜"), w.status.summary_tip_text)
        self.assertNotIn("等待筹码三选一界面", summary)
        self.assertNotIn("上一次推荐", summary)
        self.assertEqual(w.status.status_text().split("｜")[0], "对局 已结束")
        bit = w.status.shown_bits[0]
        self.assertEqual((bit.key, bit.subject, bit.value, bit.tone), ("game_over", "对局", "已结束", ""))
        self.assertEqual(bit.plain(), GAME_OVER_BIT.plain())
        tip = w.status.status_tip_text
        self.assertEqual(tip.split("\n")[0], GAME_OVER_TIP.format(why=self.WHY))
        self.assertIn("对局：已结束。依据：检测到结算画面（异变图书馆、噩梦、12 轮）。", tip)
        self.assertNotIn("））", tip)
        self.assertIn("难度 噩梦", w.status.status_text())  # 结算画面上的难度
        self.assertIn("难度：噩梦。依据结算画面识别。", tip)
        # 一行显示时这段说明也在摘要的悬停说明里，底栏（「设置」按钮，「导出诊断」在它打开的窗口里）收起着：
        # 说明写明先点击「全部展开」按钮
        self.assertIn(GAME_OVER_TIP.format(why=self.WHY), w.status.summary_tip_text)
        self.assertFalse(w.expanded)
        self.assertFalse(w.settings_btn.isVisibleTo(w.panel))
        self.assertIn("可点击底栏的「设置」按钮，在打开的窗口中点击「导出诊断」按钮（一行显示时先点击标题栏的「全部展开」按钮）",
                      GAME_OVER_TIP)
        self.assertEqual(w.toggle_btn.text(), "全部展开")
        # 只对进行中的对局有意义的提示收起，边框不变色
        self.assertEqual(w.notice_model.texts(), {})
        self.assertFalse(w.needs_attention())
        self.assertFalse(w.panel.property("attention"))
        self.assertTrue(w.map_row.isHidden())
        self.assertTrue(w.missed.isHidden())
        # 「未选择首推的原因」照常问（一行显示时也问）；上一次推荐、已有筹码保留
        self.assertIn(f"未选择「{best}」的原因？", shown(w, w.advice_card))
        self.assertEqual(w._waiting_text(), GAME_OVER_WAITING)
        self.assertEqual(GAME_OVER_WAITING, "对局已结束，等待下一局开始…")
        w.set_expanded(True)
        self.assertTrue(w.settings_btn.isVisibleTo(w.panel))  # 全部展开后底栏的「设置」按钮看得见
        self.assertEqual(w.chip_grid.names(), s.settings.owned)
        self.assertEqual(len(w.chip_grid.names()), 9)
        self.assertFalse(w.stale.isHidden())  # 可以回看上一次推荐
        self.assertTrue(w.map_row.isHidden())
        # 待识别的项不标橙（已不在对局中）
        s.settings.star_read = False
        w.refresh()
        star = next(b for b in w.status.shown_bits if b.key == "star")
        self.assertEqual((star.plain(), star.tone), ("星数 待识别", "muted"))
        for text in (w.status.status_text(), tip, w.status.summary_tip_text, shown(w)):
            for word in BANNED + ("None",):
                self.assertNotIn(word, text)
            self.assertEqual(score_marks(text), [])

    def test_status_tip_lines(self):
        """状态胶囊（和地图胶囊）的悬停说明照理由标签的做法分行（不分行的话「对局：已结束……」一行约 1680 逻辑像素宽，
        Qt 不给纯文本的悬停说明自动换行）：每项一段，长的一段按 tip_lines 分行，各段放进定宽的表格；文字内容不变。"""
        w = self.window
        self.play_to_end()
        w.set_expanded(True)
        w.show()
        APP.processEvents()
        text = w.status.status_tip_text
        paragraphs = text.split("\n")
        self.assertEqual(paragraphs[0], GAME_OVER_TIP.format(why=self.WHY))
        self.assertGreater(len(paragraphs[0]), 3 * TIP_LINE)
        tip = w.status_box.toolTip()
        self.assertEqual(tip, tip_paragraphs_html(text))
        lines = [line for line in plain_html(tip).splitlines() if line]
        self.assertEqual(lines, [line for p in paragraphs for line in tip_lines(p)])
        self.assertGreaterEqual(len(tip_lines(paragraphs[0])), 4)
        self.assertLessEqual(max(map(len, lines)), 2 * TIP_LINE)
        # 每段一行表格：去掉段内的断行就是原来的各项
        rows = re.findall(r"<td[^>]*>(.*?)</td>", tip)
        self.assertEqual([html.unescape(r.replace("<br>", "")) for r in rows], paragraphs)
        # Qt 弹出的悬停说明按表格的宽度排（按最长一行估算，再多留一个字），不是一整行，也没有被 Qt 再折行
        width = int(re.match(r'<table width="(\d+)"', tip).group(1))
        self.assertLessEqual(width, (max(map(len, lines)) + 1) * TIP_CHAR)
        shown_tip = qt_tip(w.status_box)
        self.assertLessEqual(shown_tip["尺寸"][0], width + 20)
        self.assertGreater(shown_tip["尺寸"][1], len(lines) * 10)
        self.assertEqual(qt_lines(tip), len(lines))
        # 地图胶囊：说明和地图的识别依据各一段，长的同样分行
        map_tip = w.map_box.toolTip()
        map_rows = [html.unescape(r.replace("<br>", "")) for r in re.findall(r"<td[^>]*>(.*?)</td>", map_tip)]
        self.assertEqual(len(map_rows), 2)
        self.assertTrue(map_rows[0].startswith("当前地图。"), map_rows[0])
        self.assertEqual(map_rows[1], next(p for p in paragraphs if p.startswith("地图「")))
        self.assertEqual(map_tip, tip_paragraphs_html("\n".join(map_rows)))
        self.assertGreater(len(tip_lines(map_rows[0])), 1)
        # 各段都不用分行时照写纯文本
        self.assertEqual(tip_paragraphs_html("星数读取自游戏画面左侧的玩家列表。\n队友：邦妮"),
                         "星数读取自游戏画面左侧的玩家列表。\n队友：邦妮")

    def test_summary_tip_lines(self):
        """一行显示时标题栏摘要的悬停说明照状态胶囊的做法分段分行：第一块是「｜」连起来的各项状态，
        只断在「｜」后面；空行隔开的下一块是状态胶囊的说明，各项一段；空行不另占一行，改为较大的空隙。
        托盘提示另取分行前的纯文本（第一块），不受影响。"""
        w = self.window
        self.play_to_end()
        self.assertFalse(w.expanded)
        text = w.status.summary_tip_text
        blocks = text.split("\n\n")
        self.assertTrue(blocks[0].startswith("对局 已结束｜第 12 轮｜3 星｜"), blocks[0])
        self.assertGreater(len(blocks[0]), TIP_LINE + 4)
        self.assertEqual(blocks[1], w.status.status_tip_text)
        tip = w.summary.toolTip()
        self.assertEqual(tip, tip_paragraphs_html(text))
        self.assertEqual(tip_text(tip), text.replace("\n\n", "\n"))
        first = tip_lines(blocks[0])
        self.assertGreater(len(first), 1)
        for line in first[:-1]:
            self.assertTrue(line.endswith("｜"), first)  # 不把一项（「第 12 轮」「难度 噩梦」）拆到两行
        self.assertEqual(tip.count(f"padding-top:{TIP_BLANK_GAP}px"), 1)  # 空行处
        lines = [line for line in plain_html(tip).splitlines() if line]
        self.assertEqual(lines, [line for p in text.split("\n") if p for line in tip_lines(p)])
        self.assertEqual(qt_lines(tip), len(lines))
        w.show()
        APP.processEvents()
        self.assertLess(qt_tip(w.summary)["尺寸"][0], 600)
        # 托盘提示：分行前的第一块（纯文本）
        tray = []
        w.tray_part.tray = SimpleNamespace(setToolTip=tray.append)
        try:
            w.refresh()
        finally:
            w.tray_part.tray = None
        self.assertEqual(tray[-1], "拾星助手\n" + blocks[0][:120])
        self.assertNotIn("<", tray[-1])

    def test_idle_text_and_collapsed_summaries(self):
        """没有上一次推荐时：「推荐」卡片的空闲文字和收起时的摘要用 GAME_OVER_WAITING；「识别」卡片收起时以「对局 已结束」开头。
        结束时正请用户选地图（几张地图的匹配度都低）：地图选择行收起，边框恢复。"""
        w, s = self.window, self.session
        clock = [100.0]
        s._clock = lambda: clock[0]
        s.settings.round, s.settings.star, s.settings.star_read = 9, 2, True
        s.game.map_candidates, s.game.map_ask = ["魔法学院", "海选赛运动场"], True
        w.refresh()
        self.assertTrue(w.needs_attention())
        self.assertFalse(w.map_row.isHidden())
        room = SimpleNamespace(kind="team_room", difficulty="噩梦", map=None, rounds=None)
        self.deliver(s.on_out_of_game(room))  # 对局状态还在时只读到一次：还不算结束
        clock[0] += 3
        self.deliver(s.on_out_of_game(room))
        self.assertTrue(s.game.game_over)
        self.assertEqual(w.status.summary_plain, "对局 已结束 · 地图 待识别")
        self.assertEqual(next(b for b in w.status.shown_bits if b.key == "map").tone, "muted")
        self.assertIn("对局：已结束。依据：检测到组队房间。", w.status.status_tip_text)
        self.assertTrue(w.map_row.isHidden())
        self.assertFalse(w.needs_attention())
        self.assertFalse(w.panel.property("attention"))
        w.set_expanded(True)
        self.assertTrue(w.map_row.isHidden())
        self.assertEqual(w.idle_label.text(), GAME_OVER_WAITING)
        self.assertFalse(w.idle_label.isHidden())
        self.assertEqual("".join(t for t, _ in w.advice.summary()), GAME_OVER_WAITING)
        self.assertTrue("".join(t for t, _ in w._recog_summary()).startswith("对局 已结束 · 第 9 轮"))

    def test_logged_once(self):
        """推荐日志记一次对局结束（session.game_over_info 加星数、进度、已有筹码）；重复的 game_over 事件不再记。"""
        w, s = self.window, self.session
        self.play_to_end()
        self.assertEqual(len(self.logbook.game_overs), 1)
        info = self.logbook.game_overs[0]
        self.assertEqual({k: info[k] for k in ("why", "map", "difficulty", "level", "rounds", "cap", "star")},
                         {"why": self.WHY, "map": "异变图书馆", "difficulty": "噩梦", "level": LEVEL_NIGHTMARE,
                          "rounds": 12, "cap": 17, "star": 3})
        self.assertEqual(info["owned"], s.settings.owned)
        w.handle(Event("game_over", why=self.WHY))
        self.deliver(s.on_out_of_game(self.RESULT))  # 已结束后再读到结算画面：Session 不再发
        self.assertEqual(len(self.logbook.game_overs), 1)
        # 下一局再结束（奖励画面隔一会儿读到两次）：再记一次
        self.deliver(s.on_select_screen())
        s.settings.round = 3
        clock = [100.0]
        s._clock = lambda: clock[0]
        reward = SimpleNamespace(kind="reward", difficulty=None, map=None, rounds=None)
        self.deliver(s.on_out_of_game(reward))
        clock[0] += 3
        self.deliver(s.on_out_of_game(reward))
        self.assertEqual(len(self.logbook.game_overs), 2)
        self.assertEqual(self.logbook.game_overs[1]["why"], "检测到奖励画面")

    def test_select_screen_starts_next_game(self):
        """结束后看到选角界面：新的一局不可撤销，只作说明（没有「撤销」、不是「需要您操作」），「对局 已结束」消失。
        说明是一次性提示（和「已识别地图」一样约 15 秒后收起），之后一行显示的摘要照常显示，进入对局后是难度（新的一局
        还不知道时不写）、轮次、星数。"""
        w, s = self.window, self.session
        self.play_to_end()
        flashes = w._flash_seq
        ev = self.deliver(s.on_select_screen())
        self.assertEqual((ev.kind, ev.undo, ev.cause), ("newgame", False, "select"))
        text = "已判定为新的一局（依据：上一局已结束，检测到选角界面），已清空上一局的已有筹码、星数、轮次和地图。"
        self.assertIn(text, w.status.status_text())
        self.assertTrue(flat(w.status.summary_plain).startswith("已判定为新的一局"))
        self.assertNotIn("已结束｜", w.status.status_text())
        self.assertNotEqual(w.status.shown_bits[0].key, "game_over")
        self.assertEqual(w.notice_model.texts(), {})
        self.assertEqual(w.notices.buttons("newgame"), [])
        self.assertFalse(w.needs_attention())
        self.assertEqual(w._waiting_text(), "等待筹码三选一界面…")
        self.assertEqual(self.logbook.games_log[-1][0], "new")
        # 一次性提示：到时收起
        self.assertEqual(w._flash_seq, flashes + 1)
        expire_flash(w)
        self.assertNotIn(text, w.status.status_text())
        self.assertEqual(w.status.summary_plain, "等待筹码三选一界面…")
        for _ in range(2):
            self.deliver(s.on_hud(SimpleNamespace(round=1, map=None)))
        self.assertEqual(w.status.summary_plain, "第 1 轮 · 星数 待识别")

    def test_round_starts_next_game(self):
        """结束时游戏在后台、回来时已是下一局第 7 轮（连续两次读到）：新的一局不可撤销，不显示「对局中途判定」那两行，
        另请用户打开「查看筹码」面板（开局后可能已经拿过筹码）。说明约 15 秒后收起，摘要回到轮次、星数。"""
        w, s = self.window, self.session
        self.play_to_end()
        for _ in range(2):
            self.deliver(s.on_hud(SimpleNamespace(round=7, map=None)))
        self.assertFalse(s.game.game_over)
        texts = w.notice_model.texts()
        self.assertNotIn("newgame", texts)
        self.assertNotIn("newgame_owned", texts)
        self.assertIn("panel", texts)
        self.assertIn("已判定为新的一局（依据：上一局已结束，检测到第 7 轮的对局画面）", w.status.status_text())
        self.assertNotEqual(w.status.shown_bits[0].key, "game_over")
        expire_flash(w)
        self.assertNotIn("已判定为新的一局", w.status.status_text())
        self.assertEqual(w.status.summary_plain, "第 7 轮 · 星数 待识别")
        self.assertIn("panel", w.notice_model.texts())  # 打开面板的提示不随说明收起

    def test_manual_mode_waiting_text(self):
        """手动模式下等待文字仍是按热键的提示。"""
        w = self.window
        w.set_manual(True)
        self.play_to_end()
        self.assertTrue(w._waiting_text().startswith("手动模式："))
        self.assertTrue(w.status.status_text().startswith("对局 已结束"))


if __name__ == "__main__":
    unittest.main()
